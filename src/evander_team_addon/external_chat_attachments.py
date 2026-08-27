from __future__ import annotations

import base64
import re
from dataclasses import dataclass, field
from typing import Any, Mapping

import httpx

from evander.messaging.notifications import _email_mailbox_id_from_auth_profile_ref

from .chat_attachments import (
    PreparedChatAttachment,
    TeamChatAttachmentStorage,
    validate_attachment_policy_content,
)
from .models import (
    TeamChatAttachmentExternalSource,
    TeamChatAttachmentPolicy,
)


@dataclass(frozen=True, slots=True)
class ExternalAttachmentRef:
    attachment_id: str
    filename: str
    mime_type: str | None
    byte_count: int | None


@dataclass(frozen=True, slots=True)
class ExternalAttachmentImportFailure:
    code: str
    message: str
    attachment_id: str | None = None
    filename: str | None = None

    def payload(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "attachment_id": self.attachment_id,
            "filename": self.filename,
        }


@dataclass(slots=True)
class ExternalAttachmentImportResult:
    status: str = "none"
    discovered_count: int = 0
    attachments: list[PreparedChatAttachment] = field(default_factory=list)
    failures: list[ExternalAttachmentImportFailure] = field(default_factory=list)

    def payload(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "discovered_count": self.discovered_count,
            "imported_count": len(self.attachments),
            "failed_count": len(self.failures),
            "attachment_ids": [item.id for item in self.attachments],
            "failures": [item.payload() for item in self.failures[:20]],
            "failures_truncated": len(self.failures) > 20,
        }


class ExternalTeamChatAttachmentImporter:
    """Import provider files into the Team add-on's private attachment storage."""

    def __init__(self, runtime: Any, storage: TeamChatAttachmentStorage) -> None:
        self.runtime = runtime
        self.storage = storage

    def import_message_attachments(
        self,
        *,
        session_id: str,
        provider: str,
        communication_target_id: str,
        target_address: str,
        source_message_id: str,
        policy: TeamChatAttachmentPolicy,
        auth_profile_ref: str | None = None,
    ) -> ExternalAttachmentImportResult:
        normalized_provider = str(provider or "").strip().lower()
        try:
            email_mailbox_id = (
                _email_mailbox_id_from_auth_profile_ref(auth_profile_ref or "")
                if normalized_provider == "email"
                else None
            )
            raw_refs = self._list_attachments(
                session_id=session_id,
                provider=normalized_provider,
                target_address=target_address,
                source_message_id=source_message_id,
                email_mailbox_id=email_mailbox_id,
            )
        except Exception as exc:
            return ExternalAttachmentImportResult(
                status="failed",
                failures=[
                    ExternalAttachmentImportFailure(
                        code="list_failed",
                        message=_safe_error(exc),
                    )
                ],
            )
        if not raw_refs:
            return ExternalAttachmentImportResult(status="none")

        result = ExternalAttachmentImportResult(discovered_count=len(raw_refs))
        refs: list[ExternalAttachmentRef] = []
        for raw_ref in raw_refs:
            try:
                refs.append(_attachment_ref(raw_ref))
            except Exception as exc:
                result.failures.append(_metadata_failure(exc, raw_ref))
        limit = int(policy.max_attachments_per_message or 0)
        accepted_refs = refs[:limit] if limit else refs
        for ref in refs[len(accepted_refs) :]:
            result.failures.append(
                ExternalAttachmentImportFailure(
                    code="count_limit",
                    message="attachment exceeds the Team Chat per-message count limit",
                    attachment_id=ref.attachment_id,
                    filename=ref.filename,
                )
            )
        for ref in accepted_refs:
            try:
                if ref.byte_count is not None:
                    validate_attachment_policy_content(
                        policy,
                        direction="operator_to_agent",
                        filename=ref.filename,
                        mime_type=ref.mime_type,
                        byte_count=ref.byte_count,
                    )
                content, filename, mime_type = self._download_attachment(
                    session_id=session_id,
                    provider=normalized_provider,
                    target_address=target_address,
                    source_message_id=source_message_id,
                    ref=ref,
                    email_mailbox_id=email_mailbox_id,
                )
                validate_attachment_policy_content(
                    policy,
                    direction="operator_to_agent",
                    filename=filename,
                    mime_type=mime_type,
                    byte_count=len(content),
                )
                result.attachments.append(
                    self.storage.prepare_upload(
                        content=content,
                        filename=filename,
                        mime_type=mime_type,
                        external_source=TeamChatAttachmentExternalSource(
                            provider=normalized_provider,
                            communication_target_id=communication_target_id,
                            source_message_id=source_message_id,
                            source_attachment_id=ref.attachment_id,
                        ),
                    )
                )
            except PermissionError as exc:
                result.failures.append(_failure("policy_disabled", exc, ref))
            except ValueError as exc:
                result.failures.append(_failure("policy_rejected", exc, ref))
            except Exception as exc:
                result.failures.append(_failure("download_failed", exc, ref))
        if result.attachments and result.failures:
            result.status = "partial"
        elif result.attachments:
            result.status = "complete"
        else:
            result.status = "failed"
        return result

    def _list_attachments(
        self,
        *,
        session_id: str,
        provider: str,
        target_address: str,
        source_message_id: str,
        email_mailbox_id: str | None,
    ) -> list[Any]:
        if provider == "teams":
            raw = self.runtime.teams_service_for_session(session_id).list_attachments(
                source_message_id,
                channel_target=target_address,
            )
        elif provider == "discord":
            raw = self.runtime.discord_service_for_session(session_id).list_attachments(
                source_message_id,
                channel_id=target_address.rsplit("/", 1)[-1],
            )
        elif provider == "email":
            raw = self.runtime.email_service_for_session(session_id).list_attachments(
                mailbox_id=email_mailbox_id,
                message_id=source_message_id,
            )
        else:
            raise ValueError(f"unsupported external attachment provider: {provider}")
        return list(raw)

    def _download_attachment(
        self,
        *,
        session_id: str,
        provider: str,
        target_address: str,
        source_message_id: str,
        ref: ExternalAttachmentRef,
        email_mailbox_id: str | None,
    ) -> tuple[bytes, str, str | None]:
        if provider == "teams":
            raw = self.runtime.teams_service_for_session(session_id).download_attachment(
                source_message_id,
                ref.attachment_id,
                channel_target=target_address,
            )
        elif provider == "discord":
            raw = self.runtime.discord_service_for_session(session_id).download_attachment(
                source_message_id,
                ref.attachment_id,
                channel_id=target_address.rsplit("/", 1)[-1],
            )
        elif provider == "email":
            raw = self.runtime.email_service_for_session(session_id).download_attachment(
                mailbox_id=email_mailbox_id,
                message_id=source_message_id,
                attachment_id=ref.attachment_id,
            )
        else:  # pragma: no cover - guarded by listing
            raise ValueError(f"unsupported external attachment provider: {provider}")
        payload = _mapping(raw)
        return (
            _content_bytes(payload),
            str(payload.get("filename") or payload.get("name") or ref.filename),
            _optional_text(
                payload.get("mime_type")
                or payload.get("content_type")
                or payload.get("contentType")
                or ref.mime_type
            ),
        )


def _attachment_ref(value: Any) -> ExternalAttachmentRef:
    payload = _mapping(value)
    attachment_id = str(payload.get("attachment_id") or payload.get("id") or "").strip()
    if not attachment_id:
        raise ValueError("external attachment metadata omitted attachment_id")
    filename = str(payload.get("filename") or payload.get("name") or attachment_id).strip()
    mime_type = _optional_text(
        payload.get("mime_type") or payload.get("content_type") or payload.get("contentType")
    )
    raw_size = payload.get("byte_count", payload.get("size"))
    byte_count = int(raw_size) if raw_size not in (None, "") else None
    return ExternalAttachmentRef(
        attachment_id=attachment_id,
        filename=filename,
        mime_type=mime_type,
        byte_count=byte_count,
    )


def _mapping(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        return dict(value.model_dump(mode="python"))
    if isinstance(value, Mapping):
        return dict(value)
    raise ValueError("external attachment adapter returned an unsupported payload")


def _content_bytes(payload: Mapping[str, Any]) -> bytes:
    content = payload.get("content")
    if isinstance(content, bytes):
        return content
    content_base64 = payload.get("content_base64")
    if isinstance(content_base64, str):
        return base64.b64decode(content_base64)
    if isinstance(content, str):
        if str(payload.get("encoding") or "").lower() == "base64":
            return base64.b64decode(content)
        return content.encode("utf-8")
    raise ValueError("external attachment content is unavailable")


def _failure(
    code: str,
    exc: BaseException,
    ref: ExternalAttachmentRef,
) -> ExternalAttachmentImportFailure:
    return ExternalAttachmentImportFailure(
        code=code,
        message=_safe_error(exc),
        attachment_id=ref.attachment_id,
        filename=ref.filename,
    )


def _metadata_failure(
    exc: BaseException,
    value: Any,
) -> ExternalAttachmentImportFailure:
    payload = dict(value) if isinstance(value, Mapping) else {}
    return ExternalAttachmentImportFailure(
        code="metadata_invalid",
        message=_safe_error(exc),
        attachment_id=_optional_text(payload.get("attachment_id") or payload.get("id")),
        filename=_optional_text(payload.get("filename") or payload.get("name")),
    )


def _optional_text(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None


def _safe_error(exc: BaseException) -> str:
    error_class = type(exc).__name__
    if isinstance(exc, httpx.HTTPStatusError):
        return f"{error_class} (HTTP {exc.response.status_code})"
    text = " ".join(str(exc).split())
    if isinstance(exc, httpx.HTTPError) or re.search(r"https?://", text, flags=re.IGNORECASE):
        return error_class
    return text[:300] or error_class


__all__ = [
    "ExternalAttachmentImportFailure",
    "ExternalAttachmentImportResult",
    "ExternalTeamChatAttachmentImporter",
]
