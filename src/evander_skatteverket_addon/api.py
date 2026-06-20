from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from agent_sandbox.addons import AddonContext

from .catalog import catalog_payload
from .client import SkatteverketClient
from .repository import SkatteverketRepository, SkatteverketStateError


def admin_api_routes(context: AddonContext) -> Sequence[Route]:
    repo = SkatteverketRepository(context.runtime_dir)

    async def get_state(_request: Request) -> JSONResponse:
        return JSONResponse(repo.state_payload())

    async def get_catalog(_request: Request) -> JSONResponse:
        return JSONResponse(catalog_payload())

    async def list_connections(_request: Request) -> JSONResponse:
        return JSONResponse({"connections": repo.list_connections()})

    async def upsert_connection(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        try:
            connection = repo.upsert_connection(payload)
        except SkatteverketStateError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return JSONResponse({"connection": connection}, status_code=201)

    async def connection_status(_request: Request) -> JSONResponse:
        return JSONResponse(SkatteverketClient(repo.default_connection()).connection_status())

    async def list_drafts(_request: Request) -> JSONResponse:
        return JSONResponse({"drafts": repo.list_drafts()})

    async def create_draft(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        try:
            draft = repo.create_draft(
                api_name=_required(payload, "api_name"),
                period=_optional(payload, "period"),
                subject_id=_optional(payload, "subject_id"),
                source_session_id=_optional(payload, "source_session_id"),
                payload=_mapping(payload.get("payload")),
            )
        except SkatteverketStateError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return JSONResponse({"draft": draft}, status_code=201)

    async def validate_draft(request: Request) -> JSONResponse:
        draft_id = str(request.path_params["draft_id"])
        try:
            draft = repo.validate_draft(draft_id)
        except SkatteverketStateError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return JSONResponse({"draft": draft})

    async def list_submissions(_request: Request) -> JSONResponse:
        return JSONResponse({"submissions": repo.list_submissions()})

    return [
        Route("/state", get_state, methods=["GET"]),
        Route("/catalog", get_catalog, methods=["GET"]),
        Route("/connections", list_connections, methods=["GET"]),
        Route("/connections", upsert_connection, methods=["POST"]),
        Route("/connection-status", connection_status, methods=["GET"]),
        Route("/drafts", list_drafts, methods=["GET"]),
        Route("/drafts", create_draft, methods=["POST"]),
        Route("/drafts/{draft_id}/validate", validate_draft, methods=["POST"]),
        Route("/submissions", list_submissions, methods=["GET"]),
    ]


async def _read_payload(request: Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _required(payload: dict[str, Any], key: str) -> str:
    value = _optional(payload, key)
    if value is None:
        raise SkatteverketStateError(f"{key} is required")
    return value


def _optional(payload: dict[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _mapping(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise SkatteverketStateError("payload must be a JSON object")
    return dict(value)
