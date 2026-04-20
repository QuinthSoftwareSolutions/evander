import json
import subprocess
from types import SimpleNamespace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from typer.testing import CliRunner

from agent_sandbox.cli import EndpointStatus, UnitStatus, app
from agent_sandbox.models import ApprovalRequest, LocalIssueRecord, VmActionResult, VmSummary
from agent_sandbox.teams_auth import InvalidClientConfigurationError

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


def test_help_groups_commands_by_panel_then_alphabetically() -> None:
    runner = CliRunner()

    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    output = result.output
    panel_order = [
        "Health and State",
        "Sessions and Feedback",
        "Issue Lifecycle",
        "VM Operations",
        "Seeding and Fixtures",
        "Background Workers",
    ]
    panel_positions = [output.index(panel) for panel in panel_order]
    assert panel_positions == sorted(panel_positions)

    assert (
        output.index("add-tool-permission")
        < output.index("approve")
        < output.index("attach-pr-feedback")
        < output.index("create-session")
        < output.index("delete-expired-sessions")
        < output.index("delete-session")
        < output.index("list-sessions")
        < output.index("prune-sessions")
        < output.index("renew-session")
        < output.index("revoke-session")
        < output.index("revoke-tool-permission")
    )
    assert (
        output.index("create-issue-vm") < output.index("create-local-issue") < output.index("destroy-issue-vm") < output.index("list-local-issues") < output.index("prune-orphaned-issue-state")
    )
    assert output.index("create-vm") < output.index("destroy-vm") < output.index("disable-agent") < output.index("enable-agent") < output.index("list-vms") < output.index("logs") < output.index("resume-agent") < output.index("shutdown-vm") < output.index("ssh") < output.index("start-vm")
    assert output.index("seed-discord-channel") < output.index("seed-email")
    assert "init-state" in output and "Create runtime state files." in output
    assert "migrate-global-config-to-root" in output
    assert "migrate-ado-worker-env-to-root" in output
    assert "migrate-issue-setup-env-to-root" in output
    assert "migrate-pr-template-to-root" in output
    assert "migrate-directory-ownership" in output
    assert "Backfill missing directory ownership fields" in output
    assert "create-issue-vm" in output and "Create and bootstrap an issue VM." in output
    assert "create-vm" in output and "Create and bootstrap a standalone VM." in output
    assert "teams-auth" in output
    assert "host-heartbeat" in output and "Run the host heartbeat worker." in output
    assert "teams-ingest" in output and "Run the Teams ingest worker." in output
    assert "trigger-orchestrator" in output and "Run the trigger orchestrator worker." in output


def test_teams_auth_help_exposes_subcommands() -> None:
    runner = CliRunner()

    result = runner.invoke(app, ["teams-auth", "--help"])

    assert result.exit_code == 0
    output = result.output
    assert "login" in output
    assert "status" in output
    assert "logout" in output
    assert "list" in output
    assert "channels" in output


def test_teams_auth_commands_use_admin_service_and_redact_tokens(tmp_path: Path, monkeypatch) -> None:
    config_path = write_config(tmp_path)
    runner = CliRunner()
    calls: list[str] = []

    class FakeAdminService:
        def __init__(self, config) -> None:
            self.config = config

        def teams_auth_login(self, *, directory="root"):
            calls.append("login")
            return {
                "status": "device_code_pending",
                "message": "Go to https://microsoft.com/devicelogin and enter code ABCD-EFGH",
                "verification_uri": "https://microsoft.com/devicelogin",
                "user_code": "ABCD-EFGH",
                "access_token": "should-not-leak",
                "refresh_token": "should-not-leak-either",
            }

        def teams_auth_complete_login(self, login_session, *, directory="root"):
            calls.append("complete_login")
            return {
                "status": "authenticated",
                "backend": "delegated_graph_api",
                "account_identifier": "alice@example.com",
                "tenant_id": "contoso",
                "expires_at": "2026-05-01T12:00:00Z",
                "access_token": "should-not-leak",
            }

        def teams_auth_status(self, *, directory="root"):
            calls.append("status")
            return {
                "status": "authenticated",
                "backend": "delegated_graph_api",
                "account_identifier": "alice@example.com",
                "tenant_id": "contoso",
                "expires_at": "2026-05-01T12:00:00Z",
                "access_token": "should-not-leak",
            }

        def teams_auth_list(self):
            calls.append("list")
            return [
                {
                    "status": "authenticated",
                    "account": "alice@example.com",
                    "tenant": "contoso",
                    "refresh_token": "should-not-leak",
                },
                {
                    "status": "expired",
                    "account": "bob@example.com",
                    "tenant": "fabrikam",
                },
            ]

        def teams_auth_logout(self, *, directory="root"):
            calls.append("logout")
            return None

        def teams_auth_list_channels(
            self,
            *,
            directory="root",
            team_id="",
            channel_name=None,
            include_all=False,
        ):
            calls.append("list_channels")
            return {
                "team_id": team_id,
                "channel_name": channel_name,
                "match_count": 1,
                "channels": [
                    {
                        "display_name": "Truman",
                        "channel_id": "19:abc@thread.skype",
                        "allowlist_entry": f"{team_id}/19:abc@thread.skype",
                    }
                ],
            }

    monkeypatch.setattr("agent_sandbox.cli.AdminService", FakeAdminService)

    login_result = runner.invoke(app, ["teams-auth", "login", "--config", str(config_path)])
    status_result = runner.invoke(app, ["teams-auth", "status", "--config", str(config_path)])
    list_result = runner.invoke(app, ["teams-auth", "list", "--config", str(config_path)])
    logout_result = runner.invoke(app, ["teams-auth", "logout", "--config", str(config_path)])
    channels_result = runner.invoke(
        app,
        [
            "teams-auth",
            "channels",
            "--config",
            str(config_path),
            "--team-id",
            "team-123",
            "--name",
            "Truman",
        ],
    )

    assert login_result.exit_code == 0
    assert "ABCD-EFGH" in login_result.output
    assert "should-not-leak" not in login_result.output
    assert "<redacted>" in login_result.output
    assert "alice@example.com" in login_result.output

    assert status_result.exit_code == 0
    assert "alice@example.com" in status_result.output
    assert "delegated_graph_api" in status_result.output
    assert "should-not-leak" not in status_result.output

    assert list_result.exit_code == 0
    assert "Entries: 2" in list_result.output
    assert "bob@example.com" in list_result.output
    assert "should-not-leak" not in list_result.output

    assert channels_result.exit_code == 0
    assert "Truman" in channels_result.output
    assert "19:abc@thread.skype" in channels_result.output
    assert "team-123/19:abc@thread.skype" in channels_result.output

    assert logout_result.exit_code == 0
    assert "Completed successfully." in logout_result.output
    assert calls == ["login", "complete_login", "status", "list", "logout", "list_channels"]


def test_teams_auth_login_reports_auth_errors_without_traceback(tmp_path: Path, monkeypatch) -> None:
    config_path = write_config(tmp_path)
    runner = CliRunner()

    class FakeAdminService:
        def __init__(self, config) -> None:
            self.config = config

        def teams_auth_login(self, *, directory="root"):
            return {
                "status": "device_code_pending",
                "message": "Open the browser and authenticate.",
                "verification_uri": "https://login.microsoft.com/device",
                "user_code": "ABCD-EFGH",
            }

        def teams_auth_complete_login(self, login_session, *, directory="root"):
            raise InvalidClientConfigurationError(
                "Microsoft Entra rejected the device-code flow because the configured app registration "
                "is not enabled as a public client."
            )

    monkeypatch.setattr("agent_sandbox.cli.AdminService", FakeAdminService)

    result = runner.invoke(app, ["teams-auth", "login", "--config", str(config_path)])

    assert result.exit_code == 1
    assert "Teams auth login failed:" in result.output
    assert "not enabled as a public client" in result.output
    assert "Traceback" not in result.output


def test_teams_auth_login_renders_datetime_details_without_traceback(tmp_path: Path, monkeypatch) -> None:
    config_path = write_config(tmp_path)
    runner = CliRunner()

    class FakeAdminService:
        def __init__(self, config) -> None:
            self.config = config

        def teams_auth_login(self, *, directory="root"):
            return {
                "status": "device_code_pending",
                "message": "Open the browser and authenticate.",
                "verification_uri": "https://login.microsoft.com/device",
                "user_code": "ABCD-EFGH",
            }

        def teams_auth_complete_login(self, login_session, *, directory="root"):
            return {
                "status": "authenticated",
                "backend": "delegated_graph_api",
                "account_identifier": "example-teams",
                "tenant_id": "contoso",
                "expires_at": datetime(2026, 4, 10, 7, 27, 3, tzinfo=UTC),
                "issued_at": datetime(2026, 4, 10, 6, 27, 3, tzinfo=UTC),
                "store_path": "/tmp/teams.json",
            }

    monkeypatch.setattr("agent_sandbox.cli.AdminService", FakeAdminService)

    result = runner.invoke(app, ["teams-auth", "login", "--config", str(config_path)])

    assert result.exit_code == 0
    assert "Teams auth status" in result.output
    assert "2026-04-10T06:27:03+00:00" in result.output
    assert "Traceback" not in result.output


def test_init_state_creates_directories_store_with_root(tmp_path: Path) -> None:
    config_path = write_config(tmp_path)
    runner = CliRunner()

    result = runner.invoke(app, ["init-state", "--config", str(config_path)])
    assert result.exit_code == 0

    directories_payload = json.loads((tmp_path / "runtime" / "directories.json").read_text(encoding="utf-8"))
    assert directories_payload == {
        "root": {
            "id": "root",
            "name": "root",
            "parent_id": None,
            "created_at": directories_payload["root"]["created_at"],
            "metadata": {},
        }
    }


def test_migrate_directory_ownership_backfills_missing_fields_only(tmp_path: Path) -> None:
    config_path = write_config(tmp_path)
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    (runtime_dir / "sessions.json").write_text(
        json.dumps(
            {
                "session-a": {"session_id": "session-a", "allowed_tools": [], "expires_at": "2099-01-01T00:00:00Z"},
                "session-b": {
                    "session_id": "session-b",
                    "allowed_tools": [],
                    "expires_at": "2099-01-01T00:00:00Z",
                    "directory_path": "root/team",
                },
            }
        ),
        encoding="utf-8",
    )
    (runtime_dir / "local-issues.json").write_text(
        json.dumps(
            {
                "1": {"issue_number": "1", "title": "A", "body": "", "yaml_path": "/tmp/1.yaml"},
                "2": {
                    "issue_number": "2",
                    "title": "B",
                    "body": "",
                    "yaml_path": "/tmp/2.yaml",
                    "directory_path": "root/team",
                },
            }
        ),
        encoding="utf-8",
    )
    (runtime_dir / "pr-map.json").write_text(
        json.dumps(
            {
                "entries": {
                    "github:org/repo:1": {"provider": "github", "repo": "org/repo", "pr_id": "1", "session_id": "session-a"},
                    "github:org/repo:2": {
                        "provider": "github",
                        "repo": "org/repo",
                        "pr_id": "2",
                        "session_id": "session-b",
                        "directory_path": "root/team",
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    (runtime_dir / "ado-processed-work-items.json").write_text(
        json.dumps(
            {
                "10": {"id": 10, "yaml_path": "/tmp/10.yaml"},
                "11": {"id": 11, "yaml_path": "/tmp/11.yaml", "directory_path": "root/team"},
            }
        ),
        encoding="utf-8",
    )

    runner = CliRunner()
    result = runner.invoke(app, ["migrate-directory-ownership", "--config", str(config_path)])

    assert result.exit_code == 0
    assert "Backfilled 3 record(s) across 3 file(s)" in result.output

    sessions = json.loads((runtime_dir / "sessions.json").read_text(encoding="utf-8"))
    assert sessions["session-a"]["directory_path"] == "root"
    assert sessions["session-b"]["directory_path"] == "root/team"

    issues = json.loads((runtime_dir / "local-issues.json").read_text(encoding="utf-8"))
    assert issues["1"]["directory_path"] == "root"
    assert issues["2"]["directory_path"] == "root/team"

    pr_map = json.loads((runtime_dir / "pr-map.json").read_text(encoding="utf-8"))
    assert pr_map["entries"]["github:org/repo:1"]["directory_path"] == "root"
    assert pr_map["entries"]["github:org/repo:2"]["directory_path"] == "root/team"

    ado_processed = json.loads((runtime_dir / "ado-processed-work-items.json").read_text(encoding="utf-8"))
    assert ado_processed["10"]["directory_path"] == "root"
    assert ado_processed["11"]["directory_path"] == "root/team"


def test_list_approvals_outputs_queue_health_and_redacted_payload(tmp_path: Path) -> None:
    config_path = write_config(tmp_path)
    runner = CliRunner()
    assert runner.invoke(app, ["init-state", "--config", str(config_path)]).exit_code == 0

    approvals_path = tmp_path / "runtime" / "approvals.json"
    approvals_path.write_text(
        json.dumps(
            {
                "approval-1": ApprovalRequest(
                    request_id="approval-1",
                    session_id="session-1",
                    action_type="email_send_approved",
                    tool_name="email_send_approved",
                    payload_digest="abc",
                    human_summary="send email",
                    payload={"token": "secret", "body": "hello"},
                    expires_at=datetime.now(UTC) + timedelta(hours=1),
                ).model_dump(mode="json")
            }
        ),
        encoding="utf-8",
    )

    result = runner.invoke(app, ["list-approvals", "--config", str(config_path), "--show-payload"])

    assert result.exit_code == 0
    assert "Queue total=1 pending=1 approved=0 denied=0 expired=0" in result.output
    assert "approval-1" in result.output
    assert "<redacted>" in result.output


def test_show_approval_and_approve_record_reason(tmp_path: Path) -> None:
    config_path = write_config(tmp_path)
    runner = CliRunner()
    assert runner.invoke(app, ["init-state", "--config", str(config_path)]).exit_code == 0

    approvals_path = tmp_path / "runtime" / "approvals.json"
    approvals_path.write_text(
        json.dumps(
            {
                "approval-1": ApprovalRequest(
                    request_id="approval-1",
                    session_id="session-1",
                    action_type="email_send_approved",
                    tool_name="email_send_approved",
                    payload_digest="abc",
                    human_summary="send email",
                    payload={"token": "secret", "body": "hello"},
                    expires_at=datetime.now(UTC) + timedelta(hours=1),
                ).model_dump(mode="json")
            }
        ),
        encoding="utf-8",
    )

    show_result = runner.invoke(app, ["show-approval", "--config", str(config_path), "approval-1"])
    approve_result = runner.invoke(
        app,
        ["approve", "--config", str(config_path), "--reason", "looks safe", "approval-1", "alice"],
    )

    assert show_result.exit_code == 0
    assert "<redacted>" in show_result.output
    assert approve_result.exit_code == 0
    approvals = json.loads(approvals_path.read_text(encoding="utf-8"))
    assert approvals["approval-1"]["decision_reason"] == "looks safe"
    assert approvals["approval-1"]["decision_history"][0]["reason"] == "looks safe"


def test_migrate_global_config_to_root_materializes_all_domain_files(tmp_path: Path) -> None:
    config_path = write_config(tmp_path, enable_directory_scoped_config=True)
    runner = CliRunner()

    init_result = runner.invoke(app, ["init-state", "--config", str(config_path)])
    assert init_result.exit_code == 0

    result = runner.invoke(app, ["migrate-global-config-to-root", "--config", str(config_path)])

    assert result.exit_code == 0
    assert "written=7 skipped=0 unchanged=0" in result.output
    runtime_dir = tmp_path / "runtime"
    assert (runtime_dir / "directory-config" / "root" / "policy.json").exists()
    assert (runtime_dir / "directory-config" / "root" / "providers.json").exists()
    assert (runtime_dir / "directory-config" / "root" / "email.json").exists()
    assert (runtime_dir / "directory-config" / "root" / "heartbeat.json").exists()
    assert (runtime_dir / "directory-config" / "root" / "ado-worker.json").exists()
    assert (runtime_dir / "directory-config" / "root" / "issue-setup.json").exists()
    assert (runtime_dir / "directory-config" / "root" / "vm.json").exists()

    directories_payload = json.loads((runtime_dir / "directories.json").read_text(encoding="utf-8"))
    assert directories_payload["root"]["metadata"]["directory_config"]["domains"] == [
        "ado_worker",
        "email",
        "heartbeat",
        "issue_setup",
        "policy",
        "providers",
        "vm",
    ]


def test_migrate_global_config_to_root_skips_existing_divergent_root_domains_without_overwrite(tmp_path: Path) -> None:
    config_path = write_config(tmp_path, enable_directory_scoped_config=True)
    runner = CliRunner()

    assert runner.invoke(app, ["init-state", "--config", str(config_path)]).exit_code == 0
    set_result = runner.invoke(
        app,
        [
            "directories",
            "config-set",
            "--config",
            str(config_path),
            "--domain",
            "policy",
            "root",
            '{"default_session_ttl_seconds": 120}',
        ],
    )
    assert set_result.exit_code == 0

    result = runner.invoke(app, ["migrate-global-config-to-root", "--config", str(config_path)])

    assert result.exit_code == 0
    assert "written=0 skipped=1 unchanged=6" in result.output
    assert "skipped: policy" in result.output
    policy_payload = json.loads(
        (tmp_path / "runtime" / "directory-config" / "root" / "policy.json").read_text(encoding="utf-8")
    )
    assert policy_payload == {"default_session_ttl_seconds": 120}


def test_migrate_global_config_to_root_overwrites_when_requested(tmp_path: Path) -> None:
    config_path = write_config(tmp_path, enable_directory_scoped_config=True)
    runner = CliRunner()

    assert runner.invoke(app, ["init-state", "--config", str(config_path)]).exit_code == 0
    assert runner.invoke(
        app,
        [
            "directories",
            "config-set",
            "--config",
            str(config_path),
            "--domain",
            "policy",
            "root",
            '{"default_session_ttl_seconds": 120}',
        ],
    ).exit_code == 0

    result = runner.invoke(
        app,
        ["migrate-global-config-to-root", "--config", str(config_path), "--overwrite"],
    )

    assert result.exit_code == 0
    assert "written=1 skipped=0 unchanged=6" in result.output
    policy_payload = json.loads(
        (tmp_path / "runtime" / "directory-config" / "root" / "policy.json").read_text(encoding="utf-8")
    )
    assert policy_payload["default_session_ttl_seconds"] == 28800


def test_migrate_global_config_to_root_recovers_when_metadata_lists_missing_root_file(tmp_path: Path) -> None:
    config_path = write_config(tmp_path, enable_directory_scoped_config=True)
    runner = CliRunner()

    assert runner.invoke(app, ["init-state", "--config", str(config_path)]).exit_code == 0
    runtime_dir = tmp_path / "runtime"
    directories_path = runtime_dir / "directories.json"
    directories_payload = json.loads(directories_path.read_text(encoding="utf-8"))
    directories_payload["root"]["metadata"] = {"directory_config": {"domains": ["providers"]}}
    directories_path.write_text(json.dumps(directories_payload, indent=2, sort_keys=True), encoding="utf-8")

    result = runner.invoke(app, ["migrate-global-config-to-root", "--config", str(config_path)])

    assert result.exit_code == 0
    assert "written=7 skipped=0 unchanged=0" in result.output
    assert (runtime_dir / "directory-config" / "root" / "providers.json").exists()


def test_migrate_ado_worker_env_to_root_materializes_root_domain_file(tmp_path: Path) -> None:
    config_path = write_config(tmp_path, enable_directory_scoped_config=True)
    runner = CliRunner()

    assert runner.invoke(app, ["init-state", "--config", str(config_path)]).exit_code == 0
    env_file = tmp_path / "config" / "ado-worker.env"
    env_file.parent.mkdir(parents=True, exist_ok=True)
    env_file.write_text(
        "\n".join(
            [
                "ADO_PROJECT_OR_SOLUTION=/tmp/Example.slnx",
                "ADO_PREWARM_TARGET=/tmp/Prewarm.slnx",
                "ADO_REPO_PATH=/tmp/repo",
                "ADO_BOOTSTRAP_MSSQL=true",
                "ADO_MSSQL_SA_PASSWORD=SecretPassw0rd!",
                "ADO_FRONTEND_INSTALL_ROOTS=/tmp/repo/Src/Clients,/tmp/repo/Src/Admin",
            ]
        ),
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "migrate-ado-worker-env-to-root",
            "--config",
            str(config_path),
        ],
    )

    assert result.exit_code == 0
    assert "ADO worker env migration complete: written ado_worker" in result.output
    runtime_dir = tmp_path / "runtime"
    payload = json.loads((runtime_dir / "directory-config" / "root" / "ado-worker.json").read_text(encoding="utf-8"))
    assert payload == {
        "ado_area_filters": [],
        "ado_bootstrap_dotnet": True,
        "ado_bootstrap_frontend_deps": False,
        "ado_bootstrap_mssql": True,
        "ado_bootstrap_nuget": True,
        "ado_bootstrap_playwright": False,
        "ado_frontend_install_roots": ["/tmp/repo/Src/Clients", "/tmp/repo/Src/Admin"],
        "ado_prewarm_target": "/tmp/Prewarm.slnx",
        "ado_project_or_solution": "/tmp/Example.slnx",
        "ado_repo_source": "/tmp/repo",
        "ado_setup_vms": True,
        "ado_team_filters": [],
        "create_prs_automatically": True,
        "create_prs_as_draft": False,
    }
    directories_payload = json.loads((runtime_dir / "directories.json").read_text(encoding="utf-8"))
    assert "ado_worker" in directories_payload["root"]["metadata"]["directory_config"]["domains"]


def test_migrate_issue_setup_env_to_root_materializes_root_domain_file(tmp_path: Path) -> None:
    config_path = write_config(tmp_path, enable_directory_scoped_config=True)
    runner = CliRunner()

    assert runner.invoke(app, ["init-state", "--config", str(config_path)]).exit_code == 0
    env_file = tmp_path / "config" / "issue-setup.env"
    env_file.parent.mkdir(parents=True, exist_ok=True)
    env_file.write_text(
        "\n".join(
            [
                "AGENT_SANDBOX_GIT_ROOT=/tmp/git-remotes",
                "AGENT_SANDBOX_GIT_HOST=10.1.2.3",
                "AGENT_SANDBOX_GIT_USER=gitbot",
                "AGENT_SANDBOX_GIT_SSH_ALIAS=issue-git",
                "AGENT_DEFAULT_MODEL=gpt-5.3-codex",
                "AGENT_POST_EXEC_VERIFICATION_PROMPT=verify everything",
                "ENABLE_GUEST_HEARTBEAT=1",
                "GUEST_HEARTBEAT_PROMPT=continue working",
                "AGENT_SANDBOX_AZURE_DEVOPS_PR_PAT_ENV=TEAM_ISSUE_SETUP_ADO_PAT",
            ]
        ),
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "migrate-issue-setup-env-to-root",
            "--config",
            str(config_path),
            "--env-file",
            str(env_file),
        ],
    )

    assert result.exit_code == 0
    assert "Issue setup env migration complete: written issue_setup" in result.output
    runtime_dir = tmp_path / "runtime"
    payload = json.loads((runtime_dir / "directory-config" / "root" / "issue-setup.json").read_text(encoding="utf-8"))
    assert payload == {
        "default_model": "gpt-5.3-codex",
        "enable_guest_heartbeat": True,
        "git_host": "10.1.2.3",
        "git_root": "/tmp/git-remotes",
        "git_ssh_alias": "issue-git",
        "git_user": "gitbot",
        "guest_heartbeat_prompt": "continue working",
        "issue_setup_azure_devops_pr_pat_env": "TEAM_ISSUE_SETUP_ADO_PAT",
        "post_exec_verification_prompt": "verify everything",
    }
    directories_payload = json.loads((runtime_dir / "directories.json").read_text(encoding="utf-8"))
    assert "issue_setup" in directories_payload["root"]["metadata"]["directory_config"]["domains"]


def test_migrate_pr_template_to_root_materializes_root_domain_file(tmp_path: Path) -> None:
    config_path = write_config(tmp_path, enable_directory_scoped_config=True)
    runner = CliRunner()

    assert runner.invoke(app, ["init-state", "--config", str(config_path)]).exit_code == 0
    template_file = tmp_path / "config" / "pr-template.md"
    template_file.parent.mkdir(parents=True, exist_ok=True)
    template_file.write_text("Team checklist\n- [ ] Review\n", encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "migrate-pr-template-to-root",
            "--config",
            str(config_path),
        ],
    )

    assert result.exit_code == 0
    assert "PR template migration complete: written pr_template" in result.output
    runtime_dir = tmp_path / "runtime"
    payload = json.loads((runtime_dir / "directory-config" / "root" / "pr-template.json").read_text(encoding="utf-8"))
    assert payload == {"pr_template_markdown": "Team checklist\n- [ ] Review\n"}
    directories_payload = json.loads((runtime_dir / "directories.json").read_text(encoding="utf-8"))
    assert "pr_template" in directories_payload["root"]["metadata"]["directory_config"]["domains"]


def test_migrate_ado_worker_env_to_root_imports_env_file(tmp_path: Path) -> None:
    config_path = write_config(tmp_path, enable_directory_scoped_config=True)
    env_file = tmp_path / "ado-worker.env"
    env_file.write_text(
        "\n".join(
                [
                    'ADO_PROJECT_OR_SOLUTION="/tmp/Example.slnx"',
                    'ADO_REPO_PATH="/tmp/repo"',
                    'ADO_AREA_FILTERS="Root\\Area,Root\\Other"',
                    'ADO_BOOTSTRAP_MSSQL="true"',
                    'ADO_MSSQL_SA_PASSWORD="TopSecret!"',
                ]
        )
        + "\n",
        encoding="utf-8",
    )
    runner = CliRunner()

    assert runner.invoke(app, ["init-state", "--config", str(config_path)]).exit_code == 0
    result = runner.invoke(
        app,
        [
            "migrate-ado-worker-env-to-root",
            "--config",
            str(config_path),
            "--env-file",
            str(env_file),
        ],
    )

    assert result.exit_code == 0
    assert "written ado_worker" in result.output
    payload = json.loads((tmp_path / "runtime" / "directory-config" / "root" / "ado-worker.json").read_text("utf-8"))
    assert payload["ado_project_or_solution"] == "/tmp/Example.slnx"
    assert payload["ado_repo_source"] == "/tmp/repo"
    assert payload["ado_area_filters"] == ["Root\\Area", "Root\\Other"]
    assert payload["ado_bootstrap_mssql"] is True
    assert "ado_mssql_sa_password_env" not in payload
    assert "ado_worker" in json.loads((tmp_path / "runtime" / "directories.json").read_text(encoding="utf-8"))["root"]["metadata"]["directory_config"]["domains"]


def test_migrate_issue_setup_env_to_root_imports_env_file_and_env_overrides(tmp_path: Path, monkeypatch) -> None:
    config_path = write_config(tmp_path, enable_directory_scoped_config=True)
    env_file = tmp_path / "config" / "issue-setup.env"
    env_file.parent.mkdir(parents=True, exist_ok=True)
    env_file.write_text(
        "\n".join(
            [
                'AGENT_SANDBOX_GIT_ROOT="/tmp/from-file"',
                'AGENT_SANDBOX_GIT_HOST="10.55.0.1"',
                'AGENT_SANDBOX_GIT_USER="env-file-user"',
                'AGENT_SANDBOX_GIT_SSH_ALIAS="env-file-alias"',
                'AGENT_DEFAULT_MODEL="gpt-5.2-codex"',
                'AGENT_POST_EXEC_VERIFICATION_PROMPT="verify from file"',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("ENABLE_GUEST_HEARTBEAT", "true")
    monkeypatch.setenv("GUEST_HEARTBEAT_PROMPT", "continue from env")
    monkeypatch.setenv("AZURE_DEVOPS_PR_PAT", "super-secret-pat")
    runner = CliRunner()

    assert runner.invoke(app, ["init-state", "--config", str(config_path)]).exit_code == 0
    result = runner.invoke(
        app,
        [
            "migrate-issue-setup-env-to-root",
            "--config",
            str(config_path),
            "--env-file",
            str(env_file),
        ],
    )

    assert result.exit_code == 0
    assert "written issue_setup" in result.output
    payload = json.loads((tmp_path / "runtime" / "directory-config" / "root" / "issue-setup.json").read_text("utf-8"))
    assert payload["git_root"] == "/tmp/from-file"
    assert payload["git_host"] == "10.55.0.1"
    assert payload["git_user"] == "env-file-user"
    assert payload["git_ssh_alias"] == "env-file-alias"
    assert payload["default_model"] == "gpt-5.2-codex"
    assert payload["post_exec_verification_prompt"] == "verify from file"
    assert payload["enable_guest_heartbeat"] is True
    assert payload["guest_heartbeat_prompt"] == "continue from env"
    assert payload["issue_setup_azure_devops_pr_pat_env"] == "AZURE_DEVOPS_PR_PAT"


def test_create_session_uses_directory_issue_setup_default_model_when_model_not_provided(tmp_path: Path) -> None:
    config_path = write_config(tmp_path, enable_directory_scoped_config=True)
    runner = CliRunner()

    assert runner.invoke(app, ["init-state", "--config", str(config_path)]).exit_code == 0
    assert runner.invoke(
        app,
        ["directories", "create", "--config", str(config_path), "--parent", "root", "team"],
    ).exit_code == 0
    assert runner.invoke(
        app,
        [
            "directories",
            "config-set",
            "--config",
            str(config_path),
            "--domain",
            "issue_setup",
            "root/team",
            '{"default_model": "gpt-5.3-codex"}',
        ],
    ).exit_code == 0

    result = runner.invoke(
        app,
        ["create-session", "--config", str(config_path), "--directory", "root/team", "team-session"],
    )

    assert result.exit_code == 0
    sessions = json.loads((tmp_path / "runtime" / "sessions.json").read_text(encoding="utf-8"))
    assert sessions["team-session"]["model"] == "gpt-5.3-codex"


def test_resume_agent_fails_when_model_unresolved_for_session(tmp_path: Path) -> None:
    config_path = write_config(tmp_path)
    runner = CliRunner()

    assert runner.invoke(app, ["init-state", "--config", str(config_path)]).exit_code == 0
    assert runner.invoke(app, ["create-session", "--config", str(config_path), "--vm-id", "vm-12305", "issue-12305-session"]).exit_code == 0

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

    assert result.exit_code != 0
    assert "Cannot resolve AGENT_MODEL" in result.output


def test_directories_cli_supports_crud_and_show(tmp_path: Path) -> None:
    config_path = write_config(tmp_path, enable_directory_scoped_config=True)
    runner = CliRunner()

    assert runner.invoke(app, ["init-state", "--config", str(config_path)]).exit_code == 0

    create_team = runner.invoke(
        app,
        ["directories", "create", "--config", str(config_path), "--parent", "root", "team"],
    )
    assert create_team.exit_code == 0
    assert create_team.output.strip() == "root/team"

    create_lab = runner.invoke(
        app,
        ["directories", "create", "--config", str(config_path), "--parent", "root/team", "lab"],
    )
    assert create_lab.exit_code == 0
    assert create_lab.output.strip() == "root/team/lab"

    listed = runner.invoke(app, ["directories", "list", "--config", str(config_path), "--parent", "root"])
    assert listed.exit_code == 0
    payload = json.loads(listed.output)
    assert [item["path"] for item in payload["children"]] == ["root/team"]

    renamed = runner.invoke(
        app,
        ["directories", "rename", "--config", str(config_path), "root/team/lab", "sandbox"],
    )
    assert renamed.exit_code == 0
    assert renamed.output.strip() == "root/team/sandbox"

    moved = runner.invoke(
        app,
        ["directories", "move", "--config", str(config_path), "root/team/sandbox", "--new-parent", "root"],
    )
    assert moved.exit_code == 0
    assert moved.output.strip() == "root/sandbox"

    shown = runner.invoke(app, ["directories", "show", "--config", str(config_path), "root/sandbox"])
    assert shown.exit_code == 0
    show_payload = json.loads(shown.output)
    assert show_payload["details"]["directory"]["path"] == "root/sandbox"
    assert [item["path"] for item in show_payload["details"]["ancestry"]] == ["root", "root/sandbox"]

    deleted = runner.invoke(app, ["directories", "delete", "--config", str(config_path), "root/sandbox"])
    assert deleted.exit_code == 0
    delete_payload = json.loads(deleted.output)
    assert delete_payload["deleted_count"] == 1


def test_directories_cli_rejects_edit_surfaces_when_feature_flag_disabled(tmp_path: Path) -> None:
    config_path = write_config(tmp_path, enable_directory_scoped_config=False)
    runner = CliRunner()

    assert runner.invoke(app, ["init-state", "--config", str(config_path)]).exit_code == 0

    result = runner.invoke(
        app,
        ["directories", "create", "--config", str(config_path), "--parent", "root", "team"],
    )

    assert result.exit_code != 0
    assert "directory-scoped config is disabled" in result.output


def test_directories_cli_supports_config_show_and_set(tmp_path: Path) -> None:
    config_path = write_config(tmp_path, enable_directory_scoped_config=True)
    runner = CliRunner()

    assert runner.invoke(app, ["init-state", "--config", str(config_path)]).exit_code == 0
    assert runner.invoke(
        app,
        ["directories", "create", "--config", str(config_path), "--parent", "root", "team"],
    ).exit_code == 0

    set_result = runner.invoke(
        app,
        [
            "directories",
            "config-set",
            "--config",
            str(config_path),
            "--domain",
            "policy",
            "root/team",
            '{"default_session_ttl_seconds": 120}',
        ],
    )
    assert set_result.exit_code == 0
    set_payload = json.loads(set_result.output)
    assert set_payload["config"] == {"default_session_ttl_seconds": 120}

    show_result = runner.invoke(
        app,
        ["directories", "config-show", "--config", str(config_path), "--domain", "policy", "root/team"],
    )
    assert show_result.exit_code == 0
    show_payload = json.loads(show_result.output)
    assert show_payload["raw"]["config"] == {"default_session_ttl_seconds": 120}
    assert show_payload["effective"]["resolution"]["effective_config"]["default_session_ttl_seconds"] == 120


def test_directories_cli_root_config_becomes_inherited_parent_layer(tmp_path: Path) -> None:
    config_path = write_config(tmp_path, enable_directory_scoped_config=True)
    runner = CliRunner()

    assert runner.invoke(app, ["init-state", "--config", str(config_path)]).exit_code == 0
    assert runner.invoke(
        app,
        ["directories", "create", "--config", str(config_path), "--parent", "root", "team"],
    ).exit_code == 0

    root_set = runner.invoke(
        app,
        [
            "directories",
            "config-set",
            "--config",
            str(config_path),
            "--domain",
            "policy",
            "root",
            '{"default_session_ttl_seconds": 180}',
        ],
    )
    assert root_set.exit_code == 0

    team_show = runner.invoke(
        app,
        ["directories", "config-show", "--config", str(config_path), "--domain", "policy", "root/team"],
    )
    assert team_show.exit_code == 0
    payload = json.loads(team_show.output)
    assert payload["effective"]["resolution"]["effective_config"]["default_session_ttl_seconds"] == 180
    assert payload["effective"]["resolution"]["applied_layers"][0]["directory_path"] == "root"


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
    assert "prompt_agent" in manifest["allowed_tools"]
    assert "respond_to_agent" in manifest["allowed_tools"]


def test_create_session_uses_configured_default_ttl_when_no_ttl_is_provided(tmp_path: Path) -> None:
    config_path = write_config(tmp_path)
    config_path.write_text(
        config_path.read_text(encoding="utf-8") + '\ndefault_session_ttl_seconds: 172800\n',
        encoding="utf-8",
    )
    runner = CliRunner()

    init_result = runner.invoke(app, ["init-state", "--config", str(config_path)])
    assert init_result.exit_code == 0

    before = datetime.now(UTC)
    result = runner.invoke(app, ["create-session", "--config", str(config_path), "session-1"])
    after = datetime.now(UTC)

    assert result.exit_code == 0
    sessions = json.loads((tmp_path / "runtime" / "sessions.json").read_text(encoding="utf-8"))
    expires_at = datetime.fromisoformat(sessions["session-1"]["expires_at"])
    assert before + timedelta(days=2) <= expires_at <= after + timedelta(days=2)


def test_create_session_uses_root_directory_policy_overlay(tmp_path: Path) -> None:
    config_path = write_config(tmp_path, enable_directory_scoped_config=True)
    runner = CliRunner()

    assert runner.invoke(app, ["init-state", "--config", str(config_path)]).exit_code == 0
    set_result = runner.invoke(
        app,
        [
            "directories",
            "config-set",
            "--config",
            str(config_path),
            "--domain",
            "policy",
            "root",
            '{"default_session_ttl_seconds": 120, "email_send_enabled": true}',
        ],
    )
    assert set_result.exit_code == 0

    before = datetime.now(UTC)
    result = runner.invoke(app, ["create-session", "--config", str(config_path), "session-1"])
    after = datetime.now(UTC)

    assert result.exit_code == 0
    sessions = json.loads((tmp_path / "runtime" / "sessions.json").read_text(encoding="utf-8"))
    manifest = sessions["session-1"]
    expires_at = datetime.fromisoformat(manifest["expires_at"])
    assert before + timedelta(seconds=120) <= expires_at <= after + timedelta(seconds=120)
    assert manifest["email_send_enabled"] is True
    assert "email_send_approved" in manifest["allowed_tools"]


def test_create_session_uses_selected_directory_policy_overlay(tmp_path: Path) -> None:
    config_path = write_config(tmp_path, enable_directory_scoped_config=True)
    runner = CliRunner()

    assert runner.invoke(app, ["init-state", "--config", str(config_path)]).exit_code == 0
    assert (
        runner.invoke(
            app,
            ["directories", "create", "--config", str(config_path), "--parent", "root", "team"],
        ).exit_code
        == 0
    )
    assert (
        runner.invoke(
            app,
            [
                "directories",
                "config-set",
                "--config",
                str(config_path),
                "--domain",
                "policy",
                "root/team",
                '{"default_session_ttl_seconds": 180, "email_send_enabled": true}',
            ],
        ).exit_code
        == 0
    )

    before = datetime.now(UTC)
    result = runner.invoke(
        app,
        [
            "create-session",
            "--config",
            str(config_path),
            "--directory",
            "root/team",
            "session-team",
        ],
    )
    after = datetime.now(UTC)

    assert result.exit_code == 0
    sessions = json.loads((tmp_path / "runtime" / "sessions.json").read_text(encoding="utf-8"))
    manifest = sessions["session-team"]
    expires_at = datetime.fromisoformat(manifest["expires_at"])
    assert manifest["directory_path"] == "root/team"
    assert before + timedelta(seconds=180) <= expires_at <= after + timedelta(seconds=180)
    assert manifest["email_send_enabled"] is True
    assert "email_send_approved" in manifest["allowed_tools"]


def test_create_issue_vm_creates_manifest_before_invoking_setup_script(tmp_path: Path, monkeypatch) -> None:
    config_path = write_config(tmp_path)
    runner = CliRunner()
    captured_cmd: list[str] = []

    init_result = runner.invoke(app, ["init-state", "--config", str(config_path)])
    assert init_result.exit_code == 0

    def fake_run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
        nonlocal captured_cmd
        captured_cmd = cmd
        sessions = json.loads((tmp_path / "runtime" / "sessions.json").read_text(encoding="utf-8"))
        assert "issue-1234-session" in sessions
        assert sessions["issue-1234-session"]["vm_id"] == "vm-1234"
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr("agent_sandbox.admin_service.subprocess.run", fake_run)

    result = runner.invoke(
        app,
        [
            "create-issue-vm",
            "--config",
            str(config_path),
            "--session-id",
            "issue-1234-session",
            "--model",
            "gpt-5.2-codex",
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
        "agent_sandbox.admin_service.subprocess.run",
        lambda cmd, **kwargs: subprocess.CompletedProcess(cmd, 0),
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
            "--model",
            "gpt-5.2-codex",
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
        "add_message_reaction",
        "has_pending_discord_messages",
        "list_unread_discord_messages",
        "list_all_discord_messages",
        "email_list_unread",
        "email_get_message",
        "email_create_draft",
    }
    assert manifest["metadata"] == {"issue_number": "14084"}


def test_create_issue_vm_builds_setup_issue_vm_command_correctly(tmp_path: Path, monkeypatch) -> None:
    config_path = write_config(tmp_path)
    runner = CliRunner()
    captured_cmd: list[str] = []

    init_result = runner.invoke(app, ["init-state", "--config", str(config_path)])
    assert init_result.exit_code == 0

    def fake_run(
        cmd: list[str],
        **kwargs,
    ) -> subprocess.CompletedProcess[str]:
        nonlocal captured_cmd
        captured_cmd = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr("agent_sandbox.admin_service.subprocess.run", fake_run)

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
                "--python",
                "--dotnet",
                "--nuget",
                "/tmp/project.slnx",
                "--mssql",
                "--mssql-init-root",
                "/tmp/mssql-init",
                "--playwright",
                "--repo-source",
                "/src/repo",
                "--model",
                "gpt-5.2-codex",
                "14084",
            ],
        )

    assert result.exit_code == 0
    assert captured_cmd[0].endswith("/scripts/setup_issue_vm.sh")
    assert captured_cmd[1:7] == [
        "--dry-run",
        "--python",
        "--dotnet",
        "--nuget",
        "--mssql",
        "--mssql-init-root",
    ]
    assert captured_cmd[7] == "/tmp/mssql-init"
    assert "--playwright" in captured_cmd
    assert captured_cmd[-5:] == [
        "14084",
        "/tmp/project.slnx",
        "/src/repo",
        "session-14084",
        "agent",
    ]


def test_create_issue_vm_uses_root_vm_defaults_overlay_for_guest_env(tmp_path: Path, monkeypatch) -> None:
    config_path = write_config(tmp_path, enable_directory_scoped_config=True)
    runner = CliRunner()
    captured_env: dict[str, str] = {}

    assert runner.invoke(app, ["init-state", "--config", str(config_path)]).exit_code == 0
    assert (
        runner.invoke(
            app,
            [
                "directories",
                "config-set",
                "--config",
                str(config_path),
                "--domain",
                "vm",
                "root",
                '{"gateway_url":"http://10.10.10.1:8000/mcp","openai_relay_port":12000,"nuget_relay_port":13000}',
            ],
        ).exit_code
        == 0
    )

    def fake_run(
        cmd: list[str],
        **kwargs,
    ) -> subprocess.CompletedProcess[str]:
        captured_env.update(kwargs.get("env") or {})
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr("agent_sandbox.admin_service.subprocess.run", fake_run)

    result = runner.invoke(
        app,
        [
            "create-issue-vm",
            "--config",
            str(config_path),
            "--session-id",
            "issue-1-session",
            "--model",
            "gpt-5.2-codex",
            "1",
        ],
    )

    assert result.exit_code == 0
    assert captured_env["GATEWAY_URL"] == "http://10.10.10.1:8000/mcp"
    assert captured_env["LLM_BASE_URL"] == "http://10.10.10.1:12000/v1"
    assert captured_env["NUGET_RELAY_BASE_URL"] == "http://10.10.10.1:13000"
    assert captured_env["AGENT_SANDBOX_AZURE_DEVOPS_PR_PAT_ENV"] == "AZURE_DEVOPS_PR_PAT"


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
    assert "MSSQL_SA_PASSWORD must be set when --mssql is used." in result.output


def test_create_issue_vm_rejects_mssql_init_root_without_mssql(tmp_path: Path, monkeypatch) -> None:
    config_path = write_config(tmp_path)
    runner = CliRunner()

    init_result = runner.invoke(app, ["init-state", "--config", str(config_path)])
    assert init_result.exit_code == 0

    monkeypatch.setattr(
        "agent_sandbox.admin_service.subprocess.run",
        lambda cmd, **kwargs: subprocess.CompletedProcess(cmd, 0),
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
        "agent_sandbox.admin_service.subprocess.run",
        lambda cmd, **kwargs: subprocess.CompletedProcess(cmd, 0),
    )

    result = runner.invoke(
        app,
        [
            "create-issue-vm",
                "--config",
                str(config_path),
                "--session-id",
                "issue-200-minimal",
                "--model",
                "gpt-5.2-codex",
                "200",
            ],
        )

    assert result.exit_code == 0
    sessions = json.loads((tmp_path / "runtime" / "sessions.json").read_text(encoding="utf-8"))
    manifest = sessions["issue-200-minimal"]
    assert manifest["allowed_tools"] == ["approval_submit", "approval_check"]
    assert manifest["email_mailbox_allowlist"] == []
    assert manifest["email_folder_allowlist"] == []
    assert manifest["metadata"] == {"issue_number": "200"}


def test_create_vm_creates_manifest_before_invoking_launch_flow(tmp_path: Path, monkeypatch) -> None:
    config_path = write_config(tmp_path)
    runner = CliRunner()
    captured_kwargs: dict[str, object] = {}

    assert runner.invoke(app, ["init-state", "--config", str(config_path)]).exit_code == 0

    class FakeAdminService:
        def __init__(self, config) -> None:
            captured_kwargs["config"] = config

        def create_vm(self, **kwargs):
            captured_kwargs["kwargs"] = kwargs
            return VmActionResult(success=True, vm_name=kwargs["vm_name"], action="create_vm", message="ok")

    monkeypatch.setattr("agent_sandbox.cli.AdminService", FakeAdminService)

    result = runner.invoke(
        app,
        [
            "create-vm",
            "--config",
            str(config_path),
            "--directory-path",
            "root/team",
            "--session-id",
            "lab-session",
            "--repo-source",
            "/src/repo",
            "--python",
            "--dotnet",
            "--nuget",
            "--playwright",
            "--codex-skill",
            "playwright",
            "--codex-skill",
            "playwright-interactive",
            "lab-vm",
        ],
    )

    assert result.exit_code == 0
    assert captured_kwargs["kwargs"] == {
        "vm_name": "lab-vm",
        "session_id": "lab-session",
        "model": None,
        "repo_source": "/src/repo",
        "vm_user": "agent",
        "ttl_hours": None,
        "discord_channels": [],
        "email_mailboxes": [],
        "email_folders": [],
        "python": True,
        "dotnet": True,
        "nuget": True,
        "mssql": False,
        "mssql_init_root": None,
        "playwright": True,
        "codex_skills": ["playwright", "playwright-interactive"],
        "directory_path": "root/team",
    }


def test_create_vm_rejects_mssql_init_root_without_mssql(tmp_path: Path) -> None:
    config_path = write_config(tmp_path)
    runner = CliRunner()

    assert runner.invoke(app, ["init-state", "--config", str(config_path)]).exit_code == 0

    result = runner.invoke(
        app,
        ["create-vm", "--config", str(config_path), "--mssql-init-root", "/tmp/init", "lab-vm"],
    )

    assert result.exit_code != 0
    assert "--mssql-init-root requires --mssql" in result.output


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
    assert "get_pull_request" in manifest["allowed_tools"]
    assert "list_unread_feedback" in manifest["allowed_tools"]
    assert "list_all_feedback" in manifest["allowed_tools"]
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


def test_add_tool_permission_by_session_id_updates_manifest(tmp_path: Path) -> None:
    config_path = write_config(tmp_path)
    runner = CliRunner()

    init_result = runner.invoke(app, ["init-state", "--config", str(config_path)])
    assert init_result.exit_code == 0

    create_result = runner.invoke(app, ["create-session", "--config", str(config_path), "session-1"])
    assert create_result.exit_code == 0

    sessions_path = tmp_path / "runtime" / "sessions.json"
    sessions = json.loads(sessions_path.read_text(encoding="utf-8"))
    sessions["session-1"]["allowed_tools"] = ["approval_submit"]
    sessions_path.write_text(json.dumps(sessions), encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "add-tool-permission",
            "--config",
            str(config_path),
            "session-1",
            "prompt_agent",
        ],
    )

    assert result.exit_code == 0
    manifest = json.loads(sessions_path.read_text(encoding="utf-8"))["session-1"]
    assert manifest["allowed_tools"] == ["approval_submit", "prompt_agent"]
    assert "Added tool permission prompt_agent to session session-1" in result.stdout


def test_add_tool_permission_by_vm_name_uses_matching_session(tmp_path: Path) -> None:
    config_path = write_config(tmp_path)
    runner = CliRunner()

    init_result = runner.invoke(app, ["init-state", "--config", str(config_path)])
    assert init_result.exit_code == 0

    sessions_path = tmp_path / "runtime" / "sessions.json"
    sessions_path.write_text(
        json.dumps(
            {
                "issue-14084-session": {
                    "session_id": "issue-14084-session",
                    "vm_id": "vm-14084",
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
                }
            }
        ),
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "add-tool-permission",
            "--config",
            str(config_path),
            "vm-14084",
            "email_send_approved",
        ],
    )

    assert result.exit_code == 0
    manifest = json.loads(sessions_path.read_text(encoding="utf-8"))["issue-14084-session"]
    assert manifest["allowed_tools"] == ["approval_submit", "email_send_approved"]
    assert "Added tool permission email_send_approved to session issue-14084-session" in result.stdout


def test_add_tool_permission_by_issue_number_uses_latest_matching_session(tmp_path: Path) -> None:
    config_path = write_config(tmp_path)
    runner = CliRunner()

    init_result = runner.invoke(app, ["init-state", "--config", str(config_path)])
    assert init_result.exit_code == 0

    earlier_created_at = datetime(2026, 3, 25, 7, 0, tzinfo=UTC)
    later_created_at = datetime(2026, 3, 25, 9, 0, tzinfo=UTC)
    sessions_path = tmp_path / "runtime" / "sessions.json"
    sessions_path.write_text(
        json.dumps(
            {
                "issue-13301-older": {
                    "session_id": "issue-13301-older",
                    "vm_id": "vm-13301",
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
                    "created_at": earlier_created_at.isoformat(),
                    "metadata": {},
                },
                "issue-13301-newer": {
                    "session_id": "issue-13301-newer",
                    "vm_id": "vm-13301",
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
                    "created_at": later_created_at.isoformat(),
                    "metadata": {},
                },
            }
        ),
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "add-tool-permission",
            "--config",
            str(config_path),
            "13301",
            "email_send_approved",
        ],
    )

    assert result.exit_code == 0
    sessions = json.loads(sessions_path.read_text(encoding="utf-8"))
    assert sessions["issue-13301-older"]["allowed_tools"] == ["approval_submit"]
    assert sessions["issue-13301-newer"]["allowed_tools"] == ["approval_submit", "email_send_approved"]
    assert "Added tool permission email_send_approved to session issue-13301-newer" in result.stdout


def test_add_tool_permission_is_idempotent_when_tool_already_present(tmp_path: Path) -> None:
    config_path = write_config(tmp_path)
    runner = CliRunner()

    init_result = runner.invoke(app, ["init-state", "--config", str(config_path)])
    assert init_result.exit_code == 0

    sessions_path = tmp_path / "runtime" / "sessions.json"
    sessions_path.write_text(
        json.dumps(
            {
                "session-1": {
                    "session_id": "session-1",
                    "vm_id": "vm-100",
                    "allowed_tools": ["approval_submit", "email_send_approved"],
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
                }
            }
        ),
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "add-tool-permission",
            "--config",
            str(config_path),
            "session-1",
            "email_send_approved",
        ],
    )

    assert result.exit_code == 0
    manifest = json.loads(sessions_path.read_text(encoding="utf-8"))["session-1"]
    assert manifest["allowed_tools"] == ["approval_submit", "email_send_approved"]
    assert "Tool permission email_send_approved already present on session session-1" in result.stdout


def test_revoke_tool_permission_by_session_id_updates_manifest(tmp_path: Path) -> None:
    config_path = write_config(tmp_path)
    runner = CliRunner()

    init_result = runner.invoke(app, ["init-state", "--config", str(config_path)])
    assert init_result.exit_code == 0

    sessions_path = tmp_path / "runtime" / "sessions.json"
    sessions_path.write_text(
        json.dumps(
            {
                "session-1": {
                    "session_id": "session-1",
                    "vm_id": "vm-100",
                    "allowed_tools": ["approval_submit", "respond_to_agent"],
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
                }
            }
        ),
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "revoke-tool-permission",
            "--config",
            str(config_path),
            "session-1",
            "respond_to_agent",
        ],
    )

    assert result.exit_code == 0
    manifest = json.loads(sessions_path.read_text(encoding="utf-8"))["session-1"]
    assert manifest["allowed_tools"] == ["approval_submit"]
    assert "Revoked tool permission respond_to_agent from session session-1" in result.stdout


def test_revoke_tool_permission_by_vm_name_uses_matching_session(tmp_path: Path) -> None:
    config_path = write_config(tmp_path)
    runner = CliRunner()

    init_result = runner.invoke(app, ["init-state", "--config", str(config_path)])
    assert init_result.exit_code == 0

    sessions_path = tmp_path / "runtime" / "sessions.json"
    sessions_path.write_text(
        json.dumps(
            {
                "issue-14084-session": {
                    "session_id": "issue-14084-session",
                    "vm_id": "vm-14084",
                    "allowed_tools": ["approval_submit", "email_send_approved"],
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
                }
            }
        ),
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "revoke-tool-permission",
            "--config",
            str(config_path),
            "vm-14084",
            "email_send_approved",
        ],
    )

    assert result.exit_code == 0
    manifest = json.loads(sessions_path.read_text(encoding="utf-8"))["issue-14084-session"]
    assert manifest["allowed_tools"] == ["approval_submit"]
    assert "Revoked tool permission email_send_approved from session issue-14084-session" in result.stdout


def test_revoke_tool_permission_by_issue_number_uses_latest_matching_session(tmp_path: Path) -> None:
    config_path = write_config(tmp_path)
    runner = CliRunner()

    init_result = runner.invoke(app, ["init-state", "--config", str(config_path)])
    assert init_result.exit_code == 0

    earlier_created_at = datetime(2026, 3, 25, 7, 0, tzinfo=UTC)
    later_created_at = datetime(2026, 3, 25, 9, 0, tzinfo=UTC)
    sessions_path = tmp_path / "runtime" / "sessions.json"
    sessions_path.write_text(
        json.dumps(
            {
                "issue-13301-older": {
                    "session_id": "issue-13301-older",
                    "vm_id": "vm-13301",
                    "allowed_tools": ["approval_submit", "email_send_approved"],
                    "discord_channel_allowlist": [],
                    "email_folder_allowlist": [],
                    "email_mailbox_allowlist": [],
                    "email_folder_allowlist_by_mailbox": {},
                    "email_draft_recipient_allowlist": [],
                    "email_draft_domain_allowlist": [],
                    "email_send_enabled": False,
                    "quotas": {},
                    "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
                    "created_at": earlier_created_at.isoformat(),
                    "metadata": {},
                },
                "issue-13301-newer": {
                    "session_id": "issue-13301-newer",
                    "vm_id": "vm-13301",
                    "allowed_tools": ["approval_submit", "email_send_approved"],
                    "discord_channel_allowlist": [],
                    "email_folder_allowlist": [],
                    "email_mailbox_allowlist": [],
                    "email_folder_allowlist_by_mailbox": {},
                    "email_draft_recipient_allowlist": [],
                    "email_draft_domain_allowlist": [],
                    "email_send_enabled": False,
                    "quotas": {},
                    "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
                    "created_at": later_created_at.isoformat(),
                    "metadata": {},
                },
            }
        ),
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "revoke-tool-permission",
            "--config",
            str(config_path),
            "13301",
            "email_send_approved",
        ],
    )

    assert result.exit_code == 0
    sessions = json.loads(sessions_path.read_text(encoding="utf-8"))
    assert sessions["issue-13301-older"]["allowed_tools"] == ["approval_submit", "email_send_approved"]
    assert sessions["issue-13301-newer"]["allowed_tools"] == ["approval_submit"]
    assert "Revoked tool permission email_send_approved from session issue-13301-newer" in result.stdout


def test_revoke_tool_permission_is_idempotent_when_tool_missing(tmp_path: Path) -> None:
    config_path = write_config(tmp_path)
    runner = CliRunner()

    init_result = runner.invoke(app, ["init-state", "--config", str(config_path)])
    assert init_result.exit_code == 0

    sessions_path = tmp_path / "runtime" / "sessions.json"
    sessions_path.write_text(
        json.dumps(
            {
                "session-1": {
                    "session_id": "session-1",
                    "vm_id": "vm-100",
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
                }
            }
        ),
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "revoke-tool-permission",
            "--config",
            str(config_path),
            "session-1",
            "email_send_approved",
        ],
    )

    assert result.exit_code == 0
    manifest = json.loads(sessions_path.read_text(encoding="utf-8"))["session-1"]
    assert manifest["allowed_tools"] == ["approval_submit"]
    assert "Tool permission email_send_approved not present on session session-1" in result.stdout


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


def test_renew_session_by_session_id_sets_expiration_from_now(tmp_path: Path) -> None:
    config_path = write_config(tmp_path)
    runner = CliRunner()

    init_result = runner.invoke(app, ["init-state", "--config", str(config_path)])
    assert init_result.exit_code == 0

    expires_at = datetime.now(UTC) + timedelta(days=5)
    sessions_path = tmp_path / "runtime" / "sessions.json"
    sessions_path.write_text(
        json.dumps(
            {
                "session-1": {
                    "session_id": "session-1",
                    "vm_id": "vm-100",
                    "allowed_tools": ["approval_submit"],
                    "discord_channel_allowlist": [],
                    "email_folder_allowlist": [],
                    "email_mailbox_allowlist": [],
                    "email_folder_allowlist_by_mailbox": {},
                    "email_draft_recipient_allowlist": [],
                    "email_draft_domain_allowlist": [],
                    "email_send_enabled": False,
                    "quotas": {},
                    "expires_at": expires_at.isoformat(),
                    "created_at": datetime.now(UTC).isoformat(),
                    "metadata": {},
                },
            }
        ),
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        ["renew-session", "--config", str(config_path), "--extend-by", "6h", "session-1"],
    )

    before = datetime.now(UTC)
    result = runner.invoke(
        app,
        ["renew-session", "--config", str(config_path), "--extend-by", "6h", "session-1"],
    )
    after = datetime.now(UTC)

    assert result.exit_code == 0
    sessions = json.loads(sessions_path.read_text(encoding="utf-8"))
    renewed_expires_at = datetime.fromisoformat(sessions["session-1"]["expires_at"])
    assert before + timedelta(hours=6) <= renewed_expires_at <= after + timedelta(hours=6)
    assert "Renewed session session-1" in result.stdout


def test_renew_session_by_issue_number_uses_latest_matching_session_and_defaults_to_one_day(tmp_path: Path) -> None:
    config_path = write_config(tmp_path)
    runner = CliRunner()

    init_result = runner.invoke(app, ["init-state", "--config", str(config_path)])
    assert init_result.exit_code == 0

    earlier_created_at = datetime(2026, 3, 25, 7, 0, tzinfo=UTC)
    later_created_at = datetime(2026, 3, 25, 9, 0, tzinfo=UTC)
    earlier_expires_at = datetime.now(UTC) - timedelta(days=2)
    later_expires_at = datetime.now(UTC) - timedelta(hours=2)
    sessions_path = tmp_path / "runtime" / "sessions.json"
    sessions_path.write_text(
        json.dumps(
            {
                "issue-13301-older": {
                    "session_id": "issue-13301-older",
                    "vm_id": "vm-13301",
                    "allowed_tools": ["approval_submit"],
                    "discord_channel_allowlist": [],
                    "email_folder_allowlist": [],
                    "email_mailbox_allowlist": [],
                    "email_folder_allowlist_by_mailbox": {},
                    "email_draft_recipient_allowlist": [],
                    "email_draft_domain_allowlist": [],
                    "email_send_enabled": False,
                    "quotas": {},
                    "expires_at": earlier_expires_at.isoformat(),
                    "created_at": earlier_created_at.isoformat(),
                    "metadata": {},
                },
                "issue-13301-newer": {
                    "session_id": "issue-13301-newer",
                    "vm_id": "vm-13301",
                    "allowed_tools": ["approval_submit"],
                    "discord_channel_allowlist": [],
                    "email_folder_allowlist": [],
                    "email_mailbox_allowlist": [],
                    "email_folder_allowlist_by_mailbox": {},
                    "email_draft_recipient_allowlist": [],
                    "email_draft_domain_allowlist": [],
                    "email_send_enabled": False,
                    "quotas": {},
                    "expires_at": later_expires_at.isoformat(),
                    "created_at": later_created_at.isoformat(),
                    "metadata": {},
                },
            }
        ),
        encoding="utf-8",
    )

    before = datetime.now(UTC)
    result = runner.invoke(app, ["renew-session", "--config", str(config_path), "13301"])
    after = datetime.now(UTC)

    assert result.exit_code == 0
    sessions = json.loads(sessions_path.read_text(encoding="utf-8"))
    assert datetime.fromisoformat(sessions["issue-13301-older"]["expires_at"]) == earlier_expires_at
    renewed_expires_at = datetime.fromisoformat(sessions["issue-13301-newer"]["expires_at"])
    assert before + timedelta(days=1) <= renewed_expires_at <= after + timedelta(days=1)
    assert "Renewed session issue-13301-newer" in result.stdout


def test_renew_session_by_vm_name_works(tmp_path: Path) -> None:
    config_path = write_config(tmp_path)
    runner = CliRunner()

    init_result = runner.invoke(app, ["init-state", "--config", str(config_path)])
    assert init_result.exit_code == 0

    expires_at = datetime.now(UTC) - timedelta(hours=1)
    sessions_path = tmp_path / "runtime" / "sessions.json"
    sessions_path.write_text(
        json.dumps(
            {
                "issue-14084-session": {
                    "session_id": "issue-14084-session",
                    "vm_id": "vm-14084",
                    "allowed_tools": ["approval_submit"],
                    "discord_channel_allowlist": [],
                    "email_folder_allowlist": [],
                    "email_mailbox_allowlist": [],
                    "email_folder_allowlist_by_mailbox": {},
                    "email_draft_recipient_allowlist": [],
                    "email_draft_domain_allowlist": [],
                    "email_send_enabled": False,
                    "quotas": {},
                    "expires_at": expires_at.isoformat(),
                    "created_at": datetime.now(UTC).isoformat(),
                    "metadata": {},
                },
            }
        ),
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        ["renew-session", "--config", str(config_path), "--extend-by", "12h", "vm-14084"],
    )

    assert result.exit_code == 0
    renewed_expires_at = datetime.fromisoformat(
        json.loads(sessions_path.read_text(encoding="utf-8"))["issue-14084-session"]["expires_at"]
    )
    assert renewed_expires_at > datetime.now(UTC) + timedelta(hours=11, minutes=59)
    assert "Renewed session issue-14084-session" in result.stdout


def test_list_sessions_shows_only_active_by_default(tmp_path: Path) -> None:
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
                    "vm_id": "vm-100",
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
                    "vm_id": "vm-200",
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

    result = runner.invoke(app, ["list-sessions", "--config", str(config_path)])

    assert result.exit_code == 0
    assert "active-session" in result.stdout
    assert "status=active" in result.stdout
    assert "expired-session" not in result.stdout


def test_list_sessions_supports_all_and_expired_only(tmp_path: Path) -> None:
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
                    "vm_id": "vm-100",
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
                    "vm_id": "vm-200",
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

    all_result = runner.invoke(app, ["list-sessions", "--config", str(config_path), "--all"])
    expired_result = runner.invoke(app, ["list-sessions", "--config", str(config_path), "--expired-only"])

    assert all_result.exit_code == 0
    assert "active-session" in all_result.stdout
    assert "expired-session" in all_result.stdout
    assert expired_result.exit_code == 0
    assert "active-session" not in expired_result.stdout
    assert "expired-session" in expired_result.stdout
    assert "status=expired" in expired_result.stdout


def test_revoke_session_expires_it_immediately(tmp_path: Path) -> None:
    config_path = write_config(tmp_path)
    runner = CliRunner()

    init_result = runner.invoke(app, ["init-state", "--config", str(config_path)])
    assert init_result.exit_code == 0

    create_result = runner.invoke(app, ["create-session", "--config", str(config_path), "session-1"])
    assert create_result.exit_code == 0

    before = datetime.now(UTC)
    result = runner.invoke(app, ["revoke-session", "--config", str(config_path), "session-1"])
    after = datetime.now(UTC)

    assert result.exit_code == 0
    sessions = json.loads((tmp_path / "runtime" / "sessions.json").read_text(encoding="utf-8"))
    expires_at = datetime.fromisoformat(sessions["session-1"]["expires_at"])
    assert before <= expires_at <= after
    assert "Revoked session session-1" in result.stdout


def test_delete_session_removes_targeted_session(tmp_path: Path) -> None:
    config_path = write_config(tmp_path)
    runner = CliRunner()

    init_result = runner.invoke(app, ["init-state", "--config", str(config_path)])
    assert init_result.exit_code == 0

    create_a = runner.invoke(app, ["create-session", "--config", str(config_path), "session-a"])
    create_b = runner.invoke(app, ["create-session", "--config", str(config_path), "session-b"])
    assert create_a.exit_code == 0
    assert create_b.exit_code == 0

    result = runner.invoke(app, ["delete-session", "--config", str(config_path), "session-a"])

    assert result.exit_code == 0
    sessions = json.loads((tmp_path / "runtime" / "sessions.json").read_text(encoding="utf-8"))
    assert set(sessions) == {"session-b"}
    assert "Deleted session session-a" in result.stdout


def test_delete_expired_sessions_removes_all_expired(tmp_path: Path) -> None:
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
                    "vm_id": "vm-100",
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
                "expired-a": {
                    "session_id": "expired-a",
                    "vm_id": "vm-200",
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
                "expired-b": {
                    "session_id": "expired-b",
                    "vm_id": "vm-300",
                    "allowed_tools": ["approval_submit"],
                    "discord_channel_allowlist": [],
                    "email_folder_allowlist": [],
                    "email_mailbox_allowlist": [],
                    "email_folder_allowlist_by_mailbox": {},
                    "email_draft_recipient_allowlist": [],
                    "email_draft_domain_allowlist": [],
                    "email_send_enabled": False,
                    "quotas": {},
                    "expires_at": (datetime.now(UTC) - timedelta(days=1)).isoformat(),
                    "created_at": datetime.now(UTC).isoformat(),
                    "metadata": {},
                },
            }
        ),
        encoding="utf-8",
    )

    result = runner.invoke(app, ["delete-expired-sessions", "--config", str(config_path)])

    assert result.exit_code == 0
    sessions = json.loads(sessions_path.read_text(encoding="utf-8"))
    assert set(sessions) == {"active-session"}
    assert "Deleted 2 expired session(s)" in result.stdout


def test_renew_session_rejects_invalid_duration(tmp_path: Path) -> None:
    config_path = write_config(tmp_path)
    runner = CliRunner()

    init_result = runner.invoke(app, ["init-state", "--config", str(config_path)])
    assert init_result.exit_code == 0

    create_result = runner.invoke(app, ["create-session", "--config", str(config_path), "session-1"])
    assert create_result.exit_code == 0

    result = runner.invoke(
        app,
        ["renew-session", "--config", str(config_path), "--extend-by", "90m", "session-1"],
    )

    assert result.exit_code != 0
    assert "Extension must use the format <number><h|d>" in result.output


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


def test_destroy_vm_delegates_to_admin_service(tmp_path: Path, monkeypatch) -> None:
    config_path = write_config(tmp_path)
    runner = CliRunner()
    captured: dict[str, object] = {}

    class FakeAdminService:
        def __init__(self, config) -> None:
            captured["config"] = config

        def destroy_vm(self, target: str):
            captured["target"] = target
            return VmActionResult(success=True, vm_name=target, action="destroy_vm", message="destroyed")

    monkeypatch.setattr("agent_sandbox.cli.AdminService", FakeAdminService)

    result = runner.invoke(app, ["destroy-vm", "--config", str(config_path), "lab-vm"])

    assert result.exit_code == 0
    assert captured["target"] == "lab-vm"
    assert "destroyed" in result.stdout


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


def test_list_vms_omit_state_invokes_script_with_omit_state_flag(tmp_path: Path, monkeypatch) -> None:
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

    result = runner.invoke(app, ["list-vms", "--omit-state", "--vm-user", "root"])

    assert result.exit_code == 0
    assert captured_cmd[0].endswith("/scripts/list_running_vms.sh")
    assert captured_cmd[1:] == ["--omit-state", "root"]


def test_list_vms_json_uses_admin_service(tmp_path: Path, monkeypatch) -> None:
    config_path = write_config(tmp_path)
    runner = CliRunner()

    init_result = runner.invoke(app, ["init-state", "--config", str(config_path)])
    assert init_result.exit_code == 0

    class FakeAdminService:
        def __init__(self, config):
            self.config = config

        def list_vms(self, *, include_inactive: bool, omit_state: bool, vm_user: str):
            assert include_inactive is True
            assert omit_state is False
            assert vm_user == "agent"
            return [
                VmSummary(
                    vm_name="vm-12305",
                    issue_number="12305",
                    session_ids=["issue-12305-a"],
                    libvirt_state="running",
                    ip_address="192.168.122.15",
                    agent_state="idle",
                    agent_repo_names=[],
                )
            ]

    monkeypatch.setattr("agent_sandbox.cli.AdminService", FakeAdminService)

    result = runner.invoke(app, ["list-vms", "--config", str(config_path), "--all", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload[0]["vm_name"] == "vm-12305"
    assert payload[0]["agent_state"] == "idle"


def test_start_vm_reports_failure_exit_code(tmp_path: Path, monkeypatch) -> None:
    config_path = write_config(tmp_path)
    runner = CliRunner()

    init_result = runner.invoke(app, ["init-state", "--config", str(config_path)])
    assert init_result.exit_code == 0

    class FakeAdminService:
        def __init__(self, config):
            self.config = config

        def start_vm(self, target: str):
            assert target == "vm-12305"
            return VmActionResult(
                success=False,
                vm_name="vm-12305",
                action="start",
                message="VM not found: vm-12305",
            )

    monkeypatch.setattr("agent_sandbox.cli.AdminService", FakeAdminService)

    result = runner.invoke(app, ["start-vm", "--config", str(config_path), "vm-12305"])

    assert result.exit_code == 1
    assert "VM not found: vm-12305" in result.stdout


def test_create_local_issue_uses_admin_service(tmp_path: Path, monkeypatch) -> None:
    config_path = write_config(tmp_path)
    runner = CliRunner()

    init_result = runner.invoke(app, ["init-state", "--config", str(config_path)])
    assert init_result.exit_code == 0

    class FakeAdminService:
        def __init__(self, config):
            self.config = config

        def create_local_issue(self, *, title: str, body: str, repo_source: str | None, yaml_path: str | None):
            assert title == "Build GUI"
            assert body == "Need a desktop console"
            assert repo_source == "/src/repo"
            assert yaml_path is None
            return LocalIssueRecord(
                issue_id="local-77",
                issue_number="77",
                title=title,
                body=body,
                yaml_path="/tmp/runtime/issues/77.yaml",
                repo_source=repo_source,
            )

    monkeypatch.setattr("agent_sandbox.cli.AdminService", FakeAdminService)

    result = runner.invoke(
        app,
        [
            "create-local-issue",
            "--config",
            str(config_path),
            "--title",
            "Build GUI",
            "--body",
            "Need a desktop console",
            "--repo-source",
            "/src/repo",
        ],
    )

    assert result.exit_code == 0
    assert "Created local issue 77" in result.stdout


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


def test_disable_agent_invokes_state_script(tmp_path: Path, monkeypatch) -> None:
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

    result = runner.invoke(app, ["disable-agent", "12305", "--vm-user", "root"])

    assert result.exit_code == 0
    assert captured_cmd[0].endswith("/scripts/set_issue_agent_state.sh")
    assert captured_cmd[1:4] == ["disable", "12305", "root"]
    assert captured_cmd[4] == "--host-state-path"
    assert captured_cmd[5].endswith("/runtime/host-agent-control-state.json")


def test_enable_agent_invokes_state_script(tmp_path: Path, monkeypatch) -> None:
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

    result = runner.invoke(app, ["enable-agent", "vm-12305"])

    assert result.exit_code == 0
    assert captured_cmd[0].endswith("/scripts/set_issue_agent_state.sh")
    assert captured_cmd[1:4] == ["enable", "12305", "agent"]
    assert captured_cmd[4] == "--host-state-path"
    assert captured_cmd[5].endswith("/runtime/host-agent-control-state.json")


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
    assert (
        runner.invoke(
            app,
            ["create-session", "--config", str(config_path), "--vm-id", "vm-12305", "--model", "gpt-5.2-codex", "issue-12305-session"],
        ).exit_code
        == 0
    )

    def fake_run(
        cmd: list[str],
        check: bool,
        capture_output: bool,
        text: bool,
        env: dict[str, str],
    ) -> subprocess.CompletedProcess[str]:
        nonlocal captured_cmd
        captured_cmd = cmd
        assert env["AGENT_MODEL"] == "gpt-5.2-codex"
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
    assert captured_cmd[1:4] == ["Continue from the failing restore", "12305", "agent"]
    assert captured_cmd[4] == "--host-control-state-path"
    assert captured_cmd[5].endswith("/runtime/host-agent-control-state.json")


def test_resume_agent_observe_invokes_script_with_tail_options(tmp_path: Path, monkeypatch) -> None:
    config_path = write_config(tmp_path)
    runner = CliRunner()
    captured_cmd: list[str] = []

    init_result = runner.invoke(app, ["init-state", "--config", str(config_path)])
    assert init_result.exit_code == 0
    assert (
        runner.invoke(
            app,
            ["create-session", "--config", str(config_path), "--vm-id", "vm-12305", "--model", "gpt-5.2-codex", "issue-12305-session"],
        ).exit_code
        == 0
    )

    def fake_run(cmd: list[str], check: bool, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
        nonlocal captured_cmd
        captured_cmd = cmd
        assert env["AGENT_MODEL"] == "gpt-5.2-codex"
        return subprocess.CompletedProcess(cmd, 0)

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
            "--observe",
            "--lines",
            "25",
            "--vm-user",
            "debugger",
        ],
    )

    assert result.exit_code == 0
    assert captured_cmd[0].endswith("/scripts/resume_issue_agent.sh")
    assert captured_cmd[1:4] == ["Continue from the failing restore", "12305", "debugger"]
    assert captured_cmd[4] == "--host-control-state-path"
    assert captured_cmd[5].endswith("/runtime/host-agent-control-state.json")
    assert captured_cmd[6:] == ["--observe", "25"]


def test_resume_agent_accepts_vm_name_without_vm_prefix(tmp_path: Path, monkeypatch) -> None:
    config_path = write_config(tmp_path)
    runner = CliRunner()
    captured_cmd: list[str] = []

    assert runner.invoke(app, ["init-state", "--config", str(config_path)]).exit_code == 0
    assert (
        runner.invoke(
            app,
            [
                "create-session",
                "--config",
                str(config_path),
                "--vm-id",
                "agent-sandbox-receptionist-vm",
                "--model",
                "gpt-5.4",
                "agent-sandbox-receptionist-vm-20260330T112127Z-1353036",
            ],
        ).exit_code
        == 0
    )

    def fake_run(
        cmd: list[str],
        check: bool,
        capture_output: bool,
        text: bool,
        env: dict[str, str],
    ) -> subprocess.CompletedProcess[str]:
        nonlocal captured_cmd
        captured_cmd = cmd
        assert env["AGENT_MODEL"] == "gpt-5.4"
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr("agent_sandbox.cli.subprocess.run", fake_run)

    result = runner.invoke(
        app,
        [
            "resume-agent",
            "agent-sandbox-receptionist-vm",
            "--config",
            str(config_path),
            "--message",
            "Continue from Discord",
        ],
    )

    assert result.exit_code == 0
    assert captured_cmd[0].endswith("/scripts/resume_issue_agent.sh")
    assert captured_cmd[1:] == [
        "--prompt",
        "Continue from Discord",
        "--vm-name",
        "agent-sandbox-receptionist-vm",
        "--vm-user",
        "agent",
        "--host-control-state-path",
        str(tmp_path / "runtime" / "host-agent-control-state.json"),
    ]


def test_set_session_model_updates_manifest(tmp_path: Path) -> None:
    config_path = write_config(tmp_path)
    runner = CliRunner()

    assert runner.invoke(app, ["init-state", "--config", str(config_path)]).exit_code == 0
    assert runner.invoke(app, ["create-session", "--config", str(config_path), "session-1"]).exit_code == 0

    result = runner.invoke(
        app,
        ["set-session-model", "--config", str(config_path), "--model", "gpt-5.3-codex", "session-1"],
    )

    assert result.exit_code == 0
    sessions = json.loads((tmp_path / "runtime" / "sessions.json").read_text(encoding="utf-8"))
    assert sessions["session-1"]["model"] == "gpt-5.3-codex"


def test_set_session_model_clear_removes_override(tmp_path: Path) -> None:
    config_path = write_config(tmp_path)
    runner = CliRunner()

    assert runner.invoke(app, ["init-state", "--config", str(config_path)]).exit_code == 0
    assert (
        runner.invoke(app, ["create-session", "--config", str(config_path), "--model", "gpt-5.3-codex", "session-1"]).exit_code
        == 0
    )

    result = runner.invoke(
        app,
        ["set-session-model", "--config", str(config_path), "--clear", "session-1"],
    )

    assert result.exit_code == 0
    sessions = json.loads((tmp_path / "runtime" / "sessions.json").read_text(encoding="utf-8"))
    assert sessions["session-1"]["model"] is None


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
    assert "http_target_relay: degraded" in result.stdout


def test_status_uses_root_vm_defaults_overlay_for_health_endpoints(tmp_path: Path, monkeypatch) -> None:
    config_path = write_config(tmp_path, enable_directory_scoped_config=True)
    runner = CliRunner()
    checked_urls: list[tuple[str, str]] = []

    assert runner.invoke(app, ["init-state", "--config", str(config_path)]).exit_code == 0
    assert (
        runner.invoke(
            app,
            [
                "directories",
                "config-set",
                "--config",
                str(config_path),
                "--domain",
                "vm",
                "root",
                '{"gateway_url":"http://10.0.0.5:9000/mcp","openai_relay_host":"127.0.0.2","openai_relay_port":12000,"http_target_relay_host":"127.0.0.3","http_target_relay_port":14000}',
            ],
        ).exit_code
        == 0
    )

    monkeypatch.setattr(
        "agent_sandbox.cli._get_systemd_unit_state",
        lambda unit: UnitStatus(unit=unit, state="active", available=True, detail=None),
    )

    def fake_check(name: str, url: str) -> EndpointStatus:
        checked_urls.append((name, url))
        return EndpointStatus(name=name, url=url, reachable=True, status_code=200, error=None)

    monkeypatch.setattr("agent_sandbox.cli._check_health_endpoint", fake_check)

    result = runner.invoke(app, ["status", "--config", str(config_path), "--json"])

    assert result.exit_code == 0
    assert ("gateway", "http://10.0.0.5:9000/mcp") in checked_urls
    assert ("openai_relay", "http://127.0.0.2:12000/healthz") in checked_urls
    assert ("http_target_relay", "http://127.0.0.3:14000/healthz") in checked_urls




def test_trigger_orchestrator_once_runs_single_cycle(tmp_path: Path, monkeypatch) -> None:
    config_path = write_config(tmp_path)
    runner = CliRunner()
    calls: list[str] = []

    class _FakeWorker:
        def run_once_with_lock(self):
            calls.append("run_once_with_lock")
            return SimpleNamespace(
                configured_triggers=2,
                events_discovered=5,
                events_published=4,
                next_sleep_seconds=17.25,
            )

        def run_once(self):
            raise AssertionError("run_once should not be called directly in --once mode")

        def run_forever(self) -> None:
            raise AssertionError("run_forever should not be called in --once mode")

    monkeypatch.setattr("agent_sandbox.cli.build_trigger_orchestrator_worker", lambda cfg, **kwargs: _FakeWorker())

    result = runner.invoke(
        app,
        ["trigger-orchestrator", "--config", str(config_path), "--once", "--log-level", "DEBUG"],
    )

    assert result.exit_code == 0
    assert calls == ["run_once_with_lock"]
    assert "triggers=2" in result.output
    assert "discovered=5" in result.output
    assert "published=4" in result.output

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

    monkeypatch.setattr("agent_sandbox.cli.build_host_heartbeat_worker", lambda cfg, **kwargs: _FakeWorker())

    result = runner.invoke(
        app,
        ["host-heartbeat", "--config", str(config_path), "--once", "--log-level", "DEBUG"],
    )

    assert result.exit_code == 0
    assert calls == ["run_once_with_lock"]
    assert "streams=3" in result.output
    assert "discovered=1" in result.output


def test_teams_ingest_once_runs_single_cycle(tmp_path: Path, monkeypatch) -> None:
    config_path = write_config(tmp_path)
    runner = CliRunner()
    calls: list[str] = []

    class _FakeWorker:
        def run_once_with_lock(self):
            calls.append("run_once_with_lock")
            return SimpleNamespace(directories_polled=4, sessions_seen=6, next_sleep_seconds=21.25)

        def run_forever(self) -> None:
            raise AssertionError("run_forever should not be called in --once mode")

    monkeypatch.setattr("agent_sandbox.cli._build_teams_ingest_worker", lambda cfg, **kwargs: _FakeWorker())

    result = runner.invoke(
        app,
        ["teams-ingest", "--config", str(config_path), "--once", "--log-level", "DEBUG"],
    )

    assert result.exit_code == 0
    assert calls == ["run_once_with_lock"]
    assert "directories=4" in result.output
    assert "sessions=6" in result.output


def test_wake_schedules_create_and_list(tmp_path: Path) -> None:
    config_path = write_config(tmp_path)
    runner = CliRunner()

    create_result = runner.invoke(
        app,
        [
            "wake-schedules",
            "create",
            "--config",
            str(config_path),
            "--session-id",
            "issue-11-session",
            "--prompt",
            "Resume from queue.",
            "--cron",
            "*/10 * * * *",
            "--mode",
            "new_context",
        ],
    )
    assert create_result.exit_code == 0
    created_payload = json.loads(create_result.stdout)
    assert created_payload["resume_mode"] == "new_context"

    list_result = runner.invoke(
        app,
        ["wake-schedules", "list", "--config", str(config_path), "--session-id", "issue-11-session"],
    )
    assert list_result.exit_code == 0
    listed_payload = json.loads(list_result.stdout)
    assert len(listed_payload) == 1
    assert listed_payload[0]["cron"] == "*/10 * * * *"
    assert listed_payload[0]["last_dispatch"] is None


def test_wake_schedules_create_rejects_invalid_cron(tmp_path: Path) -> None:
    config_path = write_config(tmp_path)
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "wake-schedules",
            "create",
            "--config",
            str(config_path),
            "--session-id",
            "issue-11-session",
            "--prompt",
            "Resume from queue.",
            "--cron",
            "* *",
        ],
    )
    assert result.exit_code != 0
    assert "Invalid cron expression" in result.output


def test_wake_schedules_edit_and_delete_round_trip(tmp_path: Path) -> None:
    config_path = write_config(tmp_path)
    runner = CliRunner()
    create_result = runner.invoke(
        app,
        [
            "wake-schedules",
            "create",
            "--config",
            str(config_path),
            "--session-id",
            "issue-11-session",
            "--prompt",
            "Resume from queue.",
            "--resume-at",
            "2026-04-01T10:30:00Z",
        ],
    )
    assert create_result.exit_code == 0
    request_id = json.loads(create_result.stdout)["request_id"]

    edit_result = runner.invoke(
        app,
        [
            "wake-schedules",
            "edit",
            request_id,
            "--config",
            str(config_path),
            "--cron",
            "*/15 * * * *",
            "--mode",
            "new_context",
            "--prompt",
            "Updated prompt",
        ],
    )
    assert edit_result.exit_code == 0
    edited_payload = json.loads(edit_result.stdout)
    assert edited_payload["cron"] == "*/15 * * * *"
    assert edited_payload["resume_mode"] == "new_context"

    delete_result = runner.invoke(
        app,
        ["wake-schedules", "delete", request_id, "--config", str(config_path)],
    )
    assert delete_result.exit_code == 0
    assert f"Deleted wake schedule {request_id}" in delete_result.stdout


def test_wake_schedules_create_enforces_mcp_mode_rules(tmp_path: Path) -> None:
    config_path = write_config(tmp_path)
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "wake-schedules",
            "create",
            "--config",
            str(config_path),
            "--session-id",
            "issue-11-session",
            "--prompt",
            "Resume from queue.",
            "--resume-at",
            "2026-04-01T10:30:00Z",
            "--source",
            "mcp_tool",
            "--mode",
            "new_context",
        ],
    )
    assert result.exit_code != 0
    assert "MCP scheduling only supports" in result.output
