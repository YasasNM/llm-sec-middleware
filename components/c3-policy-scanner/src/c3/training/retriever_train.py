"""T1: fine-tune the bi-encoder retriever on triplets (blueprint 12.2, 13.2).

Loss: multiple-negatives ranking with one hard negative per row. The loop is written on
plain `transformers` (mean pooling), so it matches `TransformerEmbedder` exactly and avoids
the sentence-transformers package.
"""
from __future__ import annotations

import random
from collections.abc import Sequence
from pathlib import Path

DEFAULT_BASE = "sentence-transformers/all-MiniLM-L6-v2"


def finetune_retriever(
    triplets: Sequence[dict],
    out_dir: str | Path,
    *,
    base: str = DEFAULT_BASE,
    epochs: int = 2,
    lr: float = 2e-5,
    batch_size: int = 32,
    scale: float = 20.0,
    max_seq_length: int = 256,
    seed: int = 42,
    device: str | None = None,
    log=print,
) -> dict:
    import torch
    import torch.nn.functional as F
    from transformers import AutoModel, AutoTokenizer

    random.seed(seed)
    torch.manual_seed(seed)
    device = device or ("cuda" if torch.cuda.is_available() else "cpu")
    # bf16 where the GPU supports it (RTX 30xx/40xx, A100). The Colab T4 does not: train in fp32.
    use_bf16 = device == "cuda" and torch.cuda.is_bf16_supported()
    tok = AutoTokenizer.from_pretrained(base)
    model = AutoModel.from_pretrained(base).to(device)
    model.train()
    opt = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=0.01)
    steps_total = max(1, epochs * ((len(triplets) + batch_size - 1) // batch_size))
    warm = max(1, int(0.1 * steps_total))
    sched = torch.optim.lr_scheduler.LambdaLR(
        opt, lambda s: min(1.0, (s + 1) / warm) * max(0.0, 1 - s / steps_total)
    )

    def embed(texts: list[str]):
        enc = tok(texts, padding=True, truncation=True, max_length=max_seq_length,
                  return_tensors="pt").to(device)
        hidden = model(**enc).last_hidden_state
        mask = enc["attention_mask"].unsqueeze(-1).to(hidden.dtype)
        pooled = (hidden * mask).sum(1) / mask.sum(1).clamp(min=1e-9)
        return F.normalize(pooled, dim=-1)

    history = []
    rows = list(triplets)
    for ep in range(epochs):
        random.shuffle(rows)
        total, n = 0.0, 0
        for i in range(0, len(rows), batch_size):
            b = rows[i : i + batch_size]
            if len(b) < 2:
                continue
            with torch.autocast(device_type="cuda", dtype=torch.bfloat16, enabled=use_bf16):
                a = embed([r["anchor"] for r in b])
                pos = embed([r["positive"] for r in b])
                neg = embed([random.choice(r["negatives"]) if r["negatives"] else r["positive"]
                             for r in b])
            cands = torch.cat([pos, neg], dim=0).float()
            scores = a.float() @ cands.T * scale
            loss = F.cross_entropy(scores, torch.arange(len(b), device=device))
            opt.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt.step()
            sched.step()
            total += float(loss) * len(b)
            n += len(b)
        history.append({"epoch": ep + 1, "loss": total / max(1, n)})
        log(f"[retriever] epoch {ep + 1}/{epochs} loss={total / max(1, n):.4f}")
    model.eval()
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    model.save_pretrained(str(out_dir))
    tok.save_pretrained(str(out_dir))
    return {"base": base, "epochs": epochs, "lr": lr, "batch_size": batch_size, "seed": seed,
            "triplets": len(triplets), "history": history}
