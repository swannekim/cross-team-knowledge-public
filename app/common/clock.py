"""Time helpers. All timestamps are timezone-aware UTC and serialised as ISO-8601 with a trailing 'Z'."""
from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone

UTC = timezone.utc


def parse_iso(value: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"not an ISO-8601 timestamp: {value!r}")
    text = value.strip()
    if text[-1] in "Zz":
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        raise ValueError(f"timestamp must carry a UTC designator: {value!r}")
    return parsed.astimezone(UTC)


def to_iso(moment: datetime) -> str:
    if moment.tzinfo is None:
        raise ValueError("naive datetime")
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class FixedClock:
    """Deterministic, manually advanced clock for tests and the benchmark."""

    def __init__(self, start="2026-10-07T01:00:00Z"):
        self._now = parse_iso(start) if isinstance(start, str) else start.astimezone(UTC)
        self._lock = threading.Lock()

    def now(self) -> datetime:
        with self._lock:
            return self._now

    def advance(self, *, seconds: float = 0, minutes: float = 0, hours: float = 0, days: float = 0) -> datetime:
        with self._lock:
            self._now += timedelta(seconds=seconds, minutes=minutes, hours=hours, days=days)
            return self._now

    def set(self, value) -> None:
        with self._lock:
            self._now = parse_iso(value) if isinstance(value, str) else value.astimezone(UTC)
