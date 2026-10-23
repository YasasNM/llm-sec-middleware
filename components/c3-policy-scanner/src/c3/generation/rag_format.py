"""Prompt, target, parser and grounding check for the generator (blueprint 9.11, 12.3 to 12.5).

Everything here is plain Python with no model dependency. The same code builds training
rows, decodes model output, and decides the final verdict, so training and inference cannot
drift apart.

Local labels: the model sees C1..Ck, never real clause ids. Real ids are restored in code.
"""
from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field

from c3.policy.models import Clause, normalise_space

INSTRUCTION = (
    "Check the RESPONSE against the POLICY CLAUSES. Answer: verdict, cited labels, quote, reason."
)
DEFAULT_SEV_TAU = {"high": 0.40, "medium": 0.50, "low": 0.60}
VERDICT_PREFIX = "verdict:"
STATUS_ORDER = {"verified": 0, "repaired_quote": 1, "repaired_citation": 2}
MIN_QUOTE_CHARS = 12


# ---- text the model can read ----------------------------------------------------------
# flan-t5's vocabulary cannot represent these characters (they become unknown tokens), and real
# legal text uses several of them. Folding is applied to everything the model sees, in training
# and at inference, so the two always agree.
_FOLD = {
    "<": " less than ", "\\": "/", "^": "", "`": "'", "{": "(", "}": ")", "~": "-",
    "\u00a7": "section ", "\u2212": "-", "\u00b1": "+/-", "\u00d7": "x", "\u2265": " at least ",
    "\u2264": " at most ", "\u2192": "->", "\u00b7": ".", "\u00a9": "(c)", "\u00bd": "1/2",
    "\u2011": "-", "\u2010": "-", "\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"',
    "\u2013": "-", "\u2014": "-", "\u2026": "...", "\u00a0": " ", "\u2022": "-",
}
_FOLD_TABLE = str.maketrans(_FOLD)
_SAFE_EXTRA = set("\u00e9\u20ac\u00a3\u00ae\u2122")


def fold_text(text: str) -> str:
    """Replace characters flan-t5 cannot represent, and drop other unusual ones."""
    text = text.translate(_FOLD_TABLE)
    text = "".join(ch for ch in text if ch == "\n" or ch == "\t" or 32 <= ord(ch) < 127
                   or ch in _SAFE_EXTRA)
    return re.sub(r"[ ]{2,}", " ", text)


# ---- context -------------------------------------------------------------------------
@dataclass(frozen=True)
class ContextItem:
    label: str                 # "C1"
    clause_id: str
    title: str
    text: str
    severity: str
    rank: int                  # 1 = best retrieval match


Context = dict[str, ContextItem]        # label -> item, insertion order = prompt order


def label_context(clauses: Sequence[Clause], *, order: Sequence[int] | None = None) -> Context:
    """Label clauses C1..Ck in the given sequence (retrieval rank order at inference).

    `order` is a permutation of range(len(clauses)) used in training to shuffle the labels.
    The item's `rank` always keeps its retrieval rank, whatever the label.
    """
    idx = list(order) if order is not None else list(range(len(clauses)))
    ctx: Context = {}
    for pos, i in enumerate(idx):
        c = clauses[i]
        label = f"C{pos + 1}"
        ctx[label] = ContextItem(label, c.clause_id, fold_text(c.title), fold_text(c.text),
                                 c.severity, i + 1)
    return ctx


def truncate_words(text: str, max_words: int) -> str:
    words = text.split()
    return text if len(words) <= max_words else " ".join(words[:max_words])


def head_tail(text: str, max_words: int) -> str:
    """Keep the first two thirds and the last third of the allowed words (blueprint 12.3)."""
    words = text.split()
    if len(words) <= max_words:
        return " ".join(words)
    head = (max_words * 2) // 3
    tail = max_words - head
    return " ".join(words[:head] + ["..."] + words[-tail:])


def build_prompt(
    response: str, ctx: Context, *, max_resp_words: int = 110, max_clause_words: int = 50
) -> str:
    lines = [INSTRUCTION, "POLICY CLAUSES:"]
    for label, it in ctx.items():
        lines.append(f"[{label}] {it.title}: {truncate_words(it.text, max_clause_words)}")
    lines += ["RESPONSE:", head_tail(normalise_space(fold_text(response)), max_resp_words)]
    return "\n".join(lines)


# ---- target --------------------------------------------------------------------------
def first_sentence(text: str) -> str:
    text = normalise_space(text)
    m = re.search(r"(?<=[.!?])\s", text)
    return text[: m.start()] if m else text


def clean_free_text(text: str) -> str:
    return normalise_space(text.replace("|", "/"))


def template_reason(title: str) -> str:
    return clean_free_text(f"The response breaks this clause: {title.lower()}.")


def format_target(
    cited_labels: Sequence[str], ctx: Context, *, with_quote: bool = True
) -> str:
    """'verdict: pass | cited: none' or the violation form. Quote = first sentence of top cited."""
    if not cited_labels:
        return "verdict: pass | cited: none"
    top = ctx[cited_labels[0]]
    parts = ["verdict: violation", "cited: " + ", ".join(cited_labels)]
    if with_quote:
        parts.append("quote: " + clean_free_text(first_sentence(top.text)))
    parts.append("reason: " + template_reason(top.title))
    return " | ".join(parts)


# ---- parser --------------------------------------------------------------------------
@dataclass
class Parsed:
    verdict: str | None = None            # "violation" | "pass" | None (unreadable)
    cited: list[str] = field(default_factory=list)
    quote: str | None = None
    reason: str | None = None
    raw: str = ""

    @property
    def usable(self) -> bool:
        return self.verdict is not None


_SEG = re.compile(r"\s*\|\s*(?=(?:verdict|cited|quote|reason)\s*:)", re.I)
_LABEL = re.compile(r"\bC(\d{1,2})\b")


def parse_output(text: str) -> Parsed:
    p = Parsed(raw=text or "")
    if not text:
        return p
    for seg in _SEG.split(text.strip()):
        key, sep, val = seg.partition(":")
        if not sep:
            continue
        key, val = key.strip().lower(), val.strip()
        if key == "verdict":
            low = val.lower()
            if low.startswith("violation"):
                p.verdict = "violation"
            elif low.startswith("pass"):
                p.verdict = "pass"
        elif key == "cited":
            p.cited = [f"C{m.group(1)}" for m in _LABEL.finditer(val)]
        elif key == "quote":
            p.quote = val
        elif key == "reason":
            p.reason = val
    return p


# ---- grounding check -----------------------------------------------------------------
@dataclass
class CitedItem:
    label: str
    clause_id: str
    title: str
    quote: str
    citation_status: str
    severity: str
    rank: int


@dataclass
class Final:
    verdict: str                          # "violation" | "pass" | "error"
    cited: list[CitedItem] = field(default_factory=list)
    reason: str | None = None
    citation_status: str | None = None    # worst status among cited
    problems: list[str] = field(default_factory=list)
    error_code: str | None = None
    p_violation: float | None = None
    tau: float | None = None
    raw_valid_labels: bool | None = None  # were all generated labels in the context?
    raw_quote_grounded: bool | None = None


def effective_tau(ctx: Context, sev_tau: dict[str, float] | None = None) -> float:
    """The strictest severity in the context sets the bar (blueprint 12.5)."""
    sev_tau = sev_tau or DEFAULT_SEV_TAU
    if not ctx:
        return min(sev_tau.values())
    return min(sev_tau[it.severity] for it in ctx.values())


def quote_is_grounded(quote: str | None, item: ContextItem) -> bool:
    if not quote:
        return False
    q = normalise_space(quote).strip(" .").lower()
    return len(q) >= MIN_QUOTE_CHARS and q in normalise_space(item.text).lower()


def _worse(a: str | None, b: str) -> str:
    return b if a is None or STATUS_ORDER[b] > STATUS_ORDER[a] else a


def finalize(
    parsed: Parsed | None, ctx: Context, p_violation: float | None, tau: float
) -> Final:
    """Turn model output and probability into a safe decision. The stricter reading wins."""
    out = Final(verdict="pass", p_violation=p_violation, tau=tau)
    if not ctx:
        out.verdict, out.error_code = "error", "NO_CONTEXT"
        return out
    usable_text = parsed is not None and parsed.usable
    if not usable_text and p_violation is None:
        out.verdict, out.error_code = "error", "UNPARSEABLE_NO_PROBABILITY"
        return out

    prob_says_violation = p_violation is not None and p_violation >= tau
    text_says_violation = usable_text and parsed.verdict == "violation"
    if p_violation is not None and usable_text and prob_says_violation != text_says_violation:
        out.problems.append("verdict_text_prob_disagree")
    if parsed is not None and not usable_text:
        out.problems.append("text_unparseable")      # stage 2 ran and its text was unreadable

    if not (prob_says_violation or text_says_violation):
        return out                                      # pass, nothing to cite

    out.verdict = "violation"
    labels = [lb for lb in (parsed.cited if usable_text else []) if lb in ctx]
    status_all = "verified"
    if usable_text:
        out.raw_valid_labels = bool(parsed.cited) and len(labels) == len(parsed.cited)
        if parsed.cited and len(labels) != len(parsed.cited):
            out.problems.append("label_out_of_range")
            status_all = "repaired_citation"       # the model cited a label that does not exist
    seen: set[str] = set()
    labels = [lb for lb in labels if not (lb in seen or seen.add(lb))]

    if not labels:
        # nothing usable cited: fall back to the best-ranked clause
        best = min(ctx.values(), key=lambda it: it.rank)
        labels = [best.label]
        status_all = "repaired_citation"
        out.problems.append("citation_missing")

    for n, lb in enumerate(labels):
        it = ctx[lb]
        status = status_all
        if n == 0:
            grounded = status_all == "verified" and quote_is_grounded(parsed.quote, it)
            if usable_text and status_all == "verified":
                out.raw_quote_grounded = grounded
            if status_all == "verified" and not grounded:
                # A missing quote is also repaired with the real first sentence
                status = "repaired_quote"
                out.problems.append("quote_not_in_clause")
            quote = (
                clean_free_text(parsed.quote) if grounded and parsed and parsed.quote
                else first_sentence(it.text)
            )
        else:
            quote = first_sentence(it.text)
        out.cited.append(
            CitedItem(lb, it.clause_id, it.title, quote, status, it.severity, it.rank)
        )
        out.citation_status = _worse(out.citation_status, status)

    top = ctx[labels[0]]
    out.reason = (
        clean_free_text(parsed.reason)
        if usable_text and parsed.reason and "repaired" not in (out.citation_status or "")
        else template_reason(top.title)
    )
    return out
