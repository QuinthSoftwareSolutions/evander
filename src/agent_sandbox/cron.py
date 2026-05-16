from __future__ import annotations

from datetime import UTC, datetime, timedelta

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
            "Expected 5 fields (minute hour day month weekday), "
            "for example: '*/15 * * * *'."
        )
    if not croniter.is_valid(normalized):
        raise ValueError(f"Unsupported cron expression syntax: {normalized!r}.")
    return normalized


def next_cron_time(expression: str, base: datetime) -> datetime | None:
    try:
        normalized = validate_cron_expression(expression)
        iterator = croniter(normalized, _coerce_utc(base).replace(second=0, microsecond=0))
        next_value = iterator.get_next(datetime)
    except (ValueError, OverflowError):
        return None
    return _coerce_utc(next_value).replace(second=0, microsecond=0)


def cron_min_interval_seconds(expression: str, base: datetime | None = None) -> float | None:
    if base is None:
        base = datetime.now(UTC).replace(second=0, microsecond=0) - timedelta(minutes=1)
    first = next_cron_time(expression, base)
    if first is None:
        return None
    second = next_cron_time(expression, first)
    if second is None:
        return None
    return (second - first).total_seconds()


def _coerce_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
