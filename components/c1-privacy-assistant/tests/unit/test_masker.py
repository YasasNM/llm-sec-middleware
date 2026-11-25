"""Unit tests for the M8 masker (test ids from BLUEPRINT section 21)."""

from c1.detection.entity import Entity
from c1.detection.regex import detect_regex
from c1.masking.masker import mask


# Build an Entity for the exact text it covers, so tests stay short
def entity_for(text: str, part: str, entity_type: str, occurrence: int = 0) -> Entity:
    start = -1
    for _ in range(occurrence + 1):
        start = text.index(part, start + 1)
    return Entity(entity_type, start, start + len(part), part, 0.9, "test")


# C1-U-05: the worked example from contracts/examples/c1_check_response.json
def test_contract_example_exact_percentage():
    text = "Patient Nimal Perera NIC 199512345678, review P0012"
    entities = [entity_for(text, "Nimal Perera", "PATIENT_NAME"), *detect_regex(text)]
    result = mask(text, entities)
    assert result.masked_text == "Patient [PATIENT_NAME_1] NIC [NIC_1], review P0012"
    assert result.placeholder_manifest == ["[PATIENT_NAME_1]", "[NIC_1]"]
    assert result.masking_percentage == 47.1
    assert result.entity_counts == {"PATIENT_NAME": 1, "NIC": 1}


# C1-U-04: the same value gets the same token
def test_same_name_twice_gets_same_token():
    text = "Nimal Perera was seen. Call Nimal Perera's wife."
    entities = [
        entity_for(text, "Nimal Perera", "PATIENT_NAME", 0),
        entity_for(text, "Nimal Perera", "PATIENT_NAME", 1),
    ]
    result = mask(text, entities)
    assert result.masked_text == "[PATIENT_NAME_1] was seen. Call [PATIENT_NAME_1]'s wife."
    assert result.placeholder_manifest == ["[PATIENT_NAME_1]"]
    assert result.entity_counts == {"PATIENT_NAME": 2}


def test_same_phone_written_two_ways_gets_same_token():
    text = "call 0771234567 or 077 123 4567"
    result = mask(text, detect_regex(text))
    assert result.masked_text == "call [PHONE_1] or [PHONE_1]"


def test_numbering_is_per_type():
    text = "call 0771234567 or 0719876543, NIC 951234567V"
    result = mask(text, detect_regex(text))
    assert result.masked_text == "call [PHONE_1] or [PHONE_2], NIC [NIC_1]"
    assert result.placeholder_manifest == ["[PHONE_1]", "[PHONE_2]", "[NIC_1]"]


def test_nothing_to_mask():
    result = mask("What is the dose of paracetamol?", [])
    assert result.masked_text == "What is the dose of paracetamol?"
    assert result.placeholder_manifest == []
    assert result.masking_percentage == 0.0
    assert result.entity_counts == {}


def test_empty_text():
    assert mask("", []).masking_percentage == 0.0


def test_whole_prompt_is_one_identifier():
    result = mask("0771234567", detect_regex("0771234567"))
    assert result.masked_text == "[PHONE_1]"
    assert result.masking_percentage == 100.0


def test_internal_ids_stay_visible():
    text = "Patient P0012, phone 0771234567, doctor D003"
    result = mask(text, detect_regex(text))
    assert result.masked_text == "Patient P0012, phone [PHONE_1], doctor D003"


def test_overlapping_spans_are_masked_once():
    text = "MRN: 0771234567"
    result = mask(text, detect_regex(text))  # MRN and PHONE cover the same digits
    assert result.masked_text == "MRN: [MRN_1]" or result.masked_text == "MRN: [PHONE_1]"
    assert len(result.placeholder_manifest) == 1
    assert result.masking_percentage == round(100 * 10 / len(text), 1)


def test_manifest_and_result_hold_no_values():
    text = "NIC 951234567V, mail nimal@gmail.com"
    result = mask(text, detect_regex(text))
    assert "951234567V" not in repr(result) and "nimal@gmail.com" not in repr(result)


def test_disguised_values_are_masked_as_typed():
    text = "call 0 7 7 1 2 3 4 5 6 7, mail nimal[at]gmail.com"
    result = mask(text, detect_regex(text))
    assert result.masked_text == "call [PHONE_1], mail [EMAIL_1]"
