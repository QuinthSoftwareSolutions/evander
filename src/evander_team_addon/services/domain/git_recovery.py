"""Pure state transitions and evidence identities for Git recovery."""

from __future__ import annotations

import hashlib
import json

from ...model_git_recovery import GitBaselineConstraints, GitRecoveryIncident
from ...models import utc_now


def digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def _task_requirements(task) -> dict:
    # Freeze semantic scope, not lifecycle state or scheduling metadata. A new
    # preview must never authorize an old candidate against changed requirements.
    return task.model_dump(
        mode="json",
        include={
            "title",
            "description",
            "task_kind",
            "integration_boundary",
            "outcome_contract",
            "deliverable_contract",
            "source_evidence_snapshot_ids",
        },
    )


def recovery_inputs(state, task_id: str) -> dict:
    task = state.tasks[task_id]
    project = state.projects[task.project_id]
    contract = task.deliverable_contract
    dependency_ids = set(task.dependency_task_ids)
    if contract:
        dependency_ids.update(contract.required_contribution_task_ids)
    pinned = set(contract.pinned_source_deliverable_ids if contract else [])
    sources = [
        {
            "id": source.id,
            "task_id": source.task_id,
            "state": source.state.value,
            "git_refs": source.git_refs.model_dump(mode="json") if source.git_refs else None,
            "superseded_by": source.superseded_by_deliverable_id,
            "requirements": _task_requirements(state.tasks[source.task_id]),
        }
        for source in sorted(state.deliverables.values(), key=lambda row: row.id)
        if (
            source.id in pinned
            if pinned
            else source.task_id in dependency_ids and not source.superseded_by_deliverable_id
        )
    ]
    branch = task.git_base_branch or (project.git.base_branch if project.git else None)
    base = task.git_base_commit_sha
    if base is None:
        # A server-recorded context can recover a legacy leaf's pin. Do not use
        # the mutable branch tip or infer a root from authored merge ancestry.
        contexts = sorted(
            (
                row
                for row in state.git_work_contexts.values()
                if row.task_id == task.id and row.base_branch == branch
            ),
            key=lambda row: (row.updated_at, row.id),
            reverse=True,
        )
        if contexts:
            context = contexts[0]
            base = (
                context.dependency_composition_manifest.base_commit_sha
                if context.dependency_composition_manifest
                else context.base_commit
                if not context.dependency_git_sources
                else None
            )
    return {
        "task_id": task.id,
        "requirements": _task_requirements(task),
        "project_id": project.id,
        "managed_repo_id": project.git.managed_repo_id if project.git else None,
        "directory_path": project.git.directory_path if project.git else None,
        "base_branch": branch,
        "base_commit_sha": base,
        "constraints": task.git_constraints.model_dump(mode="json"),
        "required_task_ids": sorted(dependency_ids),
        "pinned_source_ids": sorted(pinned),
        "sources": sources,
    }


def incident_for_blocker(state, blocker_id):
    return next(
        (row for row in state.git_recovery_incidents.values() if row.blocker_id == blocker_id), None
    )


def agent_can_recover(state, blocker, agent_id):
    from .blockers import agent_can_resolve_blocker
    from ...models import agent_has_project_assignment, project_lead_agent_id

    agent = state.agents.get(agent_id)
    return bool(
        agent
        and agent.status == "active"
        and agent.team_id == blocker.team_id
        and (
            agent_can_resolve_blocker(state, blocker=blocker, agent_id=agent_id)
            or (
                project_lead_agent_id(state, blocker.project_id) == agent_id
                and agent_has_project_assignment(state, agent_id, blocker.project_id)
            )
        )
    )


def record_incident(
    state,
    *,
    blocker,
    cause="unknown",
    stage="preparation",
    evidence=None,
    task_id=None,
    inputs=None,
):
    from .preparation_failures import task_git_work_context_generation

    task_id = task_id or blocker.task_id
    inputs = inputs or recovery_inputs(state, task_id)
    fingerprint = digest(inputs)
    incident = incident_for_blocker(state, blocker.id)
    if incident is None:
        parent = next(
            (
                row
                for row in state.git_recovery_incidents.values()
                if task_id in row.recovery_task_ids
                or task_id in row.evidence.get("consumer_successor_ids", {}).values()
            ),
            None,
        )
        incident = GitRecoveryIncident(
            id=f"git-recovery-{digest([blocker.id, fingerprint])[:24]}",
            blocker_id=blocker.id,
            project_id=blocker.project_id,
            task_id=task_id,
            lineage_id=parent.lineage_id
            if parent
            else state.tasks[task_id].work_lineage_id or task_id,
            cause=cause,
            stage=stage,
            input_fingerprint=fingerprint,
            inputs=inputs,
            preparation_generation=task_git_work_context_generation(state, task_id),
        )
        state.git_recovery_incidents[incident.id] = incident
    incident.evidence.update(evidence or {})
    incident.cause = cause
    incident.updated_at = utc_now()
    if incident.state == "preparing":
        incident.state = "diagnosed"
    preserve_work(state, incident)
    return incident


def preserve_work(state, incident):
    if hasattr(state, "query_records"):
        state.query_records("git_preparation_attempts", "task_id=?", (incident.task_id,))
        state.query_records("git_work_contexts", "task_id=?", (incident.task_id,))
    incident.preserved_attempt_ids = sorted(
        set(incident.preserved_attempt_ids)
        | {
            row.id
            for row in state.git_preparation_attempts.values()
            if row.task_id == incident.task_id
        }
    )
    incident.preserved_context_ids = sorted(
        set(incident.preserved_context_ids)
        | {row.id for row in state.git_work_contexts.values() if row.task_id == incident.task_id}
    )


def preparation_epoch(state, task_id):
    # Physical attempts change independently of logical task content. Counts are
    # monotonic, persisted, and never depend on request IDs or error wording.
    return sum(
        row.preparation_retries
        for row in state.git_recovery_incidents.values()
        if row.task_id == task_id
    )


def preservation_hold(state, *, attempt_id=None, context_id=None, session_id=None):
    if hasattr(state, "query_records"):
        attempts, contexts = [attempt_id] if attempt_id else [], [context_id] if context_id else []
        if session_id:
            attempts.extend(
                row.id
                for row in state.query_records(
                    "git_preparation_attempts",
                    "json_extract(payload_json,'$.session_id')=?",
                    (session_id,),
                )
                if row.session_id == session_id
            )
            prefix = f"agents/{session_id}/"
            contexts.extend(
                row.id
                for row in state.query_records(
                    "git_work_contexts",
                    "agent_branch>=? AND agent_branch<?",
                    (prefix, prefix[:-1] + "0"),
                )
                if row.agent_branch.startswith(prefix)
            )
        records = state.query_records(
            "git_recovery_incidents",
            "id IN (SELECT i.id FROM team_git_recovery_incidents i INDEXED BY idx_team_recovery_attempt_retention, json_each(i.payload_json,'$.preserved_attempt_ids') j WHERE json_array_length(i.payload_json,'$.preserved_attempt_ids')>0 AND j.value IN ("
            + ",".join("?" for _ in attempts)
            + ") UNION SELECT i.id FROM team_git_recovery_incidents i INDEXED BY idx_team_recovery_retention, json_each(i.payload_json,'$.preserved_context_ids') j WHERE json_array_length(i.payload_json,'$.preserved_context_ids')>0 AND j.value IN ("
            + ",".join("?" for _ in contexts)
            + "))",
            [*attempts, *contexts],
        )
    else:
        records = state.git_recovery_incidents.values()
    for incident in records:
        if attempt_id and attempt_id in incident.preserved_attempt_ids:
            return incident.id
        if context_id and context_id in incident.preserved_context_ids:
            return incident.id
        if session_id and any(
            attempt.session_id == session_id and attempt.id in incident.preserved_attempt_ids
            for attempt in state.git_preparation_attempts.values()
        ):
            return incident.id
        if session_id and any(
            context.id in incident.preserved_context_ids
            and context.agent_branch.startswith(f"agents/{session_id}/")
            for context in state.git_work_contexts.values()
        ):
            return incident.id
    return None


def inherited_constraints(
    state, *, parent_task_id=None, supplied_commit=None, requested=None, parent_constraints=None
):
    supplied_commit = str(supplied_commit or "").strip().casefold() or None
    parent = state.tasks.get(parent_task_id or "")
    inherited = parent_constraints or (parent.git_constraints if parent else None)
    constraints = (
        GitBaselineConstraints.model_validate(requested)
        if requested is not None
        else (
            inherited.model_copy(deep=True)
            if inherited
            else GitBaselineConstraints(
                intent="required" if supplied_commit else "snapshot",
                required_base_commit_sha=supplied_commit,
            )
        )
    )
    if requested is None and supplied_commit:
        constraints.intent = "required"
        constraints.required_base_commit_sha = supplied_commit
    if inherited:
        if inherited.intent in {"unknown", "required"} and constraints.intent == "snapshot":
            raise ValueError("child work cannot relax its parent's baseline intent")
        if (
            inherited.required_base_commit_sha
            and constraints.required_base_commit_sha != inherited.required_base_commit_sha
        ):
            raise ValueError("child work cannot change its parent's required baseline")
        constraints.excluded_ancestor_shas = sorted(
            set(constraints.excluded_ancestor_shas) | set(inherited.excluded_ancestor_shas)
        )
    if (
        constraints.required_base_commit_sha
        and supplied_commit
        and constraints.required_base_commit_sha != supplied_commit
    ):
        raise ValueError("supplied Git base conflicts with the required baseline")
    return constraints


def verify_baseline_constraints(
    managed, *, project_git, constraints, base_commit_sha, candidate_shas=()
):
    from evander.git_recovery import GitPreparationFailure

    if (
        constraints.required_base_commit_sha
        and constraints.required_base_commit_sha != base_commit_sha
    ):
        raise GitPreparationFailure(
            "candidate changed an explicit baseline requirement", cause="policy_conflict"
        )
    if constraints.excluded_ancestor_shas:
        if not base_commit_sha or not managed:
            raise GitPreparationFailure(
                "excluded ancestry requires managed Git verification", cause="policy_conflict"
            )
        managed.verify_excluded_ancestors(
            project_git.managed_repo_id,
            directory_path=project_git.directory_path,
            commit_shas=[base_commit_sha, *candidate_shas],
            excluded_ancestor_shas=constraints.excluded_ancestor_shas,
        )


def mark_preparation_resumed(state, task_id, *, context_id):
    from .blockers import resolve_blocker

    now = utc_now()
    for incident in state.git_recovery_incidents.values():
        if incident.task_id != task_id or not (
            incident.state == "preparing"
            or incident.state == "awaiting_review"
            and incident.action_id == "resolve_merge"
        ):
            continue
        # This is called only after fresh provenance validation and durable
        # context activation. Merely queueing work cannot close the incident.
        incident.state = "resumed"
        incident.resumed_at = incident.updated_at = now
        incident.waiting_reason = None
        blocker = state.blockers.get(incident.blocker_id)
        if blocker and blocker.state == "open":
            resolve_blocker(
                state,
                blocker=blocker,
                actor_agent_id=None,
                dispatch_id=None,
                note="Verified Git preparation resumed.",
                reference_ids=[context_id, incident.id],
                now=now,
            )
