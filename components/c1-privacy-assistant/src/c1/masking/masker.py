"""M8 masker: replaces explicit identifiers with [TYPE_n] placeholders (K1, K2, K3).

- Numbering is per request and per type: [PHONE_1], [PHONE_2], [NIC_1].
- The same value gets the same token, so "Nimal Perera ... Nimal Perera" -> [PATIENT_NAME_1] twice.
- The manifest lists tokens only, never values.
- It is one-way: the value-to-token mapping lives only inside this function call and is never
  stored or returned, so nothing can unmask the text later.
- masking_percentage = round(100 * masked characters / prompt length, 1) (IR7). Only explicit
  identifiers count. Re-identification rewrites (Step 4) do not.
"""

from dataclasses import dataclass, field

from c1.detection.entity import Entity


@dataclass
class MaskResult:
    """What the masker returns. It holds tokens and counts only, never the original values."""

    masked_text: str
    placeholder_manifest: list[str] = field(default_factory=list)
    masking_percentage: float = 0.0
    entity_counts: dict[str, int] = field(default_factory=dict)


# Make a comparison key so small spelling differences count as the same value
def same_value_key(entity: Entity) -> tuple[str, str]:
    simple = entity.value.lower().replace(" ", "").replace("-", "")
    return entity.type, simple


# Sort entities by position and skip any that overlap one already kept
def non_overlapping(entities: list[Entity]) -> list[Entity]:
    kept = []
    last_end = 0
    for entity in sorted(entities, key=lambda item: (item.start, -item.end)):
        if entity.start >= last_end and entity.end > entity.start:
            kept.append(entity)
            last_end = entity.end
    return kept


# Give every distinct value a token like [PHONE_1], numbering each type separately
def assign_tokens(entities: list[Entity]) -> dict[tuple[str, str], str]:
    tokens = {}
    next_number = {}
    for entity in entities:
        key = same_value_key(entity)
        if key not in tokens:
            next_number[entity.type] = next_number.get(entity.type, 0) + 1
            tokens[key] = f"[{entity.type}_{next_number[entity.type]}]"
    return tokens


# Rebuild the text left to right, putting a token where each entity was
def replace_spans(text: str, entities: list[Entity], tokens: dict) -> str:
    pieces = []
    position = 0
    for entity in entities:
        pieces.append(text[position : entity.start])
        pieces.append(tokens[same_value_key(entity)])
        position = entity.end
    pieces.append(text[position:])
    return "".join(pieces)


# Percentage of the original characters that were replaced, to 1 decimal place
def masking_percentage(text: str, entities: list[Entity]) -> float:
    if not text:
        return 0.0
    masked_characters = sum(entity.end - entity.start for entity in entities)
    return round(100 * masked_characters / len(text), 1)


# Count how many times each identifier type was masked, for example {"PHONE": 2}
def count_by_type(entities: list[Entity]) -> dict[str, int]:
    counts = {}
    for entity in entities:
        counts[entity.type] = counts.get(entity.type, 0) + 1
    return counts


# Mask every explicit identifier and return the masked text, manifest, percentage and counts
def mask(text: str, entities: list[Entity]) -> MaskResult:
    kept = non_overlapping(entities)
    tokens = assign_tokens(kept)
    return MaskResult(
        masked_text=replace_spans(text, kept, tokens),
        placeholder_manifest=list(tokens.values()),
        masking_percentage=masking_percentage(text, kept),
        entity_counts=count_by_type(kept),
    )
