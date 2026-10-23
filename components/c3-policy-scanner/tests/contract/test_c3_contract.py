"""Every PolicyVerdict and C3 event that this service produces must validate against the shared
schemas in contracts/. If this file fails, C3 and another component disagree."""
import json
from pathlib import Path

import httpx
import pytest
from c3_helpers import (
    KEY,
    SCAN_POLICY,
    CountingEmbedder,
    StubGenerator,
    markdown_policy,
    scan_request,
)
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator, FormatChecker

from c3.api.scan import ScanRequest
from c3.main import create_app

CONTRACTS = Path(__file__).resolve().parents[4] / "contracts"
H = {"X-Service-Key": KEY}
ORG = "northfield"


def validator(name: str) -> Draft202012Validator:
    schema = json.loads((CONTRACTS / "schemas" / f"{name}.schema.json").read_text())
    return Draft202012Validator(schema, format_checker=FormatChecker())


def errors(name: str, payload) -> list[str]:
    return [e.message for e in validator(name).iter_errors(payload)]


@pytest.fixture
def env(make_container):
    events = []

    def handler(request):
        events.append(json.loads(request.content))
        return httpx.Response(202, json={"accepted": True, "request_counted": True})

    c = make_container(embedder=CountingEmbedder(), generator=StubGenerator(),
                       c4_transport=httpx.MockTransport(handler))
    c.policies.upload(ORG, "clinical-guide.md", markdown_policy(SCAN_POLICY).encode())
    c.policies.wait_idle()
    with TestClient(create_app(c)) as tc:
        tc.c, tc.events = c, events
        yield tc


def scan(env, text, **kw):
    return env.post("/v1/scan", headers=H, json=scan_request(text, **kw))


CASES = {
    "clean": "Regular walking is a safe way for most adults to improve fitness.",
    "violation": "For severe pain I recommend morphine 15 mg every four hours for this patient.",
    "leak_only": "The patient's NIC is 901234567V.",
    "violation_and_leak": "I recommend morphine doses for this patient, NIC 901234567V.",
    "with_manifest": "[PATIENT_NAME_1] (P0012) should walk daily to improve fitness.",
}


@pytest.mark.parametrize("case", CASES)
def test_verdicts_and_events_validate(env, case):
    r = scan(env, CASES[case], request_id=f"req_{case}", manifest=["[PATIENT_NAME_1]"])
    assert r.status_code == 200
    assert errors("c3_scan_response", r.json()) == []
    env.c.emitter.flush()
    assert len(env.events) == 1
    assert errors("c3_event", env.events[0]) == []


def test_fail_closed_result_validates_and_sends_no_event(make_container):
    c = make_container(generator=StubGenerator(raise_on_stage1=True))
    c.policies.upload(ORG, "g.md", markdown_policy(SCAN_POLICY).encode())
    c.policies.wait_idle()
    with TestClient(create_app(c)) as tc:
        body = tc.post("/v1/scan", headers=H, json=scan_request("hello world text")).json()
    assert errors("c3_scan_response", body) == []
    assert body["decision_source"] == "error_fail_closed"


def test_dependency_down_body_validates(make_container):
    c = make_container(generator=StubGenerator())
    c.registry.get(ORG)
    with TestClient(create_app(c)) as tc:
        r = tc.post("/v1/scan", headers=H, json=scan_request("hello world text"))
    assert r.status_code == 503 and errors("c3_scan_response", r.json()) == []


def test_shared_request_example_is_accepted_and_invalid_ones_are_not():
    example = json.loads((CONTRACTS / "examples" / "c3_scan_request.json").read_text())
    assert errors("c3_scan_request", example) == []
    ScanRequest(**example)                                      # our pydantic model agrees
    for bad in (
        {**example, "extra_field": 1},
        {**example, "request_id": "has space"},
        {**example, "placeholder_manifest": ["PATIENT"]},
        {k: v for k, v in example.items() if k != "org_id"},
    ):
        assert errors("c3_scan_request", bad) != []
        with pytest.raises(ValueError):
            ScanRequest(**bad)


def test_shared_response_and_event_examples_still_validate():
    for name in ("c3_scan_response", "c3_event"):
        ex = json.loads((CONTRACTS / "examples" / f"{name}.json").read_text())
        assert errors(name, ex) == []
    for f in (CONTRACTS / "examples" / "invalid").glob("c3_event.*.json"):
        assert errors("c3_event", json.loads(f.read_text())) != []


def test_event_builder_matches_schema_rules():
    from c3.events import build_event

    req = scan_request("x")
    clean = {"decision_source": "generator", "c3_verdict": "CLEAN", "clause_id": None,
             "leaked_pii_detected": False, "leaked_pii_count": 0}
    assert errors("c3_event", build_event(req, clean)) == []
    with pytest.raises(ValueError):
        build_event(req, {**clean, "decision_source": "error_fail_closed"})
    # the schema itself rejects the inconsistent combinations the contract forbids
    bad = build_event(req, {**clean, "c3_verdict": "VIOLATION"})        # violation, no clause, no leak
    assert errors("c3_event", bad) != []


def test_event_timestamp_format_is_utc_z(env):
    scan(env, CASES["clean"])
    env.c.emitter.flush()
    assert env.events[0]["event_timestamp"].endswith("Z")


# ---- start-up checks (T-F02, T-X07) -----------------------------------------------------
def test_missing_generator_refuses_to_start(tmp_path):
    from c3.config import Settings
    from c3.container import load_generator

    s = Settings(model_dir=str(tmp_path / "models"))
    with pytest.raises(RuntimeError, match="generator not found"):
        load_generator(s, s.load_yaml(), None)


def test_changed_model_file_refuses_to_start(tmp_path):
    import hashlib

    from c3.container import verify_manifest

    gen = tmp_path / "generator"
    gen.mkdir()
    f = gen / "model.safetensors"
    f.write_bytes(b"weights")
    (tmp_path / "manifest.json").write_text(json.dumps(
        {"retriever": {}, "generator": {"model.safetensors": hashlib.sha256(b"weights").hexdigest()}}
    ))
    verify_manifest(tmp_path)                                   # intact: fine
    f.write_bytes(b"tampered")
    with pytest.raises(RuntimeError, match="changed since training"):
        verify_manifest(tmp_path)
    f.unlink()
    with pytest.raises(RuntimeError, match="missing"):
        verify_manifest(tmp_path)
    (tmp_path / "manifest.json").unlink()
    with pytest.raises(RuntimeError, match="no model manifest"):
        verify_manifest(tmp_path)
