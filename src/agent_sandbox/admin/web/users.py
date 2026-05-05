from __future__ import annotations

from typing import Any

from starlette.requests import Request
from starlette.routing import BaseRoute, Route

from agent_sandbox.admin.web.shared import AdminWebAuth, json_error, json_ok
from agent_sandbox.admin.web.user_store import AdminWebUserRole, AdminWebUserStore


def _user_payload(user) -> dict[str, Any]:
    return user.model_dump(mode="json")


async def _json_body(request: Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except Exception as exc:
        raise ValueError("Request body must be valid JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("Request body must be a JSON object")
    return payload


def _require_user_manager(auth: AdminWebAuth, request: Request) -> None:
    auth.require(request)
    if not auth.can_manage_users(request):
        raise PermissionError("Admin user role is required")


def admin_user_routes(user_store: AdminWebUserStore, auth: AdminWebAuth) -> list[BaseRoute]:
    async def list_users(request: Request):
        try:
            _require_user_manager(auth, request)
        except PermissionError as exc:
            return json_error(str(exc), status_code=403)
        return json_ok({"users": [_user_payload(user) for user in user_store.list_users()]})

    async def upsert_user(request: Request):
        try:
            _require_user_manager(auth, request)
            payload = await _json_body(request)
            login = payload.get("login")
            role = str(payload.get("role") or "").strip().lower()
            if role not in {"admin", "regular"}:
                return json_error("role must be admin or regular")
            update: dict[str, Any] = {
                "login": login,
                "role": role,
            }
            if "display_name" in payload:
                update["display_name"] = payload["display_name"]
            user = user_store.upsert_user(**update)  # type: ignore[arg-type]
        except PermissionError as exc:
            return json_error(str(exc), status_code=403)
        except ValueError as exc:
            return json_error(str(exc))
        return json_ok({"user": _user_payload(user)})

    async def update_user(request: Request):
        login = request.path_params["login"]
        try:
            _require_user_manager(auth, request)
            payload = await _json_body(request)
            raw_role = payload.get("role")
            role: AdminWebUserRole | None
            if raw_role is None:
                role = None
            else:
                candidate = str(raw_role).strip().lower()
                if candidate not in {"admin", "regular"}:
                    return json_error("role must be admin or regular")
                role = candidate  # type: ignore[assignment]
            update: dict[str, Any] = {"login": login, "role": role}
            if "display_name" in payload:
                update["display_name"] = payload["display_name"]
            user = user_store.update_user(**update)
            if user is None:
                return json_error("Admin web user not found", status_code=404)
        except PermissionError as exc:
            return json_error(str(exc), status_code=403)
        except ValueError as exc:
            return json_error(str(exc))
        return json_ok({"user": _user_payload(user)})

    async def delete_user(request: Request):
        login = request.path_params["login"]
        try:
            _require_user_manager(auth, request)
            user = user_store.delete_user(login)
            if user is None:
                return json_error("Admin web user not found", status_code=404)
        except PermissionError as exc:
            return json_error(str(exc), status_code=403)
        except ValueError as exc:
            return json_error(str(exc))
        return json_ok({"user": _user_payload(user)})

    return [
        Route("/api/admin/users", list_users, methods=["GET"]),
        Route("/api/admin/users", upsert_user, methods=["POST"]),
        Route("/api/admin/users/{login}", update_user, methods=["PATCH"]),
        Route("/api/admin/users/{login}", delete_user, methods=["DELETE"]),
    ]


__all__ = ["admin_user_routes"]
