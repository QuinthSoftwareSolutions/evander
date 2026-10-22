from __future__ import annotations

import base64
import asyncio
from contextlib import asynccontextmanager
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
import stat
import subprocess
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import httpx
import httpx2
import pytest
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from starlette.applications import Starlette
from starlette.requests import Request

from evander.addons import AddonContext
from evander.config import GatewayConfig
from evander.events.models import WorkflowNodeEnvelope
from evander.models import ApprovalRequest, SessionManifest
from evander.policy import PolicyEngine
from evander.repositories.session_manifests import JsonSessionManifestRepository
from evander.storage import JsonStore
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
                    "access": ["read", "comment", "edit"],
                }
            },
        }
    )


@pytest.mark.parametrize(
    "grants,access,roots",
    [
        ({}, [], []),
        ({"reader": {"access": ["read", "comment"]}}, ["read", "comment"], []),
        (
            {"reader": {"access": ["read"]}, "inactive": {"access": ["edit"]}},
            ["read"],
            [],
        ),
        (
            {
                "author-a": {"access": ["read", "edit"], "edit_roots": ["1:2"]},
                "author-b": {"access": ["read", "edit"], "edit_roots": ["3:4", "1:2"]},
                "reviewer": {"access": ["read", "comment"], "edit_roots": []},
                "inactive": {"access": ["edit"], "edit_roots": []},
            },
            ["read", "comment", "edit"],
            ["1:2", "3:4"],
        ),
        (
            {
                "author-a": {"access": ["read", "edit"], "edit_roots": ["1:2"]},
                "author-b": {"access": ["read", "edit"], "edit_roots": []},
            },
            ["read", "edit"],
            [],
        ),
    ],
)
def test_legacy_grants_migrate_to_file_permissions_on_read_and_save(
    tmp_path, grants, access, roots
):
    payload = settings().model_dump()
    target = payload["targets"]["mockups"]
    target.pop("access")
    target.pop("edit_roots")
    target["grants"] = grants
    store = Store(tmp_path)
    with store.db() as db:
        db.execute("INSERT INTO settings VALUES (?,?,?)", ("root/design", 7, json.dumps(payload)))
    revision, migrated = store.settings("root/design")
    assert revision == 7
    assert migrated.targets["mockups"].access == access
    assert migrated.targets["mockups"].edit_roots == roots
    assert migrated.connections["studio"].binding() == settings().connections["studio"].binding()
    assert store.save_settings("root/design", revision, migrated) == 8
    with store.db() as db:
        saved = json.loads(db.execute("SELECT payload FROM settings").fetchone()[0])
    assert "grants" not in saved["targets"]["mockups"]
    assert "grants" not in Settings.model_json_schema()["$defs"]["Target"]["properties"]


def test_explicit_file_permissions_override_legacy_grants():
    payload = settings().model_dump()
    target = payload["targets"]["mockups"]
    target.update(access=[], edit_roots=["1:2"], grants={"author": {"access": ["read", "edit"]}})
    migrated = Settings.model_validate(payload).targets["mockups"]
    assert migrated.access == []
    assert migrated.edit_roots == ["1:2"]


def test_legacy_root_overflow_cannot_turn_into_whole_file_editing():
    payload = settings().model_dump()
    target = payload["targets"]["mockups"]
    target.pop("access")
    target.pop("edit_roots")
    target["grants"] = {
        "author-a": {"access": ["read", "edit"], "edit_roots": [f"1:{i}" for i in range(100)]},
        "author-b": {"access": ["read", "edit"], "edit_roots": ["2:1"]},
    }
    with pytest.raises(ValueError, match="exceed 100 editable roots"):
        Settings.model_validate(payload)


def session_policy(runtime_dir):
    config = GatewayConfig(
        runtime_dir=runtime_dir,
        directories_store_path=runtime_dir / "directories.json",
        session_store_path=runtime_dir / "sessions.json",
        approval_store_path=runtime_dir / "approvals.json",
    )
    policy = PolicyEngine(
        config,
        JsonSessionManifestRepository(config.session_store_path),
        JsonStore(config.approval_store_path, ApprovalRequest),
        extra_mcp_tool_names=[tool.name for tool in figma_mcp.MCP_TOOL_CATALOG],
    )
    directories = policy.policy_config.directory_service
    directories.create(parent="root", name="design")
    directories.create(parent="root", name="other")
    directories.create(parent="root/design", name="child")
    for id in ("designer-a", "designer-b", "reviewer", "fresh-agent", "outsider", "child"):
        policy.session_store.upsert_session(
            SessionManifest(
                session_id=id,
                directory_path={"outsider": "root/other", "child": "root/design/child"}.get(
                    id, "root/design"
                ),
                allowed_tools=["figma_list_targets", "figma_query"]
                + ([] if id == "reviewer" else ["figma_execute"]),
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
        )
    return policy


class FakeOAuth:
    def status(self, *_):
        return {"connected": True, "generation": "test-generation", "expires_at": 9999999999}


class CanvasInterpreter:
    """One bounded Node transport; each request starts with a fresh JS context."""

    def __init__(self):
        script = Path(__file__).parent / "fixtures" / "figma_canvas.mjs"
        self.process = subprocess.Popen(
            ["node", str(script), "--worker"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        self.executor = ThreadPoolExecutor(max_workers=1)
        self.timeout = 5

    def _exchange(self, request):
        self.process.stdin.write(json.dumps(request) + "\n")
        self.process.stdin.flush()
        response = self.process.stdout.readline()
        if not response:
            raise AssertionError("Figma canvas worker exited: " + self.process.stderr.read())
        return json.loads(response)

    def execute(self, state, code):
        pending = self.executor.submit(self._exchange, {"state": state, "code": code})
        try:
            return pending.result(timeout=self.timeout)
        except TimeoutError:
            self.process.kill()
            self.process.wait(timeout=5)
            raise AssertionError("Figma canvas operation exceeded its timeout") from None

    def close(self):
        self.executor.shutdown(wait=True, cancel_futures=True)
        try:
            self.process.stdin.close()
        except BrokenPipeError:
            pass
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=5)
        finally:
            self.process.stdout.close()
            self.process.stderr.close()


@pytest.fixture(scope="module")
def canvas_interpreter():
    interpreter = CanvasInterpreter()
    try:
        yield interpreter
    finally:
        interpreter.close()


class Canvas:
    def __init__(self, interpreter):
        self.interpreter = interpreter
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
                                mime_type="image/png",
                                data=base64.b64encode(PNG).decode(),
                            )
                        ]
                    )
                if name == "get_variable_defs":
                    return SimpleNamespace(structured_content={"brand": "#112233"}, content=[])
                payload = canvas.interpreter.execute(canvas.state, arguments["code"])
                canvas.state = payload["state"]
                if payload.get("error"):
                    raise FigmaError(payload["error"])
                assert len(json.dumps(payload["result"]).encode()) < 20000, "MCP output limit"
                if canvas.lose_next_write and '"action": "edit"' in arguments["code"]:
                    canvas.lose_next_write = False
                    raise FigmaError("Simulated lost response after the mutation")
                return SimpleNamespace(structured_content=payload["result"], content=[])

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


def _studio_service(runtime_dir, canvas):
    def service(session="designer-a", operator=False, directory="root/design"):
        return FigmaService(
            runtime_dir,
            Actor(directory, session, operator=operator),
            client_factory=canvas.client,
            oauth=FakeOAuth(),
        )

    return service


@pytest.fixture(scope="module")
def studio_seed(tmp_path_factory, canvas_interpreter):
    """Validate once through the real gate, then freeze data, never mutable services."""
    runtime_dir = tmp_path_factory.mktemp("figma-studio-seed")
    canvas = Canvas(canvas_interpreter)
    store = Store(runtime_dir)
    store.save_settings("root/design", 0, settings())
    service = _studio_service(runtime_dir, canvas)
    owner = service("owner", True)
    check = owner.test_authoring("mockups")
    assert check["status"] == "passed", check
    assert check["cleanup_complete"]
    return store.path.read_bytes(), json.dumps(canvas.state)


def _fresh_studio(runtime_dir, interpreter, seed):
    database, state = seed
    Store(runtime_dir).path.write_bytes(database)
    canvas = Canvas(interpreter)
    canvas.state = json.loads(state)
    return _studio_service(runtime_dir, canvas), canvas


@pytest.fixture
def studio(tmp_path, canvas_interpreter, studio_seed):
    return _fresh_studio(tmp_path, canvas_interpreter, studio_seed)


def test_canvas_worker_resets_js_globals_fonts_and_page_switches(canvas_interpreter):
    first = canvas_interpreter.execute(
        None,
        """
globalThis.previousRequest = true;
await figma.loadFontAsync({family: 'Inter', style: 'Regular'});
await figma.setCurrentPageAsync(figma.currentPage);
const text = figma.createText();
text.characters = 'First request';
return text.id;
""",
    )
    second = canvas_interpreter.execute(
        first["state"],
        f"""
await figma.setCurrentPageAsync(figma.currentPage);
const text = await figma.getNodeByIdAsync({json.dumps(first["result"])});
let unloadedFont = false;
try {{ text.characters = 'Must require a new font load'; }}
catch (error) {{ unloadedFont = error.message === 'Unloaded font'; }}
return {{previousRequest: Boolean(globalThis.previousRequest), unloadedFont,
  characters: text.characters}};
""",
    )
    assert second["result"] == {
        "previousRequest": False,
        "unloadedFont": True,
        "characters": "First request",
    }
    empty = canvas_interpreter.execute(None, "return figma.currentPage.children.length;")
    assert empty["result"] == 0
    assert empty["state"]["next"] == 10


def test_canvas_worker_matches_single_request_transport(canvas_interpreter):
    request = {"state": None, "code": "return figma.createFrame().id;"}
    result = subprocess.run(
        ["node", str(Path(__file__).parent / "fixtures" / "figma_canvas.mjs")],
        input=json.dumps(request),
        capture_output=True,
        text=True,
        check=True,
        timeout=5,
    )
    assert canvas_interpreter.execute(**request) == json.loads(result.stdout)


def test_canvas_cached_nodes_keep_live_properties_and_independent_clones(canvas_interpreter):
    result = canvas_interpreter.execute(
        None,
        """
const frame = figma.createFrame();
frame.name = 'Before';
const rectangle = figma.createRectangle();
frame.appendChild(rectangle);
const clone = frame.clone();
frame.name = 'After';
rectangle.width = 12;
const sameNode = frame === await figma.getNodeByIdAsync(frame.id);
rectangle.remove();
return {sameNode, liveName: (await figma.getNodeByIdAsync(frame.id)).name,
  removedNode: await figma.getNodeByIdAsync(rectangle.id), childCount: frame.children.length,
  cloneName: clone.name, cloneWidth: clone.children[0].width,
  cloneParent: clone.children[0].parent.id === clone.id};
""",
    )
    assert result["result"] == {
        "sameNode": True,
        "liveName": "After",
        "removedNode": None,
        "childCount": 0,
        "cloneName": "Before",
        "cloneWidth": 100,
        "cloneParent": True,
    }


def test_canvas_worker_timeout_reaps_process():
    interpreter = CanvasInterpreter()
    try:
        assert interpreter.execute(None, "return true;")["result"] is True
        interpreter.timeout = 0.05
        with pytest.raises(AssertionError, match="exceeded its timeout"):
            interpreter.execute(None, "await new Promise(() => {});")
        assert interpreter.process.poll() is not None
    finally:
        interpreter.close()


def test_studio_seed_clones_independent_canvas_and_database(
    tmp_path, canvas_interpreter, studio_seed
):
    first, first_canvas = _fresh_studio(tmp_path / "first", canvas_interpreter, studio_seed)
    second, second_canvas = _fresh_studio(tmp_path / "second", canvas_interpreter, studio_seed)
    assert first().connection_status("studio")["authoring_ready"]
    assert second().connection_status("studio")["authoring_ready"]
    created = create_frame(first())
    assert created in first_canvas.state["nodes"]
    assert created not in second_canvas.state["nodes"]
    assert second_canvas.calls == []
    assert not second().store.records("snapshot", "root/design", "mockups", "testFile123")
    run(first(), "snapshot", node_id=created, request_id="first-only")
    assert first().store.records("snapshot", "root/design", "mockups", "testFile123")
    assert not second().store.records("snapshot", "root/design", "mockups", "testFile123")
    _, encoded_state = studio_seed
    assert created not in json.loads(encoded_state)["nodes"]


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


def test_gate_requires_real_create_edit_render_readback(tmp_path, canvas_interpreter):
    canvas = Canvas(canvas_interpreter)
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


def test_read_only_file_cannot_be_edited_or_forge_extra_fields(studio):
    service, canvas = studio
    frame = create_frame(service())
    revision, config = service().store.settings("root/design")
    config.targets["mockups"].access = ["read", "comment"]
    service().store.save_settings("root/design", revision, config)
    reviewer = service("reviewer")
    base = run(reviewer, "inspect", node_id=frame)
    calls = len(canvas.calls)
    with pytest.raises(FigmaError, match="does not allow"):
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
    config.targets["mockups"].edit_roots = [first]
    a.store.save_settings(a.actor.directory, revision, config)
    assert create_frame(b, "inside", parent=first)
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
    config.targets["mockups"].access = []
    a.store.save_settings(a.actor.directory, revision, config)
    assert b.list_targets() == []
    with pytest.raises(FigmaError, match="does not allow"):
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
    return SimpleNamespace(
        resolved_config=SimpleNamespace(runtime_dir=runtime_dir),
        metadata={"directory_path": directory, "workflow_trace_id": "trace-1"},
        admin_service=SimpleNamespace(runtime=SimpleNamespace(policy=session_policy(runtime_dir))),
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
    for session in ("outsider", "child"):
        with pytest.raises(FigmaError, match="Session must belong"):
            query.execute(resolved_config={"session_id": session})
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
    with pytest.raises(FigmaError, match="figma_execute is not allowed"):
        effect.execute()
    assert effect.execute(resolved_config={"session_id": "fresh-agent"})["result"]["node_id"]
    policy = query.runtime_context.admin_service.runtime.policy
    reviewer = policy.get_session("reviewer")
    policy.session_store.upsert_session(reviewer.model_copy(update={"allowed_tools": []}))
    count = len(canvas.calls)
    with pytest.raises(FigmaError, match="figma_query is not allowed"):
        query.execute()
    author = policy.get_session("fresh-agent")
    policy.session_store.upsert_session(author.model_copy(update={"allowed_tools": []}))
    with pytest.raises(FigmaError, match="figma_execute is not allowed"):
        effect.execute(resolved_config={"session_id": "fresh-agent"})
    assert len(canvas.calls) == count
    query.runtime_context.admin_service = None
    with pytest.raises(FigmaError, match="runtime session tool policy"):
        query.execute()


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


def test_mcp_uses_normal_tool_policy_without_session_grants(studio, monkeypatch):
    service, canvas = studio
    monkeypatch.setattr(
        figma_mcp,
        "FigmaService",
        lambda root, actor: FigmaService(
            root, actor, client_factory=canvas.client, oauth=FakeOAuth()
        ),
    )
    runtime = service().store.runtime_dir
    policy = session_policy(runtime)
    server = MCPServer("figma-policy-test")
    server._evander_transport_session_map = {"transport-1": "fresh-agent"}
    figma_mcp.register_mcp_tools(
        server,
        AddonContext(
            addon_id="figma-addon",
            runtime_dir=runtime,
            addon_runtime_dir=service().store.root,
            service=SimpleNamespace(policy=policy),
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

    def call(tool="figma_query", **arguments):
        return asyncio.run(
            asyncio.wait_for(server._tool_manager.call_tool(tool, arguments, context=context), 15)
        )

    inspect = {"operation": "inspect", "target_id": "mockups", "node_id": "0:1"}
    # File settings name no sessions. Any normally authorized session in this exact
    # directory can discover and inspect the file, including newly created agents.
    for session in ("fresh-agent", "designer-b"):
        server._evander_transport_session_map["transport-1"] = session
        result = call("figma_list_targets")
        assert result["targets"][0]["access"] == ["read", "comment", "edit"]
        assert call(request=inspect)["nodes"][0]["id"] == "0:1"
    server._evander_transport_session_map["transport-1"] = "reviewer"
    count = len(canvas.calls)
    with pytest.raises(ToolError, match="bound"):
        call("figma_list_targets", session_id="designer-a")
    with pytest.raises(ToolError, match="figma_execute is not allowed"):
        call(
            "figma_execute",
            request={
                "operation": "create_area",
                "target_id": "mockups",
                "name": "No",
                "request_id": "blocked",
            },
        )
    for session in ("outsider", "child"):
        server._evander_transport_session_map["transport-1"] = session
        assert call("figma_list_targets")["targets"] == []
        with pytest.raises(ToolError, match="Unknown Figma target"):
            call(request=inspect)
    server._evander_transport_session_map["transport-1"] = "fresh-agent"
    session = policy.get_session("fresh-agent")
    policy.session_store.upsert_session(session.model_copy(update={"allowed_tools": []}))
    with pytest.raises(ToolError, match="figma_query is not allowed"):
        call(request=inspect)
    server._evander_transport_session_map["transport-1"] = "designer-b"
    policy.policy_config.config_store.write(
        "root",
        domain="policy",
        payload={"mcp_tool_policies": {"figma_query": {"denied": True}}},
    )
    with pytest.raises(ToolError, match="denied by directory policy"):
        call(request=inspect)
    assert len(canvas.calls) == count


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
        assert kwargs["http_client"].headers["Authorization"] == "Bearer synthetic-token"
        yield None, None

    class Session:
        def __init__(self, *_, client_info):
            assert client_info.name == "evander"

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        async def initialize(self):
            pass

        async def list_tools(self, *, params=None):
            if params is None:
                return SimpleNamespace(tools=[], next_cursor="second")
            return SimpleNamespace(
                next_cursor=None,
                tools=[
                    SimpleNamespace(
                        name="use_figma",
                        input_schema={
                            "type": "object",
                            "required": ["fileKey"],
                            "properties": {"fileKey": {"type": "string"}},
                        },
                    )
                ],
            )

        async def call_tool(self, name, arguments):
            calls.append((name, arguments))
            return SimpleNamespace(is_error=False, model_dump_json=lambda: "{}")

    monkeypatch.setattr(module, "streamable_http_client", transport)
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


@pytest.mark.parametrize("image_size", [900 * 1024, 8 * 1024 * 1024 + 1])
def test_screenshot_sse_transport_preserves_the_decoded_image_limit(monkeypatch, image_size):
    from evander_figma_addon import client as module

    image = PNG + b"\0" * (image_size - len(PNG))

    def upstream(request):
        assert request.headers["Authorization"] == "Bearer synthetic-token"
        if request.method != "POST":
            return httpx2.Response(405)
        body = json.loads(request.content)
        if "id" not in body:
            return httpx2.Response(202)
        if body["method"] == "initialize":
            result = {
                "protocolVersion": "2025-06-18",
                "serverInfo": {"name": "synthetic-figma", "version": "1"},
                "capabilities": {"tools": {}},
            }
        elif body["method"] == "tools/list":
            result = {"tools": [{"name": "get_screenshot", "inputSchema": {"type": "object"}}]}
        else:
            assert body["method"] == "tools/call"
            assert body["params"]["name"] == "get_screenshot"
            result = {
                "content": [
                    {
                        "type": "image",
                        "mimeType": "image/png",
                        "data": base64.b64encode(image).decode(),
                    }
                ]
            }
            message = json.dumps({"jsonrpc": "2.0", "id": body["id"], "result": result})
            return httpx2.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=("event: message\ndata: " + message + "\n\n").encode(),
            )
        return httpx2.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": result})

    real_client = httpx2.AsyncClient
    monkeypatch.setattr(
        module.httpx2,
        "AsyncClient",
        lambda **kwargs: real_client(transport=httpx2.MockTransport(upstream), **kwargs),
    )
    client = FigmaClient(SimpleNamespace(token=lambda *_: "synthetic-token"), "root", "studio", {})
    if image_size > 8 * 1024 * 1024:
        with pytest.raises(FigmaError, match="bounded PNG preview"):
            client.screenshot("testFile123", "1:2")
    else:
        assert client.screenshot("testFile123", "1:2") == image


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


@pytest.mark.parametrize(
    "characters", ['\t"x"\n' * 1500, "Å漢😀\\\n" * 1800], ids=["escaped_text", "unicode_text"]
)
def test_large_text_remains_readable_snapshotable_and_recoverable(studio, characters):
    services, canvas = studio
    author, owner = services(), services("owner", True)
    frame = create_frame(author)
    base = run(author, "inspect", node_id=frame)
    created = run(
        author,
        "edit",
        node_id=frame,
        expected_revision=base["revision"],
        request_id="large-text",
        edits=[
            {
                "action": "create",
                "kind": "TEXT",
                "parent_id": frame,
                "properties": {"characters": characters},
            }
        ],
    )
    assert created["status"] == "succeeded", created
    text_id = created["result"]["createdNodeIds"][0]
    inspected = run(author, "inspect", node_id=frame)
    text = next(node for node in inspected["nodes"] if node["id"] == text_id)
    assert text["characters"] == characters
    assert text["textSegments"][0]["characters"] == characters
    assert inspected["revision"] == created["result"]["revision"]
    snapshot = run(author, "snapshot", node_id=frame, request_id="large-snapshot")
    assert snapshot["status"] == "succeeded", snapshot
    retained = json.loads(author._bundle(snapshot["result"])["design.json"])
    assert retained["nodes"] == inspected["nodes"]

    canvas.lose_next_write = True
    interrupted = run(
        author,
        "edit",
        node_id=frame,
        expected_revision=inspected["revision"],
        request_id="large-lost-response",
        edits=[{"action": "update", "node_id": text_id, "properties": {"name": "Renamed"}}],
    )
    assert interrupted["status"] == "uncertain"
    observed = run(owner, "reconcile", operation_id=interrupted["operation_id"])
    assert (
        next(n for n in observed["reconciliation"]["nodes"] if n["id"] == text_id)["characters"]
        == characters
    )
    owner.resolve_uncertain(
        "mockups", interrupted["operation_id"], digest(observed["reconciliation"])
    )
    assert (
        run(author, "comment", message="Review continues", request_id="after-large")["status"]
        == "succeeded"
    )


def test_fragmented_read_rejects_changes_between_chunks(studio):
    services, canvas = studio
    author = services()
    frame = create_frame(author)
    # Seed an oversized existing node to exercise reads independently of editing.
    canvas.state["nodes"][frame]["name"] = "x" * 16000
    client = canvas.client(FakeOAuth(), "root/design", "studio", settings().connections["studio"])
    script = client.script

    def concurrent_edit(file_key, payload):
        if payload.get("offset", 0) > 0:
            canvas.state["nodes"][frame]["name"] = "y" * 16000
        return script(file_key, payload)

    client.script = concurrent_edit
    with pytest.raises(FigmaError, match="changed during the read"):
        client.inspect("testFile123", frame)


def test_oversized_script_is_rejected_before_mutation_without_blocking_writes(studio):
    services, canvas = studio
    author = services()
    frame = create_frame(author)
    base = run(author, "inspect", node_id=frame)
    before = json.dumps(canvas.state, sort_keys=True)
    result = run(
        author,
        "edit",
        node_id=frame,
        expected_revision=base["revision"],
        request_id="oversized-script",
        edits=[
            {
                "action": "create",
                "kind": "TEXT",
                "parent_id": frame,
                "properties": {"characters": "\t" * 10000},
            }
        ]
        * 50,
    )
    assert result["status"] == "rejected", result
    assert json.dumps(canvas.state, sort_keys=True) == before
    assert not author.store.unresolved("testFile123")
    assert (
        run(author, "comment", message="Still writable", request_id="after-rejection")["status"]
        == "succeeded"
    )


@pytest.mark.parametrize("next_offset", [0, 1, True, "2"])
def test_fragmented_read_rejects_invalid_cursors(next_offset):
    client = FigmaClient(None, "root", "studio", settings().connections["studio"])
    client.script = lambda *_: {
        "nodes_json": "[{",
        "read_token": "unchanged",
        "next_offset": next_offset,
    }
    with pytest.raises(FigmaError, match="Invalid Figma read cursor"):
        client.inspect("testFile123", "0:1")


@pytest.mark.parametrize(
    "directory,target_id",
    [("root/design", "alias"), ("root/other", "mockups"), ("root/other", "alias")],
)
def test_self_feedback_is_suppressed_across_file_registrations(studio, directory, target_id):
    services, canvas = studio
    author, owner = services(), services("owner", True)
    revision, config = owner.store.settings(directory)
    if not config.connections:
        config.connections = settings().connections
    config.targets[target_id] = settings().targets["mockups"]
    owner.store.save_settings(directory, revision, config)
    other_owner = services("owner", True, directory=directory)
    owner.poll_external("mockups", interval=0)
    other_owner.poll_external(target_id, interval=0)
    posted = run(author, "comment", message="Integration review", request_id="shared-file-comment")
    assert posted["status"] == "succeeded"
    owner.poll_external("mockups", interval=0)
    other_owner.poll_external(target_id, interval=0)
    assert other_owner.store.events("probe", directory, target_id) == []
    other_author = services("designer-b", directory=directory)
    assert (
        other_author.execute(
            {
                "operation": "comment",
                "target_id": target_id,
                "message": "Another integration review",
                "request_id": "reply-from-alias",
            }
        )["status"]
        == "succeeded"
    )
    owner.poll_external("mockups", interval=0)
    assert all(
        row["payload"]["origin"] == "evander"
        for row in owner.store.events("probe", "root/design", "mockups")
    )

    # Keep both message and account checks when the operation is in another scope.
    original = canvas.comments_list[0]
    canvas.comments_list.extend(
        [
            {**original, "id": "quoted", "message": "Quoted: " + original["message"]},
            {**original, "id": "copied", "user": {"id": "human-user"}},
        ]
    )
    other_owner.poll_external(target_id, interval=0)
    external = [
        row["payload"]["item"]["id"]
        for row in owner.store.events("probe", directory, target_id)
        if row["payload"]["origin"] == "external"
    ]
    assert external == ["quoted", "copied"]
    # Cross-registration lookup must still require the exact file identity.
    revision, config = owner.store.settings(directory)
    config.targets[target_id].file_key = "anotherFile123"
    owner.store.save_settings(directory, revision, config)
    canvas.comments_list[:] = []
    other_owner.poll_external(target_id, interval=0)
    canvas.comments_list.append(original)
    other_owner.poll_external(target_id, interval=0)
    assert any(
        row["payload"].get("file_key") == "anotherFile123"
        and row["payload"]["origin"] == "external"
        for row in owner.store.events("probe", directory, target_id)
    )


def test_interrupted_snapshot_does_not_block_file_writes_after_restart(studio):
    services, canvas = studio
    author = services()
    frame = create_frame(author)
    original = canvas.client

    def client_factory(*args):
        client = original(*args)

        def terminate(*_):
            raise SystemExit("Simulate process termination during snapshot capture")

        client.screenshot = terminate
        return client

    author.client_factory = client_factory
    with pytest.raises(SystemExit):
        run(author, "snapshot", node_id=frame, request_id="interrupted-snapshot")
    restarted = services("designer-b")
    history = run(restarted, "history")
    assert any(
        record["request"]["operation"] == "snapshot" and record["status"] == "running"
        for record in history["operations"]
    )
    assert restarted.store.unresolved("testFile123") == []
    create_frame(restarted, "after-snapshot")
    assert (
        run(restarted, "comment", message="Still writable", request_id="after-crash")["status"]
        == "succeeded"
    )


@pytest.mark.parametrize("action", ["create", "update"])
def test_text_styles_load_the_effective_font_before_editing_text(studio, action):
    services, canvas = studio
    author = services()
    frame = create_frame(author)
    canvas.state["textStyles"] = {
        "existing-style": {"fontName": {"family": "Roboto Mono", "style": "Bold"}, "fontSize": 24}
    }
    base = run(author, "inspect", node_id=frame)
    edit = {"action": "create", "kind": "TEXT", "parent_id": frame}
    if action == "update":
        initial = run(
            author,
            "edit",
            node_id=frame,
            expected_revision=base["revision"],
            request_id="unstyled-text",
            edits=[{**edit, "properties": {"characters": "Before"}}],
        )
        assert initial["status"] == "succeeded", initial
        edit = {"action": "update", "node_id": initial["result"]["createdNodeIds"][0]}
        base = run(author, "inspect", node_id=frame)
    edit["properties"] = {
        "text_style_id": "existing-style",
        "characters": "Production overview",
        "font_size": 28,
    }
    result = run(
        author,
        "edit",
        node_id=frame,
        expected_revision=base["revision"],
        request_id="apply-style",
        edits=[edit],
    )
    assert result["status"] == "succeeded", result
    text = next(n for n in run(author, "inspect", node_id=frame)["nodes"] if n["type"] == "TEXT")
    assert text["characters"] == "Production overview"
    assert text["fontName"] == {"family": "Roboto Mono", "style": "Bold"}
    assert text["fontSize"] == 28
    assert text["textStyleId"] == "existing-style"


@pytest.mark.parametrize(
    "properties,expected",
    [
        ({"font_style": "Regular"}, {"family": "Roboto Mono", "style": "Regular"}),
        ({"font_family": "Inter"}, {"family": "Inter", "style": "Bold"}),
    ],
)
def test_partial_font_updates_preserve_unspecified_attributes(studio, properties, expected):
    services, _ = studio
    author = services()
    frame = create_frame(author)
    base = run(author, "inspect", node_id=frame)
    created = run(
        author,
        "edit",
        node_id=frame,
        expected_revision=base["revision"],
        request_id="original-font",
        edits=[
            {
                "action": "create",
                "kind": "TEXT",
                "parent_id": frame,
                "properties": {
                    "characters": "Heading",
                    "font_family": "Roboto Mono",
                    "font_style": "Bold",
                },
            }
        ],
    )
    assert created["status"] == "succeeded", created
    text_id = created["result"]["createdNodeIds"][0]
    result = run(
        author,
        "edit",
        node_id=frame,
        expected_revision=created["result"]["revision"],
        request_id="partial-font",
        edits=[{"action": "update", "node_id": text_id, "properties": properties}],
    )
    assert result["status"] == "succeeded", result
    text = next(n for n in run(author, "inspect", node_id=frame)["nodes"] if n["id"] == text_id)
    assert text["fontName"] == expected
    assert text["characters"] == "Heading"
