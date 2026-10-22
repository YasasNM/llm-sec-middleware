"""Security tests C1-S-01 (part) and C1-S-02: keys, size limits, and no patient text leaking."""

import logging

import pytest
from helpers import GATEWAY_HEADERS, OTHER_KEY, make_client

SECRET_TEXT = "Patient Nimal Perera NIC 199512345678 phone 0771234567"
SECRET_VALUES = ["Nimal Perera", "199512345678", "0771234567"]


@pytest.fixture()
def client(tmp_path):
    return make_client(tmp_path)


@pytest.mark.parametrize("route", ["/v1/analyze", "/v1/reid"])
def test_missing_key_is_401(client, route):
    assert client.post(route, json={"text": "hi"}).status_code == 401


@pytest.mark.parametrize("route", ["/v1/analyze", "/v1/reid"])
def test_wrong_key_is_401(client, route):
    reply = client.post(route, json={"text": "hi"}, headers={"X-Service-Key": "guess"})
    assert reply.status_code == 401


def test_valid_key_of_another_service_is_403(client):
    reply = client.post("/v1/analyze", json={"text": "hi"}, headers={"X-Service-Key": OTHER_KEY})
    assert reply.status_code == 403


def test_health_needs_no_key(client):
    assert client.get("/health").status_code == 200


def test_too_long_text_is_422_without_echoing_the_text(client):
    long_text = SECRET_TEXT + " x" * 4500
    reply = client.post("/v1/analyze", json={"text": long_text}, headers=GATEWAY_HEADERS)
    assert reply.status_code == 422
    for value in SECRET_VALUES:
        assert value not in reply.text


def test_bad_body_is_422_without_echoing_the_text(client):
    reply = client.post("/v1/reid", json={"prompt": SECRET_TEXT}, headers=GATEWAY_HEADERS)
    assert reply.status_code == 422
    for value in SECRET_VALUES:
        assert value not in reply.text


def test_no_patient_values_in_logs(client, caplog):
    caplog.set_level(logging.DEBUG)
    client.post("/v1/analyze", json={"text": SECRET_TEXT}, headers=GATEWAY_HEADERS)
    client.post("/v1/reid", json={"text": SECRET_TEXT}, headers=GATEWAY_HEADERS)
    for value in SECRET_VALUES:
        assert value not in caplog.text


def test_no_cors_headers(client):
    reply = client.options("/v1/analyze", headers={"Origin": "http://evil.example"})
    assert "access-control-allow-origin" not in reply.headers
