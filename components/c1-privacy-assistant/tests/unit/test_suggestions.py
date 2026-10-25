"""Unit tests for re-identification suggestions: natural wording, no doubled words, and
applying the suggestions a second time changes nothing."""

import pytest

from c1.configs import load_config
from c1.reid.finders import QuasiIdentifier
from c1.reid.scorer import score_reid
from c1.reid.suggestions import Edit, apply_edits, suggestion_edits

LISTS = load_config("reid_lists.yaml")


# The safer prompt C1 builds for a text
def safer(text: str) -> str:
    return score_reid(text, LISTS).safer_prompt


@pytest.mark.parametrize(
    "text, expected",
    [
        ("94-year-old HIV patient", "Elderly patient with a chronic condition"),
        (
            "Patient Linuka, 94-year-old HIV patient in Galle ward 3",
            "Patient Linuka, elderly patient with a chronic condition"
            " in the ward in Galle district",
        ),
        ("HIV patient in ward 3", "Patient with a chronic condition in the ward"),
        ("a 94-year-old from Hikkaduwa", "an elderly from Galle district"),
        ("an 45 yrs man", "an adult man"),
        ("94-year-old patient with HIV", "Elderly patient with a chronic condition"),
        ("Free beds? Ward 3 and Ward 7.", "Free beds? The ward and the ward."),
        ("lives in Point Pedro, Jaffna district now", "lives in Jaffna district now"),
        ("in Galle district ward 3", "in the ward in Galle district"),
        ("in Nuwara Eliya District bed 2", "in the ward in Nuwara Eliya District"),
    ],
)
def test_suggestions_read_naturally(text, expected):
    assert safer(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "Patient Linuka, 94-year-old HIV patient in Galle ward 3",
        "A 94-year-old from Hikkaduwa in ward 3",
        "the ward 3 next to Galle district",
        "from Hikkaduwa, Galle district",
        "Patient Linuka, 94-year-old HIV patient in Galle district ward 3",
        "aged 3 years, tuberculosis, bed no. 12, Point Pedro, Jaffna",
        "nothing to change here",
    ],
)
def test_applying_all_suggestions_twice_gives_the_same_text(text):
    once = safer(text)
    assert safer(once) == once
    assert score_reid(once, LISTS).quasi_identifiers == []


@pytest.mark.parametrize(
    "text, expected",
    [
        ("seen in the ward 3 today", "seen in the ward today"),  # not "the the ward"
        ("from Galle district", "from Galle district"),  # not "district district"
    ],
)
def test_no_doubled_words(text, expected):
    assert safer(text) == expected


def test_doubled_word_after_an_edit_is_dropped():
    text = "in Hikkaduwa district"
    item = QuasiIdentifier("LOCATION", 3, 12, "Hikkaduwa", "medium", "Galle district")
    assert apply_edits(text, suggestion_edits(text, [item])) == "in Galle district"


def test_one_finding_at_a_time_matches_the_demo_button():
    text = "Galle ward 3"
    ward = QuasiIdentifier("WARD", 6, 12, "ward 3", "medium", "the ward")
    assert suggestion_edits(text, [ward]) == [Edit(6, 12, "the ward")]
