"""Team avatars endpoints."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from starlette.datastructures import (
    UploadFile,
)
from starlette.requests import (
    Request,
)
from starlette.responses import (
    FileResponse,
    Response,
)

from .api_constants import (
    JSONResponse,
)
from .api_context import TeamApiDependencies
from .api_requests import (
    _coerce_int,
    _current_agent_avatar_asset_id,
    _expected_revision,
    _read_payload,
    _required,
)
from .avatars import (
    AVATAR_MIME_TYPE,
    MAX_AVATAR_UPLOAD_BYTES,
    build_avatar_prompt,
    normalize_prompt_note,
    normalize_style_preset,
    openai_avatar_generator_from_config,
)
from .repository import (
    TeamStateConflictError,
)


def build_avatars_handlers(
    dependencies: TeamApiDependencies,
) -> dict[str, Callable[[Request], Awaitable[Response]]]:
    context = dependencies.context
    repository = dependencies.repository
    admin_service = dependencies.admin_service
    avatar_storage = dependencies.avatar_storage
    _state_response = dependencies._state_response

    async def create_avatar_preview(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        state = repository.read()
        agent_id = request.path_params["agent_id"]
        agent = state.agents.get(agent_id)
        if agent is None:
            return JSONResponse({"error": "agent not found"}, status_code=404)
        role = state.roles.get(agent.role_id) if agent.role_id is not None else None
        team = state.teams.get(agent.team_id) if agent.team_id is not None else None
        style_preset = normalize_style_preset(payload.get("style_preset"))
        prompt_note = normalize_prompt_note(payload.get("prompt_note"))
        prompt = build_avatar_prompt(
            agent=agent,
            role_name=role.name if role is not None else None,
            team_name=team.name if team is not None else None,
            style_preset=style_preset,
            prompt_note=prompt_note,
        )
        generator = openai_avatar_generator_from_config(context.config)
        try:
            content = await generator.generate(prompt=prompt)
        except RuntimeError as exc:
            return JSONResponse({"error": str(exc)}, status_code=503)
        try:
            preview = avatar_storage.store_generated_preview(
                agent_id=agent.id,
                content=content,
                style_preset=style_preset,
                prompt_note=prompt_note,
                model=generator.model,
            )
        except RuntimeError as exc:
            return JSONResponse({"error": str(exc)}, status_code=503)
        return JSONResponse({"preview": preview.payload()}, status_code=201)

    async def upload_agent_avatar(request: Request) -> JSONResponse:
        try:
            form = await request.form()
        except Exception as exc:
            raise ValueError("Request body must be multipart form data") from exc
        expected_revision = _coerce_int(form.get("expected_revision"), "expected_revision")
        file_value = form.get("file")
        if not isinstance(file_value, UploadFile):
            raise ValueError("file is required")
        content = await file_value.read(MAX_AVATAR_UPLOAD_BYTES + 1)
        try:
            avatar = avatar_storage.store_upload(content)
        except RuntimeError as exc:
            return JSONResponse({"error": str(exc)}, status_code=503)
        old_asset_id = _current_agent_avatar_asset_id(
            repository.read(),
            request.path_params["agent_id"],
        )
        try:
            state = admin_service.set_agent_avatar(
                agent_id=request.path_params["agent_id"],
                avatar=avatar,
                expected_revision=expected_revision,
            )
        except Exception:
            avatar_storage.delete_asset_if_unreferenced(
                asset_id=avatar.asset_id,
                state=repository.read(),
            )
            raise
        avatar_storage.delete_asset_if_unreferenced(asset_id=old_asset_id, state=state)
        return _state_response(request, state)

    async def accept_agent_avatar_preview(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        expected_revision = _expected_revision(payload)
        agent_id = request.path_params["agent_id"]
        before = repository.read()
        if agent_id not in before.agents:
            return JSONResponse({"error": "agent not found"}, status_code=404)
        if before.revision != expected_revision:
            raise TeamStateConflictError(
                expected_revision=expected_revision,
                current_revision=before.revision,
            )
        old_asset_id = _current_agent_avatar_asset_id(before, agent_id)
        try:
            avatar = avatar_storage.avatar_from_preview(
                preview_id=_required(payload, "preview_id"),
                agent_id=agent_id,
            )
        except FileNotFoundError:
            return JSONResponse({"error": "avatar preview not found"}, status_code=404)
        state = admin_service.set_agent_avatar(
            agent_id=agent_id,
            avatar=avatar,
            expected_revision=expected_revision,
        )
        avatar_storage.promote_preview(preview_id=avatar.preview_id or avatar.asset_id)
        avatar_storage.delete_asset_if_unreferenced(asset_id=old_asset_id, state=state)
        return _state_response(request, state)

    async def delete_agent_avatar(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        old_asset_id = _current_agent_avatar_asset_id(
            repository.read(),
            request.path_params["agent_id"],
        )
        state = admin_service.clear_agent_avatar(
            agent_id=request.path_params["agent_id"],
            expected_revision=_expected_revision(payload),
        )
        avatar_storage.delete_asset_if_unreferenced(asset_id=old_asset_id, state=state)
        return _state_response(request, state)

    async def get_avatar_asset(request: Request) -> FileResponse | JSONResponse:
        try:
            path = avatar_storage.asset_path(request.path_params["asset_id"])
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        if not path.is_file():
            return JSONResponse({"error": "avatar not found"}, status_code=404)
        return FileResponse(path, media_type=AVATAR_MIME_TYPE)

    async def get_avatar_preview(request: Request) -> FileResponse | JSONResponse:
        try:
            path = avatar_storage.preview_path(request.path_params["preview_id"])
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        if not path.is_file():
            return JSONResponse({"error": "avatar preview not found"}, status_code=404)
        return FileResponse(path, media_type=AVATAR_MIME_TYPE)

    return {
        "create_avatar_preview": create_avatar_preview,
        "upload_agent_avatar": upload_agent_avatar,
        "accept_agent_avatar_preview": accept_agent_avatar_preview,
        "delete_agent_avatar": delete_agent_avatar,
        "get_avatar_asset": get_avatar_asset,
        "get_avatar_preview": get_avatar_preview,
    }
