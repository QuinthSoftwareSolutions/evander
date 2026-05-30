from __future__ import annotations

from datetime import datetime
from typing import Any

from starlette.datastructures import UploadFile
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

    async def upload_artifact(request: Request) -> JSONResponse:
        try:
            form = await request.form()
        except Exception:
            return json_error("Request body must be multipart form data")
        key = _optional_string(form.get("key"))
        if key is None:
            return json_error("key is required")
        file_value = form.get("file")
        if not isinstance(file_value, UploadFile):
            return json_error("file is required")
        try:
            content = await file_value.read()
            payload = service.upload_published_artifact(
                key=key,
                filename=file_value.filename or "artifact",
                content=content,
                artifact_path=_optional_string(form.get("artifact_path")),
                metadata={"source_route": "/api/admin/artifacts/uploads"},
            )
        except (FileNotFoundError, ValueError) as exc:
            return json_error(str(exc))
        return JSONResponse(payload.model_dump(mode="json"), status_code=201)

    return [
        Route("/api/admin/artifacts", list_artifacts, methods=["GET"]),
        Route("/api/admin/artifacts/uploads", upload_artifact, methods=["POST"]),
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


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
