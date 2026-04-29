from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Lock
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

AgentPromptStatus = Literal["unread", "responded", "closed"]

_AGENT_PROMPT_RETENTION = timedelta(days=90)


class AgentPromptRecord(BaseModel):
    message_id: str = Field(min_length=1)
    sender_session_id: str = Field(min_length=1)
    sender_vm_id: str | None = None
    target_session_id: str = Field(min_length=1)
    target_vm_id: str | None = None
    prompt_payload: dict[str, Any] = Field(default_factory=dict)
    issued_token_hash: str = Field(min_length=1)
    issued_token_claims: dict[str, Any] = Field(default_factory=dict)
    status: AgentPromptStatus = "unread"
    created_at: datetime
    updated_at: datetime
    token_used_at: datetime | None = None

    @field_validator(
        "message_id",
        "sender_session_id",
        "sender_vm_id",
        "target_session_id",
        "target_vm_id",
        "issued_token_hash",
        mode="before",
    )
    @classmethod
    def normalize_optional_strings(cls, value: object) -> object:
        if value is None:
            return None
        return str(value).strip()

    @field_validator("prompt_payload", "issued_token_claims", mode="before")
    @classmethod
    def normalize_mappings(cls, value: object) -> object:
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise ValueError("value must be a mapping")
        return value


class AgentPromptStore:
    """Atomic JSON-backed store for agent-to-agent prompts and one-time token usage."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._atomic_write(
                {"messages": {}, "token_index": {}, "consumed_tokens": {}, "idempotency_index": {}}
            )

    def create_prompt_message(self, message: AgentPromptRecord) -> bool:
        with self._lock:
            payload = self._read_raw()
            messages = payload.setdefault("messages", {})
            if message.message_id in messages:
                return False

            token_hash = message.issued_token_hash
            token_index = payload.setdefault("token_index", {})
            consumed_tokens = payload.setdefault("consumed_tokens", {})
            idempotency_index = payload.setdefault("idempotency_index", {})
            if token_hash in token_index or token_hash in consumed_tokens:
                raise ValueError(f"Token hash is already tracked: {token_hash}")

            idempotency_key = self._idempotency_key_for_message(message)
            if idempotency_key is not None:
                tracked_message_id = idempotency_index.get(idempotency_key)
                if (
                    isinstance(tracked_message_id, str)
                    and tracked_message_id
                    and tracked_message_id != message.message_id
                ):
                    raise ValueError(f"Idempotency key is already tracked: {idempotency_key}")

            messages[message.message_id] = message.model_dump(mode="json")
            token_index[token_hash] = message.message_id
            if idempotency_key is not None:
                idempotency_index[idempotency_key] = message.message_id
            self._compact(payload)
            self._atomic_write(payload)
            return True

    def list_unread_prompts(
        self,
        *,
        target_session_id: str,
        target_vm_id: str | None = None,
        limit: int | None = None,
    ) -> list[AgentPromptRecord]:
        with self._lock:
            payload = self._read_raw()
            unread: list[AgentPromptRecord] = []
            for raw_value in payload.get("messages", {}).values():
                message = self._validate_message(raw_value)
                if message is None:
                    continue
                if message.status != "unread":
                    continue
                if message.target_session_id != target_session_id:
                    continue
                if target_vm_id is not None and message.target_vm_id != target_vm_id:
                    continue
                unread.append(message)
            unread.sort(key=self._message_sort_key)
            if limit is not None:
                return unread[:limit]
            return unread

    def get_prompt(self, message_id: str) -> AgentPromptRecord | None:
        with self._lock:
            payload = self._read_raw()
            raw_value = payload.get("messages", {}).get(message_id)
            return self._validate_message(raw_value)

    def find_prompt_by_idempotency_key(
        self,
        *,
        sender_session_id: str,
        idempotency_key: str,
    ) -> AgentPromptRecord | None:
        normalized_sender_session_id = str(sender_session_id).strip()
        normalized_idempotency_key = str(idempotency_key).strip()
        if not normalized_sender_session_id or not normalized_idempotency_key:
            return None

        lookup_key = self._serialize_idempotency_key(
            normalized_sender_session_id, normalized_idempotency_key
        )
        with self._lock:
            payload = self._read_raw()
            idempotency_index = payload.setdefault("idempotency_index", {})
            message_id = idempotency_index.get(lookup_key)
            messages = payload.get("messages", {})
            if isinstance(message_id, str) and message_id.strip():
                return self._validate_message(messages.get(message_id))

            if not isinstance(messages, dict):
                return None
            for raw_value in messages.values():
                message = self._validate_message(raw_value)
                if message is None:
                    continue
                if self._idempotency_key_for_message(message) != lookup_key:
                    continue
                idempotency_index[lookup_key] = message.message_id
                self._compact(payload)
                self._atomic_write(payload)
                return message
            return None

    def mark_prompt_status(
        self,
        message_id: str,
        *,
        status: AgentPromptStatus,
        updated_at: datetime | None = None,
    ) -> AgentPromptRecord | None:
        with self._lock:
            payload = self._read_raw()
            messages = payload.setdefault("messages", {})
            message = self._validate_message(messages.get(message_id))
            if message is None:
                return None
            message.status = status
            message.updated_at = updated_at or datetime.now(timezone.utc)
            messages[message_id] = message.model_dump(mode="json")
            self._compact(payload)
            self._atomic_write(payload)
            return message

    def replace_issued_token(
        self,
        message_id: str,
        *,
        issued_token_hash: str,
        issued_token_claims: dict[str, Any],
        updated_at: datetime | None = None,
    ) -> AgentPromptRecord | None:
        normalized_message_id = str(message_id).strip()
        normalized_token_hash = str(issued_token_hash).strip()
        if not normalized_message_id or not normalized_token_hash:
            return None

        with self._lock:
            payload = self._read_raw()
            messages = payload.setdefault("messages", {})
            token_index = payload.setdefault("token_index", {})
            consumed_tokens = payload.setdefault("consumed_tokens", {})

            message = self._validate_message(messages.get(normalized_message_id))
            if message is None:
                return None

            previous_token_hash = message.issued_token_hash
            tracked_message_id = token_index.get(normalized_token_hash)
            if (
                isinstance(tracked_message_id, str)
                and tracked_message_id
                and tracked_message_id != normalized_message_id
            ):
                raise ValueError(f"Token hash is already tracked: {normalized_token_hash}")
            if (
                normalized_token_hash in consumed_tokens
                and normalized_token_hash != previous_token_hash
            ):
                raise ValueError(f"Token hash is already tracked: {normalized_token_hash}")

            if previous_token_hash != normalized_token_hash:
                token_index.pop(previous_token_hash, None)

            message.issued_token_hash = normalized_token_hash
            message.issued_token_claims = dict(issued_token_claims or {})
            message.updated_at = updated_at or datetime.now(timezone.utc)
            messages[normalized_message_id] = message.model_dump(mode="json")
            token_index[normalized_token_hash] = normalized_message_id

            self._compact(payload)
            self._atomic_write(payload)
            return message

    def validate_and_consume_token(
        self,
        token_hash: str,
        *,
        expected_message_id: str | None = None,
        consumed_at: datetime | None = None,
    ) -> AgentPromptRecord | None:
        normalized_token_hash = str(token_hash).strip()
        if not normalized_token_hash:
            return None

        with self._lock:
            payload = self._read_raw()
            token_index = payload.setdefault("token_index", {})
            consumed_tokens = payload.setdefault("consumed_tokens", {})

            if normalized_token_hash in consumed_tokens:
                return None

            message_id = token_index.get(normalized_token_hash)
            if not isinstance(message_id, str) or not message_id.strip():
                return None
            if expected_message_id is not None and message_id != expected_message_id:
                return None

            messages = payload.setdefault("messages", {})
            message = self._validate_message(messages.get(message_id))
            if message is None:
                return None
            if message.issued_token_hash != normalized_token_hash:
                return None
            if message.token_used_at is not None:
                consumed_tokens[normalized_token_hash] = message.token_used_at.isoformat().replace(
                    "+00:00", "Z"
                )
                self._compact(payload)
                self._atomic_write(payload)
                return None

            consumed_time = consumed_at or datetime.now(timezone.utc)
            message.token_used_at = consumed_time
            message.updated_at = consumed_time
            messages[message.message_id] = message.model_dump(mode="json")
            consumed_tokens[normalized_token_hash] = consumed_time.isoformat().replace(
                "+00:00", "Z"
            )

            self._compact(payload)
            self._atomic_write(payload)
            return message

    def record_response(
        self,
        message_id: str,
        *,
        response_text: str,
        responder_session_id: str,
        responded_at: datetime | None = None,
    ) -> AgentPromptRecord | None:
        normalized_message_id = str(message_id).strip()
        normalized_response_text = str(response_text).strip()
        normalized_responder_session_id = str(responder_session_id).strip()
        if (
            not normalized_message_id
            or not normalized_response_text
            or not normalized_responder_session_id
        ):
            return None

        with self._lock:
            payload = self._read_raw()
            messages = payload.setdefault("messages", {})
            message = self._validate_message(messages.get(normalized_message_id))
            if message is None:
                return None

            response_time = responded_at or datetime.now(timezone.utc)
            prompt_payload = dict(message.prompt_payload or {})
            prompt_payload["response"] = {
                "text": normalized_response_text,
                "responder_session_id": normalized_responder_session_id,
                "responded_at": response_time.isoformat().replace("+00:00", "Z"),
            }
            message.prompt_payload = prompt_payload
            message.status = "responded"
            message.updated_at = response_time

            messages[normalized_message_id] = message.model_dump(mode="json")
            self._compact(payload)
            self._atomic_write(payload)
            return message

    def read_raw(self) -> dict[str, Any]:
        with self._lock:
            return self._read_raw()

    def _compact(self, payload: dict[str, Any]) -> None:
        cutoff = datetime.now(timezone.utc) - _AGENT_PROMPT_RETENTION

        messages = payload.get("messages")
        token_index = payload.get("token_index")
        idempotency_index = payload.get("idempotency_index")
        if isinstance(messages, dict):
            retained_messages: dict[str, Any] = {}
            retained_tokens: dict[str, str] = {}
            retained_idempotency: dict[str, str] = {}
            for message_id, raw_value in messages.items():
                message = self._validate_message(raw_value)
                if message is None:
                    retained_messages[message_id] = raw_value
                    continue
                if message.status in {"responded", "closed"} and message.updated_at < cutoff:
                    continue
                retained_messages[message_id] = message.model_dump(mode="json")
                retained_tokens[message.issued_token_hash] = message.message_id
                idempotency_key = self._idempotency_key_for_message(message)
                if idempotency_key is not None:
                    retained_idempotency[idempotency_key] = message.message_id
            payload["messages"] = retained_messages
            if isinstance(token_index, dict):
                payload["token_index"] = retained_tokens
            if isinstance(idempotency_index, dict):
                payload["idempotency_index"] = retained_idempotency

        consumed_tokens = payload.get("consumed_tokens")
        if isinstance(consumed_tokens, dict):
            retained_consumed: dict[str, str] = {}
            for token_hash, raw_value in consumed_tokens.items():
                if self._parse_dt(raw_value) >= cutoff:
                    retained_consumed[token_hash] = str(raw_value)
            payload["consumed_tokens"] = retained_consumed

    def _read_raw(self) -> dict[str, Any]:
        return json.loads(self.path.read_text(encoding="utf-8"))

    def _atomic_write(self, payload: dict[str, Any]) -> None:
        tmp_path = self.path.with_suffix(f"{self.path.suffix}.tmp")
        tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        tmp_path.replace(self.path)

    @staticmethod
    def _validate_message(raw_value: object) -> AgentPromptRecord | None:
        if not isinstance(raw_value, dict):
            return None
        return AgentPromptRecord.model_validate(raw_value)

    @staticmethod
    def _message_sort_key(message: AgentPromptRecord) -> tuple[datetime, str]:
        return (message.created_at, message.message_id)

    @staticmethod
    def _parse_dt(value: Any) -> datetime:
        if not isinstance(value, str) or not value.strip():
            return datetime.min.replace(tzinfo=timezone.utc)
        normalized = value.strip()
        if normalized.endswith("Z"):
            normalized = normalized[:-1] + "+00:00"
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    @classmethod
    def _idempotency_key_for_message(cls, message: AgentPromptRecord) -> str | None:
        payload = dict(message.prompt_payload or {})
        idempotency_key = str(payload.get("idempotency_key") or "").strip()
        if not idempotency_key:
            return None
        return cls._serialize_idempotency_key(message.sender_session_id, idempotency_key)

    @staticmethod
    def _serialize_idempotency_key(sender_session_id: str, idempotency_key: str) -> str:
        return f"{str(sender_session_id).strip()}::{str(idempotency_key).strip()}"
