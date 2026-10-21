import asyncio
import io
import json
import shutil
import subprocess
import sys
import wave

import pytest

from evander_browser_sessions_addon.models import (
    BrowserError,
    CreateRequest,
    MessageRequest,
    VoiceSettings,
)
from evander_browser_sessions_addon.voice import Voice


def test_speech_settings_require_https_and_only_credential_references():
    with pytest.raises(ValueError):
        VoiceSettings(api_base_url="http://speech.test/v1")
    with pytest.raises(ValueError):
        VoiceSettings(api_base_url="https://secret@speech.test/v1")
    with pytest.raises(ValueError):
        VoiceSettings(api_key_env="not a reference")
    assert not VoiceSettings().enabled
    assert VoiceSettings().voice == "onyx"


def test_transcription_retry_does_not_bill_twice_or_persist_audio(domain, hosted, speech_provider):
    service, _, _ = domain
    voice = Voice(service)
    audio = speech_provider["audio"]
    result = asyncio.run(voice.transcribe(hosted.id, "user:alice", "clip-a", audio, "audio/wav"))
    assert result["text"] == "Move the button to the left."
    assert (
        asyncio.run(voice.transcribe(hosted.id, "user:alice", "clip-a", audio, "audio/wav"))
        == result
    )
    assert len(speech_provider["calls"]) == 1
    request = speech_provider["calls"][0]
    assert request.url.path == "/v1/audio/transcriptions"
    assert b'filename="speech.wav"' in request.content
    assert b"gpt-transcribe" in request.content
    with pytest.raises(BrowserError) as conflict:
        asyncio.run(voice.transcribe(hosted.id, "user:alice", "clip-a", audio[:-2] + b"\1\0", "audio/wav"))
    assert conflict.value.status == 409
    with service.store.connect() as db:
        values = str([tuple(row) for row in db.execute("SELECT * FROM voice_requests")])
        audits = str([tuple(row) for row in db.execute("SELECT * FROM audit")])
    assert "RIFF" not in values + audits
    assert "fixture-speech-secret" not in str(voice.info(hosted.id)) + values + audits


def test_speech_can_only_read_an_agent_reply_from_this_browser(domain, hosted, speech_provider):
    service, _, _ = domain
    voice = Voice(service)
    question = service.enqueue_message(
        hosted.id, "user:alice", MessageRequest(request_id="question", body="Please demonstrate")
    )
    reply = service.reply("agent-a", hosted.id, question["id"], "I have moved the button.")
    audio = asyncio.run(voice.speech(hosted.id, "user:alice", "read-a", reply["id"]))
    assert json.loads(speech_provider["calls"][0].content)["voice"] == "onyx"
    assert audio.startswith(b"RIFF")
    assert len(speech_provider["calls"]) == 1
    with pytest.raises(BrowserError):
        asyncio.run(voice.speech(hosted.id, "user:alice", "read-b", question["id"]))
    other = service.create("agent-b", CreateRequest(mode="user_hosted", request_id="other"))
    with pytest.raises(BrowserError):
        asyncio.run(voice.speech(other["id"], "guest:bob", "read-c", reply["id"]))
    with pytest.raises(BrowserError) as duplicate:
        asyncio.run(voice.speech(hosted.id, "user:alice", "read-a", reply["id"]))
    assert duplicate.value.status == 409 and len(speech_provider["calls"]) == 1


def test_provider_failure_is_redacted_and_unknown_requests_are_not_replayed(
    domain, hosted, speech_provider
):
    service, _, _ = domain
    speech_provider["status"] = 503
    voice = Voice(service)
    with pytest.raises(BrowserError) as failure:
        asyncio.run(voice.transcribe(hosted.id, "user:alice", "failure", speech_provider["audio"], "audio/wav"))
    assert "fixture-speech-secret" not in str(failure.value)
    with pytest.raises(BrowserError) as replay:
        asyncio.run(voice.transcribe(hosted.id, "user:alice", "failure", speech_provider["audio"], "audio/wav"))
    assert replay.value.status == 409 and len(speech_provider["calls"]) == 1


def test_voice_rate_limit_is_shared_and_transcription_cache_does_not_count_again(
    domain, hosted, speech_provider
):
    service, _, _ = domain
    revision, settings = service.store.settings("root")
    settings.voice.requests_per_minute = 2
    service.store.save_settings("root", revision, settings)
    voice = Voice(service)
    for index in range(2):
        asyncio.run(
            voice.transcribe(hosted.id, "user:alice", f"clip-{index}", speech_provider["audio"], "audio/wav")
        )
    asyncio.run(voice.transcribe(hosted.id, "user:alice", "clip-0", speech_provider["audio"], "audio/wav"))
    with pytest.raises(BrowserError) as limited:
        asyncio.run(voice.transcribe(hosted.id, "user:bob", "clip-3", speech_provider["audio"], "audio/wav"))
    assert limited.value.status == 429 and len(speech_provider["calls"]) == 2


def test_cancelling_a_voice_request_releases_its_concurrency_slot(domain, hosted, speech_provider):
    service, _, _ = domain
    voice = Voice(service)

    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        count = 0

        async def provider(*args, **kwargs):
            nonlocal count
            count += 1
            if count == 2:
                entered.set()
            await release.wait()
            return b'{"text":"Please move the button."}'

        voice.provider = provider
        first = asyncio.create_task(
            voice.transcribe(hosted.id, "user:alice", "first", speech_provider["audio"], "audio/wav")
        )
        second = asyncio.create_task(
            voice.transcribe(hosted.id, "user:alice", "second", speech_provider["audio"], "audio/wav")
        )
        await asyncio.wait_for(entered.wait(), 5)
        with pytest.raises(BrowserError) as busy:
            await voice.transcribe(hosted.id, "user:bob", "blocked", speech_provider["audio"], "audio/wav")
        assert busy.value.status == 429
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        third = asyncio.create_task(
            voice.transcribe(hosted.id, "user:bob", "third", speech_provider["audio"], "audio/wav")
        )
        release.set()
        results = await asyncio.gather(second, third)
        assert all(result["text"] == "Please move the button." for result in results)

    asyncio.run(run())
    with service.store.connect() as db:
        states = {
            row["request_id"]: row["state"] for row in db.execute("SELECT * FROM voice_requests")
        }
    assert states == {"first": "unknown", "second": "completed", "third": "completed"}


@pytest.mark.parametrize("mime", ["audio/wav", "audio/webm"])
def test_overlong_audio_never_reaches_the_provider(domain, hosted, speech_provider, mime):
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(8000)
        audio.writeframes(b"\0\0" * (8000 * 90))
    clip = output.getvalue()
    if mime == "audio/webm":
        encoder = shutil.which("ffmpeg")
        assert encoder, "The optional voice test environment needs FFmpeg"
        clip = subprocess.run(
            [encoder, "-v", "error", "-i", "pipe:0", "-c:a", "libopus", "-f", "webm", "pipe:1"],
            input=clip, capture_output=True, check=True, timeout=10,
        ).stdout
    service, _, _ = domain
    config = Voice(service).configured(hosted.id)
    assert len(clip) < config.max_audio_bytes
    assert config.max_recording_seconds == 45
    with pytest.raises(BrowserError) as rejected:
        asyncio.run(Voice(service).transcribe(hosted.id, "user:alice", "overlong", clip, mime))
    assert rejected.value.code == "audio_duration" and rejected.value.status == 413
    assert not speech_provider["calls"]


@pytest.mark.parametrize("mime,encoder", [
    ("audio/wav", None), ("audio/webm", ["-c:a", "libopus", "-f", "webm"]),
    ("audio/ogg", ["-c:a", "libopus", "-f", "ogg"]),
    ("audio/mp4", ["-c:a", "aac", "-movflags", "frag_keyframe+empty_moov", "-f", "mp4"]),
])
def test_supported_recordings_are_normalized_before_provider(
    domain, hosted, speech_provider, mime, encoder
):
    clip = speech_provider["audio"]
    if encoder:
        clip = subprocess.run(
            [shutil.which("ffmpeg"), "-v", "error", "-i", "pipe:0", *encoder, "pipe:1"],
            input=clip, capture_output=True, check=True, timeout=10,
        ).stdout
    service, _, _ = domain
    asyncio.run(Voice(service).transcribe(hosted.id, "user:alice", "supported", clip, mime))
    request = speech_provider["calls"][0]
    # Read the multipart file sent across the provider boundary as an independent WAV reader.
    normalized = request.content.split(b"RIFF", 1)[1].rsplit(b"\r\n--", 1)[0]
    with wave.open(io.BytesIO(b"RIFF" + normalized), "rb") as audio:
        assert audio.getnchannels() == 1
        assert audio.getsampwidth() == 2
        assert audio.getframerate() == 16000
        assert 0 < audio.getnframes() / audio.getframerate() < 1


@pytest.mark.parametrize("missing", [False, True])
def test_unusable_recordings_do_not_reach_provider(
    domain, hosted, speech_provider, monkeypatch, missing
):
    import evander_browser_sessions_addon.voice as module
    service, _, _ = domain
    if missing:
        monkeypatch.setattr(module.shutil, "which", lambda _: None)
        info = Voice(service).info(hosted.id)
        assert info["enabled"] and not info["microphone_available"]
    with pytest.raises(BrowserError) as rejected:
        asyncio.run(Voice(service).transcribe(
            hosted.id, "user:alice", "invalid", b"not audio", "audio/wav"
        ))
    assert rejected.value.code == ("voice_unavailable" if missing else "audio_invalid")
    assert not speech_provider["calls"]


@pytest.mark.parametrize("stop", ["cancel", "timeout"])
def test_stopped_audio_decoder_is_reaped_before_request_finishes(
    domain, hosted, speech_provider, tmp_path, monkeypatch, stop
):
    import evander_browser_sessions_addon.voice as module
    decoder = tmp_path / "waiting-decoder"
    decoder.write_text("#!" + sys.executable + "\nimport time\ntime.sleep(30)\n")
    decoder.chmod(0o700)
    monkeypatch.setattr(module.shutil, "which", lambda _: str(decoder))
    if stop == "timeout":
        monkeypatch.setattr(module, "DECODE_TIMEOUT_SECONDS", .1)
    original = asyncio.create_subprocess_exec
    children = []
    service, _, _ = domain

    async def run():
        entered = asyncio.Event()
        async def create(*args, **kwargs):
            child = await original(*args, **kwargs)
            children.append(child)
            entered.set()
            return child
        monkeypatch.setattr(module.asyncio, "create_subprocess_exec", create)
        task = asyncio.create_task(Voice(service).transcribe(
            hosted.id, "user:alice", "stopped-decoder", speech_provider["audio"], "audio/wav"
        ))
        await asyncio.wait_for(entered.wait(), 3)
        if stop == "cancel":
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            with pytest.raises(BrowserError) as timed_out:
                await asyncio.wait_for(task, 3)
            assert timed_out.value.code == "audio_timeout"
        assert children[0].returncode is not None, "The decoder must be dead and reaped"
        assert not speech_provider["calls"]
        with service.store.connect() as db:
            assert db.execute("SELECT state FROM voice_requests").fetchone()[0] == "unknown"
    asyncio.run(run())


def test_session_closed_during_decoding_does_not_contact_provider(
    domain, hosted, speech_provider, monkeypatch
):
    import evander_browser_sessions_addon.voice as module
    service, _, _ = domain
    original = module.normalize_recording
    async def normalize(*args):
        result = await original(*args)
        service.close(hosted.id)
        return result
    monkeypatch.setattr(module, "normalize_recording", normalize)
    with pytest.raises(BrowserError) as closed:
        asyncio.run(Voice(service).transcribe(
            hosted.id, "user:alice", "closed-during-decode", speech_provider["audio"], "audio/wav"
        ))
    assert closed.value.status == 410
    assert not speech_provider["calls"]


def test_streamed_speech_starts_before_provider_finishes_and_closes_on_stop(
    domain, hosted, speech_provider
):
    import httpx
    import json

    service, _, _ = domain
    question = service.enqueue_message(
        hosted.id, "user:alice", MessageRequest(request_id="stream-question", body="Show me")
    )
    reply = service.reply("agent-a", hosted.id, question["id"], "Here is the shared application.")

    async def run():
        release = asyncio.Event()
        closed = []
        requests = []

        class Audio(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield b"\0\1" * 2400
                await release.wait()
                yield b"\0\2" * 2400

            async def aclose(self):
                closed.append(True)

        async def respond(request):
            requests.append(request)
            return httpx.Response(200, stream=Audio(), headers={"Content-Type": "audio/pcm"})

        voice = Voice(service, transport=httpx.MockTransport(respond))
        stream = voice.speech_chunks(
            hosted.id, "user:alice", "stream-stop", reply["id"], validate=lambda: None
        )
        first = await asyncio.wait_for(anext(stream), 2)
        assert first == b"\0\1" * 2400 and not release.is_set()
        assert json.loads(requests[0].content)["response_format"] == "pcm"
        await stream.aclose()
        assert closed, "Stopping playback must close the upstream HTTP stream immediately"
        with service.store.connect() as db:
            assert db.execute("SELECT state FROM voice_requests WHERE request_id='stream-stop'").fetchone()[0] == "unknown"
        with pytest.raises(BrowserError) as duplicate:
            await anext(voice.speech_chunks(
                hosted.id, "user:alice", "stream-stop", reply["id"], validate=lambda: None
            ))
        assert duplicate.value.status == 409 and len(requests) == 1

        release.set()
        chunks = [chunk async for chunk in voice.speech_chunks(
            hosted.id, "user:alice", "stream-complete", reply["id"], validate=lambda: None
        )]
        assert b"".join(chunks) == b"\0\1" * 2400 + b"\0\2" * 2400
        with service.store.connect() as db:
            assert db.execute("SELECT state FROM voice_requests WHERE request_id='stream-complete'").fetchone()[0] == "completed"

    asyncio.run(run())


def test_streamed_speech_rechecks_access_and_releases_prefetched_audio(
    domain, hosted, speech_provider
):
    import httpx
    from evander_browser_sessions_addon.voice import stream_audio

    service, _, _ = domain
    question = service.enqueue_message(
        hosted.id, "user:alice", MessageRequest(request_id="stream-auth-question", body="Show me")
    )
    reply = service.reply("agent-a", hosted.id, question["id"], "This is the application.")

    async def run():
        closed = []

        class Audio(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield b"\0\1" * 100
                yield b"\0\2" * 100

            async def aclose(self):
                closed.append(True)

        voice = Voice(service, transport=httpx.MockTransport(
            lambda request: httpx.Response(200, stream=Audio(), headers={"Content-Type": "audio/pcm"})
        ))
        allowed = True
        def validate():
            if not allowed:
                raise BrowserError("Permission changed", "forbidden", 403)
        stream = voice.speech_chunks(
            hosted.id, "user:alice", "stream-auth", reply["id"], validate=validate
        )
        assert await anext(stream) == b"\0\1" * 100
        allowed = False
        with pytest.raises(BrowserError, match="Permission changed"):
            await anext(stream)
        assert len(closed) == 1

        response = await stream_audio(voice.speech_chunks(
            hosted.id, "user:alice", "stream-unsent", reply["id"], validate=lambda: None
        ))
        assert response.media_type == "audio/pcm"
        async def unavailable(message):
            raise OSError("The viewer disconnected before headers were sent")
        async def receive():
            return {"type": "http.disconnect"}
        with pytest.raises(Exception):
            await response({"type": "http", "asgi": {"spec_version": "2.4"}}, receive, unavailable)
        assert len(closed) == 2
        with service.store.connect() as db:
            assert {row[0] for row in db.execute("SELECT state FROM voice_requests")} == {"unknown"}

    asyncio.run(run())


def test_normalizing_preserves_the_beginning_of_audio_after_silence():
    import array
    from evander_browser_sessions_addon.voice import normalize_recording

    output = io.BytesIO()
    # Independent timing markers: a quiet lead-in, an initial sound, then a later sound.
    samples = array.array("h", [0] * 4000 + [5000, -5000] * 1000 + [0] * 2000 + [9000, -9000] * 1000)
    if sys.byteorder != "little":
        samples.byteswap()
    with wave.open(output, "wb") as recording:
        recording.setnchannels(1)
        recording.setsampwidth(2)
        recording.setframerate(8000)
        recording.writeframes(samples.tobytes())
    normalized = asyncio.run(normalize_recording(output.getvalue(), "audio/wav", 5))
    with wave.open(io.BytesIO(normalized), "rb") as recording:
        result = array.array("h", recording.readframes(recording.getnframes()))
        if sys.byteorder != "little":
            result.byteswap()
        assert recording.getnframes() == 20000
        assert max(abs(value) for value in result[8100:11800]) > 4000
        assert max(abs(value) for value in result[16100:19800]) > 8000
        assert max(abs(value) for value in result[:7800]) < 100


def test_speech_stream_timeout_belongs_to_its_consumer_task(
    domain, hosted, speech_provider, monkeypatch
):
    import httpx
    import evander_browser_sessions_addon.voice as module

    service, _, _ = domain
    question = service.enqueue_message(
        hosted.id, "user:alice", MessageRequest(request_id="timeout-question", body="Show me")
    )
    reply = service.reply("agent-a", hosted.id, question["id"], "This is the application.")
    monkeypatch.setattr(module, "PROVIDER_TIMEOUT_SECONDS", .05)

    async def run():
        closed = []
        class Audio(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield b"\0\1" * 100
                await asyncio.Event().wait()
            async def aclose(self):
                closed.append(True)
        voice = Voice(service, transport=httpx.MockTransport(
            lambda request: httpx.Response(200, stream=Audio(), headers={"Content-Type": "audio/pcm"})
        ))
        stream = voice.speech_chunks(
            hosted.id, "user:alice", "cross-task-timeout", reply["id"], validate=lambda: None
        )
        await anext(stream)  # Route prefetch; a response task consumes the remainder.
        consumer = asyncio.create_task(anext(stream))
        with pytest.raises(BrowserError) as failure:
            await asyncio.wait_for(consumer, 2)
        assert failure.value.code == "speech_provider" and closed
        assert not asyncio.current_task().cancelling()
        with service.store.connect() as db:
            assert db.execute("SELECT state FROM voice_requests").fetchone()[0] == "unknown"

    asyncio.run(run())
