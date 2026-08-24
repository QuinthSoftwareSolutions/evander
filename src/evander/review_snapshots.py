"""Stable managed-Git ref naming for Team review snapshots."""

from __future__ import annotations

import base64
import hashlib


def team_review_snapshot_ref(review_id: str) -> str:
    """Return the deterministic, ref-safe retained name for one Team review.

    Team review IDs are operator supplied in a few supported paths, so they
    must not become raw Git ref components.  Keep short ordinary IDs readable
    enough for diagnostics while adding a digest to preserve uniqueness when
    an ID is truncated.
    """

    normalized = str(review_id or "").strip()
    if not normalized:
        raise ValueError("review_id is required")
    encoded = base64.urlsafe_b64encode(normalized.encode("utf-8")).decode("ascii").rstrip("=")
    if len(encoded) > 120:
        encoded = f"{encoded[:80]}-{hashlib.sha256(normalized.encode('utf-8')).hexdigest()[:32]}"
    return f"refs/evander/team-reviews/{encoded}"


def team_deliverable_snapshot_binding_id(deliverable_id: str) -> str:
    """Return the retained-snapshot binding used for one submitted deliverable."""

    normalized = str(deliverable_id or "").strip()
    if not normalized:
        raise ValueError("deliverable_id is required")
    return f"deliverable:{normalized}"


def team_deliverable_snapshot_ref(deliverable_id: str) -> str:
    """Return the immutable ref created as soon as a Git deliverable is submitted."""

    return team_review_snapshot_ref(team_deliverable_snapshot_binding_id(deliverable_id))


__all__ = [
    "team_deliverable_snapshot_binding_id",
    "team_deliverable_snapshot_ref",
    "team_review_snapshot_ref",
]
