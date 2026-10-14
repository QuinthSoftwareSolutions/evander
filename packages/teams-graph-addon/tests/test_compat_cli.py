import json


from datetime import datetime, timezone


from pathlib import Path


from types import SimpleNamespace


import pytest


from typer.testing import CliRunner


from evander.cli import (
    app,
)


from evander.messaging.teams_auth import InvalidClientConfigurationError


from config_helpers import write_runtime_config


UTC = timezone.utc


@pytest.fixture(autouse=True)
def _isolate_managed_git_authorized_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EVANDER_GIT_USER", "evander-cli-tests-no-such-user")


def write_config(tmp_path: Path, *, enable_directory_scoped_config: bool = False) -> Path:
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    (runtime_dir / "managed-git-repositories.json").write_text(
        json.dumps(
            {
                "schema": "evander.managed-git-repositories/v1",
                "schema_version": 1,
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-01T00:00:00Z",
                "repositories": {},
                "migrations": {"test-fixture": {"status": "applied"}},
            }
        ),
        encoding="utf-8",
    )
    return write_runtime_config(
        tmp_path,
        enable_directory_scoped_config=enable_directory_scoped_config,
    )


def patch_cli_admin_runtime(monkeypatch, **service_factories):
    def fake_build_admin_runtime(config):
        return SimpleNamespace(
            **{name: factory(config) for name, factory in service_factories.items()}
        )

    monkeypatch.setattr("evander.cli_runtime.build_admin_runtime", fake_build_admin_runtime)


def test_teams_auth_commands_use_admin_service_and_redact_tokens(
    tmp_path: Path, monkeypatch
) -> None:
    config_path = write_config(tmp_path)
    runner = CliRunner()
    calls: list[str] = []

    class FakeAdminService:
        def __init__(self, config) -> None:
            self.config = config

        def login(self, *, directory="root"):
            calls.append("login")
            return {
                "status": "device_code_pending",
                "message": "Go to https://microsoft.com/devicelogin and enter code ABCD-EFGH",
                "verification_uri": "https://microsoft.com/devicelogin",
                "user_code": "ABCD-EFGH",
                "access_token": "should-not-leak",
                "refresh_token": "should-not-leak-either",
            }

        def complete_login(self, login_session, *, directory="root"):
            calls.append("complete_login")
            return {
                "status": "authenticated",
                "backend": "delegated_graph_api",
                "account_identifier": "alice@example.com",
                "tenant_id": "contoso",
                "expires_at": "2026-05-01T12:00:00Z",
                "access_token": "should-not-leak",
            }

        def status(self, *, directory="root"):
            calls.append("status")
            return {
                "status": "authenticated",
                "backend": "delegated_graph_api",
                "account_identifier": "alice@example.com",
                "tenant_id": "contoso",
                "expires_at": "2026-05-01T12:00:00Z",
                "access_token": "should-not-leak",
            }

        def list(self):
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

        def logout(self, *, directory="root"):
            calls.append("logout")
            return None

        def list_channels(
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
                        "display_name": "Evander",
                        "channel_id": "19:abc@thread.skype",
                        "allowlist_entry": f"{team_id}/19:abc@thread.skype",
                    }
                ],
            }

    patch_cli_admin_runtime(monkeypatch, teams_admin=FakeAdminService)

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
            "Evander",
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
    assert "Evander" in channels_result.output
    assert "19:abc@thread.skype" in channels_result.output
    assert "team-123/19:abc@thread.skype" in channels_result.output

    assert logout_result.exit_code == 0
    assert "Completed successfully." in logout_result.output
    assert calls == ["login", "complete_login", "status", "list", "logout", "list_channels"]


def test_teams_auth_login_reports_auth_errors_without_traceback(
    tmp_path: Path, monkeypatch
) -> None:
    config_path = write_config(tmp_path)
    runner = CliRunner()

    class FakeAdminService:
        def __init__(self, config) -> None:
            self.config = config

        def login(self, *, directory="root"):
            return {
                "status": "device_code_pending",
                "message": "Open the browser and authenticate.",
                "verification_uri": "https://login.microsoft.com/device",
                "user_code": "ABCD-EFGH",
            }

        def complete_login(self, login_session, *, directory="root"):
            raise InvalidClientConfigurationError(
                "Microsoft Entra rejected the device-code flow because the configured app registration "
                "is not enabled as a public client."
            )

    patch_cli_admin_runtime(monkeypatch, teams_admin=FakeAdminService)

    result = runner.invoke(app, ["teams-auth", "login", "--config", str(config_path)])

    assert result.exit_code == 1
    assert "Teams auth login failed:" in result.output
    assert "not enabled as a public client" in result.output
    assert "Traceback" not in result.output


def test_teams_auth_login_renders_datetime_details_without_traceback(
    tmp_path: Path, monkeypatch
) -> None:
    config_path = write_config(tmp_path)
    runner = CliRunner()

    class FakeAdminService:
        def __init__(self, config) -> None:
            self.config = config

        def login(self, *, directory="root"):
            return {
                "status": "device_code_pending",
                "message": "Open the browser and authenticate.",
                "verification_uri": "https://login.microsoft.com/device",
                "user_code": "ABCD-EFGH",
            }

        def complete_login(self, login_session, *, directory="root"):
            return {
                "status": "authenticated",
                "backend": "delegated_graph_api",
                "account_identifier": "example-teams",
                "tenant_id": "contoso",
                "expires_at": datetime(2026, 4, 10, 7, 27, 3, tzinfo=UTC),
                "issued_at": datetime(2026, 4, 10, 6, 27, 3, tzinfo=UTC),
                "store_path": "/tmp/teams.json",
            }

    patch_cli_admin_runtime(monkeypatch, teams_admin=FakeAdminService)

    result = runner.invoke(app, ["teams-auth", "login", "--config", str(config_path)])

    assert result.exit_code == 0
    assert "Teams auth status" in result.output
    assert "2026-04-10T06:27:03+00:00" in result.output
    assert "Traceback" not in result.output


def test_teams_ingest_once_runs_single_cycle(tmp_path: Path, monkeypatch) -> None:
    config_path = write_config(tmp_path)
    runner = CliRunner()
    calls: list[str] = []

    class _FakeWorker:
        def run_once_with_lock(self):
            calls.append("run_once_with_lock")
            return SimpleNamespace(
                directories_polled=4,
                sessions_seen=6,
                new_messages_discovered=2,
                trigger_wake_notified=True,
                next_sleep_seconds=21.25,
            )

        def run_forever(self) -> None:
            raise AssertionError("run_forever should not be called in --once mode")

    monkeypatch.setattr(
        "evander.cli_workers._build_teams_ingest_worker", lambda cfg, **kwargs: _FakeWorker()
    )

    result = runner.invoke(
        app,
        ["teams-ingest", "--config", str(config_path), "--once", "--log-level", "DEBUG"],
    )

    assert result.exit_code == 0
    assert calls == ["run_once_with_lock"]
    assert "directories=4" in result.output
    assert "sessions=6" in result.output
    assert "new_messages=2" in result.output
    assert "trigger_wake_notified=True" in result.output
