"""Protect scoped hydration, snapshot consistency and the MCP request lifetime."""

from contextlib import contextmanager

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from evander_team_addon import mcp_read_scope
from evander_team_addon.models import AgentStatus
from evander_team_addon.repository import TeamStateRepository
from evander.gateway import create_server

from test_team_addon_mcp_read_tools import (
    EXPECTED_READ_TOOL_NAMES,
    TEAM_GET_MY_IDENTITY_TOOL,
    TEAM_GET_TASK_TOOL,
    TEAM_LIST_TASKS_TOOL,
    _assert_read_success,
    _call_denied_or_not_found,
    _call_tool,
    _runtime,
    _seed_read_tool_state,
)


def test_registered_read_tools_do_not_hydrate_the_aggregate(tmp_path, monkeypatch):
    from test_team_addon_mcp import _lease_agent

    context = _runtime(tmp_path, allowed_tools=list(EXPECTED_READ_TOOL_NAMES))
    repo = _seed_read_tool_state(context.config.runtime_dir)
    _lease_agent(repo, agent_id="alpha-dev", dispatch_id="active-dispatch")
    server = create_server(context)

    def forbidden_read(self):
        raise AssertionError("read tool hydrated unrelated Team state/history")

    monkeypatch.setattr(TeamStateRepository, "read", forbidden_read)
    for name, extra in (
        (TEAM_GET_MY_IDENTITY_TOOL, {}),
        (TEAM_GET_TASK_TOOL, {"task_id": "alpha-task-a"}),
        (TEAM_LIST_TASKS_TOOL, {"limit": 2}),
    ):
        result = _call_tool(server, name, {"session_id": "session-alpha-dev", **extra})
        _assert_read_success(result)
        assert "attention_error" not in result


def test_keyed_reads_decode_only_the_requested_record_and_its_children(tmp_path, monkeypatch):
    repo = _seed_read_tool_state(tmp_path)
    decoded = []
    adapter = mcp_read_scope._MODELS["tasks"]

    class CountedAdapter:
        def validate_python(self, payload):
            decoded.append(payload["id"])
            return adapter.validate_python(payload)

    monkeypatch.setitem(mcp_read_scope._MODELS, "tasks", CountedAdapter())
    with mcp_read_scope.read_snapshot(repo.store) as snapshot:
        task = snapshot.tasks["alpha-task-a"]
        assert task.id == "alpha-task-a"
        assert snapshot.tasks.get("missing") is None
        assert snapshot.tasks["alpha-task-a"] is task
        assert decoded == ["alpha-task-a"]
        # Enumeration deliberately preserves the existing complete list semantics.
        assert "beta-task-a" in snapshot.tasks
        assert {row.id for row in snapshot.tasks.values()} >= {"alpha-task-a", "beta-task-a"}


def test_lazy_authorization_and_data_reads_share_one_committed_revision(tmp_path):
    repo = _seed_read_tool_state(tmp_path)
    with mcp_read_scope.read_snapshot(repo.store) as snapshot:
        revision = snapshot.revision
        assert snapshot.agents["alpha-dev"].status == AgentStatus.ACTIVE

        def withdraw_and_edit(state):
            state.agents["alpha-dev"].status = AgentStatus.PAUSED
            state.tasks["alpha-task-a"].title = "Changed after snapshot opened"

        repo.update(withdraw_and_edit)
        # This collection is first accessed after the concurrent commit.
        assert snapshot.tasks["alpha-task-a"].title != "Changed after snapshot opened"
        assert snapshot.revision == revision
    with mcp_read_scope.read_snapshot(repo.store) as current:
        assert current.revision > revision
        assert current.agents["alpha-dev"].status == AgentStatus.PAUSED
        assert current.tasks["alpha-task-a"].title == "Changed after snapshot opened"
    with pytest.raises(RuntimeError, match="closed"):
        snapshot.projects["alpha-project"]


def test_read_snapshot_cannot_be_written_as_a_complete_state(tmp_path):
    repo = _seed_read_tool_state(tmp_path)
    revision = repo.read_revision()
    with mcp_read_scope.read_snapshot(repo.store) as snapshot:
        with pytest.raises(ValueError, match="cannot be persisted"):
            repo.update(lambda state: snapshot)
    assert repo.read_revision() == revision


def test_tool_scope_closes_on_success_and_error_and_policy_denial_is_lazy(tmp_path, monkeypatch):
    context = _runtime(tmp_path, allowed_tools=[TEAM_GET_TASK_TOOL])
    _seed_read_tool_state(context.config.runtime_dir)
    server = create_server(context)
    lifetimes = []
    original = mcp_read_scope.read_snapshot

    @contextmanager
    def observed(store):
        with original(store) as snapshot:
            lifetimes.append("opened")
            try:
                yield snapshot
            finally:
                lifetimes.append("closed")

    monkeypatch.setattr(mcp_read_scope, "read_snapshot", observed)
    _assert_read_success(
        _call_tool(
            server,
            TEAM_GET_TASK_TOOL,
            {
                "session_id": "session-alpha-dev",
                "task_id": "alpha-task-a",
            },
        )
    )
    _call_denied_or_not_found(
        server,
        TEAM_GET_TASK_TOOL,
        {
            "session_id": "session-alpha-dev",
            "task_id": "beta-task-a",
        },
    )
    assert lifetimes == ["opened", "closed", "opened", "closed"]
    assert mcp_read_scope._SCOPE.get() is None
    with pytest.raises(ToolError):
        _call_tool(server, TEAM_LIST_TASKS_TOOL, {"session_id": "session-alpha-dev"})
    assert lifetimes == ["opened", "closed", "opened", "closed"]
