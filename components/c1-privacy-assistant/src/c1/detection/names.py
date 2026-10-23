"""Find patient names: the ML model (or the titled-name heuristic), then three safety nets.

0. Context cues: one or two words after "name is", "patient", "Mr", "son of" and similar
   cues become a PATIENT_NAME (confidence 0.7, source "context"), even in lowercase.
1. Lexicon fallback: any whole word from data/lexicons/names.json that the model missed
   ("linuka, nimal, kumari" typed in lowercase) becomes a PATIENT_NAME with confidence 0.6.
2. Repeated names: once a name is found, every other occurrence of the same text is masked too,
   so "Nimal ... nimal" gets the same token twice.

Honesty note: the lexicon holds the train, val AND test names of our corpus. So the lexicon step
must never run inside training/scripts/train_names.py or compare_name_models.py, and
evaluation/detector_by_type.py switches it off. Those scores measure the ML model only.
"""

import json
import re
from pathlib import Path

from c1.detection.entity import Entity
from c1.detection.ml_names import DEFAULT_THRESHOLD, detect_ml_names
from c1.detection.titled_names import detect_context_names, detect_titled_names

LEXICON_CONFIDENCE = 0.6
SHORTEST_LEXICON_WORD = 4
# A name right after one of these titles is a staff member, who stays visible
STAFF_TITLES = {"dr", "nurse", "sister", "consultant", "prof"}
# Lexicon names that are also ordinary English or medical words, so they are never matched alone
NOT_NAMES = {"carol", "hazel", "roger", "koch", "ganga", "mala", "deen", "will", "mark",
             "rose", "grace", "hope", "faith", "joy", "june", "april", "patience"}  # fmt: skip
# Labels of form fields; the model sometimes scores them as names ("NIC" after a placeholder)
FIELD_LABELS = {"nic", "mrn", "dob", "id", "bht", "phone", "tel", "mobile", "email", "ward", "bed"}
# A whole word made of letters (hyphens allowed), same idea as the ML tokenizer
WORD = re.compile(r"[A-Za-z]+(?:-[A-Za-z]+)*")


# Which name detector is active: "ml" or "heuristic" (shown later by /health)
def name_detector_label(model: dict | None) -> str:
    return "ml" if model is not None else "heuristic"


# Read every name word from the lexicon file (all groups), lowercased, minus the stop list
def load_name_lexicon(path: Path) -> frozenset[str]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    words = set()
    for group, lists in data.items():
        if group.startswith("_"):  # "_note" keys are comments, not names
            continue
        for names in lists.values():
            for name in names:
                words.update(word.lower() for word in name.split())
    return frozenset(w for w in words if len(w) >= SHORTEST_LEXICON_WORD and w not in NOT_NAMES)


# True when two words belong to one name: only spaces between, or the dot of "Dr." or "R."
def joined_by_space(gap: str, left_word: str) -> bool:
    if gap.strip() == "":
        return True
    is_short_form = len(left_word) == 1 or left_word.lower() in STAFF_TITLES
    return gap.strip() == "." and is_short_form


# True when the name at `position` follows a staff title, also inside a longer name:
# in "Dr. Sunil Fernando" both "Sunil" and "Fernando" are staff, so both stay visible
def after_staff_title(text: str, position: int, lexicon: frozenset[str]) -> bool:
    for word in reversed(list(WORD.finditer(text[:position]))):
        if not joined_by_space(text[word.end() : position], word.group()):
            return False  # a comma, a full stop or another sign ends the name
        lower = word.group().lower()
        if lower in STAFF_TITLES:
            return True
        if not (word.group()[0].isupper() or lower in lexicon):
            return False  # an ordinary word such as "saw" ends the name
        position = word.start()
    return False


# True when the span shares a character with any entity in the list
def overlaps_any(start: int, end: int, entities: list[Entity]) -> bool:
    return any(start < other.end and other.start < end for other in entities)


# True when this word may be masked: not a staff name and not already inside a finding
def free_name_spot(text: str, start: int, end: int, already: list, lexicon=frozenset()) -> bool:
    if overlaps_any(start, end, already):
        return False
    return not after_staff_title(text, start, lexicon)


# Lexicon words the model missed, each as a PATIENT_NAME entity with confidence 0.6
def find_lexicon_names(text: str, lexicon: frozenset[str], already: list[Entity]) -> list[Entity]:
    found = []
    for match in WORD.finditer(text):
        if match.group().lower() not in lexicon:
            continue
        if not free_name_spot(text, match.start(), match.end(), already, lexicon):
            continue
        start = match.start()
        if found and text[found[-1].end : start].strip(" ") == "":  # "nimal perera" is one name
            start = found.pop().start
        value = text[start : match.end()]
        found.append(
            Entity("PATIENT_NAME", start, match.end(), value, LEXICON_CONFIDENCE, "lexicon")
        )
    return found


# Drop model hits that are only form labels such as "NIC" or "MRN"; those are never names
def without_field_labels(names: list[Entity]) -> list[Entity]:
    return [n for n in names if not set(WORD.findall(n.value.lower())) <= FIELD_LABELS]


# Add names found after a context cue ("name is linuka"). A cue name is skipped where another
# finding already sits, unless it is longer and fully contains a name (then it replaces it).
def add_context_names(text: str, names: list[Entity], already: list[Entity]) -> list[Entity]:
    for cue_name in detect_context_names(text):
        start, end = cue_name.start, cue_name.end
        others = [n for n in names if not smaller_name_inside(n, start, end)]
        if not overlaps_any(start, end, already + others):
            names = others + [cue_name]
    return names


# Find patient names: model (or heuristic), then context-cue names, then lexicon words missed
def find_names(
    text: str,
    model: dict | None,
    threshold: float = DEFAULT_THRESHOLD,
    lexicon: frozenset[str] = frozenset(),
    already: list[Entity] | None = None,
) -> list[Entity]:
    if model is None:
        names = detect_titled_names(text)
    else:
        names = without_field_labels(detect_ml_names(text, model, threshold))
    names = add_context_names(text, names, already or [])
    taken = (already or []) + names
    return names + find_lexicon_names(text, lexicon, taken)


# True when `inner` is a smaller name lying completely inside the span start..end
def smaller_name_inside(inner: Entity, start: int, end: int) -> bool:
    inside = start <= inner.start and inner.end <= end
    return inner.type == "PATIENT_NAME" and inside and (inner.start, inner.end) != (start, end)


# Every other whole-word copy (ignoring case) of a found name; it may swallow smaller names
def find_repeats(text: str, name: Entity, taken: list[Entity]) -> list[Entity]:
    pattern = re.compile(rf"(?<![A-Za-z]){re.escape(name.value)}(?![A-Za-z])", re.IGNORECASE)
    repeats = []
    for match in pattern.finditer(text):
        start, end = match.span()
        blockers = [e for e in taken + repeats if not smaller_name_inside(e, start, end)]
        if start != name.start and free_name_spot(text, start, end, blockers):
            repeats.append(Entity(name.type, start, end, match.group(), name.confidence, "repeat"))
    return repeats


# Add repeated copies of every found name (longest first), so each copy gets the same token
def add_repeated_names(text: str, entities: list[Entity]) -> list[Entity]:
    result = list(entities)
    names = [e for e in entities if e.type == "PATIENT_NAME"]
    for name in sorted(names, key=lambda e: e.start - e.end):
        if name not in result:
            continue  # already swallowed by a longer repeat
        repeats = find_repeats(text, name, result)
        swallowed = [
            e for e in result if any(smaller_name_inside(e, r.start, r.end) for r in repeats)
        ]
        result = [e for e in result if e not in swallowed] + repeats
    return sorted(result, key=lambda entity: (entity.start, entity.end))
