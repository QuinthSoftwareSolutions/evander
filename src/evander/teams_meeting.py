from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import httpx
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator

from evander.models import DirectoryPath, utc_now
from evander.storage import JsonObjectStore

MeetingBindingStatus = Literal["active", "stopped", "failed"]
MeetingEventType = Literal["transcript", "speech_status", "error", "meeting_status"]


class TeamsMeetingBridgeEvent(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    sequence: int = Field(ge=1, validation_alias=AliasChoices("sequence", "seq"))
    event_type: str = Field(validation_alias=AliasChoices("event_type", "type"))
    occurred_at: datetime = Field(default_factory=utc_now)
    text: str | None = None
    speech_operation_id: str | None = None
    speech_status: str | None = None
    error: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("event_type", mode="before")
    @classmethod
    def normalize_event_type(cls, value: object) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("event_type is required")
        return text

    @field_validator("text", "speech_operation_id", "speech_status", "error", mode="before")
    @classmethod
    def normalize_optional_strings(cls, value: object) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None


class TeamsMeetingEvent(BaseModel):
    meeting_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    sequence: int = Field(ge=1)
    event_type: MeetingEventType
    occurred_at: datetime
    received_at: datetime = Field(default_factory=utc_now)
    text: str | None = None
    speech_operation_id: str | None = None
    speech_status: str | None = None
    bridge_sequence: int | None = Field(default=None, ge=1)
    metadata: dict[str, Any] = Field(default_factory=dict)


class TeamsMeetingBinding(BaseModel):
    meeting_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    directory_path: str = "root"
    status: MeetingBindingStatus = "active"
    join_url_sha256: str | None = None
    default_voice: str | None = None
    last_bridge_sequence: int = Field(default=0, ge=0)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    stopped_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("directory_path")
    @classmethod
    def validate_directory_path(cls, value: str) -> str:
        return DirectoryPath(segments=value).as_posix()


class TeamsMeetingStartResult(BaseModel):
    meeting_id: str
    session_id: str
    directory_path: str
    status: MeetingBindingStatus
    model: str | None = None
    bridge: dict[str, Any] = Field(default_factory=dict)


class TeamsMeetingStore(JsonObjectStore):
    """Durable meeting state with process-shared transaction locks."""

    def __init__(self, path: Path) -> None:
        super().__init__(Path(path), _empty_store_payload())

    def upsert_binding(self, binding: TeamsMeetingBinding) -> None:
        with self._lock, self._file_lock():
            payload = self._read_raw()
            bindings = _ensure_mapping(payload, "bindings")
            bindings[binding.meeting_id] = binding.model_dump(mode="json")
            _ensure_list(payload, "events", binding.meeting_id)
            self._atomic_write(payload)

    def get_binding_for_session(self, session_id: str) -> TeamsMeetingBinding | None:
        normalized = str(session_id or "").strip()
        if not normalized:
            return None
        for binding in self.list_bindings():
            if binding.session_id == normalized:
                return binding
        return None

    def get_binding(self, meeting_id: str) -> TeamsMeetingBinding | None:
        normalized = str(meeting_id or "").strip()
        if not normalized:
            return None
        with self._lock, self._file_lock():
            raw = _ensure_mapping(self._read_raw(), "bindings").get(normalized)
        if not isinstance(raw, dict):
            return None
        return TeamsMeetingBinding.model_validate(raw)

    def list_bindings(self) -> list[TeamsMeetingBinding]:
        with self._lock, self._file_lock():
            raw_bindings = dict(_ensure_mapping(self._read_raw(), "bindings"))
        bindings: list[TeamsMeetingBinding] = []
        for raw in raw_bindings.values():
            if not isinstance(raw, dict):
                continue
            bindings.append(TeamsMeetingBinding.model_validate(raw))
        bindings.sort(key=lambda item: (item.created_at, item.meeting_id))
        return bindings

    def list_active_bindings(self) -> list[TeamsMeetingBinding]:
        return [binding for binding in self.list_bindings() if binding.status == "active"]

    def close_binding(self, meeting_id: str, *, status: MeetingBindingStatus = "stopped") -> None:
        if status not in {"stopped", "failed"}:
            raise ValueError("closed meeting status must be stopped or failed")
        with self._lock, self._file_lock():
            payload = self._read_raw()
            bindings = _ensure_mapping(payload, "bindings")
            raw = bindings.get(meeting_id)
            if not isinstance(raw, dict):
                raise ValueError(f"Unknown Teams meeting: {meeting_id}")
            binding = TeamsMeetingBinding.model_validate(raw)
            now = datetime.now(UTC)
            binding.status = status
            binding.updated_at = now
            binding.stopped_at = now
            bindings[meeting_id] = binding.model_dump(mode="json")
            self._atomic_write(payload)

    def append_local_event(
        self,
        *,
        meeting_id: str,
        event_type: MeetingEventType,
        text: str | None = None,
        speech_operation_id: str | None = None,
        speech_status: str | None = None,
        occurred_at: datetime | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> TeamsMeetingEvent:
        with self._lock, self._file_lock():
            payload = self._read_raw()
            binding = self._require_binding(payload, meeting_id)
            event = TeamsMeetingEvent(
                meeting_id=meeting_id,
                session_id=binding.session_id,
                sequence=_next_host_sequence(payload, meeting_id),
                event_type=event_type,
                occurred_at=occurred_at or datetime.now(UTC),
                text=_normalize_optional_text(text),
                speech_operation_id=_normalize_optional_text(speech_operation_id),
                speech_status=_normalize_optional_text(speech_status),
                metadata=dict(metadata or {}),
            )
            _ensure_list(payload, "events", meeting_id).append(event.model_dump(mode="json"))
            self._atomic_write(payload)
            return event

    def append_bridge_events(
        self,
        *,
        meeting_id: str,
        bridge_events: list[TeamsMeetingBridgeEvent],
    ) -> list[TeamsMeetingEvent]:
        if not bridge_events:
            return []
        with self._lock, self._file_lock():
            payload = self._read_raw()
            binding = self._require_binding(payload, meeting_id)
            raw_events = _ensure_list(payload, "events", meeting_id)
            seen_bridge_sequences = {
                int(raw_event["bridge_sequence"])
                for raw_event in raw_events
                if isinstance(raw_event, dict) and isinstance(raw_event.get("bridge_sequence"), int)
            }
            appended: list[TeamsMeetingEvent] = []
            for bridge_event in sorted(bridge_events, key=lambda item: item.sequence):
                if bridge_event.sequence in seen_bridge_sequences:
                    binding.last_bridge_sequence = max(
                        binding.last_bridge_sequence, bridge_event.sequence
                    )
                    continue
                event = TeamsMeetingEvent(
                    meeting_id=meeting_id,
                    session_id=binding.session_id,
                    sequence=_next_host_sequence(payload, meeting_id),
                    event_type=_normalize_bridge_event_type(bridge_event.event_type),
                    occurred_at=bridge_event.occurred_at.astimezone(UTC),
                    text=_bridge_event_text(bridge_event),
                    speech_operation_id=bridge_event.speech_operation_id,
                    speech_status=bridge_event.speech_status,
                    bridge_sequence=bridge_event.sequence,
                    metadata={
                        **dict(bridge_event.metadata or {}),
                        "bridge_event_type": bridge_event.event_type,
                    },
                )
                raw_events.append(event.model_dump(mode="json"))
                appended.append(event)
                seen_bridge_sequences.add(bridge_event.sequence)
                binding.last_bridge_sequence = max(
                    binding.last_bridge_sequence, bridge_event.sequence
                )
            binding.updated_at = datetime.now(UTC)
            _ensure_mapping(payload, "bindings")[meeting_id] = binding.model_dump(mode="json")
            self._atomic_write(payload)
            return appended

    def list_events_for_session(
        self,
        session_id: str,
        *,
        since_sequence: int | None = None,
        limit: int = 50,
    ) -> list[TeamsMeetingEvent]:
        binding = self.get_binding_for_session(session_id)
        if binding is None:
            raise ValueError(f"Unknown Teams meeting session: {session_id}")
        with self._lock, self._file_lock():
            raw_events = list(_ensure_list(self._read_raw(), "events", binding.meeting_id))
        events = [
            TeamsMeetingEvent.model_validate(raw) for raw in raw_events if isinstance(raw, dict)
        ]
        events.sort(key=lambda item: item.sequence)
        if since_sequence is not None:
            events = [event for event in events if event.sequence > since_sequence]
        return events[:limit]

    def max_event_sequence(self, meeting_id: str) -> int:
        """Return the latest durable event sequence for one meeting."""
        normalized = str(meeting_id or "").strip()
        if not normalized:
            raise ValueError("meeting_id is required")
        with self._lock, self._file_lock():
            raw_events = list(_ensure_list(self._read_raw(), "events", normalized))
        return max(
            (
                int(raw_event["sequence"])
                for raw_event in raw_events
                if isinstance(raw_event, dict)
                and isinstance(raw_event.get("sequence"), int)
                and int(raw_event["sequence"]) >= 0
            ),
            default=0,
        )

    def _require_binding(self, payload: dict[str, Any], meeting_id: str) -> TeamsMeetingBinding:
        raw = _ensure_mapping(payload, "bindings").get(meeting_id)
        if not isinstance(raw, dict):
            raise ValueError(f"Unknown Teams meeting: {meeting_id}")
        return TeamsMeetingBinding.model_validate(raw)

    def _read_raw(self) -> dict[str, Any]:
        raw = self._read_object_unlocked()
        for section in ("bindings", "events", "next_sequences"):
            if not isinstance(raw.setdefault(section, {}), dict):
                raise ValueError(f"Teams meeting store has an invalid {section} section")
        if any(not isinstance(events, list) for events in raw["events"].values()):
            raise ValueError("Teams meeting store contains an invalid event list")
        return raw

    def _atomic_write(self, payload: dict[str, Any]) -> None:
        self._write_object_unlocked(payload)


def _empty_store_payload() -> dict[str, Any]:
    return {"bindings": {}, "events": {}, "next_sequences": {}}


def _ensure_mapping(payload: dict[str, Any], key: str) -> dict[str, Any]:
    value = payload.setdefault(key, {})
    if not isinstance(value, dict):
        payload[key] = {}
        return payload[key]
    return value


def _ensure_list(payload: dict[str, Any], key: str, nested_key: str) -> list[dict[str, Any]]:
    container = _ensure_mapping(payload, key)
    value = container.setdefault(nested_key, [])
    if not isinstance(value, list):
        container[nested_key] = []
        return container[nested_key]
    return value


def _next_host_sequence(payload: dict[str, Any], meeting_id: str) -> int:
    next_sequences = _ensure_mapping(payload, "next_sequences")
    raw_next = next_sequences.get(meeting_id)
    if isinstance(raw_next, int) and raw_next >= 1:
        sequence = raw_next
    else:
        existing = _ensure_list(payload, "events", meeting_id)
        sequence = 1 + max(
            (int(item.get("sequence", 0)) for item in existing if isinstance(item, dict)),
            default=0,
        )
    next_sequences[meeting_id] = sequence + 1
    return sequence


def _normalize_bridge_event_type(value: str) -> MeetingEventType:
    normalized = str(value or "").strip().lower().replace(".", "_").replace("-", "_")
    if normalized in {"transcript", "transcript_final", "final_transcript"}:
        return "transcript"
    if normalized in {"speech_status", "speech", "tts_status"}:
        return "speech_status"
    if normalized in {"meeting_status", "meeting"}:
        return "meeting_status"
    return "error" if normalized == "error" else "error"


def _bridge_event_text(event: TeamsMeetingBridgeEvent) -> str | None:
    if event.text:
        return event.text
    if event.error:
        return event.error
    return None


def voice_session_transcript_entries_from_teams_events(
    events: list[TeamsMeetingEvent],
) -> list[tuple[TeamsMeetingEvent, str | None, str]]:
    entries: list[tuple[TeamsMeetingEvent, str | None, str]] = []
    for event in sorted(events, key=lambda item: item.sequence):
        text = _normalize_optional_text(event.text)
        if text is None:
            continue
        if event.event_type == "transcript":
            entries.append((event, None, text))
            continue
        if event.event_type == "speech_status" and _is_completed_speech_status(event):
            entries.append((event, "assistant", text))
    return entries


def voice_session_transcript_from_teams_events(events: list[TeamsMeetingEvent]) -> str | None:
    lines: list[str] = []
    for _event, speaker, text in voice_session_transcript_entries_from_teams_events(events):
        prefix = f"{speaker}: " if speaker else ""
        lines.append(f"{prefix}{text}")
    return "\n".join(lines) if lines else None


def _is_completed_speech_status(event: TeamsMeetingEvent) -> bool:
    return str(event.speech_status or "").strip().lower() == "completed"


def _normalize_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _normalize_base_url(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip().rstrip("/")
    return normalized or None


def _json_object_response(response: httpx.Response) -> dict[str, Any]:
    if response.status_code == 204 or not response.content:
        return {}
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("media bridge response must be a JSON object")
    return payload


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _redact_bridge_response(response: dict[str, Any]) -> dict[str, Any]:
    redacted: dict[str, Any] = {}
    for key, value in response.items():
        if key.lower() in {"join_url", "token", "authorization"}:
            redacted[key] = "<redacted>"
        else:
            redacted[key] = value
    return redacted


def build_teams_meeting_bridge_client(config: Any):
    from evander.optional_integrations import (
        UnavailableTeamsGraphService,
        teams_graph_enabled,
        require_teams_graph_module,
    )

    if not teams_graph_enabled(config):
        return UnavailableTeamsGraphService(config)
    return require_teams_graph_module(
        "evander_teams_graph_addon.meetings", config
    ).build_teams_meeting_bridge_client(config)


def __getattr__(name: str):
    if name in {"TeamsMeetingBridgeClient", "TeamsMeetingService"}:
        from evander.optional_integrations import require_teams_graph_module

        return getattr(require_teams_graph_module("evander_teams_graph_addon.meetings"), name)
    raise AttributeError(name)
