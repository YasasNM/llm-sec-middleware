"""Unit tests for c1.detection.normalise (undoing tricks that hide identifiers)."""

from c1.detection.normalise import normalise, to_original_span


def test_plain_text_is_unchanged():
    cleaned = normalise("Patient seen in ward 3")
    assert cleaned.text == "Patient seen in ward 3"
    assert cleaned.starts == list(range(len("Patient seen in ward 3")))


def test_spaced_digits_are_joined():
    assert normalise("call 0 7 7 1 2 3 4 5 6 7 now").text == "call 0771234567 now"


def test_short_spaced_runs_are_left_alone():
    # Three single digits is too short to be a hidden identifier
    assert normalise("grades 1 2 3").text == "grades 1 2 3"


def test_at_and_dot_words_become_symbols():
    assert normalise("nimal[at]gmail[dot]com").text == "nimal@gmail.com"
    assert normalise("nimal [AT] gmail.com").text == "nimal@gmail.com"
    assert normalise("nimal(at)gmail.com").text == "nimal@gmail.com"


def test_span_maps_back_to_original_spaced_digits():
    original = "call 0 7 7 1 2 3 4 5 6 7 now"
    cleaned = normalise(original)
    start, end = to_original_span(cleaned, 5, 15)
    assert original[start:end] == "0 7 7 1 2 3 4 5 6 7"


def test_span_maps_back_to_original_at_word():
    original = "mail nimal[at]gmail.com"
    cleaned = normalise(original)
    start, end = to_original_span(cleaned, 5, len(cleaned.text))
    assert original[start:end] == "nimal[at]gmail.com"


def test_empty_text():
    assert normalise("").text == ""
