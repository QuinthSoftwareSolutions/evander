from __future__ import annotations

import concurrent.futures
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
import subprocess
from typing import Any

import agent_sandbox.admin_service as admin_service
import pytest
from agent_sandbox.admin_service import AdminService
from agent_sandbox.models import ApprovalRequest, ApprovalStatus, SessionManifest, ToolName

UTC = timezone.utc
REPO_ROOT = Path(__file__).resolve().parents[1]


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

def test_create_local_issue_serializes_concurrent_issue_number_allocation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = write_config(tmp_path)

    def slow_next_issue_number(self: AdminService) -> str:
        current = 0
        if self.local_issue_counter_path.exists():
            try:
                current = int(self.local_issue_counter_path.read_text(encoding="utf-8").strip() or "0")
            except ValueError:
                current = 0
        time.sleep(0.05)
        current += 1
        self.local_issue_counter_path.parent.mkdir(parents=True, exist_ok=True)
        self.local_issue_counter_path.write_text(f"{current}\n", encoding="utf-8")
        return str(current)

    monkeypatch.setattr(AdminService, "_next_local_issue_number", slow_next_issue_number)
    services = [AdminService(config_path) for _ in range(6)]

    def create_issue(args: tuple[AdminService, int]) -> str:
        service, index = args
        issue = service.create_local_issue(title=f"Issue {index}", body="parallel")
        return issue.issue_number

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        issue_numbers = list(pool.map(create_issue, zip(services, range(6), strict=True)))

    assert sorted(issue_numbers) == ["1", "2", "3", "4", "5", "6"]
    payload = json.loads((tmp_path / "runtime" / "local-issues.json").read_text(encoding="utf-8"))
    assert sorted(payload) == [f"root::local::{number}" for number in range(1, 7)]
    for number in range(1, 7):
        assert (tmp_path / "runtime" / "issues" / f"{number}.yaml").exists()


def test_create_local_issue_imports_block_scalar_body(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path))
    source_path = tmp_path / "block-issue.yaml"
    source_path.write_text(
        "issue_number: 7\n"
        "title: Block body\n"
        "body: |\n"
        "  line one\n"
        "  line two\n",
        encoding="utf-8",
    )

    issue = service.create_local_issue(yaml_path=str(source_path))

    assert issue.body == "line one\nline two"


def test_create_local_issue_imports_block_scalar_title(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path))
    source_path = tmp_path / "block-title-issue.yaml"
    source_path.write_text(
        "issue_number: 8\n"
        "title: |\n"
        "  line one\n"
        "  line two\n"
        "body: ok\n",
        encoding="utf-8",
    )

    issue = service.create_local_issue(yaml_path=str(source_path))

    assert issue.title == "line one\nline two"


def test_update_local_issue_updates_store_and_yaml(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path))
    issue = service.create_local_issue(title="Old title", body="Old body", repo_source="/old/repo")

    updated = service.update_local_issue(
        issue.issue_number,
        title="New title",
        body="New body",
        state="In Progress",
        repo_source="/new/repo",
    )

    assert updated.title == "New title"
    assert updated.body == "New body"
    assert updated.state == "In Progress"
    assert updated.repo_source == "/new/repo"
    yaml_text = Path(updated.yaml_path).read_text(encoding="utf-8")
    assert "title: New title" in yaml_text
    assert "body: New body" in yaml_text
    assert "state: In Progress" in yaml_text


def test_update_local_issue_rejects_proxy_issue(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path))
    issue = service.create_local_issue(yaml_path=str(_write_proxy_issue_yaml(tmp_path)))

    with pytest.raises(ValueError, match="Only local manual issues"):
        service.update_local_issue(
            issue.issue_number,
            title="Ignore",
            body="Ignore",
            state="New",
            repo_source=None,
        )



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


def test_resume_agent_accepts_vm_name_without_vm_prefix(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    service = AdminService(write_config(tmp_path))
    service.runtime.sessions.put(
        "agent-sandbox-receptionist-vm-20260330T112127Z-1353036",
        SessionManifest(
            session_id="agent-sandbox-receptionist-vm-20260330T112127Z-1353036",
            vm_id="agent-sandbox-receptionist-vm",
            model="gpt-5.4",
            allowed_tools=[],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )

    captured: dict[str, object] = {}

    def fake_run_script(script_name: str, args: list[str], env: dict[str, str] | None = None):
        captured["script_name"] = script_name
        captured["args"] = args
        captured["env"] = env
        return admin_service.VmActionResult(
            success=True,
            vm_name="agent-sandbox-receptionist-vm",
            action="resume_issue_agent",
            message="ok",
        )

    monkeypatch.setattr(service, "_run_script", fake_run_script)

    result = service.resume_agent("agent-sandbox-receptionist-vm", "Resume now")

    assert result.success is True
    assert captured["script_name"] == "resume_issue_agent.sh"
    assert captured["args"] == [
        "--prompt",
        "Resume now",
        "--vm-name",
        "agent-sandbox-receptionist-vm",
        "--vm-user",
        "agent",
        "--host-control-state-path",
        str(service.config.host_agent_control_state_path),
    ]
    assert captured["env"]["AGENT_MODEL"] == "gpt-5.4"


def test_probe_agent_state_accepts_codex_resume_state(tmp_path: Path, monkeypatch) -> None:
    write_config(tmp_path)
    monkeypatch.setattr("agent_sandbox.admin_service._resolve_private_key_path", lambda: Path("/tmp/test-key"))

    captured: dict[str, object] = {}

    def _fake_run_subprocess(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured["cmd"] = cmd
        captured["input"] = kwargs.get("input")
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=0,
            stdout='{"state":"running","repos":["codex-heartbeat"],"updated_at":"2026-03-29T12:06:53+00:00"}',
            stderr="",
        )

    monkeypatch.setattr("agent_sandbox.admin_service._run_subprocess", _fake_run_subprocess)

    state, repos, updated_at = admin_service._probe_agent_state("bug-finder-vm", "192.168.122.11", "agent")

    assert state == "running"
    assert repos == ["codex-heartbeat"]
    assert updated_at == datetime(2026, 3, 29, 12, 6, 53, tzinfo=UTC)
    remote_script = str(captured["input"])
    assert 'codex_state_path="${LOG_DIR}/codex-resume-last.state"' in remote_script
    assert 'active_entries+=("codex-heartbeat")' in remote_script


def test_get_agent_log_snapshot_accepts_codex_heartbeat_log(tmp_path: Path, monkeypatch) -> None:
    service = AdminService(write_config(tmp_path))
    monkeypatch.setattr("agent_sandbox.admin_service._dominfo_field", lambda vm_name, field_name: "running")
    monkeypatch.setattr("agent_sandbox.admin_service._resolve_vm_ip", lambda vm_name: "192.168.122.11")
    monkeypatch.setattr("agent_sandbox.admin_service._resolve_private_key_path", lambda: Path("/tmp/test-key"))

    captured: dict[str, object] = {}

    def _fake_run_subprocess(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured["cmd"] = cmd
        captured["input"] = kwargs.get("input")
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=0,
            stdout="Repo: codex-heartbeat\nLog: /home/agent/.agent-sandbox/codex-heartbeat-run.log\n",
            stderr="",
        )

    monkeypatch.setattr("agent_sandbox.admin_service._run_subprocess", _fake_run_subprocess)

    output = service.get_agent_log_snapshot("bug-finder-vm")

    assert "Repo: codex-heartbeat" in output
    assert "codex-heartbeat-run.log" in output
    remote_script = str(captured["input"])
    assert 'candidates+=("codex-heartbeat")' in remote_script
    assert 'LOG_PATH="${LOG_DIR}/codex-heartbeat-run.log"' in remote_script


def test_list_sessions_filters_active_and_expired(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path))
    service.runtime.sessions.put(
        "issue-123-active",
        SessionManifest(
            session_id="issue-123-active",
            vm_id="vm-123",
            model="gpt-5.4",
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
    assert active[0].model == "gpt-5.4"
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


def test_list_issues_sorts_issue_numbers_numerically(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path))
    low_path = tmp_path / "issue-2.yaml"
    low_path.write_text("issue_number: 2\ntitle: Low issue\nbody: body\n", encoding="utf-8")
    high_path = tmp_path / "issue-10.yaml"
    high_path.write_text("issue_number: 10\ntitle: High issue\nbody: body\n", encoding="utf-8")

    service.create_local_issue(yaml_path=str(high_path))
    service.create_local_issue(yaml_path=str(low_path))

    issues = service.list_issues()

    assert [issue.issue_number for issue in issues] == ["2", "10"]


def test_list_local_issues_supports_state_filters(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path))
    active_issue = service.create_local_issue(title="Active", body="body")
    completed_issue = service.create_local_issue(title="Completed", body="body")
    closed_issue = service.create_local_issue(title="Closed", body="body")

    service.update_issue(active_issue.issue_number, state="In Progress")
    service.update_issue(completed_issue.issue_number, state="Completed")
    service.update_issue(closed_issue.issue_number, state="Closed")

    active_only = service.list_local_issues(exclude_states={"Completed", "Closed"})
    completed_only = service.list_local_issues(states={"Completed"})

    assert [issue.title for issue in active_only] == ["Active"]
    assert [issue.title for issue in completed_only] == ["Completed"]


def test_list_approval_requests_filters_health_and_redacts_payload(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path, enable_directory_scoped_config=True))
    created = service.create_directory_child("root", "team")
    assert created.ok is True and created.data is not None
    team_path = created.data.details.directory.path
    service.runtime.sessions.put(
        "team-session",
        SessionManifest(
            session_id="team-session",
            vm_id="vm-42",
            directory_path=team_path,
            allowed_tools=[ToolName.APPROVAL_SUBMIT],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    service.runtime.approvals.put(
        "approval-pending",
        ApprovalRequest(
            request_id="approval-pending",
            session_id="team-session",
            action_type="email_send_approved",
            tool_name="email_send_approved",
            payload_digest="abc",
            human_summary="send email",
            payload={"token": "secret", "body": "ok"},
            directory_path=team_path,
            vm_id="vm-42",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    service.runtime.approvals.put(
        "approval-denied",
        ApprovalRequest(
            request_id="approval-denied",
            session_id="team-session",
            action_type="email_send_approved",
            tool_name="email_send_approved",
            payload_digest="def",
            human_summary="already denied",
            payload={"body": "blocked"},
            directory_path=team_path,
            vm_id="vm-42",
            status=ApprovalStatus.DENIED,
            approver="ops",
            decision_at=datetime.now(UTC),
            decision_reason="unsafe",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )

    pending = service.list_approval_requests(
        statuses={ApprovalStatus.PENDING},
        directory_path=team_path,
        direct_only=True,
        tool_name="email_send_approved",
    )
    health = service.approval_queue_health(directory_path=team_path, direct_only=True)
    review = service.get_approval_review("approval-pending")

    assert [request.request_id for request in pending] == ["approval-pending"]
    assert health.total == 2
    assert health.pending == 1
    assert health.denied == 1
    assert review["redacted_payload"]["token"] == "<redacted>"
    assert review["request"]["payload"]["token"] == "<redacted>"


def test_decide_approval_request_records_reason_history_and_audit(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path))
    service.runtime.sessions.put(
        "session-1",
        SessionManifest(
            session_id="session-1",
            vm_id="vm-1",
            allowed_tools=[ToolName.APPROVAL_SUBMIT],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    service.runtime.approvals.put(
        "approval-1",
        ApprovalRequest(
            request_id="approval-1",
            session_id="session-1",
            action_type="email_send_approved",
            tool_name="email_send_approved",
            payload_digest="abc",
            human_summary="send email",
            payload={"body": "hello"},
            vm_id="vm-1",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )

    decided = service.decide_approval_request("approval-1", approver="alice", approve=False, reason="unsafe")
    audit_lines = (tmp_path / "runtime" / "audit.jsonl").read_text(encoding="utf-8").splitlines()
    audit_payload = json.loads(audit_lines[-1])

    assert decided.status == ApprovalStatus.DENIED
    assert decided.decision_reason == "unsafe"
    assert decided.decision_history[0].reason == "unsafe"
    assert audit_payload["event_type"] == "approval_decision"
    assert audit_payload["args"]["request_id"] == "approval-1"
    assert audit_payload["reason"] == "unsafe"


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
            "host_heartbeat_scheduled_wake_prefix_text": "Scheduled wake prefix.",
            "host_heartbeat_schedule_max_active_per_session": 3,
            "host_heartbeat_schedule_max_active_total": 20,
            "host_heartbeat_schedule_min_interval_seconds": 120.0,
        },
    )
    assert write_result.config["host_heartbeat_stream_conflict_policy"] == "reject"
    assert write_result.config["host_heartbeat_scheduled_wake_prefix_text"] == "Scheduled wake prefix."

    config_path = tmp_path / "config" / "host-heartbeat" / "config.json"
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    assert payload["host_heartbeat_directory_paths"] == ["root/team-a", "root/team-b"]
    assert payload["host_heartbeat_poll_interval_seconds"] == 12.5
    assert payload["host_heartbeat_stream_conflict_policy"] == "reject"
    assert payload["host_heartbeat_vm_user"] == "worker"
    assert payload["host_heartbeat_schedule_max_active_per_session"] == 3
    assert payload["host_heartbeat_schedule_max_active_total"] == 20
    assert payload["host_heartbeat_schedule_min_interval_seconds"] == 120.0

    reread = service.read_worker_global_config("host-heartbeat")
    assert reread.config["host_heartbeat_directory_paths"] == ["root/team-a", "root/team-b"]
    assert reread.config["host_heartbeat_stream_conflict_policy"] == "reject"
    assert reread.config["host_heartbeat_scheduled_wake_prefix_text"] == "Scheduled wake prefix."


def test_admin_service_reads_and_writes_discord_ingest_worker_global_config(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path))

    read_result = service.read_worker_global_config("discord-ingest")
    assert read_result.exists is False
    assert read_result.path == str(tmp_path / "config" / "discord-ingest" / "config.json")
    assert read_result.config["poll_interval_seconds"] == 30.0
    assert read_result.config["directories"] == []
    assert read_result.config["backoff_seconds"] == 60.0

    write_result = service.write_worker_global_config(
        "discord-ingest",
        {
            "runtime_dir": str(tmp_path / "runtime"),
            "directories": ["root/team-a", "root/team-b"],
            "poll_interval_seconds": 12.5,
            "jitter_seconds": 1.5,
            "backoff_seconds": 45.0,
            "lock_path": None,
        },
    )
    assert write_result.config["directories"] == ["root/team-a", "root/team-b"]
    assert write_result.config["poll_interval_seconds"] == 12.5

    config_path = tmp_path / "config" / "discord-ingest" / "config.json"
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    assert payload["directories"] == ["root/team-a", "root/team-b"]
    assert payload["poll_interval_seconds"] == 12.5
    assert payload["backoff_seconds"] == 45.0

    reread = service.read_worker_global_config("discord-ingest")
    assert reread.config["directories"] == ["root/team-a", "root/team-b"]
    assert reread.config["backoff_seconds"] == 45.0


def test_admin_service_can_read_and_mutate_trigger_orchestrator_triggers_by_id(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path))

    read_result = service.read_worker_global_config("trigger-orchestrator")
    assert read_result.exists is False
    assert read_result.path == str(tmp_path / "config" / "trigger-orchestrator" / "config.json")
    assert read_result.config["triggers"] == []
    assert read_result.config["effects"] == []

    added = service.upsert_trigger_config(
        "cron-15m",
        {
            "display_name": "Every 15 minutes",
            "type": "trigger:cron",
            "expression": "*/15 * * * *",
        },
    )
    assert added.operation == "added"
    assert added.config["triggers"][0]["id"] == "cron-15m"

    updated = service.upsert_trigger_config(
        "cron-15m",
        {
            "display_name": "Every 30 minutes",
            "type": "trigger:cron",
            "expression": "*/30 * * * *",
        },
    )
    assert updated.operation == "updated"
    assert updated.config["triggers"] == [
        {
            "id": "cron-15m",
            "display_name": "Every 30 minutes",
            "type": "trigger:cron",
            "expression": "*/30 * * * *",
        }
    ]

    removed = service.delete_trigger_config("cron-15m")
    assert removed.operation == "deleted"
    assert removed.config["triggers"] == []


def test_admin_service_can_read_and_mutate_trigger_orchestrator_effects_by_id(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path))
    service.upsert_trigger_config(
        "cron-15m",
        {
            "display_name": "Every 15 minutes",
            "type": "trigger:cron",
            "expression": "*/15 * * * *",
        },
    )

    added = service.upsert_effect_config(
        "prompt-agent",
        {
            "display_name": "Prompt agent",
            "type": "effect:agent:prompt",
            "source_id": "cron-15m",
            "prompt": "Summarize",
        },
    )
    assert added.operation == "added"
    assert added.config["effects"][0]["id"] == "prompt-agent"
    assert added.config["edges"] == [{"source_id": "cron-15m", "target_id": "prompt-agent"}]

    updated = service.upsert_effect_config(
        "prompt-agent",
        {
            "display_name": "Prompt agent updated",
            "type": "effect:agent:prompt",
            "source_id": "cron-15m",
            "prompt": "Summarize latest",
        },
    )
    assert updated.operation == "updated"
    assert updated.config["effects"] == [
        {
            "id": "prompt-agent",
            "display_name": "Prompt agent updated",
            "type": "effect:agent:prompt",
            "prompt": "Summarize latest",
            "output_mode": "immediate",
        }
    ]
    assert updated.config["edges"] == [{"source_id": "cron-15m", "target_id": "prompt-agent"}]

    removed = service.delete_effect_config("prompt-agent")
    assert removed.operation == "deleted"
    assert removed.config["effects"] == []
    assert removed.config["edges"] == []


def test_admin_service_rejects_duplicate_trigger_ids_with_safe_error_message(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path))

    with pytest.raises(
        ValueError,
        match="Node IDs must be globally unique across triggers, gates, and effects: dup",
    ):
        service.write_worker_global_config(
            "trigger-orchestrator",
            {
                "runtime_dir": str(tmp_path / "runtime"),
                "triggers": [
                    {
                        "id": "dup",
                        "display_name": "First",
                        "type": "trigger:cron",
                        "expression": "*/15 * * * *",
                    },
                    {
                        "id": "dup",
                        "display_name": "Second",
                        "type": "trigger:cron",
                        "expression": "*/30 * * * *",
                    },
                ],
            },
        )

    with pytest.raises(ValueError) as excinfo:
        service.upsert_trigger_config(
            "email-invalid",
            {
                "display_name": "Bad email provider",
                "type": "trigger:email:new-message",
                "provider": "not-real",
                "account_identifier": "acct",
                "mailbox_ids": ["inbox"],
                "auth_secret_refs": {"token": "VERY_SECRET_TOKEN"},
            },
        )
    message = str(excinfo.value)
    assert "Invalid trigger config:" in message
    assert "Unsupported email provider" in message
    assert "VERY_SECRET_TOKEN" not in message


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
        model="gpt-5.2-codex",
    )

    assert result.success is True
    assert captured_env["GATEWAY_URL"] == "http://10.20.30.40:8000/mcp"
    assert captured_env["LLM_BASE_URL"] == "http://10.20.30.40:12000/v1"
    assert captured_env["NUGET_RELAY_BASE_URL"] == "http://10.20.30.40:13000"


def test_create_issue_vm_uses_issue_directory_when_called_with_yaml_path(tmp_path: Path, monkeypatch) -> None:
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
    issue = service.create_local_issue(title="Team issue", body="body", directory_path=team_path)

    captured_env: dict[str, str] = {}

    def fake_run(*args, **kwargs):
        captured_env.update(kwargs.get("env", {}))
        return subprocess.CompletedProcess(args[0], 0, stdout="", stderr="")

    monkeypatch.setattr("agent_sandbox.admin_service.subprocess.run", fake_run)

    result = service.create_issue_vm(
        issue=issue.yaml_path,
        session_id="issue-1-session",
        start_agent=False,
        model="gpt-5.2-codex",
    )

    manifest = service.runtime.sessions.get("issue-1-session")
    assert result.success is True
    assert manifest is not None
    assert manifest.directory_path == team_path
    assert captured_env["GATEWAY_URL"] == "http://10.20.30.40:8000/mcp"


def test_create_issue_vm_uses_yaml_path_owner_when_issue_number_is_ambiguous(tmp_path: Path, monkeypatch: Any) -> None:
    service = AdminService(write_config(tmp_path, enable_directory_scoped_config=True))
    team_a = service.create_directory_child("root", "team-a")
    team_b = service.create_directory_child("root", "team-b")
    assert team_a.ok is True and team_a.data is not None
    assert team_b.ok is True and team_b.data is not None
    team_a_path = team_a.data.details.directory.path
    team_b_path = team_b.data.details.directory.path

    assert service.write_directory_domain_config(
        team_a_path,
        "vm",
        {
            "gateway_url": "http://10.20.30.41:8000/mcp",
            "openai_relay_port": 12001,
            "nuget_relay_port": 13001,
        },
    ).ok is True
    assert service.write_directory_domain_config(
        team_b_path,
        "vm",
        {
            "gateway_url": "http://10.20.30.42:8000/mcp",
            "openai_relay_port": 12002,
            "nuget_relay_port": 13002,
        },
    ).ok is True

    (tmp_path / "project-a").mkdir()
    (tmp_path / "project-b").mkdir()

    project_a_source = tmp_path / "project-a" / "proxy-issue-42.yaml"
    project_a_source.write_text(
        "\n".join(
            [
                "issue_number: 42",
                'title: "Project A issue"',
                'description: "from project a"',
                'state: "New"',
                'url: "https://ado.example/work/42"',
                "source:",
                '  organization: "https://dev.azure.com/example"',
                '  project: "project-a"',
                '  work_item_url: "https://dev.azure.com/example/project-a/_workitems/edit/42"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    project_b_source = tmp_path / "project-b" / "proxy-issue-42.yaml"
    project_b_source.write_text(
        "\n".join(
            [
                "issue_number: 42",
                'title: "Project B issue"',
                'description: "from project b"',
                'state: "New"',
                'url: "https://ado.example/work/42"',
                "source:",
                '  organization: "https://dev.azure.com/example"',
                '  project: "project-b"',
                '  work_item_url: "https://dev.azure.com/example/project-b/_workitems/edit/42"',
                "",
            ]
        ),
        encoding="utf-8",
    )

    project_a_issue = service.create_local_issue(
        directory_path=team_a_path,
        yaml_path=str(project_a_source),
    )
    service.create_local_issue(
        directory_path=team_b_path,
        yaml_path=str(project_b_source),
    )

    captured_env: dict[str, str] = {}

    def fake_run(*args, **kwargs):
        captured_env.update(kwargs.get("env", {}))
        return subprocess.CompletedProcess(args[0], 0, stdout="", stderr="")

    monkeypatch.setattr("agent_sandbox.admin_service.subprocess.run", fake_run)

    result = service.create_issue_vm(
        issue=project_a_issue.yaml_path,
        session_id="issue-42-session",
        start_agent=False,
        model="gpt-5.2-codex",
    )

    manifest = service.runtime.sessions.get("issue-42-session")
    assert result.success is True
    assert manifest is not None
    assert manifest.directory_path == team_a_path
    assert captured_env["GATEWAY_URL"] == "http://10.20.30.41:8000/mcp"


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
        model="gpt-5.2-codex",
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
        model="gpt-5.2-codex",
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
        model="gpt-5.2-codex",
    )

    assert result.success is True
    assert captured_env["AGENT_SANDBOX_GIT_ROOT"] == "/tmp/from-env"
    assert captured_env["AGENT_SANDBOX_GIT_HOST"] == "10.55.0.1"
    assert captured_env["AGENT_SANDBOX_GIT_USER"] == "env-user"
    assert captured_env["AGENT_SANDBOX_GIT_SSH_ALIAS"] == "env-git"
    assert captured_env["AGENT_SANDBOX_AZURE_DEVOPS_PR_PAT_ENV"] == "ENV_PAT_NAME"
    assert captured_env["AGENT_POST_EXEC_VERIFICATION_PROMPT"] == "verify from env"


def test_resolve_agent_model_prefers_session_model_over_issue_setup_default(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path, enable_directory_scoped_config=True))
    service.write_directory_domain_config("root", "issue_setup", {"default_model": "gpt-5.2-codex"})
    manifest = SessionManifest(
        session_id="session-1",
        vm_id="vm-123",
        directory_path="root",
        allowed_tools=[],
        model="gpt-5.3-codex",
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    service.runtime.sessions.put(manifest.session_id, manifest)

    resolved = service._resolve_agent_model_for_session("session-1")

    assert resolved == "gpt-5.3-codex"


def test_resolve_agent_model_uses_issue_setup_default_when_session_model_missing(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path, enable_directory_scoped_config=True))
    service.write_directory_domain_config("root", "issue_setup", {"default_model": "gpt-5.2-codex"})
    manifest = SessionManifest(
        session_id="session-1",
        vm_id="vm-123",
        directory_path="root",
        allowed_tools=[],
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    service.runtime.sessions.put(manifest.session_id, manifest)

    resolved = service._resolve_agent_model_for_session("session-1")

    assert resolved == "gpt-5.2-codex"


def test_resolve_agent_model_fails_when_session_and_issue_setup_models_missing(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path, enable_directory_scoped_config=True))
    manifest = SessionManifest(
        session_id="session-1",
        vm_id="vm-123",
        directory_path="root",
        allowed_tools=[],
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    service.runtime.sessions.put(manifest.session_id, manifest)

    with pytest.raises(ValueError, match="missing session.model and missing issue_setup.default_model"):
        service._resolve_agent_model_for_session("session-1")


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
        model="gpt-5.2-codex",
    )

    assert result.success is True
    assert captured_env["AGENT_SANDBOX_AZURE_DEVOPS_PR_PAT_ENV"] == "AZURE_DEVOPS_PR_PAT"


def test_create_issue_vm_deletes_session_when_setup_fails_before_vm_exists(tmp_path: Path, monkeypatch: Any) -> None:
    service = AdminService(write_config(tmp_path))
    issue = service.create_local_issue(title="Issue VM", body="create and fail")
    monkeypatch.setattr("agent_sandbox.admin_service._dominfo_field", lambda vm_name, field_name: None)
    monkeypatch.setattr(
        "agent_sandbox.admin_service._run_subprocess",
        lambda *args, **kwargs: subprocess.CompletedProcess(args=args[0], returncode=17, stdout="", stderr="boom"),
    )

    result = service.create_issue_vm(
        issue=issue.issue_number,
        session_id="issue-1-session",
        start_agent=False,
        model="gpt-5.2-codex",
    )

    assert result.success is False
    assert result.message == "boom"
    assert service.runtime.sessions.get("issue-1-session") is None


def test_create_issue_vm_deletes_session_when_dominfo_probe_fails(tmp_path: Path, monkeypatch: Any) -> None:
    service = AdminService(write_config(tmp_path))
    issue = service.create_local_issue(title="Issue VM", body="create and fail")
    monkeypatch.setattr(
        "agent_sandbox.admin_service._dominfo_field",
        lambda vm_name, field_name: (_ for _ in ()).throw(RuntimeError("virsh unavailable")),
    )
    monkeypatch.setattr(
        "agent_sandbox.admin_service._run_subprocess",
        lambda *args, **kwargs: subprocess.CompletedProcess(args=args[0], returncode=17, stdout="", stderr="boom"),
    )

    result = service.create_issue_vm(
        issue=issue.issue_number,
        session_id="issue-1-session",
        start_agent=False,
        model="gpt-5.2-codex",
    )

    assert result.success is False
    assert result.message == "boom"
    assert service.runtime.sessions.get("issue-1-session") is None


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


def test_set_session_allowed_tools_updates_manifest(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path))
    service.runtime.sessions.put(
        "issue-13302-session",
        SessionManifest(
            session_id="issue-13302-session",
            vm_id="vm-13302",
            allowed_tools=[ToolName.CLAIM_NEXT_ISSUE],
            expires_at=datetime.now(UTC) + timedelta(hours=2),
        ),
    )

    result = service.set_session_allowed_tools(
        "13302",
        [ToolName.COMPLETE_ISSUE.value, ToolName.COMPLETE_ISSUE.value, ToolName.APPROVAL_CHECK.value],
    )

    updated = service.runtime.sessions.get("issue-13302-session")
    assert result.success is True
    assert result.session_id == "issue-13302-session"
    assert updated is not None
    assert updated.allowed_tools == [ToolName.COMPLETE_ISSUE, ToolName.APPROVAL_CHECK]


def test_set_session_model_updates_manifest(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path))
    service.runtime.sessions.put(
        "issue-13303-session",
        SessionManifest(
            session_id="issue-13303-session",
            vm_id="vm-13303",
            allowed_tools=[ToolName.CLAIM_NEXT_ISSUE],
            expires_at=datetime.now(UTC) + timedelta(hours=2),
        ),
    )

    result = service.set_session_model("13303", "  gpt-5.5  ")

    updated = service.runtime.sessions.get("issue-13303-session")
    assert result.success is True
    assert result.session_id == "issue-13303-session"
    assert updated is not None
    assert updated.model == "gpt-5.5"


def test_set_session_model_rejects_empty_value(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path))
    service.runtime.sessions.put(
        "issue-13304-session",
        SessionManifest(
            session_id="issue-13304-session",
            vm_id="vm-13304",
            allowed_tools=[],
            expires_at=datetime.now(UTC) + timedelta(hours=2),
        ),
    )

    with pytest.raises(ValueError, match="model must be non-empty"):
        service.set_session_model("13304", "   ")


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
        "agent-sandbox-discord-ingest.service": {
            "ActiveState": "active",
            "SubState": "running",
            "Result": "success",
            "UnitFileState": "enabled",
            "Description": "Discord ingest worker",
            "MainPID": "456",
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
        "agent-sandbox-issue-orchestrator.service": {
            "ActiveState": "inactive",
            "SubState": "dead",
            "Result": "success",
            "UnitFileState": "disabled",
            "Description": "Issue orchestrator worker",
            "MainPID": "0",
            "LoadState": "loaded",
            "ExecMainCode": "1",
            "ExecMainStatus": "0",
        },
        "agent-sandbox-trigger-orchestrator.service": {
            "ActiveState": "active",
            "SubState": "running",
            "Result": "success",
            "UnitFileState": "enabled",
            "Description": "Trigger orchestrator worker",
            "MainPID": "789",
            "LoadState": "loaded",
            "ExecMainCode": "1",
            "ExecMainStatus": "0",
        },
    }
    monkeypatch.setattr("agent_sandbox.admin_service._systemctl_show", lambda unit: payloads[unit])

    summaries = service.list_background_workers()

    assert [summary.worker_id for summary in summaries] == [
        "ado",
        "discord-ingest",
        "host-heartbeat",
        "issue-orchestrator",
        "trigger-orchestrator",
    ]
    assert summaries[0].running is True
    assert summaries[0].failed is False
    assert summaries[0].main_pid == 123
    assert summaries[0].unit_file_state == "enabled"
    assert summaries[1].running is True
    assert summaries[1].failed is False
    assert summaries[1].main_pid == 456
    assert summaries[2].running is False
    assert summaries[2].failed is True
    assert summaries[2].detail == "load=loaded, exec=1:2"
    assert summaries[3].running is False
    assert summaries[3].failed is False
    assert summaries[3].unit_file_state == "disabled"
    assert summaries[4].running is True
    assert summaries[4].failed is False
    assert summaries[4].main_pid == 789


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


def test_issue_actions_use_local_behavior_for_non_proxy_issue(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path))
    issue = service.create_local_issue(title="Local", body="only local")

    comment_result = service.post_issue_comment(issue.issue_number, "hello local")
    update_result = service.update_issue(issue.issue_number, state="Completed", fields={"priority": "high"})
    read_result = service.read_issue(issue.issue_number)

    assert comment_result["source_type"] == "local"
    assert update_result["source_type"] == "local"
    assert read_result["source_type"] == "local"
    assert read_result["state"] == "Completed"
    assert read_result["comments"][-1]["text"] == "hello local"


def test_get_issue_returns_full_local_record_for_closed_issue(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path))
    issue = service.create_local_issue(title="Closed task", body="only local")
    service.update_issue(issue.issue_number, state="Closed", fields={"Reason": "Out of scope"})

    result = service.get_issue(issue.issue_number)

    assert result["issue_id"] == issue.issue_id
    assert result["issue_number"] == issue.issue_number
    assert result["source_type"] == "local"
    assert result["directory_path"] == "root"
    assert result["state"] == "Closed"
    assert result["reason"] == "Out of scope"
    assert result["rejection_reason"] == "Out of scope"
    assert result["yaml_path"] == issue.yaml_path


def test_create_local_issue_defaults_to_new_state(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path))

    issue = service.create_local_issue(title="Local", body="only local")

    stored = service.local_issue_store.get(issue.issue_id)
    assert stored is not None
    assert stored.state == "New"


def test_update_issue_rejects_invalid_local_state(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path))
    issue = service.create_local_issue(title="Local", body="only local")

    with pytest.raises(ValueError, match="Invalid issue state"):
        service.update_issue(issue.issue_number, state="Done")


def test_update_issue_accepts_approved_state(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path))
    issue = service.create_local_issue(title="Local", body="only local")

    result = service.update_issue(issue.issue_number, state="Approved", fields={"Priority": "P1"})

    stored = service.local_issue_store.get(issue.issue_id)
    assert result["source_type"] == "local"
    assert stored is not None
    assert stored.state == "Approved"
    assert stored.metadata["fields"]["Priority"] == "P1"


def test_update_issue_updates_local_yaml_file(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path))
    issue = service.create_local_issue(title="Task", body="only local")
    original_yaml = Path(issue.yaml_path).read_text(encoding="utf-8")

    result = service.update_issue(issue.issue_number, state="Committed", fields={"Priority": "P1"})

    updated_yaml = Path(issue.yaml_path).read_text(encoding="utf-8")
    assert result["source_type"] == "local"
    assert updated_yaml != original_yaml
    assert "state: Committed" in updated_yaml
    assert "Priority: P1" in updated_yaml


def test_issue_actions_reject_ambiguous_bare_number_across_directories(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path, enable_directory_scoped_config=True))
    created = service.create_directory_child("root", "team")
    assert created.ok is True and created.data is not None
    team_path = created.data.details.directory.path

    root_yaml = tmp_path / "root-issue.yaml"
    root_yaml.write_text("issue_number: 7\ntitle: Root issue\nbody: Root body\n", encoding="utf-8")
    team_yaml = tmp_path / "team-issue.yaml"
    team_yaml.write_text("issue_number: 7\ntitle: Team issue\nbody: Team body\n", encoding="utf-8")

    root_issue = service.create_local_issue(yaml_path=str(root_yaml), directory_path="root")
    team_issue = service.create_local_issue(yaml_path=str(team_yaml), directory_path=team_path)

    with pytest.raises(ValueError, match=r"Ambiguous local issue identifier: 7\."):
        service.read_issue("7")
    with pytest.raises(ValueError, match=r"Ambiguous local issue identifier: 7\."):
        service.update_issue("7", state="Completed")
    with pytest.raises(ValueError, match=r"Ambiguous local issue identifier: 7\."):
        service.update_local_issue(
            "7",
            title="Edited through bare number",
            body="Edited body",
            state="In Progress",
        )

    root_read = service.read_issue(root_issue.issue_id)
    team_updated = service.update_local_issue(
        team_issue.issue_id,
        title="Edited team issue",
        body="Updated team body",
        state="In Progress",
    )

    assert root_read["title"] == "Root issue"
    assert team_updated.issue_id == team_issue.issue_id
    assert service.local_issue_store.get(root_issue.issue_id).title == "Root issue"


def test_vm_and_delete_actions_reject_ambiguous_bare_number_across_directories(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path, enable_directory_scoped_config=True))
    created = service.create_directory_child("root", "team")
    assert created.ok is True and created.data is not None
    team_path = created.data.details.directory.path

    root_yaml = tmp_path / "root-issue.yaml"
    root_yaml.write_text("issue_number: 7\ntitle: Root issue\nbody: Root body\n", encoding="utf-8")
    team_yaml = tmp_path / "team-issue.yaml"
    team_yaml.write_text("issue_number: 7\ntitle: Team issue\nbody: Team body\n", encoding="utf-8")

    service.create_local_issue(yaml_path=str(root_yaml), directory_path="root")
    service.create_local_issue(yaml_path=str(team_yaml), directory_path=team_path)

    create_result = service.create_issue_vm_from_local_issue("7", start_agent=False)
    delete_result = service.delete_local_issue("7")

    assert create_result.success is False
    assert delete_result.success is False
    assert create_result.message.startswith("Ambiguous local issue identifier: 7.")
    assert delete_result.message.startswith("Ambiguous local issue identifier: 7.")


def test_create_issue_vm_rejects_ambiguous_bare_local_issue_number_in_primary_path(
    tmp_path: Path, monkeypatch: Any
) -> None:
    service = AdminService(write_config(tmp_path, enable_directory_scoped_config=True))
    created = service.create_directory_child("root", "team")
    assert created.ok is True and created.data is not None
    team_path = created.data.details.directory.path

    root_yaml = tmp_path / "root-issue.yaml"
    root_yaml.write_text("issue_number: 7\ntitle: Root issue\nbody: Root body\n", encoding="utf-8")
    team_yaml = tmp_path / "team-issue.yaml"
    team_yaml.write_text("issue_number: 7\ntitle: Team issue\nbody: Team body\n", encoding="utf-8")

    service.create_local_issue(yaml_path=str(root_yaml), directory_path="root")
    service.create_local_issue(yaml_path=str(team_yaml), directory_path=team_path)

    def _fail_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise AssertionError("create_issue_vm should reject ambiguous identifiers before invoking setup")

    monkeypatch.setattr("agent_sandbox.admin_service.subprocess.run", _fail_run)

    result = service.create_issue_vm(issue="7", start_agent=False)

    assert result.success is False
    assert result.message.startswith("Ambiguous local issue identifier: 7.")


def test_claim_next_issue_prefers_highest_priority_committed_issue(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path))
    low = service.create_local_issue(title="Low", body="x")
    high = service.create_local_issue(title="High", body="x")
    service.update_issue(low.issue_number, state="Committed", fields={"Priority": "P2"})
    service.update_issue(high.issue_number, state="Committed", fields={"Priority": "P0"})
    service.runtime.sessions.put(
        "worker-session",
        SessionManifest(
            session_id="worker-session",
            vm_id="issue-worker-1-vm",
            directory_path="root",
            allowed_tools=[ToolName.CLAIM_NEXT_ISSUE],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )

    claimed = service.claim_next_issue(session_id="worker-session")

    assert claimed is not None
    assert claimed.issue_number == high.issue_number
    assert claimed.state == "In Progress"
    assert claimed.metadata["fields"]["claimed_by_session_id"] == "worker-session"
    assert claimed.metadata["fields"]["claimed_by_vm_id"] == "issue-worker-1-vm"


def test_complete_issue_marks_claimed_issue_completed(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path))
    issue = service.create_local_issue(title="Task", body="x")
    service.update_issue(issue.issue_number, state="Committed", fields={"Priority": "P1"})
    service.runtime.sessions.put(
        "worker-session",
        SessionManifest(
            session_id="worker-session",
            vm_id="issue-worker-1-vm",
            directory_path="root",
            allowed_tools=[ToolName.CLAIM_NEXT_ISSUE, ToolName.COMPLETE_ISSUE],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    claimed = service.claim_next_issue(session_id="worker-session")
    assert claimed is not None

    completed = service.complete_issue(
        session_id="worker-session",
        issue_id=claimed.issue_number,
        summary="Implemented and pushed",
    )

    assert completed.state == "Completed"
    assert completed.metadata["fields"]["completed_summary"] == "Implemented and pushed"
    assert completed.metadata["fields"]["completed_by_session_id"] == "worker-session"


def test_create_local_issue_from_ado_yaml_creates_proxy_record_and_repoll_updates_without_duplicate(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path))
    first_yaml = _write_proxy_issue_yaml(
        tmp_path,
        issue_number=42,
        title="Proxy issue",
        description="from remote",
        state="New",
    )
    updated_yaml = _write_proxy_issue_yaml(
        tmp_path,
        issue_number=42,
        title="Proxy issue updated",
        description="updated body",
        state="Active",
    )

    created = service.create_local_issue(yaml_path=str(first_yaml))
    updated = service.create_local_issue(yaml_path=str(updated_yaml))

    assert created.issue_number == "42"
    assert updated.issue_number == "42"
    issues = service.list_local_issues()
    assert [issue.issue_number for issue in issues] == ["42"]
    stored = service.local_issue_store.get(created.issue_id)
    assert stored is not None
    assert stored.source == "proxy_azure_devops"
    assert stored.source_type == "remote_proxy"
    assert stored.remote_reference is not None
    assert stored.remote_reference.provider == "azure_devops"
    assert stored.remote_reference.work_item_id == "42"
    assert stored.state == "Active"


def test_issue_actions_forward_proxy_issues_and_sync_cache(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path))
    issue = service.create_local_issue(
        title="Proxy issue",
        body="from remote",
        yaml_path=str(_write_proxy_issue_yaml(tmp_path)),
    )

    calls: list[tuple[str, dict[str, Any]]] = []

    class _Adapter:
        provider_name = "azure_devops"

        def read_issue(self, **kwargs):
            calls.append(("read", kwargs))
            return {
                "provider": "azure_devops",
                "remote_issue_id": kwargs["remote_issue_id"],
                "title": "Remote title",
                "description": "Remote description",
                "state": "Active",
                "url": "https://ado.example/work/42",
                "updated_at": "2026-03-28T10:11:12Z",
                "comments": [{"id": "1", "text": "remote comment"}],
                "fields": {"System.State": "Active"},
            }

        def post_comment(self, **kwargs):
            calls.append(("comment", kwargs))
            return {"provider": "azure_devops", "remote_issue_id": kwargs["remote_issue_id"], "comment": {"id": "9"}}

        def update_issue(self, **kwargs):
            calls.append(("update", kwargs))
            return {"provider": "azure_devops", "remote_issue_id": kwargs["remote_issue_id"], "updated": True}

    service.issue_service.adapters["azure_devops"] = _Adapter()

    read_result = service.read_issue(issue.issue_number, include_remote_comments=True, sync_proxy_cache=True)
    comment_result = service.post_issue_comment(issue.issue_number, "push to remote", sync_proxy_cache=True)
    update_result = service.update_issue(issue.issue_number, state="Resolved", fields={"Custom.Priority": "1"})

    assert read_result["source_type"] == "proxy"
    assert comment_result["source_type"] == "proxy"
    assert update_result["source_type"] == "proxy"
    stored = service.local_issue_store.get(issue.issue_id)
    assert stored is not None
    assert stored.title == "Remote title"
    assert stored.state == "Active"
    comment_call = next(kwargs for name, kwargs in calls if name == "comment")
    update_call = next(kwargs for name, kwargs in calls if name == "update")
    assert comment_call["remote_issue_id"] == issue.issue_number
    assert update_call["state"] == "Resolved"


def test_get_issue_retains_local_metadata_for_proxy_issue(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path))
    issue = service.create_local_issue(
        title="Proxy issue",
        body="from remote",
        yaml_path=str(_write_proxy_issue_yaml(tmp_path)),
    )

    class _Adapter:
        provider_name = "azure_devops"

        def read_issue(self, **kwargs):
            return {
                "provider": "azure_devops",
                "remote_issue_id": kwargs["remote_issue_id"],
                "title": "Remote title",
                "description": "Remote description",
                "state": "Closed",
                "comments": [],
                "fields": {"Reason": "Remote reject"},
            }

    service.issue_service.adapters["azure_devops"] = _Adapter()

    result = service.get_issue(issue.issue_number, include_remote_comments=True, sync_proxy_cache=True)

    assert result["issue_id"] == issue.issue_id
    assert result["issue_number"] == issue.issue_number
    assert result["source_type"] == "proxy"
    assert result["directory_path"] == issue.directory_path
    assert result["yaml_path"] == issue.yaml_path
    assert result["state"] == "Closed"


def test_proxy_identifier_rejects_ambiguous_ado_work_item_ids_across_projects(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path))
    project_a_issue = service.create_local_issue(
        yaml_path=str(
            _write_proxy_issue_yaml(
                tmp_path,
                issue_number=42,
                title="Project A issue",
                description="from project a",
                project="project-a",
            )
        )
    )
    project_b_issue = service.create_local_issue(
        yaml_path=str(
            _write_proxy_issue_yaml(
                tmp_path,
                issue_number=42,
                title="Project B issue",
                description="from project b",
                project="project-b",
            )
        )
    )

    with pytest.raises(ValueError, match=r"Ambiguous proxy issue identifier: proxy:ado/42\."):
        service.read_issue("proxy:ado/42")
    with pytest.raises(ValueError, match=r"Ambiguous proxy issue identifier: proxy:ado/42\."):
        service.update_issue("proxy:ado/42", state="Resolved")
    with pytest.raises(ValueError, match=r"Ambiguous proxy issue identifier: proxy:ado/42\."):
        service.post_issue_comment("proxy:ado/42", "push to remote")

    assert service._resolve_local_issue_record(project_a_issue.issue_id).issue_id == project_a_issue.issue_id
    assert service._resolve_local_issue_record(project_b_issue.issue_id).issue_id == project_b_issue.issue_id


def test_create_issue_vm_accepts_unified_proxy_identifier(tmp_path: Path, monkeypatch: Any) -> None:
    service = AdminService(write_config(tmp_path))
    issue = service.create_local_issue(
        yaml_path=str(
            _write_proxy_issue_yaml(
                tmp_path,
                issue_number=77,
                title="Proxy setup",
                description="from ado",
                state="Active",
            )
        )
    )
    captured: dict[str, Any] = {}

    def _fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured["cmd"] = cmd
        captured["env"] = kwargs.get("env")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr("agent_sandbox.admin_service.subprocess.run", _fake_run)

    result = service.create_issue_vm(issue="proxy:ado/77", start_agent=False, model="gpt-5.2-codex")

    assert result.success is True
    assert captured["cmd"][0].endswith("setup_issue_vm.sh")
    assert captured["cmd"][-3] == issue.yaml_path
    assert captured["cmd"][-1] == "agent"


def test_create_vm_launches_generic_vm_and_bootstraps_selected_toolchains(tmp_path: Path, monkeypatch: Any) -> None:
    service = AdminService(write_config(tmp_path, enable_directory_scoped_config=True))
    created = service.create_directory_child("root", "team")
    assert created.ok is True and created.data is not None
    team_path = created.data.details.directory.path
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

    monkeypatch.setenv("SSH_PUBLIC_KEY_FILE", "/home/jonathan/.ssh/gh_id_ed25519.pub")
    monkeypatch.setenv("SSH_PRIVATE_KEY_PATH", "/home/jonathan/.ssh/gh_id_ed25519")
    monkeypatch.setattr("agent_sandbox.admin_service._dominfo_field", lambda vm_name, field_name: None)
    monkeypatch.setattr("agent_sandbox.admin_service._resolve_vm_ip", lambda vm_name: "192.168.250.10")
    calls: list[tuple[list[str], dict[str, str] | None]] = []

    def _fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append((cmd, kwargs.get("env")))
        script_name = next((Path(part).name for part in cmd if part.endswith(".sh")), Path(cmd[0]).name)
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=f"ok:{script_name}", stderr="")

    monkeypatch.setattr("agent_sandbox.admin_service.subprocess.run", _fake_run)

    result = service.create_vm(
        vm_name="lab-vm",
        session_id="lab-session",
        labels={"workflow": "issue-worker", "agent_role": "issue"},
        dotnet=True,
        nuget=True,
        playwright=True,
        directory_path=team_path,
    )

    assert result.success is True
    assert [cmd[-1] for cmd, _env in calls] == ["agent"]
    assert calls[0][0][:3] == ["pkexec", "env", "SSH_PUBLIC_KEY_FILE=/home/jonathan/.ssh/gh_id_ed25519.pub"]
    assert "SSH_PRIVATE_KEY_PATH=/home/jonathan/.ssh/gh_id_ed25519" in calls[0][0]
    assert calls[0][0][-7:] == [
        str(Path(__file__).resolve().parents[1] / "scripts" / "setup_vm.sh"),
        "--dotnet",
        "--nuget",
        "--playwright",
        "lab-vm",
        "lab-session",
        "agent",
    ]
    setup_env = calls[0][1]
    assert setup_env is not None
    assert setup_env["GATEWAY_URL"] == "http://10.20.30.40:8000/mcp"
    assert setup_env["LLM_BASE_URL"] == "http://10.20.30.40:12000/v1"
    assert setup_env["NUGET_RELAY_BASE_URL"] == "http://10.20.30.40:13000"
    manifest = service.runtime.sessions.get("lab-session")
    assert manifest is not None
    assert manifest.vm_id == "lab-vm"
    assert manifest.directory_path == team_path
    assert manifest.metadata == {"labels": {"workflow": "issue-worker", "agent_role": "issue"}}
    assert result.message == "ok:setup_vm.sh"


def test_list_vms_surfaces_shared_session_labels(tmp_path: Path, monkeypatch: Any) -> None:
    service = AdminService(write_config(tmp_path))
    service.runtime.sessions.put(
        "session-1",
        SessionManifest.model_validate(
            {
                "session_id": "session-1",
                "vm_id": "lab-vm",
                "directory_path": "root",
                "allowed_tools": [],
                "expires_at": "2099-01-01T00:00:00Z",
                "metadata": {"labels": {"workflow": "issue-worker", "agent_role": "issue"}},
            }
        ),
    )

    monkeypatch.setattr("agent_sandbox.admin_service._virsh_lines", lambda args: ["lab-vm"])
    monkeypatch.setattr("agent_sandbox.admin_service._dominfo_field", lambda vm_name, field_name: "running")
    monkeypatch.setattr("agent_sandbox.admin_service._resolve_vm_ip", lambda vm_name: "192.0.2.10")
    monkeypatch.setattr(
        "agent_sandbox.admin_service._probe_agent_state",
        lambda vm_name, vm_ip, vm_user: ("idle", ["sample-repo"], datetime(2026, 4, 5, 12, 0, tzinfo=UTC)),
    )

    summaries = service.list_vms()

    assert len(summaries) == 1
    assert summaries[0].labels == {"workflow": "issue-worker", "agent_role": "issue"}


def test_create_vm_with_repo_source_clones_repo_and_bootstraps_python(tmp_path: Path, monkeypatch: Any) -> None:
    service = AdminService(write_config(tmp_path, enable_directory_scoped_config=True))
    captured_runs: list[tuple[list[str], dict[str, str] | None]] = []

    monkeypatch.setattr("agent_sandbox.admin_service._dominfo_field", lambda vm_name, field_name: None)
    monkeypatch.setattr("agent_sandbox.admin_service._resolve_vm_ip", lambda vm_name: "192.168.250.10")
    monkeypatch.setattr("agent_sandbox.admin_service._resolve_private_key_path", lambda: Path("/tmp/test-key"))
    monkeypatch.setattr(
        "agent_sandbox.admin_service._resolve_host_python_with_pip",
        lambda: str(REPO_ROOT / ".venv" / "bin" / "python"),
    )

    def _fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured_runs.append((cmd, kwargs.get("env")))
        if cmd[0].endswith("resolve_repo_source.sh") and "--path-only" in cmd:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="/src/repo\n", stderr="")
        if cmd[0].endswith("resolve_repo_source.sh") and "--name-only" in cmd:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="sample-repo\n", stderr="")
        if cmd[:5] == ["git", "-C", "/src/repo", "symbolic-ref", "--quiet"]:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="origin/main\n", stderr="")
        if cmd[:4] == ["git", "-C", "/src/repo", "branch"]:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="main\n", stderr="")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr("agent_sandbox.admin_service.subprocess.run", _fake_run)

    result = service.create_vm(
        vm_name="lab-vm",
        session_id="lab-session",
        repo_source="/src/repo",
        python=True,
    )

    assert result.success is True
    captured_cmds = [cmd for cmd, _env in captured_runs]
    assert any(cmd[0].endswith("create_git_remote.sh") and cmd[1] == "sample-repo" for cmd in captured_cmds)
    assert any(cmd[0] == "git" and cmd[1].endswith("/sample-repo.git") and cmd[2] == "fetch" for cmd in captured_cmds)
    assert any(
        cmd[0] == "git"
        and cmd[1].endswith("/sample-repo.git")
        and cmd[2:5] == ["symbolic-ref", "HEAD", "refs/heads/main"]
        for cmd in captured_cmds
    )
    assert any(cmd[0].endswith("bootstrap_git_vm.sh") and cmd[1:] == ["lab-vm", "agent"] for cmd in captured_cmds)
    assert any(cmd[0] == "ssh" and cmd[-3:] == ["agent@192.168.250.10", "bash", "-s"] for cmd in captured_cmds)
    python_runs = [
        (cmd, env)
        for cmd, env in captured_runs
        if cmd[0].endswith("bootstrap_python_vm.sh")
    ]
    assert len(python_runs) == 1
    assert python_runs[0][0][1:] == ["--vm-user", "agent", "lab-vm", "/src/repo", "/home/agent/sample-repo"]
    assert python_runs[0][1] is not None
    assert python_runs[0][1]["HOST_PYTHON_BIN"] == str(REPO_ROOT / ".venv" / "bin" / "python")


def test_create_vm_preserves_session_when_bootstrap_fails_after_launch(tmp_path: Path, monkeypatch: Any) -> None:
    service = AdminService(write_config(tmp_path))
    state = {"launched": False}
    monkeypatch.setattr(
        "agent_sandbox.admin_service._dominfo_field",
        lambda vm_name, field_name: "running" if state["launched"] and field_name == "State" else None,
    )

    def _fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if any(part.endswith("setup_vm.sh") for part in cmd):
            state["launched"] = True
            return subprocess.CompletedProcess(args=cmd, returncode=1, stdout="", stderr="codex failed")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr("agent_sandbox.admin_service.subprocess.run", _fake_run)

    result = service.create_vm(vm_name="lab-vm", session_id="lab-session")

    assert result.success is False
    assert result.message == (
        "Failed to create VM lab-vm: codex failed\n"
        "VM lab-vm and session lab-session were preserved for inspection."
    )
    manifest = service.runtime.sessions.get("lab-session")
    assert manifest is not None
    assert manifest.vm_id == "lab-vm"


def test_create_vm_failure_preserves_stdout_and_stderr(tmp_path: Path, monkeypatch: Any) -> None:
    service = AdminService(write_config(tmp_path))
    state = {"launched": False}
    monkeypatch.setattr(
        "agent_sandbox.admin_service._dominfo_field",
        lambda vm_name, field_name: "running" if state["launched"] and field_name == "State" else None,
    )

    def _fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if any(part.endswith("setup_vm.sh") for part in cmd):
            state["launched"] = True
            return subprocess.CompletedProcess(
                args=cmd,
                returncode=1,
                stdout="Bootstrapping Codex into agent@192.168.250.204\n",
                stderr="scp: /home/agent/bin/codex: No such file or directory\n",
            )
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr("agent_sandbox.admin_service.subprocess.run", _fake_run)

    result = service.create_vm(vm_name="idea-generator-vm", session_id="idea-generator")

    assert result.success is False
    assert "Bootstrapping Codex into agent@192.168.250.204" in result.message
    assert "scp: /home/agent/bin/codex: No such file or directory" in result.message
    assert "were preserved for inspection" in result.message


def test_create_vm_waits_for_ssh_before_bootstrap(tmp_path: Path, monkeypatch: Any) -> None:
    service = AdminService(write_config(tmp_path))
    monkeypatch.setattr("agent_sandbox.admin_service._dominfo_field", lambda vm_name, field_name: None)
    monkeypatch.setattr("agent_sandbox.admin_service._resolve_vm_ip", lambda vm_name: "192.168.250.154")
    captured_cmds: list[list[str]] = []
    monkeypatch.setattr(
        "agent_sandbox.admin_service.subprocess.run",
        lambda cmd, **kwargs: captured_cmds.append(cmd) or subprocess.CompletedProcess(args=cmd, returncode=0, stdout="ok", stderr=""),
    )

    result = service.create_vm(vm_name="idea-generator-vm", session_id="idea-generator")

    assert result.success is True
    assert any(any(part.endswith("setup_vm.sh") for part in cmd) for cmd in captured_cmds)


def test_sync_repo_into_bare_remote_sets_head_to_default_branch(tmp_path: Path, monkeypatch: Any) -> None:
    service = AdminService(write_config(tmp_path))
    issue_setup = service._resolve_issue_setup("root", root_dir=Path(__file__).resolve().parents[1])
    captured_cmds: list[list[str]] = []

    def _fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured_cmds.append(cmd)
        if cmd[0].endswith("create_git_remote.sh"):
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="ok", stderr="")
        if cmd[0] == "git" and "symbolic-ref" in cmd:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
        if cmd[0] == "git" and "fetch" in cmd:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
        if cmd[:5] == ["git", "-C", "/src/repo", "symbolic-ref", "--quiet"]:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="origin/main\n", stderr="")
        if cmd[:4] == ["git", "-C", "/src/repo", "branch"]:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="main\n", stderr="")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr("agent_sandbox.admin_service.subprocess.run", _fake_run)

    service._sync_repo_into_bare_remote("sample-repo", "/src/repo", issue_setup=issue_setup)

    assert any(
        cmd[0] == "git"
        and cmd[1].endswith("/sample-repo.git")
        and cmd[2:5] == ["symbolic-ref", "HEAD", "refs/heads/main"]
        for cmd in captured_cmds
    )


def test_resolve_host_python_with_pip_prefers_current_interpreter(monkeypatch: Any) -> None:
    monkeypatch.delenv("HOST_PYTHON_BIN", raising=False)
    monkeypatch.setattr(admin_service.sys, "executable", "/work/.venv/bin/python")

    def _fake_has_pip(candidate: str) -> bool:
        return candidate == "/work/.venv/bin/python"

    monkeypatch.setattr("agent_sandbox.admin_service._python_has_pip", _fake_has_pip)

    assert admin_service._resolve_host_python_with_pip() == "/work/.venv/bin/python"


def test_resolve_host_python_with_pip_honors_configured_override(monkeypatch: Any) -> None:
    monkeypatch.setenv("HOST_PYTHON_BIN", "/custom/python")
    monkeypatch.setattr("agent_sandbox.admin_service._ensure_python_has_pip", lambda candidate: candidate == "/custom/python")

    assert admin_service._resolve_host_python_with_pip() == "/custom/python"


def test_resolve_host_python_with_pip_bootstraps_pip_with_ensurepip(monkeypatch: Any) -> None:
    monkeypatch.delenv("HOST_PYTHON_BIN", raising=False)
    monkeypatch.setattr(admin_service.sys, "executable", "/work/.venv/bin/python3")
    attempts: list[str] = []

    def _fake_ensure(candidate: str) -> bool:
        attempts.append(candidate)
        return candidate == "/work/.venv/bin/python3"

    monkeypatch.setattr("agent_sandbox.admin_service._ensure_python_has_pip", _fake_ensure)

    assert admin_service._resolve_host_python_with_pip() == "/work/.venv/bin/python3"
    assert attempts == ["/work/.venv/bin/python3"]


def test_create_vm_requires_repo_source_for_python(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path))

    result = service.create_vm(vm_name="lab-vm", python=True)

    assert result.success is False
    assert result.message == "repo_source is required when Python bootstrap is enabled."


def test_wait_for_vm_ip_uses_pkexec_when_requested(monkeypatch) -> None:
    calls: list[bool] = []

    def _fake_plain(vm_name: str) -> str | None:
        calls.append(False)
        return None

    def _fake_pkexec(vm_name: str) -> str | None:
        calls.append(True)
        return "192.168.250.10"

    monkeypatch.setattr("agent_sandbox.admin_service._resolve_vm_ip", _fake_plain)
    monkeypatch.setattr("agent_sandbox.admin_service._resolve_vm_ip_with_pkexec", _fake_pkexec)
    monkeypatch.setattr("agent_sandbox.admin_service.time.sleep", lambda _seconds: None)

    ip = admin_service._wait_for_vm_ip(
        "vm-test",
        timeout_seconds=1,
        allow_pkexec=True,
    )

    assert ip == "192.168.250.10"
    assert calls == [False, True]


def test_unlock_privileged_operations_starts_helper(monkeypatch, tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path))
    started: list[str] = []

    class FakeHelper:
        def __init__(self, *, python_executable: str, project_dir: Path) -> None:
            started.append("init")

        def start(self) -> None:
            started.append("start")

        def is_running(self) -> bool:
            return True

        def close(self) -> None:
            started.append("close")

    monkeypatch.setattr("agent_sandbox.admin_service.PrivilegedHelperSession", FakeHelper)

    assert service.unlock_privileged_operations() == "Privileged operations unlocked."
    assert service.privileged_operations_unlocked() is True
    service.lock_privileged_operations()
    assert started == ["init", "start", "close"]


def test_destroy_vm_prunes_generic_sessions_and_feedback_state(tmp_path: Path, monkeypatch: Any) -> None:
    service = AdminService(write_config(tmp_path))
    service.runtime.sessions.put(
        "lab-session",
        SessionManifest(
            session_id="lab-session",
            vm_id="lab-vm",
            allowed_tools=[],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    service.runtime.sessions.put(
        "other-session",
        SessionManifest(
            session_id="other-session",
            vm_id="other-vm",
            allowed_tools=[],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    monkeypatch.setattr(
        "agent_sandbox.admin_service.subprocess.run",
        lambda cmd, **kwargs: subprocess.CompletedProcess(args=cmd, returncode=0, stdout="destroyed", stderr=""),
    )
    monkeypatch.setattr("agent_sandbox.admin_service._prune_approvals_for_sessions", lambda runtime, session_ids: len(session_ids))
    monkeypatch.setattr(
        "agent_sandbox.admin_service._prune_pr_map",
        lambda path, session_ids: (len(session_ids), {"stream-a"} if session_ids else set()),
    )
    monkeypatch.setattr(
        "agent_sandbox.admin_service._prune_pr_feedback_store",
        lambda path, streams: (len(streams), len(streams)),
    )

    result = service.destroy_vm("lab-vm")

    assert result.success is True
    assert service.runtime.sessions.get("lab-session") is None
    assert service.runtime.sessions.get("other-session") is not None
    assert "removed_sessions=1" in result.message
    assert "removed_pr_map_entries=1" in result.message


def test_create_and_list_wake_schedules_include_last_dispatch(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path))
    service.create_wake_schedule(
        session_id="issue-1-session",
        prompt="Resume now",
        cron="*/5 * * * *",
        source="admin_cli",
        resume_mode="new_context",
    )
    schedules = service.list_wake_schedules(session_id="issue-1-session")
    assert len(schedules) == 1
    assert schedules[0]["cron"] == "*/5 * * * *"
    assert schedules[0]["resume_mode"] == "new_context"
    assert schedules[0]["last_dispatch"] is None


def test_create_wake_schedule_rejects_invalid_cron(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path))
    with pytest.raises(ValueError, match="Invalid cron expression"):
        service.create_wake_schedule(
            session_id="issue-1-session",
            prompt="Resume now",
            cron="* *",
        )


def test_create_wake_schedule_enforces_max_active_per_session(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path))
    service.config.host_heartbeat_schedule_max_active_per_session = 1
    service.create_wake_schedule(
        session_id="issue-1-session",
        prompt="Resume now",
        cron="*/5 * * * *",
    )

    with pytest.raises(ValueError, match="max_active_per_session=1"):
        service.create_wake_schedule(
            session_id="issue-1-session",
            prompt="Resume again",
            cron="*/10 * * * *",
        )


def test_create_wake_schedule_enforces_max_active_total(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path))
    service.config.host_heartbeat_schedule_max_active_total = 1
    service.create_wake_schedule(
        session_id="issue-1-session",
        prompt="Resume now",
        cron="*/5 * * * *",
    )

    with pytest.raises(ValueError, match="max_active_total=1"):
        service.create_wake_schedule(
            session_id="issue-2-session",
            prompt="Resume another",
            cron="*/10 * * * *",
        )


def test_create_wake_schedule_enforces_min_interval(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path))
    service.config.host_heartbeat_schedule_min_interval_seconds = 600

    with pytest.raises(ValueError, match="minimum=600s actual=300s"):
        service.create_wake_schedule(
            session_id="issue-1-session",
            prompt="Resume now",
            cron="*/5 * * * *",
        )


def test_edit_wake_schedule_enforces_guardrails_when_enabling_or_tightening_cron(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path))
    service.config.host_heartbeat_schedule_max_active_per_session = 1
    service.config.host_heartbeat_schedule_min_interval_seconds = 600
    created = service.create_wake_schedule(
        session_id="issue-1-session",
        prompt="Resume later",
        cron="*/15 * * * *",
        enabled=False,
    )
    service.create_wake_schedule(
        session_id="issue-1-session",
        prompt="Existing active",
        cron="*/20 * * * *",
    )

    with pytest.raises(ValueError, match="max_active_per_session=1"):
        service.edit_wake_schedule(created.request_id, enabled=True)

    other = service.create_wake_schedule(
        session_id="issue-2-session",
        prompt="Safe interval",
        cron="*/15 * * * *",
    )
    with pytest.raises(ValueError, match="minimum=600s actual=300s"):
        service.edit_wake_schedule(other.request_id, cron="*/5 * * * *")


def test_edit_wake_schedule_rejects_new_context_for_mcp_source(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path))
    created = service.create_wake_schedule(
        session_id="issue-1-session",
        prompt="Resume now",
        resume_at=datetime.now(UTC) + timedelta(hours=1),
        source="mcp_tool",
        resume_mode="same_context",
    )
    with pytest.raises(ValueError, match="MCP scheduling only supports"):
        service.edit_wake_schedule(created.request_id, resume_mode="new_context")


def test_create_wake_schedule_rejects_new_context_for_mcp_source(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path))
    with pytest.raises(ValueError, match="MCP scheduling only supports"):
        service.create_wake_schedule(
            session_id="issue-1-session",
            prompt="Resume now",
            resume_at=datetime.now(UTC) + timedelta(hours=1),
            source="mcp_schedule_resume",
            resume_mode="new_context",
        )


def test_list_wake_schedules_filters_disabled_entries(tmp_path: Path) -> None:
    service = AdminService(write_config(tmp_path))
    created = service.create_wake_schedule(
        session_id="issue-1-session",
        prompt="Resume now",
        cron="*/5 * * * *",
        source="admin_cli",
    )
    service.edit_wake_schedule(created.request_id, enabled=False)

    schedules = service.list_wake_schedules(session_id="issue-1-session", include_disabled=False)

    assert schedules == []


def _write_proxy_issue_yaml(
    tmp_path: Path,
    *,
    issue_number: int = 42,
    title: str = "Proxy issue",
    description: str = "from remote",
    state: str = "New",
    organization_url: str = "https://dev.azure.com/example",
    project: str = "sample",
) -> Path:
    payload = {
        "issue_number": issue_number,
        "title": title,
        "description": description,
        "state": state,
        "url": f"https://ado.example/work/{issue_number}",
        "source": {
            "organization": organization_url,
            "project": project,
            "work_item_url": f"{organization_url.rstrip('/')}/{project}/_workitems/edit/{issue_number}",
        },
    }
    path = tmp_path / f"proxy-issue-{issue_number}-{state.lower()}.yaml"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path
