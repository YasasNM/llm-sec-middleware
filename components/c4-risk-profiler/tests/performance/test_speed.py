"""C4-P-01 (small version): profile update must finish within 2 s (NFR-02).

The planned full run replays 10,000 events; this quick one uses 400 events so that it also
runs on a laptop with the in-memory test database. Real MongoDB is faster.
"""

from __future__ import annotations

import time
from datetime import timedelta

from tests.helpers import block_event, minutes_ago, payload


def test_p95_event_processing_under_2_seconds(client, h):
    times = []
    base = minutes_ago(600)
    for i in range(400):
        user = f"user_{i % 20:03d}"
        t = base + timedelta(seconds=30 * i)
        body = block_event(t, user=user) if i % 7 == 0 else payload("c1", t, user=user)
        src = "c2" if i % 7 == 0 else "c1"
        t0 = time.perf_counter()
        assert client.post(f"/v1/events/{src}", json=body, headers=h[src]).status_code == 202
        times.append(time.perf_counter() - t0)
    times.sort()
    assert times[int(0.95 * len(times))] <= 2.0
