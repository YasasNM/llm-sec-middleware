"""Placeholder tokens such as [PATIENT_NAME_1] that earlier masking left in the text.

A token is never patient data, so no detector may find anything inside one. Every detector
(regex, names, lexicon and re-identification) reads a copy of the text where each token is
replaced by spaces of the same length, so all positions stay the same. As a second guard,
any finding that still touches a token is dropped.
"""

import re

# A placeholder token: capital letters and underscores, then an underscore and a number
PLACEHOLDER = re.compile(r"\[[A-Z_]+_\d+\]")


# Start and end of every placeholder token in the text
def placeholder_spans(text: str) -> list[tuple[int, int]]:
    return [match.span() for match in PLACEHOLDER.finditer(text)]


# The text with each placeholder replaced by spaces of the same length (positions stay the same)
def blank_placeholders(text: str) -> str:
    return PLACEHOLDER.sub(lambda match: " " * len(match.group()), text)


# True when the finding shares a character with any of the token spans
def touches_a_token(finding, spans: list[tuple[int, int]]) -> bool:
    return any(finding.start < end and start < finding.end for start, end in spans)


# Keep only findings (entities or re-id items) that do not touch a placeholder token
def outside_placeholders(findings: list, text: str) -> list:
    spans = placeholder_spans(text)
    return [finding for finding in findings if not touches_a_token(finding, spans)]
