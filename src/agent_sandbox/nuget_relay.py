from __future__ import annotations

import argparse
import base64
import json
import os
from urllib.parse import urlsplit

import httpx
import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
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


class NuGetRelay:
    def __init__(self, config: GatewayConfig, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.config = config
        self.transport = transport

    def create_app(self) -> Starlette:
        relay = self

        async def health(_: Request) -> JSONResponse:
            has_pat = bool(os.environ.get(relay.config.nuget_azure_pat_env))
            return JSONResponse(
                {
                    "status": "ok",
                    "public_upstream": relay.config.nuget_public_feed_base_url,
                    "azure_upstream": relay.config.nuget_azure_feed_base_url,
                    "azure_pat_loaded": has_pat,
                }
            )

        async def proxy_public(request: Request) -> Response:
            path = request.path_params.get("path", "")
            return await relay._proxy(
                request=request,
                upstream_base_url=relay.config.nuget_public_feed_base_url,
                path=path,
                upstream_prefix="/nuget/nuget-org",
                auth_header=None,
            )

        async def proxy_azure(request: Request) -> Response:
            pat = os.environ.get(relay.config.nuget_azure_pat_env)
            if not pat:
                return JSONResponse(
                    {
                        "error": (
                            f"Host environment variable {relay.config.nuget_azure_pat_env} is not set."
                        )
                    },
                    status_code=503,
                )

            token = base64.b64encode(f"agent:{pat}".encode("utf-8")).decode("ascii")
            auth_header = f"Basic {token}"
            path = request.path_params.get("path", "")
            return await relay._proxy(
                request=request,
                upstream_base_url=relay.config.nuget_azure_feed_base_url,
                path=path,
                upstream_prefix="/nuget/azure",
                auth_header=auth_header,
            )

        routes = [
            Route("/healthz", health, methods=["GET"]),
            Route(
                "/nuget/nuget-org/{path:path}",
                proxy_public,
                methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"],
            ),
            Route(
                "/nuget/azure/{path:path}",
                proxy_azure,
                methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"],
            ),
        ]
        return Starlette(debug=False, routes=routes)

    async def _proxy(
        self,
        request: Request,
        upstream_base_url: str,
        path: str,
        upstream_prefix: str,
        auth_header: str | None,
    ) -> Response:
        upstream_path = path.lstrip("/")
        upstream_url = f"{upstream_base_url.rstrip('/')}/{upstream_path}"
        request_body = await request.body()

        headers = {
            key: value
            for key, value in request.headers.items()
            if key.lower() not in HOP_BY_HOP_HEADERS
        }
        headers["accept-encoding"] = "identity"
        if auth_header:
            headers["authorization"] = auth_header

        async with httpx.AsyncClient(
            timeout=self.config.nuget_relay_timeout_seconds,
            transport=self.transport,
        ) as client:
            upstream_response = await client.request(
                method=request.method,
                url=upstream_url,
                headers=headers,
                content=request_body,
            )

        content, content_type = self._rewrite_json_if_needed(
            response=upstream_response,
            request=request,
            upstream_base_url=upstream_base_url,
            upstream_prefix=upstream_prefix,
        )

        return Response(
            content=content,
            status_code=upstream_response.status_code,
            headers=self._response_headers(upstream_response),
            media_type=content_type,
        )

    def _rewrite_json_if_needed(
        self,
        response: httpx.Response,
        request: Request,
        upstream_base_url: str,
        upstream_prefix: str,
    ) -> tuple[bytes, str | None]:
        content_type = response.headers.get("content-type")
        if not content_type or "json" not in content_type.lower():
            return response.content, content_type

        try:
            payload = response.json()
        except json.JSONDecodeError:
            return response.content, content_type

        upstream_root = _origin(upstream_base_url)
        local_base = f"{request.base_url.scheme}://{request.base_url.netloc}{upstream_prefix}"

        rewritten = _rewrite_urls(payload, upstream_base_url.rstrip("/"), local_base)
        rewritten = _rewrite_urls(rewritten, upstream_root.rstrip("/"), local_base)

        return json.dumps(rewritten).encode("utf-8"), content_type

    @staticmethod
    def _response_headers(response: httpx.Response) -> dict[str, str]:
        return {
            key: value
            for key, value in response.headers.items()
            if key.lower() not in HOP_BY_HOP_HEADERS
        }


def _origin(url: str) -> str:
    parsed = urlsplit(url)
    return f"{parsed.scheme}://{parsed.netloc}".rstrip("/")


def _rewrite_urls(payload: object, old: str, new: str) -> object:
    if isinstance(payload, dict):
        return {key: _rewrite_urls(value, old, new) for key, value in payload.items()}
    if isinstance(payload, list):
        return [_rewrite_urls(item, old, new) for item in payload]
    if isinstance(payload, str):
        return payload.replace(old, new)
    return payload


def create_app(config: GatewayConfig, transport: httpx.AsyncBaseTransport | None = None) -> Starlette:
    return NuGetRelay(config, transport=transport).create_app()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the host-side NuGet relay.")
    parser.add_argument("--config", default="config/gateway.yaml", help="Path to the gateway YAML config.")
    args = parser.parse_args()

    config = load_config(args.config)
    uvicorn.run(
        create_app(config),
        host=config.nuget_relay_host,
        port=config.nuget_relay_port,
    )


if __name__ == "__main__":
    main()
