import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from mcp.server.mcpserver import MCPServer
from zipfile import ZipFile

from evander.addons import AddonContext
from evander_browser_sessions_addon import register_addon
from evander_browser_sessions_addon.models import BrowserError, CreateRequest, RunRequest
from evander_browser_sessions_addon.providers import BrowserEffect, Definition
from evander_browser_sessions_addon.service import Bindings


def test_all_declared_mcp_tools_register_with_typed_contracts(tmp_path):
    addon = register_addon()
    server = MCPServer("browser-test")
    context = AddonContext(
        addon_id=addon.addon_id,
        runtime_dir=tmp_path,
        addon_runtime_dir=tmp_path / "addons" / addon.addon_id,
    )
    names = addon.mcp_tools(server, context)
    tools = asyncio.run(server.list_tools())
    assert (
        set(names)
        == {item.name for item in tools}
        == {item.name for item in addon.mcp_tool_catalog}
    )
    action = next(tool for tool in tools if tool.name == "browser_session_act")
    assert action.input_schema["properties"]["command"]["$ref"]
    serialized = json.dumps(action.input_schema)
    assert "observation_id" in serialized and "generation" in serialized
    assert "evaluate" not in serialized
    run = next(tool for tool in tools if tool.name == "browser_session_run")
    schema = run.input_schema["$defs"]["RunRequest"]["properties"]
    assert {
        "request_id",
        "generation",
        "goal",
        "values",
        "max_steps",
        "timeout_seconds",
    } <= schema.keys()
    assert all(schema[field].get("description") for field in schema)
    for example in schema["values"]["examples"]:
        request = RunRequest(
            request_id="schema-example", generation=0, goal="Check a filter", values=example
        )
        assert any(value.value == "" for value in request.values.values())
    status = next(tool for tool in tools if tool.name == "browser_session_status")
    assert status.input_schema["properties"]["include_guidance"]["default"] is False


def test_any_owning_agent_can_discover_packaged_guidance_without_a_pa_prompt(domain, monkeypatch):
    import re
    from mcp.server.mcpserver.exceptions import ToolError
    from evander_browser_sessions_addon import mcp

    service, bindings, _ = domain
    monkeypatch.setattr(mcp, "Service", lambda *args: service)
    monkeypatch.setattr(mcp, "_resolve_session_id_from_context", lambda *args, **kwargs: "agent-a")
    server = MCPServer("generic-browser-agent")
    mcp.register_mcp_tools(server, SimpleNamespace(runtime_dir=service.runtime_dir, service=None))

    def call(name, **payload):
        return asyncio.run(server._tool_manager.call_tool(name, payload, context=None))

    profiles = call("browser_session_profiles")
    created = call("browser_session_create", request={"mode": "user_hosted", "request_id": "share"})
    status = call("browser_session_status", browser_session_id=created["id"], include_guidance=True)
    assert profiles["agent_guidance"] == created["agent_guidance"] == status["agent_guidance"]
    guidance = status["agent_guidance"]
    assert guidance["instructions"]
    assert guidance["create_request_schema"]["required"] == ["mode", "request_id"]
    assert guidance["create_request_schema"]["properties"]["mode"]["enum"] == ["hosted", "user_hosted"]
    assert "hosted" in profiles["modes"]
    assert set(profiles["modes"]) <= {"hosted", "user_hosted"}
    assert all("playwright_enabled" in profile for profile in profiles["profiles"])
    # Published instructions must contain usable examples of the actual public contracts.
    creation, objective = re.findall(r"```json\n(.*?)\n```", guidance["instructions"], re.S)
    assert CreateRequest.model_validate_json(creation).mode == "hosted"
    request = RunRequest.model_validate_json(objective)
    assert any(value.value == "" for value in request.values.values())
    compact = call("browser_session_status", browser_session_id=created["id"])
    assert "agent_guidance" not in compact
    assert compact["last_activity_at"] == created["last_activity_at"]

    # Adding the guide must preserve ownership and tool-grant checks.
    monkeypatch.setattr(mcp, "_resolve_session_id_from_context", lambda *args, **kwargs: "agent-b")
    with pytest.raises(ToolError):
        call("browser_session_status", browser_session_id=created["id"], include_guidance=True)
    bindings.denied.add("agent-a")
    monkeypatch.setattr(mcp, "_resolve_session_id_from_context", lambda *args, **kwargs: "agent-a")
    with pytest.raises(ToolError):
        call("browser_session_status", browser_session_id=created["id"], include_guidance=True)


def test_workflow_cannot_launch_for_an_agent_in_another_directory(domain):
    service, _, _ = domain
    operation = object.__new__(BrowserEffect)
    operation.effect_definition = Definition(
        id="preview",
        display_name="Preview",
        type="effect:browser-session:ensure",
        agent_session_id="agent-a",
        request={},
    )
    operation.runtime_context = SimpleNamespace(
        resolved_config=SimpleNamespace(runtime_dir=service.runtime_dir),
        admin_service=None,
        metadata={"directory_path": "root/other"},
    )
    # This tests the public provider boundary rather than a hand-authored node example.
    from unittest.mock import patch

    with patch("evander_browser_sessions_addon.providers.Service", return_value=service):
        with pytest.raises(BrowserError, match="outside"):
            operation.execute()


@pytest.mark.parametrize("legacy_support", [None, "legacy-support-agent"])
def test_message_dispatch_uses_bound_owner_thread_and_channel_instructions(
    tmp_path, legacy_support
):
    calls = []
    admin = SimpleNamespace(
        enqueue_agent_prompt=lambda *args, **kwargs: (
            calls.append((args, kwargs)) or SimpleNamespace(success=True)
        )
    )
    bindings = Bindings(tmp_path, admin)
    session = SimpleNamespace(
        id="browser-a",
        agent_session_id="author-a",
        support_agent_session_id=legacy_support,
        native_thread_id="author-thread",
    )
    bindings.enqueue(
        session,
        {
            "id": "message-a",
            "channel": "viewer",
            "body": "Move this button",
            "annotation": '{"x":100,"y":50}',
        },
    )
    args, kwargs = calls[0]
    assert args[0] == "author-a"
    assert "browser_session_reply" in args[1] and "message-a" in args[1]
    assert "Point on page" in args[1]
    assert kwargs["resume_thread_id"] == "author-thread" and kwargs["resume_mode"] == "same_context"
    assert kwargs["dispatch_id"] == "browser:message-a"
    assert not any("teams" in str(value) or "discord" in str(value) for value in kwargs.values())


def test_built_extension_and_python_assets_are_complete():
    root = Path(__file__).resolve().parents[1]
    archive = root / "evander_browser_sessions_addon/ui_static/chrome-extension.zip"
    with ZipFile(archive) as bundle:
        manifest = json.loads(bundle.read("manifest.json"))
        assert manifest["manifest_version"] == 3 and manifest["minimum_chrome_version"] == "125"
        assert {"debugger", "tabCapture", "offscreen"} <= set(manifest["permissions"])
        assert {"microphone.html", "microphone.js", "capture.html", "capture.js"} <= set(
            bundle.namelist()
        )
        assert not manifest.get("externally_connectable")
        assert manifest["background"]["service_worker"] in bundle.namelist()
        assert manifest["side_panel"]["default_path"] in bundle.namelist()
    for name in ("ui_static/viewer.js", "ui_static/index.html", "controller/hosted.mjs"):
        assert (root / "evander_browser_sessions_addon" / name).stat().st_size > 100
