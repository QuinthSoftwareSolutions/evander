"""Team evidence endpoints."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from starlette.requests import (
    Request,
)
from starlette.responses import Response

from evander.admin.web.rbac import (
    ADMIN_OWNER_PERMISSION,
)

from .api_authorization import (
    _can_read_external_source_evidence,
    _external_source_evidence_project_directory_path,
)
from .api_constants import (
    JSONResponse,
)
from .api_context import TeamApiDependencies
from .api_requests import (
    _auth_can,
    _expected_revision,
    _operator_actor_from_request,
    _optional,
    _optional_iso_datetime,
    _optional_mapping_list,
    _optional_object,
    _optional_string_list,
    _query_text,
    _read_payload,
    _required,
    _required_iso_datetime,
)
from .models import (
    ExternalSourceEvidenceFreshnessStatus,
    external_source_evidence_snapshot_payload,
)


def build_evidence_handlers(
    dependencies: TeamApiDependencies,
) -> dict[str, Callable[[Request], Awaitable[Response]]]:
    context = dependencies.context
    repository = dependencies.repository
    admin_service = dependencies.admin_service

    async def list_external_source_evidence_snapshots(request: Request) -> JSONResponse:
        state = repository.read()
        rows = [
            row
            for row in state.external_source_evidence_snapshots.values()
            if _can_read_external_source_evidence(context.auth, request, state, row)
        ]
        team_id = _query_text(request, "team_id")
        project_id = _query_text(request, "project_id")
        provider_type = _query_text(request, "provider_type")
        source_type = _query_text(request, "source_type")
        external_identifier = _query_text(request, "external_identifier")
        if team_id is not None:
            rows = [row for row in rows if row.team_id == team_id]
        if project_id is not None:
            rows = [row for row in rows if row.project_id == project_id]
        if provider_type is not None:
            rows = [row for row in rows if row.provider_type == provider_type]
        if source_type is not None:
            rows = [row for row in rows if row.source_type == source_type]
        if external_identifier is not None:
            rows = [row for row in rows if row.external_identifier == external_identifier]
        rows.sort(key=lambda row: (row.fetched_at, row.created_at, row.id), reverse=True)
        return JSONResponse(
            {
                "revision": state.revision,
                "external_source_evidence_snapshots": [
                    external_source_evidence_snapshot_payload(row) for row in rows
                ],
            }
        )

    async def get_external_source_evidence_snapshot(request: Request) -> JSONResponse:
        state = repository.read()
        snapshot = state.external_source_evidence_snapshots.get(request.path_params["snapshot_id"])
        if snapshot is None:
            return JSONResponse(
                {"error": "external source evidence snapshot not found"},
                status_code=404,
            )
        if not _can_read_external_source_evidence(
            context.auth,
            request,
            state,
            snapshot,
        ):
            return JSONResponse(
                {"error": "external source evidence snapshot not found"},
                status_code=404,
            )
        return JSONResponse(
            {
                "revision": state.revision,
                "external_source_evidence_snapshot": (
                    external_source_evidence_snapshot_payload(snapshot)
                ),
            }
        )

    async def create_external_source_evidence_snapshot(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        current = repository.read()
        project_id = _optional(payload, "project_id")
        directory_path = _external_source_evidence_project_directory_path(
            current,
            project_id,
        )
        if not _auth_can(
            context.auth,
            request,
            ADMIN_OWNER_PERMISSION,
            directory_path=directory_path,
        ):
            return JSONResponse(
                {
                    "error": (
                        "External source evidence creation requires directory owner permission."
                    )
                },
                status_code=403,
            )
        raw_freshness_status = _optional(payload, "freshness_status")
        state, snapshot = admin_service.create_external_source_evidence_snapshot(
            snapshot_id=_optional(payload, "id") or _optional(payload, "snapshot_id"),
            team_id=_required(payload, "team_id"),
            project_id=project_id,
            provider_type=_required(payload, "provider_type"),
            source_type=_required(payload, "source_type"),
            external_identifier=_required(payload, "external_identifier"),
            external_version=_required(payload, "external_version"),
            fetched_at=_required_iso_datetime(payload, "fetched_at"),
            expires_at=_optional_iso_datetime(payload, "expires_at"),
            freshness_status=(
                ExternalSourceEvidenceFreshnessStatus(raw_freshness_status)
                if raw_freshness_status is not None
                else ExternalSourceEvidenceFreshnessStatus.UNKNOWN
            ),
            access_classification=_required(payload, "access_classification"),
            title=_optional(payload, "title"),
            body=_optional_object(payload, "body"),
            acceptance_criteria=_optional_mapping_list(payload, "acceptance_criteria"),
            discussion=_optional_mapping_list(payload, "discussion"),
            links=_optional_mapping_list(payload, "links"),
            metadata=_optional_object(payload, "metadata") or {},
            content_digest=_optional(payload, "content_digest"),
            case_ids=_optional_string_list(payload, "case_ids") or [],
            task_ids=_optional_string_list(payload, "task_ids") or [],
            created_by_actor=_operator_actor_from_request(context.auth, request),
            expected_revision=_expected_revision(payload),
        )
        return JSONResponse(
            {
                "revision": state.revision,
                "external_source_evidence_snapshot": (
                    external_source_evidence_snapshot_payload(snapshot)
                ),
            },
            status_code=201,
        )

    async def attach_external_source_evidence_snapshot(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        current = repository.read()
        existing = current.external_source_evidence_snapshots.get(
            request.path_params["snapshot_id"]
        )
        if existing is None:
            return JSONResponse(
                {"error": "external source evidence snapshot not found"},
                status_code=404,
            )
        if not _can_read_external_source_evidence(
            context.auth,
            request,
            current,
            existing,
        ):
            return JSONResponse(
                {"error": "external source evidence snapshot not found"},
                status_code=404,
            )
        state, snapshot = admin_service.attach_external_source_evidence_snapshot(
            snapshot_id=request.path_params["snapshot_id"],
            case_ids=_optional_string_list(payload, "case_ids") or [],
            task_ids=_optional_string_list(payload, "task_ids") or [],
            expected_revision=_expected_revision(payload),
        )
        return JSONResponse(
            {
                "revision": state.revision,
                "external_source_evidence_snapshot": (
                    external_source_evidence_snapshot_payload(snapshot)
                ),
            }
        )

    return {
        "list_external_source_evidence_snapshots": list_external_source_evidence_snapshots,
        "get_external_source_evidence_snapshot": get_external_source_evidence_snapshot,
        "create_external_source_evidence_snapshot": create_external_source_evidence_snapshot,
        "attach_external_source_evidence_snapshot": attach_external_source_evidence_snapshot,
    }
