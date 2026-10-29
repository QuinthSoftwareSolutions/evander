from __future__ import annotations

from datetime import datetime
from typing import Any

from evander.admin.services import session_helpers
from evander.models import ApprovalRequest, ApprovalStatus, DirectoryPath, LocalIssueRecord

ISSUE_STATES: tuple[str, ...] = (
    "New",
    "Approved",
    "Committed",
    "In Progress",
    "Completed",
    "Closed",
)
_ISSUE_STATE_SET = set(ISSUE_STATES)
_ISSUE_PRIORITY_ORDER: dict[str, int] = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
_APPROVAL_STATUS_ORDER: dict[ApprovalStatus, int] = {
    ApprovalStatus.PENDING: 0,
    ApprovalStatus.EXPIRED: 1,
    ApprovalStatus.DENIED: 2,
    ApprovalStatus.APPROVED: 3,
    ApprovalStatus.EXHAUSTED: 4,
}


def normalize_local_issue_state(state: str | None, *, allow_empty: bool = False) -> str | None:
    normalized = str(state or "").strip()
    if not normalized:
        return None if allow_empty else "New"
    if normalized not in _ISSUE_STATE_SET:
        allowed = ", ".join(ISSUE_STATES)
        raise ValueError(f"Invalid issue state: {normalized!r}. Allowed values: {allowed}.")
    return normalized


def normalize_issue_priority(priority: str | None) -> str | None:
    normalized = str(priority or "").strip().upper()
    if not normalized:
        return None
    if normalized not in _ISSUE_PRIORITY_ORDER:
        allowed = ", ".join(sorted(_ISSUE_PRIORITY_ORDER))
        raise ValueError(f"Invalid issue priority: {normalized!r}. Allowed values: {allowed}.")
    return normalized


def local_issue_fields(issue: LocalIssueRecord) -> dict[str, object]:
    fields = issue.metadata.get("fields")
    if isinstance(fields, dict):
        return dict(fields)
    return {}


def issue_priority_sort_key(issue: LocalIssueRecord) -> tuple[int, datetime, datetime, str]:
    fields = local_issue_fields(issue)
    priority_value = fields.get("priority")
    if priority_value is None:
        priority_value = fields.get("Priority")
    priority = normalize_issue_priority(str(priority_value or "").strip()) or "P3"
    return (
        _ISSUE_PRIORITY_ORDER[priority],
        issue.updated_at,
        issue.created_at,
        issue.issue_number,
    )


def issue_number_sort_key(issue: LocalIssueRecord) -> tuple[int, int | str, str]:
    normalized = issue.issue_number.strip()
    if normalized.isdigit():
        return (0, int(normalized), issue.issue_id)
    return (1, normalized, issue.issue_id)


def approval_sort_key(request: ApprovalRequest) -> tuple[int, datetime, str]:
    return (_APPROVAL_STATUS_ORDER[request.status], request.created_at, request.request_id)


def directory_path_matches_scope(
    request_directory_path: str | None,
    selected_directory_path: str | None,
    *,
    direct_only: bool,
) -> bool:
    request_path = str(request_directory_path or "root").strip() or "root"
    selected_path = str(selected_directory_path or "root").strip() or "root"
    if request_path == selected_path:
        return True
    if direct_only:
        return False
    return request_path.startswith(f"{selected_path}/")


def extract_issue_number_from_payload(payload: dict[str, Any]) -> str | None:
    for key in ("issue_number", "number", "id"):
        value = payload.get(key)
        if value is None:
            continue
        normalized = str(value).strip()
        if normalized.isdigit():
            return normalized
    source = payload.get("source")
    if isinstance(source, dict):
        for key in ("work_item_id", "issue_id", "work_item_url"):
            value = str(source.get(key) or "").strip()
            if value.isdigit():
                return value
            parsed = session_helpers.extract_issue_number(value)
            if parsed is not None:
                return parsed
    for key in ("url",):
        parsed = session_helpers.extract_issue_number(str(payload.get(key) or "").strip())
        if parsed is not None:
            return parsed
    return None


def scoped_issue_id(
    issue_number: str,
    *,
    directory_path: str,
    provider: str,
    organization: str | None = None,
    project: str | None = None,
) -> str:
    normalized_directory_path = DirectoryPath(segments=directory_path).as_posix()
    parts = [normalized_directory_path, provider]
    if organization and project:
        parts.extend([organization.rstrip("/"), project])
    parts.append(issue_number)
    return "::".join(parts)


_normalize_local_issue_state = normalize_local_issue_state
_normalize_issue_priority = normalize_issue_priority
_local_issue_fields = local_issue_fields
_issue_priority_sort_key = issue_priority_sort_key
_issue_number_sort_key = issue_number_sort_key
_approval_sort_key = approval_sort_key
_directory_path_matches_scope = directory_path_matches_scope
_extract_issue_number_from_payload = extract_issue_number_from_payload
_scoped_issue_id = scoped_issue_id
