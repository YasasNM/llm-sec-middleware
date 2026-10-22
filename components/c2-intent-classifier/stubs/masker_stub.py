"""Turn raw prompts into C1-style [TYPE_n] placeholders until real C1 is available."""

from __future__ import annotations

import re

PATTERNS = [
    (re.compile(r"\b\d{9,12}[VvXx]?\b"), "NIC"),
    (re.compile(r"\b0\d{9}\b"), "PHONE"),
    (re.compile(r"\b[\w.]+@[\w.]+\.[A-Za-z]{2,}\b"), "EMAIL"),
    (re.compile(r"\bPatient\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)\b"), "PATIENT_NAME"),
]


def mask(text: str) -> tuple[str, list[str]]:
    manifest: list[str] = []
    counts: dict[str, int] = {}

    def replace(match: re.Match[str], kind: str) -> str:
        counts[kind] = counts.get(kind, 0) + 1
        token = f"[{kind}_{counts[kind]}]"
        manifest.append(token)
        return token

    masked = text
    for pattern, kind in PATTERNS:
        masked = pattern.sub(lambda m, k=kind: replace(m, k), masked)
    return masked, manifest
