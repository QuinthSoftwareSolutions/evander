from __future__ import annotations

from ipaddress import ip_address
import json
import re
from typing import Any
from urllib.parse import urlencode, urljoin, urlsplit

import httpx

from evander.connectors.drivers import (
    ConnectorInvocationError,
    ConnectorUpstreamAuthenticationError,
)
from evander.connectors.models import PROTECTED_CONNECTOR_HEADERS


_HTTP_HEADER_NAME = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")
_HOSTNAME = re.compile(
    r"^(?=.{1,253}\.?$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)*"
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.?$",
    re.IGNORECASE,
)


class FreshserviceClient:
    """Bound Freshservice API client with constrained attachment egress."""

    def __init__(
        self,
        http_client: httpx.AsyncClient,
        *,
        base_url: str,
        api_headers: dict[str, str],
        attachment_hosts: set[str],
    ) -> None:
        self.http_client = http_client
        self.base_url = base_url
        self.api_headers = dict(api_headers)
        self.attachment_hosts = set(attachment_hosts)

    async def get_json(
        self,
        path: str,
        *,
        max_bytes: int,
        query: dict[str, object] | None = None,
    ) -> tuple[Any, int]:
        url = f"{self.base_url}{path}"
        if query:
            url = f"{url}?{urlencode(query)}"
        async with self.http_client.stream("GET", url, headers=self.api_headers) as response:
            content = await _read_response(response, max_bytes=max_bytes)
            _raise_for_status(response, api_request=True)
        try:
            return json.loads(content), response.status_code
        except (UnicodeDecodeError, ValueError) as exc:
            raise ConnectorInvocationError("Freshservice API response was not valid JSON") from exc

    async def download_attachment(
        self,
        url: str,
        *,
        max_bytes: int,
    ) -> tuple[bytes, str, int]:
        base_origin = _origin(self.base_url)
        _require_attachment_url(url, self.attachment_hosts)
        current_url = url
        current_headers = dict(self.api_headers) if _origin(url) == base_origin else {}
        for redirect_count in range(4):
            _require_attachment_url(current_url, self.attachment_hosts)
            async with self.http_client.stream(
                "GET", current_url, headers=current_headers
            ) as response:
                if response.is_redirect:
                    location = response.headers.get("location")
                    if not location or redirect_count >= 3:
                        raise ConnectorInvocationError(
                            "Freshservice attachment redirect limit exceeded"
                        )
                    next_url = urljoin(str(response.url), location)
                    _require_attachment_url(next_url, self.attachment_hosts)
                    if _origin(next_url) != _origin(current_url):
                        current_headers = {}
                    current_url = next_url
                    continue
                content = await _read_response(response, max_bytes=max_bytes)
                _raise_for_status(response, api_request=_origin(current_url) == base_origin)
                mime_type = (
                    response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                )
                return content, mime_type, response.status_code
        raise ConnectorInvocationError("Freshservice attachment redirect limit exceeded")


def validate_api_base_url(value: object) -> str:
    base_url = str(value or "").strip().rstrip("/")
    parts = urlsplit(base_url)
    host = (parts.hostname or "").casefold()
    if (
        parts.scheme.lower() != "https"
        or not host.endswith(".freshservice.com")
        or not _HOSTNAME.fullmatch(host)
        or parts.username
        or parts.password
        or parts.port not in {None, 443}
        or parts.query
        or parts.fragment
        or parts.path.rstrip("/") != "/api/v2"
    ):
        raise ValueError(
            "Freshservice connector config.base_url must be an "
            "https://*.freshservice.com/api/v2 URL"
        )
    return base_url


def validate_fixed_headers(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        raise ValueError("Freshservice connector config.headers must be an object")
    result: dict[str, str] = {}
    for name, raw_value in value.items():
        header_name = str(name or "")
        normalized = header_name.strip().lower()
        if not normalized or normalized in PROTECTED_CONNECTOR_HEADERS:
            raise ValueError(f"Freshservice connector header {name!r} is reserved")
        if not _HTTP_HEADER_NAME.fullmatch(header_name):
            raise ValueError(
                f"Freshservice connector header {name!r} must be a valid HTTP field name"
            )
        if not isinstance(raw_value, str):
            raise ValueError("Freshservice connector fixed header values must be strings")
        try:
            encoded = raw_value.encode("ascii")
        except UnicodeEncodeError as exc:
            raise ValueError(
                f"Freshservice connector header {name!r} value must contain only ASCII characters"
            ) from exc
        if (
            raw_value[:1] in {" ", "\t"}
            or raw_value[-1:] in {" ", "\t"}
            or any(byte == 127 or (byte < 32 and byte != 9) for byte in encoded)
        ):
            raise ValueError(
                f"Freshservice connector header {name!r} value contains invalid HTTP characters"
            )
        result[header_name] = raw_value
    return result


def validate_attachment_hosts(base_url: str, value: object) -> set[str]:
    if not isinstance(value, list):
        raise ValueError("Freshservice config.attachment_host_allowlist must be a list")
    hosts = {(urlsplit(base_url).hostname or "").casefold()}
    for raw_host in value:
        host = str(raw_host or "").strip().rstrip(".").casefold()
        if not host or "." not in host or not _HOSTNAME.fullmatch(host) or _is_ip_address(host):
            raise ValueError(
                "Freshservice attachment_host_allowlist entries must be exact DNS hostnames"
            )
        hosts.add(host)
    return hosts


def _is_ip_address(value: str) -> bool:
    try:
        ip_address(value)
    except ValueError:
        return False
    return True


def _require_attachment_url(url: str, allowed_hosts: set[str]) -> None:
    parts = urlsplit(url)
    host = (parts.hostname or "").rstrip(".").casefold()
    try:
        port = parts.port
    except ValueError as exc:
        raise ConnectorInvocationError(
            "Freshservice attachment URL is outside the configured HTTPS host allowlist"
        ) from exc
    if (
        parts.scheme.lower() != "https"
        or not host
        or host not in allowed_hosts
        or parts.username
        or parts.password
        or port not in {None, 443}
        or parts.fragment
    ):
        raise ConnectorInvocationError(
            "Freshservice attachment URL is outside the configured HTTPS host allowlist"
        )


async def _read_response(response: httpx.Response, *, max_bytes: int) -> bytes:
    declared_length = response.headers.get("content-length")
    if declared_length:
        try:
            if int(declared_length) > max_bytes:
                raise ConnectorInvocationError("Freshservice response exceeded max_response_bytes")
        except ValueError:
            pass
    content = bytearray()
    async for chunk in response.aiter_bytes():
        content.extend(chunk)
        if len(content) > max_bytes:
            raise ConnectorInvocationError("Freshservice response exceeded max_response_bytes")
    return bytes(content)


def _raise_for_status(response: httpx.Response, *, api_request: bool) -> None:
    if response.status_code == 401 and api_request:
        raise ConnectorUpstreamAuthenticationError("Freshservice rejected authentication")
    if response.status_code < 200 or response.status_code >= 300:
        raise ConnectorInvocationError(
            f"Freshservice upstream returned HTTP {response.status_code}"
        )


def _origin(url: str) -> tuple[str, str, int | None]:
    parts = urlsplit(url)
    scheme = parts.scheme.casefold()
    port = parts.port
    if port is None:
        port = 443 if scheme == "https" else 80 if scheme == "http" else None
    return scheme, (parts.hostname or "").casefold(), port
