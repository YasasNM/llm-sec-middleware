"""M3: behaviour features and the RiskProfile document (Appendix B, extended)."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta

from .classify import LABELS, RECOMMENDATION, priority_for
from .decay import decayed_points, score_at
from .rules import SOURCE_ORDER, is_suspicious, sort_events
from .ruleset import RULE_TITLES, Ruleset


def _iso(dt: datetime | None) -> str | None:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ") if dt else None


def compute_features(events: list[dict], hits: list[dict], as_of: datetime, rs: Ruleset) -> dict:
    """Counters and histograms for one user, over the history window."""
    # Only look at the last 30 days (the window), oldest first
    since = as_of - timedelta(days=rs.window_days)
    win = [e for e in sort_events(events) if since <= e["event_timestamp"] <= as_of]
    win_ids = {e["event_id"] for e in win}
    win_hits = [h for h in hits if h["event_id"] in win_ids]

    # One request produces up to three events (C1, C2, C3), so requests are counted by request_id
    request_time: dict[str, datetime] = {}
    sessions: dict[str, dict] = {}
    masks: list[float] = []
    intent_blocks = violations = leak_events = leaked_items = 0
    for e in win:
        request_time.setdefault(e["request_id"], e["event_timestamp"])  # keep the first time seen
        s = sessions.setdefault(
            e["session_id"],
            {
                "session_id": e["session_id"],
                "first_seen": e["event_timestamp"],
                "suspicious_events": 0,
            },
        )
        if is_suspicious(e):  # C2 BLOCK or C3 VIOLATION
            s["suspicious_events"] += 1
        if e["source"] == "c1":
            masks.append(float(e["masking_percentage"]))
        if e["source"] == "c2" and e["c2_verdict"] == "BLOCK":
            intent_blocks += 1
        if e["source"] == "c3":
            if e["c3_verdict"] == "VIOLATION":
                violations += 1
            if e["leaked_pii_detected"]:
                leak_events += 1
                leaked_items += int(e["leaked_pii_count"])

    # How many requests at each hour of the day (UTC). Used by rule R6 and the hour chart.
    hour_hist = [0] * 24
    for t in request_time.values():
        hour_hist[t.hour] += 1
    # "High severity" events are the ones where R2 or R5 fired
    high_events = {h["event_id"] for h in win_hits if h["rule_id"] in ("R2", "R5")}
    return {
        "hour_histogram": hour_hist,
        "sessions": [
            {**s, "first_seen": _iso(s["first_seen"])}
            for s in sorted(sessions.values(), key=lambda s: s["first_seen"])
        ],
        "counters": {
            "requests": len(request_time),
            "sessions": len(sessions),
            "intent_blocks": intent_blocks,
            "policy_violations": violations,
            "leak_events": leak_events,
            "leaked_items": leaked_items,
            "high_severity": len(high_events),
            "unfamiliar_time_events": sum(1 for h in win_hits if h["rule_id"] == "R6"),
        },
        # masking: sum and count let us keep a running average without storing every value
        "masking": {
            "sum": round(sum(masks), 3),
            "count": len(masks),
            "nonzero": sum(1 for m in masks if m > 0),
        },
    }


def behaviour_summary(features: dict) -> dict:
    """The small block of numbers shown on a profile (counters plus two masking figures)."""
    c, m = features["counters"], features["masking"]
    return {
        **c,
        "avg_masking_percentage_30d": round(m["sum"] / m["count"], 1) if m["count"] else 0.0,
        "requests_with_masking_pct": round(100.0 * m["nonzero"] / m["count"], 1)
        if m["count"]
        else 0.0,
    }


def top_contributors(hits: list[dict], now: datetime, rs: Ruleset) -> list[dict]:
    """Decayed points per rule, biggest first. Every point comes from a stored rule hit."""
    # Add up the decayed points of every rule hit, grouped by rule
    since = now - timedelta(days=rs.window_days)
    pts: dict[str, float] = defaultdict(float)
    for h in hits:
        if since <= h["event_timestamp"] <= now:
            pts[h["rule_id"]] += decayed_points(h["impact"], h["event_timestamp"], now, rs)
    rows = [
        {"rule_id": r, "title": RULE_TITLES[r], "points": round(p, 1)}
        for r, p in pts.items()
        if p >= 0.05  # a rule with almost nothing left is not worth showing
    ]
    # Biggest reason first; the rule id breaks ties so the order is always the same
    return sorted(rows, key=lambda r: (-r["points"], r["rule_id"]))


def build_profile(
    org_id: str, user_id: str, events: list[dict], hits: list[dict], as_of: datetime, rs: Ruleset
) -> dict:
    """Profile fields computed from events and rule hits (status is set by admin actions)."""
    feats = compute_features(events, hits, as_of, rs)
    # One decimal is enough; more digits would suggest accuracy the rules do not have
    score = round(score_at(hits, as_of, rs), 1)
    prio = priority_for(score, rs)
    times = [e["event_timestamp"] for e in events]
    return {
        "org_id": org_id,
        "user_id": user_id,
        "score": score,
        "priority": prio,
        "priority_label": LABELS[prio],
        "recommendation": RECOMMENDATION[prio],
        "top_contributors": top_contributors(hits, as_of, rs),
        "behaviour_summary": behaviour_summary(feats),
        "first_seen": _iso(min(times)) if times else None,
        "last_activity": _iso(max(times)) if times else None,
        "updated_at": _iso(as_of),
        "event_count": len(events),
        "ruleset_version": rs.version,
    }


__all__ = ["build_profile", "compute_features", "SOURCE_ORDER"]
