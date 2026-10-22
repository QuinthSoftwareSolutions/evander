"""Active workflow snapshot collection and ordered activity projection."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from evander.admin.common import _coerce_optional_datetime, _coerce_optional_string
from evander.admin.payloads import (
    WorkflowRunNodeActivityPayload,
    WorkflowRunRecentNodeActivityPayload,
    WorkflowRunSnapshotPayload,
    WorkflowRunSummaryPayload,
)
from evander.admin.workflow_report_helpers import _merge_run_node_activity

if TYPE_CHECKING:
    from .workflow_reports_service import WorkflowReportsAdminService


class WorkflowRunSnapshotBuilder:
    """Keep one snapshot's active runs and recent animation records together."""

    def __init__(
        self,
        *,
        service: WorkflowReportsAdminService,
        stores: dict[str, Any],
        now: datetime,
        pending_loader: Callable[..., dict[str, Any]],
    ) -> None:
        self.service = service
        self.stores = stores
        self.now = now
        self.lookback_start = now - timedelta(seconds=8)
        self.runtime_dir = stores["runtime_dir"]
        self.pending_loader = pending_loader
        self.runs_by_event: dict[str, dict[str, object]] = {}
        self.node_activity: dict[str, dict[str, int]] = {}
        self.recent_node_activity: list[WorkflowRunRecentNodeActivityPayload] = []

    def build(self) -> WorkflowRunSnapshotPayload:
        self._read_store_activity()
        self._register_queue_activity()
        self._register_effect_activity()
        self._register_gate_activity()
        self._register_await_activity()
        self._register_recent_activity()
        return self._build_payload()

    def _ensure_run(self, event_id: str) -> dict[str, object]:
        existing = self.runs_by_event.get(event_id)
        if existing is not None:
            return existing
        created: dict[str, object] = {
            "event_id": event_id,
            "active_nodes": [],
        }
        self.runs_by_event[event_id] = created
        return created

    def _register_node_activity(
        self,
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
        run = self._ensure_run(event_id)
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
        node_status_counts = self.node_activity.setdefault(normalized_node_id, {})
        node_status_counts[status] = node_status_counts.get(status, 0) + 1

    def _record_value(self, record: object, key: str) -> object | None:
        if isinstance(record, Mapping):
            return record.get(key)
        return getattr(record, key, None)

    def _normalize_activity_text(self, value: object) -> str | None:
        return _coerce_optional_string(value)

    def _activity_at_from_record(self, record: object) -> datetime | None:
        return max(
            (
                timestamp
                for timestamp in (
                    _coerce_optional_datetime(self._record_value(record, "finished_at")),
                    _coerce_optional_datetime(self._record_value(record, "delivered_at")),
                    _coerce_optional_datetime(self._record_value(record, "started_at")),
                    _coerce_optional_datetime(self._record_value(record, "inflight_at")),
                    _coerce_optional_datetime(self._record_value(record, "occurred_at")),
                )
                if timestamp is not None
            ),
            default=None,
        )

    def _register_recent_node_activity(
        self,
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
        normalized_source = self._normalize_activity_text(source)
        normalized_record_id = self._normalize_activity_text(record_id)
        normalized_event_id = self._normalize_activity_text(event_id)
        normalized_node_id = self._normalize_activity_text(node_id)
        normalized_status = self._normalize_activity_text(status)
        if normalized_source is None or normalized_record_id is None:
            return
        if not normalized_event_id or not normalized_node_id:
            return
        if normalized_status is None or activity_at is None:
            return
        if activity_at < self.lookback_start or activity_at > self.now:
            return
        self.recent_node_activity.append(
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
                trace_id=self._normalize_activity_text(trace_id),
                error=self._normalize_activity_text(error),
            )
        )

    def _collect_recent_activity(
        self,
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
            dismissed_at = _coerce_optional_datetime(self._record_value(raw_record, "dismissed_at"))
            if dismissed_at is not None:
                continue
            self._register_recent_node_activity(
                source=source,
                record_id=self._record_value(raw_record, record_id_field),
                event_id=self._record_value(raw_record, event_id_field),
                node_id=self._record_value(raw_record, node_id_field),
                status=(
                    self._record_value(raw_record, status_field)
                    if source != "trigger_event"
                    else self._record_value(raw_record, status_field) or "event"
                ),
                activity_at=self._activity_at_from_record(raw_record),
                started_at=self._record_value(raw_record, started_at_field)
                if started_at_field is not None
                else None,
                finished_at=self._record_value(raw_record, finished_at_field)
                if finished_at_field is not None
                else None,
                trace_id=self._record_value(raw_record, trace_id_field)
                if trace_id_field is not None
                else None,
                error=self._record_value(raw_record, error_field)
                if error_field is not None
                else None,
            )

    def _read_store_activity(self) -> None:
        queue_store, effect_store, gate_store = self.service._workflow_run_store_bundle(self.stores)
        self.queue_payload = {
            record["event_id"]: record for record in queue_store.list_active_summaries()
        }
        self.execution_payload = {
            record["execution_id"]: record for record in effect_store.list_active_summaries()
        }
        self.gate_payload = {
            record["execution_id"]: record for record in gate_store.list_active_summaries()
        }
        self.queue_recent_records = queue_store.list_activity_summaries_between(
            start_at=self.lookback_start, end_at=self.now
        )
        self.execution_recent_records = effect_store.list_activity_summaries_between(
            start_at=self.lookback_start, end_at=self.now
        )
        self.gate_recent_records = gate_store.list_activity_summaries_between(
            start_at=self.lookback_start, end_at=self.now
        )

    def _register_queue_activity(self) -> None:
        for event_id, raw_record in self.queue_payload.items():
            if not isinstance(raw_record, dict):
                continue
            delivery_state = str(raw_record.get("delivery_state") or "").strip()
            if delivery_state == "delivered":
                continue
            normalized_event_id = str(raw_record.get("event_id") or event_id).strip()
            source_node_id = str(raw_record.get("trigger_id") or "").strip()
            self._register_node_activity(
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

    def _register_effect_activity(self) -> None:
        for execution_id, raw_record in self.execution_payload.items():
            if not isinstance(raw_record, dict):
                continue
            status = str(raw_record.get("status") or "").strip()
            if raw_record.get("dismissed_at"):
                continue
            if status not in {"running", "waiting", "failed", "completion_pending", "error_routed"}:
                continue
            event_id = str(raw_record.get("event_id") or execution_id).strip()
            node_id = str(raw_record.get("effect_id") or "").strip()
            self._register_node_activity(
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

    def _register_gate_activity(self) -> None:
        for execution_id, raw_record in self.gate_payload.items():
            if not isinstance(raw_record, dict):
                continue
            status = str(raw_record.get("status") or "").strip()
            if raw_record.get("dismissed_at"):
                continue
            if status not in {"running", "waiting", "failed", "completion_pending"}:
                continue
            event_id = str(raw_record.get("event_id") or execution_id).strip()
            node_id = str(raw_record.get("gate_id") or "").strip()
            self._register_node_activity(
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

    def _register_await_activity(self) -> None:
        await_agent_payload = self.pending_loader(
            self.runtime_dir / "await-agent-pending.json",
            self.runtime_dir / "providers" / "gates" / "await-agent" / "pending.json",
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
            self._register_node_activity(
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

    def _register_recent_activity(self) -> None:
        self._collect_recent_activity(
            self.queue_recent_records,
            source="trigger_event",
            record_id_field="event_id",
            node_id_field="trigger_id",
            status_field="delivery_state",
            started_at_field="inflight_at",
            finished_at_field="delivered_at",
            trace_id_field="trace_id",
            error_field="error",
        )
        self._collect_recent_activity(
            self.execution_recent_records,
            source="effect_execution",
            record_id_field="execution_id",
            node_id_field="effect_id",
            status_field="status",
            started_at_field="started_at",
            finished_at_field="finished_at",
            trace_id_field="trace_id",
            error_field="error",
        )
        self._collect_recent_activity(
            self.gate_recent_records,
            source="gate_execution",
            record_id_field="execution_id",
            node_id_field="gate_id",
            status_field="status",
            started_at_field="started_at",
            finished_at_field="finished_at",
            trace_id_field="trace_id",
            error_field="error",
        )

    def _build_payload(self) -> WorkflowRunSnapshotPayload:
        self.recent_node_activity.sort(
            key=lambda item: (item.activity_at, item.activity_key), reverse=True
        )
        self.recent_node_activity = self.recent_node_activity[:200]

        runs = [
            WorkflowRunSummaryPayload.model_validate(
                {
                    **payload,
                    "active_nodes": _merge_run_node_activity(
                        payload.get("active_nodes", []),
                    ),
                }
            )
            for payload in self.runs_by_event.values()
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
            generated_at=self.now,
            runs=runs,
            node_activity={
                node_id: dict(sorted(counts.items()))
                for node_id, counts in sorted(self.node_activity.items())
            },
            recent_node_activity=self.recent_node_activity,
        )
