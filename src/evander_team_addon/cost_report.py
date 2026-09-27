"""Durable, incremental projection for the complete operator cost report.

Only compact sums and request membership are retained. Usage payloads and
correlation contexts remain in the source store. A report refresh subtracts the
old contributions of changed requests and adds their current contributions, so
late transcript imports, corrections and deletions preserve deduplication.
"""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from collections.abc import Mapping
from contextlib import closing
from datetime import UTC
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from evander.model_usage.cost_balances import (
    dedupe_model_usage_cost_events,
    model_usage_request_key,
)
from evander.model_usage.models import ModelInferenceUsageEvent
from evander.model_usage.pricing import (
    ModelPricingCatalog,
    estimate_model_usage_cost,
    extract_cache_write_input_tokens,
    extract_cached_input_tokens,
    model_pricing_catalog_fingerprint,
)
from evander.model_usage.report_snapshot import CostReportSnapshot

from .models import agent_project_ids

if TYPE_CHECKING:
    from .costs import TeamCostService

_COUNTERS = (
    "events_count",
    "priced_events_count",
    "unpriced_events_count",
    "input_tokens",
    "cached_input_tokens",
    "cache_write_input_tokens",
    "output_tokens",
    "total_tokens",
)


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _group_key(event: ModelInferenceUsageEvent) -> tuple[str, ...]:
    request = model_usage_request_key(event)
    return ("request", *request) if request is not None else ("event", event.event_id)


def _attributions(service: TeamCostService) -> dict[str, dict[str, list[str | None]]]:
    state = service.state
    agent_projects = {
        agent.id: next(iter(agent_project_ids(state, agent.id)), None)
        for agent in state.agents.values()
    }
    sessions: dict[str, list[str | None]] = {}
    for agent in state.agents.values():
        if agent.session_id:
            sessions.setdefault(agent.session_id, [agent.team_id, agent_projects[agent.id]])
    # Ephemeral session attribution takes precedence over persistent agents.
    ephemeral: dict[str, list[str | None]] = {}
    for assignment in state.ephemeral_assignments.values():
        parent = state.agents.get(assignment.parent_agent_id)
        if assignment.session_id:
            ephemeral.setdefault(
                assignment.session_id,
                [parent.team_id if parent else None, assignment.project_id],
            )
    sessions.update(ephemeral)
    dispatches: dict[str, list[str | None]] = {}
    for record in state.action_records.values():
        if record.dispatch_id and record.dispatch_id not in dispatches:
            agent = state.agents.get(record.agent_id)
            dispatches[record.dispatch_id] = (
                [agent.team_id, agent_projects[agent.id]] if agent else [None, None]
            )
    return {"sessions": sessions, "dispatches": dispatches}


def _changed_keys(previous: Mapping[str, Any], current: Mapping[str, Any]) -> set[str]:
    return {
        key for key in previous.keys() | current.keys() if previous.get(key) != current.get(key)
    }


def _empty_amounts() -> list[Any]:
    return [0] * len(_COUNTERS) + [Decimal("0"), Decimal("0")]


def _amounts(payload: str) -> list[Any]:
    values = json.loads(payload)
    values[-2:] = [Decimal(value) for value in values[-2:]]
    return values


def _apply(target: list[Any], delta: list[Any], sign: int = 1) -> None:
    for index, value in enumerate(delta):
        target[index] += sign * value


def _contributions(
    service: TeamCostService,
    events: list[ModelInferenceUsageEvent],
    catalog: ModelPricingCatalog | None,
    windows: Mapping[str, dict[str, Any] | None],
) -> dict[str, tuple[list[Any], str]]:
    from .costs import _ensure_utc, _usage_source

    service._request_attribution_cache = service._request_attributions(events)
    cells: dict[str, tuple[list[Any], str]] = {}
    for event in dedupe_model_usage_cost_events(events):
        estimate = estimate_model_usage_cost(event, catalog, currency="usd")
        estimated_total = estimate.estimated_total
        reported_total = estimate.reported_total if estimated_total is None else None
        priced = estimated_total is not None or reported_total is not None
        attribution = service._attribute_event(event)
        window = windows.get(attribution.team_id) if attribution.team_id else None
        occurred_at = _ensure_utc(event.occurred_at)
        in_window = bool(
            window
            and occurred_at >= window["started_at"]
            and (window["ends_at"] is None or occurred_at < window["ends_at"])
        )
        key = _json(
            [
                attribution.team_id,
                attribution.project_id if attribution.project_confident else None,
                event.provider or "unknown",
                event.model or "<missing>",
                _usage_source(event),
                estimate.status,
                in_window,
            ]
        )
        event_order = _json(
            [
                event.occurred_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
                event.event_id,
            ]
        )
        values, _ = cells.setdefault(key, (_empty_amounts(), event_order))
        input_tokens = int(event.input_tokens or 0)
        output_tokens = int(event.output_tokens or 0)
        _apply(
            values,
            [
                1,
                int(priced),
                int(not priced),
                input_tokens,
                int(extract_cached_input_tokens(event) or 0),
                extract_cache_write_input_tokens(event).total,
                output_tokens,
                int(event.total_tokens)
                if event.total_tokens is not None
                else input_tokens + output_tokens,
                estimated_total or Decimal("0"),
                reported_total or Decimal("0"),
            ],
        )
    return cells


def _ensure_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS report_metadata (
            singleton INTEGER PRIMARY KEY CHECK (singleton = 1), payload_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS report_members (
            event_id TEXT PRIMARY KEY, group_key TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_report_members_group ON report_members(group_key);
        CREATE TABLE IF NOT EXISTS report_contributions (
            group_key TEXT NOT NULL, cell_key TEXT NOT NULL, amounts_json TEXT NOT NULL,
            first_event_order TEXT NOT NULL,
            PRIMARY KEY (group_key, cell_key)
        );
        CREATE INDEX IF NOT EXISTS idx_report_contributions_cell_order
            ON report_contributions(cell_key, first_event_order);
        CREATE TABLE IF NOT EXISTS report_cells (
            cell_key TEXT PRIMARY KEY, amounts_json TEXT NOT NULL
        );
        """
    )


def cost_report_cells(
    service: TeamCostService,
    catalog: ModelPricingCatalog | None,
    windows: Mapping[str, dict[str, Any] | None],
) -> list[tuple[list[Any], list[Any]]]:
    """Refresh once across processes, then return the small aggregate cell set.

    The first read and repricing rebuild history. Ordinary reads query changed
    identities through an index and never re-enumerate historical usage payloads.
    A separate SQLite transaction serializes report builders without taking a
    write lock in the model-usage database used by active inference requests.
    """
    path = service.runtime_dir / "team-cost-report.db"
    current_attributions = _attributions(service)
    current_config = {
        "version": 2,
        "source": str(service.usage_store.path.resolve()),
        "pricing": model_pricing_catalog_fingerprint(catalog) if catalog else None,
        # Cap amounts and group membership affect final presentation only.
        "windows": json.loads(_json(windows)),
    }
    with closing(sqlite3.connect(path, timeout=120.0)) as connection:
        _ensure_schema(connection)
        connection.execute("BEGIN IMMEDIATE")
        try:
            row = connection.execute("SELECT payload_json FROM report_metadata").fetchone()
            previous = json.loads(row[0]) if row else {}
            with service.usage_store.cost_report_snapshot() as snapshot:
                rebuild = (
                    previous.get("config") != current_config
                    or previous.get("generation") != snapshot.generation
                    or int(previous.get("revision", -1)) > snapshot.revision
                )
                # Context lookups used for request attribution must share the
                # event snapshot, including contexts removed by retention.
                service._context_cache = {}
                service._cost_report_snapshot = snapshot
                try:
                    if rebuild:
                        _rebuild(connection, snapshot, service, catalog, windows)
                    else:
                        _refresh(
                            connection,
                            snapshot,
                            service,
                            catalog,
                            windows,
                            previous,
                            current_attributions,
                        )
                finally:
                    service._cost_report_snapshot = None
                    service._context_cache = {}
                    service._request_attribution_cache = {}
                metadata = {
                    "config": current_config,
                    "generation": snapshot.generation,
                    "revision": snapshot.revision,
                    "attributions": current_attributions,
                }
                if metadata != previous:
                    connection.execute(
                        "INSERT OR REPLACE INTO report_metadata VALUES (1, ?)", (_json(metadata),)
                    )
            cells = [
                (json.loads(row[0]), _amounts(row[1]))
                # Preserve the original first attribution if conflicting
                # historical metadata assigns one project to several teams.
                for row in connection.execute(
                    """SELECT cell_key, amounts_json FROM report_cells AS cells
                       ORDER BY (SELECT first_event_order FROM report_contributions
                                 WHERE cell_key = cells.cell_key ORDER BY first_event_order LIMIT 1)"""
                )
            ]
            connection.commit()
            return cells
        except BaseException:
            connection.rollback()
            raise


def _rebuild(
    connection: sqlite3.Connection, snapshot: CostReportSnapshot, service, catalog, windows
) -> None:
    connection.execute("DELETE FROM report_members")
    connection.execute("DELETE FROM report_contributions")
    connection.execute("DELETE FROM report_cells")
    groups: dict[tuple[str, ...], list[ModelInferenceUsageEvent]] = defaultdict(list)
    for event in snapshot.events():
        groups[_group_key(event)].append(event)
    deltas: dict[str, list[Any]] = {}
    for key, events in groups.items():
        _replace_group(connection, _json(key), events, service, catalog, windows, deltas)
    _apply_deltas(connection, deltas)


def _refresh(
    connection: sqlite3.Connection,
    snapshot: CostReportSnapshot,
    service,
    catalog,
    windows,
    previous,
    attributions,
) -> None:
    event_ids, dispatch_ids = snapshot.changes_since(int(previous["revision"]))
    old_attributions = previous.get("attributions", {})
    sessions = _changed_keys(old_attributions.get("sessions", {}), attributions["sessions"])
    dispatch_ids.update(
        _changed_keys(
            old_attributions.get("dispatches", {}),
            attributions["dispatches"],
        )
    )
    event_ids.update(snapshot.event_ids_for(field="session_id", values=sessions))
    event_ids.update(snapshot.event_ids_for(field="dispatch_id", values=dispatch_ids))
    groups: dict[tuple[str, ...], set[str]] = {}
    current_events: dict[str, ModelInferenceUsageEvent | None] = {}
    for event_id in event_ids:
        old = connection.execute(
            "SELECT group_key FROM report_members WHERE event_id = ?", (event_id,)
        ).fetchone()
        if old:
            groups.setdefault(tuple(json.loads(old[0])), set())
        event = snapshot.event(event_id)
        current_events[event_id] = event
        if event:
            groups.setdefault(_group_key(event), set()).add(event_id)
    # The projection records all request partners, including old source rows
    # whose indexed cost_request_key is NULL. Resolve them by event primary key
    # instead of reparsing an entire persistent session for each changed group.
    for key, member_ids in groups.items():
        member_ids.update(
            row[0]
            for row in connection.execute(
                "SELECT event_id FROM report_members WHERE group_key = ?",
                (_json(key),),
            )
        )
    deltas: dict[str, list[Any]] = {}
    # Remove all old memberships before inserting, since a corrected event may
    # have moved between two groups refreshed in this batch.
    for key in groups:
        connection.execute("DELETE FROM report_members WHERE group_key = ?", (_json(key),))
    for key in sorted(groups):
        events = []
        for event_id in groups[key]:
            if event_id not in current_events:
                current_events[event_id] = snapshot.event(event_id)
            event = current_events[event_id]
            if event is not None and _group_key(event) == key:
                events.append(event)
        events.sort(
            key=lambda event: (
                event.occurred_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
                event.event_id,
            )
        )
        _replace_group(connection, _json(key), events, service, catalog, windows, deltas)
    _apply_deltas(connection, deltas)


def _replace_group(connection, key, events, service, catalog, windows, deltas) -> None:
    for old_key, old_values in connection.execute(
        "SELECT cell_key, amounts_json FROM report_contributions WHERE group_key = ?", (key,)
    ):
        _apply(deltas.setdefault(old_key, _empty_amounts()), _amounts(old_values), -1)
    connection.execute("DELETE FROM report_contributions WHERE group_key = ?", (key,))
    connection.executemany(
        "INSERT INTO report_members VALUES (?, ?)",
        ((event.event_id, key) for event in events),
    )
    for cell_key, (values, first_event_order) in _contributions(
        service, events, catalog, windows
    ).items():
        _apply(deltas.setdefault(cell_key, _empty_amounts()), values)
        connection.execute(
            "INSERT INTO report_contributions VALUES (?, ?, ?, ?)",
            (key, cell_key, _json(values), first_event_order),
        )


def _apply_deltas(connection, deltas) -> None:
    for key, delta in deltas.items():
        row = connection.execute(
            "SELECT amounts_json FROM report_cells WHERE cell_key = ?", (key,)
        ).fetchone()
        current = _amounts(row[0]) if row else _empty_amounts()
        _apply(current, delta)
        if current[0] == 0:
            connection.execute("DELETE FROM report_cells WHERE cell_key = ?", (key,))
        else:
            connection.execute(
                "INSERT OR REPLACE INTO report_cells VALUES (?, ?)",
                (key, _json(current)),
            )


def add_cell_to_bucket(bucket: dict[str, Any], cell: list[Any], amounts: list[Any]) -> None:
    from .costs import _new_leaf

    _, _, provider, model, source, status, _ = cell

    def add(target: dict[str, Any]) -> None:
        for name, value in zip(_COUNTERS, amounts, strict=False):
            target[name] += value
        target["_estimated_cost"] += amounts[-2]
        target["_reported_cost"] += amounts[-1]
        for name, key in (("source_counts", source), ("cost_status_counts", status)):
            target[name][key] = target[name].get(key, 0) + amounts[0]
        if amounts[2]:
            target["_coverage_reasons"].add(status)

    add(bucket)
    add(bucket["provider_breakdown"].setdefault(provider, _new_leaf()))
    add(bucket["model_breakdown"].setdefault(f"{provider}/{model}", _new_leaf()))
