"""Service-key check and privacy-safe error replies for every C1 route except /health."""

from typing import Annotated

from fastapi import Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from c1.common import caller_for_key, parse_accepted_keys

ALLOWED_CALLER = "gateway"


# Let the request through only if X-Service-Key belongs to the gateway (K23)
def require_gateway(request: Request, x_service_key: Annotated[str | None, Header()] = None) -> str:
    accepted = parse_accepted_keys(request.app.state.settings.accepted_keys)
    caller = caller_for_key(x_service_key, accepted)
    if caller is None:
        raise HTTPException(status_code=401, detail="missing or invalid service key")
    if caller != ALLOWED_CALLER:
        raise HTTPException(status_code=403, detail="caller not allowed on this route")
    return caller


# Reply to a bad body with 422, listing where and why, but never echoing the text that was sent
async def validation_error_without_input(request: Request, error: RequestValidationError):
    problems = [
        {"loc": list(item["loc"]), "type": item["type"], "msg": item["msg"]}
        for item in error.errors()
    ]
    return JSONResponse(status_code=422, content={"detail": problems})
