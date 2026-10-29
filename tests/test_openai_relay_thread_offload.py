"""Database work must not stall the relay's unrelated requests or stream cleanup."""

import asyncio
import threading
from datetime import UTC, datetime, timedelta

import anyio
import httpx
import pytest
from starlette.requests import Request

from evander.config import GatewayConfig
from evander.models import SessionManifest
from evander.relays.openai import OpenAIRelay
from evander.repositories.session_manifests import JsonSessionManifestRepository


def _relay(tmp_path, monkeypatch, transport):
    config = GatewayConfig(
        runtime_dir=tmp_path,
        audit_log_path=tmp_path / "audit.jsonl",
        session_store_path=tmp_path / "sessions.json",
        approval_store_path=tmp_path / "approvals.json",
        draft_store_path=tmp_path / "drafts.json",
        email_store_path=tmp_path / "emails.json",
        discord_command_store_path=tmp_path / "discord.json",
        openai_api_key_env="EVANDER_TEST_RELAY_KEY",
    )
    monkeypatch.setenv("EVANDER_TEST_RELAY_KEY", "synthetic-key")
    JsonSessionManifestRepository(config.session_store_path).upsert_session(
        SessionManifest(
            session_id="session-1",
            allowed_tools=[],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    return OpenAIRelay(config, transport=transport)


@pytest.mark.parametrize(
    ("method", "stream"),
    [
        ("_resolve_request_context", False),
        ("_operator_steering_injection", False),
        ("_mark_operator_steering_delivered", False),
        ("_record_model_usage_event", False),
        ("_record_stream_model_usage_event", True),
    ],
)
def test_blocked_database_work_allows_health_request(tmp_path, monkeypatch, method, stream):
    class Body(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'data: {"model":"synthetic","usage":{"input_tokens":1}}\n\n'

    transport = httpx.MockTransport(
        lambda request: (
            httpx.Response(200, stream=Body(), headers={"content-type": "text/event-stream"})
            if stream
            else httpx.Response(200, json={"model": "synthetic", "usage": {"input_tokens": 1}})
        )
    )
    relay = _relay(tmp_path, monkeypatch, transport)
    original = getattr(relay, method)
    entered = threading.Event()
    release = threading.Event()
    threads = []

    def slow_operation(*args, **kwargs):
        threads.append(threading.get_ident())
        entered.set()
        assert release.wait(3), "event loop could not release the blocked operation"
        return original(*args, **kwargs)

    monkeypatch.setattr(relay, method, slow_operation)

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=relay.create_app()), base_url="http://relay.test"
        ) as client:
            pending = asyncio.create_task(
                client.post(
                    "/v1/session/session-1/responses",
                    json={"model": "synthetic", "input": "hello"},
                    headers={"accept": "text/event-stream"} if stream else {},
                )
            )
            try:

                async def wait_entered():
                    while not entered.is_set():
                        await asyncio.sleep(0.005)

                await asyncio.wait_for(wait_entered(), timeout=2)
                assert not pending.done()
                response = await asyncio.wait_for(client.get("/healthz"), timeout=1)
                assert response.status_code == 200
                assert len(threads) == 1
                assert threads[0] != threading.get_ident()
            finally:
                release.set()
                result = await asyncio.wait_for(pending, timeout=5)
            assert result.status_code == 200

    asyncio.run(run())


def test_cancelled_stream_records_usage_and_closes_upstream(tmp_path, monkeypatch):
    closed = []
    recorded = []

    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'data: {"model":"synthetic","usage":{"input_tokens":1}}\n\n'
            await anyio.sleep_forever()

        async def aclose(self):
            closed.append(True)

    relay = _relay(
        tmp_path,
        monkeypatch,
        httpx.MockTransport(lambda request: httpx.Response(200, stream=Stream())),
    )
    original = relay._record_stream_model_usage_event

    def record(**kwargs):
        recorded.append(threading.get_ident())
        original(**kwargs)

    monkeypatch.setattr(relay, "_record_stream_model_usage_event", record)

    async def run():
        response = await relay._stream_response(
            request=Request(
                {"type": "http", "method": "POST", "path": "/v1/responses", "headers": []}
            ),
            session_id="session-1",
            upstream_path="responses",
            upstream_url="http://upstream.test/responses",
            headers={},
            request_body=b'{"model":"synthetic"}',
            timeout_seconds=5,
        )
        received = anyio.Event()

        async def consume():
            async for _ in response.body_iterator:
                received.set()

        async with anyio.create_task_group() as group:
            group.start_soon(consume)
            await received.wait()
            group.cancel_scope.cancel()
        assert len(recorded) == 1
        assert recorded[0] != threading.get_ident()
        assert closed

    anyio.run(run)


@pytest.mark.parametrize("stage", ["mark", "release"])
def test_cancelled_stream_setup_closes_client_before_iterator_ownership(
    tmp_path, monkeypatch, stage
):
    from types import SimpleNamespace
    from evander.relays import openai

    relay = _relay(tmp_path, monkeypatch, httpx.MockTransport(lambda request: httpx.Response(200)))
    entered = threading.Event()
    release = threading.Event()

    class Context:
        exited = False

        async def __aenter__(self):
            if stage == "release":
                raise RuntimeError("synthetic upstream failure")
            return SimpleNamespace(status_code=200, headers={})

        async def __aexit__(self, *args):
            self.exited = True

    class Client:
        closed = False
        context = Context()

        def stream(self, **kwargs):
            return self.context

        async def aclose(self):
            self.closed = True

    client = Client()
    monkeypatch.setattr(openai.httpx, "AsyncClient", lambda **kwargs: client)

    def block(*args, **kwargs):
        entered.set()
        assert release.wait(3)

    method = (
        "_mark_operator_steering_delivered"
        if stage == "mark"
        else "_release_operator_steering_note"
    )
    monkeypatch.setattr(relay, method, block)

    async def run():
        task = asyncio.create_task(
            relay._stream_response(
                request=Request(
                    {"type": "http", "method": "POST", "path": "/v1/responses", "headers": []}
                ),
                session_id="session-1",
                upstream_path="responses",
                upstream_url="http://upstream.test/responses",
                headers={},
                request_body=b"{}",
                timeout_seconds=5,
            )
        )

        async def wait_entered():
            while not entered.is_set():
                await asyncio.sleep(0.005)

        try:
            await asyncio.wait_for(wait_entered(), timeout=2)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, timeout=1)
            assert client.closed
            assert client.context.exited == (stage == "mark")
        finally:
            release.set()

    asyncio.run(run())
