"""M6: score to priority P1..P4 and recommendation."""

from __future__ import annotations

from .ruleset import Ruleset

LABELS = {"P1": "P1 - Critical", "P2": "P2 - High", "P3": "P3 - Medium", "P4": "P4 - Low"}
RECOMMENDATION = {
    "P1": "BLOCK_RECOMMENDED",
    "P2": "CLOSE_MONITORING",
    "P3": "WATCH",
    "P4": "NONE",
}


def priority_for(score: float, rs: Ruleset) -> str:
    if score >= rs.priorities["P1"]:
        return "P1"
    if score >= rs.priorities["P2"]:
        return "P2"
    if score >= rs.priorities["P3"]:
        return "P3"
    return "P4"
