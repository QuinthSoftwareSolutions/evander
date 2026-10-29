"""Bounded narration; provider settings and authentication remain host-owned."""

from __future__ import annotations

import asyncio
from contextlib import aclosing
from io import BytesIO
import struct
from time import monotonic
from typing import Literal
from urllib.parse import urlsplit
import wave

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from evander.connectors.drivers import (
    ConnectorInvocationError,
    ConnectorUpstreamAuthenticationError,
)
from evander.connectors.models import ConnectorArtifactResult, ConnectorInvocationResult
from evander.realtime_speech import RealtimeSpeechError, pcm_wav, realtime_speech_chunks


MAX_AUDIO_BYTES = 8_000_000
MAX_AUDIO_SECONDS = 120


class SpeechSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_url: str
    speech_backend: Literal["realtime", "speech_api"] = "realtime"
    model: str = Field(default="gpt-realtime-2.1-mini", min_length=1, max_length=128)
    voice: str = Field(default="cedar", min_length=1, max_length=128)
    instructions: str = Field(
        default="Speak naturally, warmly and clearly. Read the supplied text only.", max_length=2000
    )
    speed: float = Field(default=1.1, ge=0.25, le=4.0, allow_inf_nan=False)

    @model_validator(mode="after")
    def speech_speed(self):
        if self.speech_backend == "realtime" and self.speed > 1.5:
            raise ValueError("Realtime speech speed must be between 0.25 and 1.5")
        return self

    @field_validator("base_url")
    @classmethod
    def provider_url(cls, value: str) -> str:
        parts = urlsplit(value)
        local_http = parts.scheme == "http" and parts.hostname in {"127.0.0.1", "::1", "localhost"}
        if (
            (parts.scheme != "https" and not local_http)
            or not parts.netloc
            or parts.username
            or parts.password
            or parts.query
            or parts.fragment
        ):
            raise ValueError(
                "Speech base_url requires HTTPS (HTTP is allowed only for a loopback fixture)"
            )
        return value.rstrip("/")


def normalize_wav(content: bytes) -> tuple[bytes, float]:
    """Validate PCM, including Speech API streaming size sentinels, and seal the WAV lengths."""
    if (
        len(content) > MAX_AUDIO_BYTES
        or len(content) < 44
        or content[:4] != b"RIFF"
        or content[8:12] != b"WAVE"
    ):
        raise ConnectorInvocationError("Speech provider returned invalid WAV audio")
    riff_size = struct.unpack_from("<I", content, 4)[0]
    if riff_size != 0xFFFFFFFF and riff_size + 8 != len(content):
        raise ConnectorInvocationError("Speech provider returned truncated WAV audio")
    fmt = None
    pcm = None
    offset = 12
    while offset + 8 <= len(content):
        kind, size = struct.unpack_from("<4sI", content, offset)
        offset += 8
        if kind == b"data" and size == 0xFFFFFFFF:
            size = len(content) - offset
        if offset + size > len(content):
            raise ConnectorInvocationError("Speech provider returned truncated WAV audio")
        chunk = content[offset : offset + size]
        if kind == b"fmt ":
            if fmt is not None or size < 16:
                raise ConnectorInvocationError("Speech provider returned invalid WAV format")
            fmt = struct.unpack_from("<HHIIHH", chunk)
        elif kind == b"data":
            if pcm is not None:
                raise ConnectorInvocationError("Speech provider returned multiple WAV data chunks")
            pcm = chunk
        offset += size + size % 2
    if offset != len(content) or fmt is None or not pcm:
        raise ConnectorInvocationError("Speech provider returned incomplete WAV audio")
    encoding, channels, rate, byte_rate, alignment, bits = fmt
    if (
        encoding != 1
        or channels not in {1, 2}
        or not 8000 <= rate <= 48000
        or bits != 16
        or alignment != channels * 2
        or byte_rate != rate * alignment
        or len(pcm) % alignment
    ):
        raise ConnectorInvocationError("Speech provider must return 16-bit PCM WAV audio")
    duration = len(pcm) / byte_rate
    if not 0 < duration <= MAX_AUDIO_SECONDS:
        raise ConnectorInvocationError("Speech audio exceeded the 120 second beat limit")
    buffer = BytesIO()
    with wave.open(buffer, "wb") as writer:
        writer.setnchannels(channels)
        writer.setsampwidth(2)
        writer.setframerate(rate)
        writer.writeframes(pcm)
    return buffer.getvalue(), duration


class NarrationConnectorDriver:
    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None, websocket_connect=None):
        self.transport = transport
        self.websocket_connect = websocket_connect

    def validate_definition(self, definition):
        SpeechSettings.model_validate(definition.config)
        if set(definition.operations) != {"synthesize"}:
            raise ValueError("Narration exposes only synthesize")
        operation = definition.operations["synthesize"]
        if operation.result_mode != "artifact" or operation.access != "write":
            raise ValueError("Narration requires a write operation returning an artifact")

    async def invoke(
        self, *, context, connector_id, definition, operation_name, operation, arguments
    ):
        self.validate_definition(definition)
        if operation_name != "synthesize" or set(arguments) != {"text"}:
            raise ConnectorInvocationError("Supply only narration text to synthesize")
        text = arguments["text"]
        if not isinstance(text, str) or not text.strip() or len(text) > 2000:
            raise ConnectorInvocationError("Narration text must contain 1 to 2000 characters")
        settings = SpeechSettings.model_validate(definition.config)
        payload = {
            "model": settings.model,
            "voice": settings.voice,
            "input": text,
            "response_format": "wav",
            "speed": settings.speed,
        }
        if settings.instructions and settings.model not in {"tts-1", "tts-1-hd"}:
            payload["instructions"] = settings.instructions
        maximum = min(int(operation.max_response_bytes), MAX_AUDIO_BYTES)
        started = monotonic()
        try:
            if context.authorize:
                context.authorize()
            if settings.speech_backend == "realtime":
                chunks = realtime_speech_chunks(
                    base_url=settings.base_url, model=settings.model, voice=settings.voice,
                    instructions=settings.instructions, speed=settings.speed, text=text,
                    headers=context.auth_headers, maximum=maximum - 44,
                    timeout=min(operation.timeout_seconds, 40), authorize=context.authorize,
                    connect=self.websocket_connect,
                )
                async with aclosing(chunks):
                    content = bytearray(pcm_wav(b"".join([chunk async for chunk in chunks])))
            else:
                content = await self._speech_api(settings, payload, maximum, operation, context)
            if context.sensitive_values:
                from evander.credentials.sanitize import reject_reflected_credentials

                reject_reflected_credentials(bytes(content), context.sensitive_values)
            wav, duration = normalize_wav(bytes(content))
            if context.authorize:
                context.authorize()
            record = context.artifact_store.stage(
                provider=f"connector:{connector_id}",
                session_id=context.session_id,
                content=wav,
                original_filename="narration.wav",
                mime_type="audio/wav",
                metadata={"duration_seconds": duration},
            )
            return ConnectorInvocationResult(
                connector_id=connector_id,
                operation=operation_name,
                result_mode="artifact",
                artifact=ConnectorArtifactResult(
                    artifact_id=record.artifact_id,
                    filename=record.filename,
                    mime_type=record.mime_type,
                    byte_count=record.byte_count,
                    sha256=record.sha256,
                    expires_at=record.expires_at.isoformat(),
                ),
                value={"duration_seconds": duration},
                duration_ms=int((monotonic() - started) * 1000),
            )
        except RealtimeSpeechError as exc:
            if exc.authentication_failed:
                raise ConnectorUpstreamAuthenticationError("Speech provider rejected authentication") from None
            raise ConnectorInvocationError(
                "Speech provider unavailable or timed out; no completed audio artifact"
            ) from None
        except (httpx.HTTPError, TimeoutError):
            raise ConnectorInvocationError(
                "Speech provider unavailable or timed out; no completed audio artifact"
            ) from None

    async def _speech_api(self, settings, payload, maximum, operation, context):
        async with asyncio.timeout(min(operation.timeout_seconds, 40)):
            async with httpx.AsyncClient(
                transport=self.transport, follow_redirects=False
            ) as client:
                async with client.stream(
                    "POST",
                    settings.base_url + "/audio/speech",
                    headers=context.auth_headers,
                    json=payload,
                ) as response:
                    if response.status_code == 401:
                        raise ConnectorUpstreamAuthenticationError(
                            "Speech provider rejected authentication"
                        )
                    if response.status_code != 200:
                        raise ConnectorInvocationError("Speech provider request failed")
                    content = bytearray()
                    async for chunk in response.aiter_bytes():
                        if context.authorize:
                            context.authorize()
                        content.extend(chunk)
                        if len(content) > maximum:
                            raise ConnectorInvocationError(
                                "Speech audio exceeded its byte limit"
                            )
        return content
