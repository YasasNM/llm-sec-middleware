"""Rebuild a user's rule hits, features and profile from stored events (FR-02, FR-07).

Deterministic: the same stored events always give the same result. Late events (outbox
replays) are fine, because everything is replayed in event-time order.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from c4.db import collections as col
from c4.db.mongo import utc

from .classify import LABELS, RECOMMENDATION, priority_for
from .clock import now_utc
from .decay import score_at
from .features import behaviour_summary, build_profile, compute_features, top_contributors
from .rules import replay
from .ruleset import Ruleset


def load_events(db, org_id: str, user_id: str) -> list[dict]:
    """All stored events of one user. {"_id": 0} leaves out MongoDB's internal id."""
    rows = list(db[col.EVENTS].find({"org_id": org_id, "user_id": user_id}, {"_id": 0}))
    for r in rows:
        r["event_timestamp"] = utc(r["event_timestamp"])
    return rows


def load_hits(db, org_id: str, user_id: str) -> list[dict]:
    rows = list(db[col.HITS].find({"org_id": org_id, "user_id": user_id}, {"_id": 0}))
    for r in rows:
        r["event_timestamp"] = utc(r["event_timestamp"])
    return rows


def recompute_user(db, rs: Ruleset, org_id: str, user_id: str, now: datetime | None = None) -> dict:
    """Replay all events of one user, store hits, features and profile. Returns the profile."""
    now = now or now_utc()
    events = load_events(db, org_id, user_id)
    # Replay every event from the oldest to the newest. Same events in, same hits out.
    hits = replay(events, rs)

    # Old hits are thrown away and rebuilt, so a late event can never leave a stale result

    db[col.HITS].delete_many({"org_id": org_id, "user_id": user_id})
    if hits:
        db[col.HITS].insert_many([dict(h) for h in hits])

    feats = compute_features(events, hits, now, rs)
    # replace_one with upsert: update the features if they exist, create them if not
    db[col.FEATURES].replace_one(
        {"org_id": org_id, "user_id": user_id},
        {"org_id": org_id, "user_id": user_id, **feats, "updated_at": now},
        upsert=True,
    )
    profile = build_profile(org_id, user_id, events, hits, now, rs)
    db[col.PROFILES].update_one(
        {"org_id": org_id, "user_id": user_id},
        # $setOnInsert: a new user starts "active", but an existing status (blocked) is kept
        {"$set": profile, "$setOnInsert": {"status": "active"}},
        upsert=True,
    )
    return get_profile(db, rs, org_id, user_id, now)


def get_profile(db, rs: Ruleset, org_id: str, user_id: str, now: datetime | None = None):
    """Stored profile with the score re-calculated for `now` (decay moves with time)."""
    now = now or now_utc()
    doc = db[col.PROFILES].find_one({"org_id": org_id, "user_id": user_id}, {"_id": 0})
    if doc is None:
        return None
    events = load_events(db, org_id, user_id)
    hits = load_hits(db, org_id, user_id)
    # Rebuild for "now" instead of trusting the stored number, because old evidence fades
    fresh = build_profile(org_id, user_id, events, hits, now, rs)
    # The block status is a human decision, so it always comes from the stored profile
    fresh["status"] = doc.get("status", "active")
    return fresh


def live_scores(db, rs: Ruleset, org_id: str, now: datetime) -> dict[str, dict]:
    """Decayed score, priority and main reason for every user of an org (dashboard list)."""
    since = now - timedelta(days=rs.window_days)
    # Fetch all rule hits of the org in one query and group them by user
    # (much faster than one query per user)
    by_user: dict[str, list[dict]] = {}
    for h in db[col.HITS].find({"org_id": org_id, "event_timestamp": {"$gte": since}}, {"_id": 0}):
        h["event_timestamp"] = utc(h["event_timestamp"])
        by_user.setdefault(h["user_id"], []).append(h)
    out = {}
    for user_id, hits in by_user.items():
        score = round(score_at(hits, now, rs), 1)
        contrib = top_contributors(hits, now, rs)
        out[user_id] = {"score": score, "main_reason": contrib[0]["title"] if contrib else None}
    return out


__all__ = [
    "recompute_user",
    "get_profile",
    "live_scores",
    "load_events",
    "load_hits",
    "now_utc",
    "LABELS",
    "RECOMMENDATION",
    "priority_for",
    "behaviour_summary",
]
