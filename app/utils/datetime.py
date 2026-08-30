"""Datetime helpers for calendar events."""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def _timezone_for_name(name: str) -> ZoneInfo | timezone:
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        if name == "Europe/Moscow":
            return timezone(timedelta(hours=3))
        return timezone.utc


def parse_start_datetime(value: str, timezone_name: str) -> datetime | None:
    """Parse a webhook datetime string into timezone-aware datetime."""
    normalized = value.replace("Z", "+00:00").replace(" ", "T")
    try:
        dt = datetime.fromisoformat(normalized)
    except (ValueError, TypeError):
        try:
            dt = datetime.strptime(value.strip(), "%Y-%m-%d %H:%M:%S")
        except (ValueError, TypeError):
            return None

    if dt.tzinfo is None:
        try:
            dt = dt.replace(tzinfo=_timezone_for_name(timezone_name))
        except Exception:
            pass
    return dt


def end_from_seance_length(
    start: datetime,
    seance_length_minutes: int,
) -> datetime:
    """end_time = start + seance_length (minutes)."""
    return start + timedelta(minutes=seance_length_minutes)
