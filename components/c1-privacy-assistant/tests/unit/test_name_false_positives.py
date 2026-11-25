"""Regression tests for three name false positives found on 26 October:
a lone "A" at a sentence start, its lowercase "a" repeats, and "support options" after "patient".

Most tests run without the model file. The tests marked `needs_model` use the real trained model
and are skipped when models/c1_model.joblib is not present (it is never committed).
"""

from dataclasses import replace

import pytest
from helpers import settings_for_tests

from c1.config import C1_ROOT
from c1.detection.entity import Entity
from c1.detection.ml_names import Token, group_into_names
from c1.detection.names import add_repeated_names
from c1.detection.titled_names import detect_context_names
from c1.pipeline import analyze_text, find_explicit, load_resources

TEST_PROMPT = (
    "A 94-year-old patient with HIV is staying in ward 3 in Galle. "
    "Suggest suitable patient support options."
)
MODEL_FILE = C1_ROOT / "models" / "c1_model.joblib"
needs_model = pytest.mark.skipif(not MODEL_FILE.exists(), reason="trained model file not present")


# Resources with the real model, the real manifest and the real name lexicon
def model_resources(tmp_path):
    settings = replace(
        settings_for_tests(tmp_path),
        model_path=MODEL_FILE,
        manifest_path=C1_ROOT / "models" / "manifest.json",
    )
    resources = load_resources(settings)
    assert resources.name_model is not None  # a hash mismatch must fail loudly, not skip
    return resources


# Every PATIENT_NAME value found in the text
def name_values(text, resources):
    return [e.value for e in find_explicit(text, resources) if e.type == "PATIENT_NAME"]


# --- 1. A lone single letter is never a model name --------------------------------------------


# Tokens for "A Perera" style tests; positions are worked out from the text
def tokens_of(text, words):
    tokens, position = [], 0
    for word in words:
        start = text.index(word, position)
        tokens.append(Token(word, start, start + len(word)))
        position = start + len(word)
    return tokens


def test_lone_capital_letter_is_not_a_name():
    text = "A patient"
    tokens = tokens_of(text, ["A", "patient"])
    assert group_into_names(text, tokens, [0.83, 0.01], 0.65) == []


def test_two_lone_initials_are_not_a_name():
    text = "R.L. was seen"
    tokens = tokens_of(text, ["R", "L", "was", "seen"])
    assert group_into_names(text, tokens, [0.9, 0.9, 0.0, 0.0], 0.65) == []


def test_initials_joined_to_a_surname_are_kept():
    text = "R. Perera and R.L. Gamage"
    tokens = tokens_of(text, ["R", "Perera", "and", "R", "L", "Gamage"])
    names = group_into_names(text, tokens, [0.9, 0.9, 0.0, 0.9, 0.9, 0.9], 0.65)
    assert [n.value for n in names] == ["R. Perera", "R.L. Gamage"]


# --- 2. Repeats: no single letters, no lowercase copies of a sentence-start capital ------------


def test_single_letter_name_is_never_repeated():
    text = "A patient with a chronic condition"
    result = add_repeated_names(text, [Entity("PATIENT_NAME", 0, 1, "A", 0.83, "context")])
    assert [e.value for e in result] == ["A"]


def test_sentence_start_model_name_has_no_lowercase_repeat():
    text = "Hope was seen. We hope to help."
    hope = Entity("PATIENT_NAME", 0, 4, "Hope", 0.8, "ml")
    assert [e.value for e in add_repeated_names(text, [hope])] == ["Hope"]


def test_sentence_start_model_name_still_repeats_with_the_same_capitals():
    text = "Kamal came. Later Kamal left."
    kamal = Entity("PATIENT_NAME", 0, 5, "Kamal", 0.9, "ml")
    assert [e.value for e in add_repeated_names(text, [kamal])] == ["Kamal", "Kamal"]


def test_mid_sentence_name_still_repeats_in_lowercase():
    text = "Patient Nimal came. Ask nimal later."
    nimal = Entity("PATIENT_NAME", 8, 13, "Nimal", 0.9, "ml")
    assert [e.value for e in add_repeated_names(text, [nimal])] == ["Nimal", "nimal"]


# --- 3. Context cue "patient": common English words are never names ---------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Suggest suitable patient support options.",
        "patient care plan",
        "patient education and safety",
        "patient transport needs",
        "patient history",
        "the patient is stable",
    ],
)
def test_common_words_after_patient_are_not_names(text):
    assert detect_context_names(text) == []


def test_lowercase_name_after_patient_is_still_found():
    found = detect_context_names("patient kumari needs review")
    assert [e.value for e in found] == ["kumari"]


def test_other_cues_are_unchanged():
    assert [e.value for e in detect_context_names("my patient name is linuka")] == ["linuka"]


# --- The four prompts from the bug report, without the model (rules and lexicon) ---------------


def test_test_prompt_has_no_names_without_model(tmp_path):
    resources = load_resources(settings_for_tests(tmp_path))
    assert name_values(TEST_PROMPT, resources) == []


@pytest.mark.parametrize(
    "text, names",
    [
        ("Patient Nimal Perera was admitted", ["Nimal Perera"]),
        ("patient kumari needs review", ["kumari"]),
    ],
)
def test_real_names_still_found_without_model(tmp_path, text, names):
    resources = load_resources(settings_for_tests(tmp_path))
    assert name_values(text, resources) == names


# --- The four prompts from the bug report, with the real model ---------------------------------


@needs_model
def test_test_prompt_has_no_names_with_model(tmp_path):
    resources = model_resources(tmp_path)
    analysis = analyze_text(TEST_PROMPT, resources)
    assert [e for e in analysis.entities if e.type == "PATIENT_NAME"] == []
    assert analysis.risk["explicit"] == {"score": 0, "level": "Low"}


@needs_model
def test_test_prompt_with_suggestions_applied_has_no_names(tmp_path):
    resources = model_resources(tmp_path)
    safer = analyze_text(TEST_PROMPT, resources).reid.safer_prompt
    assert name_values(safer, resources) == []


@needs_model
@pytest.mark.parametrize(
    "text, names",
    [
        ("Patient Nimal Perera was admitted", ["Nimal Perera"]),
        ("R. Perera and A. Silva", ["R. Perera", "A. Silva"]),
        ("patient kumari needs review", ["kumari"]),
    ],
)
def test_real_names_still_found_with_model(tmp_path, text, names):
    assert name_values(text, model_resources(tmp_path)) == names
