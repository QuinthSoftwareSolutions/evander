import gzip
import os
from pathlib import Path

import httpx
from starlette.testclient import TestClient

from agent_sandbox.config import GatewayConfig
from agent_sandbox.openai_relay import create_app


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
