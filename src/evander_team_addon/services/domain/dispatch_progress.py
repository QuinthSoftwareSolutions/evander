from __future__ import annotations

from ...models import (
    TeamDispatchActivity,
    TeamDispatchActivityStatus,
)

from .common import _optional_text


_DISPATCH_NON_PROGRESS_CLASSES = frozenset({"", "none", "context", "context_only"})

_DISPATCH_CONTEXT_SUPPRESSED_CLASSES = frozenset(
    {"none", "progress_no_context", "progress_report", "status_report"}
)

def _normalized_progress_classification(activity: TeamDispatchActivity) -> str:
    return (_optional_text(activity.progress_classification) or "none").casefold()

def _dispatch_activity_counts_as_progress(activity: TeamDispatchActivity) -> bool:
    if activity.status != TeamDispatchActivityStatus.SUCCESS:
        return False
    return _normalized_progress_classification(activity) not in _DISPATCH_NON_PROGRESS_CLASSES

def _dispatch_activity_counts_for_context_rotation(
    activity: TeamDispatchActivity,
) -> bool:
    if activity.status != TeamDispatchActivityStatus.SUCCESS:
        return False
    progress_classification = _normalized_progress_classification(activity)
    return progress_classification not in _DISPATCH_CONTEXT_SUPPRESSED_CLASSES

__all__ = [
    "_DISPATCH_NON_PROGRESS_CLASSES",
    "_DISPATCH_CONTEXT_SUPPRESSED_CLASSES",
    "_normalized_progress_classification",
    "_dispatch_activity_counts_as_progress",
    "_dispatch_activity_counts_for_context_rotation",
]
