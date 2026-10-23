# HC-PII-Corpus: dataset card (C1 Privacy Assistant)

**Status:** v1.0.0, synthetic. Owner: Linuka. Generator: `training/scripts/build_corpus.py`. Checks: `training/scripts/validate_corpus.py`.

## What it is

A synthetic Sri Lankan healthcare text corpus with exact character-level PHI labels, used to train and evaluate C1 (name model, regex rules, masking).

**It is not hospital data.** No patient, hospital record or real person was used. Every name, number and note is generated. The only real input is the **record layout**, which a practising doctor provided as a handwritten template. The generator follows that layout field by field. Do not describe this corpus as "collected from a hospital" anywhere (report, slides, viva). The blueprint's own answer to "why synthetic?" (C1 BLUEPRINT.md, viva question 5) depends on it: real PHI needs ethics approval.

## Two tracks

| Track | File | Rows (default) | Content |
|---|---|---|---|
| A. Clinical notes | `clinical_notes.jsonl` | 1,200 | Notes in the doctor's history format, with PHI spans |
| B. Prompts | `sensitivity_dataset.csv` and `.jsonl` | 3,600 | What a clinician types to the LLM: `text, entity_type, risk` (blueprint 9.6) plus `entities_json, split, kind, template_id, id` |

Full files are generated into `data/processed/` (not committed). Committed: `data/samples/` (30 notes, 60 prompts, 6 rendered notes), `data/lexicons/` (names, places, clinical scenarios), `data/review/clinician_review_sheet.csv`, `data/manifest.json` (SHA-256 of the full files).

## The clinician's record layout (Track A)

| # | Field on the form | In the data | Labelled as |
|---|---|---|---|
| | Date | `Date:` | DATE (JSONL only) |
| 1 | Name | `1) Name:` | PATIENT_NAME |
| 2 | Age | `2) Age:` | AGE |
| 3 | Presenting complaint | `3) Presenting complaint:` | none |
| 4 | Past medical history: DM, BP | `DM:` and `BP:` (Yes, years, drug / No) | none |
| 5 | Allergies | `5) Allergies:` | none |
| 6 | Smoking, Alcohol / drug abuse | `6) Smoking:` and `Alcohol / drug abuse:` | none |
| | Family history | `Family history:` (relative: condition) | none |
| | Living area | `Living area:` (town, sometimes with district) | LOCATION |
| | O/E: Pallor, Cyanosed, Edema | three lines, `-` or `+` with detail | none |

The form's first lines (letterhead, Rx) are not reproduced. The letterhead belongs to a real nursing home and is not used.

## Label schema

| Type | Class | Notes |
|---|---|---|
| PATIENT_NAME, NIC, PHONE, MRN, EMAIL, DOB, ADDRESS | explicit | Always masked (K2) |
| AGE, LOCATION | quasi | Feed the re-identification score |
| DATE | quasi | Visit or follow-up date. Not in the C1 v1 type list, so it is in the JSONL only and not in the CSV |

- `P\d{4}` and `D\d{3}` ids are never labelled (IR12).
- Span for AGE is the whole phrase ("58-year-old"). Span for MRN includes the label ("MRN 0045821").
- Row `entity_type` is the highest-priority type in the row (NIC, MRN, PHONE, EMAIL, PATIENT_NAME, DOB, ADDRESS, LOCATION, AGE), else NONE.
- Row `risk`: **high** if any explicit identifier, **medium** if only quasi identifiers, **low** if none. This is a labelling rule chosen here. Confirm it matches the report's scheme before training.

## How accuracy is controlled

| Control | How |
|---|---|
| Exact spans | Every span is checked: `text[start:end] == value`, no overlaps |
| No name leakage | Every name string (by its last word) lives in one split only: train 70%, val 15%, test 15% |
| No template leakage | Test prompts use templates never used in training |
| NIC consistent with the person | Old (9 digits + V) and new (12 digits) formats; birth year and day number match the date of birth, +500 for females, leap-year day counting. Serial and check digit are random, so a NIC is structurally valid but not verifiable |
| Age consistent | Date of birth, age phrase and NIC agree. Children get no NIC, no smoking, no DM or BP |
| Clinical coherence | 19 scenarios in `lexicons/clinical.json`. O/E findings follow the complaint: edema in heart failure and nephrotic cases, pallor in anaemia, no cyanosis in a cold or gastroenteritis. DM and BP depend on age. Diabetic foot always has DM |
| Habits by sex and age | Smoking and alcohol rates differ by sex. Females never smoke by rule (a simplification) |
| Hard negatives | Eponymous diseases (Parkinson, Addison, Crohn, Down, Cushing, Graves, Hashimoto, Hodgkin, Alzheimer), P and D ids, lab values, doses, ward numbers |
| Phone-like digits | Unlabelled phone or NIC-like strings fail validation. Note that a new 12-digit NIC beginning 2000 contains a phone-shaped substring: C1's regex needs digit boundaries and the longest-span rule |
| No real clinic details | Validation fails if the real nursing home's name, road or phone appears |

## What the numbers are based on (and what is not)

The prevalence of DM and hypertension by age, smoking and alcohol rates and the O/E probabilities are **approximate settings written by the generator author**, not figures from a study or a hospital. They are in `lexicons/clinical.json` and `build_corpus.py` so the doctor can correct them. Phone numbers are random and may match a real number by chance. Names are common Sri Lankan names and may match real people by chance.

## Clinician review (recommended before PP1 and the final report)

1. Give the doctor `data/review/clinician_review_sheet.csv` (60 notes covering all scenarios).
2. They mark layout match and clinical plausibility (Y/N) and what to fix.
3. Fix `lexicons/clinical.json` or the generator, regenerate, update `manifest.json`.
4. Only after that, write "reviewed by a clinician (n = 60)" in the report. Report the agreement rate.

## Regenerate and check

```bash
cd components/c1-privacy-assistant
python training/scripts/build_corpus.py --notes 1200 --prompts 3600 --seed 42 --sample --manifest
python training/scripts/validate_corpus.py
python -m pytest tests/unit/test_corpus.py
```

The output is deterministic: the same command gives the same SHA-256 as `manifest.json`.

## External benchmark: Nemotron-PII (evaluation only)

| Item | Detail |
|---|---|
| Source | Nemotron-PII by NVIDIA, Hugging Face `nvidia/Nemotron-PII`, licensed CC BY 4.0. Credit NVIDIA in the report |
| Nature | Synthetic by its authors' own description (tagged synthetic), English, mostly US-centred names, addresses and phone formats |
| Use in C1 | External benchmark only. Not used for training, threshold choice or any other tuning |
| Sample | The published test split only. It uses held-out domains (none of its domain names appear in its train split), so the sample is taken from its "Health" and "Healthcare Providers" domains: 5,000 random rows, seed 42. This measures how the name model handles document types it never saw |
| Labels | `first_name` and `last_name` spans, mapped to PATIENT_NAME. This is a simplification, because some of these people are not patients. Every other label counts as "not a patient name" |
| Files | The parquet file and the prepared sample stay outside the repository and are never committed. Prepared with `training/scripts/prepare_nemotron.py`, scored with `training/scripts/compare_name_models.py` |

A mixed model (our own corpus plus a Nemotron-PII train sample) was tried and scored F1 0.52 against 0.889 for the own-corpus model on our own validation split, so it was dropped.

## Limitations

- English only. No Sinhala or Tamil script.
- Templates are rule based, so language is more regular than real clinic text.
- Typed text only. Handwriting and OCR noise are separate work (C1 OCR evaluation).
- Doctor and staff names are not generated (the C1 schema has no staff-name type).
- Not a substitute for validation on real-style data (i2b2/n2c2 only if access is granted).
