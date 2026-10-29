from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from evander.cron import latest_cron_time_on_or_before, validate_cron_expression
from evander.events.models import (
    TriggerDefinitionDiscriminated,
    TriggerEvent,
    WorkflowNodeEnvelope,
)
from evander.events.provider_models import CronTriggerDefinition
from evander.triggers.providers._cursor_state import (
    TriggerCursorStore,
    parse_cursor_timestamp_text as _parse_dt,
    latest_cursor_timestamp_text,
)
from evander.triggers.providers.base import TriggerProviderBase
from evander.triggers.providers.cron_schema import CronTriggerConfigSchema

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
        pending_scheduled_for = _parse_dt(state.get("pending_scheduled_for"))
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

        if pending_scheduled_for is not None:
            latest_fire_at = pending_scheduled_for
        else:
            base = last_fired_at or (now - timedelta(minutes=1))
            latest_fire_at = latest_cron_time_on_or_before(
                expression,
                now,
                timezone_name=trigger.timezone,
            )
            if latest_fire_at is None or latest_fire_at <= base:
                return []
            cursor_store.update(
                trigger.id,
                lambda state: _mark_pending_scheduled_for(
                    state,
                    scheduled_for=latest_fire_at,
                ),
            )

        if pending_scheduled_for is not None and last_fired_at is not None:
            if pending_scheduled_for <= last_fired_at:
                return []

        if latest_fire_at is None:
            return []

        event = TriggerEvent(
            event_id=f"{trigger.id}:{latest_fire_at.isoformat()}",
            trigger_id=trigger.id,
            trigger_type=trigger.type,
            occurred_at=latest_fire_at,
            payload={
                "scheduled_for": latest_fire_at.isoformat().replace("+00:00", "Z"),
                "scheduled_for_local": latest_fire_at.astimezone(
                    ZoneInfo(trigger.timezone)
                ).isoformat(),
                "timezone": trigger.timezone,
            },
        )
        return [event]

    def acknowledge_published_event(self, event: TriggerEvent) -> None:
        trigger = self._trigger()
        if event.trigger_id != trigger.id or event.trigger_type != trigger.type:
            return
        scheduled_for = _parse_dt(event.payload.get("scheduled_for")) or event.occurred_at
        if scheduled_for is None:
            return
        self._cursor_store().update(
            trigger.id,
            lambda state: _acknowledge_scheduled_for(state, scheduled_for),
        )

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


def _mark_pending_scheduled_for(
    state: dict[str, object],
    *,
    scheduled_for: datetime,
) -> dict[str, object]:
    current = _parse_dt(state.get("pending_scheduled_for"))
    if current is not None and current >= scheduled_for:
        return state
    state["pending_scheduled_for"] = (
        scheduled_for.astimezone(UTC).isoformat().replace("+00:00", "Z")
    )
    return state


def _acknowledge_scheduled_for(
    state: dict[str, object],
    scheduled_for: datetime,
) -> dict[str, object]:
    pending = _parse_dt(state.get("pending_scheduled_for"))
    if pending is not None and pending != scheduled_for.astimezone(UTC):
        return state
    state["last_fired_at"] = latest_cursor_timestamp_text(state.get("last_fired_at"), scheduled_for)
    if pending is not None:
        state.pop("pending_scheduled_for", None)
    return state
