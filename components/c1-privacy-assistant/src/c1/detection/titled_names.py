"""M4 titled-name heuristic: the fallback name finder when the ML model is not available.

It looks for a title or label followed by one to four capitalised words:
"Patient Nimal Perera", "Mrs Kumari Silva", "Dr. Fernando", "Name: Supun Jayatilleke".
It needs no model, so C1 keeps masking obvious names even if the model file is missing.
"""

import re

from c1.detection.entity import Entity

HEURISTIC_CONFIDENCE = 0.7

# Titles and labels, any case. The name after them must start with a capital letter.
TITLE = r"(?i:\b(?:patient|pt\.?|mr\.?|mrs\.?|ms\.?|miss|master|dr\.?|name\s*:))"
CAPITAL_WORD = r"[A-Z][a-z]+(?:-[A-Z][a-z]+)?"
TITLED_NAME = re.compile(rf"{TITLE}\s+(?P<value>{CAPITAL_WORD}(?:\s+{CAPITAL_WORD}){{0,3}})")

# Capitalised words that often follow a title but are not names ("Patient History")
NOT_NAMES = {
    "Name", "Names", "With", "Has", "Had", "Is", "Was", "Who", "History", "Details",
    "Record", "Records", "Notes", "Note", "Summary", "Admitted", "Presented", "In",
}  # fmt: skip


# Cut the name at the first word that is clearly not part of a name
def trim_not_names(words: list[str]) -> list[str]:
    kept = []
    for word in words:
        if word in NOT_NAMES:
            break
        kept.append(word)
    return kept


# Find where the kept words end, walking through the matched text word by word
def end_of_words(text: str, start: int, words: list[str]) -> int:
    position = start
    for word in words:
        position = text.index(word, position) + len(word)
    return position


# Find names that follow a title such as "Patient", "Mr" or "Dr"
def detect_titled_names(text: str) -> list[Entity]:
    entities = []
    for match in TITLED_NAME.finditer(text):
        words = trim_not_names(match.group("value").split())
        if not words:
            continue
        start = match.start("value")
        end = end_of_words(text, start, words)
        entities.append(
            Entity("PATIENT_NAME", start, end, text[start:end], HEURISTIC_CONFIDENCE, "heuristic")
        )
    return entities
