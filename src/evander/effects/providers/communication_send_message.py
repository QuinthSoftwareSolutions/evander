from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from evander.messaging.notifications import CommunicationMessageSender

from .base import EffectProviderBase


class CommunicationSendMessageEffectProvider(EffectProviderBase):
    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = self._effect_payload()
        if resolved_config:
            payload.update(dict(resolved_config))

        communication_target_id = self._require_string(
            payload.get("communication_target_id"),
            field_name="communication_target_id",
        ).lower()
        message = self._require_message(payload.get("message"))
        thread_id = self._normalize_optional_string(payload.get("thread_id"))
        subject = self._normalize_optional_string(payload.get("subject"))
        sender_session_id = self._normalize_optional_string(payload.get("sender_session_id"))

        context = self._context()
        sender = CommunicationMessageSender(
            project_dir=self._project_dir(context),
            runtime=self._runtime(context),
            directory_path=str(context.metadata.get("directory_path") or "root"),
        )
        return sender.send_message(
            communication_target_id=communication_target_id,
            message=message,
            thread_id=thread_id,
            subject=subject,
            sender_session_id=sender_session_id or f"workflow:{self._effect_id()}",
        )

    def _effect_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        effect = self.effect_definition
        for field_name in (
            "communication_target_id",
            "message",
            "thread_id",
            "subject",
            "sender_session_id",
        ):
            if effect is not None and hasattr(effect, field_name):
                payload[field_name] = getattr(effect, field_name)
        return payload

    def _context(self):
        if self.runtime_context is None:
            raise RuntimeError("CommunicationSendMessageEffectProvider has no runtime context")
        return self.runtime_context

    @staticmethod
    def _project_dir(context: Any) -> Path:
        admin_service = getattr(context, "admin_service", None)
        project_dir = getattr(admin_service, "project_dir", None)
        if project_dir is None:
            project_dir = context.metadata.get("project_dir")
        if project_dir is None:
            raise RuntimeError("effect:communication:send-message requires a project_dir")
        return Path(project_dir)

    @staticmethod
    def _runtime(context: Any) -> Any:
        admin_service = getattr(context, "admin_service", None)
        runtime = getattr(admin_service, "runtime", None)
        if runtime is None:
            runtime = context.metadata.get("runtime")
        if runtime is None:
            raise RuntimeError("effect:communication:send-message requires runtime services")
        return runtime

    def _effect_id(self) -> str:
        effect = self.effect_definition
        return str(getattr(effect, "id", "communication-send-message") or "communication")

    @staticmethod
    def _require_string(value: Any, *, field_name: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError(f"effect:communication:send-message requires {field_name}")
        return text

    @staticmethod
    def _require_message(value: Any) -> str:
        if value is None:
            raise ValueError("effect:communication:send-message requires message")
        text = str(value)
        if not text.strip():
            raise ValueError("effect:communication:send-message requires message")
        return text

    @staticmethod
    def _normalize_optional_string(value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None
