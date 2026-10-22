"""Contract-faithful C4 stand-in for C2 local development."""

from __future__ import annotations

from typing import Optional

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

app = FastAPI(title="C4 stub")
EVENTS: list[dict] = []


class C2EventIn(BaseModel):
    event_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.:-]+$")
    org_id: str
    user_id: str
    request_id: str
    session_id: str
    event_timestamp: str
    c2_verdict: str
    c2_score: float = Field(ge=0, le=1)

    model_config = {"extra": "forbid"}


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "c4-stub", "events": len(EVENTS)}


@app.post("/v1/events/c2")
def receive_c2(
    event: C2EventIn,
    x_service_key: Optional[str] = Header(default=None, alias="X-Service-Key"),
) -> dict:
    if not x_service_key:
        raise HTTPException(status_code=401, detail="missing service key")
    payload = event.model_dump()
    if any(item["event_id"] == payload["event_id"] for item in EVENTS):
        return {"duplicate": True}
    EVENTS.append(payload)
    return {"accepted": True, "request_counted": True}


@app.get("/events")
def list_events() -> list[dict]:
    return EVENTS
