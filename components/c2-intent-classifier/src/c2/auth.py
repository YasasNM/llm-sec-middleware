from __future__ import annotations

import hmac
from typing import Optional

from fastapi import Header, HTTPException

from c2.config import get_settings


def _accepted() -> dict[str, str]:
    mapping: dict[str, str] = {}
    raw = get_settings().accepted_keys or ""
    for part in raw.split(","):
        part = part.strip()
        if not part or ":" not in part:
            continue
        name, key = part.split(":", 1)
        mapping[key] = name.strip()
    return mapping


def require_gateway(
    x_service_key: Optional[str] = Header(default=None, alias="X-Service-Key"),
) -> str:
    settings = get_settings()
    if settings.allow_unauthenticated:
        return "gateway"
    if not x_service_key:
        raise HTTPException(status_code=401, detail="missing service key")
    accepted = _accepted()
    for key, name in accepted.items():
        if hmac.compare_digest(key, x_service_key):
            if name != "gateway":
                raise HTTPException(status_code=403, detail="caller not allowed")
            return name
    raise HTTPException(status_code=401, detail="invalid service key")
