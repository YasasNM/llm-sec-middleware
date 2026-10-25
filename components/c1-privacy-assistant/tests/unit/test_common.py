"""Unit tests for c1.common (service-key check, event sender, outbox)."""

import re

import pytest

from c1 import common

NO_WAIT = (0, 0, 0)


def test_parse_accepted_keys():
    assert common.parse_accepted_keys("gateway:abc, c1:def") == {"gateway": "abc", "c1": "def"}


def test_parse_accepted_keys_ignores_broken_pairs():
    assert common.parse_accepted_keys("gateway:abc,junk,:x,c2:") == {"gateway": "abc"}


@pytest.mark.parametrize(
    "key, expected", [("abc", "gateway"), ("wrong", None), ("", None), (None, None)]
)
def test_caller_for_key(key, expected):
    assert common.caller_for_key(key, {"gateway": "abc"}) == expected


def test_event_id_format_and_unique():
    first, second = common.new_event_id(), common.new_event_id()
    assert first.startswith("evt-c1-") and first != second


def test_timestamp_ends_in_z():
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", common.utc_now_iso())


# Build a sample event for the sender tests
def sample_event() -> dict:
    return common.build_c1_event("org_001", "user_001", "req_20261023_000001", "sess_dev", 12.5)


# Replace post_event with a fake that returns the given statuses in order
def fake_c4(monkeypatch, statuses: list) -> list:
    calls = []

    def fake_post(c4_url, service_key, event, timeout=2.0):
        calls.append(event["event_id"])
        return statuses[min(len(calls) - 1, len(statuses) - 1)]

    monkeypatch.setattr(common, "post_event", fake_post)
    return calls


def test_send_event_delivered_first_time(monkeypatch, tmp_path):
    calls = fake_c4(monkeypatch, [202])
    outbox = str(tmp_path / "c1.db")
    assert common.send_event("http://c4", "k", sample_event(), outbox, NO_WAIT) is True
    assert len(calls) == 1 and common.load_outbox(outbox) == []


def test_send_event_c4_down_goes_to_outbox(monkeypatch, tmp_path):
    calls = fake_c4(monkeypatch, [None])
    outbox = str(tmp_path / "c1.db")
    event = sample_event()
    assert common.send_event("http://c4", "k", event, outbox, NO_WAIT) is False
    assert len(calls) == 4  # first try + 3 retries
    assert common.load_outbox(outbox) == [event]


def test_send_event_rejected_is_not_retried_or_stored(monkeypatch, tmp_path):
    calls = fake_c4(monkeypatch, [422])
    outbox = str(tmp_path / "c1.db")
    assert common.send_event("http://c4", "k", sample_event(), outbox, NO_WAIT) is False
    assert len(calls) == 1 and common.load_outbox(outbox) == []


def test_resend_outbox_delivers_and_empties(monkeypatch, tmp_path):
    outbox = str(tmp_path / "c1.db")
    common.save_to_outbox(outbox, sample_event())
    common.save_to_outbox(outbox, sample_event())
    fake_c4(monkeypatch, [202])
    assert common.resend_outbox("http://c4", "k", outbox) == 2
    assert common.load_outbox(outbox) == []


def test_resend_outbox_keeps_events_while_c4_is_down(monkeypatch, tmp_path):
    outbox = str(tmp_path / "c1.db")
    common.save_to_outbox(outbox, sample_event())
    fake_c4(monkeypatch, [None])
    assert common.resend_outbox("http://c4", "k", outbox) == 0
    assert len(common.load_outbox(outbox)) == 1


def test_post_event_returns_none_when_c4_unreachable():
    # Port 9 on localhost has nothing listening, so the connection is refused at once
    assert common.post_event("http://127.0.0.1:9", "k", sample_event(), timeout=0.5) is None
