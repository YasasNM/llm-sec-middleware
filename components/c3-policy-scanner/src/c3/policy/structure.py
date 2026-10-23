"""Turn a list of text lines into clauses (shared by the PDF and Markdown parsers).

Lines are tuples `(text, page)` or `(text, page, title_hint)`. A title hint is a heading found
elsewhere on the page, for example a margin note in a printed Act.

Heading styles, tried in this order: explicit ids (`CAIG-3.1 Title`), numbered (`3.1 Title`),
Roman numerals (`IV. Title`), UPPERCASE titles. Numbered and Roman headings must run in
order, which filters out page numbers and list items that look like headings.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .models import (
    SEVERITIES,
    Clause,
    ParsedDocument,
    PolicyLintError,
    content_hash,
    normalise_space,
    slugify,
)

# '3', '3.1', '3.2.4' followed by a capitalised title (blueprint 12.1)
NUM_HEAD = re.compile(r"^(?P<num>\d+(?:\.\d+){0,3})[.)]?\s+(?P<title>[A-Z][^\n]{2,120})$")
# 'CAIG-3.1 Title', ids may contain dots, dashes and parentheses: 'HIPAA-164.502(a)(1)'
ID_HEAD = re.compile(
    r"^(?P<id>[A-Z][A-Z0-9]{1,9}-(?=[A-Za-z0-9.()\-]*\d)[A-Za-z0-9][A-Za-z0-9.()\-]{0,40})"
    r"[:\s]+(?P<title>\S[^\n]{1,160})$"
)
STATUTE_HEAD = re.compile(r"^(?P<num>\d{1,3})\.\s+(?P<rest>\S.*)$")
ROMAN_HEAD = re.compile(r"^(?P<num>[IVX]{1,6})\.\s+(?P<title>[A-Z][^\n]{2,120})$")
PAGE_NUMBER = re.compile(
    r"^(?:page\s+)?\d+(?:\s*(?:of|/)\s*\d+)?$|^[-–—]\s*\d+\s*[-–—]$", re.I
)
SENTENCE_END = (".", ";", ",", ":", "?", "!")
SUBSECTION = re.compile(r"^\((\d{1,2})\)\s+\S")
MAX_NUMBER_JUMP = 6          # a heading may skip a few numbers (repealed sections), not more
ROMAN_VALUES = {"I": 1, "V": 5, "X": 10}


def norm_line(line: str) -> str:
    """'Page 1 of 3', 'Page 2 of 3' and '12 Act Name' / 'Act Name 13' normalise alike."""
    return re.sub(r"\s+", " ", re.sub(r"\d+", "", line.strip().lower())).strip()


def roman_to_int(s: str) -> int:
    total = 0
    for i, ch in enumerate(s):
        v = ROMAN_VALUES[ch]
        total += -v if i + 1 < len(s) and ROMAN_VALUES[s[i + 1]] > v else v
    return total


def is_numbered_heading(line: str) -> re.Match | None:
    m = NUM_HEAD.match(line.strip())
    if not m:
        return None
    title = m.group("title").strip()
    # A list item or a sentence is not a heading
    if title.endswith(SENTENCE_END) or len(title.split()) > 14:
        return None
    return m


def is_upper_heading(line: str) -> bool:
    s = line.strip()
    letters = [c for c in s if c.isalpha()]
    return (
        3 <= len(s) <= 80
        and len(letters) >= 3
        and s == s.upper()
        and not PAGE_NUMBER.match(s)
        and not s.endswith(SENTENCE_END)
    )


@dataclass
class _Section:
    key: str          # number, explicit id, roman numeral, or empty for slug headings
    title: str
    lines: list[str]
    page: int | None


def _unpack(item) -> tuple[str, int | None, str | None]:
    return item[0], item[1], (item[2] if len(item) > 2 else None)


def _number_tuple(key: str) -> tuple[int, ...]:
    return tuple(int(p) for p in key.split("."))


def split_sections(lines: list[tuple], scheme: str, strict: bool = True) -> list[_Section]:
    sections: list[_Section] = []
    current: _Section | None = None
    last_num: tuple[int, ...] | None = None
    last_roman = 0
    for item in lines:
        raw, page, hint = _unpack(item)
        line = raw.rstrip()
        key = title = None
        if scheme == "numbered":
            m = is_numbered_heading(line)
            body_first = None
            if m is None:
                # statute style 'N. (1) text': trusted with a margin note, or when the first
                # words after the number open a numbered subsection
                st = STATUTE_HEAD.match(line.strip())
                if st and (hint or re.match(r"^\(\d{1,2}\)\s", st.group("rest"))):
                    m, body_first = st, st.group("rest")
            if m:
                num = _number_tuple(m.group("num"))
                ok = True
                if strict:             # PDFs are noisy: numbers must run in order
                    ok = last_num is None and num[0] <= 30
                    if last_num is not None:
                        ok = num > last_num and num[0] - last_num[0] <= MAX_NUMBER_JUMP
                if ok:
                    if hint:
                        body_first = body_first or m.group("title")
                    key = m.group("num")
                    fallback = " ".join((body_first or "").split()[:8]) if body_first else ""
                    title = (hint or (fallback if body_first else m.group("title"))).strip()
                    last_num = num
        elif scheme == "explicit":
            m = ID_HEAD.match(line.strip())
            if m:
                key, title = m.group("id"), m.group("title").strip()
        elif scheme == "roman":
            m = ROMAN_HEAD.match(line.strip())
            if m:
                val = roman_to_int(m.group("num"))
                if not strict or val == last_roman + 1 or (last_roman == 0 and val <= 2):
                    key, title = m.group("num"), m.group("title").strip()
                    last_roman = val
        elif scheme == "slug" and is_upper_heading(line):
            key, title = "", line.strip().title()
        if key is not None:
            current = _Section(key=key, title=title, lines=[], page=page)
            if scheme == "numbered" and hint and body_first:
                current.lines.append(body_first)        # the heading line is also the text
            sections.append(current)
        elif current is not None and line.strip():
            current.lines.append(line.strip())
    return sections


def detect_scheme(lines: list[tuple], strict: bool = True) -> str:
    """Pick the heading style that finds at least two headings and covers the most text.

    Ties go to the earlier style in the order explicit, numbered, roman, uppercase. A style must
    cover clearly more text (15% more) to beat an earlier one."""
    total = sum(len(_unpack(it)[0].split()) for it in lines) or 1
    explicit = split_sections(lines, "explicit", strict)
    roman = split_sections(lines, "roman", strict)
    if len(explicit) < 2 and len(roman) >= 3:
        return "roman"
    best, best_cov = "none", 0.0
    for scheme in ("explicit", "numbered", "roman", "slug"):
        secs = split_sections(lines, scheme, strict)
        if len(secs) < 2:
            continue
        cov = sum(len(" ".join(sec.lines).split()) for sec in secs) / total
        if best == "none" or cov > best_cov * 1.15:
            best, best_cov = scheme, cov
    return best


def _windows(text: str, max_words: int) -> list[str]:
    sentences = re.split(r"(?<=[.;:!?])\s+", text)
    chunks, cur = [], []
    for sent in sentences:
        cur.append(sent)
        if len(" ".join(cur).split()) >= max_words:
            chunks.append(" ".join(cur))
            cur = []
    if cur:
        if chunks and len(" ".join(cur).split()) < max_words // 3:
            chunks[-1] += " " + " ".join(cur)
        else:
            chunks.append(" ".join(cur))
    return chunks


def split_long(lines: list[str], max_words: int) -> list[tuple[str, str]]:
    """Split an over-long section. Returns [(id_suffix, text)]; a single ('', text) if short.

    First by numbered subsections '(1)', '(2)' at line starts (statutes), keeping a short
    lead-in sentence with each part. Parts that are still too long, and sections without
    subsections, are cut into windows of about `max_words` words at sentence boundaries."""
    text = normalise_space(" ".join(lines))
    if len(text.split()) <= max_words:
        return [("", text)]
    parts: list[tuple[str, list[str]]] = []
    lead: list[str] = []
    for ln in lines:
        m = SUBSECTION.match(ln)
        if m:
            parts.append((f"({m.group(1)})", [ln]))
        elif parts:
            parts[-1][1].append(ln)
        else:
            lead.append(ln)
    pieces: list[tuple[str, str]] = []
    if len(parts) >= 2:
        lead_text = " ".join(" ".join(lead).split()[:25])
        seen_suffix: set[str] = set()
        for suffix, body in parts:
            t = normalise_space(" ".join(body))
            if len(t.split()) < 6:
                continue
            while suffix in seen_suffix:
                suffix += "b"                         # a repeated '(1)' inside one section
            seen_suffix.add(suffix)
            full = normalise_space((lead_text + " " + t) if lead_text else t)
            if len(full.split()) > 2 * max_words:
                windows = _windows(full, max_words)
                pieces += [(f"{suffix}.c{i + 1}", c) for i, c in enumerate(windows)]
            else:
                pieces.append((suffix, full))
        if len(pieces) >= 2:
            return pieces
    chunks = _windows(text, max_words)
    return [(f".c{i + 1}", c) for i, c in enumerate(chunks)] if len(chunks) > 1 else [("", text)]


def build_document(
    lines: list[tuple],
    *,
    doc_code: str,
    category: str,
    default_severity: str,
    max_clauses: int,
    page_count: int | None = None,
    max_clause_words: int = 160,
    strict_order: bool = True,
) -> ParsedDocument:
    if default_severity not in SEVERITIES:
        raise PolicyLintError("BAD_SEVERITY", f"Severity must be one of {SEVERITIES}.")
    scheme = detect_scheme(lines, strict_order)
    if scheme == "none":
        raise PolicyLintError(
            "NO_HEADINGS",
            "No clause headings found. Use numbered headings such as '3.1 Title' "
            "or upload a document with clear section titles.",
        )
    sections = split_sections(lines, scheme, strict_order)
    warnings: list[str] = []
    clauses: list[Clause] = []
    used: set[str] = set()
    dups: list[str] = []
    for sec in sections:
        pieces = split_long(sec.lines, max_clause_words)
        if not pieces or not pieces[0][1]:
            warnings.append(f"Heading '{sec.title}' has no body text and was skipped.")
            continue
        if scheme in ("numbered", "roman"):
            base = f"{doc_code}-{sec.key}"
        elif scheme == "explicit":
            base = sec.key
        else:
            base = f"{doc_code}-{slugify(sec.title)}"
        number = sec.key if scheme in ("numbered", "roman") else ""
        for suffix, text in pieces:
            clause_id = base + suffix
            if clause_id in used:
                if scheme == "slug":                 # a repeated title: number it
                    n = 2
                    while f"{clause_id}-{n}" in used:
                        n += 1
                    clause_id = f"{clause_id}-{n}"
                else:                                # a repeated number needs a human
                    dups.append(clause_id)
            used.add(clause_id)
            clauses.append(
                Clause(
                    clause_id=clause_id,
                    doc_code=doc_code,
                    number=number + suffix,
                    title=sec.title,
                    text=text,
                    category=category,
                    severity=default_severity,
                    content_hash=content_hash(sec.title, text),
                    lineage_key=slugify(sec.title),
                    order=len(clauses),
                    page=sec.page,
                )
            )
    if dups:
        raise PolicyLintError(
            "DUPLICATE_CLAUSE_IDS",
            "Duplicate clause ids: " + ", ".join(sorted(set(dups))[:5])
            + ". Each id must be unique.",
        )
    if not clauses:
        raise PolicyLintError("NO_CLAUSES", "Headings were found but none had body text.")
    if len(clauses) > max_clauses:
        raise PolicyLintError(
            "TOO_MANY_CLAUSES",
            f"The document has {len(clauses)} clauses. The limit is {max_clauses}.",
        )
    words = [len(c.text.split()) for c in clauses]
    if sum(words) / len(words) < 8:
        warnings.append("Clauses are very short on average. Check the clause preview.")
    if len(clauses) < 3:
        warnings.append("Only a few clauses were found. Check the clause preview.")
    return ParsedDocument(
        doc_code=doc_code,
        clauses=clauses,
        id_scheme="slug" if scheme == "slug" else "numbered",
        warnings=warnings,
        page_count=page_count,
    )
