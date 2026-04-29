from __future__ import annotations

from datetime import UTC

from agent_sandbox.events.models import TriggerEvent
from agent_sandbox.events.provider_models import ScriptRunRequestTriggerDefinition
from agent_sandbox.repositories.script_run_requests import (
    ScriptRunRequestRepository,
    ScriptRunRequestStore,
)

from ._cursor_state import TriggerCursorRepository, TriggerCursorStore
from .base import TriggerProviderBase


class ScriptRunRequestTriggerProvider(TriggerProviderBase):
    _CURSOR_FILENAME = "trigger-cursors-script-run-request.json"

    def poll(self) -> list[TriggerEvent]:
        trigger = self._trigger()
        cursor_store = self._cursor_store()
        state = cursor_store.get(trigger.id)
        emitted_ids = {
            str(value).strip()
            for value in state.get("emitted_request_ids", [])
            if str(value).strip()
        }

        requests = [
            request
            for request in self._store().list_requests()
            if self._in_directory_scope(request.directory_path)
        ]
        current_ids = {request.request_id for request in requests}

        events: list[TriggerEvent] = []
        for request in requests:
            if request.request_id in emitted_ids:
                continue
            events.append(
                TriggerEvent(
                    event_id=f"{trigger.id}:{request.request_id}",
                    trigger_id=trigger.id,
                    trigger_type=trigger.type,
                    occurred_at=request.created_at.astimezone(UTC),
                    payload={
                        "request_id": request.request_id,
                        "session_id": request.session_id,
                        "script_name": request.script_name,
                        "parameters": list(request.parameters),
                    },
                )
            )

        cursor_store.put(trigger.id, {"emitted_request_ids": sorted(current_ids)})
        return events

    def _in_directory_scope(self, request_directory_path: str) -> bool:
        directory_scope = (
            str(self.runtime_context.metadata.get("directory_path") or "root").strip() or "root"
        )
        return directory_scope == "root" or (
            request_directory_path == directory_scope
            or request_directory_path.startswith(f"{directory_scope}/")
        )

    def _store(self) -> ScriptRunRequestRepository:
        return ScriptRunRequestStore(
            self.runtime_context.resolved_config.runtime_dir / "script-run-requests-store.json"
        )

    def _cursor_store(self) -> TriggerCursorRepository:
        return TriggerCursorStore(
            self.runtime_context.resolved_config.runtime_dir / self._CURSOR_FILENAME
        )

    def _trigger(self) -> ScriptRunRequestTriggerDefinition:
        if not isinstance(self.trigger_definition, ScriptRunRequestTriggerDefinition):
            raise RuntimeError("ScriptRunRequestTriggerProvider initialized with wrong definition")
        return self.trigger_definition
