"""Train the M3 name model on the synthetic HC-PII corpus and write models/manifest.json.

Run from components/c1-privacy-assistant (after build_corpus.py has filled data/processed/):

    python training/scripts/train_names.py

- Trains on the "train" split only. Names never repeat across splits (split by name).
- Reports exact-span precision, recall and F1 on the "val" split, for the model and for the
  titled-name heuristic, so the two can be compared.
- Never reads the "test" split. That stays untouched until the frozen evaluation (Week 12, G4).
- The model file is gitignored. Commit only models/manifest.json.
"""

import argparse
import json
import platform
import sys
from datetime import date
from pathlib import Path

import joblib
import scipy
import sklearn
from scipy.sparse import hstack
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression

from c1.detection.ml_names import (
    DEFAULT_THRESHOLD,
    context_of,
    detect_ml_names,
    file_sha256,
    tokenize,
)
from c1.detection.titled_names import detect_titled_names

C1_ROOT = Path(__file__).resolve().parents[2]
DATA_FILES = ("clinical_notes.jsonl", "sensitivity_dataset.jsonl")


# Read a JSON Lines file into a list of dictionaries
def load_jsonl(path: Path) -> list[dict]:
    with open(path, encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


# Load notes and prompts from data/processed and keep only one split
def load_split(data_folder: Path, split: str) -> list[dict]:
    records = []
    for name in DATA_FILES:
        records.extend(row for row in load_jsonl(data_folder / name) if row["split"] == split)
    return records


# The (start, end) of every gold PATIENT_NAME span in one record
def gold_name_spans(record: dict) -> set[tuple[int, int]]:
    return {(s["start"], s["end"]) for s in record["entities"] if s["type"] == "PATIENT_NAME"}


# Label each token 1 if it lies inside a gold name span, otherwise 0
def token_labels(record: dict) -> tuple[list, list[int]]:
    tokens = tokenize(record["text"])
    spans = gold_name_spans(record)
    labels = []
    for token in tokens:
        inside = any(start <= token.start and token.end <= end for start, end in spans)
        labels.append(1 if inside else 0)
    return tokens, labels


# Collect every token's word, context and label from a list of records
def collect_examples(records: list[dict]) -> tuple[list[str], list[str], list[int]]:
    words, contexts, labels = [], [], []
    for record in records:
        tokens, record_labels = token_labels(record)
        words.extend(token.text for token in tokens)
        contexts.extend(context_of(record["text"], tokens, i) for i in range(len(tokens)))
        labels.extend(record_labels)
    return words, contexts, labels


# Fit the two vectorizers and the Logistic Regression; return them together as one model
def fit_model(words: list[str], contexts: list[str], labels: list[int]) -> dict:
    token_vectorizer = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 4), lowercase=False)
    context_vectorizer = TfidfVectorizer(token_pattern=r"\S+")
    features = hstack(
        [token_vectorizer.fit_transform(words), context_vectorizer.fit_transform(contexts)]
    ).tocsr()
    # "balanced" because only about 6% of tokens are names; without it recall drops sharply
    classifier = LogisticRegression(max_iter=2000, class_weight="balanced")
    classifier.fit(features, labels)
    return {
        "token_vectorizer": token_vectorizer,
        "context_vectorizer": context_vectorizer,
        "classifier": classifier,
    }


# Exact-span precision, recall and F1 for PATIENT_NAME, given a detector function
def span_scores(records: list[dict], detect) -> dict:
    true_positive = predicted = actual = 0
    for record in records:
        gold = gold_name_spans(record)
        found = {(e.start, e.end) for e in detect(record["text"]) if e.type == "PATIENT_NAME"}
        true_positive += len(gold & found)
        predicted += len(found)
        actual += len(gold)
    precision = true_positive / predicted if predicted else 0.0
    recall = true_positive / actual if actual else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"precision": round(precision, 3), "recall": round(recall, 3), "f1": round(f1, 3)}


# Library versions, so a later load can be checked against the training environment
def library_versions() -> dict:
    return {
        "python": platform.python_version(),
        "scikit-learn": sklearn.__version__,
        "scipy": scipy.__version__,
        "joblib": joblib.__version__,
    }


# Write models/manifest.json: file hash, data version, settings and validation metrics
def write_manifest(model_path: Path, manifest_path: Path, threshold: float, metrics: dict) -> None:
    data_manifest = json.loads((C1_ROOT / "data" / "manifest.json").read_text())
    manifest = {
        "model": "C1 name model: TF-IDF (char 2-4 + context words) + LogisticRegression",
        "files": {model_path.name: {"sha256": file_sha256(model_path)}},
        "trained_on": {"dataset": data_manifest["dataset"], "split": "train",
                       "data_files": {n: data_manifest["files"][n]["sha256"] for n in DATA_FILES}},
        "threshold": threshold,
        "metrics_val": metrics,
        "library_versions": library_versions(),
        "created": date.today().isoformat(),
        "command": "python training/scripts/train_names.py",
    }  # fmt: skip
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")


# Read the command-line options
def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Train the C1 name model (M3).")
    parser.add_argument("--data", type=Path, default=C1_ROOT / "data" / "processed")
    parser.add_argument("--out", type=Path, default=C1_ROOT / "models" / "c1_model.joblib")
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    return parser.parse_args(argv)


# Score the model on val at a few thresholds, so the threshold choice is backed by numbers
def scores_by_threshold(val: list[dict], model: dict) -> dict:
    table = {}
    for threshold in (0.5, 0.65, 0.8):
        table[str(threshold)] = span_scores(
            val, lambda text, cut=threshold: detect_ml_names(text, model, cut)
        )
    return table


# Train on the train split, score on val, save the model and its manifest
def main(argv=None) -> int:
    args = parse_args(argv)
    model = fit_model(*collect_examples(load_split(args.data, "train")))
    val = load_split(args.data, "val")
    metrics = {
        "ml": span_scores(val, lambda text: detect_ml_names(text, model, args.threshold)),
        "ml_by_threshold": scores_by_threshold(val, model),
        "heuristic": span_scores(val, detect_titled_names),
        "val_records": len(val),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, args.out)
    write_manifest(args.out, args.out.parent / "manifest.json", args.threshold, metrics)
    print(json.dumps(metrics, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
