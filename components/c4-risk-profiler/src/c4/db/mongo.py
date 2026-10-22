"""MongoDB connection. Use `mongomock://` as the URL in tests (no real database needed)."""

from __future__ import annotations

from datetime import UTC, datetime
from urllib.parse import urlparse

from .collections import ensure_indexes


def db_name_from_url(url: str) -> str:
    path = urlparse(url).path.strip("/")
    return path or "c4"


def get_db(url: str):
    if url.startswith("mongomock://"):
        import mongomock

        client = mongomock.MongoClient()
    else:
        from pymongo import MongoClient

        client = MongoClient(url, tz_aware=True, serverSelectionTimeoutMS=3000)
    db = client[db_name_from_url(url)]
    ensure_indexes(db)
    return db


def utc(dt: datetime) -> datetime:
    """MongoDB may hand back naive datetimes. Treat them as UTC."""
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def parse_ts(value: str) -> datetime:
    """'2026-08-23T07:10:01Z' (milliseconds allowed) -> aware UTC datetime."""
    return utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
