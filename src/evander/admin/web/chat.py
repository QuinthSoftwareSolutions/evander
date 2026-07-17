from __future__ import annotations

import json
import os
import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock, Thread, current_thread
from typing import Any
from uuid import uuid4

from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

from evander.admin.composition import AdminRuntimeComponents
from evander.admin.web.rbac import ADMIN_OWNER_PERMISSION
from evander.admin.web.shared import (
    AdminWebAuth,
    json_error,
    request_has_privileged_operations,
)


def admin_chat_store_path(runtime_dir: Path) -> Path:
    return runtime_dir / "admin-web-chat-store.json"


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


_ASSISTANT_SESSION_STATUSES = {
    "disabled",
    "not_started",
    "preparing",
    "ready",
    "failed",
}
_PREWARM_LOCK = Lock()
_PREWARM_TASKS: dict[tuple[str, str, str, str, str], Thread] = {}
DEFAULT_TRANSCRIPT_LIMIT = 80
MAX_TRANSCRIPT_LIMIT = 200


def _default_assistant_session() -> dict[str, str | None]:
    return {"status": "not_started", "error": None, "updated_at": None}


def _normalize_assistant_session(value: Any) -> dict[str, str | None]:
    if not isinstance(value, dict):
        return _default_assistant_session()
    status = str(value.get("status") or "").strip()
    if status not in _ASSISTANT_SESSION_STATUSES:
        status = "not_started"
    error_value = value.get("error")
    error = str(error_value).strip() if error_value is not None else None
    updated_at_value = value.get("updated_at")
    updated_at = str(updated_at_value).strip() if updated_at_value is not None else None
    return {
        "status": status,
        "error": error or None,
        "updated_at": updated_at or None,
    }


def _concise_error(exc: BaseException) -> str:
    message = str(exc).strip() or exc.__class__.__name__
    first_line = message.splitlines()[0].strip() or exc.__class__.__name__
    if len(first_line) > 300:
        return f"{first_line[:297]}..."
    return first_line


def _transcript_limit(value: Any) -> int:
    try:
        limit = int(str(value).strip()) if value is not None else DEFAULT_TRANSCRIPT_LIMIT
    except (TypeError, ValueError):
        limit = DEFAULT_TRANSCRIPT_LIMIT
    return max(1, min(MAX_TRANSCRIPT_LIMIT, limit))


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
            "assistant_session": _default_assistant_session(),
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
                    changed = False
                    if not str(thread.get("user_id") or "").strip():
                        thread["user_id"] = normalized_user_id
                        changed = True
                    if "assistant_session" not in thread:
                        thread["assistant_session"] = _default_assistant_session()
                        changed = True
                    if changed:
                        self._write(payload)
                    return thread_id
            thread_id = f"thread-{uuid4().hex[:16]}"
            users[normalized_user_id] = thread_id
            threads[thread_id] = self._new_thread(normalized_user_id)
            self._write(payload)
            return thread_id

    def start_new_thread(self, user_id: str) -> str:
        normalized_user_id = str(user_id or "").strip() or "anonymous"
        with self.lock:
            payload = self._read()
            thread_id = f"thread-{uuid4().hex[:16]}"
            payload["users"][normalized_user_id] = thread_id
            payload["threads"][thread_id] = self._new_thread(normalized_user_id)
            self._write(payload)
            return thread_id

    def append_message(
        self,
        *,
        user_id: str,
        thread_id: str,
        role: str,
        content: str,
        status: str | None = None,
        message_id: str | None = None,
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
                "id": str(message_id or "").strip() or f"msg-{uuid4().hex[:16]}",
                "role": role,
                "content": content,
                "created_at": _utc_now(),
            }
            if status:
                message["status"] = status
            thread["messages"].append(message)
            thread["updated_at"] = _utc_now()
            self._write(payload)
            return message

    def mark_message_status(
        self,
        *,
        thread_id: str,
        message_id: str,
        status: str,
    ) -> dict[str, Any] | None:
        with self.lock:
            payload = self._read()
            thread = payload["threads"].get(thread_id)
            if not isinstance(thread, dict):
                return None
            messages = thread.get("messages") if isinstance(thread.get("messages"), list) else []
            for message in messages:
                if not isinstance(message, dict):
                    continue
                if str(message.get("id") or "").strip() != message_id:
                    continue
                message["status"] = status
                message["updated_at"] = _utc_now()
                thread["updated_at"] = _utc_now()
                self._write(payload)
                return dict(message)
        return None

    def mark_latest_user_message_status(
        self,
        *,
        thread_id: str,
        status: str,
    ) -> dict[str, Any] | None:
        with self.lock:
            payload = self._read()
            thread = payload["threads"].get(thread_id)
            if not isinstance(thread, dict):
                return None
            messages = thread.get("messages") if isinstance(thread.get("messages"), list) else []
            for message in reversed(messages):
                if not isinstance(message, dict) or message.get("role") != "user":
                    continue
                message["status"] = status
                message["updated_at"] = _utc_now()
                thread["updated_at"] = _utc_now()
                self._write(payload)
                return dict(message)
        return None

    def get_transcript(
        self,
        *,
        user_id: str,
        thread_id: str,
        limit: int = DEFAULT_TRANSCRIPT_LIMIT,
        before: str | None = None,
    ) -> dict[str, Any]:
        normalized_user_id = str(user_id or "").strip() or "anonymous"
        with self.lock:
            payload = self._read()
            thread = self._thread_for_user(
                payload,
                user_id=normalized_user_id,
                thread_id=thread_id,
            )
            messages = thread.get("messages") if isinstance(thread.get("messages"), list) else []
            before_id = str(before or "").strip()
            end_index = len(messages)
            if before_id:
                end_index = 0
                for index, message in enumerate(messages):
                    if (
                        isinstance(message, dict)
                        and str(message.get("id") or "").strip() == before_id
                    ):
                        end_index = index
                        break
            effective_limit = _transcript_limit(limit)
            start_index = max(0, end_index - effective_limit)
            page = messages[start_index:end_index]
            oldest_message_id = None
            newest_message_id = None
            if page:
                first = page[0]
                last = page[-1]
                if isinstance(first, dict):
                    oldest_message_id = str(first.get("id") or "").strip() or None
                if isinstance(last, dict):
                    newest_message_id = str(last.get("id") or "").strip() or None
            return {
                "thread_id": thread_id,
                "messages": page,
                "has_older": start_index > 0,
                "oldest_message_id": oldest_message_id,
                "newest_message_id": newest_message_id,
                "assistant_session": _normalize_assistant_session(thread.get("assistant_session")),
                "updated_at": thread.get("updated_at"),
            }

    def get_assistant_session(self, *, user_id: str, thread_id: str) -> dict[str, str | None]:
        normalized_user_id = str(user_id or "").strip() or "anonymous"
        with self.lock:
            payload = self._read()
            thread = self._thread_for_user(
                payload,
                user_id=normalized_user_id,
                thread_id=thread_id,
            )
            return _normalize_assistant_session(thread.get("assistant_session"))

    def set_assistant_session(
        self,
        *,
        thread_id: str,
        status: str,
        error: str | None = None,
    ) -> dict[str, str | None]:
        normalized_status = str(status or "").strip()
        if normalized_status not in _ASSISTANT_SESSION_STATUSES:
            raise ValueError("Unknown assistant session status")
        with self.lock:
            payload = self._read()
            thread = payload["threads"].get(thread_id)
            if not isinstance(thread, dict) or self._thread_owner(payload, thread_id) is None:
                raise ValueError("Unknown chat thread")
            assistant_session: dict[str, str | None] = {
                "status": normalized_status,
                "error": str(error).strip() if error else None,
                "updated_at": _utc_now(),
            }
            thread["assistant_session"] = assistant_session
            thread["updated_at"] = assistant_session["updated_at"]
            self._write(payload)
            return dict(assistant_session)

    def append_assistant_reply(
        self,
        *,
        thread_id: str,
        content: str,
        user_message_id: str | None = None,
    ) -> dict[str, Any]:
        with self.lock:
            payload = self._read()
            owner = self._thread_owner(payload, thread_id)
            if owner is None:
                raise ValueError("Unknown chat thread")
            thread = payload["threads"].get(thread_id)
            if not isinstance(thread, dict):
                raise ValueError("Unknown chat thread")
            if not isinstance(thread.get("messages"), list):
                thread["messages"] = []
            message = {
                "id": f"msg-{uuid4().hex[:16]}",
                "role": "assistant",
                "content": content,
                "created_at": _utc_now(),
                "status": "answered",
            }
            thread["messages"].append(message)
            target_user_message_id = str(user_message_id or "").strip()
            if target_user_message_id:
                for existing in thread["messages"]:
                    if not isinstance(existing, dict):
                        continue
                    if str(existing.get("id") or "").strip() == target_user_message_id:
                        existing["status"] = "answered"
                        existing["updated_at"] = _utc_now()
                        break
            else:
                for existing in reversed(thread["messages"][:-1]):
                    if isinstance(existing, dict) and existing.get("role") == "user":
                        existing["status"] = "answered"
                        existing["updated_at"] = _utc_now()
                        break
            thread["updated_at"] = _utc_now()
            self._write(payload)
            return message


def _prewarm_key(
    *,
    user_id: str,
    backend: str,
    assistant_scope: str,
    thread_id: str,
    capability_mode: str,
) -> tuple[str, str, str, str, str]:
    return (user_id, backend, assistant_scope, thread_id, capability_mode)


def _run_chat_assistant_prewarm(
    *,
    key: tuple[str, str, str, str, str],
    service: AdminRuntimeComponents,
    store: ChatStore,
    user_id: str,
    backend: str,
    assistant_scope: str,
    settings: dict[str, Any],
    thread_id: str,
) -> None:
    try:
        service.vm_agent_admin.resolve_chat_assistant_session(
            user_id=user_id,
            backend=backend,
            assistant_scope=assistant_scope,
            settings=settings,
            thread_id=thread_id,
            user_message_id=None,
        )
    except Exception as exc:
        store.set_assistant_session(
            thread_id=thread_id,
            status="failed",
            error=_concise_error(exc),
        )
    else:
        store.set_assistant_session(thread_id=thread_id, status="ready")
    finally:
        current = current_thread()
        with _PREWARM_LOCK:
            active = _PREWARM_TASKS.get(key)
            if active is current:
                _PREWARM_TASKS.pop(key, None)


def _schedule_chat_assistant_prewarm(
    *,
    service: AdminRuntimeComponents,
    store: ChatStore,
    user_id: str,
    backend: str,
    assistant_scope: str,
    capability_mode: str,
    settings: dict[str, Any],
    thread_id: str,
) -> bool:
    key = _prewarm_key(
        user_id=user_id,
        backend=backend,
        assistant_scope=assistant_scope,
        thread_id=thread_id,
        capability_mode=capability_mode,
    )
    with _PREWARM_LOCK:
        active = _PREWARM_TASKS.get(key)
        if active is not None and active.is_alive():
            return False
        worker = Thread(
            target=_run_chat_assistant_prewarm,
            kwargs={
                "key": key,
                "service": service,
                "store": store,
                "user_id": user_id,
                "backend": backend,
                "assistant_scope": assistant_scope,
                "settings": dict(settings),
                "thread_id": thread_id,
            },
            name=f"admin-chat-prewarm-{thread_id}",
            daemon=True,
        )
        _PREWARM_TASKS[key] = worker
        worker.start()
        return True


def chat_routes(service: AdminRuntimeComponents, auth: AdminWebAuth) -> list[Route]:
    store = ChatStore(admin_chat_store_path(service.config.runtime_dir))

    def user_id_for_request(request: Request) -> str:
        payload = auth.authentication_payload(request)
        for key in ("username", "display_name", "session_id"):
            user_id = str(payload.get(key) or "").strip()
            if user_id:
                return user_id
        return "anonymous"

    def capability_mode(request: Request) -> str:
        settings = service.operations_admin.read_assistant_settings()
        if bool(settings.get("read_only")):
            return "read-only"
        if request_has_privileged_operations(
            auth=auth,
            service=service,
            request=request,
            required_permission=ADMIN_OWNER_PERMISSION,
        ):
            return "full-access"
        return "read-only"

    def effective_assistant_settings(request: Request) -> dict[str, Any]:
        settings = dict(service.operations_admin.read_assistant_settings())
        if capability_mode(request) != "full-access":
            settings["read_only"] = True
            settings["default_profile"] = "read-only"
        return settings

    def session_payload(
        *,
        user_id: str,
        thread_id: str,
        assistant_capability_mode: str,
        assistant_session: dict[str, str | None],
    ) -> dict[str, Any]:
        return {
            "user_id": user_id,
            "thread_id": thread_id,
            "assistant_capability_mode": assistant_capability_mode,
            "assistant_session": assistant_session,
        }

    def prepare_assistant_session(
        *,
        request: Request,
        user_id: str,
        thread_id: str,
        force: bool = False,
    ) -> tuple[str, dict[str, str | None]]:
        settings = service.operations_admin.read_assistant_settings()
        effective_capability_mode = (
            "read-only" if bool(settings.get("read_only")) else capability_mode(request)
        )
        if not bool(settings.get("enabled")):
            return effective_capability_mode, store.set_assistant_session(
                thread_id=thread_id,
                status="disabled",
            )

        assistant_session = store.get_assistant_session(user_id=user_id, thread_id=thread_id)
        should_schedule = force or assistant_session["status"] in {
            "disabled",
            "not_started",
            "preparing",
        }
        if not should_schedule:
            return effective_capability_mode, assistant_session

        assistant_session = store.set_assistant_session(
            thread_id=thread_id,
            status="preparing",
        )
        backend = str(getattr(service.config, "execution_backend", "libvirt_vm") or "libvirt_vm")
        _schedule_chat_assistant_prewarm(
            service=service,
            store=store,
            user_id=user_id,
            backend=backend,
            assistant_scope="admin_chat",
            capability_mode=effective_capability_mode,
            settings=effective_assistant_settings(request),
            thread_id=thread_id,
        )
        return effective_capability_mode, assistant_session

    async def start_or_get_session(request: Request) -> JSONResponse:
        auth.require(request)
        user_id = user_id_for_request(request)
        thread_id = store.get_or_create_thread(user_id)
        effective_capability_mode, assistant_session = prepare_assistant_session(
            request=request,
            user_id=user_id,
            thread_id=thread_id,
        )
        return JSONResponse(
            session_payload(
                user_id=user_id,
                thread_id=thread_id,
                assistant_capability_mode=effective_capability_mode,
                assistant_session=assistant_session,
            )
        )

    async def start_new_session(request: Request) -> JSONResponse:
        auth.require(request)
        user_id = user_id_for_request(request)
        thread_id = store.start_new_thread(user_id)
        backend = str(getattr(service.config, "execution_backend", "libvirt_vm") or "libvirt_vm")
        reset_context = getattr(service.vm_agent_admin, "reset_chat_assistant_thread_context", None)
        if callable(reset_context):
            reset_context(
                user_id=user_id,
                backend=backend,
                assistant_scope="admin_chat",
                thread_id=thread_id,
            )
        effective_capability_mode, assistant_session = prepare_assistant_session(
            request=request,
            user_id=user_id,
            thread_id=thread_id,
        )
        return JSONResponse(
            session_payload(
                user_id=user_id,
                thread_id=thread_id,
                assistant_capability_mode=effective_capability_mode,
                assistant_session=assistant_session,
            )
        )

    async def prewarm_session(request: Request) -> JSONResponse:
        auth.require(request)
        try:
            payload = await request.json()
        except Exception:
            return json_error("Request body must be valid JSON")
        if not isinstance(payload, dict):
            return json_error("Request body must be a JSON object")
        thread_id = str(payload.get("thread_id") or "").strip()
        if not thread_id:
            return json_error("thread_id is required")
        user_id = user_id_for_request(request)
        try:
            store.get_assistant_session(user_id=user_id, thread_id=thread_id)
        except ValueError as exc:
            return json_error(str(exc), status_code=404)
        effective_capability_mode, assistant_session = prepare_assistant_session(
            request=request,
            user_id=user_id,
            thread_id=thread_id,
            force=True,
        )
        return JSONResponse(
            session_payload(
                user_id=user_id,
                thread_id=thread_id,
                assistant_capability_mode=effective_capability_mode,
                assistant_session=assistant_session,
            )
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
        settings = service.operations_admin.read_assistant_settings()
        if not bool(settings.get("enabled")):
            return json_error("Assistant is disabled by admin settings.", status_code=403)
        user_id = user_id_for_request(request)
        try:
            user_message = store.append_message(
                user_id=user_id,
                thread_id=thread_id,
                role="user",
                content=content,
                status="queued",
            )
        except ValueError as exc:
            return json_error(str(exc), status_code=404)
        effective_capability_mode = capability_mode(request)
        try:
            service.operations_admin.publish_chat_assistant_message(
                user_id=user_id,
                thread_id=thread_id,
                user_message_id=user_message["id"],
                content=content,
                assistant_capability_mode=effective_capability_mode,
            )
        except Exception as exc:
            store.mark_message_status(
                thread_id=thread_id,
                message_id=user_message["id"],
                status="failed",
            )
            return json_error(str(exc), status_code=500)
        return JSONResponse(
            {
                "thread_id": thread_id,
                "assistant_capability_mode": effective_capability_mode,
                "assistant_session": store.get_assistant_session(
                    user_id=user_id,
                    thread_id=thread_id,
                ),
                "messages": [user_message],
            }
        )

    async def get_transcript(request: Request) -> JSONResponse:
        auth.require(request)
        thread_id = str(request.path_params.get("thread_id") or "").strip()
        if not thread_id:
            return json_error("thread_id is required")
        user_id = user_id_for_request(request)
        limit = _transcript_limit(request.query_params.get("limit"))
        before = str(request.query_params.get("before") or "").strip() or None
        try:
            transcript = store.get_transcript(
                user_id=user_id,
                thread_id=thread_id,
                limit=limit,
                before=before,
            )
        except ValueError as exc:
            return json_error(str(exc), status_code=404)
        transcript["assistant_capability_mode"] = capability_mode(request)
        return JSONResponse(transcript)

    async def stream_transcript(request: Request) -> StreamingResponse | JSONResponse:
        auth.require(request)
        thread_id = str(request.path_params.get("thread_id") or "").strip()
        if not thread_id:
            return json_error("thread_id is required")
        user_id = user_id_for_request(request)
        limit = _transcript_limit(request.query_params.get("limit"))
        try:
            initial = store.get_transcript(user_id=user_id, thread_id=thread_id, limit=limit)
        except ValueError as exc:
            return json_error(str(exc), status_code=404)

        async def events():
            last_serialized = ""
            snapshot = dict(initial)
            snapshot["assistant_capability_mode"] = capability_mode(request)
            last_serialized = json.dumps(snapshot, sort_keys=True)
            yield f"event: snapshot\ndata: {last_serialized}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                await asyncio.sleep(1.0)
                try:
                    snapshot = store.get_transcript(
                        user_id=user_id,
                        thread_id=thread_id,
                        limit=limit,
                    )
                except ValueError:
                    break
                snapshot["assistant_capability_mode"] = capability_mode(request)
                serialized = json.dumps(snapshot, sort_keys=True)
                if serialized == last_serialized:
                    yield ": keepalive\n\n"
                    continue
                last_serialized = serialized
                yield f"event: snapshot\ndata: {serialized}\n\n"

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    async def get_settings(request: Request) -> JSONResponse:
        auth.require(request)
        return JSONResponse(service.operations_admin.read_assistant_settings())

    async def put_settings(request: Request) -> JSONResponse:
        auth.require(request)
        if not request_has_privileged_operations(
            auth=auth,
            service=service,
            request=request,
            required_permission=ADMIN_OWNER_PERMISSION,
        ):
            return json_error("Privileged operations are locked.", status_code=403)
        try:
            payload = await request.json()
        except Exception:
            return json_error("Request body must be valid JSON")
        if not isinstance(payload, dict):
            return json_error("Request body must be a JSON object")
        try:
            result = service.operations_admin.write_assistant_settings(payload)
        except Exception as exc:
            return json_error(str(exc), status_code=400)
        return JSONResponse(result)

    return [
        Route("/api/chat/session", start_or_get_session, methods=["GET", "POST"]),
        Route("/api/chat/session/new", start_new_session, methods=["POST"]),
        Route("/api/chat/session/prewarm", prewarm_session, methods=["POST"]),
        Route("/api/chat/message", post_message, methods=["POST"]),
        Route("/api/chat/transcript/{thread_id}", get_transcript, methods=["GET"]),
        Route("/api/chat/stream/{thread_id}", stream_transcript, methods=["GET"]),
        Route("/api/chat/settings", get_settings, methods=["GET"]),
        Route("/api/chat/settings", put_settings, methods=["PUT"]),
    ]
