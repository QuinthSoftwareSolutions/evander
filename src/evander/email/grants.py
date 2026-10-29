from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from pydantic import ValidationError

from evander.config import MailboxConfig
from evander.models import EmailMailboxAccess, EmailMailboxGrant


def normalize_email_mailbox_grants(value: object) -> list[EmailMailboxGrant] | None:
    if value is None:
        return None
    if not isinstance(value, list):
        raise ValueError("email_mailbox_grants must be a JSON array or null")
    try:
        grants = [EmailMailboxGrant.model_validate(item) for item in value]
    except ValidationError as exc:
        raise ValueError(f"invalid email_mailbox_grants: {exc}") from exc
    mailbox_ids = [grant.mailbox_id for grant in grants]
    duplicates = sorted(
        mailbox_id for mailbox_id, count in Counter(mailbox_ids).items() if count > 1
    )
    if duplicates:
        raise ValueError(
            "email_mailbox_grants contains duplicate mailbox IDs: " + ", ".join(duplicates)
        )
    return grants


def validate_email_mailbox_grants(
    grants: list[EmailMailboxGrant] | None,
    *,
    mailboxes: Sequence[MailboxConfig],
    allowed_mailbox_ids: Iterable[str],
    folder_allowlist_by_mailbox: Mapping[str, Sequence[str]],
    email_send_enabled: bool,
    directory_path: str,
    error_context: str = "",
) -> list[EmailMailboxGrant] | None:
    """Resolve defaults and validate grants against one effective directory policy."""

    if grants is None:
        return None
    mailbox_by_id = {mailbox.mailbox_id: mailbox for mailbox in mailboxes}
    allowed_ids = set(allowed_mailbox_ids)
    validated: list[EmailMailboxGrant] = []
    for grant in grants:
        mailbox = mailbox_by_id.get(grant.mailbox_id)
        if mailbox is None or grant.mailbox_id not in allowed_ids:
            raise ValueError(
                f"{error_context}email_mailbox_grants contains unavailable mailbox "
                f"{grant.mailbox_id!r} for directory {directory_path!r}"
            )
        folders = (
            list(grant.folders)
            if "folders" in grant.model_fields_set
            else list(mailbox.folder_allowlist_defaults)
        )
        allowed_folders = set(
            folder_allowlist_by_mailbox.get(grant.mailbox_id, mailbox.folder_allowlist_defaults)
        )
        unavailable_folders = sorted(set(folders) - allowed_folders)
        if unavailable_folders:
            raise ValueError(
                f"{error_context}email_mailbox_grants contains unavailable folders for mailbox "
                f"{grant.mailbox_id!r}: {', '.join(unavailable_folders)}"
            )
        if grant.access == EmailMailboxAccess.SEND and not email_send_enabled:
            raise ValueError(
                f"{error_context}cannot grant send access while email send is disabled "
                f"for directory {directory_path!r}"
            )
        validated.append(grant.model_copy(update={"folders": folders}))
    return validated


def email_mailbox_grant_policy_update(
    grants: list[EmailMailboxGrant] | None,
) -> dict[str, Any]:
    """Build the canonical session fields derived from mailbox grants."""

    update: dict[str, Any] = {"email_mailbox_grants": grants}
    if grants is None:
        return update
    update["email_mailbox_allowlist"] = [grant.mailbox_id for grant in grants]
    update["email_folder_allowlist_by_mailbox"] = {
        grant.mailbox_id: list(grant.folders) for grant in grants
    }
    update["email_folder_allowlist"] = sorted(
        {folder for grant in grants for folder in grant.folders}
    )
    return update
