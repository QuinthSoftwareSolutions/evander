import asyncio
import os
from pathlib import Path
import subprocess
import sys


def test_standalone_registration_has_no_other_optional_provider_imports():
    project = Path(__file__).resolve().parents[1]
    repo = project.parents[1]
    script = """
import builtins
original = builtins.__import__
def guarded(name,*args,**kwargs):
    if name.startswith(('evander_team_addon','evander_test_fixtures_addon','evander_rabbitmq_addon')):
        raise AssertionError('Optional provider dependency: '+name)
    return original(name,*args,**kwargs)
builtins.__import__ = guarded
from evander_sqlserver_addon import register_addon
addon=register_addon()
assert addon.addon_id == 'sqlserver-addon'
assert addon.integration.required_addons == ()
assert addon.workflow_providers().effect_providers
"""
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(repo / "src"), str(project)])}
    result = subprocess.run(
        [sys.executable, "-c", script], env=env, capture_output=True, text=True, timeout=15
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_mcp_schema_exposes_bounded_arguments_and_mutation_annotations(tmp_path):
    from evander_sqlserver_addon import register_addon
    from evander.addons import AddonContext
    from mcp.server.mcpserver import MCPServer

    addon = register_addon()
    server = MCPServer("schema")
    addon.mcp_tools(server, AddonContext(addon.addon_id, tmp_path, tmp_path / addon.addon_id))
    tools = {tool.name: tool for tool in asyncio.run(server.list_tools())}
    assert set(tools) == {tool.name for tool in addon.mcp_tool_catalog}
    operation = tools["sqlserver_query"]
    assert operation.annotations.read_only_hint is True
    fields = operation.input_schema["$defs"]["Call"]["properties"]
    assert "host" not in fields and "credentials" not in fields
    assert operation.input_schema["$defs"]["Call"]["additionalProperties"] is False
    assert tools["sqlserver_inspect"].annotations.read_only_hint is True
    assert "request_id" in tools["sqlserver_reset"].input_schema["required"]
