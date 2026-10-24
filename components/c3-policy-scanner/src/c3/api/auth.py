"""Service-to-service auth: X-Service-Key on every endpoint except /health (NFR9, K23).

This is a small local version. When `packages/mediguard-common` ships its auth helper, swap
this module for it. The behaviour (401 on a missing or wrong key) is the same.
"""
from __future__ import annotations

import hmac

from fastapi import Header, HTTPException, Request, status


def require_service_key(request: Request, x_service_key: str | None = Header(default=None)) -> str:
    keys: dict[str, str] = request.app.state.container.keys
    if not x_service_key or not keys:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing or invalid service key")
    caller = None
    for key, name in keys.items():
        if hmac.compare_digest(key.encode(), x_service_key.encode()):
            caller = name
    if caller is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing or invalid service key")
    return caller
