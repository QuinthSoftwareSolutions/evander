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
        self.next_item_id = 0
        self.create_board_result = {"id": "created-board"}

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
                    result = {"ok": True}
                    if operation in {"create_canvas", "update_canvas"}:
                        existing, submitted = (
                            ET.fromstring(remote.svg),
                            ET.fromstring(values["svg"]),
                        )
                        for element in list(submitted):
                            item_id = element.get("data-miro-id")
                            previous = (
                                next(
                                    (
                                        item
                                        for item in existing
                                        if item.get("data-miro-id") == item_id
                                    ),
                                    None,
                                )
                                if item_id
                                else None
                            )
                            if previous is not None:
                                index = list(existing).index(previous)
                                existing.remove(previous)
                                if element.get("data-deleted") != "true":
                                    existing.insert(index, element)
                            elif not item_id:
                                element.set("data-miro-id", f"created{remote.next_item_id}")
                                remote.next_item_id += 1
                                existing.append(element)
                        remote.svg = (
                            ET.tostring(existing, encoding="unicode")
                            .replace("ns0:", "")
                            .replace(":ns0", "")
                        )
                        result = {
                            "result_svg": ET.tostring(submitted, encoding="unicode")
                            .replace("ns0:", "")
                            .replace(":ns0", "")
                        }
                    elif operation == "reply_comment":
                        remote.comments[0]["replies"].append(
                            {"id": "reply1", "text": values["text"]}
                        )
                    elif operation == "resolve_comment":
                        remote.comments[0]["resolved"] = values["resolved"]
                    if remote.fail_after_write:
                        raise TimeoutError("response lost after remote commit")
                    return remote.create_board_result if operation == "create_board" else result
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
    discovery = next(tool.inputSchema for tool in tools if tool.name == "miro_list_connections")
    assert not discovery.get("required")
    assert "connection_id" not in discovery["properties"]


@pytest.mark.parametrize("scope", ["root/other", "root"])
def test_access_rejected_before_network(setup, scope):
    store, _, remote, _ = setup
    service = MiroService(
        store, Access(scope, "session:author", session_id="author"), remote.client
    )
    with pytest.raises(MiroError, match="unavailable"):
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
    store.save_connection(config(boards=[]), record["revision"])
    with pytest.raises(MiroError, match="changed"):
        run(remote.client(store, record).call("read_canvas", board_id="board1"))
    with pytest.raises(MiroError, match="reload"):
        store.save_connection(config(), record["revision"])
    with pytest.raises(MiroError, match="outside"):
        read(service)


@pytest.mark.parametrize("legacy_sessions", [[], ["another-session"]])
def test_legacy_session_lists_do_not_gate_or_survive_connection_saves(setup, legacy_sessions):
    store, record, remote, service = setup
    record["config"]["session_ids"] = legacy_sessions
    store.put("connection", record["key"], record)
    assert read(service)["data"]["item_ids"] == ["item1", "item2"]
    migrated = Connection.model_validate(record["config"])
    assert "session_ids" not in migrated.model_dump()
    saved = store.save_connection(migrated, record["revision"])
    assert "session_ids" not in saved["config"]
    assert saved["generation"] == record["generation"]
    assert "session_ids" not in Connection.model_json_schema()["properties"]


def test_mcp_uses_normal_session_tool_policy_and_transport_identity(setup, monkeypatch):
    from datetime import UTC, datetime, timedelta

    from mcp.server.fastmcp.exceptions import ToolError
    from starlette.requests import Request

    from evander.config import GatewayConfig
    from evander.models import ApprovalRequest, SessionManifest
    from evander.policy import PolicyEngine
    from evander.repositories.session_manifests import JsonSessionManifestRepository
    from evander.storage import JsonStore
    from evander_miro_addon.mcp import MCP_TOOL_NAMES

    store, _, remote, _ = setup
    runtime_dir = store.root.parents[1]
    # No per-connection session list: the normal tool grant is sufficient.
    store.save_connection(config(directory_path="root", allow_workflows=False), 0)
    gateway = GatewayConfig(
        runtime_dir=runtime_dir,
        directories_store_path=runtime_dir / "directories.json",
        session_store_path=runtime_dir / "sessions.json",
        approval_store_path=runtime_dir / "approvals.json",
    )
    policy = PolicyEngine(
        gateway,
        JsonSessionManifestRepository(gateway.session_store_path),
        JsonStore(gateway.approval_store_path, ApprovalRequest),
        extra_mcp_tool_names=list(MCP_TOOL_NAMES),
    )
    session = SessionManifest(
        session_id="fresh-agent",
        directory_path="root",
        allowed_tools=["miro_read_canvas"],
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    policy.session_store.upsert_session(session)
    monkeypatch.setattr(
        "evander_miro_addon.mcp.MiroService",
        lambda store, access: MiroService(store, access, remote.client),
    )
    server = FastMCP("miro-policy-test")
    server._evander_transport_session_map = {"transport-author": session.session_id}
    register_addon().mcp_tools(
        server,
        AddonContext(
            addon_id="miro-addon",
            runtime_dir=runtime_dir,
            addon_runtime_dir=store.root,
            service=SimpleNamespace(policy=policy),
        ),
    )
    request = Request(
        {
            "type": "http",
            "path": "/mcp",
            "query_string": b"",
            "headers": [(b"mcp-session-id", b"transport-author")],
        }
    )
    context = SimpleNamespace(request_context=SimpleNamespace(request=request))
    arguments = {"connection_id": "architecture", "board_id": "board1"}

    def call(tool="miro_read_canvas", **changes):
        return run(server._tool_manager.call_tool(tool, arguments | changes, context=context))

    assert call()["data"]["item_ids"] == ["item1", "item2"]
    count = len(remote.calls)
    with pytest.raises(ToolError, match="outside"):
        call(board_id="unapproved")
    with pytest.raises(ToolError):
        call(session_id="someone-else")
    with pytest.raises(ToolError, match="not allowed"):
        call("miro_read_comments")
    policy.session_store.upsert_session(session.model_copy(update={"allowed_tools": []}))
    with pytest.raises(ToolError, match="not allowed"):
        call()
    assert len(remote.calls) == count


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


def test_scope_ids_and_explicit_deletes_fail_closed(setup):
    _, _, remote, service = setup
    before = read(service, frame_id="frame1")
    with pytest.raises(MiroError, match="selection must match"):
        run(service.execute("update_canvas", update_args(before)))
    before = read(service)
    deleting = SVG.replace('data-miro-id="item2"', 'data-miro-id="item2" data-deleted="true"')
    with pytest.raises(MiroError, match="delete"):
        run(
            service.execute(
                "update_canvas",
                update_args(before, svg=deleting),
            )
        )
    assert not any(op == "update_canvas" for op, _ in remote.calls)
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


@pytest.mark.parametrize(
    "response",
    [
        {"id": "created-board"},
        {"miro_url": "https://miro.com/app/board/created-board/"},
        {"board": {"miro_url": "https://miro.com/app/board/created-board/"}},
        {"result": {"data": {"miro_url": "https://miro.com/app/board/created-board/"}}},
        {"url": "https://miro.com/app/board/created-board/"},
        {"board_url": "https://miro.com/app/board/created-board/"},
        {"boardUrl": "https://miro.com/app/board/created-board/"},
    ],
    ids=["id", "miro_url", "nested_board", "wrapped_data", "url", "board_url", "boardUrl"],
)
def test_create_board_binds_result_and_new_canvas_remains_editable(setup, response):
    store, _, remote, service = setup
    remote.create_board_result = response
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
                "board_id": created["board_id"],
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


@pytest.mark.parametrize("fail_readback", [False, True])
def test_create_board_retry_at_capacity_preserves_durable_outcome(setup, fail_readback):
    store, record, remote, service = setup
    remote.create_board_result = {"miro_url": "https://miro.com/app/board/created-board/"}
    store.save_connection(
        config(boards=[{"board_id": f"board{i}", "writable": True} for i in range(199)]),
        record["revision"],
    )
    remote.fail_read = fail_readback
    arguments = {
        "connection_id": "architecture",
        "title": "Last available board",
        "operation_key": "last-board",
    }
    result = run(service.execute("create_board", arguments))
    assert result["status"] == ("uncertain" if fail_readback else "applied")
    assert len(store.resolve_connection("root/project", "architecture")["config"]["boards"]) == 200
    calls = list(remote.calls)

    assert run(service.execute("create_board", arguments)) == result
    with pytest.raises(MiroError, match="different arguments"):
        run(service.execute("create_board", arguments | {"title": "Different board"}))
    with pytest.raises(MiroError, match="board limit"):
        run(service.execute("create_board", arguments | {"operation_key": "another-board"}))
    assert remote.calls == calls


@pytest.mark.parametrize(
    "url",
    [
        "http://miro.com/app/board/created-board/",
        "https://miro.com.evil.test/app/board/created-board/",
        "https://miro.com/app/dashboard/created-board/",
    ],
)
def test_create_board_rejects_untrusted_miro_url_without_approving_board(setup, url):
    store, record, remote, service = setup
    remote.create_board_result = {"miro_url": url}
    result = run(
        service.execute(
            "create_board",
            {"connection_id": "architecture", "title": "New board", "operation_key": "new"},
        )
    )
    assert result["status"] == "uncertain"
    assert result["error"] == "unsupported_response"
    assert result["board_id"] is None
    assert store.resolve_connection("root/project", "architecture") == record
    assert [operation for operation, _ in remote.calls] == ["create_board"]


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


@pytest.mark.parametrize("emit_initial", [False, True])
def test_polling_connection_override_resets_baseline_and_preserves_event_identity(
    setup, emit_initial
):
    store, _, remote, _ = setup
    service = MiroService(
        store, Access("root/project/child", "workflow:child", workflow=True), remote.client
    )
    arguments = {"connection_id": "architecture", "board_id": "board1"}
    previous_events = run(
        service.poll("feedback", arguments, kind="canvas", emit_initial=emit_initial)
    )
    previous = store.list("subscription")[0]
    child = store.save_connection(config(directory_path="root/project/child"), 0)
    events = run(service.poll("feedback", arguments, kind="canvas", emit_initial=emit_initial))
    state = store.list("subscription")[0]
    assert state["connection_key"] == child["key"]
    assert state["snapshot_id"] != previous["snapshot_id"]
    snapshot = run(
        service.execute(
            "get_snapshot",
            {"connection_id": "architecture", "snapshot_id": state["snapshot_id"]},
        )
    )
    assert snapshot["data"]["item_ids"] == ["item1", "item2"]
    if emit_initial:
        assert len(events) == 1
        assert events[0]["previous_snapshot_id"] is None
        assert events[0]["event_id"] != previous_events[0]["event_id"]
        # A late acknowledgment for the old connection cannot consume the new event.
        run(service.acknowledge(previous["key"], previous_events[0]["event_id"]))
        assert store.get("subscription", state["key"])["pending"] == events
        run(service.acknowledge(state["key"], events[0]["event_id"]))
    else:
        assert events == []
    calls = list(remote.calls)
    restarted = MiroService(MiroStore(store.root.parents[1]), service.access, remote.client)
    assert (
        run(restarted.poll("feedback", arguments, kind="canvas", emit_initial=emit_initial)) == []
    )
    assert remote.calls == calls


def test_polling_connection_override_does_not_replay_old_event_after_read_failure(
    setup, monkeypatch
):
    store, _, remote, _ = setup
    clock = [100000.0]
    monkeypatch.setattr("evander_miro_addon.service.time.time", lambda: clock[0])
    service = MiroService(
        store, Access("root/project/child", "workflow:child", workflow=True), remote.client
    )
    arguments = {"connection_id": "architecture", "board_id": "board1"}
    previous = run(
        service.poll("feedback", arguments, kind="canvas", interval=60, emit_initial=True)
    )[0]
    child = store.save_connection(config(directory_path="root/project/child"), 0)
    remote.fail_read = True
    with pytest.raises(MiroError, match="Offline"):
        run(service.poll("feedback", arguments, kind="canvas", interval=60, emit_initial=True))
    state = store.list("subscription")[0]
    assert state["connection_key"] == child["key"]
    assert state["snapshot_id"] is None and state["checked_at"] == 0
    assert state["pending"] == []
    assert state["last_error"] == "upstream_unavailable"
    assert (
        run(service.poll("feedback", arguments, kind="canvas", interval=60, emit_initial=True))
        == []
    )
    remote.fail_read = False
    clock[0] += 61
    event = run(service.poll("feedback", arguments, kind="canvas", interval=60, emit_initial=True))[
        0
    ]
    assert event["event_id"] != previous["event_id"]
    assert event["previous_snapshot_id"] is None


@pytest.mark.parametrize("wait_on", ["subscription", "board"])
def test_polling_rechecks_connection_after_waiting_for_lock(setup, monkeypatch, wait_on):
    store, _, remote, _ = setup
    clock = [100000.0]
    monkeypatch.setattr("evander_miro_addon.service.time.time", lambda: clock[0])
    service = MiroService(
        store, Access("root/project/child", "workflow:child", workflow=True), remote.client
    )
    arguments = {"connection_id": "architecture", "board_id": "board1"}

    async def poll():
        return await service.poll(
            "feedback", arguments, kind="canvas", interval=60, emit_initial=True
        )

    previous = run(poll())[0]
    if wait_on == "board":
        run(service.acknowledge(previous["subscription_key"], previous["event_id"]))
        clock[0] += 61
    lock_name = (
        "subscription:" + previous["subscription_key"]
        if wait_on == "subscription"
        else "board:board1"
    )
    original_lock = store.lock
    calls = list(remote.calls)

    async def scenario():
        waiting = asyncio.Event()

        @asynccontextmanager
        async def watched_lock(name, **kwargs):
            if name == lock_name and asyncio.current_task().get_name() == "poll":
                waiting.set()
            async with original_lock(name, **kwargs):
                yield

        monkeypatch.setattr(store, "lock", watched_lock)
        async with original_lock(lock_name):
            pending = asyncio.create_task(poll(), name="poll")
            await waiting.wait()
            child = store.save_connection(config(directory_path="root/project/child"), 0)
        if wait_on == "board":
            with pytest.raises(MiroError, match="binding changed"):
                await pending
            assert remote.calls == calls
            events = await poll()
        else:
            events = await pending
        state = store.list("subscription")[0]
        assert state["connection_key"] == child["key"]
        assert events[0]["event_id"] != previous["event_id"]
        assert events[0]["previous_snapshot_id"] is None
        await service.execute(
            "get_snapshot",
            {"connection_id": "architecture", "snapshot_id": events[0]["snapshot_id"]},
        )

    run(asyncio.wait_for(scenario(), 2))


def test_polling_rechecks_connection_after_remote_read(setup, monkeypatch):
    store, parent, remote, _ = setup
    clock = [100000.0]
    monkeypatch.setattr("evander_miro_addon.service.time.time", lambda: clock[0])
    service = MiroService(
        store, Access("root/project/child", "workflow:child", workflow=True), remote.client
    )
    arguments = {"connection_id": "architecture", "board_id": "board1"}

    async def poll():
        return await service.poll("feedback", arguments, kind="canvas", interval=60)

    assert run(poll()) == []
    previous = store.list("subscription")[0]
    remote.svg = SVG.replace("Order service", "Human revision")
    clock[0] += 61

    async def scenario():
        received, finish = asyncio.Event(), asyncio.Event()

        def factory(store, record):
            client = remote.client(store, record)
            original = client.call

            async def call(operation, **values):
                result = await original(operation, **values)
                if operation == "read_canvas" and record["key"] == parent["key"]:
                    received.set()
                    await finish.wait()
                return result

            client.call = call
            return client

        service.client_factory = factory
        pending = asyncio.create_task(poll())
        await received.wait()
        child = store.save_connection(config(directory_path="root/project/child"), 0)
        finish.set()
        with pytest.raises(MiroError, match="binding changed"):
            await pending
        state = store.list("subscription")[0]
        assert state["snapshot_id"] == previous["snapshot_id"]
        assert state["checked_at"] == previous["checked_at"]
        assert state["pending"] == []
        # A retry establishes the child's baseline without emitting the old connection's edit.
        assert await poll() == []
        state = store.list("subscription")[0]
        assert state["connection_key"] == child["key"]
        snapshot = await service.execute(
            "get_snapshot",
            {"connection_id": "architecture", "snapshot_id": state["snapshot_id"]},
        )
        assert "Human revision" in snapshot["data"]["svg"]

    run(asyncio.wait_for(scenario(), 2))


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
    with pytest.raises(MiroError, match="no progress"):
        comment_page({"comments": [], "has_more": True})
    assert comment_page({"comments": [{"id": "c"}], "total": 2, "limit": 1})[1] == 1


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
            repository = MiroStore(tmp_path)
            record = repository.resolve_connection("root/project", "architecture")
            record["config"]["session_ids"] = ["retired-session"]
            repository.put("connection", record["key"], record)
            assert "session_ids" not in (await client.get(path)).json()["connections"][0]["config"]
            assert (await client.post(path, json=payload)).status_code == 409
            assert (await client.get(path)).json()["connections"][0]["connected"] is False
            assert "encrypted" not in (await client.get(path)).text
            assert (
                await client.get("/api/addons/miro-addon/connections?directory_path=root")
            ).status_code == 403
            auth.allowed = False
            assert (await client.get(path)).status_code == 403

    run(scenario())


@pytest.mark.parametrize(
    "operation_directory,allowed",
    [
        ("root/project", False),
        ("root/project/sibling", False),
        ("root/project/child-other", False),
        ("root/project/child", True),
        ("root/project/child/nested", True),
    ],
)
@pytest.mark.parametrize("boardless", [False, True])
@pytest.mark.parametrize("status", ["running", "uncertain"])
def test_admin_recovery_enforces_operation_directory(
    setup, monkeypatch, operation_directory, allowed, boardless, status
):
    from evander.admin.web.rbac import BUILTIN_GROUP_DEFINITIONS, can

    store, _, remote, _ = setup
    author = MiroService(
        store, Access(operation_directory, "session:author", session_id="author"), remote.client
    )
    arguments = (
        {"connection_id": "architecture", "title": "Uncertain board", "operation_key": "new"}
        if boardless
        else update_args(read(author))
    )
    remote.fail_after_write = True
    operation = run(author.execute("create_board" if boardless else "update_canvas", arguments))
    remote.fail_after_write = False
    operation_id = operation["operation_id"]
    entry = store.get("operation", operation_id) | {"status": status}
    store.put("operation", operation_id, entry)
    calls = list(remote.calls)

    class ScopedOwner(AdminAuth):
        def require_permission(self, request, permission, directory_path=None):
            self.require(request)
            if not can(
                assignments=[("owner", "root/project/child")],
                group_permissions={
                    name: definition["permissions"]
                    for name, definition in BUILTIN_GROUP_DEFINITIONS.items()
                },
                direct_workflow_grants=[],
                permission=permission,
                directory_path=directory_path,
            ):
                raise PermissionError()

    monkeypatch.setattr(
        "evander_miro_addon.api.MiroService",
        lambda store, access: MiroService(store, access, remote.client),
    )
    context = AddonContext(
        addon_id="miro-addon",
        runtime_dir=store.root.parents[1],
        addon_runtime_dir=store.root,
        auth=ScopedOwner(),
        service=SimpleNamespace(),
    )
    app = Starlette(routes=[Mount("/api/addons/miro-addon", routes=admin_api_routes(context))])

    async def scenario():
        path = "/api/addons/miro-addon/connections/architecture"
        scope = "?directory_path=root/project/child"
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://evander.example"
        ) as client:
            assert (
                await client.get(path + "/activity?directory_path=root/project")
            ).status_code == 403
            activity = await client.get(path + "/activity" + scope)
            assert activity.status_code == 200
            assert bool(activity.json()["operations"]) is allowed
            recovery_path = path + "/operations/" + operation_id
            readback = await client.post(recovery_path + "/reconcile" + scope, json={})
            resolution = await client.post(
                recovery_path + "/resolve" + scope,
                json={"outcome": "applied", "evidence": "Inspected the board in Miro"},
            )
            assert readback.status_code == resolution.status_code == (200 if allowed else 403)
        saved = store.get("operation", operation_id)
        if allowed:
            assert saved["status"] == "applied"
        else:
            assert saved == entry
            assert remote.calls == calls
            with pytest.raises(MiroError, match="reconciliation"):
                await author.execute(
                    "create_board" if boardless else "update_canvas",
                    arguments | {"operation_key": "after-denied-recovery"},
                )

    run(scenario())


def test_recovery_preserves_shared_reads_and_removed_board_grants(setup):
    store, record, remote, author = setup
    baseline = read(author)
    remote.fail_after_write = True
    operation = run(author.execute("update_canvas", update_args(baseline)))
    remote.fail_after_write = False
    child_agent = MiroService(
        store,
        Access("root/project/child", "session:reviewer", session_id="reviewer"),
        remote.client,
    )
    assert (
        run(
            child_agent.execute(
                "get_operation",
                {"connection_id": "architecture", "operation_id": operation["operation_id"]},
            )
        )
        == operation
    )

    store.save_connection(config(boards=[]), record["revision"])
    operator = MiroService(
        store, Access("root/project", "operator:jonathan", admin=True), remote.client
    )
    observed = run(operator.reconcile("architecture", operation["operation_id"]))
    assert "with retry" in observed["observed"]["data"]["svg"]
    assert (
        run(
            operator.resolve_operation(
                "architecture", operation["operation_id"], "applied", "Inspected"
            )
        )["status"]
        == "applied"
    )


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
                            "properties": {
                                "miro_url": {"type": "string"},
                                "widget_ids": {"type": "array", "items": {"type": "string"}},
                            },
                            "required": ["miro_url"],
                            "additionalProperties": False,
                        },
                    }
                ]
            }
        else:
            assert payload["method"] == "tools/call"
            assert payload["params"]["name"] == "canvas_read_as_svg"
            assert payload["params"]["arguments"]["miro_url"].endswith(
                "/board1/?focusWidget=frame1"
            )
            assert payload["params"]["arguments"]["widget_ids"] == ["item1"]
            result = {"content": [{"type": "text", "text": SVG}]}
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": payload["id"], "result": result})

    async def scenario():
        async with MiroClient(
            store, record, http_transport=httpx.MockTransport(handle)
        ).open() as client:
            result = await client.call(
                "read_canvas", board_id="board1", frame_id="frame1", item_ids=["item1"]
            )
            assert result["text"] == SVG
        with pytest.raises(MiroError, match="source snapshot"):
            async with MiroClient(store, record, http_transport=httpx.MockTransport(handle)).open():
                raise MiroError("conflict", "changed source snapshot")

    run(scenario())
    assert store.usage(record) == 1
    assert [request["method"] for request in requests].count("initialize") == 2


@pytest.mark.parametrize("caller", ["agent", "workflow"])
@pytest.mark.parametrize("response_format", ["structured", "json_text"])
def test_create_board_miro_url_through_mcp_and_workflow(
    setup, monkeypatch, caller, response_format
):
    from datetime import UTC, datetime, timedelta

    from evander.config import GatewayConfig
    from evander.directories import DirectoryService
    from evander.gateway import create_server
    from evander.models import SessionManifest
    from evander.runtime import build_runtime
    from evander_miro_addon.client import MiroClient
    from evander_miro_addon.models import READ_OPERATIONS

    store, record, _, _ = setup
    runtime_dir = store.root.parents[1]
    board_id = "uXjCreated="
    board_url = "https://miro.com/app/board/uXjCreated%3D/"
    calls = []

    async def token(_self, _record):
        return "synthetic-transport-token"

    def handle(request):
        if request.method != "POST":
            return httpx.Response(405)
        payload = json.loads(request.content)
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
                        "name": name,
                        "inputSchema": {
                            "type": "object",
                            "properties": {field: {"type": "string"}},
                            "required": [field],
                            "additionalProperties": False,
                        },
                    }
                    for name, field in [
                        ("board_create", "name"),
                        ("canvas_read_as_svg", "miro_url"),
                    ]
                ]
            }
        else:
            assert payload["method"] == "tools/call"
            params = payload["params"]
            calls.append(params["name"])
            if params["name"] == "board_create":
                assert params["arguments"] == {"name": "Truman Test"}
                data = {"miro_url": board_url}
            else:
                assert params["name"] == "canvas_read_as_svg"
                assert params["arguments"] == {"miro_url": board_url}
                data = {"svg": SVG}
            result = (
                {"content": [], "structuredContent": data}
                if response_format == "structured"
                else {"content": [{"type": "text", "text": json.dumps(data)}]}
            )
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": payload["id"], "result": result})

    original_init = MiroClient.__init__

    def init_client(self, store, record):
        original_init(self, store, record, http_transport=httpx.MockTransport(handle))

    monkeypatch.setattr(MiroOAuth, "access_token", token)
    monkeypatch.setattr(MiroClient, "__init__", init_client)
    if caller == "agent":
        runtime = build_runtime(
            GatewayConfig(
                runtime_dir=runtime_dir,
                addon_loader=AddonLoaderConfig(modules=["evander_miro_addon"], package_dirs=[]),
            )
        )
        DirectoryService(runtime.directories).create(parent="root", name="project")
        runtime.session_repository.upsert_session(
            SessionManifest(
                session_id="author",
                directory_path="root/project",
                expires_at=datetime.now(UTC) + timedelta(hours=1),
                allowed_tools=["miro_create_board", "miro_list_boards", "miro_read_canvas"],
            )
        )
        server = create_server(runtime)

        def invoke(operation, arguments):
            return run(
                server._tool_manager.call_tool(
                    "miro_" + operation, {"session_id": "author", **arguments}
                )
            )

    else:
        providers = register_providers()
        context = SimpleNamespace(
            provider_config={"runtime_dir": str(runtime_dir)},
            metadata={"directory_path": "root/project"},
        )

        def invoke(operation, arguments):
            query = operation in READ_OPERATIONS
            kind = ("query" if query else "effect") + ":miro:" + operation.replace("_", "-")
            registry = providers.query_providers if query else providers.effect_providers
            provider = registry[kind]()
            provider.initialize(
                WorkflowNodeEnvelope(
                    id=operation, display_name=operation, type=kind, config=arguments
                ),
                context,
            )
            return provider.execute()["result"]

    arguments = {
        "connection_id": "architecture",
        "title": "Truman Test",
        "operation_key": "create-truman-test",
    }
    created = invoke("create_board", arguments)
    assert created["status"] == "applied"
    assert created["board_id"] == board_id
    assert created["url"] == board_url
    assert store.get("snapshot", created["result_snapshot_id"])["board_id"] == board_id
    boards = invoke("list_boards", {"connection_id": "architecture"})["boards"]
    assert [board for board in boards if board["board_id"] == board_id] == [
        {"board_id": board_id, "name": "Truman Test", "writable": True, "url": board_url}
    ]
    observed = invoke("read_canvas", {"connection_id": "architecture", "board_id": board_id})
    assert observed["snapshot_id"] == created["result_snapshot_id"]
    assert observed["data"]["item_ids"] == ["item1", "item2"]
    assert invoke("create_board", arguments) == created
    assert calls == ["board_create", "canvas_read_as_svg", "canvas_read_as_svg"]
    assert store.usage(record) == 3


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
            if kwargs["offset"] == 0:
                return {"comments": [{"id": "first"}], "total": 2, "limit": 1, "offset": 0}
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
            if kwargs["offset"] == 1:
                return {
                    "comments": [{"id": "second", "text": "Second question"}],
                    "total": 2,
                    "offset": 1,
                }
            return {
                "comments": [{"id": "first", "text": "First question"}],
                "total": 2,
                "limit": 1,
                "offset": 0,
            }

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


@pytest.mark.parametrize("allow_delete", [False, True])
def test_partial_canvas_update_preserves_omitted_items(setup, allow_delete):
    _, _, remote, service = setup
    before = read(service)
    partial = '<svg xmlns="http://www.w3.org/2000/svg"><text data-miro-id="item1" x="10" y="20">Revised</text></svg>'
    result = run(
        service.execute(
            "update_canvas", update_args(before, svg=partial, allow_delete=allow_delete)
        )
    )
    assert result["status"] == "applied"
    assert result["affected_item_ids"] == ["item1"]
    assert "Revised" in remote.svg
    assert 'data-miro-id="item2"' in remote.svg


def test_approved_canvas_deletion_removes_only_marked_items(setup):
    _, _, remote, service = setup
    before = read(service)
    deleting = SVG.replace('data-miro-id="item2"', 'data-miro-id="item2" data-deleted="true"')
    result = run(
        service.execute("update_canvas", update_args(before, svg=deleting, allow_delete=True))
    )
    assert result["status"] == "applied"
    assert result["affected_item_ids"] == ["item2"]
    assert 'data-miro-id="item2"' not in remote.svg
    assert 'data-miro-id="item1"' in remote.svg


def test_creation_receipt_supports_mixed_svg_text(setup):
    _, _, remote, service = setup
    result = run(
        service.execute(
            "create_canvas",
            {
                "connection_id": "architecture",
                "board_id": "board1",
                "operation_key": "formatted-text",
                "svg": '<svg xmlns="http://www.w3.org/2000/svg"><text>Hello <tspan>world</tspan> today</text></svg>',
            },
        )
    )
    assert result["status"] == "applied"
    assert result["affected_item_ids"] == ["created0"]
    assert " today" in remote.svg


@pytest.mark.parametrize(
    "attributes", ['data-deleted="true"', 'data-miro-id="item1" data-deleted="TRUE"']
)
def test_canvas_deletion_marker_requires_an_id_and_boolean_value(attributes):
    with pytest.raises(MiroError, match="Deletion requires"):
        canvas(f"<svg><rect {attributes}/></svg>")


@pytest.mark.parametrize("metadata,count", [(True, 55), (False, 55), (False, 100)])
def test_real_client_reads_offset_comment_pages_before_snapshot(setup, metadata, count):
    from evander_miro_addon.client import MiroClient

    store, record, _, service = setup
    comments = [{"id": f"thread{i}", "text": f"Question {i}"} for i in range(count)]
    calls = []

    class Session:
        async def call_tool(self, tool, arguments):
            calls.append(arguments)
            assert tool == "comment_list_comments"
            assert arguments["miro_url"] == "https://miro.com/app/board/board1/"
            assert arguments["limit"] == 50
            offset = arguments["offset"]
            data = {"comments": comments[offset : offset + arguments["limit"]]}
            if metadata:
                data["pagination"] = {"offset": offset, "limit": 50, "total": count}
            return SimpleNamespace(isError=False, structuredContent=data)

    def factory(store, record):
        client = MiroClient(store, record)
        client.schemas["comment_list_comments"] = {
            "type": "object",
            "properties": {
                "miro_url": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50},
                "offset": {"type": "integer", "minimum": 0},
            },
            "required": ["miro_url"],
            "additionalProperties": False,
        }
        client.session = Session()

        @asynccontextmanager
        async def open_client():
            yield client

        client.open = open_client
        return client

    service.client_factory = factory
    snapshot = read(service, "comments")
    assert {item["id"] for item in snapshot["data"]["comments"]} == {
        item["id"] for item in comments
    }
    assert [call["offset"] for call in calls] == ([0, 50, 100] if count == 100 else [0, 50])
    assert store.usage(record) == len(calls)
    assert len(store.list("snapshot")) == 1


@pytest.mark.parametrize(
    "paging",
    [
        {"offset": 1, "total": 2},
        {"total": 2, "has_more": False},
        {"total": 0},
        {"limit": 0},
        {"nextCursor": "unsupported"},
    ],
)
def test_invalid_comment_pagination_never_advances_poll_checkpoint(setup, paging):
    store, _, remote, service = setup

    def factory(store, record):
        client = remote.client(store, record)

        async def call(operation, **values):
            return {"comments": [{"id": "first"}], **paging}

        client.call = call
        return client

    service.client_factory = factory
    with pytest.raises(MiroError):
        run(
            service.poll(
                "feedback", {"connection_id": "architecture", "board_id": "board1"}, kind="comments"
            )
        )
    assert store.list("snapshot") == []
    assert store.list("subscription")[0]["snapshot_id"] is None


def test_shared_operation_key_is_serialized_across_boards_and_store_instances(setup):
    store, record, remote, author = setup
    store.save_connection(
        config(
            boards=[
                {"board_id": "board1", "writable": True},
                {"board_id": "board2", "writable": True},
            ]
        ),
        record["revision"],
    )

    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()

        def factory(store, record):
            client = remote.client(store, record)

            @asynccontextmanager
            async def open_client():
                entered.set()
                await release.wait()
                yield client

            client.open = open_client
            return client

        author.client_factory = factory
        other = MiroService(MiroStore(store.root.parents[1]), author.access, factory)
        args = {"connection_id": "architecture", "svg": NEW, "operation_key": "shared"}
        first = asyncio.create_task(author.execute("create_canvas", args | {"board_id": "board1"}))
        await entered.wait()
        second = asyncio.create_task(other.execute("create_canvas", args | {"board_id": "board2"}))
        await asyncio.sleep(0)
        release.set()
        return await asyncio.gather(first, second, return_exceptions=True)

    first, second = run(asyncio.wait_for(scenario(), 2))
    assert first["status"] == "applied"
    assert isinstance(second, MiroError) and second.code == "conflict"
    assert [values["board_id"] for op, values in remote.calls if op == "create_canvas"] == [
        "board1"
    ]
    assert len(store.list("operation")) == 1
    assert store.list("operation")[0]["board_id"] == "board1"


def test_readback_waiting_on_writer_preserves_completed_operation(setup):
    from evander_miro_addon.state import digest

    store, record, remote, author = setup
    before = read(author)
    operation_id = digest([record["key"], "update-retries"])

    async def scenario():
        sent, finish, waiting = asyncio.Event(), asyncio.Event(), asyncio.Event()

        def factory(store, record):
            client = remote.client(store, record)
            original = client.call

            async def call(operation, **values):
                if operation == "update_canvas":
                    sent.set()
                    await finish.wait()
                return await original(operation, **values)

            client.call = call
            return client

        author.client_factory = factory
        operator = MiroService(store, Access("root/project", "operator:test", admin=True), factory)
        original_lock = store.lock

        @asynccontextmanager
        async def watched_lock(name, **kwargs):
            if asyncio.current_task().get_name() == "reconcile":
                waiting.set()
            async with original_lock(name, **kwargs):
                yield

        store.lock = watched_lock
        writer = asyncio.create_task(author.execute("update_canvas", update_args(before)))
        await sent.wait()
        reconciler = asyncio.create_task(
            operator.reconcile("architecture", operation_id), name="reconcile"
        )
        await waiting.wait()
        finish.set()
        return await writer, await reconciler

    written, reconciled = run(asyncio.wait_for(scenario(), 2))
    assert written["status"] == reconciled["operation"]["status"] == "applied"
    saved = store.get("operation", operation_id)
    assert saved["result_snapshot_id"] == written["result_snapshot_id"]
    assert saved["completed_at"] == written["completed_at"]
    assert saved["upstream_acknowledged"] is True
    assert saved["reconciliation_snapshot_id"]
    # The completed operation must not leave the board blocked for subsequent writes.
    assert (
        run(author.execute("update_canvas", update_args(read(author), operation_key="next")))[
            "status"
        ]
        == "applied"
    )


@pytest.mark.parametrize(
    "operation,human_change",
    [
        ("update_canvas", lambda svg: svg.replace('width="100"', 'width="120"')),
        ("update_canvas", lambda svg: svg.replace("with retry", "with human feedback")),
        (
            "create_canvas",
            lambda svg: svg.replace("</svg>", '<text data-miro-id="human">Human note</text></svg>'),
        ),
        ("create_canvas", lambda svg: svg.replace("Retry flow", "Human correction")),
    ],
)
def test_polling_preserves_human_edits_between_write_and_readback(
    setup, monkeypatch, operation, human_change
):
    _, _, remote, service = setup
    clock = [100000.0]
    monkeypatch.setattr("evander_miro_addon.service.time.time", lambda: clock[0])
    args = {"connection_id": "architecture", "board_id": "board1"}
    run(service.poll("feedback", args, kind="canvas", interval=60))
    baseline = read(service)

    def factory(store, record):
        client = remote.client(store, record)
        original = client.call

        async def call(name, **values):
            result = await original(name, **values)
            if name == operation:
                remote.svg = human_change(remote.svg)
            return result

        client.call = call
        return client

    service.client_factory = factory
    clock[0] += 10
    inputs = (
        update_args(baseline)
        if operation == "update_canvas"
        else args | {"svg": NEW, "operation_key": "add"}
    )
    written = run(service.execute(operation, inputs))
    assert written["status"] == "applied"
    assert "item2" not in written["affected_item_ids"]
    assert "human" not in written["affected_item_ids"]
    clock[0] += 61
    events = run(service.poll("feedback", args, kind="canvas", interval=60))
    assert len(events) == 1 and events[0]["origin"] == "external_or_unknown"


def test_missing_creation_receipt_does_not_hide_observed_additions(setup, monkeypatch):
    _, _, remote, service = setup
    clock = [100000.0]
    monkeypatch.setattr("evander_miro_addon.service.time.time", lambda: clock[0])
    args = {"connection_id": "architecture", "board_id": "board1"}
    run(service.poll("feedback", args, kind="canvas", interval=60))

    def factory(store, record):
        client = remote.client(store, record)
        original = client.call

        async def call(operation, **values):
            result = await original(operation, **values)
            return {"ok": True} if operation == "create_canvas" else result

        client.call = call
        return client

    service.client_factory = factory
    clock[0] += 10
    result = run(service.execute("create_canvas", args | {"svg": NEW, "operation_key": "add"}))
    assert result["status"] == "applied" and result["affected_item_ids"] == []
    clock[0] += 61
    assert (
        run(service.poll("feedback", args, kind="canvas", interval=60))[0]["origin"]
        == "external_or_unknown"
    )


@pytest.mark.parametrize(
    "operation,human_thread",
    [
        ("resolve_comment", None),
        ("resolve_comment", 0),
        ("resolve_comment", 1),
        ("reply_comment", 0),
        ("reply_comment", 1),
    ],
)
def test_comment_write_attribution_preserves_concurrent_human_feedback(
    setup, monkeypatch, operation, human_thread
):
    _, _, remote, service = setup
    remote.comments.append(
        {"id": "thread2", "text": "Another question", "replies": [], "resolved": False}
    )
    clock = [100000.0]
    monkeypatch.setattr("evander_miro_addon.service.time.time", lambda: clock[0])
    args = {"connection_id": "architecture", "board_id": "board1"}
    run(service.poll("feedback", args, kind="comments", interval=60))
    before = read(service, "comments")

    def factory(store, record):
        client = remote.client(store, record)
        original = client.call

        async def call(name, **values):
            result = await original(name, **values)
            if name == operation and human_thread is not None:
                remote.comments[human_thread]["text"] = "Human feedback during the write"
            return result

        client.call = call
        return client

    service.client_factory = factory
    clock[0] += 10
    result = run(
        service.execute(
            operation,
            args
            | {
                "comment_id": "thread1",
                "text": "Agent reply",
                "snapshot_id": before["snapshot_id"],
                "operation_key": "comment-update",
            },
        )
    )
    assert result["status"] == "applied"
    assert "thread2" not in result["affected_item_ids"]
    clock[0] += 61
    events = run(service.poll("feedback", args, kind="comments", interval=60))
    if human_thread is None:
        assert events == []
    else:
        assert len(events) == 1 and events[0]["origin"] == "external_or_unknown"
