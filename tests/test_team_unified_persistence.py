from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from evander_team_addon.models import (
    AgentInboxItem,
    AgentTask,
    Deliverable,
    DeliverableGitRefs,
    DeliverableVerification,
    DependencyCompositionManifest,
    DependencyCompositionRecovery,
    DependencyGitSource,
    GitWorkContext,
    HumanReviewAssignment,
    HumanReviewChecklistItem,
    HumanReviewDecision,
    HumanReviewFinding,
    HumanReviewFindingSnapshot,
    HumanReviewRequest,
    HumanReviewRequirementSnapshot,
    HumanReviewTestTarget,
    ProjectGitConfig,
    ProjectGoal,
    ReleaseCandidateDeliverableEvidence,
    ReleaseCandidateManifest,
    ReleasePackage,
    ReleaseReviewRequirement,
    ReviewProfile,
    ReviewRequest,
    TaskCancellation,
    TaskCompletion,
    TaskCompletionKind,
    TaskDeliverableContract,
    TeamBlocker,
    TeamBlockerRoute,
    TeamBlockerSourceKind,
    TeamBlockerState,
    TeamDispatch,
    TeamDispatchStatus,
    TeamInboxItemState,
    TeamInboxItemType,
    TeamRecordState,
    TeamTaskState,
    WorktreeCleanupCandidate,
    WorktreeCleanupState,
    summarize_worktree_cleanup_inventory,
)
from evander_team_addon.repository import TeamStateRepository
from evander_team_addon.services.admin_service import TeamAdminService
from evander_team_addon.services.chat_service import TeamChatService
from evander_team_addon.services.runtime_service import TeamRuntimeService
from evander_team_addon.sqlite_store import (
    SQLITE_SCHEMA_VERSION,
    _PERSISTENCE_GROUPS,
    _STATE_TABLES,
)


MANAGED_REPO_ID = "mr_0123456789abcdefabcd"
NOW = datetime(2026, 7, 25, 10, 0, tzinfo=UTC)


def test_persistence_registry_covers_every_collection_and_table_once() -> None:
    fields = [field for group, _specs, _writer in _PERSISTENCE_GROUPS for field in group]
    tables = [
        table for _group, specs, _writer in _PERSISTENCE_GROUPS for table, _column, _field in specs
    ]

    assert len(fields) == len(set(fields)) == 38
    assert len(tables) == len(set(tables))
    assert set(tables) == set(_STATE_TABLES)


def test_review_criterion_evidence_column_migrates_from_legacy_table(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "addons" / "team-addon" / "state.db"
    database_path.parent.mkdir(parents=True)
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            CREATE TABLE team_review_request_criteria (
                review_request_id TEXT NOT NULL,
                position INTEGER NOT NULL,
                criterion_id TEXT NOT NULL,
                state TEXT NOT NULL,
                finding TEXT NOT NULL,
                PRIMARY KEY(review_request_id, position)
            )
            """
        )

    repository = TeamStateRepository(tmp_path)

    with repository.store._connect() as connection:
        columns = {
            row["name"]: row
            for row in connection.execute(
                "PRAGMA table_info(team_review_request_criteria)"
            )
        }
    assert columns["evidence_refs_json"]["notnull"] == 1
    assert columns["evidence_refs_json"]["dflt_value"] == "'[]'"


def test_dependency_composition_recovery_round_trips_exact_git_evidence(
    tmp_path: Path,
) -> None:
    repository = _seed_team_repository(tmp_path)
    recovery = DependencyCompositionRecovery(
        id="depcomp-roundtrip",
        blocker_id="blocker-roundtrip",
        target_task_id="task-roundtrip",
        project_id="project",
        managed_repo_id=MANAGED_REPO_ID,
        directory_path="root",
        base_branch="main",
        base_commit_sha="a" * 40,
        base_tree_hash="b" * 40,
        dependency_git_sources=[
            DependencyGitSource(
                dependency_task_id="source-task",
                deliverable_id="source-deliverable",
                branch="agents/source/task",
                commit_sha="c" * 40,
            )
        ],
        source_fingerprint="f" * 64,
        preparation_generation="generation-one",
        failed_dependency_task_id="source-task",
        failed_deliverable_id="source-deliverable",
        conflict_paths=["src/shared.py"],
        created_at=NOW,
        updated_at=NOW,
    )

    repository.update(
        lambda state: (
            state.dependency_composition_recoveries.__setitem__(recovery.id, recovery) or state
        )
    )

    restored = repository.read().dependency_composition_recoveries[recovery.id]
    assert restored == recovery
    with repository.store._connect() as connection:
        row = connection.execute(
            "SELECT directory_path, dependency_git_sources_json, conflict_paths_json "
            "FROM team_dependency_composition_recoveries WHERE id = ?",
            (recovery.id,),
        ).fetchone()
    assert row is not None
    assert row["directory_path"] == "root"
    assert json.loads(row["dependency_git_sources_json"])[0]["commit_sha"] == "c" * 40
    assert json.loads(row["conflict_paths_json"]) == ["src/shared.py"]


def test_acceptance_provenance_and_composition_manifest_round_trip(
    tmp_path: Path,
) -> None:
    repository = _seed_team_repository(tmp_path)
    admin = TeamAdminService(repository)
    admin.create_or_reprioritize_task(
        task_id="integration",
        owner_agent_id="lead",
        project_id="project",
        priority=1,
        title="Integrate",
        description="Compose accepted work.",
        created_by_agent_id="lead",
        integration_boundary=True,
        deliverable_contract=TaskDeliverableContract(
            require_git_provenance=True,
        ).model_dump(mode="json"),
    )
    source = DependencyGitSource(
        dependency_task_id="source-task",
        deliverable_id="source-deliverable",
        branch="agents/source/task",
        commit_sha="c" * 40,
        relationship_kind="required_contribution",
        pinned_ref="refs/evander/team-reviews/source-review",
    )
    manifest = DependencyCompositionManifest(
        target_task_id="integration",
        managed_repo_id=MANAGED_REPO_ID,
        directory_path="root",
        base_branch="main",
        base_commit_sha="a" * 40,
        base_tree_hash="b" * 40,
        dependency_git_sources=[source],
        source_fingerprint="f" * 64,
        frontier_commit_shas=["c" * 40],
        composed_commit_sha="d" * 40,
        composed_tree_hash="e" * 40,
    )

    def _seed(state):
        state.git_work_contexts["context-integration"] = GitWorkContext(
            id="context-integration",
            agent_id="lead",
            task_id="integration",
            agent_branch="agents/lead/integration",
            base_branch="main",
            base_commit="a" * 40,
            commit_sha="d" * 40,
            dependency_git_sources=[source],
            dependency_composition_manifest=manifest,
        )
        state.deliverables["deliverable-integration"] = Deliverable(
            id="deliverable-integration",
            task_id="integration",
            producer_agent_id="lead",
            summary="Integrated candidate",
            git_refs=DeliverableGitRefs(
                branch_context_id="context-integration",
                agent_branch="agents/lead/integration",
                commit_sha="d" * 40,
                submitted_commit_sha="d" * 40,
                accepted_pinned_ref="refs/evander/team-reviews/integration-review",
            ),
            dependency_composition_manifest=manifest,
            acceptance_policy_version=2,
            verification=DeliverableVerification(
                status="not_run",
                provenance="server_release_equivalent",
                run_id="acceptance-verification-roundtrip",
                candidate_commit_sha="d" * 40,
            ),
        )
        state.tasks["integration"].deliverable_ids = ["deliverable-integration"]
        return state

    repository.update(_seed)
    reloaded = TeamStateRepository(tmp_path).read()

    assert (
        reloaded.git_work_contexts["context-integration"].dependency_composition_manifest
        == manifest
    )
    restored = reloaded.deliverables["deliverable-integration"]
    assert restored.dependency_composition_manifest == manifest
    assert restored.git_refs.accepted_pinned_ref == (
        "refs/evander/team-reviews/integration-review"
    )
    assert restored.acceptance_policy_version == 2
    assert restored.verification is not None
    assert restored.verification.run_id == "acceptance-verification-roundtrip"


def test_noop_updates_and_empty_wake_checks_preserve_revision_and_skip_state_dml(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = _seed_team_repository(tmp_path)
    statements: list[str] = []
    original_connect = repository.store._connect

    def _traced_connect() -> sqlite3.Connection:
        connection = original_connect()
        connection.set_trace_callback(statements.append)
        return connection

    monkeypatch.setattr(repository.store, "_connect", _traced_connect)
    before = repository.read()
    statements.clear()

    after = repository.update(lambda state: state)
    after_chat, claims = TeamChatService(repository).claim_pending_chat_message_dispatches(
        agent_id="missing-agent"
    )
    after_lease, leases = TeamRuntimeService(repository).lease_due_agents(agent_id="missing-agent")

    assert after.revision == before.revision
    assert after_chat.revision == before.revision
    assert after_lease.revision == before.revision
    assert claims == []
    assert leases == []
    state_dml = [
        statement
        for statement in statements
        if statement.lstrip().upper().startswith(("DELETE ", "INSERT ", "UPDATE "))
        and any(table in statement for table in _STATE_TABLES)
    ]
    assert state_dml == []


def test_single_record_update_only_rewrites_its_persistence_group(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = _seed_team_repository(tmp_path)
    statements: list[str] = []
    original_connect = repository.store._connect

    def _traced_connect() -> sqlite3.Connection:
        connection = original_connect()
        connection.set_trace_callback(statements.append)
        return connection

    monkeypatch.setattr(repository.store, "_connect", _traced_connect)

    def _rename_project(state):
        state.projects["project"].name = "Renamed project"
        return state

    before = repository.read()
    statements.clear()
    after = repository.update(_rename_project)

    assert after.revision == before.revision + 1
    state_dml = "\n".join(
        statement
        for statement in statements
        if statement.lstrip().upper().startswith(("DELETE ", "INSERT ", "UPDATE "))
    )
    assert "team_projects" in state_dml
    assert "team_project_goals" in state_dml
    assert all(
        table not in state_dml
        for table in _STATE_TABLES
        if table not in {"team_projects", "team_project_goals"}
    )


def _seed_team_repository(tmp_path: Path) -> TeamStateRepository:
    repository = TeamStateRepository(tmp_path)
    admin = TeamAdminService(repository)
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
        goals=[ProjectGoal(id="goal", description="Ship the candidate")],
        git=ProjectGitConfig(
            managed_repo_id=MANAGED_REPO_ID,
            directory_path="root",
            base_branch="main",
        ),
        expected_revision=repository.read().revision,
    )
    return repository


def test_repository_round_trips_sealed_release_review_cohort_without_legacy_backfill(
    tmp_path: Path,
) -> None:
    repository = _seed_team_repository(tmp_path)
    cohort_id = "release-review-cohort-release-cohort"

    def _seed(state):
        state.review_profiles["release-profile"] = ReviewProfile(
            id="release-profile",
            team_id="team",
            project_id="project",
            name="Release review",
        )
        state.release_packages["release-cohort"] = ReleasePackage(
            id="release-cohort",
            project_id="project",
            candidate_commit_sha="c" * 40,
            release_branch="team/team/releases/release-cohort",
            manifest=ReleaseCandidateManifest(
                project_id="project",
                release_package_id="release-cohort",
                candidate_commit_sha="c" * 40,
                release_branch="team/team/releases/release-cohort",
                review_requirements=[
                    ReleaseReviewRequirement(
                        reviewer_agent_id="lead",
                        review_profile_id="release-profile",
                    )
                ],
            ),
            review_request_ids=["release-review-cohort"],
            review_cohort_id=cohort_id,
            review_cohort_sealed_at=NOW,
            created_at=NOW,
        )
        state.review_requests["release-review-cohort"] = ReviewRequest(
            id="release-review-cohort",
            release_package_id="release-cohort",
            candidate_commit_sha="c" * 40,
            reviewed_commit_sha="c" * 40,
            reviewer_agent_id="lead",
            review_profile_id="release-profile",
            review_cohort_id=cohort_id,
            created_at=NOW,
        )
        return state

    repository.update(_seed)
    reloaded = TeamStateRepository(tmp_path).read()

    release = reloaded.release_packages["release-cohort"]
    review = reloaded.review_requests["release-review-cohort"]
    assert release.review_cohort_id == cohort_id
    assert release.review_cohort_sealed_at == NOW
    assert review.review_cohort_id == cohort_id


def test_repository_round_trips_integration_and_leaf_deliverable_contracts(
    tmp_path: Path,
) -> None:
    repository = _seed_team_repository(tmp_path)
    admin = TeamAdminService(repository)
    admin.create_or_reprioritize_task(
        task_id="dependency",
        owner_agent_id="lead",
        project_id="project",
        priority=10,
        title="Implement component",
        description="Produce the component contribution.",
        created_by_agent_id="lead",
    )
    admin.create_or_reprioritize_task(
        task_id="contracted-leaf",
        owner_agent_id="lead",
        project_id="project",
        priority=15,
        title="Correct candidate",
        description="Produce one reviewed Git correction.",
        created_by_actor="system:release-review-correction",
        integration_boundary=False,
        deliverable_contract=TaskDeliverableContract(
            require_git_provenance=True,
        ).model_dump(mode="json"),
    )
    admin.create_or_reprioritize_task(
        task_id="integration",
        owner_agent_id="lead",
        project_id="project",
        priority=20,
        title="Integrate candidate",
        description="Compose and submit the final candidate.",
        created_by_agent_id="lead",
        dependency_task_ids=["dependency"],
        integration_boundary=True,
        deliverable_contract=TaskDeliverableContract(
            require_git_provenance=True,
            required_contribution_task_ids=["dependency"],
        ).model_dump(mode="json"),
    )

    reloaded = TeamStateRepository(tmp_path).read()

    legacy = reloaded.tasks["dependency"]
    assert legacy.integration_boundary is False
    assert legacy.deliverable_contract is None
    contracted_leaf = reloaded.tasks["contracted-leaf"]
    assert contracted_leaf.integration_boundary is False
    assert contracted_leaf.deliverable_contract is not None
    assert contracted_leaf.deliverable_contract.require_git_provenance is True
    assert contracted_leaf.deliverable_contract.required_contribution_task_ids == []
    integration = reloaded.tasks["integration"]
    assert integration.integration_boundary is True
    assert integration.deliverable_contract is not None
    assert integration.deliverable_contract.require_git_provenance is True
    assert integration.deliverable_contract.required_contribution_task_ids == ["dependency"]


def test_repository_round_trips_unified_hierarchy_human_review_records(
    tmp_path: Path,
) -> None:
    repository = _seed_team_repository(tmp_path)

    def seed_unified_state(state):
        state.tasks.update(
            {
                "aggregate-task": AgentTask(
                    id="aggregate-task",
                    owner_agent_id="lead",
                    project_id="project",
                    priority=10,
                    state=TeamTaskState.FINISHED,
                    title="Assemble the release",
                    description="Integrate accepted child output.",
                    created_by_actor="operator:admin",
                    deliverable_ids=["aggregate-deliverable"],
                    created_at=NOW,
                    updated_at=NOW,
                ),
                "source-task": AgentTask(
                    id="source-task",
                    owner_agent_id="lead",
                    project_id="project",
                    priority=20,
                    state=TeamTaskState.FINISHED,
                    title="Produce the component",
                    description="Create direct child output.",
                    created_by_actor="operator:admin",
                    parent_task_id="aggregate-task",
                    deliverable_ids=["source-deliverable"],
                    created_at=NOW,
                    updated_at=NOW,
                ),
                "successor-task": AgentTask(
                    id="successor-task",
                    owner_agent_id="lead",
                    project_id="project",
                    priority=30,
                    state=TeamTaskState.PENDING,
                    title="Replacement work",
                    description="Authorized successor for cancelled work.",
                    created_by_actor="operator:admin",
                    created_at=NOW,
                    updated_at=NOW,
                ),
                "cancelled-task": AgentTask(
                    id="cancelled-task",
                    owner_agent_id="lead",
                    project_id="project",
                    priority=40,
                    state=TeamTaskState.CANCELLED,
                    title="Obsolete work",
                    description="Superseded by a replacement task.",
                    created_by_actor="operator:admin",
                    originating_task_id="aggregate-task",
                    cancellation=TaskCancellation(
                        reason_code="superseded",
                        reason="The replacement owns this scope.",
                        actor_kind="operator",
                        actor_id="admin",
                        successor_task_id="successor-task",
                        cancelled_at=NOW,
                    ),
                    created_at=NOW,
                    updated_at=NOW,
                ),
                "correction-task": AgentTask(
                    id="correction-task",
                    owner_agent_id="lead",
                    project_id="project",
                    priority=50,
                    state=TeamTaskState.FINISHED,
                    title="Correct the human finding",
                    description="Apply and evidence the requested correction.",
                    created_by_actor="system:human-review",
                    originating_task_id="aggregate-task",
                    deliverable_ids=["correction-deliverable"],
                    created_at=NOW,
                    updated_at=NOW,
                ),
            }
        )
        state.deliverables.update(
            {
                "source-deliverable": Deliverable(
                    id="source-deliverable",
                    task_id="source-task",
                    producer_agent_id="lead",
                    summary="Component output",
                    state=TeamRecordState.ACCEPTED,
                    completion=TaskCompletion(
                        kind=TaskCompletionKind.EVIDENCED_NOOP,
                        summary="Component output accepted.",
                        evidence_refs=["evidence:source"],
                    ),
                    created_at=NOW,
                ),
                "aggregate-deliverable": Deliverable(
                    id="aggregate-deliverable",
                    task_id="aggregate-task",
                    producer_agent_id="lead",
                    summary="Integrated output",
                    state=TeamRecordState.ACCEPTED,
                    review_request_ids=["agent-review"],
                    source_deliverable_ids=["source-deliverable"],
                    completion=TaskCompletion(
                        kind=TaskCompletionKind.EVIDENCED_NOOP,
                        summary="Aggregate output accepted.",
                        evidence_refs=["evidence:aggregate"],
                    ),
                    created_at=NOW,
                ),
                "correction-deliverable": Deliverable(
                    id="correction-deliverable",
                    task_id="correction-task",
                    producer_agent_id="lead",
                    summary="Human-requested correction",
                    state=TeamRecordState.ACCEPTED,
                    completion=TaskCompletion(
                        kind=TaskCompletionKind.EVIDENCED_NOOP,
                        summary="Correction accepted.",
                        evidence_refs=["evidence:correction"],
                    ),
                    created_at=NOW,
                ),
            }
        )
        state.review_requests["agent-review"] = ReviewRequest(
            id="agent-review",
            deliverable_id="aggregate-deliverable",
            reviewer_agent_id="lead",
            requested_by_actor="operator:admin",
            state=TeamRecordState.ACCEPTED,
            findings=["Reviewed at the pinned commit."],
            reviewed_commit_sha="candidate-one",
            created_at=NOW,
            decision_at=NOW,
        )
        state.inbox["inbox-review"] = AgentInboxItem(
            id="inbox-review",
            recipient_agent_id="lead",
            item_type=TeamInboxItemType.DELIVERABLE_NOTICE,
            task_id="aggregate-task",
            payload={"deliverable_id": "aggregate-deliverable"},
            state=TeamInboxItemState.HANDLED,
            created_at=NOW,
            updated_at=NOW,
        )
        state.blockers["blocker-shared"] = TeamBlocker(
            id="blocker-shared",
            team_id="team",
            project_id="project",
            task_id="cancelled-task",
            source_kind=TeamBlockerSourceKind.TASK,
            source_id="cancelled-task",
            reason_code="dependency_replaced",
            reporter_agent_id="lead",
            reporter_actor="operator:admin",
            affected_task_ids=["successor-task", "correction-task"],
            owner_agent_id="lead",
            state=TeamBlockerState.RESOLVED,
            route=TeamBlockerRoute.RESOLVED,
            summary="The obsolete dependency was replaced.",
            details="Both affected tasks can now proceed.",
            terminal_reference_ids=["successor-task"],
            created_at=NOW,
            updated_at=NOW,
            resolved_at=NOW,
        )

        requirement = HumanReviewRequirementSnapshot(
            directory_path="root",
            configured_group_ids=["release-reviewers"],
            initially_eligible_user_ids=["alice@example.com"],
            checklist=[
                HumanReviewChecklistItem(
                    id="smoke",
                    label="Run the release smoke test",
                )
            ],
            test_instructions="Exercise the production-line workflow.",
            preparation_mode="workflow",
        )
        predecessor_manifest = ReleaseCandidateManifest(
            project_id="project",
            release_package_id="release-one",
            base_commit_sha="base-one",
            candidate_commit_sha="candidate-one",
            candidate_tree_hash="tree-one",
            release_branch="release/project/one",
            deliverables=[
                ReleaseCandidateDeliverableEvidence(deliverable_id="aggregate-deliverable")
            ],
            human_review_requirement=requirement,
            contribution_graph={"aggregate-deliverable": ["source-deliverable"]},
            created_at=NOW,
        )
        successor_manifest = ReleaseCandidateManifest(
            project_id="project",
            release_package_id="release-two",
            base_commit_sha="candidate-one",
            candidate_commit_sha="candidate-two",
            candidate_tree_hash="tree-two",
            release_branch="release/project/two",
            deliverables=[
                ReleaseCandidateDeliverableEvidence(deliverable_id="aggregate-deliverable"),
                ReleaseCandidateDeliverableEvidence(deliverable_id="correction-deliverable"),
            ],
            human_review_requirement=requirement,
            predecessor_release_package_id="release-one",
            correction_deliverable_ids=["correction-deliverable"],
            carried_human_findings=[
                HumanReviewFindingSnapshot(
                    finding_id="human-finding",
                    text="The station summary needs a correction.",
                    target_deliverable_ids=["aggregate-deliverable"],
                    correction_task_id="correction-task",
                    proposed_resolution="Corrected by the successor deliverable.",
                    resolution_evidence_refs=["evidence:correction"],
                    proposed_by_deliverable_ids=["correction-deliverable"],
                )
            ],
            contribution_graph={
                "aggregate-deliverable": ["source-deliverable"],
                "correction-deliverable": ["aggregate-deliverable"],
            },
            created_at=NOW,
        )
        state.release_packages.update(
            {
                "release-one": ReleasePackage(
                    id="release-one",
                    project_id="project",
                    owner_agent_id="lead",
                    deliverable_ids=["aggregate-deliverable"],
                    release_branch="release/project/one",
                    base_commit_sha="base-one",
                    candidate_commit_sha="candidate-one",
                    candidate_tree_hash="tree-one",
                    manifest=predecessor_manifest,
                    state=TeamRecordState.REJECTED,
                    policy_version=2,
                    error="Human changes requested.",
                    created_at=NOW,
                    updated_at=NOW,
                ),
                "release-two": ReleasePackage(
                    id="release-two",
                    project_id="project",
                    supersedes_release_package_id="release-one",
                    deliverable_ids=[
                        "aggregate-deliverable",
                        "correction-deliverable",
                    ],
                    release_branch="release/project/two",
                    base_commit_sha="candidate-one",
                    candidate_commit_sha="candidate-two",
                    candidate_tree_hash="tree-two",
                    approved_commit_sha="candidate-two",
                    manifest=successor_manifest,
                    state=TeamRecordState.ACCEPTED,
                    policy_version=2,
                    created_at=NOW,
                    updated_at=NOW,
                ),
            }
        )
        state.human_review_requests.update(
            {
                "human-request-one": HumanReviewRequest(
                    id="human-request-one",
                    release_package_id="release-one",
                    candidate_commit_sha="candidate-one",
                    candidate_tree_hash="tree-one",
                    requirement_snapshot=requirement,
                    current_assignment=HumanReviewAssignment(
                        eligible_user_ids=[
                            "alice@example.com",
                            "bob@example.com",
                        ],
                        explicitly_assigned_user_ids=["bob@example.com"],
                        assigned_by_user_id="admin@example.com",
                        assigned_at=NOW,
                    ),
                    assignment_generation=2,
                    state="changes_requested",
                    preparation_generation=3,
                    test_target=HumanReviewTestTarget(
                        alias="app-preview-one",
                        expires_at=NOW + timedelta(hours=1),
                        instructions="Use the seeded production order.",
                        evidence_refs=["preview:one"],
                    ),
                    revision=4,
                    created_at=NOW,
                    ready_at=NOW,
                    completed_at=NOW,
                ),
                "human-request-two": HumanReviewRequest(
                    id="human-request-two",
                    release_package_id="release-two",
                    candidate_commit_sha="candidate-two",
                    candidate_tree_hash="tree-two",
                    requirement_snapshot=requirement,
                    current_assignment=HumanReviewAssignment(
                        eligible_user_ids=["alice@example.com"]
                    ),
                    state="approved",
                    test_target=HumanReviewTestTarget(
                        url="https://preview.example.test/release-two",
                        expires_at=NOW + timedelta(hours=2),
                        evidence_refs=["preview:two"],
                    ),
                    revision=1,
                    created_at=NOW,
                    ready_at=NOW,
                    completed_at=NOW,
                ),
            }
        )
        state.human_review_findings["human-finding"] = HumanReviewFinding(
            id="human-finding",
            source_human_review_request_id="human-request-one",
            text="The station summary needs a correction.",
            target_deliverable_ids=["aggregate-deliverable"],
            evidence_refs=["screenshot:station-summary"],
            correction_task_id="correction-task",
            state="resolved",
            proposed_resolution="Corrected by the successor deliverable.",
            resolution_evidence_refs=["evidence:correction"],
            proposed_by_deliverable_ids=["correction-deliverable"],
            resolved_by_human_review_request_id="human-request-two",
            created_at=NOW,
            resolved_at=NOW,
        )
        state.human_review_decisions.update(
            {
                "human-decision-one": HumanReviewDecision(
                    id="human-decision-one",
                    human_review_request_id="human-request-one",
                    reviewer_user_id="bob@example.com",
                    reviewer_display_name_snapshot="Bob Reviewer",
                    decision="changes_requested",
                    checklist_results={"smoke": {"state": "failed"}},
                    summary="Correct the station summary.",
                    evidence_refs=["screenshot:station-summary"],
                    request_id="decision-one",
                    decided_at=NOW,
                ),
                "human-decision-two": HumanReviewDecision(
                    id="human-decision-two",
                    human_review_request_id="human-request-two",
                    reviewer_user_id="alice@example.com",
                    reviewer_display_name_snapshot="Alice Reviewer",
                    decision="approved",
                    checklist_results={"smoke": {"state": "passed"}},
                    confirmed_finding_ids=["human-finding"],
                    summary="The successor resolves the finding.",
                    evidence_refs=["evidence:approval"],
                    request_id="decision-two",
                    decided_at=NOW,
                ),
            }
        )
        state.dispatches["dispatch-complete"] = TeamDispatch(
            id="dispatch-complete",
            agent_id="lead",
            session_id="session-lead",
            cause_kind="task",
            cause_id="aggregate-task",
            task_ids=["aggregate-task", "correction-task"],
            blocker_ids=["blocker-shared"],
            review_request_ids=["agent-review"],
            inbox_item_ids=["inbox-review"],
            project_ids=["project"],
            managed_repo_ids=[MANAGED_REPO_ID],
            status=TeamDispatchStatus.COMPLETED,
            created_at=NOW,
            prepared_at=NOW,
            leased_at=NOW,
            lease_expires_at=NOW + timedelta(minutes=15),
            completed_at=NOW + timedelta(minutes=5),
        )
        state.worktree_cleanup_candidates["cleanup-one"] = WorktreeCleanupCandidate(
            id="cleanup-one",
            agent_id="lead",
            session_id="session-lead",
            task_id="cancelled-task",
            project_id="project",
            managed_repo_id=MANAGED_REPO_ID,
            execution_backend="container_worker_vm",
            worktree_path="/worktrees/cancelled-task",
            branch="agents/session-lead/cancelled-task",
            base_ref="main",
            expected_head="candidate-one",
            retained_commit_sha="candidate-one",
            state=WorktreeCleanupState.RECOVERY_REQUIRED,
            reason="Cancelled task worktree is eligible for cleanup.",
            eligible_at=NOW,
            attempt_count=2,
            last_safety_check="unexpected_untracked_files",
            last_error="Untracked source file requires operator recovery.",
            ignored_file_inventory=["node_modules/.cache"],
            ignored_bytes=256,
            worktree_bytes=1024,
            created_at=NOW,
            updated_at=NOW,
        )
        return state

    repository.update(seed_unified_state)
    reloaded = TeamStateRepository(tmp_path).read()

    cancelled = reloaded.tasks["cancelled-task"]
    assert cancelled.originating_task_id == "aggregate-task"
    assert cancelled.cancellation is not None
    assert cancelled.cancellation.successor_task_id == "successor-task"
    assert reloaded.deliverables["aggregate-deliverable"].source_deliverable_ids == [
        "source-deliverable"
    ]
    assert (
        reloaded.deliverables["aggregate-deliverable"].completion.kind
        == TaskCompletionKind.EVIDENCED_NOOP
    )
    blocker = reloaded.blockers["blocker-shared"]
    assert blocker.reason_code == "dependency_replaced"
    assert blocker.reporter_actor == "operator:admin"
    assert blocker.affected_task_ids == ["successor-task", "correction-task"]
    dispatch = reloaded.dispatches["dispatch-complete"]
    assert dispatch.status == TeamDispatchStatus.COMPLETED
    assert dispatch.task_ids == ["aggregate-task", "correction-task"]
    assert dispatch.blocker_ids == ["blocker-shared"]
    assert dispatch.review_request_ids == ["agent-review"]
    assert dispatch.inbox_item_ids == ["inbox-review"]
    assert dispatch.project_ids == ["project"]
    assert dispatch.managed_repo_ids == [MANAGED_REPO_ID]
    successor = reloaded.release_packages["release-two"]
    assert reloaded.release_packages["release-one"].owner_agent_id == "lead"
    assert successor.supersedes_release_package_id == "release-one"
    assert successor.manifest is not None
    assert successor.manifest.predecessor_release_package_id == "release-one"
    assert successor.manifest.correction_deliverable_ids == ["correction-deliverable"]
    assert successor.manifest.carried_human_findings[0].finding_id == ("human-finding")
    request = reloaded.human_review_requests["human-request-one"]
    assert request.requirement_snapshot.configured_group_ids == ["release-reviewers"]
    assert request.requirement_snapshot.initially_eligible_user_ids == ["alice@example.com"]
    assert request.current_assignment.eligible_user_ids == [
        "alice@example.com",
        "bob@example.com",
    ]
    assert request.current_assignment.explicitly_assigned_user_ids == ["bob@example.com"]
    assert reloaded.human_review_decisions["human-decision-two"].confirmed_finding_ids == [
        "human-finding"
    ]
    finding = reloaded.human_review_findings["human-finding"]
    assert finding.target_deliverable_ids == ["aggregate-deliverable"]
    assert finding.proposed_by_deliverable_ids == ["correction-deliverable"]
    assert finding.resolved_by_human_review_request_id == "human-request-two"
    cleanup = reloaded.worktree_cleanup_candidates["cleanup-one"]
    assert cleanup.worktree_path == "/worktrees/cancelled-task"
    assert cleanup.last_safety_check == "unexpected_untracked_files"
    assert cleanup.attempt_count == 2

    db_path = tmp_path / "addons" / "team-addon" / "state.db"
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        normalized_rows = {
            "groups": connection.execute(
                """
                SELECT group_id
                FROM team_human_review_request_configured_groups
                WHERE human_review_request_id = ?
                ORDER BY position
                """,
                ("human-request-one",),
            ).fetchall(),
            "initial_users": connection.execute(
                """
                SELECT user_id
                FROM team_human_review_request_initial_users
                WHERE human_review_request_id = ?
                ORDER BY position
                """,
                ("human-request-one",),
            ).fetchall(),
            "current_users": connection.execute(
                """
                SELECT user_id
                FROM team_human_review_request_current_users
                WHERE human_review_request_id = ?
                ORDER BY position
                """,
                ("human-request-one",),
            ).fetchall(),
            "explicit_users": connection.execute(
                """
                SELECT user_id
                FROM team_human_review_request_explicit_users
                WHERE human_review_request_id = ?
                ORDER BY position
                """,
                ("human-request-one",),
            ).fetchall(),
            "confirmed_findings": connection.execute(
                """
                SELECT finding_id
                FROM team_human_review_decision_confirmed_findings
                WHERE human_review_decision_id = ?
                ORDER BY position
                """,
                ("human-decision-two",),
            ).fetchall(),
        }
        request_payload = json.loads(
            connection.execute(
                """
                SELECT payload_json
                FROM team_human_review_requests
                WHERE id = 'human-request-one'
                """
            ).fetchone()["payload_json"]
        )
        request_payload["requirement_snapshot"]["configured_group_ids"] = ["payload-only-group"]
        request_payload["requirement_snapshot"]["initially_eligible_user_ids"] = [
            "payload-only@example.com"
        ]
        request_payload["current_assignment"]["eligible_user_ids"] = ["payload-only@example.com"]
        request_payload["current_assignment"]["explicitly_assigned_user_ids"] = [
            "payload-only@example.com"
        ]
        connection.execute(
            """
            UPDATE team_human_review_requests
            SET payload_json = ?
            WHERE id = 'human-request-one'
            """,
            (json.dumps(request_payload),),
        )
        decision_payload = json.loads(
            connection.execute(
                """
                SELECT payload_json
                FROM team_human_review_decisions
                WHERE id = 'human-decision-two'
                """
            ).fetchone()["payload_json"]
        )
        decision_payload["confirmed_finding_ids"] = ["payload-only-finding"]
        connection.execute(
            """
            UPDATE team_human_review_decisions
            SET payload_json = ?
            WHERE id = 'human-decision-two'
            """,
            (json.dumps(decision_payload),),
        )
        finding_payload = json.loads(
            connection.execute(
                """
                SELECT payload_json
                FROM team_human_review_findings
                WHERE id = 'human-finding'
                """
            ).fetchone()["payload_json"]
        )
        finding_payload["target_deliverable_ids"] = ["payload-only-deliverable"]
        finding_payload["proposed_by_deliverable_ids"] = ["payload-only-proposal"]
        finding_payload["resolved_by_human_review_request_id"] = "payload-only-request"
        connection.execute(
            """
            UPDATE team_human_review_findings
            SET payload_json = ?
            WHERE id = 'human-finding'
            """,
            (json.dumps(finding_payload),),
        )

    assert [row["group_id"] for row in normalized_rows["groups"]] == ["release-reviewers"]
    assert [row["user_id"] for row in normalized_rows["initial_users"]] == ["alice@example.com"]
    assert [row["user_id"] for row in normalized_rows["current_users"]] == [
        "alice@example.com",
        "bob@example.com",
    ]
    assert [row["user_id"] for row in normalized_rows["explicit_users"]] == ["bob@example.com"]
    assert [row["finding_id"] for row in normalized_rows["confirmed_findings"]] == ["human-finding"]

    normalized_reload = TeamStateRepository(tmp_path).read()
    normalized_request = normalized_reload.human_review_requests["human-request-one"]
    assert normalized_request.requirement_snapshot.configured_group_ids == ["release-reviewers"]
    assert normalized_request.requirement_snapshot.initially_eligible_user_ids == [
        "alice@example.com"
    ]
    assert normalized_request.current_assignment.eligible_user_ids == [
        "alice@example.com",
        "bob@example.com",
    ]
    assert normalized_request.current_assignment.explicitly_assigned_user_ids == ["bob@example.com"]
    assert normalized_reload.human_review_decisions["human-decision-two"].confirmed_finding_ids == [
        "human-finding"
    ]
    normalized_finding = normalized_reload.human_review_findings["human-finding"]
    assert normalized_finding.target_deliverable_ids == ["aggregate-deliverable"]
    assert normalized_finding.proposed_by_deliverable_ids == ["correction-deliverable"]
    assert normalized_finding.resolved_by_human_review_request_id == ("human-request-two")

    invalid_decision_state = normalized_reload.model_dump(mode="python")
    invalid_decision_state["human_review_decisions"]["human-decision-two"][
        "confirmed_finding_ids"
    ] = ["missing-finding"]
    with pytest.raises(ValidationError, match="confirms missing finding"):
        type(normalized_reload).model_validate(invalid_decision_state)

    invalid_resolution_state = normalized_reload.model_dump(mode="python")
    invalid_resolution_state["human_review_findings"]["human-finding"][
        "resolved_by_human_review_request_id"
    ] = "human-request-one"
    with pytest.raises(ValidationError, match="cannot be resolved by its source"):
        type(normalized_reload).model_validate(invalid_resolution_state)


def test_sqlite_migration_adds_unified_columns_and_backfills_relationships(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "addons" / "team-addon" / "state.db"
    db_path.parent.mkdir(parents=True)
    with sqlite3.connect(db_path) as connection:
        connection.executescript(
            """
            CREATE TABLE team_state_metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            INSERT INTO team_state_metadata(key, value)
            VALUES ('sqlite_schema_version', '32');
            INSERT INTO team_state_metadata(key, value)
            VALUES ('revision', '1');

            CREATE TABLE team_tasks (
                id TEXT PRIMARY KEY,
                owner_agent_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                case_id TEXT,
                priority INTEGER NOT NULL,
                state TEXT NOT NULL,
                state_entered_at TEXT NOT NULL,
                title TEXT NOT NULL,
                description TEXT NOT NULL,
                created_by_agent_id TEXT,
                created_by_actor TEXT,
                parent_task_id TEXT,
                task_kind TEXT NOT NULL DEFAULT 'support',
                outcome_contract_json TEXT,
                completion_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE team_deliverables (
                id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                producer_agent_id TEXT NOT NULL,
                summary TEXT NOT NULL,
                state TEXT NOT NULL,
                branch_context_id TEXT,
                agent_branch TEXT,
                review_branch TEXT,
                feature_branch TEXT,
                release_branch TEXT,
                commit_sha TEXT,
                submitted_commit_sha TEXT,
                reviewed_commit_sha TEXT,
                feature_commit_sha TEXT,
                release_commit_sha TEXT,
                verification_json TEXT,
                outcomes_json TEXT NOT NULL DEFAULT '[]',
                carried_findings_json TEXT NOT NULL DEFAULT '[]',
                review_manifest_json TEXT,
                acceptance_recipient_agent_id TEXT,
                acceptance_decision_json TEXT,
                supersedes_deliverable_id TEXT,
                superseded_by_deliverable_id TEXT,
                supersession_reason TEXT,
                superseded_at TEXT,
                superseded_by_actor TEXT,
                created_at TEXT NOT NULL
            );
            CREATE TABLE team_blockers (
                id TEXT PRIMARY KEY,
                team_id TEXT NOT NULL,
                project_id TEXT,
                task_id TEXT,
                source_kind TEXT NOT NULL,
                source_id TEXT NOT NULL,
                reporter_agent_id TEXT,
                owner_agent_id TEXT,
                delegate_agent_id TEXT,
                state TEXT NOT NULL,
                route TEXT NOT NULL,
                summary TEXT NOT NULL,
                details TEXT NOT NULL,
                priority TEXT NOT NULL,
                escalation_depth INTEGER NOT NULL DEFAULT 0,
                active_dispatch_id TEXT,
                follow_up_at TEXT,
                last_progress_at TEXT,
                terminal_reference_ids_json TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                resolved_at TEXT
            );
            CREATE TABLE team_release_packages (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                release_branch TEXT,
                canonical_branch TEXT,
                published_commit_sha TEXT,
                base_commit_sha TEXT,
                candidate_commit_sha TEXT,
                candidate_tree_hash TEXT,
                approved_commit_sha TEXT,
                verification_run_ids_json TEXT NOT NULL DEFAULT '[]',
                review_request_ids_json TEXT NOT NULL DEFAULT '[]',
                manifest_json TEXT NOT NULL DEFAULT '{}',
                gate_failure_json TEXT,
                state TEXT NOT NULL,
                published_at TEXT,
                error TEXT,
                policy_version INTEGER NOT NULL DEFAULT 1,
                verification_waivers_json TEXT NOT NULL DEFAULT '[]',
                scope_validation_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT
            );
            CREATE TABLE team_human_review_requests (
                id TEXT PRIMARY KEY,
                release_package_id TEXT NOT NULL,
                state TEXT NOT NULL,
                candidate_commit_sha TEXT NOT NULL,
                candidate_tree_hash TEXT NOT NULL,
                assignment_generation INTEGER NOT NULL,
                preparation_generation INTEGER NOT NULL,
                payload_json TEXT NOT NULL
            );
            CREATE TABLE team_human_review_decisions (
                id TEXT PRIMARY KEY,
                human_review_request_id TEXT NOT NULL,
                reviewer_user_id TEXT NOT NULL,
                decision TEXT NOT NULL,
                request_id TEXT NOT NULL,
                decided_at TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                UNIQUE(human_review_request_id, request_id)
            );
            CREATE TABLE team_human_review_findings (
                id TEXT PRIMARY KEY,
                source_human_review_request_id TEXT NOT NULL,
                correction_task_id TEXT NOT NULL,
                state TEXT NOT NULL,
                payload_json TEXT NOT NULL
            );
            """
        )
        request_payload = {
            "requirement_snapshot": {
                "configured_group_ids": ["release-reviewers"],
                "initially_eligible_user_ids": ["alice@example.com"],
            },
            "current_assignment": {
                "eligible_user_ids": [
                    "alice@example.com",
                    "bob@example.com",
                ],
                "explicitly_assigned_user_ids": ["bob@example.com"],
            },
        }
        connection.execute(
            """
            INSERT INTO team_human_review_requests(
                id, release_package_id, state, candidate_commit_sha,
                candidate_tree_hash, assignment_generation,
                preparation_generation, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "request-one",
                "release-one",
                "approved",
                "candidate-one",
                "tree-one",
                2,
                1,
                json.dumps(request_payload),
            ),
        )
        connection.execute(
            """
            INSERT INTO team_human_review_decisions(
                id, human_review_request_id, reviewer_user_id,
                decision, request_id, decided_at, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "decision-one",
                "request-one",
                "alice@example.com",
                "approved",
                "decision-request-one",
                NOW.isoformat(),
                json.dumps({"confirmed_finding_ids": ["finding-one"]}),
            ),
        )
        connection.execute(
            """
            INSERT INTO team_human_review_findings(
                id, source_human_review_request_id, correction_task_id,
                state, payload_json
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                "finding-one",
                "request-zero",
                "correction-task",
                "resolved",
                json.dumps(
                    {
                        "target_deliverable_ids": ["deliverable-one"],
                        "proposed_by_deliverable_ids": ["deliverable-two"],
                        "resolved_by_human_review_request_id": "request-one",
                    }
                ),
            ),
        )

    TeamStateRepository(tmp_path)

    with sqlite3.connect(db_path) as connection:
        task_columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(team_tasks)")}
        deliverable_columns = {
            str(row[1]) for row in connection.execute("PRAGMA table_info(team_deliverables)")
        }
        blocker_columns = {
            str(row[1]) for row in connection.execute("PRAGMA table_info(team_blockers)")
        }
        release_columns = {
            str(row[1]) for row in connection.execute("PRAGMA table_info(team_release_packages)")
        }
        finding_columns = {
            str(row[1])
            for row in connection.execute("PRAGMA table_info(team_human_review_findings)")
        }
        tables = {
            str(row[0])
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        metadata = dict(connection.execute("SELECT key, value FROM team_state_metadata").fetchall())
        configured_groups = connection.execute(
            """
            SELECT group_id
            FROM team_human_review_request_configured_groups
            WHERE human_review_request_id = 'request-one'
            ORDER BY position
            """
        ).fetchall()
        initial_users = connection.execute(
            """
            SELECT user_id
            FROM team_human_review_request_initial_users
            WHERE human_review_request_id = 'request-one'
            ORDER BY position
            """
        ).fetchall()
        current_users = connection.execute(
            """
            SELECT user_id
            FROM team_human_review_request_current_users
            WHERE human_review_request_id = 'request-one'
            ORDER BY position
            """
        ).fetchall()
        explicit_users = connection.execute(
            """
            SELECT user_id
            FROM team_human_review_request_explicit_users
            WHERE human_review_request_id = 'request-one'
            ORDER BY position
            """
        ).fetchall()
        confirmed_findings = connection.execute(
            """
            SELECT finding_id
            FROM team_human_review_decision_confirmed_findings
            WHERE human_review_decision_id = 'decision-one'
            ORDER BY position
            """
        ).fetchall()
        finding_targets = connection.execute(
            """
            SELECT deliverable_id
            FROM team_human_review_finding_targets
            WHERE finding_id = 'finding-one'
            ORDER BY position
            """
        ).fetchall()
        finding_proposals = connection.execute(
            """
            SELECT deliverable_id
            FROM team_human_review_finding_proposals
            WHERE finding_id = 'finding-one'
            ORDER BY position
            """
        ).fetchall()
        resolved_by = connection.execute(
            """
            SELECT resolved_by_human_review_request_id
            FROM team_human_review_findings
            WHERE id = 'finding-one'
            """
        ).fetchone()[0]

    assert {
        "originating_task_id",
        "cancellation_json",
        "integration_boundary",
        "deliverable_contract_json",
    } <= task_columns
    assert {
        "completion_json",
        "review_cohort_id",
        "review_cohort_sealed_at",
    } <= deliverable_columns
    assert {"reason_code", "reporter_actor"} <= blocker_columns
    assert {"owner_agent_id", "supersedes_release_package_id"} <= release_columns
    assert "resolved_by_human_review_request_id" in finding_columns
    assert {
        "team_deliverable_sources",
        "team_blocker_affected_tasks",
        "team_dispatches",
        "team_dispatch_tasks",
        "team_dispatch_blockers",
        "team_dispatch_reviews",
        "team_dispatch_inbox",
        "team_dispatch_projects",
        "team_dispatch_repositories",
        "team_worktree_cleanup_candidates",
        "team_human_review_request_configured_groups",
        "team_human_review_request_initial_users",
        "team_human_review_request_current_users",
        "team_human_review_request_explicit_users",
        "team_human_review_decision_confirmed_findings",
        "team_human_review_finding_targets",
        "team_human_review_finding_proposals",
    } <= tables
    assert metadata["sqlite_schema_version"] == str(SQLITE_SCHEMA_VERSION)
    assert configured_groups == [("release-reviewers",)]
    assert initial_users == [("alice@example.com",)]
    assert current_users == [
        ("alice@example.com",),
        ("bob@example.com",),
    ]
    assert explicit_users == [("bob@example.com",)]
    assert confirmed_findings == [("finding-one",)]
    assert finding_targets == [("deliverable-one",)]
    assert finding_proposals == [("deliverable-two",)]
    assert resolved_by == "request-one"


def test_sqlite_migration_bounds_legacy_worktree_cleanup_inventory(
    tmp_path: Path,
) -> None:
    repository = _seed_team_repository(tmp_path)
    TeamAdminService(repository).create_or_reprioritize_task(
        task_id="task",
        owner_agent_id="lead",
        project_id="project",
        priority=10,
        title="Completed task",
        description="Task retained for cleanup migration evidence.",
        created_by_agent_id="lead",
    )

    def _seed_candidate(state):
        state.worktree_cleanup_candidates["cleanup"] = WorktreeCleanupCandidate(
            id="cleanup",
            agent_id="lead",
            session_id="session-lead",
            task_id="task",
            project_id="project",
            managed_repo_id=MANAGED_REPO_ID,
            execution_backend="libvirt_vm",
            worktree_path="/worktrees/task",
            branch="agents/task",
            reason="task_resolved",
        )
        return state

    repository.update(_seed_candidate)
    inventory = [f"cache/{index:05d}.bin" for index in range(65_000, -1, -1)]
    inventory.append("cache/00042.bin")
    with sqlite3.connect(repository.store.path) as connection:
        payload = json.loads(
            connection.execute(
                "SELECT payload_json FROM team_worktree_cleanup_candidates WHERE id = 'cleanup'"
            ).fetchone()[0]
        )
        payload["ignored_file_inventory"] = inventory
        payload.pop("ignored_file_count", None)
        payload.pop("ignored_file_inventory_sha256", None)
        payload.pop("ignored_file_inventory_truncated", None)
        connection.execute(
            "UPDATE team_worktree_cleanup_candidates SET payload_json = ? WHERE id = 'cleanup'",
            (json.dumps(payload),),
        )
        connection.execute(
            "UPDATE team_state_metadata SET value = '42' WHERE key = 'sqlite_schema_version'"
        )
        connection.execute(
            "UPDATE team_state_metadata SET value = '4' WHERE key = 'state_schema_version'"
        )

    expected_sample, expected_count, expected_digest, expected_truncated = (
        summarize_worktree_cleanup_inventory(inventory)
    )
    migrated = TeamStateRepository(tmp_path).read().worktree_cleanup_candidates["cleanup"]

    assert migrated.ignored_file_inventory == expected_sample
    assert migrated.ignored_file_count == expected_count == 65_001
    assert migrated.ignored_file_inventory_sha256 == expected_digest
    assert migrated.ignored_file_inventory_truncated is expected_truncated is True
    assert len(migrated.ignored_file_inventory) == 200
    assert TeamStateRepository(tmp_path).read().worktree_cleanup_candidates["cleanup"] == migrated
