"""Complete reviewed recoveries without rewriting submitted or accepted history."""

from __future__ import annotations

from ...models import TaskCancellation, TeamRecordState, TeamTaskState, utc_now
from .git_recovery import digest, recovery_inputs, preserve_work
from .blockers import resolve_blocker, task_cancellation_blocking_handoff_ids
from .task_assessment import assess_task
from .task_replacement import (
    plan_task_dependency_rebinding,
    apply_task_dependency_rebinding,
    invalidate_rebound_integration_git_contexts,
)
from .git_work_contexts import finalize_active_git_work_contexts_for_task
from .work_due import _mark_agent_work_due
from .wake import _append_agent_wake_requested_event


def affected_consumers(state, task_id):
    return sorted(
        task.id
        for task in state.tasks.values()
        if task.state not in {"accepted", "finished", "cancelled"}
        and (
            task_id in task.dependency_task_ids
            or task.deliverable_contract
            and task_id in task.deliverable_contract.required_contribution_task_ids
        )
    )


def reconcile_git_recovery_successors(state, *, result, now=None):
    now = now or utc_now()
    for incident in list(state.git_recovery_incidents.values()):
        if incident.state not in {"awaiting_review", "waiting"}:
            continue
        successor = (
            state.tasks.get(incident.recovery_task_ids[-1]) if incident.recovery_task_ids else None
        )
        if successor is None:
            continue
        if successor.state in {"cancelled", "rejected"}:
            incident.state = "diagnosed"
            incident.waiting_reason = (
                "Recovery work ended without an accepted candidate; its generation remains charged."
            )
            continue
        if assess_task(state, successor.id).resolution_status != "resolved":
            # One accepted product is insufficient while other products, reviews,
            # descendants, blockers, or provenance obligations remain unresolved.
            continue
        accepted = [
            row
            for row in state.deliverables.values()
            if row.task_id == successor.id
            and row.state == "accepted"
            and not row.superseded_by_deliverable_id
        ]
        if not accepted or not all(row.git_refs for row in accepted):
            continue
        if incident.action_id == "resolve_merge":
            # The normal preparer adopts the accepted merge. Retire earlier
            # rejected generations so they cannot keep the parent unresolved.
            try:
                simulated = state.model_copy(deep=True)
                _retire_rejected_generations(
                    simulated,
                    incident=simulated.git_recovery_incidents[incident.id],
                    successor=simulated.tasks[successor.id],
                    result={"events": []},
                    now=now,
                )
                _retire_rejected_generations(
                    state, incident=incident, successor=successor, result=result, now=now
                )
                incident.state = "awaiting_review"
                incident.waiting_reason = None
            except ValueError as exc:
                incident.state = "waiting"
                incident.waiting_reason = str(exc)
            continue
        if incident.stage == "publication":
            _complete_publication(
                state, incident=incident, accepted=accepted, result=result, now=now
            )
            continue
        original = state.tasks.get(incident.task_id)
        if original is None:
            continue
        try:
            # Validate every graph/retirement step on a private state first. A
            # deferred consumer must never leave a partially rebound live graph.
            simulated = state.model_copy(deep=True)
            _complete_successor(
                simulated,
                incident=simulated.git_recovery_incidents[incident.id],
                original=simulated.tasks[original.id],
                successor=simulated.tasks[successor.id],
                deliverables=[simulated.deliverables[row.id] for row in accepted],
                result={"events": []},
                now=now,
            )
            _complete_successor(
                state,
                incident=incident,
                original=original,
                successor=successor,
                deliverables=accepted,
                result=result,
                now=now,
            )
        except ValueError as exc:
            # Preserve the accepted result while its remaining consumer/policy
            # constraint is resolved. Acceptance itself must not be rolled back.
            incident.state = "waiting"
            incident.waiting_reason = str(exc)
            incident.updated_at = now


def _complete_publication(state, *, incident, accepted, result, now):
    from ..git_release_service import _resolve_superseded_release_failure_inbox_items
    from .project_status import _recalculate_project_status

    original = state.release_packages.get(incident.evidence.get("release_package_id"))
    if original is None:
        return
    accepted_ids = {row.id for row in accepted}
    releases = sorted(
        (
            release
            for release in state.release_packages.values()
            if release.project_id == incident.project_id
            and release.canonical_branch == original.canonical_branch
            and accepted_ids.issubset(release.deliverable_ids)
            and release.state != TeamRecordState.CANCELLED
        ),
        key=lambda release: (release.created_at, release.id),
    )
    incident.evidence["successor_release_ids"] = [release.id for release in releases]
    published = next(
        (
            release
            for release in releases
            if release.state == TeamRecordState.ACCEPTED
            and release.published_at
            and release.published_commit_sha
            and release.published_commit_sha == release.approved_commit_sha
        ),
        None,
    )
    if published is None:
        incident.waiting_reason = (
            "The reviewed successor awaits its fresh release gates and successful publication."
        )
        return

    # A reintegrated candidate has different deliverable IDs, so ordinary
    # same-source release supersession cannot close this failed attempt.
    if original.state == TeamRecordState.BLOCKED:
        incident.evidence["superseded_publication_error"] = original.error
        original.state = TeamRecordState.REJECTED
        original.error = None
        original.updated_at = now
        result.setdefault("superseded_release_package_ids", []).append(original.id)
        result.setdefault("resolved_inbox_item_ids", []).extend(
            _resolve_superseded_release_failure_inbox_items(state, release=original, now=now)
        )
    blocker = state.blockers.get(incident.blocker_id)
    if blocker and blocker.state == "open":
        resolve_blocker(
            state,
            blocker=blocker,
            actor_agent_id=None,
            dispatch_id=None,
            note=f"Publication recovered by successfully published package {published.id}.",
            reference_ids=[incident.id, original.id, published.id, published.published_commit_sha],
            now=now,
        )
        result.setdefault("resolved_blocker_ids", []).append(blocker.id)
    incident.evidence["published_successor_release_id"] = published.id
    incident.state = "resumed"
    incident.resumed_at = incident.updated_at = now
    incident.waiting_reason = None
    _recalculate_project_status(state, incident.project_id)


def _complete_successor(state, *, incident, original, successor, deliverables, result, now):
    if digest(recovery_inputs(state, original.id)) != incident.input_fingerprint:
        raise ValueError(
            "Original contribution requirements changed while recovery was reviewed; preview the new frontier."
        )
    deliverable_ids = sorted(row.id for row in deliverables)
    project = state.projects[original.project_id]
    if not project.git or not project.git.recovery.enabled:
        raise ValueError("Project recovery policy no longer authorizes continuation.")
    if project.git.recovery.model_dump(mode="json") != incident.evidence.get("authorized_policy"):
        raise ValueError("Recovery policy changed while the candidate was reviewed.")
    actor_id = incident.evidence.get("authorized_actor_id")
    if actor_id:
        from .git_recovery import agent_can_recover

        blocker = state.blockers[incident.blocker_id]
        if not agent_can_recover(state, blocker, actor_id):
            raise ValueError(
                "The agent authorized to complete this recovery is no longer eligible."
            )
    consumers = affected_consumers(state, original.id)
    if consumers != incident.evidence.get("authorized_consumer_ids", []):
        raise ValueError("The set of affected consumers changed; obtain a new recovery preview.")
    if any(state.tasks[key].project_id != original.project_id for key in consumers):
        raise ValueError("Recovery cannot change consumers in another project.")
    _retire_rejected_generations(
        state, incident=incident, successor=successor, result=result, now=now
    )
    if original.mutation_policy and not original.mutation_policy.allow_agent_cancellation:
        raise ValueError(
            "The original task's policy forbids retirement; its successor is retained for an authorized disposition."
        )
    assessment = assess_task(state, original.id)
    if assessment.unresolved_descendant_ids:
        raise ValueError(
            "Original task still has unresolved descendants: "
            + ", ".join(assessment.unresolved_descendant_ids)
        )
    handoffs, reviews = task_cancellation_blocking_handoff_ids(
        state,
        acceptance_ids=assessment.active_acceptance_ids,
        review_request_ids=assessment.active_review_request_ids,
    )
    if handoffs or reviews:
        raise ValueError("Original task still has active acceptance/review handoffs.")
    if any(
        row.state == "open" and row.task_id == original.id and row.id != incident.blocker_id
        for row in state.blockers.values()
    ):
        raise ValueError("Original task has another unresolved blocker.")
    # This method is first validated on a private state. Clear this incident's
    # propagated blocker before checking consumer terminalization, otherwise its
    # own downstream gate prevents the atomic replacement it authorizes.
    blocker = state.blockers.get(incident.blocker_id)
    if blocker and blocker.state == "open":
        resolve_blocker(
            state,
            blocker=blocker,
            actor_agent_id=None,
            dispatch_id=None,
            note=f"Reviewed successor {successor.id} adopted; original Git evidence retained.",
            reference_ids=[incident.id, successor.id, *deliverable_ids],
            now=now,
        )
    preserve_work(state, incident)
    if original.state not in {"accepted", "finished", "cancelled"}:
        original.cancellation = TaskCancellation(
            reason_code="reviewed_git_recovery",
            reason=f"Reviewed successor {successor.id} satisfies this recovery's requirements",
            actor_kind="system",
            actor_id=incident.id,
            successor_task_id=successor.id,
            rebind_dependents=True,
            cancelled_at=now,
        )
        original.state = TeamTaskState.CANCELLED
        original.state_entered_at = original.updated_at = now
        finalize_active_git_work_contexts_for_task(state, task_id=original.id, now=now)
    boundary_successors = {}
    for key in consumers:
        consumer = state.tasks[key]
        contract = consumer.deliverable_contract
        if not consumer.integration_boundary or not (
            consumer.deliverable_ids or contract and contract.pinned_source_deliverable_ids
        ):
            continue
        if any(
            dispatch.status in {"leased", "preparing"} and key in dispatch.task_ids
            for dispatch in state.dispatches.values()
        ):
            raise ValueError(
                f"Consumer {key} is still executing; await completion before retiring its boundary."
            )
        from .submitted_integration_recovery import (
            plan_submitted_integration_boundary_recovery,
            apply_submitted_integration_boundary_recovery,
        )

        # Retain every old artifact before retiring a context. A new boundary is
        # separately reviewed; none of its predecessor's decisions are copied.
        incident.preserved_context_ids = sorted(
            set(incident.preserved_context_ids)
            | {row.id for row in state.git_work_contexts.values() if row.task_id == key}
        )
        incident.preserved_attempt_ids = sorted(
            set(incident.preserved_attempt_ids)
            | {row.id for row in state.git_preparation_attempts.values() if row.task_id == key}
        )
        finalize_active_git_work_contexts_for_task(state, task_id=key, now=now)
        base = consumer.git_base_commit_sha
        if (
            consumer.git_constraints.intent == "snapshot"
            and not consumer.git_constraints.required_base_commit_sha
            and project.git.recovery.automatic_same_branch_renewal
            and consumer.git_base_branch == successor.git_base_branch
        ):
            base = successor.git_base_commit_sha
        replacement_id = f"{incident.id}-boundary-{digest(key)[:12]}"
        boundary_plan = plan_submitted_integration_boundary_recovery(
            state,
            predecessor_task_id=original.id,
            successor_task_id=successor.id,
            integration_task_id=key,
            replacement_task_id=replacement_id,
            expected_dependency_task_ids=tuple(consumer.dependency_task_ids),
            expected_required_contribution_task_ids=tuple(contract.required_contribution_task_ids),
            reason=f"Reviewed Git recovery {incident.id}",
            git_base_branch=consumer.git_base_branch,
            git_base_commit_sha=base,
            actor_kind="agent",
        )
        if not boundary_plan.terminalize_old_boundary:
            raise ValueError(
                f"Consumer {key} awaits: " + "; ".join(boundary_plan.terminalization_block_reasons)
            )
        apply_submitted_integration_boundary_recovery(
            state,
            plan=boundary_plan,
            actor_kind="agent",
            actor_id=actor_id or successor.owner_agent_id,
            result=result,
            now=now,
        )
        replacement = state.tasks[replacement_id]
        replacement.git_constraints = consumer.git_constraints.model_copy(deep=True)
        replacement.deliverable_contract.pinned_source_deliverable_ids = sorted(
            {
                replacement_id
                for source_id in contract.pinned_source_deliverable_ids
                for replacement_id in (
                    deliverable_ids
                    if state.deliverables[source_id].task_id == original.id
                    else [source_id]
                )
            }
        )
        boundary_successors[key] = replacement_id
    plan = plan_task_dependency_rebinding(
        state, predecessor_task_id=original.id, successor_task_id=successor.id
    )
    rebound = apply_task_dependency_rebinding(state, plan=plan, now=now)
    invalidated = invalidate_rebound_integration_git_contexts(state, plan=plan, now=now)
    if original.cancellation and original.cancellation.successor_task_id == successor.id:
        original.cancellation.rebound_dependent_task_ids = sorted(
            set(rebound) | set(boundary_successors)
        )
        original.cancellation.rebound_integration_boundary_task_ids = sorted(
            set(plan.affected_integration_boundary_task_ids) | set(boundary_successors)
        )
        original.cancellation.invalidated_git_work_context_ids = sorted(
            key for values in invalidated.values() for key in values
        )
    incident.evidence["consumer_successor_ids"] = boundary_successors
    incident.evidence["replacement_deliverable_ids"] = deliverable_ids
    # Retain the singular mapping for older readers only when it is unambiguous.
    incident.replacement_deliverables = (
        {row["id"]: deliverable_ids[0] for row in incident.inputs["sources"]}
        if len(deliverable_ids) == 1
        else {}
    )
    incident.state = "resumed"
    incident.resumed_at = incident.updated_at = now
    incident.waiting_reason = None
    from .task_supervision import queue_parent_resolution_transitions

    queue_parent_resolution_transitions(
        state, child_task_id=original.id, sender_agent_id=None, result=result, now=now
    )
    for key in [*rebound, *boundary_successors.values()]:
        owner = state.tasks[key].owner_agent_id
        if owner:
            _mark_agent_work_due(state, agent_id=owner, now=now)
            _append_agent_wake_requested_event(
                state,
                agent_id=owner,
                result=result,
                reason="git_recovery_adopted",
                now=now,
                delay_seconds=0,
                idempotency_suffix=f"{incident.id}:{key}",
            )


def _retire_rejected_generations(state, *, incident, successor, result, now):
    from .task_supervision import queue_parent_resolution_transitions

    for task_id in incident.recovery_task_ids:
        prior = state.tasks[task_id]
        if prior.id == successor.id or prior.state != TeamTaskState.REJECTED:
            continue
        assessment = assess_task(state, task_id)
        handoffs, reviews = task_cancellation_blocking_handoff_ids(
            state,
            acceptance_ids=assessment.active_acceptance_ids,
            review_request_ids=assessment.active_review_request_ids,
        )
        if (
            handoffs
            or reviews
            or assessment.unresolved_descendant_ids
            or set(assessment.active_blocker_ids) - {incident.blocker_id}
            or affected_consumers(state, task_id)
            or prior.mutation_policy
            and not prior.mutation_policy.allow_agent_cancellation
            or any(
                dispatch.status in {"preparing", "leased"} and task_id in dispatch.task_ids
                for dispatch in state.dispatches.values()
            )
        ):
            raise ValueError(
                f"Rejected recovery generation {task_id} still has unresolved lifecycle obligations."
            )
        incident.preserved_attempt_ids = sorted(
            set(incident.preserved_attempt_ids)
            | {row.id for row in state.git_preparation_attempts.values() if row.task_id == task_id}
        )
        incident.preserved_context_ids = sorted(
            set(incident.preserved_context_ids)
            | {row.id for row in state.git_work_contexts.values() if row.task_id == task_id}
        )
        prior.cancellation = TaskCancellation(
            reason_code="reviewed_git_recovery_generation",
            reason=f"Reviewed generation {successor.id} replaces this rejected attempt",
            actor_kind="system",
            actor_id=incident.id,
            successor_task_id=successor.id,
            cancelled_at=now,
        )
        prior.state = TeamTaskState.CANCELLED
        prior.state_entered_at = prior.updated_at = now
        finalize_active_git_work_contexts_for_task(state, task_id=task_id, now=now)
        queue_parent_resolution_transitions(
            state, child_task_id=task_id, sender_agent_id=None, result=result, now=now
        )
