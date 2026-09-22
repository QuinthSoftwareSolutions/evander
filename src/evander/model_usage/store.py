from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from typing import Any, Iterable, Mapping

from evander.model_usage.cost_balances import (
    CostAttribution,
    UsageCostSummary,
    dedupe_model_usage_cost_events,
    metadata_cost_attribution,
    model_usage_request_key,
    request_cost_attribution,
)
from evander.model_usage.models import (
    ModelInferenceUsageEvent,
    ModelUsageCorrelationContext,
)
from evander.model_usage.pricing import (
    ModelPricingCatalog,
    load_model_pricing_catalog_payload,
    model_pricing_catalog_fingerprint,
    model_pricing_catalog_payload,
)
from evander.model_usage.report_snapshot import CostReportSnapshot, ensure_cost_report_tracking

SQLITE_BUSY_TIMEOUT_MS = 30_000
_SQLITE_SUFFIXES = {".db", ".sqlite", ".sqlite3"}


def model_usage_store_db_path(path: Path) -> Path:
    candidate = Path(path)
    if candidate.suffix.lower() in _SQLITE_SUFFIXES:
        return candidate
    return candidate.with_suffix(".db")


def _json_dumps(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _storage_datetime(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _parse_storage_datetime(value: object) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _storage_optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _ensure_columns(
    connection: sqlite3.Connection,
    table_name: str,
    columns: dict[str, str],
) -> None:
    existing = {str(row["name"]) for row in connection.execute(f"PRAGMA table_info({table_name})")}
    for column_name, column_type in columns.items():
        if column_name not in existing:
            connection.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}")


class ModelUsageStore:
    def __init__(self, path: Path) -> None:
        self.path = model_usage_store_db_path(Path(path))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self.ensure_schema()

    def ensure_schema(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS model_usage_events (
                    event_id TEXT PRIMARY KEY,
                    occurred_at TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    provider_endpoint_identifier TEXT NOT NULL,
                    model TEXT,
                    deployment TEXT,
                    input_tokens INTEGER,
                    output_tokens INTEGER,
                    total_tokens INTEGER,
                    request_status TEXT NOT NULL,
                    duration_ms INTEGER,
                    normalized_usage_status TEXT NOT NULL,
                    raw_usage_metadata_json TEXT NOT NULL,
                    request_body_bytes INTEGER,
                    request_body_sha256 TEXT,
                    response_body_bytes INTEGER,
                    response_body_sha256 TEXT,
                    session_id TEXT,
                    workflow_run_id TEXT,
                    effect_id TEXT,
                    node_id TEXT,
                    dispatch_id TEXT,
                    agent_harness TEXT,
                    execution_backend TEXT,
                    directory_path TEXT,
                    trace_id TEXT,
                    request_id TEXT,
                    cost_request_key TEXT,
                    payload_json TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_model_usage_events_occurred_at
                    ON model_usage_events(occurred_at, event_id);
                CREATE INDEX IF NOT EXISTS idx_model_usage_events_session_id
                    ON model_usage_events(session_id, occurred_at, event_id);
                CREATE INDEX IF NOT EXISTS idx_model_usage_events_workflow_run_id
                    ON model_usage_events(workflow_run_id, occurred_at, event_id);
                CREATE INDEX IF NOT EXISTS idx_model_usage_events_effect_id
                    ON model_usage_events(effect_id, occurred_at, event_id);
                CREATE INDEX IF NOT EXISTS idx_model_usage_events_node_id
                    ON model_usage_events(node_id, occurred_at, event_id);
                CREATE INDEX IF NOT EXISTS idx_model_usage_events_dispatch_id
                    ON model_usage_events(dispatch_id, occurred_at, event_id);
                CREATE INDEX IF NOT EXISTS idx_model_usage_events_directory_path
                    ON model_usage_events(directory_path, occurred_at, event_id);
                CREATE INDEX IF NOT EXISTS idx_model_usage_events_provider
                    ON model_usage_events(provider, occurred_at, event_id);
                CREATE INDEX IF NOT EXISTS idx_model_usage_events_model
                    ON model_usage_events(model, occurred_at, event_id);
                CREATE INDEX IF NOT EXISTS idx_model_usage_events_agent_harness
                    ON model_usage_events(agent_harness, occurred_at, event_id);
                CREATE INDEX IF NOT EXISTS idx_model_usage_events_execution_backend
                    ON model_usage_events(execution_backend, occurred_at, event_id);
                CREATE INDEX IF NOT EXISTS idx_model_usage_events_request_status
                    ON model_usage_events(request_status, occurred_at, event_id);
                CREATE INDEX IF NOT EXISTS idx_model_usage_events_usage_status
                    ON model_usage_events(normalized_usage_status, occurred_at, event_id);

                CREATE TABLE IF NOT EXISTS model_usage_correlation_contexts (
                    dispatch_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    workflow_run_id TEXT,
                    effect_id TEXT,
                    node_id TEXT,
                    agent_harness TEXT NOT NULL,
                    execution_backend TEXT,
                    directory_path TEXT,
                    model TEXT,
                    team_id TEXT,
                    project_id TEXT,
                    agent_id TEXT,
                    trace_id TEXT,
                    started_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_model_usage_contexts_session_harness
                    ON model_usage_correlation_contexts(session_id, agent_harness, started_at, dispatch_id);
                CREATE INDEX IF NOT EXISTS idx_model_usage_contexts_expires_at
                    ON model_usage_correlation_contexts(expires_at, dispatch_id);
                CREATE INDEX IF NOT EXISTS idx_model_usage_contexts_workflow_run_id
                    ON model_usage_correlation_contexts(workflow_run_id, started_at, dispatch_id);
                CREATE INDEX IF NOT EXISTS idx_model_usage_contexts_effect_id
                    ON model_usage_correlation_contexts(effect_id, started_at, dispatch_id);
                CREATE INDEX IF NOT EXISTS idx_model_usage_contexts_execution_backend
                    ON model_usage_correlation_contexts(execution_backend, started_at, dispatch_id);
                CREATE INDEX IF NOT EXISTS idx_model_usage_contexts_directory_path
                    ON model_usage_correlation_contexts(directory_path, started_at, dispatch_id);

                CREATE TABLE IF NOT EXISTS model_usage_cost_attributions (
                    session_id TEXT PRIMARY KEY,
                    team_id TEXT,
                    project_id TEXT,
                    agent_id TEXT,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS model_usage_cost_balances (
                    dimension_kind TEXT NOT NULL,
                    dimension_id TEXT NOT NULL,
                    window_id TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    ended_at TEXT,
                    pricing_fingerprint TEXT NOT NULL,
                    pricing_catalog_json TEXT NOT NULL,
                    balance_json TEXT NOT NULL,
                    dirty_reason TEXT,
                    reconciled_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(dimension_kind, dimension_id, window_id)
                );

                CREATE INDEX IF NOT EXISTS idx_model_usage_cost_balances_window
                    ON model_usage_cost_balances(dimension_kind, started_at, ended_at, dimension_id);
                """
            )
            _ensure_columns(
                connection,
                "model_usage_events",
                {"cost_request_key": "TEXT"},
            )
            _ensure_columns(
                connection,
                "model_usage_correlation_contexts",
                {
                    "team_id": "TEXT",
                    "project_id": "TEXT",
                    "agent_id": "TEXT",
                },
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_model_usage_events_cost_request
                    ON model_usage_events(session_id, cost_request_key, occurred_at, event_id)
                """
            )
            ensure_cost_report_tracking(connection)

    @contextmanager
    def cost_report_snapshot(self) -> Iterator[CostReportSnapshot]:
        # WAL readers do not block relay writes. All report inputs (including
        # correlation contexts) must come from this same committed revision.
        with self._connect() as connection:
            connection.execute("BEGIN")
            yield CostReportSnapshot(connection)

    def upsert_event(self, event: ModelInferenceUsageEvent) -> None:
        validated = ModelInferenceUsageEvent.model_validate(event)
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing_row = connection.execute(
                "SELECT payload_json FROM model_usage_events WHERE event_id = ?",
                (validated.event_id,),
            ).fetchone()
            existing = (
                ModelInferenceUsageEvent.model_validate_json(existing_row["payload_json"])
                if existing_row is not None
                else None
            )
            affected_keys = {
                self._incremental_event_key(candidate)
                for candidate in (existing, validated)
                if candidate is not None
            }
            before_groups = {
                key: self._incremental_event_group(connection, key) for key in affected_keys
            }
            connection.execute(
                """
                INSERT INTO model_usage_events(
                    event_id,
                    occurred_at,
                    provider,
                    provider_endpoint_identifier,
                    model,
                    deployment,
                    input_tokens,
                    output_tokens,
                    total_tokens,
                    request_status,
                    duration_ms,
                    normalized_usage_status,
                    raw_usage_metadata_json,
                    request_body_bytes,
                    request_body_sha256,
                    response_body_bytes,
                    response_body_sha256,
                    session_id,
                    workflow_run_id,
                    effect_id,
                    node_id,
                    dispatch_id,
                    agent_harness,
                    execution_backend,
                    directory_path,
                    trace_id,
                    request_id,
                    cost_request_key,
                    payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(event_id) DO UPDATE SET
                    occurred_at = excluded.occurred_at,
                    provider = excluded.provider,
                    provider_endpoint_identifier = excluded.provider_endpoint_identifier,
                    model = excluded.model,
                    deployment = excluded.deployment,
                    input_tokens = excluded.input_tokens,
                    output_tokens = excluded.output_tokens,
                    total_tokens = excluded.total_tokens,
                    request_status = excluded.request_status,
                    duration_ms = excluded.duration_ms,
                    normalized_usage_status = excluded.normalized_usage_status,
                    raw_usage_metadata_json = excluded.raw_usage_metadata_json,
                    request_body_bytes = excluded.request_body_bytes,
                    request_body_sha256 = excluded.request_body_sha256,
                    response_body_bytes = excluded.response_body_bytes,
                    response_body_sha256 = excluded.response_body_sha256,
                    session_id = excluded.session_id,
                    workflow_run_id = excluded.workflow_run_id,
                    effect_id = excluded.effect_id,
                    node_id = excluded.node_id,
                    dispatch_id = excluded.dispatch_id,
                    agent_harness = excluded.agent_harness,
                    execution_backend = excluded.execution_backend,
                    directory_path = excluded.directory_path,
                    trace_id = excluded.trace_id,
                    request_id = excluded.request_id,
                    cost_request_key = excluded.cost_request_key,
                    payload_json = excluded.payload_json
                """,
                self._event_row(validated),
            )
            after_groups = {
                key: self._incremental_event_group(connection, key) for key in affected_keys
            }
            self._apply_incremental_cost_balance_changes(
                connection,
                before_groups=before_groups,
                after_groups=after_groups,
            )

    def replace_cost_balance(
        self,
        *,
        dimension_kind: str,
        dimension_id: str,
        window_id: str,
        started_at: datetime,
        ended_at: datetime | None,
        pricing_catalog: ModelPricingCatalog,
        summary: UsageCostSummary,
        reconciled_at: datetime | None = None,
    ) -> dict[str, Any]:
        normalized_kind = _storage_optional_text(dimension_kind)
        normalized_id = _storage_optional_text(dimension_id)
        normalized_window_id = _storage_optional_text(window_id)
        if normalized_kind is None or normalized_id is None or normalized_window_id is None:
            raise ValueError("cost balance dimension and window identifiers are required")
        effective_reconciled_at = reconciled_at or datetime.now(UTC)
        pricing_payload = model_pricing_catalog_payload(pricing_catalog)
        pricing_fingerprint = model_pricing_catalog_fingerprint(pricing_catalog)
        now_value = _storage_datetime(datetime.now(UTC))
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = self._replace_cost_balance_row(
                connection,
                dimension_kind=normalized_kind,
                dimension_id=normalized_id,
                window_id=normalized_window_id,
                started_at=started_at,
                ended_at=ended_at,
                pricing_payload=pricing_payload,
                pricing_fingerprint=pricing_fingerprint,
                summary=summary,
                reconciled_at=effective_reconciled_at,
                updated_at=now_value,
            )
        return self._cost_balance_payload(row)

    def reconcile_team_cost_balance(
        self,
        *,
        dimension_kind: str,
        team_id: str,
        window_id: str,
        started_at: datetime,
        ended_at: datetime | None,
        pricing_catalog: ModelPricingCatalog,
        reconciled_at: datetime | None = None,
    ) -> dict[str, Any]:
        """Atomically rebuild one Team balance from its bounded event interval.

        Holding a SQLite write transaction across the event snapshot and balance
        replacement ensures a concurrent event either participates in this rebuild
        or applies its incremental delta after the replacement commits.
        """
        normalized_kind = _storage_optional_text(dimension_kind)
        normalized_team_id = _storage_optional_text(team_id)
        normalized_window_id = _storage_optional_text(window_id)
        if normalized_kind is None or normalized_team_id is None or normalized_window_id is None:
            raise ValueError("cost balance dimension and window identifiers are required")
        effective_reconciled_at = reconciled_at or datetime.now(UTC)
        pricing_payload = model_pricing_catalog_payload(pricing_catalog)
        pricing_fingerprint = model_pricing_catalog_fingerprint(pricing_catalog)
        now_value = _storage_datetime(datetime.now(UTC))
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            clauses = ["occurred_at >= ?"]
            params: list[Any] = [_storage_datetime(started_at)]
            if ended_at is not None:
                clauses.append("occurred_at < ?")
                params.append(_storage_datetime(ended_at))
            rows = connection.execute(
                """
                SELECT payload_json
                FROM model_usage_events
                WHERE """
                + " AND ".join(clauses)
                + " ORDER BY occurred_at, event_id",
                params,
            ).fetchall()
            bounded_events = [
                ModelInferenceUsageEvent.model_validate_json(row["payload_json"]) for row in rows
            ]
            for event in bounded_events:
                request_key = (model_usage_request_key(event) or (None, None))[1]
                connection.execute(
                    """
                    UPDATE model_usage_events
                    SET cost_request_key = ?
                    WHERE event_id = ? AND cost_request_key IS NOT ?
                    """,
                    (request_key, event.event_id, request_key),
                )

            summary = UsageCostSummary()
            affected_keys = {self._incremental_event_key(event) for event in bounded_events}
            for key in sorted(affected_keys):
                summary.apply(
                    self._team_summary_for_group(
                        connection,
                        self._incremental_event_group(connection, key),
                        team_id=normalized_team_id,
                        started_at=started_at,
                        ended_at=ended_at,
                        catalog=pricing_catalog,
                    )
                )
            row = self._replace_cost_balance_row(
                connection,
                dimension_kind=normalized_kind,
                dimension_id=normalized_team_id,
                window_id=normalized_window_id,
                started_at=started_at,
                ended_at=ended_at,
                pricing_payload=pricing_payload,
                pricing_fingerprint=pricing_fingerprint,
                summary=summary,
                reconciled_at=effective_reconciled_at,
                updated_at=now_value,
            )
        return self._cost_balance_payload(row)

    def get_cost_balance(
        self,
        *,
        dimension_kind: str,
        dimension_id: str,
        window_id: str,
    ) -> dict[str, Any] | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM model_usage_cost_balances
                WHERE dimension_kind = ? AND dimension_id = ? AND window_id = ?
                """,
                (dimension_kind, dimension_id, window_id),
            ).fetchone()
        return self._cost_balance_payload(row) if row is not None else None

    @staticmethod
    def _replace_cost_balance_row(
        connection: sqlite3.Connection,
        *,
        dimension_kind: str,
        dimension_id: str,
        window_id: str,
        started_at: datetime,
        ended_at: datetime | None,
        pricing_payload: Mapping[str, Any],
        pricing_fingerprint: str,
        summary: UsageCostSummary,
        reconciled_at: datetime,
        updated_at: str | None,
    ) -> sqlite3.Row:
        connection.execute(
            """
            DELETE FROM model_usage_cost_balances
            WHERE dimension_kind = ? AND dimension_id = ? AND window_id != ?
            """,
            (dimension_kind, dimension_id, window_id),
        )
        connection.execute(
            """
            INSERT INTO model_usage_cost_balances(
                dimension_kind,
                dimension_id,
                window_id,
                started_at,
                ended_at,
                pricing_fingerprint,
                pricing_catalog_json,
                balance_json,
                dirty_reason,
                reconciled_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)
            ON CONFLICT(dimension_kind, dimension_id, window_id) DO UPDATE SET
                started_at = excluded.started_at,
                ended_at = excluded.ended_at,
                pricing_fingerprint = excluded.pricing_fingerprint,
                pricing_catalog_json = excluded.pricing_catalog_json,
                balance_json = excluded.balance_json,
                dirty_reason = NULL,
                reconciled_at = excluded.reconciled_at,
                updated_at = excluded.updated_at
            """,
            (
                dimension_kind,
                dimension_id,
                window_id,
                _storage_datetime(started_at),
                _storage_datetime(ended_at),
                pricing_fingerprint,
                _json_dumps(pricing_payload),
                _json_dumps(summary.storage_payload()),
                _storage_datetime(reconciled_at),
                updated_at,
            ),
        )
        row = connection.execute(
            """
            SELECT *
            FROM model_usage_cost_balances
            WHERE dimension_kind = ? AND dimension_id = ? AND window_id = ?
            """,
            (dimension_kind, dimension_id, window_id),
        ).fetchone()
        if row is None:  # pragma: no cover - defensive write invariant
            raise RuntimeError("cost balance write did not persist")
        return row

    def upsert_cost_attribution(
        self,
        *,
        session_id: str,
        team_id: str | None,
        project_id: str | None = None,
        agent_id: str | None = None,
    ) -> None:
        self.upsert_cost_attributions(
            [
                {
                    "session_id": session_id,
                    "team_id": team_id,
                    "project_id": project_id,
                    "agent_id": agent_id,
                }
            ]
        )

    def upsert_cost_attributions(
        self,
        attributions: Iterable[Mapping[str, object]],
    ) -> int:
        normalized: dict[str, tuple[str | None, str | None, str | None]] = {}
        for attribution in attributions:
            session_id = _storage_optional_text(attribution.get("session_id"))
            if session_id is None:
                raise ValueError("session_id is required")
            normalized[session_id] = (
                _storage_optional_text(attribution.get("team_id")),
                _storage_optional_text(attribution.get("project_id")),
                _storage_optional_text(attribution.get("agent_id")),
            )
        if not normalized:
            return 0
        now_value = _storage_datetime(datetime.now(UTC))
        changed = 0
        with self._lock, self._connect() as connection:
            existing_rows = connection.execute(
                "SELECT session_id, team_id, project_id, agent_id FROM model_usage_cost_attributions"
            ).fetchall()
            existing = {
                row["session_id"]: (row["team_id"], row["project_id"], row["agent_id"])
                for row in existing_rows
            }
            affected_team_ids: set[str] = set()
            pending: list[
                tuple[
                    str,
                    tuple[str | None, str | None, str | None],
                    tuple[str | None, str | None, str | None] | None,
                ]
            ] = []
            for session_id, new_values in normalized.items():
                old_values = existing.get(session_id)
                if old_values == new_values:
                    continue
                pending.append((session_id, new_values, old_values))
                affected_team_ids.update(
                    candidate
                    for candidate in (
                        old_values[0] if old_values is not None else None,
                        new_values[0],
                    )
                    if candidate
                )
            if not pending:
                return 0
            connection.execute("BEGIN IMMEDIATE")
            for session_id, new_values, _old_values in pending:
                connection.execute(
                    """
                    INSERT INTO model_usage_cost_attributions(
                        session_id, team_id, project_id, agent_id, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(session_id) DO UPDATE SET
                        team_id = excluded.team_id,
                        project_id = excluded.project_id,
                        agent_id = excluded.agent_id,
                        updated_at = excluded.updated_at
                    """,
                    (session_id, *new_values, now_value),
                )
                changed += 1
            for affected_team_id in affected_team_ids:
                connection.execute(
                    """
                    UPDATE model_usage_cost_balances
                    SET dirty_reason = 'session_attribution_changed', updated_at = ?
                    WHERE dimension_kind IN ('team', 'team_lifetime') AND dimension_id = ?
                    """,
                    (now_value, affected_team_id),
                )
        return changed

    def backfill_cost_request_keys(
        self,
        events: Iterable[ModelInferenceUsageEvent],
    ) -> int:
        updates = [
            ((model_usage_request_key(event) or (None, None))[1], event.event_id)
            for event in events
        ]
        if not updates:
            return 0
        changed = 0
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            for request_key, event_id in updates:
                cursor = connection.execute(
                    """
                    UPDATE model_usage_events
                    SET cost_request_key = ?
                    WHERE event_id = ? AND cost_request_key IS NOT ?
                    """,
                    (request_key, event_id, request_key),
                )
                changed += int(cursor.rowcount or 0)
        return changed

    def get_event(self, event_id: object) -> ModelInferenceUsageEvent | None:
        normalized_event_id = _storage_optional_text(event_id)
        if normalized_event_id is None:
            return None
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT payload_json
                FROM model_usage_events
                WHERE event_id = ?
                """,
                (normalized_event_id,),
            ).fetchone()
        if row is None:
            return None
        return ModelInferenceUsageEvent.model_validate_json(row["payload_json"])

    def list_events(
        self,
        *,
        session_id: object | None = None,
        workflow_run_id: object | None = None,
        effect_id: object | None = None,
        node_id: object | None = None,
        dispatch_id: object | None = None,
        directory_path: object | None = None,
        include_directory_descendants: bool = False,
        provider: object | None = None,
        model: object | None = None,
        agent_harness: object | None = None,
        execution_backend: object | None = None,
        normalized_usage_status: object | None = None,
        request_status: object | None = None,
        occurred_after: datetime | None = None,
        occurred_before: datetime | None = None,
        limit: int | None = None,
    ) -> list[ModelInferenceUsageEvent]:
        sql = ["SELECT payload_json FROM model_usage_events WHERE 1=1"]
        params: list[Any] = []

        def add_condition(condition: str, *values: Any) -> None:
            sql.append(f" AND {condition}")
            params.extend(values)

        if (normalized := _storage_optional_text(session_id)) is not None:
            add_condition("session_id = ?", normalized)
        if (normalized := _storage_optional_text(workflow_run_id)) is not None:
            add_condition("workflow_run_id = ?", normalized)
        if (normalized := _storage_optional_text(effect_id)) is not None:
            add_condition("effect_id = ?", normalized)
        if (normalized := _storage_optional_text(node_id)) is not None:
            add_condition("node_id = ?", normalized)
        if (normalized := _storage_optional_text(dispatch_id)) is not None:
            add_condition("dispatch_id = ?", normalized)
        if (normalized := _storage_optional_text(provider)) is not None:
            add_condition("provider = ?", normalized)
        if (normalized := _storage_optional_text(model)) is not None:
            add_condition("model = ?", normalized)
        if (normalized := _storage_optional_text(agent_harness)) is not None:
            add_condition("agent_harness = ?", normalized)
        if (normalized := _storage_optional_text(execution_backend)) is not None:
            add_condition("execution_backend = ?", normalized)
        if (normalized := _storage_optional_text(normalized_usage_status)) is not None:
            add_condition("normalized_usage_status = ?", normalized)
        if (normalized := _storage_optional_text(request_status)) is not None:
            add_condition("request_status = ?", normalized)
        if (normalized := _storage_optional_text(directory_path)) is not None:
            if include_directory_descendants:
                escaped = _escape_like(normalized)
                add_condition(
                    "(directory_path = ? OR directory_path LIKE ? ESCAPE '\\')",
                    normalized,
                    f"{escaped}/%",
                )
            else:
                add_condition("directory_path = ?", normalized)
        if occurred_after is not None:
            add_condition("occurred_at >= ?", _storage_datetime(occurred_after))
        if occurred_before is not None:
            add_condition("occurred_at <= ?", _storage_datetime(occurred_before))

        sql.append(" ORDER BY occurred_at ASC, event_id ASC")
        if limit is not None:
            sql.append(" LIMIT ?")
            params.append(int(limit))

        query = "".join(sql)
        with self._lock, self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [ModelInferenceUsageEvent.model_validate_json(row["payload_json"]) for row in rows]

    def sum_input_tokens(
        self,
        *,
        session_id: object,
        occurred_after: datetime,
    ) -> int | None:
        """Return exact input tokens after a boundary, or ``None`` when any are unknown."""
        normalized_session_id = _storage_optional_text(session_id)
        if normalized_session_id is None:
            return 0
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT
                    COALESCE(SUM(input_tokens), 0) AS total_input_tokens,
                    COALESCE(
                        SUM(
                            CASE
                                WHEN normalized_usage_status != 'available'
                                    OR input_tokens IS NULL
                                THEN 1
                                ELSE 0
                            END
                        ),
                        0
                    ) AS unknown_input_token_events
                FROM model_usage_events
                WHERE session_id = ? AND occurred_at > ?
                """,
                (normalized_session_id, _storage_datetime(occurred_after)),
            ).fetchone()
        if row is not None and int(row["unknown_input_token_events"] or 0) > 0:
            return None
        return int(row["total_input_tokens"] or 0) if row is not None else 0

    def upsert_correlation_context(self, context: ModelUsageCorrelationContext) -> None:
        validated = ModelUsageCorrelationContext.model_validate(context)
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            affected_event_rows = connection.execute(
                """
                SELECT payload_json
                FROM model_usage_events
                WHERE dispatch_id = ?
                """,
                (validated.dispatch_id,),
            ).fetchall()
            affected_keys = {
                self._incremental_event_key(
                    ModelInferenceUsageEvent.model_validate_json(row["payload_json"])
                )
                for row in affected_event_rows
            }
            before_groups = {
                key: self._incremental_event_group(connection, key) for key in affected_keys
            }
            existing_row = connection.execute(
                """
                SELECT payload_json
                FROM model_usage_correlation_contexts
                WHERE dispatch_id = ?
                """,
                (validated.dispatch_id,),
            ).fetchone()
            if existing_row is not None:
                existing = ModelUsageCorrelationContext.model_validate_json(
                    existing_row["payload_json"]
                )
                validated = validated.model_copy(
                    update={
                        field_name: getattr(existing, field_name)
                        for field_name in ("team_id", "project_id", "agent_id")
                        if getattr(validated, field_name) is None
                        and getattr(existing, field_name) is not None
                    }
                )
            connection.execute(
                """
                INSERT INTO model_usage_correlation_contexts(
                    dispatch_id,
                    session_id,
                    workflow_run_id,
                    effect_id,
                    node_id,
                    agent_harness,
                    execution_backend,
                    directory_path,
                    model,
                    team_id,
                    project_id,
                    agent_id,
                    trace_id,
                    started_at,
                    expires_at,
                    payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(dispatch_id) DO UPDATE SET
                    session_id = excluded.session_id,
                    workflow_run_id = excluded.workflow_run_id,
                    effect_id = excluded.effect_id,
                    node_id = excluded.node_id,
                    agent_harness = excluded.agent_harness,
                    execution_backend = excluded.execution_backend,
                    directory_path = excluded.directory_path,
                    model = excluded.model,
                    team_id = excluded.team_id,
                    project_id = excluded.project_id,
                    agent_id = excluded.agent_id,
                    trace_id = excluded.trace_id,
                    started_at = excluded.started_at,
                    expires_at = excluded.expires_at,
                    payload_json = excluded.payload_json
                """,
                self._context_row(validated),
            )
            after_groups = {
                key: self._incremental_event_group(connection, key) for key in affected_keys
            }
            self._apply_incremental_cost_balance_changes(
                connection,
                before_groups=before_groups,
                after_groups=after_groups,
            )

    def get_active_correlation_context(
        self,
        *,
        session_id: object,
        agent_harness: object | None = None,
        execution_backend: object | None = None,
        dispatch_id: object | None = None,
        now_utc: datetime | None = None,
    ) -> ModelUsageCorrelationContext | None:
        normalized_session_id = _storage_optional_text(session_id)
        if normalized_session_id is None:
            return None
        normalized_agent_harness = _storage_optional_text(agent_harness)
        normalized_execution_backend = _storage_optional_text(execution_backend)
        normalized_dispatch_id = _storage_optional_text(dispatch_id)
        now = now_utc or datetime.now(UTC)
        now_value = _storage_datetime(now)

        sql = [
            "SELECT payload_json FROM model_usage_correlation_contexts WHERE session_id = ?",
        ]
        params: list[Any] = [normalized_session_id]
        if normalized_dispatch_id is not None:
            sql.append(" AND dispatch_id = ?")
            params.append(normalized_dispatch_id)
        if normalized_agent_harness is not None:
            sql.append(" AND agent_harness = ?")
            params.append(normalized_agent_harness)
        if normalized_execution_backend is not None:
            sql.append(" AND execution_backend = ?")
            params.append(normalized_execution_backend)
        sql.append(" AND expires_at > ?")
        params.append(now_value)
        sql.append(" ORDER BY started_at DESC, dispatch_id DESC LIMIT 1")

        query = "".join(sql)
        with self._lock, self._connect() as connection:
            row = connection.execute(query, params).fetchone()
            if row is not None:
                return ModelUsageCorrelationContext.model_validate_json(row["payload_json"])
        return None

    def get_correlation_context(
        self,
        dispatch_id: object,
    ) -> ModelUsageCorrelationContext | None:
        normalized_dispatch_id = _storage_optional_text(dispatch_id)
        if normalized_dispatch_id is None:
            return None
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT payload_json
                FROM model_usage_correlation_contexts
                WHERE dispatch_id = ?
                """,
                (normalized_dispatch_id,),
            ).fetchone()
        if row is None:
            return None
        return ModelUsageCorrelationContext.model_validate_json(row["payload_json"])

    def clear_correlation_context(
        self,
        *,
        dispatch_id: object | None = None,
        session_id: object | None = None,
        agent_harness: object | None = None,
        execution_backend: object | None = None,
    ) -> int:
        clauses = []
        params: list[Any] = []
        if (normalized := _storage_optional_text(dispatch_id)) is not None:
            clauses.append("dispatch_id = ?")
            params.append(normalized)
        if (normalized := _storage_optional_text(session_id)) is not None:
            clauses.append("session_id = ?")
            params.append(normalized)
        if (normalized := _storage_optional_text(agent_harness)) is not None:
            clauses.append("agent_harness = ?")
            params.append(normalized)
        if (normalized := _storage_optional_text(execution_backend)) is not None:
            clauses.append("execution_backend = ?")
            params.append(normalized)
        if not clauses:
            return 0

        statement = "DELETE FROM model_usage_correlation_contexts WHERE " + " AND ".join(clauses)
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(statement, params)
            return int(cursor.rowcount or 0)

    def prune_expired_correlation_contexts(self, *, now_utc: datetime | None = None) -> int:
        now_value = _storage_datetime(now_utc or datetime.now(UTC))
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                DELETE FROM model_usage_correlation_contexts
                WHERE expires_at <= ?
                """,
                (now_value,),
            )
            return int(cursor.rowcount or 0)

    @staticmethod
    def _incremental_event_key(event: ModelInferenceUsageEvent) -> tuple[str, ...]:
        request_key = model_usage_request_key(event)
        if request_key is not None:
            return "request", request_key[0], request_key[1]
        return "event", event.event_id

    @staticmethod
    def _incremental_event_group(
        connection: sqlite3.Connection,
        key: tuple[str, ...],
    ) -> list[ModelInferenceUsageEvent]:
        if key[0] == "request":
            rows = connection.execute(
                """
                SELECT payload_json
                FROM model_usage_events
                WHERE session_id = ? AND cost_request_key = ?
                ORDER BY occurred_at, event_id
                """,
                (key[1], key[2]),
            ).fetchall()
        else:
            rows = connection.execute(
                "SELECT payload_json FROM model_usage_events WHERE event_id = ?",
                (key[1],),
            ).fetchall()
        return [ModelInferenceUsageEvent.model_validate_json(row["payload_json"]) for row in rows]

    def _apply_incremental_cost_balance_changes(
        self,
        connection: sqlite3.Connection,
        *,
        before_groups: Mapping[tuple[str, ...], list[ModelInferenceUsageEvent]],
        after_groups: Mapping[tuple[str, ...], list[ModelInferenceUsageEvent]],
    ) -> None:
        affected_team_ids = {
            team_id
            for groups in (before_groups, after_groups)
            for events in groups.values()
            for team_id in self._team_ids_for_group(connection, events)
        }
        if not affected_team_ids:
            return
        placeholders = ",".join("?" for _ in affected_team_ids)
        balance_rows = connection.execute(
            f"""
            SELECT *
            FROM model_usage_cost_balances
            WHERE dimension_kind IN ('team', 'team_lifetime')
                AND dirty_reason IS NULL
                AND dimension_id IN ({placeholders})
            """,  # noqa: S608 - placeholders are generated, values remain bound
            sorted(affected_team_ids),
        ).fetchall()
        if not balance_rows:
            return
        now_value = _storage_datetime(datetime.now(UTC))
        for row in balance_rows:
            try:
                catalog = load_model_pricing_catalog_payload(
                    json.loads(row["pricing_catalog_json"])
                )
                started_at = _parse_storage_datetime(row["started_at"])
                ended_at = (
                    _parse_storage_datetime(row["ended_at"])
                    if row["ended_at"] is not None
                    else None
                )
                before = UsageCostSummary()
                after = UsageCostSummary()
                for events in before_groups.values():
                    before.apply(
                        self._team_summary_for_group(
                            connection,
                            events,
                            team_id=row["dimension_id"],
                            started_at=started_at,
                            ended_at=ended_at,
                            catalog=catalog,
                        )
                    )
                for events in after_groups.values():
                    after.apply(
                        self._team_summary_for_group(
                            connection,
                            events,
                            team_id=row["dimension_id"],
                            started_at=started_at,
                            ended_at=ended_at,
                            catalog=catalog,
                        )
                    )
                if before.storage_payload() == after.storage_payload():
                    continue
                current = UsageCostSummary.from_storage_payload(json.loads(row["balance_json"]))
                current.apply(before, sign=-1)
                current.apply(after)
                connection.execute(
                    """
                    UPDATE model_usage_cost_balances
                    SET balance_json = ?, updated_at = ?
                    WHERE dimension_kind = ? AND dimension_id = ? AND window_id = ?
                    """,
                    (
                        _json_dumps(current.storage_payload()),
                        now_value,
                        row["dimension_kind"],
                        row["dimension_id"],
                        row["window_id"],
                    ),
                )
            except Exception as exc:
                connection.execute(
                    """
                    UPDATE model_usage_cost_balances
                    SET dirty_reason = ?, updated_at = ?
                    WHERE dimension_kind = ? AND dimension_id = ? AND window_id = ?
                    """,
                    (
                        f"incremental_update_failed:{exc.__class__.__name__}",
                        now_value,
                        row["dimension_kind"],
                        row["dimension_id"],
                        row["window_id"],
                    ),
                )

    def _team_ids_for_group(
        self,
        connection: sqlite3.Connection,
        events: Iterable[ModelInferenceUsageEvent],
    ) -> set[str]:
        materialized = list(events)
        if not materialized:
            return set()
        request_attribution = (
            request_cost_attribution(
                materialized,
                fallback=lambda event: self._stored_cost_attribution(connection, event),
            )
            if any(model_usage_request_key(event) is not None for event in materialized)
            else None
        )
        team_ids: set[str] = set()
        for event in dedupe_model_usage_cost_events(materialized):
            attribution = (
                request_attribution
                or metadata_cost_attribution(event)
                or self._stored_cost_attribution(connection, event)
            )
            if attribution is not None and attribution.team_id:
                team_ids.add(attribution.team_id)
        return team_ids

    def _team_summary_for_group(
        self,
        connection: sqlite3.Connection,
        events: Iterable[ModelInferenceUsageEvent],
        *,
        team_id: str,
        started_at: datetime,
        ended_at: datetime | None,
        catalog: ModelPricingCatalog,
    ) -> UsageCostSummary:
        materialized = list(events)
        summary = UsageCostSummary()
        if not materialized:
            return summary
        request_attribution = (
            request_cost_attribution(
                materialized,
                fallback=lambda event: self._stored_cost_attribution(connection, event),
            )
            if any(model_usage_request_key(event) is not None for event in materialized)
            else None
        )
        for event in dedupe_model_usage_cost_events(materialized):
            occurred_at = event.occurred_at.astimezone(UTC)
            if occurred_at < started_at or (ended_at is not None and occurred_at >= ended_at):
                continue
            attribution = (
                request_attribution
                or metadata_cost_attribution(event)
                or self._stored_cost_attribution(connection, event)
            )
            if attribution is None or attribution.team_id != team_id:
                continue
            summary.add_event(event, catalog)
        return summary

    @staticmethod
    def _stored_cost_attribution(
        connection: sqlite3.Connection,
        event: ModelInferenceUsageEvent,
    ) -> CostAttribution | None:
        if event.dispatch_id:
            row = connection.execute(
                """
                SELECT team_id, project_id, agent_id
                FROM model_usage_correlation_contexts
                WHERE dispatch_id = ?
                """,
                (event.dispatch_id,),
            ).fetchone()
            if row is not None and any(row[key] for key in ("team_id", "project_id", "agent_id")):
                return CostAttribution(
                    team_id=row["team_id"],
                    project_id=row["project_id"],
                    agent_id=row["agent_id"],
                    source="dispatch_correlation_context",
                )
        if event.session_id:
            row = connection.execute(
                """
                SELECT team_id, project_id, agent_id
                FROM model_usage_cost_attributions
                WHERE session_id = ?
                """,
                (event.session_id,),
            ).fetchone()
            if row is not None and any(row[key] for key in ("team_id", "project_id", "agent_id")):
                return CostAttribution(
                    team_id=row["team_id"],
                    project_id=row["project_id"],
                    agent_id=row["agent_id"],
                    source="session_attribution",
                )
        return None

    @staticmethod
    def _cost_balance_payload(row: sqlite3.Row) -> dict[str, Any]:
        summary = UsageCostSummary.from_storage_payload(json.loads(row["balance_json"]))
        return {
            "dimension_kind": row["dimension_kind"],
            "dimension_id": row["dimension_id"],
            "window_id": row["window_id"],
            "started_at": _parse_storage_datetime(row["started_at"]),
            "ended_at": (
                _parse_storage_datetime(row["ended_at"]) if row["ended_at"] is not None else None
            ),
            "pricing_fingerprint": row["pricing_fingerprint"],
            "summary": summary,
            "dirty_reason": row["dirty_reason"],
            "reconciled_at": _parse_storage_datetime(row["reconciled_at"]),
            "updated_at": _parse_storage_datetime(row["updated_at"]),
        }

    def _event_row(self, event: ModelInferenceUsageEvent) -> tuple[Any, ...]:
        payload_json = _json_dumps(event.model_dump(mode="json"))
        return (
            event.event_id,
            _storage_datetime(event.occurred_at),
            event.provider,
            event.provider_endpoint_identifier,
            event.model,
            event.deployment,
            event.input_tokens,
            event.output_tokens,
            event.total_tokens,
            event.request_status,
            event.duration_ms,
            event.normalized_usage_status,
            _json_dumps(event.raw_usage_metadata),
            event.request_body_bytes,
            event.request_body_sha256,
            event.response_body_bytes,
            event.response_body_sha256,
            event.session_id,
            event.workflow_run_id,
            event.effect_id,
            event.node_id,
            event.dispatch_id,
            event.agent_harness,
            event.execution_backend,
            event.directory_path,
            event.trace_id,
            event.request_id,
            (model_usage_request_key(event) or (None, None))[1],
            payload_json,
        )

    def _context_row(self, context: ModelUsageCorrelationContext) -> tuple[Any, ...]:
        payload_json = _json_dumps(context.model_dump(mode="json"))
        return (
            context.dispatch_id,
            context.session_id,
            context.workflow_run_id,
            context.effect_id,
            context.node_id,
            context.agent_harness,
            context.execution_backend,
            context.directory_path,
            context.model,
            context.team_id,
            context.project_id,
            context.agent_id,
            context.trace_id,
            _storage_datetime(context.started_at),
            _storage_datetime(context.expires_at),
            payload_json,
        )

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30.0)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA foreign_keys=ON")
            with connection:
                yield connection
        finally:
            connection.close()
