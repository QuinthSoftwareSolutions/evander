from __future__ import annotations

from typing import Any
from uuid import uuid4

from evander.models import DirectoryPath

from ...git_naming import ephemeral_agent_branch
from ...models import (
    AGENT_OPERATOR_ONLY_FIELDS,
    AgentInboxItem,
    AgentTask,
    Deliverable,
    DeliverableGitRefs,
    EphemeralAssignment,
    EphemeralAssignmentState,
    PersistentAgent,
    TeamInboxItemType,
    TeamInboxPriority,
    TeamRecordState,
    TeamState,
    TeamTaskState,
    agent_has_project_assignment,
)

from .common import (
    EPHEMERAL_WORKER_REPORT_TOOL,
    _append_unique,
    _json_dumps,
    _optional_mapping,
    _optional_text,
    _require_absent,
    _require_present,
    _required_text,
    _set_task_state,
    _string_list,
    utc_now,
)
from .task_assessment import (
    require_deliverable_submission,
    require_task_deliverable_contract_provenance,
    task_payload_with_assessment,
)


EPHEMERAL_REQUESTED_EVENT_TYPE = "team.ephemeral.requested"

EPHEMERAL_COMPLETED_EVENT_TYPE = "team.ephemeral.completed"


def _ephemeral_request_result() -> dict[str, Any]:
    return {
        "success": True,
        "applied": True,
        "errors": [],
        "created_ephemeral_assignment_ids": [],
        "events": [],
    }


def _create_ephemeral_assignments(
    state: TeamState,
    *,
    parent: PersistentAgent,
    parent_directory_path: str,
    requests: list[dict[str, Any]],
    dispatch_id: str | None,
    result: dict[str, Any],
) -> None:
    if not requests:
        return
    normalized_parent_directory_path = DirectoryPath(segments=parent_directory_path).as_posix()
    active_count = len(_active_ephemeral_assignments_for_parent(state, parent.id))
    max_concurrent = parent.ephemeral_policy.max_concurrent
    if max_concurrent <= 0:
        raise ValueError(f"agent {parent.id} is not allowed to spawn ephemeral agents")
    if active_count + len(requests) > max_concurrent:
        raise ValueError(
            f"agent {parent.id} ephemeral request would exceed max_concurrent={max_concurrent}"
        )

    normalized: list[tuple[dict[str, Any], AgentTask]] = []
    seen_ids: set[str] = set()
    for request in requests:
        requested_directory_path = _optional_text(request.get("directory_path"))
        if requested_directory_path is not None:
            normalized_requested_directory_path = DirectoryPath(
                segments=requested_directory_path
            ).as_posix()
            if normalized_requested_directory_path != normalized_parent_directory_path:
                raise ValueError(
                    "ephemeral worker directory_path must match the parent agent backing "
                    f"session directory {normalized_parent_directory_path!r}"
                )
        # The parent backing session is authoritative.  Preserve the derived value in
        # both the assignment and the event that the generated workflow pack consumes.
        request["directory_path"] = normalized_parent_directory_path
        task_id = _required_text(request.get("task_id"), "task_id")
        task = _require_present(state.tasks, task_id, "task")
        if task.owner_agent_id != parent.id:
            raise ValueError(f"agent {parent.id} cannot spawn ephemeral work for task {task_id}")
        if not agent_has_project_assignment(state, parent.id, task.project_id):
            raise ValueError(f"agent {parent.id} is not assigned to project {task.project_id}")
        assignment_id = _optional_text(request.get("id")) or f"ephemeral-{uuid4().hex}"
        if assignment_id in seen_ids:
            raise ValueError(f"duplicate ephemeral assignment id {assignment_id}")
        _require_absent(state.ephemeral_assignments, assignment_id, "ephemeral assignment")
        request["id"] = assignment_id
        seen_ids.add(assignment_id)
        normalized.append((request, task))

    now = utc_now()
    for request, task in normalized:
        assignment_id = _required_text(request.get("id"), "id")
        session_id = _optional_text(request.get("session_id")) or f"team-eph-{uuid4().hex}"
        assignment = EphemeralAssignment(
            id=assignment_id,
            parent_agent_id=parent.id,
            project_id=task.project_id,
            task_id=task.id,
            session_id=session_id,
            title=_required_text(request.get("title"), "title"),
            prompt=_required_text(request.get("prompt"), "prompt"),
            state=EphemeralAssignmentState.REQUESTED,
            model=_optional_text(request.get("model")),
            agent_provider=_optional_text(request.get("agent_provider")),
            execution_backend=(
                _optional_text(request.get("execution_backend")) or "container_worker_vm"
            ),
            container_image=_optional_text(request.get("container_image")),
            allowed_tools=_string_list(request.get("allowed_tools"), "allowed_tools"),
            ttl_hours=int(request["ttl_hours"]) if request.get("ttl_hours") is not None else None,
            directory_path=normalized_parent_directory_path,
            metadata=_optional_mapping(request.get("metadata"), "metadata") or {},
            created_at=now,
            updated_at=now,
        )
        state.ephemeral_assignments[assignment.id] = assignment
        result["created_ephemeral_assignment_ids"].append(assignment.id)


def _active_ephemeral_assignments_for_parent(
    state: TeamState,
    parent_agent_id: str,
) -> list[EphemeralAssignment]:
    active_states = {
        EphemeralAssignmentState.REQUESTED,
        EphemeralAssignmentState.SPAWN_PENDING,
        EphemeralAssignmentState.RUNNING,
    }
    return [
        assignment
        for assignment in state.ephemeral_assignments.values()
        if assignment.parent_agent_id == parent_agent_id and assignment.state in active_states
    ]


def _resolve_ephemeral_assignment(
    state: TeamState,
    *,
    action: dict[str, Any],
    session_id: str | None,
) -> EphemeralAssignment:
    assignment_id = _optional_text(action.get("ephemeral_assignment_id")) or _optional_text(
        action.get("assignment_id")
    )
    if assignment_id:
        return _require_present(
            state.ephemeral_assignments,
            assignment_id,
            "ephemeral assignment",
        )
    if session_id:
        for assignment in state.ephemeral_assignments.values():
            if assignment.session_id == session_id:
                return assignment
    raise ValueError("ephemeral action requires assignment_id or a known session_id")


def _apply_ephemeral_acceptance(
    state: TeamState,
    *,
    assignment: EphemeralAssignment,
    accepted: bool,
    deliverable_id: str | None,
    rejection_reason: str | None,
) -> str | None:
    if accepted:
        if assignment.state not in {
            EphemeralAssignmentState.COMPLETED,
            EphemeralAssignmentState.ACCEPTED,
            EphemeralAssignmentState.CLEANUP_PENDING,
            EphemeralAssignmentState.CLEANED_UP,
        }:
            raise ValueError(f"ephemeral assignment {assignment.id} is not completed")
        metadata = dict(assignment.metadata)
        existing_deliverable_id = _optional_text(metadata.get("accepted_deliverable_id"))
        if existing_deliverable_id is not None:
            if deliverable_id is not None and deliverable_id != existing_deliverable_id:
                raise ValueError(
                    f"ephemeral assignment {assignment.id} was already accepted "
                    f"as deliverable {existing_deliverable_id}"
                )
            _require_present(state.deliverables, existing_deliverable_id, "deliverable")
            assignment.state = EphemeralAssignmentState.ACCEPTED
            assignment.error = None
            assignment.updated_at = utc_now()
            return existing_deliverable_id
        deliverable_id_value = deliverable_id or f"deliverable-{uuid4().hex}"
        _require_absent(state.deliverables, deliverable_id_value, "deliverable")
        task = _require_present(state.tasks, assignment.task_id, "task")
        if task.integration_boundary:
            require_deliverable_submission(
                state,
                task_id=task.id,
                source_deliverable_ids=[],
            )
            require_task_deliverable_contract_provenance(
                state,
                task_id=task.id,
                git_refs=DeliverableGitRefs(),
                producer_agent_id=assignment.parent_agent_id,
                deliverable_id=deliverable_id_value,
                require_submitted_context=False,
            )
        deliverable = Deliverable(
            id=deliverable_id_value,
            task_id=assignment.task_id,
            producer_agent_id=assignment.parent_agent_id,
            summary=_required_text(assignment.summary, "summary"),
            artifact_refs=list(assignment.artifact_refs),
            state=TeamRecordState.PENDING,
        )
        state.deliverables[deliverable.id] = deliverable
        task.deliverable_ids = _append_unique(task.deliverable_ids, deliverable.id)
        now = utc_now()
        _set_task_state(task, TeamTaskState.WAITING_FOR_REVIEW, now=now)
        task.updated_at = now
        assignment.state = EphemeralAssignmentState.ACCEPTED
        assignment.error = None
        metadata["accepted_deliverable_id"] = deliverable.id
        assignment.metadata = metadata
        assignment.updated_at = utc_now()
        return deliverable.id

    assignment.state = EphemeralAssignmentState.REJECTED
    assignment.error = rejection_reason
    assignment.updated_at = utc_now()
    return None


def _create_ephemeral_parent_notice(
    state: TeamState,
    assignment: EphemeralAssignment,
) -> str:
    inbox_id = f"inbox-{uuid4().hex}"
    state.inbox[inbox_id] = AgentInboxItem(
        id=inbox_id,
        recipient_agent_id=assignment.parent_agent_id,
        sender_agent_id=None,
        task_id=assignment.task_id,
        item_type=TeamInboxItemType.DELIVERABLE_NOTICE,
        response_requested=True,
        priority=TeamInboxPriority.LOW,
        payload={
            "ephemeral_assignment_id": assignment.id,
            "summary": assignment.summary,
            "artifact_refs": list(assignment.artifact_refs),
        },
    )
    return inbox_id


def _ephemeral_assignment_event_payload(
    state: TeamState,
    assignment_id: str,
    *,
    event_type: str = EPHEMERAL_REQUESTED_EVENT_TYPE,
) -> dict[str, Any]:
    assignment = _require_present(
        state.ephemeral_assignments,
        assignment_id,
        "ephemeral assignment",
    )
    parent = _require_present(state.agents, assignment.parent_agent_id, "parent agent")
    task = _require_present(state.tasks, assignment.task_id, "task")
    metadata = dict(assignment.metadata)
    team_metadata = (
        dict(metadata.get("team_addon")) if isinstance(metadata.get("team_addon"), dict) else {}
    )
    team_metadata.update(
        {
            "ephemeral_assignment_id": assignment.id,
            "parent_agent_id": assignment.parent_agent_id,
            "team_id": parent.team_id,
            "project_id": assignment.project_id,
            "task_id": assignment.task_id,
            "ephemeral": True,
        }
    )
    metadata["team_addon"] = team_metadata
    assignment_payload = assignment.model_dump(mode="json")
    return {
        "event_type": event_type,
        "correlation_key": assignment.id,
        "idempotency_key": f"team-addon:{event_type}:{assignment.id}",
        "payload": {
            "assignment": assignment_payload,
            "assignment_id": assignment.id,
            "parent_agent_id": assignment.parent_agent_id,
            "project_id": assignment.project_id,
            "task_id": assignment.task_id,
            "session_id": assignment.session_id,
            "title": assignment.title,
            "prompt": _build_ephemeral_prompt(
                state=state,
                assignment=assignment,
                parent=parent,
                task=task,
            ),
            "summary": assignment.summary,
            "artifact_refs": list(assignment.artifact_refs),
            "model": assignment.model,
            "agent_provider": assignment.agent_provider,
            "execution_backend": assignment.execution_backend or "container_worker_vm",
            "container_image": assignment.container_image,
            "allowed_tools": [EPHEMERAL_WORKER_REPORT_TOOL],
            "agent_branch": (
                assignment.agent_branch
                or ephemeral_agent_branch(assignment.session_id, assignment.id)
            ),
            "ttl_hours": assignment.ttl_hours,
            "directory_path": assignment.directory_path,
            "metadata": metadata,
        },
    }


def _build_ephemeral_prompt(
    *,
    state: TeamState,
    assignment: EphemeralAssignment,
    parent: PersistentAgent,
    task: AgentTask,
) -> str:
    context = {
        "assignment": assignment.model_dump(mode="json"),
        "parent_agent": parent.model_dump(
            mode="json",
            exclude=AGENT_OPERATOR_ONLY_FIELDS,
        ),
        "task": task_payload_with_assessment(state, task),
        "report_tool_name": EPHEMERAL_WORKER_REPORT_TOOL,
        "expected_agent_branch": ephemeral_agent_branch(
            assignment.session_id,
            assignment.id,
        ),
    }
    schema = {
        "assignment_id": assignment.id,
        "summary": "",
        "artifact_refs": [],
        "agent_branch": ephemeral_agent_branch(assignment.session_id, assignment.id),
        "commit_sha": None,
        "error": None,
        "request_id": "stable-result-id",
    }
    return (
        "Complete this scoped ephemeral team-agent assignment. "
        "You are a disposable worker for one parent agent; do not spawn other agents, "
        "ask hierarchy questions, or update unrelated team state. The only Team MCP "
        f"tool granted to this worker is {EPHEMERAL_WORKER_REPORT_TOOL}.\n\n"
        f"Assignment prompt:\n{assignment.prompt}\n\n"
        f"Assignment context JSON:\n{_json_dumps(context)}\n\n"
        f"Before finishing, call {EPHEMERAL_WORKER_REPORT_TOOL}. "
        "For successful work include summary, artifact_refs, request_id, and "
        "agent_branch when you created a branch. For failed work include error "
        "and request_id. Use this argument shape:\n"
        f"{_json_dumps(schema)}\n"
    )


__all__ = [
    "EPHEMERAL_REQUESTED_EVENT_TYPE",
    "EPHEMERAL_COMPLETED_EVENT_TYPE",
    "_ephemeral_request_result",
    "_create_ephemeral_assignments",
    "_active_ephemeral_assignments_for_parent",
    "_resolve_ephemeral_assignment",
    "_apply_ephemeral_acceptance",
    "_create_ephemeral_parent_notice",
    "_ephemeral_assignment_event_payload",
    "_build_ephemeral_prompt",
]
