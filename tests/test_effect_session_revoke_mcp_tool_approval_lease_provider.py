from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_sandbox.config import GatewayConfig
from agent_sandbox.effects.providers.base import EffectRuntimeContext
from agent_sandbox.effects.providers.session_revoke_mcp_tool_approval_lease import (
    SessionRevokeMcpToolApprovalLeaseEffectProvider,
)
from agent_sandbox.events.provider_models import (
    SessionRevokeMcpToolApprovalLeaseEffectDefinition,
)
from agent_sandbox.models import ToolName
from agent_sandbox.repositories.session_tool_approval_leases import (
    JsonSessionToolApprovalLeaseRepository,
)


class _LeaseStoreProxy:
    def __init__(self, repository: JsonSessionToolApprovalLeaseRepository) -> None:
        self.repository = repository
        self.revoke_calls = 0

    def get_lease(self, lease_id: str):
        return self.repository.get_lease(lease_id)

    def revoke_lease(self, lease_id: str, *, revoked_at: datetime | None = None) -> bool:
        self.revoke_calls += 1
        return self.repository.revoke_lease(lease_id, revoked_at=revoked_at)


def _build_provider(
    tmp_path: Path,
    effect: SessionRevokeMcpToolApprovalLeaseEffectDefinition,
    lease_store: _LeaseStoreProxy | None = None,
) -> tuple[SessionRevokeMcpToolApprovalLeaseEffectProvider, JsonSessionToolApprovalLeaseRepository, _LeaseStoreProxy]:
    config = GatewayConfig(runtime_dir=tmp_path)
    repository = JsonSessionToolApprovalLeaseRepository.from_runtime_dir(tmp_path)
    lease_store = lease_store or _LeaseStoreProxy(repository)
    admin_service = SimpleNamespace(
        runtime=SimpleNamespace(
            tool_approval_leases=lease_store,
            config=config,
        )
    )
    provider = SessionRevokeMcpToolApprovalLeaseEffectProvider()
    provider.initialize(
        effect_definition=effect,
        runtime_context=EffectRuntimeContext(
            resolved_config=config,
            admin_service=admin_service,
            env={},
        ),
    )
    return provider, repository, lease_store


def _seed_lease(
    repository: JsonSessionToolApprovalLeaseRepository,
    *,
    session_id: str = "session-1",
    workflow_run_id: str = "run-1",
    revoked_at: datetime | None = None,
):
    lease = repository.upsert_lease(
        session_id=session_id,
        workflow_run_id=workflow_run_id,
        tools=[ToolName.RUN_SCRIPT, ToolName.GET_AGENT_STATUS],
        expires_at=datetime.now(UTC) + timedelta(minutes=10),
    )
    if revoked_at is not None:
        repository.revoke_lease(lease.lease_id, revoked_at=revoked_at)
    return lease


def test_session_revoke_mcp_tool_approval_lease_revokes_active_lease(
    tmp_path: Path,
) -> None:
    provider, repository, lease_store = _build_provider(
        tmp_path,
        SessionRevokeMcpToolApprovalLeaseEffectDefinition(
            id="revoke-lease",
            display_name="Revoke lease",
            type="effect:session:revoke-mcp-tool-approval-lease",
            mcp_tool_approval_lease_id="placeholder",
            session_id="session-1",
            workflow_run_id="run-1",
        ),
    )
    lease = _seed_lease(repository)
    provider.effect_definition = provider.effect_definition.model_copy(
        update={"mcp_tool_approval_lease_id": lease.lease_id}
    )

    output = provider.execute()

    revoked = repository.get_lease(lease.lease_id)
    assert output["success"] is True
    assert output["revoked"] is True
    assert output["already_revoked"] is False
    assert output["lease_id"] == lease.lease_id
    assert output["mcp_tool_approval_lease_id"] == lease.lease_id
    assert output["session_id"] == "session-1"
    assert output["workflow_run_id"] == "run-1"
    assert output["tools"] == [ToolName.RUN_SCRIPT.value, ToolName.GET_AGENT_STATUS.value]
    assert output["revoked_at"] is not None
    assert lease_store.revoke_calls == 1
    assert revoked is not None
    assert revoked.revoked_at is not None
    assert output["revoked_at"] == revoked.revoked_at.astimezone(UTC).isoformat()


@pytest.mark.parametrize("field_name", ["mcp_tool_approval_lease_id", "lease_id"])
def test_session_revoke_mcp_tool_approval_lease_accepts_both_input_aliases(
    tmp_path: Path,
    field_name: str,
) -> None:
    effect_kwargs = {
        "id": "revoke-lease",
        "display_name": "Revoke lease",
        "type": "effect:session:revoke-mcp-tool-approval-lease",
        "session_id": "session-1",
        "workflow_run_id": "run-1",
        field_name: "placeholder",
    }
    provider, repository, lease_store = _build_provider(
        tmp_path,
        SessionRevokeMcpToolApprovalLeaseEffectDefinition(**effect_kwargs),
    )
    lease = _seed_lease(repository)
    provider.effect_definition = provider.effect_definition.model_copy(
        update={field_name: lease.lease_id}
    )

    output = provider.execute()

    assert output["lease_id"] == lease.lease_id
    assert output["mcp_tool_approval_lease_id"] == lease.lease_id
    assert lease_store.revoke_calls == 1


def test_session_revoke_mcp_tool_approval_lease_is_idempotent_when_already_revoked(
    tmp_path: Path,
) -> None:
    original_revoked_at = datetime.now(UTC) - timedelta(minutes=1)
    provider, repository, lease_store = _build_provider(
        tmp_path,
        SessionRevokeMcpToolApprovalLeaseEffectDefinition(
            id="revoke-lease",
            display_name="Revoke lease",
            type="effect:session:revoke-mcp-tool-approval-lease",
            lease_id="placeholder",
        ),
    )
    lease = _seed_lease(repository, revoked_at=original_revoked_at)
    provider.effect_definition = provider.effect_definition.model_copy(
        update={"lease_id": lease.lease_id}
    )

    output = provider.execute()

    revoked = repository.get_lease(lease.lease_id)
    assert output["already_revoked"] is True
    assert output["revoked"] is True
    assert output["revoked_at"] == original_revoked_at.astimezone(UTC).isoformat()
    assert lease_store.revoke_calls == 0
    assert revoked is not None
    assert revoked.revoked_at == original_revoked_at.astimezone(UTC)


def test_session_revoke_mcp_tool_approval_lease_rejects_unknown_lease(
    tmp_path: Path,
) -> None:
    provider, _repository, _lease_store = _build_provider(
        tmp_path,
        SessionRevokeMcpToolApprovalLeaseEffectDefinition(
            id="revoke-lease",
            display_name="Revoke lease",
            type="effect:session:revoke-mcp-tool-approval-lease",
            mcp_tool_approval_lease_id="missing-lease",
        ),
    )

    with pytest.raises(ValueError, match="Unknown MCP tool approval lease"):
        provider.execute()


def test_session_revoke_mcp_tool_approval_lease_rejects_conflicting_aliases(
    tmp_path: Path,
) -> None:
    provider, _repository, _lease_store = _build_provider(
        tmp_path,
        SessionRevokeMcpToolApprovalLeaseEffectDefinition(
            id="revoke-lease",
            display_name="Revoke lease",
            type="effect:session:revoke-mcp-tool-approval-lease",
            mcp_tool_approval_lease_id="lease-a",
            lease_id="lease-b",
        ),
    )

    with pytest.raises(ValueError, match="mcp_tool_approval_lease_id and lease_id must match"):
        provider.execute()


@pytest.mark.parametrize(
    ("field_name", "guard_value", "message"),
    [
        ("session_id", "other-session", "session_id does not match"),
        ("workflow_run_id", "other-run", "workflow_run_id does not match"),
    ],
)
def test_session_revoke_mcp_tool_approval_lease_rejects_guard_mismatch(
    tmp_path: Path,
    field_name: str,
    guard_value: str,
    message: str,
) -> None:
    provider, repository, lease_store = _build_provider(
        tmp_path,
        SessionRevokeMcpToolApprovalLeaseEffectDefinition(
            id="revoke-lease",
            display_name="Revoke lease",
            type="effect:session:revoke-mcp-tool-approval-lease",
            mcp_tool_approval_lease_id="placeholder",
            session_id="session-1",
            workflow_run_id="run-1",
        ),
    )
    lease = _seed_lease(repository)
    provider.effect_definition = provider.effect_definition.model_copy(
        update={"mcp_tool_approval_lease_id": lease.lease_id, field_name: guard_value}
    )

    with pytest.raises(ValueError, match=message):
        provider.execute()

    assert lease_store.revoke_calls == 0
    assert repository.get_lease(lease.lease_id).revoked_at is None
