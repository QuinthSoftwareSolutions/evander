import asyncio
import gzip
import json
import os
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from agent_sandbox.config import GatewayConfig
from agent_sandbox.directory_config import DirectoryConfigStore
from agent_sandbox.directories import DirectoryService, DirectoryStore
from agent_sandbox.models import SessionManifest
from agent_sandbox.relays.openai import OpenAIRelay, create_app
from agent_sandbox.repositories.session_manifests import JsonSessionManifestRepository


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


def write_session(
    config: GatewayConfig,
    *,
    session_id: str,
    directory_path: str = "root",
    web_search_enabled: bool = False,
) -> None:
    store = JsonSessionManifestRepository(config.session_store_path)
    store.upsert_session(
        SessionManifest(
            session_id=session_id,
            directory_path=directory_path,
            allowed_tools=[],
            web_search_enabled=web_search_enabled,
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )


def test_relay_returns_503_without_api_key(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    write_session(config, session_id="session-1")
    app = create_app(config)
    client = TestClient(app)
    os.environ.pop("OPENAI_API_KEY", None)

    response = client.post(
        "/v1/session/session-1/responses", json={"model": "gpt-5.2-codex", "input": "hello"}
    )

    assert response.status_code == 503
    assert "OPENAI_API_KEY" in response.text


def test_relay_rejects_missing_session_id(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    app = create_app(config)
    client = TestClient(app)

    response = client.post("/v1/responses", json={"model": "gpt-5.2-codex", "input": "hello"})

    assert response.status_code == 400
    assert response.json() == {"error": "session_id query parameter is required"}


def test_relay_injects_authorization_header(tmp_path: Path) -> None:
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["authorization"] = request.headers.get("authorization", "")
        captured["accept-encoding"] = request.headers.get("accept-encoding", "")
        captured["path"] = request.url.path
        return httpx.Response(200, json={"id": "resp_test", "object": "response"})

    transport = httpx.MockTransport(handler)
    config = make_config(tmp_path)
    write_session(config, session_id="session-1")
    app = create_app(config, transport=transport)
    client = TestClient(app)
    os.environ["OPENAI_API_KEY"] = "test-key"

    response = client.post(
        "/v1/session/session-1/responses", json={"model": "gpt-5.2-codex", "input": "hello"}
    )

    assert response.status_code == 200
    assert captured["authorization"] == "Bearer test-key"
    assert captured["accept-encoding"] == "identity"
    assert captured["path"] == "/v1/responses"


def test_relay_rejects_hosted_web_search_for_session_without_flag(tmp_path: Path) -> None:
    called = False

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, json={"id": "resp_test", "object": "response"})

    transport = httpx.MockTransport(handler)
    config = make_config(tmp_path)
    write_session(config, session_id="session-1")
    app = create_app(config, transport=transport)
    client = TestClient(app)
    os.environ["OPENAI_API_KEY"] = "test-key"

    response = client.post(
        "/v1/session/session-1/responses",
        json={
            "model": "gpt-5.2-codex",
            "input": "hello",
            "tools": [{"type": "web_search_preview"}],
        },
    )

    assert response.status_code == 403
    assert "Hosted web search is disabled" in response.text
    assert called is False


def test_relay_allows_hosted_web_search_for_session_with_flag(tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["payload"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(200, json={"id": "resp_test", "object": "response"})

    transport = httpx.MockTransport(handler)
    config = make_config(tmp_path)
    write_session(config, session_id="session-1", web_search_enabled=True)
    app = create_app(config, transport=transport)
    client = TestClient(app)
    os.environ["OPENAI_API_KEY"] = "test-key"

    response = client.post(
        "/v1/session/session-1/responses",
        json={
            "model": "gpt-5.2-codex",
            "input": "hello",
            "tools": [{"type": "web_search_preview"}],
        },
    )

    assert response.status_code == 200
    assert captured["payload"] == {
        "model": "gpt-5.2-codex",
        "input": "hello",
        "tools": [{"type": "web_search_preview"}],
    }


def test_relay_uses_root_directory_provider_overlay(tmp_path: Path) -> None:
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["authorization"] = request.headers.get("authorization", "")
        captured["url"] = str(request.url)
        return httpx.Response(200, json={"id": "resp_test", "object": "response"})

    transport = httpx.MockTransport(handler)
    config = make_config(tmp_path)
    write_session(config, session_id="session-1")
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

    response = client.post(
        "/v1/session/session-1/responses", json={"model": "gpt-5.2-codex", "input": "hello"}
    )

    assert response.status_code == 200
    assert captured["authorization"] == "Bearer root-key"
    assert captured["url"] == "https://override.example/v1/responses"


def test_relay_uses_session_directory_provider_overlay(tmp_path: Path) -> None:
    captured: dict[str, str] = {}
    config = make_config(tmp_path)
    directory_store = DirectoryStore(config.directories_store_path)
    directory_store.ensure_root()
    directory_service = DirectoryService(directory_store)
    directory_service.create(parent="root", name="team-a")
    DirectoryConfigStore(directory_store, runtime_dir=config.runtime_dir).write(
        "root/team-a",
        domain="providers",
        payload={
            "openai_api_base_url": "https://team-a.example/v1",
            "openai_api_key_env": "TEAM_A_OPENAI_TOKEN",
        },
    )
    write_session(config, session_id="session-team-a", directory_path="root/team-a")

    def handler(request: httpx.Request) -> httpx.Response:
        captured["authorization"] = request.headers.get("authorization", "")
        captured["url"] = str(request.url)
        return httpx.Response(200, json={"id": "resp_test", "object": "response"})

    transport = httpx.MockTransport(handler)
    app = create_app(config, transport=transport)
    client = TestClient(app)
    os.environ["TEAM_A_OPENAI_TOKEN"] = "team-a-key"
    os.environ.pop("OPENAI_API_KEY", None)

    response = client.post(
        "/v1/session/session-team-a/responses", json={"model": "gpt-5.2-codex", "input": "hello"}
    )

    assert response.status_code == 200
    assert captured["authorization"] == "Bearer team-a-key"
    assert captured["url"] == "https://team-a.example/v1/responses"


def test_relay_routes_azure_openai_responses_requests_through_session_directory_overlays(
    tmp_path: Path,
) -> None:
    captured: dict[str, object] = {}
    config = make_config(tmp_path)
    directory_store = DirectoryStore(config.directories_store_path)
    directory_store.ensure_root()
    directory_service = DirectoryService(directory_store)
    directory_service.create(parent="root", name="team-a")

    write_provider_overlay(
        config,
        {
            "azure_openai_endpoint": "https://example-resource.openai.azure.com/",
            "azure_openai_api_key_env": "AZURE_OPENAI_API_KEY",
            "azure_openai_model_deployments": {
                "gpt-5.4": "deployment-root",
            },
        },
    )
    DirectoryConfigStore(directory_store, runtime_dir=config.runtime_dir).write(
        "root/team-a",
        domain="providers",
        payload={
            "openai_provider_mode": "azure_openai",
            "azure_openai_model_deployments": {
                "gpt-5.4": "deployment-team-a",
            },
        },
    )
    write_session(config, session_id="session-azure", directory_path="root/team-a")

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["api-key"] = request.headers.get("api-key", "")
        captured["authorization"] = request.headers.get("authorization", "")
        captured["accept-encoding"] = request.headers.get("accept-encoding", "")
        captured["payload"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(200, json={"id": "resp_test", "object": "response"})

    transport = httpx.MockTransport(handler)
    app = create_app(config, transport=transport)
    client = TestClient(app)
    os.environ["AZURE_OPENAI_API_KEY"] = "azure-key"
    os.environ.pop("OPENAI_API_KEY", None)

    response = client.post(
        "/v1/responses?session_id=session-azure&foo=bar&api-version=old",
        json={"model": "gpt-5.4", "input": "hello"},
    )

    assert response.status_code == 200
    assert captured["url"] == (
        "https://example-resource.openai.azure.com/openai/v1/responses"
        "?foo=bar&api-version=preview"
    )
    assert captured["api-key"] == "azure-key"
    assert captured["authorization"] == ""
    assert captured["accept-encoding"] == "identity"
    assert captured["payload"] == {
        "model": "deployment-team-a",
        "input": "hello",
    }


def test_relay_returns_503_when_azure_openai_api_key_env_is_missing(tmp_path: Path) -> None:
    called = False

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, json={"id": "resp_test", "object": "response"})

    transport = httpx.MockTransport(handler)
    config = make_config(tmp_path)
    write_provider_overlay(
        config,
        {
            "openai_provider_mode": "azure_openai",
            "azure_openai_endpoint": "https://example-resource.openai.azure.com/",
            "azure_openai_api_key_env": "AZURE_OPENAI_API_KEY",
            "azure_openai_model_deployments": {"gpt-5.4": "deployment-root"},
        },
    )
    write_session(config, session_id="session-azure")
    app = create_app(config, transport=transport)
    client = TestClient(app)
    os.environ.pop("AZURE_OPENAI_API_KEY", None)

    response = client.post(
        "/v1/session/session-azure/responses",
        json={"model": "gpt-5.4", "input": "hello"},
    )

    assert response.status_code == 503
    assert "AZURE_OPENAI_API_KEY" in response.text
    assert called is False


def test_relay_returns_400_when_azure_openai_model_mapping_is_missing(tmp_path: Path) -> None:
    called = False

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, json={"id": "resp_test", "object": "response"})

    transport = httpx.MockTransport(handler)
    config = make_config(tmp_path)
    write_provider_overlay(
        config,
        {
            "openai_provider_mode": "azure_openai",
            "azure_openai_endpoint": "https://example-resource.openai.azure.com/",
            "azure_openai_api_key_env": "AZURE_OPENAI_API_KEY",
            "azure_openai_model_deployments": {"gpt-5.4": "deployment-root"},
        },
    )
    write_session(config, session_id="session-azure")
    app = create_app(config, transport=transport)
    client = TestClient(app)
    os.environ["AZURE_OPENAI_API_KEY"] = "azure-key"

    response = client.post(
        "/v1/session/session-azure/responses",
        json={"model": "gpt-4.1-mini", "input": "hello"},
    )

    assert response.status_code == 400
    assert "gpt-4.1-mini" in response.text
    assert "azure_openai_model_deployments" in response.text
    assert called is False


def test_relay_ignores_unknown_allowed_tools_when_resolving_session(tmp_path: Path) -> None:
    captured: dict[str, str] = {}
    config = make_config(tmp_path)
    directory_store = DirectoryStore(config.directories_store_path)
    directory_store.ensure_root()
    directory_service = DirectoryService(directory_store)
    directory_service.create(parent="root", name="team-a")
    DirectoryConfigStore(directory_store, runtime_dir=config.runtime_dir).write(
        "root/team-a",
        domain="providers",
        payload={
            "openai_api_base_url": "https://team-a.example/v1",
            "openai_api_key_env": "TEAM_A_OPENAI_TOKEN",
        },
    )
    config.session_store_path.write_text(
        json.dumps(
            {
                "session-team-a": {
                    "session_id": "session-team-a",
                    "directory_path": "root/team-a",
                    "allowed_tools": ["future_tool_name"],
                    "expires_at": "2099-01-01T00:00:00Z",
                }
            }
        ),
        encoding="utf-8",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        captured["authorization"] = request.headers.get("authorization", "")
        captured["url"] = str(request.url)
        return httpx.Response(200, json={"id": "resp_test", "object": "response"})

    transport = httpx.MockTransport(handler)
    app = create_app(config, transport=transport)
    client = TestClient(app)
    os.environ["TEAM_A_OPENAI_TOKEN"] = "team-a-key"
    os.environ.pop("OPENAI_API_KEY", None)

    response = client.post(
        "/v1/session/session-team-a/responses", json={"model": "gpt-5.2-codex", "input": "hello"}
    )

    assert response.status_code == 200
    assert captured["authorization"] == "Bearer team-a-key"
    assert captured["url"] == "https://team-a.example/v1/responses"


def test_relay_logs_resolved_api_key_env_name(tmp_path: Path, caplog) -> None:
    captured: dict[str, str] = {}
    config = make_config(tmp_path)
    directory_store = DirectoryStore(config.directories_store_path)
    directory_store.ensure_root()
    directory_service = DirectoryService(directory_store)
    directory_service.create(parent="root", name="team-a")
    DirectoryConfigStore(directory_store, runtime_dir=config.runtime_dir).write(
        "root/team-a",
        domain="providers",
        payload={"openai_api_key_env": "TEAM_A_OPENAI_TOKEN"},
    )
    write_session(config, session_id="session-team-a", directory_path="root/team-a")

    def handler(request: httpx.Request) -> httpx.Response:
        captured["authorization"] = request.headers.get("authorization", "")
        return httpx.Response(200, json={"id": "resp_test", "object": "response"})

    transport = httpx.MockTransport(handler)
    app = create_app(config, transport=transport)
    client = TestClient(app)
    os.environ["TEAM_A_OPENAI_TOKEN"] = "team-a-key"
    os.environ.pop("OPENAI_API_KEY", None)
    caplog.set_level("INFO", logger="agent_sandbox.relays.openai")

    response = client.post(
        "/v1/session/session-team-a/responses", json={"model": "gpt-5.2-codex", "input": "hello"}
    )

    assert response.status_code == 200
    assert captured["authorization"] == "Bearer team-a-key"
    assert "relay_resolved_api_key_env" in caplog.text
    assert "session_id=session-team-a" in caplog.text
    assert "request_kind=http" in caplog.text
    assert "path=/v1/session/session-team-a/responses" in caplog.text
    assert "api_key_env=TEAM_A_OPENAI_TOKEN" in caplog.text
    assert "team-a-key" not in caplog.text


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
    write_session(config, session_id="session-1")
    app = create_app(config, transport=transport)
    client = TestClient(app)
    os.environ["OPENAI_API_KEY"] = "test-key"

    response = client.post(
        "/v1/session/session-1/responses", json={"model": "gpt-5.2-codex", "input": "hello"}
    )

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


def test_relay_stream_transport_errors_are_suppressed_with_warning(tmp_path: Path, caplog) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            stream=_ErrorAfterChunkStream(),
        )

    transport = httpx.MockTransport(handler)
    config = make_config(tmp_path)
    write_session(config, session_id="session-1")
    relay = OpenAIRelay(config, transport=transport)
    app = relay.create_app()
    client = TestClient(app)
    os.environ["OPENAI_API_KEY"] = "test-key"

    caplog.set_level("INFO", logger="agent_sandbox.relays.openai")

    with client.stream(
        "POST",
        "/v1/session/session-1/responses",
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
    write_session(config, session_id="session-1")
    relay = OpenAIRelay(config, transport=transport)
    app = relay.create_app()
    client = TestClient(app)
    os.environ["OPENAI_API_KEY"] = "test-key"

    caplog.set_level("INFO", logger="agent_sandbox.relays.openai")

    with client.stream(
        "POST",
        "/v1/session/session-1/responses",
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
    write_session(config, session_id="session-1")
    relay = OpenAIRelay(config)

    @asynccontextmanager
    async def fake_connect(url: str, *, headers: dict[str, str], timeout_seconds: float):
        captured["url"] = url
        captured["headers"] = headers
        captured["timeout_seconds"] = timeout_seconds
        yield upstream

    relay._connect_upstream_websocket = fake_connect  # type: ignore[method-assign]
    app = relay.create_app()
    client = TestClient(app)
    os.environ["OPENAI_API_KEY"] = "test-key"

    with client.websocket_connect("/v1/session/session-1/responses") as websocket:
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


def test_relay_rejects_websocket_hosted_web_search_without_session_flag(
    tmp_path: Path,
) -> None:
    upstream = _FakeUpstreamWebSocket()
    config = make_config(tmp_path)
    write_session(config, session_id="session-1")
    relay = OpenAIRelay(config)

    @asynccontextmanager
    async def fake_connect(url: str, *, headers: dict[str, str], timeout_seconds: float):
        del url, headers, timeout_seconds
        yield upstream

    relay._connect_upstream_websocket = fake_connect  # type: ignore[method-assign]
    app = relay.create_app()
    client = TestClient(app)
    os.environ["OPENAI_API_KEY"] = "test-key"

    with client.websocket_connect("/v1/session/session-1/responses") as websocket:
        websocket.send_text(
            json.dumps(
                {
                    "type": "session.update",
                    "session": {"tools": [{"type": "web_search_preview"}]},
                }
            )
        )
        with pytest.raises(WebSocketDisconnect) as exc_info:
            websocket.receive_text()

    assert exc_info.value.code == 1008
    assert upstream.sent_messages == []
    assert upstream.closed is True


def test_relay_allows_websocket_hosted_web_search_with_session_flag(tmp_path: Path) -> None:
    upstream = _FakeUpstreamWebSocket()
    config = make_config(tmp_path)
    write_session(config, session_id="session-1", web_search_enabled=True)
    relay = OpenAIRelay(config)

    @asynccontextmanager
    async def fake_connect(url: str, *, headers: dict[str, str], timeout_seconds: float):
        del url, headers, timeout_seconds
        yield upstream

    relay._connect_upstream_websocket = fake_connect  # type: ignore[method-assign]
    app = relay.create_app()
    client = TestClient(app)
    os.environ["OPENAI_API_KEY"] = "test-key"
    message = json.dumps(
        {"type": "session.update", "session": {"tools": [{"type": "web_search_preview"}]}}
    )

    with client.websocket_connect("/v1/session/session-1/responses") as websocket:
        websocket.send_text(message)
        deadline = time.time() + 1.0
        while time.time() < deadline and upstream.sent_messages != [message]:
            time.sleep(0.01)
        assert upstream.sent_messages == [message]

    assert upstream.closed


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


def test_relay_still_accepts_query_session_id_for_http(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    write_session(config, session_id="session-1")
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(200, json={"id": "resp_test", "object": "response"})

    app = create_app(config, transport=httpx.MockTransport(handler))
    client = TestClient(app)
    os.environ["OPENAI_API_KEY"] = "test-key"

    response = client.post(
        "/v1/responses?session_id=session-1", json={"model": "gpt-5.2-codex", "input": "hello"}
    )

    assert response.status_code == 200
    assert captured["url"] == "https://api.openai.com/v1/responses"
