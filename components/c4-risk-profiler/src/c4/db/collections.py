"""MongoDB collection names and indexes (database `c4`). Matches BLUEPRINT.md section 13.4."""

from __future__ import annotations

EVENTS = "security_events"
HITS = "rule_hits"
FEATURES = "user_behaviour_features"
PROFILES = "user_risk_profiles"
ACTIONS = "admin_actions"


def ensure_indexes(db) -> None:
    db[EVENTS].create_index("event_id", unique=True)
    db[EVENTS].create_index([("org_id", 1), ("user_id", 1), ("event_timestamp", 1)])
    db[EVENTS].create_index("request_id")
    db[HITS].create_index([("org_id", 1), ("user_id", 1), ("event_timestamp", 1)])
    db[FEATURES].create_index([("org_id", 1), ("user_id", 1)], unique=True)
    db[PROFILES].create_index([("org_id", 1), ("user_id", 1)], unique=True)
    db[PROFILES].create_index([("org_id", 1), ("score", -1)])
    db[ACTIONS].create_index([("org_id", 1), ("user_id", 1), ("at", 1)])
