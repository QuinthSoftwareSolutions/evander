from __future__ import annotations

import json
from datetime import UTC, datetime

from evander_team_addon.models import (
    AgentInboxItem,
    AgentProjectAssignment,
    AgentProjectAssignmentKind,
    AgentRuntime,
    AgentStandDown,
    AgentStatus,
    AgentTask,
    PersistentAgent,
    Project,
    Team,
    TeamInboxItemType,
    TeamInboxPriority,
    TeamRole,
    TeamState,
    TeamTaskState,
    agent_activity_summaries_payload,
    team_state_payload,
)
from evander_team_addon.providers import _filtered_team_state_payload


def _state(
    *,
    agent_status: AgentStatus = AgentStatus.ACTIVE,
    runtime: AgentRuntime | None = None,
    tasks: dict[str, AgentTask] | None = None,
    inbox: dict[str, AgentInboxItem] | None = None,
    stand_downs: dict[str, AgentStandDown] | None = None,
) -> TeamState:
    return TeamState(
        teams={
            "team": Team(
                id="team",
                name="Team",
                manager_agent_id="agent",
                project_ids=["project"],
            )
        },
        projects={
            "project": Project(
                id="project",
                team_id="team",
                name="Project",
                lead_agent_id="agent",
            )
        },
        roles={
            "team__worker": TeamRole(
                id="team__worker",
                team_id="team",
                name="Worker",
            )
        },
        agents={
            "agent": PersistentAgent(
                id="agent",
                team_id="team",
                role_id="team__worker",
                session_id="session-agent",
                status=agent_status,
            )
        },
        agent_runtimes={"agent": runtime} if runtime is not None else {},
        tasks=tasks or {},
        inbox=inbox or {},
        stand_downs=stand_downs or {},
    )


def _task(
    *,
    task_id: str = "task",
    state: TeamTaskState = TeamTaskState.IN_PROGRESS,
    title: str = "Checkout validation",
    dependency_task_ids: list[str] | None = None,
) -> AgentTask:
    return AgentTask(
        id=task_id,
        owner_agent_id="agent",
        project_id="project",
        priority=0,
        state=state,
        title=title,
        description="Task description",
        created_by_agent_id="agent",
        dependency_task_ids=dependency_task_ids or [],
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        updated_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def test_activity_summary_describes_current_task() -> None:
    state = _state(tasks={"task": _task()})

    summary = agent_activity_summaries_payload(state)["agent"]

    assert summary["headline"] == "Working on Checkout validation"
    assert summary["working_on"] == "Checkout validation"
    assert summary["waiting_for"] == "Clear"
    assert summary["tone"] == "working"
    assert summary["reason_refs"] == [{"type": "task", "id": "task"}]


def test_activity_summary_describes_blocked_dependency() -> None:
    state = _state(
        tasks={
            "dep": _task(task_id="dep", state=TeamTaskState.BLOCKED, title="Upstream API"),
            "task": _task(dependency_task_ids=["dep"]),
        }
    )

    summary = agent_activity_summaries_payload(state)["agent"]

    assert summary["headline"] == "Blocked on Upstream API"
    assert summary["waiting_for"] == "Blocked task state"
    assert summary["tone"] == "blocked"


def test_activity_summary_describes_pending_inbox() -> None:
    state = _state(
        inbox={
            "inbox": AgentInboxItem(
                id="inbox",
                recipient_agent_id="agent",
                item_type=TeamInboxItemType.STATUS_REQUEST,
                priority=TeamInboxPriority.HIGH,
                payload={"question": "What is the rollout status?"},
            )
        }
    )

    summary = agent_activity_summaries_payload(state)["agent"]

    assert summary["headline"] == "Handling status request: What is the rollout status?"
    assert summary["tone"] == "attention"
    assert summary["reason_refs"] == [{"type": "inbox", "id": "inbox"}]


def test_activity_summary_describes_active_dispatch() -> None:
    state = _state(
        runtime=AgentRuntime(agent_id="agent", active_dispatch_id="dispatch-1"),
        tasks={"task": _task()},
    )

    summary = agent_activity_summaries_payload(state)["agent"]

    assert summary["headline"] == "Working on Checkout validation"
    assert summary["tone"] == "working"
    assert {"type": "dispatch", "id": "dispatch-1"} in summary["reason_refs"]


def test_team_state_payload_embeds_activity_summaries() -> None:
    state = _state(tasks={"task": _task()})

    payload = team_state_payload(state)

    assert (
        payload["agent_activity_summaries"]["agent"]["headline"] == "Working on Checkout validation"
    )
    assert (
        payload["agents"]["agent"]["activity_summary"]["headline"]
        == "Working on Checkout validation"
    )


def test_filtered_team_state_payload_keeps_visible_activity_summary() -> None:
    state = _state(tasks={"task": _task()})
    state.project_assignments["assignment"] = AgentProjectAssignment(
        id="assignment",
        team_id="team",
        project_id="project",
        agent_id="agent",
        kind=AgentProjectAssignmentKind.PROJECT_MEMBER,
    )

    payload = _filtered_team_state_payload(
        state,
        team_id=None,
        project_id="project",
        agent_id=None,
    )

    assert (
        payload["agent_activity_summaries"]["agent"]["headline"] == "Working on Checkout validation"
    )
    assert (
        payload["agents"]["agent"]["activity_summary"]["headline"]
        == "Working on Checkout validation"
    )


def test_filtered_team_state_payload_drops_out_of_scope_activity_summary() -> None:
    state = TeamState(
        teams={
            "team": Team(
                id="team",
                name="Team",
                manager_agent_id="agent",
                project_ids=["visible-project", "hidden-project"],
            )
        },
        projects={
            "visible-project": Project(
                id="visible-project",
                team_id="team",
                name="Visible Project",
                lead_agent_id="agent",
            ),
            "hidden-project": Project(
                id="hidden-project",
                team_id="team",
                name="Hidden Project",
                lead_agent_id="agent",
            ),
        },
        roles={
            "team__worker": TeamRole(
                id="team__worker",
                team_id="team",
                name="Worker",
            )
        },
        agents={
            "agent": PersistentAgent(
                id="agent",
                team_id="team",
                role_id="team__worker",
                session_id="session-agent",
                status=AgentStatus.ACTIVE,
            )
        },
        project_assignments={
            "visible-assignment": AgentProjectAssignment(
                id="visible-assignment",
                team_id="team",
                project_id="visible-project",
                agent_id="agent",
                kind=AgentProjectAssignmentKind.PROJECT_MEMBER,
            ),
            "hidden-assignment": AgentProjectAssignment(
                id="hidden-assignment",
                team_id="team",
                project_id="hidden-project",
                agent_id="agent",
                kind=AgentProjectAssignmentKind.PROJECT_MEMBER,
            ),
        },
        tasks={
            "hidden-task": AgentTask(
                id="hidden-task",
                owner_agent_id="agent",
                project_id="hidden-project",
                priority=0,
                state=TeamTaskState.IN_PROGRESS,
                title="Secret launch plan",
                description="Hidden work should not leak through summaries.",
                created_by_agent_id="agent",
                created_at=datetime(2026, 1, 1, tzinfo=UTC),
                updated_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
        },
    )

    payload = _filtered_team_state_payload(
        state,
        team_id=None,
        project_id="visible-project",
        agent_id=None,
    )

    assert "agent" in payload["agents"]
    assert "agent" not in payload["agent_activity_summaries"]
    assert "activity_summary" not in payload["agents"]["agent"]
    assert "hidden-task" not in payload["tasks"]
    assert "Secret launch plan" not in json.dumps(payload)
