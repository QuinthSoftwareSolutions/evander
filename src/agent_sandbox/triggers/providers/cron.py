from __future__ import annotations

from datetime import UTC, datetime, timedelta

from agent_sandbox.events.models import (
    TriggerDefinitionDiscriminated,
    TriggerEvent,
    WorkflowNodeEnvelope,
)
from agent_sandbox.events.provider_models import CronTriggerDefinition
from agent_sandbox.triggers.providers._cursor_state import TriggerCursorStore
from agent_sandbox.triggers.providers.base import TriggerProviderBase
from agent_sandbox.triggers.providers.cron_schema import CronTriggerConfigSchema


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

        events: list[TriggerEvent] = []
        next_fire_at = _next_cron_time(trigger.expression, base)
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
            next_fire_at = _next_cron_time(trigger.expression, base)
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


def _next_cron_time(expression: str, base: datetime) -> datetime | None:
    fields = expression.split()
    if len(fields) != 5:
        return None
    minute_values = _parse_cron_field(fields[0], 0, 59)
    hour_values = _parse_cron_field(fields[1], 0, 23)
    day_values = _parse_cron_field(fields[2], 1, 31)
    month_values = _parse_cron_field(fields[3], 1, 12)
    weekday_values = _parse_cron_field(fields[4], 0, 6)
    if any(
        value is None
        for value in (minute_values, hour_values, day_values, month_values, weekday_values)
    ):
        return None

    assert minute_values is not None
    assert hour_values is not None
    assert day_values is not None
    assert month_values is not None
    assert weekday_values is not None

    candidate = base.astimezone(UTC).replace(second=0, microsecond=0) + timedelta(minutes=1)
    deadline = candidate + timedelta(days=366)
    while candidate <= deadline:
        cron_weekday = (candidate.weekday() + 1) % 7
        if (
            candidate.minute in minute_values
            and candidate.hour in hour_values
            and candidate.day in day_values
            and candidate.month in month_values
            and cron_weekday in weekday_values
        ):
            return candidate
        candidate += timedelta(minutes=1)
    return None


def _parse_cron_field(token: str, lower: int, upper: int) -> set[int] | None:
    normalized = token.strip()
    if not normalized:
        return None
    if normalized == "*":
        return set(range(lower, upper + 1))

    values: set[int] = set()
    for chunk in normalized.split(","):
        part = chunk.strip()
        if not part:
            return None
        if part.startswith("*/"):
            try:
                step = int(part[2:])
            except ValueError:
                return None
            if step <= 0:
                return None
            values.update(range(lower, upper + 1, step))
            continue
        try:
            value = int(part)
        except ValueError:
            return None
        if value < lower or value > upper:
            return None
        values.add(value)
    return values
