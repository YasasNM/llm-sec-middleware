"""Unit tests for the M10 decision log (ids, decision and numbers only)."""

import sqlite3

from c1.log.decision_log import Decision, read_decisions, write_decision


# A sample row like /v1/check writes
def sample(decision: str = "ALLOW") -> Decision:
    return Decision(
        request_id="req_20261023_000001",
        org_id="org_001",
        user_id="user_001",
        session_id="sess_dev",
        decision=decision,
        masking_percentage=47.1,
        entity_counts={"PATIENT_NAME": 1, "NIC": 1},
        reid_level="Low",
        reid_score=0,
        reid_applied=True,
        name_detector="heuristic",
        latency_ms=12,
        timestamp="2026-10-23T07:10:01Z",
    )


def test_write_and_read_back(tmp_path):
    path = str(tmp_path / "c1.db")
    write_decision(path, sample())
    write_decision(path, sample("ACCESS_BLOCKED"))
    rows = read_decisions(path)
    assert [row["decision"] for row in rows] == ["ALLOW", "ACCESS_BLOCKED"]
    assert rows[0]["entity_counts"] == '{"NIC": 1, "PATIENT_NAME": 1}'
    assert rows[0]["masking_percentage"] == 47.1 and rows[0]["reid_applied"] == 1


def test_log_has_no_text_columns(tmp_path):
    path = str(tmp_path / "c1.db")
    write_decision(path, sample())
    columns = set(read_decisions(path)[0])
    assert not columns & {"text", "masked_text", "prompt", "value", "values"}


def test_unavailable_row_may_have_no_numbers(tmp_path):
    path = str(tmp_path / "c1.db")
    row = sample("BLOCKLIST_UNAVAILABLE")
    row.masking_percentage, row.entity_counts, row.reid_level = None, {}, None
    write_decision(path, row)
    assert read_decisions(path)[0]["masking_percentage"] is None


def test_check_rows_have_no_unmasked_count_by_default(tmp_path):
    path = str(tmp_path / "c1.db")
    write_decision(path, sample())
    row = read_decisions(path)[0]
    assert row["unmasked_count"] == 0 and row["unmasked_types"] == "[]"


def test_demo_send_row_stores_count_and_types_only(tmp_path):
    path = str(tmp_path / "c1.db")
    row = sample("DEMO_SEND_AS_TYPED")
    row.unmasked_count, row.unmasked_types = 2, ["PHONE", "NIC"]
    write_decision(path, row)
    stored = read_decisions(path)[0]
    assert stored["decision"] == "DEMO_SEND_AS_TYPED" and stored["unmasked_count"] == 2
    assert stored["unmasked_types"] == '["NIC", "PHONE"]'


def test_older_log_file_gets_the_new_columns(tmp_path):
    path = str(tmp_path / "c1.db")
    old = sqlite3.connect(path)  # the table as it was before the override columns existed
    old.execute(
        "CREATE TABLE decision_log (request_id TEXT NOT NULL, org_id TEXT NOT NULL, "
        "user_id TEXT NOT NULL, session_id TEXT NOT NULL, decision TEXT NOT NULL, "
        "masking_percentage REAL, entity_counts TEXT NOT NULL, reid_level TEXT, "
        "reid_score INTEGER, reid_applied INTEGER, name_detector TEXT NOT NULL, "
        "latency_ms INTEGER NOT NULL, timestamp TEXT NOT NULL)"
    )
    old.commit()
    old.close()
    write_decision(path, sample("DEMO_SEND_AS_TYPED"))
    assert read_decisions(path)[0]["unmasked_count"] == 0
