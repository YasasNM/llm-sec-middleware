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
  * hard negatives: eponymous diseases, P/D ids (IR12), lab values, doses, ward numbers

Usage (from components/c1-privacy-assistant):
    python training/scripts/build_corpus.py --notes 1200 --prompts 3600 --seed 42
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

GENERATOR_VERSION = "1.0.0"
ROOT = Path(__file__).resolve().parents[2]  # components/c1-privacy-assistant
LEX = ROOT / "data" / "lexicons"
SPLITS = ("train", "val", "test")
SPLIT_W = (0.70, 0.15, 0.15)
GROUP_W = {"sinhala": 0.75, "tamil": 0.15, "muslim": 0.10}

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


def partition_templates(templates: list[dict]):
    """Partition templates 70/15/15 per kind so the test split has unseen templates."""
    r = random.Random(20261023)
    out = {s: {"phi": [], "neg": []} for s in SPLITS}
    for kind in ("phi", "neg"):
        lst = sorted([t for t in templates if t["kind"] == kind], key=lambda t: t["id"])
        r.shuffle(lst)
        n = len(lst)
        a, b = round(n * 0.70), round(n * 0.85)
        for i, t in enumerate(lst):
            out["train" if i < a else ("val" if i < b else "test")][kind].append(t)
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
        style = rng.choice(["plain", "plain", "space", "dash", "intl", "intl_space"])
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
    digits = rng.randint(4, 8)
    num = str(rng.randint(10 ** (digits - 1), 10**digits - 1)).zfill(digits)
    return rng.choice(["MRN ", "MRN: "]) + num


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
    return f"{local}@{rng.choice(['gmail.com', 'yahoo.com', 'hotmail.com', 'sltnet.lk'])}"


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
    return {
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
    style = rng.choice(["iso", "dmy", "long", "dots"])
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
        elif part == "{MRN}":
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
    ap.add_argument("--notes", type=int, default=1200)
    ap.add_argument("--prompts", type=int, default=3600)
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
