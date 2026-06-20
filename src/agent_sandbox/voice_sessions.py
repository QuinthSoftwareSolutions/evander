from __future__ import annotations

import json
import os
import re
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import RLock
from typing import Any, Literal
from uuid import uuid4

import httpx
from pydantic import BaseModel, Field, field_validator

from agent_sandbox.contact_allowlists import (
    require_allowed_contact_id,
    validate_allowed_contact_ids_for_directory,
)
from agent_sandbox.directory_config import (
    ContactDirectoryConfig,
    DirectoryContactEntry,
    DirectoryContactPhoneNumber,
)
from agent_sandbox.email.http_utils import ProviderRequestError
from agent_sandbox.messaging.wake_store import WakeRequestRepository
from agent_sandbox.models import (
    SessionManifest,
    WakeScheduleDefinition,
    tool_name_value,
    utc_now,
)
from agent_sandbox.retell_phone import (
    RetellPhoneCallService,
    sanitize_retell_metadata,
)

VoiceSessionChannel = Literal["phone", "teams_meeting"]
VoiceSessionStatus = Literal["created", "connecting", "active", "ended", "failed"]
VoiceSessionReconciliationStatus = Literal["not_requested", "scheduled", "failed"]
VoiceQueryStatus = Literal["pending", "answered", "failed", "timeout"]


class VoiceTranscriptEvent(BaseModel):
    sequence: int = Field(ge=1)
    text: str = Field(min_length=1)
    speaker: str | None = None
    occurred_at: datetime = Field(default_factory=utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("text", "speaker", mode="before")
    @classmethod
    def normalize_optional_text(cls, value: object) -> object:
        if value is None:
            return None
        text = str(value).strip()
        return text or None


class VoiceSessionRecord(BaseModel):
    voice_session_id: str = Field(min_length=1)
    request_id: str = Field(min_length=1)
    origin_session_id: str = Field(min_length=1)
    directory_path: str = Field(min_length=1)
    channel: VoiceSessionChannel
    status: VoiceSessionStatus = "created"
    contact_id: str | None = None
    contact_display_name: str | None = None
    meeting_id: str | None = None
    prompt_metadata: dict[str, Any] = Field(default_factory=dict)
    caller_metadata: dict[str, Any] = Field(default_factory=dict)
    projection: dict[str, Any] = Field(default_factory=dict)
    bridge_session_id: str | None = None
    bridge_metadata: dict[str, Any] = Field(default_factory=dict)
    realtime_metadata: dict[str, Any] = Field(default_factory=dict)
    transcript_events: list[VoiceTranscriptEvent] = Field(default_factory=list)
    transcript: str | None = None
    summary: str | None = None
    decisions: list[str] = Field(default_factory=list)
    action_items: list[str] = Field(default_factory=list)
    reconciliation_status: VoiceSessionReconciliationStatus = "not_requested"
    reconciliation_request_id: str | None = None
    reconciliation_prompt: str | None = None
    error: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    ended_at: datetime | None = None

    @field_validator(
        "voice_session_id",
        "request_id",
        "origin_session_id",
        "directory_path",
        "contact_id",
        "contact_display_name",
        "meeting_id",
        "bridge_session_id",
        "summary",
        "reconciliation_request_id",
        "reconciliation_prompt",
        "error",
        mode="before",
    )
    @classmethod
    def normalize_optional_strings(cls, value: object) -> object:
        if value is None:
            return None
        text = str(value).strip()
        return text or None


class VoiceQueryRecord(BaseModel):
    query_id: str = Field(min_length=1)
    voice_session_id: str = Field(min_length=1)
    origin_session_id: str = Field(min_length=1)
    directory_path: str = Field(min_length=1)
    question: str = Field(min_length=1)
    transcript_excerpt: str | None = None
    status: VoiceQueryStatus = "pending"
    answer: str | None = None
    citations: list[str] = Field(default_factory=list)
    confidence: str | None = None
    error: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    wake_request_id: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    deadline_at: datetime | None = None
    answered_at: datetime | None = None

    @field_validator(
        "query_id",
        "voice_session_id",
        "origin_session_id",
        "directory_path",
        "question",
        "transcript_excerpt",
        "answer",
        "confidence",
        "error",
        "wake_request_id",
        mode="before",
    )
    @classmethod
    def normalize_optional_text(cls, value: object) -> object:
        if value is None:
            return None
        text = str(value).strip()
        return text or None


class VoiceSessionStore:
    """Atomic JSON store for projected voice session state."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._atomic_write(_empty_voice_store_payload())

    def prepare_session(
        self,
        *,
        origin_session_id: str,
        directory_path: str,
        channel: VoiceSessionChannel,
        request_id: str | None,
        prompt_metadata: dict[str, Any],
        projection: dict[str, Any],
        caller_metadata: dict[str, Any] | None = None,
        contact_id: str | None = None,
        contact_display_name: str | None = None,
        meeting_id: str | None = None,
    ) -> tuple[VoiceSessionRecord, bool]:
        normalized_request_id = str(request_id or "").strip() or f"voice-{uuid4().hex}"
        now = datetime.now(UTC)
        with self._lock:
            payload = self._read_raw()
            request_index = _ensure_mapping(payload, "request_index")
            existing_id = request_index.get(normalized_request_id)
            if isinstance(existing_id, str) and existing_id:
                record = self._require_record(payload, existing_id)
                if (
                    record.origin_session_id != origin_session_id
                    or record.directory_path != directory_path
                    or record.channel != channel
                    or record.contact_id != _optional_text(contact_id)
                    or (
                        _optional_text(meeting_id) is not None
                        and record.meeting_id != _optional_text(meeting_id)
                    )
                    or record.prompt_metadata != dict(prompt_metadata or {})
                ):
                    raise ValueError(
                        "Voice session request_id was already used with different inputs"
                    )
                return record, False

            voice_session_id = f"voice-{uuid4().hex}"
            while voice_session_id in _ensure_mapping(payload, "sessions"):
                voice_session_id = f"voice-{uuid4().hex}"
            record = VoiceSessionRecord(
                voice_session_id=voice_session_id,
                request_id=normalized_request_id,
                origin_session_id=origin_session_id,
                directory_path=directory_path,
                channel=channel,
                contact_id=_optional_text(contact_id),
                contact_display_name=_optional_text(contact_display_name),
                meeting_id=_optional_text(meeting_id),
                prompt_metadata=dict(prompt_metadata or {}),
                caller_metadata=sanitize_voice_metadata(caller_metadata or {}),
                projection=sanitize_voice_metadata(projection),
                created_at=now,
                updated_at=now,
            )
            _ensure_mapping(payload, "sessions")[voice_session_id] = record.model_dump(
                mode="json"
            )
            request_index[normalized_request_id] = voice_session_id
            self._atomic_write(payload)
            return record, True

    def get_by_voice_session_id(self, voice_session_id: str) -> VoiceSessionRecord | None:
        normalized = str(voice_session_id or "").strip()
        if not normalized:
            return None
        with self._lock:
            raw = _ensure_mapping(self._read_raw(), "sessions").get(normalized)
        if not isinstance(raw, dict):
            return None
        return VoiceSessionRecord.model_validate(raw)

    def get_by_request_id(self, request_id: str) -> VoiceSessionRecord | None:
        normalized = str(request_id or "").strip()
        if not normalized:
            return None
        with self._lock:
            payload = self._read_raw()
            voice_session_id = _ensure_mapping(payload, "request_index").get(normalized)
            raw = (
                _ensure_mapping(payload, "sessions").get(voice_session_id)
                if isinstance(voice_session_id, str)
                else None
            )
        if not isinstance(raw, dict):
            return None
        return VoiceSessionRecord.model_validate(raw)

    def get_by_bridge_session_id(self, bridge_session_id: str) -> VoiceSessionRecord | None:
        normalized = str(bridge_session_id or "").strip()
        if not normalized:
            return None
        with self._lock:
            payload = self._read_raw()
            voice_session_id = _ensure_mapping(payload, "bridge_index").get(normalized)
            raw = (
                _ensure_mapping(payload, "sessions").get(voice_session_id)
                if isinstance(voice_session_id, str)
                else None
            )
        if not isinstance(raw, dict):
            return None
        return VoiceSessionRecord.model_validate(raw)

    def attach_bridge_session(
        self,
        voice_session_id: str,
        *,
        bridge_session_id: str | None,
        status: str | None,
        bridge_metadata: dict[str, Any] | None = None,
        realtime_metadata: dict[str, Any] | None = None,
        meeting_id: str | None = None,
    ) -> VoiceSessionRecord:
        with self._lock:
            payload = self._read_raw()
            record = self._require_record(payload, voice_session_id)
            record.bridge_session_id = _optional_text(bridge_session_id) or record.bridge_session_id
            if meeting_id:
                record.meeting_id = _optional_text(meeting_id)
            previous_status = record.status
            record.status = normalize_voice_session_status(status, default=record.status)
            if previous_status in {"ended", "failed"} and record.status not in {
                "ended",
                "failed",
            }:
                record.error = None
                record.ended_at = None
            record.bridge_metadata = sanitize_voice_metadata(
                {**record.bridge_metadata, **dict(bridge_metadata or {})}
            )
            record.realtime_metadata = sanitize_voice_metadata(
                {**record.realtime_metadata, **dict(realtime_metadata or {})}
            )
            record.updated_at = datetime.now(UTC)
            if record.status in {"ended", "failed"} and record.ended_at is None:
                record.ended_at = record.updated_at
            self._put_record(payload, record)
            if record.bridge_session_id:
                _ensure_mapping(payload, "bridge_index")[record.bridge_session_id] = (
                    record.voice_session_id
                )
            self._atomic_write(payload)
            return record

    def update_from_bridge_payload(
        self,
        voice_session_id: str,
        payload: dict[str, Any],
    ) -> VoiceSessionRecord:
        with self._lock:
            raw_store = self._read_raw()
            record = self._require_record(raw_store, voice_session_id)
            _apply_bridge_payload(record, payload)
            self._put_record(raw_store, record)
            if record.bridge_session_id:
                _ensure_mapping(raw_store, "bridge_index")[record.bridge_session_id] = (
                    record.voice_session_id
                )
            self._atomic_write(raw_store)
            return record

    def append_transcript_event(
        self,
        voice_session_id: str,
        *,
        text: str,
        speaker: str | None = None,
        occurred_at: datetime | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> VoiceSessionRecord | None:
        normalized_text = str(text or "").strip()
        if not normalized_text:
            return self.get_by_voice_session_id(voice_session_id)
        with self._lock:
            payload = self._read_raw()
            record = self._require_record(payload, voice_session_id)
            next_sequence = len(record.transcript_events) + 1
            record.transcript_events.append(
                VoiceTranscriptEvent(
                    sequence=next_sequence,
                    text=normalized_text,
                    speaker=speaker,
                    occurred_at=occurred_at or datetime.now(UTC),
                    metadata=sanitize_voice_metadata(metadata or {}),
                )
            )
            record.transcript = _transcript_from_events(record.transcript_events)
            record.updated_at = datetime.now(UTC)
            self._put_record(payload, record)
            self._atomic_write(payload)
            return record

    def complete_session(
        self,
        voice_session_id: str,
        *,
        status: VoiceSessionStatus = "ended",
        transcript: str | None = None,
        summary: str | None = None,
        decisions: list[str] | None = None,
        action_items: list[str] | None = None,
        error: str | None = None,
    ) -> VoiceSessionRecord:
        if status not in {"ended", "failed"}:
            raise ValueError("complete_session status must be ended or failed")
        with self._lock:
            payload = self._read_raw()
            record = self._require_record(payload, voice_session_id)
            record.status = status
            if transcript is not None:
                record.transcript = _optional_text(transcript)
            if summary is not None:
                record.summary = _optional_text(summary)
            if decisions is not None:
                record.decisions = [_normalize_list_text(item) for item in decisions if _normalize_list_text(item)]
            if action_items is not None:
                record.action_items = [
                    _normalize_list_text(item) for item in action_items if _normalize_list_text(item)
                ]
            if error is not None:
                record.error = _optional_text(error)
            now = datetime.now(UTC)
            record.updated_at = now
            record.ended_at = record.ended_at or now
            self._put_record(payload, record)
            self._atomic_write(payload)
            return record

    def create_query(
        self,
        voice_session_id: str,
        *,
        question: str,
        transcript_excerpt: str | None = None,
        metadata: dict[str, Any] | None = None,
        query_id: str | None = None,
        deadline_seconds: int | None = None,
    ) -> VoiceQueryRecord:
        normalized_question = str(question or "").strip()
        if not normalized_question:
            raise ValueError("question is required")
        now = datetime.now(UTC)
        with self._lock:
            payload = self._read_raw()
            session_record = self._require_record(payload, voice_session_id)
            queries = _ensure_mapping(payload, "queries")
            normalized_query_id = _optional_text(query_id) or f"voice-query-{uuid4().hex}"
            while normalized_query_id in queries:
                normalized_query_id = f"voice-query-{uuid4().hex}"
            deadline_at = None
            if deadline_seconds is not None and deadline_seconds > 0:
                deadline_at = now + timedelta(seconds=deadline_seconds)
            record = VoiceQueryRecord(
                query_id=normalized_query_id,
                voice_session_id=session_record.voice_session_id,
                origin_session_id=session_record.origin_session_id,
                directory_path=session_record.directory_path,
                question=normalized_question,
                transcript_excerpt=_optional_text(transcript_excerpt),
                metadata=sanitize_voice_metadata(metadata or {}),
                deadline_at=deadline_at,
                created_at=now,
                updated_at=now,
            )
            queries[record.query_id] = record.model_dump(mode="json")
            self._atomic_write(payload)
            return record

    def get_query(self, query_id: str) -> VoiceQueryRecord | None:
        normalized = str(query_id or "").strip()
        if not normalized:
            return None
        with self._lock:
            raw = _ensure_mapping(self._read_raw(), "queries").get(normalized)
        if not isinstance(raw, dict):
            return None
        return VoiceQueryRecord.model_validate(raw)

    def get_query_for_voice_session(
        self, voice_session_id: str, query_id: str
    ) -> VoiceQueryRecord | None:
        record = self.get_query(query_id)
        if record is None or record.voice_session_id != str(voice_session_id or "").strip():
            return None
        return record

    def list_queries_for_voice_session(self, voice_session_id: str) -> list[VoiceQueryRecord]:
        normalized = str(voice_session_id or "").strip()
        if not normalized:
            return []
        with self._lock:
            raw_queries = _ensure_mapping(self._read_raw(), "queries")
            records = []
            for raw in raw_queries.values():
                if not isinstance(raw, dict):
                    continue
                try:
                    record = VoiceQueryRecord.model_validate(raw)
                except Exception:
                    continue
                if record.voice_session_id == normalized:
                    records.append(record)
            return sorted(records, key=lambda item: item.created_at)

    def mark_query_wake_scheduled(
        self,
        query_id: str,
        *,
        wake_request_id: str,
    ) -> VoiceQueryRecord:
        def update(record: VoiceQueryRecord) -> None:
            record.wake_request_id = _optional_text(wake_request_id)

        return self._mutate_query(query_id, update)

    def complete_query(
        self,
        query_id: str,
        *,
        origin_session_id: str,
        answer: str,
        status: VoiceQueryStatus = "answered",
        citations: list[str] | None = None,
        confidence: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> VoiceQueryRecord:
        normalized_answer = str(answer or "").strip()
        if not normalized_answer:
            raise ValueError("answer is required")
        if status not in {"answered", "failed"}:
            raise ValueError("status must be answered or failed")

        def update(record: VoiceQueryRecord) -> None:
            if record.origin_session_id != str(origin_session_id or "").strip():
                raise ValueError("Voice query is outside this session scope")
            if record.status not in {"pending", "timeout"}:
                raise ValueError(f"Voice query is already {record.status}")
            record.status = status
            record.answer = normalized_answer
            record.citations = [
                _normalize_list_text(item)
                for item in list(citations or [])
                if _normalize_list_text(item)
            ]
            record.confidence = _optional_text(confidence)
            record.metadata = sanitize_voice_metadata(
                {**record.metadata, **dict(metadata or {})}
            )
            record.answered_at = datetime.now(UTC)

        return self._mutate_query(query_id, update)

    def mark_query_timeout(self, query_id: str, *, error: str | None = None) -> VoiceQueryRecord:
        def update(record: VoiceQueryRecord) -> None:
            if record.status == "pending":
                record.status = "timeout"
                record.error = _optional_text(error) or "Origin agent query timed out"

        return self._mutate_query(query_id, update)

    def mark_reconciliation_scheduled(
        self,
        voice_session_id: str,
        *,
        request_id: str,
        prompt: str,
    ) -> VoiceSessionRecord:
        with self._lock:
            payload = self._read_raw()
            record = self._require_record(payload, voice_session_id)
            record.reconciliation_status = "scheduled"
            record.reconciliation_request_id = request_id
            record.reconciliation_prompt = prompt
            record.updated_at = datetime.now(UTC)
            self._put_record(payload, record)
            self._atomic_write(payload)
            return record

    def _mutate_query(
        self,
        query_id: str,
        callback: Any,
    ) -> VoiceQueryRecord:
        with self._lock:
            payload = self._read_raw()
            queries = _ensure_mapping(payload, "queries")
            raw = queries.get(str(query_id or "").strip())
            if not isinstance(raw, dict):
                raise ValueError(f"Unknown voice query: {query_id}")
            record = VoiceQueryRecord.model_validate(raw)
            callback(record)
            record.updated_at = datetime.now(UTC)
            queries[record.query_id] = record.model_dump(mode="json")
            self._atomic_write(payload)
            return record

    def mark_reconciliation_failed(self, voice_session_id: str, error: str) -> VoiceSessionRecord:
        with self._lock:
            payload = self._read_raw()
            record = self._require_record(payload, voice_session_id)
            record.reconciliation_status = "failed"
            record.error = _optional_text(error)
            record.updated_at = datetime.now(UTC)
            self._put_record(payload, record)
            self._atomic_write(payload)
            return record

    def _read_raw(self) -> dict[str, Any]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"Voice session store {self.path} contains invalid JSON") from exc
        if not isinstance(raw, dict):
            return _empty_voice_store_payload()
        raw.setdefault("sessions", {})
        raw.setdefault("request_index", {})
        raw.setdefault("bridge_index", {})
        return raw

    def _atomic_write(self, payload: dict[str, Any]) -> None:
        tmp_path = self.path.with_suffix(f"{self.path.suffix}.tmp")
        tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        tmp_path.replace(self.path)

    def _require_record(self, payload: dict[str, Any], voice_session_id: str) -> VoiceSessionRecord:
        raw = _ensure_mapping(payload, "sessions").get(voice_session_id)
        if not isinstance(raw, dict):
            raise ValueError(f"Unknown voice session: {voice_session_id}")
        return VoiceSessionRecord.model_validate(raw)

    @staticmethod
    def _put_record(payload: dict[str, Any], record: VoiceSessionRecord) -> None:
        _ensure_mapping(payload, "sessions")[record.voice_session_id] = record.model_dump(
            mode="json"
        )
        _ensure_mapping(payload, "request_index")[record.request_id] = record.voice_session_id


class VoiceProjectionService:
    @staticmethod
    def _fallback_tool_summary(session: SessionManifest) -> dict[str, Any]:
        approval_required_values = {
            tool_name_value(tool) for tool in session.approval_required_tools
        }
        allowed_tools = [
            {
                "tool_name": tool_name_value(tool),
                "source": "session.allowed_tools",
                "requires_approval": tool_name_value(tool) in approval_required_values,
            }
            for tool in session.allowed_tools
        ]
        approval_required_tools = [
            tool_name_value(tool)
            for tool in session.approval_required_tools
            if tool_name_value(tool)
        ]
        return {
            "session_id": session.session_id,
            "available_tools": allowed_tools,
            "approval_required_tools": approval_required_tools,
            "unavailable_tools": [],
            "evaluated": False,
        }

    @staticmethod
    def _normalize_tool_summary(
        session: SessionManifest, tool_summary: Mapping[str, Any] | None
    ) -> dict[str, Any]:
        if tool_summary is None:
            return VoiceProjectionService._fallback_tool_summary(session)

        normalized = dict(tool_summary)
        normalized.setdefault("session_id", session.session_id)
        normalized.setdefault("available_tools", [])
        normalized.setdefault("approval_required_tools", [])
        normalized.setdefault("unavailable_tools", [])
        normalized.setdefault("evaluated", True)
        return normalized

    @staticmethod
    def _tool_name_from_summary_entry(entry: Any) -> str | None:
        if isinstance(entry, Mapping):
            candidate = entry.get("tool_name")
            if candidate is None:
                candidate = entry.get("name")
        else:
            candidate = entry
        text = str(candidate or "").strip()
        return text or None

    @classmethod
    def _render_available_tools(cls, summary: Mapping[str, Any]) -> str:
        rendered: list[str] = []
        for entry in summary.get("available_tools", []):
            tool_name = cls._tool_name_from_summary_entry(entry)
            if not tool_name:
                continue
            if isinstance(entry, Mapping) and entry.get("requires_approval"):
                rendered.append(f"{tool_name} (approval required)")
            else:
                rendered.append(tool_name)
        return ", ".join(rendered) or "<none>"

    @classmethod
    def _render_unavailable_tools(cls, summary: Mapping[str, Any]) -> str:
        rendered: list[str] = []
        for entry in summary.get("unavailable_tools", []):
            tool_name = cls._tool_name_from_summary_entry(entry)
            if not tool_name:
                continue
            reason = ""
            if isinstance(entry, Mapping):
                reason = str(entry.get("reason") or "").strip()
            if reason:
                rendered.append(f"{tool_name} ({reason})")
            else:
                rendered.append(tool_name)
        if rendered:
            return ", ".join(rendered)
        return "<not evaluated>" if not summary.get("evaluated", False) else "<none>"

    def build_projection(
        self,
        *,
        session: SessionManifest,
        prompt: str,
        channel: VoiceSessionChannel,
        metadata: dict[str, Any] | None = None,
        tool_summary: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized_prompt = str(prompt or "").strip()
        if not normalized_prompt:
            raise ValueError("prompt is required")
        session_metadata = sanitize_voice_metadata(dict(session.metadata or {}))
        normalized_tool_summary = self._normalize_tool_summary(session, tool_summary)
        instructions = (
            "You are a live voice projection of an existing Truman text agent session. "
            "Speak as that originating agent, preserve its current task context, and do not "
            "invent unavailable state. If the caller asks for action that requires live tools, "
            "files, repository state, or memory not present in this projection, use the "
            "query_origin_agent tool when available. Speak like a natural phone participant: "
            "keep normal answers to one or two short spoken paragraphs unless the caller asks "
            "for detail, avoid repeating yourself, and pause naturally after a long answer "
            "instead of immediately asking whether the caller is still there. Treat short "
            "acknowledgements such as ok, okay, uh-huh, good, thanks, and right as "
            "backchannels unless they include a new request; after a backchannel, do not "
            "repeat the prior answer. Use the origin agent only for missing origin-side data "
            "that is not already in the projected task/context, and do not fill pending time "
            "with generic startup or context-check scaffolding. When the origin answer "
            "arrives, give the answer directly. Preserve decisions and follow-up actions "
            "internally after the call, but do not narrate internal bookkeeping, transcript "
            "cleanup, or handoff notes aloud. For outbound phone "
            "startup, treat the supplied current voice task as authoritative first-turn task "
            "state. If it includes a complete opening or objective, begin with it immediately; "
            "do not ask the origin agent for the same script, instructions, objective, "
            "expected behavior, or call context, avoid context-check or task-missing "
            "narration, and do not ask open-ended what-to-do questions. If the supplied "
            "task is not actionable enough to start and caller input is required, ask one "
            "concise caller-facing clarification question."
        )
        complete_objective = _voice_task_looks_actionable(normalized_prompt)
        context_lines = [
            f"Origin session id: {session.session_id}",
            f"Directory path: {session.directory_path}",
            f"Model: {session.model}",
            f"Available MCP tools: {self._render_available_tools(normalized_tool_summary)}",
            f"Unavailable MCP tools: {self._render_unavailable_tools(normalized_tool_summary)}",
        ]
        if session_metadata:
            context_lines.append(
                "Origin metadata: "
                f"{json.dumps(session_metadata, sort_keys=True, ensure_ascii=True)}"
            )
        return {
            "origin_session_id": session.session_id,
            "directory_path": session.directory_path,
            "channel": channel,
            "model": session.model,
            "allowed_tools": [tool_name_value(tool) for tool in session.allowed_tools],
            "tool_summary": normalized_tool_summary,
            "origin_metadata": session_metadata,
            "caller_metadata": sanitize_voice_metadata(metadata or {}),
            "task_prompt": normalized_prompt,
            "instructions": instructions,
            "context": "\n".join(context_lines),
            "startup_contract": {
                "authoritative_first_turn_state": True,
                "complete_objective": complete_objective,
                "policy": (
                    "start_from_objective"
                    if complete_objective
                    else "ask_caller_clarification"
                ),
                "forbid_origin_query_for": [
                    "same script",
                    "same instructions",
                    "same objective",
                    "expected behavior",
                    "call context already present in the prompt",
                ],
                "pending_origin_query_fallback": "silent_until_result",
            },
            "delegation_policy": {
                "tool_name": "query_origin_agent",
                "delegate_when": [
                    "caller asks for file contents or repository state",
                    "caller asks for tool output or live session state",
                    "caller asks about context not included in this projection",
                ],
                "conversation_style": (
                    "do not fill pending time with generic startup or context-check "
                    "scaffolding, and answer directly when the origin result arrives"
                ),
            },
            "prompt": (
                f"{instructions}\n\n"
                f"Originating session: {session.session_id}\n"
                f"Directory: {session.directory_path}\n"
                f"Current voice task:\n{normalized_prompt}"
            ),
        }


def _voice_task_looks_actionable(prompt: str) -> bool:
    normalized = re.sub(r"\s+", " ", str(prompt or "").strip())
    if not normalized:
        return False
    lowered = normalized.casefold().rstrip(".!?")
    if len(lowered.split()) < 5:
        return False
    task_markers = {
        "ask",
        "tell",
        "say",
        "explain",
        "summarize",
        "request",
        "confirm",
        "verify",
        "collect",
        "give",
        "open",
        "start",
        "begin",
        "discuss",
        "approve",
        "approval",
        "script",
        "objective",
        "prompt",
    }
    if any(marker in lowered.split() for marker in task_markers):
        return True
    if re.fullmatch(
        r"(?:please\s+)?(?:call|phone|contact|reach out to)\s+[\w\s'.-]+",
        lowered,
    ):
        return False
    return False


class VoiceMediaBridgeClient:
    def __init__(
        self,
        *,
        base_url: str | None,
        token_env: str | None,
        timeout_seconds: float,
        http_client: httpx.Client | None = None,
    ) -> None:
        self.base_url = _normalize_base_url(base_url)
        self.token_env = str(token_env or "").strip() or None
        self.timeout_seconds = timeout_seconds
        self._http_client = http_client

    def start_phone_call(
        self,
        *,
        voice_session_id: str,
        request_id: str,
        to_number: str,
        from_number: str | None,
        contact: dict[str, Any],
        projection: dict[str, Any],
        realtime: dict[str, Any],
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = {
            "voice_session_id": voice_session_id,
            "request_id": request_id,
            "to_number": to_number,
            "from_number": from_number,
            "contact": contact,
            "projection": projection,
            "realtime": realtime,
            "metadata": sanitize_voice_metadata(metadata or {}),
        }
        return self._request("POST", "/phone-calls", json=payload)

    def get_voice_session(self, voice_session_id: str) -> dict[str, Any]:
        return self._request("GET", f"/voice-sessions/{voice_session_id}")

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        if self.base_url is None:
            raise RuntimeError("didww_sip_bridge_url must be configured")
        headers = dict(kwargs.pop("headers", {}) or {})
        if self.token_env is not None:
            token = os.environ.get(self.token_env, "").strip()
            if not token:
                raise RuntimeError(
                    f"DIDWW SIP bridge token env var {self.token_env!r} is not set"
                )
            headers["Authorization"] = f"Bearer {token}"
        url = f"{self.base_url}{path}"
        try:
            client = self._http_client
            if client is not None:
                response = client.request(method, url, headers=headers, **kwargs)
                response.raise_for_status()
                return _json_object_response(response)
            with httpx.Client(timeout=self.timeout_seconds) as scoped_client:
                response = scoped_client.request(method, url, headers=headers, **kwargs)
                response.raise_for_status()
                return _json_object_response(response)
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code if exc.response is not None else None
            raise ProviderRequestError(
                f"voice_bridge.{method.lower()} failed with status={status_code}",
                status_code=status_code,
            ) from exc
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise ProviderRequestError(
                f"voice_bridge.{method.lower()} failed with {type(exc).__name__}: {exc}"
            ) from exc


class OpenAIRealtimePhoneCallService:
    def __init__(
        self,
        *,
        config: Any,
        contacts_config: ContactDirectoryConfig | None,
        store: VoiceSessionStore,
        bridge_client: VoiceMediaBridgeClient,
        reconciler: VoiceSessionReconciler | None = None,
        tool_summary_provider: Callable[[SessionManifest], Mapping[str, Any] | None] | None = None,
    ) -> None:
        self.config = config
        self.contacts_config = contacts_config or ContactDirectoryConfig()
        self.store = store
        self.bridge_client = bridge_client
        self.reconciler = reconciler
        self.projection_service = VoiceProjectionService()
        self.tool_summary_provider = tool_summary_provider

    def make_phone_call(
        self,
        *,
        session: SessionManifest,
        contact: str,
        prompt: str,
        request_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized_contact = str(contact or "").strip()
        if not normalized_contact:
            raise ValueError("contact is required")
        normalized_prompt = str(prompt or "").strip()
        if not normalized_prompt:
            raise ValueError("prompt is required")
        validate_allowed_contact_ids_for_directory(
            self.contacts_config,
            session.allowed_contact_ids,
            session_id=session.session_id,
            directory_path=session.directory_path,
        )
        normalized_contact = require_allowed_contact_id(
            normalized_contact,
            session.allowed_contact_ids,
            session_id=session.session_id,
        )
        contact_entry, phone_number = self._resolve_contact(normalized_contact)
        tool_summary = None
        if self.tool_summary_provider is not None:
            try:
                tool_summary = self.tool_summary_provider(session)
            except Exception:
                tool_summary = None
        projection = self.projection_service.build_projection(
            session=session,
            prompt=normalized_prompt,
            channel="phone",
            metadata=metadata or {},
            tool_summary=tool_summary,
        )
        prompt_metadata = _prompt_metadata(normalized_prompt)
        record, created = self.store.prepare_session(
            origin_session_id=session.session_id,
            directory_path=session.directory_path,
            channel="phone",
            request_id=request_id,
            prompt_metadata=prompt_metadata,
            projection=projection,
            caller_metadata=metadata or {},
            contact_id=normalized_contact,
            contact_display_name=contact_entry.name,
        )
        if not created and record.bridge_session_id:
            return voice_phone_call_started_payload(record)

        try:
            bridge_payload = self.bridge_client.start_phone_call(
                voice_session_id=record.voice_session_id,
                request_id=record.request_id,
                to_number=phone_number.number,
                from_number=getattr(self.config, "didww_from_number", None),
                contact={"id": normalized_contact, "name": contact_entry.name},
                projection=projection,
                realtime=realtime_session_config(self.config),
                metadata={
                    "origin_session_id": session.session_id,
                    "directory_path": session.directory_path,
                    "request_id": record.request_id,
                },
            )
        except Exception as exc:
            self.store.complete_session(
                record.voice_session_id,
                status="failed",
                error=str(exc),
            )
            raise

        record = self.store.attach_bridge_session(
            record.voice_session_id,
            bridge_session_id=_bridge_session_id_from_payload(bridge_payload),
            status=str(bridge_payload.get("status") or "connecting"),
            bridge_metadata=bridge_payload,
            realtime_metadata=bridge_payload.get("realtime")
            if isinstance(bridge_payload.get("realtime"), dict)
            else {},
        )
        return voice_phone_call_started_payload(record)

    def get_phone_call_result(
        self,
        *,
        session: SessionManifest,
        request_id: str | None = None,
        call_id: str | None = None,
    ) -> dict[str, Any]:
        record = self._find_record(request_id=request_id, call_id=call_id)
        self._require_record_owned_by_session(record, session)
        if record.status not in {"ended", "failed"}:
            try:
                bridge_payload = self.bridge_client.get_voice_session(record.voice_session_id)
            except RuntimeError:
                bridge_payload = {}
            if bridge_payload:
                record = self.store.update_from_bridge_payload(
                    record.voice_session_id,
                    bridge_payload,
                )
        return voice_phone_call_result_payload(record)

    def _find_record(
        self,
        *,
        request_id: str | None = None,
        call_id: str | None = None,
    ) -> VoiceSessionRecord:
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
            by_call_id = (
                self.store.get_by_voice_session_id(normalized_call_id)
                or self.store.get_by_bridge_session_id(normalized_call_id)
            )
            if record is not None and by_call_id is not None and by_call_id.request_id != record.request_id:
                raise ValueError("request_id and call_id refer to different phone calls")
            record = record or by_call_id
        if record is None:
            raise ValueError("Unknown phone call request")
        if record.channel != "phone":
            raise ValueError("Voice session is not a phone call")
        return record

    @staticmethod
    def _require_record_owned_by_session(
        record: VoiceSessionRecord,
        session: SessionManifest,
    ) -> None:
        if record.origin_session_id != session.session_id or record.directory_path != session.directory_path:
            raise ValueError("Phone call request is outside this session scope")

    def _resolve_contact(
        self, alias: str
    ) -> tuple[DirectoryContactEntry, DirectoryContactPhoneNumber]:
        contact = self.contacts_config.contacts.get(alias)
        if contact is None:
            raise ValueError(f"Unknown contact id for phone call: {alias}")
        primary_numbers = [phone for phone in contact.phone_numbers if phone.primary]
        if not primary_numbers:
            raise ValueError(f"Contact {alias!r} does not have a primary phone number configured")
        if len(primary_numbers) > 1:
            raise ValueError(f"Contact {alias!r} has more than one primary phone number configured")
        return contact, primary_numbers[0]


class PhoneCallRouterService:
    def __init__(
        self,
        *,
        backend: str,
        retell_service: RetellPhoneCallService,
        realtime_service: OpenAIRealtimePhoneCallService,
        voice_store: VoiceSessionStore,
    ) -> None:
        self.backend = backend
        self.retell_service = retell_service
        self.realtime_service = realtime_service
        self.voice_store = voice_store

    def make_phone_call(
        self,
        *,
        session: SessionManifest,
        contact: str,
        prompt: str,
        request_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if self.backend == "openai_realtime_didww":
            return self.realtime_service.make_phone_call(
                session=session,
                contact=contact,
                prompt=prompt,
                request_id=request_id,
                metadata=metadata,
            )
        record = self.retell_service.make_phone_call(
            session=session,
            contact=contact,
            prompt=prompt,
            request_id=request_id,
            metadata=metadata,
        )
        return {
            "request_id": record.request_id,
            "call_id": record.call_id,
            "contact": record.contact_alias,
            "status": record.status or "registered",
        }

    def get_phone_call_result(
        self,
        *,
        session: SessionManifest,
        request_id: str | None = None,
        call_id: str | None = None,
    ) -> dict[str, Any]:
        if self._voice_record_exists(request_id=request_id, call_id=call_id):
            return self.realtime_service.get_phone_call_result(
                session=session,
                request_id=request_id,
                call_id=call_id,
            )
        return self.retell_service.get_phone_call_result(
            session=session,
            request_id=request_id,
            call_id=call_id,
        )

    def _voice_record_exists(self, *, request_id: str | None, call_id: str | None) -> bool:
        if request_id and self.voice_store.get_by_request_id(request_id):
            return True
        if call_id and (
            self.voice_store.get_by_voice_session_id(call_id)
            or self.voice_store.get_by_bridge_session_id(call_id)
        ):
            return True
        return False


class VoiceSessionReconciler:
    def __init__(
        self,
        *,
        store: VoiceSessionStore,
        wake_store: WakeRequestRepository,
    ) -> None:
        self.store = store
        self.wake_store = wake_store

    def schedule_reconciliation(self, voice_session_id: str) -> VoiceSessionRecord | None:
        record = self.store.get_by_voice_session_id(voice_session_id)
        if record is None:
            return None
        if record.status not in {"ended", "failed"}:
            return record
        if record.reconciliation_status == "scheduled":
            return record
        prompt = build_reconciliation_prompt(
            record,
            queries=self.store.list_queries_for_voice_session(record.voice_session_id),
        )
        request_id = f"voice-session-reconcile:{record.voice_session_id}"
        try:
            self.wake_store.schedule_resume_request(
                session_id=record.origin_session_id,
                prompt=prompt,
                schedule=WakeScheduleDefinition(resume_at=datetime.now(UTC)),
                request_id=request_id,
                source="voice_session_reconciliation",
                resume_mode="same_context",
                enabled=True,
                metadata={
                    "voice_session_id": record.voice_session_id,
                    "channel": record.channel,
                    "request_id": record.request_id,
                },
            )
        except ValueError as exc:
            if "already exists" not in str(exc):
                return self.store.mark_reconciliation_failed(record.voice_session_id, str(exc))
        return self.store.mark_reconciliation_scheduled(
            record.voice_session_id,
            request_id=request_id,
            prompt=prompt,
        )


class VoiceQueryDelegator:
    def __init__(
        self,
        *,
        store: VoiceSessionStore,
        wake_store: WakeRequestRepository,
    ) -> None:
        self.store = store
        self.wake_store = wake_store

    def create_query_and_wake_origin(
        self,
        voice_session_id: str,
        *,
        question: str,
        transcript_excerpt: str | None = None,
        metadata: dict[str, Any] | None = None,
        deadline_seconds: int | None = None,
    ) -> VoiceQueryRecord:
        query = self.store.create_query(
            voice_session_id,
            question=question,
            transcript_excerpt=transcript_excerpt,
            metadata=metadata or {},
            deadline_seconds=deadline_seconds,
        )
        prompt = build_voice_query_prompt(query)
        request_id = f"voice-query:{query.query_id}"
        try:
            self.wake_store.schedule_resume_request(
                session_id=query.origin_session_id,
                prompt=prompt,
                schedule=WakeScheduleDefinition(resume_at=datetime.now(UTC)),
                request_id=request_id,
                source="voice_session_query",
                resume_mode="same_context",
                enabled=True,
                metadata={
                    "voice_session_id": query.voice_session_id,
                    "query_id": query.query_id,
                },
            )
        except ValueError as exc:
            if "already exists" not in str(exc):
                return self.store.complete_query(
                    query.query_id,
                    origin_session_id=query.origin_session_id,
                    answer=str(exc),
                    status="failed",
                )
        return self.store.mark_query_wake_scheduled(
            query.query_id,
            wake_request_id=request_id,
        )


def disable_pending_voice_query_wake(
    *,
    query: VoiceQueryRecord,
    wake_store: WakeRequestRepository,
) -> bool:
    request_id = str(query.wake_request_id or f"voice-query:{query.query_id}").strip()
    if not request_id:
        return False
    request = wake_store.get_request(request_id)
    if request is None:
        return False
    if request.session_id != query.origin_session_id:
        return False
    if request.source != "voice_session_query":
        return False
    if not request.enabled:
        return False
    latest_dispatch = wake_store.latest_dispatch_by_request_id().get(request_id)
    if latest_dispatch is not None and latest_dispatch.status == "dispatched":
        return False
    request.enabled = False
    request.next_run_at = None
    request.updated_at = datetime.now(UTC)
    wake_store.upsert_request(request)
    return True


def realtime_session_config(config: Any) -> dict[str, Any]:
    return {
        "provider": "openai_realtime",
        "model": getattr(config, "openai_realtime_model", "gpt-realtime-2"),
        "media_controller_url": getattr(config, "voice_media_controller_url", None),
        "media_controller_token_env": getattr(
            config, "voice_media_controller_token_env", None
        ),
        "media_controller_request_timeout_seconds": getattr(
            config, "voice_media_controller_request_timeout_seconds", None
        ),
    }


def voice_phone_call_started_payload(record: VoiceSessionRecord) -> dict[str, Any]:
    return {
        "request_id": record.request_id,
        "call_id": record.bridge_session_id or record.voice_session_id,
        "contact": record.contact_id or "",
        "status": record.status,
    }


def voice_phone_call_result_payload(record: VoiceSessionRecord) -> dict[str, Any]:
    payload = {
        "request_id": record.request_id,
        "call_id": record.bridge_session_id or record.voice_session_id,
        "voice_session_id": record.voice_session_id,
        "contact": record.contact_id,
        "contact_display_name": record.contact_display_name,
        "status": record.status,
        "transcript": record.transcript,
        "transcript_events": [
            event.model_dump(mode="json") for event in record.transcript_events
        ],
        "summary": record.summary,
        "decisions": list(record.decisions),
        "action_items": list(record.action_items),
        "reconciliation_status": record.reconciliation_status,
        "reconciliation_request_id": record.reconciliation_request_id,
        "metadata": {
            "channel": record.channel,
            "bridge": sanitize_voice_metadata(record.bridge_metadata),
            "realtime": sanitize_voice_metadata(record.realtime_metadata),
        },
        "error": record.error,
    }
    bridge_audio_quality = record.bridge_metadata.get("audio_quality")
    if isinstance(bridge_audio_quality, dict):
        payload["audio_quality"] = sanitize_voice_metadata(bridge_audio_quality)
    return payload


def build_reconciliation_prompt(
    record: VoiceSessionRecord,
    *,
    queries: list[VoiceQueryRecord] | None = None,
) -> str:
    transcript = record.transcript or _transcript_from_events(record.transcript_events) or "<empty>"
    summary = record.summary or "<none>"
    decisions = "\n".join(f"- {item}" for item in record.decisions) or "- <none>"
    action_items = "\n".join(f"- {item}" for item in record.action_items) or "- <none>"
    delegated_queries = build_delegated_query_reconciliation_text(queries or [])
    return (
        "A live voice session that was projected from this Truman agent has ended. "
        "Incorporate the call or meeting into your current context, honor any explicit "
        "decisions, and continue from the user's latest intent.\n\n"
        f"Voice session id: {record.voice_session_id}\n"
        f"Channel: {record.channel}\n"
        f"Status: {record.status}\n"
        f"Summary: {summary}\n\n"
        f"Decisions:\n{decisions}\n\n"
        f"Action items:\n{action_items}\n\n"
        f"Delegated origin-agent queries:\n{delegated_queries}\n\n"
        f"Transcript:\n{transcript}"
    )


def build_voice_query_prompt(query: VoiceQueryRecord) -> str:
    transcript_excerpt = query.transcript_excerpt or "<none>"
    return (
        "A live phone/meeting voice projection needs help from this originating Truman agent. "
        "Use your normal context, files, and tools as needed, then answer the caller-facing "
        "`submit_voice_query_response` tool with the query id below.\n\n"
        f"Voice session id: {query.voice_session_id}\n"
        f"Voice query id: {query.query_id}\n"
        f"Caller question:\n{query.question}\n\n"
        f"Current call transcript excerpt:\n{transcript_excerpt}\n\n"
        "Answer concisely for a live conversation. If you cannot answer safely, submit a failed "
        "response explaining what is missing."
    )


def build_delegated_query_reconciliation_text(queries: list[VoiceQueryRecord]) -> str:
    if not queries:
        return "- <none>"
    lines: list[str] = []
    for query in queries:
        answer = query.answer or query.error or "<unresolved>"
        lines.append(
            f"- {query.query_id} [{query.status}] question={query.question!r} answer={answer!r}"
        )
    return "\n".join(lines)


def voice_query_payload(record: VoiceQueryRecord) -> dict[str, Any]:
    return {
        "query_id": record.query_id,
        "voice_session_id": record.voice_session_id,
        "origin_session_id": record.origin_session_id,
        "directory_path": record.directory_path,
        "question": record.question,
        "transcript_excerpt": record.transcript_excerpt,
        "status": record.status,
        "answer": record.answer,
        "citations": list(record.citations),
        "confidence": record.confidence,
        "error": record.error,
        "metadata": sanitize_voice_metadata(record.metadata),
        "wake_request_id": record.wake_request_id,
        "created_at": record.created_at.isoformat(),
        "updated_at": record.updated_at.isoformat(),
        "deadline_at": record.deadline_at.isoformat() if record.deadline_at else None,
        "answered_at": record.answered_at.isoformat() if record.answered_at else None,
    }


def sanitize_voice_metadata(value: Any) -> Any:
    return sanitize_retell_metadata(_redact_voice_value(value))


def normalize_voice_session_status(
    value: object,
    *,
    default: VoiceSessionStatus = "active",
) -> VoiceSessionStatus:
    text = str(value or "").strip().lower()
    if text in {"created", "new"}:
        return "created"
    if text in {"connecting", "queued", "registered", "ringing", "dialing"}:
        return "connecting"
    if text in {"active", "connected", "in_progress", "speaking"}:
        return "active"
    if text in {"ended", "complete", "completed", "stopped", "left", "hangup", "hung_up"}:
        return "ended"
    if text in {"failed", "error", "rejected", "busy", "no_answer"}:
        return "failed"
    return default


def _apply_bridge_payload(record: VoiceSessionRecord, payload: dict[str, Any]) -> None:
    now = datetime.now(UTC)
    record.status = normalize_voice_session_status(payload.get("status"), default=record.status)
    record.bridge_session_id = (
        _bridge_session_id_from_payload(payload) or record.bridge_session_id
    )
    record.bridge_metadata = sanitize_voice_metadata({**record.bridge_metadata, **payload})
    raw_realtime = payload.get("realtime")
    if isinstance(raw_realtime, dict):
        record.realtime_metadata = sanitize_voice_metadata(
            {**record.realtime_metadata, **raw_realtime}
        )
    transcript = payload.get("transcript")
    if isinstance(transcript, str) and transcript.strip():
        record.transcript = transcript.strip()
    raw_events = payload.get("transcript_events")
    if isinstance(raw_events, list):
        existing = len(record.transcript_events)
        existing_sequences = {event.sequence for event in record.transcript_events}
        for item in raw_events:
            if not isinstance(item, dict):
                continue
            text = str(item.get("text") or "").strip()
            if not text:
                continue
            sequence = item.get("sequence")
            if not isinstance(sequence, int) or sequence < 1:
                sequence = existing + 1
                existing += 1
            elif sequence in existing_sequences:
                continue
            record.transcript_events.append(
                VoiceTranscriptEvent(
                    sequence=sequence,
                    text=text,
                    speaker=_optional_text(item.get("speaker")),
                    metadata=sanitize_voice_metadata(item.get("metadata") or {}),
                )
            )
            existing_sequences.add(sequence)
        if record.transcript is None:
            record.transcript = _transcript_from_events(record.transcript_events)
    summary = payload.get("summary")
    if isinstance(summary, str) and summary.strip():
        record.summary = summary.strip()
    decisions = payload.get("decisions")
    if isinstance(decisions, list):
        record.decisions = [_normalize_list_text(item) for item in decisions if _normalize_list_text(item)]
    action_items = payload.get("action_items")
    if isinstance(action_items, list):
        record.action_items = [
            _normalize_list_text(item) for item in action_items if _normalize_list_text(item)
        ]
    error = payload.get("error")
    if isinstance(error, str) and error.strip():
        record.error = error.strip()
    record.updated_at = now
    if record.status in {"ended", "failed"} and record.ended_at is None:
        record.ended_at = now


def _bridge_session_id_from_payload(payload: dict[str, Any]) -> str | None:
    for key in ("bridge_session_id", "call_id", "meeting_id", "session_id"):
        value = _optional_text(payload.get(key))
        if value:
            return value
    return None


def _prompt_metadata(prompt: str) -> dict[str, Any]:
    import hashlib

    normalized = str(prompt or "").strip()
    return {
        "sha256": hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
        "length": len(normalized),
    }


def _empty_voice_store_payload() -> dict[str, Any]:
    return {"sessions": {}, "request_index": {}, "bridge_index": {}, "queries": {}}


def _ensure_mapping(payload: dict[str, Any], key: str) -> dict[str, Any]:
    value = payload.setdefault(key, {})
    if not isinstance(value, dict):
        payload[key] = {}
        return payload[key]
    return value


def _json_object_response(response: httpx.Response) -> dict[str, Any]:
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("voice bridge response must be a JSON object")
    return payload


def _normalize_base_url(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip().rstrip("/")
    return normalized or None


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _normalize_list_text(value: object) -> str:
    return str(value or "").strip()


def _redact_voice_value(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for raw_key, raw_value in value.items():
            key = str(raw_key)
            normalized = key.lower()
            if any(
                marker in normalized
                for marker in ("phone", "number", "token", "authorization", "secret", "api_key")
            ):
                redacted[key] = "<redacted>"
            else:
                redacted[key] = _redact_voice_value(raw_value)
        return redacted
    if isinstance(value, list):
        return [_redact_voice_value(item) for item in value]
    return value


def _transcript_from_events(events: list[VoiceTranscriptEvent]) -> str | None:
    lines: list[str] = []
    for event in sorted(events, key=lambda item: item.sequence):
        prefix = f"{event.speaker}: " if event.speaker else ""
        lines.append(f"{prefix}{event.text}")
    return "\n".join(lines) if lines else None
