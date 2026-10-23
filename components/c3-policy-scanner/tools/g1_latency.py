"""Gate G1: CPU latency of the generator (blueprint 13.5).

Measures, on this machine and on CPU only:
  G1a encoder pass, G1b stage 1 total (encoder + verdict step), G1c greedy decode of up to 30
  new tokens, G1d full violation path (retrieve + stage 1 + stage 2 + grounding), and the
  same with int8 dynamic quantisation (G1e). Pass-path scans stop after stage 1.

    python tools/g1_latency.py --runs 30 --threads 4 --out results/g1_latency.json

Latency does not depend on trained weights, but the prompt length does, so real prompts from
the synthetic pack are used. Report the machine (CPU model, threads) with every result.
"""
from __future__ import annotations

import argparse
import json
import platform
import statistics
import time
from pathlib import Path

LIMITS_MS = {"G1a_encoder": 350, "G1b_stage1": 400, "G1c_decode30": 550, "G1d_violation_path": 1000}


def pct(xs, q):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(q * (len(xs) - 1))))]


def main() -> None:
    import torch

    from c3.generation.rag_format import finalize, parse_output
    from c3.generation.t5 import T5Generator
    from c3.training.pipeline import MODELS, all_clauses, build_all_rows, make_embedder  # noqa: F401
    from c3.training.rows import build_retrievers
    from c3.training.synth import build_pack

    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=30)
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--model", default=str(MODELS / "generator"))
    ap.add_argument("--out", type=Path, default=None)
    a = ap.parse_args()
    torch.set_num_threads(a.threads)

    pack = build_pack(7)
    clauses = all_clauses(pack)
    embedder = make_embedder("tuned", MODELS / "retriever")
    embedder.model.to("cpu")
    embedder.device = "cpu"
    retrievers = build_retrievers(pack, embedder)

    from c3.training.rows import build_sft_rows, items_in_split

    items = items_in_split(pack, "test_s2")
    viol = [i for i in items if i.label][: a.runs]
    clean = [i for i in items if not i.label][: a.runs]
    rows_v = build_sft_rows(pack, viol, retrievers, draws=1, train=False, seed=1)
    rows_c = build_sft_rows(pack, clean, retrievers, draws=1, train=False, seed=1)

    report: dict = {"machine": {"cpu": platform.processor(), "threads": a.threads,
                                "torch": torch.__version__}}
    for name, quant in (("fp32", False), ("int8_dynamic", True)):
        gen = T5Generator(a.model, device="cpu")
        if quant:
            gen.model = torch.quantization.quantize_dynamic(
                gen.model, {torch.nn.Linear}, dtype=torch.qint8
            )
        # warm up
        for r in rows_v[:3]:
            gen.stage2(gen.stage1(r.input))
        enc_ms, s1_ms, dec_ms, viol_ms, pass_ms, tokens = [], [], [], [], [], []
        for r in rows_v:
            t0 = time.perf_counter()
            enc = gen.tok(r.input, return_tensors="pt", truncation=True, max_length=768)
            t = time.perf_counter()
            with torch.no_grad():
                gen.model.get_encoder()(**enc)
            enc_ms.append((time.perf_counter() - t) * 1000)
            s1 = gen.stage1(r.input)
            s1_ms.append(s1.ms)
            text, d_ms = gen.stage2(s1)
            dec_ms.append(d_ms)
            tokens.append(len(gen.tok(text).input_ids))
            from c3.training.evaluate import context_from_row

            finalize(parse_output(text), context_from_row(r, clauses), gen.probability(s1.gap), 0.4)
            viol_ms.append((time.perf_counter() - t0) * 1000)
        for r in rows_c:
            t0 = time.perf_counter()
            gen.stage1(r.input)
            pass_ms.append((time.perf_counter() - t0) * 1000)
        res = {
            "G1a_encoder": {"p50": statistics.median(enc_ms), "p95": pct(enc_ms, 0.95)},
            "G1b_stage1": {"p50": statistics.median(s1_ms), "p95": pct(s1_ms, 0.95)},
            "G1c_decode": {"p50": statistics.median(dec_ms), "p95": pct(dec_ms, 0.95),
                           "median_tokens": statistics.median(tokens)},
            "G1d_violation_path_model_only": {"p50": statistics.median(viol_ms),
                                              "p95": pct(viol_ms, 0.95)},
            "pass_path_model_only": {"p50": statistics.median(pass_ms), "p95": pct(pass_ms, 0.95)},
            "prompt_tokens_median": statistics.median(
                len(gen.tok(r.input).input_ids) for r in rows_v),
        }
        res["verdict_vs_limits"] = {
            "G1a": res["G1a_encoder"]["p95"] <= LIMITS_MS["G1a_encoder"],
            "G1b": res["G1b_stage1"]["p95"] <= LIMITS_MS["G1b_stage1"],
            "G1c": res["G1c_decode"]["p95"] <= LIMITS_MS["G1c_decode30"],
            "G1d": res["G1d_violation_path_model_only"]["p95"] <= LIMITS_MS["G1d_violation_path"],
        }
        report[name] = res
        print(name, json.dumps(res, indent=2))
    if a.out:
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
