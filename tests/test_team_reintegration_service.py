from types import SimpleNamespace

import pytest

from evander_team_addon.models import ProjectReleasePolicy, ProjectStatus, TeamRecordState
from evander_team_addon.repository import TeamStateConflictError
from evander_team_addon.services.admin_service import TeamAdminService
from evander_team_addon.services.reintegration_service import TeamReintegrationService
from test_team_addon_git_effects import _seed_team, _seed_release


def setup_service(tmp_path):
    repo = _seed_team(tmp_path)
    _seed_release(repo)

    def pin_sources(state):
        refs = state.deliverables["deliverable-1"].git_refs
        refs.commit_sha = "c" * 40
        refs.agent_branch = "agents/session-backend/work"
        return state

    repo.update(pin_sources)
    managed = SimpleNamespace(
        get_repository=lambda *args, **kwargs: {"default_branch": "trunk"},
        resolve_branch_commit=lambda *args, **kwargs: "a" * 40,
        refresh_repository=lambda *args: {},
    )
    return (
        repo,
        TeamReintegrationService(TeamAdminService(repo, managed_git_admin=managed)),
        managed,
    )


def test_preview_submission_pins_inputs_and_replays_once(tmp_path):
    repo, service, _ = setup_service(tmp_path)
    preview = service.preview("release-1")
    assert preview["base_branch"] == "trunk"
    assert preview["owner_agent_id"] == "lead"
    assert preview["contributions"][0]["title"] == "Build backend"
    state, result = service.submit(
        preview=preview, actor_id="operator:test", request_id="request-1"
    )
    task = state.tasks[result["task_id"]]
    assert task.git_base_commit_sha == "a" * 40
    assert task.deliverable_contract.pinned_source_deliverable_ids == ["deliverable-1"]
    assert task.work_lineage_id == state.release_packages["release-1"].work_lineage_id
    assert task.work_lineage_id == state.tasks["task"].work_lineage_id
    assert state.release_packages["release-1"].state == "pending"
    _, replay = service.submit(preview=preview, actor_id="operator:test", request_id="request-1")
    assert replay["idempotent_replay"]
    assert replay["task_id"] == task.id
    assert len(repo.list_outbox_records(intent_type="team.agent.wake_requested")) == 1


def test_changed_base_requires_fresh_preview(tmp_path):
    repo, service, managed = setup_service(tmp_path)
    preview = service.preview("release-1")
    managed.resolve_branch_commit = lambda *args, **kwargs: "b" * 40
    with pytest.raises(ValueError, match="preview changed"):
        service.submit(preview=preview, actor_id="operator:test", request_id="request-1")
    assert len(repo.read().tasks) == 1


def test_concurrent_state_change_rejects_preview(tmp_path):
    repo, service, _ = setup_service(tmp_path)
    preview = service.preview("release-1")

    def change(state):
        state.tasks["task"].title = "Updated contribution title"
        return state

    repo.update(change)
    with pytest.raises(TeamStateConflictError):
        service.submit(preview=preview, actor_id="operator:test", request_id="request-1")


def test_configured_owner_and_retirement_apply_only_to_source(tmp_path):
    repo, service, _ = setup_service(tmp_path)

    def configure(state):
        state.projects["project"].release_policy = ProjectReleasePolicy(
            reintegration={"owner_agent_id": "backend", "retire_source_release": True}
        )
        return state

    repo.update(configure)
    preview = service.preview("release-1")
    assert preview["retire_source_release"]
    state, result = service.submit(
        preview=preview, actor_id="operator:test", request_id="request-1"
    )
    assert state.tasks[result["task_id"]].owner_agent_id == "backend"
    assert state.release_packages["release-1"].state == "cancelled"
    assert state.tasks["task"].state == "finished"


def test_out_of_project_owner_is_rejected(tmp_path):
    _, service, _ = setup_service(tmp_path)
    with pytest.raises(ValueError, match="owner"):
        service.preview("release-1", owner_agent_id="manager")


def test_unpinned_contribution_is_rejected_before_creating_work(tmp_path):
    repo, service, _ = setup_service(tmp_path)

    def unpin(state):
        state.deliverables["deliverable-1"].git_refs.commit_sha = None
        return state

    repo.update(unpin)
    with pytest.raises(ValueError, match="consistent full"):
        service.preview("release-1")
    assert len(repo.read().tasks) == 1


def test_blocked_project_can_reintegrate_without_waiving_source_gate(tmp_path):
    repo, service, _ = setup_service(tmp_path)

    def block(state):
        state.projects["project"].status = ProjectStatus.BLOCKED
        state.release_packages["release-1"].state = TeamRecordState.BLOCKED
        return state

    repo.update(block)
    state, result = service.submit(
        preview=service.preview("release-1"), actor_id="operator:test", request_id="blocked"
    )
    assert result["task_id"] in state.tasks
    assert state.release_packages["release-1"].state == "blocked"


def test_multi_contribution_integration_keeps_release_lineage(tmp_path):
    repo, service, _ = setup_service(tmp_path)

    def add_source(state):
        from evander_team_addon.models import TeamWorkLineage

        state.tasks["task-2"] = state.tasks["task"].model_copy(
            update={"id": "task-2", "deliverable_ids": ["deliverable-2"]}
        )
        state.deliverables["deliverable-2"] = state.deliverables["deliverable-1"].model_copy(
            update={"id": "deliverable-2", "task_id": "task-2"}
        )
        state.release_packages["release-1"].deliverable_ids.append("deliverable-2")
        state.work_lineages["release-lineage"] = TeamWorkLineage(id="release-lineage")
        state.release_packages["release-1"].work_lineage_id = "release-lineage"
        return state

    repo.update(add_source)
    state, result = service.submit(
        preview=service.preview("release-1"), actor_id="operator:test", request_id="multi"
    )
    task = state.tasks[result["task_id"]]
    assert task.work_lineage_id == "release-lineage"
    assert task.deliverable_contract.pinned_source_deliverable_ids == [
        "deliverable-1",
        "deliverable-2",
    ]


def test_case_context_filters_releases_and_preserves_the_case_link(tmp_path):
    from evander_team_addon.models import TeamCase
    from evander_team_addon.services.domain.release_cancellation import cancel_release

    repo, service, _ = setup_service(tmp_path)

    def link(state):
        state.cases["case"] = TeamCase(
            id="case",
            team_id="team",
            project_id="project",
            title="Human request",
            task_ids=["task"],
        )
        state.tasks["task"].case_id = "case"
        state.release_packages["retired"] = state.release_packages["release-1"].model_copy(
            update={"id": "retired"}
        )
        cancel_release(
            state,
            release=state.release_packages["retired"],
            actor_id="operator:test",
            reason="retired fixture",
        )
        return state

    repo.update(link)
    candidates = service.candidates_for_case("case")
    assert [candidate["release_package_id"] for candidate in candidates] == ["release-1"]
    assert candidates[0]["contribution_titles"] == ["Build backend"]
    state, result = service.submit(
        preview=service.preview("release-1"), actor_id="operator:test", request_id="case"
    )
    assert state.tasks[result["task_id"]].case_id == "case"
    assert result["task_id"] in state.cases["case"].task_ids


def test_superseded_pinned_source_blocks_preparation_instead_of_swapping(tmp_path):
    from evander_team_addon.providers import _project_repo_task_dependency_git_sources_unchecked

    repo, service, managed = setup_service(tmp_path)
    state, result = service.submit(
        preview=service.preview("release-1"), actor_id="operator:test", request_id="pins"
    )
    task = state.tasks[result["task_id"]]
    state.deliverables["deliverable-1"].superseded_by_deliverable_id = "replacement"
    state.deliverables["replacement"] = state.deliverables["deliverable-1"].model_copy(
        update={"id": "replacement", "superseded_by_deliverable_id": None}
    )
    context = SimpleNamespace(admin_service=SimpleNamespace(managed_git_admin=managed))
    with pytest.raises(RuntimeError, match="pinned integration contributions changed"):
        _project_repo_task_dependency_git_sources_unchecked(
            context,
            state=state,
            task_id=task.id,
            managed_repo_id=state.projects["project"].git.managed_repo_id,
        )


def test_new_integration_work_resets_prior_no_progress_before_dispatch(tmp_path):
    from datetime import timedelta

    from evander_team_addon.models import utc_now
    from evander_team_addon.services.runtime_service import TeamRuntimeService

    repo, service, _ = setup_service(tmp_path)

    def previously_idle(state):
        runtime = state.agent_runtimes["backend"]
        runtime.no_progress_count = 3
        runtime.next_wake_at = utc_now() + timedelta(hours=1)
        return state

    repo.update(previously_idle)
    state, result = service.submit(
        preview=service.preview("release-1", owner_agent_id="backend"),
        actor_id="operator:test", request_id="fresh-work",
    )
    assert state.agent_runtimes["backend"].no_progress_count == 0
    assert state.agent_runtimes["backend"].next_wake_at is None
    state, leases = TeamRuntimeService(repo).lease_due_agents(agent_id="backend")
    assert len(leases) == 1
    assert result["task_id"] in state.dispatches[leases[0]["dispatch_id"]].task_ids
    assert not any(b.source_kind == "agent_execution_stall" for b in state.blockers.values())


def test_reintegration_keeps_existing_execution_stall_blocked(tmp_path):
    from evander_team_addon.models import TeamBlocker

    repo, service, _ = setup_service(tmp_path)

    def stalled(state):
        state.agent_runtimes["backend"].no_progress_count = 3
        state.blockers["stall"] = TeamBlocker(
            id="stall", team_id="team", project_id="project",
            source_kind="agent_execution_stall", source_id="backend",
            summary="Unresolved execution stall", owner_agent_id="lead",
        )
        return state

    repo.update(stalled)
    state, _ = service.submit(
        preview=service.preview("release-1", owner_agent_id="backend"),
        actor_id="operator:test", request_id="stalled-work",
    )
    assert state.agent_runtimes["backend"].no_progress_count == 3
    assert state.blockers["stall"].state == "open"
