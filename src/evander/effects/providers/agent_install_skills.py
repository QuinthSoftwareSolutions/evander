from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .base import EffectProviderBase
from .agent_session import selected_execution_backend_for_target


class AgentInstallSkillsEffectProvider(EffectProviderBase):
    """Use the supported additive install without rebuilding a session's grants."""

    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = self.effect_definition.model_dump()
        payload.update(resolved_config or {})
        target = str(payload["target"] or "").strip()
        names = payload["codex_skills"]
        if (
            not target
            or not isinstance(names, list)
            or not names
            or any(not isinstance(name, str) or not name.strip() for name in names)
        ):
            raise ValueError("target and a nonempty list of codex_skills are required")
        context = self.runtime_context
        backend, _ = selected_execution_backend_for_target(
            context, {"target": target}, directory_path=None
        )
        result = context.admin_service.vm_agent_admin.install_admin_agent_skills(
            agent_id=target,
            backend=backend,
            vm_user=str(payload.get("vm_user") or "agent"),
            skill_names=names,
        )
        if not result.get("success"):
            raise RuntimeError(
                "Could not install the requested managed skills in the agent session"
            )
        return {"success": True, "session_id": target, "codex_skills": names}
