"""Reusable Team test builders with a fresh repository and fake state per call."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from evander_team_addon.models import (
    AgentRuntime,
    ProjectGoal,
    TeamDispatch,
    TeamDispatchStatus,
    TeamBlockerRoute,
    TeamBlockerState,
    TeamInboxItemState,
    TeamRecordState,
)
from evander_team_addon.repository import TeamStateRepository
from evander_team_addon.services.domain.task_assessment import TaskAssessmentService
from evander_team_addon.services.agent_action_service import TeamAgentActionService
from evander_team_addon.services.admin_service import TeamAdminService
from evander_team_addon.services.commands import (
    TeamAgentControlCommandService,
    TeamAgentMessageCommandService,
    TeamBlockerCommandService,
    TeamCaseCommandService,
    TeamChatCommandService,
    TeamDeliverableSubmissionCommandService,
    TeamEphemeralCommandService,
    TeamGitReleaseCommandService,
    TeamGovernanceCommandService,
    TeamMemoryCommandService,
    TeamNoteCommandService,
    TeamOperatorEscalationCommandService,
    TeamOutcomeCatalogCommandService,
    TeamReviewCommandService,
    TeamTaskCreationCommandService,
    TeamTaskUpdateCommandService,
)


class _TeamCommandTestHarness(
    TeamNoteCommandService,
    TeamAgentMessageCommandService,
    TeamChatCommandService,
    TeamOperatorEscalationCommandService,
    TeamCaseCommandService,
    TeamTaskCreationCommandService,
    TeamTaskUpdateCommandService,
    TeamAgentControlCommandService,
    TeamBlockerCommandService,
    TeamGovernanceCommandService,
    TeamMemoryCommandService,
    TeamDeliverableSubmissionCommandService,
    TeamReviewCommandService,
    TeamGitReleaseCommandService,
    TeamEphemeralCommandService,
    TeamOutcomeCatalogCommandService,
):
    """Local test harness for cases that intentionally span command families."""


def _command_service(repo: TeamStateRepository) -> _TeamCommandTestHarness:
    service = _TeamCommandTestHarness(repo)
    service._session_lookup = _root_session_lookup
    return service


def _team_admin_service(repo: TeamStateRepository) -> TeamAdminService:
    return TeamAdminService(repo)


def _team_agent_action_service(repo: TeamStateRepository) -> TeamAgentActionService:
    return TeamAgentActionService(repo, session_lookup=_root_session_lookup)


def _root_session_lookup(_session_id: str) -> dict[str, str]:
    return {"directory_path": "root"}


def _seed_hierarchy(tmp_path: Path) -> TeamStateRepository:
    """manager -> lead (project) -> backend -> worker, all in one team/project."""

    repo = TeamStateRepository(tmp_path)
    _team_admin_service(repo).create_team(
        team_id="team",
        name="Team",
        manager_agent_id="manager",
        manager_session_id="session-manager",
        manager_display_name="  Team Manager  ",
        expected_revision=0,
    )
    _team_admin_service(repo).create_project(
        project_id="project",
        team_id="team",
        name="Project",
        lead_agent_id="lead",
        lead_session_id="session-lead",
        lead_display_name="Project Lead",
        goals=[ProjectGoal(id="goal", description="Ship work")],
        expected_revision=repo.read().revision,
    )
    _team_admin_service(repo).create_role(
        role_id="project__backend",
        team_id="team",
        project_id="project",
        name="Backend",
        expected_revision=repo.read().revision,
    )
    _team_admin_service(repo).create_role(
        role_id="project__worker",
        team_id="team",
        project_id="project",
        name="Worker",
        expected_revision=repo.read().revision,
    )
    _team_admin_service(repo).register_agent(
        agent_id="backend",
        team_id="team",
        project_id="project",
        role_id="project__backend",
        session_id="session-backend",
        reports_to_agent_id="lead",
        display_name="Backend Agent",
        expected_revision=repo.read().revision,
    )
    _team_admin_service(repo).register_agent(
        agent_id="worker",
        team_id="team",
        project_id="project",
        role_id="project__worker",
        session_id="session-worker",
        reports_to_agent_id="backend",
        display_name="  Worker Agent  ",
        expected_revision=repo.read().revision,
    )
    return repo


def _lease_agent_dispatch(
    repo: TeamStateRepository,
    *,
    agent_id: str,
    dispatch_id: str = "dispatch-command",
    wake_reason: str = "inbox",
) -> None:
    def _lease(state):
        agent = state.agents[agent_id]
        assessments = TaskAssessmentService(state)
        project_ids = sorted(
            project.id for project in state.projects.values() if project.team_id == agent.team_id
        )
        task_ids = sorted(
            task.id
            for task in state.tasks.values()
            if task.project_id in project_ids
            and task.owner_agent_id == agent_id
            and assessments.assess(task.id).readiness.value == "actionable"
        )
        blocker_ids: list[str] = []
        for blocker in state.blockers.values():
            assigned = (
                blocker.route == TeamBlockerRoute.DELEGATED
                and blocker.delegate_agent_id == agent_id
            ) or (
                blocker.route in {TeamBlockerRoute.OWNED, TeamBlockerRoute.ROUTING_FAILED}
                and blocker.owner_agent_id == agent_id
            )
            if blocker.state == TeamBlockerState.OPEN and assigned:
                blocker.active_dispatch_id = dispatch_id
                blocker_ids.append(blocker.id)
        state.dispatches[dispatch_id] = TeamDispatch(
            id=dispatch_id,
            agent_id=agent_id,
            session_id=agent.session_id,
            cause_kind=wake_reason,
            task_ids=task_ids,
            blocker_ids=sorted(blocker_ids),
            review_request_ids=sorted(
                review.id
                for review in state.review_requests.values()
                if review.reviewer_agent_id == agent_id
                and review.state in {TeamRecordState.PENDING, TeamRecordState.IN_PROGRESS}
                and review.superseded_by_review_request_id is None
                and review.retired_at is None
            ),
            inbox_item_ids=sorted(
                item.id
                for item in state.inbox.values()
                if item.recipient_agent_id == agent_id and item.state == TeamInboxItemState.PENDING
            ),
            project_ids=project_ids,
            managed_repo_ids=sorted(
                {
                    project.git.managed_repo_id
                    for project in state.projects.values()
                    if project.id in project_ids and project.git is not None
                }
            ),
            status=TeamDispatchStatus.LEASED,
            prepared_at=datetime(2098, 12, 31, 23, 59, tzinfo=UTC),
            leased_at=datetime(2098, 12, 31, 23, 59, tzinfo=UTC),
            lease_expires_at=datetime(2099, 1, 1, 0, 15, tzinfo=UTC),
        )
        state.agent_runtimes[agent_id] = AgentRuntime(
            agent_id=agent_id,
            active_dispatch_id=dispatch_id,
            wake_leased_until=datetime(2099, 1, 1, 0, 15, tzinfo=UTC),
            wake_reason=wake_reason,
        )
        return state

    repo.update(_lease)


def _seed_task(
    repo: TeamStateRepository,
    *,
    task_id: str,
    owner_agent_id: str = "backend",
) -> None:
    _team_admin_service(repo).create_or_reprioritize_task(
        task_id=task_id,
        owner_agent_id=owner_agent_id,
        project_id="project",
        priority=1,
        title=f"Task {task_id}",
        description="Work to deliver.",
        created_by_agent_id="lead",
    )


def _register_backend_peer(
    repo: TeamStateRepository,
    *,
    reports_to_agent_id: str,
) -> None:
    _team_admin_service(repo).register_agent(
        agent_id="backend-peer",
        team_id="team",
        project_id="project",
        role_id="project__backend",
        session_id="session-backend-peer",
        reports_to_agent_id=reports_to_agent_id,
        display_name="Backend Peer",
        expected_revision=repo.read().revision,
    )
