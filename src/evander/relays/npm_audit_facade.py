from __future__ import annotations

import argparse
from pathlib import Path
from urllib.parse import SplitResult, urlsplit, urlunsplit

import httpx
import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

from evander.bootstrap import load_runtime_gateway_config, resolve_runtime_dir
from evander.config import GatewayConfig
from evander.provider_config import ProviderConfigResolver

HOP_BY_HOP_HEADERS = {
    "authorization",
    "connection",
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

RESPONSE_HEADER_EXCLUDES = HOP_BY_HOP_HEADERS | {"content-encoding"}

AUDIT_ENDPOINTS = {
    "/-/npm/v1/security/advisories/bulk",
    "/-/npm/v1/security/audits/quick",
}


class NpmAuditFacade:
    def __init__(
        self, config: GatewayConfig, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self.config = config
        self.transport = transport

    def create_app(self) -> Starlette:
        relay = self

        async def health(_: Request) -> JSONResponse:
            return JSONResponse(
                {
                    "status": "ok",
                    "audit_upstream": relay.config.npm_audit_facade_audit_registry_url,
                    "package_upstream": relay._package_registry_url(),
                }
            )

        async def proxy(request: Request) -> Response:
            upstream_base_url = relay._upstream_base_url(request)
            upstream_url = relay._upstream_http_url(
                base_url=upstream_base_url,
                raw_path=relay._raw_path(request),
                raw_query=relay._raw_query_string(request),
            )
            request_body = await request.body()

            headers = {
                key: value
                for key, value in request.headers.items()
                if key.lower() not in HOP_BY_HOP_HEADERS
            }
            headers["accept-encoding"] = "identity"

            client = httpx.AsyncClient(
                timeout=relay.config.npm_audit_facade_timeout_seconds,
                transport=relay.transport,
                follow_redirects=False,
            )
            try:
                upstream_response = await client.send(
                    client.build_request(
                        method=request.method,
                        url=upstream_url,
                        headers=headers,
                        content=request_body,
                    ),
                    stream=True,
                )
            except Exception:
                await client.aclose()
                raise

            if relay._is_audit_request(request):
                try:
                    response_body = await upstream_response.aread()
                finally:
                    await upstream_response.aclose()
                    await client.aclose()
                return Response(
                    content=response_body,
                    status_code=upstream_response.status_code,
                    headers=relay._response_headers(upstream_response),
                    media_type=upstream_response.headers.get("content-type"),
                )

            if request.method.upper() == "HEAD":
                try:
                    await upstream_response.aclose()
                finally:
                    await client.aclose()
                return Response(
                    content=b"",
                    status_code=upstream_response.status_code,
                    headers=relay._response_headers(upstream_response),
                    media_type=upstream_response.headers.get("content-type"),
                )

            return StreamingResponse(
                relay._stream_upstream_response(client, upstream_response),
                status_code=upstream_response.status_code,
                headers=relay._response_headers(upstream_response),
                media_type=upstream_response.headers.get("content-type"),
            )

        routes = [
            Route("/healthz", health, methods=["GET"]),
            Route(
                "/{path:path}",
                proxy,
                methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"],
            ),
        ]
        return Starlette(debug=False, routes=routes)

    def _upstream_base_url(self, request: Request) -> str:
        if self._is_audit_request(request):
            return self.config.npm_audit_facade_audit_registry_url
        return self._package_registry_url()

    @staticmethod
    def _is_audit_request(request: Request) -> bool:
        return request.method.upper() == "POST" and request.url.path in AUDIT_ENDPOINTS

    def _package_registry_url(self) -> str:
        package_registry_url = self.config.npm_audit_facade_package_registry_url
        if package_registry_url:
            return package_registry_url
        return _service_url_from_gateway_url(
            self.config.gateway_url,
            port=8081,
            path="/repository/npm-all/",
        )

    @staticmethod
    def _raw_path(request: Request) -> str:
        raw_path = request.scope.get("raw_path", b"")
        if isinstance(raw_path, bytes):
            return raw_path.decode("ascii")
        return str(raw_path)

    @staticmethod
    def _raw_query_string(request: Request) -> str:
        raw_query_string = request.scope.get("query_string", b"")
        if isinstance(raw_query_string, bytes):
            return raw_query_string.decode("ascii")
        return str(raw_query_string)

    @staticmethod
    def _upstream_http_url(*, base_url: str, raw_path: str, raw_query: str) -> str:
        parts = urlsplit(base_url)
        base_path = parts.path.rstrip("/")
        normalized_path = "/" + raw_path.lstrip("/")
        if base_path and (
            normalized_path == base_path or normalized_path.startswith(f"{base_path}/")
        ):
            upstream_base = urlunsplit(
                SplitResult(
                    scheme=parts.scheme,
                    netloc=parts.netloc,
                    path="",
                    query="",
                    fragment="",
                )
            ).rstrip("/")
            upstream_url = f"{upstream_base}{normalized_path}"
        else:
            normalized_base = base_url.rstrip("/")
            normalized_path_without_slash = raw_path.lstrip("/")
            upstream_url = (
                normalized_base
                if not normalized_path_without_slash
                else f"{normalized_base}/{normalized_path_without_slash}"
            )
        if raw_query:
            upstream_url = f"{upstream_url}?{raw_query}"
        return upstream_url

    @staticmethod
    async def _stream_upstream_response(
        client: httpx.AsyncClient,
        upstream_response: httpx.Response,
    ):
        try:
            async for chunk in upstream_response.aiter_raw():
                yield chunk
        finally:
            await upstream_response.aclose()
            await client.aclose()

    @staticmethod
    def _response_headers(response: httpx.Response) -> dict[str, str]:
        return {
            key: value
            for key, value in response.headers.items()
            if key.lower() not in RESPONSE_HEADER_EXCLUDES
        }


def create_app(
    config: GatewayConfig, transport: httpx.AsyncBaseTransport | None = None
) -> Starlette:
    resolved_config = ProviderConfigResolver(config).resolve_for_directory("root")
    return NpmAuditFacade(resolved_config, transport=transport).create_app()


def _project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _bootstrap_runtime_config(*, legacy_config_path: Path | None = None) -> GatewayConfig:
    return load_runtime_gateway_config(
        runtime_dir=resolve_runtime_dir(
            runtime_dir=_project_root() / "runtime", legacy_config_path=legacy_config_path
        ),
        legacy_config_path=legacy_config_path,
    )


def _service_url_from_gateway_url(gateway_url: str, *, port: int, path: str) -> str:
    parts = urlsplit(gateway_url)
    host = parts.hostname or "127.0.0.1"
    netloc = host
    if ":" in host and not host.startswith("["):
        netloc = f"[{host}]"
    if parts.username:
        credentials = parts.username
        if parts.password:
            credentials = f"{credentials}:{parts.password}"
        netloc = f"{credentials}@{netloc}"
    netloc = f"{netloc}:{port}"
    normalized = SplitResult(
        scheme="http",
        netloc=netloc,
        path=path,
        query="",
        fragment="",
    )
    return urlunsplit(normalized)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the host-side npm audit facade.")
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
        host=config.npm_audit_facade_host,
        port=config.npm_audit_facade_port,
    )


if __name__ == "__main__":
    main()
