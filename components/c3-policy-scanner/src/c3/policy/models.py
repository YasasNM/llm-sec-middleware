"""Plain data types for policy ingestion. No I/O in this module."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

SEVERITIES = ("high", "medium", "low")


def slugify(text: str, max_len: int = 48) -> str:
    """Lower-case slug of a title. Used for slug clause IDs and as the lineage key."""
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:max_len].strip("-")


def normalise_space(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def content_hash(title: str, text: str) -> str:
    """Hash of title + text. Whitespace differences do not count as a change."""
    payload = normalise_space(title) + "\n" + normalise_space(text)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Clause:
    clause_id: str            # e.g. CAIG-3.1 (stable, shown to admins)
    doc_code: str             # e.g. CAIG
    number: str               # "3.1" or "" for slug-id clauses
    title: str
    text: str
    category: str
    severity: str             # high | medium | low
    content_hash: str
    lineage_key: str          # slug of the title, used to spot renumbered clauses
    order: int                # position in the document, starts at 0
    page: int | None = None

    def embed_text(self) -> str:
        """What the retriever sees: 'title. text' (blueprint 12.2)."""
        return f"{self.title}. {self.text}"


@dataclass
class ParsedDocument:
    doc_code: str
    clauses: list[Clause]
    id_scheme: str                       # "numbered" | "slug"
    warnings: list[str] = field(default_factory=list)
    page_count: int | None = None


class PolicyLintError(Exception):
    """The file cannot be accepted. `code` is machine readable, `message` is for admins."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class ClauseDiff:
    added: list[Clause] = field(default_factory=list)
    changed: list[Clause] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)          # clause ids
    unchanged: list[str] = field(default_factory=list)        # clause ids
    # (old_id, new_id) pairs where the same clause moved to another number
    renumbered: list[tuple[str, str]] = field(default_factory=list)

    def summary(self) -> dict[str, int]:
        return {
            "added": len(self.added),
            "changed": len(self.changed),
            "removed": len(self.removed),
            "unchanged": len(self.unchanged),
            "renumbered": len(self.renumbered),
        }
