"""PolicyParser: one entry point for PDF and Markdown (blueprint 19.1)."""
from __future__ import annotations

from dataclasses import dataclass

from .markdown import markdown_to_lines, parse_front_matter
from .models import SEVERITIES, ParsedDocument, PolicyLintError
from .pdf import pdf_to_lines
from .structure import build_document


@dataclass(frozen=True)
class ParserLimits:
    max_pdf_mb: float = 20
    max_md_kb: float = 1024
    max_pages: int = 200
    max_clauses: int = 500
    min_text_chars: int = 80


class PolicyParser:
    def __init__(
        self,
        limits: ParserLimits | None = None,
        category_severity: dict[str, str] | None = None,
        default_category: str = "general",
    ):
        self.limits = limits or ParserLimits()
        self.category_severity = category_severity or {}
        self.default_category = default_category

    def severity_for(self, category: str, explicit: str | None = None) -> str:
        if explicit:
            return explicit
        return self.category_severity.get(category, "medium")

    def parse_pdf(
        self,
        data: bytes,
        *,
        doc_code: str,
        category: str | None = None,
        severity: str | None = None,
    ) -> ParsedDocument:
        if len(data) > self.limits.max_pdf_mb * 1024 * 1024:
            raise PolicyLintError(
                "FILE_TOO_LARGE", f"The PDF is larger than {self.limits.max_pdf_mb:g} MB."
            )
        category = category or self.default_category
        lines, pages = pdf_to_lines(
            data, max_pages=self.limits.max_pages, min_text_chars=self.limits.min_text_chars
        )
        return build_document(
            lines,
            doc_code=doc_code,
            category=category,
            default_severity=self.severity_for(category, severity),
            max_clauses=self.limits.max_clauses,
            page_count=pages,
        )

    def parse_markdown(
        self,
        data: bytes | str,
        *,
        doc_code: str,
        category: str | None = None,
        severity: str | None = None,
    ) -> ParsedDocument:
        if isinstance(data, bytes):
            if len(data) > self.limits.max_md_kb * 1024:
                raise PolicyLintError(
                    "FILE_TOO_LARGE", f"The file is larger than {self.limits.max_md_kb:g} KB."
                )
            try:
                text = data.decode("utf-8-sig")
            except UnicodeDecodeError as exc:
                raise PolicyLintError("BAD_ENCODING", "The file must be UTF-8 text.") from exc
        else:
            text = data
        meta, body = parse_front_matter(text)
        category = category or meta.get("category") or self.default_category
        sev = severity or meta.get("severity")
        if sev is not None and sev not in SEVERITIES:
            raise PolicyLintError("BAD_SEVERITY", f"Severity must be one of {SEVERITIES}.")
        # A stored doc_code always wins: replacements never change clause ids (FR20).
        return build_document(
            markdown_to_lines(body),
            doc_code=doc_code,
            category=category,
            default_severity=self.severity_for(category, sev),
            max_clauses=self.limits.max_clauses,
            strict_order=False,
        )

    def parse(self, filename: str, data: bytes, **kw) -> ParsedDocument:
        name = filename.lower()
        if name.endswith(".pdf"):
            return self.parse_pdf(data, **kw)
        if name.endswith((".md", ".markdown")):
            return self.parse_markdown(data, **kw)
        raise PolicyLintError("UNSUPPORTED_TYPE", "Only PDF and Markdown files are accepted.")
