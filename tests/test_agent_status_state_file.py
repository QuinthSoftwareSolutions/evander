from pathlib import Path


def test_agent_status_uses_explicit_state_files_for_repo_and_codex_heartbeat() -> None:
    script = (Path(__file__).resolve().parents[1] / "scripts" / "lib" / "agent_status.sh").read_text(
        encoding="utf-8"
    )

    assert 'for state_path in "${LOG_DIR}"/agent-*.state; do' in script
    assert 'codex_state_path="${LOG_DIR}/codex-resume-last.state"' in script
    assert 'status="$(read_state_value "${state_path}" "status")"' in script
    assert 'CONTROL_STATE_PATH="${LOG_DIR}/agent-control.state"' in script
    assert 'if [[ "${control_status}" == "disabled" ]]; then' in script
    assert 'printf \'%s\\n\' "disabled"' in script
    assert '[[ "${status}" == "running" ]]' in script
    assert 'updated_at="$(read_state_value "${state_path}" "updated_at")"' in script
    assert 'if ! state_is_fresh "${updated_at}" && ! is_active_agent_pid "${pid}"; then' in script
    assert 'VM_AGENT_STATE_MAX_AGE_SECONDS="${VM_AGENT_STATE_MAX_AGE_SECONDS:-600}"' in script
    assert 'cmdline_path="/proc/${pid}/cmdline"' in script
    assert 'cmdline="$(tr \'\\000\' \' \' < "${cmdline_path}" 2>/dev/null || true)"' in script
    assert (
        '[[ "${cmdline}" == *"agent_runner_exec"* || "${cmdline}" == *"agent_runner_resume_last"* ]]'
        in script
    )
    assert 'active_entries+=("${name}")' in script
    assert 'active_entries+=("codex-heartbeat")' in script
    assert 'date -u -d "${raw}" +%s' in script
