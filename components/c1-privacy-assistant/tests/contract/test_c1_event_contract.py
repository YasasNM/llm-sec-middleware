"""Contract test C1-C-01 (events part): the C1 event must match contracts/schemas/c1_event."""

import json
from pathlib import Path

import pytest

from c1 import common

jsonschema = pytest.importorskip("jsonschema")

REPO_ROOT = Path(__file__).resolve().parents[4]
SCHEMA_FILE = REPO_ROOT / "contracts" / "schemas" / "c1_event.schema.json"


# Load the agreed C1 event schema from the read-only contracts folder
def load_schema() -> dict:
    return json.loads(SCHEMA_FILE.read_text())


@pytest.mark.parametrize("percentage", [0, 12.5, 100])
def test_built_event_matches_contract(percentage):
    event = common.build_c1_event(
        "org_001", "user_001", "req_20261023_000001", "sess_01JAB3K9Q2", percentage
    )
    jsonschema.validate(event, load_schema())


def test_event_has_exactly_the_agreed_fields():
    event = common.build_c1_event("org_001", "user_001", "req_1", "sess_1", 0)
    assert set(event) == set(load_schema()["required"])
