"""Where does the generator fail on held-out organisations? Splits S2 by rule type.

    python tools/analyse_s2.py

Reports stage-1 AUC and recall for violations of seen rules (new wording) versus unseen rules,
and what a better threshold would give. Uses the saved checkpoint and calibration.
"""
from __future__ import annotations

import json
from collections import defaultdict

from c3.generation.t5 import T5Generator
from c3.training.evaluate import Calibration, evaluate_rows
from c3.training.metrics import auc, best_f1, prf
from c3.training.pipeline import MODELS, all_clauses, make_embedder
from c3.training.rows import build_retrievers, build_sft_rows, items_in_split
from c3.training.rules_bank import RULES_BY_ID
from c3.training.synth import build_pack


def main() -> None:
    pack = build_pack(7)
    cl = all_clauses(pack)
    ret = build_retrievers(pack, make_embedder("tuned", MODELS / "retriever"))
    calib_file = MODELS / "generator" / "calibration.json"
    calib = Calibration.from_json(json.loads(calib_file.read_text()))
    gen = T5Generator(MODELS / "generator", device="cuda", temperature=calib.temperature)

    for split in ("test_s2", "val_unseen", "test_s1"):
        items = items_in_split(pack, split)
        rows = build_sft_rows(pack, items, ret, draws=1, train=False, seed=1)
        res = evaluate_rows(rows, gen, cl, calib, decode=False)
        gaps, probs = res["gaps"], res["probs"]
        by_item = {i.item_id: i for i in items}
        labels = [r.label for r in rows]
        print(f"\n== {split}: n={len(rows)}  AUC={auc(gaps, labels):.3f}  best-threshold F1="
              f"{best_f1(gaps, labels):.3f}")
        groups: dict[str, list[int]] = defaultdict(list)
        for k, r in enumerate(rows):
            it = by_item[r.item_id]
            if not it.label:
                groups["compliant"].append(k)
            else:
                kind = "unseen-rule" if any(RULES_BY_ID[g].unseen for g in it.gold_rules) \
                    else "seen-rule"
                groups[kind].append(k)
                groups[f"{kind}/gold_in_ctx={r.gold_in_context}"].append(k)
        for name, idx in sorted(groups.items()):
            p = [probs[k] for k in idx]
            print(f"  {name:32s} n={len(idx):4d} mean_p={sum(p)/len(p):.3f} "
                  f"flagged@calibrated={sum(res['final_pred'][k] for k in idx)/len(idx):.2f}")
        pos = [k for k in range(len(rows)) if labels[k]]
        neg = [k for k in range(len(rows)) if not labels[k]]
        seen_pos = [k for k in pos if not any(RULES_BY_ID[g].unseen
                                              for g in by_item[rows[k].item_id].gold_rules)]
        unseen_pos = [k for k in pos if k not in seen_pos]
        for name, ps in (("seen-rule", seen_pos), ("unseen-rule", unseen_pos)):
            if ps:
                ks = ps + neg
                print(f"  AUC {name} vs compliant: "
                      f"{auc([gaps[k] for k in ks], [labels[k] for k in ks]):.3f}")
        for t in (0.05, 0.1, 0.2, 0.3, 0.5):
            pred = [int(p >= t) for p in probs]
            m = prf(labels, pred)
            print(f"  tau={t:.2f}: F1={m['f1']:.3f} P={m['precision']:.3f} R={m['recall']:.3f} "
                  f"FPR={m['fpr']:.3f}")


if __name__ == "__main__":
    main()
