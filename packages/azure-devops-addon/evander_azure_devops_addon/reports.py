from __future__ import annotations

import json
import math
import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

DEFAULT_DONE_STATES = ("Done", "Closed", "Completed")
DEFAULT_IN_PROGRESS_STATES = ("In Progress", "Active")
DEFAULT_EFFORT_FIELDS = (
    "Microsoft.VSTS.Scheduling.Effort",
    "Microsoft.VSTS.Scheduling.StoryPoints",
    "Microsoft.VSTS.Scheduling.OriginalEstimate",
)
DEFAULT_ELAPSED_TIME_MODE = "swedish_work_time"
MANUAL_ELAPSED_TIME_MODES = frozenset({"manual", "manual_override"})
DEFAULT_WORKDAY_START = "08:00"
DEFAULT_WORKDAY_END = "17:00"
SWEDEN_TIMEZONE = "Europe/Stockholm"

_STATE_SEPARATOR_PATTERN = re.compile(r"[\s_-]+")


def build_ado_daily_status_report_data(
    *,
    items: Sequence[Mapping[str, Any]],
    project: str | None,
    team: str | None,
    iteration_path: str | None,
    iteration_start_date: str | None = None,
    iteration_finish_date: str | None = None,
    report_date: str | None = None,
    title: str = "Daily Sprint Status",
    sprint_label: str | None = None,
    sprint_length: str | None = None,
    elapsed_sprint_percent: float | None = None,
    done_states: Sequence[str] | None = None,
    in_progress_states: Sequence[str] | None = None,
    effort_fields: Sequence[str] | None = None,
    truncated: bool = False,
    page_count: int = 1,
    max_items: int | None = None,
    elapsed_time_mode: str = DEFAULT_ELAPSED_TIME_MODE,
    workday_start: str = DEFAULT_WORKDAY_START,
    workday_end: str = DEFAULT_WORKDAY_END,
    include_common_swedish_non_working_days: bool = True,
    now: datetime | None = None,
) -> dict[str, Any]:
    resolved_done_states = _normalize_string_list(done_states, DEFAULT_DONE_STATES)
    resolved_progress_states = _normalize_string_list(
        in_progress_states, DEFAULT_IN_PROGRESS_STATES
    )
    resolved_effort_fields = _normalize_string_list(effort_fields, DEFAULT_EFFORT_FIELDS)
    done_state_keys = {_state_key(state) for state in resolved_done_states}
    progress_state_keys = {_state_key(state) for state in resolved_progress_states}

    total_items = len(items)
    state_counts: Counter[str] = Counter()
    type_counts: Counter[str] = Counter()
    unassigned_count = 0
    not_started_count = 0
    in_progress_count = 0
    done_count = 0
    with_pr_count = 0
    total_effort = 0.0
    done_effort = 0.0
    items_with_effort = 0
    missing_effort_count = 0
    effort_field_hits: Counter[str] = Counter()

    for item in items:
        state = _string_or_none(item.get("state")) or _field_string(item, "System.State") or ""
        state_counts[state or "Unspecified"] += 1
        state_key = _state_key(state)
        item_type = _string_or_none(item.get("type")) or _field_string(item, "System.WorkItemType")
        if item_type:
            type_counts[item_type] += 1

        assigned_to = _string_or_none(item.get("assigned_to")) or _assigned_to_from_fields(item)
        if assigned_to is None:
            unassigned_count += 1

        is_done = state_key in done_state_keys
        if state_key in progress_state_keys:
            in_progress_count += 1
        if is_done:
            done_count += 1
        if not is_done and state_key not in progress_state_keys:
            not_started_count += 1
        if _item_has_pull_request_relation(item):
            with_pr_count += 1

        effort, effort_field = _item_effort(item, resolved_effort_fields)
        if effort is None:
            missing_effort_count += 1
            continue
        total_effort += effort
        items_with_effort += 1
        if effort_field:
            effort_field_hits[effort_field] += 1
        if is_done:
            done_effort += effort

    generated_at = (now or datetime.now(UTC)).astimezone(UTC)
    resolved_report_date = _date_string(report_date, fallback=generated_at.date())
    elapsed_calculation_mode = str(elapsed_time_mode or DEFAULT_ELAPSED_TIME_MODE).strip().lower()
    manual_elapsed_override_requested = (
        elapsed_sprint_percent is not None and elapsed_calculation_mode in MANUAL_ELAPSED_TIME_MODES
    )
    configured_elapsed_time_mode = (
        DEFAULT_ELAPSED_TIME_MODE
        if elapsed_calculation_mode in MANUAL_ELAPSED_TIME_MODES
        else elapsed_time_mode
    )
    elapsed_calculation = _elapsed_time_calculation(
        iteration_start_date,
        iteration_finish_date,
        now=generated_at,
        elapsed_time_mode=configured_elapsed_time_mode,
        workday_start=workday_start,
        workday_end=workday_end,
        include_common_swedish_non_working_days=include_common_swedish_non_working_days,
    )
    ignored_elapsed_override = None
    if manual_elapsed_override_requested:
        resolved_elapsed_percent = _round_percent(elapsed_sprint_percent)
        elapsed_calculation = {
            **elapsed_calculation,
            "mode": "manual_override",
            "configured_mode": elapsed_calculation["mode"],
            "percent": resolved_elapsed_percent,
        }
    else:
        resolved_elapsed_percent = _round_percent(elapsed_calculation["percent"])
        if elapsed_sprint_percent is not None:
            ignored_elapsed_override = _round_percent(elapsed_sprint_percent)
    resolved_sprint_label = (
        _string_or_none(sprint_label)
        or _sprint_label_from_iteration(iteration_path)
        or "Current Sprint"
    )
    resolved_sprint_length = (
        _string_or_none(sprint_length)
        or _sprint_length_from_dates(iteration_start_date, iteration_finish_date)
        or "Current sprint"
    )

    completed_effort_percent = _percent(done_effort, total_effort)
    template_data = {
        "title": _string_or_none(title) or "Daily Sprint Status",
        "reportDate": resolved_report_date,
        "sprintLabel": resolved_sprint_label,
        "sprintLength": resolved_sprint_length,
        "items": {
            "unassigned": unassigned_count,
            "notStarted": not_started_count,
            "inProgress": in_progress_count,
            "withPrs": with_pr_count,
            "done": done_count,
            "total": total_items,
        },
        "effort": {
            "done": _round_number(done_effort),
            "total": _round_number(total_effort),
        },
        "elapsedSprintPercent": resolved_elapsed_percent,
    }

    percentages = {
        "elapsedSprintPercent": resolved_elapsed_percent,
        "completedEffortPercent": _round_percent(completed_effort_percent),
        "paceDeltaPercentagePoints": _round_signed_percent(
            completed_effort_percent - resolved_elapsed_percent
        ),
        "unassignedShare": _round_percent(_percent(unassigned_count, total_items)),
        "notStartedShare": _round_percent(_percent(not_started_count, total_items)),
        "inProgressShare": _round_percent(_percent(in_progress_count, total_items)),
        "prCoverage": _round_percent(_percent(with_pr_count, total_items)),
        "doneItemRatio": _round_percent(_percent(done_count, total_items)),
    }

    return {
        "template_data": template_data,
        "template_data_json": json.dumps(template_data, indent=2, sort_keys=False),
        "percentages": percentages,
        "source": {
            "provider": "azure_devops",
            "project": project,
            "team": team,
            "iteration_path": iteration_path,
            "iteration_start_date": iteration_start_date,
            "iteration_finish_date": iteration_finish_date,
            "item_count": total_items,
            "page_count": page_count,
            "max_items": max_items,
            "truncated": truncated,
            "state_counts": dict(sorted(state_counts.items())),
            "type_counts": dict(sorted(type_counts.items())),
            "done_states": resolved_done_states,
            "in_progress_states": resolved_progress_states,
            "effort_fields": resolved_effort_fields,
            "effort_field_hits": dict(sorted(effort_field_hits.items())),
            "items_with_effort": items_with_effort,
            "items_missing_effort": missing_effort_count,
            "elapsed_time_calculation": elapsed_calculation,
            "ignored_elapsed_sprint_percent_override": ignored_elapsed_override,
            "generated_at": generated_at.isoformat().replace("+00:00", "Z"),
        },
    }


def _normalize_string_list(value: Sequence[str] | None, default: Sequence[str]) -> list[str]:
    raw_values: Iterable[str] = value if value is not None else default
    normalized: list[str] = []
    seen: set[str] = set()
    for item in raw_values:
        text = str(item or "").strip()
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(text)
    return normalized


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _field_string(item: Mapping[str, Any], field_name: str) -> str | None:
    fields = item.get("fields") if isinstance(item.get("fields"), Mapping) else {}
    return _string_or_none(fields.get(field_name))


def _assigned_to_from_fields(item: Mapping[str, Any]) -> str | None:
    fields = item.get("fields") if isinstance(item.get("fields"), Mapping) else {}
    value = fields.get("System.AssignedTo")
    if isinstance(value, Mapping):
        for key in ("displayName", "uniqueName", "mailAddress", "signInAddress", "name"):
            if candidate := _string_or_none(value.get(key)):
                return candidate
        return None
    return _string_or_none(value)


def _state_key(value: str | None) -> str:
    return _STATE_SEPARATOR_PATTERN.sub(" ", str(value or "").strip()).casefold()


def _item_has_pull_request_relation(item: Mapping[str, Any]) -> bool:
    relations = item.get("relations")
    if not isinstance(relations, list):
        return False
    for relation in relations:
        if not isinstance(relation, Mapping):
            continue
        rel = str(relation.get("rel") or "").casefold()
        url = str(relation.get("url") or "")
        attrs = (
            relation.get("attributes") if isinstance(relation.get("attributes"), Mapping) else {}
        )
        attr_values = " ".join(str(value or "") for value in attrs.values()).casefold()
        if "pullrequestid" in url.casefold() or "pull request" in attr_values:
            return True
        if "artifactlink" in rel and "pullrequest" in url.casefold():
            return True
    return False


def _item_effort(
    item: Mapping[str, Any], effort_fields: Sequence[str]
) -> tuple[float | None, str | None]:
    fields = item.get("fields") if isinstance(item.get("fields"), Mapping) else {}
    for field_name in effort_fields:
        if field_name not in fields:
            continue
        value = _number_or_none(fields.get(field_name))
        if value is not None:
            return value, field_name
    return None, None


def _number_or_none(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        number = float(value)
    else:
        text = str(value).strip().replace(" ", "")
        if "," in text and "." not in text:
            text = text.replace(",", ".")
        try:
            number = float(text)
        except ValueError:
            return None
    if not math.isfinite(number):
        return None
    return max(number, 0.0)


def _date_string(value: str | None, *, fallback: date) -> str:
    parsed = _parse_datetime(value)
    if parsed is None:
        return fallback.isoformat()
    return parsed.date().isoformat()


def _parse_datetime(value: str | None) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        try:
            parsed_date = date.fromisoformat(text)
        except ValueError:
            return None
        return datetime.combine(parsed_date, time.min, tzinfo=UTC)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _elapsed_time_calculation(
    start_value: str | None,
    finish_value: str | None,
    *,
    now: datetime,
    elapsed_time_mode: str,
    workday_start: str,
    workday_end: str,
    include_common_swedish_non_working_days: bool,
) -> dict[str, Any]:
    mode = str(elapsed_time_mode or DEFAULT_ELAPSED_TIME_MODE).strip().lower()
    if mode in {"calendar", "calendar_time", "raw"}:
        percent = _elapsed_sprint_percent(start_value, finish_value, now=now)
        return {
            "mode": "calendar_time",
            "percent": _round_percent(percent),
            "start": start_value,
            "finish": finish_value,
        }
    if mode not in {"swedish_work_time", "sweden_work_time"}:
        mode = DEFAULT_ELAPSED_TIME_MODE

    tz = ZoneInfo(SWEDEN_TIMEZONE)
    start = _parse_sprint_boundary(start_value, tz=tz, end_of_day=False)
    finish = _parse_sprint_boundary(finish_value, tz=tz, end_of_day=True)
    start_time = _parse_hh_mm(workday_start, default=DEFAULT_WORKDAY_START)
    end_time = _parse_hh_mm(workday_end, default=DEFAULT_WORKDAY_END)
    if end_time <= start_time:
        end_time = _parse_hh_mm(DEFAULT_WORKDAY_END, default=DEFAULT_WORKDAY_END)
        start_time = _parse_hh_mm(DEFAULT_WORKDAY_START, default=DEFAULT_WORKDAY_START)
    if start is None or finish is None or finish <= start:
        return {
            "mode": "swedish_work_time",
            "percent": 0,
            "timezone": SWEDEN_TIMEZONE,
            "workday_start": _format_hh_mm(start_time),
            "workday_end": _format_hh_mm(end_time),
            "include_common_swedish_non_working_days": include_common_swedish_non_working_days,
            "working_minutes_elapsed": 0,
            "working_minutes_total": 0,
            "excluded_dates": [],
        }

    now_local = now.astimezone(tz)
    elapsed_until = max(start, min(now_local, finish))
    total = _swedish_working_seconds_between(
        start,
        finish,
        workday_start=start_time,
        workday_end=end_time,
        include_common_swedish_non_working_days=include_common_swedish_non_working_days,
    )
    elapsed = _swedish_working_seconds_between(
        start,
        elapsed_until,
        workday_start=start_time,
        workday_end=end_time,
        include_common_swedish_non_working_days=include_common_swedish_non_working_days,
    )
    excluded = _excluded_swedish_dates(
        start.date(),
        finish.date(),
        include_common_swedish_non_working_days=include_common_swedish_non_working_days,
    )
    return {
        "mode": "swedish_work_time",
        "percent": _round_percent(_percent(elapsed, total)),
        "timezone": SWEDEN_TIMEZONE,
        "workday_start": _format_hh_mm(start_time),
        "workday_end": _format_hh_mm(end_time),
        "include_common_swedish_non_working_days": include_common_swedish_non_working_days,
        "working_minutes_elapsed": _round_number(elapsed / 60),
        "working_minutes_total": _round_number(total / 60),
        "excluded_dates": excluded,
    }


def _parse_sprint_boundary(value: str | None, *, tz: ZoneInfo, end_of_day: bool) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    date_match = re.match(
        r"^(?P<date>\d{4}-\d{2}-\d{2})(?:[T ]00:00:00(?:\.0+)?(?:Z|[+-]\d{2}:\d{2})?)?$", text
    )
    if date_match:
        parsed_date = date.fromisoformat(date_match.group("date"))
        boundary_time = time.max if end_of_day else time.min
        return datetime.combine(parsed_date, boundary_time, tzinfo=tz)
    parsed = _parse_datetime(text)
    if parsed is None:
        return None
    return parsed.astimezone(tz)


def _parse_hh_mm(value: str | None, *, default: str) -> time:
    text = str(value or default).strip()
    match = re.match(r"^(?P<hour>[0-2]?\d):(?P<minute>[0-5]\d)$", text)
    if not match:
        if text != default:
            return _parse_hh_mm(default, default=default)
        return time(8, 0)
    hour = int(match.group("hour"))
    minute = int(match.group("minute"))
    if hour > 23:
        return _parse_hh_mm(default, default=default)
    return time(hour, minute)


def _format_hh_mm(value: time) -> str:
    return f"{value.hour:02d}:{value.minute:02d}"


def _swedish_working_seconds_between(
    start: datetime,
    finish: datetime,
    *,
    workday_start: time,
    workday_end: time,
    include_common_swedish_non_working_days: bool,
) -> float:
    if finish <= start:
        return 0.0
    seconds = 0.0
    current_date = start.date()
    finish_date = finish.date()
    while current_date <= finish_date:
        if _is_swedish_working_day(
            current_date,
            include_common_swedish_non_working_days=include_common_swedish_non_working_days,
        ):
            day_start = datetime.combine(current_date, workday_start, tzinfo=start.tzinfo)
            day_end = datetime.combine(current_date, workday_end, tzinfo=start.tzinfo)
            interval_start = max(start, day_start)
            interval_end = min(finish, day_end)
            if interval_end > interval_start:
                seconds += (interval_end - interval_start).total_seconds()
        current_date += timedelta(days=1)
    return seconds


def _is_swedish_working_day(value: date, *, include_common_swedish_non_working_days: bool) -> bool:
    if value.weekday() >= 5:
        return False
    return value not in _swedish_non_working_dates(
        value.year,
        include_common_swedish_non_working_days=include_common_swedish_non_working_days,
    )


def _excluded_swedish_dates(
    start: date,
    finish: date,
    *,
    include_common_swedish_non_working_days: bool,
) -> list[dict[str, str]]:
    excluded: list[dict[str, str]] = []
    for year in range(start.year, finish.year + 1):
        for holiday_date, name in sorted(
            _swedish_non_working_dates(
                year,
                include_common_swedish_non_working_days=include_common_swedish_non_working_days,
            ).items()
        ):
            if start <= holiday_date <= finish and holiday_date.weekday() < 5:
                excluded.append({"date": holiday_date.isoformat(), "name": name})
    return excluded


def _swedish_non_working_dates(
    year: int, *, include_common_swedish_non_working_days: bool
) -> dict[date, str]:
    easter = _easter_sunday(year)
    midsummer_day = _first_weekday_between(year, 6, 20, 26, weekday=5)
    if midsummer_day is None:
        raise RuntimeError(f"Unable to resolve Swedish Midsummer Day for {year}")
    all_saints_day = _first_weekday_between(year, 10, 31, 31, weekday=5)
    if all_saints_day is None:
        all_saints_day = _first_weekday_between(year, 11, 1, 6, weekday=5)
    if all_saints_day is None:
        raise RuntimeError(f"Unable to resolve Swedish All Saints' Day for {year}")
    holidays = {
        date(year, 1, 1): "New Year's Day",
        date(year, 1, 6): "Epiphany",
        easter - timedelta(days=2): "Good Friday",
        easter: "Easter Sunday",
        easter + timedelta(days=1): "Easter Monday",
        date(year, 5, 1): "May Day",
        easter + timedelta(days=39): "Ascension Day",
        easter + timedelta(days=49): "Whitsun",
        date(year, 6, 6): "National Day",
        midsummer_day: "Midsummer Day",
        all_saints_day: "All Saints' Day",
        date(year, 12, 25): "Christmas Day",
        date(year, 12, 26): "Boxing Day",
    }
    if include_common_swedish_non_working_days:
        holidays.update(
            {
                midsummer_day - timedelta(days=1): "Midsummer Eve",
                date(year, 12, 24): "Christmas Eve",
                date(year, 12, 31): "New Year's Eve",
            }
        )
    return {holiday_date: name for holiday_date, name in holidays.items() if holiday_date}


def _first_weekday_between(
    year: int,
    month: int,
    start_day: int,
    end_day: int,
    *,
    weekday: int,
) -> date | None:
    for day in range(start_day, end_day + 1):
        candidate = date(year, month, day)
        if candidate.weekday() == weekday:
            return candidate
    return None


def _easter_sunday(year: int) -> date:
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    weekday_offset = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * weekday_offset) // 451
    month = (h + weekday_offset - 7 * m + 114) // 31
    day = ((h + weekday_offset - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def _elapsed_sprint_percent(
    start_value: str | None,
    finish_value: str | None,
    *,
    now: datetime,
) -> float:
    start = _parse_datetime(start_value)
    finish = _parse_datetime(finish_value)
    if start is None or finish is None or finish <= start:
        return 0.0
    elapsed = (now - start).total_seconds()
    total = (finish - start).total_seconds()
    return _percent(elapsed, total)


def _sprint_label_from_iteration(iteration_path: str | None) -> str | None:
    text = _string_or_none(iteration_path)
    if text is None:
        return None
    parts = [part.strip() for part in re.split(r"[\\/]", text) if part.strip()]
    return parts[-1] if parts else text


def _sprint_length_from_dates(start_value: str | None, finish_value: str | None) -> str | None:
    start = _parse_datetime(start_value)
    finish = _parse_datetime(finish_value)
    if start is None or finish is None or finish <= start:
        return None
    weekdays = _weekday_count_inclusive(start.date(), finish.date())
    if 4 <= weekdays <= 6:
        return "1-week sprint"
    if 9 <= weekdays <= 11:
        return "2-week sprint"
    if weekdays >= 4 and weekdays % 5 == 0:
        weeks = weekdays // 5
        return f"{weeks}-week sprint"
    days = max(int(round((finish - start).total_seconds() / 86400)), 1)
    if 13 <= days <= 15:
        return "2-week sprint"
    if 6 <= days <= 8:
        return "1-week sprint"
    if days % 7 == 0:
        weeks = days // 7
        return f"{weeks}-week sprint"
    return f"{days}-day sprint"


def _weekday_count_inclusive(start: date, finish: date) -> int:
    if finish < start:
        return 0
    total = 0
    current = start
    while current <= finish:
        if current.weekday() < 5:
            total += 1
        current += timedelta(days=1)
    return total


def _percent(value: float | int, total: float | int) -> float:
    denominator = float(total or 0)
    if denominator <= 0:
        return 0.0
    return (float(value or 0) / denominator) * 100


def _round_percent(value: float | int | None) -> float:
    return _round_number(min(max(float(value or 0), 0.0), 100.0), digits=1)


def _round_signed_percent(value: float | int | None) -> float:
    return _round_number(float(value or 0), digits=1)


def _round_number(value: float | int, *, digits: int = 2) -> float | int:
    rounded = round(float(value or 0), digits)
    if rounded == 0:
        rounded = 0.0
    if rounded.is_integer():
        return int(rounded)
    return rounded
