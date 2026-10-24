import pytest

from c3.leakcheck import LeakChecker, normalise_text

LC = LeakChecker()
MANIFEST = ["[PATIENT_NAME_1]", "[NIC_1]"]


def kinds(text, manifest=MANIFEST):
    return [e.type for e in LC.check(text, manifest).entities]


@pytest.mark.parametrize("text,kind", [
    ("NIC 901234567V", "NIC"),
    ("NIC 901234567x", "NIC"),
    ("id 198512345678 here", "NIC"),
    ("id 200012345678.", "NIC"),
    ("call 0771234567", "PHONE"),
    ("call 077 123 4567", "PHONE"),
    ("call 077-123-4567", "PHONE"),
    ("call +94771234567", "PHONE"),
    ("call +94 77 123 4567", "PHONE"),
    ("landline 0112345678", "PHONE"),
    ("write to nimal.perera@hospital.lk please", "EMAIL"),
    ("[PATIENT_NAME_9] was admitted", "UNKNOWN_PLACEHOLDER"),
])
def test_identifiers_are_found(text, kind):
    assert kinds(text) == [kind]


@pytest.mark.parametrize("text", [
    "Give 5000 mg and 20 ml daily in ward 12.",
    "Order 123456789012345 shipped.",
    "Patient P0012 and doctor D003 agree.",
    "[PATIENT_NAME_1] and [NIC_1] are placeholders from the manifest.",
    "Admitted on 2026-10-20 at 10:30, bed 14, room 305.",
    "Years 1990 2024 2030 are not identifiers.",
    "Take 250 mg every 8 hours for 7 days, maximum 4000 mg per day.",
    "The ratio was 12345678 to 1.",
    "Version 3.2.1 of the guideline.",
])
def test_benign_numbers_and_allowed_tokens_are_clean(text):
    assert kinds(text) == []


def test_results_hold_offsets_and_never_values():
    text = "Contact 0771234567 or mail a@b.lk now."
    r = LC.check(text, [])
    assert r.count == 2 and r.detected
    j = r.to_json()
    assert j["status"] == "leak" and all(set(e) == {"type", "start", "end"} for e in j["entities"])
    assert "0771234567" not in str(j) and "a@b.lk" not in str(j)
    e = r.entities[0]
    assert text[e.start:e.end] == "0771234567"


def test_a_12_digit_nic_is_not_also_counted_as_a_phone():
    r = LC.check("NIC 197812345678", [])
    assert r.count == 1


def test_clean_result_shape():
    assert LC.check("nothing here", []).to_json() == {"status": "clean", "entities": []}


def test_extra_detector_hook_respects_manifest_and_internal_ids():
    # a stand-in for Presidio: flags any capitalised word as a NAME
    def names(text):
        import re

        return [("NAME", m.start(), m.end()) for m in re.finditer(r"\b[A-Z][a-z]+\b", text)]

    lc = LeakChecker([names])
    r = lc.check("Perera met Silva", [])
    assert [e.type for e in r.entities] == ["NAME", "NAME"]
    assert lc.check("Perera met Silva", ["Perera", "Silva"]).count == 0


def test_generated_text_check():
    assert LC.check_generated(["fine reason", None, "NIC 901234567V in reason"], []) is True
    assert LC.check_generated(["The response breaks this clause: prescribing."], []) is False


def test_normalise_removes_control_characters_but_keeps_layout():
    assert normalise_text("a\x00b\x07c\nd\te") == "abc\nd\te"
