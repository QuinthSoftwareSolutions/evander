"""Exact UTC ordering for persisted ISO datetime values, without rewriting rows."""

from datetime import UTC, datetime


def timestamp_microseconds(value: datetime) -> int:
    delta = value.replace(tzinfo=value.tzinfo or UTC).astimezone(UTC) - datetime(
        1970, 1, 1, tzinfo=UTC
    )
    return (delta.days * 86400 + delta.seconds) * 1000000 + delta.microseconds


def timestamp_microseconds_sql(value: str) -> str:
    # SQLite's julianday rounds to milliseconds. Split the whole second from
    # the JSON datetime's <=6 fractional digits before applying its UTC offset,
    # preserving Python datetime ordering across sub-millisecond boundaries.
    whole = (
        f"CAST(strftime('%s',substr({value},1,19)||CASE "
        f"WHEN substr({value},-6,1) IN ('+','-') THEN substr({value},-6) ELSE '' END) AS INTEGER)"
    )
    fraction = (
        f"CASE WHEN substr({value},20,1)='.' THEN "
        f"CAST(ROUND(CAST(substr({value},20) AS REAL)*1000000) AS INTEGER) ELSE 0 END"
    )
    return f"({whole}*1000000+{fraction})"


DISPATCH_COMPLETED_KEY = timestamp_microseconds_sql("completed_at")
DISPATCH_ATTEMPT_KEY = timestamp_microseconds_sql("COALESCE(leased_at,activation_completed_at)")
DISPATCH_COMPLETED_ORDER = f"{DISPATCH_COMPLETED_KEY} DESC,id DESC"
DISPATCH_ATTEMPT_ORDER = f"{DISPATCH_ATTEMPT_KEY} DESC,id DESC"
ACTION_CREATED_KEY = timestamp_microseconds_sql("created_at")
ACTION_CREATED_ORDER = f"{ACTION_CREATED_KEY} DESC,id DESC"
