from __future__ import annotations

import base64
from collections.abc import Callable
from dataclasses import dataclass
from hashlib import sha256
import json
import os
from datetime import UTC, datetime, timedelta
from threading import Lock
from time import monotonic
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

import httpx

if TYPE_CHECKING:
    from evander.repositories.cursors import EmailCursorRepository

from .cursor_store import EmailSyncCursor
from .http_utils import ProviderRequestError, request_with_retries
from .providers import (
    EmailAttachmentContent,
    EmailAttachmentMetadata,
    EmailDraft,
    EmailDraftAttachment,
    EmailMessageDetail,
    EmailMessagePage,
    EmailMessageSearchQuery,
    EmailMessageSummary,
    EmailSendResult,
)


@dataclass(frozen=True)
class _CachedGraphAccessToken:
    value: str
    refresh_at_monotonic: float


class GraphAccessTokenCache:
    """Process-local client-credentials token cache shared by Graph providers."""

    _lock_stripe_count = 16

    def __init__(self) -> None:
        self._entries: dict[str, _CachedGraphAccessToken] = {}
        self._entries_lock = Lock()
        self._acquisition_locks = tuple(Lock() for _ in range(self._lock_stripe_count))

    def get_or_acquire(
        self,
        *,
        credential_key: str,
        acquire: Callable[[], tuple[str, float]],
        expiry_skew_seconds: float,
    ) -> str:
        cached = self._valid_entry(credential_key)
        if cached is not None:
            return cached.value

        acquisition_lock = self._acquisition_locks[
            int(credential_key[:8], 16) % self._lock_stripe_count
        ]
        with acquisition_lock:
            cached = self._valid_entry(credential_key)
            if cached is not None:
                return cached.value

            access_token, expires_in = acquire()
            refresh_skew = min(expiry_skew_seconds, expires_in * 0.1)
            cached = _CachedGraphAccessToken(
                value=access_token,
                refresh_at_monotonic=(
                    monotonic() + max(0.0, expires_in - refresh_skew)
                ),
            )
            with self._entries_lock:
                now = monotonic()
                self._entries = {
                    key: entry
                    for key, entry in self._entries.items()
                    if entry.refresh_at_monotonic > now
                }
                self._entries[credential_key] = cached
            return cached.value

    def _valid_entry(self, credential_key: str) -> _CachedGraphAccessToken | None:
        now = monotonic()
        with self._entries_lock:
            cached = self._entries.get(credential_key)
            if cached is not None and cached.refresh_at_monotonic > now:
                return cached
            self._entries.pop(credential_key, None)
        return None


_SHARED_GRAPH_ACCESS_TOKEN_CACHE = GraphAccessTokenCache()


class GraphEmailProvider:
    provider_name = "graph"
    _attachment_metadata_select = "id,name,contentType,size,isInline,lastModifiedDateTime"
    _fallback_lookback_days = 7
    _dedupe_window_size = 512
    _large_attachment_threshold_bytes = 3 * 1024 * 1024
    _upload_chunk_size = 5 * 1024 * 1024
    _client_credential_ref_names = ("tenant_id", "client_id", "client_secret")
    _client_credentials_scope = "https://graph.microsoft.com/.default"
    _token_expiry_skew_seconds = 60.0

    def __init__(
        self,
        *,
        account_identifier: str,
        folder_mapping: dict[str, str],
        auth_secret_refs: dict[str, str],
        timeout_seconds: float,
        cursor_store: "EmailCursorRepository | None" = None,
        api_base_url: str = "https://graph.microsoft.com/v1.0",
        oauth_authority_url: str = "https://login.microsoftonline.com",
        access_token_cache: GraphAccessTokenCache | None = None,
    ) -> None:
        self.account_identifier = account_identifier
        self.folder_mapping = folder_mapping
        self.auth_secret_refs = auth_secret_refs
        self.timeout_seconds = timeout_seconds
        self.cursor_store = cursor_store
        self.api_base_url = api_base_url.rstrip("/")
        self.oauth_authority_url = oauth_authority_url.rstrip("/")
        self._access_token_cache = (
            access_token_cache
            if access_token_cache is not None
            else _SHARED_GRAPH_ACCESS_TOKEN_CACHE
        )

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

        summaries = [
            self._to_summary(mailbox_id=mailbox_id, folder=folder, payload=item) for item in items
        ]
        summaries = self._dedupe_with_cursor(mailbox_id, folder, summaries, next_delta_link)
        summaries.sort(key=lambda entry: entry.received_at, reverse=True)
        return summaries[:limit]

    def list_messages(
        self,
        mailbox_id: str,
        folder: str,
        query: EmailMessageSearchQuery,
        cursor: str | None,
        limit: int,
    ) -> EmailMessagePage:
        headers = self._headers()
        uses_search = self._uses_search(query)
        if uses_search:
            headers["ConsistencyLevel"] = "eventual"
        if cursor:
            url = cursor
            params: dict[str, Any] | None = None
        else:
            graph_folder_id = self.folder_mapping.get(folder, folder)
            url = self._url(f"mailFolders/{graph_folder_id}/messages")
            params = self._search_params(query=query, limit=limit)
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = request_with_retries(
                client=client,
                provider=self.provider_name,
                operation="list_messages",
                method="GET",
                url=url,
                headers=headers,
                params=params,
            )
            payload = response.json()
        items = [item for item in payload.get("value", []) if not item.get("@removed")]
        if uses_search:
            items = [item for item in items if self._matches_structured_filters(item, query)]
        summaries = [
            self._to_summary(mailbox_id=mailbox_id, folder=folder, payload=item) for item in items
        ]
        summaries.sort(key=lambda entry: entry.received_at, reverse=True)
        next_cursor = str(payload.get("@odata.nextLink", "")).strip() or None
        return EmailMessagePage(messages=summaries, next_cursor=next_cursor)

    def get_message(self, mailbox_id: str, message_id: str) -> EmailMessageDetail:
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = request_with_retries(
                client=client,
                provider=self.provider_name,
                operation="get_message",
                method="GET",
                url=self._url(f"messages/{message_id}"),
                headers=self._headers(),
                params={
                    "$select": (
                        "id,subject,body,from,toRecipients,isRead,receivedDateTime,"
                        "conversationId,parentFolderId,hasAttachments"
                    ),
                    "$expand": f"attachments($select={self._attachment_metadata_select})",
                },
            )
            payload = response.json()
            policy_folder = self._policy_folder_from_parent(payload.get("parentFolderId"))
            if policy_folder == "unknown":
                policy_folder = self._resolve_policy_folder(
                    client=client,
                    parent_folder_id=payload.get("parentFolderId"),
                )

        return self._to_detail(
            mailbox_id=mailbox_id,
            payload=payload,
            policy_folder=policy_folder,
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
        attachment_items = attachments or []
        small_attachments = [
            item
            for item in attachment_items
            if len(item.content) < self._large_attachment_threshold_bytes
        ]
        large_attachments = [
            item
            for item in attachment_items
            if len(item.content) >= self._large_attachment_threshold_bytes
        ]
        json_payload = {
            "subject": subject or "",
            "body": {"contentType": "text", "content": body},
            "toRecipients": [{"emailAddress": {"address": address}} for address in recipients],
        }
        if small_attachments:
            json_payload["attachments"] = [
                self._draft_attachment_payload(attachment) for attachment in small_attachments
            ]
        if thread_or_message_id:
            json_payload["conversationId"] = thread_or_message_id

        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = request_with_retries(
                client=client,
                provider=self.provider_name,
                operation="create_draft",
                method="POST",
                url=self._url("messages"),
                headers=self._headers(),
                json=json_payload,
            )
            payload = response.json()
            draft_message_id = str(payload.get("id") or "")
            if large_attachments and not draft_message_id:
                raise ValueError("Graph did not return a draft id for large attachment upload")
            for attachment in large_attachments:
                self._upload_large_attachment(
                    client=client, message_id=draft_message_id, attachment=attachment
                )

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
                "attachment_count": len(attachment_items),
                "large_attachment_count": len(large_attachments),
            },
        )

    def send_draft(self, mailbox_id: str, draft_id: str) -> EmailSendResult:
        with httpx.Client(timeout=self.timeout_seconds) as client:
            request_with_retries(
                client=client,
                provider=self.provider_name,
                operation="send_draft",
                method="POST",
                url=self._url(f"messages/{draft_id}/send"),
                headers=self._headers(),
                json={},
            )

        sent_at = datetime.now(UTC)
        return EmailSendResult(
            status="sent",
            draft_id=draft_id,
            mailbox_id=mailbox_id,
            provider=self.provider_name,
            message_id=draft_id,
            sent_at=sent_at,
            metadata={"graph_message_id": draft_id},
        )

    def list_attachments(self, mailbox_id: str, message_id: str) -> list[EmailAttachmentMetadata]:
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = request_with_retries(
                client=client,
                provider=self.provider_name,
                operation="list_attachments",
                method="GET",
                url=self._url(f"messages/{message_id}/attachments"),
                headers=self._headers(),
                params={"$select": self._attachment_metadata_select},
            )
            payload = response.json()
        return [self._attachment_metadata(item) for item in payload.get("value", [])]

    def download_attachment(
        self, mailbox_id: str, message_id: str, attachment_id: str
    ) -> EmailAttachmentContent:
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = request_with_retries(
                client=client,
                provider=self.provider_name,
                operation="download_attachment",
                method="GET",
                url=self._url(f"messages/{message_id}/attachments/{attachment_id}"),
                headers=self._headers(),
            )
            payload = response.json()

        odata_type = str(payload.get("@odata.type", ""))
        if "fileAttachment" not in odata_type:
            raise ValueError(f"Graph attachment {attachment_id} is not a fileAttachment")
        content_bytes = str(payload.get("contentBytes", ""))
        if not content_bytes:
            raise ValueError(f"Graph attachment {attachment_id} did not include contentBytes")
        metadata = self._attachment_metadata(payload)
        return EmailAttachmentContent(
            **metadata.model_dump(), content=base64.b64decode(content_bytes)
        )

    def _url(self, path: str) -> str:
        return f"{self.api_base_url}/users/{self.account_identifier}/{path}"

    def _headers(self) -> dict[str, str]:
        token = self._access_token()
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

    def _access_token(self) -> str:
        client_credential_refs = {
            name: str(self.auth_secret_refs.get(name, "")).strip()
            for name in self._client_credential_ref_names
        }
        if any(client_credential_refs.values()):
            missing_refs = [name for name, ref in client_credential_refs.items() if not ref]
            if missing_refs:
                raise RuntimeError(
                    "graph OAuth client credentials require auth_secret_refs."
                    + ", auth_secret_refs.".join(missing_refs)
                )
            return self._client_credentials_access_token(client_credential_refs)

        token_ref = self.auth_secret_refs.get("access_token")
        if not token_ref:
            raise RuntimeError(
                "graph provider requires auth_secret_refs.access_token or OAuth client "
                "credential refs tenant_id, client_id, and client_secret"
            )
        token = os.environ.get(token_ref, "").strip()
        if not token:
            raise RuntimeError(f"graph access token env var {token_ref!r} is empty")
        return token

    def _client_credentials_access_token(self, refs: dict[str, str]) -> str:
        credentials = {name: os.environ.get(ref, "").strip() for name, ref in refs.items()}
        missing_values = [name for name, value in credentials.items() if not value]
        if missing_values:
            missing_env_vars = ", ".join(repr(refs[name]) for name in missing_values)
            raise RuntimeError(
                "graph OAuth client credential environment variables are empty: "
                f"{missing_env_vars}"
            )

        credential_key = self._client_credentials_cache_key(credentials)
        return self._access_token_cache.get_or_acquire(
            credential_key=credential_key,
            acquire=lambda: self._acquire_client_credentials_access_token(credentials),
            expiry_skew_seconds=self._token_expiry_skew_seconds,
        )

    def _client_credentials_cache_key(self, credentials: dict[str, str]) -> str:
        identity = {
            "oauth_authority_url": self.oauth_authority_url,
            "tenant_id": credentials["tenant_id"],
            "client_id": credentials["client_id"],
            "client_secret": credentials["client_secret"],
            "scope": self._client_credentials_scope,
        }
        encoded = json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return sha256(encoded).hexdigest()

    def _acquire_client_credentials_access_token(
        self, credentials: dict[str, str]
    ) -> tuple[str, float]:
        token_endpoint = (
            f"{self.oauth_authority_url}/{quote(credentials['tenant_id'], safe='')}"
            "/oauth2/v2.0/token"
        )
        with httpx.Client(timeout=self.timeout_seconds) as client:
            try:
                response = request_with_retries(
                    client=client,
                    provider=self.provider_name,
                    operation="acquire_access_token",
                    method="POST",
                    url=token_endpoint,
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                    data={
                        "client_id": credentials["client_id"],
                        "client_secret": credentials["client_secret"],
                        "scope": self._client_credentials_scope,
                        "grant_type": "client_credentials",
                    },
                    retry_non_idempotent_methods=True,
                )
            except ProviderRequestError as exc:
                raise RuntimeError(
                    "graph OAuth client-credentials token acquisition failed: " f"{exc}"
                ) from exc
        try:
            payload = response.json()
        except ValueError as exc:
            raise RuntimeError(
                "graph OAuth client-credentials token response was not valid JSON"
            ) from exc
        access_token = str(payload.get("access_token", "")).strip()
        if not access_token:
            raise RuntimeError(
                "graph OAuth client-credentials token response did not include access_token"
            )
        try:
            expires_in = max(0.0, float(payload.get("expires_in", 3600)))
        except (TypeError, ValueError):
            expires_in = 3600.0
        return access_token, expires_in

    @staticmethod
    def _graph_datetime(value: datetime) -> str:
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")

    @staticmethod
    def _uses_search(query: EmailMessageSearchQuery) -> bool:
        return any(
            (
                query.sender_contains,
                query.recipient_contains,
                query.subject_contains,
                query.text_contains,
            )
        )

    @staticmethod
    def _kql_value(value: str) -> str:
        return " ".join(value.replace("\\", " ").replace('"', " ").split())

    def _search_expression(self, query: EmailMessageSearchQuery) -> str:
        clauses: list[str] = []
        if query.text_contains:
            clauses.append(self._kql_value(query.text_contains))
        if query.sender_contains:
            clauses.append(f"from:{self._kql_value(query.sender_contains)}")
        if query.recipient_contains:
            clauses.append(f"to:{self._kql_value(query.recipient_contains)}")
        if query.subject_contains:
            clauses.append(f"subject:{self._kql_value(query.subject_contains)}")
        return f'"{" AND ".join(clauses)}"'

    def _matches_structured_filters(
        self, payload: dict[str, Any], query: EmailMessageSearchQuery
    ) -> bool:
        received_at = datetime.fromisoformat(
            str(payload.get("receivedDateTime", datetime.min.replace(tzinfo=UTC).isoformat())).replace(
                "Z", "+00:00"
            )
        )
        if query.received_after is not None and received_at < query.received_after:
            return False
        if query.received_before is not None and received_at >= query.received_before:
            return False
        if query.unread is not None and (not bool(payload.get("isRead", False))) is not query.unread:
            return False
        if (
            query.has_attachments is not None
            and bool(payload.get("hasAttachments", False)) is not query.has_attachments
        ):
            return False
        return True

    def _search_params(self, *, query: EmailMessageSearchQuery, limit: int) -> dict[str, Any]:
        params: dict[str, Any] = {
            "$select": (
                "id,subject,bodyPreview,from,toRecipients,isRead,receivedDateTime,"
                "conversationId,parentFolderId,hasAttachments"
            ),
            "$top": limit,
        }
        if self._uses_search(query):
            params["$search"] = self._search_expression(query)
            return params

        filters: list[str] = []
        if query.received_after is not None:
            filters.append(f"receivedDateTime ge {self._graph_datetime(query.received_after)}")
        if query.received_before is not None:
            filters.append(f"receivedDateTime lt {self._graph_datetime(query.received_before)}")
        if not filters and (query.unread is not None or query.has_attachments is not None):
            filters.append("receivedDateTime ge 1900-01-01T00:00:00Z")
        if query.unread is not None:
            filters.append(f"isRead eq {'false' if query.unread else 'true'}")
        if query.has_attachments is not None:
            filters.append(f"hasAttachments eq {'true' if query.has_attachments else 'false'}")
        if filters:
            params["$filter"] = " and ".join(filters)
        params["$orderby"] = "receivedDateTime DESC"
        return params

    def _to_summary(
        self, *, mailbox_id: str, folder: str, payload: dict[str, Any]
    ) -> EmailMessageSummary:
        received_at = datetime.fromisoformat(
            str(payload.get("receivedDateTime", datetime.now(UTC).isoformat())).replace(
                "Z", "+00:00"
            )
        )
        sender = ((payload.get("from") or {}).get("emailAddress") or {}).get(
            "address", self.account_identifier
        )
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
            subject=str(payload.get("subject", "")),
            received_at=received_at,
            unread=not bool(payload.get("isRead", False)),
            body_snippet=str(payload.get("bodyPreview", "")),
            metadata={
                "graph_conversation_id": conversation_id,
                "graph_parent_folder_id": payload.get("parentFolderId"),
            },
        )

    def _to_detail(
        self,
        *,
        mailbox_id: str,
        payload: dict[str, Any],
        policy_folder: str | None = None,
    ) -> EmailMessageDetail:
        summary = self._to_summary(
            mailbox_id=mailbox_id,
            folder=(
                policy_folder
                if policy_folder is not None
                else self._policy_folder_from_parent(payload.get("parentFolderId"))
            ),
            payload=payload,
        )
        body = payload.get("body") or {}
        return EmailMessageDetail(
            **summary.model_dump(),
            body_text=str(body.get("content", "")),
            attachments=[
                self._attachment_metadata(item) for item in self._expanded_attachment_items(payload)
            ],
        )

    @staticmethod
    def _expanded_attachment_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
        attachments = payload.get("attachments")
        if isinstance(attachments, list):
            return [item for item in attachments if isinstance(item, dict)]
        if isinstance(attachments, dict):
            value = attachments.get("value", [])
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        return []

    @staticmethod
    def _attachment_metadata(payload: dict[str, Any]) -> EmailAttachmentMetadata:
        return EmailAttachmentMetadata(
            attachment_id=str(payload.get("id", "")),
            filename=str(payload.get("name", "")),
            mime_type=str(payload.get("contentType", "")) or None,
            byte_count=payload.get("size"),
            inline=bool(payload.get("isInline", False)),
            content_id=payload.get("contentId"),
            metadata={
                "graph_attachment_type": payload.get("@odata.type"),
                "graph_last_modified": payload.get("lastModifiedDateTime"),
            },
        )

    @staticmethod
    def _draft_attachment_payload(attachment: EmailDraftAttachment) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "@odata.type": "#microsoft.graph.fileAttachment",
            "name": attachment.filename,
            "contentType": attachment.mime_type or "application/octet-stream",
            "contentBytes": base64.b64encode(attachment.content).decode("ascii"),
            "isInline": attachment.inline,
        }
        if attachment.content_id:
            payload["contentId"] = attachment.content_id
        return payload

    def _upload_large_attachment(
        self, *, client: httpx.Client, message_id: str, attachment: EmailDraftAttachment
    ) -> None:
        response = request_with_retries(
            client=client,
            provider=self.provider_name,
            operation="create_attachment_upload_session",
            method="POST",
            url=self._url(f"messages/{message_id}/attachments/createUploadSession"),
            headers=self._headers(),
            json={
                "AttachmentItem": {
                    "attachmentType": "file",
                    "name": attachment.filename,
                    "size": len(attachment.content),
                    "contentType": attachment.mime_type or "application/octet-stream",
                    "isInline": attachment.inline,
                }
            },
        )
        upload_url = str(response.json().get("uploadUrl", ""))
        if not upload_url:
            raise ValueError("Graph attachment upload session did not include uploadUrl")
        for start in range(0, len(attachment.content), self._upload_chunk_size):
            chunk = attachment.content[start : start + self._upload_chunk_size]
            end = start + len(chunk) - 1
            upload_response = client.request(
                "PUT",
                upload_url,
                content=chunk,
                headers={
                    "Content-Length": str(len(chunk)),
                    "Content-Range": f"bytes {start}-{end}/{len(attachment.content)}",
                },
            )
            upload_response.raise_for_status()

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
            except ProviderRequestError as exc:
                if exc.status_code not in {400, 404, 410}:
                    raise
        return self._list_bounded_polling(client, graph_folder_id, limit)

    def _list_from_delta_link(
        self, client: httpx.Client, delta_link: str, limit: int
    ) -> tuple[list[dict[str, Any]], str | None]:
        unread: list[dict[str, Any]] = []
        current_url: str | None = delta_link
        next_delta_link: str | None = delta_link
        max_unread = max(1, limit * 2)
        while current_url:
            response = request_with_retries(
                client=client,
                provider=self.provider_name,
                operation="list_from_delta_link",
                method="GET",
                url=current_url,
                headers=self._headers(),
            )
            payload = response.json()
            for item in payload.get("value", []):
                if item.get("@removed"):
                    continue
                if bool(item.get("isRead", False)):
                    continue
                if len(unread) < max_unread:
                    unread.append(item)
            current_url = payload.get("@odata.nextLink")
            if payload.get("@odata.deltaLink"):
                next_delta_link = str(payload["@odata.deltaLink"])
        return unread, next_delta_link

    def _list_bounded_polling(
        self, client: httpx.Client, graph_folder_id: str, limit: int
    ) -> tuple[list[dict[str, Any]], str | None]:
        since = (
            (datetime.now(UTC) - timedelta(days=self._fallback_lookback_days))
            .isoformat()
            .replace("+00:00", "Z")
        )
        response = request_with_retries(
            client=client,
            provider=self.provider_name,
            operation="list_bounded_polling",
            method="GET",
            url=self._url(f"mailFolders/{graph_folder_id}/messages/delta"),
            headers=self._headers(),
            params={
                "$select": (
                    "id,subject,bodyPreview,from,toRecipients,isRead,receivedDateTime,"
                    "conversationId,parentFolderId"
                ),
                "$filter": f"isRead eq false and receivedDateTime ge {since}",
                "$orderby": "receivedDateTime DESC",
                "$top": max(1, limit * 2),
            },
        )
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

    def _resolve_policy_folder(self, *, client: httpx.Client, parent_folder_id: Any) -> str:
        normalized_parent_folder_id = str(parent_folder_id or "").strip()
        if not normalized_parent_folder_id:
            return "unknown"

        for policy_folder, provider_folder in self.folder_mapping.items():
            try:
                response = request_with_retries(
                    client=client,
                    provider=self.provider_name,
                    operation="resolve_folder",
                    method="GET",
                    url=self._url(f"mailFolders/{quote(str(provider_folder), safe='')}"),
                    headers=self._headers(),
                    params={"$select": "id"},
                )
            except ProviderRequestError as exc:
                if exc.status_code == 404:
                    continue
                raise
            if str(response.json().get("id") or "") == normalized_parent_folder_id:
                return policy_folder

        return "unknown"
