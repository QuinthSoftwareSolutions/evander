from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
import os
from pathlib import Path
import re
from typing import Any
from uuid import uuid4

from evander.addons import addon_json_object_store, addon_storage_path

from .execution_ownership import process_is_alive


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
        evidence_ids: list[str] | None = None,
        source: str = "agent",
        note: str | None = None,
    ) -> dict[str, Any]:
        action_id = f"bokio_action_{uuid4().hex[:16]}"
        normalized_kind = _required(kind, "kind")
        normalized_evidence_ids = _evidence_ids(evidence_ids)
        if normalized_evidence_ids and normalized_kind != "journal_entry":
            raise ValueError("evidence_ids are supported only for prepared journal entries")
        action = {
            "action_id": action_id,
            "kind": normalized_kind,
            "payload": dict(payload),
            "evidence_ids": normalized_evidence_ids,
            "evidence_operation_key": (
                f"prepared-action:{action_id}" if normalized_evidence_ids else None
            ),
            "evidence_result": None,
            "source": str(source or "agent").strip() or "agent",
            "note": str(note or "").strip() or None,
            "status": "pending_approval",
            "created_at": _now(),
            "updated_at": _now(),
            "approved_at": None,
            "rejected_at": None,
            "executed_at": None,
            "execution_started_at": None,
            "execution_owner_pid": None,
            "attention_at": None,
            "result": None,
            "error": None,
            "history": [
                {
                    "status": "pending_approval",
                    "at": _now(),
                    "detail": "Prepared action created",
                }
            ],
        }

        def updater(root: dict[str, Any]) -> None:
            actions = root.setdefault("actions", {})
            if not isinstance(actions, dict):
                root["actions"] = actions = {}
            actions[action_id] = action

        self.store.update_object(updater)
        return action

    def approve_action(self, action_id: str) -> dict[str, Any]:
        return self._transition(
            action_id,
            status="approved",
            timestamp_field="approved_at",
            expected_statuses={"pending_approval"},
            detail="Approved for execution",
        )

    def reject_action(self, action_id: str, *, reason: str | None = None) -> dict[str, Any]:
        return self._transition(
            action_id,
            status="rejected",
            timestamp_field="rejected_at",
            update={"rejection_reason": str(reason or "").strip() or None},
            expected_statuses={"pending_approval", "approved", "failed"},
            detail="Rejected by operator",
        )

    def claim_execution(
        self,
        action_id: str,
        *,
        approve: bool = False,
        retry: bool = False,
        confirm_not_created: bool = False,
    ) -> tuple[dict[str, Any], bool]:
        normalized = _required(action_id, "action_id")
        now = _now()

        def updater(root: dict[str, Any]) -> tuple[dict[str, Any], bool]:
            actions = root.setdefault("actions", {})
            if not isinstance(actions, dict) or normalized not in actions:
                raise KeyError(normalized)
            action = dict(actions[normalized])
            status = str(action.get("status") or "")
            if status == "executed":
                return action, False
            if status == "pending_approval":
                if not approve:
                    raise ValueError(
                        f"Bokio prepared action {normalized} must be approved before execution"
                    )
                action["approved_at"] = action.get("approved_at") or now
            elif status == "approved":
                pass
            elif status == "failed":
                if not retry:
                    raise ValueError(
                        f"Bokio prepared action {normalized} failed; request an explicit retry"
                    )
            elif status == "needs_attention":
                if not confirm_not_created:
                    raise ValueError(
                        f"Bokio prepared action {normalized} has an uncertain outcome; inspect "
                        "Bokio and confirm it was not created before retrying"
                    )
            elif status == "executing":
                if not confirm_not_created:
                    raise ValueError(
                        f"Bokio prepared action {normalized} is executing or has an unknown "
                        "outcome; inspect Bokio and confirm it was not created before retrying"
                    )
                owner_alive = process_is_alive(action.get("execution_owner_pid"))
                if owner_alive is None:
                    raise ValueError(
                        f"Bokio prepared action {normalized} cannot be safely recovered because "
                        "its execution owner is unknown"
                    )
                if owner_alive:
                    raise ValueError(
                        f"Bokio prepared action {normalized} is still executing in a live "
                        "process; wait for that attempt to finish"
                    )
            else:
                raise ValueError(
                    f"Bokio prepared action {normalized} cannot execute from status {status!r}"
                )
            action["status"] = "executing"
            action["execution_started_at"] = now
            action["execution_owner_pid"] = os.getpid()
            action["updated_at"] = now
            action["error"] = None
            detail = (
                "Execution recovered after operator confirmed the action was absent in Bokio"
                if status in {"needs_attention", "executing"}
                else "Execution claimed"
            )
            self._append_history(action, status="executing", at=now, detail=detail)
            actions[normalized] = action
            return action, True

        try:
            action, claimed = self.store.update_object(updater)
        except KeyError as exc:
            raise ValueError(f"Unknown Bokio prepared action: {normalized}") from exc
        return dict(action), bool(claimed)

    def mark_executed(
        self,
        action_id: str,
        *,
        result: Any,
        evidence_result: Any | None = None,
    ) -> dict[str, Any]:
        return self._transition(
            action_id,
            status="executed",
            timestamp_field="executed_at",
            update={
                "result": result,
                "evidence_result": evidence_result,
                "error": None,
                "execution_owner_pid": None,
            },
            expected_statuses={"approved", "executing"},
            detail="Bokio action executed",
        )

    def mark_failed(
        self,
        action_id: str,
        *,
        error: Any,
        detail: str = "Execution failed before a successful Bokio result",
    ) -> dict[str, Any]:
        return self._transition(
            action_id,
            status="failed",
            timestamp_field="executed_at",
            update={"error": error, "execution_owner_pid": None},
            expected_statuses={"approved", "executing"},
            detail=detail,
        )

    def mark_needs_attention(self, action_id: str, *, error: Any) -> dict[str, Any]:
        return self._transition(
            action_id,
            status="needs_attention",
            timestamp_field="attention_at",
            update={"error": error, "execution_owner_pid": None},
            expected_statuses={"executing"},
            detail="Execution outcome is uncertain; reconciliation required",
        )

    def _transition(
        self,
        action_id: str,
        *,
        status: str,
        timestamp_field: str,
        update: dict[str, Any] | None = None,
        expected_statuses: set[str] | None = None,
        detail: str | None = None,
    ) -> dict[str, Any]:
        normalized = _required(action_id, "action_id")
        now = _now()

        def updater(root: dict[str, Any]) -> dict[str, Any]:
            actions = root.setdefault("actions", {})
            if not isinstance(actions, dict) or normalized not in actions:
                raise KeyError(normalized)
            action = dict(actions[normalized])
            current_status = str(action.get("status") or "")
            if current_status == status:
                return action
            if expected_statuses is not None and current_status not in expected_statuses:
                raise ValueError(
                    f"Bokio prepared action {normalized} cannot transition from "
                    f"{current_status!r} to {status!r}"
                )
            action["status"] = status
            action[timestamp_field] = now
            action["updated_at"] = now
            if update:
                action.update(update)
            self._append_history(action, status=status, at=now, detail=detail)
            actions[normalized] = action
            return action

        try:
            result = self.store.update_object(updater)
        except KeyError as exc:
            raise ValueError(f"Unknown Bokio prepared action: {normalized}") from exc
        return dict(result)

    @staticmethod
    def _append_history(
        action: dict[str, Any], *, status: str, at: str, detail: str | None
    ) -> None:
        history = action.get("history")
        if not isinstance(history, list):
            history = []
        history.append({"status": status, "at": at, "detail": detail})
        action["history"] = history


def _required(value: str, field_name: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{field_name} is required")
    return normalized


def _evidence_ids(values: list[str] | None) -> list[str]:
    if values is None:
        return []
    if not isinstance(values, list):
        raise ValueError("evidence_ids must be a JSON array")
    normalized = [str(value or "").strip() for value in values]
    if any(not value for value in normalized):
        raise ValueError("evidence_ids must contain non-empty strings")
    if len(set(normalized)) != len(normalized):
        raise ValueError("evidence_ids must not contain duplicates")
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
