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


def test_install_systemd_units_preflight_checks_for_mssql_worker_password_when_enabled() -> None:
    text = _script_text()

    assert 'config/ado-worker/config.json' in text
    assert 'runtime/directory-config/root/ado-worker.json' in text
    assert 'config/issue-orchestrator/config.json' in text
    assert 'secrets.env' in text
    assert 'config/ado-worker.env' not in text
    assert 'agent-sandbox-issue-orchestrator.service' in text


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
    heartbeat = (repo / "systemd" / "agent-sandbox-host-heartbeat.service.template").read_text(
        encoding="utf-8"
    )
    orchestrator = (repo / "systemd" / "agent-sandbox-issue-orchestrator.service.template").read_text(
        encoding="utf-8"
    )

    assert "EnvironmentFile=-__PROJECT_DIR__/config/gateway.env" in gateway
    assert "EnvironmentFile=-__PROJECT_DIR__/secrets.env" in gateway
    assert "EnvironmentFile=-__PROJECT_DIR__/config/openai-relay.env" in openai
    assert "EnvironmentFile=-__PROJECT_DIR__/secrets.env" in openai
    assert "EnvironmentFile=-__PROJECT_DIR__/config/nuget-relay.env" in nuget
    assert "EnvironmentFile=-__PROJECT_DIR__/secrets.env" in nuget
    assert "EnvironmentFile=-__PROJECT_DIR__/config/host-heartbeat.env" in heartbeat
    assert "EnvironmentFile=-__PROJECT_DIR__/secrets.env" in heartbeat
    assert "Environment=ADO_WORKER_LOG_LEVEL=INFO" in (
        repo / "systemd" / "agent-sandbox-ado-worker.service.template"
    ).read_text(encoding="utf-8")
    assert "--log-level ${ADO_WORKER_LOG_LEVEL}" in (
        repo / "systemd" / "agent-sandbox-ado-worker.service.template"
    ).read_text(encoding="utf-8")
    assert "Environment=ISSUE_ORCHESTRATOR_LOG_LEVEL=INFO" in orchestrator
    assert "--log-level ${ISSUE_ORCHESTRATOR_LOG_LEVEL}" in orchestrator
    assert "ExecStart=__PROJECT_DIR__/.venv/bin/agent-sandbox-issue-orchestrator" in orchestrator
    assert "EnvironmentFile=-__PROJECT_DIR__/secrets.env" in orchestrator
