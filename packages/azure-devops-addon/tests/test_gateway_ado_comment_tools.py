from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from http_client_support import patch_httpx_client
from evander.directories import DirectoryService
from evander.directory_config import DirectoryConfigStore
from evander.gateway import create_server
from evander.models import ApprovalStatus, SessionManifest, ToolName
from test_gateway_issue_tools import (
    call_tool,
    make_context,
    read_audit_events,
    write_root_policy_overlay,
)


CASES = [
    (ToolName.DELETE_ADO_BACKLOG_COMMENT, {"id": 77, "comment_id": 123}),
    (
        ToolName.DELETE_PR_COMMENT,
        {
            "provider": "ado",
            "repo": "repo-1",
            "pr_id": "17",
            "thread_id": "45",
            "comment_id": "3",
        },
    ),
    (
        ToolName.RESOLVE_PR_COMMENT,
        {
            "provider": "ado",
            "repo": "repo-1",
            "pr_id": "17",
            "thread_id": "45",
        },
    ),
]


def gateway(
    tmp_path,
    monkeypatch,
    tool,
    *,
    response_status=None,
    response_handler=None,
    session_options=None,
):
    context = make_context(tmp_path)
    DirectoryService(context.directories).create(parent="root", name="team-b")
    store = DirectoryConfigStore(context.directories, runtime_dir=context.config.runtime_dir)
    for suffix in ["a", "b"]:
        pat_env = f"COMMENT_TEST_PAT_{suffix.upper()}"
        monkeypatch.setenv(pat_env, f"pat-{suffix}")
        store.write(
            "root/team-" + suffix,
            domain="providers",
            payload={
                "azure_devops_organization_url": "https://dev.azure.com/org-" + suffix,
                "azure_devops_project": "Project " + suffix,
                "azure_devops_pat_env": pat_env,
                "azure_devops_pr_pat_env": pat_env,
            },
        )
        context.session_repository.upsert_session(
            SessionManifest(
                session_id="session-" + suffix,
                directory_path="root/team-" + suffix,
                expires_at=datetime.now(UTC) + timedelta(hours=1),
                **{"allowed_tools": [tool], **(session_options or {})},
            )
        )
    requests = []

    def handle(request):
        requests.append(request)
        if response_handler is not None:
            return response_handler(request)
        if response_status:
            return httpx.Response(response_status, json={"message": "Synthetic provider failure"})
        if request.method == "PATCH":
            return httpx.Response(200, json={"id": 45, "status": "fixed"})
        return httpx.Response(204)

    patch_httpx_client(monkeypatch, httpx.MockTransport(handle))
    return context, create_server(context), requests


@pytest.mark.parametrize(("tool", "arguments"), CASES)
def test_comment_tools_use_the_calling_sessions_connection_and_audit_results(
    tmp_path, monkeypatch, tool, arguments
) -> None:
    context, server, requests = gateway(tmp_path, monkeypatch, tool)
    results = [
        call_tool(server, tool.value, {"session_id": "session-" + suffix, **arguments})
        for suffix in ["a", "b"]
    ]
    assert [r.headers["authorization"] for r in requests] == ["Basic OnBhdC1h", "Basic OnBhdC1i"]
    assert [r.url.path.split("/")[:3] for r in requests] == [
        ["", "org-a", "Project a"],
        ["", "org-b", "Project b"],
    ]
    assert all(
        result["resolved" if tool is ToolName.RESOLVE_PR_COMMENT else "deleted"]
        for result in results
    )
    events = [
        e
        for e in read_audit_events(context.config.audit_log_path)
        if e.get("tool_name") == tool.value
    ]
    assert [e["decision"] for e in events] == ["allowed", "allowed"]
    assert [e["session_id"] for e in events] == ["session-a", "session-b"]
    assert "pat-a" not in json.dumps(events)
    assert "pat-b" not in json.dumps(events)


@pytest.mark.parametrize(("tool", "arguments"), CASES)
@pytest.mark.parametrize("restriction", ["no_grant", "directory_denial", "arguments"])
def test_comment_tools_enforce_policy_before_provider_io(
    tmp_path, monkeypatch, tool, arguments, restriction
) -> None:
    options = {}
    if restriction == "no_grant":
        options["allowed_tools"] = [ToolName.POST_ADO_BACKLOG_COMMENT, ToolName.POST_PR_COMMENT]
    elif restriction == "arguments":
        field = "id" if tool is ToolName.DELETE_ADO_BACKLOG_COMMENT else "repo"
        options["tool_argument_policies"] = {
            tool.value: [
                {
                    "id": "target",
                    "name": "Allowed target",
                    "constraints": [
                        {
                            "id": "target",
                            "path": field,
                            "operator": "enum",
                            "values": ["other"],
                        }
                    ],
                }
            ]
        }
    context, server, requests = gateway(tmp_path, monkeypatch, tool, session_options=options)
    if restriction == "directory_denial":
        write_root_policy_overlay(
            context.config, {"mcp_tool_policies": {tool.value: {"denied": True}}}
        )
    with pytest.raises(ToolError):
        call_tool(server, tool.value, {"session_id": "session-a", **arguments})
    assert requests == []
    events = [
        e
        for e in read_audit_events(context.config.audit_log_path)
        if e.get("tool_name") == tool.value
    ]
    assert events[-1]["decision"] == "denied"


@pytest.mark.parametrize(("tool", "arguments"), CASES)
def test_comment_tools_honor_configured_approval_and_require_the_approved_target(
    tmp_path, monkeypatch, tool, arguments
) -> None:
    context, server, requests = gateway(
        tmp_path, monkeypatch, tool, session_options={"approval_required_tools": [tool]}
    )
    arguments = {"session_id": "session-a", **arguments}
    with pytest.raises(ToolError, match="requires approval"):
        call_tool(server, tool.value, arguments)
    assert requests == []
    approval = next(iter(context.approvals.read_all().values()))
    approval.status = ApprovalStatus.APPROVED
    approval.approver = "operator"
    approval.decision_at = datetime.now(UTC)
    context.approvals.put(approval.request_id, approval)
    target_field = "thread_id" if tool is ToolName.RESOLVE_PR_COMMENT else "comment_id"
    with pytest.raises(ToolError, match="payload mismatch"):
        call_tool(
            server,
            tool.value,
            {
                **arguments,
                target_field: "456",
                "approval_request_id": approval.request_id,
            },
        )
    assert requests == []
    result = call_tool(
        server, tool.value, {**arguments, "approval_request_id": approval.request_id}
    )
    assert result["status"] == ("resolved" if tool is ToolName.RESOLVE_PR_COMMENT else "deleted")
    assert len(requests) == 1


@pytest.mark.parametrize(("tool", "arguments"), CASES)
def test_comment_tools_audit_provider_failures_without_reporting_success(
    tmp_path, monkeypatch, tool, arguments
) -> None:
    context, server, requests = gateway(tmp_path, monkeypatch, tool, response_status=404)
    with pytest.raises(ToolError):
        call_tool(server, tool.value, {"session_id": "session-a", **arguments})
    assert len(requests) == 1
    events = [
        e
        for e in read_audit_events(context.config.audit_log_path)
        if e.get("tool_name") == tool.value
    ]
    assert [e["decision"] for e in events] == ["error"]




@pytest.mark.parametrize("field", ["id", "comment_id"])
@pytest.mark.parametrize("bad_id", ["../1", "0", "-1", " ", True, False])
def test_work_item_comment_deletion_rejects_invalid_ids_before_provider_io(
    tmp_path, monkeypatch, field, bad_id
) -> None:
    context, server, requests = gateway(tmp_path, monkeypatch, ToolName.DELETE_ADO_BACKLOG_COMMENT)
    with pytest.raises(ToolError):
        call_tool(
            server,
            "delete_ado_backlog_comment",
            {
                "session_id": "session-a",
                "id": 77,
                "comment_id": 123,
                field: bad_id,
            },
        )
    assert requests == []
