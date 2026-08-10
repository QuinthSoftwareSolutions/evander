from __future__ import annotations

from pathlib import Path

from evander.effects.providers.base import EffectRuntimeContext
from evander.queries.providers.base import QueryRuntimeContext
from evander.triggers.providers.base import TriggerRuntimeContext
from evander_team_addon.models import (
    AgentStatus,
    ProjectGoal,
    ProjectStatus,
    TeamInboxItemType,
    TeamRecordState,
    TeamTaskState,
)
from evander_team_addon.provider_schemas import (
    TeamCreateReviewCohortEffectDefinition,
    TeamCreateReviewRequestEffectDefinition,
    TeamDeliverableCreatedTriggerDefinition,
    TeamMemoryQueryDefinition,
)
from evander_team_addon.providers import (
    TeamCreateReviewCohortEffectProvider,
    TeamCreateReviewRequestEffectProvider,
    TeamDeliverableCreatedTriggerProvider,
    TeamIndexMemoryEffectProvider,
    TeamMemoryQueryProvider,
    TeamRecordHumanReviewPreparationResultEffectProvider,
    TeamReindexMemoryEffectProvider,
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
    assert (
        registration.effect_providers["effect:team:create-review-cohort"]
        is TeamCreateReviewCohortEffectProvider
    )
    assert (
        registration.effect_providers["effect:team:index-memory"] is TeamIndexMemoryEffectProvider
    )
    assert (
        registration.effect_providers["effect:team:reindex-memory"]
        is TeamReindexMemoryEffectProvider
    )
    assert (
        registration.effect_providers["effect:team:record-human-review-preparation-result"]
        is TeamRecordHumanReviewPreparationResultEffectProvider
    )
    assert registration.query_providers["query:team:memory"] is TeamMemoryQueryProvider
    internal_events = {entry.event_type for entry in registration.internal_event_catalog_entries}
    assert "team.release.human_review_requested" in internal_events
    assert "team.release.human_review_preparation_requested" in internal_events


def test_team_memory_query_reads_compact_canonical_records(tmp_path: Path) -> None:
    repo = _seed_team(tmp_path)
    _admin(repo).create_or_update_memory(
        memory_id="memory-sqlite",
        team_id="team",
        project_id="project",
        scope="project",
        kind="decision",
        title="SQLite is canonical",
        summary="Qdrant can be rebuilt from canonical Team memory.",
        body="This detailed body should stay out of compact query results.",
        tags=["memory"],
        expected_revision=repo.read().revision,
    )
    provider = TeamMemoryQueryProvider()
    provider.initialize(
        TeamMemoryQueryDefinition(
            id="memory-query",
            display_name="Memory query",
            type="query:team:memory",
            team_id="team",
            project_id="project",
            query_text="rebuilt",
            limit=5,
        ),
        QueryRuntimeContext(resolved_config={"runtime_dir": str(tmp_path)}),
    )

    result = provider.execute()

    assert result["success"] is True
    assert result["semantic"] is False
    assert [record["id"] for record in result["records"]] == ["memory-sqlite"]
    assert "body" not in result["records"][0]


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
    assert payload["task"]["assessment"]["task_id"] == "task"
    assert payload["task"]["readiness"] == payload["task"]["assessment"]["readiness"]

    provider.acknowledge_published_event(events[0])

    assert provider.poll() == []


def test_contracted_leaf_can_flow_through_operator_configured_review_primitives(
    tmp_path: Path,
) -> None:
    repo = _seed_team(tmp_path)
    _admin(repo).update_task(
        task_id="task",
        integration_boundary=False,
        deliverable_contract={"require_git_provenance": False},
        set_deliverable_contract=True,
        expected_revision=repo.read().revision,
    )
    trigger = _trigger(tmp_path)

    assert trigger.poll() == []

    _record_deliverable(repo, "contracted-leaf-deliverable")
    events = trigger.poll()

    assert len(events) == 1
    payload = events[0].payload
    assert payload["task"]["integration_boundary"] is False
    assert payload["task"]["deliverable_contract"] == {
        "require_git_provenance": False,
        "required_contribution_task_ids": [],
    }

    effect = TeamCreateReviewCohortEffectProvider()
    effect.initialize(
        TeamCreateReviewCohortEffectDefinition(
            id="operator-configured-review",
            display_name="Operator-configured review",
            type="effect:team:create-review-cohort",
            actor_agent_id=payload["producer_agent_id"],
            deliverable_id=payload["deliverable_id"],
            reviews=[
                {
                    "reviewer_agent_id": "reviewer",
                    "review_profile_id": "code-review",
                }
            ],
            expected_revision=repo.read().revision,
        ),
        EffectRuntimeContext(resolved_config={"runtime_dir": str(tmp_path)}),
    )

    result = effect.execute()

    assert result["success"] is True
    assert result["created"] is True
    assert len(result["created_review_request_ids"]) == 1
    review = repo.read().review_requests[result["created_review_request_ids"][0]]
    assert review.deliverable_id == "contracted-leaf-deliverable"
    assert review.reviewer_agent_id == "reviewer"
    assert review.review_profile_id == "code-review"


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


def test_create_review_cohort_effect_seals_all_reviewers_atomically(
    tmp_path: Path,
) -> None:
    repo = _seed_team(tmp_path)
    _admin(repo).register_agent(
        agent_id="tester",
        team_id="team",
        project_id="project",
        role_id="project__reviewer",
        session_id="session-tester",
        reports_to_agent_id="lead",
        expected_revision=repo.read().revision,
    )
    _record_deliverable(repo, "deliverable")
    provider = TeamCreateReviewCohortEffectProvider()
    provider.initialize(
        TeamCreateReviewCohortEffectDefinition(
            id="create-review-cohort",
            display_name="Create review cohort",
            type="effect:team:create-review-cohort",
            actor_agent_id="lead",
            deliverable_id="deliverable",
            reviews=[
                {
                    "reviewer_agent_id": "reviewer",
                    "review_profile_id": "code-review",
                },
                {
                    "reviewer_agent_id": "tester",
                    "review_profile_id": "code-review",
                },
            ],
            expected_revision=repo.read().revision,
        ),
        EffectRuntimeContext(resolved_config={"runtime_dir": str(tmp_path)}),
    )

    result = provider.execute()

    assert result["success"] is True
    assert result["created"] is True
    assert len(result["created_review_request_ids"]) == 2
    assert result["existing_review_request_ids"] == []
    assert len(result["created_inbox_item_ids"]) == 2
    assert len(result["published_event_ids"]) == 2
    reread = repo.read()
    deliverable = reread.deliverables["deliverable"]
    assert deliverable.review_cohort_id == result["review_cohort_id"]
    assert deliverable.review_cohort_sealed_at is not None
    assert {
        reread.review_requests[review_id].review_cohort_id
        for review_id in result["created_review_request_ids"]
    } == {result["review_cohort_id"]}
    assert reread.tasks["task"].state == TeamTaskState.WAITING_FOR_REVIEW

    _set_agent_status(repo, "tester", AgentStatus.PAUSED)
    duplicate = provider.execute(
        resolved_config={
            "expected_revision": repo.read().revision,
        }
    )

    assert duplicate["success"] is True
    assert duplicate["created"] is False
    assert duplicate["created_review_request_ids"] == []
    assert set(duplicate["existing_review_request_ids"]) == set(
        result["created_review_request_ids"]
    )
    assert duplicate["created_inbox_item_ids"] == []
    assert len(repo.read().review_requests) == 2


def test_create_review_cohort_effect_rejects_cross_team_manager(
    tmp_path: Path,
) -> None:
    repo = _seed_team(tmp_path)
    _admin(repo).create_team(
        team_id="other-team",
        name="Other Team",
        manager_agent_id="other-manager",
        manager_session_id="session-other-manager",
        expected_revision=repo.read().revision,
    )
    _record_deliverable(repo, "deliverable")
    provider = TeamCreateReviewCohortEffectProvider()
    provider.initialize(
        TeamCreateReviewCohortEffectDefinition(
            id="cross-team-review-cohort",
            display_name="Cross-team review cohort",
            type="effect:team:create-review-cohort",
            actor_agent_id="other-manager",
            deliverable_id="deliverable",
            reviews=[
                {
                    "reviewer_agent_id": "reviewer",
                    "review_profile_id": "code-review",
                }
            ],
            expected_revision=repo.read().revision,
        ),
        EffectRuntimeContext(resolved_config={"runtime_dir": str(tmp_path)}),
    )

    result = provider.execute()

    assert result["success"] is False
    assert result["error"] == (
        "agent other-manager is not permitted to request review of deliverable deliverable"
    )
    deliverable = repo.read().deliverables["deliverable"]
    assert deliverable.review_cohort_id is None
    assert deliverable.review_cohort_sealed_at is None
    assert repo.read().review_requests == {}


def test_create_review_cohort_effect_reopens_completed_project(
    tmp_path: Path,
) -> None:
    repo = _seed_team(tmp_path)
    _record_deliverable(repo, "deliverable")

    def _complete_project(state):
        state.deliverables["deliverable"].state = TeamRecordState.ACCEPTED
        state.tasks["task"].state = TeamTaskState.ACCEPTED
        state.projects["project"].goals[0].state = TeamRecordState.ACCEPTED
        state.projects["project"].status = ProjectStatus.COMPLETED
        return state

    repo.update(_complete_project, expected_revision=repo.read().revision)
    provider = TeamCreateReviewCohortEffectProvider()
    provider.initialize(
        TeamCreateReviewCohortEffectDefinition(
            id="reopen-review-cohort",
            display_name="Reopen review cohort",
            type="effect:team:create-review-cohort",
            actor_agent_id="lead",
            deliverable_id="deliverable",
            reviews=[
                {
                    "reviewer_agent_id": "reviewer",
                    "review_profile_id": "code-review",
                }
            ],
            expected_revision=repo.read().revision,
        ),
        EffectRuntimeContext(resolved_config={"runtime_dir": str(tmp_path)}),
    )

    result = provider.execute()

    assert result["success"] is True
    reread = repo.read()
    assert reread.deliverables["deliverable"].state == TeamRecordState.PENDING
    assert reread.tasks["task"].state == TeamTaskState.WAITING_FOR_REVIEW
    assert reread.projects["project"].status == ProjectStatus.ACTIVE


def test_create_review_cohort_effect_retry_preserves_accepted_cohort(
    tmp_path: Path,
) -> None:
    repo = _seed_team(tmp_path)
    _record_deliverable(repo, "deliverable")
    provider = TeamCreateReviewCohortEffectProvider()
    provider.initialize(
        TeamCreateReviewCohortEffectDefinition(
            id="retry-accepted-review-cohort",
            display_name="Retry accepted review cohort",
            type="effect:team:create-review-cohort",
            actor_agent_id="lead",
            deliverable_id="deliverable",
            reviews=[
                {
                    "reviewer_agent_id": "reviewer",
                    "review_profile_id": "code-review",
                }
            ],
            expected_revision=repo.read().revision,
        ),
        EffectRuntimeContext(resolved_config={"runtime_dir": str(tmp_path)}),
    )
    created = provider.execute()
    review_id = created["created_review_request_ids"][0]

    def _accept_cohort(state):
        state.review_requests[review_id].state = TeamRecordState.ACCEPTED
        state.deliverables["deliverable"].state = TeamRecordState.ACCEPTED
        state.tasks["task"].state = TeamTaskState.FINISHED
        state.projects["project"].goals[0].state = TeamRecordState.ACCEPTED
        state.projects["project"].status = ProjectStatus.COMPLETED
        return state

    repo.update(_accept_cohort, expected_revision=repo.read().revision)

    replayed = provider.execute(resolved_config={"expected_revision": repo.read().revision})

    assert replayed["success"] is True
    assert replayed["created"] is False
    assert replayed["existing_review_request_ids"] == [review_id]
    reread = repo.read()
    assert reread.deliverables["deliverable"].state == TeamRecordState.ACCEPTED
    assert reread.tasks["task"].state == TeamTaskState.FINISHED
    assert reread.projects["project"].status == ProjectStatus.COMPLETED


def test_create_review_cohort_effect_retry_preserves_rejected_cohort(
    tmp_path: Path,
) -> None:
    repo = _seed_team(tmp_path)
    _record_deliverable(repo, "deliverable")
    provider = TeamCreateReviewCohortEffectProvider()
    provider.initialize(
        TeamCreateReviewCohortEffectDefinition(
            id="retry-rejected-review-cohort",
            display_name="Retry rejected review cohort",
            type="effect:team:create-review-cohort",
            actor_agent_id="lead",
            deliverable_id="deliverable",
            reviews=[
                {
                    "reviewer_agent_id": "reviewer",
                    "review_profile_id": "code-review",
                }
            ],
            expected_revision=repo.read().revision,
        ),
        EffectRuntimeContext(resolved_config={"runtime_dir": str(tmp_path)}),
    )
    created = provider.execute()
    review_id = created["created_review_request_ids"][0]

    def _reject_cohort(state):
        state.review_requests[review_id].state = TeamRecordState.REJECTED
        state.deliverables["deliverable"].state = TeamRecordState.REJECTED
        state.tasks["task"].state = TeamTaskState.IN_PROGRESS
        return state

    repo.update(_reject_cohort, expected_revision=repo.read().revision)

    replayed = provider.execute(resolved_config={"expected_revision": repo.read().revision})

    assert replayed["success"] is True
    assert replayed["created"] is False
    assert replayed["existing_review_request_ids"] == [review_id]
    reread = repo.read()
    assert reread.deliverables["deliverable"].state == TeamRecordState.REJECTED
    assert reread.tasks["task"].state == TeamTaskState.IN_PROGRESS
    assert reread.projects["project"].status == ProjectStatus.ACTIVE


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
