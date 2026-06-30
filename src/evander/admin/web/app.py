from __future__ import annotations

import argparse
import json
import re
from html import escape
from pathlib import Path
from typing import Any

import uvicorn
from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from evander.addons import (
    AddonContext,
    LoadedAddonCatalog,
    addon_admin_navigation_payloads,
    addon_runtime_dir,
    load_addons,
    resolve_addon_admin_api_routes,
    resolve_addon_admin_ui_routes,
)
from evander.admin.composition import AdminRuntimeComponents, build_admin_runtime
from evander.admin.release_metadata import NEWS_ASSET_BASE_PATH, NEWS_ASSET_DIRECTORY
from evander.admin.web.cache import AdminWebJsonResponseCache
from evander.admin.web.frontend_assets import (
    admin_frontend_assets_ready,
    admin_frontend_dist_dir,
)
from evander.admin.web.agents import agent_routes
from evander.admin.web.addons import addon_routes
from evander.admin.web.capacity import capacity_routes
from evander.admin.web.artifacts import artifact_routes
from evander.admin.web.chat import chat_routes
from evander.admin.web.operator_steering import operator_steering_routes
from evander.admin.web.sessions_approvals import session_approval_routes
from evander.admin.web.shared import (
    ADMIN_WEB_BRANDING_ASSET_BASE_PATH,
    AZURE_SSO_START_PATH,
    AdminWebAuth,
    AdminWebConfig,
    json_error,
    request_has_privileged_operations,
)
from evander.admin.web.pr_integration import pr_integration_routes
from evander.admin.web.rbac import ADMIN_OWNER_PERMISSION, USERS_MANAGE_PERMISSION
from evander.admin.web.reports import report_routes
from evander.admin.web.tool_calls import tool_call_routes
from evander.admin.web.tool_visibility import tool_visibility_routes
from evander.admin.web.teams_auth import teams_auth_routes
from evander.admin.web.skills import skill_routes
from evander.admin.web.user_store import AdminWebUserStore
from evander.admin.web.user_notifications import user_notification_routes
from evander.admin.web.users import admin_user_routes
from evander.admin.web.vms_issues import vm_issue_routes
from evander.admin.web.workflow import workflow_routes, workflow_run_events
from evander.admin.web.workflow_events import workflow_event_routes
from evander.admin.web.workflow_webhooks import workflow_webhook_routes
from evander.admin.web.workers_config import worker_config_routes


class AdminWebAddonServiceAdapter:
    """Narrow Admin Web service surface exposed to add-on admin routes."""

    def __init__(self, components: AdminRuntimeComponents) -> None:
        self._components = components
        self.runtime = components.runtime
        self.config = components.config
        self.project_dir = components.project_dir

    def import_git_repo(self, **kwargs: Any) -> Any:
        return self._components.issue_git_admin.import_git_repo(**kwargs)

    def list_agents(self, **kwargs: Any) -> Any:
        return self._components.vm_agent_admin.list_agents(**kwargs)

    def resume_agent(self, **kwargs: Any) -> Any:
        return self._components.vm_agent_admin.resume_agent(**kwargs)

    def vm_copy_files(self, **kwargs: Any) -> Any:
        return self._components.vm_agent_admin.vm_copy_files(**kwargs)


def _dist_dir() -> Path:
    return admin_frontend_dist_dir(Path(__file__).resolve().parents[4])


def _fallback_html(dist_dir: Path, *, page_title: str) -> str:
    return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>{escape(page_title)}</title>
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
      <p>The API is available, but the static frontend bundle was not found or is incomplete at <code>{dist_dir}</code>.</p>
      <p>Build the React app in <code>frontend/workflow-editor</code> and reload this page.</p>
    </main>
  </body>
</html>
"""


def _script_json(payload: object) -> str:
    return (
        json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        .replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def _index_html(dist_dir: Path, *, branding_payload: dict[str, object]) -> str:
    index_path = dist_dir / "index.html"
    page_title = str(branding_payload.get("page_title") or "")
    if not admin_frontend_assets_ready(dist_dir):
        return _fallback_html(dist_dir, page_title=page_title)
    html = index_path.read_text(encoding="utf-8")
    title = f"<title>{escape(page_title)}</title>"
    bootstrap_script = (
        "<script>"
        f"window.__ADMIN_WEB_BRANDING__={_script_json(branding_payload)};"
        "</script>"
    )
    if re.search(r"<title>.*?</title>", html, flags=re.IGNORECASE | re.DOTALL):
        html = re.sub(
            r"<title>.*?</title>",
            title,
            html,
            count=1,
            flags=re.IGNORECASE | re.DOTALL,
        )
    elif "</head>" in html:
        html = html.replace("</head>", f"    {title}\n  </head>", 1)
    if "__ADMIN_WEB_BRANDING__" in html:
        return html
    if "</head>" in html:
        return html.replace("</head>", f"    {bootstrap_script}\n  </head>", 1)
    return f"{bootstrap_script}\n{html}"


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
            or path == "/api/admin/branding"
            or path.startswith(f"{ADMIN_WEB_BRANDING_ASSET_BASE_PATH}/")
            or path.startswith("/api/auth/")
            or (path == "/api/workflow-events" and request.method == "POST")
            or (path.startswith("/api/workflow-webhooks/") and request.method == "POST")
        ):
            return await call_next(request)
        if path.startswith("/api/") or path.startswith("/addons/"):
            if self._auth.read_session(request) is None:
                return json_error("Authentication required", status_code=401)
            return await call_next(request)
        return await call_next(request)


def create_app(service: AdminRuntimeComponents) -> Starlette:
    dist_dir = _dist_dir()
    admin_user_store = AdminWebUserStore(
        service.config.runtime_dir / "admin-web.db",
        legacy_json_path=service.config.runtime_dir / "admin-web-users.json",
    )
    auth = AdminWebAuth(AdminWebConfig.from_env(), user_store=admin_user_store)
    dashboard_cache = AdminWebJsonResponseCache()
    try:
        addon_catalog = load_addons(service.config.addon_loader)
    except Exception:
        addon_catalog = LoadedAddonCatalog(registrations=(), loaded_addons=())
    service.vm_agent_admin.mark_abandoned_agent_create_operations_failed()

    def privileged_operations_unlocked_for_request(request: Request) -> bool:
        return request_has_privileged_operations(auth=auth, service=service, request=request)

    def _role_for_permissions(permissions: list[str] | tuple[str, ...]) -> str:
        if ADMIN_OWNER_PERMISSION in permissions or USERS_MANAGE_PERMISSION in permissions:
            return "admin"
        return "regular"

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
        session = auth.session_from_token(token)
        if session is None:
            return json_error("Sign in failed", status_code=401)
        permissions = list(session.get("effective_permissions") or session.get("permissions") or [])
        session_id = str(session.get("sid") or "").strip() or None
        response = JSONResponse(
            {
                "auth_mode": auth.config.auth_mode,
                "auth_required": auth.config.auth_required,
                "authenticated": True,
                "username": session.get("login"),
                "display_name": session.get("name"),
                "role": session.get("role") or _role_for_permissions(permissions),
                "can_manage_users": bool(session.get("can_manage_users")),
                "permissions": permissions,
                "effective_permissions": permissions,
                "session_id": session_id,
                "available_login_methods": ["password"]
                if auth.config.auth_mode == "local_password"
                else [],
                "azure_sso_login_url": AZURE_SSO_START_PATH
                if auth.config.auth_mode == "azure_sso"
                else None,
                "privileged_operations": {"unlocked": False},
                "branding": auth.config.branding.to_payload(),
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
            service.operations_admin.lock_privileged_operations()
        auth.logout(request)
        permissions = (
            list(auth.effective_permissions(auth.config.username))
            if not auth.config.auth_required
            else []
        )
        response = JSONResponse(
            {
                "auth_mode": auth.config.auth_mode,
                "auth_required": auth.config.auth_required,
                "authenticated": not auth.config.auth_required,
                "username": auth.config.username if not auth.config.auth_required else None,
                "display_name": auth.config.username if not auth.config.auth_required else None,
                "role": (
                    _role_for_permissions(permissions)
                    if not auth.config.auth_required
                    else None
                ),
                "can_manage_users": not auth.config.auth_required,
                "permissions": permissions,
                "effective_permissions": permissions,
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
                "branding": auth.config.branding.to_payload(),
            }
        )
        auth.clear_session_cookie(response)
        auth.clear_pending_azure_login(response)
        return response

    async def get_admin_state(request: Request) -> JSONResponse:
        session = auth.require(request)
        assistant_settings = service.operations_admin.read_assistant_settings()
        requested_directory_path = (
            str(request.query_params.get("directory_path") or "root").strip() or "root"
        )
        try:
            selected_directory_path = service.teams_admin.normalize_directory_reference(
                requested_directory_path
            )
        except ValueError:
            selected_directory_path = "root"
        permissions = list(auth.effective_permissions(session, selected_directory_path))
        return JSONResponse(
            {
                "selected_directory_path": selected_directory_path,
                "directory_scoped_config_enabled": service.config.enable_directory_scoped_config,
                "execution_backend": service.vm_agent_admin.effective_execution_backend(selected_directory_path),
                "role": _role_for_permissions(permissions) if session else None,
                "can_manage_users": auth.can_manage_users(
                    session, directory_path=selected_directory_path
                ),
                "permissions": permissions,
                "effective_permissions": permissions,
                "assistant": {
                    "enabled": bool(assistant_settings.get("enabled")),
                    "show_launcher_when_disabled": bool(
                        assistant_settings.get("show_launcher_when_disabled")
                    ),
                },
                "deployment": service.operations_admin.read_deployment_status().model_dump(mode="json"),
                "addons": addon_admin_navigation_payloads(addon_catalog),
                "privileged_operations": {
                    "unlocked": privileged_operations_unlocked_for_request(request)
                },
            }
        )

    async def get_deployment_status(_: Request) -> JSONResponse:
        return JSONResponse(service.operations_admin.read_deployment_status().model_dump(mode="json"))

    async def get_branding(_: Request) -> JSONResponse:
        return JSONResponse(auth.config.branding.to_payload())

    async def get_branding_asset(request: Request) -> FileResponse | JSONResponse:
        asset_root = auth.config.branding_asset_dir
        if asset_root is None:
            return json_error(
                "Admin Web branding asset directory is not configured.", status_code=404
            )
        relative_path = str(request.path_params.get("asset_path") or "").strip()
        if not relative_path or "\x00" in relative_path:
            return json_error("Branding asset not found.", status_code=404)
        root = asset_root.resolve()
        candidate = (root / relative_path).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            return json_error("Branding asset not found.", status_code=404)
        if not candidate.is_file():
            return json_error("Branding asset not found.", status_code=404)
        return FileResponse(candidate)

    async def get_release_info(request: Request) -> JSONResponse:
        try:
            news_index = _non_negative_int_query_param(request, "news_index", default=0)
        except ValueError as exc:
            return json_error(str(exc))
        return JSONResponse(
            service.operations_admin.read_release_info(news_index=news_index).model_dump(mode="json")
        )

    async def get_news_asset(request: Request) -> FileResponse | JSONResponse:
        relative_path = str(request.path_params.get("asset_path") or "").strip()
        if not relative_path or "\x00" in relative_path:
            return json_error("News asset not found.", status_code=404)
        root = (service.project_dir / NEWS_ASSET_DIRECTORY).resolve()
        candidate = (root / relative_path).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            return json_error("News asset not found.", status_code=404)
        if not candidate.is_file():
            return json_error("News asset not found.", status_code=404)
        return FileResponse(candidate)

    async def download_changelog(_: Request) -> FileResponse | JSONResponse:
        try:
            changelog_path = service.operations_admin.release_changelog_file()
        except FileNotFoundError as exc:
            return json_error(str(exc), status_code=404)
        return FileResponse(
            changelog_path,
            media_type="text/markdown; charset=utf-8",
            filename="CHANGELOG.md",
        )

    async def unlock_privileged_operations(request: Request) -> JSONResponse:
        auth.require(request)
        try:
            message = service.operations_admin.unlock_privileged_operations()
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
        service.operations_admin.lock_privileged_operations()
        auth.clear_privileged_for_request(request)
        return JSONResponse(
            {
                "message": "Privileged operations locked.",
                "privileged_operations": {"unlocked": False},
            }
        )

    async def index(_: Request) -> Response:
        return HTMLResponse(
            _index_html(dist_dir, branding_payload=auth.config.branding.to_payload())
        )

    routes = [
        Route("/healthz", health, methods=["GET"]),
        Route("/api/auth/login", login, methods=["POST"]),
        Route(AZURE_SSO_START_PATH, azure_login_start, methods=["GET"]),
        Route("/api/auth/azure-sso/callback", azure_login_callback, methods=["GET"]),
        Route("/api/auth/session", get_session, methods=["GET"]),
        Route("/api/auth/logout", logout, methods=["POST"]),
        Route("/api/admin/state", get_admin_state, methods=["GET"]),
        Route("/api/admin/branding", get_branding, methods=["GET"]),
        Route(
            f"{ADMIN_WEB_BRANDING_ASSET_BASE_PATH}" + "/{asset_path:path}",
            get_branding_asset,
            methods=["GET"],
        ),
        Route(
            "/api/admin/deployment-status",
            dashboard_cache.wrap("admin.deployment_status", get_deployment_status),
            methods=["GET"],
        ),
        Route(
            "/api/admin/release-info",
            dashboard_cache.wrap("admin.release_info", get_release_info),
            methods=["GET"],
        ),
        Route(f"{NEWS_ASSET_BASE_PATH}" + "/{asset_path:path}", get_news_asset, methods=["GET"]),
        Route("/api/admin/release-info/changelog", download_changelog, methods=["GET"]),
        Route("/api/admin/privileged/unlock", unlock_privileged_operations, methods=["POST"]),
        Route("/api/admin/privileged/lock", lock_privileged_operations, methods=["POST"]),
    ]
    routes.extend(workflow_routes(service, auth, dashboard_cache))
    routes.extend(agent_routes(service, auth, dashboard_cache))
    routes.extend(addon_routes(service, auth))
    routes.extend(capacity_routes(service, auth, dashboard_cache))
    routes.extend(skill_routes(service, auth))
    routes.extend(vm_issue_routes(service, auth))
    routes.extend(session_approval_routes(service, auth, dashboard_cache))
    routes.extend(tool_call_routes(service, dashboard_cache))
    routes.extend(tool_visibility_routes(service))
    routes.extend(worker_config_routes(service, auth, dashboard_cache))
    routes.extend(admin_user_routes(admin_user_store, auth, service))
    routes.extend(user_notification_routes(service, auth))
    routes.extend(pr_integration_routes(service, auth))
    routes.extend(report_routes(service, auth, dashboard_cache))
    routes.extend(teams_auth_routes(service, auth))
    routes.extend(chat_routes(service, auth))
    routes.extend(workflow_event_routes(service, auth))
    routes.extend(workflow_webhook_routes(service))
    routes.extend(operator_steering_routes(service, auth))
    routes.extend(artifact_routes(service))
    addon_service = AdminWebAddonServiceAdapter(service)
    for addon in addon_catalog.registrations:
        context = AddonContext(
            addon_id=addon.addon_id,
            runtime_dir=service.config.runtime_dir,
            addon_runtime_dir=addon_runtime_dir(service.config.runtime_dir, addon.addon_id),
            project_dir=service.project_dir,
            config=service.config,
            service=addon_service,
            auth=auth,
        )
        api_routes = resolve_addon_admin_api_routes(addon, context)
        if api_routes:
            routes.append(Mount(f"/api/addons/{addon.addon_id}", routes=list(api_routes)))
        ui_routes = resolve_addon_admin_ui_routes(addon, context)
        if ui_routes:
            routes.append(Mount(f"/addons/{addon.addon_id}", routes=list(ui_routes)))
    if dist_dir.exists():
        routes.append(Route("/", index, methods=["GET"]))
        routes.append(Route("/index.html", index, methods=["GET"]))
        routes.append(Mount("/", app=StaticFiles(directory=dist_dir, html=True)))
    else:
        routes.append(Route("/", index, methods=["GET"]))
        routes.append(Route("/index.html", index, methods=["GET"]))

    app = Starlette(debug=False, routes=routes)
    app.state.auth = auth
    app.state.admin_user_store = admin_user_store
    app.state.dashboard_cache = dashboard_cache
    app.state.addon_catalog = addon_catalog
    app.add_middleware(AuthMiddleware, auth=auth)
    return app


_workflow_run_events = workflow_run_events


def _non_negative_int_query_param(request: Request, name: str, *, default: int) -> int:
    value = request.query_params.get(name)
    if value is None or not str(value).strip():
        return default
    try:
        parsed = int(str(value).strip())
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer.") from exc
    if parsed < 0:
        raise ValueError(f"{name} must be greater than or equal to 0.")
    return parsed


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

    service = build_admin_runtime(Path(args.runtime_dir))
    uvicorn.run(create_app(service), host=args.host, port=args.port, log_level="info")


__all__ = ["create_app", "main", "workflow_run_events", "_workflow_run_events"]
