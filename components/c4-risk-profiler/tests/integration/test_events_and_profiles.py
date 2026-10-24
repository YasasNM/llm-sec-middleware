from __future__ import annotations

from datetime import timedelta

from c4.db import collections as col

from tests.helpers import block_event, iso, minutes_ago, now, payload, violation


def post(client, h, source, body, caller=None):
    return client.post(f"/v1/events/{source}", json=body, headers=h[caller or source])


def test_each_sender_can_post_a_valid_event(client, h):
    t = minutes_ago(10)
    assert post(client, h, "c1", payload("c1", t, masking_percentage=12.5)).status_code == 202
    assert post(client, h, "c2", payload("c2", t)).status_code == 202
    assert post(client, h, "c3", payload("c3", t)).status_code == 202


def test_c4_u01_same_event_id_twice_is_stored_once(client, h, db):
    body = payload("c2", minutes_ago(5))
    assert post(client, h, "c2", body).status_code == 202
    r = post(client, h, "c2", body)
    assert r.status_code == 200 and r.json() == {"duplicate": True}
    assert db[col.EVENTS].count_documents({}) == 1


def test_c4_u02_three_events_of_one_request_count_once(client, h):
    t = minutes_ago(5)
    a = post(client, h, "c1", payload("c1", t, req="req_X")).json()
    b = post(client, h, "c2", payload("c2", t, req="req_X")).json()
    c = post(client, h, "c3", payload("c3", t, req="req_X")).json()
    assert [a["request_counted"], b["request_counted"], c["request_counted"]] == [
        True,
        False,
        False,
    ]
    prof = client.get(
        "/v1/users/user_001/profile", params={"org_id": "org_001"}, headers=h["gateway"]
    ).json()
    assert prof["behaviour_summary"]["requests"] == 1
    assert prof["event_count"] == 3


def test_invalid_events_are_rejected_with_422(client, h):
    good = payload("c1", minutes_ago(5))
    assert post(client, h, "c1", {**good, "extra": 1}).status_code == 422
    assert post(client, h, "c1", {**good, "event_timestamp": "yesterday"}).status_code == 422
    assert (
        post(client, h, "c1", {k: v for k, v in good.items() if k != "org_id"}).status_code == 422
    )
    assert post(client, h, "c2", payload("c2", minutes_ago(5), c2_score=1.5)).status_code == 422
    assert client.post("/v1/events/c1", content="not json", headers=h["c1"]).status_code == 422


def test_timestamp_more_than_5_minutes_in_the_future_is_rejected(client, h):
    assert post(client, h, "c1", payload("c1", now() + timedelta(minutes=30))).status_code == 422
    assert post(client, h, "c1", payload("c1", now() + timedelta(minutes=2))).status_code == 202


def _burst(client, h, user="user_009"):
    """Six high-score blocks and two leak-heavy violations in a short time."""
    base = minutes_ago(30)
    for i in range(6):
        post(client, h, "c1", payload("c1", base + timedelta(minutes=i), user=user, req=f"b{i}"))
        post(
            client,
            h,
            "c2",
            block_event(base + timedelta(minutes=i), user=user, req=f"b{i}", c2_score=0.95),
        )
    for i in range(2):
        post(client, h, "c3", violation(base + timedelta(minutes=10 + i), user=user, leaks=3))


def test_burst_attacker_reaches_p1_with_block_recommendation(client, h):
    _burst(client, h)
    p = client.get(
        "/v1/users/user_009/profile", params={"org_id": "org_001"}, headers=h["gateway"]
    ).json()
    assert p["priority"] == "P1" and p["score"] >= 80
    assert p["recommendation"] == "BLOCK_RECOMMENDED" and p["status"] == "active"
    assert p["top_contributors"] and p["ruleset_version"] == "rules-v1"
    assert p["behaviour_summary"]["intent_blocks"] == 6
    assert p["behaviour_summary"]["policy_violations"] == 2
    assert p["behaviour_summary"]["leaked_items"] == 6


def test_every_point_of_the_score_traces_to_a_stored_rule_hit(client, h, db, rs):
    """NFR-01: explainability. Score equals the decayed sum of stored rule hits."""
    post(client, h, "c3", violation(minutes_ago(20), leaks=3))
    p = client.get(
        "/v1/users/user_001/profile", params={"org_id": "org_001"}, headers=h["gateway"]
    ).json()
    hits = list(db[col.HITS].find({"user_id": "user_001"}))
    assert {x["rule_id"] for x in hits} == {"R2", "R4", "R5"}
    assert all(x["event_id"] and x["ruleset_version"] == "rules-v1" for x in hits)
    assert abs(sum(c["points"] for c in p["top_contributors"]) - p["score"]) < 0.5


def test_timeline_shows_rule_fired_and_score_after_newest_first(client, h):
    base = minutes_ago(30)
    post(client, h, "c1", payload("c1", base, req="t1"))
    post(client, h, "c3", violation(base + timedelta(minutes=5), leaks=3, req="t1"))
    rows = client.get(
        "/v1/users/user_001/timeline", params={"org_id": "org_001"}, headers=h["gateway"]
    ).json()
    assert [r["source_label"] for r in rows] == ["Policy check", "Privacy check"]
    assert {x["rule_id"] for x in rows[0]["rules_fired"]} == {"R2", "R4", "R5"}
    assert rows[0]["score_after"] == 50.0 and rows[0]["display_severity"] == "High"
    assert rows[1]["rules_fired"] == [] and rows[1]["display_severity"] == "Low"
    assert all("prompt" not in r and "text" not in r for r in rows)  # no text fields, only labels


def test_late_event_is_recomputed_by_event_time(client, h, db):
    """C4-I-02: an outbox replay arrives late. The result equals in-order delivery."""
    base = minutes_ago(60)
    evs = [block_event(base + timedelta(minutes=5 * i), c2_score=0.95) for i in range(3)]
    for e in (evs[2], evs[0], evs[1]):  # out of order
        post(client, h, "c2", e)
    rules = sorted(x["rule_id"] for x in db[col.HITS].find({}))
    assert rules.count("R3") == 1 and rules.count("R1") == 3 and rules.count("R2") == 3


def test_careless_power_user_stays_p4(client, h):
    """Many events with high masking and no verdicts must not raise the score (K11)."""
    base = minutes_ago(120)
    for i in range(30):
        post(
            client,
            h,
            "c1",
            payload("c1", base + timedelta(minutes=i), user="user_020", masking_percentage=70.0),
        )
    p = client.get(
        "/v1/users/user_020/profile", params={"org_id": "org_001"}, headers=h["gateway"]
    ).json()
    assert p["priority"] == "P4" and p["score"] == 0
    assert p["behaviour_summary"]["avg_masking_percentage_30d"] == 70.0
    assert p["behaviour_summary"]["requests_with_masking_pct"] == 100.0


def test_user_list_summary_filters_and_sorting(client, h):
    _burst(client, h, user="user_009")
    post(client, h, "c1", payload("c1", minutes_ago(5), user="user_001"))
    r = client.get("/v1/users", params={"org_id": "org_001"}, headers=h["gateway"]).json()
    assert r["summary"] == {"total_users": 2, "P1": 1, "P2": 0, "P3": 0, "P4": 1, "blocked": 0}
    assert [u["user_id"] for u in r["users"]] == ["user_009", "user_001"]
    assert r["users"][0]["main_reason"]
    only = client.get(
        "/v1/users", params={"org_id": "org_001", "priority": "P1"}, headers=h["gateway"]
    ).json()
    assert [u["user_id"] for u in only["users"]] == ["user_009"]
    found = client.get(
        "/v1/users", params={"org_id": "org_001", "q": "001"}, headers=h["gateway"]
    ).json()
    assert [u["user_id"] for u in found["users"]] == ["user_001"]


def test_profiles_are_separate_per_organisation(client, h):
    post(client, h, "c2", block_event(minutes_ago(5), org="org_A", user="u1"))
    post(client, h, "c2", payload("c2", minutes_ago(5), org="org_B", user="u1"))
    a = client.get("/v1/users/u1/profile", params={"org_id": "org_A"}, headers=h["gateway"]).json()
    b = client.get("/v1/users/u1/profile", params={"org_id": "org_B"}, headers=h["gateway"]).json()
    assert a["score"] > 0 and b["score"] == 0


def test_c4_i01_block_then_blocklist_then_unblock(client, h, db):
    _burst(client, h)
    body = {"org_id": "org_001", "by": "admin_001", "reason": "Repeated attacks"}
    r = client.post("/v1/users/user_009/block", json=body, headers=h["gateway"])
    assert r.status_code == 200 and r.json()["status"] == "blocked"
    bl = client.get("/v1/blocklist", params={"org_id": "org_001"}, headers=h["c1"]).json()
    assert bl["user_ids"] == ["user_009"] and bl["org_id"] == "org_001"
    assert (
        client.get("/v1/blocklist", params={"org_id": "org_002"}, headers=h["c1"]).json()[
            "user_ids"
        ]
        == []
    )
    # blocking again changes nothing and is not logged twice
    client.post("/v1/users/user_009/block", json=body, headers=h["gateway"])
    assert db[col.ACTIONS].count_documents({}) == 1
    # a new event does not undo the admin decision
    post(client, h, "c1", payload("c1", minutes_ago(1), user="user_009"))
    assert (
        client.get(
            "/v1/users/user_009/profile", params={"org_id": "org_001"}, headers=h["gateway"]
        ).json()["status"]
        == "blocked"
    )
    r = client.post(
        "/v1/users/user_009/unblock", json={**body, "reason": "Reviewed"}, headers=h["gateway"]
    )
    assert r.json()["status"] == "active"
    assert (
        client.get("/v1/blocklist", params={"org_id": "org_001"}, headers=h["c1"]).json()[
            "user_ids"
        ]
        == []
    )
    log = client.get("/v1/admin-actions", params={"org_id": "org_001"}, headers=h["gateway"]).json()
    assert [a["action"] for a in log] == ["unblock", "block"] and log[1]["by"] == "admin_001"


def test_block_unknown_user_is_404_and_bad_body_is_422(client, h):
    body = {"org_id": "org_001", "by": "a", "reason": "r"}
    assert client.post("/v1/users/ghost/block", json=body, headers=h["gateway"]).status_code == 404
    assert (
        client.post(
            "/v1/users/ghost/block", json={"org_id": "org_001"}, headers=h["gateway"]
        ).status_code
        == 422
    )
    assert (
        client.get(
            "/v1/users/ghost/profile", params={"org_id": "org_001"}, headers=h["gateway"]
        ).status_code
        == 404
    )


def test_no_prompt_or_answer_text_is_stored(client, h, db):
    post(client, h, "c1", payload("c1", minutes_ago(3)))
    post(client, h, "c2", block_event(minutes_ago(2)))
    post(client, h, "c3", violation(minutes_ago(1), leaks=2))
    allowed = {
        "_id",
        "event_id",
        "org_id",
        "user_id",
        "request_id",
        "session_id",
        "event_timestamp",
        "source",
        "received_at",
        "masking_percentage",
        "c2_verdict",
        "c2_score",
        "c3_verdict",
        "clause_id",
        "leaked_pii_detected",
        "leaked_pii_count",
    }
    for doc in db[col.EVENTS].find({}):
        assert set(doc) <= allowed


def test_all_five_collections_are_created_with_the_planned_fields(client, h, db):
    _burst(client, h)
    client.post(
        "/v1/users/user_009/block",
        json={"org_id": "org_001", "by": "a", "reason": "r"},
        headers=h["gateway"],
    )
    assert {col.EVENTS, col.HITS, col.FEATURES, col.PROFILES, col.ACTIONS} <= set(
        db.list_collection_names()
    )
    feat = db[col.FEATURES].find_one({"user_id": "user_009"})
    assert {"hour_histogram", "sessions", "counters", "masking"} <= set(feat)
    assert len(feat["hour_histogram"]) == 24
    prof = db[col.PROFILES].find_one({"user_id": "user_009"})
    assert {
        "score",
        "priority",
        "recommendation",
        "status",
        "top_contributors",
        "behaviour_summary",
        "first_seen",
        "last_activity",
        "updated_at",
        "ruleset_version",
    } <= set(prof)
    assert iso(now())[:10] == iso(now())[:10]
