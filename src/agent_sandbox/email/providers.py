from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Protocol

from pydantic import BaseModel, Field

from agent_sandbox.models import DraftMessage, EmailMessage
from agent_sandbox.repositories.records import EmailRecordsRepository
from agent_sandbox.storage import JsonStore


class EmailAttachmentMetadata(BaseModel):
    attachment_id: str
    filename: str
    mime_type: str | None = None
    byte_count: int | None = Field(default=None, ge=0)
    inline: bool = False
    content_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class EmailAttachmentContent(EmailAttachmentMetadata):
    content: bytes


class EmailDraftAttachment(BaseModel):
    filename: str
    content: bytes
    mime_type: str | None = None
    content_id: str | None = None
    inline: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class EmailMessageSummary(BaseModel):
    mailbox_id: str
    provider: str
    folder: str
    message_id: str
    thread_id: str | None = None
    sender: str
    recipients: list[str]
    received_at: datetime
    unread: bool
    body_snippet: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class EmailMessageDetail(EmailMessageSummary):
    subject: str
    body_text: str
    attachments: list[EmailAttachmentMetadata] = Field(default_factory=list)


class EmailDraft(BaseModel):
    draft_id: str
    mailbox_id: str
    provider: str
    thread_or_message_id: str | None = None
    subject: str | None = None
    body: str
    recipients: list[str]
    created_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)


class EmailProvider(Protocol):
    def list_unread(
        self, mailbox_id: str, folder: str, limit: int
    ) -> list[EmailMessageSummary]: ...

    def get_message(self, mailbox_id: str, message_id: str) -> EmailMessageDetail: ...

    def list_attachments(
        self, mailbox_id: str, message_id: str
    ) -> list[EmailAttachmentMetadata]: ...

    def download_attachment(
        self, mailbox_id: str, message_id: str, attachment_id: str
    ) -> EmailAttachmentContent: ...

    def create_draft(
        self,
        mailbox_id: str,
        thread_or_message_id: str | None,
        subject: str | None,
        body: str,
        recipients: list[str],
        attachments: list[EmailDraftAttachment] | None = None,
    ) -> EmailDraft: ...


class MultiMailboxEmailProvider:
    """Dispatches email operations to a provider selected by mailbox_id."""

    def __init__(self, providers_by_mailbox: dict[str, EmailProvider]) -> None:
        self.providers_by_mailbox = providers_by_mailbox

    def _provider_for_mailbox(self, mailbox_id: str) -> EmailProvider:
        provider = self.providers_by_mailbox.get(mailbox_id)
        if provider is None:
            raise KeyError(f"Unknown mailbox id: {mailbox_id}")
        return provider

    def list_unread(self, mailbox_id: str, folder: str, limit: int) -> list[EmailMessageSummary]:
        provider = self._provider_for_mailbox(mailbox_id)
        return provider.list_unread(mailbox_id=mailbox_id, folder=folder, limit=limit)

    def get_message(self, mailbox_id: str, message_id: str) -> EmailMessageDetail:
        provider = self._provider_for_mailbox(mailbox_id)
        return provider.get_message(mailbox_id=mailbox_id, message_id=message_id)

    def list_attachments(
        self, mailbox_id: str, message_id: str
    ) -> list[EmailAttachmentMetadata]:
        provider = self._provider_for_mailbox(mailbox_id)
        return provider.list_attachments(mailbox_id=mailbox_id, message_id=message_id)

    def download_attachment(
        self, mailbox_id: str, message_id: str, attachment_id: str
    ) -> EmailAttachmentContent:
        provider = self._provider_for_mailbox(mailbox_id)
        return provider.download_attachment(
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
        provider = self._provider_for_mailbox(mailbox_id)
        return provider.create_draft(
            mailbox_id=mailbox_id,
            thread_or_message_id=thread_or_message_id,
            subject=subject,
            body=body,
            recipients=recipients,
            attachments=attachments,
        )


class JsonEmailProvider:
    provider_name = "json_store"

    def __init__(
        self,
        email_store: EmailRecordsRepository,
        draft_store: JsonStore[DraftMessage],
    ) -> None:
        self.email_store = email_store
        self.draft_store = draft_store

    def list_unread(self, mailbox_id: str, folder: str, limit: int) -> list[EmailMessageSummary]:
        messages = [
            message
            for message in self.email_store.read_all().values()
            if message.mailbox_id == mailbox_id and message.folder == folder and message.unread
        ]
        messages.sort(key=lambda item: item.received_at, reverse=True)
        return [self._to_summary(message) for message in messages[:limit]]

    def get_message(self, mailbox_id: str, message_id: str) -> EmailMessageDetail:
        message = self.email_store.get(message_id)
        if message is None or message.mailbox_id != mailbox_id:
            raise KeyError(f"Unknown message for mailbox {mailbox_id}: {message_id}")
        return self._to_detail(message)

    def list_attachments(
        self, mailbox_id: str, message_id: str
    ) -> list[EmailAttachmentMetadata]:
        message = self.email_store.get(message_id)
        if message is None or message.mailbox_id != mailbox_id:
            raise KeyError(f"Unknown message for mailbox {mailbox_id}: {message_id}")
        return []

    def download_attachment(
        self, mailbox_id: str, message_id: str, attachment_id: str
    ) -> EmailAttachmentContent:
        message = self.email_store.get(message_id)
        if message is None or message.mailbox_id != mailbox_id:
            raise KeyError(f"Unknown message for mailbox {mailbox_id}: {message_id}")
        raise KeyError(f"Unknown attachment for message {message_id}: {attachment_id}")

    def create_draft(
        self,
        mailbox_id: str,
        thread_or_message_id: str | None,
        subject: str | None,
        body: str,
        recipients: list[str],
        attachments: list[EmailDraftAttachment] | None = None,
    ) -> EmailDraft:
        now = datetime.now(UTC)
        draft = DraftMessage(
            draft_id=f"draft_{int(now.timestamp())}",
            mailbox_id=mailbox_id,
            provider=self.provider_name,
            thread_or_message_id=thread_or_message_id,
            subject=subject,
            body=body,
            recipients=recipients,
            created_at=now,
        )
        self.draft_store.put(draft.draft_id, draft)
        return EmailDraft(
            draft_id=draft.draft_id,
            mailbox_id=draft.mailbox_id,
            provider=draft.provider,
            thread_or_message_id=draft.thread_or_message_id,
            subject=draft.subject,
            body=draft.body,
            recipients=draft.recipients,
            created_at=draft.created_at,
            metadata={"attachment_count": len(attachments or [])},
        )

    @staticmethod
    def _to_summary(message: EmailMessage) -> EmailMessageSummary:
        return EmailMessageSummary(
            mailbox_id=message.mailbox_id,
            provider=message.provider,
            folder=message.folder,
            message_id=message.message_id,
            thread_id=message.thread_id,
            sender=message.sender,
            recipients=message.recipients,
            received_at=message.received_at,
            unread=message.unread,
            body_snippet=message.body_snippet,
            metadata=message.metadata,
        )

    @classmethod
    def _to_detail(cls, message: EmailMessage) -> EmailMessageDetail:
        summary = cls._to_summary(message)
        return EmailMessageDetail(
            **summary.model_dump(),
            subject=message.subject,
            body_text=message.body_text,
            attachments=[],
        )
