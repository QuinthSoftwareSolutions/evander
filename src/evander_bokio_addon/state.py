from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
import re
from typing import Any
from uuid import uuid4

from evander.addons import addon_json_object_store, addon_storage_path


ADDON_ID = "bokio-addon"
STATE_FILE = "prepared-actions.json"
UPLOADS_DIR = "uploads"
_SAFE_UPLOAD_SCOPE_PATTERN = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")


def upload_staging_dir(
    runtime_dir: str | Path,
    *,
    session_id: str | None = None,
    scope: str = "admin",
) -> Path:
    if session_id is not None:
        return addon_storage_path(
            runtime_dir,
            ADDON_ID,
            UPLOADS_DIR,
            "sessions",
            _safe_upload_scope(session_id),
        )
    return addon_storage_path(
        runtime_dir,
        ADDON_ID,
        UPLOADS_DIR,
        "scopes",
        _safe_upload_scope(scope),
    )


class PreparedActionStore:
    def __init__(self, runtime_dir: str | Path) -> None:
        self.store = addon_json_object_store(
            runtime_dir,
            ADDON_ID,
            STATE_FILE,
            initial_object={"actions": {}},
        )

    def list_actions(self) -> list[dict[str, Any]]:
        payload = self.store.read_object()
        actions = payload.get("actions") if isinstance(payload, dict) else {}
        if not isinstance(actions, dict):
            return []
        return sorted(
            [dict(action) for action in actions.values() if isinstance(action, dict)],
            key=lambda item: str(item.get("created_at") or ""),
            reverse=True,
        )

    def get_action(self, action_id: str) -> dict[str, Any] | None:
        normalized = str(action_id or "").strip()
        if not normalized:
            return None
        payload = self.store.read_object()
        actions = payload.get("actions") if isinstance(payload, dict) else {}
        action = actions.get(normalized) if isinstance(actions, dict) else None
        return dict(action) if isinstance(action, dict) else None

    def create_action(
        self,
        *,
        kind: str,
        payload: dict[str, Any],
        source: str = "agent",
        note: str | None = None,
    ) -> dict[str, Any]:
        action_id = f"bokio_action_{uuid4().hex[:16]}"
        action = {
            "action_id": action_id,
            "kind": _required(kind, "kind"),
            "payload": dict(payload),
            "source": str(source or "agent").strip() or "agent",
            "note": str(note or "").strip() or None,
            "status": "pending_approval",
            "created_at": _now(),
            "updated_at": _now(),
            "approved_at": None,
            "rejected_at": None,
            "executed_at": None,
            "result": None,
            "error": None,
        }

        def updater(root: dict[str, Any]) -> None:
            actions = root.setdefault("actions", {})
            if not isinstance(actions, dict):
                root["actions"] = actions = {}
            actions[action_id] = action

        self.store.update_object(updater)
        return action

    def approve_action(self, action_id: str) -> dict[str, Any]:
        return self._transition(action_id, status="approved", timestamp_field="approved_at")

    def reject_action(self, action_id: str, *, reason: str | None = None) -> dict[str, Any]:
        return self._transition(
            action_id,
            status="rejected",
            timestamp_field="rejected_at",
            update={"rejection_reason": str(reason or "").strip() or None},
        )

    def mark_executed(self, action_id: str, *, result: Any) -> dict[str, Any]:
        return self._transition(
            action_id,
            status="executed",
            timestamp_field="executed_at",
            update={"result": result, "error": None},
        )

    def mark_failed(self, action_id: str, *, error: Any) -> dict[str, Any]:
        return self._transition(
            action_id,
            status="failed",
            timestamp_field="executed_at",
            update={"error": error},
        )

    def _transition(
        self,
        action_id: str,
        *,
        status: str,
        timestamp_field: str,
        update: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized = _required(action_id, "action_id")
        now = _now()

        def updater(root: dict[str, Any]) -> dict[str, Any]:
            actions = root.setdefault("actions", {})
            if not isinstance(actions, dict) or normalized not in actions:
                raise KeyError(normalized)
            action = dict(actions[normalized])
            action["status"] = status
            action[timestamp_field] = now
            action["updated_at"] = now
            if update:
                action.update(update)
            actions[normalized] = action
            return action

        try:
            result = self.store.update_object(updater)
        except KeyError as exc:
            raise ValueError(f"Unknown Bokio prepared action: {normalized}") from exc
        return dict(result)


def _required(value: str, field_name: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{field_name} is required")
    return normalized


def _safe_upload_scope(value: str) -> str:
    normalized = str(value or "").strip()
    if _SAFE_UPLOAD_SCOPE_PATTERN.fullmatch(normalized):
        return normalized
    if not normalized:
        normalized = "default"
    return f"sha256-{sha256(normalized.encode('utf-8')).hexdigest()[:24]}"


def _now() -> str:
    return datetime.now(UTC).isoformat()
