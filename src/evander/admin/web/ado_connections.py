from __future__ import annotations

import hashlib

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import RedirectResponse
from starlette.routing import Route

from evander.admin.web.shared import json_error, json_ok
from evander.admin.services.ado_user_connections import normalize_scope
from evander.admin.services.ado_oauth import CALLBACK_PATH

OAUTH_COOKIE = "evander_ado_oauth"


def ado_connection_routes(service, auth, user_store):
    def signed_in_user(request):
        if auth.config.auth_mode not in {"azure_sso", "local_password"}:
            raise PermissionError("Enable sign-in before adding personal ADO connections")
        session = auth.require(request)
        login = str(session.get("login") or "").strip().lower()
        if not login or user_store.get_user(login) is None:
            raise PermissionError("A registered user must sign in")
        return login

    def session_hash(request):
        cookie = request.cookies.get(auth.config.cookie_name, "")
        if not cookie:
            raise PermissionError("A browser sign-in is required")
        return hashlib.sha256(cookie.encode()).hexdigest()

    async def connections(request: Request):
        try:
            login = signed_in_user(request)
            manager = service.ado_user_connections
            if request.method == "GET":
                items = await run_in_threadpool(manager.list_for_user, login)
                return json_ok({"connections": items, "oauth_available": manager.oauth_available()})
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
                await run_in_threadpool(manager.remove, **arguments, method=payload.get("method"))
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

    async def oauth_start(request: Request):
        try:
            login = signed_in_user(request)
            payload = await request.json()
            if not isinstance(payload, dict) or set(payload) - {"organization", "directory_path"}:
                raise ValueError("Provide organization and directory only")
            directory = normalize_scope(str(payload.get("directory_path") or "root"))
            if not auth.effective_permissions(request, directory_path=directory):
                raise PermissionError("Directory access is required")
            url, browser = await run_in_threadpool(
                service.ado_user_connections.begin_oauth,
                login=login,
                organization=str(payload.get("organization") or ""),
                directory_path=directory,
                session_hash=session_hash(request),
            )
            response = json_ok({"authorization_url": url})
            response.set_cookie(
                OAUTH_COOKIE,
                browser,
                httponly=True,
                secure=auth.config.cookie_secure,
                samesite="lax",
                path=CALLBACK_PATH,
                max_age=600,
            )
            response.headers["Cache-Control"] = "no-store"
            return response
        except PermissionError as exc:
            return json_error(str(exc), status_code=403)
        except ValueError as exc:
            return json_error(str(exc), status_code=400)

    async def oauth_callback(request: Request):
        status = "failed"
        try:
            login = signed_in_user(request)
            pending = await run_in_threadpool(
                service.ado_user_connections.consume_oauth,
                login=login,
                session_hash=session_hash(request),
                browser=request.cookies.get(OAUTH_COOKIE, ""),
                state=request.query_params.get("state", ""),
            )
            if not auth.effective_permissions(request, directory_path=pending["directory_path"]):
                raise PermissionError("Directory access is required")
            if request.query_params.get("error"):
                status = (
                    "cancelled" if request.query_params["error"] == "access_denied" else "failed"
                )
            else:
                code = request.query_params.get("code")
                if not code:
                    raise ValueError("Missing authorization code")
                await run_in_threadpool(service.ado_user_connections.complete_oauth, pending, code)
                status = "connected"
        except (ValueError, PermissionError):
            pass
        response = RedirectResponse("/?ado_connection=" + status, status_code=303)
        response.delete_cookie(OAUTH_COOKIE, path=CALLBACK_PATH)
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    return [
        Route("/api/admin/me/ado-connections", connections, methods=["GET", "PUT", "DELETE"]),
        Route("/api/admin/me/ado-connections/oauth/start", oauth_start, methods=["POST"]),
        Route(CALLBACK_PATH, oauth_callback, methods=["GET"]),
    ]
