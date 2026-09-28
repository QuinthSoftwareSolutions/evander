"""Reviewed recovery work must satisfy the whole lifecycle before adoption."""

import pytest

from evander_team_addon.models import (
    AgentTask,
    GitPromotionRecord,
    GitPromotionSource,
    ReleasePackage,
    ReleaseScopeValidation,
    TeamRecordState,
    utc_now,
)
from evander_team_addon.services.domain.blockers import open_release_blocker
from evander_team_addon.services.domain.task_assessment import assess_task
from evander_team_addon.services.git_release_service import TeamGitReleaseService
from evander_team_addon.services.runtime_service import TeamRuntimeService
from test_team_autonomous_git_recovery import (
    accept_existing,
    accept_successor,
    apply,
    setup_publication_recovery,
    setup_recovery,
)


@pytest.mark.parametrize("edited_task_id", ["integration", "contribution"])
def test_changed_requirements_cannot_be_satisfied_by_an_older_reviewed_successor(
    tmp_path, edited_task_id
):
    repo, service, blocker_id = setup_recovery(
        tmp_path, cause="incompatible_source_base", clean=False
    )
    state, result = apply(service, blocker_id)
    original = state.tasks[edited_task_id]
    old_description = state.tasks[result["task_id"]].description
    service.admin.create_or_reprioritize_task(
        task_id=original.id,
        project_id=original.project_id,
        owner_agent_id=original.owner_agent_id,
        priority=original.priority,
        title=original.title,
        description=original.description + " Also implement an audit log.",
        created_by_agent_id="lead",
    )
    state = accept_successor(repo, result["task_id"])
    state, _ = TeamRuntimeService(repo).reconcile_git_work_contexts()
    assert assess_task(state, result["task_id"]).resolution_status == "resolved"
    assert state.tasks[result["task_id"]].description == old_description
    assert state.tasks["integration"].state != "cancelled"
    assert state.blockers[blocker_id].state == "open"
    assert state.git_recovery_incidents[result["incident_id"]].state == "waiting"
    preview = service.preview(blocker_id)
    assert not preview["actions"]
    with pytest.raises(ValueError):
        service.apply_as_operator(
            blocker_id=blocker_id,
            preview_token=preview["preview_token"],
            action_id="resume_reviewed_successor",
            request_id="new-requirements",
            actor_id="test-operator",
        )


def test_recovery_waits_for_every_product_then_adopts_all_reviewed_products(tmp_path):
    repo, service, blocker_id = setup_recovery(
        tmp_path, cause="incompatible_source_base", clean=False
    )
    _, result = apply(service, blocker_id)
    successor_id = result["task_id"]
    accept_successor(repo, successor_id, callback=False)
    accept_successor(repo, successor_id, callback=False, deliverable_id="second-product")
    accept_existing(repo, "reviewed-successor")
    state, _ = TeamRuntimeService(repo).reconcile_git_work_contexts()
    assert assess_task(state, successor_id).resolution_status == "unresolved"
    assert state.tasks["integration"].state != "cancelled"
    assert state.blockers[blocker_id].state == "open"
    assert state.git_recovery_incidents[result["incident_id"]].state == "awaiting_review"

    state = accept_existing(repo, "second-product")
    assert assess_task(state, successor_id).resolution_status == "resolved"
    assert state.tasks["integration"].state == "cancelled"
    assert state.blockers[blocker_id].state == "resolved"
    incident = state.git_recovery_incidents[result["incident_id"]]
    assert incident.state == "resumed"
    assert incident.evidence["replacement_deliverable_ids"] == [
        "reviewed-successor",
        "second-product",
    ]


def test_terminal_recovery_task_with_unresolved_child_is_not_adopted(tmp_path):
    repo, service, blocker_id = setup_recovery(
        tmp_path, cause="incompatible_source_base", clean=False
    )
    _, result = apply(service, blocker_id)
    successor_id = result["task_id"]
    accept_successor(repo, successor_id, callback=False)

    def unfinished_child(state):
        state.tasks["unfinished-child"] = AgentTask(
            id="unfinished-child",
            project_id="project",
            owner_agent_id="backend",
            parent_task_id=successor_id,
            priority=1,
            title="Remaining verification",
            description="This required child has not finished.",
            created_by_agent_id="lead",
        )
        return state

    repo.update(unfinished_child)
    accept_existing(repo, "reviewed-successor")
    state, _ = TeamRuntimeService(repo).reconcile_git_work_contexts()
    assert state.tasks[successor_id].state == "finished"
    assert assess_task(state, successor_id).resolution_status == "invalid"
    assert state.tasks["integration"].state != "cancelled"
    assert state.blockers[blocker_id].state == "open"


@pytest.mark.parametrize("unrelated_failure", [False, True])
def test_published_successor_closes_only_its_original_failed_publication(
    tmp_path, unrelated_failure
):
    repo, service, incident, _, newer, _ = setup_publication_recovery(tmp_path)
    _, result = apply(service, incident.blocker_id)
    state = accept_successor(repo, result["task_id"])
    assert assess_task(state, result["task_id"]).resolution_status == "resolved"
    assert state.git_recovery_incidents[incident.id].state == "awaiting_review"
    assert state.blockers[incident.blocker_id].state == "open"
    original = state.release_packages["release"].model_copy(deep=True)

    def prepare_publication(state):
        release = ReleasePackage(
            id="successor-release",
            project_id="project",
            deliverable_ids=["reviewed-successor"],
            feature_branches=[],
            release_branch="team/team/releases/successor-release",
            canonical_branch="main",
            base_commit_sha=newer,
            candidate_commit_sha="e" * 40,
            approved_commit_sha="e" * 40,
            policy_version=2,
            scope_validation=ReleaseScopeValidation(state="passed"),
            state=TeamRecordState.PENDING,
            git_promotion_record_ids=["successor-push"],
        )
        state.release_packages[release.id] = release
        state.git_promotions["successor-push"] = GitPromotionRecord(
            id="successor-push",
            project_id="project",
            actor_agent_id="backend",
            source_branch=release.release_branch,
            sources=[
                GitPromotionSource(
                    source_branch=release.release_branch, expected_commit_sha="e" * 40
                )
            ],
            target_branch="main",
            promotion_kind="canonical_push",
            release_package_id=release.id,
            state="succeeded",
            commit_sha="e" * 40,
        )
        if unrelated_failure:
            unrelated = original.model_copy(deep=True)
            unrelated.id = "unrelated-failed-release"
            unrelated.release_branch = "team/team/releases/unrelated"
            state.release_packages[unrelated.id] = unrelated
            open_release_blocker(
                state,
                release_package_id=unrelated.id,
                summary="Independent publication failure",
                details="This failure has no reviewed successor.",
                result={},
                now=utc_now(),
            )
        return state

    repo.update(prepare_publication)
    state, published = TeamGitReleaseService(repo).mark_release_published(
        release_package_id="successor-release",
        success=True,
        canonical_branch="main",
        published_commit_sha="e" * 40,
    )
    assert published["state"] == "accepted", published["error"]
    assert state.git_recovery_incidents[incident.id].state == "resumed"
    blocker = state.blockers[incident.blocker_id]
    assert blocker.state == "resolved"
    assert "successor-release" in blocker.terminal_reference_ids
    assert "e" * 40 in blocker.terminal_reference_ids
    assert state.release_packages["release"].state == "rejected"
    assert state.release_packages["release"].model_dump(
        exclude={"state", "error", "updated_at"}
    ) == original.model_dump(exclude={"state", "error", "updated_at"})
    assert "release" in published["superseded_release_package_ids"]
    assert incident.blocker_id in published["resolved_blocker_ids"]
    assert (state.projects["project"].status == "blocked") == unrelated_failure
    if unrelated_failure:
        assert state.release_packages["unrelated-failed-release"].state == "blocked"
        assert any(
            row.source_id == "unrelated-failed-release" and row.state == "open"
            for row in state.blockers.values()
        )
    before = state.git_recovery_incidents[incident.id].model_copy(deep=True)
    state, _ = TeamRuntimeService(repo).reconcile_git_work_contexts()
    assert state.git_recovery_incidents[incident.id] == before
