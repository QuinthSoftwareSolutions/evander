import asyncio
from datetime import timedelta
from types import SimpleNamespace

import pytest

from evander.addons import AddonLoaderConfig
from evander.config import GatewayConfig
from evander.gateway import _MCP_REQUEST_CONTEXT, create_server
from evander.models import SessionManifest, utc_now
from evander.provider_runtime.adapters import AdminRuntimeProviderAdapter
from evander.runtime import build_runtime
from evander.session_auth import ensure_session_mcp_auth_token
from evander_team_addon.models import (
    AgentRuntime,
    AgentTask,
    PersistentAgent,
    Project,
    Team,
    TeamDispatch,
    TeamDispatchStatus,
    TeamRole,
)
from evander_team_addon.repository import TeamStateRepository
from evander_test_fixtures_addon.models import FixtureError, Settings, Worker
from evander_test_fixtures_addon.service import Service
from test_service import Client


def test_composed_runtime_uses_real_team_scope_worker_assignment_and_durable_notification(
    tmp_path, worker, monkeypatch
):
    config = GatewayConfig(
        runtime_dir=tmp_path / "control",
        execution_backend="container_worker_vm",
        container_worker_base_url="http://192.0.2.10:11888",
        addon_loader=AddonLoaderConfig(
            modules=["evander_team_addon", "evander_test_fixtures_addon"],
            package_dirs=[],
            enabled_addon_ids=["team-addon", "test-fixtures-addon"],
        ),
    )
    runtime = build_runtime(config)
    worker.clock[0] = utc_now().timestamp()
    session, proof = ensure_session_mcp_auth_token(
        SessionManifest(
            session_id="session-a",
            directory_path="root",
            vm_provider="container_worker_vm",
            expires_at=utc_now() + timedelta(hours=1),
            allowed_tools=[
                "test_fixtures_acquire",
                "test_fixtures_get",
                "test_fixtures_release",
                "test_fixtures_reset",
            ],
        )
    )
    runtime.session_repository.upsert_session(session)
    repo = TeamStateRepository(config.runtime_dir)

    def seed(state):
        state.teams["team-a"] = Team(id="team-a", name="Fixture team", manager_agent_id="agent-a")
        state.roles["developer"] = TeamRole(
            id="developer",
            team_id="team-a",
            project_id="project-a",
            name="Developer",
            description="Verify application with synthetic fixtures",
        )
        state.agents["agent-a"] = PersistentAgent(
            id="agent-a", team_id="team-a", role_id="developer", session_id="session-a"
        )
        state.projects["project-a"] = Project(
            id="project-a", team_id="team-a", name="Fixture project", lead_agent_id="agent-a"
        )
        state.tasks["task-a"] = AgentTask(
            id="task-a",
            owner_agent_id="agent-a",
            project_id="project-a",
            priority=1,
            title="Verify application",
            description="Blocked on a fixture",
            state="blocked",
            created_by_actor="system:test",
        )
        state.agent_runtimes["agent-a"] = AgentRuntime(
            agent_id="agent-a",
            active_dispatch_id="dispatch-a",
            wake_leased_until=utc_now() + timedelta(minutes=10),
        )
        state.dispatches["dispatch-a"] = TeamDispatch(
            id="dispatch-a",
            agent_id="agent-a",
            session_id="session-a",
            cause_kind="task",
            task_ids=["task-a"],
            project_ids=["project-a"],
            status="leased",
            leased_at=utc_now(),
            lease_expires_at=utc_now() + timedelta(minutes=10),
        )
        return state

    repo.update(seed, expected_revision=repo.read().revision)
    adapter = AdminRuntimeProviderAdapter(SimpleNamespace(runtime=runtime))
    service = Service(
        config.runtime_dir,
        adapter,
        client_factory=lambda _: Client(worker),
        now=lambda: worker.clock[0],
    )
    settings = Settings(
        allocations_enabled=True,
        allowed_team_ids=["team-a"],
        workers={
            "worker-a": Worker(
                worker_base_url="http://192.0.2.10:11888",
                fixture_base_url="http://192.0.2.10:11991",
                token_env="FIXTURE_TOKEN",
            )
        },
    )
    service.store.save_settings("root", 0, settings, "test")
    monkeypatch.setattr("evander_test_fixtures_addon.mcp.Service", lambda *_: service)
    server = create_server(runtime)
    request = SimpleNamespace(
        headers={"x-evander-mcp-session-token": proof}, query_params={"session_id": "session-a"}
    )
    token = _MCP_REQUEST_CONTEXT.set(request)
    try:
        value = asyncio.run(
            server._tool_manager.call_tool(
                "test_fixtures_acquire",
                {
                    "task_id": "task-a",
                    "profile": "sqlserver-rabbitmq",
                    "request_id": "real-scope",
                },
                context=SimpleNamespace(request_context=SimpleNamespace(request=request)),
            )
        )
        lease = value[1] if isinstance(value, tuple) else value
        if set(lease) == {"result"}:
            lease = lease["result"]
    finally:
        _MCP_REQUEST_CONTEXT.reset(token)
    assert lease["scope"]["worker_id"] == "worker-a"
    ready = service.reconcile(lease["lease_id"])
    assert ready["state"] == "ready"
    assert service.store.get(lease["lease_id"])["notified"] == "ready"
    assert repo.read().tasks["task-a"].state.value == "blocked"
    before = repo.read().revision
    service.reconcile(lease["lease_id"])
    assert repo.read().revision == before
    service.queue("session-a", lease["lease_id"], "reset")

    def finish_dispatch(state):
        state.dispatches["dispatch-a"].status = TeamDispatchStatus.COMPLETED
        state.dispatches["dispatch-a"].completed_at = utc_now()
        state.agent_runtimes["agent-a"].active_dispatch_id = None
        state.agent_runtimes["agent-a"].wake_leased_until = None
        return state

    repo.update(finish_dispatch, expected_revision=repo.read().revision)
    worker.test_jobs[0].status = "exited"
    inbox_before = set(repo.read().inbox)
    waiting = service.reconcile(lease["lease_id"])
    assert waiting["state"] == "awaiting_job" and waiting["operation"]["kind"] == "reset"
    state = repo.read()
    new_items = [item for key, item in state.inbox.items() if key not in inbox_before]
    assert len(new_items) == 1
    item = new_items[0]
    assert item.recipient_agent_id == "agent-a" and item.payload["project_id"] == "project-a"
    assert item.task_id == "task-a" and item.item_type.value == "operator_directive"
    assert "reset is pending" in item.payload["message"]
    assert state.tasks["task-a"].state.value == "blocked"
    before = state.revision
    service.reconcile(lease["lease_id"])
    assert repo.read().revision == before

    # Restore a live dispatch before checking that worker-origin mismatches fail.
    def resume_dispatch(state):
        state.dispatches["dispatch-b"] = state.dispatches["dispatch-a"].model_copy(
            update=dict(id="dispatch-b", status=TeamDispatchStatus.LEASED, completed_at=None)
        )
        state.agent_runtimes["agent-a"].active_dispatch_id = "dispatch-b"
        state.agent_runtimes["agent-a"].wake_leased_until = utc_now() + timedelta(minutes=10)
        return state

    repo.update(resume_dispatch, expected_revision=repo.read().revision)
    settings.workers["worker-a"].worker_base_url = "http://192.0.2.11:11888"
    service.store.save_settings("root", 1, settings, "test")
    with pytest.raises(FixtureError, match="Actual session worker"):
        service.acquire("session-a", "task-a", "sqlserver-rabbitmq", "wrong-route")
