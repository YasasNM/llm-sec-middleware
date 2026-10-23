"""OrgIndexRegistry: one index per organisation, no cross-organisation access (FR12, K14)."""
from __future__ import annotations

import re
import threading

from .chroma_mirror import ChromaMirror
from .embedder import Embedder
from .store import OrgIndex

ORG_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")


class OrgIndexRegistry:
    def __init__(self, embedder: Embedder, mirror: ChromaMirror | None = None):
        self.embedder = embedder
        self.mirror = mirror
        self._indexes: dict[str, OrgIndex] = {}
        self._lock = threading.Lock()

    def get(self, org_id: str, create: bool = True) -> OrgIndex | None:
        if not ORG_ID.match(org_id):
            raise ValueError("Invalid org_id")
        with self._lock:
            idx = self._indexes.get(org_id)
            if idx is None and create:
                idx = OrgIndex(org_id, self.embedder.version, mirror=self.mirror)
                if self.mirror is not None:
                    # Reload the durable copy. Vectors made by another retriever are ignored,
                    # so the caller must re-index that organisation's documents.
                    entries = self.mirror.load(org_id, self.embedder.version)
                    if entries:
                        idx.restore(entries, self.embedder.version)
                self._indexes[org_id] = idx
            return idx

    def orgs(self) -> list[str]:
        with self._lock:
            return sorted(self._indexes)
