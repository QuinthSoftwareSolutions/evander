import json
import subprocess
from types import SimpleNamespace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from typer.testing import CliRunner

from agent_sandbox.cli import EndpointStatus, UnitStatus, app

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


def test_seed_email_accepts_recipient_option_after_positionals(tmp_path: Path) -> None:
    config_path = write_config(tmp_path)
    runner = CliRunner()

    init_result = runner.invoke(app, ["init-state", "--config", str(config_path)])
    assert init_result.exit_code == 0

    result = runner.invoke(
        app,
        [
            "seed-email",
            "--config",
            str(config_path),
            "msg-001",
            "inbox",
            "bot@example.com",
            "Welcome",
            "Hello from test",
            "--recipient",
            "you@example.com",
            "--recipient",
            "other@example.com",
        ],
    )

    assert result.exit_code == 0
    emails = (tmp_path / "runtime" / "emails.json").read_text(encoding="utf-8")
    assert "you@example.com" in emails
    assert "other@example.com" in emails


def test_create_session_disables_email_send_by_default(tmp_path: Path) -> None:
    config_path = write_config(tmp_path)
    runner = CliRunner()

    init_result = runner.invoke(app, ["init-state", "--config", str(config_path)])
    assert init_result.exit_code == 0

    result = runner.invoke(
        app,
        ["create-session", "--config", str(config_path), "session-1"],
    )
    assert result.exit_code == 0

    sessions = json.loads((tmp_path / "runtime" / "sessions.json").read_text(encoding="utf-8"))
    manifest = sessions["session-1"]
    assert manifest["email_send_enabled"] is False
    assert "email_send_approved" not in manifest["allowed_tools"]


def test_create_issue_vm_creates_manifest_before_invoking_setup_script(tmp_path: Path, monkeypatch) -> None:
    config_path = write_config(tmp_path)
    runner = CliRunner()
    captured_cmd: list[str] = []

    init_result = runner.invoke(app, ["init-state", "--config", str(config_path)])
    assert init_result.exit_code == 0

    def fake_run(cmd: list[str], check: bool) -> subprocess.CompletedProcess[str]:
        nonlocal captured_cmd
        captured_cmd = cmd
        sessions = json.loads((tmp_path / "runtime" / "sessions.json").read_text(encoding="utf-8"))
        assert "issue-1234-session" in sessions
        assert sessions["issue-1234-session"]["vm_id"] == "vm-1234"
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr("agent_sandbox.cli.subprocess.run", fake_run)

    result = runner.invoke(
        app,
        [
            "create-issue-vm",
            "--config",
            str(config_path),
            "--session-id",
            "issue-1234-session",
            "1234",
        ],
    )

    assert result.exit_code == 0
    assert captured_cmd[-3:] == ["1234", "issue-1234-session", "agent"]


def test_create_issue_vm_persists_expected_manifest_shape(tmp_path: Path, monkeypatch) -> None:
    config_path = write_config(tmp_path)
    runner = CliRunner()

    init_result = runner.invoke(app, ["init-state", "--config", str(config_path)])
    assert init_result.exit_code == 0

    monkeypatch.setattr(
        "agent_sandbox.cli.subprocess.run",
        lambda cmd, check: subprocess.CompletedProcess(cmd, 0),
    )

    result = runner.invoke(
        app,
        [
            "create-issue-vm",
            "--config",
            str(config_path),
            "--session-id",
            "issue-14084-session",
            "--discord-channel",
            "123456789012345678",
            "--email-mailbox",
            "default",
            "--email-folder",
            "inbox",
            "14084",
        ],
    )

    assert result.exit_code == 0
    sessions = json.loads((tmp_path / "runtime" / "sessions.json").read_text(encoding="utf-8"))
    manifest = sessions["issue-14084-session"]
    assert manifest["vm_id"] == "vm-14084"
    assert manifest["discord_channel_allowlist"] == ["123456789012345678"]
    assert manifest["email_mailbox_allowlist"] == ["default"]
    assert manifest["email_folder_allowlist"] == ["inbox"]
    assert set(manifest["allowed_tools"]) == {
        "approval_submit",
        "approval_check",
        "discord_get_commands",
        "discord_post_status",
        "email_list_unread",
        "email_get_message",
        "email_create_draft",
    }
    assert manifest["metadata"] == {}


def test_create_issue_vm_builds_setup_issue_vm_command_correctly(tmp_path: Path, monkeypatch) -> None:
    config_path = write_config(tmp_path)
    runner = CliRunner()
    captured_cmd: list[str] = []

    init_result = runner.invoke(app, ["init-state", "--config", str(config_path)])
    assert init_result.exit_code == 0

    def fake_run(
        cmd: list[str],
        check: bool,
        capture_output: bool,
        text: bool,
    ) -> subprocess.CompletedProcess[str]:
        nonlocal captured_cmd
        captured_cmd = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr("agent_sandbox.cli.subprocess.run", fake_run)

    with monkeypatch.context() as m:
        m.setenv("MSSQL_SA_PASSWORD", "SecretPassw0rd!")
        result = runner.invoke(
            app,
            [
                "create-issue-vm",
                "--config",
                str(config_path),
                "--session-id",
                "session-14084",
                "--no-start-agent",
                "--dotnet",
                "--nuget",
                "/tmp/project.slnx",
                "--mssql",
                "--mssql-init-root",
                "/tmp/mssql-init",
                "--playwright",
                "--repo-source",
                "/src/repo",
                "14084",
            ],
        )

    assert result.exit_code == 0
    assert captured_cmd[0].endswith("/scripts/setup_issue_vm.sh")
    assert captured_cmd[1:7] == [
        "--dry-run",
        "--dotnet",
        "--nuget",
        "--mssql",
        "--mssql-init-root",
        "/tmp/mssql-init",
    ]
    assert "--playwright" in captured_cmd
    assert captured_cmd[-5:] == [
        "14084",
        "/tmp/project.slnx",
        "/src/repo",
        "session-14084",
        "agent",
    ]


def test_create_issue_vm_requires_mssql_password_env(tmp_path: Path, monkeypatch) -> None:
    config_path = write_config(tmp_path)
    runner = CliRunner()

    init_result = runner.invoke(app, ["init-state", "--config", str(config_path)])
    assert init_result.exit_code == 0

    monkeypatch.delenv("MSSQL_SA_PASSWORD", raising=False)
    result = runner.invoke(
        app,
        [
            "create-issue-vm",
            "--config",
            str(config_path),
            "--mssql",
            "14084",
        ],
    )

    assert result.exit_code != 0
    assert "MSSQL_SA_PASSWORD must be set in the environment" in result.output


def test_create_issue_vm_rejects_mssql_init_root_without_mssql(tmp_path: Path, monkeypatch) -> None:
    config_path = write_config(tmp_path)
    runner = CliRunner()

    init_result = runner.invoke(app, ["init-state", "--config", str(config_path)])
    assert init_result.exit_code == 0

    monkeypatch.setattr(
        "agent_sandbox.cli.subprocess.run",
        lambda cmd, check: subprocess.CompletedProcess(cmd, 0),
    )

    result = runner.invoke(
        app,
        [
            "create-issue-vm",
            "--config",
            str(config_path),
            "--mssql-init-root",
            "/tmp/mssql-init",
            "14084",
        ],
    )

    assert result.exit_code != 0
    assert "--mssql-init-root requires --mssql" in result.output


def test_create_issue_vm_rejects_existing_session_id_without_running_setup(tmp_path: Path, monkeypatch) -> None:
    config_path = write_config(tmp_path)
    runner = CliRunner()
    run_called = False

    init_result = runner.invoke(app, ["init-state", "--config", str(config_path)])
    assert init_result.exit_code == 0

    create_result = runner.invoke(app, ["create-session", "--config", str(config_path), "session-1"])
    assert create_result.exit_code == 0

    def fake_run(cmd: list[str], check: bool) -> subprocess.CompletedProcess[str]:
        nonlocal run_called
        run_called = True
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr("agent_sandbox.cli.subprocess.run", fake_run)

    result = runner.invoke(
        app,
        [
            "create-issue-vm",
            "--config",
            str(config_path),
            "--session-id",
            "session-1",
            "14084",
        ],
    )

    assert result.exit_code != 0
    assert "Session already exists: session-1" in result.output
    assert run_called is False


def test_create_issue_vm_does_not_grant_unrequested_tool_families(tmp_path: Path, monkeypatch) -> None:
    config_path = write_config(tmp_path)
    runner = CliRunner()

    init_result = runner.invoke(app, ["init-state", "--config", str(config_path)])
    assert init_result.exit_code == 0

    monkeypatch.setattr(
        "agent_sandbox.cli.subprocess.run",
        lambda cmd, check: subprocess.CompletedProcess(cmd, 0),
    )

    result = runner.invoke(
        app,
        [
            "create-issue-vm",
            "--config",
            str(config_path),
            "--session-id",
            "issue-200-minimal",
            "200",
        ],
    )

    assert result.exit_code == 0
    sessions = json.loads((tmp_path / "runtime" / "sessions.json").read_text(encoding="utf-8"))
    manifest = sessions["issue-200-minimal"]
    assert manifest["allowed_tools"] == ["approval_submit", "approval_check"]
    assert manifest["email_mailbox_allowlist"] == []
    assert manifest["email_folder_allowlist"] == []
    assert manifest["metadata"] == {}


def test_attach_pr_feedback_updates_existing_session(tmp_path: Path) -> None:
    config_path = write_config(tmp_path)
    runner = CliRunner()

    init_result = runner.invoke(app, ["init-state", "--config", str(config_path)])
    assert init_result.exit_code == 0

    create_result = runner.invoke(app, ["create-session", "--config", str(config_path), "session-1"])
    assert create_result.exit_code == 0

    result = runner.invoke(
        app,
        [
            "attach-pr-feedback",
            "--config",
            str(config_path),
            "--provider",
            "github",
            "--repo",
            "owner/repo",
            "--pr-id",
            "17",
            "session-1",
        ],
    )

    assert result.exit_code == 0
    sessions = json.loads((tmp_path / "runtime" / "sessions.json").read_text(encoding="utf-8"))
    manifest = sessions["session-1"]
    assert manifest["metadata"]["pr_feedback_streams"] == [
        {"provider": "github", "repo": "owner/repo", "pr_id": "17"}
    ]
    assert "has_pending_feedback" in manifest["allowed_tools"]
    assert "list_unread_feedback" in manifest["allowed_tools"]
    assert "post_pr_feedback_reply" in manifest["allowed_tools"]


def test_attach_pr_feedback_rejects_unknown_session(tmp_path: Path) -> None:
    config_path = write_config(tmp_path)
    runner = CliRunner()

    init_result = runner.invoke(app, ["init-state", "--config", str(config_path)])
    assert init_result.exit_code == 0

    result = runner.invoke(
        app,
        [
            "attach-pr-feedback",
            "--config",
            str(config_path),
            "--provider",
            "github",
            "--repo",
            "owner/repo",
            "--pr-id",
            "17",
            "missing-session",
        ],
    )

    assert result.exit_code != 0
    assert "Unknown session: missing-session" in result.output


def test_prune_sessions_removes_only_expired_by_default(tmp_path: Path) -> None:
    config_path = write_config(tmp_path)
    runner = CliRunner()

    init_result = runner.invoke(app, ["init-state", "--config", str(config_path)])
    assert init_result.exit_code == 0

    sessions_path = tmp_path / "runtime" / "sessions.json"
    sessions_path.write_text(
        json.dumps(
            {
                "active-session": {
                    "session_id": "active-session",
                    "vm_id": "vm-active",
                    "allowed_tools": ["approval_submit"],
                    "discord_channel_allowlist": [],
                    "email_folder_allowlist": [],
                    "email_mailbox_allowlist": [],
                    "email_folder_allowlist_by_mailbox": {},
                    "email_draft_recipient_allowlist": [],
                    "email_draft_domain_allowlist": [],
                    "email_send_enabled": False,
                    "quotas": {},
                    "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
                    "created_at": datetime.now(UTC).isoformat(),
                    "metadata": {},
                },
                "expired-session": {
                    "session_id": "expired-session",
                    "vm_id": "vm-expired",
                    "allowed_tools": ["approval_submit"],
                    "discord_channel_allowlist": [],
                    "email_folder_allowlist": [],
                    "email_mailbox_allowlist": [],
                    "email_folder_allowlist_by_mailbox": {},
                    "email_draft_recipient_allowlist": [],
                    "email_draft_domain_allowlist": [],
                    "email_send_enabled": False,
                    "quotas": {},
                    "expires_at": (datetime.now(UTC) - timedelta(hours=1)).isoformat(),
                    "created_at": datetime.now(UTC).isoformat(),
                    "metadata": {},
                },
            }
        ),
        encoding="utf-8",
    )

    result = runner.invoke(app, ["prune-sessions", "--config", str(config_path)])

    assert result.exit_code == 0
    assert "Pruned 1 expired session(s)" in result.stdout
    sessions = json.loads(sessions_path.read_text(encoding="utf-8"))
    assert set(sessions) == {"active-session"}


def test_prune_sessions_all_removes_every_session(tmp_path: Path) -> None:
    config_path = write_config(tmp_path)
    runner = CliRunner()

    init_result = runner.invoke(app, ["init-state", "--config", str(config_path)])
    assert init_result.exit_code == 0

    create_a = runner.invoke(app, ["create-session", "--config", str(config_path), "session-a"])
    create_b = runner.invoke(app, ["create-session", "--config", str(config_path), "session-b"])
    assert create_a.exit_code == 0
    assert create_b.exit_code == 0

    result = runner.invoke(app, ["prune-sessions", "--config", str(config_path), "--all"])

    assert result.exit_code == 0
    assert "Pruned 2 all session(s)" in result.stdout
    sessions = json.loads((tmp_path / "runtime" / "sessions.json").read_text(encoding="utf-8"))
    assert sessions == {}


def test_destroy_issue_vm_cleans_issue_state_and_bare_repo(tmp_path: Path, monkeypatch) -> None:
    config_path = write_config(tmp_path)
    runner = CliRunner()
    runtime_dir = tmp_path / "runtime"
    git_root = tmp_path / "git-remotes"
    bare_repo = git_root / "1234_repo.git"
    trusted_repo = tmp_path / "trusted-repo"
    hook_log = runtime_dir / "hook-logs" / "1234_repo.git.post-receive.log"
    captured_cmds: list[list[str]] = []

    init_result = runner.invoke(app, ["init-state", "--config", str(config_path)])
    assert init_result.exit_code == 0

    sessions_path = runtime_dir / "sessions.json"
    sessions_path.write_text(
        json.dumps(
            {
                "issue-1234-a": {
                    "session_id": "issue-1234-a",
                    "vm_id": "vm-1234",
                    "allowed_tools": ["approval_check"],
                    "discord_channel_allowlist": [],
                    "email_folder_allowlist": [],
                    "email_mailbox_allowlist": [],
                    "email_folder_allowlist_by_mailbox": {},
                    "email_draft_recipient_allowlist": [],
                    "email_draft_domain_allowlist": [],
                    "email_send_enabled": False,
                    "quotas": {},
                    "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
                    "created_at": datetime.now(UTC).isoformat(),
                    "metadata": {},
                },
                "issue-1234-b": {
                    "session_id": "issue-1234-b",
                    "vm_id": "vm-1234",
                    "allowed_tools": ["approval_check"],
                    "discord_channel_allowlist": [],
                    "email_folder_allowlist": [],
                    "email_mailbox_allowlist": [],
                    "email_folder_allowlist_by_mailbox": {},
                    "email_draft_recipient_allowlist": [],
                    "email_draft_domain_allowlist": [],
                    "email_send_enabled": False,
                    "quotas": {},
                    "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
                    "created_at": datetime.now(UTC).isoformat(),
                    "metadata": {},
                },
                "keep-me": {
                    "session_id": "keep-me",
                    "vm_id": "vm-9999",
                    "allowed_tools": ["approval_check"],
                    "discord_channel_allowlist": [],
                    "email_folder_allowlist": [],
                    "email_mailbox_allowlist": [],
                    "email_folder_allowlist_by_mailbox": {},
                    "email_draft_recipient_allowlist": [],
                    "email_draft_domain_allowlist": [],
                    "email_send_enabled": False,
                    "quotas": {},
                    "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
                    "created_at": datetime.now(UTC).isoformat(),
                    "metadata": {},
                },
            }
        ),
        encoding="utf-8",
    )

    (runtime_dir / "approvals.json").write_text(
        json.dumps(
            {
                "approval-1": {
                    "request_id": "approval-1",
                    "session_id": "issue-1234-a",
                    "action_type": "send",
                    "payload_digest": "abc",
                    "human_summary": "test",
                    "status": "pending",
                    "created_at": datetime.now(UTC).isoformat(),
                    "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
                },
                "approval-2": {
                    "request_id": "approval-2",
                    "session_id": "keep-me",
                    "action_type": "send",
                    "payload_digest": "def",
                    "human_summary": "keep",
                    "status": "pending",
                    "created_at": datetime.now(UTC).isoformat(),
                    "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
                },
            }
        ),
        encoding="utf-8",
    )

    (runtime_dir / "pr-map.json").write_text(
        json.dumps(
            {
                "entries": {
                    "github:org/repo:1": {
                        "provider": "github",
                        "repo": "org/repo",
                        "pr_id": "1",
                        "session_id": "issue-1234-a",
                    },
                    "github:org/repo:2": {
                        "provider": "github",
                        "repo": "org/repo",
                        "pr_id": "2",
                        "session_id": "keep-me",
                    },
                }
            }
        ),
        encoding="utf-8",
    )

    (runtime_dir / "pr-feedback-store.json").write_text(
        json.dumps(
            {
                "cursors": {
                    "github:org/repo:1": "2026-03-24T00:00:00Z",
                    "github:org/repo:2": "2026-03-24T00:00:00Z",
                },
                "feedback": {
                    "github:issue_comment:1": {
                        "feedback_id": "github:issue_comment:1",
                        "provider": "github",
                        "repo": "org/repo",
                        "pr_id": "1",
                        "author": "octocat",
                        "body": "cleanup me",
                        "created_at": "2026-03-24T00:00:00Z",
                    },
                    "github:issue_comment:2": {
                        "feedback_id": "github:issue_comment:2",
                        "provider": "github",
                        "repo": "org/repo",
                        "pr_id": "2",
                        "author": "octocat",
                        "body": "keep me",
                        "created_at": "2026-03-24T00:00:00Z",
                    },
                },
            }
        ),
        encoding="utf-8",
    )

    bare_repo.mkdir(parents=True)
    trusted_repo.mkdir(parents=True)
    hook_log.parent.mkdir(parents=True, exist_ok=True)
    hook_log.write_text("hook log", encoding="utf-8")

    monkeypatch.setenv("AGENT_SANDBOX_GIT_ROOT", str(git_root))

    def fake_run(
        cmd: list[str],
        check: bool,
        capture_output: bool | None = None,
        text: bool | None = None,
    ) -> subprocess.CompletedProcess[str]:
        captured_cmds.append(cmd)
        if len(cmd) >= 4 and cmd[0] == "git" and cmd[2] == "config":
            return subprocess.CompletedProcess(cmd, 0, stdout=str(trusted_repo), stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr("agent_sandbox.cli.subprocess.run", fake_run)

    result = runner.invoke(app, ["destroy-issue-vm", "--config", str(config_path), "1234"])

    assert result.exit_code == 0
    assert "Destroyed issue VM vm-1234" in result.stdout
    assert not bare_repo.exists()
    assert not hook_log.exists()

    sessions = json.loads(sessions_path.read_text(encoding="utf-8"))
    assert set(sessions) == {"keep-me"}

    approvals = json.loads((runtime_dir / "approvals.json").read_text(encoding="utf-8"))
    assert set(approvals) == {"approval-2"}

    pr_map = json.loads((runtime_dir / "pr-map.json").read_text(encoding="utf-8"))
    assert set(pr_map["entries"]) == {"github:org/repo:2"}

    feedback_store = json.loads((runtime_dir / "pr-feedback-store.json").read_text(encoding="utf-8"))
    assert set(feedback_store["cursors"]) == {"github:org/repo:2"}
    assert set(feedback_store["feedback"]) == {"github:issue_comment:2"}

    assert any(cmd[0].endswith("/scripts/destroy_agent_vm.sh") and cmd[1] == "vm-1234" for cmd in captured_cmds)
    assert any(cmd[:5] == ["git", "-C", str(trusted_repo), "remote", "remove"] and cmd[5] == "1234-mirror" for cmd in captured_cmds)


def test_destroy_issue_vm_accepts_vm_name_target(tmp_path: Path, monkeypatch) -> None:
    config_path = write_config(tmp_path)
    runner = CliRunner()
    captured_cmd: list[str] = []

    init_result = runner.invoke(app, ["init-state", "--config", str(config_path)])
    assert init_result.exit_code == 0

    def fake_run(
        cmd: list[str],
        check: bool,
        capture_output: bool | None = None,
        text: bool | None = None,
    ) -> subprocess.CompletedProcess[str]:
        nonlocal captured_cmd
        captured_cmd = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr("agent_sandbox.cli.subprocess.run", fake_run)

    result = runner.invoke(app, ["destroy-issue-vm", "--config", str(config_path), "vm-1234"])

    assert result.exit_code == 0
    assert captured_cmd[0].endswith("/scripts/destroy_agent_vm.sh")
    assert captured_cmd[1:] == ["vm-1234"]


def test_prune_orphaned_issue_state_removes_state_for_missing_issue_vms(tmp_path: Path, monkeypatch) -> None:
    config_path = write_config(tmp_path)
    runner = CliRunner()
    runtime_dir = tmp_path / "runtime"
    git_root = tmp_path / "git-remotes"
    trusted_repo = tmp_path / "trusted-repo"
    captured_cmds: list[list[str]] = []

    init_result = runner.invoke(app, ["init-state", "--config", str(config_path)])
    assert init_result.exit_code == 0

    (runtime_dir / "sessions.json").write_text(
        json.dumps(
            {
                "issue-12305-a": {
                    "session_id": "issue-12305-a",
                    "vm_id": "vm-12305",
                    "allowed_tools": ["approval_check"],
                    "discord_channel_allowlist": [],
                    "email_folder_allowlist": [],
                    "email_mailbox_allowlist": [],
                    "email_folder_allowlist_by_mailbox": {},
                    "email_draft_recipient_allowlist": [],
                    "email_draft_domain_allowlist": [],
                    "email_send_enabled": False,
                    "quotas": {},
                    "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
                    "created_at": datetime.now(UTC).isoformat(),
                    "metadata": {
                        "pr_feedback_streams": [
                            {"provider": "github", "repo": "org/repo", "pr_id": "1"}
                        ]
                    },
                },
                "issue-14084-a": {
                    "session_id": "issue-14084-a",
                    "vm_id": "vm-14084",
                    "allowed_tools": ["approval_check"],
                    "discord_channel_allowlist": [],
                    "email_folder_allowlist": [],
                    "email_mailbox_allowlist": [],
                    "email_folder_allowlist_by_mailbox": {},
                    "email_draft_recipient_allowlist": [],
                    "email_draft_domain_allowlist": [],
                    "email_send_enabled": False,
                    "quotas": {},
                    "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
                    "created_at": datetime.now(UTC).isoformat(),
                    "metadata": {
                        "pr_feedback_streams": [
                            {"provider": "github", "repo": "org/live", "pr_id": "2"}
                        ]
                    },
                },
            }
        ),
        encoding="utf-8",
    )

    (runtime_dir / "approvals.json").write_text(
        json.dumps(
            {
                "approval-stale": {
                    "request_id": "approval-stale",
                    "session_id": "issue-12305-a",
                    "action_type": "send",
                    "payload_digest": "abc",
                    "human_summary": "stale",
                    "status": "pending",
                    "created_at": datetime.now(UTC).isoformat(),
                    "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
                },
                "approval-live": {
                    "request_id": "approval-live",
                    "session_id": "issue-14084-a",
                    "action_type": "send",
                    "payload_digest": "def",
                    "human_summary": "live",
                    "status": "pending",
                    "created_at": datetime.now(UTC).isoformat(),
                    "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
                },
            }
        ),
        encoding="utf-8",
    )

    (runtime_dir / "pr-map.json").write_text(
        json.dumps(
            {
                "entries": {
                    "github:org/repo:1": {
                        "provider": "github",
                        "repo": "org/repo",
                        "pr_id": "1",
                        "session_id": "issue-12305-a",
                    },
                    "azure_devops:org/repo:9": {
                        "provider": "azure_devops",
                        "repo": "org/repo",
                        "pr_id": "9",
                        "session_id": "issue-13301-xyz",
                    },
                    "github:org/live:2": {
                        "provider": "github",
                        "repo": "org/live",
                        "pr_id": "2",
                        "session_id": "issue-14084-a",
                    },
                    "github:org/manual:3": {
                        "provider": "github",
                        "repo": "org/manual",
                        "pr_id": "3",
                        "session_id": "manual",
                    },
                }
            }
        ),
        encoding="utf-8",
    )

    (runtime_dir / "pr-feedback-store.json").write_text(
        json.dumps(
            {
                "cursors": {
                    "github:org/repo:1": "2026-03-24T00:00:00Z",
                    "azure_devops:org/repo:9": "2026-03-24T00:00:00Z",
                    "github:org/live:2": "2026-03-24T00:00:00Z",
                },
                "feedback": {
                    "github:issue_comment:1": {
                        "feedback_id": "github:issue_comment:1",
                        "provider": "github",
                        "repo": "org/repo",
                        "pr_id": "1",
                        "author": "octocat",
                        "body": "stale",
                        "created_at": "2026-03-24T00:00:00Z",
                    },
                    "azure_devops:thread_comment:9": {
                        "feedback_id": "azure_devops:thread_comment:9",
                        "provider": "azure_devops",
                        "repo": "org/repo",
                        "pr_id": "9",
                        "author": "octocat",
                        "body": "stale map",
                        "created_at": "2026-03-24T00:00:00Z",
                    },
                    "github:issue_comment:2": {
                        "feedback_id": "github:issue_comment:2",
                        "provider": "github",
                        "repo": "org/live",
                        "pr_id": "2",
                        "author": "octocat",
                        "body": "live",
                        "created_at": "2026-03-24T00:00:00Z",
                    },
                },
            }
        ),
        encoding="utf-8",
    )

    stale_bare_repo = git_root / "12305_repo.git"
    stale_bare_repo.mkdir(parents=True)
    (git_root / "13301_repo.git").mkdir(parents=True)
    (git_root / "14084_repo.git").mkdir(parents=True)
    trusted_repo.mkdir(parents=True)
    monkeypatch.setenv("AGENT_SANDBOX_GIT_ROOT", str(git_root))

    def fake_run(
        cmd: list[str],
        check: bool,
        capture_output: bool | None = None,
        text: bool | None = None,
    ) -> subprocess.CompletedProcess[str]:
        captured_cmds.append(cmd)
        if cmd[:4] == ["virsh", "list", "--all", "--name"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="vm-14084\nvm-13928\n", stderr="")
        if len(cmd) >= 4 and cmd[0] == "git" and cmd[2] == "config":
            return subprocess.CompletedProcess(cmd, 0, stdout=str(trusted_repo), stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr("agent_sandbox.cli.subprocess.run", fake_run)

    result = runner.invoke(app, ["prune-orphaned-issue-state", "--config", str(config_path)])

    assert result.exit_code == 0
    assert "removed_sessions=1" in result.stdout
    assert "removed_pr_map_entries=2" in result.stdout
    assert "removed_bare_repos=2" in result.stdout

    sessions = json.loads((runtime_dir / "sessions.json").read_text(encoding="utf-8"))
    assert set(sessions) == {"issue-14084-a"}

    approvals = json.loads((runtime_dir / "approvals.json").read_text(encoding="utf-8"))
    assert set(approvals) == {"approval-live"}

    pr_map = json.loads((runtime_dir / "pr-map.json").read_text(encoding="utf-8"))
    assert set(pr_map["entries"]) == {"github:org/live:2", "github:org/manual:3"}

    feedback_store = json.loads((runtime_dir / "pr-feedback-store.json").read_text(encoding="utf-8"))
    assert set(feedback_store["cursors"]) == {"github:org/live:2"}
    assert set(feedback_store["feedback"]) == {"github:issue_comment:2"}

    assert not (git_root / "12305_repo.git").exists()
    assert not (git_root / "13301_repo.git").exists()
    assert (git_root / "14084_repo.git").exists()
    assert any(cmd[:5] == ["git", "-C", str(trusted_repo), "remote", "remove"] and cmd[5] == "12305-mirror" for cmd in captured_cmds)


def test_list_vms_invokes_script_with_selected_vm_user(tmp_path: Path, monkeypatch) -> None:
    config_path = write_config(tmp_path)
    runner = CliRunner()
    captured_cmd: list[str] = []

    init_result = runner.invoke(app, ["init-state", "--config", str(config_path)])
    assert init_result.exit_code == 0

    def fake_run(
        cmd: list[str],
        check: bool,
        capture_output: bool,
        text: bool,
    ) -> subprocess.CompletedProcess[str]:
        nonlocal captured_cmd
        captured_cmd = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr("agent_sandbox.cli.subprocess.run", fake_run)

    result = runner.invoke(app, ["list-vms", "--vm-user", "root"])

    assert result.exit_code == 0
    assert captured_cmd[0].endswith("/scripts/list_running_vms.sh")
    assert captured_cmd[1:] == ["root"]


def test_list_vms_all_invokes_script_with_all_flag(tmp_path: Path, monkeypatch) -> None:
    config_path = write_config(tmp_path)
    runner = CliRunner()
    captured_cmd: list[str] = []

    init_result = runner.invoke(app, ["init-state", "--config", str(config_path)])
    assert init_result.exit_code == 0

    def fake_run(
        cmd: list[str],
        check: bool,
        capture_output: bool,
        text: bool,
    ) -> subprocess.CompletedProcess[str]:
        nonlocal captured_cmd
        captured_cmd = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr("agent_sandbox.cli.subprocess.run", fake_run)

    result = runner.invoke(app, ["list-vms", "--all", "--vm-user", "root"])

    assert result.exit_code == 0
    assert captured_cmd[0].endswith("/scripts/list_running_vms.sh")
    assert captured_cmd[1:] == ["--all", "root"]


def test_ssh_resolves_issue_number_to_vm_name(tmp_path: Path, monkeypatch) -> None:
    config_path = write_config(tmp_path)
    runner = CliRunner()
    captured_cmd: list[str] = []

    init_result = runner.invoke(app, ["init-state", "--config", str(config_path)])
    assert init_result.exit_code == 0

    def fake_run(
        cmd: list[str],
        check: bool,
        capture_output: bool,
        text: bool,
    ) -> subprocess.CompletedProcess[str]:
        nonlocal captured_cmd
        captured_cmd = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr("agent_sandbox.cli.subprocess.run", fake_run)

    result = runner.invoke(app, ["ssh", "12305"])

    assert result.exit_code == 0
    assert captured_cmd[0].endswith("/scripts/ssh.sh")
    assert captured_cmd[1:] == ["vm-12305", "agent"]


def test_logs_invokes_script_with_issue_number_and_repo_name(tmp_path: Path, monkeypatch) -> None:
    config_path = write_config(tmp_path)
    runner = CliRunner()
    captured_cmd: list[str] = []

    init_result = runner.invoke(app, ["init-state", "--config", str(config_path)])
    assert init_result.exit_code == 0

    def fake_run(cmd: list[str], check: bool) -> subprocess.CompletedProcess[str]:
        nonlocal captured_cmd
        captured_cmd = cmd
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr("agent_sandbox.cli.subprocess.run", fake_run)

    result = runner.invoke(app, ["logs", "vm-12305", "--repo-name", "AdminClient"])

    assert result.exit_code == 0
    assert captured_cmd[0].endswith("/scripts/tail_issue_vm_log.sh")
    assert captured_cmd[1:] == ["12305", "10", "AdminClient", "agent"]


def test_logs_omits_repo_name_positional_when_not_provided(tmp_path: Path, monkeypatch) -> None:
    config_path = write_config(tmp_path)
    runner = CliRunner()
    captured_cmd: list[str] = []

    init_result = runner.invoke(app, ["init-state", "--config", str(config_path)])
    assert init_result.exit_code == 0

    def fake_run(cmd: list[str], check: bool) -> subprocess.CompletedProcess[str]:
        nonlocal captured_cmd
        captured_cmd = cmd
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr("agent_sandbox.cli.subprocess.run", fake_run)

    result = runner.invoke(app, ["logs", "12305"])

    assert result.exit_code == 0
    assert captured_cmd[0].endswith("/scripts/tail_issue_vm_log.sh")
    assert captured_cmd[1:] == ["12305", "10"]


def test_logs_accepts_custom_initial_tail_line_count(tmp_path: Path, monkeypatch) -> None:
    config_path = write_config(tmp_path)
    runner = CliRunner()
    captured_cmd: list[str] = []

    init_result = runner.invoke(app, ["init-state", "--config", str(config_path)])
    assert init_result.exit_code == 0

    def fake_run(cmd: list[str], check: bool) -> subprocess.CompletedProcess[str]:
        nonlocal captured_cmd
        captured_cmd = cmd
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr("agent_sandbox.cli.subprocess.run", fake_run)

    result = runner.invoke(app, ["logs", "12305", "--lines", "25", "--vm-user", "debugger"])

    assert result.exit_code == 0
    assert captured_cmd[0].endswith("/scripts/tail_issue_vm_log.sh")
    assert captured_cmd[1:] == ["12305", "25", "", "debugger"]


def test_resume_agent_invokes_script_with_message_and_issue_number(tmp_path: Path, monkeypatch) -> None:
    config_path = write_config(tmp_path)
    runner = CliRunner()
    captured_cmd: list[str] = []

    init_result = runner.invoke(app, ["init-state", "--config", str(config_path)])
    assert init_result.exit_code == 0

    def fake_run(
        cmd: list[str],
        check: bool,
        capture_output: bool,
        text: bool,
    ) -> subprocess.CompletedProcess[str]:
        nonlocal captured_cmd
        captured_cmd = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr("agent_sandbox.cli.subprocess.run", fake_run)

    result = runner.invoke(
        app,
        [
            "resume-agent",
            "12305",
            "--config",
            str(config_path),
            "--message",
            "Continue from the failing restore",
        ],
    )

    assert result.exit_code == 0
    assert captured_cmd[0].endswith("/scripts/resume_issue_agent.sh")
    assert captured_cmd[1:] == ["Continue from the failing restore", "12305", "agent"]


def test_resume_agent_observe_invokes_script_with_tail_options(tmp_path: Path, monkeypatch) -> None:
    config_path = write_config(tmp_path)
    runner = CliRunner()
    captured_cmd: list[str] = []

    init_result = runner.invoke(app, ["init-state", "--config", str(config_path)])
    assert init_result.exit_code == 0

    def fake_run(cmd: list[str], check: bool) -> subprocess.CompletedProcess[str]:
        nonlocal captured_cmd
        captured_cmd = cmd
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr("agent_sandbox.cli.subprocess.run", fake_run)

    result = runner.invoke(
        app,
        [
            "resume-agent",
            "12305",
            "--message",
            "Continue from the failing restore",
            "--observe",
            "--lines",
            "25",
            "--vm-user",
            "debugger",
        ],
    )

    assert result.exit_code == 0
    assert captured_cmd[0].endswith("/scripts/resume_issue_agent.sh")
    assert captured_cmd[1:] == ["Continue from the failing restore", "12305", "debugger", "--observe", "25"]


def test_seed_discord_channel_creates_seed_entry_and_reports_cursor(tmp_path: Path) -> None:
    config_path = write_config(tmp_path)
    runner = CliRunner()

    init_result = runner.invoke(app, ["init-state", "--config", str(config_path)])
    assert init_result.exit_code == 0

    result = runner.invoke(
        app,
        [
            "seed-discord-channel",
            "--config",
            str(config_path),
            "123456789012345678",
        ],
    )

    assert result.exit_code == 0
    assert "123456789012345678" in result.stdout
    assert "since_cursor" in result.stdout

    payload = json.loads((tmp_path / "runtime" / "discord_commands.json").read_text(encoding="utf-8"))
    assert len(payload) == 1
    seeded = next(iter(payload.values()))
    assert seeded["channel_id"] == "123456789012345678"
    assert seeded["metadata"]["source"] == "discord_channel_seed"


def test_status_json_reports_healthy_state(tmp_path: Path, monkeypatch) -> None:
    config_path = write_config(tmp_path)
    runner = CliRunner()

    init_result = runner.invoke(app, ["init-state", "--config", str(config_path)])
    assert init_result.exit_code == 0

    create_result = runner.invoke(app, ["create-session", "--config", str(config_path), "active-session"])
    assert create_result.exit_code == 0

    sessions_path = tmp_path / "runtime" / "sessions.json"
    sessions_payload = json.loads(sessions_path.read_text(encoding="utf-8"))
    sessions_payload["expired-session"] = {
        **sessions_payload["active-session"],
        "session_id": "expired-session",
        "expires_at": (datetime.now(UTC) - timedelta(hours=1)).isoformat(),
    }
    sessions_path.write_text(json.dumps(sessions_payload), encoding="utf-8")

    monkeypatch.setattr(
        "agent_sandbox.cli._get_systemd_unit_state",
        lambda unit: UnitStatus(unit=unit, state="active", available=True, detail=None),
    )
    monkeypatch.setattr(
        "agent_sandbox.cli._check_health_endpoint",
        lambda name, url: EndpointStatus(
            name=name,
            url=url,
            reachable=True,
            status_code=200,
            error=None,
        ),
    )

    result = runner.invoke(app, ["status", "--config", str(config_path), "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["overall"] == "ok"
    assert payload["config"]["loaded"] is True
    assert payload["sessions"]["total"] == 2
    assert payload["sessions"]["active"] == 1
    assert payload["sessions"]["expired"] == 1
    assert payload["sessions"]["next_expiration"] is not None


def test_status_human_output_reports_degraded_state(tmp_path: Path, monkeypatch) -> None:
    config_path = write_config(tmp_path)
    runner = CliRunner()

    init_result = runner.invoke(app, ["init-state", "--config", str(config_path)])
    assert init_result.exit_code == 0

    email_store_path = tmp_path / "runtime" / "emails.json"
    email_store_path.unlink()

    monkeypatch.setattr(
        "agent_sandbox.cli._get_systemd_unit_state",
        lambda unit: UnitStatus(unit=unit, state="inactive", available=True, detail=None),
    )
    monkeypatch.setattr(
        "agent_sandbox.cli._check_health_endpoint",
        lambda name, url: EndpointStatus(
            name=name,
            url=url,
            reachable=False,
            status_code=None,
            error="connection refused",
        ),
    )

    result = runner.invoke(app, ["status", "--config", str(config_path)])

    assert result.exit_code == 0
    assert "Overall        degraded" in result.stdout
    assert "emails.json: degraded" in result.stdout
    assert "inactive (available)" in result.stdout
    assert "openai_relay: degraded" in result.stdout


def test_host_heartbeat_once_runs_single_cycle(tmp_path: Path, monkeypatch) -> None:
    config_path = write_config(tmp_path)
    runner = CliRunner()
    calls: list[str] = []

    class _FakeWorker:
        def run_once_with_lock(self):
            calls.append("run_once_with_lock")
            return SimpleNamespace(streams_polled=3, discovered_feedback=1, next_sleep_seconds=31.5)

        def run_once(self):
            raise AssertionError("run_once should not be called directly in --once mode")

        def run_forever(self) -> None:
            raise AssertionError("run_forever should not be called in --once mode")

    monkeypatch.setattr("agent_sandbox.cli.build_host_heartbeat_worker", lambda cfg: _FakeWorker())

    result = runner.invoke(
        app,
        ["host-heartbeat", "--config", str(config_path), "--once", "--log-level", "DEBUG"],
    )

    assert result.exit_code == 0
    assert calls == ["run_once_with_lock"]
    assert "streams=3" in result.output
    assert "discovered=1" in result.output
