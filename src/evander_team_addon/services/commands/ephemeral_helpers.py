from __future__ import annotations


from collections.abc import (
    Mapping,
    Sequence,
)
from typing import Any
from .types import TeamCommandValidationError
from ...git_naming import ephemeral_agent_branch
from ...models import (
    EphemeralAssignment,
    EphemeralAssignmentState,
    PersistentAgent,
    _unique_text_list,
)
from ..domain.common import (
    _optional_text,
    _required_text,
    _string_list,
)

def _normalize_ephemeral_request_mapping(
    item: Mapping[str, Any],
    *,
    field_name: str,
) -> dict[str, Any]:
    if not isinstance(item, Mapping):
        raise ValueError(f"{field_name} must be a JSON object")
    return {
        "id": _optional_text(item.get("id")),
        "task_id": _optional_text(item.get("task_id")),
        "session_id": (
            _optional_text(item.get("worker_session_id")) or _optional_text(item.get("session_id"))
        ),
        "title": _optional_text(item.get("title")),
        "prompt": _optional_text(item.get("prompt")),
        "model": _optional_text(item.get("model")),
        "agent_provider": _optional_text(item.get("agent_provider")),
        "execution_backend": _optional_text(item.get("execution_backend")),
        "container_image": _optional_text(item.get("container_image")),
        "allowed_tools": _unique_text_list(item.get("allowed_tools") or [], "allowed_tools"),
        "ttl_hours": item.get("ttl_hours"),
        "directory_path": _optional_text(item.get("directory_path")),
    }

def _team_request_ephemeral_normalized_payload(
    *,
    assignments: Sequence[Mapping[str, Any]],
    handled_inbox_item_ids: Sequence[str],
    correlation_id: str | None,
    causation_id: str | None,
) -> dict[str, Any]:
    return {
        "assignments": [dict(item) for item in assignments],
        "handled_inbox_item_ids": sorted(
            _unique_text_list(handled_inbox_item_ids, "handled_inbox_item_ids")
        ),
        "correlation_id": _optional_text(correlation_id),
        "causation_id": _optional_text(causation_id),
    }

def _team_accept_ephemeral_result_normalized_payload(
    *,
    assignment_id: str,
    deliverable_id: str | None,
    handled_inbox_item_ids: Sequence[str],
    correlation_id: str | None,
    causation_id: str | None,
) -> dict[str, Any]:
    return {
        "assignment_id": _required_text(assignment_id, "assignment_id"),
        "deliverable_id": _optional_text(deliverable_id),
        "handled_inbox_item_ids": sorted(
            _unique_text_list(handled_inbox_item_ids, "handled_inbox_item_ids")
        ),
        "correlation_id": _optional_text(correlation_id),
        "causation_id": _optional_text(causation_id),
    }

def _team_reject_ephemeral_result_normalized_payload(
    *,
    assignment_id: str,
    rejection_reason: str | None,
    handled_inbox_item_ids: Sequence[str],
    correlation_id: str | None,
    causation_id: str | None,
) -> dict[str, Any]:
    return {
        "assignment_id": _required_text(assignment_id, "assignment_id"),
        "rejection_reason": _optional_text(rejection_reason),
        "handled_inbox_item_ids": sorted(
            _unique_text_list(handled_inbox_item_ids, "handled_inbox_item_ids")
        ),
        "correlation_id": _optional_text(correlation_id),
        "causation_id": _optional_text(causation_id),
    }

def _team_report_ephemeral_result_normalized_payload(
    *,
    assignment_id: str | None,
    summary: str | None,
    artifact_refs: Sequence[str],
    agent_branch: str | None,
    commit_sha: str | None,
    error: str | None,
    correlation_id: str | None,
    causation_id: str | None,
) -> dict[str, Any]:
    return {
        "assignment_id": _optional_text(assignment_id),
        "summary": _optional_text(summary),
        "artifact_refs": _string_list(artifact_refs, "artifact_refs"),
        "agent_branch": _optional_text(agent_branch),
        "commit_sha": _optional_text(commit_sha),
        "error": _optional_text(error),
        "correlation_id": _optional_text(correlation_id),
        "causation_id": _optional_text(causation_id),
    }

def _require_parent_assignment_owner(
    assignment: EphemeralAssignment,
    *,
    agent: PersistentAgent,
    tool_name: str,
) -> None:
    if assignment.parent_agent_id != agent.id:
        raise TeamCommandValidationError(
            code="ephemeral_assignment_not_owned",
            message=(
                f"{tool_name} cannot mutate ephemeral assignment {assignment.id} "
                f"owned by agent {assignment.parent_agent_id}"
            ),
            correction_context={
                "assignment_id": assignment.id,
                "parent_agent_id": assignment.parent_agent_id,
                "caller_agent_id": agent.id,
            },
        )

def _validate_ephemeral_result_report(
    assignment: EphemeralAssignment,
    *,
    summary: str | None,
    agent_branch: str | None,
    error: str | None,
) -> None:
    if assignment.state not in {
        EphemeralAssignmentState.REQUESTED,
        EphemeralAssignmentState.SPAWN_PENDING,
        EphemeralAssignmentState.RUNNING,
    }:
        raise TeamCommandValidationError(
            code="ephemeral_assignment_already_reported",
            message=(f"ephemeral assignment {assignment.id} is already {assignment.state.value}"),
            correction_context={
                "assignment_id": assignment.id,
                "state": assignment.state.value,
            },
        )
    if error is None and _optional_text(summary) is None:
        raise TeamCommandValidationError(
            code="summary_required",
            message="summary is required for successful ephemeral results",
            correction_context={"assignment_id": assignment.id, "field": "summary"},
        )
    if agent_branch is not None:
        expected_branch = ephemeral_agent_branch(assignment.session_id, assignment.id)
        if agent_branch != expected_branch:
            raise TeamCommandValidationError(
                code="agent_branch_mismatch",
                message=(
                    f"agent_branch {agent_branch!r} does not match expected "
                    f"ephemeral branch {expected_branch!r}"
                ),
                correction_context={
                    "assignment_id": assignment.id,
                    "agent_branch": agent_branch,
                    "expected_agent_branch": expected_branch,
                },
            )

__all__ = [
    '_normalize_ephemeral_request_mapping',
    '_team_request_ephemeral_normalized_payload',
    '_team_accept_ephemeral_result_normalized_payload',
    '_team_reject_ephemeral_result_normalized_payload',
    '_team_report_ephemeral_result_normalized_payload',
    '_require_parent_assignment_owner',
    '_validate_ephemeral_result_report',
]
