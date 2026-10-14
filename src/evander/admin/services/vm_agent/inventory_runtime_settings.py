from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from evander.models import AgentRecord


def _text(value: Any) -> str | None:
    return (value.strip() or None) if isinstance(value, str) else None


def _settings_payload(execution_id: str, values: Mapping[str, Any]) -> dict[str, Any]:
    speed = values.get("fast_mode")
    return {
        "execution_id": execution_id,
        "model": _text(values.get("model")),
        "reasoning_effort": _text(values.get("reasoning_effort")),
        "fast_mode": speed if isinstance(speed, bool) or speed == "ultrafast" else None,
        **{
            key: source
            if (source := _text(values.get(key))) in {"prompt", "session", "default"}
            else None
            for key in ("reasoning_effort_source", "fast_mode_source")
        },
    }


class VmAgentRuntimeSettingsInventoryMixin:
    def _agent_with_runtime_settings(
        self, agent: AgentRecord, *, container_records: list[Any] | None = None
    ) -> AgentRecord:
        """Expose dispatch snapshots only; mutable session defaults are not run settings."""
        settings: list[dict[str, Any]] = []
        if agent.backend == "container_worker_vm" and agent.session_id:
            records = container_records
            if records is None:
                repository = self._container_worker_execution_repository()
                records = repository.list_active_by_session(
                    agent.session_id, {"created", "starting", "running"}
                )
            for record in sorted(records, key=self._container_execution_sort_key, reverse=True):
                if self._agent_status_is_active(self._container_execution_status(record)):
                    settings.append(_settings_payload(record.execution_id, record.metadata))
        elif agent.backend == "libvirt_vm" and agent.session_id:
            guest = agent.host.get("agent", {})
            dispatch_id = _text(guest.get("dispatch_id"))
            if dispatch_id and guest.get("state") == "running" and self._workflow_config_admin:
                from evander.daemons.effect_orchestrator import SqliteExecutionStore

                stores = self._workflow_config_admin.workflow_run_store_paths()
                store = SqliteExecutionStore(stores["orchestration_store_path"])
                receipts = store.list_prompt_records_for_dispatches({dispatch_id})
                receipts.extend(store.list_waiting_records("effect:agent:prompt"))
                candidates = []
                for record in receipts:
                    values = record.outputs
                    if (
                        record.status == "waiting"
                        and values.get("session_id") == agent.session_id
                        and values.get("dispatch_id") == dispatch_id
                        and values.get("queued") is not True
                        and values.get("success") is not False
                    ):
                        candidates.append(record)
                if candidates:
                    current = max(
                        candidates, key=lambda record: (record.started_at, record.execution_id)
                    )
                    settings.append(_settings_payload(dispatch_id, current.outputs))
        return agent.model_copy(
            update={"host": {**agent.host, "active_execution_settings": settings}}
        )
