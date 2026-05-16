from __future__ import annotations

from datetime import UTC

from agent_sandbox.messaging.agent_prompt_store import AgentPromptStore
from agent_sandbox.events.models import TriggerEvent
from agent_sandbox.events.provider_models import AgentPromptNewTriggerDefinition
from agent_sandbox.repositories.session_manifests import JsonSessionManifestRepository

from ._cursor_state import TriggerCursorStore, merge_cursor_ids
from .base import TriggerProviderBase


class AgentPromptNewTriggerProvider(TriggerProviderBase):
    _CURSOR_FILENAME = "trigger-cursors-agent-prompt-new.json"

    def poll(self) -> list[TriggerEvent]:
        trigger = self._trigger()
        cursor_store = self._cursor_store()
        state = cursor_store.get(trigger.id)
        emitted_ids = {
            str(value).strip()
            for value in state.get("emitted_unread_message_ids", [])
            if str(value).strip()
        }

        current_unread = self._list_current_unread()
        current_unread.sort(key=lambda item: (item.created_at, item.message_id))

        events: list[TriggerEvent] = []
        for message in current_unread:
            if message.message_id in emitted_ids:
                continue
            prompt_payload = dict(message.prompt_payload or {})
            events.append(
                TriggerEvent(
                    event_id=f"{trigger.id}:{message.message_id}",
                    trigger_id=trigger.id,
                    trigger_type=trigger.type,
                    occurred_at=message.created_at.astimezone(UTC),
                    payload={
                        "session_id": message.target_session_id,
                        "agent_prompt": {
                            "message_id": message.message_id,
                            "sender_session_id": message.sender_session_id,
                            "target_session_id": message.target_session_id,
                            "prompt": str(prompt_payload.get("prompt") or "").strip(),
                            "metadata": dict(prompt_payload.get("metadata") or {}),
                            "status": message.status,
                            "created_at": message.created_at.astimezone(UTC)
                            .isoformat()
                            .replace("+00:00", "Z"),
                        },
                    },
                )
            )

        return events

    def acknowledge_published_event(self, event: TriggerEvent) -> None:
        trigger = self._trigger()
        if event.trigger_id != trigger.id or event.trigger_type != trigger.type:
            return
        payload = event.payload.get("agent_prompt")
        if not isinstance(payload, dict):
            return
        message_id = str(payload.get("message_id") or "").strip()
        if not message_id:
            return
        self._cursor_store().update(
            trigger.id,
            lambda state: _acknowledge_message_id(state, message_id),
        )

    def _list_current_unread(self):
        prompt_store = AgentPromptStore(
            self.runtime_context.resolved_config.runtime_dir / "agent-prompt-store.json"
        )
        sessions = JsonSessionManifestRepository(
            self.runtime_context.resolved_config.session_store_path
        )
        directory_scope = (
            str(self.runtime_context.metadata.get("directory_path") or "root").strip() or "root"
        )

        unread = []
        for session in sessions.list_sessions():
            if directory_scope != "root" and not (
                session.directory_path == directory_scope
                or session.directory_path.startswith(f"{directory_scope}/")
            ):
                continue
            unread.extend(prompt_store.list_unread_prompts(target_session_id=session.session_id))
        return unread

    def _cursor_store(self) -> TriggerCursorStore:
        return TriggerCursorStore(
            self.runtime_context.resolved_config.runtime_dir / self._CURSOR_FILENAME
        )

    def _trigger(self) -> AgentPromptNewTriggerDefinition:
        if not isinstance(self.trigger_definition, AgentPromptNewTriggerDefinition):
            raise RuntimeError("AgentPromptNewTriggerProvider initialized with wrong definition")
        return self.trigger_definition


def _acknowledge_message_id(state: dict[str, object], message_id: str) -> dict[str, object]:
    state["emitted_unread_message_ids"] = merge_cursor_ids(
        state.get("emitted_unread_message_ids"),
        [message_id],
    )
    return state
