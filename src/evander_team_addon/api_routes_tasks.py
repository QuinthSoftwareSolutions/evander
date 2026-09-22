"""Team tasks endpoints."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from starlette.requests import (
    Request,
)
from starlette.responses import Response

from .api_constants import (
    JSONResponse,
)
from .api_context import TeamApiDependencies
from .api_requests import (
    _expected_revision,
    _idle_context_rotation_outbox_publication_extra,
    _operator_actor_from_request,
    _optional,
    _optional_bool,
    _optional_int,
    _optional_object,
    _optional_present_text,
    _optional_string_list,
    _read_payload,
    _required,
    _required_int,
)
from .models import (
    TeamCaseStatus,
    TeamInboxPriority,
    TeamOutboxStatus,
    TeamState,
    TeamTaskState,
)
from .outbox_publication import (
    request_team_outbox_publication_for_records,
)
from .services.domain.case_events import (
    TEAM_CASE_CLOSED_EVENT_TYPE,
)


def build_tasks_handlers(
    dependencies: TeamApiDependencies,
) -> dict[str, Callable[[Request], Awaitable[Response]]]:
    context = dependencies.context
    repository = dependencies.repository
    admin_service = dependencies.admin_service
    _state_response = dependencies._state_response
    _run_ui_rebasable_mutation = dependencies._run_ui_rebasable_mutation
    _task_project_owner_authorization_error = dependencies._task_project_owner_authorization_error

    async def create_case(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        case_id = _optional(payload, "id") or _optional(payload, "case_id")
        raw_status = _optional(payload, "status")
        raw_priority = _optional(payload, "priority")
        state, rebased_from_revision = _run_ui_rebasable_mutation(
            payload,
            lambda expected_revision: admin_service.create_case(
                case_id=case_id,
                team_id=_required(payload, "team_id"),
                project_id=_optional(payload, "project_id"),
                title=_required(payload, "title"),
                description=_optional(payload, "description") or "",
                status=TeamCaseStatus(raw_status) if raw_status else TeamCaseStatus.OPEN,
                priority=(
                    TeamInboxPriority(raw_priority) if raw_priority else TeamInboxPriority.NORMAL
                ),
                owner_agent_id=_optional(payload, "owner_agent_id"),
                source=_optional(payload, "source"),
                source_ref=_optional(payload, "source_ref"),
                tags=_optional_string_list(payload, "tags") or [],
                metadata=_optional_object(payload, "metadata") or {},
                task_ids=_optional_string_list(payload, "task_ids") or [],
                expected_revision=expected_revision,
            ),
            create_collection="cases",
            create_id=case_id,
        )
        return _state_response(
            request,
            state,
            status_code=201,
            rebased_from_revision=rebased_from_revision,
        )

    async def patch_case(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        case_id = request.path_params["case_id"]
        raw_status = _optional(payload, "status")
        raw_priority = _optional(payload, "priority")
        state, rebased_from_revision = _run_ui_rebasable_mutation(
            payload,
            lambda expected_revision: admin_service.update_case(
                case_id=case_id,
                title=_optional(payload, "title"),
                description=_optional_present_text(payload, "description"),
                set_description="description" in payload,
                project_id=_optional_present_text(payload, "project_id"),
                set_project_id="project_id" in payload,
                status=TeamCaseStatus(raw_status) if raw_status else None,
                priority=TeamInboxPriority(raw_priority) if raw_priority else None,
                owner_agent_id=_optional_present_text(payload, "owner_agent_id"),
                set_owner_agent_id="owner_agent_id" in payload,
                source=_optional_present_text(payload, "source"),
                set_source="source" in payload,
                source_ref=_optional_present_text(payload, "source_ref"),
                set_source_ref="source_ref" in payload,
                tags=_optional_string_list(payload, "tags"),
                metadata=(_optional_object(payload, "metadata") if "metadata" in payload else None),
                task_ids=_optional_string_list(payload, "task_ids"),
                expected_revision=expected_revision,
            ),
            target_collection="cases",
            target_id=case_id,
        )
        closure_outbox_ids = [
            record.id
            for record in repository.list_outbox_records(
                status=TeamOutboxStatus.PENDING,
                intent_type=TEAM_CASE_CLOSED_EVENT_TYPE,
                aggregate_id=case_id,
            )
        ]
        publication_wake = request_team_outbox_publication_for_records(
            context,
            outbox_record_ids=closure_outbox_ids,
        )
        return _state_response(
            request,
            state,
            rebased_from_revision=rebased_from_revision,
            extra=(
                {"outbox_publication_wake": publication_wake}
                if publication_wake is not None
                else None
            ),
        )

    async def create_task(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        raw_state = _optional(payload, "state")
        created_by_agent_id = _optional(payload, "created_by_agent_id")
        owner_agent_id = _required(payload, "owner_agent_id")
        task_id = _optional(payload, "id") or _optional(payload, "task_id")
        state, rebased_from_revision = _run_ui_rebasable_mutation(
            payload,
            lambda expected_revision: admin_service.create_or_reprioritize_task(
                task_id=task_id,
                owner_agent_id=owner_agent_id,
                project_id=_required(payload, "project_id"),
                case_id=_optional(payload, "case_id"),
                set_case_id="case_id" in payload,
                priority=_required_int(payload, "priority"),
                title=_required(payload, "title"),
                description=_required(payload, "description"),
                created_by_agent_id=created_by_agent_id,
                created_by_actor=(
                    None
                    if created_by_agent_id is not None
                    else _operator_actor_from_request(context.auth, request)
                ),
                state=TeamTaskState(raw_state) if raw_state else TeamTaskState.PENDING,
                parent_task_id=_optional(payload, "parent_task_id"),
                originating_task_id=_optional(payload, "originating_task_id"),
                authored_by=_optional_object(payload, "authored_by"),
                set_authored_by="authored_by" in payload,
                git_base_branch=_optional(payload, "git_base_branch"),
                git_base_commit_sha=_optional(payload, "git_base_commit_sha"),
                set_git_base_branch="git_base_branch" in payload,
                set_parent_task_id="parent_task_id" in payload,
                dependency_task_ids=_optional_string_list(payload, "dependency_task_ids"),
                task_kind=_optional(payload, "task_kind"),
                outcome_contract=_optional_object(payload, "outcome_contract"),
                set_outcome_contract="outcome_contract" in payload,
                integration_boundary=_optional_bool(
                    payload,
                    "integration_boundary",
                ),
                deliverable_contract=_optional_object(
                    payload,
                    "deliverable_contract",
                ),
                set_deliverable_contract="deliverable_contract" in payload,
                expected_revision=expected_revision,
            ),
            create_collection="tasks",
            create_id=task_id,
        )
        return _state_response(
            request,
            state,
            status_code=201,
            rebased_from_revision=rebased_from_revision,
            extra=_idle_context_rotation_outbox_publication_extra(
                context,
                state=state,
                agent_id=owner_agent_id,
            ),
        )

    async def patch_task(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        raw_state = _optional(payload, "state")
        task_id = request.path_params["task_id"]
        state, rebased_from_revision = _run_ui_rebasable_mutation(
            payload,
            lambda expected_revision: admin_service.update_task(
                task_id=task_id,
                priority=_optional_int(payload, "priority"),
                state=TeamTaskState(raw_state) if raw_state else None,
                title=_optional(payload, "title"),
                description=_optional(payload, "description"),
                case_id=_optional_present_text(payload, "case_id"),
                set_case_id="case_id" in payload,
                parent_task_id=_optional(payload, "parent_task_id"),
                set_parent_task_id="parent_task_id" in payload,
                authored_by=_optional_object(payload, "authored_by"),
                set_authored_by="authored_by" in payload,
                git_base_branch=_optional_present_text(payload, "git_base_branch"),
                set_git_base_branch="git_base_branch" in payload,
                dependency_task_ids=_optional_string_list(payload, "dependency_task_ids"),
                integration_boundary=_optional_bool(
                    payload,
                    "integration_boundary",
                ),
                deliverable_contract=_optional_object(
                    payload,
                    "deliverable_contract",
                ),
                set_deliverable_contract="deliverable_contract" in payload,
                expected_revision=expected_revision,
            ),
            target_collection="tasks",
            target_id=task_id,
        )
        task = state.tasks.get(task_id)
        return _state_response(
            request,
            state,
            publish_role_queue=True,
            rebased_from_revision=rebased_from_revision,
            extra=(
                _idle_context_rotation_outbox_publication_extra(
                    context,
                    state=state,
                    agent_id=task.owner_agent_id,
                )
                if task is not None
                else None
            ),
        )

    async def cancel_task(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        task_id = request.path_params["task_id"]
        rebind_dependents = _optional_bool(payload, "rebind_dependents") is True

        def _cancel(expected_revision: int) -> TeamState:
            return admin_service.cancel_task(
                task_id=task_id,
                reason_code=_required(payload, "reason_code"),
                reason=_required(payload, "reason"),
                actor_id=_operator_actor_from_request(context.auth, request),
                successor_task_id=_optional(payload, "successor_task_id"),
                rebind_dependents=rebind_dependents,
                expected_dependent_task_ids=_optional_string_list(
                    payload,
                    "expected_dependent_task_ids",
                ),
                expected_revision=expected_revision,
            )

        if rebind_dependents:
            current = repository.read()
            authorization_error = _task_project_owner_authorization_error(
                request,
                state=current,
                task_id=task_id,
                action="Task dependency rebinding",
            )
            if authorization_error is not None:
                return authorization_error
            # Graph rewrites never auto-rebase: the authorization and frozen
            # affected set both belong to this exact state revision.
            state = _cancel(_expected_revision(payload))
            rebased_from_revision = None
        else:
            state, rebased_from_revision = _run_ui_rebasable_mutation(
                payload,
                _cancel,
                target_collection="tasks",
                target_id=task_id,
            )
        return _state_response(
            request,
            state,
            rebased_from_revision=rebased_from_revision,
            extra=(
                _idle_context_rotation_outbox_publication_extra(
                    context,
                    state=state,
                    agent_id=state.tasks[task_id].owner_agent_id,
                )
                if task_id in state.tasks
                else None
            ),
        )

    async def repair_cancelled_task_dependencies(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        predecessor_task_id = request.path_params["task_id"]
        current = repository.read()
        authorization_error = _task_project_owner_authorization_error(
            request,
            state=current,
            task_id=predecessor_task_id,
            action="Cancelled-task dependency repair",
        )
        if authorization_error is not None:
            return authorization_error
        expected_dependent_task_ids = _optional_string_list(
            payload,
            "expected_dependent_task_ids",
        )
        if expected_dependent_task_ids is None:
            raise ValueError("expected_dependent_task_ids is required")
        state, result = admin_service.repair_cancelled_task_dependencies(
            predecessor_task_id=predecessor_task_id,
            successor_task_id=_required(payload, "successor_task_id"),
            expected_dependent_task_ids=expected_dependent_task_ids,
            reason=_required(payload, "reason"),
            actor_id=_operator_actor_from_request(context.auth, request),
            expected_revision=_expected_revision(payload),
        )
        return _state_response(
            request,
            state,
            extra={"task_dependency_repair": result},
        )

    async def recover_submitted_integration_boundary(
        request: Request,
    ) -> JSONResponse:
        payload = await _read_payload(request)
        integration_task_id = request.path_params["task_id"]
        current = repository.read()
        authorization_error = _task_project_owner_authorization_error(
            request,
            state=current,
            task_id=integration_task_id,
            action="Submitted integration-boundary recovery",
        )
        if authorization_error is not None:
            return authorization_error
        expected_dependency_task_ids = _optional_string_list(
            payload,
            "expected_dependency_task_ids",
        )
        expected_required_contribution_task_ids = _optional_string_list(
            payload,
            "expected_required_contribution_task_ids",
        )
        if expected_dependency_task_ids is None:
            raise ValueError("expected_dependency_task_ids is required")
        if expected_required_contribution_task_ids is None:
            raise ValueError("expected_required_contribution_task_ids is required")
        state, result = admin_service.recover_submitted_integration_boundary(
            predecessor_task_id=_required(payload, "predecessor_task_id"),
            successor_task_id=_required(payload, "successor_task_id"),
            integration_task_id=integration_task_id,
            replacement_task_id=_required(payload, "replacement_task_id"),
            expected_dependency_task_ids=expected_dependency_task_ids,
            expected_required_contribution_task_ids=(expected_required_contribution_task_ids),
            reason=_required(payload, "reason"),
            actor_id=_operator_actor_from_request(context.auth, request),
            git_base_commit_sha=_optional(payload, "git_base_commit_sha"),
            git_base_branch=_optional(payload, "git_base_branch"),
            expected_revision=_expected_revision(payload),
        )
        return _state_response(
            request,
            state,
            extra={"submitted_integration_boundary_recovery": result},
        )

    async def repair_task_state(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        task_id = request.path_params["task_id"]
        state, rebased_from_revision = _run_ui_rebasable_mutation(
            payload,
            lambda expected_revision: admin_service.repair_task_state(
                task_id=task_id,
                state=TeamTaskState(_required(payload, "state")),
                reason=_required(payload, "reason"),
                actor=_operator_actor_from_request(context.auth, request),
                reference_ids=_optional_string_list(payload, "reference_ids"),
                expected_revision=expected_revision,
            ),
            target_collection="tasks",
            target_id=task_id,
        )
        task = state.tasks.get(task_id)
        return _state_response(
            request,
            state,
            rebased_from_revision=rebased_from_revision,
            extra=(
                _idle_context_rotation_outbox_publication_extra(
                    context,
                    state=state,
                    agent_id=task.owner_agent_id,
                )
                if task is not None
                else None
            ),
        )

    return {
        "create_case": create_case,
        "patch_case": patch_case,
        "create_task": create_task,
        "patch_task": patch_task,
        "cancel_task": cancel_task,
        "repair_cancelled_task_dependencies": repair_cancelled_task_dependencies,
        "recover_submitted_integration_boundary": recover_submitted_integration_boundary,
        "repair_task_state": repair_task_state,
    }
