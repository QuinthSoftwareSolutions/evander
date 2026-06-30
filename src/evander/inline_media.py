from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Protocol
from urllib.parse import urljoin, urlparse

import httpx

from evander.models import InlineMediaRef


@dataclass(frozen=True)
class InlineMediaDownload:
    content: bytes
    filename: str
    mime_type: str | None = None


class InlineMediaProvider(Protocol):
    """Adapter for provider-hosted inline media referenced by comments/messages."""

    provider_name: str

    def list_inline_media(
        self,
        *,
        source_kind: str,
        resource_id: str,
        resource: Mapping[str, Any] | None = None,
        comment_id: str | None = None,
    ) -> list[InlineMediaRef]: ...

    def download_inline_media(
        self,
        *,
        media: InlineMediaRef,
        raw: Mapping[str, Any] | None = None,
    ) -> InlineMediaDownload: ...


def inline_media_download_from_embedded_payload(
    media: InlineMediaRef,
    raw: Mapping[str, Any] | None,
) -> InlineMediaDownload | None:
    """Return embedded bytes when tests/stubs include media content directly."""

    if raw is None:
        return None
    if raw.get("content_base64"):
        return InlineMediaDownload(
            base64.b64decode(str(raw["content_base64"])), media.filename, media.mime_type
        )
    data = raw.get("content") or raw.get("content_bytes") or raw.get("bytes")
    if isinstance(data, bytes):
        return InlineMediaDownload(data, media.filename, media.mime_type)
    if isinstance(data, str) and data:
        if raw.get("encoding") == "base64":
            return InlineMediaDownload(base64.b64decode(data), media.filename, media.mime_type)
        return InlineMediaDownload(data.encode("utf-8"), media.filename, media.mime_type)
    return None


_GITHUB_INLINE_MEDIA_HOSTS = {
    "api.github.com",
    "github.com",
    "user-images.githubusercontent.com",
    "private-user-images.githubusercontent.com",
    "media.githubusercontent.com",
    "raw.githubusercontent.com",
}

_SECRET_METADATA_KEYS = {
    "authorization",
    "token",
    "access_token",
    "accesstoken",
    "content",
    "contentbytes",
    "content_bytes",
    "content_base64",
    "data",
    "bytes",
}


def normalize_inline_media_provider(provider: str) -> str:
    normalized = str(provider or "").strip().lower()
    if normalized == "ado":
        return "azure_devops"
    return normalized


def sanitize_inline_media_metadata(value: Any) -> Any:
    """Drop secret/content-bearing fields from inline-media metadata recursively."""

    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item_value in value.items():
            normalized_key = str(key).replace("-", "_").lower()
            if normalized_key in _SECRET_METADATA_KEYS:
                continue
            result[str(key)] = sanitize_inline_media_metadata(item_value)
        return result
    if isinstance(value, list):
        return [sanitize_inline_media_metadata(item) for item in value]
    return value



def require_provider_media_url(
    *,
    provider: str,
    url: str,
    allowed_hosts: Iterable[str] | None = None,
) -> None:
    """Reject inline-media URLs outside a provider-specific HTTPS host allowlist."""

    parsed = urlparse(str(url or ""))
    if parsed.scheme.lower() != "https":
        raise ValueError("Inline media URL must be https")
    host = (parsed.hostname or "").strip().lower()
    if not host:
        raise ValueError("Inline media URL must include a hostname")
    canonical_provider = normalize_inline_media_provider(provider)
    if canonical_provider == "github":
        hosts = set(_GITHUB_INLINE_MEDIA_HOSTS)
    elif canonical_provider == "azure_devops":
        hosts = {item.strip().lower() for item in allowed_hosts or [] if item and item.strip()}
    else:
        hosts = {item.strip().lower() for item in allowed_hosts or [] if item and item.strip()}
    if host not in hosts:
        raise ValueError(f"Inline media URL host is not allowlisted: {host}")


def read_http_response_with_limit(response: httpx.Response, *, max_bytes: int | None) -> bytes:
    """Read a streaming httpx response with an optional hard byte limit."""

    chunks: list[bytes] = []
    total = 0
    for chunk in response.iter_bytes():
        if not chunk:
            continue
        total += len(chunk)
        if max_bytes is not None and total > max_bytes:
            raise ValueError("Inline media exceeds max bytes")
        chunks.append(chunk)
    return b"".join(chunks)


def download_url_with_limit(
    *,
    url: str,
    headers: Mapping[str, str] | None = None,
    timeout_seconds: float = 30.0,
    max_bytes: int | None = None,
    provider: str | None = None,
    allowed_hosts: Iterable[str] | None = None,
    max_redirects: int = 5,
) -> tuple[bytes, str | None]:
    """Download a provider-validated URL while enforcing a streaming byte cap.

    Redirects are followed only after the caller's provider allowlist validates
    the target URL, so a redirect cannot pivot the host-side control plane to
    another network.
    """

    request_headers = dict(headers or {})
    current_url = url
    redirects_seen = 0
    with httpx.Client(timeout=timeout_seconds, follow_redirects=False) as client:
        while True:
            with client.stream("GET", current_url, headers=request_headers) as response:
                if response.is_redirect:
                    redirects_seen += 1
                    if redirects_seen > max_redirects:
                        raise ValueError("Inline media redirect limit exceeded")
                    location = response.headers.get("location")
                    if not location:
                        raise ValueError("Inline media redirect response missing Location")
                    next_url = urljoin(str(response.url), location)
                    if provider is None:
                        raise ValueError("Inline media redirect requires provider validation")
                    require_provider_media_url(
                        provider=provider,
                        url=next_url,
                        allowed_hosts=allowed_hosts,
                    )
                    if urlparse(current_url).hostname != urlparse(next_url).hostname:
                        request_headers = {
                            key: value
                            for key, value in request_headers.items()
                            if key.lower() not in {"authorization", "cookie"}
                        }
                    current_url = next_url
                    continue
                response.raise_for_status()
                content = read_http_response_with_limit(response, max_bytes=max_bytes)
                mime_type = response.headers.get("content-type", "").split(";", 1)[0].strip()
                return content, mime_type or None
