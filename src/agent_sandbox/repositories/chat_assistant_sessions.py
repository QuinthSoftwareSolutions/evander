from __future__ import annotations

import json
from pathlib import Path
from threading import RLock


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

    def get_mapping(self, *, user_id: str, backend: str, assistant_scope: str) -> dict[str, str] | None:
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
            }

    def upsert_mapping(
        self,
        *,
        user_id: str,
        backend: str,
        assistant_scope: str,
        session_id: str,
        agent_thread_id: str,
    ) -> dict[str, str]:
        key = self._compose_key(user_id=user_id, backend=backend, assistant_scope=assistant_scope)
        normalized = {
            "user_id": user_id,
            "backend": backend,
            "assistant_scope": assistant_scope,
            "session_id": str(session_id or "").strip(),
            "agent_thread_id": str(agent_thread_id or "").strip(),
        }
        with self._lock:
            payload = self._read_raw()
            payload[key] = normalized
            self._atomic_write(payload)
        return {"session_id": normalized["session_id"], "agent_thread_id": normalized["agent_thread_id"]}

    def _read_raw(self) -> dict[str, object]:
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return {}
        return payload

    def _atomic_write(self, payload: dict[str, object]) -> None:
        tmp_path = self.path.with_suffix(f"{self.path.suffix}.tmp")
        tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        tmp_path.replace(self.path)

    @staticmethod
    def _compose_key(*, user_id: str, backend: str, assistant_scope: str) -> str:
        return "::".join(
            [
                str(user_id or "").strip(),
                str(backend or "").strip(),
                str(assistant_scope or "").strip(),
            ]
        )

