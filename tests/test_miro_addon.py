from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import json
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit
import xml.etree.ElementTree as ET

import httpx
import pytest
from mcp.server.fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.routing import Mount

from evander.addons import AddonContext, AddonLoaderConfig, load_addons
from evander.events.models import WorkflowNodeEnvelope
from evander_miro_addon import register_addon
from evander_miro_addon.api import admin_api_routes
from evander_miro_addon.auth import MiroOAuth
from evander_miro_addon.client import bind_arguments, comment_page
from evander_miro_addon.models import Connection, MiroError, OperationInput
from evander_miro_addon.providers import register_providers
from evander_miro_addon.service import Access, MiroService, canvas
from evander_miro_addon.state import MiroStore

SVG = '<svg xmlns="http://www.w3.org/2000/svg"><text data-miro-id="item1" x="10" y="20">Order service</text><rect data-miro-id="item2" width="100" height="50"/></svg>'
NEW = '<svg xmlns="http://www.w3.org/2000/svg"><text x="10" y="20">Retry flow</text></svg>'


def run(coroutine):
    return asyncio.run(coroutine)


def config(**changes):
    return Connection.model_validate(
        {
            "id": "architecture",
            "name": "Architecture",
            "directory_path": "root/project",
            "redirect_uri": "https://evander.example/api/addons/miro-addon/oauth/callback",
            "boards": [
                {"board_id": "board1", "name": "Architecture", "writable": True},
                {"board_id": "readonly", "writable": False},
            ],
            "session_ids": ["author", "reviewer"],
            "allow_workflows": True,
            "allow_create_boards": True,
            "daily_call_budget": 100,
            **changes,
        }
    )


class Remote:
    """Stateful external board model: tests observe behavior, not individual mock calls."""

    def __init__(self):
        self.svg = SVG
        self.comments = [
            {"id": "thread1", "text": "Where are retries?", "replies": [], "resolved": False}
        ]
        self.calls = []
        self.fail_after_write = False
        self.fail_read = False
        self.active_writes = 0
        self.max_active_writes = 0
        self.delay = False

    def client(self, store, record):
        remote = self

        class Client:
            def __init__(self):
                self.record = record

            @asynccontextmanager
            async def open(self):
                yield self

            def prepare(self, operation, **values):
                return operation, values

            async def call(self, operation, **values):
                store.assert_current(self.record)
                store.consume_call(self.record)
                remote.calls.append((operation, values))
                if operation in {"read_canvas", "read_comments"} and remote.fail_read:
                    raise MiroError("upstream_unavailable", "Offline")
                if operation == "read_canvas":
                    return {"svg": remote.svg}
                if operation == "read_comments":
                    return {"comments": json.loads(json.dumps(remote.comments))}
                if operation in {"get_canvas_format", "load_format"}:
                    return {"text": "Miro SVG format"}
                remote.active_writes += 1
                remote.max_active_writes = max(remote.max_active_writes, remote.active_writes)
                try:
                    if remote.delay:
                        await asyncio.sleep(0.08)
                    if operation == "update_canvas":
                        remote.svg = values["svg"]
                    elif operation == "create_canvas":
                        existing, new = ET.fromstring(remote.svg), ET.fromstring(values["svg"])
                        for index, element in enumerate(list(new)):
                            element.set("data-miro-id", "created" + str(index))
                            existing.append(element)
                        remote.svg = (
                            ET.tostring(existing, encoding="unicode")
                            .replace("ns0:", "")
                            .replace(":ns0", "")
                        )
                    elif operation == "reply_comment":
                        remote.comments[0]["replies"].append(
                            {"id": "reply1", "text": values["text"]}
                        )
                    elif operation == "resolve_comment":
                        remote.comments[0]["resolved"] = values["resolved"]
                    if remote.fail_after_write:
                        raise TimeoutError("response lost after remote commit")
                    return {"id": "created-board"} if operation == "create_board" else {"ok": True}
                finally:
                    remote.active_writes -= 1

        return Client()


@pytest.fixture
def setup(tmp_path):
    store = MiroStore(tmp_path)
    record = store.save_connection(config(), 0)
    remote = Remote()
    service = MiroService(
        store, Access("root/project", "session:author", session_id="author"), remote.client
    )
    return store, record, remote, service


def read(service, kind="canvas", **changes):
    return run(
        service.execute(
            "read_" + kind, {"connection_id": "architecture", "board_id": "board1", **changes}
        )
    )


def update_args(snapshot, **changes):
    return {
        "connection_id": "architecture",
        "board_id": "board1",
        "operation_key": "update-retries",
        "snapshot_id": snapshot["snapshot_id"],
        "svg": SVG.replace("Order service", "Order service with retry"),
        **changes,
    }


def test_addon_discovery_catalog_and_real_mcp_schemas(tmp_path):
    addon = register_addon()
    catalog = load_addons(AddonLoaderConfig(modules=["evander_miro_addon"]), base_dir=tmp_path)
    assert any(item.addon_id == "miro-addon" for item in catalog.registrations)
    server = FastMCP("test")
    names = addon.mcp_tools(
        server,
        AddonContext(
            addon_id="miro-addon",
            runtime_dir=tmp_path,
            addon_runtime_dir=tmp_path / "addons/miro-addon",
            service=SimpleNamespace(),
        ),
    )
    tools = run(server.list_tools())
    assert (
        set(names)
        == {tool.name for tool in tools}
        == {tool.name for tool in addon.mcp_tool_catalog}
    )
    schema = next(tool.inputSchema for tool in tools if tool.name == "miro_update_canvas")
    assert {"snapshot_id", "operation_key", "board_id", "connection_id", "svg"} <= set(
        schema["required"]
    )
    assert "ctx" not in schema["properties"]


@pytest.mark.parametrize(
    "scope,session", [("root/other", "author"), ("root", "author"), ("root/project", "intruder")]
)
def test_access_rejected_before_network(setup, scope, session):
    store, _, remote, _ = setup
    service = MiroService(
        store, Access(scope, "session:" + session, session_id=session), remote.client
    )
    with pytest.raises(MiroError, match="unavailable|no grant"):
        read(service)
    assert not remote.calls


def test_unapproved_and_readonly_boards_and_workflow_grants(setup):
    store, record, remote, service = setup
    with pytest.raises(MiroError, match="outside"):
        read(service, board_id="private")
    with pytest.raises(MiroError, match="outside"):
        run(
            service.execute(
                "create_canvas",
                {
                    "connection_id": "architecture",
                    "board_id": "readonly",
                    "svg": NEW,
                    "operation_key": "bad",
                },
            )
        )
    store.save_connection(config(allow_workflows=False), record["revision"])
    with pytest.raises(MiroError, match="Workflow access"):
        read(
            MiroService(store, Access("root/project", "workflow:one", workflow=True), remote.client)
        )
    assert not remote.calls


def test_revision_and_revoked_grants_invalidate_in_flight_calls(setup):
    store, record, remote, service = setup
    store.save_connection(config(session_ids=[]), record["revision"])
    with pytest.raises(MiroError, match="changed"):
        run(remote.client(store, record).call("read_canvas", board_id="board1"))
    with pytest.raises(MiroError, match="reload"):
        store.save_connection(config(), record["revision"])
    with pytest.raises(MiroError, match="no grant"):
        read(service)


def test_scoped_update_is_editable_and_idempotent(setup):
    store, _, remote, service = setup
    before = read(service)
    args = update_args(before, source_refs=["repo:orders@abc123", "task:architecture-review"])
    result = run(service.execute("update_canvas", args))
    assert result["status"] == "applied"
    assert result["affected_item_ids"] == ["item1"]
    assert "with retry" in remote.svg
    assert "item2" in remote.svg
    after = run(
        service.execute(
            "get_snapshot",
            {"connection_id": "architecture", "snapshot_id": result["result_snapshot_id"]},
        )
    )
    assert after["data"]["item_ids"] == ["item1", "item2"]
    call_count = len(remote.calls)
    assert run(service.execute("update_canvas", args))["operation_id"] == result["operation_id"]
    assert len(remote.calls) == call_count
    assert store.get("operation", result["operation_id"])["request"]["svg"] == args["svg"]
    with pytest.raises(MiroError, match="different arguments"):
        run(service.execute("update_canvas", args | {"svg": SVG}))


def test_human_edit_conflict_does_not_overwrite(setup):
    _, _, remote, service = setup
    before = read(service)
    remote.svg = SVG.replace("Order service", "Human correction")
    with pytest.raises(MiroError, match="changed since"):
        run(service.execute("update_canvas", update_args(before)))
    assert "Human correction" in remote.svg
    assert not any(op == "update_canvas" for op, _ in remote.calls)


def test_scope_ids_and_implicit_deletes_fail_closed(setup):
    _, _, _, service = setup
    before = read(service, frame_id="frame1")
    with pytest.raises(MiroError, match="selection must match"):
        run(service.execute("update_canvas", update_args(before)))
    before = read(service)
    with pytest.raises(MiroError, match="delete"):
        run(
            service.execute(
                "update_canvas",
                update_args(before, svg='<svg><text data-miro-id="item1">x</text></svg>'),
            )
        )
    with pytest.raises(MiroError, match="outside"):
        run(
            service.execute(
                "update_canvas", update_args(before, svg=SVG.replace("item1", "foreign-item"))
            )
        )
    with pytest.raises(MiroError, match="existing Miro"):
        run(
            service.execute(
                "create_canvas",
                {
                    "connection_id": "architecture",
                    "board_id": "board1",
                    "svg": SVG,
                    "operation_key": "create",
                },
            )
        )


def test_timeout_after_remote_write_is_uncertain_never_replayed(setup):
    store, _, remote, service = setup
    before = read(service)
    remote.fail_after_write = True
    result = run(service.execute("update_canvas", update_args(before)))
    assert result["status"] == "uncertain"
    assert "with retry" in remote.svg
    count = len(remote.calls)
    assert run(service.execute("update_canvas", update_args(before)))["status"] == "uncertain"
    assert len(remote.calls) == count
    with pytest.raises(MiroError, match="reconciliation"):
        run(
            service.execute(
                "create_canvas",
                {
                    "connection_id": "architecture",
                    "board_id": "board1",
                    "svg": NEW,
                    "operation_key": "another",
                },
            )
        )
    operator = MiroService(
        store, Access("root/project", "operator:jonathan", admin=True), remote.client
    )
    with pytest.raises(MiroError, match="Read back"):
        run(
            operator.resolve_operation("architecture", result["operation_id"], "applied", "checked")
        )
    evidence = run(operator.reconcile("architecture", result["operation_id"]))
    assert "with retry" in evidence["observed"]["data"]["svg"]
    assert (
        run(
            operator.resolve_operation(
                "architecture", result["operation_id"], "applied", "Board shows retry label"
            )
        )["status"]
        == "applied"
    )


def test_concurrent_agents_cannot_both_update_the_same_snapshot(setup):
    store, _, remote, author = setup
    reviewer = MiroService(
        store, Access("root/project", "session:reviewer", session_id="reviewer"), remote.client
    )
    before = read(author)
    remote.delay = True

    async def race():
        return await asyncio.gather(
            author.execute("update_canvas", update_args(before)),
            reviewer.execute(
                "update_canvas",
                update_args(
                    before,
                    operation_key="review-edit",
                    svg=SVG.replace("Order service", "Reviewer"),
                ),
            ),
            return_exceptions=True,
        )

    outcomes = run(race())
    assert (
        len(
            [
                result
                for result in outcomes
                if isinstance(result, dict) and result["status"] == "applied"
            ]
        )
        == 1
    )
    assert (
        len(
            [
                result
                for result in outcomes
                if isinstance(result, MiroError) and result.code == "conflict"
            ]
        )
        == 1
    )
    assert remote.max_active_writes == 1


def test_create_board_binds_result_and_new_canvas_remains_editable(setup):
    store, _, remote, service = setup
    created = run(
        service.execute(
            "create_board",
            {
                "connection_id": "architecture",
                "title": "Order architecture",
                "operation_key": "new-board",
            },
        )
    )
    assert created["status"] == "applied"
    assert created["board_id"] == "created-board"
    bindings = run(service.execute("list_boards", {"connection_id": "architecture"}))["boards"]
    assert any(item["board_id"] == "created-board" and item["writable"] for item in bindings)
    result = run(
        service.execute(
            "create_canvas",
            {
                "connection_id": "architecture",
                "board_id": "board1",
                "svg": NEW,
                "operation_key": "explanation",
            },
        )
    )
    assert result["status"] == "applied"
    assert "Retry flow" in remote.svg
    assert "Order service" in remote.svg


def test_human_feedback_reply_and_resolution_keep_threads(setup):
    _, _, remote, service = setup
    before = read(service, "comments")
    reply = {
        "connection_id": "architecture",
        "board_id": "board1",
        "comment_id": "thread1",
        "snapshot_id": before["snapshot_id"],
        "operation_key": "feedback-reply",
        "text": "The worker retries three times.",
    }
    assert run(service.execute("reply_comment", reply))["status"] == "applied"
    assert len(remote.comments[0]["replies"]) == 1
    assert run(service.execute("reply_comment", reply))["status"] == "applied"
    assert len(remote.comments[0]["replies"]) == 1
    before = read(service, "comments")
    assert (
        run(
            service.execute(
                "resolve_comment",
                {key: value for key, value in reply.items() if key != "text"}
                | {"snapshot_id": before["snapshot_id"], "operation_key": "resolve-feedback"},
            )
        )["status"]
        == "applied"
    )
    assert remote.comments[0]["resolved"]


def test_polling_baseline_restart_acknowledgment_and_repeated_transitions(setup, monkeypatch):
    store, _, remote, service = setup
    clock = [100000.0]
    monkeypatch.setattr("evander_miro_addon.service.time.time", lambda: clock[0])
    args = {"connection_id": "architecture", "board_id": "board1"}
    assert run(service.poll("watch", args, kind="canvas", interval=60)) == []
    remote.svg = SVG.replace("Order service", "Human update")
    clock[0] += 61
    events = run(service.poll("watch", args, kind="canvas", interval=60))
    assert len(events) == 1 and events[0]["origin"] == "external_or_unknown"
    restarted = MiroService(MiroStore(store.root.parents[1]), service.access, remote.client)
    assert run(restarted.poll("watch", args, kind="canvas", interval=60)) == events
    run(restarted.acknowledge(events[0]["subscription_key"], events[0]["event_id"]))
    assert run(restarted.poll("watch", args, kind="canvas", interval=60)) == []
    remote.svg = SVG
    clock[0] += 61
    reverted = run(restarted.poll("watch", args, kind="canvas", interval=60))
    assert reverted[0]["event_id"] != events[0]["event_id"]


def test_polling_suppresses_own_changes_and_backs_off_failures(setup, monkeypatch):
    store, _, remote, service = setup
    clock = [100000.0]
    monkeypatch.setattr("evander_miro_addon.service.time.time", lambda: clock[0])
    args = {"connection_id": "architecture", "board_id": "board1"}
    run(service.poll("watch", args, kind="canvas", interval=60))
    before = read(service)
    clock[0] += 10
    run(service.execute("update_canvas", update_args(before)))
    clock[0] += 60
    assert run(service.poll("watch", args, kind="canvas", interval=60)) == []
    checkpoint = store.list("subscription")[0]["snapshot_id"]
    remote.fail_read = True
    clock[0] += 61
    with pytest.raises(MiroError):
        run(service.poll("watch", args, kind="canvas", interval=60))
    count = len(remote.calls)
    assert run(service.poll("watch", args, kind="canvas", interval=60)) == []
    assert len(remote.calls) == count
    assert store.list("subscription")[0]["snapshot_id"] == checkpoint


def test_polling_attributes_scoped_writes_across_connections(setup, monkeypatch):
    store, _, remote, service = setup
    clock = [100000.0]
    monkeypatch.setattr("evander_miro_addon.service.time.time", lambda: clock[0])
    store.save_connection(config(id="monitor"), 0)
    args = {"connection_id": "monitor", "board_id": "board1"}
    run(service.poll("quiet", args, kind="canvas", interval=60))
    run(service.poll("all", args, kind="canvas", interval=60, include_self=True))
    before = read(service, frame_id="frame1")
    clock[0] += 10
    run(service.execute("update_canvas", update_args(before, frame_id="frame1")))
    clock[0] += 60
    assert run(service.poll("quiet", args, kind="canvas", interval=60)) == []
    event = run(service.poll("all", args, kind="canvas", interval=60, include_self=True))[0]
    assert event["origin"] == "evander"
    assert event["connection_id"] == "monitor"
    run(service.acknowledge(event["subscription_key"], event["event_id"]))

    # An unrelated human edit read by a subsequent Evander write remains visible.
    remote.svg = remote.svg.replace('width="100"', 'width="120"')
    before = read(service, frame_id="frame1")
    clock[0] += 10
    run(
        service.execute(
            "update_canvas",
            update_args(
                before,
                frame_id="frame1",
                operation_key="followup",
                svg=before["data"]["svg"].replace("with retry", "with bounded retries"),
            ),
        )
    )
    clock[0] += 60
    event = run(service.poll("quiet", args, kind="canvas", interval=60))[0]
    assert event["origin"] == "external_or_unknown"


def test_polling_create_canvas_suppresses_only_recorded_additions(setup, monkeypatch):
    store, _, remote, service = setup
    clock = [100000.0]
    monkeypatch.setattr("evander_miro_addon.service.time.time", lambda: clock[0])
    args = {"connection_id": "architecture", "board_id": "board1"}
    run(service.poll("watch", args, kind="canvas", interval=60))
    clock[0] += 10
    operation = run(
        service.execute(
            "create_canvas",
            args
            | {
                "frame_id": "frame1",
                "svg": NEW,
                "operation_key": "explanation",
            },
        )
    )
    assert operation["affected_item_ids"] == ["created0"]
    assert store.get("snapshot", operation["baseline_snapshot_id"])
    clock[0] += 60
    assert run(service.poll("watch", args, kind="canvas", interval=60)) == []
    remote.svg = remote.svg.replace("Retry flow", "Human correction")
    clock[0] += 60
    assert (
        run(service.poll("watch", args, kind="canvas", interval=60))[0]["origin"]
        == "external_or_unknown"
    )


def test_create_board_grant_is_rechecked_after_waiting_for_lock(setup):
    store, record, remote, service = setup

    async def scenario():
        async with store.lock("create-board:" + record["key"]):
            pending = asyncio.create_task(
                service.execute(
                    "create_board",
                    {
                        "connection_id": "architecture",
                        "title": "A new board",
                        "operation_key": "new",
                    },
                )
            )
            await asyncio.sleep(0.02)
            store.save_connection(config(allow_create_boards=False), record["revision"])
        with pytest.raises(MiroError, match="not granted"):
            await pending

    run(scenario())
    assert not remote.calls


def test_connection_shadowing_cannot_change_a_waiting_write_identity(setup):
    store, _, remote, _ = setup
    service = MiroService(
        store, Access("root/project/child", "session:author", session_id="author"), remote.client
    )

    async def scenario():
        async with store.lock("board:board1"):
            pending = asyncio.create_task(
                service.execute(
                    "create_canvas",
                    {
                        "connection_id": "architecture",
                        "board_id": "board1",
                        "svg": NEW,
                        "operation_key": "new",
                    },
                )
            )
            await asyncio.sleep(0.02)
            store.save_connection(config(directory_path="root/project/child"), 0)
        with pytest.raises(MiroError, match="binding changed"):
            await pending

    run(scenario())
    assert not remote.calls


def test_call_budget_is_shared_and_resets_at_utc_day(setup, monkeypatch):
    from datetime import UTC, datetime, timedelta

    clock = [datetime(2026, 9, 19, 23, 59, 59, tzinfo=UTC)]

    class Clock:
        @staticmethod
        def now(tz):
            assert tz == UTC
            return clock[0]

    monkeypatch.setattr("evander_miro_addon.state.datetime", Clock)
    store, record, _, _ = setup
    record = store.save_connection(config(daily_call_budget=1), record["revision"])
    store.consume_call(record)
    with pytest.raises(MiroError, match="budget"):
        MiroStore(store.root.parents[1]).consume_call(record)
    assert store.usage(record) == 1
    clock[0] += timedelta(seconds=1)
    assert store.usage(record) == 0
    MiroStore(store.root.parents[1]).consume_call(record)
    assert store.usage(record) == 1


@pytest.mark.parametrize(
    "svg",
    [
        '<!DOCTYPE svg [<!ENTITY x "boom">]><svg>&x;</svg>',
        "<svg><script>alert(1)</script></svg>",
        '<svg onload="evil()"/>',
        '<svg><image href="file:///etc/passwd"/></svg>',
    ],
)
def test_canvas_rejects_active_content(svg):
    with pytest.raises(MiroError):
        canvas(svg)


def test_schema_negotiation_and_comment_pagination_fail_closed():
    schema = {
        "type": "object",
        "properties": {"board_url": {"type": "string"}, "svg": {"type": "string"}},
        "required": ["board_url", "svg"],
        "additionalProperties": False,
    }
    assert (
        bind_arguments(
            schema, {"board_id": "b", "board_url": "https://miro.com/app/board/b/", "svg": NEW}
        )["svg"]
        == NEW
    )
    with pytest.raises(MiroError, match="schema changed"):
        bind_arguments(schema | {"required": ["new_required_field"]}, {"svg": NEW})
    with pytest.raises(MiroError, match="schema changed"):
        bind_arguments({"$ref": "https://schemas.invalid/board"}, {})
    with pytest.raises(MiroError, match="scoped board"):
        bind_arguments({"properties": {"svg": {}}}, {"board_id": "b", "svg": NEW})
    with pytest.raises(MiroError, match="incomplete"):
        comment_page({"comments": [], "has_more": True})
    assert comment_page({"comments": [{"id": "c"}], "nextCursor": "next"})[1] == "next"


def oauth_transport(captured):
    def handler(request):
        captured.append(request)
        if request.url.path == "/.well-known/oauth-authorization-server":
            return httpx.Response(
                200,
                json={
                    "issuer": "https://mcp.miro.com/",
                    "authorization_endpoint": "https://mcp.miro.com/authorize",
                    "token_endpoint": "https://mcp.miro.com/token",
                    "registration_endpoint": "https://mcp.miro.com/register",
                    "code_challenge_methods_supported": ["S256"],
                },
            )
        if request.url.path == "/register":
            return httpx.Response(
                201, json={"client_id": "test-client", "client_secret": "synthetic-client-secret"}
            )
        if request.url.path == "/token":
            return httpx.Response(
                200,
                json={
                    "access_token": "synthetic-access-token",
                    "refresh_token": "synthetic-refresh-token",
                    "expires_in": 3600,
                },
            )
        raise AssertionError(request.url)

    return httpx.MockTransport(handler)


def test_oauth_pkce_encrypted_custody_single_use_and_refresh(setup):
    store, record, _, _ = setup
    captured = []
    auth = MiroOAuth(store, oauth_transport(captured))
    url = run(auth.begin(record, "jonathan"))["authorization_url"]
    params = parse_qs(urlsplit(url).query)
    assert params["code_challenge_method"] == ["S256"]
    state = params["state"][0]
    with pytest.raises(MiroError, match="operator who started"):
        run(auth.complete(state, "test-code", "someone-else"))
    assert run(auth.complete(state, "test-code", "jonathan"))["status"] == "connected"
    assert "code_verifier=" in captured[-1].content.decode()
    with pytest.raises(MiroError, match="already used"):
        run(auth.complete(state, "test-code", "jonathan"))
    assert run(auth.access_token(record)) == "synthetic-access-token"
    for path in store.root.glob("state.db*"):
        content = path.read_bytes()
        assert b"synthetic-access-token" not in content
        assert b"synthetic-client-secret" not in content
    credentials = store.credentials(record)
    credentials["tokens"]["expires_at"] = 0
    store.save_credentials(record, credentials)
    assert run(auth.access_token(record)) == "synthetic-access-token"
    assert b"grant_type=refresh_token" in captured[-1].content


def test_oauth_callback_cannot_replace_disconnected_connection(setup):
    store, record, _, _ = setup
    captured = []
    auth = MiroOAuth(store, oauth_transport(captured))
    url = run(auth.begin(record, "jonathan"))["authorization_url"]
    state = parse_qs(urlsplit(url).query)["state"][0]
    store.disconnect(record)
    with pytest.raises(MiroError, match="changed"):
        run(auth.complete(state, "test-code", "jonathan"))
    assert not any(request.url.path == "/token" for request in captured)


def test_all_workflow_schemas_and_service_parity(setup, monkeypatch):
    store, _, remote, service = setup
    providers = register_providers()

    def for_node(context, node_id):
        return MiroService(
            store,
            Access("root/project", "workflow:root/project:" + node_id, workflow=True),
            remote.client,
        )

    monkeypatch.setattr("evander_miro_addon.providers.provider_service", for_node)
    for operation, cls in providers.query_providers.items():
        assert operation in providers.node_definition_resolvers
    envelope = WorkflowNodeEnvelope(
        id="read",
        display_name="Read",
        type="query:miro:read-canvas",
        config={"connection_id": "architecture", "board_id": "board1"},
    )
    provider = providers.query_providers[envelope.type]()
    provider.initialize(envelope, SimpleNamespace())
    response = provider.execute()
    direct = read(service)
    assert response["snapshot_id"] == direct["snapshot_id"]
    effect = providers.effect_providers["effect:miro:update-canvas"]()
    effect.initialize(
        WorkflowNodeEnvelope(
            id="update",
            display_name="Update",
            type="effect:miro:update-canvas",
            config=update_args(direct),
        ),
        SimpleNamespace(),
    )
    result = effect.execute()
    assert result["status"] == "applied"
    assert result["actor"] == "workflow:root/project:update"
    assert "with retry" in remote.svg
    assert all(
        entry.fields and all(field.description for field in entry.fields)
        for entry in providers.workflow_editor_catalog_entries
    )


class AdminAuth:
    def __init__(self):
        self.unlocked = True
        self.allowed = True

    def require(self, request):
        if not self.allowed:
            raise PermissionError()
        return {"login": "jonathan"}

    def require_permission(self, request, permission, directory_path=None):
        self.require(request)
        if directory_path not in {None, "root/project"}:
            raise PermissionError()

    def privileged_for_request(self, request):
        return self.unlocked


def test_admin_routes_revision_rbac_and_no_secret_disclosure(tmp_path):
    auth = AdminAuth()
    context = AddonContext(
        addon_id="miro-addon",
        runtime_dir=tmp_path,
        addon_runtime_dir=tmp_path / "addons/miro-addon",
        auth=auth,
        service=SimpleNamespace(),
    )
    app = Starlette(routes=[Mount("/api/addons/miro-addon", routes=admin_api_routes(context))])

    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://evander.example"
        ) as client:
            path = "/api/addons/miro-addon/connections?directory_path=root/project"
            payload = {"config": config().model_dump(), "expected_revision": 0}
            auth.unlocked = False
            assert (await client.post(path, json=payload)).status_code == 403
            auth.unlocked = True
            saved = await client.post(path, json=payload)
            assert saved.status_code == 200
            assert saved.json()["revision"] == 1
            assert (await client.post(path, json=payload)).status_code == 409
            assert (await client.get(path)).json()["connections"][0]["connected"] is False
            assert "encrypted" not in (await client.get(path)).text
            assert (
                await client.get("/api/addons/miro-addon/connections?directory_path=root")
            ).status_code == 403
            auth.allowed = False
            assert (await client.get(path)).status_code == 403

    run(scenario())


def test_real_mcp_http_handshake_schema_binding_and_error_preservation(setup, monkeypatch):
    from evander_miro_addon.client import MiroClient

    store, record, _, _ = setup
    requests = []

    async def token(_self, _record):
        return "synthetic-transport-token"

    monkeypatch.setattr(MiroOAuth, "access_token", token)

    def handle(request):
        if request.method != "POST":
            return httpx.Response(405)
        payload = json.loads(request.content)
        requests.append(payload)
        if "id" not in payload:
            return httpx.Response(202)
        if payload["method"] == "initialize":
            result = {
                "protocolVersion": "2025-06-18",
                "serverInfo": {"name": "synthetic-miro", "version": "1"},
                "capabilities": {"tools": {}},
            }
        elif payload["method"] == "tools/list":
            result = {
                "tools": [
                    {
                        "name": "canvas_read_as_svg",
                        "inputSchema": {
                            "type": "object",
                            "properties": {"board_url": {"type": "string"}},
                            "required": ["board_url"],
                            "additionalProperties": False,
                        },
                    }
                ]
            }
        else:
            assert payload["method"] == "tools/call"
            assert payload["params"]["name"] == "canvas_read_as_svg"
            assert payload["params"]["arguments"]["board_url"].endswith(
                "/board1/?focusWidget=frame1"
            )
            result = {"content": [{"type": "text", "text": SVG}]}
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": payload["id"], "result": result})

    async def scenario():
        async with MiroClient(
            store, record, http_transport=httpx.MockTransport(handle)
        ).open() as client:
            result = await client.call("read_canvas", board_id="board1", frame_id="frame1")
            assert result["text"] == SVG
        with pytest.raises(MiroError, match="source snapshot"):
            async with MiroClient(store, record, http_transport=httpx.MockTransport(handle)).open():
                raise MiroError("conflict", "changed source snapshot")

    run(scenario())
    assert store.usage(record) == 1
    assert [request["method"] for request in requests].count("initialize") == 2


def test_empty_canvas_required_empty_selection_and_incomplete_pages(setup):
    from evander_miro_addon.client import svg_from_result

    assert canvas(svg_from_result({"svg": '<svg xmlns="http://www.w3.org/2000/svg"/>'}))[1] == []
    assert bind_arguments(
        {"properties": {"item_ids": {"type": "array"}}, "required": ["item_ids"]}, {"item_ids": []}
    ) == {"item_ids": []}
    store, _, remote, service = setup
    original_factory = remote.client

    def paginated(store, record):
        client = original_factory(store, record)
        original = client.call

        async def call(operation, **kwargs):
            if operation != "read_comments":
                return await original(operation, **kwargs)
            if not kwargs.get("cursor"):
                return {"comments": [{"id": "first"}], "next_cursor": "second"}
            raise MiroError("upstream_unavailable", "Second page unavailable")

        client.call = call
        return client

    service.client_factory = paginated
    with pytest.raises(MiroError, match="Second page"):
        run(
            service.poll(
                "pages", {"connection_id": "architecture", "board_id": "board1"}, kind="comments"
            )
        )
    subscription = store.list("subscription")[0]
    assert subscription["snapshot_id"] is None
    assert subscription["pending"] == []
    assert store.list("snapshot") == []


def test_comment_pagination_completes_before_observation(setup):
    store, _, remote, service = setup
    original_factory = remote.client

    def factory(store, record):
        client = original_factory(store, record)

        async def call(operation, **kwargs):
            if kwargs.get("cursor") == "next":
                return {"comments": [{"id": "second", "text": "Second question"}]}
            return {"comments": [{"id": "first", "text": "First question"}], "nextCursor": "next"}

        client.call = call
        return client

    service.client_factory = factory
    result = read(service, "comments")
    assert [comment["id"] for comment in result["data"]["comments"]] == ["first", "second"]
    assert len(store.list("snapshot")) == 1


def test_oauth_destinations_and_concurrent_refresh(setup):
    from evander_miro_addon.auth import miro_endpoint

    for url in [
        "http://mcp.miro.com/token",
        "https://mcp.miro.com.evil.test/token",
        "https://user:secret@mcp.miro.com/token",
        "https://127.0.0.1/token",
    ]:
        with pytest.raises(MiroError):
            miro_endpoint(url)
    store, record, _, _ = setup
    requests = []
    auth = MiroOAuth(store, oauth_transport(requests))
    url = run(auth.begin(record, "operator"))["authorization_url"]
    run(auth.complete(parse_qs(urlsplit(url).query)["state"][0], "code", "operator"))
    credentials = store.credentials(record)
    credentials["tokens"]["expires_at"] = 0
    store.save_credentials(record, credentials)

    async def refresh_together():
        return await asyncio.gather(
            auth.access_token(record),
            MiroOAuth(MiroStore(store.root.parents[1]), oauth_transport(requests)).access_token(
                record
            ),
        )

    assert run(refresh_together()) == ["synthetic-access-token"] * 2
    assert (
        len([request for request in requests if b"grant_type=refresh_token" in request.content])
        == 1
    )


def test_workflow_trigger_acknowledges_only_after_publication(setup, monkeypatch):
    store, _, _, service = setup
    providers = register_providers()
    monkeypatch.setattr(
        "evander_miro_addon.providers.provider_service", lambda context, node_id: service
    )
    definition = WorkflowNodeEnvelope(
        id="miro-feedback",
        display_name="Feedback",
        type="trigger:miro:comments-changed",
        config={
            "connection_id": "architecture",
            "board_id": "board1",
            "emit_initial": True,
        },
    )
    trigger = providers.trigger_providers[definition.type]()
    trigger.initialize(definition, SimpleNamespace())
    events = trigger.poll()
    assert len(events) == 1
    assert trigger.poll()[0].event_id == events[0].event_id
    trigger.acknowledge_published_event(events[0])
    assert not trigger.poll()
    assert store.list("subscription")[0]["pending"] == []


def test_example_workflows_validate_and_use_registered_interfaces():
    from evander.workflow.packs import WorkflowPack, compose_workflow_ir
    from evander.workflow.contracts import effective_node_output_schema

    root = Path(__file__).resolve().parents[1]
    pack = WorkflowPack.model_validate_json(
        (root / "docs/examples/miro-workflows.v1.json").read_text()
    )
    providers = register_providers()
    assert len(pack.graphs) == 4
    composed = compose_workflow_ir(
        project_dir=root, workflow_packs=[], inline_ir=pack.ir.model_dump()
    )
    assert len(composed.triggers) == 4
    for entry in pack.ir.nodes:
        node = entry.node
        if ":miro:" in node.type:
            providers.node_definition_resolvers[node.type](node)
            assert "snapshot_id" in effective_node_output_schema(node)
    assert all(graph.node_ids for graph in pack.graphs)


def test_workflow_directory_is_derived_from_context(tmp_path):
    from evander_miro_addon.runtime import provider_service

    service = provider_service(
        SimpleNamespace(
            provider_config={"runtime_dir": str(tmp_path)},
            metadata={"directory_path": "root/project"},
        ),
        node_id="review",
    )
    assert service.access.directory_path == "root/project"
    assert service.access.workflow
    assert service.access.actor.endswith(":review")
    with pytest.raises(ValueError):
        OperationInput.model_validate({"connection_id": "architecture", "directory_path": "root"})
