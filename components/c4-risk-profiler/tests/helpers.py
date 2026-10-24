"""Small builders for events (used by the tests)."""

from __future__ import annotations

import itertools
from datetime import UTC, datetime, timedelta

_ids = itertools.count(1)


def now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def payload(
    source: str, ts: datetime, *, org="org_001", user="user_001", req=None, sess="sess_1", **f
):
    """An event exactly as a component would send it (agreed fields only)."""
    n = next(_ids)
    base = {
        "event_id": f"evt-{source}-{n}",
        "org_id": org,
        "user_id": user,
        "request_id": req or f"req_{n}",
        "session_id": sess,
        "event_timestamp": iso(ts),
    }
    if source == "c1":
        base["masking_percentage"] = f.pop("masking_percentage", 0.0)
    elif source == "c2":
        base["c2_verdict"] = f.pop("c2_verdict", "PASS")
        base["c2_score"] = f.pop("c2_score", 0.05)
    else:
        verdict = f.pop("c3_verdict", "CLEAN")
        base["c3_verdict"] = verdict
        base["clause_id"] = f.pop("clause_id", None if verdict == "CLEAN" else "POL-PHI-002")
        base["leaked_pii_detected"] = f.pop("leaked_pii_detected", False)
        base["leaked_pii_count"] = f.pop("leaked_pii_count", 0)
    base.update(f)
    return base


def to_internal(p: dict, source: str) -> dict:
    """The form the rule engine works on (as stored in MongoDB)."""
    from c4.db.mongo import parse_ts

    return {**p, "source": source, "event_timestamp": parse_ts(p["event_timestamp"])}


def block_event(ts, **kw):
    return payload("c2", ts, c2_verdict="BLOCK", c2_score=kw.pop("c2_score", 0.5), **kw)


def violation(ts, leaks=0, **kw):
    return payload(
        "c3",
        ts,
        c3_verdict="VIOLATION",
        leaked_pii_detected=leaks > 0,
        leaked_pii_count=leaks,
        **kw,
    )


def minutes_ago(n: float) -> datetime:
    return now() - timedelta(minutes=n)
