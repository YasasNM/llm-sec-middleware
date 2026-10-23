"""Test helpers: build policy PDFs and Markdown, count embedder calls."""
from __future__ import annotations

import io
import textwrap

import numpy as np
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from c3.indexing.embedder import HashingEmbedder

# A small realistic policy. (number, title, body)
POLICY: list[tuple[str, str, str]] = [
    ("1", "Purpose", "This guideline sets rules for using AI tools in clinical work at the hospital."),
    ("2", "Scope", "It applies to all clinicians, nurses and administrative staff who use AI tools."),
    ("3", "Patient Data", "Staff must not paste identifiable patient information into any AI tool."),
    ("3.1", "Identifiers", "Names, national identity numbers, phone numbers and addresses are identifiers."),
    ("3.2", "Retention", "AI tool outputs containing patient data must not be stored outside the record system."),
    ("4", "Prescribing", "An AI tool must never be the sole basis for a prescribing decision."),
    ("4.1", "Dose Advice", "Dose recommendations from an AI tool must be verified by a pharmacist before use."),
    ("5", "Disclosure", "Staff must not share patient diagnoses with third parties through AI tools."),
    ("6", "Incident Reporting", "Any suspected AI related data exposure must be reported within 24 hours."),
    ("7", "Review", "This guideline is reviewed every twelve months by the ethics committee."),
]


def make_pdf(
    clauses: list[tuple[str, str, str]] = POLICY,
    *,
    header: str | None = "Northfield General Hospital - Clinical AI Guidelines",
    footer: bool = True,
    per_page: int = 4,
    title_page: bool = True,
) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    w, h = A4
    pages = [clauses[i : i + per_page] for i in range(0, len(clauses), per_page)]
    total = len(pages) + (1 if title_page else 0)
    pno = 0

    def furniture():
        if header:
            c.setFont("Helvetica", 9)
            c.drawString(50, h - 30, header)
        if footer:
            c.setFont("Helvetica", 9)
            c.drawString(w / 2 - 30, 25, f"Page {pno} of {total}")

    if title_page:
        pno += 1
        furniture()
        c.setFont("Helvetica-Bold", 20)
        c.drawString(50, h - 200, "Clinical AI Usage Guidelines 2024")
        c.showPage()
    for chunk in pages:
        pno += 1
        furniture()
        y = h - 80
        for num, title, body in chunk:
            c.setFont("Helvetica-Bold", 12)
            c.drawString(50, y, f"{num} {title}")
            y -= 18
            c.setFont("Helvetica", 10)
            for line in textwrap.wrap(body, 90):
                c.drawString(50, y, line)
                y -= 14
            y -= 12
        c.showPage()
    c.save()
    return buf.getvalue()


def make_uppercase_pdf() -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    _, h = A4
    y = h - 80
    for title, body in [
        ("DATA HANDLING", "Do not enter patient identifiers into external tools or websites."),
        ("PRESCRIBING", "Prescriptions must be written by a registered practitioner only."),
        ("INCIDENT REPORTING", "Report every data exposure to the privacy officer at once."),
    ]:
        c.setFont("Helvetica-Bold", 12)
        c.drawString(50, y, title)
        y -= 18
        c.setFont("Helvetica", 10)
        c.drawString(50, y, body)
        y -= 30
    c.save()
    return buf.getvalue()


def make_scanned_pdf() -> bytes:
    """A PDF page with a drawing and no text layer, like a scan."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.rect(50, 50, 400, 600, fill=1)
    c.save()
    return buf.getvalue()


def markdown_policy(clauses: list[tuple[str, str, str]] = POLICY, front: str = "") -> str:
    parts = [front] if front else []
    parts.append("# Clinical AI Guidelines\n\nIntro text before the first clause.\n")
    for num, title, body in clauses:
        parts.append(f"## {num} {title}\n\n{body}\n")
    return "\n".join(parts)


def revised(
    base=POLICY,
    *,
    change: dict[str, str] | None = None,
    drop: set[str] = frozenset(),
    add: list[tuple[str, str, str]] = (),
):
    out = []
    for num, title, body in base:
        if num in drop:
            continue
        out.append((num, title, (change or {}).get(num, body)))
    out.extend(add)
    return sorted(out, key=lambda t: [int(x) for x in t[0].split(".")])


class CountingEmbedder(HashingEmbedder):
    """Counts how many texts were embedded, to prove that only changed clauses are."""

    def __init__(self):
        super().__init__()
        self.texts_embedded = 0
        self.calls = 0

    def embed(self, texts):
        self.calls += 1
        self.texts_embedded += len(texts)
        return super().embed(texts)


def unit(vec: np.ndarray) -> np.ndarray:
    return vec / (np.linalg.norm(vec) or 1.0)


KEY = "test-key-123"


def wait_until(pred, timeout=5.0, step=0.02):
    import time

    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(step)
    return False


# ---- scan-path helpers -----------------------------------------------------------------
import re  # noqa: E402
from dataclasses import dataclass  # noqa: E402

STOPWORDS = set("the a an and or of to in is are be must not for with on that this it as by at "
                "any all may no do does into from your you can will have has been being their "
                "them they staff patient patients assistant".split())
PROHIBIT = re.compile(r"\b(must not|never|do not|may not|not permitted|not allowed)\b", re.I)


def content_words(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]+", s.lower()) if w not in STOPWORDS and len(w) > 3}


@dataclass
class _S1:
    gap: float
    ms: float = 1.0


class StubGenerator:
    """Reads the prompt like a tiny rule engine: a prohibition clause that shares enough
    content words with the response is violated. Lets tests prove that editing a policy flips
    the verdict with no retraining. It is not a model."""

    version = "stub-gen-v1"

    def __init__(self, min_overlap: int = 2, reason: str | None = None, cite_all: bool = False,
                 raise_on_stage1: bool = False, garbage: bool = False, sleep_s: float = 0.0,
                 real_quote: bool = False):
        self.min_overlap, self.reason, self.cite_all = min_overlap, reason, cite_all
        self.raise_on_stage1, self.garbage, self.sleep_s = raise_on_stage1, garbage, sleep_s
        self.real_quote = real_quote
        self.stage1_calls = self.stage2_calls = 0
        self._cites: dict[int, list[str]] = {}
        self._texts: dict[int, dict[str, str]] = {}

    @staticmethod
    def _parse(prompt: str):
        head, resp = prompt.split("\nRESPONSE:\n", 1)
        clauses = re.findall(r"^\[(C\d+)\] ([^:]+): (.*)$", head, re.M)
        return clauses, resp

    def stage1(self, prompt: str):
        import time

        self.stage1_calls += 1
        if self.sleep_s:
            time.sleep(self.sleep_s)
        if self.raise_on_stage1:
            raise RuntimeError("model crashed")
        clauses, resp = self._parse(prompt)
        rw = content_words(resp)
        hit = [lb for lb, _t, text in clauses
               if PROHIBIT.search(text) and len(rw & content_words(text)) >= self.min_overlap]
        s1 = _S1(6.0 if hit else -6.0)
        self._cites[id(s1)] = hit
        self._texts[id(s1)] = {lb: text for lb, _t, text in clauses}
        return s1

    def stage2(self, s1, max_new_tokens: int = 40):
        self.stage2_calls += 1
        hit = self._cites.pop(id(s1), [])
        texts = self._texts.pop(id(s1), {})
        if self.garbage:
            return "lorem ipsum", 2.0
        cited = (hit if self.cite_all else hit[:1]) or ["C1"]
        reason = self.reason or "The response breaks this clause."
        quote = texts.get(cited[0], "x").split(". ")[0] if self.real_quote else "not real"
        return (f"verdict: violation | cited: {', '.join(cited)} | quote: {quote} | "
                f"reason: {reason}", 3.0)

    def probability(self, gap: float) -> float:
        import math

        return 1.0 / (1.0 + math.exp(-gap))


SCAN_POLICY = [
    ("1", "Purpose", "This guideline sets rules for using AI tools in clinical work."),
    ("2", "Prescribing", "The assistant must never recommend specific morphine doses for patients."),
    ("3", "Disclosure", "Staff must not share patient diagnoses with employers or insurers."),
    ("4", "Referral", "Referrals follow the approved pathway for every specialist service."),
]


def scan_request(text: str, *, org="northfield", request_id="req_20261020_000001", manifest=(),
                 categories=None) -> dict:
    body = {"request_id": request_id, "org_id": org, "user_id": "user_001",
            "session_id": "sess_01", "response_text": text,
            "placeholder_manifest": list(manifest)}
    if categories:
        body["categories"] = categories
    return body
