from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import ValidationError

from evander.gateway import create_server
from evander_azure_devops_addon.issues import AzureDevOpsIssueAdapter
from evander.models import AdoBacklogStateUpdateRequest, SessionManifest, ToolName
from test_gateway_issue_tools import (
    call_tool,
    make_context,
    read_audit_events,
    write_root_policy_overlay,
)


TOOL = ToolName.UPDATE_ADO_BACKLOG_STATE


def _gateway(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **session_options):
    context = make_context(tmp_path)
    context.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            directory_path="root/team-a",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            **{"allowed_tools": [TOOL], **session_options},
        )
    )
    server = create_server(context)
    requests: list[httpx.Request] = []
    directories: list[str] = []

    def resolve_connection(self, directory_path):
        directories.append(directory_path)
        return "https://dev.azure.com/example", "configured-project", "host-test-token"

    monkeypatch.setattr(AzureDevOpsIssueAdapter, "_resolve_connection", resolve_connection)
    original_client = httpx.Client

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={"id": 77, "fields": {"System.State": "Active"}})

    def transport(handler=respond):
        def send(request):
            if handler is not respond:
                requests.append(request)
            return handler(request)

        monkeypatch.setattr(
            "evander.issues.service.httpx.Client",
            lambda timeout: original_client(timeout=timeout, transport=httpx.MockTransport(send)),
        )

    transport()
    return context, server, requests, directories, transport


def _arguments(**overrides):
    return {"session_id": "session-1", "id": 77, "state": "Active", **overrides}


@pytest.mark.parametrize("expected_state", [None, " Committed "])
@pytest.mark.parametrize("project", [None, " Other Project "])
def test_state_tool_reaches_ado_with_only_state_and_atomic_guard(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    expected_state,
    project,
) -> None:
    context, server, requests, directories, _ = _gateway(tmp_path, monkeypatch)
    result = call_tool(
        server,
        TOOL.value,
        _arguments(
            state=" Active ",
            expected_state=expected_state,
            project=project,
        ),
    )

    assert directories == ["root/team-a"]
    assert len(requests) == 1
    request = requests[0]
    assert request.method == "PATCH"
    assert request.url.host == "dev.azure.com"
    selected_project = project.strip() if project else "configured-project"
    assert request.url.path == f"/example/{selected_project}/_apis/wit/workitems/77"
    assert request.headers["content-type"] == "application/json-patch+json"
    expected_operations = []
    if expected_state:
        expected_operations.append(
            {
                "op": "test",
                "path": "/fields/System.State",
                "value": "Committed",
            }
        )
    expected_operations.append({"op": "add", "path": "/fields/System.State", "value": "Active"})
    assert json.loads(request.content) == expected_operations
    assert result["updated"] is True
    assert result["work_item_id"] == "77"
    assert result["state"] == "Active"
    assert result["expected_state"] == ("Committed" if expected_state else None)
    events = [
        e
        for e in read_audit_events(context.config.audit_log_path)
        if e.get("tool_name") == TOOL.value
    ]
    assert events[-1]["decision"] == "allowed"
    assert events[-1]["result"]["updated"] is True
    assert "host-test-token" not in json.dumps(events)


@pytest.mark.parametrize("failure", ["conflict", "invalid_state", "timeout"])
def test_state_tool_propagates_provider_failure_without_unguarded_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    context, server, requests, _, transport = _gateway(tmp_path, monkeypatch)
    remote_state = "Done"

    def reject(request):
        operations = json.loads(request.content)
        assert operations[0] == {"op": "test", "path": "/fields/System.State", "value": "Committed"}
        if failure == "timeout":
            raise httpx.ReadTimeout("uncertain update", request=request)
        if failure == "conflict":
            assert remote_state != operations[0]["value"]
            return httpx.Response(412, json={"message": "state test failed"})
        return httpx.Response(400, json={"message": "invalid state transition"})

    transport(reject)
    with pytest.raises(
        ToolError,
        match="Error executing tool update_ado_backlog_state",
    ):
        call_tool(server, TOOL.value, _arguments(expected_state="Committed"))
    assert len(requests) == 1
    assert remote_state == "Done"
    events = [
        e
        for e in read_audit_events(context.config.audit_log_path)
        if e.get("tool_name") == TOOL.value
    ]
    assert events[-1]["decision"] == "error"


@pytest.mark.parametrize("restriction", ["no_grant", "directory_denial", "approval", "arguments"])
def test_state_tool_enforces_permissions_before_accessing_ado(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    restriction: str,
) -> None:
    options = {}
    if restriction == "no_grant":
        options["allowed_tools"] = [
            ToolName.GET_ADO_BACKLOG_ITEM,
            ToolName.POST_ADO_BACKLOG_COMMENT,
        ]
    elif restriction == "approval":
        options["approval_required_tools"] = [TOOL]
    elif restriction == "arguments":
        options["tool_argument_policies"] = {
            TOOL.value: [
                {
                    "id": "transition",
                    "name": "Allowed state",
                    "constraints": [
                        {
                            "id": "state",
                            "path": "state",
                            "operator": "enum",
                            "values": ["Committed"],
                        }
                    ],
                }
            ]
        }
    context, server, requests, directories, _ = _gateway(tmp_path, monkeypatch, **options)
    if restriction == "directory_denial":
        write_root_policy_overlay(
            context.config, {"mcp_tool_policies": {TOOL.value: {"denied": True}}}
        )
    with pytest.raises(ToolError):
        call_tool(server, TOOL.value, _arguments())
    assert requests == []
    assert directories == []
    if restriction == "approval":
        approvals = list(context.approvals.read_all().values())
        assert len(approvals) == 1
        assert approvals[0].tool_name == TOOL.value


@pytest.mark.parametrize(
    "overrides", [{"state": " "}, {"expected_state": " "}, {"id": "not-an-id"}]
)
def test_state_tool_rejects_invalid_arguments_before_accessing_ado(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    overrides,
) -> None:
    _, server, requests, directories, _ = _gateway(tmp_path, monkeypatch)
    with pytest.raises(ToolError):
        call_tool(server, TOOL.value, _arguments(**overrides))
    assert requests == []
    assert directories == []


def test_state_tool_schema_and_visibility_require_explicit_grant(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context, server, _, _, _ = _gateway(tmp_path, monkeypatch, allowed_tools=[])
    schema = server._tool_manager.get_tool(TOOL.value).parameters
    assert set(schema["required"]) == {"id", "state"}
    assert "expected_state" in schema["properties"]
    assert "directory_path" not in schema["properties"]
    session = context.policy.get_session("session-1")
    assert context.policy.tool_visibility_for_session(session, TOOL).available is False
    session.allowed_tools = [TOOL]
    context.session_repository.upsert_session(session)
    assert context.policy.tool_visibility_for_session(session, TOOL).available is True


def test_state_request_normalizes_names_without_assuming_a_process() -> None:
    request = AdoBacklogStateUpdateRequest(
        id=" 77 ",
        project=" Custom Project ",
        state=" Awaiting QA ",
        expected_state=" Active ",
    )
    assert request.model_dump() == {
        "id": "77",
        "project": "Custom Project",
        "state": "Awaiting QA",
        "expected_state": "Active",
    }
    assert AdoBacklogStateUpdateRequest(id=77, state="Active", project=" ").project is None


@pytest.mark.parametrize(
    "field,value",
    [
        ("id", None),
        ("id", True),
        ("id", 0),
        ("id", -1),
        ("id", 1.5),
        ("id", "1x"),
        ("id", "²"),
        ("state", None),
        ("state", ""),
        ("state", " "),
        ("state", 5),
        ("expected_state", ""),
        ("expected_state", " "),
        ("expected_state", False),
    ],
)
def test_state_request_rejects_invalid_values(field, value) -> None:
    with pytest.raises(ValidationError):
        AdoBacklogStateUpdateRequest(**{"id": 77, "state": "Active", field: value})
