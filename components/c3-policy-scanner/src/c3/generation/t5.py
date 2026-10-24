"""flan-t5 generator wrapper: stage 1 (verdict logits) and stage 2 (cited labels, quote, reason).

Needs torch and transformers (`pip install .[models]`). Imports are lazy so the rest of C3
runs without them.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

VERDICT_PREFIX_TEXT = "verdict:"
VIOLATION_PREFIX_TEXT = "verdict: violation | cited:"


@dataclass
class Stage1:
    gap: float                      # logit(violation) - logit(pass)
    enc: object                     # tokenizer output, kept so stage 2 does not re-read the prompt
    enc_out: object
    ms: float


class StartupCheckError(RuntimeError):
    pass


class T5Generator:
    def __init__(self, model_dir: str | Path | None = None, device: str = "cpu", dtype=None,
                 max_input_tokens: int = 768, temperature: float = 1.0, *, model=None, tok=None):
        import torch
        from transformers import AutoModelForSeq2SeqLM, AutoTokenizer

        self.torch = torch
        if model is None:
            tok = AutoTokenizer.from_pretrained(str(model_dir))
            model = AutoModelForSeq2SeqLM.from_pretrained(str(model_dir), torch_dtype=dtype)
        self.tok, self.model = tok, model
        self.model.to(device).eval()
        self.device = device
        self.max_input_tokens = max_input_tokens
        self.temperature = temperature
        self.version = "in-memory"
        if model_dir and Path(str(model_dir)).is_dir():
            from c3.indexing.embedder import _dir_fingerprint

            self.version = f"c3-gen-flan-t5-small-{_dir_fingerprint(Path(str(model_dir)))}"
        elif model_dir:
            self.version = str(model_dir)
        self._startup_check()

    # ---- start-up check (blueprint 12.4): do not skip it --------------------------------
    def _startup_check(self) -> None:
        tok = self.tok
        self.prefix = tok(VERDICT_PREFIX_TEXT, add_special_tokens=False).input_ids
        self.v_id = tok("violation", add_special_tokens=False).input_ids[0]
        self.p_id = tok("pass", add_special_tokens=False).input_ids[0]
        if self.v_id == self.p_id:
            raise StartupCheckError("'violation' and 'pass' start with the same token")
        for text, want in (("verdict: violation | cited: C1", self.v_id),
                           ("verdict: pass | cited: none", self.p_id)):
            ids = tok(text, add_special_tokens=False).input_ids
            if ids[: len(self.prefix) + 1] != self.prefix + [want]:
                raise StartupCheckError(
                    "the forced prefix does not match how the target text is tokenised; "
                    "change the label words and retrain"
                )
        self.start_id = self.model.config.decoder_start_token_id

    # ---- stage 1 --------------------------------------------------------------------------
    def stage1(self, prompt: str) -> Stage1:
        torch = self.torch
        t = time.perf_counter()
        with torch.no_grad():
            enc = self.tok(prompt, return_tensors="pt", truncation=True,
                           max_length=self.max_input_tokens).to(self.device)
            enc_out = self.model.get_encoder()(**enc)
            dec = torch.tensor([[self.start_id] + self.prefix], device=self.device)
            logits = self.model(encoder_outputs=enc_out, attention_mask=enc["attention_mask"],
                                decoder_input_ids=dec).logits[0, -1]
        gap = float(logits[self.v_id] - logits[self.p_id])
        return Stage1(gap, enc, enc_out, (time.perf_counter() - t) * 1000)

    def stage1_batch(self, prompts: list[str]) -> list[float]:
        """Logit gaps for many prompts at once (training-time evaluation only)."""
        torch = self.torch
        enc = self.tok(prompts, return_tensors="pt", truncation=True, padding=True,
                       max_length=self.max_input_tokens).to(self.device)
        dec = torch.tensor([[self.start_id] + self.prefix] * len(prompts), device=self.device)
        with torch.no_grad():
            logits = self.model(**enc, decoder_input_ids=dec).logits[:, -1]
        return (logits[:, self.v_id] - logits[:, self.p_id]).float().cpu().tolist()

    def probability(self, gap: float) -> float:
        import math

        return 1.0 / (1.0 + math.exp(-max(-50.0, min(50.0, gap / self.temperature))))

    # ---- stage 2 --------------------------------------------------------------------------
    def stage2(self, s1: Stage1, max_new_tokens: int = 40) -> tuple[str, float]:
        torch = self.torch
        t = time.perf_counter()
        prefix = self.tok(VIOLATION_PREFIX_TEXT, add_special_tokens=False).input_ids
        forced = torch.tensor([[self.start_id] + prefix], device=self.device)
        with torch.no_grad():
            out = self.model.generate(
                encoder_outputs=s1.enc_out, attention_mask=s1.enc["attention_mask"],
                decoder_input_ids=forced, max_new_tokens=max_new_tokens, do_sample=False,
            )
        return (self.tok.decode(out[0], skip_special_tokens=True),
                (time.perf_counter() - t) * 1000)

    def generate_batch(self, prompts: list[str], max_new_tokens: int = 40) -> list[str]:
        """Free generation with the violation prefix forced, for training-time evaluation."""
        torch = self.torch
        enc = self.tok(prompts, return_tensors="pt", truncation=True, padding=True,
                       max_length=self.max_input_tokens).to(self.device)
        prefix = self.tok(VIOLATION_PREFIX_TEXT, add_special_tokens=False).input_ids
        forced = torch.tensor([[self.start_id] + prefix] * len(prompts), device=self.device)
        with torch.no_grad():
            out = self.model.generate(**enc, decoder_input_ids=forced,
                                      max_new_tokens=max_new_tokens, do_sample=False)
        return self.tok.batch_decode(out, skip_special_tokens=True)
