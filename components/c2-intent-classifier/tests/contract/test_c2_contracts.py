from __future__ import annotations

import json
from pathlib import Path

import pytest

from c2.events import build_event, now_utc
from c2.schemas import C2Event, C2Input, C2Output

REPO = Path(__file__).resolve().parents[4]
SCHEMAS = REPO / "contracts" / "schemas"
EXAMPLES = REPO / "contracts" / "examples"

jsonschema = pytest.importorskip("jsonschema")


def _schema(name: str) -> dict:
    return json.loads((SCHEMAS / name).read_text())


def _example(name: str) -> dict:
    return json.loads((EXAMPLES / name).read_text())


def test_official_classify_request_example() -> None:
    jsonschema.validate(_example("c2_classify_request.json"), _schema("c2_classify_request.schema.json"))
    C2Input.model_validate(_example("c2_classify_request.json"))


def test_official_classify_response_example() -> None:
    jsonschema.validate(_example("c2_classify_response.json"), _schema("c2_classify_response.schema.json"))
    C2Output.model_validate(_example("c2_classify_response.json"))


def test_official_event_example() -> None:
    jsonschema.validate(_example("c2_event.json"), _schema("c2_event.schema.json"))
    C2Event.model_validate(_example("c2_event.json"))


def test_built_event_matches_contract() -> None:
    req = C2Input.model_validate(_example("c2_classify_request.json"))
    out = C2Output(
        request_id=req.request_id,
        c2_verdict="BLOCK",
        c2_score=0.87,
        method="ensemble",
        nearest_attack_ids=["hc-001-023"],
        degraded=False,
        latency_ms=61,
        model_version="c2-electra-small-v0",
    )
    event = build_event(req, out)
    jsonschema.validate(event.model_dump(), _schema("c2_event.schema.json"))
    assert event.event_timestamp.endswith("Z")
    assert now_utc().endswith("Z")
