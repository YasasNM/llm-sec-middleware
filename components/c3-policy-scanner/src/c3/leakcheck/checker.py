"""Leak re-check (FR7, FR27, blueprint 12.7).

Regex layer for local identifier formats plus a placeholder rule. Returns entity types and
offsets only, never the values, so results are safe to log.

  * NIC old format (9 digits then V or X) and new format (12 digits starting 19 or 20)
  * Sri Lankan phone numbers (+94 / 0094 / 0 prefix)
  * Email addresses
  * Placeholder tokens such as [PATIENT_NAME_1] that are NOT in the manifest
  * Not leaks: manifest tokens, P0012 (patient) and D003 (doctor) pseudonyms (IR12), doses,
    ward numbers and other digit strings that are not identifiers.

Presidio (names, locations, dates) can be added through `extra_detectors`; it is optional
because its language models are large.
"""
from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

_D = r"(?<![\w.])"           # not preceded by a word character or dot
_E = r"(?![\w])"

NIC_OLD = re.compile(rf"{_D}\d{{9}}[VvXx]{_E}")
NIC_NEW = re.compile(rf"{_D}(?:19|20)\d{{10}}{_E}")
PHONE = re.compile(
    rf"(?<![\w+])(?:\+94|0094|0)[ -]?(?:7\d|[1-9]\d)[ -]?\d{{3}}[ -]?\d{{4}}{_E}"
)
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PLACEHOLDER = re.compile(r"\[[A-Z][A-Z_]*_\d+\]")
INTERNAL_ID = re.compile(r"\b(?:P\d{4}|D\d{3})\b")        # allowed pseudonyms (IR12)

Detector = Callable[[str], Iterable[tuple[str, int, int]]]


@dataclass(frozen=True)
class Entity:
    type: str
    start: int
    end: int

    def to_json(self) -> dict:
        return {"type": self.type, "start": self.start, "end": self.end}


@dataclass
class LeakResult:
    entities: list[Entity] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.entities)

    @property
    def detected(self) -> bool:
        return bool(self.entities)

    def to_json(self) -> dict:
        return {"status": "leak" if self.detected else "clean",
                "entities": [e.to_json() for e in self.entities]}


class LeakChecker:
    def __init__(self, extra_detectors: Iterable[Detector] = ()):
        self.extra = list(extra_detectors)

    def check(self, text: str, manifest: Iterable[str] = ()) -> LeakResult:
        allowed = set(manifest)
        found: list[Entity] = []
        for kind, rx in (("NIC", NIC_OLD), ("NIC", NIC_NEW), ("PHONE", PHONE), ("EMAIL", EMAIL)):
            for m in rx.finditer(text):
                found.append(Entity(kind, m.start(), m.end()))
        for m in PLACEHOLDER.finditer(text):
            if m.group(0) not in allowed:
                found.append(Entity("UNKNOWN_PLACEHOLDER", m.start(), m.end()))
        for det in self.extra:
            for kind, s, e in det(text):
                if text[s:e] not in allowed and not INTERNAL_ID.fullmatch(text[s:e]):
                    found.append(Entity(kind, s, e))
        return LeakResult(_merge(found))

    def check_generated(self, parts: Iterable[str | None], manifest: Iterable[str] = ()) -> bool:
        """True if the generated quote or reason contains an identifier (FR27)."""
        return any(self.check(p, manifest).detected for p in parts if p)


def _merge(entities: list[Entity]) -> list[Entity]:
    """Drop entities that sit inside a longer one, so a 12-digit NIC is not also a phone."""
    entities = sorted(entities, key=lambda e: (e.start, -(e.end - e.start)))
    out: list[Entity] = []
    for e in entities:
        if out and e.start < out[-1].end:
            continue
        out.append(e)
    return out


def normalise_text(text: str) -> str:
    """Remove control characters (keep newline and tab) before scanning."""
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
