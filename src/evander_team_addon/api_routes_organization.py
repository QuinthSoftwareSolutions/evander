"""Team organization endpoints."""

from __future__ import annotations

from collections.abc import (
    Awaitable,
    Callable,
    Mapping,
)
from typing import (
    Any,
)

from starlette.concurrency import (
    run_in_threadpool,
)
from starlette.requests import (
    Request,
)
from starlette.responses import Response

from evander.admin.web.rbac import (
    ADMIN_OWNER_PERMISSION,
)

from .api_authorization import (
    _resolved_release_policy_for_admin,
)
from .api_constants import (
    JSONResponse,
)
from .api_context import TeamApiDependencies
from .api_requests import (
    _auth_can,
    _expected_revision,
    _operator_actor_from_request,
    _optional,
    _optional_bool,
    _optional_git_config,
    _optional_list,
    _read_payload,
    _required,
    _session_repository_from_context,
)
from .chat_attachments import (
    normalize_directory_path,
)
from .models import (
    GovernanceAgendaItemType,
    ProjectGoal,
    ProjectStatus,
    TeamInboxPriority,
)
from .provider_runtime import (
    _session_records_by_id,
)


def build_organization_handlers(
    dependencies: TeamApiDependencies,
) -> dict[str, Callable[[Request], Awaitable[Response]]]:
    context = dependencies.context
    repository = dependencies.repository
    admin_service = dependencies.admin_service
    _state_response = dependencies._state_response
    _run_ui_rebasable_mutation = dependencies._run_ui_rebasable_mutation
    _directory_owner_authorization_error = dependencies._directory_owner_authorization_error

    async def create_team(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        team_id = _optional(payload, "id") or _optional(payload, "team_id")
        state, rebased_from_revision = _run_ui_rebasable_mutation(
            payload,
            lambda expected_revision: admin_service.create_team(
                team_id=team_id,
                name=_required(payload, "name"),
                manager_agent_id=_optional(payload, "manager_agent_id"),
                manager_session_id=_required(payload, "manager_session_id"),
                manager_display_name=_optional(payload, "manager_display_name"),
                manager_role_id=_optional(payload, "manager_role_id"),
                manager_role_name=_optional(payload, "manager_role_name"),
                operator_communication_target_id=_optional(
                    payload, "operator_communication_target_id"
                ),
                escalate_to_governance=bool(
                    _optional_bool(payload, "escalate_to_governance") or False
                ),
                max_cost_usd=payload.get("max_cost_usd") if "max_cost_usd" in payload else None,
                cost_warning_ratio=(
                    payload.get("cost_warning_ratio") if "cost_warning_ratio" in payload else None
                ),
                cost_budget_renewal_mode=(
                    payload.get("cost_budget_renewal_mode")
                    if "cost_budget_renewal_mode" in payload
                    else None
                ),
                expected_revision=expected_revision,
            ),
            create_collection="teams",
            create_id=team_id,
        )
        return _state_response(
            request,
            state,
            status_code=201,
            rebased_from_revision=rebased_from_revision,
        )

    async def copy_team(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        source_team_id = request.path_params["team_id"]
        current_state = repository.read()
        source_team = current_state.teams.get(source_team_id)
        if source_team is None:
            return JSONResponse({"error": "source team not found"}, status_code=404)
        for directory_path in sorted(
            {
                project.directory_path
                for project in current_state.projects.values()
                if project.team_id == source_team_id
                and project.status in {ProjectStatus.ACTIVE, ProjectStatus.BLOCKED}
            }
        ):
            authorization_error = _directory_owner_authorization_error(
                request,
                directory_path=directory_path,
                action="Team copy",
            )
            if authorization_error is not None:
                return authorization_error

        raw_agent_session_ids = payload.get("agent_session_ids")
        if not isinstance(raw_agent_session_ids, Mapping):
            raise ValueError("agent_session_ids must be a JSON object")
        requested_session_ids = {
            _required(raw_agent_session_ids, source_agent_id)
            for source_agent_id in raw_agent_session_ids
        }
        session_records = _session_records_by_id(_session_repository_from_context(context))
        if session_records is None:
            raise ValueError("core session repository is unavailable")
        missing_session_ids = sorted(requested_session_ids - set(session_records))
        if missing_session_ids:
            raise ValueError("core sessions were not found: " + ", ".join(missing_session_ids))
        for session_id in sorted(requested_session_ids):
            session_record = session_records[session_id]
            raw_directory_path = (
                session_record.get("directory_path")
                if isinstance(session_record, Mapping)
                else getattr(session_record, "directory_path", None)
            )
            if not raw_directory_path:
                raise ValueError(f"core session {session_id} has no resolvable directory_path")
            session_directory_path = normalize_directory_path(str(raw_directory_path))
            if not _auth_can(
                context.auth,
                request,
                ADMIN_OWNER_PERMISSION,
                directory_path=session_directory_path,
            ):
                return JSONResponse(
                    {
                        "error": (
                            "Team copy requires directory owner permission for every "
                            "destination session."
                        )
                    },
                    status_code=403,
                )

        state, result = admin_service.copy_team(
            source_team_id=source_team_id,
            team_id=_optional(payload, "id") or _optional(payload, "team_id"),
            name=_required(payload, "name"),
            agent_session_ids=raw_agent_session_ids,
            expected_revision=_expected_revision(payload),
        )
        return _state_response(
            request,
            state,
            status_code=201,
            extra={"copied": result},
        )

    async def patch_team(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        raw_archived = payload.get("archived")
        team_id = request.path_params["team_id"]
        state, rebased_from_revision = _run_ui_rebasable_mutation(
            payload,
            lambda expected_revision: admin_service.update_team(
                team_id=team_id,
                name=_optional(payload, "name"),
                manager_agent_id=_optional(payload, "manager_agent_id"),
                operator_communication_target_id=_optional(
                    payload, "operator_communication_target_id"
                ),
                set_operator_communication_target_id="operator_communication_target_id" in payload,
                escalate_to_governance=_optional_bool(payload, "escalate_to_governance"),
                max_cost_usd=payload.get("max_cost_usd") if "max_cost_usd" in payload else None,
                set_max_cost_usd="max_cost_usd" in payload,
                cost_warning_ratio=(
                    payload.get("cost_warning_ratio") if "cost_warning_ratio" in payload else None
                ),
                set_cost_warning_ratio="cost_warning_ratio" in payload,
                cost_budget_renewal_mode=(
                    payload.get("cost_budget_renewal_mode")
                    if "cost_budget_renewal_mode" in payload
                    else None
                ),
                set_cost_budget_renewal_mode="cost_budget_renewal_mode" in payload,
                archived=raw_archived if isinstance(raw_archived, bool) else None,
                expected_revision=expected_revision,
            ),
            target_collection="teams",
            target_id=team_id,
        )
        return _state_response(request, state, rebased_from_revision=rebased_from_revision)

    async def reset_team_cost_budget(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        state = admin_service.reset_team_cost_budget(
            team_id=request.path_params["team_id"],
            reason=_required(payload, "reason"),
            operator_actor_id=_operator_actor_from_request(context.auth, request),
            expected_revision=_expected_revision(payload),
        )
        return _state_response(request, state)

    async def create_governance_group(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        governance_group_id = _optional(payload, "id") or _optional(payload, "governance_group_id")
        state, rebased_from_revision = _run_ui_rebasable_mutation(
            payload,
            lambda expected_revision: admin_service.create_governance_group(
                governance_group_id=governance_group_id,
                name=_required(payload, "name"),
                manager_agent_id=_required(payload, "manager_agent_id"),
                member_team_ids=list(_optional_list(payload, "member_team_ids")),
                goals=list(_optional_list(payload, "goals")),
                max_cost_usd=payload.get("max_cost_usd") if "max_cost_usd" in payload else None,
                expected_revision=expected_revision,
            ),
            create_collection="governance_groups",
            create_id=governance_group_id,
        )
        return _state_response(
            request,
            state,
            status_code=201,
            rebased_from_revision=rebased_from_revision,
        )

    async def patch_governance_group(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        raw_archived = payload.get("archived")
        goals = list(_optional_list(payload, "goals")) if "goals" in payload else None
        governance_group_id = request.path_params["governance_group_id"]
        state, rebased_from_revision = _run_ui_rebasable_mutation(
            payload,
            lambda expected_revision: admin_service.update_governance_group(
                governance_group_id=governance_group_id,
                name=_optional(payload, "name"),
                manager_agent_id=_optional(payload, "manager_agent_id"),
                member_team_ids=list(_optional_list(payload, "member_team_ids")),
                set_member_team_ids="member_team_ids" in payload,
                goals=goals,
                max_cost_usd=payload.get("max_cost_usd") if "max_cost_usd" in payload else None,
                set_max_cost_usd="max_cost_usd" in payload,
                archived=raw_archived if isinstance(raw_archived, bool) else None,
                expected_revision=expected_revision,
            ),
            target_collection="governance_groups",
            target_id=governance_group_id,
        )
        return _state_response(request, state, rebased_from_revision=rebased_from_revision)

    async def create_governance_agenda_item(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        raw_priority = _optional(payload, "priority") or TeamInboxPriority.NORMAL.value
        agenda_item_id = _optional(payload, "id") or _optional(payload, "agenda_item_id")
        state, rebased_from_revision = _run_ui_rebasable_mutation(
            payload,
            lambda expected_revision: admin_service.create_governance_agenda_item(
                agenda_item_id=agenda_item_id,
                governance_group_id=_required(payload, "governance_group_id"),
                item_type=GovernanceAgendaItemType(_required(payload, "item_type")),
                title=_required(payload, "title"),
                summary=_required(payload, "summary"),
                team_id=_optional(payload, "team_id"),
                project_id=_optional(payload, "project_id"),
                agent_id=_optional(payload, "agent_id"),
                payload=payload.get("payload")
                if isinstance(payload.get("payload"), Mapping)
                else None,
                priority=TeamInboxPriority(raw_priority),
                expected_revision=expected_revision,
            ),
            create_collection="governance_agenda",
            create_id=agenda_item_id,
        )
        return _state_response(
            request,
            state,
            status_code=201,
            rebased_from_revision=rebased_from_revision,
        )

    async def handle_governance_agenda_item(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        agenda_item_id = request.path_params["agenda_item_id"]
        state, rebased_from_revision = _run_ui_rebasable_mutation(
            payload,
            lambda expected_revision: admin_service.mark_governance_agenda_handled(
                agenda_item_id=agenda_item_id,
                expected_revision=expected_revision,
            ),
            target_collection="governance_agenda",
            target_id=agenda_item_id,
        )
        return _state_response(request, state, rebased_from_revision=rebased_from_revision)

    async def create_project(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        goals = list(_optional_list(payload, "goals"))
        git_config = _optional_git_config(payload)
        directory_path = normalize_directory_path(
            _optional(payload, "directory_path")
            or (git_config.directory_path if git_config is not None else "root")
        )
        authorization_error = _directory_owner_authorization_error(
            request,
            directory_path=directory_path,
            action="Project creation",
        )
        if authorization_error is not None:
            return authorization_error
        release_policy = _resolved_release_policy_for_admin(
            context.auth,
            payload,
            directory_path=directory_path,
        )
        lead_agent_id = _optional(payload, "lead_agent_id")
        lead_session_id = _optional(payload, "lead_session_id")
        if lead_session_id is None:
            lead_agent_id = _required(payload, "lead_agent_id")
        project_id = _optional(payload, "id") or _optional(payload, "project_id")
        state, rebased_from_revision = _run_ui_rebasable_mutation(
            payload,
            lambda expected_revision: admin_service.create_project(
                project_id=project_id,
                team_id=_required(payload, "team_id"),
                name=_required(payload, "name"),
                lead_agent_id=lead_agent_id,
                lead_session_id=lead_session_id,
                lead_display_name=_optional(payload, "lead_display_name"),
                lead_role_id=_optional(payload, "lead_role_id"),
                lead_role_name=_optional(payload, "lead_role_name"),
                goals=goals,
                directory_path=directory_path,
                git=git_config,
                release_policy=release_policy,
                expected_revision=expected_revision,
            ),
            create_collection="projects",
            create_id=project_id,
        )
        return _state_response(
            request,
            state,
            status_code=201,
            rebased_from_revision=rebased_from_revision,
        )

    async def patch_project(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        has_git = "git" in payload
        has_directory_path = "directory_path" in payload
        has_release_policy = "release_policy" in payload
        git_config = _optional_git_config(payload)
        has_goals = "goals" in payload
        goals = (
            [ProjectGoal.model_validate(item) for item in _optional_list(payload, "goals")]
            if has_goals
            else None
        )
        raw_status = _optional(payload, "status")
        project_id = request.path_params["project_id"]
        current_project = repository.read().projects.get(project_id)
        effective_git = (
            git_config
            if has_git
            else (current_project.git if current_project is not None else None)
        )
        effective_directory_path = normalize_directory_path(
            _optional(payload, "directory_path")
            or (
                effective_git.directory_path
                if has_git and effective_git is not None
                else (current_project.directory_path if current_project is not None else "root")
            )
        )
        if current_project is None:
            return JSONResponse({"error": "project not found"}, status_code=404)
        authorization_paths = list(
            dict.fromkeys([current_project.directory_path, effective_directory_path])
        )
        for authorization_path in authorization_paths:
            authorization_error = _directory_owner_authorization_error(
                request,
                directory_path=authorization_path,
                action="Project update",
            )
            if authorization_error is not None:
                return authorization_error
        release_policy_payload: Mapping[str, Any] = payload
        re_resolve_existing_release_policy = (
            (has_git or has_directory_path)
            and not has_release_policy
            and current_project is not None
            and current_project.release_policy is not None
        )
        if re_resolve_existing_release_policy:
            release_policy_payload = {
                **payload,
                "release_policy": current_project.release_policy.model_dump(mode="python"),
            }
        release_policy = _resolved_release_policy_for_admin(
            context.auth,
            release_policy_payload,
            directory_path=effective_directory_path,
        )
        state, rebased_from_revision = _run_ui_rebasable_mutation(
            payload,
            lambda expected_revision: admin_service.update_project(
                project_id=project_id,
                name=_optional(payload, "name"),
                directory_path=effective_directory_path,
                set_directory_path=(has_directory_path or (has_git and effective_git is not None)),
                git=git_config,
                set_git=has_git,
                release_policy=release_policy,
                set_release_policy=(has_release_policy or re_resolve_existing_release_policy),
                goals=goals,
                status=ProjectStatus(raw_status) if raw_status else None,
                expected_revision=expected_revision,
            ),
            target_collection="projects",
            target_id=project_id,
        )
        return _state_response(request, state, rebased_from_revision=rebased_from_revision)

    async def transfer_project_lead(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        project_id = request.path_params["project_id"]
        state, rebased_from_revision = _run_ui_rebasable_mutation(
            payload,
            lambda expected_revision: admin_service.transfer_project_lead(
                project_id=project_id,
                lead_agent_id=_required(payload, "lead_agent_id"),
                expected_revision=expected_revision,
            ),
            target_collection="projects",
            target_id=project_id,
        )
        return _state_response(request, state, rebased_from_revision=rebased_from_revision)

    async def import_project_git_repo(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        service = getattr(context, "service", None)
        import_git_repo = getattr(service, "import_git_repo", None)
        if not callable(import_git_repo):
            return JSONResponse({"error": "Git repo import is unavailable"}, status_code=503)
        try:
            repo_source = _required(payload, "repo_source")
            directory_path = _optional(payload, "directory_path") or "root"
            imported = await run_in_threadpool(
                lambda: import_git_repo(
                    repo_source=repo_source,
                    directory_path=directory_path,
                )
            )
        except RuntimeError as exc:
            status_code = int(getattr(exc, "status_code", 503))
            return JSONResponse({"error": str(exc)}, status_code=status_code)
        return JSONResponse({"imported": imported, "git": imported.get("git")}, status_code=201)

    return {
        "create_team": create_team,
        "copy_team": copy_team,
        "patch_team": patch_team,
        "reset_team_cost_budget": reset_team_cost_budget,
        "create_governance_group": create_governance_group,
        "patch_governance_group": patch_governance_group,
        "create_governance_agenda_item": create_governance_agenda_item,
        "handle_governance_agenda_item": handle_governance_agenda_item,
        "create_project": create_project,
        "patch_project": patch_project,
        "transfer_project_lead": transfer_project_lead,
        "import_project_git_repo": import_project_git_repo,
    }
