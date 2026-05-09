from __future__ import annotations

from agent_sandbox.effects.providers.base import EffectProviderBase


class EventEmitEffectProvider(EffectProviderBase):
    """Registry placeholder; effect:event:emit is executed by the core orchestrator."""

    def execute(self, *, resolved_config=None):
        raise RuntimeError("effect:event:emit must be executed by EventEmitEffectExecutor")
