"""Provider agents."""

from __future__ import annotations

from collections.abc import (
    Mapping,
)
from typing import (
    Any,
)

from evander.effects.providers.base import (
    EffectProviderBase,
    scoped_effect_directory_path,
)
from evander.events.models import (
    WorkflowNodeEnvelope,
)
from evander.repositories.orchestration import (
    read_latest_agent_prompt_execution,
)

from .provider_completion import (
    TeamAgentPromptCompletionFinalizeHook,
)
from .provider_events import (
    _materialize,
    _publish_team_events,
)
from .provider_prompt_queue import (
    _team_native_thread_receipt,
)
from .provider_results import (
    _register_agent_effect_error_result,
    _register_agent_effect_success_result,
    _restore_agent_effect_error_result,
    _restore_agent_effect_success_result,
    _retire_agent_effect_error_result,
    _retire_agent_effect_success_result,
)
from .provider_runtime import (
    _review_mcp_capability_observation,
    _session_repository_from_context,
)
from .provider_schemas import (
    TeamAcceptEphemeralDeliverableEffectDefinition,
    TeamFinalizeAgentDispatchEffectDefinition,
    TeamMarkEphemeralCleanedUpEffectDefinition,
    TeamMarkEphemeralSpawnedEffectDefinition,
    TeamRegisterAgentEffectDefinition,
    TeamRequestEphemeralAgentsEffectDefinition,
    TeamRestoreAgentEffectDefinition,
    TeamRetireAgentEffectDefinition,
)
from .provider_values import (
    _definition_payload,
    _list_of_mappings,
    _optional_bool,
    _optional_int,
    _optional_mapping,
    _optional_text,
    _required_text,
    _runtime_dir,
)
from .repository import (
    TeamStateConflictError,
    TeamStateRepository,
)
from .services.ephemeral_service import (
    TeamEphemeralService,
)
from .services.runtime_service import (
    TeamRuntimeService,
)
from .services.workflow_agent_lifecycle_service import (
    TeamAgentRetirementBlockedError,
    TeamWorkflowAgentLifecycleService,
)


class TeamFinalizeAgentDispatchEffectProvider(EffectProviderBase):
    @classmethod
    def lifecycle_hooks(cls) -> tuple[type["TeamAgentPromptCompletionFinalizeHook"], ...]:
        return (TeamAgentPromptCompletionFinalizeHook,)

    @classmethod
    def validate_definition(cls, effect_definition):
        if isinstance(effect_definition, WorkflowNodeEnvelope):
            return _materialize(
                TeamFinalizeAgentDispatchEffectDefinition,
                effect_definition,
            )
        return effect_definition

    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = _definition_payload(
            self.effect_definition,
            "session_id",
            "dispatch_id",
            "agent_id",
            "expected_revision",
        )
        if resolved_config:
            payload.update(dict(resolved_config))
        repo = TeamStateRepository(_runtime_dir(self.runtime_context))
        dispatch = repo.read().dispatches.get(
            _required_text(payload.get("dispatch_id"), "dispatch_id")
        )
        receipt_record = read_latest_agent_prompt_execution(
            repo.runtime_dir / "orchestration.db",
            session_id=_required_text(payload.get("session_id"), "session_id"),
            dispatch_id=_required_text(payload.get("dispatch_id"), "dispatch_id"),
        )
        try:
            _state, result = TeamRuntimeService(repo).finalize_agent_dispatch_from_activity(
                session_id=_required_text(payload.get("session_id"), "session_id"),
                dispatch_id=_required_text(payload.get("dispatch_id"), "dispatch_id"),
                agent_id=_optional_text(payload.get("agent_id")),
                expected_revision=_optional_int(
                    payload.get("expected_revision"),
                    default=None,
                ),
                mcp_capability_observation=_review_mcp_capability_observation(
                    self.runtime_context,
                    dispatch=dispatch,
                ),
                native_thread_receipt=_team_native_thread_receipt(receipt_record),
            )
        except TeamStateConflictError as exc:
            return {
                "success": False,
                "error": str(exc),
                "expected_revision": exc.expected_revision,
                "current_revision": exc.current_revision,
            }
        published_event_ids = _publish_team_events(
            self.runtime_context,
            result.get("events"),
        )
        if published_event_ids:
            result["published_event_ids"] = published_event_ids
        # The receipt identifies the committed revision and dispatch. Historical
        # Team state belongs in the Team read APIs, not every downstream event.
        return result


class TeamRegisterAgentEffectProvider(EffectProviderBase):
    @classmethod
    def validate_definition(cls, effect_definition):
        if isinstance(effect_definition, WorkflowNodeEnvelope):
            return _materialize(TeamRegisterAgentEffectDefinition, effect_definition)
        return effect_definition

    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = _definition_payload(
            self.effect_definition,
            "session_id",
            "team_id",
            "role_id",
            "agent_id",
            "project_id",
            "reports_to_agent_id",
            "display_name",
            "expected_revision",
        )
        if resolved_config:
            payload.update(dict(resolved_config))
        repo = TeamStateRepository(_runtime_dir(self.runtime_context))
        session_id = _optional_text(payload.get("session_id"))
        team_id = _optional_text(payload.get("team_id"))
        project_id = _optional_text(payload.get("project_id"))
        try:
            result = TeamWorkflowAgentLifecycleService(
                repo,
                session_lookup=_session_repository_from_context(self.runtime_context),
                workflow_directory_path=scoped_effect_directory_path(self.runtime_context),
            ).register_agent(
                session_id=_required_text(payload.get("session_id"), "session_id"),
                team_id=_required_text(payload.get("team_id"), "team_id"),
                role_id=_required_text(payload.get("role_id"), "role_id"),
                agent_id=_optional_text(payload.get("agent_id")),
                project_id=project_id,
                reports_to_agent_id=_optional_text(payload.get("reports_to_agent_id")),
                display_name=_optional_text(payload.get("display_name")),
                expected_revision=_optional_int(payload.get("expected_revision"), default=None),
            )
        except TeamStateConflictError as exc:
            return _register_agent_effect_error_result(
                revision=exc.current_revision,
                agent_id=_optional_text(payload.get("agent_id")),
                session_id=session_id,
                team_id=team_id,
                project_id=project_id,
                error=str(exc),
                expected_revision=exc.expected_revision,
                current_revision=exc.current_revision,
            )
        except (RuntimeError, TypeError, ValueError) as exc:
            return _register_agent_effect_error_result(
                revision=repo.read().revision,
                agent_id=_optional_text(payload.get("agent_id")),
                session_id=session_id,
                team_id=team_id,
                project_id=project_id,
                error=str(exc),
            )
        return _register_agent_effect_success_result(
            state=result.state,
            agent=result.agent,
            created=result.created,
        )


class TeamRestoreAgentEffectProvider(EffectProviderBase):
    @classmethod
    def validate_definition(cls, effect_definition):
        if isinstance(effect_definition, WorkflowNodeEnvelope):
            return _materialize(TeamRestoreAgentEffectDefinition, effect_definition)
        return effect_definition

    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = _definition_payload(
            self.effect_definition,
            "agent_id",
            "new_session_id",
            "expected_old_session_id",
            "expected_team_id",
            "role_id",
            "project_id",
            "reports_to_agent_id",
            "expected_revision",
        )
        if resolved_config:
            payload.update(dict(resolved_config))
        repo = TeamStateRepository(_runtime_dir(self.runtime_context))
        agent_id = _optional_text(payload.get("agent_id"))
        new_session_id = _optional_text(payload.get("new_session_id"))
        previous_session_id = _optional_text(payload.get("expected_old_session_id"))
        team_id = _optional_text(payload.get("expected_team_id"))
        project_id = _optional_text(payload.get("project_id"))
        try:
            result = TeamWorkflowAgentLifecycleService(
                repo,
                session_lookup=_session_repository_from_context(self.runtime_context),
                workflow_directory_path=scoped_effect_directory_path(self.runtime_context),
            ).restore_agent(
                agent_id=_required_text(payload.get("agent_id"), "agent_id"),
                new_session_id=_required_text(payload.get("new_session_id"), "new_session_id"),
                expected_old_session_id=_required_text(
                    payload.get("expected_old_session_id"),
                    "expected_old_session_id",
                ),
                expected_team_id=_required_text(
                    payload.get("expected_team_id"),
                    "expected_team_id",
                ),
                role_id=_required_text(payload.get("role_id"), "role_id"),
                project_id=project_id,
                reports_to_agent_id=_optional_text(payload.get("reports_to_agent_id")),
                expected_revision=_optional_int(payload.get("expected_revision"), default=None),
            )
        except TeamStateConflictError as exc:
            return _restore_agent_effect_error_result(
                revision=exc.current_revision,
                agent_id=agent_id,
                previous_session_id=previous_session_id,
                session_id=new_session_id,
                team_id=team_id,
                project_id=project_id,
                error=str(exc),
                expected_revision=exc.expected_revision,
                current_revision=exc.current_revision,
            )
        except (RuntimeError, TypeError, ValueError) as exc:
            return _restore_agent_effect_error_result(
                revision=repo.read().revision,
                agent_id=agent_id,
                previous_session_id=previous_session_id,
                session_id=new_session_id,
                team_id=team_id,
                project_id=project_id,
                error=str(exc),
            )
        return _restore_agent_effect_success_result(
            state=result.state,
            agent=result.agent,
            previous_session_id=result.previous_session_id,
            restored=result.restored,
            already_restored=result.already_restored,
        )


class TeamRetireAgentEffectProvider(EffectProviderBase):
    @classmethod
    def validate_definition(cls, effect_definition):
        if isinstance(effect_definition, WorkflowNodeEnvelope):
            return _materialize(TeamRetireAgentEffectDefinition, effect_definition)
        return effect_definition

    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = _definition_payload(
            self.effect_definition,
            "agent_id",
            "expected_session_id",
            "expected_team_id",
            "expected_revision",
        )
        if resolved_config:
            payload.update(dict(resolved_config))
        repo = TeamStateRepository(_runtime_dir(self.runtime_context))
        agent_id = _optional_text(payload.get("agent_id"))
        session_id = _optional_text(payload.get("expected_session_id"))
        try:
            result = TeamWorkflowAgentLifecycleService(
                repo,
                session_lookup=_session_repository_from_context(self.runtime_context),
                workflow_directory_path=scoped_effect_directory_path(self.runtime_context),
            ).retire_agent(
                agent_id=_required_text(payload.get("agent_id"), "agent_id"),
                expected_session_id=_required_text(
                    payload.get("expected_session_id"),
                    "expected_session_id",
                ),
                expected_team_id=_required_text(
                    payload.get("expected_team_id"),
                    "expected_team_id",
                ),
                expected_revision=_optional_int(payload.get("expected_revision"), default=None),
            )
        except TeamStateConflictError as exc:
            return _retire_agent_effect_error_result(
                revision=exc.current_revision,
                agent_id=agent_id,
                session_id=session_id,
                blockers=[],
                error=str(exc),
                expected_revision=exc.expected_revision,
                current_revision=exc.current_revision,
            )
        except TeamAgentRetirementBlockedError as exc:
            return _retire_agent_effect_error_result(
                revision=repo.read().revision,
                agent_id=agent_id,
                session_id=session_id,
                blockers=exc.blockers,
                error=str(exc),
            )
        except (RuntimeError, TypeError, ValueError) as exc:
            return _retire_agent_effect_error_result(
                revision=repo.read().revision,
                agent_id=agent_id,
                session_id=session_id,
                blockers=[],
                error=str(exc),
            )
        return _retire_agent_effect_success_result(
            state=result.state,
            agent=result.agent,
            retired=result.retired,
            already_retired=result.already_retired,
        )


class TeamRequestEphemeralAgentsEffectProvider(EffectProviderBase):
    @classmethod
    def validate_definition(cls, effect_definition):
        if isinstance(effect_definition, WorkflowNodeEnvelope):
            return _materialize(TeamRequestEphemeralAgentsEffectDefinition, effect_definition)
        return effect_definition

    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = _definition_payload(
            self.effect_definition,
            "parent_agent_id",
            "requests",
            "dispatch_id",
            "expected_revision",
        )
        if resolved_config:
            payload.update(dict(resolved_config))
        repo = TeamStateRepository(_runtime_dir(self.runtime_context))
        ephemeral_service = TeamEphemeralService(
            repo,
            session_lookup=_session_repository_from_context(self.runtime_context),
        )
        try:
            _state, result = ephemeral_service.request_ephemeral_assignments(
                parent_agent_id=_required_text(payload.get("parent_agent_id"), "parent_agent_id"),
                requests=_list_of_mappings(payload.get("requests"), "requests"),
                dispatch_id=_optional_text(payload.get("dispatch_id")),
                expected_revision=_optional_int(payload.get("expected_revision"), default=None),
            )
        except TeamStateConflictError as exc:
            return {
                "success": False,
                "error": str(exc),
                "expected_revision": exc.expected_revision,
                "current_revision": exc.current_revision,
            }
        published_event_ids = _publish_team_events(self.runtime_context, result.get("events"))
        if published_event_ids:
            result["published_event_ids"] = published_event_ids
        return result


class TeamMarkEphemeralSpawnedEffectProvider(EffectProviderBase):
    @classmethod
    def validate_definition(cls, effect_definition):
        if isinstance(effect_definition, WorkflowNodeEnvelope):
            return _materialize(TeamMarkEphemeralSpawnedEffectDefinition, effect_definition)
        return effect_definition

    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = _definition_payload(
            self.effect_definition,
            "assignment_id",
            "session_id",
            "create_result",
            "expected_revision",
        )
        if resolved_config:
            payload.update(dict(resolved_config))
        repo = TeamStateRepository(_runtime_dir(self.runtime_context))
        ephemeral_service = TeamEphemeralService(repo)
        try:
            _state, result = ephemeral_service.mark_ephemeral_spawned(
                assignment_id=_required_text(payload.get("assignment_id"), "assignment_id"),
                session_id=_optional_text(payload.get("session_id")),
                create_result=_optional_mapping(payload.get("create_result"), "create_result"),
                expected_revision=_optional_int(payload.get("expected_revision"), default=None),
            )
        except TeamStateConflictError as exc:
            return {
                "success": False,
                "error": str(exc),
                "expected_revision": exc.expected_revision,
                "current_revision": exc.current_revision,
            }
        return result


class TeamAcceptEphemeralDeliverableEffectProvider(EffectProviderBase):
    @classmethod
    def validate_definition(cls, effect_definition):
        if isinstance(effect_definition, WorkflowNodeEnvelope):
            return _materialize(TeamAcceptEphemeralDeliverableEffectDefinition, effect_definition)
        return effect_definition

    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = _definition_payload(
            self.effect_definition,
            "assignment_id",
            "accepted",
            "deliverable_id",
            "rejection_reason",
            "expected_revision",
        )
        if resolved_config:
            payload.update(dict(resolved_config))
        repo = TeamStateRepository(_runtime_dir(self.runtime_context))
        ephemeral_service = TeamEphemeralService(repo)
        try:
            _state, result = ephemeral_service.accept_ephemeral_deliverable(
                assignment_id=_required_text(payload.get("assignment_id"), "assignment_id"),
                accepted=_optional_bool(payload.get("accepted"), default=True),
                deliverable_id=_optional_text(payload.get("deliverable_id")),
                rejection_reason=_optional_text(payload.get("rejection_reason")),
                expected_revision=_optional_int(payload.get("expected_revision"), default=None),
            )
        except TeamStateConflictError as exc:
            return {
                "success": False,
                "error": str(exc),
                "expected_revision": exc.expected_revision,
                "current_revision": exc.current_revision,
            }
        return result


class TeamMarkEphemeralCleanedUpEffectProvider(EffectProviderBase):
    @classmethod
    def validate_definition(cls, effect_definition):
        if isinstance(effect_definition, WorkflowNodeEnvelope):
            return _materialize(TeamMarkEphemeralCleanedUpEffectDefinition, effect_definition)
        return effect_definition

    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = _definition_payload(
            self.effect_definition,
            "assignment_id",
            "session_id",
            "destroy_result",
            "expected_revision",
        )
        if resolved_config:
            payload.update(dict(resolved_config))
        repo = TeamStateRepository(_runtime_dir(self.runtime_context))
        ephemeral_service = TeamEphemeralService(repo)
        try:
            _state, result = ephemeral_service.mark_ephemeral_cleaned_up(
                assignment_id=_optional_text(payload.get("assignment_id")),
                session_id=_optional_text(payload.get("session_id")),
                destroy_result=_optional_mapping(payload.get("destroy_result"), "destroy_result"),
                expected_revision=_optional_int(payload.get("expected_revision"), default=None),
            )
        except TeamStateConflictError as exc:
            return {
                "success": False,
                "error": str(exc),
                "expected_revision": exc.expected_revision,
                "current_revision": exc.current_revision,
            }
        return result
