"""T3: temperature and severity thresholds on the unseen-policy validation set (blueprint 12.6).

Never tune on the test sets. Re-fit after every retraining or quantisation.
"""
from __future__ import annotations

from collections.abc import Sequence

import numpy as np

from c3.generation.rag_format import DEFAULT_SEV_TAU, effective_tau

from .evaluate import Calibration, context_from_row
from .metrics import choose_threshold, ece, fit_temperature, prf, sigmoid
from .rows import Row

RECALL_FLOOR = {"high": 0.90, "medium": 0.85, "low": 0.80}


def calibrate(
    rows: Sequence[Row], gaps: Sequence[float], clauses_by_id: dict
) -> tuple[Calibration, dict]:
    labels = [r.label for r in rows]
    temp = fit_temperature(gaps, labels)
    probs_before = sigmoid(np.asarray(gaps))
    probs = sigmoid(np.asarray(gaps) / temp)
    report: dict = {
        "temperature": temp,
        "ece_before": ece(probs_before, labels),
        "ece_after": ece(probs, labels),
        "n": len(rows),
    }

    # Group rows by the strictest severity in their context, then pick one threshold per group.
    group: dict[str, list[int]] = {"high": [], "medium": [], "low": []}
    for i, r in enumerate(rows):
        ctx = context_from_row(r, clauses_by_id)
        sev = min(ctx.values(), key=lambda it: DEFAULT_SEV_TAU[it.severity]).severity
        group[sev].append(i)
    sev_tau: dict[str, float] = dict(DEFAULT_SEV_TAU)
    for sev, idx in group.items():
        if len(idx) < 10 or len({labels[i] for i in idx}) < 2:
            continue
        t = choose_threshold([probs[i] for i in idx], [labels[i] for i in idx],
                             min_recall=RECALL_FLOOR[sev])
        sev_tau[sev] = float(min(max(t, 0.05), 0.95))
    # keep the order high <= medium <= low (stricter severity never gets a higher bar)
    sev_tau["medium"] = max(sev_tau["medium"], sev_tau["high"])
    sev_tau["low"] = max(sev_tau["low"], sev_tau["medium"])
    report["groups"] = {k: len(v) for k, v in group.items()}
    report["sev_tau"] = sev_tau
    calib = Calibration(temp, sev_tau)

    taus = [effective_tau(context_from_row(r, clauses_by_id), sev_tau) for r in rows]
    pred = [int(p >= t) for p, t in zip(probs, taus, strict=True)]
    m = prf(labels, pred)
    report["val_after"] = {k: m[k] for k in ("precision", "recall", "f1", "fpr")}
    return calib, report
