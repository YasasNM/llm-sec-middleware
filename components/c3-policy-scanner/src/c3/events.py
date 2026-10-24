"""EventEmitter: one C3 event to C4 per scan, in the background (FR9, blueprint 10.3).

  * Agreed fields only (contract `c3_event`). No response text, no entity values.
  * `POST {C4_URL}/v1/events/c3` with `X-Service-Key`.
  * 3 attempts with backoff, then the event stays `pending` in the SQLite outbox and a
    background loop resends it later (K8). A C4 outage never blocks or slows a scan.
  * 200 {"duplicate": true} and 202 both count as delivered. 4xx other than 401/403/429 means
    the payload is wrong: the event is marked `failed` and not retried forever.
"""
from __future__ import annotations

import json
import logging
import queue
import threading
import time
import uuid
from datetime import UTC, datetime

import httpx

from c3.storage.db import Database

log = logging.getLogger("c3.events")
DELIVERED = (200, 202)
NO_RETRY = (400, 422)


def now_z() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def build_event(req: dict, verdict: dict) -> dict:
    """Agreed fields only. Raises if called for a fail-closed error (IR4)."""
    if verdict["decision_source"] == "error_fail_closed":
        raise ValueError("no event is sent for a fail-closed error")
    return {
        "event_id": f"evt-c3-{uuid.uuid4()}",
        "org_id": req["org_id"],
        "user_id": req["user_id"],
        "request_id": req["request_id"],
        "session_id": req["session_id"],
        "event_timestamp": now_z(),
        "c3_verdict": verdict["c3_verdict"],
        "clause_id": verdict["clause_id"],
        "leaked_pii_detected": verdict["leaked_pii_detected"],
        "leaked_pii_count": verdict["leaked_pii_count"],
    }


class EventEmitter:
    def __init__(
        self,
        db: Database,
        c4_url: str,
        service_key: str,
        *,
        attempts: int = 3,
        backoff_s: float = 0.2,
        resend_every_s: float = 30.0,
        transport: httpx.BaseTransport | None = None,
        timeout_s: float = 2.0,
    ):
        self.db = db
        self.url = c4_url.rstrip("/") + "/v1/events/c3"
        self.key = service_key
        self.attempts, self.backoff_s, self.resend_every_s = attempts, backoff_s, resend_every_s
        self._client = httpx.Client(transport=transport, timeout=timeout_s)
        self._q: queue.Queue[str | None] = queue.Queue()
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self.delivered = 0

    # ---- public -----------------------------------------------------------------------
    def start(self) -> None:
        for target in (self._worker, self._resender):
            t = threading.Thread(target=target, daemon=True, name=f"c3-events-{target.__name__}")
            t.start()
            self._threads.append(t)

    def stop(self) -> None:
        """Stop the threads first, then close the client. Unsent events stay `pending` in the
        outbox and go out after the next start, so nothing is lost."""
        self._stop.set()
        self._q.put(None)
        for t in self._threads:
            t.join(timeout=5)
        self._client.close()

    def emit(self, event: dict) -> None:
        """Store first (so a crash loses nothing), then hand to the sender. Never blocks."""
        self.db.enqueue_event(event["event_id"], event["request_id"], json.dumps(event), now_z())
        self._q.put(event["event_id"])

    def flush(self, timeout: float = 10.0) -> None:
        """Wait until the queue is empty. For tests."""
        end = time.time() + timeout
        while time.time() < end and not self._q.empty():
            time.sleep(0.01)
        time.sleep(0.05)

    def resend_pending(self) -> int:
        """One pass over the outbox. Returns how many events were delivered."""
        n = 0
        for row in self.db.pending_events():
            if self._send_with_retries(row["event_id"], json.loads(row["payload_json"]),
                                       row["attempts"]):
                n += 1
        return n

    # ---- internals --------------------------------------------------------------------
    def _worker(self) -> None:
        while not self._stop.is_set():
            event_id = self._q.get()
            if event_id is None:
                return
            try:
                row = next((r for r in self.db.pending_events(500)
                            if r["event_id"] == event_id), None)
                if row:
                    self._send_with_retries(event_id, json.loads(row["payload_json"]),
                                            row["attempts"])
            except Exception:                      # one bad event must not kill the sender
                log.exception("sending event %s failed", event_id)

    def _resender(self) -> None:
        while not self._stop.wait(self.resend_every_s):
            try:
                self.resend_pending()
            except Exception:
                log.exception("outbox resend failed")

    def _send_with_retries(self, event_id: str, payload: dict, attempts_so_far: int) -> bool:
        err = ""
        for i in range(self.attempts):
            if self._stop.is_set():
                return False                       # shutting down: leave it in the outbox
            try:
                r = self._client.post(self.url, json=payload, headers={"X-Service-Key": self.key})
                if r.status_code in DELIVERED:
                    self.db.mark_event(event_id, "sent", attempts_so_far + i + 1)
                    self.delivered += 1
                    return True
                err = f"HTTP {r.status_code}"
                if r.status_code in NO_RETRY:
                    self.db.mark_event(event_id, "failed", attempts_so_far + i + 1, err)
                    log.error("event %s rejected by C4: %s", event_id, err)
                    return False
            except (httpx.HTTPError, RuntimeError) as exc:
                err = type(exc).__name__
            if i < self.attempts - 1 and not self._stop.wait(self.backoff_s * (2 ** i)):
                continue
        # still pending: the outbox loop will try again later
        self.db.mark_event(event_id, "pending", attempts_so_far + self.attempts, err)
        log.warning("event %s kept in outbox (%s)", event_id, err)
        return False
