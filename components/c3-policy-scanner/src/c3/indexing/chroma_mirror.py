"""ChromaDB persistence for each organisation's clause vectors (collection `policy_{org_id}`).

The in-memory snapshot answers searches. ChromaDB keeps a durable copy so the service can
restart without re-embedding every clause (blueprint 11.1). `load` rebuilds the snapshot.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

import numpy as np

from c3.policy.models import Clause

from .store import Entry

_SAFE = re.compile(r"[^A-Za-z0-9_-]")


def collection_name(org_id: str) -> str:
    """Chroma allows [a-zA-Z0-9._-]. Org ids may contain ':', so unsafe ids get a hash suffix.

    The suffix keeps 'a:b' and 'a_b' in different collections (organisation isolation).
    """
    if not _SAFE.search(org_id):
        return "policy_" + org_id
    digest = hashlib.sha1(org_id.encode()).hexdigest()[:8]
    return f"policy_{_SAFE.sub('_', org_id)}-{digest}"


def _meta(e: Entry, version: str, retriever_version: str) -> dict:
    c = e.clause
    return {
        "clause_id": c.clause_id,
        "doc_code": c.doc_code,
        "doc_id": e.doc_id,
        "number": c.number,
        "title": c.title,
        "text": c.text,
        "category": c.category,
        "severity": c.severity,
        "content_hash": c.content_hash,
        "lineage_key": c.lineage_key,
        "order": c.order,
        "page": c.page if c.page is not None else -1,
        "doc_version": e.doc_version,
        "indexed_at": e.indexed_at,
        "index_version": version,
        "retriever_version": retriever_version,
    }


class ChromaMirror:
    def __init__(self, persist_dir: str | Path):
        import chromadb

        Path(persist_dir).mkdir(parents=True, exist_ok=True)
        self.client = chromadb.PersistentClient(path=str(persist_dir))

    def _col(self, org_id: str):
        return self.client.get_or_create_collection(
            collection_name(org_id), metadata={"hnsw:space": "cosine", "org_id": org_id}
        )

    def sync(
        self,
        org_id: str,
        upserts: list[Entry],
        deletes: list[str],
        version: str,
        retriever_version: str,
    ) -> None:
        col = self._col(org_id)
        if deletes:
            col.delete(ids=deletes)
        if upserts:
            col.upsert(
                ids=[e.clause.clause_id for e in upserts],
                embeddings=[e.vector.tolist() for e in upserts],
                documents=[e.clause.embed_text() for e in upserts],
                metadatas=[_meta(e, version, retriever_version) for e in upserts],
            )

    def reset(
        self, org_id: str, entries: list[Entry], version: str, retriever_version: str
    ) -> None:
        try:
            self.client.delete_collection(collection_name(org_id))
        except Exception:
            pass
        self.sync(org_id, entries, [], version, retriever_version)

    def load(self, org_id: str, retriever_version: str) -> list[Entry]:
        col = self._col(org_id)
        if col.count() == 0:
            return []
        got = col.get(include=["embeddings", "metadatas"])
        if any(m.get("retriever_version") != retriever_version for m in got["metadatas"]):
            return []
        out: list[Entry] = []
        for vec, m in zip(got["embeddings"], got["metadatas"], strict=True):
            clause = Clause(
                clause_id=m["clause_id"],
                doc_code=m["doc_code"],
                number=m["number"],
                title=m["title"],
                text=m["text"],
                category=m["category"],
                severity=m["severity"],
                content_hash=m["content_hash"],
                lineage_key=m["lineage_key"],
                order=int(m["order"]),
                page=None if m["page"] == -1 else int(m["page"]),
            )
            out.append(
                Entry(clause, m["doc_id"], int(m["doc_version"]),
                      np.asarray(vec, dtype=np.float32), float(m["indexed_at"]))
            )
        return out

    def collection_names(self) -> list[str]:
        return [c.name for c in self.client.list_collections()]
