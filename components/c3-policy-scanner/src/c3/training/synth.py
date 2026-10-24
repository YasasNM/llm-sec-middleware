"""Build the synthetic policy pack and the compliance dataset (blueprint 8.4, 12.9, App. E).

Structure
  train orgs : 5 organisations x 3 documents, every seen rule appears once per organisation
  val org    : 1 organisation x 2 documents (new wording, some unseen rules)
  test orgs  : 4 organisations x 2 documents (new wording, all unseen rules)  -> S2

An "organisation" is one retrieval pool. Inside a pool each rule is written once, so a
response that breaks a rule has exactly one gold clause.

Documents are written as Markdown, then read back with the real PolicyParser, so training
uses the same clause ids as the product.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import random
from dataclasses import dataclass, field
from pathlib import Path

from c3.policy.doc_code import make_doc_code
from c3.policy.models import Clause
from c3.policy.parser import ParserLimits, PolicyParser

from .rules_bank import (
    CLOSINGS,
    FILLERS,
    INJECTION_TEST,
    INJECTION_TRAIN,
    LEADS_TEST,
    LEADS_TRAIN,
    LEADS_VAL,
    NEUTRAL,
    OPENINGS,
    RULES,
    RULES_BY_ID,
    SYNONYMS,
    TAILS_TEST,
    TAILS_TRAIN,
    TAILS_VAL,
    Rule,
)

CATEGORY_HEADINGS = {
    "data_handling": "Patient Data Handling",
    "disclosure": "Disclosure of Information",
    "prescribing": "Prescribing Advice",
    "referral": "Referrals",
    "ai_usage": "Use of AI Tools",
    "incident_reporting": "Incident Reporting",
}
CATEGORY_ORDER = list(CATEGORY_HEADINGS)
DOC_NAMES = [
    "Clinical AI Usage Guidelines", "Patient Information Handbook",
    "Medicines and Referral Standards",
    "Digital Health Conduct Policy", "Information Governance Standard", "Safe Use of Assistants",
    "Ward Technology Rules", "Clinical Documentation Policy", "Patient Safety Reporting Guide",
    "Data Protection Code", "AI Assistant Acceptable Use", "Medicines Safety Policy",
    "Clinic Communication Policy", "Hospital Information Rules", "Staff Technology Charter",
    "Clinical Governance Manual", "Care Quality Procedures", "Privacy and Safety Guideline",
    "Responsible AI in Care", "Frontline Staff Handbook", "Records and Reporting Policy",
    "Treatment Information Standard", "Patient Rights Charter", "Clinical Support Tools Policy",
    "Safe Practice Framework",
]
BATCH_SIZE = 10


@dataclass
class SynthDoc:
    doc_id: str
    org_id: str
    name: str
    filename: str
    doc_code: str
    markdown: str
    rule_ids: list[str]
    number_of_rule: dict[str, str]          # rid -> clause number in this document


@dataclass
class SynthOrg:
    org_id: str
    role: str                               # train | val | test
    docs: list[SynthDoc]
    clauses: list[Clause]                   # parsed with the real parser, severity from the rule
    rule_clause: dict[str, str]             # rid -> clause_id
    clause_rule: dict[str, str]             # clause_id -> rid ("" for fillers)

    def clause_by_id(self) -> dict[str, Clause]:
        return {c.clause_id: c for c in self.clauses}


@dataclass
class Item:
    item_id: str
    org_id: str
    batch_id: str
    role: str                               # train | val | test
    response: str
    gold_rules: list[str]
    gold_clause_ids: list[str]
    label: int                              # 1 = violating
    kind: str                               # compliant | hard_negative | violating | multi
    injected: bool = False
    source: str = "synthetic-v0"

    def to_json(self) -> dict:
        return dataclasses.asdict(self)


@dataclass
class Pack:
    orgs: dict[str, SynthOrg] = field(default_factory=dict)
    items: list[Item] = field(default_factory=list)
    seed: int = 7

    def org(self, org_id: str) -> SynthOrg:
        return self.orgs[org_id]


# ---- documents -----------------------------------------------------------------------
def _wording(rule: Rule, role: str, rng_index: int) -> tuple[str, str]:
    """Return (title, clause text) for the rule in this role."""
    if role == "train":
        return (rule.titles[rng_index % len(rule.titles)],
                rule.w_train[rng_index % len(rule.w_train)])
    if role == "val":
        return rule.titles[-1], rule.w_val
    return rule.titles[-1], rule.w_test[rng_index % len(rule.w_test)]


def _filler(i: int, role: str, rng_index: int) -> tuple[str, str]:
    f = FILLERS[i]
    texts = f["text"]
    if role == "train":
        return f["title"][0], texts[rng_index % 3]
    return f["title"][0], texts[(rng_index + 1) % 3]


def _render_doc(
    name: str, role: str, rules: list[Rule], org_index: int, doc_index: int, fillers: list[int]
) -> tuple[str, dict[str, str], dict[int, str]]:
    """Markdown for one document, the clause number of each rule, and filler numbers."""
    lines = [f"# {name}", ""]
    number_of: dict[str, str] = {}
    filler_number: dict[int, str] = {}
    sec = 0
    head_fillers = [f for f in fillers if f in (0, 1, 2)]
    tail_fillers = [f for f in fillers if f not in (0, 1, 2)]
    for f in head_fillers:
        sec += 1
        title, text = _filler(f, role, org_index + doc_index)
        lines += [f"## {sec} {title}", "", text, ""]
        filler_number[f] = str(sec)
    by_cat: dict[str, list[Rule]] = {}
    for r in rules:
        by_cat.setdefault(r.category, []).append(r)
    for cat in CATEGORY_ORDER:
        group = by_cat.get(cat)
        if not group:
            continue
        sec += 1
        lines += [f"## {sec} {CATEGORY_HEADINGS[cat]}", ""]
        for k, r in enumerate(group, start=1):
            title, text = _wording(r, role, org_index + doc_index + k)
            num = f"{sec}.{k}"
            lines += [f"### {num} {title}", "", text, ""]
            number_of[r.rid] = num
    for f in tail_fillers:
        sec += 1
        title, text = _filler(f, role, org_index + doc_index)
        lines += [f"## {sec} {title}", "", text, ""]
        filler_number[f] = str(sec)
    return "\n".join(lines), number_of, filler_number


def _chunks(seq: list, n: int) -> list[list]:
    k, m = divmod(len(seq), n)
    out, start = [], 0
    for i in range(n):
        end = start + k + (1 if i < m else 0)
        out.append(seq[start:end])
        start = end
    return out


def build_org(
    org_id: str, role: str, org_index: int, rules: list[Rule], n_docs: int,
    rng: random.Random, name_offset: int, parser: PolicyParser,
) -> SynthOrg:
    rules = rules[:]
    rng.shuffle(rules)
    parts = _chunks(rules, n_docs)
    filler_sets = _chunks(list(range(len(FILLERS))), n_docs)
    # interleave head and tail fillers so each doc gets a mix
    docs: list[SynthDoc] = []
    clauses: list[Clause] = []
    rule_clause: dict[str, str] = {}
    clause_rule: dict[str, str] = {}
    taken: set[str] = set()
    for d, (rs, fs) in enumerate(zip(parts, filler_sets, strict=True)):
        name = DOC_NAMES[(name_offset + d) % len(DOC_NAMES)] + (
            " 2026" if role == "train" else " 2027" if role == "val" else " 2028"
        )
        fs = sorted(set(fs) | {0 if d == 0 else 1})       # every doc starts with a purpose/scope
        md, number_of, filler_number = _render_doc(name, role, rs, org_index, d, fs)
        filename = name.lower().replace(" ", "-") + ".md"
        code = make_doc_code(filename, taken)
        taken.add(code)
        parsed = parser.parse_markdown(md, doc_code=code)
        by_num = {c.number: c for c in parsed.clauses}
        for rid, num in number_of.items():
            c = by_num[num]
            r = RULES_BY_ID[rid]
            by_num[num] = dataclasses.replace(c, category=r.category, severity=r.severity)
            rule_clause[rid] = c.clause_id
            clause_rule[c.clause_id] = rid
        for num in filler_number.values():
            c = by_num.get(num)
            if c is not None:
                by_num[num] = dataclasses.replace(c, category="general", severity="medium")
                clause_rule[c.clause_id] = ""
        ordered = [by_num[c.number] for c in parsed.clauses]
        clauses.extend(ordered)
        docs.append(SynthDoc(f"{org_id}-d{d + 1}", org_id, name, filename, code, md,
                             [r.rid for r in rs], number_of))
    # re-number clause order across the organisation
    clauses = [dataclasses.replace(c, order=i) for i, c in enumerate(clauses)]
    return SynthOrg(org_id, role, docs, clauses, rule_clause, clause_rule)


# ---- responses -----------------------------------------------------------------------
def _snippets(rule: Rule, role: str, kind: str) -> tuple[str, ...]:
    if kind == "violating":
        return rule.v_train if role == "train" else rule.v_val if role == "val" else rule.v_test
    return rule.ok_train if role in ("train", "val") else rule.ok_test or rule.ok_train


def _swap_synonyms(text: str, rng: random.Random) -> str:
    import re

    for word, alts in SYNONYMS.items():
        if rng.random() < 0.5:
            def repl(m, alts=alts):
                new = rng.choice(alts)
                return new.capitalize() if m.group(0)[0].isupper() else new

            text = re.sub(rf"\b{re.escape(word)}\b", repl, text, count=1, flags=re.I)
    return text


def decorate(snippet: str, rng: random.Random, role: str) -> str:
    """Wrap a snippet in a lead-in and a tail, and (training text only) swap some words.

    Applied to violating and compliant snippets alike, so a frame never signals the label.
    Test text uses frames that training never saw."""
    if role == "test":
        return rng.choice(LEADS_TEST) + snippet + rng.choice(TAILS_TEST)
    if role == "val":
        return rng.choice(LEADS_VAL) + snippet + rng.choice(TAILS_VAL)
    lead, tail = rng.choice(LEADS_TRAIN), rng.choice(TAILS_TRAIN)
    return lead + _swap_synonyms(snippet, rng) + tail


def compose_response(
    rng: random.Random, violating: list[tuple[Rule, str]], compliant: list[str]
) -> str:
    pieces = rng.sample(NEUTRAL, rng.choice([1, 2, 2, 3]))
    inserts = [s for _, s in violating] + compliant
    for s in inserts:
        pieces.insert(rng.randint(0, len(pieces)), s)
    return rng.choice(OPENINGS) + " ".join(pieces) + rng.choice(CLOSINGS)


def with_injection(rng: random.Random, text: str, phrasing: list[str]) -> str:
    inj = rng.choice(phrasing)
    return f"{text} {inj}" if rng.random() < 0.7 else f"{inj} {text}"


def build_items(
    org: SynthOrg, n_items: int, rng: random.Random, *, violating_share: float = 0.44,
    injection_share: float = 0.0, injection_phrasings: list[str] | None = None,
) -> list[Item]:
    org_rules = [RULES_BY_ID[r] for r in org.rule_clause]
    items: list[Item] = []
    for n in range(n_items):
        batch = f"{org.org_id}-b{n // BATCH_SIZE:02d}"
        item_id = f"{org.org_id}-i{n:03d}"
        violating = rng.random() < violating_share
        if violating:
            k = 2 if rng.random() < 0.12 else 1
            chosen = rng.sample(org_rules, k)
            viol = [(r, decorate(rng.choice(_snippets(r, org.role, "violating")), rng, org.role))
                    for r in chosen]
            text = compose_response(rng, viol, [])
            gold = [r.rid for r in chosen]
            kind = "multi" if k == 2 else "violating"
        else:
            hard = rng.random() < 0.7
            comp: list[str] = []
            if hard:
                for r in rng.sample(org_rules, rng.choice([1, 1, 2])):
                    comp.append(decorate(rng.choice(_snippets(r, org.role, "ok")), rng, org.role))
            text = compose_response(rng, [], comp)
            gold, kind = [], "hard_negative" if hard else "compliant"
        injected = False
        if injection_share and rng.random() < injection_share:
            text = with_injection(rng, text, injection_phrasings or INJECTION_TRAIN)
            injected = True
        items.append(Item(
            item_id, org.org_id, batch, org.role, text, gold,
            [org.rule_clause[g] for g in gold], int(violating), kind, injected,
        ))
    return items


# ---- the whole pack ------------------------------------------------------------------
def build_pack(seed: int = 7, *, train_items: int = 180, test_items: int = 80,
               val_items: int = 100) -> Pack:
    rng = random.Random(seed)
    parser = PolicyParser(
        ParserLimits(max_clauses=500), {}, "general"
    )
    seen = [r for r in RULES if not r.unseen]
    unseen = [r for r in RULES if r.unseen]
    pack = Pack(seed=seed)

    for i in range(5):                                   # train organisations
        org = build_org(f"train-org-{i + 1}", "train", i, seen, 3, rng, i * 3, parser)
        pack.orgs[org.org_id] = org
        pack.items += build_items(org, train_items, rng, injection_share=0.04,
                                  injection_phrasings=INJECTION_TRAIN)

    for i in range(2):                                   # validation organisations
        val_rules = rng.sample(seen, 12) + rng.sample(unseen, 3)
        val = build_org(f"val-org-{i + 1}", "val", i, val_rules, 2, rng, 16 + i * 2, parser)
        pack.orgs[val.org_id] = val
        pack.items += build_items(val, val_items, rng)

    for i in range(4):                                   # held-out test organisations
        rules = rng.sample(seen, 12) + unseen[:]
        org = build_org(f"test-org-{i + 1}", "test", i, rules, 2, rng, 18 + i * 2, parser)
        pack.orgs[org.org_id] = org
        pack.items += build_items(org, test_items, rng)
    return pack


# ---- splits and files ----------------------------------------------------------------
def split_of(item: Item, pack: Pack, seed: int = 7) -> str:
    """train / val / test_s1 for training organisations by batch (70/10/20); s2 and val_unseen
    for held-out organisations. The same batch never lands in two splits."""
    if item.role == "test":
        return "test_s2"
    if item.role == "val":
        return "val_unseen"
    h = int(hashlib.sha256(f"{seed}:{item.batch_id}".encode()).hexdigest(), 16) % 100
    return "train" if h < 70 else "val" if h < 80 else "test_s1"


def write_pack(pack: Pack, out_dir: str | Path) -> dict:
    out = Path(out_dir)
    (out / "policies").mkdir(parents=True, exist_ok=True)
    (out / "processed").mkdir(parents=True, exist_ok=True)
    for org in pack.orgs.values():
        for d in org.docs:
            p = out / "policies" / org.role / org.org_id / d.filename
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(d.markdown, encoding="utf-8")
    rows = []
    for it in pack.items:
        r = it.to_json()
        r["split"] = split_of(it, pack, pack.seed)
        rows.append(r)
    with (out / "processed" / "items.jsonl").open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    counts: dict[str, dict[str, int]] = {}
    for r in rows:
        c = counts.setdefault(r["split"], {"items": 0, "violating": 0})
        c["items"] += 1
        c["violating"] += r["label"]
    manifest = {
        "data_version": f"synthetic-v0-seed{pack.seed}",
        "seed": pack.seed,
        "synthetic": True,
        "orgs": {k: {"role": o.role, "docs": [d.filename for d in o.docs],
                     "clauses": len(o.clauses)} for k, o in pack.orgs.items()},
        "splits": counts,
        "items_sha256": hashlib.sha256(
            "\n".join(json.dumps(r, sort_keys=True) for r in rows).encode()
        ).hexdigest(),
        "note": "Synthetic silver labels by construction. Replace with real LLM answers "
                "labelled by people before any research claim (blueprint 8.4).",
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def injection_variant(item: Item, rng: random.Random, phrasings: list[str]) -> Item:
    """Copy of an item with instruction-like text appended. Label is unchanged (S6)."""
    return dataclasses.replace(
        item, item_id=item.item_id + "-inj", response=with_injection(rng, item.response, phrasings),
        injected=True,
    )


S6_PHRASINGS = INJECTION_TEST
