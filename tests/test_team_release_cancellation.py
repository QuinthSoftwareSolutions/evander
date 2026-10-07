from __future__ import annotations

import asyncio
import shutil
import sqlite3
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import httpx
import pytest

from team_ui_controller_harness import controller_script
from starlette.applications import Starlette

from evander.addons import AddonContext
from evander_team_addon.api import admin_api_routes
from evander_team_addon.models import (
    AgentTask,
    GitPromotionRecord,
    ReleaseReviewRequirement,
    ReleaseVerificationRun,
    ReviewProfile,
    ReviewRequest,
    TeamBlockerSourceKind,
    TeamRecordState,
    TeamTaskState,
)
from evander_team_addon.repository import TeamStateConflictError, TeamStateRepository
from evander_team_addon.services.admin_service import TeamAdminService
from evander_team_addon.services.domain.blockers import open_or_get_blocker
from evander_team_addon.services.domain.human_reviews import (
    ensure_human_review_request,
    reassign_human_review,
    record_human_review_decision,
    resume_human_review,
)
from evander_team_addon.services.domain.project_status import project_blocking_sources
from evander_team_addon.services.domain.release_gates import (
    advance_release_gates,
    record_release_verification_result,
    retry_release_gate,
)
from evander_team_addon.services.domain.release_packages import _release_package_requires_recreation
from evander_team_addon.services.git_release_service import TeamGitReleaseService
from evander_team_addon.services.candidate_correction_reconciliation_service import (
    reconcile_candidate_correction_successors,
)
from test_team_addon_api import _HumanApiAuthStub
from test_team_human_reviews import _human_state


def _repository(tmp_path: Path):
    state, release, _ = _human_state(eligible_user_ids=["jonathan"])
    human = ensure_human_review_request(state, release=release, result={})
    assert human is not None
    state.tasks["correction"] = AgentTask(
        id="correction",
        owner_agent_id="owner",
        project_id="project",
        priority=1,
        title="Release correction",
        description="Only needed for this release",
        created_by_actor="system:release-review-correction",
        release_base_package_id=release.id,
    )
    release.review_correction_task_id = "correction"
    state.review_requests["review"] = ReviewRequest(
        id="review",
        release_package_id=release.id,
        candidate_commit_sha=release.candidate_commit_sha,
        reviewer_agent_id="owner",
        state=TeamRecordState.PENDING,
    )
    release.review_request_ids.append("review")
    state.release_verification_runs["verification"] = ReleaseVerificationRun(
        id="verification",
        release_package_id=release.id,
        candidate_commit_sha=release.candidate_commit_sha,
        session_id="verify-session",
        state="running",
    )
    release.verification_run_ids.append("verification")
    for kind, source in [
        (TeamBlockerSourceKind.HUMAN_REVIEW_REQUEST, human.id),
        (TeamBlockerSourceKind.RELEASE_PACKAGE, release.id),
        (TeamBlockerSourceKind.REVIEW_REQUEST, "review"),
        (
            TeamBlockerSourceKind.CANDIDATE_CORRECTION_SUCCESSOR,
            "candidate-correction-successor:release",
        ),
    ]:
        open_or_get_blocker(
            state,
            source_kind=kind,
            source_id=source,
            summary="Cannot continue",
            details="Obsolete attempt",
            reporter_agent_id="owner",
            team_id="team",
            project_id="project",
            result={},
            resolution_scope="downstream"
            if kind == TeamBlockerSourceKind.CANDIDATE_CORRECTION_SUCCESSOR
            else "task",
        )
    repo = TeamStateRepository(tmp_path)
    saved = repo.update(lambda _: state)
    return repo, saved, human.id


def test_cancel_release_closes_obligations_without_rework_and_persists_audit(tmp_path):
    repo, before, human_id = _repository(tmp_path)
    state, result = TeamAdminService(repo).cancel_release_package(
        release_package_id="release",
        operator_actor_id="jonathan",
        reason="Duplicate",
        expected_revision=before.revision,
    )
    state = repo.read()
    release = state.release_packages["release"]
    assert release.state == TeamRecordState.CANCELLED
    assert release.cancellation.actor_id == "jonathan"
    assert release.cancellation.reason == "Duplicate"
    assert release.candidate_commit_sha == before.release_packages["release"].candidate_commit_sha
    assert state.human_review_requests[human_id].state == "cancelled"
    assert state.human_review_requests[human_id].completed_at is not None
    assert state.review_requests["review"].retired_at is not None
    assert state.review_requests["review"].state == TeamRecordState.CANCELLED
    assert state.release_verification_runs["verification"].state == "cancelled"
    assert state.tasks["correction"].state == TeamTaskState.CANCELLED
    assert state.tasks["source-task"].state == TeamTaskState.FINISHED
    assert state.deliverables["source-deliverable"].state == TeamRecordState.ACCEPTED
    assert result["cancelled_task_ids"] == ["correction"]
    assert len(result["resolved_blocker_ids"]) == 4
    assert not project_blocking_sources(state, "project")
    reconcile_candidate_correction_successors(state, result=result)
    assert len(state.release_packages) == 1
    assert result.get("events", []) == []
    assert set(state.tasks) == set(before.tasks)
    assert not state.human_review_decisions
    assert all(item.state.value == "handled" for item in state.inbox.values())
    assert repo.list_outbox_records() == []
    assert _release_package_requires_recreation(release)


def _sealed_cohort_repository(tmp_path, review_state, *, replaced=False):
    repo, _, human_id = _repository(tmp_path)

    def seal(state):
        release = state.release_packages["release"]
        release.review_cohort_id = "release-cohort"
        release.review_cohort_sealed_at = state.updated_at
        release.manifest.review_requirements = []
        for reviewer_id, review_id in [("owner", "review"), ("lead", "second-review")]:
            profile_id = f"{reviewer_id}-profile"
            state.review_profiles[profile_id] = ReviewProfile(
                id=profile_id, team_id="team", project_id="project", name=profile_id,
            )
            release.manifest.review_requirements.append(ReleaseReviewRequirement(
                reviewer_agent_id=reviewer_id, review_profile_id=profile_id,
            ))
            state.review_requests[review_id] = ReviewRequest(
                id=review_id,
                release_package_id=release.id,
                candidate_commit_sha=release.candidate_commit_sha,
                reviewed_commit_sha=release.candidate_commit_sha,
                reviewer_agent_id=reviewer_id,
                review_profile_id=profile_id,
                review_cohort_id=release.review_cohort_id,
                state=review_state,
                findings=["Recorded review evidence"],
                decision_at=state.updated_at if review_state != TeamRecordState.PENDING else None,
            )
        release.review_request_ids = ["review", "second-review"]
        if replaced:
            previous = state.review_requests["review"].model_copy(deep=True)
            previous.id = "previous-review"
            previous.state = TeamRecordState.BLOCKED
            previous.superseded_by_review_request_id = "review"
            state.review_requests[previous.id] = previous
            state.review_requests["review"].supersedes_review_request_id = previous.id
            state.review_requests["review"].retry_reason = "Checkout restored"
            state.review_requests["review"].retry_requested_by = "operator:jonathan"
            release.review_request_ids.insert(0, previous.id)
        return state

    before = repo.update(seal)
    return repo, before, human_id


@pytest.mark.parametrize("review_state", [
    TeamRecordState.PENDING, TeamRecordState.BLOCKED, TeamRecordState.ACCEPTED,
])
@pytest.mark.parametrize("replaced", [False, True])
def test_cancel_sealed_release_preserves_frozen_cohort_and_decisions(
    tmp_path, review_state, replaced,
):
    repo, before, human_id = _sealed_cohort_repository(
        tmp_path, review_state, replaced=replaced,
    )
    TeamAdminService(repo).cancel_release_package(
        release_package_id="release",
        operator_actor_id="jonathan",
        reason="Duplicate",
        expected_revision=before.revision,
    )
    after = repo.read()
    release = after.release_packages["release"]
    assert release.state == TeamRecordState.CANCELLED
    assert release.manifest == before.release_packages["release"].manifest
    assert release.review_request_ids == before.release_packages["release"].review_request_ids
    assert release.review_cohort_id == "release-cohort"
    for review_id in release.review_request_ids:
        review = after.review_requests[review_id]
        previous = before.review_requests[review_id]
        assert review.retired_at is not None
        assert review.findings == previous.findings
        assert review.decision_at == previous.decision_at
        assert review.reviewed_commit_sha == previous.reviewed_commit_sha
        assert review.superseded_by_review_request_id == previous.superseded_by_review_request_id
        assert review.state == (
            TeamRecordState.ACCEPTED
            if previous.state == TeamRecordState.ACCEPTED else TeamRecordState.CANCELLED
        )
    assert after.human_review_requests[human_id].state == "cancelled"
    assert not project_blocking_sources(after, "project")


@pytest.mark.parametrize("cancelled", [False, True])
def test_sealed_release_still_requires_its_frozen_members_after_cancellation(tmp_path, cancelled):
    repo, before, _ = _sealed_cohort_repository(tmp_path, TeamRecordState.ACCEPTED)
    if cancelled:
        before, _ = TeamAdminService(repo).cancel_release_package(
            release_package_id="release", operator_actor_id="jonathan",
            expected_revision=before.revision,
        )

    def remove_member(state):
        state.release_packages["release"].review_request_ids.remove("second-review")
        return state

    with pytest.raises(ValueError, match="cohort membership does not match"):
        repo.update(remove_member, expected_revision=before.revision)
    assert repo.read_revision() == before.revision


def test_cancelled_release_cannot_be_revived_by_callbacks_or_review_decisions(tmp_path):
    repo, before, human_id = _repository(tmp_path)
    state, _ = TeamAdminService(repo).cancel_release_package(
        release_package_id="release",
        operator_actor_id="jonathan",
        expected_revision=before.revision,
    )
    result = {}
    release = state.release_packages["release"]
    advance_release_gates(state, release=release, result=result)
    record_release_verification_result(
        state,
        release_package_id="release",
        verification_run_id="verification",
        candidate_commit_sha=release.candidate_commit_sha,
        verification_state="failed",
        failure_kind="candidate",
        command_results=[],
        error="Late failure",
        result=result,
    )
    assert result.get("events", []) == []
    assert release.state == TeamRecordState.CANCELLED
    assert state.release_verification_runs["verification"].state == "cancelled"
    assert not project_blocking_sources(state, "project")
    with pytest.raises(ValueError, match="not blocked"):
        retry_release_gate(
            state,
            release=release,
            gate_kind="review",
            gate_id="review",
            reason="retry",
            requested_by="operator:jonathan",
            result={},
        )
    with pytest.raises(ValueError, match="blocked"):
        resume_human_review(state, human_review_request_id=human_id, result={})
    with pytest.raises(ValueError, match="terminal"):
        reassign_human_review(
            state,
            human_review_request_id=human_id,
            user_ids=["jonathan"],
            assigned_by_user_id="jonathan",
            result={},
        )
    with pytest.raises(ValueError, match="cancelled"):
        record_human_review_decision(
            state,
            human_review_request_id=human_id,
            reviewer_user_id="jonathan",
            reviewer_display_name="Jonathan",
            decision="approved",
            checklist_results={"smoke": True},
            confirmed_finding_ids=[],
            findings=[],
            summary="late",
            evidence_refs=[],
            request_id="late",
            expected_revision=state.human_review_requests[human_id].revision,
            result={},
        )
    after, receipt = TeamGitReleaseService(repo).mark_release_published(
        release_package_id="release",
        success=False,
        error="Late publication failure",
    )
    assert receipt["ignored_terminal_state"]
    assert after.release_packages["release"].state == TeamRecordState.CANCELLED
    after, receipt = TeamGitReleaseService(repo).record_git_promotion_result(
        promotion_id="release-promotion",
        success=False,
        error="Late candidate failure",
    )
    assert receipt["ignored_terminal_state"]
    assert not project_blocking_sources(after, "project")


@pytest.mark.parametrize("published", [False, True])
@pytest.mark.parametrize("promotion_state", ["pending", "failed", "retired", "succeeded"])
def test_cannot_cancel_after_publication_boundary(tmp_path, published, promotion_state):
    repo, before, _ = _repository(tmp_path)

    def queue(state):
        state.git_promotions["publish"] = GitPromotionRecord(
            id="publish",
            project_id="project",
            actor_agent_id="lead",
            source_branch="candidate",
            target_branch="main",
            promotion_kind="canonical_push",
            release_package_id="release",
            state=promotion_state,
        )
        if published:
            state.release_packages["release"].published_commit_sha = "a" * 40
        return state

    before = repo.update(queue)
    with pytest.raises(ValueError, match="[Pp]ublish|Publication"):
        TeamAdminService(repo).cancel_release_package(
            release_package_id="release",
            operator_actor_id="jonathan",
            expected_revision=before.revision,
        )
    assert repo.read_revision() == before.revision


def test_cancellation_revision_and_idempotency(tmp_path):
    repo, before, _ = _repository(tmp_path)
    service = TeamAdminService(repo)
    with pytest.raises(TeamStateConflictError):
        service.cancel_release_package(
            release_package_id="release",
            operator_actor_id="jonathan",
            expected_revision=before.revision - 1,
        )
    state, _ = service.cancel_release_package(
        release_package_id="release", operator_actor_id="jonathan"
    )
    audit = state.release_packages["release"].cancellation
    state, result = service.cancel_release_package(
        release_package_id="release", operator_actor_id="other"
    )
    assert result["already_cancelled"]
    assert state.release_packages["release"].cancellation == audit


def test_cancel_release_api_requires_owner_and_revision(tmp_path):
    repo, before, _ = _repository(tmp_path)

    async def request(owner, payload):
        app = Starlette(
            routes=admin_api_routes(
                AddonContext(
                    addon_id="team-addon",
                    runtime_dir=tmp_path,
                    addon_runtime_dir=tmp_path / "addons" / "team-addon",
                    auth=_HumanApiAuthStub(login="jonathan", owner=owner),
                )
            )
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            return await client.post("/release-packages/release/cancel", json=payload)

    denied = asyncio.run(request(False, {"expected_revision": before.revision}))
    assert denied.status_code == 403
    assert repo.read_revision() == before.revision
    assert asyncio.run(request(True, {})).status_code == 400
    response = asyncio.run(
        request(True, {"expected_revision": before.revision, "reason": "Duplicate"})
    )
    assert response.status_code == 200, response.text
    assert repo.read().release_packages["release"].state == TeamRecordState.CANCELLED


def test_shared_correction_work_and_unrelated_blocker_survive(tmp_path):
    repo, _, _ = _repository(tmp_path)

    def add_shared_work(state):
        state.tasks["independent-pinned"] = AgentTask(
            id="independent-pinned",
            owner_agent_id="owner",
            project_id="project",
            priority=1,
            title="New request using the candidate",
            description="Independently requested",
            created_by_agent_id="lead",
            release_base_package_id="release",
        )
        state.tasks["independent"] = AgentTask(
            id="independent",
            owner_agent_id="owner",
            project_id="project",
            priority=1,
            title="Independent work",
            description="Needs shared correction",
            created_by_agent_id="lead",
            dependency_task_ids=["correction"],
        )
        open_or_get_blocker(
            state,
            source_kind="task",
            source_id="independent",
            task_id="independent",
            summary="Other problem",
            reporter_agent_id="owner",
            team_id="team",
            project_id="project",
            result={},
        )
        return state

    before = repo.update(add_shared_work)
    after, result = TeamAdminService(repo).cancel_release_package(
        release_package_id="release",
        operator_actor_id="jonathan",
        expected_revision=before.revision,
    )
    assert result["cancelled_task_ids"] == []
    assert after.tasks["correction"].state == before.tasks["correction"].state
    assert after.tasks["independent-pinned"].state == TeamTaskState.PENDING
    assert any(
        b.source_id == "independent" and b.state.value == "open" for b in after.blockers.values()
    )


def test_cancelling_rejected_human_review_stops_its_correction_graph(tmp_path):
    from evander_team_addon.services.human_correction_reconciliation_service import (
        HumanCorrectionReconciliationService,
    )

    state, release, _ = _human_state(eligible_user_ids=["jonathan"])
    request = ensure_human_review_request(state, release=release, result={})
    record_human_review_decision(
        state,
        human_review_request_id=request.id,
        reviewer_user_id="jonathan",
        reviewer_display_name="Jonathan",
        decision="changes_requested",
        request_id="reject",
        expected_revision=request.revision,
        checklist_results={},
        findings=[{"text": "Needs correction", "target_deliverable_ids": ["source-deliverable"]}],
        result={},
    )
    original_findings = {
        k: v.model_dump(mode="json") for k, v in state.human_review_findings.items()
    }
    repo = TeamStateRepository(tmp_path)
    before = repo.update(lambda _: state)
    after, result = TeamAdminService(repo).cancel_release_package(
        release_package_id="release",
        operator_actor_id="jonathan",
        expected_revision=before.revision,
    )
    assert result["cancelled_task_ids"]
    assert after.human_review_requests[request.id].state == "changes_requested"
    assert {
        k: v.model_dump(mode="json") for k, v in after.human_review_findings.items()
    } == original_findings
    assert all(case.status.value == "closed" for case in after.cases.values())
    assert not project_blocking_sources(after, "project")
    HumanCorrectionReconciliationService(repo).reconcile()
    assert len(repo.read().release_packages) == 1


def test_migration_adds_cancellation_column_to_existing_database(tmp_path):
    repo, _, _ = _repository(tmp_path)
    with sqlite3.connect(repo.store.path) as connection:
        connection.execute("ALTER TABLE team_release_packages DROP COLUMN cancellation_json")
        connection.execute(
            "UPDATE team_state_metadata SET value = '76' WHERE key = 'sqlite_schema_version'"
        )
    repo = TeamStateRepository(tmp_path)
    before = repo.read()
    assert before.release_packages["release"].cancellation is None
    TeamAdminService(repo).cancel_release_package(
        release_package_id="release",
        operator_actor_id="jonathan",
        expected_revision=before.revision,
    )
    assert (
        TeamStateRepository(tmp_path).read().release_packages["release"].cancellation.actor_id
        == "jonathan"
    )


def test_publication_and_cancellation_have_one_atomic_winner(tmp_path):
    state, release, _ = _human_state(eligible_user_ids=["jonathan"])
    request = ensure_human_review_request(state, release=release, result={})
    repo = TeamStateRepository(tmp_path)
    repo.update(lambda _: state)
    barrier = Barrier(2)

    def approve():
        barrier.wait(timeout=5)

        def mutate(current):
            record_human_review_decision(
                current,
                human_review_request_id=request.id,
                reviewer_user_id="jonathan",
                reviewer_display_name="Jonathan",
                decision="approved",
                request_id="approve",
                expected_revision=request.revision,
                checklist_results={"smoke": True},
                result={},
            )
            return current

        try:
            repo.update(mutate)
        except ValueError:
            pass

    def cancel():
        barrier.wait(timeout=5)
        try:
            TeamAdminService(repo).cancel_release_package(
                release_package_id="release", operator_actor_id="jonathan"
            )
        except ValueError:
            pass

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(approve), pool.submit(cancel)]
        for future in futures:
            future.result(timeout=10)
    after = repo.read()
    cancelled = after.release_packages["release"].state == TeamRecordState.CANCELLED
    publication_queued = any(
        p.promotion_kind == "canonical_push" for p in after.git_promotions.values()
    )
    assert cancelled != publication_queued


def test_cancelled_release_ui_has_no_retry_and_cancel_button_uses_revision():
    node = shutil.which("node")
    if not node:
        pytest.skip("node required for UI behavior test")
    script = (
        """
const assert = require("node:assert/strict");
const text = value => String(value ?? "").trim();
const escapeHtml = value => text(value).replaceAll("<", "&lt;");
const escapeAttr = escapeHtml;
const lockedAttrs = () => "";
const state = { revision: 42 };
let sent;
const setStatus = () => {};
const applyMutationResultPayload = async () => {};
const API_BASE = "/team";
const teamFetch = async (url, options) => { sent = {url, ...JSON.parse(options.body)}; return {ok: true, json: async () => ({})}; };
"""
        + "// CONTROLLER"
        + """
assert.match(renderReleaseCancellation({id: "release", cancellation_available: true}), /Cancel release/);
assert.equal(renderReleaseCancellation({id: "release", cancellation_available: false}), "");
const cancelled = {id: "release", state: "cancelled", cancellation: {actor_id: "operator", reason: "<duplicate>"}};
assert.doesNotMatch(renderReleaseCancellation(cancelled), /data-release-cancel/);
assert.match(renderReleaseCancellation(cancelled), /&lt;duplicate&gt;/);
assert.equal(releaseProgressStages(cancelled)[0].label, "Release cancelled");
(async () => {
  await cancelRelease({dataset: {releaseId: "release"}}, "duplicate");
  assert.deepEqual(sent, {url: "/api/addons/team-addon/release-packages/release/cancel", reason: "duplicate", expected_revision: 42});
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
    )
    script = controller_script(
        "releases",
        script,
        "{app: {state, setStatus}, access: {lockedAttrs, teamFetch}, mutations: {applyMutationResultPayload}}",
        ["renderReleaseCancellation", "releaseProgressStages", "cancelRelease"],
    )
    result = subprocess.run(
        [node, "--input-type=commonjs"], input=script, text=True, capture_output=True, timeout=15
    )
    assert result.returncode == 0, result.stderr
