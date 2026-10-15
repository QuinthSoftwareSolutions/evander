from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from mcp.server.fastmcp.exceptions import ToolError

from evander.config import GatewayConfig
from evander.directory_config import DirectoryConfigStore
from evander.directories import DirectoryService
from evander.gateway import create_server
from evander.models import SessionManifest, ToolName
from evander.runtime import build_runtime
from gateway_tool_support import call_tool


def gateway(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, allowed_tools: list[ToolName]):
    context = build_runtime(
        GatewayConfig(
            runtime_dir=tmp_path,
            audit_log_path=tmp_path / "audit.jsonl",
            session_store_path=tmp_path / "sessions.json",
            azure_devops_organization_url="https://dev.azure.com/example",
            azure_devops_project="RootProject",
            azure_devops_pat_env="BUILD_ROOT_PAT",
        )
    )
    DirectoryService(context.directories).create(parent="root", name="team")
    DirectoryConfigStore(context.directories, runtime_dir=tmp_path).write(
        "root/team",
        domain="providers",
        payload={
            "azure_devops_project": "TeamProject",
            "azure_devops_pat_env": "BUILD_TEAM_PAT",
        },
    )
    context.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            directory_path="root/team",
            allowed_tools=allowed_tools,
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    monkeypatch.setenv("BUILD_ROOT_PAT", "wrong-project-token")
    monkeypatch.setenv("BUILD_TEAM_PAT", "team-secret-token")
    return create_server(context)


def install_transport(monkeypatch: pytest.MonkeyPatch, handler):
    original = httpx.Client
    requests = []

    def send(request):
        requests.append(request)
        return handler(request)

    monkeypatch.setattr(
        "evander_azure_devops_addon.builds.httpx.Client",
        lambda **kwargs: original(**kwargs, transport=httpx.MockTransport(send)),
    )
    return requests


def test_granted_build_reads_use_session_directory_and_bound_work_item_refs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server = gateway(
        tmp_path,
        monkeypatch,
        [ToolName.GET_ADO_BUILD, ToolName.LIST_ADO_BUILD_WORK_ITEMS],
    )

    def respond(request):
        assert request.url.path.startswith("/example/TeamProject/_apis/build/builds/42")
        assert request.url.params["api-version"] == "7.1"
        assert request.headers["authorization"] == "Basic OnRlYW0tc2VjcmV0LXRva2Vu"
        if request.url.path.endswith("/workitems"):
            assert request.url.params["$top"] == "3"
            return httpx.Response(
                200,
                json={"count": 3, "value": [{"id": "101"}, {"id": "102"}, {"id": "103"}]},
            )
        return httpx.Response(
            200,
            json={
                "id": 42,
                "buildNumber": "synthetic-build-42",
                "status": "completed",
                "result": "succeeded",
                "sourceVersion": "abc123",
                "sourceBranch": "refs/heads/release",
            },
        )

    requests = install_transport(monkeypatch, respond)
    build = call_tool(server, "get_ado_build", {"session_id": "session-1", "build_id": 42})
    refs = call_tool(
        server,
        "list_ado_build_work_items",
        {"session_id": "session-1", "build_id": 42, "limit": 2},
    )

    assert build["source_version"] == "abc123"
    assert build["build_number"] == "synthetic-build-42"
    assert refs == {
        "build_id": "42",
        "project": "TeamProject",
        "work_item_ids": ["101", "102"],
        "count": 2,
        "truncated": True,
        "same_project_only": True,
    }
    assert len(requests) == 2


@pytest.mark.parametrize(
    "tool,granted",
    [
        ("get_ado_build", [ToolName.LIST_ADO_BUILD_WORK_ITEMS]),
        ("list_ado_build_work_items", [ToolName.GET_ADO_BUILD]),
    ],
)
def test_build_tools_require_individual_grants_before_network_access(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tool: str, granted: list[ToolName]
) -> None:
    server = gateway(tmp_path, monkeypatch, granted)
    requests = install_transport(monkeypatch, lambda request: pytest.fail("unexpected ADO call"))

    with pytest.raises(ToolError, match="not allowed"):
        asyncio.run(server.call_tool(tool, {"session_id": "session-1", "build_id": 42}))

    assert requests == []


def test_build_tools_reject_invalid_id_and_do_not_echo_ado_failure_body(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server = gateway(
        tmp_path,
        monkeypatch,
        [ToolName.GET_ADO_BUILD, ToolName.LIST_ADO_BUILD_WORK_ITEMS],
    )
    requests = install_transport(
        monkeypatch,
        lambda request: httpx.Response(403, text="private upstream content"),
    )

    with pytest.raises(ToolError, match="positive integer"):
        asyncio.run(
            server.call_tool("get_ado_build", {"session_id": "session-1", "build_id": "bad"})
        )
    assert requests == []

    with pytest.raises(ToolError) as error:
        asyncio.run(
            server.call_tool(
                "list_ado_build_work_items", {"session_id": "session-1", "build_id": 42}
            )
        )
    assert "HTTP 403" in str(error.value)
    assert "private upstream content" not in str(error.value)
