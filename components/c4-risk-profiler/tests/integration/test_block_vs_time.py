"""A block is a human decision: moving time forward fades the score but never unblocks."""

from __future__ import annotations


def test_blocked_user_stays_blocked_while_score_fades(client):
    client.post("/ui/api/demo/load")
    users = {u["user_id"]: u for u in client.get("/ui/api/overview").json()["users"]}
    p1 = [u for u in users.values() if u["priority"] == "P1"]
    target = p1[0]["user_id"]
    before = users[target]["score"]
    client.post(f"/ui/api/users/{target}/block", json={"by": "admin", "reason": "viva demo"})
    client.post("/ui/api/demo/time", json={"add_hours": 24 * 7})
    after = {u["user_id"]: u for u in client.get("/ui/api/overview").json()["users"]}[target]
    assert after["status"] == "blocked"
    assert after["score"] < before
    gate = client.post("/ui/api/gate", json={"user_id": target})
    assert gate.status_code == 403
