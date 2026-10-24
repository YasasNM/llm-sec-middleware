"""Block and unblock (K12). C4 only records the decision; C1 enforces it via /v1/blocklist."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from c4.auth import require_caller
from c4.core.admin import apply_action, recent_actions
from c4.core.recompute import get_profile

# Only the gateway may call these. It checks that the person really is an admin first.
router = APIRouter(
    prefix="/v1", tags=["admin actions"], dependencies=[Depends(require_caller("gateway"))]
)


class ActionBody(BaseModel):
    org_id: str = Field(min_length=1, max_length=64)
    by: str = Field(min_length=1, max_length=64)  # who decided
    reason: str = Field(min_length=1, max_length=500)  # why (a reason is always required)


def _run(request: Request, user_id: str, body: ActionBody, action: str):
    db = request.app.state.db
    try:
        apply_action(db, body.org_id, user_id, action, body.by, body.reason)
    except LookupError:
        raise HTTPException(status_code=404, detail="user not found") from None
    return get_profile(db, request.app.state.rules, body.org_id, user_id)


@router.post("/users/{user_id}/block")
def block(request: Request, user_id: str, body: ActionBody):
    return _run(request, user_id, body, "block")


@router.post("/users/{user_id}/unblock")
def unblock(request: Request, user_id: str, body: ActionBody):
    return _run(request, user_id, body, "unblock")


@router.get("/admin-actions")
def action_log(request: Request, org_id: str, user_id: str | None = None, limit: int = 100):
    return recent_actions(request.app.state.db, org_id, user_id, limit)
