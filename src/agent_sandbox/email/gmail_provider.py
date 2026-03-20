from __future__ import annotations

import base64
import os
from datetime import UTC, datetime
from email.message import EmailMessage as MimeMessage
from typing import Any

import httpx

from .cursor_store import DurableCursorStore, EmailSyncCursor
from .providers import EmailDraft, EmailMessageDetail, EmailMessageSummary


class GmailEmailProvider:
    provider_name = "gmail"
    _fallback_lookback_days = 7
    _dedupe_window_size = 512

    def __init__(
        self,
        *,
        account_identifier: str,
        folder_mapping: dict[str, str],
        auth_secret_refs: dict[str, str],
        timeout_seconds: float,
        cursor_store: DurableCursorStore | None = None,
        api_base_url: str = "https://gmail.googleapis.com/gmail/v1",
    ) -> None:
        self.account_identifier = account_identifier
        self.folder_mapping = folder_mapping
        self.auth_secret_refs = auth_secret_refs
        self.timeout_seconds = timeout_seconds
        self.cursor_store = cursor_store
        self.api_base_url = api_base_url.rstrip("/")

    def list_unread(self, mailbox_id: str, folder: str, limit: int) -> list[EmailMessageSummary]:
        label = self._folder_label(folder)
        cursor = self.cursor_store.get(mailbox_id, folder) if self.cursor_store else None
        with httpx.Client(timeout=self.timeout_seconds) as client:
            items, cursor_primitive = self._list_incremental(
                client=client,
                label=label,
                cursor=cursor,
                limit=limit,
            )
            summaries = [
                self._to_summary(mailbox_id=mailbox_id, folder=folder, payload=payload) for payload in items
            ]
        summaries = self._dedupe_with_cursor(mailbox_id, folder, summaries, cursor_primitive)
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

    def _list_incremental(
        self,
        *,
        client: httpx.Client,
        label: str,
        cursor: EmailSyncCursor | None,
        limit: int,
    ) -> tuple[list[dict[str, Any]], str | None]:
        if cursor and cursor.primitive_cursor:
            try:
                return self._list_from_history(
                    client=client,
                    label=label,
                    history_id=cursor.primitive_cursor,
                    limit=limit,
                )
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code not in {400, 404}:
                    raise
        return self._list_bounded_polling(client=client, label=label, limit=limit)

    def _list_from_history(
        self,
        *,
        client: httpx.Client,
        label: str,
        history_id: str,
        limit: int,
    ) -> tuple[list[dict[str, Any]], str | None]:
        params: dict[str, Any] = {
            "startHistoryId": history_id,
            "historyTypes": ["messageAdded", "labelAdded"],
            "maxResults": max(1, limit * 4),
        }
        if label:
            params["labelId"] = label

        response = client.get(self._url("history"), headers=self._headers(), params=params)
        response.raise_for_status()
        payload = response.json()
        newest_history_id = str(payload.get("historyId", history_id))
        message_ids: set[str] = set()
        for entry in payload.get("history", []):
            for section in ("messagesAdded", "labelsAdded"):
                for item in entry.get(section, []):
                    message = item.get("message", {})
                    candidate = str(message.get("id", "")).strip()
                    if candidate:
                        message_ids.add(candidate)
        messages = [
            self._fetch_message(client, message_id=message_id, format_name="metadata")
            for message_id in message_ids
        ]
        messages = [msg for msg in messages if "UNREAD" in msg.get("labelIds", [])]
        return messages, newest_history_id

    def _list_bounded_polling(
        self,
        *,
        client: httpx.Client,
        label: str,
        limit: int,
    ) -> tuple[list[dict[str, Any]], str | None]:
        query_parts = [f"newer_than:{self._fallback_lookback_days}d", "is:unread"]
        if label:
            query_parts.append(f"in:{label}")
        response = client.get(
            self._url("messages"),
            headers=self._headers(),
            params={"q": " ".join(query_parts), "maxResults": max(1, limit * 2)},
        )
        response.raise_for_status()
        items = response.json().get("messages", [])
        messages: list[dict[str, Any]] = []
        highest_history_id: int | None = None
        for item in items:
            message_id = str(item["id"])
            message = self._fetch_message(client, message_id=message_id, format_name="metadata")
            messages.append(message)
            candidate_raw = str(message.get("historyId", "")).strip()
            if candidate_raw.isdigit():
                candidate = int(candidate_raw)
                highest_history_id = candidate if highest_history_id is None else max(highest_history_id, candidate)
        return messages, str(highest_history_id) if highest_history_id is not None else None

    def _dedupe_with_cursor(
        self,
        mailbox_id: str,
        folder: str,
        summaries: list[EmailMessageSummary],
        next_cursor_primitive: str | None,
    ) -> list[EmailMessageSummary]:
        if not self.cursor_store:
            return summaries

        accepted: list[EmailMessageSummary] = []

        def _update(current: EmailSyncCursor | None) -> EmailSyncCursor:
            ordered_ids = list(current.recent_message_ids if current else [])
            prior_ids = set(ordered_ids)
            for summary in summaries:
                if summary.message_id in prior_ids:
                    continue
                accepted.append(summary)
                prior_ids.add(summary.message_id)
                ordered_ids.append(summary.message_id)

            previous_cursor = current.primitive_cursor if current else None
            chosen_cursor = self._max_history_id(previous_cursor, next_cursor_primitive)
            window = ordered_ids[-self._dedupe_window_size :]
            return EmailSyncCursor(
                mailbox_id=mailbox_id,
                folder=folder,
                provider=self.provider_name,
                primitive_cursor=chosen_cursor,
                cursor_version=(current.cursor_version + 1) if current else 1,
                updated_at=datetime.now(UTC),
                recent_message_ids=window,
            )

        self.cursor_store.update(mailbox_id, folder, _update)
        return accepted

    @staticmethod
    def _max_history_id(previous: str | None, candidate: str | None) -> str | None:
        if candidate is None:
            return previous
        if previous is None:
            return candidate
        if previous.isdigit() and candidate.isdigit():
            return str(max(int(previous), int(candidate)))
        return candidate

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
