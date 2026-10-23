"""Revision diff by clause id and content hash (FR2)."""
from __future__ import annotations

from collections.abc import Iterable

from .models import Clause, ClauseDiff


def diff_clauses(old: Iterable[Clause], new: Iterable[Clause]) -> ClauseDiff:
    old_by_id = {c.clause_id: c for c in old}
    new_by_id = {c.clause_id: c for c in new}
    d = ClauseDiff()
    for cid, c in new_by_id.items():
        prev = old_by_id.get(cid)
        if prev is None:
            d.added.append(c)
        elif prev.content_hash != c.content_hash:
            d.changed.append(c)
        else:
            d.unchanged.append(cid)
    d.removed = [cid for cid in old_by_id if cid not in new_by_id]

    # Renumbering: the same title (lineage key) now sits under a different id. This is
    # informational. Indexing still works by id, and vectors are reused by content hash.
    # Only titles that are unique on both sides are matched, to avoid wrong pairs.
    old_lin = _unique_by_lineage(old_by_id.values())
    new_lin = _unique_by_lineage(new_by_id.values())
    for key, old_id in old_lin.items():
        new_id = new_lin.get(key)
        if new_id is not None and new_id != old_id:
            d.renumbered.append((old_id, new_id))
    return d


def _unique_by_lineage(clauses) -> dict[str, str]:
    seen: dict[str, list[str]] = {}
    for c in clauses:
        seen.setdefault(c.lineage_key, []).append(c.clause_id)
    return {k: v[0] for k, v in seen.items() if len(v) == 1}
