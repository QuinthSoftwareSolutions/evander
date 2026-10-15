"""Job-scoped subscription credential attachment, owned entirely by the worker."""

from __future__ import annotations

import base64
from http.server import BaseHTTPRequestHandler
import json
from pathlib import Path
import secrets
import socket
import socketserver
import threading
from urllib.parse import parse_qsl, urlsplit

import httpx

from .codex_auth_broker import CodexExternalAuthTokens
from .unix_socket import bind_unix_socket

MAX_BODY = 32 * 1024 * 1024
MAX_METADATA = 4 * 1024 * 1024
DEFAULT_ORIGIN = "https://chatgpt.com"
ACCOUNTS_PATH = "/backend-api/wham/accounts/check"
ACCOUNT_PATHS = {ACCOUNTS_PATH, "/api/codex/accounts/check"}
REQUEST_HEADERS = {
    "accept",
    "content-type",
    "content-encoding",
    "user-agent",
    "openai-beta",
    "originator",
    "version",
    "session_id",
    "conversation_id",
    "x-client-request-id",
    "x-openai-subagent",
    "x-codex-turn-state",
    "x-codex-turn-metadata",
    "x-codex-parent-thread-id",
    "x-codex-window-id",
    "x-codex-installation-id",
    "x-codex-routing-hint",
    "x-openai-internal-codex-responses-lite",
    "if-none-match",
}


def fedramp_claim(tokens):
    try:
        part = tokens.access_token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
        return (
            claims.get("https://api.openai.com/auth", {}).get("chatgpt_account_is_fedramp") is True
        )
    except (ValueError, IndexError, AttributeError, TypeError):
        return False


def placeholder_tokens(tokens):
    """Create a native-only envelope with no authenticating upstream material."""

    def encode(value):
        return base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip("=")

    token = ".".join(
        (
            encode({"alg": "none"}),
            encode(
                {
                    "exp": 4102444800,
                    "https://api.openai.com/auth": {
                        "chatgpt_account_id": tokens.account_id,
                        "chatgpt_plan_type": tokens.plan_type,
                        "chatgpt_account_is_fedramp": fedramp_claim(tokens),
                    },
                }
            ),
            "evander-placeholder-" + secrets.token_urlsafe(32),
        )
    )
    return CodexExternalAuthTokens(token, tokens.account_id, tokens.plan_type)


def checked_target(method, target):
    # Accept origin-form URLs only, with exact native endpoints. No decoding or
    # normalization may turn a caller's path into another authenticated surface.
    if len(target) > 8192 or any(char in target for char in ("%", "\\", "#", "\r", "\n")):
        raise ValueError("Unsupported runtime request target")
    parsed = urlsplit(target)
    if parsed.scheme or parsed.netloc or not target.startswith("/") or target.startswith("//"):
        raise ValueError("Unsupported runtime request target")
    allowed = {
        "GET": ACCOUNT_PATHS
        | {
            "/backend-api/codex/models",
            "/backend-api/wham/usage",
            "/backend-api/codex/config",
        },
        "POST": {
            "/backend-api/codex/responses",
            "/backend-api/codex/responses/compact",
            "/backend-api/codex/alpha/search",
        },
    }
    if parsed.path not in allowed.get(method, set()):
        raise ValueError("Unsupported runtime endpoint")
    query = (
        parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True) if parsed.query else []
    )
    if any(key != "client_version" or len(value) > 100 for key, value in query) or len(query) > 1:
        raise ValueError("Unsupported runtime query")
    return parsed.path


def checked_route(payload, account_id):
    entries = payload.get("accounts")
    if not isinstance(entries, list):
        raise ValueError("Account discovery lacks workspace routing")
    selected = [
        entry for entry in entries if isinstance(entry, dict) and entry.get("id") == account_id
    ]
    if len(selected) != 1:
        raise ValueError("Selected account missing or ambiguous")
    entry = selected[0]
    origin = entry.get("workspace_backend_origin")
    if origin == "NO_CONSTRAINT":
        origin = DEFAULT_ORIGIN
    if not isinstance(origin, str):
        raise ValueError("Missing account routing origin")
    parsed = urlsplit(origin)
    host = parsed.hostname or ""
    if (
        parsed.scheme != "https"
        or parsed.username
        or parsed.password
        or parsed.port not in {None, 443}
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or origin.strip() != origin
        or not any(
            host == suffix or host.endswith("." + suffix)
            for suffix in ("chatgpt.com", "openai.com")
        )
    ):
        raise ValueError("Invalid account routing origin")
    routing = entry.get("account_routing_override")
    if routing not in {"NO_CONSTRAINT", "us", "us_cr"}:
        raise ValueError("Invalid account routing constraint")
    public = {
        key: value
        for key, value in entry.items()
        if key in {"id", "plan_type", "name", "profile_picture_url", "structure"}
    }
    # The worker applies the real routing constraint. Native must remain attached
    # to its local TLS adapter even for a region-constrained workspace.
    public.update(
        workspace_backend_origin="NO_CONSTRAINT", account_routing_override="NO_CONSTRAINT"
    )
    return (
        origin.rstrip("/"),
        routing,
        {
            "accounts": [public],
            "account_ordering": [account_id],
            "default_account_id": account_id,
        },
    )


class _Unauthorized(Exception):
    pass


class _UpstreamFailure(Exception):
    def __init__(self, status=502):
        self.status = status if 400 <= status <= 599 else 502


class _Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True
    block_on_close = False

    def server_bind(self):
        bind_unix_socket(self.socket, Path(self.server_address))

    def process_request(self, request, client_address):
        if not self.relay.slots.acquire(blocking=False):
            request.sendall(
                b"HTTP/1.1 503 Service Unavailable\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
            )
            self.shutdown_request(request)
            return
        with self.relay.lock:
            self.relay.connections.add(request)
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.relay.release(request)
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.relay.release(request)

    def handle_error(self, request, client_address):
        # Do not log HTTPX exception/request representations containing headers.
        pass


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def setup(self):
        self.request.settimeout(30)
        super().setup()

    def do_GET(self):
        self.forward()

    def do_POST(self):
        self.forward()

    def reply(self, status, data):
        self.send_response_only(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(data)

    def forward(self):
        self.close_connection = True
        relay = self.server.relay
        started = False
        response = None
        try:
            path = checked_target(self.command, self.path)
            lengths = self.headers.get_all("Content-Length", [])
            if self.headers.get("Transfer-Encoding") or len(lengths) > 1:
                raise ValueError("Ambiguous request framing")
            size = lengths[0] if lengths else "0"
            if not size.isdecimal() or int(size) > MAX_BODY:
                self.reply(413, b'{"error":"Runtime request too large"}')
                return
            body = self.rfile.read(int(size))
            if len(body) != int(size) or self.command == "GET" and body:
                raise ValueError("Invalid runtime body")
            headers = {
                key.lower(): value
                for key, value in self.headers.items()
                if key.lower() in REQUEST_HEADERS
            }
            response = relay.open(self.command, self.path, path, headers, body)
            if isinstance(response, bytes):
                self.reply(200, response)
                return
            cached_catalog = response.status_code == 304 and path == "/backend-api/codex/models"
            if not 200 <= response.status_code < 300 and not cached_catalog:
                raise _UpstreamFailure(response.status_code)
            self.send_response_only(response.status_code)
            for key, value in response.headers.items():
                if key in {
                    "content-type",
                    "content-encoding",
                    "etag",
                    "retry-after",
                    "x-request-id",
                    "request-id",
                } or key.startswith(("x-codex-", "x-ratelimit-")):
                    self.send_header(key, value)
            if response.status_code not in {204, 304}:
                self.send_header("Transfer-Encoding", "chunked")
            self.send_header("Connection", "close")
            self.end_headers()
            started = True
            if response.status_code in {204, 304}:
                return
            for chunk in response.iter_raw():
                if relay.closed.is_set():
                    return
                if chunk:
                    self.wfile.write(f"{len(chunk):x}\r\n".encode() + chunk + b"\r\n")
                    self.wfile.flush()
            self.wfile.write(b"0\r\n\r\n")
        except ValueError:
            if not started:
                self.reply(403, b'{"error":"Unsupported runtime request"}')
        except _UpstreamFailure as exc:
            if not started:
                self.reply(exc.status, b'{"error":"Subscription runtime upstream failed"}')
        except Exception:
            if not started:
                self.reply(502, b'{"error":"Subscription runtime unavailable"}')
        finally:
            if isinstance(response, httpx.Response):
                relay.finish(response)


class CodexRuntimeHttpRelay:
    def __init__(self, *, path: Path, tokens, refresh_tokens, client, max_connections=12):
        self.path = path
        self.tokens = tokens
        self.account_id = tokens.account_id
        self.refresh_tokens = refresh_tokens
        self.client = client
        self.auth_lock = threading.Lock()
        self.route_lock = threading.Lock()
        self.route = None
        self.route_token = None
        self.lock = threading.Lock()
        self.connections = set()
        self.responses = set()
        self.closed = threading.Event()
        self.slots = threading.BoundedSemaphore(max_connections)
        self.server = None

    def start(self):
        self.path.unlink(missing_ok=True)
        self.server = _Server(str(self.path), _Handler)
        self.server.relay = self
        self.path.chmod(0o666)  # Private job mount is the capability boundary.
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.closed.set()
        with self.lock:
            responses, connections = list(self.responses), list(self.connections)
        for connection in connections:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        for response in responses:
            response.close()
        self.client.close()
        if self.server:
            self.server.shutdown()
            self.server.server_close()
        self.path.unlink(missing_ok=True)

    def release(self, connection):
        with self.lock:
            self.connections.discard(connection)
        self.slots.release()

    def finish(self, response):
        response.close()
        with self.lock:
            self.responses.discard(response)

    def send(self, method, target, headers, body, tokens, origin, routing="NO_CONSTRAINT"):
        if self.closed.is_set():
            raise RuntimeError("Runtime relay closed")
        headers = {
            **headers,
            "authorization": "Bearer " + tokens.access_token,
            "chatgpt-account-id": self.account_id,
        }
        if routing != "NO_CONSTRAINT":
            headers["x-openai-account-routing-override"] = routing
        if fedramp_claim(tokens):
            headers["x-openai-fedramp"] = "true"
        request = self.client.build_request(method, origin + target, headers=headers, content=body)
        response = self.client.send(request, stream=True, follow_redirects=False)
        with self.lock:
            if self.closed.is_set():
                response.close()
                raise RuntimeError("Runtime relay closed")
            self.responses.add(response)
        return response

    def discover(self, tokens):
        with self.route_lock:
            if self.route_token == tokens.access_token:
                return self.route
            response = self.send("GET", ACCOUNTS_PATH, {}, b"", tokens, DEFAULT_ORIGIN)
            try:
                if response.status_code == 401:
                    raise _Unauthorized()
                if response.status_code != 200:
                    raise _UpstreamFailure(response.status_code)
                data = bytearray()
                for chunk in response.iter_bytes():
                    data.extend(chunk)
                    if len(data) > MAX_METADATA:
                        raise ValueError("Account discovery too large")
                route = checked_route(json.loads(data), self.account_id)
                self.route, self.route_token = route, tokens.access_token
                return route
            finally:
                self.finish(response)

    def refresh(self, rejected):
        with self.auth_lock:
            if self.closed.is_set():
                raise RuntimeError("Runtime relay closed")
            if self.tokens.access_token == rejected.access_token:
                value = self.refresh_tokens(
                    previous_access_token=rejected.access_token, previous_account_id=self.account_id
                )
                if value.account_id != self.account_id:
                    raise ValueError("Subscription account changed during runtime execution")
                self.tokens = value

    def open(self, method, target, path, headers, body):
        for attempt in range(2):
            with self.auth_lock:
                tokens = self.tokens
            try:
                origin, routing, public = self.discover(tokens)
                if path in ACCOUNT_PATHS:
                    return json.dumps(public).encode()
                response = self.send(method, target, headers, body, tokens, origin, routing)
                if response.status_code != 401:
                    return response
                self.finish(response)
            except _Unauthorized:
                pass
            if attempt:
                raise _UpstreamFailure(401)
            self.refresh(tokens)
        raise _UpstreamFailure(401)
