"""PDF generators that imitate real-world layouts (used by parser tests)."""
from __future__ import annotations

import io
import textwrap

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas


def make_statute_pdf(sections: list[tuple[str, str, str]], per_page: int = 3) -> bytes:
    """A printed-Act look: facing pages, a margin note beside each section, and a running
    header whose page number sits on the left of even pages and the right of odd pages.

    sections: (number, margin note, body text). Bodies may contain '(1) ...' subsections."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    _, h = A4
    pages = [sections[i : i + per_page] for i in range(0, len(sections), per_page)]
    for pno, chunk in enumerate(pages, start=1):
        odd = pno % 2 == 1
        body_x, note_x = (150, 440) if odd else (230, 70)
        c.setFont("Helvetica", 9)
        header = "Personal Data Protection Act, No. 9 of 2022"
        c.drawString(150 if odd else 230, h - 40, f"{header} {pno}" if odd else f"{pno} {header}")
        y = h - 90
        for num, note, body in chunk:
            c.setFont("Helvetica", 8)
            for k, ln in enumerate(textwrap.wrap(note, 20)):
                c.drawString(note_x, y - k * 10, ln)
            c.setFont("Helvetica", 10)
            first = True
            for para in body.split("\n"):
                for ln in textwrap.wrap(para, 52):
                    c.drawString(body_x, y, f"{num}. {ln}" if first else ln)
                    first = False
                    y -= 13
            y -= 16
        c.showPage()
    c.save()
    return buf.getvalue()


def make_roman_pdf() -> bytes:
    """A narrative guidance document: Roman-numeral headings, numbered footnotes at page
    bottoms, and an indented block that shifts the text on even pages."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    _, h = A4
    body = ("The agency intends to apply this policy to software that supports clinicians. "
            "Software must not replace independent review by the clinician. ") * 12
    for pno, (num, title) in enumerate(
        [("I", "Introduction"), ("II", "Background"), ("III", "Scope"), ("IV", "Interpretation")],
        start=1,
    ):
        c.setFont("Helvetica-Bold", 12)
        c.drawString(72, h - 80, f"{num}. {title}")
        c.setFont("Helvetica", 10)
        y = h - 105
        for ln in textwrap.wrap(body, 90):
            c.drawString(108 if pno % 2 == 0 else 72, y, ln)
            y -= 13
        c.setFont("Helvetica", 7)
        c.drawString(72, 50, f"{pno} See the agency report, page {pno * 3}, available online.")
        c.showPage()
    c.save()
    return buf.getvalue()
