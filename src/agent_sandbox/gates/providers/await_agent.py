from __future__ import annotations

import hashlib
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from agent_sandbox.agent_completion_watcher import build_agent_run_completed_event
from agent_sandbox.events.models import NODE_OUTPUTS_CONTEXT_KEY
from agent_sandbox.gates.providers.base import GateProviderBase
from agent_sandbox.storage import JsonStore
from agent_sandbox.triggers.queue import TriggerQueueRecord
from agent_sandbox.workflow_binding import resolve_workflow_bindings

logger = logging.getLogger(__name__)

_AWAIT_AGENT_STALE_GRACE_SECONDS = 120
_BRANCH_PORT_PAYLOAD_KEY = "__branch_port"
_FOR_EXECUTION_IDS_PAYLOAD_KEY = "__for_execution_ids"


class AwaitAgentPendingRecord(BaseModel):
    gate_id: str = Field(min_length=1)
    dispatch_id: str = Field(min_length=1)
    prompt_event_id: str = Field(min_length=1)
    prompt_source_node_id: str = Field(min_length=1)
    trace_id: str = Field(min_length=1)
    session_id: str | None = None
    vm_name: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    occurred_at: datetime


class _AwaitAgentStateStoreAdapter:
    def __init__(self, path: Path) -> None:
        self.store = JsonStore(path, AwaitAgentPendingRecord)

    def read_all(self) -> dict[str, AwaitAgentPendingRecord]:
        return self.store.read_all()

    def get(self, key: str) -> AwaitAgentPendingRecord | None:
        return self.store.get(key)

    def put(self, key: str, value: AwaitAgentPendingRecord) -> None:
        self.store.put(key, value)

    def delete(self, key: str) -> None:
        self.store.delete(key)


class AwaitAgentGateProvider(GateProviderBase):
    def __init__(self) -> None:
        super().__init__()
        self._adapter: _AwaitAgentStateStoreAdapter | None = None

    def load_state(self, **kwargs: Any) -> _AwaitAgentStateStoreAdapter:
        del kwargs
        if self._adapter is not None:
            return self._adapter
        runtime_config = self.runtime_context.resolved_config if self.runtime_context is not None else None
        runtime_dir = Path(getattr(runtime_config, "runtime_dir", Path("runtime")))
        provider_state = getattr(runtime_config, "provider_state", {}) if runtime_config is not None else {}
        store_path = _provider_state_path(
            provider_state,
            namespace=("gates", "await_agent", "pending_store_path"),
            default=runtime_dir / "providers" / "gates" / "await-agent" / "pending.json",
        )
        _migrate_legacy_store(
            legacy_path=runtime_dir / "await-agent-pending.json",
            provider_owned_path=store_path,
        )
        self._adapter = _AwaitAgentStateStoreAdapter(store_path)
        return self._adapter

    def cleanup(self, **kwargs: Any) -> None:
        del kwargs
        self._adapter = None

    def execute(self, **kwargs: Any) -> bool:
        worker = kwargs["worker"]
        queue_record = kwargs["queue_record"]
        gate = kwargs["gate"]
        graph = kwargs["graph"]
        started_at = kwargs["started_at"]
        config_revision = kwargs["config_revision"]
        upstream_payload = kwargs["upstream_payload"]
        upstream_output_names = kwargs["upstream_output_names"]

        resolved = resolve_workflow_bindings(
            {
                "session_id": gate.session_id,
                "vm_name": gate.vm_name,
            },
            upstream_payload,
            upstream_output_names,
            system_value_resolver=worker._system_value_resolver_for(
                started_at=started_at,
                queue_record=queue_record,
                node_id=gate.id,
            ),
            strict_validation=True,
        )
        resolved_config = worker._require_object_payload(resolved.resolved_config, gate_type="gate:await-agent")
        self._register_prompt(
            worker=worker,
            queue_record=queue_record,
            gate=gate,
            resolved_config=resolved_config,
            config_revision=config_revision,
            started_at=started_at,
        )
        return True

    def handle_completion_event(self, **kwargs: Any) -> int:
        worker = kwargs["worker"]
        queue_record = kwargs["queue_record"]
        graph = kwargs["graph"]
        completed = 0
        for gate in graph.gates_by_id.values():
            if getattr(gate, "type", "") != "gate:await-agent":
                continue
            if self._execute_completion(worker=worker, queue_record=queue_record, gate=gate):
                completed += 1
        return completed

    def reconcile(self, **kwargs: Any) -> int:
        worker = kwargs["worker"]
        graph = kwargs["graph"]
        self._reconcile_pending(worker=worker, graph=graph)
        return self._complete_pending_directly(worker=worker, graph=graph)

    def force_complete(self, **kwargs: Any) -> bool:
        worker = kwargs["worker"]
        graph = kwargs["graph"]
        dispatch_id = str(kwargs["dispatch_id"] or "").strip()
        if not dispatch_id:
            raise ValueError("dispatch_id is required")
        key, pending = next(
            ((record_key, record) for record_key, record in self._store().read_all().items() if record.dispatch_id == dispatch_id),
            (None, None),
        )
        if pending is None:
            return False
        gate = graph.gates_by_id.get(pending.gate_id)
        completed = self._complete_pending_record(worker=worker, pending=pending, gate=gate, log_skips=True, key=key)
        if completed:
            worker.run_once_with_lock()
        return completed

    def _store(self):
        return self.load_state()

    def _register_prompt(self, *, worker: Any, queue_record: TriggerQueueRecord, gate: Any, resolved_config: dict[str, Any], config_revision: str, started_at: datetime) -> None:
        dispatch_id = str(queue_record.payload.get("dispatch_id") or "").strip()
        session_id = str(resolved_config.get("session_id") or "").strip() or None
        vm_name = str(resolved_config.get("vm_name") or "").strip() or None
        if not dispatch_id:
            worker._write_gate_record(
                queue_record=queue_record,
                gate=gate,
                status="failed",
                started_at=started_at,
                resolved_config=resolved_config,
                outputs={},
                inputs_by_source={queue_record.trigger_id: dict(queue_record.payload)},
                input_event_ids_by_source={queue_record.trigger_id: queue_record.event_id},
                input_occurred_at_by_source={queue_record.trigger_id: queue_record.occurred_at},
                config_revision=config_revision,
                error="gate:await-agent requires upstream effect output dispatch_id",
            )
            return
        if session_id is None and vm_name is None:
            worker._write_gate_record(
                queue_record=queue_record,
                gate=gate,
                status="failed",
                started_at=started_at,
                resolved_config=resolved_config,
                outputs={},
                inputs_by_source={queue_record.trigger_id: dict(queue_record.payload)},
                input_event_ids_by_source={queue_record.trigger_id: queue_record.event_id},
                input_occurred_at_by_source={queue_record.trigger_id: queue_record.occurred_at},
                config_revision=config_revision,
                error="gate:await-agent requires resolved session_id or vm_name",
            )
            return
        key = f"{gate.id}:{dispatch_id}"
        self._store().put(
            key,
            AwaitAgentPendingRecord(
                gate_id=gate.id,
                dispatch_id=dispatch_id,
                prompt_event_id=queue_record.event_id,
                prompt_source_node_id=queue_record.trigger_id,
                trace_id=queue_record.trace_id,
                session_id=session_id,
                vm_name=vm_name,
                payload=dict(queue_record.payload),
                occurred_at=queue_record.occurred_at,
            ),
        )
        worker._write_gate_record(
            queue_record=queue_record,
            gate=gate,
            status="waiting",
            started_at=started_at,
            resolved_config={**resolved_config, "dispatch_id": dispatch_id, "completion_trigger_id": getattr(gate, "completion_trigger_id", None)},
            outputs={},
            inputs_by_source={queue_record.trigger_id: dict(queue_record.payload)},
            input_event_ids_by_source={queue_record.trigger_id: queue_record.event_id},
            input_occurred_at_by_source={queue_record.trigger_id: queue_record.occurred_at},
            config_revision=config_revision,
        )

    def _execute_completion(self, *, worker: Any, queue_record: TriggerQueueRecord, gate: Any) -> bool:
        started_at = datetime.now(UTC)
        dispatch_id = str(queue_record.payload.get("dispatch_id") or "").strip()
        if not dispatch_id:
            return False
        key = f"{gate.id}:{dispatch_id}"
        pending = self._store().get(key)
        if pending is None:
            return False
        outputs = {k: v for k, v in queue_record.payload.items() if k not in {NODE_OUTPUTS_CONTEXT_KEY, _BRANCH_PORT_PAYLOAD_KEY, _FOR_EXECUTION_IDS_PAYLOAD_KEY}}
        merged_payload = _payload_with_node_output_context(
            source_payload=pending.payload,
            source_node_id=pending.prompt_source_node_id,
            completed_node_id=queue_record.trigger_id,
            outputs=outputs,
        )
        merged_queue_record = TriggerQueueRecord(
            event_id=queue_record.event_id,
            trigger_id=queue_record.trigger_id,
            trigger_type=queue_record.trigger_type,
            trace_id=queue_record.trace_id,
            occurred_at=queue_record.occurred_at,
            payload=merged_payload,
            delivery_state=queue_record.delivery_state,
            delivered_at=queue_record.delivered_at,
            idempotency_key=queue_record.idempotency_key,
        )
        worker._write_gate_record(
            queue_record=merged_queue_record,
            gate=gate,
            status="succeeded",
            started_at=started_at,
            resolved_config={"dispatch_id": dispatch_id, "completion_trigger_id": getattr(gate, "completion_trigger_id", None)},
            outputs=outputs,
            inputs_by_source={pending.prompt_source_node_id: dict(pending.payload), queue_record.trigger_id: dict(queue_record.payload)},
            input_event_ids_by_source={pending.prompt_source_node_id: pending.prompt_event_id, queue_record.trigger_id: queue_record.event_id},
            input_occurred_at_by_source={pending.prompt_source_node_id: pending.occurred_at, queue_record.trigger_id: queue_record.occurred_at},
            publish_completion_event=True,
        )
        self._store().delete(key)
        return True

    def _reconcile_pending(self, *, worker: Any, graph: Any) -> None:
        pending_by_key = self._store().read_all()
        if not pending_by_key:
            return
        summaries = _list_vm_summaries(worker)
        summaries_by_vm_name: dict[str, list[Any]] = {}
        summaries_by_session_id: dict[str, list[Any]] = {}
        for summary in summaries:
            vm_name = str(getattr(summary, "vm_name", "") or "").strip()
            if vm_name:
                summaries_by_vm_name.setdefault(vm_name, []).append(summary)
            for session_id in getattr(summary, "session_ids", []) or ():
                normalized = str(session_id or "").strip()
                if normalized:
                    summaries_by_session_id.setdefault(normalized, []).append(summary)
        now = datetime.now(UTC)
        for key, pending in pending_by_key.items():
            if pending.gate_id not in graph.gates_by_id:
                self._store().delete(key)
                continue
            if (now - pending.occurred_at).total_seconds() < _AWAIT_AGENT_STALE_GRACE_SECONDS:
                continue
            session_id, vm_name = _await_agent_pending_target(pending)
            candidates: list[Any] = []
            if session_id:
                candidates.extend(summaries_by_session_id.get(session_id, ()))
            if vm_name:
                candidates.extend(summaries_by_vm_name.get(vm_name, ()))
            session_id, vm_name = _await_agent_pending_target(pending, summaries=candidates)
            if not candidates:
                continue
            matches_active_dispatch = any(
                str(getattr(summary, "dispatch_id", "") or "").strip() == pending.dispatch_id
                and str(getattr(summary, "agent_state", "") or "").strip() == "running"
                for summary in candidates
            )
            if matches_active_dispatch:
                continue
            replaced_or_unavailable = any(
                (
                    str(getattr(summary, "dispatch_id", "") or "").strip()
                    and str(getattr(summary, "dispatch_id", "") or "").strip() != pending.dispatch_id
                )
                or str(getattr(summary, "libvirt_state", "") or "").strip() != "running"
                for summary in candidates
            )
            if replaced_or_unavailable:
                self._store().delete(key)

    def _complete_pending_directly(self, *, worker: Any, graph: Any) -> int:
        pending_by_key = self._store().read_all()
        completed = 0
        for key, pending in pending_by_key.items():
            gate = graph.gates_by_id.get(pending.gate_id)
            if self._complete_pending_record(worker=worker, pending=pending, gate=gate, log_skips=True, key=key):
                completed += 1
        return completed

    def _complete_pending_record(self, *, worker: Any, pending: AwaitAgentPendingRecord, gate: Any, log_skips: bool, key: str | None) -> bool:
        if gate is None or getattr(gate, "type", "") != "gate:await-agent":
            return False
        event = self._build_completion_event(worker=worker, pending=pending, log_skips=log_skips, key=key)
        if event is None:
            return False
        queue_record = TriggerQueueRecord(
            event_id=event.event_id,
            trigger_id=event.trigger_id,
            trigger_type=event.trigger_type,
            trace_id=event.trace_id or pending.trace_id,
            occurred_at=event.occurred_at,
            payload=event.payload,
            delivery_state="delivered",
            delivered_at=datetime.now(UTC),
            idempotency_key=f"__await-agent-completion__:{pending.dispatch_id}",
        )
        self._execute_completion(worker=worker, queue_record=queue_record, gate=gate)
        return self._store().get(f"{pending.gate_id}:{pending.dispatch_id}") is None

    def _build_completion_event(self, *, worker: Any, pending: AwaitAgentPendingRecord, log_skips: bool, key: str | None):
        summaries = _list_vm_summaries(worker)
        session_id, vm_name = _await_agent_pending_target(pending, summaries=summaries)
        if not vm_name:
            return None
        matching_summary = _match_completion_summary(summaries=summaries, pending=pending, vm_name=vm_name, session_id=session_id)
        if matching_summary is None:
            return None
        if str(getattr(matching_summary, "agent_state", "") or "").strip() == "running":
            return None
        event = build_agent_run_completed_event(
            trigger_id="__await-agent-completion__",
            dispatch_id=pending.dispatch_id,
            vm_user="agent",
            summary=matching_summary,
            session_id=session_id,
            directory_path=(getattr(matching_summary, "directory_paths", None) or [None])[0],
        )
        if log_skips:
            logger.info("Await-agent completion event prepared key=%s dispatch_id=%s vm_name=%s event_id=%s", key, pending.dispatch_id, vm_name, event.event_id)
        return event


def _list_vm_summaries(worker: Any) -> list[Any]:
    admin_service = getattr(worker.provider_factory, "admin_service", None)
    if admin_service is None:
        return []
    return list(admin_service.list_vms(include_inactive=True, omit_state=False, vm_user="agent", directory_path=None, direct_only=False))


def _await_agent_pending_target(pending: AwaitAgentPendingRecord, *, summaries: list[Any] | None = None) -> tuple[str | None, str | None]:
    session_id = str(pending.session_id or "").strip() or None
    vm_name = str(pending.vm_name or "").strip() or None
    if not summaries:
        return session_id, vm_name
    if session_id is None and vm_name is not None:
        session_id = next((str(session or "").strip() for summary in summaries if str(getattr(summary, "vm_name", "") or "").strip() == vm_name for session in (getattr(summary, "session_ids", []) or []) if str(session or "").strip()), None)
    if vm_name is None and session_id is not None:
        vm_name = next((str(getattr(summary, "vm_name", "") or "").strip() for summary in summaries if session_id in {str(item or "").strip() for item in (getattr(summary, "session_ids", []) or [])} and str(getattr(summary, "vm_name", "") or "").strip()), None)
    return session_id, vm_name


def _match_completion_summary(*, summaries: list[Any], pending: AwaitAgentPendingRecord, vm_name: str, session_id: str | None) -> Any | None:
    exact_match = next((summary for summary in summaries if str(getattr(summary, "vm_name", "") or "").strip() == vm_name and str(getattr(summary, "dispatch_id", "") or "").strip() == pending.dispatch_id), None)
    if exact_match is not None:
        return exact_match
    if not session_id:
        return None
    return next((summary for summary in summaries if str(getattr(summary, "vm_name", "") or "").strip() == vm_name and session_id in {str(item or "").strip() for item in (getattr(summary, "session_ids", []) or [])} and not str(getattr(summary, "dispatch_id", "") or "").strip() and str(getattr(summary, "agent_state", "") or "").strip() != "running"), None)


def _node_output_context_from_payload(payload: dict[str, Any], *, source_node_id: str) -> dict[str, dict[str, Any]]:
    raw_context = payload.get(NODE_OUTPUTS_CONTEXT_KEY)
    if isinstance(raw_context, dict):
        context: dict[str, dict[str, Any]] = {}
        for node_id, node_payload in raw_context.items():
            if not isinstance(node_id, str) or not isinstance(node_payload, dict):
                continue
            context[node_id] = dict(node_payload)
        if context:
            return context
    flat_payload = {key: value for key, value in payload.items() if key not in {NODE_OUTPUTS_CONTEXT_KEY, _BRANCH_PORT_PAYLOAD_KEY, _FOR_EXECUTION_IDS_PAYLOAD_KEY}}
    return {source_node_id: flat_payload}


def _payload_with_node_output_context(*, source_payload: dict[str, Any], source_node_id: str, completed_node_id: str, outputs: dict[str, Any]) -> dict[str, Any]:
    payload = dict(outputs)
    inherited_context = _node_output_context_from_payload(source_payload, source_node_id=source_node_id)
    merged_context = {node_id: dict(node_payload) for node_id, node_payload in inherited_context.items()}
    merged_context[completed_node_id] = dict(outputs)
    payload[NODE_OUTPUTS_CONTEXT_KEY] = merged_context
    return payload


def _provider_state_path(provider_state: Any, *, namespace: tuple[str, ...], default: Path) -> Path:
    cursor: Any = provider_state if isinstance(provider_state, dict) else {}
    for key in namespace:
        if not isinstance(cursor, dict):
            return default
        cursor = cursor.get(key)
    if cursor is None:
        return default
    text = str(cursor).strip()
    return Path(text) if text else default


def _migrate_legacy_store(*, legacy_path: Path, provider_owned_path: Path) -> None:
    if not legacy_path.exists() or provider_owned_path.exists():
        return
    provider_owned_path.parent.mkdir(parents=True, exist_ok=True)
    legacy_path.replace(provider_owned_path)
