"""Workflow run search collection, session enrichment, and pagination."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from evander.admin.common import (
    _coerce_optional_datetime,
    _coerce_optional_string,
    _read_merged_json_objects,
)
from evander.admin.payloads import (
    WorkflowRunNodeActivityPayload,
    WorkflowRunSearchFilterPayload,
    WorkflowRunSearchItemPayload,
    WorkflowRunSearchPayload,
)
from evander.admin.workflow_report_helpers import (
    _collect_workflow_artifact_ids,
    _collect_workflow_keyed_values,
    _merge_run_node_activity,
)
from evander.communication_artifacts import CommunicationArtifactStore
from evander.models import ApprovalRequest

if TYPE_CHECKING:
    from .workflow_reports_service import WorkflowReportsAdminService


class WorkflowRunSearchBuilder:
    """Build one search from its selected records before enriching and filtering runs."""

    target_node_ids: set[str]
    graph_name: str | None
    stores: dict[str, Any]
    await_agent_payload: dict[str, Any]
    sqlite_event_ids: list[str]
    sqlite_matched_count: int | None
    sqlite_has_more_candidates: bool
    queue_records: dict[str, Any]
    effect_records: dict[str, Any]
    gate_records: dict[str, Any]
    approval_requests: dict[str, ApprovalRequest]
    artifact_store: CommunicationArtifactStore
    groups: dict[str, dict[str, Any]]
    session_ids_by_trace: dict[str, set[str]]
    matched_runs: list[WorkflowRunSearchItemPayload]

    def __init__(
        self,
        *,
        service: WorkflowReportsAdminService,
        node_id: str | None,
        graph_id: str | None,
        limit: int,
        without_session: bool,
        include_completed: bool,
        clock: Callable[[], datetime],
    ) -> None:
        self.service = service
        self.node_id = node_id
        self.graph_id = graph_id
        self.limit = limit
        self.without_session = without_session
        self.include_completed = include_completed
        self.clock = clock

    def build(self) -> WorkflowRunSearchPayload:
        self._select_nodes()
        self._read_records()
        self._collect_activity()
        self._enrich_session_references()
        self._index_sessions_by_trace()
        self._project_matching_runs()
        return self._paginate()

    def _select_nodes(self) -> None:
        document = self.service._read_composed_workflow_editor_document()

        self.target_node_ids: set[str] = set()
        self.graph_name: str | None = None
        if self.graph_id is not None:
            graph = next(
                (candidate for candidate in document.graphs if candidate.id == self.graph_id),
                None,
            )
            if graph is None:
                raise ValueError(f"Workflow graph not found: {self.graph_id}")
            self.target_node_ids.update(graph.node_ids)
            self.graph_name = graph.name
        if self.node_id is not None:
            self.target_node_ids.add(self.node_id)
        if not self.target_node_ids:
            raise ValueError("Workflow run search requires node_id or graph_id.")

    def _read_records(self) -> None:
        self.stores = self.service._workflow_run_store_paths()
        self.await_agent_payload = _read_merged_json_objects(
            self.stores["legacy_await_agent_store_path"],
            self.stores["await_agent_store_path"],
        )
        (
            self.sqlite_event_ids,
            self.sqlite_matched_count,
            self.sqlite_has_more_candidates,
        ) = self.service._workflow_run_search_sqlite_event_ids(
            self.stores,
            target_node_ids=self.target_node_ids,
            include_completed=self.include_completed,
            limit=self.limit,
            without_session=self.without_session,
        )
        self.queue_records, self.effect_records, self.gate_records = (
            self.service._workflow_run_sqlite_records_for_event_ids(
                self.stores,
                event_ids=self.sqlite_event_ids,
            )
        )
        self.approval_requests = self.service.runtime.approvals.read_all()
        self.artifact_store = self.service._communication_artifact_store()

    def _collect_activity(self) -> None:
        self.groups: dict[str, dict[str, Any]] = {}
        self._collect_trigger_events()
        self._collect_effect_executions()
        self._collect_gate_executions()
        self._collect_await_agents()

    def _collect_trigger_events(self) -> None:
        for record_key, record in self.queue_records.items():
            raw_record = _normalize_record_payload(record)
            if not raw_record:
                continue
            event_id = _record_event_id(raw_record, record_key)
            occurred_at = _coerce_optional_datetime(raw_record.get("occurred_at"))
            _register_activity(
                self.groups,
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
                latest_activity_at_value=_latest_of(
                    raw_record.get("delivered_at"),
                    raw_record.get("inflight_at"),
                    raw_record.get("occurred_at"),
                ),
                source_node_id=_coerce_optional_string(raw_record.get("trigger_id")),
                source_trigger_type=_coerce_optional_string(raw_record.get("trigger_type")),
                delivery_state=_coerce_optional_string(raw_record.get("delivery_state")),
                error=_coerce_optional_string(raw_record.get("error")),
            )

    def _collect_effect_executions(self) -> None:
        for execution_id, record in self.effect_records.items():
            raw_record = _normalize_record_payload(record)
            if not raw_record:
                continue
            event_id = _record_event_id(raw_record, execution_id)
            payload_sources = [
                raw_record.get("resolved_config"),
                raw_record.get("input_payload"),
                raw_record.get("outputs"),
            ]
            _register_activity(
                self.groups,
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

    def _collect_gate_executions(self) -> None:
        for execution_id, record in self.gate_records.items():
            raw_record = _normalize_record_payload(record)
            if not raw_record:
                continue
            event_id = _record_event_id(raw_record, execution_id)
            payload_sources = [
                raw_record.get("resolved_config"),
                raw_record.get("input_payload"),
                raw_record.get("outputs"),
                raw_record.get("inputs_by_source"),
            ]
            _register_activity(
                self.groups,
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

    def _collect_await_agents(self) -> None:
        for record_key, raw_record in self.await_agent_payload.items():
            if not isinstance(raw_record, dict):
                continue
            event_id = _coerce_optional_string(raw_record.get("prompt_event_id")) or str(record_key)
            _register_activity(
                self.groups,
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

    def _enrich_session_references(self) -> None:
        for group in self.groups.values():
            for request_id in list(group["request_ids"]):
                request = self.approval_requests.get(request_id)
                if request is not None:
                    group["session_ids"].add(request.session_id)
            for artifact_id in list(group["artifact_ids"]):
                try:
                    artifact_payload = self.artifact_store.payload(artifact_id)
                except FileNotFoundError:
                    continue
                artifact_session_id = _coerce_optional_string(artifact_payload.get("session_id"))
                if artifact_session_id:
                    group["session_ids"].add(artifact_session_id)

    def _index_sessions_by_trace(self) -> None:
        self.session_ids_by_trace: dict[str, set[str]] = {}
        for group in self.groups.values():
            for trace_id_value in group["trace_ids"]:
                if trace_id_value:
                    self.session_ids_by_trace.setdefault(trace_id_value, set()).update(
                        group["session_ids"]
                    )

    def _project_matching_runs(self) -> None:
        self.matched_runs: list[WorkflowRunSearchItemPayload] = []
        active_statuses = {"pending", "inflight", "running", "waiting", "completion_pending"}
        for group in self.groups.values():
            matched_node_ids = sorted(group["node_ids"] & self.target_node_ids)
            if not matched_node_ids:
                continue
            effective_session_ids = set(group["session_ids"])
            for trace_id_value in group["trace_ids"]:
                effective_session_ids.update(self.session_ids_by_trace.get(trace_id_value, set()))
            if self.without_session and effective_session_ids:
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
            if not self.include_completed and run_state == "completed":
                continue
            self.matched_runs.append(
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

    def _paginate(self) -> WorkflowRunSearchPayload:
        self.matched_runs.sort(
            key=lambda run: (
                run.latest_activity_at or run.occurred_at or datetime.min.replace(tzinfo=UTC),
                run.occurred_at or datetime.min.replace(tzinfo=UTC),
                run.event_id,
            ),
            reverse=True,
        )
        limited_runs = self.matched_runs[: self.limit]
        matched_count = (
            max(self.sqlite_matched_count, len(self.matched_runs))
            if self.sqlite_matched_count is not None and not self.without_session
            else len(self.matched_runs)
        )
        return WorkflowRunSearchPayload(
            generated_at=self.clock(),
            filters=WorkflowRunSearchFilterPayload(
                node_id=self.node_id,
                graph_id=self.graph_id,
                graph_name=self.graph_name,
                without_session=self.without_session,
                include_completed=self.include_completed,
            ),
            runs=limited_runs,
            matched=matched_count,
            returned=len(limited_runs),
            limit=self.limit,
            has_more=matched_count > len(limited_runs)
            or (self.sqlite_has_more_candidates and not self.without_session),
        )


def _normalize_record_payload(record: object) -> dict[str, Any]:
    if isinstance(record, BaseModel):
        return record.model_dump(mode="json")
    if isinstance(record, dict):
        return dict(record)
    return {}


def _record_event_id(record: dict[str, Any], fallback: object) -> str:
    return _coerce_optional_string(record.get("event_id")) or str(fallback)


def _latest_of(*values: object) -> datetime | None:
    parsed = [
        parsed_value
        for parsed_value in (_coerce_optional_datetime(value) for value in values)
        if parsed_value is not None
    ]
    return max(parsed) if parsed else None


def _ensure_group(groups: dict[str, dict[str, Any]], event_key: str) -> dict[str, Any]:
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


def _update_group_metadata(
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
        not isinstance(previous_latest, datetime) or latest_activity_at_value > previous_latest
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


def _register_activity(
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
    group = _ensure_group(groups, event_key)
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
    _update_group_metadata(
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
        or _latest_of(finished_at_value, started_at_value, occurred_at_value),
        source_node_id=source_node_id,
        source_trigger_type=source_trigger_type,
        delivery_state=delivery_state,
        error=error,
    )
