from pathlib import Path


def test_setup_issue_vm_runs_single_post_exec_resume_verification() -> None:
    setup_issue_vm = (Path(__file__).resolve().parents[1] / "scripts" / "setup_issue_vm.sh").read_text(
        encoding="utf-8"
    )

    assert "AGENT_POST_EXEC_VERIFICATION_PROMPT" in setup_issue_vm
    assert "AGENT_RUNNER_LIB_B64" in setup_issue_vm
    assert "AGENT_RUNNER_CODEX_LIB_B64" in setup_issue_vm
    assert 'AGENT_STATE_LIB_B64' in setup_issue_vm
    assert 'AGENT_RUNNER_TMP_DIR="$(mktemp -d)"' in setup_issue_vm
    assert 'source "${AGENT_RUNNER_TMP_DIR}/agent_runner.sh"' in setup_issue_vm
    assert "agent_runner_resume_last \\" in setup_issue_vm
    assert '--model "${AGENT_MODEL}" \\' in setup_issue_vm
    assert '--dangerously-bypass-approvals-and-sandbox \\' in setup_issue_vm
    assert 'STATE_PATH="${LOG_DIR}/agent-${GUEST_REPO_NAME}.state"' in setup_issue_vm
    assert 'write_state "running" "initial"' in setup_issue_vm
    assert 'write_state "running" "post_exec" "${exec_status}"' in setup_issue_vm
    assert 'trap on_exit_write_idle EXIT' in setup_issue_vm
    assert 'AGENT_STATE_REPO_NAME="${GUEST_REPO_NAME}"' in setup_issue_vm
