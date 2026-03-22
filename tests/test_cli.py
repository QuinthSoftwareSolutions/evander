import json
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
