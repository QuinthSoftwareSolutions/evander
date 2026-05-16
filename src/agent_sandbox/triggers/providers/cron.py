from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from agent_sandbox.cron import next_cron_time, validate_cron_expression
from agent_sandbox.events.models import (
    TriggerDefinitionDiscriminated,
    TriggerEvent,
    WorkflowNodeEnvelope,
)
from agent_sandbox.events.provider_models import CronTriggerDefinition
from agent_sandbox.triggers.providers._cursor_state import TriggerCursorStore
from agent_sandbox.triggers.providers.base import TriggerProviderBase
from agent_sandbox.triggers.providers.cron_schema import CronTriggerConfigSchema

logger = logging.getLogger(__name__)


class CronTriggerProvider(TriggerProviderBase):
    @classmethod
    def validate_definition(
        cls, trigger_definition: TriggerDefinitionDiscriminated
    ) -> TriggerDefinitionDiscriminated:
        if isinstance(trigger_definition, WorkflowNodeEnvelope):
            config = CronTriggerConfigSchema.model_validate(trigger_definition.config).model_dump()
            return CronTriggerDefinition.model_validate(
                {
                    "id": trigger_definition.id,
                    "display_name": trigger_definition.display_name,
                    "type": trigger_definition.type,
                    "outputs": trigger_definition.outputs,
                    **config,
                }
            )
        return super().validate_definition(trigger_definition)

    def poll(self) -> list[TriggerEvent]:
        trigger = self._trigger()
        cursor_store = self._cursor_store()
        state = cursor_store.get(trigger.id)

        now = datetime.now(UTC).replace(second=0, microsecond=0)
        last_fired_at = _parse_dt(state.get("last_fired_at"))
        base = last_fired_at or (now - timedelta(minutes=1))
        try:
            expression = validate_cron_expression(trigger.expression)
        except ValueError as exc:
            logger.warning(
                "Skipping invalid cron trigger expression trigger_id=%s expression=%r error=%s",
                trigger.id,
                trigger.expression,
                exc,
            )
            return []

        events: list[TriggerEvent] = []
        next_fire_at = next_cron_time(expression, base)
        iterations = 0
        while next_fire_at is not None and next_fire_at <= now and iterations < 100:
            events.append(
                TriggerEvent(
                    event_id=f"{trigger.id}:{next_fire_at.isoformat()}",
                    trigger_id=trigger.id,
                    trigger_type=trigger.type,
                    occurred_at=next_fire_at,
                    payload={"scheduled_for": next_fire_at.isoformat().replace("+00:00", "Z")},
                )
            )
            base = next_fire_at
            next_fire_at = next_cron_time(expression, base)
            iterations += 1

        if events:
            cursor_store.put(
                trigger.id,
                {"last_fired_at": events[-1].occurred_at.isoformat().replace("+00:00", "Z")},
            )
        return events

    def _trigger(self) -> CronTriggerDefinition:
        if not isinstance(self.trigger_definition, CronTriggerDefinition):
            raise RuntimeError("CronTriggerProvider initialized with a non-cron trigger definition")
        return self.trigger_definition

    def _cursor_store(self) -> TriggerCursorStore:
        if self.runtime_context is None:
            raise RuntimeError("CronTriggerProvider has no runtime context")
        return TriggerCursorStore(
            self.runtime_context.resolved_config.runtime_dir / "trigger-cursors-cron.json"
        )


def _parse_dt(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)
