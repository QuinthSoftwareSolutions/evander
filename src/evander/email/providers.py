from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Protocol

from pydantic import BaseModel, Field, model_validator

from evander.models import DraftMessage, EmailMessage
from evander.repositories.records import EmailRecordsRepository
from evander.storage import JsonStore


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
    subject: str = ""
    received_at: datetime
    unread: bool
    body_snippet: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class EmailMessageDetail(EmailMessageSummary):
    subject: str
    body_text: str
    attachments: list[EmailAttachmentMetadata] = Field(default_factory=list)


class EmailMessageSearchQuery(BaseModel):
    """Portable structured filters for historical email listing."""

    received_after: datetime | None = None
    received_before: datetime | None = None
    unread: bool | None = None
    sender_contains: str | None = None
    recipient_contains: str | None = None
    subject_contains: str | None = None
    text_contains: str | None = None
    has_attachments: bool | None = None

    @model_validator(mode="after")
    def validate_date_range(self) -> "EmailMessageSearchQuery":
        for value in (self.received_after, self.received_before):
            if value is not None and (value.tzinfo is None or value.utcoffset() is None):
                raise ValueError("received date filters must include a timezone")
        if (
            self.received_after is not None
            and self.received_before is not None
            and self.received_after >= self.received_before
        ):
            raise ValueError("received_after must be earlier than received_before")
        return self


class EmailMessagePage(BaseModel):
    messages: list[EmailMessageSummary] = Field(default_factory=list)
    next_cursor: str | None = None


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


class EmailSendResult(BaseModel):
    status: str
    draft_id: str
    mailbox_id: str
    provider: str
    message_id: str | None = None
    sent_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)


class EmailProvider(Protocol):
    def list_unread(
        self, mailbox_id: str, folder: str, limit: int
    ) -> list[EmailMessageSummary]: ...

    def list_messages(
        self,
        mailbox_id: str,
        folder: str,
        query: EmailMessageSearchQuery,
        cursor: str | None,
        limit: int,
    ) -> EmailMessagePage: ...

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

    def send_draft(self, mailbox_id: str, draft_id: str) -> EmailSendResult: ...


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

    def list_messages(
        self,
        mailbox_id: str,
        folder: str,
        query: EmailMessageSearchQuery,
        cursor: str | None,
        limit: int,
    ) -> EmailMessagePage:
        provider = self._provider_for_mailbox(mailbox_id)
        return provider.list_messages(
            mailbox_id=mailbox_id,
            folder=folder,
            query=query,
            cursor=cursor,
            limit=limit,
        )

    def get_message(self, mailbox_id: str, message_id: str) -> EmailMessageDetail:
        provider = self._provider_for_mailbox(mailbox_id)
        return provider.get_message(mailbox_id=mailbox_id, message_id=message_id)

    def list_attachments(self, mailbox_id: str, message_id: str) -> list[EmailAttachmentMetadata]:
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

    def send_draft(self, mailbox_id: str, draft_id: str) -> EmailSendResult:
        provider = self._provider_for_mailbox(mailbox_id)
        return provider.send_draft(mailbox_id=mailbox_id, draft_id=draft_id)


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

    def list_messages(
        self,
        mailbox_id: str,
        folder: str,
        query: EmailMessageSearchQuery,
        cursor: str | None,
        limit: int,
    ) -> EmailMessagePage:
        try:
            offset = int(cursor or "0")
        except ValueError as exc:
            raise ValueError("invalid email page cursor") from exc
        if offset < 0:
            raise ValueError("invalid email page cursor")

        messages = [
            message
            for message in self.email_store.read_all().values()
            if message.mailbox_id == mailbox_id
            and message.folder == folder
            and self._matches_search_query(message, query)
        ]
        messages.sort(key=lambda item: item.received_at, reverse=True)
        page = messages[offset : offset + limit]
        next_offset = offset + len(page)
        return EmailMessagePage(
            messages=[self._to_summary(message) for message in page],
            next_cursor=str(next_offset) if next_offset < len(messages) else None,
        )

    @staticmethod
    def _matches_search_query(message: EmailMessage, query: EmailMessageSearchQuery) -> bool:
        if query.received_after is not None and message.received_at < query.received_after:
            return False
        if query.received_before is not None and message.received_at >= query.received_before:
            return False
        if query.unread is not None and message.unread is not query.unread:
            return False

        def contains(value: str, expected: str | None) -> bool:
            return expected is None or expected.casefold() in value.casefold()

        if not contains(message.sender, query.sender_contains):
            return False
        if query.recipient_contains is not None and not any(
            contains(recipient, query.recipient_contains) for recipient in message.recipients
        ):
            return False
        if not contains(message.subject, query.subject_contains):
            return False
        if query.text_contains is not None and not contains(
            f"{message.subject}\n{message.body_text}", query.text_contains
        ):
            return False
        if query.has_attachments is not None:
            attachments = message.metadata.get("attachments") or []
            has_attachments = bool(attachments) or bool(message.metadata.get("has_attachments"))
            if has_attachments is not query.has_attachments:
                return False
        return True

    def get_message(self, mailbox_id: str, message_id: str) -> EmailMessageDetail:
        message = self.email_store.get(message_id)
        if message is None or message.mailbox_id != mailbox_id:
            raise KeyError(f"Unknown message for mailbox {mailbox_id}: {message_id}")
        return self._to_detail(message)

    def list_attachments(self, mailbox_id: str, message_id: str) -> list[EmailAttachmentMetadata]:
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
        draft_id = f"draft_{int(now.timestamp())}"
        draft = DraftMessage(
            draft_id=draft_id,
            mailbox_id=mailbox_id,
            provider=self.provider_name,
            provider_draft_id=draft_id,
            thread_or_message_id=thread_or_message_id,
            subject=subject,
            body=body,
            recipients=recipients,
            created_at=now,
            metadata={"attachment_count": len(attachments or [])},
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
            metadata=dict(draft.metadata),
        )

    def send_draft(self, mailbox_id: str, draft_id: str) -> EmailSendResult:
        sent_at = datetime.now(UTC)
        provider_message_id = f"sent_{draft_id}"

        def updater(items: dict[str, DraftMessage]) -> DraftMessage:
            draft = items.get(draft_id)
            if draft is None or draft.mailbox_id != mailbox_id:
                raise KeyError(f"Unknown draft for mailbox {mailbox_id}: {draft_id}")
            updated = draft.model_copy(
                update={
                    "sent_at": sent_at,
                    "send_status": "local_sent",
                    "provider_message_id": provider_message_id,
                    "metadata": {
                        **draft.metadata,
                        "local_send": True,
                    },
                }
            )
            items[draft_id] = updated
            return updated

        draft = self.draft_store.update_all(updater)
        return EmailSendResult(
            status="local_sent",
            draft_id=draft.draft_id,
            mailbox_id=draft.mailbox_id,
            provider=draft.provider,
            message_id=provider_message_id,
            sent_at=sent_at,
            metadata={"local_send": True},
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
            subject=message.subject,
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
            body_text=message.body_text,
            attachments=[],
        )
