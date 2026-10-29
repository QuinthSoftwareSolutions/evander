import base64
from concurrent.futures import ThreadPoolExecutor
import json
import socket
import threading

import httpx
import pytest

from evander.worker.codex_auth_broker import CodexExternalAuthTokens
from evander.worker.codex_runtime_http import (
    ACCOUNTS_PATH,
    CodexRuntimeHttpRelay,
    placeholder_tokens,
)
from evander.worker.codex_runtime_transport import UnixConnection

SECRET = "worker-only-subscription-token"
ACCOUNT = "selected-account"


def discovery(*, origin="NO_CONSTRAINT", routing="NO_CONSTRAINT"):
    return {
        "accounts": [
            {
                "id": ACCOUNT,
                "plan_type": "plus",
                "name": "Synthetic workspace",
                "workspace_backend_origin": origin,
                "account_routing_override": routing,
            }
        ]
    }


def response(status=200, *, data=None, body=b"", headers=None):
    if data is not None:
        body = json.dumps(data).encode()
        headers = {"content-type": "application/json", **(headers or {})}
    return httpx.Response(status, stream=httpx.ByteStream(body), headers=headers)


def request(relay, method="POST", path="/backend-api/codex/responses", body=b"{}", headers=None):
    with UnixConnection(str(relay.path), timeout=5) as connection:
        connection.request(method, path, body=body, headers=headers or {})
        reply = connection.getresponse()
        return reply.status, dict(reply.getheaders()), reply.read()


@pytest.fixture
def relay_factory(socket_tmp_path):
    relays = []

    def create(handler, *, refresh=None, max_connections=12, tokens=None, diagnostics=None):
        value = CodexRuntimeHttpRelay(
            path=socket_tmp_path / f"models-{len(relays)}.sock",
            tokens=tokens or CodexExternalAuthTokens(SECRET, ACCOUNT, "plus"),
            refresh_tokens=refresh or (lambda **kwargs: pytest.fail("Unexpected refresh")),
            client=httpx.Client(transport=httpx.MockTransport(handler)),
            max_connections=max_connections,
            diagnostics=diagnostics,
        )
        value.start()
        relays.append(value)
        return value

    yield create
    for value in relays:
        value.close()


def test_diagnostics_retain_request_ids_and_limits_without_auth_or_bodies(relay_factory):
    from types import SimpleNamespace

    events = []

    def upstream(req):
        if req.url.path == ACCOUNTS_PATH:
            return response(data=discovery())
        return response(
            429,
            body=b"private upstream body",
            headers={
                "x-request-id": "request-123",
                "retry-after": "60",
                "x-ratelimit-remaining-tokens": "0",
                "set-cookie": "private-cookie",
            },
        )

    relay = relay_factory(
        upstream, diagnostics=SimpleNamespace(event=lambda name, data: events.append((name, data)))
    )
    status, _, body = request(relay, body=b"private request body")
    assert status == 429 and b"private upstream body" not in body
    event = next(data for name, data in events if data.get("status_code") == 429)
    assert event["metadata"] == {
        "x-request-id": "request-123",
        "retry-after": "60",
        "x-ratelimit-remaining-tokens": "0",
    }
    encoded = json.dumps(events)
    assert SECRET not in encoded and "private-cookie" not in encoded
    assert "private request body" not in encoded and "private upstream body" not in encoded


def test_worker_attaches_credentials_preserves_selected_route_and_streams(relay_factory):
    seen = []

    def upstream(req):
        seen.append(req)
        if req.url.path == ACCOUNTS_PATH:
            return response(data=discovery(origin="https://us.chatgpt.com", routing="us"))
        assert req.url == "https://us.chatgpt.com/backend-api/codex/responses"
        assert req.headers["authorization"] == "Bearer " + SECRET
        assert req.headers["chatgpt-account-id"] == ACCOUNT
        assert req.headers["x-openai-account-routing-override"] == "us"
        assert req.headers["x-codex-turn-state"] == "same-turn"
        assert req.content == b'{"model":"synthetic"}'
        assert "cookie" not in req.headers and "x-forwarded-host" not in req.headers
        return response(
            body=b"data: first\n\ndata: second\n\n",
            headers={
                "content-type": "text/event-stream",
                "x-codex-turn-state": "next-turn",
                "set-cookie": "private-cookie",
            },
        )

    relay = relay_factory(upstream)
    status, headers, body = request(
        relay,
        body=b'{"model":"synthetic"}',
        headers={
            "Authorization": "Bearer caller-token",
            "ChatGPT-Account-Id": "other-account",
            "X-OpenAI-Account-Routing-Override": "us_cr",
            "Cookie": "caller-cookie",
            "X-Forwarded-Host": "attacker.invalid",
            "X-Codex-Turn-State": "same-turn",
        },
    )
    assert status == 200 and body == b"data: first\n\ndata: second\n\n"
    assert headers["x-codex-turn-state"] == "next-turn" and "set-cookie" not in headers
    assert SECRET.encode() not in body
    # Direct model requests cannot skip account discovery or its residency policy.
    assert [req.url.host for req in seen] == ["chatgpt.com", "us.chatgpt.com"]
    status, _, body = request(relay, "GET", ACCOUNTS_PATH, body=b"")
    selected = json.loads(body)["accounts"]
    assert status == 200 and len(selected) == 1 and selected[0]["id"] == ACCOUNT
    assert selected[0]["workspace_backend_origin"] == "NO_CONSTRAINT"
    assert selected[0]["account_routing_override"] == "NO_CONSTRAINT"
    assert len(seen) == 2


@pytest.mark.parametrize("suffix", ["responses/compact", "alpha/search"])
def test_native_compaction_and_search_keep_selected_account_routing(relay_factory, suffix):
    def upstream(req):
        if req.url.path == ACCOUNTS_PATH:
            return response(data=discovery(origin="https://us.chatgpt.com", routing="us"))
        assert req.url == "https://us.chatgpt.com/backend-api/codex/" + suffix
        assert req.headers["authorization"] == "Bearer " + SECRET
        assert req.headers["chatgpt-account-id"] == ACCOUNT
        assert req.headers["x-openai-account-routing-override"] == "us"
        return response(data={"output": "synthetic result"})

    relay = relay_factory(upstream)
    status, _, body = request(relay, path="/backend-api/codex/" + suffix)
    assert status == 200 and json.loads(body) == {"output": "synthetic result"}


@pytest.mark.parametrize(
    "method,path",
    [
        ("POST", "http://attacker.invalid/backend-api/codex/responses"),
        ("POST", "//attacker.invalid/backend-api/codex/responses"),
        ("POST", "/backend-api/codex/responses/../login"),
        ("POST", "/backend-api/codex/%72esponses"),
        ("POST", "/backend-api/codex/responses?redirect=https://attacker.invalid"),
        ("POST", "/backend-api/codex/responses#fragment"),
        ("GET", "/backend-api/codex/responses"),
        ("POST", "/backend-api/codex/responses/compact?account_id=other"),
        ("POST", "/oauth/token"),
    ],
)
def test_relay_rejects_noncanonical_targets_before_using_credentials(relay_factory, method, path):
    relay = relay_factory(lambda req: pytest.fail("Rejected request reached upstream"))
    assert request(relay, method, path)[0] == 403


@pytest.mark.parametrize(
    "origin,routing",
    [
        ("https://attacker.invalid", "NO_CONSTRAINT"),
        ("https://chatgpt.com.attacker.invalid", "us"),
        ("http://chatgpt.com", "NO_CONSTRAINT"),
        ("https://chatgpt.com:8443", "NO_CONSTRAINT"),
        ("https://user:password@chatgpt.com", "NO_CONSTRAINT"),
        ("https://chatgpt.com/path", "NO_CONSTRAINT"),
        ("NO_CONSTRAINT", "unknown"),
    ],
)
def test_discovery_cannot_redirect_worker_credentials_to_another_origin(
    relay_factory, origin, routing
):
    seen = []

    def upstream(req):
        seen.append(req.url)
        return response(data=discovery(origin=origin, routing=routing))

    relay = relay_factory(upstream)
    assert request(relay)[0] == 403
    assert [str(url) for url in seen] == ["https://chatgpt.com" + ACCOUNTS_PATH]


@pytest.mark.parametrize("accounts", [[], [{"id": "other"}], discovery()["accounts"] * 2])
def test_discovery_requires_exact_selected_account(relay_factory, accounts):
    relay = relay_factory(lambda req: response(data={"accounts": accounts}))
    assert request(relay)[0] == 403


@pytest.mark.parametrize("same_account", [True, False])
def test_refresh_stays_on_worker_and_cannot_switch_accounts(relay_factory, same_account):
    calls = []
    refreshes = []

    def upstream(req):
        calls.append(req)
        if req.url.path == ACCOUNTS_PATH:
            return response(data=discovery())
        if req.headers["authorization"] == "Bearer " + SECRET:
            return response(401, body=SECRET.encode())
        assert req.headers["authorization"] == "Bearer rotated-worker-token"
        return response(body=b"accepted")

    def refresh(**kwargs):
        refreshes.append(kwargs)
        return CodexExternalAuthTokens(
            "rotated-worker-token", ACCOUNT if same_account else "other", "plus"
        )

    relay = relay_factory(upstream, refresh=refresh)
    status, _, body = request(relay)
    assert status == (200 if same_account else 403)
    assert refreshes == [{"previous_access_token": SECRET, "previous_account_id": ACCOUNT}]
    assert SECRET.encode() not in body and b"rotated-worker-token" not in body
    assert len(calls) == (4 if same_account else 2)


@pytest.mark.parametrize("status", [302, 401, 403, 429, 500])
def test_upstream_errors_and_redirects_never_return_credentials(relay_factory, status):
    seen = []

    def upstream(req):
        seen.append(req.url)
        if req.url.path == ACCOUNTS_PATH:
            return response(data=discovery())
        return response(
            status, body=SECRET.encode(), headers={"location": "https://attacker.invalid/" + SECRET}
        )

    relay = relay_factory(
        upstream, refresh=lambda **kwargs: CodexExternalAuthTokens("rotation", ACCOUNT, "plus")
    )
    result, headers, body = request(relay)
    assert result == (502 if status == 302 else status)
    assert "location" not in headers and SECRET.encode() not in body
    assert all(url.host == "chatgpt.com" for url in seen)


def test_model_relay_bounds_idle_clients_and_shutdown_interrupts_them(relay_factory):
    relay = relay_factory(lambda req: pytest.fail("Unexpected request"), max_connections=1)
    first = socket.socket(socket.AF_UNIX)
    first.connect(str(relay.path))
    ready = threading.Event()
    # A complete header puts this client in its bounded body-read slot.
    first.sendall(
        b"POST /backend-api/codex/responses HTTP/1.1\r\nHost: local\r\nContent-Length: 10\r\n\r\n"
    )
    for _ in range(100):
        with relay.lock:
            if relay.connections:
                ready.set()
                break
        ready.wait(0.01)
    try:
        assert ready.is_set()
        assert request(relay)[0] == 503
        relay.close()
        assert first.recv(1) == b""
    finally:
        first.close()


def test_native_placeholder_has_no_upstream_authority():
    original = CodexExternalAuthTokens(SECRET, ACCOUNT, "plus")
    value = placeholder_tokens(original)
    assert value.account_id == ACCOUNT and value.plan_type == "plus"
    assert value.access_token != SECRET and SECRET not in value.access_token
    assert "evander-placeholder-" in value.access_token


def test_concurrent_rejections_refresh_the_bound_profile_once(relay_factory):
    rejected = threading.Barrier(2)
    refreshes = []

    def upstream(req):
        if req.url.path == ACCOUNTS_PATH:
            return response(data=discovery())
        if req.headers["authorization"] == "Bearer " + SECRET:
            rejected.wait(timeout=5)
            return response(401)
        assert req.headers["authorization"] == "Bearer rotated-token"
        assert req.headers["chatgpt-account-id"] == ACCOUNT
        return response(body=b"accepted")

    def refresh(**kwargs):
        refreshes.append(kwargs)
        return CodexExternalAuthTokens("rotated-token", ACCOUNT, "plus")

    relay = relay_factory(upstream, refresh=refresh)
    with ThreadPoolExecutor(max_workers=2) as executor:
        replies = list(executor.map(lambda _: request(relay), range(2)))
    assert [(status, body) for status, _, body in replies] == [(200, b"accepted")] * 2
    assert refreshes == [{"previous_access_token": SECRET, "previous_account_id": ACCOUNT}]


def test_worker_applies_fedramp_claim_without_exposing_real_token(relay_factory):
    claims = {"https://api.openai.com/auth": {"chatgpt_account_is_fedramp": True}}
    encoded = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    tokens = CodexExternalAuthTokens("synthetic." + encoded + ".private-signature", ACCOUNT, "plus")
    seen = []

    def upstream(req):
        seen.append(req)
        assert req.headers["authorization"] == "Bearer " + tokens.access_token
        assert req.headers["x-openai-fedramp"] == "true"
        if req.url.path == ACCOUNTS_PATH:
            return response(data=discovery(origin="https://us.chatgpt.com", routing="us_cr"))
        assert req.headers["x-openai-account-routing-override"] == "us_cr"
        return response(body=b"accepted")

    relay = relay_factory(upstream, tokens=tokens)
    assert request(relay, headers={"X-OpenAI-Fedramp": "false"})[0] == 200
    placeholder = placeholder_tokens(tokens)
    payload = placeholder.access_token.split(".")[1]
    decoded = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    assert decoded["https://api.openai.com/auth"]["chatgpt_account_is_fedramp"] is True
    assert tokens.access_token not in placeholder.access_token
    assert len(seen) == 2


def test_model_catalog_cache_validation_preserves_not_modified(relay_factory):
    def upstream(req):
        if req.url.path == ACCOUNTS_PATH:
            return response(data=discovery())
        assert req.headers["if-none-match"] == '"catalog-v1"'
        return response(304, headers={"etag": '"catalog-v1"'})

    relay = relay_factory(upstream)
    status, headers, body = request(
        relay,
        "GET",
        "/backend-api/codex/models?client_version=0.156.1",
        body=b"",
        headers={"If-None-Match": '"catalog-v1"'},
    )
    assert status == 304 and not body and headers["etag"] == '"catalog-v1"'
    assert "Transfer-Encoding" not in headers


@pytest.mark.parametrize(
    "framing,status",
    [
        (b"Content-Length: 9\r\n", 413),
        (b"Content-Length: 0\r\nContent-Length: 0\r\n", 403),
        (b"Transfer-Encoding: chunked\r\n", 403),
    ],
)
def test_invalid_request_framing_uses_no_credentials(relay_factory, monkeypatch, framing, status):
    monkeypatch.setattr("evander.worker.codex_runtime_http.MAX_BODY", 8)
    relay = relay_factory(lambda req: pytest.fail("Invalid framing reached upstream"))
    with socket.socket(socket.AF_UNIX) as client:
        client.settimeout(5)
        client.connect(str(relay.path))
        client.sendall(
            b"POST /backend-api/codex/responses HTTP/1.1\r\nHost: local\r\n" + framing + b"\r\n"
        )
        assert client.recv(65536).startswith(f"HTTP/1.1 {status} ".encode())
