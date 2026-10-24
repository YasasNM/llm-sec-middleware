"""Tests for the dashboard API (demo data, block, gate, live attack) and the page itself."""

from __future__ import annotations

import time

from c4.config import Settings
from c4.demo.data import start_attack
from c4.main import create_app
from fastapi.testclient import TestClient

from tests.conftest import KEYS


def test_dashboard_page_is_served(client):
    assert client.get("/dashboard/").status_code == 200
    assert "C4 User Risk Profiler" in client.get("/dashboard/").text
    # the home page sends people to the dashboard
    assert client.get("/", follow_redirects=False).status_code == 307


def test_dashboard_can_be_switched_off(db):
    settings = Settings(
        accepted_keys=",".join(f"{n}:{k}" for n, k in KEYS.items()),
        mongo_url="mongomock://x/c4",
        dashboard_enabled=False,
    )
    off = TestClient(create_app(settings, db))
    assert off.get("/dashboard/").status_code == 404
    assert off.get("/ui/api/overview", params={"org_id": "org_001"}).status_code == 404


def test_load_demo_data_gives_seven_users_and_three_critical(client):
    r = client.post("/ui/api/demo/load")
    assert r.status_code == 200 and r.json()["users"] == 7
    summary = client.get("/ui/api/overview").json()["summary"]
    assert summary["total_users"] == 7
    assert summary["P1"] == 3  # escalator, burst attacker and compromised account
    assert summary["blocked"] == 0


def test_reset_empties_everything(client):
    client.post("/ui/api/demo/load")
    client.post("/ui/api/demo/reset")
    assert client.get("/ui/api/overview").json()["summary"]["total_users"] == 0


def test_blocked_user_is_refused_by_the_gate_and_can_return(client):
    client.post("/ui/api/demo/load")
    user = "user_005"

    # not blocked yet: allowed, but the reason says C4 recommends a block
    r = client.post("/ui/api/gate", json={"user_id": user})
    assert r.status_code == 200 and "recommends" in r.json()["reason"]

    client.post(f"/ui/api/users/{user}/block", json={"reason": "test"})
    r = client.post("/ui/api/gate", json={"user_id": user})
    assert r.status_code == 403 and r.json()["decision"] == "BLOCKED"

    # the real C1 endpoint shows the same user, because both read the same list
    ids = client.get(
        "/v1/blocklist", params={"org_id": "org_001"}, headers={"X-Service-Key": KEYS["c1"]}
    )
    assert user in ids.json()["user_ids"]

    client.post(f"/ui/api/users/{user}/unblock", json={"reason": "test"})
    assert client.post("/ui/api/gate", json={"user_id": user}).status_code == 200

    log = client.get("/ui/api/access").json()["actions"]
    assert [a["action"] for a in log] == ["unblock", "block"]  # newest first


def test_unknown_user_is_allowed_and_cannot_be_blocked(client):
    assert client.post("/ui/api/gate", json={"user_id": "nobody"}).status_code == 200
    assert client.post("/ui/api/users/nobody/block", json={"reason": "x"}).status_code == 404
    assert client.post("/ui/api/users/user_001/ban", json={"reason": "x"}).status_code == 404


def test_a_reason_is_required_to_block(client):
    client.post("/ui/api/demo/load")
    assert client.post("/ui/api/users/user_005/block", json={}).status_code == 422
    assert client.post("/ui/api/users/user_005/block", json={"reason": ""}).status_code == 422


def test_user_detail_has_everything_the_page_needs(client):
    client.post("/ui/api/demo/load")
    d = client.get("/ui/api/users/user_005").json()
    assert d["profile"]["priority"] == "P1"
    assert len(d["hour_histogram"]) == 24 and d["sessions"] and d["timeline"]

    # no field called prompt, answer or text anywhere in what the page receives
    def keys(x):
        if isinstance(x, dict):
            for k, v in x.items():
                yield k
                yield from keys(v)
        elif isinstance(x, list):
            for v in x:
                yield from keys(v)

    assert not {"prompt", "answer", "text", "response"} & set(keys(d))
    assert client.get("/ui/api/users/missing").status_code == 404


def test_rules_endpoint_reads_the_rules_file(client):
    d = client.get("/ui/api/rules").json()
    assert [r["id"] for r in d["rules"]] == ["R1", "R2", "R3", "R4", "R5", "R6", "R7"]
    assert d["priorities"] == {"P1": 80.0, "P2": 60.0, "P3": 30.0}
    assert all(r["description"] for r in d["rules"])


def test_live_attack_pushes_a_user_to_critical(client):
    app = client.app
    assert start_attack(app.state, "org_001", "user_live", pause_seconds=0)
    for _ in range(100):  # wait up to 10 s for the background thread
        if not app.state.sim["running"]:
            break
        time.sleep(0.1)
    assert not app.state.sim["running"]
    users = {u["user_id"]: u for u in client.get("/ui/api/overview").json()["users"]}
    assert users["user_live"]["priority"] == "P1"


def test_only_one_attack_runs_at_a_time(client):
    app = client.app
    assert start_attack(app.state, "org_001", "user_a", pause_seconds=0.3)
    assert not start_attack(app.state, "org_001", "user_b", pause_seconds=0)
    for _ in range(100):
        if not app.state.sim["running"]:
            break
        time.sleep(0.1)


def test_moving_time_forward_makes_scores_fade_and_can_be_undone(client):
    client.post("/ui/api/demo/load")

    def score(user):
        users = client.get("/ui/api/overview").json()["users"]
        return next(u["score"] for u in users if u["user_id"] == user)

    start = score("user_004")
    assert start == 100.0

    clock = client.post("/ui/api/demo/time", json={"add_hours": 24 * 10}).json()
    assert clock["offset_hours"] == 240
    assert score("user_004") < 40  # ten days is more than three half-lives

    clock = client.post("/ui/api/demo/time", json={"add_hours": 24 * 30}).json()
    assert score("user_004") == 0.0  # past the 30-day window nothing is left

    assert client.post("/ui/api/demo/time", json={"reset": True}).json()["offset_hours"] == 0
    assert score("user_004") == start


def test_loading_demo_data_puts_the_clock_back(client):
    client.post("/ui/api/demo/time", json={"add_hours": 100})
    client.post("/ui/api/demo/load")
    assert client.get("/ui/api/overview").json()["clock"]["offset_hours"] == 0


def test_time_cannot_move_backwards_or_too_far(client):
    assert client.post("/ui/api/demo/time", json={"add_hours": -5}).status_code == 422
    assert client.post("/ui/api/demo/time", json={"add_hours": 99999}).status_code == 422
