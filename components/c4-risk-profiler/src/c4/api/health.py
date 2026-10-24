"""GET /health: is C4 alive, and can it reach MongoDB? (open, no key needed)"""

from __future__ import annotations

from fastapi import APIRouter, Request

from c4 import __version__

router = APIRouter(tags=["health"])


@router.get("/health")
def health(request: Request):
    try:
        # Any cheap call proves the database answers
        request.app.state.db.list_collection_names()
        mongo = "ok"
    except Exception:  # noqa: BLE001  (any database problem is reported, not raised)
        mongo = "error"
    return {
        "status": "ok" if mongo == "ok" else "degraded",
        "service": "c4",
        "version": __version__,
        "mongo": mongo,
        "ruleset_version": request.app.state.rules.version,
    }
