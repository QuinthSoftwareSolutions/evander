"""HTTP policy contracts shared by legacy sessions and MCP 2026 requests."""

from datetime import UTC, datetime, timedelta
import asyncio
import json

import httpx
import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
import pytest
from starlette.testclient import TestClient

from evander.addons import AddonLoaderConfig
from evander.directory_config import DirectoryConfigStore
from evander.gateway import create_app
from evander.models import ApprovalGrantSettings, ApprovalStatus, ToolName
from evander.session_auth import ensure_session_mcp_auth_token
from evander_elastic_stack_addon import mcp as elastic_mcp
from test_elastic_stack_addon import _client as elastic_client
from test_gateway import (
    _build_runtime_with_workflow_config,
    _mcp_initialize_session,
    _mcp_response_payload,
    read_audit_events,
)


@pytest.fixture(params=["legacy", "modern"])
def gateway(request, tmp_path):
    context = _build_runtime_with_workflow_config(
        tmp_path, allowed_tools=[ToolName.GET_WORKFLOW_SNAPSHOT]
    )
    manifest = context.session_repository.get_session("session-1")
    manifest, proof = ensure_session_mcp_auth_token(manifest)
    context.session_repository.upsert_session(manifest)
    with TestClient(create_app(context)) as client:
        headers = {
            "accept": "application/json, text/event-stream",
            "x-evander-session-id": "session-1",
            "x-evander-mcp-session-token": proof,
        }
        if request.param == "legacy":
            headers["mcp-session-id"] = _mcp_initialize_session(
                client, session_id="session-1", mcp_session_token=proof
            )
        else:
            headers["mcp-protocol-version"] = "2026-07-28"

        def rpc(method, params=None, *, extra_headers=None):
            params = dict(params or {})
            call_headers = headers | (extra_headers or {})
            if request.param == "modern":
                params["_meta"] = {
                    "io.modelcontextprotocol/protocolVersion": "2026-07-28",
                    "io.modelcontextprotocol/clientCapabilities": {},
                }
                call_headers["mcp-method"] = method
                if method == "tools/call":
                    call_headers["mcp-name"] = params["name"]
            response = client.post(
                "/mcp",
                headers=call_headers,
                json={"jsonrpc": "2.0", "id": 2, "method": method, "params": params},
            )
            if request.param == "modern":
                assert "mcp-session-id" not in response.headers
            return response

        yield context, rpc, proof


def payload(response):
    assert response.status_code == 200, response.text
    if response.headers.get("content-type", "").startswith("application/json"):
        return response.json()
    return _mcp_response_payload(response)


def call(rpc, arguments=None, *, extra_headers=None, name="get_workflow_snapshot"):
    return payload(
        rpc("tools/call", {"name": name, "arguments": arguments or {}}, extra_headers=extra_headers)
    )["result"]


def test_discovery_and_calls_recheck_current_session_grants(gateway):
    context, rpc, _ = gateway
    tools = payload(rpc("tools/list"))["result"]["tools"]
    assert [tool["name"] for tool in tools] == ["get_workflow_snapshot"]
    assert tools[0]["inputSchema"]["type"] == "object"
    assert "approval_request_id" in tools[0]["inputSchema"]["properties"]
    result = call(rpc)
    assert result.get("isError", False) is False
    assert result["structuredContent"]["worker_id"] == "trigger-orchestrator"
    assert "structured_content" not in result

    manifest = context.session_repository.get_session("session-1")
    manifest.allowed_tools = []
    context.session_repository.upsert_session(manifest)
    assert payload(rpc("tools/list"))["result"]["tools"] == []
    assert call(rpc)["isError"] is True
    events = read_audit_events(context.config.audit_log_path)
    assert sum(event.get("decision") == "allowed" for event in events) == 1


def test_session_proof_and_argument_binding_cannot_be_bypassed(gateway):
    context, rpc, _ = gateway
    response = rpc("tools/list", extra_headers={"x-evander-mcp-session-token": "wrong"})
    assert response.status_code == 401
    result = call(rpc, {"session_id": "other-session"})
    assert result["isError"] is True
    assert "session_id is bound" in json.dumps(result)
    events = read_audit_events(context.config.audit_log_path)
    assert not any(event.get("decision") == "allowed" for event in events)


def test_workflow_lease_and_exact_approval_retry_survive_protocol_change(gateway):
    context, rpc, proof = gateway
    manifest = context.session_repository.get_session("session-1")
    manifest.allowed_tools = []
    context.session_repository.upsert_session(manifest)
    DirectoryConfigStore(context.directories, runtime_dir=context.config.runtime_dir).write(
        "root",
        domain="policy",
        payload={"approval_required_tools": [ToolName.GET_WORKFLOW_SNAPSHOT.value]},
    )
    lease = context.tool_approval_leases.upsert_lease(
        session_id="session-1",
        workflow_run_id="run-1",
        tools=[ToolName.GET_WORKFLOW_SNAPSHOT],
        expires_at=datetime.now(UTC) + timedelta(minutes=10),
        approval_settings={ToolName.GET_WORKFLOW_SNAPSHOT.value: ApprovalGrantSettings(max_uses=1)},
    )
    headers = {
        "x-evander-workflow-run-id": "run-1",
        "x-evander-mcp-tool-approval-lease-id": lease.lease_id,
    }
    wrong_run = headers | {"x-evander-workflow-run-id": "run-2"}
    assert payload(rpc("tools/list", extra_headers=wrong_run))["result"]["tools"] == []
    assert call(rpc, extra_headers=wrong_run)["isError"] is True
    assert context.approvals.read_all() == {}

    pending = call(rpc, extra_headers=headers)
    assert pending["isError"] is True
    assert "requires approval" in json.dumps(pending)
    assert context.tool_approval_leases.get_lease(lease.lease_id).used_counts == {}
    approval = next(iter(context.approvals.read_all().values()))
    approval.status = ApprovalStatus.APPROVED
    approval.approver = "operator"
    approval.decision_at = datetime.now(UTC)
    context.approvals.put(approval.request_id, approval)

    mismatch = call(
        rpc,
        {"approval_request_id": approval.request_id, "include_runs": True},
        extra_headers=headers,
    )
    assert mismatch["isError"] is True
    assert context.tool_approval_leases.get_lease(lease.lease_id).used_counts == {}
    arguments = {"approval_request_id": approval.request_id}
    assert call(rpc, arguments, extra_headers=headers).get("isError", False) is False
    assert call(rpc, arguments, extra_headers=headers)["isError"] is True
    assert context.approvals.get(approval.request_id).used_count == 1
    assert context.tool_approval_leases.get_lease(lease.lease_id).used_counts == {
        ToolName.GET_WORKFLOW_SNAPSHOT.value: 1
    }
    events = read_audit_events(context.config.audit_log_path)
    allowed = [event for event in events if event.get("decision") == "allowed"]
    assert len(allowed) == 1
    assert allowed[0]["approval_request_id"] == approval.request_id
    assert allowed[0]["session_id"] == "session-1"
    assert proof not in context.config.audit_log_path.read_text()


def test_domain_validation_errors_omit_rejected_input_values(gateway):
    context, rpc, _ = gateway
    manifest = context.session_repository.get_session("session-1")
    manifest.allowed_tools.append(ToolName.SCHEDULE_RESUME)
    context.session_repository.upsert_session(manifest)
    result = call(
        rpc,
        {"resume_at": "private-input-sentinel", "prompt": "Resume next"},
        name="schedule_resume",
    )
    assert result["isError"] is True
    assert "resume_at" in json.dumps(result)
    assert "private-input-sentinel" not in json.dumps(result)


@pytest.mark.parametrize("protocol", ["legacy", "modern"])
def test_gateway_preserves_large_elastic_bulk_requests(tmp_path, monkeypatch, protocol):
    upstream_calls = []

    def upstream(request):
        upstream_calls.append(len(request.content))
        return httpx.Response(200, json={"errors": False, "items": [{"index": {"status": 201}}]})

    backend = elastic_client(upstream)
    monkeypatch.setattr(elastic_mcp, "client_for_session", lambda *_: backend)
    context = _build_runtime_with_workflow_config(
        tmp_path, allowed_tools=["elasticsearch_bulk_index_documents"]
    )
    context.config.addon_loader = AddonLoaderConfig(
        modules=["evander_elastic_stack_addon"], package_dirs=[]
    )
    manifest, proof = ensure_session_mcp_auth_token(
        context.session_repository.get_session("session-1")
    )
    context.session_repository.upsert_session(manifest)
    with TestClient(create_app(context)) as client:
        headers = {
            "accept": "application/json, text/event-stream",
            "x-evander-session-id": "session-1",
            "x-evander-mcp-session-token": proof,
        }
        if protocol == "legacy":
            headers["mcp-session-id"] = _mcp_initialize_session(
                client, session_id="session-1", mcp_session_token=proof
            )
        else:
            headers.update(
                {
                    "mcp-protocol-version": "2026-07-28",
                    "mcp-method": "tools/call",
                    "mcp-name": "elasticsearch_bulk_index_documents",
                }
            )

        def bulk(document_size):
            params = {
                "name": "elasticsearch_bulk_index_documents",
                "arguments": {
                    "target_id": "static",
                    "index_name": "events-synthetic",
                    "documents": [{"value": "x" * document_size}],
                },
            }
            if protocol == "modern":
                params["_meta"] = {
                    "io.modelcontextprotocol/protocolVersion": "2026-07-28",
                    "io.modelcontextprotocol/clientCapabilities": {},
                }
            return payload(
                client.post(
                    "/mcp",
                    headers=headers,
                    json={"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": params},
                )
            )["result"]

        assert bulk(4500 * 1024).get("isError", False) is False
        assert len(upstream_calls) == 1
        assert 4 * 1024 * 1024 < upstream_calls[0] < 5 * 1024 * 1024
        assert bulk(5 * 1024 * 1024)["isError"] is True
        assert len(upstream_calls) == 1


def test_gateway_rejects_oversized_wire_bodies_before_dispatch(gateway):
    context, rpc, _ = gateway
    response = rpc(
        "tools/call",
        {"name": "get_workflow_snapshot", "arguments": {}},
        extra_headers={"content-length": str(320 * 1024 * 1024 + 1)},
    )
    assert response.status_code == 413
    assert not any(
        event.get("decision") == "allowed"
        for event in read_audit_events(context.config.audit_log_path)
    )


def test_sdk_client_accepts_modern_gateway_responses(tmp_path):
    context = _build_runtime_with_workflow_config(
        tmp_path, allowed_tools=[ToolName.GET_WORKFLOW_SNAPSHOT]
    )
    manifest = context.session_repository.get_session("session-1")
    manifest, proof = ensure_session_mcp_auth_token(manifest)
    context.session_repository.upsert_session(manifest)
    app = create_app(context)

    async def exercise():
        async with (
            app.router.lifespan_context(app),
            httpx2.AsyncClient(
                transport=httpx2.ASGITransport(app=app),
                headers={
                    "x-evander-session-id": "session-1",
                    "x-evander-mcp-session-token": proof,
                },
                timeout=5,
            ) as http_client,
        ):
            async with Client(
                streamable_http_client("http://testserver/mcp", http_client=http_client),
                read_timeout_seconds=5,
            ) as client:
                assert client.protocol_version == "2026-07-28"
                tools = await client.list_tools()
                assert [tool.name for tool in tools.tools] == ["get_workflow_snapshot"]
                result = await client.call_tool("get_workflow_snapshot", {})
                assert result.is_error is False
                assert result.structured_content["worker_id"] == "trigger-orchestrator"

    asyncio.run(asyncio.wait_for(exercise(), timeout=10))
