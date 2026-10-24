"""Event & Rule Timeline: each event with the rule fired and the score right after it."""

from __future__ import annotations

from .decay import score_at
from .rules import is_suspicious
from .ruleset import RULE_TITLES, Ruleset

SOURCE_LABEL = {"c1": "Privacy check", "c2": "Intent check", "c3": "Policy check"}


def _title(ev: dict) -> str:
    """A plain sentence for an event. It contains no prompt or answer text (C4 never has any)."""
    if ev["source"] == "c1":
        return f"Prompt received, {ev['masking_percentage']}% masked"
    if ev["source"] == "c2":
        return "Prompt blocked as suspicious" if ev["c2_verdict"] == "BLOCK" else "Prompt passed"
    if ev["c3_verdict"] == "VIOLATION":
        return "Answer withheld: policy violation"
    return "Answer clean"


def _fields(ev: dict) -> dict:
    # Only the agreed fields of each source are shown
    keys = {
        "c1": ("masking_percentage",),
        "c2": ("c2_verdict", "c2_score"),
        "c3": ("c3_verdict", "clause_id", "leaked_pii_detected", "leaked_pii_count"),
    }[ev["source"]]
    return {k: ev[k] for k in keys}


def build_timeline(
    events: list[dict], hits: list[dict], rs: Ruleset, limit: int = 100
) -> list[dict]:
    # Group the rule hits by the event that caused them
    by_event: dict[str, list[dict]] = {}
    for h in hits:
        by_event.setdefault(h["event_id"], []).append(h)

    rows = []
    for ev in sorted(events, key=lambda e: e["event_timestamp"], reverse=True)[:limit]:
        ev_hits = sorted(by_event.get(ev["event_id"], []), key=lambda h: h["rule_id"])
        fired = {h["rule_id"] for h in ev_hits}
        # Display severity: R2/R5 = High, any other suspicious event = Medium, the rest = Low
        if fired & {"R2", "R5"}:
            severity = "High"
        elif is_suspicious(ev):
            severity = "Medium"
        else:
            severity = "Low"
        # Events with a hit carry the stored score; the others get a freshly calculated one
        if ev_hits:
            score_after = ev_hits[0]["score_after"]
        else:
            score_after = round(score_at(hits, ev["event_timestamp"], rs), 1)
        rows.append(
            {
                "event_id": ev["event_id"],
                "request_id": ev["request_id"],
                "session_id": ev["session_id"],
                "event_timestamp": ev["event_timestamp"].strftime("%Y-%m-%dT%H:%M:%SZ"),
                "source_label": SOURCE_LABEL[ev["source"]],
                "event_title": _title(ev),
                "display_severity": severity,
                "verdict_fields": _fields(ev),
                "rules_fired": [
                    {
                        "rule_id": h["rule_id"],
                        "title": RULE_TITLES[h["rule_id"]],
                        "impact": h["impact"],
                    }
                    for h in ev_hits
                ],
                "score_after": score_after,
            }
        )
    return rows
