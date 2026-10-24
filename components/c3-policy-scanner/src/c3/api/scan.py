"""POST /v1/scan and the admin audit lookup (blueprint 10.1, FR8, FR19; contract c3_scan_*)."""
from __future__ import annotations

import asyncio
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from c3.api.auth import require_service_key
from c3.events import build_event

log = logging.getLogger("c3.scan")
ID = r"^[A-Za-z0-9_.:-]+$"
DEPENDENCY_CODES = {"INDEX_UNAVAILABLE", "MODEL_UNAVAILABLE", "RETRIEVER_MISMATCH", "NO_CONTEXT"}

router = APIRouter(tags=["scan"], dependencies=[Depends(require_service_key)])


class ScanRequest(BaseModel):
    """Mirrors contracts/schemas/c3_scan_request.schema.json. Unknown fields are rejected."""

    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(min_length=1, max_length=64, pattern=ID)
    org_id: str = Field(min_length=1, max_length=64, pattern=ID)
    user_id: str = Field(min_length=1, max_length=64, pattern=ID)
    session_id: str = Field(min_length=1, max_length=64, pattern=ID)
    response_text: str                       # size is checked in the route so it can return 413
    placeholder_manifest: list[Annotated[str, Field(pattern=r"^\[[A-Z_]+_[0-9]+\]$")]]
    categories: list[str] | None = None

    @field_validator("response_text")
    @classmethod
    def not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("response_text must not be empty")
        return v


@router.post("/v1/scan")
async def scan(req: ScanRequest, request: Request):
    c = request.app.state.container
    if len(req.response_text) > c.cfg.api.max_response_chars:
        raise HTTPException(413, "response_text is too large")
    if c.registry.get(req.org_id, create=False) is None:
        raise HTTPException(404, "Unknown organisation")

    body = req.model_dump(exclude_none=True)
    loop = asyncio.get_running_loop()
    try:
        verdict = await asyncio.wait_for(
            loop.run_in_executor(None, c.scanner.scan, body), timeout=c.cfg.api.timeout_s
        )
    except TimeoutError:
        verdict = c.scanner._error(body, "SCAN_TIMEOUT", 0.0)
        verdict["latency_ms"] = int(c.cfg.api.timeout_s * 1000)

    if verdict["decision_source"] != "error_fail_closed":
        try:                                   # never blocks the answer; never raises (IR4)
            c.emitter.emit(build_event(body, verdict))
        except Exception:
            log.exception("could not queue C3 event for %s", req.request_id)
    else:
        log.error("scan failed closed: request=%s code=%s", req.request_id,
                  verdict.get("error_code"))

    status = 503 if verdict.get("error_code") in DEPENDENCY_CODES else 200
    return JSONResponse(verdict, status_code=status)


OrgId = Annotated[str, Path(pattern=r"^[A-Za-z0-9_.:-]{1,64}$")]


@router.get("/v1/orgs/{org_id}/scans/{request_id}")
def scan_audit(org_id: OrgId, request_id: Annotated[str, Path(pattern=ID, max_length=64)],
               request: Request):
    """Admin trace for one request (FR19). Hashes and ids only, never the response text."""
    rec = request.app.state.container.db.get_scan(org_id, request_id)
    if rec is None:
        raise HTTPException(404, "No scan recorded for this request")
    return rec
