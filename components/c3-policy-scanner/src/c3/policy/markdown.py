"""Markdown policy files to lines, plus optional front matter."""
from __future__ import annotations

import re

import yaml

from .models import SEVERITIES, PolicyLintError

HEADING_MARK = re.compile(r"^\s{0,3}#{1,6}\s+")
FRONT_MATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.S)


def parse_front_matter(text: str) -> tuple[dict, str]:
    m = FRONT_MATTER.match(text)
    if not m:
        return {}, text
    try:
        meta = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError as exc:
        raise PolicyLintError("BAD_FRONT_MATTER", "The front matter is not valid YAML.") from exc
    if not isinstance(meta, dict):
        raise PolicyLintError("BAD_FRONT_MATTER", "The front matter must be a mapping.")
    sev = meta.get("severity")
    if sev is not None and sev not in SEVERITIES:
        raise PolicyLintError("BAD_SEVERITY", f"Severity must be one of {SEVERITIES}.")
    return meta, text[m.end():]


def markdown_to_lines(text: str) -> list[tuple[str, int | None]]:
    """Drop the '#' marks so that '## 3.1 Title' becomes the heading '3.1 Title'."""
    out: list[tuple[str, int | None]] = []
    in_fence = False
    for raw in text.splitlines():
        if raw.strip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            out.append((raw, None))
            continue
        is_heading = bool(HEADING_MARK.match(raw))
        line = HEADING_MARK.sub("", raw)
        if not is_heading:
            line = re.sub(r"^\s*[-*+]\s+", "", line)   # bullet marks
        line = line.replace("**", "").replace("__", "").replace("`", "")
        out.append((line, None))
    return out
