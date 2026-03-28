from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import subprocess

from agent_sandbox.admin_service import AdminService
from agent_sandbox.models import SessionManifest

UTC = timezone.utc


def write_config(tmp_path: Path, *, enable_directory_scoped_config: bool = False) -> Path:
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
                f'enable_directory_scoped_config: {str(enable_directory_scoped_config).lower()}',
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
    assert issue.issue_id == "root::local::1"
    assert issue.title == "Fix VM dashboard"
    yaml_path = Path(issue.yaml_path)
    assert yaml_path.exists()
    yaml_text = yaml_path.read_text(encoding="utf-8")
    assert "issue_number: 1" in yaml_text
    assert '"Fix VM dashboard"' in yaml_text

    payload = json.loads((tmp_path / "runtime" / "local-issues.json").read_text(encoding="utf-8"))
    assert payload[issue.issue_id]["directory_path"] == "root"
    assert payload[issue.issue_id]["repo_source"] == "/src/repo"


def test_create_local_issue_reuses_scoped_id_within_directory(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path, enable_directory_scoped_config=True))
    created = service.create_directory_child("root", "team")
    assert created.ok is True and created.data is not None
    team_path = created.data.details.directory.path

    source_path = tmp_path / "team-issue.yaml"
    source_path.write_text("issue_number: 7\ntitle: Team issue\nbody: First body\n", encoding="utf-8")
    first = service.create_local_issue(yaml_path=str(source_path), directory_path=team_path)

    source_path.write_text("issue_number: 7\ntitle: Team issue\nbody: Updated body\n", encoding="utf-8")
    second = service.create_local_issue(yaml_path=str(source_path), directory_path=team_path)

    assert first.issue_id == "root/team::local::7"
    assert second.issue_id == first.issue_id
    assert second.body == "Updated body"

    records = service.local_issue_store.read_all()
    assert list(records) == ["root/team::local::7"]



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


def test_list_sessions_filters_active_and_expired(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path))
    service.runtime.sessions.put(
        "issue-123-active",
        SessionManifest(
            session_id="issue-123-active",
            vm_id="vm-123",
            allowed_tools=[],
            created_at=datetime(2026, 3, 25, 9, 0, tzinfo=UTC),
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    service.runtime.sessions.put(
        "issue-123-expired",
        SessionManifest(
            session_id="issue-123-expired",
            vm_id="vm-123",
            allowed_tools=[],
            created_at=datetime(2026, 3, 25, 8, 0, tzinfo=UTC),
            expires_at=datetime.now(UTC) - timedelta(hours=1),
        ),
    )

    active = service.list_sessions()
    expired = service.list_sessions(expired_only=True)
    all_sessions = service.list_sessions(include_expired=True)

    assert [summary.session_id for summary in active] == ["issue-123-active"]
    assert [summary.session_id for summary in expired] == ["issue-123-expired"]
    assert [summary.session_id for summary in all_sessions] == ["issue-123-expired", "issue-123-active"]
    assert active[0].issue_number == "123"
    assert active[0].is_expired is False
    assert expired[0].is_expired is True


def test_directory_filtered_lists_use_direct_owned_items_only(tmp_path: Path, monkeypatch) -> None:
    service = AdminService(write_config(tmp_path, enable_directory_scoped_config=True))
    root_dir = service.create_directory_child("root", "team")
    assert root_dir.ok is True and root_dir.data is not None
    team_path = root_dir.data.details.directory.path

    service.runtime.sessions.put(
        "team-session",
        SessionManifest(
            session_id="team-session",
            vm_id="vm-101",
            directory_path=team_path,
            allowed_tools=[],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            metadata={"issue_number": "101"},
        ),
    )
    service.runtime.sessions.put(
        "root-session",
        SessionManifest(
            session_id="root-session",
            vm_id="vm-root",
            directory_path="root",
            allowed_tools=[],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            metadata={"issue_number": "1"},
        ),
    )
    service.create_local_issue(title="Root issue", body="a", directory_path="root")
    service.create_local_issue(title="Team issue", body="b", directory_path=team_path)

    monkeypatch.setattr("agent_sandbox.admin_service._virsh_lines", lambda args: ["vm-101", "vm-root", "vm-free"])
    monkeypatch.setattr("agent_sandbox.admin_service._dominfo_field", lambda vm_name, field_name: "shut off")

    team_sessions = service.list_sessions(include_expired=True, directory_path=team_path, direct_only=True)
    team_issues = service.list_local_issues(directory_path=team_path, direct_only=True)
    team_vms = service.list_vms(include_inactive=True, directory_path=team_path, direct_only=True)
    root_vms = service.list_vms(include_inactive=True, directory_path="root", direct_only=True)

    assert [summary.session_id for summary in team_sessions] == ["team-session"]
    assert [issue.title for issue in team_issues] == ["Team issue"]
    assert [summary.vm_name for summary in team_vms] == ["vm-101"]
    assert {summary.vm_name for summary in root_vms} == {"vm-root", "vm-free"}


def test_admin_service_exposes_directory_config_for_selected_directory(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path, enable_directory_scoped_config=True))
    created = service.create_directory_child("root", "team")
    assert created.ok is True and created.data is not None
    team_path = created.data.details.directory.path

    write_result = service.write_directory_domain_config(
        team_path,
        "policy",
        {"default_session_ttl_seconds": 120},
    )
    assert write_result.ok is True

    read_result = service.read_directory_domain_config(team_path, "policy")
    assert read_result.ok is True
    assert read_result.data is not None
    assert read_result.data.config == {"default_session_ttl_seconds": 120}

    effective_result = service.compute_directory_effective_config(team_path, "policy")
    assert effective_result.ok is True
    assert effective_result.data is not None
    assert effective_result.data.resolution.effective_config["default_session_ttl_seconds"] == 120


def test_admin_service_reads_and_writes_ado_worker_global_config(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path))

    read_result = service.read_worker_global_config("ado")
    assert read_result.exists is False
    assert read_result.path == str(tmp_path / "config" / "ado-worker" / "config.json")
    assert read_result.config["ado_work_items_azure_devops_pat_env"] == "ADO_WORK_ITEMS_AZURE_DEVOPS_PAT"
    assert read_result.config["directories"] == []

    write_result = service.write_worker_global_config(
        "ado",
        {
            "runtime_dir": str(tmp_path / "runtime"),
            "poll_interval_seconds": 12.5,
            "directories": ["root/team-a", "root/team-b"],
            "ado_work_items_azure_devops_pat_env": "ADO_WORK_ITEMS_AZURE_DEVOPS_PAT",
            "include_comments": False,
            "comments_max_count": 7,
        },
    )
    assert write_result.config["poll_interval_seconds"] == 12.5
    assert write_result.config["directories"] == ["root/team-a", "root/team-b"]
    assert write_result.config["ado_work_items_azure_devops_pat_env"] == "ADO_WORK_ITEMS_AZURE_DEVOPS_PAT"

    config_path = tmp_path / "config" / "ado-worker" / "config.json"
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    assert payload["poll_interval_seconds"] == 12.5
    assert payload["directories"] == ["root/team-a", "root/team-b"]
    assert payload["ado_work_items_azure_devops_pat_env"] == "ADO_WORK_ITEMS_AZURE_DEVOPS_PAT"
    assert payload["include_comments"] is False
    assert payload["comments_max_count"] == 7

    reread = service.read_worker_global_config("ado")
    assert reread.config["include_comments"] is False


def test_admin_service_reads_and_writes_host_heartbeat_worker_global_config(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path))

    read_result = service.read_worker_global_config("host-heartbeat")
    assert read_result.exists is False
    assert read_result.path == str(tmp_path / "config" / "host-heartbeat" / "config.json")
    assert read_result.config["host_heartbeat_poll_interval_seconds"] == 30.0
    assert read_result.config["host_heartbeat_directory_paths"] == []

    write_result = service.write_worker_global_config(
        "host-heartbeat",
        {
            "runtime_dir": str(tmp_path / "runtime"),
            "host_heartbeat_directory_paths": ["root/team-a", "root/team-b"],
            "host_heartbeat_poll_interval_seconds": 12.5,
            "host_heartbeat_jitter_seconds": 1.5,
            "host_heartbeat_cooldown_seconds": 90.0,
            "host_heartbeat_http_timeout_seconds": 15.0,
            "host_heartbeat_lock_path": None,
            "host_heartbeat_session_cooldown_path": None,
            "host_busy_check_script_path": "scripts/check_session_vm_busy.sh",
            "host_resume_script_path": "scripts/resume_issue_agent.sh",
            "host_heartbeat_wake_state_path": None,
            "host_heartbeat_vm_user": "worker",
            "host_heartbeat_resume_timeout_seconds": 600.0,
            "host_heartbeat_stream_conflict_policy": "reject",
        },
    )
    assert write_result.config["host_heartbeat_stream_conflict_policy"] == "reject"

    config_path = tmp_path / "config" / "host-heartbeat" / "config.json"
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    assert payload["host_heartbeat_directory_paths"] == ["root/team-a", "root/team-b"]
    assert payload["host_heartbeat_poll_interval_seconds"] == 12.5
    assert payload["host_heartbeat_stream_conflict_policy"] == "reject"
    assert payload["host_heartbeat_vm_user"] == "worker"

    reread = service.read_worker_global_config("host-heartbeat")
    assert reread.config["host_heartbeat_directory_paths"] == ["root/team-a", "root/team-b"]
    assert reread.config["host_heartbeat_stream_conflict_policy"] == "reject"


def test_create_issue_vm_uses_directory_vm_defaults_overlay_for_guest_env(tmp_path: Path, monkeypatch) -> None:
    service = AdminService(write_config(tmp_path, enable_directory_scoped_config=True))
    create_result = service.create_directory_child("root", "team")
    assert create_result.ok is True and create_result.data is not None
    team_path = create_result.data.details.directory.path
    write_result = service.write_directory_domain_config(
        team_path,
        "vm",
        {
            "gateway_url": "http://10.20.30.40:8000/mcp",
            "openai_relay_port": 12000,
            "nuget_relay_port": 13000,
        },
    )
    assert write_result.ok is True

    captured_env: dict[str, str] = {}

    def fake_run(*args, **kwargs):
        captured_env.update(kwargs.get("env", {}))
        return subprocess.CompletedProcess(args[0], 0, stdout="", stderr="")

    monkeypatch.setattr("agent_sandbox.admin_service.subprocess.run", fake_run)

    result = service.create_issue_vm(
        issue="123",
        session_id="issue-123-session",
        directory_path=team_path,
        start_agent=False,
    )

    assert result.success is True
    assert captured_env["GATEWAY_URL"] == "http://10.20.30.40:8000/mcp"
    assert captured_env["LLM_BASE_URL"] == "http://10.20.30.40:12000/v1"
    assert captured_env["NUGET_RELAY_BASE_URL"] == "http://10.20.30.40:13000"


def test_create_issue_vm_uses_issue_setup_domain_for_setup_env(tmp_path: Path, monkeypatch) -> None:
    service = AdminService(write_config(tmp_path, enable_directory_scoped_config=True))
    create_result = service.create_directory_child("root", "team")
    assert create_result.ok is True and create_result.data is not None
    team_path = create_result.data.details.directory.path
    write_result = service.write_directory_domain_config(
        team_path,
        "issue_setup",
        {
            "git_root": "/tmp/git-remotes",
            "git_host": "10.1.2.3",
            "git_user": "gitbot",
            "git_ssh_alias": "issue-git",
            "post_exec_verification_prompt": "verify everything",
            "enable_guest_heartbeat": True,
            "guest_heartbeat_prompt": "continue working",
        },
    )
    assert write_result.ok is True

    captured_env: dict[str, str] = {}

    def fake_run(*args, **kwargs):
        captured_env.update(kwargs.get("env", {}))
        return subprocess.CompletedProcess(args[0], 0, stdout="", stderr="")

    monkeypatch.setattr("agent_sandbox.admin_service.subprocess.run", fake_run)

    result = service.create_issue_vm(
        issue="124",
        session_id="issue-124-session",
        directory_path=team_path,
        start_agent=False,
    )

    assert result.success is True
    assert captured_env["AGENT_SANDBOX_GIT_ROOT"] == "/tmp/git-remotes"
    assert captured_env["AGENT_SANDBOX_GIT_HOST"] == "10.1.2.3"
    assert captured_env["AGENT_SANDBOX_GIT_USER"] == "gitbot"
    assert captured_env["AGENT_SANDBOX_GIT_SSH_ALIAS"] == "issue-git"
    assert captured_env["AGENT_POST_EXEC_VERIFICATION_PROMPT"] == "verify everything"
    assert captured_env["ENABLE_GUEST_HEARTBEAT"] == "1"
    assert captured_env["GUEST_HEARTBEAT_PROMPT"] == "continue working"
    assert captured_env["AGENT_SANDBOX_AZURE_DEVOPS_PR_PAT_ENV"] == "AZURE_DEVOPS_PR_PAT"


def test_create_issue_vm_uses_issue_setup_azure_pat_override(tmp_path: Path, monkeypatch) -> None:
    service = AdminService(write_config(tmp_path, enable_directory_scoped_config=True))
    create_result = service.create_directory_child("root", "team")
    assert create_result.ok is True and create_result.data is not None
    team_path = create_result.data.details.directory.path
    write_result = service.write_directory_domain_config(
        team_path,
        "issue_setup",
        {
            "git_root": "/tmp/git-remotes",
            "git_host": "10.1.2.3",
            "git_user": "gitbot",
            "git_ssh_alias": "issue-git",
            "issue_setup_azure_devops_pr_pat_env": "TEAM_ISSUE_SETUP_ADO_PAT",
        },
    )
    assert write_result.ok is True

    captured_env: dict[str, str] = {}

    def fake_run(*args, **kwargs):
        captured_env.update(kwargs.get("env", {}))
        return subprocess.CompletedProcess(args[0], 0, stdout="", stderr="")

    monkeypatch.setattr("agent_sandbox.admin_service.subprocess.run", fake_run)

    result = service.create_issue_vm(
        issue="125",
        session_id="issue-125-session",
        directory_path=team_path,
        start_agent=False,
    )

    assert result.success is True
    assert captured_env["AGENT_SANDBOX_AZURE_DEVOPS_PR_PAT_ENV"] == "TEAM_ISSUE_SETUP_ADO_PAT"


def test_create_issue_vm_uses_issue_setup_fallback_env_when_domain_missing(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_SANDBOX_GIT_ROOT", "/tmp/from-env")
    monkeypatch.setenv("AGENT_SANDBOX_GIT_HOST", "10.55.0.1")
    monkeypatch.setenv("AGENT_SANDBOX_GIT_USER", "env-user")
    monkeypatch.setenv("AGENT_SANDBOX_GIT_SSH_ALIAS", "env-git")
    monkeypatch.setenv("AGENT_SANDBOX_AZURE_DEVOPS_PR_PAT_ENV", "ENV_PAT_NAME")
    monkeypatch.setenv("AGENT_POST_EXEC_VERIFICATION_PROMPT", "verify from env")
    service = AdminService(write_config(tmp_path, enable_directory_scoped_config=True))

    captured_env: dict[str, str] = {}

    def fake_run(*args, **kwargs):
        captured_env.update(kwargs.get("env", {}))
        return subprocess.CompletedProcess(args[0], 0, stdout="", stderr="")

    monkeypatch.setattr("agent_sandbox.admin_service.subprocess.run", fake_run)

    result = service.create_issue_vm(
        issue="126",
        session_id="issue-126-session",
        directory_path="root",
        start_agent=False,
    )

    assert result.success is True
    assert captured_env["AGENT_SANDBOX_GIT_ROOT"] == "/tmp/from-env"
    assert captured_env["AGENT_SANDBOX_GIT_HOST"] == "10.55.0.1"
    assert captured_env["AGENT_SANDBOX_GIT_USER"] == "env-user"
    assert captured_env["AGENT_SANDBOX_GIT_SSH_ALIAS"] == "env-git"
    assert captured_env["AGENT_SANDBOX_AZURE_DEVOPS_PR_PAT_ENV"] == "ENV_PAT_NAME"
    assert captured_env["AGENT_POST_EXEC_VERIFICATION_PROMPT"] == "verify from env"


def test_create_issue_vm_never_uses_pat_secret_as_env_var_name(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("AGENT_SANDBOX_AZURE_DEVOPS_PR_PAT_ENV", raising=False)
    monkeypatch.setenv("AZURE_DEVOPS_PR_PAT", "super-secret-pat")
    service = AdminService(write_config(tmp_path, enable_directory_scoped_config=True))

    captured_env: dict[str, str] = {}

    def fake_run(*args, **kwargs):
        captured_env.update(kwargs.get("env", {}))
        return subprocess.CompletedProcess(args[0], 0, stdout="", stderr="")

    monkeypatch.setattr("agent_sandbox.admin_service.subprocess.run", fake_run)

    result = service.create_issue_vm(
        issue="127",
        session_id="issue-127-session",
        directory_path="root",
        start_agent=False,
    )

    assert result.success is True
    assert captured_env["AGENT_SANDBOX_AZURE_DEVOPS_PR_PAT_ENV"] == "AZURE_DEVOPS_PR_PAT"


def test_admin_service_rejects_directory_edits_when_feature_flag_disabled(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path))

    result = service.create_directory_child("root", "team")

    assert result.ok is False
    assert result.errors[0].code == "feature_disabled"


def test_renew_session_sets_expiration_from_now(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path))
    service.runtime.sessions.put(
        "issue-14084-session",
        SessionManifest(
            session_id="issue-14084-session",
            vm_id="vm-14084",
            allowed_tools=[],
            expires_at=datetime.now(UTC) + timedelta(days=5),
        ),
    )

    before = datetime.now(UTC)
    result = service.renew_session("14084", extension="6h")
    after = datetime.now(UTC)

    renewed = service.runtime.sessions.get("issue-14084-session")
    assert renewed is not None
    assert result.success is True
    assert result.session_id == "issue-14084-session"
    assert before + timedelta(hours=6) <= renewed.expires_at <= after + timedelta(hours=6)


def test_revoke_session_expires_immediately(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path))
    service.runtime.sessions.put(
        "session-1",
        SessionManifest(
            session_id="session-1",
            vm_id="vm-1",
            allowed_tools=[],
            expires_at=datetime.now(UTC) + timedelta(hours=2),
        ),
    )

    before = datetime.now(UTC)
    result = service.revoke_session("session-1")
    after = datetime.now(UTC)

    revoked = service.runtime.sessions.get("session-1")
    assert revoked is not None
    assert result.success is True
    assert before <= revoked.expires_at <= after


def test_delete_session_removes_latest_matching_target(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path))
    service.runtime.sessions.put(
        "issue-13301-older",
        SessionManifest(
            session_id="issue-13301-older",
            vm_id="vm-13301",
            allowed_tools=[],
            created_at=datetime(2026, 3, 25, 8, 0, tzinfo=UTC),
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    service.runtime.sessions.put(
        "issue-13301-newer",
        SessionManifest(
            session_id="issue-13301-newer",
            vm_id="vm-13301",
            allowed_tools=[],
            created_at=datetime(2026, 3, 25, 9, 0, tzinfo=UTC),
            expires_at=datetime.now(UTC) + timedelta(hours=2),
        ),
    )

    result = service.delete_session("vm-13301")

    remaining = service.runtime.sessions.read_all()
    assert result.success is True
    assert result.session_id == "issue-13301-newer"
    assert set(remaining) == {"issue-13301-older"}


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
