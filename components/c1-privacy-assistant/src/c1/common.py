"""Small local stand-in for packages/mediguard-common (service-key check + C1 event sender).

The shared package is still empty, so C1 carries its own minimal copy. When the gateway owner
ships mediguard-common, these functions are replaced by imports from it.

Privacy rule: events hold ids, a timestamp and masking_percentage only. Log lines hold the
event_id and an HTTP status only. No prompt text or entity value ever passes through here.
"""

import hmac
import json
import logging
import sqlite3
import time
import urllib.error
import urllib.request
import uuid
from datetime import UTC, datetime

logger = logging.getLogger("c1.common")

EVENT_PATH = "/v1/events/c1"
RETRY_DELAYS = (0.5, 2.0, 5.0)


# Turn "gateway:abc,c1:def" into {"gateway": "abc", "c1": "def"}
def parse_accepted_keys(text: str) -> dict[str, str]:
    keys = {}
    for pair in text.split(","):
        name, separator, key = pair.strip().partition(":")
        if separator and name and key:
            keys[name] = key
    return keys


# Return the caller's name if the presented key is one we accept, otherwise None
def caller_for_key(presented_key: str | None, accepted: dict[str, str]) -> str | None:
    if not presented_key:
        return None
    for name, key in accepted.items():
        if hmac.compare_digest(presented_key.encode(), key.encode()):
            return name
    return None


# Make a new event id that is never reused, for example evt-c1-<uuid4>
def new_event_id() -> str:
    return f"evt-c1-{uuid.uuid4()}"


# Current UTC time in ISO 8601 ending in Z, for example 2026-10-23T07:10:01Z
def utc_now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


# Build the agreed C1 event: ids, time and masking percentage only, never any text
def build_c1_event(
    org_id: str, user_id: str, request_id: str, session_id: str, masking_percentage: float
) -> dict:
    return {
        "event_id": new_event_id(),
        "org_id": org_id,
        "user_id": user_id,
        "request_id": request_id,
        "session_id": session_id,
        "event_timestamp": utc_now_iso(),
        "masking_percentage": masking_percentage,
    }


# POST one event to C4 and return the HTTP status, or None if C4 could not be reached
def post_event(c4_url: str, service_key: str, event: dict, timeout: float = 2.0) -> int | None:
    request = urllib.request.Request(
        c4_url.rstrip("/") + EVENT_PATH,
        data=json.dumps(event).encode(),
        method="POST",
        headers={"Content-Type": "application/json", "X-Service-Key": service_key},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status
    except urllib.error.HTTPError as error:
        return error.code
    except (urllib.error.URLError, OSError):
        return None


# True when C4 has the event: 202 accepted, or 200 because it was a duplicate
def is_delivered(status: int | None) -> bool:
    return status in (200, 202)


# True when trying again later could help: C4 unreachable or a 5xx server error
def is_worth_retrying(status: int | None) -> bool:
    return status is None or status >= 500


# Deliver an event with retries; if C4 stays unreachable, keep it in the outbox for later
def send_event(
    c4_url: str, service_key: str, event: dict, outbox_path: str, delays=RETRY_DELAYS
) -> bool:
    status = post_event(c4_url, service_key, event)
    for delay in delays:
        if not is_worth_retrying(status):
            break
        time.sleep(delay)
        status = post_event(c4_url, service_key, event)
    if is_delivered(status):
        return True
    if is_worth_retrying(status):
        save_to_outbox(outbox_path, event)
    logger.warning("c1 event not delivered event_id=%s status=%s", event["event_id"], status)
    return False


# Open the SQLite outbox file and create its table the first time
def open_outbox(path: str) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE IF NOT EXISTS outbox (event_id TEXT PRIMARY KEY, payload TEXT NOT NULL)"
    )
    return connection


# Store an undelivered event so it can be resent later
def save_to_outbox(path: str, event: dict) -> None:
    connection = open_outbox(path)
    try:
        connection.execute(
            "INSERT OR IGNORE INTO outbox (event_id, payload) VALUES (?, ?)",
            (event["event_id"], json.dumps(event)),
        )
        connection.commit()
    finally:
        connection.close()


# Read every event that is waiting in the outbox
def load_outbox(path: str) -> list[dict]:
    connection = open_outbox(path)
    try:
        rows = connection.execute("SELECT payload FROM outbox ORDER BY rowid").fetchall()
    finally:
        connection.close()
    return [json.loads(row[0]) for row in rows]


# Remove one event from the outbox once it no longer needs sending
def remove_from_outbox(path: str, event_id: str) -> None:
    connection = open_outbox(path)
    try:
        connection.execute("DELETE FROM outbox WHERE event_id = ?", (event_id,))
        connection.commit()
    finally:
        connection.close()


# Try every waiting event once; return how many C4 accepted
def resend_outbox(c4_url: str, service_key: str, outbox_path: str) -> int:
    delivered = 0
    for event in load_outbox(outbox_path):
        status = post_event(c4_url, service_key, event)
        if is_delivered(status):
            delivered += 1
        elif not is_worth_retrying(status):
            logger.warning("c1 event rejected event_id=%s status=%s", event["event_id"], status)
        if not is_worth_retrying(status):
            remove_from_outbox(outbox_path, event["event_id"])
    return delivered
