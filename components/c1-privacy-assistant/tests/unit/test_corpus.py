# ruff: noqa: E501, B905
"""Unit tests for the synthetic corpus generator (run from components/c1-privacy-assistant)."""

import sys
from datetime import date
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "training" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import build_corpus as bc  # noqa: E402
import validate_corpus as vc  # noqa: E402


@pytest.fixture(scope="module")
def corpus():
    return bc.build(n_notes=300, n_prompts=900, seed=7)


def test_deterministic():
    a = bc.build(40, 80, seed=1)
    b = bc.build(40, 80, seed=1)
    assert a == b
    c = bc.build(40, 80, seed=2)
    assert a != c


def test_all_validator_checks_pass(corpus):
    notes, prompts = corpus
    assert vc.validate(notes, prompts) == []


def test_spans_are_exact(corpus):
    for r in corpus[0] + corpus[1]:
        for s in r["entities"]:
            assert r["text"][s["start"] : s["end"]] == s["value"]


def test_note_follows_doctor_format(corpus):
    labels = [
        "1) Name:",
        "2) Age:",
        "3) Presenting complaint:",
        "4) Past medical history:",
        "DM:",
        "BP:",
        "5) Allergies:",
        "6) Smoking:",
        "Alcohol / drug abuse:",
        "Family history:",
        "Living area:",
        "O/E:",
        "Pallor:",
        "Cyanosed:",
        "Edema:",
    ]
    for r in corpus[0]:
        pos = -1
        for lab in labels:
            i = r["text"].find(lab)
            assert i > pos, (r["id"], lab)
            pos = i


def test_nic_matches_dob_and_sex():
    # 14 March 1985, female. NIC day numbers use a leap-year calendar: 31 + 29 + 14 = 74, plus 500 = 574
    import random

    nic = bc.make_nic(random.Random(1), date(1985, 3, 14), "F")
    assert nic.startswith("85574") or nic.startswith("1985574")
    assert vc.check_nic(nic, date(1985, 3, 14), "F") is None
    assert vc.check_nic(nic, date(1985, 3, 14), "M") is not None


def test_no_pd_ids_are_entities(corpus):
    for r in corpus[1]:
        for s in r["entities"]:
            assert not vc.ID_RE.fullmatch(s["value"])


def test_hard_negatives_have_no_entities(corpus):
    neg = [r for r in corpus[1] if r["kind"] == "hard_negative_or_no_phi"]
    assert neg and all(
        r["entities"] == [] and r["entity_type"] == "NONE" and r["risk"] == "low" for r in neg
    )


def test_split_by_name_and_template(corpus):
    names = {"train": set(), "val": set(), "test": set()}
    tpls = {"train": set(), "val": set(), "test": set()}
    for r in corpus[0] + corpus[1]:
        for s in r["entities"]:
            if s["type"] == "PATIENT_NAME":
                names[r["split"]] |= vc.name_tokens(s["value"])
    for r in corpus[1]:
        tpls[r["split"]].add(r["template_id"])
    assert (
        not names["train"] & names["test"]
        and not names["train"] & names["val"]
        and not names["val"] & names["test"]
    )
    assert (
        not tpls["train"] & tpls["test"]
        and not tpls["train"] & tpls["val"]
        and not tpls["val"] & tpls["test"]
    )


def test_no_real_clinic_details(corpus):
    blob = " ".join(r["text"] for r in corpus[0] + corpus[1]).lower()
    for bad in vc.FORBIDDEN_STRINGS:
        assert bad not in blob


def test_children_have_no_adult_fields(corpus):
    for r in corpus[0]:
        if r["meta"]["age_years"] < 16:
            assert (
                r["fields"]["smoking"] == "N/A"
                and r["fields"]["dm"] == "No"
                and r["fields"]["bp"] == "No"
            )


# v1.1: every split must hold every PHI type, so each one can be evaluated everywhere
def test_every_split_has_every_phi_type(corpus):
    assert vc.check_coverage(corpus[1], min_spans=5) == []


# v1.1: doctor and staff names appear but stay unlabelled (C1 keeps them visible)
def test_staff_names_appear_and_are_never_labelled(corpus):
    prompts = corpus[1]
    assert any(vc.STAFF_TITLE_RE.search(r["text"]) for r in prompts)
    assert vc.check_staff_unlabelled(prompts) == []


# v1.1: the MRN span is the number only; the "MRN" label stays visible, as C1 masks it
def test_mrn_span_is_the_number_only(corpus):
    mrns = [s["value"] for r in corpus[1] for s in r["entities"] if s["type"] == "MRN"]
    assert mrns and all(value.isdigit() for value in mrns)


# v1.1: some prompts name two different patients
def test_some_prompts_have_two_patients(corpus):
    def two_names(r):
        names = {s["value"] for s in r["entities"] if s["type"] == "PATIENT_NAME"}
        return len(names) >= 2

    assert any(two_names(r) for r in corpus[1])


# v1.1: names also appear all lowercase or all capitals
def test_names_appear_in_lower_and_upper_case(corpus):
    names = [s["value"] for r in corpus[1] for s in r["entities"] if s["type"] == "PATIENT_NAME"]
    assert any(n.islower() for n in names) and any(n.isupper() for n in names)


# v1.1: the templates of each split cover every PHI kind and every hard-negative kind
def test_template_split_covers_every_kind():
    parts = bc.partition_templates(bc.TEMPLATES)
    for kind in ("phi", "neg"):
        every = {k for t in bc.TEMPLATES if t["kind"] == kind for k in bc.template_kinds(t)}
        for split in bc.SPLITS:
            kinds = {k for t in parts[split][kind] for k in bc.template_kinds(t)}
            assert kinds == every, (kind, split)
