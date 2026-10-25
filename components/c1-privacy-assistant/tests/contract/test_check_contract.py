"""Contract tests C1-C-01 for Step 6: /v1/check request and response, the C1 event, the block list.

C1 checks bodies at runtime with Pydantic models (the Docker image does not carry contracts/).
These tests prove that those models accept and reject the same payloads as the real schema
files in contracts/schemas, and that the invalid examples are rejected.
"""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from c1 import common
from c1.gate.blocklist import BlocklistFetchError, parse_blocklist
from c1.schemas import CheckRequest, CheckResponse

jsonschema = pytest.importorskip("jsonschema")

CONTRACTS = Path(__file__).resolve().parents[4] / "contracts"
NAMES = ["c1_check_request", "c1_check_response", "c1_event", "c4_blocklist_response"]


# Load one example payload from contracts/examples
def example(name: str) -> dict:
    return json.loads((CONTRACTS / "examples" / f"{name}.json").read_text())


# True when the real schema file accepts the payload (formats checked too)
def schema_accepts(name: str, payload) -> bool:
    schema = json.loads((CONTRACTS / "schemas" / f"{name}.schema.json").read_text())
    checker = jsonschema.FormatChecker()
    return not list(
        jsonschema.Draft202012Validator(schema, format_checker=checker).iter_errors(payload)
    )


# True when C1's own runtime model accepts the payload
def model_accepts(model, payload) -> bool:
    try:
        model.model_validate(payload)
    except ValidationError:
        return False
    return True


# True when C1's block-list parser accepts C4's answer for org_001
def parser_accepts(payload) -> bool:
    try:
        parse_blocklist(payload, payload.get("org_id", "org_001"))
    except BlocklistFetchError:
        return False
    return True


@pytest.mark.parametrize("name", NAMES)
def test_every_example_matches_its_schema(name):
    assert schema_accepts(name, example(name))


@pytest.mark.parametrize(
    "path", sorted((CONTRACTS / "examples" / "invalid").glob("c1_event.*.json"))
)
def test_invalid_c1_event_examples_are_rejected(path):
    assert not schema_accepts("c1_event", json.loads(path.read_text()))


def test_built_events_match_the_schema():
    for percentage in (0, 12.5, 47.1, 100):
        event = common.build_c1_event("org_001", "user_001", "req_1", "sess_1", percentage)
        assert schema_accepts("c1_event", event)


REQUEST_CASES = [
    {},
    {"apply_reid_suggestions": False},
    {"severity": 2},
    {"request_id": "req 1"},
    {"request_id": "r" * 65},
    {"org_id": ""},
    {"text": ""},
    {"text": "x" * 8000},
    {"text": "x" * 8001},
    {"apply_reid_suggestions": "yes"},
    {"user_id": 17},
    {"session_id": None},
]


@pytest.mark.parametrize("changes", REQUEST_CASES)
def test_request_model_agrees_with_the_schema(changes):
    payload = dict(example("c1_check_request"), **changes)
    assert model_accepts(CheckRequest, payload) == schema_accepts("c1_check_request", payload)


def test_request_without_optional_flag_is_valid_both_ways():
    payload = example("c1_check_request")
    del payload["apply_reid_suggestions"]
    assert model_accepts(CheckRequest, payload) and schema_accepts("c1_check_request", payload)


RESPONSE_CASES = [
    {},
    {"decision": "ACCESS_BLOCKED", "masked_text": None, "user_message_code": "ACCESS_BLOCKED"},
    {"decision": "BLOCK"},
    {"placeholder_manifest": ["Nimal Perera"]},
    {"masking_percentage": 100.5},
    {"entity_counts": {"NIC": -1}},
    {"reid": {"level": "Critical", "score": 0, "applied": True}},
    {"reid": {"level": "Low", "score": 101, "applied": True}},
    {"user_message_code": "REQUEST_BLOCKED"},
    {"latency_ms": -1},
]


@pytest.mark.parametrize("changes", RESPONSE_CASES)
def test_response_model_agrees_with_the_schema(changes):
    payload = dict(example("c1_check_response"), **changes)
    assert model_accepts(CheckResponse, payload) == schema_accepts("c1_check_response", payload)


BLOCKLIST_CASES = [
    {},
    {"user_ids": []},
    {"user_ids": ["user 017"]},
    {"user_ids": ["user_017", "user_017"]},
    {"generated_at": "2026-08-23 07:10:00"},
    {"generated_at": "2026-08-23T07:10:00.123Z"},
    {"org_id": ""},
]


@pytest.mark.parametrize("changes", BLOCKLIST_CASES)
def test_blocklist_parser_agrees_with_the_schema(changes):
    payload = dict(example("c4_blocklist_response"), **changes)
    assert parser_accepts(payload) == schema_accepts("c4_blocklist_response", payload)
