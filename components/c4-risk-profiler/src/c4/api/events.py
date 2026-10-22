"""POST /v1/events/c1 | c2 | c3  (FR-01, M1: validate, de-duplicate, count requests)."""

from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pymongo.errors import DuplicateKeyError

from c4.auth import require_caller
from c4.contracts import SOURCES, validate_event
from c4.core.recompute import now_utc, recompute_user
from c4.db import collections as col
from c4.db.mongo import parse_ts

router = APIRouter(prefix="/v1/events", tags=["events"])


def _make_route(source: str):
    def receive(
        request: Request,
        payload: Annotated[dict, Body()],
        _caller: Annotated[str, Depends(require_caller(source))],
    ):
        app = request.app
        errors = validate_event(app.state.validators, source, payload)
        if errors:
            raise HTTPException(status_code=422, detail=errors)

        ts = parse_ts(payload["event_timestamp"])
        now = now_utc()
        if ts > now + timedelta(minutes=app.state.settings.future_skew_minutes):
            raise HTTPException(status_code=422, detail=["event_timestamp is in the future"])

        db = app.state.db
        org, user = payload["org_id"], payload["user_id"]
        with app.state.user_lock(org, user):
            if db[col.EVENTS].find_one({"event_id": payload["event_id"]}, {"_id": 1}):
                return JSONResponse({"duplicate": True}, status_code=200)
            request_counted = (
                db[col.EVENTS].find_one(
                    {"org_id": org, "user_id": user, "request_id": payload["request_id"]},
                    {"_id": 1},
                )
                is None
            )
            doc = {**payload, "source": source, "event_timestamp": ts, "received_at": now}
            try:
                db[col.EVENTS].insert_one(doc)
            except DuplicateKeyError:
                return JSONResponse({"duplicate": True}, status_code=200)
            recompute_user(db, app.state.rules, org, user, now)  # FR-07, within 2 s (NFR-02)
        return JSONResponse({"accepted": True, "request_counted": request_counted}, status_code=202)

    return receive


for _src in SOURCES:
    router.add_api_route(f"/{_src}", _make_route(_src), methods=["POST"], name=f"events_{_src}")
