"""Creator requirements for host-owned pull request publication."""

from collections.abc import Mapping
from enum import StrEnum


class PrPublicationPolicy(StrEnum):
    ALLOW_BOT_FALLBACK = "allow_bot_fallback"
    REQUIRE_PERSONAL = "require_personal"


class PrPublicationPolicyError(ValueError):
    def __init__(self, reason: str, *, pr_id: object = None) -> None:
        self.reason = reason
        self.pr_id = str(pr_id) if pr_id is not None else None
        suffix = f" (PR {self.pr_id})" if self.pr_id else ""
        super().__init__(f"Personal PR publication required: {reason}{suffix}")


def require_personal_connection(context: Mapping, authorization: str | None) -> None:
    if not context:
        raise PrPublicationPolicyError("publication_context_missing")
    if not context.get("identity_id"):
        raise PrPublicationPolicyError(
            context.get("fallback_reason") or "work_item_assignee_unavailable"
        )
    if not authorization:
        raise PrPublicationPolicyError(
            context.get("fallback_reason") or "personal_connection_unavailable"
        )


def require_pr_creator(pr: Mapping, identity_id: str, *, existing: bool) -> None:
    creator = pr.get("createdBy")
    actual_id = creator.get("id") if isinstance(creator, Mapping) else None
    if str(actual_id or "").lower() != identity_id.lower():
        raise PrPublicationPolicyError(
            "existing_pr_creator_mismatch" if existing else "created_pr_creator_mismatch",
            pr_id=pr.get("pullRequestId"),
        )
