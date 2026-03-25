from pathlib import Path


def test_setup_issue_vm_supports_opt_in_bootstraps() -> None:
    setup_issue_vm = Path("/home/jonathan/agent-sandbox/scripts/setup_issue_vm.sh").read_text(
        encoding="utf-8"
    )

    assert "--dotnet" in setup_issue_vm
    assert "--python" in setup_issue_vm
    assert "--nuget" in setup_issue_vm
    assert "--mssql" in setup_issue_vm
    assert "--mssql-init-root" in setup_issue_vm
    assert "--playwright" in setup_issue_vm
    assert "--frontend-deps" in setup_issue_vm
    assert 'BOOTSTRAP_PYTHON=0' in setup_issue_vm
    assert 'BOOTSTRAP_DOTNET=0' in setup_issue_vm
    assert 'BOOTSTRAP_NUGET=0' in setup_issue_vm
    assert 'BOOTSTRAP_MSSQL=0' in setup_issue_vm
    assert 'BOOTSTRAP_PLAYWRIGHT=0' in setup_issue_vm
    assert 'BOOTSTRAP_FRONTEND_DEPS=0' in setup_issue_vm
    assert '"${SCRIPT_DIR}/bootstrap_python_vm.sh"' in setup_issue_vm
    assert '"${SCRIPT_DIR}/bootstrap_dotnet_vm.sh"' in setup_issue_vm
    assert '"${SCRIPT_DIR}/bootstrap_nuget_vm.sh"' in setup_issue_vm
    assert '"${SCRIPT_DIR}/bootstrap_mssql_vm.sh"' in setup_issue_vm
    assert '"${SCRIPT_DIR}/bootstrap_playwright_vm.sh"' in setup_issue_vm
    assert '"${SCRIPT_DIR}/bootstrap_frontend_deps_vm.sh"' in setup_issue_vm
    assert 'MSSQL_SA_PASSWORD="${MSSQL_SA_PASSWORD}"' in setup_issue_vm
    assert 'Use the repo-local virtualenv at ${TARGET_DIR}/.venv' in setup_issue_vm


def test_setup_issue_vm_reinterprets_positionals_when_nuget_is_disabled() -> None:
    setup_issue_vm = Path("/home/jonathan/agent-sandbox/scripts/setup_issue_vm.sh").read_text(
        encoding="utf-8"
    )

    assert 'if (( BOOTSTRAP_NUGET )); then' in setup_issue_vm
    assert 'PREWARM_TARGET="${2:-}"' in setup_issue_vm
    assert 'REPO_SOURCE_ARG="${2:-}"' in setup_issue_vm


def test_setup_issue_vm_reinterprets_positionals_when_frontend_deps_is_enabled() -> None:
    setup_issue_vm = Path("/home/jonathan/agent-sandbox/scripts/setup_issue_vm.sh").read_text(
        encoding="utf-8"
    )

    assert "--frontend-host-checkout" in setup_issue_vm
    assert "--frontend-install-root" in setup_issue_vm
    assert "FRONTEND_INSTALL_ROOTS" in setup_issue_vm
    assert "FRONTEND_INSTALL_ROOTS_JSON" in setup_issue_vm
