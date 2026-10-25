"""Shared integration value and HTTP helpers."""

from __future__ import annotations
import base64
import json
import logging
import mimetypes
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Iterator, Mapping
import httpx
from evander.models import (
    AdoIdentitySummary,
    PullRequestWorkItem,
)
from evander.issue_owner import assigned_to_identity
from evander.inline_media import (
    sanitize_inline_media_metadata,
)


_LOGGER = logging.getLogger("evander.services")


_IGNORED_FEEDBACK_RETENTION = timedelta(days=90)


_ATTACHMENT_SECRET_KEYS = {
    "authorization",
    "token",
    "access_token",
    "content",
    "contentBytes",
    "contentbytes",
    "content_bytes",
    "content_base64",
    "data",
}


def _parse_snowflake(value: str, *, field_name: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"Invalid {field_name} {value!r}: expected a numeric Discord snowflake string."
        ) from exc


def _parse_dt(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"Invalid timestamp {value!r}: expected an ISO 8601 string.")
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _parse_optional_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(UTC) if value.tzinfo is not None else value.replace(tzinfo=UTC)
    text = str(value or "").strip()
    if not text:
        return None
    return _parse_dt(text)


def _iso_z(value: datetime) -> str:
    normalized = value.astimezone(UTC) if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return normalized.isoformat().replace("+00:00", "Z")


def _datetime_in_range(value: datetime | None, *, since: datetime, until: datetime | None) -> bool:
    if value is None:
        return False
    normalized = value.astimezone(UTC) if value.tzinfo is not None else value.replace(tzinfo=UTC)
    start = since.astimezone(UTC) if since.tzinfo is not None else since.replace(tzinfo=UTC)
    if normalized < start:
        return False
    if until is not None:
        end = until.astimezone(UTC) if until.tzinfo is not None else until.replace(tzinfo=UTC)
        if normalized > end:
            return False
    return True


def _text_or_none(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _identity_property(identity: Mapping[str, Any], key: str) -> str | None:
    properties = identity.get("properties")
    if not isinstance(properties, Mapping):
        return None
    value = properties.get(key)
    if isinstance(value, Mapping):
        value = value.get("$value")
    return _text_or_none(value)


def _identity_email(identity: Mapping[str, Any]) -> str | None:
    for key in ("Mail", "SignInAddress", "Account"):
        value = _identity_property(identity, key)
        if value and "@" in value:
            return value
    for key in ("uniqueName", "mailAddress", "email"):
        value = _text_or_none(identity.get(key))
        if value and "@" in value:
            return value
    return None


def _ado_identity_summary(query: str, identity: Mapping[str, Any]) -> dict[str, Any]:
    display_name = (
        _text_or_none(identity.get("providerDisplayName"))
        or _text_or_none(identity.get("displayName"))
        or _text_or_none(identity.get("customDisplayName"))
        or _text_or_none(identity.get("uniqueName"))
    )
    unique_name = (
        _text_or_none(identity.get("uniqueName"))
        or _identity_property(identity, "Account")
        or _identity_property(identity, "Mail")
        or _identity_property(identity, "SignInAddress")
    )
    descriptor = _text_or_none(identity.get("descriptor")) or _text_or_none(
        identity.get("subjectDescriptor")
    )
    return {
        "id": _text_or_none(identity.get("id")),
        "descriptor": descriptor,
        "display_name": display_name or query,
        "unique_name": unique_name,
        "email": _identity_email(identity),
        "raw": dict(identity),
    }


def _unique_positive_ints(values: Iterator[Any]) -> list[int]:
    normalized: list[int] = []
    seen: set[int] = set()
    for value in values:
        try:
            candidate = int(str(value).strip())
        except (TypeError, ValueError):
            continue
        if candidate <= 0 or candidate in seen:
            continue
        seen.add(candidate)
        normalized.append(candidate)
    return normalized


def _pull_request_work_item_assignee(value: Any) -> AdoIdentitySummary | None:
    identity = assigned_to_identity(value)
    if identity is None:
        return None
    payload = dict(identity)
    if isinstance(value, Mapping):
        payload["raw"] = dict(value)
    return AdoIdentitySummary.model_validate(payload)


def _pull_request_work_item_from_reference(
    *,
    reference: Mapping[str, Any],
    details: Mapping[str, Any] | None,
) -> PullRequestWorkItem:
    work_item_id = str(reference.get("id") or "").strip()
    detail_payload = dict(details) if isinstance(details, Mapping) else {}
    fields = detail_payload.get("fields")
    fields = fields if isinstance(fields, Mapping) else {}
    web_links = detail_payload.get("_links")
    web_links = web_links if isinstance(web_links, Mapping) else {}
    web_link = web_links.get("web")
    web_link = web_link if isinstance(web_link, Mapping) else {}
    return PullRequestWorkItem(
        work_item_id=work_item_id,
        title=_text_or_none(fields.get("System.Title")),
        url=(
            _text_or_none(web_link.get("href"))
            or _text_or_none(reference.get("url"))
            or _text_or_none(detail_payload.get("url"))
        ),
        assignee=_pull_request_work_item_assignee(fields.get("System.AssignedTo")),
        raw={"reference": dict(reference), "work_item": detail_payload},
    )


def _identity_match_values(value: Any) -> set[str]:
    values: set[str] = set()

    def add(candidate: Any) -> None:
        text = str(candidate or "").strip()
        if text:
            values.add(text.casefold())

    if isinstance(value, Mapping):
        for key in (
            "id",
            "descriptor",
            "subjectDescriptor",
            "displayName",
            "display_name",
            "providerDisplayName",
            "customDisplayName",
            "uniqueName",
            "unique_name",
            "email",
            "mailAddress",
            "name",
        ):
            add(value.get(key))
        properties = value.get("properties")
        if isinstance(properties, Mapping):
            for key in ("Account", "Mail", "SignInAddress"):
                raw = properties.get(key)
                if isinstance(raw, Mapping):
                    raw = raw.get("$value")
                add(raw)
    else:
        add(value)
    return values


def _identity_matches(query: str | None, value: Any) -> bool:
    text = str(query or "").strip()
    if not text:
        return True
    expected = text.casefold()
    return expected in _identity_match_values(value)


def _snippet(text: str | None, *, limit: int = 240) -> str | None:
    normalized = " ".join(str(text or "").split())
    if not normalized:
        return None
    if len(normalized) <= limit:
        return normalized
    return normalized[: max(0, limit - 1)].rstrip() + "..."


def _message_sort_value(value: str) -> tuple[int, int | str]:
    if value.isdigit():
        return (0, int(value))
    return (1, value)


def _sanitize_attachment_metadata(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    result = {
        str(key): sanitize_inline_media_metadata(value)
        for key, value in raw.items()
        if str(key).replace("-", "_").lower() not in _ATTACHMENT_SECRET_KEYS
    }
    # A forwarded Teams message carries its text and attribution in this JSON
    # field. File content and credentials must still be excluded from metadata.
    if raw.get("contentType") == "forwardedMessageReference":
        content = raw.get("content")
        if isinstance(content, str):
            try:
                forwarded = json.loads(content)
            except ValueError:
                forwarded = None
            if isinstance(forwarded, dict):
                result["content"] = json.dumps(
                    sanitize_inline_media_metadata(forwarded), ensure_ascii=False
                )
    return result


def _sanitize_message_payload_attachments(raw: Any) -> Any:
    if not isinstance(raw, dict):
        return raw
    payload = dict(raw)
    if isinstance(payload.get("attachments"), list):
        payload["attachments"] = [
            _sanitize_attachment_metadata(item)
            for item in payload["attachments"]
            if isinstance(item, dict)
        ]
    if isinstance(payload.get("hostedContents"), list):
        payload["hostedContents"] = [
            _sanitize_attachment_metadata(item)
            for item in payload["hostedContents"]
            if isinstance(item, dict)
        ]
    if isinstance(payload.get("replies"), list):
        payload["replies"] = [
            _sanitize_message_payload_attachments(item)
            for item in payload["replies"]
            if isinstance(item, dict)
        ]
    return payload


def _artifact_filename(artifact: dict[str, Any], *, index: int) -> str:
    filename = str(artifact.get("filename") or artifact.get("name") or "").strip()
    return filename or f"attachment-{index + 1}"


def _artifact_content_type(artifact: dict[str, Any], filename: str) -> str:
    return str(
        artifact.get("mime_type")
        or artifact.get("content_type")
        or artifact.get("contentType")
        or mimetypes.guess_type(filename)[0]
        or "application/octet-stream"
    )


def _artifact_bytes(artifact: dict[str, Any]) -> bytes:
    path = artifact.get("path")
    if path:
        return Path(str(path)).read_bytes()
    data = artifact.get("content") or artifact.get("content_bytes") or artifact.get("bytes")
    if isinstance(data, bytes):
        return data
    if isinstance(data, str):
        if artifact.get("encoding") == "base64" or artifact.get("content_base64"):
            return base64.b64decode(str(artifact.get("content_base64") or data))
        return data.encode("utf-8")
    content_base64 = artifact.get("content_base64")
    if isinstance(content_base64, str):
        return base64.b64decode(content_base64)
    raise ValueError("Artifact payload must include a readable path or inline content bytes.")


def _buffer_streaming_response(
    response: httpx.Response,
    *,
    max_bytes: int,
    allow_redirect: bool = False,
) -> httpx.Response:
    """Materialize a streaming response without ever crossing the caller's byte cap."""

    try:
        if not (allow_redirect and response.is_redirect):
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError:
                buffered_error = httpx.Response(
                    response.status_code,
                    headers=response.headers,
                    content=b"",
                    request=response.request,
                    extensions=response.extensions,
                )
                buffered_error.raise_for_status()
        content_length = response.headers.get("content-length")
        if content_length is not None:
            try:
                declared_length = int(content_length)
            except ValueError:
                declared_length = None
            if declared_length is not None and declared_length > max_bytes:
                raise ValueError(
                    f"provider attachment exceeds the maximum size of {max_bytes} bytes"
                )
        chunks: list[bytes] = []
        total = 0
        for chunk in response.iter_bytes():
            if not chunk:
                continue
            total += len(chunk)
            if total > max_bytes:
                raise ValueError(
                    f"provider attachment exceeds the maximum size of {max_bytes} bytes"
                )
            chunks.append(chunk)
        return httpx.Response(
            response.status_code,
            headers=response.headers,
            content=b"".join(chunks),
            request=response.request,
            extensions=response.extensions,
        )
    finally:
        response.close()


def _normalize_optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _normalize_string_list(value: Any) -> list[str]:
    if value is None:
        return []
    raw_items = [value] if isinstance(value, str) else value
    if not isinstance(raw_items, (list, tuple, set)):
        raise ValueError("pull request tag/status filters must be a string or list of strings")
    return [str(item).strip() for item in raw_items if str(item).strip()]


def normalize_pr_automation_provider(provider: str) -> str:
    normalized = provider.strip()
    if normalized in {"ado", "azure_devops", "azure-devops"}:
        return "azure_devops"
    return normalized


def normalize_pr_feedback_provider(provider: str) -> str:
    return normalize_pr_automation_provider(provider)


def _normalize_optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
