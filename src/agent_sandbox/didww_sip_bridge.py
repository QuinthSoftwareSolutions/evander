from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import inspect
import json
import logging
import os
import re
import struct
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from typing import Any, Literal
from urllib.parse import urlencode
from uuid import uuid4

import httpx
import uvicorn
from pydantic import BaseModel, Field, field_validator, model_validator
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from agent_sandbox.bootstrap import resolve_runtime_dir
from agent_sandbox.models import utc_now
from agent_sandbox.voice_sessions import (
    VoiceTranscriptEvent,
    normalize_voice_session_status,
    sanitize_voice_metadata,
)

logger = logging.getLogger(__name__)

BridgeCallStatus = Literal["created", "connecting", "active", "ended", "failed"]

_RTP_STATIC_AUDIO_FORMATS = {
    "alaw": {"openai_type": "audio/pcma", "payload_type": 8, "sample_rate_hz": 8000},
    "ulaw": {"openai_type": "audio/pcmu", "payload_type": 0, "sample_rate_hz": 8000},
}

_FAILED_HANGUP_CAUSE_CODES = {
    1,
    2,
    3,
    17,
    18,
    19,
    20,
    21,
    22,
    27,
    28,
    34,
    38,
    41,
    42,
    44,
    47,
    57,
    58,
    63,
    88,
    102,
}
_FAILED_HANGUP_CAUSE_TEXTS = {
    "allocated_channel_not_available",
    "bearer_capability_not_authorized",
    "bearer_capability_not_available",
    "busy",
    "call_rejected",
    "congestion",
    "destination_out_of_order",
    "incompatible_destination",
    "invalid_number_format",
    "network_out_of_order",
    "no_answer",
    "no_answer_from_user",
    "no_route_to_destination",
    "no_route_to_specified_transit_network",
    "no_user_responding",
    "normal_circuit_congestion",
    "number_changed",
    "recovery_on_timer_expiry",
    "requested_channel_not_available",
    "resource_unavailable",
    "service_or_option_not_available",
    "subscriber_absent",
    "switching_equipment_congestion",
    "temporary_failure",
    "unallocated_number",
    "user_busy",
}

_DEBUG_SNIPPET_LIMIT = 200
_ORIGIN_QUERY_ACK_GRACE_SECONDS = 2.0
_BRIDGE_IDLE_PROMPT_DELAY_SECONDS = 30.0
_ASSISTANT_PLAYBACK_DRAIN_SECONDS = 0.5
_IDLE_PROMPT_RESPONSE_KEY = "bridge_idle_prompt"
_IDLE_PROMPT_INSTRUCTIONS = "Are you still there?"
_END_CALL_TOOL_NAME = "end_call"
_PROJECTED_TASK_RESPONSE_KEY = "projection:calling_task"
_PROJECTED_TASK_RESPONSE_INSTRUCTIONS = (
    "Answer the caller from the projected calling task now. "
    "Do not say you are checking internally."
)
_MISSING_TASK_CLARIFICATION_RESPONSE_KEY = "projection:missing_task_clarification"
_MISSING_TASK_CLARIFICATION_INSTRUCTIONS = (
    "Ask exactly one concise caller-facing clarification question about the call objective."
)
_CALLER_TURN_COMPLETION_RESPONSE_KEY_PREFIX = "bridge:caller_turn_completed"
_CALLER_TURN_COMPLETION_INSTRUCTIONS = (
    "Briefly acknowledge or answer the caller's latest turn. "
    "Do not use or mention the queued lookup result yet."
)
_ORIGIN_QUERY_ANSWER_RESPONSE_INSTRUCTIONS = (
    "Start with a natural transition such as 'I heard back on the earlier question.' "
    "Then briefly answer from the lookup result. Do not mention internal agent handoffs, "
    "do not say the query is still pending, and do not say that you are still checking."
)
_ORIGIN_QUERY_HOLD_RESPONSE_INSTRUCTIONS = (
    "Tell the caller in one short sentence that you will look that up for them. "
    "Then say, in one short sentence, that you will wait for the answer; if they want "
    "you to keep discussing something relevant while you wait, they can say so. "
    "Do not expose internal handoffs, delegated-query details, or technical underpinnings. "
    "Do not introduce unrelated filler or a new topic unless the caller asks for it."
)
_DEFAULT_BACKCHANNEL_PHRASES = (
    "ok",
    "okay",
    "uh-huh",
    "mhm",
    "yeah",
    "yes",
    "good",
    "great",
    "thanks",
    "thank you",
)


def _end_call_tool_definition() -> dict[str, Any]:
    return {
        "type": "function",
        "name": _END_CALL_TOOL_NAME,
        "description": (
            "End the phone call after a natural goodbye or closing when the caller has no "
            "further request."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "description": "Optional caller-facing reason for ending the call.",
                }
            },
        },
    }


class DidwwSipBridgeConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = Field(default=11888, ge=1, le=65535)
    runtime_dir: Path = Field(default_factory=lambda: Path("runtime"))
    bridge_token_env: str = Field(default="DIDWW_SIP_BRIDGE_TOKEN", min_length=1)
    enable_inbound_phone_calls: bool = False
    inbound_gateway_url: str | None = None
    inbound_gateway_token_env: str = Field(
        default="VOICE_MEDIA_CONTROLLER_TOKEN", min_length=1
    )
    openai_api_key_env: str = Field(default="OPENAI_API_KEY", min_length=1)
    openai_realtime_model: str = Field(default="gpt-realtime-2", min_length=1)
    openai_realtime_api_base_url: str = "https://api.openai.com/v1"
    openai_realtime_voice: str = "marin"
    openai_realtime_idle_timeout_ms: int = Field(default=10000, ge=5000, le=30000)
    openai_realtime_transcription_model: str | None = "gpt-4o-transcribe"
    openai_realtime_input_noise_reduction_type: Literal[
        "near_field",
        "far_field",
        "disabled",
    ] = "near_field"
    openai_realtime_turn_detection_type: Literal["server_vad", "semantic_vad"] = "server_vad"
    openai_realtime_vad_threshold: float = Field(default=0.70, ge=0.0, le=1.0)
    openai_realtime_vad_prefix_padding_ms: int = Field(default=300, ge=0, le=5000)
    openai_realtime_vad_silence_duration_ms: int = Field(default=800, ge=0, le=5000)
    asterisk_ari_url: str = "http://127.0.0.1:8088/ari"
    asterisk_ari_user: str = "agent-sandbox"
    asterisk_ari_password_env: str = Field(default="ASTERISK_ARI_PASSWORD", min_length=1)
    asterisk_ari_app: str = "agent_sandbox_didww"
    asterisk_pjsip_endpoint: str = "didww"
    asterisk_endpoint_template: str = "PJSIP/{number}@{trunk}"
    external_media_host: str = "127.0.0.1"
    rtp_bind_host: str = "127.0.0.1"
    rtp_port_min: int = Field(default=41000, ge=1, le=65535)
    rtp_port_max: int = Field(default=41100, ge=1, le=65535)
    rtp_payload_type: int = Field(default=8, ge=0, le=127)
    rtp_ssrc: int = Field(default=0x41534757, ge=0, le=0xFFFFFFFF)
    media_format: str = "alaw"
    sample_rate_hz: int = Field(default=8000, ge=8000)
    frame_duration_ms: int = Field(default=20, ge=10, le=60)
    call_timeout_seconds: float = Field(default=45.0, gt=0)
    max_call_seconds: float = Field(default=1800.0, gt=0)
    request_timeout_seconds: float = Field(default=15.0, gt=0)
    max_concurrent_calls: int = Field(default=4, ge=1)
    voice_query_poll_interval_seconds: float = Field(default=1.0, gt=0)
    voice_query_timeout_seconds: float = Field(default=90.0, gt=0)
    barge_in_delay_ms: int = Field(default=700, ge=0, le=5000)
    backchannel_max_words: int = Field(default=3, ge=1, le=12)
    backchannel_phrases: list[str] = Field(
        default_factory=lambda: list(_DEFAULT_BACKCHANNEL_PHRASES)
    )

    @field_validator(
        "openai_realtime_api_base_url",
        "asterisk_ari_url",
        "inbound_gateway_url",
        mode="before",
    )
    @classmethod
    def normalize_base_url(cls, value: object) -> object:
        return str(value or "").strip().rstrip("/")

    @field_validator("media_format", mode="before")
    @classmethod
    def normalize_media_format(cls, value: object) -> object:
        return str(value or "").strip().lower()

    @field_validator("backchannel_phrases", mode="before")
    @classmethod
    def normalize_backchannel_phrases(cls, value: object) -> object:
        if isinstance(value, str):
            parts = value.split(",")
        elif isinstance(value, (list, tuple, set)):
            parts = list(value)
        else:
            parts = []
        normalized = [
            _normalize_backchannel_phrase(part)
            for part in parts
            if _normalize_backchannel_phrase(part)
        ]
        return normalized or list(_DEFAULT_BACKCHANNEL_PHRASES)

    @field_validator(
        "openai_realtime_input_noise_reduction_type",
        "openai_realtime_turn_detection_type",
        mode="before",
    )
    @classmethod
    def normalize_openai_realtime_modes(cls, value: object, info: Any) -> object:
        text = str(value or "").strip().lower()
        if info.field_name == "openai_realtime_input_noise_reduction_type" and text in {
            "none",
            "disabled",
        }:
            return "disabled"
        return text

    @field_validator("media_format")
    @classmethod
    def validate_media_format(cls, value: str) -> str:
        if value not in _RTP_STATIC_AUDIO_FORMATS:
            raise ValueError(
                "Asterisk ExternalMedia RTP bridge requires static G.711 "
                "media_format='alaw' or 'ulaw'"
            )
        return value

    @model_validator(mode="after")
    def validate_static_rtp_audio(self) -> DidwwSipBridgeConfig:
        audio_format = _RTP_STATIC_AUDIO_FORMATS[self.media_format]
        expected_payload_type = int(audio_format["payload_type"])
        if self.rtp_payload_type != expected_payload_type:
            raise ValueError(
                f"media_format={self.media_format!r} requires "
                f"rtp_payload_type={expected_payload_type}"
            )
        expected_sample_rate = int(audio_format["sample_rate_hz"])
        if self.sample_rate_hz != expected_sample_rate:
            raise ValueError(
                f"media_format={self.media_format!r} requires "
                f"sample_rate_hz={expected_sample_rate}"
            )
        return self

    @field_validator("rtp_port_max")
    @classmethod
    def validate_port_range(cls, value: int, info: Any) -> int:
        minimum = info.data.get("rtp_port_min")
        if isinstance(minimum, int) and value < minimum:
            raise ValueError("rtp_port_max must be greater than or equal to rtp_port_min")
        return value

    @property
    def store_path(self) -> Path:
        return self.runtime_dir / "didww-sip-bridge-store.json"


class PhoneCallStartRequest(BaseModel):
    voice_session_id: str = Field(min_length=1)
    request_id: str = Field(min_length=1)
    to_number: str = Field(min_length=1)
    from_number: str | None = None
    contact: dict[str, Any] = Field(default_factory=dict)
    projection: dict[str, Any] = Field(default_factory=dict)
    realtime: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("voice_session_id", "request_id", "to_number", "from_number", mode="before")
    @classmethod
    def normalize_text(cls, value: object) -> object:
        if value is None:
            return None
        text = str(value).strip()
        return text or None


class InboundPhoneCallStartRequest(BaseModel):
    voice_session_id: str = Field(min_length=1)
    request_id: str = Field(min_length=1)
    gateway_voice_session_id: str = Field(min_length=1)
    route_id: str = Field(min_length=1)
    asterisk_channel_id: str = Field(min_length=1)
    bridge_call_id: str = Field(min_length=1)
    projection: dict[str, Any] = Field(default_factory=dict)
    realtime: dict[str, Any] = Field(default_factory=dict)
    route: dict[str, Any] = Field(default_factory=dict)
    inbound_instructions: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    did_redacted: str | None = None
    caller_redacted: str | None = None

    @field_validator(
        "voice_session_id",
        "request_id",
        "gateway_voice_session_id",
        "route_id",
        "asterisk_channel_id",
        "bridge_call_id",
        "did_redacted",
        "caller_redacted",
        mode="before",
    )
    @classmethod
    def normalize_optional_text(cls, value: object) -> object:
        if value is None:
            return None
        text = str(value).strip()
        return text or None


class DidwwBridgeDebugEvent(BaseModel):
    sequence: int = Field(ge=1)
    event_type: str = Field(min_length=1)
    occurred_at: datetime = Field(default_factory=utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("event_type", mode="before")
    @classmethod
    def normalize_event_type(cls, value: object) -> object:
        text = str(value or "").strip()
        return text or None


class DidwwBridgeCallRecord(BaseModel):
    voice_session_id: str = Field(min_length=1)
    request_id: str = Field(min_length=1)
    call_id: str = Field(min_length=1)
    direction: Literal["outbound", "inbound"] = "outbound"
    route_id: str | None = None
    gateway_voice_session_id: str | None = None
    status: BridgeCallStatus = "created"
    contact: dict[str, Any] = Field(default_factory=dict)
    projection: dict[str, Any] = Field(default_factory=dict)
    realtime: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    to_number_redacted: str | None = None
    from_number_redacted: str | None = None
    did_redacted: str | None = None
    caller_redacted: str | None = None
    ari_channel_id: str | None = None
    ari_bridge_id: str | None = None
    ari_external_media_channel_id: str | None = None
    rtp_port: int | None = None
    transcript_events: list[VoiceTranscriptEvent] = Field(default_factory=list)
    debug_events: list[DidwwBridgeDebugEvent] = Field(default_factory=list)
    transcript: str | None = None
    summary: str | None = None
    decisions: list[str] = Field(default_factory=list)
    action_items: list[str] = Field(default_factory=list)
    error: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    ended_at: datetime | None = None

    @field_validator(
        "voice_session_id",
        "request_id",
        "call_id",
        "direction",
        "route_id",
        "gateway_voice_session_id",
        "to_number_redacted",
        "from_number_redacted",
        "did_redacted",
        "caller_redacted",
        "ari_channel_id",
        "ari_bridge_id",
        "ari_external_media_channel_id",
        "transcript",
        "summary",
        "error",
        mode="before",
    )
    @classmethod
    def normalize_optional_text(cls, value: object) -> object:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    def response_payload(self) -> dict[str, Any]:
        return {
            "voice_session_id": self.voice_session_id,
            "request_id": self.request_id,
            "call_id": self.call_id,
            "direction": self.direction,
            "route_id": self.route_id,
            "gateway_voice_session_id": self.gateway_voice_session_id,
            "status": self.status,
            "transcript_events": [
                event.model_dump(mode="json") for event in self.transcript_events
            ],
            "debug_events": [
                event.model_dump(mode="json") for event in self.debug_events
            ],
            "transcript": self.transcript,
            "summary": self.summary,
            "decisions": list(self.decisions),
            "action_items": list(self.action_items),
            "realtime": sanitize_voice_metadata(self.realtime),
            "metadata": {
                "contact": sanitize_voice_metadata(self.contact),
                "to_number": self.to_number_redacted,
                "from_number": self.from_number_redacted,
                "did": self.did_redacted,
                "caller": self.caller_redacted,
                "ari_channel_id": self.ari_channel_id,
                "ari_bridge_id": self.ari_bridge_id,
                "ari_external_media_channel_id": self.ari_external_media_channel_id,
                "rtp_port": self.rtp_port,
                "route_id": self.route_id,
                "gateway_voice_session_id": self.gateway_voice_session_id,
            },
            "error": self.error,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "ended_at": self.ended_at.isoformat() if self.ended_at else None,
        }


class DidwwBridgeStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._atomic_write({"calls": {}, "request_index": {}})

    def create_call(self, payload: PhoneCallStartRequest) -> tuple[DidwwBridgeCallRecord, bool]:
        with self._lock:
            raw = self._read_raw()
            request_index = _mapping(raw, "request_index")
            existing_id = request_index.get(payload.request_id)
            if isinstance(existing_id, str):
                return self._require(raw, existing_id), False
            call_id = f"didww-{uuid4().hex}"
            record = DidwwBridgeCallRecord(
                voice_session_id=payload.voice_session_id,
                request_id=payload.request_id,
                call_id=call_id,
                status="connecting",
                contact=sanitize_voice_metadata(payload.contact),
                projection=sanitize_voice_metadata(payload.projection),
                realtime=sanitize_voice_metadata(payload.realtime),
                metadata=sanitize_voice_metadata(payload.metadata),
                to_number_redacted=redact_phone_number(payload.to_number),
                from_number_redacted=redact_phone_number(payload.from_number),
            )
            _mapping(raw, "calls")[record.voice_session_id] = record.model_dump(mode="json")
            request_index[record.request_id] = record.voice_session_id
            self._atomic_write(raw)
            return record, True

    def create_inbound_call(
        self,
        payload: InboundPhoneCallStartRequest,
    ) -> tuple[DidwwBridgeCallRecord, bool]:
        with self._lock:
            raw = self._read_raw()
            request_index = _mapping(raw, "request_index")
            existing_id = request_index.get(payload.request_id)
            if isinstance(existing_id, str):
                return self._require(raw, existing_id), False
            call_id = f"inbound-{_sanitize_call_id(payload.bridge_call_id)}"
            record = DidwwBridgeCallRecord(
                voice_session_id=payload.voice_session_id,
                request_id=payload.request_id,
                call_id=call_id,
                direction="inbound",
                route_id=payload.route_id,
                gateway_voice_session_id=payload.gateway_voice_session_id,
                status="connecting",
                contact={
                    "id": payload.route_id,
                    "name": payload.route_id,
                    "direction": "inbound",
                },
                projection=sanitize_voice_metadata(payload.projection),
                realtime=sanitize_voice_metadata(payload.realtime),
                metadata=sanitize_voice_metadata(payload.metadata),
                did_redacted=payload.did_redacted,
                caller_redacted=payload.caller_redacted,
                to_number_redacted=payload.did_redacted,
                from_number_redacted=payload.caller_redacted,
            )
            _mapping(raw, "calls")[record.voice_session_id] = record.model_dump(mode="json")
            request_index[record.request_id] = record.voice_session_id
            self._atomic_write(raw)
            return record, True

    def get(self, voice_session_id: str) -> DidwwBridgeCallRecord | None:
        normalized = str(voice_session_id or "").strip()
        if not normalized:
            return None
        with self._lock:
            raw = _mapping(self._read_raw(), "calls").get(normalized)
        if not isinstance(raw, dict):
            return None
        return DidwwBridgeCallRecord.model_validate(raw)

    def attach_asterisk(
        self,
        voice_session_id: str,
        *,
        ari_channel_id: str,
        ari_bridge_id: str,
        ari_external_media_channel_id: str,
        rtp_port: int,
    ) -> DidwwBridgeCallRecord:
        def mutate(record: DidwwBridgeCallRecord) -> None:
            record.ari_channel_id = ari_channel_id
            record.ari_bridge_id = ari_bridge_id
            record.ari_external_media_channel_id = ari_external_media_channel_id
            record.rtp_port = rtp_port

        return self._mutate(voice_session_id, mutate)

    def update_status(
        self,
        voice_session_id: str,
        status: str,
        *,
        error: str | None = None,
    ) -> DidwwBridgeCallRecord:
        def mutate(record: DidwwBridgeCallRecord) -> None:
            record.status = normalize_voice_session_status(status, default=record.status)
            if error is not None:
                record.error = str(error)
            if record.status in {"ended", "failed"} and record.ended_at is None:
                record.ended_at = datetime.now(UTC)

        return self._mutate(voice_session_id, mutate)

    def append_transcript_event(
        self,
        voice_session_id: str,
        *,
        text: str,
        speaker: str,
        metadata: dict[str, Any] | None = None,
    ) -> DidwwBridgeCallRecord | None:
        normalized_text = str(text or "").strip()
        if not normalized_text:
            return self.get(voice_session_id)

        def mutate(record: DidwwBridgeCallRecord) -> None:
            record.transcript_events.append(
                VoiceTranscriptEvent(
                    sequence=len(record.transcript_events) + 1,
                    speaker=speaker,
                    text=normalized_text,
                    metadata=sanitize_voice_metadata(metadata or {}),
                )
            )
            record.transcript = transcript_from_events(record.transcript_events)

        return self._mutate(voice_session_id, mutate)

    def append_debug_event(
        self,
        voice_session_id: str,
        event_type: str,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> DidwwBridgeCallRecord | None:
        normalized_event_type = str(event_type or "").strip()
        if not normalized_event_type:
            return self.get(voice_session_id)

        occurred_at = datetime.now(UTC)
        sanitized_metadata = _sanitize_debug_metadata(metadata or {})

        def mutate(record: DidwwBridgeCallRecord) -> None:
            record.debug_events.append(
                DidwwBridgeDebugEvent(
                    sequence=len(record.debug_events) + 1,
                    event_type=normalized_event_type,
                    occurred_at=occurred_at,
                    metadata=sanitized_metadata,
                )
            )

        record = self._mutate(voice_session_id, mutate)
        _log_debug_event(record, normalized_event_type, sanitized_metadata)
        return record

    def complete(
        self,
        voice_session_id: str,
        *,
        status: str = "ended",
        summary: str | None = None,
        decisions: list[str] | None = None,
        action_items: list[str] | None = None,
        error: str | None = None,
    ) -> DidwwBridgeCallRecord:
        def mutate(record: DidwwBridgeCallRecord) -> None:
            record.status = normalize_voice_session_status(status, default="ended")
            sanitized_summary = _debug_snippet(summary)
            if sanitized_summary is not None:
                record.summary = sanitized_summary
            if decisions is not None:
                record.decisions = [_normalize_list_item(item) for item in decisions if _normalize_list_item(item)]
            if action_items is not None:
                record.action_items = [
                    _normalize_list_item(item) for item in action_items if _normalize_list_item(item)
                ]
            if error is not None:
                record.error = _debug_snippet(error)
            record.ended_at = record.ended_at or datetime.now(UTC)

        return self._mutate(voice_session_id, mutate)

    def _mutate(
        self,
        voice_session_id: str,
        callback: Callable[[DidwwBridgeCallRecord], None],
    ) -> DidwwBridgeCallRecord:
        with self._lock:
            raw = self._read_raw()
            record = self._require(raw, voice_session_id)
            callback(record)
            record.updated_at = datetime.now(UTC)
            _mapping(raw, "calls")[record.voice_session_id] = record.model_dump(mode="json")
            self._atomic_write(raw)
            return record

    def _require(self, raw: dict[str, Any], voice_session_id: str) -> DidwwBridgeCallRecord:
        record = _mapping(raw, "calls").get(voice_session_id)
        if not isinstance(record, dict):
            raise ValueError(f"Unknown DIDWW bridge voice session: {voice_session_id}")
        return DidwwBridgeCallRecord.model_validate(record)

    def _read_raw(self) -> dict[str, Any]:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        _mapping(payload, "calls")
        _mapping(payload, "request_index")
        return payload

    def _atomic_write(self, payload: dict[str, Any]) -> None:
        tmp = self.path.with_suffix(f"{self.path.suffix}.tmp")
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(self.path)


@dataclass(frozen=True)
class AsteriskCallHandles:
    call_channel_id: str
    bridge_id: str
    external_media_channel_id: str
    external_media_local_address: str | None = None
    external_media_local_port: int | None = None

    @property
    def external_media_local_addr(self) -> tuple[str, int] | None:
        if not self.external_media_local_address or self.external_media_local_port is None:
            return None
        return (self.external_media_local_address, self.external_media_local_port)


class AsteriskAriClient:
    def __init__(
        self,
        config: DidwwSipBridgeConfig,
        *,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.config = config
        self._http_client = http_client

    async def start_external_media_call(
        self,
        *,
        call_id: str,
        to_number: str,
        from_number: str | None,
        external_host: str,
        event_websocket: Any | None = None,
    ) -> AsteriskCallHandles:
        call_channel_id = _ari_id("call", call_id)
        bridge_id = _ari_id("bridge", call_id)
        external_media_channel_id = _ari_id("media", call_id)
        endpoint = self._endpoint_for_number(to_number)

        try:
            await self._request(
                "POST",
                f"/bridges/{bridge_id}",
                params={"type": "mixing", "name": bridge_id},
            )
            await self._request(
                "POST",
                f"/channels/{call_channel_id}",
                params={
                    "endpoint": endpoint,
                    "app": self.config.asterisk_ari_app,
                    "appArgs": call_id,
                    "callerId": from_number or "",
                    "timeout": int(self.config.call_timeout_seconds),
                },
            )
            external_media = await self._request(
                "POST",
                "/channels/externalMedia",
                params={
                    "channelId": external_media_channel_id,
                    "app": self.config.asterisk_ari_app,
                    "appArgs": call_id,
                    "external_host": external_host,
                    "format": self.config.media_format,
                    "direction": "both",
                },
            )
            external_media_local_addr = _unicast_rtp_local_addr(external_media)
            if event_websocket is not None:
                await self._wait_for_stasis_channels(
                    event_websocket,
                    {call_channel_id, external_media_channel_id},
                    timeout_seconds=self.config.call_timeout_seconds,
                )
            await self._request(
                "POST",
                f"/bridges/{bridge_id}/addChannel",
                params={"channel": call_channel_id},
            )
            await self._request(
                "POST",
                f"/bridges/{bridge_id}/addChannel",
                params={"channel": external_media_channel_id},
            )
        except Exception:
            await self.hangup_channel(external_media_channel_id)
            await self.hangup_channel(call_channel_id)
            await self.destroy_bridge(bridge_id)
            raise
        return AsteriskCallHandles(
            call_channel_id=call_channel_id,
            bridge_id=bridge_id,
            external_media_channel_id=external_media_channel_id,
            external_media_local_address=(
                external_media_local_addr[0] if external_media_local_addr is not None else None
            ),
            external_media_local_port=(
                external_media_local_addr[1] if external_media_local_addr is not None else None
            ),
        )

    async def start_external_media_for_inbound(
        self,
        *,
        call_id: str,
        inbound_channel_id: str,
        external_host: str,
        event_websocket: Any | None = None,
    ) -> AsteriskCallHandles:
        bridge_id = _ari_id("bridge", call_id)
        external_media_channel_id = _ari_id("media", call_id)

        try:
            await self._request(
                "POST",
                f"/bridges/{bridge_id}",
                params={"type": "mixing", "name": bridge_id},
            )
            await self.answer_channel(inbound_channel_id)
            external_media = await self._request(
                "POST",
                "/channels/externalMedia",
                params={
                    "channelId": external_media_channel_id,
                    "app": self.config.asterisk_ari_app,
                    "appArgs": call_id,
                    "external_host": external_host,
                    "format": self.config.media_format,
                    "direction": "both",
                },
            )
            external_media_local_addr = _unicast_rtp_local_addr(external_media)
            if event_websocket is not None:
                await self._wait_for_stasis_channels(
                    event_websocket,
                    {external_media_channel_id},
                    timeout_seconds=self.config.call_timeout_seconds,
                )
            await self._request(
                "POST",
                f"/bridges/{bridge_id}/addChannel",
                params={"channel": inbound_channel_id},
            )
            await self._request(
                "POST",
                f"/bridges/{bridge_id}/addChannel",
                params={"channel": external_media_channel_id},
            )
        except Exception:
            await self.hangup_channel(external_media_channel_id)
            await self.hangup_channel(inbound_channel_id)
            await self.destroy_bridge(bridge_id)
            raise
        return AsteriskCallHandles(
            call_channel_id=inbound_channel_id,
            bridge_id=bridge_id,
            external_media_channel_id=external_media_channel_id,
            external_media_local_address=(
                external_media_local_addr[0] if external_media_local_addr is not None else None
            ),
            external_media_local_port=(
                external_media_local_addr[1] if external_media_local_addr is not None else None
            ),
        )

    async def hangup_channel(self, channel_id: str | None) -> None:
        if not channel_id:
            return
        try:
            await self._request("DELETE", f"/channels/{channel_id}")
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 404:
                raise

    async def answer_channel(self, channel_id: str | None) -> None:
        if not channel_id:
            return
        try:
            await self._request("POST", f"/channels/{channel_id}/answer")
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 404:
                raise

    async def destroy_bridge(self, bridge_id: str | None) -> None:
        if not bridge_id:
            return
        try:
            await self._request("DELETE", f"/bridges/{bridge_id}")
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 404:
                raise

    @asynccontextmanager
    async def event_stream(self) -> AsyncIterator[Any]:
        try:
            import websockets
        except ModuleNotFoundError as exc:  # pragma: no cover - dependency exists in project
            raise RuntimeError("Asterisk ARI event streaming requires websockets") from exc

        headers = {"Authorization": f"Basic {self._basic_auth_token()}"}
        async with websockets.connect(
            self._websocket_events_url(),
            additional_headers=headers,
            open_timeout=self.config.request_timeout_seconds,
            close_timeout=self.config.request_timeout_seconds,
        ) as websocket:
            yield websocket

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        client = self._http_client
        if client is not None:
            response = await client.request(
                method,
                f"{self.config.asterisk_ari_url}{path}",
                params=params,
                auth=self._http_auth(),
            )
            response.raise_for_status()
            return _json_response(response)
        async with httpx.AsyncClient(timeout=self.config.request_timeout_seconds) as scoped:
            response = await scoped.request(
                method,
                f"{self.config.asterisk_ari_url}{path}",
                params=params,
                auth=self._http_auth(),
            )
            response.raise_for_status()
            return _json_response(response)

    def _endpoint_for_number(self, to_number: str) -> str:
        number = "".join(ch for ch in str(to_number or "") if ch.isdigit() or ch == "+")
        return self.config.asterisk_endpoint_template.format(
            number=number,
            trunk=self.config.asterisk_pjsip_endpoint,
        )

    async def _wait_for_stasis_channels(
        self,
        websocket: Any,
        channel_ids: set[str],
        *,
        timeout_seconds: float,
    ) -> None:
        pending = set(channel_ids)
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        while pending:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise TimeoutError(
                    "Timed out waiting for Asterisk channels to enter Stasis: "
                    + ", ".join(sorted(pending))
                )
            try:
                raw = await asyncio.wait_for(websocket.recv(), timeout=min(0.5, remaining))
            except TimeoutError:
                continue
            event = json.loads(raw if isinstance(raw, str) else raw.decode("utf-8"))
            channel_id = _ari_event_channel_id(event)
            if channel_id not in pending:
                continue
            event_type = str(event.get("type") or "")
            if event_type == "StasisStart":
                pending.remove(channel_id)
                continue
            if event_type in {"ChannelHangupRequest", "ChannelDestroyed", "StasisEnd"}:
                cause = event.get("cause_txt") or event.get("cause") or event_type
                raise RuntimeError(
                    f"Asterisk channel {channel_id} ended before entering Stasis: {cause}"
                )

    def _http_auth(self) -> httpx.BasicAuth:
        return httpx.BasicAuth(self.config.asterisk_ari_user, self._password())

    def _basic_auth_token(self) -> str:
        value = f"{self.config.asterisk_ari_user}:{self._password()}".encode("utf-8")
        return base64.b64encode(value).decode("ascii")

    def _password(self) -> str:
        password = os.environ.get(self.config.asterisk_ari_password_env, "")
        if not password:
            raise RuntimeError(
                f"Asterisk ARI password env var {self.config.asterisk_ari_password_env!r} is not set"
            )
        return password

    def _websocket_events_url(self) -> str:
        base_url = self.config.asterisk_ari_url
        if base_url.startswith("https://"):
            ws_base = f"wss://{base_url[len('https://') :]}"
        elif base_url.startswith("http://"):
            ws_base = f"ws://{base_url[len('http://') :]}"
        else:
            ws_base = base_url
        query = urlencode({"app": self.config.asterisk_ari_app})
        return f"{ws_base}/events?{query}"


def _openai_audio_format(media_format: str) -> dict[str, Any]:
    try:
        audio_format = _RTP_STATIC_AUDIO_FORMATS[media_format]
    except KeyError as exc:  # pragma: no cover - guarded by config validation
        raise ValueError(f"Unsupported Realtime bridge media format: {media_format}") from exc
    return {"type": str(audio_format["openai_type"])}


def _rtp_bytes_per_sample(media_format: str) -> int:
    if media_format in _RTP_STATIC_AUDIO_FORMATS:
        return 1
    raise ValueError(f"Unsupported RTP media format: {media_format}")


class OpenAIRealtimeSession:
    def __init__(
        self,
        *,
        config: DidwwSipBridgeConfig,
        projection: dict[str, Any],
        realtime: dict[str, Any],
    ) -> None:
        self.config = config
        self.projection = projection
        self.realtime = realtime
        self.websocket: Any | None = None
        self._session_config_fallback_applied = False

    async def __aenter__(self) -> OpenAIRealtimeSession:
        try:
            import websockets
        except ModuleNotFoundError as exc:  # pragma: no cover - dependency exists in project
            raise RuntimeError("OpenAI Realtime bridge requires websockets") from exc

        api_key_env = str(self.config.openai_api_key_env).strip()
        api_key = os.environ.get(api_key_env, "").strip()
        if not api_key:
            raise RuntimeError(f"OpenAI Realtime API key env var {api_key_env!r} is not set")
        model = str(self.realtime.get("model") or self.config.openai_realtime_model).strip()
        self.websocket = await websockets.connect(
            self._websocket_url(model),
            additional_headers={"Authorization": f"Bearer {api_key}"},
            open_timeout=self.config.request_timeout_seconds,
            close_timeout=self.config.request_timeout_seconds,
        )
        await self.send_json({"type": "session.update", "session": self._session_payload(model)})
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        if self.websocket is not None:
            await self.websocket.close()

    async def append_audio(self, audio: bytes) -> None:
        if not audio:
            return
        await self.send_json(
            {
                "type": "input_audio_buffer.append",
                "audio": base64.b64encode(audio).decode("ascii"),
            }
        )

    async def create_summary_response(self) -> None:
        await self.send_json(
            {
                "type": "response.create",
                "response": {
                    "output_modalities": ["text"],
                    "instructions": (
                        "Summarize this phone call for the originating Truman agent. "
                        "Include a brief summary, decisions, and action items."
                    ),
                },
            }
        )

    async def send_tool_output(self, *, call_id: str, output: dict[str, Any]) -> None:
        await self.send_json(
            {
                "type": "conversation.item.create",
                "item": {
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": json.dumps(output, sort_keys=True),
                },
            }
        )

    async def request_response(self, instructions: str | None = None) -> None:
        payload: dict[str, Any] = {"type": "response.create"}
        if instructions:
            payload["response"] = {"instructions": instructions}
        await self.send_json(payload)

    async def cancel_response(self, response_id: str | None = None) -> None:
        payload: dict[str, Any] = {"type": "response.cancel"}
        if response_id:
            payload["response_id"] = response_id
        await self.send_json(payload)

    async def truncate_assistant_audio(
        self,
        *,
        item_id: str,
        audio_end_ms: int,
        content_index: int = 0,
    ) -> None:
        if not item_id:
            return
        await self.send_json(
            {
                "type": "conversation.item.truncate",
                "item_id": item_id,
                "content_index": max(0, int(content_index)),
                "audio_end_ms": max(0, int(audio_end_ms)),
            }
        )

    async def inject_origin_query_result(
        self,
        query: dict[str, Any],
        *,
        request_response: bool = True,
    ) -> None:
        query_id = str(query.get("query_id") or "").strip()
        status = str(query.get("status") or "").strip() or "unknown"
        answer = str(query.get("answer") or query.get("error") or "").strip()
        citations = query.get("citations") if isinstance(query.get("citations"), list) else []
        text = (
            f"Lookup result for voice query {query_id} "
            f"with status {status}:\n{answer or '<no answer>'}"
        )
        if citations:
            text = f"{text}\nCitations: {', '.join(str(item) for item in citations)}"
        await self.send_json(
            {
                "type": "conversation.item.create",
                "item": {
                    "type": "message",
                    "role": "system",
                    "content": [{"type": "input_text", "text": text}],
                },
            }
        )
        if request_response:
            await self.request_response(
                "Start with a natural transition such as 'I heard back on the earlier question.' "
                "Then briefly tell the caller the lookup result is ready. "
                "Do not mention internal agent handoffs."
            )

    async def recv_json(self) -> dict[str, Any]:
        if self.websocket is None:
            raise RuntimeError("Realtime websocket is not connected")
        raw = await self.websocket.recv()
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            return {}
        return payload

    async def send_json(self, payload: dict[str, Any]) -> None:
        if self.websocket is None:
            raise RuntimeError("Realtime websocket is not connected")
        await self.websocket.send(json.dumps(payload))

    def _websocket_url(self, model: str) -> str:
        base_url = str(self.config.openai_realtime_api_base_url or "").rstrip("/")
        if base_url.startswith("https://"):
            ws_base = f"wss://{base_url[len('https://') :]}"
        elif base_url.startswith("http://"):
            ws_base = f"ws://{base_url[len('http://') :]}"
        else:
            ws_base = base_url
        return f"{ws_base}/realtime?{urlencode({'model': model})}"

    def _session_payload(self, model: str) -> dict[str, Any]:
        instructions = projection_to_instructions(self.projection)
        openai_audio_format = _openai_audio_format(self.config.media_format)
        noise_reduction_type = str(
            self.config.openai_realtime_input_noise_reduction_type
        ).strip().lower()
        turn_detection_type = str(self.config.openai_realtime_turn_detection_type).strip().lower()
        turn_detection: dict[str, Any]
        if turn_detection_type == "semantic_vad":
            turn_detection = {
                "type": "semantic_vad",
                "create_response": False,
                "interrupt_response": False,
            }
        else:
            turn_detection = {
                "type": "server_vad",
                "create_response": False,
                "interrupt_response": False,
                "threshold": self.config.openai_realtime_vad_threshold,
                "prefix_padding_ms": self.config.openai_realtime_vad_prefix_padding_ms,
                "silence_duration_ms": self.config.openai_realtime_vad_silence_duration_ms,
            }
        payload: dict[str, Any] = {
            "type": "realtime",
            "model": model,
            "instructions": instructions,
            "output_modalities": ["audio"],
            "audio": {
                "input": {
                    "format": dict(openai_audio_format),
                    "turn_detection": turn_detection,
                },
                "output": {
                    "format": dict(openai_audio_format),
                    "voice": self.config.openai_realtime_voice,
                },
            },
        }
        if noise_reduction_type and noise_reduction_type != "disabled":
            payload["audio"]["input"]["noise_reduction"] = {"type": noise_reduction_type}
        if self.config.openai_realtime_transcription_model:
            payload["audio"]["input"]["transcription"] = {
                "model": self.config.openai_realtime_transcription_model
            }
        tools = [_end_call_tool_definition()]
        if self.realtime.get("media_controller_url"):
            tools.append(
                {
                    "type": "function",
                    "name": "query_origin_agent",
                    "description": (
                        "Ask the originating Truman agent a live-context question that requires "
                        "its files, tools, repository state, or memory and is not already "
                        "answered by the Current task or Context. Do not use this tool to ask "
                        "for the outbound call objective, opening, script, instructions, "
                        "expected behavior, or call context already present in the projection."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "question": {
                                "type": "string",
                                "description": (
                                    "The caller-facing origin-side question that cannot be "
                                    "answered from projected task/context."
                                ),
                            },
                            "reason": {
                                "type": "string",
                                "description": "Why this cannot be answered from projected context.",
                            },
                        },
                        "required": ["question"],
                    },
                }
            )
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        return payload

    async def apply_session_config_fallback(self, event: dict[str, Any]) -> dict[str, Any] | None:
        if self._session_config_fallback_applied:
            return None
        error = event.get("error") if isinstance(event.get("error"), dict) else event
        if not isinstance(error, dict):
            return None
        message_bits = [
            str(error.get("code") or ""),
            str(error.get("type") or ""),
            str(error.get("param") or ""),
            str(error.get("message") or ""),
        ]
        text = " ".join(bit.lower() for bit in message_bits if bit)
        unsupported_markers = (
            "unsupported",
            "not supported",
            "invalid",
            "unknown field",
            "unrecognized",
        )
        if not any(marker in text for marker in unsupported_markers):
            return None
        removed_features: list[str] = []
        include_noise_reduction = True
        turn_detection_type = str(self.config.openai_realtime_turn_detection_type).strip().lower()
        if any(
            marker in text
            for marker in (
                "noise_reduction",
                "noise reduction",
                "input_audio_noise_reduction",
            )
        ) and str(self.config.openai_realtime_input_noise_reduction_type).strip().lower() != "disabled":
            include_noise_reduction = False
            removed_features.append("audio.input.noise_reduction")
        if (
            turn_detection_type == "semantic_vad"
            and any(
                marker in text
                for marker in (
                    "turn_detection",
                    "turn detection",
                    "semantic_vad",
                    "semantic vad",
                    "vad",
                )
            )
        ):
            turn_detection_type = "server_vad"
            removed_features.append("audio.input.turn_detection.type")
        if not removed_features:
            return None
        self._session_config_fallback_applied = True
        model = str(self.realtime.get("model") or self.config.openai_realtime_model).strip()
        payload = self._session_payload(model)
        if not include_noise_reduction:
            payload["audio"]["input"].pop("noise_reduction", None)
        if turn_detection_type == "server_vad":
            payload["audio"]["input"]["turn_detection"] = {
                "type": "server_vad",
                "create_response": False,
                "interrupt_response": False,
                "threshold": self.config.openai_realtime_vad_threshold,
                "prefix_padding_ms": self.config.openai_realtime_vad_prefix_padding_ms,
                "silence_duration_ms": self.config.openai_realtime_vad_silence_duration_ms,
            }
        await self.send_json({"type": "session.update", "session": payload})
        fallback_metadata = {
            "removed_features": removed_features,
            **_realtime_error_metadata(event),
        }
        if turn_detection_type == "server_vad":
            fallback_metadata["fallback_turn_detection_type"] = "server_vad"
        if not include_noise_reduction:
            fallback_metadata["noise_reduction_omitted"] = True
        return fallback_metadata


@dataclass(frozen=True)
class RealtimeFunctionCall:
    call_id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class BridgeResponseRequest:
    instructions: str | None = None
    reason: str = "bridge_requested"
    key: str | None = None


@dataclass
class OriginQueryState:
    query_id: str
    question: str
    question_key: str
    status: str = "pending"
    query: dict[str, Any] = field(default_factory=dict)
    ack_response_queued: bool = False
    answer_response_queued: bool = False
    timeout_response_queued: bool = False
    result_injected: bool = False
    transcript_recorded: bool = False
    failure_reused: bool = False
    completed_event: asyncio.Event | None = None


@dataclass(frozen=True)
class OutboundAudioFrame:
    payload: bytes
    response_id: str | None = None
    item_id: str | None = None
    content_index: int = 0


class VoiceMediaControllerClient:
    def __init__(
        self,
        *,
        base_url: str | None,
        token_env: str | None,
        timeout_seconds: float,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = str(base_url or "").strip().rstrip("/") or None
        self.token_env = str(token_env or "").strip() or None
        self.timeout_seconds = timeout_seconds
        self._http_client = http_client

    @classmethod
    def from_realtime(
        cls,
        config: DidwwSipBridgeConfig,
        realtime: dict[str, Any],
    ) -> VoiceMediaControllerClient:
        timeout = realtime.get("media_controller_request_timeout_seconds")
        try:
            timeout_seconds = float(timeout) if timeout is not None else config.request_timeout_seconds
        except (TypeError, ValueError):
            timeout_seconds = config.request_timeout_seconds
        return cls(
            base_url=str(realtime.get("media_controller_url") or "").strip() or None,
            token_env=str(realtime.get("media_controller_token_env") or "").strip() or None,
            timeout_seconds=timeout_seconds,
        )

    @property
    def configured(self) -> bool:
        return self.base_url is not None

    async def create_query(
        self,
        voice_session_id: str,
        *,
        question: str,
        transcript_excerpt: str | None,
        metadata: dict[str, Any] | None = None,
        deadline_seconds: int | None = None,
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            f"/voice-sessions/{voice_session_id}/queries",
            json={
                "question": question,
                "transcript_excerpt": transcript_excerpt,
                "metadata": metadata or {},
                "deadline_seconds": deadline_seconds,
            },
        )

    async def get_query(self, voice_session_id: str, query_id: str) -> dict[str, Any]:
        return await self._request(
            "GET",
            f"/voice-sessions/{voice_session_id}/queries/{query_id}",
        )

    async def mark_query_timeout(self, voice_session_id: str, query_id: str) -> dict[str, Any]:
        return await self._request(
            "POST",
            f"/voice-sessions/{voice_session_id}/queries/{query_id}/timeout",
        )

    async def create_inbound_phone_call(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._request(
            "POST",
            "/voice-sessions/inbound-phone-calls",
            json=payload,
        )

    async def sync_voice_session(
        self,
        voice_session_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            f"/voice-sessions/{voice_session_id}/bridge-result",
            json=payload,
        )

    async def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        if self.base_url is None:
            raise RuntimeError("voice media controller URL is not configured")
        headers = dict(kwargs.pop("headers", {}) or {})
        if self.token_env is not None:
            token = os.environ.get(self.token_env, "").strip()
            if not token:
                raise RuntimeError(
                    f"Voice media controller token env var {self.token_env!r} is not set"
                )
            headers["Authorization"] = f"Bearer {token}"
        url = f"{self.base_url}{path}"
        client = self._http_client
        if client is not None:
            response = await client.request(method, url, headers=headers, **kwargs)
            response.raise_for_status()
            return _json_response(response)
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as scoped:
            response = await scoped.request(method, url, headers=headers, **kwargs)
            response.raise_for_status()
            return _json_response(response)


@dataclass
class RtpPacket:
    sequence: int
    timestamp: int
    ssrc: int
    payload_type: int
    payload: bytes
    marker: bool = False

    @classmethod
    def parse(cls, data: bytes) -> RtpPacket:
        if len(data) < 12:
            raise ValueError("RTP packet is too short")
        first, second, sequence, timestamp, ssrc = struct.unpack("!BBHII", data[:12])
        version = first >> 6
        if version != 2:
            raise ValueError("RTP packet version must be 2")
        csrc_count = first & 0x0F
        header_length = 12 + (csrc_count * 4)
        if len(data) < header_length:
            raise ValueError("RTP CSRC header is truncated")
        return cls(
            sequence=sequence,
            timestamp=timestamp,
            ssrc=ssrc,
            payload_type=second & 0x7F,
            marker=bool(second & 0x80),
            payload=data[header_length:],
        )

    def serialize(self) -> bytes:
        first = 0x80
        second = self.payload_type | (0x80 if self.marker else 0)
        header = struct.pack(
            "!BBHII",
            first,
            second,
            self.sequence & 0xFFFF,
            self.timestamp & 0xFFFFFFFF,
            self.ssrc & 0xFFFFFFFF,
        )
        return header + self.payload


class RtpDatagramProtocol(asyncio.DatagramProtocol):
    def __init__(self, endpoint: RtpUdpEndpoint) -> None:
        self.endpoint = endpoint

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.endpoint.transport = transport  # type: ignore[assignment]

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        self.endpoint.remote_addr = addr
        try:
            packet = RtpPacket.parse(data)
        except ValueError:
            return
        self.endpoint.incoming.put_nowait(packet.payload)


class RtpUdpEndpoint:
    def __init__(self, config: DidwwSipBridgeConfig) -> None:
        self.config = config
        self.incoming: asyncio.Queue[bytes] = asyncio.Queue()
        self.transport: asyncio.DatagramTransport | None = None
        self.remote_addr: tuple[str, int] | None = None
        self.port: int | None = None
        self._sequence = 1
        self._timestamp = 0

    async def start(self) -> RtpUdpEndpoint:
        loop = asyncio.get_running_loop()
        last_error: Exception | None = None
        for port in range(self.config.rtp_port_min, self.config.rtp_port_max + 1):
            try:
                transport, _protocol = await loop.create_datagram_endpoint(
                    lambda: RtpDatagramProtocol(self),
                    local_addr=(self.config.rtp_bind_host, port),
                )
            except OSError as exc:
                last_error = exc
                continue
            self.transport = transport  # type: ignore[assignment]
            self.port = port
            return self
        raise RuntimeError(
            f"No RTP UDP port available in {self.config.rtp_port_min}-{self.config.rtp_port_max}"
        ) from last_error

    async def recv_payload(self) -> bytes:
        return await self.incoming.get()

    async def send_payload(
        self,
        payload: bytes,
        *,
        on_samples_sent: Callable[[int], None] | None = None,
    ) -> int:
        if not payload or self.transport is None or self.remote_addr is None:
            return 0
        samples_per_frame = int(self.config.sample_rate_hz * self.config.frame_duration_ms / 1000)
        bytes_per_sample = _rtp_bytes_per_sample(self.config.media_format)
        payload_bytes_per_frame = max(1, samples_per_frame * bytes_per_sample)
        chunks = [
            payload[offset : offset + payload_bytes_per_frame]
            for offset in range(0, len(payload), payload_bytes_per_frame)
        ]
        total_samples_sent = 0
        for index, chunk in enumerate(chunks):
            packet = RtpPacket(
                sequence=self._sequence,
                timestamp=self._timestamp,
                ssrc=self.config.rtp_ssrc,
                payload_type=self.config.rtp_payload_type,
                payload=chunk,
            )
            self.transport.sendto(packet.serialize(), self.remote_addr)
            self._sequence = (self._sequence + 1) & 0xFFFF
            samples_sent = max(1, (len(chunk) + bytes_per_sample - 1) // bytes_per_sample)
            self._timestamp = (self._timestamp + samples_sent) & 0xFFFFFFFF
            total_samples_sent += samples_sent
            if on_samples_sent is not None:
                on_samples_sent(samples_sent)
            if index < len(chunks) - 1:
                await asyncio.sleep(self.config.frame_duration_ms / 1000)
        return total_samples_sent

    def close(self) -> None:
        if self.transport is not None:
            self.transport.close()
            self.transport = None


class DidwwSipBridgeService:
    def __init__(
        self,
        config: DidwwSipBridgeConfig,
        *,
        store: DidwwBridgeStore | None = None,
        ari_client: AsteriskAriClient | None = None,
        realtime_factory: Callable[
            [dict[str, Any], dict[str, Any]], OpenAIRealtimeSession
        ]
        | None = None,
        rtp_endpoint_factory: Callable[[], RtpUdpEndpoint] | None = None,
        start_background_tasks: bool = True,
    ) -> None:
        self.config = config
        self.store = store or DidwwBridgeStore(config.store_path)
        self.ari_client = ari_client or AsteriskAriClient(config)
        self.realtime_factory = realtime_factory or (
            lambda projection, realtime: OpenAIRealtimeSession(
                config=config,
                projection=projection,
                realtime=realtime,
            )
        )
        self.rtp_endpoint_factory = rtp_endpoint_factory or (lambda: RtpUdpEndpoint(config))
        self.start_background_tasks = start_background_tasks
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._background_tasks: set[asyncio.Task[None]] = set()
        self._semaphore = asyncio.Semaphore(config.max_concurrent_calls)

    def create_app(self) -> Starlette:
        service = self

        async def health(_: Request) -> JSONResponse:
            return JSONResponse(
                {
                    "status": "ok",
                    "bridge": "didww_sip_openai_realtime",
                    "asterisk_ari_url": service.config.asterisk_ari_url,
                    "realtime_model": service.config.openai_realtime_model,
                }
            )

        async def start_phone_call(request: Request) -> JSONResponse:
            auth_error = service._auth_error(request)
            if auth_error is not None:
                return auth_error
            try:
                payload = PhoneCallStartRequest.model_validate(await request.json())
            except Exception as exc:
                return JSONResponse({"error": str(exc)}, status_code=400)
            try:
                record, created = service.store.create_call(payload)
            except Exception as exc:
                return JSONResponse({"error": str(exc)}, status_code=400)
            if created and service.start_background_tasks:
                service._start_background_call(record, payload)
            return JSONResponse(
                {
                    "voice_session_id": record.voice_session_id,
                    "request_id": record.request_id,
                    "call_id": record.call_id,
                    "status": record.status,
                    "realtime": sanitize_voice_metadata(record.realtime),
                },
                status_code=201 if created else 200,
            )

        async def get_voice_session(request: Request) -> JSONResponse:
            auth_error = service._auth_error(request)
            if auth_error is not None:
                return auth_error
            voice_session_id = str(request.path_params.get("voice_session_id") or "").strip()
            record = service.store.get(voice_session_id)
            if record is None:
                return JSONResponse({"error": "Unknown voice session"}, status_code=404)
            return JSONResponse(record.response_payload())

        async def hangup_voice_session(request: Request) -> JSONResponse:
            auth_error = service._auth_error(request)
            if auth_error is not None:
                return auth_error
            voice_session_id = str(request.path_params.get("voice_session_id") or "").strip()
            record = service.store.get(voice_session_id)
            if record is None:
                return JSONResponse({"error": "Unknown voice session"}, status_code=404)
            await service._cleanup_asterisk(record)
            record = service.store.complete(voice_session_id, status="ended")
            return JSONResponse(record.response_payload())

        @asynccontextmanager
        async def lifespan(_app: Starlette) -> AsyncIterator[None]:
            listener_task: asyncio.Task[None] | None = None
            if service.start_background_tasks and service.config.enable_inbound_phone_calls:
                listener_task = asyncio.create_task(service._run_inbound_phone_listener())
                service._track_background_task(listener_task)
            try:
                yield
            finally:
                if listener_task is not None:
                    listener_task.cancel()
                    await asyncio.gather(listener_task, return_exceptions=True)
                for task in list(service._background_tasks):
                    task.cancel()
                if service._background_tasks:
                    await asyncio.gather(*service._background_tasks, return_exceptions=True)

        return Starlette(
            debug=False,
            routes=[
                Route("/healthz", health, methods=["GET"]),
                Route("/phone-calls", start_phone_call, methods=["POST"]),
                Route("/voice-sessions/{voice_session_id}", get_voice_session, methods=["GET"]),
                Route(
                    "/voice-sessions/{voice_session_id}/hangup",
                    hangup_voice_session,
                    methods=["POST"],
                ),
            ],
            lifespan=lifespan,
        )

    def _track_background_task(self, task: asyncio.Task[None]) -> None:
        self._background_tasks.add(task)

        def discard(completed: asyncio.Task[None]) -> None:
            self._background_tasks.discard(completed)
            try:
                completed.result()
            except asyncio.CancelledError:
                return
            except Exception:
                logger.exception("didww_bridge_background_task_failed")

        task.add_done_callback(discard)

    def _inbound_gateway_client(self) -> VoiceMediaControllerClient:
        return VoiceMediaControllerClient(
            base_url=self.config.inbound_gateway_url,
            token_env=self.config.inbound_gateway_token_env,
            timeout_seconds=self.config.request_timeout_seconds,
        )

    async def _run_inbound_phone_listener(self) -> None:
        if not self.config.enable_inbound_phone_calls:
            return
        if not self.config.inbound_gateway_url:
            logger.info("inbound phone listener disabled because gateway url is not configured")
            return

        gateway_client = self._inbound_gateway_client()
        handled_channels: set[str] = set()
        try:
            async with self.ari_client.event_stream() as websocket:
                while True:
                    try:
                        raw = await websocket.recv()
                    except Exception as exc:
                        logger.exception("inbound_phone_listener_recv_failed: %s", exc)
                        return
                    event = json.loads(raw if isinstance(raw, str) else raw.decode("utf-8"))
                    if str(event.get("type") or "") != "StasisStart":
                        continue
                    context = _extract_inbound_stasis_context(event)
                    if context is None:
                        continue
                    channel_id = str(context.get("asterisk_channel_id") or "").strip()
                    if not channel_id or channel_id in handled_channels:
                        continue
                    handled_channels.add(channel_id)
                    task = asyncio.create_task(
                        self._handle_inbound_stasis_start(context, gateway_client)
                    )
                    self._track_background_task(task)
        except Exception:
            logger.exception("inbound_phone_listener_failed")

    async def _handle_inbound_stasis_start(
        self,
        context: dict[str, Any],
        gateway_client: VoiceMediaControllerClient,
    ) -> None:
        asterisk_channel_id = str(context.get("asterisk_channel_id") or "").strip()
        bridge_call_id = str(context.get("bridge_call_id") or "").strip()
        did = str(context.get("did") or "").strip()
        route_id = str(context.get("route_id") or "").strip() or None
        caller_number = str(context.get("caller_number") or "").strip()
        sip_headers = context.get("sip_headers") if isinstance(context.get("sip_headers"), dict) else {}
        if not asterisk_channel_id:
            return
        request_payload: dict[str, Any] = {
            "did": did or None,
            "called_number": did or None,
            "caller_number": caller_number or None,
            "asterisk_channel_id": asterisk_channel_id,
            "bridge_call_id": bridge_call_id or asterisk_channel_id,
            "route_id": route_id,
            "sip_headers": sanitize_voice_metadata(sip_headers or {}),
        }
        try:
            response = await gateway_client.create_inbound_phone_call(request_payload)
            payload = InboundPhoneCallStartRequest.model_validate(
                {
                    "voice_session_id": response["voice_session_id"],
                    "request_id": response["request_id"],
                    "gateway_voice_session_id": response["voice_session_id"],
                    "route_id": response["route"]["route_id"],
                    "asterisk_channel_id": response["route"]["asterisk_channel_id"],
                    "bridge_call_id": response["route"]["bridge_call_id"],
                    "projection": response["projection"],
                    "realtime": response["realtime_session_config"],
                    "route": response["route"],
                    "inbound_instructions": response.get("inbound_instructions", {}),
                    "metadata": {
                        "route": response["route"],
                        "inbound_instructions": response.get("inbound_instructions", {}),
                    },
                    "did_redacted": response["route"]["did"]["redacted"],
                    "caller_redacted": response["route"]["caller"]["redacted"],
                }
            )
        except Exception as exc:
            logger.info(
                "inbound_phone_call.provisioning_failed channel_id=%s bridge_call_id=%s "
                "route_id=%s did_sha256=%s caller_sha256=%s reason=%s",
                _debug_log_value(asterisk_channel_id),
                _debug_log_value(bridge_call_id),
                _debug_log_value(route_id),
                _debug_log_value(_hash_identity(did) if did else None),
                _debug_log_value(_hash_identity(caller_number) if caller_number else None),
                _debug_log_value(_debug_snippet(exc)),
            )
            await self.ari_client.hangup_channel(asterisk_channel_id)
            return

        record, created = self.store.create_inbound_call(payload)
        self._append_debug_event(
            record.voice_session_id,
            "inbound_phone_call.provisioned",
            metadata={
                "route_id": record.route_id,
                "gateway_voice_session_id": record.gateway_voice_session_id,
                "did_redacted": record.did_redacted,
                "caller_redacted": record.caller_redacted,
                "asterisk_channel_id": asterisk_channel_id,
                "bridge_call_id": bridge_call_id,
                "created": created,
            },
        )
        if created:
            self._start_inbound_background_call(record, payload)

    def _start_inbound_background_call(
        self,
        record: DidwwBridgeCallRecord,
        payload: InboundPhoneCallStartRequest,
    ) -> None:
        task = asyncio.create_task(self.run_inbound_call(record, payload))
        self._track_background_task(task)

    async def run_inbound_call(
        self,
        record: DidwwBridgeCallRecord,
        payload: InboundPhoneCallStartRequest,
    ) -> None:
        async with self._semaphore:
            rtp = self.rtp_endpoint_factory()
            try:
                await rtp.start()
                if rtp.port is None:
                    raise RuntimeError("RTP endpoint did not bind a port")
                external_host = f"{self.config.external_media_host}:{rtp.port}"
                async with self.realtime_factory(payload.projection, payload.realtime) as realtime:
                    media_controller = VoiceMediaControllerClient.from_realtime(
                        self.config,
                        payload.realtime,
                    )
                    async with self.ari_client.event_stream() as ari_events:
                        handles = await self.ari_client.start_external_media_for_inbound(
                            call_id=record.call_id,
                            inbound_channel_id=payload.asterisk_channel_id,
                            external_host=external_host,
                            event_websocket=ari_events,
                        )
                        self.store.attach_asterisk(
                            record.voice_session_id,
                            ari_channel_id=handles.call_channel_id,
                            ari_bridge_id=handles.bridge_id,
                            ari_external_media_channel_id=handles.external_media_channel_id,
                            rtp_port=rtp.port,
                        )
                        if handles.external_media_local_addr is not None:
                            rtp.remote_addr = handles.external_media_local_addr
                        initial_instructions = initial_inbound_call_instructions(
                            payload.inbound_instructions,
                            payload.projection,
                        )
                        self._append_debug_event(
                            record.voice_session_id,
                            "bridge.response.initial_greeting_requested",
                            metadata={
                                "reason": "inbound_call_started",
                                **_instruction_debug_metadata(initial_instructions),
                            },
                        )
                        await realtime.request_response(initial_instructions)
                        await self._bridge_media_until_done(
                            record.voice_session_id,
                            handles,
                            rtp,
                            realtime,
                            media_controller,
                            ari_events=ari_events,
                        )
            except Exception as exc:
                logger.exception(
                    "didww_bridge_inbound_call_failed voice_session_id=%s",
                    record.voice_session_id,
                )
                self.store.complete(record.voice_session_id, status="failed", error=str(exc))
            finally:
                rtp.close()
                latest = self.store.get(record.voice_session_id)
                if latest is not None:
                    await self._sync_inbound_voice_session_to_gateway(latest, payload.realtime)
                    await self._cleanup_asterisk(latest)

    async def run_call(self, record: DidwwBridgeCallRecord, payload: PhoneCallStartRequest) -> None:
        async with self._semaphore:
            rtp = self.rtp_endpoint_factory()
            try:
                await rtp.start()
                if rtp.port is None:
                    raise RuntimeError("RTP endpoint did not bind a port")
                external_host = f"{self.config.external_media_host}:{rtp.port}"
                async with self.realtime_factory(payload.projection, payload.realtime) as realtime:
                    media_controller = VoiceMediaControllerClient.from_realtime(
                        self.config,
                        payload.realtime,
                    )
                    async with self.ari_client.event_stream() as ari_events:
                        handles = await self.ari_client.start_external_media_call(
                            call_id=record.call_id,
                            to_number=payload.to_number,
                            from_number=payload.from_number,
                            external_host=external_host,
                            event_websocket=ari_events,
                        )
                        self.store.attach_asterisk(
                            record.voice_session_id,
                            ari_channel_id=handles.call_channel_id,
                            ari_bridge_id=handles.bridge_id,
                            ari_external_media_channel_id=handles.external_media_channel_id,
                            rtp_port=rtp.port,
                        )
                        if handles.external_media_local_addr is not None:
                            rtp.remote_addr = handles.external_media_local_addr
                        initial_instructions = initial_outbound_call_instructions(
                            payload.projection
                        )
                        self._append_debug_event(
                            record.voice_session_id,
                            "bridge.response.initial_greeting_requested",
                            metadata={
                                "reason": "call_started",
                                **_instruction_debug_metadata(initial_instructions),
                            },
                        )
                        await realtime.request_response(initial_instructions)
                        await self._bridge_media_until_done(
                            record.voice_session_id,
                            handles,
                            rtp,
                            realtime,
                            media_controller,
                            ari_events=ari_events,
                        )
            except Exception as exc:
                logger.exception("didww_bridge_call_failed voice_session_id=%s", record.voice_session_id)
                self.store.complete(record.voice_session_id, status="failed", error=str(exc))
            finally:
                rtp.close()
                latest = self.store.get(record.voice_session_id)
                if latest is not None:
                    await self._cleanup_asterisk(latest)

    async def _bridge_media_until_done(
        self,
        voice_session_id: str,
        handles: AsteriskCallHandles,
        rtp: RtpUdpEndpoint,
        realtime: OpenAIRealtimeSession,
        media_controller: VoiceMediaControllerClient,
        ari_events: Any | None = None,
    ) -> None:
        stop_event = asyncio.Event()
        tasks = [
            asyncio.create_task(self._rtp_to_realtime(rtp, realtime, stop_event)),
            asyncio.create_task(
                self._realtime_to_rtp(
                    voice_session_id,
                    rtp,
                    realtime,
                    media_controller,
                    stop_event,
                )
            ),
            asyncio.create_task(
                self._wait_for_asterisk_end(
                    voice_session_id,
                    handles,
                    stop_event,
                    websocket=ari_events,
                )
            ),
        ]
        try:
            done, pending = await asyncio.wait(
                tasks,
                timeout=self.config.max_call_seconds,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if not done:
                self.store.complete(voice_session_id, status="ended", summary="Call reached max duration.")
            stop_event.set()
            await asyncio.gather(*pending, return_exceptions=True)
            for task in done:
                exc = task.exception()
                if exc is not None:
                    raise exc
            latest = self.store.get(voice_session_id)
            if latest is not None and latest.status not in {"ended", "failed"}:
                self.store.complete(voice_session_id, status="ended")
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _rtp_to_realtime(
        self,
        rtp: RtpUdpEndpoint,
        realtime: OpenAIRealtimeSession,
        stop_event: asyncio.Event,
    ) -> None:
        while not stop_event.is_set():
            try:
                payload = await asyncio.wait_for(rtp.recv_payload(), timeout=0.5)
            except TimeoutError:
                continue
            await realtime.append_audio(payload)

    async def _realtime_to_rtp(
        self,
        voice_session_id: str,
        rtp: RtpUdpEndpoint,
        realtime: OpenAIRealtimeSession,
        media_controller: VoiceMediaControllerClient,
        stop_event: asyncio.Event,
    ) -> None:
        summary_parts: list[str] = []
        query_tasks: set[asyncio.Task[None]] = set()
        delegated_queries: dict[str, str] = {}
        origin_queries_by_question: dict[str, OriginQueryState] = {}
        origin_queries_by_id: dict[str, OriginQueryState] = {}
        assistant_speaking = True
        response_active = True
        active_response_id: str | None = None
        last_caller_turn: str | None = None
        pending_responses: list[BridgeResponseRequest] = []
        pending_response_keys: set[str] = set()
        last_response_request: BridgeResponseRequest | None = None
        response_lock = asyncio.Lock()
        audio_response_started_at: dict[str, float] = {}
        audio_delta_counts: dict[str, int] = {}
        audio_response_finished: set[str] = set()
        audio_item_played_samples: dict[str, int] = {}
        audio_item_content_index: dict[str, int] = {}
        audio_item_response_id: dict[str, str] = {}
        truncated_audio_items: set[str] = set()
        outbound_audio_queue: asyncio.Queue[OutboundAudioFrame] = asyncio.Queue()
        outbound_sender_active = False
        outbound_sender_task: asyncio.Task[None] | None = None
        last_played_audio_item_id: str | None = None
        last_assistant_audio_at: float | None = None
        current_caller_utterance_id = 0
        caller_speech_active = False
        caller_turn_incomplete = False
        caller_speech_started_at: float | None = None
        caller_speech_started_during_assistant = False
        barge_cancelled_utterance_id: int | None = None
        barge_delay_tasks: set[asyncio.Task[None]] = set()
        idle_prompt_task: asyncio.Task[None] | None = None
        idle_prompt_sent_this_period = False
        idle_generation = 0
        loop = asyncio.get_running_loop()
        bytes_per_sample = _rtp_bytes_per_sample(self.config.media_format)

        def payload_sample_count(payload: bytes) -> int:
            if not payload:
                return 0
            return max(1, (len(payload) + bytes_per_sample - 1) // bytes_per_sample)

        def played_audio_end_ms(item_id: str) -> int:
            samples = max(0, audio_item_played_samples.get(item_id, 0))
            return int((samples * 1000) // self.config.sample_rate_hz)

        def send_payload_accepts_sample_callback() -> bool:
            send_payload = getattr(rtp, "send_payload", None)
            if not callable(send_payload):
                return False
            try:
                parameters = inspect.signature(send_payload).parameters
            except (TypeError, ValueError):
                return False
            return "on_samples_sent" in parameters or any(
                parameter.kind is inspect.Parameter.VAR_KEYWORD
                for parameter in parameters.values()
            )

        rtp_send_supports_sample_callback = send_payload_accepts_sample_callback()

        def mark_frame_samples_sent(frame: OutboundAudioFrame, samples: int) -> None:
            nonlocal last_played_audio_item_id
            sent_samples = max(0, samples)
            if not frame.item_id or sent_samples <= 0:
                return
            last_played_audio_item_id = frame.item_id
            audio_item_played_samples[frame.item_id] = (
                audio_item_played_samples.get(frame.item_id, 0) + sent_samples
            )
            audio_item_content_index.setdefault(frame.item_id, frame.content_index)
            if frame.response_id:
                audio_item_response_id.setdefault(frame.item_id, frame.response_id)

        def fallback_sent_sample_count(result: object, payload: bytes) -> int:
            if result is None:
                return payload_sample_count(payload)
            try:
                return max(0, int(result))
            except (TypeError, ValueError):
                return 0

        async def send_outbound_audio() -> None:
            nonlocal outbound_sender_active, last_assistant_audio_at
            while True:
                frame = await outbound_audio_queue.get()
                outbound_sender_active = True
                try:
                    if rtp_send_supports_sample_callback:
                        await rtp.send_payload(
                            frame.payload,
                            on_samples_sent=lambda samples: mark_frame_samples_sent(
                                frame, samples
                            ),
                        )
                    else:
                        result = await rtp.send_payload(frame.payload)
                        mark_frame_samples_sent(
                            frame,
                            fallback_sent_sample_count(result, frame.payload),
                        )
                finally:
                    last_assistant_audio_at = loop.time()
                    outbound_sender_active = False

        def start_outbound_sender() -> None:
            nonlocal outbound_sender_task
            outbound_sender_task = asyncio.create_task(send_outbound_audio())

        def drain_outbound_audio_queue() -> int:
            drained = 0
            while True:
                try:
                    outbound_audio_queue.get_nowait()
                except asyncio.QueueEmpty:
                    return drained
                drained += 1

        async def restart_outbound_sender_after_cancel() -> None:
            nonlocal outbound_sender_task, outbound_sender_active
            if outbound_sender_task is not None and not outbound_sender_task.done():
                outbound_sender_task.cancel()
                await asyncio.gather(outbound_sender_task, return_exceptions=True)
            outbound_sender_active = False
            start_outbound_sender()

        def assistant_audio_active() -> bool:
            if outbound_sender_active or not outbound_audio_queue.empty():
                return True
            if any(key not in audio_response_finished for key in audio_response_started_at):
                return True
            if last_assistant_audio_at is not None:
                return (loop.time() - last_assistant_audio_at) < _ASSISTANT_PLAYBACK_DRAIN_SECONDS
            return False

        def interruptible_assistant_turn_active() -> bool:
            return assistant_audio_active() or response_active or assistant_speaking

        def origin_query_turn_active() -> bool:
            if any(state.status == "pending" for state in origin_queries_by_id.values()):
                return True
            if any(state.status == "pending" for state in origin_queries_by_question.values()):
                return True
            if last_response_request is not None and _is_origin_query_response_key(
                last_response_request.key
            ):
                return True
            return any(_is_origin_query_response_key(request.key) for request in pending_responses)

        def response_blocked_by_caller_turn(request: BridgeResponseRequest) -> bool:
            # Caller speech can still barge in, but origin-query results must stay queued
            # until the caller turn has a final transcript.
            return caller_turn_incomplete and _is_origin_query_response_key(request.key)

        def first_blocked_origin_response_index() -> int | None:
            for index, pending in enumerate(pending_responses):
                if response_blocked_by_caller_turn(pending):
                    return index
            return None

        def discard_pending_response_key(key: str) -> int:
            before = len(pending_responses)
            pending_responses[:] = [
                pending for pending in pending_responses if pending.key != key
            ]
            removed = before - len(pending_responses)
            if removed:
                pending_response_keys.discard(key)
            return removed

        def cancel_idle_prompt_timer() -> bool:
            nonlocal idle_prompt_task
            task = idle_prompt_task
            idle_prompt_task = None
            if task is not None and not task.done():
                task.cancel()
                return True
            return False

        async def cancel_idle_prompt(
            *,
            reason: str,
            reset_period: bool = False,
        ) -> None:
            nonlocal idle_generation, idle_prompt_sent_this_period
            if reset_period:
                idle_generation += 1
                idle_prompt_sent_this_period = False
            timer_cancelled = cancel_idle_prompt_timer()
            async with response_lock:
                removed = discard_pending_response_key(_IDLE_PROMPT_RESPONSE_KEY)
                active_idle = (
                    last_response_request is not None
                    and last_response_request.key == _IDLE_PROMPT_RESPONSE_KEY
                    and not assistant_audio_active()
                )
            if timer_cancelled or removed or active_idle:
                self._append_debug_event(
                    voice_session_id,
                    "bridge.idle_prompt.cancelled",
                    metadata={
                        "reason": reason,
                        "timer_cancelled": timer_cancelled,
                        "removed_queued_count": removed,
                        "active_idle_response": active_idle,
                    },
                )
            if active_idle:
                await realtime.cancel_response(active_response_id)

        async def idle_prompt_after_playback(generation: int, *, reason: str) -> None:
            nonlocal idle_prompt_sent_this_period
            try:
                while not stop_event.is_set() and assistant_audio_active():
                    await asyncio.sleep(0.05)
                if (
                    stop_event.is_set()
                    or generation != idle_generation
                    or idle_prompt_sent_this_period
                ):
                    return
                self._append_debug_event(
                    voice_session_id,
                    "bridge.idle_countdown.started",
                    metadata={
                        "reason": reason,
                        "delay_ms": int(_BRIDGE_IDLE_PROMPT_DELAY_SECONDS * 1000),
                        "playback_drain_ms": int(_ASSISTANT_PLAYBACK_DRAIN_SECONDS * 1000),
                    },
                )
                await asyncio.sleep(_BRIDGE_IDLE_PROMPT_DELAY_SECONDS)
                if (
                    stop_event.is_set()
                    or generation != idle_generation
                    or idle_prompt_sent_this_period
                    or caller_speech_active
                    or response_active
                    or assistant_speaking
                    or pending_responses
                ):
                    return
                idle_prompt_sent_this_period = True
                await request_bridge_response(
                    _IDLE_PROMPT_INSTRUCTIONS,
                    reason="idle_prompt",
                    key=_IDLE_PROMPT_RESPONSE_KEY,
                )
            except asyncio.CancelledError:
                raise

        def schedule_idle_prompt_after_playback(*, reason: str) -> None:
            nonlocal idle_prompt_task
            if idle_prompt_sent_this_period:
                return
            if not audio_response_started_at and last_assistant_audio_at is None:
                return
            cancel_idle_prompt_timer()
            idle_prompt_task = asyncio.create_task(
                idle_prompt_after_playback(idle_generation, reason=reason)
            )

        async def send_barge_in_cancel(
            *,
            reason: str,
            utterance_id: int,
            transcript_text: str | None = None,
        ) -> None:
            nonlocal barge_cancelled_utterance_id
            if barge_cancelled_utterance_id == utterance_id:
                return
            barge_cancelled_utterance_id = utterance_id
            elapsed_ms = int(
                max(
                    0.0,
                    loop.time() - (caller_speech_started_at or loop.time()),
                )
                * 1000
            )
            truncate_item_id = last_played_audio_item_id
            truncate_audio_end_ms = (
                played_audio_end_ms(truncate_item_id) if truncate_item_id else 0
            )
            cancel_response_id = (
                active_response_id if active_response_id and response_active else None
            )
            dropped_frames = drain_outbound_audio_queue()
            await restart_outbound_sender_after_cancel()
            self._append_debug_event(
                voice_session_id,
                "barge_in_cancel_sent",
                metadata={
                    "reason": reason,
                    "response_id": active_response_id,
                    "response_active": response_active,
                    "cancel_response_id": cancel_response_id,
                    "utterance_id": utterance_id,
                    "elapsed_ms": elapsed_ms,
                    "dropped_audio_frames": dropped_frames,
                    "truncate_item_id": truncate_item_id,
                    "truncate_audio_end_ms": truncate_audio_end_ms,
                    "transcript_snippet": _debug_snippet(transcript_text),
                },
            )
            if cancel_response_id:
                await realtime.cancel_response(cancel_response_id)
            else:
                self._append_debug_event(
                    voice_session_id,
                    "barge_in_cancel_response_skipped",
                    metadata={
                        "reason": "no_active_response",
                        "trigger_reason": reason,
                        "utterance_id": utterance_id,
                        "truncate_item_id": truncate_item_id,
                        "truncate_audio_end_ms": truncate_audio_end_ms,
                    },
                )
            if (
                truncate_item_id
                and truncate_audio_end_ms > 0
                and truncate_item_id not in truncated_audio_items
            ):
                content_index = audio_item_content_index.get(truncate_item_id, 0)
                await realtime.truncate_assistant_audio(
                    item_id=truncate_item_id,
                    content_index=content_index,
                    audio_end_ms=truncate_audio_end_ms,
                )
                truncated_audio_items.add(truncate_item_id)
                self._append_debug_event(
                    voice_session_id,
                    "assistant_audio_truncated",
                    metadata={
                        "reason": reason,
                        "response_id": audio_item_response_id.get(
                            truncate_item_id, active_response_id
                        ),
                        "item_id": truncate_item_id,
                        "content_index": content_index,
                        "audio_end_ms": truncate_audio_end_ms,
                        "utterance_id": utterance_id,
                        "dropped_audio_frames": dropped_frames,
                    },
                )

        async def delayed_barge_in_check(utterance_id: int) -> None:
            delay_seconds = self.config.barge_in_delay_ms / 1000
            if delay_seconds > 0:
                await asyncio.sleep(delay_seconds)
            if stop_event.is_set():
                return
            if utterance_id != current_caller_utterance_id:
                return
            if not caller_speech_active or not caller_speech_started_during_assistant:
                return
            if not interruptible_assistant_turn_active():
                return
            await send_barge_in_cancel(
                reason="speech_delay_elapsed",
                utterance_id=utterance_id,
            )

        def track_barge_delay_task(task: asyncio.Task[None]) -> None:
            barge_delay_tasks.add(task)
            task.add_done_callback(lambda completed: barge_delay_tasks.discard(completed))

        async def handle_speech_started(event: dict[str, Any]) -> None:
            nonlocal current_caller_utterance_id
            nonlocal caller_speech_active, caller_speech_started_at, caller_turn_incomplete
            nonlocal caller_speech_started_during_assistant
            await cancel_idle_prompt(reason="caller_speech_started", reset_period=True)
            current_caller_utterance_id += 1
            caller_speech_active = True
            caller_turn_incomplete = True
            caller_speech_started_at = loop.time()
            audio_active = assistant_audio_active()
            caller_speech_started_during_assistant = (
                audio_active or response_active or assistant_speaking
            )
            if not caller_speech_started_during_assistant:
                return
            self._append_debug_event(
                voice_session_id,
                "barge_in_delay_started",
                metadata={
                    "reason": "caller_speech_started",
                    "response_id": active_response_id,
                    "utterance_id": current_caller_utterance_id,
                    "delay_ms": self.config.barge_in_delay_ms,
                    "assistant_audio_active": audio_active,
                    "response_active": response_active,
                    "assistant_speaking": assistant_speaking,
                    **_realtime_vad_metadata(event),
                },
            )
            track_barge_delay_task(
                asyncio.create_task(delayed_barge_in_check(current_caller_utterance_id))
            )

        async def handle_speech_stopped() -> None:
            nonlocal caller_speech_active
            caller_speech_active = False

        async def request_bridge_response(
            instructions: str | None = None,
            *,
            reason: str = "bridge_requested",
            key: str | None = None,
            supersede_keys: set[str] | None = None,
        ) -> None:
            nonlocal last_response_request, response_active
            request = BridgeResponseRequest(instructions=instructions, reason=reason, key=key)
            async with response_lock:
                if request.key != _IDLE_PROMPT_RESPONSE_KEY:
                    timer_cancelled = cancel_idle_prompt_timer()
                    removed_idle = discard_pending_response_key(_IDLE_PROMPT_RESPONSE_KEY)
                    if timer_cancelled or removed_idle:
                        self._append_debug_event(
                            voice_session_id,
                            "bridge.idle_prompt.cancelled",
                            metadata={
                                "reason": request.reason,
                                "timer_cancelled": timer_cancelled,
                                "removed_queued_count": removed_idle,
                            },
                        )
                if supersede_keys:
                    pending_responses[:] = [
                        pending
                        for pending in pending_responses
                        if pending.key not in supersede_keys
                    ]
                    pending_response_keys.difference_update(supersede_keys)
                if key and (
                    key in pending_response_keys
                    or (last_response_request is not None and last_response_request.key == key)
                ):
                    self._append_debug_event(
                        voice_session_id,
                        "bridge.response.skipped_duplicate",
                        metadata={
                            "reason": reason,
                            "request_key": key,
                            "queued_count": len(pending_responses),
                            "last_caller_turn_snippet": _debug_snippet(last_caller_turn),
                            **_instruction_debug_metadata(instructions),
                        },
                    )
                    return
                if response_blocked_by_caller_turn(request):
                    pending_responses.append(request)
                    if key:
                        pending_response_keys.add(key)
                    self._append_debug_event(
                        voice_session_id,
                        "bridge.response.queued",
                        metadata={
                            "reason": "caller_turn_incomplete",
                            "request_reason": request.reason,
                            "request_key": request.key,
                            "queued_count": len(pending_responses),
                            "last_caller_turn_snippet": _debug_snippet(last_caller_turn),
                            **_instruction_debug_metadata(instructions),
                        },
                    )
                    return
                if response_active or assistant_speaking:
                    insert_at = len(pending_responses)
                    if request.reason == "caller_turn_completed":
                        blocked_index = first_blocked_origin_response_index()
                        if blocked_index is not None:
                            insert_at = blocked_index
                    pending_responses.insert(insert_at, request)
                    if key:
                        pending_response_keys.add(key)
                    self._append_debug_event(
                        voice_session_id,
                        "bridge.response.queued",
                        metadata={
                            "reason": "active_response",
                            "request_reason": request.reason,
                            "request_key": request.key,
                            "queued_count": len(pending_responses),
                            "last_caller_turn_snippet": _debug_snippet(last_caller_turn),
                            **_instruction_debug_metadata(instructions),
                        },
                    )
                    return
                response_active = True
                last_response_request = request
                self._append_debug_event(
                    voice_session_id,
                    "bridge.response.requested",
                    metadata={
                        "reason": request.reason,
                        "request_key": request.key,
                        "queued_count": len(pending_responses),
                        "last_caller_turn_snippet": _debug_snippet(last_caller_turn),
                        **_instruction_debug_metadata(instructions),
                    },
                )
            await realtime.request_response(request.instructions)

        async def discard_bridge_response_keys(
            keys: set[str],
            *,
            reason: str,
        ) -> int:
            removed = 0
            async with response_lock:
                for key in keys:
                    removed += discard_pending_response_key(key)
            if removed:
                self._append_debug_event(
                    voice_session_id,
                    "bridge.response.discarded",
                    metadata={
                        "reason": reason,
                        "removed_count": removed,
                        "request_keys": sorted(keys),
                        "last_caller_turn_snippet": _debug_snippet(last_caller_turn),
                    },
                )
            return removed

        async def mark_caller_turn_completed(transcript_text: str) -> None:
            nonlocal caller_speech_active, caller_speech_started_during_assistant
            nonlocal caller_turn_incomplete, last_caller_turn
            blocked_origin_response_index = first_blocked_origin_response_index()
            await cancel_idle_prompt(reason="caller_transcript_completed", reset_period=True)
            last_caller_turn = transcript_text
            caller_speech_active = False
            caller_turn_incomplete = False
            is_backchannel = _is_backchannel_transcript(
                transcript_text,
                phrases=self.config.backchannel_phrases,
                max_words=self.config.backchannel_max_words,
            )
            if origin_query_turn_active() and _is_origin_query_hold_ack(transcript_text):
                caller_speech_started_during_assistant = False
                self._append_debug_event(
                    voice_session_id,
                    "backchannel_ignored",
                    metadata={
                        "reason": "origin_query_hold_ack",
                        "utterance_id": current_caller_utterance_id,
                        "transcript_snippet": _debug_snippet(transcript_text),
                    },
                )
                if (
                    blocked_origin_response_index is not None
                    and not response_active
                    and not assistant_speaking
                ):
                    await mark_response_done_and_flush()
                return
            if not caller_speech_started_during_assistant:
                if is_backchannel:
                    self._append_debug_event(
                        voice_session_id,
                        "backchannel_ignored",
                        metadata={
                            "reason": "standalone_backchannel",
                            "utterance_id": current_caller_utterance_id,
                            "transcript_snippet": _debug_snippet(transcript_text),
                        },
                    )
                    if (
                        blocked_origin_response_index is not None
                        and not response_active
                        and not assistant_speaking
                    ):
                        await mark_response_done_and_flush()
                    return
                request_instructions = (
                    _CALLER_TURN_COMPLETION_INSTRUCTIONS
                    if blocked_origin_response_index is not None
                    else None
                )
                request_key = (
                    f"{_CALLER_TURN_COMPLETION_RESPONSE_KEY_PREFIX}:{current_caller_utterance_id}"
                    if blocked_origin_response_index is not None
                    else None
                )
                await request_bridge_response(
                    request_instructions,
                    reason="caller_turn_completed",
                    key=request_key,
                )
                return
            elapsed_ms = int(
                max(
                    0.0,
                    loop.time() - (caller_speech_started_at or loop.time()),
                )
                * 1000
            )
            self._append_debug_event(
                voice_session_id,
                "barge_in_transcript_classified",
                metadata={
                    "reason": "backchannel" if is_backchannel else "substantive",
                    "response_id": active_response_id,
                    "utterance_id": current_caller_utterance_id,
                    "elapsed_ms": elapsed_ms,
                    "transcript_snippet": _debug_snippet(transcript_text),
                    "word_count": _word_count(transcript_text),
                },
            )
            if is_backchannel:
                caller_speech_started_during_assistant = False
                self._append_debug_event(
                    voice_session_id,
                    "backchannel_ignored",
                    metadata={
                        "reason": "classified_backchannel",
                        "response_id": active_response_id,
                        "utterance_id": current_caller_utterance_id,
                        "elapsed_ms": elapsed_ms,
                        "transcript_snippet": _debug_snippet(transcript_text),
                    },
                )
                if (
                    blocked_origin_response_index is not None
                    and not response_active
                    and not assistant_speaking
                ):
                    await mark_response_done_and_flush()
                return
            caller_speech_started_during_assistant = False
            request_instructions = (
                _CALLER_TURN_COMPLETION_INSTRUCTIONS
                if blocked_origin_response_index is not None
                else None
            )
            request_key = (
                f"{_CALLER_TURN_COMPLETION_RESPONSE_KEY_PREFIX}:{current_caller_utterance_id}"
                if blocked_origin_response_index is not None
                else None
            )
            await request_bridge_response(
                request_instructions,
                reason="caller_turn_completed",
                key=request_key,
            )
            if barge_cancelled_utterance_id == current_caller_utterance_id:
                return
            if assistant_audio_active():
                await send_barge_in_cancel(
                    reason="substantive_transcript",
                    utterance_id=current_caller_utterance_id,
                    transcript_text=transcript_text,
                )

        async def mark_response_created(event: dict[str, Any]) -> None:
            nonlocal active_response_id, assistant_speaking, response_active
            async with response_lock:
                active_response_id = _realtime_response_id(event) or active_response_id
                assistant_speaking = True
                response_active = True

        async def mark_assistant_speaking() -> None:
            nonlocal assistant_speaking
            async with response_lock:
                assistant_speaking = True

        async def mark_response_done_and_flush() -> bool:
            nonlocal assistant_speaking, last_response_request, response_active
            request: BridgeResponseRequest | None = None
            async with response_lock:
                assistant_speaking = False
                response_active = False
                last_response_request = None
                if not pending_responses:
                    return False
                if response_blocked_by_caller_turn(pending_responses[0]):
                    self._append_debug_event(
                        voice_session_id,
                        "bridge.response.blocked",
                        metadata={
                            "reason": "caller_turn_incomplete",
                            "request_reason": pending_responses[0].reason,
                            "request_key": pending_responses[0].key,
                            "queued_count": len(pending_responses),
                            "last_caller_turn_snippet": _debug_snippet(last_caller_turn),
                            **_instruction_debug_metadata(pending_responses[0].instructions),
                        },
                    )
                    return False
                request = pending_responses.pop(0)
                if request.key:
                    pending_response_keys.discard(request.key)
                response_active = True
                last_response_request = request
                self._append_debug_event(
                    voice_session_id,
                    "bridge.response.flushed",
                    metadata={
                        "reason": "response_done",
                        "request_reason": request.reason,
                        "request_key": request.key,
                        "queued_count": len(pending_responses),
                        "last_caller_turn_snippet": _debug_snippet(last_caller_turn),
                        **_instruction_debug_metadata(request.instructions),
                    },
                )
            await realtime.request_response(request.instructions)
            return True

        async def requeue_last_response_request() -> None:
            nonlocal assistant_speaking, last_response_request, response_active
            async with response_lock:
                assistant_speaking = True
                response_active = True
                request = last_response_request
                last_response_request = None
                if request is None:
                    return
                if request.key and request.key in pending_response_keys:
                    return
                pending_responses.insert(0, request)
                if request.key:
                    pending_response_keys.add(request.key)
                self._append_debug_event(
                    voice_session_id,
                    "bridge.response.requeued",
                    metadata={
                        "reason": "conversation_already_has_active_response",
                        "request_reason": request.reason,
                        "request_key": request.key,
                        "queued_count": len(pending_responses),
                        "last_caller_turn_snippet": _debug_snippet(last_caller_turn),
                        **_instruction_debug_metadata(request.instructions),
                    },
                )

        def mark_audio_delta(event: dict[str, Any]) -> None:
            nonlocal active_response_id, last_assistant_audio_at
            active_response_id = _realtime_response_id(event) or active_response_id
            last_assistant_audio_at = loop.time()
            key = _realtime_audio_key(event)
            audio_delta_counts[key] = audio_delta_counts.get(key, 0) + 1
            if key in audio_response_started_at:
                return
            audio_response_started_at[key] = loop.time()
            self._append_debug_event(
                voice_session_id,
                "response.audio_delta.started",
                metadata={
                    **_realtime_audio_metadata(event),
                    "reason": "audio_delta",
                },
            )

        def mark_audio_finished(event: dict[str, Any], *, reason: str) -> None:
            key = _realtime_audio_key(event)
            keys = [key] if key in audio_response_started_at else []
            if not keys and reason in {"response.done", "response.cancelled"}:
                keys = [
                    started_key
                    for started_key in audio_response_started_at
                    if started_key not in audio_response_finished
                ]
            for finished_key in keys:
                if finished_key in audio_response_finished:
                    continue
                audio_response_finished.add(finished_key)
                started_at = audio_response_started_at.get(finished_key, loop.time())
                metadata = {
                    **_realtime_audio_metadata(event),
                    "reason": reason,
                    "audio_delta_count": audio_delta_counts.get(finished_key, 0),
                    "duration_ms": int(max(0.0, loop.time() - started_at) * 1000),
                }
                self._append_debug_event(
                    voice_session_id,
                    "response.audio_delta.finished",
                    metadata=metadata,
                )

        def mark_response_inactive(event: dict[str, Any]) -> None:
            nonlocal active_response_id
            response_id = _realtime_response_id(event)
            if response_id is None or response_id == active_response_id:
                active_response_id = None

        start_outbound_sender()

        try:
            while not stop_event.is_set():
                try:
                    event = await asyncio.wait_for(realtime.recv_json(), timeout=0.5)
                except TimeoutError:
                    continue
                event_type = str(event.get("type") or "")
                if event_type in {"response.created", "response.done", "response.cancelled"}:
                    self._append_debug_event(
                        voice_session_id,
                        event_type,
                        metadata=_realtime_lifecycle_metadata(event),
                    )
                if event_type == "response.created":
                    await mark_response_created(event)
                if _realtime_vad_event(event):
                    self._append_debug_event(
                        voice_session_id,
                        "realtime.vad",
                        metadata=_realtime_vad_metadata(event),
                    )
                if event_type == "input_audio_buffer.speech_started":
                    await handle_speech_started(event)
                if event_type == "input_audio_buffer.speech_stopped":
                    await handle_speech_stopped()
                function_call = _realtime_function_call(event)
                if function_call is not None:
                    await self._handle_realtime_function_call(
                        voice_session_id,
                        function_call,
                        realtime,
                        media_controller,
                        query_tasks,
                        stop_event,
                        delegated_queries=delegated_queries,
                        origin_queries_by_question=origin_queries_by_question,
                        origin_queries_by_id=origin_queries_by_id,
                        request_response=request_bridge_response,
                        discard_response_keys=discard_bridge_response_keys,
                    )
                    continue
                audio_delta = _realtime_audio_delta(event)
                if audio_delta:
                    await mark_assistant_speaking()
                    mark_audio_delta(event)
                    await outbound_audio_queue.put(
                        OutboundAudioFrame(
                            payload=base64.b64decode(audio_delta),
                            response_id=_realtime_response_id(event),
                            item_id=_realtime_item_id(event),
                            content_index=_realtime_content_index(event),
                        )
                    )
                    await asyncio.sleep(0)
                    continue
                if _realtime_audio_done(event):
                    mark_audio_finished(event, reason=event_type)
                transcript_text = _realtime_transcript_text(event)
                if transcript_text:
                    speaker = "assistant" if event_type.startswith("response.") else "caller"
                    transcript_item_id = _realtime_item_id(event)
                    if speaker == "assistant" and transcript_item_id in truncated_audio_items:
                        self._append_debug_event(
                            voice_session_id,
                            "assistant_transcript.truncated_ignored",
                            metadata=_realtime_transcript_metadata(event, transcript_text),
                        )
                    else:
                        self.store.append_transcript_event(
                            voice_session_id,
                            speaker=speaker,
                            text=transcript_text,
                            metadata={"event_type": event_type},
                        )
                        self._append_debug_event(
                            voice_session_id,
                            (
                                "assistant_transcript.completed"
                                if speaker == "assistant"
                                else "caller_transcript.completed"
                            ),
                            metadata=_realtime_transcript_metadata(event, transcript_text),
                        )
                    if event_type == "conversation.item.input_audio_transcription.completed":
                        await mark_caller_turn_completed(transcript_text)
                text_delta = _realtime_text_delta(event)
                if text_delta:
                    summary_parts.append(text_delta)
                if event_type == "response.done" and summary_parts:
                    self.store.complete(
                        voice_session_id,
                        status="ended",
                        summary="".join(summary_parts).strip(),
                    )
                if event_type == "response.done":
                    mark_audio_finished(event, reason="response.done")
                    mark_response_inactive(event)
                    flushed_response = await mark_response_done_and_flush()
                    if not flushed_response:
                        schedule_idle_prompt_after_playback(reason="response_done")
                if event_type == "response.cancelled":
                    mark_audio_finished(event, reason="response.cancelled")
                    mark_response_inactive(event)
                    await mark_response_done_and_flush()
                if event_type == "error":
                    error = event.get("error") if isinstance(event.get("error"), dict) else event
                    self._append_debug_event(
                        voice_session_id,
                        "error",
                        metadata=_realtime_error_metadata(event),
                    )
                    fallback_handler = getattr(realtime, "apply_session_config_fallback", None)
                    fallback_metadata = (
                        await fallback_handler(event) if callable(fallback_handler) else None
                    )
                    if fallback_metadata is not None:
                        self._append_debug_event(
                            voice_session_id,
                            "realtime.session_config_fallback",
                            metadata=fallback_metadata,
                        )
                        continue
                    if (
                        isinstance(error, dict)
                        and error.get("code") == "conversation_already_has_active_response"
                    ):
                        await requeue_last_response_request()
                        continue
                    if (
                        isinstance(error, dict)
                        and error.get("code") == "response_cancel_not_active"
                    ):
                        self._append_debug_event(
                            voice_session_id,
                            "response_cancel_not_active_ignored",
                            metadata={
                                "reason": "cancel_race",
                                **_realtime_error_metadata(event),
                            },
                        )
                        continue
                    raise RuntimeError(str(event.get("error") or event))
        finally:
            if idle_prompt_task is not None:
                idle_prompt_task.cancel()
            if outbound_sender_task is not None:
                outbound_sender_task.cancel()
            for task in barge_delay_tasks:
                task.cancel()
            for task in query_tasks:
                task.cancel()
            if idle_prompt_task is not None:
                await asyncio.gather(idle_prompt_task, return_exceptions=True)
            if outbound_sender_task is not None:
                await asyncio.gather(outbound_sender_task, return_exceptions=True)
            await asyncio.gather(*barge_delay_tasks, return_exceptions=True)
            await asyncio.gather(*query_tasks, return_exceptions=True)

    async def _handle_realtime_function_call(
        self,
        voice_session_id: str,
        function_call: RealtimeFunctionCall,
        realtime: OpenAIRealtimeSession,
        media_controller: VoiceMediaControllerClient,
        query_tasks: set[asyncio.Task[None]],
        stop_event: asyncio.Event,
        *,
        delegated_queries: dict[str, str] | None = None,
        origin_queries_by_question: dict[str, OriginQueryState] | None = None,
        origin_queries_by_id: dict[str, OriginQueryState] | None = None,
        request_response: Callable[..., Awaitable[None]] | None = None,
        discard_response_keys: Callable[..., Awaitable[int]] | None = None,
    ) -> None:
        if request_response is None:
            async def request_response(
                instructions: str | None = None,
                **_: Any,
            ) -> None:
                await realtime.request_response(instructions)
        if origin_queries_by_question is None:
            origin_queries_by_question = {}
        if origin_queries_by_id is None:
            origin_queries_by_id = {}
        if discard_response_keys is None:
            async def discard_response_keys(
                keys: set[str],
                **_: Any,
            ) -> int:
                return 0
        if function_call.name != "query_origin_agent":
            await realtime.send_tool_output(
                call_id=function_call.call_id,
                output={"status": "failed", "error": f"Unsupported tool: {function_call.name}"},
            )
            await request_response("Tell the caller that tool is unavailable.")
            return
        question = str(
            function_call.arguments.get("question")
            or function_call.arguments.get("query")
            or ""
        ).strip()
        record = self.store.get(voice_session_id)
        projection = record.projection if record is not None else {}
        if not question:
            projection_answer = _projected_startup_task_answer(projection)
            if projection_answer is not None:
                self._append_debug_event(
                    voice_session_id,
                    "origin_query.empty_question_answered_from_projection",
                    metadata={
                        "realtime_call_id": function_call.call_id,
                        "reason": "projected_task",
                        "answer_snippet": _debug_snippet(projection_answer),
                    },
                )
                await realtime.send_tool_output(
                    call_id=function_call.call_id,
                    output={
                        "status": "answered",
                        "source": "projection",
                        "answer": projection_answer,
                    },
                )
                await request_response(
                    _PROJECTED_TASK_RESPONSE_INSTRUCTIONS,
                    reason="projected_task",
                    key=_PROJECTED_TASK_RESPONSE_KEY,
                )
                return
            await realtime.send_tool_output(
                call_id=function_call.call_id,
                output={"status": "failed", "error": "question is required"},
            )
            await request_response(
                _MISSING_TASK_CLARIFICATION_INSTRUCTIONS,
                reason="missing_task_clarification",
                key=_MISSING_TASK_CLARIFICATION_RESPONSE_KEY,
            )
            return
        projection_answer = _projected_task_answer(
            question,
            projection,
        )
        if projection_answer is not None:
            self._append_debug_event(
                voice_session_id,
                "origin_query.answered_from_projection",
                metadata={
                    "realtime_call_id": function_call.call_id,
                    "reason": "projected_task",
                    "question_snippet": _debug_snippet(question),
                    "answer_snippet": _debug_snippet(projection_answer),
                },
            )
            await realtime.send_tool_output(
                call_id=function_call.call_id,
                output={
                    "status": "answered",
                    "source": "projection",
                    "answer": projection_answer,
                },
            )
            await request_response(
                _PROJECTED_TASK_RESPONSE_INSTRUCTIONS,
                reason="projected_task",
                key=_PROJECTED_TASK_RESPONSE_KEY,
            )
            return
        query_key = _voice_query_key(question)
        argument_query_id = str(function_call.arguments.get("query_id") or "").strip()
        state = origin_queries_by_id.get(argument_query_id) if argument_query_id else None
        state = state or origin_queries_by_question.get(query_key)
        if state is not None:
            if state.status in {"failed", "timeout"} and state.failure_reused:
                origin_queries_by_question.pop(state.question_key, None)
                origin_queries_by_id.pop(state.query_id, None)
                if delegated_queries is not None:
                    delegated_queries.pop(query_key, None)
                    delegated_queries.pop(state.question_key, None)
            else:
                await realtime.send_tool_output(
                    call_id=function_call.call_id,
                    output=_origin_query_tool_output(state.query, deduplicated=True),
                )
                if state.status in {"failed", "timeout"}:
                    state.failure_reused = True
                if state.status != "pending":
                    await self._queue_terminal_origin_query_response(
                        voice_session_id,
                        state,
                        request_response=request_response,
                    )
                return
        if not media_controller.configured:
            await realtime.send_tool_output(
                call_id=function_call.call_id,
                output={"status": "failed", "error": "lookup is not configured"},
            )
            await request_response(
                "Tell the caller you cannot look that up right now. "
                "Do not mention internal agent handoffs."
            )
            return
        if delegated_queries is not None and query_key in delegated_queries:
            await realtime.send_tool_output(
                call_id=function_call.call_id,
                output={
                    "status": "pending",
                    "query_id": delegated_queries[query_key],
                    "deduplicated": True,
                },
            )
            return
        transcript_excerpt = record.transcript if record is not None else None
        try:
            query = await media_controller.create_query(
                voice_session_id,
                question=question,
                transcript_excerpt=transcript_excerpt,
                metadata={
                    "reason": function_call.arguments.get("reason"),
                    "realtime_call_id": function_call.call_id,
                },
                deadline_seconds=int(self.config.voice_query_timeout_seconds),
            )
        except Exception as exc:
            logger.warning(
                "voice_query_create_failed voice_session_id=%s call_id=%s",
                voice_session_id,
                function_call.call_id,
                exc_info=True,
            )
            self._append_debug_event(
                voice_session_id,
                "origin_query.create_failed",
                metadata={
                    "reason": type(exc).__name__,
                    "realtime_call_id": function_call.call_id,
                    "question_snippet": _debug_snippet(question),
                    "message_snippet": _debug_snippet(exc),
                },
            )
            await realtime.send_tool_output(
                call_id=function_call.call_id,
                output={"status": "failed", "error": str(exc)},
            )
            await request_response(
                "Tell the caller you cannot complete that lookup right now. "
                "Do not mention internal agent handoffs."
            )
            return
        query_id = str(query.get("query_id") or "").strip()
        if delegated_queries is not None and query_id:
            delegated_queries[query_key] = query_id
        self._append_debug_event(
            voice_session_id,
            "origin_query.created",
            metadata={
                "query_id": query_id,
                "realtime_call_id": function_call.call_id,
                "reason": function_call.arguments.get("reason"),
                "question_snippet": _debug_snippet(question),
            },
        )
        self.store.append_transcript_event(
            voice_session_id,
            speaker="assistant",
            text=f"Lookup requested: {question}",
            metadata={"query_id": query_id, "event_type": "voice_query_created"},
        )
        await realtime.send_tool_output(
            call_id=function_call.call_id,
            output=_origin_query_tool_output(query),
        )
        status = str(query.get("status") or "pending").strip() or "pending"
        state = OriginQueryState(
            query_id=query_id,
            question=question,
            question_key=query_key,
            status=status,
            query=dict(query),
            completed_event=asyncio.Event(),
        )
        origin_queries_by_question[query_key] = state
        if query_id:
            origin_queries_by_id[query_id] = state
        await discard_response_keys(
            {_PROJECTED_TASK_RESPONSE_KEY},
            reason="origin_query_created",
        )
        if status in {"answered", "failed", "timeout"}:
            await self._handle_terminal_origin_query_result(
                voice_session_id,
                state,
                query,
                realtime,
                request_response=request_response,
                event_type="origin_query.answered" if status != "timeout" else "origin_query.timeout",
                started_at=asyncio.get_running_loop().time(),
            )
            return
        ack_task = asyncio.create_task(
            self._queue_delayed_origin_query_ack(
                voice_session_id,
                state,
                stop_event,
                request_response=request_response,
            )
        )
        query_tasks.add(ack_task)
        ack_task.add_done_callback(lambda completed: query_tasks.discard(completed))
        poll_task = asyncio.create_task(
            self._poll_origin_query_result(
                voice_session_id,
                query_id,
                media_controller,
                realtime,
                stop_event,
                state=state,
                request_response=request_response,
            )
        )
        query_tasks.add(poll_task)
        poll_task.add_done_callback(lambda completed: query_tasks.discard(completed))

    async def _poll_origin_query_result(
        self,
        voice_session_id: str,
        query_id: str,
        media_controller: VoiceMediaControllerClient,
        realtime: OpenAIRealtimeSession,
        stop_event: asyncio.Event,
        *,
        state: OriginQueryState | None = None,
        request_response: Callable[..., Awaitable[None]] | None = None,
    ) -> None:
        if request_response is None:
            async def request_response(
                instructions: str | None = None,
                **_: Any,
            ) -> None:
                await realtime.request_response(instructions)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.config.voice_query_timeout_seconds
        started_at = loop.time()
        if state is None:
            state = OriginQueryState(
                query_id=query_id,
                question="",
                question_key="",
                query={"query_id": query_id, "status": "pending"},
                completed_event=asyncio.Event(),
            )
        poll_attempt = 0
        while not stop_event.is_set() and loop.time() < deadline:
            await asyncio.sleep(self.config.voice_query_poll_interval_seconds)
            query = await media_controller.get_query(voice_session_id, query_id)
            poll_attempt += 1
            status = str(query.get("status") or "").strip()
            if status in {"answered", "failed"}:
                await self._handle_terminal_origin_query_result(
                    voice_session_id,
                    state,
                    query,
                    realtime,
                    request_response=request_response,
                    event_type="origin_query.answered",
                    started_at=started_at,
                )
                return
            self._append_debug_event(
                voice_session_id,
                "origin_query.poll_pending",
                metadata={
                    "query_id": query_id,
                    "reason": status or "pending",
                    "status": status or "pending",
                    "poll_attempt": poll_attempt,
                    "elapsed_ms": int(max(0.0, loop.time() - started_at) * 1000),
                },
            )
        if not stop_event.is_set():
            query = await media_controller.mark_query_timeout(voice_session_id, query_id)
            await self._handle_terminal_origin_query_result(
                voice_session_id,
                state,
                query,
                realtime,
                request_response=request_response,
                event_type="origin_query.timeout",
                started_at=started_at,
            )

    async def _queue_delayed_origin_query_ack(
        self,
        voice_session_id: str,
        state: OriginQueryState,
        stop_event: asyncio.Event,
        *,
        request_response: Callable[..., Awaitable[None]],
    ) -> None:
        completed = state.completed_event
        if completed is not None:
            try:
                await asyncio.wait_for(
                    completed.wait(),
                    timeout=_ORIGIN_QUERY_ACK_GRACE_SECONDS,
                )
                return
            except TimeoutError:
                pass
        else:
            await asyncio.sleep(_ORIGIN_QUERY_ACK_GRACE_SECONDS)
        if stop_event.is_set() or state.status != "pending" or state.ack_response_queued:
            return
        state.ack_response_queued = True
        response_instructions = _ORIGIN_QUERY_HOLD_RESPONSE_INSTRUCTIONS
        self._append_debug_event(
            voice_session_id,
            "origin_query.hold_offer_requested",
            metadata={
                "query_id": state.query_id,
                "reason": "query_pending_hold_offer",
                **_instruction_debug_metadata(response_instructions),
            },
        )
        await request_response(
            response_instructions,
            reason="query_pending_hold_offer",
            key=_origin_query_response_key(state.query_id, "ack"),
            supersede_keys={_PROJECTED_TASK_RESPONSE_KEY},
        )

    async def _handle_terminal_origin_query_result(
        self,
        voice_session_id: str,
        state: OriginQueryState,
        query: dict[str, Any],
        realtime: OpenAIRealtimeSession,
        *,
        request_response: Callable[..., Awaitable[None]],
        event_type: str,
        started_at: float,
    ) -> None:
        loop = asyncio.get_running_loop()
        status = str(query.get("status") or "").strip() or state.status
        if event_type == "origin_query.timeout":
            status = "timeout"
        state.status = status
        state.query = dict(query)
        if state.completed_event is not None:
            state.completed_event.set()
        answer = str(query.get("answer") or query.get("error") or "").strip()
        self._append_debug_event(
            voice_session_id,
            event_type,
            metadata={
                "query_id": state.query_id,
                "reason": status,
                "status": status,
                "elapsed_ms": int(max(0.0, loop.time() - started_at) * 1000),
                "answer_snippet": _debug_snippet(answer),
            },
        )
        if not state.result_injected:
            await realtime.inject_origin_query_result(query, request_response=False)
            state.result_injected = True
            self._append_debug_event(
                voice_session_id,
                "origin_query.result_injected",
                metadata={
                    "query_id": state.query_id,
                    "reason": status,
                    "status": status,
                    "answer_snippet": _debug_snippet(answer),
                },
            )
        await self._queue_terminal_origin_query_response(
            voice_session_id,
            state,
            request_response=request_response,
        )
        if answer and not state.transcript_recorded:
            state.transcript_recorded = True
            self.store.append_transcript_event(
                voice_session_id,
                speaker="origin_agent",
                text=answer,
                metadata={"query_id": state.query_id, "event_type": "voice_query_answered"},
            )

    async def _queue_terminal_origin_query_response(
        self,
        voice_session_id: str,
        state: OriginQueryState,
        *,
        request_response: Callable[..., Awaitable[None]],
    ) -> None:
        status = state.status
        if status == "timeout":
            if state.timeout_response_queued:
                return
            state.timeout_response_queued = True
            response_instructions = (
                "Tell the caller the lookup did not finish in time. "
                "Do not mention internal agent handoffs or say the query is still pending."
            )
            response_kind = "timeout"
            response_reason = "query_timeout"
        else:
            if state.answer_response_queued:
                return
            state.answer_response_queued = True
            if status == "failed":
                response_instructions = (
                    "Tell the caller, in one short sentence, that you could not complete the lookup "
                    "right now. "
                    "Do not mention internal agent handoffs or say the query is still pending."
                )
            else:
                response_instructions = _ORIGIN_QUERY_ANSWER_RESPONSE_INSTRUCTIONS
            response_kind = "answer"
            response_reason = "query_result"
        self._append_debug_event(
            voice_session_id,
            "origin_query.response_requested",
            metadata={
                "query_id": state.query_id,
                "reason": response_reason,
                **_instruction_debug_metadata(response_instructions),
            },
        )
        await request_response(
            response_instructions,
            reason=response_reason,
            key=_origin_query_response_key(state.query_id, response_kind),
            supersede_keys={
                _origin_query_response_key(state.query_id, "ack"),
            },
        )

    async def _wait_for_asterisk_end(
        self,
        voice_session_id: str,
        handles: AsteriskCallHandles,
        stop_event: asyncio.Event,
        *,
        websocket: Any | None = None,
    ) -> None:
        if websocket is not None:
            await self._consume_asterisk_events(
                voice_session_id,
                handles,
                stop_event,
                websocket,
            )
            return
        async with self.ari_client.event_stream() as event_websocket:
            await self._consume_asterisk_events(
                voice_session_id,
                handles,
                stop_event,
                event_websocket,
            )

    async def _consume_asterisk_events(
        self,
        voice_session_id: str,
        handles: AsteriskCallHandles,
        stop_event: asyncio.Event,
        websocket: Any,
    ) -> None:
        while not stop_event.is_set():
            try:
                raw = await asyncio.wait_for(websocket.recv(), timeout=0.5)
            except TimeoutError:
                continue
            event = json.loads(raw if isinstance(raw, str) else raw.decode("utf-8"))
            event_type = str(event.get("type") or "")
            channel_id = _ari_event_channel_id(event)
            if channel_id != handles.call_channel_id:
                continue
            if event_type == "ChannelStateChange" and _ari_channel_state(event) == "Up":
                self._append_debug_event(
                    voice_session_id,
                    "asterisk.channel_up",
                    metadata={
                        "ari_event_type": event_type,
                        "channel_id": channel_id,
                        "state": "Up",
                    },
                )
                self.store.update_status(voice_session_id, "active")
            if event_type in {"ChannelHangupRequest", "ChannelDestroyed", "StasisEnd"}:
                cause = event.get("cause_txt") or event.get("cause") or "hangup"
                self._append_debug_event(
                    voice_session_id,
                    _asterisk_debug_event_type(event_type),
                    metadata={
                        "ari_event_type": event_type,
                        "channel_id": channel_id,
                        "reason": cause,
                        "status": _ari_hangup_status(event),
                    },
                )
                self.store.complete(
                    voice_session_id,
                    status=_ari_hangup_status(event),
                    summary=f"Call ended: {cause}",
                )
                stop_event.set()
                return

    async def _cleanup_asterisk(self, record: DidwwBridgeCallRecord) -> None:
        await self.ari_client.hangup_channel(record.ari_external_media_channel_id)
        await self.ari_client.hangup_channel(record.ari_channel_id)
        await self.ari_client.destroy_bridge(record.ari_bridge_id)

    async def _sync_inbound_voice_session_to_gateway(
        self,
        record: DidwwBridgeCallRecord,
        realtime: dict[str, Any],
    ) -> None:
        if record.direction != "inbound":
            return
        voice_session_id = record.gateway_voice_session_id or record.voice_session_id
        if not voice_session_id:
            return
        controller = VoiceMediaControllerClient.from_realtime(self.config, realtime)
        if not controller.configured:
            self._append_debug_event(
                record.voice_session_id,
                "inbound_phone_call.gateway_sync_skipped",
                metadata={"reason": "voice media controller URL is not configured"},
            )
            return
        try:
            await controller.sync_voice_session(voice_session_id, record.response_payload())
            self._append_debug_event(
                record.voice_session_id,
                "inbound_phone_call.gateway_synced",
                metadata={"gateway_voice_session_id": voice_session_id},
            )
        except Exception as exc:
            self._append_debug_event(
                record.voice_session_id,
                "inbound_phone_call.gateway_sync_failed",
                metadata={
                    "gateway_voice_session_id": voice_session_id,
                    "reason": _debug_snippet(exc),
                },
            )

    def _append_debug_event(
        self,
        voice_session_id: str,
        event_type: str,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        try:
            self.store.append_debug_event(
                voice_session_id,
                event_type,
                metadata=metadata or {},
            )
        except Exception:
            logger.exception(
                "didww_bridge_debug_event_failed voice_session_id=%s event_type=%s",
                voice_session_id,
                event_type,
            )

    def _start_background_call(
        self,
        record: DidwwBridgeCallRecord,
        payload: PhoneCallStartRequest,
    ) -> None:
        task = asyncio.create_task(self.run_call(record, payload))
        self._tasks[record.call_id] = task
        self._track_background_task(task)

        def discard(completed: asyncio.Task[None]) -> None:
            self._tasks.pop(record.call_id, None)
            try:
                completed.result()
            except asyncio.CancelledError:
                return
            except Exception:
                logger.exception("didww_bridge_background_task_failed call_id=%s", record.call_id)

        task.add_done_callback(discard)

    def _auth_error(self, request: Request) -> JSONResponse | None:
        token = os.environ.get(self.config.bridge_token_env, "").strip()
        if not token:
            return JSONResponse(
                {"error": f"Bridge token env var {self.config.bridge_token_env!r} is not set"},
                status_code=503,
            )
        header = request.headers.get("authorization", "")
        if header != f"Bearer {token}":
            return JSONResponse({"error": "Unauthorized"}, status_code=401)
        return None


def create_app(
    config: DidwwSipBridgeConfig | None = None,
    *,
    store: DidwwBridgeStore | None = None,
    ari_client: AsteriskAriClient | None = None,
    realtime_factory: Callable[[dict[str, Any], dict[str, Any]], OpenAIRealtimeSession] | None = None,
    rtp_endpoint_factory: Callable[[], RtpUdpEndpoint] | None = None,
    start_background_tasks: bool = True,
) -> Starlette:
    cfg = config or load_config()
    return DidwwSipBridgeService(
        cfg,
        store=store,
        ari_client=ari_client,
        realtime_factory=realtime_factory,
        rtp_endpoint_factory=rtp_endpoint_factory,
        start_background_tasks=start_background_tasks,
    ).create_app()


def load_config(runtime_dir: Path | None = None) -> DidwwSipBridgeConfig:
    resolved_runtime_dir = runtime_dir or resolve_runtime_dir(runtime_dir=None)
    return DidwwSipBridgeConfig(
        host=_env("DIDWW_SIP_BRIDGE_HOST", "127.0.0.1"),
        port=_env_int("DIDWW_SIP_BRIDGE_PORT", 11888),
        runtime_dir=resolved_runtime_dir,
        bridge_token_env=_env("DIDWW_SIP_BRIDGE_TOKEN_ENV", "DIDWW_SIP_BRIDGE_TOKEN"),
        enable_inbound_phone_calls=_env_bool(
            "DIDWW_SIP_BRIDGE_ENABLE_INBOUND_PHONE_CALLS",
            False,
        ),
        inbound_gateway_url=_env_optional(
            "DIDWW_SIP_BRIDGE_INBOUND_GATEWAY_URL",
            None,
        ),
        inbound_gateway_token_env=_env(
            "DIDWW_SIP_BRIDGE_INBOUND_GATEWAY_TOKEN_ENV",
            "VOICE_MEDIA_CONTROLLER_TOKEN",
        ),
        openai_api_key_env=_env("OPENAI_REALTIME_API_KEY_ENV", "OPENAI_API_KEY"),
        openai_realtime_model=_env("OPENAI_REALTIME_MODEL", "gpt-realtime-2"),
        openai_realtime_api_base_url=_env(
            "OPENAI_REALTIME_API_BASE_URL", "https://api.openai.com/v1"
        ),
        openai_realtime_voice=_env("OPENAI_REALTIME_VOICE", "marin"),
        openai_realtime_idle_timeout_ms=_env_int("OPENAI_REALTIME_IDLE_TIMEOUT_MS", 10000),
        openai_realtime_transcription_model=_env_optional(
            "OPENAI_REALTIME_TRANSCRIPTION_MODEL", "gpt-4o-transcribe"
        ),
        openai_realtime_input_noise_reduction_type=_env(
            "OPENAI_REALTIME_INPUT_NOISE_REDUCTION_TYPE", "near_field"
        ),
        openai_realtime_turn_detection_type=_env(
            "OPENAI_REALTIME_TURN_DETECTION_TYPE", "server_vad"
        ),
        openai_realtime_vad_threshold=_env_float("OPENAI_REALTIME_VAD_THRESHOLD", 0.70),
        openai_realtime_vad_prefix_padding_ms=_env_int(
            "OPENAI_REALTIME_VAD_PREFIX_PADDING_MS", 300
        ),
        openai_realtime_vad_silence_duration_ms=_env_int(
            "OPENAI_REALTIME_VAD_SILENCE_DURATION_MS", 800
        ),
        asterisk_ari_url=_env("ASTERISK_ARI_URL", "http://127.0.0.1:8088/ari"),
        asterisk_ari_user=_env("ASTERISK_ARI_USER", "agent-sandbox"),
        asterisk_ari_password_env=_env("ASTERISK_ARI_PASSWORD_ENV", "ASTERISK_ARI_PASSWORD"),
        asterisk_ari_app=_env("ASTERISK_ARI_APP", "agent_sandbox_didww"),
        asterisk_pjsip_endpoint=_env("ASTERISK_PJSIP_ENDPOINT", "didww"),
        asterisk_endpoint_template=_env(
            "ASTERISK_ENDPOINT_TEMPLATE", "PJSIP/{number}@{trunk}"
        ),
        external_media_host=_env("DIDWW_SIP_BRIDGE_EXTERNAL_MEDIA_HOST", "127.0.0.1"),
        rtp_bind_host=_env("DIDWW_SIP_BRIDGE_RTP_BIND_HOST", "127.0.0.1"),
        rtp_port_min=_env_int("DIDWW_SIP_BRIDGE_RTP_PORT_MIN", 41000),
        rtp_port_max=_env_int("DIDWW_SIP_BRIDGE_RTP_PORT_MAX", 41100),
        rtp_payload_type=_env_int("DIDWW_SIP_BRIDGE_RTP_PAYLOAD_TYPE", 8),
        media_format=_env("DIDWW_SIP_BRIDGE_MEDIA_FORMAT", "alaw"),
        sample_rate_hz=_env_int("DIDWW_SIP_BRIDGE_SAMPLE_RATE_HZ", 8000),
        frame_duration_ms=_env_int("DIDWW_SIP_BRIDGE_FRAME_DURATION_MS", 20),
        call_timeout_seconds=_env_float("DIDWW_SIP_BRIDGE_CALL_TIMEOUT_SECONDS", 45.0),
        max_call_seconds=_env_float("DIDWW_SIP_BRIDGE_MAX_CALL_SECONDS", 1800.0),
        request_timeout_seconds=_env_float("DIDWW_SIP_BRIDGE_REQUEST_TIMEOUT_SECONDS", 15.0),
        max_concurrent_calls=_env_int("DIDWW_SIP_BRIDGE_MAX_CONCURRENT_CALLS", 4),
        voice_query_poll_interval_seconds=_env_float(
            "DIDWW_SIP_BRIDGE_VOICE_QUERY_POLL_INTERVAL_SECONDS", 1.0
        ),
        voice_query_timeout_seconds=_env_float(
            "DIDWW_SIP_BRIDGE_VOICE_QUERY_TIMEOUT_SECONDS", 90.0
        ),
        barge_in_delay_ms=_env_int("DIDWW_SIP_BRIDGE_BARGE_IN_DELAY_MS", 700),
        backchannel_max_words=_env_int("DIDWW_SIP_BRIDGE_BACKCHANNEL_MAX_WORDS", 3),
        backchannel_phrases=_env_list(
            "DIDWW_SIP_BRIDGE_BACKCHANNEL_PHRASES",
            list(_DEFAULT_BACKCHANNEL_PHRASES),
        ),
    )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run the DIDWW SIP/OpenAI Realtime bridge.")
    parser.add_argument("--runtime-dir", type=Path, default=None)
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    args = parser.parse_args(argv)
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
    config = load_config(runtime_dir=args.runtime_dir)
    if args.host is not None:
        config.host = args.host
    if args.port is not None:
        config.port = args.port
    uvicorn.run(create_app(config), host=config.host, port=config.port)


def initial_inbound_call_instructions(
    inbound_instructions: dict[str, Any],
    projection: dict[str, Any],
) -> str:
    route_prompt = _optional_text(inbound_instructions.get("route_prompt")) or (
        _extract_projected_task_prompt(projection) or ""
    )
    startup_text = _optional_text(inbound_instructions.get("startup_text"))
    opening = startup_text or route_prompt or "<missing>"
    return (
        "Start the inbound phone call now. Greet the caller naturally using the supplied "
        "startup text when present, then continue from the route prompt. Do not use "
        "outbound startup wording, do not narrate internal handoff or context checks, "
        "and do not ask for the same script or instructions again.\n\n"
        f"Caller-facing opening:\n{opening}\n\n"
        f"Route prompt:\n{route_prompt or '<missing>'}"
    )


def projection_to_instructions(projection: dict[str, Any]) -> str:
    if _projection_is_inbound(projection):
        return _inbound_projection_to_instructions(projection)
    instructions = [
        "You are the same Truman agent represented by this projected voice session.",
        "Use the provided origin context faithfully. Do not claim to be a separate handoff agent.",
        (
            "Outbound startup contract: treat the supplied Current task as authoritative "
            "first-turn task state. If it includes a complete opening or objective, begin "
            "with it immediately. Do not ask internally for the same script, "
            "instructions, objective, expected behavior, or call context. Do not say you "
            "are doing a context check, narrate task-missing state, or ask open-ended "
            "what-to-do questions when the Current task already provides the task."
        ),
        (
            "Speak like a natural phone participant: keep normal answers to one or two short "
            "spoken paragraphs unless the caller asks for detail, avoid repeating yourself, "
            "and pause naturally after a long answer instead of immediately asking whether "
            "the caller is still there."
        ),
        (
            "Treat short acknowledgements such as ok, okay, uh-huh, good, thanks, and right "
            "as backchannels unless they include a new request. After a backchannel, do not "
            "repeat the prior answer; continue only if you were mid-thought, otherwise pause."
        ),
        (
            "Use the query_origin_agent tool only for missing origin-side files, tools, "
            "repository state, memory, or live session state that is not already in the "
            "projected task/context. While an origin query is pending, do not fill the call "
            "with generic startup scaffolding such as asking what to do, saying you are "
            "doing a context check, or narrating task-missing state. When the origin answer "
            "arrives, give the answer directly without recapping the pending state."
        ),
        (
            "If the supplied Current task is not actionable enough to start and the missing "
            "data must come from the caller, ask exactly one concise caller-facing "
            "clarification question, then wait."
        ),
        (
            "Preserve decisions and follow-up actions internally after the call, but do not "
            "narrate internal bookkeeping, transcript cleanup, or handoff notes aloud."
        ),
        (
            "After a natural goodbye or closing, and only when the caller has no further "
            "request, call end_call to end the phone call. Do not call it during active "
            "discussion or before saying goodbye."
        ),
    ]
    origin = projection.get("origin")
    if isinstance(origin, dict):
        instructions.append(f"Origin: {json.dumps(sanitize_voice_metadata(origin), sort_keys=True)}")
    prompt = projection.get("prompt")
    if isinstance(prompt, str) and prompt.strip():
        instructions.append(f"Current task: {prompt.strip()}")
    context = projection.get("context")
    if isinstance(context, str) and context.strip():
        instructions.append(f"Context:\n{context.strip()}")
    return "\n\n".join(instructions)


def _inbound_projection_to_instructions(projection: dict[str, Any]) -> str:
    instructions = [
        "You are the same Truman agent represented by this projected voice session.",
        "Use the provided origin context faithfully. Do not claim to be a separate handoff agent.",
        (
            "Inbound startup contract: treat the supplied route prompt as the caller-facing "
            "starting context. If the route includes startup text, greet the caller with it "
            "naturally. Do not use outbound startup wording, do not ask for the same script "
            "or instructions again, and do not narrate context checks or internal handoffs."
        ),
        (
            "Speak like a natural phone participant: keep normal answers to one or two short "
            "spoken paragraphs unless the caller asks for detail, avoid repeating yourself, "
            "and pause naturally after a long answer instead of immediately asking whether "
            "the caller is still there."
        ),
        (
            "Treat short acknowledgements such as ok, okay, uh-huh, good, thanks, and right "
            "as backchannels unless they include a new request. After a backchannel, do not "
            "repeat the prior answer; continue only if you were mid-thought, otherwise pause."
        ),
        (
            "Use the query_origin_agent tool only for missing origin-side files, tools, "
            "repository state, memory, or live session state that is not already in the "
            "projected task/context. While an origin query is pending, do not fill the call "
            "with generic startup scaffolding. When the origin answer arrives, give the "
            "answer directly without recapping the pending state."
        ),
        (
            "If the supplied route prompt is not actionable enough to start and the missing "
            "data must come from the caller, ask exactly one concise caller-facing "
            "clarification question, then wait."
        ),
        (
            "Preserve decisions and follow-up actions internally after the call, but do not "
            "narrate internal bookkeeping, transcript cleanup, or handoff notes aloud."
        ),
        (
            "After a natural goodbye or closing, and only when the caller has no further "
            "request, call end_call to end the phone call. Do not call it during active "
            "discussion or before saying goodbye."
        ),
    ]
    origin = projection.get("origin")
    if isinstance(origin, dict):
        instructions.append(f"Origin: {json.dumps(sanitize_voice_metadata(origin), sort_keys=True)}")
    caller_metadata = projection.get("caller_metadata")
    if isinstance(caller_metadata, dict):
        instructions.append(
            f"Caller metadata: {json.dumps(sanitize_voice_metadata(caller_metadata), sort_keys=True)}"
        )
    prompt = _extract_projected_task_prompt(projection)
    if isinstance(prompt, str) and prompt.strip():
        instructions.append(f"Route prompt: {prompt.strip()}")
    context = projection.get("context")
    if isinstance(context, str) and context.strip():
        instructions.append(f"Context:\n{context.strip()}")
    return "\n\n".join(instructions)


def _projection_is_inbound(projection: dict[str, Any]) -> bool:
    caller_metadata = projection.get("caller_metadata")
    if isinstance(caller_metadata, dict) and str(caller_metadata.get("direction") or "").strip() == "inbound":
        return True
    metadata = projection.get("metadata")
    if isinstance(metadata, dict) and str(metadata.get("direction") or "").strip() == "inbound":
        return True
    return str(projection.get("direction") or "").strip() == "inbound"


def initial_outbound_call_instructions(projection: dict[str, Any]) -> str:
    task_prompt = _extract_projected_task_prompt(projection) or ""
    if _projection_has_complete_startup_objective(projection, task_prompt):
        return (
            "Start the outbound phone call now using the supplied Current task as "
            "authoritative first-turn task state. The first audible assistant turn must "
            "begin from the task-specific opening/objective below. Do not ask for the "
            "script, instructions, expected behavior, or call context; do not say you are "
            "doing a context check or narrate task-missing state; do not ask open-ended "
            "what-to-do questions.\n\n"
            f"Caller-facing opening/objective:\n{task_prompt}"
        )
    supplied = task_prompt or "<missing>"
    return (
        "Start the outbound phone call now. The supplied Current task is not actionable "
        "enough to start, so ask exactly one concise caller-facing clarification question "
        "about the call objective, then wait. Do not mention origin context, missing task "
        "state, internal handoff, or context-check narration.\n\n"
        f"Supplied task:\n{supplied}"
    )


def transcript_from_events(events: list[VoiceTranscriptEvent]) -> str | None:
    lines = []
    for event in events:
        speaker = event.speaker or "speaker"
        lines.append(f"{speaker}: {event.text}")
    return "\n".join(lines) if lines else None


def _sanitize_debug_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    jsonable = _debug_jsonable(metadata)
    sanitized = sanitize_voice_metadata(jsonable)
    truncated = _truncate_debug_value(sanitized)
    if not isinstance(truncated, dict):
        return {}
    _restore_debug_hashes(jsonable, truncated)
    return truncated


def _debug_jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _debug_jsonable(raw_value) for key, raw_value in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_debug_jsonable(item) for item in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _truncate_debug_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _truncate_debug_value(raw_value) for key, raw_value in value.items()}
    if isinstance(value, list):
        return [_truncate_debug_value(item) for item in value[:20]]
    if isinstance(value, str):
        return _debug_snippet(value, limit=_DEBUG_SNIPPET_LIMIT)
    return value


def _restore_debug_hashes(original: Any, sanitized: Any) -> None:
    if not isinstance(original, dict) or not isinstance(sanitized, dict):
        return
    for key, raw_value in original.items():
        text_key = str(key)
        if text_key.endswith("_sha256") and isinstance(raw_value, str):
            normalized = raw_value.strip().lower()
            if re.fullmatch(r"[0-9a-f]{64}", normalized):
                sanitized[text_key] = normalized
                continue
        _restore_debug_hashes(raw_value, sanitized.get(text_key))


def _debug_snippet(value: object, *, limit: int = _DEBUG_SNIPPET_LIMIT) -> str | None:
    text = re.sub(r"\s+", " ", str(value or "").strip())
    if not text:
        return None
    sanitized = sanitize_voice_metadata(text)
    text = str(sanitized or "").strip()
    if len(text) <= limit:
        return text
    return f"{text[: max(0, limit - 3)].rstrip()}..."


def _instruction_debug_metadata(instructions: str | None) -> dict[str, Any]:
    normalized = str(instructions or "").strip()
    if not normalized:
        return {"instruction_present": False}
    return {
        "instruction_present": True,
        "instruction_sha256": hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
        "instruction_length": len(normalized),
        "instruction_snippet": _debug_snippet(normalized),
    }


def _log_debug_event(
    record: DidwwBridgeCallRecord,
    event_type: str,
    metadata: dict[str, Any],
) -> None:
    logger.info(
        "didww_bridge_debug_event voice_session_id=%s call_id=%s event_type=%s "
        "response_id=%s query_id=%s reason=%s elapsed_ms=%s duration_ms=%s metadata=%s",
        record.voice_session_id,
        record.call_id,
        event_type,
        _debug_log_value(metadata.get("response_id")),
        _debug_log_value(metadata.get("query_id")),
        _debug_log_value(metadata.get("reason")),
        _debug_log_value(metadata.get("elapsed_ms")),
        _debug_log_value(metadata.get("duration_ms")),
        json.dumps(metadata, sort_keys=True, ensure_ascii=True),
    )


def _debug_log_value(value: Any) -> str:
    if value is None:
        return "-"
    return str(value)


def _normalize_backchannel_phrase(value: object) -> str:
    text = str(value or "").strip().casefold()
    if not text:
        return ""
    text = text.replace("'", "")
    text = re.sub(r"[-_]+", " ", text)
    text = re.sub(r"[^a-z0-9\s]+", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _word_count(value: object) -> int:
    normalized = _normalize_backchannel_phrase(value)
    if not normalized:
        return 0
    return len(normalized.split())


def _is_backchannel_transcript(
    transcript_text: object,
    *,
    phrases: list[str],
    max_words: int,
) -> bool:
    normalized = _normalize_backchannel_phrase(transcript_text)
    if not normalized or _word_count(normalized) > max_words:
        return False
    normalized_phrases = {
        phrase
        for phrase in (_normalize_backchannel_phrase(item) for item in phrases)
        if phrase
    }
    if normalized in normalized_phrases:
        return True
    return all(word in normalized_phrases for word in normalized.split())


def _is_origin_query_hold_ack(transcript_text: object) -> bool:
    normalized = _normalize_backchannel_phrase(transcript_text)
    if not normalized or _word_count(normalized) > 6:
        return False
    hold_ack_phrases = {
        "ill wait",
        "i will wait",
        "ill just wait",
        "i will just wait",
        "yes",
        "yes please",
        "yeah",
        "yeah please",
        "yep",
        "yep please",
        "sure",
        "sure please",
        "ok",
        "okay",
        "nah ill wait",
        "nah i will wait",
        "nah ill just wait",
        "nah i will just wait",
        "no ill wait",
        "no i will wait",
        "no ill just wait",
        "no i will just wait",
        "no thanks ill wait",
        "no thanks i will wait",
    }
    return normalized in hold_ack_phrases


def _is_origin_query_response_key(key: object) -> bool:
    return str(key or "").startswith("query:")


def _origin_query_response_key(query_id: str, kind: str) -> str:
    return f"query:{query_id}:{kind}"


def _origin_query_tool_output(
    query: dict[str, Any],
    *,
    deduplicated: bool = False,
) -> dict[str, Any]:
    status = str(query.get("status") or "pending").strip() or "pending"
    output: dict[str, Any] = {"status": status}
    query_id = str(query.get("query_id") or "").strip()
    if query_id:
        output["query_id"] = query_id
    answer = str(query.get("answer") or "").strip()
    error = str(query.get("error") or "").strip()
    if answer:
        output["answer"] = answer
    if error:
        output["error"] = error
    citations = query.get("citations")
    if isinstance(citations, list) and citations:
        output["citations"] = citations
    if deduplicated:
        output["deduplicated"] = True
    return output


def _projected_task_answer(question: str, projection: dict[str, Any]) -> str | None:
    if not _question_asks_projected_task(question):
        return None
    return _projected_startup_task_answer(projection)


def _projected_startup_task_answer(projection: dict[str, Any]) -> str | None:
    task_prompt = _extract_projected_task_prompt(projection)
    if not task_prompt:
        return None
    if not _projection_has_complete_startup_objective(projection, task_prompt):
        return None
    return f"The projected calling task is already authoritative: {task_prompt}"


def _projection_has_complete_startup_objective(
    projection: dict[str, Any],
    task_prompt: str,
) -> bool:
    contract = projection.get("startup_contract")
    if isinstance(contract, dict) and "complete_objective" in contract:
        return bool(contract.get("complete_objective"))
    return _voice_task_looks_actionable(task_prompt)


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


def _question_asks_projected_task(question: str) -> bool:
    normalized = _voice_query_key(question)
    if not normalized:
        return False
    task_terms = (
        "calling task",
        "call task",
        "call objective",
        "current task",
        "voice task",
        "task prompt",
        "task details",
        "why am i calling",
        "why are we calling",
        "what am i calling",
        "what are we calling",
        "what is this call about",
        "what's this call about",
        "what am i supposed to do",
        "what are we supposed to do",
        "what should i do",
        "what do you want me to do",
        "what would you like me to do",
    )
    if any(term in normalized for term in task_terms):
        return True
    standalone_startup_terms = (
        "what are my instructions",
        "what are the instructions",
        "what instructions should i follow",
        "what exact instructions",
        "exact instructions",
        "same exact instructions",
        "what is the expected behavior",
        "what behavior is expected",
        "what opening should i use",
        "what script should i use",
    )
    if any(term in normalized for term in standalone_startup_terms):
        return True
    script_terms = (
        "script",
        "opening",
        "instructions",
        "expected behavior",
        "what should i say",
        "what do i say",
        "how should i start",
        "how do i start",
    )
    call_terms = ("call", "caller", "phone", "voice", "task", "objective", "prompt")
    return any(term in normalized for term in script_terms) and any(
        term in normalized for term in call_terms
    )


def _extract_projected_task_prompt(projection: dict[str, Any]) -> str | None:
    task_prompt = projection.get("task_prompt")
    if isinstance(task_prompt, str) and task_prompt.strip():
        return task_prompt.strip()
    prompt = projection.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        return None
    marker = "Current voice task:"
    if marker in prompt:
        return prompt.split(marker, 1)[1].strip()
    return prompt.strip()


def _voice_query_key(question: str) -> str:
    return re.sub(r"\s+", " ", str(question or "").strip()).casefold()


def redact_phone_number(value: str | None) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    digits = [char for char in text if char.isdigit()]
    if len(digits) <= 4:
        return "***"
    return f"***{''.join(digits[-4:])}"


def _ari_id(prefix: str, call_id: str) -> str:
    allowed = "".join(ch for ch in call_id if ch.isalnum() or ch in {"-", "_"})
    return f"{prefix}-{allowed}"[:80]


def _sanitize_call_id(value: object) -> str:
    text = "".join(ch for ch in str(value or "").strip() if ch.isalnum() or ch in {"-", "_"})
    return text or uuid4().hex


def _normalize_inbound_phone_number(value: object) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    normalized = "".join(ch for ch in text if ch.isdigit() or ch == "+")
    return normalized or None


def _hash_identity(value: object) -> str | None:
    normalized = _normalize_inbound_phone_number(value) or _optional_text(value)
    if not normalized:
        return None
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _ari_event_channel_id(event: dict[str, Any]) -> str | None:
    channel = event.get("channel")
    if isinstance(channel, dict):
        value = channel.get("id")
        return str(value) if value is not None else None
    return None


def _ari_event_args(event: dict[str, Any]) -> list[str]:
    raw_args = event.get("args")
    if isinstance(raw_args, (list, tuple)):
        return [str(item).strip() for item in raw_args if str(item).strip()]
    if isinstance(raw_args, str):
        return [part.strip() for part in raw_args.split(",") if part.strip()]
    raw_app_args = event.get("appArgs")
    if isinstance(raw_app_args, str):
        return [part.strip() for part in raw_app_args.split(",") if part.strip()]
    return []


def _extract_sip_headers(channelvars: dict[str, Any], event: dict[str, Any]) -> dict[str, Any]:
    for candidate in (
        channelvars.get("sip_headers"),
        channelvars.get("SIP_HEADERS"),
        event.get("sip_headers"),
        event.get("SIP_HEADERS"),
    ):
        if isinstance(candidate, dict):
            return sanitize_voice_metadata(candidate)
    return {}


def _extract_inbound_stasis_context(event: dict[str, Any]) -> dict[str, Any] | None:
    if str(event.get("type") or "") != "StasisStart":
        return None
    channel = event.get("channel")
    if not isinstance(channel, dict):
        return None
    channel_id = _optional_text(channel.get("id")) or _optional_text(event.get("channel_id"))
    if not channel_id:
        return None
    channelvars_candidate = channel.get("channelvars") or channel.get("variables") or event.get(
        "channelvars"
    )
    channelvars = channelvars_candidate if isinstance(channelvars_candidate, dict) else {}
    args = _ari_event_args(event)
    inbound_marker = False
    did: str | None = None
    route_id: str | None = None
    for arg in args:
        lowered = arg.casefold()
        if lowered in {"inbound", "inbound-phone", "inbound_phone"}:
            inbound_marker = True
            continue
        if not lowered.startswith("inbound:"):
            continue
        inbound_marker = True
        descriptor = arg.split(":", 1)[1].strip()
        if not descriptor:
            continue
        first, _, second = descriptor.partition(":")
        maybe_did = _normalize_inbound_phone_number(first)
        if maybe_did is not None:
            did = maybe_did
            if second:
                route_id = _optional_text(second) or route_id
        else:
            route_id = _optional_text(first) or route_id
            if second:
                maybe_did = _normalize_inbound_phone_number(second)
                if maybe_did is not None:
                    did = maybe_did
    for key in ("INBOUND_PHONE_CALL", "INBOUND_CALL", "TRUMAN_INBOUND", "INBOUND"):
        marker = _optional_text(channelvars.get(key))
        if marker and marker.casefold() in {"1", "true", "yes", "inbound"}:
            inbound_marker = True
    if not inbound_marker:
        return None
    if did is None:
        for key in ("DID", "CALLED_NUMBER", "CALLED_DID", "EXTEN", "called_number", "did"):
            did = _normalize_inbound_phone_number(channelvars.get(key))
            if did:
                break
    if route_id is None:
        for key in ("INBOUND_ROUTE_ID", "ROUTE_ID", "route_id"):
            route_id = _optional_text(channelvars.get(key))
            if route_id:
                break
    if did is not None or route_id is not None:
        inbound_marker = True
    if did is None and route_id is None:
        return None
    caller_number: str | None = None
    caller = channel.get("caller")
    if isinstance(caller, dict):
        caller_number = _normalize_inbound_phone_number(
            caller.get("number") or caller.get("name")
        )
    if caller_number is None:
        for key in ("CALLERID(num)", "CALLER_NUMBER", "caller_number", "callerid_num"):
            caller_number = _normalize_inbound_phone_number(channelvars.get(key))
            if caller_number:
                break
    bridge_call_id = (
        _optional_text(channelvars.get("BRIDGE_CALL_ID"))
        or _optional_text(event.get("bridge_call_id"))
        or _optional_text(event.get("bridgeCallId"))
        or channel_id
    )
    return {
        "asterisk_channel_id": channel_id,
        "did": did,
        "route_id": route_id,
        "caller_number": caller_number,
        "bridge_call_id": bridge_call_id,
        "sip_headers": _extract_sip_headers(channelvars, event),
    }


def _ari_channel_state(event: dict[str, Any]) -> str | None:
    channel = event.get("channel")
    if isinstance(channel, dict):
        state = channel.get("state")
        return str(state) if state is not None else None
    return None


def _asterisk_debug_event_type(ari_event_type: str) -> str:
    if ari_event_type == "ChannelHangupRequest":
        return "asterisk.hangup"
    if ari_event_type == "ChannelDestroyed":
        return "asterisk.channel_destroyed"
    if ari_event_type == "StasisEnd":
        return "asterisk.stasis_end"
    return "asterisk.call_event"


def _unicast_rtp_local_addr(channel: dict[str, Any]) -> tuple[str, int] | None:
    raw_vars = channel.get("channelvars")
    channelvars = raw_vars if isinstance(raw_vars, dict) else {}
    address = str(channelvars.get("UNICASTRTP_LOCAL_ADDRESS") or "").strip()
    port_text = str(channelvars.get("UNICASTRTP_LOCAL_PORT") or "").strip()
    if not address or not port_text:
        return None
    try:
        port = int(port_text)
    except ValueError:
        return None
    if port <= 0 or port > 65535:
        return None
    return (address, port)


def _ari_hangup_status(event: dict[str, Any]) -> BridgeCallStatus:
    if _ari_hangup_cause_is_failed(event.get("cause_txt")):
        return "failed"
    if _ari_hangup_cause_is_failed(event.get("cause")):
        return "failed"
    return "ended"


def _ari_hangup_cause_is_failed(value: object) -> bool:
    code = _ari_hangup_cause_code(value)
    if code in _FAILED_HANGUP_CAUSE_CODES:
        return True
    return _normalize_hangup_cause_text(value) in _FAILED_HANGUP_CAUSE_TEXTS


def _ari_hangup_cause_code(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    text = str(value or "").strip()
    return int(text) if text.isdecimal() else None


def _normalize_hangup_cause_text(value: object) -> str:
    text = str(value or "").strip().lower()
    return re.sub(r"[^a-z0-9]+", "_", text).strip("_")


def _realtime_audio_delta(event: dict[str, Any]) -> str | None:
    event_type = str(event.get("type") or "")
    if event_type in {"response.output_audio.delta", "response.audio.delta"}:
        value = event.get("delta") or event.get("audio")
        return str(value) if value else None
    return None


def _realtime_audio_done(event: dict[str, Any]) -> bool:
    return str(event.get("type") or "") in {
        "response.output_audio.done",
        "response.audio.done",
    }


def _realtime_response_id(event: dict[str, Any]) -> str | None:
    response = event.get("response")
    if isinstance(response, dict):
        value = response.get("id")
        if value:
            return str(value)
    for key in ("response_id", "id"):
        value = event.get(key)
        if value:
            return str(value)
    return None


def _realtime_item_id(event: dict[str, Any]) -> str | None:
    item = event.get("item")
    if isinstance(item, dict):
        value = item.get("id") or item.get("item_id")
        if value:
            return str(value)
    value = event.get("item_id")
    return str(value) if value else None


def _realtime_content_index(event: dict[str, Any]) -> int:
    value = event.get("content_index")
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _realtime_audio_key(event: dict[str, Any]) -> str:
    return _realtime_response_id(event) or _realtime_item_id(event) or "unknown"


def _realtime_lifecycle_metadata(event: dict[str, Any]) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "realtime_event_type": str(event.get("type") or ""),
    }
    response_id = _realtime_response_id(event)
    if response_id:
        metadata["response_id"] = response_id
    response = event.get("response")
    if isinstance(response, dict):
        status = response.get("status")
        if status:
            metadata["status"] = str(status)
        status_details = response.get("status_details")
        if isinstance(status_details, dict):
            reason = status_details.get("reason") or status_details.get("type")
            if reason:
                metadata["reason"] = str(reason)
    return metadata


def _realtime_audio_metadata(event: dict[str, Any]) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "realtime_event_type": str(event.get("type") or ""),
    }
    for key, value in {
        "response_id": _realtime_response_id(event),
        "item_id": _realtime_item_id(event),
        "output_index": event.get("output_index"),
        "content_index": event.get("content_index"),
    }.items():
        if value is not None:
            metadata[key] = value
    return metadata


def _realtime_transcript_metadata(event: dict[str, Any], transcript_text: str) -> dict[str, Any]:
    metadata = _realtime_audio_metadata(event)
    metadata["transcript_snippet"] = _debug_snippet(transcript_text)
    return metadata


def _realtime_vad_event(event: dict[str, Any]) -> bool:
    event_type = str(event.get("type") or "")
    lowered = event_type.lower()
    return event_type in {
        "input_audio_buffer.speech_started",
        "input_audio_buffer.speech_stopped",
        "input_audio_buffer.timeout_triggered",
    } or "vad" in lowered


def _realtime_vad_metadata(event: dict[str, Any]) -> dict[str, Any]:
    metadata: dict[str, Any] = {"realtime_event_type": str(event.get("type") or "")}
    for key in ("audio_start_ms", "audio_end_ms", "item_id"):
        value = event.get(key)
        if value is not None:
            metadata[key] = value
    return metadata


def _realtime_error_metadata(event: dict[str, Any]) -> dict[str, Any]:
    error = event.get("error") if isinstance(event.get("error"), dict) else event
    metadata: dict[str, Any] = {
        "realtime_event_type": str(event.get("type") or "error"),
    }
    if isinstance(error, dict):
        code = error.get("code")
        error_type = error.get("type")
        message = error.get("message")
        event_id = error.get("event_id") or event.get("event_id")
        response_id = error.get("response_id") or _realtime_response_id(event)
        if code:
            metadata["error_code"] = str(code)
            metadata["reason"] = str(code)
        if error_type:
            metadata["error_type"] = str(error_type)
        if message:
            metadata["message_snippet"] = _debug_snippet(message)
        if event_id:
            metadata["realtime_event_id"] = str(event_id)
        if response_id:
            metadata["response_id"] = str(response_id)
    else:
        metadata["message_snippet"] = _debug_snippet(error)
    return metadata


def _realtime_function_call(event: dict[str, Any]) -> RealtimeFunctionCall | None:
    event_type = str(event.get("type") or "")
    raw_item = event.get("item")
    item = raw_item if isinstance(raw_item, dict) else event
    item_type = str(item.get("type") or "")
    if event_type == "response.output_item.done" and item_type != "function_call":
        return None
    if event_type not in {
        "response.output_item.done",
        "response.function_call_arguments.done",
    } and item_type != "function_call":
        return None
    name = str(item.get("name") or event.get("name") or "").strip()
    call_id = str(item.get("call_id") or event.get("call_id") or item.get("id") or "").strip()
    raw_arguments = item.get("arguments") if "arguments" in item else event.get("arguments")
    if not name or not call_id:
        return None
    arguments: dict[str, Any] = {}
    if isinstance(raw_arguments, dict):
        arguments = raw_arguments
    elif isinstance(raw_arguments, str) and raw_arguments.strip():
        try:
            parsed = json.loads(raw_arguments)
            if isinstance(parsed, dict):
                arguments = parsed
        except json.JSONDecodeError:
            arguments = {"question": raw_arguments}
    return RealtimeFunctionCall(call_id=call_id, name=name, arguments=arguments)


def _realtime_transcript_text(event: dict[str, Any]) -> str | None:
    event_type = str(event.get("type") or "")
    if event_type in {
        "conversation.item.input_audio_transcription.completed",
        "response.output_audio_transcript.done",
        "response.audio_transcript.done",
    }:
        value = event.get("transcript") or event.get("text")
        return str(value).strip() if value else None
    return None


def _realtime_text_delta(event: dict[str, Any]) -> str | None:
    event_type = str(event.get("type") or "")
    if event_type in {"response.output_text.delta", "response.text.delta"}:
        value = event.get("delta")
        return str(value) if value else None
    return None


def _json_response(response: httpx.Response) -> dict[str, Any]:
    if not response.content:
        return {}
    payload = response.json()
    return payload if isinstance(payload, dict) else {}


def _mapping(payload: dict[str, Any], key: str) -> dict[str, Any]:
    value = payload.get(key)
    if not isinstance(value, dict):
        value = {}
        payload[key] = value
    return value


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _normalize_list_item(value: object) -> str:
    return str(value or "").strip()


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default).strip() or default


def _env_optional(name: str, default: str | None) -> str | None:
    value = os.environ.get(name)
    if value is None:
        return default
    value = value.strip()
    return value or None


def _env_int(name: str, default: int) -> int:
    value = os.environ.get(name, "").strip()
    if not value:
        return default
    return int(value)


def _env_float(name: str, default: float) -> float:
    value = os.environ.get(name, "").strip()
    if not value:
        return default
    return float(value)


def _env_list(name: str, default: list[str]) -> list[str]:
    value = os.environ.get(name, "").strip()
    if not value:
        return list(default)
    return [part.strip() for part in value.split(",") if part.strip()]


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    normalized = value.strip().casefold()
    if not normalized:
        return default
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"Invalid boolean value for {name}: {value!r}")


if __name__ == "__main__":  # pragma: no cover
    main()
