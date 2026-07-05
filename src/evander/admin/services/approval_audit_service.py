from __future__ import annotations

import json
import os
from collections import deque
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

from evander.admin.payloads import (
    ToolCallAuditCountsPayload,
    ToolCallAuditEntryPayload,
    ToolCallAuditListPayload,
    ToolCallAuditRelatedLinkPayload,
)
from evander.admin.services import issue_helpers
from evander.admin.tool_visibility import (
    ToolVisibilityPayload,
    build_tool_visibility_payload,
)
from evander.models import (
    ApprovalDecisionRecord,
    ApprovalQueueHealth,
    ApprovalRequest,
    ApprovalStatus,
    DirectoryPath,
)
from evander.repositories.audit_index import DEFAULT_TAIL_IMPORT_MAX_LINES
from evander.repositories.session_manifests import as_session_manifest_repository


@dataclass(frozen=True)
class ApprovalAuditAdminDependencies:
    runtime: Any
    config: Any
    tool_call_audit_index: Any | None
    directory_path: Any


class ApprovalAuditAdminService:
    """Approval queue and tool-call audit operations for the admin facade."""

    def __init__(self, dependencies: ApprovalAuditAdminDependencies) -> None:
        self._deps = dependencies
        self.runtime = dependencies.runtime
        self.config = dependencies.config
        self.tool_call_audit_index = dependencies.tool_call_audit_index

    def _session_repository(self):
        return as_session_manifest_repository(self.runtime.session_repository)

    def _refresh_approval_request(self, request: ApprovalRequest) -> ApprovalRequest:
        session = self._session_repository().get_session(request.session_id)
        mutated = False
        if not request.directory_path and session is not None:
            request.directory_path = session.directory_path
            mutated = True
        if request.vm_id is None and session is not None and session.vm_id:
            request.vm_id = session.vm_id
            mutated = True
        if not request.tool_name:
            request.tool_name = request.action_type
            mutated = True
        if (
            request.status in {ApprovalStatus.PENDING, ApprovalStatus.APPROVED}
            and request.expires_at <= _utc_now()
        ):
            request.status = ApprovalStatus.EXPIRED
            mutated = True
        if mutated:
            self.runtime.approvals.put(request.request_id, request)
        return request

    def _approval_matches_filters(
        self,
        request: ApprovalRequest,
        *,
        statuses: set[ApprovalStatus] | None = None,
        directory_path: str | None = None,
        direct_only: bool = False,
        session_id: str | None = None,
        vm_id: str | None = None,
        tool_name: str | None = None,
        min_age_seconds: int | None = None,
    ) -> bool:
        if statuses is not None and request.status not in statuses:
            return False
        if directory_path is not None:
            normalized_directory_path = self._deps.directory_path.normalize_directory_path(
                directory_path
            )
            request_directory_path = self._deps.directory_path.normalize_directory_path(
                request.directory_path
            )
            if not issue_helpers.directory_path_matches_scope(
                request_directory_path,
                normalized_directory_path,
                direct_only=direct_only,
            ):
                return False
        if session_id is not None and request.session_id != session_id.strip():
            return False
        if vm_id is not None and (request.vm_id or "") != vm_id.strip():
            return False
        if (
            tool_name is not None
            and (request.tool_name or request.action_type) != tool_name.strip()
        ):
            return False
        if min_age_seconds is not None:
            age_seconds = int((_utc_now() - request.created_at).total_seconds())
            if age_seconds < min_age_seconds:
                return False
        return True

    def list_approval_requests(
        self,
        *,
        statuses: set[ApprovalStatus] | None = None,
        directory_path: str | None = None,
        direct_only: bool = False,
        session_id: str | None = None,
        vm_id: str | None = None,
        tool_name: str | None = None,
        min_age_seconds: int | None = None,
    ) -> list[ApprovalRequest]:
        requests = [
            self._refresh_approval_request(request)
            for request in self.runtime.approvals.read_all().values()
        ]
        filtered = [
            request
            for request in requests
            if self._approval_matches_filters(
                request,
                statuses=statuses,
                directory_path=directory_path,
                direct_only=direct_only,
                session_id=session_id,
                vm_id=vm_id,
                tool_name=tool_name,
                min_age_seconds=min_age_seconds,
            )
        ]
        return sorted(filtered, key=issue_helpers.approval_sort_key)

    def approval_queue_health(
        self,
        *,
        statuses: set[ApprovalStatus] | None = None,
        directory_path: str | None = None,
        direct_only: bool = False,
        session_id: str | None = None,
        vm_id: str | None = None,
        tool_name: str | None = None,
        min_age_seconds: int | None = None,
    ) -> ApprovalQueueHealth:
        requests = self.list_approval_requests(
            statuses=statuses,
            directory_path=directory_path,
            direct_only=direct_only,
            session_id=session_id,
            vm_id=vm_id,
            tool_name=tool_name,
            min_age_seconds=min_age_seconds,
        )
        health = ApprovalQueueHealth(total=len(requests))
        pending_ages: list[int] = []
        for request in requests:
            if request.status == ApprovalStatus.PENDING:
                health.pending += 1
                pending_ages.append(int((_utc_now() - request.created_at).total_seconds()))
            elif request.status == ApprovalStatus.APPROVED:
                health.approved += 1
            elif request.status == ApprovalStatus.DENIED:
                health.denied += 1
            elif request.status == ApprovalStatus.EXPIRED:
                health.expired += 1
            elif request.status == ApprovalStatus.EXHAUSTED:
                health.exhausted += 1
        if pending_ages:
            health.oldest_pending_age_seconds = max(pending_ages)
        return health

    def get_approval_request(self, request_id: str) -> ApprovalRequest:
        request = self.runtime.approvals.get(request_id)
        if request is None:
            raise ValueError(f"Unknown approval request: {request_id}")
        return self._refresh_approval_request(request)

    def _approval_audit_context(
        self, request: ApprovalRequest, *, limit: int = 8
    ) -> list[dict[str, Any]]:
        if not self.config.audit_log_path.exists():
            return []
        try:
            lines = self.config.audit_log_path.read_text(encoding="utf-8").splitlines()
        except Exception:
            return []
        matches: list[dict[str, Any]] = []
        for raw_line in reversed(lines):
            try:
                payload = json.loads(raw_line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict) and approval_audit_payload_matches_request(
                payload, request
            ):
                matches.append(payload)
            if len(matches) >= limit:
                break
        matches.reverse()
        return matches

    def get_approval_review(self, request_id: str) -> dict[str, Any]:
        request = self.get_approval_request(request_id)
        return build_approval_review_payload(request, self._approval_audit_context(request))

    def decide_approval_request(
        self,
        request_id: str,
        *,
        approver: str,
        approve: bool,
        reason: str,
    ) -> ApprovalRequest:
        request = self.get_approval_request(request_id)
        normalized_approver = approver.strip()
        normalized_reason = reason.strip()
        if not normalized_approver:
            raise ValueError("approver is required")
        if not normalized_reason:
            raise ValueError("reason is required")
        if request.status != ApprovalStatus.PENDING:
            raise ValueError(f"Approval request {request_id} is not pending")
        decision_status = ApprovalStatus.APPROVED if approve else ApprovalStatus.DENIED
        decided_at = _utc_now()
        request.status = decision_status
        request.approver = normalized_approver
        request.decision_at = decided_at
        request.decision_reason = normalized_reason
        request.decision_history.append(
            ApprovalDecisionRecord(
                status=decision_status,
                approver=normalized_approver,
                reason=normalized_reason,
                decided_at=decided_at,
            )
        )
        self.runtime.approvals.put(request.request_id, request)
        self.runtime.audit.log_decision(
            event_type="approval_decision",
            session_id=request.session_id,
            vm_id=request.vm_id,
            approval_request_id=request.request_id,
            decision=request.status.value,
            reason=normalized_reason,
            args={
                "request_id": request.request_id,
                "tool_name": request.tool_name or request.action_type,
                "approver": normalized_approver,
                "payload_digest": request.payload_digest,
            },
            result={
                "status": request.status.value,
                "tool_name": request.tool_name or request.action_type,
                "payload_digest": request.payload_digest,
                "request_id": request.request_id,
            },
        )
        return request

    def revoke_approval_request(
        self,
        request_id: str,
        *,
        approver: str,
        reason: str,
    ) -> ApprovalRequest:
        request = self.get_approval_request(request_id)
        normalized_approver = approver.strip()
        normalized_reason = reason.strip()
        if not normalized_approver:
            raise ValueError("approver is required")
        if not normalized_reason:
            raise ValueError("reason is required")
        if request.status != ApprovalStatus.APPROVED:
            raise ValueError(f"Approval request {request_id} is not approved")
        if request.expires_at <= _utc_now():
            raise ValueError(f"Approval request {request_id} has expired")
        decided_at = _utc_now()
        request.status = ApprovalStatus.DENIED
        request.approver = normalized_approver
        request.decision_at = decided_at
        request.decision_reason = normalized_reason
        request.decision_history.append(
            ApprovalDecisionRecord(
                status=ApprovalStatus.DENIED,
                approver=normalized_approver,
                reason=normalized_reason,
                decided_at=decided_at,
            )
        )
        self.runtime.approvals.put(request.request_id, request)
        self.runtime.audit.log_decision(
            event_type="approval_decision",
            session_id=request.session_id,
            vm_id=request.vm_id,
            approval_request_id=request.request_id,
            decision=request.status.value,
            reason=normalized_reason,
            args={
                "request_id": request.request_id,
                "tool_name": request.tool_name or request.action_type,
                "approver": normalized_approver,
                "payload_digest": request.payload_digest,
            },
            result={
                "status": request.status.value,
                "action": "revoke_approval",
                "tool_name": request.tool_name or request.action_type,
                "payload_digest": request.payload_digest,
                "request_id": request.request_id,
            },
        )
        return request

    def tool_visibility_payload(
        self,
        *,
        target: str,
        mcp_transport_session_id: str | None = None,
        workflow_run_id: str | None = None,
        mcp_tool_approval_lease_id: str | None = None,
    ) -> ToolVisibilityPayload:
        normalized_target = str(target or "").strip()
        if not normalized_target:
            raise ValueError("target is required")
        session = self.runtime.session_repository.get_session(normalized_target)
        if session is None:
            raise ValueError(f"Unknown session: {normalized_target}")
        return build_tool_visibility_payload(
            self,
            session,
            mcp_transport_session_id=mcp_transport_session_id,
            workflow_run_id=workflow_run_id,
            mcp_tool_approval_lease_id=mcp_tool_approval_lease_id,
        )

    def list_tool_call_audit_entries(
        self,
        *,
        session_id: str | None = None,
        tool_name: str | None = None,
        status: str | None = None,
        directory_path: str | None = None,
        direct_only: bool = False,
        created_after: datetime | None = None,
        created_before: datetime | None = None,
        q: str | None = None,
        limit: int | None = None,
        recent_only: bool = False,
        index_only: bool = False,
    ) -> ToolCallAuditListPayload:
        normalized_limit = 100 if limit is None else max(1, min(int(limit), 500))
        normalized_status = tool_call_normalize_status(status)
        normalized_directory_path = (
            agent_record_directory_path(directory_path) if directory_path is not None else None
        )
        indexed_page = self._list_tool_call_audit_entries_from_index(
            session_id=session_id,
            tool_name=tool_name,
            status=normalized_status,
            directory_path=normalized_directory_path,
            direct_only=direct_only,
            created_after=created_after,
            created_before=created_before,
            q=q,
            limit=normalized_limit,
            recent_only=recent_only,
            allow_import=not index_only,
        )
        if indexed_page is not None:
            return indexed_page
        if index_only:
            return ToolCallAuditListPayload(limit=normalized_limit)
        if recent_only and tool_call_audit_tail_scan_supported(
            session_id=session_id,
            tool_name=tool_name,
            status=normalized_status,
            created_after=created_after,
            created_before=created_before,
            q=q,
        ):
            return self._list_recent_tool_call_audit_entries_from_tail(
                session_id=session_id,
                tool_name=tool_name,
                status=normalized_status,
                directory_path=normalized_directory_path,
                direct_only=direct_only,
                created_after=created_after,
                created_before=created_before,
                q=q,
                limit=normalized_limit,
                max_lines=tool_call_recent_scan_max_lines(normalized_limit),
            )
        entries: deque[ToolCallAuditEntryPayload] = deque(maxlen=normalized_limit)
        counts = ToolCallAuditCountsPayload()
        if not self.config.audit_log_path.exists():
            return ToolCallAuditListPayload(limit=normalized_limit)
        try:
            with self.config.audit_log_path.open(encoding="utf-8") as handle:
                for raw_line in handle:
                    line = raw_line.strip()
                    if not line:
                        continue
                    counts.scanned += 1
                    try:
                        payload = json.loads(line)
                    except json.JSONDecodeError:
                        counts.skipped_malformed += 1
                        continue
                    if not isinstance(payload, dict):
                        counts.skipped_malformed += 1
                        continue
                    event_type = str(payload.get("event_type") or "").strip().casefold()
                    if event_type != "tool_call":
                        counts.skipped_non_tool_call += 1
                        continue
                    entry = tool_call_audit_entry_from_payload(payload)
                    if entry is None:
                        counts.skipped_malformed += 1
                        continue
                    if not tool_call_audit_entry_matches_filters(
                        entry,
                        session_id=session_id,
                        tool_name=tool_name,
                        status=normalized_status,
                        directory_path=normalized_directory_path,
                        direct_only=direct_only,
                        created_after=created_after,
                        created_before=created_before,
                        q=q,
                    ):
                        continue
                    counts.matched += 1
                    counts.status_counts[entry.status or "success"] = (
                        counts.status_counts.get(entry.status or "success", 0) + 1
                    )
                    entries.append(entry)
        except Exception:
            return ToolCallAuditListPayload(limit=normalized_limit)
        items = list(entries)
        items.reverse()
        counts.returned = len(items)
        return ToolCallAuditListPayload(
            items=items,
            counts=ToolCallAuditCountsPayload(
                scanned=counts.scanned,
                matched=counts.matched,
                returned=counts.returned,
                skipped_malformed=counts.skipped_malformed,
                skipped_non_tool_call=counts.skipped_non_tool_call,
                status_counts=dict(sorted(counts.status_counts.items())),
            ),
            limit=normalized_limit,
            has_more=counts.matched > len(items),
        )

    def _list_tool_call_audit_entries_from_index(
        self,
        *,
        session_id: str | None,
        tool_name: str | None,
        status: str | None,
        directory_path: str | None,
        direct_only: bool,
        created_after: datetime | None,
        created_before: datetime | None,
        q: str | None,
        limit: int,
        recent_only: bool,
        allow_import: bool = True,
    ) -> ToolCallAuditListPayload | None:
        index = self.tool_call_audit_index
        if index is None:
            return None
        recent_tail_partial = False
        try:
            if allow_import:
                full_import_complete = index.import_is_complete(self.config.audit_log_path)
                use_index = full_import_complete

                if created_after is not None:
                    import_result = index.import_from_jsonl_tail(
                        self.config.audit_log_path,
                        created_after=created_after,
                        max_lines=DEFAULT_TAIL_IMPORT_MAX_LINES,
                    )
                    use_index = (
                        full_import_complete
                        or import_result.complete
                        or import_result.reached_time_boundary
                    )
                elif recent_only or not any(
                    str(value or "").strip()
                    for value in (
                        session_id,
                        tool_name,
                        status,
                        directory_path,
                        created_before,
                        q,
                    )
                ):
                    import_result = index.import_from_jsonl_tail(
                        self.config.audit_log_path,
                        max_lines=tool_call_recent_scan_max_lines(limit),
                    )
                    recent_tail_partial = recent_only and not (
                        full_import_complete or import_result.complete
                    )
                    use_index = full_import_complete or import_result.complete or recent_only
                elif not full_import_complete:
                    index.import_from_jsonl(self.config.audit_log_path, max_lines=1_000)
                    use_index = index.import_is_complete(self.config.audit_log_path)

                if not use_index:
                    return None

            page = index.query_tool_calls(
                session_id=session_id,
                tool_name=tool_name,
                status=status,
                directory_path=directory_path,
                direct_only=direct_only,
                created_after=created_after,
                created_before=created_before,
                q=q,
                limit=limit,
            )
        except Exception:
            return None

        items: list[ToolCallAuditEntryPayload] = []
        for payload in page.payloads:
            entry = tool_call_audit_entry_from_payload(payload)
            if entry is not None:
                items.append(entry)
        return ToolCallAuditListPayload(
            items=items,
            counts=ToolCallAuditCountsPayload(
                scanned=page.matched,
                matched=page.matched,
                returned=len(items),
                status_counts=page.status_counts,
            ),
            limit=limit,
            has_more=page.has_more or recent_tail_partial,
        )

    def _list_recent_tool_call_audit_entries_from_tail(
        self,
        *,
        session_id: str | None,
        tool_name: str | None,
        status: str | None,
        directory_path: str | None,
        direct_only: bool,
        created_after: datetime | None,
        created_before: datetime | None,
        q: str | None,
        limit: int,
        max_lines: int,
    ) -> ToolCallAuditListPayload:
        if not self.config.audit_log_path.exists():
            return ToolCallAuditListPayload(limit=limit)

        counts = ToolCallAuditCountsPayload()
        items: list[ToolCallAuditEntryPayload] = []
        has_more = False
        try:
            for line in iter_file_lines_reverse(self.config.audit_log_path):
                if counts.scanned >= max_lines:
                    has_more = True
                    break
                line = line.strip()
                if not line:
                    continue
                counts.scanned += 1
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    counts.skipped_malformed += 1
                    continue
                if not isinstance(payload, dict):
                    counts.skipped_malformed += 1
                    continue
                event_type = str(payload.get("event_type") or "").strip().casefold()
                if event_type != "tool_call":
                    counts.skipped_non_tool_call += 1
                    continue
                entry = tool_call_audit_entry_from_payload(payload)
                if entry is None:
                    counts.skipped_malformed += 1
                    continue
                if not tool_call_audit_entry_matches_filters(
                    entry,
                    session_id=session_id,
                    tool_name=tool_name,
                    status=status,
                    directory_path=directory_path,
                    direct_only=direct_only,
                    created_after=created_after,
                    created_before=created_before,
                    q=q,
                ):
                    continue
                counts.matched += 1
                if len(items) >= limit:
                    has_more = True
                    break
                counts.status_counts[entry.status or "success"] = (
                    counts.status_counts.get(entry.status or "success", 0) + 1
                )
                items.append(entry)
        except Exception:
            return ToolCallAuditListPayload(limit=limit)

        counts.returned = len(items)
        return ToolCallAuditListPayload(
            items=items,
            counts=ToolCallAuditCountsPayload(
                scanned=counts.scanned,
                matched=counts.matched,
                returned=counts.returned,
                skipped_malformed=counts.skipped_malformed,
                skipped_non_tool_call=counts.skipped_non_tool_call,
                status_counts=dict(sorted(counts.status_counts.items())),
            ),
            limit=limit,
            has_more=has_more,
        )


def _utc_now() -> datetime:
    return datetime.now(UTC)


def agent_record_directory_path(directory_path: str | None) -> str:
    try:
        return DirectoryPath(segments=str(directory_path or "root")).as_posix()
    except ValueError:
        return "root"


def redact_sensitive_payload(value: Any, *, key_name: str | None = None) -> Any:
    normalized_key = (key_name or "").strip().lower()
    if normalized_key and any(marker in normalized_key for marker in _SENSITIVE_PAYLOAD_MARKERS):
        return "<redacted>"
    if isinstance(value, dict):
        return {
            str(key): redact_sensitive_payload(item, key_name=str(key))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_sensitive_payload(item) for item in value]
    return value


def parse_tool_call_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def tool_call_directory_matches(
    record_directory_path: str | None,
    selected_directory_path: str | None,
    *,
    direct_only: bool,
) -> bool:
    if selected_directory_path is None:
        return True
    record_path = agent_record_directory_path(record_directory_path)
    selected_path = agent_record_directory_path(selected_directory_path)
    if direct_only:
        return record_path == selected_path
    return record_path == selected_path or record_path.startswith(f"{selected_path}/")


def tool_call_normalize_status(status: Any) -> str | None:
    normalized = str(status or "").strip().casefold()
    if not normalized:
        return None
    if normalized in {"allowed", "approved", "success", "ok"}:
        return "success"
    if normalized in {"pending_approval"}:
        return "pending_approval"
    if normalized in {"denied", "blocked", "rejected"}:
        return "denied"
    if normalized in {"error", "failed", "failure"}:
        return "error"
    raise ValueError("status must be one of: success, pending_approval, denied, or error")


def tool_call_failure_status(event: dict[str, Any]) -> str:
    decision = str(event.get("decision") or event.get("status") or "").strip().casefold()
    result = event.get("result") if isinstance(event.get("result"), dict) else {}
    result_status = str(result.get("status") or "").strip().casefold()
    if decision == "pending_approval":
        return "pending_approval"
    if decision in {"denied", "blocked", "rejected"}:
        return "denied"
    if decision in {"error", "failed", "failure"}:
        return "error"
    if decision in {"allowed", "approved", "success", "ok"}:
        return "success"
    if result_status == "pending_approval":
        return "pending_approval"
    if result_status in {"denied", "blocked", "rejected"}:
        return "denied"
    if result_status in {"error", "failed", "failure"}:
        return "error"
    if result_status in {"allowed", "approved", "success", "ok"}:
        return "success"
    if isinstance(result, dict) and any(
        key in result for key in ("error", "error_class", "exception", "traceback")
    ):
        return "error"
    return "success"


def approval_audit_payload_matches_request(
    payload: dict[str, Any], request: ApprovalRequest
) -> bool:
    args_payload = payload.get("args") if isinstance(payload.get("args"), dict) else {}
    result_payload = payload.get("result") if isinstance(payload.get("result"), dict) else {}
    for candidate in (
        payload.get("approval_request_id"),
        args_payload.get("approval_request_id"),
        result_payload.get("approval_request_id"),
        args_payload.get("request_id"),
        result_payload.get("request_id"),
        payload.get("payload_digest"),
        args_payload.get("payload_digest"),
        result_payload.get("payload_digest"),
    ):
        text = str(candidate or "").strip()
        if not text:
            continue
        if text == request.request_id or text == request.payload_digest:
            return True
    return payload.get("session_id") == request.session_id


def compact_payload_summary(value: Any, *, max_length: int = 180) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
    elif isinstance(value, (dict, list)):
        if not value:
            return None
        text = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    else:
        text = str(value).strip()
    if not text:
        return None
    if len(text) <= max_length:
        return text
    return f"{text[: max_length - 1].rstrip()}..."


def approval_review_blocked_tool_call_diagnostics(
    audit_context: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    diagnostics: list[dict[str, Any]] = []
    for entry in reversed(audit_context):
        if not isinstance(entry, dict):
            continue
        if tool_call_failure_status(entry) != "denied":
            continue
        result = entry.get("result") if isinstance(entry.get("result"), dict) else {}
        diagnostic = result.get("approval_diagnostic")
        if isinstance(diagnostic, dict):
            diagnostics.append(diagnostic)
    return diagnostics


def build_approval_review_payload(
    request: ApprovalRequest, audit_context: list[dict[str, Any]]
) -> dict[str, Any]:
    now = _utc_now()
    age_seconds = int((now - request.created_at).total_seconds())
    expires_in_seconds = int((request.expires_at - now).total_seconds())
    redacted_payload = redact_sensitive_payload(request.payload)
    redacted_audit_context = [
        redact_sensitive_payload(entry) if isinstance(entry, dict) else entry
        for entry in audit_context
    ]
    request_payload = request.model_dump(mode="json")
    request_payload["payload"] = redacted_payload
    blocked_tool_call_diagnostics = approval_review_blocked_tool_call_diagnostics(
        redacted_audit_context
    )
    approval_diagnostics = {
        "request_id": request.request_id,
        "status": request.status.value,
        "action_type": request.action_type,
        "tool_name": request.tool_name or request.action_type,
        "payload_digest": request.payload_digest,
        "approver": request.approver,
        "decision_at": request_payload.get("decision_at"),
        "decision_reason": request.decision_reason,
        "created_at": request_payload.get("created_at"),
        "expires_at": request_payload.get("expires_at"),
        "age_seconds": age_seconds,
        "expires_in_seconds": expires_in_seconds,
        "payload_summary": compact_payload_summary(redacted_payload),
        "approved_payload": redacted_payload,
        "requested_payload": redacted_payload,
        "blocked_tool_call_diagnostics": blocked_tool_call_diagnostics,
    }
    return {
        "request": request_payload,
        "redacted_payload": redacted_payload,
        "payload_summary": compact_payload_summary(redacted_payload),
        "approval_diagnostics": approval_diagnostics,
        "audit_context": redacted_audit_context,
        "age_seconds": age_seconds,
        "expires_in_seconds": expires_in_seconds,
    }


def tool_call_error_summary(
    entry: dict[str, Any], status: str, result: dict[str, Any]
) -> str | None:
    for candidate in (
        entry.get("reason"),
        result.get("error"),
        result.get("message"),
        result.get("details"),
        result.get("summary"),
    ):
        text = str(candidate or "").strip()
        if text:
            return text
    if status == "denied":
        return "Denied"
    if status == "error":
        return "Tool call failed."
    if status == "pending_approval":
        return "Approval required."
    return None


def tool_call_safe_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return str(value)


def tool_call_related_links(
    entry: ToolCallAuditEntryPayload,
) -> list[ToolCallAuditRelatedLinkPayload]:
    links: list[ToolCallAuditRelatedLinkPayload] = []
    if entry.session_id:
        links.append(
            ToolCallAuditRelatedLinkPayload(
                kind="session",
                label="Session",
                value=entry.session_id,
                href=f"/api/admin/sessions/{quote(entry.session_id, safe='')}",
            )
        )
    if entry.vm_id:
        links.append(
            ToolCallAuditRelatedLinkPayload(
                kind="vm",
                label="VM",
                value=entry.vm_id,
                href=f"/api/admin/vms/{quote(entry.vm_id, safe='')}",
            )
        )
    if entry.approval_request_id:
        links.append(
            ToolCallAuditRelatedLinkPayload(
                kind="approval_request",
                label="Approval request",
                value=entry.approval_request_id,
                href=f"/api/admin/approvals/{quote(entry.approval_request_id, safe='')}",
            )
        )
    if entry.workflow_run_id:
        links.append(
            ToolCallAuditRelatedLinkPayload(
                kind="workflow_run",
                label="Workflow run",
                value=entry.workflow_run_id,
                href=(
                    "/api/workflow-editor/run-detail?run_id="
                    f"{quote(entry.workflow_run_id, safe='')}"
                ),
            )
        )
    if entry.trace_id:
        links.append(
            ToolCallAuditRelatedLinkPayload(
                kind="workflow_trace",
                label="Workflow trace",
                value=entry.trace_id,
                href=(f"/api/workflow-editor/run-detail?trace_id={quote(entry.trace_id, safe='')}"),
            )
        )
    return links


def tool_call_audit_entry_from_payload(
    payload: dict[str, Any],
) -> ToolCallAuditEntryPayload | None:
    created_at = parse_tool_call_datetime(
        payload.get("created_at") or payload.get("timestamp") or payload.get("occurred_at")
    )
    if created_at is None:
        return None
    raw_args = payload.get("args") if isinstance(payload.get("args"), dict) else {}
    raw_result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
    redacted_args = redact_sensitive_payload(raw_args)
    redacted_result = redact_sensitive_payload(raw_result)
    approval_diagnostic = redacted_result.get("approval_diagnostic")
    if not isinstance(approval_diagnostic, dict):
        approval_diagnostic = None
    status = tool_call_failure_status(payload)
    decision = str(payload.get("decision") or payload.get("status") or "").strip() or None
    request_id = None
    for candidate in (
        payload.get("request_id"),
        raw_args.get("request_id"),
        raw_result.get("request_id"),
    ):
        text = str(candidate or "").strip()
        if text:
            request_id = text
            break
    correlation_id = None
    for candidate in (
        payload.get("correlation_id"),
        raw_args.get("correlation_id"),
        raw_result.get("correlation_id"),
    ):
        text = str(candidate or "").strip()
        if text:
            correlation_id = text
            break
    approval_request_id = None
    for candidate in (
        payload.get("approval_request_id"),
        raw_args.get("approval_request_id"),
        raw_result.get("approval_request_id"),
    ):
        text = str(candidate or "").strip()
        if text:
            approval_request_id = text
            break
    mcp_tool_approval_lease_id = None
    for candidate in (
        payload.get("mcp_tool_approval_lease_id"),
        payload.get("tool_approval_lease_id"),
        raw_args.get("mcp_tool_approval_lease_id"),
        raw_args.get("tool_approval_lease_id"),
        raw_result.get("mcp_tool_approval_lease_id"),
        raw_result.get("tool_approval_lease_id"),
    ):
        text = str(candidate or "").strip()
        if text:
            mcp_tool_approval_lease_id = text
            break
    tool_name = None
    for candidate in (payload.get("tool_name"), payload.get("tool"), raw_args.get("tool_name")):
        text = str(candidate or "").strip()
        if text:
            tool_name = text
            break
    session_id = None
    for candidate in (
        payload.get("session_id"),
        raw_args.get("session_id"),
        raw_result.get("session_id"),
        payload.get("target_session_id"),
    ):
        text = str(candidate or "").strip()
        if text:
            session_id = text
            break
    vm_id = None
    for candidate in (
        payload.get("vm_id"),
        payload.get("vm_name"),
        raw_args.get("vm_id"),
        raw_args.get("vm_name"),
        raw_result.get("vm_id"),
        raw_result.get("vm_name"),
    ):
        text = str(candidate or "").strip()
        if text:
            vm_id = text
            break
    directory_path = agent_record_directory_path(payload.get("directory_path"))
    agent_role = None
    for candidate in (payload.get("agent_role"), payload.get("role")):
        text = str(candidate or "").strip()
        if text:
            agent_role = text
            break
    actor_user_id = None
    for candidate in (payload.get("actor_user_id"), payload.get("user_id")):
        text = str(candidate or "").strip()
        if text:
            actor_user_id = text
            break
    workflow_run_id = None
    for candidate in (
        payload.get("workflow_run_id"),
        raw_args.get("workflow_run_id"),
        raw_result.get("workflow_run_id"),
    ):
        text = str(candidate or "").strip()
        if text:
            workflow_run_id = text
            break
    trace_id = None
    for candidate in (
        payload.get("trace_id"),
        payload.get("workflow_trace_id"),
        raw_args.get("trace_id"),
        raw_args.get("workflow_trace_id"),
        raw_result.get("trace_id"),
        raw_result.get("workflow_trace_id"),
    ):
        text = str(candidate or "").strip()
        if text:
            trace_id = text
            break
    duration_ms = payload.get("duration_ms")
    try:
        normalized_duration_ms = int(duration_ms) if duration_ms is not None else None
    except (TypeError, ValueError):
        normalized_duration_ms = None
    entry = ToolCallAuditEntryPayload(
        created_at=created_at,
        session_id=session_id,
        vm_id=vm_id,
        directory_path=directory_path,
        agent_role=agent_role,
        actor_user_id=actor_user_id,
        tool_name=tool_name,
        request_id=request_id,
        correlation_id=correlation_id,
        approval_request_id=approval_request_id,
        mcp_tool_approval_lease_id=mcp_tool_approval_lease_id,
        workflow_run_id=workflow_run_id,
        trace_id=trace_id,
        duration_ms=normalized_duration_ms,
        decision=decision,
        status=status,
        error_summary=tool_call_error_summary(payload, status, raw_result),
        args=redacted_args,
        result=redacted_result,
        approval_diagnostic=approval_diagnostic,
    )
    entry.related_links = tool_call_related_links(entry)
    return entry


def tool_call_audit_tail_scan_supported(
    *,
    session_id: str | None,
    tool_name: str | None,
    status: str | None,
    created_after: datetime | None,
    created_before: datetime | None,
    q: str | None,
) -> bool:
    if created_after is not None or created_before is not None:
        return False
    return not any(str(value or "").strip() for value in (session_id, tool_name, status, q))


def tool_call_recent_scan_max_lines(limit: int) -> int:
    return max(1_000, min(DEFAULT_TAIL_IMPORT_MAX_LINES, max(1, int(limit)) * 25))


def iter_file_lines_reverse(path: Path, *, block_size: int = 64 * 1024) -> Iterator[str]:
    with path.open("rb") as handle:
        handle.seek(0, os.SEEK_END)
        position = handle.tell()
        buffer = b""
        while position > 0:
            read_size = min(block_size, position)
            position -= read_size
            handle.seek(position)
            chunk = handle.read(read_size)
            lines = (chunk + buffer).split(b"\n")
            buffer = lines[0]
            for raw_line in reversed(lines[1:]):
                if raw_line:
                    yield raw_line.decode("utf-8", errors="replace")
        if buffer:
            yield buffer.decode("utf-8", errors="replace")


def tool_call_audit_entry_matches_filters(
    entry: ToolCallAuditEntryPayload,
    *,
    session_id: str | None,
    tool_name: str | None,
    status: str | None,
    directory_path: str | None,
    direct_only: bool,
    created_after: datetime | None,
    created_before: datetime | None,
    q: str | None,
) -> bool:
    if session_id is not None and entry.session_id != str(session_id).strip():
        return False
    if tool_name is not None and entry.tool_name != str(tool_name).strip():
        return False
    if status is not None and entry.status != str(status).strip().casefold():
        return False
    if not tool_call_directory_matches(
        entry.directory_path,
        directory_path,
        direct_only=direct_only,
    ):
        return False
    if created_after is not None and entry.created_at < created_after:
        return False
    if created_before is not None and entry.created_at > created_before:
        return False
    normalized_query = str(q or "").strip().casefold()
    if normalized_query:
        search_haystack = " ".join(
            part
            for part in (
                tool_call_safe_text(entry.session_id),
                tool_call_safe_text(entry.vm_id),
                tool_call_safe_text(entry.directory_path),
                tool_call_safe_text(entry.agent_role),
                tool_call_safe_text(entry.actor_user_id),
                tool_call_safe_text(entry.tool_name),
                tool_call_safe_text(entry.request_id),
                tool_call_safe_text(entry.correlation_id),
                tool_call_safe_text(entry.approval_request_id),
                tool_call_safe_text(entry.mcp_tool_approval_lease_id),
                tool_call_safe_text(entry.workflow_run_id),
                tool_call_safe_text(entry.trace_id),
                tool_call_safe_text(entry.decision),
                tool_call_safe_text(entry.status),
                tool_call_safe_text(entry.error_summary),
                tool_call_safe_text(entry.args),
                tool_call_safe_text(entry.result),
            )
            if part
        ).casefold()
        if normalized_query not in search_haystack:
            return False
    return True


_SENSITIVE_PAYLOAD_MARKERS: tuple[str, ...] = (
    "token",
    "secret",
    "password",
    "pat",
    "authorization",
    "cookie",
    "private_key",
    "ssh_key",
)

_redact_sensitive_payload = redact_sensitive_payload
_parse_tool_call_datetime = parse_tool_call_datetime
_tool_call_directory_matches = tool_call_directory_matches
_tool_call_normalize_status = tool_call_normalize_status
_tool_call_failure_status = tool_call_failure_status
_approval_audit_payload_matches_request = approval_audit_payload_matches_request
_compact_payload_summary = compact_payload_summary
_approval_review_blocked_tool_call_diagnostics = approval_review_blocked_tool_call_diagnostics
_tool_call_error_summary = tool_call_error_summary
_tool_call_safe_text = tool_call_safe_text
_tool_call_related_links = tool_call_related_links
_tool_call_audit_entry_from_payload = tool_call_audit_entry_from_payload
_tool_call_audit_tail_scan_supported = tool_call_audit_tail_scan_supported
_tool_call_recent_scan_max_lines = tool_call_recent_scan_max_lines
_iter_file_lines_reverse = iter_file_lines_reverse
_tool_call_audit_entry_matches_filters = tool_call_audit_entry_matches_filters
