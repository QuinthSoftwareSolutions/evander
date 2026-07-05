from __future__ import annotations

import argparse
from pathlib import Path
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

import httpx
import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from evander.bootstrap import load_runtime_gateway_config, resolve_runtime_dir
from evander.config import GatewayConfig
from evander.models import HttpTargetMetadata, SessionManifest
from evander.provider_config import ProviderConfigResolver
from evander.session_auth import (
    MCP_SESSION_TOKEN_HEADER_NAMES,
    MCP_SESSION_TOKEN_QUERY_PARAM_NAMES,
    verify_mcp_session_token,
)

HOP_BY_HOP_HEADERS = {
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
    *MCP_SESSION_TOKEN_HEADER_NAMES,
}

MCP_SESSION_TOKEN_REQUIRED_MESSAGE = "MCP session token is required for this session"


class HttpTargetRelay:
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

    def create_app(self) -> Starlette:
        relay = self

        async def health(_: Request) -> JSONResponse:
            return JSONResponse(
                {
                    "status": "ok",
                    "relay_mode": "session_scoped_http_targets",
                    "auth_mode": "transparent_guest_managed",
                    "forwards_authorization": True,
                }
            )

        async def proxy(request: Request) -> Response:
            try:
                resolved_config, http_target = relay._resolve_request_target(request)
            except PermissionError as exc:
                return JSONResponse({"error": str(exc)}, status_code=401)
            except ValueError as exc:
                return JSONResponse({"error": str(exc)}, status_code=404)

            upstream_url = self._upstream_http_url(
                base_url=http_target.base_url,
                upstream_path=request.path_params.get("path", ""),
                query_items=request.query_params.multi_items(),
            )
            request_body = await request.body()
            headers = relay._request_headers_for_upstream(request)
            headers["accept-encoding"] = "identity"

            async with httpx.AsyncClient(
                timeout=resolved_config.http_target_relay_timeout_seconds,
                transport=relay.transport,
                follow_redirects=False,
            ) as client:
                upstream_response = await client.request(
                    method=request.method,
                    url=upstream_url,
                    headers=headers,
                    content=request_body,
                )
                redirect_location = upstream_response.headers.get("location")
                if redirect_location is not None:
                    redirect_location = self._relay_redirect_location(
                        redirect_location,
                        base_url=http_target.base_url,
                        session_id=request.path_params["session_id"],
                        alias=request.path_params["alias"],
                        mcp_session_token=relay._mcp_session_token_from_path(request),
                        mcp_session_query_token=relay._mcp_session_token_from_query(
                            request
                        ),
                    )
                    if redirect_location is None:
                        return JSONResponse(
                            {
                                "error": "upstream redirect target is outside the allowlisted target origin"
                            },
                            status_code=502,
                        )
                return Response(
                    content=upstream_response.content,
                    status_code=upstream_response.status_code,
                    headers=relay._response_headers(
                        upstream_response, redirect_location=redirect_location
                    ),
                    media_type=upstream_response.headers.get("content-type"),
                )

        return Starlette(
            debug=False,
            routes=[
                Route("/healthz", health, methods=["GET"]),
                Route(
                    "/auth/{mcp_session_token}/session/{session_id}/target/{alias}",
                    proxy,
                    methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"],
                ),
                Route(
                    "/auth/{mcp_session_token}/session/{session_id}/target/{alias}/{path:path}",
                    proxy,
                    methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"],
                ),
                Route(
                    "/session/{session_id}/target/{alias}",
                    proxy,
                    methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"],
                ),
                Route(
                    "/session/{session_id}/target/{alias}/{path:path}",
                    proxy,
                    methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"],
                ),
            ],
        )

    def _resolve_request_target(self, request: Request) -> tuple[GatewayConfig, HttpTargetMetadata]:
        session_id = self._session_id_from_path_params(request.path_params)
        alias = self._alias_from_path_params(request.path_params)
        session = self.provider_config_resolver.get_session_manifest(session_id)
        self._ensure_mcp_session_token(session, request)
        http_target = self._http_target_for_alias(session, alias)
        resolved_config = self.provider_config_resolver.resolve_for_session_manifest(session)
        return resolved_config, http_target

    @staticmethod
    def _session_id_from_path_params(path_params: dict[str, object]) -> str:
        session_id = str(path_params.get("session_id") or "").strip()
        if not session_id:
            raise ValueError("session_id path parameter is required")
        return session_id

    @staticmethod
    def _alias_from_path_params(path_params: dict[str, object]) -> str:
        alias = str(path_params.get("alias") or "").strip()
        if not alias:
            raise ValueError("alias path parameter is required")
        return alias

    @staticmethod
    def _http_target_for_alias(session: SessionManifest, alias: str) -> HttpTargetMetadata:
        try:
            return session.http_target(alias)
        except ValueError as exc:
            raise ValueError(f"session {session.session_id} {exc}") from exc

    @classmethod
    def _ensure_mcp_session_token(cls, session: SessionManifest, request: Request) -> None:
        if verify_mcp_session_token(session, cls._mcp_session_token_from_request(request)):
            return
        raise PermissionError(MCP_SESSION_TOKEN_REQUIRED_MESSAGE)

    @staticmethod
    def _mcp_session_token_from_request(request: Request) -> str | None:
        token = HttpTargetRelay._mcp_session_token_from_query(request)
        if token:
            return token
        token = HttpTargetRelay._mcp_session_token_from_path(request)
        if token:
            return token
        for name in MCP_SESSION_TOKEN_HEADER_NAMES:
            token = str(request.headers.get(name) or "").strip()
            if token:
                return token
        return None

    @staticmethod
    def _mcp_session_token_from_query(request: Request) -> str | None:
        for name in MCP_SESSION_TOKEN_QUERY_PARAM_NAMES:
            token = str(request.query_params.get(name) or "").strip()
            if token:
                return token
        return None

    @staticmethod
    def _mcp_session_token_from_path(request: Request) -> str | None:
        token = str(request.path_params.get("mcp_session_token") or "").strip()
        return token or None

    @staticmethod
    def _response_headers(
        response: httpx.Response,
        *,
        redirect_location: str | None = None,
    ) -> dict[str, str]:
        headers = {
            key: value
            for key, value in response.headers.items()
            if key.lower() not in HOP_BY_HOP_HEADERS
        }
        if redirect_location is not None:
            headers["location"] = redirect_location
        return headers

    @classmethod
    def _request_headers_for_upstream(cls, request: Request) -> dict[str, str]:
        blocked_headers = set(HOP_BY_HOP_HEADERS)
        if cls._request_has_mcp_session_token(request):
            blocked_headers.update({"referer", "referrer"})
        return {
            key: value
            for key, value in request.headers.items()
            if key.lower() not in blocked_headers
        }

    @classmethod
    def _request_has_mcp_session_token(cls, request: Request) -> bool:
        return cls._mcp_session_token_from_request(request) is not None

    @staticmethod
    def _filtered_query_string(query_items: list[tuple[str, str]]) -> str:
        filtered = [
            (key, value)
            for key, value in query_items
            if key not in MCP_SESSION_TOKEN_QUERY_PARAM_NAMES
        ]
        return urlencode(filtered, doseq=True)

    @classmethod
    def _upstream_http_url(
        cls, *, base_url: str, upstream_path: str, query_items: list[tuple[str, str]]
    ) -> str:
        normalized_base = base_url.rstrip("/")
        normalized_path = upstream_path.lstrip("/")
        upstream_url = (
            normalized_base if not normalized_path else f"{normalized_base}/{normalized_path}"
        )
        filtered_query = cls._filtered_query_string(query_items)
        if filtered_query:
            upstream_url = f"{upstream_url}?{filtered_query}"
        return upstream_url

    @staticmethod
    def _relay_redirect_location(
        location: str | None,
        *,
        base_url: str,
        session_id: str,
        alias: str,
        mcp_session_token: str | None = None,
        mcp_session_query_token: str | None = None,
    ) -> str | None:
        if not location:
            return location
        location_parts = urlsplit(location)
        if not location_parts.scheme and not location_parts.netloc and not location.startswith("/"):
            return location
        base_parts = urlsplit(base_url)
        if location_parts.scheme or location_parts.netloc:
            if (
                location_parts.scheme,
                location_parts.netloc,
            ) != (
                base_parts.scheme,
                base_parts.netloc,
            ):
                return None
        relay_upstream_path = HttpTargetRelay._strip_target_base_path(
            location_parts.path or "/",
            base_path=base_parts.path,
        )
        if relay_upstream_path is None:
            return None
        auth_prefix = (
            f"/auth/{quote(mcp_session_token, safe='')}" if mcp_session_token else ""
        )
        relay_path = f"{auth_prefix}/session/{session_id}/target/{alias}{relay_upstream_path}"
        relay_query = location_parts.query
        if mcp_session_query_token:
            query_items = parse_qsl(relay_query, keep_blank_values=True)
            if not any(key in MCP_SESSION_TOKEN_QUERY_PARAM_NAMES for key, _ in query_items):
                query_items.append(
                    (MCP_SESSION_TOKEN_QUERY_PARAM_NAMES[0], mcp_session_query_token)
                )
                relay_query = urlencode(query_items, doseq=True)
        return urlunsplit(("", "", relay_path, relay_query, location_parts.fragment))

    @staticmethod
    def _strip_target_base_path(path: str, *, base_path: str) -> str | None:
        normalized_path = path or "/"
        normalized_base_path = base_path or ""
        if not normalized_base_path or normalized_base_path == "/":
            return normalized_path
        normalized_base_path = normalized_base_path.rstrip("/")
        if normalized_path == normalized_base_path:
            return "/"
        if normalized_path.startswith(f"{normalized_base_path}/"):
            return normalized_path[len(normalized_base_path) :]
        return None


def create_app(
    config: GatewayConfig, transport: httpx.AsyncBaseTransport | None = None
) -> Starlette:
    return HttpTargetRelay(
        config,
        transport=transport,
        provider_config_resolver=ProviderConfigResolver(config),
    ).create_app()


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
    parser = argparse.ArgumentParser(
        description="Run the host-side session-scoped HTTP target relay."
    )
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
        host=config.http_target_relay_host,
        port=config.http_target_relay_port,
    )


if __name__ == "__main__":
    main()
