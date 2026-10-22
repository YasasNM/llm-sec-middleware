from c2.config import Thresholds
from c2.ensemble.decide import BranchResult, decide


class _T(Thresholds):
    def __init__(self) -> None:  # noqa: D107
        self.near_exact_threshold = 0.97
        self.ensemble_threshold = 0.75
        self.similarity_weight = 0.5
        self.classifier_weight = 0.5
        self.top_k = 5


def test_similarity_override_beats_disagreeing_classifier() -> None:
    verdict, score, method, degraded = decide(
        BranchResult(ok=True, score=0.99),
        BranchResult(ok=True, score=0.20),
        _T(),
    )
    assert verdict == "BLOCK"
    assert method == "similarity_override"
    assert score == 0.99
    assert degraded is False


def test_combined_score_blocks_when_above_threshold() -> None:
    verdict, score, method, _ = decide(
        BranchResult(ok=True, score=0.70),
        BranchResult(ok=True, score=0.85),
        _T(),
    )
    assert verdict == "BLOCK"
    assert method == "ensemble"
    assert score == 0.775


def test_placeholder_weights_do_not_block_on_low_similarity() -> None:
    # 0.5*0.30 + 0.5*0.85 = 0.575, below the 0.75 placeholder (BLUEPRINT §12.3).
    verdict, score, method, _ = decide(
        BranchResult(ok=True, score=0.30),
        BranchResult(ok=True, score=0.85),
        _T(),
    )
    assert verdict == "PASS"
    assert method == "ensemble"
    assert score == 0.575


def test_benign_clinical_prompt_passes() -> None:
    verdict, _, method, _ = decide(
        BranchResult(ok=True, score=0.55),
        BranchResult(ok=True, score=0.10),
        _T(),
    )
    assert verdict == "PASS"
    assert method == "ensemble"


def test_both_branches_down_fail_safe() -> None:
    verdict, score, method, degraded = decide(
        BranchResult(ok=False, error="chroma down"),
        BranchResult(ok=False, error="checkpoint missing"),
        _T(),
    )
    assert verdict == "BLOCK"
    assert method == "fail_safe"
    assert score == 1.0
    assert degraded is True


def test_classifier_only_when_chroma_empty() -> None:
    verdict, score, method, degraded = decide(
        BranchResult(ok=False, error="chroma collection empty"),
        BranchResult(ok=True, score=0.91),
        _T(),
    )
    assert verdict == "BLOCK"
    assert method == "ensemble"
    assert score == 0.91
    assert degraded is True
