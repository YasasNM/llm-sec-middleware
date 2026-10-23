from pathlib import Path

import pytest
from c3_pdf_layouts import make_roman_pdf, make_statute_pdf

from c3.policy import PolicyLintError
from c3.policy.parser import ParserLimits, PolicyParser

RAW = Path(__file__).resolve().parents[2] / "data" / "raw"

ACT = [
    ("1", "Short title", "(1) This Act may be cited as the Example Data Act of 2022.\n"
                         "(2) This section comes into operation on certification."),
    ("2", "Lawful processing", "A controller shall process personal data lawfully and only for "
                               "specified, explicit and legitimate purposes."),
    ("3", "Right of access", "(1) A data subject may request access to personal data held about "
                             "them.\n(2) The controller shall respond within twenty one days of "
                             "the request being received."),
    ("4", "Security", "A controller shall implement appropriate technical and organisational "
                      "measures to protect personal data against loss."),
    ("5", "Breach notice", "A controller shall notify the Authority of a personal data breach "
                           "without undue delay and within seventy two hours."),
    ("6", "Penalties", "Any controller who contravenes this Act commits an offence under this "
                       "Act and shall on conviction be liable to a fine."),
    ("7", "Data officer", "Every controller shall appoint a data protection officer who shall "
                          "advise the controller on its obligations under this Act."),
    ("8", "Transfers", "A controller shall not transfer personal data to a third country unless "
                       "that country provides an adequate level of protection."),
    ("9", "Consent", "Where processing is based on consent, the controller shall be able to "
                     "demonstrate that the data subject has consented to the processing."),
    ("10", "Children", "A controller shall obtain the consent of a parent or guardian before "
                       "processing the personal data of a child under sixteen years."),
    ("11", "Records", "A controller shall keep a record of all processing activities carried "
                      "out under its responsibility and make it available to the Authority."),
    ("12", "Complaints", "A data subject may make a complaint to the Authority where the data "
                         "subject considers that this Act has been contravened."),
]


@pytest.fixture(scope="module")
def parser():
    return PolicyParser(ParserLimits(max_pages=100, max_clauses=2000))


def test_statute_pdf_uses_margin_notes_as_titles_and_ignores_the_header(parser):
    doc = parser.parse_pdf(make_statute_pdf(ACT, per_page=2), doc_code="EDA")
    ids = [c.clause_id for c in doc.clauses]
    assert ids == [f"EDA-{n}" for n in range(1, 13)]           # short sections stay whole
    by_id = {c.clause_id: c for c in doc.clauses}
    assert by_id["EDA-2"].title == "Lawful processing"
    assert by_id["EDA-5"].title == "Breach notice"
    assert "Personal Data Protection Act" not in " ".join(c.text for c in doc.clauses)
    assert "Lawful processing" not in by_id["EDA-2"].text          # margin note is not body text
    assert by_id["EDA-2"].text.startswith("A controller shall process personal data lawfully")


def test_header_with_alternating_page_number_is_not_a_heading(parser):
    doc = parser.parse_pdf(make_statute_pdf(ACT, per_page=2), doc_code="EDA")
    assert doc.clauses[0].clause_id.startswith("EDA-1")
    assert not any(c.title.startswith("Personal Data") for c in doc.clauses)


def test_roman_headings_beat_footnote_numbers(parser):
    doc = parser.parse_pdf(make_roman_pdf(), doc_code="GUIDE")
    titles = []
    for c in doc.clauses:
        if c.title not in titles:
            titles.append(c.title)
    assert titles == ["Introduction", "Background", "Scope", "Interpretation"]
    assert all(c.clause_id.startswith("GUIDE-") for c in doc.clauses)


def test_long_sections_are_split_into_bounded_clauses(parser):
    doc = parser.parse_pdf(make_roman_pdf(), doc_code="GUIDE")
    assert len(doc.clauses) > 4
    assert max(len(c.text.split()) for c in doc.clauses) <= 260
    assert len({c.clause_id for c in doc.clauses}) == len(doc.clauses)


def test_repeated_uppercase_titles_are_numbered_not_rejected(parser):
    md = ("NOTES\nFirst note text that is long enough.\nNOTES\nSecond note text here.\n"
          "NOTES\nThird note text here too.\n")
    doc = parser.parse_markdown(md, doc_code="X")
    assert [c.clause_id for c in doc.clauses] == ["X-notes", "X-notes-2", "X-notes-3"]


def test_markdown_still_reports_a_repeated_section_number(parser):
    md = ("## 1 Scope\nApplies to all staff.\n## 2 Duties\nStaff must act with care.\n"
          "## 3 Review\nReviewed yearly.\n")
    assert len(parser.parse_markdown(md, doc_code="X").clauses) == 3
    with pytest.raises(PolicyLintError):
        parser.parse_markdown(md + "## 2 Duties again\nMore text for the duplicate.\n",
                              doc_code="X")


# ---- real documents, when they have been downloaded ------------------------------------
@pytest.mark.skipif(not (RAW / "lk_pdpa.pdf").exists(), reason="run c3.training.public.fetch")
def test_real_sri_lankan_act_ingests(parser):
    doc = parser.parse_pdf((RAW / "lk_pdpa.pdf").read_bytes(), doc_code="LKPDPA")
    assert 100 <= len(doc.clauses) <= 300
    assert any(c.title == "Application of this Act" for c in doc.clauses)
    assert any("(" in c.clause_id for c in doc.clauses)          # long sections split by subsection
    assert max(len(c.text.split()) for c in doc.clauses) <= 330
    assert len({c.clause_id for c in doc.clauses}) == len(doc.clauses)


@pytest.mark.skipif(not (RAW / "fda_cds.pdf").exists(), reason="run c3.training.public.fetch")
def test_real_fda_guidance_ingests(parser):
    doc = parser.parse_pdf((RAW / "fda_cds.pdf").read_bytes(), doc_code="FDACDS")
    titles = {c.title for c in doc.clauses}
    assert {"Introduction", "Background", "Scope", "Examples"} <= titles
