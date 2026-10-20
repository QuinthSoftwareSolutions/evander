from __future__ import annotations

from datetime import datetime
from typing import Any

from starlette.datastructures import UploadFile
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import BaseRoute, Route

from evander.admin.composition import AdminRuntimeComponents
from evander.admin.web.shared import AdminWebAuth, json_error
from evander.artifact_spaces import ArtifactSpaceConflictError
from evander.communication_artifact_links import (
    CommunicationArtifactLinkError,
    PUBLIC_COMMUNICATION_ARTIFACT_DOWNLOAD_PREFIX,
    PUBLIC_COMMUNICATION_PERSONA_AVATAR_PREFIX,
    communication_artifact_link_secret_env,
    verify_communication_artifact_download_token,
    verify_communication_persona_avatar_token,
)
from evander.communication_artifacts import CommunicationArtifactStore
from evander.discord_webhooks import CommunicationPersonaAvatarStore


def artifact_routes(service: AdminRuntimeComponents, auth: AdminWebAuth) -> list[BaseRoute]:
    async def list_artifacts(request: Request) -> JSONResponse:
        params = request.query_params
        try:
            subject = _request_artifact_space_subject(auth, request)
        except PermissionError as exc:
            return json_error(str(exc), status_code=401)
        try:
            published_after = _parse_datetime(params.get("published_after"))
            published_before = _parse_datetime(params.get("published_before"))
            limit = int(params.get("limit") or "50")
            payload = service.artifact_admin.list_published_artifacts(
                key=params.get("key") or None,
                published_after=published_after,
                published_before=published_before,
                limit=limit,
                cursor=params.get("cursor") or None,
                space_path=params.get("space_path") or None,
                space_recursive=_parse_bool(params.get("space_recursive"), default=True),
                subject=subject,
            )
        except ValueError as exc:
            return json_error(str(exc))
        return JSONResponse(payload.model_dump(mode="json"))

    async def get_artifact(request: Request) -> JSONResponse:
        key = request.path_params.get("key", "")
        try:
            payload = service.artifact_admin.get_published_artifact(key)
        except FileNotFoundError as exc:
            return json_error(str(exc), status_code=404)
        except ValueError as exc:
            return json_error(str(exc))
        return JSONResponse(payload.model_dump(mode="json"))

    async def download_artifact(request: Request) -> FileResponse | JSONResponse:
        key = request.path_params.get("key", "")
        try:
            artifact = service.artifact_admin.published_artifact_file(key)
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
        if artifact.metadata.provider == "workflow":
            # Host workflow files retain their signing directory independently
            # of the optional sender identity or a live source agent session.
            directory_path = artifact.metadata.metadata.get("directory_path")
            if not isinstance(directory_path, str) or not directory_path.strip():
                return json_error("Invalid or expired artifact download link", status_code=403)
            try:
                resolved_config = service.runtime.provider_config.resolve_for_directory(
                    directory_path
                )
            except ValueError:
                return json_error("Invalid or expired artifact download link", status_code=403)
        else:
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
        inline_png = (
            artifact.metadata.mime_type == "image/png"
            and artifact.metadata.metadata.get("purpose") == "teams_progress_image"
        )
        if inline_png:
            with artifact.blob_path.open("rb") as image:
                inline_png = image.read(8) == b"\x89PNG\r\n\x1a\n"
        return FileResponse(
            artifact.blob_path,
            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
            media_type=artifact.metadata.mime_type or "application/octet-stream",
            filename=artifact.metadata.filename,
            content_disposition_type="inline" if inline_png else "attachment",
        )

    async def download_communication_persona_avatar(
        request: Request,
    ) -> FileResponse | JSONResponse:
        sha256 = str(request.path_params.get("sha256") or "").strip()
        token = str(request.query_params.get("token") or "").strip()
        if not token:
            return json_error("Invalid or expired persona avatar link", status_code=403)
        try:
            digest = verify_communication_persona_avatar_token(
                sha256=sha256,
                token=token,
                secret_env=communication_artifact_link_secret_env(service.config),
            )
            path, mime_type = CommunicationPersonaAvatarStore(service.config.runtime_dir).get(
                digest
            )
        except (CommunicationArtifactLinkError, ValueError):
            return json_error("Invalid or expired persona avatar link", status_code=403)
        except FileNotFoundError:
            return json_error("Persona avatar not found", status_code=404)
        return FileResponse(
            path,
            headers={"Cache-Control": "public, max-age=604800, immutable"},
            media_type=mime_type,
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
            payload = service.artifact_admin.upload_published_artifact(
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
            subject = _request_artifact_space_subject(auth, request)
        except PermissionError as exc:
            return json_error(str(exc), status_code=401)
        try:
            payload = service.artifact_admin.list_artifact_spaces(
                path=params.get("path") or None,
                recursive=_parse_bool(params.get("recursive"), default=True),
                subject=subject,
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
            subject = _request_artifact_space_subject(auth, request)
        except PermissionError as exc:
            return json_error(str(exc), status_code=401)
        try:
            result = service.artifact_admin.create_artifact_space(
                path=_required_string(payload.get("path"), "path"),
                display_name=_optional_string(payload.get("display_name")),
                metadata=_optional_object(payload.get("metadata"), "metadata"),
                grants=_optional_object(payload.get("grants"), "grants"),
                subject=subject,
                expected_revision=_optional_int(payload.get("expected_revision")),
            )
        except ArtifactSpaceConflictError as exc:
            return _artifact_space_conflict_response(exc)
        except (PermissionError, ValueError) as exc:
            status_code = 403 if isinstance(exc, PermissionError) else 400
            return json_error(str(exc), status_code=status_code)
        return JSONResponse(result, status_code=201)

    async def publish_artifact_space_version(request: Request) -> JSONResponse:
        try:
            payload = await request.json()
        except Exception:
            return json_error("Request body must be a JSON object")
        if not isinstance(payload, dict):
            return json_error("Request body must be a JSON object")
        try:
            subject = _request_artifact_space_subject(auth, request)
        except PermissionError as exc:
            return json_error(str(exc), status_code=401)
        try:
            result = service.artifact_admin.publish_artifact_space_version(
                space_path=_required_string(payload.get("space_path"), "space_path"),
                artifact_id=_required_string(payload.get("artifact_id"), "artifact_id"),
                artifact_key=_required_string(payload.get("artifact_key"), "artifact_key"),
                subject=subject,
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
            status_code = 403 if isinstance(exc, PermissionError) else 400
            return json_error(str(exc), status_code=status_code)
        return JSONResponse(result, status_code=201)

    async def latest_artifact_space_versions(request: Request) -> JSONResponse:
        params = request.query_params
        try:
            subject = _request_artifact_space_subject(auth, request)
        except PermissionError as exc:
            return json_error(str(exc), status_code=401)
        try:
            payload = service.artifact_admin.latest_artifact_space_versions(
                space_path=_required_string(params.get("space_path"), "space_path"),
                artifact_id=params.get("artifact_id") or None,
                subject=subject,
            )
        except FileNotFoundError as exc:
            return json_error(str(exc), status_code=404)
        except (PermissionError, ValueError) as exc:
            status_code = 403 if isinstance(exc, PermissionError) else 400
            return json_error(str(exc), status_code=status_code)
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
        Route(
            f"{PUBLIC_COMMUNICATION_PERSONA_AVATAR_PREFIX}/{{sha256:str}}",
            download_communication_persona_avatar,
            methods=["GET"],
        ),
        Route("/api/admin/artifact-downloads/{key:path}", download_artifact, methods=["GET"]),
        Route("/api/admin/artifacts/{key:path}", get_artifact, methods=["GET"]),
    ]


def _request_artifact_space_subject(auth: AdminWebAuth, request: Request) -> str:
    session = auth.require(request)
    subject = str(session.get("sub") or session.get("login") or "").strip()
    if not subject:
        raise PermissionError("Authenticated session subject is required")
    return subject


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
