from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Lock
from typing import Any
from uuid import uuid4

import httpx
from pydantic import BaseModel, Field, field_validator

from agent_sandbox.config import GatewayConfig, RetellPhonebookContact
from agent_sandbox.email.http_utils import ProviderRequestError
from agent_sandbox.models import DirectoryPath, SessionManifest

_RETELL_CALL_RETENTION = timedelta(days=90)
_RETELL_FINAL_STATUSES = frozenset({"ended", "error", "not_connected"})
_PHONE_LIKE_RE = re.compile(r"\+?\d[\d .()\-]{6,}\d")
_WEBHOOK_SIGNATURE_RE = re.compile(r"^v=(\d+),d=([0-9a-fA-F]+)$")
_WEBHOOK_TOLERANCE_MS = 5 * 60 * 1000

_PHONE_FIELD_NAMES = frozenset(
    {
        "from_number",
        "to_number",
        "phone_number",
        "user_number",
        "agent_number",
        "transfer_destination",
    }
)
_SENSITIVE_FIELD_FRAGMENTS = (
    "authorization",
    "api_key",
    "access_token",
    "secret",
    "token",
    "password",
)
_DROP_PAYLOAD_KEYS = _PHONE_FIELD_NAMES | {
    "access_token",
    "telephony_identifier",
    "custom_sip_headers",
    "recording_url",
    "recording_multi_channel_url",
    "scrubbed_recording_url",
    "scrubbed_recording_multi_channel_url",
    "public_log_url",
    "knowledge_base_retrieved_contents_url",
}


class RetellWebhookSignatureError(ValueError):
    """Raised when a Retell webhook signature is missing, stale, or invalid."""


class RetellPhoneCallRecord(BaseModel):
    request_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    directory_path: str = "root"
    contact_alias: str = Field(min_length=1)
    contact_display_name: str = Field(min_length=1)
    call_id: str | None = None
    status: str = "pending"
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    prompt_metadata: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    retell_metadata: dict[str, Any] = Field(default_factory=dict)
    start_timestamp: int | None = None
    end_timestamp: int | None = None
    transfer_end_timestamp: int | None = None
    duration_ms: int | None = None
    disconnection_reason: str | None = None
    transcript: str | None = None
    transcript_object: list[dict[str, Any]] | None = None
    transcript_with_tool_calls: list[dict[str, Any]] | None = None
    scrubbed_transcript_with_tool_calls: list[dict[str, Any]] | None = None
    call_analysis: dict[str, Any] | None = None
    retell_payload: dict[str, Any] = Field(default_factory=dict)

    @field_validator("directory_path", mode="before")
    @classmethod
    def normalize_directory_path(cls, value: object) -> str:
        text = str(value or "").strip() or "root"
        return DirectoryPath(segments=text).as_posix()

    @field_validator(
        "request_id",
        "session_id",
        "contact_alias",
        "contact_display_name",
        "call_id",
        "status",
        "disconnection_reason",
        mode="before",
    )
    @classmethod
    def normalize_optional_strings(cls, value: object) -> object:
        if value is None:
            return None
        text = str(value).strip()
        return text or None


class RetellPhoneCallStore:
    """Atomic JSON store for Retell phone call request state."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._atomic_write({"calls": {}})

    def prepare_call(
        self,
        *,
        session_id: str,
        directory_path: str,
        contact_alias: str,
        contact_display_name: str,
        prompt_metadata: dict[str, Any],
        metadata: dict[str, Any] | None = None,
        request_id: str | None = None,
    ) -> tuple[RetellPhoneCallRecord, bool]:
        normalized_request_id = str(request_id or "").strip() or f"phone-call-{uuid4().hex}"
        now = datetime.now(UTC)
        with self._lock:
            payload = self._read_raw()
            calls = payload.setdefault("calls", {})
            existing = self._validate_call(calls.get(normalized_request_id))
            if existing is not None:
                if (
                    existing.session_id != session_id
                    or existing.directory_path != DirectoryPath(segments=directory_path).as_posix()
                    or existing.contact_alias != contact_alias
                    or existing.prompt_metadata.get("sha256") != prompt_metadata.get("sha256")
                    or existing.metadata != sanitize_retell_metadata(metadata or {})
                ):
                    raise ValueError(
                        f"Retell phone call request already exists with different parameters: "
                        f"{normalized_request_id}"
                    )
                return existing, False

            record = RetellPhoneCallRecord(
                request_id=normalized_request_id,
                session_id=session_id,
                directory_path=directory_path,
                contact_alias=contact_alias,
                contact_display_name=contact_display_name,
                prompt_metadata=prompt_metadata,
                metadata=sanitize_retell_metadata(metadata or {}),
                created_at=now,
                updated_at=now,
            )
            calls[record.request_id] = record.model_dump(mode="json")
            self._compact(payload)
            self._atomic_write(payload)
            return record, True

    def attach_registered_call(
        self,
        request_id: str,
        retell_payload: dict[str, Any],
    ) -> RetellPhoneCallRecord:
        with self._lock:
            payload = self._read_raw()
            calls = payload.setdefault("calls", {})
            record = self._validate_call(calls.get(request_id))
            if record is None:
                raise ValueError(f"Unknown Retell phone call request: {request_id}")
            call_id = str(retell_payload.get("call_id") or "").strip()
            if not call_id:
                raise ValueError("Retell create phone call response did not include call_id")
            for other_id, raw_value in calls.items():
                other = self._validate_call(raw_value)
                if other_id != request_id and other is not None and other.call_id == call_id:
                    raise ValueError(f"Retell call_id is already associated with {other_id}")
            updated = _record_with_retell_payload(record, retell_payload, event=None)
            calls[request_id] = updated.model_dump(mode="json")
            self._atomic_write(payload)
            return updated

    def mark_error(self, request_id: str, error: str) -> RetellPhoneCallRecord | None:
        with self._lock:
            payload = self._read_raw()
            calls = payload.setdefault("calls", {})
            record = self._validate_call(calls.get(request_id))
            if record is None:
                return None
            safe_error = _redact_phone_like_text(str(error or ""))
            updated = record.model_copy(
                update={
                    "status": "error",
                    "updated_at": datetime.now(UTC),
                    "retell_metadata": {
                        **record.retell_metadata,
                        "last_error": safe_error,
                    },
                }
            )
            calls[request_id] = updated.model_dump(mode="json")
            self._atomic_write(payload)
            return updated

    def update_from_retell_call(
        self,
        call_id: str,
        retell_payload: dict[str, Any],
        *,
        event: str | None = None,
    ) -> RetellPhoneCallRecord | None:
        normalized_call_id = str(call_id or "").strip()
        if not normalized_call_id:
            return None
        with self._lock:
            payload = self._read_raw()
            calls = payload.setdefault("calls", {})
            for request_id, raw_value in calls.items():
                record = self._validate_call(raw_value)
                if record is None or record.call_id != normalized_call_id:
                    continue
                updated = _record_with_retell_payload(record, retell_payload, event=event)
                calls[request_id] = updated.model_dump(mode="json")
                self._atomic_write(payload)
                return updated
            return None

    def get_by_request_id(self, request_id: str) -> RetellPhoneCallRecord | None:
        with self._lock:
            payload = self._read_raw()
            return self._validate_call(payload.get("calls", {}).get(request_id))

    def get_by_call_id(self, call_id: str) -> RetellPhoneCallRecord | None:
        normalized_call_id = str(call_id or "").strip()
        if not normalized_call_id:
            return None
        with self._lock:
            payload = self._read_raw()
            for raw_value in payload.get("calls", {}).values():
                record = self._validate_call(raw_value)
                if record is not None and record.call_id == normalized_call_id:
                    return record
            return None

    def read_raw(self) -> dict[str, Any]:
        with self._lock:
            return self._read_raw()

    def _compact(self, payload: dict[str, Any]) -> None:
        cutoff = datetime.now(UTC) - _RETELL_CALL_RETENTION
        calls = payload.get("calls")
        if isinstance(calls, dict):
            payload["calls"] = {
                request_id: raw_value
                for request_id, raw_value in calls.items()
                if self._parse_dt((raw_value or {}).get("created_at")) >= cutoff
            }

    def _read_raw(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"calls": {}}
        text = self.path.read_text(encoding="utf-8")
        if not text.strip():
            return {"calls": {}}
        raw = json.loads(text)
        if not isinstance(raw, dict):
            raise ValueError(f"Retell phone call store at {self.path} must contain an object")
        raw.setdefault("calls", {})
        return raw

    def _atomic_write(self, payload: dict[str, Any]) -> None:
        tmp_path = self.path.with_suffix(f"{self.path.suffix}.tmp")
        tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        tmp_path.replace(self.path)

    @staticmethod
    def _validate_call(raw_value: object) -> RetellPhoneCallRecord | None:
        if not isinstance(raw_value, dict):
            return None
        return RetellPhoneCallRecord.model_validate(raw_value)

    @staticmethod
    def _parse_dt(value: Any) -> datetime:
        if not isinstance(value, str) or not value.strip():
            return datetime.min.replace(tzinfo=UTC)
        normalized = value.strip()
        if normalized.endswith("Z"):
            normalized = normalized[:-1] + "+00:00"
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)


class RetellPhoneClient:
    def __init__(
        self,
        *,
        api_base_url: str,
        api_key: str,
        timeout_seconds: float,
        client: httpx.Client | None = None,
    ) -> None:
        self.api_base_url = str(api_base_url).strip().rstrip("/")
        self.api_key = api_key
        self.timeout_seconds = float(timeout_seconds)
        self._client = client or httpx.Client(timeout=self.timeout_seconds)

    def close(self) -> None:
        self._client.close()

    def create_phone_call(
        self,
        *,
        from_number: str,
        to_number: str,
        override_agent_id: str,
        metadata: dict[str, Any],
        agent_question: str,
    ) -> dict[str, Any]:
        payload = {
            "from_number": from_number,
            "to_number": to_number,
            "override_agent_id": override_agent_id,
            "metadata": metadata,
            "retell_llm_dynamic_variables": {"agent_question": agent_question},
        }
        return self._request_json(
            "POST",
            f"{self.api_base_url}/v2/create-phone-call",
            json_payload=payload,
            operation="create_phone_call",
        )

    def get_call(self, call_id: str) -> dict[str, Any]:
        return self._request_json(
            "GET",
            f"{self.api_base_url}/v2/get-call/{call_id}",
            json_payload=None,
            operation="get_call",
        )

    def _request_json(
        self,
        method: str,
        url: str,
        *,
        json_payload: dict[str, Any] | None,
        operation: str,
    ) -> dict[str, Any]:
        try:
            response = self._client.request(
                method,
                url,
                headers={"Authorization": f"Bearer {self.api_key}"},
                json=json_payload,
            )
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code if exc.response is not None else None
            raise ProviderRequestError(
                f"retell.{operation} failed with status={status_code}",
                status_code=status_code,
            ) from exc
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise ProviderRequestError(
                f"retell.{operation} failed with {type(exc).__name__}: {exc}"
            ) from exc
        if not isinstance(payload, dict):
            raise ProviderRequestError(f"retell.{operation} returned a non-object payload")
        return payload


class RetellPhoneCallService:
    def __init__(
        self,
        *,
        config: GatewayConfig,
        store: RetellPhoneCallStore,
        client: RetellPhoneClient | None = None,
    ) -> None:
        self.config = config
        self.store = store
        self._client = client

    def make_phone_call(
        self,
        *,
        session: SessionManifest,
        contact: str,
        prompt: str,
        request_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> RetellPhoneCallRecord:
        normalized_contact = str(contact or "").strip()
        if not normalized_contact:
            raise ValueError("contact is required")
        normalized_prompt = str(prompt or "").strip()
        if not normalized_prompt:
            raise ValueError("prompt is required")
        contact_entry = self._resolve_contact(normalized_contact)
        prompt_metadata = _prompt_metadata(normalized_prompt)
        record, _created = self.store.prepare_call(
            session_id=session.session_id,
            directory_path=session.directory_path,
            contact_alias=normalized_contact,
            contact_display_name=contact_entry.display_name,
            prompt_metadata=prompt_metadata,
            metadata=metadata or {},
            request_id=request_id,
        )
        if record.call_id:
            return record

        retell_metadata = {
            **record.metadata,
            "agent_sandbox_request_id": record.request_id,
            "agent_sandbox_session_id": session.session_id,
            "agent_sandbox_directory_path": session.directory_path,
            "agent_sandbox_contact": normalized_contact,
        }
        client = self._client_or_create()
        try:
            retell_payload = client.create_phone_call(
                from_number=self._require_from_number(),
                to_number=contact_entry.phone_number,
                override_agent_id=self._resolve_agent_id(contact_entry),
                metadata=retell_metadata,
                agent_question=normalized_prompt,
            )
        except Exception as exc:
            self.store.mark_error(record.request_id, str(exc))
            raise
        finally:
            if self._client is None:
                client.close()
        return self.store.attach_registered_call(record.request_id, retell_payload)

    def get_phone_call_result(
        self,
        *,
        session: SessionManifest,
        request_id: str | None = None,
        call_id: str | None = None,
    ) -> dict[str, Any]:
        record = self._find_record(request_id=request_id, call_id=call_id)
        self._require_record_owned_by_session(record, session)
        if record.call_id and not is_retell_phone_call_complete(record):
            client = self._client_or_create()
            try:
                retell_payload = client.get_call(record.call_id)
            finally:
                if self._client is None:
                    client.close()
            record = self.store.update_from_retell_call(record.call_id, retell_payload) or record
        return phone_call_result_payload(record)

    def _find_record(
        self,
        *,
        request_id: str | None = None,
        call_id: str | None = None,
    ) -> RetellPhoneCallRecord:
        normalized_request_id = str(request_id or "").strip()
        normalized_call_id = str(call_id or "").strip()
        if not normalized_request_id and not normalized_call_id:
            raise ValueError("request_id or call_id is required")
        record = (
            self.store.get_by_request_id(normalized_request_id)
            if normalized_request_id
            else None
        )
        if normalized_call_id:
            by_call_id = self.store.get_by_call_id(normalized_call_id)
            if record is not None and by_call_id is not None and by_call_id.request_id != record.request_id:
                raise ValueError("request_id and call_id refer to different phone calls")
            record = record or by_call_id
        if record is None:
            raise ValueError("Unknown phone call request")
        return record

    @staticmethod
    def _require_record_owned_by_session(
        record: RetellPhoneCallRecord,
        session: SessionManifest,
    ) -> None:
        if record.session_id != session.session_id or record.directory_path != session.directory_path:
            raise ValueError("Phone call request is outside this session scope")

    def _resolve_contact(self, alias: str) -> RetellPhonebookContact:
        contact = self.config.retell_phonebook.get(alias)
        if contact is None:
            raise ValueError(f"Unknown Retell phonebook contact alias: {alias}")
        return contact

    def _require_from_number(self) -> str:
        value = str(self.config.retell_from_number or "").strip()
        if not value:
            raise ValueError("retell_from_number is not configured")
        return value

    def _resolve_agent_id(self, contact: RetellPhonebookContact) -> str:
        value = str(contact.agent_id or self.config.retell_default_agent_id or "").strip()
        if not value:
            raise ValueError("retell_default_agent_id or contact agent_id is required")
        return value

    def _client_or_create(self) -> RetellPhoneClient:
        if self._client is not None:
            return self._client
        env_var_name = str(self.config.retell_api_key_env or "").strip()
        api_key = os.environ.get(env_var_name, "").strip()
        if not api_key:
            raise ValueError(
                f"Retell API key is missing in configured environment variable {env_var_name!r}"
            )
        return RetellPhoneClient(
            api_base_url=self.config.retell_api_base_url,
            api_key=api_key,
            timeout_seconds=self.config.retell_request_timeout_seconds,
        )


def verify_retell_webhook_signature(
    *,
    raw_body: bytes,
    signature_header: str | None,
    api_key: str,
    now_ms: int | None = None,
    tolerance_ms: int = _WEBHOOK_TOLERANCE_MS,
) -> None:
    signature = str(signature_header or "").strip()
    match = _WEBHOOK_SIGNATURE_RE.match(signature)
    if match is None:
        raise RetellWebhookSignatureError("invalid Retell webhook signature format")
    timestamp_text, digest = match.groups()
    timestamp_ms = int(timestamp_text)
    current_ms = now_ms if now_ms is not None else int(datetime.now(UTC).timestamp() * 1000)
    if abs(current_ms - timestamp_ms) > tolerance_ms:
        raise RetellWebhookSignatureError("stale Retell webhook signature timestamp")
    expected = hmac.new(
        api_key.encode("utf-8"),
        raw_body + timestamp_text.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected, digest):
        raise RetellWebhookSignatureError("invalid Retell webhook signature digest")


def retell_api_key_from_config(config: GatewayConfig) -> str:
    env_var_name = str(config.retell_api_key_env or "").strip()
    if not env_var_name:
        raise ValueError("retell_api_key_env is not configured")
    api_key = os.environ.get(env_var_name, "").strip()
    if not api_key:
        raise ValueError(
            f"Retell API key is missing in configured environment variable {env_var_name!r}"
        )
    return api_key


def sanitize_retell_metadata(value: Any) -> Any:
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for raw_key, raw_value in value.items():
            key = str(raw_key)
            if _is_sensitive_key(key) or _is_phone_key(key):
                sanitized[key] = "<redacted>"
                continue
            sanitized[key] = sanitize_retell_metadata(raw_value)
        return sanitized
    if isinstance(value, list):
        return [sanitize_retell_metadata(item) for item in value]
    if isinstance(value, str):
        return _redact_phone_like_text(value)
    return value


def sanitize_retell_payload(payload: dict[str, Any]) -> dict[str, Any]:
    sanitized: dict[str, Any] = {}
    for raw_key, raw_value in payload.items():
        key = str(raw_key)
        if key in _DROP_PAYLOAD_KEYS:
            continue
        if _is_sensitive_key(key) or _is_phone_key(key):
            sanitized[key] = "<redacted>"
            continue
        sanitized[key] = sanitize_retell_metadata(raw_value)
    return sanitized


def is_retell_phone_call_complete(record: RetellPhoneCallRecord) -> bool:
    status = str(record.status or "").strip().lower()
    return status in _RETELL_FINAL_STATUSES or record.end_timestamp is not None


def phone_call_result_payload(record: RetellPhoneCallRecord) -> dict[str, Any]:
    result: dict[str, Any] = {
        "request_id": record.request_id,
        "call_id": record.call_id,
        "contact": {
            "alias": record.contact_alias,
            "display_name": record.contact_display_name,
        },
        "status": record.status,
        "created_at": record.created_at.isoformat(),
        "updated_at": record.updated_at.isoformat(),
        "metadata": record.retell_metadata,
    }
    optional_fields = {
        "start_timestamp": record.start_timestamp,
        "end_timestamp": record.end_timestamp,
        "transfer_end_timestamp": record.transfer_end_timestamp,
        "duration_ms": record.duration_ms,
        "disconnection_reason": record.disconnection_reason,
        "transcript": record.transcript,
        "transcript_object": record.transcript_object,
        "transcript_with_tool_calls": record.transcript_with_tool_calls,
        "scrubbed_transcript_with_tool_calls": record.scrubbed_transcript_with_tool_calls,
        "call_analysis": record.call_analysis,
    }
    for key, value in optional_fields.items():
        if value is not None:
            result[key] = value
    return result


def _record_with_retell_payload(
    record: RetellPhoneCallRecord,
    payload: dict[str, Any],
    *,
    event: str | None,
) -> RetellPhoneCallRecord:
    sanitized_payload = sanitize_retell_payload(payload)
    default_status = "registered" if payload.get("call_id") else record.status
    status = str(payload.get("call_status") or default_status or "registered").strip()
    if event == "call_ended" and status not in _RETELL_FINAL_STATUSES:
        status = "ended"
    call_id = str(payload.get("call_id") or record.call_id or "").strip() or None
    retell_metadata: dict[str, Any] = dict(record.retell_metadata)
    if isinstance(payload.get("metadata"), dict):
        retell_metadata["metadata"] = sanitize_retell_metadata(payload["metadata"])
    if isinstance(payload.get("collected_dynamic_variables"), dict):
        retell_metadata["collected_dynamic_variables"] = sanitize_retell_metadata(
            payload["collected_dynamic_variables"]
        )
    if event:
        retell_metadata["last_webhook_event"] = event
    update = {
        "call_id": call_id,
        "status": status or "registered",
        "updated_at": datetime.now(UTC),
        "retell_metadata": retell_metadata,
        "retell_payload": sanitized_payload,
    }
    if "start_timestamp" in payload:
        update["start_timestamp"] = _optional_int(payload.get("start_timestamp"))
    if "end_timestamp" in payload:
        update["end_timestamp"] = _optional_int(payload.get("end_timestamp"))
    if "transfer_end_timestamp" in payload:
        update["transfer_end_timestamp"] = _optional_int(
            payload.get("transfer_end_timestamp")
        )
    if "duration_ms" in payload:
        update["duration_ms"] = _optional_int(payload.get("duration_ms"))
    if "disconnection_reason" in payload:
        update["disconnection_reason"] = _optional_str(payload.get("disconnection_reason"))
    if "transcript" in payload:
        update["transcript"] = _optional_str(sanitize_retell_metadata(payload.get("transcript")))
    if "transcript_object" in payload:
        update["transcript_object"] = _optional_list_of_dicts(
            sanitize_retell_metadata(payload.get("transcript_object"))
        )
    if "transcript_with_tool_calls" in payload:
        update["transcript_with_tool_calls"] = _optional_list_of_dicts(
            sanitize_retell_metadata(payload.get("transcript_with_tool_calls"))
        )
    if "scrubbed_transcript_with_tool_calls" in payload:
        update["scrubbed_transcript_with_tool_calls"] = _optional_list_of_dicts(
            sanitize_retell_metadata(payload.get("scrubbed_transcript_with_tool_calls"))
        )
    if isinstance(payload.get("call_analysis"), dict):
        update["call_analysis"] = sanitize_retell_metadata(payload.get("call_analysis"))
    return record.model_copy(update=update)


def _prompt_metadata(prompt: str) -> dict[str, Any]:
    return {
        "length": len(prompt),
        "sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
    }


def _is_sensitive_key(key: str) -> bool:
    lowered = key.strip().lower()
    return any(fragment in lowered for fragment in _SENSITIVE_FIELD_FRAGMENTS)


def _is_phone_key(key: str) -> bool:
    lowered = key.strip().lower()
    return lowered in _PHONE_FIELD_NAMES or "phone" in lowered


def _redact_phone_like_text(value: str) -> str:
    return _PHONE_LIKE_RE.sub("<redacted-phone>", value)


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_list_of_dicts(value: Any) -> list[dict[str, Any]] | None:
    if not isinstance(value, list):
        return None
    normalized = [item for item in value if isinstance(item, dict)]
    return normalized
