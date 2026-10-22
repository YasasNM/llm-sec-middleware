"""Unit tests for finding quasi-identifiers: age, condition, ward and location (M5)."""

import pytest

from c1.configs import load_config
from c1.reid.finders import (
    age_in_years,
    age_rarity,
    age_suggestion,
    find_ages,
    find_conditions,
    find_locations,
    find_quasi_identifiers,
    find_wards,
)

LISTS = load_config("reid_lists.yaml")


# Return (match, rarity) pairs so tests read clearly
def pairs(found) -> list[tuple[str, str]]:
    return [(item.match, item.rarity) for item in found]


@pytest.mark.parametrize(
    "text, expected",
    [
        ("a 94-year-old man", [("94-year-old", "high")]),
        ("she is 45 years old", [("45 years old", "low")]),
        ("Patient is 45 yrs, from home", [("45 yrs", "low")]),
        ("male 45y with fever", [("45y", "low")]),
        ("62 y/o lady", [("62 y/o", "low")]),
        ("2) Age: 53 years", [("53 years", "low")]),
        ("aged 3 years", [("3 years", "high")]),
        ("a 9-month-old baby", [("9-month-old", "high")]),
    ],
)
def test_ages_found(text, expected):
    assert pairs(find_ages(text, LISTS)) == expected


@pytest.mark.parametrize(
    "text",
    [
        "DM: Yes, 3 years, on metformin",
        "cough for 2 weeks",
        "fever since 3 days",
        "BP 120/80",
        "dose 5 mg daily",
    ],
)
def test_durations_and_numbers_are_not_ages(text):
    assert find_ages(text, LISTS) == []


def test_age_helpers():
    assert age_in_years(9, "months") == 0.75
    assert age_rarity(0.75, LISTS["age"]) == "high"
    assert age_rarity(90, LISTS["age"]) == "high"
    assert age_rarity(40, LISTS["age"]) == "low"
    assert age_suggestion(94, LISTS["age"]) == "elderly"
    assert age_suggestion(0.5, LISTS["age"]) == "infant"
    assert age_suggestion(40, LISTS["age"]) == "adult"


@pytest.mark.parametrize(
    "text, expected",
    [
        ("known HIV patient", [("HIV", "high")]),
        ("has Parkinson's disease", [("Parkinson's disease", "medium")]),
        ("treated for tuberculosis", [("tuberculosis", "medium")]),
        ("child with Down syndrome", [("Down syndrome", "medium")]),
    ],
)
def test_conditions_found(text, expected):
    assert pairs(find_conditions(text, LISTS)) == expected


@pytest.mark.parametrize("text", ["my hearing aids are broken", "go down the corridor", "tb"])
def test_ordinary_words_are_not_conditions(text):
    assert find_conditions(text, LISTS) == []


def test_wards_found():
    assert pairs(find_wards("in ward 3, bed no. 12, ICU", LISTS)) == [
        ("ward 3", "medium"),
        ("bed no. 12", "medium"),
    ]


def test_locations_small_town_and_big_city():
    found = find_locations("from Hikkaduwa, now in Colombo", LISTS)
    assert pairs(found) == [("Hikkaduwa", "medium"), ("Colombo", "low")]
    assert [item.suggestion for item in found] == ["Galle district", "Colombo district"]


def test_location_includes_following_district():
    found = find_locations("lives in Point Pedro, Jaffna now", LISTS)
    assert pairs(found) == [("Point Pedro, Jaffna", "medium")]


def test_longest_town_name_wins():
    assert pairs(find_locations("from Colombo 05", LISTS)) == [("Colombo 05", "low")]


def test_all_finders_together_in_reading_order():
    found = find_quasi_identifiers("94-year-old HIV patient in Galle ward 3", LISTS)
    assert [item.type for item in found] == ["AGE", "CONDITION", "LOCATION", "WARD"]


def test_spans_point_at_original_text():
    text = "Patient is 45 yrs, from Point Pedro, Jaffna, HIV, ward 7"
    for item in find_quasi_identifiers(text, LISTS):
        assert text[item.start : item.end] == item.match


def test_repr_never_shows_the_match():
    item = find_ages("a 94-year-old man", LISTS)[0]
    assert "94-year-old" not in repr(item)


def test_sinhala_text_does_not_crash():
    assert find_quasi_identifiers("රෝගියා ඉතා අසනීපයි", LISTS) == []


@pytest.mark.parametrize(
    "text, expected",
    [
        ("has Down's syndrome", "Down's syndrome"),
        ("has Wilson disease", "Wilson disease"),
        ("has Wilson's disease", "Wilson's disease"),
    ],
)
def test_eponyms_with_their_word(text, expected):
    assert [item.match for item in find_conditions(text, LISTS)] == [expected]


@pytest.mark.parametrize("text", ["Mr Wilson called", "go down the corridor", "Graves were dug"])
def test_eponyms_alone_are_not_conditions(text):
    assert find_conditions(text, LISTS) == []
