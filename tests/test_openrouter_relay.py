"""OpenRouter policy and wire-contract regressions; no live provider requests."""

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from evander.models import SessionManifest
from evander.effects.providers.agent_session import effective_config_for_session
from evander.provider_config import (
    OpenRouterSessionError,
    ProviderConfigResolver,
    ProviderSessionManifest,
)
from evander.relays.openai import OpenAIRelay
from evander.session_auth import session_mcp_auth_metadata
from test_openai_relay import make_config, request_asgi, write_provider_overlay, write_session


MODEL = "openai/gpt-5.4"


@pytest.fixture
def router_config(tmp_path, monkeypatch):
    config = make_config(tmp_path)
    write_provider_overlay(
        config,
        {
            "model_provider_mode": "openrouter",
            "openrouter_model_routes": {MODEL: "azure/eu"},
        },
    )
    write_session(config, session_id="router", model=MODEL)
    monkeypatch.setenv("OPENROUTER_API_KEY", "host-only-router-key")
    return config


@pytest.mark.parametrize("endpoint", ["responses", "responses/compact"])
def test_router_stream_preserves_bytes_and_terminal_usage(router_config, endpoint):
    metadata = {
        "requested": MODEL,
        "strategy": "direct",
        "region": "ams",
        "attempt": 1,
        "is_byok": True,
        "endpoints": {"available": [{"provider": "Azure", "model": MODEL, "selected": True}]},
        "attempts": [{"provider": "Azure", "model": MODEL, "status": 200}],
        "pipeline": [{"data": {"prompt": "private-content"}}],
    }
    events = (
        'data: {"type":"response.output_text.delta","delta":"private-content"}\n\n'
        + "data: "
        + json.dumps(
            {
                "type": "response.completed",
                "response": {
                    "id": "gen-router",
                    "model": MODEL,
                    "usage": {
                        "input_tokens": 4,
                        "output_tokens": 2,
                        "total_tokens": 6,
                        "cost": 0.003,
                        "cost_details": {"upstream_inference_cost": 0.2},
                    },
                },
                "openrouter_metadata": metadata,
            }
        )
        + "\n\ndata: [DONE]\n\n"
    ).encode()

    class Stream(httpx.AsyncByteStream):
        closed = False

        async def __aiter__(self):
            for offset in range(0, len(events), 13):
                assert not self.closed
                yield events[offset : offset + 13]

        async def aclose(self):
            self.closed = True

    stream = Stream()
    calls = []

    def handler(request):
        calls.append(request)
        assert request.url.path == "/api/v1/" + endpoint
        assert request.url.query == b""
        assert json.loads(request.content)["provider"]["only"] == ["azure/eu"]
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream", "x-generation-id": "gen-router"},
            stream=stream,
        )

    relay = OpenAIRelay(router_config, transport=httpx.MockTransport(handler))
    response = request_asgi(
        relay.create_app(),
        "POST",
        f"/v1/session/router/{endpoint}?provider=attacker",
        json={"model": MODEL, "input": "private-prompt", "stream": True},
    )
    assert response.content == events
    assert len(calls) == 1
    assert stream.closed
    [event] = relay.model_usage_store.list_events()
    assert event.provider == "openrouter"
    assert event.request_id == "gen-router"
    assert event.total_tokens == 6
    assert event.raw_usage_metadata["usage"]["cost"] == 0.003
    routing = event.raw_usage_metadata["response_metadata"]["openrouter"]
    assert routing["selected_provider"] == "Azure"
    assert routing["selected_model"] == MODEL
    assert routing["attempt"] == 1
    assert routing["is_byok"] is True
    assert routing["attempts"] == metadata["attempts"]
    assert "pipeline" not in routing
    assert "private-content" not in event.model_dump_json()
    assert "private-prompt" not in event.model_dump_json()


@pytest.mark.parametrize(
    "endpoint",
    [
        "chat/completions",
        "embeddings",
        "messages",
        "images/generations",
        "files",
        "presets/test/responses",
    ],
)
def test_router_rejects_out_of_scope_endpoints(router_config, endpoint):
    def handler(_):
        pytest.fail("out-of-scope request reached upstream")

    relay = OpenAIRelay(router_config, transport=httpx.MockTransport(handler))
    response = request_asgi(
        relay.create_app(), "POST", f"/v1/session/router/{endpoint}", json={"model": MODEL}
    )
    assert response.status_code in {403, 501}


@pytest.mark.parametrize(
    "payload",
    [
        {},
        [],
        {"model": None},
        {"model": [MODEL]},
        {"model": MODEL + " "},
        {"model": MODEL, "preset": "unsafe"},
        {
            "model": MODEL,
            "tools": [{"type": "namespace", "tools": [{"type": "openrouter:web_fetch"}]}],
        },
    ],
)
def test_router_rejects_invalid_requests_before_upstream(router_config, payload):
    def handler(_):
        pytest.fail("invalid request reached upstream")

    relay = OpenAIRelay(router_config, transport=httpx.MockTransport(handler))
    response = request_asgi(
        relay.create_app(), "POST", "/v1/session/router/responses", json=payload
    )
    assert response.status_code == 400


@pytest.mark.parametrize(
    "tool,enabled,status",
    [
        ("web_search", False, 403),
        ("image_generation", False, 403),
        ("web_search", True, 200),
        ("image_generation", True, 200),
        ("openrouter:web_search", True, 400),
    ],
)
def test_router_preserves_session_tool_gates(router_config, tool, enabled, status):
    write_session(
        router_config,
        session_id="router",
        model=MODEL,
        web_search_enabled=enabled,
        image_generation_enabled=enabled,
    )
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"id": "test"})

    relay = OpenAIRelay(router_config, transport=httpx.MockTransport(handler))
    response = request_asgi(
        relay.create_app(),
        "POST",
        "/v1/session/router/responses",
        json={"model": MODEL, "tools": [{"type": tool}]},
    )
    assert response.status_code == status
    assert len(calls) == (1 if status == 200 else 0)


@pytest.mark.parametrize("status", [400, 404, 429, 503])
def test_router_preserves_upstream_errors_without_fallback(router_config, status):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status, headers={"retry-after": "2"}, json={"error": "unavailable"})

    relay = OpenAIRelay(router_config, transport=httpx.MockTransport(handler))
    response = request_asgi(
        relay.create_app(),
        "POST",
        "/v1/session/router/responses/compact",
        json={"model": MODEL, "input": []},
    )
    assert response.status_code == status
    assert response.json() == {"error": "unavailable"}
    assert response.headers["retry-after"] == "2"
    assert len(calls) == 1
    [event] = relay.model_usage_store.list_events()
    assert event.request_status != "success"


def test_router_requires_host_key_and_valid_session_auth(router_config, monkeypatch):
    token = "router-session-token"
    write_session(
        router_config,
        session_id="router",
        model=MODEL,
        metadata={"mcp_session_auth": session_mcp_auth_metadata(token)},
    )
    relay = OpenAIRelay(
        router_config,
        transport=httpx.MockTransport(
            lambda _: pytest.fail("invalid credentials reached upstream")
        ),
    )
    app = relay.create_app()
    response = request_asgi(app, "POST", "/v1/session/router/responses", json={"model": MODEL})
    assert response.status_code == 401
    monkeypatch.delenv("OPENROUTER_API_KEY")
    response = request_asgi(
        app,
        "POST",
        "/v1/session/router/responses",
        headers={"authorization": "Bearer " + token},
        json={"model": MODEL},
    )
    assert response.status_code == 503


def test_router_rejects_websocket_before_upstream(router_config):
    relay = OpenAIRelay(router_config)
    with TestClient(relay.create_app()) as client:
        with pytest.raises(WebSocketDisconnect) as failure:
            with client.websocket_connect("/v1/session/router/responses"):
                pytest.fail("OpenRouter websocket was accepted")
    assert failure.value.code == 1008


@pytest.mark.parametrize("model", [None, "other/model"])
def test_router_resolution_rejects_missing_or_unapproved_model(router_config, model):
    resolver = ProviderConfigResolver(router_config)
    session = ProviderSessionManifest(session_id="router", model=model)
    with pytest.raises(ValueError, match="explicit session model|openrouter_model_routes"):
        resolver.resolve_for_session_manifest(session)


@pytest.mark.parametrize(
    "metadata,auth,error",
    [
        ({"agent_provider": "claude", "model_provider_mode": "openrouter"}, None, "Codex"),
        ({}, "chatgpt_subscription", "metered_api"),
    ],
)
def test_router_rejects_incompatible_harness_or_auth(router_config, metadata, auth, error):
    session = SessionManifest(
        session_id="router",
        allowed_tools=[],
        model=MODEL,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        metadata=metadata,
        codex_auth_mode=auth,
    )
    with pytest.raises(ValueError, match=error):
        ProviderConfigResolver(router_config).resolve_for_session_manifest(session)


def test_router_model_listing_cannot_bypass_filter_with_sse_accept(router_config):
    relay = OpenAIRelay(
        router_config,
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json={"data": [{"id": MODEL}, {"id": "other/model"}]})
        ),
    )
    response = request_asgi(
        relay.create_app(),
        "GET",
        "/v1/session/router/models",
        headers={"accept": "text/event-stream"},
    )
    assert response.json()["data"] == [{"id": MODEL}]


@pytest.mark.parametrize("payload", [[], {"models": [{"id": "other/model"}]}, "invalid"])
def test_router_rejects_malformed_model_listing(router_config, payload):
    relay = OpenAIRelay(
        router_config, transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))
    )
    response = request_asgi(relay.create_app(), "GET", "/v1/session/router/models")
    assert response.status_code == 502
    assert "other/model" not in response.text


def test_router_session_override_does_not_change_directory_default(router_config):
    write_provider_overlay(
        router_config,
        {
            "model_provider_mode": "openai_public",
            "openrouter_model_routes": {MODEL: "azure/eu"},
        },
    )
    resolver = ProviderConfigResolver(router_config)
    router = ProviderSessionManifest(
        session_id="router", model=MODEL, metadata={"model_provider_mode": "openrouter"}
    )
    assert resolver.resolve_for_session_manifest(router).model_provider_mode == "openrouter"
    assert resolver.resolve_for_directory("root").model_provider_mode == "openai_public"
    with pytest.raises(OpenRouterSessionError, match="metered_api"):
        resolver.resolve_for_session_manifest(
            router.model_copy(update={"codex_auth_mode": "chatgpt_subscription"})
        )


def test_router_invalid_session_does_not_fall_back_to_public_effect_config(router_config):
    write_session(router_config, session_id="router", model="unapproved/model")
    context = SimpleNamespace(
        admin_service=SimpleNamespace(
            runtime=SimpleNamespace(provider_config=ProviderConfigResolver(router_config))
        ),
        provider_config=router_config.model_dump(),
    )
    with pytest.raises(OpenRouterSessionError, match="openrouter_model_routes"):
        effective_config_for_session(context, "router")


@pytest.mark.parametrize("event_type", ["error", "response.failed"])
def test_router_stream_failure_is_forwarded_and_audited_without_fallback(router_config, event_type):
    content = (
        "data: "
        + json.dumps(
            {
                "type": event_type,
                "error": {"message": "upstream unavailable"},
            }
        )
        + "\n\ndata: [DONE]\n\n"
    ).encode()
    calls = []

    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield content

    def handler(request):
        calls.append(request)
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=Stream())

    relay = OpenAIRelay(router_config, transport=httpx.MockTransport(handler))
    response = request_asgi(
        relay.create_app(),
        "POST",
        "/v1/session/router/responses",
        json={"model": MODEL, "stream": True},
    )
    assert response.content == content
    assert len(calls) == 1
    [event] = relay.model_usage_store.list_events()
    assert event.request_status == "failed"
