"""Provider memory."""

from __future__ import annotations

from collections.abc import (
    Mapping,
)
from typing import (
    Any,
)

from evander.effects.providers.base import (
    EffectProviderBase,
)
from evander.events.models import (
    WorkflowNodeEnvelope,
)
from evander.queries.providers.base import (
    QueryProviderBase,
)

from .models import (
    TeamMemoryRecord,
    TeamMemoryScope,
    TeamMemoryStatus,
    TeamState,
)
from .provider_events import (
    _materialize,
)
from .provider_schemas import (
    TeamIndexMemoryEffectDefinition,
    TeamMemoryQueryDefinition,
    TeamReindexMemoryEffectDefinition,
)
from .provider_values import (
    _definition_payload,
    _optional_bool,
    _optional_float,
    _optional_int,
    _optional_text,
    _required_text,
    _runtime_dir,
    _string_list,
)
from .repository import (
    TeamStateRepository,
)
from .services.memory_service import (
    DEFAULT_MEMORY_LIMIT,
    TeamMemorySemanticService,
    list_memory_records,
    memory_record_payload,
)


class TeamIndexMemoryEffectProvider(EffectProviderBase):
    @classmethod
    def validate_definition(cls, effect_definition):
        if isinstance(effect_definition, WorkflowNodeEnvelope):
            return _materialize(TeamIndexMemoryEffectDefinition, effect_definition)
        return effect_definition

    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = _definition_payload(
            self.effect_definition,
            "memory_id",
            "memory_ids",
            "team_id",
            "project_id",
            "directory_path",
            "wait",
        )
        if resolved_config:
            payload.update(dict(resolved_config))
        repo = TeamStateRepository(_runtime_dir(self.runtime_context))
        state = repo.read()
        try:
            memory_ids = _team_memory_id_selection(payload)
            team_id = _optional_text(payload.get("team_id"))
            project_id = _optional_text(payload.get("project_id"))
            if memory_ids:
                records, errors = _team_memory_records_by_ids(
                    state,
                    memory_ids=memory_ids,
                    team_id=team_id,
                    project_id=project_id,
                )
            else:
                required_team_id = _required_text(payload.get("team_id"), "team_id")
                records = _team_memory_records_for_team(
                    state,
                    team_id=required_team_id,
                    project_id=project_id,
                )
                errors = []
            return _sync_team_memory_records_to_semantic_index(
                self.runtime_context,
                state=state,
                records=records,
                directory_path=_optional_text(payload.get("directory_path")),
                wait=_optional_bool(payload.get("wait"), default=False),
                initial_errors=errors,
            )
        except (TypeError, ValueError) as exc:
            return {
                "success": False,
                "revision": state.revision,
                "indexed_count": 0,
                "deleted_count": 0,
                "memory_ids": [],
                "index_results": [],
                "delete_results": [],
                "errors": [str(exc)],
            }


class TeamReindexMemoryEffectProvider(EffectProviderBase):
    @classmethod
    def validate_definition(cls, effect_definition):
        if isinstance(effect_definition, WorkflowNodeEnvelope):
            return _materialize(TeamReindexMemoryEffectDefinition, effect_definition)
        return effect_definition

    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = _definition_payload(
            self.effect_definition,
            "team_id",
            "project_id",
            "directory_path",
            "wait",
        )
        if resolved_config:
            payload.update(dict(resolved_config))
        repo = TeamStateRepository(_runtime_dir(self.runtime_context))
        state = repo.read()
        try:
            team_id = _required_text(payload.get("team_id"), "team_id")
            project_id = _optional_text(payload.get("project_id"))
            records = _team_memory_records_for_team(
                state,
                team_id=team_id,
                project_id=project_id,
            )
            result = _sync_team_memory_records_to_semantic_index(
                self.runtime_context,
                state=state,
                records=records,
                directory_path=_optional_text(payload.get("directory_path")),
                wait=_optional_bool(payload.get("wait"), default=False),
            )
            return {
                **result,
                "team_id": team_id,
                "project_id": project_id,
            }
        except (TypeError, ValueError) as exc:
            return {
                "success": False,
                "revision": state.revision,
                "team_id": _optional_text(payload.get("team_id")) or "",
                "project_id": _optional_text(payload.get("project_id")),
                "indexed_count": 0,
                "deleted_count": 0,
                "memory_ids": [],
                "index_results": [],
                "delete_results": [],
                "errors": [str(exc)],
            }


class TeamMemoryQueryProvider(QueryProviderBase):
    @classmethod
    def validate_definition(cls, query_definition):
        if isinstance(query_definition, WorkflowNodeEnvelope):
            return _materialize(TeamMemoryQueryDefinition, query_definition)
        return query_definition

    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = _definition_payload(
            self.query_definition,
            "team_id",
            "project_id",
            "codebase_id",
            "agent_id",
            "query_text",
            "semantic",
            "kinds",
            "statuses",
            "tags_any",
            "limit",
            "directory_path",
            "score_threshold",
        )
        if resolved_config:
            payload.update(dict(resolved_config))
        state = TeamStateRepository(_runtime_dir(self.runtime_context)).read()
        team_id = _optional_text(payload.get("team_id"))
        query_text = _optional_text(payload.get("query_text"))
        kinds = _string_list(payload.get("kinds"), "kinds")
        statuses = _string_list(payload.get("statuses"), "statuses")
        tags_any = _string_list(payload.get("tags_any"), "tags_any")
        limit = _optional_int(payload.get("limit"), default=DEFAULT_MEMORY_LIMIT)
        semantic_error: str | None = None
        if _optional_bool(payload.get("semantic"), default=False) and query_text is not None:
            if team_id is None:
                semantic_error = "query:team:memory semantic search requires team_id"
            else:
                try:
                    semantic_result = TeamMemorySemanticService(
                        self.runtime_context
                    ).semantic_search(
                        state,
                        query_text=query_text,
                        team_id=team_id,
                        project_id=_optional_text(payload.get("project_id")),
                        codebase_id=_optional_text(payload.get("codebase_id")),
                        agent_id=_optional_text(payload.get("agent_id")),
                        kinds=kinds,
                        statuses=statuses,
                        tags_any=tags_any,
                        limit=limit or DEFAULT_MEMORY_LIMIT,
                        directory_path=_optional_text(payload.get("directory_path")),
                        score_threshold=_optional_float(
                            payload.get("score_threshold"),
                            default=None,
                        ),
                    )
                    records = list(semantic_result.get("records") or [])
                    return {
                        **semantic_result,
                        "success": True,
                        "revision": state.revision,
                        "semantic": True,
                        "memories": records,
                        "records": records,
                    }
                except Exception as exc:
                    semantic_error = str(exc)
        result = list_memory_records(
            state,
            team_id=team_id,
            project_id=_optional_text(payload.get("project_id")),
            codebase_id=_optional_text(payload.get("codebase_id")),
            agent_id=_optional_text(payload.get("agent_id")),
            kinds=kinds,
            statuses=statuses,
            tags_any=tags_any,
            query_text=query_text,
            include_archived=TeamMemoryStatus.ARCHIVED.value in set(statuses),
            limit=limit or DEFAULT_MEMORY_LIMIT,
        )
        records = [
            memory_record_payload(memory, include_body=False) for memory in result.pop("records")
        ]
        response = {
            "success": True,
            "revision": state.revision,
            "semantic": False,
            "memories": records,
            "records": records,
            **result,
        }
        if semantic_error is not None:
            response["semantic_error"] = semantic_error
        return response


def _team_memory_id_selection(payload: Mapping[str, Any]) -> list[str]:
    memory_ids: list[str] = []
    memory_id = _optional_text(payload.get("memory_id"))
    if memory_id is not None:
        memory_ids.append(memory_id)
    for item in _string_list(payload.get("memory_ids"), "memory_ids"):
        if item not in memory_ids:
            memory_ids.append(item)
    return memory_ids


def _team_memory_records_by_ids(
    state: TeamState,
    *,
    memory_ids: list[str],
    team_id: str | None,
    project_id: str | None,
) -> tuple[list[TeamMemoryRecord], list[str]]:
    records: list[TeamMemoryRecord] = []
    errors: list[str] = []
    for memory_id in memory_ids:
        memory = state.memories.get(memory_id)
        if memory is None:
            errors.append(f"memory {memory_id} does not exist")
            continue
        if team_id is not None and memory.team_id != team_id:
            errors.append(f"memory {memory_id} belongs to team {memory.team_id}, not {team_id}")
            continue
        if not _team_memory_matches_project_filter(memory, project_id):
            errors.append(f"memory {memory_id} does not match project filter {project_id}")
            continue
        records.append(memory)
    return records, errors


def _team_memory_records_for_team(
    state: TeamState,
    *,
    team_id: str,
    project_id: str | None,
) -> list[TeamMemoryRecord]:
    records = [
        memory
        for memory in state.memories.values()
        if memory.team_id == team_id and _team_memory_matches_project_filter(memory, project_id)
    ]
    records.sort(key=lambda memory: (memory.updated_at, memory.id))
    return records


def _team_memory_matches_project_filter(
    memory: TeamMemoryRecord,
    project_id: str | None,
) -> bool:
    if project_id is None:
        return True
    return memory.scope == TeamMemoryScope.TEAM_GLOBAL or memory.project_id == project_id


def _sync_team_memory_records_to_semantic_index(
    context: Any,
    *,
    state: TeamState,
    records: list[TeamMemoryRecord],
    directory_path: str | None,
    wait: bool,
    initial_errors: list[str] | None = None,
) -> dict[str, Any]:
    service = TeamMemorySemanticService(context)
    index_results: list[dict[str, Any]] = []
    delete_results: list[dict[str, Any]] = []
    errors = list(initial_errors or [])
    for memory in records:
        if memory.status == TeamMemoryStatus.ACTIVE:
            try:
                index_results.append(
                    service.index_memory(
                        memory,
                        directory_path=directory_path,
                        wait=wait,
                    )
                )
            except Exception as exc:
                errors.append(f"{memory.id}: {exc}")
            continue
        try:
            delete_results.append(
                service.delete_memory(
                    team_id=memory.team_id,
                    memory_id=memory.id,
                    directory_path=directory_path,
                    wait=wait,
                )
            )
        except ValueError as exc:
            if "does not exist" in str(exc):
                delete_results.append(
                    {
                        "success": True,
                        "memory_id": memory.id,
                        "team_id": memory.team_id,
                        "skipped": True,
                        "reason": str(exc),
                    }
                )
            else:
                errors.append(f"{memory.id}: {exc}")
        except Exception as exc:
            errors.append(f"{memory.id}: {exc}")
    return {
        "success": not errors,
        "revision": state.revision,
        "indexed_count": len(index_results),
        "deleted_count": len(delete_results),
        "memory_ids": [memory.id for memory in records],
        "index_results": index_results,
        "delete_results": delete_results,
        "errors": errors,
    }
