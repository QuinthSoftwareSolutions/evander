"""Team memory endpoints."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from starlette.requests import (
    Request,
)
from starlette.responses import Response

from .api_constants import (
    JSONResponse,
)
from .api_context import TeamApiDependencies
from .api_requests import (
    _expected_revision,
    _memory_artifact_refs,
    _operator_identity_from_request,
    _optional,
    _optional_string_list,
    _query_bool,
    _query_limited_int,
    _query_non_negative_int,
    _query_text,
    _query_values,
    _read_payload,
    _required,
)
from .services.memory_service import (
    list_memory_records,
    memory_record_payload,
)


def build_memory_handlers(
    dependencies: TeamApiDependencies,
) -> dict[str, Callable[[Request], Awaitable[Response]]]:
    context = dependencies.context
    repository = dependencies.repository
    admin_service = dependencies.admin_service

    async def list_memories(request: Request) -> JSONResponse:
        state = repository.read()
        statuses = _query_values(request, "statuses") or _query_values(request, "status")
        include_archived = _query_bool(request, "include_archived") is True
        result = list_memory_records(
            state,
            team_id=_query_text(request, "team_id"),
            project_id=_query_text(request, "project_id"),
            codebase_id=_query_text(request, "codebase_id"),
            agent_id=_query_text(request, "agent_id"),
            kinds=_query_values(request, "kinds") or _query_values(request, "kind"),
            statuses=statuses,
            tags_any=_query_values(request, "tags_any") or _query_values(request, "tag"),
            query_text=_query_text(request, "query"),
            include_archived=include_archived,
            limit=_query_limited_int(request, "limit", default=20, max_value=50),
            offset=_query_non_negative_int(request, "offset", default=0),
        )
        records = [
            memory_record_payload(memory, include_body=False) for memory in result.pop("records")
        ]
        return JSONResponse({"revision": state.revision, "memories": records, **result})

    async def get_memory(request: Request) -> JSONResponse:
        state = repository.read()
        memory = state.memories.get(request.path_params["memory_id"])
        if memory is None:
            return JSONResponse({"error": "memory not found"}, status_code=404)
        return JSONResponse(
            {
                "revision": state.revision,
                "memory": memory_record_payload(memory, include_body=True),
            }
        )

    async def create_memory(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        state, memory = admin_service.create_or_update_memory(
            memory_id=_optional(payload, "id") or _optional(payload, "memory_id"),
            team_id=_required(payload, "team_id"),
            project_id=_optional(payload, "project_id"),
            codebase_id=_optional(payload, "codebase_id"),
            agent_id=_optional(payload, "agent_id"),
            scope=_optional(payload, "scope"),
            kind=_required(payload, "kind"),
            title=_required(payload, "title"),
            summary=_required(payload, "summary"),
            body=_optional(payload, "body"),
            tags=_optional_string_list(payload, "tags") or [],
            source_refs=_optional_string_list(payload, "source_refs") or [],
            artifact_refs=_memory_artifact_refs(payload),
            confidence=(
                float(payload["confidence"]) if payload.get("confidence") is not None else None
            ),
            supersedes_memory_id=_optional(payload, "supersedes_memory_id"),
            created_by=_operator_identity_from_request(context.auth, request)["actor"],
            expected_revision=_expected_revision(payload),
        )
        return JSONResponse(
            {
                "revision": state.revision,
                "memory": memory_record_payload(memory, include_body=True),
            },
            status_code=201,
        )

    async def patch_memory(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        state = repository.read()
        existing = state.memories.get(request.path_params["memory_id"])
        if existing is None:
            return JSONResponse({"error": "memory not found"}, status_code=404)
        next_payload = existing.model_dump(mode="json")
        for key in (
            "team_id",
            "project_id",
            "codebase_id",
            "agent_id",
            "scope",
            "kind",
            "title",
            "summary",
            "body",
            "tags",
            "source_refs",
            "artifact_refs",
            "confidence",
            "supersedes_memory_id",
        ):
            if key in payload:
                next_payload[key] = payload.get(key)
        if "artifact_space_ref" in payload:
            next_payload["artifact_space_ref"] = payload.get("artifact_space_ref")
            next_payload["artifact_refs"] = _memory_artifact_refs(next_payload)
        state, memory = admin_service.create_or_update_memory(
            memory_id=existing.id,
            team_id=_required(next_payload, "team_id"),
            project_id=_optional(next_payload, "project_id"),
            codebase_id=_optional(next_payload, "codebase_id"),
            agent_id=_optional(next_payload, "agent_id"),
            scope=_optional(next_payload, "scope"),
            kind=_required(next_payload, "kind"),
            title=_required(next_payload, "title"),
            summary=_required(next_payload, "summary"),
            body=_optional(next_payload, "body"),
            tags=_optional_string_list(next_payload, "tags") or [],
            source_refs=_optional_string_list(next_payload, "source_refs") or [],
            artifact_refs=_memory_artifact_refs(next_payload),
            confidence=(
                float(next_payload["confidence"])
                if next_payload.get("confidence") is not None
                else None
            ),
            supersedes_memory_id=_optional(next_payload, "supersedes_memory_id"),
            created_by=_operator_identity_from_request(context.auth, request)["actor"],
            expected_revision=_expected_revision(payload),
        )
        return JSONResponse(
            {
                "revision": state.revision,
                "memory": memory_record_payload(memory, include_body=True),
            }
        )

    async def archive_memory(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        state, memory = admin_service.archive_memory(
            memory_id=request.path_params["memory_id"],
            superseded_by_memory_id=_optional(payload, "superseded_by_memory_id"),
            expected_revision=_expected_revision(payload),
        )
        return JSONResponse(
            {
                "revision": state.revision,
                "memory": memory_record_payload(memory, include_body=True),
            }
        )

    return {
        "list_memories": list_memories,
        "get_memory": get_memory,
        "create_memory": create_memory,
        "patch_memory": patch_memory,
        "archive_memory": archive_memory,
    }
