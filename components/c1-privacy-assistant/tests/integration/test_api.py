"""Integration tests: the C1 web app answering /health, /v1/analyze and /v1/reid."""

import json
import sys
from pathlib import Path

import joblib
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "training" / "scripts"))

import build_corpus  # noqa: E402
import train_names  # noqa: E402
from helpers import GATEWAY_HEADERS, make_client  # noqa: E402

from c1.detection.ml_names import file_sha256  # noqa: E402

DEMO = (
    "Patient Nimal Perera NIC 199512345678, 94-year-old HIV patient in Galle ward 3, review P0012"
)


@pytest.fixture()
def client(tmp_path):
    return make_client(tmp_path)


# Train a tiny model and save it with a matching manifest, as train_names.py would
def save_tiny_model(folder: Path) -> None:
    notes, prompts = build_corpus.build(n_notes=40, n_prompts=120, seed=9)
    train = [record for record in notes + prompts if record["split"] == "train"]
    joblib.dump(
        train_names.fit_model(*train_names.collect_examples(train)), folder / "c1_model.joblib"
    )
    sha = file_sha256(folder / "c1_model.joblib")
    manifest = {"files": {"c1_model.joblib": {"sha256": sha}}}
    (folder / "manifest.json").write_text(json.dumps(manifest))


def test_health_without_model_reports_heuristic(client):
    reply = client.get("/health")
    assert reply.status_code == 200
    assert reply.json()["name_detector"] == "heuristic"


def test_health_with_model_reports_ml(tmp_path):
    save_tiny_model(tmp_path)
    assert make_client(tmp_path).get("/health").json()["name_detector"] == "ml"


def test_analyze_returns_entities_and_risk(client):
    reply = client.post("/v1/analyze", json={"text": DEMO}, headers=GATEWAY_HEADERS)
    body = reply.json()
    assert reply.status_code == 200
    assert [(e["type"], e["value"]) for e in body["entities"]] == [
        ("PATIENT_NAME", "Nimal Perera"),
        ("NIC", "199512345678"),
    ]
    assert body["explicit_risk"] == {"score": 50, "level": "High"}
    assert body["overall_level"] == "High"
    assert body["latency_ms"] >= 0


def test_reid_returns_level_and_safer_prompt(client):
    reply = client.post("/v1/reid", json={"text": DEMO}, headers=GATEWAY_HEADERS)
    body = reply.json()
    assert reply.status_code == 200
    assert (body["level"], body["count"]) == ("High", 4)
    assert "elderly" in body["safer_prompt"] and "94-year-old" not in body["safer_prompt"]
    assert [item["type"] for item in body["quasi_identifiers"]] == [
        "AGE",
        "CONDITION",
        "LOCATION",
        "WARD",
    ]


def test_empty_text_is_fine(client):
    reply = client.post("/v1/analyze", json={"text": ""}, headers=GATEWAY_HEADERS)
    assert reply.status_code == 200 and reply.json()["entities"] == []
