from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
from hashlib import sha256
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx
import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocket, WebSocketDisconnect

from agent_sandbox.bootstrap import load_runtime_gateway_config, resolve_runtime_dir
from agent_sandbox.config import GatewayConfig
from agent_sandbox.model_usage.adapters import (
    MappingModelUsageExtractor,
    ModelUsageExtractionRequest,
)
from agent_sandbox.model_usage.runtime import (
    MODEL_USAGE_AGENT_HARNESS,
    model_usage_store,
)
from agent_sandbox.provider_config import ProviderConfigResolver

logger = logging.getLogger(__name__)


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
}

HOSTED_WEB_SEARCH_TOOL_TYPES = {
    "web_search",
    "web_search_preview",
}


@dataclass(frozen=True)
class RelayRequestContext:
    session_id: str
    config: GatewayConfig
    web_search_enabled: bool


class OpenAIRelay:
    def __init__(
        self,
        config: GatewayConfig,
        transport: httpx.AsyncBaseTransport | None = None,
        *,
        provider_config_resolver: ProviderConfigResolver | None = None,
    ) -> None:
        self.config = config
        self.transport = transport
        self.provider_config_resolver = provider_config_resolver or ProviderConfigResolver(config)
        self.model_usage_store = model_usage_store(config.runtime_dir)
        self.stream_started_count = 0
        self.stream_clean_end_count = 0
        self.stream_transport_terminated_count = 0

    def create_app(self) -> Starlette:
        relay = self

        async def health(_: Request) -> JSONResponse:
            has_api_key = bool(os.environ.get(relay.config.openai_api_key_env))
            return JSONResponse(
                {
                    "status": "ok",
                    "upstream": relay.config.openai_api_base_url,
                    "api_key_loaded": has_api_key,
                }
            )

        async def proxy(request: Request) -> Response:
            try:
                request_context = relay._resolve_request_context(request)
            except ValueError as exc:
                return JSONResponse({"error": str(exc)}, status_code=400)

            api_key = os.environ.get(request_context.config.openai_api_key_env)
            if not api_key:
                return JSONResponse(
                    {
                        "error": f"Host environment variable {request_context.config.openai_api_key_env} is not set."
                    },
                    status_code=503,
                )

            upstream_path = request.path_params["path"]
            upstream_url = relay._upstream_http_url(
                base_url=request_context.config.openai_api_base_url,
                upstream_path=upstream_path,
                query_items=request.query_params.multi_items(),
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

            headers = {
                key: value
                for key, value in request.headers.items()
                if key.lower() not in HOP_BY_HOP_HEADERS
            }
            headers["accept-encoding"] = "identity"
            headers["authorization"] = f"Bearer {api_key}"

            if "text/event-stream" in request.headers.get("accept", ""):
                return await relay._stream_response(
                    request=request,
                    upstream_url=upstream_url,
                    headers=headers,
                    request_body=request_body,
                    timeout_seconds=request_context.config.openai_relay_timeout_seconds,
                )

            async with httpx.AsyncClient(
                timeout=request_context.config.openai_relay_timeout_seconds,
                transport=relay.transport,
            ) as client:
                upstream_response = await client.request(
                    method=request.method,
                    url=upstream_url,
                    headers=headers,
                    content=request_body,
                )
                relay._record_model_usage_event(
                    session_id=request_context.session_id,
                    upstream_path=upstream_path,
                    request_body=request_body,
                    upstream_response=upstream_response,
                )
                return Response(
                    content=upstream_response.content,
                    status_code=upstream_response.status_code,
                    headers=relay._response_headers(upstream_response),
                    media_type=upstream_response.headers.get("content-type"),
                )

        async def websocket_proxy(websocket: WebSocket) -> None:
            try:
                request_context = relay._resolve_websocket_context(websocket)
            except ValueError as exc:
                await websocket.close(code=1008, reason=str(exc))
                return

            api_key = os.environ.get(request_context.config.openai_api_key_env)
            if not api_key:
                await websocket.close(code=1011, reason="missing upstream API key")
                return

            upstream_path = websocket.path_params["path"]
            upstream_url = relay._upstream_websocket_url(
                base_url=request_context.config.openai_api_base_url,
                upstream_path=upstream_path,
                query_items=websocket.query_params.multi_items(),
            )
            headers = {
                key: value
                for key, value in websocket.headers.items()
                if key.lower() not in HOP_BY_HOP_HEADERS
                and not key.lower().startswith("sec-websocket-")
            }
            headers["authorization"] = f"Bearer {api_key}"

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
                    path=websocket.url.path,
                    web_search_enabled=request_context.web_search_enabled,
                )

        return Starlette(
            debug=False,
            routes=[
                Route("/healthz", health, methods=["GET"]),
                Route(
                    "/v1/session/{session_id}/{path:path}",
                    proxy,
                    methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
                ),
                Route("/v1/{path:path}", proxy, methods=["GET", "POST", "PUT", "PATCH", "DELETE"]),
                WebSocketRoute("/v1/session/{session_id}/{path:path}", websocket_proxy),
                WebSocketRoute("/v1/{path:path}", websocket_proxy),
            ],
        )

    async def _stream_response(
        self,
        request: Request,
        upstream_url: str,
        headers: dict[str, str],
        request_body: bytes,
        timeout_seconds: float,
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
        )
        upstream_stream = client.stream(
            method=request.method,
            url=upstream_url,
            headers=headers,
            content=request_body,
        )
        upstream_response = await upstream_stream.__aenter__()
        logger.info(
            "relay_stream_upstream_response stream_id=%s status_code=%s content_type=%s",
            stream_id,
            upstream_response.status_code,
            upstream_response.headers.get("content-type", ""),
        )

        async def iterator() -> AsyncIterator[bytes]:
            bytes_sent = 0
            chunk_count = 0
            try:
                try:
                    async for chunk in upstream_response.aiter_raw():
                        chunk_count += 1
                        bytes_sent += len(chunk)
                        yield chunk
                except (httpx.ReadError, httpx.RemoteProtocolError) as exc:
                    # Some upstream streaming responses terminate with a transport-level
                    # read error after the final bytes have already been delivered.
                    # Treat that as end-of-stream instead of crashing the relay.
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
                await upstream_stream.__aexit__(None, None, None)
                await client.aclose()

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

    def _record_model_usage_event(
        self,
        *,
        session_id: str,
        upstream_path: str,
        request_body: bytes,
        upstream_response: httpx.Response,
    ) -> None:
        if not self._is_model_usage_endpoint(upstream_path):
            return

        response_payload = self._json_payload_from_response(upstream_response)
        request_status = self._request_status_for_response(upstream_response.status_code)
        request_metadata = {
            "method": "POST",
            "path": f"/v1/{upstream_path}",
        }
        response_metadata = {
            "status_code": upstream_response.status_code,
            "content_type": upstream_response.headers.get("content-type"),
        }
        correlation_context = self.model_usage_store.get_active_correlation_context(
            session_id=session_id,
            agent_harness=MODEL_USAGE_AGENT_HARNESS,
            execution_backend=self.config.execution_backend,
        )
        request_hash = sha256(request_body).hexdigest() if request_body else None
        response_body = upstream_response.content
        response_hash = sha256(response_body).hexdigest() if response_body else None
        extractor = MappingModelUsageExtractor(provider_name="openai")
        result = extractor.extract(
            ModelUsageExtractionRequest(
                provider="openai",
                provider_endpoint_identifier=upstream_path,
                request_status=request_status,
                model=response_payload.get("model"),
                request_body_bytes=len(request_body) if request_body else None,
                request_body_sha256=request_hash,
                response_body_bytes=len(response_body) if response_body else None,
                response_body_sha256=response_hash,
                response_payload=response_payload,
                request_metadata=request_metadata,
                response_metadata=response_metadata,
                correlation_context=correlation_context,
            )
        )
        self.model_usage_store.upsert_event(result.event)

    @staticmethod
    def _is_model_usage_endpoint(upstream_path: str) -> bool:
        normalized = upstream_path.lstrip("/")
        return normalized in {"responses", "chat/completions", "completions"}

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
    def _request_status_for_response(status_code: int) -> str:
        if status_code < 400:
            return "success"
        if status_code < 500:
            return "failed"
        return "error"

    def _resolve_request_context(self, request: Request) -> RelayRequestContext:
        session_id = self._session_id_from_path_params(
            request.path_params
        ) or self._require_session_id(request.query_params.multi_items())
        resolved_config = self.provider_config_resolver.resolve_for_session_id(session_id)
        self._log_resolved_api_key_env(
            session_id=session_id,
            request_kind="http",
            path=request.url.path,
            api_key_env=resolved_config.openai_api_key_env,
        )
        return RelayRequestContext(
            session_id=session_id,
            config=resolved_config,
            web_search_enabled=self._session_web_search_enabled(session_id),
        )

    def _resolve_websocket_context(self, websocket: WebSocket) -> RelayRequestContext:
        session_id = self._session_id_from_path_params(
            websocket.path_params
        ) or self._require_session_id(websocket.query_params.multi_items())
        resolved_config = self.provider_config_resolver.resolve_for_session_id(session_id)
        self._log_resolved_api_key_env(
            session_id=session_id,
            request_kind="websocket",
            path=websocket.url.path,
            api_key_env=resolved_config.openai_api_key_env,
        )
        return RelayRequestContext(
            session_id=session_id,
            config=resolved_config,
            web_search_enabled=self._session_web_search_enabled(session_id),
        )

    def _session_web_search_enabled(self, session_id: str) -> bool:
        try:
            session = self.provider_config_resolver.get_session_manifest(session_id)
        except ValueError:
            return False
        return bool(getattr(session, "web_search_enabled", False))

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
    def _log_hosted_web_search_denied(*, session_id: str, request_kind: str, path: str) -> None:
        logger.warning(
            "relay_denied_hosted_web_search session_id=%s request_kind=%s path=%s",
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
        filtered = [(key, value) for key, value in query_items if key != "session_id"]
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
        upstream_url = f"{base_url.rstrip('/')}/{upstream_path}"
        filtered_query = cls._filtered_query_string(query_items)
        if filtered_query:
            upstream_url = f"{upstream_url}?{filtered_query}"
        return upstream_url

    @classmethod
    def _upstream_websocket_url(
        cls, *, base_url: str, upstream_path: str, query_items: list[tuple[str, str]]
    ) -> str:
        base_url = cls._websocket_base_url(base_url.rstrip("/"))
        upstream_url = f"{base_url}/{upstream_path}"
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

        async with websockets.connect(
            upstream_url,
            additional_headers=headers,
            open_timeout=timeout_seconds,
            close_timeout=timeout_seconds,
        ) as upstream:
            yield upstream

    async def _proxy_websocket_traffic(
        self,
        websocket: WebSocket,
        upstream: Any,
        *,
        session_id: str | None = None,
        path: str = "",
        web_search_enabled: bool = True,
    ) -> None:
        try:
            from websockets.exceptions import ConnectionClosedOK
        except ModuleNotFoundError:  # pragma: no cover - guarded earlier
            ConnectionClosedOK = ()  # type: ignore[assignment]

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
                    await upstream.send(message["bytes"])

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

                if isinstance(message, bytes):
                    await websocket.send_bytes(message)
                else:
                    await websocket.send_text(message)

        tasks = [
            asyncio.create_task(client_to_upstream()),
            asyncio.create_task(upstream_to_client()),
        ]
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        for task in done:
            exc = task.exception()
            if exc is not None and not isinstance(exc, WebSocketDisconnect):
                raise exc


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


def _project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _bootstrap_runtime_config(*, legacy_config_path: Path | None = None) -> GatewayConfig:
    return load_runtime_gateway_config(
        runtime_dir=resolve_runtime_dir(
            runtime_dir=_project_root() / "runtime", legacy_config_path=legacy_config_path
        ),
        legacy_config_path=legacy_config_path,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the host-side OpenAI relay.")
    parser.add_argument(
        "--legacy-config",
        help="Optional legacy gateway YAML used only to import root config if root is missing.",
    )
    parser.add_argument(
        "--config",
        dest="legacy_config",
        help=argparse.SUPPRESS,
    )
    args = parser.parse_args()

    legacy_config_path = Path(args.legacy_config) if args.legacy_config else None
    config = _bootstrap_runtime_config(legacy_config_path=legacy_config_path)
    uvicorn.run(
        create_app(config),
        host=config.openai_relay_host,
        port=config.openai_relay_port,
    )


if __name__ == "__main__":
    main()
