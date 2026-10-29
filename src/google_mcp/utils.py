"""Shared validation and normalization helpers."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any


def bounded_int(
    value: int | None, *, default: int, minimum: int = 1, maximum: int = 50, name: str = "max_results"
) -> int:
    """Return an integer inside the requested inclusive range; name is the parameter named in errors."""
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    if value < minimum or value > maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def require_text(value: str, name: str) -> str:
    """Validate a required non-empty string."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def parse_datetime(value: str, name: str) -> datetime:
    """Parse ISO 8601 and attach the system timezone to a naive value."""
    raw = require_text(value, name)
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{name} must be an ISO 8601 datetime") from exc
    if parsed.tzinfo is None:
        local_tz = datetime.now().astimezone().tzinfo
        if local_tz is None:
            raise ValueError(f"{name} has no UTC offset and the local timezone is unavailable")
        parsed = parsed.replace(tzinfo=local_tz)
    return parsed


def rfc3339(value: str, name: str) -> str:
    """Return an RFC 3339 datetime string with an explicit offset."""
    parsed = parse_datetime(value, name)
    rendered = parsed.isoformat(timespec="seconds")
    return rendered.replace("+00:00", "Z")


def parse_date(value: str, name: str) -> str:
    """Validate an ISO calendar date and return its canonical form."""
    raw = require_text(value, name)
    try:
        return date.fromisoformat(raw).isoformat()
    except ValueError as exc:
        raise ValueError(f"{name} must be a date in YYYY-MM-DD format") from exc


def default_event_window() -> tuple[str, str]:
    """Return now through seven days from now in UTC."""
    start = datetime.now(UTC)
    end = start + timedelta(days=7)
    return start.isoformat(timespec="seconds").replace("+00:00", "Z"), end.isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def compact(mapping: dict[str, Any]) -> dict[str, Any]:
    """Remove only None values, preserving empty strings and lists."""
    return {key: value for key, value in mapping.items() if value is not None}
