"""Overlap rule: when two findings cover the same characters, keep only one (BLUEPRINT 12.1).

Order of preference:
1. the longer span (it carries more of the identifier);
2. then the higher confidence;
3. then the type that matters more, so "MRN: 0771234567" stays an MRN, not a phone.
"""

from c1.detection.entity import Entity
from c1.detection.regex import spans_overlap

# Lower number wins a full tie. Same order as the corpus's row priority (data/README.md).
TYPE_PRIORITY = {"NIC": 0, "MRN": 1, "PHONE": 2, "EMAIL": 3, "PATIENT_NAME": 4, "DOB": 5}


# Sort key that puts the strongest finding first
def strength(entity: Entity) -> tuple:
    length = entity.end - entity.start
    priority = TYPE_PRIORITY.get(entity.type, 99)
    return (-length, -entity.confidence, priority, entity.start)


# True when this entity shares a character with any entity already kept
def clashes_with_kept(entity: Entity, kept: list[Entity]) -> bool:
    span = (entity.start, entity.end)
    return any(spans_overlap(span, (other.start, other.end)) for other in kept)


# Keep the strongest finding wherever findings overlap; return the rest sorted by position
def resolve_overlaps(entities: list[Entity]) -> list[Entity]:
    kept = []
    for entity in sorted(entities, key=strength):
        if not clashes_with_kept(entity, kept):
            kept.append(entity)
    return sorted(kept, key=lambda entity: (entity.start, entity.end))
