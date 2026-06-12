from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import re
from pathlib import PurePath, PurePosixPath, PureWindowsPath
from typing import Any

from agent_sandbox.config import GatewayConfig
from agent_sandbox.approval_notification_targets import (
    normalize_approval_notification_target_ids,
)
from agent_sandbox.approval_diagnostics import build_payload_diagnostic
from agent_sandbox.models import (
    ApprovalGrantSettings,
    ApprovalNotificationStatus,
    ApprovalPayloadMode,
    ApprovalRequest,
    ApprovalStatus,
    DiscordCommandEnvelope,
    McpToolPolicyRule,
    SessionManifest,
    ToolArgumentConstraint,
    ToolArgumentConstraintOperator,
    ToolArgumentPolicyRule,
    TeamsMessageEnvelope,
    ToolName,
    digest_payload,
)
from agent_sandbox.messaging.targets import (
    CommunicationTargetCapability,
    CommunicationTargetDefinition,
)
from agent_sandbox.session_backends import session_has_vm_binding, session_is_container_worker_vm
from agent_sandbox.services import (
    normalize_teams_target,
    parse_teams_target,
    teams_target_from_message,
)
from agent_sandbox.policy_config import EffectivePolicyConfig, PolicyConfigResolver
from agent_sandbox.repositories.session_manifests import (
    SessionManifestRepository,
    as_session_manifest_repository,
)
from agent_sandbox.repositories.session_tool_approval_leases import (
    SessionToolApprovalLeaseRepository,
)
from agent_sandbox.storage import JsonStore


class PolicyError(RuntimeError):
    """Raised when a tool action violates the session policy."""


class ApprovalPolicyError(PolicyError):
    """PolicyError that carries structured approval rejection diagnostics."""

    def __init__(
        self,
        message: str,
        *,
        approval_request_id: str,
        rejection_kind: str,
        approval_request: ApprovalRequest | None = None,
        approval_diagnostic: dict[str, Any] | None = None,
    ) -> None:
        self.approval_request_id = approval_request_id
        self.rejection_kind = rejection_kind
        self.approval_request = approval_request
        self.approval_diagnostic = approval_diagnostic
        self.diagnostic = approval_diagnostic
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class ToolApprovalContext:
    session_id: str | None = None
    workflow_run_id: str | None = None
    lease_id: str | None = None


@dataclass(frozen=True, slots=True)
class ToolVisibility:
    tool: ToolName
    available: bool
    visible: bool
    source: str
    reason: str | None = None
    requires_approval: bool = False
    approval_settings: ApprovalGrantSettings | None = None


_TOOL_APPROVAL_CONTEXT: ContextVar[ToolApprovalContext | None] = ContextVar(
    "agent_sandbox_tool_approval_context",
    default=None,
)


def set_tool_approval_context(
    *,
    session_id: str | None,
    workflow_run_id: str | None,
    lease_id: str | None,
) -> Token[ToolApprovalContext | None]:
    context = ToolApprovalContext(
        session_id=str(session_id or "").strip() or None,
        workflow_run_id=str(workflow_run_id or "").strip() or None,
        lease_id=str(lease_id or "").strip() or None,
    )
    return _TOOL_APPROVAL_CONTEXT.set(context)


def reset_tool_approval_context(token: Token[ToolApprovalContext | None]) -> None:
    _TOOL_APPROVAL_CONTEXT.reset(token)


class ToolArgumentPolicyError(PolicyError):
    """Raised when tool arguments violate a declared policy rule."""

    def __init__(
        self,
        *,
        rule_id: str,
        constraint_id: str,
        path: str,
        message: str,
        rule_name: str | None = None,
    ) -> None:
        self.rule_id = rule_id
        self.rule_name = rule_name
        self.constraint_id = constraint_id
        self.path = path
        super().__init__(message)


def _normalize_extension(value: str | None) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""
    return text if text.startswith(".") else f".{text}"


def _normalize_mime_type(value: str | None) -> str | None:
    text = str(value or "").strip().lower()
    return text or None


def _artifact_field(artifact: Any, field_name: str, default: Any = None) -> Any:
    if isinstance(artifact, dict):
        return artifact.get(field_name, default)
    return getattr(artifact, field_name, default)


def _redact_nested_payload(redacted_keys: set[str], value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, nested_value in value.items():
            if key in redacted_keys:
                redacted[key] = "<redacted>"
                continue
            redacted[key] = _redact_nested_payload(redacted_keys, nested_value)
        return redacted
    if isinstance(value, list):
        return [_redact_nested_payload(redacted_keys, item) for item in value]
    return value


_MISSING = object()


def _lookup_argument_value(candidate: Any, key: str) -> Any:
    if isinstance(candidate, dict):
        return candidate.get(key, _MISSING)
    if hasattr(candidate, key):
        return getattr(candidate, key)
    return _MISSING


def _is_empty_argument_value(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, dict):
        return not value or all(_is_empty_argument_value(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return not value or all(_is_empty_argument_value(item) for item in value)
    return False


def _extract_argument_values(arguments: Any, path: str) -> list[Any]:
    segments = [segment.strip() for segment in str(path or "").split(".") if segment.strip()]
    if not segments:
        return []

    values: list[Any] = [arguments]
    for segment in segments:
        flatten = segment.endswith("[]")
        key = segment[:-2] if flatten else segment
        next_values: list[Any] = []
        for candidate in values:
            current = candidate if not key else _lookup_argument_value(candidate, key)
            if current is _MISSING:
                continue
            if flatten:
                if isinstance(current, (list, tuple)):
                    next_values.extend(current)
                else:
                    next_values.append(current)
            else:
                next_values.append(current)
        values = next_values
        if not values:
            break
    return values


def _stringify_path(path: str) -> str:
    return str(path or "").strip() or "<root>"


def _normalize_string_case(value: str, *, case_sensitive: bool) -> str:
    return value if case_sensitive else value.casefold()


def _values_equal(left: Any, right: Any, *, case_sensitive: bool) -> bool:
    if isinstance(left, str) and isinstance(right, str):
        return _normalize_string_case(left, case_sensitive=case_sensitive) == _normalize_string_case(
            right, case_sensitive=case_sensitive
        )
    return left == right


def _matches_pattern(value: Any, pattern: str, *, case_sensitive: bool) -> bool:
    if not isinstance(value, str):
        return False
    flags = 0 if case_sensitive else re.IGNORECASE
    return re.fullmatch(pattern, value, flags=flags) is not None


def _tool_argument_policy_error(
    tool_name: ToolName,
    rule: ToolArgumentPolicyRule,
    constraint: ToolArgumentConstraint,
    message: str,
) -> ToolArgumentPolicyError:
    return ToolArgumentPolicyError(
        rule_id=rule.id,
        rule_name=rule.name,
        constraint_id=constraint.id,
        path=constraint.path,
        message=f"tool argument policy violation for {tool_name.value}: {message}",
    )


def _approval_request_is_reusable(request: ApprovalRequest, now: datetime) -> bool:
    if request.status not in {ApprovalStatus.PENDING, ApprovalStatus.APPROVED}:
        return False
    settings = _approval_settings_for_request(request)
    if settings is not None and settings.max_uses is not None:
        if request.used_count >= settings.max_uses:
            return False
    return request.expires_at > now


def _approval_settings_for_request(request: ApprovalRequest) -> ApprovalGrantSettings | None:
    if request.approval_settings is not None:
        return request.approval_settings
    if request.action_type == "mcp_tool_invocation":
        return ApprovalGrantSettings()
    return None


def _approval_rejection_kind_for_status(status: ApprovalStatus) -> str:
    if status == ApprovalStatus.DENIED:
        return "denied"
    if status == ApprovalStatus.EXPIRED:
        return "expired"
    if status == ApprovalStatus.EXHAUSTED:
        return "exhausted"
    return "not_approved"


def _approval_rejection_error(
    *,
    request_id: str,
    rejection_kind: str,
    message: str,
    request: ApprovalRequest | None = None,
    attempted_payload: dict[str, Any] | None = None,
    status: str | None = None,
    redacted_keys: set[str] | None = None,
) -> ApprovalPolicyError:
    diagnostic: dict[str, Any] = {
        "approval_request_id": request_id,
        "rejection_kind": rejection_kind,
    }
    if request is not None and attempted_payload is not None:
        diagnostic.update(
            build_payload_diagnostic(
                approved_payload=request.payload,
                attempted_payload=attempted_payload,
                redacted_keys=redacted_keys,
                status=status or request.status.value,
                reason=message,
                approved_payload_digest=request.payload_digest,
                attempted_payload_digest=digest_payload(attempted_payload),
            )
        )
    else:
        diagnostic["status"] = status
        diagnostic["reason"] = message
    return ApprovalPolicyError(
        message,
        approval_request_id=request_id,
        rejection_kind=rejection_kind,
        approval_request=request,
        approval_diagnostic=diagnostic,
    )


def _stricter_max_uses(current: int | None, limit: int | None) -> int | None:
    if limit is None:
        return current
    if current is None:
        return limit
    return min(current, limit)


class PolicyEngine:
    def __init__(
        self,
        config: GatewayConfig,
        session_store: SessionManifestRepository,
        approval_store: JsonStore[ApprovalRequest],
        *,
        policy_config: PolicyConfigResolver | None = None,
        tool_approval_leases: SessionToolApprovalLeaseRepository | None = None,
    ) -> None:
        self.config = config
        self.session_store = as_session_manifest_repository(session_store)
        self.approval_store = approval_store
        self.tool_approval_leases = tool_approval_leases
        self.policy_config = policy_config or PolicyConfigResolver(
            config,
            session_store=session_store,
        )
        self.approval_notification_dispatcher: Any | None = None

    def set_approval_notification_dispatcher(self, dispatcher: Any | None) -> None:
        self.approval_notification_dispatcher = dispatcher

    def get_session(self, session_id: str) -> SessionManifest:
        session = self.session_store.get_session(session_id)
        if session is None:
            raise PolicyError(f"Unknown session: {session_id}")
        if session.expires_at <= datetime.now(UTC):
            raise PolicyError(f"Expired session: {session_id}")
        return session

    def _tool_requires_approval_for_session(
        self,
        session: SessionManifest,
        tool_name: ToolName,
        *,
        effective_policy: EffectivePolicyConfig | None = None,
    ) -> bool:
        if tool_name in {ToolName.APPROVAL_SUBMIT, ToolName.APPROVAL_CHECK}:
            return False
        effective_policy = effective_policy or self.effective_policy_for_session(session)
        required_tools = set(session.approval_required_tools or [])
        required_tools.update(effective_policy.approval_required_tools)
        rule = effective_policy.mcp_tool_policies.get(tool_name.value)
        if rule is not None and rule.require_approval:
            return True
        return tool_name in required_tools

    def _tool_visibility_blocked(
        self,
        *,
        tool_name: ToolName,
        source: str,
        reason: str,
        requires_approval: bool = False,
    ) -> ToolVisibility:
        return ToolVisibility(
            tool=tool_name,
            available=False,
            visible=False,
            source=source,
            reason=reason,
            requires_approval=requires_approval,
        )

    def _tool_visibility_for_session(
        self,
        session: SessionManifest,
        tool_name: ToolName,
        *,
        workflow_run_id: str | None = None,
        mcp_tool_approval_lease_id: str | None = None,
        effective_policy: EffectivePolicyConfig | None = None,
    ) -> ToolVisibility:
        effective_policy = effective_policy or self.effective_policy_for_session(session)
        requires_approval = self._tool_requires_approval_for_session(
            session,
            tool_name,
            effective_policy=effective_policy,
        )

        if tool_name.value.startswith("assistant_"):
            profile = str(session.metadata.get("capability_profile") or "").strip().lower()
            if not profile:
                return self._tool_visibility_blocked(
                    tool_name=tool_name,
                    source="session.metadata.capability_profile",
                    reason=(
                        f"Session {session.session_id} has no capability profile; "
                        "assistant tool access is denied by default"
                    ),
                    requires_approval=requires_approval,
                )
            if tool_name in {
                ToolName.ASSISTANT_UPSERT_CONFIG,
                ToolName.ASSISTANT_CREATE_WORKFLOW,
                ToolName.ASSISTANT_RUN_SAFE_OPERATION,
            }:
                try:
                    self._require_assistant_mutation_guardrails(session, tool_name)
                except PolicyError as exc:
                    return self._tool_visibility_blocked(
                        tool_name=tool_name,
                        source="session.metadata.assistant_mutation_policy",
                        reason=str(exc),
                        requires_approval=requires_approval,
                    )

        rule = effective_policy.mcp_tool_policies.get(tool_name.value)
        if rule is not None and rule.denied:
            return self._tool_visibility_blocked(
                tool_name=tool_name,
                source="directory.mcp_tool_policies",
                reason=(
                    f"Tool {tool_name.value} is denied by directory policy for session "
                    f"{session.session_id}"
                ),
                requires_approval=requires_approval,
            )

        has_tool_permission = tool_name in session.allowed_tools
        source = "session.allowed_tools"
        if tool_name == ToolName.LIST_PENDING_AGENT_PROMPTS and not has_tool_permission:
            has_tool_permission = ToolName.RESPOND_TO_AGENT in session.allowed_tools
            if has_tool_permission:
                source = "session.allowed_tools.respond_to_agent"
        if not has_tool_permission:
            has_tool_permission = self._lease_allows_tool(
                session_id=session.session_id,
                tool_name=tool_name,
                workflow_run_id=workflow_run_id,
                mcp_tool_approval_lease_id=mcp_tool_approval_lease_id,
            )
            if has_tool_permission:
                source = "session_tool_approval_lease"

        if not has_tool_permission:
            return self._tool_visibility_blocked(
                tool_name=tool_name,
                source="session.allowed_tools",
                reason=f"Tool {tool_name.value} is not allowed for session {session.session_id}",
                requires_approval=requires_approval,
            )

        if tool_name == ToolName.SCHEDULE_RESUME and not (
            session_has_vm_binding(session) or session_is_container_worker_vm(session)
        ):
            return self._tool_visibility_blocked(
                tool_name=tool_name,
                source="session.vm_binding",
                reason=f"Tool {tool_name.value} requires a session with a VM binding",
                requires_approval=requires_approval,
            )

        if tool_name in {
            ToolName.HAS_PENDING_FEEDBACK,
            ToolName.LIST_UNREAD_FEEDBACK,
            ToolName.LIST_ALL_FEEDBACK,
        }:
            try:
                self.require_pr_feedback_streams(session)
            except PolicyError as exc:
                return self._tool_visibility_blocked(
                    tool_name=tool_name,
                    source="session.metadata.pr_feedback_streams",
                    reason=str(exc),
                    requires_approval=requires_approval,
                )

        approval_settings = (
            self.approval_grant_settings_for_tool(session, tool_name)
            if requires_approval
            else None
        )
        return ToolVisibility(
            tool=tool_name,
            available=True,
            visible=True,
            source=source,
            requires_approval=requires_approval,
            approval_settings=approval_settings,
        )

    def require_tool(
        self,
        session_id: str,
        tool_name: ToolName,
        *,
        workflow_run_id: str | None = None,
        mcp_tool_approval_lease_id: str | None = None,
    ) -> SessionManifest:
        session = self.get_session(session_id)
        visibility = self._tool_visibility_for_session(
            session,
            tool_name,
            workflow_run_id=workflow_run_id,
            mcp_tool_approval_lease_id=mcp_tool_approval_lease_id,
        )
        if not visibility.available:
            raise PolicyError(
                visibility.reason
                or f"Tool {tool_name.value} is not allowed for session {session_id}"
            )
        return session

    def require_tool_not_denied(self, session: SessionManifest, tool_name: ToolName) -> None:
        rule = self.mcp_tool_policy_for_session(session, tool_name)
        if rule is not None and rule.denied:
            raise PolicyError(
                f"Tool {tool_name.value} is denied by directory policy for session {session.session_id}"
            )

    def _lease_allows_tool(
        self,
        *,
        session_id: str,
        tool_name: ToolName,
        workflow_run_id: str | None,
        mcp_tool_approval_lease_id: str | None,
    ) -> bool:
        if self.tool_approval_leases is None:
            return False
        context = _TOOL_APPROVAL_CONTEXT.get()
        normalized_session_id = str(session_id or "").strip()
        resolved_workflow_run_id = str(workflow_run_id or "").strip() or None
        resolved_lease_id = str(mcp_tool_approval_lease_id or "").strip() or None
        if context is not None and context.session_id == normalized_session_id:
            resolved_workflow_run_id = resolved_workflow_run_id or context.workflow_run_id
            resolved_lease_id = resolved_lease_id or context.lease_id
        if not resolved_workflow_run_id or not resolved_lease_id:
            return False
        return self.tool_approval_leases.is_tool_approved(
            lease_id=resolved_lease_id,
            session_id=normalized_session_id,
            workflow_run_id=resolved_workflow_run_id,
            tool_name=tool_name,
        )

    def consume_tool_approval_lease_use(
        self,
        *,
        session_id: str | None,
        tool_name: ToolName,
        workflow_run_id: str | None,
        mcp_tool_approval_lease_id: str | None,
    ) -> None:
        if self.tool_approval_leases is None:
            return
        normalized_session_id = str(session_id or "").strip()
        normalized_workflow_run_id = str(workflow_run_id or "").strip()
        normalized_lease_id = str(mcp_tool_approval_lease_id or "").strip()
        if not normalized_session_id or not normalized_workflow_run_id or not normalized_lease_id:
            return
        session = self.get_session(normalized_session_id)
        if tool_name in session.allowed_tools:
            return
        self.require_tool_not_denied(session, tool_name)
        rule = self.mcp_tool_policy_for_session(session, tool_name)
        if rule is not None and rule.require_approval:
            raise PolicyError(
                f"Tool {tool_name.value} requires per-invocation approval in this directory"
            )
        if rule is not None and rule.require_exact_payload:
            raise PolicyError(
                f"Tool {tool_name.value} requires exact-payload approval in this directory"
            )
        consumed = self.tool_approval_leases.consume_tool_use(
            lease_id=normalized_lease_id,
            session_id=normalized_session_id,
            workflow_run_id=normalized_workflow_run_id,
            tool_name=tool_name,
        )
        if not consumed:
            raise PolicyError(
                f"Tool {tool_name.value} is not allowed for session {normalized_session_id}: "
                f"approval lease {normalized_lease_id} is not usable"
            )

    def approval_required_for_tool(self, session: SessionManifest, tool_name: ToolName) -> bool:
        if tool_name in {ToolName.APPROVAL_SUBMIT, ToolName.APPROVAL_CHECK}:
            return False
        self.require_tool_not_denied(session, tool_name)
        return self._tool_requires_approval_for_session(session, tool_name)

    def tool_visibility_for_session(
        self,
        session: SessionManifest,
        tool_name: ToolName,
        *,
        workflow_run_id: str | None = None,
        mcp_tool_approval_lease_id: str | None = None,
    ) -> ToolVisibility:
        return self._tool_visibility_for_session(
            session,
            tool_name,
            workflow_run_id=workflow_run_id,
            mcp_tool_approval_lease_id=mcp_tool_approval_lease_id,
        )

    def visible_tools_for_session(
        self,
        session: SessionManifest,
        *,
        workflow_run_id: str | None = None,
        mcp_tool_approval_lease_id: str | None = None,
    ) -> list[ToolVisibility]:
        effective_policy = self.effective_policy_for_session(session)
        visibilities: list[ToolVisibility] = []
        for tool in ToolName:
            visibility = self._tool_visibility_for_session(
                session,
                tool,
                workflow_run_id=workflow_run_id,
                mcp_tool_approval_lease_id=mcp_tool_approval_lease_id,
                effective_policy=effective_policy,
            )
            if visibility.visible:
                visibilities.append(visibility)
        return visibilities

    def allowed_tool_summary_for_session(
        self,
        session: SessionManifest,
        *,
        workflow_run_id: str | None = None,
        mcp_tool_approval_lease_id: str | None = None,
    ) -> dict[str, Any]:
        effective_policy = self.effective_policy_for_session(session)
        available_tools: list[dict[str, Any]] = []
        approval_required_tools: list[str] = []
        unavailable_tools: list[dict[str, Any]] = []

        for tool in ToolName:
            visibility = self._tool_visibility_for_session(
                session,
                tool,
                workflow_run_id=workflow_run_id,
                mcp_tool_approval_lease_id=mcp_tool_approval_lease_id,
                effective_policy=effective_policy,
            )
            if visibility.visible:
                entry: dict[str, Any] = {
                    "tool_name": visibility.tool.value,
                    "source": visibility.source,
                    "requires_approval": visibility.requires_approval,
                }
                if visibility.approval_settings is not None:
                    entry["approval_settings"] = visibility.approval_settings.model_dump(mode="json")
                available_tools.append(entry)
                if visibility.requires_approval:
                    approval_required_tools.append(visibility.tool.value)
                continue
            unavailable_tools.append(
                {
                    "tool_name": visibility.tool.value,
                    "source": visibility.source,
                    "reason": visibility.reason,
                }
            )

        return {
            "session_id": session.session_id,
            "available_tools": available_tools,
            "approval_required_tools": approval_required_tools,
            "unavailable_tools": unavailable_tools,
        }

    def mcp_tool_policy_for_session(
        self, session: SessionManifest, tool_name: ToolName
    ) -> McpToolPolicyRule | None:
        return self.effective_policy_for_session(session).mcp_tool_policies.get(tool_name.value)

    def approval_grant_settings_for_tool(
        self, session: SessionManifest, tool_name: ToolName
    ) -> ApprovalGrantSettings:
        rule = self.mcp_tool_policy_for_session(session, tool_name)
        settings = ApprovalGrantSettings()
        if rule is not None and rule.default_settings is not None:
            settings = rule.default_settings
        session_settings = session.approval_tool_settings.get(tool_name.value)
        if session_settings is not None:
            settings = session_settings
        return self._apply_approval_grant_limits(settings, rule)

    def _apply_approval_grant_limits(
        self, settings: ApprovalGrantSettings, rule: McpToolPolicyRule | None
    ) -> ApprovalGrantSettings:
        ttl_seconds = settings.ttl_seconds
        max_uses = settings.max_uses
        payload_mode = settings.payload_mode
        if rule is not None:
            if rule.max_ttl_seconds is not None:
                ttl_seconds = min(ttl_seconds, rule.max_ttl_seconds)
            max_uses = _stricter_max_uses(max_uses, rule.max_uses)
            if rule.require_exact_payload:
                payload_mode = ApprovalPayloadMode.EXACT
        return ApprovalGrantSettings(
            ttl_seconds=ttl_seconds,
            max_uses=max_uses,
            payload_mode=payload_mode,
        )

    def tool_argument_policy_for_session(
        self, session: SessionManifest, tool_name: ToolName
    ) -> list[ToolArgumentPolicyRule]:
        effective_policy = self.effective_policy_for_session(session)
        inherited_rules = list(effective_policy.tool_argument_policies.get(tool_name.value, []))
        session_rules = list(session.tool_argument_policies.get(tool_name.value, []))
        return inherited_rules + session_rules

    def require_tool_arguments(
        self,
        session: SessionManifest,
        tool_name: ToolName,
        arguments: dict[str, Any] | Any,
    ) -> None:
        rules = self.tool_argument_policy_for_session(session, tool_name)
        if not rules:
            return

        for rule in rules:
            for constraint in rule.constraints:
                extracted_values = _extract_argument_values(arguments, constraint.path)
                present_values = [
                    value for value in extracted_values if not _is_empty_argument_value(value)
                ]

                if constraint.operator == ToolArgumentConstraintOperator.REQUIRED:
                    if present_values:
                        continue
                    raise _tool_argument_policy_error(
                        tool_name,
                        rule,
                        constraint,
                        f"path {_stringify_path(constraint.path)!r} must be present",
                    )

                if constraint.operator == ToolArgumentConstraintOperator.FIXED:
                    if not present_values:
                        raise _tool_argument_policy_error(
                            tool_name,
                            rule,
                            constraint,
                            f"path {_stringify_path(constraint.path)!r} must be present",
                        )
                    if len(present_values) != 1:
                        raise _tool_argument_policy_error(
                            tool_name,
                            rule,
                            constraint,
                            f"path {_stringify_path(constraint.path)!r} must resolve to a single scalar",
                        )
                    if _values_equal(
                        present_values[0],
                        constraint.value,
                        case_sensitive=constraint.case_sensitive,
                    ):
                        continue
                    raise _tool_argument_policy_error(
                        tool_name,
                        rule,
                        constraint,
                        f"path {_stringify_path(constraint.path)!r} must equal {constraint.value!r}",
                    )

                if not present_values:
                    continue

                if constraint.operator in {
                    ToolArgumentConstraintOperator.ALLOWLIST,
                    ToolArgumentConstraintOperator.ENUM,
                }:
                    allowed_values = list(constraint.values or [])
                    if all(
                        any(
                            _values_equal(
                                present_value,
                                allowed_value,
                                case_sensitive=constraint.case_sensitive,
                            )
                            for allowed_value in allowed_values
                        )
                        for present_value in present_values
                    ):
                        continue
                    raise _tool_argument_policy_error(
                        tool_name,
                        rule,
                        constraint,
                        f"path {_stringify_path(constraint.path)!r} contains a value outside the allowed set",
                    )

                if constraint.operator == ToolArgumentConstraintOperator.DENYLIST:
                    denied_values = list(constraint.values or [])
                    if all(
                        all(
                            not _values_equal(
                                present_value,
                                denied_value,
                                case_sensitive=constraint.case_sensitive,
                            )
                            for denied_value in denied_values
                        )
                        for present_value in present_values
                    ):
                        continue
                    raise _tool_argument_policy_error(
                        tool_name,
                        rule,
                        constraint,
                        f"path {_stringify_path(constraint.path)!r} contains a denied value",
                    )

                if constraint.operator == ToolArgumentConstraintOperator.PATTERN:
                    if all(
                        _matches_pattern(
                            present_value,
                            str(constraint.pattern or ""),
                            case_sensitive=constraint.case_sensitive,
                        )
                        for present_value in present_values
                    ):
                        continue
                    raise _tool_argument_policy_error(
                        tool_name,
                        rule,
                        constraint,
                        f"path {_stringify_path(constraint.path)!r} does not match the required pattern",
                    )

    def _require_assistant_mutation_guardrails(
        self, session: SessionManifest, tool_name: ToolName
    ) -> None:
        metadata = session.metadata if isinstance(session.metadata, dict) else {}
        mutation_policy = metadata.get("assistant_mutation_policy")
        if not isinstance(mutation_policy, dict):
            raise PolicyError(
                f"Session {session.session_id} is missing metadata.assistant_mutation_policy for "
                f"{tool_name.value}"
            )
        writable_scopes = mutation_policy.get("writable_scopes")
        if not isinstance(writable_scopes, list) or not any(
            isinstance(item, str) and item.strip() for item in writable_scopes
        ):
            raise PolicyError(
                f"Session {session.session_id} must define non-empty assistant mutation writable_scopes"
            )
        if tool_name == ToolName.ASSISTANT_RUN_SAFE_OPERATION and bool(
            mutation_policy.get("require_destructive_approval_gate", False)
        ):
            approval_gate = str(mutation_policy.get("destructive_approval_gate") or "").strip()
            if not approval_gate:
                raise PolicyError(
                    f"Session {session.session_id} requires destructive approval gate metadata"
                )

    def require_script_name(self, session: SessionManifest, script_name: str) -> str:
        normalized = str(script_name or "").strip()
        if not normalized:
            raise PolicyError("script_name is required")
        if normalized not in set(session.allowed_script_names or []):
            raise PolicyError(
                f"Script {normalized!r} is not allowlisted for session {session.session_id}"
            )
        return normalized

    def require_pr_feedback_streams(self, session: SessionManifest) -> list[dict[str, str]]:
        streams = session.metadata.get("pr_feedback_streams")
        if not isinstance(streams, list) or not streams:
            raise PolicyError(
                f"Session {session.session_id} is missing metadata.pr_feedback_streams configuration"
            )

        validated: list[dict[str, str]] = []
        for stream in streams:
            if not isinstance(stream, dict):
                raise PolicyError(
                    f"Session {session.session_id} has invalid pr feedback stream configuration"
                )
            provider = str(stream.get("provider", "")).strip()
            repo = str(stream.get("repo", "")).strip()
            pr_id = str(stream.get("pr_id", "")).strip()
            if not provider or not repo or not pr_id:
                raise PolicyError(
                    f"Session {session.session_id} has incomplete pr feedback stream metadata"
                )
            validated.append({"provider": provider, "repo": repo, "pr_id": pr_id})
        return validated

    def require_pr_feedback_stream(
        self,
        session: SessionManifest,
        *,
        provider: str,
        repo: str,
        pr_id: str,
    ) -> None:
        streams = self.require_pr_feedback_streams(session)
        allowed = {(stream["provider"], stream["repo"], stream["pr_id"]) for stream in streams}
        key = (provider, repo, pr_id)
        if key not in allowed:
            raise PolicyError(
                f"PR feedback stream {provider}:{repo}:{pr_id} is not allowlisted for session "
                f"{session.session_id}"
            )

    def require_pr_feedback_repo(
        self,
        session: SessionManifest,
        *,
        provider: str,
        repo: str,
    ) -> None:
        streams = self.require_pr_feedback_streams(session)
        allowed = {(stream["provider"], stream["repo"]) for stream in streams}
        key = (provider, repo)
        if key not in allowed:
            raise PolicyError(
                f"PR repository {provider}:{repo} is not allowlisted for session {session.session_id}"
            )

    def _communication_target_for_session(
        self,
        session: SessionManifest,
        reference: str,
        *,
        provider: str,
        capability: CommunicationTargetCapability,
    ) -> CommunicationTargetDefinition | None:
        try:
            return self.policy_config.resolve_communication_target_reference_for_session(
                session,
                reference,
                provider=provider,
                capability=capability,
            )
        except ValueError as exc:
            raise PolicyError(str(exc)) from exc

    @staticmethod
    def _discord_channel_id_from_target(target: CommunicationTargetDefinition) -> str:
        return str(target.address or "").strip().split("/")[-1].strip()

    def _requested_discord_channel_id(
        self,
        session: SessionManifest,
        channel_id: str,
        *,
        capability: CommunicationTargetCapability,
    ) -> str:
        normalized_channel_id = str(channel_id or "").strip()
        target = self._communication_target_for_session(
            session,
            normalized_channel_id,
            provider="discord",
            capability=capability,
        )
        if target is not None:
            return self._discord_channel_id_from_target(target)
        return normalized_channel_id

    def _requested_teams_target(
        self,
        session: SessionManifest,
        channel_target: str,
        *,
        capability: CommunicationTargetCapability,
        allow_user_target: bool,
    ) -> str:
        raw_target = str(channel_target or "").strip()
        target = self._communication_target_for_session(
            session,
            raw_target,
            provider="teams",
            capability=capability,
        )
        try:
            target_value = target.address if target is not None else raw_target
            normalized_target = normalize_teams_target(target_value)
        except ValueError as exc:
            raise PolicyError(str(exc)) from exc
        if parse_teams_target(normalized_target).kind == "user" and not allow_user_target:
            raise PolicyError("Teams user targets are only supported for send operations")
        return normalized_target

    def require_discord_channel(
        self,
        session: SessionManifest,
        channel_id: str,
        *,
        capability: CommunicationTargetCapability = "read",
    ) -> str:
        requested_channel_id = self._requested_discord_channel_id(
            session,
            channel_id,
            capability=capability,
        )
        allowed_channel_ids = set(
            self.policy_config.resolved_discord_channel_ids_for_session(
                session,
                capability=capability,
            )
        )
        if requested_channel_id not in allowed_channel_ids:
            raise PolicyError(
                f"Discord channel is not allowlisted for session {session.session_id}"
            )
        return requested_channel_id

    def require_discord_command_source(
        self,
        session: SessionManifest,
        command: DiscordCommandEnvelope,
    ) -> None:
        effective_policy = self.effective_policy_for_session(session)
        source = str(command.metadata.get("source", ""))
        if source != "discord_bot_api":
            raise PolicyError(
                f"Discord command {command.command_id} has untrusted source {source!r}"
            )

        source_channel_id = str(command.metadata.get("channel_id", command.channel_id)).strip()
        source_author_id = str(command.metadata.get("author_id", command.author_id)).strip()
        if not source_channel_id or not source_author_id:
            raise PolicyError(
                f"Discord command {command.command_id} is missing required source metadata"
            )

        allowlisted_channel = source_channel_id in set(
            self.policy_config.resolved_discord_channel_ids_for_session(
                session,
                capability="read",
            )
        )
        allowlisted_operator = source_author_id in effective_policy.discord_operator_user_allowlist
        if not (allowlisted_channel or allowlisted_operator):
            raise PolicyError(
                f"Discord command {command.command_id} source is not allowlisted for session "
                f"{session.session_id}"
            )

    def require_teams_message_source(
        self,
        session: SessionManifest,
        message: TeamsMessageEnvelope,
    ) -> None:
        source = str(message.metadata.get("source", ""))
        if source == "teams_graph_api_status":
            # Historical name: this tag marks a Teams message previously posted
            # through the Graph API by one of our agent sessions. Visibility is
            # still enforced by the allowlisted Teams target below.
            pass
        elif source != "teams_graph_api":
            raise PolicyError(f"Teams message {message.cursor} has untrusted source {source!r}")

        try:
            target = teams_target_from_message(
                team_id=message.team_id,
                channel_id=message.channel_id,
                chat_id=message.chat_id,
                metadata=message.metadata,
            )
        except ValueError as exc:
            raise PolicyError(
                f"Teams message {message.cursor} is missing required source metadata"
            ) from exc

        if target not in set(
            self.policy_config.resolved_teams_channel_targets_for_session(
                session,
                capability="read",
            )
        ):
            raise PolicyError(
                f"Teams message {message.cursor} source is not allowlisted for session "
                f"{session.session_id}"
            )

    def require_teams_channel_target(
        self,
        session: SessionManifest,
        channel_target: str,
        *,
        capability: CommunicationTargetCapability = "read",
    ) -> str:
        normalized_target = self._requested_teams_target(
            session,
            channel_target,
            capability=capability,
            allow_user_target=False,
        )
        if normalized_target not in set(
            self.policy_config.resolved_teams_channel_targets_for_session(
                session,
                capability=capability,
            )
        ):
            raise PolicyError(f"Teams target is not allowlisted for session {session.session_id}")
        return normalized_target

    def require_teams_send_target(
        self,
        session: SessionManifest,
        channel_target: str,
        *,
        capability: CommunicationTargetCapability = "write",
    ) -> str:
        normalized_target = self._requested_teams_target(
            session,
            channel_target,
            capability=capability,
            allow_user_target=True,
        )
        parsed_target = parse_teams_target(normalized_target)
        if parsed_target.kind == "user":
            allowed_users = set(
                self.policy_config.resolved_teams_user_targets_for_session(
                    session,
                    capability=capability,
                )
            )
            if normalized_target not in allowed_users:
                raise PolicyError(
                    f"Teams user target is not allowlisted for session {session.session_id}"
                )
            return normalized_target
        if normalized_target not in set(
            self.policy_config.resolved_teams_channel_targets_for_session(
                session,
                capability=capability,
            )
        ):
            raise PolicyError(f"Teams target is not allowlisted for session {session.session_id}")
        return normalized_target

    def require_email_folder(self, session: SessionManifest, folder: str) -> None:
        if folder not in session.email_folder_allowlist:
            raise PolicyError(f"Email folder is not allowlisted for session {session.session_id}")

    def require_email_mailbox(self, session: SessionManifest, mailbox_id: str) -> None:
        mailbox_allowlist = (
            session.email_mailbox_allowlist
            or self.effective_policy_for_session(session).email_mailbox_allowlist
        )
        if mailbox_id not in mailbox_allowlist:
            raise PolicyError(f"Email mailbox is not allowlisted for session {session.session_id}")

    def require_email_folder_for_mailbox(
        self,
        session: SessionManifest,
        mailbox_id: str,
        folder: str,
    ) -> None:
        mailbox_folders = (
            session.email_folder_allowlist_by_mailbox
            or self.effective_policy_for_session(session).email_folder_allowlist_by_mailbox
        )
        allowed_folders = mailbox_folders.get(mailbox_id, [])
        if folder in allowed_folders:
            return
        # Backward compatibility for older manifests that only had global folder allowlists.
        self.require_email_folder(session, folder)

    def require_email_send_enabled(self, session: SessionManifest) -> None:
        effective_policy = self.effective_policy_for_session(session)
        if not (session.email_send_enabled or effective_policy.email_send_enabled):
            raise PolicyError(f"Email send is disabled for session {session.session_id}")

    def _communication_file_policy_value(self, session: SessionManifest, name: str) -> Any:
        session_value = getattr(session, name)
        effective_value = getattr(self.effective_policy_for_session(session), name)
        if isinstance(session_value, bool):
            return bool(session_value or effective_value)
        if session_value not in (None, [], {}):
            return session_value
        return effective_value

    def require_communication_file_receive_enabled(self, session: SessionManifest) -> None:
        if not self._communication_file_policy_value(
            session, "communication_file_receive_enabled"
        ):
            raise PolicyError(
                f"Inbound communication file receive is disabled for session {session.session_id}"
            )

    def require_inline_media_receive_enabled(
        self,
        session: SessionManifest,
        *,
        source_kind: str,
    ) -> None:
        """Require receive permission for inline media that will become staged artifacts.

        PR feedback and backlog inline screenshots are not chat attachments, but they use
        the same host-side communication artifact staging path.  Keep a single receive
        gate so sessions must explicitly opt in before any provider-sourced media can
        be downloaded.
        """

        try:
            self.require_communication_file_receive_enabled(session)
        except PolicyError as exc:
            normalized_source = str(source_kind or "inline_media").strip() or "inline_media"
            raise PolicyError(
                f"Inline media receive is disabled for {normalized_source} in session "
                f"{session.session_id}"
            ) from exc

    def require_inline_media_content(
        self,
        session: SessionManifest,
        *,
        source_kind: str,
        filename: str,
        mime_type: str | None,
        byte_count: int,
    ) -> None:
        """Apply the communication-file MIME/size policy to inline media downloads."""

        self.require_inline_media_receive_enabled(session, source_kind=source_kind)
        self.require_communication_file_content(
            session,
            filename=filename,
            mime_type=mime_type,
            byte_count=byte_count,
        )

    def require_communication_file_send_enabled(self, session: SessionManifest) -> None:
        if not self._communication_file_policy_value(
            session, "communication_file_send_enabled"
        ):
            raise PolicyError(
                f"Outbound communication file send is disabled for session {session.session_id}"
            )

    def require_safe_communication_filename(self, session: SessionManifest, filename: str) -> str:
        normalized = str(filename or "").strip()
        if not normalized:
            raise PolicyError("Communication file filename is required")
        if "\x00" in normalized:
            raise PolicyError("Communication file filename contains a NUL byte")
        if normalized in {".", ".."}:
            raise PolicyError("Communication file filename is not safe")
        if any(part == ".." for part in PurePosixPath(normalized).parts):
            raise PolicyError("Communication file filename must not contain parent traversal")
        if any(part == ".." for part in PureWindowsPath(normalized).parts):
            raise PolicyError("Communication file filename must not contain parent traversal")
        if PurePosixPath(normalized).is_absolute() or PureWindowsPath(normalized).is_absolute():
            raise PolicyError("Communication file filename must not be absolute")
        if PurePath(normalized).name != normalized or "/" in normalized or "\\" in normalized:
            raise PolicyError("Communication file filename must not contain path separators")
        return normalized

    def require_communication_file_scope(
        self,
        session: SessionManifest,
        *,
        provider: str,
        mailbox_id: str | None = None,
        channel_id: str | None = None,
        channel_target: str | None = None,
        allow_teams_user_target: bool = False,
        capability: CommunicationTargetCapability = "read",
    ) -> str:
        normalized_provider = str(provider or "").strip().lower()
        if normalized_provider == "discord":
            return self.require_discord_channel(
                session,
                str(channel_id or "").strip(),
                capability=capability,
            )
        elif normalized_provider == "teams":
            if allow_teams_user_target:
                return self.require_teams_send_target(
                    session,
                    str(channel_target or "").strip(),
                    capability=capability,
                )
            return self.require_teams_channel_target(
                session,
                str(channel_target or "").strip(),
                capability=capability,
            )
        elif normalized_provider == "email":
            self.require_email_mailbox(session, str(mailbox_id or "").strip())
            return str(mailbox_id or "").strip()
        else:
            raise PolicyError("Communication file provider must be one of: discord, teams, email")

    def require_communication_file_content(
        self,
        session: SessionManifest,
        *,
        filename: str,
        mime_type: str | None,
        byte_count: int,
    ) -> None:
        safe_filename = self.require_safe_communication_filename(session, filename)
        if byte_count < 0:
            raise PolicyError("Communication file size must be non-negative")
        max_bytes = self._communication_file_policy_value(
            session, "communication_file_max_bytes"
        )
        if max_bytes is not None and byte_count > int(max_bytes):
            raise PolicyError(
                f"Communication file exceeds max bytes for session {session.session_id}: "
                f"{byte_count} > {int(max_bytes)}"
            )
        extension = _normalize_extension(PurePath(safe_filename).suffix)
        blocked_extensions = {
            _normalize_extension(value)
            for value in self._communication_file_policy_value(
                session, "communication_file_blocked_extensions"
            )
            if str(value or "").strip()
        }
        if extension and extension in blocked_extensions:
            raise PolicyError(
                f"Communication file extension {extension!r} is blocked for session "
                f"{session.session_id}"
            )
        allowed_extensions = {
            _normalize_extension(value)
            for value in self._communication_file_policy_value(
                session, "communication_file_allowed_extensions"
            )
            if str(value or "").strip()
        }
        if allowed_extensions and extension not in allowed_extensions:
            raise PolicyError(
                f"Communication file extension {extension or '<none>'!r} is not allowlisted "
                f"for session {session.session_id}"
            )
        normalized_mime = _normalize_mime_type(mime_type)
        allowed_mime_types = {
            str(value or "").strip().lower()
            for value in self._communication_file_policy_value(
                session, "communication_file_allowed_mime_types"
            )
            if str(value or "").strip()
        }
        if allowed_mime_types and normalized_mime not in allowed_mime_types:
            raise PolicyError(
                f"Communication file MIME type {normalized_mime or '<unknown>'!r} is not "
                f"allowlisted for session {session.session_id}"
            )

    def require_communication_file_artifacts(
        self,
        session: SessionManifest,
        artifacts: list[Any],
        *,
        provider: str,
        mailbox_id: str | None = None,
        channel_id: str | None = None,
        channel_target: str | None = None,
        allow_teams_user_target: bool = False,
        capability: CommunicationTargetCapability = "write",
    ) -> str:
        self.require_communication_file_send_enabled(session)
        resolved_scope = self.require_communication_file_scope(
            session,
            provider=provider,
            mailbox_id=mailbox_id,
            channel_id=channel_id,
            channel_target=channel_target,
            allow_teams_user_target=allow_teams_user_target,
            capability=capability,
        )
        for artifact in artifacts:
            artifact_session_id = _artifact_field(artifact, "session_id")
            if artifact_session_id is not None and str(artifact_session_id) != session.session_id:
                raise PolicyError(
                    f"Communication file artifact is not owned by session {session.session_id}"
                )
            self.require_communication_file_content(
                session,
                filename=str(
                    _artifact_field(
                        artifact,
                        "normalized_safe_filename",
                        _artifact_field(artifact, "filename", ""),
                    )
                ),
                mime_type=_artifact_field(artifact, "mime_type"),
                byte_count=int(_artifact_field(artifact, "byte_count", 0)),
            )
        return resolved_scope

    def require_communication_file_send_approval(
        self,
        session: SessionManifest,
        *,
        request_id: str | None,
        payload: dict[str, Any],
    ) -> ApprovalRequest | None:
        if not self._communication_file_policy_value(
            session, "communication_file_require_approval_for_send"
        ):
            return None
        normalized_request_id = str(request_id or "").strip()
        if not normalized_request_id:
            raise PolicyError(
                "Outbound communication file send requires approval for session "
                f"{session.session_id}"
            )
        approval = self.require_approved(normalized_request_id, payload)
        if approval.session_id != session.session_id:
            raise PolicyError(
                f"Approval request {normalized_request_id} does not belong to session "
                f"{session.session_id}"
            )
        return approval

    def require_draft_recipients(self, session: SessionManifest, recipients: list[str]) -> None:
        if not recipients:
            return
        normalized_recipients = [
            recipient.strip().lower() for recipient in recipients if recipient.strip()
        ]
        if not normalized_recipients:
            return

        effective_policy = self.effective_policy_for_session(session)
        recipient_allowlist = (
            session.email_draft_recipient_allowlist
            or effective_policy.email_draft_recipient_allowlist
        )
        if recipient_allowlist:
            allowlisted = {recipient.strip().lower() for recipient in recipient_allowlist}
            denied = [
                recipient for recipient in normalized_recipients if recipient not in allowlisted
            ]
            if denied:
                raise PolicyError(
                    f"Draft recipients are not allowlisted for session {session.session_id}: {', '.join(denied)}"
                )

        domain_allowlist = (
            session.email_draft_domain_allowlist or effective_policy.email_draft_domain_allowlist
        )
        if domain_allowlist:
            allowlisted_domains = {
                domain.strip().lower().lstrip("@") for domain in domain_allowlist
            }
            denied_domains: list[str] = []
            for recipient in normalized_recipients:
                _, separator, domain = recipient.rpartition("@")
                if not separator or domain not in allowlisted_domains:
                    denied_domains.append(recipient)
            if denied_domains:
                raise PolicyError(
                    f"Draft recipient domains are not allowlisted for session {session.session_id}: "
                    f"{', '.join(denied_domains)}"
                )

    def effective_policy_for_session(self, session: SessionManifest) -> EffectivePolicyConfig:
        return self.policy_config.resolve_for_session_manifest(session)

    def effective_policy_for_session_id(self, session_id: str) -> EffectivePolicyConfig:
        return self.policy_config.resolve_for_session_id(session_id)

    def _approval_notification_target_ids_for_session(
        self,
        *,
        session: SessionManifest,
        effective_policy: EffectivePolicyConfig,
        now: datetime,
    ) -> list[str]:
        target_ids = list(effective_policy.approval_notification_target_ids)
        target_ids.extend(session.approval_notification_target_ids)

        context = _TOOL_APPROVAL_CONTEXT.get()
        if (
            self.tool_approval_leases is not None
            and context is not None
            and context.session_id == session.session_id
            and context.workflow_run_id
            and context.lease_id
        ):
            lease = self.tool_approval_leases.get_lease(context.lease_id)
            if (
                lease is not None
                and lease.session_id == session.session_id
                and lease.workflow_run_id == context.workflow_run_id
                and lease.revoked_at is None
                and lease.expires_at > now
            ):
                target_ids.extend(lease.approval_notification_target_ids)

        return normalize_approval_notification_target_ids(
            target_ids,
            field_name="approval_notification_target_ids",
        )

    def create_approval(
        self,
        session_id: str,
        action_type: str,
        payload: dict[str, Any],
        human_summary: str,
        *,
        tool_name: str | None = None,
        intended_effect: str | None = None,
        approval_settings: ApprovalGrantSettings | None = None,
    ) -> ApprovalRequest:
        request_id = f"apr_{digest_payload({'session_id': session_id, **payload})[:16]}"
        now = datetime.now(UTC)
        existing = self.approval_store.get(request_id)
        if existing is not None:
            session = self.session_store.get_session(existing.session_id)
            if session is not None:
                effective_policy = self.effective_policy_for_session(session)
                redacted_payload = _redact_nested_payload(
                    effective_policy.redacted_keys, existing.payload
                )
                if redacted_payload != existing.payload:
                    existing.payload = redacted_payload
                    self.approval_store.put(existing.request_id, existing)
            if _approval_request_is_reusable(existing, now):
                return existing

        session = self.get_session(session_id)
        effective_policy = self.effective_policy_for_session(session)
        if tool_name:
            tool_enum = ToolName._value2member_map_.get(str(tool_name).strip())
            if tool_enum is not None:
                self.require_tool_not_denied(session, tool_enum)
        redacted_payload = _redact_nested_payload(effective_policy.redacted_keys, payload)
        notification_target_ids = self._approval_notification_target_ids_for_session(
            session=session,
            effective_policy=effective_policy,
            now=now,
        )
        notification_status = (
            ApprovalNotificationStatus.QUEUED
            if notification_target_ids
            else ApprovalNotificationStatus.DISABLED
        )
        if existing is None:
            ttl_seconds = (
                approval_settings.ttl_seconds
                if approval_settings is not None
                else effective_policy.approval_ttl_seconds
            )
            request = ApprovalRequest(
                request_id=request_id,
                session_id=session_id,
                action_type=action_type,
                payload_digest=digest_payload(payload),
                human_summary=human_summary,
                payload=redacted_payload,
                directory_path=session.directory_path,
                vm_id=session.vm_id,
                tool_name=tool_name or action_type,
                intended_effect=intended_effect,
                approval_settings=approval_settings,
                notification_status=notification_status,
                notification_target_ids=notification_target_ids,
                notification_attempted_at=now if notification_target_ids else None,
                expires_at=now + timedelta(seconds=ttl_seconds),
            )
        else:
            ttl_seconds = (
                approval_settings.ttl_seconds
                if approval_settings is not None
                else effective_policy.approval_ttl_seconds
            )
            request = existing
            request.session_id = session_id
            request.action_type = action_type
            request.payload_digest = digest_payload(payload)
            request.human_summary = human_summary
            request.payload = redacted_payload
            request.directory_path = session.directory_path
            request.vm_id = session.vm_id
            request.tool_name = tool_name or action_type
            request.intended_effect = intended_effect
            request.approval_settings = approval_settings
            request.used_count = 0
            request.last_used_at = None
            request.status = ApprovalStatus.PENDING
            request.notification_status = notification_status
            request.notification_target_ids = notification_target_ids
            request.notification_attempted_at = now if notification_target_ids else None
            request.notification_error = None
            request.notification_deliveries = []
            request.created_at = now
            request.expires_at = now + timedelta(seconds=ttl_seconds)
            request.approver = None
            request.decision_at = None
            request.decision_reason = None
        self.approval_store.put(request.request_id, request)
        if self.approval_notification_dispatcher is not None:
            self.approval_notification_dispatcher.dispatch(request)
        return request

    def get_approval(self, request_id: str) -> ApprovalRequest:
        request = self.approval_store.get(request_id)
        if request is None:
            raise PolicyError(f"Unknown approval request: {request_id}")
        session = self.session_store.get_session(request.session_id)
        if session is not None:
            redacted_payload = _redact_nested_payload(
                self.effective_policy_for_session(session).redacted_keys,
                request.payload,
            )
            if redacted_payload != request.payload:
                request.payload = redacted_payload
                self.approval_store.put(request.request_id, request)
        if request.status in {
            ApprovalStatus.PENDING,
            ApprovalStatus.APPROVED,
        } and request.expires_at <= datetime.now(UTC):
            request.status = ApprovalStatus.EXPIRED
            self.approval_store.put(request.request_id, request)
        return request

    def require_approved(
        self, request_id: str, payload: dict[str, Any], *, consume: bool = False
    ) -> ApprovalRequest:
        now = datetime.now(UTC)
        error: PolicyError | None = None

        def _validate_and_maybe_consume(items: dict[str, ApprovalRequest]) -> ApprovalRequest:
            nonlocal error
            request = items.get(request_id)
            if request is None:
                error = _approval_rejection_error(
                    request_id=request_id,
                    rejection_kind="missing",
                    message=f"Unknown approval request: {request_id}",
                    status="missing",
                )
                return ApprovalRequest(
                    request_id=request_id,
                    session_id="unknown",
                    action_type="unknown",
                    payload_digest="",
                    human_summary="unknown",
                    expires_at=now,
                )
            self._refresh_approval_redaction(request)
            request_session = self.session_store.get_session(request.session_id)
            redacted_keys = (
                set(self.effective_policy_for_session(request_session).redacted_keys)
                if request_session is not None
                else set()
            )
            if request.expires_at <= now:
                request.status = ApprovalStatus.EXPIRED
                items[request_id] = request
                error = _approval_rejection_error(
                    request_id=request_id,
                    rejection_kind="expired",
                    message=f"Approval request {request_id} has expired",
                    request=request,
                    attempted_payload=payload,
                    status=request.status.value,
                    redacted_keys=redacted_keys,
                )
                return request
            if request.status != ApprovalStatus.APPROVED:
                error = _approval_rejection_error(
                    request_id=request_id,
                    rejection_kind=_approval_rejection_kind_for_status(request.status),
                    message=f"Approval request {request_id} is not approved",
                    request=request,
                    attempted_payload=payload,
                    status=request.status.value,
                    redacted_keys=redacted_keys,
                )
                return request
            settings = _approval_settings_for_request(request)
            payload_mode = settings.payload_mode if settings is not None else ApprovalPayloadMode.EXACT
            if payload_mode == ApprovalPayloadMode.EXACT and request.payload_digest != digest_payload(payload):
                error = _approval_rejection_error(
                    request_id=request_id,
                    rejection_kind="payload_mismatch",
                    message=f"Approval payload mismatch for request {request_id}",
                    request=request,
                    attempted_payload=payload,
                    status=request.status.value,
                    redacted_keys=redacted_keys,
                )
                return request
            if request.action_type == "mcp_tool_invocation":
                expected_session_id = str(payload.get("session_id") or "").strip()
                if expected_session_id and request.session_id != expected_session_id:
                    error = _approval_rejection_error(
                        request_id=request_id,
                        rejection_kind="wrong_session",
                        message=(
                            f"Approval request {request_id} does not belong to session "
                            f"{expected_session_id}"
                        ),
                        request=request,
                        attempted_payload=payload,
                        status=request.status.value,
                        redacted_keys=redacted_keys,
                    )
                    return request
                expected_tool_name = str(payload.get("tool_name") or "").strip()
                actual_tool_name = str(request.tool_name or request.action_type).strip()
                if expected_tool_name and actual_tool_name != expected_tool_name:
                    error = _approval_rejection_error(
                        request_id=request_id,
                        rejection_kind="wrong_tool",
                        message=f"Approval request {request_id} is not for tool {expected_tool_name}",
                        request=request,
                        attempted_payload=payload,
                        status=request.status.value,
                        redacted_keys=redacted_keys,
                    )
                    return request
                if request_session is None:
                    error = _approval_rejection_error(
                        request_id=request_id,
                        rejection_kind="unknown_session",
                        message=f"Unknown session: {request.session_id}",
                        request=request,
                        attempted_payload=payload,
                        status=request.status.value,
                        redacted_keys=redacted_keys,
                    )
                    return request
                tool = ToolName._value2member_map_.get(str(request.tool_name or "").strip())
                if tool is not None:
                    try:
                        self.require_tool_not_denied(request_session, tool)
                    except PolicyError as exc:
                        error = _approval_rejection_error(
                            request_id=request_id,
                            rejection_kind="tool_denied",
                            message=str(exc),
                            request=request,
                            attempted_payload=payload,
                            status=request.status.value,
                            redacted_keys=redacted_keys,
                        )
                        return request
            if consume and settings is not None and settings.max_uses is not None:
                if request.used_count >= settings.max_uses:
                    request.status = ApprovalStatus.EXHAUSTED
                    items[request_id] = request
                    error = _approval_rejection_error(
                        request_id=request_id,
                        rejection_kind="exhausted",
                        message=f"Approval request {request_id} has no remaining uses",
                        request=request,
                        attempted_payload=payload,
                        status=request.status.value,
                        redacted_keys=redacted_keys,
                    )
                    return request
                request.used_count += 1
                request.last_used_at = now
                if request.used_count >= settings.max_uses:
                    request.status = ApprovalStatus.EXHAUSTED
                items[request_id] = request
            return request

        request = self.approval_store.update_all(_validate_and_maybe_consume)
        if error is not None:
            raise error
        return request

    def _refresh_approval_redaction(self, request: ApprovalRequest) -> None:
        session = self.session_store.get_session(request.session_id)
        if session is None:
            return
        redacted_payload = _redact_nested_payload(
            self.effective_policy_for_session(session).redacted_keys,
            request.payload,
        )
        if redacted_payload != request.payload:
            request.payload = redacted_payload
