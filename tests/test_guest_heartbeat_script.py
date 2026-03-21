from pathlib import Path


def test_guest_heartbeat_script_includes_lock_pid_and_cooldown_logic() -> None:
    script = Path("/home/jonathan/agent-sandbox/scripts/guest/heartbeat_cycle.sh").read_text(
        encoding="utf-8"
    )

    assert "flock -n 9" in script
    assert "kill -0" in script
    assert "codex-active" in script
    assert "stale_pid" in script
    assert "has_pending_feedback" in script
    assert "heartbeat.cooldown" in script
    assert "codex-heartbeat.pid" in script
    assert "codex-heartbeat-run.log" in script
    assert "codex exec resume" in script
    assert "HEARTBEAT_ENABLE_RESUME" in script
    assert 'HEARTBEAT_ENABLE_RESUME="${HEARTBEAT_ENABLE_RESUME:-1}"' in script
    assert 'nohup codex exec resume "${resume_args[@]}" "${prompt}"' in script
    assert "New human/provider feedback is pending" in script


def test_codex_bootstrap_deploys_guest_heartbeat_script() -> None:
    script = Path("/home/jonathan/agent-sandbox/scripts/bootstrap_codex_vm.sh").read_text(
        encoding="utf-8"
    )

    assert 'GUEST_HEARTBEAT_SCRIPT="${ROOT_DIR}/scripts/guest/heartbeat_cycle.sh"' in script
    assert 'scp "${SSH_OPTS[@]}" "${GUEST_HEARTBEAT_SCRIPT}" "${VM_USER}@${VM_IP}:~/bin/agent-heartbeat-cycle"' in script
    assert "chmod +x ~/bin/agent-heartbeat-cycle" in script
