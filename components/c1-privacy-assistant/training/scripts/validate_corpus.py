#!/usr/bin/env python3
# ruff: noqa: E501, B905
"""Validate the synthetic HC-PII corpus. Exit code 1 if any check fails.

python training/scripts/validate_corpus.py                 # checks data/processed
python training/scripts/validate_corpus.py --dir some/dir
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ALLOWED = {
    "PATIENT_NAME",
    "MRN",
    "PHONE",
    "EMAIL",
    "NIC",
    "LOCATION",
    "ADDRESS",
    "AGE",
    "DOB",
    "DATE",
}
FORBIDDEN_STRINGS = ["nimro", "udumulla", "0112078909", "0112-078909", "0112 078909"]
ID_RE = re.compile(r"\b[PD]\d{3,4}\b")
PHONE_RE = re.compile(
    r"(?<![\d+])(?:\+94|0)[\s\-]?\d{2}[\s\-]?\d{3}[\s\-]?\d{4}(?!\d)"
)  # digit boundaries: a 12-digit NIC is not a phone
NIC_RE = re.compile(r"\b(?:\d{9}[VvXx]|\d{12})\b")
# v1.1: every split must contain every one of these types in its prompts
COVERAGE_TYPES = ["PATIENT_NAME", "NIC", "PHONE", "MRN", "EMAIL", "DOB", "ADDRESS", "AGE", "LOCATION", "DATE"]
# v1.1: staff names stay visible in C1, so the word after these titles is never a patient name
STAFF_TITLE_RE = re.compile(r"\b(?:Dr\.?|Prof\.|Consultant|Nurse|Sister|Nursing Officer) ")


def load_jsonl(path: Path):
    return [json.loads(line) for line in path.open(encoding="utf-8")]


def name_tokens(value: str):
    return {t.lower() for t in re.split(r"[\s.]+", value) if len(t) >= 3 and t.isalpha()}


def doy(d: date) -> int:
    return (date(2000, d.month, d.day) - date(2000, 1, 1)).days + 1


def check_nic(nic: str, dob: date, sex: str):
    if re.fullmatch(r"\d{9}[VvXx]", nic):
        yy, rest = int(nic[:2]), int(nic[2:5])
        year = 1900 + yy
    elif re.fullmatch(r"\d{12}", nic):
        year, rest = int(nic[:4]), int(nic[4:7])
    else:
        return "bad NIC shape"
    want = doy(dob) + (500 if sex == "F" else 0)
    if year != dob.year or rest != want:
        return f"NIC {nic} does not match DOB {dob} / sex {sex}"
    return None


def check_coverage(prompts, min_spans: int):
    """Every split must hold at least `min_spans` prompt spans of every PHI type."""
    errs = []
    counts = {s: Counter() for s in ("train", "val", "test")}
    for r in prompts:
        counts[r["split"]].update(e["type"] for e in r["entities"])
    for split, found in counts.items():
        for t in COVERAGE_TYPES:
            if found[t] < min_spans:
                errs.append(f"coverage: {split} has {found[t]} {t} spans (need {min_spans})")
    return errs


def check_staff_unlabelled(prompts):
    """A name right after Dr, Prof, Nurse, Sister or Consultant must never be labelled."""
    errs = []
    for r in prompts:
        starts = {e["start"] for e in r["entities"] if e["type"] == "PATIENT_NAME"}
        for m in STAFF_TITLE_RE.finditer(r["text"]):
            if m.end() in starts:
                errs.append(f"{r['id']}: staff name labelled as PATIENT_NAME")
    return errs


def validate(notes, prompts, min_spans: int = 5):
    errs = check_coverage(prompts, min_spans) + check_staff_unlabelled(prompts)
    split_tokens = defaultdict(set)
    for r in notes + prompts:
        text, spans = r["text"], sorted(r["entities"], key=lambda s: s["start"])
        for s in spans:
            if s["type"] not in ALLOWED:
                errs.append(f"{r['id']}: bad type {s['type']}")
            if text[s["start"] : s["end"]] != s["value"]:
                errs.append(f"{r['id']}: span text mismatch {s}")
            if s["type"] == "PATIENT_NAME":
                split_tokens[r["split"]] |= name_tokens(s["value"])
            if ID_RE.fullmatch(s["value"]):
                errs.append(f"{r['id']}: P/D id labelled as entity (IR12)")
        for a, b in zip(spans, spans[1:]):
            if a["end"] > b["start"]:
                errs.append(f"{r['id']}: overlapping spans")
        low = text.lower()
        for bad in FORBIDDEN_STRINGS:
            if bad in low:
                errs.append(f"{r['id']}: forbidden real-world string {bad}")
        # every phone/NIC looking string must be labelled (no unlabelled identifiers)
        for m in PHONE_RE.finditer(text):
            if not any(
                s["type"] in ("PHONE", "MRN") and s["start"] <= m.start() and m.end() <= s["end"]
                for s in spans
            ):
                errs.append(f"{r['id']}: unlabelled phone-like string {m.group()}")
        for m in NIC_RE.finditer(text):
            if not any(
                s["type"] in ("NIC", "PHONE", "MRN")
                and s["start"] <= m.start()
                and m.end() <= s["end"]
                for s in spans
            ):
                errs.append(f"{r['id']}: unlabelled NIC-like string {m.group()}")
    # split by name
    for a in ("train", "val", "test"):
        for b in ("train", "val", "test"):
            if a < b and split_tokens[a] & split_tokens[b]:
                errs.append(
                    f"name leakage {a}/{b}: {sorted(split_tokens[a] & split_tokens[b])[:5]}"
                )
    # split by template
    tpl = defaultdict(set)
    for r in prompts:
        tpl[r["split"]].add(r["template_id"])
    for a in ("train", "val", "test"):
        for b in ("train", "val", "test"):
            if a < b and tpl[a] & tpl[b]:
                errs.append(f"template leakage {a}/{b}: {sorted(tpl[a] & tpl[b])}")
    # NIC consistent with DOB and sex; age phrase consistent with meta age
    for r in prompts:
        nic = next((s["value"] for s in r["entities"] if s["type"] == "NIC"), None)
        if nic:
            e = check_nic(nic, date.fromisoformat(r["meta"]["dob"]), r["meta"]["sex"])
            if e:
                errs.append(f"{r['id']}: {e}")
        if nic and r["meta"]["age_years"] < 16:
            errs.append(f"{r['id']}: child with NIC")
        for s in r["entities"]:
            if s["type"] == "AGE":
                m = re.match(r"(\d+)", s["value"])
                if "month" not in s["value"] and m and int(m.group(1)) != r["meta"]["age_years"]:
                    errs.append(f"{r['id']}: age phrase {s['value']} != {r['meta']['age_years']}")
    # clinical coherence on notes
    by_sc = defaultdict(list)
    for r in notes:
        f, m = r["fields"], r["meta"]
        by_sc[r["scenario"]].append(r)
        if m["age_years"] < 16 and (f["smoking"] != "N/A" or f["dm"] != "No" or f["bp"] != "No"):
            errs.append(f"{r['id']}: child with adult habits or DM/HTN")
        if m["sex"] == "F" and f["smoking"].startswith(("Current", "Ex-")):
            errs.append(f"{r['id']}: female smoker (generator rule: never)")
        mm = re.search(r"Yes, (\d+) year", f["dm"])
        if mm and int(mm.group(1)) > m["age_years"]:
            errs.append(f"{r['id']}: DM duration longer than age")
        mm = re.search(r"Yes, (\d+) year", f["bp"])
        if mm and int(mm.group(1)) > m["age_years"]:
            errs.append(f"{r['id']}: BP duration longer than age")
        if r["scenario"] == "diabetic_foot" and f["dm"] == "No":
            errs.append(f"{r['id']}: diabetic foot without DM")

    def rate(sc, key, pos):
        rows = by_sc.get(sc, [])
        return (
            (sum(1 for r in rows if r["fields"][key] != "-") / len(rows))
            if len(rows) >= 10
            else None
        )

    for sc, key, lo in [
        ("heart_failure", "edema", 0.6),
        ("nephrotic_renal", "edema", 0.7),
        ("anaemia", "pallor", 0.7),
    ]:
        v = rate(sc, key, True)
        if v is not None and v < lo:
            errs.append(f"scenario {sc}: {key} rate {v:.2f} below {lo}")
    for sc in ("urti", "gastroenteritis", "fever_myalgia"):
        v = rate(sc, "cyanosed", True)
        if v is not None and v > 0:
            errs.append(f"scenario {sc}: cyanosis should not occur")
    return errs


def stats(notes, prompts):
    print("\nNotes by split:", dict(Counter(r["split"] for r in notes)))
    print("Prompts by split:", dict(Counter(r["split"] for r in prompts)))
    print("Prompt kind:", dict(Counter(r["kind"] for r in prompts)))
    print("Prompt primary entity_type:", dict(Counter(r["entity_type"] for r in prompts)))
    print("Prompt risk:", dict(Counter(r["risk"] for r in prompts)))
    ent = Counter(s["type"] for r in notes + prompts for s in r["entities"])
    print("Entity spans (notes + prompts):", dict(ent))
    for split in ("train", "val", "test"):
        c = Counter(s["type"] for r in prompts if r["split"] == split for s in r["entities"])
        print(f"Prompt spans in {split}:", {t: c[t] for t in COVERAGE_TYPES})
    print("Notes by scenario:", dict(Counter(r["scenario"] for r in notes)))
    n = len(notes)
    dm = sum(1 for r in notes if r["fields"]["dm"] != "No")
    bp = sum(1 for r in notes if r["fields"]["bp"] != "No")
    print(f"Notes with DM: {dm / n:.1%}, with BP (HTN): {bp / n:.1%}")
    ad = [r for r in notes if r["meta"]["age_years"] >= 16]
    m = [r for r in ad if r["meta"]["sex"] == "M"]
    f = [r for r in ad if r["meta"]["sex"] == "F"]
    sm = sum(1 for r in m if r["fields"]["smoking"].startswith("Current"))
    print(
        f"Adult males currently smoking: {sm / max(1, len(m)):.1%} (n={len(m)}); adult females smoking: 0 by rule (n={len(f)})"
    )


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", type=Path, default=ROOT / "data" / "processed")
    ap.add_argument("--min-spans", type=int, default=25, help="per type, per split (coverage)")
    a = ap.parse_args(argv)
    notes = load_jsonl(a.dir / "clinical_notes.jsonl")
    prompts = load_jsonl(a.dir / "sensitivity_dataset.jsonl")
    errs = validate(notes, prompts, a.min_spans)
    stats(notes, prompts)
    if errs:
        print(f"\nFAILED: {len(errs)} problems")
        for e in errs[:30]:
            print(" -", e)
        sys.exit(1)
    print("\nAll checks passed")


if __name__ == "__main__":
    main()
