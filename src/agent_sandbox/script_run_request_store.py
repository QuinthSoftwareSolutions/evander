from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Lock
from typing import Any
from uuid import uuid4

from .models import ScriptRunRequestRecord

_SCRIPT_RUN_REQUEST_RETENTION = timedelta(days=90)


class ScriptRunRequestStore:
    """Atomic JSON-backed store for queued script run requests."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._atomic_write({"requests": {}})

    def enqueue(
        self,
        *,
        session_id: str,
        directory_path: str,
        script_name: str,
        parameters: list[str],
        request_id: str | None = None,
        source: str = "mcp_tool",
        metadata: dict[str, Any] | None = None,
    ) -> ScriptRunRequestRecord:
        now = datetime.now(UTC)
        with self._lock:
            payload = self._read_raw()
            requests = payload.setdefault("requests", {})
            normalized_request_id = str(request_id or "").strip() or f"script-run-{uuid4().hex}"
            while normalized_request_id in requests:
                if request_id:
                    raise ValueError(f"Script run request already exists: {normalized_request_id}")
                normalized_request_id = f"script-run-{uuid4().hex}"
            record = ScriptRunRequestRecord(
                request_id=normalized_request_id,
                session_id=session_id,
                directory_path=directory_path,
                script_name=script_name,
                parameters=parameters,
                source=source,
                created_at=now,
                metadata=dict(metadata or {}),
            )
            requests[record.request_id] = record.model_dump(mode="json")
            self._compact(payload)
            self._atomic_write(payload)
            return record

    def list_requests(self) -> list[ScriptRunRequestRecord]:
        with self._lock:
            payload = self._read_raw()
            raw_requests = payload.get("requests", {})
            if not isinstance(raw_requests, dict):
                return []
            records: list[ScriptRunRequestRecord] = []
            for raw_value in raw_requests.values():
                if not isinstance(raw_value, dict):
                    continue
                records.append(ScriptRunRequestRecord.model_validate(raw_value))
            records.sort(key=lambda item: (item.created_at, item.request_id))
            return records

    def read_raw(self) -> dict[str, Any]:
        with self._lock:
            return self._read_raw()

    def _compact(self, payload: dict[str, Any]) -> None:
        cutoff = datetime.now(UTC) - _SCRIPT_RUN_REQUEST_RETENTION
        requests = payload.get("requests")
        if isinstance(requests, dict):
            payload["requests"] = {
                request_id: raw_value
                for request_id, raw_value in requests.items()
                if self._parse_dt((raw_value or {}).get("created_at")) >= cutoff
            }

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
