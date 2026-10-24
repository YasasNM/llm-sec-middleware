"""POST /v1/events/c1 | c2 | c3  (FR-01).

C1, C2 and C3 each have their own URL and their own service key, so C4 always knows who sent
an event and a component can never post on behalf of another one.
"""

from typing import Annotated

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from c4.auth import require_caller
from c4.contracts import SOURCES
from c4.core.ingest import IngestError, ingest_event

router = APIRouter(prefix="/v1/events", tags=["events"])


def _make_route(source: str):
    """Build the endpoint function for one source (c1, c2 or c3)."""

    def receive(
        request: Request,
        payload: Annotated[dict, Body()],
        # The key must belong to this source, otherwise require_caller answers 401 or 403.
        _caller: Annotated[str, Depends(require_caller(source))],
    ):
        try:
            status, body = ingest_event(request.app.state, source, payload)
        except IngestError as err:
            raise HTTPException(status_code=422, detail=err.detail) from err
        return JSONResponse(body, status_code=status)

    return receive


# Register the three endpoints from one function so they can never drift apart.
for _src in SOURCES:
    router.add_api_route(f"/{_src}", _make_route(_src), methods=["POST"], name=f"events_{_src}")
