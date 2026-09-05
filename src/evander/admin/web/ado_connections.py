from __future__ import annotations

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.routing import Route

from evander.admin.web.shared import json_error, json_ok
from evander.admin.services.ado_user_connections import normalize_scope


def ado_connection_routes(service, auth, user_store):
    async def connections(request: Request):
        try:
            if auth.config.auth_mode not in {"azure_sso", "local_password"}:
                raise PermissionError("Enable sign-in before adding personal ADO connections")
            session = auth.require(request)
            login = str(session.get("login") or "").strip().lower()
            if not login or user_store.get_user(login) is None:
                raise PermissionError("A registered user must sign in")
            manager = service.ado_user_connections
            if request.method == "GET":
                items = await run_in_threadpool(manager.list_for_user, login)
                return json_ok({"connections": items})
            payload = await request.json()
            if not isinstance(payload, dict):
                raise ValueError("Request body must be an object")
            # The authenticated session, never a request field, selects the owner.
            if "login" in payload or "identity_id" in payload:
                raise ValueError("Connection ownership is determined by sign-in and PAT validation")
            directory_path = normalize_scope(str(payload.get("directory_path") or "root"))
            arguments = dict(
                login=login,
                directory_path=directory_path,
                organization=str(payload.get("organization") or ""),
            )
            if request.method == "DELETE":
                await run_in_threadpool(manager.remove, **arguments)
                return json_ok({"removed": True})
            if not auth.effective_permissions(request, directory_path=directory_path):
                raise PermissionError("Directory access is required")
            pat = payload.get("pat")
            if not isinstance(pat, str):
                raise ValueError("A personal access token is required")
            result = await run_in_threadpool(manager.save, **arguments, pat=pat)
            return json_ok({"connection": result})
        except PermissionError as exc:
            return json_error(str(exc), status_code=403)
        except ValueError as exc:
            return json_error(str(exc), status_code=400)

    return [Route("/api/admin/me/ado-connections", connections, methods=["GET", "PUT", "DELETE"])]
