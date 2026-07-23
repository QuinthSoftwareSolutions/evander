from __future__ import annotations

from enum import Enum
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

from evander.models import ApprovalGrantSettings, McpToolName, SessionManifest, ToolName
from evander.models import digest_payload
from evander.models import tool_name_value
from evander.policy import ToolVisibility


class ToolVisibilityState(str, Enum):
    VISIBLE = "visible"
    HIDDEN_BY_POLICY = "hidden_by_policy"
    UNAVAILABLE_TO_SESSION = "unavailable_to_session"
    UNKNOWN = "unknown"


class ToolVisibilityExecutionState(str, Enum):
    DENIED_AT_EXECUTION = "denied_at_execution"


class ToolVisibilityBindingState(str, Enum):
    BOUND = "bound"
    MISMATCH = "mismatch"
    UNKNOWN = "unknown"


class ToolVisibilityManifestState(str, Enum):
    CURRENT = "current"
    STALE_MANIFEST_POSSIBLE = "stale_manifest_possible"
    UNKNOWN = "unknown"


class ToolVisibilityNoticePayload(BaseModel):
    kind: str
    message: str
    tool_name: str | None = None


class ToolVisibilitySessionContextPayload(BaseModel):
    session_id: str
    directory_path: str
    vm_id: str | None = None
    vm_provider: str | None = None
    expires_at: datetime
    is_expired: bool = False
    capability_profile: str | None = None
    policy_metadata: dict[str, Any] = Field(default_factory=dict)


class ToolVisibilityQueryContextPayload(BaseModel):
    workflow_run_id: str | None = None
    mcp_tool_approval_lease_id: str | None = None


class ToolVisibilityBindingContextPayload(BaseModel):
    state: ToolVisibilityBindingState = ToolVisibilityBindingState.UNKNOWN
    manifest_state: ToolVisibilityManifestState = ToolVisibilityManifestState.UNKNOWN
    transport_session_id: str | None = None
    bound_session_id: str | None = None
    last_tools_list_at: datetime | None = None
    last_visible_tool_fingerprint: str | None = None
    message: str = ""


class ToolVisibilityToolPayload(BaseModel):
    tool_name: str
    visibility_state: ToolVisibilityState
    available: bool
    visible: bool
    execution_state: ToolVisibilityExecutionState | None = None
    source: str | None = None
    reason: str | None = None
    requires_approval: bool = False
    approval_locked: bool = False
    approval_settings: ApprovalGrantSettings | None = None
    provider: str | None = None
    category: str = "Other"
    label: str | None = None
    last_denied_at: datetime | None = None
    decision: str | None = None
    error_summary: str | None = None
    request_id: str | None = None
    approval_request_id: str | None = None
    workflow_run_id: str | None = None
    mcp_tool_approval_lease_id: str | None = None
    related_links: list[dict[str, Any]] = Field(default_factory=list)


class ToolVisibilityCountsPayload(BaseModel):
    total: int = 0
    visible: int = 0
    hidden_by_policy: int = 0
    unavailable_to_session: int = 0
    denied_at_execution: int = 0
    unknown: int = 0
    requires_approval: int = 0


class ToolVisibilityPayload(BaseModel):
    session_context: ToolVisibilitySessionContextPayload
    query_context: ToolVisibilityQueryContextPayload = Field(
        default_factory=ToolVisibilityQueryContextPayload
    )
    binding_context: ToolVisibilityBindingContextPayload = Field(
        default_factory=ToolVisibilityBindingContextPayload
    )
    tools: list[ToolVisibilityToolPayload] = Field(default_factory=list)
    counts: ToolVisibilityCountsPayload = Field(default_factory=ToolVisibilityCountsPayload)
    notices: list[ToolVisibilityNoticePayload] = Field(default_factory=list)


_ISSUE_TOOLS = {
    ToolName.CREATE_LOCAL_ISSUE,
    ToolName.LIST_ISSUES,
    ToolName.GET_ISSUE,
    ToolName.SET_ISSUE_PLAN,
    ToolName.APPROVE_ISSUE,
    ToolName.COMMIT_ISSUE,
    ToolName.REJECT_ISSUE,
    ToolName.CLAIM_NEXT_ISSUE,
    ToolName.COMPLETE_ISSUE,
}

_AZURE_DEVOPS_TOOLS = {
    ToolName.CREATE_ADO_BACKLOG_ITEM,
    ToolName.QUERY_ADO_BACKLOG_ITEMS,
    ToolName.LIST_ADO_CURRENT_ITERATION_BACKLOG_ITEMS,
    ToolName.GET_ADO_DAILY_STATUS_REPORT_DATA,
    ToolName.GET_ADO_BACKLOG_ITEM,
    ToolName.POST_ADO_BACKLOG_COMMENT,
    ToolName.DELETE_ADO_BACKLOG_ITEM,
    ToolName.UPDATE_ADO_BACKLOG_TEXT,
    ToolName.UPDATE_ADO_BACKLOG_TITLE,
    ToolName.UPDATE_ADO_BACKLOG_TAGS,
    ToolName.RESOLVE_ADO_IDENTITY,
    ToolName.QUERY_ADO_WORK_ITEM_ACTIVITY,
}

_PULL_REQUEST_TOOLS = {
    ToolName.LIST_PULL_REQUESTS,
    ToolName.GET_PULL_REQUEST,
    ToolName.LIST_ADO_PR_COMMENT_ACTIVITY,
    ToolName.LIST_ADO_PR_COMMIT_ACTIVITY,
    ToolName.REQUEST_PR_REVIEW,
    ToolName.POST_PR_COMMENT,
    ToolName.POST_PR_INLINE_COMMENT,
    ToolName.SUBMIT_PR_REVIEW,
    ToolName.POST_PR_FEEDBACK_REPLY,
}

_APPROVAL_TOOLS = {
    ToolName.APPROVAL_SUBMIT,
    ToolName.APPROVAL_CHECK,
}

_DOCUMENTATION_TOOLS = {
    ToolName.DOCUMENTATION_LIST,
    ToolName.DOCUMENTATION_SEARCH,
    ToolName.DOCUMENTATION_READ,
}

_AGENT_CONTROL_TOOLS = {
    ToolName.GET_AGENT_STATUS,
    ToolName.GET_AGENT_LOGS,
    ToolName.PROMPT_AGENT,
    ToolName.LIST_PENDING_AGENT_PROMPTS,
    ToolName.RESPOND_TO_AGENT,
    ToolName.CONSULT_CODEBASE,
    ToolName.GET_CODEBASE_CONSULTATION,
    ToolName.CANCEL_CODEBASE_CONSULTATION,
    ToolName.SUBMIT_CODEBASE_CONSULTATION_RESULT,
    ToolName.SCHEDULE_RESUME,
    ToolName.RUN_SCRIPT,
    ToolName.COPILOT_HOST_AUTH,
    ToolName.GET_WORKFLOW_SNAPSHOT,
    ToolName.ASSISTANT_READ_STATUS,
    ToolName.ASSISTANT_LIST_AGENTS,
    ToolName.ASSISTANT_INSPECT_WORKFLOWS,
    ToolName.ASSISTANT_READ_CONFIG,
    ToolName.ASSISTANT_UPSERT_CONFIG,
    ToolName.ASSISTANT_CREATE_WORKFLOW,
    ToolName.ASSISTANT_RUN_SAFE_OPERATION,
    ToolName.ASSISTANT_POST_CHAT_REPLY,
}

_DISCORD_TOOLS = {
    ToolName.DISCORD_GET_COMMANDS,
    ToolName.DISCORD_POST_STATUS,
    ToolName.HAS_PENDING_DISCORD_MESSAGES,
    ToolName.LIST_UNREAD_DISCORD_MESSAGES,
    ToolName.LIST_ALL_DISCORD_MESSAGES,
    ToolName.DISCORD_POST_FILE,
}

_MESSAGING_TOOLS = {
    ToolName.ADD_MESSAGE_REACTION,
}

_VOICE_TOOLS = {
    ToolName.LIST_CONTACTS,
    ToolName.MAKE_PHONE_CALL,
    ToolName.GET_PHONE_CALL_RESULT,
    ToolName.SUBMIT_VOICE_QUERY_RESPONSE,
}

_SQL_SERVER_TOOLS = {
    ToolName.MSSQL_PROVISION_DATABASE,
    ToolName.MSSQL_LIST_DATABASES,
    ToolName.MSSQL_DROP_DATABASE,
}

_FILES_TOOLS = {
    ToolName.COMMUNICATION_LIST_ATTACHMENTS,
    ToolName.COMMUNICATION_DOWNLOAD_ATTACHMENT,
    ToolName.LIST_INLINE_MEDIA,
    ToolName.DOWNLOAD_INLINE_MEDIA,
    ToolName.STAGE_ARTIFACT,
    ToolName.COMMUNICATION_COPY_ARTIFACT_TO_WORKSPACE,
    ToolName.COMMUNICATION_STAGE_WORKSPACE_FILE,
    ToolName.TEAMS_POST_FILE,
    ToolName.EMAIL_CREATE_DRAFT_WITH_ATTACHMENTS,
    ToolName.EMAIL_SEND_WITH_ATTACHMENTS,
    ToolName.EMAIL_SEND_APPROVED_WITH_ATTACHMENTS,
    ToolName.HOST_DIRECTORY_LIST,
    ToolName.HOST_DIRECTORY_IMPORT,
    ToolName.HOST_DIRECTORY_DELETE,
}

_TEAMS_TOOLS = {
    ToolName.TEAMS_GET_MESSAGES,
    ToolName.TEAMS_POST_STATUS,
    ToolName.TEAMS_MEETING_GET_EVENTS,
    ToolName.TEAMS_MEETING_SPEAK,
    ToolName.TEAMS_MEETING_STOP,
}

_EMAIL_TOOLS = {
    ToolName.EMAIL_LIST_UNREAD,
    ToolName.EMAIL_LIST_MESSAGES,
    ToolName.EMAIL_GET_MESSAGE,
    ToolName.EMAIL_CREATE_DRAFT,
    ToolName.EMAIL_SEND,
    ToolName.EMAIL_SEND_APPROVED,
}

_FEEDBACK_TOOLS = {
    ToolName.HAS_PENDING_FEEDBACK,
    ToolName.LIST_UNREAD_FEEDBACK,
    ToolName.LIST_ALL_FEEDBACK,
}

_CONNECTOR_TOOLS = {ToolName.LIST_CONNECTORS, ToolName.CALL_CONNECTOR}

_HIDDEN_BY_POLICY_SOURCES = {
    "directory.mcp_tool_policies",
    "session.allowed_tools",
    "session.allowed_tools.respond_to_agent",
    "session.metadata.capability_profile",
    "session.metadata.assistant_mutation_policy",
}

_UNAVAILABLE_TO_SESSION_SOURCES = {
    "session.vm_binding",
    "session.metadata.pr_feedback_streams",
}


def _tool_category(tool_name: McpToolName) -> str:
    builtin = ToolName._value2member_map_.get(tool_name_value(tool_name))
    if builtin is None:
        return "Add-ons"
    tool_name = builtin
    if tool_name in _ISSUE_TOOLS:
        return "Issue intake"
    if tool_name in _AZURE_DEVOPS_TOOLS:
        return "Azure DevOps"
    if tool_name in _PULL_REQUEST_TOOLS:
        return "Pull requests"
    if tool_name in _APPROVAL_TOOLS:
        return "Approvals"
    if tool_name in _DOCUMENTATION_TOOLS:
        return "Documentation"
    if tool_name in _AGENT_CONTROL_TOOLS:
        return "Agent control"
    if tool_name in _MESSAGING_TOOLS:
        return "Messaging"
    if tool_name in _VOICE_TOOLS:
        return "Voice calling"
    if tool_name in _SQL_SERVER_TOOLS:
        return "SQL Server"
    if tool_name in _FILES_TOOLS:
        return "Files"
    if tool_name in _DISCORD_TOOLS:
        return "Discord"
    if tool_name in _TEAMS_TOOLS:
        return "Teams"
    if tool_name in _EMAIL_TOOLS:
        return "Email"
    if tool_name in _FEEDBACK_TOOLS:
        return "Feedback"
    if tool_name in _CONNECTOR_TOOLS:
        return "Connectors"
    return "Other"


def _tool_provider(tool_name: McpToolName, category: str) -> str | None:
    builtin = ToolName._value2member_map_.get(tool_name_value(tool_name))
    if builtin is None:
        return "add-on" if category == "Add-ons" else None
    tool_name = builtin
    if tool_name in _AZURE_DEVOPS_TOOLS:
        return "azure_devops"
    if tool_name in _PULL_REQUEST_TOOLS:
        return "pull_requests"
    if tool_name in _APPROVAL_TOOLS:
        return "approvals"
    if tool_name in _DOCUMENTATION_TOOLS:
        return "documentation"
    if tool_name in _AGENT_CONTROL_TOOLS:
        return "agent"
    if tool_name in _DISCORD_TOOLS:
        return "discord"
    if tool_name in _MESSAGING_TOOLS:
        return "messaging"
    if tool_name in _VOICE_TOOLS:
        return "voice_calling"
    if tool_name in _SQL_SERVER_TOOLS:
        return "sql_server"
    if tool_name in _FILES_TOOLS:
        return "files"
    if tool_name in _TEAMS_TOOLS:
        return "teams"
    if tool_name in _EMAIL_TOOLS:
        return "email"
    if tool_name in _FEEDBACK_TOOLS:
        return "feedback"
    if tool_name in _CONNECTOR_TOOLS:
        return "connectors"
    if tool_name in _ISSUE_TOOLS:
        return "issue"
    return category.lower().replace(" ", "_") if category != "Other" else None


def _tool_label(tool_name: McpToolName) -> str:
    return tool_name_value(tool_name).replace("_", " ")


def _policy_metadata(session: SessionManifest) -> dict[str, Any]:
    metadata = session.metadata if isinstance(session.metadata, dict) else {}
    policy_metadata: dict[str, Any] = {}
    for key in ("assistant_mutation_policy", "pr_feedback_streams"):
        if key in metadata:
            policy_metadata[key] = metadata.get(key)
    return policy_metadata


def _session_context_payload(session: SessionManifest) -> ToolVisibilitySessionContextPayload:
    metadata = session.metadata if isinstance(session.metadata, dict) else {}
    capability_profile = str(metadata.get("capability_profile") or "").strip() or None
    vm_id = str(session.vm_id or "").strip() or None
    vm_provider = str(session.vm_provider or "").strip() or None
    expires_at = session.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    return ToolVisibilitySessionContextPayload(
        session_id=session.session_id,
        directory_path=session.directory_path,
        vm_id=vm_id,
        vm_provider=vm_provider,
        expires_at=expires_at,
        is_expired=expires_at <= datetime.now(UTC),
        capability_profile=capability_profile,
        policy_metadata=_policy_metadata(session),
    )


def _visibility_state_for_tool(
    visibility: ToolVisibility, *, policy_error: Exception | None = None
) -> ToolVisibilityState:
    if policy_error is not None:
        return ToolVisibilityState.UNKNOWN
    if visibility.visible:
        return ToolVisibilityState.VISIBLE
    if visibility.source in _UNAVAILABLE_TO_SESSION_SOURCES:
        return ToolVisibilityState.UNAVAILABLE_TO_SESSION
    if visibility.source in _HIDDEN_BY_POLICY_SOURCES:
        return ToolVisibilityState.HIDDEN_BY_POLICY
    return ToolVisibilityState.HIDDEN_BY_POLICY


def _tool_payload_for_visibility(
    visibility: ToolVisibility,
    *,
    policy_error: Exception | None = None,
) -> ToolVisibilityToolPayload:
    tool_name = visibility.tool
    category = _tool_category(tool_name)
    visibility_state = _visibility_state_for_tool(visibility, policy_error=policy_error)
    return ToolVisibilityToolPayload(
        tool_name=tool_name_value(tool_name),
        visibility_state=visibility_state,
        available=bool(visibility.available) and policy_error is None,
        visible=bool(visibility.visible) and policy_error is None,
        source=visibility.source if policy_error is None else "unknown",
        reason=str(policy_error) if policy_error is not None else visibility.reason,
        requires_approval=bool(visibility.requires_approval) and policy_error is None,
        approval_locked=bool(visibility.approval_locked) and policy_error is None,
        approval_settings=(
            visibility.approval_settings.model_dump(mode="json")
            if visibility.approval_settings is not None and policy_error is None
            else None
        ),
        provider=_tool_provider(tool_name, category),
        category=category,
        label=_tool_label(tool_name),
    )


def _unknown_tool_payload(tool_name: McpToolName, reason: str) -> ToolVisibilityToolPayload:
    category = _tool_category(tool_name)
    return ToolVisibilityToolPayload(
        tool_name=tool_name_value(tool_name),
        visibility_state=ToolVisibilityState.UNKNOWN,
        available=False,
        visible=False,
        source="unknown",
        reason=reason,
        requires_approval=False,
        approval_locked=False,
        approval_settings=None,
        provider=_tool_provider(tool_name, category),
        category=category,
        label=_tool_label(tool_name),
    )


def _tool_visibility_notices(session: SessionManifest) -> list[ToolVisibilityNoticePayload]:
    metadata = session.metadata if isinstance(session.metadata, dict) else {}
    notices: list[ToolVisibilityNoticePayload] = []
    capability_profile = str(metadata.get("capability_profile") or "").strip()
    if not capability_profile:
        notices.append(
            ToolVisibilityNoticePayload(
                kind="missing_capability_profile",
                message=(
                    f"Session {session.session_id} does not define metadata.capability_profile; "
                    "assistant tools are hidden by policy."
                ),
            )
        )
    return notices


def _stale_client_manifest_notice() -> ToolVisibilityNoticePayload:
    return ToolVisibilityNoticePayload(
        kind="stale_client_manifest",
        message=(
            "MCP clients may cache a client-side tool manifest until they reconnect or "
            "refresh, so a server-side policy change may not be visible immediately."
        ),
    )


def _datetime_sort_value(value: datetime | None) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return datetime.min.replace(tzinfo=UTC)


def _binding_sort_key(binding: Any) -> datetime:
    candidates = [
        _datetime_sort_value(getattr(binding, "last_tools_list_at", None)),
        _datetime_sort_value(getattr(binding, "last_seen_at", None)),
        _datetime_sort_value(getattr(binding, "updated_at", None)),
        _datetime_sort_value(getattr(binding, "created_at", None)),
    ]
    return max(candidates)


def _binding_matches_session(binding: Any, session_id: str) -> bool:
    bound_session_id = str(getattr(binding, "bound_session_id", "") or "").strip()
    if bound_session_id == session_id:
        return True
    conflict = getattr(binding, "latest_conflict", None)
    if conflict is None:
        return False
    declared_session_id = str(getattr(conflict, "declared_session_id", "") or "").strip()
    return declared_session_id == session_id


def _latest_binding_for_session(service: Any, session_id: str) -> Any | None:
    bindings = getattr(getattr(service, "runtime", None), "mcp_transport_bindings", None)
    if bindings is None:
        return None
    try:
        all_bindings = list(bindings.read_all().values())
    except Exception:
        return None
    session_bindings = [
        binding for binding in all_bindings if _binding_matches_session(binding, session_id)
    ]
    if not session_bindings:
        return None
    return max(session_bindings, key=_binding_sort_key)


def _resolve_binding_for_session(
    service: Any,
    session: SessionManifest,
    *,
    mcp_transport_session_id: str | None = None,
) -> tuple[Any | None, ToolVisibilityBindingState]:
    bindings = getattr(getattr(service, "runtime", None), "mcp_transport_bindings", None)
    if bindings is None:
        return None, ToolVisibilityBindingState.UNKNOWN

    normalized_transport_session_id = str(mcp_transport_session_id or "").strip() or None
    binding: Any | None = None
    if normalized_transport_session_id:
        try:
            binding = bindings.get_binding(normalized_transport_session_id)
        except Exception:
            binding = None
        if binding is not None:
            bound_session_id = str(getattr(binding, "bound_session_id", "") or "").strip()
            conflict = getattr(binding, "latest_conflict", None)
            conflict_session_id = (
                str(getattr(conflict, "declared_session_id", "") or "").strip()
                if conflict is not None
                else None
            )
            if bound_session_id == session.session_id:
                return binding, ToolVisibilityBindingState.BOUND
            if conflict_session_id == session.session_id:
                return binding, ToolVisibilityBindingState.MISMATCH
            if bound_session_id or conflict_session_id:
                return binding, ToolVisibilityBindingState.MISMATCH
            return binding, ToolVisibilityBindingState.UNKNOWN

    binding = _latest_binding_for_session(service, session.session_id)
    if binding is None:
        return None, ToolVisibilityBindingState.UNKNOWN
    bound_session_id = str(getattr(binding, "bound_session_id", "") or "").strip()
    conflict = getattr(binding, "latest_conflict", None)
    conflict_session_id = (
        str(getattr(conflict, "declared_session_id", "") or "").strip()
        if conflict is not None
        else None
    )
    if bound_session_id == session.session_id:
        return binding, ToolVisibilityBindingState.BOUND
    if conflict_session_id == session.session_id:
        return binding, ToolVisibilityBindingState.MISMATCH
    if bound_session_id or conflict_session_id:
        return binding, ToolVisibilityBindingState.MISMATCH
    return binding, ToolVisibilityBindingState.UNKNOWN


def _tool_names_fingerprint(tool_names: list[str]) -> str:
    return digest_payload({"visible_tool_names": tool_names})


def _binding_context_message(
    *,
    session: SessionManifest,
    binding: Any | None,
    state: ToolVisibilityBindingState,
    manifest_state: ToolVisibilityManifestState,
    mcp_transport_session_id: str | None = None,
    policy_error: Exception | None = None,
) -> str:
    normalized_transport_session_id = str(mcp_transport_session_id or "").strip() or None
    if binding is None or state is ToolVisibilityBindingState.UNKNOWN:
        if policy_error is not None:
            return (
                "Transport binding telemetry is unavailable and current visibility could not "
                f"be read: {policy_error}"
            )
        return f"No MCP transport binding telemetry is available for session {session.session_id}."

    bound_session_id = str(getattr(binding, "bound_session_id", "") or "").strip() or None
    transport_session_id = (
        str(getattr(binding, "transport_session_id", "") or "").strip()
        or normalized_transport_session_id
    )
    if state is ToolVisibilityBindingState.MISMATCH:
        if bound_session_id:
            base = (
                f"Transport {transport_session_id} is bound to session {bound_session_id}, "
                f"not {session.session_id}."
            )
        else:
            base = (
                f"Transport {transport_session_id} has a conflicting declaration for "
                f"session {session.session_id}."
            )
    else:
        base = f"Transport {transport_session_id} is bound to session {session.session_id}."

    if manifest_state is ToolVisibilityManifestState.STALE_MANIFEST_POSSIBLE:
        base += " The latest served tool manifest may be stale after policy changes."
    elif manifest_state is ToolVisibilityManifestState.CURRENT:
        base += " The latest served tool manifest matches current visibility."
    elif policy_error is not None:
        base += f" Current visibility could not be compared: {policy_error}"
    return base


def _binding_context_payload(
    service: Any,
    session: SessionManifest,
    *,
    current_visible_tool_names: list[str] | None,
    mcp_transport_session_id: str | None = None,
    policy_error: Exception | None = None,
) -> ToolVisibilityBindingContextPayload:
    binding, state = _resolve_binding_for_session(
        service,
        session,
        mcp_transport_session_id=mcp_transport_session_id,
    )
    manifest_state = ToolVisibilityManifestState.UNKNOWN
    if binding is not None and policy_error is None and current_visible_tool_names is not None:
        last_fingerprint = (
            str(getattr(binding, "last_visible_tool_fingerprint", "") or "").strip() or None
        )
        if last_fingerprint is not None:
            manifest_state = (
                ToolVisibilityManifestState.CURRENT
                if last_fingerprint == _tool_names_fingerprint(current_visible_tool_names)
                else ToolVisibilityManifestState.STALE_MANIFEST_POSSIBLE
            )
    return ToolVisibilityBindingContextPayload(
        state=state,
        manifest_state=manifest_state,
        transport_session_id=(
            str(getattr(binding, "transport_session_id", "") or "").strip()
            if binding is not None
            else str(mcp_transport_session_id or "").strip() or None
        ),
        bound_session_id=(
            str(getattr(binding, "bound_session_id", "") or "").strip() or None
            if binding is not None
            else None
        ),
        last_tools_list_at=(
            getattr(binding, "last_tools_list_at", None) if binding is not None else None
        ),
        last_visible_tool_fingerprint=(
            str(getattr(binding, "last_visible_tool_fingerprint", "") or "").strip() or None
            if binding is not None
            else None
        ),
        message=_binding_context_message(
            session=session,
            binding=binding,
            state=state,
            manifest_state=manifest_state,
            mcp_transport_session_id=mcp_transport_session_id,
            policy_error=policy_error,
        ),
    )


def _execution_denial_payload(
    entry: Any,
) -> dict[str, Any]:
    return {
        "execution_state": ToolVisibilityExecutionState.DENIED_AT_EXECUTION,
        "last_denied_at": getattr(entry, "created_at", None),
        "decision": getattr(entry, "decision", None),
        "error_summary": getattr(entry, "error_summary", None),
        "request_id": getattr(entry, "request_id", None),
        "approval_request_id": getattr(entry, "approval_request_id", None),
        "workflow_run_id": getattr(entry, "workflow_run_id", None),
        "mcp_tool_approval_lease_id": getattr(entry, "mcp_tool_approval_lease_id", None),
        "related_links": [
            link.model_dump(mode="json")
            for link in getattr(entry, "related_links", [])
            if link is not None
        ],
    }


def _apply_execution_denial_summary(
    tool: ToolVisibilityToolPayload,
    entry: Any,
) -> None:
    summary = _execution_denial_payload(entry)
    tool.execution_state = summary["execution_state"]
    tool.last_denied_at = summary["last_denied_at"]
    tool.decision = summary["decision"]
    tool.error_summary = summary["error_summary"]
    tool.request_id = summary["request_id"]
    tool.approval_request_id = summary["approval_request_id"]
    tool.workflow_run_id = summary["workflow_run_id"]
    tool.mcp_tool_approval_lease_id = summary["mcp_tool_approval_lease_id"]
    tool.related_links = summary["related_links"]


def _tool_visibility_denied_execution_payload(
    entry: Any,
) -> ToolVisibilityToolPayload | None:
    tool_name = str(getattr(entry, "tool_name", "") or "").strip()
    if not tool_name:
        return None
    category = "Unknown"
    label = tool_name.replace("_", " ")
    summary = _execution_denial_payload(entry)
    return ToolVisibilityToolPayload(
        tool_name=tool_name,
        visibility_state=ToolVisibilityState.UNKNOWN,
        available=False,
        visible=False,
        execution_state=summary["execution_state"],
        source="unknown",
        reason=summary["error_summary"] or "Denied at execution.",
        requires_approval=False,
        approval_settings=None,
        provider=None,
        category=category,
        label=label,
        last_denied_at=summary["last_denied_at"],
        decision=summary["decision"],
        error_summary=summary["error_summary"],
        request_id=summary["request_id"],
        approval_request_id=summary["approval_request_id"],
        workflow_run_id=summary["workflow_run_id"],
        mcp_tool_approval_lease_id=summary["mcp_tool_approval_lease_id"],
        related_links=summary["related_links"],
    )


def _tool_visibility_denied_execution_summary(
    service: Any, session: SessionManifest
) -> tuple[dict[str, Any], int]:
    denied_page = service.list_tool_call_audit_entries(
        status="denied",
        session_id=session.session_id,
        limit=500,
    )
    latest_denied_by_tool: dict[str, Any] = {}
    denied_count = 0
    for entry in denied_page.items:
        denied_count += 1
        tool_name = str(getattr(entry, "tool_name", "") or "").strip()
        if not tool_name:
            continue
        current = latest_denied_by_tool.get(tool_name)
        entry_created_at = getattr(entry, "created_at", None)
        current_created_at = getattr(current, "created_at", None) if current is not None else None
        if current is None or current_created_at is None:
            latest_denied_by_tool[tool_name] = entry
            continue
        if entry_created_at is None or entry_created_at >= current_created_at:
            latest_denied_by_tool[tool_name] = entry
    return latest_denied_by_tool, denied_count


def build_tool_visibility_payload(
    service: Any,
    session: SessionManifest,
    *,
    mcp_transport_session_id: str | None = None,
    workflow_run_id: str | None = None,
    mcp_tool_approval_lease_id: str | None = None,
) -> ToolVisibilityPayload:
    normalized_workflow_run_id = str(workflow_run_id or "").strip() or None
    normalized_mcp_tool_approval_lease_id = str(mcp_tool_approval_lease_id or "").strip() or None
    query_context = ToolVisibilityQueryContextPayload(
        workflow_run_id=normalized_workflow_run_id,
        mcp_tool_approval_lease_id=normalized_mcp_tool_approval_lease_id,
    )
    session_context = _session_context_payload(session)
    notices = _tool_visibility_notices(session)
    tools: list[ToolVisibilityToolPayload] = []
    known_tools = service.runtime.policy._iter_known_tool_names()
    counts = ToolVisibilityCountsPayload(total=len(known_tools))
    policy_error: Exception | None = None

    try:
        service.runtime.policy_config.resolve_for_session_manifest(session)
    except Exception as exc:
        policy_error = exc
        notices.append(
            ToolVisibilityNoticePayload(
                kind="policy_unavailable",
                message=(
                    "Tool visibility could not be fully resolved because the session policy "
                    f"configuration could not be read: {exc}"
                ),
            )
        )
    if policy_error is not None:
        for tool in known_tools:
            tools.append(_unknown_tool_payload(tool, str(policy_error)))
        counts.unknown = len(known_tools)
    else:
        for tool in known_tools:
            try:
                visibility = service.runtime.policy.tool_visibility_for_session(
                    session,
                    tool,
                    workflow_run_id=normalized_workflow_run_id,
                    mcp_tool_approval_lease_id=normalized_mcp_tool_approval_lease_id,
                )
            except Exception as exc:
                tools.append(_unknown_tool_payload(tool, str(exc)))
                counts.unknown += 1
                continue

            payload = _tool_payload_for_visibility(visibility)
            tools.append(payload)
            match payload.visibility_state:
                case ToolVisibilityState.VISIBLE:
                    counts.visible += 1
                    if payload.requires_approval:
                        counts.requires_approval += 1
                case ToolVisibilityState.HIDDEN_BY_POLICY:
                    counts.hidden_by_policy += 1
                case ToolVisibilityState.UNAVAILABLE_TO_SESSION:
                    counts.unavailable_to_session += 1
                case ToolVisibilityState.UNKNOWN:
                    counts.unknown += 1

    current_visible_tool_names = sorted(
        {
            tool.tool_name
            for tool in tools
            if tool.visible and tool.visibility_state is ToolVisibilityState.VISIBLE
        }
    )
    binding_context = _binding_context_payload(
        service,
        session,
        current_visible_tool_names=current_visible_tool_names if policy_error is None else None,
        mcp_transport_session_id=mcp_transport_session_id,
        policy_error=policy_error,
    )
    if binding_context.state is not ToolVisibilityBindingState.UNKNOWN:
        notices.append(_stale_client_manifest_notice())

    try:
        latest_denied_by_tool, denied_count = _tool_visibility_denied_execution_summary(
            service, session
        )
    except Exception:
        latest_denied_by_tool = {}
        denied_count = 0
    counts.denied_at_execution = denied_count
    tool_rows_by_name = {tool.tool_name: tool for tool in tools}
    for tool_name, entry in latest_denied_by_tool.items():
        tool = tool_rows_by_name.get(tool_name)
        if tool is not None:
            _apply_execution_denial_summary(tool, entry)
            continue
        extra_tool = _tool_visibility_denied_execution_payload(entry)
        if extra_tool is None:
            continue
        tools.append(extra_tool)
        tool_rows_by_name[extra_tool.tool_name] = extra_tool
        if extra_tool.visibility_state is ToolVisibilityState.UNKNOWN:
            counts.unknown += 1

    counts.total = len(tools)

    return ToolVisibilityPayload(
        session_context=session_context,
        query_context=query_context,
        binding_context=binding_context,
        tools=tools,
        counts=counts,
        notices=notices,
    )
