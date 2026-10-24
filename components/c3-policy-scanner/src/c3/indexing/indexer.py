"""PolicyIndexer: diff, embed only what changed, swap in a new snapshot (FR2, M2)."""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field

from c3.policy.diff import diff_clauses
from c3.policy.models import Clause, ClauseDiff, PolicyLintError

from .embedder import Embedder
from .registry import OrgIndexRegistry
from .store import Entry


@dataclass
class IndexRun:
    run_id: str
    org_id: str
    doc_id: str
    path: str                       # "upload" | "file"
    t0: float                       # epoch seconds when the change was accepted or first detected
    queue_or_detect_ms: float = 0.0
    parse_ms: float = 0.0
    embed_ms: float = 0.0
    upsert_ms: float = 0.0
    diff: ClauseDiff = field(default_factory=ClauseDiff)
    embedded: int = 0               # clauses that actually went through the embedder
    reused: int = 0                 # vectors copied from an earlier version of the same text
    index_version: str = ""
    t_done: float = 0.0             # epoch seconds when the new snapshot became visible
    ok: bool = True
    message: str | None = None

    def stages_ms(self) -> dict[str, float]:
        key = "detect_debounce" if self.path == "file" else "queue"
        return {
            key: self.queue_or_detect_ms,
            "parse": self.parse_ms,
            "embed": self.embed_ms,
            "upsert": self.upsert_ms,
        }

    @property
    def t_index_ms(self) -> float:
        return (self.t_done - self.t0) * 1000.0

    def to_row(self) -> dict:
        s = self.diff.summary()
        return {
            "run_id": self.run_id, "org_id": self.org_id, "doc_id": self.doc_id,
            "path": self.path, "t0": self.t0,
            "queue_or_detect_ms": self.queue_or_detect_ms, "parse_ms": self.parse_ms,
            "embed_ms": self.embed_ms, "upsert_ms": self.upsert_ms,
            "added": s["added"], "changed": s["changed"], "removed": s["removed"],
            "embedded": self.embedded, "index_version": self.index_version,
            "ok": int(self.ok), "message": self.message,
        }


class PolicyIndexer:
    def __init__(self, registry: OrgIndexRegistry, embedder: Embedder, batch_size: int = 32):
        self.registry = registry
        self.embedder = embedder
        self.batch_size = batch_size

    def _embed(self, clauses: list[Clause]) -> list:
        texts = [c.embed_text() for c in clauses]
        vecs = []
        for i in range(0, len(texts), self.batch_size):
            vecs.extend(self.embedder.embed(texts[i : i + self.batch_size]))
        return vecs

    def index_document(
        self,
        org_id: str,
        doc_id: str,
        doc_version: int,
        clauses: list[Clause],
        *,
        path: str,
        t0: float,
        queue_or_detect_ms: float = 0.0,
        parse_ms: float = 0.0,
    ) -> IndexRun:
        index = self.registry.get(org_id)
        snap = index.snapshot()
        run = IndexRun(
            run_id="run-" + uuid.uuid4().hex[:12], org_id=org_id, doc_id=doc_id, path=path,
            t0=t0, queue_or_detect_ms=queue_or_detect_ms, parse_ms=parse_ms,
        )

        # A clause id must belong to one document only
        for c in clauses:
            owner = snap.entries.get(c.clause_id)
            if owner is not None and owner.doc_id != doc_id:
                raise PolicyLintError(
                    "CLAUSE_ID_TAKEN",
                    f"Clause id {c.clause_id} is already used by another document.",
                )

        old_entries = {e.clause.clause_id: e for e in snap.entries.values() if e.doc_id == doc_id}
        run.diff = diff = diff_clauses([e.clause for e in old_entries.values()], clauses)

        # Vectors can be reused when the same text (same hash) existed before, for example
        # a clause that only moved to a new number.
        by_hash = {e.clause.content_hash: e.vector for e in old_entries.values()}
        to_embed = [c for c in diff.added + diff.changed if c.content_hash not in by_hash]
        t = time.perf_counter()
        new_vecs = self._embed(to_embed) if to_embed else []
        run.embed_ms = (time.perf_counter() - t) * 1000.0
        run.embedded = len(to_embed)
        vec_for = {c.clause_id: v for c, v in zip(to_embed, new_vecs, strict=True)}

        now = time.time()
        upserts: list[Entry] = []
        for c in diff.added + diff.changed:
            vec = vec_for.get(c.clause_id)
            if vec is None:
                vec = by_hash[c.content_hash]
                run.reused += 1
            upserts.append(Entry(c, doc_id, doc_version, vec, now))
        # Unchanged clauses keep their vector. They only get the new document version.
        unchanged_ids = set(diff.unchanged)
        by_id = {c.clause_id: c for c in clauses}
        for cid in unchanged_ids:
            old = old_entries[cid]
            if old.doc_version != doc_version or old.clause != by_id[cid]:
                upserts.append(Entry(by_id[cid], doc_id, doc_version, old.vector, old.indexed_at))

        t = time.perf_counter()
        run.index_version = index.apply(
            upserts=upserts, deletes=list(diff.removed), retriever_version=self.embedder.version
        )
        run.upsert_ms = (time.perf_counter() - t) * 1000.0
        run.t_done = time.time()
        return run

    def remove_document(self, org_id: str, doc_id: str, *, path: str = "upload") -> IndexRun:
        index = self.registry.get(org_id)
        t0 = time.time()
        run = IndexRun(
            run_id="run-" + uuid.uuid4().hex[:12], org_id=org_id, doc_id=doc_id, path=path, t0=t0
        )
        ids = [c.clause_id for c in index.snapshot().doc_clauses(doc_id)]
        run.diff = ClauseDiff(removed=ids)
        t = time.perf_counter()
        run.index_version = index.apply(upserts=[], deletes=ids)
        run.upsert_ms = (time.perf_counter() - t) * 1000.0
        run.t_done = time.time()
        return run

    def reindex_org(self, org_id: str) -> str:
        """Re-embed every clause. Needed after the retriever is replaced (blueprint M2)."""
        index = self.registry.get(org_id)
        snap = index.snapshot()
        clauses = [e.clause for e in snap.entries.values()]
        vecs = self._embed(clauses)
        now = time.time()
        entries = []
        for c, v in zip(clauses, vecs, strict=True):
            old = snap.entries[c.clause_id]
            entries.append(Entry(c, old.doc_id, old.doc_version, v, now))
        return index.replace_all(entries, self.embedder.version)
