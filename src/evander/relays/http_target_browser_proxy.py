from __future__ import annotations

import argparse
import asyncio
import base64
import binascii
import logging
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

from evander.bootstrap import load_runtime_gateway_config, resolve_runtime_dir
from evander.config import GatewayConfig
from evander.models import SessionManifest
from evander.provider_config import ProviderConfigResolver
from evander.session_auth import verify_mcp_session_proof_token

LOGGER = logging.getLogger(__name__)
MAX_HEADER_BYTES = 64 * 1024
BUFFER_SIZE = 64 * 1024


class ProxyRequestError(ValueError):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


class HttpTargetBrowserProxy:
    """Authenticated forward proxy restricted to a session's HTTP target origins."""

    def __init__(
        self,
        config: GatewayConfig,
        *,
        provider_config_resolver: ProviderConfigResolver | None = None,
    ) -> None:
        self.config = config
        self.provider_config_resolver = provider_config_resolver or ProviderConfigResolver(config)
        self._connections = asyncio.Semaphore(config.http_target_browser_proxy_max_connections)

    async def handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        peer = writer.get_extra_info("peername")
        try:
            async with self._connections:
                await self._handle_request(reader, writer)
        except ProxyRequestError as exc:
            await self._send_error(writer, exc.status, str(exc))
        except (asyncio.IncompleteReadError, ConnectionError, TimeoutError):
            LOGGER.info("browser proxy connection ended peer=%s", peer)
        except Exception:
            LOGGER.exception("browser proxy request failed peer=%s", peer)
            await self._send_error(writer, 502, "browser proxy request failed")
        finally:
            writer.close()
            with suppress(Exception):
                await writer.wait_closed()

    async def _handle_request(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        raw_headers = await asyncio.wait_for(
            reader.readuntil(b"\r\n\r\n"),
            timeout=self.config.http_target_browser_proxy_idle_timeout_seconds,
        )
        if len(raw_headers) > MAX_HEADER_BYTES:
            raise ProxyRequestError(431, "proxy request headers are too large")
        request_line, headers = self._parse_request_headers(raw_headers)
        try:
            method, request_target, version = request_line.split(" ", 2)
        except ValueError as exc:
            raise ProxyRequestError(400, "invalid proxy request line") from exc

        if method == "GET" and request_target == "/healthz":
            await self._send_response(
                writer,
                200,
                b'{"status":"ok","proxy_mode":"session_scoped_http_targets"}\n',
                content_type="application/json",
            )
            return

        session = self._authenticate(headers.get("proxy-authorization"))
        if method.upper() == "CONNECT":
            host, port = self._parse_authority(request_target)
            self._authorize_origin(session, scheme="https", host=host, port=port)
            LOGGER.info(
                "browser proxy CONNECT allowed session=%s authority=%s:%s",
                session.session_id,
                host,
                port,
            )
            await self._connect_tunnel(reader, writer, host=host, port=port)
            return

        parts = urlsplit(request_target)
        scheme = parts.scheme.lower()
        if scheme not in {"http", "ws"} or not parts.hostname:
            raise ProxyRequestError(400, "proxy requests must use an absolute http URL")
        port = parts.port or 80
        host = parts.hostname
        self._authorize_origin(session, scheme="http", host=host, port=port)
        LOGGER.info(
            "browser proxy HTTP allowed session=%s authority=%s:%s",
            session.session_id,
            host,
            port,
        )
        await self._forward_http(
            reader,
            writer,
            method=method,
            version=version,
            parts=parts,
            headers=headers,
            host=host,
            port=port,
        )

    def _authenticate(self, authorization: str | None) -> SessionManifest:
        session_id, token = self._basic_credentials(authorization)
        try:
            session = self.provider_config_resolver.get_session_manifest(session_id)
        except ValueError as exc:
            raise ProxyRequestError(407, "proxy authentication required") from exc
        if session.expires_at <= datetime.now(UTC):
            raise ProxyRequestError(407, "proxy authentication required")
        if not verify_mcp_session_proof_token(session, token):
            raise ProxyRequestError(407, "proxy authentication required")
        return session

    @staticmethod
    def _basic_credentials(authorization: str | None) -> tuple[str, str]:
        scheme, _, encoded = str(authorization or "").partition(" ")
        if scheme.casefold() != "basic" or not encoded:
            raise ProxyRequestError(407, "proxy authentication required")
        try:
            decoded = base64.b64decode(encoded, validate=True).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError) as exc:
            raise ProxyRequestError(407, "proxy authentication required") from exc
        session_id, separator, token = decoded.partition(":")
        if not separator or not session_id.strip() or not token:
            raise ProxyRequestError(407, "proxy authentication required")
        return session_id.strip(), token

    @staticmethod
    def _target_origins(session: SessionManifest) -> set[tuple[str, str, int]]:
        origins: set[tuple[str, str, int]] = set()
        for target in session.http_targets_metadata().targets.values():
            parts = urlsplit(target.base_url)
            if not parts.hostname:
                continue
            scheme = parts.scheme.lower()
            port = parts.port or (443 if scheme == "https" else 80)
            origins.add((scheme, parts.hostname.casefold(), port))
        return origins

    @classmethod
    def _authorize_origin(
        cls,
        session: SessionManifest,
        *,
        scheme: str,
        host: str,
        port: int,
    ) -> None:
        origin = (scheme.casefold(), host.casefold().rstrip("."), port)
        normalized_origins = {
            (allowed_scheme, allowed_host.rstrip("."), allowed_port)
            for allowed_scheme, allowed_host, allowed_port in cls._target_origins(session)
        }
        if origin not in normalized_origins:
            LOGGER.warning(
                "browser proxy origin denied session=%s authority=%s:%s",
                session.session_id,
                host,
                port,
            )
            raise ProxyRequestError(403, "target origin is not configured for this session")

    async def _connect_tunnel(
        self,
        client_reader: asyncio.StreamReader,
        client_writer: asyncio.StreamWriter,
        *,
        host: str,
        port: int,
    ) -> None:
        upstream_reader, upstream_writer = await asyncio.wait_for(
            asyncio.open_connection(host, port),
            timeout=self.config.http_target_browser_proxy_connect_timeout_seconds,
        )
        client_writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        await client_writer.drain()
        await self._relay_bidirectional(
            client_reader,
            client_writer,
            upstream_reader,
            upstream_writer,
        )

    async def _forward_http(
        self,
        client_reader: asyncio.StreamReader,
        client_writer: asyncio.StreamWriter,
        *,
        method: str,
        version: str,
        parts,
        headers: dict[str, str],
        host: str,
        port: int,
    ) -> None:
        upstream_reader, upstream_writer = await asyncio.wait_for(
            asyncio.open_connection(host, port),
            timeout=self.config.http_target_browser_proxy_connect_timeout_seconds,
        )
        is_upgrade = headers.get("upgrade", "").casefold() == "websocket"
        filtered_headers = {
            key: value
            for key, value in headers.items()
            if key not in {"proxy-authorization", "proxy-connection"}
        }
        if not is_upgrade:
            filtered_headers["connection"] = "close"
        path = parts.path or "/"
        if parts.query:
            path = f"{path}?{parts.query}"
        upstream_writer.write(f"{method} {path} {version}\r\n".encode("ascii"))
        for key, value in filtered_headers.items():
            upstream_writer.write(f"{key}: {value}\r\n".encode("latin-1"))
        upstream_writer.write(b"\r\n")
        await upstream_writer.drain()
        await self._relay_bidirectional(
            client_reader,
            client_writer,
            upstream_reader,
            upstream_writer,
        )

    async def _relay_bidirectional(
        self,
        client_reader: asyncio.StreamReader,
        client_writer: asyncio.StreamWriter,
        upstream_reader: asyncio.StreamReader,
        upstream_writer: asyncio.StreamWriter,
    ) -> None:
        async def pump(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            while True:
                data = await asyncio.wait_for(
                    reader.read(BUFFER_SIZE),
                    timeout=self.config.http_target_browser_proxy_idle_timeout_seconds,
                )
                if not data:
                    break
                writer.write(data)
                await writer.drain()

        tasks = {
            asyncio.create_task(pump(client_reader, upstream_writer)),
            asyncio.create_task(pump(upstream_reader, client_writer)),
        }
        try:
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                with suppress(ConnectionError, TimeoutError):
                    task.result()
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
        finally:
            upstream_writer.close()
            with suppress(Exception):
                await upstream_writer.wait_closed()

    @staticmethod
    def _parse_request_headers(raw: bytes) -> tuple[str, dict[str, str]]:
        try:
            lines = raw.decode("latin-1").split("\r\n")
        except UnicodeDecodeError as exc:
            raise ProxyRequestError(400, "invalid proxy request headers") from exc
        headers: dict[str, str] = {}
        for line in lines[1:]:
            if not line:
                break
            name, separator, value = line.partition(":")
            if not separator:
                raise ProxyRequestError(400, "invalid proxy request headers")
            headers[name.strip().casefold()] = value.strip()
        return lines[0], headers

    @staticmethod
    def _parse_authority(authority: str) -> tuple[str, int]:
        parts = urlsplit(f"//{authority}")
        if not parts.hostname or parts.port is None or parts.path or parts.query or parts.fragment:
            raise ProxyRequestError(400, "CONNECT target must include host and port")
        return parts.hostname, parts.port

    @staticmethod
    async def _send_error(writer: asyncio.StreamWriter, status: int, message: str) -> None:
        headers = {"Proxy-Authenticate": 'Basic realm="Evander session"'} if status == 407 else {}
        await HttpTargetBrowserProxy._send_response(
            writer,
            status,
            f"{message}\n".encode("utf-8"),
            headers=headers,
        )

    @staticmethod
    async def _send_response(
        writer: asyncio.StreamWriter,
        status: int,
        body: bytes,
        *,
        content_type: str = "text/plain; charset=utf-8",
        headers: dict[str, str] | None = None,
    ) -> None:
        reasons = {200: "OK", 400: "Bad Request", 403: "Forbidden", 407: "Proxy Authentication Required", 431: "Request Header Fields Too Large", 502: "Bad Gateway"}
        writer.write(f"HTTP/1.1 {status} {reasons.get(status, 'Error')}\r\n".encode("ascii"))
        writer.write(f"Content-Length: {len(body)}\r\n".encode("ascii"))
        writer.write(f"Content-Type: {content_type}\r\nConnection: close\r\n".encode("ascii"))
        for key, value in (headers or {}).items():
            writer.write(f"{key}: {value}\r\n".encode("latin-1"))
        writer.write(b"\r\n")
        writer.write(body)
        await writer.drain()


async def serve(config: GatewayConfig) -> None:
    proxy = HttpTargetBrowserProxy(config)
    server = await asyncio.start_server(
        proxy.handle_client,
        host=config.http_target_browser_proxy_host,
        port=config.http_target_browser_proxy_port,
        limit=MAX_HEADER_BYTES + 1,
    )
    LOGGER.info(
        "HTTP target browser proxy listening on %s:%s",
        config.http_target_browser_proxy_host,
        config.http_target_browser_proxy_port,
    )
    async with server:
        await server.serve_forever()


def _project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _bootstrap_runtime_config(*, runtime_dir: Path | None = None) -> GatewayConfig:
    return load_runtime_gateway_config(
        runtime_dir=resolve_runtime_dir(runtime_dir=runtime_dir or _project_root() / "runtime")
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the session-scoped HTTP target browser forward proxy."
    )
    parser.add_argument("--runtime-dir", type=Path, help="Runtime directory root.")
    args = parser.parse_args()
    config = _bootstrap_runtime_config(runtime_dir=args.runtime_dir)
    logging.basicConfig(level=config.log_level.upper())
    asyncio.run(serve(config))


if __name__ == "__main__":
    main()
