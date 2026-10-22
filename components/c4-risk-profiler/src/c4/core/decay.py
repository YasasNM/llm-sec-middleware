"""M5: time decay. Older evidence counts less (exponential, half-life from rules.yaml)."""

from __future__ import annotations

import math
from collections.abc import Iterable
from datetime import datetime, timedelta

from .ruleset import Ruleset


def decay_factor(age_hours: float, half_life_hours: float) -> float:
    """1.0 for new evidence, 0.5 after one half-life. Future evidence counts fully."""
    if age_hours <= 0:
        return 1.0
    return math.exp(-(math.log(2) / half_life_hours) * age_hours)


def decayed_points(impact: float, hit_time: datetime, now: datetime, rs: Ruleset) -> float:
    age_hours = (now - hit_time).total_seconds() / 3600.0
    return impact * decay_factor(age_hours, rs.half_life_hours)


def score_at(hits: Iterable[dict], now: datetime, rs: Ruleset) -> float:
    """Score at time `now` from rule hits {impact, event_timestamp}. Bounded to 0..100 (M6)."""
    cutoff = now - timedelta(days=rs.window_days)
    total = 0.0
    for h in hits:
        t = h["event_timestamp"]
        if t > now or t < cutoff:
            continue
        total += decayed_points(h["impact"], t, now, rs)
    return min(100.0, total)
