from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from pydantic import ValidationError
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from agent_sandbox.addons import AddonContext
from agent_sandbox.events.internal import (
    InternalEventBus,
    InternalEventSource,
    internal_event_store_path,
)

from .models import (
    EphemeralAgentPolicy,
    ProjectGitConfig,
    ProjectGoal,
    ReviewProfileCriterion,
    TeamRecordState,
    TeamTaskState,
    team_state_payload,
)
from .templates import get_template, template_catalog
from .repository import TeamStateConflictError, TeamStateRepository


def admin_api_routes(context: AddonContext) -> Sequence[Route]:
    repository = TeamStateRepository(context.runtime_dir)

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

    async def create_team(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        state = repository.create_team(
            team_id=_required(payload, "id", "team_id"),
            name=_required(payload, "name"),
            manager_agent_id=_required(payload, "manager_agent_id"),
            manager_session_id=_required(payload, "manager_session_id"),
            manager_display_name=_optional(payload, "manager_display_name"),
            manager_role=_optional(payload, "manager_role") or "manager",
            operator_communication_target_id=_optional(
                payload, "operator_communication_target_id"
            ),
            expected_revision=_expected_revision(payload),
        )
        return JSONResponse(team_state_payload(state), status_code=201)

    async def patch_team(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        state = repository.update_team(
            team_id=request.path_params["team_id"],
            operator_communication_target_id=_optional(
                payload, "operator_communication_target_id"
            ),
            expected_revision=_expected_revision(payload),
        )
        return JSONResponse(team_state_payload(state))

    async def create_project(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        goals = [ProjectGoal.model_validate(item) for item in _optional_list(payload, "goals")]
        state = repository.create_project(
            project_id=_required(payload, "id", "project_id"),
            team_id=_required(payload, "team_id"),
            name=_required(payload, "name"),
            lead_agent_id=_required(payload, "lead_agent_id"),
            lead_session_id=_required(payload, "lead_session_id"),
            lead_display_name=_optional(payload, "lead_display_name"),
            lead_role=_optional(payload, "lead_role") or "project_lead",
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
        state = repository.update_project(
            project_id=request.path_params["project_id"],
            git=_optional_git_config(payload),
            set_git=has_git,
            goals=goals,
            expected_revision=_expected_revision(payload),
        )
        return JSONResponse(team_state_payload(state))

    async def register_agent(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        ephemeral_policy = payload.get("ephemeral_policy")
        state = repository.register_agent(
            agent_id=_required(payload, "id", "agent_id"),
            team_id=_required(payload, "team_id"),
            project_id=_required(payload, "project_id"),
            role=_required(payload, "role"),
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

    async def create_task(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        raw_state = _optional(payload, "state")
        state = repository.create_or_reprioritize_task(
            task_id=_required(payload, "id", "task_id"),
            owner_agent_id=_required(payload, "owner_agent_id"),
            project_id=_required(payload, "project_id"),
            priority=_required_int(payload, "priority"),
            title=_required(payload, "title"),
            description=_required(payload, "description"),
            created_by_agent_id=_required(payload, "created_by_agent_id"),
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
            deliverable_id=_required(payload, "id", "deliverable_id"),
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
            review_request_id=_required(payload, "id", "review_request_id"),
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

    async def create_review_profile(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        criteria = [
            ReviewProfileCriterion.model_validate(item)
            for item in _optional_list(payload, "criteria")
        ]
        state = repository.create_review_profile(
            profile_id=_required(payload, "id", "review_profile_id"),
            team_id=_required(payload, "team_id"),
            project_id=_optional(payload, "project_id"),
            name=_required(payload, "name"),
            description=_optional(payload, "description") or "",
            applicable_roles=_optional_string_list(payload, "applicable_roles"),
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
            description=_optional(payload, "description"),
            applicable_roles=_optional_string_list(payload, "applicable_roles"),
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
        Route("/teams", _with_errors(create_team), methods=["POST"]),
        Route("/teams/{team_id}", _with_errors(patch_team), methods=["PATCH"]),
        Route("/projects", _with_errors(create_project), methods=["POST"]),
        Route("/projects/{project_id}", _with_errors(patch_project), methods=["PATCH"]),
        Route("/agents", _with_errors(register_agent), methods=["POST"]),
        Route("/tasks", _with_errors(create_task), methods=["POST"]),
        Route("/tasks/{task_id}", _with_errors(patch_task), methods=["PATCH"]),
        Route("/deliverables", _with_errors(record_deliverable), methods=["POST"]),
        Route("/review-requests", _with_errors(record_review_request), methods=["POST"]),
        Route(
            "/review-requests/{review_request_id}/decision",
            _with_errors(decide_review_request),
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


def _optional_git_config(payload: Mapping[str, Any]) -> ProjectGitConfig | None:
    git_payload = payload.get("git")
    if git_payload is None:
        return None
    if not isinstance(git_payload, Mapping):
        raise ValueError("git must be a JSON object")
    return ProjectGitConfig.model_validate(dict(git_payload))


def _expected_revision(payload: Mapping[str, Any]) -> int | None:
    if "expected_revision" not in payload or payload.get("expected_revision") is None:
        return None
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
