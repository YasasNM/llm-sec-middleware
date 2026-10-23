"""Security test C1-S-01 for Step 6: no PHI in the decision log, the outbox or any log line.

A name and a phone number are planted in prompts that go through every /v1/check path: allowed,
blocked, C4 down at event time (so events wait in the outbox) and block list unavailable (503).
Then the raw SQLite file, its rows, every captured log line and the stub's printed output are
searched. None may contain the planted values.
"""

import logging

import pytest
from fake_c4 import c1_with_fake_c4
from helpers import GATEWAY_HEADERS

from c1 import common
from c1.log.decision_log import read_decisions

PLANTED_NAME = "Tharindu Ekanayake"
PLANTED_PHONE = "0771234567"
SECRETS = [PLANTED_NAME, "Tharindu", "Ekanayake", PLANTED_PHONE, "077 123 4567", "1234567"]
PROMPT = f"Patient {PLANTED_NAME}, phone {PLANTED_PHONE} (also 077 123 4567), fever for 3 days"


# Send one /v1/check with the planted prompt
def check(client, request_id: str, user_id: str = "user_001"):
    body = {"request_id": request_id, "org_id": "org_001", "user_id": user_id,
            "session_id": "sess_sec", "text": PROMPT}  # fmt: skip
    return client.post("/v1/check", json=body, headers=GATEWAY_HEADERS)


# Fail the test if any planted value appears in the given text
def assert_no_secrets(where: str, text: str) -> None:
    for secret in SECRETS:
        assert secret not in text, f"planted value found in {where}"


@pytest.fixture()
def every_path_run(monkeypatch, tmp_path, caplog, capsys):
    caplog.set_level(logging.DEBUG)
    client, c4, clock = c1_with_fake_c4(monkeypatch, tmp_path, {"org_001": ["user_017"]})
    assert check(client, "req_sec_1").json()["decision"] == "ALLOW"
    assert check(client, "req_sec_2", user_id="user_017").json()["decision"] == "ACCESS_BLOCKED"
    c4.events_down = True  # events now wait in the outbox
    check(client, "req_sec_3")
    c4.blocklist_down, clock.now = True, clock.now + 301
    assert check(client, "req_sec_4").status_code == 503
    # Collect the log lines and the stub's printed lines here, while they belong to this run
    return client, c4, caplog.text, capsys.readouterr().out


def test_masked_answer_to_the_gateway_has_no_phi(monkeypatch, tmp_path):
    client, _, _ = c1_with_fake_c4(monkeypatch, tmp_path, {})
    reply = check(client, "req_sec_9")
    assert reply.json()["masking_percentage"] > 0
    assert_no_secrets("masked answer", reply.text)


def test_raw_database_file_has_no_phi(every_path_run):
    client = every_path_run[0]
    raw = client.app.state.settings.outbox_path.read_bytes().decode("utf-8", errors="ignore")
    assert_no_secrets("c1.db file", raw)


def test_decision_log_rows_have_no_phi(every_path_run):
    client = every_path_run[0]
    rows = read_decisions(str(client.app.state.settings.outbox_path))
    assert [r["decision"] for r in rows] == [
        "ALLOW",
        "ACCESS_BLOCKED",
        "ALLOW",
        "BLOCKLIST_UNAVAILABLE",
    ]
    assert_no_secrets("decision log", repr(rows))


def test_outbox_has_no_phi(every_path_run):
    client = every_path_run[0]
    waiting = common.load_outbox(str(client.app.state.settings.outbox_path))
    assert [e["request_id"] for e in waiting] == ["req_sec_3"]
    assert_no_secrets("outbox", repr(waiting))


def test_events_sent_to_c4_have_no_phi(every_path_run):
    c4 = every_path_run[1]
    assert len(c4.events) == 2
    assert_no_secrets("events", repr(c4.events))


def test_server_log_lines_have_no_phi(every_path_run):
    log_lines = every_path_run[2]
    assert "req_sec_1" in log_lines and "BLOCKLIST_UNAVAILABLE" in log_lines  # logging worked
    assert_no_secrets("log lines", log_lines)


def test_stub_output_has_no_phi(every_path_run):
    printed = every_path_run[3]
    assert "event_id=evt-c1-" in printed
    assert_no_secrets("stub output", printed)
