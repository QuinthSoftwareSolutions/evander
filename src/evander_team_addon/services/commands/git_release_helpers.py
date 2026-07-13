from __future__ import annotations


from collections.abc import (
    Mapping,
    Sequence,
)
from typing import Any
from .types import TeamCommandValidationError
from ...models import (
    ReleasePackage,
    TeamState,
    _unique_text_list,
)
from ..domain.common import (
    _optional_text,
    _required_text,
    _string_list,
)

def _team_publish_review_branch_normalized_payload(
    *,
    assignment_id: str | None,
    deliverable_id: str | None,
    source_branch: str | None,
    commit_sha: str | None,
    handled_inbox_item_ids: Sequence[str],
    correlation_id: str | None,
    causation_id: str | None,
) -> dict[str, Any]:
    return {
        "assignment_id": _optional_text(assignment_id),
        "deliverable_id": _optional_text(deliverable_id),
        "source_branch": _optional_text(source_branch),
        "commit_sha": _optional_text(commit_sha),
        "handled_inbox_item_ids": sorted(
            _unique_text_list(handled_inbox_item_ids, "handled_inbox_item_ids")
        ),
        "correlation_id": _optional_text(correlation_id),
        "causation_id": _optional_text(causation_id),
    }

def _team_update_feature_branch_normalized_payload(
    *,
    task_id: str,
    source_review_branches: Sequence[str],
    feature_branch: str | None,
    handled_inbox_item_ids: Sequence[str],
    correlation_id: str | None,
    causation_id: str | None,
) -> dict[str, Any]:
    return {
        "task_id": _required_text(task_id, "task_id"),
        "source_review_branches": _string_list(
            source_review_branches,
            "source_review_branches",
        ),
        "feature_branch": _optional_text(feature_branch),
        "handled_inbox_item_ids": sorted(
            _unique_text_list(handled_inbox_item_ids, "handled_inbox_item_ids")
        ),
        "correlation_id": _optional_text(correlation_id),
        "causation_id": _optional_text(causation_id),
    }

def _team_request_release_normalized_payload(
    *,
    release_id: str | None,
    project_id: str,
    deliverable_ids: Sequence[str],
    release_branch: str | None,
    canonical_branch: str | None,
    summary: str | None,
    verification_waivers: Sequence[Mapping[str, Any]],
    handled_inbox_item_ids: Sequence[str],
    correlation_id: str | None,
    causation_id: str | None,
) -> dict[str, Any]:
    return {
        "id": _optional_text(release_id),
        "project_id": _required_text(project_id, "project_id"),
        "deliverable_ids": _string_list(deliverable_ids, "deliverable_ids"),
        "release_branch": _optional_text(release_branch),
        "canonical_branch": _optional_text(canonical_branch),
        "summary": _optional_text(summary),
        "verification_waivers": [
            {
                "deliverable_id": _required_text(
                    item.get("deliverable_id"),
                    "verification_waivers.deliverable_id",
                ),
                "reason": _required_text(
                    item.get("reason"),
                    "verification_waivers.reason",
                ),
            }
            for item in verification_waivers
        ],
        "handled_inbox_item_ids": sorted(
            _unique_text_list(handled_inbox_item_ids, "handled_inbox_item_ids")
        ),
        "correlation_id": _optional_text(correlation_id),
        "causation_id": _optional_text(causation_id),
    }

def _team_recover_release_normalized_payload(
    *,
    release_package_id: str,
    agent_branch: str,
    commit_sha: str,
    summary: str | None,
    handled_inbox_item_ids: Sequence[str],
    correlation_id: str | None,
    causation_id: str | None,
) -> dict[str, Any]:
    return {
        "release_package_id": _required_text(
            release_package_id,
            "release_package_id",
        ),
        "agent_branch": _required_text(agent_branch, "agent_branch"),
        "commit_sha": _required_text(commit_sha, "commit_sha"),
        "summary": _optional_text(summary),
        "handled_inbox_item_ids": sorted(
            _unique_text_list(handled_inbox_item_ids, "handled_inbox_item_ids")
        ),
        "correlation_id": _optional_text(correlation_id),
        "causation_id": _optional_text(causation_id),
    }


def _team_retry_release_gate_normalized_payload(
    *,
    release_package_id: str,
    gate_kind: str,
    gate_id: str,
    reason: str,
    handled_inbox_item_ids: Sequence[str],
    correlation_id: str | None,
    causation_id: str | None,
) -> dict[str, Any]:
    normalized_kind = _required_text(gate_kind, "gate_kind")
    if normalized_kind not in {"verification", "review"}:
        raise ValueError("gate_kind must be verification or review")
    return {
        "release_package_id": _required_text(
            release_package_id,
            "release_package_id",
        ),
        "gate_kind": normalized_kind,
        "gate_id": _required_text(gate_id, "gate_id"),
        "reason": _required_text(reason, "reason"),
        "handled_inbox_item_ids": sorted(
            _unique_text_list(handled_inbox_item_ids, "handled_inbox_item_ids")
        ),
        "correlation_id": _optional_text(correlation_id),
        "causation_id": _optional_text(causation_id),
    }

def _empty_git_command_result() -> dict[str, Any]:
    return {
        "errors": [],
        "events": [],
        "git_promotion_event_ids": [],
        "release_package_ids": [],
        "release_recovery_event_ids": [],
        "created_deliverable_ids": [],
        "updated_deliverable_ids": [],
    }

def _git_command_validation_error(
    result: Mapping[str, Any],
    *,
    code: str,
) -> TeamCommandValidationError:
    errors = result.get("errors")
    if isinstance(errors, Sequence) and not isinstance(errors, (str, bytes)):
        messages = [str(item) for item in errors if str(item)]
    else:
        messages = [str(errors)] if errors else []
    message = "; ".join(messages) or "Git intent validation failed"
    return TeamCommandValidationError(
        code=code,
        message=message,
        correction_context={"errors": messages},
    )

def _release_command_next_action_hints(
    state: TeamState,
    release: ReleasePackage,
    *,
    promotion_event_ids: Sequence[str],
) -> list[str]:
    if not promotion_event_ids:
        return [
            "Release package recorded without pending Git promotion effects; inspect artifact_refs and package state before publishing externally."
        ]
    promotion_kinds = {
        state.git_promotions[promotion_id].promotion_kind
        for promotion_id in promotion_event_ids
        if promotion_id in state.git_promotions
    }
    if "feature" in promotion_kinds:
        return [
            "Prerequisite feature promotion intent recorded; release promotion will be queued after the async feature promotion succeeds."
        ]
    if "release" in promotion_kinds:
        return [
            "Release promotion intent recorded; wait for async Git promotion result before expecting canonical publication."
        ]
    return [
        "Git promotion intent recorded; wait for async effect completion before taking the next release step."
    ]

__all__ = [
    '_team_publish_review_branch_normalized_payload',
    '_team_update_feature_branch_normalized_payload',
    '_team_request_release_normalized_payload',
    '_team_recover_release_normalized_payload',
    '_team_retry_release_gate_normalized_payload',
    '_empty_git_command_result',
    '_git_command_validation_error',
    '_release_command_next_action_hints',
]
