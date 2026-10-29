from __future__ import annotations

import asyncio
import base64
from contextlib import aclosing
import io
import json
import wave

import pytest

from evander.realtime_speech import RealtimeSpeechError, pcm_wav, realtime_speech_chunks


class Socket:
    def __init__(self, events):
        self.events = iter([{"type": "session.created"}, {"type": "session.updated"}, *events])
        self.sent = []
        self.closed = False

    async def send(self, payload):
        self.sent.append(json.loads(payload))

    async def recv(self):
        try:
            event = next(self.events)
        except StopIteration:
            raise OSError("connection lost private upstream error") from None
        return json.dumps(event)

    async def close(self):
        self.closed = True


def delta(pcm=b"\x01\x00" * 2400):
    return {"type": "response.output_audio.delta", "delta": base64.b64encode(pcm).decode()}


def done(status="completed"):
    return {"type": "response.done", "response": {"status": status}}


def stream(socket, **overrides):
    async def connect(url, **kwargs):
        socket.connection = (url, kwargs)
        return socket

    options = dict(
        base_url="https://api.openai.com/v1", model="configured-speech", voice="cedar",
        instructions="Warm, brisk delivery.", speed=1.1, text="Order 42 costs 19.50 EUR.",
        headers={"Authorization": "Bearer synthetic-secret"}, maximum=10000, connect=connect,
    )
    options.update(overrides)
    return realtime_speech_chunks(**options)


def test_configured_speech_produces_pcm_and_sealed_wav_without_sending_secrets_as_content():
    socket = Socket([delta(), done()])

    async def consume():
        return b"".join([chunk async for chunk in stream(socket)])

    pcm = asyncio.run(consume())
    with wave.open(io.BytesIO(pcm_wav(pcm))) as audio:
        assert (audio.getnchannels(), audio.getsampwidth(), audio.getframerate()) == (1, 2, 24000)
        assert audio.getnframes() == 2400
    url, kwargs = socket.connection
    assert url == "wss://api.openai.com/v1/realtime?model=configured-speech"
    assert kwargs["proxy"] is None
    assert kwargs["additional_headers"] == {"Authorization": "Bearer synthetic-secret"}
    config = socket.sent[0]["session"]
    assert config["audio"]["output"] == {
        "format": {"type": "audio/pcm", "rate": 24000}, "voice": "cedar", "speed": 1.1,
    }
    assert config["tools"] == [] and config["tool_choice"] == "none"
    assert socket.sent[1]["item"]["content"][0]["text"] == "Order 42 costs 19.50 EUR."
    assert "synthetic-secret" not in json.dumps(socket.sent)
    assert socket.closed


@pytest.mark.parametrize("events,limit", [
    ([delta(), done("failed")], 10000),
    ([delta(), done("incomplete")], 10000),
    ([done()], 10000),
    ([delta(b"x"), done()], 10000),
    ([{"type": "response.output_audio.delta", "delta": "!bad!"}], 10000),
    ([delta(), done()], 10),
    ([{"type": "error", "error": {"message": "private upstream error"}}], 10000),
    ([delta()], 10000),
])
def test_incomplete_invalid_and_oversized_speech_cannot_finish_successfully(events, limit):
    socket = Socket(events)

    async def consume():
        return [chunk async for chunk in stream(socket, maximum=limit)]

    with pytest.raises(RealtimeSpeechError) as failure:
        asyncio.run(consume())
    assert "private upstream error" not in str(failure.value)
    assert socket.closed


def test_prefetched_stream_can_change_consuming_tasks_and_closes_on_access_revocation():
    socket = Socket([delta(), delta(), done()])
    allowed = True

    def authorize():
        if not allowed:
            raise PermissionError("revoked")

    async def consume():
        nonlocal allowed
        async with aclosing(stream(socket, authorize=authorize)) as chunks:
            assert await asyncio.create_task(anext(chunks)) == b"\x01\x00" * 2400
            allowed = False
            with pytest.raises(PermissionError, match="revoked"):
                await asyncio.create_task(anext(chunks))

    asyncio.run(consume())
    assert socket.closed


@pytest.mark.parametrize("cancel", [False, True])
def test_timeout_and_cancellation_close_the_upstream(cancel):
    class Stalled(Socket):
        async def recv(self):
            await asyncio.Event().wait()

    socket = Stalled([])

    async def consume():
        async with aclosing(stream(socket, timeout=0.01)) as chunks:
            task = asyncio.create_task(anext(chunks))
            await asyncio.sleep(0)
            if cancel:
                task.cancel()
            with pytest.raises(asyncio.CancelledError if cancel else RealtimeSpeechError):
                await task

    asyncio.run(consume())
    assert socket.closed
