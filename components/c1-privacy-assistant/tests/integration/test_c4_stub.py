"""Tests for stubs/c4_stub.py: the block list from blocklist.json and contract-checked events."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from c1 import common
from stubs import c4_stub

pytest.importorskip("jsonschema")

INVALID = Path(__file__).resolve().parents[4] / "contracts" / "examples" / "invalid"
KEY = {"X-Service-Key": "any-key"}


@pytest.fixture()
def stub(monkeypatch, tmp_path):
    lists = tmp_path / "blocklist.json"
    lists.write_text(json.dumps({"org_001": ["user_017"]}))
    monkeypatch.setattr(c4_stub, "BLOCKLIST_FILE", lists)
    monkeypatch.delenv("MG_ACCEPTED_KEYS", raising=False)
    c4_stub.seen_event_ids.clear()
    return TestClient(c4_stub.app), lists


# A valid C1 event, built the same way C1 builds it
def event() -> dict:
    return common.build_c1_event("org_001", "user_001", "req_1", "sess_1", 12.5)


def test_blocklist_per_org_and_follows_file_edits(stub):
    client, lists = stub
    body = client.get("/v1/blocklist", params={"org_id": "org_001"}, headers=KEY).json()
    assert body["org_id"] == "org_001" and body["user_ids"] == ["user_017"]
    assert body["generated_at"].endswith("Z")
    lists.write_text(json.dumps({"org_001": ["user_017", "user_020"]}))  # edited while running
    body = client.get("/v1/blocklist", params={"org_id": "org_001"}, headers=KEY).json()
    assert body["user_ids"] == ["user_017", "user_020"]


def test_unknown_org_blocks_nobody(stub):
    client, _ = stub
    assert (
        client.get("/v1/blocklist", params={"org_id": "org_9"}, headers=KEY).json()["user_ids"]
        == []
    )


def test_key_is_required_and_checked_when_configured(stub, monkeypatch):
    client, _ = stub
    assert client.get("/v1/blocklist", params={"org_id": "org_001"}).status_code == 401
    monkeypatch.setenv("MG_ACCEPTED_KEYS", "c1:right-key")
    wrong = client.post("/v1/events/c1", json=event(), headers={"X-Service-Key": "wrong"})
    right = client.post("/v1/events/c1", json=event(), headers={"X-Service-Key": "right-key"})
    assert (wrong.status_code, right.status_code) == (401, 202)


def test_valid_event_accepted_then_duplicate(stub):
    client, _ = stub
    sample = event()
    first = client.post("/v1/events/c1", json=sample, headers=KEY)
    again = client.post("/v1/events/c1", json=sample, headers=KEY)
    assert first.status_code == 202 and first.json() == {"accepted": True, "request_counted": True}
    assert again.status_code == 200 and again.json() == {"duplicate": True}


@pytest.mark.parametrize("path", sorted(INVALID.glob("c1_event.*.json")))
def test_invalid_contract_examples_are_rejected(stub, path):
    client, _ = stub
    reply = client.post("/v1/events/c1", json=json.loads(path.read_text()), headers=KEY)
    assert reply.status_code == 422


def test_body_that_is_not_json_is_rejected(stub):
    client, _ = stub
    reply = client.post("/v1/events/c1", content=b"not json", headers=KEY)
    assert reply.status_code == 422


def test_stub_prints_only_event_id_and_percentage(stub, capsys):
    client, _ = stub
    sample = event()
    client.post("/v1/events/c1", json=sample, headers=KEY)
    printed = capsys.readouterr().out.strip()
    assert printed == f"[c4_stub] event_id={sample['event_id']} masking_percentage=12.5"
