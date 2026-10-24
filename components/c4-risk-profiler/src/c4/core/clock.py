"""The clock C4 uses everywhere ("now").

Normally it is the real time. The dashboard's "Move time forward" button can add hours to it,
so the panel can watch scores fade without waiting days. This only changes what "now" means
for calculations. No stored event or profile is touched, and "Back to now" undoes it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

MAX_OFFSET_HOURS = 24 * 90  # three months is far enough for any demo

_offset_hours = 0.0


def now_utc() -> datetime:
    """Current time in UTC, without microseconds (every other time in C4 is whole seconds)."""
    real = datetime.now(UTC).replace(microsecond=0)
    return real + timedelta(hours=_offset_hours)


def offset_hours() -> float:
    return _offset_hours


def move_forward(hours: float) -> float:
    """Add `hours` to the clock (never beyond MAX_OFFSET_HOURS). Returns the new offset."""
    global _offset_hours
    _offset_hours = min(MAX_OFFSET_HOURS, max(0.0, _offset_hours + hours))
    return _offset_hours


def reset() -> None:
    """Back to the real time."""
    global _offset_hours
    _offset_hours = 0.0
