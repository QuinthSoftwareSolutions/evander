import asyncio

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
    audio = b"synthetic microphone bytes"
    result = asyncio.run(voice.transcribe(hosted.id, "user:alice", "clip-a", audio, "audio/webm"))
    assert result["text"] == "Move the button to the left."
    assert (
        asyncio.run(voice.transcribe(hosted.id, "user:alice", "clip-a", audio, "audio/webm"))
        == result
    )
    assert len(speech_provider["calls"]) == 1
    request = speech_provider["calls"][0]
    assert request.url.path == "/v1/audio/transcriptions"
    assert b'filename="speech.webm"' in request.content
    assert b"gpt-transcribe" in request.content
    with pytest.raises(BrowserError) as conflict:
        asyncio.run(voice.transcribe(hosted.id, "user:alice", "clip-a", b"different", "audio/webm"))
    assert conflict.value.status == 409
    with service.store.connect() as db:
        values = str([tuple(row) for row in db.execute("SELECT * FROM voice_requests")])
        audits = str([tuple(row) for row in db.execute("SELECT * FROM audit")])
    assert "synthetic microphone bytes" not in values + audits
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
        asyncio.run(voice.transcribe(hosted.id, "user:alice", "failure", b"clip", "audio/webm"))
    assert "fixture-speech-secret" not in str(failure.value)
    with pytest.raises(BrowserError) as replay:
        asyncio.run(voice.transcribe(hosted.id, "user:alice", "failure", b"clip", "audio/webm"))
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
            voice.transcribe(hosted.id, "user:alice", f"clip-{index}", b"clip", "audio/webm")
        )
    asyncio.run(voice.transcribe(hosted.id, "user:alice", "clip-0", b"clip", "audio/webm"))
    with pytest.raises(BrowserError) as limited:
        asyncio.run(voice.transcribe(hosted.id, "user:bob", "clip-3", b"clip", "audio/webm"))
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
            voice.transcribe(hosted.id, "user:alice", "first", b"1", "audio/webm")
        )
        second = asyncio.create_task(
            voice.transcribe(hosted.id, "user:alice", "second", b"2", "audio/webm")
        )
        await asyncio.wait_for(entered.wait(), 2)
        with pytest.raises(BrowserError) as busy:
            await voice.transcribe(hosted.id, "user:bob", "blocked", b"3", "audio/webm")
        assert busy.value.status == 429
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        third = asyncio.create_task(
            voice.transcribe(hosted.id, "user:bob", "third", b"3", "audio/webm")
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
