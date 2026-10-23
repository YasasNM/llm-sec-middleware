import csv
import json
from pathlib import Path

import pytest

from c3.policy.parser import ParserLimits, PolicyParser
from c3.training.public import fetch, generate, review
from c3.training.public.datacard import build as build_datacard
from c3.training.public.policies import ecfr_to_clauses, obligation_clauses, to_markdown

RAW = Path(__file__).resolve().parents[2] / "data" / "raw"

XML = """<?xml version="1.0"?>
<DIV6 N="E" TYPE="SUBPART"><HEAD>Subpart E</HEAD>
<DIV8 N="164.501" TYPE="SECTION"><HEAD>&#xA7; 164.501 Definitions.</HEAD>
<P>Business associate means a person who performs functions on behalf of a covered entity here.</P></DIV8>
<DIV8 N="164.502" TYPE="SECTION"><HEAD>&#xA7; 164.502 Uses and disclosures: General rules.</HEAD>
<P>(a) Standard. A covered entity may not use or disclose protected health information, except as follows:</P>
<P>(1) To the individual who is the subject of the information, at the request of that individual.</P>
<P>(2) Pursuant to and in compliance with a valid authorization given by the individual under the rules.</P>
<P>(i) A first roman item that the covered entity must handle with care in every single case.</P>
<P>(ii) A second roman item that the covered entity must also document before any disclosure occurs.</P>
<P>(b) Minimum necessary. When using or disclosing protected health information, a covered entity must make reasonable efforts to limit it.</P>
<P>(c) Short.</P>
<P>(h) Another letter paragraph that the covered entity must respect when it discloses information at all.</P>
<P>(i) The letter i here continues the lettering after h and is a top level paragraph of this section.</P>
</DIV8>
<DIV8 N="164.510" TYPE="SECTION"><HEAD>&#xA7; 164.510 [Reserved]</HEAD>
<P>(a) This paragraph is reserved and must never be turned into a clause by the adapter at all.</P></DIV8>
</DIV6>"""


@pytest.fixture
def clauses(tmp_path):
    f = tmp_path / "x.xml"
    f.write_text(XML, encoding="utf-8")
    return ecfr_to_clauses(f, "HIPAA")


def test_ids_follow_the_legal_citation(clauses):
    ids = [c["id"] for c in clauses]
    assert "HIPAA-164.502(a)(1)" in ids and "HIPAA-164.502(a)(2)" in ids
    assert "HIPAA-164.502(b)" in ids
    assert len(ids) == len(set(ids))


def test_lead_in_is_carried_into_each_sub_item_and_not_kept_alone(clauses):
    by = {c["id"]: c for c in clauses}
    assert "HIPAA-164.502(a)" not in by                      # the lead-in alone is not a clause
    assert by["HIPAA-164.502(a)(1)"]["text"].startswith("Standard. A covered entity may not use or disclose")
    assert "To the individual" in by["HIPAA-164.502(a)(1)"]["text"]


def test_roman_numerals_nest_but_the_letter_i_continues_the_letters(clauses):
    ids = {c["id"] for c in clauses}
    assert "HIPAA-164.502(a)(2)(i)" in ids and "HIPAA-164.502(a)(2)(ii)" in ids
    assert "HIPAA-164.502(i)" in ids                         # follows (h): a top-level letter


def test_definitions_reserved_and_tiny_paragraphs_are_skipped(clauses):
    ids = " ".join(c["id"] for c in clauses)
    assert "164.501" not in ids and "164.510" not in ids and "(c)" not in ids.replace("(a)(2)", "")


def test_markdown_round_trip_through_the_product_parser(clauses):
    md = to_markdown("Test", clauses)
    doc = PolicyParser(ParserLimits(max_clauses=500)).parse_markdown(md, doc_code="HIPAA")
    assert [c.clause_id for c in doc.clauses] == [c["id"] for c in clauses]
    assert len(obligation_clauses(doc, min_words=10)) >= 3


# ---- generation keeps only what the judge agrees with ------------------------------------
class FakeBackend:
    name = "fake-llm"

    def __init__(self):
        self.calls = 0

    def chat(self, prompts, max_new_tokens=140, temperature=0.7):
        out = []
        for p in prompts:
            self.calls += 1
            if "Write ONE short" in p:
                out.append("Can I send the file to her employer?")
            elif "careless" in p:
                out.append("Reply: Sure, just email the whole record to the employer today, no need for consent.")
            else:
                out.append("Please ask the patient for written consent before sharing anything with the employer.")
        return out

    def judge(self, prompts):
        return [0.9 if "careless" not in p and "email the whole record" in p else 0.1 for p in prompts]


def test_generate_keeps_only_agreeing_answers(tmp_path):
    clauses = [{"clause_id": f"X-{i}", "text": "Rule: do not share records with employers.",
                "doc_key": "hipaa_privacy"} for i in range(3)]
    out = tmp_path / "gen.jsonl"
    rows = generate.generate(clauses, FakeBackend(), out_path=out, log=lambda *_: None)
    assert len(rows) == 3 and all(r["keep_violating"] and r["keep_compliant"] for r in rows)
    assert rows[0]["violating"].startswith("Sure, just email")        # "Reply:" prefix is stripped
    assert rows[0]["model"] == "fake-llm" and rows[0]["prompt_version"] == generate.PROMPT_VERSION
    assert len(out.read_text().splitlines()) == 3

    class Strict(FakeBackend):
        def judge(self, prompts):
            return [0.5] * len(prompts)                      # the judge is unsure about everything

    rows = generate.generate(clauses, Strict(), log=lambda *_: None)
    assert not any(r["keep_violating"] for r in rows)         # 0.5 < 0.6
    assert not any(r["keep_compliant"] for r in rows)         # 0.5 > 0.3


def test_short_or_empty_answers_are_dropped():
    class Short(FakeBackend):
        def chat(self, prompts, max_new_tokens=140, temperature=0.7):
            return ["Yes."] * len(prompts)

        def judge(self, prompts):
            return [0.95] * len(prompts)

    rows = generate.generate([{"clause_id": "X-1", "text": "r", "doc_key": "k"}], Short(),
                             log=lambda *_: None)
    assert not rows[0]["keep_violating"]


# ---- human review sheet ---------------------------------------------------------------------
def test_review_export_apply_round_trip(tmp_path):
    src = tmp_path / "g.jsonl"
    recs = [{"clause_id": f"X-{i}", "doc_key": "d", "question": "q", "violating": f"bad answer {i}",
             "compliant": f"good answer {i}", "judge_p_violating": 0.9, "judge_p_compliant": 0.1,
             "keep_violating": True, "keep_compliant": i != 2} for i in range(3)]
    src.write_text("\n".join(json.dumps(r) for r in recs), encoding="utf-8")
    sheet = tmp_path / "review.csv"
    assert review.export(src, sheet) == 6
    rows = list(csv.DictReader(sheet.open(encoding="utf-8-sig")))
    assert len(rows) == 6 and rows[0]["intended"] == "VIOLATES the clause"
    for r in rows:
        if r["row_id"] == "0:violating":
            r["human_ok"] = "no"                              # the person rejects it
        if r["row_id"] == "2:compliant":
            r["human_ok"] = "yes"                             # the person rescues a dropped one
    with sheet.open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=review.COLUMNS)
        w.writeheader()
        w.writerows(rows)
    dst = tmp_path / "reviewed.jsonl"
    stats = review.apply(src, sheet, dst)
    assert stats == {"reviewed": 2, "removed_by_human": 1, "added_by_human": 1}
    out = [json.loads(x) for x in dst.read_text().splitlines()]
    assert out[0]["keep_violating"] is False and out[0]["human_reviewed_violating"] is True
    assert out[2]["keep_compliant"] is True and out[1]["human_reviewed_violating"] is False


# ---- source registry and data card ------------------------------------------------------------
def test_every_source_has_a_licence_and_a_unique_key():
    keys = [s.key for s in fetch.SOURCES]
    assert len(keys) == len(set(keys))
    assert all(s.licence and s.url and s.filename for s in fetch.SOURCES)
    assert fetch._hf_url("hf://owner/name/dir/file.parquet").endswith("/owner/name/resolve/main/dir/file.parquet")
    assert "/api/datasets/owner/name/parquet/default/train/0.parquet" in fetch._hf_url("hf://owner/name")
    big = [s for s in fetch.SOURCES if s.optional]
    assert [s.key for s in big] == ["dynabench_train"]        # the 500 MB file is opt-in


def test_datacard_names_every_downloaded_source():
    text = build_datacard()
    assert "Held-out means whole documents" in text and "machine-generated" in text.lower()
    if fetch.MANIFEST.exists():
        for s in json.loads(fetch.MANIFEST.read_text())["sources"]:
            assert s["title"] in text


# ---- the assembled pack (needs the downloaded data) ---------------------------------------
needs_data = pytest.mark.skipif(
    not (RAW / "hipaa_privacy.xml").exists() or not (RAW / "aegis2.parquet").exists(),
    reason="run python -m c3.training.public.fetch")


@needs_data
def test_public_pack_splits_balance_and_provenance(tmp_path):
    from c3.indexing.embedder import HashingEmbedder
    from c3.training import checks
    from c3.training.public.pack import build_public_pack, provenance
    from c3.training.rows import build_retrievers, build_sft_rows, items_in_split

    gen = tmp_path / "gen.jsonl"
    recs = []
    for key, cid in (("hipaa_privacy", "HIPAA-164.502(a)(1)"), ("hhs_1557_92", None), ("lk_pdpa", None)):
        pack0 = None
        recs.append({"clause_id": cid or "x", "doc_key": key, "question": "q",
                     "violating": "Sure, go ahead and share everything you have with the employer today.",
                     "compliant": "Please check the rule and ask the patient for consent before sharing.",
                     "keep_violating": True, "keep_compliant": True, "judge_p_violating": .9,
                     "judge_p_compliant": .1})
        assert pack0 is None
    gen.write_text("\n".join(json.dumps(r) for r in recs), encoding="utf-8")
    pack = build_public_pack(gen, n_medquad=200, n_aegis_safe=200, n_aegis_unsafe=200, log=lambda *_: None)

    roles = {o.org_id: o.role for o in pack.orgs.values()}
    assert roles == {"pub-train": "train", "pub-val": "val", "pub-test": "test", "pub-conduct": "train"}
    test_clauses = {c.clause_id for c in pack.orgs["pub-test"].clauses}
    assert any(c.startswith("LKPDPA-") for c in test_clauses) and not any(
        c.startswith("HIPAA") for c in test_clauses)
    prov = provenance(pack)
    assert prov["share_handwritten_templates"] == 0.0 and prov["share_real"] > 0.9
    train = [i for i in pack.items if i.role == "train"]
    share = sum(i.label for i in train) / len(train)
    assert 0.38 < share < 0.52

    # held-out clauses never reach a training row, and the check would catch it if they did
    rets = build_retrievers(pack, HashingEmbedder())
    rows = build_sft_rows(pack, items_in_split(pack, "train"), rets, draws=1, train=True, seed=1)
    checks.check_no_heldout_clause_in_train(pack, rows, [])
    held = next(iter(test_clauses))
    import dataclasses
    bad = dataclasses.replace(rows[0], context_ids=[*rows[0].context_ids, held])
    with pytest.raises(checks.DataCheckError, match="held-out clause"):
        checks.check_no_heldout_clause_in_train(pack, [bad], [])


# ---- clause focus and text folding ---------------------------------------------------------
def test_behaviour_score_prefers_rules_an_answer_can_break():
    from c3.training.public.policies import behaviour_score

    breakable = "A covered entity may not disclose health information to an employer without the patient's consent."
    admin = "The group health plan sponsor must amend the plan documents and keep documentation for the Secretary."
    assert behaviour_score(breakable) >= 3
    assert behaviour_score(admin) < 1


def test_focus_clauses_take_the_best_and_fill_from_the_rest():
    import random

    from c3.policy.models import ParsedDocument
    from c3.policy.parser import ParserLimits, PolicyParser
    from c3.training.public.policies import focus_clauses

    md = "\n".join(
        f"## TT-{i} Rule {i}\n{t}\n" for i, t in enumerate([
            "A covered entity must not disclose records to an employer without written consent today.",
            "Staff shall not share patient data with family members unless the patient agrees fully.",
            "The organisation must maintain suitable documentation of its internal committee meetings.",
        ], start=1))
    doc: ParsedDocument = PolicyParser(ParserLimits()).parse_markdown(md, doc_code="TT")
    top = focus_clauses(doc, 2, random.Random(0))
    assert {c.clause_id for c in top} == {"TT-1", "TT-2"}


def test_fold_text_makes_legal_text_readable_by_the_model():
    from c3.generation.rag_format import build_prompt, fold_text, label_context
    from c3.policy.models import Clause, content_hash

    raw = "§ 164.502 Use if x < 5 and y ≥ 3: “quoted” {set} a~b — done ± 2"
    out = fold_text(raw)
    assert out == 'section 164.502 Use if x less than 5 and y at least 3: "quoted" (set) a-b - done +/- 2'
    c = Clause("D-1", "D", "1", "T§", raw, "general", "high", content_hash("T", raw), "t", 0)
    prompt = build_prompt("Reply with “this” and <tags>", label_context([c]))
    assert "§" not in prompt and "<" not in prompt and "“" not in prompt
