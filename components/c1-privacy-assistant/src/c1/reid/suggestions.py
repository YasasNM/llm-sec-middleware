"""Turn re-identification findings into a safer prompt (part of M5).

The clinician decides whether to use it (K2). Wording is rule based, so grammar can be awkward
("a elderly man"); BLUEPRINT 12.5 lists this as a known limitation.
"""

from c1.reid.finders import QuasiIdentifier


# Rebuild the text left to right, putting each suggestion where its finding was
def apply_suggestions(text: str, found: list[QuasiIdentifier]) -> str:
    pieces = []
    position = 0
    for item in sorted(found, key=lambda item: item.start):
        pieces.append(text[position : item.start])
        pieces.append(item.suggestion)
        position = item.end
    pieces.append(text[position:])
    return "".join(pieces)
