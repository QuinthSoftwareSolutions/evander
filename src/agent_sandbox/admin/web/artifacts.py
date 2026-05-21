from __future__ import annotations

from datetime import datetime
from typing import Any

from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import BaseRoute, Route

from agent_sandbox.admin.service import AdminService
from agent_sandbox.admin.web.shared import json_error


def artifact_routes(service: AdminService) -> list[BaseRoute]:
    async def list_artifacts(request: Request) -> JSONResponse:
        params = request.query_params
        try:
            published_after = _parse_datetime(params.get("published_after"))
            published_before = _parse_datetime(params.get("published_before"))
            limit = int(params.get("limit") or "50")
            payload = service.list_published_artifacts(
                key=params.get("key") or None,
                published_after=published_after,
                published_before=published_before,
                limit=limit,
                cursor=params.get("cursor") or None,
            )
        except ValueError as exc:
            return json_error(str(exc))
        return JSONResponse(payload.model_dump(mode="json"))

    async def get_artifact(request: Request) -> JSONResponse:
        key = request.path_params.get("key", "")
        try:
            payload = service.get_published_artifact(key)
        except FileNotFoundError as exc:
            return json_error(str(exc), status_code=404)
        except ValueError as exc:
            return json_error(str(exc))
        return JSONResponse(payload.model_dump(mode="json"))

    async def download_artifact(request: Request) -> FileResponse | JSONResponse:
        key = request.path_params.get("key", "")
        try:
            artifact = service.published_artifact_file(key)
        except FileNotFoundError as exc:
            return json_error(str(exc), status_code=404)
        except ValueError as exc:
            return json_error(str(exc))
        return FileResponse(
            artifact.blob_path,
            media_type=artifact.metadata.mime_type,
            filename=artifact.metadata.filename,
        )

    return [
        Route("/api/admin/artifacts", list_artifacts, methods=["GET"]),
        Route("/api/admin/artifact-downloads/{key:path}", download_artifact, methods=["GET"]),
        Route("/api/admin/artifacts/{key:path}", get_artifact, methods=["GET"]),
    ]


def _parse_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"Invalid datetime value: {value}") from exc
