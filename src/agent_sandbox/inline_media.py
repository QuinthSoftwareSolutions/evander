from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from agent_sandbox.models import InlineMediaRef


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
