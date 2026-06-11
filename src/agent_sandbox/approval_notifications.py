from __future__ import annotations

import os
import logging
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from agent_sandbox.messaging.notifications import NotificationSender
from agent_sandbox.models import (
    ApprovalNotificationDeliveryRecord,
    ApprovalNotificationDeliveryStatus,
    ApprovalNotificationStatus,
    ApprovalRequest,
    utc_now,
)

logger = logging.getLogger(__name__)

_ADMIN_WEB_PUBLIC_BASE_URL_ENV = "AGENT_SANDBOX_ADMIN_WEB_PUBLIC_BASE_URL"
_ADMIN_WEB_ENV_RELATIVE_PATH = Path("config") / "admin-web.env"
_DEFAULT_ADMIN_WEB_BASE_URL = "http://127.0.0.1:8100"


class ApprovalNotificationDispatcher:
    def __init__(self, *, runtime: Any, project_dir: Path | None = None) -> None:
        self.runtime = runtime
        self.project_dir = Path(project_dir) if project_dir is not None else Path.cwd()

    def dispatch(self, request: ApprovalRequest) -> ApprovalRequest:
        review_command = self._review_command(request.request_id)
        try:
            target_ids = list(request.notification_target_ids)
            if not target_ids and request.notification_status == ApprovalNotificationStatus.QUEUED:
                effective_policy = self.runtime.policy_config.resolve_for_directory(
                    request.directory_path
                )
                target_ids = list(effective_policy.approval_notification_target_ids)
                request.notification_target_ids = target_ids
        except Exception as exc:  # pragma: no cover - exercised via tests
            error = str(exc).strip() or exc.__class__.__name__
            request.notification_status = ApprovalNotificationStatus.FAILED
            request.notification_error = error
            self._persist(request)
            logger.warning(
                "metric_approval_notification outcome=failed request_id=%s target_id=%s review_command=%s error=%s",
                request.request_id,
                "<policy-resolution>",
                review_command,
                error,
                exc_info=True,
            )
            return request

        request.notification_target_ids = target_ids

        if not target_ids:
            request.notification_status = ApprovalNotificationStatus.DISABLED
            request.notification_attempted_at = None
            request.notification_error = None
            request.notification_deliveries = []
            self._persist(request)
            logger.info(
                "metric_approval_notification outcome=disabled request_id=%s target_count=0 review_command=%s",
                request.request_id,
                self._review_command(request.request_id),
            )
            return request

        request.notification_status = ApprovalNotificationStatus.QUEUED
        request.notification_attempted_at = utc_now()
        request.notification_error = None
        request.notification_deliveries = []
        self._persist(request)

        try:
            sender = NotificationSender(
                project_dir=self.project_dir,
                runtime=self.runtime,
                directory_path=request.directory_path,
            )
            message = self._render_message(request)
            subject = f"Approval request {request.request_id}"
        except Exception as exc:  # pragma: no cover - exercised via tests
            error = str(exc).strip() or exc.__class__.__name__
            request.notification_status = ApprovalNotificationStatus.FAILED
            request.notification_error = error
            self._persist(request)
            logger.warning(
                "metric_approval_notification outcome=failed request_id=%s target_id=%s review_command=%s error=%s",
                request.request_id,
                "<notification-send>",
                review_command,
                error,
                exc_info=True,
            )
            return request

        sent_count = 0
        failure_messages: list[str] = []
        for target_id in target_ids:
            attempted_at = utc_now()
            try:
                result = sender.send(
                    communication_target_id=target_id,
                    message=message,
                    severity="warning",
                    subject=subject,
                    sender_session_id=f"approval:{request.request_id}",
                )
            except Exception as exc:  # pragma: no cover - exercised via tests
                error = str(exc).strip() or exc.__class__.__name__
                request.notification_deliveries.append(
                    ApprovalNotificationDeliveryRecord(
                        target_id=target_id,
                        status=ApprovalNotificationDeliveryStatus.FAILED,
                        attempted_at=attempted_at,
                        error=error,
                    )
                )
                request.notification_error = error
                failure_messages.append(f"{target_id}: {error}")
                self._persist(request)
                logger.warning(
                    "metric_approval_notification outcome=failed request_id=%s target_id=%s review_command=%s error=%s",
                    request.request_id,
                    target_id,
                    review_command,
                    error,
                    exc_info=True,
                )
                continue

            request.notification_deliveries.append(
                ApprovalNotificationDeliveryRecord(
                    target_id=target_id,
                    status=ApprovalNotificationDeliveryStatus.SENT,
                    attempted_at=attempted_at,
                    provider=str(result.get("provider") or "").strip() or None,
                    message_id=self._extract_message_id(result),
                    result_summary=self._summarize_result(result),
                )
            )
            sent_count += 1
            self._persist(request)

        if sent_count == len(target_ids):
            request.notification_status = ApprovalNotificationStatus.DELIVERED
            request.notification_error = None
        elif sent_count == 0:
            request.notification_status = ApprovalNotificationStatus.FAILED
            request.notification_error = "; ".join(failure_messages) if failure_messages else None
        else:
            request.notification_status = ApprovalNotificationStatus.PARTIAL
            request.notification_error = "; ".join(failure_messages) if failure_messages else None

        self._persist(request)
        return request

    def _persist(self, request: ApprovalRequest) -> None:
        self.runtime.approvals.put(request.request_id, request)

    @staticmethod
    def _review_command(request_id: str) -> str:
        return f"agent-sandbox-cli show-approval {request_id}"

    def _render_message(self, request: ApprovalRequest) -> str:
        review_command = self._review_command(request.request_id)
        decision_command = (
            f'agent-sandbox-cli approve {request.request_id} <approver> '
            '--approve|--deny --reason "..."'
        )
        review_url = self._approval_admin_web_url(request.request_id)
        approve_url = self._approval_admin_web_url(request.request_id, decision="approve")
        deny_url = self._approval_admin_web_url(request.request_id, decision="deny")
        vm_id = request.vm_id or "n/a"
        tool_name = request.tool_name or request.action_type
        return "\n".join(
            [
                "Approval request needs review.",
                f"request_id: {request.request_id}",
                f"action_type: {request.action_type}",
                f"tool_name: {tool_name}",
                f"session_id: {request.session_id}",
                f"directory_path: {request.directory_path}",
                f"vm_id: {vm_id}",
                f"expires_at: {request.expires_at.isoformat()}",
                f"human_summary: {request.human_summary}",
                f"payload_digest: {request.payload_digest}",
                "",
                "Admin Web:",
                f"  Review: [Open approval]({review_url})",
                f"  Approve: [Open approval with approve selected]({approve_url})",
                f"  Deny: [Open approval with deny selected]({deny_url})",
                "",
                "CLI fallback:",
                f"  {review_command}",
                "Decide:",
                f"  {decision_command}",
            ]
        )

    def _approval_admin_web_url(self, request_id: str, *, decision: str | None = None) -> str:
        base_url = self._admin_web_base_url()
        query = {"request_id": request_id}
        if decision is not None:
            query["decision"] = decision
        return f"{base_url}/#approvals?{urlencode(query)}"

    def _admin_web_base_url(self) -> str:
        env_value = os.environ.get(_ADMIN_WEB_PUBLIC_BASE_URL_ENV, "").strip().rstrip("/")
        if env_value:
            return env_value
        file_value = _load_optional_env_file(
            self.project_dir / _ADMIN_WEB_ENV_RELATIVE_PATH
        ).get(_ADMIN_WEB_PUBLIC_BASE_URL_ENV, "")
        return file_value.strip().rstrip("/") or _DEFAULT_ADMIN_WEB_BASE_URL

    @staticmethod
    def _extract_message_id(result: dict[str, Any]) -> str | None:
        for key in ("message_id", "draft_id", "id"):
            value = str(result.get(key) or "").strip()
            if value:
                return value
        return None

    @staticmethod
    def _summarize_result(result: dict[str, Any]) -> str | None:
        summary_keys = (
            "status",
            "provider",
            "communication_target_id",
            "channel_target",
            "channel_id",
            "team_id",
            "mailbox_id",
            "thread_id",
        )
        parts = [
            f"{key}={result[key]}"
            for key in summary_keys
            if result.get(key) not in (None, "")
        ]
        if not parts:
            return None
        return ", ".join(parts)


def _load_optional_env_file(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        logger.warning("Could not read Admin Web env file %s: %s", path, exc)
        return {}

    values: dict[str, str] = {}
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue
        values[key] = value.strip().strip('"').strip("'")
    return values
