from pathlib import Path


def _script_text() -> str:
    return (Path(__file__).resolve().parents[1] / "scripts" / "install_systemd_units.sh").read_text(
        encoding="utf-8"
    )


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


def test_install_systemd_units_root_without_sudo_user_uses_repo_owner() -> None:
    text = _script_text()

    assert 'if [[ -n "${SUDO_USER:-}" ]]; then' in text
    assert 'RUN_USER="${SUDO_USER}"' in text
    assert 'elif [[ "$(id -u)" -eq 0 ]]; then' in text
    assert 'RUN_USER="$(stat -c \'%U\' "${ROOT_DIR}")"' in text
    assert 'RUN_USER="${USER}"' in text


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

    assert "config/ado-worker/config.json" in text
    assert "runtime/directory-config/root/ado-worker.json" in text
    assert "config/teams-ingest/config.json" in text
    assert "config/trigger-orchestrator/config.json" in text
    assert "secrets.env" in text
    assert "config/ado-worker.env" not in text
    assert "config/issue-orchestrator/config.json" not in text
    assert "evander-issue-orchestrator.service" not in text
    assert "evander-teams-ingest.service" in text
    assert "evander-trigger-orchestrator.service" in text
    assert "evander-http-target-relay.service" in text
    assert "evander-didww-sip-bridge.service" in text
    assert "evander-admin-web.service" in text
    assert "evander-npm-audit-facade.service" in text
    assert "evander-managed-git-refresh.service" in text


def test_install_systemd_units_retires_legacy_agent_sandbox_units() -> None:
    text = _script_text()

    assert 'LEGACY_LOCAL_SECRETS_FILE="/etc/agent-sandbox/local-secrets.env"' in text
    assert 'LOCAL_SECRETS_FILE="/etc/evander/local-secrets.env"' in text
    assert "Environment=EVANDER_SECRETS_ENV_FILE=%s" in text
    assert "grant_local_secrets_git_access" in text
    assert 'local git_user="${EVANDER_GIT_USER:-${RUN_USER}}"' in text
    assert 'setfacl -m "u:${git_user}:--x" "${secrets_dir}"' in text
    assert 'setfacl -m "u:${git_user}:r--" "${secrets_file}"' in text
    assert "migrate_legacy_local_secrets" in text
    assert "retire_legacy_agent_sandbox_units" in text
    assert "agent-sandbox-gateway.service" in text
    assert "agent-sandbox-admin-web.service" in text
    assert "agent-sandbox-nexus.service" not in text
    assert "agent-sandbox-worker-daemon.service" not in text
    assert 'systemctl disable --now "${unit}"' in text
    assert 'rm -rf "${SYSTEMD_DIR:?}/${unit}.d"' in text
    assert 'systemctl enable "${EVANDER_UNITS[@]}"' in text


def test_install_systemd_units_preflight_is_service_profile_aware() -> None:
    text = _script_text()

    assert 'SERVICE_PROFILE="${EVANDER_SERVICE_PROFILE:-full}"' in text
    assert 'SETUP_EXECUTION_BACKEND="${EVANDER_SETUP_EXECUTION_BACKEND:-}"' in text
    assert 'SETUP_EXECUTION_MODEL="${EVANDER_SETUP_EXECUTION_MODEL:-}"' in text
    assert 'if [[ "${SERVICE_PROFILE}" == "full" ]]; then' in text
    assert (
        'elif [[ "${SERVICE_PROFILE}" == "setup" && "${SETUP_EXECUTION_BACKEND}" == "libvirt_vm" ]]; then'
        in text
    )
    assert "Skipping virsh check for service profile" in text
    assert (
        'if [[ "${SERVICE_PROFILE}" == "setup" && "${SETUP_EXECUTION_MODEL}" == "container_same_host" ]]; then'
        in text
    )
    assert "Install Docker so the same-host container worker can run agent containers." in text
    assert 'if [[ "${SERVICE_PROFILE}" == "setup" ]]; then' in text
    assert 'setup_managed_config_severity="warning"' in text
    assert '"${setup_managed_config_severity}"' in text
    assert '"warning"' in text


def test_install_systemd_units_preflight_checks_admin_frontend_assets() -> None:
    text = _script_text()

    assert "admin_frontend_assets_ready()" in text
    assert "apt_package_candidate()" in text
    assert "check_admin_frontend_assets" in text
    assert "Admin Web frontend assets are built." in text
    assert "Admin Web frontend assets are missing or stale; npm is available" in text
    assert "guided setup can install Node.js ${nodejs_version} and npm ${npm_version}" in text
    assert "Admin Web frontend assets are missing or stale and npm is not installed." in text
    assert "run guided setup on an apt-based host with sudo available" in text


def test_install_systemd_units_no_longer_requires_removed_per_service_env_files() -> None:
    text = _script_text()

    assert "config/gateway.env" not in text
    assert "config/openai-relay.env" not in text
    assert "config/nuget-relay.env" not in text
    assert "config/host-heartbeat.env" not in text


def test_service_templates_use_optional_shared_secrets_env() -> None:
    repo = Path(__file__).resolve().parents[1]

    gateway = (repo / "systemd" / "evander-gateway.service.template").read_text(encoding="utf-8")
    openai = (repo / "systemd" / "evander-openai-relay.service.template").read_text(
        encoding="utf-8"
    )
    nuget = (repo / "systemd" / "evander-nuget-relay.service.template").read_text(encoding="utf-8")
    npm_audit_facade = (repo / "systemd" / "evander-npm-audit-facade.service.template").read_text(
        encoding="utf-8"
    )
    http_target = (repo / "systemd" / "evander-http-target-relay.service.template").read_text(
        encoding="utf-8"
    )
    didww_sip_bridge = (repo / "systemd" / "evander-didww-sip-bridge.service.template").read_text(
        encoding="utf-8"
    )
    discord_wake = (repo / "systemd" / "evander-discord-wake.service.template").read_text(
        encoding="utf-8"
    )
    scheduled_wake = (repo / "systemd" / "evander-scheduled-wake.service.template").read_text(
        encoding="utf-8"
    )
    managed_git_refresh = (
        repo / "systemd" / "evander-managed-git-refresh.service.template"
    ).read_text(encoding="utf-8")
    teams_ingest = (repo / "systemd" / "evander-teams-ingest.service.template").read_text(
        encoding="utf-8"
    )
    discord_ingest = (repo / "systemd" / "evander-discord-ingest.service.template").read_text(
        encoding="utf-8"
    )
    proxy_issue_sync = (repo / "systemd" / "evander-proxy-issue-sync.service.template").read_text(
        encoding="utf-8"
    )
    trigger_orchestrator = (
        repo / "systemd" / "evander-trigger-orchestrator.service.template"
    ).read_text(encoding="utf-8")
    admin_web = (repo / "systemd" / "evander-admin-web.service.template").read_text(
        encoding="utf-8"
    )

    assert "EnvironmentFile=-__PROJECT_DIR__/config/gateway.env" in gateway
    assert "EnvironmentFile=-__PROJECT_DIR__/secrets.env" in gateway
    assert "EnvironmentFile=-__PROJECT_DIR__/config/admin-web.env" in admin_web
    assert "EnvironmentFile=-__PROJECT_DIR__/config/openai-relay.env" in admin_web
    assert "EnvironmentFile=-__PROJECT_DIR__/secrets.env" in admin_web
    assert "EnvironmentFile=-/etc/evander/local-secrets.env" in admin_web
    assert (
        "ExecStart=__PROJECT_DIR__/.venv/bin/evander-admin-web --runtime-dir __PROJECT_DIR__/runtime --host ${ADMIN_WEB_HOST} --port ${ADMIN_WEB_PORT}"
        in admin_web
    )
    assert "EnvironmentFile=-__PROJECT_DIR__/config/openai-relay.env" in openai
    assert "EnvironmentFile=-__PROJECT_DIR__/secrets.env" in openai
    assert "EnvironmentFile=-__PROJECT_DIR__/config/nuget-relay.env" in nuget
    assert "EnvironmentFile=-__PROJECT_DIR__/secrets.env" in nuget
    assert "EnvironmentFile=-__PROJECT_DIR__/config/npm-audit-facade.env" in npm_audit_facade
    assert "EnvironmentFile=-__PROJECT_DIR__/secrets.env" in npm_audit_facade
    assert "ExecStart=__PROJECT_DIR__/.venv/bin/evander-npm-audit-facade" in npm_audit_facade
    assert "EnvironmentFile=-__PROJECT_DIR__/config/http-target-relay.env" in http_target
    assert "EnvironmentFile=-__PROJECT_DIR__/secrets.env" in http_target
    assert "ExecStart=__PROJECT_DIR__/.venv/bin/evander-http-target-relay" in http_target
    assert "EnvironmentFile=-__PROJECT_DIR__/config/didww-sip-bridge.env" in didww_sip_bridge
    assert "EnvironmentFile=-__PROJECT_DIR__/secrets.env" in didww_sip_bridge
    assert (
        "ExecStart=__PROJECT_DIR__/.venv/bin/evander-didww-sip-bridge --runtime-dir __PROJECT_DIR__/runtime"
        in didww_sip_bridge
    )
    assert "Environment=DISCORD_WAKE_LOG_LEVEL=INFO" in discord_wake
    assert "--log-level ${DISCORD_WAKE_LOG_LEVEL}" in discord_wake
    assert "ExecStart=__PROJECT_DIR__/.venv/bin/evander-discord-wake" in discord_wake
    assert "EnvironmentFile=-__PROJECT_DIR__/secrets.env" in discord_wake
    assert "Environment=SCHEDULED_WAKE_LOG_LEVEL=INFO" in scheduled_wake
    assert "--log-level ${SCHEDULED_WAKE_LOG_LEVEL}" in scheduled_wake
    assert "ExecStart=__PROJECT_DIR__/.venv/bin/evander-scheduled-wake" in scheduled_wake
    assert "EnvironmentFile=-__PROJECT_DIR__/secrets.env" in scheduled_wake
    assert "EnvironmentFile=-__PROJECT_DIR__/secrets.env" in managed_git_refresh
    assert "EnvironmentFile=-__PROJECT_DIR__/config/managed-git-refresh.env" in managed_git_refresh
    assert "RuntimeDirectory=evander" in managed_git_refresh
    assert "Group=__GIT_GROUP__" in managed_git_refresh
    assert "RuntimeDirectoryMode=0750" in managed_git_refresh
    assert "--allowed-uid __GIT_UID__" in managed_git_refresh
    assert "--socket-path /run/evander/managed-git-refresh.sock" in managed_git_refresh
    assert "ExecStart=__PROJECT_DIR__/.venv/bin/evander-managed-git-refresh" in managed_git_refresh
    assert "Environment=TEAMS_INGEST_LOG_LEVEL=INFO" in teams_ingest
    assert "ExecStart=__PROJECT_DIR__/scripts/run_teams_ingest.sh" in teams_ingest
    assert "EnvironmentFile=-__PROJECT_DIR__/secrets.env" in teams_ingest
    assert "Environment=DISCORD_INGEST_LOG_LEVEL=INFO" in discord_ingest
    assert "--log-level ${DISCORD_INGEST_LOG_LEVEL}" in discord_ingest
    assert "ExecStart=__PROJECT_DIR__/.venv/bin/evander-discord-ingest" in discord_ingest
    assert "EnvironmentFile=-__PROJECT_DIR__/secrets.env" in discord_ingest
    assert "Environment=ADO_WORKER_LOG_LEVEL=INFO" in (
        repo / "systemd" / "evander-ado-worker.service.template"
    ).read_text(encoding="utf-8")
    assert "--log-level ${ADO_WORKER_LOG_LEVEL}" in (
        repo / "systemd" / "evander-ado-worker.service.template"
    ).read_text(encoding="utf-8")
    assert "Environment=PROXY_ISSUE_SYNC_LOG_LEVEL=INFO" in proxy_issue_sync
    assert "ExecStart=__PROJECT_DIR__/.venv/bin/evander-proxy-issue-sync" in (proxy_issue_sync)
    assert "--log-level ${PROXY_ISSUE_SYNC_LOG_LEVEL}" in proxy_issue_sync
    assert "EnvironmentFile=-__PROJECT_DIR__/secrets.env" in proxy_issue_sync
    assert "Environment=TRIGGER_ORCHESTRATOR_LOG_LEVEL=INFO" in trigger_orchestrator
    assert "--log-level ${TRIGGER_ORCHESTRATOR_LOG_LEVEL}" in trigger_orchestrator
    assert "evander-cli trigger-orchestrator" in trigger_orchestrator
    assert "EnvironmentFile=-__PROJECT_DIR__/secrets.env" in trigger_orchestrator


def test_wake_service_templates_run_as_operator_user_and_load_shared_secrets() -> None:
    repo = Path(__file__).resolve().parents[1]

    discord_wake = (repo / "systemd" / "evander-discord-wake.service.template").read_text(
        encoding="utf-8"
    )
    scheduled_wake = (repo / "systemd" / "evander-scheduled-wake.service.template").read_text(
        encoding="utf-8"
    )

    assert discord_wake.count("\nUser=__RUN_USER__\n") == 1
    assert scheduled_wake.count("\nUser=__RUN_USER__\n") == 1
    assert "WorkingDirectory=__PROJECT_DIR__" in discord_wake
    assert "WorkingDirectory=__PROJECT_DIR__" in scheduled_wake
    assert "EnvironmentFile=-__PROJECT_DIR__/secrets.env" in discord_wake
    assert "EnvironmentFile=-__PROJECT_DIR__/secrets.env" in scheduled_wake
    assert "Environment=DISCORD_WAKE_LOG_LEVEL=INFO" in discord_wake
    assert "--log-level ${DISCORD_WAKE_LOG_LEVEL}" in discord_wake
    assert "ExecStart=__PROJECT_DIR__/.venv/bin/evander-discord-wake" in discord_wake
    assert "Environment=SCHEDULED_WAKE_LOG_LEVEL=INFO" in scheduled_wake
    assert "--log-level ${SCHEDULED_WAKE_LOG_LEVEL}" in scheduled_wake
    assert "ExecStart=__PROJECT_DIR__/.venv/bin/evander-scheduled-wake" in scheduled_wake
