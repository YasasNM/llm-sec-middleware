import pytest

from c3.generation.rag_format import (
    DEFAULT_SEV_TAU,
    build_prompt,
    effective_tau,
    finalize,
    first_sentence,
    format_target,
    head_tail,
    label_context,
    parse_output,
    template_reason,
)
from c3.policy.models import Clause, content_hash


def clause(cid, title, text, severity="medium"):
    return Clause(cid, cid.split("-")[0], "", title, text, "general", severity,
                  content_hash(title, text), title.lower(), 0)


CLAUSES = [
    clause("CAGV-3", "Prescribing advice",
           "The assistant provides general clinical reference only. It does not replace the prescriber."),
    clause("CAGV-3.2", "Stopping treatment",
           "The assistant must not advise a patient to stop prescribed medication without clinician review.",
           "high"),
    clause("CAGV-1", "Purpose and scope", "This policy sets out how clinical staff may use AI."),
    clause("CAGV-2", "Patient data handling", "AI assistants must never store patient identifiers.",
           "high"),
]


@pytest.fixture
def ctx():
    return label_context(CLAUSES)


def test_labels_hide_real_ids_and_map_back(ctx):
    prompt = build_prompt("Please stop your tablets today.", ctx)
    assert "CAGV" not in prompt
    assert prompt.splitlines()[0].startswith("Check the RESPONSE")
    assert "[C2] Stopping treatment:" in prompt
    assert ctx["C2"].clause_id == "CAGV-3.2" and ctx["C2"].rank == 2


def test_shuffled_order_keeps_retrieval_rank(ctx):
    shuffled = label_context(CLAUSES, order=[2, 0, 3, 1])
    assert shuffled["C1"].clause_id == "CAGV-1" and shuffled["C1"].rank == 3
    assert shuffled["C4"].clause_id == "CAGV-3.2" and shuffled["C4"].rank == 2


def test_head_tail_keeps_the_end_of_a_long_response():
    text = " ".join(f"w{i}" for i in range(300))
    out = head_tail(text, 90)
    words = out.split()
    assert words[0] == "w0" and words[-1] == "w299" and "..." in words
    assert len(words) == 91


def test_clause_text_is_cut_to_fifty_words():
    long_clause = [clause("X-1", "Long", " ".join(["word"] * 120))]
    prompt = build_prompt("hi", label_context(long_clause))
    assert len(prompt.split("[C1] Long: ")[1].split("\nRESPONSE:")[0].split()) == 50


def test_pass_target_is_exact(ctx):
    assert format_target([], ctx) == "verdict: pass | cited: none"


def test_violation_target_round_trips(ctx):
    t = format_target(["C2"], ctx)
    assert t == (
        "verdict: violation | cited: C2 | quote: The assistant must not advise a patient to stop "
        "prescribed medication without clinician review. | reason: The response breaks this "
        "clause: stopping treatment."
    )
    p = parse_output(t)
    assert p.verdict == "violation" and p.cited == ["C2"]
    assert p.quote.startswith("The assistant must not") and p.reason.endswith("stopping treatment.")


def test_pipe_in_free_text_cannot_break_the_format():
    odd = [clause("X-1", "A | B", "Rule one | rule two applies here. Second sentence.")]
    c = label_context(odd)
    t = format_target(["C1"], c)
    p = parse_output(t)
    assert p.verdict == "violation" and p.cited == ["C1"] and "|" not in (p.quote + p.reason)


def test_parse_garbage_and_partial():
    assert not parse_output("lorem ipsum").usable
    assert not parse_output("").usable
    p = parse_output("verdict: violation | cited: C1, C3")
    assert p.usable and p.cited == ["C1", "C3"] and p.quote is None


def test_first_sentence_and_template():
    assert first_sentence("One two. Three four.") == "One two."
    assert template_reason("Stopping treatment") == "The response breaks this clause: stopping treatment."


def test_effective_tau_uses_strictest_severity(ctx):
    assert effective_tau(ctx) == DEFAULT_SEV_TAU["high"]
    only_medium = label_context([CLAUSES[0], CLAUSES[2]])
    assert effective_tau(only_medium) == DEFAULT_SEV_TAU["medium"]


# ---- the finalize table from blueprint 12.5 ----------------------------------------------
def good(ctx):
    return parse_output(format_target(["C2"], ctx))


def test_high_probability_valid_label_and_quote_is_verified(ctx):
    f = finalize(good(ctx), ctx, 0.95, 0.40)
    assert f.verdict == "violation" and f.citation_status == "verified"
    assert f.cited[0].clause_id == "CAGV-3.2" and f.problems == []
    assert f.raw_valid_labels and f.raw_quote_grounded


def test_quote_not_in_clause_is_repaired_with_real_sentence(ctx):
    p = parse_output("verdict: violation | cited: C2 | quote: the model made this up | reason: x")
    f = finalize(p, ctx, 0.9, 0.4)
    assert f.citation_status == "repaired_quote"
    assert f.cited[0].quote == first_sentence(ctx["C2"].text)
    assert f.raw_quote_grounded is False


def test_label_out_of_range_is_repaired_citation(ctx):
    p = parse_output("verdict: violation | cited: C9 | quote: x | reason: y")
    f = finalize(p, ctx, 0.9, 0.4)
    assert f.citation_status == "repaired_citation"
    assert f.cited[0].label == "C1"                 # best-ranked clause
    assert f.raw_valid_labels is False


def test_mixed_valid_and_invalid_labels_count_as_repaired(ctx):
    p = parse_output("verdict: violation | cited: C2, C9")
    f = finalize(p, ctx, 0.9, 0.4)
    assert [c.label for c in f.cited] == ["C2"] and f.citation_status == "repaired_citation"


def test_text_says_pass_but_probability_high_is_violation(ctx):
    f = finalize(parse_output("verdict: pass | cited: none"), ctx, 0.95, 0.4)
    assert f.verdict == "violation" and f.citation_status == "repaired_citation"
    assert "verdict_text_prob_disagree" in f.problems


def test_text_says_violation_but_probability_low_stricter_wins(ctx):
    f = finalize(good(ctx), ctx, 0.05, 0.4)
    assert f.verdict == "violation" and "verdict_text_prob_disagree" in f.problems


def test_low_probability_and_pass_text_is_a_pass(ctx):
    f = finalize(parse_output("verdict: pass | cited: none"), ctx, 0.05, 0.4)
    assert f.verdict == "pass" and f.cited == []


def test_garbage_text_uses_probability(ctx):
    f = finalize(parse_output("???"), ctx, 0.9, 0.4)
    assert f.verdict == "violation" and "text_unparseable" in f.problems
    assert f.citation_status == "repaired_citation"
    assert finalize(parse_output("???"), ctx, 0.01, 0.4).verdict == "pass"


def test_garbage_text_and_no_probability_fails_closed(ctx):
    f = finalize(parse_output("???"), ctx, None, 0.4)
    assert f.verdict == "error" and f.error_code == "UNPARSEABLE_NO_PROBABILITY"
    assert finalize(None, ctx, None, 0.4).verdict == "error"


def test_no_context_fails_closed():
    f = finalize(parse_output("verdict: pass | cited: none"), {}, 0.1, 0.4)
    assert f.verdict == "error" and f.error_code == "NO_CONTEXT"


def test_citations_only_come_from_the_context(ctx):
    for text in ("verdict: violation | cited: C1, C2, C3, C4", "verdict: violation | cited: C2, C2"):
        f = finalize(parse_output(text), ctx, 0.9, 0.4)
        assert {c.clause_id for c in f.cited} <= {c.clause_id for c in CLAUSES}
        assert len({c.label for c in f.cited}) == len(f.cited)


def test_reason_is_replaced_by_template_when_repaired(ctx):
    p = parse_output("verdict: violation | cited: C9 | quote: x | reason: my invented reason")
    f = finalize(p, ctx, 0.9, 0.4)
    assert f.reason == template_reason("Prescribing advice")      # C1 is the best-ranked clause
