from pathlib import Path


def test_setup_issue_vm_runs_single_post_exec_resume_verification() -> None:
    setup_issue_vm = (Path(__file__).resolve().parents[1] / "scripts" / "setup_issue_vm.sh").read_text(
        encoding="utf-8"
    )

    assert "AGENT_POST_EXEC_VERIFICATION_PROMPT" in setup_issue_vm
    assert "AGENT_RUNNER_LIB_B64" in setup_issue_vm
    assert "AGENT_RUNNER_PROVIDER_LIB_B64" in setup_issue_vm
    assert "AGENT_RUNNER_PROVIDER_LIB_NAME" in setup_issue_vm
    assert "AGENT_RUNNER_CODEX_LIB_B64" not in setup_issue_vm
    assert 'AGENT_STATE_LIB_B64' in setup_issue_vm
    assert 'AGENT_RUNNER_TMP_DIR="$(mktemp -d)"' in setup_issue_vm
    assert 'source "${AGENT_RUNNER_TMP_DIR}/agent_runner.sh"' in setup_issue_vm
    assert 'printf \'%s\' "${AGENT_RUNNER_LIB_B64}" | base64 -d > "${LOG_DIR}/lib/agent_runner.sh"' in setup_issue_vm
    assert 'printf \'%s\' "${AGENT_RUNNER_PROVIDER_LIB_B64}" | base64 -d > "${LOG_DIR}/lib/${AGENT_RUNNER_PROVIDER_LIB_NAME}"' in setup_issue_vm
    assert '. "$HOME/.agent-sandbox/lib/agent_runner.sh"' in setup_issue_vm
    assert "agent_runner_resume_last \\" in setup_issue_vm
    assert '--model "${AGENT_MODEL}" \\' in setup_issue_vm
    assert '--dangerously-bypass-approvals-and-sandbox \\' in setup_issue_vm
    assert 'STATE_PATH="${LOG_DIR}/agent-${GUEST_REPO_NAME}.state"' in setup_issue_vm
    assert 'write_state "running" "initial"' in setup_issue_vm
    assert 'write_state "running" "post_exec" "${exec_status}"' in setup_issue_vm
    assert 'trap on_exit_write_idle EXIT' in setup_issue_vm
    assert 'AGENT_STATE_REPO_NAME="${GUEST_REPO_NAME}"' in setup_issue_vm
    assert 'AGENT_BOOTSTRAP_SCRIPT_PATH="$(agent_provider_bootstrap_script_path "${SCRIPT_DIR}" "${AGENT_PROVIDER}")"' in setup_issue_vm


def test_setup_vm_selects_provider_bootstrap_script() -> None:
    setup_vm = (Path(__file__).resolve().parents[1] / "scripts" / "setup_vm.sh").read_text(
        encoding="utf-8"
    )

    assert 'source "${SCRIPT_DIR}/lib/agent_provider_assets.sh"' in setup_vm
    assert 'AGENT_BOOTSTRAP_SCRIPT_PATH="$(agent_provider_bootstrap_script_path "${SCRIPT_DIR}" "${AGENT_PROVIDER}")"' in setup_vm
    assert setup_vm.index('AGENT_BOOTSTRAP_SCRIPT_PATH="$(agent_provider_bootstrap_script_path "${SCRIPT_DIR}" "${AGENT_PROVIDER}")"') < setup_vm.index('log_step "launch_vm"')
    assert "Unsupported AGENT_PROVIDER" in setup_vm
    assert 'log_step "bootstrap_${AGENT_PROVIDER}"' in setup_vm
    assert '"${AGENT_BOOTSTRAP_SCRIPT_PATH}" "${VM_NAME}" "${VM_USER}"' in setup_vm
    assert '"${SCRIPT_DIR}/bootstrap_codex_vm.sh" "${VM_NAME}" "${VM_USER}"' not in setup_vm
