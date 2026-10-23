from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from evander.admin.services.session_helpers import resolve_session_target
from evander.reasoning_effort import normalize_reasoning_effort

from .base import EffectProviderBase, scoped_effect_directory_path


class SessionSetReasoningEffortEffectProvider(EffectProviderBase):
    """Persist a native session default without changing an in-flight turn."""

    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = {
            key: getattr(self.effect_definition, key)
            for key in ("target", "session_id", "vm_name", "reasoning_effort")
            if hasattr(self.effect_definition, key)
        }
        if resolved_config is not None:
            payload.update(resolved_config)
        selectors = [
            value
            for key in ("target", "session_id", "vm_name")
            if (value := payload.get(key)) is not None
        ]
        if len(selectors) != 1 or not isinstance(selectors[0], str) or not selectors[0].strip():
            raise ValueError("Provide exactly one non-empty target, session_id, or vm_name")
        if "reasoning_effort" not in payload:
            raise ValueError("reasoning_effort is required; use null to clear the session default")
        effort = normalize_reasoning_effort(payload["reasoning_effort"])
        context = self.runtime_context
        if context is None:
            raise RuntimeError("SessionSetReasoningEffortEffectProvider has no runtime context")
        lookup = getattr(context.session_lookup, "read_all_sessions", None)
        update = getattr(
            context.session_model_settings_updater, "update_session_model_settings", None
        )
        if not callable(lookup) or not callable(update):
            raise RuntimeError("Session lookup and model settings updater are required")
        session_id, session = resolve_session_target(selectors[0], dict(lookup()))
        scoped_effect_directory_path(context, session.directory_path)
        result = update(session_id, {"reasoning_effort": effort})
        output = dict(result) if isinstance(result, Mapping) else result.model_dump(mode="json")
        if output.get("success") is True:
            output.update(reasoning_effort=effort, directory_path=session.directory_path)
        return output
