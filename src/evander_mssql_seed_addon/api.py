from __future__ import annotations

from collections.abc import Sequence
import json
from typing import Any

from pydantic import ValidationError
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from evander.addons import AddonContext
from evander.admin.web.rbac import ADMIN_OWNER_PERMISSION

from .state import MssqlSeedStateError, MssqlSeedStore


def admin_api_routes(context: AddonContext) -> Sequence[Route]:
    store = MssqlSeedStore(context.runtime_dir)

    async def health(_: Request) -> JSONResponse:
        state = store.read()
        return JSONResponse(
            {
                "status": "ok",
                "sidecar_count": len(state.sidecars),
                "seed_count": len(state.seeds),
                "database_count": len(state.databases),
            }
        )

    async def get_settings(_: Request) -> JSONResponse:
        state = store.read()
        return JSONResponse(
            {
                "sidecars": [
                    sidecar.model_dump(mode="json")
                    for sidecar in sorted(state.sidecars.values(), key=lambda item: item.sidecar_id)
                ],
                "seeds": [
                    seed.model_dump(mode="json")
                    for seed in sorted(state.seeds.values(), key=lambda item: item.seed_alias)
                ],
            }
        )

    async def put_settings(request: Request) -> JSONResponse:
        permission_error = _require_settings_write_permission(context, request)
        if permission_error is not None:
            return permission_error
        try:
            payload = await _read_payload(request)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        try:
            state = store.replace_settings(
                sidecars=_object_or_array(payload.get("sidecars"), "sidecars"),
                seeds=_object_or_array(payload.get("seeds"), "seeds"),
            )
        except (MssqlSeedStateError, ValidationError, ValueError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return JSONResponse(
            {
                "sidecars": [
                    sidecar.model_dump(mode="json")
                    for sidecar in sorted(state.sidecars.values(), key=lambda item: item.sidecar_id)
                ],
                "seeds": [
                    seed.model_dump(mode="json")
                    for seed in sorted(state.seeds.values(), key=lambda item: item.seed_alias)
                ],
            }
        )

    async def list_databases(request: Request) -> JSONResponse:
        session_id = str(request.query_params.get("session_id") or "").strip() or None
        try:
            databases = store.list_databases(session_id=session_id)
        except (MssqlSeedStateError, ValidationError, ValueError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return JSONResponse(
            {"databases": [database.model_dump(mode="json") for database in databases]}
        )

    return [
        Route("/health", health, methods=["GET"]),
        Route("/settings", get_settings, methods=["GET"]),
        Route("/settings", put_settings, methods=["PUT"]),
        Route("/databases", list_databases, methods=["GET"]),
    ]


async def _read_payload(request: Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except json.JSONDecodeError as exc:
        raise ValueError(f"Request body must be valid JSON: {exc.msg}") from exc
    if not isinstance(payload, dict):
        raise ValueError("Request body must be a JSON object")
    return payload


def _require_settings_write_permission(
    context: AddonContext, request: Request
) -> JSONResponse | None:
    auth = context.auth
    require_permission = getattr(auth, "require_permission", None)
    if not callable(require_permission):
        return JSONResponse({"error": "Admin owner permission is required"}, status_code=403)
    try:
        require_permission(request, ADMIN_OWNER_PERMISSION)
    except PermissionError as exc:
        return JSONResponse({"error": str(exc)}, status_code=403)
    return None


def _object_or_array(value: Any, field_name: str) -> dict[str, Any] | list[Any]:
    if value is None:
        return {}
    if isinstance(value, (dict, list)):
        return value
    raise MssqlSeedStateError(f"{field_name} must be a JSON object or array")
