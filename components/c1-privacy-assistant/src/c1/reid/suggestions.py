"""Turn re-identification findings into a safer prompt (part of M5).

The clinician decides whether to use it (K2). Each suggestion becomes an Edit (start, end,
replacement) that is tidied so the result reads naturally:
- "<age> <condition> patient" becomes one phrase:
  "94-year-old HIV patient" -> "elderly patient with a chronic condition";
- "<condition> patient" becomes "patient with a chronic condition";
- "<town> ward 3" becomes "the ward in <district> district";
- a word repeated at the edge of an edit is not doubled ("the the ward", "district district");
- "a" or "an" in front of the new words is corrected ("a 94-year-old" -> "an elderly");
- at the start of a sentence the new words start with a capital ("94-year-old ..." -> "Elderly").
Applying the suggestions to an already generalised text changes nothing.
"""

import re
from dataclasses import dataclass

from c1.reid.finders import QuasiIdentifier

# " patient" right after a finding (any case), so "<condition> patient" can be rephrased
PATIENT_AFTER = re.compile(r"\s+patients?\b", re.IGNORECASE)
LAST_WORD = re.compile(r"([A-Za-z]+)\s*$")
FIRST_WORD = re.compile(r"^\s*([A-Za-z]+)")
# A district written just before a ward: "Galle district ward 3", "Nuwara Eliya District bed 2"
DISTRICT_BEFORE = re.compile(r"((?:[A-Z][A-Za-z-]*\s+)+(?i:district))\s+$")


@dataclass(frozen=True)
class Edit:
    """Replace text[start:end] with `replacement`."""

    start: int
    end: int
    replacement: str


# End of " patient" right after position `end`, or None when it does not follow
def patient_after(text: str, end: int) -> int | None:
    match = PATIENT_AFTER.match(text, end)
    return match.end() if match else None


# True when only spaces sit between two findings ("94-year-old HIV")
def side_by_side(text: str, first: QuasiIdentifier, second: QuasiIdentifier) -> bool:
    return text[first.end : second.start].strip() == ""


# One raw edit per finding, merging "<age> <condition> patient" into a single natural phrase
def phrase_edits(text: str, found: list[QuasiIdentifier]) -> list[Edit]:
    items = sorted(found, key=lambda item: item.start)
    edits, index = [], 0
    while index < len(items):
        item = items[index]
        after = items[index + 1] if index + 1 < len(items) else None
        merged = age_condition_patient(text, item, after) or town_ward(text, item, after)
        if merged:
            edits.append(merged)
            index += 2
            continue
        edits.append(single_edit(text, item))
        index += 1
    return edits


# "94-year-old HIV patient" -> "elderly patient with a chronic condition", or None
def age_condition_patient(text: str, age, condition) -> Edit | None:
    if age.type != "AGE" or condition is None or condition.type != "CONDITION":
        return None
    end = patient_after(text, condition.end)
    if end is None or not side_by_side(text, age, condition):
        return None
    return Edit(age.start, end, f"{age.suggestion} patient with {condition.suggestion}")


# "Galle ward 3" -> "the ward in Galle district", or None
def town_ward(text: str, town, ward) -> Edit | None:
    if town.type != "LOCATION" or ward is None or ward.type != "WARD":
        return None
    if not side_by_side(text, town, ward):
        return None
    return Edit(town.start, ward.end, f"{ward.suggestion} in {town.suggestion}")


# The edit for one finding; "<condition> patient" reads as "patient with <condition>", and
# "Galle district ward 3" reads as "the ward in Galle district"
def single_edit(text: str, item: QuasiIdentifier) -> Edit:
    end = patient_after(text, item.end) if item.type == "CONDITION" else None
    if end is not None:
        return Edit(item.start, end, f"patient with {item.suggestion}")
    district = DISTRICT_BEFORE.search(text[: item.start]) if item.type == "WARD" else None
    if district:
        return Edit(district.start(), item.end, f"{item.suggestion} in {district.group(1)}")
    return Edit(item.start, item.end, item.suggestion)


# Drop the first word of the replacement when the text just before already ends with it
def without_repeat_before(text: str, edit: Edit) -> Edit:
    before = LAST_WORD.search(text[: edit.start])
    first = FIRST_WORD.match(edit.replacement)
    if before and first and before.group(1).lower() == first.group(1).lower():
        return Edit(edit.start, edit.end, edit.replacement[first.end() :].lstrip())
    return edit


# Drop the last word of the replacement when the text just after already starts with it
def without_repeat_after(text: str, edit: Edit) -> Edit:
    after = FIRST_WORD.match(text[edit.end :])
    last = LAST_WORD.search(edit.replacement)
    if after and last and after.group(1).lower() == last.group(1).lower():
        return Edit(edit.start, edit.end, edit.replacement[: last.start()].rstrip())
    return edit


# Fix "a"/"an" just before the edit: "a 94-year-old" -> "an elderly", "an ward" -> "a ward"
def with_right_article(text: str, edit: Edit) -> Edit:
    article = re.search(r"\b(an?)\s+$", text[: edit.start], re.IGNORECASE)
    if not article or not edit.replacement:
        return edit
    wanted = "an" if edit.replacement[0].lower() in "aeiou" else "a"
    if article.group(1).lower() == wanted:
        return edit
    wanted = wanted.capitalize() if article.group(1)[0].isupper() else wanted
    return Edit(article.start(), edit.end, f"{wanted} {edit.replacement}")


# Start the replacement with a capital letter when it starts a sentence ("? The ward")
def with_sentence_case(text: str, edit: Edit) -> Edit:
    before = text[: edit.start].rstrip()
    starts_sentence = before == "" or before[-1] in ".?!"
    if starts_sentence and edit.replacement[:1].islower():
        return Edit(edit.start, edit.end, edit.replacement[0].upper() + edit.replacement[1:])
    return edit


# Tidy one edit: no doubled words at its edges, the right article, a capital at a sentence start
def tidy(text: str, edit: Edit) -> Edit:
    edit = without_repeat_after(text, without_repeat_before(text, edit))
    return with_right_article(text, with_sentence_case(text, edit))


# The tidied edits for all findings, in text order (also used by the demo, one finding at a time)
def suggestion_edits(text: str, found: list[QuasiIdentifier]) -> list[Edit]:
    return [tidy(text, edit) for edit in phrase_edits(text, found)]


# Apply edits from the end of the text backwards, so earlier positions stay right
def apply_edits(text: str, edits: list[Edit]) -> str:
    for edit in sorted(edits, key=lambda edit: edit.start, reverse=True):
        text = text[: edit.start] + edit.replacement + text[edit.end :]
    return text


# Rebuild the text with every suggestion applied, reading naturally
def apply_suggestions(text: str, found: list[QuasiIdentifier]) -> str:
    return apply_edits(text, suggestion_edits(text, found))
