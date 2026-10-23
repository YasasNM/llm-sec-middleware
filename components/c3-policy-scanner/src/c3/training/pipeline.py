"""End-to-end training pipeline: data -> T1 retriever -> rows -> checks -> T2 generator ->
T3 calibration -> evaluation (blueprint 8.4, 13.2).

    python -m c3.training.pipeline --stage all
    python -m c3.training.pipeline --stage t2 --epochs-gen 8

Outputs
    data/                 policy pack (committed: .md files + manifest.json), processed/ ignored
    models/retriever/     fine-tuned retriever            (weights ignored by git)
    models/generator/     fine-tuned generator + calibration.json
    models/manifest.json  data version, seeds, SHA-256 of every model file
    results/<run>/        metrics JSON and RESULTS.md
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import time
from pathlib import Path

from c3.config import COMPONENT_ROOT

from . import checks
from .evaluate import (
    Calibration,
    evaluate_rows,
    order_consistency,
    shuffled_copy,
)
from .rows import (
    Row,
    build_counterfactual_rows,
    build_removal_rows,
    build_retrievers,
    build_sft_rows,
    build_triplets,
    items_in_split,
    recall_at_k,
)
from .rules_bank import INJECTION_TEST
from .synth import Item, Pack, build_pack, injection_variant, write_pack

ROOT = COMPONENT_ROOT
DATA = ROOT / "data"
_RUN = os.getenv("C3_RUN_DIR")           # Colab / smoke runs keep their output elsewhere
MODELS = Path(_RUN) / "models" if _RUN else ROOT / "models"
RESULTS = Path(_RUN) / "results" if _RUN else ROOT / "results"


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def dir_hashes(d: Path) -> dict[str, str]:
    return {str(p.relative_to(d)).replace("\\", "/"): sha256_file(p)
            for p in sorted(d.rglob("*")) if p.is_file()}


def dedupe(rows: list[Row]) -> list[Row]:
    seen, out = set(), []
    for r in rows:
        key = (r.input, r.target)
        if key not in seen:
            seen.add(key)
            out.append(r)
    return out


def make_embedder(kind: str, retriever_dir: Path | None = None):
    from c3.indexing.embedder import TransformerEmbedder

    base = "sentence-transformers/all-MiniLM-L6-v2"
    if kind == "base":
        return TransformerEmbedder(None, base, device="cuda")
    return TransformerEmbedder(retriever_dir, base, device="cuda")


def all_clauses(pack: Pack) -> dict:
    return {c.clause_id: c for o in pack.orgs.values() for c in o.clauses}


# ---- stages --------------------------------------------------------------------------
def stage_data(seed: int, log=print) -> Pack:
    pack = build_pack(seed)
    (DATA / "processed").mkdir(parents=True, exist_ok=True)
    manifest = write_pack(pack, DATA)
    log(f"[data] {len(pack.items)} items, splits={manifest['splits']}")
    return pack


def stage_data_public(seed: int, gen_path: Path, log=print) -> Pack:
    from .public.pack import build_public_pack, provenance

    if not gen_path.exists():
        log(f"[data] WARNING: {gen_path.name} not found. Without generated answers there are no "
            "violations tied to the real regulations. Run c3.training.public.generate (Colab).")
    pack = build_public_pack(gen_path, seed=seed, log=log)
    prov = provenance(pack)
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / "data_provenance.json").write_text(json.dumps(prov, indent=2))
    log(f"[data] public pack: {prov}")
    return pack


def recall_report(pack: Pack, retrievers) -> dict:
    out = {}
    for split in ("val", "test_s1", "val_unseen", "test_s2"):
        out[split] = recall_at_k(pack, items_in_split(pack, split), retrievers)
    return out


def stage_t1(pack: Pack, epochs: int, seed: int, log=print) -> dict:
    from .retriever_train import finetune_retriever

    base = make_embedder("base")
    before = recall_report(pack, build_retrievers(pack, base))
    log(f"[t1] recall before: {json.dumps(before['test_s2'])}")
    train_items = items_in_split(pack, "train")
    triplets = build_triplets(pack, train_items, build_retrievers(pack, base))
    log(f"[t1] {len(triplets)} triplets from {len(train_items)} train items")
    out = MODELS / "retriever"
    info = finetune_retriever(triplets, out, epochs=epochs, seed=seed, log=log)
    tuned = make_embedder("tuned", out)
    after = recall_report(pack, build_retrievers(pack, tuned))
    log(f"[t1] recall after:  {json.dumps(after['test_s2'])}")
    res = {"train": info, "recall_before": before, "recall_after": after,
           "embedder_version": tuned.version}
    (RESULTS / "t1_retriever.json").parent.mkdir(parents=True, exist_ok=True)
    (RESULTS / "t1_retriever.json").write_text(json.dumps(res, indent=2))
    return res


def build_all_rows(pack: Pack, retrievers, seed: int, tokenizer=None, log=print,
                   removal: bool = False) -> dict:
    train_items = items_in_split(pack, "train")
    train_rows = dedupe(
        build_sft_rows(pack, train_items, retrievers, draws=3, train=True, seed=seed)
        + build_counterfactual_rows(pack, train_items, retrievers, seed=seed)
        + (build_removal_rows(pack, train_items, retrievers, seed=seed, draws=3) if removal else [])
    )
    triplets = build_triplets(pack, train_items, retrievers)
    count = (lambda s: len(tokenizer(s).input_ids)) if tokenizer else None
    report = checks.run_all(pack, train_items, train_rows, triplets, count)
    if tokenizer is not None:                          # tokens must survive a round trip
        for r in random.Random(seed).sample(train_rows, min(300, len(train_rows))):
            ids = tokenizer(text_target=r.target).input_ids
            if tokenizer.decode(ids, skip_special_tokens=True) != r.target:
                raise checks.DataCheckError(f"{r.example_id}: target does not survive tokenising")
    log(f"[rows] train rows={len(train_rows)} checks={report}")

    # Checkpoint choice and calibration use the unseen-policy validation organisations only.
    # The "val" split of training organisations shares snippets with training, so it is too
    # easy and would make thresholds optimistic. It is kept for reporting.
    val_items = items_in_split(pack, "val_unseen")
    s1 = items_in_split(pack, "test_s1")
    s2 = items_in_split(pack, "test_s2")
    rng = random.Random(seed + 1)
    s6_src = rng.sample(s1, min(120, len(s1))) + rng.sample(s2, min(120, len(s2)))
    s6 = [injection_variant(i, rng, INJECTION_TEST) for i in s6_src]

    def ev(items: list[Item]) -> list[Row]:
        return build_sft_rows(pack, items, retrievers, draws=1, train=False, seed=seed)

    probes = {}
    for name, its in (("s1", s1), ("s2", s2)):
        probes[f"{name}_rm"] = build_removal_rows(pack, its, retrievers, seed=seed + 5)
    return {"train": train_rows, "triplets": triplets, "checks": report, **probes,
            "val": ev(val_items), "val_seen": ev(items_in_split(pack, "val")),
            "s1": ev(s1), "s2": ev(s2), "s6": ev(s6)}


def stage_t2(rows: dict, clauses_by_id: dict, epochs: int, seed: int, lr: float, log=print) -> dict:
    from .generator_train import finetune_generator

    info = finetune_generator(rows["train"], rows["val"], clauses_by_id, MODELS / "generator",
                              epochs=epochs, seed=seed, lr=lr, log=log)
    (RESULTS / "t2_generator.json").parent.mkdir(parents=True, exist_ok=True)
    (RESULTS / "t2_generator.json").write_text(json.dumps(info, indent=2))
    return info


def stage_t3(rows: dict, clauses_by_id: dict, log=print) -> Calibration:
    from c3.generation.t5 import T5Generator

    from .calibrate import calibrate

    gen = T5Generator(MODELS / "generator", device="cuda")
    ev = evaluate_rows(rows["val"], gen, clauses_by_id, Calibration(), decode=False)
    calib, report = calibrate(rows["val"], ev["gaps"], clauses_by_id)
    (MODELS / "generator" / "calibration.json").write_text(json.dumps(calib.to_json(), indent=2))
    (RESULTS / "t3_calibration.json").write_text(json.dumps(report, indent=2))
    log(f"[t3] T={calib.temperature:.3f} sev_tau={calib.sev_tau} "
        f"ece {report['ece_before']:.3f}->{report['ece_after']:.3f} val={report['val_after']}")
    return calib


def breakdown(rows: list[Row], final_pred: list[int]) -> dict:
    """Metrics per data source, so real and generated data are never blended into one number."""
    from .metrics import prf

    out = {}
    for src in sorted({r.source for r in rows}):
        idx = [i for i, r in enumerate(rows) if r.source == src]
        y = [rows[i].label for i in idx]
        m = prf(y, [final_pred[i] for i in idx])
        out[src or "unknown"] = {"n": len(idx), "violating": int(sum(y)),
                                 "f1": m["f1"], "recall": m["recall"], "fpr": m["fpr"],
                                 "precision": m["precision"]}
    return out


def keyword_baseline(rows_val: list[Row], rows_eval: list[Row], clauses_by_id: dict) -> dict:
    """B0: overlap of content words between the response and the best context clause."""
    import re

    from .evaluate import context_from_row
    from .metrics import choose_threshold, prf

    stop = set("the a an and or of to in is are be must not for with on that this it as by at "
               "any all may no do does".split())

    def toks(s: str) -> set[str]:
        return {w for w in re.findall(r"[a-z]+", s.lower()) if w not in stop and len(w) > 2}

    def score(r: Row) -> float:
        resp = toks(r.input.split("\nRESPONSE:\n", 1)[1])
        best = 0.0
        for it in context_from_row(r, clauses_by_id).values():
            ct = toks(it.text)
            if ct:
                best = max(best, len(resp & ct) / len(ct))
        return best

    t = choose_threshold([score(r) for r in rows_val], [r.label for r in rows_val])
    pred = [int(score(r) >= t) for r in rows_eval]
    m = prf([r.label for r in rows_eval], pred)
    return {"threshold": t, "f1": m["f1"], "precision": m["precision"], "recall": m["recall"],
            "fpr": m["fpr"]}


def stage_eval(pack: Pack, rows: dict, clauses_by_id: dict, calib: Calibration,
               log=print) -> dict:
    from c3.generation.t5 import T5Generator

    from .metrics import mcnemar

    trained = T5Generator(MODELS / "generator", device="cuda", temperature=calib.temperature)
    untrained = T5Generator("google/flan-t5-small", device="cuda")
    res: dict = {"created": time.strftime("%Y-%m-%dT%H:%M:%S"), "calibration": calib.to_json()}
    for name in ("s1", "s2", "s6"):
        t = evaluate_rows(rows[name], trained, clauses_by_id, calib)
        u = evaluate_rows(rows[name], untrained, clauses_by_id, Calibration())
        res[name] = {
            "trained": t["metrics"], "untrained_B3": u["metrics"],
            "by_source": breakdown(rows[name], t["final_pred"]),
            "mcnemar_trained_vs_untrained": mcnemar(t["correct"], u["correct"]),
            "keyword_B0": keyword_baseline(rows["val"], rows[name], clauses_by_id),
        }
        log(f"[eval] {name}: trained F1={t['metrics']['f1']:.3f} "
            f"B3 F1={u['metrics']['f1']:.3f} B0 F1={res[name]['keyword_B0']['f1']:.3f}")
    # Rule dependence: of the violations it caught, how many does it let go when the broken rule
    # is removed from the context? A model that reads the rules should drop nearly all of them.
    for name in ("s1", "s2"):
        rm = rows.get(f"{name}_rm") or []
        if not rm:
            continue
        base = evaluate_rows(rows[name], trained, clauses_by_id, calib, decode=False)
        caught = {r.item_id for r, p in zip(rows[name], base["final_pred"], strict=True)
                  if p and r.label and r.gold_in_context}
        rm = [r for r in rm if r.item_id in caught]
        if rm:
            after = evaluate_rows(rm, trained, clauses_by_id, calib, decode=False)
            flipped = sum(1 for p in after["final_pred"] if not p)
            res[name]["rule_dependence"] = {"violations_caught": len(caught), "tested": len(rm),
                                            "let_go_when_rule_removed": flipped / len(rm)}
            log(f"[eval] {name} rule dependence: {flipped}/{len(rm)} let go without the rule")
    s1_shuf = shuffled_copy(rows["s1"], clauses_by_id, seed=3)
    res["order_consistency_s1"] = order_consistency(
        rows["s1"], s1_shuf, trained, clauses_by_id, calib
    )
    return res


def write_report(res: dict, t1: dict | None, t2: dict | None) -> None:
    lines = ["# C3 v0 training results", "",
             "> Synthetic data (silver labels by construction). These numbers show that the "
             "pipeline works. They are not a research claim until real answers are labelled.", ""]
    if t1:
        lines += ["## Retriever (recall of the gold clause)", "",
                  "| Split | recall@5 untuned | recall@5 tuned |", "|---|---|---|"]
        for sp in ("val", "test_s1", "val_unseen", "test_s2"):
            lines.append(f"| {sp} | {t1['recall_before'][sp]['recall@5']:.3f} | "
                         f"{t1['recall_after'][sp]['recall@5']:.3f} |")
        lines.append("")
    lines += ["## Generator", "",
              "| Set | System | F1 | 95% CI | Recall | FPR | Attribution top-1 | "
              "Raw citation validity | Repair rate |",
              "|---|---|---|---|---|---|---|---|---|"]
    for name in ("s1", "s2", "s6"):
        for label, key in (("C3 trained", "trained"), ("B3 untrained", "untrained_B3")):
            m = res[name][key]
            ci = m["f1_ci95"]
            lines.append(
                f"| {name.upper()} | {label} | {m['f1']:.3f} | {ci[0]:.3f} to {ci[1]:.3f} | "
                f"{m['recall']:.3f} | {m['fpr']:.3f} | {m['attribution_top1']:.3f} | "
                f"{m['raw_citation_validity']:.3f} | {m['repair_rate']:.3f} |")
        b0 = res[name]["keyword_B0"]
        lines.append(f"| {name.upper()} | B0 keyword | {b0['f1']:.3f} | | {b0['recall']:.3f} | "
                     f"{b0['fpr']:.3f} | | | |")
    for name in ("s1", "s2"):
        rd = res[name].get("rule_dependence")
        if rd:
            share = f"{rd['let_go_when_rule_removed']:.0%}"
            lines.append(f"Rule dependence on {name.upper()}: {share} of {rd['tested']} caught "
                         "violations are let go when the broken rule is removed from the "
                         "context (higher is better).")
    lines += ["", f"Order consistency on S1 (shuffled labels, same verdict): "
              f"{res['order_consistency_s1']:.3f}", "",
              f"Calibration: temperature {res['calibration']['temperature']:.3f}, "
              f"thresholds {res['calibration']['sev_tau']}", ""]
    (RESULTS / "RESULTS.md").write_text("\n".join(lines), encoding="utf-8")


def write_manifest(pack: Pack, seed: int, extra: dict) -> None:
    MODELS.mkdir(parents=True, exist_ok=True)
    trained_hashes = sorted({c.content_hash for o in pack.orgs.values() if o.role == "train"
                             for c in o.clauses})
    manifest = {
        "data_version": (f"public-v1-seed{pack.seed}"
                         if any(i.source == "aegis2" for i in pack.items)
                         else f"synthetic-v0-seed{pack.seed}"), "seed": seed,
        "retriever": dir_hashes(MODELS / "retriever") if (MODELS / "retriever").exists() else {},
        "generator": dir_hashes(MODELS / "generator") if (MODELS / "generator").exists() else {},
        "trained_clause_hashes_sha256": hashlib.sha256(
            "".join(trained_hashes).encode()
        ).hexdigest(),
        "trained_clause_count": len(trained_hashes), **extra,
    }
    (MODELS / "manifest.json").write_text(json.dumps(manifest, indent=2))
    (MODELS / "trained_clause_hashes.json").write_text(json.dumps(trained_hashes))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", default="all",
                    choices=["all", "data", "t1", "rows", "t2", "t3", "eval"])
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--epochs-ret", type=int, default=3)
    ap.add_argument("--epochs-gen", type=int, default=8)
    ap.add_argument("--lr-gen", type=float, default=3e-4)
    ap.add_argument("--data", choices=["public", "synthetic"], default="public",
                    help="public = real policies and answers (default); synthetic = hand-written")
    ap.add_argument("--gen-file", type=Path, default=DATA / "processed" / "generated_answers.jsonl")
    a = ap.parse_args()
    RESULTS.mkdir(parents=True, exist_ok=True)

    MODELS.mkdir(parents=True, exist_ok=True)
    pack = (stage_data_public(a.seed, a.gen_file) if a.data == "public" else stage_data(a.seed))
    cl = all_clauses(pack)
    t1 = None
    if a.stage in ("all", "t1"):
        t1 = stage_t1(pack, a.epochs_ret, a.seed)
    elif (RESULTS / "t1_retriever.json").exists():
        t1 = json.loads((RESULTS / "t1_retriever.json").read_text())
    if a.stage == "t1":
        return

    from transformers import AutoTokenizer

    tok = AutoTokenizer.from_pretrained("google/flan-t5-small")
    retrievers = build_retrievers(pack, make_embedder("tuned", MODELS / "retriever"))
    rows = build_all_rows(pack, retrievers, a.seed, tok,
                          removal=True)
    if a.stage == "rows":
        return
    t2 = None
    if a.stage in ("all", "t2"):
        t2 = stage_t2(rows, cl, a.epochs_gen, a.seed, a.lr_gen)
    if a.stage == "t2":
        write_manifest(pack, a.seed, {"stage": "t2"})
        return
    calib = (stage_t3(rows, cl) if a.stage in ("all", "t3")
             else Calibration.from_json(json.loads(
                 (MODELS / "generator" / "calibration.json").read_text())))
    if a.stage == "t3":
        return
    res = stage_eval(pack, rows, cl, calib)
    (RESULTS / "evaluation.json").write_text(json.dumps(res, indent=2))
    write_report(res, t1, t2)
    write_manifest(pack, a.seed, {"stage": "complete", "metrics": "results/evaluation.json"})
    print((RESULTS / "RESULTS.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
