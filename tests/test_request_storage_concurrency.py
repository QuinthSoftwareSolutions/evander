"""A stalled store must not stall unrelated requests or weaken per-call authority."""

import asyncio
from datetime import UTC, datetime, timedelta
import json
import threading
from types import SimpleNamespace

import anyio
import httpx
import pytest
from mcp.server.fastmcp.exceptions import ToolError
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from evander.addons import AddonLoaderConfig
from evander.admin.web.app import AuthMiddleware
from evander.config import GatewayConfig
from evander.gateway import _MCP_REQUEST_CONTEXT, create_app, create_server
from evander.gateway.app import _install_mcp_http_logging
import evander.gateway.server as gateway_server
from evander.models import ApprovalStatus, SessionManifest
from evander.runtime import build_runtime
from evander.session_auth import ensure_session_mcp_auth_token


@pytest.fixture
def runtime(tmp_path):
    return build_runtime(
        GatewayConfig(
            runtime_dir=tmp_path,
            audit_log_path=tmp_path / "audit.jsonl",
            addon_loader=AddonLoaderConfig(enabled_entry_points=False),
        )
    )


def session(runtime, name="session-1", *, approval=False):
    manifest, proof = ensure_session_mcp_auth_token(
        SessionManifest(
            session_id=name,
            allowed_tools=["probe"],
            approval_required_tools=["probe"] if approval else [],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    runtime.session_repository.upsert_session(manifest)
    return SimpleNamespace(
        query_params={"session_id": name}, headers={"x-evander-mcp-session-token": proof}
    )


def blocked(monkeypatch, target, name):
    entered, release = threading.Event(), threading.Event()
    original = getattr(target, name)

    def call(*args, **kwargs):
        entered.set()
        assert release.wait(3), "storage blocked the request event loop"
        return original(*args, **kwargs)

    monkeypatch.setattr(target, name, call)
    return entered, release


async def call_probe(server, arguments, request):
    context = SimpleNamespace(request_context=SimpleNamespace(request=request))
    return await server._tool_manager.call_tool("probe", arguments, context=context)


async def while_blocked(operation, entered, release, check):
    task = asyncio.create_task(operation)

    async def wait_entered():
        while not entered.is_set():
            await asyncio.sleep(0.005)

    try:
        await asyncio.wait_for(wait_entered(), 2)
        assert not task.done()
        await asyncio.wait_for(check(), 1)
    except BaseException:
        release.set()
        await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), 5)
        raise
    finally:
        release.set()
    return await asyncio.wait_for(task, 5)


@pytest.mark.parametrize("stage", ["authentication", "binding", "discovery"])
def test_mcp_storage_wait_does_not_block_unrelated_http(runtime, monkeypatch, stage):
    request = session(runtime)
    app = create_app(runtime)

    async def exercise():
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://gateway.test"
            ) as client:
                headers = {
                    **request.headers,
                    "x-evander-session-id": "session-1",
                    "accept": "application/json, text/event-stream",
                }
                initialized = await client.post(
                    "/mcp",
                    headers=headers,
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "initialize",
                        "params": {
                            "protocolVersion": "2024-11-05",
                            "capabilities": {},
                            "clientInfo": {"name": "test", "version": "1"},
                        },
                    },
                )
                assert initialized.status_code == 200
                headers["mcp-session-id"] = initialized.headers["mcp-session-id"]
                target, method = {
                    "authentication": (runtime.session_repository, "get_session"),
                    "binding": (runtime.mcp_transport_bindings, "bind_transport_session"),
                    "discovery": (runtime.policy, "visible_tools_for_session"),
                }[stage]
                entered, release = blocked(monkeypatch, target, method)

                async def unrelated():
                    assert (await client.get("/unrelated-path")).status_code == 404

                result = await while_blocked(
                    client.post(
                        "/mcp",
                        headers=headers,
                        json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                    ),
                    entered,
                    release,
                    unrelated,
                )
                assert result.status_code == 200
                assert '"error"' not in result.text

    asyncio.run(exercise())


@pytest.mark.parametrize("stage", ["authorization", "success_audit", "error_audit"])
def test_mcp_dispatch_waits_for_policy_and_audit_without_blocking(runtime, monkeypatch, stage):
    request = session(runtime)
    server = create_server(runtime)
    runtime.policy.set_extra_mcp_tool_names({"probe"})
    calls = []

    @server.tool()
    async def probe(session_id: str) -> dict:
        calls.append(session_id)
        if stage == "error_audit":
            raise ValueError("synthetic failure")
        return {"session_id": session_id}

    target, method = (
        (runtime.policy, "require_tool") if stage == "authorization" else (runtime.audit, "log")
    )
    entered, release = blocked(monkeypatch, target, method)

    async def exercise():
        token = _MCP_REQUEST_CONTEXT.set(request)
        try:

            async def concurrent_work():
                await asyncio.sleep(0)
                if stage == "authorization":
                    assert not calls
                else:
                    assert calls == ["session-1"]

            operation = call_probe(server, {}, request)
            if stage == "error_audit":
                with pytest.raises(ToolError, match="synthetic failure"):
                    await while_blocked(operation, entered, release, concurrent_work)
            else:
                await while_blocked(operation, entered, release, concurrent_work)
        finally:
            _MCP_REQUEST_CONTEXT.reset(token)

    asyncio.run(exercise())
    events = [json.loads(line) for line in runtime.audit.path.read_text().splitlines()]
    calls = [event for event in events if event.get("tool_name") == "probe"]
    assert len(calls) == 1
    assert calls[0]["session_id"] == "session-1"
    assert calls[0]["decision"] == ("error" if stage == "error_audit" else "allowed")


def test_concurrent_calls_consume_one_use_approval_once(runtime):
    request = session(runtime, approval=True)
    server = create_server(runtime)
    runtime.policy.set_extra_mcp_tool_names({"probe"})
    executed = []

    @server.tool()
    async def probe(session_id: str) -> dict:
        executed.append(session_id)
        await asyncio.sleep(0)
        return {"ok": True}

    async def exercise():
        token = _MCP_REQUEST_CONTEXT.set(request)
        try:
            with pytest.raises(ToolError, match="requires approval"):
                await call_probe(server, {}, request)
            approval = next(iter(runtime.approvals.read_all().values()))
            approval.status = ApprovalStatus.APPROVED
            approval.approver = "operator"
            approval.decision_at = datetime.now(UTC)
            runtime.approvals.put(approval.request_id, approval)
            results = await asyncio.gather(
                *(
                    call_probe(server, {"approval_request_id": approval.request_id}, request)
                    for _ in range(2)
                ),
                return_exceptions=True,
            )
            assert sum(isinstance(result, ToolError) for result in results) == 1
            assert executed == ["session-1"]
            assert runtime.approvals.get(approval.request_id).used_count == 1
        finally:
            _MCP_REQUEST_CONTEXT.reset(token)

    asyncio.run(exercise())


@pytest.mark.parametrize("fails", [False, True])
def test_cancelled_sync_tool_keeps_its_final_audit(runtime, monkeypatch, fails):
    request = session(runtime)
    entered, release = threading.Event(), threading.Event()
    effects = []
    register = gateway_server.register_connector_tools

    def register_probe(server, context, service):
        register(server, context, service)

        @server.tool()
        def probe(session_id: str) -> dict:
            effects.append(session_id)
            entered.set()
            assert release.wait(3), "tool was not released after cancellation"
            if fails:
                raise ValueError("failure after mutation")
            return {"ok": True}

    # Register before gateway setup so the real synchronous-tool wrapper is used.
    monkeypatch.setattr(gateway_server, "register_connector_tools", register_probe)
    server = create_server(runtime)
    runtime.policy.set_extra_mcp_tool_names({"probe"})

    async def exercise():
        scopes = []

        async def invoke():
            with anyio.CancelScope() as scope:
                scopes.append(scope)
                token = _MCP_REQUEST_CONTEXT.set(request)
                try:
                    if fails:
                        with pytest.raises(ToolError, match="failure after mutation"):
                            await call_probe(server, {}, request)
                    else:
                        await call_probe(server, {}, request)
                finally:
                    _MCP_REQUEST_CONTEXT.reset(token)

        async with anyio.create_task_group() as tasks:
            tasks.start_soon(invoke)
            try:
                with anyio.fail_after(2):
                    while not entered.is_set():
                        await anyio.sleep(0.005)
                scopes[0].cancel()
            finally:
                release.set()

    anyio.run(exercise)
    assert effects == ["session-1"]
    events = [json.loads(line) for line in runtime.audit.path.read_text().splitlines()]
    calls = [event for event in events if event.get("tool_name") == "probe"]
    assert len(calls) == 1
    assert calls[0]["session_id"] == "session-1"
    assert calls[0]["decision"] == ("error" if fails else "allowed")


def test_offloaded_calls_keep_each_sessions_identity_and_audit(runtime):
    requests = [session(runtime, name=f"session-{index}") for index in (1, 2)]
    server = create_server(runtime)
    runtime.policy.set_extra_mcp_tool_names({"probe"})
    executed = []

    @server.tool()
    async def probe(session_id: str, label: str) -> dict:
        await asyncio.sleep(0)
        executed.append((session_id, label))
        return {"label": label}

    async def invoke(request, index):
        token = _MCP_REQUEST_CONTEXT.set(request)
        try:
            await call_probe(server, {"label": f"call-{index}"}, request)
        finally:
            _MCP_REQUEST_CONTEXT.reset(token)

    async def exercise():
        await asyncio.gather(*(invoke(request, index) for index, request in enumerate(requests, 1)))

    asyncio.run(exercise())
    assert sorted(executed) == [("session-1", "call-1"), ("session-2", "call-2")]
    events = [json.loads(line) for line in runtime.audit.path.read_text().splitlines()]
    assert sorted(
        (event["session_id"], event["args"]["label"])
        for event in events
        if event.get("tool_name") == "probe"
    ) == sorted(executed)


def test_admin_authentication_wait_does_not_block_health(monkeypatch):
    auth = SimpleNamespace(
        config=SimpleNamespace(auth_required=True), read_session=lambda request: None
    )
    entered, release = blocked(monkeypatch, auth, "read_session")

    async def health(request):
        return JSONResponse({"ok": True})

    app = Starlette(routes=[Route("/healthz", health), Route("/api/private", health)])
    app.add_middleware(AuthMiddleware, auth=auth)

    async def exercise():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://admin.test"
        ) as client:

            async def check():
                assert (await client.get("/healthz")).status_code == 200

            result = await while_blocked(client.get("/api/private"), entered, release, check)
            assert result.status_code == 401

    asyncio.run(exercise())


def test_simultaneous_authenticated_requests_cannot_rebind_transport(runtime, monkeypatch):
    requests = [session(runtime, name=f"session-{index}") for index in (1, 2)]
    barrier = threading.Barrier(2)
    read = runtime.session_repository.get_session

    def simultaneous_read(session_id):
        result = read(session_id)
        barrier.wait(timeout=3)
        return result

    monkeypatch.setattr(runtime.session_repository, "get_session", simultaneous_read)
    identities = {}

    async def endpoint(request):
        return JSONResponse({"ok": True})

    app = Starlette(routes=[Route("/mcp", endpoint, methods=["POST"])])
    _install_mcp_http_logging(
        app,
        runtime_context=runtime,
        transport_session_map=identities,
        transport_binding_store=runtime.mcp_transport_bindings,
    )

    async def exercise():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://gateway.test"
        ) as client:
            results = await asyncio.gather(
                *(
                    client.post(
                        "/mcp",
                        headers={
                            **request.headers,
                            "x-evander-session-id": request.query_params["session_id"],
                            "mcp-session-id": "shared-transport",
                        },
                        json={},
                    )
                    for request in requests
                )
            )
        assert sorted(result.status_code for result in results) == [200, 409]
        winner = next(index for index, result in enumerate(results, 1) if result.status_code == 200)
        assert identities["shared-transport"] == f"session-{winner}"
        binding = runtime.mcp_transport_bindings.get_binding("shared-transport")
        assert binding.bound_session_id == f"session-{winner}"

    asyncio.run(exercise())
