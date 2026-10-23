"""Turn the downloaded public policy sources into clause Markdown and parsed clauses.

  * eCFR XML (HIPAA, 42 CFR Part 2, 45 CFR Part 92): one clause per paragraph, ids that follow
    the legal citation, for example HIPAA-164.502(a)(1)(i). Lead-in text ("... as follows:")
    is carried into each sub-item so every clause can be read on its own.
  * PDFs (Sri Lanka PDPA, FDA guidance): read by the product's own PDF parser.

The Markdown is what an administrator would upload, so the same files feed training, the tests
and the demo.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET  # trusted source: official eCFR API over TLS
from dataclasses import dataclass
from pathlib import Path

from c3.config import COMPONENT_ROOT
from c3.policy.models import ParsedDocument, normalise_space
from c3.policy.parser import ParserLimits, PolicyParser

RAW = COMPONENT_ROOT / "data" / "raw"
OUT = COMPONENT_ROOT / "data" / "policies" / "public"

ROMAN_LOWER = {"i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x", "xi", "xii", "xiii",
               "xiv", "xv", "xvi", "xvii", "xviii", "xix", "xx"}
LABEL = re.compile(r"^\(([A-Za-z0-9]{1,5})\)\s*")
SKIP_TITLE = re.compile(
    r"\b(definitions?|\[reserved\]|reserved|effective date|compliance dates?|transition|"
    r"severability|preemption|relationship to|paperwork)\b", re.I)
MODAL = re.compile(
    r"\b(shall|must|may not|must not|shall not|prohibited|not permitted|required to|"
    r"is permitted|are permitted|may use or disclose|may disclose|may use)\b", re.I)
MIN_WORDS = 12
MAX_WORDS = 220


@dataclass(frozen=True)
class PublicPolicy:
    key: str
    code: str            # clause id prefix
    title: str
    file: str
    kind: str            # ecfr | pdf
    role: str            # train | val | test
    category: str
    severity: str


POLICIES: list[PublicPolicy] = [
    PublicPolicy("hipaa_privacy", "HIPAA", "HIPAA Privacy Rule (45 CFR 164 Subpart E)",
                 "hipaa_privacy.xml", "ecfr", "train", "disclosure", "high"),
    PublicPolicy("hipaa_breach", "HIPAABR", "HIPAA Breach Notification (45 CFR 164 Subpart D)",
                 "hipaa_breach.xml", "ecfr", "train", "incident_reporting", "medium"),
    PublicPolicy("cfr42_part2", "CFR42", "Confidentiality of Substance Use Disorder Records (42 CFR Part 2)",
                 "cfr42_part2.xml", "ecfr", "train", "disclosure", "high"),
    PublicPolicy("hhs_1557_92", "HHS92", "Nondiscrimination in Health Programs (45 CFR Part 92)",
                 "hhs_part92.xml", "ecfr", "val", "ai_usage", "medium"),
    PublicPolicy("lk_pdpa", "LKPDPA", "Sri Lanka Personal Data Protection Act, No. 9 of 2022",
                 "lk_pdpa.pdf", "pdf", "test", "data_handling", "high"),
    PublicPolicy("fda_cds", "FDACDS", "FDA Clinical Decision Support Software guidance (2022)",
                 "fda_cds.pdf", "pdf", "test", "ai_usage", "medium"),
]
BY_KEY = {p.key: p for p in POLICIES}


def _text(el: ET.Element) -> str:
    return normalise_space("".join(el.itertext()))


def _kind(label: str, stack: list[tuple[str, str]]) -> str:
    if label.isdigit():
        return "digit"
    if label.isupper():
        return "upper"
    if label in ROMAN_LOWER:
        if len(label) > 1:
            return "roman"
        # a single i, v or x is a letter only if it continues the top-level lettering
        top = stack[0][1] if stack and stack[0][0] == "lower" else None
        if top and len(top) == 1 and ord(label) == ord(top) + 1:
            return "lower"
        return "roman" if stack else "lower"
    return "lower"


def _depth(kind: str, stack: list[tuple[str, str]]) -> int:
    base = {"lower": 0, "digit": 1, "roman": 2, "upper": 3}[kind]
    if kind == "digit" and any(k == "upper" for k, _ in stack):
        return 4
    if kind == "roman" and any(k == "upper" for k, _ in stack):
        return 5
    return base


def ecfr_to_clauses(xml_path: Path, code: str) -> list[dict]:
    """[{id, title, text, section}] for every substantive paragraph."""
    root = ET.fromstring(xml_path.read_text(encoding="utf-8"))
    out: list[dict] = []
    for sec in root.iter("DIV8"):
        head = sec.find("HEAD")
        if head is None:
            continue
        number = sec.get("N", "")
        title = re.sub(r"^\s*§\s*[\d.]+\s*", "", _text(head)).strip(" .")
        if SKIP_TITLE.search(title) or not number:
            continue
        stack: list[tuple[str, str]] = []          # (kind, label)
        lead: dict[int, str] = {}                  # depth -> lead-in text
        pending: dict | None = None                # a lead-in clause not yet known to have children
        for p in sec.findall("P"):
            text = _text(p)
            m = LABEL.match(text)
            if not m:                              # an unlabelled paragraph: treat as body text
                if len(text.split()) >= MIN_WORDS:
                    out.append({"id": f"{code}-{number}", "title": title,
                                "text": text, "section": number})
                continue
            label = m.group(1)
            kind = _kind(label, stack)
            depth = _depth(kind, stack)
            stack = stack[:depth] + [(kind, label)]
            body = text[m.end():].strip()
            cid = f"{code}-{number}" + "".join(f"({lb})" for _, lb in stack)
            parent_lead = lead.get(depth - 1, "")
            full = f"{parent_lead} {body}".strip() if parent_lead else body
            if parent_lead and pending is not None:
                pending["drop"] = True             # the lead-in is carried by its children
            for d in [d for d in lead if d >= depth]:
                del lead[d]
            if body.endswith(":"):
                lead[depth] = " ".join(body.split()[:45])
                pending = {"id": cid, "title": title, "text": full, "section": number}
                out.append(pending)
                continue
            pending = None
            if len(full.split()) >= MIN_WORDS:
                out.append({"id": cid, "title": title, "text": full, "section": number})
    kept, used = [], set()
    for c in out:
        if c.get("drop") or len(c["text"].split()) < MIN_WORDS:
            continue
        while c["id"] in used:                      # an unlabelled paragraph or odd numbering
            c["id"] += "b"
        used.add(c["id"])
        kept.append(c)
    for c in kept:                                  # bound the length
        words = c["text"].split()
        if len(words) > MAX_WORDS:
            c["text"] = " ".join(words[:MAX_WORDS])
    return kept


def to_markdown(title: str, clauses: list[dict]) -> str:
    lines = [f"# {title}", ""]
    for c in clauses:
        lines += [f"## {c['id']} {c['title'][:90]}", "", c["text"], ""]
    return "\n".join(lines)


def build_policy_corpus(force: bool = False, log=print) -> dict[str, ParsedDocument]:
    """Write Markdown for every downloaded policy and return the parsed clauses by key."""
    OUT.mkdir(parents=True, exist_ok=True)
    parser = PolicyParser(ParserLimits(max_pages=300, max_clauses=5000, max_md_kb=4096))
    docs: dict[str, ParsedDocument] = {}
    for pol in POLICIES:
        src = RAW / pol.file
        if not src.exists():
            log(f"[policies] {pol.key}: not downloaded, skipped")
            continue
        if pol.kind == "ecfr":
            md_path = OUT / f"{pol.key}.md"
            if force or not md_path.exists():
                md_path.write_text(to_markdown(pol.title, ecfr_to_clauses(src, pol.code)),
                                   encoding="utf-8")
            doc = parser.parse_markdown(md_path.read_bytes(), doc_code=pol.code,
                                        category=pol.category, severity=pol.severity)
        else:
            doc = parser.parse_pdf(src.read_bytes(), doc_code=pol.code, category=pol.category,
                                   severity=pol.severity)
        docs[pol.key] = doc
        log(f"[policies] {pol.key}: {len(doc.clauses)} clauses ({pol.role})")
    return docs


# An assistant's answer can break a rule about disclosing, sharing, consent, access or breach
# reporting. It cannot break one about how a plan sponsor drafts its notices.
BEHAVIOUR = re.compile(
    r"\b(disclos\w*|shar(?:e|es|ed|ing)|consent|authori[sz]\w*|notif\w*|access|minimum necessary|"
    r"de-?identif\w*|employer|insur\w*|famil\w*|relatives?|personal representative|breach\w*|"
    r"personal data|data subject|controller|erasure|rectif\w*|sensitive|special categor\w*|"
    r"genetic|psychotherapy|marketing|sale of|research|law enforcement|emergency)\b", re.I)
ADMINISTRATIVE = re.compile(
    r"\b(plan sponsor|group health plan|underwriting|notice of privacy practices|business associate "
    r"(?:contract|agreement)|organi[sz]ational requirements|implementation specification|workforce|"
    r"transition|hybrid entit\w*|affiliated covered|the secretary|compliance review|investigat\w*|"
    r"civil money|appeal|retention|documentation)\b", re.I)


def behaviour_score(text: str) -> int:
    """Higher means more likely to be breakable by an assistant's answer."""
    return len({m.group(0).lower() for m in BEHAVIOUR.finditer(text)}) - 2 * len(
        ADMINISTRATIVE.findall(text))


def focus_clauses(doc: ParsedDocument, n: int, rng, min_score: int = 2) -> list:
    """The `n` obligation clauses an assistant could most plausibly break.

    Clauses scoring at least `min_score` come first (random among ties). If a document has fewer
    than `n` of those, the next best obligations fill the rest, so a small document still gives
    enough rows."""
    scored = sorted(((behaviour_score(c.title + " " + c.text) + rng.random() * 0.9, c)
                     for c in obligation_clauses(doc)), key=lambda sc: -sc[0])
    good = [c for sc, c in scored if sc >= min_score]
    rest = [c for sc, c in scored if sc < min_score and sc >= 0.5]
    return (good + rest)[:n]


def obligation_clauses(doc: ParsedDocument, min_words: int = 14) -> list:
    """Clauses that state a duty or a permission, the ones an answer can break."""
    return [c for c in doc.clauses if len(c.text.split()) >= min_words and MODAL.search(c.text)]


if __name__ == "__main__":
    build_policy_corpus(force=True)
