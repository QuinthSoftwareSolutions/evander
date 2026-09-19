from __future__ import annotations

import base64
import asyncio
from contextlib import asynccontextmanager
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import stat
import subprocess
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from mcp.server.fastmcp.exceptions import ToolError
from starlette.applications import Starlette
from starlette.requests import Request

from evander.addons import AddonContext
from evander.events.models import WorkflowNodeEnvelope
from evander.policy import PolicyError
from evander.artifact_spaces import ArtifactSpaceStore
from evander_figma_addon import register_addon
from evander_figma_addon import api as figma_api, mcp as figma_mcp, providers as figma_providers
from evander_figma_addon.auth import OAuth
from evander_figma_addon.client import FigmaClient
from evander_figma_addon.models import DesignRequest, FigmaConflict, FigmaError, Settings, digest
from evander_figma_addon.providers import (
    FigmaChangedProvider,
    FigmaQueryProvider,
    register_providers,
)
from evander_figma_addon.service import Actor, FigmaService
from evander_figma_addon.state import Store

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a3xkAAAAASUVORK5CYII="
)


def settings():
    return Settings.model_validate(
        {
            "connections": {
                "studio": {
                    "client_id": "approved-client",
                    "client_secret_env": "TEST_FIGMA_SECRET",
                    "redirect_uri": "https://evander.test/api/addons/figma-addon/oauth/callback",
                    "rest_token_env": "TEST_FIGMA_REST",
                }
            },
            "targets": {
                "mockups": {
                    "name": "UI mockups",
                    "connection_id": "studio",
                    "file_key": "testFile123",
                    "grants": {
                        "designer-a": {"access": ["read", "comment", "edit"]},
                        "designer-b": {"access": ["read", "comment", "edit"]},
                        "reviewer": {"access": ["read", "comment"]},
                    },
                }
            },
        }
    )


class FakeOAuth:
    def status(self, *_):
        return {"connected": True, "generation": "test-generation", "expires_at": 9999999999}


class Canvas:
    def __init__(self):
        self.state = None
        self.calls = []
        self.lose_next_write = False
        self.comments_list = []
        self.version_list = []

    def client(self, oauth, directory, connection_id, config):
        canvas = self

        class Client(FigmaClient):
            def _mcp(self, name, arguments):
                canvas.calls.append((name, arguments))
                if name == "get_screenshot":
                    return SimpleNamespace(
                        content=[
                            SimpleNamespace(
                                type="image",
                                mimeType="image/png",
                                data=base64.b64encode(PNG).decode(),
                            )
                        ]
                    )
                if name == "get_variable_defs":
                    return SimpleNamespace(structuredContent={"brand": "#112233"}, content=[])
                script = Path(__file__).parent / "fixtures" / "figma_canvas.mjs"
                response = subprocess.run(
                    ["node", str(script)],
                    input=json.dumps({"state": canvas.state, "code": arguments["code"]}),
                    text=True,
                    capture_output=True,
                    check=True,
                    timeout=5,
                )
                payload = json.loads(response.stdout)
                canvas.state = payload["state"]
                if payload.get("error"):
                    raise FigmaError(payload["error"])
                if canvas.lose_next_write and '"action": "edit"' in arguments["code"]:
                    canvas.lose_next_write = False
                    raise FigmaError("Simulated lost response after the mutation")
                return SimpleNamespace(structuredContent=payload["result"], content=[])

            def comments(self, _):
                return json.loads(json.dumps(canvas.comments_list))

            def versions(self, _):
                return canvas.version_list

            def user_id(self):
                return "integration-user"

            def comment(self, _, message, node_id=None, comment_id=None):
                item = {
                    "id": str(len(canvas.comments_list)),
                    "message": message,
                    "user": {"id": self.user_id()},
                }
                canvas.comments_list.append(item)
                return item

        return Client(oauth, directory, connection_id, config)


@pytest.fixture
def studio(tmp_path):
    canvas = Canvas()
    Store(tmp_path).save_settings("root/design", 0, settings())

    def service(session="designer-a", operator=False, directory="root/design"):
        return FigmaService(
            tmp_path,
            Actor(directory, session, operator=operator),
            client_factory=canvas.client,
            oauth=FakeOAuth(),
        )

    owner = service("owner", True)
    check = owner.test_authoring("mockups")
    assert check["status"] == "passed", check
    assert check["cleanup_complete"]
    return service, canvas


def run(service, operation, **kwargs):
    return service.execute({"operation": operation, "target_id": "mockups", **kwargs})


def create_frame(service, request_id="new-frame", parent="0:1"):
    base = run(service, "inspect", node_id=parent)
    result = run(
        service,
        "edit",
        node_id=parent,
        expected_revision=base["revision"],
        request_id=request_id,
        edits=[
            {
                "action": "create",
                "kind": "FRAME",
                "parent_id": parent,
                "properties": {
                    "name": request_id,
                    "width": 500,
                    "height": 300,
                    "layout_mode": "VERTICAL",
                    "padding": 20,
                },
            }
        ],
    )
    assert result["status"] == "succeeded", result
    return result["result"]["createdNodeIds"][0]


def test_gate_requires_real_create_edit_render_readback(tmp_path):
    canvas = Canvas()
    store = Store(tmp_path)
    store.save_settings("root/design", 0, settings())
    author = FigmaService(
        tmp_path,
        Actor("root/design", "designer-a"),
        client_factory=canvas.client,
        oauth=FakeOAuth(),
    )
    with pytest.raises(FigmaError, match="disabled"):
        run(author, "create_area", name="Draft", request_id="a")
    owner = FigmaService(
        tmp_path,
        Actor("root/design", "owner", operator=True),
        client_factory=canvas.client,
        oauth=FakeOAuth(),
    )
    assert owner.test_authoring("mockups")["status"] == "passed"
    assert owner.connection_status("studio")["authoring_ready"]
    assert any(name == "get_screenshot" for name, _ in canvas.calls)
    assert [
        n["name"]
        for n in canvas.state["nodes"].values()
        if n["type"] == "PAGE" and not n.get("removed")
    ] == ["Existing designs"]


def test_separate_proposals_review_and_compose_pinned_snapshots(studio):
    service, _ = studio
    a, b, reviewer = service(), service("designer-b"), service("reviewer")
    first, second = create_frame(a, "proposal-a"), create_frame(b, "proposal-b")
    snap_a = run(a, "snapshot", node_id=first, request_id="snapshot-a")["result"]
    snap_b = run(b, "snapshot", node_id=second, request_id="snapshot-b")["result"]
    imported = run(reviewer, "import_snapshot", snapshot_id=snap_a["snapshot_id"])
    assert {item["filename"] for item in imported["artifacts"]} == {
        "preview.png",
        "design.json",
        "README.md",
    }
    assert all(item["session_id"] == "reviewer" for item in imported["artifacts"])
    assert (
        run(
            reviewer, "comment", message="Use the first layout", node_id=first, request_id="review"
        )["status"]
        == "succeeded"
    )
    integration = run(a, "create_area", name="Combined", request_id="integration")["result"][
        "node_id"
    ]
    base = run(a, "inspect", node_id=integration)
    result = run(
        a,
        "compose",
        node_id=integration,
        expected_revision=base["revision"],
        request_id="compose",
        source_snapshot_ids=[snap_a["snapshot_id"], snap_b["snapshot_id"]],
    )
    assert result["status"] == "succeeded", result
    assert len(result["result"]["createdNodeIds"]) == 2
    state = ArtifactSpaceStore(a.store.runtime_dir).read()
    assert len(state.versions) == 2


def test_one_author_reviewers_cannot_edit_or_forge_extra_fields(studio):
    service, canvas = studio
    frame = create_frame(service())
    reviewer = service("reviewer")
    base = run(reviewer, "inspect", node_id=frame)
    calls = len(canvas.calls)
    with pytest.raises(FigmaError, match="not granted"):
        run(
            reviewer,
            "edit",
            node_id=frame,
            expected_revision=base["revision"],
            request_id="unauthorized",
            edits=[{"action": "update", "node_id": frame, "properties": {"name": "Oops"}}],
        )
    assert len(canvas.calls) == calls
    with pytest.raises(ValueError):
        run(reviewer, "inspect", node_id=frame, directory="root")
    with pytest.raises(ValueError):
        DesignRequest.model_validate(
            {"operation": "edit", "target_id": "mockups", "code": "steal()"}
        )


def test_shared_edit_conflict_then_fresh_revision(studio):
    service, _ = studio
    a, b = service(), service("designer-b")
    frame = create_frame(a)
    base = run(a, "inspect", node_id=frame)
    result = run(
        a,
        "edit",
        node_id=frame,
        expected_revision=base["revision"],
        request_id="first-edit",
        edits=[{"action": "update", "node_id": frame, "properties": {"name": "First"}}],
    )
    assert result["status"] == "succeeded"
    with pytest.raises(FigmaConflict, match="changed"):
        run(
            b,
            "edit",
            node_id=frame,
            expected_revision=base["revision"],
            request_id="second-edit",
            edits=[{"action": "update", "node_id": frame, "properties": {"name": "Second"}}],
        )
    new = run(b, "inspect", node_id=frame)
    result = run(
        b,
        "edit",
        node_id=frame,
        expected_revision=new["revision"],
        request_id="second-edit",
        edits=[{"action": "update", "node_id": frame, "properties": {"name": "Second"}}],
    )
    assert result["status"] == "succeeded"


def test_duplicate_requests_and_lost_write_response_are_not_replayed(studio):
    service, canvas = studio
    author = service()
    frame = create_frame(author)
    base = run(author, "inspect", node_id=frame)
    args = dict(
        node_id=frame,
        expected_revision=base["revision"],
        request_id="lost",
        edits=[
            {
                "action": "create",
                "kind": "TEXT",
                "parent_id": frame,
                "properties": {"characters": "Only once"},
            }
        ],
    )
    canvas.lose_next_write = True
    result = run(author, "edit", **args)
    assert result["status"] == "uncertain"
    calls = len(canvas.calls)
    assert run(author, "edit", **args)["status"] == "uncertain"
    assert len(canvas.calls) == calls
    assert (
        len([n for n in canvas.state["nodes"].values() if n.get("characters") == "Only once"]) == 1
    )
    with pytest.raises(FigmaConflict, match="uncertain"):
        run(service("designer-b"), "create_area", name="Wait", request_id="wait")
    owner = service("owner", True)
    observed = run(owner, "reconcile", operation_id=result["operation_id"])
    assert observed["status"] == "uncertain"
    resolved = owner.resolve_uncertain(
        "mockups", result["operation_id"], digest(observed["reconciliation"])
    )
    assert resolved["status"] == "reconciled"
    assert run(author, "edit", **args)["status"] == "reconciled"


def test_snapshot_is_immutable_and_changed_proposals_cannot_be_composed(studio):
    service, _ = studio
    author = service()
    frame = create_frame(author)
    snapshot = run(author, "snapshot", node_id=frame, request_id="snapshot")["result"]
    original = author._bundle(snapshot)["design.json"]
    base = run(author, "inspect", node_id=frame)
    run(
        author,
        "edit",
        node_id=frame,
        expected_revision=base["revision"],
        request_id="change",
        edits=[{"action": "update", "node_id": frame, "properties": {"name": "Changed later"}}],
    )
    assert author._bundle(snapshot)["design.json"] == original
    page = run(author, "inspect", node_id="0:1")
    result = run(
        author,
        "compose",
        node_id="0:1",
        expected_revision=page["revision"],
        request_id="stale-compose",
        source_snapshot_ids=[snapshot["snapshot_id"]],
    )
    assert result["status"] == "rejected"


def test_revoked_access_cross_directory_and_root_constraints(studio):
    service, _ = studio
    a, b = service(), service("designer-b")
    first, second = create_frame(a, "first"), create_frame(a, "second")
    revision, config = a.store.settings(a.actor.directory)
    config.targets["mockups"].grants["designer-b"].edit_roots = [first]
    a.store.save_settings(a.actor.directory, revision, config)
    other = run(b, "inspect", node_id=second)
    with pytest.raises(FigmaError, match="outside"):
        run(
            b,
            "edit",
            node_id=second,
            expected_revision=other["revision"],
            request_id="outside",
            edits=[{"action": "update", "node_id": second, "properties": {"name": "No"}}],
        )
    with pytest.raises(FigmaError, match="Unknown"):
        run(service(directory="root/other"), "inspect", node_id=first)
    revision, config = a.store.settings(a.actor.directory)
    config.targets["mockups"].grants.pop("designer-b")
    a.store.save_settings(a.actor.directory, revision, config)
    with pytest.raises(FigmaError, match="not granted"):
        run(b, "history")


def test_two_agents_cannot_apply_the_same_base_concurrently(studio):
    service, _ = studio
    frame = create_frame(service())
    base = run(service(), "inspect", node_id=frame)

    def edit(session):
        try:
            return run(
                service(session),
                "edit",
                node_id=frame,
                expected_revision=base["revision"],
                request_id=session,
                edits=[{"action": "update", "node_id": frame, "properties": {"name": session}}],
            )["status"]
        except FigmaConflict:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(edit, ["designer-a", "designer-b"])) == ["conflict", "succeeded"]


def test_oauth_pkce_operator_binding_refresh_and_private_storage(tmp_path):
    store = Store(tmp_path)
    config = settings()
    store.save_settings("root/design", 0, config)
    requests = []

    def handler(request):
        requests.append(request)
        data = parse_qs(request.content.decode())
        assert request.url.host == "api.figma.com"
        assert request.headers["authorization"].startswith("Basic ")
        assert data["grant_type"][0] in {"authorization_code", "refresh_token"}
        return httpx.Response(
            200,
            json={
                "access_token": "test-access",
                "refresh_token": "test-refresh",
                "expires_in": 1,
                "token_type": "Bearer",
            },
        )

    oauth = OAuth(
        store, transport=httpx.MockTransport(handler), secret_resolver=lambda _: "test-secret"
    )
    conn = config.connections["studio"]
    url = oauth.start("root/design", "studio", conn, "owner")
    query = parse_qs(urlsplit(url).query)
    assert query["code_challenge_method"] == ["S256"]
    assert query["client_id"] == ["approved-client"]
    state = query["state"][0]
    with pytest.raises(FigmaError):
        oauth.complete(state, "code", "another-operator")
    oauth.complete(state, "code", "owner")
    assert stat.S_IMODE(oauth.credentials.path.stat().st_mode) == 0o600
    assert oauth.token("root/design", "studio", conn) == "test-access"
    assert len(requests) == 2
    with pytest.raises(FigmaError):
        oauth.complete(state, "code", "owner")
    pending_url = oauth.start("root/design", "studio", conn, "owner")
    pending_state = parse_qs(urlsplit(pending_url).query)["state"][0]
    oauth.disconnect("root/design", "studio")
    assert not oauth.status("root/design", "studio", conn)["connected"]
    with pytest.raises(FigmaError, match="expired"):
        oauth.complete(pending_state, "code", "owner")


def test_rest_does_not_leak_credentials_or_follow_redirects(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_FIGMA_REST", "test-sensitive-value")
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(302, headers={"Location": "https://other.test/"})

    client = FigmaClient(
        None,
        "root",
        "studio",
        settings().connections["studio"],
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(FigmaError) as exc:
        client.comments("testFile123")
    assert "test-sensitive-value" not in str(exc.value)
    assert len(seen) == 1


def test_external_poll_baseline_dedup_and_self_feedback_suppression(studio):
    service, canvas = studio
    owner = service("owner", True)
    owner.poll_external("mockups", interval=0)
    canvas.comments_list.append({"id": "human", "message": "Please simplify the header"})
    run(service(), "comment", message="Done", request_id="comment")
    canvas.version_list.append({"id": "v1", "label": "Approved draft"})
    owner.poll_external("mockups", interval=0)
    owner.poll_external("mockups", interval=0)
    external = [
        r
        for r in owner.store.events("test", "root/design", "mockups")
        if r["payload"]["origin"] == "external"
    ]
    assert [r["payload"]["kind"] for r in external] == ["comment.created", "version.created"]


def test_registration_and_schema_catalog():
    addon = register_addon()
    assert addon.addon_id == "figma-addon"
    providers = register_providers()
    assert {n.type for n in providers.workflow_editor_catalog_entries} == {
        "query:figma:read",
        "effect:figma:execute",
        "trigger:figma:changed",
    }
    assert len(addon.mcp_tool_catalog) == 3


def provider_context(runtime_dir, directory="root/design"):
    sessions = {
        id: SimpleNamespace(session_id=id, directory_path="root/design")
        for id in ("designer-a", "designer-b", "reviewer")
    }
    sessions["outsider"] = SimpleNamespace(session_id="outsider", directory_path="root/other")
    return SimpleNamespace(
        resolved_config=SimpleNamespace(runtime_dir=runtime_dir),
        metadata={"directory_path": directory, "workflow_trace_id": "trace-1"},
        session_lookup=SimpleNamespace(read_all_sessions=lambda: sessions),
    )


def test_workflow_runtime_enforces_session_scope_and_read_only_queries(studio, monkeypatch):
    service, canvas = studio

    def factory(runtime_dir, actor):
        return FigmaService(runtime_dir, actor, client_factory=canvas.client, oauth=FakeOAuth())

    monkeypatch.setattr(figma_providers, "FigmaService", factory)
    query = FigmaQueryProvider()
    query.initialize(
        WorkflowNodeEnvelope(
            id="read",
            display_name="Read",
            type="query:figma:read",
            config={
                "session_id": "reviewer",
                "request": {"operation": "variables", "target_id": "mockups", "node_id": "0:1"},
            },
        ),
        provider_context(service().store.runtime_dir),
    )
    assert query.execute()["variables_and_styles"] == {"brand": "#112233"}
    with pytest.raises(FigmaError, match="Session must belong"):
        query.execute(resolved_config={"session_id": "outsider"})
    with pytest.raises(FigmaError, match="cannot perform effects"):
        query.execute(
            resolved_config={
                "request": {
                    "operation": "create_area",
                    "target_id": "mockups",
                    "name": "No",
                    "request_id": "q",
                }
            }
        )
    effect = figma_providers.FigmaEffectProvider()
    effect.initialize(
        WorkflowNodeEnvelope(
            id="edit",
            display_name="Edit",
            type="effect:figma:execute",
            config={
                "session_id": "reviewer",
                "request": {
                    "operation": "create_area",
                    "target_id": "mockups",
                    "name": "No",
                    "request_id": "e",
                },
            },
        ),
        query.runtime_context,
    )
    with pytest.raises(FigmaError, match="not granted"):
        effect.execute()
    assert effect.execute(resolved_config={"session_id": "designer-a"})["result"]["node_id"]


def test_trigger_redelivery_out_of_order_ack_and_target_replacement(studio):
    service, _ = studio
    owner = service("owner", True)
    definition = WorkflowNodeEnvelope(
        id="changed",
        display_name="Changes",
        type="trigger:figma:changed",
        config={"target_id": "mockups", "origin": "external", "kinds": ["comment.created"]},
    )
    provider = FigmaChangedProvider()
    context = provider_context(owner.store.runtime_dir)
    provider.initialize(definition, context)
    for index, file_key, origin in [
        (1, "oldFile123", "external"),
        (2, "testFile123", "external"),
        (3, "testFile123", "external"),
        (4, "testFile123", "evander"),
    ]:
        owner.store.event(
            str(index),
            "root/design",
            "mockups",
            {
                "kind": "comment.created",
                "file_key": file_key,
                "origin": origin,
                "created_at": "2026-09-19T12:00:00+00:00",
            },
        )
    first, second = provider.poll()
    provider.acknowledge_published_event(second)
    restored = FigmaChangedProvider()
    restored.initialize(definition, context)
    assert [e.event_id for e in restored.poll()] == [first.event_id]
    restored.acknowledge_published_event(first)
    assert restored.poll() == []


def test_mcp_uses_transport_session_and_policy(studio, monkeypatch):
    service, canvas = studio
    callbacks, checked = {}, []

    class Server:
        _evander_transport_session_map = {"transport-1": "reviewer"}

        def tool(self, *, name, **_):
            def register(callback):
                callbacks[name] = callback
                return callback

            return register

    def require(session_id, tool):
        checked.append((session_id, tool))
        if tool == "figma_execute":
            raise PolicyError("Tool is not allowed")
        return SimpleNamespace(session_id=session_id, directory_path="root/design")

    monkeypatch.setattr(
        figma_mcp,
        "FigmaService",
        lambda root, actor: FigmaService(
            root, actor, client_factory=canvas.client, oauth=FakeOAuth()
        ),
    )
    runtime = service().store.runtime_dir
    figma_mcp.register_mcp_tools(
        Server(),
        AddonContext(
            addon_id="figma-addon",
            runtime_dir=runtime,
            addon_runtime_dir=service().store.root,
            service=SimpleNamespace(policy=SimpleNamespace(require_tool=require)),
        ),
    )
    request = Request(
        {
            "type": "http",
            "path": "/mcp",
            "headers": [(b"mcp-session-id", b"transport-1")],
            "query_string": b"",
        }
    )
    context = SimpleNamespace(request_context=SimpleNamespace(request=request))
    result = callbacks["figma_list_targets"](ctx=context)
    assert result["targets"][0]["access"] == ["read", "comment"]
    assert checked == [("reviewer", "figma_list_targets")]
    with pytest.raises(ToolError, match="bound"):
        callbacks["figma_list_targets"](session_id="designer-a", ctx=context)
    with pytest.raises(ToolError, match="not allowed"):
        callbacks["figma_execute"](
            DesignRequest(
                operation="comment", target_id="mockups", message="Hi", request_id="blocked"
            ),
            ctx=context,
        )


def test_admin_permissions_revision_and_directory_scoped_oauth(tmp_path, monkeypatch):
    from evander.admin.web.rbac import ADMIN_OWNER_PERMISSION

    store = Store(tmp_path)
    store.save_settings("root/design", 0, settings())

    class Auth:
        def require(self, request):
            return {"login": request.headers.get("x-user", "owner")}

        def require_permission(self, request, permission, *, directory_path):
            if directory_path != "root/design" or (
                permission == ADMIN_OWNER_PERMISSION and self.require(request)["login"] != "owner"
            ):
                raise PermissionError()

    oauth = OAuth(
        store,
        secret_resolver=lambda _: "synthetic-secret",
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200, json={"access_token": "synthetic-access", "expires_in": 3600}
            )
        ),
    )
    monkeypatch.setattr(figma_api, "OAuth", lambda _: oauth)
    monkeypatch.setattr(
        figma_api, "FigmaService", lambda root, actor: FigmaService(root, actor, oauth=oauth)
    )
    context = AddonContext(
        addon_id="figma-addon", runtime_dir=tmp_path, addon_runtime_dir=store.root, auth=Auth()
    )
    app = Starlette(routes=figma_api.admin_api_routes(context))

    async def check():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://evander.test"
        ) as client:
            assert (
                await client.get("/settings?directory=root/design", headers={"x-user": "reader"})
            ).status_code == 403
            assert (
                await client.get(
                    "/history?directory=root/design&target_id=mockups", headers={"x-user": "reader"}
                )
            ).status_code == 200
            assert (await client.get("/settings?directory=root/other")).status_code == 403
            result = await client.get("/settings?directory=root/design")
            assert result.json()["revision"] == 1
            assert (
                await client.put(
                    "/settings?directory=root/design", json={"settings": settings().model_dump()}
                )
            ).status_code == 428
            assert (
                await client.put(
                    "/settings?directory=root/design",
                    json={"expected_revision": 0, "settings": settings().model_dump()},
                )
            ).status_code == 409
            started = await client.post(
                "/oauth/start?directory=root/design", json={"connection_id": "studio"}
            )
            state = parse_qs(urlsplit(started.json()["authorize_url"]).query)["state"][0]
            callback = await client.get(
                "/oauth/callback", params={"code": "synthetic-code", "state": state}
            )
            assert callback.status_code == 303  # No root owner permission needed.
            assert callback.headers["location"].endswith("directory=root%2Fdesign")
            result = (await client.get("/settings?directory=root/design")).json()
            assert result["connections"]["studio"]["connected"]
            assert not result["connections"]["studio"]["authoring_ready"]
            assert "synthetic-access" not in json.dumps(result)

    asyncio.run(asyncio.wait_for(check(), 15))


def test_official_mcp_transport_client_identity_and_schema_validation(tmp_path, monkeypatch):
    from evander_figma_addon import client as module

    calls = []

    @asynccontextmanager
    async def transport(url, **kwargs):
        assert url == "https://mcp.figma.com/mcp"
        assert kwargs["headers"] == {"Authorization": "Bearer synthetic-token"}
        yield None, None, None

    class Session:
        def __init__(self, *_, client_info):
            assert client_info.name == "evander"

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        async def initialize(self):
            pass

        async def list_tools(self, cursor=None):
            if cursor is None:
                return SimpleNamespace(tools=[], nextCursor="second")
            return SimpleNamespace(
                nextCursor=None,
                tools=[
                    SimpleNamespace(
                        name="use_figma",
                        inputSchema={
                            "type": "object",
                            "required": ["fileKey"],
                            "properties": {"fileKey": {"type": "string"}},
                        },
                    )
                ],
            )

        async def call_tool(self, name, arguments):
            calls.append((name, arguments))
            return SimpleNamespace(isError=False, model_dump_json=lambda: "{}")

    monkeypatch.setattr(module, "streamablehttp_client", transport)
    monkeypatch.setattr(module, "ClientSession", Session)
    client = FigmaClient(
        SimpleNamespace(token=lambda *_: "synthetic-token"),
        "root",
        "studio",
        settings().connections["studio"],
    )
    client._mcp("use_figma", {"fileKey": "testFile123"})
    with pytest.raises(FigmaError):
        client._mcp("use_figma", {})
    assert len(calls) == 1


def test_literal_text_and_complete_batch_scope_validation(studio):
    service, canvas = studio
    a = service()
    frame = create_frame(a)
    literal = 'Mockup `); figma.root.remove(); // ${42} \\ <script> & "text"'
    base = run(a, "inspect", node_id=frame)
    result = run(
        a,
        "edit",
        node_id=frame,
        expected_revision=base["revision"],
        request_id="literal",
        edits=[
            {
                "action": "create",
                "kind": "TEXT",
                "parent_id": frame,
                "properties": {"characters": literal, "font_size": 22},
            }
        ],
    )
    assert result["status"] == "succeeded"
    assert any(n.get("characters") == literal for n in run(a, "inspect", node_id=frame)["nodes"])
    client = canvas.client(FakeOAuth(), "root/design", "studio", settings().connections["studio"])
    before = run(a, "inspect", node_id=frame)["nodes"]
    with pytest.raises(FigmaError, match="Outside inspected"):
        client.script(
            "testFile123",
            {
                "action": "edit",
                "node_id": frame,
                "expected": before,
                "edits": [
                    {
                        "action": "update",
                        "node_id": frame,
                        "properties": {"name": "Must not apply"},
                    },
                    {"action": "update", "node_id": "0:1", "properties": {"name": "Escape"}},
                ],
            },
        )
    assert run(a, "inspect", node_id=frame)["nodes"] == before


def test_comment_reconciliation_after_lost_response_and_missing_comment(studio, monkeypatch):
    service, canvas = studio
    owner = service("owner", True)
    real = canvas.client

    def factory(*args):
        client = real(*args)

        def fail(*_):
            raise FigmaError("Lost response")

        client.comment = fail
        return client

    author = service()
    author.client_factory = factory
    lost = run(author, "comment", message="Review", request_id="lost-comment")
    observed = run(owner, "reconcile", operation_id=lost["operation_id"])
    assert observed["reconciliation"]["comments"] == []
    canvas.comments_list.append(
        {
            "id": "1",
            "message": f"Review\n\n[Evander designer-a; operation {lost['operation_id']} ]",
            "user": {"id": "integration-user"},
        }
    )
    with pytest.raises(FigmaConflict, match="Comments changed"):
        owner.resolve_uncertain("mockups", lost["operation_id"], digest(observed["reconciliation"]))
    canvas.comments_list[0]["message"] = (
        f"Review\n\n[Evander designer-a; operation {lost['operation_id']}]"
    )
    found = run(owner, "reconcile", operation_id=lost["operation_id"])
    assert found["status"] == "succeeded"
    assert any(
        r["payload"].get("operation_id") == lost["operation_id"]
        for r in owner.store.events("reconciled-comments", "root/design", "mockups")
    )
    with pytest.raises(FigmaConflict, match="no longer"):
        owner.resolve_uncertain("mockups", lost["operation_id"], digest(observed["reconciliation"]))


def test_reconcile_deleted_design_and_reconnect_invalidates_gate(studio):
    service, canvas = studio
    a = service()
    frame = create_frame(a)
    base = run(a, "inspect", node_id=frame)
    canvas.lose_next_write = True
    result = run(
        a,
        "edit",
        node_id=frame,
        expected_revision=base["revision"],
        request_id="deleted",
        edits=[{"action": "update", "node_id": frame, "properties": {"name": "Updated"}}],
    )
    canvas.state["nodes"][frame]["removed"] = True
    owner = service("owner", True)
    observed = run(owner, "reconcile", operation_id=result["operation_id"])
    assert observed["reconciliation"]["nodes"] is None
    assert (
        owner.resolve_uncertain(
            "mockups", result["operation_id"], digest(observed["reconciliation"])
        )["status"]
        == "reconciled"
    )
    a.oauth = SimpleNamespace(
        status=lambda *_: {"connected": True, "generation": "new", "expires_at": 9999999999}
    )
    assert not a.connection_status("studio")["authoring_ready"]


def test_polling_failure_persists_backoff(studio):
    service, canvas = studio
    owner = service("owner", True)
    count = []
    real = canvas.client

    def factory(*args):
        client = real(*args)

        def fail(*_):
            count.append(True)
            raise FigmaError("Rate limited")

        client.comments = fail
        return client

    owner.client_factory = factory
    with pytest.raises(FigmaError):
        owner.poll_external("mockups")
    owner.poll_external("mockups")
    assert len(count) == 1


def test_example_packs_validate_with_registered_providers():
    from evander.workflow.packs import load_workflow_pack
    from evander.workflow.plugins import load_provider_plugins
    from evander.workflow.validation import validate_workflow_graph

    catalog = load_provider_plugins(
        include_builtin=True,
        include_entry_points=False,
        module_names=["evander_figma_addon.providers"],
    )
    classes = {**catalog.trigger_providers, **catalog.effect_providers, **catalog.query_providers}
    paths = sorted(
        (Path(__file__).resolve().parents[1] / "config/workflow-packs").glob(
            "figma-*.v1-example.json"
        )
    )
    assert len(paths) == 3
    for path in paths:
        pack = load_workflow_pack(path.parents[2], path.name)
        nodes = [item.node for item in pack.ir.nodes]
        for node in nodes:
            classes[node.type].validate_definition(node)
        assert (
            validate_workflow_graph(
                nodes=nodes,
                edges=pack.ir.edges,
                trigger_node_ids=[n.id for n in nodes if n.type.startswith("trigger:")],
                node_capabilities={
                    n.id: classes[n.type].node_capability()
                    for n in nodes
                    if hasattr(classes[n.type], "node_capability")
                },
            )
            == []
        )
        assert all(
            edge.source_port == "completed"
            for edge in pack.ir.edges
            if next(n for n in nodes if n.id == edge.source_id).type == "effect:agent:prompt"
        )


def test_directory_switch_and_save_races():
    result = subprocess.run(
        ["node", str(Path(__file__).parent / "fixtures" / "figma_ui.mjs")],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def populate_frame(service, node_id, count=20):
    base = run(service, "inspect", node_id=node_id)
    result = run(
        service,
        "edit",
        node_id=node_id,
        expected_revision=base["revision"],
        request_id="populate-" + node_id,
        edits=[{"action": "create", "kind": "RECTANGLE", "parent_id": node_id}] * count,
    )
    assert result["status"] == "succeeded", result


@pytest.mark.parametrize("action", ["clone", "instance", "grow_source_then_clone"])
def test_oversized_batches_are_rejected_before_mutation_and_do_not_block_writes(studio, action):
    services, canvas = studio
    author = services()
    root = create_frame(author)
    source = create_frame(author, "source", parent=root)
    populate_frame(author, source)
    edit = {"action": "clone", "node_id": source, "parent_id": root}
    edits = [edit] * 49
    if action in {"instance", "grow_source_then_clone"}:
        component = create_frame(author, "component")
        populate_frame(author, component)
        canvas.state["nodes"][component]["type"] = "COMPONENT"
        instance = {
            "action": "create",
            "kind": "INSTANCE",
            "component_id": component,
            "parent_id": root if action == "instance" else source,
        }
        edits = [instance] * 49 if action == "instance" else [instance, *([edit] * 30)]
    base = run(author, "inspect", node_id=root)
    original = json.dumps(canvas.state, sort_keys=True)
    result = run(
        author,
        "edit",
        node_id=root,
        expected_revision=base["revision"],
        request_id="oversized",
        edits=edits,
    )
    assert result["status"] == "rejected", result
    assert "1000-node" in result["error"]
    assert json.dumps(canvas.state, sort_keys=True) == original
    assert not author.store.unresolved("testFile123")
    # The rejected batch must not leave the file requiring operator recovery.
    next_result = run(
        author,
        "edit",
        node_id=root,
        expected_revision=base["revision"],
        request_id="smaller-batch",
        edits=[edit],
    )
    assert next_result["status"] == "succeeded", next_result


def test_node_limit_accounts_for_removals_before_cloning(studio):
    services, _ = studio
    author = services()
    root = create_frame(author)
    source = create_frame(author, "source", parent=root)
    payload = create_frame(author, "payload", parent=source)
    populate_frame(author, payload)
    base = run(author, "inspect", node_id=root)
    result = run(
        author,
        "edit",
        node_id=root,
        expected_revision=base["revision"],
        request_id="remove-then-clone",
        edits=[
            {"action": "remove", "node_id": payload},
            *([{"action": "clone", "node_id": source, "parent_id": root}] * 49),
        ],
    )
    assert result["status"] == "succeeded", result
    assert len(run(author, "inspect", node_id=root)["nodes"]) == 51


def test_oversized_composition_is_rejected_without_applying_any_proposals(studio):
    services, canvas = studio
    author = services()
    source = create_frame(author, "source")
    populate_frame(author, source, count=50)
    snapshot = run(author, "snapshot", node_id=source, request_id="proposal")["result"]
    destination = create_frame(author, "destination")
    base = run(author, "inspect", node_id=destination)
    original = json.dumps(canvas.state, sort_keys=True)
    result = run(
        author,
        "compose",
        node_id=destination,
        expected_revision=base["revision"],
        request_id="oversized-composition",
        source_snapshot_ids=[snapshot["snapshot_id"]] * 20,
    )
    assert result["status"] == "rejected", result
    assert json.dumps(canvas.state, sort_keys=True) == original
    assert not author.store.unresolved("testFile123")


@pytest.mark.parametrize("existing_count,expected_status", [(999, "succeeded"), (1000, "rejected")])
def test_create_area_obeys_the_inspection_limit(studio, existing_count, expected_status):
    services, canvas = studio
    author = services()
    root = create_frame(author)
    # Seed an already populated file; only topology matters for this boundary.
    for index in range(existing_count - 1):
        node_id = f"2:{index}"
        canvas.state["nodes"][node_id] = {
            "id": node_id,
            "type": "RECTANGLE",
            "name": "Existing rectangle",
            "parent_id": root,
            "children": [],
            "x": 0,
            "y": 0,
            "width": 1,
            "height": 1,
        }
        canvas.state["nodes"][root]["children"].append(node_id)
    base = run(author, "inspect", node_id=root)
    result = run(
        author,
        "create_area",
        name="New area",
        node_id=root,
        expected_revision=base["revision"],
        request_id="area-at-limit",
    )
    assert result["status"] == expected_status, result
    assert not author.store.unresolved("testFile123")
    assert len(run(author, "inspect", node_id=root)["nodes"]) == 1000


def test_comment_recovery_requires_the_exact_message_and_pinned_rest_user(studio):
    services, canvas = studio
    author, owner = services(), services("owner", True)
    original = canvas.client

    def client_factory(*args):
        client = original(*args)

        def lost_response(*_):
            raise FigmaError("Synthetic lost POST")

        client.comment = lost_response
        return client

    author.client_factory = client_factory
    owner.poll_external("mockups", interval=0)
    result = run(author, "comment", message="Approved design A", request_id="lost-comment")
    marker = f"[Evander designer-a; operation {result['operation_id']}]"
    expected = f"Approved design A\n\n{marker}"
    mismatches = [
        {"id": "quote", "message": f"Did this fail? {marker}", "user": {"id": "integration-user"}},
        {"id": "copy", "message": expected, "user": {"id": "human-user"}},
        {"id": "no-author", "message": expected},
    ]
    for comment in mismatches:
        canvas.comments_list[:] = [comment]
        observed = run(owner, "reconcile", operation_id=result["operation_id"])
        assert observed["status"] == "uncertain"
        assert observed["reconciliation"]["requires_operator_resolution"]
    canvas.comments_list[:] = mismatches
    owner.poll_external("mockups", interval=0)
    events = author.store.events("review", "root/design", "mockups")
    assert {event["payload"]["item"]["id"] for event in events} == {"quote", "copy", "no-author"}
    assert all(event["payload"]["origin"] == "external" for event in events)
    canvas.comments_list.append(
        {"id": "actual", "message": expected, "user": {"id": "integration-user"}}
    )
    found = run(owner, "reconcile", operation_id=result["operation_id"])
    assert found["status"] == "succeeded"
    assert found["result"]["id"] == "actual"
    owner.poll_external("mockups", interval=0)
    events = author.store.events("review", "root/design", "mockups")
    assert len(events) == 4  # Three human comments and one recovered delivery, without an echo.


def test_comment_user_lookup_failure_does_not_create_an_uncertain_write(studio):
    services, canvas = studio
    author = services()
    original = canvas.client

    def client_factory(*args):
        client = original(*args)

        def unauthorized():
            raise FigmaError("Missing current_user:read scope")

        client.user_id = unauthorized
        return client

    author.client_factory = client_factory
    with pytest.raises(FigmaError, match="current_user:read"):
        run(author, "comment", message="Review", request_id="missing-user-scope")
    assert not author.store.unresolved("testFile123")
    assert canvas.comments_list == []


def test_rest_account_identity_uses_the_comment_credential(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_FIGMA_REST", "synthetic-rest-token")

    def handler(request):
        assert str(request.url) == "https://api.figma.com/v1/me"
        assert request.headers["X-Figma-Token"] == "synthetic-rest-token"
        return httpx.Response(200, json={"id": "integration-user"})

    client = FigmaClient(
        None,
        "root",
        "studio",
        settings().connections["studio"],
        transport=httpx.MockTransport(handler),
    )
    assert client.user_id() == "integration-user"
