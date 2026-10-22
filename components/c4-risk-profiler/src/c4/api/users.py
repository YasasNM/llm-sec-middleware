"""Admin read endpoints. Only the gateway calls these (after checking the admin role)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from c4.auth import require_caller
from c4.core.classify import LABELS, priority_for
from c4.core.recompute import get_profile, live_scores, load_events, load_hits, now_utc
from c4.core.timeline import build_timeline
from c4.db import collections as col

router = APIRouter(prefix="/v1", tags=["users"], dependencies=[Depends(require_caller("gateway"))])


@router.get("/users")
def list_users(
    request: Request,
    org_id: str,
    priority: str | None = Query(default=None, pattern="^P[1-4]$"),
    status: str | None = Query(default=None, pattern="^(active|blocked)$"),
    q: str | None = None,
):
    db, rs = request.app.state.db, request.app.state.rules
    now = now_utc()
    live = live_scores(db, rs, org_id, now)
    rows = []
    for p in db[col.PROFILES].find({"org_id": org_id}, {"_id": 0}):
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
    users = [
        r
        for r in rows
        if (not priority or r["priority"] == priority)
        and (not status or r["status"] == status)
        and (not q or q.lower() in r["user_id"].lower())
    ]
    users.sort(key=lambda r: (-r["score"], r["user_id"]))
    return {"summary": summary, "users": users}


@router.get("/users/{user_id}/profile")
def profile(request: Request, user_id: str, org_id: str):
    prof = get_profile(request.app.state.db, request.app.state.rules, org_id, user_id)
    if prof is None:
        raise HTTPException(status_code=404, detail="user not found")
    return prof


@router.get("/users/{user_id}/timeline")
def timeline(request: Request, user_id: str, org_id: str, limit: int = Query(100, ge=1, le=1000)):
    db, rs = request.app.state.db, request.app.state.rules
    events = load_events(db, org_id, user_id)
    if not events:
        raise HTTPException(status_code=404, detail="user not found")
    return build_timeline(events, load_hits(db, org_id, user_id), rs, limit)
