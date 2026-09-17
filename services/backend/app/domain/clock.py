"""An injectable clock so domain services never call ``datetime.now()`` or
``date.today()`` directly.

Tests monkeypatch ``today_in``/``utcnow`` (or the call sites that use them)
to pin "now" instead of depending on wall-clock time.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo


def utcnow() -> datetime:
    """The current instant, timezone-aware in UTC."""
    return datetime.now(tz=UTC)


def today_in(tz: str) -> date:
    """Today's calendar date in the IANA timezone ``tz``."""
    return datetime.now(tz=ZoneInfo(tz)).date()
