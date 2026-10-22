"""Unit tests for pipeline.analyze_text (the shared analysis used by every route)."""

from c1.config import C1_ROOT
from c1.configs import load_config
from c1.pipeline import Resources, analyze_text, elapsed_ms

DEMO = (
    "Patient Nimal Perera NIC 199512345678, 94-year-old HIV patient in Galle ward 3, review P0012"
)


# Resources with no name model, so the heuristic is used and no model file is needed
def heuristic_resources() -> Resources:
    return Resources(
        name_model=None,
        name_threshold=0.65,
        reid_lists=load_config("reid_lists.yaml", C1_ROOT / "configs"),
        risk_rules=load_config("default.yaml", C1_ROOT / "configs")["risk"],
    )


def test_analyze_finds_explicit_and_reid():
    result = analyze_text(DEMO, heuristic_resources())
    assert [(e.type, e.value) for e in result.entities] == [
        ("PATIENT_NAME", "Nimal Perera"),
        ("NIC", "199512345678"),
    ]
    assert result.reid.level == "High"
    assert result.risk["explicit"] == {"score": 50, "level": "High"}
    assert result.risk["overall_level"] == "High"


def test_internal_id_is_never_an_entity():
    result = analyze_text(DEMO, heuristic_resources())
    assert all("P0012" not in entity.value for entity in result.entities)


def test_overlaps_are_resolved_inside_the_pipeline():
    result = analyze_text("MRN: 0771234567", heuristic_resources())
    assert [entity.type for entity in result.entities] == ["MRN"]


def test_safe_prompt():
    result = analyze_text("What is the dose of paracetamol?", heuristic_resources())
    assert result.entities == [] and result.risk["overall_level"] == "Low"


def test_name_detector_label_without_model():
    assert heuristic_resources().name_detector == "heuristic"


def test_elapsed_ms_is_a_whole_number():
    assert isinstance(elapsed_ms(0.0), int)
