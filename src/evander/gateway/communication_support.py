"""Shared communication tool contracts and signed mailbox cursors."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Any

from pydantic import Field as PydanticField

from evander.email.providers import EmailMessageSearchQuery

TeamsMentionRefsArgument = Annotated[
    list[str] | None,
    PydanticField(
        description=(
            "Optional Teams user refs to mention in a channel post, chat post, or channel thread reply. "
            "Use user:<graph-user-id-or-upn> or a bare Graph user ID, UPN, or mail address. "
            "Plain @name text in message does not notify users."
        )
    ),
]


@dataclass(frozen=True)
class CommunicationToolHelpers:
    audit_success: Callable[..., None]
    audit_failure: Callable[..., None]
    artifact_payloads: Callable[..., Any]
    artifact_audit_summaries: Callable[..., Any]
    communication_target_audit_fields: Callable[..., Any]
    record_email_draft_snapshot: Callable[..., Any]
    send_email_draft: Callable[..., Any]
    audit_inline_media_operation: Callable[..., Any]
    resolve_inline_media: Callable[..., Any]
    download_inline_media_bytes: Callable[..., Any]
    provider_download_attachment: Callable[..., Any]
    stage_inline_media_artifact: Callable[..., Any]
    inline_media_ref_audit_payload: Callable[..., Any]
    normalize_inline_media_source_kind: Callable[..., Any]
    resolve_communication_attachments: Callable[..., Any]
    stage_communication_artifact: Callable[..., Any]
    admin_service: Callable[..., Any]
    artifact_to_model: Callable[..., Any]
    communication_artifact_store: Callable[..., Any]
    safe_artifact_id: Callable[..., Any]
    normalize_workspace_source_path: Callable[..., Any]
    read_agent_workspace_file: Callable[..., Any]
    teams_artifacts_with_public_download_links: Callable[..., Any]
    draft_recipients_from_payload: Callable[..., Any]
    filter_discord_commands_for_session: Callable[..., Any]
    filter_teams_messages_for_session: Callable[..., Any]
    teams_cursor_for_target: Callable[..., Any]
    transport_session_map: dict[str, str] | None = None


class _EmailListCursorCodec:
    _ttl_seconds = 15 * 60

    def __init__(self) -> None:
        self._secret = secrets.token_bytes(32)

    @staticmethod
    def _query_digest(query: EmailMessageSearchQuery) -> str:
        serialized = json.dumps(
            query.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
        )
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    def issue(
        self,
        *,
        session_id: str,
        mailbox_id: str,
        folder: str,
        query: EmailMessageSearchQuery,
        provider_cursor: str,
    ) -> str:
        payload = {
            "version": 1,
            "expires_at": int(time.time()) + self._ttl_seconds,
            "session_id": session_id,
            "mailbox_id": mailbox_id,
            "folder": folder,
            "query_digest": self._query_digest(query),
            "provider_cursor": provider_cursor,
        }
        encoded = (
            base64.urlsafe_b64encode(
                json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
            )
            .decode("ascii")
            .rstrip("=")
        )
        signature = hmac.new(self._secret, encoded.encode("ascii"), hashlib.sha256).hexdigest()
        return f"{encoded}.{signature}"

    def resolve(
        self,
        cursor: str,
        *,
        session_id: str,
        mailbox_id: str,
        folder: str,
        query: EmailMessageSearchQuery,
    ) -> str:
        try:
            encoded, signature = cursor.split(".", 1)
            expected_signature = hmac.new(
                self._secret, encoded.encode("ascii"), hashlib.sha256
            ).hexdigest()
            if not hmac.compare_digest(signature, expected_signature):
                raise ValueError("invalid email page cursor")
            padding = "=" * (-len(encoded) % 4)
            payload = json.loads(base64.urlsafe_b64decode(f"{encoded}{padding}").decode("utf-8"))
        except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError("invalid email page cursor") from exc
        if (
            payload.get("version") != 1
            or not isinstance(payload.get("provider_cursor"), str)
            or int(payload.get("expires_at", 0)) < int(time.time())
            or payload.get("session_id") != session_id
            or payload.get("mailbox_id") != mailbox_id
            or payload.get("folder") != folder
            or payload.get("query_digest") != self._query_digest(query)
        ):
            raise ValueError("invalid or expired email page cursor")
        return str(payload["provider_cursor"])
