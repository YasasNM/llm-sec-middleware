"""Text-layer PDF to lines. Scanned PDFs are rejected (OCR is out of scope).

Extraction works on word positions, so it can:
  * drop running headers and footers, also when the page number moves from side to side;
  * separate margin notes (printed Acts put each section title in the margin, left on even
    pages and right on odd pages) and offer them as clause titles.
Margin handling switches on only when a document shows the pattern on enough pages.
"""
from __future__ import annotations

import io
import re
import statistics
from collections import Counter
from dataclasses import dataclass, field

import pdfplumber

from .models import PolicyLintError
from .structure import PAGE_NUMBER, norm_line

EDGE_LINES = 2           # header and footer lines sit in the first and last 2 lines of a page
LINE_TOL = 3.0           # words whose tops differ by less than this are on one line
MARGIN_GAP = 8.0         # words this far outside the text block count as margin notes
WIDE_GAP = 18.0          # a gap this wide inside one line suggests a margin note
MIN_MARGIN_PAGES = 0.25  # share of pages that must show margin notes before they are separated
FACING_SHIFT = 30.0      # odd and even pages must place the text block this far apart


@dataclass
class PageText:
    lines: list[tuple[str, float]] = field(default_factory=list)          # (text, top)
    notes: list[tuple[float, float, str]] = field(default_factory=list)   # (top, bottom, text)


def _group_lines(words: list[dict]) -> list[list[dict]]:
    words = sorted(words, key=lambda w: (round(w["top"], 1), w["x0"]))
    lines: list[list[dict]] = []
    for w in words:
        if lines and abs(w["top"] - lines[-1][0]["top"]) <= LINE_TOL:
            lines[-1].append(w)
        else:
            lines.append([w])
    return [sorted(ln, key=lambda w: w["x0"]) for ln in lines]


def _has_gap(ln: list[dict]) -> bool:
    return any(b["x0"] - a["x1"] >= WIDE_GAP for a, b in zip(ln, ln[1:], strict=False))


def _block_bounds(lines: list[list[dict]]) -> tuple[float, float] | None:
    """Left edge = most common line start. Right edge = 75th percentile of line ends.

    Lines with an unusually wide internal gap usually carry a margin note, so they are left out
    of the estimate."""
    plain = [ln for ln in lines if not _has_gap(ln)]
    pool = plain if len(plain) >= 4 else lines
    starts = Counter(round(ln[0]["x0"] / 4) * 4 for ln in pool if len(ln) >= 3)
    ends = sorted(ln[-1]["x1"] for ln in pool if len(ln) >= 5)
    if not starts or len(ends) < 3:
        return None
    left = starts.most_common(1)[0][0]
    right = ends[int(0.75 * (len(ends) - 1))]
    return left, right


def _split_margin(lines: list[list[dict]], bounds) -> tuple[list[list[dict]], list[list[dict]]]:
    left, right = bounds
    body, margin = [], []
    for ln in lines:
        b = [w for w in ln if w["x1"] >= left - MARGIN_GAP and w["x0"] <= right + MARGIN_GAP]
        m = [w for w in ln if w not in b]
        if b:
            body.append(b)
        if m:
            margin.append(m)
    return body, margin


def _notes_from(margin_lines: list[list[dict]]) -> list[tuple[float, float, str]]:
    """Merge margin words on consecutive lines into note blocks."""
    notes: list[list] = []
    for ln in sorted(margin_lines, key=lambda ln: ln[0]["top"]):
        top, bottom = ln[0]["top"], ln[0]["bottom"]
        text = " ".join(w["text"] for w in ln)
        if notes and top - notes[-1][1] <= 14:
            notes[-1][1] = bottom
            notes[-1][2] += " " + text
        else:
            notes.append([top, bottom, text])
    return [(t, b, x) for t, b, x in notes]


def extract_pages(data: bytes, *, max_pages: int) -> list[PageText]:
    if not data.lstrip()[:5].startswith(b"%PDF"):
        raise PolicyLintError("NOT_A_PDF", "The file is not a PDF.")
    try:
        pdf = pdfplumber.open(io.BytesIO(data))
    except Exception as exc:  # corrupt, encrypted, malformed
        msg = (
            "The PDF is password protected."
            if "password" in str(exc).lower()
            else "The PDF could not be read. It may be damaged."
        )
        raise PolicyLintError("PDF_UNREADABLE", msg) from exc
    with pdf:
        if len(pdf.pages) > max_pages:
            raise PolicyLintError(
                "TOO_MANY_PAGES", f"The PDF has {len(pdf.pages)} pages. The limit is {max_pages}."
            )
        raw_pages: list[list[list[dict]]] = []
        for page in pdf.pages:
            try:
                words = page.extract_words(x_tolerance=2, y_tolerance=3)
            except Exception as exc:
                raise PolicyLintError(
                    "PDF_UNREADABLE", "A page of the PDF could not be read."
                ) from exc
            raw_pages.append(_group_lines(words))

    # Decide once for the whole document whether margin notes are present. A facing-page layout
    # shows itself by the text block sitting in a different place on odd and even pages.
    bounds_by_page = [_block_bounds(lines) for lines in raw_pages]
    odd = [b[0] for i, b in enumerate(bounds_by_page) if b and i % 2 == 0]
    even = [b[0] for i, b in enumerate(bounds_by_page) if b and i % 2 == 1]
    facing = (
        len(odd) >= 3 and len(even) >= 3
        and abs(statistics.median(odd) - statistics.median(even)) >= FACING_SHIFT
    )
    split: list[tuple[list[list[dict]], list[list[dict]]]] = []
    with_margin = total_margin = total_words = 0
    for lines, bounds in zip(raw_pages, bounds_by_page, strict=True):
        body, margin = _split_margin(lines, bounds) if (bounds and facing) else (lines, [])
        split.append((body, margin))
        n_margin = sum(len(m) for m in margin)
        with_margin += 1 if n_margin >= 2 else 0
        total_margin += n_margin
        total_words += sum(len(ln) for ln in lines)
    use_margin = bool(
        facing
        and with_margin / len(raw_pages) >= MIN_MARGIN_PAGES
        and total_margin >= 0.02 * max(1, total_words)
    )

    pages: list[PageText] = []
    for lines, (body, margin) in zip(raw_pages, split, strict=True):
        chosen = body if use_margin else lines
        pt = PageText(
            lines=[(" ".join(w["text"] for w in ln), ln[0]["top"]) for ln in chosen
                   if " ".join(w["text"] for w in ln).strip()],
            notes=_notes_from(margin) if use_margin else [],
        )
        pages.append(pt)
    return pages


def strip_repeated_lines(pages: list[PageText]) -> list[PageText]:
    """Remove running headers and footers and bare page numbers."""
    n = len(pages)
    counts: Counter[str] = Counter()
    for pg in pages:
        texts = [t for t, _ in pg.lines]
        edge = texts[:EDGE_LINES] + texts[-EDGE_LINES:]
        counts.update({norm_line(ln) for ln in edge if norm_line(ln)})
    # Repeated on at least half of the pages, and on at least two pages
    threshold = max(2, (n + 1) // 2)
    repeated = {k for k, c in counts.items() if c >= threshold} if n >= 2 else set()
    cleaned = []
    for pg in pages:
        keep = []
        for i, (ln, top) in enumerate(pg.lines):
            at_edge = i < EDGE_LINES or i >= len(pg.lines) - EDGE_LINES
            if PAGE_NUMBER.match(ln.strip()):
                continue
            if at_edge and norm_line(ln) in repeated:
                continue
            keep.append((ln, top))
        cleaned.append(PageText(keep, pg.notes))
    return cleaned


def pdf_to_lines(
    data: bytes, *, max_pages: int, min_text_chars: int
) -> tuple[list[tuple], int]:
    pages = extract_pages(data, max_pages=max_pages)
    total_chars = sum(len(t) for p in pages for t, _ in p.lines)
    if total_chars < min_text_chars:
        raise PolicyLintError("NO_TEXT_LAYER", "No text layer found. Upload a text-based PDF.")
    pages = strip_repeated_lines(pages)
    out: list[tuple] = []
    for pno, pg in enumerate(pages, start=1):
        hints: dict[int, str] = {}
        for top, bottom, text in pg.notes:
            for i, (ln, ltop) in enumerate(pg.lines):
                if top - 10 <= ltop <= bottom + 4 and re.match(r"^\d+[.)]?\s", ln.strip()):
                    hints.setdefault(i, text)
                    break
        for i, (ln, _top) in enumerate(pg.lines):
            out.append((ln, pno, hints.get(i)) if i in hints else (ln, pno))
    return out, len(pages)
