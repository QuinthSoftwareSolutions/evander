from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_sandbox.config import GatewayConfig
from agent_sandbox.effects.providers.base import EffectRuntimeContext
from agent_sandbox.effects.providers.session_approve_mcp_tools import (
    SessionApproveMcpToolsForRunEffectProvider,
)
from agent_sandbox.events.provider_models import SessionApproveMcpToolsForRunEffectDefinition
from agent_sandbox.models import SessionManifest, ToolName
from agent_sandbox.repositories.session_manifests import JsonSessionManifestRepository
from agent_sandbox.repositories.session_tool_approval_leases import (
    JsonSessionToolApprovalLeaseRepository,
)


class _ProviderConfigResolver:
    def __init__(self, config: GatewayConfig) -> None:
        self.config = config

    def resolve_for_directory(self, directory_path: str | None) -> GatewayConfig:
        del directory_path
        return self.config


def _build_provider(
    tmp_path: Path,
    effect: SessionApproveMcpToolsForRunEffectDefinition,
) -> tuple[
    SessionApproveMcpToolsForRunEffectProvider,
    JsonSessionManifestRepository,
    JsonSessionToolApprovalLeaseRepository,
]:
    config = GatewayConfig(runtime_dir=tmp_path)
    session_repository = JsonSessionManifestRepository(config.session_store_path)
    lease_repository = JsonSessionToolApprovalLeaseRepository.from_runtime_dir(tmp_path)
    admin_service = SimpleNamespace(
        runtime=SimpleNamespace(
            session_repository=session_repository,
            tool_approval_leases=lease_repository,
            provider_config=_ProviderConfigResolver(config),
        )
    )
    provider = SessionApproveMcpToolsForRunEffectProvider()
    provider.initialize(
        effect_definition=effect,
        runtime_context=EffectRuntimeContext(
            resolved_config=config,
            admin_service=admin_service,
            env={},
        ),
    )
    return provider, session_repository, lease_repository


def test_approve_mcp_tools_for_run_creates_scoped_lease_without_manifest_mutation(
    tmp_path: Path,
) -> None:
    provider, sessions, leases = _build_provider(
        tmp_path,
        SessionApproveMcpToolsForRunEffectDefinition(
            id="approve-tools",
            display_name="Approve tools",
            type="effect:session:approve-mcp-tools-for-run",
            session_id="session-1",
            tools=["run_script", "get_agent_status"],
            expires_in_seconds=600,
        ),
    )
    sessions.upsert_session(
        SessionManifest(
            session_id="session-1",
            vm_id="vm-1",
            allowed_tools=[],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )

    output = provider.execute(
        execution_context={
            "workflow_run_id": "run-1",
            "execution_id": "effect-exec-1",
        }
    )

    lease = leases.get_lease(output["lease_id"])
    manifest = sessions.get_session("session-1")
    assert output["success"] is True
    assert output["mcp_tool_approval_lease_id"] == output["lease_id"]
    assert output["workflow_run_id"] == "run-1"
    assert output["tools"] == ["run_script", "get_agent_status"]
    assert lease is not None
    assert lease.session_id == "session-1"
    assert lease.workflow_run_id == "run-1"
    assert leases.is_tool_approved(
        lease_id=lease.lease_id,
        session_id="session-1",
        workflow_run_id="run-1",
        tool_name=ToolName.RUN_SCRIPT,
    )
    assert manifest is not None
    assert manifest.allowed_tools == []


def test_approve_mcp_tools_for_run_resolves_vm_name_and_caps_expiry(
    tmp_path: Path,
) -> None:
    provider, sessions, leases = _build_provider(
        tmp_path,
        SessionApproveMcpToolsForRunEffectDefinition(
            id="approve-tools",
            display_name="Approve tools",
            type="effect:session:approve-mcp-tools-for-run",
            vm_name="vm-1",
            tools="run_script",
            expires_in_seconds=3600,
        ),
    )
    session_expires_at = datetime.now(UTC) + timedelta(minutes=5)
    sessions.upsert_session(
        SessionManifest(
            session_id="session-1",
            vm_id="vm-1",
            allowed_tools=[],
            expires_at=session_expires_at,
        )
    )

    output = provider.execute(execution_context={"workflow_run_id": "run-1"})

    lease = leases.get_lease(output["lease_id"])
    assert output["session_id"] == "session-1"
    assert lease is not None
    assert lease.expires_at == session_expires_at


def test_approve_mcp_tools_for_run_requires_workflow_run_id(tmp_path: Path) -> None:
    provider, sessions, _leases = _build_provider(
        tmp_path,
        SessionApproveMcpToolsForRunEffectDefinition(
            id="approve-tools",
            display_name="Approve tools",
            type="effect:session:approve-mcp-tools-for-run",
            session_id="session-1",
            tools=["run_script"],
        ),
    )
    sessions.upsert_session(
        SessionManifest(
            session_id="session-1",
            allowed_tools=[],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )

    with pytest.raises(ValueError, match="workflow_run_id"):
        provider.execute(execution_context={})
