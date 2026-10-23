"""Automatic data checks. A training run must fail if any check fails (FR26, blueprint 12.9)."""
from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Sequence

from c3.generation.rag_format import parse_output

from .rows import Row
from .synth import Item, Pack, split_of


class DataCheckError(AssertionError):
    pass


def check_targets_parse(rows: Sequence[Row]) -> None:
    """Every target parses and cites only labels that exist in its own prompt."""
    for r in rows:
        p = parse_output(r.target)
        if not p.usable:
            raise DataCheckError(f"{r.example_id}: target does not parse: {r.target!r}")
        n_labels = r.input.count("\n[C")
        for lb in p.cited:
            if int(lb[1:]) > n_labels:
                raise DataCheckError(f"{r.example_id}: cites {lb} but prompt has {n_labels}")
        if (p.verdict == "pass") != (not p.cited):
            raise DataCheckError(f"{r.example_id}: verdict and citations disagree")


def check_violating_rows_contain_gold(rows: Sequence[Row]) -> None:
    for r in rows:
        if r.kind == "sft" and r.label and not r.gold_in_context:
            raise DataCheckError(f"{r.example_id}: violating train row without its gold clause")


def check_draws_differ(rows: Sequence[Row], min_share: float = 0.5) -> None:
    by_item: dict[str, set[tuple[str, ...]]] = {}
    for r in rows:
        if r.kind == "sft":
            by_item.setdefault(r.item_id, set()).add(tuple(r.context_ids))
    multi = [s for s in by_item.values()]
    differing = sum(1 for s in multi if len(s) > 1)
    if multi and differing / len(multi) < min_share:
        raise DataCheckError("label orders are too similar across draws")


def check_no_batch_crosses_splits(pack: Pack, items: Sequence[Item]) -> None:
    seen: dict[str, str] = {}
    for it in items:
        sp = split_of(it, pack, pack.seed)
        if seen.setdefault(it.batch_id, sp) != sp:
            raise DataCheckError(f"batch {it.batch_id} appears in two splits")


def check_no_heldout_clause_in_train(
    pack: Pack, rows: Sequence[Row], triplets: Sequence[dict]
) -> None:
    """No clause text of a validation or test organisation may appear in a train row or triplet.

    Filler clauses (purpose, scope, review ...) are skipped: they restate generic boilerplate
    and carry no rule. Every rule clause of a held-out organisation is checked.
    """
    held: dict[str, str] = {}
    for org in pack.orgs.values():
        if org.role == "train":
            continue
        for c in org.clauses:
            if org.clause_rule.get(c.clause_id):
                held[c.text.strip()] = c.clause_id
    for r in rows:
        if r.kind == "counterfactual":
            continue                                   # permit rewrites are new text by design
        for text, cid in held.items():
            if text in r.input:
                raise DataCheckError(f"{r.example_id}: contains held-out clause {cid}")
    for t in triplets:
        for text, cid in held.items():
            if text in t["positive"] or any(text in n for n in t["negatives"]):
                raise DataCheckError(f"triplet {t['item_id']}: contains held-out clause {cid}")
    for r in rows:                      # each context clause must come from the row's train org
        org = pack.orgs[r.org_id]
        own = {c.clause_id for c in org.clauses}
        stray = [cid for cid in r.context_ids if cid not in own]
        if org.role != "train" or stray:
            raise DataCheckError(
                f"{r.example_id}: context holds held-out clause {stray[0] if stray else r.org_id}")
    train_orgs = {o.org_id for o in pack.orgs.values() if o.role == "train"}
    for r in rows:
        if r.org_id not in train_orgs:
            raise DataCheckError(f"{r.example_id}: row from non-train organisation {r.org_id}")


def check_no_duplicates(rows: Sequence[Row]) -> None:
    c = Counter((r.input, r.target) for r in rows)
    dup = [k for k, n in c.items() if n > 1]
    if dup:
        raise DataCheckError(f"{len(dup)} duplicate (input, target) rows")


def check_prompt_length(
    rows: Sequence[Row], count_tokens: Callable[[str], int] | None = None,
    limit: int = 700, share: float = 0.99,
) -> float:
    # Without a tokenizer, use a rough proxy of 1.5 tokens per word
    count = count_tokens or (lambda s: int(len(s.split()) * 1.5))
    lens = sorted(count(r.input) for r in rows)
    ok = sum(1 for n in lens if n <= limit) / len(lens)
    if ok < share:
        raise DataCheckError(f"only {ok:.1%} of prompts are at most {limit} tokens")
    return ok


def check_violating_share(rows: Sequence[Row], low: float = 0.35, high: float = 0.50) -> float:
    sft = [r for r in rows if r.kind == "sft"]
    share = sum(r.label for r in sft) / len(sft)
    if not low <= share <= high:
        raise DataCheckError(f"violating share {share:.2%} outside [{low:.0%}, {high:.0%}]")
    return share


def run_all(
    pack: Pack, train_items: Sequence[Item], rows: Sequence[Row], triplets: Sequence[dict],
    count_tokens: Callable[[str], int] | None = None,
) -> dict:
    check_targets_parse(rows)
    check_violating_rows_contain_gold(rows)
    check_draws_differ(rows)
    check_no_batch_crosses_splits(pack, pack.items)
    check_no_heldout_clause_in_train(pack, rows, triplets)
    check_no_duplicates(rows)
    ok_len = check_prompt_length(rows, count_tokens)
    share = check_violating_share(rows)
    return {"rows": len(rows), "triplets": len(triplets), "prompts_within_700_tokens": ok_len,
            "violating_share": share, "status": "passed"}
