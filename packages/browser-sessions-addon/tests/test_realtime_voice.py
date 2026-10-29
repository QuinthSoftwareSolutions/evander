import asyncio
import base64
import io
import json
from pathlib import Path
import runpy
import wave

import pytest

from evander.directories import DirectoryStore
from evander.host_config import HostConfig
from evander_browser_sessions_addon.models import BrowserError, MessageRequest, VoiceSettings
from evander_browser_sessions_addon.voice import Voice


class Provider:
    def __init__(self, *, status="completed"):
        self.events = iter([
            {"type": "session.updated"},
            {"type": "response.output_audio.delta", "delta": base64.b64encode(b"\x01\x00" * 2400).decode()},
            {"type": "response.done", "response": {"status": status}},
        ])
        self.sent = []
        self.closed = False

    async def connect(self, url, **kwargs):
        self.url, self.headers = url, kwargs["additional_headers"]
        return self

    async def send(self, payload):
        self.sent.append(json.loads(payload))

    async def recv(self):
        return json.dumps(next(self.events))

    async def close(self):
        self.closed = True


def configure(service):
    revision, settings = service.store.settings("root")
    settings.voice = VoiceSettings(
        enabled=True, api_base_url=settings.voice.api_base_url,
        credential_ref=settings.voice.credential_ref,
    )
    service.store.save_settings("root", revision, settings)


def reply(service, session):
    question = service.enqueue_message(session.id, "user:alice", MessageRequest(
        request_id="question", body="Explain the change"))
    return service.reply("agent-a", session.id, question["id"], "Order 42 is ready.")["id"]


def test_realtime_voice_uses_host_credential_and_preserves_buffered_wav(domain, hosted, speech_provider):
    service, _, _ = domain
    configure(service)
    provider = Provider()
    voice = Voice(service, websocket_connect=provider.connect)
    content = asyncio.run(voice.speech(hosted.id, "user:alice", "read", reply(service, hosted)))
    with wave.open(io.BytesIO(content)) as audio:
        assert audio.getframerate() == 24000 and audio.getnframes() == 2400
    assert provider.url.endswith("/realtime?model=gpt-realtime-2.1-mini")
    assert provider.headers == {"Authorization": "Bearer fixture-speech-secret"}
    assert provider.sent[0]["session"]["audio"]["output"]["voice"] == "cedar"
    assert provider.sent[1]["item"]["content"][0]["text"] == "Order 42 is ready."
    assert provider.closed and not speech_provider["calls"]
    with service.store.connect() as db:
        assert db.execute("SELECT state FROM voice_requests").fetchone()[0] == "completed"


@pytest.mark.parametrize("stop", ["failed", "revoked", "cancel"])
def test_realtime_stream_cleanup_never_records_partial_speech_as_completed(
    domain, hosted, speech_provider, stop
):
    service, _, _ = domain
    configure(service)
    provider = Provider(status="failed" if stop == "failed" else "completed")
    voice = Voice(service, websocket_connect=provider.connect)
    message = reply(service, hosted)

    async def run():
        chunks = voice.speech_chunks(hosted.id, "user:alice", "stream", message, validate=lambda: None)
        try:
            assert await anext(chunks) == b"\x01\x00" * 2400
            if stop == "cancel":
                await chunks.aclose()
            else:
                if stop == "revoked":
                    service.close(hosted.id)
                with pytest.raises(BrowserError):
                    await anext(chunks)
        finally:
            await chunks.aclose()

    asyncio.run(run())
    assert provider.closed
    with service.store.connect() as db:
        assert db.execute("SELECT state FROM voice_requests").fetchone()[0] == "unknown"


def test_one_time_browser_migration_preserves_credentials_and_does_not_create_inherited_rows(domain):
    service, _, _ = domain
    runtime = service.runtime_dir
    (runtime / "host-config.json").write_text(HostConfig(runtime_dir=runtime).model_dump_json())
    DirectoryStore(runtime / "directories.json").ensure_root()
    revision, settings = service.store.settings("root")
    settings.voice = VoiceSettings(
        enabled=True, speech_backend="speech_api", speech_model="gpt-4o-mini-tts-2025-12-15",
        voice="onyx", speed=2.5, instructions="Keep the chosen delivery.",
    )
    service.store.save_settings("root", revision, settings)
    before_revision, loaded = service.store.settings("root")
    assert loaded.voice.speech_model == "gpt-4o-mini-tts-2025-12-15"
    migration = runpy.run_path(str(Path(__file__).parents[3] / "scripts/migrate_openai_deprecations.py"))
    plan, _ = migration["inventory"](runtime)
    assert service.store.settings("root")[0] == before_revision
    result = migration["apply"](plan, backup_dir=runtime.parent / "recovery", maintenance=True)
    assert result["applied"]
    after_revision, loaded = service.store.settings("root/child")
    assert after_revision == before_revision + 1
    assert loaded.voice.speech_backend == "realtime"
    assert loaded.voice.speech_model == "gpt-realtime-2.1-mini"
    assert loaded.voice.voice == "cedar" and loaded.voice.speed == 1.5
    assert loaded.voice.instructions == "Keep the chosen delivery."
    with service.store.connect() as db:
        assert db.execute("SELECT directory FROM settings").fetchall()[0][0] == "root"
        assert db.execute("SELECT count(*) FROM settings").fetchone()[0] == 1
    assert migration["apply"](plan, backup_dir=runtime.parent / "recovery", maintenance=True)["already_migrated"]
