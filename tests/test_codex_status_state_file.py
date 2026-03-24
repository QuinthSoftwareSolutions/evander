from pathlib import Path


def test_codex_status_uses_explicit_state_file_only() -> None:
    script = (Path(__file__).resolve().parents[1] / "scripts" / "lib" / "codex_status.sh").read_text(
        encoding="utf-8"
    )

    assert 'for state_path in "${LOG_DIR}"/codex-*.state; do' in script
    assert 'status="$(read_state_value "${state_path}" "status")"' in script
    assert '[[ "${status}" == "running" ]]' in script
    assert 'updated_at="$(read_state_value "${state_path}" "updated_at")"' in script
    assert 'if ! state_is_fresh "${updated_at}" && ! is_active_codex_pid "${pid}"; then' in script
    assert 'VM_CODEX_STATE_MAX_AGE_SECONDS="${VM_CODEX_STATE_MAX_AGE_SECONDS:-600}"' in script
    assert 'active_entries+=("${name}")' in script
    assert 'date -u -d "${raw}" +%s' in script
