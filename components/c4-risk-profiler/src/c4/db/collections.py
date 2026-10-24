"""MongoDB collection names and indexes (database `c4`). Matches BLUEPRINT.md section 13.4."""

from __future__ import annotations

# The five collections. Only `security_events` is evidence; the other four can be rebuilt
# from it at any time.
EVENTS = "security_events"  # raw events from C1, C2, C3
HITS = "rule_hits"  # one document per rule that fired
FEATURES = "user_behaviour_features"  # counters and histograms per user
PROFILES = "user_risk_profiles"  # the result per user (score, priority, status)
ACTIONS = "admin_actions"  # audit log of block / unblock decisions


def ensure_indexes(db) -> None:
    """Create the indexes (safe to run again, MongoDB ignores ones that already exist)."""
    # Unique event_id is how duplicate events are caught
    db[EVENTS].create_index("event_id", unique=True)
    # The main lookup: all events of one user in time order
    db[EVENTS].create_index([("org_id", 1), ("user_id", 1), ("event_timestamp", 1)])
    db[EVENTS].create_index("request_id")
    db[HITS].create_index([("org_id", 1), ("user_id", 1), ("event_timestamp", 1)])
    # Unique (org, user) means a user can never have two profiles
    db[FEATURES].create_index([("org_id", 1), ("user_id", 1)], unique=True)
    db[PROFILES].create_index([("org_id", 1), ("user_id", 1)], unique=True)
    # For the dashboard list: highest score first
    db[PROFILES].create_index([("org_id", 1), ("score", -1)])
    db[ACTIONS].create_index([("org_id", 1), ("user_id", 1), ("at", 1)])
