from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
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
    lock: Lock = field(default_factory=Lock)

    @staticmethod
    def _empty_payload() -> dict[str, Any]:
        return {"users": {}, "threads": {}}

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return self._empty_payload()
        text = self.path.read_text(encoding="utf-8")
        if not text.strip():
            return self._empty_payload()
        payload = json.loads(text)
        if not isinstance(payload, dict):
            return self._empty_payload()
        if not isinstance(payload.get("users"), dict):
            payload["users"] = {}
        if not isinstance(payload.get("threads"), dict):
            payload["threads"] = {}
        return payload

    def _write(self, payload: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        serialized = json.dumps(payload, indent=2, sort_keys=True)
        temp_path = self.path.with_name(f".{self.path.name}.{uuid4().hex}.tmp")
        try:
            with temp_path.open("w", encoding="utf-8") as handle:
                handle.write(serialized)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, self.path)
        finally:
            if temp_path.exists():
                temp_path.unlink()

    @staticmethod
    def _thread_owner(payload: dict[str, Any], thread_id: str) -> str | None:
        threads = payload["threads"]
        thread = threads.get(thread_id)
        if not isinstance(thread, dict):
            return None
        owner = str(thread.get("user_id") or "").strip()
        if owner:
            return owner
        for user_id, mapped_thread_id in payload["users"].items():
            if str(mapped_thread_id or "").strip() == thread_id:
                normalized_user_id = str(user_id or "").strip()
                return normalized_user_id or None
        return None

    def _thread_for_user(
        self,
        payload: dict[str, Any],
        *,
        user_id: str,
        thread_id: str,
    ) -> dict[str, Any]:
        thread = payload["threads"].get(thread_id)
        if not isinstance(thread, dict):
            raise ValueError("Unknown chat thread")
        if self._thread_owner(payload, thread_id) != user_id:
            raise ValueError("Unknown chat thread")
        return thread

    @staticmethod
    def _new_thread(user_id: str) -> dict[str, Any]:
        now = _utc_now()
        return {
            "user_id": user_id,
            "messages": [],
            "created_at": now,
            "updated_at": now,
        }

    def get_or_create_thread(self, user_id: str) -> str:
        normalized_user_id = str(user_id or "").strip() or "anonymous"
        with self.lock:
            payload = self._read()
            users = payload["users"]
            threads = payload["threads"]
            thread_id = str(users.get(normalized_user_id, "")).strip()
            if thread_id and thread_id in threads:
                try:
                    thread = self._thread_for_user(
                        payload,
                        user_id=normalized_user_id,
                        thread_id=thread_id,
                    )
                except ValueError:
                    pass
                else:
                    if not str(thread.get("user_id") or "").strip():
                        thread["user_id"] = normalized_user_id
                        self._write(payload)
                    return thread_id
            thread_id = f"thread-{uuid4().hex[:16]}"
            users[normalized_user_id] = thread_id
            threads[thread_id] = self._new_thread(normalized_user_id)
            self._write(payload)
            return thread_id

    def append_message(
        self,
        *,
        user_id: str,
        thread_id: str,
        role: str,
        content: str,
    ) -> dict[str, Any]:
        normalized_user_id = str(user_id or "").strip() or "anonymous"
        with self.lock:
            payload = self._read()
            thread = self._thread_for_user(
                payload,
                user_id=normalized_user_id,
                thread_id=thread_id,
            )
            if not isinstance(thread.get("messages"), list):
                thread["messages"] = []
            if not str(thread.get("user_id") or "").strip():
                thread["user_id"] = normalized_user_id
            message = {
                "id": f"msg-{uuid4().hex[:16]}",
                "role": role,
                "content": content,
                "created_at": _utc_now(),
            }
            thread["messages"].append(message)
            thread["updated_at"] = _utc_now()
            self._write(payload)
            return message

    def get_transcript(self, *, user_id: str, thread_id: str) -> dict[str, Any]:
        normalized_user_id = str(user_id or "").strip() or "anonymous"
        with self.lock:
            payload = self._read()
            thread = self._thread_for_user(
                payload,
                user_id=normalized_user_id,
                thread_id=thread_id,
            )
            messages = thread.get("messages") if isinstance(thread.get("messages"), list) else []
            return {"thread_id": thread_id, "messages": messages}


def chat_routes(service: AdminService, auth: AdminWebAuth) -> list[Route]:
    store = ChatStore(service.config.runtime_dir / "admin-web-chat-store.json")

    def user_id_for_request(request: Request) -> str:
        payload = auth.authentication_payload(request)
        for key in ("username", "display_name", "session_id"):
            user_id = str(payload.get(key) or "").strip()
            if user_id:
                return user_id
        return "anonymous"

    def capability_mode(request: Request) -> str:
        if auth.privileged_for_request(request) and service.privileged_operations_unlocked():
            return "full-access"
        return "read-only"

    async def start_or_get_session(request: Request) -> JSONResponse:
        auth.require(request)
        settings = service.read_assistant_settings()
        if not bool(settings.get("enabled")):
            return json_error("Assistant is disabled by admin settings.", status_code=403)
        user_id = user_id_for_request(request)
        thread_id = store.get_or_create_thread(user_id)
        return JSONResponse(
            {
                "user_id": user_id,
                "thread_id": thread_id,
                "assistant_capability_mode": "read-only"
                if bool(settings.get("read_only"))
                else capability_mode(request),
            }
        )

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
        settings = service.read_assistant_settings()
        if not bool(settings.get("enabled")):
            return json_error("Assistant is disabled by admin settings.", status_code=403)
        if bool(settings.get("read_only")):
            return json_error("Assistant is in read-only mode.", status_code=403)
        user_id = user_id_for_request(request)
        try:
            user_message = store.append_message(
                user_id=user_id,
                thread_id=thread_id,
                role="user",
                content=content,
            )
        except ValueError as exc:
            return json_error(str(exc), status_code=404)

        assistant_session = service.vm_agent_admin.resolve_chat_assistant_session(
            user_id=user_id,
            backend="container_worker_vm",
            assistant_scope="admin_chat",
        )
        session_id = str(assistant_session.get("session_id") or "").strip()
        assistant_session_id = (
            str(assistant_session.get("agent_thread_id") or "").strip() or session_id
        )
        if session_id:
            enqueue_agent_prompt(
                runtime_dir=service.config.runtime_dir,
                queue_key=session_id,
                target=session_id,
                vm_name=session_id,
                session_id=session_id,
                prompt=content,
                model=settings.get("default_model"),
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
        assistant_message = store.append_message(
            user_id=user_id,
            thread_id=thread_id,
            role="assistant",
            content=assistant_text,
        )
        return JSONResponse(
            {
                "thread_id": thread_id,
                "assistant_capability_mode": capability_mode(request),
                "messages": [user_message, assistant_message],
            }
        )

    async def get_transcript(request: Request) -> JSONResponse:
        auth.require(request)
        thread_id = str(request.path_params.get("thread_id") or "").strip()
        if not thread_id:
            return json_error("thread_id is required")
        user_id = user_id_for_request(request)
        try:
            transcript = store.get_transcript(user_id=user_id, thread_id=thread_id)
        except ValueError as exc:
            return json_error(str(exc), status_code=404)
        transcript["assistant_capability_mode"] = capability_mode(request)
        return JSONResponse(transcript)

    async def get_settings(request: Request) -> JSONResponse:
        auth.require(request)
        return JSONResponse(service.read_assistant_settings())

    async def put_settings(request: Request) -> JSONResponse:
        auth.require(request)
        try:
            payload = await request.json()
        except Exception:
            return json_error("Request body must be valid JSON")
        if not isinstance(payload, dict):
            return json_error("Request body must be a JSON object")
        try:
            result = service.write_assistant_settings(payload)
        except Exception as exc:
            return json_error(str(exc), status_code=400)
        return JSONResponse(result)

    return [
        Route("/api/chat/session", start_or_get_session, methods=["GET", "POST"]),
        Route("/api/chat/message", post_message, methods=["POST"]),
        Route("/api/chat/transcript/{thread_id}", get_transcript, methods=["GET"]),
        Route("/api/chat/settings", get_settings, methods=["GET"]),
        Route("/api/chat/settings", put_settings, methods=["PUT"]),
    ]
