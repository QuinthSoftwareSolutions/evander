from __future__ import annotations

import os
import subprocess
from pathlib import Path


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    os.chmod(path, 0o755)


def _prepare_script(tmp_path: Path) -> tuple[Path, Path]:
    root = Path(__file__).resolve().parents[1]
    source_script = root / "scripts" / "set_issue_agent_state.sh"
    source_vm_lib = root / "scripts" / "lib" / "vm_network.sh"
    source_guest_ssh_lib = root / "scripts" / "lib" / "guest_ssh.sh"

    sandbox_root = tmp_path / "sandbox"
    scripts_dir = sandbox_root / "scripts"
    lib_dir = scripts_dir / "lib"
    scripts_dir.mkdir(parents=True)
    lib_dir.mkdir(parents=True)

    script_path = scripts_dir / "set_issue_agent_state.sh"
    script_path.write_text(source_script.read_text(encoding="utf-8"), encoding="utf-8")
    os.chmod(script_path, 0o755)
    (lib_dir / "vm_network.sh").write_text(source_vm_lib.read_text(encoding="utf-8"), encoding="utf-8")
    (lib_dir / "guest_ssh.sh").write_text(source_guest_ssh_lib.read_text(encoding="utf-8"), encoding="utf-8")

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


def test_set_issue_agent_state_disables_guest_control_state(tmp_path: Path) -> None:
    script_path, fake_bin = _prepare_script(tmp_path)
    private_key = tmp_path / "id_ed25519"
    private_key.write_text("PRIVATE\n", encoding="utf-8")
    ssh_log = tmp_path / "ssh.log"
    ssh_stdin = tmp_path / "ssh.stdin"
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
cat > "{ssh_stdin}"
printf 'state=disabled path=/home/agent/.agent-sandbox/agent-control.state\\n'
""",
    )

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    env["SSH_PRIVATE_KEY_PATH"] = str(private_key)
    env["USER"] = env.get("USER", "root")

    result = subprocess.run(
        [str(script_path), "disable", "14084"],
        text=True,
        capture_output=True,
        env=env,
        check=True,
    )

    assert result.stdout.strip() == "state=disabled path=/home/agent/.agent-sandbox/agent-control.state"
    ssh_calls = ssh_log.read_text(encoding="utf-8").splitlines()
    assert any("agent@192.168.122.11 true" in call for call in ssh_calls)
    assert any("REMOTE_STATUS='disabled' REMOTE_PHASE='operator-disable' bash -s" in call for call in ssh_calls)

    remote_script = ssh_stdin.read_text(encoding="utf-8")
    assert 'CONTROL_STATE_PATH="${LOG_DIR}/agent-control.state"' in remote_script
    assert 'terminate_pid_file "${pid_file}"' in remote_script


def test_set_issue_agent_state_tracks_control_file_state_machine() -> None:
    script_text = (Path(__file__).resolve().parents[1] / "scripts" / "set_issue_agent_state.sh").read_text(
        encoding="utf-8"
    )

    assert 'set_issue_agent_state.sh <enable|disable> <issue-number> [vm-user]' in script_text
    assert 'REMOTE_STATUS="disabled"' in script_text
    assert 'REMOTE_STATUS="idle"' in script_text
    assert 'CONTROL_STATE_PATH="${LOG_DIR}/agent-control.state"' in script_text
