"""M2 regex detector: finds NIC, PHONE, MRN, EMAIL and DOB in a prompt.

Patterns follow BLUEPRINT section 12.1 (Sri Lankan formats). Each pattern has a group called
"value": only that part is reported, so labels such as "MRN:" or "DOB" stay readable after masking.
Internal ids such as P0012 (patient) and D003 (doctor) are never reported (rule IR12).
"""

import re

from c1.detection.entity import Entity
from c1.detection.normalise import NormalisedText, normalise, to_original_span

REGEX_CONFIDENCE = 0.99

# Sri Lankan phone: 0771234567, 077 123 4567, 077-123-4567, +94771234567
PHONE = r"(?<![\d+])(?P<value>(?:\+94|0)[\s\-]?\d{2}[\s\-]?\d{3}[\s\-]?\d{4})(?!\d)"

# Sri Lankan NIC: old format 9 digits + V or X (951234567V), new format 12 digits
NIC = r"(?<!\w)(?P<value>\d{9}[VX]|\d{12})(?!\w)"

# Medical record number written after the word MRN: "MRN 123456", "MRN: 123456"
MRN = r"\bMRN\s*[:#]?\s*(?P<value>\d{4,10})\b"

# Standard email address
EMAIL = r"(?P<value>[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,})"

# Date shapes used for DOB: 14/03/1985, 1985-03-14, 14 March 1985, March 14, 1985
MONTH = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?"
ISO_DATE = r"\d{4}-\d{2}-\d{2}"
NUMERIC_DATE = r"\d{1,2}[/.\-]\d{1,2}[/.\-](?:\d{4}|\d{2})"
DAY_MONTH_YEAR = rf"\d{{1,2}}(?:st|nd|rd|th)?\s+{MONTH},?\s+\d{{4}}"
MONTH_DAY_YEAR = rf"{MONTH}\s+\d{{1,2}}(?:st|nd|rd|th)?,?\s+\d{{4}}"
ANY_DATE = rf"(?:{ISO_DATE}|{NUMERIC_DATE}|{DAY_MONTH_YEAR}|{MONTH_DAY_YEAR})"

# Date of birth, only when a label says so. A plain date ("review on 14/03/2026") is not a DOB.
DOB_LABEL = r"(?:\bDOB\b|\bD\.O\.B\.?|\bdate of birth\b|\bborn(?:\s+on)?\b)"
DOB = rf"{DOB_LABEL}\s*[:\-]?\s*(?P<value>{ANY_DATE})"

# All patterns, by entity type. IGNORECASE lets "mrn", "dob" and "951234567v" match too.
PATTERNS = {
    "NIC": re.compile(NIC, re.IGNORECASE),
    "PHONE": re.compile(PHONE, re.IGNORECASE),
    "MRN": re.compile(MRN, re.IGNORECASE),
    "EMAIL": re.compile(EMAIL, re.IGNORECASE),
    "DOB": re.compile(DOB, re.IGNORECASE),
}

# Internal patient ids (P0001 to P9999) and doctor ids (D001 to D999). Never PHI.
INTERNAL_ID = re.compile(r"\b(?:P\d{4}|D\d{3})\b")


# Run one pattern on the cleaned text and return its matches as spans in the original text
def find_entities_of_type(
    entity_type: str, pattern: re.Pattern, original: str, cleaned: NormalisedText
) -> list[Entity]:
    found = []
    for match in pattern.finditer(cleaned.text):
        start, end = to_original_span(cleaned, match.start("value"), match.end("value"))
        value = original[start:end]
        found.append(Entity(entity_type, start, end, value, REGEX_CONFIDENCE, "regex"))
    return found


# True when two spans share at least one character
def spans_overlap(first: tuple[int, int], second: tuple[int, int]) -> bool:
    return first[0] < second[1] and second[0] < first[1]


# Remove any entity that touches an internal id such as P0012 or D003
def drop_internal_ids(text: str, entities: list[Entity]) -> list[Entity]:
    id_spans = [match.span() for match in INTERNAL_ID.finditer(text)]
    kept = []
    for entity in entities:
        span = (entity.start, entity.end)
        if not any(spans_overlap(span, id_span) for id_span in id_spans):
            kept.append(entity)
    return kept


# Find every structured identifier in the text, sorted by where it starts
def detect_regex(text: str) -> list[Entity]:
    cleaned = normalise(text)
    entities = []
    for entity_type, pattern in PATTERNS.items():
        entities.extend(find_entities_of_type(entity_type, pattern, text, cleaned))
    entities = drop_internal_ids(text, entities)
    return sorted(entities, key=lambda entity: (entity.start, entity.end))
