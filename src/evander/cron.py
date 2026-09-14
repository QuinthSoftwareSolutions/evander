from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import croniter


def normalize_cron_expression(expression: str) -> str:
    normalized = " ".join(str(expression).split())
    if not normalized:
        raise ValueError("Cron expression must be non-empty.")
    return normalized


def validate_cron_expression(expression: str) -> str:
    normalized = normalize_cron_expression(expression)
    if len(normalized.split()) != 5:
        raise ValueError(
            "Expected 5 fields (minute hour day month weekday), for example: '*/15 * * * *'."
        )
    if not croniter.is_valid(normalized):
        raise ValueError(f"Unsupported cron expression syntax: {normalized!r}.")
    return normalized


def normalize_cron_timezone(value: object) -> str:
    normalized = str(value or "UTC").strip() or "UTC"
    try:
        ZoneInfo(normalized)
    except (ValueError, ZoneInfoNotFoundError) as exc:
        raise ValueError(f"Unknown IANA timezone: {normalized!r}") from exc
    return normalized


def next_cron_time(
    expression: str, base: datetime, *, timezone_name: str = "UTC"
) -> datetime | None:
    try:
        normalized = validate_cron_expression(expression)
        zone = ZoneInfo(normalize_cron_timezone(timezone_name))
        local_base = _coerce_utc(base).astimezone(zone).replace(second=0, microsecond=0)
        iterator = croniter(normalized, local_base)
        next_value = iterator.get_next(datetime)
    except (ValueError, OverflowError, ZoneInfoNotFoundError):
        return None
    return _coerce_cron_value(next_value, zone).astimezone(UTC).replace(second=0, microsecond=0)


def latest_cron_time_on_or_before(
    expression: str, base: datetime, *, timezone_name: str = "UTC"
) -> datetime | None:
    try:
        normalized = validate_cron_expression(expression)
        zone = ZoneInfo(normalize_cron_timezone(timezone_name))
        local_base = _coerce_utc(base).astimezone(zone).replace(second=0, microsecond=0)
        iterator = croniter(normalized, local_base + timedelta(minutes=1))
        latest_value = iterator.get_prev(datetime)
    except (ValueError, OverflowError, ZoneInfoNotFoundError):
        return None
    return _coerce_cron_value(latest_value, zone).astimezone(UTC).replace(second=0, microsecond=0)


def cron_min_interval_seconds(
    expression: str,
    base: datetime | None = None,
    *,
    timezone_name: str = "UTC",
) -> float | None:
    if base is None:
        base = datetime.now(UTC).replace(second=0, microsecond=0) - timedelta(minutes=1)
    first = next_cron_time(expression, base, timezone_name=timezone_name)
    if first is None:
        return None
    second = next_cron_time(expression, first, timezone_name=timezone_name)
    if second is None:
        return None
    return (second - first).total_seconds()


def _coerce_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _coerce_cron_value(value: datetime, zone: ZoneInfo) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=zone)
    return value
