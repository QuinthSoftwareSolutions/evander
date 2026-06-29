from __future__ import annotations

from typing import Any

from ..repository import TeamStateRepository
from .domain_helpers import (
    EPHEMERAL_COMPLETED_EVENT_TYPE,
    EphemeralAssignmentState,
    TeamState,
    _apply_ephemeral_acceptance,
    _bool_from_value,
    _create_ephemeral_assignments,
    _create_ephemeral_parent_notice,
    _ephemeral_assignment_event_payload,
    _ephemeral_request_result,
    _list_of_mappings,
    _optional_text,
    _require_present,
    _required_text,
    _resolve_ephemeral_assignment,
    _string_list,
    ephemeral_agent_branch,
    utc_now,
)


class TeamEphemeralService:
    def __init__(self, repository: TeamStateRepository) -> None:
        self._repository = repository

    @property
    def repository(self) -> TeamStateRepository:
        return self._repository

    def request_ephemeral_assignments(
        self,
        *,
        parent_agent_id: str,
        requests: list[dict[str, Any]],
        dispatch_id: str | None = None,
        expected_revision: int | None = None,
    ) -> tuple[TeamState, dict[str, Any]]:
        normalized_parent_id = _required_text(parent_agent_id, "parent_agent_id")
        normalized_dispatch_id = _optional_text(dispatch_id)
        normalized_requests = _list_of_mappings(requests, "requests")
        result: dict[str, Any] = _ephemeral_request_result()

        def _mutate(state_model: TeamState) -> TeamState:
            parent = _require_present(state_model.agents, normalized_parent_id, "parent agent")
            _create_ephemeral_assignments(
                state_model,
                parent=parent,
                requests=normalized_requests,
                dispatch_id=normalized_dispatch_id,
                result=result,
            )
            return state_model

        state = self._repository.update(_mutate, expected_revision=expected_revision)
        result["revision"] = state.revision
        result["events"] = [
            _ephemeral_assignment_event_payload(state, assignment_id)
            for assignment_id in result["created_ephemeral_assignment_ids"]
        ]
        return state, result

    def mark_ephemeral_spawned(
        self,
        *,
        assignment_id: str,
        session_id: str | None = None,
        create_result: dict[str, Any] | None = None,
        expected_revision: int | None = None,
    ) -> tuple[TeamState, dict[str, Any]]:
        normalized_assignment_id = _required_text(assignment_id, "assignment_id")
        normalized_session_id = _optional_text(session_id)
        normalized_create_result = dict(create_result or {})
        result: dict[str, Any] = {
            "success": False,
            "assignment_id": normalized_assignment_id,
            "session_id": normalized_session_id,
            "state": None,
            "error": None,
        }

        def _mutate(state_model: TeamState) -> TeamState:
            assignment = _require_present(
                state_model.ephemeral_assignments,
                normalized_assignment_id,
                "ephemeral assignment",
            )
            result["session_id"] = assignment.session_id
            if normalized_session_id is not None and normalized_session_id != assignment.session_id:
                result["error"] = "session_id does not match ephemeral assignment"
                assignment.state = EphemeralAssignmentState.FAILED
                assignment.error = str(result["error"])
            elif normalized_create_result and not _bool_from_value(
                normalized_create_result.get("success"),
                default=True,
            ):
                assignment.state = EphemeralAssignmentState.FAILED
                assignment.error = (
                    _optional_text(normalized_create_result.get("message"))
                    or _optional_text(normalized_create_result.get("error"))
                    or "spawn failed"
                )
                result["error"] = assignment.error
            else:
                assignment.state = EphemeralAssignmentState.RUNNING
                assignment.error = None
                result["success"] = True
            assignment.updated_at = utc_now()
            result["state"] = assignment.state.value
            return state_model

        state = self._repository.update(_mutate, expected_revision=expected_revision)
        result["revision"] = state.revision
        return state, result

    def apply_ephemeral_action(
        self,
        *,
        action: dict[str, Any] | None,
        session_id: str | None = None,
        valid: bool = True,
        error: str | None = None,
        expected_revision: int | None = None,
    ) -> tuple[TeamState, dict[str, Any]]:
        normalized_action = dict(action or {}) if isinstance(action, dict) else {}
        normalized_session_id = _optional_text(session_id)
        result: dict[str, Any] = {
            "success": False,
            "assignment_id": None,
            "session_id": normalized_session_id,
            "state": None,
            "error": None,
            "event": None,
            "created_inbox_item_ids": [],
        }

        def _mutate(state_model: TeamState) -> TeamState:
            assignment = _resolve_ephemeral_assignment(
                state_model,
                action=normalized_action,
                session_id=normalized_session_id,
            )
            result["assignment_id"] = assignment.id
            result["session_id"] = assignment.session_id
            if not valid:
                assignment.state = EphemeralAssignmentState.FAILED
                assignment.error = error or "invalid ephemeral action"
                result["error"] = assignment.error
                result["state"] = assignment.state.value
                assignment.updated_at = utc_now()
                return state_model

            assignment.summary = _required_text(normalized_action.get("summary"), "summary")
            assignment.artifact_refs = _string_list(
                normalized_action.get("artifact_refs"),
                "artifact_refs",
            )
            agent_branch = _optional_text(normalized_action.get("agent_branch"))
            commit_sha = _optional_text(normalized_action.get("commit_sha"))
            if agent_branch is not None:
                expected_branch = ephemeral_agent_branch(assignment.session_id, assignment.id)
                if agent_branch != expected_branch:
                    assignment.state = EphemeralAssignmentState.FAILED
                    assignment.error = (
                        f"agent_branch {agent_branch!r} does not match expected "
                        f"ephemeral branch {expected_branch!r}"
                    )
                    assignment.updated_at = utc_now()
                    result["error"] = assignment.error
                    result["state"] = assignment.state.value
                    result["success"] = False
                    return state_model
                assignment.agent_branch = agent_branch
            if commit_sha is not None:
                assignment.commit_sha = commit_sha
            assignment.error = _optional_text(normalized_action.get("error"))
            assignment.state = (
                EphemeralAssignmentState.FAILED
                if assignment.error
                else EphemeralAssignmentState.COMPLETED
            )
            if assignment.state == EphemeralAssignmentState.COMPLETED:
                inbox_id = _create_ephemeral_parent_notice(state_model, assignment)
                result["created_inbox_item_ids"].append(inbox_id)
            assignment.updated_at = utc_now()
            result["success"] = assignment.state == EphemeralAssignmentState.COMPLETED
            result["state"] = assignment.state.value
            result["error"] = assignment.error
            return state_model

        state = self._repository.update(_mutate, expected_revision=expected_revision)
        result["revision"] = state.revision
        assignment_id = result.get("assignment_id")
        if isinstance(assignment_id, str) and result.get("success"):
            result["event"] = _ephemeral_assignment_event_payload(
                state,
                assignment_id,
                event_type=EPHEMERAL_COMPLETED_EVENT_TYPE,
            )
        return state, result

    def accept_ephemeral_deliverable(
        self,
        *,
        assignment_id: str,
        accepted: bool = True,
        deliverable_id: str | None = None,
        rejection_reason: str | None = None,
        expected_revision: int | None = None,
    ) -> tuple[TeamState, dict[str, Any]]:
        normalized_assignment_id = _required_text(assignment_id, "assignment_id")
        normalized_deliverable_id = _optional_text(deliverable_id)
        normalized_rejection_reason = _optional_text(rejection_reason)
        result: dict[str, Any] = {
            "success": False,
            "assignment_id": normalized_assignment_id,
            "accepted": bool(accepted),
            "deliverable_id": None,
            "state": None,
            "error": None,
        }

        def _mutate(state_model: TeamState) -> TeamState:
            assignment = _require_present(
                state_model.ephemeral_assignments,
                normalized_assignment_id,
                "ephemeral assignment",
            )
            result["deliverable_id"] = _apply_ephemeral_acceptance(
                state_model,
                assignment=assignment,
                accepted=accepted,
                deliverable_id=normalized_deliverable_id,
                rejection_reason=normalized_rejection_reason,
            )
            result["success"] = True
            result["state"] = assignment.state.value
            return state_model

        state = self._repository.update(_mutate, expected_revision=expected_revision)
        result["revision"] = state.revision
        return state, result

    def mark_ephemeral_cleaned_up(
        self,
        *,
        assignment_id: str | None = None,
        session_id: str | None = None,
        destroy_result: dict[str, Any] | None = None,
        expected_revision: int | None = None,
    ) -> tuple[TeamState, dict[str, Any]]:
        normalized_assignment_id = _optional_text(assignment_id)
        normalized_session_id = _optional_text(session_id)
        normalized_destroy_result = dict(destroy_result or {})
        result: dict[str, Any] = {
            "success": False,
            "assignment_id": normalized_assignment_id,
            "session_id": normalized_session_id,
            "state": None,
            "error": None,
        }

        def _mutate(state_model: TeamState) -> TeamState:
            assignment = _resolve_ephemeral_assignment(
                state_model,
                action={"ephemeral_assignment_id": normalized_assignment_id}
                if normalized_assignment_id
                else {},
                session_id=normalized_session_id,
            )
            result["assignment_id"] = assignment.id
            result["session_id"] = assignment.session_id
            if normalized_destroy_result and not bool(
                normalized_destroy_result.get("success", True)
            ):
                assignment.state = EphemeralAssignmentState.FAILED
                assignment.error = (
                    _optional_text(normalized_destroy_result.get("message")) or "cleanup failed"
                )
                result["error"] = assignment.error
            else:
                assignment.state = EphemeralAssignmentState.CLEANED_UP
                assignment.error = None
                result["success"] = True
            assignment.updated_at = utc_now()
            result["state"] = assignment.state.value
            return state_model

        state = self._repository.update(_mutate, expected_revision=expected_revision)
        result["revision"] = state.revision
        return state, result
