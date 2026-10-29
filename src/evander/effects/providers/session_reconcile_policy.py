from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .base import EffectProviderBase, scoped_effect_directory_path


class SessionReconcilePolicyEffectProvider(EffectProviderBase):
    """Apply workflow-owned policy fields without recreating the session."""

    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = {
            key: getattr(self.effect_definition, key)
            for key in ("session_id", "directory_path", "policy")
            if hasattr(self.effect_definition, key)
        }
        if resolved_config is not None:
            payload.update(resolved_config)
        session_id = payload.get("session_id")
        if not isinstance(session_id, str) or not session_id.strip():
            raise ValueError("session_id must resolve to a non-empty string")
        policy = payload.get("policy")
        if not isinstance(policy, Mapping):
            raise ValueError("policy must resolve to a JSON object")
        context = self.runtime_context
        if context is None:
            raise RuntimeError("SessionReconcilePolicyEffectProvider has no runtime context")
        directory_path = scoped_effect_directory_path(context, payload.get("directory_path"))
        update = getattr(context.session_policy_reconciler, "reconcile_session_policy", None)
        if not callable(update):
            raise RuntimeError("Session policy reconciler is required")
        return dict(update(session_id.strip(), policy, directory_path=directory_path))
