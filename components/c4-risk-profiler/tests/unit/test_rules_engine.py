from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

from c4.core.classify import priority_for
from c4.core.decay import decay_factor, score_at
from c4.core.rules import replay
from c4.core.ruleset import parse_ruleset

from tests.helpers import block_event, now, payload, to_internal, violation

T0 = now() - timedelta(days=2)


def ev(p, source):
    return to_internal(p, source)


def hit_ids(hits):
    return sorted(h["rule_id"] for h in hits)


def test_c4_u03_pass_and_clean_add_zero_points(rs):
    events = [
        ev(payload("c1", T0, req="r1", masking_percentage=63.0), "c1"),
        ev(payload("c2", T0, req="r1"), "c2"),
        ev(payload("c3", T0, req="r1"), "c3"),
    ]
    hits = replay(events, rs)
    assert hits == []
    assert score_at(hits, T0, rs) == 0


def test_r1_only_for_plain_block(rs):
    hits = replay([ev(block_event(T0, c2_score=0.5), "c2")], rs)
    assert hit_ids(hits) == ["R1"]


def test_c4_u04_leak_heavy_violation_is_r4_r5_r2_raw_50(rs):
    hits = replay([ev(violation(T0, leaks=3), "c3")], rs)
    assert hit_ids(hits) == ["R2", "R4", "R5"]
    assert sum(h["impact"] for h in hits) == 50


def test_r2_from_high_c2_score(rs):
    hits = replay([ev(block_event(T0, c2_score=0.95), "c2")], rs)
    assert hit_ids(hits) == ["R1", "R2"]


def test_c4_u05_decay_halves_points_after_one_half_life(rs):
    assert abs(decay_factor(rs.half_life_hours, rs.half_life_hours) - 0.5) < 1e-9
    hit = {"impact": 20.0, "event_timestamp": T0}
    later = T0 + timedelta(hours=rs.half_life_hours)
    assert abs(score_at([hit], later, rs) - 10.0) < 1e-9


def test_score_is_bounded_to_100(rs):
    hits = [{"impact": 20.0, "event_timestamp": T0}] * 20
    assert score_at(hits, T0, rs) == 100.0


def test_old_hits_outside_window_are_ignored(rs):
    hit = {"impact": 20.0, "event_timestamp": T0 - timedelta(days=rs.window_days + 1)}
    assert score_at([hit], T0, rs) == 0


def test_r3_fires_on_third_suspicious_event_once_per_window(rs):
    t = [T0 + timedelta(minutes=m) for m in (0, 5, 10, 15)]
    events = [ev(block_event(x), "c2") for x in t]
    hits = replay(events, rs)
    r3 = [h for h in hits if h["rule_id"] == "R3"]
    assert len(r3) == 1 and r3[0]["event_timestamp"] == t[2]


def test_r3_does_not_fire_when_events_are_spread_out(rs):
    events = [ev(block_event(T0 + timedelta(hours=3 * i)), "c2") for i in range(4)]
    assert "R3" not in hit_ids(replay(events, rs))


def test_worked_example_raw_40_with_two_event_window(rs):
    """Proposal Appendix C: suspicious C2 (+10), repeated suspicious (+15), C3 violation (+15).

    With r3.count = 3 (the blueprint default) that exact sequence cannot fire R3, so this test
    uses count = 2 to show the engine reproduces the raw 40. See the note in README.
    """
    data = {
        "ruleset_version": "t",
        "half_life_hours": rs.half_life_hours,
        "window_days": rs.window_days,
        "rules": {**rs.rules, "R3": {**rs.rules["R3"], "count": 2}},
        "priorities": rs.priorities,
    }
    rs2 = parse_ruleset(data)
    events = [ev(block_event(T0), "c2"), ev(violation(T0 + timedelta(minutes=5)), "c3")]
    hits = replay(events, rs2)
    assert hit_ids(hits) == ["R1", "R3", "R4"]
    assert sum(h["impact"] for h in hits) == 40


def test_c4_u06_same_events_same_score_in_any_order(rs):
    events = [ev(block_event(T0 + timedelta(minutes=i), c2_score=0.95), "c2") for i in range(4)] + [
        ev(violation(T0 + timedelta(minutes=9), leaks=3), "c3")
    ]
    a = replay(events, rs)
    b = replay(list(reversed(events)), rs)
    assert a == b
    assert replay(events, rs) == a


def _normal_requests(n):
    hour_start = T0.replace(hour=9, minute=0)
    return [
        ev(payload("c1", hour_start + timedelta(days=-(i % 5), minutes=i), req=f"n{i}"), "c1")
        for i in range(n)
    ]


def test_c4_u07_r6_does_not_fire_before_20_requests(rs):
    night = T0.replace(hour=2, minute=0)
    events = _normal_requests(10) + [ev(block_event(night), "c2")]
    assert "R6" not in hit_ids(replay(events, rs))


def test_r6_fires_for_off_hours_suspicious_event_after_enough_history(rs):
    night = T0.replace(hour=2, minute=0)
    events = _normal_requests(25) + [ev(block_event(night), "c2")]
    assert "R6" in hit_ids(replay(events, rs))


def test_r6_does_not_fire_at_a_normal_hour(rs):
    normal = T0.replace(hour=9, minute=30)
    events = _normal_requests(25) + [ev(block_event(normal), "c2")]
    assert "R6" not in hit_ids(replay(events, rs))


def test_r7_fires_when_suspicious_count_rises_over_three_sessions(rs):
    d = T0
    events = [
        ev(payload("c1", d, sess="s1", req="a1"), "c1"),  # session 1: 0 suspicious
        ev(block_event(d + timedelta(days=1), sess="s2"), "c2"),  # session 2: 1
        ev(block_event(d + timedelta(days=2), sess="s3"), "c2"),  # session 3: 1 (not rising yet)
        ev(block_event(d + timedelta(days=2, hours=3), sess="s3"), "c2"),  # session 3: 2 -> rises
    ]
    hits = replay(events, rs)
    r7 = [h for h in hits if h["rule_id"] == "R7"]
    assert len(r7) == 1 and r7[0]["session_id"] == "s3"


def test_r7_does_not_fire_with_flat_counts(rs):
    d = T0
    events = [ev(block_event(d + timedelta(days=i), sess=f"s{i}"), "c2") for i in range(3)]
    assert "R7" not in hit_ids(replay(events, rs))


def test_running_score_after_each_hit_is_stored(rs):
    hits = replay([ev(violation(T0, leaks=3), "c3")], rs)
    assert all(h["score_after"] == 50.0 for h in hits)


def test_priority_boundaries(rs):
    assert [priority_for(s, rs) for s in (0, 29.9, 30, 59.9, 60, 79.9, 80, 100)] == [
        "P4",
        "P4",
        "P3",
        "P3",
        "P2",
        "P2",
        "P1",
        "P1",
    ]


def test_bad_rules_file_is_rejected(rs):
    import pytest

    with pytest.raises(ValueError):
        parse_ruleset(
            {
                "ruleset_version": "x",
                "half_life_hours": 0,
                "window_days": 30,
                "rules": rs.rules,
                "priorities": rs.priorities,
            }
        )
    with pytest.raises(ValueError):
        parse_ruleset(
            {
                "ruleset_version": "x",
                "half_life_hours": 72,
                "window_days": 30,
                "rules": {"R1": {"impact": 1}},
                "priorities": rs.priorities,
            }
        )
    assert replace(rs, version="y").version == "y"
