from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from evander.config import GatewayConfig
from evander.gateway import create_server
from evander.models import SessionManifest, ToolName
from evander.runtime import build_runtime


def test_mssql_tools_register_after_domain_extraction(tmp_path: Path) -> None:
    context = build_runtime(
        GatewayConfig(
            runtime_dir=tmp_path,
            audit_log_path=tmp_path / "audit.jsonl",
            session_store_path=tmp_path / "sessions.json",
            approval_store_path=tmp_path / "approvals.json",
            draft_store_path=tmp_path / "drafts.json",
            email_store_path=tmp_path / "emails.json",
            discord_command_store_path=tmp_path / "discord.json",
        )
    )
    context.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            allowed_tools=[],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            directory_path="root",
        )
    )

    server = create_server(context)
    tools = {tool.name: tool for tool in server._tool_manager.list_tools()}

    assert {
        ToolName.MSSQL_PROVISION_DATABASE.value,
        ToolName.MSSQL_LIST_DATABASES.value,
        ToolName.MSSQL_DROP_DATABASE.value,
    }.issubset(tools)
    provision_parameters = tools[ToolName.MSSQL_PROVISION_DATABASE.value].parameters
    assert "session_id" in provision_parameters["properties"]
    assert "logical_name" in provision_parameters["properties"]
