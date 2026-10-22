"""GET /v1/blocklist?org_id=  (read by C1, cached there for up to 30 s)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from c4.auth import require_caller
from c4.core.recompute import now_utc
from c4.db import collections as col

router = APIRouter(prefix="/v1", tags=["blocklist"])


@router.get("/blocklist", dependencies=[Depends(require_caller("c1", "gateway"))])
def blocklist(request: Request, org_id: str):
    rows = request.app.state.db[col.PROFILES].find(
        {"org_id": org_id, "status": "blocked"}, {"user_id": 1}
    )
    return {
        "org_id": org_id,
        "user_ids": sorted(r["user_id"] for r in rows),
        "generated_at": now_utc().strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
