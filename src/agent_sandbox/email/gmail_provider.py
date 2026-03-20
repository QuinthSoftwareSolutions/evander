from __future__ import annotations

import base64
import os
from datetime import UTC, datetime
from email.message import EmailMessage as MimeMessage
from typing import Any

import httpx

from .providers import EmailDraft, EmailMessageDetail, EmailMessageSummary


class GmailEmailProvider:
    provider_name = "gmail"

    def __init__(
        self,
        *,
        account_identifier: str,
        folder_mapping: dict[str, str],
        auth_secret_refs: dict[str, str],
        timeout_seconds: float,
        api_base_url: str = "https://gmail.googleapis.com/gmail/v1",
    ) -> None:
        self.account_identifier = account_identifier
        self.folder_mapping = folder_mapping
        self.auth_secret_refs = auth_secret_refs
        self.timeout_seconds = timeout_seconds
        self.api_base_url = api_base_url.rstrip("/")

    def list_unread(self, mailbox_id: str, folder: str, limit: int) -> list[EmailMessageSummary]:
        label = self._folder_label(folder)
        query = f"is:unread in:{label}" if label else "is:unread"
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.get(
                self._url("messages"),
                headers=self._headers(),
                params={"q": query, "maxResults": max(1, limit)},
            )
            response.raise_for_status()
            items = response.json().get("messages", [])
            summaries: list[EmailMessageSummary] = []
            for item in items:
                message_id = str(item["id"])
                message = self._fetch_message(client, message_id=message_id, format_name="metadata")
                summaries.append(self._to_summary(mailbox_id=mailbox_id, folder=folder, payload=message))
        summaries.sort(key=lambda entry: entry.received_at, reverse=True)
        return summaries[:limit]

    def get_message(self, mailbox_id: str, message_id: str) -> EmailMessageDetail:
        with httpx.Client(timeout=self.timeout_seconds) as client:
            payload = self._fetch_message(client, message_id=message_id, format_name="full")
        return self._to_detail(mailbox_id=mailbox_id, payload=payload)

    def create_draft(
        self,
        mailbox_id: str,
        thread_or_message_id: str | None,
        subject: str | None,
        body: str,
        recipients: list[str],
    ) -> EmailDraft:
        mime = MimeMessage()
        mime["From"] = self.account_identifier
        mime["To"] = ", ".join(recipients)
        mime["Subject"] = subject or ""
        mime.set_content(body)
        raw_message = base64.urlsafe_b64encode(mime.as_bytes()).decode("utf-8")

        payload: dict[str, Any] = {"message": {"raw": raw_message}}
        if thread_or_message_id:
            payload["message"]["threadId"] = thread_or_message_id

        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.post(
                self._url("drafts"),
                headers=self._headers(),
                json=payload,
            )
            response.raise_for_status()
            data = response.json()

        now = datetime.now(UTC)
        draft_id = str(data.get("id") or data.get("draft", {}).get("id") or f"draft_{int(now.timestamp())}")
        message_payload = data.get("message", {})
        thread_id = str(message_payload.get("threadId") or thread_or_message_id or "") or None
        metadata = {
            "gmail_thread_id": thread_id,
            "gmail_message_id": message_payload.get("id"),
        }

        return EmailDraft(
            draft_id=draft_id,
            mailbox_id=mailbox_id,
            provider=self.provider_name,
            thread_or_message_id=thread_or_message_id,
            subject=subject,
            body=body,
            recipients=recipients,
            created_at=now,
            metadata=metadata,
        )

    def _url(self, path: str) -> str:
        return f"{self.api_base_url}/users/{self.account_identifier}/{path}"

    def _folder_label(self, folder: str) -> str:
        return self.folder_mapping.get(folder, folder)

    def _headers(self) -> dict[str, str]:
        token_ref = self.auth_secret_refs.get("access_token")
        if not token_ref:
            raise RuntimeError("gmail provider requires auth_secret_refs.access_token")
        token = os.environ.get(token_ref, "").strip()
        if not token:
            raise RuntimeError(f"gmail access token env var {token_ref!r} is empty")
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

    def _fetch_message(self, client: httpx.Client, *, message_id: str, format_name: str) -> dict[str, Any]:
        response = client.get(
            self._url(f"messages/{message_id}"),
            headers=self._headers(),
            params={"format": format_name},
        )
        response.raise_for_status()
        return response.json()

    def _to_summary(self, *, mailbox_id: str, folder: str, payload: dict[str, Any]) -> EmailMessageSummary:
        headers = self._header_map(payload)
        internal_ms = int(payload.get("internalDate", "0") or "0")
        received_at = datetime.fromtimestamp(internal_ms / 1000, tz=UTC) if internal_ms else datetime.now(UTC)
        thread_id = payload.get("threadId")
        metadata = {
            "gmail_thread_id": thread_id,
            "gmail_label_ids": payload.get("labelIds", []),
        }
        return EmailMessageSummary(
            mailbox_id=mailbox_id,
            provider=self.provider_name,
            folder=folder,
            message_id=str(payload.get("id", "")),
            thread_id=str(thread_id) if thread_id else None,
            sender=headers.get("from", self.account_identifier),
            recipients=self._split_recipients(headers.get("to", "")),
            received_at=received_at,
            unread="UNREAD" in payload.get("labelIds", []),
            body_snippet=str(payload.get("snippet", "")),
            metadata=metadata,
        )

    def _to_detail(self, *, mailbox_id: str, payload: dict[str, Any]) -> EmailMessageDetail:
        summary = self._to_summary(mailbox_id=mailbox_id, folder=self._primary_folder(payload), payload=payload)
        headers = self._header_map(payload)
        return EmailMessageDetail(
            **summary.model_dump(),
            subject=headers.get("subject", ""),
            body_text=self._extract_body_text(payload.get("payload", {})),
        )

    @staticmethod
    def _extract_body_text(payload: dict[str, Any]) -> str:
        body = payload.get("body", {})
        data = body.get("data")
        if data:
            try:
                return base64.urlsafe_b64decode(data + "==").decode("utf-8")
            except Exception:
                return ""
        for part in payload.get("parts", []):
            text = GmailEmailProvider._extract_body_text(part)
            if text:
                return text
        return ""

    @staticmethod
    def _header_map(payload: dict[str, Any]) -> dict[str, str]:
        headers = payload.get("payload", {}).get("headers", [])
        return {str(item.get("name", "")).lower(): str(item.get("value", "")) for item in headers}

    @staticmethod
    def _split_recipients(raw: str) -> list[str]:
        return [part.strip() for part in raw.split(",") if part.strip()]

    @staticmethod
    def _primary_folder(payload: dict[str, Any]) -> str:
        labels = payload.get("labelIds", [])
        if "INBOX" in labels:
            return "inbox"
        if "DRAFT" in labels:
            return "drafts"
        return "unknown"
