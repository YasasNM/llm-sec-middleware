"""Check and store one incoming event (FR-01).

Both the real endpoints (api/events.py) and the dashboard's "simulate attack" button call
`ingest_event`, so a demo event goes through exactly the same checks as a real one from C1/C2/C3.
"""

from __future__ import annotations

from datetime import timedelta

from pymongo.errors import DuplicateKeyError

from c4.contracts import validate_event
from c4.db import collections as col
from c4.db.mongo import parse_ts

from .recompute import now_utc, recompute_user


class IngestError(Exception):
    """The event was refused. `detail` is the list of reasons we send back with the 422."""

    def __init__(self, detail: list[str]) -> None:
        super().__init__("; ".join(detail))
        self.detail = detail


def ingest_event(state, source: str, payload: dict) -> tuple[int, dict]:
    """Validate, de-duplicate and store one event, then rebuild that user's profile.

    `state` is app.state (it holds the db, the rules, the schema validators and the user locks).
    Returns (http_status, body). Raises IngestError when the event is invalid.
    """
    errors = validate_event(state.validators, source, payload)
    if errors:
        raise IngestError(errors)

    # A clock that runs ahead would let someone hide events in the future, so we refuse them.
    # A few minutes of slack is allowed because the sender's clock is never perfect.
    ts = parse_ts(payload["event_timestamp"])
    now = now_utc()
    if ts > now + timedelta(minutes=state.settings.future_skew_minutes):
        raise IngestError(["event_timestamp is in the future"])

    db = state.db
    org, user = payload["org_id"], payload["user_id"]

    # One lock per user: two events of the same user must not rebuild the profile at the same time.
    with state.user_lock(org, user):
        # Senders may retry, so the same event_id can arrive twice. Keep only the first copy.
        if db[col.EVENTS].find_one({"event_id": payload["event_id"]}, {"_id": 1}):
            return 200, {"duplicate": True}

        # C1, C2 and C3 each send one event per request. Only the first one of a request
        # counts as a "request" in the behaviour numbers, otherwise every request counts 3 times.
        request_counted = (
            db[col.EVENTS].find_one(
                {"org_id": org, "user_id": user, "request_id": payload["request_id"]},
                {"_id": 1},
            )
            is None
        )

        doc = {**payload, "source": source, "event_timestamp": ts, "received_at": now}
        try:
            db[col.EVENTS].insert_one(doc)
        except DuplicateKeyError:  # two copies raced each other; the unique index caught it
            return 200, {"duplicate": True}

        # FR-07: the profile is rebuilt right away (target: within 2 seconds, NFR-02)
        recompute_user(db, state.rules, org, user, now)
    return 202, {"accepted": True, "request_counted": request_counted}
