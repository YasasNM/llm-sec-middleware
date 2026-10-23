"""SQLite access: documents and index runs (blueprint 11.2). Scan tables arrive with /v1/scan."""
from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    doc_id         TEXT PRIMARY KEY,
    org_id         TEXT NOT NULL,
    name           TEXT NOT NULL,
    filename       TEXT NOT NULL,
    doc_code       TEXT NOT NULL,
    category       TEXT NOT NULL,
    severity       TEXT,
    sha256         TEXT NOT NULL,
    size_bytes     INTEGER NOT NULL,
    version        INTEGER NOT NULL DEFAULT 0,
    status         TEXT NOT NULL,
    status_message TEXT,
    clause_count   INTEGER NOT NULL DEFAULT 0,
    source         TEXT NOT NULL DEFAULT 'upload',
    uploaded_at    TEXT NOT NULL,
    uploaded_by    TEXT,
    UNIQUE (org_id, doc_code)
);
CREATE INDEX IF NOT EXISTS idx_documents_org ON documents(org_id);

CREATE TABLE IF NOT EXISTS index_runs (
    run_id             TEXT PRIMARY KEY,
    org_id             TEXT NOT NULL,
    doc_id             TEXT NOT NULL,
    path               TEXT NOT NULL,
    t0                 REAL NOT NULL,
    queue_or_detect_ms REAL NOT NULL,
    parse_ms           REAL NOT NULL,
    embed_ms           REAL NOT NULL,
    upsert_ms          REAL NOT NULL,
    added              INTEGER NOT NULL,
    changed            INTEGER NOT NULL,
    removed            INTEGER NOT NULL,
    embedded           INTEGER NOT NULL,
    index_version      TEXT NOT NULL,
    ok                 INTEGER NOT NULL DEFAULT 1,
    message            TEXT
);
CREATE INDEX IF NOT EXISTS idx_runs_org ON index_runs(org_id, t0);

CREATE TABLE IF NOT EXISTS scan_records (
    request_id            TEXT PRIMARY KEY,
    org_id                TEXT NOT NULL,
    user_id               TEXT NOT NULL,
    session_id            TEXT NOT NULL,
    response_hash         TEXT NOT NULL,
    verdict               TEXT NOT NULL,
    decision_source       TEXT NOT NULL,
    assurance             TEXT,
    confidence            REAL,
    tau                   REAL,
    decoded               INTEGER NOT NULL DEFAULT 0,
    output_tokens         INTEGER,
    leaked_pii_count      INTEGER NOT NULL DEFAULT 0,
    reason_replaced       INTEGER NOT NULL DEFAULT 0,
    error_code            TEXT,
    rationale             TEXT,
    policy_index_version  TEXT,
    model_version         TEXT,
    retriever_version     TEXT,
    latency_total_ms      INTEGER NOT NULL,
    thresholds_json       TEXT,
    created_at            TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_scan_org ON scan_records(org_id, created_at);

CREATE TABLE IF NOT EXISTS cited_clauses (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id       TEXT NOT NULL,
    clause_id        TEXT NOT NULL,
    doc_id           TEXT,
    title            TEXT,
    quote            TEXT,
    citation_status  TEXT NOT NULL,
    retrieval_rank   INTEGER
);
CREATE INDEX IF NOT EXISTS idx_cited_req ON cited_clauses(request_id);

CREATE TABLE IF NOT EXISTS security_events (
    event_id      TEXT PRIMARY KEY,
    request_id    TEXT NOT NULL,
    payload_json  TEXT NOT NULL,
    status        TEXT NOT NULL,
    attempts      INTEGER NOT NULL DEFAULT 0,
    last_error    TEXT,
    created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_status ON security_events(status, created_at);

CREATE TABLE IF NOT EXISTS bench_runs (
    bench_id   TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    config_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS bench_trials (
    trial_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    bench_id     TEXT NOT NULL REFERENCES bench_runs(bench_id),
    path         TEXT NOT NULL,
    change_type  TEXT NOT NULL,
    t_index_ms   REAL,
    t_enforce_ms REAL,
    stages_json  TEXT
);
"""

DOC_COLUMNS = (
    "doc_id", "org_id", "name", "filename", "doc_code", "category", "severity", "sha256",
    "size_bytes", "version", "status", "status_message", "clause_count", "source",
    "uploaded_at", "uploaded_by",
)


class Database:
    """One connection guarded by a lock. Fine for one worker and small tables."""

    def __init__(self, path: str | Path):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(SCHEMA)

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            try:
                yield self._conn
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ---- documents --------------------------------------------------------------------
    def insert_document(self, doc: dict) -> None:
        cols = ",".join(DOC_COLUMNS)
        marks = ",".join("?" for _ in DOC_COLUMNS)
        with self.tx() as c:
            c.execute(
                f"INSERT INTO documents ({cols}) VALUES ({marks})",
                [doc.get(k) for k in DOC_COLUMNS],
            )

    def update_document(self, doc_id: str, **fields) -> None:
        if not fields:
            return
        sets = ",".join(f"{k}=?" for k in fields)
        with self.tx() as c:
            c.execute(f"UPDATE documents SET {sets} WHERE doc_id=?", [*fields.values(), doc_id])

    def get_document(self, org_id: str, doc_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM documents WHERE org_id=? AND doc_id=?", (org_id, doc_id)
            ).fetchone()
        return dict(row) if row else None

    def list_documents(self, org_id: str, include_removed: bool = False) -> list[dict]:
        q = "SELECT * FROM documents WHERE org_id=?"
        if not include_removed:
            q += " AND status!='removed'"
        q += " ORDER BY uploaded_at, doc_id"
        with self._lock:
            return [dict(r) for r in self._conn.execute(q, (org_id,)).fetchall()]

    def doc_codes(self, org_id: str) -> set[str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT doc_code FROM documents WHERE org_id=?", (org_id,)
            ).fetchall()
        return {r["doc_code"] for r in rows}

    def find_by_source(self, org_id: str, filename: str, source: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM documents WHERE org_id=? AND filename=? AND source=? "
                "AND status!='removed'",
                (org_id, filename, source),
            ).fetchone()
        return dict(row) if row else None

    def orgs(self) -> list[str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT DISTINCT org_id FROM documents WHERE status!='removed'"
            ).fetchall()
        return sorted(r["org_id"] for r in rows)

    def next_doc_id(self) -> str:
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) AS n FROM documents").fetchone()
        return f"doc-{row['n'] + 1:04d}"

    # ---- index runs -------------------------------------------------------------------
    def insert_run(self, run: dict) -> None:
        cols = list(run)
        with self.tx() as c:
            c.execute(
                f"INSERT INTO index_runs ({','.join(cols)}) VALUES ({','.join('?' for _ in cols)})",
                [run[k] for k in cols],
            )

    def list_runs(self, org_id: str, limit: int = 50) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM index_runs WHERE org_id=? ORDER BY t0 DESC LIMIT ?", (org_id, limit)
            ).fetchall()
        return [dict(r) for r in rows]

    # ---- scans and events -------------------------------------------------------------
    def save_scan(self, record: dict, cited: list[dict]) -> None:
        cols = list(record)
        with self.tx() as c:
            c.execute("DELETE FROM cited_clauses WHERE request_id=?", (record["request_id"],))
            c.execute(
                f"INSERT OR REPLACE INTO scan_records ({','.join(cols)}) "
                f"VALUES ({','.join('?' for _ in cols)})",
                [record[k] for k in cols],
            )
            for cl in cited:
                c.execute(
                    "INSERT INTO cited_clauses (request_id, clause_id, doc_id, title, quote, "
                    "citation_status, retrieval_rank) VALUES (?,?,?,?,?,?,?)",
                    (record["request_id"], cl["clause_id"], cl.get("doc_id"), cl.get("title"),
                     cl.get("quote"), cl["citation_status"], cl.get("retrieval_rank")),
                )

    def get_scan(self, org_id: str, request_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM scan_records WHERE org_id=? AND request_id=?", (org_id, request_id)
            ).fetchone()
            if row is None:
                return None
            cited = self._conn.execute(
                "SELECT * FROM cited_clauses WHERE request_id=? ORDER BY retrieval_rank",
                (request_id,),
            ).fetchall()
        return {**dict(row), "cited_clauses": [dict(c) for c in cited]}

    def enqueue_event(self, event_id: str, request_id: str, payload_json: str, created_at: str):
        with self.tx() as c:
            c.execute(
                "INSERT OR IGNORE INTO security_events (event_id, request_id, payload_json, "
                "status, attempts, created_at) VALUES (?,?,?,'pending',0,?)",
                (event_id, request_id, payload_json, created_at),
            )

    def pending_events(self, limit: int = 50) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM security_events WHERE status='pending' ORDER BY created_at LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def mark_event(self, event_id: str, status: str, attempts: int, error: str | None = None):
        with self.tx() as c:
            c.execute(
                "UPDATE security_events SET status=?, attempts=?, last_error=? WHERE event_id=?",
                (status, attempts, error, event_id),
            )

    def event_counts(self) -> dict[str, int]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT status, COUNT(*) AS n FROM security_events GROUP BY status"
            ).fetchall()
        return {r["status"]: r["n"] for r in rows}

    # ---- bench ------------------------------------------------------------------------
    def insert_bench(self, bench_id: str, created_at: str, config: dict) -> None:
        with self.tx() as c:
            c.execute(
                "INSERT INTO bench_runs (bench_id, created_at, config_json) VALUES (?,?,?)",
                (bench_id, created_at, json.dumps(config)),
            )

    def insert_trial(self, bench_id: str, trial: dict) -> None:
        with self.tx() as c:
            c.execute(
                "INSERT INTO bench_trials (bench_id, path, change_type, t_index_ms, t_enforce_ms, "
                "stages_json) VALUES (?,?,?,?,?,?)",
                (
                    bench_id, trial["path"], trial["change_type"], trial.get("t_index_ms"),
                    trial.get("t_enforce_ms"), json.dumps(trial.get("stages", {})),
                ),
            )
