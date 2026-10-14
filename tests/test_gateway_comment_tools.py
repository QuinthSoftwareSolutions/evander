"""GitHub PR comment operations remain available without optional integrations."""

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from gateway_tool_support import call_tool
from http_client_support import patch_httpx_client
from evander.config import GatewayConfig
from evander.directories import DirectoryService
from evander.directory_config import DirectoryConfigStore
from evander.gateway import create_server
from evander.models import SessionManifest, ToolName
from evander.runtime import build_runtime


@pytest.mark.parametrize("tool", [ToolName.DELETE_PR_COMMENT, ToolName.RESOLVE_PR_COMMENT])
def test_github_comment_tools_reach_the_provider_through_the_session_gateway(
    tmp_path, monkeypatch, tool
) -> None:
    def handle(request):
        if request.method == "DELETE":
            return httpx.Response(204)
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "pull_request_url": "https://api.github.com/repos/org/repo/pulls/17",
                    "node_id": "PRRC_target",
                },
            )
        query = json.loads(request.content)["query"]
        if "mutation" in query:
            data = {"resolveReviewThread": {"thread": {"id": "PRRT_target", "isResolved": True}}}
        else:
            data = {
                "repository": {
                    "pullRequest": {
                        "reviewThreads": {
                            "nodes": [
                                {
                                    "id": "PRRT_target",
                                    "isResolved": False,
                                    "comments": {
                                        "nodes": [{"id": "PRRC_target"}],
                                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                                    },
                                }
                            ],
                            "pageInfo": {"hasNextPage": False, "endCursor": None},
                        }
                    }
                }
            }
        return httpx.Response(200, json={"data": data})

    context = build_runtime(GatewayConfig(
        runtime_dir=tmp_path,
        audit_log_path=tmp_path / "audit.jsonl",
        addon_loader={"enabled_addon_ids": []},
    ))
    DirectoryService(context.directories).create(parent="root", name="team-a")
    context.session_repository.upsert_session(SessionManifest(
        session_id="session-a",
        directory_path="root/team-a",
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        allowed_tools=[tool],
    ))
    requests = []

    def record_request(request):
        requests.append(request)
        return handle(request)

    patch_httpx_client(monkeypatch, httpx.MockTransport(record_request))
    monkeypatch.setenv("COMMENT_GITHUB_TOKEN", "synthetic-github-token")
    DirectoryConfigStore(context.directories, runtime_dir=context.config.runtime_dir).write(
        "root/team-a", domain="providers", payload={"github_token_env": "COMMENT_GITHUB_TOKEN"}
    )
    arguments = {"provider": "github", "repo": "org/repo", "pr_id": "17", "comment_id": "123"}
    if tool is ToolName.DELETE_PR_COMMENT:
        arguments["feedback_kind"] = "review_comment"
    result = call_tool(create_server(context), tool.value, {"session_id": "session-a", **arguments})
    assert result["provider"] == "github"
    assert result["status"] == ("deleted" if tool is ToolName.DELETE_PR_COMMENT else "resolved")
    assert all(r.headers["authorization"] == "Bearer synthetic-github-token" for r in requests)
