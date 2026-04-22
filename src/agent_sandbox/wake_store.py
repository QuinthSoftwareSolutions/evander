from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Lock
from typing import Any, Protocol
from uuid import uuid4

from .models import (
    WakeDispatchRecord,
    WakeMessageEnvelope,
    WakeResumePromptPayload,
    WakeScheduledRequest,
    WakeScheduleDefinition,
)

_WAKE_RETENTION = timedelta(days=90)


class WakeRequestRepository(Protocol):
    """Repository contract for scheduled wake requests and dispatch history."""

    def upsert_request(self, request: WakeScheduledRequest) -> None: ...

    def get_request(self, request_id: str) -> WakeScheduledRequest | None: ...

    def delete_request(self, request_id: str) -> bool: ...

    def record_dispatch(self, dispatch: WakeDispatchRecord) -> None: ...

    def schedule_resume_request(
        self,
        *,
        session_id: str,
        prompt: str,
        schedule: WakeScheduleDefinition,
        request_id: str | None,
        source: str | None,
        resume_mode: str,
        enabled: bool,
        metadata: dict[str, Any],
    ) -> WakeScheduledRequest: ...

    def list_requests(
        self,
        *,
        session_id: str | None = None,
        include_disabled: bool = True,
    ) -> list[WakeScheduledRequest]: ...

    def latest_dispatch_by_request_id(self) -> dict[str, WakeDispatchRecord]: ...

    def read_raw(self) -> dict[str, Any]: ...


class WakeRequestStore:
    """Atomic JSON-backed store for scheduled wake requests and wake dispatch history."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._atomic_write({"requests": {}, "dispatches": {}, "messages": {}})

    def upsert_request(self, request: WakeScheduledRequest) -> None:
        with self._lock:
            payload = self._read_raw()
            requests = payload.setdefault("requests", {})
            requests[request.request_id] = request.model_dump(mode="json")
            self._compact(payload)
            self._atomic_write(payload)

    def get_request(self, request_id: str) -> WakeScheduledRequest | None:
        with self._lock:
            raw_request = self._read_raw().get("requests", {}).get(request_id)
            if not isinstance(raw_request, dict):
                return None
            return WakeScheduledRequest.model_validate(raw_request)

    def delete_request(self, request_id: str) -> bool:
        with self._lock:
            payload = self._read_raw()
            requests = payload.get("requests")
            if not isinstance(requests, dict) or request_id not in requests:
                return False
            requests.pop(request_id, None)
            self._compact(payload)
            self._atomic_write(payload)
            return True

    def record_dispatch(self, dispatch: WakeDispatchRecord) -> None:
        with self._lock:
            payload = self._read_raw()
            dispatches = payload.setdefault("dispatches", {})
            dispatches[dispatch.dispatch_id] = dispatch.model_dump(mode="json")

            if dispatch.request_id:
                requests = payload.setdefault("requests", {})
                raw_request = requests.get(dispatch.request_id)
                if isinstance(raw_request, dict):
                    request = WakeScheduledRequest.model_validate(raw_request)
                    request.last_dispatched_at = dispatch.dispatched_at
                    request.updated_at = datetime.now(UTC)
                    if request.schedule.resume_at is not None and dispatch.status == "dispatched":
                        request.next_run_at = None
                        request.enabled = False
                    requests[dispatch.request_id] = request.model_dump(mode="json")

            messages = payload.setdefault("messages", {})
            message = WakeMessageEnvelope(
                message_id=f"wake:{dispatch.dispatch_id}",
                session_id=dispatch.session_id,
                request_id=dispatch.request_id,
                dispatch_id=dispatch.dispatch_id,
                status=dispatch.status,
                resume_prompt=dispatch.resume_prompt,
                resume_mode=dispatch.resume_mode,
                created_at=dispatch.dispatched_at,
                metadata=dispatch.metadata,
            )
            messages[message.message_id] = message.model_dump(mode="json")

            self._compact(payload)
            self._atomic_write(payload)

    def schedule_resume_request(
        self,
        *,
        session_id: str,
        prompt: str,
        schedule: WakeScheduleDefinition,
        request_id: str | None,
        source: str | None,
        resume_mode: str,
        enabled: bool,
        metadata: dict[str, Any],
    ) -> WakeScheduledRequest:
        now = datetime.now(UTC)
        with self._lock:
            payload = self._read_raw()
            requests = payload.setdefault("requests", {})
            if request_id:
                normalized_request_id = request_id
                if normalized_request_id in requests:
                    raise ValueError(f"Wake request already exists: {normalized_request_id}")
            else:
                normalized_request_id = f"wake-{uuid4().hex}"
                while normalized_request_id in requests:
                    normalized_request_id = f"wake-{uuid4().hex}"
            request = WakeScheduledRequest(
                request_id=normalized_request_id,
                session_id=session_id,
                schedule=schedule,
                resume_prompt=WakeResumePromptPayload(prompt=prompt, metadata=metadata),
                resume_mode=resume_mode,
                enabled=enabled,
                source=source,
                created_at=now,
                updated_at=now,
                next_run_at=schedule.resume_at,
            )
            requests[request.request_id] = request.model_dump(mode="json")
            self._compact(payload)
            self._atomic_write(payload)
            return request

    def read_raw(self) -> dict[str, Any]:
        with self._lock:
            return self._read_raw()

    def list_requests(
        self,
        *,
        session_id: str | None = None,
        include_disabled: bool = True,
    ) -> list[WakeScheduledRequest]:
        with self._lock:
            payload = self._read_raw()
            raw_requests = payload.get("requests", {})
            if not isinstance(raw_requests, dict):
                return []
            requests: list[WakeScheduledRequest] = []
            for raw_value in raw_requests.values():
                if not isinstance(raw_value, dict):
                    continue
                try:
                    request = WakeScheduledRequest.model_validate(raw_value)
                except Exception:
                    continue
                if session_id and request.session_id != session_id:
                    continue
                if not include_disabled and not request.enabled:
                    continue
                requests.append(request)
            return requests

    def latest_dispatch_by_request_id(self) -> dict[str, WakeDispatchRecord]:
        with self._lock:
            payload = self._read_raw()
            raw_dispatches = payload.get("dispatches", {})
            if not isinstance(raw_dispatches, dict):
                return {}
            latest: dict[str, WakeDispatchRecord] = {}
            for raw_value in raw_dispatches.values():
                if not isinstance(raw_value, dict):
                    continue
                try:
                    dispatch = WakeDispatchRecord.model_validate(raw_value)
                except Exception:
                    continue
                if not dispatch.request_id:
                    continue
                current = latest.get(dispatch.request_id)
                if current is None or dispatch.dispatched_at > current.dispatched_at:
                    latest[dispatch.request_id] = dispatch
            return latest

    def _compact(self, payload: dict[str, Any]) -> None:
        cutoff = datetime.now(UTC) - _WAKE_RETENTION

        dispatches = payload.get("dispatches")
        if isinstance(dispatches, dict):
            payload["dispatches"] = {
                dispatch_id: raw_value
                for dispatch_id, raw_value in dispatches.items()
                if self._parse_dt((raw_value or {}).get("dispatched_at")) >= cutoff
            }

        messages = payload.get("messages")
        if isinstance(messages, dict):
            payload["messages"] = {
                message_id: raw_value
                for message_id, raw_value in messages.items()
                if self._parse_dt((raw_value or {}).get("created_at")) >= cutoff
            }

        requests = payload.get("requests")
        if isinstance(requests, dict):
            compacted_requests: dict[str, Any] = {}
            for request_id, raw_value in requests.items():
                if not isinstance(raw_value, dict):
                    compacted_requests[request_id] = raw_value
                    continue
                updated_at = self._parse_dt(raw_value.get("updated_at"))
                enabled = bool(raw_value.get("enabled", True))
                if not enabled and updated_at < cutoff:
                    continue
                compacted_requests[request_id] = raw_value
            payload["requests"] = compacted_requests

    def _read_raw(self) -> dict[str, Any]:
        return json.loads(self.path.read_text(encoding="utf-8"))

    def _atomic_write(self, payload: dict[str, Any]) -> None:
        tmp_path = self.path.with_suffix(f"{self.path.suffix}.tmp")
        tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        tmp_path.replace(self.path)

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
