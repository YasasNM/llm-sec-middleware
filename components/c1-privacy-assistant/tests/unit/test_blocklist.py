"""Unit tests for the M1 block-list gate: 30 s cache, 300 s stale limit, fail closed."""

import pytest

from c1.gate.blocklist import (
    BlocklistCache,
    BlocklistFetchError,
    BlocklistUnavailable,
    fetch_from_c4,
    parse_blocklist,
)

GOOD = {"org_id": "org_001", "user_ids": ["user_017"], "generated_at": "2026-10-23T07:10:00Z"}


class FakeClock:
    """A clock the test moves by hand, in seconds."""

    def __init__(self):
        self.now = 1000.0

    # Return the current fake time
    def __call__(self) -> float:
        return self.now


class FakeC4:
    """Answers block-list requests with GOOD, or raises when `down` is True. Counts the calls."""

    def __init__(self):
        self.down = False
        self.calls = 0

    # Answer one fetch like C4 would (or fail like a C4 outage)
    def __call__(self, org_id: str) -> dict:
        self.calls += 1
        if self.down:
            raise BlocklistFetchError("C4 is down")
        return dict(GOOD, org_id=org_id)


@pytest.fixture()
def setup():
    clock, c4 = FakeClock(), FakeC4()
    cache = BlocklistCache("http://c4.test", "key", 30, 300, fetch=c4, clock=clock)
    return cache, c4, clock


def test_blocked_and_allowed_users(setup):
    cache, _, _ = setup
    assert cache.is_blocked("org_001", "user_017") is True
    assert cache.is_blocked("org_001", "user_001") is False


def test_list_is_reused_for_30_seconds(setup):
    cache, c4, clock = setup
    cache.is_blocked("org_001", "user_001")
    clock.now += 30
    cache.is_blocked("org_001", "user_001")
    assert c4.calls == 1


def test_list_is_fetched_again_after_30_seconds(setup):
    cache, c4, clock = setup
    cache.is_blocked("org_001", "user_001")
    clock.now += 31
    cache.is_blocked("org_001", "user_001")
    assert c4.calls == 2


def test_each_org_has_its_own_cache(setup):
    cache, c4, _ = setup
    cache.is_blocked("org_001", "user_001")
    cache.is_blocked("org_002", "user_001")
    assert c4.calls == 2


def test_c4_down_uses_stale_list_up_to_300_seconds(setup):
    cache, c4, clock = setup
    cache.is_blocked("org_001", "user_001")
    c4.down = True
    clock.now += 300
    assert cache.is_blocked("org_001", "user_017") is True


def test_c4_down_and_list_older_than_300_seconds_fails_closed(setup):
    cache, c4, clock = setup
    cache.is_blocked("org_001", "user_001")
    c4.down = True
    clock.now += 301
    with pytest.raises(BlocklistUnavailable):
        cache.is_blocked("org_001", "user_001")


def test_c4_down_and_never_loaded_fails_closed(setup):
    cache, c4, _ = setup
    c4.down = True
    with pytest.raises(BlocklistUnavailable):
        cache.is_blocked("org_001", "user_001")


def test_good_answer_after_outage_refreshes_the_cache(setup):
    cache, c4, clock = setup
    cache.is_blocked("org_001", "user_001")
    c4.down = True
    clock.now += 200
    cache.is_blocked("org_001", "user_001")  # stale copy used
    c4.down = False
    cache.is_blocked("org_001", "user_001")  # fresh copy fetched
    assert cache.oldest_age() == 0


def test_oldest_age_for_health(setup):
    cache, _, clock = setup
    assert cache.oldest_age() is None
    cache.is_blocked("org_001", "user_001")
    clock.now += 12.34
    assert cache.oldest_age() == 12.3


@pytest.mark.parametrize(
    "payload",
    [
        {"org_id": "org_001", "user_ids": ["user_017"]},  # generated_at missing
        dict(GOOD, generated_at="2026-10-23 07:10:00"),  # no T and no Z
        dict(GOOD, user_ids=["user 017"]),  # space is not allowed in ids
        dict(GOOD, user_ids=["user_017", "user_017"]),  # repeated ids
        dict(GOOD, user_ids="user_017"),  # not a list
        dict(GOOD, org_id="org_999"),  # answer for another org
        "not json object",
    ],
)
def test_answers_that_break_the_contract_are_rejected(payload):
    with pytest.raises(BlocklistFetchError):
        parse_blocklist(payload, "org_001")


def test_contract_breaking_answer_counts_as_c4_failure(setup):
    cache, _, _ = setup
    cache.fetch = lambda org_id: {"org_id": org_id}
    with pytest.raises(BlocklistUnavailable):
        cache.is_blocked("org_001", "user_001")


def test_unreachable_c4_raises_fetch_error():
    with pytest.raises(BlocklistFetchError):
        fetch_from_c4("http://127.0.0.1:9", "key", "org_001")
