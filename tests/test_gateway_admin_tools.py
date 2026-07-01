from __future__ import annotations

from pathlib import Path

from evander.config import GatewayConfig
from evander.gateway import create_server
from evander.runtime import build_runtime


def test_create_server_registers_admin_operator_tools(tmp_path: Path) -> None:
    config = GatewayConfig(
        runtime_dir=tmp_path,
        audit_log_path=tmp_path / "audit.jsonl",
        session_store_path=tmp_path / "sessions.json",
        approval_store_path=tmp_path / "approvals.json",
        draft_store_path=tmp_path / "drafts.json",
        email_store_path=tmp_path / "emails.json",
        discord_command_store_path=tmp_path / "discord.json",
    )
    server = create_server(build_runtime(config))

    assert {
        "assistant_read_status",
        "assistant_list_agents",
        "get_workflow_snapshot",
        "assistant_inspect_workflows",
        "assistant_read_config",
        "assistant_upsert_config",
        "assistant_create_workflow",
        "assistant_run_safe_operation",
    }.issubset(server._tool_manager._tools)
