"""The comparison scorer: a fixed event-count baseline (proposal 3.5).

No rules, no time decay, no weights: every suspicious event adds the same fixed points
(suspicious = C2 BLOCK or C3 VIOLATION). Used to show what C4's design adds.
"""

from __future__ import annotations

from datetime import datetime, timedelta

POINTS_PER_SUSPICIOUS_EVENT = 10.0


def baseline_score(events: list[dict], as_of: datetime, window_days: int = 30) -> float:
    since = as_of - timedelta(days=window_days)
    count = sum(
        1
        for e in events
        if since <= e["event_timestamp"] <= as_of
        and (
            (e["source"] == "c2" and e["c2_verdict"] == "BLOCK")
            or (e["source"] == "c3" and e["c3_verdict"] == "VIOLATION")
        )
    )
    return min(100.0, count * POINTS_PER_SUSPICIOUS_EVENT)
