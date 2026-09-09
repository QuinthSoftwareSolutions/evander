from __future__ import annotations

import json

from starlette.responses import JSONResponse
from starlette.routing import Route

from evander.admin.services.website_session_admin_service import WebsiteProfileRevisionConflict
from evander.admin.web.rbac import ADMIN_OWNER_PERMISSION
from evander.admin.web.shared import request_has_privileged_operations


def website_session_routes(service, auth):
    async def handle(request):
        headers = {"cache-control": "no-store"}
        def error(message, status=400):
            return JSONResponse({"error": message}, status_code=status, headers=headers)
        try:
            scope = service.teams_admin.normalize_directory_reference(request.query_params.get("directory_path", "root"))
            session = auth.require(request)
            auth.require_permission(request, ADMIN_OWNER_PERMISSION, directory_path=scope)
        except PermissionError:
            return error("Website configuration requires directory owner access", 403)
        except ValueError:
            return error("Invalid directory", 400)
        if request.method != "GET" and not request_has_privileged_operations(auth=auth, service=service, request=request):
            return error("Privileged operations are locked.", 403)
        try:
            admin = service.website_session_admin
            if request.method == "GET":
                return JSONResponse(admin.snapshot(scope), headers=headers)
            # Bound request size without buffering arbitrary credential payloads.
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > 131072:
                    return error("Website configuration request is too large", 413)
            try:
                payload = json.loads(body)
            except (ValueError, UnicodeError):
                return error("Request body must be valid JSON")
            if not isinstance(payload, dict):
                return error("Request body must be an object")
            revision = payload.get("expected_revision")
            if not isinstance(revision, str) or not revision:
                return error("expected_revision is required", 428)
            profile_id = request.path_params["profile_id"]
            actor = str(session.get("login") or session.get("user_id") or "admin")
            if request.method == "PUT":
                result = admin.save(scope, profile_id, payload, actor)
            elif request.method == "DELETE":
                result = admin.delete(scope, profile_id, revision,
                                      credentials_only=request.url.path.endswith("/credentials"), actor=actor)
            else:
                result = await admin.test_login(scope, profile_id, revision, actor)
            return JSONResponse(result, headers=headers)
        except WebsiteProfileRevisionConflict as exc:
            return error(str(exc), 409)
        except ValueError as exc:
            return error(str(exc))
        except Exception:
            # Storage failures must not expose credential payloads or exception chains.
            return error("Website configuration operation failed", 503)

    base = "/api/managed-website-sessions"
    return [Route(base, handle, methods=["GET"]),
            Route(base + "/{profile_id}", handle, methods=["PUT", "DELETE"]),
            Route(base + "/{profile_id}/credentials", handle, methods=["DELETE"]),
            Route(base + "/{profile_id}/test", handle, methods=["POST"])]
