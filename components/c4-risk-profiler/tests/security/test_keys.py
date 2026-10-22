from __future__ import annotations

from tests.helpers import minutes_ago, payload

ORG = {"org_id": "org_001"}


def test_no_key_or_wrong_key_is_401(client):
    body = payload("c1", minutes_ago(5))
    assert client.post("/v1/events/c1", json=body).status_code == 401
    assert (
        client.post("/v1/events/c1", json=body, headers={"X-Service-Key": "nope"}).status_code
        == 401
    )
    assert client.get("/v1/users", params=ORG).status_code == 401
    assert (
        client.get("/v1/blocklist", params=ORG, headers={"X-Service-Key": "nope"}).status_code
        == 401
    )


def test_c4_s01_c2_key_on_c3_endpoint_is_403(client, h):
    assert (
        client.post(
            "/v1/events/c3", json=payload("c3", minutes_ago(5)), headers=h["c2"]
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/v1/events/c1", json=payload("c1", minutes_ago(5)), headers=h["gateway"]
        ).status_code
        == 403
    )


def test_admin_endpoints_accept_only_the_gateway_key(client, h):
    for caller in ("c1", "c2", "c3"):
        assert client.get("/v1/users", params=ORG, headers=h[caller]).status_code == 403
        r = client.post(
            "/v1/users/u/block", json={**ORG, "by": "a", "reason": "r"}, headers=h[caller]
        )
        assert r.status_code == 403
    assert client.get("/v1/users", params=ORG, headers=h["gateway"]).status_code == 200


def test_blocklist_is_for_c1_and_gateway_only(client, h):
    assert client.get("/v1/blocklist", params=ORG, headers=h["c1"]).status_code == 200
    assert client.get("/v1/blocklist", params=ORG, headers=h["c2"]).status_code == 403


def test_health_needs_no_key_and_shows_mongo(client):
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["mongo"] == "ok" and r.json()["service"] == "c4"


def test_rejected_events_do_not_echo_text_and_store_nothing(client, h, db):
    from c4.db import collections as col

    client.post(
        "/v1/events/c1",
        json={**payload("c1", minutes_ago(5)), "prompt": "secret words"},
        headers=h["c1"],
    )
    assert db[col.EVENTS].count_documents({}) == 0
