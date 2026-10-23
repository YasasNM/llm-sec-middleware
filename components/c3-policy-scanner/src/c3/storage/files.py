"""File store: policies/{org_id}/{doc_id}/v{n}.{ext} (blueprint 11.1).

Only the last good version and one before it are kept, so a verdict can be reproduced
(blueprint 11.3). Rejected uploads go to `rejected/` for the admin to inspect.
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path

_SAFE = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")


class PolicyFileStore:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _dir(self, org_id: str, doc_id: str) -> Path:
        if not _SAFE.match(org_id) or not _SAFE.match(doc_id):
            raise ValueError("Unsafe path component")
        d = (self.root / org_id.replace(":", "_") / doc_id).resolve()
        if self.root.resolve() not in d.parents:
            raise ValueError("Path escapes the policy folder")
        return d

    def save(self, org_id: str, doc_id: str, version: int, ext: str, data: bytes) -> Path:
        d = self._dir(org_id, doc_id)
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"v{version}{ext}"
        path.write_bytes(data)
        # keep this version and the one before it
        for old in d.glob("v*"):
            m = re.match(r"v(\d+)", old.stem)
            if m and int(m.group(1)) < version - 1:
                old.unlink(missing_ok=True)
        return path

    def save_rejected(self, org_id: str, doc_id: str, ext: str, data: bytes) -> Path:
        d = self._dir(org_id, doc_id) / "rejected"
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"latest{ext}"
        path.write_bytes(data)
        return path

    def latest(self, org_id: str, doc_id: str) -> Path | None:
        d = self._dir(org_id, doc_id)
        files = [p for p in d.glob("v*") if p.is_file()] if d.exists() else []
        if not files:
            return None
        return max(files, key=lambda p: int(re.match(r"v(\d+)", p.stem).group(1)))

    def delete(self, org_id: str, doc_id: str) -> None:
        d = self._dir(org_id, doc_id)
        if d.exists():
            shutil.rmtree(d)
