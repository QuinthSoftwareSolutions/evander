from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Lock
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from .models import DirectoryPath

ReviewRequestStatus = Literal["pending", "leased", "dispatched", "completed", "failed", "cancelled"]

_REVIEW_REQUEST_RETENTION = timedelta(days=90)


class ReviewRequestRecord(BaseModel):
    request_id: str = Field(min_length=1)
    requested_at: datetime
    requested_by: str = ""
    request_channel: str = ""
    provider: str = Field(min_length=1)
    repo: str = Field(min_length=1)
    pr_id: str = Field(min_length=1)
    canonical_url: str = ""
    title: str = ""
    body: str = ""
    base_branch: str = ""
    head_branch: str = ""
    base_commit: str | None = None
    head_commit: str | None = None
    review_type: str = "review"
    instructions: str = ""
    directory_path: str = "root"
    model: str | None = None
    labels: dict[str, str] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    status: ReviewRequestStatus = "pending"
    updated_at: datetime | None = None
    lease_owner: str | None = None
    lease_expires_at: datetime | None = None
    dispatch_count: int = 0

    @field_validator(
        "request_id",
        "requested_by",
        "request_channel",
        "provider",
        "repo",
        "pr_id",
        "canonical_url",
        "title",
        "body",
        "base_branch",
        "head_branch",
        "review_type",
        "instructions",
        "directory_path",
        "model",
        mode="before",
    )
    @classmethod
    def normalize_optional_strings(cls, value: object) -> object:
        if value is None:
            return None
        return str(value).strip()

    @field_validator("directory_path", mode="before")
    @classmethod
    def normalize_directory_path(cls, value: object) -> str:
        text = str(value).strip() if value is not None else "root"
        return DirectoryPath(segments=text or "root").as_posix()

    @field_validator("labels", mode="before")
    @classmethod
    def normalize_labels(cls, value: object) -> object:
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise ValueError("labels must be a mapping")
        normalized: dict[str, str] = {}
        for raw_key, raw_value in value.items():
            key = str(raw_key).strip()
            if not key:
                continue
            mapped_value = "" if raw_value is None else str(raw_value).strip()
            if mapped_value:
                normalized[key] = mapped_value
        return normalized

    @field_validator("metadata", mode="before")
    @classmethod
    def normalize_metadata(cls, value: object) -> object:
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise ValueError("metadata must be a mapping")
        return value


class ReviewRequestStore:
    """Atomic JSON-backed store for review requests and their workflow state."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._atomic_write({"requests": {}})

    def add_request_if_new(self, request: ReviewRequestRecord) -> bool:
        with self._lock:
            payload = self._read_raw()
            requests = payload.setdefault("requests", {})
            if request.request_id in requests:
                return False
            requests[request.request_id] = request.model_dump(mode="json")
            self._compact_requests(payload)
            self._atomic_write(payload)
            return True

    def get_request(self, request_id: str) -> ReviewRequestRecord | None:
        with self._lock:
            payload = self._read_raw()
            raw_request = payload.get("requests", {}).get(request_id)
            return self._validate_request(raw_request)

    def list_requests(
        self,
        *,
        status: ReviewRequestStatus | None = None,
        directory_path: str | None = None,
        limit: int | None = None,
    ) -> list[ReviewRequestRecord]:
        with self._lock:
            payload = self._read_raw()
            requests: list[ReviewRequestRecord] = []
            normalized_directory_path = (
                DirectoryPath(segments=directory_path).as_posix()
                if directory_path and str(directory_path).strip()
                else None
            )
            for raw_value in payload.get("requests", {}).values():
                candidate = self._validate_request(raw_value)
                if candidate is None:
                    continue
                if status is not None and candidate.status != status:
                    continue
                if normalized_directory_path is not None and not _request_in_scope(
                    candidate.directory_path,
                    normalized_directory_path,
                ):
                    continue
                requests.append(candidate)
            requests.sort(key=self._request_sort_key)
            if limit is not None:
                return requests[:limit]
            return requests

    def claim_ready_requests(
        self,
        *,
        lease_owner: str,
        lease_seconds: int = 300,
        directory_path: str | None = None,
        limit: int | None = None,
        now: datetime | None = None,
    ) -> list[ReviewRequestRecord]:
        normalized_owner = str(lease_owner).strip()
        if not normalized_owner:
            raise ValueError("lease_owner must be a non-empty string")
        lease_seconds = int(lease_seconds)
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be greater than zero")
        lease_started_at = now or datetime.now(UTC)
        lease_expires_at = lease_started_at + timedelta(seconds=lease_seconds)
        normalized_directory_path = (
            DirectoryPath(segments=directory_path).as_posix()
            if directory_path and str(directory_path).strip()
            else None
        )

        with self._lock:
            payload = self._read_raw()
            requests = payload.setdefault("requests", {})
            claimed: list[ReviewRequestRecord] = []
            candidates: list[tuple[str, ReviewRequestRecord]] = []
            for request_id, raw_value in requests.items():
                request = self._validate_request(raw_value)
                if request is None:
                    continue
                if request.status not in {"pending", "leased"}:
                    continue
                if normalized_directory_path is not None and not _request_in_scope(
                    request.directory_path,
                    normalized_directory_path,
                ):
                    continue
                if request.status == "leased" and request.lease_expires_at is not None:
                    if request.lease_expires_at > lease_started_at:
                        continue
                candidates.append((request_id, request))

            for request_id, request in sorted(candidates, key=lambda item: self._request_sort_key(item[1])):
                request.status = "leased"
                request.lease_owner = normalized_owner
                request.lease_expires_at = lease_expires_at
                request.dispatch_count += 1
                request.updated_at = lease_started_at
                requests[request_id] = request.model_dump(mode="json")
                claimed.append(request)
                if limit is not None and len(claimed) >= limit:
                    break
            self._compact_requests(payload)
            self._atomic_write(payload)
            return claimed

    def set_status(
        self,
        request_id: str,
        *,
        status: ReviewRequestStatus,
        updated_at: datetime | None = None,
        lease_owner: str | None = None,
        lease_expires_at: datetime | None = None,
        clear_lease: bool = False,
    ) -> ReviewRequestRecord | None:
        with self._lock:
            payload = self._read_raw()
            requests = payload.setdefault("requests", {})
            raw_request = requests.get(request_id)
            request = self._validate_request(raw_request)
            if request is None:
                return None
            request.status = status
            request.updated_at = updated_at or datetime.now(UTC)
            if clear_lease:
                request.lease_owner = None
                request.lease_expires_at = None
            else:
                if lease_owner is not None:
                    request.lease_owner = lease_owner
                if lease_expires_at is not None:
                    request.lease_expires_at = lease_expires_at
            requests[request_id] = request.model_dump(mode="json")
            self._compact_requests(payload)
            self._atomic_write(payload)
            return request

    def read_raw(self) -> dict[str, Any]:
        with self._lock:
            return self._read_raw()

    def _read_raw(self) -> dict[str, Any]:
        return json.loads(self.path.read_text(encoding="utf-8"))

    def _atomic_write(self, payload: dict[str, Any]) -> None:
        tmp_path = self.path.with_suffix(f"{self.path.suffix}.tmp")
        tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        tmp_path.replace(self.path)

    def _compact_requests(self, payload: dict[str, Any]) -> None:
        requests = payload.get("requests")
        if not isinstance(requests, dict):
            return

        cutoff = datetime.now(UTC) - _REVIEW_REQUEST_RETENTION
        compacted: dict[str, Any] = {}
        changed = False
        for request_id, raw_value in requests.items():
            request = self._validate_request(raw_value)
            if request is None:
                compacted[request_id] = raw_value
                continue
            if request.requested_at < cutoff and request.status in {
                "completed",
                "dispatched",
                "failed",
                "cancelled",
            }:
                changed = True
                continue
            compacted[request_id] = raw_value
        if changed:
            payload["requests"] = compacted

    @staticmethod
    def _validate_request(raw_value: object) -> ReviewRequestRecord | None:
        if not isinstance(raw_value, dict):
            return None
        return ReviewRequestRecord.model_validate(raw_value)

    @staticmethod
    def _request_sort_key(request: ReviewRequestRecord) -> tuple[datetime, str]:
        return (request.requested_at, request.request_id)


def _request_in_scope(request_directory_path: str, scope_directory_path: str) -> bool:
    normalized_request = DirectoryPath(segments=request_directory_path).as_posix()
    normalized_scope = DirectoryPath(segments=scope_directory_path).as_posix()
    return normalized_request == normalized_scope or normalized_request.startswith(f"{normalized_scope}/")
