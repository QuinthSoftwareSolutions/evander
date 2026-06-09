from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import ValidationError

from agent_sandbox.addons import addon_json_object_store
from agent_sandbox.models import utc_now
from agent_sandbox.storage import JsonObjectStore

from .git_naming import (
    canonical_release_branch,
    ephemeral_agent_branch,
    feature_branch,
    release_branch,
    review_branch,
    validate_branch_name,
)
from .models import (
    AgentActionRecord,
    AgentInboxItem,
    AgentRuntime,
    AgentTask,
    Deliverable,
    EphemeralAssignment,
    EphemeralAssignmentState,
    EphemeralAgentPolicy,
    AgentStatus,
    GitPromotionRecord,
    PersistentAgent,
    Project,
    ProjectGoal,
    ProjectStatus,
    ReleasePackage,
    ReviewRequest,
    Team,
    TeamInboxItemState,
    TeamInboxItemType,
    TeamRecordState,
    TeamState,
    TeamTaskState,
)

ADDON_ID = "team-addon"
STATE_FILE = "state.json"
EPHEMERAL_REQUESTED_EVENT_TYPE = "team.ephemeral.requested"
EPHEMERAL_COMPLETED_EVENT_TYPE = "team.ephemeral.completed"
OPERATOR_ESCALATION_EVENT_TYPE = "team.operator.escalation_requested"
GIT_PROMOTION_EVENT_TYPE = "team.git.promotion_requested"

_NON_TERMINAL_TASK_STATES = frozenset(
    {
        TeamTaskState.PENDING,
        TeamTaskState.IN_PROGRESS,
        TeamTaskState.BLOCKED,
        TeamTaskState.WAITING_FOR_REVIEW,
    }
)


class TeamStateConflictError(RuntimeError):
    def __init__(self, *, expected_revision: int, current_revision: int) -> None:
        super().__init__(
            f"stale team add-on state revision: expected {expected_revision}, "
            f"current {current_revision}"
        )
        self.expected_revision = expected_revision
        self.current_revision = current_revision


class TeamStateRepository:
    def __init__(self, runtime_dir: str | Path) -> None:
        self.runtime_dir = Path(runtime_dir)
        self.store: JsonObjectStore[Any] = addon_json_object_store(
            self.runtime_dir,
            ADDON_ID,
            STATE_FILE,
            initial_object=TeamState.empty().model_dump(mode="json"),
        )

    def read(self) -> TeamState:
        return TeamState.model_validate(self.store.read_object())

    def update(
        self,
        mutator: Callable[[TeamState], TeamState | None],
        *,
        expected_revision: int | None = None,
    ) -> TeamState:
        def _update(payload: dict[str, Any]) -> TeamState:
            state = TeamState.model_validate(payload)
            if expected_revision is not None and state.revision != expected_revision:
                raise TeamStateConflictError(
                    expected_revision=expected_revision,
                    current_revision=state.revision,
                )
            next_state = mutator(state) or state
            now = utc_now()
            next_state = next_state.model_copy(
                update={"revision": state.revision + 1, "updated_at": now},
                deep=True,
            )
            validated = TeamState.model_validate(next_state.model_dump(mode="json"))
            payload.clear()
            payload.update(validated.model_dump(mode="json"))
            return validated

        return self.store.update_object(_update)  # type: ignore[return-value]

    def create_team(
        self,
        *,
        team_id: str,
        name: str,
        manager_agent_id: str,
        manager_session_id: str,
        manager_role: str = "manager",
        expected_revision: int | None = None,
    ) -> TeamState:
        def _mutate(state: TeamState) -> TeamState:
            _require_absent(state.teams, team_id, "team")
            _require_absent(state.agents, manager_agent_id, "agent")
            now = utc_now()
            manager = PersistentAgent(
                id=manager_agent_id,
                team_id=team_id,
                role=manager_role,
                session_id=manager_session_id,
                reports_to_agent_id=None,
            )
            state.agents[manager.id] = manager
            state.teams[team_id] = Team(
                id=team_id,
                name=name,
                manager_agent_id=manager.id,
                created_at=now,
                updated_at=now,
            )
            return state

        return self.update(_mutate, expected_revision=expected_revision)

    def create_project(
        self,
        *,
        project_id: str,
        team_id: str,
        name: str,
        lead_agent_id: str,
        lead_session_id: str,
        goals: list[ProjectGoal] | None = None,
        lead_role: str = "project_lead",
        expected_revision: int | None = None,
    ) -> TeamState:
        def _mutate(state: TeamState) -> TeamState:
            team = _require_present(state.teams, team_id, "team")
            _require_absent(state.projects, project_id, "project")
            _require_absent(state.agents, lead_agent_id, "agent")
            now = utc_now()
            lead = PersistentAgent(
                id=lead_agent_id,
                team_id=team_id,
                project_id=project_id,
                role=lead_role,
                session_id=lead_session_id,
                reports_to_agent_id=team.manager_agent_id,
            )
            state.agents[lead.id] = lead
            manager = state.agents[team.manager_agent_id]
            manager.subordinate_agent_ids = _append_unique(manager.subordinate_agent_ids, lead.id)
            state.projects[project_id] = Project(
                id=project_id,
                team_id=team_id,
                name=name,
                lead_agent_id=lead.id,
                goals=goals or [],
                created_at=now,
                updated_at=now,
            )
            team.project_ids = _append_unique(team.project_ids, project_id)
            team.updated_at = now
            return state

        return self.update(_mutate, expected_revision=expected_revision)

    def register_agent(
        self,
        *,
        agent_id: str,
        team_id: str,
        project_id: str,
        role: str,
        session_id: str,
        reports_to_agent_id: str,
        ephemeral_policy: EphemeralAgentPolicy | None = None,
        expected_revision: int | None = None,
    ) -> TeamState:
        def _mutate(state: TeamState) -> TeamState:
            _require_absent(state.agents, agent_id, "agent")
            _require_present(state.teams, team_id, "team")
            project = _require_present(state.projects, project_id, "project")
            if project.team_id != team_id:
                raise ValueError(f"project {project_id} does not belong to team {team_id}")
            superior = _require_present(state.agents, reports_to_agent_id, "superior agent")
            if superior.team_id != team_id:
                raise ValueError(f"superior agent {reports_to_agent_id} is not in team {team_id}")
            agent = PersistentAgent(
                id=agent_id,
                team_id=team_id,
                project_id=project_id,
                role=role,
                session_id=session_id,
                reports_to_agent_id=reports_to_agent_id,
                ephemeral_policy=ephemeral_policy or EphemeralAgentPolicy(),
            )
            state.agents[agent.id] = agent
            superior.subordinate_agent_ids = _append_unique(superior.subordinate_agent_ids, agent.id)
            return state

        return self.update(_mutate, expected_revision=expected_revision)

    def create_or_reprioritize_task(
        self,
        *,
        task_id: str,
        owner_agent_id: str,
        project_id: str,
        priority: int,
        title: str,
        description: str,
        created_by_agent_id: str,
        state: TeamTaskState = TeamTaskState.PENDING,
        expected_revision: int | None = None,
    ) -> TeamState:
        def _mutate(state_model: TeamState) -> TeamState:
            now = utc_now()
            if task_id in state_model.tasks:
                task = state_model.tasks[task_id]
                task.priority = priority
                task.title = title
                task.description = description
                task.updated_at = now
                return state_model
            state_model.tasks[task_id] = AgentTask(
                id=task_id,
                owner_agent_id=owner_agent_id,
                project_id=project_id,
                priority=priority,
                state=state,
                title=title,
                description=description,
                created_by_agent_id=created_by_agent_id,
                created_at=now,
                updated_at=now,
            )
            return state_model

        return self.update(_mutate, expected_revision=expected_revision)

    def update_task(
        self,
        *,
        task_id: str,
        priority: int | None = None,
        state: TeamTaskState | None = None,
        title: str | None = None,
        description: str | None = None,
        expected_revision: int | None = None,
    ) -> TeamState:
        def _mutate(state_model: TeamState) -> TeamState:
            task = _require_present(state_model.tasks, task_id, "task")
            if priority is not None:
                task.priority = priority
            if state is not None:
                task.state = state
            if title is not None:
                task.title = title
            if description is not None:
                task.description = description
            task.updated_at = utc_now()
            return state_model

        return self.update(_mutate, expected_revision=expected_revision)

    def record_deliverable(
        self,
        *,
        deliverable_id: str,
        task_id: str,
        producer_agent_id: str,
        summary: str,
        artifact_refs: list[str] | None = None,
        state: TeamRecordState = TeamRecordState.PENDING,
        expected_revision: int | None = None,
    ) -> TeamState:
        def _mutate(state_model: TeamState) -> TeamState:
            _require_absent(state_model.deliverables, deliverable_id, "deliverable")
            task = _require_present(state_model.tasks, task_id, "task")
            deliverable = Deliverable(
                id=deliverable_id,
                task_id=task_id,
                producer_agent_id=producer_agent_id,
                summary=summary,
                artifact_refs=artifact_refs or [],
                state=state,
            )
            state_model.deliverables[deliverable.id] = deliverable
            task.deliverable_ids = _append_unique(task.deliverable_ids, deliverable.id)
            task.updated_at = utc_now()
            return state_model

        return self.update(_mutate, expected_revision=expected_revision)

    def record_review_request(
        self,
        *,
        review_request_id: str,
        deliverable_id: str,
        reviewer_agent_id: str,
        state: TeamRecordState = TeamRecordState.PENDING,
        findings: list[str] | None = None,
        decision_at: datetime | None = None,
        expected_revision: int | None = None,
    ) -> TeamState:
        def _mutate(state_model: TeamState) -> TeamState:
            _require_absent(state_model.review_requests, review_request_id, "review request")
            deliverable = _require_present(state_model.deliverables, deliverable_id, "deliverable")
            review = ReviewRequest(
                id=review_request_id,
                deliverable_id=deliverable_id,
                reviewer_agent_id=reviewer_agent_id,
                state=state,
                findings=findings or [],
                decision_at=decision_at,
            )
            state_model.review_requests[review.id] = review
            deliverable.review_request_ids = _append_unique(
                deliverable.review_request_ids,
                review.id,
            )
            return state_model

        return self.update(_mutate, expected_revision=expected_revision)

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

        state = self.update(_mutate, expected_revision=expected_revision)
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

        state = self.update(_mutate, expected_revision=expected_revision)
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
                expected_branch = ephemeral_agent_branch(
                    assignment.session_id, assignment.id
                )
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
                inbox_id = f"inbox-{uuid4().hex}"
                state_model.inbox[inbox_id] = AgentInboxItem(
                    id=inbox_id,
                    recipient_agent_id=assignment.parent_agent_id,
                    sender_agent_id=None,
                    task_id=assignment.task_id,
                    item_type=TeamInboxItemType.DELIVERABLE_NOTICE,
                    payload={
                        "ephemeral_assignment_id": assignment.id,
                        "summary": assignment.summary,
                        "artifact_refs": list(assignment.artifact_refs),
                    },
                )
                result["created_inbox_item_ids"].append(inbox_id)
            assignment.updated_at = utc_now()
            result["success"] = assignment.state == EphemeralAssignmentState.COMPLETED
            result["state"] = assignment.state.value
            result["error"] = assignment.error
            return state_model

        state = self.update(_mutate, expected_revision=expected_revision)
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

        state = self.update(_mutate, expected_revision=expected_revision)
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
            if normalized_destroy_result and not bool(normalized_destroy_result.get("success", True)):
                assignment.state = EphemeralAssignmentState.FAILED
                assignment.error = (
                    _optional_text(normalized_destroy_result.get("message"))
                    or "cleanup failed"
                )
                result["error"] = assignment.error
            else:
                assignment.state = EphemeralAssignmentState.CLEANED_UP
                assignment.error = None
                result["success"] = True
            assignment.updated_at = utc_now()
            result["state"] = assignment.state.value
            return state_model

        state = self.update(_mutate, expected_revision=expected_revision)
        result["revision"] = state.revision
        return state, result

    def lease_due_agents(
        self,
        *,
        now: datetime | None = None,
        limit: int = 10,
        lease_seconds: int = 900,
        wake_interval_seconds: int = 900,
        max_no_progress_count: int = 3,
        team_id: str | None = None,
        project_id: str | None = None,
        agent_id: str | None = None,
    ) -> tuple[TeamState, list[dict[str, Any]]]:
        effective_now = _ensure_utc(now or utc_now())
        normalized_team_id = _optional_text(team_id)
        normalized_project_id = _optional_text(project_id)
        normalized_agent_id = _optional_text(agent_id)
        normalized_limit = max(0, int(limit))
        leases: list[dict[str, Any]] = []

        def _mutate(state_model: TeamState) -> TeamState:
            for agent in sorted(state_model.agents.values(), key=lambda item: item.id):
                if normalized_limit and len(leases) >= normalized_limit:
                    break
                if agent.status != AgentStatus.ACTIVE:
                    continue
                if normalized_team_id is not None and agent.team_id != normalized_team_id:
                    continue
                if normalized_project_id is not None and agent.project_id != normalized_project_id:
                    continue
                if normalized_agent_id is not None and agent.id != normalized_agent_id:
                    continue
                runtime = state_model.agent_runtimes.setdefault(
                    agent.id,
                    AgentRuntime(agent_id=agent.id),
                )
                if (
                    runtime.wake_leased_until is not None
                    and _ensure_utc(runtime.wake_leased_until) > effective_now
                ):
                    continue
                active_tasks = _active_tasks_for_agent(state_model, agent.id)
                pending_inbox = _pending_inbox_for_agent(state_model, agent.id)
                if not active_tasks and not pending_inbox:
                    continue
                due_by_time = (
                    runtime.next_wake_at is None
                    or _ensure_utc(runtime.next_wake_at) <= effective_now
                )
                if not pending_inbox and not due_by_time:
                    continue
                if (
                    max_no_progress_count >= 0
                    and runtime.no_progress_count >= max_no_progress_count
                    and not pending_inbox
                ):
                    continue

                dispatch_id = f"team-dispatch-{uuid4().hex}"
                reason = "inbox" if pending_inbox else "scheduled"
                runtime.active_dispatch_id = dispatch_id
                runtime.wake_reason = reason
                runtime.last_wake_at = effective_now
                runtime.wake_leased_until = effective_now + timedelta(seconds=lease_seconds)
                runtime.next_wake_at = effective_now + timedelta(seconds=wake_interval_seconds)
                runtime.updated_at = effective_now
                leases.append(
                    {
                        "agent": agent.model_dump(mode="json"),
                        "runtime": runtime.model_dump(mode="json"),
                        "dispatch_id": dispatch_id,
                        "reason": reason,
                        "tasks": [task.model_dump(mode="json") for task in active_tasks],
                        "inbox": [item.model_dump(mode="json") for item in pending_inbox],
                        "superior_agent_id": agent.reports_to_agent_id,
                        "subordinate_agent_ids": list(agent.subordinate_agent_ids),
                    }
                )
            return state_model

        if normalized_limit == 0:
            return self.read(), []
        state = self.update(_mutate)
        return state, leases

    def mirror_session_metadata_payload(
        self,
        *,
        state: TeamState,
        agent_id: str,
    ) -> dict[str, Any]:
        agent = _require_present(state.agents, agent_id, "agent")
        active_task_ids = [task.id for task in _active_tasks_for_agent(state, agent_id)]
        pending_inbox_ids = [item.id for item in _pending_inbox_for_agent(state, agent_id)]
        return {
            "team_addon": {
                "agent_id": agent.id,
                "team_id": agent.team_id,
                "project_id": agent.project_id,
                "role": agent.role,
                "reports_to_agent_id": agent.reports_to_agent_id,
                "subordinate_agent_ids": list(agent.subordinate_agent_ids),
                "active_task_ids": active_task_ids,
                "pending_inbox_item_ids": pending_inbox_ids,
            }
        }

    def apply_agent_action(
        self,
        *,
        action: dict[str, Any] | None,
        session_id: str | None = None,
        dispatch_id: str | None = None,
        valid: bool = True,
        error: str | None = None,
        expected_revision: int | None = None,
    ) -> tuple[TeamState, dict[str, Any]]:
        normalized_action = dict(action or {}) if isinstance(action, dict) else {}
        normalized_session_id = _optional_text(session_id)
        normalized_dispatch_id = _optional_text(dispatch_id) or _optional_text(
            normalized_action.get("dispatch_id")
        )
        result: dict[str, Any] = {
            "success": False,
            "applied": False,
            "agent_id": None,
            "errors": [],
            "created_inbox_item_ids": [],
            "created_deliverable_ids": [],
            "created_task_ids": [],
            "created_status_request_ids": [],
            "created_review_request_ids": [],
            "decided_review_request_ids": [],
            "updated_goal_ids": [],
            "operator_escalation_event_ids": [],
            "git_promotion_event_ids": [],
            "release_package_ids": [],
            "updated_task_ids": [],
            "handled_inbox_item_ids": [],
            "created_ephemeral_assignment_ids": [],
            "ephemeral_events": [],
            "events": [],
            "accepted_ephemeral_assignment_ids": [],
            "rejected_ephemeral_assignment_ids": [],
        }

        def _mutate(state_model: TeamState) -> TeamState:
            agent = _resolve_action_agent(
                state_model,
                action=normalized_action,
                session_id=normalized_session_id,
            )
            result["agent_id"] = agent.id
            runtime = state_model.agent_runtimes.setdefault(
                agent.id,
                AgentRuntime(agent_id=agent.id),
            )
            action_id = f"action-{uuid4().hex}"
            if not valid:
                _mark_action_failure(
                    state_model,
                    runtime=runtime,
                    agent_id=agent.id,
                    dispatch_id=normalized_dispatch_id or runtime.active_dispatch_id or action_id,
                    action=normalized_action,
                    error=error or "invalid action",
                    action_id=action_id,
                    result=result,
                )
                return state_model
            if normalized_dispatch_id and runtime.active_dispatch_id:
                if normalized_dispatch_id != runtime.active_dispatch_id:
                    _mark_action_failure(
                        state_model,
                        runtime=runtime,
                        agent_id=agent.id,
                        dispatch_id=normalized_dispatch_id,
                        action=normalized_action,
                        error="dispatch_id does not match active team runtime lease",
                        action_id=action_id,
                        result=result,
                    )
                    return state_model

            progress = False
            projects_to_recalc: set[str] = set()
            for update in _list_of_mappings(normalized_action.get("task_updates"), "task_updates"):
                task_id = _required_text(update.get("task_id"), "task_id")
                task = _require_present(state_model.tasks, task_id, "task")
                if task.owner_agent_id != agent.id:
                    result["errors"].append(f"agent {agent.id} cannot update task {task_id}")
                    continue
                raw_state = _optional_text(update.get("state"))
                if raw_state:
                    task.state = TeamTaskState(raw_state)
                    projects_to_recalc.add(task.project_id)
                    progress = True
                priority = update.get("priority")
                if priority is not None:
                    task.priority = int(priority)
                    progress = True
                title = _optional_text(update.get("title"))
                if title is not None:
                    task.title = title
                    progress = True
                description = _optional_text(update.get("description"))
                if description is not None:
                    task.description = description
                    progress = True
                task.updated_at = utc_now()
                result["updated_task_ids"].append(task.id)

            for creation in _list_of_mappings(
                normalized_action.get("task_creations"), "task_creations"
            ):
                created_id = _apply_task_creation(
                    state_model,
                    actor=agent,
                    creation=creation,
                    result=result,
                )
                if created_id is not None:
                    result["created_task_ids"].append(created_id)
                    projects_to_recalc.add(state_model.tasks[created_id].project_id)
                    progress = True

            for question in _list_of_mappings(normalized_action.get("questions"), "questions"):
                recipient_id = (
                    _optional_text(question.get("recipient_agent_id"))
                    or agent.reports_to_agent_id
                )
                upward_error = _upward_recipient_error(agent, recipient_id, "questions")
                if upward_error is not None:
                    result["errors"].append(upward_error)
                    continue
                inbox_id = f"inbox-{uuid4().hex}"
                item = AgentInboxItem(
                    id=inbox_id,
                    recipient_agent_id=recipient_id,
                    sender_agent_id=agent.id,
                    task_id=_optional_text(question.get("task_id")),
                    item_type=TeamInboxItemType.QUESTION,
                    payload={
                        key: value
                        for key, value in question.items()
                        if key not in {"recipient_agent_id", "task_id"}
                    },
                )
                state_model.inbox[item.id] = item
                result["created_inbox_item_ids"].append(item.id)
                progress = True

            for report in _list_of_mappings(
                normalized_action.get("status_reports"), "status_reports"
            ):
                recipient_id = (
                    _optional_text(report.get("recipient_agent_id"))
                    or agent.reports_to_agent_id
                )
                upward_error = _upward_recipient_error(agent, recipient_id, "status_reports")
                if upward_error is not None:
                    result["errors"].append(upward_error)
                    continue
                inbox_id = f"inbox-{uuid4().hex}"
                item = AgentInboxItem(
                    id=inbox_id,
                    recipient_agent_id=recipient_id,
                    sender_agent_id=agent.id,
                    task_id=_optional_text(report.get("task_id")),
                    item_type=TeamInboxItemType.PROGRESS_REPORT,
                    payload={
                        key: value
                        for key, value in report.items()
                        if key not in {"recipient_agent_id", "task_id"}
                    },
                )
                state_model.inbox[item.id] = item
                result["created_inbox_item_ids"].append(item.id)
                progress = True

            for status_request in _list_of_mappings(
                normalized_action.get("status_requests"), "status_requests"
            ):
                recipient_id = _required_text(
                    status_request.get("recipient_agent_id"), "recipient_agent_id"
                )
                if not _is_direct_subordinate(agent, recipient_id):
                    result["errors"].append(
                        f"agent {agent.id} can only request status from a direct subordinate"
                    )
                    continue
                inbox_id = f"inbox-{uuid4().hex}"
                item = AgentInboxItem(
                    id=inbox_id,
                    recipient_agent_id=recipient_id,
                    sender_agent_id=agent.id,
                    task_id=_optional_text(status_request.get("task_id")),
                    item_type=TeamInboxItemType.STATUS_REQUEST,
                    payload={
                        key: value
                        for key, value in status_request.items()
                        if key not in {"recipient_agent_id", "task_id"}
                    },
                )
                state_model.inbox[item.id] = item
                result["created_status_request_ids"].append(item.id)
                progress = True

            for escalation in _list_of_mappings(
                normalized_action.get("operator_escalations"), "operator_escalations"
            ):
                if _apply_operator_escalation(
                    state_model,
                    actor=agent,
                    escalation=escalation,
                    result=result,
                ):
                    progress = True

            for deliverable_payload in _list_of_mappings(
                normalized_action.get("deliverables"), "deliverables"
            ):
                task_id = _required_text(deliverable_payload.get("task_id"), "task_id")
                task = _require_present(state_model.tasks, task_id, "task")
                if task.owner_agent_id != agent.id:
                    result["errors"].append(f"agent {agent.id} cannot deliver task {task_id}")
                    continue
                deliverable_id = _optional_text(deliverable_payload.get("id")) or f"deliverable-{uuid4().hex}"
                artifact_refs = _string_list(deliverable_payload.get("artifact_refs"), "artifact_refs")
                artifact_space_ref = _optional_text(deliverable_payload.get("artifact_space_ref"))
                if artifact_space_ref:
                    artifact_refs = _append_unique(artifact_refs, artifact_space_ref)
                deliverable = Deliverable(
                    id=deliverable_id,
                    task_id=task_id,
                    producer_agent_id=agent.id,
                    summary=_required_text(deliverable_payload.get("summary"), "summary"),
                    artifact_refs=artifact_refs,
                    state=TeamRecordState.PENDING,
                )
                state_model.deliverables[deliverable.id] = deliverable
                task.deliverable_ids = _append_unique(task.deliverable_ids, deliverable.id)
                task.state = TeamTaskState.WAITING_FOR_REVIEW
                task.updated_at = utc_now()
                projects_to_recalc.add(task.project_id)
                result["created_deliverable_ids"].append(deliverable.id)
                if agent.reports_to_agent_id is not None:
                    inbox_id = f"inbox-{uuid4().hex}"
                    state_model.inbox[inbox_id] = AgentInboxItem(
                        id=inbox_id,
                        recipient_agent_id=agent.reports_to_agent_id,
                        sender_agent_id=agent.id,
                        task_id=task_id,
                        item_type=TeamInboxItemType.DELIVERABLE_NOTICE,
                        payload={"deliverable_id": deliverable.id, "summary": deliverable.summary},
                    )
                    result["created_inbox_item_ids"].append(inbox_id)
                progress = True

            for review_request in _list_of_mappings(
                normalized_action.get("review_requests"), "review_requests"
            ):
                created_id = _apply_review_request(
                    state_model,
                    actor=agent,
                    request=review_request,
                    result=result,
                )
                if created_id is not None:
                    result["created_review_request_ids"].append(created_id)
                    progress = True

            for decision in _list_of_mappings(
                normalized_action.get("review_decisions"), "review_decisions"
            ):
                decided_id = _apply_review_decision(
                    state_model,
                    actor=agent,
                    decision=decision,
                    result=result,
                    projects_to_recalc=projects_to_recalc,
                )
                if decided_id is not None:
                    result["decided_review_request_ids"].append(decided_id)
                    progress = True

            for goal_update in _list_of_mappings(
                normalized_action.get("goal_updates"), "goal_updates"
            ):
                updated_id = _apply_goal_update(
                    state_model,
                    actor=agent,
                    update=goal_update,
                    result=result,
                    projects_to_recalc=projects_to_recalc,
                )
                if updated_id is not None:
                    result["updated_goal_ids"].append(updated_id)
                    progress = True

            for promotion in _list_of_mappings(
                normalized_action.get("git_review_promotions"), "git_review_promotions"
            ):
                promotion_id = _apply_git_review_promotion(
                    state_model,
                    actor=agent,
                    request=promotion,
                    result=result,
                )
                if promotion_id is not None:
                    result["git_promotion_event_ids"].append(promotion_id)
                    progress = True

            for feature_update in _list_of_mappings(
                normalized_action.get("git_feature_updates"), "git_feature_updates"
            ):
                promotion_id = _apply_git_feature_update(
                    state_model,
                    actor=agent,
                    update=feature_update,
                    result=result,
                )
                if promotion_id is not None:
                    result["git_promotion_event_ids"].append(promotion_id)
                    progress = True

            for release_request in _list_of_mappings(
                normalized_action.get("git_release_requests"), "git_release_requests"
            ):
                release_id = _apply_git_release_request(
                    state_model,
                    actor=agent,
                    request=release_request,
                    result=result,
                    projects_to_recalc=projects_to_recalc,
                )
                if release_id is not None:
                    result["release_package_ids"].append(release_id)
                    progress = True

            for inbox_item_id in _string_list(
                normalized_action.get("handled_inbox_item_ids"),
                "handled_inbox_item_ids",
            ):
                item = state_model.inbox.get(inbox_item_id)
                if item is None or item.recipient_agent_id != agent.id:
                    result["errors"].append(f"agent {agent.id} cannot handle inbox item {inbox_item_id}")
                    continue
                item.state = TeamInboxItemState.HANDLED
                item.updated_at = utc_now()
                result["handled_inbox_item_ids"].append(inbox_item_id)
                progress = True

            ephemeral_requests = _list_of_mappings(
                normalized_action.get("ephemeral_requests"),
                "ephemeral_requests",
            )
            if ephemeral_requests:
                _create_ephemeral_assignments(
                    state_model,
                    parent=agent,
                    requests=ephemeral_requests,
                    dispatch_id=normalized_dispatch_id or runtime.active_dispatch_id,
                    result=result,
                )
                progress = True

            for acceptance in _list_of_mappings(
                normalized_action.get("ephemeral_acceptances"),
                "ephemeral_acceptances",
            ):
                assignment_id = _required_text(acceptance.get("assignment_id"), "assignment_id")
                assignment = _require_present(
                    state_model.ephemeral_assignments,
                    assignment_id,
                    "ephemeral assignment",
                )
                if assignment.parent_agent_id != agent.id:
                    result["errors"].append(
                        f"agent {agent.id} cannot accept ephemeral assignment {assignment_id}"
                    )
                    continue
                accepted = _bool_from_value(acceptance.get("accepted"), default=True)
                _apply_ephemeral_acceptance(
                    state_model,
                    assignment=assignment,
                    accepted=accepted,
                    deliverable_id=_optional_text(acceptance.get("deliverable_id")),
                    rejection_reason=_optional_text(acceptance.get("rejection_reason")),
                )
                if accepted:
                    result["accepted_ephemeral_assignment_ids"].append(assignment.id)
                else:
                    result["rejected_ephemeral_assignment_ids"].append(assignment.id)
                progress = True

            for project_id in sorted(projects_to_recalc):
                _recalculate_project_status(state_model, project_id)

            record = AgentActionRecord(
                id=action_id,
                agent_id=agent.id,
                dispatch_id=normalized_dispatch_id or runtime.active_dispatch_id or action_id,
                action=normalized_action,
                success=not result["errors"],
                error="; ".join(result["errors"]) if result["errors"] else None,
            )
            state_model.action_records[record.id] = record
            runtime.active_dispatch_id = None
            runtime.wake_leased_until = None
            runtime.wake_reason = None
            runtime.no_progress_count = 0 if progress and not result["errors"] else runtime.no_progress_count + 1
            if progress and not result["errors"]:
                runtime.last_progress_at = utc_now()
            runtime.updated_at = utc_now()
            result["success"] = not result["errors"]
            result["applied"] = not result["errors"]
            return state_model

        state = self.update(_mutate, expected_revision=expected_revision)
        result["revision"] = state.revision
        result["ephemeral_events"] = [
            _ephemeral_assignment_event_payload(state, assignment_id)
            for assignment_id in result["created_ephemeral_assignment_ids"]
        ]
        return state, result


def _require_present(collection: dict[str, Any], key: str, label: str) -> Any:
    if key not in collection:
        raise ValueError(f"{label} {key} does not exist")
    return collection[key]


def _require_absent(collection: dict[str, Any], key: str, label: str) -> None:
    if key in collection:
        raise ValueError(f"{label} {key} already exists")


def _append_unique(values: list[str], value: str) -> list[str]:
    if value in values:
        return values
    return [*values, value]


def _required_text(value: object, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field_name} must be a non-empty string")
    return text


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _active_tasks_for_agent(state: TeamState, agent_id: str) -> list[AgentTask]:
    terminal = {TeamTaskState.FINISHED, TeamTaskState.ACCEPTED}
    tasks = [
        task
        for task in state.tasks.values()
        if task.owner_agent_id == agent_id and task.state not in terminal
    ]
    tasks.sort(key=lambda item: (item.priority, item.created_at, item.id))
    return tasks


def _pending_inbox_for_agent(state: TeamState, agent_id: str) -> list[AgentInboxItem]:
    items = [
        item
        for item in state.inbox.values()
        if item.recipient_agent_id == agent_id and item.state == TeamInboxItemState.PENDING
    ]
    items.sort(key=lambda item: (item.created_at, item.id))
    return items


def _resolve_action_agent(
    state: TeamState,
    *,
    action: dict[str, Any],
    session_id: str | None,
) -> PersistentAgent:
    action_agent_id = _optional_text(action.get("agent_id"))
    if action_agent_id:
        return _require_present(state.agents, action_agent_id, "agent")
    if session_id:
        for agent in state.agents.values():
            if agent.session_id == session_id:
                return agent
    raise ValueError("agent action requires agent_id or a known session_id")


def _mark_action_failure(
    state: TeamState,
    *,
    runtime: AgentRuntime,
    agent_id: str,
    dispatch_id: str,
    action: dict[str, Any],
    error: str,
    action_id: str,
    result: dict[str, Any],
) -> None:
    runtime.active_dispatch_id = None
    runtime.wake_leased_until = None
    runtime.wake_reason = None
    runtime.no_progress_count += 1
    runtime.updated_at = utc_now()
    state.action_records[action_id] = AgentActionRecord(
        id=action_id,
        agent_id=agent_id,
        dispatch_id=dispatch_id,
        action=action,
        success=False,
        error=error,
    )
    result["errors"].append(error)


def _is_direct_subordinate(actor: PersistentAgent, agent_id: str) -> bool:
    return agent_id in actor.subordinate_agent_ids


def _is_team_manager(state: TeamState, actor: PersistentAgent) -> bool:
    if actor.reports_to_agent_id is not None:
        return False
    team = state.teams.get(actor.team_id)
    return team is not None and team.manager_agent_id == actor.id


def _is_project_lead_for(state: TeamState, agent_id: str, project_id: str) -> bool:
    project = state.projects.get(project_id)
    return project is not None and project.lead_agent_id == agent_id


def _agent_in_project_hierarchy(state: TeamState, agent_id: str, project_id: str) -> bool:
    agent = state.agents.get(agent_id)
    return agent is not None and agent.project_id == project_id


def _upward_recipient_error(
    agent: PersistentAgent,
    recipient_id: str | None,
    kind: str,
) -> str | None:
    if agent.reports_to_agent_id is None:
        return (
            f"agent {agent.id} is a top-level manager and must use operator_escalations "
            f"instead of {kind}"
        )
    if recipient_id != agent.reports_to_agent_id:
        return (
            f"agent {agent.id} can only send {kind} to its direct superior "
            f"{agent.reports_to_agent_id}"
        )
    return None


def _apply_task_creation(
    state: TeamState,
    *,
    actor: PersistentAgent,
    creation: dict[str, Any],
    result: dict[str, Any],
) -> str | None:
    owner_id = _required_text(creation.get("owner_agent_id"), "owner_agent_id")
    project_id = _required_text(creation.get("project_id"), "project_id")
    owner = state.agents.get(owner_id)
    if owner is None:
        result["errors"].append(f"task creation references missing owner {owner_id}")
        return None
    project = state.projects.get(project_id)
    if project is None:
        result["errors"].append(f"task creation references missing project {project_id}")
        return None
    if owner.team_id != actor.team_id or project.team_id != actor.team_id:
        result["errors"].append(
            f"agent {actor.id} cannot create tasks outside its team"
        )
        return None
    if owner.id != actor.id and owner.project_id != project_id:
        result["errors"].append(
            f"agent {actor.id} cannot create a task for {owner_id} outside project {project_id}"
        )
        return None
    permitted = (
        owner.id == actor.id
        or _is_direct_subordinate(actor, owner.id)
        or (
            _is_project_lead_for(state, actor.id, project_id)
            and _agent_in_project_hierarchy(state, owner.id, project_id)
        )
        or (
            _is_team_manager(state, actor)
            and _is_project_lead_for(state, owner.id, project_id)
        )
    )
    if not permitted:
        result["errors"].append(
            f"agent {actor.id} is not permitted to create tasks for {owner_id}"
        )
        return None
    raw_state = _optional_text(creation.get("state"))
    if raw_state is not None:
        try:
            task_state = TeamTaskState(raw_state)
        except ValueError:
            result["errors"].append(f"task creation has invalid state {raw_state}")
            return None
        if task_state not in _NON_TERMINAL_TASK_STATES:
            result["errors"].append(
                f"task creation cannot start in terminal state {raw_state}"
            )
            return None
    else:
        task_state = TeamTaskState.PENDING
    task_id = _optional_text(creation.get("id")) or f"task-{uuid4().hex}"
    if task_id in state.tasks:
        result["errors"].append(f"task {task_id} already exists")
        return None
    priority = creation.get("priority")
    now = utc_now()
    state.tasks[task_id] = AgentTask(
        id=task_id,
        owner_agent_id=owner_id,
        project_id=project_id,
        priority=int(priority) if priority is not None else 0,
        state=task_state,
        title=_required_text(creation.get("title"), "title"),
        description=_required_text(creation.get("description"), "description"),
        created_by_agent_id=actor.id,
        created_at=now,
        updated_at=now,
    )
    return task_id


def _apply_operator_escalation(
    state: TeamState,
    *,
    actor: PersistentAgent,
    escalation: dict[str, Any],
    result: dict[str, Any],
) -> bool:
    if not _is_team_manager(state, actor):
        result["errors"].append(
            f"agent {actor.id} is not permitted to send operator escalations"
        )
        return False
    title = _required_text(escalation.get("title"), "title")
    message = _required_text(escalation.get("message"), "message")
    urgency = _optional_text(escalation.get("urgency")) or "normal"
    project_id = _optional_text(escalation.get("project_id"))
    task_id = _optional_text(escalation.get("task_id"))
    team = state.teams.get(actor.team_id)
    target = (
        _optional_text(team.operator_communication_target_id) if team is not None else None
    )
    if not target:
        result["errors"].append(
            f"team {actor.team_id} has no operator_communication_target_id for escalations"
        )
        return False
    escalation_id = f"escalation-{uuid4().hex}"
    payload: dict[str, Any] = {
        "team_id": actor.team_id,
        "manager_agent_id": actor.id,
        "communication_target_id": target,
        "title": title,
        "message": message,
        "urgency": urgency,
    }
    if project_id is not None:
        payload["project_id"] = project_id
    if task_id is not None:
        payload["task_id"] = task_id
    inbox_id = f"inbox-{uuid4().hex}"
    state.inbox[inbox_id] = AgentInboxItem(
        id=inbox_id,
        recipient_agent_id=actor.id,
        sender_agent_id=actor.id,
        task_id=task_id,
        item_type=TeamInboxItemType.ESCALATION,
        payload={"escalation_id": escalation_id, **payload},
    )
    result["created_inbox_item_ids"].append(inbox_id)
    result["events"].append(
        {
            "event_type": OPERATOR_ESCALATION_EVENT_TYPE,
            "correlation_key": escalation_id,
            "idempotency_key": (
                f"team-addon:{OPERATOR_ESCALATION_EVENT_TYPE}:{escalation_id}"
            ),
            "payload": payload,
        }
    )
    result["operator_escalation_event_ids"].append(escalation_id)
    return True


def _supervises(state: TeamState, supervisor_id: str, agent_id: str) -> bool:
    """Return True if supervisor_id is an ancestor of agent_id in the reporting chain."""

    seen: set[str] = set()
    current = state.agents.get(agent_id)
    while current is not None and current.reports_to_agent_id is not None:
        superior_id = current.reports_to_agent_id
        if superior_id in seen:
            break
        seen.add(superior_id)
        if superior_id == supervisor_id:
            return True
        current = state.agents.get(superior_id)
    return False


def _apply_review_request(
    state: TeamState,
    *,
    actor: PersistentAgent,
    request: dict[str, Any],
    result: dict[str, Any],
) -> str | None:
    deliverable_id = _required_text(request.get("deliverable_id"), "deliverable_id")
    deliverable = state.deliverables.get(deliverable_id)
    if deliverable is None:
        result["errors"].append(f"review request references missing deliverable {deliverable_id}")
        return None
    reviewer_id = _required_text(request.get("reviewer_agent_id"), "reviewer_agent_id")
    reviewer = state.agents.get(reviewer_id)
    if reviewer is None:
        result["errors"].append(f"review request references missing reviewer {reviewer_id}")
        return None
    if reviewer.team_id != actor.team_id:
        result["errors"].append(
            f"reviewer {reviewer_id} is not in the same team as {actor.id}"
        )
        return None
    task = state.tasks.get(deliverable.task_id)
    project_id = task.project_id if task is not None else None
    permitted = (
        actor.id == deliverable.producer_agent_id
        or _supervises(state, actor.id, deliverable.producer_agent_id)
        or (project_id is not None and _is_project_lead_for(state, actor.id, project_id))
        or _is_team_manager(state, actor)
    )
    if not permitted:
        result["errors"].append(
            f"agent {actor.id} is not permitted to request review of deliverable {deliverable_id}"
        )
        return None
    review_id = _optional_text(request.get("id")) or f"review-{uuid4().hex}"
    if review_id in state.review_requests:
        result["errors"].append(f"review request {review_id} already exists")
        return None
    review = ReviewRequest(
        id=review_id,
        deliverable_id=deliverable_id,
        reviewer_agent_id=reviewer_id,
        state=TeamRecordState.PENDING,
    )
    state.review_requests[review.id] = review
    deliverable.review_request_ids = _append_unique(
        deliverable.review_request_ids,
        review.id,
    )
    inbox_id = f"inbox-{uuid4().hex}"
    state.inbox[inbox_id] = AgentInboxItem(
        id=inbox_id,
        recipient_agent_id=reviewer_id,
        sender_agent_id=actor.id,
        task_id=deliverable.task_id,
        item_type=TeamInboxItemType.STATUS_REQUEST,
        payload={
            "kind": "review_request",
            "review_request_id": review.id,
            "deliverable_id": deliverable_id,
            "instructions": _optional_text(request.get("instructions")) or "",
        },
    )
    result["created_inbox_item_ids"].append(inbox_id)
    return review.id


def _apply_review_decision(
    state: TeamState,
    *,
    actor: PersistentAgent,
    decision: dict[str, Any],
    result: dict[str, Any],
    projects_to_recalc: set[str],
) -> str | None:
    review_request_id = _required_text(
        decision.get("review_request_id"), "review_request_id"
    )
    review = state.review_requests.get(review_request_id)
    if review is None:
        result["errors"].append(
            f"review decision references missing review request {review_request_id}"
        )
        return None
    if review.reviewer_agent_id != actor.id:
        result["errors"].append(
            f"agent {actor.id} is not the assigned reviewer for {review_request_id}"
        )
        return None
    raw_state = _required_text(decision.get("state"), "state")
    try:
        decision_state = TeamRecordState(raw_state)
    except ValueError:
        result["errors"].append(f"review decision has invalid state {raw_state}")
        return None
    if decision_state not in {
        TeamRecordState.ACCEPTED,
        TeamRecordState.REJECTED,
        TeamRecordState.BLOCKED,
    }:
        result["errors"].append(
            f"review decision state {raw_state} must be accepted, rejected, or blocked"
        )
        return None
    review.state = decision_state
    review.findings = _string_list(decision.get("findings"), "findings")
    review.decision_at = utc_now()
    deliverable = _require_present(state.deliverables, review.deliverable_id, "deliverable")
    task = _require_present(state.tasks, deliverable.task_id, "task")
    now = utc_now()
    if decision_state == TeamRecordState.ACCEPTED:
        all_accepted = all(
            state.review_requests[review_id].state == TeamRecordState.ACCEPTED
            for review_id in deliverable.review_request_ids
        )
        if all_accepted:
            deliverable.state = TeamRecordState.ACCEPTED
            task_deliverables = [
                state.deliverables[deliverable_id]
                for deliverable_id in task.deliverable_ids
                if deliverable_id in state.deliverables
            ]
            if task_deliverables and all(
                item.state == TeamRecordState.ACCEPTED for item in task_deliverables
            ):
                task.state = TeamTaskState.ACCEPTED
                task.updated_at = now
    elif decision_state == TeamRecordState.REJECTED:
        deliverable.state = TeamRecordState.REJECTED
        task.state = TeamTaskState.BLOCKED
        task.updated_at = now
    else:  # blocked
        deliverable.state = TeamRecordState.BLOCKED
        task.state = TeamTaskState.BLOCKED
        task.updated_at = now
    projects_to_recalc.add(task.project_id)
    return review.id


def _apply_goal_update(
    state: TeamState,
    *,
    actor: PersistentAgent,
    update: dict[str, Any],
    result: dict[str, Any],
    projects_to_recalc: set[str],
) -> str | None:
    project_id = _required_text(update.get("project_id"), "project_id")
    goal_id = _required_text(update.get("goal_id"), "goal_id")
    project = state.projects.get(project_id)
    if project is None:
        result["errors"].append(f"goal update references missing project {project_id}")
        return None
    permitted = _is_project_lead_for(state, actor.id, project_id) or (
        _is_team_manager(state, actor) and project.team_id == actor.team_id
    )
    if not permitted:
        result["errors"].append(
            f"agent {actor.id} is not permitted to update goals on project {project_id}"
        )
        return None
    raw_state = _required_text(update.get("state"), "state")
    try:
        goal_state = TeamRecordState(raw_state)
    except ValueError:
        result["errors"].append(f"goal update has invalid state {raw_state}")
        return None
    goal = next((item for item in project.goals if item.id == goal_id), None)
    if goal is None:
        result["errors"].append(
            f"goal {goal_id} does not exist on project {project_id}"
        )
        return None
    goal.state = goal_state
    project.updated_at = utc_now()
    projects_to_recalc.add(project_id)
    return goal_id


def _project_git_or_error(
    project: Project,
    result: dict[str, Any],
) -> Any:
    if project.git is None:
        result["errors"].append(
            f"project {project.id} has no git configuration for promotions"
        )
        return None
    return project.git


def _bare_repo_fields(git: Any) -> dict[str, str]:
    if git.bare_repo_name:
        return {"bare_repo_name": git.bare_repo_name}
    return {"bare_repo_path": git.bare_repo_path}


def _emit_git_promotion_event(
    result: dict[str, Any],
    *,
    promotion_id: str,
    payload: dict[str, Any],
) -> None:
    result["events"].append(
        {
            "event_type": GIT_PROMOTION_EVENT_TYPE,
            "correlation_key": promotion_id,
            "idempotency_key": f"team-addon:{GIT_PROMOTION_EVENT_TYPE}:{promotion_id}",
            "payload": payload,
        }
    )


def _apply_git_review_promotion(
    state: TeamState,
    *,
    actor: PersistentAgent,
    request: dict[str, Any],
    result: dict[str, Any],
) -> str | None:
    assignment_id = _required_text(request.get("assignment_id"), "assignment_id")
    assignment = state.ephemeral_assignments.get(assignment_id)
    if assignment is None:
        result["errors"].append(
            f"git review promotion references missing ephemeral assignment {assignment_id}"
        )
        return None
    if assignment.parent_agent_id != actor.id:
        result["errors"].append(
            f"agent {actor.id} cannot promote ephemeral assignment {assignment_id}"
        )
        return None
    if assignment.state not in {
        EphemeralAssignmentState.COMPLETED,
        EphemeralAssignmentState.ACCEPTED,
    }:
        result["errors"].append(
            f"ephemeral assignment {assignment_id} is not completed or accepted"
        )
        return None
    if not assignment.agent_branch:
        result["errors"].append(
            f"ephemeral assignment {assignment_id} has no agent_branch to promote"
        )
        return None
    deliverable_id = _optional_text(request.get("deliverable_id"))
    if deliverable_id is not None and deliverable_id not in state.deliverables:
        result["errors"].append(
            f"git review promotion references missing deliverable {deliverable_id}"
        )
        return None
    project = state.projects.get(assignment.project_id)
    if project is None:
        result["errors"].append(
            f"git review promotion references missing project {assignment.project_id}"
        )
        return None
    git = _project_git_or_error(project, result)
    if git is None:
        return None
    source_branch = assignment.agent_branch
    target_branch = review_branch(actor.team_id, assignment_id)
    promotion_id = f"promotion-{uuid4().hex}"
    state.git_promotions[promotion_id] = GitPromotionRecord(
        id=promotion_id,
        project_id=project.id,
        task_id=assignment.task_id,
        deliverable_id=deliverable_id,
        actor_agent_id=actor.id,
        source_branch=source_branch,
        target_branch=target_branch,
        promotion_kind="review",
    )
    payload: dict[str, Any] = {
        "promotion_id": promotion_id,
        "promotion_kind": "review",
        "project_id": project.id,
        "host_repo_path": git.host_repo_path,
        "source_branch": source_branch,
        "target_branch": target_branch,
        "assignment_id": assignment_id,
    }
    payload.update(_bare_repo_fields(git))
    if deliverable_id is not None:
        payload["deliverable_id"] = deliverable_id
    _emit_git_promotion_event(result, promotion_id=promotion_id, payload=payload)
    return promotion_id


def _apply_git_feature_update(
    state: TeamState,
    *,
    actor: PersistentAgent,
    update: dict[str, Any],
    result: dict[str, Any],
) -> str | None:
    task_id = _required_text(update.get("task_id"), "task_id")
    task = state.tasks.get(task_id)
    if task is None:
        result["errors"].append(
            f"git feature update references missing task {task_id}"
        )
        return None
    if task.owner_agent_id != actor.id and not _supervises(
        state, actor.id, task.owner_agent_id
    ):
        result["errors"].append(
            f"agent {actor.id} is not permitted to consolidate task {task_id}"
        )
        return None
    project = state.projects.get(task.project_id)
    if project is None:
        result["errors"].append(
            f"git feature update references missing project {task.project_id}"
        )
        return None
    git = _project_git_or_error(project, result)
    if git is None:
        return None
    raw_sources = _string_list(
        update.get("source_review_branches"), "source_review_branches"
    )
    if not raw_sources:
        result["errors"].append(
            f"git feature update for task {task_id} requires source_review_branches"
        )
        return None
    try:
        source_branches = [validate_branch_name(branch) for branch in raw_sources]
        target_branch = validate_branch_name(
            _optional_text(update.get("feature_branch"))
            or feature_branch(actor.team_id, task_id, actor.id)
        )
    except ValueError as exc:
        result["errors"].append(f"git feature update has invalid branch name: {exc}")
        return None
    promotion_id = f"promotion-{uuid4().hex}"
    state.git_promotions[promotion_id] = GitPromotionRecord(
        id=promotion_id,
        project_id=project.id,
        task_id=task_id,
        actor_agent_id=actor.id,
        source_branch=source_branches[0],
        target_branch=target_branch,
        promotion_kind="feature",
    )
    payload: dict[str, Any] = {
        "promotion_id": promotion_id,
        "promotion_kind": "feature",
        "project_id": project.id,
        "host_repo_path": git.host_repo_path,
        "base_branch": git.base_branch,
        "source_branches": source_branches,
        "target_branch": target_branch,
        "task_id": task_id,
    }
    _emit_git_promotion_event(result, promotion_id=promotion_id, payload=payload)
    return promotion_id


def _apply_git_release_request(
    state: TeamState,
    *,
    actor: PersistentAgent,
    request: dict[str, Any],
    result: dict[str, Any],
    projects_to_recalc: set[str],
) -> str | None:
    project_id = _required_text(request.get("project_id"), "project_id")
    project = state.projects.get(project_id)
    if project is None:
        result["errors"].append(
            f"git release request references missing project {project_id}"
        )
        return None
    permitted = _is_project_lead_for(state, actor.id, project_id) or (
        _is_team_manager(state, actor) and project.team_id == actor.team_id
    )
    if not permitted:
        result["errors"].append(
            f"agent {actor.id} is not permitted to request releases on project {project_id}"
        )
        return None
    git = _project_git_or_error(project, result)
    if git is None:
        return None
    deliverable_ids = _string_list(request.get("deliverable_ids"), "deliverable_ids")
    if not deliverable_ids:
        result["errors"].append(
            f"git release request for project {project_id} requires deliverable_ids"
        )
        return None
    feature_branches: list[str] = []
    valid = True
    for deliverable_id in deliverable_ids:
        deliverable = state.deliverables.get(deliverable_id)
        if deliverable is None:
            result["errors"].append(
                f"git release request references missing deliverable {deliverable_id}"
            )
            valid = False
            continue
        task = state.tasks.get(deliverable.task_id)
        if task is None or task.project_id != project_id:
            result["errors"].append(
                f"deliverable {deliverable_id} does not belong to project {project_id}"
            )
            valid = False
            continue
        if deliverable.state != TeamRecordState.ACCEPTED:
            result["errors"].append(
                f"deliverable {deliverable_id} is not accepted and cannot be released"
            )
            valid = False
            continue
        feature = deliverable.git_refs.feature_branch
        if feature:
            feature_branches = _append_unique(feature_branches, feature)
        elif not deliverable.artifact_refs:
            result["errors"].append(
                f"deliverable {deliverable_id} has neither a feature branch nor artifact refs"
            )
            valid = False
    if not valid:
        return None
    release_id = _optional_text(request.get("id")) or f"release-{uuid4().hex}"
    if release_id in state.release_packages:
        result["errors"].append(f"release package {release_id} already exists")
        return None
    target_release_branch = validate_branch_name(
        _optional_text(request.get("release_branch"))
        or release_branch(actor.team_id, release_id)
    )
    target_canonical_branch = validate_branch_name(
        _optional_text(request.get("canonical_branch"))
        or canonical_release_branch(project, release_id)
    )
    promotion_ids: list[str] = []
    # Internal feature branches are consolidated into a release branch, which is then
    # pushed to the canonical remote. Only create git promotions when there is at least
    # one feature branch to consolidate; pure artifact deliverables need no push.
    if feature_branches:
        release_promotion_id = f"promotion-{uuid4().hex}"
        state.git_promotions[release_promotion_id] = GitPromotionRecord(
            id=release_promotion_id,
            project_id=project.id,
            release_package_id=release_id,
            actor_agent_id=actor.id,
            source_branch=feature_branches[0],
            target_branch=target_release_branch,
            promotion_kind="release",
        )
        promotion_ids.append(release_promotion_id)
        canonical_promotion_id = f"promotion-{uuid4().hex}"
        state.git_promotions[canonical_promotion_id] = GitPromotionRecord(
            id=canonical_promotion_id,
            project_id=project.id,
            release_package_id=release_id,
            actor_agent_id=actor.id,
            source_branch=target_release_branch,
            target_branch=target_canonical_branch,
            promotion_kind="canonical_push",
        )
        promotion_ids.append(canonical_promotion_id)
    state.release_packages[release_id] = ReleasePackage(
        id=release_id,
        project_id=project.id,
        deliverable_ids=deliverable_ids,
        feature_branches=feature_branches,
        release_branch=target_release_branch,
        canonical_branch=target_canonical_branch,
        git_promotion_record_ids=promotion_ids,
        state=TeamRecordState.PENDING,
    )
    if feature_branches:
        _emit_git_promotion_event(
            result,
            promotion_id=promotion_ids[0],
            payload={
                "promotion_id": promotion_ids[0],
                "promotion_kind": "release",
                "project_id": project.id,
                "release_package_id": release_id,
                "host_repo_path": git.host_repo_path,
                "base_branch": git.base_branch,
                "source_branches": list(feature_branches),
                "target_branch": target_release_branch,
            },
        )
        canonical_payload: dict[str, Any] = {
            "promotion_id": promotion_ids[1],
            "promotion_kind": "canonical_push",
            "project_id": project.id,
            "release_package_id": release_id,
            "host_repo_path": git.host_repo_path,
            "base_branch": git.base_branch,
            "source_branch": target_release_branch,
            "target_branch": target_canonical_branch,
        }
        if git.canonical_origin_url is not None:
            canonical_payload["canonical_origin_url"] = git.canonical_origin_url
        if git.provider is not None:
            canonical_payload["provider"] = git.provider
        _emit_git_promotion_event(
            result,
            promotion_id=promotion_ids[1],
            payload=canonical_payload,
        )
        result["git_promotion_event_ids"].extend(promotion_ids)
    projects_to_recalc.add(project_id)
    return release_id


def _recalculate_project_status(state: TeamState, project_id: str) -> None:
    project = state.projects.get(project_id)
    if project is None or project.status == ProjectStatus.ARCHIVED:
        return
    project_tasks = [
        task for task in state.tasks.values() if task.project_id == project_id
    ]
    project_deliverables = [
        deliverable
        for deliverable in state.deliverables.values()
        if (task := state.tasks.get(deliverable.task_id)) is not None
        and task.project_id == project_id
    ]
    deliverable_ids = {deliverable.id for deliverable in project_deliverables}
    project_reviews = [
        review
        for review in state.review_requests.values()
        if review.deliverable_id in deliverable_ids
    ]
    project_releases = [
        release
        for release in state.release_packages.values()
        if release.project_id == project_id
    ]

    pending_or_blocked = {TeamRecordState.PENDING, TeamRecordState.BLOCKED}
    blocked = (
        any(task.state == TeamTaskState.BLOCKED for task in project_tasks)
        or any(goal.state == TeamRecordState.BLOCKED for goal in project.goals)
        or any(
            deliverable.state == TeamRecordState.BLOCKED
            for deliverable in project_deliverables
        )
        or any(review.state == TeamRecordState.BLOCKED for review in project_reviews)
        or any(release.state == TeamRecordState.BLOCKED for release in project_releases)
    )
    completed = (
        all(goal.state == TeamRecordState.ACCEPTED for goal in project.goals)
        and not any(task.state in _NON_TERMINAL_TASK_STATES for task in project_tasks)
        and not any(
            deliverable.state in pending_or_blocked
            for deliverable in project_deliverables
        )
        and not any(review.state in pending_or_blocked for review in project_reviews)
        and not any(release.state in pending_or_blocked for release in project_releases)
    )
    if completed:
        new_status = ProjectStatus.COMPLETED
    elif blocked:
        new_status = ProjectStatus.BLOCKED
    else:
        new_status = ProjectStatus.ACTIVE
    if new_status != project.status:
        project.status = new_status
        project.updated_at = utc_now()


def _list_of_mappings(value: object, field_name: str) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a list")
    records: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError(f"{field_name} must contain JSON objects")
        records.append(dict(item))
    return records


def _string_list(value: object, field_name: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a list of strings")
    return [str(item).strip() for item in value if str(item).strip()]


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
    requests: list[dict[str, Any]],
    dispatch_id: str | None,
    result: dict[str, Any],
) -> None:
    if not requests:
        return
    active_count = len(_active_ephemeral_assignments_for_parent(state, parent.id))
    max_concurrent = parent.ephemeral_policy.max_concurrent
    if max_concurrent <= 0:
        raise ValueError(f"agent {parent.id} is not allowed to spawn ephemeral agents")
    if active_count + len(requests) > max_concurrent:
        raise ValueError(
            f"agent {parent.id} ephemeral request would exceed max_concurrent="
            f"{max_concurrent}"
        )

    normalized: list[tuple[dict[str, Any], AgentTask]] = []
    seen_ids: set[str] = set()
    for request in requests:
        task_id = _required_text(request.get("task_id"), "task_id")
        task = _require_present(state.tasks, task_id, "task")
        if task.owner_agent_id != parent.id:
            raise ValueError(f"agent {parent.id} cannot spawn ephemeral work for task {task_id}")
        if parent.project_id != task.project_id:
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
            directory_path=_optional_text(request.get("directory_path")),
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
    assignment_id = (
        _optional_text(action.get("ephemeral_assignment_id"))
        or _optional_text(action.get("assignment_id"))
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
        deliverable_id_value = deliverable_id or f"deliverable-{uuid4().hex}"
        _require_absent(state.deliverables, deliverable_id_value, "deliverable")
        task = _require_present(state.tasks, assignment.task_id, "task")
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
        task.state = TeamTaskState.WAITING_FOR_REVIEW
        task.updated_at = utc_now()
        assignment.state = EphemeralAssignmentState.ACCEPTED
        assignment.error = None
        assignment.updated_at = utc_now()
        return deliverable.id

    assignment.state = EphemeralAssignmentState.REJECTED
    assignment.error = rejection_reason
    assignment.updated_at = utc_now()
    return None


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
    team_metadata = dict(metadata.get("team_addon")) if isinstance(metadata.get("team_addon"), dict) else {}
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
            "allowed_tools": list(assignment.allowed_tools),
            "ttl_hours": assignment.ttl_hours,
            "directory_path": assignment.directory_path,
            "metadata": metadata,
        },
    }


def _build_ephemeral_prompt(
    *,
    assignment: EphemeralAssignment,
    parent: PersistentAgent,
    task: AgentTask,
) -> str:
    context = {
        "assignment": assignment.model_dump(mode="json"),
        "parent_agent": parent.model_dump(mode="json"),
        "task": task.model_dump(mode="json"),
        "action_artifact_path": "/artifacts/team-action.json",
    }
    schema = {
        "ephemeral_assignment_id": assignment.id,
        "summary": "",
        "artifact_refs": [],
        "task_notes": "",
        "error": None,
    }
    return (
        "Complete this scoped ephemeral team-agent assignment. "
        "You are a disposable worker for one parent agent; do not spawn other agents, "
        "ask hierarchy questions, or update unrelated team state.\n\n"
        f"Assignment prompt:\n{assignment.prompt}\n\n"
        f"Assignment context JSON:\n{_json_dumps(context)}\n\n"
        "Before finishing, write a UTF-8 JSON action object to "
        "/artifacts/team-action.json using this shape:\n"
        f"{_json_dumps(schema)}\n"
    )


def _optional_mapping(value: object, field_name: str) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} must be a JSON object")
    return dict(value)


def _bool_from_value(value: object, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    raise ValueError("boolean value is required")


def _json_dumps(value: Any) -> str:
    import json

    return json.dumps(value, sort_keys=True, indent=2)


__all__ = [
    "ADDON_ID",
    "EPHEMERAL_COMPLETED_EVENT_TYPE",
    "EPHEMERAL_REQUESTED_EVENT_TYPE",
    "GIT_PROMOTION_EVENT_TYPE",
    "OPERATOR_ESCALATION_EVENT_TYPE",
    "STATE_FILE",
    "TeamStateConflictError",
    "TeamStateRepository",
    "ValidationError",
]
