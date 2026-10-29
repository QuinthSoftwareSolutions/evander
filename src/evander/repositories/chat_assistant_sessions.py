from __future__ import annotations

import json
from pathlib import Path
from threading import RLock

from evander.storage import write_json_atomically


class JsonChatAssistantSessionRepository:
    """Durable mapping for assistant session resolution.

    Keys are composed of user_id + backend + assistant_scope.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.write_text("{}", encoding="utf-8")

    def get_mapping(
        self, *, user_id: str, backend: str, assistant_scope: str
    ) -> dict[str, str] | None:
        key = self._compose_key(user_id=user_id, backend=backend, assistant_scope=assistant_scope)
        with self._lock:
            payload = self._read_raw()
            value = payload.get(key)
            if not isinstance(value, dict):
                return None
            session_id = str(value.get("session_id") or "").strip()
            agent_thread_id = str(value.get("agent_thread_id") or "").strip()
            if not session_id:
                return None
            return {
                "session_id": session_id,
                "agent_thread_id": agent_thread_id,
                "thread_id": str(value.get("thread_id") or "").strip(),
            }

    def upsert_mapping(
        self,
        *,
        user_id: str,
        backend: str,
        assistant_scope: str,
        session_id: str,
        agent_thread_id: str,
        thread_id: str | None = None,
    ) -> dict[str, str]:
        key = self._compose_key(user_id=user_id, backend=backend, assistant_scope=assistant_scope)
        normalized = {
            "user_id": user_id,
            "backend": backend,
            "assistant_scope": assistant_scope,
            "session_id": str(session_id or "").strip(),
            "agent_thread_id": str(agent_thread_id or "").strip(),
            "thread_id": str(thread_id or "").strip(),
        }
        with self._lock:
            payload = self._read_raw()
            payload[key] = normalized
            self._atomic_write(payload)
        return {
            "session_id": normalized["session_id"],
            "agent_thread_id": normalized["agent_thread_id"],
            "thread_id": normalized["thread_id"],
        }

    def update_agent_thread_id_for_session(
        self,
        *,
        session_id: str,
        agent_thread_id: str,
        backend: str | None = None,
        assistant_scope: str | None = None,
    ) -> int:
        normalized_session_id = str(session_id or "").strip()
        normalized_thread_id = str(agent_thread_id or "").strip()
        if not normalized_session_id or not normalized_thread_id:
            return 0
        normalized_backend = str(backend or "").strip()
        normalized_scope = str(assistant_scope or "").strip()
        updated = 0
        with self._lock:
            payload = self._read_raw()
            for value in payload.values():
                if not isinstance(value, dict):
                    continue
                if str(value.get("session_id") or "").strip() != normalized_session_id:
                    continue
                if (
                    normalized_backend
                    and str(value.get("backend") or "").strip() != normalized_backend
                ):
                    continue
                if (
                    normalized_scope
                    and str(value.get("assistant_scope") or "").strip() != normalized_scope
                ):
                    continue
                if str(value.get("agent_thread_id") or "").strip() == normalized_thread_id:
                    continue
                value["agent_thread_id"] = normalized_thread_id
                updated += 1
            if updated:
                self._atomic_write(payload)
        return updated

    def delete_mappings_for_sessions(self, session_ids: set[str]) -> int:
        normalized_session_ids = {str(session_id or "").strip() for session_id in session_ids}
        normalized_session_ids.discard("")
        if not normalized_session_ids:
            return 0
        with self._lock:
            payload = self._read_raw()
            keys_to_delete = [
                key
                for key, value in payload.items()
                if isinstance(value, dict)
                and str(value.get("session_id") or "").strip() in normalized_session_ids
            ]
            for key in keys_to_delete:
                payload.pop(key, None)
            if keys_to_delete:
                self._atomic_write(payload)
        return len(keys_to_delete)

    def _read_raw(self) -> dict[str, object]:
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return {}
        return payload

    def _atomic_write(self, payload: dict[str, object]) -> None:
        write_json_atomically(self.path, payload)

    @staticmethod
    def _compose_key(*, user_id: str, backend: str, assistant_scope: str) -> str:
        return "::".join(
            [
                str(user_id or "").strip(),
                str(backend or "").strip(),
                str(assistant_scope or "").strip(),
            ]
        )
