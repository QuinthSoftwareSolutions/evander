"""Authorization and readiness at the optional add-on's public boundaries."""

import asyncio
from types import SimpleNamespace
import httpx
from starlette.applications import Starlette
from starlette.routing import Mount
from evander_application_environments_addon import web, readiness
from evander_application_environments_addon.models import Settings


def request(app, method, path, **kwargs):
    async def run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://control.test"
        ) as client:
            return await client.request(method, path, **kwargs)

    return asyncio.run(run())


def test_settings_require_login_directory_permission_and_revision(tmp_path, monkeypatch):
    from evander_application_environments_addon.state import Store

    store = Store(tmp_path)
    grants = set()
    login = [None]
    checked = []

    def authorize(session, permission, directory_path):
        checked.append((permission, directory_path))
        if (permission, directory_path) not in grants:
            raise PermissionError()

    auth = SimpleNamespace(
        config=SimpleNamespace(auth_required=True),
        read_session=lambda request: login[0],
        require_permission=authorize,
    )
    context = SimpleNamespace(runtime_dir=tmp_path, service=None, auth=auth)
    monkeypatch.setattr(web, "Service", lambda *args: SimpleNamespace(store=store))
    app = Starlette(
        routes=[
            Mount(
                "/api/addons/application-environments-addon", routes=web.admin_api_routes(context)
            ),
            Mount("/addons/application-environments-addon", routes=web.admin_ui_routes(context)),
        ]
    )
    path = "/api/addons/application-environments-addon/settings?directory=root/example"
    assert request(app, "GET", "/addons/application-environments-addon/").status_code == 200
    assert (
        request(app, "GET", "/addons/application-environments-addon/environments.js").status_code
        == 200
    )
    assert request(app, "GET", path).status_code == 401
    login[0] = {"login": "operator"}
    assert request(app, "GET", path).status_code == 403
    grants.add(("application_environments.view", "root/example"))
    assert request(app, "GET", path).json()["revision"] == 0
    payload = {"expected_revision": 0, "settings": Settings().model_dump()}
    assert request(app, "PUT", path, json=payload).status_code == 403
    grants.add(("application_environments.manage", "root/example"))
    assert (
        request(
            app, "PUT", path, json=payload, headers={"Origin": "https://foreign.test"}
        ).status_code
        == 409
    )
    assert store.settings("root/example")[0] == 0
    assert request(app, "PUT", path, json=payload).status_code == 200
    assert request(app, "PUT", path, json=payload).status_code == 409
    assert checked[-1] == ("application_environments.manage", "root/example")


def test_worker_api_authenticates_before_any_allocation(tmp_path, monkeypatch):
    from evander_application_environments_addon.worker import WorkerSettings
    from evander_application_environments_addon.worker_api import create_app

    calls = []
    supervisor = SimpleNamespace(create=lambda value: calls.append(value))
    settings = WorkerSettings(worker_id="worker-a", worker_root=tmp_path, runtime_dir=tmp_path)
    monkeypatch.setenv(settings.token_env, "synthetic-private-token")
    app = create_app(settings, supervisor=supervisor)
    path = "/v1/environments/environment-a"
    assert request(app, "POST", path, json={}).status_code == 401
    assert not calls
    response = request(
        app, "GET", "/healthz", headers={"Authorization": "Bearer synthetic-private-token"}
    )
    assert response.json()["capabilities"] == {"application_environments": 1}


def test_readiness_checks_worker_identity_without_allocating(tmp_path, monkeypatch):
    settings = Settings(
        allocations_enabled=True,
        workers={
            "worker-a": {
                "base_url": "https://application-worker.test",
                "parent_worker_url": "https://worker.test",
                "token_env": "APPLICATION_TOKEN",
            }
        },
        profiles={"web": {"image": "approved:1"}},
    )
    context = SimpleNamespace(runtime_dir=tmp_path, service=None, directory_path="root/example")
    monkeypatch.setattr(
        readiness,
        "Service",
        lambda *args: SimpleNamespace(
            store=SimpleNamespace(settings=lambda directory: (1, settings))
        ),
    )
    calls = []
    health = {"worker_id": "worker-a", "capabilities": {"application_environments": 1}}

    class Peer:
        def __init__(self, base, token, *, timeout):
            assert 0 < timeout <= 10

        def call(self, method, path):
            calls.append((method, path))
            return health

    monkeypatch.setattr(readiness, "Peer", Peer)
    assert readiness.check_readiness(context).status == "ready"
    assert calls == [("GET", "/healthz")]
    health["worker_id"] = "foreign-worker"
    assert readiness.check_readiness(context).code == "worker_incompatible"


def test_mcp_catalog_registers_typed_creation_without_other_optional_addons(tmp_path):
    from mcp.server.mcpserver import MCPServer
    from evander.addons import AddonContext
    from evander_application_environments_addon import register_addon

    addon = register_addon()
    server = MCPServer("application-test")
    names = addon.mcp_tools(
        server,
        AddonContext(
            addon_id=addon.addon_id,
            runtime_dir=tmp_path,
            addon_runtime_dir=tmp_path / "addons" / addon.addon_id,
        ),
    )
    tools = asyncio.run(server.list_tools())
    assert (
        set(names)
        == {tool.name for tool in tools}
        == {tool.name for tool in addon.mcp_tool_catalog}
    )
    create = next(tool for tool in tools if tool.name == "application_environment_create")
    request_schema = create.input_schema["$defs"]["CreateRequest"]
    assert {"workspace_ref", "profile_id", "request_id", "launch_manifest_path"} <= set(
        request_schema["required"]
    )
    assert request_schema["additionalProperties"] is False


def test_workflow_refuses_owner_from_another_directory_before_launch(tmp_path, monkeypatch):
    from evander_application_environments_addon import providers
    from evander.resources.models import ResourceError

    operation = object.__new__(providers.ApplicationEffect)
    operation.effect_definition = providers.Definition(
        id="application",
        display_name="Application",
        type="effect:application-environment:ensure",
        agent_session_id="agent-a",
        request={},
    )
    operation.runtime_context = SimpleNamespace(
        resolved_config=SimpleNamespace(runtime_dir=tmp_path),
        admin_service=None,
        metadata={"directory_path": "root/other"},
    )
    monkeypatch.setattr(
        providers,
        "Service",
        lambda *args: SimpleNamespace(
            parent=lambda id: SimpleNamespace(session_id=id, directory="root/example")
        ),
    )
    import pytest

    with pytest.raises(ResourceError, match="outside"):
        operation.execute()
