from __future__ import annotations

import logging
import socket
import socketserver
import threading
from collections.abc import Iterable
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

DEFAULT_CODEX_EGRESS_HOSTS = (
    "auth.openai.com",
    "api.openai.com",
    "chatgpt.com",
    "*.openai.com",
    "*.chatgpt.com",
)


class RestrictedCodexEgressProxy:
    """Minimal CONNECT proxy restricted to operator-approved HTTPS hosts."""

    def __init__(
        self,
        *,
        host: str,
        port: int,
        allowed_hosts: Iterable[str] = DEFAULT_CODEX_EGRESS_HOSTS,
        connect_timeout_seconds: float = 15.0,
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
        self.connect_timeout_seconds = max(0.1, float(connect_timeout_seconds))
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
                proxy._handle_connection(self.request)

        server = _ThreadingTcpServer((self.host, self.port), Handler)
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

    def _handle_connection(self, client: socket.socket) -> None:
        client.settimeout(self.connect_timeout_seconds)
        try:
            header = _read_http_header(client)
            request_line = header.split(b"\r\n", 1)[0].decode("ascii", errors="replace")
            parts = request_line.split()
            if len(parts) != 3 or parts[0].upper() != "CONNECT":
                _send_response(client, 405, "CONNECT required")
                return
            host, port = _parse_connect_authority(parts[1])
            if port != 443 or not self._host_allowed(host):
                _send_response(client, 403, "destination not allowed")
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
            client.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            _tunnel(client, upstream)
        finally:
            upstream.close()

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


def _normalize_allowed_host(value: object) -> str:
    text = str(value or "").strip().rstrip(".").casefold()
    if not text or ":" in text or "/" in text:
        raise ValueError(f"invalid Codex egress host rule: {value!r}")
    if "*" in text and not text.startswith("*."):
        raise ValueError(f"invalid Codex egress wildcard rule: {value!r}")
    return text


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


def _tunnel(client: socket.socket, upstream: socket.socket) -> None:
    client.settimeout(None)
    upstream.settimeout(None)
    stopped = threading.Event()

    def pump(source: socket.socket, destination: socket.socket) -> None:
        try:
            while not stopped.is_set():
                data = source.recv(65536)
                if not data:
                    break
                destination.sendall(data)
        except OSError:
            pass
        finally:
            stopped.set()
            try:
                destination.shutdown(socket.SHUT_WR)
            except OSError:
                pass

    client_to_upstream = threading.Thread(
        target=pump,
        args=(client, upstream),
        name="evander-codex-proxy-upload",
        daemon=True,
    )
    upstream_to_client = threading.Thread(
        target=pump,
        args=(upstream, client),
        name="evander-codex-proxy-download",
        daemon=True,
    )
    client_to_upstream.start()
    upstream_to_client.start()
    client_to_upstream.join()
    upstream_to_client.join()
