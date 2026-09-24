"""Provider reviews."""

from __future__ import annotations

from collections.abc import (
    Mapping,
)
from typing import (
    Any,
)

from evander.effects.providers.base import (
    EffectProviderBase,
)
from evander.events.models import (
    WorkflowNodeEnvelope,
)

from .models import (
    AgentStatus,
    TeamState,
)
from .outbox_publication import (
    publish_team_wake_events_with_outbox_recovery,
)
from .provider_events import (
    _materialize,
    _publish_team_events,
)
from .provider_results import (
    _review_cohort_effect_error_result,
    _review_cohort_effect_success_result,
    _review_request_effect_error_result,
    _review_request_effect_success_result,
)
from .provider_schemas import (
    TeamCreateOperatorDirectiveEffectDefinition,
    TeamCreateReviewCohortEffectDefinition,
    TeamCreateReviewRequestEffectDefinition,
)
from .provider_values import (
    _definition_payload,
    _list_of_mappings,
    _optional_bool,
    _optional_int,
    _optional_text,
    _required_text,
    _runtime_dir,
)
from .repository import (
    TeamStateConflictError,
    TeamStateRepository,
)
from .services.admin_service import (
    TeamAdminService,
)
from .services.domain.project_status import (
    _recalculate_project_status,
)
from .services.domain.review import (
    _apply_review_request,
    create_sealed_review_cohort,
)


class TeamCreateOperatorDirectiveEffectProvider(EffectProviderBase):
    @classmethod
    def validate_definition(cls, effect_definition):
        if isinstance(effect_definition, WorkflowNodeEnvelope):
            return _materialize(TeamCreateOperatorDirectiveEffectDefinition, effect_definition)
        return effect_definition

    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = _definition_payload(
            self.effect_definition,
            "team_id",
            "message",
            "recipient_agent_id",
            "project_id",
            "task_id",
            "title",
            "source",
            "source_message_id",
            "created_by",
            "requesting_session_id",
            "expected_revision",
        )
        if resolved_config:
            payload.update(dict(resolved_config))
        repo = TeamStateRepository(_runtime_dir(self.runtime_context))
        try:
            state, item, wake_result = TeamAdminService(repo).create_operator_directive(
                team_id=_required_text(payload.get("team_id"), "team_id"),
                message=_required_text(payload.get("message"), "message"),
                recipient_agent_id=_optional_text(payload.get("recipient_agent_id")),
                project_id=_optional_text(payload.get("project_id")),
                task_id=_optional_text(payload.get("task_id")),
                title=_optional_text(payload.get("title")),
                source=_optional_text(payload.get("source")),
                source_message_id=_optional_text(payload.get("source_message_id")),
                created_by=_optional_text(payload.get("created_by")),
                requesting_session_id=_optional_text(payload.get("requesting_session_id")),
                expected_revision=_optional_int(payload.get("expected_revision"), default=None),
            )
        except TeamStateConflictError as exc:
            return {
                "success": False,
                "revision": exc.current_revision,
                "inbox_item_id": None,
                "recipient_agent_id": None,
                "team_id": _optional_text(payload.get("team_id")),
                "project_id": _optional_text(payload.get("project_id")),
                "task_id": _optional_text(payload.get("task_id")),
                "error": str(exc),
                "expected_revision": exc.expected_revision,
                "current_revision": exc.current_revision,
            }
        except (TypeError, ValueError) as exc:
            return {
                "success": False,
                "revision": repo.read().revision,
                "inbox_item_id": None,
                "recipient_agent_id": None,
                "team_id": _optional_text(payload.get("team_id")),
                "project_id": _optional_text(payload.get("project_id")),
                "task_id": _optional_text(payload.get("task_id")),
                "error": str(exc),
            }
        wake_delivery = publish_team_wake_events_with_outbox_recovery(
            self.runtime_context,
            repository=repo,
            events=wake_result.get("events"),
            outbox_record_ids=wake_result.get("outbox_record_ids") or [],
            publish_events=lambda events: _publish_team_events(self.runtime_context, events),
        )
        project_id = item.payload.get("project_id")
        return {
            "success": True,
            "revision": state.revision,
            "inbox_item_id": item.id,
            "recipient_agent_id": item.recipient_agent_id,
            "team_id": item.payload.get("team_id"),
            "project_id": project_id if isinstance(project_id, str) else None,
            "task_id": item.task_id,
            "error": None,
            **wake_delivery,
        }


class TeamCreateReviewRequestEffectProvider(EffectProviderBase):
    @classmethod
    def validate_definition(cls, effect_definition):
        if isinstance(effect_definition, WorkflowNodeEnvelope):
            return _materialize(TeamCreateReviewRequestEffectDefinition, effect_definition)
        return effect_definition

    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = _definition_payload(
            self.effect_definition,
            "actor_agent_id",
            "deliverable_id",
            "reviewer_agent_id",
            "review_request_id",
            "review_profile_id",
            "instructions",
            "skip_if_existing",
            "publish_wake_events",
            "expected_revision",
        )
        if resolved_config:
            payload.update(dict(resolved_config))
        repo = TeamStateRepository(_runtime_dir(self.runtime_context))
        current_state: TeamState | None = None
        review_result: dict[str, Any] = {
            "errors": [],
            "created_inbox_item_ids": [],
            "existing_review_request_ids": [],
            "events": [],
        }
        review_request_id: str | None = None
        actor_agent_id = _optional_text(payload.get("actor_agent_id"))
        deliverable_id = _optional_text(payload.get("deliverable_id"))
        reviewer_agent_id = _optional_text(payload.get("reviewer_agent_id"))
        skip_if_existing = True
        publish_wake_events = True
        try:
            current_state = repo.read()
            skip_if_existing = _optional_bool(payload.get("skip_if_existing"), default=True)
            publish_wake_events = _optional_bool(
                payload.get("publish_wake_events"),
                default=True,
            )

            def _mutate(state: TeamState) -> TeamState:
                nonlocal review_request_id
                actor_id = _required_text(payload.get("actor_agent_id"), "actor_agent_id")
                actor = state.agents.get(actor_id)
                if actor is None:
                    raise ValueError(f"actor agent {actor_id} does not exist")
                reviewer_id = _required_text(
                    payload.get("reviewer_agent_id"),
                    "reviewer_agent_id",
                )
                reviewer = state.agents.get(reviewer_id)
                if reviewer is None:
                    raise ValueError(f"reviewer agent {reviewer_id} does not exist")
                if reviewer.status != AgentStatus.ACTIVE:
                    raise ValueError(f"recipient agent {reviewer.id} is {reviewer.status.value}")
                request_payload = {
                    "id": _optional_text(payload.get("review_request_id")),
                    "deliverable_id": _required_text(
                        payload.get("deliverable_id"), "deliverable_id"
                    ),
                    "reviewer_agent_id": reviewer_id,
                    "review_profile_id": _optional_text(payload.get("review_profile_id")),
                    "instructions": _optional_text(payload.get("instructions")),
                }
                created_id = _apply_review_request(
                    state,
                    actor=actor,
                    request=request_payload,
                    result=review_result,
                )
                if created_id is None:
                    raise ValueError(
                        "; ".join(str(error) for error in review_result["errors"])
                        or "review request could not be created"
                    )
                existing_ids = list(review_result.get("existing_review_request_ids") or [])
                if existing_ids and not skip_if_existing:
                    raise ValueError(
                        "active review request already exists for this deliverable and reviewer"
                    )
                review_request_id = created_id
                return state

            state = repo.update(
                _mutate,
                expected_revision=_optional_int(payload.get("expected_revision"), default=None),
            )
        except TeamStateConflictError as exc:
            return _review_request_effect_error_result(
                revision=exc.current_revision,
                review_request_id=review_request_id,
                actor_agent_id=actor_agent_id,
                deliverable_id=deliverable_id,
                reviewer_agent_id=reviewer_agent_id,
                error=str(exc),
                expected_revision=exc.expected_revision,
                current_revision=exc.current_revision,
            )
        except (TypeError, ValueError) as exc:
            revision = current_state.revision if current_state is not None else repo.read().revision
            return _review_request_effect_error_result(
                revision=revision,
                review_request_id=review_request_id,
                actor_agent_id=actor_agent_id,
                deliverable_id=deliverable_id,
                reviewer_agent_id=reviewer_agent_id,
                error=str(exc),
            )

        published_event_ids: list[str] = []
        if publish_wake_events:
            published_event_ids = _publish_team_events(
                self.runtime_context, review_result["events"]
            )
        return _review_request_effect_success_result(
            state,
            result=review_result,
            review_request_id=review_request_id,
            actor_agent_id=actor_agent_id,
            published_event_ids=published_event_ids,
        )


class TeamCreateReviewCohortEffectProvider(EffectProviderBase):
    @classmethod
    def validate_definition(cls, effect_definition):
        if isinstance(effect_definition, WorkflowNodeEnvelope):
            return _materialize(TeamCreateReviewCohortEffectDefinition, effect_definition)
        return effect_definition

    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = _definition_payload(
            self.effect_definition,
            "actor_agent_id",
            "deliverable_id",
            "reviews",
            "publish_wake_events",
            "expected_revision",
        )
        if resolved_config:
            payload.update(dict(resolved_config))
        repo = TeamStateRepository(_runtime_dir(self.runtime_context))
        current_state: TeamState | None = None
        review_result: dict[str, Any] = {
            "errors": [],
            "created_inbox_item_ids": [],
            "existing_review_request_ids": [],
            "events": [],
        }
        actor_agent_id = _optional_text(payload.get("actor_agent_id"))
        deliverable_id = _optional_text(payload.get("deliverable_id"))
        review_cohort_id: str | None = None
        created_review_ids: list[str] = []
        existing_review_ids: list[str] = []
        publish_wake_events = True
        try:
            current_state = repo.read()
            reviews = _list_of_mappings(payload.get("reviews"), "reviews")
            publish_wake_events = _optional_bool(
                payload.get("publish_wake_events"),
                default=True,
            )

            def _mutate(state: TeamState) -> TeamState:
                nonlocal review_cohort_id, created_review_ids, existing_review_ids
                projects_to_recalc: set[str] = set()
                actor_id = _required_text(
                    payload.get("actor_agent_id"),
                    "actor_agent_id",
                )
                actor = state.agents.get(actor_id)
                if actor is None:
                    raise ValueError(f"actor agent {actor_id} does not exist")
                requested_deliverable_id = _required_text(
                    payload.get("deliverable_id"),
                    "deliverable_id",
                )
                deliverable = state.deliverables.get(requested_deliverable_id)
                already_sealed = bool(
                    deliverable is not None
                    and deliverable.review_cohort_id is not None
                    and deliverable.review_cohort_sealed_at is not None
                )
                if not already_sealed:
                    for index, review in enumerate(reviews):
                        reviewer_id = _required_text(
                            review.get("reviewer_agent_id"),
                            f"reviews[{index}].reviewer_agent_id",
                        )
                        reviewer = state.agents.get(reviewer_id)
                        if reviewer is None:
                            raise ValueError(f"reviewer agent {reviewer_id} does not exist")
                        if reviewer.status != AgentStatus.ACTIVE:
                            raise ValueError(
                                f"recipient agent {reviewer.id} is {reviewer.status.value}"
                            )
                (
                    review_cohort_id,
                    created_review_ids,
                    existing_review_ids,
                ) = create_sealed_review_cohort(
                    state,
                    actor=actor,
                    deliverable_id=requested_deliverable_id,
                    reviews=reviews,
                    result=review_result,
                    projects_to_recalc=projects_to_recalc,
                )
                for project_id in sorted(projects_to_recalc):
                    _recalculate_project_status(state, project_id)
                return state

            state = repo.update(
                _mutate,
                expected_revision=_optional_int(
                    payload.get("expected_revision"),
                    default=None,
                ),
            )
        except TeamStateConflictError as exc:
            return _review_cohort_effect_error_result(
                revision=exc.current_revision,
                review_cohort_id=review_cohort_id,
                actor_agent_id=actor_agent_id,
                deliverable_id=deliverable_id,
                error=str(exc),
                expected_revision=exc.expected_revision,
                current_revision=exc.current_revision,
            )
        except (TypeError, ValueError) as exc:
            revision = current_state.revision if current_state is not None else repo.read().revision
            return _review_cohort_effect_error_result(
                revision=revision,
                review_cohort_id=review_cohort_id,
                actor_agent_id=actor_agent_id,
                deliverable_id=deliverable_id,
                error=str(exc),
            )

        published_event_ids: list[str] = []
        if publish_wake_events:
            published_event_ids = _publish_team_events(
                self.runtime_context,
                review_result["events"],
            )
        return _review_cohort_effect_success_result(
            state,
            review_cohort_id=review_cohort_id,
            actor_agent_id=actor_agent_id,
            created_review_ids=created_review_ids,
            existing_review_ids=existing_review_ids,
            result=review_result,
            published_event_ids=published_event_ids,
        )
