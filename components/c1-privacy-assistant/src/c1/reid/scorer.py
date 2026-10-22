"""M5 re-identification scorer: how likely the details in a prompt point to one patient.

Rules (BLUEPRINT 12.2, numbers in configs/reid_lists.yaml):
- Each kind of detail (AGE, CONDITION, WARD, LOCATION) adds its highest rarity weight:
  high 3, medium 2, low 1. Repeats of the same kind do not add more ("Ward 3 and Ward 7").
- count = how many different kinds were found. raw = the sum of their weights.
- High if count >= 3 or raw >= 7; Medium if count >= 2 or raw >= 4; otherwise Low.
Inspired by k-anonymity (Sweeney 2002); it is rule based, not a k-anonymity implementation.
"""

from dataclasses import dataclass, field

from c1.reid.finders import QuasiIdentifier, find_quasi_identifiers
from c1.reid.suggestions import apply_suggestions

# How each kind of detail is described in the explanation (never the value itself)
KIND_WORDS = {"AGE": "age", "CONDITION": "condition", "WARD": "ward or bed", "LOCATION": "town"}


@dataclass
class ReidResult:
    """Everything /v1/reid returns. The explanation names kinds of detail, never values."""

    quasi_identifiers: list[QuasiIdentifier] = field(default_factory=list)
    count: int = 0
    score: int = 0
    level: str = "Low"
    explanation: str = ""
    safer_prompt: str = ""


# The highest rarity weight seen for each kind of detail, e.g. {"AGE": 3, "WARD": 2}
def weight_per_kind(found: list[QuasiIdentifier], weights: dict) -> dict[str, int]:
    best = {}
    for item in found:
        best[item.type] = max(best.get(item.type, 0), weights[item.rarity])
    return best


# Turn count and raw points into a level and a 0 to 100 score
def level_and_score(count: int, raw: int, levels: dict) -> tuple[str, int]:
    if count >= levels["high"]["min_count"] or raw >= levels["high"]["min_raw"]:
        return "High", min(60 + raw * 6, 100)
    if count >= levels["medium"]["min_count"] or raw >= levels["medium"]["min_raw"]:
        return "Medium", min(30 + raw * 5, 59)
    return "Low", min(raw * 10, 29)


# A short, value-free explanation the UI can show next to the score
def explain(kinds: list[str], level: str) -> str:
    if not kinds:
        return "No re-identification details found."
    listed = ", ".join(KIND_WORDS.get(kind, kind.lower()) for kind in kinds)
    if level == "High":
        return f"Together these details could identify the patient: {listed}. Use the suggestions."
    if level == "Medium":
        return f"These details narrow down who the patient is: {listed}. Consider the suggestions."
    return f"Low risk from: {listed}."


# Score the re-identification risk of a prompt and build the safer version
def score_reid(text: str, lists: dict) -> ReidResult:
    found = find_quasi_identifiers(text, lists)
    per_kind = weight_per_kind(found, lists["rarity_weights"])
    count, raw = len(per_kind), sum(per_kind.values())
    level, score = level_and_score(count, raw, lists["levels"])
    return ReidResult(
        quasi_identifiers=found,
        count=count,
        score=score,
        level=level,
        explanation=explain(list(per_kind), level),
        safer_prompt=apply_suggestions(text, found),
    )
