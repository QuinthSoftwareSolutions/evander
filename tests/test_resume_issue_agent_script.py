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

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
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
printf 'resumed pid=321 log=/home/agent/.agent-sandbox/codex-heartbeat-run.log\\n'
exit 0
""",
    )

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    env["SSH_PRIVATE_KEY_PATH"] = str(private_key)
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
        "(resumed pid=321 log=/home/agent/.agent-sandbox/codex-heartbeat-run.log).\n"
    )
    ssh_calls = ssh_log.read_text(encoding="utf-8").splitlines()
    assert any("agent@192.168.122.11 true" in call for call in ssh_calls)
    run_call = next(call for call in ssh_calls if "PROMPT_B64=" in call)
    assert "agent@192.168.122.11" in run_call
    assert "CODEX_MODEL='gpt-5.4'" in run_call
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
  printf 'resumed pid=321 log=/home/agent/.agent-sandbox/codex-heartbeat-run.log\\n'
  exit 0
fi
cat > /dev/null
""",
    )

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    env["SSH_PRIVATE_KEY_PATH"] = str(private_key)
    env["USER"] = env.get("USER", "root")

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


def test_resume_issue_agent_tracks_explicit_state_lifecycle() -> None:
    script_text = (Path(__file__).resolve().parents[1] / "scripts" / "resume_issue_agent.sh").read_text(
        encoding="utf-8"
    )

    assert 'STATE_PATH="${LOG_DIR}/codex-resume-last.state"' in script_text
    assert 'write_state "running" "resume"' in script_text
    assert 'write_state "idle" "complete" "${exit_code}"' in script_text
    assert 'last_attempt_at' in script_text
    assert '--worker-mode' in script_text


def test_resume_issue_agent_worker_mode_starts_vm_and_skips_tty(tmp_path: Path) -> None:
    script_path, fake_bin = _prepare_script(tmp_path)
    private_key = tmp_path / "id_ed25519"
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
    env["SSH_PRIVATE_KEY_PATH"] = str(private_key)
    env["USER"] = env.get("USER", "root")

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

    virsh_calls = virsh_log.read_text(encoding="utf-8")
    assert "start vm-14084" in virsh_calls

    state_payload = json.loads(host_state_path.read_text(encoding="utf-8"))
    session_state = state_payload["sessions"]["session-14084"]
    assert session_state["outcome"] == "ok"
    assert "last_attempt_at" in session_state
