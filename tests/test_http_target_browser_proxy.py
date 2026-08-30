from __future__ import annotations

import asyncio
import base64
import ssl
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


def _tls_client_hello(server_hostname: str | None) -> bytes:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    incoming = ssl.MemoryBIO()
    outgoing = ssl.MemoryBIO()
    tls = context.wrap_bio(
        incoming,
        outgoing,
        server_side=False,
        server_hostname=server_hostname,
    )
    try:
        tls.do_handshake()
    except ssl.SSLWantReadError:
        pass
    return outgoing.read()


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


def test_proxy_canonicalizes_unicode_and_idna_target_hosts() -> None:
    session = _session("https://bücher.example/app")
    proxy = HttpTargetBrowserProxy(GatewayConfig(), provider_config_resolver=_Resolver(session))

    proxy._authorize_origin(
        session,
        scheme="https",
        host="xn--bcher-kva.example",
        port=443,
    )
    proxy._validate_tunnel_server_name(
        "xn--bcher-kva.example",
        "xn--bcher-kva.example",
    )


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


def test_connect_tunnel_binds_tls_sni_and_denies_unlisted_authority(monkeypatch) -> None:
    async def scenario() -> None:
        upstream_payloads: asyncio.Queue[bytes] = asyncio.Queue()

        async def capture(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            await upstream_payloads.put(await reader.read(64 * 1024))
            writer.close()

        upstream_server = await asyncio.start_server(capture, "127.0.0.1", 0)
        upstream_port = upstream_server.sockets[0].getsockname()[1]
        original_open_connection = asyncio.open_connection

        async def open_connection(host, port, *args, **kwargs):
            if host == "allowed.test":
                host = "127.0.0.1"
            return await original_open_connection(host, port, *args, **kwargs)

        monkeypatch.setattr(asyncio, "open_connection", open_connection)
        config = GatewayConfig(
            http_target_browser_proxy_header_timeout_seconds=2,
            http_target_browser_proxy_idle_timeout_seconds=2,
            http_target_browser_proxy_connect_timeout_seconds=2,
        )
        proxy = HttpTargetBrowserProxy(
            config,
            provider_config_resolver=_Resolver(_session(f"https://allowed.test:{upstream_port}")),
        )
        proxy_server = await asyncio.start_server(proxy.handle_client, "127.0.0.1", 0)
        proxy_port = proxy_server.sockets[0].getsockname()[1]
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", proxy_port)
            writer.write(
                (
                    f"CONNECT allowed.test:{upstream_port} HTTP/1.1\r\n"
                    f"Proxy-Authorization: {_auth()}\r\n\r\n"
                ).encode()
            )
            await writer.drain()
            assert await reader.readuntil(b"\r\n\r\n") == (
                b"HTTP/1.1 200 Connection Established\r\n\r\n"
            )
            allowed_client_hello = _tls_client_hello("allowed.test")
            writer.write(allowed_client_hello)
            await writer.drain()
            assert await asyncio.wait_for(upstream_payloads.get(), timeout=1) == (
                allowed_client_hello
            )
            writer.close()
            await writer.wait_closed()

            reader, writer = await asyncio.open_connection("127.0.0.1", proxy_port)
            writer.write(
                (
                    f"CONNECT allowed.test:{upstream_port} HTTP/1.1\r\n"
                    f"Proxy-Authorization: {_auth()}\r\n\r\n"
                ).encode()
            )
            await writer.drain()
            assert await reader.readuntil(b"\r\n\r\n") == (
                b"HTTP/1.1 200 Connection Established\r\n\r\n"
            )
            writer.write(_tls_client_hello("unlisted.test"))
            await writer.drain()
            assert await asyncio.wait_for(reader.read(), timeout=1) == b""
            assert await asyncio.wait_for(upstream_payloads.get(), timeout=1) == b""
            writer.close()
            await writer.wait_closed()

            reader, writer = await asyncio.open_connection("127.0.0.1", proxy_port)
            writer.write(
                (
                    f"CONNECT unlisted.test:443 HTTP/1.1\r\nProxy-Authorization: {_auth()}\r\n\r\n"
                ).encode()
            )
            await writer.drain()
            response = await reader.read()
            assert response.startswith(b"HTTP/1.1 403 Forbidden")
            writer.close()
            await writer.wait_closed()
        finally:
            proxy_server.close()
            upstream_server.close()
            await proxy_server.wait_closed()
            await upstream_server.wait_closed()

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
                    "Host: unlisted.test\r\n"
                    f"Proxy-Authorization: {_auth()}\r\n\r\n"
                ).encode()
            )
            await writer.drain()
            response = await reader.read()
            assert response.endswith(b"forward")
            assert received.startswith(b"GET /asset.js HTTP/1.1\r\n")
            assert b"proxy-authorization" not in received.lower()
            assert f"\r\nhost: 127.0.0.1:{upstream_port}\r\n".encode() in received.lower()
            assert b"unlisted.test" not in received
            writer.close()
            await writer.wait_closed()
        finally:
            proxy_server.close()
            upstream_server.close()
            await proxy_server.wait_closed()
            await upstream_server.wait_closed()

    asyncio.run(scenario())


def test_proxy_rejects_excess_connections_without_queuing_and_expires_headers() -> None:
    async def scenario() -> None:
        proxy = HttpTargetBrowserProxy(
            GatewayConfig(
                http_target_browser_proxy_header_timeout_seconds=0.1,
                http_target_browser_proxy_max_connections=1,
            ),
            provider_config_resolver=_Resolver(_session("https://allowed.test")),
        )
        proxy_server = await asyncio.start_server(proxy.handle_client, "127.0.0.1", 0)
        proxy_port = proxy_server.sockets[0].getsockname()[1]
        first_writer = None
        second_writer = None
        third_writer = None
        try:
            first_reader, first_writer = await asyncio.open_connection("127.0.0.1", proxy_port)
            for _ in range(20):
                if proxy._connection_slots.empty():
                    break
                await asyncio.sleep(0)
            assert proxy._connection_slots.empty()

            second_reader, second_writer = await asyncio.open_connection("127.0.0.1", proxy_port)
            second_response = await asyncio.wait_for(second_reader.read(), timeout=1)
            assert second_response.startswith(b"HTTP/1.1 503 Service Unavailable")

            assert await asyncio.wait_for(first_reader.read(), timeout=1) == b""
            assert proxy._connection_slots.qsize() == 1

            third_reader, third_writer = await asyncio.open_connection("127.0.0.1", proxy_port)
            third_writer.write(b"GET /healthz HTTP/1.1\r\nHost: proxy\r\n\r\n")
            await third_writer.drain()
            third_response = await asyncio.wait_for(third_reader.read(), timeout=1)
            assert third_response.startswith(b"HTTP/1.1 200 OK")
        finally:
            for writer in (first_writer, second_writer, third_writer):
                if writer is not None:
                    writer.close()
                    await writer.wait_closed()
            proxy_server.close()
            await proxy_server.wait_closed()

    asyncio.run(scenario())
