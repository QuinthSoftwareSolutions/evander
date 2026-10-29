import json
from urllib.parse import urlsplit

from starlette.responses import JSONResponse
from starlette.routing import Route

from evander.admin.web.rbac import ADMIN_OWNER_PERMISSION, WORKFLOWS_READ_PERMISSION
from evander.admin.web.shared import request_has_privileged_operations
from evander.models import DirectoryPath

from .models import Conflict, FixtureError, Settings, receipt
from .service import Service
from .worker_api import read_body


def admin_api_routes(context):
    def authorize(request, write):
        directory = DirectoryPath(segments=request.query_params.get("directory", "root")).as_posix()
        auth = context.auth
        login = auth.require(request).get("login", "operator")
        auth.require_permission(
            request,
            ADMIN_OWNER_PERMISSION if write else WORKFLOWS_READ_PERMISSION,
            directory_path=directory,
        )
        if write:
            if not request_has_privileged_operations(
                auth=auth, service=context.service, request=request
            ):
                raise PermissionError
            if (
                request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                != "application/json"
            ):
                raise PermissionError
            origin = request.headers.get("origin")
            if origin is not None:

                def authority(url):
                    parsed = urlsplit(url)
                    if (
                        parsed.scheme not in {"http", "https"}
                        or not parsed.hostname
                        or parsed.username
                        or parsed.password
                    ):
                        raise PermissionError
                    return (
                        parsed.scheme,
                        parsed.hostname,
                        parsed.port or (443 if parsed.scheme == "https" else 80),
                    )

                value = urlsplit(origin)
                if (
                    value.path
                    or value.query
                    or value.fragment
                    or authority(origin) != authority(str(request.url))
                ):
                    raise PermissionError
        return Service(context.runtime_dir, context.service), directory, "operator:" + login

    async def endpoint(request):
        try:
            write = request.method != "GET"
            service, directory, actor = authorize(request, write)
            if request.url.path.endswith("/settings"):
                if write:
                    body = await read_body(request)
                    if type(body.get("expected_revision")) is not int:
                        return JSONResponse(
                            {"error": "expected_revision is required"}, status_code=428
                        )
                    settings = Settings.model_validate(body["settings"])
                    service.store.save_settings(
                        directory, body["expected_revision"], settings, actor
                    )
                revision, settings = service.store.settings(directory)
                result = {"revision": revision, "settings": settings.model_dump()}
            elif write:
                body = await read_body(request)
                if type(body.get("expected_revision")) is not int:
                    return JSONResponse({"error": "expected_revision is required"}, status_code=428)
                result = service.operator_release(
                    request.path_params["lease_id"], body["expected_revision"], actor, directory
                )
            else:
                result = {
                    "leases": [
                        receipt(lease)
                        for lease in service.store.list()
                        if lease["scope"]["directory"] == directory
                    ]
                }
            return JSONResponse(result, headers={"Cache-Control": "no-store"})
        except PermissionError:
            return JSONResponse({"error": "Fixture operator permission required"}, status_code=403)
        except Conflict as exc:
            return JSONResponse({"error": str(exc)}, status_code=409)
        except (FixtureError, ValueError, KeyError, json.JSONDecodeError):
            return JSONResponse({"error": "Invalid fixture request"}, status_code=400)

    return [
        Route("/settings", endpoint, methods=["GET", "PUT"]),
        Route("/leases", endpoint),
        Route("/leases/{lease_id}/release", endpoint, methods=["POST"]),
    ]
