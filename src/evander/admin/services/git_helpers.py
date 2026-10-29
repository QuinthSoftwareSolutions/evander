from __future__ import annotations

from pathlib import Path

from evander.pr_feedback.store import PrFeedbackStore
from evander.pr_tracking import PrMapStore


def prune_pr_map(path: Path, *, session_ids: set[str]) -> tuple[int, set[tuple[str, str, str]]]:
    """Remove PR routing entries owned by deleted sessions."""

    return PrMapStore(path).prune_session_bindings(session_ids=session_ids)


def prune_pr_feedback_store(
    path: Path,
    *,
    streams: set[tuple[str, str, str]],
) -> tuple[int, int]:
    """Remove feedback cursors and records for PR streams no longer routed."""

    if not streams or not path.exists():
        return 0, 0
    return PrFeedbackStore(path).prune_streams(streams)


_prune_pr_map = prune_pr_map
_prune_pr_feedback_store = prune_pr_feedback_store
