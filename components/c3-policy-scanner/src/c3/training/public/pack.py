"""Assemble the public-data training pack (replaces the hand-written rule bank).

  organisations (retrieval pools of REAL clauses)
    pub-train    HIPAA Privacy Rule, HIPAA Breach Rule, 42 CFR Part 2        -> training
    pub-val      45 CFR Part 92 (nondiscrimination, AI decision support)      -> validation
    pub-test     Sri Lanka PDPA 2022 and FDA CDS guidance                     -> held-out test
    pub-conduct  a short general-conduct policy built on the Aegis categories -> training
  answers
    llm-generated  answers an open LLM wrote for real clauses, kept only if a judge agrees
    medquad        real NIH medical answers, compliant
    aegis2         real LLM responses with human or jury safety labels
  Held-out means whole documents, not rows: no clause of the validation or test organisation
  is ever seen in training, and the test documents come from a different country and style.
"""
from __future__ import annotations

import dataclasses
import json
import random
from collections import Counter
from pathlib import Path

from c3.policy.models import Clause, content_hash, slugify
from c3.training.rules_bank import INJECTION_TRAIN
from c3.training.synth import Item, Pack, SynthOrg, with_injection

from .answers import CONDUCT_CLAUSES, RealAnswer, bucket, load_aegis, load_medquad
from .policies import BY_KEY, POLICIES, build_policy_corpus

ORG_OF_ROLE = {"train": "pub-train", "val": "pub-val", "test": "pub-test"}


def _conduct_org() -> SynthOrg:
    clauses, cmap = [], {}
    for i, (cat, (title, text)) in enumerate(CONDUCT_CLAUSES.items()):
        cid = f"CONDUCT-{slugify(title)}"
        clauses.append(Clause(cid, "CONDUCT", "", title, text, "general_conduct", "high",
                              content_hash(title, text), slugify(title), i))
        cmap[cat] = cid
    org = SynthOrg("pub-conduct", "train", [], clauses, {}, {c.clause_id: c.clause_id for c in clauses})
    org.rule_clause = dict(cmap)
    return org


def load_generated(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def build_public_pack(
    gen_path: Path,
    *,
    seed: int = 7,
    n_medquad: int = 800,
    n_aegis_safe: int = 800,
    n_aegis_unsafe: int = 800,
    injection_share: float = 0.04,
    target_violating: float = 0.45,
    log=print,
) -> Pack:
    rng = random.Random(seed)
    docs = build_policy_corpus(log=lambda *_: None)
    pack = Pack(seed=seed)

    # ---- organisations of real clauses ---------------------------------------------------
    by_role: dict[str, list[Clause]] = {"train": [], "val": [], "test": []}
    doc_role: dict[str, str] = {}
    for pol in POLICIES:
        if pol.key in docs:
            by_role[pol.role] += docs[pol.key].clauses
            doc_role[pol.key] = pol.role
    for role, clauses in by_role.items():
        if clauses:
            clauses = [dataclasses.replace(c, order=i) for i, c in enumerate(clauses)]
            pack.orgs[ORG_OF_ROLE[role]] = SynthOrg(
                ORG_OF_ROLE[role], role, [], clauses, {}, {c.clause_id: c.clause_id for c in clauses})
    conduct = _conduct_org()
    pack.orgs[conduct.org_id] = conduct

    items: list[Item] = []

    # ---- generated answers for real clauses ----------------------------------------------
    for rec in load_generated(gen_path):
        pol = BY_KEY.get(rec["doc_key"])
        if pol is None or pol.key not in doc_role:
            continue
        org_id, role = ORG_OF_ROLE[pol.role], pol.role
        batch = f"{org_id}-{rec['clause_id']}"           # every answer of a clause stays together
        vid = f"{rec['clause_id']}-{rec.get('variant', 0)}"
        def src(kind: str, rec=rec) -> str:
            return "llm-generated+human" if rec.get(f"human_reviewed_{kind}") else "llm-generated"

        if rec["keep_violating"]:
            items.append(Item(f"gen-{vid}-v", org_id, batch, role, rec["violating"],
                              [], [rec["clause_id"]], 1, "violating", source=src("violating")))
        if rec["keep_compliant"]:
            items.append(Item(f"gen-{vid}-c", org_id, batch, role, rec["compliant"],
                              [], [], 0, "hard_negative", source=src("compliant")))

    # ---- real medical answers: compliant --------------------------------------------------
    for n, a in enumerate(load_medquad(n_medquad, seed)):
        role = bucket(a.answer_id, seed)
        org_id = ORG_OF_ROLE[role] if ORG_OF_ROLE[role] in pack.orgs else "pub-train"
        items.append(Item(a.answer_id, org_id, f"mq-{role}-{n // 10}", role, a.text, [], [], 0,
                          "compliant", source="medquad"))

    # ---- real labelled responses ------------------------------------------------------------
    for n, a in enumerate(load_aegis(n_aegis_safe, n_aegis_unsafe, seed)):
        gold = [conduct.rule_clause[c] for c in a.categories if c in conduct.rule_clause]
        items.append(Item(f"aegis-{a.answer_id}", conduct.org_id, f"aegis-{n // 10}", "train",
                          a.text, [], gold, a.label, "real_unsafe" if a.label else "real_safe",
                          source="aegis2"))

    # ---- balance to about 45% violating inside each split (drop easy negatives first) ------
    balanced: list[Item] = []
    for role in ("train", "val", "test"):
        part = [i for i in items if i.role == role]
        pos = [i for i in part if i.label]
        neg = [i for i in part if not i.label]
        if pos:
            want_neg = int(len(pos) * (1 - target_violating) / target_violating)
            if len(neg) > want_neg:
                keep_first = [i for i in neg if i.source.startswith("llm-generated")]   # hard negatives stay
                rest = [i for i in neg if not i.source.startswith("llm-generated")]
                rng.shuffle(rest)
                neg = keep_first + rest[: max(0, want_neg - len(keep_first))]
        balanced += pos + neg
    items = balanced
    rng.shuffle(items)

    # ---- a few instruction-injection rows in training ----------------------------------------
    for it in items:
        if it.role == "train" and rng.random() < injection_share:
            it.response = with_injection(rng, it.response, INJECTION_TRAIN)
            it.injected = True
    pack.items = items
    log(f"[pack] {len(items)} items: " + ", ".join(f"{k}={v}" for k, v in
                                                     Counter(i.source for i in items).items()))
    return pack


def provenance(pack: Pack) -> dict:
    """What share of items is real and what share is machine-generated."""
    c = Counter(i.source for i in pack.items)
    total = sum(c.values()) or 1
    gen = c["llm-generated"] + c["llm-generated+human"]
    return {"items": total, "by_source": dict(c),
            "share_real": round((c["medquad"] + c["aegis2"]) / total, 3),
            "share_llm_generated": round(gen / total, 3),
            "share_llm_generated_and_human_checked": round(c["llm-generated+human"] / total, 3),
            "share_handwritten_templates": round(c.get("synthetic-v0", 0) / total, 3)}


__all__ = ["build_public_pack", "provenance", "RealAnswer"]
