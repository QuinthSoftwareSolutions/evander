from datetime import UTC, datetime, timedelta

from agent_sandbox.models import ApprovalGrantSettings, ToolName
from agent_sandbox.repositories.session_tool_approval_leases import (
    JsonSessionToolApprovalLeaseRepository,
)


def test_tool_approval_lease_allows_only_matching_session_run_and_tool(tmp_path):
    repository = JsonSessionToolApprovalLeaseRepository.from_runtime_dir(tmp_path)
    expires_at = datetime.now(UTC) + timedelta(minutes=10)

    lease = repository.upsert_lease(
        session_id="session-1",
        workflow_run_id="run-1",
        tools=["run_script", ToolName.GET_AGENT_STATUS],
        expires_at=expires_at,
        effect_execution_id="effect-exec-1",
    )

    assert lease.lease_id.startswith("mcp_tool_lease_")
    assert lease.tools == [ToolName.RUN_SCRIPT, ToolName.GET_AGENT_STATUS]
    assert repository.is_tool_approved(
        lease_id=lease.lease_id,
        session_id="session-1",
        workflow_run_id="run-1",
        tool_name=ToolName.RUN_SCRIPT,
    )
    assert not repository.is_tool_approved(
        lease_id=lease.lease_id,
        session_id="session-2",
        workflow_run_id="run-1",
        tool_name=ToolName.RUN_SCRIPT,
    )
    assert not repository.is_tool_approved(
        lease_id=lease.lease_id,
        session_id="session-1",
        workflow_run_id="run-2",
        tool_name=ToolName.RUN_SCRIPT,
    )
    assert not repository.is_tool_approved(
        lease_id=lease.lease_id,
        session_id="session-1",
        workflow_run_id="run-1",
        tool_name=ToolName.DISCORD_POST_STATUS,
    )


def test_tool_approval_lease_stops_approving_tool_after_max_uses(tmp_path):
    repository = JsonSessionToolApprovalLeaseRepository.from_runtime_dir(tmp_path)
    lease = repository.upsert_lease(
        session_id="session-1",
        workflow_run_id="run-1",
        tools=[ToolName.RUN_SCRIPT],
        expires_at=datetime.now(UTC) + timedelta(minutes=10),
        approval_settings={
            ToolName.RUN_SCRIPT.value: ApprovalGrantSettings(max_uses=1),
        },
    )

    assert repository.is_tool_approved(
        lease_id=lease.lease_id,
        session_id="session-1",
        workflow_run_id="run-1",
        tool_name=ToolName.RUN_SCRIPT,
    )
    assert repository.consume_tool_use(
        lease_id=lease.lease_id,
        session_id="session-1",
        workflow_run_id="run-1",
        tool_name=ToolName.RUN_SCRIPT,
    )
    assert not repository.is_tool_approved(
        lease_id=lease.lease_id,
        session_id="session-1",
        workflow_run_id="run-1",
        tool_name=ToolName.RUN_SCRIPT,
    )
    assert not repository.consume_tool_use(
        lease_id=lease.lease_id,
        session_id="session-1",
        workflow_run_id="run-1",
        tool_name=ToolName.RUN_SCRIPT,
    )


def test_tool_approval_lease_persists_notification_target_ids(tmp_path):
    repository = JsonSessionToolApprovalLeaseRepository.from_runtime_dir(tmp_path)
    lease = repository.upsert_lease(
        session_id="session-1",
        workflow_run_id="run-1",
        tools=[ToolName.RUN_SCRIPT],
        expires_at=datetime.now(UTC) + timedelta(minutes=10),
        approval_notification_target_ids=[" Ops-Primary ", "ops-primary", "ops-backup"],
    )

    assert lease.approval_notification_target_ids == ["ops-primary", "ops-backup"]
    assert repository.get_lease(lease.lease_id).approval_notification_target_ids == [
        "ops-primary",
        "ops-backup",
    ]


def test_tool_approval_lease_loads_missing_notification_target_ids_as_empty_list(tmp_path):
    repository = JsonSessionToolApprovalLeaseRepository.from_runtime_dir(tmp_path)
    repository.path.write_text(
        """
        {
          "lease-1": {
            "lease_id": "lease-1",
            "session_id": "session-1",
            "workflow_run_id": "run-1",
            "tools": ["run_script"],
            "expires_at": "2030-01-01T00:10:00Z"
          }
        }
        """.strip(),
        encoding="utf-8",
    )

    lease = repository.get_lease("lease-1")

    assert lease is not None
    assert lease.approval_notification_target_ids == []


def test_tool_approval_lease_expiry_and_revoke_disable_access(tmp_path):
    repository = JsonSessionToolApprovalLeaseRepository.from_runtime_dir(tmp_path)
    lease = repository.upsert_lease(
        session_id="session-1",
        workflow_run_id="run-1",
        tools=[ToolName.RUN_SCRIPT],
        expires_at=datetime.now(UTC) + timedelta(minutes=10),
    )

    assert not repository.is_tool_approved(
        lease_id=lease.lease_id,
        session_id="session-1",
        workflow_run_id="run-1",
        tool_name=ToolName.RUN_SCRIPT,
        now=datetime.now(UTC) + timedelta(hours=1),
    )

    assert repository.revoke_lease(lease.lease_id)
    assert not repository.is_tool_approved(
        lease_id=lease.lease_id,
        session_id="session-1",
        workflow_run_id="run-1",
        tool_name=ToolName.RUN_SCRIPT,
    )


def test_delete_expired_removes_expired_and_revoked_leases(tmp_path):
    repository = JsonSessionToolApprovalLeaseRepository.from_runtime_dir(tmp_path)
    active = repository.upsert_lease(
        session_id="session-1",
        workflow_run_id="run-1",
        tools=[ToolName.RUN_SCRIPT],
        expires_at=datetime.now(UTC) + timedelta(minutes=10),
    )
    expired = repository.upsert_lease(
        session_id="session-1",
        workflow_run_id="run-2",
        tools=[ToolName.RUN_SCRIPT],
        expires_at=datetime.now(UTC) - timedelta(minutes=10),
    )
    revoked = repository.upsert_lease(
        session_id="session-1",
        workflow_run_id="run-3",
        tools=[ToolName.RUN_SCRIPT],
        expires_at=datetime.now(UTC) + timedelta(minutes=10),
    )
    assert repository.revoke_lease(revoked.lease_id)

    removed = repository.delete_expired(now=datetime.now(UTC))

    assert removed == 2
    assert repository.get_lease(active.lease_id) is not None
    assert repository.get_lease(expired.lease_id) is None
    assert repository.get_lease(revoked.lease_id) is None
