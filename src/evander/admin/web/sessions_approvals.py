from __future__ import annotations

import hashlib
import json
from collections import OrderedDict
from copy import deepcopy
from datetime import datetime
from typing import Any

from pydantic import ValidationError
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import BaseRoute, Route

from evander.admin.composition import AdminRuntimeComponents
from evander.admin.services.session_helpers import COMMUNICATION_FILE_SESSION_POLICY_FIELDS
from evander.admin.tool_visibility import _tool_category, _tool_provider
from evander.admin.web.cache import AdminWebJsonResponseCache
from evander.admin.web.rbac import ADMIN_OWNER_PERMISSION
from evander.admin.web.shared import (
    AdminWebAuth,
    json_error,
    request_has_privileged_operations,
)
from evander.models import (
    ApprovalGrantSettings,
    ApprovalPayloadMode,
    ApprovalQueueHealth,
    ApprovalRequest,
    ApprovalStatus,
    DirectoryPath,
    McpToolPolicyRule,
    SessionSummary,
    ToolName,
    WakeScheduledRequest,
    normalize_mcp_tool_policies,
    tool_name_value,
    utc_now,
)
from evander.policy import mandatory_approval_required_for_tool
from evander.addons import LoadedAddonCatalog, addon_mcp_tool_catalog_payloads


_TOOL_DESCRIPTIONS: dict[ToolName, str] = {
    ToolName.DOCUMENTATION_LIST: (
        "List current project documentation available through the host documentation catalog."
    ),
    ToolName.DOCUMENTATION_SEARCH: (
        "Search current project documentation using case-insensitive literal text."
    ),
    ToolName.DOCUMENTATION_READ: ("Read a bounded line range from current project documentation."),
    ToolName.LIST_CONNECTORS: (
        "List connector operations explicitly granted to the current agent session."
    ),
    ToolName.CALL_CONNECTOR: (
        "Invoke one schema-validated operation on a connector granted to the current session."
    ),
    ToolName.ASSISTANT_READ_STATUS: (
        "Read high-level control-plane status for the assistant Admin chat surface."
    ),
    ToolName.ASSISTANT_LIST_AGENTS: (
        "List agent sessions visible to the assistant Admin chat surface."
    ),
    ToolName.ASSISTANT_INSPECT_WORKFLOWS: (
        "Inspect workflow definitions and recent workflow runtime state."
    ),
    ToolName.ASSISTANT_READ_CONFIG: (
        "Read directory-scoped configuration visible to the assistant Admin chat surface."
    ),
    ToolName.ASSISTANT_UPSERT_CONFIG: (
        "Create or update directory-scoped configuration through the assistant Admin chat surface."
    ),
    ToolName.ASSISTANT_CREATE_WORKFLOW: (
        "Create workflow drafts through the assistant Admin chat surface."
    ),
    ToolName.ASSISTANT_RUN_SAFE_OPERATION: (
        "Run allowlisted safe host operations through the assistant Admin chat surface."
    ),
    ToolName.ASSISTANT_POST_CHAT_REPLY: (
        "Post a reply back into the assistant Admin chat conversation."
    ),
    ToolName.CREATE_ADO_BACKLOG_ITEM: (
        "Create an Azure DevOps backlog item using the host-side ADO integration."
    ),
    ToolName.QUERY_ADO_BACKLOG_ITEMS: ("Search Azure DevOps backlog items with host-side filters."),
    ToolName.LIST_ADO_CURRENT_ITERATION_BACKLOG_ITEMS: (
        "List Azure DevOps backlog items in the configured current iteration."
    ),
    ToolName.GET_ADO_DAILY_STATUS_REPORT_DATA: (
        "Build daily status report data from Azure DevOps current-iteration items."
    ),
    ToolName.GET_ADO_BACKLOG_ITEM: ("Fetch details for one Azure DevOps backlog item."),
    ToolName.POST_ADO_BACKLOG_COMMENT: (
        "Post comments to Azure DevOps backlog items, including staged image artifacts when enabled."
    ),
    ToolName.DELETE_ADO_BACKLOG_ITEM: (
        "Delete Azure DevOps backlog items; soft delete by default and permanent only with destroy=true."
    ),
    ToolName.UPDATE_ADO_BACKLOG_TEXT: (
        "Update Azure DevOps backlog description or acceptance criteria text."
    ),
    ToolName.UPDATE_ADO_BACKLOG_TITLE: "Update the title of an Azure DevOps backlog item.",
    ToolName.UPDATE_ADO_BACKLOG_TAGS: "Add or remove Azure DevOps backlog item tags.",
    ToolName.RESOLVE_ADO_IDENTITY: "Resolve an Azure DevOps user identity for reporting filters.",
    ToolName.QUERY_ADO_WORK_ITEM_ACTIVITY: (
        "Find Azure DevOps work items with matching created, changed, or commented activity."
    ),
    ToolName.CREATE_LOCAL_ISSUE: ("Create a local issue record in the session's directory scope."),
    ToolName.LIST_ISSUES: ("List local issue records visible to the session."),
    ToolName.GET_ISSUE: ("Read details for a local issue record."),
    ToolName.SET_ISSUE_PLAN: ("Store or update the implementation plan attached to a local issue."),
    ToolName.APPROVE_ISSUE: ("Mark a local issue as approved for implementation."),
    ToolName.COMMIT_ISSUE: ("Move a local issue into the committed state when work is ready."),
    ToolName.REJECT_ISSUE: ("Reject a local issue instead of approving it for execution."),
    ToolName.CLAIM_NEXT_ISSUE: (
        "Claim the next available local issue in the session's directory scope."
    ),
    ToolName.COMPLETE_ISSUE: ("Mark a local issue as completed after the work is finished."),
    ToolName.GET_AGENT_STATUS: (
        "Inspect current guest agent state, including whether it is running."
    ),
    ToolName.GET_AGENT_LOGS: "Fetch recent logs for the guest agent attached to this session.",
    ToolName.DISCORD_GET_COMMANDS: (
        "Read Discord command traffic ingested for this session or directory."
    ),
    ToolName.DISCORD_POST_STATUS: (
        "Post a Discord status update from the host on behalf of the session."
    ),
    ToolName.COMMUNICATION_LIST_ATTACHMENTS: (
        "List inbound attachment metadata from allowlisted Discord, Teams, or email sources."
    ),
    ToolName.COMMUNICATION_DOWNLOAD_ATTACHMENT: (
        "Download an inbound attachment into the host communication artifact store."
    ),
    ToolName.LIST_INLINE_MEDIA: (
        "List inline media references available from an inbound communication item."
    ),
    ToolName.DOWNLOAD_INLINE_MEDIA: (
        "Download inline media into the host communication artifact store."
    ),
    ToolName.STAGE_ARTIFACT: (
        "Copy a file or directory from the agent workspace into the session artifacts folder."
    ),
    ToolName.COMMUNICATION_COPY_ARTIFACT_TO_WORKSPACE: (
        "Copy a host-managed communication artifact into the agent workspace."
    ),
    ToolName.COMMUNICATION_STAGE_WORKSPACE_FILE: (
        "Stage a workspace file as a host-managed communication artifact."
    ),
    ToolName.DISCORD_POST_FILE: (
        "Post staged communication file artifacts to an allowlisted Discord channel."
    ),
    ToolName.TEAMS_GET_MESSAGES: (
        "Read Microsoft Teams messages made available by the host ingest worker."
    ),
    ToolName.TEAMS_POST_STATUS: (
        "Post a Microsoft Teams status update from the host on behalf of the session."
    ),
    ToolName.TEAMS_POST_FILE: (
        "Post staged communication file artifacts to an allowlisted Microsoft Teams target."
    ),
    ToolName.TEAMS_MEETING_GET_EVENTS: (
        "Read Teams meeting transcript and status events captured for this session."
    ),
    ToolName.TEAMS_MEETING_SPEAK: (
        "Speak into the active Teams meeting through the host-managed meeting bridge."
    ),
    ToolName.TEAMS_MEETING_STOP: (
        "Leave the active Teams meeting and close the host-side meeting binding."
    ),
    ToolName.ADD_MESSAGE_REACTION: (
        "Add a reaction to an allowlisted Discord or Teams message from the host side."
    ),
    ToolName.LIST_CONTACTS: (
        "List directory-configured contact ids without exposing addresses or phone numbers."
    ),
    ToolName.MAKE_PHONE_CALL: ("Start host-side phone calls to directory-configured contact ids."),
    ToolName.GET_PHONE_CALL_RESULT: (
        "Read phone call status and transcripts for calls owned by the session."
    ),
    ToolName.SUBMIT_VOICE_QUERY_RESPONSE: (
        "Answer delegated live voice questions for phone calls projected from this session."
    ),
    ToolName.EMAIL_LIST_UNREAD: (
        "List unread email messages available through the host email provider."
    ),
    ToolName.EMAIL_LIST_MESSAGES: (
        "List and search email messages available through the host email provider."
    ),
    ToolName.EMAIL_GET_MESSAGE: (
        "Open a specific email message the session is allowed to inspect."
    ),
    ToolName.EMAIL_CREATE_DRAFT: ("Create an email draft on the host without sending it."),
    ToolName.EMAIL_CREATE_DRAFT_WITH_ATTACHMENTS: (
        "Create an email draft with staged communication file artifacts attached."
    ),
    ToolName.EMAIL_SEND: (
        "Send a host-recorded email draft through the configured email provider."
    ),
    ToolName.EMAIL_SEND_WITH_ATTACHMENTS: (
        "Send a host-recorded email draft with staged communication file artifacts attached."
    ),
    ToolName.APPROVAL_SUBMIT: (
        "Submit a human approval request for a guarded action the session wants reviewed."
    ),
    ToolName.APPROVAL_CHECK: (
        "Check the current state of a human approval request previously submitted by the session."
    ),
    ToolName.EMAIL_SEND_APPROVED: (
        "Send a previously approved email draft through the host email provider."
    ),
    ToolName.EMAIL_SEND_APPROVED_WITH_ATTACHMENTS: (
        "Send a previously approved email with staged communication file artifacts attached."
    ),
    ToolName.HAS_PENDING_FEEDBACK: (
        "Check whether pull request or review feedback is waiting for the session."
    ),
    ToolName.LIST_UNREAD_FEEDBACK: (
        "List unread review feedback items that need the session's attention."
    ),
    ToolName.LIST_ALL_FEEDBACK: (
        "List all review feedback visible to the session within its scope."
    ),
    ToolName.POST_PR_FEEDBACK_REPLY: (
        "Post a reply to pull request feedback using the host integration."
    ),
    ToolName.LIST_PULL_REQUESTS: (
        "List pull requests available to the session through the configured provider."
    ),
    ToolName.GET_PULL_REQUEST: "Fetch the latest details for a specific pull request.",
    ToolName.LIST_ADO_PR_COMMENT_ACTIVITY: (
        "Find Azure DevOps pull request comments matching an author and date range."
    ),
    ToolName.LIST_ADO_PR_COMMIT_ACTIVITY: (
        "Find Azure DevOps pull request commits matching an author and date range."
    ),
    ToolName.REQUEST_PR_REVIEW: ("Request reviewers on a pull request from the host side."),
    ToolName.POST_PR_COMMENT: ("Post a pull request comment using the host integration."),
    ToolName.POST_PR_INLINE_COMMENT: (
        "Post an inline pull request comment at a specific diff position."
    ),
    ToolName.SUBMIT_PR_REVIEW: (
        "Submit a pull request review decision and body from the host side."
    ),
    ToolName.HAS_PENDING_DISCORD_MESSAGES: (
        "Check whether the session has unread Discord messages waiting."
    ),
    ToolName.LIST_UNREAD_DISCORD_MESSAGES: (
        "List unread Discord messages available to the session."
    ),
    ToolName.LIST_ALL_DISCORD_MESSAGES: (
        "List all Discord messages visible to the session within its policy scope."
    ),
    ToolName.SCHEDULE_RESUME: (
        "Create or update a host-managed wake schedule that resumes this session later."
    ),
    ToolName.CANCEL_SCHEDULED_WAKE: (
        "Cancel an obsolete host-managed wake schedule owned by this session."
    ),
    ToolName.PROMPT_AGENT: (
        "Send a new prompt to another agent session within the allowed directory scope."
    ),
    ToolName.LIST_PENDING_AGENT_PROMPTS: (
        "List prompts queued for this agent session that have not been handled yet."
    ),
    ToolName.RESPOND_TO_AGENT: (
        "Reply to a pending agent prompt using its one-time response token."
    ),
    ToolName.CONSULT_CODEBASE: (
        "Ask a request-scoped agent to inspect an exact managed Git snapshot."
    ),
    ToolName.GET_CODEBASE_CONSULTATION: (
        "Read or briefly wait for a codebase consultation owned by this session."
    ),
    ToolName.CANCEL_CODEBASE_CONSULTATION: (
        "Cancel a queued or running codebase consultation owned by this session."
    ),
    ToolName.SUBMIT_CODEBASE_CONSULTATION_RESULT: (
        "Submit the bounded structured result for a request-scoped consultation worker."
    ),
    ToolName.RUN_SCRIPT: ("Run an approved host-side script on behalf of the session."),
    ToolName.GET_WORKFLOW_SNAPSHOT: (
        "Read the current workflow editor document and optional run snapshot."
    ),
    ToolName.SEMANTIC_UPSERT_TEXT: (
        "Insert or update text points in an allowlisted semantic collection."
    ),
    ToolName.SEMANTIC_SEARCH: ("Search an allowlisted semantic collection for relevant points."),
    ToolName.SEMANTIC_GET_POINTS: ("Read specific points from an allowlisted semantic collection."),
    ToolName.SEMANTIC_DELETE_POINTS: (
        "Delete specific points from an allowlisted semantic collection."
    ),
    ToolName.MSSQL_PROVISION_DATABASE: (
        "Provision a session-owned SQL Server database through the container worker."
    ),
    ToolName.MSSQL_LIST_DATABASES: "List SQL Server databases owned by this session.",
    ToolName.MSSQL_DROP_DATABASE: "Drop a SQL Server database owned by this session.",
    ToolName.COPILOT_HOST_AUTH: (
        "Bridge host-managed GitHub Copilot authentication into the guest when that provider is used."
    ),
}


_SESSION_POLICY_FIELD_KINDS: dict[str, str] = {
    "web_search_enabled": "bool",
    "communication_file_receive_enabled": "bool",
    "communication_file_send_enabled": "bool",
    "communication_file_max_bytes": "int_or_null",
    "communication_file_allowed_mime_types": "string_list",
    "communication_file_allowed_extensions": "string_list",
    "communication_file_blocked_extensions": "string_list",
    "communication_file_require_approval_for_send": "bool",
    "allowed_contact_ids": "string_list",
    "tool_argument_policies": "json",
}

_SESSION_POLICY_FIELD_LABELS: dict[str, str] = {
    field_name: field_name.removeprefix("communication_file_").replace("_", " ")
    for field_name in COMMUNICATION_FILE_SESSION_POLICY_FIELDS
}
_SESSION_POLICY_FIELD_LABELS["web_search_enabled"] = "web search"
_SESSION_POLICY_FIELD_LABELS["tool_argument_policies"] = "tool argument policy"


def _session_policy_field_catalog() -> list[dict[str, str]]:
    catalog = [
        {
            "field_name": "web_search_enabled",
            "label": _SESSION_POLICY_FIELD_LABELS["web_search_enabled"],
            "kind": _SESSION_POLICY_FIELD_KINDS["web_search_enabled"],
            "section": "Hosted tools",
        },
    ]
    catalog.append(
        {
            "field_name": "allowed_contact_ids",
            "label": "allowed contact ids",
            "kind": _SESSION_POLICY_FIELD_KINDS["allowed_contact_ids"],
            "section": "Contacts",
        }
    )
    catalog.extend(
        {
            "field_name": field_name,
            "label": _SESSION_POLICY_FIELD_LABELS[field_name],
            "kind": _SESSION_POLICY_FIELD_KINDS[field_name],
            "section": "Communication files",
        }
        for field_name in COMMUNICATION_FILE_SESSION_POLICY_FIELDS
    )
    catalog.append(
        {
            "field_name": "tool_argument_policies",
            "label": _SESSION_POLICY_FIELD_LABELS["tool_argument_policies"],
            "kind": _SESSION_POLICY_FIELD_KINDS["tool_argument_policies"],
            "section": "Tool argument policy",
        }
    )
    return catalog


def _parse_bool(value: str | None, *, default: bool = False) -> bool:
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"Invalid boolean value: {value}")


def _parse_int(value: str | None) -> int | None:
    if value is None or not value.strip():
        return None
    return int(value)


def _parse_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        raise ValueError("Datetime values must be ISO-8601 strings.")
    normalized = value.strip()
    if not normalized:
        return None
    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"
    try:
        return datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(f"Invalid datetime value: {value}") from exc


def _parse_statuses(value: str | None) -> set[ApprovalStatus] | None:
    if value is None or not value.strip():
        return None
    statuses: set[ApprovalStatus] = set()
    for part in value.split(","):
        normalized = part.strip().lower()
        if not normalized:
            continue
        statuses.add(ApprovalStatus(normalized))
    return statuses or None


def _iso(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, list):
        return [_iso(item) for item in value]
    if isinstance(value, dict):
        return {key: _iso(item) for key, item in value.items()}
    return value


def _approval_payload(request: ApprovalRequest) -> dict[str, Any]:
    payload = request.model_dump(mode="json")
    payload["tool_name"] = request.tool_name or request.action_type
    return payload


def _approval_review_payload(review: dict[str, Any]) -> dict[str, Any]:
    payload = _iso(review)
    request_payload = payload.get("request")
    if isinstance(request_payload, dict):
        normalized_request = dict(request_payload)
        normalized_request["tool_name"] = normalized_request.get(
            "tool_name"
        ) or normalized_request.get("action_type")
        if isinstance(payload.get("redacted_payload"), dict):
            normalized_request["payload"] = payload["redacted_payload"]
        payload["request"] = normalized_request
    return payload


def _approval_queue_health_payload(items: list[ApprovalRequest]) -> dict[str, Any]:
    health = ApprovalQueueHealth(total=len(items))
    pending_ages: list[int] = []
    now = utc_now()
    for request in items:
        if request.status == ApprovalStatus.PENDING:
            health.pending += 1
            pending_ages.append(int((now - request.created_at).total_seconds()))
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
    return health.model_dump(mode="json")


def _json_revision(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class _SessionPolicySummaryCache:
    def __init__(self, *, max_entries: int = 256) -> None:
        self._max_entries = max(1, max_entries)
        self._entries: OrderedDict[str, dict[str, Any]] = OrderedDict()

    def get(self, key: str) -> dict[str, Any] | None:
        payload = self._entries.get(key)
        if payload is None:
            return None
        self._entries.move_to_end(key)
        return deepcopy(payload)

    def put(self, key: str, payload: dict[str, Any]) -> None:
        self._entries[key] = deepcopy(payload)
        self._entries.move_to_end(key)
        while len(self._entries) > self._max_entries:
            self._entries.popitem(last=False)


def _rule_effective_settings(rule: McpToolPolicyRule | None) -> ApprovalGrantSettings:
    settings = ApprovalGrantSettings()
    if rule is not None and rule.default_settings is not None:
        settings = rule.default_settings
    if rule is not None:
        ttl_seconds = settings.ttl_seconds
        if rule.max_ttl_seconds is not None:
            ttl_seconds = min(ttl_seconds, rule.max_ttl_seconds)
        max_uses = settings.max_uses
        if rule.max_uses is not None:
            max_uses = rule.max_uses if max_uses is None else min(max_uses, rule.max_uses)
        payload_mode = settings.payload_mode
        if rule.require_exact_payload:
            payload_mode = ApprovalPayloadMode.EXACT
        settings = ApprovalGrantSettings(
            ttl_seconds=ttl_seconds,
            max_uses=max_uses,
            payload_mode=payload_mode,
        )
    return settings


def _tool_option(tool: ToolName) -> dict[str, Any]:
    category = _tool_category(tool)
    return {
        "value": tool.value,
        "label": tool.value.replace("_", " "),
        "description": _TOOL_DESCRIPTIONS.get(tool, ""),
        "group": category,
        "provider": _tool_provider(tool, category),
        "addon_id": None,
    }


def _model_option(model: str) -> dict[str, str]:
    return {"value": model, "label": model}


def _model_options_from_pricing_catalog(service: AdminRuntimeComponents) -> list[dict[str, str]]:
    status = service.model_usage_admin.read_model_pricing_catalog_status()
    if status.get("status") != "ok":
        return []
    catalog = status.get("catalog")
    if not isinstance(catalog, dict):
        return []
    rules = catalog.get("rules")
    if not isinstance(rules, list):
        return []

    options: list[dict[str, str]] = []
    seen_models: set[str] = set()
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        models = rule.get("models")
        if not isinstance(models, list):
            continue
        for raw_model in models:
            model = str(raw_model).strip()
            if not model or model in seen_models:
                continue
            seen_models.add(model)
            options.append(_model_option(model))
    return options


def _approval_actor_from_request(
    auth: AdminWebAuth, request: Request, payload: dict[str, Any]
) -> str:
    if not auth.config.auth_required:
        return str(payload.get("approver") or "").strip()
    session = auth.require(request)
    return str(
        session.get("name")
        or session.get("login")
        or session.get("sub")
        or session.get("sid")
        or ""
    ).strip()


def _compact_session_payload(summary: SessionSummary) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "session_id": summary.session_id,
        "vm_id": summary.vm_id,
        "vm_provider": summary.vm_provider,
        "vm_provider_id": summary.vm_provider_id,
        "model": summary.model,
        "directory_path": summary.directory_path,
        "expires_at": _iso(summary.expires_at),
        "created_at": _iso(summary.created_at),
        "issue_number": summary.issue_number,
        "is_expired": summary.is_expired,
        "container_lifecycle": (
            None
            if summary.container_lifecycle is None
            else summary.container_lifecycle.model_dump(mode="json")
        ),
    }
    return payload


def _session_policy_summary_payload(
    service: AdminRuntimeComponents,
    summary: SessionSummary,
    *,
    cache: _SessionPolicySummaryCache | None = None,
) -> dict[str, Any]:
    effective_policy = service.runtime.policy_config.resolve_for_session_manifest(summary)
    cache_key = _json_revision(
        {
            "session": summary.model_dump(mode="json"),
            "effective_policy": effective_policy.model_dump(mode="json"),
        }
    )
    if cache is not None:
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

    tool_summary = service.runtime.policy.allowed_tool_summary_for_session(
        summary,
        effective_policy=effective_policy,
    )
    payload = {
        "tool_summary": tool_summary,
        "effective_tool_policy": service.vm_agent_admin.effective_tool_policy_payload(
            summary,
            effective_policy=effective_policy,
            tool_summary=tool_summary,
        ),
    }
    if cache is not None:
        cache.put(cache_key, payload)
    return deepcopy(payload)


def _session_payload(
    service: AdminRuntimeComponents,
    summary: SessionSummary,
    *,
    policy_cache: _SessionPolicySummaryCache | None = None,
) -> dict[str, Any]:
    payload = summary.model_dump(mode="json")
    payload["allowed_tools"] = [tool_name_value(tool) for tool in summary.allowed_tools]
    payload.update(_session_policy_summary_payload(service, summary, cache=policy_cache))
    payload["approval_notification_target_options"] = [
        option.model_dump(mode="json")
        for option in service.vm_agent_admin.approval_notification_target_options(summary)
    ]
    try:
        grant_options = service.vm_agent_admin.agent_create_options(summary.directory_path)
    except ValueError:
        grant_options = {"communication_targets": [], "email_mailboxes": []}
    payload["communication_target_options"] = grant_options["communication_targets"]
    payload["email_mailbox_options"] = grant_options["email_mailboxes"]
    payload["runtime_image"] = service.vm_agent_admin.session_runtime_image_payload(summary)
    return payload


def _wake_schedule_payload(item: dict[str, Any]) -> dict[str, Any]:
    payload = _iso(item)
    schedule = payload.pop("schedule", {}) if isinstance(payload.get("schedule"), dict) else {}
    resume_prompt = (
        payload.pop("resume_prompt", {}) if isinstance(payload.get("resume_prompt"), dict) else {}
    )
    payload["cron"] = schedule.get("cron", payload.get("cron"))
    payload["resume_at"] = schedule.get("resume_at", payload.get("resume_at"))
    payload["prompt"] = resume_prompt.get("prompt", payload.get("prompt"))
    payload["metadata"] = resume_prompt.get("metadata", payload.get("metadata", {}))
    payload["schedule_kind"] = "cron" if payload.get("cron") else "resume_at"
    return payload


_WAKE_LEDGER_STATUSES = {
    "pending",
    "due",
    "dispatching",
    "queued",
    "dispatched",
    "failed",
    "missed_or_late",
    "completed_or_acknowledged",
    "cancelled",
}


def _parse_wake_statuses(value: str | None) -> list[str] | None:
    if value is None or not value.strip():
        return None
    statuses: list[str] = []
    seen: set[str] = set()
    for part in value.split(","):
        normalized = part.strip().lower()
        if not normalized:
            continue
        if normalized not in _WAKE_LEDGER_STATUSES:
            raise ValueError(f"Invalid wake status: {part.strip()}")
        if normalized in seen:
            continue
        seen.add(normalized)
        statuses.append(normalized)
    return statuses or None


def _wake_ledger_payload(item: dict[str, Any]) -> dict[str, Any]:
    return _iso(item)


def session_approval_routes(
    service: AdminRuntimeComponents,
    auth: AdminWebAuth,
    cache: AdminWebJsonResponseCache | None = None,
    addon_catalog: LoadedAddonCatalog | None = None,
) -> list[BaseRoute]:
    policy_summary_cache = _SessionPolicySummaryCache()
    addon_tool_options = (
        addon_mcp_tool_catalog_payloads(addon_catalog) if addon_catalog is not None else []
    )
    addon_tool_options_by_name = {item["value"]: item for item in addon_tool_options}
    core_tool_names = {tool.value for tool in ToolName}
    collisions = sorted(core_tool_names.intersection(addon_tool_options_by_name))
    if collisions:
        raise ValueError("add-on MCP tool names collide with core tools: " + ", ".join(collisions))
    merged_tool_options = [
        *[_tool_option(tool) for tool in ToolName],
        *addon_tool_options,
    ]

    def _validated_directory_path(directory_path: object | None) -> str | None:
        if directory_path is None:
            return None
        value = str(directory_path).strip() or "root"
        return DirectoryPath(segments=value).as_posix()

    def _owner_privileged_error(
        request: Request, *, directory_path: object | None = None
    ) -> JSONResponse | None:
        try:
            normalized_directory_path = _validated_directory_path(directory_path)
        except ValidationError as exc:
            return json_error(str(exc), status_code=400)
        if request_has_privileged_operations(
            auth=auth,
            service=service,
            request=request,
            required_permission=ADMIN_OWNER_PERMISSION,
            directory_path=normalized_directory_path,
        ):
            return None
        return json_error("Privileged operations are locked.", status_code=403)

    def _privileged_unlocked_error(request: Request) -> JSONResponse | None:
        if request_has_privileged_operations(auth=auth, service=service, request=request):
            return None
        return json_error("Privileged operations are locked.", status_code=403)

    def _owner_read_error(
        request: Request, *, directory_path: object | None = None
    ) -> JSONResponse | None:
        try:
            normalized_directory_path = _validated_directory_path(directory_path)
            auth.require_permission(
                request,
                ADMIN_OWNER_PERMISSION,
                directory_path=normalized_directory_path,
            )
        except ValidationError as exc:
            return json_error(str(exc), status_code=400)
        except PermissionError:
            return json_error("Permission required: admin.owner", status_code=403)
        return None

    def _can_read_directory(
        request: Request,
        directory_path: object | None,
        cache: dict[str, bool],
    ) -> bool:
        cache_key = str(directory_path or "root").strip() or "root"
        if cache_key not in cache:
            cache[cache_key] = _owner_read_error(request, directory_path=cache_key) is None
        return cache[cache_key]

    def _approval_request_or_error(
        request_id: str,
    ) -> tuple[ApprovalRequest | None, JSONResponse | None]:
        try:
            return service.approval_audit_admin.get_approval_request(request_id), None
        except ValueError as exc:
            return None, json_error(str(exc), status_code=404)

    async def approvals_index(request: Request) -> JSONResponse:
        params = request.query_params
        requested_directory_path = params.get("directory_path")
        if requested_directory_path is not None:
            auth_error = _owner_read_error(request, directory_path=requested_directory_path)
            if auth_error is not None:
                return auth_error
        try:
            statuses = _parse_statuses(params.get("statuses"))
            direct_only = _parse_bool(params.get("direct_only"))
            min_age_seconds = _parse_int(params.get("min_age_seconds"))
            items = service.approval_audit_admin.list_approval_requests(
                statuses=statuses,
                directory_path=requested_directory_path,
                direct_only=direct_only,
                session_id=params.get("session_id"),
                vm_id=params.get("vm_id"),
                tool_name=params.get("tool_name"),
                min_age_seconds=min_age_seconds,
            )
        except ValueError as exc:
            return json_error(str(exc))
        auth_cache: dict[str, bool] = {}
        items = [
            item for item in items if _can_read_directory(request, item.directory_path, auth_cache)
        ]
        return JSONResponse(
            {
                "items": [_approval_payload(item) for item in items],
                "health": _approval_queue_health_payload(items),
            }
        )

    async def approval_detail(_: Request) -> JSONResponse:
        raise AssertionError("unreachable")

    async def get_approval_detail(request: Request) -> JSONResponse:
        request_id = request.path_params.get("request_id", "")
        approval_request, load_error = _approval_request_or_error(request_id)
        if load_error is not None:
            return load_error
        assert approval_request is not None
        auth_error = _owner_read_error(request, directory_path=approval_request.directory_path)
        if auth_error is not None:
            return auth_error
        try:
            review = service.approval_audit_admin.get_approval_review(request_id)
        except ValueError as exc:
            return json_error(str(exc), status_code=404)
        return JSONResponse(_approval_review_payload(review))

    async def decide_approval(request: Request) -> JSONResponse:
        auth_error = _privileged_unlocked_error(request)
        if auth_error is not None:
            return auth_error
        request_id = request.path_params.get("request_id", "")
        approval_request, load_error = _approval_request_or_error(request_id)
        if load_error is not None:
            return load_error
        auth_error = _owner_privileged_error(
            request,
            directory_path=approval_request.directory_path if approval_request else None,
        )
        if auth_error is not None:
            return auth_error
        try:
            payload = await request.json()
        except Exception:
            return json_error("Request body must be valid JSON.")
        if not isinstance(payload, dict):
            return json_error("Request body must be a JSON object.")
        approver = _approval_actor_from_request(auth, request, payload)
        reason = str(payload.get("reason") or "").strip()
        approve_raw = payload.get("approve")
        if not approver:
            return json_error("Approver is required.")
        if not reason:
            return json_error("Reason is required.")
        if not isinstance(approve_raw, bool):
            return json_error("approve must be a boolean.")
        try:
            updated = service.approval_audit_admin.decide_approval_request(
                request_id,
                approver=approver,
                approve=approve_raw,
                reason=reason,
            )
        except ValueError as exc:
            status_code = 404 if "Unknown approval request" in str(exc) else 400
            return json_error(str(exc), status_code=status_code)
        return JSONResponse({"request": _approval_payload(updated)})

    async def revoke_approval(request: Request) -> JSONResponse:
        auth_error = _privileged_unlocked_error(request)
        if auth_error is not None:
            return auth_error
        request_id = request.path_params.get("request_id", "")
        approval_request, load_error = _approval_request_or_error(request_id)
        if load_error is not None:
            return load_error
        auth_error = _owner_privileged_error(
            request,
            directory_path=approval_request.directory_path if approval_request else None,
        )
        if auth_error is not None:
            return auth_error
        try:
            payload = await request.json()
        except Exception:
            return json_error("Request body must be valid JSON.")
        if not isinstance(payload, dict):
            return json_error("Request body must be a JSON object.")
        approver = _approval_actor_from_request(auth, request, payload)
        reason = str(payload.get("reason") or "").strip()
        if not approver:
            return json_error("Approver is required.")
        if not reason:
            return json_error("Reason is required.")
        try:
            updated = service.approval_audit_admin.revoke_approval_request(
                request_id,
                approver=approver,
                reason=reason,
            )
        except ValueError as exc:
            status_code = 404 if "Unknown approval request" in str(exc) else 400
            return json_error(str(exc), status_code=status_code)
        return JSONResponse({"request": _approval_payload(updated)})

    async def sessions_catalog(_: Request) -> JSONResponse:
        return JSONResponse(
            {
                "tool_options": merged_tool_options,
                "model_options": _model_options_from_pricing_catalog(service),
                "resume_mode_options": [
                    {"value": "same_context", "label": "same_context"},
                    {"value": "new_context", "label": "new_context"},
                ],
                "renew_extension_presets": ["1h", "6h", "12h", "1d", "7d"],
                "session_policy_fields": _session_policy_field_catalog(),
            }
        )

    async def list_sessions(request: Request) -> JSONResponse:
        params = request.query_params
        requested_directory_path = params.get("directory_path")
        if requested_directory_path is not None:
            auth_error = _owner_read_error(request, directory_path=requested_directory_path)
            if auth_error is not None:
                return auth_error
        try:
            items = service.vm_agent_admin.list_sessions(
                include_expired=_parse_bool(params.get("include_expired")),
                expired_only=_parse_bool(params.get("expired_only")),
                directory_path=requested_directory_path,
                direct_only=_parse_bool(params.get("direct_only")),
            )
        except ValueError as exc:
            return json_error(str(exc))
        auth_cache: dict[str, bool] = {}
        items = [
            item for item in items if _can_read_directory(request, item.directory_path, auth_cache)
        ]
        active_items = [item for item in items if not item.is_expired]
        expired_items = [item for item in items if item.is_expired]
        return JSONResponse(
            {
                "items": [_compact_session_payload(item) for item in items],
                "counts": {
                    "total": len(items),
                    "active": len(active_items),
                    "expired": len(expired_items),
                },
                "status_ids": {
                    "active": [item.session_id for item in active_items],
                    "expired": [item.session_id for item in expired_items],
                },
            }
        )

    def _get_session_summary(session_id: str) -> SessionSummary:
        for item in service.vm_agent_admin.list_sessions(include_expired=True):
            if item.session_id == session_id:
                return item
        raise ValueError(f"Unknown session: {session_id}")

    def _session_summary_or_error(
        session_id: str,
    ) -> tuple[SessionSummary | None, JSONResponse | None]:
        try:
            return _get_session_summary(session_id), None
        except ValueError as exc:
            return None, json_error(str(exc), status_code=404)

    def _authorize_session_mutation(
        request: Request,
        session_id: str,
    ) -> tuple[SessionSummary | None, JSONResponse | None]:
        summary, load_error = _session_summary_or_error(session_id)
        if load_error is not None:
            return None, load_error
        assert summary is not None
        auth_error = _owner_privileged_error(
            request,
            directory_path=summary.directory_path,
        )
        if auth_error is not None:
            return None, auth_error
        return summary, None

    def _wake_request_or_error(
        request_id: str,
    ) -> tuple[WakeScheduledRequest | None, JSONResponse | None]:
        wake_request = service.runtime.wake_store.get_request(request_id)
        if wake_request is None:
            return None, json_error(f"Wake schedule not found: {request_id}", status_code=404)
        return wake_request, None

    def _wake_request_directory_path(wake_request: WakeScheduledRequest) -> str:
        directory_path = str(getattr(wake_request, "directory_path", "") or "").strip()
        if directory_path:
            return directory_path
        metadata = getattr(wake_request.resume_prompt, "metadata", None)
        if isinstance(metadata, dict):
            directory_path = str(metadata.get("directory_path") or "").strip()
            if directory_path:
                return directory_path
        return "root"

    def _authorize_wake_request_mutation(
        request: Request,
        request_id: str,
    ) -> tuple[WakeScheduledRequest | None, JSONResponse | None]:
        wake_request, load_error = _wake_request_or_error(request_id)
        if load_error is not None:
            return None, load_error
        assert wake_request is not None
        try:
            directory_path = _get_session_summary(wake_request.session_id).directory_path
        except ValueError:
            directory_path = _wake_request_directory_path(wake_request)
        auth_error = _owner_privileged_error(
            request,
            directory_path=directory_path,
        )
        if auth_error is not None:
            return None, auth_error
        return wake_request, None

    async def get_session_detail(request: Request) -> JSONResponse:
        session_id = request.path_params.get("session_id", "")
        try:
            summary = _get_session_summary(session_id)
        except ValueError as exc:
            return json_error(str(exc), status_code=404)
        auth_error = _owner_read_error(request, directory_path=summary.directory_path)
        if auth_error is not None:
            return auth_error
        try:
            wake_schedules = service.operations_admin.list_wake_schedules(session_id=session_id)
        except ValueError as exc:
            return json_error(str(exc), status_code=404)
        return JSONResponse(
            {
                "session": _session_payload(service, summary, policy_cache=policy_summary_cache),
                "wake_schedules": [_wake_schedule_payload(item) for item in wake_schedules],
                "git_grants": service.vm_agent_admin.list_managed_git_access(session_id),
                "summary_json": _iso(summary.model_dump(mode="json")),
            }
        )

    async def list_session_git_grants(request: Request) -> JSONResponse:
        session_id = request.path_params.get("session_id", "")
        summary, load_error = _session_summary_or_error(session_id)
        if load_error is not None:
            return load_error
        assert summary is not None
        auth_error = _owner_read_error(request, directory_path=summary.directory_path)
        if auth_error is not None:
            return auth_error
        return JSONResponse({"items": service.vm_agent_admin.list_managed_git_access(session_id)})

    async def put_session_git_grant(request: Request) -> JSONResponse:
        session_id = request.path_params.get("session_id", "")
        _, auth_error = _authorize_session_mutation(request, session_id)
        if auth_error is not None:
            return auth_error
        try:
            payload = await request.json()
        except Exception:
            payload = {}
        if payload is None:
            payload = {}
        if not isinstance(payload, dict):
            return json_error("Request body must be a JSON object.")
        try:
            grant = service.vm_agent_admin.grant_managed_git_access(
                session_id,
                request.path_params.get("managed_repo_id", ""),
                access_mode=str(payload.get("access_mode") or "read-only"),
                lifetime="session",
                vm_user=str(payload.get("vm_user") or "agent"),
            )
        except ValueError as exc:
            return json_error(str(exc), status_code=400)
        except RuntimeError as exc:
            return json_error(str(exc), status_code=409)
        return JSONResponse({"grant": grant})

    async def delete_session_git_grant(request: Request) -> JSONResponse:
        session_id = request.path_params.get("session_id", "")
        _, auth_error = _authorize_session_mutation(request, session_id)
        if auth_error is not None:
            return auth_error
        try:
            result = service.vm_agent_admin.revoke_managed_git_access(
                session_id,
                request.path_params.get("managed_repo_id", ""),
            )
        except ValueError as exc:
            return json_error(str(exc), status_code=400)
        except RuntimeError as exc:
            return json_error(str(exc), status_code=409)
        return JSONResponse(result)

    async def clone_session_managed_repo(request: Request) -> JSONResponse:
        session_id = request.path_params.get("session_id", "")
        _, auth_error = _authorize_session_mutation(request, session_id)
        if auth_error is not None:
            return auth_error
        try:
            payload = await request.json()
        except Exception:
            payload = {}
        if payload is None:
            payload = {}
        if not isinstance(payload, dict):
            return json_error("Request body must be a JSON object.")
        try:
            result = service.vm_agent_admin.clone_managed_repo_into_agent(
                session_id,
                request.path_params.get("managed_repo_id", ""),
                access_mode=str(payload.get("access_mode") or "read-only"),
                lifetime=str(payload.get("lifetime") or "session"),
                vm_user=str(payload.get("vm_user") or "agent"),
            )
        except ValueError as exc:
            return json_error(str(exc), status_code=400)
        except RuntimeError as exc:
            return json_error(str(exc), status_code=409)
        return JSONResponse({"result": result})

    async def renew_session(request: Request) -> JSONResponse:
        session_id = request.path_params.get("session_id", "")
        _, auth_error = _authorize_session_mutation(request, session_id)
        if auth_error is not None:
            return auth_error
        try:
            payload = await request.json()
        except Exception:
            payload = {}
        if payload is None:
            payload = {}
        if not isinstance(payload, dict):
            return json_error("Request body must be a JSON object.")
        try:
            result = service.operations_admin.renew_session(
                session_id, extension=str(payload.get("extension") or "1d")
            )
        except ValueError as exc:
            return json_error(str(exc), status_code=404 if "Unknown" in str(exc) else 400)
        return JSONResponse(result.model_dump(mode="json"))

    async def revoke_session(request: Request) -> JSONResponse:
        session_id = request.path_params.get("session_id", "")
        _, auth_error = _authorize_session_mutation(request, session_id)
        if auth_error is not None:
            return auth_error
        try:
            result = service.operations_admin.revoke_session(session_id)
        except ValueError as exc:
            return json_error(str(exc), status_code=404 if "Unknown" in str(exc) else 400)
        return JSONResponse(result.model_dump(mode="json"))

    async def delete_session(request: Request) -> JSONResponse:
        session_id = request.path_params.get("session_id", "")
        _, auth_error = _authorize_session_mutation(request, session_id)
        if auth_error is not None:
            return auth_error
        try:
            result = service.vm_agent_admin.delete_session(session_id)
        except ValueError as exc:
            return json_error(str(exc), status_code=404 if "Unknown" in str(exc) else 400)
        return JSONResponse(result.model_dump(mode="json"))

    async def cleanup_session(request: Request) -> JSONResponse:
        session_id = request.path_params.get("session_id", "")
        _, auth_error = _authorize_session_mutation(request, session_id)
        if auth_error is not None:
            return auth_error
        try:
            result = service.vm_agent_admin.cleanup_container_session(session_id)
            summary = _get_session_summary(session_id)
        except ValueError as exc:
            return json_error(str(exc), status_code=404 if "Unknown" in str(exc) else 400)
        payload = {
            **result.model_dump(mode="json"),
            "session": _session_payload(service, summary, policy_cache=policy_summary_cache),
        }
        if not result.success:
            payload["error"] = result.message
        status_code = 200 if result.success else 502
        return JSONResponse(payload, status_code=status_code)

    async def update_session_model(request: Request) -> JSONResponse:
        session_id = request.path_params.get("session_id", "")
        _, auth_error = _authorize_session_mutation(request, session_id)
        if auth_error is not None:
            return auth_error
        try:
            payload = await request.json()
        except Exception:
            return json_error("Request body must be valid JSON.")
        if not isinstance(payload, dict):
            return json_error("Request body must be a JSON object.")
        model = str(payload.get("model") or "").strip()
        if not model:
            return json_error("Model is required.")
        try:
            result = service.operations_admin.set_session_model(session_id, model)
            summary = _get_session_summary(session_id)
        except ValueError as exc:
            return json_error(str(exc), status_code=404 if "Unknown" in str(exc) else 400)
        return JSONResponse(
            {
                **result.model_dump(mode="json"),
                "session": _session_payload(service, summary, policy_cache=policy_summary_cache),
            }
        )

    async def update_session_codex_auth_mode(request: Request) -> JSONResponse:
        session_id = request.path_params.get("session_id", "")
        _, auth_error = _authorize_session_mutation(request, session_id)
        if auth_error is not None:
            return auth_error
        try:
            payload = await request.json()
        except Exception:
            return json_error("Request body must be valid JSON.")
        if not isinstance(payload, dict):
            return json_error("Request body must be a JSON object.")
        raw_mode = payload.get("codex_auth_mode")
        mode = None if raw_mode is None else str(raw_mode).strip()
        try:
            result = service.operations_admin.set_session_codex_auth_mode(session_id, mode)
            summary = _get_session_summary(session_id)
        except ValueError as exc:
            return json_error(str(exc), status_code=404 if "Unknown" in str(exc) else 400)
        return JSONResponse(
            {
                **result.model_dump(mode="json"),
                "session": _session_payload(service, summary, policy_cache=policy_summary_cache),
            }
        )

    async def update_session_tools(request: Request) -> JSONResponse:
        session_id = request.path_params.get("session_id", "")
        _, auth_error = _authorize_session_mutation(request, session_id)
        if auth_error is not None:
            return auth_error
        try:
            payload = await request.json()
        except Exception:
            return json_error("Request body must be valid JSON.")
        if not isinstance(payload, dict):
            return json_error("Request body must be a JSON object.")
        raw_tools = payload.get("allowed_tools", [])
        if not isinstance(raw_tools, list):
            return json_error("allowed_tools must be a list.")
        try:
            result = service.operations_admin.set_session_allowed_tools(
                session_id, [str(item) for item in raw_tools]
            )
            summary = _get_session_summary(session_id)
        except ValueError as exc:
            return json_error(str(exc), status_code=404 if "Unknown" in str(exc) else 400)
        return JSONResponse(
            {
                **result.model_dump(mode="json"),
                "session": _session_payload(service, summary, policy_cache=policy_summary_cache),
            }
        )

    async def update_session_policy(request: Request) -> JSONResponse:
        session_id = request.path_params.get("session_id", "")
        _, auth_error = _authorize_session_mutation(request, session_id)
        if auth_error is not None:
            return auth_error
        try:
            payload = await request.json()
        except Exception:
            return json_error("Request body must be valid JSON.")
        if not isinstance(payload, dict):
            return json_error("Request body must be a JSON object.")
        try:
            result = service.operations_admin.update_session_policy(session_id, payload)
            summary = _get_session_summary(session_id)
        except ValueError as exc:
            return json_error(str(exc), status_code=404 if "Unknown" in str(exc) else 400)
        return JSONResponse(
            {
                **result.model_dump(mode="json"),
                "session": _session_payload(service, summary, policy_cache=policy_summary_cache),
            }
        )

    def _mcp_tools_payload(directory_path: str) -> dict[str, Any]:
        raw_result = service.operations_admin.read_directory_domain_config(directory_path, "policy")
        raw_config = (
            raw_result.data.config
            if raw_result.ok and raw_result.data is not None and raw_result.data.config is not None
            else {}
        )
        raw_policies = raw_config.get("mcp_tool_policies")
        if not isinstance(raw_policies, dict):
            raw_policies = {}
        effective_policy = service.runtime.policy_config.resolve_for_directory(directory_path)
        approval_required_tools = {
            tool_name_value(tool) for tool in effective_policy.approval_required_tools
        }
        items: list[dict[str, Any]] = []
        extra_tool_names = (
            set(raw_policies)
            | set(effective_policy.mcp_tool_policies)
            | set(addon_tool_options_by_name)
        )
        extra_tool_names.update(approval_required_tools)
        known_tools = list(ToolName) + sorted(
            tool_name
            for tool_name in extra_tool_names
            if ToolName._value2member_map_.get(tool_name) is None
        )
        for tool in sorted(known_tools, key=tool_name_value):
            tool_value = tool_name_value(tool)
            effective_rule = effective_policy.mcp_tool_policies.get(tool_value)
            effective_settings = _rule_effective_settings(effective_rule)
            option = (
                _tool_option(tool)
                if isinstance(tool, ToolName)
                else addon_tool_options_by_name.get(tool_value)
                or {
                    "value": tool_value,
                    "label": tool_value.replace("_", " "),
                    "description": "Add-on MCP tool.",
                    "group": "Add-ons",
                    "provider": "add-on",
                    "addon_id": None,
                }
            )
            items.append(
                {
                    **option,
                    "local_policy": raw_policies.get(tool_value),
                    "effective_policy": effective_rule.model_dump(mode="json")
                    if effective_rule is not None
                    else None,
                    "effective_settings": effective_settings.model_dump(mode="json"),
                    "approval_required": (
                        mandatory_approval_required_for_tool(tool)
                        or tool_value in approval_required_tools
                        or bool(effective_rule and effective_rule.require_approval)
                    ),
                    "denied": bool(effective_rule and effective_rule.denied),
                }
            )
        return {
            "directory_path": directory_path,
            "revision": _json_revision({"directory_path": directory_path, "policy": raw_config}),
            "mcp_tool_policies": raw_policies,
            "items": items,
        }

    async def mcp_tools_index(request: Request) -> JSONResponse:
        directory_path = str(request.query_params.get("directory_path") or "root").strip() or "root"
        try:
            payload = _mcp_tools_payload(directory_path)
        except ValueError as exc:
            return json_error(str(exc), status_code=400)
        return JSONResponse(payload)

    async def update_mcp_tools(request: Request) -> JSONResponse:
        directory_path = str(request.query_params.get("directory_path") or "root").strip() or "root"
        auth_error = _owner_privileged_error(request, directory_path=directory_path)
        if auth_error is not None:
            return auth_error
        try:
            payload = await request.json()
        except Exception:
            return json_error("Request body must be valid JSON.")
        if not isinstance(payload, dict):
            return json_error("Request body must be a JSON object.")
        expected_revision = str(payload.get("expected_revision") or "").strip()
        if not expected_revision:
            return json_error("expected_revision is required.", status_code=428)
        try:
            current = _mcp_tools_payload(directory_path)
        except ValueError as exc:
            return json_error(str(exc), status_code=400)
        if current["revision"] != expected_revision:
            return json_error(
                "MCP tool policy changed before save. Reload before saving again.",
                status_code=409,
            )
        try:
            normalized_policies = normalize_mcp_tool_policies(
                payload.get("mcp_tool_policies"),
                field_name="mcp_tool_policies",
            )
            validated_policies = {
                tool_name: McpToolPolicyRule.model_validate(policy).model_dump(mode="json")
                for tool_name, policy in normalized_policies.items()
            }
        except ValueError as exc:
            return json_error(str(exc), status_code=400)
        raw_result = service.operations_admin.read_directory_domain_config(directory_path, "policy")
        raw_config = (
            dict(raw_result.data.config)
            if raw_result.ok and raw_result.data is not None and raw_result.data.config is not None
            else {}
        )
        raw_config["mcp_tool_policies"] = validated_policies
        result = service.operations_admin.write_directory_domain_config(
            directory_path, "policy", raw_config
        )
        if not result.ok:
            message = (
                result.errors[0].message if result.errors else "Unable to save MCP tool policy."
            )
            return json_error(message, status_code=400)
        return JSONResponse(_mcp_tools_payload(directory_path))

    async def list_wake_schedules(request: Request) -> JSONResponse:
        session_id = request.path_params.get("session_id", "")
        try:
            items = service.operations_admin.list_wake_schedules(
                session_id=session_id,
                include_disabled=_parse_bool(
                    request.query_params.get("include_disabled"), default=True
                ),
            )
        except ValueError as exc:
            return json_error(str(exc), status_code=400)
        return JSONResponse({"items": [_wake_schedule_payload(item) for item in items]})

    async def list_scheduled_wakes(request: Request) -> JSONResponse:
        params = request.query_params
        limit = _parse_int(params.get("limit"))
        try:
            items = service.operations_admin.list_scheduled_wake_ledger(
                statuses=_parse_wake_statuses(params.get("statuses")),
                session_id=params.get("session_id"),
                limit=100 if limit is None else limit,
                include_completed=_parse_bool(params.get("include_completed"), default=True),
            )
        except ValueError as exc:
            return json_error(str(exc), status_code=400)
        return JSONResponse({"items": [_wake_ledger_payload(item) for item in items]})

    async def create_wake_schedule(request: Request) -> JSONResponse:
        session_id = request.path_params.get("session_id", "")
        _, auth_error = _authorize_session_mutation(request, session_id)
        if auth_error is not None:
            return auth_error
        try:
            payload = await request.json()
        except Exception:
            return json_error("Request body must be valid JSON.")
        if not isinstance(payload, dict):
            return json_error("Request body must be a JSON object.")
        try:
            created = service.operations_admin.create_wake_schedule(
                session_id=session_id,
                prompt=str(payload.get("prompt") or ""),
                resume_at=_parse_datetime(payload.get("resume_at")),
                cron=str(payload.get("cron")).strip() if payload.get("cron") is not None else None,
                resume_mode=str(payload.get("resume_mode") or "same_context"),
                enabled=bool(payload.get("enabled", True)),
                source="admin_web",
            )
        except ValueError as exc:
            status_code = 404 if "Unknown" in str(exc) else 400
            return json_error(str(exc), status_code=status_code)
        return JSONResponse({"item": _wake_schedule_payload(created.model_dump(mode="json"))})

    async def update_wake_schedule(request: Request) -> JSONResponse:
        request_id = request.path_params.get("request_id", "")
        _, auth_error = _authorize_wake_request_mutation(request, request_id)
        if auth_error is not None:
            return auth_error
        try:
            payload = await request.json()
        except Exception:
            return json_error("Request body must be valid JSON.")
        if not isinstance(payload, dict):
            return json_error("Request body must be a JSON object.")
        try:
            updated = service.operations_admin.edit_wake_schedule(
                request_id,
                resume_at=_parse_datetime(payload.get("resume_at"))
                if "resume_at" in payload
                else None,
                cron=str(payload.get("cron")).strip()
                if "cron" in payload and payload.get("cron") is not None
                else payload.get("cron"),
                prompt=str(payload.get("prompt")).strip()
                if "prompt" in payload and payload.get("prompt") is not None
                else None,
                resume_mode=str(payload.get("resume_mode"))
                if "resume_mode" in payload and payload.get("resume_mode") is not None
                else None,
                enabled=bool(payload.get("enabled")) if "enabled" in payload else None,
            )
        except ValueError as exc:
            status_code = 404 if "not found" in str(exc).lower() else 400
            return json_error(str(exc), status_code=status_code)
        return JSONResponse({"item": _wake_schedule_payload(updated.model_dump(mode="json"))})

    async def retry_scheduled_wake(request: Request) -> JSONResponse:
        request_id = request.path_params.get("request_id", "")
        _, auth_error = _authorize_wake_request_mutation(request, request_id)
        if auth_error is not None:
            return auth_error
        try:
            payload = await request.json()
        except Exception:
            payload = {}
        if payload is None:
            payload = {}
        if not isinstance(payload, dict):
            return json_error("Request body must be a JSON object.")
        try:
            resume_at = (
                _parse_datetime(payload.get("resume_at")) if "resume_at" in payload else None
            )
            if "run_now" in payload:
                run_now_raw = payload.get("run_now")
                if not isinstance(run_now_raw, bool):
                    return json_error("run_now must be a boolean.")
                run_now = bool(run_now_raw)
                if resume_at is not None and run_now:
                    return json_error("Provide either resume_at or run_now=true, not both.")
            else:
                run_now = resume_at is None
            if resume_at is None and not run_now:
                return json_error("Either resume_at or run_now=true is required.")
            updated = service.operations_admin.retry_scheduled_wake(
                request_id,
                resume_at=resume_at,
                run_now=run_now,
            )
        except ValueError as exc:
            message = str(exc)
            if "not retryable" in message.lower():
                return json_error(message, status_code=409)
            status_code = 404 if "not found" in message.lower() else 400
            return json_error(message, status_code=status_code)
        return JSONResponse({"item": _wake_ledger_payload(updated)})

    async def remove_wake_schedule(request: Request) -> JSONResponse:
        request_id = request.path_params.get("request_id", "")
        _, auth_error = _authorize_wake_request_mutation(request, request_id)
        if auth_error is not None:
            return auth_error
        try:
            deleted = service.operations_admin.delete_wake_schedule(request_id)
        except ValueError as exc:
            return json_error(str(exc), status_code=404 if "not found" in str(exc).lower() else 400)
        if not deleted:
            return json_error(f"Wake schedule not found: {request_id}", status_code=404)
        return JSONResponse({"ok": True, "request_id": request_id})

    return [
        Route(
            "/api/admin/approvals",
            cache.wrap("admin.approvals.list", approvals_index) if cache else approvals_index,
            methods=["GET"],
        ),
        Route("/api/admin/approvals/{request_id:str}", get_approval_detail, methods=["GET"]),
        Route("/api/admin/approvals/{request_id:str}/decision", decide_approval, methods=["POST"]),
        Route("/api/admin/approvals/{request_id:str}/revoke", revoke_approval, methods=["POST"]),
        Route("/api/admin/mcp-tools", mcp_tools_index, methods=["GET"]),
        Route("/api/admin/mcp-tools", update_mcp_tools, methods=["PUT"]),
        Route("/api/admin/sessions/catalog", sessions_catalog, methods=["GET"]),
        Route(
            "/api/admin/sessions",
            cache.wrap("admin.sessions.list", list_sessions) if cache else list_sessions,
            methods=["GET"],
        ),
        Route("/api/admin/sessions/{session_id:str}", get_session_detail, methods=["GET"]),
        Route(
            "/api/admin/sessions/{session_id:str}/git-grants",
            list_session_git_grants,
            methods=["GET"],
        ),
        Route(
            "/api/admin/sessions/{session_id:str}/git-grants/{managed_repo_id:str}",
            put_session_git_grant,
            methods=["PUT"],
        ),
        Route(
            "/api/admin/sessions/{session_id:str}/git-grants/{managed_repo_id:str}/clone",
            clone_session_managed_repo,
            methods=["POST"],
        ),
        Route(
            "/api/admin/sessions/{session_id:str}/git-grants/{managed_repo_id:str}",
            delete_session_git_grant,
            methods=["DELETE"],
        ),
        Route("/api/admin/sessions/{session_id:str}", update_session_policy, methods=["PATCH"]),
        Route("/api/admin/sessions/{session_id:str}/renew", renew_session, methods=["POST"]),
        Route("/api/admin/sessions/{session_id:str}/revoke", revoke_session, methods=["POST"]),
        Route("/api/admin/sessions/{session_id:str}/cleanup", cleanup_session, methods=["POST"]),
        Route("/api/admin/sessions/{session_id:str}", delete_session, methods=["DELETE"]),
        Route("/api/admin/sessions/{session_id:str}/model", update_session_model, methods=["PUT"]),
        Route(
            "/api/admin/sessions/{session_id:str}/codex-auth-mode",
            update_session_codex_auth_mode,
            methods=["PUT"],
        ),
        Route(
            "/api/admin/sessions/{session_id:str}/allowed-tools",
            update_session_tools,
            methods=["PUT"],
        ),
        Route(
            "/api/admin/sessions/{session_id:str}/wake-schedules",
            list_wake_schedules,
            methods=["GET"],
        ),
        Route(
            "/api/admin/scheduled-wakes",
            list_scheduled_wakes,
            methods=["GET"],
        ),
        Route(
            "/api/admin/sessions/{session_id:str}/wake-schedules",
            create_wake_schedule,
            methods=["POST"],
        ),
        Route(
            "/api/admin/sessions/wake-schedules/{request_id:str}",
            update_wake_schedule,
            methods=["PATCH"],
        ),
        Route(
            "/api/admin/scheduled-wakes/{request_id:str}/retry",
            retry_scheduled_wake,
            methods=["POST"],
        ),
        Route(
            "/api/admin/sessions/wake-schedules/{request_id:str}",
            remove_wake_schedule,
            methods=["DELETE"],
        ),
    ]


__all__ = ["session_approval_routes"]
