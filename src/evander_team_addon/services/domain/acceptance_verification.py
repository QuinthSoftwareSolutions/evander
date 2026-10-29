from __future__ import annotations

from typing import Any

from ...models import Deliverable, DeliverableVerification, TeamState

# Kept for persisted outbox records and old workflow packs. New submissions do
# not create this event; the release candidate is the sole server-owned check.
DELIVERABLE_ACCEPTANCE_VERIFICATION_REQUESTED_EVENT_TYPE = (
    "team.deliverable.acceptance_verification_requested"
)
ACCEPTANCE_VERIFICATION_RETIRED_WARNING = (
    "The deliverable acceptance-verification gate has been retired. "
    "Agent-reported verification remains advisory; configured release-candidate "
    "verification is authoritative."
)
LEGACY_VERIFICATION_WAIVERS_IGNORED_WARNING = (
    "Legacy verification_waivers input was ignored because verification waivers "
    "are retired; release-candidate verification is authoritative."
)


def apply_formal_review_acceptance_policy(
    state: TeamState,
    *,
    deliverable: Deliverable,
    result: dict[str, Any],
) -> DeliverableVerification | None:
    """Retain the policy marker without creating a hidden verification run."""

    if deliverable.acceptance_policy_version >= 2:
        return deliverable.verification
    deliverable.acceptance_policy_version = 2
    return deliverable.verification


__all__ = [
    "DELIVERABLE_ACCEPTANCE_VERIFICATION_REQUESTED_EVENT_TYPE",
    "ACCEPTANCE_VERIFICATION_RETIRED_WARNING",
    "LEGACY_VERIFICATION_WAIVERS_IGNORED_WARNING",
    "apply_formal_review_acceptance_policy",
]
