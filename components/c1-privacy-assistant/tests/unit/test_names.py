"""Unit tests for choosing between the ML name model and the heuristic (C1-U-06)."""

from c1.detection.names import find_names, name_detector_label


def test_no_model_uses_heuristic():
    entities = find_names("Patient Nimal Perera was seen", None)
    assert [(e.value, e.source) for e in entities] == [("Nimal Perera", "heuristic")]
    assert name_detector_label(None) == "heuristic"


def test_with_model_label_is_ml():
    assert name_detector_label({"classifier": "anything"}) == "ml"
