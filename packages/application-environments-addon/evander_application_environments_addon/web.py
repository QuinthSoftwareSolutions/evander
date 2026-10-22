"""Thin Admin Web transport for the application domain."""

from pathlib import Path
from starlette.concurrency import run_in_threadpool
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Route
from evander.models import DirectoryPath
from evander.admin.web.rbac import (
    APPLICATION_ENVIRONMENTS_MANAGE_PERMISSION as MANAGE,
    APPLICATION_ENVIRONMENTS_VIEW_PERMISSION as VIEW,
)
from evander.resources.models import ResourceError
from .models import Settings, CreateRequest
from .service import Service


def admin_api_routes(context):
    service = Service(context.runtime_dir, context.service)

    async def handle(request):
        try:
            directory = DirectoryPath(
                segments=request.query_params.get("directory", "root")
            ).as_posix()
            auth = context.auth.read_session(request) if context.auth.config.auth_required else None
            if auth is None:
                return JSONResponse({"error": "Admin sign-in required"}, status_code=401)
            context.auth.require_permission(
                auth, MANAGE if request.method != "GET" else VIEW, directory_path=directory
            )
            if request.method != "GET":
                from urllib.parse import urlsplit

                supplied = request.headers.get("origin")
                if (
                    request.headers.get("content-type", "").split(";")[0] != "application/json"
                    or supplied
                    and urlsplit(supplied).netloc != urlsplit(str(request.base_url)).netloc
                ):
                    raise ResourceError("Request origin or content type rejected")
            if request.url.path.endswith("/settings"):
                revision, value = service.store.settings(directory)
                if request.method == "PUT":
                    payload = await request.json()
                    if type(payload.get("expected_revision")) is not int:
                        raise ResourceError("expected_revision is required")
                    value = Settings.model_validate(payload["settings"])
                    revision = service.store.save_settings(
                        directory, payload["expected_revision"], value
                    )
                result = {"revision": revision, "settings": value.model_dump()}
            elif request.path_params.get("id"):
                id = request.path_params["id"]
                record = service.store.get(id, directory)
                result = (
                    await run_in_threadpool(service.close, id, directory=directory)
                    if request.method == "POST"
                    else await run_in_threadpool(
                        service.status, record["session"]["agent_session_id"], id, tool=None
                    )
                )
            elif request.method == "POST":
                payload = await request.json()
                parent = service.parent(payload["agent_session_id"])
                if parent.directory != directory:
                    raise ResourceError("Owner is outside this directory")
                result = await run_in_threadpool(
                    service.create,
                    parent.session_id,
                    CreateRequest.model_validate(payload["request"]),
                    tool=None,
                )
            else:
                result = {
                    "environments": [
                        service.receipt(record) for record in service.store.list(directory)
                    ]
                }
            return JSONResponse(result, headers={"Cache-Control": "no-store"})
        except PermissionError:
            return JSONResponse({"error": "Application permission required"}, status_code=403)
        except (ResourceError, ValueError, KeyError):
            return JSONResponse(
                {
                    "error": "Application request failed; refresh configuration or inspect resource status"
                },
                status_code=409,
            )

    base = ""
    return [
        Route(base + "/settings", handle, methods=["GET", "PUT"]),
        Route(base + "/environments", handle, methods=["GET", "POST"]),
        Route(base + "/environments/{id}", handle),
        Route(base + "/environments/{id}/close", handle, methods=["POST"]),
    ]


def admin_ui_routes(context):
    async def ui(request):
        return FileResponse(
            Path(__file__).parent / "ui_static/index.html",
            headers={
                "Cache-Control": "no-store",
                "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; base-uri 'none'; frame-ancestors 'self'",
            },
        )

    async def script(request):
        return FileResponse(
            Path(__file__).parent / "ui_static/environments.js",
            headers={"Cache-Control": "no-store"},
        )

    async def style(request):
        return FileResponse(Path(__file__).parent / "ui_static/environments.css")

    return [
        Route("/environments.css", style),
        Route("/", ui),
        Route("/environments.js", script),
    ]
