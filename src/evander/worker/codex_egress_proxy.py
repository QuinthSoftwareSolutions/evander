from __future__ import annotations

import argparse
import logging
import os
import select
import signal
import socket
import socketserver
import threading
import time
from collections.abc import Callable, Iterable, Sequence
from ipaddress import IPv4Network, IPv6Network, ip_address, ip_network
from urllib.parse import urlsplit
from pathlib import Path

from evander.privacy.providers import ProviderDispatchGuard, runtime_provider_guard

logger = logging.getLogger(__name__)

DEFAULT_CODEX_EGRESS_HOSTS = (
    "auth.openai.com",
    "api.openai.com",
    "chatgpt.com",
    "*.openai.com",
    "*.chatgpt.com",
)
DEFAULT_CODEX_EGRESS_PROXY_HOST = "127.0.0.1"
DEFAULT_CODEX_EGRESS_PROXY_PORT = 11889


class RestrictedCodexEgressProxy:
    """Minimal CONNECT proxy restricted to operator-approved HTTPS hosts."""

    def __init__(
        self,
        *,
        host: str,
        port: int,
        allowed_hosts: Iterable[str] = DEFAULT_CODEX_EGRESS_HOSTS,
        allowed_client_cidrs: Iterable[str] | None = None,
        connect_timeout_seconds: float = 15.0,
        tunnel_idle_timeout_seconds: float = 300.0,
        max_connections: int = 64,
        privacy_guard: ProviderDispatchGuard | None = None,
        runtime_dir: Path | None = None,
    ) -> None:
        self.host = str(host).strip()
        self.port = int(port)
        if not self.host:
            raise ValueError("Codex egress proxy host is required")
        if not (0 <= self.port <= 65535):
            raise ValueError("Codex egress proxy port must be between 0 and 65535")
        self.allowed_hosts = tuple(_normalize_allowed_host(item) for item in allowed_hosts)
        if not self.allowed_hosts:
            raise ValueError("at least one Codex egress host must be allowed")
        self.allowed_client_networks = tuple(
            _normalize_allowed_client_cidr(item) for item in (allowed_client_cidrs or ())
        )
        self.connect_timeout_seconds = max(0.1, float(connect_timeout_seconds))
        self.tunnel_idle_timeout_seconds = max(0.1, float(tunnel_idle_timeout_seconds))
        self.max_connections = int(max_connections)
        self.privacy_guard = privacy_guard or runtime_provider_guard(
            runtime_dir or Path("/var/lib/evander")
        )
        if self.max_connections <= 0:
            raise ValueError("Codex egress proxy max_connections must be positive")
        self._server: _ThreadingTcpServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def bound_port(self) -> int:
        server = self._server
        return int(server.server_address[1]) if server is not None else self.port

    def start(self) -> None:
        if self._server is not None:
            return
        proxy = self

        class Handler(socketserver.BaseRequestHandler):
            def handle(self) -> None:
                proxy._handle_connection(self.request, client_address=self.client_address)

        server = _ThreadingTcpServer(
            (self.host, self.port),
            Handler,
            max_connections=self.max_connections,
        )
        self._server = server
        self._thread = threading.Thread(
            target=server.serve_forever,
            name="evander-codex-egress-proxy",
            daemon=True,
        )
        self._thread.start()

    def close(self) -> None:
        server = self._server
        if server is None:
            return
        server.shutdown()
        server.server_close()
        self._server = None
        thread = self._thread
        if thread is not None:
            thread.join(timeout=5)
        self._thread = None

    def _handle_connection(
        self,
        client: socket.socket,
        *,
        client_address: object | None = None,
    ) -> None:
        client.settimeout(self.connect_timeout_seconds)
        try:
            if not self._client_allowed(client_address):
                _send_response(client, 403, "client not allowed")
                return
            header = _read_http_header(client)
            request_line = header.split(b"\r\n", 1)[0].decode("ascii", errors="replace")
            parts = request_line.split()
            if len(parts) == 3 and parts[0].upper() == "GET" and parts[1] == "/healthz":
                _send_response(client, 200, "ok")
                return
            if len(parts) != 3 or parts[0].upper() != "CONNECT":
                _send_response(client, 405, "CONNECT required")
                return
            host, port = _parse_connect_authority(parts[1])
            if port != 443 or not self._host_allowed(host):
                _send_response(client, 403, "destination not allowed")
                return
            if not self._privacy_allowed(host):
                _send_response(client, 403, "privacy approval required")
                return
            upstream = socket.create_connection((host, port), timeout=self.connect_timeout_seconds)
        except (OSError, ValueError) as exc:
            logger.info(
                "codex_egress_proxy_connect_rejected exception_class=%s",
                exc.__class__.__name__,
            )
            try:
                _send_response(client, 502, "upstream connection failed")
            except OSError:
                pass
            return
        try:
            if not self._privacy_allowed(host):
                _send_response(client, 403, "privacy approval required")
                return
            client.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            _tunnel(
                client,
                upstream,
                idle_timeout_seconds=self.tunnel_idle_timeout_seconds,
                privacy_check=lambda: self._privacy_allowed(host),
            )
        finally:
            upstream.close()

    def _privacy_allowed(self, host: str) -> bool:
        return self.privacy_guard.check(
            provider="codex_subscription",
            endpoint=f"https://{host}",
            connection_id="subscription:installation",
            directory_path=None,
            capability="codex_subscription",
            installation_wide=True,
        ).allowed

    def _client_allowed(self, client_address: object | None) -> bool:
        if not self.allowed_client_networks:
            return True
        if not isinstance(client_address, tuple) or not client_address:
            return False
        try:
            client_ip = ip_address(str(client_address[0]))
        except ValueError:
            return False
        return any(client_ip in network for network in self.allowed_client_networks)

    def _host_allowed(self, host: str) -> bool:
        normalized = host.rstrip(".").casefold()
        for rule in self.allowed_hosts:
            if rule.startswith("*."):
                suffix = rule[1:]
                if normalized.endswith(suffix) and normalized != suffix[1:]:
                    return True
            elif normalized == rule:
                return True
        return False


class _ThreadingTcpServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(
        self,
        server_address: tuple[str, int],
        request_handler_class: type[socketserver.BaseRequestHandler],
        *,
        max_connections: int,
    ) -> None:
        self._connection_slots = threading.BoundedSemaphore(max_connections)
        self.request_queue_size = max(5, min(max_connections, 128))
        super().__init__(server_address, request_handler_class)

    def process_request(self, request: socket.socket, client_address: object) -> None:
        if not self._connection_slots.acquire(blocking=False):
            try:
                request.settimeout(1.0)
                _send_response(request, 503, "proxy connection limit reached")
            except OSError:
                pass
            finally:
                self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self._connection_slots.release()
            raise

    def process_request_thread(self, request: socket.socket, client_address: object) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._connection_slots.release()


def _normalize_allowed_host(value: object) -> str:
    text = str(value or "").strip().rstrip(".").casefold()
    if not text or ":" in text or "/" in text:
        raise ValueError(f"invalid Codex egress host rule: {value!r}")
    if "*" in text and not text.startswith("*."):
        raise ValueError(f"invalid Codex egress wildcard rule: {value!r}")
    return text


def _normalize_allowed_client_cidr(value: object) -> IPv4Network | IPv6Network:
    text = str(value or "").strip()
    if not text:
        raise ValueError("Codex egress client CIDR must not be empty")
    try:
        return ip_network(text, strict=False)
    except ValueError as exc:
        raise ValueError(f"invalid Codex egress client CIDR: {value!r}") from exc


def _environment_list(name: str, fallback: Iterable[str] = ()) -> list[str]:
    value = str(os.environ.get(name) or "").replace(",", " ")
    parsed = [item for item in value.split() if item]
    return parsed or list(fallback)


def build_codex_egress_proxy_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the source- and destination-restricted Codex HTTPS CONNECT proxy."
    )
    parser.add_argument(
        "--host",
        default=os.environ.get(
            "EVANDER_CODEX_EGRESS_PROXY_HOST", DEFAULT_CODEX_EGRESS_PROXY_HOST
        ),
    )
    parser.add_argument(
        "--runtime-dir", type=Path,
        default=Path(os.environ.get("EVANDER_RUNTIME_DIR", "/var/lib/evander")),
        help="Control-plane runtime directory containing authoritative privacy policy.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(
            os.environ.get(
                "EVANDER_CODEX_EGRESS_PROXY_PORT", str(DEFAULT_CODEX_EGRESS_PROXY_PORT)
            )
        ),
    )
    parser.add_argument(
        "--allowed-host",
        action="append",
        default=None,
        help="Allowed HTTPS destination hostname or wildcard; repeat as needed.",
    )
    parser.add_argument(
        "--allowed-client-cidr",
        action="append",
        default=None,
        help="Allowed proxy-client address or CIDR; repeat as needed.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args = build_codex_egress_proxy_parser().parse_args(argv)
    allowed_hosts = args.allowed_host or _environment_list(
        "EVANDER_CODEX_EGRESS_PROXY_ALLOWED_HOSTS", DEFAULT_CODEX_EGRESS_HOSTS
    )
    allowed_client_cidrs = args.allowed_client_cidr or _environment_list(
        "EVANDER_CODEX_EGRESS_PROXY_ALLOWED_CLIENT_CIDRS"
    )
    if not allowed_client_cidrs:
        raise SystemExit(
            "at least one --allowed-client-cidr or "
            "EVANDER_CODEX_EGRESS_PROXY_ALLOWED_CLIENT_CIDRS value is required"
        )
    proxy = RestrictedCodexEgressProxy(
        host=args.host,
        port=args.port,
        allowed_hosts=allowed_hosts,
        allowed_client_cidrs=allowed_client_cidrs,
        runtime_dir=args.runtime_dir,
    )
    stopped = threading.Event()

    def stop(_signum: int, _frame: object) -> None:
        stopped.set()

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    proxy.start()
    logger.info(
        "Codex egress proxy listening on %s:%s for %s client network(s)",
        args.host,
        proxy.bound_port,
        len(proxy.allowed_client_networks),
    )
    try:
        stopped.wait()
    finally:
        proxy.close()


def _read_http_header(client: socket.socket, *, limit: int = 65536) -> bytes:
    chunks = bytearray()
    while b"\r\n\r\n" not in chunks:
        chunk = client.recv(min(4096, limit - len(chunks)))
        if not chunk:
            raise ValueError("proxy client closed before sending a request")
        chunks.extend(chunk)
        if len(chunks) >= limit:
            raise ValueError("proxy request headers are too large")
    header, remainder = bytes(chunks).split(b"\r\n\r\n", 1)
    if remainder:
        raise ValueError("CONNECT request must not include an early request body")
    return header + b"\r\n\r\n"


def _parse_connect_authority(authority: str) -> tuple[str, int]:
    parsed = urlsplit(f"//{authority}")
    host = str(parsed.hostname or "").strip().rstrip(".").casefold()
    if not host:
        raise ValueError("CONNECT destination host is required")
    try:
        port = parsed.port or 443
    except ValueError as exc:
        raise ValueError("CONNECT destination port is invalid") from exc
    return host, port


def _send_response(client: socket.socket, status: int, reason: str) -> None:
    body = (reason + "\n").encode("utf-8")
    client.sendall(
        f"HTTP/1.1 {status} {reason}\r\nContent-Length: {len(body)}\r\n"
        "Connection: close\r\nContent-Type: text/plain\r\n\r\n".encode("ascii")
        + body
    )


def _tunnel(
    client: socket.socket,
    upstream: socket.socket,
    *,
    idle_timeout_seconds: float,
    privacy_check: Callable[[], bool] | None = None,
) -> None:
    """Pump a tunnel on its bounded handler thread and expire inactive peers."""

    timeout = max(0.1, float(idle_timeout_seconds))
    client.settimeout(timeout)
    upstream.settimeout(timeout)
    peers = {client: upstream, upstream: client}
    last_activity = time.monotonic()
    try:
        while True:
            if privacy_check is not None and not privacy_check():
                return
            remaining = timeout - (time.monotonic() - last_activity)
            if remaining <= 0:
                return
            readable, _, exceptional = select.select(
                tuple(peers),
                (),
                tuple(peers),
                min(1.0, remaining) if privacy_check is not None else remaining,
            )
            if exceptional:
                return
            if not readable:
                continue
            for source in readable:
                if privacy_check is not None and not privacy_check():
                    return
                data = source.recv(65536)
                if not data:
                    return
                peers[source].sendall(data)
                last_activity = time.monotonic()
    except (OSError, ValueError):
        return
