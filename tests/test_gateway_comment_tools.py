"""GitHub PR comment operations remain available without optional integrations."""

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from gateway_tool_support import call_tool
from http_client_support import patch_httpx_client
from evander.config import GatewayConfig
from evander.directories import DirectoryService
from evander.directory_config import DirectoryConfigStore
from evander.gateway import create_server
from evander.models import SessionManifest, ToolName
from evander.runtime import build_runtime


def review_gateway(tmp_path, *, metadata=None, grant=True):
    class RecordingPublisher:
        def __init__(self):
            self.calls = []
            self.result = {"status": "posted", "provider": "azure_devops", "comment_ids": ["10"]}

        def submit_review(self, **arguments):
            self.calls.append(arguments)
            return self.result

    context = build_runtime(
        GatewayConfig(
            runtime_dir=tmp_path,
            audit_log_path=tmp_path / "audit.jsonl",
            addon_loader={"enabled_addon_ids": []},
        )
    )
    context.session_repository.upsert_session(
        SessionManifest(
            session_id="session-a",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            allowed_tools=[ToolName.SUBMIT_PR_REVIEW] if grant else [],
            metadata=metadata or {},
        )
    )
    publisher = RecordingPublisher()
    context.pr_publish_service = publisher
    return context, create_server(context), publisher


@pytest.mark.parametrize(
    "restriction", ["missing-stream", "wrong-pr", "wrong-repo", "no-grant", "approval"]
)
def test_azure_review_votes_enforce_session_target_and_existing_permissions(tmp_path, restriction):
    metadata = {
        "pr_feedback_streams": [{"provider": "azure_devops", "repo": "repo-1", "pr_id": "17"}]
    }
    if restriction == "missing-stream":
        metadata = {}
    context, server, publisher = review_gateway(
        tmp_path, metadata=metadata, grant=restriction != "no-grant"
    )
    if restriction == "approval":
        DirectoryConfigStore(context.directories, runtime_dir=tmp_path).write(
            "root", domain="policy", payload={"pr_reply_require_approval": True}
        )
    arguments = {
        "session_id": "session-a",
        "provider": "ado",
        "repo": "repo-1",
        "pr_id": "17",
        "body_text": "Summary",
        "event": "APPROVE",
    }
    if restriction == "wrong-pr":
        arguments["pr_id"] = "18"
    elif restriction == "wrong-repo":
        arguments["repo"] = "another-repo"
    with pytest.raises(ToolError):
        call_tool(server, "submit_pr_review", arguments)
    assert publisher.calls == []


def test_azure_review_reset_reaches_publisher_and_audits_zero_vote(tmp_path):
    context, server, publisher = review_gateway(
        tmp_path,
        metadata={
            "pr_feedback_streams": [{"provider": "azure_devops", "repo": "repo-1", "pr_id": "17"}]
        },
    )
    publisher.result.update({"reviewer_id": "host-bot", "vote": 0})
    result = call_tool(
        server,
        "submit_pr_review",
        {
            "session_id": "session-a",
            "provider": "ado",
            "repo": "repo-1",
            "pr_id": "17",
            "body_text": "Resetting my review vote",
            "event": "RESET",
        },
    )
    assert result["vote"] == 0
    assert len(publisher.calls) == 1
    assert publisher.calls[0]["provider"] == "azure_devops"
    assert publisher.calls[0]["event"] == "RESET"
    entries = [json.loads(line) for line in context.config.audit_log_path.read_text().splitlines()]
    assert entries[-1]["decision"] == "allowed"
    assert entries[-1]["result"]["vote"] == 0
    assert entries[-1]["result"]["reviewer_id"] == "host-bot"


@pytest.mark.parametrize(
    ("provider", "event"),
    [
        ("ado", None),
        ("ado", "COMMENT"),
        ("ado", " "),
        ("github", "REQUEST_CHANGES"),
    ],
)
def test_comment_only_reviews_and_github_keep_existing_target_policy(tmp_path, provider, event):
    _, server, publisher = review_gateway(tmp_path)
    call_tool(
        server,
        "submit_pr_review",
        {
            "session_id": "session-a",
            "provider": provider,
            "repo": "repo-1",
            "pr_id": "17",
            "body_text": "Summary",
            "event": event,
        },
    )
    assert len(publisher.calls) == 1


def test_azure_draft_review_returns_and_audits_deferred_vote(tmp_path):
    context, server, publisher = review_gateway(
        tmp_path,
        metadata={
            "pr_feedback_streams": [{"provider": "azure_devops", "repo": "repo-1", "pr_id": "17"}]
        },
    )
    publisher.result.update(
        {
            "event": "COMMENT",
            "requested_event": "APPROVE",
            "vote_deferred": True,
            "vote_deferred_reason": "draft_pull_request",
        }
    )
    result = call_tool(
        server,
        "submit_pr_review",
        {
            "session_id": "session-a",
            "provider": "ado",
            "repo": "repo-1",
            "pr_id": "17",
            "body_text": "Summary",
            "event": "APPROVE",
        },
    )
    assert result["event"] == "COMMENT"
    assert result["vote_deferred"] is True
    assert "vote" not in result
    entries = [json.loads(line) for line in context.config.audit_log_path.read_text().splitlines()]
    assert entries[-1]["decision"] == "allowed"
    assert entries[-1]["result"]["vote_deferred"] is True
    assert entries[-1]["result"]["vote_deferred_reason"] == "draft_pull_request"
    assert "vote" not in entries[-1]["result"]


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
