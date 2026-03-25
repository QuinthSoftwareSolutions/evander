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


def test_list_vms_marks_inactive_vm_disabled_from_host_control_state(tmp_path: Path, monkeypatch) -> None:
    service = AdminService(write_config(tmp_path))
    service.agent_control_store.set_status(vm_name="vm-999", status="disabled", phase="operator-disable")

    monkeypatch.setattr("agent_sandbox.admin_service._virsh_lines", lambda args: ["vm-999"])
    monkeypatch.setattr("agent_sandbox.admin_service._dominfo_field", lambda vm_name, field_name: "shut off")

    summaries = service.list_vms(include_inactive=True)

    assert summaries[0].agent_state == "disabled"


def test_list_background_workers_reads_systemd_state(tmp_path: Path, monkeypatch) -> None:
    service = AdminService(write_config(tmp_path))

    payloads = {
        "agent-sandbox-ado-worker.service": {
            "ActiveState": "active",
            "SubState": "running",
            "Result": "success",
            "UnitFileState": "enabled",
            "Description": "ADO worker",
            "MainPID": "123",
            "LoadState": "loaded",
            "ExecMainCode": "1",
            "ExecMainStatus": "0",
        },
        "agent-sandbox-host-heartbeat.service": {
            "ActiveState": "failed",
            "SubState": "failed",
            "Result": "failed",
            "UnitFileState": "enabled",
            "Description": "Host heartbeat worker",
            "MainPID": "0",
            "LoadState": "loaded",
            "ExecMainCode": "1",
            "ExecMainStatus": "2",
        },
    }
    monkeypatch.setattr("agent_sandbox.admin_service._systemctl_show", lambda unit: payloads[unit])

    summaries = service.list_background_workers()

    assert [summary.worker_id for summary in summaries] == ["ado", "host-heartbeat"]
    assert summaries[0].running is True
    assert summaries[0].failed is False
    assert summaries[0].main_pid == 123
    assert summaries[0].unit_file_state == "enabled"
    assert summaries[1].running is False
    assert summaries[1].failed is True
    assert summaries[1].detail == "load=loaded, exec=1:2"


def test_list_background_workers_marks_unavailable_when_systemctl_fails(tmp_path: Path, monkeypatch) -> None:
    service = AdminService(write_config(tmp_path))

    def _raise(unit: str) -> dict[str, str]:
        raise RuntimeError(f"cannot inspect {unit}")

    monkeypatch.setattr("agent_sandbox.admin_service._systemctl_show", _raise)

    summaries = service.list_background_workers()

    assert all(summary.active_state == "unavailable" for summary in summaries)
    assert all(summary.running is False for summary in summaries)
    assert all(summary.failed is False for summary in summaries)


def test_get_background_worker_log_snapshot_returns_unit_journal(tmp_path: Path, monkeypatch) -> None:
    service = AdminService(write_config(tmp_path))
    monkeypatch.setattr(
        "agent_sandbox.admin_service._journalctl_tail",
        lambda unit, lines: f"{unit} log tail ({lines})",
    )

    text = service.get_background_worker_log_snapshot("ado", lines=25)

    assert "Worker: ADO Worker" in text
    assert "Unit: agent-sandbox-ado-worker.service" in text
    assert "agent-sandbox-ado-worker.service log tail (25)" in text


def test_start_background_worker_controls_systemd_unit(tmp_path: Path, monkeypatch) -> None:
    service = AdminService(write_config(tmp_path))
    calls: list[tuple[str, str]] = []

    class _Completed:
        returncode = 0
        stdout = "started"
        stderr = ""

    def _fake_control(action: str, unit: str):
        calls.append((action, unit))
        return _Completed()

    monkeypatch.setattr("agent_sandbox.admin_service._run_systemd_control", _fake_control)

    result = service.start_background_worker("ado")

    assert calls == [("start", "agent-sandbox-ado-worker.service")]
    assert result.success is True
    assert result.worker_id == "ado"
    assert result.action == "start"
    assert result.message == "started"


def test_stop_background_worker_controls_systemd_unit(tmp_path: Path, monkeypatch) -> None:
    service = AdminService(write_config(tmp_path))
    calls: list[tuple[str, str]] = []

    class _Completed:
        returncode = 0
        stdout = ""
        stderr = ""

    def _fake_control(action: str, unit: str):
        calls.append((action, unit))
        return _Completed()

    monkeypatch.setattr("agent_sandbox.admin_service._run_systemd_control", _fake_control)

    result = service.stop_background_worker("host-heartbeat")

    assert calls == [("stop", "agent-sandbox-host-heartbeat.service")]
    assert result.success is True
    assert result.worker_id == "host-heartbeat"
    assert result.action == "stop"
    assert "Stopped Host Heartbeat Worker" == result.message


def test_background_worker_actions_reject_unknown_worker(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path))

    start = service.start_background_worker("missing")
    stop = service.stop_background_worker("missing")

    assert start.success is False
    assert stop.success is False
    assert start.message == "Unknown worker: missing"
    assert stop.message == "Unknown worker: missing"
