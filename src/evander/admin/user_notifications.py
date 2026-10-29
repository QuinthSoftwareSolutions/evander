from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Any, Mapping
from uuid import uuid4

STORE_FILE = "admin-user-notifications.json"
MAX_NOTIFICATIONS_PER_USER = 200


def utc_timestamp() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def normalize_admin_notification_user_id(value: object | None) -> str | None:
    text = str(value or "").strip().lower()
    return text or None


def admin_user_notification_store_path(runtime_dir: str | Path) -> Path:
    return Path(runtime_dir) / STORE_FILE


class AdminUserNotificationStore:
    def __init__(self, runtime_dir: str | Path) -> None:
        self.path = admin_user_notification_store_path(runtime_dir)
        self._lock = Lock()

    @staticmethod
    def _empty_payload() -> dict[str, Any]:
        return {"notifications": {}}

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return self._empty_payload()
        text = self.path.read_text(encoding="utf-8")
        if not text.strip():
            return self._empty_payload()
        payload = json.loads(text)
        if not isinstance(payload, dict):
            return self._empty_payload()
        if not isinstance(payload.get("notifications"), dict):
            payload["notifications"] = {}
        return payload

    def _write(self, payload: Mapping[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        serialized = json.dumps(payload, indent=2, sort_keys=True)
        temp_path = self.path.with_name(f".{self.path.name}.{uuid4().hex}.tmp")
        try:
            with temp_path.open("w", encoding="utf-8") as handle:
                handle.write(serialized)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, self.path)
        finally:
            if temp_path.exists():
                temp_path.unlink()

    def push(
        self,
        *,
        user_id: object,
        title: str,
        body: str,
        source: str,
        source_id: object,
        target_url: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized_user_id = normalize_admin_notification_user_id(user_id)
        if normalized_user_id is None:
            raise ValueError("user_id is required")
        normalized_title = str(title or "").strip()
        if not normalized_title:
            raise ValueError("title is required")
        normalized_source = str(source or "").strip()
        if not normalized_source:
            raise ValueError("source is required")
        normalized_source_id = str(source_id or "").strip()
        if not normalized_source_id:
            raise ValueError("source_id is required")
        normalized_body = str(body or "").strip()
        normalized_target_url = str(target_url or "").strip() or None
        now = utc_timestamp()

        with self._lock:
            payload = self._read()
            notifications: dict[str, Any] = payload["notifications"]
            for notification in notifications.values():
                if not isinstance(notification, dict):
                    continue
                if (
                    notification.get("user_id") == normalized_user_id
                    and notification.get("source") == normalized_source
                    and notification.get("source_id") == normalized_source_id
                ):
                    return dict(notification)

            notification_id = f"notif-{uuid4().hex}"
            notification = {
                "id": notification_id,
                "user_id": normalized_user_id,
                "title": normalized_title,
                "body": normalized_body,
                "source": normalized_source,
                "source_id": normalized_source_id,
                "target_url": normalized_target_url,
                "metadata": dict(metadata or {}),
                "created_at": now,
                "read_at": None,
            }
            notifications[notification_id] = notification
            self._prune_user_notifications(notifications, normalized_user_id)
            self._write(payload)
            return dict(notification)

    def list_unread(self, *, user_id: object, limit: int = 20) -> tuple[list[dict[str, Any]], int]:
        normalized_user_id = normalize_admin_notification_user_id(user_id)
        if normalized_user_id is None:
            return [], 0
        bounded_limit = max(1, min(int(limit), 100))
        with self._lock:
            payload = self._read()
            unread = [
                dict(notification)
                for notification in payload["notifications"].values()
                if isinstance(notification, dict)
                and notification.get("user_id") == normalized_user_id
                and not notification.get("read_at")
            ]
        unread.sort(
            key=lambda item: (str(item.get("created_at") or ""), str(item.get("id") or "")),
            reverse=True,
        )
        return unread[:bounded_limit], len(unread)

    def mark_read(self, *, user_id: object, notification_ids: list[str] | None = None) -> list[str]:
        normalized_user_id = normalize_admin_notification_user_id(user_id)
        if normalized_user_id is None:
            return []
        target_ids: set[str] | None = None
        if notification_ids is not None:
            target_ids = {str(item or "").strip() for item in notification_ids}
            target_ids.discard("")
            if not target_ids:
                return []
        now = utc_timestamp()
        marked: list[str] = []
        with self._lock:
            payload = self._read()
            for notification_id, notification in payload["notifications"].items():
                if not isinstance(notification, dict):
                    continue
                if notification.get("user_id") != normalized_user_id:
                    continue
                if target_ids is not None and notification_id not in target_ids:
                    continue
                if notification.get("read_at"):
                    continue
                notification["read_at"] = now
                marked.append(notification_id)
            if marked:
                self._write(payload)
        return marked

    def mark_source_read(
        self, *, user_id: object, source: object, source_ids: list[str]
    ) -> list[str]:
        normalized_user_id = normalize_admin_notification_user_id(user_id)
        if normalized_user_id is None:
            return []
        normalized_source = str(source or "").strip()
        if not normalized_source:
            return []
        target_source_ids = {str(item or "").strip() for item in source_ids}
        target_source_ids.discard("")
        if not target_source_ids:
            return []
        now = utc_timestamp()
        marked: list[str] = []
        with self._lock:
            payload = self._read()
            for notification_id, notification in payload["notifications"].items():
                if not isinstance(notification, dict):
                    continue
                if notification.get("user_id") != normalized_user_id:
                    continue
                if notification.get("source") != normalized_source:
                    continue
                if str(notification.get("source_id") or "") not in target_source_ids:
                    continue
                if notification.get("read_at"):
                    continue
                notification["read_at"] = now
                marked.append(notification_id)
            if marked:
                self._write(payload)
        return marked

    def mark_source_metadata_read(
        self,
        *,
        user_id: object,
        source: object,
        metadata: Mapping[str, Any],
    ) -> list[str]:
        normalized_user_id = normalize_admin_notification_user_id(user_id)
        if normalized_user_id is None:
            return []
        normalized_source = str(source or "").strip()
        if not normalized_source:
            return []
        target_metadata = {
            str(key or "").strip(): str(value or "").strip()
            for key, value in metadata.items()
            if str(key or "").strip() and str(value or "").strip()
        }
        if not target_metadata:
            return []
        now = utc_timestamp()
        marked: list[str] = []
        with self._lock:
            payload = self._read()
            for notification_id, notification in payload["notifications"].items():
                if not isinstance(notification, dict):
                    continue
                if notification.get("user_id") != normalized_user_id:
                    continue
                if notification.get("source") != normalized_source:
                    continue
                notification_metadata = notification.get("metadata")
                if not isinstance(notification_metadata, dict):
                    continue
                if any(
                    str(notification_metadata.get(key) or "").strip() != value
                    for key, value in target_metadata.items()
                ):
                    continue
                if notification.get("read_at"):
                    continue
                notification["read_at"] = now
                marked.append(notification_id)
            if marked:
                self._write(payload)
        return marked

    @staticmethod
    def _prune_user_notifications(notifications: dict[str, Any], user_id: str) -> None:
        user_items = [
            (notification_id, notification)
            for notification_id, notification in notifications.items()
            if isinstance(notification, dict) and notification.get("user_id") == user_id
        ]
        if len(user_items) <= MAX_NOTIFICATIONS_PER_USER:
            return
        user_items.sort(
            key=lambda item: (
                str(item[1].get("created_at") or ""),
                str(item[1].get("id") or ""),
            )
        )
        for notification_id, _notification in user_items[
            : len(user_items) - MAX_NOTIFICATIONS_PER_USER
        ]:
            notifications.pop(notification_id, None)
