"""M4 titled-name heuristic: the fallback name finder when the ML model is not available.

It looks for a patient title or label followed by one to four capitalised words:
"Patient Nimal Perera", "Mrs Kumari Silva", "Master Kasun", "Name: Supun Jayatilleke".
Doctor and staff titles (Dr, Prof, Nurse) are NOT included: staff names stay visible in C1.
It needs no model, so C1 keeps masking obvious names even if the model file is missing.

detect_context_names() (bottom of this file) is a separate safety net that runs always, also
next to the model: it takes one or two words after a clear cue such as "name is" or "patient",
even in lowercase. detect_titled_names() itself is unchanged, so the heuristic baseline in
training/scripts/train_names.py still measures the same thing.
"""

import re

from c1.detection.entity import Entity

HEURISTIC_CONFIDENCE = 0.7

# Patient titles and labels, any case. The name after them must start with a capital letter.
TITLE = r"(?i:\b(?:patient|pt\.?|mr\.?|mrs\.?|ms\.?|miss|master|name\s*:))"
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


# --- Context cues: names typed in lowercase after a clear cue ("my patient name is linuka") ---

CONTEXT_CONFIDENCE = 0.7
# Cues before a patient name, as lowercase word lists ("name is" is two words)
CONTEXT_CUES = [["name", "is"], ["named"], ["patient"], ["pt"], ["mr"], ["mrs"], ["miss"], ["ms"],
                ["son", "of"], ["daughter", "of"], ["wife", "of"], ["husband", "of"]]  # fmt: skip
# Staff titles: a word after these is never taken, so staff names stay visible
STAFF_WORDS = {"dr", "doctor", "nurse", "sister", "prof", "professor", "consultant"}
# Ordinary English and medical words that often follow a cue but are never names
CONTEXT_STOP = STAFF_WORDS | {
    "a", "an", "the", "is", "was", "are", "were", "be", "been", "being", "has", "had", "have",
    "with", "without", "who", "whose", "which", "that", "this", "and", "or", "but", "not", "no",
    "of", "in", "on", "at", "to", "for", "from", "by", "as", "if", "so", "also", "again", "still",
    "my", "our", "his", "her", "their", "your", "he", "she", "they", "it", "we", "i", "you",
    "name", "named", "names", "patient", "patients", "pt", "mr", "mrs", "miss", "ms", "son",
    "daughter", "wife", "husband", "mother", "father", "baby", "child", "boy", "girl", "man",
    "woman", "lady", "male", "female", "aged", "age", "year", "years", "old", "here", "there",
    "today", "yesterday", "now", "then", "came", "comes", "presented", "presents", "presenting",
    "admitted", "discharged", "reviewed", "seen", "denies", "reports", "reported", "complains",
    "complained", "says", "said", "states", "feels", "felt", "needs", "needed", "requires",
    "takes", "taking", "took", "given", "received", "developed", "underwent", "should", "will",
    "can", "could", "may", "might", "must", "would", "did", "does", "do", "got", "gets", "went",
    "history", "details", "record", "notes", "summary", "fever", "cough", "pain", "chest",
    "headache", "vomiting", "diarrhoea", "diabetes", "hypertension", "asthma", "infection",
    "ward", "bed", "unit", "room", "clinic", "hospital", "number", "id", "nic", "mrn", "dob",
    "phone", "email", "address", "report", "results", "test", "tests", "bp", "dose", "drug",
    "medication", "medicine", "tablets", "surgery", "operation", "case", "file", "care",
}  # fmt: skip
# A whole word made only of letters: "P" in the id "P9908" is not one (digits are attached)
LETTERS = re.compile(r"(?<![A-Za-z0-9_])[A-Za-z]+(?![A-Za-z0-9_])")
# A placeholder token such as [PATIENT_NAME_1]
TOKEN = re.compile(r"\[[A-Z_]+_\d+\]")


# True when a candidate may be a name: not a stop word, not an "-ing" verb such as
# "weighing", not a single letter, and not inside a token
def can_be_name(text: str, match: re.Match, tokens: list[tuple[int, int]]) -> bool:
    word = match.group().lower()
    inside_token = any(start <= match.start() < end for start, end in tokens)
    ordinary = word in CONTEXT_STOP or word.endswith("ing") or len(word) < 2
    return not ordinary and not inside_token


# True when the cue words sit exactly at words[index:...], joined only by spaces
def cue_at(text: str, words: list[re.Match], index: int, cue: list[str]) -> bool:
    found = words[index : index + len(cue)]
    if [word.group().lower() for word in found] != cue:
        return False
    return all(
        text[a.end() : b.start()].strip() == "" for a, b in zip(found, found[1:], strict=False)
    )


# The one or two name words right after a cue that ends at words[index - 1]
def name_after_cue(text: str, words: list[re.Match], index: int, tokens: list) -> list[re.Match]:
    taken = []
    for word in words[index : index + 2]:
        before = words[index - 1] if not taken else taken[-1]
        gap = text[before.end() : word.start()]
        # Only spaces may separate; after the cue a dot or colon is fine too ("Mr. linuka")
        if gap.strip(" " if taken else " .:") != "" or not can_be_name(text, word, tokens):
            break
        taken.append(word)
    return taken


# Find lowercase or capitalised names right after a context cue, as PATIENT_NAME entities
def detect_context_names(text: str) -> list[Entity]:
    words = list(LETTERS.finditer(text))
    tokens = [match.span() for match in TOKEN.finditer(text)]
    found = []
    for index in range(len(words)):
        for cue in CONTEXT_CUES:
            if not cue_at(text, words, index, cue):
                continue
            name = name_after_cue(text, words, index + len(cue), tokens)
            if name:
                start, end = name[0].start(), name[-1].end()
                found.append(Entity("PATIENT_NAME", start, end, text[start:end],
                                    CONTEXT_CONFIDENCE, "context"))  # fmt: skip
    return found
