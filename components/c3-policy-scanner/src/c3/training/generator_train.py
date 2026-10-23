"""T2: fine-tune flan-t5-small on generator rows (blueprint 12.9, 13.2).

Design notes
  * fp32 weights with bf16 autocast. T5 can overflow in fp16.
  * No label smoothing: it flattens the logit gap the verdict probability depends on.
  * The checkpoint is chosen by validation F1 (from the stage-1 probability) and raw citation
    validity, not by loss alone.
"""
from __future__ import annotations

import json
import random
import time
from collections.abc import Sequence
from pathlib import Path

from c3.generation.t5 import T5Generator

from .evaluate import Calibration, evaluate_rows
from .metrics import auc, best_f1
from .rows import Row

DEFAULT_BASE = "google/flan-t5-small"


def finetune_generator(
    train_rows: Sequence[Row],
    val_rows: Sequence[Row],
    clauses_by_id: dict,
    out_dir: str | Path,
    *,
    base: str = DEFAULT_BASE,
    epochs: int = 8,
    lr: float = 3e-4,
    batch_size: int = 8,
    grad_accum: int = 2,
    max_source: int = 768,
    max_target: int = 64,
    seed: int = 42,
    device: str | None = None,
    patience: int = 5,
    min_epochs: int = 6,
    verdict_weight: float = 5.0,
    log=print,
) -> dict:
    import torch
    import torch.nn.functional as F
    from transformers import AutoModelForSeq2SeqLM, AutoTokenizer

    random.seed(seed)
    torch.manual_seed(seed)
    device = device or ("cuda" if torch.cuda.is_available() else "cpu")
    # bf16 where the GPU supports it (RTX 30xx/40xx, A100). The Colab T4 does not: train in fp32.
    use_bf16 = device == "cuda" and torch.cuda.is_bf16_supported()
    tok = AutoTokenizer.from_pretrained(base)
    model = AutoModelForSeq2SeqLM.from_pretrained(base).to(device)

    # The verdict word is one token among about 40. Weight it so its loss is not diluted.
    verdict_pos = len(tok("verdict:", add_special_tokens=False).input_ids)

    enc_rows = []
    for r in train_rows:
        x = tok(r.input, max_length=max_source, truncation=True)["input_ids"]
        y = tok(text_target=r.target, max_length=max_target, truncation=True)["input_ids"]
        enc_rows.append((x, y))

    def collate(batch):
        mx = max(len(x) for x, _ in batch)
        my = max(len(y) for _, y in batch)
        ids = torch.full((len(batch), mx), tok.pad_token_id)
        att = torch.zeros((len(batch), mx), dtype=torch.long)
        lab = torch.full((len(batch), my), -100)
        for i, (x, y) in enumerate(batch):
            ids[i, : len(x)] = torch.tensor(x)
            att[i, : len(x)] = 1
            lab[i, : len(y)] = torch.tensor(y)
        return ids.to(device), att.to(device), lab.to(device)

    opt = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=0.01)
    steps_per_epoch = (len(enc_rows) + batch_size * grad_accum - 1) // (batch_size * grad_accum)
    total_steps = steps_per_epoch * epochs
    warm = max(1, int(0.05 * total_steps))
    sched = torch.optim.lr_scheduler.LambdaLR(
        opt, lambda s: min(1.0, (s + 1) / warm) * max(0.05, 1 - s / total_steps)
    )

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    history, best, bad = [], -1.0, 0
    t_start = time.time()
    for ep in range(1, epochs + 1):
        model.train()
        random.shuffle(enc_rows)
        total, count = 0.0, 0
        for step, i in enumerate(range(0, len(enc_rows), batch_size)):
            ids, att, lab = collate(enc_rows[i : i + batch_size])
            with torch.autocast(device_type="cuda", dtype=torch.bfloat16, enabled=use_bf16):
                logits = model(input_ids=ids, attention_mask=att, labels=lab).logits
            tok_loss = F.cross_entropy(
                logits.float().view(-1, logits.size(-1)), lab.view(-1), ignore_index=-100,
                reduction="none",
            ).view(lab.shape)
            w = (lab != -100).float()
            w[:, verdict_pos] *= verdict_weight
            loss = (tok_loss * w).sum() / w.sum()
            (loss / grad_accum).backward()
            total += float(loss) * len(ids)
            count += len(ids)
            if (step + 1) % grad_accum == 0:
                torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
                opt.step()
                sched.step()
                opt.zero_grad()
        opt.step() if (step + 1) % grad_accum else None
        opt.zero_grad()

        model.eval()
        gen = T5Generator(model=model, tok=tok, device=device, max_input_tokens=max_source)
        res = evaluate_rows(val_rows, gen, clauses_by_id, Calibration(), batch_size=16)
        ev = res["metrics"]
        gaps = res["gaps"]
        labels = [r.label for r in val_rows]
        v_auc, v_best = auc(gaps, labels), best_f1(gaps, labels)
        sub = {}
        for name in ("val", "val_unseen"):
            idx = [i for i, r in enumerate(val_rows) if r.split == name]
            if idx:
                sub[name] = round(auc([gaps[i] for i in idx], [labels[i] for i in idx]), 4)
        raw = ev["raw_citation_validity"]
        raw0 = 0.0 if raw != raw else raw
        score = 0.4 * v_auc + 0.4 * v_best + 0.2 * raw0
        if score != score:                 # no violations in validation: still keep a checkpoint
            score = 0.0
        rec = {"epoch": ep, "train_loss": total / count, "val_auc": v_auc, "val_best_f1": v_best,
               "val_auc_by_subset": sub, "val_f1_at_default": ev["f1"],
               "val_raw_citation_validity": raw, "val_attribution_top1": ev["attribution_top1"],
               "score": score, "minutes": (time.time() - t_start) / 60}
        history.append(rec)
        log(f"[generator] epoch {ep}/{epochs} loss={rec['train_loss']:.4f} auc={v_auc:.3f} "
            f"{sub} best_f1={v_best:.3f} raw_valid={raw:.3f} score={score:.3f}")
        if score > best:
            best, bad = score, 0
            model.save_pretrained(out_dir)
            tok.save_pretrained(out_dir)
            (out_dir / "best_epoch.json").write_text(json.dumps(rec, indent=2))
        else:
            bad += 1
            if bad >= patience and ep >= min_epochs:
                log(f"[generator] early stop after epoch {ep}")
                break
    return {"base": base, "epochs_run": len(history), "lr": lr, "batch": batch_size * grad_accum,
            "seed": seed, "rows": len(train_rows), "best_score": best, "history": history}
