"""Unit tests for the overlap rule: longest span, then confidence, then type priority."""

from c1.detection.entity import Entity
from c1.detection.overlaps import resolve_overlaps
from c1.detection.regex import detect_regex
from c1.masking.masker import mask


# Make an entity quickly; the value is a placeholder because only positions matter here
def make(entity_type: str, start: int, end: int, confidence: float = 0.9) -> Entity:
    return Entity(entity_type, start, end, "x" * (end - start), confidence, "test")


def test_longest_span_wins():
    longer, shorter = make("EMAIL", 0, 22), make("PATIENT_NAME", 0, 5)
    assert resolve_overlaps([shorter, longer]) == [longer]


def test_higher_confidence_wins_when_same_length():
    weak, strong = make("PATIENT_NAME", 0, 5, 0.7), make("PATIENT_NAME", 0, 5, 0.95)
    assert resolve_overlaps([weak, strong]) == [strong]


def test_type_priority_breaks_a_full_tie():
    phone, mrn = make("PHONE", 5, 15, 0.99), make("MRN", 5, 15, 0.99)
    assert resolve_overlaps([phone, mrn]) == [mrn]


def test_separate_entities_are_all_kept_in_order():
    first, second = make("NIC", 10, 20), make("PHONE", 0, 5)
    assert resolve_overlaps([first, second]) == [second, first]


def test_touching_but_not_overlapping_are_both_kept():
    left, right = make("NIC", 0, 5), make("PHONE", 5, 10)
    assert resolve_overlaps([left, right]) == [left, right]


def test_empty_list():
    assert resolve_overlaps([]) == []


def test_mrn_with_phone_shaped_number_is_masked_as_mrn():
    text = "MRN: 0771234567"
    result = mask(text, resolve_overlaps(detect_regex(text)))
    assert result.masked_text == "MRN: [MRN_1]"
