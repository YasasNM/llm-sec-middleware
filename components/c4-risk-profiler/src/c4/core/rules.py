"""M4: rule engine R1..R7.

`replay()` takes a user's events and processes them in time order. It returns every rule hit
with its impact and the running score after that event (explainability, NFR-01).
It is a pure function: the same events always give the same hits (reproducible).
"""

from __future__ import annotations

from datetime import timedelta

from .decay import score_at
from .ruleset import Ruleset

SOURCE_ORDER = {"c1": 0, "c2": 1, "c3": 2}


def is_suspicious(ev: dict) -> bool:
    """Suspicious event = C2 BLOCK or C3 VIOLATION."""
    return (ev["source"] == "c2" and ev.get("c2_verdict") == "BLOCK") or (
        ev["source"] == "c3" and ev.get("c3_verdict") == "VIOLATION"
    )


def sort_events(events: list[dict]) -> list[dict]:
    return sorted(
        events, key=lambda e: (e["event_timestamp"], SOURCE_ORDER[e["source"]], e["event_id"])
    )


def _hit(ev: dict, rule_id: str, rs: Ruleset) -> dict:
    return {
        "org_id": ev["org_id"],
        "user_id": ev["user_id"],
        "event_id": ev["event_id"],
        "request_id": ev["request_id"],
        "session_id": ev["session_id"],
        "rule_id": rule_id,
        "impact": rs.impact(rule_id),
        "event_timestamp": ev["event_timestamp"],
        "ruleset_version": rs.version,
    }


def replay(events: list[dict], rs: Ruleset) -> list[dict]:
    hits: list[dict] = []
    # State we carry forward while walking through the events in time order
    request_times: list = []  # one time per unique request_id
    seen_requests: set[str] = set()
    sessions: list[dict] = []  # in order of first activity
    session_index: dict[str, dict] = {}
    r3_times: list = []

    for ev in sort_events(events):
        t = ev["event_timestamp"]

        # Remember every request once (needed for rule R6) and every session in order of first use
        if ev["request_id"] not in seen_requests:
            seen_requests.add(ev["request_id"])
            request_times.append(t)
        if ev["session_id"] not in session_index:
            s = {"session_id": ev["session_id"], "suspicious": 0, "r7_fired": False}
            session_index[ev["session_id"]] = s
            sessions.append(s)

        fired: list[str] = []  # rules that fire on this one event
        suspicious = is_suspicious(ev)

        # R1 and R4 are the basic signals. R2 and R5 mark severe cases on top of them.

        if ev["source"] == "c2" and ev["c2_verdict"] == "BLOCK":
            fired.append("R1")
            if ev["c2_score"] >= rs.param("R2", "c2_score"):
                fired.append("R2")
        if ev["source"] == "c3" and ev["c3_verdict"] == "VIOLATION":
            fired.append("R4")
            if ev["leaked_pii_detected"]:
                fired.append("R5")
            if ev["leaked_pii_count"] >= rs.param("R2", "leak_count"):
                fired.append("R2")

        # R3, R6 and R7 look at patterns, so they only matter on a suspicious event
        if suspicious:
            sess = session_index[ev["session_id"]]
            sess["suspicious"] += 1

            # R3: enough suspicious events inside a short window; fires once per window
            window = timedelta(minutes=rs.param("R3", "window_minutes"))
            # keep only the suspicious events that are still inside the window, then add this one
            r3_times = [x for x in r3_times if t - x <= window] + [t]
            if len(r3_times) >= rs.param("R3", "count"):
                fired.append("R3")
                # start counting again, so R3 fires once per burst, not on every event
                r3_times = []

            # R6: suspicious event at an hour the user rarely uses
            since = t - timedelta(days=rs.window_days)
            recent = [x for x in request_times if since <= x <= t]
            if len(recent) >= rs.param("R6", "min_requests"):
                # share = fraction of the user's requests that happened at this hour of the day
                share = sum(1 for x in recent if x.hour == t.hour) / len(recent)
                if share < rs.param("R6", "min_share"):
                    fired.append("R6")

            # R7: suspicious count rises across the last N sessions
            # Needs the last n sessions to have strictly more suspicious events each time (1, 2, 3)
            n = int(rs.param("R7", "sessions"))
            k = len(sessions) - 1
            if (
                sessions[k] is sess
                and k >= n - 1
                and not sess["r7_fired"]
                and sess["suspicious"] == sessions[k - 1]["suspicious"] + 1
            ):
                counts = [sessions[i]["suspicious"] for i in range(k - n + 1, k + 1)]
                if all(counts[i] < counts[i + 1] for i in range(len(counts) - 1)):
                    fired.append("R7")
                    sess["r7_fired"] = True

        # set() so a rule never fires twice on the same event (R2 can be reached two ways)
        new_hits = [_hit(ev, rid, rs) for rid in sorted(set(fired))]
        hits.extend(new_hits)
        # Score right after this event. Stored on the hit so the timeline can show how it grew.
        score_now = round(score_at(hits, t, rs), 1)
        for h in new_hits:
            h["score_after"] = score_now
    return hits
