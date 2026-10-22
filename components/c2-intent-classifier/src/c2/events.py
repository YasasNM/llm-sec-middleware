from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from c2.config import get_settings
from c2.schemas import C2Event, C2Input, C2Output


def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def new_event_id() -> str:
    return f"evt-c2-{uuid.uuid4()}"


def build_event(req: C2Input, out: C2Output) -> C2Event:
    return C2Event(
        event_id=new_event_id(),
        org_id=req.org_id,
        user_id=req.user_id,
        request_id=req.request_id,
        session_id=req.session_id,
        event_timestamp=now_utc(),
        c2_verdict=out.c2_verdict,
        c2_score=out.c2_score,
    )


class EventSender:
    """Background POST to C4 with a SQLite outbox. Never blocks the request path."""

    def __init__(self) -> None:
        settings = get_settings()
        self.endpoint = f"{settings.c4_url.rstrip('/')}/v1/events/c2"
        self.service_key = settings.service_key
        self.path = Path(settings.outbox_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS outbox (
                event_id TEXT PRIMARY KEY,
                endpoint TEXT,
                payload_json TEXT,
                status TEXT,
                attempts INTEGER,
                created_at TEXT
            )
            """
        )
        conn.commit()
        return conn

    def _init_db(self) -> None:
        self._connect().close()

    def send(self, event: C2Event) -> None:
        payload = event.model_dump()
        self._store(payload, status="pending")
        threading.Thread(target=self._deliver, args=(payload,), daemon=True).start()

    def pending_count(self) -> int:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT COUNT(*) FROM outbox WHERE status = 'pending'"
            ).fetchone()
            return int(row[0]) if row else 0
        finally:
            conn.close()

    def _store(self, payload: dict, status: str, attempts: int = 0) -> None:
        conn = self._connect()
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO outbox
                (event_id, endpoint, payload_json, status, attempts, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    payload["event_id"],
                    self.endpoint,
                    json.dumps(payload),
                    status,
                    attempts,
                    now_utc(),
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def _mark(self, event_id: str, status: str, attempts: int) -> None:
        conn = self._connect()
        try:
            conn.execute(
                "UPDATE outbox SET status = ?, attempts = ? WHERE event_id = ?",
                (status, attempts, event_id),
            )
            conn.commit()
        finally:
            conn.close()

    def _deliver(self, payload: dict) -> None:
        import httpx

        delays = (0.5, 2.0, 5.0)
        for attempt, delay in enumerate(delays, start=1):
            try:
                response = httpx.post(
                    self.endpoint,
                    json=payload,
                    headers={"X-Service-Key": self.service_key},
                    timeout=2.0,
                )
                if response.status_code in {200, 202}:
                    self._mark(payload["event_id"], "sent", attempt)
                    return
            except httpx.HTTPError:
                pass
            threading.Event().wait(delay)
        self._mark(payload["event_id"], "pending", len(delays))
