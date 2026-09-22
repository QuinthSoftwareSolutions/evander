"""Provider tasks."""

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
    TeamState,
    TeamTaskState,
)
from .outbox_publication import (
    request_team_outbox_publication_for_records,
)
from .provider_events import (
    _materialize,
)
from .provider_results import (
    _case_effect_error_result,
    _case_effect_success_result,
    _task_effect_error_result,
    _task_effect_success_result,
    _validate_create_task_effect_scope,
)
from .provider_schemas import (
    TeamCreateCaseEffectDefinition,
    TeamCreateTaskEffectDefinition,
    TeamSetTaskWorkStateEffectDefinition,
    TeamUpdateCaseEffectDefinition,
)
from .provider_values import (
    _configured_definition_fields,
    _definition_payload,
    _optional_bool,
    _optional_int,
    _optional_mapping,
    _optional_text,
    _payload_field_configured,
    _required_text,
    _runtime_dir,
    _string_list,
)
from .repository import (
    TeamStateConflictError,
    TeamStateRepository,
)
from .services.admin_service import (
    TeamAdminService,
)
from .services.ado_attribution import (
    capture_work_item_attribution,
)
from .services.domain.case_events import (
    TEAM_CASE_CLOSED_EVENT_TYPE,
)


class TeamCreateTaskEffectProvider(EffectProviderBase):
    @classmethod
    def validate_definition(cls, effect_definition):
        if isinstance(effect_definition, WorkflowNodeEnvelope):
            return _materialize(TeamCreateTaskEffectDefinition, effect_definition)
        return effect_definition

    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = _definition_payload(
            self.effect_definition,
            "team_id",
            "project_id",
            "owner_agent_id",
            "priority",
            "title",
            "description",
            "task_id",
            "case_id",
            "state",
            "task_kind",
            "parent_task_id",
            "originating_task_id",
            "authored_by",
            "ado_work_item_id",
            "ado_project",
            "git_base_branch",
            "git_base_commit_sha",
            "dependency_task_ids",
            "integration_boundary",
            "deliverable_contract",
            "created_by",
            "expected_revision",
        )
        definition_fields = _configured_definition_fields(self.effect_definition)
        resolved_fields: set[str] = set()
        if resolved_config:
            resolved_fields = {str(key) for key in resolved_config}
            payload.update(dict(resolved_config))
        repo = TeamStateRepository(_runtime_dir(self.runtime_context))
        task_id = _optional_text(payload.get("task_id"))
        team_id = _optional_text(payload.get("team_id"))
        project_id = _optional_text(payload.get("project_id"))
        owner_agent_id = _optional_text(payload.get("owner_agent_id"))
        current_state: TeamState | None = None
        try:
            current_state = repo.read()
            normalized_team_id = _required_text(team_id, "team_id")
            normalized_project_id = _required_text(project_id, "project_id")
            normalized_owner_id = _required_text(owner_agent_id, "owner_agent_id")
            normalized_priority = _optional_int(payload.get("priority"), default=None)
            if normalized_priority is None:
                raise ValueError("priority is required")
            _validate_create_task_effect_scope(
                current_state,
                team_id=normalized_team_id,
                project_id=normalized_project_id,
                owner_agent_id=normalized_owner_id,
                task_id=task_id,
            )
            case_configured = _payload_field_configured(
                "case_id",
                definition_fields=definition_fields,
                resolved_fields=resolved_fields,
            )
            parent_configured = _payload_field_configured(
                "parent_task_id",
                definition_fields=definition_fields,
                resolved_fields=resolved_fields,
            )
            git_base_configured = _payload_field_configured(
                "git_base_branch",
                definition_fields=definition_fields,
                resolved_fields=resolved_fields,
            )
            dependencies_configured = _payload_field_configured(
                "dependency_task_ids",
                definition_fields=definition_fields,
                resolved_fields=resolved_fields,
            )
            integration_boundary_configured = _payload_field_configured(
                "integration_boundary",
                definition_fields=definition_fields,
                resolved_fields=resolved_fields,
            )
            deliverable_contract_configured = _payload_field_configured(
                "deliverable_contract",
                definition_fields=definition_fields,
                resolved_fields=resolved_fields,
            )
            attributions = None
            ado_work_item_id = _optional_text(payload.get("ado_work_item_id"))
            if ado_work_item_id and task_id not in current_state.tasks:
                attributions, author = capture_work_item_attribution(
                    self.runtime_context,
                    directory_path=current_state.projects[normalized_project_id].directory_path,
                    work_item_id=ado_work_item_id,
                    project=_optional_text(payload.get("ado_project")),
                )
                payload["authored_by"] = author.model_dump(mode="json") if author else None
            state, effective_task_id, created = TeamAdminService(
                repo,
                managed_git_admin=getattr(
                    getattr(self.runtime_context, "admin_service", None),
                    "managed_git_admin",
                    None,
                ),
            ).create_or_reprioritize_task_with_result(
                task_id=task_id,
                owner_agent_id=normalized_owner_id,
                project_id=normalized_project_id,
                case_id=_optional_text(payload.get("case_id")),
                set_case_id=case_configured,
                priority=normalized_priority,
                title=_required_text(payload.get("title"), "title"),
                description=_required_text(payload.get("description"), "description"),
                created_by_actor=_optional_text(payload.get("created_by")) or "workflow",
                state=TeamTaskState(_optional_text(payload.get("state")) or "pending"),
                task_kind=_optional_text(payload.get("task_kind")),
                parent_task_id=_optional_text(payload.get("parent_task_id")),
                set_parent_task_id=parent_configured,
                originating_task_id=_optional_text(payload.get("originating_task_id")),
                authored_by=payload.get("authored_by"),
                work_item_attributions=attributions,
                set_authored_by=_payload_field_configured(
                    "authored_by",
                    definition_fields=definition_fields,
                    resolved_fields=resolved_fields,
                ),
                git_base_branch=_optional_text(payload.get("git_base_branch")),
                git_base_commit_sha=_optional_text(payload.get("git_base_commit_sha")),
                set_git_base_branch=git_base_configured,
                dependency_task_ids=(
                    _string_list(payload.get("dependency_task_ids"), "dependency_task_ids")
                    if dependencies_configured
                    else None
                ),
                integration_boundary=(
                    _optional_bool(
                        payload.get("integration_boundary"),
                        default=False,
                    )
                    if integration_boundary_configured
                    else None
                ),
                deliverable_contract=(
                    _optional_mapping(
                        payload.get("deliverable_contract"),
                        "deliverable_contract",
                    )
                    if deliverable_contract_configured
                    else None
                ),
                set_deliverable_contract=deliverable_contract_configured,
                expected_revision=_optional_int(payload.get("expected_revision"), default=None),
            )
        except TeamStateConflictError as exc:
            return _task_effect_error_result(
                revision=exc.current_revision,
                task_id=task_id,
                team_id=team_id,
                project_id=project_id,
                owner_agent_id=owner_agent_id,
                error=str(exc),
                expected_revision=exc.expected_revision,
                current_revision=exc.current_revision,
            )
        except (TypeError, ValueError) as exc:
            revision = current_state.revision if current_state is not None else repo.read().revision
            return _task_effect_error_result(
                revision=revision,
                task_id=task_id,
                team_id=team_id,
                project_id=project_id,
                owner_agent_id=owner_agent_id,
                error=str(exc),
            )
        return _task_effect_success_result(
            state,
            task_id=effective_task_id,
            created=created,
        )


class TeamSetTaskWorkStateEffectProvider(EffectProviderBase):
    @classmethod
    def validate_definition(cls, effect_definition):
        if isinstance(effect_definition, WorkflowNodeEnvelope):
            return _materialize(TeamSetTaskWorkStateEffectDefinition, effect_definition)
        return effect_definition

    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = _definition_payload(
            self.effect_definition,
            "task_id",
            "state",
            "expected_revision",
        )
        if resolved_config:
            payload.update(dict(resolved_config))
        repo = TeamStateRepository(_runtime_dir(self.runtime_context))
        task_id = _optional_text(payload.get("task_id"))
        current_state: TeamState | None = None
        try:
            current_state = repo.read()
            task = current_state.tasks.get(_required_text(task_id, "task_id"))
            if task is None:
                raise ValueError(f"task {task_id} does not exist")
            work_states = {
                TeamTaskState.PENDING,
                TeamTaskState.IN_PROGRESS,
                TeamTaskState.WAITING_FOR_INPUT,
            }
            if task.state not in work_states:
                raise ValueError(
                    f"effect:team:set-task-work-state cannot change task {task.id} "
                    f"from {task.state.value}; current state must be pending, "
                    "in_progress, or waiting_for_input"
                )
            requested_state = TeamTaskState(_required_text(payload.get("state"), "state"))
            if requested_state not in work_states:
                raise ValueError(
                    "effect:team:set-task-work-state state must be pending, "
                    "in_progress, or waiting_for_input"
                )
            expected_revision = _optional_int(payload.get("expected_revision"), default=None)
            state = TeamAdminService(repo).update_task(
                task_id=task.id,
                state=requested_state,
                expected_revision=(
                    expected_revision if expected_revision is not None else current_state.revision
                ),
            )
        except TeamStateConflictError as exc:
            return _task_effect_error_result(
                revision=exc.current_revision,
                task_id=task_id,
                team_id=None,
                project_id=None,
                owner_agent_id=None,
                error=str(exc),
                expected_revision=exc.expected_revision,
                current_revision=exc.current_revision,
            )
        except (TypeError, ValueError) as exc:
            task = current_state.tasks.get(task_id or "") if current_state is not None else None
            project = current_state.projects.get(task.project_id) if task is not None else None
            return _task_effect_error_result(
                revision=current_state.revision
                if current_state is not None
                else repo.read().revision,
                task_id=task_id,
                team_id=project.team_id if project is not None else None,
                project_id=task.project_id if task is not None else None,
                owner_agent_id=task.owner_agent_id if task is not None else None,
                error=str(exc),
            )
        return _task_effect_success_result(state, task_id=task_id, created=False)


class TeamCreateCaseEffectProvider(EffectProviderBase):
    @classmethod
    def validate_definition(cls, effect_definition):
        if isinstance(effect_definition, WorkflowNodeEnvelope):
            return _materialize(TeamCreateCaseEffectDefinition, effect_definition)
        return effect_definition

    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = _definition_payload(
            self.effect_definition,
            "case_id",
            "reuse_existing",
            "team_id",
            "title",
            "description",
            "project_id",
            "status",
            "priority",
            "owner_agent_id",
            "source",
            "source_ref",
            "tags",
            "metadata",
            "task_ids",
            "expected_revision",
        )
        if resolved_config:
            payload.update(dict(resolved_config))
        repo = TeamStateRepository(_runtime_dir(self.runtime_context))
        case_id = _optional_text(payload.get("case_id"))
        try:
            state, effective_case_id = TeamAdminService(repo).create_case_with_id(
                case_id=case_id,
                reuse_existing=_optional_bool(payload.get("reuse_existing"), default=False),
                team_id=_required_text(payload.get("team_id"), "team_id"),
                title=_required_text(payload.get("title"), "title"),
                description=_optional_text(payload.get("description")) or "",
                project_id=_optional_text(payload.get("project_id")),
                status=_optional_text(payload.get("status")) or "open",
                priority=_optional_text(payload.get("priority")) or "normal",
                owner_agent_id=_optional_text(payload.get("owner_agent_id")),
                source=_optional_text(payload.get("source")),
                source_ref=_optional_text(payload.get("source_ref")),
                tags=_string_list(payload.get("tags"), "tags"),
                metadata=_optional_mapping(payload.get("metadata"), "metadata") or {},
                task_ids=_string_list(payload.get("task_ids"), "task_ids"),
                expected_revision=_optional_int(payload.get("expected_revision"), default=None),
            )
        except TeamStateConflictError as exc:
            return _case_effect_error_result(
                revision=exc.current_revision,
                case_id=case_id,
                team_id=_optional_text(payload.get("team_id")),
                project_id=_optional_text(payload.get("project_id")),
                error=str(exc),
                expected_revision=exc.expected_revision,
                current_revision=exc.current_revision,
            )
        except (TypeError, ValueError) as exc:
            return _case_effect_error_result(
                revision=repo.read().revision,
                case_id=case_id,
                team_id=_optional_text(payload.get("team_id")),
                project_id=_optional_text(payload.get("project_id")),
                error=str(exc),
            )
        return _case_effect_success_result(state, case_id=effective_case_id)


class TeamUpdateCaseEffectProvider(EffectProviderBase):
    @classmethod
    def validate_definition(cls, effect_definition):
        if isinstance(effect_definition, WorkflowNodeEnvelope):
            return _materialize(TeamUpdateCaseEffectDefinition, effect_definition)
        return effect_definition

    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = _definition_payload(
            self.effect_definition,
            "case_id",
            "title",
            "description",
            "project_id",
            "status",
            "priority",
            "owner_agent_id",
            "source",
            "source_ref",
            "tags",
            "metadata",
            "task_ids",
            "expected_revision",
        )
        resolved_fields = set(resolved_config or {})
        definition_fields = _configured_definition_fields(self.effect_definition)
        if resolved_config:
            payload.update(dict(resolved_config))
        repo = TeamStateRepository(_runtime_dir(self.runtime_context))
        case_id = _optional_text(payload.get("case_id"))
        try:
            required_case_id = _required_text(payload.get("case_id"), "case_id")
            state = TeamAdminService(repo).update_case(
                case_id=required_case_id,
                title=_optional_text(payload.get("title")),
                description=_optional_text(payload.get("description")),
                set_description=_payload_field_configured(
                    "description",
                    definition_fields=definition_fields,
                    resolved_fields=resolved_fields,
                ),
                project_id=_optional_text(payload.get("project_id")),
                set_project_id=_payload_field_configured(
                    "project_id",
                    definition_fields=definition_fields,
                    resolved_fields=resolved_fields,
                ),
                status=_optional_text(payload.get("status")),
                priority=_optional_text(payload.get("priority")),
                owner_agent_id=_optional_text(payload.get("owner_agent_id")),
                set_owner_agent_id=_payload_field_configured(
                    "owner_agent_id",
                    definition_fields=definition_fields,
                    resolved_fields=resolved_fields,
                ),
                source=_optional_text(payload.get("source")),
                set_source=_payload_field_configured(
                    "source",
                    definition_fields=definition_fields,
                    resolved_fields=resolved_fields,
                ),
                source_ref=_optional_text(payload.get("source_ref")),
                set_source_ref=_payload_field_configured(
                    "source_ref",
                    definition_fields=definition_fields,
                    resolved_fields=resolved_fields,
                ),
                tags=(
                    _string_list(payload.get("tags"), "tags")
                    if _payload_field_configured(
                        "tags",
                        definition_fields=definition_fields,
                        resolved_fields=resolved_fields,
                    )
                    and payload.get("tags") is not None
                    else None
                ),
                metadata=(
                    _optional_mapping(payload.get("metadata"), "metadata")
                    if _payload_field_configured(
                        "metadata",
                        definition_fields=definition_fields,
                        resolved_fields=resolved_fields,
                    )
                    and payload.get("metadata") is not None
                    else None
                ),
                task_ids=(
                    _string_list(payload.get("task_ids"), "task_ids")
                    if _payload_field_configured(
                        "task_ids",
                        definition_fields=definition_fields,
                        resolved_fields=resolved_fields,
                    )
                    and payload.get("task_ids") is not None
                    else None
                ),
                expected_revision=_optional_int(payload.get("expected_revision"), default=None),
            )
        except TeamStateConflictError as exc:
            return _case_effect_error_result(
                revision=exc.current_revision,
                case_id=case_id,
                team_id=None,
                project_id=_optional_text(payload.get("project_id")),
                error=str(exc),
                expected_revision=exc.expected_revision,
                current_revision=exc.current_revision,
            )
        except (TypeError, ValueError) as exc:
            return _case_effect_error_result(
                revision=repo.read().revision,
                case_id=case_id,
                team_id=None,
                project_id=_optional_text(payload.get("project_id")),
                error=str(exc),
            )
        result = _case_effect_success_result(state, case_id=required_case_id)
        closure_outbox_ids = [
            record.id
            for record in repo.list_outbox_records(
                status="pending",
                intent_type=TEAM_CASE_CLOSED_EVENT_TYPE,
                aggregate_id=required_case_id,
            )
        ]
        publication_wake = request_team_outbox_publication_for_records(
            self.runtime_context,
            outbox_record_ids=closure_outbox_ids,
        )
        if publication_wake is not None:
            result["outbox_publication_wake"] = publication_wake
        return result
