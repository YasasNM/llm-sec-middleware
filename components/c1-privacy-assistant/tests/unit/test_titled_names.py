"""Unit tests for the M4 titled-name heuristic (the fallback when the model is missing)."""

import pytest

from c1.detection.titled_names import detect_context_names, detect_titled_names


# Return just the name strings found
def names(text: str) -> list[str]:
    return [entity.value for entity in detect_titled_names(text)]


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Patient Nimal Perera was admitted", ["Nimal Perera"]),
        ("Mrs. Kumari Silva, ward 3", ["Kumari Silva"]),
        ("Master Kasun Perera, 6 years", ["Kasun Perera"]),
        ("1) Name: Supun Jayatilleke", ["Supun Jayatilleke"]),
        ("pt Abdul-Cader Hameed", ["Abdul-Cader Hameed"]),
        ("PATIENT Nimal Nimal Perera", ["Nimal Nimal Perera"]),
    ],
)
def test_titled_names_found(text, expected):
    assert names(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "Patient History is attached",
        "patient with fever for 3 days",
        "review Patient P0012 tomorrow",
        "What is the dose of paracetamol?",
        "seen by Dr Fernando today",
        "",
    ],
)
def test_no_name_after_title(text):
    assert names(text) == []


def test_name_is_cut_at_a_non_name_word():
    assert names("Patient Nimal Perera Admitted yesterday") == ["Nimal Perera"]


def test_heuristic_entities_are_labelled():
    entity = detect_titled_names("Patient Nimal Perera")[0]
    assert (entity.type, entity.source, entity.confidence) == ("PATIENT_NAME", "heuristic", 0.7)
    assert "Patient Nimal Perera"[entity.start : entity.end] == "Nimal Perera"


# --- Context cues (detect_context_names) ------------------------------------------------------


# Return (value, source) pairs found after context cues
def context(text: str) -> list[tuple[str, str]]:
    return [(entity.value, entity.source) for entity in detect_context_names(text)]


@pytest.mark.parametrize(
    "text, expected",
    [
        ("my patient name is linuka", ["linuka"]),
        ("patient linuka perera has fever", ["linuka perera"]),
        ("this is mr. sunil silva", ["sunil silva"]),
        ("pt kumari for review", ["kumari"]),
        ("son of nimal perera came", ["nimal perera"]),
        ("a lady named fathima rizvi", ["fathima rizvi"]),
        ("wife of ruwan, 40 years", ["ruwan"]),
    ],
)
def test_names_after_context_cues(text, expected):
    assert [value for value, _ in context(text)] == expected


@pytest.mark.parametrize(
    "text",
    [
        "patient has fever",
        "Dr. nimal",
        "seen by Dr nimal perera",
        "nurse kumari gave the dose",
        "patient denies chest pain",
        "patient [PATIENT_NAME_1] has fever",
        "patient 1234 in ward",
        "patient, linuka",
        "Patient P9908 (see LAB-78338)",
        "dose for a patient weighing 62 kg",
        "",
    ],
)
def test_no_context_name(text):
    assert context(text) == []


def test_context_entities_are_labelled():
    entity = detect_context_names("my patient name is linuka")[0]
    assert (entity.type, entity.confidence, entity.source) == ("PATIENT_NAME", 0.7, "context")
    assert "my patient name is linuka"[entity.start : entity.end] == "linuka"
