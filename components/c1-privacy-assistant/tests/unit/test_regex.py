"""Unit tests for the M2 regex detector (test ids from BLUEPRINT section 21)."""

import pytest

from c1.detection.entity import Entity
from c1.detection.regex import detect_regex, drop_internal_ids


# Return (type, value) pairs so tests read clearly
def found(text: str) -> list[tuple[str, str]]:
    return [(entity.type, entity.value) for entity in detect_regex(text)]


# C1-U-01
@pytest.mark.parametrize("nic", ["199512345678", "951234567V", "951234567v", "951234567X"])
def test_nic_formats(nic):
    assert found(f"NIC {nic} on file") == [("NIC", nic)]


# C1-U-02
@pytest.mark.parametrize(
    "phone", ["0771234567", "077 123 4567", "077-123-4567", "+94771234567", "+94 77 123 4567"]
)
def test_phone_formats(phone):
    assert found(f"call {phone} today") == [("PHONE", phone)]


def test_spaced_out_phone_is_caught():
    assert found("call 0 7 7 1 2 3 4 5 6 7 today") == [("PHONE", "0 7 7 1 2 3 4 5 6 7")]


# C1-U-03
@pytest.mark.parametrize("text", ["review P0012", "assign D003", "P0012 seen by D003"])
def test_internal_ids_are_never_entities(text):
    assert found(text) == []


def test_drop_internal_ids_removes_touching_entity():
    text = "see P0012"
    fake = Entity("MRN", 5, 9, "0012", 0.99, "regex")
    assert drop_internal_ids(text, [fake]) == []


@pytest.mark.parametrize("text", ["MRN 123456", "MRN: 123456", "mrn:123456", "MRN #123456"])
def test_mrn_keeps_label_and_finds_number(text):
    assert found(text) == [("MRN", "123456")]


def test_email_and_disguised_email():
    assert found("mail nimal.perera@gmail.com") == [("EMAIL", "nimal.perera@gmail.com")]
    assert found("mail nimal[at]gmail.com") == [("EMAIL", "nimal[at]gmail.com")]
    assert found("mail nimal [at] gmail [dot] com") == [("EMAIL", "nimal [at] gmail [dot] com")]


@pytest.mark.parametrize(
    "text, date",
    [
        ("DOB 14/03/1985", "14/03/1985"),
        ("dob: 14-03-85", "14-03-85"),
        ("date of birth: 1985-03-14", "1985-03-14"),
        ("born on 14 March 1985", "14 March 1985"),
        ("born 14th Mar 1985", "14th Mar 1985"),
        ("D.O.B. March 14, 1985", "March 14, 1985"),
    ],
)
def test_dob_with_label(text, date):
    assert found(text) == [("DOB", date)]


def test_date_without_dob_label_is_not_dob():
    assert found("review on 14/03/2026") == []


# C1-U-07
@pytest.mark.parametrize(
    "text",
    [
        "What is the first-line treatment for hypertension?",
        "BP 120/80, HR 72, temp 37.2",
        "",
        "12345",
    ],
)
def test_safe_prompts_have_no_entities(text):
    assert found(text) == []


def test_spans_point_at_original_text():
    text = "Patient NIC 951234567V, call 0 7 7 1 2 3 4 5 6 7, mail a[at]b.lk, review P0012"
    for entity in detect_regex(text):
        assert text[entity.start : entity.end] == entity.value


def test_results_are_sorted_by_position():
    entities = detect_regex("mail a@b.lk then NIC 951234567V then call 0771234567")
    assert [entity.type for entity in entities] == ["EMAIL", "NIC", "PHONE"]


def test_regex_entities_have_source_and_confidence():
    entity = detect_regex("NIC 951234567V")[0]
    assert entity.source == "regex" and entity.confidence == 0.99


def test_sinhala_and_tamil_text_do_not_crash():
    assert found("රෝගියා ඉතා අසනීපයි. நோயாளி") == []


def test_entity_repr_never_shows_the_value():
    entity = detect_regex("NIC 951234567V")[0]
    assert "951234567V" not in repr(entity)
