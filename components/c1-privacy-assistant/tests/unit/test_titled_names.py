"""Unit tests for the M4 titled-name heuristic (the fallback when the model is missing)."""

import pytest

from c1.detection.titled_names import detect_titled_names


# Return just the name strings found
def names(text: str) -> list[str]:
    return [entity.value for entity in detect_titled_names(text)]


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Patient Nimal Perera was admitted", ["Nimal Perera"]),
        ("Mrs. Kumari Silva, ward 3", ["Kumari Silva"]),
        ("seen by Dr Fernando today", ["Fernando"]),
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
