"""Provider triggers."""

from __future__ import annotations

from datetime import (
    UTC,
    datetime,
)
from typing import (
    Any,
)

from evander.events.models import (
    TriggerEvent,
    WorkflowNodeEnvelope,
)
from evander.triggers.delivery import (
    TriggerDeliveryRepository,
    TriggerDeliverySourceItem,
    TriggerDeliveryStore,
    TriggerDeliverySubscription,
)
from evander.triggers.providers.base import (
    TriggerProviderBase,
)

from .models import (
    TeamState,
)
from .provider_events import (
    _materialize,
)
from .provider_schemas import (
    TeamDeliverableCreatedTriggerDefinition,
)
from .provider_values import (
    _optional_text,
    _runtime_dir,
    _team_datetime_utc,
)
from .repository import (
    TeamStateRepository,
)
from .services.domain.task_assessment import (
    TaskAssessmentService,
    task_payload_with_assessment,
)


class TeamDeliverableCreatedTriggerProvider(TriggerProviderBase):
    @classmethod
    def validate_definition(cls, trigger_definition):
        if isinstance(trigger_definition, WorkflowNodeEnvelope):
            return _materialize(TeamDeliverableCreatedTriggerDefinition, trigger_definition)
        return trigger_definition

    def poll(self) -> list[TriggerEvent]:
        trigger = self._trigger()
        now = datetime.now(UTC)
        repo = TeamStateRepository(_runtime_dir(self.runtime_context))
        subscription = TriggerDeliverySubscription(
            trigger_type=trigger.type,
            trigger_id=trigger.id,
            directory_scope=_team_trigger_directory_scope(self.runtime_context),
        )
        delivery_store = self._delivery_store()
        with repo.deliverable_trigger_snapshot(
            team_id=_optional_text(trigger.team_id),
            project_id=_optional_text(trigger.project_id),
            producer_agent_id=_optional_text(trigger.producer_agent_id),
        ) as snapshot:
            source_items = [
                TriggerDeliverySourceItem(deliverable_id, occurred_at)
                for deliverable_id, occurred_at in snapshot.sources
            ]
            delivery_store.initialize_subscription_if_new(subscription, source_items, now=now)
            undelivered = {
                item.source_item_id
                for item in delivery_store.list_undelivered(subscription, source_items)
            }
            if not undelivered:
                return []

            state = snapshot.read_state()
            assessment_service = TaskAssessmentService(state)
            events: list[TriggerEvent] = []
            for deliverable_id, occurred_at in snapshot.sources:
                if deliverable_id not in undelivered:
                    continue
                payload = _team_deliverable_created_payload(
                    state, deliverable_id, assessment_service=assessment_service,
                )
                if payload is None:
                    continue
                events.append(
                    TriggerEvent(
                        event_id=f"{trigger.id}:{deliverable_id}",
                        trigger_id=trigger.id,
                        trigger_type=trigger.type,
                        occurred_at=occurred_at,
                        payload=payload,
                    )
                )
            return events

    def acknowledge_published_event(self, event: TriggerEvent) -> None:
        trigger = self._trigger()
        deliverable_id = _optional_text(event.payload.get("deliverable_id"))
        if deliverable_id is None:
            return
        occurred_at = _team_payload_datetime(event.payload.get("created_at")) or event.occurred_at
        subscription = TriggerDeliverySubscription(
            trigger_type=trigger.type,
            trigger_id=trigger.id,
            directory_scope=_team_trigger_directory_scope(self.runtime_context),
        )
        self._delivery_store().mark_published(
            subscription,
            TriggerDeliverySourceItem(
                source_item_id=deliverable_id,
                source_occurred_at=_team_datetime_utc(occurred_at),
            ),
            event_id=event.event_id,
        )

    def _trigger(self) -> TeamDeliverableCreatedTriggerDefinition:
        if not isinstance(self.trigger_definition, TeamDeliverableCreatedTriggerDefinition):
            raise RuntimeError(
                "TeamDeliverableCreatedTriggerProvider initialized with wrong definition"
            )
        return self.trigger_definition

    def _delivery_store(self) -> TriggerDeliveryRepository:
        return TriggerDeliveryStore(_runtime_dir(self.runtime_context) / "trigger-deliveries.json")


def _team_trigger_directory_scope(context: Any) -> str:
    if context is None:
        return "root"
    directory_path = str(getattr(context, "metadata", {}).get("directory_path") or "").strip()
    return directory_path or "root"


def _team_deliverable_created_payload(
    state: TeamState,
    deliverable_id: str,
    *,
    assessment_service: TaskAssessmentService | None = None,
) -> dict[str, Any] | None:
    deliverable = state.deliverables.get(deliverable_id)
    if deliverable is None:
        return None
    task = state.tasks.get(deliverable.task_id)
    project = state.projects.get(task.project_id) if task is not None else None
    team = state.teams.get(project.team_id) if project is not None else None
    deliverable_payload = deliverable.model_dump(mode="json")
    task_payload = (
        task_payload_with_assessment(state, task, assessment_service=assessment_service)
        if task is not None else None
    )
    project_payload = project.model_dump(mode="json") if project is not None else None
    team_payload = team.model_dump(mode="json") if team is not None else None
    return {
        "deliverable_id": deliverable.id,
        "team_id": project.team_id if project is not None else "",
        "project_id": task.project_id if task is not None else "",
        "task_id": deliverable.task_id,
        "producer_agent_id": deliverable.producer_agent_id,
        "state": deliverable.state.value,
        "created_at": _team_datetime_utc(deliverable.created_at).isoformat(),
        "artifact_refs": list(deliverable.artifact_refs),
        "review_request_ids": list(deliverable.review_request_ids),
        "deliverable": deliverable_payload,
        "task": task_payload,
        "project": project_payload,
        "team": team_payload,
        "git_refs": deliverable_payload.get("git_refs"),
        "verification": deliverable_payload.get("verification"),
    }


def _team_payload_datetime(value: object) -> datetime | None:
    text = _optional_text(value)
    if text is None:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return _team_datetime_utc(parsed)
