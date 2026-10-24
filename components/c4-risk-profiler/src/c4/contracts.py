"""Check events against the shared JSON schemas in contracts/schemas (M1).

The schemas are the written agreement between C4 and C1, C2 and C3 about which fields an
event has. If a field is missing, has the wrong type, or an extra field appears, the event
is refused. This keeps every component honest.
"""

from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

SOURCES = ("c1", "c2", "c3")


def load_validators(contracts_dir: Path) -> dict[str, Draft202012Validator]:
    """Read the three schema files once at start-up and build one checker for each."""
    out = {}
    for src in SOURCES:
        schema = json.loads(
            (Path(contracts_dir) / "schemas" / f"{src}_event.schema.json").read_text()
        )
        # FormatChecker makes the "date-time" format really checked, not just ignored
        out[src] = Draft202012Validator(schema, format_checker=FormatChecker())
    return out


def validate_event(validators: dict, source: str, payload: dict) -> list[str]:
    """Return a sorted list of problems. An empty list means the event is valid."""
    return sorted(e.message for e in validators[source].iter_errors(payload))
