import io
import json
from pathlib import Path
import zipfile

from pydantic import ValidationError
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from evander.admin.web.rbac import ADMIN_OWNER_PERMISSION, WORKFLOWS_READ_PERMISSION
from evander.models import DirectoryPath

from .models import Conflict, Settings, WordPressError
from .service import Actor, Service


def plugin_zip():
    stream = io.BytesIO()
    for_file = Path(__file__).with_name("companion")
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(for_file.glob("*.php")):
            info = zipfile.ZipInfo(
                "evander-wordpress/" + path.name, date_time=(1980, 1, 1, 0, 0, 0)
            )
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, path.read_bytes())
    return stream.getvalue()


def admin_api_routes(context):
    def service(request, write=False):
        directory = DirectoryPath(segments=request.query_params.get("directory", "root")).as_posix()
        auth = context.auth
        if not callable(getattr(auth, "require_permission", None)):
            raise PermissionError
        login = auth.require(request).get("login", "operator")
        auth.require_permission(
            request,
            ADMIN_OWNER_PERMISSION if write else WORKFLOWS_READ_PERMISSION,
            directory_path=directory,
        )
        return Service(
            context.runtime_dir,
            Actor(directory, "operator:" + login, True),
            context.service.connector_service,
        )

    async def body(request):
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > 1024**2:
                raise WordPressError("Request exceeds the size limit")
        result = json.loads(raw)
        if not isinstance(result, dict):
            raise ValueError
        return result

    async def settings(request):
        api = service(request, request.method != "GET")
        if request.method == "PUT":
            payload = await body(request)
            if type(payload.get("expected_revision")) is not int:
                return JSONResponse({"error": "expected_revision is required"}, status_code=428)
            parsed = Settings.model_validate(payload["settings"])
            _, old = api.store.settings(api.directory)
            for key, site in parsed.sites.items():
                prior = old.sites.get(key)
                # Probe stamps are server-owned. Editing a destination/configuration invalidates them.
                site.recovery_verified = (
                    prior.recovery_verified if prior and prior.binding() == site.binding() else None
                )
                if site.enabled and site.recovery_verified != site.binding():
                    raise WordPressError(
                        "Save the site disabled, run its restore test, then enable it"
                    )
            revision = api.store.save_settings(api.directory, payload["expected_revision"], parsed)
        revision, parsed = api.store.settings(api.directory)
        return JSONResponse({"revision": revision, "settings": parsed.model_dump(mode="json")})

    async def discover(request):
        api = service(request)
        return JSONResponse(await api.discover(request.query_params["site_id"]))

    async def test(request):
        api = service(request, True)
        return JSONResponse(await api.test((await body(request))["site_id"]))

    async def history(request):
        api = service(request)
        return JSONResponse(
            api.status(request.query_params.get("site_id"), request.path_params.get("change_id"))
        )

    async def restore(request):
        api = service(request, True)
        payload = await body(request)
        payload["change_id"] = request.path_params["change_id"]
        return JSONResponse(await api.queue(payload, restore=True))

    async def resolve(request):
        api = service(request, True)
        payload = await body(request)
        payload["change_id"] = request.path_params["change_id"]
        return JSONResponse(await api.resolve(payload))

    async def artifact(request):
        api = service(request)
        change = api.store.get(request.path_params["change_id"], api.directory)
        name = request.path_params["artifact_id"]
        if name not in {shot.get("artifact") for shot in change.get("screenshots", [])}:
            raise PermissionError
        return Response(
            api.store.read_artifact(name),
            media_type="image/png",
            headers={"Cache-Control": "no-store"},
        )

    async def download(request):
        service(request)
        return Response(
            plugin_zip(),
            media_type="application/zip",
            headers={"Content-Disposition": 'attachment; filename="evander-wordpress.zip"'},
        )

    async def schema(request):
        service(request)
        return JSONResponse(Settings.model_json_schema())

    def safe(handler):
        async def endpoint(request):
            try:
                return await handler(request)
            except PermissionError:
                return JSONResponse(
                    {"error": "Permission denied for this directory"}, status_code=403
                )
            except Conflict as error:
                return JSONResponse({"error": str(error)}, status_code=409)
            except WordPressError as error:
                return JSONResponse({"error": str(error)}, status_code=400)
            except (ValidationError, ValueError, KeyError, TypeError):
                return JSONResponse({"error": "Invalid WordPress request"}, status_code=400)

        return endpoint

    return [
        Route(path, safe(handler), methods=methods)
        for path, handler, methods in [
            ("/settings", settings, ["GET", "PUT"]),
            ("/schema", schema, ["GET"]),
            ("/discover", discover, ["GET"]),
            ("/test", test, ["POST"]),
            ("/history", history, ["GET"]),
            ("/changes/{change_id}", history, ["GET"]),
            ("/changes/{change_id}/restore", restore, ["POST"]),
            ("/changes/{change_id}/resolve", resolve, ["POST"]),
            ("/changes/{change_id}/screenshots/{artifact_id}", artifact, ["GET"]),
            ("/companion.zip", download, ["GET"]),
        ]
    ]
