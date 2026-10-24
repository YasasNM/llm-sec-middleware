"""The user list with live scores, used by GET /v1/users and by the dashboard."""

from __future__ import annotations

from c4.db import collections as col

from .classify import LABELS, priority_for
from .recompute import live_scores, now_utc
from .ruleset import Ruleset


def collect_users(db, rs: Ruleset, org_id: str) -> tuple[list[dict], dict]:
    """Return (rows, summary): one row per user plus the counts shown at the top of the page.

    The score is recalculated for *now*, not read from the stored profile, because old
    evidence fades with time and a stored number would go stale.
    """
    live = live_scores(db, rs, org_id, now_utc())
    rows = []
    for p in db[col.PROFILES].find({"org_id": org_id}, {"_id": 0}):
        # A user with no rule hits has no entry in `live`, so their score is 0.
        info = live.get(p["user_id"], {"score": 0.0, "main_reason": None})
        prio = priority_for(info["score"], rs)
        rows.append(
            {
                "user_id": p["user_id"],
                "score": info["score"],
                "priority": prio,
                "priority_label": LABELS[prio],
                "status": p.get("status", "active"),
                "main_reason": info["main_reason"],
                "last_updated": p.get("updated_at"),
                "last_activity": p.get("last_activity"),
                "request_count": p["behaviour_summary"]["requests"],
            }
        )
    summary = {
        "total_users": len(rows),
        **{k: sum(1 for r in rows if r["priority"] == k) for k in ("P1", "P2", "P3", "P4")},
        "blocked": sum(1 for r in rows if r["status"] == "blocked"),
    }
    # Highest risk first; the user id breaks ties so the order never jumps around.
    rows.sort(key=lambda r: (-r["score"], r["user_id"]))
    return rows, summary
