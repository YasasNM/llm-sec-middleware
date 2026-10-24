"""Dummy data: the 7 behaviour scenarios (proposal 3.5) as agreed C1/C2/C3 events.

Every scenario is built from a seed, so the same seed always gives the same events
(reproducible). Events end shortly before `end` (so none is in the future).
Only agreed fields are produced (contracts/schemas): no prompt or answer text exists here.
"""

from __future__ import annotations

import random
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

# Normal working hours in UTC. 04:00-10:59 UTC is 09:30-16:30 in Sri Lanka (UTC+5:30).
NORMAL_HOURS = range(4, 11)  # 04:00-10:59 UTC = 09:30-16:30 in Sri Lanka


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


class Builder:
    """Creates the events of one user. One request = one C1 event, then C2, then C3."""

    def __init__(self, org_id: str, user_id: str, seed: int) -> None:
        self.org, self.user = org_id, user_id
        # A random generator that starts from the seed: same seed, same events
        self.rng = random.Random(f"{seed}:{org_id}:{user_id}")
        self.events: list[dict] = []
        self._req = 0

    def _id(self, source: str) -> str:
        return f"evt-{source}-{uuid.UUID(int=self.rng.getrandbits(128))}"

    def _event(self, source: str, ts: datetime, req: str, sess: str, **fields) -> None:
        self.events.append(
            {
                "source": source,
                "payload": {
                    "event_id": self._id(source),
                    "org_id": self.org,
                    "user_id": self.user,
                    "request_id": req,
                    "session_id": sess,
                    "event_timestamp": _iso(ts),
                    **fields,
                },
            }
        )

    def request(self, ts, sess, masking=0.0, c2=("PASS", None), c3=None) -> None:
        """c2 = (verdict, score). c3 = None or (verdict, clause_id, leaks)."""
        self._req += 1  # every request gets its own number
        req = f"req_{ts:%Y%m%d}_{self._req:06d}"
        # C1 always reports (how much of the prompt it masked)
        self._event("c1", ts, req, sess, masking_percentage=round(masking, 1))
        # C2 always reports (did the prompt look like an attack?)
        verdict, score = c2
        if score is None:
            score = round(self.rng.uniform(0.01, 0.25), 2)
        self._event("c2", ts + timedelta(seconds=1), req, sess, c2_verdict=verdict, c2_score=score)
        if verdict == "BLOCK":
            return  # C2 blocked: the request never reaches C3
        # C3 only sees requests that C2 let through
        v, clause, leaks = c3 or ("CLEAN", None, 0)
        self._event(
            "c3",
            ts + timedelta(seconds=3),
            req,
            sess,
            c3_verdict=v,
            clause_id=clause,
            leaked_pii_detected=leaks > 0,
            leaked_pii_count=leaks,
        )

    def blocked(self, ts, sess, score, masking=5.0) -> None:
        self.request(ts, sess, masking, c2=("BLOCK", score))

    def violation(self, ts, sess, leaks, masking=5.0) -> None:
        self.request(ts, sess, masking, c3=("VIOLATION", "POL-PHI-002", leaks))

    def normal_days(self, end: datetime, days: int, per_day, masking=(0, 12), first_day=0):
        """Normal working-hours requests on each of the last `days` days (one session a day)."""
        for d in range(days, first_day, -1):
            day = (end - timedelta(days=d)).replace(hour=0, minute=0, second=0)
            sess = f"sess_{self.user}_{day:%m%d}"
            n = self.rng.randint(*per_day) if isinstance(per_day, tuple) else per_day
            hours = sorted(
                self.rng.choice(NORMAL_HOURS) * 60 + self.rng.randint(0, 59) for _ in range(n)
            )
            for m in hours:
                self.request(day + timedelta(minutes=m), sess, self.rng.uniform(*masking))

    def result(self) -> list[dict]:
        return sorted(self.events, key=lambda e: (e["payload"]["event_timestamp"], e["source"]))


def _day(end: datetime, days_ago: int, hour: int, minute: int = 0) -> datetime:
    return (end - timedelta(days=days_ago)).replace(hour=hour, minute=minute, second=0)


def benign_clinician(b: Builder, end: datetime) -> None:
    """Normal work for two weeks, a little personal data masked. Should stay at score 0."""
    b.normal_days(end, 14, (4, 8), masking=(0, 15))


def careless_power_user(b: Builder, end: datetime) -> None:
    """Lots of requests with a lot of masking, but nothing hostile. Heavy use is not a threat."""
    b.normal_days(end, 14, (8, 14), masking=(30, 75))


def curious_tester(b: Builder, end: datetime) -> None:
    """Normal work, then one afternoon of four blocked prompts. A small, short-lived blip."""
    b.normal_days(end, 10, (3, 5))
    sess = f"sess_{b.user}_probe"
    for i, score in enumerate((0.7, 0.95, 0.6, 0.8)):
        b.blocked(_day(end, 4, 7, 0) + timedelta(minutes=20 * i), sess, score)


def gradual_escalator(b: Builder, end: datetime) -> None:
    """Suspicious events grow day by day. This is what rule R7 (escalation) is made to find."""
    b.normal_days(end, 8, 5)
    plan = {6: (0, 0), 5: (0, 0), 4: (1, 0), 3: (1, 0), 2: (2, 0), 1: (2, 1), 0: (3, 1)}
    for days_ago, (blocks, violations) in plan.items():
        sess = f"sess_{b.user}_esc{days_ago}"
        t = _day(end, days_ago, 8, 0) if days_ago else end - timedelta(hours=3)
        for i in range(blocks):
            b.blocked(t + timedelta(hours=i), sess, 0.7 + 0.08 * i)
        for i in range(violations):
            b.violation(t + timedelta(hours=blocks + i), sess, leaks=1 + days_ago % 2)


def burst_attacker(b: Builder, end: datetime) -> None:
    """Six blocked prompts and two leaking answers within 40 minutes. Should reach P1 fast."""
    b.normal_days(end, 5, 5)
    sess = f"sess_{b.user}_burst"
    start = end - timedelta(minutes=40)
    for i in range(6):
        b.blocked(start + timedelta(minutes=3 * i), sess, 0.9 + 0.01 * i)
    for i in range(2):
        b.violation(start + timedelta(minutes=19 + 2 * i), sess, leaks=3)


def slow_and_low(b: Builder, end: datetime) -> None:
    """One blocked prompt every three days for a month. Each one is harmless alone, but a fixed
    event counter would reach 100 because it never forgets. Time decay keeps the score low."""
    b.normal_days(end, 30, 5)
    for k, days_ago in enumerate(range(28, 0, -3)):
        b.blocked(_day(end, days_ago, 6, 30), f"sess_{b.user}_slow{k}", 0.55)


def compromised_account(b: Builder, end: datetime) -> None:
    """Twenty normal days, then a sudden attack in the evening, an hour this user never works.
    Rule R6 (unusual time) should fire."""
    b.normal_days(end, 20, 5)
    when = end - timedelta(days=1) if end.hour < 21 else end
    start = when.replace(hour=20, minute=0, second=0)
    sess = f"sess_{b.user}_takeover"
    for i in range(3):
        b.blocked(start + timedelta(minutes=4 * i), sess, 0.93)
    for i in range(2):
        b.violation(start + timedelta(minutes=14 + 3 * i), sess, leaks=2)


# The seven scenarios by name. user_001 is the first one, user_002 the second, and so on.
SCENARIOS: dict[str, Callable[[Builder, datetime], None]] = {
    "benign_clinician": benign_clinician,
    "careless_power_user": careless_power_user,
    "curious_tester": curious_tester,
    "gradual_escalator": gradual_escalator,
    "burst_attacker": burst_attacker,
    "slow_and_low": slow_and_low,
    "compromised_account": compromised_account,
}

USER_FOR_SCENARIO = {name: f"user_{i:03d}" for i, name in enumerate(SCENARIOS, start=1)}


def build_scenario(
    name: str,
    user_id: str | None = None,
    org_id: str = "org_001",
    seed: int = 42,
    end: datetime | None = None,
) -> list[dict]:
    """Return [{"source": "c1"|"c2"|"c3", "payload": {...}}, ...] sorted by time."""
    end = (end or datetime.now(UTC)).replace(microsecond=0) - timedelta(minutes=5)
    b = Builder(org_id, user_id or USER_FOR_SCENARIO[name], seed)
    SCENARIOS[name](b, end)
    return b.result()
