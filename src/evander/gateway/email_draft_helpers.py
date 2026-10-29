"""Email draft snapshot helpers for communication MCP tools."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from typing import Any

from evander.email.providers import EmailDraft
from evander.models import DraftMessage
from evander.policy import PolicyError
from evander.runtime import RuntimeContext


def draft_recipients_from_payload(payload: dict[str, Any]) -> list[str]:
    recipients: list[str] = []
    for key in ("recipients", "to", "cc", "bcc"):
        value = payload.get(key)
        if value is None:
            continue
        if isinstance(value, str):
            recipients.append(value)
        elif isinstance(value, list):
            recipients.extend([item for item in value if isinstance(item, str)])
    return recipients


def _normalized_email_recipients(recipients: list[str]) -> list[str]:
    return sorted(address.strip().casefold() for address in recipients if address.strip())


@dataclass(frozen=True)
class EmailDraftHelpers:
    context: RuntimeContext

    def record_email_draft_snapshot(
        self,
        *,
        session_id: str,
        draft: EmailDraft,
        body: str,
        artifact_ids: list[str] | None = None,
    ) -> None:
        snapshot = DraftMessage(
            draft_id=draft.draft_id,
            mailbox_id=draft.mailbox_id,
            provider=draft.provider,
            provider_draft_id=draft.draft_id,
            thread_or_message_id=draft.thread_or_message_id,
            subject=draft.subject,
            body=body,
            body_digest=sha256(body.encode("utf-8")).hexdigest(),
            recipients=list(draft.recipients),
            attachment_ids=list(artifact_ids or []),
            metadata={**draft.metadata, "session_id": session_id},
            created_at=draft.created_at,
        )
        self.context.drafts.put(snapshot.draft_id, snapshot)

    def load_email_draft_snapshot(
        self, *, session_id: str, mailbox_id: str, draft_id: str
    ) -> DraftMessage:
        normalized_draft_id = str(draft_id or "").strip()
        if not normalized_draft_id:
            raise PolicyError("email send requires draft_id")
        snapshot = self.context.drafts.get(normalized_draft_id)
        if snapshot is None:
            raise PolicyError(
                "Email send requires a host-recorded draft snapshot; create the draft first."
            )
        if snapshot.mailbox_id != mailbox_id:
            raise PolicyError(
                f"Email draft {normalized_draft_id!r} belongs to mailbox "
                f"{snapshot.mailbox_id!r}, not {mailbox_id!r}"
            )
        owner_session_id = str(snapshot.metadata.get("session_id") or "").strip()
        if not owner_session_id:
            raise PolicyError(
                "Email draft snapshot does not include session ownership; recreate the draft before sending."
            )
        if owner_session_id != session_id:
            raise PolicyError(
                f"Email draft {normalized_draft_id!r} belongs to session {owner_session_id!r}"
            )
        return snapshot

    def validate_email_send_snapshot(
        self,
        *,
        snapshot: DraftMessage,
        recipients: list[str],
        subject: str | None,
        artifact_ids: list[str] | None,
    ) -> None:
        if _normalized_email_recipients(recipients) != _normalized_email_recipients(
            snapshot.recipients
        ):
            raise PolicyError("Email send recipients must match the recorded draft recipients.")
        if subject is not None and subject != snapshot.subject:
            raise PolicyError("Email send subject must match the recorded draft subject.")
        if artifact_ids is None:
            if snapshot.attachment_ids:
                raise PolicyError(
                    "Email draft has attachments; use email_send_with_attachments with "
                    "matching artifact_ids."
                )
            return
        if sorted(artifact_ids) != sorted(snapshot.attachment_ids):
            raise PolicyError("Email send attachments must match the recorded draft attachments.")

    def mark_email_draft_sent(
        self, *, draft_id: str, sent_at: datetime, status: str, provider_message_id: str | None
    ) -> None:
        def updater(items: dict[str, DraftMessage]) -> None:
            snapshot = items.get(draft_id)
            if snapshot is None:
                return
            items[draft_id] = snapshot.model_copy(
                update={
                    "sent_at": sent_at,
                    "send_status": status,
                    "provider_message_id": provider_message_id,
                    "metadata": {
                        **snapshot.metadata,
                        "send_status": status,
                        "provider_message_id": provider_message_id,
                    },
                }
            )

        self.context.drafts.update_all(updater)

    def send_email_draft(
        self,
        *,
        session_id: str,
        session: Any,
        mailbox_id: str,
        draft_id: str,
        recipients: list[str],
        subject: str | None = None,
        artifact_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        self.context.policy.require_email_send_enabled(session, mailbox_id)
        self.context.policy.require_draft_recipients(session, recipients)
        snapshot = self.load_email_draft_snapshot(
            session_id=session_id, mailbox_id=mailbox_id, draft_id=draft_id
        )
        self.validate_email_send_snapshot(
            snapshot=snapshot,
            recipients=recipients,
            subject=subject,
            artifact_ids=artifact_ids,
        )
        matched_target_ids = self.context.policy.require_email_send_targets(
            session,
            mailbox_id,
            list(snapshot.recipients),
        )
        result = self.context.email_service_for_session(session_id).send_draft(
            mailbox_id=mailbox_id,
            draft_id=draft_id,
        )
        self.mark_email_draft_sent(
            draft_id=draft_id,
            sent_at=result.sent_at,
            status=result.status,
            provider_message_id=result.message_id,
        )
        output = result.model_dump(mode="json")
        output["recipients"] = list(snapshot.recipients)
        if session.communication_target_ids is not None:
            output["communication_target_ids"] = matched_target_ids
        if artifact_ids is not None:
            output["artifact_ids"] = list(artifact_ids)
        return output
