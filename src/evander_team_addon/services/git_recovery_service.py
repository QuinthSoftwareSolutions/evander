"""Incident-scoped orchestration of reviewed Git repairs and exact-source retries."""

from __future__ import annotations

from ..models import (
    AgentTask,
    GitWorkContextState,
    TaskDeliverableContract,
    TeamTaskState,
    agent_has_project_assignment,
    project_lead_agent_id,
    utc_now,
)
from .domain.git_recovery import (
    agent_can_recover,
    digest,
    incident_for_blocker,
    preserve_work,
    record_incident,
    recovery_inputs,
)
from .domain.blockers import resolve_blocker
from .domain.dependency_composition_recovery import (
    _resolver_assignment_error,
    start_dependency_composition_recovery,
)
from .domain.work_due import _mark_agent_work_due
from .domain.wake import _append_agent_wake_requested_event
from .commands.types import TeamCommandApplyResult, TeamCommandContext, TeamCommandValidationError


class TeamGitRecoveryService:
    def __init__(self, admin_service, *, salvage_backend=None):
        self.salvage_backend = salvage_backend
        self.admin = admin_service
        self.repository = admin_service.repository

    def _authorize(self, state, blocker_id, *, session_id=None):
        blocker = state.blockers.get(blocker_id)
        incident = incident_for_blocker(state, blocker_id)
        publication = bool(
            incident
            and incident.stage == "publication"
            and blocker
            and blocker.source_kind == "release_package"
            and blocker.source_id == incident.evidence.get("release_package_id")
        )
        if (
            blocker is None
            or (incident.task_id if publication else blocker.task_id) not in state.tasks
        ):
            raise ValueError("Git recovery requires a task preparation blocker")
        from .domain.preparation_failures import TASK_EXECUTION_PREPARATION_BLOCKER_REASON_CODES

        if (
            blocker.reason_code not in TASK_EXECUTION_PREPARATION_BLOCKER_REASON_CODES
            and not publication
        ):
            raise ValueError("blocker is not a Git preparation incident")
        if session_id is None:
            return blocker, None
        from .domain.agent_access import _agent_for_session

        actor = _agent_for_session(state, session_id=session_id)
        runtime = state.agent_runtimes.get(actor.id)
        dispatch = state.dispatches.get(runtime.active_dispatch_id or "") if runtime else None
        if (
            not dispatch
            or dispatch.status not in {"leased", "preparing"}
            or blocker.id not in dispatch.blocker_ids
        ):
            raise ValueError("Git recovery blocker is outside the active dispatch scope")
        if not agent_can_recover(state, blocker, actor.id):
            raise ValueError("Git recovery requires an active agent on the blocker resolution line")
        return blocker, actor

    def preview(self, blocker_id, *, session_id=None):
        state = self.repository.read()
        blocker, actor = self._authorize(state, blocker_id, session_id=session_id)
        incident = incident_for_blocker(state, blocker_id)
        if incident and incident.stage == "publication":
            project = state.projects[incident.project_id]
            self.admin._managed_git_admin.refresh_repository(project.git.managed_repo_id)
            state = self.repository.read()
            blocker, actor = self._authorize(state, blocker_id, session_id=session_id)
        return self._preview(state, blocker, actor)

    def _preview(self, state, blocker, actor):
        incident = incident_for_blocker(state, blocker.id)
        # Legacy incidents are diagnosed on demand without mutating historical rows.
        if incident is None:
            transient = state.model_copy(deep=True)
            incident = record_incident(transient, blocker=blocker)
        task = state.tasks[incident.task_id]
        project = state.projects[task.project_id]
        from .domain.git_recovery_publication import current_incident_inputs

        inputs = current_incident_inputs(state, incident)
        policy = project.git.recovery if project.git else None
        family = [
            row
            for row in state.git_recovery_incidents.values()
            if row.lineage_id == incident.lineage_id
        ]
        contexts = sorted(
            (row for row in state.git_work_contexts.values() if row.task_id == task.id),
            key=lambda row: (row.updated_at, row.id),
            reverse=True,
        )
        context = contexts[0] if contexts else None
        manifest = context.dependency_composition_manifest if context else None
        from .domain.git_recovery_continuation import affected_consumers

        payload = {
            "blocker_id": blocker.id,
            "incident_id": incident.id,
            "revision": state.revision,
            "actor_id": actor.id if actor else "operator",
            "project_id": project.id,
            "cause": incident.cause,
            "stage": incident.stage,
            "inputs": inputs,
            "evidence": incident.evidence,
            "state": incident.state,
            "git_points": {
                "original_baseline": task.git_base_commit_sha,
                "composed_start": manifest.composed_commit_sha if manifest else None,
                "candidate": inputs.get("release", {}).get("candidate_commit_sha")
                or (context.commit_sha if context else None),
                "observed_destination": incident.evidence.get("destination_commit_sha"),
            },
            "continuation_waiting_reason": incident.waiting_reason,
            "preserved_attempt_ids": incident.preserved_attempt_ids,
            "preserved_context_ids": incident.preserved_context_ids,
            "preparation_retries": sum(row.preparation_retries for row in family),
            "candidate_generations": sum(row.candidate_generations for row in family),
            "policy": policy.model_dump(mode="json") if policy else None,
            "affected_consumer_ids": affected_consumers(state, task.id),
            "actions": [],
            "waiting_reason": None,
        }

        def finish(reason=None):
            payload["waiting_reason"] = reason
            payload["preview_token"] = digest(payload)
            return payload

        if blocker.state != "open" or incident.state in {"resumed", "superseded"}:
            return finish("This incident no longer requires an action.")
        if not policy or not policy.enabled or project.status not in {"active", "blocked"}:
            return finish("Project Git recovery is disabled or the project is inactive.")
        if digest(inputs) != incident.input_fingerprint:
            return finish("Source requirements changed; a fresh preparation diagnosis is required.")
        if incident.state in {"preparing", "awaiting_review"}:
            return finish(
                "Waiting for verified preparation or the existing recovery task's review."
            )
        if actor and incident.cause in {
            "incompatible_source_base",
            "destination_advanced",
            "reviewed_object_unavailable",
            "source_ref_moved",
            "contradictory_provenance",
        }:
            lead_id = project_lead_agent_id(state, project.id)
            if actor.id != lead_id:
                if not lead_id or not agent_can_recover(state, blocker, lead_id):
                    return finish("No active configured project lead can authorize this successor.")
                payload["resolver_agent_id"] = lead_id
                payload["actions"] = [{"id": "route_to_project_lead", "requires_review": False}]
                return finish()
        owner = self._resolver(state, task, actor)
        if owner is None:
            return finish(
                "No active, assigned resolver is available within the caller's authority."
            )
        payload["resolver_agent_id"] = owner.id
        if incident.state == "waiting" and incident.recovery_task_ids:
            payload["actions"] = [{"id": "resume_reviewed_successor", "requires_review": False}]
            return finish()
        if incident.stage == "publication":
            try:
                payload["reintegration"] = self._publication_preview(state, incident, owner)
                payload["renewed_base_commit_sha"] = payload["reintegration"]["base_commit_sha"]
            except (ValueError, RuntimeError, LookupError) as exc:
                return finish(f"Publication recovery prerequisite: {exc}")
            action = "renew_release"
        elif incident.cause == "merge_conflict":
            recoveries = [
                row
                for row in state.dependency_composition_recoveries.values()
                if row.blocker_id == blocker.id
                and row.state in {"awaiting_assignment", "in_progress"}
            ]
            if len(recoveries) != 1:
                return finish("There is no uniquely authoritative dependency composition recovery.")
            error = _resolver_assignment_error(
                state, actor=actor, resolver=owner, recovery=recoveries[0]
            )
            if error:
                return finish(error)
            payload["dependency_recovery_id"] = recoveries[0].id
            action = "resolve_merge"
        elif incident.cause in {
            "workspace_receipt_mismatch",
            "workspace_missing",
            "workspace_dirty",
            "transport_unavailable",
            "inputs_changed",
        }:
            if (
                incident.cause != "workspace_dirty"
                and payload["preparation_retries"] >= policy.preparation_retry_limit
            ):
                return finish(
                    "Automatic preparation retry budget exhausted; the unresolved prerequisite requires diagnosis."
                )
            try:
                payload["verified_inputs"] = self._verify_inputs(state, task, inputs)
            except (ValueError, RuntimeError, LookupError) as exc:
                return finish(f"Cannot reconstruct unchanged inputs: {exc}")
            if incident.cause == "workspace_dirty":
                if not self.salvage_backend or not incident.evidence.get("attempt_id"):
                    return finish(
                        "Dirty work requires an authoritative retained attempt and a salvage-capable backend."
                    )
                action = "salvage_workspace"
            else:
                action = "reprepare"
        elif incident.cause in {"incompatible_source_base", "destination_advanced"}:
            if actor and project_lead_agent_id(state, project.id) != actor.id:
                return finish(
                    "Route this incident to the configured project lead for a successor integration."
                )
            constraints = task.git_constraints
            if (
                policy.automatic_same_branch_renewal
                and constraints.intent == "snapshot"
                and not constraints.required_base_commit_sha
                and (not project.release_policy or project.release_policy.reintegration.enabled)
            ):
                try:
                    branch = (
                        project.git.base_branch
                        or self.admin._resolve_managed_git_default_branch(
                            managed_repo_id=project.git.managed_repo_id,
                            directory_path=project.git.directory_path,
                        )
                    )
                    if branch != inputs["base_branch"]:
                        return finish("Renewal would change the configured workstream.")
                    base = self.admin._resolve_managed_git_branch_commit(
                        managed_repo_id=project.git.managed_repo_id,
                        directory_path=project.git.directory_path,
                        branch=branch,
                    )
                    payload["verified_inputs"] = self._verify_inputs(
                        state, task, {**inputs, "base_commit_sha": base}
                    )
                    payload["renewed_base_commit_sha"] = base
                    action = "renew_baseline"
                except (ValueError, RuntimeError, LookupError) as exc:
                    return finish(f"The configured baseline cannot satisfy this recovery: {exc}")
            else:
                # Reimplementation on a fixed base does not transfer old acceptance.
                if not inputs["base_commit_sha"]:
                    return finish(
                        "No authoritative fixed baseline is available for replacement work."
                    )
                action = "replace_sources"
        elif incident.cause in {
            "reviewed_object_unavailable",
            "source_ref_moved",
            "contradictory_provenance",
        }:
            if not inputs["base_commit_sha"]:
                return finish(
                    "Source proof is ambiguous and no authoritative replacement baseline is recorded."
                )
            if actor and project_lead_agent_id(state, project.id) != actor.id:
                return finish(
                    "Route missing source proof to the project lead for reviewed replacement work."
                )
            action = "replace_sources"
        else:
            return finish(
                "No mechanical repair is proven. Diagnose the unknown failure or resolve the explicit policy conflict."
            )
        if action == "replace_sources":
            try:
                managed = self.admin._managed_git_admin
                managed.resolve_snapshot(
                    project.git.managed_repo_id,
                    project.git.directory_path,
                    commit_sha=inputs["base_commit_sha"],
                )
                from .domain.git_recovery import verify_baseline_constraints

                verify_baseline_constraints(
                    managed,
                    project_git=project.git,
                    constraints=task.git_constraints,
                    base_commit_sha=inputs["base_commit_sha"],
                )
            except (ValueError, RuntimeError, LookupError, AttributeError) as exc:
                return finish(f"Replacement baseline cannot be verified: {exc}")
        if (
            action != "reprepare"
            and payload["candidate_generations"] >= policy.candidate_generation_limit
        ):
            return finish("Candidate-changing recovery budget exhausted for this work lineage.")
        payload["actions"] = [{"id": action, "requires_review": action != "reprepare"}]
        return finish()

    def _resolver(self, state, task, actor):
        from .domain.agent_access import _supervises

        project = state.projects[task.project_id]
        configured = (
            project.release_policy.reintegration.owner_agent_id if project.release_policy else None
        )
        candidates = [
            configured,
            task.owner_agent_id,
            project_lead_agent_id(state, project.id),
            actor.id if actor else None,
        ]
        candidates += sorted(state.agents)
        for agent_id in dict.fromkeys(candidates):
            agent = state.agents.get(agent_id)
            if (
                agent
                and agent.status == "active"
                and agent.team_id == project.team_id
                and agent_has_project_assignment(state, agent.id, project.id)
                and (
                    actor is None or actor.id == agent.id or _supervises(state, actor.id, agent.id)
                )
            ):
                return agent
        return None

    def _publication_preview(self, state, incident, owner):
        from .reintegration_service import TeamReintegrationService
        from .domain.git_recovery import verify_baseline_constraints

        project = state.projects[incident.project_id]
        if not project.git.recovery.automatic_same_branch_renewal:
            raise ValueError("same-branch renewal is disabled")
        preview = TeamReintegrationService(self.admin)._preview(
            state, incident.evidence["release_package_id"], owner_agent_id=owner.id
        )
        managed = self.admin._managed_git_admin
        constraints = []
        for source in preview["contributions"]:
            task = state.tasks[source["task_id"]]
            if (
                task.git_constraints.intent != "snapshot"
                or task.git_constraints.required_base_commit_sha
            ):
                raise ValueError(
                    f"source task {task.id} has an explicit or legacy baseline constraint"
                )
            if task.git_base_branch != preview["base_branch"]:
                raise ValueError("publication recovery would cross configured workstreams")
            constraints.extend(task.git_constraints.excluded_ancestor_shas)
            verify_baseline_constraints(
                managed,
                project_git=project.git,
                constraints=task.git_constraints,
                base_commit_sha=preview["base_commit_sha"],
                candidate_shas=[source["git_refs"]["commit_sha"]],
            )
        managed.verify_branch_commit_ancestry(
            project.git.managed_repo_id,
            directory_path=project.git.directory_path,
            branch=preview["base_branch"],
            commit_sha=preview["base_commit_sha"],
            required_ancestor_shas=[incident.evidence["destination_commit_sha"]],
        )
        # Use ordinary preparation proof for the exact accepted release sources.
        trial = state.model_copy(deep=True)
        task = trial.tasks[incident.task_id].model_copy(deep=True)
        task.id = f"{incident.id}-preflight"
        task.git_base_commit_sha = preview["base_commit_sha"]
        task.dependency_task_ids = sorted({row["task_id"] for row in preview["contributions"]})
        task.deliverable_contract = TaskDeliverableContract(
            require_git_provenance=True,
            required_contribution_task_ids=task.dependency_task_ids,
            pinned_source_deliverable_ids=[
                row["deliverable_id"] for row in preview["contributions"]
            ],
        )
        task.integration_boundary = True
        trial.tasks[task.id] = task
        self._verify_inputs(trial, task, recovery_inputs(trial, task.id))
        preview["git_recovery_excluded_ancestors"] = sorted(set(constraints))
        return preview

    def _verify_inputs(self, state, task, inputs):
        managed = self.admin._managed_git_admin
        if managed is None or not inputs["base_commit_sha"]:
            raise ValueError("an authoritative pinned base and managed repository are required")
        git = state.projects[task.project_id].git
        base = managed.resolve_snapshot(
            git.managed_repo_id, git.directory_path, commit_sha=inputs["base_commit_sha"]
        )
        # Use the normal source verifier, including retained review refs and
        # provenance consistency. Object availability alone is insufficient.
        from types import SimpleNamespace
        from ..provider_git_sources import _project_repo_task_dependency_git_sources

        verified_state = state
        if task.git_base_commit_sha != inputs["base_commit_sha"]:
            verified_state = state.model_copy(deep=True)
            verified_state.tasks[task.id].git_base_commit_sha = inputs["base_commit_sha"]
        rows = _project_repo_task_dependency_git_sources(
            SimpleNamespace(admin_service=SimpleNamespace(managed_git_admin=managed)),
            state=verified_state,
            task_id=task.id,
            managed_repo_id=git.managed_repo_id,
        )
        constraints = task.git_constraints
        if (
            constraints.required_base_commit_sha
            and constraints.required_base_commit_sha != inputs["base_commit_sha"]
        ):
            raise ValueError("the explicit baseline requirement forbids this candidate")
        if constraints.excluded_ancestor_shas:
            managed.verify_excluded_ancestors(
                git.managed_repo_id,
                directory_path=git.directory_path,
                commit_shas=[inputs["base_commit_sha"], *[row["commit_sha"] for row in rows]],
                excluded_ancestor_shas=constraints.excluded_ancestor_shas,
            )
        return {
            "base_commit_sha": base["commit_sha"],
            "base_tree_hash": managed.reduce_commit_frontier(
                git.managed_repo_id,
                directory_path=git.directory_path,
                base_commit_sha=inputs["base_commit_sha"],
                sources=rows,
            )["base_tree_hash"],
            "sources": rows,
        }

    def _capture_salvage(
        self, *, blocker_id, preview_token, action_id, request_id, session_id=None
    ):
        if action_id != "salvage_workspace":
            return None
        state = self.repository.read()
        blocker, actor = self._authorize(state, blocker_id, session_id=session_id)
        incident = incident_for_blocker(state, blocker_id)
        if incident and request_id in incident.applied_requests:
            return None
        preview = self._preview(state, blocker, actor)
        if preview["preview_token"] != preview_token or action_id not in {
            row["id"] for row in preview["actions"]
        }:
            raise ValueError("Git recovery preview changed; obtain a fresh preview")
        # Preparation failure already placed durable holds. Network/guest work
        # happens outside SQLite's writer transaction; apply rechecks the preview.
        if incident is None or not incident.preserved_attempt_ids:
            raise ValueError("salvage requires a durable preservation hold")
        return self.salvage_backend(state, incident)

    def _apply(
        self,
        state,
        *,
        blocker_id,
        preview_token,
        action_id,
        actor,
        request_id,
        dispatch_id=None,
        salvage=None,
    ):
        blocker = state.blockers[blocker_id]
        incident = incident_for_blocker(state, blocker_id)
        if incident and request_id in incident.applied_requests:
            prior = incident.applied_requests[request_id]
            if prior["preview_token"] != preview_token or prior["action_id"] != action_id:
                raise ValueError("request ID was already used for a different Git recovery")
            return {**prior["result"], "idempotent_replay": True}
        preview = self._preview(state, blocker, actor)
        if preview_token != preview["preview_token"]:
            raise ValueError("Git recovery preview changed; obtain a fresh preview")
        if action_id not in {row["id"] for row in preview["actions"]}:
            raise ValueError("Git recovery action is not authorized by this preview")
        if incident is None:
            incident = record_incident(state, blocker=blocker)
        now = utc_now()
        incident.evidence.update(
            authorized_policy=preview["policy"],
            authorized_consumer_ids=preview["affected_consumer_ids"],
            authorized_actor_id=actor.id if actor else None,
        )
        preserve_work(state, incident)
        result = {
            "incident_id": incident.id,
            "blocker_id": blocker.id,
            "action_id": action_id,
            "events": [],
        }
        task = state.tasks[incident.task_id]
        owner = state.agents[preview["resolver_agent_id"]]
        if action_id == "route_to_project_lead":
            from .domain.blockers import assign_blocker_owner

            assign_blocker_owner(
                state,
                blocker=blocker,
                owner_agent_id=owner.id,
                actor_agent_id=actor.id if actor else None,
                reason="Git recovery requires a reviewed successor authorized by the project lead",
                result=result,
                wake_owner=False,
                now=now,
            )
        elif action_id == "resume_reviewed_successor":
            from .domain.git_recovery_continuation import reconcile_git_recovery_successors

            reconcile_git_recovery_successors(state, result=result, now=now)
            result["state"] = incident.state
            result["waiting_reason"] = incident.waiting_reason
        elif action_id == "reprepare":
            if task.git_base_commit_sha is None:
                task.git_base_commit_sha = preview["verified_inputs"]["base_commit_sha"]
                task.git_base_branch = preview["inputs"]["base_branch"]
            incident.preparation_retries += 1
            incident.state = "preparing"
            for context in state.git_work_contexts.values():
                if context.task_id == task.id and context.state == "active":
                    context.state = GitWorkContextState.ABANDONED
                    context.updated_at = now
            resolve_blocker(
                state,
                blocker=blocker,
                actor_agent_id=actor.id if actor else None,
                dispatch_id=dispatch_id,
                note="Exact-source preparation authorized; recovery awaits verified resumption.",
                reference_ids=[incident.id],
                now=now,
            )
            if task.state in {"blocked", "waiting_for_input"}:
                task.state = TeamTaskState.PENDING
                task.state_entered_at = task.updated_at = now
            owner = state.agents[task.owner_agent_id]
        else:
            if action_id == "salvage_workspace":
                if salvage is None:
                    raise ValueError("salvage transport has not completed")
                incident.evidence["salvage"] = salvage
            incident.candidate_generations += 1
            incident.state = "awaiting_review"
            if action_id == "renew_release":
                from .reintegration_service import TeamReintegrationService
                from ..models import GitBaselineConstraints

                release_preview = dict(preview["reintegration"])
                excluded = release_preview.pop("git_recovery_excluded_ancestors")
                TeamReintegrationService(self.admin)._submission_mutator(
                    preview=release_preview,
                    actor_id=f"agent:{actor.id}" if actor else "operator:git-recovery",
                )(state, result)
                successor = state.tasks[result["task_id"]]
                successor.git_constraints = GitBaselineConstraints(
                    intent="snapshot", excluded_ancestor_shas=excluded
                )
            elif action_id == "resolve_merge":
                recovery = state.dependency_composition_recoveries[
                    preview["dependency_recovery_id"]
                ]
                successor = start_dependency_composition_recovery(
                    state,
                    recovery=recovery,
                    blocker=blocker,
                    actor=actor,
                    operator_actor_id="operator:git-recovery" if actor is None else None,
                    resolver=owner,
                    dispatch_id=dispatch_id,
                    result=result,
                    now=now,
                )
            else:
                successor = self._create_successor(
                    state,
                    task=task,
                    incident=incident,
                    preview=preview,
                    action_id=action_id,
                    owner=owner,
                    now=now,
                )
            incident.recovery_task_ids.append(successor.id)
            result["task_id"] = successor.id
        if action_id not in {"route_to_project_lead", "resume_reviewed_successor"}:
            incident.action_id = action_id
        incident.updated_at = now
        if action_id != "resume_reviewed_successor":
            incident.waiting_reason = None
        if action_id != "renew_release":
            _mark_agent_work_due(state, agent_id=owner.id, now=now)
            _append_agent_wake_requested_event(
                state,
                agent_id=owner.id,
                result=result,
                reason="git_recovery_requested",
                now=now,
                delay_seconds=0,
                idempotency_suffix=f"{incident.id}:{incident.preparation_retries}:{incident.candidate_generations}",
            )
        incident.applied_requests[request_id] = {
            "preview_token": preview_token,
            "action_id": action_id,
            "result": {key: value for key, value in result.items() if key != "events"},
        }
        return result

    def _create_successor(self, state, *, task, incident, preview, action_id, owner, now):
        source_ids = [row["id"] for row in preview["inputs"]["sources"]]
        renew = action_id == "renew_baseline"
        successor = AgentTask(
            id=f"{incident.id}-candidate-{incident.candidate_generations}",
            owner_agent_id=owner.id,
            project_id=task.project_id,
            case_id=task.case_id,
            priority=task.priority,
            task_kind=task.task_kind,
            integration_boundary=renew,
            title=("Reintegrate: " if renew else "Replace incompatible contributions: ")
            + task.title,
            description=(
                task.description
                + "\n\nGit recovery of "
                + task.id
                + ". "
                + (
                    "Integrate the exact pinned accepted contributions and repeat all candidate review and verification."
                    if renew
                    else "Reimplement the original contribution requirements on the pinned baseline. The original accepted candidates are historical evidence, not approved ancestry. Submit new code for review; do not merge incompatible source history."
                )
            ),
            originating_task_id=task.id,
            work_lineage_id=task.work_lineage_id,
            parent_task_id=task.parent_task_id,
            mutation_policy=task.mutation_policy.model_copy(deep=True)
            if task.mutation_policy
            else None,
            outcome_contract=task.outcome_contract,
            authored_by=task.authored_by,
            work_item_attributions=task.work_item_attributions,
            git_base_branch=task.git_base_branch,
            git_base_commit_sha=preview.get("renewed_base_commit_sha")
            or preview["inputs"]["base_commit_sha"],
            git_constraints=task.git_constraints.model_copy(deep=True),
            source_evidence_snapshot_ids=list(task.source_evidence_snapshot_ids),
            deliverable_contract=TaskDeliverableContract(
                require_git_provenance=True,
                required_contribution_task_ids=preview["inputs"]["required_task_ids"]
                if renew
                else [],
                pinned_source_deliverable_ids=source_ids if renew else [],
            ),
            dependency_task_ids=preview["inputs"]["required_task_ids"] if renew else [],
            created_by_actor="git-recovery",
            created_at=now,
            updated_at=now,
        )
        if action_id == "salvage_workspace":
            snapshot = incident.evidence["salvage"]
            successor.description += (
                f"\n\nRetained unapproved working content: {snapshot['work_ref']} at {snapshot['commit_sha']}. "
                f"Retained original index: {snapshot['index_ref']} at {snapshot['index_commit_sha']}. "
                "Fetch these exact refs from the managed repository, inspect the changes, and carry the relevant code onto this task's pinned base. "
                "Do not merge their unverified ancestry. The entire original worktree remains retained, including ignored data. "
                "These snapshots are evidence for new work, not accepted contributions."
            )
        if not renew:
            for row in preview["inputs"]["sources"]:
                requirement = state.tasks[row["task_id"]]
                successor.description += f"\n\nRequired contribution {requirement.id}: {requirement.title}\n{requirement.description}"
        state.tasks[successor.id] = successor
        if task.case_id:
            from .domain.case_task import _link_task_to_case

            _link_task_to_case(state, task_id=successor.id, case_id=task.case_id, now=now)
        return successor

    def apply_from_agent(self, *, blocker_id, preview_token, action_id, request_id, session_id):
        from .commands.executor import TeamCommandExecutor

        salvage = self._capture_salvage(
            blocker_id=blocker_id,
            preview_token=preview_token,
            action_id=action_id,
            request_id=request_id,
            session_id=session_id,
        )

        def apply(state, resolved):
            blocker, actor = self._authorize(state, blocker_id, session_id=session_id)
            try:
                result = self._apply(
                    state,
                    blocker_id=blocker.id,
                    preview_token=preview_token,
                    action_id=action_id,
                    actor=actor,
                    request_id=request_id,
                    dispatch_id=resolved.dispatch_id,
                    salvage=salvage,
                )
            except ValueError as exc:
                raise TeamCommandValidationError(
                    code="git_recovery_precondition_changed", message=str(exc)
                ) from exc
            from .commands.outbox import _team_event_outbox_records

            return TeamCommandApplyResult(
                response_payload=result, outbox_records=_team_event_outbox_records(result["events"])
            )

        return TeamCommandExecutor(self.repository)._execute_team_command(
            context=TeamCommandContext(
                ambient_session_id=session_id,
                tool_name="team_apply_git_recovery",
                request_id=request_id,
                progress_classification="git_recovery",
            ),
            normalized_payload={
                "blocker_id": blocker_id,
                "preview_token": preview_token,
                "action_id": action_id,
            },
            handled_inbox_item_ids=[],
            apply_command=apply,
            require_active_dispatch=True,
        )

    def apply_as_operator(self, *, blocker_id, preview_token, action_id, request_id, actor_id):
        salvage = self._capture_salvage(
            blocker_id=blocker_id,
            preview_token=preview_token,
            action_id=action_id,
            request_id=request_id,
        )

        def apply(state, result):
            blocker, _ = self._authorize(state, blocker_id)
            result.update(
                self._apply(
                    state,
                    blocker_id=blocker.id,
                    preview_token=preview_token,
                    action_id=action_id,
                    actor=None,
                    request_id=request_id,
                    salvage=salvage,
                )
            )

        return self.admin._run_idempotent_human_command(
            operation="git_recovery",
            caller_user_id=actor_id,
            scope_kind="blocker",
            scope_id=blocker_id,
            request_id=request_id,
            normalized_payload={"preview_token": preview_token, "action_id": action_id},
            apply_command=apply,
        )
