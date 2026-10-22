"""Service-key check. C4 knows the caller from the key (blueprint section 12).

This is a small local version. When `packages/mediguard-common` exists (auth.py), switch to it.
401 = missing or wrong key.  403 = valid key, but this caller is not allowed on this route.
"""

from __future__ import annotations

import hmac

from fastapi import Header, HTTPException, Request


def require_caller(*allowed: str):
    def dependency(
        request: Request,
        x_service_key: str | None = Header(default=None, alias="X-Service-Key"),
    ) -> str:
        if not x_service_key:
            raise HTTPException(status_code=401, detail="missing service key")
        caller = None
        for key, name in request.app.state.key_map.items():
            if hmac.compare_digest(key.encode(), x_service_key.encode()):
                caller = name
        if caller is None:
            raise HTTPException(status_code=401, detail="invalid service key")
        if caller not in allowed:
            raise HTTPException(status_code=403, detail="caller not allowed on this route")
        return caller

    return dependency
