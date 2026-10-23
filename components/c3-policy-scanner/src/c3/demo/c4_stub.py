"""C4 stand-in for demos and tests (blueprint 19.5). NOT the real risk profiler.

Accepts C3 events on POST /v1/events/c3 exactly like the contract says (X-Service-Key, 202 on
success, 200 for a repeated event_id, 422 for a bad body, 401 for a wrong key) and keeps a tiny
per-user risk count so a demo can show the closed loop: repeated violations raise the level.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from jsonschema import Draft202012Validator, FormatChecker

REPO = Path(__file__).resolve().parents[5]
SCHEMA = REPO / "contracts" / "schemas" / "c3_event.schema.json"
LEVELS = [(0, "P1 low"), (1, "P2 watch"), (2, "P3 review"), (3, "P4 block recommended")]


def _validator() -> Draft202012Validator | None:
    if SCHEMA.exists():
        return Draft202012Validator(json.loads(SCHEMA.read_text()), format_checker=FormatChecker())
    return None


def create_app(accepted_keys: dict[str, str] | None = None) -> FastAPI:
    keys = accepted_keys or dict(
        part.split(":", 1)[::-1] for part in os.getenv("C4_ACCEPTED_KEYS", "c3:demo-c3-key").split(",")
    )                                                   # key -> caller
    app = FastAPI(title="C4 stub (demo)")
    events: list[dict] = []
    seen: set[str] = set()
    validator = _validator()

    @app.get("/health")
    def health():
        return {"status": "ok", "stub": True, "events": len(events)}

    @app.post("/v1/events/c3", status_code=202)
    async def c3_event(request: Request, x_service_key: str | None = Header(default=None)):
        if x_service_key not in keys:
            raise HTTPException(401, "Missing or invalid service key")
        if keys[x_service_key] != "c3":
            raise HTTPException(403, "This key may not post C3 events")
        body = await request.json()
        if validator is not None:
            errs = [e.message for e in validator.iter_errors(body)]
            if errs:
                return JSONResponse({"detail": errs[:3]}, status_code=422)
        if body.get("event_id") in seen:
            return JSONResponse({"duplicate": True}, status_code=200)
        seen.add(body["event_id"])
        events.append(body)
        return {"accepted": True, "request_counted": True}

    @app.get("/_demo/events")
    def list_events(limit: int = 50):
        return list(reversed(events))[:limit]

    @app.get("/_demo/users")
    def users():
        score: dict[str, dict] = {}
        for e in events:
            u = score.setdefault(e["user_id"], {"user_id": e["user_id"], "requests": 0,
                                                "violations": 0, "leaks": 0})
            u["requests"] += 1
            u["violations"] += e["c3_verdict"] == "VIOLATION"
            u["leaks"] += e["leaked_pii_count"]
        out = []
        for u in score.values():
            level = [name for n, name in LEVELS if u["violations"] >= n][-1]
            out.append({**u, "level": level})
        return sorted(out, key=lambda u: -u["violations"])

    @app.post("/_demo/reset")
    def reset():
        events.clear()
        seen.clear()
        return {"reset": True}

    return app


app = create_app()
