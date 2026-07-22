from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from evander.events.provider_models import (
    AgentGrantRepoAccessEffectDefinition,
    AgentRevokeRepoAccessEffectDefinition,
)
from evander.managed_git_grants import normalize_git_access_mode

from .agent_session import normalize_optional_string, required_string
from .base import EffectProviderBase


def _target(payload: Mapping[str, Any], *, effect_type: str) -> str:
    for key in ("target", "session_id", "vm_name"):
        value = normalize_optional_string(payload.get(key))
        if value:
            return value
    raise ValueError(f"{effect_type} requires target, session_id, or vm_name")


class AgentGrantRepoAccessEffectProvider(EffectProviderBase):
    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        effect = self.effect_definition
        if not isinstance(effect, AgentGrantRepoAccessEffectDefinition):
            raise RuntimeError("invalid grant-repo-access effect definition")
        payload = effect.model_dump(mode="python")
        if resolved_config:
            payload.update(dict(resolved_config))
        context = self.runtime_context
        if context is None:
            raise RuntimeError("grant-repo-access provider has no runtime context")
        grant = context.admin_service.grant_managed_git_access(
            _target(payload, effect_type=effect.type),
            required_string(
                payload.get("managed_repo_id"),
                "effect:agent:grant-repo-access requires managed_repo_id",
            ),
            access_mode=normalize_git_access_mode(
                payload.get("git_access_mode"), default="read-only"
            ),
            lifetime="session",
            vm_user=normalize_optional_string(payload.get("vm_user")) or "agent",
        )
        return {"success": True, "grant": grant, **grant}


class AgentRevokeRepoAccessEffectProvider(EffectProviderBase):
    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        effect = self.effect_definition
        if not isinstance(effect, AgentRevokeRepoAccessEffectDefinition):
            raise RuntimeError("invalid revoke-repo-access effect definition")
        payload = effect.model_dump(mode="python")
        if resolved_config:
            payload.update(dict(resolved_config))
        context = self.runtime_context
        if context is None:
            raise RuntimeError("revoke-repo-access provider has no runtime context")
        result = context.admin_service.revoke_managed_git_access(
            _target(payload, effect_type=effect.type),
            required_string(
                payload.get("managed_repo_id"),
                "effect:agent:revoke-repo-access requires managed_repo_id",
            ),
        )
        return {"success": True, **result}
