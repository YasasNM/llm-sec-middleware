"""M6: score to priority P1..P4 and recommendation."""

from __future__ import annotations

from .ruleset import Ruleset

# Names shown to people
LABELS = {"P1": "P1 - Critical", "P2": "P2 - High", "P3": "P3 - Medium", "P4": "P4 - Low"}
# What C4 advises for each priority. Only P1 recommends a block, and a person still decides.
RECOMMENDATION = {
    "P1": "BLOCK_RECOMMENDED",
    "P2": "CLOSE_MONITORING",
    "P3": "WATCH",
    "P4": "NONE",
}


def priority_for(score: float, rs: Ruleset) -> str:
    """Turn a score into P1..P4 using the limits in rules.yaml (P1 >= 80, P2 >= 60, P3 >= 30)."""
    # Check from the highest limit down; the first one the score reaches wins
    if score >= rs.priorities["P1"]:
        return "P1"
    if score >= rs.priorities["P2"]:
        return "P2"
    if score >= rs.priorities["P3"]:
        return "P3"
    return "P4"
