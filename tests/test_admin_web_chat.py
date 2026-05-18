from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.testclient import TestClient

from agent_sandbox.admin.web.chat import ChatStore, chat_routes


class FakeAuth:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    def require(self, _request: Request) -> dict[str, Any]:
        return self.payload

    def authentication_payload(self, _request: Request) -> dict[str, Any]:
        return self.payload

    def privileged_for_request(self, _request: Request) -> bool:
        return False


class PrivilegedFakeAuth(FakeAuth):
    def privileged_for_request(self, _request: Request) -> bool:
        return True


class FakeVmAgentAdmin:
    def __init__(self) -> None:
        self.resolve_calls: list[dict[str, Any]] = []
        self.reset_calls: list[dict[str, Any]] = []

    def resolve_chat_assistant_session(self, **kwargs: Any) -> dict[str, str]:
        self.resolve_calls.append(dict(kwargs))
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
        self.published_chat_events: list[dict[str, Any]] = []

    def read_assistant_settings(self) -> dict[str, Any]:
        return {
            "enabled": True,
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


def _client_for_user(service: FakeAdminService, payload: dict[str, Any]) -> TestClient:
    return TestClient(Starlette(routes=chat_routes(service, FakeAuth(payload))))


def _expected_chat_session_resolve_call(*, user_id: str, thread_id: str) -> dict[str, Any]:
    return {
        "user_id": user_id,
        "backend": "libvirt_vm",
        "assistant_scope": "admin_chat",
        "settings": {
            "enabled": True,
            "read_only": True,
            "default_model": "gpt-test",
            "default_profile": "read-only",
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


def test_chat_routes_normalize_user_ids_and_enforce_thread_ownership(tmp_path: Path) -> None:
    service = FakeAdminService(tmp_path)
    alice = _client_for_user(service, {"username": "  ", "display_name": " Alice "})
    bob = _client_for_user(service, {"username": "bob"})

    alice_session_response = alice.post("/api/chat/session")
    bob_session_response = bob.post("/api/chat/session")
    alice_session = alice_session_response.json()
    bob_session = bob_session_response.json()

    assert alice_session["user_id"] == "Alice"
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
            "assistant_capability_mode": "read-only",
        }
    ]
    assert response.json()["assistant_capability_mode"] == "read-only"

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


def test_chat_session_start_prewarms_assistant_session_with_effective_settings(
    tmp_path: Path,
) -> None:
    service = FakeAdminService(tmp_path)
    alice = _client_for_user(service, {"username": "alice"})

    response = alice.post("/api/chat/session")
    assert response.status_code == 200

    session = response.json()
    assert session["user_id"] == "alice"
    assert session["assistant_capability_mode"] == "read-only"
    assert service.vm_agent_admin.resolve_calls == [
        _expected_chat_session_resolve_call(
            user_id="alice",
            thread_id=session["thread_id"],
        )
    ]


def test_chat_session_start_preserves_full_access_settings_for_privileged_request(
    tmp_path: Path,
) -> None:
    service = FakeAdminService(tmp_path)
    alice = TestClient(Starlette(routes=chat_routes(service, PrivilegedFakeAuth({"username": "alice"}))))

    response = alice.post("/api/chat/session")
    assert response.status_code == 200

    session = response.json()
    assert session["user_id"] == "alice"
    assert session["assistant_capability_mode"] == "full-access"
    assert service.vm_agent_admin.resolve_calls == [
        {
            "user_id": "alice",
            "backend": "libvirt_vm",
            "assistant_scope": "admin_chat",
            "settings": {
                "enabled": True,
                "read_only": False,
                "default_model": "gpt-test",
                "default_profile": "full-admin",
            },
            "thread_id": session["thread_id"],
            "user_message_id": None,
        }
    ]


def test_chat_new_session_clears_widget_thread_and_resets_assistant_context(
    tmp_path: Path,
) -> None:
    service = FakeAdminService(tmp_path)
    alice = _client_for_user(service, {"username": "alice"})

    first_session = alice.post("/api/chat/session").json()
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
    assert [message["content"] for message in old_transcript["messages"]] == [
        "keep this separate"
    ]
    assert new_transcript["messages"] == []
