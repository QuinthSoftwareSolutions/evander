from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest

from evander.models import utc_now
from evander_team_addon.models import (
    AgentRuntime,
    AgentStatus,
    TeamTaskState,
    task_payload_with_derived,
)
from evander_team_addon.repository import TeamStateRepository
from evander_team_addon.services.domain.dependency_composition_recovery import (
    accepted_recovery_for_target,
)
from evander_team_addon.services.domain.preparation_failures import task_preparation_generation
from evander_team_addon.services.runtime_service import TeamRuntimeService
from team_repository_support import (
    _command_service,
    _lease_agent_dispatch,
    _register_backend_peer,
    _seed_hierarchy,
    _seed_task,
    _team_agent_action_service,
)


@pytest.fixture
def role_repo(tmp_path):
    repo = _seed_hierarchy(tmp_path)
    _register_backend_peer(repo, reports_to_agent_id="lead")
    _lease_agent_dispatch(repo, agent_id="lead")
    return repo


def create_role_task(repo, task_id, **fields):
    return _command_service(repo).create_task_for_role_command(
        session_id="session-lead",
        task_id=task_id,
        role_id="project__backend",
        project_id="project",
        priority=1,
        title=task_id,
        description="Role queue work",
        request_id=f"create-{task_id}",
        **fields,
    )


def lease(repo, **fields):
    return TeamRuntimeService(repo).lease_due_agents(
        project_id="project",
        limit=2,
        defer_activation=True,
        **fields,
    )


@pytest.fixture
def recovered_role_repo(role_repo):
    from evander_team_addon.models import (
        Deliverable,
        DeliverableGitRefs,
        DependencyCompositionRecovery,
        ProjectGitConfig,
    )

    _seed_task(role_repo, task_id="source")
    create_role_task(role_repo, "recovered", dependency_task_ids=["source"])

    def seed(state):
        state.projects["project"].git = ProjectGitConfig(
            managed_repo_id="mr_0123456789abcdefabcd", base_branch="main",
        )
        state.tasks["source"].state = TeamTaskState.FINISHED
        state.tasks["source"].deliverable_ids = ["source-deliverable"]
        state.deliverables["source-deliverable"] = Deliverable(
            id="source-deliverable", task_id="source", producer_agent_id="backend",
            state="accepted", summary="Source contribution",
            git_refs=DeliverableGitRefs(agent_branch="agents/source", commit_sha="b" * 40),
        )
        state.tasks["recovered"].git_base_branch = "main"
        state.tasks["recovered"].git_base_commit_sha = "c" * 40
        state.dependency_composition_recoveries["recovery"] = DependencyCompositionRecovery(
            id="recovery", blocker_id="resolved-blocker", target_task_id="recovered",
            project_id="project", managed_repo_id="mr_0123456789abcdefabcd",
            directory_path="root", base_branch="main", base_commit_sha="a" * 40,
            base_tree_hash="d" * 40, source_fingerprint="f" * 64,
            preparation_generation=task_preparation_generation(state, "recovered"),
            dependency_git_sources=[{
                "dependency_task_id": "source", "deliverable_id": "source-deliverable",
                "branch": "agents/source", "commit_sha": "b" * 40,
            }],
            failed_dependency_task_id="source", failed_deliverable_id="source-deliverable",
            recovery_task_id="resolver-task", verified_deliverable_id="reviewed-merge",
            verified_commit_sha="c" * 40, verified_at=utc_now(),
            accepted_deliverable_id="reviewed-merge", resolved_commit_sha="c" * 40,
            resolved_at=utc_now(), state="accepted",
        )

    role_repo.update(seed)
    return role_repo


def current_recovery(state):
    recovery = state.dependency_composition_recoveries["recovery"]
    return accepted_recovery_for_target(
        state, target_task_id="recovered",
        dependency_git_sources=recovery.dependency_git_sources,
    )


def test_checkout_retry_upgrade_preserves_accepted_recovery(recovered_role_repo, monkeypatch):
    from evander_team_addon.services.domain import preparation_failures

    state = recovered_role_repo.read()
    original = current_recovery(state).model_dump(mode="json")
    git_generation = preparation_failures.task_git_work_context_generation(state, "recovered")
    prior_failure = preparation_failures.task_preparation_failure_generation(
        state, "recovered", reason_code="dependency_git_preparation_failed",
    )
    monkeypatch.setattr(
        preparation_failures, "TASK_GIT_PREPARATION_RETRY_VERSION",
        preparation_failures.TASK_GIT_PREPARATION_RETRY_VERSION + 1,
    )
    assert preparation_failures.task_preparation_failure_generation(
        state, "recovered", reason_code="dependency_git_preparation_failed",
    ) != prior_failure
    assert preparation_failures.task_git_work_context_generation(state, "recovered") == git_generation
    assert current_recovery(state).model_dump(mode="json") == original


def test_role_queue_keeps_accepted_recovery_across_release_and_peer_claim(recovered_role_repo):
    repo = recovered_role_repo
    original = current_recovery(repo.read()).model_dump()
    state, leases = lease(repo, agent_id="backend")
    assert state.tasks["recovered"].owner_agent_id == "backend"
    assert current_recovery(state) is not None
    state = TeamRuntimeService(repo).fail_agent_dispatch(
        agent_id="backend", dispatch_id=leases[0]["dispatch_id"],
        error="Worker unavailable before delivery", delivery_not_started=True,
    )
    assert state.tasks["recovered"].owner_agent_id is None
    assert current_recovery(state).model_dump() == original

    reopened = TeamStateRepository(repo.runtime_dir)
    state, leases = lease(reopened, agent_id="backend-peer", respect_wake_interval=False)
    assert state.tasks["recovered"].owner_agent_id == "backend-peer"
    assert current_recovery(state) is not None
    state, _ = TeamRuntimeService(reopened).activate_delivered_agent_dispatch(
        dispatch_id=leases[0]["dispatch_id"],
    )
    assert state.tasks["recovered"].assignment_started_at is not None
    assert current_recovery(state).model_dump(exclude={"preparation_generation"}) == {
        key: value for key, value in original.items() if key != "preparation_generation"
    }


def test_role_queue_restores_recovery_when_tentative_claim_is_discarded(recovered_role_repo):
    original = current_recovery(recovered_role_repo.read()).model_dump()
    checks = 0

    def becomes_busy(session_id):
        nonlocal checks
        assert session_id == "session-backend"
        checks += 1
        return checks > 1

    state, leases = lease(
        recovered_role_repo, agent_id="backend", session_is_busy=becomes_busy,
    )
    assert checks >= 2  # Available during selection, busy before dispatch reservation.
    assert not leases
    assert state.tasks["recovered"].owner_agent_id is None
    assert current_recovery(state).model_dump() == original


@pytest.mark.parametrize("changed", ["task", "dependency", "project_git"])
def test_role_queue_does_not_revive_stale_accepted_recovery(recovered_role_repo, changed):
    original = current_recovery(recovered_role_repo.read()).model_dump()

    def change_input(state):
        if changed == "task":
            state.tasks["recovered"].description = "Changed requirements"
        elif changed == "dependency":
            state.deliverables["source-deliverable"].git_refs.commit_sha = "e" * 40
        else:
            state.projects["project"].git.base_branch = "release"

    recovered_role_repo.update(change_input)
    state, leases = lease(recovered_role_repo, agent_id="backend")
    assert leases
    assert state.tasks["recovered"].owner_agent_id == "backend"
    assert current_recovery(state) is None
    assert state.dependency_composition_recoveries["recovery"].model_dump() == original


def test_explicit_reassignment_still_invalidates_accepted_recovery(recovered_role_repo):
    original = current_recovery(recovered_role_repo.read()).model_dump()
    state, result = _command_service(recovered_role_repo).update_task_command(
        session_id="session-lead", task_id="recovered", owner_agent_id="backend-peer",
        request_id="manual-owner",
    )
    assert result["success"], result
    assert state.tasks["recovered"].assignment_mode == "agent"
    assert current_recovery(state) is None
    assert state.dependency_composition_recoveries["recovery"].model_dump() == original


@pytest.mark.parametrize(
    "changed",
    ["task", "dependency", "owner", "project_git", "context_base", "context_sources", "context_id"],
)
def test_retained_recovery_context_does_not_bypass_changed_provenance(recovered_role_repo, changed):
    from evander_team_addon.models import GitWorkContext, GitWorkContextState
    from evander_team_addon.services.domain.preparation_failures import (
        task_git_work_context_generation,
    )

    state, _ = lease(recovered_role_repo, agent_id="backend")
    recovery = current_recovery(state)
    context = GitWorkContext(
        id="retained", task_id="recovered", agent_id="backend",
        agent_branch="agents/backend/recovered", base_branch="main",
        base_commit=recovery.resolved_commit_sha,
        dependency_composition_recovery_id=recovery.id,
        dependency_git_sources=recovery.dependency_git_sources,
        preparation_generation=task_git_work_context_generation(state, "recovered"),
        state=GitWorkContextState.ABANDONED,
    )
    state.git_work_contexts[context.id] = context
    state.tasks["recovered"].state = TeamTaskState.IN_PROGRESS
    assert recovery.preparation_generation != task_preparation_generation(state, "recovered")
    assert current_recovery(state) is recovery
    if changed == "task":
        state.tasks["recovered"].description = "Changed requirements"
    elif changed == "dependency":
        state.deliverables["source-deliverable"].git_refs.commit_sha = "e" * 40
    elif changed == "owner":
        state.tasks["recovered"].owner_agent_id = "backend-peer"
    elif changed == "project_git":
        state.projects["project"].git.base_branch = "release"
    elif changed == "context_base":
        context.base_commit = "d" * 40
    elif changed == "context_sources":
        context.dependency_git_sources = []
    else:
        context.dependency_composition_recovery_id = "unrelated-recovery"
    assert current_recovery(state) is None


def test_role_queue_roundtrip_and_dispatch_capacity(role_repo):
    for task_id in ["one", "two", "three"]:
        state, result = create_role_task(role_repo, task_id)
        assert result["success"] and result["assignment_outcome"] == "queued"
        assert result["selected_owner_agent_id"] is None
        assert state.tasks[task_id].owner_agent_id is None
    state = TeamStateRepository(role_repo.runtime_dir).read()
    assert all(state.tasks[t].assignment_mode == "role" for t in ["one", "two", "three"])
    state, leases = lease(role_repo)
    assert len(leases) == 2
    assert {entry["agent"]["id"] for entry in leases} == {"backend", "backend-peer"}
    assert [entry["dispatch"]["task_ids"] for entry in leases] == [["one"], ["two"]]
    assert state.tasks["three"].owner_agent_id is None
    assert state.tasks["one"].assignment_started_at is None
    assert all(entry["role_task_allocations"][0]["candidate_scores"] for entry in leases)
    _, retry = create_role_task(role_repo, "one")
    assert retry["assignment_outcome"] == "queued"  # Replay original result, no new claim.
    _, repeated_leases = lease(role_repo)
    assert repeated_leases == []


def test_role_queue_claims_follow_dependency_readiness(role_repo):
    _seed_task(role_repo, task_id="prerequisite")
    create_role_task(role_repo, "dependent", dependency_task_ids=["prerequisite"])
    create_role_task(role_repo, "independent")
    state, leases = lease(role_repo)
    assert state.tasks["dependent"].owner_agent_id is None
    assert state.tasks["independent"].owner_agent_id == "backend-peer"
    assert state.tasks["prerequisite"].owner_agent_id == "backend"
    assert (
        task_payload_with_derived(state, state.tasks["dependent"])["assignment_wait_reason"]
        == "waiting_for_dependencies"
    )


@pytest.mark.parametrize("unavailable", ["busy", "paused", "standdown", "budget", "allowance"])
def test_role_queue_retains_work_when_capacity_unavailable(role_repo, unavailable):
    from evander_team_addon.models import AgentStandDown

    if unavailable in {"paused", "standdown", "allowance"}:

        def mutate(state):
            for aid in ["backend", "backend-peer"]:
                if unavailable == "paused":
                    state.agents[aid].status = AgentStatus.PAUSED
                elif unavailable == "standdown":
                    state.stand_downs[aid] = AgentStandDown(
                        id=aid, agent_id=aid, scope="agent", reason="test", set_by="operator"
                    )
                else:
                    state.agent_runtimes.setdefault(
                        aid, AgentRuntime(agent_id=aid)
                    ).no_progress_count = 3

        role_repo.update(mutate)
    state, result = create_role_task(role_repo, "queued")
    assert result["success"], result
    kwargs = {}
    if unavailable == "busy":
        kwargs["session_is_busy"] = lambda _sid: True
    if unavailable == "budget":
        kwargs["blocked_team_ids"] = {"team"}
    state, leases = lease(role_repo, **kwargs)
    assert state.tasks["queued"].owner_agent_id is None
    assert not [entry for entry in leases if "queued" in entry["dispatch"]["task_ids"]]


def test_role_queue_concurrent_claims_are_unique(role_repo):
    for task_id in ["one", "two", "three"]:
        create_role_task(role_repo, task_id)

    def run():
        return lease(TeamStateRepository(role_repo.runtime_dir))[1]

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: run(), range(2)))
    leases = [lease for result in results for lease in result]
    assert len(leases) == 2
    assert len({lease["agent"]["id"] for lease in leases}) == 2
    assert len({tid for lease in leases for tid in lease["dispatch"]["task_ids"]}) == 2


def test_role_queue_activation_retains_owner(role_repo):
    create_role_task(role_repo, "one")
    state, leases = lease(role_repo)
    dispatch = leases[0]["dispatch_id"]
    owner = state.tasks["one"].owner_agent_id
    service = TeamRuntimeService(role_repo)
    state, _ = service.activate_delivered_agent_dispatch(dispatch_id=dispatch)
    started_at = state.tasks["one"].assignment_started_at
    assert started_at is not None
    state, _ = service.activate_delivered_agent_dispatch(dispatch_id=dispatch)
    assert state.tasks["one"].assignment_started_at == started_at
    state, _ = lease(role_repo, now=utc_now() + timedelta(seconds=10))
    assert state.tasks["one"].owner_agent_id == owner


def test_role_queue_creator_can_edit_cancel_but_not_execute(role_repo):
    create_role_task(role_repo, "one")
    service = _command_service(role_repo)
    _, result = service.update_task_command(
        session_id="session-lead", task_id="one", title="Edited", request_id="edit"
    )
    assert result["success"], result
    _, result = service.update_task_command(
        session_id="session-lead", task_id="one", state="in_progress", request_id="start"
    )
    assert not result["success"]
    assert result["errors"][0]["code"] == "role_task_not_assigned"
    _, result = service.cancel_task_command(
        session_id="session-lead",
        task_id="one",
        reason="Cancelled",
        reason_code="superseded",
        request_id="cancel",
    )
    assert result["success"], result
    assert role_repo.read().tasks["one"].state == TeamTaskState.CANCELLED


@pytest.mark.parametrize("confirmed", [True, False])
def test_role_queue_failure_requeues_only_before_confirmed_delivery(role_repo, confirmed):
    create_role_task(role_repo, "one")
    state, leases = lease(role_repo)
    dispatch = leases[0]
    owner = state.tasks["one"].owner_agent_id
    state = TeamRuntimeService(role_repo).fail_agent_dispatch(
        agent_id=owner,
        dispatch_id=dispatch["dispatch_id"],
        error="preparation failure",
        delivery_not_started=confirmed,
    )
    assert state.tasks["one"].owner_agent_id == (None if confirmed else owner)
    if confirmed:
        events = role_repo.list_outbox_records(intent_type="team.role_queue.wake_requested")
        assert events


def test_role_queue_rechecks_current_delegation_authority(role_repo):
    create_role_task(role_repo, "one")

    def detach(state):
        for aid in ["backend", "backend-peer"]:
            state.agents[aid].role_id = "project__worker"
            for assignment in state.project_assignments.values():
                if assignment.agent_id == aid:
                    assignment.role_id = "project__worker"

    role_repo.update(detach)
    state, leases = lease(role_repo)
    assert state.tasks["one"].owner_agent_id is None
    assert not [entry for entry in leases if "one" in entry["dispatch"]["task_ids"]]
    assert (
        task_payload_with_derived(state, state.tasks["one"])["assignment_wait_reason"]
        == "waiting_for_eligible_role_holder"
    )


def test_role_queue_migration_preserves_legacy_ownership(role_repo):
    import sqlite3

    _seed_task(role_repo, task_id="legacy")
    before = role_repo.read().tasks["legacy"].model_dump(mode="json")
    with sqlite3.connect(role_repo.store.path) as connection:
        sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='team_tasks'"
        ).fetchone()[0]
        sql = sql.replace("team_tasks", "team_tasks_old", 1).replace(
            "owner_agent_id TEXT,", "owner_agent_id TEXT NOT NULL,"
        )
        sql = (
            sql.replace("    assignment_mode TEXT NOT NULL DEFAULT 'agent',\n", "")
            .replace("    assignment_role_id TEXT,\n", "")
            .replace("    assignment_started_at TEXT,\n", "")
        )
        columns = [
            r[1]
            for r in connection.execute("PRAGMA table_info(team_tasks)")
            if r[1] not in {"assignment_mode", "assignment_role_id", "assignment_started_at"}
        ]
        connection.execute(sql)
        fields = ",".join(columns)
        connection.execute(f"INSERT INTO team_tasks_old ({fields}) SELECT {fields} FROM team_tasks")
        connection.execute("DROP TABLE team_tasks")
        connection.execute("ALTER TABLE team_tasks_old RENAME TO team_tasks")
        connection.execute(
            "UPDATE team_state_metadata SET value='74' WHERE key='sqlite_schema_version'"
        )
    migrated = TeamStateRepository(role_repo.runtime_dir)
    assert migrated.read().tasks["legacy"].model_dump(mode="json") == before
    state, result = create_role_task(migrated, "new")
    assert result["success"]
    assert state.tasks["new"].owner_agent_id is None
    assert state.tasks["legacy"].owner_agent_id == "backend"


def test_role_queue_dispatch_finish_wakes_remaining_work(role_repo):
    for task_id in ["one", "two", "three"]:
        create_role_task(role_repo, task_id)
    state, leases = lease(role_repo)
    first = leases[0]
    runtime = TeamRuntimeService(role_repo)
    runtime.activate_delivered_agent_dispatch(dispatch_id=first["dispatch_id"])
    _, result = _command_service(role_repo).cancel_task_command(
        session_id=first["agent"]["session_id"],
        task_id="one",
        reason="No longer needed",
        reason_code="superseded",
        request_id="cancel-one",
    )
    assert result["success"], result
    state, finalized = runtime.finalize_agent_dispatch_from_activity(
        session_id=first["agent"]["session_id"],
        dispatch_id=first["dispatch_id"],
    )
    assert finalized["success"], finalized
    assert any(
        event["event_type"] == "team.role_queue.wake_requested" for event in finalized["events"]
    )
    state, next_leases = lease(role_repo, respect_wake_interval=False)
    assert state.tasks["one"].owner_agent_id == first["agent"]["id"]
    assert state.tasks["three"].owner_agent_id == first["agent"]["id"]
    assert [entry["dispatch"]["task_ids"] for entry in next_leases] == [["three"]]


def test_role_queue_dependency_edit_wakes_and_claims_newly_ready_work(role_repo):
    _seed_task(role_repo, task_id="prerequisite")
    create_role_task(role_repo, "dependent", dependency_task_ids=["prerequisite"])
    _, result = _command_service(role_repo).update_task_command(
        session_id="session-lead",
        task_id="dependent",
        dependency_task_ids=[],
        request_id="remove-dependency",
    )
    assert result["success"], result
    assert any(
        event["event_type"] == "team.role_queue.wake_requested" for event in result["events"]
    )
    state, _ = lease(role_repo)
    assert state.tasks["dependent"].owner_agent_id == "backend-peer"


def test_role_queue_explicit_reassignment_becomes_fixed_owner(role_repo):
    create_role_task(role_repo, "one")
    state, result = _command_service(role_repo).update_task_command(
        session_id="session-lead",
        task_id="one",
        owner_agent_id="backend-peer",
        request_id="manual-owner",
    )
    assert result["success"], result
    task = state.tasks["one"]
    assert task.owner_agent_id == "backend-peer"
    assert task.assignment_mode == "agent" and task.assignment_role_id is None
    assert None not in state.agent_runtimes


def test_role_queue_cannot_be_edited_by_unrelated_project_member(role_repo):
    create_role_task(role_repo, "one")
    _lease_agent_dispatch(role_repo, agent_id="worker", dispatch_id="worker-dispatch")
    state, result = _command_service(role_repo).update_task_command(
        session_id="session-worker",
        task_id="one",
        title="Hijacked",
        request_id="unauthorized-edit",
    )
    assert not result["success"]
    assert state.tasks["one"].title == "one"


def test_role_queue_does_not_substitute_different_roles(role_repo):
    create_role_task(role_repo, "one")
    state, leases = lease(
        role_repo, session_is_busy=lambda sid: sid in {"session-backend", "session-backend-peer"}
    )
    assert not leases
    assert state.tasks["one"].owner_agent_id is None
    assert state.agents["worker"].status == AgentStatus.ACTIVE


def test_role_queue_does_not_reassign_delivered_pending_task_on_failure(role_repo):
    create_role_task(role_repo, "one")
    state, leases = lease(role_repo)
    first = leases[0]
    runtime = TeamRuntimeService(role_repo)
    runtime.activate_delivered_agent_dispatch(dispatch_id=first["dispatch_id"])
    state = runtime.fail_agent_dispatch(
        agent_id=first["agent"]["id"],
        dispatch_id=first["dispatch_id"],
        error="late preparation report",
        delivery_not_started=True,
    )
    assert state.tasks["one"].owner_agent_id == first["agent"]["id"]
    assert state.tasks["one"].assignment_started_at is not None


def test_role_queue_availability_change_records_durable_wake(role_repo):
    from evander_team_addon.services.admin_service import TeamAdminService

    create_role_task(role_repo, "one")
    admin = TeamAdminService(role_repo)
    admin.update_agent(agent_id="backend", status=AgentStatus.PAUSED)
    before = {record.id for record in role_repo.list_outbox_records()}
    # Avoid coalescing this later availability event into the creation's debounce bucket.
    import unittest.mock

    with unittest.mock.patch(
        "evander_team_addon.services.admin_service.utc_now",
        return_value=utc_now() + timedelta(seconds=2),
    ):
        admin.update_agent(agent_id="backend", status=AgentStatus.ACTIVE)
    after = [record for record in role_repo.list_outbox_records() if record.id not in before]
    assert any(record.intent_type == "team.role_queue.wake_requested" for record in after)


def test_role_queue_creation_requires_pending_state(role_repo):
    state, result = create_role_task(role_repo, "one", state="in_progress")
    assert not result["success"]
    assert result["errors"][0]["code"] == "role_task_requires_pending_state"
    assert "one" not in state.tasks


def test_role_queue_manager_supervision_does_not_start_ownerless_work(role_repo):
    create_role_task(role_repo, "one")
    runtime = TeamRuntimeService(role_repo)
    _, finalized = runtime.finalize_agent_dispatch_from_activity(
        session_id="session-lead",
        dispatch_id="dispatch-command",
    )
    assert finalized["success"]
    state, leases = runtime.lease_due_agents(agent_id="manager", respect_wake_interval=False, defer_activation=True)
    assert len(leases) == 1
    assert leases[0]["reason"] == "manager_fallback"
    assert state.tasks["one"].owner_agent_id is None
    state, _ = runtime.activate_delivered_agent_dispatch(dispatch_id=leases[0]["dispatch_id"])
    assert state.tasks["one"].assignment_started_at is None
    assert state.tasks["one"].owner_agent_id is None


def test_role_queue_discards_assignment_when_dispatch_cannot_be_reserved(role_repo):
    create_role_task(role_repo, "one")

    def defer(state):
        for aid in ["backend", "backend-peer"]:
            state.agent_runtimes[aid] = AgentRuntime(
                agent_id=aid,
                next_wake_at=utc_now() + timedelta(hours=1),
            )

    role_repo.update(defer)
    state, leases = lease(role_repo)
    assert not leases
    assert state.tasks["one"].owner_agent_id is None
    assert not role_repo.list_outbox_records(intent_type="team.task.role_assigned")


def test_role_queue_ownerless_coordination_parent_child_can_be_accepted(role_repo):
    _, result = create_role_task(role_repo, "parent", task_kind="coordination")
    assert result["success"], result
    _, result = create_role_task(
        role_repo, "child", parent_task_id="parent", task_kind="support"
    )
    assert result["success"], result
    state, leases = lease(role_repo)
    assert state.tasks["parent"].owner_agent_id is None
    assert len(leases) == 1
    child_lease = leases[0]
    assert child_lease["dispatch"]["task_ids"] == ["child"]
    TeamRuntimeService(role_repo).activate_delivered_agent_dispatch(
        dispatch_id=child_lease["dispatch_id"]
    )

    state, submitted = _command_service(role_repo).submit_deliverable_command(
        session_id=child_lease["agent"]["session_id"],
        id="child-deliverable",
        task_id="child",
        summary="Child contribution ready for acceptance.",
        artifact_refs=["artifact:child"],
        request_id="submit-child",
    )
    assert submitted["success"], submitted
    assert state.tasks["child"].state == TeamTaskState.WAITING_FOR_REVIEW
    assert state.deliverables["child-deliverable"].acceptance_recipient_agent_id == "lead"
    assert any(
        state.inbox[item_id].recipient_agent_id == "lead"
        and state.inbox[item_id].payload.get("kind") == "superior_acceptance_requested"
        for item_id in submitted["created_inbox_item_ids"]
    )

    _lease_agent_dispatch(role_repo, agent_id="lead", dispatch_id="accept-child")
    state, accepted = _command_service(role_repo).decide_deliverable_command(
        session_id="session-lead",
        deliverable_id="child-deliverable",
        decision="accepted",
        reason="Contribution is complete.",
        request_id="accept-child",
    )
    assert accepted["success"], accepted
    assert state.tasks["child"].state == TeamTaskState.FINISHED
    assert state.tasks["parent"].owner_agent_id is None
    assert any(
        item.recipient_agent_id == "lead"
        and item.payload.get("parent_task_id") == "parent"
        and item.payload.get("transition") == "integration_ready"
        for item in state.inbox.values()
    )
    state, parent_leases = lease(role_repo)
    assert any("parent" in entry["dispatch"]["task_ids"] for entry in parent_leases)
    assert state.tasks["parent"].owner_agent_id is not None


def test_role_queue_uncertain_delivery_retains_owner_after_later_preparation_failure(role_repo):
    create_role_task(role_repo, "one")
    state, leases = lease(role_repo)
    first = leases[0]
    owner = state.tasks["one"].owner_agent_id
    TeamRuntimeService(role_repo).fail_agent_dispatch(
        agent_id=owner,
        dispatch_id=first["dispatch_id"],
        error="enqueue outcome unknown",
    )

    # Reopen the repository between attempts to exercise durable ownership evidence.
    reopened = TeamStateRepository(role_repo.runtime_dir)
    state, retries = lease(reopened, respect_wake_interval=False)
    retry = next(entry for entry in retries if "one" in entry["dispatch"]["task_ids"])
    assert retry["dispatch_id"] != first["dispatch_id"]
    assert retry["agent"]["id"] == owner
    state = TeamRuntimeService(reopened).fail_agent_dispatch(
        agent_id=owner,
        dispatch_id=retry["dispatch_id"],
        error="preparation failed before submission",
        delivery_not_started=True,
    )
    assert state.tasks["one"].assignment_started_at is None
    assert state.tasks["one"].owner_agent_id == owner
    state, peers = lease(reopened, agent_id="backend-peer", respect_wake_interval=False)
    assert not any("one" in entry["dispatch"]["task_ids"] for entry in peers)
    assert state.tasks["one"].owner_agent_id == owner


def test_role_queue_legacy_dispatch_without_claim_evidence_retains_owner(role_repo):
    import sqlite3

    create_role_task(role_repo, "one")
    state, leases = lease(role_repo)
    dispatch = leases[0]
    owner = state.tasks["one"].owner_agent_id
    with sqlite3.connect(role_repo.store.path) as connection:
        connection.execute("UPDATE team_state_metadata SET value='0' WHERE key IN ('sqlite_schema_version', 'state_schema_version')")
        connection.execute("ALTER TABLE team_dispatches DROP COLUMN claimed_role_task_ids_json")

    migrated = TeamStateRepository(role_repo.runtime_dir)
    state = TeamRuntimeService(migrated).fail_agent_dispatch(
        agent_id=owner,
        dispatch_id=dispatch["dispatch_id"],
        error="preparation failed without original claim evidence",
        delivery_not_started=True,
    )
    assert state.tasks["one"].owner_agent_id == owner


@pytest.mark.parametrize("respect_wake_interval", [True, False])
def test_role_queue_selects_candidate_that_can_pass_wake_interval(role_repo, respect_wake_interval):
    create_role_task(role_repo, "one")
    now = utc_now()

    def defer_first(state):
        state.agent_runtimes["backend"] = AgentRuntime(
            agent_id="backend", next_wake_at=now + timedelta(hours=1)
        )

    role_repo.update(defer_first)
    state, leases = lease(role_repo, now=now, respect_wake_interval=respect_wake_interval)
    assert len(leases) == 1
    expected_owner = "backend-peer" if respect_wake_interval else "backend"
    assert leases[0]["agent"]["id"] == expected_owner
    assert leases[0]["dispatch"]["task_ids"] == ["one"]
    assert state.tasks["one"].owner_agent_id == expected_owner


@pytest.mark.parametrize("parent_owner", [None, "backend"])
def test_reassigned_child_blocker_uses_parent_owner_or_new_owners_superior(role_repo, parent_owner):
    from evander_team_addon.models import TeamBlockerRoute, TeamInboxItemState
    from evander_team_addon.services.domain.blockers import open_or_get_blocker

    create_role_task(role_repo, "parent")
    _seed_task(role_repo, task_id="child", owner_agent_id="worker")

    def seed_blocker(state):
        state.tasks["parent"].owner_agent_id = parent_owner
        state.tasks["child"].parent_task_id = "parent"
        state.tasks["child"].state = TeamTaskState.BLOCKED
        open_or_get_blocker(
            state, source_kind="task", source_id="child", task_id="child",
            summary="Needs supervisor help", reporter_agent_id="worker",
            team_id="team", project_id="project", result={"events": []},
        )

    before = role_repo.update(seed_blocker)
    [blocker] = list(before.blockers.values())
    assert blocker.owner_agent_id == "backend"
    old_inbox_ids = [
        item.id for item in before.inbox.values()
        if item.payload.get("blocker_id") == blocker.id
        and item.state == TeamInboxItemState.PENDING
    ]
    assert old_inbox_ids
    state, result = _team_agent_action_service(role_repo).apply_agent_action(
        action={
            "agent_id": "lead",
            "task_updates": [{"task_id": "child", "owner_agent_id": "backend-peer"}],
        },
    )
    assert result["success"], result
    assert state.blockers[blocker.id].route == TeamBlockerRoute.OWNED
    assert state.blockers[blocker.id].owner_agent_id == (parent_owner or "lead")
    assert all(state.inbox[item_id].state == TeamInboxItemState.HANDLED for item_id in old_inbox_ids)
    [notification] = [
        item for item in state.inbox.values()
        if item.payload.get("blocker_id") == blocker.id
        and item.state == TeamInboxItemState.PENDING
    ]
    assert notification.recipient_agent_id == (parent_owner or "lead")
    assert notification.payload["kind"] == "blocker_owner_transferred"
