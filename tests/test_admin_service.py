from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from agent_sandbox.admin_service import AdminService
from agent_sandbox.models import SessionManifest

UTC = timezone.utc


def write_config(tmp_path: Path) -> Path:
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
            ]
        ),
        encoding="utf-8",
    )
    return config_path


def test_create_local_issue_persists_yaml_and_store(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path))

    issue = service.create_local_issue(
        title="Fix VM dashboard",
        body="Build a desktop dashboard for VM state.",
        repo_source="/src/repo",
    )

    assert issue.issue_number == "1"
    assert issue.title == "Fix VM dashboard"
    yaml_path = Path(issue.yaml_path)
    assert yaml_path.exists()
    yaml_text = yaml_path.read_text(encoding="utf-8")
    assert "issue_number: 1" in yaml_text
    assert '"Fix VM dashboard"' in yaml_text

    payload = json.loads((tmp_path / "runtime" / "local-issues.json").read_text(encoding="utf-8"))
    assert payload["1"]["repo_source"] == "/src/repo"


def test_list_vms_collects_session_and_agent_state(tmp_path: Path, monkeypatch) -> None:
    service = AdminService(write_config(tmp_path))
    service.runtime.sessions.put(
        "issue-123-session",
        SessionManifest(
            session_id="issue-123-session",
            vm_id="vm-123",
            allowed_tools=[],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )

    monkeypatch.setattr("agent_sandbox.admin_service._virsh_lines", lambda args: ["vm-123", "vm-999"])
    monkeypatch.setattr(
        "agent_sandbox.admin_service._dominfo_field",
        lambda vm_name, field_name: "running" if vm_name == "vm-123" else "shut off",
    )
    monkeypatch.setattr(
        "agent_sandbox.admin_service._resolve_vm_ip",
        lambda vm_name: "192.168.122.10" if vm_name == "vm-123" else None,
    )
    monkeypatch.setattr(
        "agent_sandbox.admin_service._probe_agent_state",
        lambda vm_name, vm_ip, vm_user: ("running", ["repo-one"], datetime.now(UTC)),
    )

    summaries = service.list_vms(include_inactive=True)

    assert [summary.vm_name for summary in summaries] == ["vm-123", "vm-999"]
    assert summaries[0].issue_number == "123"
    assert summaries[0].session_ids == ["issue-123-session"]
    assert summaries[0].agent_state == "running"
    assert summaries[0].agent_repo_names == ["repo-one"]
    assert summaries[1].agent_state == "inactive_vm"
