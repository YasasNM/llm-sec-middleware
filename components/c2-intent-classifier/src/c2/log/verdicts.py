from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from c2.config import Settings
from c2.events import now_utc
from c2.schemas import C2Input, C2Output


class VerdictLog:
    def __init__(self, settings: Settings) -> None:
        self.path = Path(settings.outbox_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = self._connect()
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS verdicts (
                request_id TEXT PRIMARY KEY,
                org_id TEXT,
                user_id TEXT,
                session_id TEXT,
                ts TEXT,
                c2_verdict TEXT,
                c2_score REAL,
                similarity_score REAL,
                probability_score REAL,
                method TEXT,
                degraded INTEGER,
                nearest_ids TEXT,
                latency_ms INTEGER,
                model_version TEXT
            )
            """
        )
        conn.commit()
        conn.close()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)

    def write(
        self,
        req: C2Input,
        out: C2Output,
        similarity_score: float,
        probability_score: float,
    ) -> None:
        conn = self._connect()
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO verdicts (
                    request_id, org_id, user_id, session_id, ts, c2_verdict, c2_score,
                    similarity_score, probability_score, method, degraded, nearest_ids,
                    latency_ms, model_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    req.request_id,
                    req.org_id,
                    req.user_id,
                    req.session_id,
                    now_utc(),
                    out.c2_verdict,
                    out.c2_score,
                    similarity_score,
                    probability_score,
                    out.method,
                    int(out.degraded),
                    json.dumps(out.nearest_attack_ids),
                    out.latency_ms,
                    out.model_version,
                ),
            )
            conn.commit()
        finally:
            conn.close()
