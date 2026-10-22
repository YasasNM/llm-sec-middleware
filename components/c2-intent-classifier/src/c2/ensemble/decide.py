from __future__ import annotations

from dataclasses import dataclass, field

from c2.config import Thresholds


@dataclass
class BranchResult:
    ok: bool
    score: float = 0.0
    nearest_ids: list[str] = field(default_factory=list)
    nearest_scores: list[float] = field(default_factory=list)
    error: str | None = None


def decide(
    similarity: BranchResult,
    classifier: BranchResult,
    thresholds: Thresholds,
) -> tuple[str, float, str, bool]:
    """Return (verdict, score, method, degraded)."""
    if not similarity.ok and not classifier.ok:
        return "BLOCK", 1.0, "fail_safe", True

    if similarity.ok and similarity.score >= thresholds.near_exact_threshold:
        return "BLOCK", _clip(similarity.score), "similarity_override", not classifier.ok

    if similarity.ok and classifier.ok:
        combined = (
            thresholds.similarity_weight * similarity.score
            + thresholds.classifier_weight * classifier.score
        )
        verdict = "BLOCK" if combined >= thresholds.ensemble_threshold else "PASS"
        return verdict, _clip(combined), "ensemble", False

    if classifier.ok:
        verdict = "BLOCK" if classifier.score >= thresholds.ensemble_threshold else "PASS"
        return verdict, _clip(classifier.score), "ensemble", True

    verdict = "BLOCK" if similarity.score >= thresholds.ensemble_threshold else "PASS"
    return verdict, _clip(similarity.score), "ensemble", True


def _clip(value: float) -> float:
    return round(min(1.0, max(0.0, value)), 4)
