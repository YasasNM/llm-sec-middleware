import dataclasses
import random

import pytest

from c3.indexing.embedder import HashingEmbedder
from c3.training import checks
from c3.training.evaluate import Calibration, evaluate_rows
from c3.training.metrics import (
    bootstrap_ci,
    choose_threshold,
    ece,
    fit_temperature,
    mcnemar,
    precision_at_prevalence,
    prf,
    sigmoid,
)
from c3.training.rows import (
    build_counterfactual_rows,
    build_retrievers,
    build_sft_rows,
    build_triplets,
    items_in_split,
    recall_at_k,
)
from c3.training.rules_bank import INJECTION_TEST, INJECTION_TRAIN, RULES
from c3.training.synth import build_pack, injection_variant, split_of


@pytest.fixture(scope="module")
def pack():
    return build_pack(seed=7)


@pytest.fixture(scope="module")
def retrievers(pack):
    return build_retrievers(pack, HashingEmbedder())


# ---- pack ------------------------------------------------------------------------------
def test_pack_is_deterministic():
    a, b = build_pack(seed=3), build_pack(seed=3)
    assert [i.response for i in a.items] == [i.response for i in b.items]
    assert build_pack(seed=4).items[0].response != a.items[0].response


def test_every_org_clause_id_is_unique_and_every_rule_has_one_clause(pack):
    for org in pack.orgs.values():
        ids = [c.clause_id for c in org.clauses]
        assert len(ids) == len(set(ids))
        assert len(org.rule_clause) == len(set(org.rule_clause.values()))
        for cid in org.rule_clause.values():
            assert org.clause_by_id()[cid].severity in ("high", "medium", "low")


def test_unseen_rules_never_appear_in_train_organisations(pack):
    unseen = {r.rid for r in RULES if r.unseen}
    for org in pack.orgs.values():
        has = unseen & set(org.rule_clause)
        assert (org.role == "train") == (not has) or org.role != "train"
        if org.role == "train":
            assert not has
    assert all(unseen <= set(o.rule_clause) for o in pack.orgs.values() if o.role == "test")


def test_split_is_by_batch_and_roles_map_to_splits(pack):
    seen = {}
    for it in pack.items:
        sp = split_of(it, pack, pack.seed)
        assert seen.setdefault(it.batch_id, sp) == sp
        if it.role == "test":
            assert sp == "test_s2"
        if it.role == "val":
            assert sp == "val_unseen"
    assert {split_of(i, pack, pack.seed) for i in pack.items if i.role == "train"} == {
        "train", "val", "test_s1"}


def test_violating_share_is_near_44_percent(pack):
    train = items_in_split(pack, "train")
    share = sum(i.label for i in train) / len(train)
    assert 0.36 < share < 0.52


def test_injection_phrasings_do_not_overlap():
    assert not set(INJECTION_TRAIN) & set(INJECTION_TEST)
    assert len(INJECTION_TRAIN) >= 15 and len(INJECTION_TEST) >= 15


def test_heldout_rule_clause_text_differs_from_train_text(pack):
    train_text = {c.text for o in pack.orgs.values() if o.role == "train" for c in o.clauses}
    for o in pack.orgs.values():
        if o.role == "train":
            continue
        for c in o.clauses:
            if o.clause_rule.get(c.clause_id):
                assert c.text not in train_text


# ---- rows ------------------------------------------------------------------------------
def test_eval_rows_use_rank_order_and_never_inject_gold(pack, retrievers):
    items = items_in_split(pack, "test_s2")
    rows = build_sft_rows(pack, items, retrievers, draws=3, train=False, seed=1)
    assert len(rows) == len(items)                           # one row per item
    for r in rows:
        ranked = [c.clause_id for c in retrievers[r.org_id].rank(
            next(i for i in items if i.item_id == r.item_id).response, 5)]
        assert r.context_ids == ranked
    # at least one retrieval miss stays a miss with a stand-in embedder
    assert any(r.label and not r.gold_in_context for r in rows)


def test_train_rows_always_contain_gold_and_shuffle_labels(pack, retrievers):
    items = items_in_split(pack, "train")
    rows = build_sft_rows(pack, items, retrievers, draws=3, train=True, seed=1)
    assert len(rows) == 3 * len(items)
    assert all(r.gold_in_context for r in rows if r.label)
    by_item = {}
    for r in rows:
        by_item.setdefault(r.item_id, set()).add(tuple(r.context_ids))
    assert sum(len(s) > 1 for s in by_item.values()) / len(by_item) > 0.9


def test_targets_cite_the_right_label(pack, retrievers):
    from c3.generation.rag_format import parse_output

    items = items_in_split(pack, "train")
    rows = build_sft_rows(pack, items, retrievers, draws=2, train=True, seed=2)
    for r in rows[:300]:
        p = parse_output(r.target)
        if r.label:
            cited_ids = [r.context_ids[int(lb[1:]) - 1] for lb in p.cited]
            assert set(cited_ids) == set(r.gold_clause_ids)
        else:
            assert p.verdict == "pass" and p.cited == []


def test_counterfactual_rows_permit_the_behaviour(pack, retrievers):
    items = items_in_split(pack, "train")
    cf = build_counterfactual_rows(pack, items, retrievers, seed=1)
    assert cf and all(r.target == "verdict: pass | cited: none" and r.label == 0 for r in cf)
    r = cf[0]
    gold_rule = next(i for i in items if i.item_id == r.item_id).gold_rules[0]
    from c3.training.rules_bank import RULES_BY_ID

    assert RULES_BY_ID[gold_rule].permit.split(".")[0][:40] in r.input


def test_multi_gold_items_get_no_counterfactual(pack, retrievers):
    items = [i for i in items_in_split(pack, "train") if len(i.gold_clause_ids) == 2]
    assert items and build_counterfactual_rows(pack, items, retrievers, seed=1) == []


def test_triplets_exclude_gold_from_negatives_and_use_train_only(pack, retrievers):
    items = items_in_split(pack, "train")
    trips = build_triplets(pack, items, retrievers)
    assert trips
    for t in trips:
        assert t["positive_id"] not in t["negative_ids"]
        assert t["org_id"].startswith("train-org")
    assert build_triplets(pack, items_in_split(pack, "test_s2"), retrievers) != []  # builder works...
    # ...but the pipeline only ever passes train items; the check below enforces it
    rows = build_sft_rows(pack, items[:20], retrievers, draws=1, train=True, seed=1)
    bad = [dict(t) for t in build_triplets(pack, items_in_split(pack, "test_s2")[:40], retrievers)]
    with pytest.raises(checks.DataCheckError):
        checks.check_no_heldout_clause_in_train(pack, rows, bad)


def test_recall_at_k_reports_all_cutoffs(pack, retrievers):
    r = recall_at_k(pack, items_in_split(pack, "test_s1"), retrievers)
    assert set(r) == {"recall@1", "recall@3", "recall@5", "recall@8"}
    assert r["recall@1"] <= r["recall@3"] <= r["recall@5"] <= r["recall@8"] <= 1.0


# ---- automatic checks ------------------------------------------------------------------
@pytest.fixture(scope="module")
def good_rows(pack, retrievers):
    items = items_in_split(pack, "train")
    rows = build_sft_rows(pack, items, retrievers, draws=3, train=True, seed=5)
    rows += build_counterfactual_rows(pack, items, retrievers, seed=5)
    return items, rows


def test_all_checks_pass_on_good_data(pack, retrievers, good_rows):
    items, rows = good_rows
    seen, uniq = set(), []
    for r in rows:
        if (r.input, r.target) not in seen:
            seen.add((r.input, r.target))
            uniq.append(r)
    report = checks.run_all(pack, items, uniq, build_triplets(pack, items, retrievers))
    assert report["status"] == "passed" and 0.36 < report["violating_share"] < 0.5


def test_check_catches_heldout_clause_leaking_into_a_train_row(pack, good_rows):
    _, rows = good_rows
    test_org = next(o for o in pack.orgs.values() if o.role == "test")
    cid = next(iter(test_org.rule_clause.values()))
    held_text = test_org.clause_by_id()[cid].text
    leaked = dataclasses.replace(rows[0], input=rows[0].input + "\n" + held_text)
    with pytest.raises(checks.DataCheckError, match="held-out clause"):
        checks.check_no_heldout_clause_in_train(pack, [leaked], [])


def test_check_catches_duplicates_missing_gold_and_bad_targets(good_rows):
    _, rows = good_rows
    with pytest.raises(checks.DataCheckError):
        checks.check_no_duplicates([rows[0], rows[0]])
    viol = next(r for r in rows if r.kind == "sft" and r.label)
    with pytest.raises(checks.DataCheckError):
        checks.check_violating_rows_contain_gold([dataclasses.replace(viol, gold_in_context=False)])
    with pytest.raises(checks.DataCheckError):
        checks.check_targets_parse([dataclasses.replace(viol, target="nonsense")])
    with pytest.raises(checks.DataCheckError):
        checks.check_targets_parse([dataclasses.replace(viol, target="verdict: violation | cited: C9")])
    with pytest.raises(checks.DataCheckError):
        checks.check_violating_share([r for r in rows if r.label and r.kind == "sft"])
    with pytest.raises(checks.DataCheckError):
        checks.check_prompt_length(rows[:5], count_tokens=lambda s: 5000)


def test_check_catches_a_batch_in_two_splits(pack):
    items = list(pack.items[:20])
    # force the same batch id onto items that land in different splits
    a = next(i for i in pack.items if split_of(i, pack, 7) == "train")
    b = next(i for i in pack.items if i.role == "test")
    clash = [a, dataclasses.replace(b, batch_id=a.batch_id)]
    with pytest.raises(checks.DataCheckError):
        checks.check_no_batch_crosses_splits(pack, clash)
    checks.check_no_batch_crosses_splits(pack, items)


# ---- metrics ---------------------------------------------------------------------------
def test_prf_and_bootstrap():
    m = prf([1, 1, 0, 0], [1, 0, 0, 1])
    assert m["precision"] == 0.5 and m["recall"] == 0.5 and m["f1"] == 0.5 and m["fpr"] == 0.5
    lo, hi = bootstrap_ci([1, 0] * 50, [1, 0] * 50, n=200)
    assert lo == hi == 1.0


def test_temperature_scaling_reduces_overconfidence():
    rng = random.Random(0)
    y = [rng.random() < 0.5 for _ in range(600)]
    # noisy, overconfident gaps: right sign only 80% of the time, magnitude huge
    gaps = [(8 if (yy if rng.random() < 0.8 else not yy) else -8) + rng.gauss(0, 1) for yy in y]
    t = fit_temperature(gaps, [int(v) for v in y])
    import numpy as np

    before = ece(sigmoid(np.asarray(gaps)), [int(v) for v in y])
    after = ece(sigmoid(np.asarray(gaps) / t), [int(v) for v in y])
    assert t > 2 and after < before


def test_mcnemar_and_precision_at_prevalence_and_threshold():
    r = mcnemar([True] * 30 + [False] * 2, [False] * 30 + [True] * 2)
    assert r["p"] < 0.001 and r["n_a_only"] == 30
    assert mcnemar([True, False], [True, False])["p"] == 1.0
    assert round(precision_at_prevalence(0.90, 0.05, 0.05), 2) == 0.49
    t = choose_threshold([0.1, 0.2, 0.8, 0.9], [0, 0, 1, 1], min_recall=1.0)
    assert 0.2 < t <= 0.8


def test_injection_variant_keeps_the_label(pack):
    it = next(i for i in pack.items if i.label)
    v = injection_variant(it, random.Random(1), INJECTION_TEST)
    assert v.label == it.label and v.gold_clause_ids == it.gold_clause_ids and v.injected
    assert any(p in v.response for p in INJECTION_TEST)


# ---- evaluation harness with a fake generator ------------------------------------------
class OracleGenerator:
    """Knows the answer. Used to prove the evaluation maths, not model quality."""

    def __init__(self, rows):
        self.by_input = {r.input: r for r in rows}

    def stage1_batch(self, prompts):
        return [6.0 if self.by_input[p].label and self.by_input[p].gold_in_context else -6.0
                for p in prompts]

    def generate_batch(self, prompts, max_new_tokens=40):
        from c3.generation.rag_format import build_prompt  # noqa: F401  (imported for clarity)

        outs = []
        for p in prompts:
            r = self.by_input[p]
            label = next((f"C{n + 1}" for n, cid in enumerate(r.context_ids)
                          if cid in r.gold_clause_ids), "C1")
            outs.append(f"verdict: violation | cited: {label} | quote: nope | reason: x")
        return outs


def test_evaluation_harness_with_oracle_stage1(pack, retrievers):
    from c3.training.pipeline import all_clauses

    rows = build_sft_rows(pack, items_in_split(pack, "test_s1"), retrievers, draws=1,
                          train=False, seed=1)
    res = evaluate_rows(rows, OracleGenerator(rows), all_clauses(pack), Calibration())
    m = res["metrics"]
    assert m["n"] == len(rows)
    # the oracle only flags violations whose gold clause was retrieved, so recall = gold_in_context
    assert m["recall"] == pytest.approx(m["gold_in_context"], abs=1e-9)
    assert m["precision"] == 1.0 and m["fpr"] == 0.0
    assert m["attribution_top1"] == 1.0
    assert m["raw_citation_validity"] == 1.0
    assert m["quote_grounded"] == 0.0 and m["repair_rate"] == 1.0   # the oracle's quote is fake


def test_train_val_and_test_use_different_frames():
    from c3.training.rules_bank import (
        LEADS_TEST,
        LEADS_TRAIN,
        LEADS_VAL,
        TAILS_TEST,
        TAILS_TRAIN,
        TAILS_VAL,
    )

    for a, b in ((LEADS_TRAIN, LEADS_VAL), (LEADS_TRAIN, LEADS_TEST), (LEADS_VAL, LEADS_TEST),
                 (TAILS_TRAIN, TAILS_VAL), (TAILS_TRAIN, TAILS_TEST), (TAILS_VAL, TAILS_TEST)):
        assert not (set(a) & set(b)) - {""}          # only the empty frame is shared


def test_decorate_never_changes_the_label_bearing_core():
    from c3.training.synth import decorate

    rng = random.Random(0)
    core = "Please stop taking your blood pressure tablets today."
    for role in ("train", "val", "test"):
        out = decorate(core, rng, role)
        if role != "train":
            assert core in out                       # val and test text keep the snippet verbatim
        assert "stop" in out.lower() or "quit" in out.lower() or "discontinue" in out.lower()


# ---- rule-removal counterfactuals ------------------------------------------------------------
def test_removal_rows_keep_the_answer_and_drop_the_broken_rule(pack, retrievers):
    from c3.training.rows import build_removal_rows

    items = items_in_split(pack, "train")
    rm = build_removal_rows(pack, items, retrievers, seed=1)
    assert rm and all(r.target == "verdict: pass | cited: none" and r.label == 0 for r in rm)
    by_item = {i.item_id: i for i in items}
    for r in rm[:60]:
        it = by_item[r.item_id]
        assert it.label == 1 and r.kind == "removal"
        assert not set(r.context_ids) & set(it.gold_clause_ids)          # the broken rule is gone
        assert len(r.context_ids) == 5 and not r.gold_in_context
        assert it.response.split()[3] in r.input                        # the same answer text
    again = build_removal_rows(pack, items, retrievers, seed=1)
    assert [r.input for r in rm] == [r.input for r in again]            # deterministic


def test_removal_rows_pass_the_data_checks_and_do_not_skew_the_violating_share(pack, retrievers):
    from c3.training.rows import build_removal_rows

    items = items_in_split(pack, "train")
    rows = build_sft_rows(pack, items, retrievers, draws=3, train=True, seed=5)
    rows += build_removal_rows(pack, items, retrievers, seed=5)
    seen, uniq = set(), []
    for r in rows:
        if (r.input, r.target) not in seen:
            seen.add((r.input, r.target))
            uniq.append(r)
    report = checks.run_all(pack, items, uniq, build_triplets(pack, items, retrievers))
    assert report["status"] == "passed" and 0.36 < report["violating_share"] < 0.5
