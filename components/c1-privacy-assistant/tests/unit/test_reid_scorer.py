"""Unit tests for the M5 re-identification score, level, explanation and safer prompt."""

from c1.configs import load_config
from c1.reid.scorer import explain, level_and_score, score_reid
from c1.reid.suggestions import apply_suggestions

LISTS = load_config("reid_lists.yaml")
LEVELS = LISTS["levels"]


def test_blueprint_example_is_high():
    result = score_reid("94-year-old HIV patient in Galle ward 3", LISTS)
    assert (result.level, result.count, result.score) == ("High", 4, 100)


def test_two_kinds_is_medium():
    result = score_reid("Patient is 45 yrs, from Hikkaduwa, with fever.", LISTS)
    assert (result.level, result.count) == ("Medium", 2)


def test_repeats_of_one_kind_stay_low():
    # Corpus hard negative N09: two ward numbers, no patient
    result = score_reid("Which wards have free beds today? Ward 3 and Ward 7.", LISTS)
    assert (result.level, result.count) == ("Low", 1)


def test_safe_prompt_scores_zero():
    result = score_reid("What is the first-line treatment for hypertension?", LISTS)
    assert (result.level, result.score, result.count) == ("Low", 0, 0)
    assert result.safer_prompt == "What is the first-line treatment for hypertension?"


def test_level_rules():
    assert level_and_score(3, 3, LEVELS) == ("High", 78)
    assert level_and_score(1, 7, LEVELS) == ("High", 100)
    assert level_and_score(2, 2, LEVELS) == ("Medium", 40)
    assert level_and_score(1, 4, LEVELS) == ("Medium", 50)
    assert level_and_score(1, 3, LEVELS) == ("Low", 29)
    assert level_and_score(0, 0, LEVELS) == ("Low", 0)


def test_safer_prompt_uses_suggestions():
    result = score_reid("A 94-year-old from Hikkaduwa in ward 3", LISTS)
    assert result.safer_prompt == "An elderly from Galle district in the ward"


def test_apply_suggestions_with_nothing_found():
    assert apply_suggestions("no change", []) == "no change"


def test_explanation_names_kinds_not_values():
    result = score_reid("94-year-old HIV patient in Galle ward 3", LISTS)
    assert "age" in result.explanation and "condition" in result.explanation
    for value in ("94", "HIV", "Galle", "ward 3"):
        assert value not in result.explanation


def test_explain_with_nothing_found():
    assert explain([], "Low") == "No re-identification details found."


def test_placeholders_do_not_count_as_quasi_identifiers():
    result = score_reid("[PATIENT_NAME_1], [NIC_1], [PHONE_1]", LISTS)
    assert result.count == 0
