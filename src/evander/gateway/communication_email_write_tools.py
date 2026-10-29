"""MCP tools for email write."""

from __future__ import annotations
from collections.abc import Callable
from pathlib import Path
from typing import Any
import httpx
from evander.email.http_utils import ProviderRequestError
from evander.email.providers import EmailDraftAttachment
from evander.models import (
    CommunicationSendMessageWithAttachmentsRequest,
    ToolName,
)
from evander.policy import PolicyError

from .communication_support import CommunicationToolHelpers


def create_email_write_tools(
    context: Any, helpers: CommunicationToolHelpers
) -> dict[str, Callable[..., Any]]:
    _artifact_audit_summaries = helpers.artifact_audit_summaries
    _artifact_payloads = helpers.artifact_payloads
    _communication_target_audit_fields = helpers.communication_target_audit_fields
    _record_email_draft_snapshot = helpers.record_email_draft_snapshot
    _send_email_draft = helpers.send_email_draft
    audit_failure = helpers.audit_failure
    audit_success = helpers.audit_success
    draft_recipients_from_payload = helpers.draft_recipients_from_payload

    def email_create_draft(
        session_id: str,
        body: str,
        mailbox_id: str,
        thread_or_message_id: str | None = None,
        subject: str | None = None,
        recipients: list[str] | None = None,
    ) -> dict[str, Any]:
        """Create an unsent draft; discover the mailbox_id with email_list_mailboxes.

        Recipients may be omitted or empty while preparing a draft for human review.
        """
        args = {
            "session_id": session_id,
            "mailbox_id": mailbox_id,
            "thread_or_message_id": thread_or_message_id,
            "subject": subject,
            "recipients": recipients,
            "body": body,
        }
        try:
            session = context.policy.require_tool(session_id, ToolName.EMAIL_CREATE_DRAFT)
            context.policy.require_email_draft_enabled(session, mailbox_id)
            context.policy.require_email_folder_for_mailbox(session, mailbox_id, "drafts")
            normalized_recipients = recipients or []
            context.policy.require_draft_recipients(session, normalized_recipients)
            draft = context.email_service_for_session(session_id).create_draft(
                mailbox_id, thread_or_message_id, subject, body, normalized_recipients
            )
            _record_email_draft_snapshot(
                session_id=session_id,
                draft=draft,
                body=body,
            )
            result = draft.model_dump(mode="json")
            audit_success(
                session_id, ToolName.EMAIL_CREATE_DRAFT, args, {"draft_id": draft.draft_id}
            )
            return result
        except PolicyError as exc:
            audit_failure(session_id, ToolName.EMAIL_CREATE_DRAFT, args, str(exc))
            raise
        except (httpx.HTTPError, ProviderRequestError) as exc:
            audit_failure(
                session_id,
                ToolName.EMAIL_CREATE_DRAFT,
                args,
                str(exc),
                decision="error",
                error_class="adapter_http_error",
            )
            raise
        except Exception as exc:
            audit_failure(
                session_id,
                ToolName.EMAIL_CREATE_DRAFT,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    def email_create_draft_with_attachments(
        session_id: str,
        body: str,
        mailbox_id: str,
        artifact_ids: list[str],
        thread_or_message_id: str | None = None,
        subject: str | None = None,
        recipients: list[str] | None = None,
    ) -> dict[str, Any]:
        request = CommunicationSendMessageWithAttachmentsRequest(
            session_id=session_id,
            provider="email",
            mailbox_id=mailbox_id,
            message=body,
            artifact_ids=artifact_ids,
            thread_id=thread_or_message_id,
            subject=subject,
            recipients=recipients or [],
        )
        args = request.model_dump(mode="json")
        try:
            session = context.policy.require_tool(
                session_id, ToolName.EMAIL_CREATE_DRAFT_WITH_ATTACHMENTS
            )
            context.policy.require_email_draft_enabled(session, mailbox_id)
            context.policy.require_email_folder_for_mailbox(session, mailbox_id, "drafts")
            normalized_recipients = recipients or []
            context.policy.require_draft_recipients(session, normalized_recipients)
            artifacts = _artifact_payloads(artifact_ids)
            args["artifacts"] = _artifact_audit_summaries(artifact_ids)
            context.policy.require_communication_file_artifacts(
                session, artifacts, provider="email", mailbox_id=mailbox_id
            )
            service = context.email_service_for_session(session_id)
            if hasattr(service, "create_draft_with_attachments"):
                draft = service.create_draft_with_attachments(
                    mailbox_id,
                    thread_or_message_id,
                    subject,
                    body,
                    normalized_recipients,
                    artifacts,
                )
            else:
                draft_attachments = [
                    EmailDraftAttachment(
                        filename=str(artifact["filename"]),
                        content=Path(str(artifact["path"])).read_bytes(),
                        mime_type=artifact.get("mime_type"),
                    )
                    for artifact in artifacts
                ]
                draft = service.create_draft(
                    mailbox_id,
                    thread_or_message_id,
                    subject,
                    body,
                    normalized_recipients,
                    attachments=draft_attachments,
                )
            _record_email_draft_snapshot(
                session_id=session_id,
                draft=draft,
                body=body,
                artifact_ids=artifact_ids,
            )
            result = draft.model_dump(mode="json") if hasattr(draft, "model_dump") else dict(draft)
            result["artifact_ids"] = artifact_ids
            audit_success(
                session_id,
                ToolName.EMAIL_CREATE_DRAFT_WITH_ATTACHMENTS,
                args,
                {
                    **_communication_target_audit_fields(
                        provider="email",
                        mailbox_id=mailbox_id,
                    ),
                    "draft_id": result.get("draft_id"),
                    "artifacts": _artifact_audit_summaries(artifact_ids),
                },
            )
            return result
        except PolicyError as exc:
            audit_failure(session_id, ToolName.EMAIL_CREATE_DRAFT_WITH_ATTACHMENTS, args, str(exc))
            raise
        except (httpx.HTTPError, ProviderRequestError) as exc:
            audit_failure(
                session_id,
                ToolName.EMAIL_CREATE_DRAFT_WITH_ATTACHMENTS,
                args,
                str(exc),
                decision="error",
                error_class="adapter_http_error",
            )
            raise
        except Exception as exc:
            audit_failure(
                session_id,
                ToolName.EMAIL_CREATE_DRAFT_WITH_ATTACHMENTS,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    def email_send(
        session_id: str,
        mailbox_id: str,
        draft_id: str,
        recipients: list[str],
        subject: str | None = None,
        approval_request_id: str | None = None,
    ) -> dict[str, Any]:
        args = {
            "session_id": session_id,
            "mailbox_id": mailbox_id,
            "draft_id": draft_id,
            "recipients": recipients,
            "subject": subject,
            "approval_request_id": approval_request_id,
        }
        try:
            session = context.policy.require_tool(session_id, ToolName.EMAIL_SEND)
            result = _send_email_draft(
                session_id=session_id,
                session=session,
                mailbox_id=mailbox_id,
                draft_id=draft_id,
                recipients=recipients,
                subject=subject,
            )
            audit_success(
                session_id,
                ToolName.EMAIL_SEND,
                args,
                {
                    **_communication_target_audit_fields(
                        provider="email",
                        mailbox_id=mailbox_id,
                        approval_request_id=approval_request_id,
                    ),
                    **result,
                },
            )
            return result
        except PolicyError as exc:
            audit_failure(session_id, ToolName.EMAIL_SEND, args, str(exc))
            raise
        except (httpx.HTTPError, ProviderRequestError) as exc:
            audit_failure(
                session_id,
                ToolName.EMAIL_SEND,
                args,
                str(exc),
                decision="error",
                error_class="adapter_http_error",
            )
            raise
        except Exception as exc:
            audit_failure(
                session_id,
                ToolName.EMAIL_SEND,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    def email_send_with_attachments(
        session_id: str,
        mailbox_id: str,
        draft_id: str,
        recipients: list[str],
        artifact_ids: list[str],
        subject: str | None = None,
        request_id: str | None = None,
        approval_request_id: str | None = None,
    ) -> dict[str, Any]:
        args = {
            "session_id": session_id,
            "mailbox_id": mailbox_id,
            "draft_id": draft_id,
            "recipients": recipients,
            "artifact_ids": artifact_ids,
            "subject": subject,
            "request_id": request_id,
            "approval_request_id": approval_request_id,
        }
        try:
            session = context.policy.require_tool(session_id, ToolName.EMAIL_SEND_WITH_ATTACHMENTS)
            context.policy.require_email_send_enabled(session, mailbox_id)
            artifacts = _artifact_payloads(artifact_ids)
            args["artifacts"] = _artifact_audit_summaries(artifact_ids)
            context.policy.require_communication_file_artifacts(
                session, artifacts, provider="email", mailbox_id=mailbox_id
            )
            approval_payload: dict[str, Any] = {
                "session_id": session_id,
                "provider": "email",
                "mailbox_id": mailbox_id,
                "draft_id": draft_id,
                "recipients": recipients,
                "artifact_ids": artifact_ids,
            }
            if subject is not None:
                approval_payload["subject"] = subject
            approval = context.policy.require_communication_file_send_approval(
                session,
                request_id=request_id,
                payload=approval_payload,
            )
            result = _send_email_draft(
                session_id=session_id,
                session=session,
                mailbox_id=mailbox_id,
                draft_id=draft_id,
                recipients=recipients,
                subject=subject,
                artifact_ids=artifact_ids,
            )
            audit_success(
                session_id,
                ToolName.EMAIL_SEND_WITH_ATTACHMENTS,
                args,
                {
                    **_communication_target_audit_fields(
                        provider="email",
                        mailbox_id=mailbox_id,
                        approval_request_id=request_id,
                        approval_decision=None if approval is None else approval.status.value,
                    ),
                    "artifacts": _artifact_audit_summaries(artifact_ids),
                    **result,
                },
            )
            return result
        except PolicyError as exc:
            audit_failure(session_id, ToolName.EMAIL_SEND_WITH_ATTACHMENTS, args, str(exc))
            raise
        except (httpx.HTTPError, ProviderRequestError) as exc:
            audit_failure(
                session_id,
                ToolName.EMAIL_SEND_WITH_ATTACHMENTS,
                args,
                str(exc),
                decision="error",
                error_class="adapter_http_error",
            )
            raise
        except Exception as exc:
            audit_failure(
                session_id,
                ToolName.EMAIL_SEND_WITH_ATTACHMENTS,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    def email_send_approved_with_attachments(
        session_id: str,
        request_id: str,
        payload: dict[str, Any],
        artifact_ids: list[str],
    ) -> dict[str, Any]:
        args = {
            "session_id": session_id,
            "request_id": request_id,
            "payload": payload,
            "artifact_ids": artifact_ids,
        }
        try:
            session = context.policy.require_tool(
                session_id, ToolName.EMAIL_SEND_APPROVED_WITH_ATTACHMENTS
            )
            context.policy.require_email_send_enabled(session, str(payload.get("mailbox_id", "")))
            recipients = draft_recipients_from_payload(payload)
            context.policy.require_draft_recipients(session, recipients)
            artifacts = _artifact_payloads(artifact_ids)
            args["artifacts"] = _artifact_audit_summaries(artifact_ids)
            context.policy.require_communication_file_artifacts(
                session, artifacts, provider="email", mailbox_id=str(payload.get("mailbox_id", ""))
            )
            file_approval_payload = {**payload, "artifact_ids": artifact_ids}
            file_approval = context.policy.require_communication_file_send_approval(
                session, request_id=request_id, payload=file_approval_payload
            )
            approval = file_approval or context.policy.require_approved(request_id, payload)
            if approval.session_id != session_id:
                raise PolicyError(
                    f"Approval request {request_id} does not belong to session {session_id}"
                )
            mailbox_id = str(payload.get("mailbox_id", ""))
            draft_id = str(payload.get("draft_id") or payload.get("provider_draft_id") or "")
            if not recipients:
                snapshot = context.drafts.get(draft_id)
                if snapshot is not None and snapshot.mailbox_id == mailbox_id:
                    recipients = list(snapshot.recipients)
            subject = payload.get("subject") if isinstance(payload.get("subject"), str) else None
            result = _send_email_draft(
                session_id=session_id,
                session=session,
                mailbox_id=mailbox_id,
                draft_id=draft_id,
                recipients=recipients,
                subject=subject,
                artifact_ids=artifact_ids,
            )
            audit_success(
                session_id,
                ToolName.EMAIL_SEND_APPROVED_WITH_ATTACHMENTS,
                args,
                {
                    **_communication_target_audit_fields(
                        provider="email",
                        mailbox_id=str(payload.get("mailbox_id", "")),
                        approval_request_id=request_id,
                        approval_decision=(approval.status.value),
                    ),
                    "artifacts": _artifact_audit_summaries(artifact_ids),
                    **result,
                },
            )
            return result
        except PolicyError as exc:
            audit_failure(session_id, ToolName.EMAIL_SEND_APPROVED_WITH_ATTACHMENTS, args, str(exc))
            raise
        except (httpx.HTTPError, ProviderRequestError) as exc:
            audit_failure(
                session_id,
                ToolName.EMAIL_SEND_APPROVED_WITH_ATTACHMENTS,
                args,
                str(exc),
                decision="error",
                error_class="adapter_http_error",
            )
            raise
        except Exception as exc:
            audit_failure(
                session_id,
                ToolName.EMAIL_SEND_APPROVED_WITH_ATTACHMENTS,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    def email_send_approved(
        session_id: str, request_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        args = {"session_id": session_id, "request_id": request_id, "payload": payload}
        try:
            session = context.policy.require_tool(session_id, ToolName.EMAIL_SEND_APPROVED)
            context.policy.require_email_send_enabled(session, str(payload.get("mailbox_id", "")))
            recipients = draft_recipients_from_payload(payload)
            context.policy.require_draft_recipients(session, recipients)
            approval = context.policy.require_approved(request_id, payload)
            if approval.session_id != session_id:
                raise PolicyError(
                    f"Approval request {request_id} does not belong to session {session_id}"
                )
            mailbox_id = str(payload.get("mailbox_id", ""))
            draft_id = str(payload.get("draft_id") or payload.get("provider_draft_id") or "")
            if not recipients:
                snapshot = context.drafts.get(draft_id)
                if snapshot is not None and snapshot.mailbox_id == mailbox_id:
                    recipients = list(snapshot.recipients)
            subject = payload.get("subject") if isinstance(payload.get("subject"), str) else None
            result = _send_email_draft(
                session_id=session_id,
                session=session,
                mailbox_id=mailbox_id,
                draft_id=draft_id,
                recipients=recipients,
                subject=subject,
            )
            audit_success(session_id, ToolName.EMAIL_SEND_APPROVED, args, result)
            return result
        except PolicyError as exc:
            audit_failure(session_id, ToolName.EMAIL_SEND_APPROVED, args, str(exc))
            raise
        except (httpx.HTTPError, ProviderRequestError) as exc:
            audit_failure(
                session_id,
                ToolName.EMAIL_SEND_APPROVED,
                args,
                str(exc),
                decision="error",
                error_class="adapter_http_error",
            )
            raise
        except Exception as exc:
            audit_failure(
                session_id,
                ToolName.EMAIL_SEND_APPROVED,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    return {
        "email_create_draft": email_create_draft,
        "email_create_draft_with_attachments": email_create_draft_with_attachments,
        "email_send": email_send,
        "email_send_with_attachments": email_send_with_attachments,
        "email_send_approved_with_attachments": email_send_approved_with_attachments,
        "email_send_approved": email_send_approved,
    }
