from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from evander_team_addon.models import (
    AgentContextClearingPolicy,
    AgentInboxItem,
    AgentRuntime,
    Deliverable,
    DeliverableGitRefs,
    DeliverableInitialReleaseDisposition,
    HumanReviewRequest,
    HumanReviewRequirementSnapshot,
    ProjectGoal,
    ProjectReleasePolicy,
    ReleasePackage,
    ReviewRequest,
    TeamBlocker,
    TeamBlockerSourceKind,
    TeamDispatch,
    TeamDispatchStatus,
    TeamInboxItemState,
    TeamInboxItemType,
    TeamInboxPriority,
    TeamRecordState,
    TeamState,
    TeamTaskState,
    wake_work_fingerprint,
    wake_work_record_fingerprints,
)
from evander_team_addon.repository import TeamStateRepository
from evander_team_addon.services.admin_service import TeamAdminService
from evander_team_addon.services.runtime_service import TeamRuntimeService


NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _seed_repo(tmp_path: Path) -> TeamStateRepository:
    repo = TeamStateRepository(tmp_path)
    admin = TeamAdminService(repo)
    admin.create_team(
        team_id="team",
        name="Team",
        manager_agent_id="manager",
        manager_session_id="session-manager",
        expected_revision=0,
    )
    admin.create_project(
        project_id="project",
        team_id="team",
        name="Project",
        lead_agent_id="lead",
        lead_session_id="session-lead",
        goals=[ProjectGoal(id="goal", description="Ship work")],
        expected_revision=repo.read().revision,
    )
    admin.create_role(
        role_id="project__backend",
        team_id="team",
        project_id="project",
        name="Backend",
        expected_revision=repo.read().revision,
    )
    admin.register_agent(
        agent_id="backend",
        team_id="team",
        project_id="project",
        role_id="project__backend",
        session_id="session-backend",
        reports_to_agent_id="lead",
        expected_revision=repo.read().revision,
    )
    return repo


def _create_task(
    repo: TeamStateRepository,
    *,
    task_id: str,
    owner_agent_id: str = "backend",
    priority: int,
) -> TeamState:
    return TeamAdminService(repo).create_or_reprioritize_task(
        task_id=task_id,
        owner_agent_id=owner_agent_id,
        project_id="project",
        priority=priority,
        title=task_id,
        description=f"Work for {task_id}.",
        created_by_agent_id="lead",
        expected_revision=repo.read().revision,
    )


def _set_work_lineage_policy(repo: TeamStateRepository, agent_id: str) -> None:
    TeamAdminService(repo).update_agent(
        agent_id=agent_id,
        context_clearing_policy=AgentContextClearingPolicy.WORK_LINEAGE,
        set_context_clearing_policy=True,
        expected_revision=repo.read().revision,
    )


def _defer_backend(repo: TeamStateRepository) -> None:
    def _mutate(state: TeamState) -> TeamState:
        runtime = state.agent_runtimes.setdefault(
            "backend",
            AgentRuntime(agent_id="backend"),
        )
        runtime.next_wake_at = NOW + timedelta(hours=1)
        return state

    repo.update(_mutate)


def test_manager_initial_release_authority_is_limited_to_selected_lineage(
    tmp_path: Path,
) -> None:
    repo = _seed_repo(tmp_path)
    _create_task(repo, task_id="task-a", priority=1)
    _create_task(repo, task_id="task-b", priority=2)
    _set_work_lineage_policy(repo, "manager")

    def _seed_pending_releases(state: TeamState) -> TeamState:
        state.projects["project"].release_policy = ProjectReleasePolicy(allow_ungated=True)
        for suffix in ("a", "b"):
            task = state.tasks[f"task-{suffix}"]
            task.state = TeamTaskState.FINISHED
            deliverable_id = f"deliverable-{suffix}"
            state.deliverables[deliverable_id] = Deliverable(
                id=deliverable_id,
                task_id=task.id,
                producer_agent_id="backend",
                summary=f"Pending initial release {suffix}.",
                state=TeamRecordState.ACCEPTED,
                git_refs=DeliverableGitRefs(
                    feature_branch=f"team/team/features/task-{suffix}",
                    commit_sha=f"commit-{suffix}",
                ),
                initial_release_disposition=DeliverableInitialReleaseDisposition(
                    created_at=NOW + timedelta(seconds=0 if suffix == "a" else 1),
                    updated_at=NOW + timedelta(seconds=0 if suffix == "a" else 1),
                ),
                created_at=NOW + timedelta(seconds=0 if suffix == "a" else 1),
            )
            task.deliverable_ids = [deliverable_id]
        return state

    repo.update(_seed_pending_releases)
    _state, leases = TeamRuntimeService(repo).lease_due_agents(now=NOW)

    lease = next(row for row in leases if row["agent"]["id"] == "manager")
    selected_lineage_id = repo.read().tasks["task-a"].work_lineage_id
    assert lease["dispatch"]["work_lineage_id"] == selected_lineage_id
    assert lease["dispatch"]["initial_release_decision_deliverable_ids"] == [
        "deliverable-a"
    ]
    assert [
        row["deliverable_id"]
        for row in lease["manager_fallback_summary"][
            "pending_initial_release_decisions"
        ]
    ] == ["deliverable-a"]


def test_exhausted_ordinary_lineage_selects_new_review_from_another_lineage(
    tmp_path: Path,
) -> None:
    repo = _seed_repo(tmp_path)
    _create_task(repo, task_id="task-a", priority=1)
    _create_task(repo, task_id="task-b", priority=2)
    _set_work_lineage_policy(repo, "backend")

    def _seed_reviews_and_attempt(state: TeamState) -> TeamState:
        reviews: dict[str, ReviewRequest] = {}
        for suffix in ("a", "b"):
            task = state.tasks[f"task-{suffix}"]
            task.state = TeamTaskState.WAITING_FOR_REVIEW
            deliverable_id = f"deliverable-{suffix}"
            review_id = f"review-{suffix}"
            state.deliverables[deliverable_id] = Deliverable(
                id=deliverable_id,
                task_id=task.id,
                producer_agent_id="backend",
                summary=f"Review {suffix}.",
                created_at=NOW + timedelta(seconds=0 if suffix == "a" else 1),
            )
            task.deliverable_ids = [deliverable_id]
            review = ReviewRequest(
                id=review_id,
                deliverable_id=deliverable_id,
                reviewer_agent_id="backend",
                created_at=NOW + timedelta(seconds=0 if suffix == "a" else 1),
            )
            state.review_requests[review_id] = review
            reviews[suffix] = review
        state.dispatches["prior-a"] = TeamDispatch(
            id="prior-a",
            agent_id="backend",
            session_id="session-backend",
            cause_kind="review",
            work_lineage_id=state.tasks["task-a"].work_lineage_id,
            review_request_ids=["review-a"],
            wake_work_fingerprint=wake_work_fingerprint(
                inbox_items=[],
                reviews=[reviews["a"]],
            ),
            wake_work_record_fingerprints=wake_work_record_fingerprints(
                inbox_items=[],
                reviews=[reviews["a"]],
            ),
            status=TeamDispatchStatus.COMPLETED,
            created_at=NOW - timedelta(minutes=2),
            prepared_at=NOW - timedelta(minutes=2),
            leased_at=NOW - timedelta(minutes=2),
            lease_expires_at=NOW - timedelta(minutes=1),
            completed_at=NOW - timedelta(minutes=1),
        )
        state.agent_runtimes["backend"].no_progress_count = 3
        return state

    repo.update(_seed_reviews_and_attempt)
    _state, leases = TeamRuntimeService(repo).lease_due_agents(
        agent_id="backend",
        now=NOW,
        max_no_progress_count=3,
        respect_wake_interval=False,
    )

    assert len(leases) == 1
    assert leases[0]["dispatch"]["work_lineage_id"] == repo.read().tasks[
        "task-b"
    ].work_lineage_id
    assert leases[0]["dispatch"]["review_request_ids"] == ["review-b"]


def test_exhausted_lineage_selects_unattempted_review_before_fingerprinting(
    tmp_path: Path,
) -> None:
    repo = _seed_repo(tmp_path)
    _create_task(repo, task_id="task", priority=1)
    _set_work_lineage_policy(repo, "backend")

    def _seed_reviews_and_attempt(state: TeamState) -> TeamState:
        task = state.tasks["task"]
        task.state = TeamTaskState.WAITING_FOR_REVIEW
        reviews: list[ReviewRequest] = []
        for index in (1, 2):
            deliverable_id = f"deliverable-{index}"
            review_id = f"review-{index}"
            state.deliverables[deliverable_id] = Deliverable(
                id=deliverable_id,
                task_id=task.id,
                producer_agent_id="backend",
                summary=f"Review {index}.",
                created_at=NOW + timedelta(seconds=index),
            )
            review = ReviewRequest(
                id=review_id,
                deliverable_id=deliverable_id,
                reviewer_agent_id="backend",
                created_at=NOW + timedelta(seconds=index),
            )
            state.review_requests[review_id] = review
            task.deliverable_ids.append(deliverable_id)
            reviews.append(review)
        state.dispatches["prior-review-1"] = TeamDispatch(
            id="prior-review-1",
            agent_id="backend",
            session_id="session-backend",
            cause_kind="review",
            work_lineage_id=task.work_lineage_id,
            review_request_ids=[reviews[0].id],
            wake_work_fingerprint=wake_work_fingerprint(
                inbox_items=[],
                reviews=[reviews[0]],
            ),
            wake_work_record_fingerprints=wake_work_record_fingerprints(
                inbox_items=[],
                reviews=[reviews[0]],
            ),
            status=TeamDispatchStatus.COMPLETED,
            created_at=NOW - timedelta(minutes=2),
            prepared_at=NOW - timedelta(minutes=2),
            leased_at=NOW - timedelta(minutes=2),
            lease_expires_at=NOW - timedelta(minutes=1),
            completed_at=NOW - timedelta(minutes=1),
        )
        state.agent_runtimes["backend"].no_progress_count = 3
        return state

    repo.update(_seed_reviews_and_attempt)
    runtime_service = TeamRuntimeService(repo)
    _state, leases = runtime_service.lease_due_agents(
        agent_id="backend",
        now=NOW,
        max_no_progress_count=3,
        respect_wake_interval=False,
    )

    assert len(leases) == 1
    assert leases[0]["dispatch"]["review_request_ids"] == ["review-2"]
    expected_fingerprint = wake_work_fingerprint(
        inbox_items=[],
        reviews=[repo.read().review_requests["review-2"]],
    )
    assert leases[0]["dispatch"]["wake_work_fingerprint"] == expected_fingerprint

    _state, finalized = runtime_service.finalize_agent_dispatch_from_activity(
        session_id="session-backend",
        dispatch_id=leases[0]["dispatch_id"],
    )
    assert finalized["success"] is True
    _state, replay = runtime_service.lease_due_agents(
        agent_id="backend",
        now=NOW + timedelta(seconds=1),
        max_no_progress_count=3,
        respect_wake_interval=False,
    )
    assert replay == []


def test_manager_fallback_chooses_lineage_after_removing_manager_owned_work(
    tmp_path: Path,
) -> None:
    repo = _seed_repo(tmp_path)
    _create_task(repo, task_id="manager-blocked", owner_agent_id="manager", priority=1)
    _create_task(repo, task_id="subordinate-work", priority=2)
    _set_work_lineage_policy(repo, "manager")
    _defer_backend(repo)

    def _block_manager_task(state: TeamState) -> TeamState:
        state.tasks["manager-blocked"].state = TeamTaskState.BLOCKED
        return state

    repo.update(_block_manager_task)
    _state, leases = TeamRuntimeService(repo).lease_due_agents(now=NOW)

    lease = next(row for row in leases if row["agent"]["id"] == "manager")
    assert lease["dispatch"]["work_lineage_id"] == repo.read().tasks[
        "subordinate-work"
    ].work_lineage_id
    assert lease["dispatch"]["task_ids"] == ["subordinate-work"]


def test_exhausted_manager_fallback_reselects_unattempted_lineage(
    tmp_path: Path,
) -> None:
    repo = _seed_repo(tmp_path)
    _create_task(repo, task_id="task-a", priority=1)
    _create_task(repo, task_id="task-b", priority=2)
    _set_work_lineage_policy(repo, "manager")
    _defer_backend(repo)
    runtime = TeamRuntimeService(repo)

    _state, first_leases = runtime.lease_due_agents(
        now=NOW,
        max_no_progress_count=1,
    )
    first = next(row for row in first_leases if row["agent"]["id"] == "manager")
    assert first["dispatch"]["work_lineage_id"] == repo.read().tasks[
        "task-a"
    ].work_lineage_id
    state, finalized = runtime.finalize_agent_dispatch_from_activity(
        session_id="session-manager",
        dispatch_id=first["dispatch_id"],
    )
    assert finalized["success"] is True
    assert state.agent_runtimes["manager"].no_progress_count == 1

    _state, second_leases = runtime.lease_due_agents(
        now=NOW + timedelta(seconds=1),
        max_no_progress_count=1,
    )

    second = next(row for row in second_leases if row["agent"]["id"] == "manager")
    assert second["dispatch"]["work_lineage_id"] == repo.read().tasks[
        "task-b"
    ].work_lineage_id
    assert second["dispatch"]["task_ids"] == ["task-b"]


def test_work_lineage_lease_filters_releases_and_human_review_attention(
    tmp_path: Path,
) -> None:
    repo = _seed_repo(tmp_path)
    _create_task(repo, task_id="task-a", priority=1)
    _create_task(repo, task_id="task-b", priority=2)
    _set_work_lineage_policy(repo, "backend")

    def _seed_releases(state: TeamState) -> TeamState:
        lineage_a = state.tasks["task-a"].work_lineage_id
        lineage_b = state.tasks["task-b"].work_lineage_id
        state.release_packages["release-a"] = ReleasePackage(
            id="release-a",
            project_id="project",
            work_lineage_id=lineage_a,
            state=TeamRecordState.BLOCKED,
            error="Release A needs attention.",
        )
        state.release_packages["release-b"] = ReleasePackage(
            id="release-b",
            project_id="project",
            work_lineage_id=lineage_b,
            candidate_commit_sha="a" * 40,
            candidate_tree_hash="b" * 40,
            state=TeamRecordState.BLOCKED,
            error="Release B needs attention.",
        )
        state.human_review_requests["human-review-b"] = HumanReviewRequest(
            id="human-review-b",
            release_package_id="release-b",
            candidate_commit_sha="a" * 40,
            candidate_tree_hash="b" * 40,
            requirement_snapshot=HumanReviewRequirementSnapshot(
                directory_path="root",
                configured_group_ids=["reviewers"],
                initially_eligible_user_ids=[],
            ),
            state="preparing",
        )
        state.blockers["human-review-blocker-b"] = TeamBlocker(
            id="human-review-blocker-b",
            team_id="team",
            project_id="project",
            source_kind=TeamBlockerSourceKind.HUMAN_REVIEW_REQUEST,
            source_id="human-review-b",
            owner_agent_id="manager",
            summary="Unrelated human review needs recovery.",
        )
        return state

    repo.update(_seed_releases)
    _state, leases = TeamRuntimeService(repo).lease_due_agents(
        agent_id="backend",
        now=NOW,
        respect_wake_interval=False,
    )

    assert len(leases) == 1
    lease = leases[0]
    assert lease["dispatch"]["work_lineage_id"] == repo.read().tasks[
        "task-a"
    ].work_lineage_id
    assert set(lease["release_packages"]) == {"release-a"}
    attention_ids = {row["id"] for row in lease["runtime_attention"]}
    assert "release-a" in attention_ids
    assert "release-b" not in attention_ids
    assert "human-review-blocker-b" not in attention_ids


def test_filtered_low_priority_lineage_preserves_batch_until_all_low_work_is_included(
    tmp_path: Path,
) -> None:
    repo = _seed_repo(tmp_path)
    _create_task(repo, task_id="task-a", priority=1)
    _create_task(repo, task_id="task-b", priority=2)
    _set_work_lineage_policy(repo, "backend")
    batch_started_at = NOW - timedelta(minutes=30)

    def _seed_low_priority_work(state: TeamState) -> TeamState:
        for suffix in ("a", "b"):
            state.inbox[f"low-{suffix}"] = AgentInboxItem(
                id=f"low-{suffix}",
                recipient_agent_id="backend",
                task_id=f"task-{suffix}",
                item_type=TeamInboxItemType.PROGRESS_REPORT,
                response_requested=True,
                priority=TeamInboxPriority.LOW,
                state=TeamInboxItemState.PENDING,
            )
        runtime = state.agent_runtimes["backend"]
        runtime.low_priority_batch_started_at = batch_started_at
        runtime.low_priority_batch_cycle_count = 2
        runtime.low_priority_batch_due_after_cycles = 4
        return state

    repo.update(_seed_low_priority_work)
    state, leases = TeamRuntimeService(repo).lease_due_agents(
        agent_id="backend",
        now=NOW,
        low_priority_wake_after_cycles=4,
        respect_wake_interval=False,
    )

    assert len(leases) == 1
    assert leases[0]["dispatch"]["work_lineage_id"] == state.tasks[
        "task-a"
    ].work_lineage_id
    assert [row["id"] for row in leases[0]["inbox"]] == ["low-a"]
    runtime = state.agent_runtimes["backend"]
    assert runtime.low_priority_batch_started_at == batch_started_at
    assert runtime.low_priority_batch_cycle_count == 2
    assert runtime.low_priority_batch_due_after_cycles == 4
