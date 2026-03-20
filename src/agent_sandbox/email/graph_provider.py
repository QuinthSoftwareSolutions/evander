from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from .cursor_store import DurableCursorStore, EmailSyncCursor
from .providers import EmailDraft, EmailMessageDetail, EmailMessageSummary


class GraphEmailProvider:
    provider_name = "graph"
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
        api_base_url: str = "https://graph.microsoft.com/v1.0",
    ) -> None:
        self.account_identifier = account_identifier
        self.folder_mapping = folder_mapping
        self.auth_secret_refs = auth_secret_refs
        self.timeout_seconds = timeout_seconds
        self.cursor_store = cursor_store
        self.api_base_url = api_base_url.rstrip("/")

    def list_unread(self, mailbox_id: str, folder: str, limit: int) -> list[EmailMessageSummary]:
        graph_folder_id = self.folder_mapping.get(folder, folder)
        cursor = self.cursor_store.get(mailbox_id, folder) if self.cursor_store else None
        with httpx.Client(timeout=self.timeout_seconds) as client:
            items, next_delta_link = self._list_incremental(
                client=client,
                graph_folder_id=graph_folder_id,
                limit=limit,
                cursor=cursor,
            )

        summaries = [self._to_summary(mailbox_id=mailbox_id, folder=folder, payload=item) for item in items]
        summaries = self._dedupe_with_cursor(mailbox_id, folder, summaries, next_delta_link)
        summaries.sort(key=lambda entry: entry.received_at, reverse=True)
        return summaries[:limit]

    def get_message(self, mailbox_id: str, message_id: str) -> EmailMessageDetail:
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.get(
                self._url(f"messages/{message_id}"),
                headers=self._headers(),
                params={"$select": "id,subject,body,from,toRecipients,isRead,receivedDateTime,conversationId,parentFolderId"},
            )
            response.raise_for_status()
            payload = response.json()

        return self._to_detail(mailbox_id=mailbox_id, payload=payload)

    def create_draft(
        self,
        mailbox_id: str,
        thread_or_message_id: str | None,
        subject: str | None,
        body: str,
        recipients: list[str],
    ) -> EmailDraft:
        json_payload = {
            "subject": subject or "",
            "body": {"contentType": "text", "content": body},
            "toRecipients": [
                {"emailAddress": {"address": address}}
                for address in recipients
            ],
        }
        if thread_or_message_id:
            json_payload["conversationId"] = thread_or_message_id

        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.post(
                self._url("messages"),
                headers=self._headers(),
                json=json_payload,
            )
            response.raise_for_status()
            payload = response.json()

        now = datetime.now(UTC)
        conversation_id = payload.get("conversationId") or thread_or_message_id
        return EmailDraft(
            draft_id=str(payload.get("id") or f"draft_{int(now.timestamp())}"),
            mailbox_id=mailbox_id,
            provider=self.provider_name,
            thread_or_message_id=thread_or_message_id,
            subject=subject,
            body=body,
            recipients=recipients,
            created_at=now,
            metadata={
                "graph_conversation_id": conversation_id,
                "graph_message_id": payload.get("id"),
            },
        )

    def _url(self, path: str) -> str:
        return f"{self.api_base_url}/users/{self.account_identifier}/{path}"

    def _headers(self) -> dict[str, str]:
        token_ref = self.auth_secret_refs.get("access_token")
        if not token_ref:
            raise RuntimeError("graph provider requires auth_secret_refs.access_token")
        token = os.environ.get(token_ref, "").strip()
        if not token:
            raise RuntimeError(f"graph access token env var {token_ref!r} is empty")
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

    def _to_summary(self, *, mailbox_id: str, folder: str, payload: dict[str, Any]) -> EmailMessageSummary:
        received_at = datetime.fromisoformat(
            str(payload.get("receivedDateTime", datetime.now(UTC).isoformat())).replace("Z", "+00:00")
        )
        sender = ((payload.get("from") or {}).get("emailAddress") or {}).get("address", self.account_identifier)
        recipients = [
            ((recipient.get("emailAddress") or {}).get("address", "")).strip()
            for recipient in payload.get("toRecipients", [])
        ]
        recipients = [address for address in recipients if address]
        conversation_id = payload.get("conversationId")
        return EmailMessageSummary(
            mailbox_id=mailbox_id,
            provider=self.provider_name,
            folder=folder,
            message_id=str(payload.get("id", "")),
            thread_id=str(conversation_id) if conversation_id else None,
            sender=sender,
            recipients=recipients,
            received_at=received_at,
            unread=not bool(payload.get("isRead", False)),
            body_snippet=str(payload.get("bodyPreview", "")),
            metadata={
                "graph_conversation_id": conversation_id,
                "graph_parent_folder_id": payload.get("parentFolderId"),
            },
        )

    def _to_detail(self, *, mailbox_id: str, payload: dict[str, Any]) -> EmailMessageDetail:
        summary = self._to_summary(
            mailbox_id=mailbox_id,
            folder=self._policy_folder_from_parent(payload.get("parentFolderId")),
            payload=payload,
        )
        body = payload.get("body") or {}
        return EmailMessageDetail(
            **summary.model_dump(),
            subject=str(payload.get("subject", "")),
            body_text=str(body.get("content", "")),
        )

    def _list_incremental(
        self,
        *,
        client: httpx.Client,
        graph_folder_id: str,
        limit: int,
        cursor: EmailSyncCursor | None,
    ) -> tuple[list[dict[str, Any]], str | None]:
        if cursor and cursor.primitive_cursor:
            try:
                return self._list_from_delta_link(client, cursor.primitive_cursor, limit)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code not in {400, 404, 410}:
                    raise
        return self._list_bounded_polling(client, graph_folder_id, limit)

    def _list_from_delta_link(
        self, client: httpx.Client, delta_link: str, limit: int
    ) -> tuple[list[dict[str, Any]], str | None]:
        unread: list[dict[str, Any]] = []
        current_url: str | None = delta_link
        next_delta_link: str | None = delta_link
        while current_url and len(unread) < max(1, limit * 2):
            response = client.get(current_url, headers=self._headers())
            response.raise_for_status()
            payload = response.json()
            for item in payload.get("value", []):
                if item.get("@removed"):
                    continue
                if bool(item.get("isRead", False)):
                    continue
                unread.append(item)
            current_url = payload.get("@odata.nextLink")
            if payload.get("@odata.deltaLink"):
                next_delta_link = str(payload["@odata.deltaLink"])
        return unread, next_delta_link

    def _list_bounded_polling(
        self, client: httpx.Client, graph_folder_id: str, limit: int
    ) -> tuple[list[dict[str, Any]], str | None]:
        since = (datetime.now(UTC) - timedelta(days=self._fallback_lookback_days)).isoformat().replace("+00:00", "Z")
        response = client.get(
            self._url(f"mailFolders/{graph_folder_id}/messages/delta"),
            headers=self._headers(),
            params={
                "$select": "id,subject,bodyPreview,from,toRecipients,isRead,receivedDateTime,conversationId,parentFolderId",
                "$filter": f"isRead eq false and receivedDateTime ge {since}",
                "$orderby": "receivedDateTime DESC",
                "$top": max(1, limit * 2),
            },
        )
        response.raise_for_status()
        payload = response.json()
        unread = [item for item in payload.get("value", []) if not bool(item.get("isRead", False))]
        next_delta_link = payload.get("@odata.deltaLink") or payload.get("@odata.nextLink")
        return unread, str(next_delta_link) if next_delta_link else None

    def _dedupe_with_cursor(
        self,
        mailbox_id: str,
        folder: str,
        summaries: list[EmailMessageSummary],
        next_delta_link: str | None,
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
            return EmailSyncCursor(
                mailbox_id=mailbox_id,
                folder=folder,
                provider=self.provider_name,
                primitive_cursor=next_delta_link or (current.primitive_cursor if current else None),
                cursor_version=(current.cursor_version + 1) if current else 1,
                updated_at=datetime.now(UTC),
                recent_message_ids=ordered_ids[-self._dedupe_window_size :],
            )

        self.cursor_store.update(mailbox_id, folder, _update)
        return accepted

    def _policy_folder_from_parent(self, parent_folder_id: Any) -> str:
        if not parent_folder_id:
            return "unknown"
        folder_id = str(parent_folder_id)
        if not folder_id:
            return "unknown"

        for policy_folder, provider_folder in self.folder_mapping.items():
            mapped_folder = str(provider_folder)
            if mapped_folder == folder_id:
                return policy_folder
            if mapped_folder.casefold() == folder_id.casefold():
                return policy_folder

        for policy_folder in self.folder_mapping:
            if policy_folder.casefold() == folder_id.casefold():
                return policy_folder

        return "unknown"
