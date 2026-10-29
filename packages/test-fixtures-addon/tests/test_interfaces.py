import asyncio
from datetime import timedelta
from types import SimpleNamespace

import httpx
import pytest
from starlette.applications import Starlette
from starlette.routing import Mount

from evander.addons import AddonContext, AddonLoaderConfig
from evander.config import GatewayConfig
from evander.gateway import _MCP_REQUEST_CONTEXT, create_server
from evander.models import SessionManifest, utc_now
from evander.runtime import build_runtime
from evander.session_auth import ensure_session_mcp_auth_token
from evander_test_fixtures_addon import register_addon
from evander_test_fixtures_addon.api import admin_api_routes
from evander_test_fixtures_addon.models import Ensure, Worker
from evander_test_fixtures_addon.client import WorkerClient
from evander_test_fixtures_addon.worker_api import create_app


def request(app, method, path, **kwargs):
    async def run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://fixtures.test"
        ) as client:
            return await asyncio.wait_for(client.request(method, path, **kwargs), 10)

    return asyncio.run(run())


def test_worker_api_auth_validates_scope_and_redacts_all_responses(worker, scope, monkeypatch):
    monkeypatch.setenv(worker.settings.token_env, "synthetic-bearer")
    app = create_app(worker.settings, service=worker, periodic=False)
    assert request(app, "GET", "/healthz").status_code == 401
    headers = {"Authorization": "Bearer synthetic-bearer"}
    assert request(app, "GET", "/healthz", headers=headers).json()["protocol"] == 1
    lease = "fx-" + "a" * 32
    value = Ensure(lease_id=lease, scope=scope, profile="sqlserver-rabbitmq").model_dump()
    result = request(app, "POST", f"/v1/leases/{lease}/ensure", headers=headers, json=value)
    assert result.status_code == 200
    assert result.json()["state"] == "ready"
    assert "secret-" not in result.text and "password" not in result.text
    value["scope"]["worker_id"] = "foreign"
    assert (
        request(app, "POST", f"/v1/leases/{lease}/ensure", headers=headers, json=value).status_code
        == 400
    )
    result = request(app, "POST", "/v1/leases/arbitrary-container/release", headers=headers)
    assert result.status_code == 400


def test_sidecar_client_never_reflects_remote_errors_or_redirects(monkeypatch):
    monkeypatch.setenv("FIXTURE_TOKEN", "host-only-secret")
    worker = Worker(
        worker_base_url="http://192.0.2.1:11888",
        fixture_base_url="http://192.0.2.1:11991",
        token_env="FIXTURE_TOKEN",
    )

    def handle(request):
        assert request.headers["Authorization"] == "Bearer host-only-secret"
        return httpx.Response(500, json={"error": "password=private"})

    client = WorkerClient(worker, client=httpx.Client(transport=httpx.MockTransport(handle)))
    from evander_test_fixtures_addon.models import FixtureError

    with pytest.raises(FixtureError) as exc:
        client.call("get", "fx-" + "a" * 32)
    assert "private" not in str(exc.value) and "secret" not in str(exc.value)


def test_actual_authenticated_mcp_discovery_tracks_policy_and_addon_presence(tmp_path):
    config = GatewayConfig(
        runtime_dir=tmp_path / "runtime",
        addon_loader=AddonLoaderConfig(
            modules=["evander_team_addon", "evander_test_fixtures_addon"],
            package_dirs=[],
            enabled_addon_ids=["team-addon", "test-fixtures-addon"],
        ),
    )
    runtime = build_runtime(config)
    tools = [tool.name for tool in register_addon().mcp_tool_catalog]
    session, proof = ensure_session_mcp_auth_token(
        SessionManifest(
            session_id="fixture-agent",
            directory_path="root",
            expires_at=utc_now() + timedelta(hours=1),
            allowed_tools=tools,
        )
    )
    runtime.session_repository.upsert_session(session)
    server = create_server(runtime)
    token = _MCP_REQUEST_CONTEXT.set(
        SimpleNamespace(
            headers={"x-evander-mcp-session-token": proof},
            query_params={"session_id": session.session_id},
        )
    )
    try:
        visible = {tool.name for tool in server._tool_manager.list_tools()}
        assert set(tools) <= visible
        for tool in asyncio.run(server.list_tools()):
            if tool.name in tools:
                assert "session_id" not in tool.input_schema.get("properties", {})
                assert "worker_base_url" not in tool.input_schema.get("properties", {})
            if tool.name == "test_fixtures_acquire":
                assert set(tool.input_schema["properties"]["profile"]["enum"]) == {
                    "sqlserver",
                    "rabbitmq",
                    "sqlserver-rabbitmq",
                }
            if tool.name == "test_fixtures_control":
                duration = tool.input_schema["properties"]["duration_seconds"]
                assert (duration["default"], duration["minimum"], duration["maximum"]) == (
                    60,
                    1,
                    120,
                )
        runtime.session_repository.upsert_session(
            session.model_copy(update={"allowed_tools": ["test_fixtures_acquire"]})
        )
        visible = {tool.name for tool in server._tool_manager.list_tools()}
        assert "test_fixtures_get" in visible
        assert "test_fixtures_control" not in visible
        runtime.session_repository.upsert_session(session.model_copy(update={"allowed_tools": []}))
        assert not (set(tools) & {tool.name for tool in server._tool_manager.list_tools()})
    finally:
        _MCP_REQUEST_CONTEXT.reset(token)
    absent = create_server(
        build_runtime(
            config.model_copy(
                update={
                    "runtime_dir": tmp_path / "absent",
                    "addon_loader": AddonLoaderConfig(package_dirs=[], modules=[]),
                }
            )
        )
    )
    assert not (set(tools) & {tool.name for tool in absent._tool_manager.list_tools()})


class Auth:
    def require(self, request):
        return {"login": "test"}

    def require_permission(self, request, permission, **kwargs):
        if request.headers.get("x-deny"):
            raise PermissionError


def test_admin_api_checks_permission_origin_and_revision(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "evander_test_fixtures_addon.api.request_has_privileged_operations", lambda **_: True
    )
    context = AddonContext(
        addon_id="test-fixtures-addon",
        runtime_dir=tmp_path,
        addon_runtime_dir=tmp_path / "addons" / "test-fixtures-addon",
        service=None,
        auth=Auth(),
    )
    app = Starlette(
        routes=[Mount("/api/addons/test-fixtures-addon", routes=admin_api_routes(context))]
    )
    path = "/api/addons/test-fixtures-addon/settings"
    assert request(app, "GET", path, headers={"x-deny": "1"}).status_code == 403
    initial = request(app, "GET", path).json()
    body = {"expected_revision": 0, "settings": initial["settings"]}
    assert (
        request(app, "PUT", path, json=body, headers={"Origin": "https://foreign.test"}).status_code
        == 403
    )
    assert request(app, "PUT", path, json={"settings": initial["settings"]}).status_code == 428
    assert request(app, "PUT", path, json=body).status_code == 200
    assert request(app, "PUT", path, json=body).status_code == 409


def test_workflow_pack_validates_through_registered_provider_contracts():
    from pathlib import Path
    from evander.workflow.packs import WorkflowPack

    pack = WorkflowPack.model_validate_json(
        (
            Path(__file__).resolve().parents[1] / "examples/workflow-packs/test-fixtures.v1.json"
        ).read_text()
    )
    assert len(pack.ir.nodes) == 2
    registration = register_addon().workflow_providers()
    for envelope in pack.ir.nodes:
        node = envelope.node
        model = registration.node_definition_resolvers[node.type](node)
        assert model.type == node.type
