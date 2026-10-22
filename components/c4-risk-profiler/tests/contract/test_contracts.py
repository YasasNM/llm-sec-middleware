"""C4-C-01: samples from contracts/examples are accepted or rejected as the contract says."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker

CONTRACTS = Path(__file__).resolve().parents[4] / "contracts"
VALID = sorted(
    p for s in ("c1", "c2", "c3") for p in (CONTRACTS / "examples").glob(f"{s}_event.json")
)
INVALID = sorted(
    p
    for s in ("c1", "c2", "c3")
    for p in (CONTRACTS / "examples" / "invalid").glob(f"{s}_event.*.json")
)


@pytest.mark.parametrize("path", VALID, ids=lambda p: p.name)
def test_valid_examples_are_accepted(client, h, path):
    source = path.name[:2]
    r = client.post(f"/v1/events/{source}", json=json.loads(path.read_text()), headers=h[source])
    assert r.status_code == 202, r.text


@pytest.mark.parametrize("path", INVALID, ids=lambda p: p.name)
def test_invalid_examples_are_rejected_with_422(client, h, path):
    source = path.name[:2]
    r = client.post(f"/v1/events/{source}", json=json.loads(path.read_text()), headers=h[source])
    assert r.status_code == 422


def test_blocklist_response_matches_the_contract(client, h):
    schema = json.loads((CONTRACTS / "schemas" / "c4_blocklist_response.schema.json").read_text())
    body = client.get("/v1/blocklist", params={"org_id": "org_001"}, headers=h["c1"]).json()
    assert (
        list(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(body)) == []
    )
