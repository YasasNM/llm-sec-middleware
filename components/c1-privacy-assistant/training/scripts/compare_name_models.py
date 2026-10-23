"""Score C1's name model (own corpus) on our own val split and on the Nemotron-PII benchmark.

Run from components/c1-privacy-assistant:

    python training/scripts/compare_name_models.py \
        --nemotron-test ~/Desktop/IT4010/external-data/c1_processed/nemotron_test.jsonl

Evaluated on: (i) our own val split, (ii) the Nemotron-PII external benchmark sample ("Health"
and "Healthcare Providers", held-out domains not present in the Nemotron train split).
Nemotron-PII is an external benchmark only; it is never used to train C1.
Our own test split is never read; it stays frozen until Week 12 (G4).
Nothing here is tuned: the thresholds 0.5, 0.65 and 0.8 are only reported side by side.
The table is printed and saved to evaluation/results/ (gitignored).
"""

import argparse
import json
import sys
from pathlib import Path

import train_names as tn

from c1.detection.ml_names import group_into_names, load_name_model, name_probabilities, tokenize

THRESHOLDS = (0.5, 0.65, 0.8)
MODEL_LABEL = "C1 name model (own corpus)"


# Load C1's name model through the normal hash check; stop with a clear message if it fails
def load_own_model(models_folder: Path) -> dict:
    model = load_name_model(models_folder / tn.MODEL_FILE, models_folder / "manifest.json")
    if model is None:
        raise SystemExit(f"{tn.MODEL_FILE} is missing or its hash does not match the manifest")
    return model


# Score each token of every record once, so all thresholds can reuse the same scores
def score_records(records: list[dict], model: dict) -> list[tuple]:
    scored = []
    for record in records:
        tokens = tokenize(record["text"])
        scored.append((record, tokens, name_probabilities(model, record["text"], tokens)))
    return scored


# Exact-span precision, recall and F1 at one threshold, from already scored records
def scores_at(scored: list[tuple], threshold: float) -> dict:
    true_positive = predicted = actual = 0
    for record, tokens, scores in scored:
        gold = tn.gold_name_spans(record)
        found = {
            (e.start, e.end) for e in group_into_names(record["text"], tokens, scores, threshold)
        }
        true_positive += len(gold & found)
        predicted += len(found)
        actual += len(gold)
    return tn.prf(true_positive, predicted, actual)


# All table rows for one model on one dataset: one row per threshold
def rows_for(label: str, dataset: str, records: list[dict], model: dict) -> list[dict]:
    scored = score_records(records, model)
    return [
        {"model": label, "dataset": dataset, "threshold": t, **scores_at(scored, t)}
        for t in THRESHOLDS
    ]


# Print the rows as a Markdown table that can go straight into the report
def print_table(rows: list[dict]) -> None:
    print("| Model | Evaluated on | Threshold | Precision | Recall | F1 |")
    print("|---|---|---|---|---|---|")
    for row in rows:
        print(
            f"| {row['model']} | {row['dataset']} | {row['threshold']} | "
            f"{row['precision']:.3f} | {row['recall']:.3f} | {row['f1']:.3f} |"
        )


# Read the command-line options
def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Score the name model on val and Nemotron-PII.")
    parser.add_argument("--nemotron-test", type=Path, required=True)
    parser.add_argument("--data", type=Path, default=tn.C1_ROOT / "data" / "processed")
    parser.add_argument("--models", type=Path, default=tn.C1_ROOT / "models")
    out = tn.C1_ROOT / "evaluation" / "results" / "name_models.json"
    parser.add_argument("--out", type=Path, default=out)
    return parser.parse_args(argv)


# Evaluate the name model on both datasets, print the table and save it
def main(argv=None) -> int:
    args = parse_args(argv)
    datasets = {
        "own val": tn.load_split(args.data, "val"),
        "Nemotron-PII benchmark (held-out health domains)": tn.load_jsonl(
            args.nemotron_test.expanduser()
        ),
    }
    model = load_own_model(args.models)
    rows = []
    for dataset, records in datasets.items():
        rows.extend(rows_for(MODEL_LABEL, dataset, records, model))
    print_table(rows)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(rows, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
