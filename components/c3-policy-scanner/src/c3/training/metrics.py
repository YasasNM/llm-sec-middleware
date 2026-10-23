"""Metrics: F1 with bootstrap interval, McNemar, temperature scaling, calibration error.

Small, dependency-light implementations (numpy only) so they run in unit tests.
"""
from __future__ import annotations

import math
from collections.abc import Sequence

import numpy as np


def prf(y_true: Sequence[int], y_pred: Sequence[int]) -> dict[str, float]:
    yt, yp = np.asarray(y_true), np.asarray(y_pred)
    tp = int(((yt == 1) & (yp == 1)).sum())
    fp = int(((yt == 0) & (yp == 1)).sum())
    fn = int(((yt == 1) & (yp == 0)).sum())
    tn = int(((yt == 0) & (yp == 0)).sum())
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * p * r / (p + r) if p + r else 0.0
    fpr = fp / (fp + tn) if fp + tn else 0.0
    return {"precision": p, "recall": r, "f1": f1, "fpr": fpr, "tp": tp, "fp": fp, "fn": fn,
            "tn": tn, "n": len(yt)}


def bootstrap_ci(
    y_true: Sequence[int], y_pred: Sequence[int], *, metric: str = "f1", n: int = 1000,
    seed: int = 0, alpha: float = 0.05,
) -> tuple[float, float]:
    yt, yp = np.asarray(y_true), np.asarray(y_pred)
    rng = np.random.default_rng(seed)
    vals = []
    for _ in range(n):
        idx = rng.integers(0, len(yt), len(yt))
        vals.append(prf(yt[idx], yp[idx])[metric])
    lo, hi = np.quantile(vals, [alpha / 2, 1 - alpha / 2])
    return float(lo), float(hi)


def mcnemar(correct_a: Sequence[bool], correct_b: Sequence[bool]) -> dict[str, float]:
    """Exact-ish McNemar test on paired correctness (continuity-corrected chi-square)."""
    a, b = np.asarray(correct_a, bool), np.asarray(correct_b, bool)
    n01 = int((a & ~b).sum())        # A right, B wrong
    n10 = int((~a & b).sum())        # A wrong, B right
    if n01 + n10 == 0:
        return {"n_a_only": n01, "n_b_only": n10, "chi2": 0.0, "p": 1.0}
    chi2 = (abs(n01 - n10) - 1) ** 2 / (n01 + n10)
    p = math.erfc(math.sqrt(chi2 / 2))                  # survival function of chi2 with 1 dof
    return {"n_a_only": n01, "n_b_only": n10, "chi2": chi2, "p": p}


def sigmoid(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(x, -50, 50)))


def fit_temperature(gaps: Sequence[float], labels: Sequence[int]) -> float:
    """One temperature T minimising NLL of sigmoid(gap / T), by golden-section search.

    `gap` is the logit gap (violation minus pass) at the verdict position.
    """
    g, y = np.asarray(gaps, float), np.asarray(labels, float)

    def nll(t: float) -> float:
        p = np.clip(sigmoid(g / t), 1e-7, 1 - 1e-7)
        return float(-(y * np.log(p) + (1 - y) * np.log(1 - p)).mean())

    lo, hi = 0.05, 50.0
    phi = (math.sqrt(5) - 1) / 2
    a, b = lo, hi
    c, d = b - phi * (b - a), a + phi * (b - a)
    for _ in range(80):
        if nll(c) < nll(d):
            b = d
        else:
            a = c
        c, d = b - phi * (b - a), a + phi * (b - a)
    return float((a + b) / 2)


def ece(probs: Sequence[float], labels: Sequence[int], bins: int = 10) -> float:
    p, y = np.asarray(probs, float), np.asarray(labels, float)
    edges = np.linspace(0, 1, bins + 1)
    total = 0.0
    for lo, hi in zip(edges[:-1], edges[1:], strict=True):
        m = (p > lo) & (p <= hi) if lo > 0 else (p >= lo) & (p <= hi)
        if m.any():
            total += m.mean() * abs(p[m].mean() - y[m].mean())
    return float(total)


def precision_at_prevalence(tpr: float, fpr: float, prevalence: float) -> float:
    denom = prevalence * tpr + (1 - prevalence) * fpr
    return prevalence * tpr / denom if denom else 0.0


def choose_threshold(
    probs: Sequence[float], labels: Sequence[int], *, min_recall: float = 0.0,
) -> float:
    """Threshold maximising F1 subject to a recall floor. Falls back to best recall."""
    p, y = np.asarray(probs, float), np.asarray(labels, int)
    best_t, best_f1, best_any = 0.5, -1.0, (0.5, -1.0)
    for t in np.unique(np.concatenate([p, [0.5]])):
        m = prf(y, (p >= t).astype(int))
        if m["recall"] >= min_recall and m["f1"] > best_f1:
            best_t, best_f1 = float(t), m["f1"]
        if m["recall"] > best_any[1]:
            best_any = (float(t), m["recall"])
    return best_t if best_f1 >= 0 else best_any[0]


def auc(scores: Sequence[float], labels: Sequence[int]) -> float:
    """Area under the ROC curve (Mann-Whitney), ties count half."""
    s, y = np.asarray(scores, float), np.asarray(labels, int)
    pos, neg = s[y == 1], s[y == 0]
    if len(pos) == 0 or len(neg) == 0:
        return float("nan")
    order = np.argsort(np.concatenate([pos, neg]), kind="mergesort")
    ranks = np.empty(len(order))
    allv = np.concatenate([pos, neg])[order]
    i = 0
    while i < len(allv):
        j = i
        while j + 1 < len(allv) and allv[j + 1] == allv[i]:
            j += 1
        ranks[order[i : j + 1]] = (i + j) / 2 + 1
        i = j + 1
    r_pos = ranks[: len(pos)].sum()
    return float((r_pos - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg)))


def best_f1(scores: Sequence[float], labels: Sequence[int]) -> float:
    """F1 at the best threshold. A smoother model-selection signal than F1 at a fixed cutoff."""
    s = np.asarray(scores, float)
    best = 0.0
    for t in np.unique(s):
        best = max(best, prf(labels, (s >= t).astype(int))["f1"])
    return best
