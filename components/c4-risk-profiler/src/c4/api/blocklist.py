"""GET /v1/blocklist?org_id=  (read by C1, cached there for up to 30 s)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from c4.auth import require_caller
from c4.core.admin import blocked_user_ids
from c4.core.recompute import now_utc

router = APIRouter(prefix="/v1", tags=["blocklist"])


# C1 uses this list to refuse requests from blocked users; the gateway may read it too.
@router.get("/blocklist", dependencies=[Depends(require_caller("c1", "gateway"))])
def blocklist(request: Request, org_id: str):
    return {
        "org_id": org_id,
        "user_ids": blocked_user_ids(request.app.state.db, org_id),
        "generated_at": now_utc().strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
