import asyncio
import gzip
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest
from starlette.testclient import TestClient

from agent_sandbox.config import GatewayConfig
from agent_sandbox.directory_config import DirectoryConfigStore
from agent_sandbox.directories import DirectoryStore
from agent_sandbox.openai_relay import OpenAIRelay, create_app


def make_config(tmp_path: Path) -> GatewayConfig:
    return GatewayConfig(
        runtime_dir=tmp_path,
        audit_log_path=tmp_path / "audit.jsonl",
        session_store_path=tmp_path / "sessions.json",
        approval_store_path=tmp_path / "approvals.json",
        draft_store_path=tmp_path / "drafts.json",
        email_store_path=tmp_path / "emails.json",
        discord_command_store_path=tmp_path / "discord.json",
        openai_api_base_url="https://api.openai.com/v1",
        openai_api_key_env="OPENAI_API_KEY",
    )


def write_provider_overlay(config: GatewayConfig, payload: dict[str, object]) -> None:
    directory_store = DirectoryStore(config.directories_store_path)
    directory_store.ensure_root()
    DirectoryConfigStore(directory_store, runtime_dir=config.runtime_dir).write(
        "root",
        domain="providers",
        payload=payload,
    )


def test_relay_returns_503_without_api_key(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    app = create_app(config)
    client = TestClient(app)
    os.environ.pop("OPENAI_API_KEY", None)

    response = client.post("/v1/responses", json={"model": "gpt-5.2-codex", "input": "hello"})

    assert response.status_code == 503
    assert "OPENAI_API_KEY" in response.text


def test_relay_injects_authorization_header(tmp_path: Path) -> None:
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["authorization"] = request.headers.get("authorization", "")
        captured["accept-encoding"] = request.headers.get("accept-encoding", "")
        captured["path"] = request.url.path
        return httpx.Response(200, json={"id": "resp_test", "object": "response"})

    transport = httpx.MockTransport(handler)
    config = make_config(tmp_path)
    app = create_app(config, transport=transport)
    client = TestClient(app)
    os.environ["OPENAI_API_KEY"] = "test-key"

    response = client.post("/v1/responses", json={"model": "gpt-5.2-codex", "input": "hello"})

    assert response.status_code == 200
    assert captured["authorization"] == "Bearer test-key"
    assert captured["accept-encoding"] == "identity"
    assert captured["path"] == "/v1/responses"


def test_relay_uses_root_directory_provider_overlay(tmp_path: Path) -> None:
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["authorization"] = request.headers.get("authorization", "")
        captured["url"] = str(request.url)
        return httpx.Response(200, json={"id": "resp_test", "object": "response"})

    transport = httpx.MockTransport(handler)
    config = make_config(tmp_path)
    write_provider_overlay(
        config,
        {
            "openai_api_base_url": "https://override.example/v1",
            "openai_api_key_env": "ROOT_OPENAI_TOKEN",
        },
    )
    app = create_app(config, transport=transport)
    client = TestClient(app)
    os.environ["ROOT_OPENAI_TOKEN"] = "root-key"

    response = client.post("/v1/responses", json={"model": "gpt-5.2-codex", "input": "hello"})

    assert response.status_code == 200
    assert captured["authorization"] == "Bearer root-key"
    assert captured["url"] == "https://override.example/v1/responses"


def test_relay_strips_content_encoding_from_response(tmp_path: Path) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        compressed = gzip.compress(b'{"id":"resp_test","object":"response"}')
        return httpx.Response(
            200,
            headers={"content-encoding": "gzip", "content-type": "application/json"},
            content=compressed,
        )

    transport = httpx.MockTransport(handler)
    config = make_config(tmp_path)
    app = create_app(config, transport=transport)
    client = TestClient(app)
    os.environ["OPENAI_API_KEY"] = "test-key"

    response = client.post("/v1/responses", json={"model": "gpt-5.2-codex", "input": "hello"})

    assert response.status_code == 200
    assert "content-encoding" not in response.headers


class _ErrorAfterChunkStream(httpx.AsyncByteStream):
    async def __aiter__(self):
        yield b"data: first\n\n"
        raise httpx.ReadError("peer closed stream")

    async def aclose(self) -> None:
        return None


class _CleanStream(httpx.AsyncByteStream):
    async def __aiter__(self):
        yield b"data: first\n\n"
        yield b"data: second\n\n"

    async def aclose(self) -> None:
        return None


class _FakeUpstreamWebSocket:
    def __init__(self) -> None:
        self.sent_messages: list[str | bytes] = []
        self.received_messages: list[str | bytes] = []
        self._recv_queue: asyncio.Queue[str | bytes | None] = asyncio.Queue()
        self.closed = False

    async def send(self, message: str | bytes) -> None:
        self.sent_messages.append(message)

    async def recv(self) -> str | bytes:
        message = await self._recv_queue.get()
        if message is None:
            raise RuntimeError("upstream closed")
        self.received_messages.append(message)
        return message

    async def push(self, message: str | bytes) -> None:
        await self._recv_queue.put(message)

    async def close(self) -> None:
        self.closed = True
        await self._recv_queue.put(None)


def test_relay_stream_transport_errors_are_suppressed_with_warning(
    tmp_path: Path, caplog
) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            stream=_ErrorAfterChunkStream(),
        )

    transport = httpx.MockTransport(handler)
    config = make_config(tmp_path)
    relay = OpenAIRelay(config, transport=transport)
    app = relay.create_app()
    client = TestClient(app)
    os.environ["OPENAI_API_KEY"] = "test-key"

    caplog.set_level("INFO", logger="agent_sandbox.openai_relay")

    with client.stream(
        "POST",
        "/v1/responses",
        headers={"accept": "text/event-stream"},
        json={"model": "gpt-5.2-codex", "input": "hello"},
    ) as response:
        body = b"".join(response.iter_bytes())

    assert response.status_code == 200
    assert b"data: first" in body
    assert relay.stream_started_count == 1
    assert relay.stream_clean_end_count == 0
    assert relay.stream_transport_terminated_count == 1
    assert "relay_stream_transport_terminated" in caplog.text
    assert "exception_class=ReadError" in caplog.text


def test_relay_stream_clean_end_logs_completion(tmp_path: Path, caplog) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            stream=_CleanStream(),
        )

    transport = httpx.MockTransport(handler)
    config = make_config(tmp_path)
    relay = OpenAIRelay(config, transport=transport)
    app = relay.create_app()
    client = TestClient(app)
    os.environ["OPENAI_API_KEY"] = "test-key"

    caplog.set_level("INFO", logger="agent_sandbox.openai_relay")

    with client.stream(
        "POST",
        "/v1/responses",
        headers={"accept": "text/event-stream"},
        json={"model": "gpt-5.2-codex", "input": "hello"},
    ) as response:
        body = b"".join(response.iter_bytes())

    assert response.status_code == 200
    assert b"data: first" in body
    assert b"data: second" in body
    assert relay.stream_started_count == 1
    assert relay.stream_clean_end_count == 1
    assert relay.stream_transport_terminated_count == 0
    assert "relay_stream_request_start" in caplog.text
    assert "relay_stream_upstream_response" in caplog.text
    assert "relay_stream_completed" in caplog.text


def test_relay_proxies_websocket_messages_and_injects_auth(tmp_path: Path) -> None:
    captured: dict[str, object] = {}
    upstream = _FakeUpstreamWebSocket()
    config = make_config(tmp_path)
    relay = OpenAIRelay(config)

    @asynccontextmanager
    async def fake_connect(url: str, *, headers: dict[str, str]):
        captured["url"] = url
        captured["headers"] = headers
        yield upstream

    relay._connect_upstream_websocket = fake_connect  # type: ignore[method-assign]
    app = relay.create_app()
    client = TestClient(app)
    os.environ["OPENAI_API_KEY"] = "test-key"

    with client.websocket_connect("/v1/responses") as websocket:
        websocket.send_text("hello upstream")
        deadline = time.time() + 1.0
        while time.time() < deadline and upstream.sent_messages != ["hello upstream"]:
            time.sleep(0.01)
        assert upstream.sent_messages == ["hello upstream"]

        asyncio.run(upstream.push('{"id":"resp_1"}'))
        assert websocket.receive_text() == '{"id":"resp_1"}'

    assert upstream.closed
    assert captured["url"] == "wss://api.openai.com/v1/responses"
    headers = captured["headers"]
    assert isinstance(headers, dict)
    assert headers["authorization"] == "Bearer test-key"
    assert all(not key.lower().startswith("sec-websocket-") for key in headers)


def test_relay_treats_clean_upstream_websocket_close_as_normal(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    relay = OpenAIRelay(config)
    client_messages: list[dict[str, object]] = []

    class DummyClientWebSocket:
        async def receive(self):
            await asyncio.sleep(10)

        async def close(self, code: int = 1000, reason: str = "") -> None:
            client_messages.append({"code": code, "reason": reason})

    class DummyUpstream:
        async def recv(self):
            from websockets.exceptions import ConnectionClosedOK

            raise ConnectionClosedOK(None, None)

    async def run_case() -> None:
        await relay._proxy_websocket_traffic(DummyClientWebSocket(), DummyUpstream())

    asyncio.run(run_case())
    assert client_messages == [{"code": 1000, "reason": ""}]
