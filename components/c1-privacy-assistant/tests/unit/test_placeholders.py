"""Unit tests for placeholder tokens: nothing inside [PATIENT_NAME_1] is ever detected again."""

from c1.config import C1_ROOT
from c1.configs import load_config
from c1.detection.entity import Entity
from c1.detection.placeholders import blank_placeholders, outside_placeholders, placeholder_spans
from c1.reid.scorer import score_reid

TOKENS_ONLY = "[PATIENT_NAME_1] [PATIENT_NAME_2] [PHONE_1]"


def test_spans_of_tokens():
    assert placeholder_spans("hi [NIC_1] and [PATIENT_NAME_12]") == [(3, 10), (15, 32)]


def test_blanking_keeps_every_position():
    text = "Ask [PATIENT_NAME_1] now"
    clean = blank_placeholders(text)
    assert len(clean) == len(text) and clean == "Ask " + " " * 16 + " now"


def test_findings_touching_a_token_are_dropped():
    text = "[PATIENT_NAME_1] Nimal"
    inside = Entity("PATIENT_NAME", 9, 13, "NAME", 0.8, "ml")
    outside = Entity("PATIENT_NAME", 17, 22, "Nimal", 0.9, "ml")
    assert outside_placeholders([inside, outside], text) == [outside]


def test_lowercase_or_unnumbered_brackets_are_not_tokens():
    assert placeholder_spans("[note] [patient_name_1] [NIC]") == []


def test_reid_finds_nothing_inside_tokens():
    result = score_reid(
        "[WARD_1] [AGE_1] " + TOKENS_ONLY, load_config("reid_lists.yaml", C1_ROOT / "configs")
    )
    assert result.quasi_identifiers == [] and result.score == 0
