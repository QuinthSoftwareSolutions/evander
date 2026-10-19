"""Bounded speech requests; credentials and provider access stay on the host."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os

import httpx
from starlette.requests import ClientDisconnect

from .models import BrowserError, identifier, origin


AUDIO_TYPES = {
    "audio/webm": ("speech.webm", "audio/webm"),
    "audio/ogg": ("speech.ogg", "audio/ogg"),
    "audio/mp4": ("speech.mp4", "audio/mp4"),
    "audio/wav": ("speech.wav", "audio/wav"),
}


async def read_audio(request, maximum):
    mime = request.headers.get("content-type", "").split(";")[0].lower()
    if mime not in AUDIO_TYPES:
        raise BrowserError("Use a supported microphone audio format", "audio_type", 415)
    content = bytearray()
    try:
        async with asyncio.timeout(20):
            async for chunk in request.stream():
                if len(content) + len(chunk) > maximum:
                    raise BrowserError("Voice recording is too large", "audio_limit", 413)
                content.extend(chunk)
    except TimeoutError:
        raise BrowserError("Voice upload timed out", "audio_timeout", 408) from None
    except ClientDisconnect:
        raise BrowserError("Voice upload was interrupted", "audio_interrupted", 400) from None
    if not content:
        raise BrowserError("The voice recording is empty", "audio_empty", 422)
    return bytes(content), mime


class Voice:
    def __init__(self, service, *, transport=None):
        self.service, self.store, self.transport = service, service.store, transport

    def configured(self, session_id):
        session = self.store.get(session_id)
        self.store.active(session)
        _, settings = self.service.settings(session.directory)
        config = settings.voice
        if not config.enabled or not os.environ.get(config.api_key_env):
            raise BrowserError(
                "Voice has not been configured for this directory", "voice_unavailable", 409
            )
        return config

    def info(self, session_id):
        session = self.store.get(session_id)
        self.store.active(session)
        _, settings = self.service.settings(session.directory)
        config = settings.voice
        with self.store.connect() as db:
            latest = db.execute(
                "SELECT COALESCE(MAX(seq),0) FROM messages WHERE session_id=? AND channel='viewer'",
                (session_id,),
            ).fetchone()[0]
        return {
            "latest_message_seq": latest,
            "enabled": config.enabled and bool(os.environ.get(config.api_key_env)),
            "max_recording_seconds": config.max_recording_seconds,
            "max_audio_bytes": config.max_audio_bytes,
            "ai_generated": True,
        }

    def begin(self, session_id, actor, request_id, kind, fingerprint, config):
        identifier(request_id)
        now = self.service.now()
        with self.store.connect(write=True) as db:
            previous = db.execute(
                "SELECT * FROM voice_requests WHERE session_id=? AND actor=? AND request_id=?",
                (session_id, actor, request_id),
            ).fetchone()
            if previous:
                if previous["kind"] != kind or previous["fingerprint"] != fingerprint:
                    raise BrowserError("Voice request identity conflict", "voice_conflict", 409)
                if kind == "transcribe" and previous["state"] == "completed":
                    return json.loads(previous["result"])
                raise BrowserError(
                    "This voice request was already submitted; start a new request",
                    "voice_duplicate",
                    409,
                )
            recent = db.execute(
                "SELECT COUNT(*) FROM voice_requests WHERE session_id=? AND at>?",
                (session_id, now - 60),
            ).fetchone()[0]
            active = db.execute(
                "SELECT session_id FROM voice_requests WHERE state='processing' AND lease_until>?",
                (now,),
            ).fetchall()
            if (
                recent >= config.requests_per_minute
                or len(active) >= 8
                or sum(row["session_id"] == session_id for row in active) >= 2
            ):
                raise BrowserError("Voice is busy; wait before trying again", "voice_limit", 429)
            db.execute(
                "INSERT INTO voice_requests VALUES(?,?,?,?,?,'processing',?,?,NULL)",
                (session_id, actor, request_id, kind, fingerprint, now, now + 45),
            )
        return None

    def finish(self, session_id, actor, request_id, state, result=None):
        with self.store.connect(write=True) as db:
            db.execute(
                """UPDATE voice_requests SET state=?,result=?,lease_until=0
                   WHERE session_id=? AND actor=? AND request_id=? AND state='processing'""",
                (state, json.dumps(result) if result else None, session_id, actor, request_id),
            )

    async def provider(self, config, path, *, maximum, **kwargs):
        try:
            async with asyncio.timeout(40):
                async with httpx.AsyncClient(
                    timeout=30, trust_env=False, follow_redirects=False, transport=self.transport
                ) as client:
                    async with client.stream(
                        "POST",
                        config.api_base_url + path,
                        headers={"Authorization": "Bearer " + os.environ[config.api_key_env]},
                        **kwargs,
                    ) as response:
                        if response.status_code != 200:
                            raise BrowserError(
                                "Speech service is unavailable", "speech_provider", 502
                            )
                        content = bytearray()
                        async for chunk in response.aiter_bytes():
                            if len(content) + len(chunk) > maximum:
                                raise BrowserError(
                                    "Speech response exceeded its limit", "speech_limit", 502
                                )
                            content.extend(chunk)
                        return bytes(content)
        except BrowserError:
            raise
        except (httpx.HTTPError, TimeoutError, KeyError):
            raise BrowserError("Speech service is unavailable", "speech_provider", 502) from None

    async def transcribe(self, session_id, actor, request_id, audio, mime):
        config = self.configured(session_id)
        if mime not in AUDIO_TYPES or not audio or len(audio) > config.max_audio_bytes:
            raise BrowserError("Invalid voice recording", "audio_limit", 413)
        fingerprint = hashlib.sha256(mime.encode() + b"\0" + audio).hexdigest()
        previous = self.begin(session_id, actor, request_id, "transcribe", fingerprint, config)
        if previous is not None:
            return previous
        completed = False
        try:
            filename, content_type = AUDIO_TYPES[mime]
            content = await self.provider(
                config,
                "/audio/transcriptions",
                maximum=65536,
                files={"file": (filename, audio, content_type)},
                data={"model": config.transcription_model, "response_format": "json"},
            )
            try:
                text = json.loads(content)["text"]
            except (ValueError, KeyError, TypeError):
                raise BrowserError(
                    "Invalid transcription response", "speech_provider", 502
                ) from None
            if not isinstance(text, str) or not text.strip() or len(text) > 16000:
                raise BrowserError("No usable speech was detected", "speech_empty", 422)
            self.configured(session_id)
            result = {"text": text.strip(), "request_id": request_id}
            self.finish(session_id, actor, request_id, "completed", result)
            self.store.audit(session_id, actor, "voice_transcribed", {"characters": len(text)})
            completed = True
            return result
        finally:
            if not completed:
                self.finish(session_id, actor, request_id, "unknown")

    async def speech(self, session_id, actor, request_id, message_id):
        config = self.configured(session_id)
        with self.store.connect() as db:
            message = db.execute(
                """SELECT * FROM messages WHERE id=? AND session_id=? AND channel='viewer'
                   AND reply_to IS NOT NULL AND actor LIKE 'agent:%'""",
                (message_id, session_id),
            ).fetchone()
        if message is None:
            raise BrowserError("This reply is unavailable", "not_found", 404)
        text = message["body"][: config.max_speech_characters]
        fingerprint = hashlib.sha256((message_id + "\0" + text).encode()).hexdigest()
        self.begin(session_id, actor, request_id, "speech", fingerprint, config)
        completed = False
        try:
            content = await self.provider(
                config,
                "/audio/speech",
                maximum=8_000_000,
                json={
                    "model": config.speech_model,
                    "voice": config.voice,
                    "input": text,
                    "response_format": "wav",
                },
            )
            if not content.startswith(b"RIFF") or content[8:12] != b"WAVE":
                raise BrowserError("Invalid speech response", "speech_provider", 502)
            self.configured(session_id)
            self.finish(session_id, actor, request_id, "completed")
            self.store.audit(session_id, actor, "voice_spoken", {"message_id": message_id})
            completed = True
            return content
        finally:
            if not completed:
                self.finish(session_id, actor, request_id, "unknown")


def require_voice_origin(request, settings):
    supplied = request.headers.get("origin")
    if not supplied or origin(supplied) != origin(settings.public_base_url):
        raise BrowserError("Voice request origin was rejected", "forbidden", 403)
