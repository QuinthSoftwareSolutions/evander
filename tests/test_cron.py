from __future__ import annotations

from datetime import UTC, datetime

import pytest

from evander.admin.workflow_catalog import _WORKFLOW_EDITOR_NODE_CATALOG
from evander.cron import (
    cron_min_interval_seconds,
    latest_cron_time_on_or_before,
    next_cron_time,
    normalize_cron_expression,
    normalize_cron_timezone,
    validate_cron_expression,
)
from evander.triggers.providers.cron_schema import CronTriggerConfigSchema


def test_normalize_cron_expression_collapses_whitespace() -> None:
    assert normalize_cron_expression("  */15   *  *  *   *  ") == "*/15 * * * *"


@pytest.mark.parametrize(
    ("expression", "base", "expected"),
    [
        (
            "0 6,15 * * 1-5",
            datetime(2026, 5, 18, 5, 59, tzinfo=UTC),
            datetime(2026, 5, 18, 6, 0, tzinfo=UTC),
        ),
        (
            "0 9 * jan mon-fri",
            datetime(2026, 1, 1, 8, 0, tzinfo=UTC),
            datetime(2026, 1, 1, 9, 0, tzinfo=UTC),
        ),
        (
            "0 8-18/2 * * 1-5",
            datetime(2026, 5, 18, 7, 59, tzinfo=UTC),
            datetime(2026, 5, 18, 8, 0, tzinfo=UTC),
        ),
        (
            "0 12 * * 7",
            datetime(2026, 5, 16, 12, 0, tzinfo=UTC),
            datetime(2026, 5, 17, 12, 0, tzinfo=UTC),
        ),
    ],
)
def test_next_cron_time_supports_standard_field_syntax(
    expression: str, base: datetime, expected: datetime
) -> None:
    assert next_cron_time(expression, base) == expected


def test_next_cron_time_uses_posix_day_of_month_or_week_semantics() -> None:
    assert next_cron_time("0 9 1 * mon", datetime(2026, 6, 1, 9, 0, tzinfo=UTC)) == datetime(
        2026, 6, 8, 9, 0, tzinfo=UTC
    )


@pytest.mark.parametrize(
    ("expression", "base", "expected"),
    [
        (
            "0 6,15 * * 1-5",
            datetime(2026, 5, 18, 6, 0, tzinfo=UTC),
            datetime(2026, 5, 18, 6, 0, tzinfo=UTC),
        ),
        (
            "*/15 * * * *",
            datetime(2026, 5, 18, 6, 7, tzinfo=UTC),
            datetime(2026, 5, 18, 6, 0, tzinfo=UTC),
        ),
        (
            "*/15 * * * *",
            datetime(2026, 5, 18, 6, 7),
            datetime(2026, 5, 18, 6, 0, tzinfo=UTC),
        ),
    ],
)
def test_latest_cron_time_on_or_before_supports_exact_and_previous_matches(
    expression: str, base: datetime, expected: datetime
) -> None:
    assert latest_cron_time_on_or_before(expression, base) == expected


def test_latest_cron_time_on_or_before_returns_none_for_invalid_syntax() -> None:
    assert (
        latest_cron_time_on_or_before("0 nope * * *", datetime(2026, 5, 18, 6, 0, tzinfo=UTC))
        is None
    )


def test_validate_cron_expression_rejects_invalid_syntax() -> None:
    with pytest.raises(ValueError, match="Expected 5 fields"):
        validate_cron_expression("* *")
    with pytest.raises(ValueError, match="Unsupported cron expression syntax"):
        validate_cron_expression("0 nope * * *")


def test_normalize_cron_timezone_accepts_iana_name_and_rejects_unknown_name() -> None:
    assert normalize_cron_timezone(" Europe/Stockholm ") == "Europe/Stockholm"
    assert normalize_cron_timezone(None) == "UTC"
    with pytest.raises(ValueError, match="Unknown IANA timezone"):
        normalize_cron_timezone("Europe/Not-A-City")


def test_cron_trigger_schema_defaults_to_utc_and_rejects_unknown_timezone() -> None:
    assert CronTriggerConfigSchema(expression="0 8 * * *").timezone == "UTC"
    with pytest.raises(ValueError, match="Unknown IANA timezone"):
        CronTriggerConfigSchema(expression="0 8 * * *", timezone="Mars/Olympus")


def test_workflow_editor_cron_catalog_exposes_timezone() -> None:
    cron_node = next(node for node in _WORKFLOW_EDITOR_NODE_CATALOG if node.type == "trigger:cron")
    timezone_field = next(field for field in cron_node.fields if field.key == "timezone")

    assert timezone_field.default_value == "UTC"
    assert timezone_field.placeholder == "Europe/Stockholm"


@pytest.mark.parametrize(
    ("base", "expected"),
    [
        (
            datetime(2026, 3, 28, 6, 59, tzinfo=UTC),
            datetime(2026, 3, 28, 7, 0, tzinfo=UTC),
        ),
        (
            datetime(2026, 3, 29, 5, 59, tzinfo=UTC),
            datetime(2026, 3, 29, 6, 0, tzinfo=UTC),
        ),
        (
            datetime(2026, 10, 25, 6, 59, tzinfo=UTC),
            datetime(2026, 10, 25, 7, 0, tzinfo=UTC),
        ),
    ],
)
def test_next_cron_time_keeps_local_hour_across_stockholm_dst(
    base: datetime, expected: datetime
) -> None:
    assert next_cron_time("0 8 * * *", base, timezone_name="Europe/Stockholm") == expected


def test_latest_cron_time_uses_configured_timezone() -> None:
    assert latest_cron_time_on_or_before(
        "0 8 * * *",
        datetime(2026, 3, 29, 6, 3, tzinfo=UTC),
        timezone_name="Europe/Stockholm",
    ) == datetime(2026, 3, 29, 6, 0, tzinfo=UTC)


def test_cron_min_interval_seconds_uses_shared_iterator() -> None:
    interval = cron_min_interval_seconds(
        "0 8-18/2 * * 1-5", datetime(2026, 5, 18, 7, 59, tzinfo=UTC)
    )

    assert interval == 2 * 60 * 60
