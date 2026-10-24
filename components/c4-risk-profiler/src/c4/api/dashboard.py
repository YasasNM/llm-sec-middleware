"""Data for the browser dashboard (served at /dashboard). Routes live under /ui/api.

These routes need no service key because they are for the demo: the page and the API come
from the same C4 server. In the finished system the gateway sits in front of C4, checks that
the person is an admin, and calls the /v1 admin endpoints instead. Set C4_DASHBOARD_ENABLED=false
to switch this whole page off.
"""

from __future__ import annotations

from collections import deque
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from c4.core import clock
from c4.core.admin import apply_action, blocked_user_ids, gate_decision, recent_actions
from c4.core.overview import collect_users
from c4.core.recompute import get_profile, load_events, load_hits
from c4.core.ruleset import RULE_IDS, RULE_TITLES, describe_rule
from c4.core.timeline import build_timeline
from c4.db import collections as col
from c4.demo.data import load_demo, reset_all, start_attack

router = APIRouter(prefix="/ui/api", tags=["dashboard (demo)"])

DEFAULT_ORG = "org_001"


class Decision(BaseModel):
    by: str = Field(default="admin_demo", min_length=1, max_length=64)
    reason: str = Field(min_length=1, max_length=500)


class GateRequest(BaseModel):
    user_id: str = Field(min_length=1, max_length=64)


class TimeMove(BaseModel):
    # add_hours moves the clock forward; reset=true returns to the real time
    add_hours: float = Field(default=0, ge=0, le=24 * 90)
    reset: bool = False


class AttackRequest(BaseModel):
    user_id: str = Field(
        default="user_live", min_length=1, max_length=64, pattern="^[A-Za-z0-9_.:-]+$"
    )


def _state(request: Request):
    return request.app.state


def _clock() -> dict:
    """The dashboard's current time. The page needs it to show "5 min ago" correctly."""
    return {
        "now": clock.now_utc().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "offset_hours": clock.offset_hours(),
    }


@router.get("/overview")
def overview(request: Request, org_id: str = DEFAULT_ORG):
    """Counts + every user with the current score. The page calls this every few seconds."""
    st = _state(request)
    rows, summary = collect_users(st.db, st.rules, org_id)
    sim = {k: v for k, v in st.sim.items() if k != "lock"}
    return {"summary": summary, "users": rows, "simulation": sim, "clock": _clock()}


@router.get("/users/{user_id}")
def user_detail(request: Request, user_id: str, org_id: str = DEFAULT_ORG):
    """Everything the user page needs: profile, timeline, hour chart and sessions."""
    st = _state(request)
    profile = get_profile(st.db, st.rules, org_id, user_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="user not found")
    events = load_events(st.db, org_id, user_id)
    timeline = build_timeline(events, load_hits(st.db, org_id, user_id), st.rules, limit=400)
    feats = st.db[col.FEATURES].find_one({"org_id": org_id, "user_id": user_id}, {"_id": 0}) or {}
    return {
        "profile": profile,
        "timeline": timeline,  # newest first
        "hour_histogram": feats.get("hour_histogram", [0] * 24),
        "sessions": feats.get("sessions", []),
        "clock": _clock(),
    }


@router.post("/users/{user_id}/{action}")
def decide(request: Request, user_id: str, action: str, body: Decision, org_id: str = DEFAULT_ORG):
    """Block or unblock from the dashboard. Uses the same function as the /v1 endpoints."""
    if action not in ("block", "unblock"):
        raise HTTPException(status_code=404, detail="unknown action")
    st = _state(request)
    try:
        apply_action(st.db, org_id, user_id, action, body.by, body.reason)
    except LookupError:
        raise HTTPException(status_code=404, detail="user not found") from None
    return get_profile(st.db, st.rules, org_id, user_id)


@router.get("/access")
def access(request: Request, org_id: str = DEFAULT_ORG):
    """The block list, the audit log and the latest gate attempts, for the Access page."""
    st = _state(request)
    return {
        "blocked": blocked_user_ids(st.db, org_id),
        "actions": recent_actions(st.db, org_id, limit=50),
        "gate_log": list(st.gate_log),
    }


@router.post("/gate")
def gate(request: Request, body: GateRequest, org_id: str = DEFAULT_ORG):
    """Simulated C1 / gateway: a request from this user arrives. Is it let in?

    The answer comes from the same block list that C1 will read from /v1/blocklist. A blocked
    user gets a real HTTP 403, an allowed user gets 200.
    """
    st = _state(request)
    result = gate_decision(st.db, st.rules, org_id, body.user_id)
    st.gate_log.appendleft(
        {
            "at": datetime.now(UTC).strftime("%H:%M:%S"),
            "user_id": body.user_id,
            "decision": result["decision"],
        }
    )
    return JSONResponse(result, status_code=403 if result["decision"] == "BLOCKED" else 200)


@router.get("/rules")
def rules(request: Request):
    """The scoring rules exactly as loaded from rules.yaml, with a plain sentence each."""
    rs = _state(request).rules
    return {
        "version": rs.version,
        "half_life_hours": rs.half_life_hours,
        "window_days": rs.window_days,
        "priorities": rs.priorities,
        "rules": [
            {
                "id": rid,
                "title": RULE_TITLES[rid],
                "description": describe_rule(rs, rid),
                "impact": rs.impact(rid),
            }
            for rid in RULE_IDS
        ],
    }


@router.post("/demo/load")
def demo_load(request: Request, org_id: str = DEFAULT_ORG):
    """Fill C4 with the 7 dummy scenarios (replaces whatever was there)."""
    st = _state(request)
    st.gate_log.clear()
    clock.reset()  # fresh data should be judged at the real time
    return load_demo(st, org_id)


@router.post("/demo/reset")
def demo_reset(request: Request):
    """Empty all five collections."""
    st = _state(request)
    st.gate_log.clear()
    clock.reset()
    return reset_all(st.db)


@router.post("/demo/time")
def demo_time(body: TimeMove):
    """Move the dashboard's clock forward (to show time decay) or back to the real time."""
    if body.reset:
        clock.reset()
    else:
        clock.move_forward(body.add_hours)
    return _clock()


@router.post("/demo/attack")
def demo_attack(request: Request, body: AttackRequest, org_id: str = DEFAULT_ORG):
    """Start the live attack for one user. The page then watches the score rise."""
    started = start_attack(_state(request), org_id, body.user_id)
    if not started:
        raise HTTPException(status_code=409, detail="an attack simulation is already running")
    return {"started": True, "user_id": body.user_id}


def new_gate_log() -> deque:
    return deque(maxlen=30)
