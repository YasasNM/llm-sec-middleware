"""Unit tests for the M3 ML name detector and its safe model loading (C1-U-06)."""

import json
import sys
from pathlib import Path

import joblib
import pytest

from c1.detection.ml_names import (
    Token,
    character_after,
    detect_ml_names,
    file_sha256,
    group_into_names,
    group_start,
    load_name_model,
    same_group,
    tokenize,
    word_shape,
)

SCRIPTS = Path(__file__).resolve().parents[2] / "training" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import build_corpus  # noqa: E402
import train_names  # noqa: E402


@pytest.fixture(scope="module")
def small_corpus():
    notes, prompts = build_corpus.build(n_notes=80, n_prompts=300, seed=5)
    return notes + prompts


@pytest.fixture(scope="module")
def small_model(small_corpus):
    train = [record for record in small_corpus if record["split"] == "train"]
    return train_names.fit_model(*train_names.collect_examples(train))


def test_tokenize_keeps_positions_and_splits_apostrophes():
    tokens = tokenize("Nimal Perera's wife, Abdul-Cader")
    assert [token.text for token in tokens] == ["Nimal", "Perera", "s", "wife", "Abdul-Cader"]
    assert tokens[1].start == 6 and tokens[1].end == 12


@pytest.mark.parametrize("word, shape", [("A", "X"), ("MRN", "XX"), ("Perera", "Xx"), ("the", "x")])
def test_word_shape(word, shape):
    assert word_shape(word) == shape


def test_character_after():
    text = "R.L. Gamage, end"
    tokens = tokenize(text)
    assert character_after(text, tokens[0]) == "after:dot"
    assert character_after(text, tokens[2]) == "after:comma"
    assert character_after(text, tokens[3]) == "after:edge"


def test_digit_after_a_letter_is_marked():
    text = "Patient ID P1728"
    assert character_after(text, tokenize(text)[2]) == "after:digit"


def test_same_group_joins_capitalised_words_but_not_titles():
    text = "Dr. Nimal Perera saw nimal"
    tokens = tokenize(text)
    assert same_group(text, tokens[1], tokens[2])  # Nimal Perera
    assert not same_group(text, tokens[0], tokens[1])  # a title ends the group
    assert not same_group(text, tokens[3], tokens[4])  # lowercase words never group


def test_group_start_is_the_word_before_the_name_group():
    text = "Dr. Nimal Perera and Name: Edward Christopher Ludowyk"
    tokens = tokenize(text)
    assert group_start(text, tokens, 2) == "groupstart:dr"  # Perera
    assert group_start(text, tokens, 6) == "groupstart:name"  # Christopher
    assert group_start(text, tokens, 0) == "groupstart:<edge>"


def test_grouping_joins_neighbours_above_threshold():
    text = "Patient Nimal Perera was seen"
    tokens = tokenize(text)
    names = group_into_names(text, tokens, [0.1, 0.9, 0.7, 0.1, 0.1], 0.65)
    assert [(name.value, name.confidence) for name in names] == [("Nimal Perera", 0.7)]


def test_grouping_joins_initials_across_dots():
    text = "for R.L. Gamage today"
    names = group_into_names(text, tokenize(text), [0.1, 0.9, 0.9, 0.9, 0.1], 0.65)
    assert [name.value for name in names] == ["R.L. Gamage"]


def test_grouping_keeps_separate_names_apart():
    text = "Nimal and Kumari"
    names = group_into_names(text, tokenize(text), [0.9, 0.1, 0.9], 0.65)
    assert [name.value for name in names] == ["Nimal", "Kumari"]


def test_grouping_with_no_tokens():
    assert group_into_names("", [], [], 0.65) == []


def test_small_model_learns_names(small_corpus, small_model):
    val = [record for record in small_corpus if record["split"] == "val"]
    scores = train_names.span_scores(val, lambda text: detect_ml_names(text, small_model))
    assert scores["f1"] > 0.5


def test_model_entities_point_at_original_text(small_corpus, small_model):
    for record in small_corpus[:40]:
        for entity in detect_ml_names(record["text"], small_model):
            assert record["text"][entity.start : entity.end] == entity.value
            assert entity.type == "PATIENT_NAME" and entity.source == "ml"


def test_empty_text_with_model(small_model):
    assert detect_ml_names("", small_model) == []


# Save a model plus a manifest that names its hash, the way train_names.py does
def save_with_manifest(model, folder: Path, sha: str | None = None) -> tuple[Path, Path]:
    model_path, manifest_path = folder / "c1_model.joblib", folder / "manifest.json"
    joblib.dump(model, model_path)
    digest = sha or file_sha256(model_path)
    manifest_path.write_text(json.dumps({"files": {"c1_model.joblib": {"sha256": digest}}}))
    return model_path, manifest_path


def test_load_model_with_matching_hash(small_model, tmp_path):
    model_path, manifest_path = save_with_manifest(small_model, tmp_path)
    assert load_name_model(model_path, manifest_path) is not None


# C1-U-06: model file missing
def test_load_model_missing_file_returns_none(tmp_path):
    assert load_name_model(tmp_path / "nope.joblib", tmp_path / "manifest.json") is None


def test_load_model_with_wrong_hash_returns_none(small_model, tmp_path):
    model_path, manifest_path = save_with_manifest(small_model, tmp_path, sha="0" * 64)
    assert load_name_model(model_path, manifest_path) is None


def test_load_model_without_manifest_returns_none(small_model, tmp_path):
    model_path, manifest_path = save_with_manifest(small_model, tmp_path)
    manifest_path.unlink()
    assert load_name_model(model_path, manifest_path) is None


def test_token_dataclass_is_simple():
    assert Token("Nimal", 0, 5).text == "Nimal"


def test_full_stop_after_a_whole_word_ends_the_name():
    text = "ask kumari. nimal perera"
    names = group_into_names(text, tokenize(text), [0.1, 0.9, 0.9, 0.9], 0.65)
    assert [name.value for name in names] == ["kumari", "nimal perera"]
