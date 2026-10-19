from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
import sqlite3
from types import SimpleNamespace

import httpx
import pytest
from starlette.applications import Starlette
from starlette.routing import Mount

from evander.addons import AddonContext
from evander.messaging.teams_inbox_store import TeamsInboxMessage, TeamsInboxStore
from evander.models import SessionManifest
from evander.repositories.session_manifests import JsonSessionManifestRepository
from evander.workflow.state_store import WorkflowStateStore
from evander_miro_addon.access_workflow import NAMESPACE, grant_access, inspect_access
from evander_miro_addon.api import admin_api_routes
from evander_miro_addon.mcp import MCP_TOOL_NAMES
from evander_miro_addon.models import MiroError
from evander_miro_addon.permissions import board_reference
from evander_miro_addon.service import Access, MiroService
from evander_miro_addon.state import MiroStore
from test_miro_addon import AREA, NEW, AdminAuth, read, run
from test_miro_addon import setup as setup


def peer(store, name="peer", directory="root/project"):
    session = SessionManifest(
        session_id=name,
        directory_path=directory,
        allowed_tools=list(MCP_TOOL_NAMES),
        expires_at=datetime.now(UTC) + timedelta(hours=2),
    )
    JsonSessionManifestRepository(store.runtime_dir / "sessions.json").upsert_session(session)
    return session


def test_shared_tools_and_connection_never_share_board_grants(setup):
    store, record, remote, author = setup
    other = peer(store)
    service = MiroService(
        store,
        Access(other.directory_path, "session:peer", session_id=other.session_id),
        remote.client,
    )
    assert run(service.execute("list_boards", {"connection_id": "architecture"})) == {"boards": []}
    assert service.list_connections()["connections"][0]["approved_board_count"] == 0
    for operation, extra in (
        ("read_canvas", {"canvas_scope": AREA}),
        ("search_canvas", {}),
        ("create_canvas", {"canvas_scope": AREA, "svg": NEW, "operation_key": "blocked"}),
    ):
        with pytest.raises(MiroError) as error:
            run(
                service.execute(
                    operation, {"connection_id": "architecture", "board_id": "board1", **extra}
                )
            )
        assert error.value.code == "board_not_approved"
    assert remote.calls == []
    store.save_board_grant(record, other, "board1", "read", actor="operator:test")
    assert read(service)["data"]["item_ids"] == ["item1", "item2"]
    with pytest.raises(MiroError) as error:
        run(
            service.execute(
                "create_canvas",
                {
                    "connection_id": "architecture",
                    "board_id": "board1",
                    "canvas_scope": AREA,
                    "svg": NEW,
                    "operation_key": "readonly",
                },
            )
        )
    assert error.value.code == "board_read_only"
    assert store.board_grant(record, author.session(), "board1")["access"] == "edit"


def test_deleted_recreated_and_expired_sessions_cannot_reuse_grants(setup):
    store, record, remote, service = setup
    old = service.session()
    snapshot = read(service)
    repository = JsonSessionManifestRepository(store.runtime_dir / "sessions.json")
    replacement = old.model_copy(update={"created_at": old.created_at + timedelta(seconds=1)})
    repository.upsert_session(replacement)
    with pytest.raises(MiroError, match="replaced"):
        read(service)
    fresh = MiroService(store, service.access, remote.client)
    with pytest.raises(MiroError) as error:
        run(
            fresh.execute(
                "get_snapshot",
                {"connection_id": "architecture", "snapshot_id": snapshot["snapshot_id"]},
            )
        )
    assert error.value.code == "board_not_approved"
    assert store.board_grant(record, replacement, "board1") is None
    repository.upsert_session(
        old.model_copy(update={"expires_at": datetime.now(UTC) - timedelta(seconds=1)})
    )
    with pytest.raises(MiroError, match="expired"):
        read(service)


def test_revocation_blocks_cached_artifacts_and_next_external_call(setup):
    store, record, remote, service = setup
    snapshot = read(service)
    grant = store.board_grant(record, service.session(), "board1")
    store.revoke_board_grant(
        grant["grant_id"], expected_revision=grant["revision"], actor="operator:test"
    )
    remote.calls.clear()
    for operation, args in (
        ("read_canvas", {"board_id": "board1", "canvas_scope": AREA}),
        ("get_snapshot", {"snapshot_id": snapshot["snapshot_id"]}),
    ):
        with pytest.raises(MiroError) as error:
            run(service.execute(operation, {"connection_id": "architecture", **args}))
        assert error.value.code == "board_not_approved"
    assert remote.calls == []


def test_new_board_belongs_only_to_creator_and_retry_cannot_share_it(setup):
    store, record, remote, creator = setup
    other = peer(store)
    operation = {"connection_id": "architecture", "title": "New plan", "operation_key": "new-plan"}
    result = run(creator.execute("create_board", operation))
    assert result["status"] == "applied"
    assert store.board_grant(record, creator.session(), "created-board")["access"] == "edit"
    assert store.board_grant(record, other, "created-board") is None
    assert store.resolve_connection("root/project", "architecture")["config"]["boards"] == []
    assert run(creator.execute("create_board", operation)) == result
    assert len([op for op, _ in remote.calls if op == "create_board"]) == 1


def test_workflows_require_session_and_keep_connection_workflow_gate(setup):
    store, record, remote, _ = setup
    missing = MiroService(
        store, Access("root/project", "workflow:test", workflow=True), remote.client
    )
    with pytest.raises(MiroError, match="require an agent session"):
        read(missing)
    workflow = MiroService(
        store,
        Access("root/project", "workflow:test", workflow=True, session_id="author"),
        remote.client,
    )
    assert read(workflow)["data"]["item_ids"] == ["item1", "item2"]
    from test_miro_addon import config

    store.save_connection(config(allow_workflows=False), record["revision"])
    with pytest.raises(MiroError, match="Workflow access"):
        read(workflow)


def test_legacy_approvals_are_invalidated_once_without_losing_history(tmp_path):
    from test_miro_addon import config

    path = tmp_path / "addons/miro-addon/state.db"
    path.parent.mkdir(parents=True)
    record = {
        "key": "legacy-key",
        "config": config().model_dump(),
        "revision": 5,
        "generation": "oauth-generation",
    }
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE records(kind TEXT,key TEXT,value TEXT,PRIMARY KEY(kind,key))")
        for kind, key, value in (
            ("connection", "legacy-key", record),
            ("snapshot", "saved", {"original": True}),
            (
                "credentials",
                "legacy-key",
                {"encrypted": "opaque", "generation": "oauth-generation"},
            ),
        ):
            db.execute("INSERT INTO records VALUES (?,?,?)", (kind, key, json.dumps(value)))
    store = MiroStore(tmp_path)
    migrated = store.get("connection", "legacy-key")
    assert migrated["config"]["boards"] == [] and migrated["revision"] == 6
    assert migrated["generation"] == "oauth-generation"
    assert store.get("snapshot", "saved") == {"original": True}
    assert store.get("credentials", "legacy-key")["encrypted"] == "opaque"
    assert store.list("board-grant") == []
    assert len(store.list("invalidated-board-approvals")[0]["boards"]) == 2
    assert MiroStore(tmp_path).get("connection", "legacy-key")["revision"] == 6


@pytest.mark.parametrize(
    "reference,expected",
    [
        ("board=", "board="),
        ("https://miro.com/app/board/board%3D/?moveToWidget=123", "board="),
        ("https://miro.com/app/board/board=/", "board="),
    ],
)
def test_board_reference_preserves_exact_identifier(reference, expected):
    assert board_reference(reference) == expected


@pytest.mark.parametrize(
    "reference",
    [
        "https://miro.com.attacker.test/app/board/a/",
        "http://miro.com/app/board/a/",
        "https://user@miro.com/app/board/a/",
        "https://miro.com/app/board/a/extra",
        "https://miro.com/app/board/a%2Fb/",
    ],
)
def test_board_reference_rejects_other_origins_or_paths(reference):
    with pytest.raises(MiroError):
        board_reference(reference)


def inbox_message(
    store, text, *, message_id="1", author="owner", created=None, chat="private-chat"
):
    message = TeamsInboxMessage(
        cursor=f"chat:{chat}/{message_id}",
        message_id=message_id,
        chat_id=chat,
        author_id=author,
        author_name="Owner",
        content=text,
        created_at=created or datetime.now(UTC),
        metadata={"chat_type": "oneOnOne"},
    )
    TeamsInboxStore(store.runtime_dir / "teams-inbox-store.json").add_message_if_new(message)
    return message.cursor


def chat_context(store, remote):
    session = peer(store)
    service = MiroService(
        store,
        Access("root/project", "workflow:approval", workflow=True, session_id=session.session_id),
        remote.client,
    )
    context = SimpleNamespace(
        metadata={"directory_path": "root/project", "graph_id": "personal", "node_id": "approve"}
    )
    return session, service, context


def test_explicit_chat_approval_uses_original_author_and_exact_board(setup):
    store, record, remote, _ = setup
    session, service, context = chat_context(store, remote)
    cursor = inbox_message(store, "You may edit https://miro.com/app/board/private-board=/")
    args = {
        "connection_id": "architecture",
        "session_id": session.session_id,
        "board_id": "private-board=",
        "message_cursor": cursor,
        "approver_user_id": "owner",
        "access": "edit",
        "operation_key": "source-1",
    }
    result = run(grant_access(context, service, args))
    assert result["status"] == "applied"
    assert store.board_grant(record, session, "private-board=")["access"] == "edit"
    assert remote.calls == []
    grant = store.board_grant(record, session, "private-board=")
    store.revoke_board_grant(
        grant["grant_id"], expected_revision=grant["revision"], actor="operator:test"
    )
    assert (
        run(grant_access(context, service, args | {"operation_key": "different-delivery"}))[
            "status"
        ]
        == "revoked"
    )
    assert store.board_grant(record, session, "private-board=") is None


@pytest.mark.parametrize(
    "change", [{"approver_user_id": "someone-else"}, {"board_id": "another-board"}]
)
def test_chat_approval_cannot_change_author_or_board(setup, change):
    store, record, remote, _ = setup
    session, service, context = chat_context(store, remote)
    cursor = inbox_message(store, "You may edit https://miro.com/app/board/private-board=/")
    args = {
        "connection_id": "architecture",
        "session_id": session.session_id,
        "board_id": "private-board=",
        "message_cursor": cursor,
        "approver_user_id": "owner",
        "access": "edit",
        "operation_key": "source-1",
        **change,
    }
    with pytest.raises(MiroError):
        run(grant_access(context, service, args))
    assert store.session_grants(record, session) == []


def test_pending_confirmation_is_scoped_consumed_and_replayable(setup):
    store, record, remote, _ = setup
    session, service, context = chat_context(store, remote)
    first = inbox_message(store, "Please edit https://miro.com/app/board/private-board=/")
    args = {
        "connection_id": "architecture",
        "session_id": session.session_id,
        "message_cursor": first,
        "approver_user_id": "owner",
    }
    info = inspect_access(context, service, args)
    assert info["access"] == "none"
    state = WorkflowStateStore(store.runtime_dir)
    state.set(
        "root/project",
        "personal",
        NAMESPACE,
        info["pending_key"],
        value=info["pending_edit"] | {"ready": True},
        expected_version=0,
    )
    yes = inbox_message(store, "Yes, you may edit it", message_id="2")
    approval = args | {
        "message_cursor": yes,
        "board_id": "private-board=",
        "access": "edit",
        "operation_key": "source-2",
    }
    result = run(grant_access(context, service, approval))
    assert result["resume_request"] == "Please edit https://miro.com/app/board/private-board=/"
    assert inspect_access(context, service, args | {"message_cursor": yes})["pending"] is None
    assert run(grant_access(context, service, approval)) == result
    assert store.board_grant(record, session, "private-board=")["source"]["message_id"] == "2"


def test_pending_confirmation_cannot_cross_chat_or_use_quoted_link(setup):
    store, record, remote, _ = setup
    session, service, context = chat_context(store, remote)
    first = inbox_message(store, "Please edit https://miro.com/app/board/private-board=/")
    args = {
        "connection_id": "architecture",
        "session_id": session.session_id,
        "message_cursor": first,
        "approver_user_id": "owner",
    }
    info = inspect_access(context, service, args)
    WorkflowStateStore(store.runtime_dir).set(
        "root/project",
        "personal",
        NAMESPACE,
        info["pending_key"],
        value=info["pending_edit"] | {"ready": True},
        expected_version=0,
    )
    for number, text, chat in (
        ("2", "Yes", "another-chat"),
        (
            "3",
            "<blockquote>You may edit https://miro.com/app/board/private-board=/</blockquote>",
            "another-chat",
        ),
        (
            "4",
            "<blockquote>You may edit https://miro.com/app/board/private-board=/</blockquote>",
            "private-chat",
        ),
    ):
        cursor = inbox_message(store, text, message_id=number, chat=chat)
        with pytest.raises(MiroError):
            run(
                grant_access(
                    context,
                    service,
                    args
                    | {
                        "message_cursor": cursor,
                        "board_id": "private-board=",
                        "access": "edit",
                        "operation_key": number,
                    },
                )
            )
    assert store.session_grants(record, session) == []


def test_admin_grants_are_revision_checked_and_reapprovable(setup):
    store, record, remote, _ = setup
    other = peer(store)
    auth = AdminAuth()
    context = AddonContext(
        addon_id="miro-addon",
        runtime_dir=store.runtime_dir,
        addon_runtime_dir=store.root,
        auth=auth,
        service=SimpleNamespace(),
    )
    app = Starlette(routes=[Mount("/api/addons/miro-addon", routes=admin_api_routes(context))])

    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://evander.example"
        ) as client:
            path = "/api/addons/miro-addon/connections/architecture/session-grants?directory_path=root/project"
            payload = {
                "session_id": other.session_id,
                "board_id": "private-board=",
                "access": "edit",
                "expected_revision": 0,
            }
            auth.unlocked = False
            assert (await client.post(path, json=payload)).status_code == 403
            auth.unlocked = True
            grant = (await client.post(path, json=payload)).json()
            assert grant["access"] == "edit" and grant["revision"] == 1
            assert (await client.post(path, json=payload)).status_code == 409
            revoke = f"/api/addons/miro-addon/connections/architecture/session-grants/{grant['grant_id']}/revoke?directory_path=root/project"
            assert (await client.post(revoke, json={"expected_revision": 1})).status_code == 200
            rows = (await client.get(path)).json()["grants"]
            assert next(g for g in rows if g["grant_id"] == grant["grant_id"])["active"] is False
            response = await client.post(path, json=payload | {"expected_revision": 2})
            assert response.status_code == 200 and response.json()["revision"] == 3

    run(scenario())


def test_grant_retry_finishes_consuming_pending_after_receipt_saved(setup, monkeypatch):
    from evander_miro_addon import access_workflow

    store, record, remote, _ = setup
    session, service, context = chat_context(store, remote)
    first = inbox_message(store, "Please edit https://miro.com/app/board/private-board=/")
    args = {
        "connection_id": "architecture",
        "session_id": session.session_id,
        "message_cursor": first,
        "approver_user_id": "owner",
    }
    info = inspect_access(context, service, args)
    state = WorkflowStateStore(store.runtime_dir)
    state.set(
        "root/project",
        "personal",
        NAMESPACE,
        info["pending_key"],
        value=info["pending_edit"] | {"ready": True},
        expected_version=0,
    )
    yes = inbox_message(store, "Yes", message_id="2")
    approval = args | {
        "message_cursor": yes,
        "board_id": "private-board=",
        "access": "edit",
        "operation_key": "confirmation",
    }
    clear = access_workflow.clear_pending

    def interrupted(*args):
        raise RuntimeError("Interrupted after durable receipt")

    monkeypatch.setattr(access_workflow, "clear_pending", interrupted)
    with pytest.raises(RuntimeError, match="Interrupted"):
        run(grant_access(context, service, approval))
    assert store.board_grant(record, session, "private-board=")["revision"] == 1
    monkeypatch.setattr(access_workflow, "clear_pending", clear)
    assert run(grant_access(context, service, approval))["status"] == "applied"
    assert store.board_grant(record, session, "private-board=")["revision"] == 1
    assert inspect_access(context, service, args | {"message_cursor": yes})["pending"] is None


@pytest.mark.parametrize("expired", [False, True])
def test_pending_confirmation_requires_a_delivered_unexpired_question(setup, expired):
    store, record, remote, _ = setup
    session, service, context = chat_context(store, remote)
    first = inbox_message(store, "Please edit https://miro.com/app/board/private-board=/")
    args = {
        "connection_id": "architecture",
        "session_id": session.session_id,
        "message_cursor": first,
        "approver_user_id": "owner",
    }
    info = inspect_access(context, service, args)
    # A message sent before the workflow delivered/activated its question cannot
    # be interpreted as an answer to that later question.
    if not expired:
        yes = inbox_message(store, "Yes", message_id="2")
    pending = info["pending_edit"] | {"ready": True}
    if expired:
        pending["expires_at"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    WorkflowStateStore(store.runtime_dir).set(
        "root/project",
        "personal",
        NAMESPACE,
        info["pending_key"],
        value=pending,
        expected_version=0,
    )
    if expired:
        yes = inbox_message(store, "Yes", message_id="2")
    with pytest.raises(MiroError):
        run(
            grant_access(
                context,
                service,
                args
                | {
                    "message_cursor": yes,
                    "board_id": "private-board=",
                    "access": "edit",
                    "operation_key": "confirmation",
                },
            )
        )
    assert store.session_grants(record, session) == []


def test_expired_original_approval_cannot_grant_access(setup):
    store, record, remote, _ = setup
    session, service, context = chat_context(store, remote)
    cursor = inbox_message(
        store,
        "You may edit https://miro.com/app/board/private-board=/",
        created=datetime.now(UTC) - timedelta(hours=2),
    )
    with pytest.raises(MiroError):
        run(
            grant_access(
                context,
                service,
                {
                    "connection_id": "architecture",
                    "session_id": session.session_id,
                    "message_cursor": cursor,
                    "approver_user_id": "owner",
                    "board_id": "private-board=",
                    "access": "edit",
                    "operation_key": "old-approval",
                },
            )
        )
    assert store.session_grants(record, session) == []


def test_inherited_admin_grants_cannot_cross_selected_directory_or_shadow(setup):
    from test_miro_addon import config

    store, record, remote, _ = setup
    child = peer(store, "child", "root/project/child")
    sibling = peer(store, "sibling", "root/sibling")

    class ScopedAuth(AdminAuth):
        def require_permission(self, request, permission, directory_path=None):
            self.require(request)
            if not directory_path or not directory_path.startswith("root/"):
                raise PermissionError()

    context = AddonContext(
        addon_id="miro-addon",
        runtime_dir=store.runtime_dir,
        addon_runtime_dir=store.root,
        auth=ScopedAuth(),
        service=SimpleNamespace(),
    )
    app = Starlette(routes=[Mount("/api/addons/miro-addon", routes=admin_api_routes(context))])

    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://evander.example"
        ) as client:
            path = "/api/addons/miro-addon/connections/architecture/session-grants?directory_path=root/project/child"
            rows = (await client.get(path)).json()
            assert {s["session_id"] for s in rows["sessions"]} == {child.session_id}
            payload = {
                "session_id": sibling.session_id,
                "board_id": "private-board=",
                "access": "edit",
                "expected_revision": 0,
            }
            assert (await client.post(path, json=payload)).status_code == 403
            grant = (
                await client.post(path, json=payload | {"session_id": child.session_id})
            ).json()
            assert grant["session_id"] == child.session_id
            revoke = f"/api/addons/miro-addon/connections/architecture/session-grants/{grant['grant_id']}/revoke?directory_path=root/sibling"
            assert (await client.post(revoke, json={"expected_revision": 1})).status_code == 403
            store.save_connection(config(directory_path=child.directory_path), 0)
            parent_path = "/api/addons/miro-addon/connections/architecture/session-grants?directory_path=root/project"
            assert (
                await client.post(parent_path, json=payload | {"session_id": child.session_id})
            ).status_code == 403

    run(scenario())
    assert store.board_grant(record, sibling, "private-board=") is None


@pytest.mark.parametrize("graph_id", ["personal", "another-workflow"])
def test_old_receipt_cannot_consume_a_later_pending_question(setup, graph_id):
    store, record, remote, _ = setup
    session, service, context = chat_context(store, remote)
    args = {
        "connection_id": "architecture",
        "session_id": session.session_id,
        "approver_user_id": "owner",
    }
    state = WorkflowStateStore(store.runtime_dir)
    first = inbox_message(store, "Please edit https://miro.com/app/board/first-board=/")
    info = inspect_access(context, service, args | {"message_cursor": first})
    state.set(
        "root/project",
        "personal",
        NAMESPACE,
        info["pending_key"],
        value=info["pending_edit"] | {"ready": True},
        expected_version=info["pending_version"],
    )
    yes = inbox_message(store, "Yes", message_id="2")
    approval = args | {
        "message_cursor": yes,
        "board_id": "first-board=",
        "access": "edit",
        "operation_key": "confirmation",
    }
    run(grant_access(context, service, approval))
    second = inbox_message(
        store, "Please edit https://miro.com/app/board/second-board=/", message_id="3"
    )
    context = SimpleNamespace(metadata=context.metadata | {"graph_id": graph_id})
    second_info = inspect_access(context, service, args | {"message_cursor": second})
    state.set(
        "root/project",
        graph_id,
        NAMESPACE,
        second_info["pending_key"],
        value=second_info["pending_edit"] | {"ready": True},
        expected_version=second_info["pending_version"],
    )
    later_yes = inbox_message(store, "Yes", message_id="4")
    if graph_id == "personal":
        assert run(grant_access(context, service, approval))["status"] == "applied"
    else:
        with pytest.raises(MiroError, match="different arguments"):
            run(grant_access(context, service, approval))
    pending = inspect_access(context, service, args | {"message_cursor": later_yes})["pending"]
    assert pending["board_id"] == "second-board="
    assert (
        run(
            grant_access(
                context,
                service,
                approval
                | {
                    "message_cursor": later_yes,
                    "board_id": "second-board=",
                    "operation_key": "later-confirmation",
                },
            )
        )["status"]
        == "applied"
    )
    assert store.board_grant(record, session, "second-board=")["access"] == "edit"


def test_renewal_and_store_restart_retain_grants_but_session_deletion_blocks_them(setup):
    store, record, remote, service = setup
    session = service.session()
    repository = JsonSessionManifestRepository(store.runtime_dir / "sessions.json")
    repository.upsert_session(
        session.model_copy(update={"expires_at": datetime.now(UTC) + timedelta(days=3)})
    )
    resumed = MiroService(MiroStore(store.runtime_dir), service.access, remote.client)
    assert read(resumed)["data"]["item_ids"] == ["item1", "item2"]
    assert repository.delete_session(session.session_id)
    remote.calls.clear()
    with pytest.raises(MiroError, match="missing or expired"):
        read(resumed)
    assert remote.calls == []


def test_revocation_after_initial_authorization_blocks_the_upstream_call(setup):
    from contextlib import asynccontextmanager

    store, record, remote, service = setup

    class RevokeBeforeCall:
        def __init__(self, store, record):
            self.store, self.record = store, record

        @asynccontextmanager
        async def open(self):
            grant = self.store.board_grant(self.record, service.session(), "board1")
            self.store.revoke_board_grant(
                grant["grant_id"], expected_revision=grant["revision"], actor="operator:test"
            )
            async with remote.client(self.store, self.record).open() as client:
                yield client

    guarded = MiroService(store, service.access, RevokeBeforeCall)
    with pytest.raises(MiroError) as error:
        read(guarded)
    assert error.value.code == "board_not_approved"
    assert remote.calls == []


def test_workflow_cannot_bypass_a_directory_tool_denial(setup):
    from evander.addons import AddonLoaderConfig
    from evander.config import GatewayConfig
    from evander.directories import DirectoryService
    from evander.directory_config import DirectoryConfigStore
    from evander.policy import PolicyError
    from evander.runtime import build_runtime
    from evander_miro_addon.runtime import provider_service

    store, record, remote, _ = setup
    runtime = build_runtime(
        GatewayConfig(
            runtime_dir=store.runtime_dir,
            addon_loader=AddonLoaderConfig(modules=["evander_miro_addon"], package_dirs=[]),
        )
    )
    DirectoryService(runtime.directories).create(parent="root", name="project")
    DirectoryConfigStore(runtime.directories, runtime_dir=store.runtime_dir).write(
        "root",
        domain="policy",
        payload={"mcp_tool_policies": {"miro_list_boards": {"denied": True}}},
    )
    service = provider_service(
        SimpleNamespace(
            provider_config={"runtime_dir": str(store.runtime_dir)},
            metadata={"directory_path": "root/project"},
        ),
        session_id="author",
    )
    with pytest.raises(PolicyError):
        run(service.execute("list_boards", {"connection_id": "architecture"}))
    assert remote.calls == []
