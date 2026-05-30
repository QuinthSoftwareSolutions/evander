from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC
from typing import Any

from agent_sandbox.events.provider_models import (
    SessionRevokeMcpToolApprovalLeaseEffectDefinition,
)
from agent_sandbox.models import SessionToolApprovalLease, ToolName
from agent_sandbox.repositories.session_tool_approval_leases import (
    JsonSessionToolApprovalLeaseRepository,
    SessionToolApprovalLeaseRepository,
)

from .base import EffectProviderBase, EffectRuntimeContext


class SessionRevokeMcpToolApprovalLeaseEffectProvider(EffectProviderBase):
    def execute(
        self,
        *,
        resolved_config: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        effect = self._effect()
        payload: dict[str, Any] = {
            "mcp_tool_approval_lease_id": effect.mcp_tool_approval_lease_id,
            "lease_id": effect.lease_id,
            "session_id": effect.session_id,
            "workflow_run_id": effect.workflow_run_id,
        }
        if resolved_config:
            payload.update(dict(resolved_config))

        normalized_lease_id = self._lease_id(payload)
        store = self._lease_store()
        lease = store.get_lease(normalized_lease_id)
        if lease is None:
            raise ValueError(f"Unknown MCP tool approval lease: {normalized_lease_id}")

        self._validate_guard(payload, lease)

        already_revoked = lease.revoked_at is not None
        if not already_revoked:
            if not store.revoke_lease(normalized_lease_id):
                raise RuntimeError(
                    f"Failed to revoke MCP tool approval lease: {normalized_lease_id}"
                )
            lease = store.get_lease(normalized_lease_id)
            if lease is None:
                raise RuntimeError(
                    f"MCP tool approval lease disappeared after revocation: {normalized_lease_id}"
                )

        return self._result_payload(lease, already_revoked=already_revoked)

    def _effect(self) -> SessionRevokeMcpToolApprovalLeaseEffectDefinition:
        if not isinstance(
            self.effect_definition, SessionRevokeMcpToolApprovalLeaseEffectDefinition
        ):
            raise RuntimeError(
                "SessionRevokeMcpToolApprovalLeaseEffectProvider initialized with a non-"
                "session-revoke-mcp-tool-approval-lease effect definition"
            )
        return self.effect_definition

    def _lease_id(self, payload: Mapping[str, Any]) -> str:
        preferred = self._normalize_optional_string(payload.get("mcp_tool_approval_lease_id"))
        alias = self._normalize_optional_string(payload.get("lease_id"))
        if preferred is not None and alias is not None and preferred != alias:
            raise ValueError(
                "effect:session:revoke-mcp-tool-approval-lease mcp_tool_approval_lease_id and "
                "lease_id must match when both are provided"
            )
        normalized = preferred or alias
        if normalized is None:
            raise ValueError(
                "effect:session:revoke-mcp-tool-approval-lease requires "
                "mcp_tool_approval_lease_id or lease_id"
            )
        return normalized

    def _validate_guard(self, payload: Mapping[str, Any], lease: SessionToolApprovalLease) -> None:
        session_id = self._normalize_optional_string(payload.get("session_id"))
        if session_id is not None and session_id != lease.session_id:
            raise ValueError(
                "effect:session:revoke-mcp-tool-approval-lease session_id does not match "
                f"lease session_id {lease.session_id!r}"
            )

        workflow_run_id = self._normalize_optional_string(payload.get("workflow_run_id"))
        if workflow_run_id is not None and workflow_run_id != lease.workflow_run_id:
            raise ValueError(
                "effect:session:revoke-mcp-tool-approval-lease workflow_run_id does not match "
                f"lease workflow_run_id {lease.workflow_run_id!r}"
            )

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
                "effect:session:revoke-mcp-tool-approval-lease requires a tool approval lease store"
            )
        return JsonSessionToolApprovalLeaseRepository.from_runtime_dir(runtime_dir)

    def _context(self) -> EffectRuntimeContext:
        if self.runtime_context is None:
            raise RuntimeError(
                "SessionRevokeMcpToolApprovalLeaseEffectProvider has no runtime context"
            )
        return self.runtime_context

    @staticmethod
    def _normalize_optional_string(value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @staticmethod
    def _result_payload(
        lease: SessionToolApprovalLease,
        *,
        already_revoked: bool,
    ) -> dict[str, Any]:
        revoked_at = lease.revoked_at
        return {
            "success": True,
            "lease_id": lease.lease_id,
            "mcp_tool_approval_lease_id": lease.lease_id,
            "session_id": lease.session_id,
            "workflow_run_id": lease.workflow_run_id,
            "tools": [tool.value if isinstance(tool, ToolName) else str(tool) for tool in lease.tools],
            "revoked": True,
            "already_revoked": already_revoked,
            "revoked_at": revoked_at.astimezone(UTC).isoformat() if revoked_at is not None else None,
        }
