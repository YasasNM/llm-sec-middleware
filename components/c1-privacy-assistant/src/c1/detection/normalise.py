"""Undo common tricks that hide identifiers, while remembering where each character came from.

Example: "0 7 7 1 2 3 4 5 6 7" becomes "0771234567" and "nimal[at]gmail.com" becomes
"nimal@gmail.com". Detectors run on the cleaned text, then every span is mapped back to the
original text, so masking always replaces exactly what the clinician typed.
"""

import re
from dataclasses import dataclass

# A run of at least 4 single digits separated by single spaces, for example "0 7 7 1"
SPACED_DIGITS = re.compile(r"(?<!\d)\d(?: \d){3,}(?!\d)")

# Disguised symbols and what they really mean. Longer forms come first so they win.
REPLACEMENTS = {
    " [at] ": "@",
    " (at) ": "@",
    "[at]": "@",
    "(at)": "@",
    " [dot] ": ".",
    "[dot]": ".",
}


@dataclass
class NormalisedText:
    """Cleaned text plus, for each cleaned character, its start and end in the original text."""

    text: str
    starts: list[int]
    ends: list[int]


# Find the positions of the spaces inside spaced-out digit runs, so they can be skipped
def find_spaces_to_drop(text: str) -> set[int]:
    spaces = set()
    for match in SPACED_DIGITS.finditer(text):
        for index in range(match.start(), match.end()):
            if text[index] == " ":
                spaces.add(index)
    return spaces


# Return the disguised symbol that starts at this position, or None if there is none
def replacement_here(text: str, index: int) -> str | None:
    for disguised in REPLACEMENTS:
        if text[index : index + len(disguised)].lower() == disguised:
            return disguised
    return None


# Build the cleaned text and remember where every cleaned character came from
def normalise(text: str) -> NormalisedText:
    spaces_to_drop = find_spaces_to_drop(text)
    result = NormalisedText(text="", starts=[], ends=[])
    characters = []
    index = 0
    while index < len(text):
        disguised = replacement_here(text, index)
        if disguised:
            characters.append(REPLACEMENTS[disguised])
            result.starts.append(index)
            result.ends.append(index + len(disguised))
            index += len(disguised)
            continue
        if index not in spaces_to_drop:
            characters.append(text[index])
            result.starts.append(index)
            result.ends.append(index + 1)
        index += 1
    result.text = "".join(characters)
    return result


# Turn a (start, end) span in the cleaned text back into a span in the original text
def to_original_span(cleaned: NormalisedText, start: int, end: int) -> tuple[int, int]:
    return cleaned.starts[start], cleaned.ends[end - 1]
