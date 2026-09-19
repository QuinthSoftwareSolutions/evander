from __future__ import annotations

import json

from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse
from starlette.routing import Route

from evander.admin.services.classification_admin_service import ClassificationRevisionConflict
from evander.admin.web.rbac import ADMIN_OWNER_PERMISSION
from evander.admin.web.shared import request_has_privileged_operations


def classification_routes(service, auth):
    async def handle(request):
        headers = {"cache-control": "no-store"}

        def error(message, status=400):
            return JSONResponse({"error": message}, status_code=status, headers=headers)

        try:
            scope = service.teams_admin.normalize_directory_reference(
                request.query_params.get("directory_path", "root")
            )
            session = auth.require(request)
            auth.require_permission(request, ADMIN_OWNER_PERMISSION, directory_path=scope)
        except PermissionError:
            return error("Classification configuration requires directory owner access", 403)
        except ValueError:
            return error("Invalid directory", 400)
        if request.method != "GET" and not request_has_privileged_operations(
            auth=auth, service=service, request=request
        ):
            return error("Privileged operations are locked.", 403)
        try:
            admin = service.classification_admin
            if request.method == "GET":
                return JSONResponse(await run_in_threadpool(admin.snapshot, scope), headers=headers)
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > 2 * 1024 * 1024:
                    return error("Classification request is too large", 413)
            payload = json.loads(body)
            if not isinstance(payload, dict):
                return error("Request body must be an object")
            actor = str(session.get("login") or "") or None
            if request.method == "PUT":
                if (
                    not isinstance(payload.get("expected_revision"), str)
                    or not payload["expected_revision"]
                ):
                    return error("expected_revision is required", 428)
                result = await run_in_threadpool(
                    admin.save,
                    scope,
                    expected_revision=payload["expected_revision"],
                    config_payload=payload.get("config"),
                    actor=actor,
                )
            else:
                result = await run_in_threadpool(admin.evaluate, scope, payload, actor=actor)
            return JSONResponse(result, headers=headers)
        except ClassificationRevisionConflict as exc:
            return error(str(exc), 409)
        except (json.JSONDecodeError, UnicodeError):
            return error("Request body must be valid JSON")
        except ValueError as exc:
            return error(str(exc))
        except Exception:
            return error("Classification operation failed", 502)

    return [
        Route("/api/classification", handle, methods=["GET", "PUT"]),
        Route("/api/classification/evaluate", handle, methods=["POST"]),
    ]
