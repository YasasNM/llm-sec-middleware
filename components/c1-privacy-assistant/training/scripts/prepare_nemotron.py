"""Turn the Nemotron-PII test file into an external benchmark sample for the C1 name model.

Run from components/c1-privacy-assistant:

    python training/scripts/prepare_nemotron.py \
        --test-parquet ~/Desktop/IT4010/external-data/test-00000-of-00001.parquet

- Evaluation only. Nemotron-PII is never used to train C1 or to choose its threshold.
- 5,000 random rows from the "Health" and "Healthcare Providers" domains (seed 42). The Nemotron
  test split uses held-out domains (no domain name is shared with its train split), so this
  measures how the model handles document types it never saw.
- Output goes NEXT TO the parquet file (external-data/c1_processed/), never into the repository.

Nemotron-PII (NVIDIA, CC BY 4.0) is synthetic data made by its authors, in US-centred English.
"""

import argparse
import ast
import json
import sys
from pathlib import Path

import pandas as pd

# SIMPLIFICATION: Nemotron labels people by role-free name parts, so first_name and last_name
# become our PATIENT_NAME even when the person is a doctor, customer or employee, not a patient.
# Every other Nemotron label (company_name, street_address, ...) counts as "not a patient name".
NAME_LABELS = {"first_name", "last_name"}
# The test split is a held-out domain split: it has no "Healthcare" domain. These are its
# health-related domains, so the evaluation measures document types the model never saw.
TEST_HEALTH_DOMAINS = {"Health", "Healthcare Providers"}
KEEP_COLUMNS = ["text", "spans", "domain"]


# Read one Nemotron parquet file, keeping only the text, spans and domain columns
def read_nemotron(path: Path) -> pd.DataFrame:
    return pd.read_parquet(path, columns=KEEP_COLUMNS)


# Spans arrive as a list, or as a string that looks like a Python list; return a plain list
def parse_spans(value) -> list[dict]:
    if isinstance(value, str):
        return ast.literal_eval(value)
    return [dict(span) for span in value]


# Join a first name and a last name that sit next to each other into one name span
def merge_name_parts(text: str, parts: list[tuple[int, int]]) -> list[tuple[int, int]]:
    merged = []
    for start, end in sorted(parts):
        if merged and text[merged[-1][1] : start].strip(" .") == "":
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


# Find the PATIENT_NAME spans in one row (first_name and last_name only, see SIMPLIFICATION)
def name_spans(text: str, spans: list[dict]) -> list[tuple[int, int]]:
    parts = [(int(s["start"]), int(s["end"])) for s in spans if s["label"] in NAME_LABELS]
    return merge_name_parts(text, parts)


# Convert one Nemotron row into the record format our evaluation code already reads
def to_record(text: str, spans_value, domain: str, split: str, number: int) -> dict:
    entities = [
        {"type": "PATIENT_NAME", "start": start, "end": end, "value": text[start:end]}
        for start, end in name_spans(text, parse_spans(spans_value))
    ]
    return {
        "id": f"nemotron-{split}-{number:05d}",
        "split": split,
        "source": "nemotron",
        "domain": domain,
        "text": text,
        "entities": entities,
    }


# Test sample: random rows from the held-out health domains only, used for evaluation only
def sample_test(frame: pd.DataFrame, size: int, seed: int) -> pd.DataFrame:
    health = frame[frame["domain"].isin(TEST_HEALTH_DOMAINS)]
    if health.empty:
        found = sorted(frame["domain"].unique())
        raise ValueError(f"no {sorted(TEST_HEALTH_DOMAINS)} rows in the test file; found {found}")
    return health.sample(n=min(size, len(health)), random_state=seed)


# Convert every row of a sample into records
def to_records(frame: pd.DataFrame, split: str) -> list[dict]:
    rows = zip(frame["text"], frame["spans"], frame["domain"], strict=True)
    return [
        to_record(text, spans, domain, split, n) for n, (text, spans, domain) in enumerate(rows)
    ]


# Write records as JSON Lines, one record per line
def write_jsonl(records: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


# Read the command-line options
def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prepare the Nemotron-PII benchmark sample.")
    parser.add_argument("--test-parquet", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=None, help="default: next to the parquet file")
    parser.add_argument("--test-rows", type=int, default=5000)
    parser.add_argument("--seed", type=int, default=42)
    return parser.parse_args(argv)


# Build the benchmark sample and write it outside the repository
def main(argv=None) -> int:
    args = parse_args(argv)
    out = args.out or args.test_parquet.expanduser().parent / "c1_processed"
    test = sample_test(read_nemotron(args.test_parquet.expanduser()), args.test_rows, args.seed)
    write_jsonl(to_records(test, "nemotron_test"), out / "nemotron_test.jsonl")
    print(f"benchmark rows {len(test)} -> {out / 'nemotron_test.jsonl'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
