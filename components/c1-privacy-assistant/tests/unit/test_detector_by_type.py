"""Unit tests for evaluation/detector_by_type.py (per-type scores of the explicit detector)."""

import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "evaluation"))

import detector_by_type as dbt  # noqa: E402

from c1.config import C1_ROOT  # noqa: E402
from c1.configs import load_config  # noqa: E402
from c1.pipeline import Resources  # noqa: E402


# Resources with no name model, so the titled-name heuristic finds names
def heuristic_resources() -> Resources:
    return Resources(
        name_model=None,
        name_threshold=0.65,
        reid_lists=load_config("reid_lists.yaml", C1_ROOT / "configs"),
        risk_rules=load_config("default.yaml", C1_ROOT / "configs")["risk"],
    )


def test_exact_matches_are_counted_per_type():
    text = "Patient Nimal Perera, MRN: 123456, Dr. Silva"
    record = {
        "text": text,
        "entities": [
            {"type": "PATIENT_NAME", "start": 8, "end": 20},
            {"type": "MRN", "start": 27, "end": 33},
            {"type": "AGE", "start": 0, "end": 1},  # not an explicit type, ignored
        ],
    }
    correct, predicted, labelled = dbt.count_matches([record], heuristic_resources())
    assert correct == Counter({"PATIENT_NAME": 1, "MRN": 1})
    assert labelled == Counter({"PATIENT_NAME": 1, "MRN": 1})
    assert predicted["PATIENT_NAME"] == 1  # the doctor's name is not flagged


def test_score_table_has_every_type_and_an_overall_row():
    rows = dbt.score_table(Counter({"NIC": 2}), Counter({"NIC": 2}), Counter({"NIC": 4}))
    by_type = {row["type"]: row for row in rows}
    assert list(by_type) == dbt.EXPLICIT_TYPES + ["ALL"]
    assert by_type["NIC"]["recall"] == 0.5 and by_type["ADDRESS"]["f1"] == 0.0


def test_scores_with_nothing_found():
    assert dbt.scores(0, 0, 3) == {"precision": 0.0, "recall": 0.0, "f1": 0.0}
