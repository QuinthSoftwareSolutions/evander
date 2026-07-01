from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Lock
from typing import Any, Protocol

from pydantic import BaseModel, Field

from evander.models import InlineMediaRef

_FEEDBACK_RETENTION = timedelta(days=90)


class FeedbackEnvelope(BaseModel):
    feedback_id: str
    provider: str
    repo: str
    pr_id: str
    thread_id: str | None = None
    comment_id: str | None = None
    author: str
    body: str
    file_path: str | None = None
    line: int | None = None
    created_at: datetime
    in_reply_to: str | None = None
    resolved: bool | None = None
    inline_media: list[dict[str, Any] | InlineMediaRef] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


@dataclass(frozen=True)
class ProviderPollResult:
    feedback: list[FeedbackEnvelope]
    next_cursor: str | None


class FeedbackProviderConfigurationError(RuntimeError):
    """Raised when a feedback provider cannot poll due to local configuration."""


class FeedbackProviderAuthError(RuntimeError):
    """Raised when a feedback provider rejects the configured credentials."""


class PrFeedbackRepository(Protocol):
    """Repository contract for persisted PR feedback state."""

    def get_cursor(self, stream_key: str) -> str | None: ...

    def set_cursor(self, stream_key: str, cursor: str | None) -> None: ...

    def add_feedback_if_new(self, envelope: FeedbackEnvelope) -> bool: ...

    def get_session_cursor(self, session_id: str, stream_key: str) -> str | None: ...

    def advance_session_cursor(
        self, session_id: str, stream_key: str, cursor: str | None
    ) -> str | None: ...

    def list_feedback_for_streams(
        self,
        *,
        streams: list[dict[str, str]],
        after: dict[tuple[str, str, str], str | None],
        exclude_feedback_ids: set[str] | None = None,
        limit: int | None = None,
    ) -> list[FeedbackEnvelope]: ...

    def list_all_feedback(
        self,
        *,
        after: dict[tuple[str, str, str], str | None] | None = None,
        allowed_providers: set[str] | None = None,
        exclude_feedback_ids: set[str] | None = None,
        limit: int | None = None,
    ) -> list[FeedbackEnvelope]: ...

    def mark_session_streams_read(
        self,
        *,
        session_id: str,
        streams: list[dict[str, str]],
        cursor: str | None,
    ) -> dict[str, str | None]: ...

    def has_unread_feedback(self, *, session_id: str, streams: list[dict[str, str]]) -> bool: ...

    def read_raw(self) -> dict[str, Any]: ...

    def prune_streams(self, streams: set[tuple[str, str, str]]) -> tuple[int, int]: ...


class PrFeedbackStore:
    """Atomic JSON-backed store for feedback, provider cursors, and session read cursors."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._atomic_write({"cursors": {}, "feedback": {}, "session_reads": {}})

    def get_cursor(self, stream_key: str) -> str | None:
        with self._lock:
            payload = self._read_raw()
            cursor = payload.get("cursors", {}).get(stream_key)
            return str(cursor) if cursor is not None else None

    def set_cursor(self, stream_key: str, cursor: str | None) -> None:
        with self._lock:
            payload = self._read_raw()
            cursors = payload.setdefault("cursors", {})
            if cursor is None:
                cursors.pop(stream_key, None)
            else:
                cursors[stream_key] = cursor
            self._compact_feedback(payload)
            self._atomic_write(payload)

    def add_feedback_if_new(self, envelope: FeedbackEnvelope) -> bool:
        with self._lock:
            payload = self._read_raw()
            stored_feedback = payload.setdefault("feedback", {})
            if envelope.feedback_id in stored_feedback:
                return False
            stored_feedback[envelope.feedback_id] = envelope.model_dump(mode="json")
            self._compact_feedback(payload)
            self._atomic_write(payload)
            return True

    def get_session_cursor(self, session_id: str, stream_key: str) -> str | None:
        with self._lock:
            payload = self._read_raw()
            raw_session_reads = payload.get("session_reads", {})
            if not isinstance(raw_session_reads, dict):
                return None
            session_reads = raw_session_reads.get(session_id, {})
            if not isinstance(session_reads, dict):
                return None
            cursor = session_reads.get(stream_key)
            return str(cursor) if cursor is not None else None

    def advance_session_cursor(
        self, session_id: str, stream_key: str, cursor: str | None
    ) -> str | None:
        with self._lock:
            payload = self._read_raw()
            session_reads = payload.setdefault("session_reads", {})
            if not isinstance(session_reads, dict):
                session_reads = {}
                payload["session_reads"] = session_reads
            current_reads = session_reads.get(session_id)
            if not isinstance(current_reads, dict):
                current_reads = {}
                session_reads[session_id] = current_reads

            current_cursor = current_reads.get(stream_key)
            next_cursor = _latest_cursor(
                [
                    str(current_cursor) if current_cursor is not None else None,
                    cursor,
                ]
            )
            if next_cursor is None:
                current_reads.pop(stream_key, None)
            else:
                current_reads[stream_key] = next_cursor
            if not current_reads:
                session_reads.pop(session_id, None)
            self._compact_feedback(payload)
            self._atomic_write(payload)
            return next_cursor

    def has_unread_feedback(self, *, session_id: str, streams: list[dict[str, str]]) -> bool:
        with self._lock:
            payload = self._read_raw()
            feedback_items = payload.get("feedback", {})
            if not isinstance(feedback_items, dict):
                return False
            for stream in streams:
                stream_key = _stream_key(stream["provider"], stream["repo"], stream["pr_id"])
                session_cursor = self._get_session_cursor_from_payload(
                    payload, session_id, stream_key
                )
                if self._stream_has_unread_feedback(
                    feedback_items=feedback_items,
                    provider=stream["provider"],
                    repo=stream["repo"],
                    pr_id=stream["pr_id"],
                    cursor=session_cursor,
                ):
                    return True
            return False

    def list_feedback_for_streams(
        self,
        *,
        streams: list[dict[str, str]],
        after: dict[tuple[str, str, str], str | None],
        exclude_feedback_ids: set[str] | None = None,
        limit: int | None = None,
    ) -> list[FeedbackEnvelope]:
        with self._lock:
            payload = self._read_raw()
            allowed_streams = {
                (stream["provider"], stream["repo"], stream["pr_id"]) for stream in streams
            }
            return self._list_feedback_from_payload(
                payload,
                allowed_streams=allowed_streams,
                after=after,
                exclude_feedback_ids=exclude_feedback_ids,
                limit=limit,
            )

    def list_all_feedback(
        self,
        *,
        after: dict[tuple[str, str, str], str | None] | None = None,
        allowed_providers: set[str] | None = None,
        exclude_feedback_ids: set[str] | None = None,
        limit: int | None = None,
    ) -> list[FeedbackEnvelope]:
        with self._lock:
            payload = self._read_raw()
            return self._list_feedback_from_payload(
                payload,
                after=after,
                allowed_providers=allowed_providers,
                exclude_feedback_ids=exclude_feedback_ids,
                limit=limit,
            )

    def mark_session_streams_read(
        self,
        *,
        session_id: str,
        streams: list[dict[str, str]],
        cursor: str | None,
    ) -> dict[str, str | None]:
        with self._lock:
            payload = self._read_raw()
            session_reads = payload.setdefault("session_reads", {})
            if not isinstance(session_reads, dict):
                session_reads = {}
                payload["session_reads"] = session_reads
            current_reads = session_reads.get(session_id)
            if not isinstance(current_reads, dict):
                current_reads = {}
                session_reads[session_id] = current_reads

            updated: dict[str, str | None] = {}
            for stream in streams:
                stream_key = _stream_key(stream["provider"], stream["repo"], stream["pr_id"])
                current_cursor = current_reads.get(stream_key)
                next_cursor = _latest_cursor(
                    [
                        str(current_cursor) if current_cursor is not None else None,
                        cursor,
                    ]
                )
                if next_cursor is None:
                    current_reads.pop(stream_key, None)
                else:
                    current_reads[stream_key] = next_cursor
                updated[stream_key] = next_cursor

            if not current_reads:
                session_reads.pop(session_id, None)
            self._compact_feedback(payload)
            self._atomic_write(payload)
            return updated

    def read_raw(self) -> dict[str, Any]:
        with self._lock:
            return self._read_raw()

    def prune_streams(self, streams: set[tuple[str, str, str]]) -> tuple[int, int]:
        if not streams:
            return 0, 0
        with self._lock:
            payload = self._read_raw()
            removed_cursor_count = 0
            cursors = payload.get("cursors")
            if isinstance(cursors, dict):
                for provider, repo, pr_id in streams:
                    stream_key = _stream_key(provider, repo, pr_id)
                    if stream_key in cursors:
                        cursors.pop(stream_key, None)
                        removed_cursor_count += 1

            removed_feedback_count = 0
            feedback = payload.get("feedback")
            if isinstance(feedback, dict):
                kept_feedback: dict[str, object] = {}
                for feedback_id, raw_feedback in feedback.items():
                    if not isinstance(raw_feedback, dict):
                        kept_feedback[feedback_id] = raw_feedback
                        continue
                    key = (
                        str(raw_feedback.get("provider", "")).strip(),
                        str(raw_feedback.get("repo", "")).strip(),
                        str(raw_feedback.get("pr_id", "")).strip(),
                    )
                    if key in streams:
                        removed_feedback_count += 1
                        continue
                    kept_feedback[feedback_id] = raw_feedback
                payload["feedback"] = kept_feedback

            if removed_cursor_count or removed_feedback_count:
                self._atomic_write(payload)
            return removed_cursor_count, removed_feedback_count

    @staticmethod
    def _get_session_cursor_from_payload(
        payload: dict[str, Any], session_id: str, stream_key: str
    ) -> str | None:
        raw_session_reads = payload.get("session_reads", {})
        if not isinstance(raw_session_reads, dict):
            return None
        session_reads = raw_session_reads.get(session_id, {})
        if not isinstance(session_reads, dict):
            return None
        cursor = session_reads.get(stream_key)
        return str(cursor) if cursor is not None else None

    @staticmethod
    def _stream_has_unread_feedback(
        *,
        feedback_items: dict[str, Any],
        provider: str,
        repo: str,
        pr_id: str,
        cursor: str | None,
    ) -> bool:
        cursor_dt = _parse_dt(cursor) if cursor else None
        for raw_value in feedback_items.values():
            if not isinstance(raw_value, dict):
                continue
            if (
                str(raw_value.get("provider")) != provider
                or str(raw_value.get("repo")) != repo
                or str(raw_value.get("pr_id")) != pr_id
            ):
                continue
            created_at = _parse_dt(raw_value.get("created_at"))
            if cursor_dt and created_at <= cursor_dt:
                continue
            return True
        return False

    @staticmethod
    def _list_feedback_from_payload(
        payload: dict[str, Any],
        *,
        after: dict[tuple[str, str, str], str | None] | None = None,
        allowed_streams: set[tuple[str, str, str]] | None = None,
        allowed_providers: set[str] | None = None,
        exclude_feedback_ids: set[str] | None = None,
        limit: int | None = None,
    ) -> list[FeedbackEnvelope]:
        feedback_items = payload.get("feedback", {})
        if not isinstance(feedback_items, dict):
            return []

        ignored_feedback_ids = exclude_feedback_ids or set()
        items: list[FeedbackEnvelope] = []
        for raw_value in feedback_items.values():
            if not isinstance(raw_value, dict):
                continue
            candidate = FeedbackEnvelope.model_validate(raw_value)
            if candidate.feedback_id in ignored_feedback_ids:
                continue
            if allowed_providers is not None and candidate.provider not in allowed_providers:
                continue
            key = (candidate.provider, candidate.repo, candidate.pr_id)
            if allowed_streams is not None and key not in allowed_streams:
                continue
            effective_cursor = after.get(key) if after else None
            if effective_cursor and candidate.created_at <= _parse_dt(effective_cursor):
                continue
            items.append(candidate)
        items.sort(key=lambda item: item.created_at)
        if limit is not None:
            return items[:limit]
        return items

    def _read_raw(self) -> dict[str, Any]:
        return json.loads(self.path.read_text(encoding="utf-8"))

    def _compact_feedback(self, payload: dict[str, Any]) -> None:
        feedback = payload.get("feedback")
        cursors = payload.get("cursors")
        if not isinstance(feedback, dict) or not isinstance(cursors, dict):
            return

        cutoff = datetime.now(timezone.utc) - _FEEDBACK_RETENTION
        compacted: dict[str, Any] = {}
        changed = False
        for feedback_id, raw_value in feedback.items():
            if not isinstance(raw_value, dict):
                compacted[feedback_id] = raw_value
                continue
            provider = str(raw_value.get("provider", "")).strip()
            repo = str(raw_value.get("repo", "")).strip()
            pr_id = str(raw_value.get("pr_id", "")).strip()
            if not provider or not repo or not pr_id:
                compacted[feedback_id] = raw_value
                continue
            created_at = _parse_dt(raw_value.get("created_at"))
            stream_cursor = cursors.get(_stream_key(provider, repo, pr_id))
            if (
                created_at < cutoff
                and stream_cursor is not None
                and created_at <= _parse_dt(stream_cursor)
            ):
                changed = True
                continue
            compacted[feedback_id] = raw_value
        if changed:
            payload["feedback"] = compacted

    def _atomic_write(self, payload: dict[str, Any]) -> None:
        tmp_path = self.path.with_suffix(f"{self.path.suffix}.tmp")
        tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        tmp_path.replace(self.path)


def _stream_key(provider: str, repo: str, pr_id: str) -> str:
    return f"{provider}:{repo}:{pr_id}"


def _latest_cursor(candidates: list[str | None]) -> str | None:
    datetimes = [_parse_dt(value) for value in candidates if value]
    if not datetimes:
        return None
    latest = max(datetimes)
    return latest.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")


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
