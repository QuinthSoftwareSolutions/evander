from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from evander.config import GatewayConfig
from evander.gateway.server import create_server
from evander.models import CompletePullRequestResponse, SessionManifest, ToolName
from evander.runtime import build_runtime
from gateway_tool_support import call_tool as _call_tool


@pytest.fixture(params=["azure_devops", "github"])
def gateway(tmp_path, request):
    provider = request.param
    repo = "owner/repo" if provider == "github" else "repo"
    context = build_runtime(GatewayConfig(runtime_dir=tmp_path))
    context.session_repository.upsert_session(
        SessionManifest(
            session_id="reviewer",
            allowed_tools=[],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            metadata={"pr_feedback_streams": [{"provider": provider, "repo": repo, "pr_id": "17"}]},
        )
    )
    calls = []

    def complete(**kwargs):
        calls.append(kwargs)
        return CompletePullRequestResponse(provider=provider, repo=repo, pr_id="17", merged=True)

    context.pr_query_service = SimpleNamespace(complete_pull_request=complete)
    arguments = dict(
        session_id="reviewer",
        provider="ado" if provider == "azure_devops" else provider,
        repo=repo,
        pr_id="17",
        expected_source_commit="a" * 40,
        expected_target_branch="main",
    )
    return context, create_server(context), calls, arguments


def grant(context):
    session = context.session_repository.get_session("reviewer")
    session.allowed_tools = [ToolName.COMPLETE_PULL_REQUEST]
    context.session_repository.upsert_session(session)


def test_completion_requires_permission_and_a_bound_pr(gateway):
    context, server, calls, arguments = gateway
    with pytest.raises(ToolError, match="not allowed"):
        _call_tool(server, "complete_pull_request", arguments)
    grant(context)
    with pytest.raises(ToolError, match="not allowlisted"):
        _call_tool(server, "complete_pull_request", {**arguments, "pr_id": "18"})
    assert calls == []
    result = _call_tool(server, "complete_pull_request", arguments)
    assert result["merged"] is True
    assert calls == [
        {
            "provider": "azure_devops" if arguments["provider"] == "ado" else "github",
            "repo": arguments["repo"],
            "pr_id": "17",
            "expected_source_commit": "a" * 40,
            "expected_target_branch": "main",
        }
    ]


def test_completion_keeps_per_invocation_approval(gateway):
    context, server, calls, arguments = gateway
    grant(context)
    session = context.session_repository.get_session("reviewer")
    session.approval_required_tools = [ToolName.COMPLETE_PULL_REQUEST]
    context.session_repository.upsert_session(session)
    with pytest.raises(ToolError, match="approval"):
        _call_tool(server, "complete_pull_request", arguments)
    assert calls == []


@pytest.mark.parametrize(
    "change",
    [
        {"expected_source_commit": "short"},
        {"pr_id": "17/threads"},
        {"provider": "gitlab"},
    ],
)
def test_completion_rejects_invalid_arguments(gateway, change):
    context, server, calls, arguments = gateway
    grant(context)
    with pytest.raises((ToolError, ValueError)):
        _call_tool(server, "complete_pull_request", {**arguments, **change})
    assert calls == []


def test_status_update_cannot_bypass_completion(gateway):
    context, server, calls, arguments = gateway
    session = context.session_repository.get_session("reviewer")
    session.allowed_tools = [ToolName.UPDATE_PULL_REQUEST]
    context.session_repository.upsert_session(session)
    with pytest.raises(ToolError, match="complete_pull_request"):
        _call_tool(
            server,
            "update_pull_request",
            {
                "session_id": "reviewer",
                "provider": "ado",
                "repo": "repo",
                "pr_id": "17",
                "status": "completed",
            },
        )
    assert calls == []
