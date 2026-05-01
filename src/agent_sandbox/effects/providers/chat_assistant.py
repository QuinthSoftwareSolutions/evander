from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agent_sandbox.events.provider_models import ChatAssistantEnsureSessionEffectDefinition

from .agent_session import normalize_optional_string
from .base import EffectProviderBase


class ChatAssistantEnsureSessionEffectProvider(EffectProviderBase):
    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        effect = self._effect()
        context = self._context()
        payload = {
            "user_id": effect.user_id,
            "thread_id": effect.thread_id,
            "user_message_id": effect.user_message_id,
            "content": effect.content,
            "backend": effect.backend,
            "assistant_scope": effect.assistant_scope,
        }
        if resolved_config:
            payload.update(dict(resolved_config))

        admin_service = context.admin_service
        if admin_service is None:
            raise RuntimeError("effect:chat-assistant:ensure-session requires admin_service")

        user_id = self._require_string(payload.get("user_id"), field_name="user_id")
        thread_id = self._require_string(payload.get("thread_id"), field_name="thread_id")
        user_message_id = self._require_string(
            payload.get("user_message_id"), field_name="user_message_id"
        )
        content = self._require_string(payload.get("content"), field_name="content")
        settings = admin_service.read_assistant_settings()
        if not bool(settings.get("enabled")):
            raise RuntimeError("Assistant is disabled by admin settings.")

        backend = normalize_optional_string(payload.get("backend")) or str(
            getattr(admin_service.config, "execution_backend", "libvirt_vm")
        )
        scope = normalize_optional_string(payload.get("assistant_scope")) or "admin_chat"
        assistant_session = admin_service.vm_agent_admin.resolve_chat_assistant_session(
            user_id=user_id,
            backend=backend,
            assistant_scope=scope,
            settings=settings,
            thread_id=thread_id,
            user_message_id=user_message_id,
        )

        model = normalize_optional_string(settings.get("default_model"))
        prompt = _compose_assistant_prompt(
            content=content,
            user_id=user_id,
            thread_id=thread_id,
            user_message_id=user_message_id,
            custom_system_instructions=normalize_optional_string(
                settings.get("custom_system_instructions")
            ),
        )
        return {
            "success": True,
            "session_id": assistant_session["session_id"],
            "vm_name": assistant_session.get("vm_name") or assistant_session["session_id"],
            "agent_thread_id": assistant_session["agent_thread_id"],
            "thread_id": thread_id,
            "user_id": user_id,
            "user_message_id": user_message_id,
            "prompt": prompt,
            "model": model or "",
            "execution_backend": assistant_session.get("execution_backend") or backend,
            "capability_profile": assistant_session.get("capability_profile") or "",
        }

    def _effect(self) -> ChatAssistantEnsureSessionEffectDefinition:
        if not isinstance(self.effect_definition, ChatAssistantEnsureSessionEffectDefinition):
            raise RuntimeError(
                "ChatAssistantEnsureSessionEffectProvider initialized with a non-chat-assistant effect definition"
            )
        return self.effect_definition

    def _context(self):
        if self.runtime_context is None:
            raise RuntimeError("ChatAssistantEnsureSessionEffectProvider has no runtime context")
        return self.runtime_context

    @staticmethod
    def _require_string(value: Any, *, field_name: str) -> str:
        text = normalize_optional_string(value)
        if not text:
            raise ValueError(f"{field_name} is required")
        return text

    @classmethod
    def node_type(cls) -> str:
        return "effect:chat-assistant:ensure-session"


def _compose_assistant_prompt(
    *,
    content: str,
    user_id: str,
    thread_id: str,
    user_message_id: str,
    custom_system_instructions: str | None,
) -> str:
    instructions = [
        "You are the admin web assistant for the Agent Sandbox control plane.",
        "Use the assistant_* MCP tools for control-plane reads and permitted operations.",
        "When you are ready to answer, call assistant_post_chat_reply with the provided thread_id and user_message_id. Do not treat logs or stdout as the chat reply transport.",
    ]
    if custom_system_instructions:
        instructions.append(custom_system_instructions)
    instructions.append(
        f"Chat context: user_id={user_id!r}, thread_id={thread_id!r}, user_message_id={user_message_id!r}."
    )
    instructions.append("User message:")
    instructions.append(content)
    return "\n\n".join(instructions)
