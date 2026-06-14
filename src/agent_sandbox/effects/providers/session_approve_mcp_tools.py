from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from agent_sandbox.approval_notification_targets import (
    resolve_approval_notification_target_ids_for_directory,
)
from agent_sandbox.models import SessionToolApprovalLease, ToolName, normalize_tool_names
from agent_sandbox.policy import mandatory_approval_required_for_tool
from agent_sandbox.policy_config import PolicyConfigResolver
from agent_sandbox.repositories.session_tool_approval_leases import (
    JsonSessionToolApprovalLeaseRepository,
    SessionToolApprovalLeaseRepository,
)

from .base import EffectProviderBase
from .vm_targeting import read_all_sessions


DEFAULT_TOOL_APPROVAL_LEASE_SECONDS = 24 * 60 * 60


class SessionApproveMcpToolsForRunEffectProvider(EffectProviderBase):
    def execute(
        self,
        *,
        resolved_config: Mapping[str, Any] | None = None,
        execution_context: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        effect = self.effect_definition
        payload: dict[str, Any] = {}
        for field_name in (
            "target",
            "session_id",
            "vm_name",
            "tools",
            "expires_in_seconds",
            "approval_notification_target_ids",
        ):
            if effect is not None and hasattr(effect, field_name):
                payload[field_name] = getattr(effect, field_name)
        if resolved_config:
            payload.update(dict(resolved_config))

        session_id, session = self._resolve_session(payload)
        tools = normalize_tool_names(payload.get("tools"), field_name="tools")
        if not tools:
            raise ValueError("effect:session:approve-mcp-tools-for-run requires at least one tool")
        workflow_run_id = self._workflow_run_id(execution_context)
        approval_settings = self._approval_settings_for_tools(session=session, tools=tools)
        approval_notification_target_ids = self._approval_notification_target_ids(
            session=session, payload=payload
        )
        expires_at = self._expires_at(
            payload=payload,
            session=session,
            approval_settings=approval_settings,
        )
        store = self._lease_store()
        lease = store.upsert_lease(
            session_id=session_id,
            workflow_run_id=workflow_run_id,
            tools=tools,
            expires_at=expires_at,
            effect_execution_id=self._effect_execution_id(execution_context),
            approval_settings=approval_settings,
            approval_notification_target_ids=approval_notification_target_ids,
        )
        return self._result_payload(lease)

    def _resolve_session(self, payload: Mapping[str, Any]) -> tuple[str, Any]:
        context = self._context()
        sessions = read_all_sessions(
            context,
            effect_type="effect:session:approve-mcp-tools-for-run",
        )
        session_id = self._normalize_optional_string(payload.get("session_id"))
        if session_id:
            session = sessions.get(session_id)
            if session is None:
                raise ValueError(f"Unknown session: {session_id}")
            return session_id, session

        target = self._normalize_optional_string(payload.get("target"))
        if target and target in sessions:
            return target, sessions[target]

        vm_name = self._normalize_optional_string(payload.get("vm_name")) or target
        if vm_name:
            matches = [
                (candidate_session_id, session)
                for candidate_session_id, session in sessions.items()
                if self._normalize_optional_string(getattr(session, "vm_id", None)) == vm_name
            ]
            if len(matches) == 1:
                return matches[0]
            if len(matches) > 1:
                session_ids = ", ".join(sorted(session_id for session_id, _session in matches))
                raise ValueError(
                    f"VM {vm_name!r} is associated with multiple sessions: {session_ids}"
                )
            if target:
                raise ValueError(f"Unknown session or VM target: {target}")
            raise ValueError(f"Unknown VM session target: {vm_name}")

        raise ValueError(
            "effect:session:approve-mcp-tools-for-run requires target, session_id, or vm_name"
        )

    def _expires_at(
        self,
        *,
        payload: Mapping[str, Any],
        session: Any,
        approval_settings: dict[str, Any],
    ) -> datetime:
        ttl_seconds = self._coerce_positive_int(
            payload.get("expires_in_seconds"),
            field_name="expires_in_seconds",
            default=DEFAULT_TOOL_APPROVAL_LEASE_SECONDS,
        )
        ttl_limits = [
            setting.ttl_seconds
            for setting in approval_settings.values()
            if getattr(setting, "ttl_seconds", None) is not None
        ]
        if ttl_limits:
            ttl_seconds = min(ttl_seconds, min(ttl_limits))
        requested = datetime.now(UTC) + timedelta(seconds=ttl_seconds)
        session_expires_at = getattr(session, "expires_at", None)
        if isinstance(session_expires_at, datetime):
            session_expires_at = (
                session_expires_at.replace(tzinfo=UTC)
                if session_expires_at.tzinfo is None
                else session_expires_at.astimezone(UTC)
            )
            return min(requested, session_expires_at)
        return requested

    def _approval_settings_for_tools(
        self, *, session: Any, tools: list[ToolName]
    ) -> dict[str, Any]:
        policy_resolver = self._policy_resolver()
        effective_policy = policy_resolver.resolve_for_session_manifest(session)
        approval_required_tools = set(effective_policy.approval_required_tools)
        settings: dict[str, Any] = {}
        for tool in tools:
            rule = effective_policy.mcp_tool_policies.get(tool.value)
            if rule is not None and rule.denied:
                raise ValueError(
                    f"Tool {tool.value} is denied by directory policy for session {session.session_id}"
                )
            if (
                mandatory_approval_required_for_tool(tool)
                or tool in approval_required_tools
                or (rule is not None and rule.require_approval)
            ):
                raise ValueError(
                    f"Tool {tool.value} requires per-invocation approval in this directory"
                )
            if rule is None:
                continue
            ttl_seconds = rule.max_ttl_seconds or DEFAULT_TOOL_APPROVAL_LEASE_SECONDS
            max_uses = rule.max_uses
            if rule.require_exact_payload:
                raise ValueError(
                    f"Tool {tool.value} requires exact-payload approval in this directory"
                )
            if rule.max_ttl_seconds is not None or max_uses is not None:
                from agent_sandbox.models import ApprovalGrantSettings

                settings[tool.value] = ApprovalGrantSettings(
                    ttl_seconds=ttl_seconds,
                    max_uses=max_uses,
                    payload_mode="any",
                )
        return settings

    def _approval_notification_target_ids(self, *, session: Any, payload: Mapping[str, Any]) -> list[str]:
        target_ids = payload.get("approval_notification_target_ids")
        if target_ids is None:
            return []
        context = self._context()
        runtime = getattr(context.admin_service, "runtime", None)
        if runtime is None:
            raise RuntimeError(
                "effect:session:approve-mcp-tools-for-run requires runtime for notification target validation"
            )
        runtime_dir = getattr(getattr(runtime, "config", None), "runtime_dir", None)
        directory_store = getattr(runtime, "directories", None)
        if runtime_dir is None or directory_store is None:
            raise RuntimeError(
                "effect:session:approve-mcp-tools-for-run requires directory configuration for notification target validation"
            )
        return resolve_approval_notification_target_ids_for_directory(
            runtime_dir=runtime_dir,
            directory_store=directory_store,
            directory_path=getattr(session, "directory_path", "root"),
            target_ids=target_ids,
        )

    def _policy_resolver(self) -> PolicyConfigResolver:
        context = self._context()
        runtime = getattr(context.admin_service, "runtime", None)
        resolver = getattr(runtime, "policy_config", None)
        if resolver is not None:
            return resolver
        config = getattr(runtime, "config", None)
        if config is None:
            provider_resolver = getattr(runtime, "provider_config", None)
            if provider_resolver is None:
                provider_resolver = getattr(context, "provider_config_resolver", None)
            config = getattr(provider_resolver, "config", None)
        if config is None:
            config = getattr(context, "resolved_config", None)
        if config is None:
            raise RuntimeError(
                "effect:session:approve-mcp-tools-for-run requires policy configuration"
            )
        session_store = getattr(runtime, "session_repository", None)
        return PolicyConfigResolver(config, session_store=session_store)

    def _lease_store(self) -> SessionToolApprovalLeaseRepository:
        context = self._context()
        runtime = getattr(context.admin_service, "runtime", None)
        store = getattr(runtime, "tool_approval_leases", None)
        if store is not None:
            return store
        config = getattr(runtime, "config", None)
        if config is None:
            resolver = getattr(context, "provider_config_resolver", None)
            config = getattr(resolver, "config", None)
        runtime_dir = getattr(config, "runtime_dir", None)
        if runtime_dir is None:
            raise RuntimeError(
                "effect:session:approve-mcp-tools-for-run requires a tool approval lease store"
            )
        return JsonSessionToolApprovalLeaseRepository.from_runtime_dir(runtime_dir)

    def _workflow_run_id(self, execution_context: Mapping[str, Any] | None) -> str:
        for key in ("workflow_run_id", "workflow_trace_id", "trace_id"):
            candidate = (
                execution_context.get(key)
                if isinstance(execution_context, Mapping)
                else None
            )
            text = self._normalize_optional_string(candidate)
            if text:
                return text
        raise ValueError(
            "effect:session:approve-mcp-tools-for-run requires workflow_run_id from execution context"
        )

    def _effect_execution_id(self, execution_context: Mapping[str, Any] | None) -> str | None:
        if not isinstance(execution_context, Mapping):
            return None
        for key in ("execution_id", "idempotency_key"):
            text = self._normalize_optional_string(execution_context.get(key))
            if text:
                return text
        return None

    def _context(self):
        if self.runtime_context is None:
            raise RuntimeError("SessionApproveMcpToolsForRunEffectProvider has no runtime context")
        return self.runtime_context

    @staticmethod
    def _normalize_optional_string(value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @staticmethod
    def _coerce_positive_int(value: Any, *, field_name: str, default: int) -> int:
        if value is None or value == "":
            return default
        try:
            number = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{field_name} must be a positive integer") from exc
        if number <= 0:
            raise ValueError(f"{field_name} must be a positive integer")
        return number

    @staticmethod
    def _result_payload(lease: SessionToolApprovalLease) -> dict[str, Any]:
        return {
            "success": True,
            "lease_id": lease.lease_id,
            "mcp_tool_approval_lease_id": lease.lease_id,
            "session_id": lease.session_id,
            "workflow_run_id": lease.workflow_run_id,
            "tools": [tool.value if isinstance(tool, ToolName) else str(tool) for tool in lease.tools],
            "approval_notification_target_ids": list(lease.approval_notification_target_ids),
            "expires_at": lease.expires_at.astimezone(UTC).isoformat(),
            "approval_settings": {
                tool_name: settings.model_dump(mode="json")
                for tool_name, settings in lease.approval_settings.items()
            },
        }
