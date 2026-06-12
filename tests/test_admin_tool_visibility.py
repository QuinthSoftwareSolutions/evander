from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from agent_sandbox.admin.service import AdminService
from agent_sandbox.admin.tool_visibility import ToolVisibilityState
from agent_sandbox.models import SessionManifest, ToolName


def write_config(tmp_path: Path, *, enable_directory_scoped_config: bool = True) -> Path:
    runtime_dir = tmp_path / "runtime"
    config_path = tmp_path / "gateway.yaml"
    config_path.write_text(
        "\n".join(
            [
                f'runtime_dir: "{runtime_dir}"',
                f'audit_log_path: "{runtime_dir / "audit.jsonl"}"',
                f'session_store_path: "{runtime_dir / "sessions.json"}"',
                f'approval_store_path: "{runtime_dir / "approvals.json"}"',
                f'draft_store_path: "{runtime_dir / "drafts.json"}"',
                f'email_store_path: "{runtime_dir / "emails.json"}"',
                f'discord_command_store_path: "{runtime_dir / "discord_commands.json"}"',
                f"enable_directory_scoped_config: {str(enable_directory_scoped_config).lower()}",
            ]
        ),
        encoding="utf-8",
    )
    return config_path


def _tool_rows(payload) -> dict[str, object]:
    return {row.tool_name: row for row in payload.tools}


def test_admin_service_tool_visibility_payload_classifies_rows_and_metadata(
    tmp_path: Path,
) -> None:
    service = AdminService(write_config(tmp_path))
    service.write_directory_domain_config(
        "root",
        "policy",
        {"mcp_tool_policies": {ToolName.RUN_SCRIPT.value: {"denied": True}}},
    )
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            vm_id="vm-1",
            vm_provider="libvirt",
            allowed_tools=[
                ToolName.CREATE_ADO_BACKLOG_ITEM,
                ToolName.RUN_SCRIPT,
                ToolName.ASSISTANT_CREATE_WORKFLOW,
                ToolName.LIST_UNREAD_FEEDBACK,
            ],
            approval_required_tools=[ToolName.CREATE_ADO_BACKLOG_ITEM],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )

    payload = service.tool_visibility_payload(
        target="session-1",
        workflow_run_id="run-7",
        mcp_tool_approval_lease_id="lease-9",
    )

    serialized = payload.model_dump(mode="json")
    rows = _tool_rows(payload)

    assert serialized["session_context"]["session_id"] == "session-1"
    assert serialized["session_context"]["vm_id"] == "vm-1"
    assert serialized["session_context"]["vm_provider"] == "libvirt"
    assert serialized["session_context"]["capability_profile"] is None
    assert serialized["query_context"] == {
        "workflow_run_id": "run-7",
        "mcp_tool_approval_lease_id": "lease-9",
    }

    visible = rows[ToolName.CREATE_ADO_BACKLOG_ITEM.value]
    assert visible.visibility_state is ToolVisibilityState.VISIBLE
    assert visible.source == "session.allowed_tools"
    assert visible.category == "Azure DevOps"
    assert visible.provider == "azure_devops"
    assert visible.label == "create ado backlog item"
    assert visible.requires_approval is True
    assert visible.approval_settings is not None

    hidden = rows[ToolName.RUN_SCRIPT.value]
    assert hidden.visibility_state is ToolVisibilityState.HIDDEN_BY_POLICY
    assert hidden.source == "directory.mcp_tool_policies"
    assert hidden.category == "Agent control"
    assert "denied by directory policy" in str(hidden.reason)

    unavailable = rows[ToolName.LIST_UNREAD_FEEDBACK.value]
    assert unavailable.visibility_state is ToolVisibilityState.UNAVAILABLE_TO_SESSION
    assert unavailable.source == "session.metadata.pr_feedback_streams"
    assert "missing metadata.pr_feedback_streams" in str(unavailable.reason)

    assistant_hidden = rows[ToolName.ASSISTANT_CREATE_WORKFLOW.value]
    assert assistant_hidden.visibility_state is ToolVisibilityState.HIDDEN_BY_POLICY
    assert assistant_hidden.source == "session.metadata.capability_profile"
    assert "capability profile" in str(assistant_hidden.reason)

    assert payload.counts.total == len(ToolName)
    assert payload.counts.visible == 1
    assert payload.counts.hidden_by_policy == len(ToolName) - 2
    assert payload.counts.unavailable_to_session == 1
    assert payload.counts.unknown == 0
    assert payload.counts.requires_approval == 1

    assert payload.notices
    assert payload.notices[0].kind == "missing_capability_profile"
    assert "assistant tools are hidden" in payload.notices[0].message

