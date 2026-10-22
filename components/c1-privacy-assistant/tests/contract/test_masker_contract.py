"""Contract test C1-C-01 (masking part): masker output fits the c1_check_response fields."""

import json
from pathlib import Path

import pytest

from c1.detection.regex import detect_regex
from c1.masking.masker import mask

jsonschema = pytest.importorskip("jsonschema")

REPO_ROOT = Path(__file__).resolve().parents[4]
SCHEMA_FILE = REPO_ROOT / "contracts" / "schemas" / "c1_check_response.schema.json"


# Build a full response around the masker output, using fixed values for the later steps
def response_from_mask(text: str) -> dict:
    result = mask(text, detect_regex(text))
    return {
        "request_id": "req_20261023_000001",
        "decision": "ALLOW",
        "masked_text": result.masked_text,
        "placeholder_manifest": result.placeholder_manifest,
        "masking_percentage": result.masking_percentage,
        "entity_counts": result.entity_counts,
        "reid": {"level": "Low", "score": 0, "applied": False},
        "user_message_code": None,
        "latency_ms": 1,
    }


@pytest.mark.parametrize(
    "text",
    [
        "NIC 951234567V, call 0771234567, mail a@b.lk, DOB 14/03/1985, MRN 123456",
        "No identifiers in this prompt",
    ],
)
def test_masker_output_matches_contract(text):
    schema = json.loads(SCHEMA_FILE.read_text())
    jsonschema.validate(response_from_mask(text), schema)
