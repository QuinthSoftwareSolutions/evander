import asyncio
import io
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
