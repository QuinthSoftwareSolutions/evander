import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from mcp.server.fastmcp import FastMCP
from zipfile import ZipFile

from evander.addons import AddonContext
from evander_browser_sessions_addon import register_addon
from evander_browser_sessions_addon.models import BrowserError
from evander_browser_sessions_addon.providers import BrowserEffect, Definition
from evander_browser_sessions_addon.service import Bindings


def test_all_declared_mcp_tools_register_with_typed_contracts(tmp_path):
    addon = register_addon()
    server = FastMCP("browser-test")
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
    assert action.inputSchema["properties"]["command"]["$ref"]
    serialized = json.dumps(action.inputSchema)
    assert "observation_id" in serialized and "generation" in serialized
    assert "evaluate" not in serialized


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
def test_message_dispatch_uses_bound_owner_thread_and_channel_instructions(tmp_path, legacy_support):
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
        assert {"microphone.html", "microphone.js", "capture.html", "capture.js"} <= set(bundle.namelist())
        assert not manifest.get("externally_connectable")
        assert manifest["background"]["service_worker"] in bundle.namelist()
        assert manifest["side_panel"]["default_path"] in bundle.namelist()
    for name in ("ui_static/viewer.js", "ui_static/index.html", "controller/hosted.mjs"):
        assert (root / "evander_browser_sessions_addon" / name).stat().st_size > 100
