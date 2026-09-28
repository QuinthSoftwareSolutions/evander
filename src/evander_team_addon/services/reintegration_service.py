"""Contextual integration recovery using existing task and release domain rules."""

from __future__ import annotations

import hashlib
import json
import re

from ..models import (
    AgentTask,
    GitBaselineConstraints,
    ReintegrationPolicy,
    TaskDeliverableContract,
    TeamTaskKind,
    TeamTaskState,
    agent_has_project_assignment,
    project_lead_agent_id,
    utc_now,
)
from .domain.case_task import (
    _project_default_task_git_base_branch,
    _normalized_task_git_base_commit,
    _link_task_to_case,
)
from .domain.release_cancellation import (
    cancel_release,
    release_cancellation_error,
    _exclusive_correction_task_ids,
)
from .domain.work_lineage import ensure_release_work_lineage
from .domain.task_authorship import combined_deliverable_attributions, common_deliverable_author
from .domain.wake import _append_agent_wake_requested_event
from .domain.work_due import _mark_agent_work_due


class TeamReintegrationService:
    def __init__(self, admin_service):
        self.admin = admin_service
        self.repo = admin_service.repository

    def candidates_for_case(self, case_id: str) -> list[dict]:
        state = self.repo.read()
        case = state.cases[case_id]
        task_ids = set(case.task_ids) | {
            task.id for task in state.tasks.values() if task.case_id == case.id
        }
        replaced = {
            release.supersedes_release_package_id
            for release in state.release_packages.values()
            if release.state != "cancelled"
        }
        candidates = []
        for release in state.release_packages.values():
            if (
                release.project_id != case.project_id
                or release.state == "cancelled"
                or release.id in replaced
            ):
                continue
            inputs = [state.deliverables.get(source_id) for source_id in release.deliverable_ids]
            if not inputs or any(
                source is None or source.state != "accepted" or source.superseded_by_deliverable_id
                for source in inputs
            ):
                continue
            if not any(source.task_id in task_ids for source in inputs):
                continue
            candidates.append(
                {
                    "release_package_id": release.id,
                    "state": release.state,
                    "created_at": release.created_at.isoformat(),
                    "contribution_titles": [state.tasks[source.task_id].title for source in inputs],
                }
            )
        return sorted(
            candidates, key=lambda row: (row["created_at"], row["release_package_id"]), reverse=True
        )

    def preview(
        self, release_package_id: str, *, owner_agent_id: str | None = None, refresh: bool = True
    ) -> dict:
        state = self.repo.read()
        release = state.release_packages[release_package_id]
        project = state.projects[release.project_id]
        if not project.git:
            raise ValueError("project has no managed Git repository")
        if refresh:
            self.admin._managed_git_admin.refresh_repository(project.git.managed_repo_id)
            state = self.repo.read()
        return self._preview(state, release_package_id, owner_agent_id=owner_agent_id)

    def _preview(self, state, release_id, *, owner_agent_id=None):
        release = state.release_packages[release_id]
        project = state.projects[release.project_id]
        policy = (
            project.release_policy.reintegration
            if project.release_policy
            else ReintegrationPolicy()
        )
        if not policy.enabled or not project.git or project.status not in {"active", "blocked"}:
            raise ValueError("reintegration is unavailable for this project")
        branch = _project_default_task_git_base_branch(
            state,
            project_id=project.id,
            git_default_branch_resolver=self.admin._resolve_managed_git_default_branch,
        )
        commit = _normalized_task_git_base_commit(
            self.admin._resolve_managed_git_branch_commit(
                managed_repo_id=project.git.managed_repo_id,
                directory_path=project.git.directory_path,
                branch=branch,
            )
        )
        if not commit:
            raise ValueError("configured base has no resolved commit")
        source_ids = (
            sorted(row.deliverable_id for row in release.manifest.deliverables)
            if release.manifest
            else sorted(release.deliverable_ids)
        )
        contributions = []
        for source_id in source_ids:
            source = state.deliverables[source_id]
            if (
                source.state != "accepted"
                or source.superseded_by_deliverable_id
                or not source.git_refs
            ):
                raise ValueError(
                    "release inputs are no longer current accepted Git contributions; select their successor release"
                )
            task = state.tasks[source.task_id]
            if task.project_id != project.id:
                raise ValueError("release contribution belongs to another project")
            from .domain.git_recovery import verify_baseline_constraints
            verify_baseline_constraints(self.admin._managed_git_admin, project_git=project.git,
                constraints=task.git_constraints, base_commit_sha=commit)
            commits = {
                value.casefold()
                for value in (
                    source.git_refs.commit_sha,
                    source.git_refs.submitted_commit_sha,
                    source.git_refs.reviewed_commit_sha,
                )
                if value
            }
            if len(commits) != 1 or not re.fullmatch(
                r"(?:[0-9a-f]{40}|[0-9a-f]{64})", next(iter(commits), "")
            ):
                raise ValueError(
                    "accepted contribution requires one consistent full submitted/reviewed commit"
                )
            context = state.git_work_contexts.get(source.git_refs.branch_context_id or "")
            if not (source.git_refs.agent_branch or (context and context.agent_branch)):
                raise ValueError("accepted contribution has no source branch provenance")
            contributions.append(
                {
                    "deliverable_id": source.id,
                    "task_id": task.id,
                    "title": task.title,
                    "git_refs": source.git_refs.model_dump(mode="json"),
                }
            )
        if not contributions:
            raise ValueError("release has no accepted contributions")
        eligible = sorted(
            (
                agent
                for agent in state.agents.values()
                if agent.status == "active"
                and agent.team_id == project.team_id
                and agent_has_project_assignment(state, agent.id, project.id)
            ),
            key=lambda a: a.id,
        )
        selected_owner = (
            owner_agent_id or policy.owner_agent_id or project_lead_agent_id(state, project.id)
        )
        if selected_owner not in {agent.id for agent in eligible}:
            selected_owner = (
                eligible[0].id
                if len(eligible) == 1 and not owner_agent_id and not policy.owner_agent_id
                else None
            )
        if owner_agent_id and selected_owner is None:
            raise ValueError("selected owner is not an active project agent")
        retire = (
            policy.retire_source_release
            and release_cancellation_error(state, release) is None
            and release.state != "cancelled"
        )
        payload = {
            "revision": state.revision,
            "release_package_id": release.id,
            "project_id": project.id,
            "project_name": project.name,
            "base_branch": branch,
            "base_commit_sha": commit,
            "contributions": contributions,
            "owner_agent_id": selected_owner,
            "eligible_owners": [{"id": a.id, "name": a.display_name or a.id} for a in eligible],
            "instructions": policy.instructions,
            "retire_source_release": retire,
            "retiring_task_ids": sorted(
                task_id
                for task_id in _exclusive_correction_task_ids(state, release)
                if task_id in state.tasks
                and state.tasks[task_id].state not in {"accepted", "finished", "cancelled"}
            )
            if retire
            else [],
        }
        payload["preview_digest"] = hashlib.sha256(
            json.dumps(payload, sort_keys=True).encode()
        ).hexdigest()
        return payload

    def _submission_mutator(self, *, preview: dict, actor_id: str):
        release_id = preview["release_package_id"]

        def apply(state, result):
            current = self._preview(state, release_id, owner_agent_id=preview.get("owner_agent_id"))
            if current != preview:
                raise ValueError("reintegration preview changed; refresh and review it again")
            owner_id = current["owner_agent_id"]
            if not owner_id:
                raise ValueError("select an integration owner")
            contributions = current["contributions"]
            task_ids = sorted({row["task_id"] for row in contributions})
            source_ids = [row["deliverable_id"] for row in contributions]
            case_ids = {state.tasks[task_id].case_id for task_id in task_ids}
            case_id = next(iter(case_ids)) if len(case_ids) == 1 else None
            task_id = f"integration-{current['preview_digest'][:24]}"
            if task_id in state.tasks:
                result["task_id"] = task_id
                return
            now = utc_now()
            task = AgentTask(
                id=task_id,
                owner_agent_id=owner_id,
                project_id=current["project_id"],
                title=f"Reintegrate {current['project_name']} onto {current['base_branch']}",
                description=current["instructions"] + "\n\nSource release: " + release_id,
                task_kind=TeamTaskKind.SUPPORT,
                state=TeamTaskState.PENDING,
                priority=0,
                integration_boundary=True,
                dependency_task_ids=task_ids,
                originating_task_id=task_ids[0] if len(task_ids) == 1 else None,
                case_id=case_id,
                authored_by=common_deliverable_author(state, source_ids),
                work_item_attributions=combined_deliverable_attributions(state, source_ids),
                git_base_branch=current["base_branch"],
                git_base_commit_sha=current["base_commit_sha"],
                git_constraints=GitBaselineConstraints(
                    intent="required" if any(state.tasks[key].git_constraints.intent == "required" for key in task_ids) else "snapshot",
                    required_base_commit_sha=next((state.tasks[key].git_constraints.required_base_commit_sha for key in task_ids
                        if state.tasks[key].git_constraints.required_base_commit_sha), None),
                    excluded_ancestor_shas=sorted({sha for key in task_ids for sha in state.tasks[key].git_constraints.excluded_ancestor_shas})),
                created_by_actor=actor_id,
                created_at=now,
                updated_at=now,
                deliverable_contract=TaskDeliverableContract(
                    require_git_provenance=True,
                    required_contribution_task_ids=task_ids,
                    pinned_source_deliverable_ids=source_ids,
                ),
            )
            state.tasks[task.id] = task
            release = state.release_packages[release_id]
            task.work_lineage_id = release.work_lineage_id or ensure_release_work_lineage(
                state, release
            )
            if case_id is not None:
                _link_task_to_case(state, task_id=task.id, case_id=case_id, now=now)
            if current["retire_source_release"]:
                result["retirement"] = cancel_release(
                    state,
                    release=state.release_packages[release_id],
                    actor_id=actor_id,
                    reason=f"Reintegrating accepted work as {task.id}",
                )
            _mark_agent_work_due(state, agent_id=owner_id, now=now)
            _append_agent_wake_requested_event(
                state,
                agent_id=owner_id,
                result=result,
                reason="integration_requested",
                now=now,
                delay_seconds=0,
                idempotency_suffix=task.id,
            )
            result.update(task_id=task.id, source_release_package_id=release_id)

        return apply

    def submit(self, *, preview: dict, actor_id: str, request_id: str):
        return self.admin._run_idempotent_human_command(
            operation="reintegrate_release",
            caller_user_id=actor_id,
            scope_kind="release",
            scope_id=preview["release_package_id"],
            request_id=request_id,
            normalized_payload=preview,
            apply_command=self._submission_mutator(preview=preview, actor_id=actor_id),
            expected_state_revision=preview["revision"],
        )

    def submit_from_agent(self, *, preview: dict, session_id: str, request_id: str):
        from .commands.executor import TeamCommandExecutor
        from .commands.types import TeamCommandContext, TeamCommandApplyResult
        from .commands.validators import _require_command_agent
        from ..repository import TeamStateConflictError

        def apply(state, resolved):
            agent = _require_command_agent(resolved, "team_reintegrate_release")
            release = state.release_packages[preview["release_package_id"]]
            if project_lead_agent_id(state, release.project_id) != agent.id:
                raise ValueError("only the configured project lead may submit agent reintegration")
            if state.revision != preview["revision"]:
                raise TeamStateConflictError(
                    expected_revision=preview["revision"], current_revision=state.revision
                )
            result = {"events": []}
            self._submission_mutator(preview=preview, actor_id=f"agent:{agent.id}")(state, result)
            return TeamCommandApplyResult(
                response_payload=result, created_domain_ids=[result["task_id"]]
            )

        return TeamCommandExecutor(self.repo)._execute_team_command(
            context=TeamCommandContext(
                ambient_session_id=session_id,
                tool_name="team_reintegrate_release",
                request_id=request_id,
                progress_classification="progress",
            ),
            normalized_payload={"project_id": preview["project_id"], "preview": preview},
            handled_inbox_item_ids=[],
            apply_command=apply,
            require_active_dispatch=True,
        )
