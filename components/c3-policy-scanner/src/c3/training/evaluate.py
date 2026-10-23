"""Evaluation: violation F1, attribution, citation validity, repair rate (blueprint 12.10, 22).

Evaluation rows are built with rank-order labels and no gold injection, so a retrieval miss
counts as an error. The verdict used for F1 comes from `finalize` (probability first, text can
only make it stricter).
"""
from __future__ import annotations

import random
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field

from c3.generation.rag_format import (
    DEFAULT_SEV_TAU,
    Context,
    ContextItem,
    effective_tau,
    finalize,
    parse_output,
)

from .metrics import bootstrap_ci, ece, prf, sigmoid
from .rows import Row


@dataclass
class Calibration:
    temperature: float = 1.0
    sev_tau: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_SEV_TAU))

    def to_json(self) -> dict:
        return {"temperature": self.temperature, "sev_tau": self.sev_tau}

    @classmethod
    def from_json(cls, d: dict) -> Calibration:
        return cls(d["temperature"], d["sev_tau"])


def context_from_row(row: Row, clauses_by_id: dict) -> Context:
    """Rebuild the labelled context from a row (labels follow prompt order)."""
    ctx: Context = {}
    for pos, cid in enumerate(row.context_ids):
        c = clauses_by_id[cid]
        ctx[f"C{pos + 1}"] = ContextItem(f"C{pos + 1}", cid, c.title, c.text, c.severity, pos + 1)
    return ctx


def evaluate_rows(
    rows: Sequence[Row],
    generator,
    clauses_by_id: dict,
    calib: Calibration,
    *,
    batch_size: int = 16,
    decode: bool = True,
    max_new_tokens: int = 40,
) -> dict:
    """Run stage 1 on every row and stage 2 on predicted violations. Returns metrics + details."""
    gaps: list[float] = []
    for i in range(0, len(rows), batch_size):
        gaps += generator.stage1_batch([r.input for r in rows[i : i + batch_size]])
    probs = [float(sigmoid(g / calib.temperature)) for g in gaps]
    ctxs = [context_from_row(r, clauses_by_id) for r in rows]
    taus = [effective_tau(c, calib.sev_tau) for c in ctxs]
    prob_pred = [int(p >= t) for p, t in zip(probs, taus, strict=True)]

    texts: dict[int, str] = {}
    if decode:
        todo = [i for i, v in enumerate(prob_pred) if v]
        for j in range(0, len(todo), batch_size):
            chunk = todo[j : j + batch_size]
            outs = generator.generate_batch([rows[i].input for i in chunk], max_new_tokens)
            texts.update(zip(chunk, outs, strict=True))

    finals = []
    for i in range(len(rows)):
        parsed = parse_output(texts[i]) if i in texts else None
        finals.append(finalize(parsed, ctxs[i], probs[i], taus[i]))
    y = [r.label for r in rows]
    final_pred = [int(f.verdict == "violation") for f in finals]

    viol_idx = [i for i, r in enumerate(rows) if r.label and final_pred[i]]
    top1 = [finals[i].cited[0].clause_id in rows[i].gold_clause_ids for i in viol_idx]
    decoded = [i for i in range(len(rows)) if i in texts]
    raw_valid = [bool(finals[i].raw_valid_labels) for i in decoded]
    quote_ok = [bool(finals[i].raw_quote_grounded) for i in decoded if
                finals[i].raw_quote_grounded is not None]
    parse_ok = [parse_output(texts[i]).usable for i in decoded]
    predicted_viol = [i for i in range(len(rows)) if final_pred[i]]
    repaired = [finals[i].citation_status != "verified" for i in predicted_viol]

    m = prf(y, final_pred)
    lo, hi = bootstrap_ci(y, final_pred, n=500) if len(set(y)) > 1 else (float("nan"),) * 2
    out = {
        "n": len(rows), "violating": int(sum(y)),
        "f1": m["f1"], "f1_ci95": [lo, hi], "precision": m["precision"], "recall": m["recall"],
        "fpr": m["fpr"],
        "f1_probability_only": prf(y, prob_pred)["f1"],
        "attribution_top1": (sum(top1) / len(top1)) if top1 else float("nan"),
        "raw_citation_validity": (sum(raw_valid) / len(raw_valid)) if raw_valid else float("nan"),
        "quote_grounded": (sum(quote_ok) / len(quote_ok)) if quote_ok else float("nan"),
        "parse_rate": (sum(parse_ok) / len(parse_ok)) if parse_ok else float("nan"),
        "repair_rate": (sum(repaired) / len(repaired)) if repaired else float("nan"),
        "ece": ece(probs, y),
        "gold_in_context": sum(r.gold_in_context for r in rows if r.label)
        / max(1, sum(r.label for r in rows)),
        "problems": dict(Counter(p for f in finals for p in f.problems)),
        "mean_p_violating": (sum(p for p, v in zip(probs, y, strict=True) if v) / max(1, sum(y))),
        "mean_p_compliant": (sum(p for p, v in zip(probs, y, strict=True) if not v)
                             / max(1, len(y) - sum(y))),
    }
    return {"metrics": out, "gaps": gaps, "probs": probs, "final_pred": final_pred,
            "correct": [int(a == b) for a, b in zip(final_pred, y, strict=True)]}


def order_consistency(
    rows_rank: Sequence[Row], rows_shuf: Sequence[Row], generator, clauses_by_id, calib
) -> float:
    """Same response, shuffled labels: share of items with the same verdict."""
    a = evaluate_rows(rows_rank, generator, clauses_by_id, calib, decode=False)["final_pred"]
    b = evaluate_rows(rows_shuf, generator, clauses_by_id, calib, decode=False)["final_pred"]
    return sum(int(x == y) for x, y in zip(a, b, strict=True)) / max(1, len(a))


def shuffled_copy(rows: Sequence[Row], clauses_by_id: dict, seed: int = 0) -> list[Row]:
    """Rebuild the prompts with a random label order (for the order-consistency test)."""
    from c3.generation.rag_format import build_prompt, format_target, label_context

    rng = random.Random(seed)
    out = []
    for r in rows:
        clauses = [clauses_by_id[cid] for cid in r.context_ids]
        order = list(range(len(clauses)))
        rng.shuffle(order)
        ctx = label_context(clauses, order=order)
        response = r.input.split("\nRESPONSE:\n", 1)[1]
        out.append(Row(**{**r.__dict__, "input": build_prompt(response, ctx),
                          "target": format_target([], ctx), "context_ids": [ctx[lb].clause_id
                                                                          for lb in ctx]}))
    return out
