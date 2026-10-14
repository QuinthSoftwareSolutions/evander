from __future__ import annotations

import json
from pathlib import Path
from threading import Event
from time import monotonic, sleep
from types import SimpleNamespace
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.testclient import TestClient

import evander.admin.web.chat as chat_module
from evander.admin.web.chat import ChatStore, chat_routes


class FakeAuth:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    def require(self, _request: Request) -> dict[str, Any]:
        return self.payload

    def require_permission(
        self, _request: Request, _permission: object, directory_path: object | None = None
    ) -> None:
        del directory_path

    def authentication_payload(self, _request: Request) -> dict[str, Any]:
        return self.payload

    def privileged_for_request(self, _request: Request) -> bool:
        return False


class HeaderFakeAuth(FakeAuth):
    def require(self, request: Request) -> dict[str, Any]:
        return json.loads(request.headers["x-test-auth"])

    def authentication_payload(self, request: Request) -> dict[str, Any]:
        return self.require(request)

    def privileged_for_request(self, request: Request) -> bool:
        return request.headers.get("x-test-privileged") == "true"


class FakeVmAgentAdmin:
    def __init__(self) -> None:
        self.resolve_calls: list[dict[str, Any]] = []
        self.reset_calls: list[dict[str, Any]] = []
        self.resolve_started = Event()
        self.resolve_continue = Event()
        self.resolve_continue.set()
        self.resolve_error: Exception | None = None

    def resolve_chat_assistant_session(self, **kwargs: Any) -> dict[str, str]:
        self.resolve_started.set()
        self.resolve_calls.append(dict(kwargs))
        if not self.resolve_continue.wait(timeout=5):
            raise AssertionError("Timed out waiting to resolve chat assistant session")
        if self.resolve_error is not None:
            raise self.resolve_error
        return {
            "session_id": "assistant-session",
            "agent_thread_id": "assistant-thread",
        }

    def reset_chat_assistant_thread_context(self, **kwargs: Any) -> dict[str, str]:
        self.reset_calls.append(dict(kwargs))
        return {
            "session_id": "assistant-session",
            "agent_thread_id": "",
            "thread_id": str(kwargs.get("thread_id") or ""),
        }


class FakeAdminService:
    def __init__(self, runtime_dir: Path) -> None:
        self.config = SimpleNamespace(runtime_dir=runtime_dir, execution_backend="libvirt_vm")
        self.vm_agent_admin = FakeVmAgentAdmin()
        self.operations_admin = self
        self.published_chat_events: list[dict[str, Any]] = []
        self.assistant_enabled = True
        self.chat_app: Starlette | None = None

    def read_assistant_settings(self) -> dict[str, Any]:
        return {
            "enabled": self.assistant_enabled,
            "read_only": False,
            "default_model": "gpt-test",
            "default_profile": "full-admin",
        }

    def privileged_operations_unlocked(self) -> bool:
        return False

    def privileged_helper_required(self) -> bool:
        return False

    def publish_chat_assistant_message(self, **kwargs: Any) -> dict[str, Any]:
        self.published_chat_events.append(kwargs)
        return {"event_id": f"chat:{kwargs['user_message_id']}"}


def _client_for_user(
    service: FakeAdminService,
    payload: dict[str, Any],
    *,
    privileged: bool = False,
) -> TestClient:
    # Match the server: all users share one app and one chat store.
    if service.chat_app is None:
        service.chat_app = Starlette(routes=chat_routes(service, HeaderFakeAuth({})))
    return TestClient(
        service.chat_app,
        headers={
            "x-test-auth": json.dumps(payload),
            "x-test-privileged": "true" if privileged else "false",
        },
    )


def _wait_for(predicate, *, timeout: float = 2.0) -> None:
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        if predicate():
            return
        sleep(0.01)
    assert predicate()


def _assistant_session(client: TestClient, thread_id: str) -> dict[str, Any]:
    response = client.get(f"/api/chat/transcript/{thread_id}")
    assert response.status_code == 200
    return response.json()["assistant_session"]


def _expected_chat_session_resolve_call(*, user_id: str, thread_id: str) -> dict[str, Any]:
    return {
        "user_id": user_id,
        "backend": "libvirt_vm",
        "assistant_scope": "admin_chat",
        "settings": {
            "enabled": True,
            "read_only": False,
            "default_model": "gpt-test",
            "default_profile": "full-admin",
        },
        "thread_id": thread_id,
        "user_message_id": None,
    }


def test_chat_store_uses_per_instance_locks_and_atomic_json_write(tmp_path: Path) -> None:
    path = tmp_path / "admin-web-chat-store.json"
    first = ChatStore(path)
    second = ChatStore(path)

    assert first.lock is not second.lock

    thread_id = first.get_or_create_thread("alice")
    first.append_message(user_id="alice", thread_id=thread_id, role="user", content="hello")

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["threads"][thread_id]["user_id"] == "alice"
    assert list(tmp_path.glob(".*.tmp")) == []


def test_chat_transcript_and_stream_read_off_the_event_loop(tmp_path: Path, monkeypatch) -> None:
    import asyncio
    import threading

    service = FakeAdminService(tmp_path)
    store = ChatStore(tmp_path / "admin-web-chat-store.json")
    thread_id = store.get_or_create_thread("alice")
    loop_thread = threading.get_ident()
    reads = []
    original = ChatStore.get_transcript

    def checked_read(self, **kwargs):
        reads.append(threading.get_ident())
        assert reads[-1] != loop_thread
        return original(self, **kwargs)

    monkeypatch.setattr(ChatStore, "get_transcript", checked_read)
    routes = chat_routes(service, FakeAuth({"username": "alice"}))

    async def run():
        for path in ("/api/chat/transcript/{thread_id}", "/api/chat/stream/{thread_id}"):
            endpoint = next(route.endpoint for route in routes if route.path == path)
            request = Request(
                {
                    "type": "http",
                    "method": "GET",
                    "path": path,
                    "query_string": b"",
                    "headers": [],
                    "path_params": {"thread_id": thread_id},
                }
            )
            response = await endpoint(request)
            assert response.status_code == 200
            if path.startswith("/api/chat/stream"):
                event = await anext(response.body_iterator)
                assert "event: snapshot" in event
                await response.body_iterator.aclose()

    asyncio.run(run())
    assert len(reads) == 2


def test_chat_routes_normalize_user_ids_and_enforce_thread_ownership(tmp_path: Path) -> None:
    service = FakeAdminService(tmp_path)

    class RequestAuth(FakeAuth):
        def authentication_payload(self, request: Request) -> dict[str, Any]:
            if request.headers["x-test-user"] == "alice":
                return {"username": "  ", "display_name": " Alice "}
            return {"username": "bob"}

        def require(self, request: Request) -> dict[str, Any]:
            return self.authentication_payload(request)

        def privileged_for_request(self, request: Request) -> bool:
            return request.headers["x-test-user"] == "alice"

    # Both users access the same application and ChatStore, as in production.
    app = Starlette(routes=chat_routes(service, RequestAuth({})))
    alice = TestClient(app, headers={"x-test-user": "alice"})
    bob = TestClient(app, headers={"x-test-user": "bob"})

    alice_session_response = alice.post("/api/chat/session")
    bob_session_response = bob.post("/api/chat/session")
    alice_session = alice_session_response.json()
    bob_session = bob_session_response.json()

    assert alice_session["user_id"] == "Alice"
    assert alice_session["assistant_session"]["status"] == "preparing"
    assert alice_session["thread_id"] != bob_session["thread_id"]

    response = alice.post(
        "/api/chat/message",
        json={"thread_id": alice_session["thread_id"], "content": "hello"},
    )
    assert response.status_code == 200
    assert service.published_chat_events == [
        {
            "user_id": "Alice",
            "thread_id": alice_session["thread_id"],
            "user_message_id": response.json()["messages"][0]["id"],
            "content": "hello",
            "assistant_capability_mode": "full-access",
        }
    ]
    assert response.json()["assistant_capability_mode"] == "full-access"

    denied_transcript = bob.get(f"/api/chat/transcript/{alice_session['thread_id']}")
    assert denied_transcript.status_code == 404

    denied_post = bob.post(
        "/api/chat/message",
        json={"thread_id": alice_session["thread_id"], "content": "wrong thread"},
    )
    assert denied_post.status_code == 404

    alice_transcript = alice.get(f"/api/chat/transcript/{alice_session['thread_id']}").json()
    assert [message["role"] for message in alice_transcript["messages"]] == ["user"]
    assert alice_transcript["messages"][0]["status"] == "queued"


def test_chat_transcript_is_bounded_and_supports_before_cursor(tmp_path: Path) -> None:
    service = FakeAdminService(tmp_path)
    alice = _client_for_user(service, {"username": "alice"}, privileged=True)
    store_path = tmp_path / "admin-web-chat-store.json"
    store = ChatStore(store_path)
    thread_id = store.get_or_create_thread("alice")
    payload = json.loads(store_path.read_text(encoding="utf-8"))
    messages = [
        {
            "id": f"msg-{index:03d}",
            "role": "user" if index % 2 == 0 else "assistant",
            "content": f"message {index:03d}",
            "created_at": "2026-06-26T00:00:00Z",
        }
        for index in range(125)
    ]
    payload["threads"][thread_id]["messages"] = messages
    payload["threads"][thread_id]["updated_at"] = "2026-06-26T00:00:00Z"
    store_path.write_text(json.dumps(payload), encoding="utf-8")

    latest_response = alice.get(f"/api/chat/transcript/{thread_id}")

    assert latest_response.status_code == 200
    latest = latest_response.json()
    assert len(latest["messages"]) == 80
    assert latest["messages"][0]["id"] == "msg-045"
    assert latest["messages"][-1]["id"] == "msg-124"
    assert latest["has_older"] is True
    assert latest["oldest_message_id"] == "msg-045"
    assert latest["newest_message_id"] == "msg-124"

    older_response = alice.get(f"/api/chat/transcript/{thread_id}?limit=20&before=msg-045")

    assert older_response.status_code == 200
    older = older_response.json()
    assert [message["id"] for message in older["messages"]] == [
        f"msg-{index:03d}" for index in range(25, 45)
    ]
    assert older["has_older"] is True

    oldest_response = alice.get(f"/api/chat/transcript/{thread_id}?limit=50&before=msg-025")

    assert oldest_response.status_code == 200
    oldest = oldest_response.json()
    assert [message["id"] for message in oldest["messages"]] == [
        f"msg-{index:03d}" for index in range(25)
    ]
    assert oldest["has_older"] is False


def test_chat_session_start_prewarms_assistant_session_with_effective_settings(
    tmp_path: Path,
) -> None:
    service = FakeAdminService(tmp_path)
    alice = _client_for_user(service, {"username": "alice"}, privileged=True)

    response = alice.post("/api/chat/session")
    assert response.status_code == 200

    session = response.json()
    assert session["user_id"] == "alice"
    assert session["assistant_capability_mode"] == "full-access"
    assert session["assistant_session"]["status"] == "preparing"
    _wait_for(lambda: _assistant_session(alice, session["thread_id"])["status"] == "ready")
    assert service.vm_agent_admin.resolve_calls == [
        _expected_chat_session_resolve_call(
            user_id="alice",
            thread_id=session["thread_id"],
        )
    ]


def test_chat_session_start_schedules_prewarm_without_synchronous_resolution(
    tmp_path: Path,
    monkeypatch,
) -> None:
    service = FakeAdminService(tmp_path)
    alice = _client_for_user(service, {"username": "alice"}, privileged=True)
    scheduled: list[dict[str, Any]] = []

    def fake_schedule_chat_assistant_prewarm(**kwargs: Any) -> bool:
        scheduled.append(kwargs)
        return True

    monkeypatch.setattr(
        chat_module,
        "_schedule_chat_assistant_prewarm",
        fake_schedule_chat_assistant_prewarm,
    )

    response = alice.post("/api/chat/session")

    assert response.status_code == 200
    session = response.json()
    assert session["assistant_session"]["status"] == "preparing"
    assert service.vm_agent_admin.resolve_calls == []
    assert len(scheduled) == 1
    assert scheduled[0]["user_id"] == "alice"
    assert scheduled[0]["thread_id"] == session["thread_id"]


def test_chat_session_start_preserves_full_access_settings_for_authorized_request(
    tmp_path: Path,
) -> None:
    service = FakeAdminService(tmp_path)
    alice = _client_for_user(service, {"username": "alice"}, privileged=True)

    response = alice.post("/api/chat/session")
    assert response.status_code == 200

    session = response.json()
    assert session["user_id"] == "alice"
    assert session["assistant_capability_mode"] == "full-access"
    _wait_for(lambda: _assistant_session(alice, session["thread_id"])["status"] == "ready")
    assert service.vm_agent_admin.resolve_calls == [
        _expected_chat_session_resolve_call(user_id="alice", thread_id=session["thread_id"])
    ]


def test_chat_new_session_clears_widget_thread_and_resets_assistant_context(
    tmp_path: Path,
) -> None:
    service = FakeAdminService(tmp_path)
    alice = _client_for_user(service, {"username": "alice"}, privileged=True)

    first_session = alice.post("/api/chat/session").json()
    _wait_for(lambda: len(service.vm_agent_admin.resolve_calls) == 1)
    first_post = alice.post(
        "/api/chat/message",
        json={"thread_id": first_session["thread_id"], "content": "keep this separate"},
    )
    assert first_post.status_code == 200

    new_session_response = alice.post("/api/chat/session/new")
    assert new_session_response.status_code == 200
    new_session = new_session_response.json()

    assert new_session["thread_id"] != first_session["thread_id"]
    assert service.vm_agent_admin.reset_calls == [
        {
            "user_id": "alice",
            "backend": "libvirt_vm",
            "assistant_scope": "admin_chat",
            "thread_id": new_session["thread_id"],
        }
    ]
    _wait_for(lambda: len(service.vm_agent_admin.resolve_calls) == 2)
    assert service.vm_agent_admin.resolve_calls == [
        _expected_chat_session_resolve_call(
            user_id="alice",
            thread_id=first_session["thread_id"],
        ),
        _expected_chat_session_resolve_call(
            user_id="alice",
            thread_id=new_session["thread_id"],
        ),
    ]

    old_transcript = alice.get(f"/api/chat/transcript/{first_session['thread_id']}").json()
    new_transcript = alice.get(f"/api/chat/transcript/{new_session['thread_id']}").json()
    assert [message["content"] for message in old_transcript["messages"]] == ["keep this separate"]
    assert new_transcript["messages"] == []


def test_chat_session_prewarm_failure_is_exposed_in_transcript(tmp_path: Path) -> None:
    service = FakeAdminService(tmp_path)
    service.vm_agent_admin.resolve_error = RuntimeError("assistant-shared unavailable\nmore")
    alice = _client_for_user(service, {"username": "alice"})

    response = alice.post("/api/chat/session")

    assert response.status_code == 200
    session = response.json()
    assert session["assistant_session"]["status"] == "preparing"
    _wait_for(lambda: _assistant_session(alice, session["thread_id"])["status"] == "failed")
    failed_session = _assistant_session(alice, session["thread_id"])
    assert failed_session["error"] == "assistant-shared unavailable"


def test_chat_session_dedupes_active_prewarm_for_same_thread(tmp_path: Path) -> None:
    service = FakeAdminService(tmp_path)
    service.vm_agent_admin.resolve_continue.clear()
    alice = _client_for_user(service, {"username": "alice"})

    first_response = alice.post("/api/chat/session")
    assert first_response.status_code == 200
    assert service.vm_agent_admin.resolve_started.wait(timeout=1)

    second_response = alice.post("/api/chat/session")

    assert second_response.status_code == 200
    assert second_response.json()["thread_id"] == first_response.json()["thread_id"]
    sleep(0.05)
    assert len(service.vm_agent_admin.resolve_calls) == 1

    service.vm_agent_admin.resolve_continue.set()
    _wait_for(
        lambda: _assistant_session(alice, first_response.json()["thread_id"])["status"] == "ready"
    )
    assert len(service.vm_agent_admin.resolve_calls) == 1


def test_chat_session_prewarm_endpoint_enforces_ownership_and_retries_failed_thread(
    tmp_path: Path,
) -> None:
    service = FakeAdminService(tmp_path)
    service.vm_agent_admin.resolve_error = RuntimeError("vm offline")
    alice = _client_for_user(service, {"username": "alice"})
    bob = _client_for_user(service, {"username": "bob"})

    session = alice.post("/api/chat/session").json()
    _wait_for(lambda: _assistant_session(alice, session["thread_id"])["status"] == "failed")

    denied = bob.post(
        "/api/chat/session/prewarm",
        json={"thread_id": session["thread_id"]},
    )
    assert denied.status_code == 404

    service.vm_agent_admin.resolve_error = None
    retry_response = alice.post(
        "/api/chat/session/prewarm",
        json={"thread_id": session["thread_id"]},
    )

    assert retry_response.status_code == 200
    assert retry_response.json()["assistant_session"]["status"] == "preparing"
    _wait_for(lambda: _assistant_session(alice, session["thread_id"])["status"] == "ready")
    assert len(service.vm_agent_admin.resolve_calls) == 2


def test_chat_session_disabled_status_does_not_schedule_prewarm(tmp_path: Path) -> None:
    service = FakeAdminService(tmp_path)
    service.assistant_enabled = False
    alice = _client_for_user(service, {"username": "alice"})

    response = alice.post("/api/chat/session")

    assert response.status_code == 200
    session = response.json()
    assert session["assistant_session"]["status"] == "disabled"
    assert service.vm_agent_admin.resolve_calls == []


def test_chat_session_reenabled_disabled_thread_schedules_prewarm(tmp_path: Path) -> None:
    service = FakeAdminService(tmp_path)
    service.assistant_enabled = False
    alice = _client_for_user(service, {"username": "alice"}, privileged=True)

    disabled_session = alice.post("/api/chat/session").json()

    assert disabled_session["assistant_session"]["status"] == "disabled"
    assert service.vm_agent_admin.resolve_calls == []

    service.assistant_enabled = True
    response = alice.post("/api/chat/session")

    assert response.status_code == 200
    session = response.json()
    assert session["thread_id"] == disabled_session["thread_id"]
    assert session["assistant_session"]["status"] == "preparing"
    _wait_for(lambda: _assistant_session(alice, session["thread_id"])["status"] == "ready")
    assert service.vm_agent_admin.resolve_calls == [
        _expected_chat_session_resolve_call(
            user_id="alice",
            thread_id=session["thread_id"],
        )
    ]
