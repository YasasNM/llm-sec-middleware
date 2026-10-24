"""Per-organisation clause stores (FR12, NFR13).

Readers always see one complete snapshot. A writer prepares everything off to the side
(embedding happens before `apply`) and then swaps in one step. Scans therefore never see
a half-updated index and never wait for an upload.
"""
from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass, field

import numpy as np

from c3.policy.models import Clause


@dataclass(frozen=True)
class Hit:
    clause: Clause
    similarity: float
    rank: int          # 1 = best


@dataclass(frozen=True)
class Entry:
    clause: Clause
    doc_id: str
    doc_version: int
    vector: np.ndarray = field(repr=False, compare=False)
    indexed_at: float = 0.0


@dataclass(frozen=True)
class Snapshot:
    version: str
    entries: dict[str, Entry]          # clause_id -> entry
    retriever_version: str
    created_at: float

    def clauses(self) -> list[Clause]:
        return [e.clause for e in self.entries.values()]

    def doc_clauses(self, doc_id: str) -> list[Clause]:
        return [e.clause for e in self.entries.values() if e.doc_id == doc_id]

    def doc_ids(self) -> set[str]:
        return {e.doc_id for e in self.entries.values()}


def new_version() -> str:
    return f"idx-{int(time.time() * 1000)}-{secrets.token_hex(2)}"


def empty_snapshot(retriever_version: str) -> Snapshot:
    return Snapshot("idx-empty", {}, retriever_version, time.time())


class OrgIndex:
    """In-memory copy-on-write index for one organisation. Also the source of truth for search."""

    def __init__(self, org_id: str, retriever_version: str, mirror=None):
        self.org_id = org_id
        self._snap = empty_snapshot(retriever_version)
        self._write_lock = threading.Lock()
        self._mirror = mirror              # optional persistent vector mirror (ChromaDB)

    # ---- reading ----------------------------------------------------------------------
    def snapshot(self) -> Snapshot:
        return self._snap                   # a reference read is atomic in CPython

    @property
    def version(self) -> str:
        return self._snap.version

    def search(
        self,
        query_vec: np.ndarray,
        k: int = 5,
        categories: list[str] | None = None,
        snap: Snapshot | None = None,
    ) -> list[Hit]:
        snap = snap or self._snap
        entries = [
            e for e in snap.entries.values() if not categories or e.clause.category in categories
        ]
        if not entries:
            return []
        mat = np.stack([e.vector for e in entries])
        sims = mat @ query_vec.astype(np.float32)
        order = np.argsort(-sims, kind="stable")[:k]
        return [Hit(entries[i].clause, float(sims[i]), rank + 1) for rank, i in enumerate(order)]

    # ---- writing ----------------------------------------------------------------------
    def apply(
        self,
        *,
        upserts: list[Entry],
        deletes: list[str],
        retriever_version: str | None = None,
    ) -> str:
        """Swap in a new snapshot. Returns the new index version."""
        with self._write_lock:
            entries = dict(self._snap.entries)      # copy: old readers keep the old dict
            for cid in deletes:
                entries.pop(cid, None)
            for e in upserts:
                entries[e.clause.clause_id] = e
            version = new_version()
            if self._mirror is not None:
                self._mirror.sync(
                    self.org_id, upserts, deletes, version,
                    retriever_version or self._snap.retriever_version,
                )
            self._snap = Snapshot(
                version=version,
                entries=entries,
                retriever_version=retriever_version or self._snap.retriever_version,
                created_at=time.time(),
            )
            return version

    def restore(self, entries: list[Entry], retriever_version: str) -> str:
        """Load entries that are already stored durably. Does not write to the mirror."""
        with self._write_lock:
            version = new_version()
            self._snap = Snapshot(
                version, {e.clause.clause_id: e for e in entries}, retriever_version, time.time()
            )
            return version

    def replace_all(self, entries: list[Entry], retriever_version: str) -> str:
        """Full rebuild, used when the retriever changes."""
        with self._write_lock:
            version = new_version()
            if self._mirror is not None:
                self._mirror.reset(self.org_id, entries, version, retriever_version)
            self._snap = Snapshot(
                version, {e.clause.clause_id: e for e in entries}, retriever_version, time.time()
            )
            return version
