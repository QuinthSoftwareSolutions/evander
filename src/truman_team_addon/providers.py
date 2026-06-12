from __future__ import annotations

import json
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from agent_sandbox.effects.providers.base import EffectProviderBase
from agent_sandbox.events.internal import InternalEventBus, InternalEventSource, internal_event_store_path
from agent_sandbox.events.models import WorkflowNodeEnvelope
from agent_sandbox.queries.providers.base import QueryProviderBase
from agent_sandbox.workflow.contracts import register_node_output_schema
from agent_sandbox.workflow.plugins import ProviderPluginRegistration

from .git_naming import validate_branch_name
from .models import team_state_payload
from .provider_schemas import (
    TEAM_OUTPUT_SCHEMAS,
    TeamAgentActionArtifactQueryDefinition,
    TeamAcceptEphemeralDeliverableEffectDefinition,
    TeamApplyAgentActionEffectDefinition,
    TeamApplyEphemeralActionEffectDefinition,
    TeamConsolidateGitBranchesEffectDefinition,
    TeamMarkEphemeralCleanedUpEffectDefinition,
    TeamMarkEphemeralSpawnedEffectDefinition,
    TeamMarkReleasePublishedEffectDefinition,
    TeamRecordGitPromotionEffectDefinition,
    TeamRequestEphemeralAgentsEffectDefinition,
    TeamStateQueryDefinition,
    TeamSessionRoutingQueryDefinition,
    TeamWakeDueAgentsEffectDefinition,
)
from .repository import TeamStateConflictError, TeamStateRepository

DEFAULT_ACTION_ARTIFACT_PATH = "team-action.json"


class TeamWakeDueAgentsEffectProvider(EffectProviderBase):
    @classmethod
    def validate_definition(cls, effect_definition):
        if isinstance(effect_definition, WorkflowNodeEnvelope):
            return _materialize(TeamWakeDueAgentsEffectDefinition, effect_definition)
        return effect_definition

    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = _definition_payload(
            self.effect_definition,
            "team_id",
            "project_id",
            "agent_id",
            "limit",
            "lease_seconds",
            "wake_interval_seconds",
            "max_no_progress_count",
            "action_artifact_path",
            "resume_mode",
        )
        if resolved_config:
            payload.update(dict(resolved_config))
        context = self.runtime_context
        resumer = getattr(context, "agent_resumer", None)
        if resumer is None or not callable(getattr(resumer, "resume_agent", None)):
            raise RuntimeError("effect:team:wake-due-agents requires agent_resumer")

        repo = TeamStateRepository(_runtime_dir(context))
        state, leases = repo.lease_due_agents(
            team_id=_optional_text(payload.get("team_id")),
            project_id=_optional_text(payload.get("project_id")),
            agent_id=_optional_text(payload.get("agent_id")),
            limit=_optional_int(payload.get("limit"), default=10),
            lease_seconds=_optional_int(payload.get("lease_seconds"), default=900),
            wake_interval_seconds=_optional_int(payload.get("wake_interval_seconds"), default=900),
            max_no_progress_count=_optional_int(
                payload.get("max_no_progress_count"),
                default=3,
            ),
        )
        action_path = _optional_text(payload.get("action_artifact_path")) or DEFAULT_ACTION_ARTIFACT_PATH
        resume_mode = _optional_text(payload.get("resume_mode")) or "same_context"
        dispatches: list[dict[str, Any]] = []
        errors: list[str] = []
        for lease in leases:
            agent = lease["agent"]
            prompt = _build_prompt(lease, action_artifact_path=action_path)
            try:
                result = resumer.resume_agent(
                    target=agent["session_id"],
                    prompt=prompt,
                    resume_mode=resume_mode,
                    dispatch_id=lease["dispatch_id"],
                )
                result_payload = _coerce_mapping(result)
                if _result_reports_failure(result_payload):
                    error = _resume_failure_message(result_payload)
                    state = _record_wake_failure(repo, lease=lease, error=error)
                    errors.append(f"{agent['id']}: {error}")
                    continue
                dispatches.append(
                    {
                        "agent_id": agent["id"],
                        "session_id": agent["session_id"],
                        "dispatch_id": lease["dispatch_id"],
                        "result": result_payload,
                    }
                )
                _mirror_session_metadata(
                    context,
                    repo=repo,
                    state=state,
                    agent_id=agent["id"],
                    session_id=agent["session_id"],
                )
            except Exception as exc:  # pragma: no cover - exercised by provider tests with fakes
                error = str(exc)
                try:
                    state = _record_wake_failure(repo, lease=lease, error=error)
                except Exception as release_exc:  # pragma: no cover - defensive diagnostics
                    error = f"{error}; failed to release team runtime lease: {release_exc}"
                errors.append(f"{agent['id']}: {error}")
        return {
            "success": not errors,
            "revision": state.revision,
            "leased_count": len(leases),
            "leases": leases,
            "dispatches": dispatches,
            "errors": errors,
        }


class TeamApplyAgentActionEffectProvider(EffectProviderBase):
    @classmethod
    def validate_definition(cls, effect_definition):
        if isinstance(effect_definition, WorkflowNodeEnvelope):
            return _materialize(TeamApplyAgentActionEffectDefinition, effect_definition)
        return effect_definition

    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = _definition_payload(
            self.effect_definition,
            "action",
            "valid",
            "error",
            "session_id",
            "dispatch_id",
            "expected_revision",
        )
        if resolved_config:
            payload.update(dict(resolved_config))
        repo = TeamStateRepository(_runtime_dir(self.runtime_context))
        try:
            state, result = repo.apply_agent_action(
                action=_optional_mapping(payload.get("action"), "action"),
                session_id=_optional_text(payload.get("session_id")),
                dispatch_id=_optional_text(payload.get("dispatch_id")),
                valid=_optional_bool(payload.get("valid"), default=True),
                error=_optional_text(payload.get("error")),
                expected_revision=_optional_int(payload.get("expected_revision"), default=None),
            )
        except TeamStateConflictError as exc:
            return {
                "success": False,
                "error": str(exc),
                "expected_revision": exc.expected_revision,
                "current_revision": exc.current_revision,
            }
        agent_id = result.get("agent_id")
        if isinstance(agent_id, str) and agent_id in state.agents:
            _mirror_session_metadata(
                self.runtime_context,
                repo=repo,
                state=state,
                agent_id=agent_id,
                session_id=state.agents[agent_id].session_id,
            )
        published_event_ids = _publish_team_events(
            self.runtime_context,
            result.get("ephemeral_events"),
        )
        published_event_ids += _publish_team_events(
            self.runtime_context,
            result.get("events"),
        )
        if published_event_ids:
            result["published_event_ids"] = published_event_ids
        return {**result, "state": team_state_payload(state)}


class TeamRequestEphemeralAgentsEffectProvider(EffectProviderBase):
    @classmethod
    def validate_definition(cls, effect_definition):
        if isinstance(effect_definition, WorkflowNodeEnvelope):
            return _materialize(TeamRequestEphemeralAgentsEffectDefinition, effect_definition)
        return effect_definition

    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = _definition_payload(
            self.effect_definition,
            "parent_agent_id",
            "requests",
            "dispatch_id",
            "expected_revision",
        )
        if resolved_config:
            payload.update(dict(resolved_config))
        repo = TeamStateRepository(_runtime_dir(self.runtime_context))
        try:
            _state, result = repo.request_ephemeral_assignments(
                parent_agent_id=_required_text(payload.get("parent_agent_id"), "parent_agent_id"),
                requests=_list_of_mappings(payload.get("requests"), "requests"),
                dispatch_id=_optional_text(payload.get("dispatch_id")),
                expected_revision=_optional_int(payload.get("expected_revision"), default=None),
            )
        except TeamStateConflictError as exc:
            return {
                "success": False,
                "error": str(exc),
                "expected_revision": exc.expected_revision,
                "current_revision": exc.current_revision,
            }
        published_event_ids = _publish_team_events(self.runtime_context, result.get("events"))
        if published_event_ids:
            result["published_event_ids"] = published_event_ids
        return result


class TeamMarkEphemeralSpawnedEffectProvider(EffectProviderBase):
    @classmethod
    def validate_definition(cls, effect_definition):
        if isinstance(effect_definition, WorkflowNodeEnvelope):
            return _materialize(TeamMarkEphemeralSpawnedEffectDefinition, effect_definition)
        return effect_definition

    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = _definition_payload(
            self.effect_definition,
            "assignment_id",
            "session_id",
            "create_result",
            "expected_revision",
        )
        if resolved_config:
            payload.update(dict(resolved_config))
        repo = TeamStateRepository(_runtime_dir(self.runtime_context))
        try:
            _state, result = repo.mark_ephemeral_spawned(
                assignment_id=_required_text(payload.get("assignment_id"), "assignment_id"),
                session_id=_optional_text(payload.get("session_id")),
                create_result=_optional_mapping(payload.get("create_result"), "create_result"),
                expected_revision=_optional_int(payload.get("expected_revision"), default=None),
            )
        except TeamStateConflictError as exc:
            return {
                "success": False,
                "error": str(exc),
                "expected_revision": exc.expected_revision,
                "current_revision": exc.current_revision,
            }
        return result


class TeamApplyEphemeralActionEffectProvider(EffectProviderBase):
    @classmethod
    def validate_definition(cls, effect_definition):
        if isinstance(effect_definition, WorkflowNodeEnvelope):
            return _materialize(TeamApplyEphemeralActionEffectDefinition, effect_definition)
        return effect_definition

    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = _definition_payload(
            self.effect_definition,
            "action",
            "valid",
            "error",
            "session_id",
            "expected_revision",
        )
        if resolved_config:
            payload.update(dict(resolved_config))
        repo = TeamStateRepository(_runtime_dir(self.runtime_context))
        try:
            _state, result = repo.apply_ephemeral_action(
                action=_optional_mapping(payload.get("action"), "action"),
                session_id=_optional_text(payload.get("session_id")),
                valid=_optional_bool(payload.get("valid"), default=True),
                error=_optional_text(payload.get("error")),
                expected_revision=_optional_int(payload.get("expected_revision"), default=None),
            )
        except TeamStateConflictError as exc:
            return {
                "success": False,
                "error": str(exc),
                "expected_revision": exc.expected_revision,
                "current_revision": exc.current_revision,
            }
        published_event_ids = _publish_team_events(self.runtime_context, [result.get("event")])
        if published_event_ids:
            result["published_event_ids"] = published_event_ids
        return result


class TeamAcceptEphemeralDeliverableEffectProvider(EffectProviderBase):
    @classmethod
    def validate_definition(cls, effect_definition):
        if isinstance(effect_definition, WorkflowNodeEnvelope):
            return _materialize(TeamAcceptEphemeralDeliverableEffectDefinition, effect_definition)
        return effect_definition

    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = _definition_payload(
            self.effect_definition,
            "assignment_id",
            "accepted",
            "deliverable_id",
            "rejection_reason",
            "expected_revision",
        )
        if resolved_config:
            payload.update(dict(resolved_config))
        repo = TeamStateRepository(_runtime_dir(self.runtime_context))
        try:
            _state, result = repo.accept_ephemeral_deliverable(
                assignment_id=_required_text(payload.get("assignment_id"), "assignment_id"),
                accepted=_optional_bool(payload.get("accepted"), default=True),
                deliverable_id=_optional_text(payload.get("deliverable_id")),
                rejection_reason=_optional_text(payload.get("rejection_reason")),
                expected_revision=_optional_int(payload.get("expected_revision"), default=None),
            )
        except TeamStateConflictError as exc:
            return {
                "success": False,
                "error": str(exc),
                "expected_revision": exc.expected_revision,
                "current_revision": exc.current_revision,
            }
        return result


class TeamMarkEphemeralCleanedUpEffectProvider(EffectProviderBase):
    @classmethod
    def validate_definition(cls, effect_definition):
        if isinstance(effect_definition, WorkflowNodeEnvelope):
            return _materialize(TeamMarkEphemeralCleanedUpEffectDefinition, effect_definition)
        return effect_definition

    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = _definition_payload(
            self.effect_definition,
            "assignment_id",
            "session_id",
            "destroy_result",
            "expected_revision",
        )
        if resolved_config:
            payload.update(dict(resolved_config))
        repo = TeamStateRepository(_runtime_dir(self.runtime_context))
        try:
            _state, result = repo.mark_ephemeral_cleaned_up(
                assignment_id=_optional_text(payload.get("assignment_id")),
                session_id=_optional_text(payload.get("session_id")),
                destroy_result=_optional_mapping(payload.get("destroy_result"), "destroy_result"),
                expected_revision=_optional_int(payload.get("expected_revision"), default=None),
            )
        except TeamStateConflictError as exc:
            return {
                "success": False,
                "error": str(exc),
                "expected_revision": exc.expected_revision,
                "current_revision": exc.current_revision,
            }
        return result


class TeamRecordGitPromotionEffectProvider(EffectProviderBase):
    @classmethod
    def validate_definition(cls, effect_definition):
        if isinstance(effect_definition, WorkflowNodeEnvelope):
            return _materialize(TeamRecordGitPromotionEffectDefinition, effect_definition)
        return effect_definition

    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = _definition_payload(
            self.effect_definition,
            "promotion_id",
            "success",
            "outputs",
            "error",
            "commit_sha",
            "expected_revision",
        )
        if resolved_config:
            payload.update(dict(resolved_config))
        repo = TeamStateRepository(_runtime_dir(self.runtime_context))
        try:
            _state, result = repo.record_git_promotion_result(
                promotion_id=_required_text(payload.get("promotion_id"), "promotion_id"),
                success=_optional_bool(payload.get("success"), default=True),
                outputs=_optional_mapping(payload.get("outputs"), "outputs") or {},
                error=_optional_text(payload.get("error")),
                commit_sha=_optional_text(payload.get("commit_sha")),
                expected_revision=_optional_int(payload.get("expected_revision"), default=None),
            )
        except TeamStateConflictError as exc:
            return {
                "success": False,
                "error": str(exc),
                "expected_revision": exc.expected_revision,
                "current_revision": exc.current_revision,
            }
        published_event_ids = _publish_team_events(
            self.runtime_context,
            result.get("events"),
        )
        if published_event_ids:
            result["published_event_ids"] = published_event_ids
        return result


class TeamMarkReleasePublishedEffectProvider(EffectProviderBase):
    @classmethod
    def validate_definition(cls, effect_definition):
        if isinstance(effect_definition, WorkflowNodeEnvelope):
            return _materialize(TeamMarkReleasePublishedEffectDefinition, effect_definition)
        return effect_definition

    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = _definition_payload(
            self.effect_definition,
            "release_package_id",
            "success",
            "canonical_branch",
            "published_commit_sha",
            "outputs",
            "error",
            "expected_revision",
        )
        if resolved_config:
            payload.update(dict(resolved_config))
        repo = TeamStateRepository(_runtime_dir(self.runtime_context))
        try:
            _state, result = repo.mark_release_published(
                release_package_id=_required_text(
                    payload.get("release_package_id"), "release_package_id"
                ),
                success=_optional_bool(payload.get("success"), default=True),
                canonical_branch=_optional_text(payload.get("canonical_branch")),
                published_commit_sha=_optional_text(payload.get("published_commit_sha")),
                outputs=_optional_mapping(payload.get("outputs"), "outputs") or {},
                error=_optional_text(payload.get("error")),
                expected_revision=_optional_int(payload.get("expected_revision"), default=None),
            )
        except TeamStateConflictError as exc:
            return {
                "success": False,
                "error": str(exc),
                "expected_revision": exc.expected_revision,
                "current_revision": exc.current_revision,
            }
        return result


class TeamConsolidateGitBranchesEffectProvider(EffectProviderBase):
    @classmethod
    def validate_definition(cls, effect_definition):
        if isinstance(effect_definition, WorkflowNodeEnvelope):
            return _materialize(TeamConsolidateGitBranchesEffectDefinition, effect_definition)
        return effect_definition

    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = _definition_payload(
            self.effect_definition,
            "host_repo_path",
            "source_branches",
            "target_branch",
            "base_branch",
        )
        if resolved_config:
            payload.update(dict(resolved_config))
        host_repo_path = _required_text(payload.get("host_repo_path"), "host_repo_path")
        try:
            source_branches = [
                validate_branch_name(branch)
                for branch in _string_list(payload.get("source_branches"), "source_branches")
            ]
            target_branch = validate_branch_name(
                _required_text(payload.get("target_branch"), "target_branch")
            )
            base_value = _optional_text(payload.get("base_branch"))
            base_branch = validate_branch_name(base_value) if base_value else None
        except ValueError as exc:
            return _consolidation_failure(None, str(exc))
        if not source_branches:
            return _consolidation_failure(target_branch, "source_branches is required")
        return _consolidate_git_branches(
            host_repo_path=host_repo_path,
            source_branches=source_branches,
            target_branch=target_branch,
            base_branch=base_branch,
        )


class TeamAgentActionArtifactQueryProvider(QueryProviderBase):
    @classmethod
    def validate_definition(cls, query_definition):
        if isinstance(query_definition, WorkflowNodeEnvelope):
            return _materialize(TeamAgentActionArtifactQueryDefinition, query_definition)
        return query_definition

    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = _definition_payload(self.query_definition, "session_id", "path", "vm_user")
        if resolved_config:
            payload.update(dict(resolved_config))
        session_id = _required_text(payload.get("session_id"), "session_id")
        path = _optional_text(payload.get("path")) or DEFAULT_ACTION_ARTIFACT_PATH
        vm_user = _optional_text(payload.get("vm_user")) or "agent"
        reader = getattr(self.runtime_context, "session_artifact_reader", None)
        read_fn = getattr(reader, "read_session_artifact_text", None)
        if not callable(read_fn):
            raise RuntimeError("query:team:agent-action-artifact requires session_artifact_reader")
        try:
            result = read_fn(session_id=session_id, path=path, vm_user=vm_user)
            content = result.get("content") if isinstance(result, Mapping) else result
            action = json.loads(str(content or ""))
            if not isinstance(action, dict):
                raise ValueError("action artifact must contain a JSON object")
            return {
                "success": True,
                "valid": True,
                "session_id": session_id,
                "path": path,
                "action": action,
                "error": None,
            }
        except Exception as exc:
            return {
                "success": False,
                "valid": False,
                "session_id": session_id,
                "path": path,
                "action": {},
                "error": str(exc),
            }


class TeamStateQueryProvider(QueryProviderBase):
    @classmethod
    def validate_definition(cls, query_definition):
        if isinstance(query_definition, WorkflowNodeEnvelope):
            return _materialize(TeamStateQueryDefinition, query_definition)
        return query_definition

    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = _definition_payload(
            self.query_definition,
            "team_id",
            "project_id",
            "agent_id",
            "include_records",
        )
        if resolved_config:
            payload.update(dict(resolved_config))
        state = TeamStateRepository(_runtime_dir(self.runtime_context)).read()
        team_id = _optional_text(payload.get("team_id"))
        project_id = _optional_text(payload.get("project_id"))
        agent_id = _optional_text(payload.get("agent_id"))
        filtered = _filtered_team_state_payload(
            state,
            team_id=team_id,
            project_id=project_id,
            agent_id=agent_id,
        )
        return {
            "success": True,
            "revision": state.revision,
            "state": filtered,
            "teams": filtered["teams"],
            "projects": filtered["projects"],
            "agents": filtered["agents"],
            "tasks": filtered["tasks"],
        }


class TeamSessionRoutingQueryProvider(QueryProviderBase):
    @classmethod
    def validate_definition(cls, query_definition):
        if isinstance(query_definition, WorkflowNodeEnvelope):
            return _materialize(TeamSessionRoutingQueryDefinition, query_definition)
        return query_definition

    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = _definition_payload(self.query_definition, "session_id")
        if resolved_config:
            payload.update(dict(resolved_config))
        session_id = _required_text(payload.get("session_id"), "session_id")
        state = TeamStateRepository(_runtime_dir(self.runtime_context)).read()
        for agent in state.agents.values():
            if agent.session_id == session_id:
                return {
                    "success": True,
                    "session_id": session_id,
                    "kind": "persistent",
                    "agent_id": agent.id,
                    "assignment_id": None,
                    "persistent": True,
                    "ephemeral": False,
                }
        for assignment in state.ephemeral_assignments.values():
            if assignment.session_id == session_id:
                return {
                    "success": True,
                    "session_id": session_id,
                    "kind": "ephemeral",
                    "agent_id": None,
                    "assignment_id": assignment.id,
                    "persistent": False,
                    "ephemeral": True,
                }
        return {
            "success": False,
            "session_id": session_id,
            "kind": "unknown",
            "agent_id": None,
            "assignment_id": None,
            "persistent": False,
            "ephemeral": False,
        }


def register_providers() -> ProviderPluginRegistration:
    for node_type, outputs in TEAM_OUTPUT_SCHEMAS.items():
        register_node_output_schema(node_type=node_type, outputs=outputs)
    return ProviderPluginRegistration(
        effect_providers={
            "effect:team:wake-due-agents": TeamWakeDueAgentsEffectProvider,
            "effect:team:apply-agent-action": TeamApplyAgentActionEffectProvider,
            "effect:team:request-ephemeral-agents": TeamRequestEphemeralAgentsEffectProvider,
            "effect:team:mark-ephemeral-spawned": TeamMarkEphemeralSpawnedEffectProvider,
            "effect:team:apply-ephemeral-action": TeamApplyEphemeralActionEffectProvider,
            "effect:team:accept-ephemeral-deliverable": (
                TeamAcceptEphemeralDeliverableEffectProvider
            ),
            "effect:team:mark-ephemeral-cleaned-up": (
                TeamMarkEphemeralCleanedUpEffectProvider
            ),
            "effect:team:record-git-promotion": TeamRecordGitPromotionEffectProvider,
            "effect:team:mark-release-published": TeamMarkReleasePublishedEffectProvider,
            "effect:team:consolidate-git-branches": (
                TeamConsolidateGitBranchesEffectProvider
            ),
        },
        query_providers={
            "query:team:agent-action-artifact": TeamAgentActionArtifactQueryProvider,
            "query:team:state": TeamStateQueryProvider,
            "query:team:session-routing": TeamSessionRoutingQueryProvider,
        },
        node_definition_resolvers={
            "effect:team:wake-due-agents": (
                lambda node: _materialize(TeamWakeDueAgentsEffectDefinition, node)
            ),
            "effect:team:apply-agent-action": (
                lambda node: _materialize(TeamApplyAgentActionEffectDefinition, node)
            ),
            "effect:team:request-ephemeral-agents": (
                lambda node: _materialize(TeamRequestEphemeralAgentsEffectDefinition, node)
            ),
            "effect:team:mark-ephemeral-spawned": (
                lambda node: _materialize(TeamMarkEphemeralSpawnedEffectDefinition, node)
            ),
            "effect:team:apply-ephemeral-action": (
                lambda node: _materialize(TeamApplyEphemeralActionEffectDefinition, node)
            ),
            "effect:team:accept-ephemeral-deliverable": (
                lambda node: _materialize(TeamAcceptEphemeralDeliverableEffectDefinition, node)
            ),
            "effect:team:mark-ephemeral-cleaned-up": (
                lambda node: _materialize(TeamMarkEphemeralCleanedUpEffectDefinition, node)
            ),
            "effect:team:record-git-promotion": (
                lambda node: _materialize(TeamRecordGitPromotionEffectDefinition, node)
            ),
            "effect:team:mark-release-published": (
                lambda node: _materialize(TeamMarkReleasePublishedEffectDefinition, node)
            ),
            "effect:team:consolidate-git-branches": (
                lambda node: _materialize(TeamConsolidateGitBranchesEffectDefinition, node)
            ),
            "query:team:agent-action-artifact": (
                lambda node: _materialize(TeamAgentActionArtifactQueryDefinition, node)
            ),
            "query:team:state": (lambda node: _materialize(TeamStateQueryDefinition, node)),
            "query:team:session-routing": (
                lambda node: _materialize(TeamSessionRoutingQueryDefinition, node)
            ),
        },
    )


def _build_prompt(lease: Mapping[str, Any], *, action_artifact_path: str) -> str:
    agent = lease["agent"]
    context = {
        "agent": agent,
        "dispatch_id": lease["dispatch_id"],
        "reason": lease["reason"],
        "superior_agent_id": lease["superior_agent_id"],
        "subordinate_agent_ids": lease["subordinate_agent_ids"],
        "tasks": lease["tasks"],
        "inbox": lease["inbox"],
        "action_artifact_path": f"/artifacts/{action_artifact_path}",
    }
    schema = {
        "agent_id": agent["id"],
        "dispatch_id": lease["dispatch_id"],
        "task_updates": [],
        "task_creations": [],
        "status_requests": [],
        "questions": [],
        "status_reports": [],
        "deliverables": [],
        "review_requests": [],
        "review_decisions": [],
        "goal_updates": [],
        "operator_escalations": [],
        "git_review_promotions": [],
        "git_feature_updates": [],
        "git_release_requests": [],
        "ephemeral_requests": [],
        "ephemeral_acceptances": [],
        "handled_inbox_item_ids": [],
        "notes": "",
    }
    return (
        "Continue your assigned team-agent work.\n"
        "Use the provided team context and update only your own assigned tasks. "
        "Questions and progress reports must go to your direct superior; top-level "
        "managers use operator_escalations. Use task_creations only for yourself, "
        "subordinates, or project/team scopes you lead. Use status_requests only "
        "for direct subordinates. Use review_requests/review_decisions for deliverable "
        "review, goal_updates for project goals, git_* actions for review/feature/release "
        "branch promotion, and ephemeral_* actions for scoped worker delegation.\n\n"
        "Each task carries derived graph context: dependency_status, child_task_ids, "
        "blocked_by_task_ids, and subtask_counts. Do not start tasks whose "
        "dependency_status is not 'satisfied'; wait until their blocked_by_task_ids "
        "clear. Decompose large work by creating child tasks via task_creations with "
        "parent_task_id instead of ad hoc checklists in descriptions, and declare "
        "ordering between tasks with dependency_task_ids.\n\n"
        f"Team context JSON:\n{json.dumps(context, sort_keys=True, indent=2)}\n\n"
        "Before finishing, write a UTF-8 JSON action object to "
        f"/artifacts/{action_artifact_path}. Use this shape:\n"
        f"{json.dumps(schema, sort_keys=True, indent=2)}\n"
    )


def _mirror_session_metadata(
    context: Any,
    *,
    repo: TeamStateRepository,
    state: Any,
    agent_id: str,
    session_id: str,
) -> None:
    updater = getattr(context, "session_metadata_updater", None)
    update_fn = getattr(updater, "session_update_metadata", None)
    if not callable(update_fn):
        return
    update_fn(
        target=session_id,
        metadata=repo.mirror_session_metadata_payload(state=state, agent_id=agent_id),
    )


def _record_wake_failure(
    repo: TeamStateRepository,
    *,
    lease: Mapping[str, Any],
    error: str,
) -> Any:
    state, _result = repo.apply_agent_action(
        action={},
        session_id=_required_text(lease["agent"].get("session_id"), "session_id"),
        dispatch_id=_required_text(lease.get("dispatch_id"), "dispatch_id"),
        valid=False,
        error=error,
    )
    return state


def _publish_team_events(context: Any, events: Any) -> list[str]:
    if not isinstance(events, list):
        return []
    bus = _internal_event_bus(context)
    published_ids: list[str] = []
    for event in events:
        if not isinstance(event, Mapping):
            continue
        payload = event.get("payload")
        if not isinstance(payload, Mapping):
            payload = {}
        published = bus.publish(
            event_type=_required_text(event.get("event_type"), "event_type"),
            payload=dict(payload),
            correlation_key=_optional_text(event.get("correlation_key")),
            idempotency_key=_optional_text(event.get("idempotency_key")),
            source=InternalEventSource(source_type="team-addon", source_name="team-addon"),
        )
        published_ids.append(published.event_id)
    return published_ids


def _internal_event_bus(context: Any) -> InternalEventBus:
    runtime_dir = _runtime_dir(context)
    metadata = getattr(context, "metadata", {})
    event_path = None
    journal_path = None
    if isinstance(metadata, Mapping):
        event_path = metadata.get("internal_event_store_path")
        journal_path = metadata.get("workflow_event_journal_path")
    return InternalEventBus(
        Path(event_path) if event_path else internal_event_store_path(runtime_dir),
        journal_path=Path(journal_path) if journal_path else None,
    )


def _materialize(model_class: type[Any], node: WorkflowNodeEnvelope) -> Any:
    return model_class.model_validate(
        {
            "id": node.id,
            "display_name": node.display_name,
            "type": node.type,
            "outputs": node.outputs,
            **dict(node.config),
        }
    )


def _definition_payload(definition: Any, *field_names: str) -> dict[str, Any]:
    return {
        field_name: getattr(definition, field_name)
        for field_name in field_names
        if definition is not None and hasattr(definition, field_name)
    }


def _runtime_dir(context: Any) -> Path:
    resolved = getattr(context, "resolved_config", None)
    runtime_dir = getattr(resolved, "runtime_dir", None)
    if runtime_dir is None:
        raise RuntimeError("team add-on provider requires resolved_config.runtime_dir")
    return Path(runtime_dir)


def _coerce_mapping(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, Mapping):
        return dict(value)
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        dumped = model_dump(mode="json")
        if isinstance(dumped, Mapping):
            return dict(dumped)
    if hasattr(value, "__dict__"):
        return dict(vars(value))
    return {"result": value}


def _result_reports_failure(result: Mapping[str, Any]) -> bool:
    if "success" not in result:
        return False
    return not _truthy_result_success(result.get("success"))


def _truthy_result_success(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, int | float):
        return value != 0
    text = str(value).strip().lower()
    if text in {"", "0", "false", "no", "off"}:
        return False
    return True


def _resume_failure_message(result: Mapping[str, Any]) -> str:
    return (
        _optional_text(result.get("message"))
        or _optional_text(result.get("error"))
        or "resume_agent failed"
    )


def _filtered_team_state_payload(
    state: Any,
    *,
    team_id: str | None,
    project_id: str | None,
    agent_id: str | None,
) -> dict[str, Any]:
    filtered = team_state_payload(state)
    teams = filtered["teams"]
    projects = filtered["projects"]
    agents = filtered["agents"]

    visible_team_ids = set(teams)
    visible_project_ids = set(projects)
    visible_agent_ids = set(agents)

    if team_id is not None:
        visible_team_ids &= {team_id}
        visible_project_ids = {
            key for key, value in projects.items() if value.get("team_id") in visible_team_ids
        }
        visible_agent_ids = {
            key for key, value in agents.items() if value.get("team_id") in visible_team_ids
        }

    if project_id is not None:
        if project_id in visible_project_ids and project_id in projects:
            visible_project_ids = {project_id}
            visible_team_ids &= {_required_text(projects[project_id].get("team_id"), "team_id")}
            visible_agent_ids = {
                key
                for key in visible_agent_ids
                if agents[key].get("project_id") == project_id
            }
        else:
            visible_team_ids = set()
            visible_project_ids = set()
            visible_agent_ids = set()

    if agent_id is not None:
        if agent_id in visible_agent_ids and agent_id in agents:
            agent = agents[agent_id]
            visible_agent_ids = {agent_id}
            visible_team_ids &= {_required_text(agent.get("team_id"), "team_id")}
            agent_project_id = _optional_text(agent.get("project_id"))
            visible_project_ids = (
                visible_project_ids & {agent_project_id}
                if agent_project_id is not None
                else set()
            )
        else:
            visible_team_ids = set()
            visible_project_ids = set()
            visible_agent_ids = set()

    filtered["teams"] = {
        key: {**value, "project_ids": _filter_ids(value.get("project_ids"), visible_project_ids)}
        for key, value in teams.items()
        if key in visible_team_ids
    }
    filtered["projects"] = {
        key: value for key, value in projects.items() if key in visible_project_ids
    }
    filtered["agents"] = {
        key: {
            **value,
            "reports_to_agent_id": value.get("reports_to_agent_id")
            if value.get("reports_to_agent_id") in visible_agent_ids
            else None,
            "subordinate_agent_ids": _filter_ids(
                value.get("subordinate_agent_ids"),
                visible_agent_ids,
            ),
        }
        for key, value in agents.items()
        if key in visible_agent_ids
    }
    filtered["agent_runtimes"] = {
        key: value
        for key, value in filtered.get("agent_runtimes", {}).items()
        if key in visible_agent_ids
    }
    filtered["tasks"] = {
        key: value
        for key, value in filtered["tasks"].items()
        if value.get("project_id") in visible_project_ids
        and value.get("owner_agent_id") in visible_agent_ids
    }
    visible_task_ids = set(filtered["tasks"])
    filtered["deliverables"] = {
        key: value
        for key, value in filtered.get("deliverables", {}).items()
        if value.get("task_id") in visible_task_ids
        and value.get("producer_agent_id") in visible_agent_ids
    }
    visible_deliverable_ids = set(filtered["deliverables"])
    filtered["review_requests"] = {
        key: value
        for key, value in filtered.get("review_requests", {}).items()
        if value.get("deliverable_id") in visible_deliverable_ids
        and value.get("reviewer_agent_id") in visible_agent_ids
    }
    visible_review_ids = set(filtered["review_requests"])
    filtered["deliverables"] = {
        key: {
            **value,
            "review_request_ids": _filter_ids(value.get("review_request_ids"), visible_review_ids),
        }
        for key, value in filtered["deliverables"].items()
    }
    filtered["tasks"] = {
        key: {
            **value,
            "deliverable_ids": _filter_ids(value.get("deliverable_ids"), visible_deliverable_ids),
            # Keep the graph self-consistent within the scoped view: drop edges that
            # point at tasks the caller cannot see. dependency_status and
            # subtask_counts are intentionally left as computed from the full state
            # so they reflect global truth even when some edges are hidden here.
            "parent_task_id": value.get("parent_task_id")
            if value.get("parent_task_id") in visible_task_ids
            else None,
            "dependency_task_ids": _filter_ids(
                value.get("dependency_task_ids"), visible_task_ids
            ),
            "child_task_ids": _filter_ids(value.get("child_task_ids"), visible_task_ids),
            "blocked_by_task_ids": _filter_ids(
                value.get("blocked_by_task_ids"), visible_task_ids
            ),
        }
        for key, value in filtered["tasks"].items()
    }
    filtered["inbox"] = {
        key: value
        for key, value in filtered.get("inbox", {}).items()
        if (
            value.get("recipient_agent_id") in visible_agent_ids
            or value.get("sender_agent_id") in visible_agent_ids
        )
        and (value.get("task_id") is None or value.get("task_id") in visible_task_ids)
    }
    filtered["action_records"] = {
        key: value
        for key, value in filtered.get("action_records", {}).items()
        if value.get("agent_id") in visible_agent_ids
    }
    filtered["release_packages"] = {
        key: {
            **value,
            "deliverable_ids": _filter_ids(value.get("deliverable_ids"), visible_deliverable_ids),
        }
        for key, value in filtered.get("release_packages", {}).items()
        if value.get("project_id") in visible_project_ids
        and all(
            deliverable_id in visible_deliverable_ids
            for deliverable_id in _string_items(value.get("deliverable_ids"))
        )
    }
    visible_release_ids = set(filtered["release_packages"])
    filtered["git_promotions"] = {
        key: value
        for key, value in filtered.get("git_promotions", {}).items()
        if value.get("project_id") in visible_project_ids
        and (
            value.get("task_id") is None
            or value.get("task_id") in visible_task_ids
        )
        and (
            value.get("deliverable_id") is None
            or value.get("deliverable_id") in visible_deliverable_ids
        )
        and (
            value.get("release_package_id") is None
            or value.get("release_package_id") in visible_release_ids
        )
    }
    visible_git_promotion_ids = set(filtered["git_promotions"])
    filtered["deliverables"] = {
        key: _filter_deliverable_git_refs(value, visible_git_promotion_ids)
        for key, value in filtered["deliverables"].items()
    }
    filtered["release_packages"] = {
        key: {
            **value,
            "git_promotion_record_ids": _filter_ids(
                value.get("git_promotion_record_ids"),
                visible_git_promotion_ids,
            ),
        }
        for key, value in filtered["release_packages"].items()
    }
    filtered["ephemeral_assignments"] = {
        key: value
        for key, value in filtered.get("ephemeral_assignments", {}).items()
        if value.get("parent_agent_id") in visible_agent_ids
        and value.get("project_id") in visible_project_ids
        and value.get("task_id") in visible_task_ids
    }
    filtered["runtime_operations"] = _filter_runtime_operations(
        filtered.get("runtime_operations"),
        visible_agent_ids=visible_agent_ids,
        visible_project_ids=visible_project_ids,
        visible_task_ids=visible_task_ids,
        visible_deliverable_ids=visible_deliverable_ids,
        visible_inbox_ids=set(filtered["inbox"]),
        visible_action_ids=set(filtered["action_records"]),
        visible_release_ids=visible_release_ids,
        visible_git_promotion_ids=visible_git_promotion_ids,
        visible_assignment_ids=set(filtered["ephemeral_assignments"]),
    )
    return filtered


def _filter_runtime_operations(
    value: object,
    *,
    visible_agent_ids: set[str],
    visible_project_ids: set[str],
    visible_task_ids: set[str],
    visible_deliverable_ids: set[str],
    visible_inbox_ids: set[str],
    visible_action_ids: set[str],
    visible_release_ids: set[str],
    visible_git_promotion_ids: set[str],
    visible_assignment_ids: set[str],
) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {"generated_at": None, "agents": [], "attention": []}
    agents = [
        row
        for row in _mapping_items(value.get("agents"))
        if _optional_text(row.get("id")) in visible_agent_ids
    ]
    attention = [
        row
        for row in _mapping_items(value.get("attention"))
        if _runtime_row_is_visible(
            row,
            visible_agent_ids=visible_agent_ids,
            visible_project_ids=visible_project_ids,
            visible_task_ids=visible_task_ids,
            visible_deliverable_ids=visible_deliverable_ids,
            visible_inbox_ids=visible_inbox_ids,
            visible_action_ids=visible_action_ids,
            visible_release_ids=visible_release_ids,
            visible_git_promotion_ids=visible_git_promotion_ids,
            visible_assignment_ids=visible_assignment_ids,
        )
    ]
    return {
        "generated_at": value.get("generated_at"),
        "agents": agents,
        "attention": attention,
    }


def _runtime_row_is_visible(
    row: dict[str, Any],
    *,
    visible_agent_ids: set[str],
    visible_project_ids: set[str],
    visible_task_ids: set[str],
    visible_deliverable_ids: set[str],
    visible_inbox_ids: set[str],
    visible_action_ids: set[str],
    visible_release_ids: set[str],
    visible_git_promotion_ids: set[str],
    visible_assignment_ids: set[str],
) -> bool:
    checks = (
        (_optional_text(row.get("agent_id")), visible_agent_ids),
        (_optional_text(row.get("project_id")), visible_project_ids),
        (_optional_text(row.get("task_id")), visible_task_ids),
        (_optional_text(row.get("deliverable_id")), visible_deliverable_ids),
        (_optional_text(row.get("inbox_item_id")), visible_inbox_ids),
        (_optional_text(row.get("action_record_id")), visible_action_ids),
        (_optional_text(row.get("release_package_id")), visible_release_ids),
        (_optional_text(row.get("promotion_id")), visible_git_promotion_ids),
        (_optional_text(row.get("assignment_id")), visible_assignment_ids),
    )
    if not any(identifier is not None for identifier, _allowed in checks):
        return False
    for identifier, allowed in checks:
        if identifier is not None and identifier not in allowed:
            return False
    deliverable_ids = _string_items(row.get("deliverable_ids"))
    if deliverable_ids and any(
        deliverable_id not in visible_deliverable_ids
        for deliverable_id in deliverable_ids
    ):
        return False
    return True


def _mapping_items(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]


def _filter_ids(value: object, allowed: set[str]) -> list[str]:
    return [item for item in _string_items(value) if item in allowed]


def _filter_deliverable_git_refs(value: dict[str, Any], allowed: set[str]) -> dict[str, Any]:
    git_refs = value.get("git_refs")
    if not isinstance(git_refs, dict):
        return value
    return {
        **value,
        "git_refs": {
            **git_refs,
            "promotion_record_ids": _filter_ids(
                git_refs.get("promotion_record_ids"),
                allowed,
            ),
        },
    }


def _string_items(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if isinstance(item, str)]


def _required_text(value: object, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field_name} is required")
    return text


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_int(value: object, *, default: int | None) -> int | None:
    if value is None:
        return default
    if isinstance(value, bool):
        raise ValueError("integer value is required")
    return int(value)


def _optional_bool(value: object, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    raise ValueError("boolean value is required")


def _optional_mapping(value: object, field_name: str) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be a JSON object")
    return dict(value)


def _list_of_mappings(value: object, field_name: str) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a list")
    records: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError(f"{field_name} must contain JSON objects")
        records.append(dict(item))
    return records


def _string_list(value: object, field_name: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a list of strings")
    return [str(item).strip() for item in value if str(item).strip()]


def _consolidation_failure(target_branch: str | None, error: str) -> dict[str, Any]:
    return {
        "success": False,
        "target_branch": target_branch,
        "merged_branches": [],
        "commit_sha": None,
        "conflict_branch": None,
        "output": None,
        "error": error,
    }


def _git_text(proc: subprocess.CompletedProcess[str]) -> str:
    return (proc.stdout or "") + (proc.stderr or "")


def _consolidate_git_branches(
    *,
    host_repo_path: str,
    source_branches: list[str],
    target_branch: str,
    base_branch: str | None,
) -> dict[str, Any]:
    """Merge ``source_branches`` into ``target_branch`` inside ``host_repo_path``.

    Branch names are validated by the caller. Merges run in the listed order and
    produce deterministic merge commits. A merge conflict aborts the merge and
    returns ``success=False`` with the conflict output; nothing is auto-resolved.
    """

    repo_path = Path(host_repo_path)
    git_marker = repo_path / ".git"
    if not git_marker.exists():
        return _consolidation_failure(
            target_branch, f"{host_repo_path} is not a git repository"
        )

    def _git(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args],
            cwd=str(repo_path),
            capture_output=True,
            text=True,
            check=False,
        )

    # A linked worktree (``.git`` is a file pointing at the gitdir) is already an
    # isolated checkout, so a dirty tree there is acceptable; a primary worktree
    # must be clean before we move HEAD around.
    isolated_worktree = git_marker.is_file()
    status = _git("status", "--porcelain")
    if status.returncode != 0:
        return _consolidation_failure(target_branch, _git_text(status))
    if status.stdout.strip() and not isolated_worktree:
        failure = _consolidation_failure(
            target_branch, "refusing to merge into a dirty worktree"
        )
        failure["output"] = status.stdout
        return failure

    target_ref = f"refs/heads/{target_branch}"
    target_exists = _git("rev-parse", "--verify", "--quiet", target_ref).returncode == 0
    if target_exists:
        checkout = _git("checkout", target_branch)
    elif base_branch is not None:
        checkout = _git("checkout", "-B", target_branch, base_branch)
    else:
        checkout = _git("checkout", "-B", target_branch)
    if checkout.returncode != 0:
        return _consolidation_failure(target_branch, _git_text(checkout))

    merged: list[str] = []
    for source in source_branches:
        merge = _git(
            "merge",
            "--no-ff",
            "-m",
            f"Merge {source} into {target_branch}",
            source,
        )
        if merge.returncode != 0:
            _git("merge", "--abort")
            failure = _consolidation_failure(
                target_branch,
                f"merge conflict while merging {source} into {target_branch}",
            )
            failure["merged_branches"] = merged
            failure["conflict_branch"] = source
            failure["output"] = _git_text(merge)
            return failure
        merged.append(source)

    head = _git("rev-parse", "HEAD")
    commit_sha = head.stdout.strip() if head.returncode == 0 else None
    return {
        "success": True,
        "target_branch": target_branch,
        "merged_branches": merged,
        "commit_sha": commit_sha,
        "conflict_branch": None,
        "output": None,
        "error": None,
    }
