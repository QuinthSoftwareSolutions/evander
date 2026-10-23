from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from mcp.server.mcpserver.exceptions import ToolError

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
    "tool,granted,arguments",
    [
        ("get_ado_build", [ToolName.LIST_ADO_BUILDS], {"build_id": 42}),
        ("list_ado_build_work_items", [ToolName.LIST_ADO_BUILDS], {"build_id": 42}),
        (
            "list_ado_builds",
            [ToolName.GET_ADO_BUILD, ToolName.LIST_ADO_BUILD_WORK_ITEMS],
            {"build_number": "release-42"},
        ),
    ],
)
def test_build_tools_require_individual_grants_before_network_access(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tool: str,
    granted: list[ToolName],
    arguments: dict,
) -> None:
    server = gateway(tmp_path, monkeypatch, granted)
    requests = install_transport(monkeypatch, lambda request: pytest.fail("unexpected ADO call"))

    with pytest.raises(ToolError, match="not allowed"):
        asyncio.run(server.call_tool(tool, {"session_id": "session-1", **arguments}))

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


def test_list_builds_combines_filters_with_directory_scoped_read_only_access(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server = gateway(tmp_path, monkeypatch, [ToolName.LIST_ADO_BUILDS])

    def respond(request):
        assert request.method == "GET"
        assert request.url.path == "/example/TeamProject/_apis/build/builds"
        assert request.headers["authorization"] == "Basic OnRlYW0tc2VjcmV0LXRva2Vu"
        assert dict(request.url.params) == {
            "api-version": "7.1",
            "$top": "2",
            "queryOrder": "queueTimeDescending",
            "buildNumber": "release-42",
            "definitions": "7",
            "repositoryId": "repo-1",
            "branchName": "refs/heads/release",
        }
        return httpx.Response(
            200,
            json={
                "value": [
                    {
                        "id": 42,
                        "buildNumber": "release-42",
                        "definition": {"id": 7, "name": "Release pipeline"},
                        "repository": {"id": "repo-1", "name": "Application"},
                        "status": "completed",
                        "result": "succeeded",
                        "sourceBranch": "refs/heads/release",
                        "sourceVersion": "abc123",
                        "queueTime": "2026-09-10T09:00:00Z",
                        "finishTime": "2026-09-10T09:05:00Z",
                        "parameters": "private build parameters",
                        "url": "https://untrusted.example/build",
                    }
                ]
            },
        )

    requests = install_transport(monkeypatch, respond)
    payload = call_tool(
        server,
        "list_ado_builds",
        {
            "session_id": "session-1",
            "build_number": " release-42 ",
            "pipeline_id": "7",
            "repository_id": " repo-1 ",
            "branch": " refs/heads/release ",
            "limit": 2,
        },
    )

    assert payload == {
        "project": "TeamProject",
        "builds": [
            {
                "run_id": "42",
                "build_number": "release-42",
                "run_url": "https://dev.azure.com/example/TeamProject/_build/results?buildId=42",
                "status": "completed",
                "result": "succeeded",
                "completed": True,
                "success": True,
                "source_version": "abc123",
                "source_branch": "refs/heads/release",
                "queue_time": "2026-09-10T09:00:00Z",
                "finish_time": "2026-09-10T09:05:00Z",
                "pipeline_id": "7",
                "pipeline_name": "Release pipeline",
                "repository_id": "repo-1",
                "repository_name": "Application",
            }
        ],
        "count": 1,
        "continuation_token": None,
    }
    assert len(requests) == 1


def test_list_builds_preserves_ambiguous_matches_and_opaque_paging_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server = gateway(tmp_path, monkeypatch, [ToolName.LIST_ADO_BUILDS])
    cursor = " page+/=2 "

    def respond(request):
        assert request.url.params["buildNumber"] == "release-*"
        assert request.url.params["$top"] == "25"
        assert "definitions" not in request.url.params
        if "continuationToken" in request.url.params:
            assert request.url.params["continuationToken"] == cursor
            return httpx.Response(200, json={"value": []})
        return httpx.Response(
            200,
            json={
                "value": [
                    {
                        "id": 42,
                        "buildNumber": "release-42",
                        "definition": {"id": 7, "name": "Application"},
                        "status": "completed",
                        "result": "failed",
                    },
                    {
                        "id": 43,
                        "buildNumber": "release-42",
                        "definition": {"id": 8, "name": "Database"},
                        "status": "inProgress",
                    },
                ]
            },
            headers={"x-ms-continuationtoken": cursor},
        )

    requests = install_transport(monkeypatch, respond)
    arguments = {"session_id": "session-1", "build_number": "release-*"}
    first = call_tool(server, "list_ado_builds", arguments)
    second = call_tool(
        server, "list_ado_builds", {**arguments, "continuation_token": first["continuation_token"]}
    )

    assert first["count"] == 2
    assert first["continuation_token"] == cursor
    assert [item["run_id"] for item in first["builds"]] == ["42", "43"]
    assert [item["pipeline_id"] for item in first["builds"]] == ["7", "8"]
    assert [item["pipeline_name"] for item in first["builds"]] == ["Application", "Database"]
    assert [item["status"] for item in first["builds"]] == ["completed", "inprogress"]
    assert all(not item["success"] for item in first["builds"])
    assert second == {
        "project": "TeamProject", "builds": [], "count": 0, "continuation_token": None
    }
    assert len(requests) == 2


def test_list_builds_allows_bounded_unfiltered_discovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server = gateway(tmp_path, monkeypatch, [ToolName.LIST_ADO_BUILDS])
    requests = install_transport(monkeypatch, lambda request: httpx.Response(200, json={"value": []}))

    payload = call_tool(server, "list_ado_builds", {"session_id": "session-1", "limit": 100})

    assert payload["count"] == 0
    assert dict(requests[0].url.params) == {
        "api-version": "7.1", "$top": "100", "queryOrder": "queueTimeDescending"
    }


@pytest.mark.parametrize(
    "arguments",
    [
        {"pipeline_id": "bad"},
        {"pipeline_id": 0},
        {"pipeline_id": -1},
        {"pipeline_id": True},
        {"build_number": " "},
        {"repository_id": ""},
        {"branch": " "},
        {"continuation_token": ""},
        {"limit": 0},
        {"limit": 101},
        {"limit": True},
        {"limit": 1.5},
    ],
)
def test_list_builds_rejects_invalid_filters_and_limits_before_network_access(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, arguments: dict
) -> None:
    server = gateway(tmp_path, monkeypatch, [ToolName.LIST_ADO_BUILDS])
    requests = install_transport(monkeypatch, lambda request: pytest.fail("unexpected ADO call"))

    with pytest.raises(ToolError):
        asyncio.run(server.call_tool("list_ado_builds", {"session_id": "session-1", **arguments}))

    assert requests == []


@pytest.mark.parametrize(
    "response,message",
    [
        (httpx.Response(403, text="private upstream content"), "HTTP 403"),
        (httpx.Response(200, text="private upstream content"), "invalid build data"),
        (httpx.Response(200, json={"value": "private upstream content"}), "invalid build list"),
        (
            httpx.Response(200, json={"value": [{"id": 42}, {"id": 43}]}),
            "more builds than requested",
        ),
        (
            httpx.Response(200, json={"value": [{"definition": "private upstream content"}]}),
            "invalid build identity data",
        ),
    ],
)
def test_list_builds_reports_provider_failures_without_raw_response_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, response: httpx.Response, message: str
) -> None:
    server = gateway(tmp_path, monkeypatch, [ToolName.LIST_ADO_BUILDS])
    install_transport(monkeypatch, lambda request: response)

    with pytest.raises(ToolError, match=message) as error:
        asyncio.run(server.call_tool("list_ado_builds", {"session_id": "session-1", "limit": 1}))

    assert "private upstream content" not in str(error.value)


def test_list_builds_advertises_read_only_tool_and_bounded_schema(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server = gateway(tmp_path, monkeypatch, [ToolName.LIST_ADO_BUILDS])
    tool = next(tool for tool in asyncio.run(server.list_tools()) if tool.name == "list_ado_builds")

    published = tool.model_dump(by_alias=True)
    assert published["annotations"]["readOnlyHint"] is True
    properties = published["inputSchema"]["properties"]
    assert properties["limit"]["default"] == 25
    assert properties["limit"]["minimum"] == 1
    assert properties["limit"]["maximum"] == 100
    assert {"build_number", "pipeline_id", "repository_id", "branch", "continuation_token"} <= properties.keys()
