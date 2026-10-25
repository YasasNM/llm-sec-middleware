"""M7 live risk level: one summary for the UI from explicit PHI and re-identification risk.

Explicit score = sum of class weights of the explicit identifiers found (high 30, medium 20,
low 10), capped at 100. BLUEPRINT 9.1 example: name (20) + phone (20) = 40, Medium.
The overall level is the higher of the explicit level and the re-identification level.
Numbers live in configs/default.yaml.
"""

from c1.detection.entity import Entity

LEVEL_ORDER = {"Low": 0, "Medium": 1, "High": 2}


# Score the explicit identifiers found, 0 to 100
def explicit_score(entities: list[Entity], risk_rules: dict) -> int:
    total = 0
    for entity in entities:
        entity_class = risk_rules["entity_classes"].get(entity.type, "low")
        total += risk_rules["class_weights"][entity_class]
    return min(total, 100)


# Turn the explicit score into Low, Medium or High
def explicit_level(score: int, risk_rules: dict) -> str:
    if score >= risk_rules["levels"]["high_from"]:
        return "High"
    if score >= risk_rules["levels"]["medium_from"]:
        return "Medium"
    return "Low"


# The higher of two levels, e.g. ("Medium", "High") -> "High"
def higher_level(first: str, second: str) -> str:
    return first if LEVEL_ORDER[first] >= LEVEL_ORDER[second] else second


# The full live risk summary: explicit score and level, re-id level, overall level
def live_risk(entities: list[Entity], reid_level: str, risk_rules: dict) -> dict:
    score = explicit_score(entities, risk_rules)
    level = explicit_level(score, risk_rules)
    return {
        "explicit": {"score": score, "level": level},
        "reid_level": reid_level,
        "overall_level": higher_level(level, reid_level),
    }
