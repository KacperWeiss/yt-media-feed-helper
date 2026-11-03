"""Time: `now_utc()` (frozen via YTFEED_NOW in tests) and parsing of `7d`/`24h` windows."""
from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone

from .errors import ToolError


def now_utc() -> datetime:
    frozen = os.environ.get("YTFEED_NOW", "").strip()
    if frozen:
        return parse_iso(frozen)
    return datetime.now(timezone.utc)


def parse_iso(s: str) -> datetime:
    s = s.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


_REL = re.compile(r"^\s*(\d+)\s*(d|h|w|min)\s*$", re.I)


def parse_since(value: str | None, default_days: int | None = None) -> datetime | None:
    """`"7d"`, `"24h"`, `"2w"`, `"30min"` (minutes) or ISO 8601 → UTC datetime. `"30m"` is ambiguous (Q-21) → error."""
    if value is None or value == "":
        return now_utc() - timedelta(days=default_days) if default_days else None
    m = _REL.match(value)
    if m:
        n, unit = int(m.group(1)), m.group(2).lower()
        delta = {"d": timedelta(days=n), "h": timedelta(hours=n), "w": timedelta(weeks=n), "min": timedelta(minutes=n)}[unit]
        return now_utc() - delta
    if re.match(r"^\s*\d+\s*(m|mo|mies\w*|month\w*)\s*$", value, re.I):
        raise ToolError("INVALID_ARG", f"Ambiguous window {value!r} (minutes or months?)", "Use e.g. '30d' (days) or '30min' (minutes)")
    try:
        return parse_iso(value)
    except ValueError as e:
        raise ToolError("INVALID_ARG", f"Invalid date/window: {value!r}", "Use e.g. '7d', '24h' or ISO 8601") from e


_DUR = re.compile(r"^P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?$")


def parse_duration_iso8601(s: str | None) -> int | None:
    if not s:
        return None
    m = _DUR.match(s)
    if not m:
        return None
    d, h, mi, se = (int(x) if x else 0 for x in m.groups())
    return d * 86400 + h * 3600 + mi * 60 + se
