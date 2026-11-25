"""A fake C4 for tests: C1's block-list fetch and event POSTs are routed into the real C4 stub.

So every event C1 sends is validated by stubs/c4_stub.py against contracts/schemas/c1_event,
exactly as when the stub runs on port 8004, but without opening a network port.
Set `blocklist_down` or `events_down` to True to simulate a C4 outage.
"""

import json

from fastapi.testclient import TestClient
from helpers import make_client

from c1 import common
from c1.gate.blocklist import BlocklistCache, BlocklistFetchError
from stubs import c4_stub

C1_KEY_HEADER = {"X-Service-Key": "test-c1-key"}


class FakeClock:
    """A clock the test moves by hand, in seconds."""

    def __init__(self):
        self.now = 1000.0

    # Return the current fake time
    def __call__(self) -> float:
        return self.now


class FakeC4:
    """Stands in for C4 and records every event it accepted."""

    # Write the block list file the stub reads, and route C1's event sender to the stub
    def __init__(self, monkeypatch, folder, blocked: dict):
        blocklist_file = folder / "blocklist.json"
        blocklist_file.write_text(json.dumps(blocked))
        monkeypatch.setattr(c4_stub, "BLOCKLIST_FILE", blocklist_file)
        c4_stub.seen_event_ids.clear()
        self.stub = TestClient(c4_stub.app)
        self.blocklist_down = False
        self.events_down = False
        self.events = []
        monkeypatch.setattr(common, "post_event", self.post_event)

    # C1's block-list fetch, answered by the stub (or failing like an outage)
    def fetch(self, org_id: str) -> dict:
        if self.blocklist_down:
            raise BlocklistFetchError("C4 is down")
        return self.stub.get(
            "/v1/blocklist", params={"org_id": org_id}, headers=C1_KEY_HEADER
        ).json()

    # C1's event POST, answered by the stub; None means "C4 unreachable"
    def post_event(self, c4_url, service_key, event, timeout=2.0):
        if self.events_down:
            return None
        reply = self.stub.post("/v1/events/c1", json=event, headers={"X-Service-Key": service_key})
        if reply.status_code in (200, 202):
            self.events.append(event)
        return reply.status_code


# A C1 test client wired to a fake C4 and a hand-moved clock
def c1_with_fake_c4(monkeypatch, folder, blocked: dict):
    c4, clock = FakeC4(monkeypatch, folder, blocked), FakeClock()
    client = make_client(folder)
    client.app.state.blocklist = BlocklistCache("http://c4.test", "test-c1-key", 30, 300,
                                                fetch=c4.fetch, clock=clock)  # fmt: skip
    return client, c4, clock
