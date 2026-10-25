"""Integration tests for POST /v1/check with the gate, the event (M9) and the decision log (M10).

C4 is the real stub (stubs/c4_stub.py) reached through tests/fake_c4.py, so every event that
reaches it has been validated against contracts/schemas/c1_event.
"""

import json
from pathlib import Path

import pytest
from fake_c4 import c1_with_fake_c4
from helpers import GATEWAY_HEADERS, OTHER_KEY

from c1 import common
from c1.log.decision_log import read_decisions

jsonschema = pytest.importorskip("jsonschema")

REPO_ROOT = Path(__file__).resolve().parents[4]
EXAMPLE_REQUEST = json.loads((REPO_ROOT / "contracts/examples/c1_check_request.json").read_text())
RESPONSE_SCHEMA = json.loads(
    (REPO_ROOT / "contracts/schemas/c1_check_response.schema.json").read_text()
)
BLOCKED = {"org_001": ["user_017"]}


@pytest.fixture()
def c1(monkeypatch, tmp_path):
    return c1_with_fake_c4(monkeypatch, tmp_path, BLOCKED)


# Send one /v1/check as the gateway; `changes` replaces fields of the contract example
def check(client, **changes):
    return client.post("/v1/check", json=dict(EXAMPLE_REQUEST, **changes), headers=GATEWAY_HEADERS)


# The answer body, after proving it matches the c1_check_response contract
def valid_body(reply) -> dict:
    body = reply.json()
    jsonschema.validate(body, RESPONSE_SCHEMA)
    return body


# The rows of the decision log for this test
def decisions(client) -> list[dict]:
    return read_decisions(str(client.app.state.settings.outbox_path))


def test_allowed_user_gets_the_contract_example_answer(c1):
    client, c4, _ = c1
    body = valid_body(check(client))
    assert body["decision"] == "ALLOW" and body["user_message_code"] is None
    assert body["masked_text"] == "Patient [PATIENT_NAME_1] NIC [NIC_1], review P0012"
    assert body["placeholder_manifest"] == ["[PATIENT_NAME_1]", "[NIC_1]"]
    assert body["masking_percentage"] == 47.1
    assert body["entity_counts"] == {"PATIENT_NAME": 1, "NIC": 1}
    assert body["reid"] == {"level": "Low", "score": 0, "applied": True}
    assert len(c4.events) == 1 and c4.events[0]["masking_percentage"] == 47.1


def test_reid_suggestions_applied_only_when_asked(c1):
    client, _, _ = c1
    text = "Patient Nimal Perera, 94-year-old HIV patient from Hikkaduwa"
    on = valid_body(check(client, text=text))
    off = valid_body(check(client, text=text, apply_reid_suggestions=False))
    assert "elderly" in on["masked_text"] and "94-year-old" not in on["masked_text"]
    assert "94-year-old" in off["masked_text"] and "[PATIENT_NAME_1]" in off["masked_text"]
    assert on["reid"]["applied"] is True and off["reid"]["applied"] is False
    assert on["masking_percentage"] == off["masking_percentage"]  # re-id never counts (IR7)


def test_apply_reid_suggestions_defaults_to_true(c1):
    client, _, _ = c1
    request = dict(EXAMPLE_REQUEST)
    del request["apply_reid_suggestions"]
    body = valid_body(client.post("/v1/check", json=request, headers=GATEWAY_HEADERS))
    assert body["reid"]["applied"] is True


def test_blocked_user_gets_access_blocked_and_one_real_event(c1):
    client, c4, _ = c1
    body = valid_body(check(client, user_id="user_017"))
    assert body["decision"] == "ACCESS_BLOCKED" and body["user_message_code"] == "ACCESS_BLOCKED"
    assert body["masked_text"] is None and body["placeholder_manifest"] == []
    assert body["masking_percentage"] == 47.1  # detection still ran (IR14)
    assert [e["user_id"] for e in c4.events] == ["user_017"]
    assert c4.events[0]["masking_percentage"] == 47.1


def test_c4_down_with_fresh_cache_still_allows(c1):
    client, c4, clock = c1
    check(client)
    c4.blocklist_down, clock.now = True, clock.now + 120  # cache is 2 minutes old (C1-I-02)
    assert valid_body(check(client))["decision"] == "ALLOW"
    assert valid_body(check(client, user_id="user_017"))["decision"] == "ACCESS_BLOCKED"


def test_c4_down_with_stale_cache_gives_503_and_no_event(c1):
    client, c4, clock = c1
    check(client)
    c4.blocklist_down, clock.now = True, clock.now + 301  # older than 5 minutes (C1-I-03)
    reply = check(client, request_id="req_20261023_000002")
    assert reply.status_code == 503
    assert reply.json() == {"code": "BLOCKLIST_UNAVAILABLE", "request_id": "req_20261023_000002"}
    assert [e["request_id"] for e in c4.events] == [EXAMPLE_REQUEST["request_id"]]
    assert decisions(client)[-1]["decision"] == "BLOCKLIST_UNAVAILABLE"


def test_c4_never_reached_gives_503_and_no_event(c1):
    client, c4, _ = c1
    c4.blocklist_down = True
    assert check(client).status_code == 503 and c4.events == []


def test_exactly_one_event_per_request(c1):
    client, c4, _ = c1
    ids = [f"req_20261023_00000{n}" for n in range(1, 4)]
    for request_id in ids:
        check(client, request_id=request_id)
    check(client, request_id="req_20261023_000009", user_id="user_017")
    assert [e["request_id"] for e in c4.events] == ids + ["req_20261023_000009"]
    assert len({e["event_id"] for e in c4.events}) == 4
    for event in c4.events:
        assert event["session_id"] == EXAMPLE_REQUEST["session_id"]
        assert event["event_id"].startswith("evt-c1-") and event["event_timestamp"].endswith("Z")


def test_zero_percent_request_still_sends_an_event(c1):
    client, c4, _ = c1
    body = valid_body(check(client, text="What is the usual adult dose of paracetamol?"))
    assert body["masking_percentage"] == 0 and body["masked_text"].startswith("What is")
    assert [e["masking_percentage"] for e in c4.events] == [0]


def test_c4_down_at_send_time_keeps_event_in_outbox(c1):
    client, c4, _ = c1
    c4.events_down = True
    assert valid_body(check(client))["decision"] == "ALLOW"  # the answer is not affected
    outbox = str(client.app.state.settings.outbox_path)
    assert [e["request_id"] for e in common.load_outbox(outbox)] == [EXAMPLE_REQUEST["request_id"]]
    c4.events_down = False
    assert common.resend_outbox("http://c4.test", "test-c1-key", outbox) == 1  # C1-I-04
    assert common.load_outbox(outbox) == [] and len(c4.events) == 1


def test_decision_log_row_per_request(c1):
    client, _, _ = c1
    check(client)
    check(client, user_id="user_017", apply_reid_suggestions=False)
    rows = decisions(client)
    assert [r["decision"] for r in rows] == ["ALLOW", "ACCESS_BLOCKED"]
    assert json.loads(rows[0]["entity_counts"]) == {"NIC": 1, "PATIENT_NAME": 1}
    assert rows[0]["reid_level"] == "Low" and rows[0]["latency_ms"] >= 0
    assert rows[0]["timestamp"].endswith("Z") and rows[0]["name_detector"] == "heuristic"


def test_health_shows_block_list_age(c1):
    client, _, clock = c1
    assert client.get("/health").json()["blocklist_cache_age_s"] is None
    check(client)
    clock.now += 7
    assert client.get("/health").json()["blocklist_cache_age_s"] == 7


def test_check_needs_the_gateway_key(c1):
    client, c4, _ = c1
    assert client.post("/v1/check", json=EXAMPLE_REQUEST).status_code == 401
    other = {"X-Service-Key": OTHER_KEY}
    assert client.post("/v1/check", json=EXAMPLE_REQUEST, headers=other).status_code == 403
    assert c4.events == []


@pytest.mark.parametrize(
    "changes",
    [
        {"severity": 2},  # unknown field
        {"request_id": "req with spaces"},
        {"org_id": ""},
        {"text": ""},
        {"text": "x" * 8001},
        {"apply_reid_suggestions": "yes"},
        {"user_id": 17},
    ],
)
def test_bad_body_gives_422_without_echo_and_no_event(c1, changes):
    client, c4, _ = c1
    reply = check(client, **changes)
    assert reply.status_code == 422 and "Nimal" not in reply.text
    assert c4.events == []
