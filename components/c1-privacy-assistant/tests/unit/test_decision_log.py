"""Unit tests for the M10 decision log (ids, decision and numbers only)."""

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
