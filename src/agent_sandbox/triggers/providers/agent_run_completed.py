from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Mapping

from agent_sandbox.events.models import TriggerEvent
from agent_sandbox.models import VmSummary
from agent_sandbox.triggers.providers._cursor_state import TriggerCursorStore
from agent_sandbox.triggers.providers.base import TriggerProviderBase


class AgentRunCompletedTriggerProvider(TriggerProviderBase):
    """Emit an event when a host-observable agent run settles back to idle."""

    _CURSOR_FILENAME = "trigger-cursors-agent-run-completed.json"

    def poll(self) -> list[TriggerEvent]:
        trigger = self._trigger()
        cursor_store = self._cursor_store()
        state = cursor_store.get(trigger.id)
        seen_by_vm = state.get("seen_by_vm", {})
        if not isinstance(seen_by_vm, dict):
            seen_by_vm = {}

        vm_user = self._scope_vm_user(trigger)
        directory_path = self._scope_directory_path(trigger)
        target_vm_name = self._scope_vm_name(trigger)
        target_session_id = self._scope_session_id(trigger)

        list_vms = self._list_vms()
        summaries = list_vms(
            include_inactive=True,
            omit_state=False,
            vm_user=vm_user,
            directory_path=directory_path,
            direct_only=False,
        )

        normalized_summaries = [
            summary
            for summary in (self._normalize_summary(raw) for raw in summaries)
            if summary is not None
            and self._summary_matches_target(
                summary, target_vm_name=target_vm_name, target_session_id=target_session_id
            )
        ]
        normalized_summaries.sort(
            key=lambda summary: self._normalize_optional_string(summary.vm_name) or ""
        )

        events: list[TriggerEvent] = []
        updated_seen_by_vm: dict[str, dict[str, Any]] = {
            str(vm_name): dict(previous)
            for vm_name, previous in seen_by_vm.items()
            if str(vm_name).strip() and isinstance(previous, dict)
        }

        for summary in normalized_summaries:
            vm_name = self._normalize_optional_string(summary.vm_name)
            if not vm_name:
                continue

            current_state = self._normalize_optional_string(summary.agent_state) or "unknown"
            current_updated_at = self._normalize_dt(summary.last_state_update_at)
            previous = seen_by_vm.get(vm_name, {})
            previous_state = (
                self._normalize_optional_string(previous.get("last_agent_state")) or "unknown"
            )
            previous_updated_at = self._normalize_dt(previous.get("last_state_update_at"))
            previous_emitted_at = self._normalize_dt(previous.get("last_emitted_state_update_at"))
            current_dispatch_id = self._normalize_optional_string(summary.dispatch_id)
            previous_dispatch_id = self._normalize_optional_string(previous.get("last_dispatch_id"))
            previous_emitted_dispatch_id = self._normalize_optional_string(
                previous.get("last_emitted_dispatch_id")
            )

            state_transition_completed = (
                previous_state == "running"
                and (previous_updated_at is None or current_updated_at > previous_updated_at)
            )
            idle_dispatch_completed = (
                previous_state == "idle"
                and current_dispatch_id is not None
                and previous_dispatch_id is not None
                and current_dispatch_id != previous_dispatch_id
                and (previous_updated_at is None or current_updated_at >= previous_updated_at)
            )
            should_emit = (
                current_state == "idle"
                and current_updated_at is not None
                and (state_transition_completed or idle_dispatch_completed)
                and not self._already_emitted_completion(
                    current_updated_at=current_updated_at,
                    current_dispatch_id=current_dispatch_id,
                    previous_emitted_at=previous_emitted_at,
                    previous_emitted_dispatch_id=previous_emitted_dispatch_id,
                )
            )
            if should_emit:
                events.append(
                    self._build_event(
                        trigger_id=trigger.id,
                        trigger_type=trigger.type,
                        summary=summary,
                        previous_state=previous_state,
                        current_state=current_state,
                        updated_at=current_updated_at,
                        vm_user=vm_user,
                        directory_path=directory_path,
                        target_vm_name=target_vm_name,
                        target_session_id=target_session_id,
                    )
                )
                continue

            seen_entry: dict[str, Any] = {
                "last_agent_state": current_state,
                "last_state_update_at": self._format_dt(current_updated_at),
            }
            stored_dispatch_id = current_dispatch_id or previous_dispatch_id
            if stored_dispatch_id is not None:
                seen_entry["last_dispatch_id"] = stored_dispatch_id
            if previous_emitted_at is not None:
                seen_entry["last_emitted_state_update_at"] = self._format_dt(previous_emitted_at)
            if previous_emitted_dispatch_id is not None:
                seen_entry["last_emitted_dispatch_id"] = previous_emitted_dispatch_id
            updated_seen_by_vm[vm_name] = seen_entry

        if updated_seen_by_vm != seen_by_vm:
            cursor_store.put(
                trigger.id,
                {
                    "seen_by_vm": updated_seen_by_vm,
                    "directory_path": directory_path,
                    "vm_name": target_vm_name,
                    "session_id": target_session_id,
                    "vm_user": vm_user,
                },
            )

        return events

    def acknowledge_published_event(self, event: TriggerEvent) -> None:
        trigger = self._trigger()
        if event.trigger_id != trigger.id or event.trigger_type != trigger.type:
            return
        payload = event.payload
        vm_name = self._normalize_optional_string(payload.get("vm_name"))
        current_state = self._normalize_optional_string(payload.get("agent_state")) or "unknown"
        current_updated_at = self._normalize_dt(payload.get("last_state_update_at"))
        current_dispatch_id = self._normalize_optional_string(payload.get("dispatch_id"))
        if not vm_name or current_updated_at is None:
            return
        scope = payload.get("scope")
        self._cursor_store().update(
            trigger.id,
            lambda state: _acknowledge_vm(
                state,
                vm_name=vm_name,
                current_state=current_state,
                current_updated_at=current_updated_at,
                current_dispatch_id=current_dispatch_id,
                scope=scope if isinstance(scope, dict) else None,
            ),
        )

    def _trigger(self) -> Any:
        if self.trigger_definition is None:
            raise RuntimeError("AgentRunCompletedTriggerProvider has no trigger definition")
        trigger_type = self._normalize_optional_string(
            getattr(self.trigger_definition, "type", None)
        )
        if trigger_type and trigger_type != "trigger:agent:run:completed":
            raise RuntimeError(
                f"AgentRunCompletedTriggerProvider initialized with wrong definition: {trigger_type!r}"
            )
        return self.trigger_definition

    def _context(self):
        if self.runtime_context is None:
            raise RuntimeError("AgentRunCompletedTriggerProvider has no runtime context")
        return self.runtime_context

    def _runtime_dir(self) -> Path:
        return self._context().resolved_config.runtime_dir

    def _cursor_store(self) -> TriggerCursorStore:
        return TriggerCursorStore(self._runtime_dir() / self._CURSOR_FILENAME)

    def _runtime_metadata(self) -> Mapping[str, Any]:
        return self._context().metadata

    def _list_vms(self) -> Callable[..., list[Any]]:
        context = self._context()
        admin_service = getattr(context, "admin_service", None)
        if admin_service is not None:
            list_vms = getattr(admin_service, "list_vms", None)
            if callable(list_vms):
                return list_vms

        metadata = self._runtime_metadata()
        admin_service = metadata.get("admin_service")
        if admin_service is not None:
            list_vms = getattr(admin_service, "list_vms", None)
            if callable(list_vms):
                return list_vms

        list_vms = metadata.get("list_vms")
        if callable(list_vms):
            return list_vms

        raise RuntimeError(
            "AgentRunCompletedTriggerProvider requires an admin_service with list_vms() in runtime context or metadata"
        )

    def _scope_directory_path(self, trigger: Any) -> str | None:
        explicit = self._normalize_optional_string(getattr(trigger, "directory_path", None))
        if explicit and explicit != "root":
            return explicit

        metadata_directory = self._normalize_optional_string(
            self._runtime_metadata().get("directory_path")
        )
        if metadata_directory and metadata_directory != "root":
            return metadata_directory

        return None

    def _scope_vm_name(self, trigger: Any) -> str | None:
        explicit = self._normalize_optional_string(getattr(trigger, "vm_name", None))
        if explicit:
            return explicit
        return self._normalize_optional_string(self._runtime_metadata().get("vm_name"))

    def _scope_session_id(self, trigger: Any) -> str | None:
        explicit = self._normalize_optional_string(getattr(trigger, "session_id", None))
        if explicit:
            return explicit
        return self._normalize_optional_string(self._runtime_metadata().get("session_id"))

    def _scope_vm_user(self, trigger: Any) -> str:
        explicit = self._normalize_optional_string(getattr(trigger, "vm_user", None))
        if explicit:
            return explicit
        metadata_vm_user = self._normalize_optional_string(self._runtime_metadata().get("vm_user"))
        return metadata_vm_user or "agent"

    def _summary_matches_target(
        self,
        summary: VmSummary,
        *,
        target_vm_name: str | None,
        target_session_id: str | None,
    ) -> bool:
        vm_name = self._normalize_optional_string(summary.vm_name)
        if target_vm_name and vm_name != target_vm_name:
            return False

        if target_session_id:
            session_ids = {
                self._normalize_optional_string(value)
                for value in summary.session_ids
                if self._normalize_optional_string(value)
            }
            if target_session_id not in session_ids:
                return False

        return True

    @staticmethod
    def _normalize_summary(value: Any) -> VmSummary | None:
        if isinstance(value, VmSummary):
            return value
        if not isinstance(value, dict):
            return None
        try:
            return VmSummary.model_validate(value)
        except Exception:
            return None

    def _build_event(
        self,
        *,
        trigger_id: str,
        trigger_type: str,
        summary: VmSummary,
        previous_state: str,
        current_state: str,
        updated_at: datetime,
        vm_user: str,
        directory_path: str | None,
        target_vm_name: str | None,
        target_session_id: str | None,
    ) -> TriggerEvent:
        vm_name = self._normalize_optional_string(summary.vm_name) or "unknown-vm"
        dispatch_id = self._normalize_optional_string(summary.dispatch_id)
        completion_id = updated_at.isoformat()
        if dispatch_id is not None:
            completion_id = f"{dispatch_id}:{completion_id}"
        event_id = f"{trigger_id}:{vm_name}:{completion_id}"
        payload: dict[str, Any] = {
            "vm": summary.model_dump(mode="json"),
            "vm_name": vm_name,
            "provider": summary.provider,
            "session_ids": list(summary.session_ids),
            "labels": dict(summary.labels),
            "directory_paths": list(summary.directory_paths),
            "agent_repo_names": list(summary.agent_repo_names),
            "power_state": summary.power_state,
            "libvirt_state": summary.libvirt_state,
            "provider_state": summary.provider_state,
            "agent_state": current_state,
            "previous_agent_state": previous_state,
            "transition": {"from": previous_state, "to": current_state},
            "last_state_update_at": updated_at.isoformat().replace("+00:00", "Z"),
            "scope": {
                "directory_path": directory_path,
                "vm_name": target_vm_name,
                "session_id": target_session_id,
                "vm_user": vm_user,
            },
        }
        if summary.ip_address is not None:
            payload["ip_address"] = summary.ip_address
        if dispatch_id is not None:
            payload["dispatch_id"] = dispatch_id
        return TriggerEvent(
            event_id=event_id,
            trigger_id=trigger_id,
            trigger_type=trigger_type,
            occurred_at=updated_at.astimezone(UTC),
            payload=payload,
        )

    @staticmethod
    def _normalize_optional_string(value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @staticmethod
    def _normalize_dt(value: Any) -> datetime | None:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value.astimezone(UTC)
        text = str(value).strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)

    @staticmethod
    def _format_dt(value: datetime | None) -> str | None:
        if value is None:
            return None
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")

    @staticmethod
    def _already_emitted_completion(
        *,
        current_updated_at: datetime,
        current_dispatch_id: str | None,
        previous_emitted_at: datetime | None,
        previous_emitted_dispatch_id: str | None,
    ) -> bool:
        if previous_emitted_at is None:
            return False
        if current_dispatch_id is not None:
            return (
                previous_emitted_dispatch_id == current_dispatch_id
                and current_updated_at <= previous_emitted_at
            )
        return current_updated_at <= previous_emitted_at


def _acknowledge_vm(
    state: dict[str, Any],
    *,
    vm_name: str,
    current_state: str,
    current_updated_at: datetime,
    current_dispatch_id: str | None,
    scope: dict[str, Any] | None,
) -> dict[str, Any]:
    seen_by_vm = state.get("seen_by_vm", {})
    if isinstance(seen_by_vm, dict):
        updated_seen_by_vm = {
            str(existing_vm_name): dict(existing_state)
            for existing_vm_name, existing_state in seen_by_vm.items()
            if str(existing_vm_name).strip() and isinstance(existing_state, dict)
        }
    else:
        updated_seen_by_vm = {}

    seen_entry: dict[str, Any] = {
        "last_agent_state": current_state,
        "last_state_update_at": current_updated_at.astimezone(UTC)
        .isoformat()
        .replace("+00:00", "Z"),
        "last_emitted_state_update_at": current_updated_at.astimezone(UTC)
        .isoformat()
        .replace("+00:00", "Z"),
    }
    if current_dispatch_id is not None:
        seen_entry["last_dispatch_id"] = current_dispatch_id
        seen_entry["last_emitted_dispatch_id"] = current_dispatch_id
    updated_seen_by_vm[vm_name] = seen_entry
    state["seen_by_vm"] = updated_seen_by_vm

    if scope is not None:
        for key in ("directory_path", "vm_name", "session_id", "vm_user"):
            value = scope.get(key)
            if value is not None:
                state[key] = value
    return state
