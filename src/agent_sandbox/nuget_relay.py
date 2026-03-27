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
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

from .config import GatewayConfig, load_config
from .provider_config import ProviderConfigResolver
from .vm_defaults_config import VmDefaultsConfigResolver

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
        upstream_url = _build_upstream_url(upstream_base_url, upstream_path)
        request_body = await request.body()

        headers = {
            key: value
            for key, value in request.headers.items()
            if key.lower() not in HOP_BY_HOP_HEADERS
        }
        headers["accept-encoding"] = "identity"
        if auth_header:
            headers["authorization"] = auth_header

        follow_redirects = _should_follow_redirects(upstream_path)
        client = httpx.AsyncClient(
            timeout=self.config.nuget_relay_timeout_seconds,
            transport=self.transport,
            follow_redirects=follow_redirects,
        )
        upstream_response = await client.send(
            client.build_request(
                method=request.method,
                url=upstream_url,
                headers=headers,
                content=request_body,
            ),
            stream=True,
        )

        content_type = upstream_response.headers.get("content-type")
        if not content_type or "json" not in content_type.lower():
            return StreamingResponse(
                self._stream_upstream_response(client, upstream_response),
                status_code=upstream_response.status_code,
                headers=self._response_headers(upstream_response),
                media_type=content_type,
            )

        try:
            response_body = await upstream_response.aread()
        finally:
            await upstream_response.aclose()
            await client.aclose()

        content, content_type = self._rewrite_json_if_needed(
            response_body=response_body,
            response_headers=upstream_response.headers,
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

    async def _stream_upstream_response(
        self,
        client: httpx.AsyncClient,
        upstream_response: httpx.Response,
    ):
        try:
            async for chunk in upstream_response.aiter_raw():
                yield chunk
        finally:
            await upstream_response.aclose()
            await client.aclose()

    def _rewrite_json_if_needed(
        self,
        response_body: bytes,
        response_headers: httpx.Headers,
        request: Request,
        upstream_base_url: str,
        upstream_prefix: str,
    ) -> tuple[bytes, str | None]:
        content_type = response_headers.get("content-type")
        if not content_type or "json" not in content_type.lower():
            return response_body, content_type

        try:
            payload = json.loads(response_body)
        except json.JSONDecodeError:
            return response_body, content_type

        payload = _strip_insecure_repository_signatures(
            payload=payload,
            request=request,
            upstream_base_url=upstream_base_url,
        )

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


def _build_upstream_url(upstream_base_url: str, upstream_path: str) -> str:
    if not upstream_path:
        return upstream_base_url.rstrip("/")

    # Azure Artifacts service indexes can advertise absolute-origin resource paths
    # like "exampleindustry/_packaging/<feed-guid>/nuget/v3/flat2/...". When those
    # are rewritten onto the guest-facing relay and called back, appending them to
    # the configured feed base duplicates the feed path. If the path already looks
    # like a feed-rooted Azure resource, proxy from the upstream origin instead.
    path_parts = upstream_path.split("/")
    if "_packaging" in path_parts:
        return f"{_origin(upstream_base_url)}/{upstream_path}"

    return f"{upstream_base_url.rstrip('/')}/{upstream_path}"


def _should_follow_redirects(upstream_path: str) -> bool:
    normalized = upstream_path.lower()
    return normalized.endswith(".nupkg") or normalized.endswith(".snupkg")


def _strip_insecure_repository_signatures(
    payload: object,
    request: Request,
    upstream_base_url: str,
) -> object:
    if request.base_url.scheme.lower() == "https":
        return payload

    if request.path_params.get("path", "").lstrip("/") != "v3/index.json":
        return payload

    if not isinstance(payload, dict):
        return payload

    resources = payload.get("resources")
    if not isinstance(resources, list):
        return payload

    filtered_resources = []
    for resource in resources:
        if not isinstance(resource, dict):
            filtered_resources.append(resource)
            continue

        resource_type = resource.get("@type", "")
        if isinstance(resource_type, str) and resource_type.startswith("RepositorySignatures/"):
            continue

        filtered_resources.append(resource)

    updated_payload = dict(payload)
    updated_payload["resources"] = filtered_resources
    return updated_payload


def _rewrite_urls(payload: object, old: str, new: str) -> object:
    if isinstance(payload, dict):
        return {key: _rewrite_urls(value, old, new) for key, value in payload.items()}
    if isinstance(payload, list):
        return [_rewrite_urls(item, old, new) for item in payload]
    if isinstance(payload, str):
        return payload.replace(old, new)
    return payload


def create_app(config: GatewayConfig, transport: httpx.AsyncBaseTransport | None = None) -> Starlette:
    resolved_config = ProviderConfigResolver(config).resolve_for_directory("root")
    return NuGetRelay(resolved_config, transport=transport).create_app()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the host-side NuGet relay.")
    parser.add_argument("--config", default="config/gateway.yaml", help="Path to the gateway YAML config.")
    args = parser.parse_args()

    config = load_config(args.config)
    vm_defaults = VmDefaultsConfigResolver(config).resolve_for_directory("root")
    uvicorn.run(
        create_app(config),
        host=vm_defaults.nuget_relay_host,
        port=vm_defaults.nuget_relay_port,
    )


if __name__ == "__main__":
    main()
