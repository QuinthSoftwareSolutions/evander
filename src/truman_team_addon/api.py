from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from pydantic import ValidationError
from starlette.datastructures import UploadFile
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Route

from agent_sandbox.addons import AddonContext
from agent_sandbox.events.internal import (
    InternalEventBus,
    InternalEventSource,
    internal_event_store_path,
)

from .avatars import (
    AVATAR_MIME_TYPE,
    MAX_AVATAR_UPLOAD_BYTES,
    AvatarStorage,
    build_avatar_prompt,
    normalize_prompt_note,
    normalize_style_preset,
    openai_avatar_generator_from_config,
)
from .models import (
    AgentStatus,
    EphemeralAgentPolicy,
    ProjectGitConfig,
    ProjectGoal,
    ProjectStatus,
    ReviewProfileCriterion,
    TeamRecordState,
    TeamTaskState,
    team_state_payload,
)
from .costs import TeamCostService
from .templates import get_template, template_catalog
from .timeline import derive_team_timeline
from .repository import TeamStateConflictError, TeamStateRepository


def admin_api_routes(context: AddonContext) -> Sequence[Route]:
    repository = TeamStateRepository(context.runtime_dir)
    avatar_storage = AvatarStorage(context.runtime_dir)

    async def get_state(_request: Request) -> JSONResponse:
        return JSONResponse(team_state_payload(repository.read()))

    async def list_templates(_request: Request) -> JSONResponse:
        return JSONResponse(
            {
                "templates": [
                    template.model_dump(mode="json") for template in template_catalog()
                ]
            }
        )

    async def get_template_details(request: Request) -> JSONResponse:
        template = get_template(request.path_params["template_id"])
        if template is None:
            return JSONResponse({"error": "template not found"}, status_code=404)
        return JSONResponse(template.model_dump(mode="json"))

    async def materialize_template(request: Request) -> JSONResponse:
        template_id = request.path_params["template_id"]
        if get_template(template_id) is None:
            return JSONResponse({"error": "template not found"}, status_code=404)
        payload = await _read_payload(request)
        state, result = repository.materialize_template(
            template_id,
            inputs=payload,
            expected_revision=_expected_revision(payload),
        )
        response_payload = team_state_payload(state)
        response_payload["materialized"] = result
        return JSONResponse(response_payload, status_code=201)

    async def get_timeline(request: Request) -> JSONResponse:
        state = repository.read()
        team_id = _query_text(request, "team_id")
        project_id = _query_text(request, "project_id")
        agent_id = _query_text(request, "agent_id")
        types = _query_values(request, "type")
        statuses = _query_values(request, "status")
        attention_needed = _query_bool(request, "attention_needed")
        timeline = derive_team_timeline(
            state,
            team_id=team_id,
            project_id=project_id,
            agent_id=agent_id,
            types=types,
            statuses=statuses,
            attention_needed=attention_needed,
        )
        filters: dict[str, object] = {}
        if team_id is not None:
            filters["team_id"] = team_id
        if project_id is not None:
            filters["project_id"] = project_id
        if agent_id is not None:
            filters["agent_id"] = agent_id
        if types is not None:
            filters["type"] = types
        if statuses is not None:
            filters["status"] = statuses
        if attention_needed is not None:
            filters["attention_needed"] = attention_needed
        payload = {
            **timeline,
            "source": "derived_from_current_team_state",
            "description": "Derived timeline from the current TeamState snapshot.",
            "filters": filters,
        }
        return JSONResponse(payload)

    async def get_costs(request: Request) -> JSONResponse:
        state = repository.read()
        backfill = _query_bool(request, "backfill")
        service = TeamCostService(
            runtime_dir=context.runtime_dir,
            state=state,
            session_repository=_session_repository_from_context(context),
        )
        return JSONResponse(service.costs_payload(backfill_transcripts=backfill is not False))

    async def create_team(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        state = repository.create_team(
            team_id=_optional(payload, "id") or _optional(payload, "team_id"),
            name=_required(payload, "name"),
            manager_agent_id=_optional(payload, "manager_agent_id"),
            manager_session_id=_required(payload, "manager_session_id"),
            manager_display_name=_optional(payload, "manager_display_name"),
            manager_role_id=_optional(payload, "manager_role_id"),
            manager_role_name=_optional(payload, "manager_role_name"),
            operator_communication_target_id=_optional(
                payload, "operator_communication_target_id"
            ),
            max_cost_usd=payload.get("max_cost_usd") if "max_cost_usd" in payload else None,
            expected_revision=_expected_revision(payload),
        )
        return JSONResponse(team_state_payload(state), status_code=201)

    async def patch_team(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        raw_archived = payload.get("archived")
        state = repository.update_team(
            team_id=request.path_params["team_id"],
            name=_optional(payload, "name"),
            operator_communication_target_id=_optional(
                payload, "operator_communication_target_id"
            ),
            set_operator_communication_target_id="operator_communication_target_id" in payload,
            max_cost_usd=payload.get("max_cost_usd") if "max_cost_usd" in payload else None,
            set_max_cost_usd="max_cost_usd" in payload,
            archived=raw_archived if isinstance(raw_archived, bool) else None,
            expected_revision=_expected_revision(payload),
        )
        return JSONResponse(team_state_payload(state))

    async def create_project(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        goals = list(_optional_list(payload, "goals"))
        state = repository.create_project(
            project_id=_optional(payload, "id") or _optional(payload, "project_id"),
            team_id=_required(payload, "team_id"),
            name=_required(payload, "name"),
            lead_agent_id=_optional(payload, "lead_agent_id"),
            lead_session_id=_required(payload, "lead_session_id"),
            lead_display_name=_optional(payload, "lead_display_name"),
            lead_role_id=_optional(payload, "lead_role_id"),
            lead_role_name=_optional(payload, "lead_role_name"),
            goals=goals,
            git=_optional_git_config(payload),
            expected_revision=_expected_revision(payload),
        )
        return JSONResponse(team_state_payload(state), status_code=201)

    async def patch_project(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        has_git = "git" in payload
        has_goals = "goals" in payload
        goals = (
            [ProjectGoal.model_validate(item) for item in _optional_list(payload, "goals")]
            if has_goals
            else None
        )
        raw_status = _optional(payload, "status")
        state = repository.update_project(
            project_id=request.path_params["project_id"],
            name=_optional(payload, "name"),
            git=_optional_git_config(payload),
            set_git=has_git,
            goals=goals,
            status=ProjectStatus(raw_status) if raw_status else None,
            expected_revision=_expected_revision(payload),
        )
        return JSONResponse(team_state_payload(state))

    async def create_role(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        ephemeral_policy = payload.get("ephemeral_policy")
        state = repository.create_role(
            role_id=_optional(payload, "id") or _optional(payload, "role_id"),
            team_id=_required(payload, "team_id"),
            project_id=_optional(payload, "project_id"),
            name=_required(payload, "name"),
            description=_optional(payload, "description"),
            permissions=_optional_string_list(payload, "permissions"),
            ephemeral_policy=(
                EphemeralAgentPolicy.model_validate(ephemeral_policy)
                if ephemeral_policy is not None
                else None
            ),
            expected_revision=_expected_revision(payload),
        )
        return JSONResponse(team_state_payload(state), status_code=201)

    async def patch_role(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        ephemeral_policy = payload.get("ephemeral_policy")
        raw_archived = payload.get("archived")
        state = repository.update_role(
            role_id=request.path_params["role_id"],
            name=_optional(payload, "name"),
            project_id=_optional(payload, "project_id"),
            set_project_id="project_id" in payload,
            description=_optional_present_text(payload, "description"),
            set_description="description" in payload,
            permissions=(
                _optional_string_list(payload, "permissions")
                if "permissions" in payload
                else None
            ),
            ephemeral_policy=(
                EphemeralAgentPolicy.model_validate(ephemeral_policy)
                if ephemeral_policy is not None
                else None
            ),
            archived=raw_archived if isinstance(raw_archived, bool) else None,
            expected_revision=_expected_revision(payload),
        )
        return JSONResponse(team_state_payload(state))

    async def list_roles(_request: Request) -> JSONResponse:
        state = repository.read()
        return JSONResponse(
            {
                "revision": state.revision,
                "roles": [role.model_dump(mode="json") for role in state.roles.values()],
            }
        )

    async def get_role(request: Request) -> JSONResponse:
        state = repository.read()
        role = state.roles.get(request.path_params["role_id"])
        if role is None:
            return JSONResponse({"error": "role not found"}, status_code=404)
        return JSONResponse(role.model_dump(mode="json"))

    async def register_agent(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        ephemeral_policy = payload.get("ephemeral_policy")
        state = repository.register_agent(
            agent_id=_optional(payload, "id") or _optional(payload, "agent_id"),
            team_id=_required(payload, "team_id"),
            project_id=_required(payload, "project_id"),
            role_id=_required(payload, "role_id"),
            session_id=_required(payload, "session_id"),
            reports_to_agent_id=_required(payload, "reports_to_agent_id"),
            display_name=_optional(payload, "display_name"),
            ephemeral_policy=(
                EphemeralAgentPolicy.model_validate(ephemeral_policy)
                if ephemeral_policy is not None
                else None
            ),
            expected_revision=_expected_revision(payload),
        )
        return JSONResponse(team_state_payload(state), status_code=201)

    async def patch_agent(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        ephemeral_policy = payload.get("ephemeral_policy")
        raw_status = _optional(payload, "status")
        state = repository.update_agent(
            agent_id=request.path_params["agent_id"],
            project_id=_optional(payload, "project_id"),
            set_project_id="project_id" in payload,
            role_id=_optional(payload, "role_id"),
            session_id=_optional(payload, "session_id"),
            reports_to_agent_id=_optional(payload, "reports_to_agent_id"),
            set_reports_to_agent_id="reports_to_agent_id" in payload,
            display_name=_optional(payload, "display_name"),
            set_display_name="display_name" in payload,
            ephemeral_policy=(
                EphemeralAgentPolicy.model_validate(ephemeral_policy)
                if ephemeral_policy is not None
                else None
            ),
            status=AgentStatus(raw_status) if raw_status else None,
            auto_context_clearing_enabled=_optional_bool(
                payload,
                "auto_context_clearing_enabled",
            ),
            set_auto_context_clearing_enabled=(
                "auto_context_clearing_enabled" in payload
            ),
            expected_revision=_expected_revision(payload),
        )
        return JSONResponse(team_state_payload(state))

    async def create_avatar_preview(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        state = repository.read()
        agent_id = request.path_params["agent_id"]
        agent = state.agents.get(agent_id)
        if agent is None:
            return JSONResponse({"error": "agent not found"}, status_code=404)
        role = state.roles.get(agent.role_id)
        team = state.teams.get(agent.team_id)
        style_preset = normalize_style_preset(payload.get("style_preset"))
        prompt_note = normalize_prompt_note(payload.get("prompt_note"))
        prompt = build_avatar_prompt(
            agent=agent,
            role_name=role.name if role is not None else None,
            team_name=team.name if team is not None else None,
            style_preset=style_preset,
            prompt_note=prompt_note,
        )
        generator = openai_avatar_generator_from_config(context.config)
        try:
            content = await generator.generate(prompt=prompt)
        except RuntimeError as exc:
            return JSONResponse({"error": str(exc)}, status_code=503)
        try:
            preview = avatar_storage.store_generated_preview(
                agent_id=agent.id,
                content=content,
                style_preset=style_preset,
                prompt_note=prompt_note,
                model=generator.model,
            )
        except RuntimeError as exc:
            return JSONResponse({"error": str(exc)}, status_code=503)
        return JSONResponse({"preview": preview.payload()}, status_code=201)

    async def upload_agent_avatar(request: Request) -> JSONResponse:
        try:
            form = await request.form()
        except Exception as exc:
            raise ValueError("Request body must be multipart form data") from exc
        expected_revision = _coerce_int(form.get("expected_revision"), "expected_revision")
        file_value = form.get("file")
        if not isinstance(file_value, UploadFile):
            raise ValueError("file is required")
        content = await file_value.read(MAX_AVATAR_UPLOAD_BYTES + 1)
        try:
            avatar = avatar_storage.store_upload(content)
        except RuntimeError as exc:
            return JSONResponse({"error": str(exc)}, status_code=503)
        old_asset_id = _current_agent_avatar_asset_id(
            repository.read(),
            request.path_params["agent_id"],
        )
        try:
            state = repository.set_agent_avatar(
                agent_id=request.path_params["agent_id"],
                avatar=avatar,
                expected_revision=expected_revision,
            )
        except Exception:
            avatar_storage.delete_asset_if_unreferenced(
                asset_id=avatar.asset_id,
                state=repository.read(),
            )
            raise
        avatar_storage.delete_asset_if_unreferenced(asset_id=old_asset_id, state=state)
        return JSONResponse(team_state_payload(state))

    async def accept_agent_avatar_preview(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        expected_revision = _expected_revision(payload)
        agent_id = request.path_params["agent_id"]
        before = repository.read()
        if agent_id not in before.agents:
            return JSONResponse({"error": "agent not found"}, status_code=404)
        if before.revision != expected_revision:
            raise TeamStateConflictError(
                expected_revision=expected_revision,
                current_revision=before.revision,
            )
        old_asset_id = _current_agent_avatar_asset_id(before, agent_id)
        try:
            avatar = avatar_storage.avatar_from_preview(
                preview_id=_required(payload, "preview_id"),
                agent_id=agent_id,
            )
        except FileNotFoundError:
            return JSONResponse({"error": "avatar preview not found"}, status_code=404)
        state = repository.set_agent_avatar(
            agent_id=agent_id,
            avatar=avatar,
            expected_revision=expected_revision,
        )
        avatar_storage.promote_preview(preview_id=avatar.preview_id or avatar.asset_id)
        avatar_storage.delete_asset_if_unreferenced(asset_id=old_asset_id, state=state)
        return JSONResponse(team_state_payload(state))

    async def delete_agent_avatar(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        old_asset_id = _current_agent_avatar_asset_id(
            repository.read(),
            request.path_params["agent_id"],
        )
        state = repository.clear_agent_avatar(
            agent_id=request.path_params["agent_id"],
            expected_revision=_expected_revision(payload),
        )
        avatar_storage.delete_asset_if_unreferenced(asset_id=old_asset_id, state=state)
        return JSONResponse(team_state_payload(state))

    async def get_avatar_asset(request: Request) -> FileResponse | JSONResponse:
        try:
            path = avatar_storage.asset_path(request.path_params["asset_id"])
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        if not path.is_file():
            return JSONResponse({"error": "avatar not found"}, status_code=404)
        return FileResponse(path, media_type=AVATAR_MIME_TYPE)

    async def get_avatar_preview(request: Request) -> FileResponse | JSONResponse:
        try:
            path = avatar_storage.preview_path(request.path_params["preview_id"])
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        if not path.is_file():
            return JSONResponse({"error": "avatar preview not found"}, status_code=404)
        return FileResponse(path, media_type=AVATAR_MIME_TYPE)

    async def request_agent_context_rotation(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        state = repository.request_agent_context_rotation(
            agent_id=request.path_params["agent_id"],
            requested_by=_optional(payload, "requested_by")
            or _operator_actor_from_request(context.auth, request),
            request_note=_optional(payload, "reason"),
            expected_revision=_expected_revision(payload),
        )
        return JSONResponse(team_state_payload(state))

    async def stand_down_agent(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        state = repository.stand_down_agent(
            agent_id=request.path_params["agent_id"],
            reason=_required(payload, "reason"),
            set_by=_optional(payload, "set_by")
            or _operator_actor_from_request(context.auth, request),
            expected_revision=_expected_revision(payload),
        )
        return JSONResponse(team_state_payload(state), status_code=201)

    async def revoke_agent_stand_down(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        state = repository.revoke_operator_stand_downs(
            agent_id=request.path_params["agent_id"],
            revoked_by=_optional(payload, "revoked_by")
            or _operator_actor_from_request(context.auth, request),
            expected_revision=_expected_revision(payload),
        )
        return JSONResponse(team_state_payload(state))

    async def create_task(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        raw_state = _optional(payload, "state")
        created_by_agent_id = _optional(payload, "created_by_agent_id")
        state = repository.create_or_reprioritize_task(
            task_id=_optional(payload, "id") or _optional(payload, "task_id"),
            owner_agent_id=_required(payload, "owner_agent_id"),
            project_id=_required(payload, "project_id"),
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
            set_parent_task_id="parent_task_id" in payload,
            dependency_task_ids=_optional_string_list(payload, "dependency_task_ids"),
            expected_revision=_expected_revision(payload),
        )
        return JSONResponse(team_state_payload(state), status_code=201)

    async def patch_task(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        raw_state = _optional(payload, "state")
        state = repository.update_task(
            task_id=request.path_params["task_id"],
            priority=_optional_int(payload, "priority"),
            state=TeamTaskState(raw_state) if raw_state else None,
            title=_optional(payload, "title"),
            description=_optional(payload, "description"),
            parent_task_id=_optional(payload, "parent_task_id"),
            set_parent_task_id="parent_task_id" in payload,
            dependency_task_ids=_optional_string_list(payload, "dependency_task_ids"),
            expected_revision=_expected_revision(payload),
        )
        return JSONResponse(team_state_payload(state))

    async def record_deliverable(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        raw_state = _optional(payload, "state")
        state = repository.record_deliverable(
            deliverable_id=_optional(payload, "id") or _optional(payload, "deliverable_id"),
            task_id=_required(payload, "task_id"),
            producer_agent_id=_required(payload, "producer_agent_id"),
            summary=_required(payload, "summary"),
            artifact_refs=_optional_string_list(payload, "artifact_refs"),
            state=TeamRecordState(raw_state) if raw_state else TeamRecordState.PENDING,
            expected_revision=_expected_revision(payload),
        )
        return JSONResponse(team_state_payload(state), status_code=201)

    async def record_review_request(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        raw_state = _optional(payload, "state")
        raw_decision_at = _optional(payload, "decision_at")
        state = repository.record_review_request(
            review_request_id=_optional(payload, "id") or _optional(payload, "review_request_id"),
            deliverable_id=_required(payload, "deliverable_id"),
            reviewer_agent_id=_required(payload, "reviewer_agent_id"),
            state=TeamRecordState(raw_state) if raw_state else TeamRecordState.PENDING,
            findings=_optional_string_list(payload, "findings"),
            review_profile_id=_optional(payload, "review_profile_id"),
            instructions=_optional(payload, "instructions"),
            decision_at=datetime.fromisoformat(raw_decision_at) if raw_decision_at else None,
            expected_revision=_expected_revision(payload),
        )
        return JSONResponse(team_state_payload(state), status_code=201)

    async def decide_review_request(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        criteria = (
            _optional_list(payload, "criteria") if payload.get("criteria") is not None else None
        )
        state = repository.record_review_decision(
            review_request_id=request.path_params["review_request_id"],
            state=TeamRecordState(_required(payload, "state")),
            findings=_optional_string_list(payload, "findings"),
            criteria=criteria,
            expected_revision=_expected_revision(payload),
        )
        return JSONResponse(team_state_payload(state))

    async def create_operator_directive(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        created_by = _optional(payload, "created_by") or _operator_actor_from_request(
            context.auth,
            request,
        )
        state, item = repository.create_operator_directive(
            team_id=_required(payload, "team_id"),
            message=_required(payload, "message"),
            recipient_agent_id=_optional(payload, "recipient_agent_id"),
            project_id=_optional(payload, "project_id"),
            task_id=_optional(payload, "task_id"),
            title=_optional(payload, "title"),
            source=_optional(payload, "source"),
            source_message_id=_optional(payload, "source_message_id"),
            created_by=created_by,
            requesting_session_id=_optional(payload, "requesting_session_id"),
            expected_revision=_expected_revision(payload),
        )
        response_payload = team_state_payload(state)
        response_payload["operator_directive"] = item.model_dump(mode="json")
        return JSONResponse(response_payload, status_code=201)

    async def dismiss_action_record(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        state = repository.dismiss_action_record_attention(
            action_record_id=request.path_params["action_record_id"],
            dismissed_by=_optional(payload, "dismissed_by"),
            dismissed_reason=_optional(payload, "dismissed_reason"),
            expected_revision=_expected_revision(payload),
        )
        return JSONResponse(team_state_payload(state))

    async def create_review_profile(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        criteria = [
            ReviewProfileCriterion.model_validate(item)
            for item in _optional_list(payload, "criteria")
        ]
        state = repository.create_review_profile(
            profile_id=_optional(payload, "id") or _optional(payload, "review_profile_id"),
            team_id=_required(payload, "team_id"),
            project_id=_optional(payload, "project_id"),
            name=_required(payload, "name"),
            description=_optional(payload, "description") or "",
            applicable_role_ids=_optional_string_list(payload, "applicable_role_ids"),
            criteria=criteria,
            expected_revision=_expected_revision(payload),
        )
        return JSONResponse(team_state_payload(state), status_code=201)

    async def patch_review_profile(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        has_project = "project_id" in payload
        has_criteria = "criteria" in payload
        criteria = (
            [
                ReviewProfileCriterion.model_validate(item)
                for item in _optional_list(payload, "criteria")
            ]
            if has_criteria
            else None
        )
        raw_archived = payload.get("archived")
        state = repository.update_review_profile(
            profile_id=request.path_params["review_profile_id"],
            name=_optional(payload, "name"),
            description=_optional_present_text(payload, "description"),
            applicable_role_ids=(
                _optional_string_list(payload, "applicable_role_ids")
                if "applicable_role_ids" in payload
                else None
            ),
            criteria=criteria,
            archived=raw_archived if isinstance(raw_archived, bool) else None,
            project_id=_optional(payload, "project_id"),
            set_project_id=has_project,
            expected_revision=_expected_revision(payload),
        )
        return JSONResponse(team_state_payload(state))

    async def list_review_profiles(_request: Request) -> JSONResponse:
        state = repository.read()
        return JSONResponse(
            {
                "revision": state.revision,
                "review_profiles": [
                    profile.model_dump(mode="json")
                    for profile in state.review_profiles.values()
                ],
            }
        )

    async def get_review_profile(request: Request) -> JSONResponse:
        state = repository.read()
        profile = state.review_profiles.get(request.path_params["review_profile_id"])
        if profile is None:
            return JSONResponse({"error": "review profile not found"}, status_code=404)
        return JSONResponse(profile.model_dump(mode="json"))

    async def create_release_package(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        state, result = repository.create_release_package_with_result(
            project_id=_required(payload, "project_id"),
            deliverable_ids=_optional_string_list(payload, "deliverable_ids") or [],
            release_id=_optional(payload, "id") or _optional(payload, "release_package_id"),
            release_branch=_optional(payload, "release_branch"),
            canonical_branch=_optional(payload, "canonical_branch"),
            expected_revision=_expected_revision(payload),
        )
        response_payload = team_state_payload(state)
        published_event_ids = _publish_team_events(context, result.get("events"))
        if published_event_ids:
            response_payload["published_event_ids"] = published_event_ids
        return JSONResponse(response_payload, status_code=201)

    async def list_release_packages(_request: Request) -> JSONResponse:
        state = repository.read()
        return JSONResponse(
            {
                "revision": state.revision,
                "release_packages": [
                    release.model_dump(mode="json")
                    for release in state.release_packages.values()
                ],
            }
        )

    async def get_release_package(request: Request) -> JSONResponse:
        state = repository.read()
        release = state.release_packages.get(request.path_params["release_package_id"])
        if release is None:
            return JSONResponse({"error": "release package not found"}, status_code=404)
        return JSONResponse(release.model_dump(mode="json"))

    async def list_git_promotions(_request: Request) -> JSONResponse:
        state = repository.read()
        return JSONResponse(
            {
                "revision": state.revision,
                "git_promotions": [
                    promotion.model_dump(mode="json")
                    for promotion in state.git_promotions.values()
                ],
            }
        )

    async def get_git_promotion(request: Request) -> JSONResponse:
        state = repository.read()
        promotion = state.git_promotions.get(request.path_params["promotion_id"])
        if promotion is None:
            return JSONResponse({"error": "git promotion not found"}, status_code=404)
        return JSONResponse(promotion.model_dump(mode="json"))

    return [
        Route("/state", _with_errors(get_state), methods=["GET"]),
        Route("/templates", _with_errors(list_templates), methods=["GET"]),
        Route(
            "/templates/{template_id}",
            _with_errors(get_template_details),
            methods=["GET"],
        ),
        Route(
            "/templates/{template_id}/materialize",
            _with_errors(materialize_template),
            methods=["POST"],
        ),
        Route("/timeline", _with_errors(get_timeline), methods=["GET"]),
        Route("/costs", _with_errors(get_costs), methods=["GET"]),
        Route("/teams", _with_errors(create_team), methods=["POST"]),
        Route("/teams/{team_id}", _with_errors(patch_team), methods=["PATCH"]),
        Route("/projects", _with_errors(create_project), methods=["POST"]),
        Route("/projects/{project_id}", _with_errors(patch_project), methods=["PATCH"]),
        Route("/roles", _with_errors(create_role), methods=["POST"]),
        Route("/roles", _with_errors(list_roles), methods=["GET"]),
        Route("/roles/{role_id}", _with_errors(get_role), methods=["GET"]),
        Route("/roles/{role_id}", _with_errors(patch_role), methods=["PATCH"]),
        Route("/agents", _with_errors(register_agent), methods=["POST"]),
        Route("/agents/{agent_id}", _with_errors(patch_agent), methods=["PATCH"]),
        Route(
            "/agents/{agent_id}/avatar/previews",
            _with_errors(create_avatar_preview),
            methods=["POST"],
        ),
        Route(
            "/agents/{agent_id}/avatar/upload",
            _with_errors(upload_agent_avatar),
            methods=["POST"],
        ),
        Route(
            "/agents/{agent_id}/avatar/accept-preview",
            _with_errors(accept_agent_avatar_preview),
            methods=["POST"],
        ),
        Route(
            "/agents/{agent_id}/avatar",
            _with_errors(delete_agent_avatar),
            methods=["DELETE"],
        ),
        Route(
            "/avatars/{asset_id}",
            _with_errors(get_avatar_asset),
            methods=["GET"],
        ),
        Route(
            "/avatar-previews/{preview_id}",
            _with_errors(get_avatar_preview),
            methods=["GET"],
        ),
        Route(
            "/agents/{agent_id}/context-rotation",
            _with_errors(request_agent_context_rotation),
            methods=["POST"],
        ),
        Route(
            "/agents/{agent_id}/stand-down",
            _with_errors(stand_down_agent),
            methods=["POST"],
        ),
        Route(
            "/agents/{agent_id}/stand-down/revoke",
            _with_errors(revoke_agent_stand_down),
            methods=["POST"],
        ),
        Route("/tasks", _with_errors(create_task), methods=["POST"]),
        Route("/tasks/{task_id}", _with_errors(patch_task), methods=["PATCH"]),
        Route("/deliverables", _with_errors(record_deliverable), methods=["POST"]),
        Route("/review-requests", _with_errors(record_review_request), methods=["POST"]),
        Route(
            "/review-requests/{review_request_id}/decision",
            _with_errors(decide_review_request),
            methods=["POST"],
        ),
        Route(
            "/operator-directives",
            _with_errors(create_operator_directive),
            methods=["POST"],
        ),
        Route(
            "/action-records/{action_record_id}/dismiss",
            _with_errors(dismiss_action_record),
            methods=["POST"],
        ),
        Route("/review-profiles", _with_errors(create_review_profile), methods=["POST"]),
        Route("/review-profiles", _with_errors(list_review_profiles), methods=["GET"]),
        Route(
            "/review-profiles/{review_profile_id}",
            _with_errors(get_review_profile),
            methods=["GET"],
        ),
        Route(
            "/review-profiles/{review_profile_id}",
            _with_errors(patch_review_profile),
            methods=["PATCH"],
        ),
        Route("/release-packages", _with_errors(create_release_package), methods=["POST"]),
        Route("/release-packages", _with_errors(list_release_packages), methods=["GET"]),
        Route(
            "/release-packages/{release_package_id}",
            _with_errors(get_release_package),
            methods=["GET"],
        ),
        Route("/git-promotions", _with_errors(list_git_promotions), methods=["GET"]),
        Route(
            "/git-promotions/{promotion_id}",
            _with_errors(get_git_promotion),
            methods=["GET"],
        ),
    ]


def _with_errors(handler):
    async def _wrapped(request: Request) -> JSONResponse:
        try:
            return await handler(request)
        except TeamStateConflictError as exc:
            return JSONResponse(
                {
                    "error": str(exc),
                    "expected_revision": exc.expected_revision,
                    "current_revision": exc.current_revision,
                },
                status_code=409,
            )
        except (ValueError, TypeError, ValidationError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    return _wrapped


def _session_repository_from_context(context: AddonContext) -> Any | None:
    service = getattr(context, "service", None)
    runtime = getattr(service, "runtime", None)
    repository = getattr(runtime, "session_repository", None)
    if repository is not None:
        return repository
    return getattr(service, "session_repository", None)


def _publish_team_events(context: AddonContext, events: object) -> list[str]:
    if not isinstance(events, list):
        return []
    bus = InternalEventBus(internal_event_store_path(context.runtime_dir))
    published_ids: list[str] = []
    for event in events:
        if not isinstance(event, Mapping):
            continue
        payload = event.get("payload")
        if not isinstance(payload, Mapping):
            payload = {}
        published = bus.publish(
            event_type=_required_event_text(event.get("event_type"), "event_type"),
            payload=dict(payload),
            correlation_key=_optional_event_text(event.get("correlation_key")),
            idempotency_key=_optional_event_text(event.get("idempotency_key")),
            source=InternalEventSource(source_type="team-addon", source_name="team-addon"),
        )
        published_ids.append(published.event_id)
    return published_ids


def _required_event_text(value: object, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field_name} is required")
    return text


def _optional_event_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


async def _read_payload(request: Request) -> Mapping[str, Any]:
    payload = await request.json()
    if not isinstance(payload, Mapping):
        raise ValueError("request body must be a JSON object")
    return payload


def _required(payload: Mapping[str, Any], *field_names: str) -> str:
    for field_name in field_names:
        value = payload.get(field_name)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    joined = " or ".join(field_names)
    raise ValueError(f"{joined} is required")


def _optional(payload: Mapping[str, Any], field_name: str) -> str | None:
    value = payload.get(field_name)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _operator_actor_from_request(auth: Any | None, request: Request) -> str:
    session: Mapping[str, Any] | None = None
    read_session = getattr(auth, "read_session", None)
    if callable(read_session):
        raw_session = read_session(request)
        if isinstance(raw_session, Mapping):
            session = raw_session
    if session is None:
        require = getattr(auth, "require", None)
        if callable(require):
            raw_session = require(request)
            if isinstance(raw_session, Mapping):
                session = raw_session
    for key in ("login", "preferred_username", "username", "name", "sub"):
        value = str((session or {}).get(key) or "").strip()
        if value:
            return f"operator:{value}"
    return "operator"


def _optional_present_text(payload: Mapping[str, Any], field_name: str) -> str | None:
    if field_name not in payload or payload.get(field_name) is None:
        return None
    return str(payload[field_name]).strip()


def _query_text(request: Request, field_name: str) -> str | None:
    value = request.query_params.get(field_name)
    if value is None:
        return None
    text = value.strip()
    return text or None


def _query_values(request: Request, field_name: str) -> list[str] | None:
    values = request.query_params.getlist(field_name)
    normalized: list[str] = []
    for value in values:
        for item in value.split(","):
            text = item.strip()
            if text:
                normalized.append(text)
    if not normalized:
        return None
    return list(dict.fromkeys(normalized))


def _query_bool(request: Request, field_name: str) -> bool | None:
    value = request.query_params.get(field_name)
    if value is None:
        return None
    text = value.strip().lower()
    if not text:
        return None
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{field_name} must be true or false")


def _optional_bool(payload: Mapping[str, Any], field_name: str) -> bool | None:
    if field_name not in payload or payload.get(field_name) is None:
        return None
    value = payload[field_name]
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"1", "true", "yes", "on"}:
            return True
        if text in {"0", "false", "no", "off"}:
            return False
    raise ValueError(f"{field_name} must be true or false")


def _optional_git_config(payload: Mapping[str, Any]) -> ProjectGitConfig | None:
    git_payload = payload.get("git")
    if git_payload is None:
        return None
    if not isinstance(git_payload, Mapping):
        raise ValueError("git must be a JSON object")
    normalized = dict(git_payload)
    bare_repo = _optional(normalized, "bare_repo")
    normalized.pop("bare_repo", None)
    if bare_repo:
        if normalized.get("bare_repo_name") or normalized.get("bare_repo_path"):
            raise ValueError(
                "bare_repo cannot be combined with bare_repo_name or bare_repo_path"
            )
        if bare_repo.startswith("/"):
            normalized["bare_repo_path"] = bare_repo
        else:
            normalized["bare_repo_name"] = bare_repo
    return ProjectGitConfig.model_validate(normalized)


def _current_agent_avatar_asset_id(state: Any, agent_id: str) -> str | None:
    agent = state.agents.get(agent_id)
    if agent is None:
        return None
    avatar = getattr(agent, "avatar", None)
    return getattr(avatar, "asset_id", None)


def _expected_revision(payload: Mapping[str, Any]) -> int:
    if "expected_revision" not in payload or payload.get("expected_revision") is None:
        raise ValueError("expected_revision is required")
    return _coerce_int(payload["expected_revision"], "expected_revision")


def _required_int(payload: Mapping[str, Any], field_name: str) -> int:
    if field_name not in payload:
        raise ValueError(f"{field_name} is required")
    return _coerce_int(payload[field_name], field_name)


def _optional_int(payload: Mapping[str, Any], field_name: str) -> int | None:
    if field_name not in payload or payload.get(field_name) is None:
        return None
    return _coerce_int(payload[field_name], field_name)


def _coerce_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be an integer")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be an integer") from exc


def _optional_list(payload: Mapping[str, Any], field_name: str) -> list[Any]:
    value = payload.get(field_name)
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a list")
    return value


def _optional_string_list(payload: Mapping[str, Any], field_name: str) -> list[str] | None:
    if field_name not in payload or payload.get(field_name) is None:
        return None
    value = payload[field_name]
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a list of strings")
    normalized = [str(item).strip() for item in value if str(item).strip()]
    return normalized
