from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

from mcp.server.fastmcp.exceptions import ToolError
import pytest

from evander.config import GatewayConfig
from evander.gateway import create_server
from evander.host_directories.models import HostDirectoryDirectoryConfig
from evander.host_directories.paths import host_directory_revision
from evander.models import (
    ApprovalGrantSettings,
    ApprovalPayloadMode,
    ApprovalStatus,
    SessionManifest,
    ToolName,
)
from evander.runtime import build_runtime


def _runtime(tmp_path: Path, host_root: Path):
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
    config = HostDirectoryDirectoryConfig(
        host_directories={
            "drop": {
                "display_name": "Drop",
                "description": "Gateway test drop",
                "root": host_root,
                "capabilities": ["list", "import", "delete"],
                "delete_requires_approval": True,
            }
        }
    )
    context.provider_config.resolve_host_directories_for_directory = (  # type: ignore[method-assign]
        lambda _path: config
    )
    context.session_repository.upsert_session(
        SessionManifest(
            session_id="session-host-directory",
            directory_path="root",
            allowed_tools=[
                ToolName.HOST_DIRECTORY_LIST,
                ToolName.HOST_DIRECTORY_IMPORT,
                ToolName.HOST_DIRECTORY_DELETE,
            ],
            host_directory_grants={
                "drop": ["list", "import", "delete"],
            },
            approval_tool_settings={
                ToolName.HOST_DIRECTORY_DELETE.value: ApprovalGrantSettings(
                    ttl_seconds=7200,
                    max_uses=None,
                    payload_mode=ApprovalPayloadMode.ANY,
                )
            },
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    return context


def test_gateway_host_directory_tools_hide_root_and_gate_delete_exactly_once(
    tmp_path: Path,
) -> None:
    host_root = tmp_path / "host-drop"
    host_root.mkdir()
    target = host_root / "invoice.txt"
    target.write_text("invoice", encoding="utf-8")
    context = _runtime(tmp_path / "runtime", host_root)
    server = create_server(context)

    tool_names = {tool.name for tool in server._tool_manager.list_tools()}
    assert {
        "host_directory_list",
        "host_directory_import",
        "host_directory_delete",
    }.issubset(tool_names)

    listed = asyncio.run(
        server.call_tool(
            "host_directory_list",
            {"session_id": "session-host-directory", "directory_id": "drop"},
        )
    )
    imported = asyncio.run(
        server.call_tool(
            "host_directory_import",
            {
                "session_id": "session-host-directory",
                "directory_id": "drop",
                "path": "invoice.txt",
            },
        )
    )
    assert "invoice.txt" in str(listed)
    assert "comm_" in str(imported)
    assert str(host_root) not in str(listed)
    assert str(host_root) not in str(imported)

    delete_arguments = {
        "session_id": "session-host-directory",
        "directory_id": "drop",
        "path": "invoice.txt",
        "expected_revision": host_directory_revision(target.lstat()),
    }
    with pytest.raises(ToolError, match="approval"):
        asyncio.run(server.call_tool("host_directory_delete", delete_arguments))

    approval = next(iter(context.approvals.read_all().values()))
    assert approval.approval_settings is not None
    assert approval.approval_settings.ttl_seconds == 7200
    assert approval.approval_settings.max_uses == 1
    assert approval.approval_settings.payload_mode is ApprovalPayloadMode.EXACT
    assert approval.payload["arguments"]["directory_id"] == "drop"
    assert approval.payload["arguments"]["path"] == "invoice.txt"
    assert str(host_root) not in str(approval.model_dump(mode="json"))

    approval.status = ApprovalStatus.APPROVED
    approval.approver = "operator"
    approval.decision_at = datetime.now(UTC)
    approval.decision_reason = "verified"
    context.approvals.put(approval.request_id, approval)

    deleted = asyncio.run(
        server.call_tool(
            "host_directory_delete",
            {
                **delete_arguments,
                "approval_request_id": approval.request_id,
            },
        )
    )
    assert "quarantined" in str(deleted)
    assert not target.exists()
    assert str(host_root) not in str(deleted)

    with pytest.raises(ToolError):
        asyncio.run(
            server.call_tool(
                "host_directory_delete",
                {
                    **delete_arguments,
                    "approval_request_id": approval.request_id,
                },
            )
        )

    assert str(host_root) not in (tmp_path / "runtime" / "audit.jsonl").read_text(encoding="utf-8")
