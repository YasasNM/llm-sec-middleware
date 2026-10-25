"""Unit tests for prepare_nemotron.py (benchmark only), using small fake parquet files."""

import json
import sys
from pathlib import Path

import pytest

pd = pytest.importorskip("pandas")
pytest.importorskip("pyarrow")

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "training" / "scripts"))

import prepare_nemotron as pn  # noqa: E402

HEALTH_TEXT = "Patient John Smith was seen by Dr Mary Jones at Acme Clinic."


# Spans for HEALTH_TEXT: two names (first + last each) and one label we do not use
def health_spans() -> list[dict]:
    return [
        {"start": 8, "end": 12, "text": "John", "label": "first_name"},
        {"start": 13, "end": 18, "text": "Smith", "label": "last_name"},
        {"start": 34, "end": 38, "text": "Mary", "label": "first_name"},
        {"start": 39, "end": 44, "text": "Jones", "label": "last_name"},
        {"start": 48, "end": 59, "text": "Acme Clinic", "label": "company_name"},
    ]


# A fake Nemotron table: a few Healthcare rows and more rows from another domain
def fake_frame(health_rows: int = 3, other_rows: int = 10) -> "pd.DataFrame":
    rows = [
        {
            "uid": f"h{i}",
            "text": HEALTH_TEXT,
            "spans": health_spans(),
            "domain": "Healthcare",
            "document_type": "note",
        }
        for i in range(health_rows)
    ]
    rows += [{"uid": f"o{i}", "text": f"Invoice {i} for Ann Lee", "domain": "Finance",
              "spans": [{"start": 14 + len(str(i)), "end": 17 + len(str(i)), "text": "Ann",
                         "label": "first_name"}], "document_type": "invoice"}
             for i in range(other_rows)]  # fmt: skip
    return pd.DataFrame(rows)


# Write the fake table to a parquet file the way Hugging Face ships it
def fake_parquet(folder: Path, name: str, frame: "pd.DataFrame") -> Path:
    path = folder / name
    frame.to_parquet(path)
    return path


def test_read_keeps_only_three_columns(tmp_path):
    frame = pn.read_nemotron(fake_parquet(tmp_path, "train.parquet", fake_frame()))
    assert list(frame.columns) == ["text", "spans", "domain"]


def test_parse_spans_from_string_and_from_list():
    as_text = str(health_spans())
    assert pn.parse_spans(as_text) == health_spans()
    assert pn.parse_spans(health_spans()) == health_spans()


def test_first_and_last_name_become_one_patient_name():
    assert pn.name_spans(HEALTH_TEXT, health_spans()) == [(8, 18), (34, 44)]


def test_other_labels_are_not_names():
    company_only = [span for span in health_spans() if span["label"] == "company_name"]
    assert pn.name_spans(HEALTH_TEXT, company_only) == []


def test_initial_and_last_name_join():
    text = "Seen: J. Smith today"
    parts = [(6, 7), (9, 14)]
    assert pn.merge_name_parts(text, parts) == [(6, 14)]


def test_record_has_exact_spans_and_our_format():
    record = pn.to_record(HEALTH_TEXT, health_spans(), "Healthcare", "train", 1)
    assert record["split"] == "train" and record["source"] == "nemotron"
    assert [e["value"] for e in record["entities"]] == ["John Smith", "Mary Jones"]
    for entity in record["entities"]:
        assert entity["type"] == "PATIENT_NAME"
        assert record["text"][entity["start"] : entity["end"]] == entity["value"]


# A fake test file with held-out domain names, like the real Nemotron test split
def fake_test_frame() -> "pd.DataFrame":
    frame = fake_frame(health_rows=6, other_rows=4)
    frame.loc[0:2, "domain"] = "Health"
    frame.loc[3:5, "domain"] = "Healthcare Providers"
    frame.loc[6:, "domain"] = "Legal Services"
    return frame


def test_test_sample_uses_the_held_out_health_domains():
    sample = pn.sample_test(fake_test_frame(), size=5, seed=42)
    assert len(sample) == 5
    assert set(sample["domain"]) <= {"Health", "Healthcare Providers"}


def test_test_sample_is_repeatable_with_the_same_seed():
    first = pn.sample_test(fake_test_frame(), size=4, seed=42)
    second = pn.sample_test(fake_test_frame(), size=4, seed=42)
    assert list(first.index) == list(second.index)


def test_test_sample_stops_when_no_health_domain_is_found():
    with pytest.raises(ValueError, match="Legal Services"):
        pn.sample_test(fake_frame(0, 5).assign(domain="Legal Services"), size=5, seed=42)


def test_main_writes_only_the_benchmark_file_next_to_the_parquet(tmp_path):
    test = fake_parquet(tmp_path, "test-00000-of-00001.parquet", fake_test_frame())
    pn.main(["--test-parquet", str(test)])
    out = tmp_path / "c1_processed"
    test_rows = [json.loads(line) for line in open(out / "nemotron_test.jsonl")]
    assert len(test_rows) == 6
    assert {row["split"] for row in test_rows} == {"nemotron_test"}
    assert not (out / "nemotron_train.jsonl").exists()


def test_there_is_no_training_sample_any_more():
    # Nemotron-PII is an external benchmark only; nothing here prepares training data
    assert not hasattr(pn, "sample_train")
