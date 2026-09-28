"""Incremental, provider-neutral inference cost reporting.

The projection is disposable. Source writes stay in model-usage.db; report
builders serialize on a separate database and read a consistent source snapshot.
"""

from __future__ import annotations

import json
import sqlite3
import time
from collections import OrderedDict
from contextlib import closing
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from threading import RLock
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from evander.models import DirectoryPath
from .cost_balances import dedupe_model_usage_cost_events, model_usage_request_key
from .models import ModelInferenceUsageEvent
from .pricing import estimate_model_usage_cost, model_pricing_catalog_fingerprint
from .store import ModelUsageStore


def _json(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def _request_key(event: ModelInferenceUsageEvent) -> str:
    key = model_usage_request_key(event)
    return _json(["request", *key] if key else ["event", event.event_id])


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("Report boundaries must include a timezone")
    return value.astimezone(UTC)


def _iso(value: datetime) -> str:
    return _utc(value).isoformat()


def _billing_kind(event: ModelInferenceUsageEvent) -> str:
    if event.billing_kind:
        return event.billing_kind
    # Before billing provenance was captured, these provider names denoted
    # metered relay/classification traffic. Subscription turns bypass that relay.
    if event.provider in {"openai", "azure_openai", "anthropic", "openrouter", "jev"}:
        return "api"
    return "unknown"


class InferenceCostReport:
    def __init__(self, runtime_dir: Path, usage_store: ModelUsageStore) -> None:
        self.path = runtime_dir / "inference-cost-report.db"
        self.store = usage_store
        self._lock = RLock()
        self._cache: OrderedDict[tuple, tuple[float, dict]] = OrderedDict()

    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS metadata (singleton INTEGER PRIMARY KEY, payload TEXT);
            CREATE TABLE IF NOT EXISTS members (event_id TEXT PRIMARY KEY, request_key TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS members_request ON members(request_key);
            CREATE TABLE IF NOT EXISTS costs (
                request_key TEXT NOT NULL, event_id TEXT NOT NULL, occurred_at TEXT NOT NULL,
                directory_path TEXT, category TEXT, payload TEXT NOT NULL,
                PRIMARY KEY(request_key, event_id)
            );
            CREATE INDEX IF NOT EXISTS costs_period ON costs(occurred_at, directory_path);
            CREATE INDEX IF NOT EXISTS costs_categories ON costs(directory_path, category);
        """)
        return connection

    def _refresh(self, connection, catalog) -> str:
        fingerprint = model_pricing_catalog_fingerprint(catalog) if catalog else None
        connection.execute("BEGIN IMMEDIATE")
        try:
            row = connection.execute("SELECT payload FROM metadata WHERE singleton=1").fetchone()
            previous = json.loads(row[0]) if row else {}
            with self.store.cost_report_snapshot() as snapshot:
                current = {
                    "version": 1,
                    "generation": snapshot.generation,
                    "revision": snapshot.revision,
                    "pricing": fingerprint,
                }
                rebuild = any(
                    previous.get(k) != current[k] for k in ("version", "generation", "pricing")
                )
                rebuild = rebuild or previous.get("revision", -1) > snapshot.revision
                groups: dict[str, dict[str, ModelInferenceUsageEvent]] = {}
                if rebuild:
                    connection.execute("DELETE FROM members")
                    connection.execute("DELETE FROM costs")
                    for event in snapshot.events():
                        groups.setdefault(_request_key(event), {})[event.event_id] = event
                else:
                    event_ids, _ = snapshot.changes_since(previous.get("revision", -1))
                    events = {identity: snapshot.event(identity) for identity in event_ids}
                    for identity, event in events.items():
                        old = connection.execute(
                            "SELECT request_key FROM members WHERE event_id=?", (identity,)
                        ).fetchone()
                        if old:
                            groups.setdefault(old[0], {})
                        if event:
                            groups.setdefault(_request_key(event), {})[identity] = event
                    for key, group in groups.items():
                        for row in connection.execute(
                            "SELECT event_id FROM members WHERE request_key=?", (key,)
                        ):
                            identity = row[0]
                            if identity not in events:
                                events[identity] = snapshot.event(identity)
                            event = events[identity]
                            if event and _request_key(event) == key:
                                group[identity] = event
                    # Remove memberships first: corrections can move between groups.
                    for key in groups:
                        connection.execute("DELETE FROM members WHERE request_key=?", (key,))
                for key, group in groups.items():
                    self._replace(connection, key, list(group.values()), catalog)
                connection.execute(
                    "INSERT OR REPLACE INTO metadata VALUES (1, ?)", (_json(current),)
                )
            connection.commit()
            return _json(current)
        except BaseException:
            connection.rollback()
            raise

    @staticmethod
    def _replace(connection, key, events, catalog) -> None:
        connection.execute("DELETE FROM costs WHERE request_key=?", (key,))
        connection.executemany(
            "INSERT INTO members VALUES (?, ?)", ((e.event_id, key) for e in events)
        )
        # The relay knows the dispatched category/key; a later transcript can
        # improve token accounting without losing that immutable attribution.
        recorded = sorted(
            (e for e in events if e.cost_attribution_recorded),
            key=lambda e: (e.occurred_at, e.event_id),
        )
        for event in dedupe_model_usage_cost_events(events):
            attribution = (
                event if event.cost_attribution_recorded else (recorded[0] if recorded else event)
            )
            estimate = estimate_model_usage_cost(event, catalog, currency="usd")
            payload = {
                "provider": event.provider,
                "api_key_name": attribution.api_key_name,
                "category": attribution.cost_category,
                "billing_kind": _billing_kind(attribution),
                "estimated_cost": str(estimate.estimated_total)
                if estimate.estimated_total is not None
                else None,
                "reported_cost": str(estimate.reported_total)
                if estimate.reported_total is not None
                else None,
                "status": estimate.status,
            }
            connection.execute(
                "INSERT INTO costs VALUES (?, ?, ?, ?, ?, ?)",
                (
                    key,
                    event.event_id,
                    _iso(event.occurred_at),
                    attribution.directory_path or event.directory_path,
                    attribution.cost_category,
                    _json(payload),
                ),
            )

    def categories(self, *, directory_path: str, catalog) -> list[str]:
        directory = DirectoryPath(segments=directory_path).as_posix()
        with self._lock, closing(self._connect()) as connection:
            self._refresh(connection, catalog)
            return sorted(
                {
                    row[0]
                    for row in connection.execute(
                        "SELECT DISTINCT category FROM costs WHERE category IS NOT NULL AND "
                        "(directory_path=? OR substr(directory_path,1,?)=?)",
                        (directory, len(directory) + 1, directory + "/"),
                    )
                }
            )

    def read(
        self,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        timezone: str = "UTC",
        directory_path: str = "root",
        catalog=None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
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
            .replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            .astimezone(UTC)
        )
        if period_end <= period_start:
            raise ValueError("start must be earlier than end")
        with self._lock, closing(self._connect()) as connection:
            revision = self._refresh(connection, catalog)
            cache_key = (start, end, timezone, directory, revision, clock.astimezone(zone).date())
            cached = self._cache.get(cache_key)
            if cached and time.monotonic() - cached[0] < 45 and now is None:
                return cached[1]
            rows = connection.execute(
                "SELECT occurred_at,payload FROM costs WHERE occurred_at>=? AND occurred_at<? AND "
                "(directory_path=? OR substr(directory_path,1,?)=? OR (?='root' AND directory_path IS NULL)) "
                "ORDER BY occurred_at,event_id",
                (
                    _iso(period_start),
                    _iso(period_end),
                    directory,
                    len(directory) + 1,
                    directory + "/",
                    directory,
                ),
            )
            payload = self._aggregate(rows, period_start, period_end, zone, directory, catalog)
            self._cache[cache_key] = (time.monotonic(), payload)
            while len(self._cache) > 16:
                self._cache.popitem(last=False)
            return payload

    @staticmethod
    def _aggregate(rows, start, end, zone, directory, catalog):
        groups: dict[tuple, dict] = {}
        accounts: dict[str, dict] = {}
        days: dict[str, dict] = {}
        date = start.astimezone(zone).date()
        last = (end - timedelta(microseconds=1)).astimezone(zone).date()
        while date <= last:
            days[date.isoformat()] = {"categories": {}, "accounts": {}}
            date += timedelta(days=1)
        coverage = {
            "priced_events": 0,
            "unpriced_events": 0,
            "excluded_subscription_events": 0,
            "excluded_local_events": 0,
            "unknown_billing_events": 0,
        }
        estimated, reported = Decimal(0), Decimal(0)
        for row in rows:
            value = json.loads(row["payload"])
            kind = value["billing_kind"]
            if kind != "api":
                coverage[
                    {
                        "subscription": "excluded_subscription_events",
                        "local": "excluded_local_events",
                    }.get(kind, "unknown_billing_events")
                ] += 1
                continue
            key = (value["provider"], value["api_key_name"], value["category"])
            account_id = _json(list(key[:2]))
            accounts.setdefault(
                account_id, {"id": account_id, "provider": key[0], "api_key_name": key[1]}
            )
            group = groups.setdefault(
                key,
                {
                    "provider": key[0],
                    "api_key_name": key[1],
                    "category": key[2],
                    "cost": Decimal(0),
                    "priced_events": 0,
                    "unpriced_events": 0,
                },
            )
            amount = (
                value["reported_cost"]
                if value["reported_cost"] is not None
                else value["estimated_cost"]
            )
            if amount is None:
                coverage["unpriced_events"] += 1
                group["unpriced_events"] += 1
                continue
            cost = Decimal(amount)
            coverage["priced_events"] += 1
            group["priced_events"] += 1
            group["cost"] += cost
            if value["reported_cost"] is not None:
                reported += cost
            else:
                estimated += cost
            day = days[
                datetime.fromisoformat(row["occurred_at"]).astimezone(zone).date().isoformat()
            ]
            category_id = _json(key[2])
            day["categories"][category_id] = day["categories"].get(category_id, Decimal(0)) + cost
            day["accounts"][account_id] = day["accounts"].get(account_id, Decimal(0)) + cost
        cumulative = {key: Decimal(0) for key in accounts}
        total = Decimal(0)
        daily = []
        for date, day in days.items():
            day_total = sum(day["accounts"].values(), Decimal(0))
            total += day_total
            for key in accounts:
                cumulative[key] += day["accounts"].get(key, Decimal(0))
            daily.append(
                {
                    "date": date,
                    "cost": float(day_total),
                    "cumulative_cost": float(total),
                    "categories": [
                        {"category": json.loads(k), "cost": float(v)}
                        for k, v in sorted(day["categories"].items())
                    ],
                    "accounts": [
                        {
                            "id": k,
                            "cost": float(day["accounts"].get(k, 0)),
                            "cumulative_cost": float(v),
                        }
                        for k, v in cumulative.items()
                    ],
                }
            )
        group_rows = [
            {**g, "cost": float(g["cost"]) if g["priced_events"] else None}
            for _, g in sorted(groups.items(), key=lambda item: tuple(v or "" for v in item[0]))
        ]
        return {
            "period_start": _iso(start),
            "period_end": _iso(end),
            "timezone": str(zone),
            "directory_path": directory,
            "currency": "USD",
            "pricing_version": catalog.version if catalog else None,
            "total_cost": float(estimated + reported),
            "estimated_cost": float(estimated),
            "reported_cost": float(reported),
            "coverage": {
                **coverage,
                "partial": bool(coverage["unpriced_events"] or coverage["unknown_billing_events"]),
            },
            "groups": group_rows,
            "accounts": sorted(
                accounts.values(), key=lambda a: (a["provider"], a["api_key_name"] or "")
            ),
            "daily": daily,
        }
