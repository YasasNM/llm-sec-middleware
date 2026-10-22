"""Check events against the shared JSON schemas in contracts/schemas (M1)."""

from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

SOURCES = ("c1", "c2", "c3")


def load_validators(contracts_dir: Path) -> dict[str, Draft202012Validator]:
    out = {}
    for src in SOURCES:
        schema = json.loads(
            (Path(contracts_dir) / "schemas" / f"{src}_event.schema.json").read_text()
        )
        out[src] = Draft202012Validator(schema, format_checker=FormatChecker())
    return out


def validate_event(validators: dict, source: str, payload: dict) -> list[str]:
    return sorted(e.message for e in validators[source].iter_errors(payload))
