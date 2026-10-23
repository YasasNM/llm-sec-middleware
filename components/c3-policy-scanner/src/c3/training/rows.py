"""Row builders: generator rows, counterfactual rows, retriever triplets (blueprint 9.11-9.12).

Rules that matter
  * Training rows inject the gold clause if retrieval missed it. Evaluation rows never do.
  * Training uses shuffled label order. Evaluation uses retrieval rank order.
  * Counterfactual rows rewrite the single gold clause to permit the behaviour, target = pass.
  * Triplets and rows are built from the train split only.
"""
from __future__ import annotations

import dataclasses
import random
from collections.abc import Callable, Sequence
from dataclasses import dataclass

import numpy as np

from c3.generation.rag_format import build_prompt, format_target, label_context
from c3.indexing.embedder import Embedder

from .rules_bank import RULES_BY_ID
from .synth import Item, Pack, SynthOrg, split_of

TOP_K = 5


class PoolRetriever:
    """Whole-response retrieval over one organisation's clauses (what the product does)."""

    def __init__(self, org: SynthOrg, embedder: Embedder):
        self.org = org
        self.embedder = embedder
        self.clauses = org.clauses
        self.mat = embedder.embed([c.embed_text() for c in self.clauses])

    def rank(self, response: str, k: int = TOP_K) -> list:
        return self.rank_many([response], k)[0]

    def rank_many(self, responses: Sequence[str], k: int = TOP_K) -> list[list]:
        q = self.embedder.embed(list(responses))
        # Round before sorting so near-ties break by clause order, whatever the batch size
        sims = np.round(q @ self.mat.T, 6)
        return [[self.clauses[i] for i in np.argsort(-row, kind="stable")[:k]] for row in sims]


def build_retrievers(pack: Pack, embedder: Embedder) -> dict[str, PoolRetriever]:
    return {oid: PoolRetriever(org, embedder) for oid, org in pack.orgs.items()}


def recall_at_k(
    pack: Pack, items: Sequence[Item], retrievers: dict[str, PoolRetriever],
    ks: Sequence[int] = (1, 3, 5, 8),
) -> dict[str, float]:
    """Share of violating items whose gold clause(s) are all in the top k (ceiling on recall)."""
    hits = {k: 0 for k in ks}
    n = 0
    by_org: dict[str, list[Item]] = {}
    for it in items:
        if it.label:
            by_org.setdefault(it.org_id, []).append(it)
    for oid, group in by_org.items():
        ranked = retrievers[oid].rank_many([g.response for g in group], max(ks))
        for it, rk in zip(group, ranked, strict=True):
            ids = [c.clause_id for c in rk]
            n += 1
            for k in ks:
                if all(g in ids[:k] for g in it.gold_clause_ids):
                    hits[k] += 1
    return {f"recall@{k}": (hits[k] / n if n else float("nan")) for k in ks}


# ---- generator rows ------------------------------------------------------------------
@dataclass
class Row:
    example_id: str
    item_id: str
    batch_id: str
    org_id: str
    split: str
    label: int
    kind: str                       # sft | counterfactual
    context_ids: list[str]
    gold_clause_ids: list[str]
    gold_in_context: bool
    input: str
    target: str
    injected: bool = False
    source: str = ""

    def to_json(self) -> dict:
        return dataclasses.asdict(self)


def _context_for(item: Item, ranked: list, org: SynthOrg, inject_gold: bool, k: int) -> list:
    """Top-k clauses in rank order. In training, put missing gold clauses in place of the
    lowest-ranked non-gold clause so every violating row shows the real clause."""
    ctx = list(ranked[:k])
    if inject_gold and item.gold_clause_ids:
        by_id = org.clause_by_id()
        ids = [c.clause_id for c in ctx]
        for gid in item.gold_clause_ids:
            if gid not in ids:
                for pos in range(len(ctx) - 1, -1, -1):
                    if ctx[pos].clause_id not in item.gold_clause_ids:
                        ctx[pos] = by_id[gid]
                        ids = [c.clause_id for c in ctx]
                        break
    return ctx


def _cited_labels(item: Item, ctx_clauses: list, order: list[int]) -> list[str]:
    """Labels of the gold clauses present in the context, best-ranked first."""
    pos_of = {ctx_clauses[i].clause_id: n for n, i in enumerate(order)}   # clause -> label index
    rank_of = {c.clause_id: n for n, c in enumerate(ctx_clauses)}
    present = [g for g in item.gold_clause_ids if g in pos_of]
    present.sort(key=lambda g: rank_of[g])
    return [f"C{pos_of[g] + 1}" for g in present]


def build_sft_rows(
    pack: Pack,
    items: Sequence[Item],
    retrievers: dict[str, PoolRetriever],
    *,
    draws: int,
    train: bool,
    seed: int,
    k: int = TOP_K,
    with_quote: bool = True,
) -> list[Row]:
    """Generator rows. train=True: inject gold, shuffle labels, `draws` per item.
    train=False: evaluation rows, no gold injection, rank-order labels, one per item."""
    rng = random.Random(seed)
    rows: list[Row] = []
    by_org: dict[str, list[Item]] = {}
    for it in items:
        by_org.setdefault(it.org_id, []).append(it)
    for oid, group in by_org.items():
        org, ret = pack.orgs[oid], retrievers[oid]
        ranked_all = ret.rank_many([g.response for g in group], max(k, 8))
        for it, ranked in zip(group, ranked_all, strict=True):
            ctx_clauses = _context_for(it, ranked, org, train, k)
            n_draws = draws if train else 1
            for d in range(n_draws):
                order = list(range(len(ctx_clauses)))
                if train:
                    rng.shuffle(order)
                ctx = label_context(ctx_clauses, order=order)
                cited = _cited_labels(it, ctx_clauses, order) if it.label else []
                if it.label and not cited:
                    cited = []          # evaluation: retrieval missed the gold clause
                rows.append(Row(
                    example_id=f"{it.item_id}#{d}", item_id=it.item_id, batch_id=it.batch_id,
                    org_id=oid, split=split_of(it, pack, pack.seed), label=it.label, kind="sft",
                    context_ids=[ctx[lb].clause_id for lb in ctx],
                    gold_clause_ids=list(it.gold_clause_ids),
                    gold_in_context=all(g in {c.clause_id for c in ctx_clauses}
                                        for g in it.gold_clause_ids),
                    input=build_prompt(it.response, ctx),
                    target=format_target(cited, ctx, with_quote=with_quote),
                    injected=it.injected, source=it.source,
                ))
    return rows


def build_counterfactual_rows(
    pack: Pack, items: Sequence[Item], retrievers: dict[str, PoolRetriever], *, seed: int,
    k: int = TOP_K, max_rows: int | None = None,
) -> list[Row]:
    """Same response, the single gold clause rewritten to permit it, target = pass."""
    rng = random.Random(seed)
    rows: list[Row] = []
    for it in items:
        if not it.label or len(it.gold_clause_ids) != 1 or not it.gold_rules:
            continue          # permit rewrites exist only for the hand-written rule bank
        org, ret = pack.orgs[it.org_id], retrievers[it.org_id]
        gid = it.gold_clause_ids[0]
        ctx_clauses = _context_for(it, ret.rank(it.response, max(k, 8)), org, True, k)
        permit = RULES_BY_ID[it.gold_rules[0]].permit
        ctx_clauses = [dataclasses.replace(c, text=permit) if c.clause_id == gid else c
                       for c in ctx_clauses]
        order = list(range(len(ctx_clauses)))
        rng.shuffle(order)
        ctx = label_context(ctx_clauses, order=order)
        rows.append(Row(
            example_id=f"{it.item_id}#cf", item_id=it.item_id, batch_id=it.batch_id,
            org_id=it.org_id, split=split_of(it, pack, pack.seed), label=0, kind="counterfactual",
            context_ids=[ctx[lb].clause_id for lb in ctx], gold_clause_ids=[gid],
            gold_in_context=True, input=build_prompt(it.response, ctx),
            target=format_target([], ctx), injected=it.injected,
        ))
    if max_rows is not None and len(rows) > max_rows:
        rows = rng.sample(rows, max_rows)
    return rows


def build_removal_rows(
    pack: Pack, items: Sequence[Item], retrievers: dict[str, PoolRetriever], *, seed: int,
    k: int = TOP_K, near: tuple[int, int] = (3, 25), draws: int = 1,
) -> list[Row]:
    """Same answer, the clause it breaks taken OUT of the context -> pass.

    A violation only counts against a rule that is in front of the model. These rows break the
    shortcut "this answer sounds careless": the text is identical to a violating row, so only the
    rules can explain the different target. The replacement clauses are near misses (similarity
    rank `near`), which keeps them on topic but far enough from the top to avoid clauses that
    forbid the same thing."""
    rng = random.Random(seed)
    rows: list[Row] = []
    for it in items:
        if not it.label or not it.gold_clause_ids:
            continue
        ret = retrievers[it.org_id]
        gold = set(it.gold_clause_ids)
        ranked = ret.rank(it.response, near[1] + len(gold))
        pool = [c for c in ranked[near[0]:] if c.clause_id not in gold]
        if len(pool) < k:
            continue
        for d in range(draws):                  # as many removal rows as violating rows (paired)
            ctx_clauses = rng.sample(pool, k)
            order = list(range(k))
            rng.shuffle(order)
            ctx = label_context(ctx_clauses, order=order)
            rows.append(Row(
                example_id=f"{it.item_id}#rm{d}", item_id=it.item_id, batch_id=it.batch_id,
                org_id=it.org_id, split=split_of(it, pack, pack.seed), label=0, kind="removal",
                context_ids=[ctx[lb].clause_id for lb in ctx], gold_clause_ids=list(gold),
                gold_in_context=False, input=build_prompt(it.response, ctx),
                target=format_target([], ctx), injected=it.injected, source=it.source,
            ))
    return rows


# ---- retriever triplets --------------------------------------------------------------
def build_triplets(
    pack: Pack, items: Sequence[Item], retrievers: dict[str, PoolRetriever], *,
    n_negatives: int = 3,
) -> list[dict]:
    out: list[dict] = []
    for it in items:
        if not it.label:
            continue
        org, ret = pack.orgs[it.org_id], retrievers[it.org_id]
        by_id = org.clause_by_id()
        ranked = ret.rank(it.response, 10)
        negs = [c for c in ranked if c.clause_id not in it.gold_clause_ids][:n_negatives]
        for gid in it.gold_clause_ids:
            out.append({
                "item_id": it.item_id, "batch_id": it.batch_id, "org_id": it.org_id,
                "anchor": it.response, "positive": by_id[gid].embed_text(),
                "positive_id": gid, "negatives": [c.embed_text() for c in negs],
                "negative_ids": [c.clause_id for c in negs],
            })
    return out


def items_in_split(pack: Pack, split: str) -> list[Item]:
    return [i for i in pack.items if split_of(i, pack, pack.seed) == split]


EmbedderFactory = Callable[[], Embedder]
