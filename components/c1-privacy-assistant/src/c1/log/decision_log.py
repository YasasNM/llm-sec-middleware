"""M10 decision log: one PHI-free row per /v1/check, kept by C1 only and never sent (BLUEPRINT 9.5).

It stores ids, the decision, numbers and entity TYPES with counts. It never stores prompt text,
masked text or any entity value, so the file is safe to keep for C1's own evaluation.
The table lives in the same SQLite file as the event outbox (c1.db, gitignored).
"""

import json
import sqlite3
from dataclasses import asdict, dataclass

CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS decision_log (
    request_id TEXT NOT NULL,
    org_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    decision TEXT NOT NULL,
    masking_percentage REAL,
    entity_counts TEXT NOT NULL,
    reid_level TEXT,
    reid_score INTEGER,
    reid_applied INTEGER,
    name_detector TEXT NOT NULL,
    latency_ms INTEGER NOT NULL,
    timestamp TEXT NOT NULL
)
"""


@dataclass
class Decision:
    """One row of the log. Every field is an id, a label or a number, never patient text."""

    request_id: str
    org_id: str
    user_id: str
    session_id: str
    decision: str  # ALLOW, ACCESS_BLOCKED or BLOCKLIST_UNAVAILABLE
    masking_percentage: float | None
    entity_counts: dict[str, int]
    reid_level: str | None
    reid_score: int | None
    reid_applied: bool | None
    name_detector: str
    latency_ms: int
    timestamp: str


# Open the SQLite file and create the table the first time
def open_log(path: str) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.execute(CREATE_TABLE)
    return connection


# Add one decision to the log; entity_counts is stored as JSON text
def write_decision(path: str, decision: Decision) -> None:
    row = asdict(decision)
    row["entity_counts"] = json.dumps(row["entity_counts"], sort_keys=True)
    columns = ", ".join(row)
    marks = ", ".join("?" for _ in row)
    connection = open_log(path)
    try:
        connection.execute(
            f"INSERT INTO decision_log ({columns}) VALUES ({marks})", list(row.values())
        )
        connection.commit()
    finally:
        connection.close()


# Read every logged decision as plain dicts, oldest first (used by tests and evaluation)
def read_decisions(path: str) -> list[dict]:
    connection = open_log(path)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute("SELECT * FROM decision_log ORDER BY rowid").fetchall()
    finally:
        connection.close()
    return [dict(row) for row in rows]
