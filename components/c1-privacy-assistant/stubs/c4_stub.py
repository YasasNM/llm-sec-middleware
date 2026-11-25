"""Tiny stand-in for C4 (port 8004), so C1 can be developed and demoed alone. Development only.

It serves:
- GET  /v1/blocklist?org_id=...  -> the org's blocked users from stubs/blocklist.json. The file
  is read on every call, so you can edit it while everything runs and see a user get blocked.
- POST /v1/events/c1             -> checks each event against contracts/schemas/c1_event and
  prints only the event_id and masking_percentage (never anything else).

Run it from components/c1-privacy-assistant:

    uvicorn stubs.c4_stub:app --port 8004

If MG_ACCEPTED_KEYS is set (for example "c1:dev-c1-key"), only that caller's key is accepted.
"""

import json
import os
from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from jsonschema import Draft202012Validator, FormatChecker

STUB_FOLDER = Path(__file__).resolve().parent
BLOCKLIST_FILE = STUB_FOLDER / "blocklist.json"
EVENT_SCHEMA = STUB_FOLDER.parents[2] / "contracts" / "schemas" / "c1_event.schema.json"

app = FastAPI(title="C4 stub (development only)")
seen_event_ids: set[str] = set()


# The key C1 must present, from MG_ACCEPTED_KEYS ("c1:<key>"); empty means accept any key
def accepted_c1_key() -> str:
    for pair in os.environ.get("MG_ACCEPTED_KEYS", "").split(","):
        name, _, key = pair.strip().partition(":")
        if name == "c1":
            return key
    return ""


# Stop the request with 401 unless a key is present (and is C1's key, when one is configured)
def check_key(presented: str | None) -> None:
    expected = accepted_c1_key()
    if not presented or (expected and presented != expected):
        raise HTTPException(status_code=401, detail="missing or invalid service key")


# Current UTC time in the contract's format, ending in Z
def utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


# Return the org's block list, read fresh from blocklist.json (unknown org = nobody blocked)
@app.get("/v1/blocklist")
def blocklist(org_id: str, x_service_key: str | None = Header(default=None)) -> dict:
    check_key(x_service_key)
    lists = json.loads(BLOCKLIST_FILE.read_text())
    return {"org_id": org_id, "user_ids": lists.get(org_id, []), "generated_at": utc_now()}


# The list of contract problems in one event; empty means the event is valid
def event_problems(event: object) -> list[str]:
    schema = json.loads(EVENT_SCHEMA.read_text())
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    return [error.message for error in validator.iter_errors(event)]


# Accept one C1 event like C4 would: 422 if invalid, 200 for a repeat, otherwise 202
@app.post("/v1/events/c1")
async def receive_c1_event(request: Request, x_service_key: str | None = Header(default=None)):
    check_key(x_service_key)
    try:
        event = await request.json()
    except ValueError:
        return JSONResponse(status_code=422, content={"detail": "body is not JSON"})
    problems = event_problems(event)
    if problems:
        print(f"[c4_stub] rejected event: {len(problems)} contract problem(s)")
        return JSONResponse(
            status_code=422, content={"detail": "event breaks the c1_event contract"}
        )
    if event["event_id"] in seen_event_ids:
        return JSONResponse(status_code=200, content={"duplicate": True})
    seen_event_ids.add(event["event_id"])
    print(
        f"[c4_stub] event_id={event['event_id']} masking_percentage={event['masking_percentage']}"
    )
    return JSONResponse(status_code=202, content={"accepted": True, "request_counted": True})
