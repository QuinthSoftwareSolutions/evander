from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ...models import (
    AgentActionRecord,
    TeamState,
)


def _non_empty_action_fields(action: Mapping[str, Any]) -> list[str]:
    ignored_fields = {"agent_id", "dispatch_id", "schema_version", "notes"}
    return sorted(
        key
        for key, value in action.items()
        if key not in ignored_fields and value not in (None, "", [], {})
    )

def _correction_hints(error: str) -> list[str]:
    normalized = error.casefold()
    hints: list[str] = []
    if "cannot combine git_feature_updates and git_release_requests" in normalized:
        hints.append(
            "Submit git_release_requests alone when the intent is a release; it can "
            "auto-enqueue prerequisite feature promotions for accepted deliverables "
            "with review or agent branches."
        )
    if "must not start with 'refs/'" in normalized:
        hints.append("Use bare branch names like agents/... or team/..., not refs/heads/....")
    if "cannot deliver task" in normalized:
        hints.append("Only the task owner can create deliverables for that task.")
    if "artifact_paths must contain at least one path" in normalized:
        hints.append(
            "For Git-only deliverables, omit artifact_paths; otherwise list staged "
            "paths under /artifacts."
        )
    if "supersedes_deliverable_id" in normalized:
        hints.append(
            "When revising a rejected deliverable, set supersedes_deliverable_id to "
            "the rejected deliverable id from the review_rejected inbox payload."
        )
    if (
        "has neither a feature branch nor artifact refs" in normalized
        or "has neither a feature branch, review branch, agent branch" in normalized
    ):
        hints.append(
            "Release requests can auto-promote accepted Git deliverables only when "
            "they have git_refs.review_branch or git_refs.agent_branch; otherwise "
            "publish a review branch or provide artifact refs first."
        )
    if "assignment_id must be a non-empty string" in normalized:
        hints.append(
            "Use the assignment_id shape for ephemeral promotions, or the "
            "deliverable_id shape for persistent deliverables."
        )
    if "already exists" in normalized:
        if "release package" in normalized:
            hints.append(
                "git_release_requests creates new release packages only. For an "
                "existing or blocked package, inspect release_packages[id], resolve "
                "its release_branch/error, and follow its next_action_hint. Only "
                "submit git_release_recoveries when the hint identifies a failed "
                "release promotion and the resolved commit has been pushed to an "
                "agents/... ref; do not reuse the same id in another "
                "git_release_requests item."
            )
        if "review request" in normalized:
            hints.append(
                "For review handoff, do not reuse the existing review request id; "
                "omit id or choose a new id for the intended reviewer."
            )
        hints.append("Do not recreate existing ids; reference existing records or choose a new id.")
    if not hints:
        hints.append(
            "Review the current Team context and retry only the missing corrected action items."
        )
    return hints

def _latest_action_feedback_for_agent(
    state: TeamState,
    agent_id: str,
) -> list[dict[str, Any]]:
    records = [record for record in state.action_records.values() if record.agent_id == agent_id]
    if not records:
        return []
    latest_record = max(records, key=lambda record: (record.created_at, record.id))
    if latest_record.success:
        return []
    if latest_record.dismissed_at is not None:
        return []
    if latest_record.feedback_delivered_at is not None:
        return []
    latest_dispatch_id = latest_record.dispatch_id
    failed_records = [
        record
        for record in records
        if record.dispatch_id == latest_dispatch_id
        and not record.success
        and record.dismissed_at is None
    ]
    return [
        _action_feedback_payload(record)
        for record in sorted(
            failed_records,
            key=lambda record: (record.created_at, record.id),
        )
    ]

def _action_feedback_payload(record: AgentActionRecord) -> dict[str, Any]:
    error = record.error or "action failed validation"
    return {
        "action_record_id": record.id,
        "dispatch_id": record.dispatch_id,
        "created_at": record.created_at.isoformat(),
        "error": error,
        "action_fields": _non_empty_action_fields(record.action),
        "correction_hints": _correction_hints(error),
    }

__all__ = [
    "_non_empty_action_fields",
    "_correction_hints",
    "_latest_action_feedback_for_agent",
    "_action_feedback_payload",
]
