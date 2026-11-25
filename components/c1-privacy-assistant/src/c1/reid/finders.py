"""Find quasi-identifiers: age, condition, ward and location (part of M5).

A quasi-identifier is a detail that does not name the patient on its own, but can when combined
with others ("94-year-old" + "HIV" + "ward 3" + "Galle"). Each finding gets a rarity
(high, medium or low) and a suggested general wording. All lists come from configs/reid_lists.yaml.
"""

import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class QuasiIdentifier:
    """One risky detail. `match` is hidden from repr() so it never lands in a log."""

    type: str
    start: int
    end: int
    match: str = field(repr=False)
    rarity: str
    suggestion: str


# Number + unit, optionally followed by "old": "94-year-old", "45 yrs", "45y", "9 months old"
AGE_WITH_UNIT = re.compile(
    r"\b(?P<number>\d{1,3})\s*-?\s*(?P<unit>years?|yrs?|y/o|yo|y|months?|weeks?|days?)"
    r"(?P<old>\s*-?\s*old)?\b",
    re.IGNORECASE,
)
# An age cue just before the number: "Age: 53 years", "aged 94"
AGE_CUE = re.compile(r"\b(?:age|aged)\s*[:\-]?\s*$", re.IGNORECASE)
# Words that show the number is a duration, not an age: "for 3 years", "since 2 days"
DURATION_WORDS = {"for", "since", "past", "last", "over", "within", "after", "in", "x"}
# Short age forms that are rarely used for durations
SHORT_AGE_UNITS = {"yrs", "yr", "y/o", "yo", "y"}

WARD = re.compile(
    r"\b(?:ward|wd|bed|room|cubicle|unit)\.?\s*(?:no\.?\s*)?\d{1,3}[A-Z]?\b", re.IGNORECASE
)


# Convert a number and unit into an age in years ("9 months" is 0.75 years)
def age_in_years(number: int, unit: str) -> float:
    unit = unit.lower()
    if unit.startswith("month"):
        return number / 12
    if unit.startswith("week"):
        return number / 52
    if unit.startswith("day"):
        return number / 365
    return float(number)


# The word just before a position, lowercased ("" at the start of the text)
def previous_word(text: str, position: int) -> str:
    words = text[:position].split()
    return words[-1].lower().strip(",.;:(") if words else ""


# Decide whether a number-with-unit match is an age rather than a duration
def looks_like_age(text: str, match: re.Match) -> bool:
    if match.group("old") or AGE_CUE.search(text[: match.start()]):
        return True
    if previous_word(text, match.start()) in DURATION_WORDS:
        return False
    return match.group("unit").lower() in SHORT_AGE_UNITS


# Rarity of an age: extreme ages (very young or very old) are high, the rest low
def age_rarity(years: float, age_rules: dict) -> str:
    if years < age_rules["young_below"] or years > age_rules["old_above"]:
        return "high"
    return "low"


# General wording for an age, from the bands in the config ("94" -> "elderly")
def age_suggestion(years: float, age_rules: dict) -> str:
    for band in age_rules["bands"]:
        if years < band["below"]:
            return band["suggestion"]
    return age_rules["bands"][-1]["suggestion"]


# Find every age in the text
def find_ages(text: str, lists: dict) -> list[QuasiIdentifier]:
    found = []
    for match in AGE_WITH_UNIT.finditer(text):
        if not looks_like_age(text, match):
            continue
        years = age_in_years(int(match.group("number")), match.group("unit"))
        rarity = age_rarity(years, lists["age"])
        suggestion = age_suggestion(years, lists["age"])
        found.append(QuasiIdentifier("AGE", *match.span(), match.group(), rarity, suggestion))
    return found


# Build a pattern for one condition: "Down syndrome" also matches "Down's syndrome", and a
# one-word entry such as "Parkinson" also matches "Parkinson's disease"
def condition_pattern(name: str) -> re.Pattern:
    flags = 0 if name.isupper() else re.IGNORECASE
    first_word, _, rest = name.partition(" ")
    possessive = r"(?:['’]?s)?"
    if rest:
        pattern = rf"\b{re.escape(first_word)}{possessive}\s+{re.escape(rest)}\b"
    else:
        pattern = rf"\b{re.escape(first_word)}{possessive}(?:\s+(?:disease|syndrome))?\b"
    return re.compile(pattern, flags)


# Find conditions from the high and medium rarity lists
def find_conditions(text: str, lists: dict) -> list[QuasiIdentifier]:
    found = []
    for rarity, group in lists["conditions"].items():
        for name in group["names"]:
            for match in condition_pattern(name).finditer(text):
                found.append(
                    QuasiIdentifier(
                        "CONDITION", *match.span(), match.group(), rarity, group["suggestion"]
                    )
                )
    return found


# Find ward, bed, room or unit numbers
def find_wards(text: str, lists: dict) -> list[QuasiIdentifier]:
    suggestion = lists["ward"]["suggestion"]
    return [
        QuasiIdentifier("WARD", *match.span(), match.group(), "medium", suggestion)
        for match in WARD.finditer(text)
    ]


# One pattern for all town names, longest first so "Colombo 05" wins over "Colombo"
def towns_pattern(towns: dict) -> re.Pattern:
    names = sorted(towns, key=len, reverse=True)
    return re.compile(r"\b(?:" + "|".join(re.escape(name) for name in names) + r")\b")


# Where a town mention ends: include ", District" when the town's district follows it
def location_end(text: str, end: int, district: str) -> int:
    follow = f", {district}"
    if text.startswith(follow, end):
        return end + len(follow)
    return end


# True when the place is already general: "<town> district" or "<province> province" (any case).
# Such a place is not a clue, so it is never suggested again ("Galle district district").
def already_general(text: str, end: int) -> bool:
    following = text[end : end + 10].lower()
    return following.startswith(" district") or following.startswith(" province")


# Find towns: big cities are low rarity, other towns medium; suggest the district instead
def find_locations(text: str, lists: dict) -> list[QuasiIdentifier]:
    towns = lists["location"]["towns"]
    big_cities = set(lists["location"]["big_cities"])
    found = []
    for match in towns_pattern(towns).finditer(text):
        if already_general(text, match.end()):
            continue
        town, district = match.group(), towns[match.group()]
        end = location_end(text, match.end(), district) if town != district else match.end()
        rarity = "low" if town.split()[0] in big_cities else "medium"
        span_text = text[match.start() : end]
        suggestion = f"{district} district"
        found.append(QuasiIdentifier("LOCATION", match.start(), end, span_text, rarity, suggestion))
    # A district already swallowed by "Town, District" must not be reported again
    return without_overlaps(found)


# Keep findings in text order and drop any that overlap one already kept
def without_overlaps(found: list[QuasiIdentifier]) -> list[QuasiIdentifier]:
    kept = []
    last_end = 0
    for item in sorted(found, key=lambda item: (item.start, -item.end)):
        if item.start >= last_end:
            kept.append(item)
            last_end = item.end
    return kept


# Find every quasi-identifier in the text, in reading order
def find_quasi_identifiers(text: str, lists: dict) -> list[QuasiIdentifier]:
    found = find_ages(text, lists) + find_conditions(text, lists)
    found += find_wards(text, lists) + find_locations(text, lists)
    return without_overlaps(found)
