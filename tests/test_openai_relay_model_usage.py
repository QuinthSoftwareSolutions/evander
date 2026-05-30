from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
from starlette.testclient import TestClient

from agent_sandbox.config import GatewayConfig
from agent_sandbox.directories import DirectoryService, DirectoryStore
from agent_sandbox.model_usage.runtime import register_model_usage_correlation_context
from agent_sandbox.model_usage.store import ModelUsageStore
from agent_sandbox.models import SessionManifest
from agent_sandbox.relays.openai import create_app
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
    assert event.response_body_sha256 is not None
    assert "secret prompt text" not in event_json
    assert "secret completion text" not in event_json


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
