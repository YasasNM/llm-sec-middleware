#!/usr/bin/env python3
# ruff: noqa: E501, B905
"""Build the synthetic HC-PII corpus for C1 (Privacy Assistant).

Two tracks, both fully synthetic (no real patient data anywhere):

  A. clinical_notes.jsonl      Notes in the clinician-provided history format
                               (Name, Age, Presenting complaint, PMH DM/BP, Allergies,
                               Smoking/Alcohol/Drug, Family history, Living area,
                               O/E Pallor/Cyanosed/Edema) with exact PHI spans.
  B. sensitivity_dataset.csv   Clinician-style prompts to the LLM (text, entity_type, risk)
                               plus entities_json, split, kind, template_id, note_id.
                               Same file as sensitivity_dataset.jsonl with full metadata.

Controls from the C1 blueprint (section 13):
  * split by name: every name string belongs to exactly one of train/val/test
  * split by template: test prompts use templates never seen in training
  * coverage: templates are shared out so EVERY split contains every PHI type (v1.1)
  * hard negatives: eponymous diseases, P/D ids (IR12), lab values, doses, ward numbers,
    reference numbers, and doctor and staff names (visible on purpose, never labelled)

Usage (from components/c1-privacy-assistant):
    python training/scripts/build_corpus.py --notes 2000 --prompts 8000 --seed 42
    python training/scripts/build_corpus.py --sample            # small committed samples
Standard library only.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
import re
import sys
from datetime import date, timedelta
from pathlib import Path

GENERATOR_VERSION = "1.1.0"
ROOT = Path(__file__).resolve().parents[2]  # components/c1-privacy-assistant
LEX = ROOT / "data" / "lexicons"
SPLITS = ("train", "val", "test")
SPLIT_W = (0.70, 0.15, 0.15)
GROUP_W = {"sinhala": 0.72, "tamil": 0.15, "muslim": 0.10, "burgher": 0.03}

# entity classes: explicit identifiers are always masked (K2); quasi identifiers feed re-id
EXPLICIT = {"PATIENT_NAME", "NIC", "PHONE", "MRN", "EMAIL", "DOB", "ADDRESS"}
QUASI = {"AGE", "LOCATION", "DATE"}
PRIORITY = [
    "NIC",
    "MRN",
    "PHONE",
    "EMAIL",
    "PATIENT_NAME",
    "DOB",
    "ADDRESS",
    "LOCATION",
    "AGE",
    "DATE",
]
CSV_TYPES = {
    "PATIENT_NAME",
    "MRN",
    "PHONE",
    "EMAIL",
    "NIC",
    "LOCATION",
    "ADDRESS",
    "AGE",
    "DOB",
}  # DATE kept in JSONL only

# ----------------------------------------------------------------------------- helpers


class Builder:
    """Builds text while recording exact character spans."""

    def __init__(self) -> None:
        self.parts: list[str] = []
        self.spans: list[dict] = []
        self.n = 0

    def add(self, s: str, etype: str | None = None) -> Builder:
        if etype:
            self.spans.append({"type": etype, "start": self.n, "end": self.n + len(s), "value": s})
        self.parts.append(s)
        self.n += len(s)
        return self

    def extend(self, other: Builder) -> Builder:
        for sp in other.spans:
            self.spans.append({**sp, "start": sp["start"] + self.n, "end": sp["end"] + self.n})
        t = other.text()
        self.parts.append(t)
        self.n += len(t)
        return self

    def text(self) -> str:
        return "".join(self.parts)


def wchoice(rng: random.Random, pairs):
    items, weights = zip(*pairs)
    return rng.choices(items, weights=weights, k=1)[0]


def load_lexicons():
    names = {
        k: v
        for k, v in json.loads((LEX / "names.json").read_text()).items()
        if not k.startswith("_")
    }
    places = json.loads((LEX / "places.json").read_text())
    clin = json.loads((LEX / "clinical.json").read_text())
    return names, places, clin


def build_name_pools(names: dict):
    """Assign every distinct name string to ONE split (no leakage), stratified per group."""
    r = random.Random(20261022)  # fixed: pools must not change with --seed
    assigned: dict[str, str] = {}
    pools = {s: {} for s in SPLITS}
    for g in sorted(names):
        for key in ("male", "female", "surname"):
            lst = sorted(names[g][key])
            r.shuffle(lst)
            n = len(lst)
            a, b = round(n * 0.70), round(n * 0.85)
            for i, nm in enumerate(lst):
                k = nm.split()[-1].lower()  # "de Silva" and "Silva" share one key
                if k not in assigned:
                    assigned[k] = "train" if i < a else ("val" if i < b else "test")
            for s in SPLITS:
                pools[s].setdefault(g, {})[key] = [
                    nm for nm in lst if assigned[nm.split()[-1].lower()] == s
                ]
    for s in SPLITS:
        for g in pools[s]:
            for key, lst in pools[s][g].items():
                assert lst, f"empty name pool {s}/{g}/{key}"
    return pools, assigned


# PHI kinds every split must contain (template token -> kind)
COVER_KINDS = {
    "{NAME}": "NAME", "{NAME_LOWER}": "NAME", "{NAME_UPPER}": "NAME", "{NAME2}": "NAME",
    "{NIC}": "NIC", "{PHONE}": "PHONE", "{MRN}": "MRN", "{EMAIL}": "EMAIL", "{DOB}": "DOB",
    "{ADDR}": "ADDR", "{AGE}": "AGE", "{AGE_ADJ}": "AGE", "{TOWN}": "TOWN", "{DATE}": "DATE",
}
NOTE_KINDS = {"NAME", "AGE", "TOWN", "DATE"}  # an embedded {NOTE} contains these
# Hard-negative kinds every split must also contain: ids, staff names, reference numbers
NEGATIVE_KINDS = {"{PID}": "ID", "{PID2}": "ID", "{DOCTOR}": "STAFF", "{DOCTOR2}": "STAFF",
                  "{NURSE}": "STAFF", "{LABREF}": "REF"}  # fmt: skip
LITERAL_ID = re.compile(r"\bD\d{3}\b")  # doctor ids written straight into a template


def template_kinds(tpl: dict) -> set[str]:
    """The PHI and hard-negative kinds a template produces, e.g. {"NAME", "PHONE", "STAFF"}."""
    tokens = TOKEN_RE.findall(tpl["text"])
    kinds = {COVER_KINDS[t] for t in tokens if t in COVER_KINDS}
    kinds |= {NEGATIVE_KINDS[t] for t in tokens if t in NEGATIVE_KINDS}
    if LITERAL_ID.search(tpl["text"]):
        kinds.add("ID")
    return kinds | (NOTE_KINDS if "{NOTE}" in tpl["text"] else set())


def partition_templates(templates: list[dict]):
    """Share templates out to train/val/test with no template in two splits (v1.1).

    Step 1: for each PHI kind, rarest first, make sure val, test and train each get at least
    one template that produces it, so every split can be evaluated on every PHI type.
    Step 2: hand the remaining templates to whichever split is furthest below its 70/15/15 share.
    """
    r = random.Random(20261023)
    out = {s: {"phi": [], "neg": []} for s in SPLITS}
    weight = dict(zip(SPLITS, SPLIT_W))
    for kind in ("phi", "neg"):
        left = sorted([t for t in templates if t["kind"] == kind], key=lambda t: t["id"])
        r.shuffle(left)
        # rarest kind first; ties broken by name so the split never depends on set order
        cover = sorted({k for t in left for k in template_kinds(t)},
                       key=lambda k: (sum(k in template_kinds(t) for t in left), k))
        for need in cover:
            for split in ("val", "test", "train"):
                if any(need in template_kinds(t) for t in out[split][kind]):
                    continue
                pick = next((t for t in left if need in template_kinds(t)), None)
                if pick:
                    out[split][kind].append(pick)
                    left.remove(pick)
        for t in left:
            split = min(SPLITS, key=lambda s: len(out[s][kind]) / weight[s])
            out[split][kind].append(t)
    return out


# ----------------------------------------------------------------------------- patient


def pick_dur(rng, spec):
    unit, lo, hi = spec
    n = rng.randint(lo, hi)
    u = unit[:-1] if n == 1 else unit
    return f"{n} {u}"


def prob_dm(age):
    return 0.01 if age < 30 else 0.06 if age < 45 else 0.20 if age < 60 else 0.35


def prob_htn(age):
    return (
        0.01 if age < 30 else 0.08 if age < 45 else 0.30 if age < 60 else 0.50 if age < 75 else 0.60
    )


def day_of_year_sl(d: date) -> int:
    """Sri Lankan NIC counts days as in a leap year (Feb always has 29 days)."""
    return (date(2000, d.month, d.day) - date(2000, 1, 1)).days + 1


def make_nic(rng, dob: date, sex: str) -> str:
    doy = day_of_year_sl(dob) + (500 if sex == "F" else 0)
    if dob.year <= 1999 and rng.random() < 0.85:
        return f"{dob.year % 100:02d}{doy:03d}{rng.randint(0, 999):03d}{rng.randint(0, 9)}{rng.choice('VvXx') if rng.random() < 0.1 else 'V'}"
    return f"{dob.year}{doy:03d}{rng.randint(0, 99999):05d}"


def make_phone(rng) -> str:
    if rng.random() < 0.85:
        pre = rng.choice(["070", "071", "072", "074", "075", "076", "077", "078"])
        num = f"{rng.randint(1000000, 9999999)}"
        a, b = pre[1:], num
        style = rng.choice(["plain", "plain", "space", "dash", "intl", "intl_space", "spaced"])
        if style == "spaced":  # "0 7 7 1 2 3 4 5 6 7", a common way to dodge filters
            return " ".join(pre + b)
        if style == "plain":
            return pre + b
        if style == "space":
            return f"{pre} {b[:3]} {b[3:]}"
        if style == "dash":
            return f"{pre}-{b}"
        if style == "intl":
            return f"+94{a}{b}"
        return f"+94 {a} {b[:3]} {b[3:]}"
    return (
        f"011{rng.randint(2000000, 2999999)}"
        if rng.random() < 0.5
        else f"011 {rng.randint(2000000, 2999999)}"
    )


def make_mrn(rng) -> str:
    """The MRN number only. The label ("MRN: ") is added unlabelled when rendering (v1.1)."""
    digits = rng.randint(4, 8)
    return str(rng.randint(10 ** (digits - 1), 10**digits - 1)).zfill(digits)


MRN_LABELS = ["MRN ", "MRN: ", "MRN: ", "MRN#", "MRN # "]


def make_email(rng, name: str, dob: date) -> str:
    base = re.sub(r"[^a-z]", "", name.lower().replace(" ", "."))
    toks = [t for t in re.split(r"[ .]+", name.lower()) if len(t) > 2]
    if len(toks) >= 2:
        local = rng.choice(
            [f"{toks[0]}.{toks[-1]}", f"{toks[0]}{toks[-1]}", f"{toks[0][0]}{toks[-1]}"]
        )
    else:
        local = base or "patient"
    local += rng.choice(["", str(dob.year % 100), str(rng.randint(1, 99))])
    domain = rng.choice(["gmail.com", "yahoo.com", "hotmail.com", "sltnet.lk"])
    at = rng.choice(["@"] * 11 + ["[at]"])  # sometimes disguised, as people do to dodge filters
    return f"{local}{at}{domain}"


def make_name(rng, split, pools, group, sex):
    p = pools[split][group]
    key = "male" if sex == "M" else "female"
    given, sur = rng.choice(p[key]), rng.choice(p["surname"])
    style = rng.random()
    if style < 0.18:  # initials + surname: K.D. Perera
        k = rng.choice([1, 2])
        return "".join(rng.choice(p[key])[0] + "." for _ in range(k)) + " " + sur
    if style < 0.36:  # middle given name
        mid = rng.choice(p[key])
        if mid != given:
            return f"{given} {mid} {sur}"
    return f"{given} {sur}"


def make_staff(rng, split, pools, role: str) -> str:
    """A doctor or nurse name. Staff names stay visible in C1, so they are NEVER labelled."""
    group = rng.choices(list(GROUP_W), weights=list(GROUP_W.values()), k=1)[0]
    pool = pools[split][group]
    given = rng.choice(pool[rng.choice(["male", "female"])])
    sur = rng.choice(pool["surname"])
    if role == "doctor":
        return rng.choice(
            [f"Dr. {sur}", f"Dr {sur}", f"Dr. {given} {sur}", f"Dr {given[0]}. {sur}",
             f"Prof. {sur}", f"Consultant {given} {sur}"]
        )
    return rng.choice([f"Nurse {given}", f"Sister {sur}", f"Nursing Officer {given} {sur}"])


def make_patient(
    rng, split, pools, places, clin, visit_from=date(2026, 1, 5), visit_to=date(2026, 10, 20)
):
    sc_list = clin["scenarios"]
    sc = rng.choices(sc_list, weights=[s["weight"] for s in sc_list], k=1)[0]
    sex = "M" if rng.random() < sc["male"] else "F"
    lo, hi = sc["age"]
    age = rng.randint(lo, hi)
    visit = visit_from + timedelta(days=rng.randint(0, (visit_to - visit_from).days))
    # date of birth consistent with the age on the visit date
    if age < 2 and rng.random() < 0.6:
        months = rng.randint(2, 23)
        dob = visit - timedelta(days=int(months * 30.44))
        age_months = months
    else:
        m, d = rng.randint(1, 12), rng.randint(1, 28)
        dob = date(visit.year - age, m, d)
        if dob > visit:
            dob = date(visit.year - age - 1, m, d)
        age_months = None
        age = visit.year - dob.year - ((visit.month, visit.day) < (dob.month, dob.day))
    group = rng.choices(list(GROUP_W), weights=list(GROUP_W.values()), k=1)[0]
    name = make_name(rng, split, pools, group, sex)
    towns = places["towns"]
    if group == "tamil":
        pref = [t for t in towns if t[2] in ("north", "east", "central", "west")]
    elif group == "muslim":
        pref = [t for t in towns if t[2] in ("east", "west", "central", "northwest")]
    else:
        pref = [t for t in towns if t[2] not in ("north", "east")]
    town, district, _ = rng.choice(pref if rng.random() < 0.8 else towns)
    street = rng.choice(places["streets"])
    house = f"No. {rng.randint(1, 250)}" + rng.choice(
        ["", "", "/" + str(rng.randint(1, 9)), "A", "B"]
    )
    adult = age >= 16
    group2 = rng.choices(list(GROUP_W), weights=list(GROUP_W.values()), k=1)[0]
    return {
        "name2": make_name(rng, split, pools, group2, rng.choice("MF")),  # a second patient
        "doctor": make_staff(rng, split, pools, "doctor"),
        "doctor2": make_staff(rng, split, pools, "doctor"),
        "nurse": make_staff(rng, split, pools, "nurse"),
        "scenario": sc,
        "sex": sex,
        "age": age,
        "age_months": age_months,
        "dob": dob,
        "visit": visit,
        "group": group,
        "name": name,
        "town": town,
        "district": district,
        "address": f"{house}, {street}, {town}",
        "nic": make_nic(rng, dob, sex) if adult else None,
        "phone": make_phone(rng),
        "mrn": make_mrn(rng),
        "email": make_email(rng, name, dob) if adult else None,
        "pid": f"P{rng.randint(1, 9999):04d}",
        "complaint": rng.choice(sc["complaints"]).format(dur=pick_dur(rng, sc["dur"])),
    }


def clinical_fields(rng, p, clin):
    """Past history, habits, family history and O/E for a patient (coherent with age, sex, scenario)."""
    sc, age, sex = p["scenario"], p["age"], p["sex"]
    adult = age >= 16
    dm = sc.get("force_dm", False) or (rng.random() < min(0.95, prob_dm(age) * sc["dm_mult"]))
    htn = rng.random() < min(0.95, prob_htn(age) * sc["htn_mult"])
    if age < 16:
        dm = htn = False
    f = {}
    if dm:
        yrs = rng.randint(1, max(1, min(30, age - 20)))
        f["dm"] = f"Yes, {yrs} {'year' if yrs == 1 else 'years'}, on {rng.choice(clin['dm_drugs'])}"
    else:
        f["dm"] = "No"
    if htn:
        yrs = rng.randint(1, max(1, min(25, age - 25)))
        f["bp"] = (
            f"Yes, {yrs} {'year' if yrs == 1 else 'years'}, on {rng.choice(clin['htn_drugs'])}"
        )
    else:
        f["bp"] = "No"
    # allergies
    if rng.random() < sc.get("allergy_prob", 0.20):
        f["allergies"] = wchoice(rng, [tuple(x) for x in clin["allergies"]])
    else:
        f["allergies"] = rng.choice(clin["allergy_none_forms"])
    # smoking and alcohol / drugs
    if not adult:
        f["smoking"] = "N/A"
        f["alcohol_drug"] = "N/A"
    elif sex == "M":
        cur = min(0.7, 0.25 * sc["smoke_mult"])
        ex = 0.15
        r = rng.random()
        if r < cur:
            f["smoking"] = (
                f"Current smoker, {rng.randint(3, 20)} cigarettes/day, {rng.randint(3, max(3, age - 15))} years"
            )
        elif r < cur + ex:
            f["smoking"] = f"Ex-smoker, stopped {rng.randint(1, 15)} years ago"
        else:
            f["smoking"] = rng.choice(["Never smoked", "Non smoker"])
        f["alcohol_drug"] = wchoice(
            rng,
            [
                ("No alcohol, no drug abuse", 55),
                ("Occasional alcohol (arrack or beer), no drug abuse", 30),
                ("Alcohol most days (arrack), no drug abuse", 12),
                ("Occasional alcohol, cannabis in the past", 3),
            ],
        )
    else:
        f["smoking"] = "Never smoked"
        f["alcohol_drug"] = wchoice(
            rng, [("No alcohol, no drug abuse", 97), ("Occasional alcohol, no drug abuse", 3)]
        )
    # family history
    nrel = wchoice(rng, [(0, 30), (1, 35), (2, 25), (3, 10)])
    if nrel == 0:
        f["family"] = rng.choice(["Nil", "No significant family history"])
    else:
        conds = [tuple(x) for x in clin["family_conditions"]]
        if dm:
            conds = [(c, w * (1.6 if c == "DM" else 1)) for c, w in conds]
        items, used = [], set()
        for _ in range(nrel):
            rel = wchoice(rng, [tuple(x) for x in clin["family_relations"]])
            cond = wchoice(rng, conds)
            if (rel, cond) in used:
                continue
            used.add((rel, cond))
            if cond == "Cancer":
                female_rel = rel in ("mother", "sister", "maternal grandmother")
                cond = (
                    "Cancer (breast)"
                    if female_rel
                    else rng.choice(["Cancer (prostate)", "Cancer (lung)"])
                )
            items.append(f"{rel.capitalize()}: {cond}")
        f["family"] = "; ".join(items)
    # O/E
    oe = sc["oe"]
    pallor = rng.random() < oe["pallor"]
    cyan = rng.random() < oe["cyanosed"]
    edema = rng.random() < oe["edema"]
    f["pallor"] = rng.choice(["+", "+", "mild +", "++"]) if pallor else "-"
    f["cyanosed"] = rng.choice(["+ (central)", "+ (peripheral)"]) if cyan else "-"
    if edema:
        f["edema"] = rng.choice(
            sc.get("edema_detail", ["ankle edema +", "mild ankle edema", "bilateral ankle edema +"])
        )
    else:
        f["edema"] = "-"
    return f


# ----------------------------------------------------------------------------- rendering


def fmt_date(rng, d: date) -> str:
    style = rng.choice(["iso", "dmy", "long", "dots", "full", "us"])
    if style == "full":  # 14 March 1985
        return f"{d.day} {d.strftime('%B %Y')}"
    if style == "us":  # March 14, 1985
        return f"{d.strftime('%B')} {d.day}, {d.year}"
    if style == "iso":
        return d.isoformat()
    if style == "dmy":
        return d.strftime("%d/%m/%Y")
    if style == "long":
        return f"{d.day} {d.strftime('%b %Y')}"
    return d.strftime("%d.%m.%Y")


def age_note(p) -> str:
    if p["age_months"] is not None:
        return f"{p['age_months']} months"
    return f"{p['age']} {'year' if p['age'] == 1 else 'years'}"


def age_phrase(rng, p) -> str:
    if p["age_months"] is not None:
        return rng.choice(
            [
                f"{p['age_months']}-month-old",
                f"{p['age_months']} months old",
                f"{p['age_months']} months",
            ]
        )
    a = p["age"]
    return rng.choice(
        [f"{a}-year-old", f"{a} years old", f"{a} yrs", f"{a}y", f"{a} y/o", f"{a}-year-old"]
    )


def render_note(rng, p, f) -> Builder:
    """The clinician's format, in the clinician's order."""
    b = Builder()
    b.add("Date: ").add(fmt_date(rng, p["visit"]), "DATE").add("\n")
    b.add("1) Name: ").add(p["name"], "PATIENT_NAME").add("\n")
    b.add("2) Age: ").add(age_note(p), "AGE").add("\n")
    b.add(f"3) Presenting complaint: {p['complaint']}\n")
    b.add("4) Past medical history:\n")
    b.add(f"   DM: {f['dm']}\n")
    b.add(f"   BP: {f['bp']}\n")
    b.add(f"5) Allergies: {f['allergies']}\n")
    b.add(f"6) Smoking: {f['smoking']}\n")
    b.add(f"   Alcohol / drug abuse: {f['alcohol_drug']}\n")
    b.add(f"Family history: {f['family']}\n")
    loc = p["town"] + (
        f", {p['district']}" if rng.random() < 0.4 and p["district"] != p["town"] else ""
    )
    b.add("Living area: ").add(loc, "LOCATION").add("\n")
    b.add("O/E:\n")
    b.add(f"   Pallor: {f['pallor']}\n")
    b.add(f"   Cyanosed: {f['cyanosed']}\n")
    b.add(f"   Edema: {f['edema']}")
    return b


def note_record(rid, split, p, f, b: Builder) -> dict:
    return {
        "id": rid,
        "split": split,
        "track": "clinical_note",
        "scenario": p["scenario"]["id"],
        "text": b.text(),
        "entities": b.spans,
        "fields": {
            "name": p["name"],
            "age": age_note(p),
            "presenting_complaint": p["complaint"],
            "dm": f["dm"],
            "bp": f["bp"],
            "allergies": f["allergies"],
            "smoking": f["smoking"],
            "alcohol_drug": f["alcohol_drug"],
            "family_history": f["family"],
            "pallor": f["pallor"],
            "cyanosed": f["cyanosed"],
            "edema": f["edema"],
        },
        "meta": {
            "sex": p["sex"],
            "age_years": p["age"],
            "dob": p["dob"].isoformat(),
            "visit_date": p["visit"].isoformat(),
            "name_group": p["group"],
            "district": p["district"],
        },
    }


PHI_TEMPLATES = [
    ("P01", "Summarise the history of patient {NAME}, {AGE}, from {TOWN}."),
    ("P02", "Patient {NAME} {NIC} presents with {COMPLAINT}. Suggest differential diagnoses."),
    (
        "P03",
        "Draft a referral letter for {NAME} ({AGE}), {MRN}. Presenting complaint: {COMPLAINT}.",
    ),
    (
        "P04",
        "{NAME}'s contact number is {PHONE}. Write a reminder asking the family to bring previous reports.",
    ),
    (
        "P05",
        "Write a discharge summary for {NAME}, {AGE}, living at {ADDR}. Admitted with {COMPLAINT}.",
    ),
    ("P06", "Is it safe to continue current medication for {NAME} ({NIC}) who has {COMPLAINT}?"),
    ("P07", "Email the lab results to {EMAIL} for patient {NAME}."),
    ("P08", "Review {PID} {NAME}, DOB {DOB}. Complaint: {COMPLAINT}."),
    (
        "P09",
        "Patient {NAME}, lives at {ADDR}, tel {PHONE}. Complaint: {COMPLAINT}. Which investigations should I order?",
    ),
    ("P10", "Patient is {AGE}, from {TOWN}, with {COMPLAINT}. Suggest management."),
    ("P11", "Summarise this clinic note:\n{NOTE}"),
    ("P12", "Convert this note into SOAP format:\n{NOTE}"),
    ("P13", "Patient: {NAME}\nNIC: {NIC}\nTel: {PHONE}\nComplaint: {COMPLAINT}"),
    ("P14", "Check the allergy history for {NAME} ({MRN})."),
    ("P15", "Translate the discharge instructions for {NAME} into Sinhala."),
    ("P16", "My patient {NAME} is {AGE}, from {TOWN}. Complaint: {COMPLAINT}. Any red flags?"),
    ("P17", "Reminder: {NAME} follow up on {DATE}. Call {PHONE} if absent."),
    (
        "P18",
        "Compare outcomes for {COMPLAINT} in {AGE_ADJ} patients with and without hypertension.",
    ),
    ("P19", "{NAME}, {NIC}, {ADDR}. Please prepare the insurance claim summary."),
    ("P20", "Can you explain {NAME}'s test results to the family? DOB {DOB}, {MRN}."),
    ("P21", "Patient {NAME} ({AGE}) from {TOWN}: {COMPLAINT}. Past history: {PMH}. Next steps?"),
    ("P22", "Please add to the ward list: {NAME}, {AGE}, bed 12, {MRN}."),
    ("P23", "Send an SMS to {PHONE}: the appointment for {NAME} on {DATE} is confirmed."),
    ("P24", "Who is the next of kin for {NAME}? Contact {EMAIL}."),
    ("P25", "Draft a sick leave certificate for {NAME}, {NIC}, for {COMPLAINT}."),
    ("P26", "{AGE_ADJ} patient from {TOWN} with {COMPLAINT}. Is admission needed?"),
    ("P27", "Please rewrite this note in simple English for the patient:\n{NOTE}"),
    ("P28", "Summarise {PID}: {NAME}, {AGE}, DOB {DOB}."),
    ("P29", "Update the record of {NAME} living at {ADDR}, phone {PHONE}."),
    ("P30", "Is {COMPLAINT} serious in a {AGE_ADJ} patient from {TOWN}?"),
    # v1.1: names in more places, two patients, staff names next to patients, more formats
    ("P31", "{NAME} was seen by {DOCTOR} today for {COMPLAINT}. Please draft the clinic summary."),
    ("P32", "Please book a review with {DOCTOR} for {NAME}, {MRN}."),
    ("P33", "{DOCTOR} asked me to check whether {NAME} (DOB {DOB}) needs a repeat HbA1c."),
    ("P34", "Two patients for today: {NAME}, {AGE}, and {NAME2}. Who should be seen first?"),
    ("P35", "Compare the BP trends of {NAME} and {NAME2} over the last month."),
    ("P36", "{NAME}, date of birth {DOB}, NIC {NIC}. Is the NIC consistent with the date of birth?"),
    ("P37", "Patient born on {DOB} in {TOWN}, {MRN}. Which vaccines are due?"),
    ("P38", "D.O.B. {DOB}, {NAME}. Please calculate the exact age."),
    ("P39", "pls check the reports of {NAME_LOWER} and call {PHONE}"),
    ("P40", "URGENT: {NAME_UPPER} {NIC} transferred from {TOWN}."),
    ("P41", "Send the discharge letter to {EMAIL} and copy {DOCTOR}."),
    ("P42", "{NAME} can be reached at {PHONE} or {EMAIL}."),
    ("P43", "Email from {EMAIL}: my father {NAME} still has {COMPLAINT}. What should we do?"),
    ("P44", "Write a letter to {NAME} at {ADDR} about the missed appointment on {DATE}."),
    ("P45", "Home visit needed: {ADDR}. Patient {NAME}, {AGE}, contact {PHONE}."),
    ("P46", "Insurance form: Name {NAME}; NIC {NIC}; Address {ADDR}; Tel {PHONE}."),
    ("P47", "Lab request for {MRN} ({NAME}): FBC and lipid profile. Ordered by {DOCTOR}."),
    ("P48", "Which ward is {NAME} in? {MRN}."),
    ("P49", "Does {NAME}'s sugar control look acceptable? {MRN}, {AGE}."),
    ("P50", "As discussed with {NURSE}, {NAME} needs a dressing on alternate days."),
    ("P51", "{NAME}\n{AGE}\n{TOWN}\n{COMPLAINT}"),
    ("P52", "Name: {NAME} | {MRN} | DOB: {DOB}"),
    ("P53", "Follow up for {NAME} on {DATE} with {DOCTOR}; remind via {PHONE}."),
    ("P54", "Child {NAME}, {AGE}, mother's phone {PHONE}. Fever for two days, what dose of paracetamol?"),
    ("P55", "Refer {NAME} ({NIC}) to the teaching hospital for a cardiology opinion."),
    ("P56", "{NAME} ({AGE}, {TOWN}) has {COMPLAINT}; past history {PMH}. Plan?"),
    ("P57", "Message for {NAME}: your results are ready, please call {PHONE}."),
    ("P58", "Recheck {PID}: the patient {NAME} gave a new number, {PHONE}."),
    ("P59", "Can you summarise the notes for {NAME}, born {DOB}?"),
    ("P60", "Patient details: {NAME}, {AGE}, {NIC}, {ADDR}."),
    ("P61", "{NAME2} is related to {NAME}. Both have {COMPLAINT}. Is it infectious?"),
    ("P62", "Note from {DOCTOR}: {NAME} to continue {DRUG}, review in two weeks."),
    ("P63", "Ward round: bed 4 {NAME}, bed 7 {NAME2}. Any interactions with {DRUG}?"),
    ("P64", "email {EMAIL} the scan report of {NAME_LOWER}"),
    ("P65", "Patient ID {PID}, name {NAME}, NIC {NIC}: summarise the last admission."),
    ("P66", "Create a portal account for {NAME} with {EMAIL}."),
    ("P67", "Reply to {EMAIL} confirming {NAME}'s appointment on {DATE}."),
    ("P68", "Date of birth {DOB}. {NAME} from {ADDR} needs a medical certificate."),
    # v1.1: names next to punctuation (brackets, quotes, slashes, line breaks, possessives)
    ("P69", "Bed 3 (patient {NAME}) needs a review of {DRUG}."),
    ("P70", "The family of \"{NAME}\" asked about visiting hours; reply to {PHONE}."),
    ("P71", "{NAME}/{MRN}: please add the CT report to the file."),
    ("P72", "Discharged today:\n- {NAME}\n- {NAME2}"),
    ("P73", "Is {NAME}'s NIC {NIC} valid? The clerk thinks a digit is missing."),
    ("P74", "Swab result for [{NAME}] came back negative; inform {DOCTOR}."),
    ("P75", "Patient ID {PID}, {NAME} ({AGE}), {TOWN}: summarise the admission."),
    ("P76", "Ask {NAME}'s daughter to email {EMAIL} with the old prescriptions."),
    ("P77", "{NAME_LOWER} ({AGE}) from {TOWN}, wants a second opinion"),
    ("P78", "Re: {NAME}; DOB {DOB}; {MRN}. Request for medical records."),
    ("P79", "Cancel the appointment of {NAME}, tel. {PHONE}, booked with {DOCTOR}."),
    ("P80", "{NAME_UPPER}, {AGE}, {ADDR}. FOR URGENT REVIEW."),
]
NEG_TEMPLATES = [
    ("N01", "Summarise patient {PID}."),
    ("N02", "List cardiology doctors D004 and D011."),
    ("N03", "What is the usual starting dose of {DRUG} in adults?"),
    ("N04", "Explain the difference between Parkinson disease and Addison disease."),
    ("N05", "How is a flare of Crohn disease managed?"),
    ("N06", "What is the screening schedule for children with Down syndrome?"),
    ("N07", "Interpret: Hb 9.8 g/dL, WBC 12,400, CRP 48 mg/L, platelets 150,000."),
    ("N08", "BP 140/90, pulse 88, SpO2 96%. What is the next step?"),
    ("N09", "Which wards have free beds today? Ward 3 and Ward 7."),
    ("N10", "What are the red flags for {COMPLAINT_GENERIC}?"),
    ("N11", "How is {DRUG} adjusted in renal impairment?"),
    ("N12", "Draft a poster reminding people about the diabetes clinic on Tuesdays."),
    ("N13", "Summarise the national guideline on dengue fluid management."),
    ("N14", "Cushing syndrome versus Cushing disease: key differences."),
    ("N15", "Staging of Hodgkin lymphoma in simple words."),
    ("N16", "Show the appointments for {PID} next week."),
    ("N17", "Dose of {DRUG} for a patient weighing 62 kg."),
    ("N18", "Cut-off values: HbA1c 6.5% and fasting glucose 126 mg/dL. What do they mean?"),
    ("N19", "What are the side effects of {DRUG}?"),
    ("N20", "Explain Graves disease and Hashimoto thyroiditis to a new intern."),
    ("N21", "List the investigations for {COMPLAINT_GENERIC} in adults."),
    ("N22", "Review {PID} and {PID2}: compare their last two visits."),
    ("N23", "What is the Alzheimer disease staging used in clinic?"),
    ("N24", "Translate this instruction into Sinhala: take the tablet after meals."),
    ("N25", "Which tests are done at the Colombo and Kandy hospitals for dengue?"),
    # v1.1: staff names (visible on purpose, never labelled), reference numbers, eponyms
    ("N26", "Please ask {DOCTOR} to review the ward 5 roster."),
    ("N27", "{DOCTOR} will take the diabetes clinic on Thursday."),
    ("N28", "Who is on call tonight, {DOCTOR} or {DOCTOR2}?"),
    ("N29", "{NURSE} reported that the BP machine in ward 2 is faulty."),
    ("N30", "Lab reference {LABREF}: haemolysed sample, please repeat."),
    ("N31", "Pharmacy order {LABREF}: 200 tablets of {DRUG}."),
    ("N32", "What is the difference between Wilson disease and Menkes disease?"),
    ("N33", "Is Bell palsy the same as a stroke?"),
    ("N34", "Follow-up echocardiograms in Kawasaki disease: how often?"),
    ("N35", "Explain the progression of Parkinson disease to a family."),
    ("N36", "Clinic hours: Monday 8.00 to 12.00, Wednesday 14.00 to 17.00."),
    ("N37", "Write a thank you note to {DOCTOR} for the lecture on sepsis."),
    ("N38", "{DOCTOR} recommends a lower HbA1c target for frail patients. Is this evidence based?"),
    ("N39", "Summarise the article by {DOCTOR} on dengue in children."),
    ("N40", "Room 12 needs cleaning before the next patient."),
    ("N41", "How do I document an allergy to {DRUG} correctly?"),
    ("N42", "What dose of {DRUG} is used for a 30 kg child?"),
    ("N43", "Is the reference range for TSH 0.4 to 4.0 mIU/L?"),
    ("N44", "Compare {PID} with {PID2} for medication adherence."),
    ("N45", "Hand over to {NURSE}: {PID} needs four hourly observations."),
    # v1.1: ids and staff in places where a patient name would usually sit
    ("N46", "Patient ID {PID} has been moved to ward 6."),
    ("N47", "Doctor ID D017 is not on the rota; please check with {DOCTOR}."),
    ("N48", "Patient {PID} (see {LABREF}) needs a repeat potassium."),
    ("N49", "Reply to {NURSE}'s message about the oxygen supply."),
    ("N50", "Bed 9 ({PID}) is ready for discharge after {DOCTOR}'s round."),
]
TEMPLATES = [{"id": i, "kind": "phi", "text": t} for i, t in PHI_TEMPLATES] + [
    {"id": i, "kind": "neg", "text": t} for i, t in NEG_TEMPLATES
]
DRUGS = [
    "metformin",
    "amlodipine",
    "enalapril",
    "atenolol",
    "paracetamol",
    "amoxicillin",
    "omeprazole",
    "salbutamol",
    "furosemide",
    "gliclazide",
    "losartan",
    "prednisolone",
]
GENERIC_COMPLAINTS = [
    "chest pain",
    "persistent cough",
    "breathlessness",
    "abdominal pain",
    "high fever",
    "leg swelling",
]
TOKEN_RE = re.compile(r"(\{[A-Z0-9_]+\})")
NEEDS_ADULT = {"{NIC}", "{EMAIL}"}


def render_prompt(rng, tpl, p, f, clin) -> tuple[Builder, str | None]:
    """Fill a template; returns the builder and the note id marker if a note was embedded."""
    b = Builder()
    title = ""
    if rng.random() < 0.15:
        if p["age"] < 16:
            title = "Master " if p["sex"] == "M" else "Miss "
        else:
            title = "Mr. " if p["sex"] == "M" else rng.choice(["Mrs. ", "Ms. "])
    note_used = None
    for part in TOKEN_RE.split(tpl["text"]):
        if not TOKEN_RE.fullmatch(part):
            b.add(part)
            continue
        if part == "{NAME}":
            if title:
                b.add(title)
            b.add(p["name"], "PATIENT_NAME")
        elif part == "{NAME_LOWER}":  # typed quickly, all lowercase
            b.add(p["name"].lower(), "PATIENT_NAME")
        elif part == "{NAME_UPPER}":  # shouted, all capitals
            b.add(p["name"].upper(), "PATIENT_NAME")
        elif part == "{NAME2}":  # a second patient in the same prompt
            b.add(p["name2"], "PATIENT_NAME")
        elif part in ("{DOCTOR}", "{DOCTOR2}", "{NURSE}"):  # staff: visible, never labelled
            b.add(p[part.strip("{}").lower()])
        elif part == "{LABREF}":  # reference numbers are not identifiers
            b.add(f"{rng.choice(['LAB', 'RX', 'ORD'])}-{rng.randint(10000, 99999)}")
        elif part == "{AGE}":
            b.add(age_phrase(rng, p), "AGE")
        elif part == "{AGE_ADJ}":
            b.add(
                f"{p['age_months']}-month-old"
                if p["age_months"] is not None
                else f"{p['age']}-year-old",
                "AGE",
            )
        elif part == "{TOWN}":
            b.add(p["town"], "LOCATION")
        elif part == "{ADDR}":
            b.add(p["address"], "ADDRESS")
        elif part == "{NIC}":
            b.add(p["nic"], "NIC")
        elif part == "{PHONE}":
            b.add(p["phone"], "PHONE")
        elif part == "{MRN}":  # label visible, number labelled (as C1 masks it)
            b.add(rng.choice(MRN_LABELS))
            b.add(p["mrn"], "MRN")
        elif part == "{EMAIL}":
            b.add(p["email"], "EMAIL")
        elif part == "{DOB}":
            b.add(fmt_date(rng, p["dob"]), "DOB")
        elif part == "{DATE}":
            b.add(fmt_date(rng, p["visit"]), "DATE")
        elif part == "{PID}":
            b.add(p["pid"])
        elif part == "{PID2}":
            b.add(f"P{rng.randint(1, 9999):04d}")
        elif part == "{COMPLAINT}":
            b.add(p["complaint"][0].lower() + p["complaint"][1:])
        elif part == "{PMH}":
            dm, htn = f["dm"] != "No", f["bp"] != "No"
            b.add(
                "diabetes and hypertension"
                if dm and htn
                else "diabetes"
                if dm
                else "hypertension"
                if htn
                else "no chronic illness"
            )
        elif part == "{COMPLAINT_GENERIC}":
            b.add(rng.choice(GENERIC_COMPLAINTS))
        elif part == "{DRUG}":
            b.add(rng.choice(DRUGS))
        elif part == "{NOTE}":
            b.extend(render_note(rng, p, f))
            note_used = "embedded"
        else:
            raise ValueError(f"unknown token {part}")
    return b, note_used


def risk_of(spans) -> str:
    types = {s["type"] for s in spans}
    if types & EXPLICIT:
        return "high"
    if types & QUASI:
        return "medium"
    return "low"


def primary_type(spans) -> str:
    types = {s["type"] for s in spans if s["type"] in CSV_TYPES}
    for t in PRIORITY:
        if t in types:
            return t
    return "NONE"


# ----------------------------------------------------------------------------- build


def build(n_notes: int, n_prompts: int, seed: int):
    names, places, clin = load_lexicons()
    pools, _ = build_name_pools(names)
    tpl_parts = partition_templates(TEMPLATES)
    rng = random.Random(seed)

    notes, prompts = [], []
    counters = {s: 0 for s in SPLITS}
    for _ in range(n_notes):
        split = rng.choices(SPLITS, weights=SPLIT_W, k=1)[0]
        p = make_patient(rng, split, pools, places, clin)
        f = clinical_fields(rng, p, clin)
        b = render_note(rng, p, f)
        counters[split] += 1
        notes.append(note_record(f"hcpii-note-{split}-{counters[split]:05d}", split, p, f, b))

    counters = {s: 0 for s in SPLITS}
    for _ in range(n_prompts):
        split = rng.choices(SPLITS, weights=SPLIT_W, k=1)[0]
        kind = "neg" if rng.random() < 0.25 else "phi"
        p = make_patient(rng, split, pools, places, clin)
        f = clinical_fields(rng, p, clin)
        for _try in range(20):
            tpl = rng.choice(tpl_parts[split][kind])
            if p["age"] < 16 and any(tok in tpl["text"] for tok in NEEDS_ADULT):
                continue
            break
        else:
            raise RuntimeError("no compatible template")
        b, note = render_prompt(rng, tpl, p, f, clin)
        counters[split] += 1
        spans = sorted(b.spans, key=lambda s: s["start"])
        prompts.append(
            {
                "id": f"hcpii-prompt-{split}-{counters[split]:05d}",
                "split": split,
                "track": "prompt",
                "kind": "hard_negative_or_no_phi" if kind == "neg" else "phi",
                "template_id": tpl["id"],
                "text": b.text(),
                "entities": spans,
                "entity_type": primary_type(spans),
                "risk": risk_of([s for s in spans if s["type"] in CSV_TYPES]),
                "note_embedded": note is not None,
                "meta": {
                    "sex": p["sex"],
                    "age_years": p["age"],
                    "dob": p["dob"].isoformat(),
                    "nic": p["nic"],
                    "name_group": p["group"],
                },
            }
        )
    return notes, prompts


def write_outputs(notes, prompts, out: Path):
    out.mkdir(parents=True, exist_ok=True)
    paths = {}
    p = out / "clinical_notes.jsonl"
    p.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in notes), encoding="utf-8")
    paths["clinical_notes.jsonl"] = p
    p = out / "sensitivity_dataset.jsonl"
    p.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in prompts), encoding="utf-8"
    )
    paths["sensitivity_dataset.jsonl"] = p
    p = out / "sensitivity_dataset.csv"
    with p.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(
            ["text", "entity_type", "risk", "entities_json", "split", "kind", "template_id", "id"]
        )
        for r in prompts:
            ents = [e for e in r["entities"] if e["type"] in CSV_TYPES]
            w.writerow(
                [
                    r["text"],
                    r["entity_type"],
                    r["risk"],
                    json.dumps(ents, ensure_ascii=False),
                    r["split"],
                    r["kind"],
                    r["template_id"],
                    r["id"],
                ]
            )
    paths["sensitivity_dataset.csv"] = p
    return paths


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def count_rows(path: Path) -> int:
    if path.suffix == ".csv":
        with path.open(newline="", encoding="utf-8") as fh:
            return sum(1 for _ in csv.reader(fh)) - 1  # minus header; quoted newlines handled
    return sum(1 for _ in path.open(encoding="utf-8"))


def write_samples(notes, prompts, samples: Path, review: Path):
    samples.mkdir(parents=True, exist_ok=True)
    review.mkdir(parents=True, exist_ok=True)
    pick = lambda rows, k: [r for s in SPLITS for r in [x for x in rows if x["split"] == s][:k]]  # noqa: E731
    ns = pick(notes, 10)
    ps = pick(prompts, 20)
    (samples / "clinical_notes_sample.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in ns), encoding="utf-8"
    )
    with (samples / "sensitivity_sample.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(
            ["text", "entity_type", "risk", "entities_json", "split", "kind", "template_id", "id"]
        )
        for r in ps:
            ents = [e for e in r["entities"] if e["type"] in CSV_TYPES]
            w.writerow(
                [
                    r["text"],
                    r["entity_type"],
                    r["risk"],
                    json.dumps(ents, ensure_ascii=False),
                    r["split"],
                    r["kind"],
                    r["template_id"],
                    r["id"],
                ]
            )
    md = [
        "# Sample synthetic clinical notes (doctor's format)",
        "",
        "All people, numbers and places-with-people are synthetic. Generated by `training/scripts/build_corpus.py`.",
        "",
    ]
    for r in ns[:6]:
        md += [f"## {r['id']} ({r['scenario']})", "", "```text", r["text"], "```", ""]
    (samples / "notes_rendered_sample.md").write_text("\n".join(md), encoding="utf-8")
    # clinician review sheet: 60 notes spread over scenarios, blank columns for the doctor
    r = random.Random(7)
    by_sc: dict[str, list] = {}
    for x in notes:
        by_sc.setdefault(x["scenario"], []).append(x)
    chosen = []
    keys = sorted(by_sc)
    i = 0
    while len(chosen) < min(60, len(notes)):
        k = keys[i % len(keys)]
        if by_sc[k]:
            chosen.append(by_sc[k].pop(r.randrange(len(by_sc[k]))))
        i += 1
        if i > 10000:
            break
    with (review / "clinician_review_sheet.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(
            [
                "id",
                "scenario",
                "note_text",
                "layout_matches_real_form (Y/N)",
                "clinically_plausible (Y/N)",
                "what_to_fix",
            ]
        )
        for x in chosen:
            w.writerow([x["id"], x["scenario"], x["text"], "", "", ""])


def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--notes", type=int, default=2000)
    ap.add_argument("--prompts", type=int, default=8000)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", type=Path, default=ROOT / "data" / "processed")
    ap.add_argument(
        "--sample",
        action="store_true",
        help="also write data/samples and the clinician review sheet",
    )
    ap.add_argument(
        "--manifest", action="store_true", help="write data/manifest.json with SHA-256 hashes"
    )
    a = ap.parse_args(argv)

    notes, prompts = build(a.notes, a.prompts, a.seed)
    paths = write_outputs(notes, prompts, a.out)
    print(f"notes={len(notes)} prompts={len(prompts)} seed={a.seed} -> {a.out}")
    if a.sample:
        write_samples(notes, prompts, ROOT / "data" / "samples", ROOT / "data" / "review")
        print("samples and review sheet written")
    if a.manifest:
        manifest = {
            "dataset": "HC-PII-Corpus (synthetic, Sri Lankan formats)",
            "generator": f"training/scripts/build_corpus.py v{GENERATOR_VERSION}",
            "command": f"python training/scripts/build_corpus.py --notes {a.notes} --prompts {a.prompts} --seed {a.seed}",
            "seed": a.seed,
            "python": sys.version.split()[0],
            "files": {
                n: {"sha256": sha256(p), "bytes": p.stat().st_size, "rows": count_rows(p)}
                for n, p in paths.items()
            },
            "location": "Regenerate with the command above (deterministic), or fetch from the team Drive.",
            "note": "All data is synthetic. Not collected from any hospital or patient.",
        }
        (ROOT / "data" / "manifest.json").write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
        )
        print("manifest written")


if __name__ == "__main__":
    main()
