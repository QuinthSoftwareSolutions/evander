"""Read-only token reporting over recorded API and subscription inference."""

from __future__ import annotations

import time
from collections import OrderedDict
from datetime import UTC, datetime, timedelta
from threading import RLock
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from evander.models import DirectoryPath

from .cost_balances import dedupe_model_usage_cost_events
from .inference_report import _billing_kind, _iso, _request_key, _utc
from .store import ModelUsageStore


def _token_events(events):
    selected = dedupe_model_usage_cost_events(events)
    if selected:
        return selected
    # A zero-only transcript is unhelpful for pricing, but still represents
    # known zero token usage when there is no stronger relay record.
    return [
        event
        for event in events
        if event.normalized_usage_status == "available"
        and (event.total_tokens == 0 or (event.input_tokens == 0 and event.output_tokens == 0))
    ]


class InferenceTokenReport:
    def __init__(self, store: ModelUsageStore) -> None:
        self.store = store
        self._lock = RLock()
        self._cache: OrderedDict[tuple, tuple[float, dict]] = OrderedDict()

    def read(
        self,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        timezone: str = "UTC",
        directory_path: str = "root",
        now: datetime | None = None,
    ) -> dict:
        try:
            zone = ZoneInfo(timezone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("timezone must be a valid IANA timezone") from exc
        directory = DirectoryPath(segments=directory_path).as_posix()
        clock = _utc(now or datetime.now(UTC))
        period_end = _utc(end) if end else clock
        period_start = (
            _utc(start)
            if start
            else clock.astimezone(zone)
            .replace(
                day=1,
                hour=0,
                minute=0,
                second=0,
                microsecond=0,
            )
            .astimezone(UTC)
        )
        if period_end <= period_start:
            raise ValueError("start must be earlier than end")
        with self._lock, self.store.cost_report_snapshot() as snapshot:
            key = (
                start,
                end,
                timezone,
                directory,
                snapshot.generation,
                snapshot.revision,
                clock.astimezone(zone).date(),
            )
            cached = self._cache.get(key)
            if cached and now is None and time.monotonic() - cached[0] < 45:
                return cached[1]
            groups: dict[str, list] = {}
            for event in snapshot.events_in_period(
                start=period_start,
                end=period_end,
                directory_path=directory,
                include_request_siblings=True,
            ):
                groups.setdefault(_request_key(event), []).append(event)
            days: dict[str, dict] = {}
            day = period_start.astimezone(zone).date()
            last = (period_end - timedelta(microseconds=1)).astimezone(zone).date()
            while day <= last:
                days[day.isoformat()] = {"total_tokens": 0, "categories": {}}
                day += timedelta(days=1)
            coverage = {"available_events": 0, "unavailable_events": 0}
            total = 0
            for events in groups.values():
                recorded = sorted(
                    (event for event in events if event.cost_attribution_recorded),
                    key=lambda event: (event.occurred_at, event.event_id),
                )
                for event in _token_events(events):
                    attribution = (
                        event
                        if event.cost_attribution_recorded
                        else (recorded[0] if recorded else event)
                    )
                    if not period_start <= event.occurred_at < period_end:
                        continue
                    path = (
                        attribution.directory_path
                        or event.directory_path
                        or next(
                            (item.directory_path for item in recorded if item.directory_path),
                            None,
                        )
                    )
                    if not (
                        path == directory
                        or (path and path.startswith(directory + "/"))
                        or (directory == "root" and path is None)
                    ):
                        continue
                    if _billing_kind(attribution) not in {"api", "subscription"}:
                        continue
                    tokens = event.total_tokens
                    if (
                        tokens is None
                        and event.input_tokens is not None
                        and event.output_tokens is not None
                    ):
                        tokens = event.input_tokens + event.output_tokens
                    if event.normalized_usage_status != "available" or tokens is None:
                        coverage["unavailable_events"] += 1
                        continue
                    coverage["available_events"] += 1
                    total += tokens
                    values = days[event.occurred_at.astimezone(zone).date().isoformat()]
                    values["total_tokens"] += tokens
                    category = attribution.cost_category
                    values["categories"][category] = values["categories"].get(category, 0) + tokens
            result = {
                "period_start": _iso(period_start),
                "period_end": _iso(period_end),
                "timezone": str(zone),
                "directory_path": directory,
                "total_tokens": total,
                "coverage": {**coverage, "partial": coverage["unavailable_events"] > 0},
                "daily": [
                    {
                        "date": date,
                        "total_tokens": values["total_tokens"],
                        "categories": [
                            {"category": category, "total_tokens": amount}
                            for category, amount in sorted(
                                values["categories"].items(),
                                key=lambda item: item[0] or "",
                            )
                        ],
                    }
                    for date, values in days.items()
                ],
            }
            self._cache[key] = (time.monotonic(), result)
            while len(self._cache) > 16:
                self._cache.popitem(last=False)
            return result
