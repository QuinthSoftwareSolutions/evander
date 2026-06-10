from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_sandbox.config import GatewayConfig
from agent_sandbox.directory_config import DirectoryConfigStore
from agent_sandbox.directories import DirectoryStore
from agent_sandbox.effects.providers.base import EffectRuntimeContext
from agent_sandbox.effects.providers.session_approve_mcp_tools import (
    SessionApproveMcpToolsForRunEffectProvider,
)
from agent_sandbox.events.provider_models import SessionApproveMcpToolsForRunEffectDefinition
from agent_sandbox.models import SessionManifest, ToolName
from agent_sandbox.messaging.targets import (
    SqliteCommunicationTargetsRepository,
    communication_targets_db_path,
)
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
    directory_store = DirectoryStore(config.directories_store_path)
    directory_store.ensure_root()
    admin_service = SimpleNamespace(
        runtime=SimpleNamespace(
            config=config,
            directories=directory_store,
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


def _write_notification_targets(tmp_path: Path, targets: list[dict[str, object]]) -> None:
    repository = SqliteCommunicationTargetsRepository(communication_targets_db_path(tmp_path))
    repository.write_overlay("root", {"targets": targets})


def _write_root_policy(tmp_path: Path, payload: dict[str, object]) -> None:
    config = GatewayConfig(runtime_dir=tmp_path)
    directory_store = DirectoryStore(config.directories_store_path)
    directory_store.ensure_root()
    DirectoryConfigStore(directory_store, runtime_dir=config.runtime_dir).write(
        "root",
        domain="policy",
        payload=payload,
    )


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


def test_approve_mcp_tools_for_run_rejects_directory_denied_tool(
    tmp_path: Path,
) -> None:
    _write_root_policy(
        tmp_path,
        {"mcp_tool_policies": {ToolName.RUN_SCRIPT.value: {"denied": True}}},
    )
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
            vm_id="vm-1",
            allowed_tools=[],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )

    with pytest.raises(ValueError, match="denied by directory policy"):
        provider.execute(execution_context={"workflow_run_id": "run-1"})


def test_approve_mcp_tools_for_run_clamps_lease_with_directory_hard_limits(
    tmp_path: Path,
) -> None:
    _write_root_policy(
        tmp_path,
        {
            "mcp_tool_policies": {
                ToolName.RUN_SCRIPT.value: {
                    "max_ttl_seconds": 120,
                    "max_uses": 1,
                }
            }
        },
    )
    provider, sessions, leases = _build_provider(
        tmp_path,
        SessionApproveMcpToolsForRunEffectDefinition(
            id="approve-tools",
            display_name="Approve tools",
            type="effect:session:approve-mcp-tools-for-run",
            session_id="session-1",
            tools=["run_script"],
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

    started_at = datetime.now(UTC)
    output = provider.execute(execution_context={"workflow_run_id": "run-1"})

    lease = leases.get_lease(output["lease_id"])
    assert lease is not None
    assert lease.expires_at <= started_at + timedelta(seconds=121)
    assert lease.approval_settings[ToolName.RUN_SCRIPT.value].ttl_seconds == 120
    assert lease.approval_settings[ToolName.RUN_SCRIPT.value].max_uses == 1
    assert output["approval_settings"][ToolName.RUN_SCRIPT.value]["max_uses"] == 1


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


def test_approve_mcp_tools_for_run_persists_notification_target_ids(
    tmp_path: Path,
) -> None:
    _write_notification_targets(
        tmp_path,
        [
            {
                "id": "ops-approvals",
                "provider": "teams",
                "kind": "teams",
                "address": "team-a/channel-1",
                "policy": {"capabilities": ["notify"]},
            }
        ],
    )
    provider, sessions, leases = _build_provider(
        tmp_path,
        SessionApproveMcpToolsForRunEffectDefinition(
            id="approve-tools",
            display_name="Approve tools",
            type="effect:session:approve-mcp-tools-for-run",
            session_id="session-1",
            tools=["run_script"],
            approval_notification_target_ids=[" OPS-Approvals ", "ops-approvals"],
        ),
    )
    sessions.upsert_session(
        SessionManifest(
            session_id="session-1",
            allowed_tools=[],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )

    output = provider.execute(execution_context={"workflow_run_id": "run-1"})
    lease = leases.get_lease(output["lease_id"])

    assert output["approval_notification_target_ids"] == ["ops-approvals"]
    assert lease is not None
    assert lease.approval_notification_target_ids == ["ops-approvals"]


def test_approve_mcp_tools_for_run_rejects_unknown_notification_target(
    tmp_path: Path,
) -> None:
    provider, sessions, _leases = _build_provider(
        tmp_path,
        SessionApproveMcpToolsForRunEffectDefinition(
            id="approve-tools",
            display_name="Approve tools",
            type="effect:session:approve-mcp-tools-for-run",
            session_id="session-1",
            tools=["run_script"],
            approval_notification_target_ids=["missing-target"],
        ),
    )
    sessions.upsert_session(
        SessionManifest(
            session_id="session-1",
            allowed_tools=[],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )

    with pytest.raises(ValueError, match="unknown communication target\\(s\\): missing-target"):
        provider.execute(execution_context={"workflow_run_id": "run-1"})


def test_approve_mcp_tools_for_run_rejects_non_notify_notification_target(
    tmp_path: Path,
) -> None:
    _write_notification_targets(
        tmp_path,
        [
            {
                "id": "ops-read",
                "provider": "discord",
                "kind": "discord",
                "address": "123456789012345678",
                "policy": {"capabilities": ["read"]},
            }
        ],
    )
    provider, sessions, _leases = _build_provider(
        tmp_path,
        SessionApproveMcpToolsForRunEffectDefinition(
            id="approve-tools",
            display_name="Approve tools",
            type="effect:session:approve-mcp-tools-for-run",
            session_id="session-1",
            tools=["run_script"],
            approval_notification_target_ids=["ops-read"],
        ),
    )
    sessions.upsert_session(
        SessionManifest(
            session_id="session-1",
            allowed_tools=[],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )

    with pytest.raises(ValueError, match="without notify capability: ops-read"):
        provider.execute(execution_context={"workflow_run_id": "run-1"})


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
