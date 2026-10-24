"""Admin read endpoints. Only the gateway calls these (after checking the admin role)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from c4.auth import require_caller
from c4.core.overview import collect_users
from c4.core.recompute import get_profile, load_events, load_hits
from c4.core.timeline import build_timeline

# Every route in this file needs the gateway key (the gateway has already checked the admin role)
router = APIRouter(prefix="/v1", tags=["users"], dependencies=[Depends(require_caller("gateway"))])


@router.get("/users")
def list_users(
    request: Request,
    org_id: str,
    priority: str | None = Query(default=None, pattern="^P[1-4]$"),
    status: str | None = Query(default=None, pattern="^(active|blocked)$"),
    q: str | None = None,
):
    rows, summary = collect_users(request.app.state.db, request.app.state.rules, org_id)
    # The summary counts everybody; the filters only shrink the list below it.
    users = [
        r
        for r in rows
        if (not priority or r["priority"] == priority)
        and (not status or r["status"] == status)
        and (not q or q.lower() in r["user_id"].lower())
    ]
    return {"summary": summary, "users": users}


@router.get("/users/{user_id}/profile")
def profile(request: Request, user_id: str, org_id: str):
    # get_profile re-scores the user for the current time, so decay is always up to date
    prof = get_profile(request.app.state.db, request.app.state.rules, org_id, user_id)
    if prof is None:
        raise HTTPException(status_code=404, detail="user not found")
    return prof


@router.get("/users/{user_id}/timeline")
def timeline(request: Request, user_id: str, org_id: str, limit: int = Query(100, ge=1, le=1000)):
    # Every event of the user with the rules that fired on it and the score right after
    db, rs = request.app.state.db, request.app.state.rules
    events = load_events(db, org_id, user_id)
    if not events:
        raise HTTPException(status_code=404, detail="user not found")
    return build_timeline(events, load_hits(db, org_id, user_id), rs, limit)
