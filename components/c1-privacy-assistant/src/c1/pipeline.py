"""The one shared analysis function used by /v1/analyze, /v1/reid and later /v1/check and /v1/ocr.

analyze_text() runs, in order:
1. M2 regex (NIC, phone, MRN, email, DOB) and M3/M4 names (model, or heuristic fallback);
2. the overlap rule, so each character belongs to at most one finding;
3. M5 re-identification scoring;
4. M7 live risk level.
"""

import time
from dataclasses import dataclass

from c1.config import Settings
from c1.configs import load_config
from c1.detection.entity import Entity
from c1.detection.ml_names import load_name_model
from c1.detection.names import (
    add_repeated_names,
    find_names,
    load_name_lexicon,
    name_detector_label,
)
from c1.detection.overlaps import resolve_overlaps
from c1.detection.placeholders import blank_placeholders, outside_placeholders
from c1.detection.regex import detect_regex
from c1.reid.scorer import ReidResult, score_reid
from c1.risk import live_risk


@dataclass
class Resources:
    """Things loaded once at start-up and shared by every request."""

    name_model: dict | None
    name_threshold: float
    reid_lists: dict
    risk_rules: dict
    name_lexicon: frozenset[str] = frozenset()  # empty when the lexicon fallback is off

    # "ml" when the trained model loaded, otherwise "heuristic"
    @property
    def name_detector(self) -> str:
        return name_detector_label(self.name_model)


@dataclass
class Analysis:
    """The result of analyze_text(): explicit findings, re-id result and live risk."""

    entities: list[Entity]
    reid: ReidResult
    risk: dict


# The name lexicon when the setting is on, otherwise an empty set
def lexicon_for(settings: Settings) -> frozenset[str]:
    return load_name_lexicon(settings.lexicon_path) if settings.name_lexicon else frozenset()


# Load the name model and the YAML rules once, when the service starts
def load_resources(settings: Settings) -> Resources:
    return Resources(
        name_model=load_name_model(settings.model_path, settings.manifest_path),
        name_threshold=settings.name_threshold,
        reid_lists=load_config("reid_lists.yaml", settings.config_folder),
        risk_rules=load_config("default.yaml", settings.config_folder)["risk"],
        name_lexicon=lexicon_for(settings),
    )


# Find every explicit identifier (regex, names, lexicon names), then copy names to repeats.
# Detectors read a copy with placeholders blanked, so "[PATIENT_NAME_1]" never looks like a name.
def find_explicit(text: str, resources: Resources) -> list[Entity]:
    clean = blank_placeholders(text)
    regex = detect_regex(clean)
    model, threshold = resources.name_model, resources.name_threshold
    names = find_names(clean, model, threshold, resources.name_lexicon, regex)
    found = add_repeated_names(clean, resolve_overlaps(regex + names))
    return outside_placeholders(found, text)


# Run the whole analysis on one piece of text
def analyze_text(text: str, resources: Resources) -> Analysis:
    entities = find_explicit(text, resources)
    reid = score_reid(text, resources.reid_lists)
    risk = live_risk(entities, reid.level, resources.risk_rules)
    return Analysis(entities=entities, reid=reid, risk=risk)


# Milliseconds since `started` (a time.perf_counter() value), as a whole number
def elapsed_ms(started: float) -> int:
    return round((time.perf_counter() - started) * 1000)
