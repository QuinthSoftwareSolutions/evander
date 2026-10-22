"""Artifact-only handoffs through the existing deliverable submission command."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any
from uuid import uuid4

from evander.models import utc_now

from ...models import (
    AgentInboxItem,
    AgentStatus,
    AgentTask,
    PersistentAgent,
    TeamInboxItemType,
    TeamInboxPriority,
    TeamInboxItemState,
    TeamState,
)
from ..domain.common import _NON_TERMINAL_TASK_STATES, _optional_text
from ..domain.task_supervision import task_acceptance_recipient_agent_id
from ..domain.wake import _append_agent_wake_requested_event_for_inbox_item
from ..domain.work_due import _mark_agent_work_due
from .deliverable_helpers import _publish_command_deliverable_artifacts
from .types import (
    TeamCommandApplyResult,
    TeamCommandValidationError,
    TeamDeliverableArtifactPublisher,
)
from .validators import _required_command_text


def publish_task_evidence(
    state: TeamState,
    *,
    agent: PersistentAgent,
    task: AgentTask,
    deliverable: Mapping[str, Any],
    request_id: str,
    dispatch_id: str | None,
    artifact_publisher: TeamDeliverableArtifactPublisher | None,
) -> TeamCommandApplyResult:
    """Publish diagnostics without entering any product lifecycle boundary."""

    summary = _required_command_text(
        deliverable.get("summary"), field_name="summary", code="empty_summary"
    )
    verification = dict(deliverable.get("verification") or {})
    if task.state not in _NON_TERMINAL_TASK_STATES:
        raise TeamCommandValidationError(
            code="evidence_task_terminal",
            message=f"task {task.id} is {task.state.value}; evidence requires an open task",
        )
    if verification.get("status") not in {"failed", "partial", "not_run", "unknown"}:
        raise TeamCommandValidationError(
            code="evidence_verification_required",
            message="evidence_only requires a failed or incomplete verification status",
        )
    product_fields = [
        field
        for field in (
            "supersedes_deliverable_id",
            "completion",
            "outcomes",
            "source_deliverable_ids",
        )
        if deliverable.get(field)
    ]
    if product_fields:
        raise TeamCommandValidationError(
            code="evidence_product_fields_forbidden",
            message="evidence_only cannot record completion, outcomes, or product replacement",
            correction_context={"unsupported_fields": product_fields},
        )
    if not deliverable.get("artifact_paths"):
        raise TeamCommandValidationError(
            code="evidence_artifact_paths_required",
            message="evidence_only requires retained files in artifact_paths",
        )
    git_refs = dict(deliverable.get("git_refs") or {})
    if (
        deliverable.get("branch_context_id")
        and git_refs.get("branch_context_id")
        and deliverable["branch_context_id"] != git_refs["branch_context_id"]
    ):
        raise TeamCommandValidationError(
            code="evidence_git_context_forbidden",
            message="evidence branch_context_id fields must agree",
        )
    context_id = _optional_text(deliverable.get("branch_context_id")) or _optional_text(
        git_refs.get("branch_context_id")
    )
    if context_id is not None:
        context = state.git_work_contexts.get(context_id)
        if context is None or context.agent_id != agent.id or context.task_id != task.id:
            raise TeamCommandValidationError(
                code="evidence_git_context_forbidden",
                message="evidence Git context must belong to the caller and task",
            )
        if git_refs.get("agent_branch") not in {None, context.agent_branch}:
            raise TeamCommandValidationError(
                code="agent_branch_mismatch",
                message="evidence agent_branch does not match its retained task context",
            )
        git_refs["branch_context_id"] = context.id
        git_refs["agent_branch"] = context.agent_branch

    # Stable across dispatch retries, including recovery after archive publication
    # but before the Team transaction commits. Evidence is not a Git candidate.
    digest = hashlib.sha256(f"{agent.id}\0{task.id}\0{request_id}".encode()).hexdigest()
    payload_digest = hashlib.sha256(
        json.dumps(dict(deliverable), sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    evidence_id = f"evidence-{digest[:32]}"
    metadata = {
        "task_id": task.id,
        "summary": summary,
        "verification": verification,
        "reported_git_refs": git_refs,
        "git_provenance": "agent_reported",
        "record_kind": "evidence",
        "producer_agent_id": agent.id,
        "request_id": request_id,
        "request_payload_digest": payload_digest,
    }
    records = _publish_command_deliverable_artifacts(
        state,
        agent=agent,
        task=task,
        deliverable_id=evidence_id,
        dispatch_id=f"request-{digest[:24]}-{payload_digest[:24]}",
        artifact_paths=deliverable["artifact_paths"],
        artifact_publisher=artifact_publisher,
        evidence_metadata=metadata,
    )
    refs = [record["artifact_ref"] for record in records]
    recipients: set[str] = set()
    superior = task_acceptance_recipient_agent_id(state, task=task, producer_agent_id=agent.id)
    if superior is not None:
        recipients.add(superior)
    dispatch = state.dispatches.get(dispatch_id or "")
    if dispatch is not None:
        for inbox_id in dispatch.inbox_item_ids:
            item = state.inbox.get(inbox_id)
            if (
                item is not None
                and item.task_id == task.id
                and item.recipient_agent_id == agent.id
                and item.response_requested
                and item.state == TeamInboxItemState.PENDING
                and item.item_type
                in {
                    TeamInboxItemType.QUESTION,
                    TeamInboxItemType.STATUS_REQUEST,
                    TeamInboxItemType.OPERATOR_DIRECTIVE,
                }
                and item.sender_agent_id is not None
            ):
                recipients.add(item.sender_agent_id)
    created_ids: list[str] = []
    events: dict[str, Any] = {"events": []}
    now = utc_now()
    for recipient_id in sorted(recipients - {agent.id}):
        recipient = state.agents.get(recipient_id)
        if (
            recipient is None
            or recipient.team_id != agent.team_id
            or recipient.status != AgentStatus.ACTIVE
        ):
            continue
        item = AgentInboxItem(
            id=f"inbox-{uuid4().hex}",
            recipient_agent_id=recipient.id,
            sender_agent_id=agent.id,
            task_id=task.id,
            item_type=TeamInboxItemType.PROGRESS_REPORT,
            response_requested=False,
            priority=TeamInboxPriority.NORMAL,
            payload={
                "kind": "task_evidence_published",
                "evidence_only": True,
                "evidence_id": evidence_id,
                "task_id": task.id,
                "summary": summary,
                "verification": verification,
                "artifact_refs": refs,
                "published_evidence_artifacts": records,
            },
            created_at=now,
            updated_at=now,
        )
        state.inbox[item.id] = item
        created_ids.append(item.id)
        _mark_agent_work_due(state, agent_id=recipient.id, now=now)
        _append_agent_wake_requested_event_for_inbox_item(state, item=item, result=events, now=now)
    return TeamCommandApplyResult(
        response_payload={
            "evidence_only": True,
            "evidence_id": evidence_id,
            "deliverable_id": None,
            "created_deliverable_ids": [],
            "created_inbox_item_ids": created_ids,
            "published_artifact_refs": refs,
            "published_evidence_artifacts": records,
            "task_id": task.id,
            "task_state": task.state.value,
            "verification": verification,
            "events": events["events"],
            "intent_recorded": True,
            "async_effect_pending": bool(created_ids),
            "progress_made": True,
        },
        created_domain_ids=created_ids,
        progress_classification="evidence_publication",
    )
