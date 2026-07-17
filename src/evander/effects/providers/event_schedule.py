from __future__ import annotations

from evander.effects.providers.base import EffectProviderBase


class EventScheduleEffectProvider(EffectProviderBase):
    """Registry placeholder; effect:event:schedule is executed by the core orchestrator."""

    def execute(self, *, resolved_config=None):
        raise RuntimeError("effect:event:schedule must be executed by EventScheduleEffectExecutor")
