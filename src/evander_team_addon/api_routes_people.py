"""Team people endpoints."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import (
    UTC,
    datetime,
)
from typing import (
    Any,
)

from starlette.requests import (
    Request,
)
from starlette.responses import Response

from .api_constants import (
    JSONResponse,
)
from .api_context import TeamApiDependencies
from .api_requests import (
    _operator_actor_from_request,
    _optional,
    _optional_bool,
    _optional_present_text,
    _optional_string_list,
    _publish_team_events,
    _read_payload,
    _required,
)
from .models import (
    AgentStatus,
    AgentVoiceProfile,
    EphemeralAgentPolicy,
)
from .services.domain.context_rotation import (
    _append_idle_context_rotation_wake_requested_event,
)


def build_people_handlers(
    dependencies: TeamApiDependencies,
) -> dict[str, Callable[[Request], Awaitable[Response]]]:
    context = dependencies.context
    repository = dependencies.repository
    admin_service = dependencies.admin_service
    runtime_service = dependencies.runtime_service
    _state_response = dependencies._state_response
    _run_ui_rebasable_mutation = dependencies._run_ui_rebasable_mutation

    async def create_role(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        ephemeral_policy = payload.get("ephemeral_policy")
        role_id = _optional(payload, "id") or _optional(payload, "role_id")
        state, rebased_from_revision = _run_ui_rebasable_mutation(
            payload,
            lambda expected_revision: admin_service.create_role(
                role_id=role_id,
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
                expected_revision=expected_revision,
            ),
            create_collection="roles",
            create_id=role_id,
        )
        return _state_response(
            request,
            state,
            status_code=201,
            rebased_from_revision=rebased_from_revision,
        )

    async def patch_role(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        ephemeral_policy = payload.get("ephemeral_policy")
        raw_archived = payload.get("archived")
        role_id = request.path_params["role_id"]
        state, rebased_from_revision = _run_ui_rebasable_mutation(
            payload,
            lambda expected_revision: admin_service.update_role(
                role_id=role_id,
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
                expected_revision=expected_revision,
            ),
            target_collection="roles",
            target_id=role_id,
        )
        return _state_response(
            request, state, rebased_from_revision=rebased_from_revision, publish_role_queue=True
        )

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
        agent_id = _optional(payload, "id") or _optional(payload, "agent_id")
        state, rebased_from_revision = _run_ui_rebasable_mutation(
            payload,
            lambda expected_revision: admin_service.register_agent(
                agent_id=agent_id,
                team_id=_optional(payload, "team_id"),
                project_id=_optional(payload, "project_id"),
                role_id=_optional(payload, "role_id"),
                session_id=_required(payload, "session_id"),
                reports_to_agent_id=_optional(payload, "reports_to_agent_id"),
                display_name=_optional(payload, "display_name"),
                ephemeral_policy=(
                    EphemeralAgentPolicy.model_validate(ephemeral_policy)
                    if ephemeral_policy is not None
                    else None
                ),
                expected_revision=expected_revision,
            ),
            create_collection="agents",
            create_id=agent_id,
        )
        return _state_response(
            request,
            state,
            publish_role_queue=True,
            status_code=201,
            rebased_from_revision=rebased_from_revision,
        )

    async def patch_agent(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        ephemeral_policy = payload.get("ephemeral_policy")
        raw_voice_profile = payload.get("voice_profile")
        raw_status = _optional(payload, "status")
        agent_id = request.path_params["agent_id"]
        state, rebased_from_revision = _run_ui_rebasable_mutation(
            payload,
            lambda expected_revision: admin_service.update_agent(
                agent_id=agent_id,
                team_id=_optional(payload, "team_id"),
                set_team_id="team_id" in payload,
                project_id=_optional(payload, "project_id"),
                set_project_id="project_id" in payload,
                project_ids=(
                    _optional_string_list(payload, "project_ids")
                    if "project_ids" in payload
                    else None
                ),
                set_project_ids="project_ids" in payload,
                role_id=_optional(payload, "role_id"),
                set_role_id="role_id" in payload,
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
                voice_profile=(
                    AgentVoiceProfile.model_validate(raw_voice_profile)
                    if raw_voice_profile is not None
                    else None
                ),
                set_voice_profile="voice_profile" in payload,
                context_clearing_policy=_optional(payload, "context_clearing_policy"),
                set_context_clearing_policy="context_clearing_policy" in payload,
                context_clearing_idle_grace_seconds=_optional(
                    payload,
                    "context_clearing_idle_grace_seconds",
                ),
                set_context_clearing_idle_grace_seconds=(
                    "context_clearing_idle_grace_seconds" in payload
                ),
                auto_context_clearing_enabled=_optional_bool(
                    payload,
                    "auto_context_clearing_enabled",
                ),
                set_auto_context_clearing_enabled=("auto_context_clearing_enabled" in payload),
                expected_revision=expected_revision,
            ),
            target_collection="agents",
            target_id=agent_id,
        )
        idle_wake_result: dict[str, Any] = {"events": []}
        if (
            "context_clearing_policy" in payload
            or "context_clearing_idle_grace_seconds" in payload
            or "auto_context_clearing_enabled" in payload
        ):
            _append_idle_context_rotation_wake_requested_event(
                state,
                agent_id=agent_id,
                result=idle_wake_result,
                now=datetime.now(UTC),
            )
        published_event_ids = _publish_team_events(
            context,
            idle_wake_result.get("events"),
        )
        return _state_response(
            request,
            state,
            publish_role_queue=True,
            rebased_from_revision=rebased_from_revision,
            extra=({"published_event_ids": published_event_ids} if published_event_ids else None),
        )

    async def request_agent_context_rotation(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        agent_id = request.path_params["agent_id"]
        state, rebased_from_revision = _run_ui_rebasable_mutation(
            payload,
            lambda expected_revision: runtime_service.request_agent_context_rotation(
                agent_id=agent_id,
                requested_by=_optional(payload, "requested_by")
                or _operator_actor_from_request(context.auth, request),
                request_note=_optional(payload, "reason"),
                expected_revision=expected_revision,
            ),
            target_collection="agents",
            target_id=agent_id,
        )
        return _state_response(request, state, rebased_from_revision=rebased_from_revision)

    async def stand_down_agent(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        agent_id = request.path_params["agent_id"]
        state, rebased_from_revision = _run_ui_rebasable_mutation(
            payload,
            lambda expected_revision: runtime_service.stand_down_agent(
                agent_id=agent_id,
                reason=_required(payload, "reason"),
                set_by=_optional(payload, "set_by")
                or _operator_actor_from_request(context.auth, request),
                scope=_optional(payload, "scope") or "subtree",
                expected_revision=expected_revision,
            ),
            target_collection="agents",
            target_id=agent_id,
        )
        return _state_response(
            request,
            state,
            status_code=201,
            rebased_from_revision=rebased_from_revision,
        )

    async def revoke_agent_stand_down(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        agent_id = request.path_params["agent_id"]
        state, rebased_from_revision = _run_ui_rebasable_mutation(
            payload,
            lambda expected_revision: runtime_service.revoke_stand_downs(
                agent_id=agent_id,
                revoked_by=_optional(payload, "revoked_by")
                or _operator_actor_from_request(context.auth, request),
                expected_revision=expected_revision,
            ),
            target_collection="agents",
            target_id=agent_id,
        )
        return _state_response(request, state, rebased_from_revision=rebased_from_revision)

    return {
        "create_role": create_role,
        "patch_role": patch_role,
        "list_roles": list_roles,
        "get_role": get_role,
        "register_agent": register_agent,
        "patch_agent": patch_agent,
        "request_agent_context_rotation": request_agent_context_rotation,
        "stand_down_agent": stand_down_agent,
        "revoke_agent_stand_down": revoke_agent_stand_down,
    }
