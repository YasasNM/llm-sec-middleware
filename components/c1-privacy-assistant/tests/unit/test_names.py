"""Unit tests for choosing between the ML name model and the heuristic (C1-U-06)."""

from dataclasses import replace

import pytest
from helpers import settings_for_tests

from c1.config import C1_ROOT
from c1.detection.entity import Entity
from c1.detection.names import (
    add_repeated_names,
    find_names,
    load_name_lexicon,
    name_detector_label,
    without_field_labels,
)
from c1.masking.masker import mask
from c1.pipeline import find_explicit, load_resources


def test_no_model_uses_heuristic():
    entities = find_names("Patient Nimal Perera was seen", None)
    assert [(e.value, e.source) for e in entities] == [("Nimal Perera", "heuristic")]
    assert name_detector_label(None) == "heuristic"


def test_with_model_label_is_ml():
    assert name_detector_label({"classifier": "anything"}) == "ml"


# --- Lexicon fallback -------------------------------------------------------------------------

LEXICON = frozenset({"nimal", "kumari", "perera", "sunil", "fathima"})


def test_lowercase_list_of_lexicon_names_is_masked():
    text = "send to nimal , kumari and sunil"
    found = find_names(text, None, lexicon=LEXICON)
    assert [(e.value, e.source, e.confidence) for e in found] == [
        ("nimal", "lexicon", 0.6),
        ("kumari", "lexicon", 0.6),
        ("sunil", "lexicon", 0.6),
    ]


@pytest.mark.parametrize("title", ["Dr.", "Dr", "Nurse", "Sister", "Consultant", "Prof."])
def test_staff_name_after_a_title_is_not_masked(title):
    assert find_names(f"seen by {title} Sunil today", None, lexicon=LEXICON) == []


def test_whole_staff_name_after_a_title_stays_visible():
    text = "seen by Dr. Sunil Perera and dr sunil perera, then nimal"
    found = find_names(text, None, lexicon=LEXICON)
    assert [e.value for e in found] == ["nimal"]


def test_staff_title_does_not_reach_past_a_full_stop():
    text = "Seen by Dr. Sunil. Nimal is stable"
    assert [e.value for e in find_names(text, None, lexicon=LEXICON)] == ["Nimal"]


def test_staff_initials_stay_visible():
    assert find_names("by Dr. R. L. Perera", None, lexicon=LEXICON) == []


def test_model_hits_are_not_duplicated_by_the_lexicon():
    found = find_names("Patient Nimal Perera was seen", None, lexicon=LEXICON)
    assert [(e.value, e.source) for e in found] == [("Nimal Perera", "heuristic")]


def test_words_inside_other_findings_are_skipped():
    email = Entity("EMAIL", 3, 25, "nimal.perera@mail.test", 1.0, "regex")
    text = "to nimal.perera@mail.test"
    assert find_names(text, None, lexicon=LEXICON, already=[email]) == []


def test_lexicon_off_finds_nothing_extra():
    assert find_names("send to nimal and kumari", None) == []


def test_real_lexicon_skips_short_and_common_words():
    words = load_name_lexicon(C1_ROOT / "data" / "lexicons" / "names.json")
    assert "perera" in words and "fathima" in words
    assert "hazel" not in words and "carol" not in words
    assert all(len(word) >= 4 for word in words)


def test_flag_off_disables_the_lexicon_in_the_pipeline(tmp_path):
    on = load_resources(settings_for_tests(tmp_path))
    off = load_resources(replace(settings_for_tests(tmp_path), name_lexicon=False))
    text = "send to nimal and kumari"
    assert [e.value for e in find_explicit(text, on)] == ["nimal", "kumari"]
    assert find_explicit(text, off) == []


# --- Repeated names ---------------------------------------------------------------------------


def test_same_name_twice_gets_one_token_twice(tmp_path):
    resources = load_resources(settings_for_tests(tmp_path))
    text = "Patient Nimal Perera came. Later nimal perera left."
    entities = find_explicit(text, resources)
    assert [e.value for e in entities] == ["Nimal Perera", "nimal perera"]
    masked = mask(text, entities)
    assert masked.masked_text == "Patient [PATIENT_NAME_1] came. Later [PATIENT_NAME_1] left."


def test_repeats_never_overlap_existing_findings():
    text = "Nimal Perera, nimal perera"
    first = Entity("PATIENT_NAME", 0, 12, "Nimal Perera", 0.9, "ml")
    second = Entity("PATIENT_NAME", 14, 26, "nimal perera", 0.6, "lexicon")
    result = add_repeated_names(text, [first, second])
    assert [(e.start, e.end) for e in result] == [(0, 12), (14, 26)]


def test_repeat_is_a_whole_word_only():
    text = "Patient Nimal and Nimali"
    result = add_repeated_names(text, [Entity("PATIENT_NAME", 8, 13, "Nimal", 0.9, "ml")])
    assert [e.value for e in result] == ["Nimal"]


def test_masking_percentage_counts_every_occurrence(tmp_path):
    resources = load_resources(settings_for_tests(tmp_path))
    text = "Patient Nimal Perera; Nimal Perera again"
    entities = find_explicit(text, resources)
    assert len(entities) == 2
    assert mask(text, entities).masking_percentage == round(100 * 24 / len(text), 1)


def test_name_outside_the_lexicon_is_repeated(tmp_path):
    resources = load_resources(settings_for_tests(tmp_path))
    text = "Patient Zorvan Quell came. Ask zorvan quell, not Dr. Zorvan Quell."
    entities = find_explicit(text, resources)
    assert [(e.value, e.source) for e in entities] == [
        ("Zorvan Quell", "heuristic"),
        ("zorvan quell", "repeat"),
    ]


def test_repeat_swallows_a_smaller_name_inside_it():
    text = "Patient Nimal Perera. Ask nimal perera"
    first = Entity("PATIENT_NAME", 8, 20, "Nimal Perera", 0.9, "ml")
    part = Entity("PATIENT_NAME", 26, 31, "nimal", 0.7, "ml")
    result = add_repeated_names(text, [first, part])
    assert [(e.value, e.source) for e in result] == [
        ("Nimal Perera", "ml"),
        ("nimal perera", "repeat"),
    ]


def test_form_labels_are_never_model_names():
    labels = [
        Entity("PATIENT_NAME", 0, 3, "NIC", 0.7, "ml"),
        Entity("PATIENT_NAME", 4, 9, "Nimal", 0.9, "ml"),
    ]
    assert [e.value for e in without_field_labels(labels)] == ["Nimal"]


@pytest.mark.parametrize(
    "text, masked",
    [
        ("my patient name is linuka", "my patient name is [PATIENT_NAME_1]"),
        ("patient linuka perera has fever", "patient [PATIENT_NAME_1] has fever"),
        ("patient has fever", "patient has fever"),
        ("Dr. nimal", "Dr. nimal"),
    ],
)
def test_context_cue_names_are_masked_in_the_pipeline(tmp_path, text, masked):
    resources = load_resources(settings_for_tests(tmp_path))
    assert mask(text, find_explicit(text, resources)).masked_text == masked
