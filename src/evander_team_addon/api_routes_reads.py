"""Team reads endpoints."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import (
    Any,
)

from starlette.concurrency import (
    run_in_threadpool,
)
from starlette.requests import (
    Request,
)
from starlette.responses import (
    Response,
    StreamingResponse,
)

from evander.models import (
    utc_now,
)

from .api_constants import (
    _BOOTSTRAP_COLLECTIONS,
    TEAM_VIEW_IDS,
    JSONResponse,
    _view_read_collections,
)
from .api_context import TeamApiDependencies
from .api_projection import (
    _bootstrap_payload_for_request,
    _inbox_history_payload_for_request,
    _registered_communication_target_options,
    _team_costs_payload,
    _timeline_payload_for_request,
    _view_payload_for_request,
)
from .api_requests import (
    _expected_revision,
    _privileged_operations_locked_response,
    _query_bool,
    _query_limited_int,
    _query_non_negative_int,
    _query_text,
    _read_payload,
    _session_repository_from_context,
    _state_revision_payload,
    _state_snapshot_events,
)
from .templates import (
    get_template,
    template_catalog,
)


def build_reads_handlers(
    dependencies: TeamApiDependencies,
) -> dict[str, Callable[[Request], Awaitable[Response]]]:
    context = dependencies.context
    repository = dependencies.repository
    admin_service = dependencies.admin_service
    _state_payload = dependencies._state_payload
    _state_response = dependencies._state_response

    async def get_state(request: Request) -> JSONResponse:
        return await run_in_threadpool(
            lambda: JSONResponse(_state_payload(request, repository.read()))
        )

    async def get_bootstrap(request: Request) -> JSONResponse:
        def response() -> JSONResponse:
            state = repository.read_projection(collections=sorted(_BOOTSTRAP_COLLECTIONS))
            return JSONResponse(
                _bootstrap_payload_for_request(
                    context.auth,
                    request,
                    state,
                    communication_targets=_registered_communication_target_options(context),
                )
            )

        return await run_in_threadpool(response)

    async def get_inbox_history(request: Request) -> JSONResponse:
        return JSONResponse(_inbox_history_payload_for_request(request, repository.read()))

    async def get_dispatch_events(request: Request) -> JSONResponse:
        state = repository.read()
        team_id = _query_text(request, "team_id")
        agent_id = _query_text(request, "agent_id")
        dispatch_id = _query_text(request, "dispatch_id")
        tool_name = _query_text(request, "tool_name")
        status = _query_text(request, "status")
        search_text = _query_text(request, "q")
        limit = _query_limited_int(
            request,
            "limit",
            default=100,
            max_value=200,
        )
        offset = _query_non_negative_int(request, "offset", default=0)
        rows = repository.list_dispatch_activity(
            dispatch_id=dispatch_id,
            agent_id=agent_id,
            tool_name=tool_name,
            newest_first=True,
        )
        if team_id:
            team_agent_ids = {
                agent.id for agent in state.agents.values() if agent.team_id == team_id
            }
            rows = [row for row in rows if row.agent_id in team_agent_ids]
        if status:
            rows = [row for row in rows if row.status.value == status]
        if search_text:
            needle = search_text.casefold()
            rows = [
                row
                for row in rows
                if needle
                in " ".join(
                    value
                    for value in (
                        row.id,
                        row.dispatch_id or "",
                        row.agent_id or "",
                        row.tool_name,
                        row.error_code or "",
                        row.error_message or "",
                        row.correction_context_summary or "",
                    )
                ).casefold()
            ]
        total_count = len(rows)
        page = rows[offset : offset + limit]

        def _event_payload(row: Any) -> dict[str, Any]:
            agent = state.agents.get(row.agent_id or "")
            if row.tool_name == "team_dispatch_started":
                kind = "dispatch_started"
                summary = "Dispatch started"
            elif row.tool_name == "team_dispatch_finished":
                kind = "dispatch_finished"
                summary = (
                    "Dispatch finished with progress"
                    if row.progress_classification != "none"
                    else "Dispatch finished without recorded progress"
                )
            elif row.status.value == "failure":
                kind = "tool_failed"
                summary = row.error_message or f"{row.tool_name} failed"
            else:
                kind = "tool_completed"
                summary = f"{row.tool_name} completed"
            return {
                "id": row.id,
                "dispatch_id": row.dispatch_id,
                "agent_id": row.agent_id,
                "agent_display_name": (
                    (agent.display_name or agent.id) if agent is not None else row.agent_id
                ),
                "team_id": agent.team_id if agent is not None else None,
                "kind": kind,
                "tool_name": (
                    None
                    if row.tool_name in {"team_dispatch_started", "team_dispatch_finished"}
                    else row.tool_name
                ),
                "status": row.status.value,
                "progress_classification": row.progress_classification,
                "summary": summary,
                "error_code": row.error_code,
                "correction_context_summary": row.correction_context_summary,
                "occurred_at": row.created_at.isoformat(),
            }

        return JSONResponse(
            {
                "revision": state.revision,
                "generated_at": utc_now().isoformat(),
                "retention_days": 30,
                "filters": {
                    key: value
                    for key, value in {
                        "team_id": team_id,
                        "agent_id": agent_id,
                        "dispatch_id": dispatch_id,
                        "tool_name": tool_name,
                        "status": status,
                        "q": search_text,
                    }.items()
                    if value is not None
                },
                "total_count": total_count,
                "limit": limit,
                "offset": offset,
                "has_more": offset + len(page) < total_count,
                "events": [_event_payload(row) for row in page],
            }
        )

    async def get_view(request: Request) -> JSONResponse:
        view = request.path_params["view"]
        if view not in TEAM_VIEW_IDS:
            return JSONResponse({"error": "view not found"}, status_code=404)

        def response() -> JSONResponse:
            state = repository.read_projection(collections=sorted(_view_read_collections(view)))
            return JSONResponse(
                _view_payload_for_request(
                    context.auth,
                    request,
                    state,
                    view=view,
                    runtime_dir=context.runtime_dir,
                    session_repository=_session_repository_from_context(context),
                )
            )

        # Projection and JSON encoding can cost more than the database read.
        # Keep the complete synchronous pipeline off the ASGI event loop.
        return await run_in_threadpool(response)

    async def stream_state(request: Request) -> StreamingResponse:
        return StreamingResponse(
            _state_snapshot_events(
                lambda: _state_revision_payload(repository.read_revision()),
                request,
            ),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    async def list_templates(_request: Request) -> JSONResponse:
        return JSONResponse(
            {"templates": [template.model_dump(mode="json") for template in template_catalog()]}
        )

    async def get_template_details(request: Request) -> JSONResponse:
        template = get_template(request.path_params["template_id"])
        if template is None:
            return JSONResponse({"error": "template not found"}, status_code=404)
        return JSONResponse(template.model_dump(mode="json"))

    async def materialize_template(request: Request) -> JSONResponse:
        template_id = request.path_params["template_id"]
        if get_template(template_id) is None:
            return JSONResponse({"error": "template not found"}, status_code=404)
        payload = await _read_payload(request)
        state, result = admin_service.materialize_template(
            template_id,
            inputs=payload,
            expected_revision=_expected_revision(payload),
        )
        return _state_response(
            request,
            state,
            status_code=201,
            extra={"materialized": result},
        )

    async def get_timeline(request: Request) -> JSONResponse:
        state = repository.read()
        return JSONResponse(_timeline_payload_for_request(request, state))

    async def get_costs(request: Request) -> JSONResponse:
        backfill = _query_bool(request, "backfill")
        if backfill is True:
            if locked_response := _privileged_operations_locked_response(context, request):
                return locked_response
        payload = await run_in_threadpool(
            _team_costs_payload,
            context,
            repository,
            backfill_transcripts=backfill is True,
        )
        return JSONResponse(payload)

    async def backfill_costs(request: Request) -> JSONResponse:
        payload = await run_in_threadpool(
            _team_costs_payload,
            context,
            repository,
            backfill_transcripts=True,
        )
        return JSONResponse(payload)

    return {
        "get_state": get_state,
        "get_bootstrap": get_bootstrap,
        "get_inbox_history": get_inbox_history,
        "get_dispatch_events": get_dispatch_events,
        "get_view": get_view,
        "stream_state": stream_state,
        "list_templates": list_templates,
        "get_template_details": get_template_details,
        "materialize_template": materialize_template,
        "get_timeline": get_timeline,
        "get_costs": get_costs,
        "backfill_costs": backfill_costs,
    }
