"""Block and unblock (K12). C4 only records the decision; C1 enforces it via /v1/blocklist."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from c4.auth import require_caller
from c4.core.recompute import get_profile
from c4.db import collections as col

router = APIRouter(
    prefix="/v1", tags=["admin actions"], dependencies=[Depends(require_caller("gateway"))]
)


class ActionBody(BaseModel):
    org_id: str = Field(min_length=1, max_length=64)
    by: str = Field(min_length=1, max_length=64)
    reason: str = Field(min_length=1, max_length=500)


def _apply(request: Request, user_id: str, body: ActionBody, action: str, status: str):
    db, rs = request.app.state.db, request.app.state.rules
    doc = db[col.PROFILES].find_one({"org_id": body.org_id, "user_id": user_id}, {"_id": 0})
    if doc is None:
        raise HTTPException(status_code=404, detail="user not found")
    if doc.get("status", "active") != status:
        db[col.PROFILES].update_one(
            {"org_id": body.org_id, "user_id": user_id}, {"$set": {"status": status}}
        )
        db[col.ACTIONS].insert_one(
            {
                "org_id": body.org_id,
                "user_id": user_id,
                "action": action,
                "by": body.by,
                "reason": body.reason,
                "at": datetime.now(UTC),
            }
        )
    return get_profile(db, rs, body.org_id, user_id)


@router.post("/users/{user_id}/block")
def block(request: Request, user_id: str, body: ActionBody):
    return _apply(request, user_id, body, "block", "blocked")


@router.post("/users/{user_id}/unblock")
def unblock(request: Request, user_id: str, body: ActionBody):
    return _apply(request, user_id, body, "unblock", "active")


@router.get("/admin-actions")
def action_log(request: Request, org_id: str, user_id: str | None = None, limit: int = 100):
    query = {"org_id": org_id, **({"user_id": user_id} if user_id else {})}
    rows = list(
        request.app.state.db[col.ACTIONS]
        .find(query, {"_id": 0})
        .sort([("at", -1), ("_id", -1)])
        .limit(limit)
    )
    for r in rows:
        r["at"] = r["at"].strftime("%Y-%m-%dT%H:%M:%SZ")
    return rows
