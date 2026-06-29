from __future__ import annotations


from collections.abc import (
    Mapping,
    Sequence,
)
from typing import Any
from .types import (
    TeamCommandApplyResult,
    TeamCommandContext,
)
from ...models import TeamState
from ..domain.common import _optional_text
from ...sqlite_store import TeamIdempotencyConflictError

def _command_success_response(
    *,
    state: TeamState,
    context: TeamCommandContext,
    activity_id: str,
    handled_inbox_item_ids: Sequence[str],
    apply_result: TeamCommandApplyResult,
) -> dict[str, Any]:
    response = dict(apply_result.response_payload)
    progress_made = bool(response.get("progress_made", False))
    response.update(
        {
            "success": True,
            "applied": True,
            "progress_made": progress_made,
            "revision": state.revision,
            "activity_id": activity_id,
            "tool_name": context.tool_name,
            "action_record_id": _optional_text(response.get("action_record_id")),
            "handled_inbox_item_ids": list(handled_inbox_item_ids),
            "errors": [],
        }
    )
    return response

def _command_idempotency_conflict_response(
    *,
    state: TeamState,
    conflict: TeamIdempotencyConflictError,
) -> dict[str, Any]:
    return {
        "success": False,
        "applied": False,
        "progress_made": False,
        "revision": state.revision,
        "activity_id": None,
        "action_record_id": None,
        "errors": [
            {
                "code": "idempotency_conflict",
                "message": str(conflict),
                "existing_digest": conflict.existing_digest,
                "attempted_digest": conflict.attempted_digest,
            }
        ],
    }

def _command_correction_context_summary(
    correction_context: Mapping[str, Any],
) -> str | None:
    if not correction_context:
        return None
    summary = "; ".join(
        f"{key}={value}" for key, value in sorted(correction_context.items()) if value is not None
    )
    return summary[:240] if summary else None

__all__ = [
    '_command_success_response',
    '_command_idempotency_conflict_response',
    '_command_correction_context_summary',
]
