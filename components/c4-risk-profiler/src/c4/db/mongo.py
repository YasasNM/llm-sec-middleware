"""MongoDB connection. Use `mongomock://` as the URL in tests (no real database needed)."""

from __future__ import annotations

from datetime import UTC, datetime
from urllib.parse import urlparse

from .collections import ensure_indexes


def db_name_from_url(url: str) -> str:
    """'mongodb://localhost:27017/c4' -> 'c4'. Falls back to c4 if the URL has no name."""
    path = urlparse(url).path.strip("/")
    return path or "c4"


def get_db(url: str):
    """Connect and return the database object, with the indexes in place."""
    if url.startswith("mongomock://"):
        # An in-memory fake of MongoDB, so the tests run without installing anything
        import mongomock

        client = mongomock.MongoClient()
    else:
        from pymongo import MongoClient

        # tz_aware=True: dates come back with a time zone, so we never mix naive and aware times
        client = MongoClient(url, tz_aware=True, serverSelectionTimeoutMS=3000)
    db = client[db_name_from_url(url)]
    ensure_indexes(db)
    return db


def utc(dt: datetime) -> datetime:
    """MongoDB may hand back naive datetimes. Treat them as UTC."""
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def parse_ts(value: str) -> datetime:
    """'2026-08-23T07:10:01Z' (milliseconds allowed) -> aware UTC datetime."""
    # Python's fromisoformat does not read a trailing Z in older versions, so swap it for +00:00
    return utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
