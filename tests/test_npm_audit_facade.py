from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from starlette.testclient import TestClient

from agent_sandbox.config import GatewayConfig
from agent_sandbox.directory_config import DirectoryConfigStore
from agent_sandbox.directories import DirectoryStore
from agent_sandbox.relays.npm_audit_facade import create_app


class _ChunkStream(httpx.AsyncByteStream):
    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = chunks
        self.closed = False

    async def __aiter__(self):
        for chunk in self.chunks:
            yield chunk

    async def aclose(self) -> None:
        self.closed = True


def make_config(tmp_path: Path) -> GatewayConfig:
    return GatewayConfig(
        runtime_dir=tmp_path,
        audit_log_path=tmp_path / "audit.jsonl",
        session_store_path=tmp_path / "sessions.json",
        approval_store_path=tmp_path / "approvals.json",
        draft_store_path=tmp_path / "drafts.json",
        email_store_path=tmp_path / "emails.json",
        discord_command_store_path=tmp_path / "discord.json",
        gateway_url="http://192.168.250.1:8000/mcp",
        npm_audit_facade_audit_registry_url="https://registry.npmjs.org",
    )


def write_provider_overlay(config: GatewayConfig, payload: dict[str, object]) -> None:
    directory_store = DirectoryStore(config.directories_store_path)
    directory_store.ensure_root()
    DirectoryConfigStore(directory_store, runtime_dir=config.runtime_dir).write(
        "root",
        domain="providers",
        payload=payload,
    )

def test_health_reports_audit_and_package_upstreams(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    client = TestClient(create_app(config))

    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "audit_upstream": "https://registry.npmjs.org",
        "package_upstream": "http://192.168.250.1:8081/repository/npm-all/",
    }


@pytest.mark.parametrize(
    "path",
    [
        "/-/npm/v1/security/advisories/bulk",
        "/-/npm/v1/security/audits/quick",
    ],
)
def test_audit_posts_target_public_npm_and_strip_authorization(
    tmp_path: Path, path: str
) -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers.get("authorization")
        captured["accept-encoding"] = request.headers.get("accept-encoding")
        captured["body"] = request.content
        return httpx.Response(
            201,
            headers={"content-type": "application/json", "x-upstream": "audit"},
            json={"ok": True},
        )

    config = make_config(tmp_path)
    client = TestClient(create_app(config, transport=httpx.MockTransport(handler)))

    response = client.post(
        path,
        headers={
            "authorization": "Bearer guest-token",
            "connection": "keep-alive",
            "accept-encoding": "gzip",
        },
        json={"name": "example"},
    )

    assert response.status_code == 201
    assert response.json() == {"ok": True}
    assert response.headers["x-upstream"] == "audit"
    assert "content-encoding" not in response.headers
    assert captured["method"] == "POST"
    assert captured["url"] == f"https://registry.npmjs.org{path}"
    assert captured["authorization"] is None
    assert captured["accept-encoding"] == "identity"
    assert captured["body"] == b'{"name":"example"}'


def test_package_requests_use_default_nexus_url_and_preserve_raw_path_and_query(
    tmp_path: Path,
) -> None:
    captured: dict[str, object] = {}
    upstream_stream = _ChunkStream([b"chunk-1", b"chunk-2"])

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["raw_path"] = request.url.raw_path.decode("ascii")
        captured["authorization"] = request.headers.get("authorization")
        captured["accept-encoding"] = request.headers.get("accept-encoding")
        return httpx.Response(
            200,
            headers={"content-type": "application/octet-stream", "x-upstream": "package"},
            stream=upstream_stream,
        )

    config = make_config(tmp_path)
    client = TestClient(create_app(config, transport=httpx.MockTransport(handler)))

    with client.stream("GET", "/@scope%2fpkg/-/pkg.tgz?download=1&tag=latest") as response:
        body = b"".join(response.iter_bytes())

    assert response.status_code == 200
    assert response.headers["x-upstream"] == "package"
    assert body == b"chunk-1chunk-2"
    assert upstream_stream.closed is True
    assert captured["method"] == "GET"
    assert (
        captured["raw_path"]
        == "/repository/npm-all/@scope%2fpkg/-/pkg.tgz?download=1&tag=latest"
    )
    assert captured["authorization"] is None
    assert captured["accept-encoding"] == "identity"


def test_package_requests_use_root_overlay_for_custom_registry(tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["url"] = str(request.url)
        return httpx.Response(200, headers={"content-type": "application/json"}, json={"ok": True})

    config = make_config(tmp_path)
    write_provider_overlay(
        config,
        {
            "npm_audit_facade_package_registry_url": "https://nexus.test/repository/npm-all/",
        },
    )
    client = TestClient(create_app(config, transport=httpx.MockTransport(handler)))

    response = client.head("/left-pad?download=1")

    assert response.status_code == 200
    assert captured["method"] == "HEAD"
    assert captured["url"] == "https://nexus.test/repository/npm-all/left-pad?download=1"
