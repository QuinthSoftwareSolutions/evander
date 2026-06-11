from __future__ import annotations

from datetime import datetime
from typing import Any

from starlette.datastructures import UploadFile
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import BaseRoute, Route

from agent_sandbox.admin.service import AdminService
from agent_sandbox.admin.web.shared import json_error
from agent_sandbox.artifact_spaces import ArtifactSpaceConflictError
from agent_sandbox.communication_artifact_links import (
    CommunicationArtifactLinkError,
    PUBLIC_COMMUNICATION_ARTIFACT_DOWNLOAD_PREFIX,
    communication_artifact_link_secret_env,
    verify_communication_artifact_download_token,
)
from agent_sandbox.communication_artifacts import CommunicationArtifactStore


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

    async def download_communication_artifact(request: Request) -> FileResponse | JSONResponse:
        artifact_id = request.path_params.get("artifact_id", "")
        token = str(request.query_params.get("token") or "").strip()
        if not token:
            return json_error("Invalid or expired artifact download link", status_code=403)
        try:
            artifact = CommunicationArtifactStore(service.config.runtime_dir).get(artifact_id)
        except ValueError:
            return json_error("Invalid or expired artifact download link", status_code=403)
        except FileNotFoundError as exc:
            return json_error(str(exc), status_code=404)
        try:
            resolved_config = service.runtime.provider_config.resolve_for_session_id(
                artifact.metadata.session_id
            )
        except ValueError:
            resolved_config = service.config
        try:
            verify_communication_artifact_download_token(
                artifact_id=artifact_id,
                token=token,
                secret_env=communication_artifact_link_secret_env(resolved_config),
            )
        except (CommunicationArtifactLinkError, ValueError):
            return json_error("Invalid or expired artifact download link", status_code=403)
        return FileResponse(
            artifact.blob_path,
            media_type=artifact.metadata.mime_type or "application/octet-stream",
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

    async def list_artifact_spaces(request: Request) -> JSONResponse:
        params = request.query_params
        try:
            payload = service.list_artifact_spaces(
                path=params.get("path") or None,
                recursive=_parse_bool(params.get("recursive"), default=True),
                subject=params.get("subject") or None,
                include_versions=_parse_bool(params.get("include_versions"), default=False),
            )
        except ValueError as exc:
            return json_error(str(exc))
        return JSONResponse(payload)

    async def create_artifact_space(request: Request) -> JSONResponse:
        try:
            payload = await request.json()
        except Exception:
            return json_error("Request body must be a JSON object")
        if not isinstance(payload, dict):
            return json_error("Request body must be a JSON object")
        try:
            result = service.create_artifact_space(
                path=_required_string(payload.get("path"), "path"),
                display_name=_optional_string(payload.get("display_name")),
                metadata=_optional_object(payload.get("metadata"), "metadata"),
                grants=_optional_object(payload.get("grants"), "grants"),
                subject=_optional_string(payload.get("subject")),
                expected_revision=_optional_int(payload.get("expected_revision")),
            )
        except ArtifactSpaceConflictError as exc:
            return _artifact_space_conflict_response(exc)
        except (PermissionError, ValueError) as exc:
            return json_error(str(exc))
        return JSONResponse(result, status_code=201)

    async def publish_artifact_space_version(request: Request) -> JSONResponse:
        try:
            payload = await request.json()
        except Exception:
            return json_error("Request body must be a JSON object")
        if not isinstance(payload, dict):
            return json_error("Request body must be a JSON object")
        try:
            result = service.publish_artifact_space_version(
                space_path=_required_string(payload.get("space_path"), "space_path"),
                artifact_id=_required_string(payload.get("artifact_id"), "artifact_id"),
                artifact_key=_required_string(payload.get("artifact_key"), "artifact_key"),
                subject=_optional_string(payload.get("subject")),
                metadata=_optional_object(payload.get("metadata"), "metadata"),
                expected_revision=_optional_int(payload.get("expected_revision")),
                require_existing_artifact=_parse_bool(
                    payload.get("require_existing_artifact"),
                    default=True,
                ),
            )
        except ArtifactSpaceConflictError as exc:
            return _artifact_space_conflict_response(exc)
        except FileNotFoundError as exc:
            return json_error(str(exc), status_code=404)
        except (PermissionError, ValueError) as exc:
            return json_error(str(exc))
        return JSONResponse(result, status_code=201)

    async def latest_artifact_space_versions(request: Request) -> JSONResponse:
        params = request.query_params
        try:
            payload = service.latest_artifact_space_versions(
                space_path=_required_string(params.get("space_path"), "space_path"),
                artifact_id=params.get("artifact_id") or None,
                subject=params.get("subject") or None,
            )
        except FileNotFoundError as exc:
            return json_error(str(exc), status_code=404)
        except (PermissionError, ValueError) as exc:
            return json_error(str(exc))
        return JSONResponse(payload)

    return [
        Route("/api/admin/artifacts", list_artifacts, methods=["GET"]),
        Route("/api/admin/artifacts/uploads", upload_artifact, methods=["POST"]),
        Route("/api/admin/artifact-spaces", list_artifact_spaces, methods=["GET"]),
        Route("/api/admin/artifact-spaces", create_artifact_space, methods=["POST"]),
        Route(
            "/api/admin/artifact-space-versions",
            publish_artifact_space_version,
            methods=["POST"],
        ),
        Route(
            "/api/admin/artifact-space-versions/latest",
            latest_artifact_space_versions,
            methods=["GET"],
        ),
        Route(
            f"{PUBLIC_COMMUNICATION_ARTIFACT_DOWNLOAD_PREFIX}/{{artifact_id:str}}",
            download_communication_artifact,
            methods=["GET"],
        ),
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


def _artifact_space_conflict_response(exc: ArtifactSpaceConflictError) -> JSONResponse:
    return json_error(
        str(exc),
        status_code=409,
        details={
            "expected_revision": exc.expected_revision,
            "current_revision": exc.current_revision,
        },
    )


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _required_string(value: Any, field_name: str) -> str:
    text = _optional_string(value)
    if text is None:
        raise ValueError(f"{field_name} is required")
    return text


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError("integer value is required")
    return int(value)


def _optional_object(value: Any, field_name: str) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} must be a JSON object")
    return dict(value)


def _parse_bool(value: Any, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if not text:
        return default
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    raise ValueError("boolean value is required")
