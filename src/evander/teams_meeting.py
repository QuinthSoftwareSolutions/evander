from __future__ import annotations

import hashlib
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote
from uuid import uuid4

import httpx
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator

from evander.directories import DirectoryService
from evander.models import DirectoryPath, SessionManifest, ToolName, utc_now
from evander.storage import JsonObjectStore
from evander.voice_sessions import (
    VoiceProjectionService,
    realtime_session_config,
    sanitize_voice_metadata,
)

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


class TeamsMeetingBridgeClient:
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

    def join(
        self,
        *,
        join_url: str,
        external_meeting_id: str,
        default_voice: str | None = None,
        metadata: dict[str, Any] | None = None,
        realtime: dict[str, Any] | None = None,
        projection: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = {
            "join_url": join_url,
            "external_meeting_id": external_meeting_id,
            "default_voice": default_voice,
            "metadata": dict(metadata or {}),
        }
        if realtime is not None:
            payload["realtime"] = sanitize_voice_metadata(realtime)
        if projection is not None:
            payload["projection"] = sanitize_voice_metadata(projection)
        return self._request("POST", "/meetings/join", json=payload)

    def get_events(
        self, *, meeting_id: str, after_sequence: int = 0
    ) -> list[TeamsMeetingBridgeEvent]:
        payload = self._request(
            "GET",
            f"/meetings/{quote(meeting_id, safe='')}/events",
            params={"after": after_sequence},
        )
        raw_events = payload.get("events") if isinstance(payload, dict) else payload
        if not isinstance(raw_events, list):
            raise ValueError("media bridge events response must contain an events list")
        return [
            TeamsMeetingBridgeEvent.model_validate(raw_event)
            for raw_event in raw_events
            if isinstance(raw_event, dict)
        ]

    def speak(
        self,
        *,
        meeting_id: str,
        text: str,
        voice: str | None,
        interrupt: bool,
    ) -> dict[str, Any]:
        payload = {"text": text, "voice": voice, "interrupt": interrupt}
        return self._request(
            "POST",
            f"/meetings/{quote(meeting_id, safe='')}/speak",
            json=payload,
        )

    def leave(self, *, meeting_id: str) -> dict[str, Any]:
        return self._request("POST", f"/meetings/{quote(meeting_id, safe='')}/leave")

    def get_call(self, voice_session_id: str) -> dict[str, Any]:
        return self._request("GET", f"/calls/{quote(voice_session_id, safe='')}")

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        if self.base_url is None:
            raise RuntimeError("teams_meeting_media_bridge_url must be configured")
        headers = dict(kwargs.pop("headers", {}) or {})
        if self.token_env is not None:
            token = os.environ.get(self.token_env, "").strip()
            if not token:
                raise RuntimeError(
                    f"Teams meeting media bridge token env var {self.token_env!r} is not set"
                )
            headers["Authorization"] = f"Bearer {token}"
        url = f"{self.base_url}{path}"
        client = self._http_client
        if client is not None:
            response = client.request(method, url, headers=headers, **kwargs)
            response.raise_for_status()
            return _json_object_response(response)
        with httpx.Client(timeout=self.timeout_seconds) as scoped_client:
            response = scoped_client.request(method, url, headers=headers, **kwargs)
            response.raise_for_status()
            return _json_object_response(response)


class TeamsMeetingService:
    def __init__(
        self,
        *,
        config: Any,
        runtime: Any,
        store: TeamsMeetingStore,
        bridge_client: TeamsMeetingBridgeClient,
    ) -> None:
        self.config = config
        self.runtime = runtime
        self.store = store
        self.bridge_client = bridge_client

    def start_meeting(
        self,
        *,
        directory_path: str,
        join_url: str,
        model: str | None,
        session_id: str | None = None,
        ttl_hours: int | None = None,
        origin_session_id: str | None = None,
        prompt: str | None = None,
    ) -> TeamsMeetingStartResult:
        normalized_join_url = str(join_url or "").strip()
        if not normalized_join_url:
            raise ValueError("join_url is required")
        max_active = int(getattr(self.config, "teams_meeting_max_active_meetings", 1))
        active_bindings = self.store.list_active_bindings()
        if len(active_bindings) >= max_active:
            active_ids = ", ".join(binding.meeting_id for binding in active_bindings)
            raise ValueError(
                f"Teams meeting MVP allows {max_active} active meeting(s); active={active_ids}"
            )

        directory_service = DirectoryService(self.runtime.directories)
        normalized_directory = directory_service.show(directory_path or "root").directory.path
        policy = self.runtime.policy_config.resolve_for_directory(normalized_directory)
        resolved_session_id = (
            str(session_id or "").strip()
            or f"teams-meeting-{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{uuid4().hex[:8]}"
        )
        if self.runtime.session_repository.get_session(resolved_session_id) is not None:
            raise ValueError(f"Session already exists: {resolved_session_id}")

        external_meeting_id = f"meeting-{uuid4().hex}"
        default_voice = str(getattr(self.config, "teams_meeting_default_voice", "") or "").strip()
        origin_session = self._resolve_origin_session(origin_session_id)
        voice_record = None
        voice_projection: dict[str, Any] | None = None
        if origin_session is not None:
            voice_prompt = (
                str(prompt or "").strip()
                or "Join this Teams meeting as a projected live voice session for the originating Evander agent."
            )
            tool_summary = None
            tool_policy = getattr(self.runtime, "policy", None)
            if tool_policy is not None:
                try:
                    tool_summary = tool_policy.allowed_tool_summary_for_session(origin_session)
                except Exception:
                    tool_summary = None
            voice_projection = VoiceProjectionService().build_projection(
                session=origin_session,
                prompt=voice_prompt,
                channel="teams_meeting",
                metadata={
                    "meeting_session_id": resolved_session_id,
                    "join_url_sha256": _sha256(normalized_join_url),
                },
                tool_summary=tool_summary,
            )
            voice_record, _created = self.runtime.voice_session_store.prepare_session(
                origin_session_id=origin_session.session_id,
                directory_path=origin_session.directory_path,
                channel="teams_meeting",
                request_id=f"teams-meeting:{resolved_session_id}",
                prompt_metadata={
                    "sha256": _sha256(voice_prompt),
                    "length": len(voice_prompt),
                },
                projection=voice_projection,
                caller_metadata={
                    "meeting_session_id": resolved_session_id,
                    "join_url_sha256": _sha256(normalized_join_url),
                },
            )
        bridge_metadata = {
            "session_id": resolved_session_id,
            "directory_path": normalized_directory,
        }
        if origin_session is not None and voice_record is not None:
            bridge_metadata.update(
                {
                    "origin_session_id": origin_session.session_id,
                    "voice_session_id": voice_record.voice_session_id,
                }
            )
        try:
            bridge_response = self.bridge_client.join(
                join_url=normalized_join_url,
                external_meeting_id=external_meeting_id,
                default_voice=default_voice or None,
                metadata=bridge_metadata,
                realtime=realtime_session_config(self.config) if voice_record is not None else None,
                projection=voice_projection,
            )
        except Exception as exc:
            if voice_record is not None:
                self.runtime.voice_session_store.complete_session(
                    voice_record.voice_session_id,
                    status="failed",
                    error=str(exc),
                )
            raise
        meeting_id = str(bridge_response.get("meeting_id") or external_meeting_id).strip()
        if not meeting_id:
            raise ValueError("media bridge join response did not include a meeting_id")
        if voice_record is not None:
            voice_record = self.runtime.voice_session_store.attach_bridge_session(
                voice_record.voice_session_id,
                bridge_session_id=meeting_id,
                status=str(bridge_response.get("status") or "active"),
                bridge_metadata=_redact_bridge_response(bridge_response),
                realtime_metadata=bridge_response.get("realtime")
                if isinstance(bridge_response.get("realtime"), dict)
                else {},
                meeting_id=meeting_id,
            )

        ttl_seconds = (
            int(ttl_hours) * 3600
            if ttl_hours is not None
            else int(policy.default_session_ttl_seconds)
        )
        manifest = SessionManifest(
            session_id=resolved_session_id,
            directory_path=normalized_directory,
            allowed_tools=[
                ToolName.TEAMS_MEETING_GET_EVENTS,
                ToolName.TEAMS_MEETING_SPEAK,
                ToolName.TEAMS_MEETING_STOP,
            ],
            email_mailbox_allowlist=policy.email_mailbox_allowlist,
            email_folder_allowlist_by_mailbox=policy.email_folder_allowlist_by_mailbox,
            email_draft_recipient_allowlist=policy.email_draft_recipient_allowlist,
            email_draft_domain_allowlist=policy.email_draft_domain_allowlist,
            email_send_enabled=policy.email_send_enabled,
            expires_at=datetime.now(UTC) + timedelta(seconds=ttl_seconds),
            model=str(model or "").strip() or None,
            metadata={
                "teams_meeting": {
                    "meeting_id": meeting_id,
                    "status": "active",
                    "started_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                    "join_url_sha256": _sha256(normalized_join_url),
                    **(
                        {
                            "origin_session_id": origin_session.session_id,
                            "voice_session_id": voice_record.voice_session_id,
                        }
                        if origin_session is not None and voice_record is not None
                        else {}
                    ),
                }
            },
        )
        binding = TeamsMeetingBinding(
            meeting_id=meeting_id,
            session_id=resolved_session_id,
            directory_path=normalized_directory,
            join_url_sha256=_sha256(normalized_join_url),
            default_voice=default_voice or None,
            metadata={
                "bridge": _redact_bridge_response(bridge_response),
                **(
                    {
                        "origin_session_id": origin_session.session_id,
                        "voice_session_id": voice_record.voice_session_id,
                    }
                    if origin_session is not None and voice_record is not None
                    else {}
                ),
            },
        )
        self.runtime.session_repository.upsert_session(manifest)
        self.store.upsert_binding(binding)
        self.store.append_local_event(
            meeting_id=meeting_id,
            event_type="meeting_status",
            speech_status="joined",
            metadata={"bridge": _redact_bridge_response(bridge_response)},
        )
        return TeamsMeetingStartResult(
            meeting_id=meeting_id,
            session_id=resolved_session_id,
            directory_path=normalized_directory,
            status="active",
            model=manifest.model,
            bridge=_redact_bridge_response(bridge_response),
        )

    def stop_meeting(self, *, session_id: str) -> dict[str, Any]:
        binding = self.store.get_binding_for_session(session_id)
        if binding is None:
            raise ValueError(f"Unknown Teams meeting session: {session_id}")
        response = self.bridge_client.leave(meeting_id=binding.meeting_id)
        self.store.close_binding(binding.meeting_id, status="stopped")
        self.store.append_local_event(
            meeting_id=binding.meeting_id,
            event_type="meeting_status",
            speech_status="stopped",
            metadata={"bridge": _redact_bridge_response(response)},
        )
        self._complete_bound_voice_session(binding)
        manifest = self.runtime.session_repository.get_session(session_id)
        if manifest is not None:
            metadata = dict(manifest.metadata or {})
            teams_metadata = dict(metadata.get("teams_meeting") or {})
            teams_metadata["status"] = "stopped"
            teams_metadata["stopped_at"] = datetime.now(UTC).isoformat().replace("+00:00", "Z")
            metadata["teams_meeting"] = teams_metadata
            self.runtime.session_repository.upsert_session(
                manifest.model_copy(update={"metadata": metadata})
            )
        return {"status": "stopped", "meeting_id": binding.meeting_id, "bridge": response}

    def _resolve_origin_session(self, origin_session_id: str | None) -> SessionManifest | None:
        normalized = str(origin_session_id or "").strip()
        if not normalized:
            return None
        session = self.runtime.session_repository.get_session(normalized)
        if session is None:
            raise ValueError(f"Origin session does not exist: {normalized}")
        return session

    def _complete_bound_voice_session(self, binding: TeamsMeetingBinding) -> None:
        voice_session_id = str(binding.metadata.get("voice_session_id") or "").strip()
        if not voice_session_id or not hasattr(self.runtime, "voice_session_store"):
            return
        events = self.store.list_events_for_session(
            binding.session_id, since_sequence=0, limit=10_000
        )
        transcript = voice_session_transcript_from_teams_events(events)
        self.runtime.voice_session_store.complete_session(
            voice_session_id,
            status="ended",
            transcript=transcript,
            summary="Teams meeting ended; review the transcript for decisions and follow-up.",
        )


def build_teams_meeting_bridge_client(config: Any) -> TeamsMeetingBridgeClient:
    return TeamsMeetingBridgeClient(
        base_url=getattr(config, "teams_meeting_media_bridge_url", None),
        token_env=getattr(config, "teams_meeting_media_bridge_token_env", None),
        timeout_seconds=float(getattr(config, "teams_meeting_request_timeout_seconds", 15.0)),
    )


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
