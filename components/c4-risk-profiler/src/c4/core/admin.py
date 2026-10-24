"""Admin decisions (block / unblock) and the question "may this user in?" (FR-08, K12).

C4 only records the decision. The real enforcement happens in C1 / the gateway, which read
the block list from C4. `blocked_user_ids` is the single source for that list.
"""

from __future__ import annotations

from datetime import UTC, datetime

from c4.db import collections as col

from .recompute import get_profile
from .ruleset import Ruleset


def apply_action(db, org_id: str, user_id: str, action: str, by: str, reason: str) -> bool:
    """Block or unblock a user and write the audit record.

    Returns True when the status really changed. Doing the same action twice changes nothing
    and writes no second record, so the audit log only holds real decisions.
    Raises LookupError when C4 has never seen this user.
    """
    new_status = "blocked" if action == "block" else "active"
    doc = db[col.PROFILES].find_one({"org_id": org_id, "user_id": user_id}, {"_id": 0})
    if doc is None:
        raise LookupError(user_id)
    if doc.get("status", "active") == new_status:
        return False

    db[col.PROFILES].update_one(
        {"org_id": org_id, "user_id": user_id}, {"$set": {"status": new_status}}
    )
    db[col.ACTIONS].insert_one(
        {
            "org_id": org_id,
            "user_id": user_id,
            "action": action,
            "by": by,
            "reason": reason,
            "at": datetime.now(UTC),
        }
    )
    return True


def blocked_user_ids(db, org_id: str) -> list[str]:
    """Everyone whose status is 'blocked'. This is what C1 downloads and enforces."""
    rows = db[col.PROFILES].find({"org_id": org_id, "status": "blocked"}, {"user_id": 1})
    return sorted(r["user_id"] for r in rows)


def recent_actions(db, org_id: str, user_id: str | None = None, limit: int = 100) -> list[dict]:
    """Audit log, newest first. Same-second records are ordered by insertion (_id)."""
    query = {"org_id": org_id, **({"user_id": user_id} if user_id else {})}
    rows = list(
        db[col.ACTIONS].find(query, {"_id": 0}).sort([("at", -1), ("_id", -1)]).limit(limit)
    )
    for r in rows:
        r["at"] = r["at"].strftime("%Y-%m-%dT%H:%M:%SZ")
    return rows


def gate_decision(db, rs: Ruleset, org_id: str, user_id: str) -> dict:
    """What a gate that follows the C4 block list would do with a request from this user.

    A user C4 has never seen is allowed (C1 only stops users on the list). A user who is
    P1 but not yet blocked is also allowed: C4 only *recommends*, a person decides.
    """
    if user_id in blocked_user_ids(db, org_id):
        return {"decision": "BLOCKED", "reason": "User is on the C4 block list"}
    profile = get_profile(db, rs, org_id, user_id)
    result = {"decision": "ALLOWED", "reason": "User is not on the block list"}
    if profile and profile["recommendation"] == "BLOCK_RECOMMENDED":
        result["reason"] = "Not blocked yet, but C4 recommends blocking (P1). Waiting for an admin."
    return result
