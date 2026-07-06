from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any
from uuid import uuid4

from evander.models import utc_now

from ...models import (
    TeamMemoryKind,
    TeamMemoryRecord,
    TeamMemoryScope,
    TeamMemoryStatus,
    TeamState,
    _unique_text_list,
)
from ..domain.common import _optional_text, _required_text
from .deliverable_helpers import _safe_deliverable_artifact_paths
from .executor import TeamCommandExecutor
from .types import (
    TeamCommandApplyResult,
    TeamCommandContext,
    TeamCommandValidationError,
    _ResolvedTeamCommandContext,
)

TeamMemoryArtifactPublisher = Callable[..., Mapping[str, Any]]


class TeamMemoryCommandService(TeamCommandExecutor):
    def record_memory_command(
        self,
        *,
        session_id: str,
        request_id: str,
        kind: str,
        title: str,
        summary: str,
        id: str | None = None,
        project_id: str | None = None,
        codebase_id: str | None = None,
        agent_id: str | None = None,
        scope: str | None = None,
        body: str | None = None,
        tags: Sequence[str] | None = None,
        source_refs: Sequence[str] | None = None,
        artifact_refs: Sequence[str] | None = None,
        artifact_paths: Sequence[str] | None = None,
        artifact_space_ref: str | None = None,
        confidence: float | None = None,
        supersedes_memory_id: str | None = None,
        handled_inbox_item_ids: Sequence[str] | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
        artifact_publisher: TeamMemoryArtifactPublisher | None = None,
    ) -> tuple[TeamState, dict[str, Any]]:
        normalized_session_id = _required_text(session_id, "session_id")
        normalized_request_id = _required_text(request_id, "request_id")
        normalized_kind = TeamMemoryKind(_required_text(kind, "kind"))
        normalized_title = _required_text(title, "title")
        normalized_summary = _required_text(summary, "summary")
        normalized_scope = TeamMemoryScope(_optional_text(scope) or _default_scope(project_id, agent_id))
        normalized_tags = [
            value.casefold() for value in _unique_text_list(tags or [], "tags")
        ]
        normalized_source_refs = _unique_text_list(source_refs or [], "source_refs")
        normalized_artifact_refs = _unique_text_list(artifact_refs or [], "artifact_refs")
        normalized_artifact_paths = _safe_deliverable_artifact_paths(artifact_paths or [])
        normalized_artifact_space_ref = _optional_text(artifact_space_ref)
        if normalized_artifact_space_ref is not None:
            normalized_artifact_refs = _append_unique(
                normalized_artifact_refs,
                normalized_artifact_space_ref,
            )
        normalized_handled_ids = _unique_text_list(
            handled_inbox_item_ids or [],
            "handled_inbox_item_ids",
        )
        context = TeamCommandContext(
            ambient_session_id=normalized_session_id,
            tool_name="team_record_memory",
            request_id=normalized_request_id,
            correlation_id=_optional_text(correlation_id),
            causation_id=_optional_text(causation_id),
            progress_classification="context",
        )
        normalized_payload = {
            "id": _optional_text(id),
            "project_id": _optional_text(project_id),
            "codebase_id": _optional_text(codebase_id),
            "agent_id": _optional_text(agent_id),
            "scope": normalized_scope.value,
            "kind": normalized_kind.value,
            "title": normalized_title,
            "summary": normalized_summary,
            "body": _optional_text(body),
            "tags": sorted(normalized_tags),
            "source_refs": sorted(normalized_source_refs),
            "artifact_refs": sorted(normalized_artifact_refs),
            "artifact_paths": sorted(normalized_artifact_paths),
            "confidence": confidence,
            "supersedes_memory_id": _optional_text(supersedes_memory_id),
            "handled_inbox_item_ids": sorted(normalized_handled_ids),
            "correlation_id": context.correlation_id,
            "causation_id": context.causation_id,
        }

        def _apply(
            state: TeamState,
            resolved: _ResolvedTeamCommandContext,
        ) -> TeamCommandApplyResult:
            agent = resolved.agent
            if agent is None or resolved.dispatch_id is None:
                raise TeamCommandValidationError(
                    code="invalid_command_scope",
                    message="team_record_memory requires an active agent dispatch",
                )
            if agent.team_id is None:
                raise TeamCommandValidationError(
                    code="team_required",
                    message="team_record_memory requires a Team agent",
                )
            memory_id = _optional_text(id) or f"memory-{uuid4().hex}"
            existing = state.memories.get(memory_id)
            if existing is not None and existing.team_id != agent.team_id:
                raise TeamCommandValidationError(
                    code="memory_scope_conflict",
                    message=f"memory {memory_id} belongs to another team",
                    correction_context={"memory_id": memory_id},
                )
            target_agent_id = _optional_text(agent_id)
            if normalized_scope == TeamMemoryScope.AGENT_PRIVATE:
                target_agent_id = target_agent_id or agent.id
                if target_agent_id != agent.id:
                    raise TeamCommandValidationError(
                        code="agent_private_memory_scope",
                        message="agent-private memory can only target the caller",
                        correction_context={"agent_id": target_agent_id},
                    )
            _validate_memory_scope(
                state,
                team_id=agent.team_id,
                project_id=_optional_text(project_id),
                agent_id=target_agent_id,
                scope=normalized_scope,
            )
            refs = list(normalized_artifact_refs)
            published_refs = _publish_memory_artifacts(
                agent=agent,
                team_id=agent.team_id,
                project_id=_optional_text(project_id),
                memory_id=memory_id,
                dispatch_id=resolved.dispatch_id,
                artifact_paths=normalized_artifact_paths,
                artifact_publisher=artifact_publisher,
            )
            for ref in published_refs:
                refs = _append_unique(refs, ref)
            now = utc_now()
            memory = existing or TeamMemoryRecord(
                id=memory_id,
                team_id=agent.team_id,
                kind=normalized_kind,
                title=normalized_title,
                summary=normalized_summary,
                created_by_agent_id=agent.id,
                created_by=f"agent:{agent.id}",
                created_at=now,
                updated_at=now,
            )
            memory.project_id = _optional_text(project_id)
            memory.codebase_id = _optional_text(codebase_id)
            memory.agent_id = target_agent_id
            memory.scope = normalized_scope
            memory.kind = normalized_kind
            memory.title = normalized_title
            memory.summary = normalized_summary
            memory.body = _optional_text(body)
            memory.tags = list(normalized_tags)
            memory.source_refs = list(normalized_source_refs)
            memory.artifact_refs = refs
            memory.status = TeamMemoryStatus.ACTIVE
            memory.confidence = confidence
            memory.supersedes_memory_id = _optional_text(supersedes_memory_id)
            memory.updated_at = now
            state.memories[memory.id] = memory
            if memory.supersedes_memory_id is not None:
                superseded = state.memories.get(memory.supersedes_memory_id)
                if superseded is None:
                    raise TeamCommandValidationError(
                        code="missing_superseded_memory",
                        message=f"superseded memory {memory.supersedes_memory_id} does not exist",
                        correction_context={"supersedes_memory_id": memory.supersedes_memory_id},
                    )
                if superseded.team_id != memory.team_id:
                    raise TeamCommandValidationError(
                        code="memory_scope_conflict",
                        message="superseded memory belongs to another team",
                        correction_context={"supersedes_memory_id": memory.supersedes_memory_id},
                    )
                superseded.status = TeamMemoryStatus.SUPERSEDED
                superseded.superseded_by_memory_id = memory.id
                superseded.updated_at = now
            return TeamCommandApplyResult(
                response_payload={
                    "memory_id": memory.id,
                    "memory": memory.model_dump(mode="json", exclude={"body"}),
                    "published_artifact_refs": published_refs,
                    "progress_made": False,
                },
                created_domain_ids=[memory.id],
            )

        return self._execute_team_command(
            context=context,
            normalized_payload=normalized_payload,
            handled_inbox_item_ids=normalized_handled_ids,
            apply_command=_apply,
            require_active_dispatch=True,
        )

    def archive_memory_command(
        self,
        *,
        session_id: str,
        request_id: str,
        memory_id: str,
        reason: str | None = None,
        superseded_by_memory_id: str | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> tuple[TeamState, dict[str, Any]]:
        normalized_session_id = _required_text(session_id, "session_id")
        normalized_request_id = _required_text(request_id, "request_id")
        normalized_memory_id = _required_text(memory_id, "memory_id")
        normalized_superseded_by = _optional_text(superseded_by_memory_id)
        context = TeamCommandContext(
            ambient_session_id=normalized_session_id,
            tool_name="team_archive_memory",
            request_id=normalized_request_id,
            correlation_id=_optional_text(correlation_id),
            causation_id=_optional_text(causation_id),
            progress_classification="context",
        )
        normalized_payload = {
            "memory_id": normalized_memory_id,
            "reason": _optional_text(reason),
            "superseded_by_memory_id": normalized_superseded_by,
            "correlation_id": context.correlation_id,
            "causation_id": context.causation_id,
        }

        def _apply(
            state: TeamState,
            resolved: _ResolvedTeamCommandContext,
        ) -> TeamCommandApplyResult:
            agent = resolved.agent
            if agent is None or resolved.dispatch_id is None:
                raise TeamCommandValidationError(
                    code="invalid_command_scope",
                    message="team_archive_memory requires an active agent dispatch",
                )
            memory = state.memories.get(normalized_memory_id)
            if memory is None:
                raise TeamCommandValidationError(
                    code="memory_not_found",
                    message=f"memory {normalized_memory_id} does not exist",
                    correction_context={"memory_id": normalized_memory_id},
                )
            if memory.team_id != agent.team_id:
                raise TeamCommandValidationError(
                    code="memory_scope_conflict",
                    message=f"memory {normalized_memory_id} belongs to another team",
                    correction_context={"memory_id": normalized_memory_id},
                )
            if memory.scope == TeamMemoryScope.AGENT_PRIVATE and memory.agent_id != agent.id:
                raise TeamCommandValidationError(
                    code="memory_scope_conflict",
                    message="agent-private memory can only be archived by its owner",
                    correction_context={"memory_id": normalized_memory_id},
                )
            now = utc_now()
            memory.status = (
                TeamMemoryStatus.SUPERSEDED
                if normalized_superseded_by is not None
                else TeamMemoryStatus.ARCHIVED
            )
            memory.superseded_by_memory_id = normalized_superseded_by
            memory.updated_at = now
            return TeamCommandApplyResult(
                response_payload={
                    "memory_id": memory.id,
                    "status": memory.status.value,
                    "reason": _optional_text(reason),
                    "progress_made": False,
                },
                created_domain_ids=[memory.id],
            )

        return self._execute_team_command(
            context=context,
            normalized_payload=normalized_payload,
            handled_inbox_item_ids=[],
            apply_command=_apply,
            require_active_dispatch=True,
        )


def _default_scope(project_id: str | None, agent_id: str | None) -> str:
    if _optional_text(agent_id) is not None:
        return TeamMemoryScope.AGENT_PRIVATE.value
    if _optional_text(project_id) is not None:
        return TeamMemoryScope.PROJECT.value
    return TeamMemoryScope.TEAM_GLOBAL.value


def _validate_memory_scope(
    state: TeamState,
    *,
    team_id: str,
    project_id: str | None,
    agent_id: str | None,
    scope: TeamMemoryScope,
) -> None:
    if project_id is not None:
        project = state.projects.get(project_id)
        if project is None:
            raise TeamCommandValidationError(
                code="project_not_found",
                message=f"project {project_id} does not exist",
                correction_context={"project_id": project_id},
            )
        if project.team_id != team_id:
            raise TeamCommandValidationError(
                code="project_scope_conflict",
                message=f"project {project_id} belongs to another team",
                correction_context={"project_id": project_id},
            )
    if agent_id is not None:
        agent = state.agents.get(agent_id)
        if agent is None:
            raise TeamCommandValidationError(
                code="agent_not_found",
                message=f"agent {agent_id} does not exist",
                correction_context={"agent_id": agent_id},
            )
        if agent.team_id != team_id:
            raise TeamCommandValidationError(
                code="agent_scope_conflict",
                message=f"agent {agent_id} belongs to another team",
                correction_context={"agent_id": agent_id},
            )
    if scope == TeamMemoryScope.PROJECT and project_id is None:
        raise TeamCommandValidationError(
            code="project_required",
            message="project-scoped memory requires project_id",
        )
    if scope == TeamMemoryScope.AGENT_PRIVATE and agent_id is None:
        raise TeamCommandValidationError(
            code="agent_required",
            message="agent-private memory requires agent_id",
        )


def _publish_memory_artifacts(
    *,
    agent: Any,
    team_id: str,
    project_id: str | None,
    memory_id: str,
    dispatch_id: str,
    artifact_paths: Sequence[str],
    artifact_publisher: TeamMemoryArtifactPublisher | None,
) -> list[str]:
    safe_paths = _safe_deliverable_artifact_paths(artifact_paths)
    if not safe_paths:
        return []
    if artifact_publisher is None:
        raise TeamCommandValidationError(
            code="artifact_publisher_unavailable",
            message="artifact_paths were provided but no host artifact publisher is available",
            correction_context={"memory_id": memory_id, "artifact_paths": list(safe_paths)},
        )
    try:
        record = artifact_publisher(
            agent=agent,
            team_id=team_id,
            project_id=project_id,
            memory_id=memory_id,
            dispatch_id=dispatch_id,
            source_paths=list(safe_paths),
        )
    except Exception as exc:
        raise TeamCommandValidationError(
            code="artifact_publication_failed",
            message=f"failed to publish memory artifact_paths: {exc}",
            correction_context={"memory_id": memory_id, "artifact_paths": list(safe_paths)},
        ) from None
    artifact_ref = _optional_text(dict(record).get("artifact_ref"))
    if artifact_ref is None:
        raise TeamCommandValidationError(
            code="artifact_publication_failed",
            message="artifact publisher did not return artifact_ref",
            correction_context={"memory_id": memory_id, "published_artifact": dict(record)},
        )
    return [artifact_ref]


def _append_unique(values: list[str], value: str) -> list[str]:
    return values if value in values else [*values, value]


__all__ = ["TeamMemoryArtifactPublisher", "TeamMemoryCommandService"]
