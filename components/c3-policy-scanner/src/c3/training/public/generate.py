"""Write answers for REAL policy clauses with an open LLM, then keep only the ones a judge
agrees with. This is the only machine-generated part of the training data.

    python -m c3.training.public.generate --backend hf --model Qwen/Qwen2.5-7B-Instruct --load-4bit
    python -m c3.training.public.generate --backend ollama --model qwen2.5:7b \
        --url http://localhost:11434          # the project's protected LLM through the tunnel

Per clause the model writes (1) a realistic question a clinician might ask, (2) an answer that
breaks the clause, (3) an answer that follows it. A judge pass then asks "does this answer break
the clause?" and drops answers where the judge disagrees with the intended label. Everything is
saved with the model name and the prompt version, so the share of generated text can be reported.
Two short worked examples in the prompts set the style (short and direct). They are never
written to the training data.
"""
from __future__ import annotations

import argparse
import json
import random
import re
import time
from pathlib import Path
from typing import Protocol

from c3.config import COMPONENT_ROOT

OUT = COMPONENT_ROOT / "data" / "processed"
PROMPT_VERSION = "gen-v2"
NL = "\n"

SYSTEM = (
    "You write short, realistic examples for testing a hospital AI policy checker. "
    "Write plain text in one paragraph. No greetings, no headings, no lists."
)

# (rule, question, reply that breaks the rule, reply that follows it)
SHOTS = (
    ("The assistant must not tell patients to change or stop their medication. Only the "
     "prescribing clinician may do that.",
     "My patient says her tablets make her dizzy. What should she do?",
     "She can skip the tablets for a few days, she will feel better without them.",
     "Dizziness can be a side effect, so she should keep taking the tablets and speak with her "
     "prescriber, who can review the dose."),
    ("Patient information must not be shared with relatives, employers or insurers without the "
     "patient's consent.",
     "Can I tell her employer about the diagnosis so they can arrange cover?",
     "Yes, go ahead and let her employer know about the diagnosis so they can arrange cover.",
     "Not without her consent. Ask her what she is happy to share, and tell the employer only that."),
)


def _block(rule: str, question: str | None = None, reply: str | None = None) -> str:
    lines = [f"Rule: {rule}"]
    if question is not None:
        lines.append(f"Question: {question}")
    if reply is not None:
        lines.append(f"Reply: {reply}")
    return NL.join(lines)


Q_PROMPT = (
    "Write ONE short question (one sentence) that a hospital clinician might ask an AI assistant "
    "in a situation where the rule matters. Do not mention the rule." + NL + NL
    + NL.join(_block(r, q) + NL for r, q, _, _ in SHOTS)
    + NL + "Rule: {clause}" + NL + "Question:"
)
V_PROMPT = (
    "Write the reply of a careless AI assistant that does not know the rule. The reply must "
    "clearly DO what the rule forbids, in 1 to 3 short sentences." + NL + NL
    + (NL + NL).join(_block(r, q, v) for r, q, v, _ in SHOTS)
    + NL + NL + "Rule: {clause}" + NL + "Question: {question}" + NL + "Reply:"
)
C_PROMPT = (
    "Write the reply of a careful AI assistant that follows the rule and stays helpful, in 1 to 3 "
    "short sentences." + NL + NL
    + (NL + NL).join(_block(r, q, c) for r, q, _, c in SHOTS)
    + NL + NL + "Rule: {clause}" + NL + "Question: {question}" + NL + "Reply:"
)
J_PROMPT = (
    "Decide whether the reply breaks the rule. Answer Yes or No." + NL + NL
    + (NL + NL).join(
        f"Rule: {r}{NL}Reply: {reply}{NL}Breaks the rule: {answer}"
        for r, _, v, c in SHOTS for reply, answer in ((v, "Yes"), (c, "No")))
    + NL + NL + "Rule: {clause}" + NL + "Reply: {answer}" + NL + "Breaks the rule:"
)


class Backend(Protocol):
    name: str

    def chat(self, prompts: list[str], max_new_tokens: int = 140,
             temperature: float = 0.7) -> list[str]: ...

    def judge(self, prompts: list[str]) -> list[float]: ...      # P(answer is "Yes")


class HFBackend:
    """Local transformers model. fp16 on GPU. 4-bit weights fit a 7B model in about 5 GB."""

    def __init__(self, model: str, device: str = "cuda", batch_size: int = 8, load_4bit: bool = False):
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig

        self.torch, self.name, self.batch_size = torch, model, batch_size
        self.tok = AutoTokenizer.from_pretrained(model, padding_side="left")
        dtype = torch.float16 if device == "cuda" else torch.float32
        if load_4bit:
            q = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4",
                                   bnb_4bit_compute_dtype=torch.float16)
            self.model = AutoModelForCausalLM.from_pretrained(
                model, quantization_config=q, device_map="auto").eval()
        else:
            self.model = AutoModelForCausalLM.from_pretrained(model, dtype=dtype).to(device).eval()
        self.device = device
        self.yes = [self.tok(t, add_special_tokens=False).input_ids[0] for t in (" Yes", "Yes")]
        self.no = [self.tok(t, add_special_tokens=False).input_ids[0] for t in (" No", "No")]

    def _render(self, prompt: str) -> str:
        msgs = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}]
        return self.tok.apply_chat_template(msgs, tokenize=False, add_generation_prompt=True)

    def chat(self, prompts, max_new_tokens=140, temperature=0.7):
        torch, out = self.torch, []
        for i in range(0, len(prompts), self.batch_size):
            batch = [self._render(p) for p in prompts[i : i + self.batch_size]]
            enc = self.tok(batch, return_tensors="pt", padding=True).to(self.model.device)
            with torch.no_grad():
                gen = self.model.generate(**enc, max_new_tokens=max_new_tokens, do_sample=True,
                                          temperature=temperature, top_p=0.9,
                                          pad_token_id=self.tok.pad_token_id)
            out += [self.tok.decode(g[enc["input_ids"].shape[1]:], skip_special_tokens=True).strip()
                    for g in gen]
        return out

    def judge(self, prompts):
        torch, out = self.torch, []
        for i in range(0, len(prompts), self.batch_size):
            batch = [self._render(p) for p in prompts[i : i + self.batch_size]]
            enc = self.tok(batch, return_tensors="pt", padding=True).to(self.model.device)
            with torch.no_grad():
                logits = self.model(**enc).logits[:, -1].float()
            y = torch.logsumexp(logits[:, self.yes], dim=-1)
            n = torch.logsumexp(logits[:, self.no], dim=-1)
            out += torch.sigmoid(y - n).cpu().tolist()
        return out


class OllamaBackend:
    """An Ollama server, for example the project's protected LLM reached through the SSH tunnel."""

    def __init__(self, model: str, url: str = "http://localhost:11434"):
        import httpx

        self.name, self.url, self.model = f"ollama:{model}", url.rstrip("/"), model
        self.http = httpx.Client(timeout=300)

    def _one(self, prompt: str, max_new_tokens: int, temperature: float) -> str:
        r = self.http.post(f"{self.url}/api/chat", json={
            "model": self.model, "stream": False,
            "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}],
            "options": {"temperature": temperature, "num_predict": max_new_tokens},
        })
        r.raise_for_status()
        return r.json()["message"]["content"].strip()

    def chat(self, prompts, max_new_tokens=140, temperature=0.7):
        return [self._one(p, max_new_tokens, temperature) for p in prompts]

    def judge(self, prompts):
        outs = [self._one(p, 3, 0.0).lower() for p in prompts]
        return [1.0 if o.startswith("yes") else 0.0 if o.startswith("no") else 0.5 for o in outs]


def clean(text: str) -> str:
    """Keep the first paragraph, drop speaker labels and quotes."""
    text = re.sub(r"^(reply|answer|message|question)\s*:\s*", "", text.strip(), flags=re.I)
    text = re.split(r"\n\s*\n|\n(?:Rule|Question|Reply)\s*:", text)[0]
    return re.sub(r"\s+", " ", text).strip().strip('"').strip()


def generate(
    clauses: list[dict], backend: Backend, *, seed: int = 7, v_min: float = 0.6, c_max: float = 0.3,
    log=print, out_path: Path | None = None,
) -> list[dict]:
    """clauses: [{clause_id, text, doc_key}]. Returns the records and appends them to out_path."""
    random.seed(seed)
    t0 = time.time()
    qs = [clean(x) for x in backend.chat([Q_PROMPT.format(clause=c["text"]) for c in clauses],
                                         max_new_tokens=60)]
    vs = [clean(x) for x in backend.chat(
        [V_PROMPT.format(clause=c["text"], question=q) for c, q in zip(clauses, qs, strict=True)])]
    cs = [clean(x) for x in backend.chat(
        [C_PROMPT.format(clause=c["text"], question=q) for c, q in zip(clauses, qs, strict=True)])]
    jv = backend.judge([J_PROMPT.format(clause=c["text"], answer=a)
                        for c, a in zip(clauses, vs, strict=True)])
    jc = backend.judge([J_PROMPT.format(clause=c["text"], answer=a)
                        for c, a in zip(clauses, cs, strict=True)])
    rows = []
    for c, q, v, k, pv, pc in zip(clauses, qs, vs, cs, jv, jc, strict=True):
        rows.append({
            "clause_id": c["clause_id"], "variant": c.get("variant", 0), "doc_key": c["doc_key"],
            "question": q,
            "violating": v, "compliant": k, "judge_p_violating": pv, "judge_p_compliant": pc,
            "keep_violating": pv >= v_min and 8 <= len(v.split()) <= 90,
            "keep_compliant": pc <= c_max and 8 <= len(k.split()) <= 90,
            "model": backend.name, "prompt_version": PROMPT_VERSION, "seed": seed,
        })
    if out_path:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("a", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps(r, ensure_ascii=False) + NL)
    kv = sum(r["keep_violating"] for r in rows)
    kc = sum(r["keep_compliant"] for r in rows)
    log(f"[generate] {len(rows)} clauses in {time.time() - t0:.0f}s: kept {kv} violating, {kc} compliant")
    return rows


def main() -> None:
    from c3.training.public.policies import POLICIES, build_policy_corpus, focus_clauses

    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", choices=["hf", "ollama"], default="hf")
    ap.add_argument("--model", default="Qwen/Qwen2.5-1.5B-Instruct")
    ap.add_argument("--url", default="http://localhost:11434")
    ap.add_argument("--load-4bit", action="store_true", help="4-bit weights (needs bitsandbytes)")
    ap.add_argument("--batch", type=int, default=8)
    ap.add_argument("--per-doc", type=int, default=120, help="clauses to sample from each policy")
    ap.add_argument("--variants", type=int, default=2,
                    help="different question and answers per clause (temperature sampling)")
    ap.add_argument("--chunk", type=int, default=64)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--v-min", type=float, default=0.6,
                    help="judge must give a violating answer at least this")
    ap.add_argument("--c-max", type=float, default=0.3,
                    help="judge must give a compliant answer at most this")
    ap.add_argument("--out", type=Path, default=OUT / "generated_answers.jsonl")
    ap.add_argument("--only", nargs="*", default=None)
    a = ap.parse_args()

    docs = build_policy_corpus()
    rng = random.Random(a.seed)
    todo = []
    for pol in POLICIES:
        if pol.key not in docs or (a.only and pol.key not in a.only):
            continue
        for c in focus_clauses(docs[pol.key], a.per_doc, rng):
            for v in range(a.variants):
                todo.append({"clause_id": c.clause_id, "variant": v, "doc_key": pol.key,
                             "text": f"{c.title}: {c.text}"})
    backend = (HFBackend(a.model, batch_size=a.batch, load_4bit=a.load_4bit) if a.backend == "hf"
               else OllamaBackend(a.model, a.url))
    done = set()
    if a.out.exists():
        done = {(r["clause_id"], r.get("variant", 0)) for r in
                (json.loads(line) for line in a.out.read_text(encoding="utf-8").splitlines()
                 if line.strip())}
    todo = [t for t in todo if (t["clause_id"], t["variant"]) not in done]
    print(f"{len(todo)} clauses to write answers for ({len(done)} already done)")
    for i in range(0, len(todo), a.chunk):
        generate(todo[i : i + a.chunk], backend, seed=a.seed, out_path=a.out,
                 v_min=a.v_min, c_max=a.c_max)


if __name__ == "__main__":
    main()
