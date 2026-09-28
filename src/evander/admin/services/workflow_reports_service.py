from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel

from evander.admin.common import (
    _coerce_optional_datetime,
    _coerce_optional_string,
    _read_merged_json_objects,
    _utc_now,
)
from evander.admin.payloads import (
    ConformanceReportPayload,
    ConformanceReportRunPayload,
    ConformanceReportsPayload,
    ConformanceRequirementSnippetPayload,
    WorkflowEditorDocumentPayload,
    WorkflowFailureClusterExamplePayload,
    WorkflowFailureClusterPayload,
    WorkflowFailureClusterReportPayload,
    WorkflowFailureClusterSourceKind,
    WorkflowGraphActivityGraphPayload,
    WorkflowGraphActivityNodePayload,
    WorkflowGraphActivityReportPayload,
    WorkflowNodeExecutionDetailPayload,
    WorkflowNodeExecutionFieldsPayload,
    WorkflowNodeExecutionSearchPayload,
    WorkflowRunDetailPayload,
    WorkflowRunLookupPayload,
    WorkflowRunNodeActivityPayload,
    WorkflowRunRecentNodeActivityPayload,
    WorkflowRunSearchFilterPayload,
    WorkflowRunSearchItemPayload,
    WorkflowRunSearchPayload,
    WorkflowRunSnapshotPayload,
    WorkflowRunSummaryPayload,
)
from evander.admin.services.snapshot_cache import SnapshotCache
from evander.admin.workflow_report_helpers import (
    CONFORMANCE_REPORT_RUN_TIMEOUT_SECONDS,
    CONFORMANCE_REPORT_SNIPPET_LIMIT,
    CONFORMANCE_REPORT_SPECS,
    CONFORMANCE_REPORT_STALE_AFTER,
    WORKFLOW_FAILURE_CLUSTER_DEFAULT_RECENT_THRESHOLD_SECONDS,
    WORKFLOW_FAILURE_CLUSTER_EXAMPLE_LIMIT,
    WORKFLOW_FAILURE_CLUSTER_LIMIT_MAX,
    WORKFLOW_FAILURE_CLUSTER_STATE_PRIORITY,
    _as_utc,
    _collect_workflow_artifact_ids,
    _collect_workflow_keyed_values,
    _join_conformance_process_output,
    _merge_run_node_activity,
    _normalize_workflow_failure_signature,
    _trim_conformance_process_output,
    _trim_conformance_text,
)
from evander.communication_artifacts import CommunicationArtifactStore
from evander.models import AgentRecord
from evander.repositories.execution_history import (
    list_node_execution_authorization_paths,
    list_node_execution_fields,
    read_node_execution_authorization_path,
    read_node_execution_detail,
    search_node_executions,
)
from evander.repositories.operational_payloads import load_record
from evander.repositories.orchestration_db import (
    WORKFLOW_REPORT_READ_DEADLINE_SECONDS,
    connect_orchestration_db,
    connect_orchestration_db_read_only,
    datetime_to_storage,
    ensure_orchestration_schema,
)

WORKFLOW_GRAPH_ACTIVITY_SNAPSHOT_TTL_SECONDS = 15.0


@dataclass(slots=True)
class WorkflowReportsAdminDependencies:
    project_dir: Path
    config: Any
    runtime: Any
    workflow_config_admin: Any
    list_agents: Callable[..., list[AgentRecord]]
    project_dir_provider: Callable[[], Path] | None = None


class WorkflowReportsAdminService:
    """Workflow reporting operations."""

    def __init__(self, dependencies: WorkflowReportsAdminDependencies) -> None:
        self._project_dir = dependencies.project_dir
        self._project_dir_provider = dependencies.project_dir_provider
        self.config = dependencies.config
        self.runtime = dependencies.runtime
        self._workflow_config_admin = dependencies.workflow_config_admin
        self._list_agents = dependencies.list_agents
        self._graph_activity_snapshots: SnapshotCache[WorkflowGraphActivityReportPayload] = (
            SnapshotCache(ttl_seconds=WORKFLOW_GRAPH_ACTIVITY_SNAPSHOT_TTL_SECONDS)
        )

    @property
    def project_dir(self) -> Path:
        if self._project_dir_provider is not None:
            return self._project_dir_provider()
        return self._project_dir

    def read_workflow_graph_activity_report(
        self,
        *,
        period_start: datetime | None = None,
        period_end: datetime | None = None,
        limit: int | None = None,
        refresh: bool = False,
    ) -> "WorkflowGraphActivityReportPayload":
        kwargs = {"refresh": True} if refresh else {}
        return self._read_workflow_graph_activity_report(
            period_start=period_start, period_end=period_end, limit=limit, **kwargs
        )

    def read_workflow_failure_clusters_report(
        self,
        *,
        period_start: datetime | None = None,
        period_end: datetime | None = None,
        limit: int | None = None,
        recent_threshold_seconds: int | None = None,
    ) -> "WorkflowFailureClusterReportPayload":
        return self._read_workflow_failure_clusters_report(
            period_start=period_start,
            period_end=period_end,
            limit=limit,
            recent_threshold_seconds=recent_threshold_seconds,
        )

    def read_conformance_reports(self) -> "ConformanceReportsPayload":
        return self._read_conformance_reports()

    def read_workflow_node_execution_search(self, **kwargs: Any):
        return self._read_workflow_node_execution_search(**kwargs)

    def read_workflow_node_execution_fields(self, **kwargs: Any):
        return self._read_workflow_node_execution_fields(**kwargs)

    def read_workflow_node_execution_detail(self, execution_key: str):
        return self._read_workflow_node_execution_detail(execution_key)

    def read_workflow_node_execution_authorization_paths(self, **kwargs: Any):
        return self._read_workflow_node_execution_authorization_paths(**kwargs)

    def read_workflow_node_execution_authorization_path(self, execution_key: str):
        return self._read_workflow_node_execution_authorization_path(execution_key)

    def run_conformance_report(self, kind: str) -> "ConformanceReportRunPayload":
        return self._run_conformance_report(kind)

    def list_agents(self, **kwargs: Any) -> list[AgentRecord]:
        return self._list_agents(**kwargs)

    def _workflow_run_store_paths(self):
        return self._workflow_config_admin._workflow_run_store_paths()

    def _workflow_run_store_bundle(self, *args: Any, **kwargs: Any):
        return self._workflow_config_admin._workflow_run_store_bundle(*args, **kwargs)

    def _workflow_run_event_records(self, *args: Any, **kwargs: Any):
        return self._workflow_config_admin._workflow_run_event_records(*args, **kwargs)

    def _read_composed_workflow_editor_document(self):
        return self._workflow_config_admin._read_composed_workflow_editor_document()

    def _workflow_editor_revision(self, *args: Any, **kwargs: Any):
        return self._workflow_config_admin._workflow_editor_revision(*args, **kwargs)

    def _workflow_config_snapshot_repository(self, *args: Any, **kwargs: Any):
        return self._workflow_config_admin._workflow_config_snapshot_repository(*args, **kwargs)

    def _latest_resumable_failed_workflow_record(self, *args: Any, **kwargs: Any):
        return self._workflow_config_admin._latest_resumable_failed_workflow_record(*args, **kwargs)

    def _communication_artifact_store(self) -> CommunicationArtifactStore:
        return CommunicationArtifactStore(self.config.runtime_dir)

    def _read_workflow_run_snapshot(self) -> WorkflowRunSnapshotPayload:
        stores = self._workflow_run_store_paths()
        now = _utc_now()
        lookback_start = now - timedelta(seconds=8)
        runtime_dir = stores["runtime_dir"]
        runs_by_event: dict[str, dict[str, object]] = {}
        node_activity: dict[str, dict[str, int]] = {}
        recent_node_activity: list[WorkflowRunRecentNodeActivityPayload] = []

        def ensure_run(event_id: str) -> dict[str, object]:
            existing = runs_by_event.get(event_id)
            if existing is not None:
                return existing
            created: dict[str, object] = {
                "event_id": event_id,
                "active_nodes": [],
            }
            runs_by_event[event_id] = created
            return created

        def register_node_activity(
            *,
            event_id: str,
            node_id: str,
            status: str,
            source: str,
            trace_id: str | None = None,
            source_node_id: str | None = None,
            source_trigger_type: str | None = None,
            delivery_state: str | None = None,
            occurred_at: datetime | None = None,
            started_at: datetime | None = None,
            finished_at: datetime | None = None,
            dispatch_id: str | None = None,
            error: str | None = None,
            resumable: bool = False,
        ) -> None:
            normalized_node_id = str(node_id or "").strip()
            if not normalized_node_id:
                return
            run = ensure_run(event_id)
            if trace_id and not run.get("trace_id"):
                run["trace_id"] = trace_id
            if source_node_id and not run.get("source_node_id"):
                run["source_node_id"] = source_node_id
            if source_trigger_type and not run.get("source_trigger_type"):
                run["source_trigger_type"] = source_trigger_type
            if delivery_state and not run.get("delivery_state"):
                run["delivery_state"] = delivery_state
            if occurred_at is not None and run.get("occurred_at") is None:
                run["occurred_at"] = occurred_at
            latest_activity_at = max(
                (value for value in (finished_at, started_at, occurred_at) if value is not None),
                default=None,
            )
            previous_latest = run.get("latest_activity_at")
            if latest_activity_at is not None and (
                not isinstance(previous_latest, datetime) or latest_activity_at > previous_latest
            ):
                run["latest_activity_at"] = latest_activity_at
            if status == "failed":
                run["run_state"] = "errored"
                previous_failed_at = run.get("failed_at")
                if latest_activity_at is not None and (
                    not isinstance(previous_failed_at, datetime)
                    or latest_activity_at >= previous_failed_at
                ):
                    run["failed_node_id"] = normalized_node_id
                    run["failed_node_status"] = status
                    run["failed_at"] = latest_activity_at
                    run["error"] = error
                    run["resumable"] = resumable
            elif status == "error_routed":
                if run.get("run_state") != "errored":
                    run["run_state"] = "error_routed"
                if error is not None and not run.get("error"):
                    run["error"] = error
            elif run.get("run_state") not in {"errored", "error_routed"}:
                run["run_state"] = "active"
            active_nodes = run.setdefault("active_nodes", [])
            assert isinstance(active_nodes, list)
            active_nodes.append(
                WorkflowRunNodeActivityPayload(
                    node_id=normalized_node_id,
                    status=status,
                    source=source,
                    started_at=started_at,
                    finished_at=finished_at,
                    dispatch_id=dispatch_id,
                    error=error,
                )
            )
            node_status_counts = node_activity.setdefault(normalized_node_id, {})
            node_status_counts[status] = node_status_counts.get(status, 0) + 1

        def record_value(record: object, key: str) -> object | None:
            if isinstance(record, Mapping):
                return record.get(key)
            return getattr(record, key, None)

        def normalize_activity_text(value: object) -> str | None:
            return _coerce_optional_string(value)

        def activity_at_from_record(record: object) -> datetime | None:
            return max(
                (
                    timestamp
                    for timestamp in (
                        _coerce_optional_datetime(record_value(record, "finished_at")),
                        _coerce_optional_datetime(record_value(record, "delivered_at")),
                        _coerce_optional_datetime(record_value(record, "started_at")),
                        _coerce_optional_datetime(record_value(record, "inflight_at")),
                        _coerce_optional_datetime(record_value(record, "occurred_at")),
                    )
                    if timestamp is not None
                ),
                default=None,
            )

        def register_recent_node_activity(
            *,
            source: str,
            record_id: object,
            event_id: object,
            node_id: object,
            status: object,
            activity_at: datetime | None,
            started_at: object = None,
            finished_at: object = None,
            trace_id: object = None,
            error: object = None,
        ) -> None:
            normalized_source = normalize_activity_text(source)
            normalized_record_id = normalize_activity_text(record_id)
            normalized_event_id = normalize_activity_text(event_id)
            normalized_node_id = normalize_activity_text(node_id)
            normalized_status = normalize_activity_text(status)
            if normalized_source is None or normalized_record_id is None:
                return
            if not normalized_event_id or not normalized_node_id:
                return
            if normalized_status is None or activity_at is None:
                return
            if activity_at < lookback_start or activity_at > now:
                return
            recent_node_activity.append(
                WorkflowRunRecentNodeActivityPayload(
                    activity_key=(
                        f"{normalized_source}:{normalized_record_id}:{normalized_node_id}:"
                        f"{normalized_status}:{activity_at.isoformat()}"
                    ),
                    event_id=normalized_event_id,
                    node_id=normalized_node_id,
                    status=normalized_status,
                    source=normalized_source,
                    activity_at=activity_at,
                    started_at=_coerce_optional_datetime(started_at),
                    finished_at=_coerce_optional_datetime(finished_at),
                    trace_id=normalize_activity_text(trace_id),
                    error=normalize_activity_text(error),
                )
            )

        def collect_recent_activity(
            records: list[Any],
            *,
            source: str,
            record_id_field: str,
            event_id_field: str = "event_id",
            node_id_field: str,
            status_field: str,
            started_at_field: str | None = None,
            finished_at_field: str | None = None,
            trace_id_field: str | None = None,
            error_field: str | None = None,
        ) -> None:
            for raw_record in records:
                if not isinstance(raw_record, Mapping) and not isinstance(raw_record, BaseModel):
                    continue
                dismissed_at = _coerce_optional_datetime(record_value(raw_record, "dismissed_at"))
                if dismissed_at is not None:
                    continue
                register_recent_node_activity(
                    source=source,
                    record_id=record_value(raw_record, record_id_field),
                    event_id=record_value(raw_record, event_id_field),
                    node_id=record_value(raw_record, node_id_field),
                    status=(
                        record_value(raw_record, status_field)
                        if source != "trigger_event"
                        else record_value(raw_record, status_field) or "event"
                    ),
                    activity_at=activity_at_from_record(raw_record),
                    started_at=record_value(raw_record, started_at_field)
                    if started_at_field is not None
                    else None,
                    finished_at=record_value(raw_record, finished_at_field)
                    if finished_at_field is not None
                    else None,
                    trace_id=record_value(raw_record, trace_id_field)
                    if trace_id_field is not None
                    else None,
                    error=record_value(raw_record, error_field)
                    if error_field is not None
                    else None,
                )

        queue_store, effect_store, gate_store = self._workflow_run_store_bundle(stores)
        queue_payload = {
            record["event_id"]: record for record in queue_store.list_active_summaries()
        }
        execution_payload = {
            record["execution_id"]: record for record in effect_store.list_active_summaries()
        }
        gate_payload = {
            record["execution_id"]: record for record in gate_store.list_active_summaries()
        }
        queue_recent_records = queue_store.list_activity_summaries_between(
            start_at=lookback_start, end_at=now
        )
        execution_recent_records = effect_store.list_activity_summaries_between(
            start_at=lookback_start, end_at=now
        )
        gate_recent_records = gate_store.list_activity_summaries_between(
            start_at=lookback_start, end_at=now
        )

        for event_id, raw_record in queue_payload.items():
            if not isinstance(raw_record, dict):
                continue
            delivery_state = str(raw_record.get("delivery_state") or "").strip()
            if delivery_state == "delivered":
                continue
            normalized_event_id = str(raw_record.get("event_id") or event_id).strip()
            source_node_id = str(raw_record.get("trigger_id") or "").strip()
            register_node_activity(
                event_id=normalized_event_id,
                node_id=source_node_id,
                status=delivery_state or "pending",
                source="queue",
                trace_id=_coerce_optional_string(raw_record.get("trace_id")),
                source_node_id=source_node_id,
                source_trigger_type=_coerce_optional_string(raw_record.get("trigger_type")),
                delivery_state=delivery_state or None,
                occurred_at=_coerce_optional_datetime(raw_record.get("occurred_at")),
                started_at=_coerce_optional_datetime(raw_record.get("inflight_at")),
                finished_at=_coerce_optional_datetime(raw_record.get("delivered_at")),
            )

        for execution_id, raw_record in execution_payload.items():
            if not isinstance(raw_record, dict):
                continue
            status = str(raw_record.get("status") or "").strip()
            if raw_record.get("dismissed_at"):
                continue
            if status not in {"running", "waiting", "failed", "completion_pending", "error_routed"}:
                continue
            event_id = str(raw_record.get("event_id") or execution_id).strip()
            node_id = str(raw_record.get("effect_id") or "").strip()
            register_node_activity(
                event_id=event_id,
                node_id=node_id,
                status=status,
                source="effect_execution",
                trace_id=_coerce_optional_string(raw_record.get("trace_id")),
                source_node_id=_coerce_optional_string(raw_record.get("source_node_id")),
                source_trigger_type=_coerce_optional_string(raw_record.get("source_trigger_type")),
                started_at=_coerce_optional_datetime(raw_record.get("started_at")),
                finished_at=_coerce_optional_datetime(raw_record.get("finished_at")),
                error=_coerce_optional_string(raw_record.get("error")),
                resumable=bool(
                    _coerce_optional_string(raw_record.get("source_node_id"))
                    and raw_record.get("has_input_payload")
                ),
            )

        for execution_id, raw_record in gate_payload.items():
            if not isinstance(raw_record, dict):
                continue
            status = str(raw_record.get("status") or "").strip()
            if raw_record.get("dismissed_at"):
                continue
            if status not in {"running", "waiting", "failed", "completion_pending"}:
                continue
            event_id = str(raw_record.get("event_id") or execution_id).strip()
            node_id = str(raw_record.get("gate_id") or "").strip()
            register_node_activity(
                event_id=event_id,
                node_id=node_id,
                status=status,
                source="gate_execution",
                trace_id=_coerce_optional_string(raw_record.get("trace_id")),
                source_node_id=_coerce_optional_string(raw_record.get("source_node_id")),
                source_trigger_type=_coerce_optional_string(raw_record.get("source_trigger_type")),
                started_at=_coerce_optional_datetime(raw_record.get("started_at")),
                finished_at=_coerce_optional_datetime(raw_record.get("finished_at")),
                error=_coerce_optional_string(raw_record.get("error")),
                resumable=bool(
                    _coerce_optional_string(raw_record.get("source_node_id"))
                    and raw_record.get("has_input_payload")
                ),
            )

        await_agent_payload = _read_merged_json_objects(
            runtime_dir / "await-agent-pending.json",
            runtime_dir / "providers" / "gates" / "await-agent" / "pending.json",
        )
        for record_key, raw_record in await_agent_payload.items():
            if not isinstance(raw_record, dict):
                continue
            gate_id = str(raw_record.get("gate_id") or "").strip()
            dispatch_id = _coerce_optional_string(raw_record.get("dispatch_id"))
            trace_id = _coerce_optional_string(raw_record.get("trace_id"))
            prompt_event_id = (
                _coerce_optional_string(raw_record.get("prompt_event_id")) or record_key
            )
            register_node_activity(
                event_id=prompt_event_id,
                node_id=gate_id,
                status="waiting",
                source="await_agent",
                trace_id=trace_id,
                source_node_id=_coerce_optional_string(raw_record.get("prompt_source_node_id")),
                source_trigger_type="trigger:agent:run:completed",
                started_at=_coerce_optional_datetime(raw_record.get("occurred_at")),
                dispatch_id=dispatch_id,
            )

        collect_recent_activity(
            queue_recent_records,
            source="trigger_event",
            record_id_field="event_id",
            node_id_field="trigger_id",
            status_field="delivery_state",
            started_at_field="inflight_at",
            finished_at_field="delivered_at",
            trace_id_field="trace_id",
            error_field="error",
        )
        collect_recent_activity(
            execution_recent_records,
            source="effect_execution",
            record_id_field="execution_id",
            node_id_field="effect_id",
            status_field="status",
            started_at_field="started_at",
            finished_at_field="finished_at",
            trace_id_field="trace_id",
            error_field="error",
        )
        collect_recent_activity(
            gate_recent_records,
            source="gate_execution",
            record_id_field="execution_id",
            node_id_field="gate_id",
            status_field="status",
            started_at_field="started_at",
            finished_at_field="finished_at",
            trace_id_field="trace_id",
            error_field="error",
        )
        recent_node_activity.sort(
            key=lambda item: (item.activity_at, item.activity_key), reverse=True
        )
        recent_node_activity = recent_node_activity[:200]

        runs = [
            WorkflowRunSummaryPayload.model_validate(
                {
                    **payload,
                    "active_nodes": _merge_run_node_activity(
                        payload.get("active_nodes", []),
                    ),
                }
            )
            for payload in runs_by_event.values()
            if payload.get("active_nodes")
        ]
        runs.sort(
            key=lambda run: (
                run.latest_activity_at or datetime.min.replace(tzinfo=UTC),
                run.occurred_at or datetime.min.replace(tzinfo=UTC),
                run.event_id,
            ),
            reverse=True,
        )
        return WorkflowRunSnapshotPayload(
            generated_at=now,
            runs=runs,
            node_activity={
                node_id: dict(sorted(counts.items()))
                for node_id, counts in sorted(node_activity.items())
            },
            recent_node_activity=recent_node_activity,
        )

    def _read_workflow_node_execution_search(
        self,
        *,
        node_id: str,
        started_from: datetime,
        started_to: datetime,
        statuses: list[str],
        filters: list[Mapping[str, Any]],
        limit: int,
        cursor: str | None,
        include_total: bool = True,
    ) -> WorkflowNodeExecutionSearchPayload:
        stores = self._workflow_run_store_paths()
        payload = search_node_executions(
            stores["orchestration_store_path"],
            node_id=node_id,
            started_from=started_from,
            started_to=started_to,
            statuses=statuses,
            filters=filters,
            limit=limit,
            cursor=cursor,
            include_total=include_total,
            full_retention_days=int(
                stores["orchestration_retention_days"]
                if stores.get("orchestration_retention_days") is not None
                else 30
            ),
            compact_retention_days=int(
                stores["orchestration_compact_history_retention_days"]
                if stores.get("orchestration_compact_history_retention_days") is not None
                else 365
            ),
        )
        return WorkflowNodeExecutionSearchPayload.model_validate(payload)

    def _read_workflow_node_execution_fields(
        self,
        *,
        node_id: str,
        started_from: datetime,
        started_to: datetime,
    ) -> WorkflowNodeExecutionFieldsPayload:
        stores = self._workflow_run_store_paths()
        return WorkflowNodeExecutionFieldsPayload.model_validate(
            list_node_execution_fields(
                stores["orchestration_store_path"],
                node_id=node_id,
                started_from=started_from,
                started_to=started_to,
            )
        )

    def _read_workflow_node_execution_detail(
        self, execution_key: str
    ) -> WorkflowNodeExecutionDetailPayload:
        stores = self._workflow_run_store_paths()
        return WorkflowNodeExecutionDetailPayload.model_validate(
            read_node_execution_detail(
                stores["orchestration_store_path"], execution_key=execution_key
            )
        )

    def _read_workflow_node_execution_authorization_paths(
        self,
        *,
        node_id: str,
        started_from: datetime,
        started_to: datetime,
    ) -> tuple[str, ...]:
        stores = self._workflow_run_store_paths()
        return list_node_execution_authorization_paths(
            stores["orchestration_store_path"],
            node_id=node_id,
            started_from=started_from,
            started_to=started_to,
        )

    def _read_workflow_node_execution_authorization_path(self, execution_key: str) -> str:
        stores = self._workflow_run_store_paths()
        return read_node_execution_authorization_path(
            stores["orchestration_store_path"], execution_key=execution_key
        )

    def _read_workflow_run_search(
        self,
        *,
        node_id: str | None = None,
        graph_id: str | None = None,
        without_session: bool = False,
        include_completed: bool = True,
        limit: int | None = None,
    ) -> WorkflowRunSearchPayload:
        normalized_node_id = _coerce_optional_string(node_id)
        normalized_graph_id = _coerce_optional_string(graph_id)
        normalized_limit = max(1, min(100, int(limit or 25)))
        document = self._read_composed_workflow_editor_document()

        target_node_ids: set[str] = set()
        graph_name: str | None = None
        if normalized_graph_id is not None:
            graph = next(
                (candidate for candidate in document.graphs if candidate.id == normalized_graph_id),
                None,
            )
            if graph is None:
                raise ValueError(f"Workflow graph not found: {normalized_graph_id}")
            target_node_ids.update(graph.node_ids)
            graph_name = graph.name
        if normalized_node_id is not None:
            target_node_ids.add(normalized_node_id)
        if not target_node_ids:
            raise ValueError("Workflow run search requires node_id or graph_id.")

        stores = self._workflow_run_store_paths()
        await_agent_payload = _read_merged_json_objects(
            stores["legacy_await_agent_store_path"],
            stores["await_agent_store_path"],
        )
        (
            sqlite_event_ids,
            sqlite_matched_count,
            sqlite_has_more_candidates,
        ) = self._workflow_run_search_sqlite_event_ids(
            stores,
            target_node_ids=target_node_ids,
            include_completed=include_completed,
            limit=normalized_limit,
            without_session=without_session,
        )
        queue_records, effect_records, gate_records = (
            self._workflow_run_sqlite_records_for_event_ids(
                stores,
                event_ids=sqlite_event_ids,
            )
        )
        approval_requests = self.runtime.approvals.read_all()
        artifact_store = self._communication_artifact_store()

        def normalize_record_payload(record: object) -> dict[str, Any]:
            if isinstance(record, BaseModel):
                return record.model_dump(mode="json")
            if isinstance(record, dict):
                return dict(record)
            return {}

        def record_event_id(record: dict[str, Any], fallback: object) -> str:
            return _coerce_optional_string(record.get("event_id")) or str(fallback)

        def latest_of(*values: object) -> datetime | None:
            parsed = [
                parsed_value
                for parsed_value in (_coerce_optional_datetime(value) for value in values)
                if parsed_value is not None
            ]
            return max(parsed) if parsed else None

        def ensure_group(groups: dict[str, dict[str, Any]], event_key: str) -> dict[str, Any]:
            group = groups.get(event_key)
            if group is None:
                group = {
                    "event_id": event_key,
                    "trace_ids": set(),
                    "session_ids": set(),
                    "artifact_ids": set(),
                    "request_ids": set(),
                    "node_ids": set(),
                    "activities": [],
                    "status_counts": {},
                    "occurred_at": None,
                    "latest_activity_at": None,
                    "source_node_id": None,
                    "source_trigger_type": None,
                    "delivery_state": None,
                    "error": None,
                }
                groups[event_key] = group
            return group

        def update_group_metadata(
            group: dict[str, Any],
            *,
            trace_id_value: str | None = None,
            session_ids: list[str] | None = None,
            artifact_ids: list[str] | None = None,
            request_ids: list[str] | None = None,
            occurred_at_value: datetime | None = None,
            latest_activity_at_value: datetime | None = None,
            source_node_id: str | None = None,
            source_trigger_type: str | None = None,
            delivery_state: str | None = None,
            error: str | None = None,
        ) -> None:
            if trace_id_value:
                group["trace_ids"].add(trace_id_value)
            for value in session_ids or []:
                group["session_ids"].add(value)
            for value in artifact_ids or []:
                group["artifact_ids"].add(value)
            for value in request_ids or []:
                group["request_ids"].add(value)
            if occurred_at_value is not None and group.get("occurred_at") is None:
                group["occurred_at"] = occurred_at_value
            previous_latest = group.get("latest_activity_at")
            if latest_activity_at_value is not None and (
                not isinstance(previous_latest, datetime)
                or latest_activity_at_value > previous_latest
            ):
                group["latest_activity_at"] = latest_activity_at_value
            if source_node_id and not group.get("source_node_id"):
                group["source_node_id"] = source_node_id
            if source_trigger_type and not group.get("source_trigger_type"):
                group["source_trigger_type"] = source_trigger_type
            if delivery_state and not group.get("delivery_state"):
                group["delivery_state"] = delivery_state
            if error and not group.get("error"):
                group["error"] = error

        def register_activity(
            groups: dict[str, dict[str, Any]],
            *,
            event_key: str,
            node_id_value: str | None,
            status: str | None,
            source: str,
            trace_id_value: str | None = None,
            session_payloads: list[Any] | None = None,
            artifact_payloads: list[Any] | None = None,
            request_payloads: list[Any] | None = None,
            occurred_at_value: datetime | None = None,
            started_at_value: datetime | None = None,
            finished_at_value: datetime | None = None,
            latest_activity_at_value: datetime | None = None,
            source_node_id: str | None = None,
            source_trigger_type: str | None = None,
            delivery_state: str | None = None,
            dispatch_id: str | None = None,
            error: str | None = None,
        ) -> None:
            normalized_activity_node_id = _coerce_optional_string(node_id_value)
            if normalized_activity_node_id is None:
                return
            normalized_status = (status or "unknown").strip() or "unknown"
            group = ensure_group(groups, event_key)
            group["node_ids"].add(normalized_activity_node_id)
            status_counts = group["status_counts"]
            status_counts[normalized_status] = status_counts.get(normalized_status, 0) + 1
            group["activities"].append(
                WorkflowRunNodeActivityPayload(
                    node_id=normalized_activity_node_id,
                    status=normalized_status,
                    source=source,
                    started_at=started_at_value,
                    finished_at=finished_at_value,
                    dispatch_id=dispatch_id,
                    error=error,
                )
            )
            update_group_metadata(
                group,
                trace_id_value=trace_id_value,
                session_ids=[
                    value
                    for payload in session_payloads or []
                    for value in _collect_workflow_keyed_values(payload, key_suffix="session_id")
                ],
                artifact_ids=[
                    value
                    for payload in artifact_payloads or []
                    for value in _collect_workflow_artifact_ids(payload)
                ],
                request_ids=[
                    value
                    for payload in request_payloads or []
                    for value in _collect_workflow_keyed_values(payload, key_suffix="request_id")
                ],
                occurred_at_value=occurred_at_value,
                latest_activity_at_value=latest_activity_at_value
                or latest_of(finished_at_value, started_at_value, occurred_at_value),
                source_node_id=source_node_id,
                source_trigger_type=source_trigger_type,
                delivery_state=delivery_state,
                error=error,
            )

        groups: dict[str, dict[str, Any]] = {}
        candidate_event_ids = None
        for record_key, record in queue_records.items():
            raw_record = normalize_record_payload(record)
            if not raw_record:
                continue
            event_id = record_event_id(raw_record, record_key)
            if candidate_event_ids is not None and event_id not in candidate_event_ids:
                continue
            occurred_at = _coerce_optional_datetime(raw_record.get("occurred_at"))
            register_activity(
                groups,
                event_key=event_id,
                node_id_value=_coerce_optional_string(raw_record.get("trigger_id")),
                status=_coerce_optional_string(raw_record.get("delivery_state")) or "pending",
                source="trigger_event",
                trace_id_value=_coerce_optional_string(raw_record.get("trace_id")),
                session_payloads=[raw_record],
                artifact_payloads=[raw_record.get("payload")],
                request_payloads=[raw_record],
                occurred_at_value=occurred_at,
                started_at_value=_coerce_optional_datetime(raw_record.get("inflight_at")),
                finished_at_value=_coerce_optional_datetime(raw_record.get("delivered_at")),
                latest_activity_at_value=latest_of(
                    raw_record.get("delivered_at"),
                    raw_record.get("inflight_at"),
                    raw_record.get("occurred_at"),
                ),
                source_node_id=_coerce_optional_string(raw_record.get("trigger_id")),
                source_trigger_type=_coerce_optional_string(raw_record.get("trigger_type")),
                delivery_state=_coerce_optional_string(raw_record.get("delivery_state")),
                error=_coerce_optional_string(raw_record.get("error")),
            )

        for execution_id, record in effect_records.items():
            raw_record = normalize_record_payload(record)
            if not raw_record:
                continue
            event_id = record_event_id(raw_record, execution_id)
            if candidate_event_ids is not None and event_id not in candidate_event_ids:
                continue
            payload_sources = [
                raw_record.get("resolved_config"),
                raw_record.get("input_payload"),
                raw_record.get("outputs"),
            ]
            register_activity(
                groups,
                event_key=event_id,
                node_id_value=_coerce_optional_string(raw_record.get("effect_id")),
                status=_coerce_optional_string(raw_record.get("status")),
                source="effect_execution",
                trace_id_value=_coerce_optional_string(raw_record.get("trace_id")),
                session_payloads=payload_sources,
                artifact_payloads=payload_sources,
                request_payloads=payload_sources,
                started_at_value=_coerce_optional_datetime(raw_record.get("started_at")),
                finished_at_value=_coerce_optional_datetime(raw_record.get("finished_at")),
                source_node_id=_coerce_optional_string(raw_record.get("source_node_id")),
                source_trigger_type=_coerce_optional_string(raw_record.get("source_trigger_type")),
                error=_coerce_optional_string(raw_record.get("error")),
            )

        for execution_id, record in gate_records.items():
            raw_record = normalize_record_payload(record)
            if not raw_record:
                continue
            event_id = record_event_id(raw_record, execution_id)
            if candidate_event_ids is not None and event_id not in candidate_event_ids:
                continue
            payload_sources = [
                raw_record.get("resolved_config"),
                raw_record.get("input_payload"),
                raw_record.get("outputs"),
                raw_record.get("inputs_by_source"),
            ]
            register_activity(
                groups,
                event_key=event_id,
                node_id_value=_coerce_optional_string(raw_record.get("gate_id")),
                status=_coerce_optional_string(raw_record.get("status")),
                source="gate_execution",
                trace_id_value=_coerce_optional_string(raw_record.get("trace_id")),
                session_payloads=payload_sources,
                artifact_payloads=payload_sources,
                request_payloads=payload_sources,
                started_at_value=_coerce_optional_datetime(raw_record.get("started_at")),
                finished_at_value=_coerce_optional_datetime(raw_record.get("finished_at")),
                source_node_id=_coerce_optional_string(raw_record.get("source_node_id")),
                source_trigger_type=_coerce_optional_string(raw_record.get("source_trigger_type")),
                error=_coerce_optional_string(raw_record.get("error")),
            )

        for record_key, raw_record in await_agent_payload.items():
            if not isinstance(raw_record, dict):
                continue
            event_id = _coerce_optional_string(raw_record.get("prompt_event_id")) or str(record_key)
            register_activity(
                groups,
                event_key=event_id,
                node_id_value=_coerce_optional_string(raw_record.get("gate_id")),
                status="waiting",
                source="await_agent",
                trace_id_value=_coerce_optional_string(raw_record.get("trace_id")),
                session_payloads=[raw_record],
                artifact_payloads=[raw_record],
                request_payloads=[raw_record],
                occurred_at_value=_coerce_optional_datetime(raw_record.get("occurred_at")),
                started_at_value=_coerce_optional_datetime(raw_record.get("occurred_at")),
                source_node_id=_coerce_optional_string(raw_record.get("prompt_source_node_id")),
                source_trigger_type="trigger:agent:run:completed",
                dispatch_id=_coerce_optional_string(raw_record.get("dispatch_id")),
            )

        for group in groups.values():
            for request_id in list(group["request_ids"]):
                request = approval_requests.get(request_id)
                if request is not None:
                    group["session_ids"].add(request.session_id)
            for artifact_id in list(group["artifact_ids"]):
                try:
                    artifact_payload = artifact_store.payload(artifact_id)
                except FileNotFoundError:
                    continue
                artifact_session_id = _coerce_optional_string(artifact_payload.get("session_id"))
                if artifact_session_id:
                    group["session_ids"].add(artifact_session_id)

        session_ids_by_trace: dict[str, set[str]] = {}
        for group in groups.values():
            for trace_id_value in group["trace_ids"]:
                if trace_id_value:
                    session_ids_by_trace.setdefault(trace_id_value, set()).update(
                        group["session_ids"]
                    )

        matched_runs: list[WorkflowRunSearchItemPayload] = []
        active_statuses = {"pending", "inflight", "running", "waiting", "completion_pending"}
        for group in groups.values():
            matched_node_ids = sorted(group["node_ids"] & target_node_ids)
            if not matched_node_ids:
                continue
            effective_session_ids = set(group["session_ids"])
            for trace_id_value in group["trace_ids"]:
                effective_session_ids.update(session_ids_by_trace.get(trace_id_value, set()))
            if without_session and effective_session_ids:
                continue
            status_counts = dict(sorted(group["status_counts"].items()))
            if status_counts.get("failed", 0) > 0:
                run_state = "errored"
            elif status_counts.get("error_routed", 0) > 0:
                run_state = "error_routed"
            elif any(status_counts.get(status, 0) > 0 for status in active_statuses):
                run_state = "active"
            else:
                run_state = "completed"
            if not include_completed and run_state == "completed":
                continue
            matched_runs.append(
                WorkflowRunSearchItemPayload(
                    event_id=str(group["event_id"]),
                    trace_id=next((value for value in sorted(group["trace_ids"]) if value), None),
                    run_state=run_state,
                    source_node_id=_coerce_optional_string(group.get("source_node_id")),
                    source_trigger_type=_coerce_optional_string(group.get("source_trigger_type")),
                    delivery_state=_coerce_optional_string(group.get("delivery_state")),
                    occurred_at=group.get("occurred_at"),
                    latest_activity_at=group.get("latest_activity_at"),
                    node_ids=sorted(group["node_ids"]),
                    matched_node_ids=matched_node_ids,
                    session_ids=sorted(effective_session_ids),
                    has_agent_session=bool(effective_session_ids),
                    status_counts=status_counts,
                    nodes=_merge_run_node_activity(group["activities"]),
                    error=_coerce_optional_string(group.get("error")),
                )
            )

        matched_runs.sort(
            key=lambda run: (
                run.latest_activity_at or run.occurred_at or datetime.min.replace(tzinfo=UTC),
                run.occurred_at or datetime.min.replace(tzinfo=UTC),
                run.event_id,
            ),
            reverse=True,
        )
        limited_runs = matched_runs[:normalized_limit]
        matched_count = (
            max(sqlite_matched_count, len(matched_runs))
            if sqlite_matched_count is not None and not without_session
            else len(matched_runs)
        )
        return WorkflowRunSearchPayload(
            generated_at=_utc_now(),
            filters=WorkflowRunSearchFilterPayload(
                node_id=normalized_node_id,
                graph_id=normalized_graph_id,
                graph_name=graph_name,
                without_session=without_session,
                include_completed=include_completed,
            ),
            runs=limited_runs,
            matched=matched_count,
            returned=len(limited_runs),
            limit=normalized_limit,
            has_more=matched_count > len(limited_runs)
            or (sqlite_has_more_candidates and not without_session),
        )

    def _read_workflow_graph_activity_report(
        self,
        *,
        period_start: datetime | None = None,
        period_end: datetime | None = None,
        limit: int | None = None,
        refresh: bool = False,
    ) -> WorkflowGraphActivityReportPayload:
        document = self._read_composed_workflow_editor_document()
        stores = self._workflow_run_store_paths()

        def collect() -> WorkflowGraphActivityReportPayload:
            return self._collect_workflow_graph_activity_report(
                period_start=period_start,
                period_end=period_end,
                limit=limit,
                document=document,
                stores=stores,
            )

        # Explicit intervals are exact reads, including backfilled executions.
        # Only the default rolling dashboard window is a short-lived snapshot.
        if period_start is not None or period_end is not None:
            return collect()
        key = (
            str(stores.get("orchestration_store_path", "")),
            document.revision or self._workflow_editor_revision(
                config=document.config, layout=document.layout, graphs=document.graphs
            ),
            limit,
        )
        return self._graph_activity_snapshots.read(key, collect, refresh=refresh).model_copy(
            deep=True
        )

    def _collect_workflow_graph_activity_report(
        self,
        *,
        period_start: datetime | None,
        period_end: datetime | None,
        limit: int | None,
        document: WorkflowEditorDocumentPayload,
        stores: dict[str, Any],
    ) -> WorkflowGraphActivityReportPayload:
        now = _utc_now()
        end_at = (
            period_end.astimezone(UTC)
            if period_end is not None and period_end.tzinfo is not None
            else period_end.replace(tzinfo=UTC)
            if period_end is not None
            else now
        )
        start_at = (
            period_start.astimezone(UTC)
            if period_start is not None and period_start.tzinfo is not None
            else period_start.replace(tzinfo=UTC)
            if period_start is not None
            else end_at - timedelta(days=1)
        )
        if start_at > end_at:
            raise ValueError("Workflow graph activity period_start must be before period_end.")

        node_metadata: dict[str, dict[str, str | None]] = {}
        for collection_name in ("triggers", "gates", "effects", "queries"):
            nodes = document.config.get(collection_name)
            if not isinstance(nodes, list):
                continue
            for raw_node in nodes:
                if not isinstance(raw_node, dict):
                    continue
                node_id = str(raw_node.get("id") or "").strip()
                if not node_id:
                    continue
                node_metadata[node_id] = {
                    "node_type": _coerce_optional_string(raw_node.get("type")),
                    "display_name": _coerce_optional_string(raw_node.get("display_name")),
                }

        graph_states: dict[str, dict[str, Any]] = {}
        node_to_graph_id: dict[str, str] = {}
        for graph in document.graphs:
            graph_id = graph.id.strip()
            if not graph_id:
                continue
            graph_states[graph_id] = {
                "graph": graph,
                "run_keys": set(),
                "activity_count": 0,
                "trigger_event_count": 0,
                "gate_execution_count": 0,
                "effect_execution_count": 0,
                "status_counts": {},
                "first_activity_at": None,
                "latest_activity_at": None,
                "nodes": {},
            }
            for node_id in graph.node_ids:
                node_to_graph_id.setdefault(node_id, graph_id)

        def in_period(timestamp: datetime | None) -> bool:
            return timestamp is not None and start_at <= timestamp <= end_at

        def update_bounds(state: dict[str, Any], timestamp: datetime) -> None:
            first_activity_at = state.get("first_activity_at")
            if not isinstance(first_activity_at, datetime) or timestamp < first_activity_at:
                state["first_activity_at"] = timestamp
            latest_activity_at = state.get("latest_activity_at")
            if not isinstance(latest_activity_at, datetime) or timestamp > latest_activity_at:
                state["latest_activity_at"] = timestamp

        def increment_status(counts: dict[str, int], status: str) -> None:
            counts[status] = counts.get(status, 0) + 1

        def register_activity(
            *,
            node_id: str,
            timestamp: datetime | None,
            source: str,
            status: str | None,
            run_key: str | None,
        ) -> None:
            normalized_node_id = node_id.strip()
            if not normalized_node_id or not in_period(timestamp):
                return
            graph_id = node_to_graph_id.get(normalized_node_id)
            if graph_id is None:
                return
            assert timestamp is not None
            state = graph_states[graph_id]
            state["activity_count"] += 1
            if source == "trigger":
                state["trigger_event_count"] += 1
            elif source == "gate":
                state["gate_execution_count"] += 1
            elif source == "effect":
                state["effect_execution_count"] += 1
            if run_key:
                state["run_keys"].add(run_key)
            normalized_status = (status or "unknown").strip() or "unknown"
            increment_status(state["status_counts"], normalized_status)
            update_bounds(state, timestamp)

            nodes = state["nodes"]
            node_state = nodes.setdefault(
                normalized_node_id,
                {
                    "activity_count": 0,
                    "status_counts": {},
                    "first_activity_at": None,
                    "latest_activity_at": None,
                },
            )
            node_state["activity_count"] += 1
            increment_status(node_state["status_counts"], normalized_status)
            update_bounds(node_state, timestamp)

        def register_aggregated_activity(
            *,
            graph_id: str,
            node_id: str,
            source: str,
            status: str,
            activity_count: int,
            first_activity_at: datetime | None,
            latest_activity_at: datetime | None,
        ) -> None:
            state = graph_states.get(graph_id)
            if state is None or activity_count <= 0:
                return
            state["activity_count"] += activity_count
            if source == "trigger":
                state["trigger_event_count"] += activity_count
            elif source == "gate":
                state["gate_execution_count"] += activity_count
            elif source == "effect":
                state["effect_execution_count"] += activity_count
            normalized_status = status.strip() or "unknown"
            state["status_counts"][normalized_status] = (
                state["status_counts"].get(normalized_status, 0) + activity_count
            )
            if first_activity_at is not None:
                update_bounds(state, first_activity_at)
            if latest_activity_at is not None:
                update_bounds(state, latest_activity_at)

            nodes = state["nodes"]
            node_state = nodes.setdefault(
                node_id,
                {
                    "activity_count": 0,
                    "status_counts": {},
                    "first_activity_at": None,
                    "latest_activity_at": None,
                },
            )
            node_state["activity_count"] += activity_count
            node_state["status_counts"][normalized_status] = (
                node_state["status_counts"].get(normalized_status, 0) + activity_count
            )
            if first_activity_at is not None:
                update_bounds(node_state, first_activity_at)
            if latest_activity_at is not None:
                update_bounds(node_state, latest_activity_at)

        def latest_datetime(*values: object) -> datetime | None:
            parsed_values = [
                parsed
                for parsed in (_coerce_optional_datetime(value) for value in values)
                if parsed
            ]
            return max(parsed_values) if parsed_values else None

        if stores:
            start_text = datetime_to_storage(start_at)
            end_text = datetime_to_storage(end_at)
            db_path = Path(stores["orchestration_store_path"])
            ensure_orchestration_schema(db_path)
            with connect_orchestration_db(
                db_path, deadline_seconds=WORKFLOW_REPORT_READ_DEADLINE_SECONDS,
            ) as connection:
                connection.execute(
                    """
                    CREATE TEMP TABLE workflow_graph_activity_nodes (
                        node_id TEXT PRIMARY KEY,
                        graph_id TEXT NOT NULL
                    )
                    """
                )
                connection.executemany(
                    """
                    INSERT INTO workflow_graph_activity_nodes(node_id, graph_id)
                    VALUES (?, ?)
                    """,
                    tuple(node_to_graph_id.items()),
                )
                activity_sql = """
                    WITH activity AS MATERIALIZED (
                        SELECT nodes.graph_id AS graph_id,
                               events.trigger_id AS node_id,
                               'trigger' AS source,
                               COALESCE(NULLIF(events.delivery_state, ''), 'event') AS status,
                               events.occurred_at AS activity_at,
                               COALESCE(NULLIF(events.trace_id, ''), events.event_id) AS run_key
                        FROM trigger_events AS events INDEXED BY idx_trigger_events_occurred
                        INNER JOIN workflow_graph_activity_nodes AS nodes
                            ON nodes.node_id = events.trigger_id
                        WHERE events.occurred_at BETWEEN ? AND ?
                        UNION ALL
                        SELECT nodes.graph_id AS graph_id,
                               executions.effect_id AS node_id,
                               'effect' AS source,
                               COALESCE(NULLIF(executions.status, ''), 'unknown') AS status,
                               COALESCE(executions.finished_at, executions.started_at) AS activity_at,
                               COALESCE(
                                   NULLIF(executions.trace_id, ''),
                                   NULLIF(executions.event_id, ''),
                                   executions.execution_id
                               ) AS run_key
                        FROM effect_executions AS executions
                            INDEXED BY idx_effect_executions_activity
                        INNER JOIN workflow_graph_activity_nodes AS nodes
                            ON nodes.node_id = executions.effect_id
                        WHERE COALESCE(executions.finished_at, executions.started_at) BETWEEN ? AND ?
                        UNION ALL
                        SELECT nodes.graph_id AS graph_id,
                               executions.gate_id AS node_id,
                               'gate' AS source,
                               COALESCE(NULLIF(executions.status, ''), 'unknown') AS status,
                               COALESCE(executions.finished_at, executions.started_at) AS activity_at,
                               COALESCE(
                                   NULLIF(executions.trace_id, ''),
                                   NULLIF(executions.event_id, ''),
                                   executions.execution_id
                               ) AS run_key
                        FROM gate_executions AS executions
                            INDEXED BY idx_gate_executions_activity
                        INNER JOIN workflow_graph_activity_nodes AS nodes
                            ON nodes.node_id = executions.gate_id
                        WHERE COALESCE(executions.finished_at, executions.started_at) BETWEEN ? AND ?
                    )
                """
                activity_parameters = (
                    start_text,
                    end_text,
                    start_text,
                    end_text,
                    start_text,
                    end_text,
                )
                for row in connection.execute(
                    activity_sql
                    + """
                    SELECT graph_id, node_id, source, status,
                           COUNT(*) AS activity_count,
                           MIN(activity_at) AS first_activity_at,
                           MAX(activity_at) AS latest_activity_at,
                           NULL AS run_count
                    FROM activity
                    GROUP BY graph_id, node_id, source, status
                    UNION ALL
                    SELECT graph_id, NULL, NULL, NULL, NULL, NULL, NULL,
                           COUNT(DISTINCT run_key) AS run_count
                    FROM activity
                    GROUP BY graph_id
                    """,
                    activity_parameters,
                ):
                    if row["run_count"] is not None:
                        state = graph_states.get(str(row["graph_id"] or ""))
                        if state is not None:
                            state["run_count"] = int(row["run_count"])
                        continue
                    register_aggregated_activity(
                        graph_id=str(row["graph_id"] or ""),
                        node_id=str(row["node_id"] or ""),
                        source=str(row["source"] or ""),
                        status=str(row["status"] or "unknown"),
                        activity_count=int(row["activity_count"] or 0),
                        first_activity_at=_coerce_optional_datetime(row["first_activity_at"]),
                        latest_activity_at=_coerce_optional_datetime(row["latest_activity_at"]),
                    )
        graphs: list[WorkflowGraphActivityGraphPayload] = []
        for graph_id, state in graph_states.items():
            graph = state["graph"]
            node_payloads: list[WorkflowGraphActivityNodePayload] = []
            for node_id, node_state in state["nodes"].items():
                metadata = node_metadata.get(node_id, {})
                node_payloads.append(
                    WorkflowGraphActivityNodePayload(
                        node_id=node_id,
                        node_type=metadata.get("node_type"),
                        display_name=metadata.get("display_name"),
                        activity_count=node_state["activity_count"],
                        status_counts=dict(sorted(node_state["status_counts"].items())),
                        first_activity_at=node_state.get("first_activity_at"),
                        latest_activity_at=node_state.get("latest_activity_at"),
                    )
                )
            node_payloads.sort(
                key=lambda node: (
                    node.activity_count,
                    node.latest_activity_at or datetime.min.replace(tzinfo=UTC),
                    node.display_name or node.node_id,
                ),
                reverse=True,
            )
            graphs.append(
                WorkflowGraphActivityGraphPayload(
                    graph_id=graph_id,
                    name=graph.name,
                    description=graph.description,
                    directory_path=graph.directory_path,
                    user_ids=graph.user_ids,
                    node_ids=graph.node_ids,
                    node_count=len(graph.node_ids),
                    run_count=int(state.get("run_count", len(state["run_keys"]))),
                    activity_count=state["activity_count"],
                    trigger_event_count=state["trigger_event_count"],
                    gate_execution_count=state["gate_execution_count"],
                    effect_execution_count=state["effect_execution_count"],
                    status_counts=dict(sorted(state["status_counts"].items())),
                    first_activity_at=state.get("first_activity_at"),
                    latest_activity_at=state.get("latest_activity_at"),
                    nodes=node_payloads,
                )
            )
        graphs.sort(
            key=lambda graph: (
                graph.activity_count,
                graph.run_count,
                graph.latest_activity_at or datetime.min.replace(tzinfo=UTC),
                graph.name,
            ),
            reverse=True,
        )
        if limit is not None and limit > 0:
            graphs = graphs[:limit]

        return WorkflowGraphActivityReportPayload(
            generated_at=now,
            period_start=start_at,
            period_end=end_at,
            graphs=graphs,
        )

    def _read_workflow_failure_clusters_report(
        self,
        *,
        period_start: datetime | None = None,
        period_end: datetime | None = None,
        limit: int | None = None,
        recent_threshold_seconds: int | None = None,
    ) -> WorkflowFailureClusterReportPayload:
        now = _utc_now()
        end_at = _as_utc(period_end) if period_end is not None else now
        start_at = _as_utc(period_start) if period_start is not None else end_at - timedelta(days=1)
        if start_at > end_at:
            raise ValueError("Workflow failure cluster period_start must be before period_end.")

        normalized_limit = (
            WORKFLOW_FAILURE_CLUSTER_LIMIT_MAX
            if limit is None
            else max(1, min(WORKFLOW_FAILURE_CLUSTER_LIMIT_MAX, int(limit)))
        )
        normalized_recent_threshold_seconds = (
            WORKFLOW_FAILURE_CLUSTER_DEFAULT_RECENT_THRESHOLD_SECONDS
            if recent_threshold_seconds is None
            else max(1, int(recent_threshold_seconds))
        )
        recent_threshold = timedelta(seconds=normalized_recent_threshold_seconds)

        document = self._read_composed_workflow_editor_document()
        node_metadata: dict[str, dict[str, str | None]] = {}
        graph_metadata_by_id: dict[str, dict[str, str | None]] = {}
        node_to_graph_id: dict[str, str] = {}
        for raw_node in document.config.get("triggers", []):
            if not isinstance(raw_node, dict):
                continue
            node_id = str(raw_node.get("id") or "").strip()
            if not node_id:
                continue
            node_metadata[node_id] = {
                "node_type": _coerce_optional_string(raw_node.get("type")),
                "display_name": _coerce_optional_string(raw_node.get("display_name")),
            }
        for collection_name in ("gates", "effects", "queries"):
            nodes = document.config.get(collection_name)
            if not isinstance(nodes, list):
                continue
            for raw_node in nodes:
                if not isinstance(raw_node, dict):
                    continue
                node_id = str(raw_node.get("id") or "").strip()
                if not node_id:
                    continue
                node_metadata[node_id] = {
                    "node_type": _coerce_optional_string(raw_node.get("type")),
                    "display_name": _coerce_optional_string(raw_node.get("display_name")),
                }

        for graph in document.graphs:
            graph_id = graph.id.strip()
            if not graph_id:
                continue
            graph_metadata_by_id[graph_id] = {
                "graph_id": graph_id,
                "graph_name": graph.name,
                "graph_description": graph.description,
                "directory_path": graph.directory_path,
            }
            for node_id in graph.node_ids:
                node_to_graph_id.setdefault(node_id, graph_id)

        root_graph_metadata = {
            "graph_id": "root",
            "graph_name": "Unassigned",
            "graph_description": None,
            "directory_path": "root",
        }

        def graph_context_for_node(node_id: str) -> tuple[str, dict[str, str | None]]:
            graph_id = node_to_graph_id.get(node_id)
            if graph_id is None:
                return "root", root_graph_metadata
            graph_metadata = graph_metadata_by_id.get(graph_id)
            if graph_metadata is None:
                return "root", root_graph_metadata
            return graph_id, graph_metadata

        def normalize_record_payload(record: object) -> dict[str, Any]:
            if isinstance(record, BaseModel):
                return record.model_dump(mode="json")
            if isinstance(record, dict):
                return dict(record)
            return {}

        def latest_of(*values: object) -> datetime | None:
            parsed_values = [
                parsed
                for parsed in (_coerce_optional_datetime(value) for value in values)
                if parsed is not None
            ]
            return max(parsed_values) if parsed_values else None

        def dedupe(values: list[str]) -> list[str]:
            seen: set[str] = set()
            deduped: list[str] = []
            for value in values:
                if value in seen:
                    continue
                seen.add(value)
                deduped.append(value)
            return deduped

        def collect_keyed_values(*payloads: object, suffixes: tuple[str, ...]) -> list[str]:
            values: list[str] = []
            for payload in payloads:
                for suffix in suffixes:
                    values.extend(_collect_workflow_keyed_values(payload, key_suffix=suffix))
            return dedupe(values)

        def collect_artifact_ids(*payloads: object) -> list[str]:
            values: list[str] = []
            for payload in payloads:
                values.extend(_collect_workflow_artifact_ids(payload))
            return dedupe(values)

        def payload_sources(raw_record: dict[str, Any]) -> tuple[object, ...]:
            return (
                raw_record,
                raw_record.get("resolved_config"),
                raw_record.get("input_payload"),
                raw_record.get("outputs"),
                raw_record.get("payload"),
                raw_record.get("inputs_by_source"),
            )

        def normalize_error_signature(
            raw_record: dict[str, Any],
            *,
            source_kind: WorkflowFailureClusterSourceKind,
            node_type: str | None,
            error_text: object | None = None,
        ) -> tuple[str, str, str | None]:
            outputs = raw_record.get("outputs")
            signature_outputs = dict(outputs) if isinstance(outputs, dict) else {}
            signature_outputs["source_kind"] = source_kind
            if node_type:
                signature_outputs["node_type"] = node_type
            if source_kind == "trigger":
                signature_outputs.setdefault(
                    "trigger_type",
                    _coerce_optional_string(raw_record.get("trigger_type")),
                )
            elif source_kind == "effect":
                signature_outputs.setdefault(
                    "effect_type",
                    _coerce_optional_string(raw_record.get("effect_type")),
                )
            elif source_kind == "gate":
                signature_outputs.setdefault(
                    "gate_type",
                    _coerce_optional_string(raw_record.get("gate_type")),
                )
            summary, key = _normalize_workflow_failure_signature(error_text, signature_outputs)
            return _coerce_optional_string(error_text) or summary, summary, key

        failure_statuses = {"failed", "error_routed"}
        activity_records: list[dict[str, Any]] = []

        def register_record(
            raw_record: dict[str, Any],
            *,
            source_kind: WorkflowFailureClusterSourceKind,
            record_key: str,
        ) -> None:
            node_field = {
                "trigger": "trigger_id",
                "effect": "effect_id",
                "gate": "gate_id",
            }[source_kind]
            type_field = {
                "trigger": "trigger_type",
                "effect": "effect_type",
                "gate": "gate_type",
            }[source_kind]
            node_id = _coerce_optional_string(raw_record.get(node_field))
            if node_id is None:
                return
            if source_kind == "trigger":
                activity_at = latest_of(
                    raw_record.get("delivered_at"),
                    raw_record.get("inflight_at"),
                    raw_record.get("occurred_at"),
                )
            else:
                activity_at = latest_of(raw_record.get("finished_at"), raw_record.get("started_at"))
            if activity_at is None:
                return

            node_info = node_metadata.get(node_id, {})
            graph_id, graph_info = graph_context_for_node(node_id)
            raw_status = _coerce_optional_string(raw_record.get("status"))
            node_type = _coerce_optional_string(
                raw_record.get(type_field)
            ) or _coerce_optional_string(node_info.get("node_type"))
            display_name = _coerce_optional_string(node_info.get("display_name"))
            linked_payloads = payload_sources(raw_record)
            session_ids = collect_keyed_values(
                *linked_payloads, suffixes=("session_id", "session_ids")
            )
            pr_ids = collect_keyed_values(*linked_payloads, suffixes=("pr_id", "pr_ids"))
            ado_item_ids = collect_keyed_values(
                *linked_payloads,
                suffixes=("ado_item_id", "ado_item_ids", "work_item_id", "work_item_ids"),
            )
            issue_ids = collect_keyed_values(*linked_payloads, suffixes=("issue_id", "issue_ids"))
            artifact_ids = collect_artifact_ids(*linked_payloads)
            raw_error: str | None = _coerce_optional_string(raw_record.get("error"))
            if raw_error is None:
                raw_error = _coerce_optional_string(raw_record.get("binding_error"))
            if raw_error is None and isinstance(raw_record.get("outputs"), dict):
                outputs = raw_record.get("outputs")
                raw_error = _coerce_optional_string(outputs.get("error_message"))
            if raw_error is None and isinstance(raw_record.get("outputs"), dict):
                outputs = raw_record.get("outputs")
                raw_error = _coerce_optional_string(outputs.get("message"))
            if raw_error is None:
                raw_error = _coerce_optional_string(raw_record.get("clear_reason"))
            error_text, error_signature, error_signature_key = normalize_error_signature(
                raw_record,
                source_kind=source_kind,
                node_type=node_type,
                error_text=raw_error,
            )
            failure_candidate = (
                (source_kind == "trigger" and bool(raw_error))
                or (source_kind == "effect" and raw_status in failure_statuses)
                or (source_kind == "gate" and raw_status == "failed")
            )
            success_activity = not failure_candidate
            event_id = _coerce_optional_string(raw_record.get("event_id"))
            if source_kind != "trigger" and event_id is None:
                event_id = _coerce_optional_string(raw_record.get("execution_id"))
            record = {
                "activity_at": activity_at,
                "event_id": event_id,
                "trace_id": _coerce_optional_string(raw_record.get("trace_id")),
                "session_ids": session_ids,
                "pr_ids": pr_ids,
                "ado_item_ids": ado_item_ids,
                "issue_ids": issue_ids,
                "artifact_ids": artifact_ids,
                "error_text": error_text,
                "error_signature": error_signature,
                "error_signature_key": error_signature_key,
                "failure_candidate": failure_candidate,
                "success_activity": success_activity,
                "dismissed": bool(raw_record.get("dismissed_at")),
                "cleared": bool(
                    raw_record.get("cleared_at")
                    or (source_kind == "trigger" and raw_record.get("clear_reason"))
                ),
                "raw_status": raw_status,
                "graph_id": graph_id,
                "graph_name": graph_info["graph_name"],
                "graph_description": graph_info["graph_description"],
                "directory_path": graph_info["directory_path"],
                "node_id": node_id,
                "node_display_name": display_name,
                "node_type": node_type,
                "source_kind": source_kind,
                "trigger_type": _coerce_optional_string(raw_record.get("trigger_type"))
                if source_kind == "trigger"
                else None,
                "effect_type": _coerce_optional_string(raw_record.get("effect_type"))
                if source_kind == "effect"
                else None,
                "gate_type": _coerce_optional_string(raw_record.get("gate_type"))
                if source_kind == "gate"
                else None,
                "started_at": _coerce_optional_datetime(raw_record.get("started_at")),
                "finished_at": _coerce_optional_datetime(raw_record.get("finished_at")),
                "occurred_at": _coerce_optional_datetime(raw_record.get("occurred_at")),
                "record_key": record_key,
                "payload": raw_record,
            }
            activity_records.append(record)

        stores = self._workflow_run_store_paths()
        if stores:
            start_text = datetime_to_storage(start_at)
            end_text = datetime_to_storage(end_at)
            db_path = Path(stores["orchestration_store_path"])
            ensure_orchestration_schema(db_path)
            with connect_orchestration_db_read_only(
                db_path, deadline_seconds=WORKFLOW_REPORT_READ_DEADLINE_SECONDS,
            ) as connection:
                connection.execute("BEGIN")
                for row in connection.execute(
                    """
                    SELECT payload_json
                    FROM trigger_events
                    WHERE COALESCE(delivered_at, inflight_at, occurred_at) BETWEEN ? AND ?
                    ORDER BY COALESCE(delivered_at, inflight_at, occurred_at) ASC, event_id ASC
                    """,
                    (start_text, end_text),
                ):
                    raw_record = load_record(connection, row["payload_json"])
                    register_record(
                        raw_record,
                        source_kind="trigger",
                        record_key=str(
                            _coerce_optional_string(raw_record.get("event_id"))
                            or _coerce_optional_string(raw_record.get("trigger_id"))
                            or ""
                        ),
                    )
                for row in connection.execute(
                    """
                    SELECT payload_json
                    FROM effect_executions
                    WHERE COALESCE(finished_at, started_at) BETWEEN ? AND ?
                    ORDER BY COALESCE(finished_at, started_at) ASC, execution_id ASC
                    """,
                    (start_text, end_text),
                ):
                    raw_record = load_record(connection, row["payload_json"])
                    register_record(
                        raw_record,
                        source_kind="effect",
                        record_key=str(
                            _coerce_optional_string(raw_record.get("execution_id"))
                            or _coerce_optional_string(raw_record.get("event_id"))
                            or ""
                        ),
                    )
                for row in connection.execute(
                    """
                    SELECT payload_json
                    FROM gate_executions
                    WHERE COALESCE(finished_at, started_at) BETWEEN ? AND ?
                    ORDER BY COALESCE(finished_at, started_at) ASC, execution_id ASC
                    """,
                    (start_text, end_text),
                ):
                    raw_record = load_record(connection, row["payload_json"])
                    register_record(
                        raw_record,
                        source_kind="gate",
                        record_key=str(
                            _coerce_optional_string(raw_record.get("execution_id"))
                            or _coerce_optional_string(raw_record.get("event_id"))
                            or ""
                        ),
                    )
        activity_records.sort(
            key=lambda item: (
                item["activity_at"] or datetime.min.replace(tzinfo=UTC),
                item["source_kind"],
                item["node_id"],
                item["event_id"] or item["record_key"],
            ),
            reverse=True,
        )

        latest_success_at_by_node_source: dict[tuple[str, str, str], datetime] = {}
        clusters: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}

        def cluster_key(record: dict[str, Any]) -> tuple[str, str, str, str, str]:
            return (
                str(record["graph_id"]),
                str(record["node_id"]),
                str(record["source_kind"]),
                str(record["node_type"] or ""),
                str(record["error_signature_key"]),
            )

        for record in activity_records:
            node_source_key = (
                str(record["graph_id"]),
                str(record["node_id"]),
                str(record["source_kind"]),
            )
            if record["success_activity"]:
                latest_success_at_by_node_source.setdefault(node_source_key, record["activity_at"])
            if not record["failure_candidate"]:
                continue

            key = cluster_key(record)
            cluster = clusters.get(key)
            if cluster is None:
                cluster = {
                    "cluster_id": hashlib.sha256("|".join(key).encode("utf-8")).hexdigest()[:16],
                    "graph_id": record["graph_id"],
                    "graph_name": record["graph_name"],
                    "graph_description": record["graph_description"],
                    "directory_path": record["directory_path"],
                    "node_id": record["node_id"],
                    "node_display_name": record["node_display_name"],
                    "node_type": record["node_type"],
                    "trigger_type": record["trigger_type"],
                    "effect_type": record["effect_type"],
                    "gate_type": record["gate_type"],
                    "source_kind": record["source_kind"],
                    "error_signature": record["error_signature"],
                    "error_signature_key": record["error_signature_key"],
                    "count": 0,
                    "first_seen_at": record["activity_at"],
                    "last_seen_at": record["activity_at"],
                    "latest_error_summary": record["error_signature"],
                    "state": "historical",
                    "event_ids": [],
                    "trace_ids": [],
                    "session_ids": [],
                    "pr_ids": [],
                    "ado_item_ids": [],
                    "issue_ids": [],
                    "artifact_ids": [],
                    "examples": [],
                    "_example_limit": WORKFLOW_FAILURE_CLUSTER_EXAMPLE_LIMIT,
                    "_latest_failure_at": record["activity_at"],
                    "_latest_failure_dismissed": bool(record["dismissed"]),
                    "_latest_failure_cleared": bool(record["cleared"]),
                    "_latest_failure_status": record["raw_status"],
                    "_node_source_key": node_source_key,
                }
                clusters[key] = cluster
            else:
                if record["activity_at"] < cluster["first_seen_at"]:
                    cluster["first_seen_at"] = record["activity_at"]
            cluster["count"] += 1
            if record["activity_at"] > cluster["last_seen_at"]:
                cluster["last_seen_at"] = record["activity_at"]
                cluster["latest_error_summary"] = record["error_signature"]
                cluster["_latest_failure_at"] = record["activity_at"]
                cluster["_latest_failure_dismissed"] = bool(record["dismissed"])
                cluster["_latest_failure_cleared"] = bool(record["cleared"])
                cluster["_latest_failure_status"] = record["raw_status"]
            elif cluster.get("_latest_failure_at") is None:
                cluster["_latest_failure_at"] = record["activity_at"]
                cluster["_latest_failure_dismissed"] = bool(record["dismissed"])
                cluster["_latest_failure_cleared"] = bool(record["cleared"])
                cluster["_latest_failure_status"] = record["raw_status"]

            for value in [record["event_id"]]:
                if value and value not in cluster["event_ids"]:
                    cluster["event_ids"].append(value)
            for value in [record["trace_id"]]:
                if value and value not in cluster["trace_ids"]:
                    cluster["trace_ids"].append(value)
            for value in record["session_ids"]:
                if value not in cluster["session_ids"]:
                    cluster["session_ids"].append(value)
            for value in record["pr_ids"]:
                if value not in cluster["pr_ids"]:
                    cluster["pr_ids"].append(value)
            for value in record["ado_item_ids"]:
                if value not in cluster["ado_item_ids"]:
                    cluster["ado_item_ids"].append(value)
            for value in record["issue_ids"]:
                if value not in cluster["issue_ids"]:
                    cluster["issue_ids"].append(value)
            for value in record["artifact_ids"]:
                if value not in cluster["artifact_ids"]:
                    cluster["artifact_ids"].append(value)
            if len(cluster["examples"]) < cluster["_example_limit"]:
                cluster["examples"].append(
                    WorkflowFailureClusterExamplePayload(
                        event_id=record["event_id"],
                        trace_id=record["trace_id"],
                        session_id=record["session_ids"][0] if record["session_ids"] else None,
                        occurred_at=record["activity_at"],
                        started_at=record["started_at"],
                        finished_at=record["finished_at"],
                        error_summary=record["error_signature"],
                        run_state="errored" if record["failure_candidate"] else "active",
                        run_detail_href=(
                            f"#workflow?event_id={record['event_id']}"
                            if record["event_id"]
                            else None
                        ),
                        api_run_detail_href=(
                            f"/api/workflow-editor/run-detail?event_id={record['event_id']}"
                            if record["event_id"]
                            else None
                        ),
                        artifact_ids=list(record["artifact_ids"]),
                    )
                )

        cluster_payloads: list[WorkflowFailureClusterPayload] = []
        for cluster in clusters.values():
            latest_failure_at = cluster.pop("_latest_failure_at", None)
            latest_failure_dismissed = bool(cluster.pop("_latest_failure_dismissed", False))
            latest_failure_cleared = bool(cluster.pop("_latest_failure_cleared", False))
            latest_failure_status = _coerce_optional_string(
                cluster.pop("_latest_failure_status", None)
            )
            node_source_key = cluster.pop("_node_source_key", None)
            latest_success_at = (
                latest_success_at_by_node_source.get(node_source_key)
                if isinstance(node_source_key, tuple)
                else None
            )
            if (
                latest_failure_at is not None
                and latest_success_at is not None
                and latest_success_at > latest_failure_at
            ):
                cluster["state"] = "resolved"
            elif latest_failure_dismissed or latest_failure_cleared:
                cluster["state"] = "historical"
            elif latest_failure_status == "failed":
                cluster["state"] = "active"
            elif latest_failure_at is not None and latest_failure_at >= end_at - recent_threshold:
                cluster["state"] = "recent"
            else:
                cluster["state"] = "historical"

            cluster["examples"] = list(cluster["examples"])

        cluster_items = list(clusters.values())
        cluster_items.sort(
            key=lambda item: (
                item["last_seen_at"] or datetime.min.replace(tzinfo=UTC),
                item["cluster_id"],
            ),
            reverse=True,
        )
        cluster_items.sort(
            key=lambda item: WORKFLOW_FAILURE_CLUSTER_STATE_PRIORITY.get(
                str(item["state"]), len(WORKFLOW_FAILURE_CLUSTER_STATE_PRIORITY)
            )
        )

        limited_clusters = cluster_items[:normalized_limit]
        has_more = len(cluster_items) > len(limited_clusters)
        for cluster in limited_clusters:
            cluster_payloads.append(
                WorkflowFailureClusterPayload.model_validate(
                    {key: value for key, value in cluster.items() if not key.startswith("_")}
                )
            )

        return WorkflowFailureClusterReportPayload(
            generated_at=now,
            period_start=start_at,
            period_end=end_at,
            recent_threshold_seconds=normalized_recent_threshold_seconds,
            limit=normalized_limit,
            has_more=has_more,
            clusters=cluster_payloads,
        )

    def _read_conformance_reports(self) -> ConformanceReportsPayload:
        now = _utc_now()
        specs = (CONFORMANCE_REPORT_SPECS["trigger"], CONFORMANCE_REPORT_SPECS["effect"])
        return ConformanceReportsPayload(
            generated_at=now,
            stale_after_seconds=int(CONFORMANCE_REPORT_STALE_AFTER.total_seconds()),
            reports=[self._read_conformance_report(now=now, **spec) for spec in specs],
        )

    def _run_conformance_report(self, kind: str) -> ConformanceReportRunPayload:
        normalized_kind = kind.strip().lower()
        spec = CONFORMANCE_REPORT_SPECS.get(normalized_kind)
        if spec is None:
            allowed = ", ".join(sorted(CONFORMANCE_REPORT_SPECS))
            raise ValueError(f"Unknown conformance report {kind!r}. Expected one of: {allowed}.")

        suite_path = Path(str(spec["suite_path"]))
        suite_file = self.project_dir / suite_path
        if not suite_file.exists():
            raise RuntimeError(f"{spec['label']} conformance suite is missing: {suite_path}")

        artifact_path = (self.config.runtime_dir / str(spec["artifact_path"])).resolve()
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        env = os.environ.copy()
        env[str(spec["env_var"])] = str(artifact_path)
        src_path = str((self.project_dir / "src").resolve())
        existing_pythonpath = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = (
            src_path if not existing_pythonpath else f"{src_path}{os.pathsep}{existing_pythonpath}"
        )

        command = [sys.executable, "-m", "pytest", "-q", str(suite_path)]
        started_at = _utc_now()
        try:
            result = subprocess.run(
                command,
                cwd=self.project_dir,
                env=env,
                text=True,
                capture_output=True,
                timeout=CONFORMANCE_REPORT_RUN_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired as exc:
            output = _trim_conformance_process_output(
                _join_conformance_process_output(exc.stdout, exc.stderr)
            )
            message = (
                f"{spec['label']} conformance suite timed out after "
                f"{CONFORMANCE_REPORT_RUN_TIMEOUT_SECONDS} seconds."
            )
            if output:
                message = f"{message}\n{output}"
            raise RuntimeError(message) from exc
        except OSError as exc:
            raise RuntimeError(
                f"{spec['label']} conformance suite could not be started: {exc}"
            ) from exc

        completed_at = _utc_now()
        output = _trim_conformance_process_output(
            _join_conformance_process_output(result.stdout, result.stderr)
        )
        return ConformanceReportRunPayload(
            kind=normalized_kind,
            exit_code=result.returncode,
            started_at=started_at,
            completed_at=completed_at,
            duration_seconds=max(0.0, (completed_at - started_at).total_seconds()),
            output=output or None,
            reports=self._read_conformance_reports(),
        )

    def _read_conformance_report(
        self,
        *,
        kind: str,
        label: str,
        artifact_path: str,
        guidance: str,
        env_var: str,
        suite_path: str,
        now: datetime,
    ) -> ConformanceReportPayload:
        path = self.config.runtime_dir / artifact_path

        def report(
            state: str,
            *,
            status: str | None = None,
            generated_at: datetime | None = None,
            requirement_counts: dict[str, int] | None = None,
            requirement_count: int = 0,
            missing_coverage_count: int = 0,
            missing_coverage: dict[str, list[str]] | None = None,
            traceability_task_count: int = 0,
            failing_requirements: list[ConformanceRequirementSnippetPayload] | None = None,
            remediation_snippets: list[ConformanceRequirementSnippetPayload] | None = None,
            error: str | None = None,
        ) -> ConformanceReportPayload:
            return ConformanceReportPayload(
                kind=kind,
                label=label,
                artifact_path=artifact_path,
                state=state,
                status=status,
                generated_at=generated_at,
                requirement_counts=requirement_counts or {},
                requirement_count=requirement_count,
                missing_coverage_count=missing_coverage_count,
                missing_coverage=missing_coverage or {},
                traceability_task_count=traceability_task_count,
                failing_requirements=failing_requirements or [],
                remediation_snippets=remediation_snippets or [],
                error=error,
                guidance=guidance,
            )

        if not path.exists():
            return report("missing", error=f"{artifact_path} has not been generated yet.")

        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            return report("invalid", error=f"{artifact_path} is not valid JSON: {exc.msg}.")
        except OSError as exc:
            return report("invalid", error=f"{artifact_path} could not be read: {exc}.")

        if not isinstance(payload, dict):
            return report("invalid", error=f"{artifact_path} must contain a JSON object.")

        raw_generated_at = payload.get("generated_at")
        generated_at = _coerce_optional_datetime(raw_generated_at)
        if generated_at is None:
            return report("invalid", error=f"{artifact_path} is missing a valid generated_at.")

        status = (_coerce_optional_string(payload.get("status")) or "").lower()
        if status not in {"pass", "fail"}:
            return report(
                "invalid",
                generated_at=generated_at,
                error=f"{artifact_path} has unsupported status {payload.get('status')!r}.",
            )

        raw_requirements = payload.get("requirements")
        if not isinstance(raw_requirements, list):
            return report(
                "invalid",
                status=status,
                generated_at=generated_at,
                error=f"{artifact_path} requirements must be a list.",
            )
        raw_traceability_matrix = payload.get("traceability_matrix")
        if not isinstance(raw_traceability_matrix, dict):
            return report(
                "invalid",
                status=status,
                generated_at=generated_at,
                error=f"{artifact_path} traceability_matrix must be an object.",
            )
        raw_missing_coverage = payload.get("missing_coverage")
        if not isinstance(raw_missing_coverage, dict):
            return report(
                "invalid",
                status=status,
                generated_at=generated_at,
                error=f"{artifact_path} missing_coverage must be an object.",
            )

        requirement_counts: dict[str, int] = {}
        failing_requirements: list[ConformanceRequirementSnippetPayload] = []
        remediation_snippets: list[ConformanceRequirementSnippetPayload] = []
        for index, raw_requirement in enumerate(raw_requirements):
            if not isinstance(raw_requirement, dict):
                return report(
                    "invalid",
                    status=status,
                    generated_at=generated_at,
                    error=f"{artifact_path} requirements[{index}] must be an object.",
                )
            requirement_status = (
                _coerce_optional_string(raw_requirement.get("status")) or "unknown"
            ).lower()
            requirement_counts[requirement_status] = (
                requirement_counts.get(requirement_status, 0) + 1
            )
            requirement_id = _coerce_optional_string(raw_requirement.get("requirement_id"))
            raw_coverage = raw_requirement.get("coverage")
            coverage = (
                [str(item) for item in raw_coverage] if isinstance(raw_coverage, list) else []
            )
            snippet = ConformanceRequirementSnippetPayload(
                requirement_id=requirement_id or f"requirement[{index}]",
                status=requirement_status,
                details=_trim_conformance_text(raw_requirement.get("details")),
                remediation=_trim_conformance_text(raw_requirement.get("remediation")),
                coverage=coverage,
            )
            if (
                requirement_status != "pass"
                and len(failing_requirements) < CONFORMANCE_REPORT_SNIPPET_LIMIT
            ):
                failing_requirements.append(snippet)
            if snippet.remediation and len(remediation_snippets) < CONFORMANCE_REPORT_SNIPPET_LIMIT:
                remediation_snippets.append(snippet)

        missing_coverage: dict[str, list[str]] = {}
        missing_coverage_count = 0
        for task_name, raw_missing_items in raw_missing_coverage.items():
            if not isinstance(raw_missing_items, list):
                return report(
                    "invalid",
                    status=status,
                    generated_at=generated_at,
                    error=f"{artifact_path} missing_coverage.{task_name} must be a list.",
                )
            items = [str(item) for item in raw_missing_items]
            missing_coverage[str(task_name)] = items
            missing_coverage_count += len(items)

        state = status
        if generated_at < now - CONFORMANCE_REPORT_STALE_AFTER:
            state = "stale"

        return report(
            state,
            status=status,
            generated_at=generated_at,
            requirement_counts=dict(sorted(requirement_counts.items())),
            requirement_count=len(raw_requirements),
            missing_coverage_count=missing_coverage_count,
            missing_coverage=dict(sorted(missing_coverage.items())),
            traceability_task_count=len(raw_traceability_matrix),
            failing_requirements=failing_requirements,
            remediation_snippets=remediation_snippets,
        )

    def _workflow_run_search_sqlite_event_ids(
        self,
        stores: Mapping[str, Any],
        *,
        target_node_ids: set[str],
        include_completed: bool,
        limit: int,
        without_session: bool,
    ) -> tuple[list[str], int, bool]:
        node_ids = sorted(node_id for node_id in target_node_ids if node_id)
        if not node_ids:
            return [], 0, False

        node_value_placeholders = ", ".join("(?)" for _ in node_ids)
        target_params = node_ids
        candidate_conditions: list[str] = []
        non_completed_cte = ""
        if not include_completed:
            candidate_conditions.append("event_id IN (SELECT event_id FROM non_completed_events)")
            non_completed_cte = """
            ,
            non_completed_events AS (
                SELECT event_id
                FROM trigger_events
                WHERE delivery_state IN ('pending', 'inflight')
                UNION
                SELECT event_id
                FROM effect_executions
                WHERE status IN (
                    'running', 'waiting', 'failed', 'completion_pending', 'error_routed'
                )
                UNION
                SELECT event_id
                FROM gate_executions
                WHERE status IN ('running', 'waiting', 'failed', 'completion_pending')
            )
            """
        no_session_cte = ""
        if without_session:
            candidate_conditions.append("event_id NOT IN (SELECT event_id FROM session_events)")
            session_id_value_predicate = """
                LOWER(CAST(session_value.key AS TEXT)) LIKE '%session_id'
                AND session_value.type = 'text'
                AND TRIM(CAST(session_value.value AS TEXT)) <> ''
            """
            session_selects = []
            for kind, table, key in (("trigger", "trigger_events", "event_id"),
                                     ("effect", "effect_executions", "execution_id"),
                                     ("gate", "gate_executions", "execution_id")):
                session_selects.append(f"""
                    SELECT r.event_id FROM {table} r
                    JOIN candidate_events c ON c.event_id=r.event_id
                    LEFT JOIN workflow_execution_projections p ON p.record_kind='{kind}' AND p.record_id=r.{key}
                    WHERE CASE WHEN p.record_id IS NOT NULL THEN p.has_session_reference
                        ELSE EXISTS(SELECT 1 FROM json_tree(r.payload_json) AS session_value
                            WHERE {session_id_value_predicate}) END
                """)
            no_session_cte = ", session_events AS (" + " UNION ".join(session_selects) + ")"
        candidate_filter = (
            "WHERE " + " AND ".join(candidate_conditions) if candidate_conditions else ""
        )
        cte = f"""
            WITH target_nodes(node_id) AS (
                VALUES {node_value_placeholders}
            ),
            target_activity AS (
                SELECT event_id, COALESCE(delivered_at, inflight_at, occurred_at) AS activity_at
                FROM trigger_events
                WHERE trigger_id IN (SELECT node_id FROM target_nodes)
                UNION ALL
                SELECT event_id, COALESCE(finished_at, started_at) AS activity_at
                FROM effect_executions
                WHERE effect_id IN (SELECT node_id FROM target_nodes)
                UNION ALL
                SELECT event_id, COALESCE(finished_at, started_at) AS activity_at
                FROM gate_executions
                WHERE gate_id IN (SELECT node_id FROM target_nodes)
            ),
            candidate_events AS (
                SELECT event_id, MAX(activity_at) AS latest_activity_at
                FROM target_activity
                GROUP BY event_id
            )
            {non_completed_cte}
            {no_session_cte}
        """
        db_path = Path(stores["orchestration_store_path"])
        ensure_orchestration_schema(db_path)
        with connect_orchestration_db_read_only(
            db_path, deadline_seconds=WORKFLOW_REPORT_READ_DEADLINE_SECONDS,
        ) as connection:
            connection.execute("BEGIN")
            count_row = connection.execute(
                f"{cte} SELECT COUNT(*) AS matched FROM candidate_events {candidate_filter}",
                target_params,
            ).fetchone()
            matched_count = int(count_row["matched"] if count_row is not None else 0)
            fetch_limit = matched_count if without_session else min(500, limit + 1)
            if fetch_limit <= 0:
                return [], matched_count, False
            rows = connection.execute(
                f"""
                {cte}
                SELECT event_id
                FROM candidate_events
                {candidate_filter}
                ORDER BY latest_activity_at DESC, event_id DESC
                LIMIT ?
                """,
                [*target_params, fetch_limit],
            ).fetchall()
        event_ids = [
            event_id
            for row in rows
            for event_id in [_coerce_optional_string(row["event_id"])]
            if event_id
        ]
        return event_ids, matched_count, matched_count > len(event_ids)

    def _workflow_run_sqlite_records_for_event_ids(
        self,
        stores: Mapping[str, Any],
        *,
        event_ids: list[str],
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        normalized_event_ids = [
            event_id.strip()
            for event_id in dict.fromkeys(event_ids)
            if event_id and event_id.strip()
        ]
        if not normalized_event_ids:
            return {}, {}, {}

        db_path = Path(stores["orchestration_store_path"])
        ensure_orchestration_schema(db_path)
        queue_records: dict[str, Any] = {}
        effect_records: dict[str, Any] = {}
        gate_records: dict[str, Any] = {}

        def add_payload(
            target: dict[str, Any],
            row: Any,
            *,
            key_field: str,
        ) -> None:
            payload = load_record(connection, row["payload_json"])
            key = _coerce_optional_string(payload.get(key_field)) or _coerce_optional_string(
                row[key_field]
            )
            if key:
                target[key] = payload

        def event_id_batches() -> Iterator[list[str]]:
            batch_size = 500
            for index in range(0, len(normalized_event_ids), batch_size):
                yield normalized_event_ids[index : index + batch_size]

        with connect_orchestration_db_read_only(
            db_path, deadline_seconds=WORKFLOW_REPORT_READ_DEADLINE_SECONDS,
        ) as connection:
            connection.execute("BEGIN")
            for event_id_batch in event_id_batches():
                placeholders = ", ".join("?" for _ in event_id_batch)
                for row in connection.execute(
                    f"""
                    SELECT event_id, payload_json
                    FROM trigger_events
                    WHERE event_id IN ({placeholders})
                    ORDER BY occurred_at ASC, event_id ASC
                    """,
                    event_id_batch,
                ):
                    add_payload(queue_records, row, key_field="event_id")
                for row in connection.execute(
                    f"""
                    SELECT execution_id, payload_json
                    FROM effect_executions
                    WHERE event_id IN ({placeholders})
                    ORDER BY COALESCE(finished_at, started_at) ASC, execution_id ASC
                    """,
                    event_id_batch,
                ):
                    add_payload(effect_records, row, key_field="execution_id")
                for row in connection.execute(
                    f"""
                    SELECT execution_id, payload_json
                    FROM gate_executions
                    WHERE event_id IN ({placeholders})
                    ORDER BY COALESCE(finished_at, started_at) ASC, execution_id ASC
                    """,
                    event_id_batch,
                ):
                    add_payload(gate_records, row, key_field="execution_id")

        return queue_records, effect_records, gate_records

    def _workflow_run_detail_sqlite_records(
        self,
        stores: Mapping[str, Any],
        lookup: WorkflowRunLookupPayload,
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]] | None:
        event_keys = {
            value for value in (lookup.run_id, lookup.event_id, lookup.trigger_event_id) if value
        }
        trace_id = lookup.trace_id
        if not event_keys and trace_id is None:
            return None

        db_path = Path(stores["orchestration_store_path"])
        ensure_orchestration_schema(db_path)
        queue_records: dict[str, Any] = {}
        effect_records: dict[str, Any] = {}
        gate_records: dict[str, Any] = {}

        def add_payload(
            target: dict[str, Any],
            row: Any,
            *,
            key_field: str,
        ) -> None:
            payload = load_record(connection, row["payload_json"])
            key = _coerce_optional_string(payload.get(key_field)) or _coerce_optional_string(
                row[key_field]
            )
            if key:
                target[key] = payload

        def placeholders(values: set[str]) -> str:
            return ", ".join("?" for _ in values)

        with connect_orchestration_db_read_only(
            db_path, deadline_seconds=WORKFLOW_REPORT_READ_DEADLINE_SECONDS,
        ) as connection:
            connection.execute("BEGIN")
            if event_keys:
                event_key_params = sorted(event_keys)
                event_key_placeholders = placeholders(event_keys)
                for row in connection.execute(
                    f"""
                    SELECT event_id, payload_json
                    FROM trigger_events
                    WHERE event_id IN ({event_key_placeholders})
                    ORDER BY occurred_at ASC, event_id ASC
                    """,
                    event_key_params,
                ):
                    add_payload(queue_records, row, key_field="event_id")
                for row in connection.execute(
                    f"""
                    SELECT execution_id, payload_json
                    FROM effect_executions
                    WHERE event_id IN ({event_key_placeholders})
                    ORDER BY COALESCE(finished_at, started_at) ASC, execution_id ASC
                    """,
                    event_key_params,
                ):
                    add_payload(effect_records, row, key_field="execution_id")
                for row in connection.execute(
                    f"""
                    SELECT execution_id, payload_json
                    FROM gate_executions
                    WHERE event_id IN ({event_key_placeholders})
                    ORDER BY COALESCE(finished_at, started_at) ASC, execution_id ASC
                    """,
                    event_key_params,
                ):
                    add_payload(gate_records, row, key_field="execution_id")

            if trace_id is not None:
                for row in connection.execute(
                    """
                    SELECT event_id, payload_json
                    FROM trigger_events
                    WHERE trace_id = ?
                    ORDER BY occurred_at ASC, event_id ASC
                    """,
                    (trace_id,),
                ):
                    add_payload(queue_records, row, key_field="event_id")
                for row in connection.execute(
                    """
                    SELECT execution_id, payload_json
                    FROM effect_executions
                    WHERE trace_id = ?
                    ORDER BY COALESCE(finished_at, started_at) ASC, execution_id ASC
                    """,
                    (trace_id,),
                ):
                    add_payload(effect_records, row, key_field="execution_id")
                for row in connection.execute(
                    """
                    SELECT execution_id, payload_json
                    FROM gate_executions
                    WHERE trace_id = ?
                    ORDER BY COALESCE(finished_at, started_at) ASC, execution_id ASC
                    """,
                    (trace_id,),
                ):
                    add_payload(gate_records, row, key_field="execution_id")

            related_event_ids = {
                event_id
                for record in (
                    list(queue_records.values())
                    + list(effect_records.values())
                    + list(gate_records.values())
                )
                for event_id in [_coerce_optional_string(record.get("event_id"))]
                if event_id
            }
            if trace_id is not None and related_event_ids:
                event_id_params = sorted(related_event_ids)
                event_id_placeholders = placeholders(related_event_ids)
                for row in connection.execute(
                    f"""
                    SELECT event_id, payload_json
                    FROM trigger_events
                    WHERE event_id IN ({event_id_placeholders})
                    ORDER BY occurred_at ASC, event_id ASC
                    """,
                    event_id_params,
                ):
                    add_payload(queue_records, row, key_field="event_id")
                for row in connection.execute(
                    f"""
                    SELECT execution_id, payload_json
                    FROM effect_executions
                    WHERE event_id IN ({event_id_placeholders})
                    ORDER BY COALESCE(finished_at, started_at) ASC, execution_id ASC
                    """,
                    event_id_params,
                ):
                    add_payload(effect_records, row, key_field="execution_id")
                for row in connection.execute(
                    f"""
                    SELECT execution_id, payload_json
                    FROM gate_executions
                    WHERE event_id IN ({event_id_placeholders})
                    ORDER BY COALESCE(finished_at, started_at) ASC, execution_id ASC
                    """,
                    event_id_params,
                ):
                    add_payload(gate_records, row, key_field="execution_id")

        return queue_records, effect_records, gate_records

    def _read_workflow_run_detail(
        self,
        *,
        run_id: str | None = None,
        event_id: str | None = None,
        trigger_event_id: str | None = None,
        trace_id: str | None = None,
        session_id: str | None = None,
        artifact_id: str | None = None,
    ) -> WorkflowRunDetailPayload:
        from .workflow_run_detail import WorkflowRunDetailBuilder

        return WorkflowRunDetailBuilder(self).build(
            run_id=run_id,
            event_id=event_id,
            trigger_event_id=trigger_event_id,
            trace_id=trace_id,
            session_id=session_id,
            artifact_id=artifact_id,
        )
