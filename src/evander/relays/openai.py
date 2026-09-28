from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import tempfile
import time
from hashlib import sha256
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote, urlencode

import httpx
import anyio
from anyio import CancelScope
import uvicorn
from python_multipart.multipart import (
    MultipartParseError,
    MultipartParser,
    parse_options_header,
)
from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocket, WebSocketDisconnect

from evander.bootstrap import load_runtime_gateway_config, resolve_runtime_dir
from evander.config import GatewayConfig
from evander.model_usage.runtime import (
    model_usage_store,
)
from evander.operator_steering import OperatorSteeringNote, operator_steering_note_store
from evander.provider_config import ProviderConfigResolver
from evander.privacy.providers import (
    PrivacyDispatchDenied,
    ProviderDispatchGuard,
    config_provider_guard,
)
from evander.relays.local_openai_tool_compatibility import (
    LocalOpenAIToolCompatibility,
    LocalOpenAIToolCompatibilityAdapter,
)
from evander.relays.openai_usage import OpenAIUsageAccounting
from evander.relays.openai_standalone_search import (
    prepare_standalone_search_request,
    standalone_search_response,
)
from evander.session_expiry import SessionExpiredError, ensure_session_not_expired
from evander.session_auth import (
    MCP_SESSION_TOKEN_HEADER_NAMES,
    MCP_SESSION_TOKEN_QUERY_PARAM_NAMES,
    verify_mcp_session_token,
)

logger = logging.getLogger(__name__)

MCP_SESSION_TOKEN_REQUIRED_MESSAGE = "MCP session token is required for this session"
_PRIVACY_STREAM_RECHECK_SECONDS = 1.0

HOP_BY_HOP_HEADERS = {
    "authorization",
    "connection",
    "content-encoding",
    "content-length",
    "host",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "x-api-key",
    *MCP_SESSION_TOKEN_HEADER_NAMES,
}

HOSTED_WEB_SEARCH_TOOL_TYPES = {
    "web_search",
    "web_search_preview",
}
IMAGE_GENERATION_TOOL_TYPES = {"image_generation"}
OPENAI_IMAGE_HTTP_ENDPOINTS = {
    ("POST", "images/generations"),
    ("POST", "images/edits"),
}
OPENAI_COMPATIBLE_HTTP_ENDPOINTS = {
    ("GET", "models"),
    ("POST", "responses"),
    ("POST", "responses/compact"),
    ("POST", "chat/completions"),
    ("POST", "completions"),
    ("POST", "embeddings"),
    *OPENAI_IMAGE_HTTP_ENDPOINTS,
}
OPENAI_COMPATIBLE_WEBSOCKET_ENDPOINTS = {
    "responses",
}
OPENROUTER_HTTP_ENDPOINTS = {
    ("GET", "models"),
    ("POST", "responses"),
    ("POST", "responses/compact"),
}
OPENROUTER_FORBIDDEN_REQUEST_FIELDS = frozenset(
    {"models", "route", "provider", "plugins", "preset"}
)
OPENAI_COMPATIBLE_ENDPOINTS_DESCRIPTION = (
    "OpenAI relay currently supports GET /v1/models and POST /v1/responses, "
    "/v1/responses/compact, /v1/chat/completions, /v1/completions, /v1/embeddings, "
    "/v1/images/generations, /v1/images/edits, and /v1/alpha/search"
)
PATH_SEGMENT_SAFE_CHARS = ":@!$&'()*+,;=-._~"
MAX_PATH_DECODE_PASSES = 5
OPENAI_AUTH_PATH_SCOPE_TOKEN_KEY = "evander_openai_mcp_session_token"
MULTIPART_MODEL_CAPTURE_MAX_HEADER_BYTES = 16 * 1024


def _rewrite_openai_auth_path(path: str) -> tuple[str, str] | None:
    prefix = "/v1/session/"
    if not path.startswith(prefix):
        return None
    remainder = path[len(prefix) :]
    session_id, separator, session_remainder = remainder.partition("/")
    if separator != "/" or not session_id or not session_remainder.startswith("auth/"):
        return None
    token_and_path = session_remainder[len("auth/") :]
    token, separator, upstream_path = token_and_path.partition("/")
    if separator != "/" or not token or not upstream_path:
        return None
    return token, f"{prefix}{session_id}/{upstream_path}"


class OpenAIAuthPathTokenMiddleware:
    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") in {"http", "websocket"}:
            rewritten = _rewrite_openai_auth_path(str(scope.get("path") or ""))
            if rewritten is not None:
                token, rewritten_path = rewritten
                scope[OPENAI_AUTH_PATH_SCOPE_TOKEN_KEY] = token
                scope["path"] = rewritten_path
                scope["raw_path"] = rewritten_path.encode("utf-8")
        await self.app(scope, receive, send)


@dataclass(frozen=True)
class RelayRequestContext:
    session_id: str
    model: str | None
    config: GatewayConfig
    web_search_enabled: bool
    image_generation_enabled: bool
    directory_path: str | None = None
    usage_snapshot: dict[str, Any] | None = None


@dataclass(frozen=True)
class OperatorSteeringInjection:
    request_body: bytes
    note: OperatorSteeringNote | None = None


@dataclass(frozen=True)
class OpenAICompatibleUpstream:
    base_url: str
    api_key: str | None
    api_key_source: str
    provider_name: str


class _MultipartModelCapture:
    """Capture only the small multipart `model` field while the request is spooled."""

    def __init__(
        self,
        content_type: str,
        *,
        max_value_bytes: int = 256,
        max_header_bytes: int = MULTIPART_MODEL_CAPTURE_MAX_HEADER_BYTES,
    ) -> None:
        _media_type, options = parse_options_header(content_type)
        boundary = options.get(b"boundary")
        if not boundary:
            raise ValueError("Multipart image edit request is missing a boundary")
        self.max_value_bytes = max_value_bytes
        self.max_header_bytes = max_header_bytes
        self.part_header_bytes = 0
        self.header_field = bytearray()
        self.header_value = bytearray()
        self.content_disposition = b""
        self.capture_part = False
        self.value = bytearray()
        self.model: str | None = None
        self.parser = MultipartParser(
            boundary,
            {
                "on_part_begin": self._on_part_begin,
                "on_part_data": self._on_part_data,
                "on_part_end": self._on_part_end,
                "on_header_begin": self._on_header_begin,
                "on_header_field": self._on_header_field,
                "on_header_value": self._on_header_value,
                "on_header_end": self._on_header_end,
                "on_headers_finished": self._on_headers_finished,
            },
        )

    def write(self, chunk: bytes) -> None:
        self.parser.write(chunk)

    def finalize(self) -> None:
        self.parser.finalize()

    def _on_part_begin(self) -> None:
        self.part_header_bytes = 0
        self.content_disposition = b""
        self.capture_part = False
        self.value = bytearray()

    def _on_header_begin(self) -> None:
        self.header_field = bytearray()
        self.header_value = bytearray()

    def _on_header_field(self, data: bytes, start: int, end: int) -> None:
        value = data[start:end]
        self._count_header_bytes(len(value))
        self.header_field.extend(value)

    def _on_header_value(self, data: bytes, start: int, end: int) -> None:
        value = data[start:end]
        self._count_header_bytes(len(value))
        if bytes(self.header_field).lower() == b"content-disposition":
            self.header_value.extend(value)

    def _on_header_end(self) -> None:
        if bytes(self.header_field).lower() == b"content-disposition":
            self.content_disposition = bytes(self.header_value)
        self.header_field = bytearray()
        self.header_value = bytearray()

    def _on_headers_finished(self) -> None:
        _value, options = parse_options_header(self.content_disposition)
        self.capture_part = options.get(b"name") == b"model"

    def _count_header_bytes(self, count: int) -> None:
        self.part_header_bytes += count
        if self.part_header_bytes > self.max_header_bytes:
            raise MultipartParseError(
                f"Multipart part headers exceed the relay limit of {self.max_header_bytes} bytes"
            )

    def _on_part_data(self, data: bytes, start: int, end: int) -> None:
        if not self.capture_part or len(self.value) >= self.max_value_bytes:
            return
        remaining = self.max_value_bytes - len(self.value)
        self.value.extend(data[start : min(end, start + remaining)])

    def _on_part_end(self) -> None:
        if not self.capture_part:
            return
        text = self.value.decode("utf-8", errors="ignore").strip()
        self.model = text or None


class OpenAIRelay(OpenAIUsageAccounting):
    def __init__(
        self,
        config: GatewayConfig,
        transport: httpx.AsyncBaseTransport | None = None,
        *,
        provider_config_resolver: ProviderConfigResolver | None = None,
        privacy_guard: ProviderDispatchGuard | None = None,
    ) -> None:
        self.config = config
        self.transport = transport
        self.provider_config_resolver = provider_config_resolver or ProviderConfigResolver(config)
        self.privacy_guard = privacy_guard or config_provider_guard(config)
        self._local_openai_tool_adapter = LocalOpenAIToolCompatibilityAdapter()
        self.model_usage_store = model_usage_store(config.runtime_dir)
        self.operator_steering_store = operator_steering_note_store(config.runtime_dir)
        self.stream_started_count = 0
        self.stream_clean_end_count = 0
        self.stream_transport_terminated_count = 0

    async def _proxy_standalone_web_search(
        self, request: Request, context: RelayRequestContext
    ) -> Response:
        if not context.web_search_enabled:
            self._log_hosted_web_search_denied(
                session_id=context.session_id,
                request_kind="http",
                path=request.url.path,
            )
            return JSONResponse(
                {"error": "Standalone web search is disabled for this session"},
                status_code=403,
            )
        if context.config.model_provider_mode != "openai_public":
            return JSONResponse(
                {"error": "Standalone web search requires the public OpenAI provider"},
                status_code=501,
            )

        try:
            payload = json.loads((await request.body()).decode("utf-8"))
            upstream_payload = prepare_standalone_search_request(payload)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return JSONResponse(
                {"error": "Standalone web search requires valid JSON"}, status_code=400
            )
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

        upstream = self._openai_compatible_upstream(context.config)
        if not upstream.api_key:
            return JSONResponse(
                {"error": f"Host environment variable {upstream.api_key_source} is not set."},
                status_code=503,
            )

        upstream_body = json.dumps(upstream_payload, separators=(",", ":")).encode("utf-8")
        upstream_url = self._upstream_http_url(
            base_url=upstream.base_url,
            upstream_path="responses",
            query_items=[],
        )
        async with httpx.AsyncClient(
            timeout=context.config.openai_relay_timeout_seconds,
            transport=self.transport,
            trust_env=False,
            follow_redirects=False,
            event_hooks={"request": [self._privacy_request_hook(context)]},
        ) as client:
            started_at = time.perf_counter()
            upstream_response = await client.post(
                upstream_url,
                headers={
                    "authorization": f"Bearer {upstream.api_key}",
                    "content-type": "application/json",
                    "accept-encoding": "identity",
                },
                content=upstream_body,
            )
            try:
                await run_in_threadpool(
                    self._record_model_usage_event,
                    usage_snapshot=context.usage_snapshot if context else None,
                    session_id=context.session_id,
                    upstream_path="responses",
                    request_body=upstream_body,
                    upstream_response=upstream_response,
                    duration_ms=self._duration_ms_since(started_at),
                    provider_name=upstream.provider_name,
                )
            except Exception as exc:
                logger.warning(
                    "relay_http_model_usage_record_failed session_id=%s path=%s exception_class=%s exception_message=%s",
                    context.session_id,
                    request.url.path,
                    exc.__class__.__name__,
                    str(exc),
                )

        if upstream_response.is_error:
            return Response(
                content=upstream_response.content,
                status_code=upstream_response.status_code,
                headers=self._response_headers(upstream_response),
                media_type=upstream_response.headers.get("content-type"),
            )
        try:
            response_payload = standalone_search_response(upstream_response.json())
        except (ValueError, json.JSONDecodeError):
            return JSONResponse(
                {"error": "Upstream response did not contain a completed web search"},
                status_code=502,
            )
        return JSONResponse(response_payload)

    def create_app(self) -> Starlette:
        relay = self

        async def health(_: Request) -> JSONResponse:
            api_key_env = relay._resolved_api_key_env(relay.config)
            has_api_key = relay._resolved_api_key_loaded(relay.config)
            return JSONResponse(
                {
                    "status": "ok",
                    "provider_mode": relay.config.model_provider_mode,
                    "upstream": relay._resolved_upstream_base_url(relay.config),
                    "openai_upstream": relay.config.openai_api_base_url,
                    "openrouter_upstream": relay.config.openrouter_api_base_url,
                    "local_openai_upstream": relay.config.local_openai_api_base_url,
                    "anthropic_upstream": relay.config.anthropic_api_base_url,
                    "api_key_env": api_key_env,
                    "api_key_loaded": has_api_key,
                }
            )

        async def proxy(request: Request) -> Response:
            try:
                request_context = await run_in_threadpool(relay._resolve_request_context, request)
            except SessionExpiredError as exc:
                return JSONResponse({"error": str(exc)}, status_code=403)
            except PrivacyDispatchDenied as exc:
                return JSONResponse({"error": str(exc)}, status_code=403)
            except PermissionError as exc:
                return JSONResponse({"error": str(exc)}, status_code=401)
            except ValueError as exc:
                return JSONResponse({"error": str(exc)}, status_code=400)

            upstream_path = request.path_params["path"]
            try:
                canonical_upstream_path = _canonical_relay_path(
                    upstream_path,
                    field_name="OpenAI relay path",
                )
            except ValueError:
                canonical_upstream_path = ""
            if canonical_upstream_path == "alpha/search" and request.method == "POST":
                return await relay._proxy_standalone_web_search(request, request_context)
            is_direct_image_request = (
                request.method.upper(),
                canonical_upstream_path,
            ) in OPENAI_IMAGE_HTTP_ENDPOINTS
            if is_direct_image_request and not request_context.image_generation_enabled:
                relay._log_image_generation_denied(
                    session_id=request_context.session_id,
                    request_kind="http",
                    path=request.url.path,
                )
                return relay._image_generation_disabled_response()
            if is_direct_image_request:
                return await relay._proxy_openai_image_http(
                    request=request,
                    request_context=request_context,
                    upstream_path=canonical_upstream_path,
                )

            request_body = await request.body()
            if (
                not request_context.web_search_enabled
                and relay._request_body_declares_hosted_web_search(request_body)
            ):
                relay._log_hosted_web_search_denied(
                    session_id=request_context.session_id,
                    request_kind="http",
                    path=request.url.path,
                )
                return JSONResponse(
                    {
                        "error": (
                            "Hosted web search is disabled for this session. "
                            "Set session.web_search_enabled=true to allow Responses web_search tools."
                        )
                    },
                    status_code=403,
                )
            if (
                not request_context.image_generation_enabled
                and relay._request_body_declares_image_generation(request_body)
            ):
                relay._log_image_generation_denied(
                    session_id=request_context.session_id,
                    request_kind="http",
                    path=request.url.path,
                )
                return relay._image_generation_disabled_response()

            if request_context.config.model_provider_mode == "openrouter":
                return await relay._proxy_openrouter_http(
                    request=request,
                    request_context=request_context,
                    upstream_path=upstream_path,
                    request_body=request_body,
                )

            if request_context.config.model_provider_mode == "anthropic":
                return await relay._proxy_anthropic_http(
                    request=request,
                    request_context=request_context,
                    upstream_path=upstream_path,
                    request_body=request_body,
                )

            if request_context.config.model_provider_mode == "azure_openai":
                if request.method != "POST" or upstream_path != "responses":
                    return JSONResponse(
                        {
                            "error": (
                                "Azure OpenAI relay currently supports only POST /v1/responses"
                            )
                        },
                        status_code=501,
                    )

                api_key_env = request_context.config.azure_openai_api_key_env
                api_key = os.environ.get(api_key_env) if api_key_env else None
                if not api_key:
                    return JSONResponse(
                        {"error": (f"Host environment variable {api_key_env} is not set.")},
                        status_code=503,
                    )

                try:
                    request_body = relay._rewrite_azure_openai_request_body(
                        request_body,
                        model_deployments=request_context.config.azure_openai_model_deployments,
                    )
                except ValueError as exc:
                    return JSONResponse({"error": str(exc)}, status_code=400)
                steering_injection = await run_in_threadpool(
                    relay._operator_steering_injection,
                    session_id=request_context.session_id,
                    method=request.method,
                    provider_name="openai",
                    upstream_path=upstream_path,
                    request_body=request_body,
                )
                request_body = steering_injection.request_body

                upstream_url = relay._azure_upstream_http_url(
                    endpoint=request_context.config.azure_openai_endpoint or "",
                    upstream_path=upstream_path,
                    query_items=request.query_params.multi_items(),
                    api_version=request_context.config.azure_openai_api_version,
                )
                headers = {
                    key: value
                    for key, value in request.headers.items()
                    if key.lower() not in HOP_BY_HOP_HEADERS
                }
                headers["accept-encoding"] = "identity"
                headers["api-key"] = api_key

                if "text/event-stream" in request.headers.get("accept", ""):
                    return await relay._stream_response(
                        request=request,
                        session_id=request_context.session_id,
                        upstream_path=upstream_path,
                        upstream_url=upstream_url,
                        headers=headers,
                        request_body=request_body,
                        timeout_seconds=request_context.config.openai_relay_timeout_seconds,
                        provider_name="openai",
                        operator_steering_note=steering_injection.note,
                        privacy_context=request_context,
                    )

                async with httpx.AsyncClient(
                    timeout=request_context.config.openai_relay_timeout_seconds,
                    transport=relay.transport,
                    trust_env=False,
                    follow_redirects=False,
                    event_hooks={"request": [relay._privacy_request_hook(request_context)]},
                ) as client:
                    started_at = time.perf_counter()
                    try:
                        upstream_response = await client.request(
                            method=request.method,
                            url=upstream_url,
                            headers=headers,
                            content=request_body,
                        )
                    except Exception as exc:
                        await run_in_threadpool(
                            relay._release_operator_steering_note,
                            steering_injection.note,
                            error=f"upstream request failed before delivery: {exc}",
                        )
                        raise
                    await run_in_threadpool(
                        relay._mark_operator_steering_delivered,
                        steering_injection.note,
                        provider_name="openai",
                        provider_endpoint=upstream_path,
                    )
                    try:
                        await run_in_threadpool(
                            relay._record_model_usage_event,
                            usage_snapshot=request_context.usage_snapshot
                            if request_context
                            else None,
                            session_id=request_context.session_id,
                            upstream_path=upstream_path,
                            request_body=request_body,
                            upstream_response=upstream_response,
                            duration_ms=relay._duration_ms_since(started_at),
                            provider_name="openai",
                        )
                    except Exception as exc:
                        logger.warning(
                            "relay_http_model_usage_record_failed session_id=%s path=%s exception_class=%s exception_message=%s",
                            request_context.session_id,
                            request.url.path,
                            exc.__class__.__name__,
                            str(exc),
                        )
                    return Response(
                        content=upstream_response.content,
                        status_code=upstream_response.status_code,
                        headers=relay._response_headers(upstream_response),
                        media_type=upstream_response.headers.get("content-type"),
                    )

            try:
                upstream_path = relay._supported_openai_compatible_http_path(
                    method=request.method,
                    upstream_path=upstream_path,
                )
            except ValueError as exc:
                return JSONResponse({"error": str(exc)}, status_code=501)

            openai_compatible_upstream = relay._openai_compatible_upstream(request_context.config)
            if not openai_compatible_upstream.api_key:
                return JSONResponse(
                    {
                        "error": (
                            "Host environment variable "
                            f"{openai_compatible_upstream.api_key_source} is not set."
                        )
                    },
                    status_code=503,
                )

            upstream_url = relay._upstream_http_url(
                base_url=openai_compatible_upstream.base_url,
                upstream_path=upstream_path,
                query_items=request.query_params.multi_items(),
            )
            headers = {
                key: value
                for key, value in request.headers.items()
                if key.lower() not in HOP_BY_HOP_HEADERS
            }
            headers["accept-encoding"] = "identity"
            headers["authorization"] = f"Bearer {openai_compatible_upstream.api_key}"
            steering_injection = await run_in_threadpool(
                relay._operator_steering_injection,
                session_id=request_context.session_id,
                method=request.method,
                provider_name=openai_compatible_upstream.provider_name,
                upstream_path=upstream_path,
                request_body=request_body,
            )
            request_body = steering_injection.request_body
            local_openai_tool_compatibility: LocalOpenAIToolCompatibility | None = None
            if request_context.config.model_provider_mode == "local_openai":
                local_openai_tool_compatibility = relay._local_openai_tool_adapter.prepare_request(
                    method=request.method,
                    upstream_path=upstream_path,
                    request_body=request_body,
                )
                request_body = local_openai_tool_compatibility.request_body

            if "text/event-stream" in request.headers.get("accept", ""):
                return await relay._stream_response(
                    request=request,
                    session_id=request_context.session_id,
                    upstream_path=upstream_path,
                    upstream_url=upstream_url,
                    headers=headers,
                    request_body=request_body,
                    timeout_seconds=request_context.config.openai_relay_timeout_seconds,
                    provider_name=openai_compatible_upstream.provider_name,
                    operator_steering_note=steering_injection.note,
                    local_openai_tool_compatibility=local_openai_tool_compatibility,
                    privacy_context=request_context,
                )

            async with httpx.AsyncClient(
                timeout=request_context.config.openai_relay_timeout_seconds,
                transport=relay.transport,
                trust_env=False,
                follow_redirects=False,
                event_hooks={"request": [relay._privacy_request_hook(request_context)]},
            ) as client:
                started_at = time.perf_counter()
                try:
                    upstream_response = await client.request(
                        method=request.method,
                        url=upstream_url,
                        headers=headers,
                        content=request_body,
                    )
                except Exception as exc:
                    await run_in_threadpool(
                        relay._release_operator_steering_note,
                        steering_injection.note,
                        error=f"upstream request failed before delivery: {exc}",
                    )
                    raise
                await run_in_threadpool(
                    relay._mark_operator_steering_delivered,
                    steering_injection.note,
                    provider_name=openai_compatible_upstream.provider_name,
                    provider_endpoint=upstream_path,
                )
                try:
                    await run_in_threadpool(
                        relay._record_model_usage_event,
                        usage_snapshot=request_context.usage_snapshot if request_context else None,
                        session_id=request_context.session_id,
                        upstream_path=upstream_path,
                        request_body=request_body,
                        upstream_response=upstream_response,
                        duration_ms=relay._duration_ms_since(started_at),
                        provider_name=openai_compatible_upstream.provider_name,
                    )
                except Exception as exc:
                    logger.warning(
                        "relay_http_model_usage_record_failed session_id=%s path=%s exception_class=%s exception_message=%s",
                        request_context.session_id,
                        request.url.path,
                        exc.__class__.__name__,
                        str(exc),
                    )
                response_content = upstream_response.content
                if local_openai_tool_compatibility is not None:
                    response_content = relay._local_openai_tool_adapter.rewrite_response(
                        response_content,
                        compatibility=local_openai_tool_compatibility,
                    )
                return Response(
                    content=response_content,
                    status_code=upstream_response.status_code,
                    headers=relay._response_headers(upstream_response),
                    media_type=upstream_response.headers.get("content-type"),
                )

        async def websocket_proxy(websocket: WebSocket) -> None:
            try:
                request_context = await run_in_threadpool(
                    relay._resolve_websocket_context, websocket
                )
            except SessionExpiredError as exc:
                await websocket.close(code=1008, reason=str(exc))
                return
            except PermissionError as exc:
                await websocket.close(code=1008, reason=str(exc))
                return
            except ValueError as exc:
                await websocket.close(code=1008, reason=str(exc))
                return

            if request_context.config.model_provider_mode == "azure_openai":
                reason = (
                    "Azure OpenAI websocket/realtime relay is not supported by this provider mode"
                )
                logger.warning(
                    "relay_rejected_azure_websocket session_id=%s path=%s reason=%s",
                    request_context.session_id,
                    websocket.url.path,
                    reason,
                )
                await websocket.close(code=1008, reason=reason)
                return
            if request_context.config.model_provider_mode == "openrouter":
                reason = "OpenRouter relay does not support websocket/realtime requests"
                logger.warning(
                    "relay_rejected_openrouter_websocket session_id=%s path=%s reason=%s",
                    request_context.session_id,
                    websocket.url.path,
                    reason,
                )
                await websocket.close(code=1008, reason=reason)
                return
            if request_context.config.model_provider_mode == "anthropic":
                reason = "Anthropic relay does not support websocket/realtime requests"
                logger.warning(
                    "relay_rejected_anthropic_websocket session_id=%s path=%s reason=%s",
                    request_context.session_id,
                    websocket.url.path,
                    reason,
                )
                await websocket.close(code=1008, reason=reason)
                return
            if request_context.config.model_provider_mode == "local_openai":
                reason = (
                    "Local OpenAI-compatible relay does not support websocket/realtime requests"
                )
                logger.warning(
                    "relay_rejected_local_openai_websocket session_id=%s path=%s reason=%s",
                    request_context.session_id,
                    websocket.url.path,
                    reason,
                )
                await websocket.close(code=1008, reason=reason)
                return

            upstream_path = websocket.path_params["path"]
            try:
                upstream_path = relay._supported_openai_compatible_websocket_path(
                    upstream_path=upstream_path,
                )
            except ValueError as exc:
                await websocket.close(code=1008, reason=str(exc))
                return

            openai_compatible_upstream = relay._openai_compatible_upstream(request_context.config)
            if not openai_compatible_upstream.api_key:
                await websocket.close(code=1011, reason="missing upstream API key")
                return

            upstream_url = relay._upstream_websocket_url(
                base_url=openai_compatible_upstream.base_url,
                upstream_path=upstream_path,
                query_items=websocket.query_params.multi_items(),
            )
            headers = {
                key: value
                for key, value in websocket.headers.items()
                if key.lower() not in HOP_BY_HOP_HEADERS
                and not key.lower().startswith("sec-websocket-")
            }
            headers["authorization"] = f"Bearer {openai_compatible_upstream.api_key}"

            try:
                relay._require_privacy(request_context)
            except PrivacyDispatchDenied as exc:
                await websocket.close(code=1008, reason=str(exc))
                return
            async with relay._connect_upstream_websocket(
                upstream_url,
                headers=headers,
                timeout_seconds=request_context.config.openai_relay_timeout_seconds,
            ) as upstream:
                await websocket.accept()
                await relay._proxy_websocket_traffic(
                    websocket,
                    upstream,
                    session_id=request_context.session_id,
                    upstream_path=upstream_path,
                    path=websocket.url.path,
                    web_search_enabled=request_context.web_search_enabled,
                    image_generation_enabled=request_context.image_generation_enabled,
                    provider_name=openai_compatible_upstream.provider_name,
                    privacy_context=request_context,
                )

        async def privacy_denied(_request: Request, exc: PrivacyDispatchDenied) -> JSONResponse:
            return JSONResponse({"error": str(exc)}, status_code=403)

        return Starlette(
            debug=False,
            exception_handlers={PrivacyDispatchDenied: privacy_denied},
            middleware=[Middleware(OpenAIAuthPathTokenMiddleware)],
            routes=[
                Route("/healthz", health, methods=["GET"]),
                Route(
                    "/v1/session/{session_id}/{path:path}",
                    proxy,
                    methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
                ),
                Route(
                    "/session/{session_id}/v1/{path:path}",
                    proxy,
                    methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
                ),
                Route("/v1/{path:path}", proxy, methods=["GET", "POST", "PUT", "PATCH", "DELETE"]),
                WebSocketRoute("/v1/session/{session_id}/{path:path}", websocket_proxy),
                WebSocketRoute("/v1/{path:path}", websocket_proxy),
            ],
        )

    async def _proxy_openrouter_http(
        self,
        *,
        request: Request,
        request_context: RelayRequestContext,
        upstream_path: str,
        request_body: bytes,
    ) -> Response:
        try:
            normalized_path = _canonical_relay_path(
                upstream_path,
                field_name="OpenRouter relay path",
            )
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        if (request.method.upper(), normalized_path) not in OPENROUTER_HTTP_ENDPOINTS:
            return JSONResponse(
                {
                    "error": (
                        "OpenRouter relay supports only GET /v1/models, "
                        "POST /v1/responses, and POST /v1/responses/compact"
                    )
                },
                status_code=501,
            )

        api_key_env = request_context.config.openrouter_api_key_env
        api_key = os.environ.get(api_key_env)
        if not api_key:
            return JSONResponse(
                {"error": f"Host environment variable {api_key_env} is not set."},
                status_code=503,
            )

        if request.method.upper() == "POST":
            try:
                request_body = self._rewrite_openrouter_request_body(
                    request_body,
                    session_model=request_context.model,
                    model_routes=request_context.config.openrouter_model_routes,
                )
            except ValueError as exc:
                return JSONResponse({"error": str(exc)}, status_code=400)

        upstream_url = self._upstream_http_url(
            base_url=request_context.config.openrouter_api_base_url,
            upstream_path=normalized_path,
            query_items=[],
        )
        headers = {
            key: value
            for key, value in request.headers.items()
            if key.lower() not in HOP_BY_HOP_HEADERS
            and not key.lower().startswith("x-openrouter-")
            and key.lower() not in {"api-key", "cookie"}
        }
        headers["accept-encoding"] = "identity"
        headers["authorization"] = f"Bearer {api_key}"
        headers["x-openrouter-metadata"] = "enabled"

        steering_injection = await run_in_threadpool(
            self._operator_steering_injection,
            session_id=request_context.session_id,
            method=request.method,
            provider_name="openrouter",
            upstream_path=normalized_path,
            request_body=request_body,
        )
        request_body = steering_injection.request_body
        if request.method.upper() == "POST" and self._should_stream_http_response(
            request=request, request_body=request_body
        ):
            return await self._stream_response(
                request=request,
                session_id=request_context.session_id,
                upstream_path=normalized_path,
                upstream_url=upstream_url,
                headers=headers,
                request_body=request_body,
                timeout_seconds=request_context.config.openai_relay_timeout_seconds,
                provider_name="openrouter",
                operator_steering_note=steering_injection.note,
                privacy_context=request_context,
            )

        async with httpx.AsyncClient(
            timeout=request_context.config.openai_relay_timeout_seconds,
            transport=self.transport,
            trust_env=False,
            follow_redirects=False,
            event_hooks={"request": [self._privacy_request_hook(request_context)]},
        ) as client:
            started_at = time.perf_counter()
            try:
                upstream_response = await client.request(
                    method=request.method,
                    url=upstream_url,
                    headers=headers,
                    content=request_body,
                )
            except Exception as exc:
                await run_in_threadpool(
                    self._release_operator_steering_note,
                    steering_injection.note,
                    error=f"upstream request failed before delivery: {exc}",
                )
                raise
            await run_in_threadpool(
                self._mark_operator_steering_delivered,
                steering_injection.note,
                provider_name="openrouter",
                provider_endpoint=normalized_path,
            )
            response_content = upstream_response.content
            if request.method.upper() == "GET" and normalized_path == "models":
                if upstream_response.is_success:
                    try:
                        response_content = self._filter_openrouter_models_response(
                            response_content,
                            allowed_models=request_context.config.openrouter_model_routes,
                        )
                    except ValueError as exc:
                        return JSONResponse({"error": str(exc)}, status_code=502)
            else:
                try:
                    await run_in_threadpool(
                        self._record_model_usage_event,
                        usage_snapshot=request_context.usage_snapshot if request_context else None,
                        session_id=request_context.session_id,
                        upstream_path=normalized_path,
                        request_body=request_body,
                        upstream_response=upstream_response,
                        duration_ms=self._duration_ms_since(started_at),
                        provider_name="openrouter",
                    )
                except Exception as exc:
                    logger.warning(
                        "relay_http_model_usage_record_failed session_id=%s path=%s exception_class=%s exception_message=%s",
                        request_context.session_id,
                        request.url.path,
                        exc.__class__.__name__,
                        str(exc),
                    )
            return Response(
                content=response_content,
                status_code=upstream_response.status_code,
                headers=self._response_headers(upstream_response),
                media_type=upstream_response.headers.get("content-type"),
            )

    @staticmethod
    def _rewrite_openrouter_request_body(
        request_body: bytes,
        *,
        session_model: str | None,
        model_routes: dict[str, str],
    ) -> bytes:
        try:
            payload = json.loads(request_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("OpenRouter request body must be a JSON object") from exc
        if not isinstance(payload, dict):
            raise ValueError("OpenRouter request body must be a JSON object")

        forbidden = sorted(OPENROUTER_FORBIDDEN_REQUEST_FIELDS.intersection(payload))
        if forbidden:
            raise ValueError(
                "OpenRouter request must not set relay-controlled fields: "
                + ", ".join(forbidden)
            )
        model = payload.get("model")
        expected_model = session_model
        if not isinstance(model, str) or not model:
            raise ValueError("OpenRouter request requires an explicit model")
        if model != expected_model:
            raise ValueError(
                f"OpenRouter request model {model!r} does not match session model "
                f"{expected_model!r}"
            )
        provider = model_routes.get(model)
        if not provider:
            raise ValueError(f"OpenRouter model {model!r} is not approved for this session")
        if _tools_declare_openrouter_server_tool(payload.get("tools")):
            raise ValueError("OpenRouter server tools are not allowed")

        rewritten = dict(payload)
        rewritten["provider"] = {
            "order": [provider],
            "only": [provider],
            "allow_fallbacks": False,
            "require_parameters": True,
            "zdr": True,
        }
        return json.dumps(rewritten, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

    @staticmethod
    def _filter_openrouter_models_response(
        response_content: bytes,
        *,
        allowed_models: dict[str, str],
    ) -> bytes:
        try:
            payload = json.loads(response_content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("OpenRouter returned an invalid model listing") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
            raise ValueError("OpenRouter returned an invalid model listing")
        filtered = dict(payload)
        filtered["data"] = [
            item
            for item in payload["data"]
            if isinstance(item, dict)
            and isinstance(item.get("id"), str)
            and item["id"] in allowed_models
        ]
        return json.dumps(filtered, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

    async def _proxy_openai_image_http(
        self,
        *,
        request: Request,
        request_context: RelayRequestContext,
        upstream_path: str,
    ) -> Response:
        if request_context.config.model_provider_mode in {
            "anthropic",
            "azure_openai",
            "openrouter",
        }:
            return JSONResponse(
                {
                    "error": (
                        "Direct OpenAI image generation and editing require the public "
                        "or local OpenAI-compatible provider mode"
                    )
                },
                status_code=501,
            )

        try:
            upstream_path = self._supported_openai_compatible_http_path(
                method=request.method,
                upstream_path=upstream_path,
            )
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=501)

        max_request_bytes = (
            request_context.config.openai_image_edit_max_request_bytes
            if upstream_path == "images/edits"
            else request_context.config.openai_image_generation_max_request_bytes
        )
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                declared_length = int(content_length)
            except ValueError:
                return JSONResponse({"error": "Invalid Content-Length header"}, status_code=400)
            if declared_length < 0:
                return JSONResponse({"error": "Invalid Content-Length header"}, status_code=400)
            if declared_length > max_request_bytes:
                return self._image_request_too_large_response(max_request_bytes)

        spool = tempfile.SpooledTemporaryFile(max_size=1_048_576, mode="w+b")
        request_size = 0
        request_hasher = sha256()
        request_model: str | None = None
        request_declares_stream = False
        multipart_capture: _MultipartModelCapture | None = None
        if upstream_path == "images/edits":
            content_type = request.headers.get("content-type", "")
            if not content_type.lower().startswith("multipart/form-data"):
                spool.close()
                return JSONResponse(
                    {"error": "Image edit requests must use multipart/form-data"},
                    status_code=400,
                )
            try:
                multipart_capture = _MultipartModelCapture(content_type)
            except ValueError as exc:
                spool.close()
                return JSONResponse({"error": str(exc)}, status_code=400)

        try:
            async for chunk in request.stream():
                request_size += len(chunk)
                if request_size > max_request_bytes:
                    return self._image_request_too_large_response(max_request_bytes)
                request_hasher.update(chunk)
                spool.write(chunk)
                if multipart_capture is not None:
                    try:
                        multipart_capture.write(chunk)
                    except MultipartParseError as exc:
                        return self._malformed_multipart_response(exc)
            if multipart_capture is not None:
                try:
                    multipart_capture.finalize()
                except MultipartParseError as exc:
                    return self._malformed_multipart_response(exc)
                request_model = multipart_capture.model
            spool.seek(0)
            if upstream_path == "images/generations":
                generation_body = spool.read()
                request_model = self._model_from_request_body(generation_body)
                request_declares_stream = self._request_body_declares_stream(generation_body)
                spool.seek(0)

            upstream = self._openai_compatible_upstream(request_context.config)
            if not upstream.api_key:
                return JSONResponse(
                    {"error": (f"Host environment variable {upstream.api_key_source} is not set.")},
                    status_code=503,
                )
            upstream_url = self._upstream_http_url(
                base_url=upstream.base_url,
                upstream_path=upstream_path,
                query_items=request.query_params.multi_items(),
            )
            headers = {
                key: value
                for key, value in request.headers.items()
                if key.lower() not in HOP_BY_HOP_HEADERS
            }
            headers["accept-encoding"] = "identity"
            headers["authorization"] = f"Bearer {upstream.api_key}"
            headers["content-length"] = str(request_size)

            if request_declares_stream:
                return await self._stream_response(
                    request=request,
                    session_id=request_context.session_id,
                    upstream_path=upstream_path,
                    upstream_url=upstream_url,
                    headers=headers,
                    request_body=None,
                    request_content=self._spooled_file_chunks(spool),
                    request_body_bytes=request_size or None,
                    request_body_sha256=(request_hasher.hexdigest() if request_size else None),
                    request_model=request_model,
                    timeout_seconds=request_context.config.openai_relay_timeout_seconds,
                    provider_name=upstream.provider_name,
                    privacy_context=request_context,
                )

            async with httpx.AsyncClient(
                timeout=request_context.config.openai_relay_timeout_seconds,
                transport=self.transport,
                trust_env=False,
                follow_redirects=False,
                event_hooks={"request": [self._privacy_request_hook(request_context)]},
            ) as client:
                started_at = time.perf_counter()
                upstream_response = await client.request(
                    method=request.method,
                    url=upstream_url,
                    headers=headers,
                    content=self._spooled_file_chunks(spool),
                )
                try:
                    await run_in_threadpool(
                        self._record_model_usage_event,
                        usage_snapshot=request_context.usage_snapshot if request_context else None,
                        session_id=request_context.session_id,
                        upstream_path=upstream_path,
                        request_body=None,
                        request_body_bytes=request_size or None,
                        request_body_sha256=(request_hasher.hexdigest() if request_size else None),
                        request_model=request_model,
                        upstream_response=upstream_response,
                        duration_ms=self._duration_ms_since(started_at),
                        provider_name=upstream.provider_name,
                    )
                except Exception as exc:
                    logger.warning(
                        "relay_http_model_usage_record_failed session_id=%s path=%s exception_class=%s exception_message=%s",
                        request_context.session_id,
                        request.url.path,
                        exc.__class__.__name__,
                        str(exc),
                    )
                return Response(
                    content=upstream_response.content,
                    status_code=upstream_response.status_code,
                    headers=self._response_headers(upstream_response),
                    media_type=upstream_response.headers.get("content-type"),
                )
        finally:
            spool.close()

    @staticmethod
    async def _spooled_file_chunks(spool: Any) -> AsyncIterator[bytes]:
        while True:
            chunk = spool.read(64 * 1024)
            if not chunk:
                return
            yield chunk

    @staticmethod
    def _image_request_too_large_response(max_request_bytes: int) -> JSONResponse:
        return JSONResponse(
            {
                "error": (
                    "Image request exceeds the configured session relay limit of "
                    f"{max_request_bytes} bytes"
                )
            },
            status_code=413,
        )

    @staticmethod
    def _malformed_multipart_response(exc: MultipartParseError) -> JSONResponse:
        return JSONResponse(
            {"error": f"Malformed multipart image edit request: {exc}"},
            status_code=400,
        )

    @staticmethod
    def _image_generation_disabled_response() -> JSONResponse:
        return JSONResponse(
            {
                "error": (
                    "Image generation is disabled for this session. Set "
                    "session.image_generation_enabled=true to allow OpenAI image "
                    "generation, editing, and Responses image_generation tools."
                )
            },
            status_code=403,
        )

    async def _proxy_anthropic_http(
        self,
        *,
        request: Request,
        request_context: RelayRequestContext,
        upstream_path: str,
        request_body: bytes,
    ) -> Response:
        normalized_path = self._anthropic_upstream_path(upstream_path)
        if not self._is_supported_anthropic_endpoint(
            method=request.method,
            upstream_path=normalized_path,
        ):
            return JSONResponse(
                {
                    "error": (
                        "Anthropic relay currently supports GET /v1/models, "
                        "POST /v1/messages, and POST /v1/messages/count_tokens"
                    )
                },
                status_code=501,
            )

        api_key = os.environ.get(request_context.config.anthropic_api_key_env)
        if not api_key:
            return JSONResponse(
                {
                    "error": (
                        "Host environment variable "
                        f"{request_context.config.anthropic_api_key_env} is not set."
                    )
                },
                status_code=503,
            )

        upstream_url = self._anthropic_upstream_http_url(
            base_url=request_context.config.anthropic_api_base_url,
            upstream_path=normalized_path,
            query_items=request.query_params.multi_items(),
        )
        headers = {
            key: value
            for key, value in request.headers.items()
            if key.lower() not in HOP_BY_HOP_HEADERS
        }
        headers["accept-encoding"] = "identity"
        headers["x-api-key"] = api_key
        if not any(key.lower() == "anthropic-version" for key in headers):
            headers["anthropic-version"] = request_context.config.anthropic_api_version
        steering_injection = await run_in_threadpool(
            self._operator_steering_injection,
            session_id=request_context.session_id,
            method=request.method,
            provider_name="anthropic",
            upstream_path=normalized_path,
            request_body=request_body,
        )
        request_body = steering_injection.request_body

        if self._should_stream_http_response(request=request, request_body=request_body):
            return await self._stream_response(
                request=request,
                session_id=request_context.session_id,
                upstream_path=normalized_path,
                upstream_url=upstream_url,
                headers=headers,
                request_body=request_body,
                timeout_seconds=request_context.config.anthropic_relay_timeout_seconds,
                provider_name="anthropic",
                operator_steering_note=steering_injection.note,
                privacy_context=request_context,
            )

        async with httpx.AsyncClient(
            timeout=request_context.config.anthropic_relay_timeout_seconds,
            transport=self.transport,
            trust_env=False,
            follow_redirects=False,
            event_hooks={"request": [self._privacy_request_hook(request_context)]},
        ) as client:
            started_at = time.perf_counter()
            try:
                upstream_response = await client.request(
                    method=request.method,
                    url=upstream_url,
                    headers=headers,
                    content=request_body,
                )
            except Exception as exc:
                await run_in_threadpool(
                    self._release_operator_steering_note,
                    steering_injection.note,
                    error=f"upstream request failed before delivery: {exc}",
                )
                raise
            await run_in_threadpool(
                self._mark_operator_steering_delivered,
                steering_injection.note,
                provider_name="anthropic",
                provider_endpoint=normalized_path,
            )
            try:
                await run_in_threadpool(
                    self._record_model_usage_event,
                    usage_snapshot=request_context.usage_snapshot if request_context else None,
                    session_id=request_context.session_id,
                    upstream_path=normalized_path,
                    request_body=request_body,
                    upstream_response=upstream_response,
                    duration_ms=self._duration_ms_since(started_at),
                    provider_name="anthropic",
                )
            except Exception as exc:
                logger.warning(
                    "relay_http_model_usage_record_failed session_id=%s path=%s exception_class=%s exception_message=%s",
                    request_context.session_id,
                    request.url.path,
                    exc.__class__.__name__,
                    str(exc),
                )
            return Response(
                content=upstream_response.content,
                status_code=upstream_response.status_code,
                headers=self._response_headers(upstream_response),
                media_type=upstream_response.headers.get("content-type"),
            )

    async def _stream_response(
        self,
        request: Request,
        session_id: str,
        upstream_path: str,
        upstream_url: str,
        headers: dict[str, str],
        request_body: bytes | None,
        timeout_seconds: float,
        provider_name: str = "openai",
        operator_steering_note: OperatorSteeringNote | None = None,
        local_openai_tool_compatibility: LocalOpenAIToolCompatibility | None = None,
        request_content: bytes | AsyncIterator[bytes] | None = None,
        request_body_bytes: int | None = None,
        request_body_sha256: str | None = None,
        request_model: str | None = None,
        privacy_context: RelayRequestContext | None = None,
    ) -> StreamingResponse:
        self.stream_started_count += 1
        stream_id = self.stream_started_count
        logger.info(
            "relay_stream_request_start stream_id=%s method=%s path=%s upstream=%s stream_started=%s clean_end=%s transport_terminated_end=%s",
            stream_id,
            request.method,
            request.url.path,
            upstream_url,
            self.stream_started_count,
            self.stream_clean_end_count,
            self.stream_transport_terminated_count,
        )

        client = httpx.AsyncClient(
            timeout=timeout_seconds,
            transport=self.transport,
            trust_env=False,
            follow_redirects=False,
            event_hooks={"request": [self._privacy_request_hook(privacy_context)]}
            if privacy_context else None,
        )
        upstream_stream = client.stream(
            method=request.method,
            url=upstream_url,
            headers=headers,
            content=request_body if request_content is None else request_content,
        )
        started_at = time.perf_counter()
        try:
            upstream_response = await upstream_stream.__aenter__()
        except BaseException as exc:
            with CancelScope(shield=True):
                try:
                    await run_in_threadpool(
                        self._release_operator_steering_note,
                        operator_steering_note,
                        error=f"upstream stream failed before delivery: {exc}",
                    )
                finally:
                    await client.aclose()
            raise

        async def close_upstream() -> None:
            with CancelScope(shield=True):
                try:
                    await upstream_stream.__aexit__(None, None, None)
                finally:
                    await client.aclose()

        try:
            await run_in_threadpool(
                self._mark_operator_steering_delivered,
                operator_steering_note,
                provider_name=provider_name,
                provider_endpoint=upstream_path,
            )
        except BaseException:
            # The response iterator does not own the upstream yet.
            await close_upstream()
            raise
        logger.info(
            "relay_stream_upstream_response stream_id=%s status_code=%s content_type=%s",
            stream_id,
            upstream_response.status_code,
            upstream_response.headers.get("content-type", ""),
        )

        async def iterator() -> AsyncIterator[bytes]:
            bytes_sent = 0
            chunk_count = 0
            response_hash = sha256()
            stream_payload: dict[str, Any] = {}
            sse_buffer = ""
            rewrite_buffer = b""
            try:
                try:
                    async for chunk in self._privacy_stream_chunks(
                        upstream_response.aiter_raw(), privacy_context
                    ):
                        chunk_count += 1
                        bytes_sent += len(chunk)
                        response_hash.update(chunk)
                        sse_buffer = self._capture_stream_usage_payload(
                            buffer=sse_buffer,
                            chunk=chunk,
                            payload=stream_payload,
                        )
                        if local_openai_tool_compatibility is None:
                            rewritten, rewrite_buffer = rewrite_buffer + chunk, b""
                        else:
                            rewritten, rewrite_buffer = (
                                self._local_openai_tool_adapter.rewrite_stream_chunk(
                                    buffer=rewrite_buffer,
                                    chunk=chunk,
                                    compatibility=local_openai_tool_compatibility,
                                )
                            )
                        if rewritten:
                            yield rewritten
                    if rewrite_buffer:
                        if local_openai_tool_compatibility is None:
                            yield rewrite_buffer
                        else:
                            yield self._local_openai_tool_adapter.rewrite_sse_line(
                                rewrite_buffer,
                                compatibility=local_openai_tool_compatibility,
                            )
                except PrivacyDispatchDenied:
                    logger.info("privacy_provider_stream_revoked capability=model_relay")
                    return
                except (httpx.ReadError, httpx.RemoteProtocolError) as exc:
                    # Some upstream streaming responses terminate with a transport-level
                    # read error after the final bytes have already been delivered.
                    # Treat that as end-of-stream instead of crashing the relay.
                    if rewrite_buffer:
                        if local_openai_tool_compatibility is None:
                            yield rewrite_buffer
                        else:
                            yield self._local_openai_tool_adapter.rewrite_sse_line(
                                rewrite_buffer,
                                compatibility=local_openai_tool_compatibility,
                            )
                        rewrite_buffer = b""
                    self.stream_transport_terminated_count += 1
                    logger.warning(
                        "relay_stream_transport_terminated stream_id=%s exception_class=%s exception_message=%s chunks=%s bytes=%s stream_started=%s clean_end=%s transport_terminated_end=%s",
                        stream_id,
                        exc.__class__.__name__,
                        str(exc),
                        chunk_count,
                        bytes_sent,
                        self.stream_started_count,
                        self.stream_clean_end_count,
                        self.stream_transport_terminated_count,
                    )
                    return
                self.stream_clean_end_count += 1
                logger.info(
                    "relay_stream_completed stream_id=%s end_reason=clean chunks=%s bytes=%s stream_started=%s clean_end=%s transport_terminated_end=%s",
                    stream_id,
                    chunk_count,
                    bytes_sent,
                    self.stream_started_count,
                    self.stream_clean_end_count,
                    self.stream_transport_terminated_count,
                )
            finally:
                with CancelScope(shield=True):
                    try:
                        await run_in_threadpool(
                            self._record_stream_model_usage_event,
                            usage_snapshot=privacy_context.usage_snapshot
                            if privacy_context
                            else None,
                            session_id=session_id,
                            upstream_path=upstream_path,
                            request_body=request_body,
                            upstream_response=upstream_response,
                            response_body_bytes=bytes_sent,
                            response_body_sha256=response_hash.hexdigest() if bytes_sent else None,
                            response_payload=stream_payload,
                            duration_ms=self._duration_ms_since(started_at),
                            provider_name=provider_name,
                            request_body_bytes=request_body_bytes,
                            request_body_sha256=request_body_sha256,
                            request_model=request_model,
                        )
                    except Exception as exc:
                        logger.warning(
                            "relay_stream_model_usage_record_failed session_id=%s path=%s exception_class=%s exception_message=%s",
                            session_id,
                            request.url.path,
                            exc.__class__.__name__,
                            str(exc),
                        )
                    finally:
                        await close_upstream()

        return StreamingResponse(
            iterator(),
            status_code=upstream_response.status_code,
            headers=self._response_headers(upstream_response),
            media_type=upstream_response.headers.get("content-type"),
        )

    @staticmethod
    def _response_headers(response: httpx.Response) -> dict[str, str]:
        return {
            key: value
            for key, value in response.headers.items()
            if key.lower() not in HOP_BY_HOP_HEADERS
        }

    def _operator_steering_injection(
        self,
        *,
        session_id: str,
        method: str,
        provider_name: str,
        upstream_path: str,
        request_body: bytes,
    ) -> OperatorSteeringInjection:
        if not self._is_operator_steering_delivery_endpoint(
            method=method,
            provider_name=provider_name,
            upstream_path=upstream_path,
        ):
            return OperatorSteeringInjection(request_body=request_body)
        try:
            payload = json.loads(request_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return OperatorSteeringInjection(request_body=request_body)
        if not isinstance(payload, dict):
            return OperatorSteeringInjection(request_body=request_body)

        correlation_context = self._active_model_usage_correlation_context(session_id=session_id)
        if correlation_context is None:
            return OperatorSteeringInjection(request_body=request_body)

        try:
            note = self.operator_steering_store.claim_next_pending(
                session_id=session_id,
                dispatch_id=correlation_context.dispatch_id,
            )
        except Exception as exc:
            logger.warning(
                "operator_steering_claim_failed session_id=%s dispatch_id=%s path=%s exception_class=%s exception_message=%s",
                session_id,
                correlation_context.dispatch_id,
                upstream_path,
                exc.__class__.__name__,
                str(exc),
            )
            return OperatorSteeringInjection(request_body=request_body)
        if note is None:
            return OperatorSteeringInjection(request_body=request_body)

        try:
            injected_payload = self._payload_with_operator_steering_note(
                provider_name=provider_name,
                upstream_path=upstream_path,
                payload=payload,
                message=note.message,
            )
        except ValueError as exc:
            self._release_operator_steering_note(note, error=str(exc))
            logger.warning(
                "operator_steering_injection_skipped note_id=%s session_id=%s dispatch_id=%s path=%s reason=%s",
                note.note_id,
                note.session_id,
                note.dispatch_id,
                upstream_path,
                str(exc),
            )
            return OperatorSteeringInjection(request_body=request_body)

        return OperatorSteeringInjection(
            request_body=json.dumps(
                injected_payload,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8"),
            note=note,
        )

    @classmethod
    def _payload_with_operator_steering_note(
        cls,
        *,
        provider_name: str,
        upstream_path: str,
        payload: dict[str, Any],
        message: str,
    ) -> dict[str, Any]:
        normalized_path = upstream_path.strip().lstrip("/")
        steering_text = cls._operator_steering_text(message)
        injected = dict(payload)
        if provider_name == "anthropic":
            messages = payload.get("messages")
            if not isinstance(messages, list):
                raise ValueError("Anthropic steering delivery requires a messages array")
            steering_message = {
                "role": "user",
                "content": [{"type": "text", "text": steering_text}],
            }
            insertion_index = len(messages)
            while insertion_index > 0:
                previous = messages[insertion_index - 1]
                if not isinstance(previous, dict):
                    break
                if str(previous.get("role") or "").strip().lower() != "system":
                    break
                insertion_index -= 1
            injected["messages"] = [
                *messages[:insertion_index],
                steering_message,
                *messages[insertion_index:],
            ]
            return injected

        if normalized_path == "chat/completions":
            messages = payload.get("messages")
            if not isinstance(messages, list):
                raise ValueError("chat steering delivery requires a messages array")
            injected["messages"] = [
                *messages,
                {"role": "user", "content": steering_text},
            ]
            return injected

        if normalized_path == "responses":
            steering_item = {
                "role": "user",
                "content": [{"type": "input_text", "text": steering_text}],
            }
            input_value = payload.get("input")
            if input_value is None:
                injected["input"] = [steering_item]
                return injected
            if isinstance(input_value, list):
                injected["input"] = [*input_value, steering_item]
                return injected
            if isinstance(input_value, str):
                injected["input"] = [
                    {
                        "role": "user",
                        "content": [{"type": "input_text", "text": input_value}],
                    },
                    steering_item,
                ]
                return injected
            raise ValueError("Responses steering delivery requires string or array input")

        raise ValueError(f"unsupported steering endpoint: {normalized_path}")

    @staticmethod
    def _operator_steering_text(message: str) -> str:
        return (
            "Operator steering note for the current active run. "
            "Apply it only to this run, then continue from the existing context.\n\n"
            f"{message}"
        )

    @staticmethod
    def _is_operator_steering_delivery_endpoint(
        *, method: str, provider_name: str, upstream_path: str
    ) -> bool:
        if str(method or "").upper() != "POST":
            return False
        normalized_path = upstream_path.strip().lstrip("/")
        if provider_name == "anthropic":
            return normalized_path == "messages"
        return normalized_path in {"responses", "chat/completions"}

    def _mark_operator_steering_delivered(
        self,
        note: OperatorSteeringNote | None,
        *,
        provider_name: str,
        provider_endpoint: str,
    ) -> None:
        if note is None:
            return
        try:
            self.operator_steering_store.mark_delivered(
                note.note_id,
                provider=provider_name,
                provider_endpoint=provider_endpoint,
            )
        except Exception as exc:
            logger.warning(
                "operator_steering_mark_delivered_failed note_id=%s session_id=%s dispatch_id=%s exception_class=%s exception_message=%s",
                note.note_id,
                note.session_id,
                note.dispatch_id,
                exc.__class__.__name__,
                str(exc),
            )

    def _release_operator_steering_note(
        self,
        note: OperatorSteeringNote | None,
        *,
        error: str | None = None,
    ) -> None:
        if note is None:
            return
        try:
            self.operator_steering_store.release_claim(note.note_id, error=error)
        except Exception as exc:
            logger.warning(
                "operator_steering_release_failed note_id=%s session_id=%s dispatch_id=%s exception_class=%s exception_message=%s",
                note.note_id,
                note.session_id,
                note.dispatch_id,
                exc.__class__.__name__,
                str(exc),
            )

    @staticmethod
    def _supported_openai_compatible_http_path(*, method: str, upstream_path: str) -> str:
        normalized_method = str(method or "").upper()
        normalized_path = _canonical_relay_path(
            upstream_path,
            field_name="OpenAI relay path",
        )
        if (normalized_method, normalized_path) not in OPENAI_COMPATIBLE_HTTP_ENDPOINTS:
            raise ValueError(OPENAI_COMPATIBLE_ENDPOINTS_DESCRIPTION)
        return normalized_path

    @staticmethod
    def _supported_openai_compatible_websocket_path(*, upstream_path: str) -> str:
        normalized_path = _canonical_relay_path(
            upstream_path,
            field_name="OpenAI websocket relay path",
        )
        if normalized_path not in OPENAI_COMPATIBLE_WEBSOCKET_ENDPOINTS:
            raise ValueError("OpenAI websocket relay currently supports only /v1/responses")
        return normalized_path

    @staticmethod
    def _anthropic_upstream_path(upstream_path: str) -> str:
        normalized = upstream_path.strip().lstrip("/")
        if normalized == "v1":
            return ""
        if normalized.startswith("v1/"):
            normalized = normalized[len("v1/") :]
        return normalized

    @staticmethod
    def _is_supported_anthropic_endpoint(*, method: str, upstream_path: str) -> bool:
        normalized_method = str(method or "").upper()
        normalized_path = upstream_path.strip().lstrip("/")
        if normalized_method == "GET" and normalized_path == "models":
            return True
        return normalized_method == "POST" and normalized_path in {
            "messages",
            "messages/count_tokens",
        }

    @classmethod
    def _anthropic_upstream_http_url(
        cls, *, base_url: str, upstream_path: str, query_items: list[tuple[str, str]]
    ) -> str:
        base = base_url.rstrip("/")
        if base.endswith("/v1"):
            upstream_url = f"{base}/{upstream_path}"
        else:
            upstream_url = f"{base}/v1/{upstream_path}"
        filtered_query = cls._filtered_query_string(query_items)
        if filtered_query:
            upstream_url = f"{upstream_url}?{filtered_query}"
        return upstream_url

    @classmethod
    def _should_stream_http_response(cls, *, request: Request, request_body: bytes) -> bool:
        if "text/event-stream" in request.headers.get("accept", ""):
            return True
        return cls._request_body_declares_stream(request_body)

    @staticmethod
    def _request_body_declares_stream(request_body: bytes) -> bool:
        if not request_body:
            return False
        try:
            payload = json.loads(request_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return False
        return isinstance(payload, dict) and bool(payload.get("stream"))

    @staticmethod
    def _json_payload_from_response(response: httpx.Response) -> dict[str, Any]:
        try:
            payload = json.loads(response.content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError, AttributeError):
            return {}
        if isinstance(payload, dict):
            return payload
        return {}

    @staticmethod
    def _model_from_request_body(request_body: bytes | None) -> str | None:
        if not request_body:
            return None
        try:
            payload = json.loads(request_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict):
            return None
        text = str(payload.get("model") or "").strip()
        return text or None

    @staticmethod
    def _request_status_for_response(status_code: int) -> str:
        if status_code < 400:
            return "success"
        if status_code < 500:
            return "failed"
        return "error"

    @staticmethod
    def _duration_ms_since(started_at: float) -> int:
        return max(0, int((time.perf_counter() - started_at) * 1000))

    def _resolve_request_context(self, request: Request) -> RelayRequestContext:
        session_id = self._session_id_from_path_params(
            request.path_params
        ) or self._require_session_id(request.query_params.multi_items())
        session = self._session_for_request_context(session_id)
        self._ensure_mcp_session_token(session, request)
        ensure_session_not_expired(session)
        resolved_config = self.provider_config_resolver.resolve_for_session_manifest(session)
        self._log_resolved_api_key_env(
            session_id=session_id,
            request_kind="http",
            path=request.url.path,
            api_key_env=self._resolved_api_key_env(resolved_config),
        )
        context = RelayRequestContext(
            session_id=session_id,
            model=getattr(session, "model", None),
            config=resolved_config,
            web_search_enabled=bool(session.web_search_enabled),
            image_generation_enabled=bool(session.image_generation_enabled),
            directory_path=session.directory_path,
            usage_snapshot=self._capture_usage_snapshot(session, resolved_config),
        )
        self._require_privacy(context)
        return context

    def _resolve_websocket_context(self, websocket: WebSocket) -> RelayRequestContext:
        session_id = self._session_id_from_path_params(
            websocket.path_params
        ) or self._require_session_id(websocket.query_params.multi_items())
        session = self._session_for_request_context(session_id)
        self._ensure_mcp_session_token(session, websocket)
        ensure_session_not_expired(session)
        resolved_config = self.provider_config_resolver.resolve_for_session_manifest(session)
        self._log_resolved_api_key_env(
            session_id=session_id,
            request_kind="websocket",
            path=websocket.url.path,
            api_key_env=self._resolved_api_key_env(resolved_config),
        )
        context = RelayRequestContext(
            session_id=session_id,
            model=getattr(session, "model", None),
            config=resolved_config,
            web_search_enabled=bool(session.web_search_enabled),
            image_generation_enabled=bool(session.image_generation_enabled),
            directory_path=session.directory_path,
            usage_snapshot=self._capture_usage_snapshot(session, resolved_config),
        )
        self._require_privacy(context)
        return context

    def _require_privacy(self, context: RelayRequestContext) -> None:
        decision = self.privacy_guard.require(
            provider=context.config.model_provider_mode,
            endpoint=self._resolved_upstream_base_url(context.config),
            connection_id=f"model:{self._resolved_api_key_env(context.config)}",
            directory_path=context.directory_path,
            capability="model_relay",
        )
        if decision.enforced:
            # A persistent connection must not outlive a changed/revoked session
            # or switch silently to the scope/provider of a later configuration.
            try:
                session = self._session_for_request_context(context.session_id)
                ensure_session_not_expired(session)
                current = self.provider_config_resolver.resolve_for_session_manifest(session)
                if (
                    session.directory_path != context.directory_path
                    or current.model_provider_mode != context.config.model_provider_mode
                    or self._resolved_upstream_base_url(current) != self._resolved_upstream_base_url(context.config)
                    or self._resolved_api_key_env(current) != self._resolved_api_key_env(context.config)
                ):
                    raise PrivacyDispatchDenied()
            except Exception:
                raise PrivacyDispatchDenied() from None

    def _privacy_request_hook(self, context: RelayRequestContext):
        async def check(_request: httpx.Request) -> None:
            self._require_privacy(context)
        return check

    async def _privacy_stream_chunks(
        self, chunks: AsyncIterator[bytes], context: RelayRequestContext | None
    ) -> AsyncIterator[bytes]:
        if context is None:
            async for chunk in chunks:
                yield chunk
            return
        pending: asyncio.Task[bytes] | None = None
        loop = asyncio.get_running_loop()
        next_check = float("-inf")
        try:
            while True:
                if loop.time() >= next_check:
                    # Registry/session/provider reads are synchronous. Offload one
                    # complete check per interval, not two checks per SSE chunk.
                    # Do not forward bytes while a due check is still running.
                    await asyncio.to_thread(self._require_privacy, context)
                    next_check = loop.time() + _PRIVACY_STREAM_RECHECK_SECONDS
                if pending is None:
                    pending = asyncio.create_task(anext(chunks))
                done, _ = await asyncio.wait(
                    {pending}, timeout=max(0.0, next_check - loop.time())
                )
                if not done or loop.time() >= next_check:
                    # Even an idle upstream is rechecked. A chunk arriving at a
                    # deadline stays pending until fresh authorization succeeds.
                    continue
                try:
                    chunk = pending.result()
                except StopAsyncIteration:
                    return
                pending = None
                yield chunk
        finally:
            if pending is not None:
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)

    @staticmethod
    def _request_body_declares_hosted_web_search(request_body: bytes) -> bool:
        if not request_body:
            return False
        try:
            payload = json.loads(request_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return False
        return _payload_declares_hosted_web_search(payload)

    @staticmethod
    def _request_body_declares_image_generation(request_body: bytes) -> bool:
        if not request_body:
            return False
        try:
            payload = json.loads(request_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return False
        return _payload_declares_image_generation(payload)

    def _session_for_request_context(self, session_id: str):
        session = self.provider_config_resolver.session_store.get_session(session_id)
        if session is None:
            session = self.provider_config_resolver.provider_session_store.get(session_id)
        if session is None:
            raise ValueError(f"unknown session: {session_id}")
        return session

    @classmethod
    def _ensure_mcp_session_token(cls, session: Any, request: Request | WebSocket) -> None:
        if verify_mcp_session_token(session, cls._mcp_session_token_from_request(request)):
            return
        raise PermissionError(MCP_SESSION_TOKEN_REQUIRED_MESSAGE)

    @staticmethod
    def _mcp_session_token_from_request(request: Request | WebSocket) -> str | None:
        for name in MCP_SESSION_TOKEN_QUERY_PARAM_NAMES:
            token = str(request.query_params.get(name) or "").strip()
            if token:
                return token
        for name in MCP_SESSION_TOKEN_HEADER_NAMES:
            token = str(request.headers.get(name) or "").strip()
            if token:
                return token
        token = str(request.scope.get(OPENAI_AUTH_PATH_SCOPE_TOKEN_KEY) or "").strip()
        if token:
            return token
        token = OpenAIRelay._mcp_session_token_from_auth_headers(request)
        if token:
            return token
        return None

    @staticmethod
    def _mcp_session_token_from_auth_headers(request: Request | WebSocket) -> str | None:
        authorization = str(request.headers.get("authorization") or "").strip()
        if authorization.casefold().startswith("bearer "):
            token = authorization[7:].strip()
            if token:
                return token
        api_key = str(request.headers.get("x-api-key") or "").strip()
        return api_key or None

    @staticmethod
    def _log_resolved_api_key_env(
        *, session_id: str, request_kind: str, path: str, api_key_env: str
    ) -> None:
        logger.warning(
            "relay_resolved_api_key_env session_id=%s request_kind=%s path=%s api_key_env=%s",
            session_id,
            request_kind,
            path,
            api_key_env,
        )

    @staticmethod
    def _resolved_api_key_env(config: GatewayConfig) -> str:
        if config.model_provider_mode == "azure_openai":
            return config.azure_openai_api_key_env or config.openai_api_key_env
        if config.model_provider_mode == "anthropic":
            return config.anthropic_api_key_env
        if config.model_provider_mode == "local_openai":
            return "local_openai_api_key"
        if config.model_provider_mode == "openrouter":
            return config.openrouter_api_key_env
        return config.openai_api_key_env

    @classmethod
    def _resolved_api_key_loaded(cls, config: GatewayConfig) -> bool:
        if config.model_provider_mode == "local_openai":
            return bool(config.local_openai_api_key)
        return bool(os.environ.get(cls._resolved_api_key_env(config)))

    @staticmethod
    def _resolved_upstream_base_url(config: GatewayConfig) -> str:
        if config.model_provider_mode == "anthropic":
            return config.anthropic_api_base_url
        if config.model_provider_mode == "azure_openai":
            return config.azure_openai_endpoint or ""
        if config.model_provider_mode == "local_openai":
            return config.local_openai_api_base_url
        if config.model_provider_mode == "openrouter":
            return config.openrouter_api_base_url
        return config.openai_api_base_url

    @staticmethod
    def _openai_compatible_upstream(config: GatewayConfig) -> OpenAICompatibleUpstream:
        if config.model_provider_mode == "local_openai":
            return OpenAICompatibleUpstream(
                base_url=config.local_openai_api_base_url,
                api_key=config.local_openai_api_key,
                api_key_source="local_openai_api_key",
                provider_name=config.local_openai_provider_name,
            )
        api_key_source = config.openai_api_key_env
        return OpenAICompatibleUpstream(
            base_url=config.openai_api_base_url,
            api_key=os.environ.get(api_key_source),
            api_key_source=api_key_source,
            provider_name="openai",
        )

    @staticmethod
    def _log_hosted_web_search_denied(*, session_id: str, request_kind: str, path: str) -> None:
        logger.warning(
            "relay_denied_hosted_web_search session_id=%s request_kind=%s path=%s",
            session_id,
            request_kind,
            path,
        )

    @staticmethod
    def _log_image_generation_denied(*, session_id: str, request_kind: str, path: str) -> None:
        logger.warning(
            "relay_denied_image_generation session_id=%s request_kind=%s path=%s",
            session_id,
            request_kind,
            path,
        )

    @staticmethod
    def _session_id_from_path_params(path_params: dict[str, Any]) -> str | None:
        session_id = str(path_params.get("session_id") or "").strip()
        return session_id or None

    @staticmethod
    def _require_session_id(query_items: list[tuple[str, str]]) -> str:
        for key, value in query_items:
            if key != "session_id":
                continue
            session_id = value.strip()
            if session_id:
                return session_id
            raise ValueError("session_id query parameter must not be empty")
        raise ValueError("session_id query parameter is required")

    @staticmethod
    def _filtered_query_string(query_items: list[tuple[str, str]]) -> str:
        filtered = [
            (key, value)
            for key, value in query_items
            if key != "session_id" and key not in MCP_SESSION_TOKEN_QUERY_PARAM_NAMES
        ]
        return urlencode(filtered, doseq=True)

    @staticmethod
    def _websocket_base_url(base_url: str) -> str:
        if base_url.startswith("https://"):
            return f"wss://{base_url[len('https://') :]}"
        if base_url.startswith("http://"):
            return f"ws://{base_url[len('http://') :]}"
        return base_url

    @classmethod
    def _upstream_http_url(
        cls, *, base_url: str, upstream_path: str, query_items: list[tuple[str, str]]
    ) -> str:
        upstream_url = (
            f"{base_url.rstrip('/')}/"
            f"{_canonical_relay_path(upstream_path, field_name='OpenAI relay path')}"
        )
        filtered_query = cls._filtered_query_string(query_items)
        if filtered_query:
            upstream_url = f"{upstream_url}?{filtered_query}"
        return upstream_url

    @classmethod
    def _azure_upstream_http_url(
        cls,
        *,
        endpoint: str,
        upstream_path: str,
        query_items: list[tuple[str, str]],
        api_version: str,
    ) -> str:
        filtered_query_items = [
            (key, value)
            for key, value in query_items
            if key not in {"session_id", "api-version"}
            and key not in MCP_SESSION_TOKEN_QUERY_PARAM_NAMES
        ]
        filtered_query_items.append(("api-version", api_version))
        upstream_url = f"{endpoint.rstrip('/')}/openai/v1/{upstream_path}"
        filtered_query = urlencode(filtered_query_items, doseq=True)
        if filtered_query:
            upstream_url = f"{upstream_url}?{filtered_query}"
        return upstream_url

    @staticmethod
    def _rewrite_azure_openai_request_body(
        request_body: bytes, *, model_deployments: dict[str, str]
    ) -> bytes:
        try:
            payload = json.loads(request_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("Azure OpenAI responses requests must contain valid JSON") from exc
        if not isinstance(payload, dict):
            raise ValueError("Azure OpenAI responses requests must be a JSON object")

        model = payload.get("model")
        if not isinstance(model, str):
            raise ValueError("Azure OpenAI responses requests require a top-level string model")
        logical_model = model.strip()
        if not logical_model:
            raise ValueError("Azure OpenAI responses requests require a non-empty model")

        deployment_name = model_deployments.get(logical_model)
        if deployment_name is None:
            raise ValueError(
                f"Azure OpenAI model {logical_model!r} is not configured in "
                "azure_openai_model_deployments"
            )

        rewritten_payload = dict(payload)
        rewritten_payload["model"] = deployment_name
        return json.dumps(rewritten_payload).encode("utf-8")

    @classmethod
    def _upstream_websocket_url(
        cls, *, base_url: str, upstream_path: str, query_items: list[tuple[str, str]]
    ) -> str:
        base_url = cls._websocket_base_url(base_url.rstrip("/"))
        upstream_url = (
            f"{base_url}/"
            f"{_canonical_relay_path(upstream_path, field_name='OpenAI websocket relay path')}"
        )
        filtered_query = cls._filtered_query_string(query_items)
        if filtered_query:
            upstream_url = f"{upstream_url}?{filtered_query}"
        return upstream_url

    @asynccontextmanager
    async def _connect_upstream_websocket(
        self, upstream_url: str, *, headers: dict[str, str], timeout_seconds: float
    ) -> AsyncIterator[Any]:
        try:
            import websockets
        except ModuleNotFoundError as exc:  # pragma: no cover - exercised via integration/runtime
            raise RuntimeError(
                "websocket relay support requires the 'websockets' package to be installed"
            ) from exc

        class ApprovedEndpointConnect(websockets.connect):
            def process_redirect(self, exc: Exception) -> Exception:
                # Even same-origin redirects may change the approved service
                # path. Reconfiguration and reapproval must be explicit.
                return exc

        async with ApprovedEndpointConnect(
            upstream_url,
            additional_headers=headers,
            open_timeout=timeout_seconds,
            close_timeout=timeout_seconds,
            proxy=None,
        ) as upstream:
            yield upstream

    async def _proxy_websocket_traffic(
        self,
        websocket: WebSocket,
        upstream: Any,
        *,
        session_id: str | None = None,
        upstream_path: str = "",
        path: str = "",
        web_search_enabled: bool = True,
        image_generation_enabled: bool = True,
        provider_name: str = "openai",
        privacy_context: RelayRequestContext | None = None,
    ) -> None:
        try:
            from websockets.exceptions import ConnectionClosedOK
        except ModuleNotFoundError:  # pragma: no cover - guarded earlier
            ConnectionClosedOK = ()  # type: ignore[assignment]

        from collections import OrderedDict, deque

        pending_usage = deque()
        response_usage = OrderedDict()

        async def capture_request_usage(message) -> None:
            try:
                payload = json.loads(message)
            except (ValueError, UnicodeDecodeError):
                return
            if not isinstance(payload, dict) or payload.get("type") != "response.create":
                return
            snapshot = privacy_context.usage_snapshot if privacy_context else None
            if privacy_context is not None and session_id:
                session = await run_in_threadpool(self._session_for_request_context, session_id)
                snapshot = await run_in_threadpool(
                    self._capture_usage_snapshot, session, privacy_context.config
                )
            pending_usage.append(snapshot)

        async def safe_close_client(code: int = 1000, reason: str = "") -> None:
            try:
                await websocket.close(code=code, reason=reason)
            except RuntimeError:
                # Starlette/Uvicorn raises if the websocket is already closing/closed.
                return

        async def client_to_upstream() -> None:
            while True:
                try:
                    message = await websocket.receive()
                except WebSocketDisconnect:
                    await upstream.close()
                    return

                message_type = message["type"]
                if message_type == "websocket.disconnect":
                    await upstream.close()
                    return
                if message_type != "websocket.receive":
                    continue
                if privacy_context is not None:
                    try:
                        self._require_privacy(privacy_context)
                    except PrivacyDispatchDenied:
                        await upstream.close()
                        await safe_close_client(code=1008, reason="privacy approval revoked")
                        return
                if message.get("text") is not None:
                    text = message["text"]
                    if not web_search_enabled and _websocket_message_declares_hosted_web_search(
                        text
                    ):
                        self._log_hosted_web_search_denied(
                            session_id=session_id or "",
                            request_kind="websocket",
                            path=path,
                        )
                        await upstream.close()
                        await safe_close_client(
                            code=1008,
                            reason="hosted web search is disabled for this session",
                        )
                        return
                    if (
                        not image_generation_enabled
                        and _websocket_message_declares_image_generation(text)
                    ):
                        self._log_image_generation_denied(
                            session_id=session_id or "",
                            request_kind="websocket",
                            path=path,
                        )
                        await upstream.close()
                        await safe_close_client(
                            code=1008,
                            reason="image generation is disabled for this session",
                        )
                        return
                    await capture_request_usage(message["text"])
                    await upstream.send(message["text"])
                    continue
                if message.get("bytes") is not None:
                    payload = message["bytes"]
                    if not web_search_enabled and _websocket_message_declares_hosted_web_search(
                        payload
                    ):
                        self._log_hosted_web_search_denied(
                            session_id=session_id or "",
                            request_kind="websocket",
                            path=path,
                        )
                        await upstream.close()
                        await safe_close_client(
                            code=1008,
                            reason="hosted web search is disabled for this session",
                        )
                        return
                    if (
                        not image_generation_enabled
                        and _websocket_message_declares_image_generation(payload)
                    ):
                        self._log_image_generation_denied(
                            session_id=session_id or "",
                            request_kind="websocket",
                            path=path,
                        )
                        await upstream.close()
                        await safe_close_client(
                            code=1008,
                            reason="image generation is disabled for this session",
                        )
                        return
                    await capture_request_usage(message["bytes"])
                    await upstream.send(message["bytes"])

        started_at = time.perf_counter()

        async def upstream_to_client() -> None:
            while True:
                try:
                    message = await upstream.recv()
                except Exception as exc:
                    if isinstance(exc, ConnectionClosedOK):
                        await safe_close_client(code=1000)
                        return
                    await safe_close_client(code=1011)
                    return

                try:
                    envelope = json.loads(message)
                except (ValueError, UnicodeDecodeError):
                    envelope = {}
                if isinstance(envelope, dict) and envelope.get("type") == "response.created":
                    response = envelope.get("response")
                    response_id = response.get("id") if isinstance(response, dict) else None
                    if response_id and response_id not in response_usage and pending_usage:
                        response_usage[response_id] = pending_usage.popleft()
                elif (
                    isinstance(envelope, dict) and envelope.get("type") == "error" and pending_usage
                ):
                    # Rejected creates have no response.created/completed pair.
                    pending_usage.popleft()
                usage_payload = self._websocket_usage_payload_from_message(message)
                if usage_payload is not None and session_id:
                    response_payload, event_type = usage_payload
                    response_id = response_payload.get("id")
                    if response_id in response_usage:
                        usage_snapshot = response_usage[response_id]
                    elif pending_usage:
                        usage_snapshot = pending_usage.popleft()
                    else:
                        usage_snapshot = privacy_context.usage_snapshot if privacy_context else None
                    if response_id:
                        # Keep recent completions so a duplicate cannot consume
                        # attribution queued for the next response on this socket.
                        response_usage[response_id] = usage_snapshot
                        while len(response_usage) > 256:
                            response_usage.popitem(last=False)
                    try:
                        await run_in_threadpool(
                            self._record_websocket_model_usage_event,
                            usage_snapshot=usage_snapshot,
                            session_id=session_id,
                            upstream_path=upstream_path,
                            message=message,
                            response_payload=response_payload,
                            event_type=event_type,
                            duration_ms=self._duration_ms_since(started_at),
                            provider_name=provider_name,
                        )
                    except Exception as exc:
                        logger.warning(
                            "relay_websocket_model_usage_record_failed session_id=%s path=%s exception_class=%s exception_message=%s",
                            session_id,
                            path,
                            exc.__class__.__name__,
                            str(exc),
                        )
                if isinstance(message, bytes):
                    await websocket.send_bytes(message)
                else:
                    await websocket.send_text(message)

        tasks = [
            asyncio.create_task(client_to_upstream()),
            asyncio.create_task(upstream_to_client()),
        ]
        if privacy_context is not None:
            async def watch_privacy() -> None:
                while True:
                    await asyncio.sleep(1.0)
                    try:
                        self._require_privacy(privacy_context)
                    except PrivacyDispatchDenied:
                        await upstream.close()
                        await safe_close_client(code=1008, reason="privacy approval revoked")
                        return
            tasks.append(asyncio.create_task(watch_privacy()))
        cancelled = False
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                if task.cancelled():
                    continue
                exc = task.exception()
                if exc is not None and not isinstance(exc, WebSocketDisconnect):
                    raise exc
        except asyncio.CancelledError:
            # ASGI servers cancel a WebSocket handler during client teardown.
            # Treat that as connection closure, while still reaping child tasks.
            cancelled = True
            return
        finally:
            # Also reap the policy watcher if the ASGI request itself is cancelled.
            for task in tasks:
                if not task.done():
                    task.cancel()
            with anyio.CancelScope(shield=True):
                await asyncio.gather(*tasks, return_exceptions=True)
                if cancelled:
                    await upstream.close()

    @classmethod
    def _websocket_usage_payload_from_message(
        cls, message: str | bytes
    ) -> tuple[dict[str, Any], str | None] | None:
        if isinstance(message, bytes):
            try:
                text = message.decode("utf-8")
            except UnicodeDecodeError:
                return None
        else:
            text = message
        try:
            event_payload = json.loads(text)
        except json.JSONDecodeError:
            return None
        if not isinstance(event_payload, dict):
            return None

        candidate = event_payload.get("response")
        if not isinstance(candidate, dict):
            candidate = event_payload
        event_type = event_payload.get("type") or candidate.get("type")
        normalized_event_type = str(event_type or "").strip()
        has_response_id = candidate.get("id") is not None
        has_usage = isinstance(candidate.get("usage"), dict)
        is_final_response_event = normalized_event_type in {
            "response.completed",
            "response.done",
        }
        is_response_payload_with_usage = has_response_id and has_usage
        if not has_response_id or not (is_final_response_event or is_response_payload_with_usage):
            return None

        payload: dict[str, Any] = {}
        cls._merge_stream_event_dict(event_payload, payload)
        if not payload.get("id"):
            return None
        return payload, normalized_event_type or None


def create_app(
    config: GatewayConfig, transport: httpx.AsyncBaseTransport | None = None
) -> Starlette:
    return OpenAIRelay(
        config,
        transport=transport,
        provider_config_resolver=ProviderConfigResolver(config),
    ).create_app()


def _websocket_message_declares_hosted_web_search(message: str | bytes) -> bool:
    try:
        if isinstance(message, bytes):
            payload = json.loads(message.decode("utf-8"))
        else:
            payload = json.loads(message)
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError):
        return False
    return _payload_declares_hosted_web_search(payload)


def _websocket_message_declares_image_generation(message: str | bytes) -> bool:
    try:
        if isinstance(message, bytes):
            payload = json.loads(message.decode("utf-8"))
        else:
            payload = json.loads(message)
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError):
        return False
    return _payload_declares_image_generation(payload)


def _canonical_relay_path(path: object, *, field_name: str) -> str:
    segments = _safe_path_segments(path, field_name=field_name)
    return "/".join(quote(segment, safe=PATH_SEGMENT_SAFE_CHARS) for segment in segments)


def _safe_path_segments(path: object, *, field_name: str) -> list[str]:
    raw_path = str(path or "").lstrip("/")
    if not raw_path:
        return []
    segments: list[str] = []
    for raw_segment in raw_path.split("/"):
        if not raw_segment:
            continue
        segment = _fully_unquote_path_segment(raw_segment, field_name=field_name)
        if segment in {".", ".."}:
            raise ValueError(f"{field_name} must not contain dot segments")
        if "/" in segment or "\\" in segment:
            raise ValueError(f"{field_name} must not contain encoded path separators")
        segments.append(segment)
    return segments


def _fully_unquote_path_segment(segment: str, *, field_name: str) -> str:
    decoded = segment
    for _ in range(MAX_PATH_DECODE_PASSES):
        next_decoded = unquote(decoded)
        if next_decoded == decoded:
            return decoded
        decoded = next_decoded
    if unquote(decoded) != decoded:
        raise ValueError(f"{field_name} is too deeply percent-encoded")
    return decoded


def _payload_declares_hosted_web_search(payload: Any) -> bool:
    if isinstance(payload, list):
        return any(_payload_declares_hosted_web_search(item) for item in payload)
    if not isinstance(payload, dict):
        return False

    tools = payload.get("tools")
    if _tools_declare_hosted_web_search(tools):
        return True
    for value in payload.values():
        if isinstance(value, (dict, list)) and _payload_declares_hosted_web_search(value):
            return True
    return False


def _tools_declare_hosted_web_search(tools: Any) -> bool:
    if isinstance(tools, list):
        return any(_tool_declares_hosted_web_search(tool) for tool in tools)
    if isinstance(tools, dict):
        return _tool_declares_hosted_web_search(tools)
    return False


def _tool_declares_hosted_web_search(tool: Any) -> bool:
    if not isinstance(tool, dict):
        return False
    raw_type = str(tool.get("type") or "").strip()
    if raw_type in HOSTED_WEB_SEARCH_TOOL_TYPES or (
        raw_type.startswith("web_search_") and raw_type != "web_search_call"
    ):
        return True
    nested_tools = tool.get("tools")
    return _tools_declare_hosted_web_search(nested_tools)


def _payload_declares_image_generation(payload: Any) -> bool:
    if isinstance(payload, list):
        return any(_payload_declares_image_generation(item) for item in payload)
    if not isinstance(payload, dict):
        return False

    tools = payload.get("tools")
    if _tools_declare_image_generation(tools):
        return True
    for value in payload.values():
        if isinstance(value, (dict, list)) and _payload_declares_image_generation(value):
            return True
    return False


def _tools_declare_image_generation(tools: Any) -> bool:
    if isinstance(tools, list):
        return any(_tool_declares_image_generation(tool) for tool in tools)
    if isinstance(tools, dict):
        return _tool_declares_image_generation(tools)
    return False


def _tools_declare_openrouter_server_tool(tools: Any) -> bool:
    if isinstance(tools, list):
        return any(_tools_declare_openrouter_server_tool(tool) for tool in tools)
    if not isinstance(tools, dict):
        return False
    raw_type = str(tools.get("type") or "").strip().casefold()
    if raw_type.startswith("openrouter:"):
        return True
    return _tools_declare_openrouter_server_tool(tools.get("tools"))


def _tool_declares_image_generation(tool: Any) -> bool:
    if not isinstance(tool, dict):
        return False
    raw_type = str(tool.get("type") or "").strip()
    if raw_type in IMAGE_GENERATION_TOOL_TYPES:
        return True
    return _tools_declare_image_generation(tool.get("tools"))


def _project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _bootstrap_runtime_config(*, runtime_dir: Path | None = None) -> GatewayConfig:
    return load_runtime_gateway_config(
        runtime_dir=resolve_runtime_dir(runtime_dir=runtime_dir or _project_root() / "runtime")
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the host-side OpenAI relay.")
    parser.add_argument("--runtime-dir", type=Path, help="Runtime directory root.")
    args = parser.parse_args()

    config = _bootstrap_runtime_config(runtime_dir=args.runtime_dir)
    uvicorn.run(
        create_app(config),
        host=config.openai_relay_host,
        port=config.openai_relay_port,
    )


if __name__ == "__main__":
    main()
