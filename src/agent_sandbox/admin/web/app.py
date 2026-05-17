from __future__ import annotations

import argparse
from pathlib import Path

import uvicorn
from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from agent_sandbox.admin.service import AdminService
from agent_sandbox.admin.web.agents import agent_routes
from agent_sandbox.admin.web.chat import chat_routes
from agent_sandbox.admin.web.sessions_approvals import session_approval_routes
from agent_sandbox.admin.web.shared import (
    AZURE_SSO_START_PATH,
    AdminWebAuth,
    AdminWebConfig,
    json_error,
    request_has_privileged_operations,
)
from agent_sandbox.admin.web.reports import report_routes
from agent_sandbox.admin.web.teams_auth import teams_auth_routes
from agent_sandbox.admin.web.skills import skill_routes
from agent_sandbox.admin.web.user_store import AdminWebUserStore
from agent_sandbox.admin.web.users import admin_user_routes
from agent_sandbox.admin.web.vms_issues import vm_issue_routes
from agent_sandbox.admin.web.workflow import workflow_routes, workflow_run_events
from agent_sandbox.admin.web.workers_config import worker_config_routes


def _dist_dir() -> Path:
    return Path(__file__).resolve().parents[4] / "frontend" / "workflow-editor" / "dist"


def _fallback_html(dist_dir: Path) -> str:
    return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Workflow Editor</title>
    <style>
      body {{
        font-family: ui-sans-serif, system-ui, sans-serif;
        margin: 0;
        padding: 3rem;
        background: #f4f1e8;
        color: #1e1b16;
      }}
      main {{
        max-width: 42rem;
        background: #fffaf1;
        border: 1px solid #d7cdbb;
        border-radius: 18px;
        padding: 2rem;
        box-shadow: 0 18px 60px rgba(58, 45, 20, 0.08);
      }}
      code {{
        background: #efe7d7;
        border-radius: 6px;
        padding: 0.12rem 0.33rem;
      }}
    </style>
  </head>
  <body>
    <main>
      <h1>Admin frontend not built</h1>
      <p>The API is available, but the static frontend bundle was not found at <code>{dist_dir}</code>.</p>
      <p>Build the React app in <code>frontend/workflow-editor</code> and reload this page.</p>
    </main>
  </body>
</html>
"""


class AuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, auth: AdminWebAuth) -> None:
        super().__init__(app)
        self._auth = auth

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if not self._auth.config.auth_required:
            return await call_next(request)
        if (
            path == "/healthz"
            or path == "/api/admin/deployment-status"
            or path.startswith("/api/auth/")
        ):
            return await call_next(request)
        if path.startswith("/api/"):
            if self._auth.read_session(request) is None:
                return json_error("Authentication required", status_code=401)
            return await call_next(request)
        return await call_next(request)


def create_app(service: AdminService) -> Starlette:
    dist_dir = _dist_dir()
    admin_user_store = AdminWebUserStore(service.config.runtime_dir / "admin-web-users.json")
    auth = AdminWebAuth(AdminWebConfig.from_env(), user_store=admin_user_store)

    def privileged_operations_unlocked_for_request(request: Request) -> bool:
        return request_has_privileged_operations(auth=auth, service=service, request=request)

    async def health(_: Request) -> JSONResponse:
        return JSONResponse({"ok": True})

    async def login(request: Request) -> JSONResponse:
        try:
            payload = await request.json()
        except Exception:
            return json_error("Request body must be valid JSON")
        if not isinstance(payload, dict):
            return json_error("Request body must be a JSON object")
        try:
            token = auth.login(
                str(payload.get("username") or ""), str(payload.get("password") or "")
            )
        except ValueError as exc:
            return json_error(str(exc), status_code=401)
        response = JSONResponse(
            {
                "auth_mode": auth.config.auth_mode,
                "auth_required": auth.config.auth_required,
                "authenticated": True,
                "username": auth.config.username,
                "display_name": auth.config.username,
                "role": "admin",
                "can_manage_users": True,
                "session_id": None,
                "available_login_methods": ["password"]
                if auth.config.auth_mode == "local_password"
                else [],
                "azure_sso_login_url": AZURE_SSO_START_PATH
                if auth.config.auth_mode == "azure_sso"
                else None,
                "privileged_operations": {"unlocked": False},
            }
        )
        auth.attach_session_cookie(response, token)
        return response

    async def azure_login_start(request: Request) -> Response:
        try:
            redirect_url, state, code_verifier, next_path = auth.build_azure_login_redirect(
                request.query_params.get("next")
            )
        except ValueError as exc:
            return json_error(str(exc), status_code=400)
        response = RedirectResponse(redirect_url, status_code=307)
        auth.attach_pending_azure_login(
            response, state=state, code_verifier=code_verifier, next_path=next_path
        )
        return response

    async def azure_login_callback(request: Request) -> Response:
        if request.query_params.get("error"):
            response = json_error(
                str(request.query_params.get("error_description") or request.query_params["error"]),
                status_code=401,
            )
            auth.clear_pending_azure_login(response)
            return response
        code = str(request.query_params.get("code") or "")
        state = str(request.query_params.get("state") or "")
        if not code or not state:
            response = json_error(
                "Azure sign-in callback is missing code or state", status_code=400
            )
            auth.clear_pending_azure_login(response)
            return response
        try:
            token, post_login_redirect = await auth.exchange_azure_callback(request, code, state)
        except ValueError as exc:
            response = json_error(str(exc), status_code=401)
            auth.clear_pending_azure_login(response)
            return response
        response = RedirectResponse(post_login_redirect, status_code=303)
        auth.attach_session_cookie(response, token)
        auth.clear_pending_azure_login(response)
        return response

    async def get_session(request: Request) -> JSONResponse:
        payload = auth.authentication_payload(request)
        payload["privileged_operations"] = {
            "unlocked": privileged_operations_unlocked_for_request(request)
        }
        return JSONResponse(payload)

    async def logout(request: Request) -> JSONResponse:
        if auth.privileged_for_request(request):
            service.lock_privileged_operations()
        auth.logout(request)
        response = JSONResponse(
            {
                "auth_mode": auth.config.auth_mode,
                "auth_required": auth.config.auth_required,
                "authenticated": not auth.config.auth_required,
                "username": auth.config.username if not auth.config.auth_required else None,
                "display_name": auth.config.username if not auth.config.auth_required else None,
                "role": "admin" if not auth.config.auth_required else None,
                "can_manage_users": not auth.config.auth_required,
                "session_id": None,
                "available_login_methods": (
                    []
                    if auth.config.auth_mode == "none"
                    else ["password"]
                    if auth.config.auth_mode == "local_password"
                    else ["azure_sso"]
                ),
                "azure_sso_login_url": AZURE_SSO_START_PATH
                if auth.config.auth_mode == "azure_sso"
                else None,
                "privileged_operations": {"unlocked": False},
            }
        )
        auth.clear_session_cookie(response)
        auth.clear_pending_azure_login(response)
        return response

    async def get_admin_state(request: Request) -> JSONResponse:
        session = auth.require(request)
        assistant_settings = service.read_assistant_settings()
        requested_directory_path = (
            str(request.query_params.get("directory_path") or "root").strip() or "root"
        )
        try:
            selected_directory_path = service._normalize_directory_reference(
                requested_directory_path
            )
        except ValueError:
            selected_directory_path = "root"
        return JSONResponse(
            {
                "selected_directory_path": selected_directory_path,
                "directory_scoped_config_enabled": service.directory_scoped_config_enabled,
                "execution_backend": service.effective_execution_backend(selected_directory_path),
                "role": session.get("role"),
                "can_manage_users": bool(session.get("can_manage_users")),
                "assistant": {
                    "enabled": bool(assistant_settings.get("enabled")),
                    "show_launcher_when_disabled": bool(
                        assistant_settings.get("show_launcher_when_disabled")
                    ),
                },
                "deployment": service.read_deployment_status().model_dump(mode="json"),
                "privileged_operations": {
                    "unlocked": privileged_operations_unlocked_for_request(request)
                },
            }
        )

    async def get_deployment_status(_: Request) -> JSONResponse:
        return JSONResponse(service.read_deployment_status().model_dump(mode="json"))

    async def unlock_privileged_operations(request: Request) -> JSONResponse:
        auth.require(request)
        try:
            message = service.unlock_privileged_operations()
        except ValueError as exc:
            return json_error(str(exc))
        auth.mark_privileged_for_request(request)
        return JSONResponse(
            {
                "message": message,
                "privileged_operations": {
                    "unlocked": privileged_operations_unlocked_for_request(request)
                },
            }
        )

    async def lock_privileged_operations(request: Request) -> JSONResponse:
        auth.require(request)
        service.lock_privileged_operations()
        auth.clear_privileged_for_request(request)
        return JSONResponse(
            {
                "message": "Privileged operations locked.",
                "privileged_operations": {"unlocked": False},
            }
        )

    async def index(_: Request) -> Response:
        return HTMLResponse(_fallback_html(dist_dir))

    routes = [
        Route("/healthz", health, methods=["GET"]),
        Route("/api/auth/login", login, methods=["POST"]),
        Route(AZURE_SSO_START_PATH, azure_login_start, methods=["GET"]),
        Route("/api/auth/azure-sso/callback", azure_login_callback, methods=["GET"]),
        Route("/api/auth/session", get_session, methods=["GET"]),
        Route("/api/auth/logout", logout, methods=["POST"]),
        Route("/api/admin/state", get_admin_state, methods=["GET"]),
        Route("/api/admin/deployment-status", get_deployment_status, methods=["GET"]),
        Route("/api/admin/privileged/unlock", unlock_privileged_operations, methods=["POST"]),
        Route("/api/admin/privileged/lock", lock_privileged_operations, methods=["POST"]),
    ]
    routes.extend(workflow_routes(service))
    routes.extend(agent_routes(service, auth))
    routes.extend(skill_routes(service, auth))
    routes.extend(vm_issue_routes(service, auth))
    routes.extend(session_approval_routes(service, auth))
    routes.extend(worker_config_routes(service, auth))
    routes.extend(admin_user_routes(admin_user_store, auth, service))
    routes.extend(report_routes(service))
    routes.extend(teams_auth_routes(service, auth))
    routes.extend(chat_routes(service, auth))
    if dist_dir.exists():
        routes.append(Mount("/", app=StaticFiles(directory=dist_dir, html=True)))
    else:
        routes.append(Route("/", index, methods=["GET"]))

    app = Starlette(debug=False, routes=routes)
    app.state.auth = auth
    app.state.admin_user_store = admin_user_store
    app.add_middleware(AuthMiddleware, auth=auth)
    return app


_workflow_run_events = workflow_run_events


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the local workflow editor admin web app.")
    parser.add_argument(
        "--runtime-dir",
        "--config",
        default=str(Path(__file__).resolve().parents[4] / "runtime"),
        help="Runtime directory root. A legacy gateway YAML path is accepted for bootstrap compatibility.",
    )
    parser.add_argument(
        "--host", default="127.0.0.1", help="Bind host for the local admin web app."
    )
    parser.add_argument(
        "--port", type=int, default=8100, help="Bind port for the local admin web app."
    )
    args = parser.parse_args()

    service = AdminService(Path(args.runtime_dir))
    uvicorn.run(create_app(service), host=args.host, port=args.port, log_level="info")


__all__ = ["create_app", "main", "workflow_run_events", "_workflow_run_events"]
