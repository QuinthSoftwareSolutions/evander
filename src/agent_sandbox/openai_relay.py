from __future__ import annotations

import argparse
import os
from collections.abc import AsyncIterator

import httpx
import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

from .config import GatewayConfig, load_config

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

        return Starlette(
            debug=False,
            routes=[
                Route("/healthz", health, methods=["GET"]),
                Route("/v1/{path:path}", proxy, methods=["GET", "POST", "PUT", "PATCH", "DELETE"]),
            ],
        )

    async def _stream_response(
        self,
        request: Request,
        upstream_url: str,
        headers: dict[str, str],
        request_body: bytes,
    ) -> StreamingResponse:
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

        async def iterator() -> AsyncIterator[bytes]:
            try:
                try:
                    async for chunk in upstream_response.aiter_raw():
                        yield chunk
                except (httpx.ReadError, httpx.RemoteProtocolError):
                    # Some upstream streaming responses terminate with a transport-level
                    # read error after the final bytes have already been delivered.
                    # Treat that as end-of-stream instead of crashing the relay.
                    return
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


def create_app(config: GatewayConfig, transport: httpx.AsyncBaseTransport | None = None) -> Starlette:
    return OpenAIRelay(config, transport=transport).create_app()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the host-side OpenAI relay.")
    parser.add_argument("--config", default="config/gateway.yaml", help="Path to the gateway YAML config.")
    args = parser.parse_args()

    config = load_config(args.config)
    uvicorn.run(
        create_app(config),
        host=config.openai_relay_host,
        port=config.openai_relay_port,
    )


if __name__ == "__main__":
    main()
