from __future__ import annotations

from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from agent_sandbox.admin.service import AdminService
from agent_sandbox.admin.user_notifications import (
    AdminUserNotificationStore,
    normalize_admin_notification_user_id,
)
from agent_sandbox.admin.web.shared import AdminWebAuth, json_error


def user_notification_routes(service: AdminService, auth: AdminWebAuth) -> list[Route]:
    store = AdminUserNotificationStore(service.config.runtime_dir)

    def _current_user_id(request: Request) -> str:
        session = auth.require(request)
        user_id = normalize_admin_notification_user_id(
            session.get("login")
            or session.get("preferred_username")
            or session.get("username")
            or session.get("sub")
            or session.get("name")
            or auth.config.username
        )
        return user_id or "admin"

    async def list_user_notifications(request: Request) -> JSONResponse:
        user_id = _current_user_id(request)
        try:
            limit = int(str(request.query_params.get("limit") or "20").strip())
        except ValueError:
            return json_error("limit must be an integer")
        notifications, unread_count = store.list_unread(user_id=user_id, limit=limit)
        return JSONResponse(
            {
                "user_id": user_id,
                "unread_count": unread_count,
                "notifications": notifications,
            }
        )

    async def mark_user_notifications_read(request: Request) -> JSONResponse:
        user_id = _current_user_id(request)
        try:
            payload: Any = await request.json()
        except Exception:
            payload = {}
        if payload is None:
            payload = {}
        if not isinstance(payload, dict):
            return json_error("Request body must be a JSON object")
        mark_all = bool(payload.get("all"))
        raw_ids = payload.get("notification_ids")
        notification_ids: list[str] | None = None
        if not mark_all:
            if not isinstance(raw_ids, list):
                return json_error("notification_ids must be a list unless all is true")
            notification_ids = [str(item or "").strip() for item in raw_ids if str(item or "").strip()]
        marked_ids = store.mark_read(user_id=user_id, notification_ids=notification_ids)
        notifications, unread_count = store.list_unread(user_id=user_id, limit=20)
        return JSONResponse(
            {
                "user_id": user_id,
                "marked_notification_ids": marked_ids,
                "unread_count": unread_count,
                "notifications": notifications,
            }
        )

    return [
        Route("/api/admin/user-notifications", list_user_notifications, methods=["GET"]),
        Route("/api/admin/user-notifications/read", mark_user_notifications_read, methods=["POST"]),
    ]
