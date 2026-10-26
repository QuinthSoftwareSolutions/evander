"""Bounded speech requests; credentials and provider access stay on the host."""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import re
import shutil
import wave
from contextlib import aclosing

import httpx
from starlette.requests import ClientDisconnect
from starlette.responses import StreamingResponse

from .models import BrowserError, identifier, origin


AUDIO_TYPES = {
    "audio/webm": "matroska",
    "audio/ogg": "ogg",
    "audio/mp4": "mov",
    "audio/wav": "wav",
}

DECODE_TIMEOUT_SECONDS = 10
PROVIDER_TIMEOUT_SECONDS = 40


def speech_payload(config, text, response_format):
    payload = {
        "model": config.speech_model,
        "voice": config.voice,
        "input": text,
        "response_format": response_format,
        "speed": config.speed,
    }
    # The legacy OpenAI models accept speed but do not support delivery instructions.
    if config.instructions and config.speech_model not in {"tts-1", "tts-1-hd"}:
        payload["instructions"] = config.instructions
    return payload


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


async def normalize_recording(audio, mime, seconds):
    """Measure decoded samples and send only the validated audio to the provider."""
    decoder = shutil.which("ffmpeg")
    if decoder is None:
        raise BrowserError(
            "Voice transcription requires FFmpeg on this host", "voice_unavailable", 409
        )
    process = None
    try:
        async with asyncio.timeout(DECODE_TIMEOUT_SECONDS):
            process = await asyncio.create_subprocess_exec(
                decoder, "-v", "error", "-nostdin", "-max_alloc", "16777216",
                "-protocol_whitelist", "pipe", "-f", AUDIO_TYPES[mime],
                "-threads", "1", "-i", "pipe:0", "-map", "0:a:0", "-vn", "-sn", "-dn",
                # Fixed-size sample frames bound output independently of uploaded timestamps.
                "-af", "aresample=16000,asetnsamples=n=16000:p=0,asetpts=N/SR/TB",
                "-frames:a", str(seconds + 1), "-ac", "1", "-ar", "16000",
                "-c:a", "pcm_s16le", "-f", "s16le", "pipe:1",
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            pcm, _ = await process.communicate(audio)
        if process.returncode or not pcm:
            raise BrowserError("The recording could not be decoded", "audio_invalid", 422)
        if len(pcm) > seconds * 16000 * 2:
            raise BrowserError(
                "The recording exceeds the duration limit; try a shorter phrase",
                "audio_duration", 413,
            )
    except TimeoutError:
        raise BrowserError("Audio decoding timed out", "audio_timeout", 422) from None
    except OSError:
        raise BrowserError("Voice audio decoder is unavailable", "voice_unavailable", 409) from None
    finally:
        if process is not None and process.returncode is None:
            process.kill()
            await process.communicate()
    output = io.BytesIO()
    with wave.open(output, "wb") as normalized:
        normalized.setnchannels(1)
        normalized.setsampwidth(2)
        normalized.setframerate(16000)
        normalized.writeframes(pcm)
    return output.getvalue()


class Voice:
    def __init__(self, service, *, transport=None):
        self.service, self.store, self.transport = service, service.store, transport

    def configured(self, session_id):
        session = self.store.get(session_id)
        self.store.active(session)
        _, settings = self.service.settings(session.directory)
        config = settings.voice
        if not config.enabled:
            raise BrowserError(
                "Voice has not been configured for this directory", "voice_unavailable", 409
            )
        self.service.credentials.reference(session.directory, "voice", config.api_base_url, config.credential_ref)
        config._directory = session.directory
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
            "enabled": config.enabled and bool(config.credential_ref),
            "microphone_available": shutil.which("ffmpeg") is not None,
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
                (session_id, actor, request_id, kind, fingerprint, now,
                 now + DECODE_TIMEOUT_SECONDS + PROVIDER_TIMEOUT_SECONDS + 5),
            )
        return None

    def finish(self, session_id, actor, request_id, state, result=None):
        with self.store.connect(write=True) as db:
            db.execute(
                """UPDATE voice_requests SET state=?,result=?,lease_until=0
                   WHERE session_id=? AND actor=? AND request_id=? AND state='processing'""",
                (state, json.dumps(result) if result else None, session_id, actor, request_id),
            )

    async def provider_chunks(self, config, path, *, maximum, content_types=None, **kwargs):
        # Each awaited operation owns its timeout. A prefetched stream may later be
        # consumed by Starlette's separate response task, so no timeout scope spans yield.
        deadline = asyncio.get_running_loop().time() + PROVIDER_TIMEOUT_SECONDS
        try:
            async with httpx.AsyncClient(
                timeout=30, trust_env=False, follow_redirects=False, transport=self.transport
            ) as client:
                request = client.build_request(
                    "POST", config.api_base_url + path,
                    headers={"Authorization": "Bearer " + self.service.credentials.token(
                        config._directory, "voice", config.api_base_url, config.credential_ref
                    )},
                    **kwargs,
                )
                async with asyncio.timeout_at(deadline):
                    response = await client.send(request, stream=True)
                try:
                    if response.status_code != 200:
                        raise BrowserError("Speech service is unavailable", "speech_provider", 502)
                    content_type = response.headers.get("content-type", "").split(";")[0].lower()
                    if content_types and content_type not in content_types:
                        raise BrowserError("Invalid speech response", "speech_provider", 502)
                    size = 0
                    chunks = response.aiter_bytes()
                    while True:
                        try:
                            async with asyncio.timeout_at(deadline):
                                chunk = await anext(chunks)
                        except StopAsyncIteration:
                            break
                        size += len(chunk)
                        if size > maximum:
                            raise BrowserError("Speech response exceeded its limit", "speech_limit", 502)
                        if chunk:
                            yield chunk
                finally:
                    await response.aclose()
        except BrowserError:
            raise
        except (httpx.HTTPError, TimeoutError, KeyError):
            raise BrowserError("Speech service is unavailable", "speech_provider", 502) from None

    async def provider(self, config, path, *, maximum, **kwargs):
        content = bytearray()
        async with aclosing(self.provider_chunks(config, path, maximum=maximum, **kwargs)) as chunks:
            async for chunk in chunks:
                content.extend(chunk)
        return bytes(content)

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
            normalized = await normalize_recording(audio, mime, config.max_recording_seconds)
            self.configured(session_id)
            content = await self.provider(
                config,
                "/audio/transcriptions",
                maximum=65536,
                files={"file": ("speech.wav", normalized, "audio/wav")},
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

    def speech_input(self, session_id, actor, request_id, message_id):
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
        return config, text

    async def speech(self, session_id, actor, request_id, message_id):
        config, text = self.speech_input(session_id, actor, request_id, message_id)
        completed = False
        try:
            content = await self.provider(
                config,
                "/audio/speech",
                maximum=8_000_000,
                json=speech_payload(config, text, "wav"),
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


    async def speech_chunks(self, session_id, actor, request_id, message_id, *, validate):
        """Bounded raw PCM, keeping auth and provider lifetime attached to consumption."""
        config, text = self.speech_input(session_id, actor, request_id, message_id)
        completed = False
        size = 0
        try:
            async with aclosing(self.provider_chunks(
                config, "/audio/speech", maximum=8_000_000,
                content_types={"audio/pcm", "application/octet-stream"},
                json=speech_payload(config, text, "pcm"),
            )) as chunks:
                async for chunk in chunks:
                    self.configured(session_id)
                    validate()
                    size += len(chunk)
                    yield chunk
            if not size or size % 2:
                raise BrowserError("Invalid speech response", "speech_provider", 502)
            self.configured(session_id)
            validate()
            self.finish(session_id, actor, request_id, "completed")
            self.store.audit(session_id, actor, "voice_spoken", {"message_id": message_id})
            completed = True
        finally:
            if not completed:
                self.finish(session_id, actor, request_id, "unknown")


async def stream_audio(chunks):
    # Validate the provider and first chunk before committing a successful HTTP status.
    try:
        first = await anext(chunks)
    except BaseException:
        await chunks.aclose()
        raise

    async def body():
        try:
            yield first
            async for chunk in chunks:
                yield chunk
        finally:
            await chunks.aclose()

    class AudioResponse(StreamingResponse):
        async def __call__(self, scope, receive, send):
            try:
                await super().__call__(scope, receive, send)
            finally:
                # Also close a prefetched provider when HTTP headers cannot be sent.
                await chunks.aclose()

    return AudioResponse(body(), media_type="audio/pcm", headers={
        "Cache-Control": "no-store", "X-Accel-Buffering": "no",
        "X-Content-Type-Options": "nosniff",
    })


def require_voice_origin(request, settings, *, allow_extension=False):
    supplied = request.headers.get("origin")
    if allow_extension and re.fullmatch(r"chrome-extension://[a-p]{32}", supplied or ""):
        return
    try:
        accepted = supplied and origin(supplied) == origin(settings.public_base_url)
    except BrowserError:
        accepted = False
    if not accepted:
        raise BrowserError("Voice request origin was rejected", "forbidden", 403)
