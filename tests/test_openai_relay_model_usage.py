from __future__ import annotations

import asyncio
import json
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
from starlette.testclient import TestClient

from agent_sandbox.config import GatewayConfig
from agent_sandbox.directory_config import DirectoryConfigStore
from agent_sandbox.directories import DirectoryService, DirectoryStore
from agent_sandbox.model_usage.runtime import register_model_usage_correlation_context
from agent_sandbox.model_usage.store import ModelUsageStore
from agent_sandbox.models import SessionManifest
from agent_sandbox.operator_steering import operator_steering_note_store
from agent_sandbox.relays.openai import OpenAIRelay, create_app
from agent_sandbox.repositories.session_manifests import JsonSessionManifestRepository


class _UsageStream(httpx.AsyncByteStream):
    async def __aiter__(self):
        yield (
            b'data: {"type":"response.output_text.delta","delta":"secret completion text"}\n\n'
        )
        yield (
            b'data: {"type":"response.completed","response":{"id":"resp-stream",'
            b'"type":"response","model":"gpt-5.2-codex","output_text":"secret completion text",'
            b'"usage":{"input_tokens":7,"output_tokens":8,"total_tokens":15}}}\n\n'
        )
        yield b"data: [DONE]\n\n"

    async def aclose(self) -> None:
        return None


class _AnthropicUsageStream(httpx.AsyncByteStream):
    async def __aiter__(self):
        yield (
            b'data: {"type":"message_start","message":{"id":"msg-stream",'
            b'"type":"message","model":"claude-sonnet-4-6",'
            b'"usage":{"input_tokens":7,"cache_creation_input_tokens":5,'
            b'"cache_read_input_tokens":3,"cache_creation":{"ephemeral_5m_input_tokens":2,'
            b'"ephemeral_1h_input_tokens":3}}}}\n\n'
        )
        yield b'data: {"type":"content_block_delta","delta":{"text":"secret completion text"}}\n\n'
        yield b'data: {"type":"message_delta","usage":{"output_tokens":8}}\n\n'

    async def aclose(self) -> None:
        return None


class _FakeUpstreamWebSocket:
    def __init__(self) -> None:
        self.sent_messages: list[str | bytes] = []
        self._recv_queue: asyncio.Queue[str | bytes | None] = asyncio.Queue()
        self.closed = False

    async def send(self, message: str | bytes) -> None:
        self.sent_messages.append(message)

    async def recv(self) -> str | bytes:
        message = await self._recv_queue.get()
        if message is None:
            raise RuntimeError("upstream closed")
        return message

    async def push(self, message: str | bytes) -> None:
        await self._recv_queue.put(message)

    async def close(self) -> None:
        self.closed = True
        await self._recv_queue.put(None)


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
    directory_path: str = "root/team-a",
) -> None:
    store = JsonSessionManifestRepository(config.session_store_path)
    store.upsert_session(
        SessionManifest(
            session_id=session_id,
            directory_path=directory_path,
            allowed_tools=[],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )


def ensure_directory(config: GatewayConfig, directory_path: str = "root/team-a") -> None:
    directory_store = DirectoryStore(config.directories_store_path)
    directory_store.ensure_root()
    directory_service = DirectoryService(directory_store)
    parent, name = directory_path.rsplit("/", 1)
    directory_service.create(parent=parent, name=name)


def wait_for_upstream_messages(
    upstream: _FakeUpstreamWebSocket, expected: list[str | bytes]
) -> None:
    deadline = time.time() + 1.0
    while time.time() < deadline and upstream.sent_messages != expected:
        time.sleep(0.01)
    assert upstream.sent_messages == expected


def test_openai_relay_records_usage_without_raw_prompt_or_completion_text(
    tmp_path: Path, monkeypatch
) -> None:
    runtime_dir = tmp_path / "runtime"
    config = make_config(runtime_dir)
    ensure_directory(config)
    write_session(config, session_id="session-1")
    context_started_at = datetime.now(UTC)
    register_model_usage_correlation_context(
        runtime_dir=runtime_dir,
        session_id="session-1",
        dispatch_id="dispatch-1",
        workflow_run_id="workflow-1",
        effect_id="effect-1",
        execution_backend="libvirt_vm",
        directory_path="root/team-a",
        model="gpt-5.2-codex",
        trace_id="trace-1",
        started_at=context_started_at,
        expires_at=context_started_at + timedelta(hours=1),
    )
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["payload"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(
            200,
            json={
                "id": "resp-1",
                "type": "response",
                "model": "gpt-5.2-codex",
                "output_text": "secret completion text",
                "usage": {
                    "input_tokens": 11,
                    "output_tokens": 22,
                    "total_tokens": 33,
                    "input_tokens_details": {"cached_tokens": 4},
                },
            },
        )

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    app = create_app(config, transport=httpx.MockTransport(handler))
    client = TestClient(app)

    response = client.post(
        "/v1/session/session-1/responses",
        json={
            "model": "gpt-5.2-codex",
            "input": "secret prompt text",
        },
    )

    assert response.status_code == 200
    assert captured["payload"] == {
        "model": "gpt-5.2-codex",
        "input": "secret prompt text",
    }

    store = ModelUsageStore(runtime_dir / "model-usage.db")
    events = store.list_events(session_id="session-1")
    assert len(events) == 1
    event = events[0]
    event_json = json.dumps(event.model_dump(mode="json"), sort_keys=True)

    assert event.event_id == "resp-1"
    assert event.provider == "openai"
    assert event.provider_endpoint_identifier == "responses"
    assert event.normalized_usage_status == "available"
    assert event.input_tokens == 11
    assert event.output_tokens == 22
    assert event.total_tokens == 33
    assert event.session_id == "session-1"
    assert event.workflow_run_id == "workflow-1"
    assert event.effect_id == "effect-1"
    assert event.node_id == "effect-1"
    assert event.dispatch_id == "dispatch-1"
    assert event.agent_harness == "codex"
    assert event.execution_backend == "libvirt_vm"
    assert event.directory_path == "root/team-a"
    assert event.trace_id == "trace-1"
    assert event.request_body_sha256 is not None
    assert event.response_body_sha256 is not None
    assert event.request_body_bytes is not None
    assert event.response_body_bytes is not None
    assert "secret prompt text" not in event_json
    assert "secret completion text" not in event_json
    assert event.raw_usage_metadata == {
        "provider_response_kind": "response",
        "usage": {
            "input_tokens": 11,
            "output_tokens": 22,
            "total_tokens": 33,
            "input_tokens_details": {"cached_tokens": 4},
        },
        "response_metadata": {
            "status_code": 200,
            "content_type": "application/json",
        },
        "request_metadata": {
            "method": "POST",
            "path": "/v1/responses",
        },
    }


def test_openai_relay_delivers_operator_steering_note_on_next_responses_call(
    tmp_path: Path, monkeypatch
) -> None:
    runtime_dir = tmp_path / "runtime"
    config = make_config(runtime_dir)
    ensure_directory(config)
    write_session(config, session_id="session-1")
    register_model_usage_correlation_context(
        runtime_dir=runtime_dir,
        session_id="session-1",
        dispatch_id="dispatch-steer",
        workflow_run_id="workflow-1",
        effect_id="effect-1",
        execution_backend="libvirt_vm",
        directory_path="root/team-a",
        model="gpt-5.2-codex",
    )
    steering_store = operator_steering_note_store(runtime_dir)
    note = steering_store.create_note(
        session_id="session-1",
        dispatch_id="dispatch-steer",
        message="Stop analyzing and write the tests now.",
        created_by="operator@example.com",
    )
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["payload"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(
            200,
            json={
                "id": "resp-steered",
                "type": "response",
                "model": "gpt-5.2-codex",
                "usage": {"input_tokens": 1, "output_tokens": 2, "total_tokens": 3},
            },
        )

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    app = create_app(config, transport=httpx.MockTransport(handler))
    client = TestClient(app)

    response = client.post(
        "/v1/session/session-1/responses",
        json={
            "model": "gpt-5.2-codex",
            "input": [
                {
                    "role": "user",
                    "content": [{"type": "input_text", "text": "Do the work."}],
                }
            ],
        },
    )

    assert response.status_code == 200
    payload = captured["payload"]
    assert isinstance(payload, dict)
    assert isinstance(payload["input"], list)
    assert len(payload["input"]) == 2
    steering_item = payload["input"][1]
    assert steering_item["role"] == "user"
    assert "Stop analyzing and write the tests now." in steering_item["content"][0]["text"]

    delivered = steering_store.get_note(note.note_id)
    assert delivered is not None
    assert delivered.status == "delivered"
    assert delivered.provider == "openai"
    assert delivered.provider_endpoint == "responses"
    assert delivered.delivery_attempts == 1


def test_openai_relay_does_not_deliver_operator_steering_note_to_responses_compact(
    tmp_path: Path, monkeypatch
) -> None:
    runtime_dir = tmp_path / "runtime"
    config = make_config(runtime_dir)
    ensure_directory(config)
    write_session(config, session_id="session-1")
    register_model_usage_correlation_context(
        runtime_dir=runtime_dir,
        session_id="session-1",
        dispatch_id="dispatch-steer",
        directory_path="root/team-a",
        model="gpt-5.2-codex",
    )
    steering_store = operator_steering_note_store(runtime_dir)
    note = steering_store.create_note(
        session_id="session-1",
        dispatch_id="dispatch-steer",
        message="This should wait for a real generation call.",
    )
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["payload"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(
            200,
            json={
                "id": "resp-compact",
                "type": "response",
                "model": "gpt-5.2-codex",
                "usage": {"input_tokens": 1, "output_tokens": 0, "total_tokens": 1},
            },
        )

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    app = create_app(config, transport=httpx.MockTransport(handler))
    client = TestClient(app)

    response = client.post(
        "/v1/session/session-1/responses/compact",
        json={"model": "gpt-5.2-codex", "input": "compact this"},
    )

    assert response.status_code == 200
    assert captured["payload"] == {"model": "gpt-5.2-codex", "input": "compact this"}
    pending = steering_store.get_note(note.note_id)
    assert pending is not None
    assert pending.status == "pending"
    assert pending.delivery_attempts == 0


def test_openai_relay_delivers_operator_steering_note_on_next_chat_completion_call(
    tmp_path: Path, monkeypatch
) -> None:
    runtime_dir = tmp_path / "runtime"
    config = make_config(runtime_dir)
    ensure_directory(config)
    write_session(config, session_id="session-1")
    register_model_usage_correlation_context(
        runtime_dir=runtime_dir,
        session_id="session-1",
        dispatch_id="dispatch-chat-steer",
        directory_path="root/team-a",
        model="gpt-5.2-codex",
    )
    steering_store = operator_steering_note_store(runtime_dir)
    note = steering_store.create_note(
        session_id="session-1",
        dispatch_id="dispatch-chat-steer",
        message="Keep the response brief.",
    )
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["payload"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-steered",
                "object": "chat.completion",
                "model": "gpt-5.2-codex",
                "choices": [{"message": {"content": "ok"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    app = create_app(config, transport=httpx.MockTransport(handler))
    client = TestClient(app)

    response = client.post(
        "/v1/session/session-1/chat/completions",
        json={
            "model": "gpt-5.2-codex",
            "messages": [{"role": "user", "content": "hello"}],
        },
    )

    assert response.status_code == 200
    payload = captured["payload"]
    assert isinstance(payload, dict)
    assert isinstance(payload["messages"], list)
    assert payload["messages"][-1]["role"] == "user"
    assert "Keep the response brief." in payload["messages"][-1]["content"]
    delivered = steering_store.get_note(note.note_id)
    assert delivered is not None
    assert delivered.status == "delivered"
    assert delivered.provider_endpoint == "chat/completions"


def test_openai_relay_returns_non_streaming_response_when_usage_recording_fails(
    tmp_path: Path, monkeypatch, caplog
) -> None:
    runtime_dir = tmp_path / "runtime"
    config = make_config(runtime_dir)
    ensure_directory(config)
    write_session(config, session_id="session-1")

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "resp-usage-failure",
                "type": "response",
                "model": "gpt-5.2-codex",
                "usage": {"input_tokens": 1, "output_tokens": 2, "total_tokens": 3},
            },
        )

    def fail_record_usage(self: OpenAIRelay, **_: object) -> None:
        raise OSError("usage store locked")

    monkeypatch.setattr(OpenAIRelay, "_record_model_usage_event", fail_record_usage)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    caplog.set_level("WARNING", logger="agent_sandbox.relays.openai")
    app = create_app(config, transport=httpx.MockTransport(handler))
    client = TestClient(app)

    response = client.post(
        "/v1/session/session-1/responses",
        json={"model": "gpt-5.2-codex", "input": "hello"},
    )

    assert response.status_code == 200
    assert response.json()["id"] == "resp-usage-failure"
    assert any(
        record.message.startswith("relay_http_model_usage_record_failed")
        for record in caplog.records
    )
    assert ModelUsageStore(runtime_dir / "model-usage.db").list_events() == []


def test_openai_relay_returns_streaming_response_when_usage_recording_fails(
    tmp_path: Path, monkeypatch, caplog
) -> None:
    runtime_dir = tmp_path / "runtime"
    config = make_config(runtime_dir)
    ensure_directory(config)
    write_session(config, session_id="session-1")

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            stream=_UsageStream(),
        )

    def fail_record_usage(self: OpenAIRelay, **_: object) -> None:
        raise OSError("usage store locked")

    monkeypatch.setattr(OpenAIRelay, "_record_stream_model_usage_event", fail_record_usage)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    caplog.set_level("WARNING", logger="agent_sandbox.relays.openai")
    app = create_app(config, transport=httpx.MockTransport(handler))
    client = TestClient(app)

    with client.stream(
        "POST",
        "/v1/session/session-1/responses",
        headers={"accept": "text/event-stream"},
        json={"model": "gpt-5.2-codex", "input": "hello"},
    ) as response:
        body = b"".join(response.iter_bytes())

    assert response.status_code == 200
    assert b"secret completion text" in body
    assert any(
        record.message.startswith("relay_stream_model_usage_record_failed")
        for record in caplog.records
    )
    assert ModelUsageStore(runtime_dir / "model-usage.db").list_events() == []


def test_openai_relay_marks_usage_unavailable_when_response_has_no_usage(
    tmp_path: Path, monkeypatch
) -> None:
    runtime_dir = tmp_path / "runtime"
    config = make_config(runtime_dir)
    ensure_directory(config)
    write_session(config, session_id="session-1")

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "resp-2",
                "type": "response",
                "model": "gpt-5.2-codex",
                "output_text": "secret completion text",
            },
        )

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    app = create_app(config, transport=httpx.MockTransport(handler))
    client = TestClient(app)

    response = client.post(
        "/v1/session/session-1/responses",
        json={
            "model": "gpt-5.2-codex",
            "input": "secret prompt text",
        },
    )

    assert response.status_code == 200

    event = ModelUsageStore(runtime_dir / "model-usage.db").get_event("resp-2")
    assert event is not None
    event_json = json.dumps(event.model_dump(mode="json"), sort_keys=True)

    assert event.normalized_usage_status == "usage_unavailable"
    assert event.input_tokens is None
    assert event.output_tokens is None
    assert event.total_tokens is None
    assert event.request_status == "success"
    assert "secret prompt text" not in event_json
    assert "secret completion text" not in event_json


def test_openai_relay_maps_chat_completion_token_names(tmp_path: Path, monkeypatch) -> None:
    runtime_dir = tmp_path / "runtime"
    config = make_config(runtime_dir)
    ensure_directory(config)
    write_session(config, session_id="session-1")

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-1",
                "object": "chat.completion",
                "model": "gpt-5.2-codex",
                "choices": [{"message": {"content": "secret completion text"}}],
                "usage": {
                    "prompt_tokens": 3,
                    "completion_tokens": 4,
                    "total_tokens": 7,
                },
            },
        )

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    app = create_app(config, transport=httpx.MockTransport(handler))
    client = TestClient(app)

    response = client.post(
        "/v1/session/session-1/chat/completions",
        json={
            "model": "gpt-5.2-codex",
            "messages": [{"role": "user", "content": "secret prompt text"}],
        },
    )

    assert response.status_code == 200

    event = ModelUsageStore(runtime_dir / "model-usage.db").get_event("chatcmpl-1")
    assert event is not None
    event_json = json.dumps(event.model_dump(mode="json"), sort_keys=True)
    assert event.input_tokens == 3
    assert event.output_tokens == 4
    assert event.total_tokens == 7
    assert event.normalized_usage_status == "available"
    assert "secret prompt text" not in event_json
    assert "secret completion text" not in event_json


def test_openai_relay_records_responses_compact_usage(tmp_path: Path, monkeypatch) -> None:
    runtime_dir = tmp_path / "runtime"
    config = make_config(runtime_dir)
    ensure_directory(config)
    write_session(config, session_id="session-1")
    register_model_usage_correlation_context(
        runtime_dir=runtime_dir,
        session_id="session-1",
        dispatch_id="dispatch-compact",
        workflow_run_id="workflow-compact",
        effect_id="effect-compact",
        execution_backend="libvirt_vm",
        directory_path="root/team-a",
        model="gpt-5.2-codex",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/responses/compact"
        return httpx.Response(
            200,
            json={
                "id": "resp-compact",
                "type": "response",
                "usage": {"input_tokens": 2, "output_tokens": 3, "total_tokens": 5},
            },
        )

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    app = create_app(config, transport=httpx.MockTransport(handler))
    client = TestClient(app)

    response = client.post(
        "/v1/session/session-1/responses/compact",
        json={"model": "gpt-5.2-codex", "input": "secret prompt text"},
    )

    assert response.status_code == 200
    event = ModelUsageStore(runtime_dir / "model-usage.db").get_event("resp-compact")
    assert event is not None
    event_json = json.dumps(event.model_dump(mode="json"), sort_keys=True)
    assert event.provider_endpoint_identifier == "responses/compact"
    assert event.model == "gpt-5.2-codex"
    assert event.total_tokens == 5
    assert event.workflow_run_id == "workflow-compact"
    assert "secret prompt text" not in event_json


def test_openai_relay_records_azure_http_usage(tmp_path: Path, monkeypatch) -> None:
    runtime_dir = tmp_path / "runtime"
    config = make_config(runtime_dir)
    ensure_directory(config)
    write_provider_overlay(
        config,
        {
            "model_provider_mode": "azure_openai",
            "azure_openai_endpoint": "https://example-resource.openai.azure.com/",
            "azure_openai_api_key_env": "AZURE_OPENAI_API_KEY",
            "azure_openai_model_deployments": {"gpt-5.4": "deployment-root"},
        },
    )
    write_session(config, session_id="session-azure")
    register_model_usage_correlation_context(
        runtime_dir=runtime_dir,
        session_id="session-azure",
        dispatch_id="dispatch-azure",
        workflow_run_id="workflow-azure",
        effect_id="effect-azure",
        execution_backend="libvirt_vm",
        directory_path="root/team-a",
        model="gpt-5.4",
    )
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["query"] = request.url.query.decode("utf-8")
        captured["payload"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(
            200,
            json={
                "id": "resp-azure",
                "type": "response",
                "model": "deployment-root",
                "usage": {"input_tokens": 6, "output_tokens": 7, "total_tokens": 13},
            },
        )

    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "azure-key")
    app = create_app(config, transport=httpx.MockTransport(handler))
    client = TestClient(app)

    response = client.post(
        "/v1/session/session-azure/responses",
        json={"model": "gpt-5.4", "input": "secret prompt text"},
    )

    assert response.status_code == 200
    assert captured["path"] == "/openai/v1/responses"
    assert "api-version=preview" in str(captured["query"])
    assert captured["payload"] == {"model": "deployment-root", "input": "secret prompt text"}
    event = ModelUsageStore(runtime_dir / "model-usage.db").get_event("resp-azure")
    assert event is not None
    event_json = json.dumps(event.model_dump(mode="json"), sort_keys=True)
    assert event.provider == "openai"
    assert event.provider_endpoint_identifier == "responses"
    assert event.model == "deployment-root"
    assert event.total_tokens == 13
    assert event.session_id == "session-azure"
    assert event.workflow_run_id == "workflow-azure"
    assert "secret prompt text" not in event_json


def test_openai_relay_records_anthropic_http_usage(tmp_path: Path, monkeypatch) -> None:
    runtime_dir = tmp_path / "runtime"
    config = make_config(runtime_dir)
    ensure_directory(config)
    write_provider_overlay(
        config,
        {
            "model_provider_mode": "anthropic",
            "anthropic_api_base_url": "https://anthropic.example",
            "anthropic_api_key_env": "ANTHROPIC_TEST_KEY",
        },
    )
    write_session(config, session_id="session-anthropic")
    register_model_usage_correlation_context(
        runtime_dir=runtime_dir,
        session_id="session-anthropic",
        dispatch_id="dispatch-anthropic",
        workflow_run_id="workflow-anthropic",
        effect_id="effect-anthropic",
        execution_backend="container_worker_vm",
        directory_path="root/team-a",
        model="claude-sonnet-4-6",
        agent_harness="claude",
    )

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "msg-1",
                "type": "message",
                "model": "claude-sonnet-4-6",
                "content": [{"type": "text", "text": "secret completion text"}],
                "usage": {"input_tokens": 6, "output_tokens": 7},
            },
        )

    monkeypatch.setenv("ANTHROPIC_TEST_KEY", "anthropic-key")
    app = create_app(config, transport=httpx.MockTransport(handler))
    client = TestClient(app)

    response = client.post(
        "/session/session-anthropic/v1/messages",
        json={
            "model": "claude-sonnet-4-6",
            "messages": [{"role": "user", "content": "secret prompt text"}],
            "max_tokens": 64,
        },
    )

    assert response.status_code == 200
    event = ModelUsageStore(runtime_dir / "model-usage.db").get_event("msg-1")
    assert event is not None
    event_json = json.dumps(event.model_dump(mode="json"), sort_keys=True)
    assert event.provider == "anthropic"
    assert event.provider_endpoint_identifier == "messages"
    assert event.model == "claude-sonnet-4-6"
    assert event.input_tokens == 6
    assert event.output_tokens == 7
    assert event.total_tokens is None
    assert event.normalized_usage_status == "available"
    assert event.session_id == "session-anthropic"
    assert event.workflow_run_id == "workflow-anthropic"
    assert event.dispatch_id == "dispatch-anthropic"
    assert event.agent_harness == "claude"
    assert event.execution_backend == "container_worker_vm"
    assert event.directory_path == "root/team-a"
    assert event.raw_usage_metadata["request_metadata"]["path"] == "/v1/messages"
    assert "secret prompt text" not in event_json
    assert "secret completion text" not in event_json


def test_openai_relay_delivers_operator_steering_note_on_next_anthropic_message_call(
    tmp_path: Path, monkeypatch
) -> None:
    runtime_dir = tmp_path / "runtime"
    config = make_config(runtime_dir)
    ensure_directory(config)
    write_provider_overlay(
        config,
        {
            "model_provider_mode": "anthropic",
            "anthropic_api_base_url": "https://anthropic.example",
            "anthropic_api_key_env": "ANTHROPIC_TEST_KEY",
        },
    )
    write_session(config, session_id="session-anthropic")
    register_model_usage_correlation_context(
        runtime_dir=runtime_dir,
        session_id="session-anthropic",
        dispatch_id="dispatch-anthropic",
        workflow_run_id="workflow-anthropic",
        effect_id="effect-anthropic",
        execution_backend="container_worker_vm",
        directory_path="root/team-a",
        model="claude-sonnet-4-6",
        agent_harness="claude",
    )
    steering_store = operator_steering_note_store(runtime_dir)
    note = steering_store.create_note(
        session_id="session-anthropic",
        dispatch_id="dispatch-anthropic",
        message="Move from exploration to implementation.",
    )
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["payload"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(
            200,
            json={
                "id": "msg-steered",
                "type": "message",
                "model": "claude-sonnet-4-6",
                "content": [{"type": "text", "text": "ok"}],
                "usage": {"input_tokens": 6, "output_tokens": 7},
            },
        )

    monkeypatch.setenv("ANTHROPIC_TEST_KEY", "anthropic-key")
    app = create_app(config, transport=httpx.MockTransport(handler))
    client = TestClient(app)

    response = client.post(
        "/session/session-anthropic/v1/messages",
        json={
            "model": "claude-sonnet-4-6",
            "messages": [{"role": "user", "content": "secret prompt text"}],
            "max_tokens": 64,
        },
    )

    assert response.status_code == 200
    payload = captured["payload"]
    assert isinstance(payload, dict)
    assert isinstance(payload["messages"], list)
    steering_message = payload["messages"][-1]
    assert steering_message["role"] == "user"
    assert "Move from exploration to implementation." in steering_message["content"][0]["text"]
    delivered = steering_store.get_note(note.note_id)
    assert delivered is not None
    assert delivered.status == "delivered"
    assert delivered.provider == "anthropic"
    assert delivered.provider_endpoint == "messages"
    assert delivered.delivery_attempts == 1


def test_openai_relay_inserts_anthropic_operator_steering_before_trailing_system_messages(
    tmp_path: Path, monkeypatch
) -> None:
    runtime_dir = tmp_path / "runtime"
    config = make_config(runtime_dir)
    ensure_directory(config)
    write_provider_overlay(
        config,
        {
            "model_provider_mode": "anthropic",
            "anthropic_api_base_url": "https://anthropic.example",
            "anthropic_api_key_env": "ANTHROPIC_TEST_KEY",
        },
    )
    write_session(config, session_id="session-anthropic")
    register_model_usage_correlation_context(
        runtime_dir=runtime_dir,
        session_id="session-anthropic",
        dispatch_id="dispatch-anthropic",
        workflow_run_id="workflow-anthropic",
        effect_id="effect-anthropic",
        execution_backend="container_worker_vm",
        directory_path="root/team-a",
        model="claude-sonnet-4-6",
        agent_harness="claude",
    )
    steering_store = operator_steering_note_store(runtime_dir)
    note = steering_store.create_note(
        session_id="session-anthropic",
        dispatch_id="dispatch-anthropic",
        message="Do the smallest safe fix now.",
    )
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["payload"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(
            200,
            json={
                "id": "msg-steered",
                "type": "message",
                "model": "claude-sonnet-4-6",
                "content": [{"type": "text", "text": "ok"}],
                "usage": {"input_tokens": 6, "output_tokens": 7},
            },
        )

    monkeypatch.setenv("ANTHROPIC_TEST_KEY", "anthropic-key")
    app = create_app(config, transport=httpx.MockTransport(handler))
    client = TestClient(app)

    response = client.post(
        "/session/session-anthropic/v1/messages",
        json={
            "model": "claude-sonnet-4-6",
            "messages": [
                {"role": "user", "content": "secret prompt text"},
                {"role": "assistant", "content": "I will inspect it."},
                {"role": "system", "content": "Runtime reminder that must stay last."},
            ],
            "max_tokens": 64,
        },
    )

    assert response.status_code == 200
    payload = captured["payload"]
    assert isinstance(payload, dict)
    assert isinstance(payload["messages"], list)
    roles = [message["role"] for message in payload["messages"]]
    assert roles == ["user", "assistant", "user", "system"]
    steering_message = payload["messages"][-2]
    assert "Do the smallest safe fix now." in steering_message["content"][0]["text"]
    assert payload["messages"][-1]["content"] == "Runtime reminder that must stay last."
    delivered = steering_store.get_note(note.note_id)
    assert delivered is not None
    assert delivered.status == "delivered"
    assert delivered.provider == "anthropic"
    assert delivered.provider_endpoint == "messages"


def test_openai_relay_does_not_deliver_operator_steering_note_to_anthropic_count_tokens(
    tmp_path: Path, monkeypatch
) -> None:
    runtime_dir = tmp_path / "runtime"
    config = make_config(runtime_dir)
    ensure_directory(config)
    write_provider_overlay(
        config,
        {
            "model_provider_mode": "anthropic",
            "anthropic_api_base_url": "https://anthropic.example",
            "anthropic_api_key_env": "ANTHROPIC_TEST_KEY",
        },
    )
    write_session(config, session_id="session-anthropic")
    register_model_usage_correlation_context(
        runtime_dir=runtime_dir,
        session_id="session-anthropic",
        dispatch_id="dispatch-anthropic",
        directory_path="root/team-a",
        model="claude-sonnet-4-6",
        agent_harness="claude",
    )
    steering_store = operator_steering_note_store(runtime_dir)
    note = steering_store.create_note(
        session_id="session-anthropic",
        dispatch_id="dispatch-anthropic",
        message="This should wait for a real message call.",
    )
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["payload"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(200, json={"input_tokens": 11})

    monkeypatch.setenv("ANTHROPIC_TEST_KEY", "anthropic-key")
    app = create_app(config, transport=httpx.MockTransport(handler))
    client = TestClient(app)

    response = client.post(
        "/session/session-anthropic/v1/messages/count_tokens",
        json={
            "model": "claude-sonnet-4-6",
            "messages": [{"role": "user", "content": "count this"}],
        },
    )

    assert response.status_code == 200
    assert captured["payload"] == {
        "model": "claude-sonnet-4-6",
        "messages": [{"role": "user", "content": "count this"}],
    }
    pending = steering_store.get_note(note.note_id)
    assert pending is not None
    assert pending.status == "pending"
    assert pending.delivery_attempts == 0


def test_openai_relay_records_anthropic_streaming_usage(tmp_path: Path, monkeypatch) -> None:
    runtime_dir = tmp_path / "runtime"
    config = make_config(runtime_dir)
    ensure_directory(config)
    write_provider_overlay(
        config,
        {
            "model_provider_mode": "anthropic",
            "anthropic_api_base_url": "https://anthropic.example",
            "anthropic_api_key_env": "ANTHROPIC_TEST_KEY",
        },
    )
    write_session(config, session_id="session-anthropic")
    register_model_usage_correlation_context(
        runtime_dir=runtime_dir,
        session_id="session-anthropic",
        dispatch_id="dispatch-anthropic-stream",
        workflow_run_id="workflow-anthropic",
        effect_id="effect-anthropic",
        execution_backend="container_worker_vm",
        directory_path="root/team-a",
        model="claude-sonnet-4-6",
        agent_harness="claude",
    )

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream", "request-id": "req-anthropic-stream"},
            stream=_AnthropicUsageStream(),
        )

    monkeypatch.setenv("ANTHROPIC_TEST_KEY", "anthropic-key")
    app = create_app(config, transport=httpx.MockTransport(handler))
    client = TestClient(app)

    with client.stream(
        "POST",
        "/session/session-anthropic/v1/messages",
        json={
            "model": "claude-sonnet-4-6",
            "messages": [{"role": "user", "content": "secret prompt text"}],
            "max_tokens": 64,
            "stream": True,
        },
    ) as response:
        body = b"".join(response.iter_bytes())

    assert response.status_code == 200
    assert b"secret completion text" in body

    event = ModelUsageStore(runtime_dir / "model-usage.db").get_event("msg-stream")
    assert event is not None
    event_json = json.dumps(event.model_dump(mode="json"), sort_keys=True)
    assert event.provider == "anthropic"
    assert event.provider_endpoint_identifier == "messages"
    assert event.input_tokens == 7
    assert event.output_tokens == 8
    assert event.total_tokens is None
    assert event.request_id == "req-anthropic-stream"
    assert event.normalized_usage_status == "available"
    assert event.raw_usage_metadata["request_metadata"]["stream"] is True
    assert event.raw_usage_metadata["usage"]["cache_creation_input_tokens"] == 5
    assert event.raw_usage_metadata["usage"]["cache_read_input_tokens"] == 3
    assert event.raw_usage_metadata["usage"]["cache_creation"] == {
        "ephemeral_5m_input_tokens": 2,
        "ephemeral_1h_input_tokens": 3,
    }
    assert event.agent_harness == "claude"
    assert "secret prompt text" not in event_json
    assert "secret completion text" not in event_json


def test_openai_relay_records_streaming_final_usage_without_raw_text(
    tmp_path: Path, monkeypatch
) -> None:
    runtime_dir = tmp_path / "runtime"
    config = make_config(runtime_dir)
    ensure_directory(config)
    write_session(config, session_id="session-1")

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            stream=_UsageStream(),
        )

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    app = create_app(config, transport=httpx.MockTransport(handler))
    client = TestClient(app)

    with client.stream(
        "POST",
        "/v1/session/session-1/responses",
        headers={"accept": "text/event-stream"},
        json={"model": "gpt-5.2-codex", "input": "secret prompt text"},
    ) as response:
        body = b"".join(response.iter_bytes())

    assert response.status_code == 200
    assert b"secret completion text" in body

    event = ModelUsageStore(runtime_dir / "model-usage.db").get_event("resp-stream")
    assert event is not None
    event_json = json.dumps(event.model_dump(mode="json"), sort_keys=True)
    assert event.input_tokens == 7
    assert event.output_tokens == 8
    assert event.total_tokens == 15
    assert event.normalized_usage_status == "available"
    assert event.raw_usage_metadata["request_metadata"]["stream"] is True
    assert "provider_request_id" not in event.raw_usage_metadata["response_metadata"]
    assert event.response_body_sha256 is not None
    assert "secret prompt text" not in event_json
    assert "secret completion text" not in event_json


def test_openai_relay_records_websocket_final_usage_without_raw_text(
    tmp_path: Path, monkeypatch
) -> None:
    runtime_dir = tmp_path / "runtime"
    config = make_config(runtime_dir)
    ensure_directory(config)
    write_session(config, session_id="session-1")
    context_started_at = datetime.now(UTC)
    register_model_usage_correlation_context(
        runtime_dir=runtime_dir,
        session_id="session-1",
        dispatch_id="dispatch-websocket",
        workflow_run_id="workflow-websocket",
        effect_id="effect-websocket",
        execution_backend="container_worker_vm",
        directory_path="root/team-a",
        model="gpt-5.2-codex",
        trace_id="trace-websocket",
        started_at=context_started_at,
        expires_at=context_started_at + timedelta(hours=1),
    )
    upstream = _FakeUpstreamWebSocket()
    relay = OpenAIRelay(config)

    @asynccontextmanager
    async def fake_connect(url: str, *, headers: dict[str, str], timeout_seconds: float):
        del url, headers, timeout_seconds
        yield upstream

    relay._connect_upstream_websocket = fake_connect  # type: ignore[method-assign]
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    client = TestClient(relay.create_app())
    request_message = json.dumps(
        {
            "type": "response.create",
            "response": {"instructions": "secret prompt text"},
        }
    )
    delta_message = json.dumps(
        {
            "type": "response.output_text.delta",
            "delta": "secret completion text",
        }
    )
    final_message = json.dumps(
        {
            "type": "response.completed",
            "response": {
                "id": "resp-websocket",
                "request_id": "req-websocket",
                "type": "response",
                "model": "gpt-5.2-codex",
                "output_text": "secret completion text",
                "usage": {
                    "input_tokens": 13,
                    "output_tokens": 21,
                    "total_tokens": 34,
                },
            },
        }
    )

    with client.websocket_connect("/v1/session/session-1/responses") as websocket:
        websocket.send_text(request_message)
        wait_for_upstream_messages(upstream, [request_message])
        asyncio.run(upstream.push(delta_message))
        assert websocket.receive_text() == delta_message
        asyncio.run(upstream.push(final_message))
        assert websocket.receive_text() == final_message

    event = ModelUsageStore(runtime_dir / "model-usage.db").get_event("resp-websocket")
    assert event is not None
    event_json = json.dumps(event.model_dump(mode="json"), sort_keys=True)
    assert event.provider == "openai"
    assert event.provider_endpoint_identifier == "responses"
    assert event.normalized_usage_status == "available"
    assert event.input_tokens == 13
    assert event.output_tokens == 21
    assert event.total_tokens == 34
    assert event.session_id == "session-1"
    assert event.workflow_run_id == "workflow-websocket"
    assert event.effect_id == "effect-websocket"
    assert event.dispatch_id == "dispatch-websocket"
    assert event.execution_backend == "container_worker_vm"
    assert event.directory_path == "root/team-a"
    assert event.trace_id == "trace-websocket"
    assert event.request_id == "req-websocket"
    assert event.request_body_sha256 is None
    assert event.response_body_sha256 is not None
    assert event.raw_usage_metadata["request_metadata"] == {
        "method": "WEBSOCKET",
        "path": "/v1/responses",
        "websocket": True,
    }
    assert event.raw_usage_metadata["response_metadata"]["websocket"] is True
    assert event.raw_usage_metadata["response_metadata"]["event_type"] == "response.completed"
    assert "secret prompt text" not in event_json
    assert "secret completion text" not in event_json


def test_openai_relay_records_websocket_usage_unavailable(
    tmp_path: Path, monkeypatch
) -> None:
    runtime_dir = tmp_path / "runtime"
    config = make_config(runtime_dir)
    ensure_directory(config)
    write_session(config, session_id="session-1")
    register_model_usage_correlation_context(
        runtime_dir=runtime_dir,
        session_id="session-1",
        dispatch_id="dispatch-websocket-empty",
        workflow_run_id="workflow-websocket",
        effect_id="effect-websocket",
        execution_backend="container_worker_vm",
        directory_path="root/team-a",
        model="gpt-5.2-codex",
    )
    upstream = _FakeUpstreamWebSocket()
    relay = OpenAIRelay(config)

    @asynccontextmanager
    async def fake_connect(url: str, *, headers: dict[str, str], timeout_seconds: float):
        del url, headers, timeout_seconds
        yield upstream

    relay._connect_upstream_websocket = fake_connect  # type: ignore[method-assign]
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    client = TestClient(relay.create_app())
    final_message = json.dumps(
        {
            "type": "response.done",
            "response": {
                "id": "resp-websocket-empty",
                "type": "response",
                "model": "gpt-5.2-codex",
                "output_text": "secret completion text",
            },
        }
    )

    with client.websocket_connect("/v1/session/session-1/responses") as websocket:
        asyncio.run(upstream.push(final_message))
        assert websocket.receive_text() == final_message

    event = ModelUsageStore(runtime_dir / "model-usage.db").get_event(
        "resp-websocket-empty"
    )
    assert event is not None
    event_json = json.dumps(event.model_dump(mode="json"), sort_keys=True)
    assert event.normalized_usage_status == "usage_unavailable"
    assert event.input_tokens is None
    assert event.output_tokens is None
    assert event.total_tokens is None
    assert event.raw_usage_metadata["response_metadata"]["event_type"] == "response.done"
    assert "provider_request_id" not in event.raw_usage_metadata["response_metadata"]
    assert "secret completion text" not in event_json


def test_openai_relay_websocket_duplicate_final_events_upsert(
    tmp_path: Path, monkeypatch
) -> None:
    runtime_dir = tmp_path / "runtime"
    config = make_config(runtime_dir)
    ensure_directory(config)
    write_session(config, session_id="session-1")
    register_model_usage_correlation_context(
        runtime_dir=runtime_dir,
        session_id="session-1",
        dispatch_id="dispatch-websocket-duplicate",
        workflow_run_id="workflow-websocket",
        effect_id="effect-websocket",
        execution_backend="container_worker_vm",
        directory_path="root/team-a",
        model="gpt-5.2-codex",
    )
    upstream = _FakeUpstreamWebSocket()
    relay = OpenAIRelay(config)

    @asynccontextmanager
    async def fake_connect(url: str, *, headers: dict[str, str], timeout_seconds: float):
        del url, headers, timeout_seconds
        yield upstream

    relay._connect_upstream_websocket = fake_connect  # type: ignore[method-assign]
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    client = TestClient(relay.create_app())
    final_message = json.dumps(
        {
            "type": "response.completed",
            "response": {
                "id": "resp-websocket-duplicate",
                "type": "response",
                "model": "gpt-5.2-codex",
                "usage": {"input_tokens": 1, "output_tokens": 2, "total_tokens": 3},
            },
        }
    )

    with client.websocket_connect("/v1/session/session-1/responses") as websocket:
        asyncio.run(upstream.push(final_message))
        assert websocket.receive_text() == final_message
        asyncio.run(upstream.push(final_message))
        assert websocket.receive_text() == final_message

    events = ModelUsageStore(runtime_dir / "model-usage.db").list_events(
        session_id="session-1"
    )
    assert [event.event_id for event in events] == ["resp-websocket-duplicate"]


def test_openai_relay_correlates_container_context_without_backend_filter(
    tmp_path: Path, monkeypatch
) -> None:
    runtime_dir = tmp_path / "runtime"
    config = make_config(runtime_dir)
    ensure_directory(config)
    write_session(config, session_id="session-1")
    register_model_usage_correlation_context(
        runtime_dir=runtime_dir,
        session_id="session-1",
        dispatch_id="dispatch-container",
        workflow_run_id="workflow-container",
        effect_id="effect-container",
        execution_backend="container_worker_vm",
        directory_path="root/team-a",
        model="gpt-5.2-codex",
    )

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "resp-container",
                "type": "response",
                "model": "gpt-5.2-codex",
                "usage": {
                    "input_tokens": 1,
                    "output_tokens": 2,
                    "total_tokens": 3,
                },
            },
        )

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    app = create_app(config, transport=httpx.MockTransport(handler))
    client = TestClient(app)

    response = client.post(
        "/v1/session/session-1/responses",
        json={"model": "gpt-5.2-codex", "input": "continue"},
    )

    assert response.status_code == 200

    event = ModelUsageStore(runtime_dir / "model-usage.db").get_event("resp-container")
    assert event is not None
    assert event.dispatch_id == "dispatch-container"
    assert event.workflow_run_id == "workflow-container"
    assert event.execution_backend == "container_worker_vm"
