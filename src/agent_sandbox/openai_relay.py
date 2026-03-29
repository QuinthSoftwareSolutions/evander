from __future__ import annotations

import argparse
import asyncio
import logging
import os
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocket, WebSocketDisconnect

from .bootstrap import load_runtime_gateway_config, resolve_runtime_dir
from .config import GatewayConfig
from .provider_config import ProviderConfigResolver

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


class OpenAIRelay:
    def __init__(self, config: GatewayConfig, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.config = config
        self.transport = transport
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
            api_key = os.environ.get(relay.config.openai_api_key_env)
            if not api_key:
                return JSONResponse(
                    {
                        "error": f"Host environment variable {relay.config.openai_api_key_env} is not set."
                    },
                    status_code=503,
                )

            upstream_path = request.path_params["path"]
            upstream_url = f"{relay.config.openai_api_base_url.rstrip('/')}/{upstream_path}"
            request_body = await request.body()

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
                )

            async with httpx.AsyncClient(
                timeout=relay.config.openai_relay_timeout_seconds,
                transport=relay.transport,
            ) as client:
                upstream_response = await client.request(
                    method=request.method,
                    url=upstream_url,
                    headers=headers,
                    content=request_body,
                )
                return Response(
                    content=upstream_response.content,
                    status_code=upstream_response.status_code,
                    headers=relay._response_headers(upstream_response),
                    media_type=upstream_response.headers.get("content-type"),
                )

        async def websocket_proxy(websocket: WebSocket) -> None:
            api_key = os.environ.get(relay.config.openai_api_key_env)
            if not api_key:
                await websocket.close(code=1011, reason="missing upstream API key")
                return

            upstream_path = websocket.path_params["path"]
            upstream_url = relay._upstream_websocket_url(websocket, upstream_path)
            headers = {
                key: value
                for key, value in websocket.headers.items()
                if key.lower() not in HOP_BY_HOP_HEADERS
                and not key.lower().startswith("sec-websocket-")
            }
            headers["authorization"] = f"Bearer {api_key}"

            async with relay._connect_upstream_websocket(upstream_url, headers=headers) as upstream:
                await websocket.accept()
                await relay._proxy_websocket_traffic(websocket, upstream)

        return Starlette(
            debug=False,
            routes=[
                Route("/healthz", health, methods=["GET"]),
                Route("/v1/{path:path}", proxy, methods=["GET", "POST", "PUT", "PATCH", "DELETE"]),
                WebSocketRoute("/v1/{path:path}", websocket_proxy),
            ],
        )

    async def _stream_response(
        self,
        request: Request,
        upstream_url: str,
        headers: dict[str, str],
        request_body: bytes,
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
            timeout=self.config.openai_relay_timeout_seconds,
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

    @staticmethod
    def _websocket_base_url(base_url: str) -> str:
        if base_url.startswith("https://"):
            return f"wss://{base_url[len('https://'):]}"
        if base_url.startswith("http://"):
            return f"ws://{base_url[len('http://'):]}"
        return base_url

    def _upstream_websocket_url(self, websocket: WebSocket, upstream_path: str) -> str:
        base_url = self._websocket_base_url(self.config.openai_api_base_url.rstrip("/"))
        upstream_url = f"{base_url}/{upstream_path}"
        if websocket.url.query:
            upstream_url = f"{upstream_url}?{websocket.url.query}"
        return upstream_url

    @asynccontextmanager
    async def _connect_upstream_websocket(
        self, upstream_url: str, *, headers: dict[str, str]
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
            open_timeout=self.config.openai_relay_timeout_seconds,
            close_timeout=self.config.openai_relay_timeout_seconds,
        ) as upstream:
            yield upstream

    async def _proxy_websocket_traffic(self, websocket: WebSocket, upstream: Any) -> None:
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
                    await upstream.send(message["text"])
                    continue
                if message.get("bytes") is not None:
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


def create_app(config: GatewayConfig, transport: httpx.AsyncBaseTransport | None = None) -> Starlette:
    resolved_config = ProviderConfigResolver(config).resolve_for_directory("root")
    return OpenAIRelay(resolved_config, transport=transport).create_app()


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _bootstrap_runtime_config(*, legacy_config_path: Path | None = None) -> GatewayConfig:
    return load_runtime_gateway_config(
        runtime_dir=resolve_runtime_dir(runtime_dir=_project_root() / "runtime", legacy_config_path=legacy_config_path),
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
