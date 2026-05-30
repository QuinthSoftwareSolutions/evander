from __future__ import annotations

import argparse
import asyncio
import base64
import json
import logging
import os
import re
import struct
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from typing import Any, Literal
from urllib.parse import urlencode
from uuid import uuid4

import httpx
import uvicorn
from pydantic import BaseModel, Field, field_validator
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


class DidwwSipBridgeConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = Field(default=11888, ge=1, le=65535)
    runtime_dir: Path = Field(default_factory=lambda: Path("runtime"))
    bridge_token_env: str = Field(default="DIDWW_SIP_BRIDGE_TOKEN", min_length=1)
    openai_api_key_env: str = Field(default="OPENAI_API_KEY", min_length=1)
    openai_realtime_model: str = Field(default="gpt-realtime-2", min_length=1)
    openai_realtime_api_base_url: str = "https://api.openai.com/v1"
    openai_realtime_voice: str = "marin"
    openai_realtime_idle_timeout_ms: int = Field(default=10000, ge=5000, le=30000)
    openai_realtime_transcription_model: str | None = "gpt-4o-transcribe"
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
    rtp_payload_type: int = Field(default=118, ge=0, le=127)
    rtp_ssrc: int = Field(default=0x41534757, ge=0, le=0xFFFFFFFF)
    media_format: str = "slin16"
    sample_rate_hz: int = Field(default=16000, ge=8000)
    frame_duration_ms: int = Field(default=20, ge=10, le=60)
    call_timeout_seconds: float = Field(default=45.0, gt=0)
    max_call_seconds: float = Field(default=1800.0, gt=0)
    request_timeout_seconds: float = Field(default=15.0, gt=0)
    max_concurrent_calls: int = Field(default=4, ge=1)
    voice_query_poll_interval_seconds: float = Field(default=1.0, gt=0)
    voice_query_timeout_seconds: float = Field(default=90.0, gt=0)

    @field_validator("openai_realtime_api_base_url", "asterisk_ari_url", mode="before")
    @classmethod
    def normalize_base_url(cls, value: object) -> object:
        return str(value or "").strip().rstrip("/")

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


class DidwwBridgeCallRecord(BaseModel):
    voice_session_id: str = Field(min_length=1)
    request_id: str = Field(min_length=1)
    call_id: str = Field(min_length=1)
    status: BridgeCallStatus = "created"
    contact: dict[str, Any] = Field(default_factory=dict)
    projection: dict[str, Any] = Field(default_factory=dict)
    realtime: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    to_number_redacted: str | None = None
    from_number_redacted: str | None = None
    ari_channel_id: str | None = None
    ari_bridge_id: str | None = None
    ari_external_media_channel_id: str | None = None
    rtp_port: int | None = None
    transcript_events: list[VoiceTranscriptEvent] = Field(default_factory=list)
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
        "to_number_redacted",
        "from_number_redacted",
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
            "status": self.status,
            "transcript_events": [
                event.model_dump(mode="json") for event in self.transcript_events
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
                "ari_channel_id": self.ari_channel_id,
                "ari_bridge_id": self.ari_bridge_id,
                "ari_external_media_channel_id": self.ari_external_media_channel_id,
                "rtp_port": self.rtp_port,
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
            record.summary = _optional_text(summary) or record.summary
            if decisions is not None:
                record.decisions = [_normalize_list_item(item) for item in decisions if _normalize_list_item(item)]
            if action_items is not None:
                record.action_items = [
                    _normalize_list_item(item) for item in action_items if _normalize_list_item(item)
                ]
            if error is not None:
                record.error = str(error)
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
            await self._request(
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
        )

    async def hangup_channel(self, channel_id: str | None) -> None:
        if not channel_id:
            return
        try:
            await self._request("DELETE", f"/channels/{channel_id}")
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

    async def append_audio(self, pcm16_audio: bytes) -> None:
        if not pcm16_audio:
            return
        await self.send_json(
            {
                "type": "input_audio_buffer.append",
                "audio": base64.b64encode(pcm16_audio).decode("ascii"),
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

    async def inject_origin_query_result(self, query: dict[str, Any]) -> None:
        query_id = str(query.get("query_id") or "").strip()
        status = str(query.get("status") or "").strip() or "unknown"
        answer = str(query.get("answer") or query.get("error") or "").strip()
        citations = query.get("citations") if isinstance(query.get("citations"), list) else []
        text = (
            f"Origin agent result for delegated voice query {query_id} "
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
        await self.request_response(
            "If still relevant, briefly tell the caller the origin agent has the answer."
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
        payload: dict[str, Any] = {
            "type": "realtime",
            "model": model,
            "instructions": instructions,
            "output_modalities": ["audio"],
            "audio": {
                "input": {
                    "format": {
                        "type": "audio/pcm",
                        "rate": self.config.sample_rate_hz,
                    },
                    "turn_detection": {
                        "type": "server_vad",
                        "create_response": True,
                        "interrupt_response": True,
                        "idle_timeout_ms": self.config.openai_realtime_idle_timeout_ms,
                    },
                },
                "output": {
                    "format": {
                        "type": "audio/pcm",
                        "rate": self.config.sample_rate_hz,
                    },
                    "voice": self.config.openai_realtime_voice,
                },
            },
        }
        if self.config.openai_realtime_transcription_model:
            payload["audio"]["input"]["transcription"] = {
                "model": self.config.openai_realtime_transcription_model
            }
        if self.realtime.get("media_controller_url"):
            payload["tools"] = [
                {
                    "type": "function",
                    "name": "query_origin_agent",
                    "description": (
                        "Ask the originating Truman agent a live-context question that requires "
                        "its files, tools, repository state, or memory."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "question": {
                                "type": "string",
                                "description": "The caller-facing question to ask the origin agent.",
                            },
                            "reason": {
                                "type": "string",
                                "description": "Why this cannot be answered from projected context.",
                            },
                        },
                        "required": ["question"],
                    },
                }
            ]
            payload["tool_choice"] = "auto"
        return payload


@dataclass(frozen=True)
class RealtimeFunctionCall:
    call_id: str
    name: str
    arguments: dict[str, Any]


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

    def send_payload(self, payload: bytes) -> None:
        if not payload or self.transport is None or self.remote_addr is None:
            return
        samples_per_frame = int(self.config.sample_rate_hz * self.config.frame_duration_ms / 1000)
        bytes_per_sample = 2
        payload_bytes_per_frame = max(1, samples_per_frame * bytes_per_sample)
        for offset in range(0, len(payload), payload_bytes_per_frame):
            chunk = payload[offset : offset + payload_bytes_per_frame]
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
        )

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
                    handles = await self.ari_client.start_external_media_call(
                        call_id=record.call_id,
                        to_number=payload.to_number,
                        from_number=payload.from_number,
                        external_host=external_host,
                    )
                    self.store.attach_asterisk(
                        record.voice_session_id,
                        ari_channel_id=handles.call_channel_id,
                        ari_bridge_id=handles.bridge_id,
                        ari_external_media_channel_id=handles.external_media_channel_id,
                        rtp_port=rtp.port,
                    )
                    await self._bridge_media_until_done(
                        record.voice_session_id,
                        handles,
                        rtp,
                        realtime,
                        media_controller,
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
            asyncio.create_task(self._wait_for_asterisk_end(voice_session_id, handles, stop_event)),
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
        try:
            while not stop_event.is_set():
                try:
                    event = await asyncio.wait_for(realtime.recv_json(), timeout=0.5)
                except TimeoutError:
                    continue
                event_type = str(event.get("type") or "")
                function_call = _realtime_function_call(event)
                if function_call is not None:
                    await self._handle_realtime_function_call(
                        voice_session_id,
                        function_call,
                        realtime,
                        media_controller,
                        query_tasks,
                        stop_event,
                    )
                    continue
                audio_delta = _realtime_audio_delta(event)
                if audio_delta:
                    rtp.send_payload(base64.b64decode(audio_delta))
                    continue
                transcript_text = _realtime_transcript_text(event)
                if transcript_text:
                    speaker = "assistant" if event_type.startswith("response.") else "caller"
                    self.store.append_transcript_event(
                        voice_session_id,
                        speaker=speaker,
                        text=transcript_text,
                        metadata={"event_type": event_type},
                    )
                text_delta = _realtime_text_delta(event)
                if text_delta:
                    summary_parts.append(text_delta)
                if event_type == "response.done" and summary_parts:
                    self.store.complete(
                        voice_session_id,
                        status="ended",
                        summary="".join(summary_parts).strip(),
                    )
                if event_type == "error":
                    raise RuntimeError(str(event.get("error") or event))
        finally:
            for task in query_tasks:
                task.cancel()
            await asyncio.gather(*query_tasks, return_exceptions=True)

    async def _handle_realtime_function_call(
        self,
        voice_session_id: str,
        function_call: RealtimeFunctionCall,
        realtime: OpenAIRealtimeSession,
        media_controller: VoiceMediaControllerClient,
        query_tasks: set[asyncio.Task[None]],
        stop_event: asyncio.Event,
    ) -> None:
        if function_call.name != "query_origin_agent":
            await realtime.send_tool_output(
                call_id=function_call.call_id,
                output={"status": "failed", "error": f"Unsupported tool: {function_call.name}"},
            )
            await realtime.request_response("Tell the caller that tool is unavailable.")
            return
        question = str(
            function_call.arguments.get("question")
            or function_call.arguments.get("query")
            or ""
        ).strip()
        if not question:
            await realtime.send_tool_output(
                call_id=function_call.call_id,
                output={"status": "failed", "error": "question is required"},
            )
            await realtime.request_response("Ask the caller to clarify the question.")
            return
        if not media_controller.configured:
            await realtime.send_tool_output(
                call_id=function_call.call_id,
                output={"status": "failed", "error": "origin agent delegation is not configured"},
            )
            await realtime.request_response(
                "Tell the caller you cannot check the origin agent right now."
            )
            return
        record = self.store.get(voice_session_id)
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
            await realtime.send_tool_output(
                call_id=function_call.call_id,
                output={"status": "failed", "error": str(exc)},
            )
            await realtime.request_response(
                "Tell the caller you cannot check the origin agent right now."
            )
            return
        query_id = str(query.get("query_id") or "").strip()
        self.store.append_transcript_event(
            voice_session_id,
            speaker="assistant",
            text=f"Delegated question to origin agent: {question}",
            metadata={"query_id": query_id, "event_type": "voice_query_created"},
        )
        await realtime.send_tool_output(
            call_id=function_call.call_id,
            output={"status": "pending", "query_id": query_id},
        )
        await realtime.request_response(
            "Tell the caller you are checking with the originating agent, then continue naturally."
        )
        task = asyncio.create_task(
            self._poll_origin_query_result(
                voice_session_id,
                query_id,
                media_controller,
                realtime,
                stop_event,
            )
        )
        query_tasks.add(task)
        task.add_done_callback(lambda completed: query_tasks.discard(completed))

    async def _poll_origin_query_result(
        self,
        voice_session_id: str,
        query_id: str,
        media_controller: VoiceMediaControllerClient,
        realtime: OpenAIRealtimeSession,
        stop_event: asyncio.Event,
    ) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.config.voice_query_timeout_seconds
        while not stop_event.is_set() and loop.time() < deadline:
            await asyncio.sleep(self.config.voice_query_poll_interval_seconds)
            query = await media_controller.get_query(voice_session_id, query_id)
            status = str(query.get("status") or "").strip()
            if status in {"answered", "failed"}:
                await realtime.inject_origin_query_result(query)
                answer = str(query.get("answer") or query.get("error") or "").strip()
                if answer:
                    self.store.append_transcript_event(
                        voice_session_id,
                        speaker="origin_agent",
                        text=answer,
                        metadata={"query_id": query_id, "event_type": "voice_query_answered"},
                    )
                return
        if not stop_event.is_set():
            query = await media_controller.mark_query_timeout(voice_session_id, query_id)
            await realtime.inject_origin_query_result(query)

    async def _wait_for_asterisk_end(
        self,
        voice_session_id: str,
        handles: AsteriskCallHandles,
        stop_event: asyncio.Event,
    ) -> None:
        async with self.ari_client.event_stream() as websocket:
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
                    self.store.update_status(voice_session_id, "active")
                if event_type in {"ChannelHangupRequest", "ChannelDestroyed", "StasisEnd"}:
                    cause = event.get("cause_txt") or event.get("cause") or "hangup"
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

    def _start_background_call(
        self,
        record: DidwwBridgeCallRecord,
        payload: PhoneCallStartRequest,
    ) -> None:
        task = asyncio.create_task(self.run_call(record, payload))
        self._tasks[record.call_id] = task

        def discard(completed: asyncio.Task[None]) -> None:
            self._tasks.pop(record.call_id, None)
            try:
                completed.result()
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
        rtp_payload_type=_env_int("DIDWW_SIP_BRIDGE_RTP_PAYLOAD_TYPE", 118),
        media_format=_env("DIDWW_SIP_BRIDGE_MEDIA_FORMAT", "slin16"),
        sample_rate_hz=_env_int("DIDWW_SIP_BRIDGE_SAMPLE_RATE_HZ", 16000),
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


def projection_to_instructions(projection: dict[str, Any]) -> str:
    instructions = [
        "You are the same Truman agent represented by this projected voice session.",
        "Use the provided origin context faithfully. Do not claim to be a separate handoff agent.",
        "Keep the phone conversation concise and natural.",
        "At the end of the call, preserve decisions and follow-up actions for reconciliation.",
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


def transcript_from_events(events: list[VoiceTranscriptEvent]) -> str | None:
    lines = []
    for event in events:
        speaker = event.speaker or "speaker"
        lines.append(f"{speaker}: {event.text}")
    return "\n".join(lines) if lines else None


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


def _ari_event_channel_id(event: dict[str, Any]) -> str | None:
    channel = event.get("channel")
    if isinstance(channel, dict):
        value = channel.get("id")
        return str(value) if value is not None else None
    return None


def _ari_channel_state(event: dict[str, Any]) -> str | None:
    channel = event.get("channel")
    if isinstance(channel, dict):
        state = channel.get("state")
        return str(state) if state is not None else None
    return None


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


if __name__ == "__main__":  # pragma: no cover
    main()
