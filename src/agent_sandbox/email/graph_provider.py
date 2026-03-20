from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any

import httpx

from .providers import EmailDraft, EmailMessageDetail, EmailMessageSummary


class GraphEmailProvider:
    provider_name = "graph"

    def __init__(
        self,
        *,
        account_identifier: str,
        folder_mapping: dict[str, str],
        auth_secret_refs: dict[str, str],
        timeout_seconds: float,
        api_base_url: str = "https://graph.microsoft.com/v1.0",
    ) -> None:
        self.account_identifier = account_identifier
        self.folder_mapping = folder_mapping
        self.auth_secret_refs = auth_secret_refs
        self.timeout_seconds = timeout_seconds
        self.api_base_url = api_base_url.rstrip("/")

    def list_unread(self, mailbox_id: str, folder: str, limit: int) -> list[EmailMessageSummary]:
        graph_folder_id = self.folder_mapping.get(folder, folder)
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.get(
                self._url(f"mailFolders/{graph_folder_id}/messages"),
                headers=self._headers(),
                params={
                    "$filter": "isRead eq false",
                    "$orderby": "receivedDateTime DESC",
                    "$top": max(1, limit),
                },
            )
            response.raise_for_status()
            items = response.json().get("value", [])

        return [self._to_summary(mailbox_id=mailbox_id, folder=folder, payload=item) for item in items]

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
