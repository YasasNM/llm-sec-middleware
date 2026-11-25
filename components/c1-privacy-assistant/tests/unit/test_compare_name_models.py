"""Unit tests for training the own-corpus name model and scoring it on the benchmark."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "training" / "scripts"))

import build_corpus  # noqa: E402
import compare_name_models as compare  # noqa: E402
import train_names as tn  # noqa: E402

from c1.detection.ml_names import load_name_model  # noqa: E402


# Write a list of records as a JSON Lines file
def write_jsonl(path: Path, records: list[dict]) -> Path:
    path.write_text("".join(json.dumps(record) + "\n" for record in records))
    return path


@pytest.fixture(scope="module")
def own_data(tmp_path_factory):
    folder = tmp_path_factory.mktemp("processed")
    notes, prompts = build_corpus.build(n_notes=40, n_prompts=120, seed=3)
    write_jsonl(folder / "clinical_notes.jsonl", notes)
    write_jsonl(folder / "sensitivity_dataset.jsonl", prompts)
    return folder


def test_training_uses_only_our_own_corpus(own_data):
    records, info = tn.training_data(tn.parse_args(["--data", str(own_data)]))
    assert info["sources"] == [tn.OWN_SOURCE]
    assert len(records) == info["rows"]["own_train"]


def test_there_is_no_mixed_training_option():
    with pytest.raises(SystemExit):
        tn.parse_args(["--nemotron", "x.jsonl"])


def test_writing_the_model_entry_keeps_other_manifest_fields(tmp_path):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"model": "old", "files": {"c1_model.joblib": {"sha256": "a"}}}))
    tn.write_manifest(manifest, tmp_path / "c1_model.joblib", {"sha256": "b"})
    saved = json.loads(manifest.read_text())
    assert saved["model"] == "old" and saved["files"] == {"c1_model.joblib": {"sha256": "b"}}


def test_train_end_to_end_and_load_the_model(own_data, tmp_path):
    out = tmp_path / "models" / "c1_model.joblib"
    tn.main(["--data", str(own_data), "--out", str(out)])
    entry = json.loads((out.parent / "manifest.json").read_text())["files"][out.name]
    assert entry["sources"] == [tn.OWN_SOURCE] and entry["threshold"] == 0.65
    assert load_name_model(out, out.parent / "manifest.json") is not None


def test_benchmark_rows_match_span_scores(own_data):
    train = tn.load_split(own_data, "train")
    val = tn.load_split(own_data, "val")
    model = tn.fit_model(*tn.collect_examples(train))
    rows = compare.rows_for(compare.MODEL_LABEL, "own val", val, model)
    assert [row["threshold"] for row in rows] == [0.5, 0.65, 0.8]
    direct = tn.span_scores(val, lambda text: tn.detect_ml_names(text, model, 0.65))
    assert {key: rows[1][key] for key in ("precision", "recall", "f1")} == direct


def test_missing_model_stops_with_a_clear_message(tmp_path):
    with pytest.raises(SystemExit, match="c1_model.joblib"):
        compare.load_own_model(tmp_path)


def test_print_table_has_a_header(capsys):
    row = {"model": "C1", "dataset": "own val", "threshold": 0.65,
           "precision": 0.9, "recall": 0.8, "f1": 0.85}  # fmt: skip
    compare.print_table([row])
    assert "| Model | Evaluated on |" in capsys.readouterr().out


def test_prf_with_nothing_found():
    assert tn.prf(0, 0, 5) == {"precision": 0.0, "recall": 0.0, "f1": 0.0}
