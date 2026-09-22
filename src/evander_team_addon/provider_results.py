"""Provider results."""

from __future__ import annotations

from collections.abc import (
    Mapping,
    Sequence,
)
from typing import (
    Any,
)
from urllib.parse import (
    urlencode,
)

from .models import (
    TeamState,
    agent_project_ids,
)
from .services.domain.review import (
    _review_request_was_created,
)
from .services.domain.task_assessment import (
    task_payload_with_assessment,
)


def _review_request_effect_success_result(
    state: TeamState,
    *,
    result: Mapping[str, Any],
    review_request_id: str | None,
    actor_agent_id: str | None,
    published_event_ids: list[str],
) -> dict[str, Any]:
    review = state.review_requests.get(review_request_id or "")
    created = bool(
        review_request_id is not None and _review_request_was_created(result, review_request_id)
    )
    existing_review_request_ids = list(result.get("existing_review_request_ids") or [])
    return {
        "success": review is not None,
        "revision": state.revision,
        "review_request_id": review.id if review is not None else review_request_id,
        "deliverable_id": review.deliverable_id if review is not None else None,
        "reviewer_agent_id": review.reviewer_agent_id if review is not None else None,
        "actor_agent_id": actor_agent_id,
        "created": created,
        "created_review_request_ids": [review_request_id] if created and review_request_id else [],
        "existing_review_request_ids": existing_review_request_ids,
        "created_inbox_item_ids": list(result.get("created_inbox_item_ids") or []),
        "published_event_ids": published_event_ids,
        "events": list(result.get("events") or []),
        "review_request": review.model_dump(mode="json") if review is not None else None,
        "error": None if review is not None else "review request was not found after mutation",
    }


def _review_request_effect_error_result(
    *,
    revision: int,
    review_request_id: str | None,
    actor_agent_id: str | None,
    deliverable_id: str | None,
    reviewer_agent_id: str | None,
    error: str,
    expected_revision: int | None = None,
    current_revision: int | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "success": False,
        "revision": revision,
        "review_request_id": review_request_id,
        "deliverable_id": deliverable_id,
        "reviewer_agent_id": reviewer_agent_id,
        "actor_agent_id": actor_agent_id,
        "created": False,
        "created_review_request_ids": [],
        "existing_review_request_ids": [],
        "created_inbox_item_ids": [],
        "published_event_ids": [],
        "events": [],
        "review_request": None,
        "error": error,
    }
    if expected_revision is not None:
        result["expected_revision"] = expected_revision
    if current_revision is not None:
        result["current_revision"] = current_revision
    return result


def _review_cohort_effect_success_result(
    state: TeamState,
    *,
    review_cohort_id: str | None,
    actor_agent_id: str | None,
    created_review_ids: Sequence[str],
    existing_review_ids: Sequence[str],
    result: Mapping[str, Any],
    published_event_ids: list[str],
) -> dict[str, Any]:
    review_ids = [*created_review_ids, *existing_review_ids]
    reviews = [
        state.review_requests[review_id]
        for review_id in review_ids
        if review_id in state.review_requests
    ]
    deliverable = (
        state.deliverables.get(reviews[0].deliverable_id)
        if reviews
        else next(
            (
                candidate
                for candidate in state.deliverables.values()
                if candidate.review_cohort_id == review_cohort_id
            ),
            None,
        )
    )
    return {
        "success": bool(
            deliverable is not None
            and review_cohort_id
            and deliverable.review_cohort_id == review_cohort_id
            and deliverable.review_cohort_sealed_at is not None
        ),
        "revision": state.revision,
        "review_cohort_id": review_cohort_id,
        "review_cohort_sealed_at": (
            deliverable.review_cohort_sealed_at.isoformat()
            if deliverable is not None and deliverable.review_cohort_sealed_at is not None
            else None
        ),
        "deliverable_id": deliverable.id if deliverable is not None else None,
        "actor_agent_id": actor_agent_id,
        "created": bool(created_review_ids),
        "created_review_request_ids": list(created_review_ids),
        "existing_review_request_ids": list(existing_review_ids),
        "created_inbox_item_ids": list(result.get("created_inbox_item_ids") or []),
        "published_event_ids": published_event_ids,
        "events": list(result.get("events") or []),
        "review_requests": [review.model_dump(mode="json") for review in reviews],
        "error": None,
    }


def _review_cohort_effect_error_result(
    *,
    revision: int,
    review_cohort_id: str | None,
    actor_agent_id: str | None,
    deliverable_id: str | None,
    error: str,
    expected_revision: int | None = None,
    current_revision: int | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "success": False,
        "revision": revision,
        "review_cohort_id": review_cohort_id,
        "review_cohort_sealed_at": None,
        "deliverable_id": deliverable_id,
        "actor_agent_id": actor_agent_id,
        "created": False,
        "created_review_request_ids": [],
        "existing_review_request_ids": [],
        "created_inbox_item_ids": [],
        "published_event_ids": [],
        "events": [],
        "review_requests": [],
        "error": error,
    }
    if expected_revision is not None:
        result["expected_revision"] = expected_revision
    if current_revision is not None:
        result["current_revision"] = current_revision
    return result


def _register_agent_effect_success_result(
    *,
    state: TeamState,
    agent: Any,
    created: bool,
) -> dict[str, Any]:
    return {
        "success": True,
        "revision": state.revision,
        "created": created,
        "agent_id": agent.id,
        "session_id": agent.session_id,
        "team_id": agent.team_id,
        "project_id": next(iter(agent_project_ids(state, agent.id)), None),
        "agent": agent.model_dump(mode="json"),
        "error": None,
    }


def _register_agent_effect_error_result(
    *,
    revision: int,
    agent_id: str | None,
    session_id: str | None,
    team_id: str | None,
    project_id: str | None,
    error: str,
    expected_revision: int | None = None,
    current_revision: int | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "success": False,
        "revision": revision,
        "created": False,
        "agent_id": agent_id,
        "session_id": session_id,
        "team_id": team_id,
        "project_id": project_id,
        "agent": None,
        "error": error,
    }
    if expected_revision is not None:
        result["expected_revision"] = expected_revision
    if current_revision is not None:
        result["current_revision"] = current_revision
    return result


def _restore_agent_effect_success_result(
    *,
    state: TeamState,
    agent: Any,
    previous_session_id: str,
    restored: bool,
    already_restored: bool,
) -> dict[str, Any]:
    return {
        "success": True,
        "revision": state.revision,
        "restored": restored,
        "already_restored": already_restored,
        "agent_id": agent.id,
        "previous_session_id": previous_session_id,
        "session_id": agent.session_id,
        "team_id": agent.team_id,
        "project_id": next(iter(agent_project_ids(state, agent.id)), None),
        "agent": agent.model_dump(mode="json"),
        "error": None,
    }


def _restore_agent_effect_error_result(
    *,
    revision: int,
    agent_id: str | None,
    previous_session_id: str | None,
    session_id: str | None,
    team_id: str | None,
    project_id: str | None,
    error: str,
    expected_revision: int | None = None,
    current_revision: int | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "success": False,
        "revision": revision,
        "restored": False,
        "already_restored": False,
        "agent_id": agent_id,
        "previous_session_id": previous_session_id,
        "session_id": session_id,
        "team_id": team_id,
        "project_id": project_id,
        "agent": None,
        "error": error,
    }
    if expected_revision is not None:
        result["expected_revision"] = expected_revision
    if current_revision is not None:
        result["current_revision"] = current_revision
    return result


def _retire_agent_effect_success_result(
    *,
    state: TeamState,
    agent: Any,
    retired: bool,
    already_retired: bool,
) -> dict[str, Any]:
    return {
        "success": True,
        "revision": state.revision,
        "retired": retired,
        "already_retired": already_retired,
        "agent_id": agent.id,
        "session_id": agent.session_id,
        "blockers": [],
        "agent": agent.model_dump(mode="json"),
        "error": None,
    }


def _retire_agent_effect_error_result(
    *,
    revision: int,
    agent_id: str | None,
    session_id: str | None,
    blockers: Sequence[str],
    error: str,
    expected_revision: int | None = None,
    current_revision: int | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "success": False,
        "revision": revision,
        "retired": False,
        "already_retired": False,
        "agent_id": agent_id,
        "session_id": session_id,
        "blockers": list(blockers),
        "agent": None,
        "error": error,
    }
    if expected_revision is not None:
        result["expected_revision"] = expected_revision
    if current_revision is not None:
        result["current_revision"] = current_revision
    return result


def _case_effect_success_result(
    state: TeamState,
    *,
    case_id: str | None,
) -> dict[str, Any]:
    case = state.cases.get(case_id or "")
    case_payload = case.model_dump(mode="json") if case is not None else None
    return {
        "task_board_path": "/#addon:team-addon?"
        + urlencode({"view": "task-board", "case_id": case_id}),
        "success": case is not None,
        "revision": state.revision,
        "case_id": case.id if case is not None else case_id,
        "team_id": case.team_id if case is not None else None,
        "project_id": case.project_id if case is not None else None,
        "task_ids": list(case.task_ids) if case is not None else [],
        "case": case_payload,
        "error": None if case is not None else "case was not found after mutation",
    }


def _case_effect_error_result(
    *,
    revision: int,
    case_id: str | None,
    team_id: str | None,
    project_id: str | None,
    error: str,
    expected_revision: int | None = None,
    current_revision: int | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "success": False,
        "revision": revision,
        "case_id": case_id,
        "team_id": team_id,
        "project_id": project_id,
        "task_ids": [],
        "case": None,
        "error": error,
    }
    if expected_revision is not None:
        result["expected_revision"] = expected_revision
    if current_revision is not None:
        result["current_revision"] = current_revision
    return result


def _task_effect_success_result(
    state: TeamState,
    *,
    task_id: str | None,
    created: bool,
) -> dict[str, Any]:
    task = state.tasks.get(task_id or "")
    task_payload = task_payload_with_assessment(state, task) if task is not None else None
    project = state.projects.get(task.project_id) if task is not None else None
    return {
        "success": task is not None,
        "revision": state.revision,
        "task_id": task.id if task is not None else task_id,
        "team_id": project.team_id if project is not None else None,
        "project_id": task.project_id if task is not None else None,
        "owner_agent_id": task.owner_agent_id if task is not None else None,
        "created": created,
        "task": task_payload,
        "error": None if task is not None else "task was not found after mutation",
    }


def _task_effect_error_result(
    *,
    revision: int,
    task_id: str | None,
    team_id: str | None,
    project_id: str | None,
    owner_agent_id: str | None,
    error: str,
    expected_revision: int | None = None,
    current_revision: int | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "success": False,
        "revision": revision,
        "task_id": task_id,
        "team_id": team_id,
        "project_id": project_id,
        "owner_agent_id": owner_agent_id,
        "created": False,
        "task": None,
        "error": error,
    }
    if expected_revision is not None:
        result["expected_revision"] = expected_revision
    if current_revision is not None:
        result["current_revision"] = current_revision
    return result


def _validate_create_task_effect_scope(
    state: TeamState,
    *,
    team_id: str,
    project_id: str,
    owner_agent_id: str,
    task_id: str | None,
) -> None:
    if team_id not in state.teams:
        raise ValueError(f"team {team_id} does not exist")
    project = state.projects.get(project_id)
    if project is None:
        raise ValueError(f"project {project_id} does not exist")
    if project.team_id != team_id:
        raise ValueError(f"project {project_id} does not belong to team {team_id}")
    owner = state.agents.get(owner_agent_id)
    if owner is None:
        raise ValueError(f"task owner {owner_agent_id} does not exist")
    if owner.team_id != team_id:
        raise ValueError(f"task owner {owner_agent_id} does not belong to team {team_id}")
    if task_id is None:
        return
    existing_task = state.tasks.get(task_id)
    if existing_task is None:
        return
    if existing_task.project_id != project_id:
        raise ValueError(
            f"task {task_id} belongs to project {existing_task.project_id}, not {project_id}"
        )
    if existing_task.owner_agent_id != owner_agent_id:
        raise ValueError(
            f"task {task_id} is owned by {existing_task.owner_agent_id}, not {owner_agent_id}"
        )


def _result_outputs_payload(payload: Mapping[str, Any]) -> Any:
    if "result_outputs" in payload:
        return payload.get("result_outputs")
    return payload.get("outputs")
