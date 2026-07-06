from __future__ import annotations

import hashlib
from collections.abc import Sequence
from typing import Any
from uuid import UUID, uuid5

from evander.semantic.qdrant import QdrantClient
from evander.semantic.workflow_helpers import (
    SEMANTIC_TEXT_PAYLOAD_KEY,
    embed_text,
    require_collection_exists,
    require_collection_schema,
    resolve_qdrant_api_key,
    resolve_settings,
    semantic_output_base,
)

from ..models import (
    PersistentAgent,
    TeamMemoryRecord,
    TeamMemoryScope,
    TeamMemoryStatus,
    TeamState,
    agent_lead_project_ids,
    agent_project_ids,
)
from .domain.common import _optional_text, _required_text, _slug_id_part

TEAM_MEMORY_POINT_NAMESPACE = UUID("63fd31de-7e1e-4aa9-b549-7b1564e15c80")
DEFAULT_MEMORY_LIMIT = 20
MAX_MEMORY_LIMIT = 50
MAX_SEMANTIC_CANDIDATES = 80


def team_memory_collection_name(team_id: str) -> str:
    normalized = _required_text(team_id, "team_id")
    slug = _slug_id_part(normalized, fallback="team")[:48]
    digest = hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:12]
    return f"team_memory_{slug}_{digest}"


def team_memory_point_id(memory_id: str) -> str:
    return str(
        uuid5(TEAM_MEMORY_POINT_NAMESPACE, f"team-memory:{_required_text(memory_id, 'memory_id')}")
    )


def team_memory_index_text(memory: TeamMemoryRecord) -> str:
    parts = [
        f"Title: {memory.title}",
        f"Summary: {memory.summary}",
    ]
    if memory.body is not None:
        parts.append(f"Body: {memory.body}")
    if memory.tags:
        parts.append(f"Tags: {', '.join(memory.tags)}")
    return "\n".join(parts)


def team_memory_point_payload(memory: TeamMemoryRecord) -> dict[str, Any]:
    text = team_memory_index_text(memory)
    return {
        "memory_id": memory.id,
        "team_id": memory.team_id,
        "project_id": memory.project_id,
        "codebase_id": memory.codebase_id,
        "agent_id": memory.agent_id,
        "scope": memory.scope.value,
        "kind": memory.kind.value,
        "status": memory.status.value,
        "tags": list(memory.tags),
        "artifact_refs": list(memory.artifact_refs),
        "created_at": memory.created_at.isoformat(),
        "updated_at": memory.updated_at.isoformat(),
        SEMANTIC_TEXT_PAYLOAD_KEY: text,
    }


def memory_record_payload(
    memory: TeamMemoryRecord,
    *,
    include_body: bool = False,
    score: float | None = None,
) -> dict[str, Any]:
    payload = memory.model_dump(mode="json")
    if not include_body:
        payload.pop("body", None)
    if score is not None:
        payload["score"] = score
    return payload


def list_memory_records(
    state: TeamState,
    *,
    actor: PersistentAgent | None = None,
    team_id: str | None = None,
    project_id: str | None = None,
    codebase_id: str | None = None,
    agent_id: str | None = None,
    kinds: Sequence[str] | None = None,
    statuses: Sequence[str] | None = None,
    tags_any: Sequence[str] | None = None,
    query_text: str | None = None,
    include_archived: bool = False,
    limit: int = DEFAULT_MEMORY_LIMIT,
    offset: int = 0,
) -> dict[str, Any]:
    kind_filter = {_required_text(value, "kinds") for value in kinds or []}
    status_filter = {_required_text(value, "statuses") for value in statuses or []}
    include_archived = include_archived or _memory_status_filter_includes_archived(statuses)
    tag_filter = {_required_text(value, "tags_any").casefold() for value in tags_any or []}
    normalized_query = _optional_text(query_text)
    query_lower = normalized_query.casefold() if normalized_query is not None else None
    rows = [
        memory
        for memory in state.memories.values()
        if memory_visible_to_actor(state, memory, actor=actor)
        and _memory_matches_scope(
            memory,
            team_id=team_id,
            project_id=project_id,
            codebase_id=codebase_id,
            agent_id=agent_id,
        )
        and (include_archived or memory.status != TeamMemoryStatus.ARCHIVED)
        and (not kind_filter or memory.kind.value in kind_filter)
        and (not status_filter or memory.status.value in status_filter)
        and (not tag_filter or tag_filter.intersection(memory.tags))
        and (query_lower is None or _memory_text_matches(memory, query_lower))
    ]
    rows.sort(key=lambda memory: (memory.updated_at, memory.id), reverse=True)
    safe_limit = _memory_limit(limit)
    safe_offset = max(0, int(offset or 0))
    page = rows[safe_offset : safe_offset + safe_limit]
    next_offset = safe_offset + len(page)
    return {
        "records": page,
        "count": len(page),
        "total": len(rows),
        "total_count": len(rows),
        "offset": safe_offset,
        "limit": safe_limit,
        "next_cursor": str(next_offset) if next_offset < len(rows) else None,
        "has_more": next_offset < len(rows),
        "omitted_count": max(0, len(rows) - next_offset),
    }


class TeamMemorySemanticService:
    def __init__(self, runtime_context: Any) -> None:
        self._runtime_context = runtime_context

    def index_memory(
        self,
        memory: TeamMemoryRecord,
        *,
        directory_path: str | None = None,
        wait: bool | None = None,
    ) -> dict[str, Any]:
        settings = resolve_settings(
            self._runtime_context,
            directory_path=directory_path,
            provider_kind="effect",
        )
        qdrant_api_key = resolve_qdrant_api_key(self._runtime_context, settings)
        collection_name = team_memory_collection_name(memory.team_id)
        text = team_memory_index_text(memory)
        with QdrantClient(settings.qdrant_base_url, api_key=qdrant_api_key) as client:
            collection_result = client.ensure_collection(
                collection_name,
                vector_size=settings.semantic_embedding_dimensions,
                distance=settings.semantic_vector_distance,
            )
            vector = embed_text(self._runtime_context, settings, text)
            operation = client.upsert_point(
                collection_result.collection.name,
                point_id=team_memory_point_id(memory.id),
                vector=vector,
                payload=team_memory_point_payload(memory),
                wait=wait,
            )
        return {
            "success": True,
            **semantic_output_base(settings, qdrant_api_key=qdrant_api_key),
            "collection_name": collection_result.collection.name,
            "created_collection": collection_result.created,
            "point_id": team_memory_point_id(memory.id),
            "memory_id": memory.id,
            "team_id": memory.team_id,
            "operation_status": operation.status,
            "operation_id": operation.operation_id,
        }

    def delete_memory(
        self,
        *,
        team_id: str,
        memory_id: str,
        directory_path: str | None = None,
        wait: bool | None = None,
    ) -> dict[str, Any]:
        settings = resolve_settings(
            self._runtime_context,
            directory_path=directory_path,
            provider_kind="effect",
        )
        qdrant_api_key = resolve_qdrant_api_key(self._runtime_context, settings)
        collection_name = team_memory_collection_name(team_id)
        point_id = team_memory_point_id(memory_id)
        with QdrantClient(settings.qdrant_base_url, api_key=qdrant_api_key) as client:
            collection = require_collection_exists(
                client,
                collection_name,
                node_type="effect:team:index-memory",
            )
            operation = client.delete_points(collection.name, point_ids=[point_id], wait=wait)
        return {
            "success": True,
            **semantic_output_base(settings, qdrant_api_key=qdrant_api_key),
            "collection_name": collection.name,
            "point_ids": [point_id],
            "memory_id": memory_id,
            "team_id": team_id,
            "operation_status": operation.status,
            "operation_id": operation.operation_id,
        }

    def semantic_search(
        self,
        state: TeamState,
        *,
        query_text: str,
        actor: PersistentAgent | None = None,
        team_id: str,
        project_id: str | None = None,
        codebase_id: str | None = None,
        agent_id: str | None = None,
        kinds: Sequence[str] | None = None,
        statuses: Sequence[str] | None = None,
        tags_any: Sequence[str] | None = None,
        limit: int = DEFAULT_MEMORY_LIMIT,
        directory_path: str | None = None,
        score_threshold: float | None = None,
    ) -> dict[str, Any]:
        settings = resolve_settings(
            self._runtime_context,
            directory_path=directory_path,
            provider_kind="query",
        )
        qdrant_api_key = resolve_qdrant_api_key(self._runtime_context, settings)
        collection_name = team_memory_collection_name(team_id)
        safe_limit = _memory_limit(limit)
        candidate_limit = min(MAX_SEMANTIC_CANDIDATES, max(safe_limit * 4, safe_limit))
        with QdrantClient(settings.qdrant_base_url, api_key=qdrant_api_key) as client:
            collection = require_collection_exists(
                client,
                collection_name,
                node_type="query:team:memory",
            )
            require_collection_schema(collection, settings)
            vector = embed_text(self._runtime_context, settings, query_text)
            points = client.query_points(
                collection.name,
                query_vector=vector,
                filter=_semantic_filter(
                    team_id=team_id,
                    project_id=project_id,
                    codebase_id=codebase_id,
                    agent_id=agent_id,
                    statuses=statuses,
                    kinds=kinds,
                    tags_any=tags_any,
                ),
                limit=candidate_limit,
                score_threshold=score_threshold,
            )
        scores_by_memory_id: dict[str, float | None] = {}
        for point in points:
            payload = dict(point.payload or {})
            memory_id = _optional_text(payload.get("memory_id"))
            if memory_id is None:
                continue
            scores_by_memory_id.setdefault(memory_id, point.score)
        records: list[dict[str, Any]] = []
        for memory_id, score in scores_by_memory_id.items():
            memory = state.memories.get(memory_id)
            if memory is None:
                continue
            if not memory_visible_to_actor(state, memory, actor=actor):
                continue
            if not _memory_matches_scope(
                memory,
                team_id=team_id,
                project_id=project_id,
                codebase_id=codebase_id,
                agent_id=agent_id,
            ):
                continue
            if not _memory_matches_filters(
                memory,
                kinds=kinds,
                statuses=statuses,
                tags_any=tags_any,
                include_archived=_memory_status_filter_includes_archived(statuses),
            ):
                continue
            records.append(memory_record_payload(memory, include_body=False, score=score))
            if len(records) >= safe_limit:
                break
        return {
            "success": True,
            **semantic_output_base(settings, qdrant_api_key=qdrant_api_key),
            "collection_name": collection.name,
            "query_text": query_text,
            "records": records,
            "semantic_point_count": len(points),
            "count": len(records),
            "limit": safe_limit,
        }


def _memory_limit(limit: int) -> int:
    return max(1, min(MAX_MEMORY_LIMIT, int(limit or DEFAULT_MEMORY_LIMIT)))


def _memory_status_filter_includes_archived(statuses: Sequence[str] | None) -> bool:
    return any(
        _required_text(value, "statuses") == TeamMemoryStatus.ARCHIVED.value
        for value in statuses or []
    )


def _memory_matches_scope(
    memory: TeamMemoryRecord,
    *,
    team_id: str | None,
    project_id: str | None,
    codebase_id: str | None,
    agent_id: str | None,
) -> bool:
    normalized_team_id = _optional_text(team_id)
    if normalized_team_id is not None and memory.team_id != normalized_team_id:
        return False
    normalized_project_id = _optional_text(project_id)
    if normalized_project_id is not None and memory.scope != TeamMemoryScope.TEAM_GLOBAL:
        if memory.project_id != normalized_project_id:
            return False
    normalized_codebase_id = _optional_text(codebase_id)
    if normalized_codebase_id is not None and memory.scope != TeamMemoryScope.TEAM_GLOBAL:
        if memory.codebase_id != normalized_codebase_id:
            return False
    normalized_agent_id = _optional_text(agent_id)
    if normalized_agent_id is not None and memory.agent_id != normalized_agent_id:
        return False
    return True


def memory_visible_to_actor(
    state: TeamState,
    memory: TeamMemoryRecord,
    *,
    actor: PersistentAgent | None,
) -> bool:
    if actor is None:
        return True
    visible_team_ids = _visible_team_ids(state, actor)
    if memory.team_id not in visible_team_ids:
        return False
    if memory.scope == TeamMemoryScope.AGENT_PRIVATE:
        return memory.agent_id == actor.id
    if memory.scope == TeamMemoryScope.PROJECT and memory.project_id is not None:
        return memory.project_id in _visible_project_ids(state, actor)
    return True


def _visible_team_ids(state: TeamState, actor: PersistentAgent) -> set[str]:
    visible = _managed_team_ids(state, actor)
    if actor.team_id is not None:
        visible.add(actor.team_id)
    return visible


def _visible_project_ids(state: TeamState, actor: PersistentAgent) -> set[str]:
    visible = set(agent_project_ids(state, actor.id))
    visible.update(agent_lead_project_ids(state, actor.id))
    if actor.role_id is not None:
        role = state.roles.get(actor.role_id)
        if role is not None and role.project_id is not None:
            visible.add(role.project_id)
    managed_team_ids = _managed_team_ids(state, actor)
    for project in state.projects.values():
        if project.lead_agent_id == actor.id:
            visible.add(project.id)
        if project.team_id in managed_team_ids:
            visible.add(project.id)
    for subordinate_id in _subordinate_agent_ids(state, actor):
        visible.update(agent_project_ids(state, subordinate_id))
        visible.update(agent_lead_project_ids(state, subordinate_id))
    return visible


def _managed_team_ids(state: TeamState, actor: PersistentAgent) -> set[str]:
    return {team.id for team in state.teams.values() if team.manager_agent_id == actor.id}


def _subordinate_agent_ids(state: TeamState, actor: PersistentAgent) -> set[str]:
    result: set[str] = set()
    actor_id = actor.id
    stack = list(actor.subordinate_agent_ids)
    while stack:
        agent_id = stack.pop()
        if agent_id in result:
            continue
        result.add(agent_id)
        agent = state.agents.get(agent_id)
        if agent is not None:
            stack.extend(agent.subordinate_agent_ids)
    for agent in state.agents.values():
        if agent.id != actor_id and _reports_under(state, agent.id, actor_id):
            result.add(agent.id)
    return result


def _reports_under(state: TeamState, agent_id: str, ancestor_id: str) -> bool:
    seen: set[str] = set()
    current = state.agents.get(agent_id)
    while current is not None and current.reports_to_agent_id is not None:
        parent_id = current.reports_to_agent_id
        if parent_id == ancestor_id:
            return True
        if parent_id in seen:
            return False
        seen.add(parent_id)
        current = state.agents.get(parent_id)
    return False


def _memory_text_matches(memory: TeamMemoryRecord, query_lower: str) -> bool:
    fields = [
        memory.id,
        memory.title,
        memory.summary,
        memory.body or "",
        " ".join(memory.tags),
        " ".join(memory.source_refs),
        " ".join(memory.artifact_refs),
    ]
    return any(query_lower in field.casefold() for field in fields)


def _memory_matches_filters(
    memory: TeamMemoryRecord,
    *,
    kinds: Sequence[str] | None,
    statuses: Sequence[str] | None,
    tags_any: Sequence[str] | None,
    include_archived: bool,
) -> bool:
    if not include_archived and memory.status == TeamMemoryStatus.ARCHIVED:
        return False
    kind_filter = {_required_text(value, "kinds") for value in kinds or []}
    if kind_filter and memory.kind.value not in kind_filter:
        return False
    status_filter = {_required_text(value, "statuses") for value in statuses or []}
    if status_filter and memory.status.value not in status_filter:
        return False
    tag_filter = {_required_text(value, "tags_any").casefold() for value in tags_any or []}
    if tag_filter and not tag_filter.intersection(memory.tags):
        return False
    return True


def _semantic_filter(
    *,
    team_id: str,
    project_id: str | None,
    codebase_id: str | None,
    agent_id: str | None,
    statuses: Sequence[str] | None,
    kinds: Sequence[str] | None,
    tags_any: Sequence[str] | None,
) -> dict[str, Any]:
    must: list[dict[str, Any]] = [
        {"key": "team_id", "match": {"value": _required_text(team_id, "team_id")}},
    ]
    status_values = [_required_text(value, "statuses") for value in statuses or []]
    if status_values:
        must.append({"key": "status", "match": {"any": status_values}})
    else:
        must.append({"key": "status", "match": {"value": TeamMemoryStatus.ACTIVE.value}})
    kind_values = [_required_text(value, "kinds") for value in kinds or []]
    if kind_values:
        must.append({"key": "kind", "match": {"any": kind_values}})
    tag_values = [_required_text(value, "tags_any").casefold() for value in tags_any or []]
    if tag_values:
        must.append({"key": "tags", "match": {"any": tag_values}})
    if agent_id is not None:
        must.append({"key": "agent_id", "match": {"value": _required_text(agent_id, "agent_id")}})
    should: list[dict[str, Any]] = []
    if project_id is not None:
        should.extend(
            [
                {"key": "project_id", "match": {"value": _required_text(project_id, "project_id")}},
                {"key": "scope", "match": {"value": TeamMemoryScope.TEAM_GLOBAL.value}},
            ]
        )
    if codebase_id is not None:
        should.extend(
            [
                {
                    "key": "codebase_id",
                    "match": {"value": _required_text(codebase_id, "codebase_id")},
                },
                {"key": "scope", "match": {"value": TeamMemoryScope.TEAM_GLOBAL.value}},
            ]
        )
    result: dict[str, Any] = {"must": must}
    if should:
        result["should"] = should
    return result


__all__ = [
    "DEFAULT_MEMORY_LIMIT",
    "MAX_MEMORY_LIMIT",
    "TeamMemorySemanticService",
    "list_memory_records",
    "memory_visible_to_actor",
    "memory_record_payload",
    "team_memory_collection_name",
    "team_memory_index_text",
    "team_memory_point_id",
    "team_memory_point_payload",
]
