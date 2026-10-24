"""Dummy data helpers for the dashboard's demo buttons (until C1, C2 and C3 send real events).

Three things live here:
  * reset_all        - empty the five collections
  * load_demo        - fill them with the 7 behaviour scenarios
  * start_attack     - feed a fast burst of hostile events for one user, step by step,
                       through the normal ingest path, so the score visibly rises
"""

from __future__ import annotations

import threading
import time
import uuid
from datetime import UTC, datetime

from c4.contracts import validate_event
from c4.core.ingest import ingest_event
from c4.core.recompute import now_utc, recompute_user
from c4.db import collections as col
from c4.db.mongo import parse_ts

from .scenarios import SCENARIOS, USER_FOR_SCENARIO, build_scenario

ALL_COLLECTIONS = (col.EVENTS, col.HITS, col.FEATURES, col.PROFILES, col.ACTIONS)


def reset_all(db) -> dict[str, int]:
    """Delete every document in the five C4 collections. Returns how many were removed."""
    return {name: db[name].delete_many({}).deleted_count for name in ALL_COLLECTIONS}


def load_demo(state, org_id: str, seed: int = 42) -> dict:
    """Replace everything with the 7 scenarios.

    To keep the button fast, events are saved in one batch per user and the profile is rebuilt
    once at the end. This gives the same result as sending the events one by one, because the
    rebuild always replays all events in time order (see core/recompute.py). Every event is still
    checked against the contract schema first.
    """
    db = state.db
    removed = reset_all(db)
    end = datetime.now(UTC)
    total = 0
    for name in SCENARIOS:
        user = USER_FOR_SCENARIO[name]
        docs = []
        for item in build_scenario(name, user, org_id, seed, end):
            errors = validate_event(state.validators, item["source"], item["payload"])
            if errors:  # would mean a bug in the generator, so stop loudly
                raise ValueError(f"{name}: generated event is invalid: {errors}")
            docs.append(
                {
                    **item["payload"],
                    "source": item["source"],
                    "event_timestamp": parse_ts(item["payload"]["event_timestamp"]),
                    "received_at": now_utc(),
                }
            )
        db[col.EVENTS].insert_many(docs)
        recompute_user(db, state.rules, org_id, user)
        total += len(docs)
    return {"users": len(SCENARIOS), "events": total, "removed": sum(removed.values())}


# Steps of the live attack. The numbers are chosen so the score climbs slowly at first and
# crosses P1 (80) after about five steps: ("block", C2 score) or ("leak", leaked identifiers).
ATTACK_STEPS = [
    ("block", 0.62),
    ("block", 0.68),
    ("block", 0.72),
    ("block", 0.93),
    ("leak", 2),
    ("block", 0.95),
]


def _attack_events(org_id: str, user_id: str, session_id: str, kind: str, value: float) -> list:
    """The events one request produces: C1 always, C2 always, C3 only if C2 let it through."""
    ts = now_utc().strftime("%Y-%m-%dT%H:%M:%SZ")
    req = f"req_live_{uuid.uuid4().hex[:12]}"

    def event(source: str, **fields) -> tuple[str, dict]:
        return source, {
            "event_id": f"evt-{source}-{uuid.uuid4()}",
            "org_id": org_id,
            "user_id": user_id,
            "request_id": req,
            "session_id": session_id,
            "event_timestamp": ts,
            **fields,
        }

    out = [event("c1", masking_percentage=5.0)]
    if kind == "block":
        out.append(event("c2", c2_verdict="BLOCK", c2_score=value))
    else:  # the prompt passed C2, but the answer leaked identifiers and C3 flagged it
        out.append(event("c2", c2_verdict="PASS", c2_score=0.2))
        out.append(
            event(
                "c3",
                c3_verdict="VIOLATION",
                clause_id="POL-PHI-002",
                leaked_pii_detected=True,
                leaked_pii_count=int(value),
            )
        )
    return out


def start_attack(state, org_id: str, user_id: str, pause_seconds: float = 1.2) -> bool:
    """Start the attack in a background thread. Returns False if one is already running.

    The thread posts a hostile request, waits a moment, posts the next one, and so on, so the
    dashboard (which refreshes every two seconds) shows the score climbing live.
    """
    sim = state.sim
    with sim["lock"]:
        if sim["running"]:
            return False
        sim.update(running=True, user_id=user_id, step=0, total=len(ATTACK_STEPS))

    def run() -> None:
        try:
            session_id = f"sess_live_{uuid.uuid4().hex[:8]}"
            for kind, value in ATTACK_STEPS:
                for source, payload in _attack_events(org_id, user_id, session_id, kind, value):
                    ingest_event(state, source, payload)
                sim["step"] += 1
                time.sleep(pause_seconds)
        finally:
            sim["running"] = False

    threading.Thread(target=run, daemon=True).start()
    return True


def new_sim_state() -> dict:
    return {"lock": threading.Lock(), "running": False, "user_id": None, "step": 0, "total": 0}
