"""Tests for the temporary demo page and its key-free route (only when C1_DEMO=1)."""

import logging

import pytest
from helpers import make_client

from c1.api.demo import entity_rows
from c1.detection.entity import Entity
from c1.log.decision_log import read_decisions

SECRET_TEXT = "Patient Nimal Perera NIC 199512345678, 94-year-old in Galle ward 3, review P0012"
SECRET_VALUES = ["Nimal Perera", "199512345678"]


@pytest.fixture()
def demo_client(tmp_path):
    return make_client(tmp_path, demo=True)


@pytest.mark.parametrize("route", ["/demo", "/demo/samples"])
def test_demo_is_off_by_default(tmp_path, route):
    assert make_client(tmp_path).get(route).status_code == 404


@pytest.mark.parametrize("route", ["/demo/analyze", "/demo/send"])
def test_demo_post_routes_are_off_by_default(tmp_path, route):
    reply = make_client(tmp_path).post(route, json={"text": "hi"})
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


def test_each_entity_row_has_its_risk_class(demo_client):
    entities = demo_client.post("/demo/analyze", json={"text": SECRET_TEXT}).json()["entities"]
    assert [(e["type"], e["risk_class"]) for e in entities] == [
        ("PATIENT_NAME", "medium"),
        ("NIC", "high"),
    ]


def test_risk_class_of_every_explicit_type(demo_client):
    text = (
        "Patient Nimal Perera, MRN 123456, phone 0771234567, nimal@example.com, "
        "DOB 14/03/1985, NIC 199512345678"
    )
    entities = demo_client.post("/demo/analyze", json={"text": text}).json()["entities"]
    classes = {e["type"]: e["risk_class"] for e in entities}
    assert classes == {
        "PATIENT_NAME": "medium",
        "MRN": "high",
        "PHONE": "medium",
        "EMAIL": "medium",
        "DOB": "medium",
        "NIC": "high",
    }


def test_risk_class_comes_from_the_config_and_defaults_to_low():
    rules = {"entity_classes": {"NIC": "high"}}
    nic = Entity("NIC", 0, 12, "199512345678", 0.99, "regex")
    other = Entity("ADDRESS", 13, 20, "x", 0.5, "regex")
    rows = entity_rows([nic, other], rules)
    assert [row["risk_class"] for row in rows] == ["high", "low"]


def test_page_takes_the_risk_class_from_c1_and_turns_off_autofill(demo_client):
    page = demo_client.get("/demo").text
    assert "item.risk_class" in page and '"HIGH RISK"' not in page
    for attribute in ['autocomplete="off"', 'autocorrect="off"', 'autocapitalize="off"',
                      'spellcheck="false"']:  # fmt: skip
        assert attribute in page


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


# --- Demo send: "Send" goes as typed, "Mask all and send" masks everything ---------------------
# DEMO ONLY: /v1/check always masks every direct identifier (see test_check.py).

KUMARI_TEXT = "Patient Kumari Silva, NIC 199012345678, needs a follow-up."
USER_MASKED = "Patient [PATIENT_NAME_1], NIC 199012345678, needs a follow-up."


# One demo send with the given button mode
def demo_send(client, text, mode):
    return client.post("/demo/send", json={"text": text, "mode": mode})


def test_page_has_no_browser_dialogs(demo_client):
    page = demo_client.get("/demo").text
    assert "/demo/send" in page and "sent unmasked" in page
    assert "alert(" not in page and "confirm(" not in page


def test_send_keeps_the_unmasked_nic_and_the_user_masked_name(demo_client):
    body = demo_send(demo_client, USER_MASKED, "as_typed").json()
    assert body["sent_text"] == USER_MASKED  # nothing masked automatically
    assert body["masked_by_user"] == 1
    assert body["unmasked_sent"] == 1 and body["types"] == ["NIC"]
    assert body["masked_automatically"] == 0 and body["generalised"] == 0


def test_send_with_nothing_masked_sends_everything_as_typed(demo_client):
    body = demo_send(demo_client, KUMARI_TEXT, "as_typed").json()
    assert body["sent_text"] == KUMARI_TEXT
    assert body["masked_by_user"] == 0
    assert body["unmasked_sent"] == 2 and body["types"] == ["NIC", "PATIENT_NAME"]


def test_mask_all_and_send_still_masks_everything(demo_client):
    body = demo_send(demo_client, KUMARI_TEXT, "mask_all").json()
    assert body["sent_text"] == "Patient [PATIENT_NAME_1], NIC [NIC_1], needs a follow-up."
    assert body["unmasked_sent"] == 0 and body["types"] == []
    assert body["masked_automatically"] == 2


def test_mask_all_numbers_new_tokens_after_the_users_own(demo_client):
    text = "Patient [PATIENT_NAME_1] and patient Nimal Perera, NIC 199012345678"
    body = demo_send(demo_client, text, "mask_all").json()
    assert body["sent_text"] == "Patient [PATIENT_NAME_1] and patient [PATIENT_NAME_2], NIC [NIC_1]"


def test_mask_all_also_generalises_the_clues(demo_client):
    text = "Patient Kumari Silva, 94-year-old HIV patient in Galle ward 3"
    body = demo_send(demo_client, text, "mask_all").json()
    assert body["sent_text"] == (
        "Patient [PATIENT_NAME_1], elderly patient with a chronic condition "
        "in the ward in Galle district"
    )
    assert body["generalised"] == 4


def test_send_writes_one_phi_free_log_row(tmp_path):
    client = make_client(tmp_path, demo=True)
    demo_send(client, USER_MASKED, "as_typed")
    rows = read_decisions(str(tmp_path / "c1.db"))
    assert len(rows) == 1
    row = rows[0]
    assert row["decision"] == "DEMO_SEND_AS_TYPED"
    assert row["unmasked_count"] == 1 and row["unmasked_types"] == '["NIC"]'
    raw = (tmp_path / "c1.db").read_bytes()
    for value in [b"Kumari", b"Silva", b"199012345678", b"follow-up", b"PATIENT_NAME_1]"]:
        assert value not in raw


def test_both_modes_log_rows_without_text(tmp_path):
    client = make_client(tmp_path, demo=True)
    demo_send(client, KUMARI_TEXT, "as_typed")
    demo_send(client, KUMARI_TEXT, "mask_all")
    rows = read_decisions(str(tmp_path / "c1.db"))
    assert [(r["decision"], r["unmasked_count"]) for r in rows] == [
        ("DEMO_SEND_AS_TYPED", 2),
        ("DEMO_SEND_MASK_ALL", 0),
    ]
    raw = (tmp_path / "c1.db").read_bytes()
    assert b"Kumari" not in raw and b"199012345678" not in raw


def test_send_logs_no_patient_values(demo_client, caplog):
    caplog.set_level(logging.DEBUG)
    demo_send(demo_client, KUMARI_TEXT, "as_typed")
    assert "unmasked_sent=2" in caplog.text
    for value in ["Kumari", "Silva", "199012345678"]:
        assert value not in caplog.text


def test_send_rejects_an_unknown_mode(demo_client):
    reply = demo_send(demo_client, KUMARI_TEXT, "send_anyway")
    assert reply.status_code == 422 and "Kumari" not in reply.text


def test_send_keeps_the_size_limit(demo_client):
    reply = demo_send(demo_client, KUMARI_TEXT + " x" * 4500, "as_typed")
    assert reply.status_code == 422 and "Kumari" not in reply.text
