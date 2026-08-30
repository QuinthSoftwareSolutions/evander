from __future__ import annotations

import asyncio
import base64
from datetime import UTC, datetime, timedelta

from evander.config import GatewayConfig
from evander.models import SessionManifest
from evander.relays.http_target_browser_proxy import HttpTargetBrowserProxy
from evander.session_auth import session_mcp_auth_metadata


class _Resolver:
    def __init__(self, session: SessionManifest) -> None:
        self.session = session

    def get_session_manifest(self, session_id: str) -> SessionManifest:
        if session_id != self.session.session_id:
            raise ValueError("unknown session")
        return self.session


def _session(*base_urls: str) -> SessionManifest:
    return SessionManifest(
        session_id="session-browser",
        allowed_tools=[],
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        metadata={
            "mcp_session_auth": session_mcp_auth_metadata("correct-token"),
            "http_targets": {
                "targets": {
                    f"target-{index}": {"base_url": base_url}
                    for index, base_url in enumerate(base_urls)
                }
            },
        },
    )


def _auth(token: str = "correct-token") -> str:
    encoded = base64.b64encode(f"session-browser:{token}".encode()).decode()
    return f"Basic {encoded}"


def test_proxy_authenticates_session_and_authorizes_exact_origins() -> None:
    resolver = _Resolver(_session("https://Example.Test/app", "http://api.test:8080/root"))
    proxy = HttpTargetBrowserProxy(GatewayConfig(), provider_config_resolver=resolver)

    session = proxy._authenticate(_auth())
    proxy._authorize_origin(session, scheme="https", host="example.test", port=443)
    proxy._authorize_origin(session, scheme="http", host="API.TEST", port=8080)

    try:
        proxy._authorize_origin(session, scheme="https", host="example.test", port=444)
    except ValueError as exc:
        assert "not configured" in str(exc)
    else:
        raise AssertionError("unconfigured origin was accepted")


def test_proxy_rejects_invalid_session_credentials() -> None:
    proxy = HttpTargetBrowserProxy(
        GatewayConfig(), provider_config_resolver=_Resolver(_session("https://example.test"))
    )

    for credentials in (None, "Bearer token", _auth("wrong-token")):
        try:
            proxy._authenticate(credentials)
        except ValueError as exc:
            assert "authentication required" in str(exc)
        else:
            raise AssertionError("invalid proxy credentials were accepted")

    expired = _session("https://example.test").model_copy(
        update={"expires_at": datetime.now(UTC) - timedelta(seconds=1)}
    )
    expired_proxy = HttpTargetBrowserProxy(
        GatewayConfig(), provider_config_resolver=_Resolver(expired)
    )
    try:
        expired_proxy._authenticate(_auth())
    except ValueError as exc:
        assert "authentication required" in str(exc)
    else:
        raise AssertionError("expired session credentials were accepted")


def test_proxy_uses_live_session_targets_for_each_connection() -> None:
    resolver = _Resolver(_session("https://first.test"))
    proxy = HttpTargetBrowserProxy(GatewayConfig(), provider_config_resolver=resolver)
    first = proxy._authenticate(_auth())
    proxy._authorize_origin(first, scheme="https", host="first.test", port=443)

    resolver.session = _session("https://second.test:9443")
    second = proxy._authenticate(_auth())
    proxy._authorize_origin(second, scheme="https", host="second.test", port=9443)


def test_connect_tunnel_relays_bytes_and_denies_unlisted_authority() -> None:
    async def scenario() -> None:
        async def echo(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            data = await reader.read(16)
            writer.write(data)
            await writer.drain()
            writer.close()

        echo_server = await asyncio.start_server(echo, "127.0.0.1", 0)
        echo_port = echo_server.sockets[0].getsockname()[1]
        config = GatewayConfig(
            http_target_browser_proxy_idle_timeout_seconds=2,
            http_target_browser_proxy_connect_timeout_seconds=2,
        )
        proxy = HttpTargetBrowserProxy(
            config,
            provider_config_resolver=_Resolver(_session(f"https://127.0.0.1:{echo_port}")),
        )
        proxy_server = await asyncio.start_server(proxy.handle_client, "127.0.0.1", 0)
        proxy_port = proxy_server.sockets[0].getsockname()[1]
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", proxy_port)
            writer.write(
                (
                    f"CONNECT 127.0.0.1:{echo_port} HTTP/1.1\r\n"
                    f"Proxy-Authorization: {_auth()}\r\n\r\n"
                ).encode()
            )
            await writer.drain()
            assert await reader.readuntil(b"\r\n\r\n") == (
                b"HTTP/1.1 200 Connection Established\r\n\r\n"
            )
            writer.write(b"proxy works")
            await writer.drain()
            assert await reader.readexactly(len(b"proxy works")) == b"proxy works"
            writer.close()
            await writer.wait_closed()

            reader, writer = await asyncio.open_connection("127.0.0.1", proxy_port)
            writer.write(
                (
                    "CONNECT unlisted.test:443 HTTP/1.1\r\n"
                    f"Proxy-Authorization: {_auth()}\r\n\r\n"
                ).encode()
            )
            await writer.drain()
            response = await reader.read()
            assert response.startswith(b"HTTP/1.1 403 Forbidden")
            writer.close()
            await writer.wait_closed()
        finally:
            proxy_server.close()
            echo_server.close()
            await proxy_server.wait_closed()
            await echo_server.wait_closed()

    asyncio.run(scenario())


def test_http_proxy_forwards_absolute_request_without_proxy_credentials() -> None:
    async def scenario() -> None:
        received = b""

        async def upstream(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            nonlocal received
            received = await reader.readuntil(b"\r\n\r\n")
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Length: 7\r\nConnection: close\r\n\r\nforward"
            )
            await writer.drain()
            writer.close()

        upstream_server = await asyncio.start_server(upstream, "127.0.0.1", 0)
        upstream_port = upstream_server.sockets[0].getsockname()[1]
        proxy = HttpTargetBrowserProxy(
            GatewayConfig(http_target_browser_proxy_idle_timeout_seconds=2),
            provider_config_resolver=_Resolver(
                _session(f"http://127.0.0.1:{upstream_port}/configured/path")
            ),
        )
        proxy_server = await asyncio.start_server(proxy.handle_client, "127.0.0.1", 0)
        proxy_port = proxy_server.sockets[0].getsockname()[1]
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", proxy_port)
            writer.write(
                (
                    f"GET http://127.0.0.1:{upstream_port}/asset.js HTTP/1.1\r\n"
                    f"Host: 127.0.0.1:{upstream_port}\r\n"
                    f"Proxy-Authorization: {_auth()}\r\n\r\n"
                ).encode()
            )
            await writer.drain()
            response = await reader.read()
            assert response.endswith(b"forward")
            assert received.startswith(b"GET /asset.js HTTP/1.1\r\n")
            assert b"proxy-authorization" not in received.lower()
            writer.close()
            await writer.wait_closed()
        finally:
            proxy_server.close()
            upstream_server.close()
            await proxy_server.wait_closed()
            await upstream_server.wait_closed()

    asyncio.run(scenario())
