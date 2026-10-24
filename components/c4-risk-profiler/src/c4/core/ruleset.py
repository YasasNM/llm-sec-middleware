"""Load and check configs/rules.yaml (NFR-08: thresholds are not hard-coded)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

# The seven rules from the proposal. The numbers for each live in configs/rules.yaml.
RULE_IDS = ("R1", "R2", "R3", "R4", "R5", "R6", "R7")

RULE_TITLES = {
    "R1": "Suspicious prompt",
    "R2": "High-severity event",
    "R3": "Repeated suspicious activity",
    "R4": "Policy violation",
    "R5": "Identifier leakage in answers",
    "R6": "Unusual time with suspicious activity",
    "R7": "Escalating across sessions",
}


def describe_rule(rs: Ruleset, rule_id: str) -> str:
    """One plain sentence per rule, with the numbers taken from rules.yaml (never typed here)."""
    p = rs.rules[rule_id]
    # Each sentence is wrapped in a lambda so only the asked rule is built
    # (the other rules may not have the same settings).
    text = {
        "R1": lambda: "C2 blocked the prompt as suspicious.",
        "R2": lambda: (
            f"A severe event: C2 score of {p['c2_score']} or higher, "
            f"or {p['leak_count']} or more identifiers leaked in one answer."
        ),
        "R3": lambda: (
            f"{p['count']} or more suspicious events within {p['window_minutes']} minutes. "
            "Counted once per window."
        ),
        "R4": lambda: "C3 found a policy violation in the answer.",
        "R5": lambda: "C3 found leaked personal identifiers in the answer.",
        "R6": lambda: (
            "A suspicious event at an hour the user almost never works "
            f"(under {round(p['min_share'] * 100)}% of their requests, "
            f"needs at least {p['min_requests']} requests of history)."
        ),
        "R7": lambda: (
            f"Suspicious events rise from session to session over {p['sessions']} sessions."
        ),
    }
    return text[rule_id]()


@dataclass(frozen=True)
class Ruleset:
    """The loaded rules.yaml. frozen=True means nothing can change it while C4 runs."""

    version: str
    half_life_hours: float
    window_days: int
    rules: dict[str, dict[str, Any]]
    priorities: dict[str, float]

    def impact(self, rule_id: str) -> float:
        return float(self.rules[rule_id]["impact"])

    def param(self, rule_id: str, name: str) -> Any:
        return self.rules[rule_id][name]


def parse_ruleset(data: dict[str, Any]) -> Ruleset:
    """Check that the YAML is complete and sensible, then build the Ruleset."""
    # A broken rules file should stop C4 at start-up, not give wrong scores later
    for key in ("ruleset_version", "half_life_hours", "window_days", "rules", "priorities"):
        if key not in data:
            raise ValueError(f"rules file is missing '{key}'")
    rules = data["rules"]
    for rid in RULE_IDS:
        if rid not in rules or "impact" not in rules[rid]:
            raise ValueError(f"rules file: rule {rid} needs an 'impact'")
    if float(data["half_life_hours"]) <= 0:
        raise ValueError("half_life_hours must be greater than 0")
    pr = data["priorities"]
    if not (float(pr["P1"]) > float(pr["P2"]) > float(pr["P3"]) >= 0):
        raise ValueError("priorities must satisfy P1 > P2 > P3 >= 0")
    return Ruleset(
        version=str(data["ruleset_version"]),
        half_life_hours=float(data["half_life_hours"]),
        window_days=int(data["window_days"]),
        rules={k: dict(v) for k, v in rules.items()},
        priorities={k: float(v) for k, v in pr.items()},
    )


def load_ruleset(path: str | Path) -> Ruleset:
    with open(path, encoding="utf-8") as fh:
        return parse_ruleset(yaml.safe_load(fh))
