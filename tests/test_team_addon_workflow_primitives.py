from __future__ import annotations

from pathlib import Path

from agent_sandbox.effects.providers.base import EffectRuntimeContext
from agent_sandbox.triggers.providers.base import TriggerRuntimeContext
from evander_team_addon.models import AgentStatus, ProjectGoal, TeamInboxItemType
from evander_team_addon.provider_schemas import (
    TeamCreateReviewRequestEffectDefinition,
    TeamDeliverableCreatedTriggerDefinition,
)
from evander_team_addon.providers import (
    TeamCreateReviewRequestEffectProvider,
    TeamDeliverableCreatedTriggerProvider,
    register_providers,
)
from evander_team_addon.repository import TeamStateRepository
from evander_team_addon.services.admin_service import TeamAdminService


def _admin(repo: TeamStateRepository) -> TeamAdminService:
    return TeamAdminService(repo)


def _seed_team(runtime_dir: Path) -> TeamStateRepository:
    repo = TeamStateRepository(runtime_dir)
    _admin(repo).create_team(
        team_id="team",
        name="Team",
        manager_agent_id="manager",
        manager_session_id="session-manager",
        expected_revision=0,
    )
    _admin(repo).create_project(
        project_id="project",
        team_id="team",
        name="Project",
        lead_agent_id="lead",
        lead_session_id="session-lead",
        goals=[ProjectGoal(id="goal", description="Ship reviewed work")],
        expected_revision=repo.read().revision,
    )
    _admin(repo).create_role(
        role_id="project__implementer",
        team_id="team",
        project_id="project",
        name="Implementer",
        expected_revision=repo.read().revision,
    )
    _admin(repo).create_role(
        role_id="project__reviewer",
        team_id="team",
        project_id="project",
        name="Reviewer",
        expected_revision=repo.read().revision,
    )
    _admin(repo).register_agent(
        agent_id="implementer",
        team_id="team",
        project_id="project",
        role_id="project__implementer",
        session_id="session-implementer",
        reports_to_agent_id="lead",
        expected_revision=repo.read().revision,
    )
    _admin(repo).register_agent(
        agent_id="reviewer",
        team_id="team",
        project_id="project",
        role_id="project__reviewer",
        session_id="session-reviewer",
        reports_to_agent_id="lead",
        expected_revision=repo.read().revision,
    )
    _admin(repo).create_review_profile(
        profile_id="code-review",
        team_id="team",
        project_id="project",
        name="Code review",
        applicable_role_ids=["project__reviewer"],
        expected_revision=repo.read().revision,
    )
    _admin(repo).create_or_reprioritize_task(
        task_id="task",
        owner_agent_id="implementer",
        project_id="project",
        priority=1,
        title="Implement feature",
        description="Build the requested feature.",
        created_by_agent_id="lead",
        expected_revision=repo.read().revision,
    )
    return repo


def _record_deliverable(repo: TeamStateRepository, deliverable_id: str) -> None:
    _admin(repo).record_deliverable(
        deliverable_id=deliverable_id,
        task_id="task",
        producer_agent_id="implementer",
        summary=f"Deliverable {deliverable_id}",
        artifact_refs=[f"artifact-space:/teams/team/projects/project/{deliverable_id}"],
        expected_revision=repo.read().revision,
    )


def _set_agent_status(repo: TeamStateRepository, agent_id: str, status: AgentStatus) -> None:
    def _mutate(state):
        state.agents[agent_id].status = status
        return state

    repo.update(_mutate, expected_revision=repo.read().revision)


def _trigger(runtime_dir: Path) -> TeamDeliverableCreatedTriggerProvider:
    provider = TeamDeliverableCreatedTriggerProvider()
    provider.initialize(
        TeamDeliverableCreatedTriggerDefinition(
            id="team-deliverable-created",
            display_name="Team deliverable created",
            type="trigger:team:deliverable-created",
            team_id="team",
            project_id="project",
        ),
        TriggerRuntimeContext(resolved_config={"runtime_dir": str(runtime_dir)}),
    )
    return provider


def test_team_workflow_provider_registration_includes_review_primitives() -> None:
    registration = register_providers()

    assert (
        registration.trigger_providers["trigger:team:deliverable-created"]
        is TeamDeliverableCreatedTriggerProvider
    )
    assert (
        registration.effect_providers["effect:team:create-review-request"]
        is TeamCreateReviewRequestEffectProvider
    )


def test_team_deliverable_created_trigger_suppresses_existing_then_emits_new(
    tmp_path: Path,
) -> None:
    repo = _seed_team(tmp_path)
    _record_deliverable(repo, "old-deliverable")
    provider = _trigger(tmp_path)

    assert provider.poll() == []

    _record_deliverable(repo, "new-deliverable")
    events = provider.poll()

    assert [event.event_id for event in events] == ["team-deliverable-created:new-deliverable"]
    payload = events[0].payload
    assert payload["deliverable_id"] == "new-deliverable"
    assert payload["team_id"] == "team"
    assert payload["project_id"] == "project"
    assert payload["task_id"] == "task"
    assert payload["producer_agent_id"] == "implementer"
    assert payload["deliverable"]["summary"] == "Deliverable new-deliverable"

    provider.acknowledge_published_event(events[0])

    assert provider.poll() == []


def test_create_review_request_effect_uses_team_review_handoff(tmp_path: Path) -> None:
    repo = _seed_team(tmp_path)
    _record_deliverable(repo, "deliverable")
    provider = TeamCreateReviewRequestEffectProvider()
    provider.initialize(
        TeamCreateReviewRequestEffectDefinition(
            id="create-review",
            display_name="Create review",
            type="effect:team:create-review-request",
            actor_agent_id="lead",
            deliverable_id="deliverable",
            reviewer_agent_id="reviewer",
            review_profile_id="code-review",
            instructions="Review code quality and test evidence.",
            expected_revision=repo.read().revision,
        ),
        EffectRuntimeContext(resolved_config={"runtime_dir": str(tmp_path)}),
    )

    result = provider.execute()

    assert result["success"] is True
    assert result["created"] is True
    assert result["created_review_request_ids"] == [result["review_request_id"]]
    assert result["existing_review_request_ids"] == []
    assert result["created_inbox_item_ids"]
    assert result["published_event_ids"]
    reread = repo.read()
    review = reread.review_requests[result["review_request_id"]]
    assert review.deliverable_id == "deliverable"
    assert review.reviewer_agent_id == "reviewer"
    assert review.review_profile_id == "code-review"
    inbox_item = reread.inbox[result["created_inbox_item_ids"][0]]
    assert inbox_item.recipient_agent_id == "reviewer"
    assert inbox_item.item_type == TeamInboxItemType.STATUS_REQUEST
    assert inbox_item.payload["kind"] == "review_request"
    assert inbox_item.payload["review_request_id"] == review.id

    duplicate = provider.execute(
        resolved_config={
            "expected_revision": repo.read().revision,
        }
    )

    assert duplicate["success"] is True
    assert duplicate["created"] is False
    assert duplicate["review_request_id"] == review.id
    assert duplicate["existing_review_request_ids"] == [review.id]
    assert duplicate["created_inbox_item_ids"] == []


def test_create_review_request_effect_validates_wake_flag_before_mutating(
    tmp_path: Path,
) -> None:
    repo = _seed_team(tmp_path)
    _record_deliverable(repo, "deliverable")
    provider = TeamCreateReviewRequestEffectProvider()
    provider.initialize(
        TeamCreateReviewRequestEffectDefinition(
            id="create-review",
            display_name="Create review",
            type="effect:team:create-review-request",
            actor_agent_id="lead",
            deliverable_id="deliverable",
            reviewer_agent_id="reviewer",
            expected_revision=repo.read().revision,
        ),
        EffectRuntimeContext(resolved_config={"runtime_dir": str(tmp_path)}),
    )

    result = provider.execute(resolved_config={"publish_wake_events": "sometimes"})

    assert result["success"] is False
    assert result["error"] == "boolean value is required"
    reread = repo.read()
    assert reread.review_requests == {}
    assert reread.inbox == {}


def test_create_review_request_effect_rejects_inactive_reviewer_before_mutating(
    tmp_path: Path,
) -> None:
    repo = _seed_team(tmp_path)
    _record_deliverable(repo, "deliverable")
    _set_agent_status(repo, "reviewer", AgentStatus.PAUSED)
    provider = TeamCreateReviewRequestEffectProvider()
    provider.initialize(
        TeamCreateReviewRequestEffectDefinition(
            id="create-review",
            display_name="Create review",
            type="effect:team:create-review-request",
            actor_agent_id="lead",
            deliverable_id="deliverable",
            reviewer_agent_id="reviewer",
            expected_revision=repo.read().revision,
        ),
        EffectRuntimeContext(resolved_config={"runtime_dir": str(tmp_path)}),
    )

    result = provider.execute()

    assert result["success"] is False
    assert result["error"] == "recipient agent reviewer is paused"
    reread = repo.read()
    assert reread.review_requests == {}
    assert reread.inbox == {}
