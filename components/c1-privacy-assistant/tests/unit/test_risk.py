"""Unit tests for the M7 live risk level."""

from c1.configs import load_config
from c1.detection.entity import Entity
from c1.risk import explicit_level, explicit_score, higher_level, live_risk

RULES = load_config("default.yaml")["risk"]


# Make an entity of a given type; only the type matters for the risk level
def make(entity_type: str) -> Entity:
    return Entity(entity_type, 0, 1, "x", 0.9, "test")


def test_blueprint_example_name_and_phone_is_40_medium():
    result = live_risk([make("PATIENT_NAME"), make("PHONE")], "Low", RULES)
    assert result["explicit"] == {"score": 40, "level": "Medium"}


def test_nic_and_name_is_high():
    assert explicit_level(explicit_score([make("NIC"), make("PATIENT_NAME")], RULES), RULES) == (
        "High"
    )


def test_nothing_found_is_low():
    assert live_risk([], "Low", RULES) == {
        "explicit": {"score": 0, "level": "Low"},
        "reid_level": "Low",
        "overall_level": "Low",
    }


def test_score_is_capped_at_100():
    assert explicit_score([make("NIC")] * 10, RULES) == 100


def test_unknown_type_counts_as_low():
    assert explicit_score([make("SOMETHING_NEW")], RULES) == 10


def test_overall_takes_the_higher_level():
    assert live_risk([make("PHONE")], "High", RULES)["overall_level"] == "High"
    assert higher_level("Medium", "Low") == "Medium"
    assert higher_level("Low", "Low") == "Low"
