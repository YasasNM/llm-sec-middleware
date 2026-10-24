"""Service-key check. C4 knows the caller from the key (blueprint section 12).

This is a small local version. When `packages/mediguard-common` exists (auth.py), switch to it.
401 = missing or wrong key.  403 = valid key, but this caller is not allowed on this route.
"""

from __future__ import annotations

import hmac

from fastapi import Header, HTTPException, Request


def require_caller(*allowed: str):
    """Make a FastAPI dependency that only lets the named callers through.

    Example: require_caller("c1", "gateway") accepts the C1 key and the gateway key.
    The dependency returns the caller's name, so a route can tell who called.
    """

    def dependency(
        request: Request,
        x_service_key: str | None = Header(default=None, alias="X-Service-Key"),
    ) -> str:
        if not x_service_key:
            raise HTTPException(status_code=401, detail="missing service key")

        # Find which caller owns this key. compare_digest takes the same time for a right and
        # a wrong key, so an attacker cannot guess the key one character at a time.
        caller = None
        for key, name in request.app.state.key_map.items():
            if hmac.compare_digest(key.encode(), x_service_key.encode()):
                caller = name
        if caller is None:
            raise HTTPException(status_code=401, detail="invalid service key")

        # The key is real, but this route is not meant for that caller (for example the C2 key
        # on the C3 endpoint). That is "forbidden", not "unauthorised".
        if caller not in allowed:
            raise HTTPException(status_code=403, detail="caller not allowed on this route")
        return caller

    return dependency
