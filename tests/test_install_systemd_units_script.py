from pathlib import Path


def _script_text() -> str:
    return (Path(__file__).resolve().parents[1] / "scripts" / "install_systemd_units.sh").read_text(encoding="utf-8")


def test_install_systemd_units_supports_preflight_help_output() -> None:
    text = _script_text()

    assert "Usage: install_systemd_units.sh [--preflight] [--help]" in text
    assert "--preflight" in text
    assert "Run validation checks only (non-destructive)." in text


def test_install_systemd_units_runs_preflight_before_install() -> None:
    text = _script_text()

    assert 'if [[ "${MODE}" == "preflight" ]]; then' in text
    assert "run_preflight_checks" in text
    assert "Aborting install because preflight checks failed." in text


def test_install_systemd_units_preflight_reports_failures_with_hints() -> None:
    text = _script_text()

    assert "[PASS]" in text
    assert "[FAIL]" in text
    assert "hint:" in text
    assert "Result: FAIL (critical checks failed)." in text
    assert 'local severity="${3:-critical}"' in text
    assert 'report_fail "Missing config file ${file_path}." "${hint}" "${severity}"' in text


def test_install_systemd_units_preflight_checks_for_mssql_worker_password_when_enabled() -> None:
    text = _script_text()

    assert 'config/ado-worker/config.json' in text
    assert 'runtime/directory-config/root/ado-worker.json' in text
    assert 'config/teams-ingest/config.json' in text
    assert 'config/trigger-orchestrator/config.json' in text
    assert 'secrets.env' in text
    assert 'config/ado-worker.env' not in text
    assert 'config/issue-orchestrator/config.json' not in text
    assert 'agent-sandbox-issue-orchestrator.service' not in text
    assert 'agent-sandbox-teams-ingest.service' in text
    assert 'agent-sandbox-trigger-orchestrator.service' in text
    assert 'agent-sandbox-http-target-relay.service' in text
    assert 'agent-sandbox-admin-web.service' in text


def test_install_systemd_units_preflight_is_service_profile_aware() -> None:
    text = _script_text()

    assert 'SERVICE_PROFILE="${AGENT_SANDBOX_SERVICE_PROFILE:-full}"' in text
    assert 'if [[ "${SERVICE_PROFILE}" == "full" ]]; then' in text
    assert "Skipping virsh check for service profile" in text
    assert '"warning"' in text


def test_install_systemd_units_no_longer_requires_removed_per_service_env_files() -> None:
    text = _script_text()

    assert 'config/gateway.env' not in text
    assert 'config/openai-relay.env' not in text
    assert 'config/nuget-relay.env' not in text
    assert 'config/host-heartbeat.env' not in text


def test_service_templates_use_optional_shared_secrets_env() -> None:
    repo = Path(__file__).resolve().parents[1]

    gateway = (repo / "systemd" / "agent-sandbox-gateway.service.template").read_text(encoding="utf-8")
    openai = (repo / "systemd" / "agent-sandbox-openai-relay.service.template").read_text(
        encoding="utf-8"
    )
    nuget = (repo / "systemd" / "agent-sandbox-nuget-relay.service.template").read_text(
        encoding="utf-8"
    )
    http_target = (repo / "systemd" / "agent-sandbox-http-target-relay.service.template").read_text(
        encoding="utf-8"
    )
    discord_wake = (repo / "systemd" / "agent-sandbox-discord-wake.service.template").read_text(
        encoding="utf-8"
    )
    scheduled_wake = (repo / "systemd" / "agent-sandbox-scheduled-wake.service.template").read_text(
        encoding="utf-8"
    )
    teams_ingest = (repo / "systemd" / "agent-sandbox-teams-ingest.service.template").read_text(
        encoding="utf-8"
    )
    discord_ingest = (repo / "systemd" / "agent-sandbox-discord-ingest.service.template").read_text(
        encoding="utf-8"
    )
    trigger_orchestrator = (
        repo / "systemd" / "agent-sandbox-trigger-orchestrator.service.template"
    ).read_text(encoding="utf-8")
    admin_web = (repo / "systemd" / "agent-sandbox-admin-web.service.template").read_text(encoding="utf-8")

    assert "EnvironmentFile=-__PROJECT_DIR__/config/gateway.env" in gateway
    assert "EnvironmentFile=-__PROJECT_DIR__/secrets.env" in gateway
    assert "EnvironmentFile=-__PROJECT_DIR__/config/admin-web.env" in admin_web
    assert "EnvironmentFile=-__PROJECT_DIR__/secrets.env" in admin_web
    assert "ExecStart=__PROJECT_DIR__/.venv/bin/agent-sandbox-admin-web --runtime-dir __PROJECT_DIR__/runtime --host ${ADMIN_WEB_HOST} --port ${ADMIN_WEB_PORT}" in admin_web
    assert "EnvironmentFile=-__PROJECT_DIR__/config/openai-relay.env" in openai
    assert "EnvironmentFile=-__PROJECT_DIR__/secrets.env" in openai
    assert "EnvironmentFile=-__PROJECT_DIR__/config/nuget-relay.env" in nuget
    assert "EnvironmentFile=-__PROJECT_DIR__/secrets.env" in nuget
    assert "EnvironmentFile=-__PROJECT_DIR__/config/http-target-relay.env" in http_target
    assert "EnvironmentFile=-__PROJECT_DIR__/secrets.env" in http_target
    assert "ExecStart=__PROJECT_DIR__/.venv/bin/agent-sandbox-http-target-relay" in http_target
    assert "Environment=DISCORD_WAKE_LOG_LEVEL=INFO" in discord_wake
    assert "--log-level ${DISCORD_WAKE_LOG_LEVEL}" in discord_wake
    assert "ExecStart=__PROJECT_DIR__/.venv/bin/agent-sandbox-discord-wake" in discord_wake
    assert "EnvironmentFile=-__PROJECT_DIR__/secrets.env" in discord_wake
    assert "Environment=SCHEDULED_WAKE_LOG_LEVEL=INFO" in scheduled_wake
    assert "--log-level ${SCHEDULED_WAKE_LOG_LEVEL}" in scheduled_wake
    assert "ExecStart=__PROJECT_DIR__/.venv/bin/agent-sandbox-scheduled-wake" in scheduled_wake
    assert "EnvironmentFile=-__PROJECT_DIR__/secrets.env" in scheduled_wake
    assert "Environment=TEAMS_INGEST_LOG_LEVEL=INFO" in teams_ingest
    assert "ExecStart=__PROJECT_DIR__/scripts/run_teams_ingest.sh" in teams_ingest
    assert "EnvironmentFile=-__PROJECT_DIR__/secrets.env" in teams_ingest
    assert "Environment=DISCORD_INGEST_LOG_LEVEL=INFO" in discord_ingest
    assert "--log-level ${DISCORD_INGEST_LOG_LEVEL}" in discord_ingest
    assert "ExecStart=__PROJECT_DIR__/.venv/bin/agent-sandbox-discord-ingest" in discord_ingest
    assert "EnvironmentFile=-__PROJECT_DIR__/secrets.env" in discord_ingest
    assert "Environment=ADO_WORKER_LOG_LEVEL=INFO" in (
        repo / "systemd" / "agent-sandbox-ado-worker.service.template"
    ).read_text(encoding="utf-8")
    assert "--log-level ${ADO_WORKER_LOG_LEVEL}" in (
        repo / "systemd" / "agent-sandbox-ado-worker.service.template"
    ).read_text(encoding="utf-8")
    assert "Environment=TRIGGER_ORCHESTRATOR_LOG_LEVEL=INFO" in trigger_orchestrator
    assert "--log-level ${TRIGGER_ORCHESTRATOR_LOG_LEVEL}" in trigger_orchestrator
    assert "agent-sandbox-cli trigger-orchestrator" in trigger_orchestrator
    assert "EnvironmentFile=-__PROJECT_DIR__/secrets.env" in trigger_orchestrator
