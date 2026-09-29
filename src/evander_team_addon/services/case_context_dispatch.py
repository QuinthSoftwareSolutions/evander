"""Freeze context during dispatch preparation; rendering remains a pure read."""

import logging

from .case_context_service import CaseContextService
from .case_context_prompt import compact_case_contexts

logger = logging.getLogger(__name__)


def previous_context_snapshot(repository, state, dispatch):
    """Only a completed dispatch known to have used this retained native thread."""
    runtime = state.agent_runtimes[dispatch.agent_id]
    if runtime.context_rotation_pending or runtime.context_clearing_policy.value == "every_wake":
        return None
    thread_id = (
        runtime.work_lineage_thread_ids.get(dispatch.work_lineage_id)
        if runtime.context_clearing_policy.value == "work_lineage"
        else runtime.last_thread_id
    )
    if not thread_id:
        return None
    candidates = {}
    for previous in state.dispatches.values():
        if (
            previous
            and previous.id != dispatch.id
            and previous.agent_id == dispatch.agent_id
            and previous.session_id == dispatch.session_id
            and previous.status.value == "completed"
            and previous.native_thread_id == thread_id
            and previous.completed_at
            and previous.completed_at <= dispatch.created_at
        ):
            candidates[previous.id] = previous.completed_at
    return repository.snapshots.previous_id(candidates, state=state)


def prepare_case_context(repository, state, dispatch):
    from .domain.wake import _dispatch_case_ids

    agent = state.agents[dispatch.agent_id]
    case_ids = sorted(_dispatch_case_ids(state, agent=agent, dispatch=dispatch))
    if not case_ids:
        return
    projects = [state.projects[p] for p in dispatch.project_ids if p in state.projects]
    path = projects[0].directory_path if len({p.directory_path for p in projects}) == 1 else "root"
    try:
        CaseContextService(repository).snapshot_in_state(
            state,
            request_id=f"dispatch:{dispatch.id}",
            directory_path=path,
            actor_id=agent.id,
            dispatch_id=dispatch.id,
            case_ids=case_ids,
            view_id="review-brief" if dispatch.review_request_ids else "task-brief",
            recipient_session_id=agent.session_id,
            baseline_snapshot_id=previous_context_snapshot(repository, state, dispatch),
        )
    except (ValueError, PermissionError, OSError):
        logger.warning("Case context unavailable for dispatch %s", dispatch.id, exc_info=True)


def dispatch_case_contexts(state, dispatch_id, *, snapshots, delta=False):
    from .domain.wake import _dispatch_case_ids

    contexts = [
        {
            "snapshot_id": s.id,
            "text": context["text"],
            "case_ids": s.case_ids,
            "complete": context["complete"],
            "mode": context.get("mode", "baseline"),
            **(
                {
                    "baseline_snapshot_id": s.baseline_snapshot_id,
                    "changed_record_count": context["changed_record_count"],
                }
                if context.get("mode") == "delta"
                else {}
            ),
            "omissions": context["omissions"],
            "content_digest": s.content_digest,
            "read": {"tool": "team_get_case_context_snapshot", "snapshot_id": s.id},
        }
        for s in snapshots
        if s.dispatch_id == dispatch_id
        for context in [s.wake_delta_context if delta and s.wake_delta_context else s.context]
    ]
    dispatch = state.dispatches.get(dispatch_id)
    if (
        not contexts
        and dispatch
        and _dispatch_case_ids(state, agent=state.agents[dispatch.agent_id], dispatch=dispatch)
    ):
        return [
            {
                "complete": False,
                "text": "Case context brief unavailable; use scoped case/task tools.",
                "omissions": [{"kind": "unavailable", "section": "case_context"}],
            }
        ]
    return compact_case_contexts(contexts)
