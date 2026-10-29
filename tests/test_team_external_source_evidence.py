from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.testclient import TestClient

from evander.addons import AddonContext
from evander_team_addon import api as team_addon_api
from evander_team_addon.models import (
    AgentInboxItem,
    Deliverable,
    ExternalSourceEvidenceFreshnessStatus,
    ProjectGoal,
    ReviewRequest,
    TeamCaseStatus,
    TeamInboxItemType,
    TeamRecordState,
    TeamTaskState,
)
from evander_team_addon.providers import _build_prompt, _filtered_team_state_payload
from evander_team_addon.repository import TeamStateRepository
from evander_team_addon.services.admin_service import TeamAdminService
from evander_team_addon.services.commands import (
    TeamDeliverableSubmissionCommandService,
    TeamReviewCommandService,
    TeamTaskUpdateCommandService,
)
from evander_team_addon.services.domain.preparation_failures import (
    task_preparation_generation,
)
from evander_team_addon.services.read_service import TeamReadService
from evander_team_addon.services.runtime_service import TeamRuntimeService
from evander_team_addon.services.wake_prompt_context import build_compact_wake_context


NOW = datetime(2026, 8, 24, 18, 0, tzinfo=UTC)


def _seed_repository(
    tmp_path: Path,
    *,
    directory_path: str = "root",
) -> TeamStateRepository:
    repository = TeamStateRepository(tmp_path)
    admin = TeamAdminService(repository)
    admin.create_team(
        team_id="team",
        name="Team",
        manager_agent_id="manager",
        manager_session_id="session-manager",
    )
    admin.create_project(
        project_id="project",
        team_id="team",
        name="Project",
        lead_agent_id="lead",
        lead_session_id="session-lead",
        directory_path=directory_path,
        goals=[ProjectGoal(id="goal", description="Complete the sourced work")],
    )
    admin.create_role(
        role_id="project__worker",
        team_id="team",
        project_id="project",
        name="Worker",
    )
    admin.register_agent(
        agent_id="worker",
        team_id="team",
        project_id="project",
        role_id="project__worker",
        session_id="session-worker",
        reports_to_agent_id="lead",
    )
    admin.create_or_reprioritize_task(
        task_id="task",
        owner_agent_id="worker",
        project_id="project",
        priority=1,
        title="Implement the sourced requirement",
        description="Use the attached normalized source snapshot.",
        created_by_agent_id="lead",
    )
    admin.create_case(
        case_id="case",
        team_id="team",
        project_id="project",
        title="External requirement",
        owner_agent_id="worker",
        task_ids=["task"],
    )
    return repository


def _create_snapshot(
    repository: TeamStateRepository,
    *,
    snapshot_id: str | None = "snapshot-1",
    attach: bool = True,
    external_identifier: str = "EXT-42",
    external_version: str = "7",
    fetched_at: datetime = NOW,
    body_content: str = "Use the source record as captured.",
):
    return TeamAdminService(repository).create_external_source_evidence_snapshot(
        snapshot_id=snapshot_id,
        team_id="team",
        project_id="project",
        provider_type="work-management",
        source_type="work-item",
        external_identifier=external_identifier,
        external_version=external_version,
        fetched_at=fetched_at,
        expires_at=fetched_at + timedelta(hours=1),
        freshness_status=ExternalSourceEvidenceFreshnessStatus.CURRENT,
        access_classification="internal",
        title="Reconcile the source record",
        body={"format": "markdown", "content": body_content},
        acceptance_criteria=[
            {
                "external_id": "criterion-1",
                "text": "The normalized balance matches the captured source.",
                "status": "active",
            }
        ],
        discussion=[
            {
                "external_id": "comment-1",
                "author": "reviewer@example.com",
                "body": "Preserve an audit link to the source version.",
                "occurred_at": NOW - timedelta(minutes=5),
            }
        ],
        links=[
            {
                "url": "https://source.example/items/EXT-42",
                "label": "Source record",
                "relation": "canonical",
            }
        ],
        metadata={"adapter": "fixture", "category": "reconciliation"},
        case_ids=["case"] if attach else [],
        task_ids=["task"] if attach else [],
        created_by_actor="operator:test@example.com",
    )


def _seed_pending_deliverable_handoff(
    repository: TeamStateRepository,
    *,
    handoff_kind: str,
) -> None:
    def _mutate(state):
        now = datetime.now(UTC)
        task = state.tasks["task"]
        task.state = TeamTaskState.WAITING_FOR_REVIEW
        task.state_entered_at = now
        task.updated_at = now
        review_ids = ["review"] if handoff_kind == "review" else []
        state.deliverables["deliverable"] = Deliverable(
            id="deliverable",
            task_id=task.id,
            producer_agent_id="worker",
            summary="Evidence-bearing handoff",
            review_request_ids=review_ids,
            acceptance_recipient_agent_id=(
                "lead" if handoff_kind == "acceptance" else None
            ),
            created_at=now,
        )
        task.deliverable_ids = ["deliverable"]
        if handoff_kind == "review":
            state.review_requests["review"] = ReviewRequest(
                id="review",
                deliverable_id="deliverable",
                reviewer_agent_id="lead",
                requested_by_agent_id="worker",
                created_at=now,
            )
        else:
            state.inbox["acceptance-notice"] = AgentInboxItem(
                id="acceptance-notice",
                recipient_agent_id="lead",
                sender_agent_id="worker",
                task_id=task.id,
                item_type=TeamInboxItemType.DELIVERABLE_NOTICE,
                payload={
                    "kind": "superior_acceptance_requested",
                    "deliverable_id": "deliverable",
                    "task_id": task.id,
                },
                created_at=now,
                updated_at=now,
            )
        return state

    repository.update(_mutate)


def test_external_source_evidence_round_trips_with_immutable_attachments(
    tmp_path: Path,
) -> None:
    repository = _seed_repository(tmp_path)
    state, snapshot = _create_snapshot(repository)

    assert snapshot.content_digest.startswith("sha256:")
    assert state.cases["case"].source_evidence_snapshot_ids == [snapshot.id]
    assert state.tasks["task"].source_evidence_snapshot_ids == [snapshot.id]

    restored = TeamStateRepository(tmp_path).read()
    restored_snapshot = restored.external_source_evidence_snapshots[snapshot.id]
    assert restored_snapshot == snapshot
    assert restored_snapshot.acceptance_criteria[0].external_id == "criterion-1"
    assert restored_snapshot.discussion[0].body.startswith("Preserve an audit link")

    with sqlite3.connect(repository.store.path) as connection:
        row = connection.execute(
            "SELECT provider_type, external_version, content_digest "
            "FROM team_external_source_evidence_snapshots WHERE id = ?",
            (snapshot.id,),
        ).fetchone()
        case_ids_json = connection.execute(
            "SELECT source_evidence_snapshot_ids_json FROM team_cases WHERE id = 'case'"
        ).fetchone()[0]
    assert row == ("work-management", "7", snapshot.content_digest)
    assert case_ids_json == '["snapshot-1"]'

    revision_before_replay = restored.revision
    state, replayed = _create_snapshot(repository)
    assert replayed.id == snapshot.id
    assert state.revision == revision_before_replay
    assert state.tasks["task"].source_evidence_snapshot_ids == [snapshot.id]
    assert state.cases["case"].source_evidence_snapshot_ids == [snapshot.id]

    with pytest.raises(ValueError, match="different normalized content"):
        _create_snapshot(
            repository,
            snapshot_id="snapshot-conflicting-replay",
            body_content="Conflicting content for the same external version.",
        )
    with pytest.raises(ValueError, match="different capture metadata"):
        _create_snapshot(
            repository,
            fetched_at=NOW + timedelta(minutes=1),
        )


def test_new_snapshot_attachments_refresh_active_task_preparation_and_work_due(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "evander_team_addon.services.admin_service.utc_now",
        lambda: NOW,
    )
    repository = _seed_repository(tmp_path)
    admin = TeamAdminService(repository)
    admin.create_or_reprioritize_task(
        task_id="case-peer",
        owner_agent_id="worker",
        project_id="project",
        case_id="case",
        priority=2,
        title="Apply the same case evidence",
        description="This active task is linked through the same case.",
        created_by_agent_id="lead",
    )
    _state, case_snapshot = _create_snapshot(
        repository,
        snapshot_id="snapshot-case",
        attach=False,
        external_version="8",
        body_content="Case-linked source version 8.",
    )

    old_update = NOW - timedelta(days=2)
    deferred_until = NOW + timedelta(days=2)

    def _defer_owner(state):
        state.tasks["task"].updated_at = old_update
        state.tasks["case-peer"].updated_at = old_update
        runtime = state.agent_runtimes["worker"]
        runtime.next_wake_at = deferred_until
        runtime.no_progress_count = 2
        runtime.updated_at = old_update
        return state

    repository.update(_defer_owner)
    generation_before_case_attachment = task_preparation_generation(
        repository.read(),
        "task",
    )
    peer_generation_before_case_attachment = task_preparation_generation(
        repository.read(),
        "case-peer",
    )

    admin.attach_external_source_evidence_snapshot(
        snapshot_id=case_snapshot.id,
        case_ids=["case"],
    )

    case_attached = repository.read()
    assert case_attached.tasks["task"].source_evidence_snapshot_ids == []
    assert case_attached.cases["case"].source_evidence_snapshot_ids == [
        case_snapshot.id
    ]
    assert case_attached.tasks["task"].updated_at == NOW
    assert case_attached.tasks["case-peer"].updated_at == NOW
    assert case_attached.agent_runtimes["worker"].next_wake_at is None
    assert case_attached.agent_runtimes["worker"].no_progress_count == 0
    generation_after_case_attachment = task_preparation_generation(
        case_attached,
        "task",
    )
    assert generation_after_case_attachment != generation_before_case_attachment
    assert (
        task_preparation_generation(case_attached, "case-peer")
        != peer_generation_before_case_attachment
    )

    _state, direct_snapshot = _create_snapshot(
        repository,
        snapshot_id="snapshot-direct",
        attach=False,
        external_version="9",
        fetched_at=NOW + timedelta(minutes=1),
        body_content="Direct task source version 9.",
    )
    repository.update(_defer_owner)
    generation_before_direct_attachment = task_preparation_generation(
        repository.read(),
        "task",
    )

    admin.attach_external_source_evidence_snapshot(
        snapshot_id=direct_snapshot.id,
        task_ids=["task"],
    )

    directly_attached = repository.read()
    assert directly_attached.tasks["task"].source_evidence_snapshot_ids == [
        direct_snapshot.id
    ]
    assert directly_attached.tasks["task"].updated_at == NOW
    assert directly_attached.agent_runtimes["worker"].next_wake_at is None
    generation_after_direct_attachment = task_preparation_generation(
        directly_attached,
        "task",
    )
    assert generation_after_direct_attachment != generation_before_direct_attachment

    repository.update(_defer_owner)
    generation_before_duplicate = task_preparation_generation(repository.read(), "task")
    admin.attach_external_source_evidence_snapshot(
        snapshot_id=direct_snapshot.id,
        task_ids=["task"],
    )
    duplicate = repository.read()
    assert duplicate.tasks["task"].updated_at == old_update
    assert duplicate.agent_runtimes["worker"].next_wake_at == deferred_until
    assert task_preparation_generation(duplicate, "task") == generation_before_duplicate


def test_generated_evidence_identity_replays_and_adds_only_missing_attachments(
    tmp_path: Path,
) -> None:
    repository = _seed_repository(tmp_path)
    initial, snapshot = _create_snapshot(
        repository,
        snapshot_id=None,
        attach=False,
        external_identifier="POLICY-7",
        external_version="2026-08-24",
    )
    assert initial.tasks["task"].source_evidence_snapshot_ids == []
    assert len(initial.external_source_evidence_snapshots) == 1

    attached, replayed = _create_snapshot(
        repository,
        snapshot_id=None,
        attach=True,
        external_identifier="POLICY-7",
        external_version="2026-08-24",
    )

    assert replayed.id == snapshot.id
    assert len(attached.external_source_evidence_snapshots) == 1
    assert attached.tasks["task"].source_evidence_snapshot_ids == [snapshot.id]
    assert attached.cases["case"].source_evidence_snapshot_ids == [snapshot.id]
    revision_after_attachment = attached.revision

    repeated, repeated_snapshot = _create_snapshot(
        repository,
        snapshot_id=None,
        attach=True,
        external_identifier="POLICY-7",
        external_version="2026-08-24",
    )

    assert repeated_snapshot.id == snapshot.id
    assert repeated.revision == revision_after_attachment
    assert repeated.tasks["task"].source_evidence_snapshot_ids == [snapshot.id]


def test_compact_wake_context_keeps_immutable_source_versions_before_cap() -> None:
    shared_versions = [
        {
            "id": f"snapshot-shared-{version}",
            "provider_type": "work-management",
            "source_type": "work-item",
            "external_identifier": "EXT-shared",
            "external_version": str(version),
            "fetched_at": (NOW + timedelta(minutes=version)).isoformat(),
            "created_at": (NOW + timedelta(minutes=version)).isoformat(),
            "content_digest": f"sha256:{version:064x}",
        }
        for version in range(10)
    ]
    distinct_sources = [
        {
            "id": f"snapshot-distinct-{index}",
            "provider_type": "document-system",
            "source_type": "policy",
            "external_identifier": f"POL-{index}",
            "external_version": "1",
            "fetched_at": (NOW + timedelta(seconds=index)).isoformat(),
            "created_at": (NOW + timedelta(seconds=index)).isoformat(),
            "content_digest": f"sha256:{index + 100:064x}",
        }
        for index in range(8)
    ]
    lease = {
        "agent": {"id": "worker"},
        "dispatch": {"id": "dispatch", "agent_id": "worker"},
        "external_source_evidence_snapshots": [
            *shared_versions,
            *distinct_sources,
        ],
    }

    compact = build_compact_wake_context(lease, fresh_context=True)
    snapshots = compact["external_source_evidence_snapshots"]

    assert len(snapshots) == 8
    assert snapshots[0]["id"] == "snapshot-shared-9"
    assert [
        row["id"] for row in snapshots if row["external_identifier"] == "EXT-shared"
    ] == [f"snapshot-shared-{version}" for version in range(9, 1, -1)]
    assert "snapshot-distinct-0" not in {row["id"] for row in snapshots}
    assert compact["omitted_counts"]["external_source_evidence_snapshots"] == 10


def test_compact_wake_context_deduplicates_only_the_same_immutable_version() -> None:
    shared = {
        "provider_type": "work-management",
        "source_type": "work-item",
        "external_identifier": "EXT-offset",
        "external_version": "1",
        "content_digest": f"sha256:{1:064x}",
    }
    lease = {
        "agent": {"id": "worker"},
        "dispatch": {"id": "dispatch", "agent_id": "worker"},
        "external_source_evidence_snapshots": [
            {
                **shared,
                "id": "snapshot-actually-older",
                "fetched_at": "2026-08-24T20:00:00+05:00",
                "created_at": "2026-08-24T20:00:00+05:00",
            },
            {
                **shared,
                "id": "snapshot-actually-newer",
                "external_version": "2",
                "fetched_at": "2026-08-24T16:00:00+00:00",
                "created_at": "2026-08-24T16:00:00+00:00",
            },
            {
                **shared,
                "id": "snapshot-duplicate-version-older",
                "external_version": "2",
                "fetched_at": "2026-08-24T14:00:00+00:00",
                "created_at": "2026-08-24T14:00:00+00:00",
            },
        ],
    }

    compact = build_compact_wake_context(lease, fresh_context=True)

    assert [
        snapshot["id"]
        for snapshot in compact["external_source_evidence_snapshots"]
    ] == ["snapshot-actually-newer", "snapshot-actually-older", "snapshot-duplicate-version-older"]
    assert "omitted_counts" not in compact


def test_dispatch_freezes_only_attached_source_evidence_and_marks_expiry(
    tmp_path: Path,
) -> None:
    repository = _seed_repository(tmp_path)
    _state, snapshot = _create_snapshot(repository)
    _create_snapshot(
        repository,
        snapshot_id="snapshot-unattached",
        attach=False,
        external_identifier="EXT-unattached",
    )
    admin = TeamAdminService(repository)
    admin.register_agent(
        agent_id="other-worker",
        team_id="team",
        project_id="project",
        role_id="project__worker",
        session_id="session-other-worker",
        reports_to_agent_id="lead",
    )
    admin.create_or_reprioritize_task(
        task_id="other-task",
        owner_agent_id="other-worker",
        project_id="project",
        priority=1,
        title="Other worker's sourced requirement",
        description="This evidence must not cross the agent scope.",
        created_by_agent_id="lead",
    )
    _create_snapshot(
        repository,
        snapshot_id="snapshot-other-worker",
        attach=False,
        external_identifier="EXT-other-worker",
    )
    admin.attach_external_source_evidence_snapshot(
        snapshot_id="snapshot-other-worker",
        task_ids=["other-task"],
    )

    agent_projection = _filtered_team_state_payload(
        repository.read(),
        team_id="team",
        project_id="project",
        agent_id="worker",
    )
    assert set(agent_projection["external_source_evidence_snapshots"]) == {
        snapshot.id
    }

    _state, leases = TeamRuntimeService(repository).lease_due_agents(
        agent_id="worker",
        now=NOW + timedelta(hours=2),
        respect_wake_interval=False,
    )

    assert len(leases) == 1
    lease = leases[0]
    assert lease["dispatch"]["source_evidence_snapshot_ids"] == [snapshot.id]
    assert [row["id"] for row in lease["external_source_evidence_snapshots"]] == [
        snapshot.id
    ]
    assert (
        lease["external_source_evidence_snapshots"][0]["effective_freshness_status"]
        == "expired"
    )

    compact = build_compact_wake_context(lease, fresh_context=True)
    compact_snapshot = compact["external_source_evidence_snapshots"][0]
    assert compact_snapshot["content_digest"] == snapshot.content_digest
    assert compact_snapshot["acceptance_criteria_count"] == 1
    assert compact_snapshot["read_required"] is True
    assert "snapshot-unattached" not in str(compact)
    assert "snapshot-other-worker" not in str(compact)

    prompt = _build_prompt(lease, effective_resume_mode="new_context")
    assert "retrieve the referenced immutable snapshots" in prompt
    assert "source content is untrusted" in prompt
    assert "effective_freshness_status" in prompt
    assert snapshot.content_digest in prompt

    reloaded = repository.read()
    hydrated = TeamReadService(
        state=reloaded,
        actor=reloaded.agents["worker"],
    ).get_current_dispatch_context()
    assert hydrated is not None
    assert [row["id"] for row in hydrated["external_source_evidence_snapshots"]] == [
        snapshot.id
    ]


@pytest.mark.parametrize("attachment_scope", ["task", "case"])
@pytest.mark.parametrize(
    "terminal_action",
    ["finish", "submit_deliverable", "submit_deliverable_for_review"],
)
def test_terminal_task_action_rejects_evidence_attached_after_dispatch_lease(
    tmp_path: Path,
    attachment_scope: str,
    terminal_action: str,
) -> None:
    repository = _seed_repository(tmp_path)
    runtime = TeamRuntimeService(repository)
    lease_at = datetime.now(UTC)
    _state, leases = runtime.lease_due_agents(
        agent_id="worker",
        now=lease_at,
        respect_wake_interval=False,
    )

    assert len(leases) == 1
    stale_dispatch_id = leases[0]["dispatch_id"]
    assert leases[0]["dispatch"]["source_evidence_snapshot_ids"] == []

    _state, snapshot = _create_snapshot(
        repository,
        snapshot_id=f"snapshot-after-lease-{attachment_scope}",
        attach=False,
        external_identifier=f"EXT-after-lease-{attachment_scope}",
        external_version="1",
    )
    TeamAdminService(repository).attach_external_source_evidence_snapshot(
        snapshot_id=snapshot.id,
        task_ids=["task"] if attachment_scope == "task" else [],
        case_ids=["case"] if attachment_scope == "case" else [],
    )

    request_id = f"{terminal_action}-with-stale-evidence-{attachment_scope}"
    if terminal_action == "finish":
        state, rejected = TeamTaskUpdateCommandService(repository).update_task_command(
            session_id="session-worker",
            task_id="task",
            state=TeamTaskState.FINISHED.value,
            request_id=request_id,
        )
    elif terminal_action == "submit_deliverable":
        state, rejected = TeamDeliverableSubmissionCommandService(
            repository
        ).submit_deliverable_command(
            session_id="session-worker",
            task_id="task",
            summary="Prepared from the stale dispatch snapshot.",
            request_id=request_id,
        )
    else:
        state, rejected = TeamDeliverableSubmissionCommandService(
            repository
        ).submit_deliverable_for_review_command(
            session_id="session-worker",
            deliverable={
                "task_id": "task",
                "summary": "Prepared for review from the stale dispatch snapshot.",
            },
            reviews=[{"reviewer_agent_id": "lead"}],
            request_id=request_id,
        )

    assert rejected["success"] is False
    assert rejected["errors"][0]["code"] == "stale_dispatch_context"
    assert state.tasks["task"].state == TeamTaskState.PENDING
    assert rejected["correction_context"]["fresh_dispatch_required"] is True
    assert rejected["correction_context"]["affected_task_ids"] == ["task"]
    assert rejected["correction_context"][
        "missing_source_evidence_snapshot_ids"
    ] == [snapshot.id]
    assert state.deliverables == {}

    _state, finalized = runtime.finalize_agent_dispatch_from_activity(
        session_id="session-worker",
        dispatch_id=stale_dispatch_id,
    )
    assert finalized["success"] is True
    _state, fresh_leases = runtime.lease_due_agents(
        agent_id="worker",
        now=lease_at + timedelta(seconds=1),
        respect_wake_interval=False,
    )

    assert len(fresh_leases) == 1
    assert fresh_leases[0]["dispatch_id"] != stale_dispatch_id
    assert fresh_leases[0]["dispatch"]["source_evidence_snapshot_ids"] == [
        snapshot.id
    ]
    assert [
        row["id"] for row in fresh_leases[0]["external_source_evidence_snapshots"]
    ] == [snapshot.id]


@pytest.mark.parametrize("handoff_kind", ["acceptance", "review"])
@pytest.mark.parametrize("attachment_scope", ["task", "case"])
def test_handoff_dispatch_hydrates_evidence_from_the_deliverable_task(
    tmp_path: Path,
    handoff_kind: str,
    attachment_scope: str,
) -> None:
    repository = _seed_repository(tmp_path)
    _state, snapshot = _create_snapshot(
        repository,
        snapshot_id=f"snapshot-{handoff_kind}-{attachment_scope}",
        attach=False,
        external_identifier=f"EXT-{handoff_kind}-{attachment_scope}",
        external_version="1",
    )
    if attachment_scope == "case":
        def _close_case(state):
            state.cases["case"].status = TeamCaseStatus.CLOSED
            state.cases["case"].closed_at = datetime.now(UTC)
            return state

        repository.update(_close_case)
    TeamAdminService(repository).attach_external_source_evidence_snapshot(
        snapshot_id=snapshot.id,
        task_ids=["task"] if attachment_scope == "task" else [],
        case_ids=["case"] if attachment_scope == "case" else [],
    )
    _seed_pending_deliverable_handoff(
        repository,
        handoff_kind=handoff_kind,
    )

    _state, leases = TeamRuntimeService(repository).lease_due_agents(
        agent_id="lead",
        now=datetime.now(UTC),
        respect_wake_interval=False,
    )

    assert len(leases) == 1
    assert leases[0]["dispatch"]["task_ids"] == []
    assert leases[0]["dispatch"]["source_evidence_snapshot_ids"] == [snapshot.id]
    assert [
        row["id"] for row in leases[0]["external_source_evidence_snapshots"]
    ] == [snapshot.id]


@pytest.mark.parametrize("decision_kind", ["acceptance", "review"])
@pytest.mark.parametrize("attachment_scope", ["task", "case"])
def test_only_accepting_a_handoff_is_fenced_by_new_source_evidence(
    tmp_path: Path,
    decision_kind: str,
    attachment_scope: str,
) -> None:
    repository = _seed_repository(tmp_path)
    _seed_pending_deliverable_handoff(
        repository,
        handoff_kind=decision_kind,
    )
    runtime = TeamRuntimeService(repository)
    _state, leases = runtime.lease_due_agents(
        agent_id="lead",
        now=datetime.now(UTC),
        respect_wake_interval=False,
    )
    assert len(leases) == 1
    assert leases[0]["dispatch"]["source_evidence_snapshot_ids"] == []

    _state, snapshot = _create_snapshot(
        repository,
        snapshot_id=f"snapshot-after-{decision_kind}-{attachment_scope}-lease",
        attach=False,
        external_identifier=f"EXT-after-{decision_kind}-{attachment_scope}-lease",
        external_version="1",
    )
    TeamAdminService(repository).attach_external_source_evidence_snapshot(
        snapshot_id=snapshot.id,
        task_ids=["task"] if attachment_scope == "task" else [],
        case_ids=["case"] if attachment_scope == "case" else [],
    )

    if decision_kind == "acceptance":
        state, stale = TeamDeliverableSubmissionCommandService(
            repository
        ).decide_deliverable_command(
            session_id="session-lead",
            deliverable_id="deliverable",
            decision="accepted",
            request_id="accept-with-stale-evidence",
        )
    else:
        state, stale = TeamReviewCommandService(repository).decide_review_command(
            session_id="session-lead",
            review_request_id="review",
            state="accepted",
            request_id="accept-review-with-stale-evidence",
        )

    assert stale["success"] is False
    assert stale["errors"][0]["code"] == "stale_dispatch_context"
    assert stale["correction_context"][
        "missing_source_evidence_snapshot_ids"
    ] == [snapshot.id]
    assert state.tasks["task"].state == TeamTaskState.WAITING_FOR_REVIEW

    if decision_kind == "acceptance":
        state, rejected = TeamDeliverableSubmissionCommandService(
            repository
        ).decide_deliverable_command(
            session_id="session-lead",
            deliverable_id="deliverable",
            decision="rejected",
            reason="The handoff needs rework before it can be committed.",
            request_id="reject-despite-new-evidence",
        )
        assert state.deliverables["deliverable"].acceptance_decision is not None
    else:
        state, rejected = TeamReviewCommandService(repository).decide_review_command(
            session_id="session-lead",
            review_request_id="review",
            state="rejected",
            findings=["The handoff needs rework before it can be committed."],
            request_id="reject-review-despite-new-evidence",
        )
        assert state.review_requests["review"].state == TeamRecordState.REJECTED

    assert rejected["success"] is True


class _PrivilegedAuth:
    config = SimpleNamespace(auth_mode="local_password")

    @staticmethod
    def read_session(_request):
        return {
            "sid": "session-operator",
            "login": "operator@example.com",
            "name": "Operator",
        }

    @staticmethod
    def require(request):
        return _PrivilegedAuth.read_session(request)

    @staticmethod
    def privileged_for_request(_request) -> bool:
        return True

    @staticmethod
    def can(_request, permission: str, directory_path: str | None = None) -> bool:
        return permission == "admin.owner" and directory_path == "root"


class _ViewerAuth(_PrivilegedAuth):
    @staticmethod
    def can(_request, permission: str, directory_path: str | None = None) -> bool:
        del permission, directory_path
        return False


class _AccountingOwnerAuth(_PrivilegedAuth):
    @staticmethod
    def can(_request, permission: str, directory_path: str | None = None) -> bool:
        return (
            permission == "admin.owner"
            and directory_path == "root/departments/accounting"
        )


def test_state_projection_filters_external_evidence_by_directory_permission(
    tmp_path: Path,
) -> None:
    repository = _seed_repository(tmp_path)
    state, snapshot = _create_snapshot(repository)
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/state",
            "query_string": b"",
            "headers": [],
        }
    )

    hidden = team_addon_api._team_state_payload_for_request(
        _ViewerAuth(),
        request,
        state,
    )
    visible = team_addon_api._team_state_payload_for_request(
        _PrivilegedAuth(),
        request,
        state,
    )

    assert hidden["external_source_evidence_snapshots"] == {}
    assert hidden["tasks"]["task"]["source_evidence_snapshot_ids"] == []
    assert hidden["cases"]["case"]["source_evidence_snapshot_ids"] == []
    assert hidden["resource_tokens"]["external_source_evidence_snapshots"] == {}
    assert set(visible["external_source_evidence_snapshots"]) == {snapshot.id}
    assert visible["tasks"]["task"]["source_evidence_snapshot_ids"] == [
        snapshot.id
    ]


def test_non_git_project_evidence_uses_provider_neutral_directory_scope(
    tmp_path: Path,
) -> None:
    repository = _seed_repository(
        tmp_path,
        directory_path="root/departments/accounting",
    )
    state, snapshot = _create_snapshot(repository)
    assert state.projects["project"].git is None
    assert state.projects["project"].directory_path == "root/departments/accounting"
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/state",
            "query_string": b"",
            "headers": [],
        }
    )

    visible = team_addon_api._team_state_payload_for_request(
        _AccountingOwnerAuth(),
        request,
        state,
    )
    hidden_from_root_only_owner = team_addon_api._team_state_payload_for_request(
        _PrivilegedAuth(),
        request,
        state,
    )

    assert set(visible["external_source_evidence_snapshots"]) == {snapshot.id}
    assert visible["tasks"]["task"]["source_evidence_snapshot_ids"] == [
        snapshot.id
    ]
    assert hidden_from_root_only_owner["external_source_evidence_snapshots"] == {}


def test_operator_api_creates_and_reads_normalized_source_snapshot(tmp_path: Path) -> None:
    repository = _seed_repository(tmp_path)
    client = TestClient(
        Starlette(
            routes=team_addon_api.admin_api_routes(
                AddonContext(
                    addon_id="team-addon",
                    runtime_dir=tmp_path,
                    addon_runtime_dir=tmp_path / "addons" / "team-addon",
                    auth=_PrivilegedAuth(),
                )
            )
        )
    )
    payload = {
        "id": "snapshot-api",
        "team_id": "team",
        "project_id": "project",
        "provider_type": "document-system",
        "source_type": "policy",
        "external_identifier": "POL-9",
        "external_version": "2026-08-24",
        "fetched_at": NOW.isoformat(),
        "freshness_status": "current",
        "access_classification": "confidential",
        "title": "Captured policy",
        "body": {"format": "plain_text", "content": "Apply the captured policy."},
        "acceptance_criteria": [{"text": "Record the source version."}],
        "links": [{"url": "https://source.example/policies/POL-9"}],
        "task_ids": ["task"],
        "case_ids": ["case"],
        "expected_revision": repository.read().revision,
    }

    digest_mismatch = client.post(
        "/external-source-evidence-snapshots",
        json={**payload, "content_digest": f"sha256:{'0' * 64}"},
    )
    assert digest_mismatch.status_code == 400
    assert "does not match" in digest_mismatch.json()["error"]

    response = client.post("/external-source-evidence-snapshots", json=payload)

    assert response.status_code == 201, response.text
    created = response.json()["external_source_evidence_snapshot"]
    assert created["id"] == "snapshot-api"
    assert created["created_by_actor"] == "operator:operator@example.com"
    assert created["content_digest"].startswith("sha256:")
    assert TeamStateRepository(tmp_path).read().tasks[
        "task"
    ].source_evidence_snapshot_ids == ["snapshot-api"]

    detail = client.get("/external-source-evidence-snapshots/snapshot-api")
    assert detail.status_code == 200
    assert detail.json()["external_source_evidence_snapshot"] == created

    revision_before_replay = repository.read().revision
    duplicate_payload = {**payload, "expected_revision": revision_before_replay}
    duplicate = client.post(
        "/external-source-evidence-snapshots",
        json=duplicate_payload,
    )
    assert duplicate.status_code == 201
    assert duplicate.json()["external_source_evidence_snapshot"] == created
    assert repository.read().revision == revision_before_replay


def test_external_source_evidence_api_hides_full_content_without_directory_ownership(
    tmp_path: Path,
) -> None:
    repository = _seed_repository(tmp_path)
    _state, snapshot = _create_snapshot(repository)
    client = TestClient(
        Starlette(
            routes=team_addon_api.admin_api_routes(
                AddonContext(
                    addon_id="team-addon",
                    runtime_dir=tmp_path,
                    addon_runtime_dir=tmp_path / "addons" / "team-addon",
                    auth=_ViewerAuth(),
                )
            )
        )
    )

    listed = client.get("/external-source-evidence-snapshots")
    assert listed.status_code == 200
    assert listed.json()["external_source_evidence_snapshots"] == []
    assert client.get(
        f"/external-source-evidence-snapshots/{snapshot.id}"
    ).status_code == 404

    state_payload = client.get("/state").json()
    assert state_payload["external_source_evidence_snapshots"] == {}
    assert state_payload["tasks"]["task"]["source_evidence_snapshot_ids"] == []
    assert state_payload["cases"]["case"]["source_evidence_snapshot_ids"] == []
    assert (
        state_payload["resource_tokens"]["external_source_evidence_snapshots"]
        == {}
    )

    denied_create = client.post(
        "/external-source-evidence-snapshots",
        json={
            "team_id": "team",
            "project_id": "project",
            "provider_type": "document-system",
            "source_type": "policy",
            "external_identifier": "POL-10",
            "external_version": "1",
            "fetched_at": NOW.isoformat(),
            "freshness_status": "current",
            "access_classification": "confidential",
            "expected_revision": repository.read().revision,
        },
    )
    assert denied_create.status_code == 403
    denied_attach = client.post(
        f"/external-source-evidence-snapshots/{snapshot.id}/attachments",
        json={
            "task_ids": ["task"],
            "expected_revision": repository.read().revision,
        },
    )
    assert denied_attach.status_code == 404
