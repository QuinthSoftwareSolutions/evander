from pathlib import Path


def test_setup_issue_vm_runs_single_post_exec_resume_verification() -> None:
    setup_issue_vm = (Path(__file__).resolve().parents[1] / "scripts" / "setup_issue_vm.sh").read_text(
        encoding="utf-8"
    )

    assert "AGENT_SANDBOX_CODEX_POST_EXEC_VERIFICATION_PROMPT" in setup_issue_vm
    assert "codex exec resume \\" in setup_issue_vm
    assert '--last \\' in setup_issue_vm
    assert '--model "${CODEX_MODEL}" \\' in setup_issue_vm
    assert '--dangerously-bypass-approvals-and-sandbox \\' in setup_issue_vm
    assert 'STATE_PATH="${LOG_DIR}/codex-${GUEST_REPO_NAME}.state"' in setup_issue_vm
    assert 'write_state "running" "initial"' in setup_issue_vm
    assert 'write_state "running" "post_exec" "${exec_status}"' in setup_issue_vm
    assert 'write_state "idle" "complete" "${exit_code}"' in setup_issue_vm
