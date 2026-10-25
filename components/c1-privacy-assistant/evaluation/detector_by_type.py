"""Score C1's explicit-PHI detector (regex + name model) per PHI type on one corpus split.

Run from components/c1-privacy-assistant (after build_corpus.py and train_names.py):

    python evaluation/detector_by_type.py            # our own val split
    python evaluation/detector_by_type.py --split test   # ONLY at the frozen evaluation (Week 12)

A finding counts as correct only if its type, start and end all match the label exactly.
ADDRESS is labelled in the corpus but C1 has no address detector (it is not in the contract's
always-masked list), so it is reported with zero recall on purpose rather than hidden.
"""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

C1_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(C1_ROOT / "src"))

from dataclasses import replace  # noqa: E402

from c1.config import load_settings  # noqa: E402
from c1.pipeline import find_explicit, load_resources  # noqa: E402

EXPLICIT_TYPES = ["PATIENT_NAME", "NIC", "PHONE", "MRN", "EMAIL", "DOB", "ADDRESS"]
DATA_FILES = ("clinical_notes.jsonl", "sensitivity_dataset.jsonl")


# Read every record of one split from both corpus files
def load_split(folder: Path, split: str) -> list[dict]:
    records = []
    for name in DATA_FILES:
        with open(folder / name, encoding="utf-8") as handle:
            records += [r for r in map(json.loads, handle) if r["split"] == split]
    return records


# Count correct, predicted and labelled spans per type over all records
def count_matches(records: list[dict], resources) -> tuple[Counter, Counter, Counter]:
    correct, predicted, labelled = Counter(), Counter(), Counter()
    for record in records:
        gold = {(e["type"], e["start"], e["end"]) for e in record["entities"]}
        found = {(e.type, e.start, e.end) for e in find_explicit(record["text"], resources)}
        gold = {g for g in gold if g[0] in EXPLICIT_TYPES}
        correct.update(t for t, _, _ in gold & found)
        predicted.update(t for t, _, _ in found)
        labelled.update(t for t, _, _ in gold)
    return correct, predicted, labelled


# Precision, recall and F1 for one type, rounded to 3 decimals
def scores(correct: int, predicted: int, labelled: int) -> dict:
    precision = correct / predicted if predicted else 0.0
    recall = correct / labelled if labelled else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"precision": round(precision, 3), "recall": round(recall, 3), "f1": round(f1, 3)}


# One table row per type, plus an overall row
def score_table(correct: Counter, predicted: Counter, labelled: Counter) -> list[dict]:
    rows = [
        {"type": t, "labelled": labelled[t], **scores(correct[t], predicted[t], labelled[t])}
        for t in EXPLICIT_TYPES
    ]
    total = scores(sum(correct.values()), sum(predicted.values()), sum(labelled.values()))
    return rows + [{"type": "ALL", "labelled": sum(labelled.values()), **total}]


# Print the rows as a Markdown table for the report
def print_table(rows: list[dict], split: str, detector: str) -> None:
    print(f"Split: {split}. Name detector: {detector}.")
    print("| Type | Labelled | Precision | Recall | F1 |")
    print("|---|---|---|---|---|")
    for r in rows:
        numbers = f"{r['precision']:.3f} | {r['recall']:.3f} | {r['f1']:.3f}"
        print(f"| {r['type']} | {r['labelled']} | {numbers} |")


# Read the command-line options
def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Per-type scores of the C1 explicit detector.")
    parser.add_argument("--data", type=Path, default=C1_ROOT / "data" / "processed")
    parser.add_argument("--split", default="val", choices=["train", "val", "test"])
    return parser.parse_args(argv)


# Load C1 like the service, but WITHOUT the name lexicon: it holds val and test names,
# so using it here would leak the answers. The scores measure the ML model and regex only.
def main(argv=None) -> list[dict]:
    args = parse_args(argv)
    resources = load_resources(replace(load_settings(), name_lexicon=False))
    rows = score_table(*count_matches(load_split(args.data, args.split), resources))
    print_table(rows, args.split, resources.name_detector)
    return rows


if __name__ == "__main__":
    main()
