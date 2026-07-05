from __future__ import annotations

import json
import socket
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from evander.config import GatewayConfig
from evander.effects.providers.base import EffectRuntimeContext
from evander.effects.providers.http_request import HttpRequestEffectProvider
from evander.events.provider_models import HttpRequestEffectDefinition


def _runtime_context(tmp_path: Path, *, timeout_seconds: float = 12.5) -> EffectRuntimeContext:
    return EffectRuntimeContext(
        resolved_config=GatewayConfig(
            runtime_dir=tmp_path / "runtime",
            openai_relay_timeout_seconds=timeout_seconds,
        ),
        admin_service=SimpleNamespace(),
        env={},
    )


def _effect_definition(**overrides: object) -> HttpRequestEffectDefinition:
    payload: dict[str, object] = {
        "id": "effect-http",
        "display_name": "HTTP request",
        "type": "effect:http:request",
        "url": "https://example.test/api",
    }
    payload.update(overrides)
    return HttpRequestEffectDefinition.model_validate(payload)


def _install_mock_client(
    monkeypatch: pytest.MonkeyPatch,
    transport: httpx.MockTransport,
    *,
    captured_calls: list[dict[str, object]] | None = None,
) -> None:
    original_client = httpx.Client

    def _client_factory(*args: object, **kwargs: object) -> httpx.Client:
        if captured_calls is not None:
            captured_calls.append({"args": args, "kwargs": dict(kwargs)})
        kwargs = dict(kwargs)
        kwargs["transport"] = transport
        return original_client(*args, **kwargs)

    monkeypatch.setattr("evander.effects.providers.http_request.httpx.Client", _client_factory)


def test_http_request_provider_gets_json_response_without_payload(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    requests: list[httpx.Request] = []
    client_calls: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={"id": 42, "status": "ok"},
            headers={
                "content-type": "application/json; charset=utf-8",
                "x-request-id": "req-123",
            },
            request=request,
        )

    transport = httpx.MockTransport(handler)
    _install_mock_client(monkeypatch, transport, captured_calls=client_calls)

    provider = HttpRequestEffectProvider()
    provider.initialize(_effect_definition(), _runtime_context(tmp_path))

    result = provider.execute()

    assert len(requests) == 1
    request = requests[0]
    assert request.method == "GET"
    assert str(request.url) == "https://example.test/api"
    assert request.content == b""
    assert client_calls[0]["kwargs"]["follow_redirects"] is False
    assert client_calls[0]["kwargs"]["timeout"] == 12.5
    assert result["success"] is True
    assert result["status_code"] == 200
    assert result["method"] == "GET"
    assert result["url"] == "https://example.test/api"
    assert result["headers"]["x-request-id"] == "req-123"
    assert result["body_json"] == {"id": 42, "status": "ok"}
    assert json.loads(result["body_text"]) == {"id": 42, "status": "ok"}
    assert result["content_type"] == "application/json; charset=utf-8"
    assert isinstance(result["elapsed_ms"], int)
    assert result["elapsed_ms"] >= 0


def test_http_request_provider_posts_json_body_and_normalizes_method(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            201,
            json={"created": True},
            headers={"content-type": "application/json"},
            request=request,
        )

    transport = httpx.MockTransport(handler)
    _install_mock_client(monkeypatch, transport)

    provider = HttpRequestEffectProvider()
    provider.initialize(_effect_definition(), _runtime_context(tmp_path))

    result = provider.execute(
        resolved_config={
            "method": "post",
            "payload": {"name": "demo", "enabled": True},
            "headers": {"X-Test": "1", "": "skip"},
        }
    )

    assert len(requests) == 1
    request = requests[0]
    assert request.method == "POST"
    assert request.headers["x-test"] == "1"
    assert "" not in request.headers
    assert result["method"] == "POST"
    assert result["status_code"] == 201
    assert result["body_json"] == {"created": True}
    assert result["success"] is True
    assert request.content.decode("utf-8") == "{\"name\":\"demo\",\"enabled\":true}"


def test_http_request_provider_returns_body_json_none_for_text_response(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text="plain text body",
            headers={"content-type": "text/plain; charset=utf-8"},
            request=request,
        )

    transport = httpx.MockTransport(handler)
    _install_mock_client(monkeypatch, transport)

    provider = HttpRequestEffectProvider()
    provider.initialize(_effect_definition(), _runtime_context(tmp_path))

    result = provider.execute()

    assert result["success"] is True
    assert result["body_text"] == "plain text body"
    assert result["body_json"] is None
    assert result["content_type"] == "text/plain; charset=utf-8"


def test_http_request_provider_allows_matching_url_pattern(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"ok": True}, request=request)

    transport = httpx.MockTransport(handler)
    _install_mock_client(monkeypatch, transport)

    provider = HttpRequestEffectProvider()
    provider.initialize(_effect_definition(), _runtime_context(tmp_path))

    result = provider.execute(
        resolved_config={
            "url": "https://api.example.test/items/42",
            "allowed_url_patterns": ["https://api.example.test/*"],
        }
    )

    assert len(requests) == 1
    assert str(requests[0].url) == "https://api.example.test/items/42"
    assert result["success"] is True


def test_http_request_provider_allows_bound_public_url_with_explicit_allowlist(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"ok": True}, request=request)

    transport = httpx.MockTransport(handler)
    _install_mock_client(monkeypatch, transport)

    provider = HttpRequestEffectProvider()
    provider.initialize(_effect_definition(url="${{trigger.url}}"), _runtime_context(tmp_path))

    result = provider.execute(
        resolved_config={
            "url": "https://api.example.test/items/42",
            "allowed_url_patterns": ["https://api.example.test/*"],
        }
    )

    assert len(requests) == 1
    assert str(requests[0].url) == "https://api.example.test/items/42"
    assert result["success"] is True


def test_http_request_provider_rejects_bound_url_without_explicit_allowlist(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    requests: list[httpx.Request] = []
    transport = httpx.MockTransport(
        lambda request: requests.append(request) or httpx.Response(200, request=request)
    )
    _install_mock_client(monkeypatch, transport)

    provider = HttpRequestEffectProvider()
    provider.initialize(_effect_definition(url="${{trigger.url}}"), _runtime_context(tmp_path))

    with pytest.raises(ValueError, match="bound urls require explicit allowed_url_patterns"):
        provider.execute(resolved_config={"url": "https://api.example.test/items/42"})

    assert requests == []


def test_http_request_provider_rejects_bound_url_with_global_allowlist(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    requests: list[httpx.Request] = []
    transport = httpx.MockTransport(
        lambda request: requests.append(request) or httpx.Response(200, request=request)
    )
    _install_mock_client(monkeypatch, transport)

    provider = HttpRequestEffectProvider()
    provider.initialize(_effect_definition(url="${{trigger.url}}"), _runtime_context(tmp_path))

    with pytest.raises(
        ValueError,
        match="bound urls require host-restricted allowed_url_patterns",
    ):
        provider.execute(
            resolved_config={
                "url": "https://api.example.test/items/42",
                "allowed_url_patterns": ["*"],
            }
        )

    assert requests == []


@pytest.mark.parametrize(
    ("url", "allowed_url_patterns", "message"),
    [
        (
            "http://127.0.0.1:8080/admin",
            ["http://127.0.0.1:8080/*"],
            "blocked private, loopback, or link-local destination",
        ),
        (
            "http://10.1.2.3/api",
            ["http://10.1.2.3/*"],
            "blocked private, loopback, or link-local destination",
        ),
        (
            "http://169.254.1.2/api",
            ["http://169.254.1.2/*"],
            "blocked private, loopback, or link-local destination",
        ),
        (
            "http://169.254.169.254/latest/meta-data",
            ["http://169.254.169.254/*"],
            "blocked metadata destination",
        ),
        (
            "http://metadata.google.internal/computeMetadata/v1/",
            ["http://metadata.google.internal/*"],
            "blocked metadata destination",
        ),
    ],
)
def test_http_request_provider_rejects_bound_private_and_metadata_destinations_by_default(
    url: str,
    allowed_url_patterns: list[str],
    message: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    requests: list[httpx.Request] = []
    transport = httpx.MockTransport(
        lambda request: requests.append(request) or httpx.Response(200, request=request)
    )
    _install_mock_client(monkeypatch, transport)

    provider = HttpRequestEffectProvider()
    provider.initialize(_effect_definition(url="${{trigger.url}}"), _runtime_context(tmp_path))

    with pytest.raises(ValueError, match=message):
        provider.execute(
            resolved_config={
                "url": url,
                "allowed_url_patterns": allowed_url_patterns,
            }
        )

    assert requests == []


def test_http_request_provider_rejects_hostname_that_resolves_to_metadata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    requests: list[httpx.Request] = []
    transport = httpx.MockTransport(
        lambda request: requests.append(request) or httpx.Response(200, request=request)
    )
    _install_mock_client(monkeypatch, transport)

    def fake_getaddrinfo(
        host: str,
        port: int,
        *_args: object,
        **_kwargs: object,
    ) -> list[tuple[object, object, object, str, tuple[str, int]]]:
        assert host == "169.254.169.254.nip.io"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", port))]

    monkeypatch.setattr(
        "evander.effects.providers.http_request.socket.getaddrinfo",
        fake_getaddrinfo,
    )

    provider = HttpRequestEffectProvider()
    provider.initialize(_effect_definition(url="${{trigger.url}}"), _runtime_context(tmp_path))

    with pytest.raises(ValueError, match="blocked metadata destination"):
        provider.execute(
            resolved_config={
                "url": "http://169.254.169.254.nip.io/latest/meta-data",
                "allowed_url_patterns": ["http://169.254.169.254.nip.io/*"],
            }
        )

    assert requests == []


def test_http_request_provider_rejects_numeric_hostname_that_resolves_to_loopback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    requests: list[httpx.Request] = []
    transport = httpx.MockTransport(
        lambda request: requests.append(request) or httpx.Response(200, request=request)
    )
    _install_mock_client(monkeypatch, transport)

    provider = HttpRequestEffectProvider()
    provider.initialize(_effect_definition(url="${{trigger.url}}"), _runtime_context(tmp_path))

    with pytest.raises(
        ValueError,
        match="blocked private, loopback, or link-local destination",
    ):
        provider.execute(
            resolved_config={
                "url": "http://2130706433/admin",
                "allowed_url_patterns": ["http://2130706433/*"],
            }
        )

    assert requests == []


def test_http_request_provider_allows_public_hostname_resolution(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"ok": True}, request=request)

    transport = httpx.MockTransport(handler)
    _install_mock_client(monkeypatch, transport)

    def fake_getaddrinfo(
        host: str,
        port: int,
        *_args: object,
        **_kwargs: object,
    ) -> list[tuple[object, object, object, str, tuple[str, int]]]:
        assert host == "api.example.test"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]

    monkeypatch.setattr(
        "evander.effects.providers.http_request.socket.getaddrinfo",
        fake_getaddrinfo,
    )

    provider = HttpRequestEffectProvider()
    provider.initialize(_effect_definition(url="${{trigger.url}}"), _runtime_context(tmp_path))

    result = provider.execute(
        resolved_config={
            "url": "https://api.example.test/items/42",
            "allowed_url_patterns": ["https://api.example.test/*"],
        }
    )

    assert len(requests) == 1
    assert result["success"] is True


def test_http_request_provider_allows_private_destination_with_explicit_opt_in(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"ok": True}, request=request)

    transport = httpx.MockTransport(handler)
    _install_mock_client(monkeypatch, transport)

    provider = HttpRequestEffectProvider()
    provider.initialize(_effect_definition(url="${{trigger.url}}"), _runtime_context(tmp_path))

    result = provider.execute(
        resolved_config={
            "url": "http://10.1.2.3/api",
            "allowed_url_patterns": ["http://10.1.2.3/*"],
            "allow_private_networks": True,
        }
    )

    assert len(requests) == 1
    assert str(requests[0].url) == "http://10.1.2.3/api"
    assert result["success"] is True


def test_http_request_provider_rejects_disallowed_url_before_request(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"ok": True}, request=request)

    transport = httpx.MockTransport(handler)
    _install_mock_client(monkeypatch, transport)

    provider = HttpRequestEffectProvider()
    provider.initialize(_effect_definition(), _runtime_context(tmp_path))

    with pytest.raises(ValueError, match="url is not allowed by allowed_url_patterns"):
        provider.execute(
            resolved_config={
                "url": "https://api.example.test@169.254.169.254/latest/meta-data",
                "allowed_url_patterns": ["https://api.example.test/*"],
            }
        )

    assert requests == []


def test_http_request_provider_returns_failure_result_when_check_is_disabled(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            503,
            text="service unavailable",
            headers={"content-type": "text/plain"},
            request=request,
        )

    transport = httpx.MockTransport(handler)
    _install_mock_client(monkeypatch, transport)

    provider = HttpRequestEffectProvider()
    provider.initialize(_effect_definition(), _runtime_context(tmp_path))

    result = provider.execute()

    assert result["success"] is False
    assert result["status_code"] == 503
    assert result["body_text"] == "service unavailable"
    assert result["body_json"] is None


def test_http_request_provider_raises_when_checked_request_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            502,
            text="gateway timeout while talking to upstream",
            headers={"content-type": "text/plain"},
            request=request,
        )

    transport = httpx.MockTransport(handler)
    _install_mock_client(monkeypatch, transport)

    provider = HttpRequestEffectProvider()
    provider.initialize(_effect_definition(), _runtime_context(tmp_path))

    with pytest.raises(
        RuntimeError,
        match=(
            r"effect:http:request GET https://example\.test/api failed with status 502: "
            r"gateway timeout while talking to upstream"
        ),
    ) as exc_info:
        provider.execute(resolved_config={"check": True})

    outputs = getattr(exc_info.value, "outputs")
    assert outputs["success"] is False
    assert outputs["status_code"] == 502
    assert outputs["body_text"] == "gateway timeout while talking to upstream"
    assert outputs["headers"]["content-type"] == "text/plain"


@pytest.mark.parametrize(
    ("field_name", "resolved_config", "message"),
    [
        (
            "method",
            {"method": "delete"},
            "effect:http:request method must be GET, POST, or PUT",
        ),
        (
            "url",
            {"url": "ftp://example.test/api"},
            "effect:http:request url must be a non-empty http or https URL",
        ),
        (
            "allowed_url_patterns",
            {"allowed_url_patterns": []},
            "effect:http:request allowed_url_patterns must include a pattern",
        ),
    ],
)
def test_http_request_provider_rejects_invalid_runtime_values(
    field_name: str,
    resolved_config: dict[str, object],
    message: str,
    tmp_path: Path,
) -> None:
    del field_name
    provider = HttpRequestEffectProvider()
    provider.initialize(_effect_definition(), _runtime_context(tmp_path))

    with pytest.raises(ValueError, match=message):
        provider.execute(resolved_config=resolved_config)
