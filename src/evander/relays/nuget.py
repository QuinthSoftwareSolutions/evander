from __future__ import annotations

import argparse
import base64
from collections.abc import Collection
import json
import os
from pathlib import Path
import re
from urllib.parse import quote, unquote, urlsplit, urlunsplit

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
PATH_SEGMENT_SAFE_CHARS = ":@!$&'()*+,;=-._~"
MAX_PATH_DECODE_PASSES = 5
AZURE_FEED_ID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


class NuGetRelay:
    def __init__(
        self, config: GatewayConfig, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self.config = config
        self.transport = transport
        self._azure_feed_ids: set[str] = set()

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
        try:
            upstream_url = _build_upstream_url(
                upstream_base_url,
                upstream_path,
                allowed_feed_ids=self._azure_feed_ids,
            )
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
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
            upstream_url=upstream_url,
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
        upstream_url: str,
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
        if _is_configured_service_index_url(upstream_url, upstream_base_url):
            self._azure_feed_ids.update(
                _advertised_azure_feed_ids(payload, upstream_base_url=upstream_base_url)
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


def _configured_feed_base_segments(base_segments: list[str]) -> list[str]:
    if base_segments[-2:] == ["v3", "index.json"]:
        return base_segments[:-2]
    return base_segments


def _build_upstream_url(
    upstream_base_url: str,
    upstream_path: str,
    *,
    allowed_feed_ids: Collection[str] = (),
) -> str:
    base_parts = urlsplit(upstream_base_url)
    base_segments = _safe_path_segments(
        base_parts.path,
        field_name="configured NuGet feed base path",
    )
    configured_feed_base_segments = _configured_feed_base_segments(base_segments)
    upstream_segments = _safe_path_segments(
        upstream_path,
        field_name="NuGet relay path",
    )
    if not upstream_segments:
        return urlunsplit(
            (
                base_parts.scheme,
                base_parts.netloc,
                _path_from_segments(base_segments, root_if_empty=base_parts.path == "/"),
                "",
                "",
            )
        )

    # Azure Artifacts service indexes can advertise absolute-origin resource paths
    # like "exampleindustry/_packaging/<feed-guid>/nuget/v3/flat2/...". When those
    # are rewritten onto the guest-facing relay and called back, appending them to
    # the configured feed base duplicates the feed path. Honor that shortcut for
    # the configured feed name and for feed IDs observed in the service index.
    if _is_origin_rooted_packaging_path(upstream_segments, base_segments):
        if not _packaging_path_matches_configured_feed(
            upstream_segments,
            base_segments,
            allowed_feed_ids=allowed_feed_ids,
        ):
            raise ValueError("NuGet relay path is outside the configured feed")
        target_segments = upstream_segments
    else:
        target_segments = [*configured_feed_base_segments, *upstream_segments]

    return urlunsplit(
        (
            base_parts.scheme,
            base_parts.netloc,
            _path_from_segments(target_segments, root_if_empty=base_parts.path == "/"),
            "",
            "",
        )
    )


def _is_origin_rooted_packaging_path(
    upstream_segments: list[str],
    base_segments: list[str],
) -> bool:
    feed_scope = _azure_feed_scope(base_segments)
    if feed_scope is None:
        return False
    base_prefix, _configured_feed, _base_suffix = feed_scope
    packaging_scope = [*base_prefix, "_packaging"]
    return upstream_segments[: len(packaging_scope)] == packaging_scope


def _advertised_azure_feed_ids(payload: object, upstream_base_url: str) -> set[str]:
    base_segments = _safe_path_segments(
        urlsplit(upstream_base_url).path,
        field_name="configured NuGet feed base path",
    )
    feed_scope = _azure_feed_scope(base_segments)
    if feed_scope is None:
        return set()

    upstream_root = _origin(upstream_base_url)
    allowed_feed_ids: set[str] = set()
    resources = payload.get("resources") if isinstance(payload, dict) else None
    if not isinstance(resources, list):
        return set()
    for resource in resources:
        if not isinstance(resource, dict):
            continue
        value = resource.get("@id")
        if not isinstance(value, str):
            continue
        if not value.startswith(f"{upstream_root}/"):
            continue
        try:
            segments = _safe_path_segments(
                urlsplit(value).path,
                field_name="advertised NuGet resource path",
            )
        except ValueError:
            continue
        feed_id = _matching_packaging_feed_id(segments, base_segments)
        if feed_id is not None:
            allowed_feed_ids.add(feed_id.lower())
    return allowed_feed_ids


def _is_configured_service_index_url(upstream_url: str, upstream_base_url: str) -> bool:
    upstream_parts = urlsplit(upstream_url)
    base_parts = urlsplit(upstream_base_url)
    if upstream_parts.scheme.lower() != base_parts.scheme.lower():
        return False
    if upstream_parts.netloc.lower() != base_parts.netloc.lower():
        return False
    try:
        upstream_segments = _safe_path_segments(
            upstream_parts.path,
            field_name="NuGet upstream path",
        )
        base_segments = _safe_path_segments(
            base_parts.path,
            field_name="configured NuGet feed base path",
        )
    except ValueError:
        return False
    if base_segments[-2:] == ["v3", "index.json"]:
        return upstream_segments == base_segments
    return upstream_segments == [*base_segments, "v3", "index.json"]


def _packaging_path_matches_configured_feed(
    upstream_segments: list[str],
    base_segments: list[str],
    *,
    allowed_feed_ids: Collection[str],
) -> bool:
    if upstream_segments[: len(base_segments)] == base_segments:
        return True
    feed_id = _matching_packaging_feed_id(upstream_segments, base_segments)
    return feed_id is not None and feed_id.lower() in {value.lower() for value in allowed_feed_ids}


def _matching_packaging_feed_id(
    upstream_segments: list[str],
    base_segments: list[str],
) -> str | None:
    feed_scope = _azure_feed_scope(base_segments)
    if feed_scope is None:
        return None
    base_prefix, configured_feed, base_suffix = feed_scope

    try:
        packaging_index = upstream_segments.index("_packaging")
    except ValueError:
        return None
    if upstream_segments[:packaging_index] != base_prefix:
        return None
    feed_index = packaging_index + 1
    if len(upstream_segments) <= feed_index:
        return None
    feed = upstream_segments[feed_index]
    suffix_start = feed_index + 1
    suffix_end = suffix_start + len(base_suffix)
    if upstream_segments[suffix_start:suffix_end] != base_suffix:
        return None
    if feed == configured_feed:
        return feed
    if AZURE_FEED_ID_RE.fullmatch(feed):
        return feed
    return None


def _azure_feed_scope(base_segments: list[str]) -> tuple[list[str], str, list[str]] | None:
    try:
        packaging_index = base_segments.index("_packaging")
    except ValueError:
        return None
    feed_index = packaging_index + 1
    if len(base_segments) <= feed_index:
        return None
    base_suffix = base_segments[feed_index + 1 :]
    if base_suffix[-2:] == ["v3", "index.json"]:
        base_suffix = base_suffix[:-1]
    return (
        base_segments[:packaging_index],
        base_segments[feed_index],
        base_suffix,
    )


def _should_follow_redirects(upstream_path: str) -> bool:
    normalized = upstream_path.lower()
    return normalized.endswith(".nupkg") or normalized.endswith(".snupkg")


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


def _path_from_segments(segments: list[str], *, root_if_empty: bool) -> str:
    if not segments:
        return "/" if root_if_empty else ""
    encoded_segments = [quote(segment, safe=PATH_SEGMENT_SAFE_CHARS) for segment in segments]
    return f"/{'/'.join(encoded_segments)}"


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


def create_app(
    config: GatewayConfig, transport: httpx.AsyncBaseTransport | None = None
) -> Starlette:
    resolved_config = ProviderConfigResolver(config).resolve_for_directory("root")
    return NuGetRelay(resolved_config, transport=transport).create_app()


def _project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _bootstrap_runtime_config(*, runtime_dir: Path | None = None) -> GatewayConfig:
    return load_runtime_gateway_config(
        runtime_dir=resolve_runtime_dir(runtime_dir=runtime_dir or _project_root() / "runtime")
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the host-side NuGet relay.")
    parser.add_argument("--runtime-dir", type=Path, help="Runtime directory root.")
    args = parser.parse_args()

    config = _bootstrap_runtime_config(runtime_dir=args.runtime_dir)
    uvicorn.run(
        create_app(config),
        host=config.nuget_relay_host,
        port=config.nuget_relay_port,
    )


if __name__ == "__main__":
    main()
