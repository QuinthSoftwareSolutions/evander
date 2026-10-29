from __future__ import annotations

import json
from urllib.parse import quote

from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse, RedirectResponse, Response
from starlette.routing import Route

from evander.admin.web.rbac import ADMIN_OWNER_PERMISSION, WORKFLOWS_READ_PERMISSION
from evander.models import DirectoryPath

from .models import FigmaConflict, FigmaError, Settings, digest
from .auth import OAuth
from .service import Actor, FigmaService
from .state import Store


def admin_api_routes(context):
    def service(request, *, write=False, directory=None):
        directory = DirectoryPath(
            segments=directory or request.query_params.get("directory", "root")
        ).as_posix()
        auth = context.auth
        if not callable(getattr(auth, "require_permission", None)):
            raise PermissionError("Admin authentication is required")
        login = auth.require(request).get("login", "operator")
        auth.require_permission(
            request,
            ADMIN_OWNER_PERMISSION if write else WORKFLOWS_READ_PERMISSION,
            directory_path=directory,
        )
        return FigmaService(
            context.runtime_dir, Actor(directory, "operator:" + login, operator=True)
        )

    async def body(request):
        data = bytearray()
        async for chunk in request.stream():
            data.extend(chunk)
            if len(data) > 1024 * 1024:
                raise FigmaError("Request exceeds the size limit")
        value = json.loads(data)
        if not isinstance(value, dict):
            raise FigmaError("Expected a JSON object")
        return value

    async def settings(request):
        api = service(request, write=True)
        if request.method == "PUT":
            payload = await body(request)
            revision = payload.get("expected_revision")
            if type(revision) is not int or revision < 0:
                return JSONResponse({"error": "expected_revision is required"}, status_code=428)
            parsed = Settings.model_validate(payload.get("settings"))
            await run_in_threadpool(api.store.save_settings, api.actor.directory, revision, parsed)
        revision, config = await run_in_threadpool(api.store.settings, api.actor.directory)
        statuses = {
            key: await run_in_threadpool(api.connection_status, key) for key in config.connections
        }
        return JSONResponse(
            {"revision": revision, "settings": config.model_dump(), "connections": statuses}
        )

    async def oauth_start(request):
        api = service(request, write=True)
        payload = await body(request)
        id = payload.get("connection_id")
        config = api.store.settings(api.actor.directory)[1].connections.get(id)
        if not config:
            raise FigmaError("Unknown connection")
        url = await run_in_threadpool(
            api.oauth.start, api.actor.directory, id, config, api.actor.session_id
        )
        return JSONResponse({"authorize_url": url})

    async def oauth_callback(request):
        # Bind state to the authenticated operator before resolving its directory.
        if not callable(getattr(context.auth, "require", None)):
            raise PermissionError("Admin authentication is required")
        login = context.auth.require(request).get("login", "operator")
        state = request.query_params.get("state", "")
        pending = OAuth(Store(context.runtime_dir)).pending(state, "operator:" + login)
        api = service(request, write=True, directory=pending["directory"])
        if request.query_params.get("error") or not request.query_params.get("code"):
            raise FigmaError("Figma authorization was not completed")
        issuer = request.query_params.get("iss")
        if issuer and issuer != "https://api.figma.com":
            raise FigmaError("Unexpected OAuth issuer")
        await run_in_threadpool(
            api.oauth.complete, state, request.query_params["code"], api.actor.session_id
        )
        return RedirectResponse(
            "/addons/figma-addon/?directory=" + quote(api.actor.directory, safe=""), status_code=303
        )

    async def disconnect(request):
        api = service(request, write=True)
        payload = await body(request)
        await run_in_threadpool(
            api.oauth.disconnect, api.actor.directory, payload.get("connection_id")
        )
        return JSONResponse({"disconnected": True})

    async def test(request):
        api = service(request, write=True)
        payload = await body(request)
        return JSONResponse(await run_in_threadpool(api.test_authoring, payload.get("target_id")))

    async def history(request):
        api = service(request)
        return JSONResponse(
            await run_in_threadpool(
                api.execute,
                {"operation": "history", "target_id": request.query_params.get("target_id")},
            )
        )

    async def preview(request):
        api = service(request)
        target_id = request.query_params.get("target_id")
        target, _, _, _ = api._target(target_id, "read")
        snapshot = api.store.get(
            "snapshot",
            request.path_params["snapshot_id"],
            api.actor.directory,
            target_id,
            target.file_key,
        )
        bundle = await run_in_threadpool(api._bundle, snapshot)
        return Response(
            bundle["preview.png"], media_type="image/png", headers={"Cache-Control": "no-store"}
        )

    async def reconcile(request):
        api = service(request, write=True)
        payload = await body(request)
        result = await run_in_threadpool(
            api.execute,
            {
                "operation": "reconcile",
                "target_id": payload.get("target_id"),
                "operation_id": payload.get("operation_id"),
            },
        )
        if result.get("reconciliation"):
            result["observation_digest"] = digest(result["reconciliation"])
        return JSONResponse(result)

    async def resolve(request):
        api = service(request, write=True)
        payload = await body(request)
        result = await run_in_threadpool(
            api.resolve_uncertain,
            payload.get("target_id"),
            payload.get("operation_id"),
            payload.get("observation_digest"),
        )
        return JSONResponse(result)

    def safe(endpoint):
        async def handle(request):
            try:
                return await endpoint(request)
            except PermissionError:
                return JSONResponse(
                    {"error": "Permission denied for this directory"}, status_code=403
                )
            except FigmaConflict as exc:
                return JSONResponse({"error": str(exc)}, status_code=409)
            except FigmaError as exc:
                return JSONResponse({"error": str(exc)}, status_code=400)
            except (ValidationError, ValueError, KeyError, TypeError):
                return JSONResponse({"error": "Invalid Figma request"}, status_code=400)

        return handle

    return [
        Route(path, safe(handler), methods=methods)
        for path, handler, methods in [
            ("/settings", settings, ["GET", "PUT"]),
            ("/oauth/start", oauth_start, ["POST"]),
            ("/oauth/callback", oauth_callback, ["GET"]),
            ("/disconnect", disconnect, ["POST"]),
            ("/test", test, ["POST"]),
            ("/history", history, ["GET"]),
            ("/snapshots/{snapshot_id}/preview", preview, ["GET"]),
            ("/reconcile", reconcile, ["POST"]),
            ("/resolve", resolve, ["POST"]),
        ]
    ]
