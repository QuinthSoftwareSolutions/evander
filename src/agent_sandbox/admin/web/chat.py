from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Any
from uuid import uuid4

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from agent_sandbox.admin.service import AdminService
from agent_sandbox.admin.web.shared import AdminWebAuth, json_error
from agent_sandbox.messaging.agent_prompt_enqueue import enqueue_agent_prompt


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


@dataclass
class ChatStore:
    path: Path
    lock: Lock = Lock()

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"users": {}, "threads": {}}
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return {"users": {}, "threads": {}}
        payload.setdefault("users", {})
        payload.setdefault("threads", {})
        return payload

    def _write(self, payload: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    def get_or_create_thread(self, user_id: str) -> str:
        with self.lock:
            payload = self._read()
            users = payload["users"]
            threads = payload["threads"]
            thread_id = str(users.get(user_id, "")).strip()
            if thread_id and thread_id in threads:
                return thread_id
            thread_id = f"thread-{uuid4().hex[:16]}"
            users[user_id] = thread_id
            threads[thread_id] = {"messages": [], "created_at": _utc_now(), "updated_at": _utc_now()}
            self._write(payload)
            return thread_id

    def append_message(self, thread_id: str, role: str, content: str) -> dict[str, Any]:
        with self.lock:
            payload = self._read()
            thread = payload["threads"].setdefault(thread_id, {"messages": [], "created_at": _utc_now(), "updated_at": _utc_now()})
            message = {"id": f"msg-{uuid4().hex[:16]}", "role": role, "content": content, "created_at": _utc_now()}
            thread["messages"].append(message)
            thread["updated_at"] = _utc_now()
            self._write(payload)
            return message

    def get_transcript(self, thread_id: str) -> dict[str, Any]:
        with self.lock:
            payload = self._read()
            thread = payload["threads"].get(thread_id)
            if not isinstance(thread, dict):
                raise ValueError("Unknown chat thread")
            messages = thread.get("messages") if isinstance(thread.get("messages"), list) else []
            return {"thread_id": thread_id, "messages": messages}


def chat_routes(service: AdminService, auth: AdminWebAuth) -> list[Route]:
    store = ChatStore(service.config.runtime_dir / "admin-web-chat-store.json")

    def user_id_for_request(request: Request) -> str:
        payload = auth.authentication_payload(request)
        return str(payload.get("username") or payload.get("display_name") or payload.get("session_id") or "anonymous")

    def capability_mode(request: Request) -> str:
        if auth.privileged_for_request(request) and service.privileged_operations_unlocked():
            return "full-access"
        return "read-only"

    async def start_or_get_session(request: Request) -> JSONResponse:
        auth.require(request)
        user_id = user_id_for_request(request)
        thread_id = store.get_or_create_thread(user_id)
        return JSONResponse({"user_id": user_id, "thread_id": thread_id, "assistant_capability_mode": capability_mode(request)})

    async def post_message(request: Request) -> JSONResponse:
        auth.require(request)
        try:
            payload = await request.json()
        except Exception:
            return json_error("Request body must be valid JSON")
        if not isinstance(payload, dict):
            return json_error("Request body must be a JSON object")
        thread_id = str(payload.get("thread_id") or "").strip()
        content = str(payload.get("content") or "").strip()
        if not thread_id:
            return json_error("thread_id is required")
        if not content:
            return json_error("content is required")
        user_id = user_id_for_request(request)
        user_message = store.append_message(thread_id, "user", content)

        assistant_session = service.vm_agent_admin.resolve_chat_assistant_session(
            user_id=user_id,
            backend="container_worker_vm",
            assistant_scope="admin_chat",
        )
        session_id = str(assistant_session.get("session_id") or "").strip()
        assistant_session_id = str(assistant_session.get("agent_thread_id") or "").strip() or session_id
        if session_id:
            enqueue_agent_prompt(
                runtime_dir=service.config.runtime_dir,
                queue_key=session_id,
                target=session_id,
                vm_name=session_id,
                session_id=session_id,
                prompt=content,
                model=None,
                vm_user="agent",
                resume_thread_id=None,
                effect_id="admin-chat",
                metadata={
                    "source": "admin_chat",
                    "user_id": user_id,
                    "thread_id": thread_id,
                    "assistant_session_id": assistant_session_id,
                },
            )

        assistant_text = "Message queued for assistant processing."
        assistant_message = store.append_message(thread_id, "assistant", assistant_text)
        return JSONResponse({"thread_id": thread_id, "assistant_capability_mode": capability_mode(request), "messages": [user_message, assistant_message]})

    async def get_transcript(request: Request) -> JSONResponse:
        auth.require(request)
        thread_id = str(request.path_params.get("thread_id") or "").strip()
        if not thread_id:
            return json_error("thread_id is required")
        try:
            transcript = store.get_transcript(thread_id)
        except ValueError as exc:
            return json_error(str(exc), status_code=404)
        transcript["assistant_capability_mode"] = capability_mode(request)
        return JSONResponse(transcript)

    return [
        Route("/api/chat/session", start_or_get_session, methods=["GET", "POST"]),
        Route("/api/chat/message", post_message, methods=["POST"]),
        Route("/api/chat/transcript/{thread_id}", get_transcript, methods=["GET"]),
    ]
