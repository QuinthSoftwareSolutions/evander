"""Mailbox service facade."""

from __future__ import annotations
from typing import Any
from evander.email.providers import (
    EmailAttachmentContent,
    EmailAttachmentMetadata,
    EmailDraft,
    EmailDraftAttachment,
    EmailMessageDetail,
    EmailMessagePage,
    EmailMessageSearchQuery,
    EmailMessageSummary,
    EmailProvider,
    EmailSendResult,
)


class EmailService:
    def __init__(self, provider: EmailProvider) -> None:
        self.provider = provider

    def list_unread(self, mailbox_id: str, folder: str, limit: int) -> list[EmailMessageSummary]:
        return self.provider.list_unread(mailbox_id=mailbox_id, folder=folder, limit=limit)

    def list_messages(
        self,
        mailbox_id: str,
        folder: str,
        query: EmailMessageSearchQuery,
        cursor: str | None,
        limit: int,
    ) -> EmailMessagePage:
        return self.provider.list_messages(
            mailbox_id=mailbox_id,
            folder=folder,
            query=query,
            cursor=cursor,
            limit=limit,
        )

    def get_message(self, mailbox_id: str, message_id: str) -> EmailMessageDetail:
        return self.provider.get_message(mailbox_id=mailbox_id, message_id=message_id)

    def list_attachments(self, mailbox_id: str, message_id: str) -> list[EmailAttachmentMetadata]:
        return self.provider.list_attachments(mailbox_id=mailbox_id, message_id=message_id)

    def download_attachment(
        self, mailbox_id: str, message_id: str, attachment_id: str
    ) -> EmailAttachmentContent:
        return self.provider.download_attachment(
            mailbox_id=mailbox_id, message_id=message_id, attachment_id=attachment_id
        )

    def create_draft(
        self,
        mailbox_id: str,
        thread_or_message_id: str | None,
        subject: str | None,
        body: str,
        recipients: list[str],
        attachments: list[EmailDraftAttachment] | None = None,
    ) -> EmailDraft:
        return self.provider.create_draft(
            mailbox_id=mailbox_id,
            thread_or_message_id=thread_or_message_id,
            subject=subject,
            body=body,
            recipients=recipients,
            attachments=attachments,
        )

    def send_draft(self, mailbox_id: str, draft_id: str) -> EmailSendResult:
        return self.provider.send_draft(mailbox_id=mailbox_id, draft_id=draft_id)

    def send_from_draft(self, request_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        mailbox_id = str(payload.get("mailbox_id") or "").strip()
        draft_id = str(payload.get("draft_id") or payload.get("provider_draft_id") or "").strip()
        if not mailbox_id:
            raise ValueError("email send payload must include mailbox_id")
        if not draft_id:
            raise ValueError("email send payload must include draft_id")
        result = self.send_draft(mailbox_id=mailbox_id, draft_id=draft_id)
        output = result.model_dump(mode="json")
        output["request_id"] = request_id
        return output
