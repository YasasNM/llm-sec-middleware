import pytest
from c3_helpers import (
    POLICY,
    make_pdf,
    make_scanned_pdf,
    make_uppercase_pdf,
    markdown_policy,
    revised,
)

from c3.policy import PolicyLintError, diff_clauses, make_doc_code
from c3.policy.parser import ParserLimits, PolicyParser


# T-U01 ---------------------------------------------------------------------------------
def test_numbered_pdf_gives_ten_clauses_with_ids_and_clean_text(parser):
    doc = parser.parse_pdf(make_pdf(), doc_code="CAIG")
    ids = [c.clause_id for c in doc.clauses]
    assert ids == [f"CAIG-{n}" for n, _, _ in POLICY]
    assert doc.id_scheme == "numbered"
    assert doc.page_count == 4
    text = " ".join(c.text for c in doc.clauses)
    assert "Northfield General Hospital" not in text          # running header removed
    assert "Page " not in text                                # page numbers removed
    assert "Clinical AI Usage Guidelines 2024" not in text    # title page is not a clause


def test_clause_fields(parser):
    doc = parser.parse_pdf(make_pdf(), doc_code="CAIG", category="prescribing")
    c = next(c for c in doc.clauses if c.clause_id == "CAIG-4.1")
    assert c.title == "Dose Advice"
    assert c.number == "4.1"
    assert c.category == "prescribing"
    assert c.severity == "high"                         # category default
    assert c.lineage_key == "dose-advice"
    assert len(c.content_hash) == 64
    assert c.page is not None


def test_text_of_one_clause_does_not_leak_into_the_next(parser):
    doc = parser.parse_pdf(make_pdf(), doc_code="CAIG")
    c3 = next(c for c in doc.clauses if c.clause_id == "CAIG-3")
    assert "identifiable patient information" in c3.text
    assert "Names, national identity" not in c3.text    # that belongs to 3.1


# T-U03 ---------------------------------------------------------------------------------
def test_unnumbered_pdf_uses_slug_ids(parser):
    doc = parser.parse_pdf(make_uppercase_pdf(), doc_code="POL")
    assert doc.id_scheme == "slug"
    assert [c.clause_id for c in doc.clauses] == [
        "POL-data-handling", "POL-prescribing", "POL-incident-reporting",
    ]


# T-U04 ---------------------------------------------------------------------------------
def test_pdf_without_text_layer_is_rejected(parser):
    with pytest.raises(PolicyLintError) as e:
        parser.parse_pdf(make_scanned_pdf(), doc_code="X")
    assert e.value.code == "NO_TEXT_LAYER"
    assert "text-based PDF" in e.value.message


def test_not_a_pdf_and_corrupt_pdf(parser):
    with pytest.raises(PolicyLintError) as e:
        parser.parse_pdf(b"hello world", doc_code="X")
    assert e.value.code == "NOT_A_PDF"
    with pytest.raises(PolicyLintError) as e:
        parser.parse_pdf(b"%PDF-1.4\n garbage garbage", doc_code="X")
    assert e.value.code == "PDF_UNREADABLE"


def test_duplicate_ids_reject_the_file(parser):
    dup = POLICY + [("3", "Patient Data Again", "A second section three, which is a duplicate.")]
    with pytest.raises(PolicyLintError) as e:
        parser.parse_markdown(markdown_policy(dup), doc_code="CAIG")
    assert e.value.code == "DUPLICATE_CLAUSE_IDS"
    assert "CAIG-3" in e.value.message


def test_no_headings_is_rejected(parser):
    with pytest.raises(PolicyLintError) as e:
        parser.parse_markdown("just a paragraph with no structure at all, only prose.", doc_code="X")
    assert e.value.code == "NO_HEADINGS"


def test_size_and_clause_limits():
    small = PolicyParser(ParserLimits(max_pdf_mb=0.0001, max_clauses=3))
    with pytest.raises(PolicyLintError) as e:
        small.parse_pdf(make_pdf(), doc_code="X")
    assert e.value.code == "FILE_TOO_LARGE"
    few = PolicyParser(ParserLimits(max_clauses=3))
    with pytest.raises(PolicyLintError) as e:
        few.parse_markdown(markdown_policy(), doc_code="X")
    assert e.value.code == "TOO_MANY_CLAUSES"
    pages = PolicyParser(ParserLimits(max_pages=2))
    with pytest.raises(PolicyLintError) as e:
        pages.parse_pdf(make_pdf(), doc_code="X")
    assert e.value.code == "TOO_MANY_PAGES"


def test_numbered_sentences_are_not_headings(parser):
    md = markdown_policy().replace(
        "Staff must not paste identifiable patient information into any AI tool.",
        "Staff must follow these steps:\n1. Remove names before use.\n2. Check the output twice.",
    )
    doc = parser.parse_markdown(md, doc_code="CAIG")
    assert len(doc.clauses) == 10
    c3 = next(c for c in doc.clauses if c.clause_id == "CAIG-3")
    assert "Remove names before use" in c3.text


# T-U05 ---------------------------------------------------------------------------------
def test_markdown_clauses_with_numbered_headings(parser):
    doc = parser.parse_markdown(markdown_policy(), doc_code="CAIG")
    assert [c.clause_id for c in doc.clauses] == [f"CAIG-{n}" for n, _, _ in POLICY]
    assert "Intro text" not in " ".join(c.text for c in doc.clauses)


def test_markdown_explicit_ids_and_front_matter(parser):
    md = (
        "---\ncategory: data_handling\nseverity: low\n---\n"
        "## HOSP-DH-1 Identifiers\nDo not share identifiers outside the hospital network.\n"
        "## HOSP-DH-2 Storage\nStore records only in the approved record system.\n"
    )
    doc = parser.parse_markdown(md, doc_code="IGNORED")
    assert [c.clause_id for c in doc.clauses] == ["HOSP-DH-1", "HOSP-DH-2"]
    assert all(c.category == "data_handling" and c.severity == "low" for c in doc.clauses)


def test_markdown_bad_front_matter_and_severity(parser):
    with pytest.raises(PolicyLintError):
        parser.parse_markdown("---\nseverity: critical\n---\n## 1 A\nbody text here.\n## 2 B\nmore text.", doc_code="X")
    with pytest.raises(PolicyLintError):
        parser.parse_markdown(b"\xff\xfe\x00bad", doc_code="X")


def test_stored_doc_code_wins_over_filename(parser):
    a = parser.parse_markdown(markdown_policy(), doc_code="CAIG")
    b = parser.parse_markdown(markdown_policy(), doc_code="CAIG")
    assert [c.clause_id for c in a.clauses] == [c.clause_id for c in b.clauses]


# doc codes -----------------------------------------------------------------------------
def test_doc_code_rules():
    assert make_doc_code("clinical-ai-guidelines-2024.pdf") == "CAIG"
    assert make_doc_code("data_handling_policy.md") == "DH"
    assert make_doc_code("clinical-ai-guidelines.pdf", {"CAIG"}) == "CAIG2"
    assert make_doc_code("2024.pdf") == "DOC"
    assert make_doc_code("guide.md") == "GUID"


# T-U02 ---------------------------------------------------------------------------------
def test_revision_diff(parser):
    old = parser.parse_markdown(markdown_policy(), doc_code="CAIG").clauses
    rev = revised(
        change={"3.2": "AI outputs containing patient data may be stored for 30 days only."},
        drop={"7"},
        add=[("8", "Training", "All staff must complete AI safety training each year.")],
    )
    new = parser.parse_markdown(markdown_policy(rev), doc_code="CAIG").clauses
    d = diff_clauses(old, new)
    assert [c.clause_id for c in d.changed] == ["CAIG-3.2"]
    assert d.removed == ["CAIG-7"]
    assert [c.clause_id for c in d.added] == ["CAIG-8"]
    assert len(d.unchanged) == 8
    assert d.renumbered == []


def test_whitespace_only_edit_is_not_a_change(parser):
    old = parser.parse_markdown(markdown_policy(), doc_code="CAIG").clauses
    spaced = markdown_policy().replace("must not paste", "must   not  paste")
    new = parser.parse_markdown(spaced, doc_code="CAIG").clauses
    assert diff_clauses(old, new).changed == []


# T-I09 (logic part) --------------------------------------------------------------------
def test_inserted_section_reports_renumbered(parser):
    old = parser.parse_markdown(markdown_policy(), doc_code="CAIG").clauses
    shifted = [
        ("1", "Purpose", POLICY[0][2]), ("2", "Scope", POLICY[1][2]),
        ("3", "Governance", "A new governance section is inserted before patient data."),
        ("4", "Patient Data", POLICY[2][2]),
    ]
    new = parser.parse_markdown(markdown_policy(shifted), doc_code="CAIG").clauses
    d = diff_clauses(old, new)
    assert ("CAIG-3", "CAIG-4") in d.renumbered   # Patient Data moved 3 -> 4
