from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    os.chmod(path, 0o755)


def _prepare_script(tmp_path: Path) -> tuple[Path, Path]:
    root = Path(__file__).resolve().parents[1]
    source_script = root / "scripts" / "resume_issue_agent.sh"
    source_lib = root / "scripts" / "lib" / "vm_network.sh"
    source_guest_ssh_lib = root / "scripts" / "lib" / "guest_ssh.sh"
    source_runner_lib = root / "scripts" / "lib" / "agent_runner.sh"
    source_provider_assets_lib = root / "scripts" / "lib" / "agent_provider_assets.sh"
    source_runner_codex_lib = root / "scripts" / "lib" / "agent_runner_codex.sh"
    source_runner_copilot_lib = root / "scripts" / "lib" / "agent_runner_copilot.sh"
    source_state_lib = root / "scripts" / "lib" / "agent_state.sh"

    sandbox_root = tmp_path / "sandbox"
    scripts_dir = sandbox_root / "scripts"
    lib_dir = scripts_dir / "lib"
    scripts_dir.mkdir(parents=True)
    lib_dir.mkdir(parents=True)

    script_path = scripts_dir / "resume_issue_agent.sh"
    script_path.write_text(source_script.read_text(encoding="utf-8"), encoding="utf-8")
    os.chmod(script_path, 0o755)

    lib_path = lib_dir / "vm_network.sh"
    lib_path.write_text(source_lib.read_text(encoding="utf-8"), encoding="utf-8")
    guest_ssh_lib_path = lib_dir / "guest_ssh.sh"
    guest_ssh_lib_path.write_text(source_guest_ssh_lib.read_text(encoding="utf-8"), encoding="utf-8")
    runner_lib_path = lib_dir / "agent_runner.sh"
    runner_lib_path.write_text(source_runner_lib.read_text(encoding="utf-8"), encoding="utf-8")
    provider_assets_lib_path = lib_dir / "agent_provider_assets.sh"
    provider_assets_lib_path.write_text(
        source_provider_assets_lib.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    runner_codex_lib_path = lib_dir / "agent_runner_codex.sh"
    runner_codex_lib_path.write_text(source_runner_codex_lib.read_text(encoding="utf-8"), encoding="utf-8")
    runner_copilot_lib_path = lib_dir / "agent_runner_copilot.sh"
    runner_copilot_lib_path.write_text(
        source_runner_copilot_lib.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    state_lib_path = lib_dir / "agent_state.sh"
    state_lib_path.write_text(source_state_lib.read_text(encoding="utf-8"), encoding="utf-8")

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_executable(
        fake_bin / "ssh-keyscan",
        """#!/usr/bin/env bash
set -euo pipefail
host="${@: -1}"
printf '%s %s %s\\n' "${host}" "ssh-ed25519" "AAAA-test-host-key"
""",
    )
    return script_path, fake_bin


def _write_fake_sudo(path: Path) -> None:
    _write_executable(
        path,
        """#!/usr/bin/env bash
set -euo pipefail
if [[ "${1:-}" == "-n" ]]; then
  shift
fi
exec "$@"
""",
    )


def test_resume_issue_agent_runs_resume_command_in_guest(tmp_path: Path) -> None:
    script_path, fake_bin = _prepare_script(tmp_path)
    private_key = tmp_path / "id_ed25519"
    private_key.write_text("PRIVATE\n", encoding="utf-8")
    ssh_log = tmp_path / "ssh.log"
    _write_fake_sudo(fake_bin / "sudo")

    _write_executable(
        fake_bin / "virsh",
        """#!/usr/bin/env bash
set -euo pipefail
cmd="${1:-}"
shift || true

case "${cmd}" in
  dominfo)
    [[ "${1:-}" == "vm-14084" ]] || exit 1
    cat <<'EOF'
Id:             7
Name:           vm-14084
State:          running
EOF
    ;;
  domifaddr)
    [[ "${1:-}" == "vm-14084" ]] || exit 1
    printf 'vnet0 52:54:00:00:00:01 ipv4 192.168.122.11/24\\n'
    ;;
  *)
    exit 1
    ;;
esac
""",
    )

    _write_executable(
        fake_bin / "ssh",
        f"""#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$*" >> "{ssh_log}"
if [[ "${{@: -1}}" == "true" ]]; then
  exit 0
fi
cat > /dev/null
printf 'resumed pid=321 log=/home/agent/.agent-sandbox/agent-ExampleIndustryAPP.log\\n'
exit 0
""",
    )

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    env["SSH_PRIVATE_KEY_PATH"] = str(private_key)
    env["USER"] = env.get("USER", "root")
    env["AGENT_MODEL"] = "gpt-5.3-codex"

    result = subprocess.run(
        [str(script_path), "Resume work now", "14084"],
        text=True,
        capture_output=True,
        env=env,
        check=True,
    )

    assert result.stdout == (
        "Agent resumed for vm-14084 "
        "(resumed pid=321 log=/home/agent/.agent-sandbox/agent-ExampleIndustryAPP.log).\n"
    )
    ssh_calls = ssh_log.read_text(encoding="utf-8").splitlines()
    assert any("agent@192.168.122.11 true" in call for call in ssh_calls)
    run_call = next(call for call in ssh_calls if "PROMPT_B64=" in call)
    assert "agent@192.168.122.11" in run_call
    assert "AGENT_MODEL='gpt-5.3-codex'" in run_call
    assert "bash -s" in run_call


def test_resume_issue_agent_observe_tails_resume_log_after_resuming(tmp_path: Path) -> None:
    script_path, fake_bin = _prepare_script(tmp_path)
    private_key = tmp_path / "id_ed25519"
    private_key.write_text("PRIVATE\n", encoding="utf-8")
    ssh_log = tmp_path / "ssh.log"
    tail_log = tmp_path / "tail-ssh.log"
    _write_fake_sudo(fake_bin / "sudo")

    _write_executable(
        fake_bin / "virsh",
        """#!/usr/bin/env bash
set -euo pipefail
cmd="${1:-}"
shift || true

case "${cmd}" in
  dominfo)
    [[ "${1:-}" == "vm-14084" ]] || exit 1
    cat <<'EOF'
Id:             7
Name:           vm-14084
State:          running
EOF
    ;;
  domifaddr)
    [[ "${1:-}" == "vm-14084" ]] || exit 1
    printf 'vnet0 52:54:00:00:00:01 ipv4 192.168.122.11/24\\n'
    ;;
  *)
    exit 1
    ;;
esac
""",
    )

    _write_executable(
        fake_bin / "ssh",
        f"""#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$*" >> "{ssh_log}"
printf '%s\\n' "$*" >> "{tail_log}"
if [[ "${{@: -1}}" == "true" ]]; then
  exit 0
fi
if [[ "$*" == *"PROMPT_B64="* ]]; then
  cat > /dev/null
  printf 'resumed pid=321 log=/home/agent/.agent-sandbox/agent-ExampleIndustryAPP.log\\n'
  exit 0
fi
cat > /dev/null
""",
    )

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    env["SSH_PRIVATE_KEY_PATH"] = str(private_key)
    env["USER"] = env.get("USER", "root")
    env["AGENT_MODEL"] = "gpt-5.3-codex"

    result = subprocess.run(
        [str(script_path), "Resume work now", "14084", "agent", "--observe", "25"],
        text=True,
        capture_output=True,
        env=env,
        check=True,
    )

    assert result.stdout == ""
    tail_calls = tail_log.read_text(encoding="utf-8").splitlines()
    assert any("PROMPT_B64=" in call for call in tail_calls)
    assert any("LINES='25' bash -s" in call for call in tail_calls)
    ssh_calls = ssh_log.read_text(encoding="utf-8").splitlines()
    assert any("agent@192.168.122.11 true" in call for call in ssh_calls)


def test_resume_issue_agent_requires_explicit_agent_model(tmp_path: Path) -> None:
    script_path, fake_bin = _prepare_script(tmp_path)
    private_key = tmp_path / "id_ed25519"
    private_key.write_text("PRIVATE\n", encoding="utf-8")
    _write_fake_sudo(fake_bin / "sudo")

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    env["SSH_PRIVATE_KEY_PATH"] = str(private_key)
    env["USER"] = env.get("USER", "root")
    env.pop("AGENT_MODEL", None)

    result = subprocess.run(
        [str(script_path), "Resume work now", "14084"],
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )

    assert result.returncode == 10
    assert "AGENT_MODEL is required for session-bound Codex execution" in result.stderr


def test_resume_issue_agent_reports_missing_vm(tmp_path: Path) -> None:
    script_path, fake_bin = _prepare_script(tmp_path)
    private_key = tmp_path / "id_ed25519"
    private_key.write_text("PRIVATE\n", encoding="utf-8")
    _write_fake_sudo(fake_bin / "sudo")

    _write_executable(
        fake_bin / "virsh",
        """#!/usr/bin/env bash
set -euo pipefail
exit 1
""",
    )

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    env["SSH_PRIVATE_KEY_PATH"] = str(private_key)
    env["USER"] = env.get("USER", "root")
    env["AGENT_MODEL"] = "gpt-5.3-codex"

    result = subprocess.run(
        [str(script_path), "Resume work now", "14084"],
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )

    assert result.returncode == 12
    assert result.stdout == ""
    assert result.stderr.strip() == "VM not found: vm-14084"


def test_resume_issue_agent_rejects_missing_provider_adapter(tmp_path: Path) -> None:
    script_path, fake_bin = _prepare_script(tmp_path)
    private_key = tmp_path / "id_ed25519"
    private_key.write_text("PRIVATE\n", encoding="utf-8")
    _write_fake_sudo(fake_bin / "sudo")

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    env["SSH_PRIVATE_KEY_PATH"] = str(private_key)
    env["AGENT_PROVIDER"] = "missing-provider"
    env["USER"] = env.get("USER", "root")

    result = subprocess.run(
        [str(script_path), "Resume work now", "14084"],
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )

    assert result.returncode == 1
    assert "Unsupported AGENT_PROVIDER 'missing-provider': missing runner adapter" in result.stderr


def test_resume_issue_agent_accepts_copilot_provider_when_adapter_exists(tmp_path: Path) -> None:
    script_path, fake_bin = _prepare_script(tmp_path)
    private_key = tmp_path / "id_ed25519"
    private_key.write_text("PRIVATE\n", encoding="utf-8")
    ssh_log = tmp_path / "ssh.log"
    _write_fake_sudo(fake_bin / "sudo")

    _write_executable(
        fake_bin / "virsh",
        """#!/usr/bin/env bash
set -euo pipefail
cmd="${1:-}"
shift || true

case "${cmd}" in
  dominfo)
    [[ "${1:-}" == "vm-14084" ]] || exit 1
    cat <<'EOF'
Id:             7
Name:           vm-14084
State:          running
EOF
    ;;
  domifaddr)
    [[ "${1:-}" == "vm-14084" ]] || exit 1
    printf 'vnet0 52:54:00:00:00:01 ipv4 192.168.122.11/24\\n'
    ;;
  *)
    exit 1
    ;;
esac
""",
    )

    _write_executable(
        fake_bin / "ssh",
        f"""#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$*" >> "{ssh_log}"
if [[ "${{@: -1}}" == "true" ]]; then
  exit 0
fi
cat > /dev/null
printf 'resumed pid=456 log=/home/agent/.agent-sandbox/agent-ExampleIndustryAPP.log\\n'
exit 0
""",
    )

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    env["SSH_PRIVATE_KEY_PATH"] = str(private_key)
    env["AGENT_PROVIDER"] = "copilot"
    env["USER"] = env.get("USER", "root")

    result = subprocess.run(
        [str(script_path), "Resume work now", "14084"],
        text=True,
        capture_output=True,
        env=env,
        check=True,
    )

    assert result.stdout == (
        "Agent resumed for vm-14084 "
        "(resumed pid=456 log=/home/agent/.agent-sandbox/agent-ExampleIndustryAPP.log).\n"
    )
    run_call = next(
        call for call in ssh_log.read_text(encoding="utf-8").splitlines() if "PROMPT_B64=" in call
    )
    assert "AGENT_PROVIDER='copilot'" in run_call


def test_resume_issue_agent_tracks_explicit_state_lifecycle() -> None:
    script_text = (Path(__file__).resolve().parents[1] / "scripts" / "resume_issue_agent.sh").read_text(
        encoding="utf-8"
    )

    assert 'resolve_primary_agent_paths() {' in script_text
    assert 'RUN_LOG_FILE="${LOG_DIR}/agent-${RESOLVED_REPO_NAME}.log"' in script_text
    assert 'PID_FILE="${LOG_DIR}/agent-${RESOLVED_REPO_NAME}.pid"' in script_text
    assert 'STATE_PATH="${LOG_DIR}/agent-${RESOLVED_REPO_NAME}.state"' in script_text
    assert 'AGENT_RUNNER_PROVIDER_LIB_NAME="$(agent_provider_runner_lib_name "${AGENT_PROVIDER}")"' in script_text
    assert 'AGENT_RUNNER_PROVIDER_LIB_B64' in script_text
    assert 'printf \'%s\' "${AGENT_RUNNER_PROVIDER_LIB_B64}" | base64 -d > "${LOG_DIR}/lib/${AGENT_RUNNER_PROVIDER_LIB_NAME}"' in script_text
    assert "AGENT_RUNNER_CODEX_LIB_B64" not in script_text
    assert 'ensure_agent_enabled' in script_text
    assert 'write_state "running" "resume"' in script_text
    assert 'trap on_exit_write_idle EXIT' in script_text
    assert 'STATE_PATH="${STATE_PATH}" \\' in script_text
    assert 'AGENT_STATE_REPO_NAME="${AGENT_STATE_REPO_NAME}" \\' in script_text
    assert 'AGENT_STATE_LIB_B64' in script_text
    assert 'printf \'%s\' "${AGENT_RUNNER_LIB_B64}" | base64 -d > "${LOG_DIR}/lib/agent_runner.sh"' in script_text
    assert 'nohup env \\' in script_text
    assert '. "$HOME/.agent-sandbox/lib/agent_runner.sh"' in script_text
    assert 'STATE_PATH="${LOG_DIR}/codex-resume-last.state"' in script_text
    assert 'RUN_LOG_FILE="${LOG_DIR}/codex-heartbeat-run.log"' in script_text
    assert 'write_state "running" "resume"' in script_text
    assert 'last_attempt_at' in script_text
    assert '--worker-mode' in script_text
    assert 'write_agent_control_state "${HOST_CONTROL_STATUS}" "host-sync"' in script_text
    assert 'LOG_PATH="${LOG_DIR}/agent-${RESOLVED_REPO_NAME}.log"' in script_text
    assert 'LOG_PATH="${LOG_DIR}/codex-heartbeat-run.log"' in script_text
    assert 'RUN_LOG_START_SIZE=0' in script_text
    assert 'RUN_LOG_START_SIZE="$(wc -c < "${RUN_LOG_FILE}" | tr -d ' in script_text
    assert 'tail -c "+$((RUN_LOG_START_SIZE + 1))" "${RUN_LOG_FILE}" 2>/dev/null' in script_text
    assert """awk -F': ' '/^session id: / {thread_id=$2} END {print thread_id}'""" in script_text


def test_resume_issue_agent_worker_mode_starts_vm_and_skips_tty(tmp_path: Path) -> None:
    script_path, fake_bin = _prepare_script(tmp_path)
    caller_home = tmp_path / "caller-home"
    ssh_dir = caller_home / ".ssh"
    ssh_dir.mkdir(parents=True)
    private_key = ssh_dir / "id_ed25519"
    private_key.write_text("PRIVATE\n", encoding="utf-8")
    ssh_log = tmp_path / "ssh.log"
    virsh_log = tmp_path / "virsh.log"
    host_state_path = tmp_path / "resume-state.json"
    _write_fake_sudo(fake_bin / "sudo")

    _write_executable(
        fake_bin / "virsh",
        f"""#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$*" >> "{virsh_log}"
cmd="${{1:-}}"
shift || true

case "${{cmd}}" in
  dominfo)
    [[ "${{1:-}}" == "vm-14084" ]] || exit 1
    cat <<'OUT'
Id:             -
Name:           vm-14084
State:          shut off
OUT
    ;;
  start)
    [[ "${{1:-}}" == "vm-14084" ]] || exit 1
    ;;
  domifaddr)
    [[ "${{1:-}}" == "vm-14084" ]] || exit 1
    printf 'vnet0 52:54:00:00:00:01 ipv4 192.168.122.44/24\\n'
    ;;
  *)
    exit 1
    ;;
esac
""",
    )

    _write_executable(
        fake_bin / "ssh",
        f"""#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$*" >> "{ssh_log}"
if [[ "${{@: -1}}" == "true" ]]; then
  exit 0
fi
cat > /dev/null
exit 0
""",
    )

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    env["HOME"] = str(caller_home)
    env["USER"] = ""
    env["SUDO_USER"] = ""
    env["AGENT_MODEL"] = "gpt-5.3-codex"

    result = subprocess.run(
        [
            str(script_path),
            "--prompt",
            "Resume work now",
            "--vm-name",
            "vm-14084",
            "--session-id",
            "session-14084",
            "--worker-mode",
            "--host-state-file",
            str(host_state_path),
        ],
        text=True,
        capture_output=True,
        env=env,
        check=True,
    )

    assert result.stdout == ""
    ssh_calls = ssh_log.read_text(encoding="utf-8").splitlines()
    run_call = next(call for call in ssh_calls if "PROMPT_B64=" in call)
    assert " -t " not in f" {run_call} "
    assert "agent@192.168.122.44" in run_call
    assert f"UserKnownHostsFile={caller_home}/.ssh/agent-sandbox-known_hosts" in run_call
    assert "SESSION_ID='session-14084'" in run_call
    assert "AGENT_BRANCH_SUFFIX='vm-14084'" in run_call

    virsh_calls = virsh_log.read_text(encoding="utf-8")
    assert "start vm-14084" in virsh_calls

    state_payload = json.loads(host_state_path.read_text(encoding="utf-8"))
    session_state = state_payload["sessions"]["session-14084"]
    assert session_state["outcome"] == "ok"
    assert "last_attempt_at" in session_state


def test_resume_issue_agent_dispatch_only_returns_after_launch(tmp_path: Path) -> None:
    script_path, fake_bin = _prepare_script(tmp_path)
    caller_home = tmp_path / "caller-home"
    ssh_dir = caller_home / ".ssh"
    ssh_dir.mkdir(parents=True)
    private_key = ssh_dir / "id_ed25519"
    private_key.write_text("PRIVATE\n", encoding="utf-8")
    ssh_log = tmp_path / "ssh.log"
    host_state_path = tmp_path / "resume-state.json"
    _write_fake_sudo(fake_bin / "sudo")

    _write_executable(
        fake_bin / "virsh",
        """#!/usr/bin/env bash
set -euo pipefail
cmd="${1:-}"
shift || true

case "${cmd}" in
  dominfo)
    [[ "${1:-}" == "bug-finder-vm" ]] || exit 1
    cat <<'OUT'
Id:             7
Name:           bug-finder-vm
State:          running
OUT
    ;;
  domifaddr)
    [[ "${1:-}" == "bug-finder-vm" ]] || exit 1
    printf 'vnet0 52:54:00:00:00:01 ipv4 192.168.122.44/24\n'
    ;;
  *)
    exit 1
    ;;
esac
""",
    )

    _write_executable(
        fake_bin / "ssh",
        f"""#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$*" >> "{ssh_log}"
if [[ "${{@: -1}}" == "true" ]]; then
  exit 0
fi
cat > /dev/null
exit 0
""",
    )

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    env["HOME"] = str(caller_home)
    env["USER"] = ""
    env["SUDO_USER"] = ""
    env["AGENT_MODEL"] = "gpt-5.3-codex"

    result = subprocess.run(
        [
            str(script_path),
            "--prompt",
            "Find one bug",
            "--vm-name",
            "bug-finder-vm",
            "--session-id",
            "bug-session",
            "--worker-mode",
            "--dispatch-only",
            "--host-state-file",
            str(host_state_path),
        ],
        text=True,
        capture_output=True,
        env=env,
        check=True,
    )

    assert result.stdout == ""
    ssh_calls = ssh_log.read_text(encoding="utf-8").splitlines()
    run_call = next(call for call in ssh_calls if "PROMPT_B64=" in call)
    assert " -t " not in f" {run_call} "
    assert "bug-finder-vm" in run_call

    state_payload = json.loads(host_state_path.read_text(encoding="utf-8"))
    session_state = state_payload["sessions"]["bug-session"]
    assert session_state["outcome"] == "ok"


def test_resume_issue_agent_refuses_host_disabled_agent(tmp_path: Path) -> None:
    script_path, fake_bin = _prepare_script(tmp_path)
    private_key = tmp_path / "id_ed25519"
    private_key.write_text("PRIVATE\n", encoding="utf-8")
    host_control_state_path = tmp_path / "host-agent-control-state.json"
    host_control_state_path.write_text(
        json.dumps(
            {
                "vm-14084": {
                    "vm_name": "vm-14084",
                    "status": "disabled",
                    "phase": "operator-disable",
                    "updated_at": "2026-03-25T00:00:00Z",
                }
            }
        ),
        encoding="utf-8",
    )
    _write_fake_sudo(fake_bin / "sudo")

    _write_executable(
        fake_bin / "virsh",
        """#!/usr/bin/env bash
set -euo pipefail
cmd="${1:-}"
shift || true

case "${cmd}" in
  dominfo)
    [[ "${1:-}" == "vm-14084" ]] || exit 1
    cat <<'EOF'
Id:             -
Name:           vm-14084
State:          shut off
EOF
    ;;
  *)
    exit 1
    ;;
esac
""",
    )

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    env["SSH_PRIVATE_KEY_PATH"] = str(private_key)
    env["USER"] = env.get("USER", "root")
    env["AGENT_MODEL"] = "gpt-5.3-codex"

    result = subprocess.run(
        [
            str(script_path),
            "Resume work now",
            "14084",
            "agent",
            "--host-control-state-path",
            str(host_control_state_path),
        ],
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )

    assert result.returncode == 16
    assert result.stderr.strip() == "Agent is disabled for vm-14084."
