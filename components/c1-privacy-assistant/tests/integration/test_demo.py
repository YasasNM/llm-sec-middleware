"""Tests for the temporary demo page and its key-free route (only when C1_DEMO=1)."""

import logging

import pytest
from helpers import make_client

SECRET_TEXT = "Patient Nimal Perera NIC 199512345678, 94-year-old in Galle ward 3, review P0012"
SECRET_VALUES = ["Nimal Perera", "199512345678"]


@pytest.fixture()
def demo_client(tmp_path):
    return make_client(tmp_path, demo=True)


@pytest.mark.parametrize("route", ["/demo", "/demo/samples"])
def test_demo_is_off_by_default(tmp_path, route):
    assert make_client(tmp_path).get(route).status_code == 404


def test_demo_analyze_is_off_by_default(tmp_path):
    reply = make_client(tmp_path).post("/demo/analyze", json={"text": "hi"})
    assert reply.status_code == 404


def test_demo_page_is_served(demo_client):
    reply = demo_client.get("/demo")
    assert reply.status_code == 200 and "C1 Privacy Assistant" in reply.text


def test_demo_page_has_no_external_links(demo_client):
    page = demo_client.get("/demo").text
    assert "http://" not in page and "https://" not in page


def test_samples_are_listed(demo_client):
    samples = demo_client.get("/demo/samples").json()
    assert len(samples) == 90 and samples[0]["text"]


def test_demo_analyze_masks_without_a_key(demo_client):
    body = demo_client.post("/demo/analyze", json={"text": SECRET_TEXT}).json()
    assert body["masked_text"].startswith("Patient [PATIENT_NAME_1] NIC [NIC_1]")
    assert "P0012" in body["masked_text"]
    assert body["masking_percentage"] > 0
    assert body["tokens"] == [
        {"token": "[PATIENT_NAME_1]", "type": "PATIENT_NAME", "count": 1},
        {"token": "[NIC_1]", "type": "NIC", "count": 1},
    ]
    assert body["risk"]["overall_level"] == "High"
    assert "elderly" in body["reid"]["masked_with_suggestions"]


def test_demo_reply_never_contains_detected_values(demo_client):
    reply = demo_client.post("/demo/analyze", json={"text": SECRET_TEXT})
    for value in SECRET_VALUES:
        assert value not in reply.text


def test_demo_analyze_logs_no_patient_values(demo_client, caplog):
    caplog.set_level(logging.DEBUG)
    demo_client.post("/demo/analyze", json={"text": SECRET_TEXT})
    for value in SECRET_VALUES:
        assert value not in caplog.text


def test_demo_analyze_keeps_the_size_limit(demo_client):
    reply = demo_client.post("/demo/analyze", json={"text": SECRET_TEXT + " x" * 4500})
    assert reply.status_code == 422 and "Nimal Perera" not in reply.text


def test_v1_routes_still_need_the_key_in_demo_mode(demo_client):
    assert demo_client.post("/v1/analyze", json={"text": "hi"}).status_code == 401


def test_demo_text_made_only_of_tokens_is_clean(demo_client):
    body = demo_client.post(
        "/demo/analyze", json={"text": "[PATIENT_NAME_1] [PATIENT_NAME_2] [PHONE_1]"}
    ).json()
    assert body["entities"] == [] and body["reid"]["items"] == []
    assert body["risk"]["explicit"]["score"] == 0 and body["reid"]["score"] == 0
    assert body["masked_text"] == "[PATIENT_NAME_1] [PATIENT_NAME_2] [PHONE_1]"


def test_demo_route_uses_the_name_lexicon(demo_client):
    body = demo_client.post("/demo/analyze", json={"text": "send to nimal and kumari"}).json()
    assert [e["source"] for e in body["entities"]] == ["lexicon", "lexicon"]
    assert body["masked_text"] == "send to [PATIENT_NAME_1] and [PATIENT_NAME_2]"


def test_demo_gives_tidy_edits_and_no_clue_for_a_district(demo_client):
    text = "Patient Linuka, 94-year-old HIV patient in Galle district, ward 3"
    reid = demo_client.post("/demo/analyze", json={"text": text}).json()["reid"]
    assert [item["type"] for item in reid["items"]] == ["AGE", "CONDITION", "WARD"]  # no LOCATION
    age = reid["items"][0]["edit"]
    assert text[age["start"] : age["end"]] == "94-year-old" and age["replacement"] == "elderly"
    merged = reid["edits"][0]
    assert text[merged["start"] : merged["end"]] == "94-year-old HIV patient"
    assert merged["replacement"] == "elderly patient with a chronic condition"
