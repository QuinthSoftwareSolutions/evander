"""Session-scoped mailbox metadata, without provider calls or credential exposure."""

from __future__ import annotations

from typing import Any

from evander.config import GatewayConfig
from evander.models import EmailMailboxAccess, SessionManifest
from evander.policy import PolicyEngine, PolicyError


def discover_email_mailboxes(
    session: SessionManifest, config: GatewayConfig, policy: PolicyEngine
) -> list[dict[str, Any]]:
    """Project configured mailboxes through the same grants used by email operations."""

    mailboxes: list[dict[str, Any]] = []
    for mailbox in sorted(config.email_mailboxes, key=lambda item: item.mailbox_id):
        try:
            grant = policy.require_email_mailbox_access(
                session, mailbox.mailbox_id, EmailMailboxAccess.READ
            )
        except PolicyError:
            continue

        if grant is not None:
            access = grant.access
            folders = sorted(set(grant.folders))
        else:
            # Legacy manifests have no access rank. Match their existing draft/send
            # checks and folder fallback, without inventing provider defaults.
            access = EmailMailboxAccess.DRAFT
            try:
                policy.require_email_send_enabled(session, mailbox.mailbox_id)
            except PolicyError:
                pass
            else:
                access = EmailMailboxAccess.SEND
            folder_map = (
                session.email_folder_allowlist_by_mailbox
                or policy.effective_policy_for_session(session).email_folder_allowlist_by_mailbox
            )
            folders = sorted(
                set(folder_map.get(mailbox.mailbox_id, [])) | set(session.email_folder_allowlist)
            )

        # Deliberately project public fields; never serialize MailboxConfig, which
        # also contains host-only credential references and provider folder IDs.
        mailboxes.append(
            {
                "mailbox_id": mailbox.mailbox_id,
                "provider": mailbox.provider,
                "account_identifier": mailbox.account_identifier,
                "access": access.value,
                "folders": folders,
            }
        )
    return mailboxes
