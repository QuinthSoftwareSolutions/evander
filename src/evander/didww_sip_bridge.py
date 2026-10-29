from __future__ import annotations

import argparse
import asyncio
import base64
import json
import logging
import os
import struct
import time
from collections import deque
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
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

from evander.bootstrap import resolve_runtime_dir
from evander.models import utc_now
from evander.storage import JsonObjectStore
from evander.voice_sessions import (
    VoiceTranscriptEvent,
    normalize_voice_session_status,
    sanitize_voice_metadata,
)

from .didww_origin_queries import DidwwOriginQueryHandling

from .didww_bridge_protocol import (
    BridgeCallStatus as BridgeCallStatus,
    BridgeResponseRequest as BridgeResponseRequest,
    OriginQueryState as OriginQueryState,
    OutboundAudioFrame as OutboundAudioFrame,
    RealtimeFunctionCall as RealtimeFunctionCall,
    _ASSISTANT_PLAYBACK_DRAIN_SECONDS as _ASSISTANT_PLAYBACK_DRAIN_SECONDS,
    _AUDIO_QUALITY_ANOMALY_EVENT_LIMIT as _AUDIO_QUALITY_ANOMALY_EVENT_LIMIT,
    _AUDIO_QUALITY_FRAME_GAP_TOLERANCE as _AUDIO_QUALITY_FRAME_GAP_TOLERANCE,
    _AUDIO_QUALITY_RECENT_SEQUENCE_LIMIT as _AUDIO_QUALITY_RECENT_SEQUENCE_LIMIT,
    _AUDIO_QUALITY_WINDOW_LIMIT as _AUDIO_QUALITY_WINDOW_LIMIT,
    _BRIDGE_IDLE_PROMPT_DELAY_SECONDS as _BRIDGE_IDLE_PROMPT_DELAY_SECONDS,
    _BRIDGE_IDLE_PROMPT_MAX_PER_CALL as _BRIDGE_IDLE_PROMPT_MAX_PER_CALL,
    _CALLER_TURN_COMPLETION_INSTRUCTIONS as _CALLER_TURN_COMPLETION_INSTRUCTIONS,
    _CALLER_TURN_COMPLETION_RESPONSE_KEY_PREFIX as _CALLER_TURN_COMPLETION_RESPONSE_KEY_PREFIX,
    _DEBUG_SNIPPET_LIMIT as _DEBUG_SNIPPET_LIMIT,
    _DEFAULT_BACKCHANNEL_PHRASES as _DEFAULT_BACKCHANNEL_PHRASES,
    _END_CALL_TOOL_NAME as _END_CALL_TOOL_NAME,
    _FAILED_HANGUP_CAUSE_CODES as _FAILED_HANGUP_CAUSE_CODES,
    _FAILED_HANGUP_CAUSE_TEXTS as _FAILED_HANGUP_CAUSE_TEXTS,
    _G711_DECODE_TABLES as _G711_DECODE_TABLES,
    _G711_SILENCE_BYTES as _G711_SILENCE_BYTES,
    _IDLE_PROMPT_INSTRUCTIONS as _IDLE_PROMPT_INSTRUCTIONS,
    _IDLE_PROMPT_RESPONSE_KEY as _IDLE_PROMPT_RESPONSE_KEY,
    _INTERRUPTED_ASSISTANT_NO_TRANSCRIPT_RECOVERY_GRACE_SECONDS as _INTERRUPTED_ASSISTANT_NO_TRANSCRIPT_RECOVERY_GRACE_SECONDS,
    _INTERRUPTED_ASSISTANT_RECOVERY_GRACE_SECONDS as _INTERRUPTED_ASSISTANT_RECOVERY_GRACE_SECONDS,
    _INTERRUPTED_ASSISTANT_RESUME_INSTRUCTIONS as _INTERRUPTED_ASSISTANT_RESUME_INSTRUCTIONS,
    _INTERRUPTED_ASSISTANT_RESUME_RESPONSE_KEY_PREFIX as _INTERRUPTED_ASSISTANT_RESUME_RESPONSE_KEY_PREFIX,
    _MISSING_TASK_CLARIFICATION_INSTRUCTIONS as _MISSING_TASK_CLARIFICATION_INSTRUCTIONS,
    _MISSING_TASK_CLARIFICATION_RESPONSE_KEY as _MISSING_TASK_CLARIFICATION_RESPONSE_KEY,
    _ORIGIN_QUERY_ACK_GRACE_SECONDS as _ORIGIN_QUERY_ACK_GRACE_SECONDS,
    _ORIGIN_QUERY_ANSWER_RESPONSE_INSTRUCTIONS as _ORIGIN_QUERY_ANSWER_RESPONSE_INSTRUCTIONS,
    _ORIGIN_QUERY_HOLD_RESPONSE_INSTRUCTIONS as _ORIGIN_QUERY_HOLD_RESPONSE_INSTRUCTIONS,
    _ORIGIN_QUERY_IDLE_PROMPT_INSTRUCTIONS as _ORIGIN_QUERY_IDLE_PROMPT_INSTRUCTIONS,
    _ORIGIN_QUERY_TIMEOUT_RESPONSE_INSTRUCTIONS as _ORIGIN_QUERY_TIMEOUT_RESPONSE_INSTRUCTIONS,
    _PROJECTED_TASK_RESPONSE_INSTRUCTIONS as _PROJECTED_TASK_RESPONSE_INSTRUCTIONS,
    _PROJECTED_TASK_RESPONSE_KEY as _PROJECTED_TASK_RESPONSE_KEY,
    _RTP_STATIC_AUDIO_FORMATS as _RTP_STATIC_AUDIO_FORMATS,
    _ari_channel_state as _ari_channel_state,
    _ari_event_args as _ari_event_args,
    _ari_event_channel_id as _ari_event_channel_id,
    _ari_hangup_cause_code as _ari_hangup_cause_code,
    _ari_hangup_cause_is_failed as _ari_hangup_cause_is_failed,
    _ari_hangup_status as _ari_hangup_status,
    _ari_id as _ari_id,
    _asterisk_debug_event_type as _asterisk_debug_event_type,
    _audio_quality_tag as _audio_quality_tag,
    _backchannel_similarity as _backchannel_similarity,
    _build_barge_in_fade_payload as _build_barge_in_fade_payload,
    _debug_jsonable as _debug_jsonable,
    _debug_log_value as _debug_log_value,
    _debug_snippet as _debug_snippet,
    _decode_g711_alaw_sample as _decode_g711_alaw_sample,
    _decode_g711_ulaw_sample as _decode_g711_ulaw_sample,
    _end_call_tool_definition as _end_call_tool_definition,
    _env as _env,
    _env_bool as _env_bool,
    _env_float as _env_float,
    _env_int as _env_int,
    _env_list as _env_list,
    _env_optional as _env_optional,
    _extract_inbound_stasis_context as _extract_inbound_stasis_context,
    _extract_projected_task_prompt as _extract_projected_task_prompt,
    _extract_sip_headers as _extract_sip_headers,
    _g711_decode_sample as _g711_decode_sample,
    _g711_encode_sample as _g711_encode_sample,
    _g711_silence_byte as _g711_silence_byte,
    _hash_identity as _hash_identity,
    _inbound_projection_to_instructions as _inbound_projection_to_instructions,
    _instruction_debug_metadata as _instruction_debug_metadata,
    _interrupted_assistant_resume_response_key as _interrupted_assistant_resume_response_key,
    _is_backchannel_transcript as _is_backchannel_transcript,
    _is_goodbye_transcript as _is_goodbye_transcript,
    _is_interrupted_assistant_resume_response_key as _is_interrupted_assistant_resume_response_key,
    _is_origin_query_hold_ack as _is_origin_query_hold_ack,
    _is_origin_query_response_key as _is_origin_query_response_key,
    _json_response as _json_response,
    _log_debug_event as _log_debug_event,
    _mapping as _mapping,
    _normalize_backchannel_phrase as _normalize_backchannel_phrase,
    _normalize_hangup_cause_text as _normalize_hangup_cause_text,
    _normalize_inbound_phone_number as _normalize_inbound_phone_number,
    _normalize_list_item as _normalize_list_item,
    _openai_audio_format as _openai_audio_format,
    _optional_text as _optional_text,
    _origin_query_response_key as _origin_query_response_key,
    _origin_query_tool_output as _origin_query_tool_output,
    _projected_startup_task_answer as _projected_startup_task_answer,
    _projected_task_answer as _projected_task_answer,
    _projection_has_complete_startup_objective as _projection_has_complete_startup_objective,
    _projection_is_inbound as _projection_is_inbound,
    _question_asks_projected_task as _question_asks_projected_task,
    _realtime_audio_delta as _realtime_audio_delta,
    _realtime_audio_done as _realtime_audio_done,
    _realtime_audio_key as _realtime_audio_key,
    _realtime_audio_metadata as _realtime_audio_metadata,
    _realtime_content_index as _realtime_content_index,
    _realtime_error_metadata as _realtime_error_metadata,
    _realtime_function_call as _realtime_function_call,
    _realtime_item_id as _realtime_item_id,
    _realtime_lifecycle_metadata as _realtime_lifecycle_metadata,
    _realtime_response_id as _realtime_response_id,
    _realtime_text_delta as _realtime_text_delta,
    _realtime_transcript_metadata as _realtime_transcript_metadata,
    _realtime_transcript_text as _realtime_transcript_text,
    _realtime_vad_event as _realtime_vad_event,
    _realtime_vad_metadata as _realtime_vad_metadata,
    _restore_debug_hashes as _restore_debug_hashes,
    _rtp_bytes_per_sample as _rtp_bytes_per_sample,
    _sanitize_call_id as _sanitize_call_id,
    _sanitize_debug_metadata as _sanitize_debug_metadata,
    _truncate_debug_value as _truncate_debug_value,
    _unicast_rtp_local_addr as _unicast_rtp_local_addr,
    _voice_query_key as _voice_query_key,
    _voice_task_looks_actionable as _voice_task_looks_actionable,
    _word_count as _word_count,
    initial_inbound_call_instructions as initial_inbound_call_instructions,
    initial_outbound_call_instructions as initial_outbound_call_instructions,
    projection_to_instructions as projection_to_instructions,
    redact_phone_number as redact_phone_number,
    transcript_from_events as transcript_from_events,
)

logger = logging.getLogger(__name__)


class DidwwSipBridgeConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = Field(default=11888, ge=1, le=65535)
    runtime_dir: Path = Field(default_factory=lambda: Path("runtime"))
    bridge_token_env: str = Field(default="DIDWW_SIP_BRIDGE_TOKEN", min_length=1)
    enable_inbound_phone_calls: bool = False
    inbound_gateway_url: str | None = None
    inbound_gateway_token_env: str = Field(default="VOICE_MEDIA_CONTROLLER_TOKEN", min_length=1)
    openai_api_key_env: str = Field(default="OPENAI_API_KEY", min_length=1)
    openai_realtime_model: str = Field(default="gpt-realtime-2", min_length=1)
    openai_realtime_api_base_url: str = "https://api.openai.com/v1"
    openai_realtime_voice: str = "marin"
    openai_realtime_idle_timeout_ms: int = Field(default=10000, ge=5000, le=30000)
    openai_realtime_transcription_model: str | None = "gpt-live-transcribe"
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
    asterisk_ari_user: str = "evander"
    asterisk_ari_password_env: str = Field(default="ASTERISK_ARI_PASSWORD", min_length=1)
    asterisk_ari_app: str = "evander_didww"
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
    voice_query_caller_patience_seconds: float = Field(default=18.0, gt=0)
    voice_query_timeout_seconds: float = Field(default=90.0, gt=0)
    barge_in_delay_ms: int = Field(default=700, ge=0, le=5000)
    barge_in_fade_ms: int = Field(default=120, ge=0, le=500)
    goodbye_idle_hangup_seconds: float = Field(default=0.0, ge=0)
    backchannel_max_words: int = Field(default=3, ge=1, le=12)
    backchannel_fuzzy_threshold: float = Field(default=0.84, ge=0.0, le=1.0)
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
                f"media_format={self.media_format!r} requires sample_rate_hz={expected_sample_rate}"
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


class DidwwAudioQualityEvent(BaseModel):
    occurred_at: datetime = Field(default_factory=utc_now)
    event_type: str = Field(min_length=1)
    severity: Literal["info", "warning", "error"] = "info"
    message: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("event_type", mode="before")
    @classmethod
    def normalize_event_type(cls, value: object) -> object:
        text = str(value or "").strip()
        return text or None

    @field_validator("severity", mode="before")
    @classmethod
    def normalize_severity(cls, value: object) -> object:
        text = str(value or "").strip().lower()
        return text or "info"

    @field_validator("message", mode="before")
    @classmethod
    def normalize_message(cls, value: object) -> object:
        if value is None:
            return None
        return _debug_snippet(value)

    @field_validator("metadata", mode="before")
    @classmethod
    def normalize_metadata(cls, value: object) -> object:
        if not isinstance(value, dict):
            return {}
        return _sanitize_debug_metadata(value)


class DidwwAudioQualityWindow(BaseModel):
    started_at: datetime = Field(default_factory=utc_now)
    ended_at: datetime = Field(default_factory=utc_now)
    inbound_packets: int = Field(default=0, ge=0)
    inbound_bytes: int = Field(default=0, ge=0)
    inbound_samples: int = Field(default=0, ge=0)
    outbound_packets: int = Field(default=0, ge=0)
    outbound_bytes: int = Field(default=0, ge=0)
    outbound_samples: int = Field(default=0, ge=0)
    lost_packets: int = Field(default=0, ge=0)
    late_packets: int = Field(default=0, ge=0)
    out_of_order_packets: int = Field(default=0, ge=0)
    duplicate_packets: int = Field(default=0, ge=0)
    malformed_packets: int = Field(default=0, ge=0)
    decode_errors: int = Field(default=0, ge=0)
    buffer_underruns: int = Field(default=0, ge=0)
    buffer_overruns: int = Field(default=0, ge=0)
    jitter_ms: float = Field(default=0.0, ge=0.0)
    queue_depth_max: int = Field(default=0, ge=0)


class DidwwAudioQualityMetrics(BaseModel):
    voice_session_id: str = Field(min_length=1)
    call_id: str = Field(min_length=1)
    direction: Literal["outbound", "inbound"] = "outbound"
    started_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    media_format: str = Field(min_length=1)
    rtp_payload_type: int = Field(ge=0, le=127)
    sample_rate_hz: int = Field(ge=1)
    frame_duration_ms: int = Field(ge=1)
    rtp_port: int | None = Field(default=None, ge=1, le=65535)
    route_id: str | None = None
    asterisk_pjsip_endpoint: str | None = None
    carrier: str | None = None
    region: str | None = None
    inbound_packets: int = Field(default=0, ge=0)
    inbound_bytes: int = Field(default=0, ge=0)
    inbound_samples: int = Field(default=0, ge=0)
    outbound_packets: int = Field(default=0, ge=0)
    outbound_bytes: int = Field(default=0, ge=0)
    outbound_samples: int = Field(default=0, ge=0)
    lost_packets: int = Field(default=0, ge=0)
    late_packets: int = Field(default=0, ge=0)
    out_of_order_packets: int = Field(default=0, ge=0)
    duplicate_packets: int = Field(default=0, ge=0)
    malformed_packets: int = Field(default=0, ge=0)
    decode_errors: int = Field(default=0, ge=0)
    buffer_underruns: int = Field(default=0, ge=0)
    buffer_overruns: int = Field(default=0, ge=0)
    jitter_ms: float = Field(default=0.0, ge=0.0)
    queue_depth_max: int = Field(default=0, ge=0)
    windows: list[DidwwAudioQualityWindow] = Field(default_factory=list)
    anomaly_events: list[DidwwAudioQualityEvent] = Field(default_factory=list)

    @field_validator(
        "voice_session_id",
        "call_id",
        "route_id",
        "asterisk_pjsip_endpoint",
        mode="before",
    )
    @classmethod
    def normalize_text(cls, value: object) -> object:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @field_validator("direction", mode="before")
    @classmethod
    def normalize_direction(cls, value: object) -> object:
        text = str(value or "").strip().lower()
        return text or None

    @field_validator("media_format", mode="before")
    @classmethod
    def normalize_media_format(cls, value: object) -> object:
        text = str(value or "").strip().lower()
        return text or None

    @field_validator("carrier", "region", mode="before")
    @classmethod
    def normalize_location_text(cls, value: object) -> object:
        if value is None:
            return None
        return _debug_snippet(value)

    @field_validator("windows", "anomaly_events", mode="before")
    @classmethod
    def normalize_sequence_fields(cls, value: object) -> object:
        if isinstance(value, (list, tuple)):
            return list(value)
        if isinstance(value, set):
            return list(value)
        if not isinstance(value, list):
            return []
        return value

    @model_validator(mode="after")
    def bound_collections(self) -> DidwwAudioQualityMetrics:
        self.windows = list(self.windows[-_AUDIO_QUALITY_WINDOW_LIMIT:])
        self.anomaly_events = list(self.anomaly_events[-_AUDIO_QUALITY_ANOMALY_EVENT_LIMIT:])
        return self


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
    audio_quality: DidwwAudioQualityMetrics | None = None
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
            "debug_events": [event.model_dump(mode="json") for event in self.debug_events],
            "audio_quality": self.audio_quality.model_dump(mode="json")
            if self.audio_quality is not None
            else None,
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


class DidwwBridgeStore(JsonObjectStore):
    """Durable bridge state with process-shared transaction locks."""

    def __init__(self, path: Path) -> None:
        super().__init__(Path(path), {"calls": {}, "request_index": {}})

    def create_call(self, payload: PhoneCallStartRequest) -> tuple[DidwwBridgeCallRecord, bool]:
        with self._lock, self._file_lock():
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
        with self._lock, self._file_lock():
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
        with self._lock, self._file_lock():
            raw = _mapping(self._read_raw(), "calls").get(normalized)
        if not isinstance(raw, dict):
            return None
        return DidwwBridgeCallRecord.model_validate(raw)

    def get_by_call_id(self, call_id: str) -> DidwwBridgeCallRecord | None:
        normalized = str(call_id or "").strip()
        if not normalized:
            return None
        with self._lock, self._file_lock():
            calls = list(_mapping(self._read_raw(), "calls").values())
        for raw in calls:
            if not isinstance(raw, dict):
                continue
            if str(raw.get("call_id") or "").strip() != normalized:
                continue
            return DidwwBridgeCallRecord.model_validate(raw)
        return None

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

    def update_audio_quality(
        self,
        voice_session_id: str,
        metrics: DidwwAudioQualityMetrics | dict[str, Any],
    ) -> DidwwBridgeCallRecord:
        normalized_metrics = (
            metrics
            if isinstance(metrics, DidwwAudioQualityMetrics)
            else DidwwAudioQualityMetrics.model_validate(metrics)
        )

        def mutate(record: DidwwBridgeCallRecord) -> None:
            record.audio_quality = normalized_metrics

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
                record.decisions = [
                    _normalize_list_item(item) for item in decisions if _normalize_list_item(item)
                ]
            if action_items is not None:
                record.action_items = [
                    _normalize_list_item(item)
                    for item in action_items
                    if _normalize_list_item(item)
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
        with self._lock, self._file_lock():
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
        payload = self._read_object_unlocked()
        for section in ("calls", "request_index"):
            if not isinstance(payload.setdefault(section, {}), dict):
                raise ValueError(f"DIDWW bridge store has an invalid {section} section")
        return payload

    def _atomic_write(self, payload: dict[str, Any]) -> None:
        self._write_object_unlocked(payload)


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
                        "Summarize this phone call for the originating Evander agent. "
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
            await self.request_response(_ORIGIN_QUERY_ANSWER_RESPONSE_INSTRUCTIONS)

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
        noise_reduction_type = (
            str(self.config.openai_realtime_input_noise_reduction_type).strip().lower()
        )
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
        transcription_model = self.realtime.get(
            "transcription_model", self.config.openai_realtime_transcription_model
        )
        if transcription_model:
            payload["audio"]["input"]["transcription"] = {"model": transcription_model}
        tools = [_end_call_tool_definition()]
        if self.realtime.get("media_controller_url"):
            tools.append(
                {
                    "type": "function",
                    "name": "query_origin_agent",
                    "description": (
                        "Ask the originating Evander agent a live-context question that requires "
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
        if (
            any(
                marker in text
                for marker in (
                    "noise_reduction",
                    "noise reduction",
                    "input_audio_noise_reduction",
                )
            )
            and str(self.config.openai_realtime_input_noise_reduction_type).strip().lower()
            != "disabled"
        ):
            include_noise_reduction = False
            removed_features.append("audio.input.noise_reduction")
        if turn_detection_type == "semantic_vad" and any(
            marker in text
            for marker in (
                "turn_detection",
                "turn detection",
                "semantic_vad",
                "semantic vad",
                "vad",
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


@dataclass
class DidwwAudioQualityTracker:
    voice_session_id: str
    call_id: str
    direction: Literal["outbound", "inbound"] = "outbound"
    media_format: str = "alaw"
    rtp_payload_type: int = 8
    sample_rate_hz: int = 8000
    frame_duration_ms: int = 20
    rtp_port: int | None = None
    route_id: str | None = None
    asterisk_pjsip_endpoint: str | None = None
    carrier: str | None = None
    region: str | None = None
    started_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    inbound_packets: int = 0
    inbound_bytes: int = 0
    inbound_samples: int = 0
    outbound_packets: int = 0
    outbound_bytes: int = 0
    outbound_samples: int = 0
    lost_packets: int = 0
    late_packets: int = 0
    out_of_order_packets: int = 0
    duplicate_packets: int = 0
    malformed_packets: int = 0
    decode_errors: int = 0
    buffer_underruns: int = 0
    buffer_overruns: int = 0
    jitter_ms: float = 0.0
    queue_depth_max: int = 0
    windows: list[DidwwAudioQualityWindow] = field(default_factory=list)
    anomaly_events: list[DidwwAudioQualityEvent] = field(default_factory=list)
    _window: DidwwAudioQualityWindow = field(
        default_factory=DidwwAudioQualityWindow, init=False, repr=False
    )
    _last_inbound_monotonic: float | None = field(default=None, init=False, repr=False)
    _last_inbound_timestamp: int | None = field(default=None, init=False, repr=False)
    _last_outbound_monotonic: float | None = field(default=None, init=False, repr=False)
    _highest_sequence: int | None = field(default=None, init=False, repr=False)
    _recent_missing_sequences: deque[int] = field(default_factory=deque, init=False, repr=False)
    _recent_missing_sequence_set: set[int] = field(default_factory=set, init=False, repr=False)
    _recent_seen_sequences: deque[int] = field(default_factory=deque, init=False, repr=False)
    _recent_seen_sequence_set: set[int] = field(default_factory=set, init=False, repr=False)

    def __post_init__(self) -> None:
        self.direction = str(self.direction or "outbound").strip().lower() or "outbound"
        self.media_format = str(self.media_format or "alaw").strip().lower() or "alaw"
        self.voice_session_id = str(self.voice_session_id or "").strip()
        self.call_id = str(self.call_id or "").strip()
        if self.route_id is not None:
            self.route_id = str(self.route_id).strip() or None
        if self.asterisk_pjsip_endpoint is not None:
            self.asterisk_pjsip_endpoint = str(self.asterisk_pjsip_endpoint).strip() or None
        self.carrier = _debug_snippet(self.carrier)
        self.region = _debug_snippet(self.region)
        self._window.started_at = self.started_at
        self._window.ended_at = self.started_at

    def record_malformed_packet(
        self,
        *,
        reason: str | None = None,
        payload_length: int | None = None,
        occurred_at: datetime | None = None,
    ) -> None:
        self.malformed_packets += 1
        self._window.malformed_packets += 1
        self._touch(occurred_at)
        self._append_event(
            "rtp.packet.malformed",
            severity="warning",
            message=reason or "Malformed RTP packet",
            metadata={"payload_length": payload_length} if payload_length is not None else None,
            occurred_at=occurred_at,
        )

    def record_inbound_packet(
        self,
        packet: RtpPacket,
        *,
        payload_bytes: int | None = None,
        queue_depth: int | None = None,
        received_at_monotonic: float | None = None,
        occurred_at: datetime | None = None,
    ) -> None:
        payload_bytes = len(packet.payload) if payload_bytes is None else max(0, payload_bytes)
        payload_samples = self._payload_samples(payload_bytes)
        now_monotonic = time.monotonic() if received_at_monotonic is None else received_at_monotonic
        now_utc = occurred_at or utc_now()
        sequence = packet.sequence & 0xFFFF
        extended_sequence = self._unwrap_sequence(sequence)
        if packet.payload_type != self.rtp_payload_type:
            self._append_event(
                "rtp.packet.payload_type_mismatch",
                severity="warning",
                message=(
                    f"expected payload type {self.rtp_payload_type}, received {packet.payload_type}"
                ),
                metadata={
                    "expected_payload_type": self.rtp_payload_type,
                    "observed_payload_type": packet.payload_type,
                },
                occurred_at=now_utc,
            )
        if self._highest_sequence is None:
            self._highest_sequence = extended_sequence
        elif extended_sequence > self._highest_sequence:
            missing = extended_sequence - self._highest_sequence - 1
            if missing > 0:
                self.lost_packets += missing
                self._window.lost_packets += missing
                for missing_sequence in range(self._highest_sequence + 1, extended_sequence):
                    self._remember_missing(missing_sequence)
                self._append_event(
                    "rtp.packet.loss",
                    severity="warning",
                    message=f"missed {missing} RTP packet(s)",
                    metadata={
                        "from_sequence": self._highest_sequence & 0xFFFF,
                        "to_sequence": sequence,
                        "lost_packets": missing,
                    },
                    occurred_at=now_utc,
                )
            self._highest_sequence = extended_sequence
        elif extended_sequence in self._recent_missing_sequence_set:
            self.late_packets += 1
            self._window.late_packets += 1
            self._forget_missing(extended_sequence)
            self._append_event(
                "rtp.packet.late",
                severity="info",
                message="Late RTP packet arrived after a gap",
                metadata={"sequence": sequence},
                occurred_at=now_utc,
            )
        elif extended_sequence in self._recent_seen_sequence_set:
            self.duplicate_packets += 1
            self._window.duplicate_packets += 1
            self._append_event(
                "rtp.packet.duplicate",
                severity="info",
                message="Duplicate RTP packet received",
                metadata={"sequence": sequence},
                occurred_at=now_utc,
            )
        elif self._highest_sequence is not None and extended_sequence < self._highest_sequence:
            self.out_of_order_packets += 1
            self._window.out_of_order_packets += 1
            self._append_event(
                "rtp.packet.out_of_order",
                severity="info",
                message="Out-of-order RTP packet received",
                metadata={"sequence": sequence},
                occurred_at=now_utc,
            )

        self.inbound_packets += 1
        self.inbound_bytes += payload_bytes
        self.inbound_samples += payload_samples
        self._window.inbound_packets += 1
        self._window.inbound_bytes += payload_bytes
        self._window.inbound_samples += payload_samples
        self._window.ended_at = now_utc
        self._record_jitter(packet.timestamp, now_monotonic, now_utc)
        if self._last_inbound_monotonic is not None:
            interarrival = now_monotonic - self._last_inbound_monotonic
            expected = self.frame_duration_ms / 1000.0
            if interarrival > expected * _AUDIO_QUALITY_FRAME_GAP_TOLERANCE:
                self._append_event(
                    "rtp.frame_gap",
                    severity="warning",
                    message="Inbound RTP inter-arrival gap exceeded frame duration",
                    metadata={
                        "gap_ms": round(interarrival * 1000.0, 3),
                        "expected_frame_duration_ms": self.frame_duration_ms,
                    },
                    occurred_at=now_utc,
                )
        self._last_inbound_monotonic = now_monotonic
        self._last_inbound_timestamp = packet.timestamp & 0xFFFFFFFF
        if queue_depth is not None:
            self.record_queue_depth(queue_depth, occurred_at=now_utc)
        self._remember_seen(extended_sequence)
        self._touch(now_utc)

    def record_outbound_packet(
        self,
        *,
        samples_sent: int,
        payload_bytes: int,
        rtp_timestamp: int | None = None,
        send_gap_seconds: float | None = None,
        sent_at_monotonic: float | None = None,
        occurred_at: datetime | None = None,
    ) -> None:
        now_monotonic = time.monotonic() if sent_at_monotonic is None else sent_at_monotonic
        now_utc = occurred_at or utc_now()
        self.outbound_packets += 1
        self.outbound_bytes += max(0, payload_bytes)
        self.outbound_samples += max(0, samples_sent)
        self._window.outbound_packets += 1
        self._window.outbound_bytes += max(0, payload_bytes)
        self._window.outbound_samples += max(0, samples_sent)
        self._window.ended_at = now_utc
        gap = send_gap_seconds
        if gap is None and self._last_outbound_monotonic is not None:
            gap = now_monotonic - self._last_outbound_monotonic
        if gap is not None:
            expected = self.frame_duration_ms / 1000.0
            if gap > expected * _AUDIO_QUALITY_FRAME_GAP_TOLERANCE:
                self._append_event(
                    "rtp.frame_gap",
                    severity="warning",
                    message="Outbound RTP send gap exceeded frame duration",
                    metadata={
                        "gap_ms": round(gap * 1000.0, 3),
                        "expected_frame_duration_ms": self.frame_duration_ms,
                        "rtp_timestamp": rtp_timestamp,
                    },
                    occurred_at=now_utc,
                )
        self._last_outbound_monotonic = now_monotonic
        self._touch(now_utc)

    def record_decode_error(
        self,
        *,
        reason: str | None = None,
        occurred_at: datetime | None = None,
    ) -> None:
        self.decode_errors += 1
        self._window.decode_errors += 1
        self._touch(occurred_at)
        self._append_event(
            "realtime.audio_delta.decode_error",
            severity="error",
            message=reason or "Failed to decode audio delta",
            occurred_at=occurred_at,
        )

    def record_queue_depth(
        self,
        queue_depth: int,
        *,
        occurred_at: datetime | None = None,
    ) -> None:
        if queue_depth < 0:
            return
        if queue_depth > self.queue_depth_max:
            self.queue_depth_max = queue_depth
            self._window.queue_depth_max = max(self._window.queue_depth_max, queue_depth)
        self._touch(occurred_at)
        self._window.ended_at = occurred_at or utc_now()
        if queue_depth > self._queue_depth_warning_threshold():
            self.buffer_overruns += 1
            self._window.buffer_overruns += 1
            self._append_event(
                "rtp.queue_overrun",
                severity="warning",
                message="RTP receive queue depth exceeded the warning threshold",
                metadata={
                    "queue_depth": queue_depth,
                    "warning_threshold": self._queue_depth_warning_threshold(),
                },
                occurred_at=occurred_at,
            )

    def record_buffer_underrun(
        self,
        *,
        wait_ms: float,
        queue_name: str = "outbound_audio",
        occurred_at: datetime | None = None,
    ) -> None:
        self.buffer_underruns += 1
        self._window.buffer_underruns += 1
        self._touch(occurred_at)
        self._append_event(
            "rtp.buffer_underrun",
            severity="warning",
            message="Audio sender waited for queued audio longer than the frame-gap threshold",
            metadata={
                "queue": queue_name,
                "wait_ms": round(max(0.0, wait_ms), 3),
                "expected_frame_duration_ms": self.frame_duration_ms,
            },
            occurred_at=occurred_at,
        )

    def snapshot(self, *, occurred_at: datetime | None = None) -> DidwwAudioQualityMetrics:
        now = occurred_at or utc_now()
        self.updated_at = now
        self._window.ended_at = now
        window = self._window.model_copy(deep=True)
        self.windows = [window]
        return DidwwAudioQualityMetrics(
            voice_session_id=self.voice_session_id,
            call_id=self.call_id,
            direction=self.direction,
            started_at=self.started_at,
            updated_at=now,
            media_format=self.media_format,
            rtp_payload_type=self.rtp_payload_type,
            sample_rate_hz=self.sample_rate_hz,
            frame_duration_ms=self.frame_duration_ms,
            rtp_port=self.rtp_port,
            route_id=self.route_id,
            asterisk_pjsip_endpoint=self.asterisk_pjsip_endpoint,
            carrier=self.carrier,
            region=self.region,
            inbound_packets=self.inbound_packets,
            inbound_bytes=self.inbound_bytes,
            inbound_samples=self.inbound_samples,
            outbound_packets=self.outbound_packets,
            outbound_bytes=self.outbound_bytes,
            outbound_samples=self.outbound_samples,
            lost_packets=self.lost_packets,
            late_packets=self.late_packets,
            out_of_order_packets=self.out_of_order_packets,
            duplicate_packets=self.duplicate_packets,
            malformed_packets=self.malformed_packets,
            decode_errors=self.decode_errors,
            buffer_underruns=self.buffer_underruns,
            buffer_overruns=self.buffer_overruns,
            jitter_ms=self.jitter_ms,
            queue_depth_max=self.queue_depth_max,
            windows=[window],
            anomaly_events=[event.model_copy(deep=True) for event in self.anomaly_events],
        )

    def _payload_samples(self, payload_bytes: int) -> int:
        if payload_bytes <= 0:
            return 0
        bytes_per_sample = _rtp_bytes_per_sample(self.media_format)
        return max(1, (payload_bytes + bytes_per_sample - 1) // bytes_per_sample)

    def _record_jitter(
        self,
        timestamp: int,
        now_monotonic: float,
        occurred_at: datetime,
    ) -> None:
        if self._last_inbound_monotonic is None or self._last_inbound_timestamp is None:
            return
        arrival_delta = now_monotonic - self._last_inbound_monotonic
        timestamp_delta = self._unwrap_timestamp(timestamp, self._last_inbound_timestamp)
        transit_delta = arrival_delta - (timestamp_delta / self.sample_rate_hz)
        jitter_seconds = self.jitter_ms / 1000.0
        jitter_seconds += (abs(transit_delta) - jitter_seconds) / 16.0
        self.jitter_ms = max(0.0, jitter_seconds * 1000.0)
        self._touch(occurred_at)

    def _unwrap_sequence(self, sequence: int) -> int:
        sequence &= 0xFFFF
        if self._highest_sequence is None:
            return sequence
        base = self._highest_sequence
        candidate = (base & ~0xFFFF) | sequence
        if candidate - base > 0x7FFF:
            candidate -= 0x10000
        elif base - candidate > 0x7FFF:
            candidate += 0x10000
        return candidate

    def _unwrap_timestamp(self, timestamp: int, previous_timestamp: int) -> int:
        timestamp &= 0xFFFFFFFF
        previous_timestamp &= 0xFFFFFFFF
        candidate = timestamp
        if candidate - previous_timestamp > 0x7FFFFFFF:
            candidate -= 0x100000000
        elif previous_timestamp - candidate > 0x7FFFFFFF:
            candidate += 0x100000000
        return candidate

    def _remember_missing(self, sequence: int) -> None:
        self._recent_missing_sequences.append(sequence)
        self._recent_missing_sequence_set.add(sequence)
        while len(self._recent_missing_sequences) > _AUDIO_QUALITY_RECENT_SEQUENCE_LIMIT:
            stale = self._recent_missing_sequences.popleft()
            self._recent_missing_sequence_set.discard(stale)

    def _forget_missing(self, sequence: int) -> None:
        if sequence not in self._recent_missing_sequence_set:
            return
        self._recent_missing_sequence_set.discard(sequence)
        try:
            self._recent_missing_sequences.remove(sequence)
        except ValueError:
            pass

    def _remember_seen(self, sequence: int) -> None:
        self._recent_seen_sequences.append(sequence)
        self._recent_seen_sequence_set.add(sequence)
        while len(self._recent_seen_sequences) > _AUDIO_QUALITY_RECENT_SEQUENCE_LIMIT:
            stale = self._recent_seen_sequences.popleft()
            self._recent_seen_sequence_set.discard(stale)

    def _append_event(
        self,
        event_type: str,
        *,
        severity: Literal["info", "warning", "error"] = "info",
        message: str | None = None,
        metadata: dict[str, Any] | None = None,
        occurred_at: datetime | None = None,
    ) -> None:
        self.anomaly_events.append(
            DidwwAudioQualityEvent(
                event_type=event_type,
                severity=severity,
                message=message,
                metadata=metadata or {},
                occurred_at=occurred_at or utc_now(),
            )
        )
        self.anomaly_events = self.anomaly_events[-_AUDIO_QUALITY_ANOMALY_EVENT_LIMIT:]

    def _touch(self, occurred_at: datetime | None = None) -> None:
        self.updated_at = occurred_at or utc_now()

    def _queue_depth_warning_threshold(self) -> int:
        return max(8, int(round(self.frame_duration_ms * 2)))


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
            timeout_seconds = (
                float(timeout) if timeout is not None else config.request_timeout_seconds
            )
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
        now_utc = utc_now()
        now_monotonic = time.monotonic()
        try:
            packet = RtpPacket.parse(data)
        except ValueError as exc:
            if self.endpoint.tracker is not None:
                try:
                    self.endpoint.tracker.record_malformed_packet(
                        reason=str(exc),
                        payload_length=len(data),
                        occurred_at=now_utc,
                    )
                except Exception:
                    logger.exception("didww_bridge_audio_quality_record_failed")
            return
        try:
            self.endpoint.incoming.put_nowait(packet.payload)
        except asyncio.QueueFull:
            if self.endpoint.tracker is not None:
                try:
                    self.endpoint.tracker.record_queue_depth(
                        self.endpoint.incoming.qsize(),
                        occurred_at=now_utc,
                    )
                except Exception:
                    logger.exception("didww_bridge_audio_quality_record_failed")
            return
        if self.endpoint.tracker is not None:
            try:
                self.endpoint.tracker.record_inbound_packet(
                    packet,
                    payload_bytes=len(packet.payload),
                    queue_depth=self.endpoint.incoming.qsize(),
                    received_at_monotonic=now_monotonic,
                    occurred_at=now_utc,
                )
            except Exception:
                logger.exception("didww_bridge_audio_quality_record_failed")


class RtpUdpEndpoint:
    def __init__(
        self,
        config: DidwwSipBridgeConfig,
        *,
        tracker: DidwwAudioQualityTracker | None = None,
    ) -> None:
        self.config = config
        self.incoming: asyncio.Queue[bytes] = asyncio.Queue()
        self.transport: asyncio.DatagramTransport | None = None
        self.remote_addr: tuple[str, int] | None = None
        self.port: int | None = None
        self.tracker = tracker
        self._sequence = 1
        self._timestamp = 0
        self._last_send_monotonic: float | None = None

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
        loop = asyncio.get_running_loop()
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
            now_monotonic = loop.time()
            samples_sent = max(1, (len(chunk) + bytes_per_sample - 1) // bytes_per_sample)
            if self.tracker is not None:
                self.tracker.record_outbound_packet(
                    samples_sent=samples_sent,
                    payload_bytes=len(chunk),
                    rtp_timestamp=self._timestamp,
                    send_gap_seconds=(
                        None
                        if self._last_send_monotonic is None
                        else now_monotonic - self._last_send_monotonic
                    ),
                    sent_at_monotonic=now_monotonic,
                    occurred_at=utc_now(),
                )
            self._last_send_monotonic = now_monotonic
            self._sequence = (self._sequence + 1) & 0xFFFF
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


@dataclass(frozen=True)
class _AudioQualityTrackerAttachment:
    voice_session_id: str
    tracker: Any
    created_tracker: bool
    had_rtp_tracker: bool
    previous_rtp_tracker: Any
    assigned_rtp_tracker: bool


class DidwwSipBridgeService(DidwwOriginQueryHandling):
    def __init__(
        self,
        config: DidwwSipBridgeConfig,
        *,
        store: DidwwBridgeStore | None = None,
        ari_client: AsteriskAriClient | None = None,
        realtime_factory: Callable[[dict[str, Any], dict[str, Any]], OpenAIRealtimeSession]
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
        self._audio_quality_trackers: dict[str, Any] = {}
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
            await service._flush_audio_quality_tracker(voice_session_id)
            record = service.store.get(voice_session_id)
            if record is None:
                return JSONResponse({"error": "Unknown voice session"}, status_code=404)
            return JSONResponse(record.response_payload())

        async def get_voice_session_audio_quality(request: Request) -> JSONResponse:
            auth_error = service._auth_error(request)
            if auth_error is not None:
                return auth_error
            voice_session_id = str(request.path_params.get("voice_session_id") or "").strip()
            await service._flush_audio_quality_tracker(voice_session_id)
            record = service.store.get(voice_session_id)
            if record is None:
                return JSONResponse({"error": "Unknown voice session"}, status_code=404)
            return JSONResponse(record.response_payload())

        async def get_phone_call_audio_quality(request: Request) -> JSONResponse:
            auth_error = service._auth_error(request)
            if auth_error is not None:
                return auth_error
            call_id = str(request.path_params.get("call_id") or "").strip()
            record = service.store.get_by_call_id(call_id)
            if record is None:
                return JSONResponse({"error": "Unknown phone call"}, status_code=404)
            await service._flush_audio_quality_tracker(record.voice_session_id)
            record = service.store.get(record.voice_session_id) or record
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
                    "/voice-sessions/{voice_session_id}/audio-quality",
                    get_voice_session_audio_quality,
                    methods=["GET"],
                ),
                Route(
                    "/phone-calls/{call_id}/audio-quality",
                    get_phone_call_audio_quality,
                    methods=["GET"],
                ),
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

    async def _flush_audio_quality_tracker(self, voice_session_id: str) -> None:
        normalized_voice_session_id = str(voice_session_id or "").strip()
        if not normalized_voice_session_id:
            return
        tracker = self._audio_quality_trackers.get(normalized_voice_session_id)
        if tracker is None:
            return
        flush = getattr(tracker, "flush", None)
        if not callable(flush):
            snapshot = getattr(tracker, "snapshot", None)
            if not callable(snapshot):
                return

            def flush() -> DidwwBridgeCallRecord:
                return self.store.update_audio_quality(
                    normalized_voice_session_id,
                    snapshot(),
                )

        try:
            result = flush()
            if asyncio.iscoroutine(result):
                await result
        except Exception:
            logger.exception(
                "didww_bridge_audio_quality_flush_failed voice_session_id=%s",
                normalized_voice_session_id,
            )

    def _create_audio_quality_tracker(
        self,
        record: DidwwBridgeCallRecord,
        rtp: RtpUdpEndpoint,
    ) -> DidwwAudioQualityTracker:
        return DidwwAudioQualityTracker(
            voice_session_id=record.voice_session_id,
            call_id=record.call_id,
            direction=record.direction,
            media_format=self.config.media_format,
            rtp_payload_type=self.config.rtp_payload_type,
            sample_rate_hz=self.config.sample_rate_hz,
            frame_duration_ms=self.config.frame_duration_ms,
            rtp_port=getattr(rtp, "port", None) or record.rtp_port,
            route_id=record.route_id,
            asterisk_pjsip_endpoint=self.config.asterisk_pjsip_endpoint,
            carrier=_audio_quality_tag(record, "carrier"),
            region=_audio_quality_tag(record, "region"),
        )

    def _attach_audio_quality_tracker(
        self,
        voice_session_id: str,
        rtp: RtpUdpEndpoint,
    ) -> _AudioQualityTrackerAttachment | None:
        normalized_voice_session_id = str(voice_session_id or "").strip()
        if not normalized_voice_session_id:
            return None
        tracker = self._audio_quality_trackers.get(normalized_voice_session_id)
        created_tracker = False
        if tracker is None:
            record = self.store.get(normalized_voice_session_id)
            if record is None:
                return None
            tracker = self._create_audio_quality_tracker(record, rtp)
            self._audio_quality_trackers[normalized_voice_session_id] = tracker
            created_tracker = True

        if getattr(tracker, "rtp_port", None) is None:
            rtp_port = getattr(rtp, "port", None)
            if rtp_port is not None:
                try:
                    setattr(tracker, "rtp_port", rtp_port)
                except Exception:
                    logger.exception(
                        "didww_bridge_audio_quality_port_attach_failed voice_session_id=%s",
                        normalized_voice_session_id,
                    )

        had_rtp_tracker = hasattr(rtp, "tracker")
        previous_rtp_tracker = getattr(rtp, "tracker", None)
        assigned_rtp_tracker = False
        if previous_rtp_tracker is not tracker:
            try:
                setattr(rtp, "tracker", tracker)
                assigned_rtp_tracker = True
            except Exception:
                logger.exception(
                    "didww_bridge_audio_quality_attach_failed voice_session_id=%s",
                    normalized_voice_session_id,
                )
        return _AudioQualityTrackerAttachment(
            voice_session_id=normalized_voice_session_id,
            tracker=tracker,
            created_tracker=created_tracker,
            had_rtp_tracker=had_rtp_tracker,
            previous_rtp_tracker=previous_rtp_tracker,
            assigned_rtp_tracker=assigned_rtp_tracker,
        )

    async def _release_audio_quality_tracker(
        self,
        attachment: _AudioQualityTrackerAttachment,
        rtp: RtpUdpEndpoint,
    ) -> None:
        await self._flush_audio_quality_tracker(attachment.voice_session_id)
        if (
            attachment.created_tracker
            and self._audio_quality_trackers.get(attachment.voice_session_id) is attachment.tracker
        ):
            self._audio_quality_trackers.pop(attachment.voice_session_id, None)
        if not attachment.assigned_rtp_tracker:
            return
        try:
            if attachment.had_rtp_tracker:
                setattr(rtp, "tracker", attachment.previous_rtp_tracker)
            else:
                delattr(rtp, "tracker")
        except Exception:
            logger.exception(
                "didww_bridge_audio_quality_detach_failed voice_session_id=%s",
                attachment.voice_session_id,
            )

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
        sip_headers = (
            context.get("sip_headers") if isinstance(context.get("sip_headers"), dict) else {}
        )
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
            tracker_attachment: _AudioQualityTrackerAttachment | None = None
            try:
                await rtp.start()
                if rtp.port is None:
                    raise RuntimeError("RTP endpoint did not bind a port")
                tracker_attachment = self._attach_audio_quality_tracker(
                    record.voice_session_id,
                    rtp,
                )
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
                if tracker_attachment is not None:
                    await self._release_audio_quality_tracker(tracker_attachment, rtp)
                rtp.close()
                latest = self.store.get(record.voice_session_id)
                if latest is not None:
                    await self._sync_inbound_voice_session_to_gateway(latest, payload.realtime)
                    await self._cleanup_asterisk(latest)

    async def run_call(self, record: DidwwBridgeCallRecord, payload: PhoneCallStartRequest) -> None:
        async with self._semaphore:
            rtp = self.rtp_endpoint_factory()
            tracker_attachment: _AudioQualityTrackerAttachment | None = None
            try:
                await rtp.start()
                if rtp.port is None:
                    raise RuntimeError("RTP endpoint did not bind a port")
                tracker_attachment = self._attach_audio_quality_tracker(
                    record.voice_session_id,
                    rtp,
                )
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
                logger.exception(
                    "didww_bridge_call_failed voice_session_id=%s", record.voice_session_id
                )
                self.store.complete(record.voice_session_id, status="failed", error=str(exc))
            finally:
                if tracker_attachment is not None:
                    await self._release_audio_quality_tracker(tracker_attachment, rtp)
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
        tracker_attachment = self._attach_audio_quality_tracker(voice_session_id, rtp)
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
                self.store.complete(
                    voice_session_id, status="ended", summary="Call reached max duration."
                )
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
            if tracker_attachment is not None:
                if tracker_attachment.created_tracker:
                    await self._release_audio_quality_tracker(tracker_attachment, rtp)
                else:
                    await self._flush_audio_quality_tracker(voice_session_id)

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
        from .didww_realtime_media import RealtimeMediaTiming, RealtimeRtpSession

        timing = RealtimeMediaTiming(
            assistant_playback_drain_seconds=_ASSISTANT_PLAYBACK_DRAIN_SECONDS,
            audio_quality_frame_gap_tolerance=_AUDIO_QUALITY_FRAME_GAP_TOLERANCE,
            bridge_idle_prompt_delay_seconds=_BRIDGE_IDLE_PROMPT_DELAY_SECONDS,
            bridge_idle_prompt_max_per_call=_BRIDGE_IDLE_PROMPT_MAX_PER_CALL,
            interrupted_assistant_no_transcript_recovery_grace_seconds=_INTERRUPTED_ASSISTANT_NO_TRANSCRIPT_RECOVERY_GRACE_SECONDS,
            interrupted_assistant_recovery_grace_seconds=_INTERRUPTED_ASSISTANT_RECOVERY_GRACE_SECONDS,
        )
        await RealtimeRtpSession(self, timing).run(
            voice_session_id, rtp, realtime, media_controller, stop_event
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
    realtime_factory: Callable[[dict[str, Any], dict[str, Any]], OpenAIRealtimeSession]
    | None = None,
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
            "OPENAI_REALTIME_TRANSCRIPTION_MODEL", "gpt-live-transcribe"
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
        asterisk_ari_user=_env("ASTERISK_ARI_USER", "evander"),
        asterisk_ari_password_env=_env("ASTERISK_ARI_PASSWORD_ENV", "ASTERISK_ARI_PASSWORD"),
        asterisk_ari_app=_env("ASTERISK_ARI_APP", "evander_didww"),
        asterisk_pjsip_endpoint=_env("ASTERISK_PJSIP_ENDPOINT", "didww"),
        asterisk_endpoint_template=_env("ASTERISK_ENDPOINT_TEMPLATE", "PJSIP/{number}@{trunk}"),
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
        voice_query_caller_patience_seconds=_env_float(
            "DIDWW_SIP_BRIDGE_VOICE_QUERY_CALLER_PATIENCE_SECONDS", 18.0
        ),
        voice_query_timeout_seconds=_env_float(
            "DIDWW_SIP_BRIDGE_VOICE_QUERY_TIMEOUT_SECONDS", 90.0
        ),
        barge_in_delay_ms=_env_int("DIDWW_SIP_BRIDGE_BARGE_IN_DELAY_MS", 700),
        barge_in_fade_ms=_env_int("DIDWW_SIP_BRIDGE_BARGE_IN_FADE_MS", 120),
        goodbye_idle_hangup_seconds=_env_float("DIDWW_SIP_BRIDGE_GOODBYE_IDLE_HANGUP_SECONDS", 0.0),
        backchannel_max_words=_env_int("DIDWW_SIP_BRIDGE_BACKCHANNEL_MAX_WORDS", 3),
        backchannel_fuzzy_threshold=_env_float(
            "DIDWW_SIP_BRIDGE_BACKCHANNEL_FUZZY_THRESHOLD",
            0.84,
        ),
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


if __name__ == "__main__":  # pragma: no cover
    main()
