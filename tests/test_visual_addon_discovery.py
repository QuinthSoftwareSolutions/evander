from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
import json
from types import SimpleNamespace

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from evander.addons import AddonLoaderConfig, AddonMcpTool, AddonRegistration
from evander.admin.composition import build_admin_runtime
from evander.config import GatewayConfig
from evander.directories import DirectoryService
from evander.directory_config import DirectoryConfigStore
from evander.gateway import _MCP_REQUEST_CONTEXT, create_server
from evander.models import SessionManifest
from evander.policy import PolicyError
from evander.runtime import build_runtime
from evander.session_auth import ensure_session_mcp_auth_token
from evander_figma_addon.models import Settings
from evander_figma_addon.state import Store as FigmaStore
from evander_miro_addon.models import Connection, OPERATIONS
from evander_miro_addon.state import MiroStore
from config_helpers import write_runtime_config


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    context = build_runtime(
        GatewayConfig(
            runtime_dir=tmp_path,
            addon_loader=AddonLoaderConfig(
                modules=["evander_miro_addon", "evander_figma_addon"], package_dirs=[]
            ),
        )
    )
    directories = DirectoryService(context.directories)
    directories.create(parent="root", name="project")
    directories.create(parent="root", name="sibling")
    directories.create(parent="root/project", name="child")

    def no_credentials(*args, **kwargs):
        pytest.fail("Discovery must not read credentials or contact a provider")

    monkeypatch.setattr(MiroStore, "credentials", no_credentials)
    monkeypatch.setattr("evander_miro_addon.client.MiroClient.open", no_credentials)
    monkeypatch.setattr("evander_figma_addon.auth.OAuth.status", no_credentials)
    monkeypatch.setattr("evander_figma_addon.client.FigmaClient._mcp", no_credentials)
    return context


def add_session(runtime, tools, **changes):
    session = SessionManifest.model_validate(
        {
            "session_id": "agent",
            "directory_path": "root/project",
            "allowed_tools": tools,
            "expires_at": datetime.now(UTC) + timedelta(hours=1),
            **changes,
        }
    )
    runtime.session_repository.upsert_session(session)
    return session


def miro_connection(runtime, id="studio", directory="root", **changes):
    store = MiroStore(runtime.config.runtime_dir)
    return store.save_connection(
        Connection.model_validate(
            {
                "id": id,
                "name": f"{directory} {id}",
                "directory_path": directory,
                "redirect_uri": "https://evander.test/api/addons/miro-addon/oauth/callback",
                **changes,
            }
        ),
        0,
    )


def call(server, tool, **args):
    request = _MCP_REQUEST_CONTEXT.get()
    context = SimpleNamespace(request_context=SimpleNamespace(request=request)) if request else None
    arguments = args if request else {"session_id": "agent", **args}
    return asyncio.run(
        asyncio.wait_for(server._tool_manager.call_tool(tool, arguments, context=context), 10)
    )


def deny(runtime, tool, **rule):
    DirectoryConfigStore(runtime.directories, runtime_dir=runtime.config.runtime_dir).write(
        "root", domain="policy", payload={"mcp_tool_policies": {tool: rule}}
    )


@pytest.mark.parametrize("source", ["miro_" + operation for operation in OPERATIONS])
def test_every_existing_miro_tool_grant_enables_connection_discovery(runtime, source):
    session = add_session(runtime, [source])
    miro_connection(runtime, id="example-miro", allow_create_boards=True)
    server = create_server(runtime)
    assert "miro_list_connections" not in session.allowed_tools
    visibility = runtime.policy.tool_visibility_for_session(session, "miro_list_connections")
    assert visibility.visible and visibility.available
    assert visibility.source == "session.allowed_tools.addon_discovery"
    assert call(server, "miro_list_connections") == {
        "connections": [
            {
                "connection_id": "example-miro",
                "name": "root example-miro",
                "directory_path": "root",
                "inherited": True,
                "allow_create_boards": True,
                "approved_board_count": 0,
                "writable_board_count": 0,
            }
        ]
    }
    # Discovery does not imply permission to call other Miro operations.
    other = "miro_read_canvas" if source != "miro_read_canvas" else "miro_create_board"
    assert not runtime.policy.tool_visibility_for_session(session, other).available


def test_miro_discovery_uses_effective_directory_scope_and_public_fields(runtime):
    add_session(runtime, ["miro_list_boards"])
    miro_connection(runtime, "inherited")
    miro_connection(runtime, "shadowed", allow_create_boards=True)
    child = miro_connection(
        runtime,
        "shadowed",
        "root/project",
        boards=[
            {"board_id": "board1", "writable": True},
            {"board_id": "board2"},
        ],
    )
    miro_connection(runtime, "disabled", enabled=False)
    miro_connection(runtime, "disabled-child")
    miro_connection(runtime, "disabled-child", "root/project", enabled=False)
    miro_connection(runtime, "sibling-private", "root/sibling")
    miro_connection(runtime, "child-private", "root/project/child")
    store = MiroStore(runtime.config.runtime_dir)
    store.save_credentials(child, {"access_token": "secret-marker"})
    server = create_server(runtime)
    rows = call(server, "miro_list_connections")["connections"]
    assert [row["connection_id"] for row in rows] == ["inherited", "shadowed"]
    assert rows[1] == {
        "connection_id": "shadowed",
        "name": "root/project shadowed",
        "directory_path": "root/project",
        "inherited": False,
        "allow_create_boards": False,
        "approved_board_count": 2,
        "writable_board_count": 1,
    }
    assert "secret-marker" not in json.dumps(rows)
    assert "redirect_uri" not in json.dumps(rows)
    assert (
        call(server, "miro_list_boards", connection_id="shadowed")["boards"][0]["board_id"]
        == "board1"
    )
    with pytest.raises(ToolError, match="disabled"):
        call(server, "miro_list_boards", connection_id="disabled-child")
    store.save_connection(
        Connection.model_validate(child["config"]).model_copy(update={"enabled": False}),
        child["revision"],
    )
    assert [r["connection_id"] for r in call(server, "miro_list_connections")["connections"]] == [
        "inherited"
    ]
    assert store.usage(child) == 0


@pytest.mark.parametrize("source", ["figma_query", "figma_execute"])
def test_figma_discovery_uses_existing_permissions_and_exact_file_scope(runtime, source):
    session = add_session(runtime, [source])
    settings = Settings.model_validate(
        {
            "connections": {
                "studio": {
                    "client_id": "host-client",
                    "client_secret_env": "HOST_SECRET",
                    "redirect_uri": "https://evander.test/api/addons/figma-addon/oauth/callback",
                }
            },
            "targets": {
                "mockups": {
                    "name": "Mockups",
                    "connection_id": "studio",
                    "file_key": "testFile123",
                    "access": ["read", "edit"],
                    "edit_roots": ["1:2"],
                },
                "disabled": {
                    "name": "Disabled",
                    "connection_id": "studio",
                    "file_key": "testFile456",
                    "access": [],
                },
            },
        }
    )
    store = FigmaStore(runtime.config.runtime_dir)
    store.save_settings("root/project", 0, settings)
    store.save_settings("root", 0, settings)
    server = create_server(runtime)
    assert "figma_list_targets" not in session.allowed_tools
    rows = call(server, "figma_list_targets")["targets"]
    assert rows == [
        {
            "target_id": "mockups",
            "name": "Mockups",
            "file_url": "https://www.figma.com/design/testFile123",
            "access": ["read", "edit"],
            "edit_roots": ["1:2"],
        }
    ]
    assert "HOST_SECRET" not in json.dumps(rows)
    for directory in ("root/project/child", "root/sibling"):
        add_session(runtime, [source], directory_path=directory)
        assert call(server, "figma_list_targets") == {"targets": []}


@pytest.mark.parametrize(
    "tool,source",
    [("miro_list_connections", "miro_list_boards"), ("figma_list_targets", "figma_execute")],
)
def test_discovery_visibility_revocation_transport_identity_and_expiry(runtime, tool, source):
    session, proof = ensure_session_mcp_auth_token(add_session(runtime, [source]))
    runtime.session_repository.upsert_session(session)
    server = create_server(runtime)
    token = _MCP_REQUEST_CONTEXT.set(
        SimpleNamespace(
            headers={"x-evander-mcp-session-token": proof},
            query_params={"session_id": "agent"},
        )
    )
    try:
        assert tool in {entry.name for entry in server._tool_manager.list_tools()}
        call(server, tool)
        with pytest.raises(ToolError, match="bound|match"):
            call(server, tool, session_id="someone-else")
        runtime.session_repository.upsert_session(session.model_copy(update={"allowed_tools": []}))
        assert tool not in {entry.name for entry in server._tool_manager.list_tools()}
        with pytest.raises(ToolError, match="not allowed"):
            call(server, tool)
        runtime.session_repository.upsert_session(
            session.model_copy(update={"expires_at": datetime.now(UTC) - timedelta(seconds=1)})
        )
        with pytest.raises(ToolError, match="Expired"):
            call(server, tool)
    finally:
        _MCP_REQUEST_CONTEXT.reset(token)


@pytest.mark.parametrize(
    "tool,source",
    [("miro_list_connections", "miro_read_canvas"), ("figma_list_targets", "figma_query")],
)
def test_discovery_respects_source_denials_own_denials_and_approval(runtime, tool, source):
    session = add_session(runtime, [source])
    server = create_server(runtime)
    deny(runtime, source, denied=True)
    assert not runtime.policy.tool_visibility_for_session(session, tool).visible
    with pytest.raises(ToolError, match="not allowed"):
        call(server, tool)
    deny(runtime, tool, denied=True)
    with pytest.raises(ToolError, match="denied by directory"):
        call(server, tool)
    deny(runtime, tool, require_approval=True)
    assert runtime.policy.tool_visibility_for_session(session, tool).requires_approval
    with pytest.raises(ToolError, match="requires approval"):
        call(server, tool)


@pytest.mark.parametrize(
    "tool,other",
    [("miro_list_connections", "miro_list_boards"), ("figma_list_targets", "figma_query")],
)
def test_discovery_can_be_granted_explicitly_without_granting_other_tools(runtime, tool, other):
    session = add_session(runtime, [tool])
    server = create_server(runtime)
    call(server, tool)
    assert not runtime.policy.tool_visibility_for_session(session, other).available
    runtime.policy.set_extra_mcp_tool_names([])
    # Removing add-on registration removes its implied permissions too.
    add_session(runtime, [other])
    assert not runtime.policy.tool_visibility_for_session(
        runtime.policy.get_session("agent"), tool
    ).available


@pytest.mark.parametrize(
    "tool,source",
    [("miro_list_connections", "miro_list_boards"), ("figma_list_targets", "figma_query")],
)
def test_implicit_discovery_does_not_consume_workflow_lease(runtime, monkeypatch, tool, source):
    add_session(runtime, [source])
    create_server(runtime)

    def no_lease(*args, **kwargs):
        pytest.fail("Implicit session discovery must not check or consume a workflow lease")

    monkeypatch.setattr(runtime.policy.tool_approval_leases, "is_tool_approved", no_lease)
    monkeypatch.setattr(runtime.policy.tool_approval_leases, "consume_tool_use", no_lease)
    arguments = {
        "session_id": "agent",
        "tool_name": tool,
        "workflow_run_id": "run",
        "mcp_tool_approval_lease_id": "unrelated-lease",
    }
    assert not runtime.policy.consume_tool_approval_lease_use(**arguments)
    deny(runtime, tool, denied=True)
    with pytest.raises(PolicyError, match="denied by directory"):
        runtime.policy.consume_tool_approval_lease_use(**arguments)


def test_admin_runtime_and_gateway_share_discovery_permissions(tmp_path):
    components = build_admin_runtime(
        write_runtime_config(
            tmp_path,
            addon_loader={"modules": ["evander_miro_addon", "evander_figma_addon"]},
        )
    )
    session = add_session(
        components.runtime, ["miro_read_canvas", "figma_query"], directory_path="root"
    )
    for tool in ("miro_list_connections", "figma_list_targets"):
        assert components.runtime.policy.tool_visibility_for_session(session, tool).available


@pytest.mark.parametrize("sources", [("outside_addon",), ("discover",)])
def test_discovery_declarations_cannot_reference_other_addons_or_themselves(sources):
    with pytest.raises(ValueError, match="own catalog"):
        AddonRegistration(
            addon_id="test-addon",
            display_name="Test",
            mcp_tool_catalog=[
                AddonMcpTool(
                    name="discover",
                    label="Discover",
                    description="Read metadata",
                    discovery_for=sources,
                )
            ],
        )
