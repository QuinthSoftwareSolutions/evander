from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from pydantic import ValidationError
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from agent_sandbox.addons import AddonContext

from .models import (
    EphemeralAgentPolicy,
    ProjectGoal,
    TeamRecordState,
    TeamTaskState,
    team_state_payload,
)
from .repository import TeamStateConflictError, TeamStateRepository


def admin_api_routes(context: AddonContext) -> Sequence[Route]:
    repository = TeamStateRepository(context.runtime_dir)

    async def get_state(_request: Request) -> JSONResponse:
        return JSONResponse(team_state_payload(repository.read()))

    async def create_team(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        state = repository.create_team(
            team_id=_required(payload, "id", "team_id"),
            name=_required(payload, "name"),
            manager_agent_id=_required(payload, "manager_agent_id"),
            manager_session_id=_required(payload, "manager_session_id"),
            manager_role=_optional(payload, "manager_role") or "manager",
            expected_revision=_expected_revision(payload),
        )
        return JSONResponse(team_state_payload(state), status_code=201)

    async def create_project(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        goals = [ProjectGoal.model_validate(item) for item in _optional_list(payload, "goals")]
        state = repository.create_project(
            project_id=_required(payload, "id", "project_id"),
            team_id=_required(payload, "team_id"),
            name=_required(payload, "name"),
            lead_agent_id=_required(payload, "lead_agent_id"),
            lead_session_id=_required(payload, "lead_session_id"),
            lead_role=_optional(payload, "lead_role") or "project_lead",
            goals=goals,
            expected_revision=_expected_revision(payload),
        )
        return JSONResponse(team_state_payload(state), status_code=201)

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
            decision_at=datetime.fromisoformat(raw_decision_at) if raw_decision_at else None,
            expected_revision=_expected_revision(payload),
        )
        return JSONResponse(team_state_payload(state), status_code=201)

    return [
        Route("/state", _with_errors(get_state), methods=["GET"]),
        Route("/teams", _with_errors(create_team), methods=["POST"]),
        Route("/projects", _with_errors(create_project), methods=["POST"]),
        Route("/agents", _with_errors(register_agent), methods=["POST"]),
        Route("/tasks", _with_errors(create_task), methods=["POST"]),
        Route("/tasks/{task_id}", _with_errors(patch_task), methods=["PATCH"]),
        Route("/deliverables", _with_errors(record_deliverable), methods=["POST"]),
        Route("/review-requests", _with_errors(record_review_request), methods=["POST"]),
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
