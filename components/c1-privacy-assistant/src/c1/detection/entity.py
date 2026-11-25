"""The Entity record that every C1 detector returns."""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Entity:
    """One detected identifier and where it sits in the original text.

    `value` is hidden from repr(), so printing or logging an Entity never shows patient data.
    """

    type: str
    start: int
    end: int
    value: str = field(repr=False)
    confidence: float
    source: str
