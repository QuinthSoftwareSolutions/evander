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
    (lib_dir / "vm_network.sh").write_text(
        source_vm_lib.read_text(encoding="utf-8"), encoding="utf-8"
    )
    (lib_dir / "guest_ssh.sh").write_text(
        source_guest_ssh_lib.read_text(encoding="utf-8"), encoding="utf-8"
    )

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
    host_state_path = tmp_path / "host-agent-control-state.json"
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
        [str(script_path), "disable", "14084", "--host-state-path", str(host_state_path)],
        text=True,
        capture_output=True,
        env=env,
        check=True,
    )

    assert (
        result.stdout.strip()
        == "state=disabled path=/home/agent/.agent-sandbox/agent-control.state"
    )
    assert "CALLER_HOME: unbound variable" not in result.stderr
    ssh_calls = ssh_log.read_text(encoding="utf-8").splitlines()
    assert any("agent@192.168.122.11 true" in call for call in ssh_calls)
    assert any(
        "REMOTE_STATUS='disabled' REMOTE_PHASE='operator-disable' bash -s" in call
        for call in ssh_calls
    )
    assert any("UserKnownHostsFile=" in call for call in ssh_calls)

    remote_script = ssh_stdin.read_text(encoding="utf-8")
    assert 'CONTROL_STATE_PATH="${LOG_DIR}/agent-control.state"' in remote_script
    assert 'terminate_pid_file "${pid_file}"' in remote_script

    host_state = json.loads(host_state_path.read_text(encoding="utf-8"))
    assert host_state["vm-14084"]["status"] == "disabled"


def test_set_issue_agent_state_tracks_control_file_state_machine() -> None:
    script_text = (
        Path(__file__).resolve().parents[1] / "scripts" / "set_issue_agent_state.sh"
    ).read_text(encoding="utf-8")

    assert "set_issue_agent_state.sh <enable|disable> <issue-number> [vm-user]" in script_text
    assert 'REMOTE_STATUS="disabled"' in script_text
    assert 'REMOTE_STATUS="idle"' in script_text
    assert 'CONTROL_STATE_PATH="${LOG_DIR}/agent-control.state"' in script_text


def test_set_issue_agent_state_updates_host_state_when_vm_is_off(tmp_path: Path) -> None:
    script_path, fake_bin = _prepare_script(tmp_path)
    private_key = tmp_path / "id_ed25519"
    private_key.write_text("PRIVATE\n", encoding="utf-8")
    host_state_path = tmp_path / "host-agent-control-state.json"
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

    result = subprocess.run(
        [str(script_path), "disable", "14084", "--host-state-path", str(host_state_path)],
        text=True,
        capture_output=True,
        env=env,
        check=True,
    )

    assert "guest_sync=skipped" in result.stdout
    host_state = json.loads(host_state_path.read_text(encoding="utf-8"))
    assert host_state["vm-14084"]["status"] == "disabled"


def test_set_issue_agent_state_preserves_unrelated_host_entries(tmp_path: Path) -> None:
    script_path, fake_bin = _prepare_script(tmp_path)
    private_key = tmp_path / "id_ed25519"
    private_key.write_text("PRIVATE\n", encoding="utf-8")
    host_state_path = tmp_path / "host-agent-control-state.json"
    host_state_path.write_text(
        json.dumps(
            {
                "vm-1": {
                    "vm_name": "vm-1",
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

    subprocess.run(
        [str(script_path), "disable", "14084", "--host-state-path", str(host_state_path)],
        text=True,
        capture_output=True,
        env=env,
        check=True,
    )

    host_state = json.loads(host_state_path.read_text(encoding="utf-8"))
    assert host_state["vm-1"]["status"] == "disabled"
    assert host_state["vm-14084"]["status"] == "disabled"


def test_set_issue_agent_state_accepts_vm_user_before_host_state_option(tmp_path: Path) -> None:
    script_path, fake_bin = _prepare_script(tmp_path)
    private_key = tmp_path / "id_ed25519"
    private_key.write_text("PRIVATE\n", encoding="utf-8")
    ssh_log = tmp_path / "ssh.log"
    host_state_path = tmp_path / "host-agent-control-state.json"
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
    printf 'vnet0 52:54:00:00:00:01 ipv4 192.168.122.11/24\n'
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
printf 'state=disabled path=/home/root/.agent-sandbox/agent-control.state\\n'
""",
    )

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    env["SSH_PRIVATE_KEY_PATH"] = str(private_key)
    env["USER"] = env.get("USER", "root")

    result = subprocess.run(
        [str(script_path), "disable", "14084", "root", "--host-state-path", str(host_state_path)],
        text=True,
        capture_output=True,
        env=env,
        check=True,
    )

    assert (
        result.stdout.strip() == "state=disabled path=/home/root/.agent-sandbox/agent-control.state"
    )
    ssh_calls = ssh_log.read_text(encoding="utf-8").splitlines()
    assert any("root@192.168.122.11 true" in call for call in ssh_calls)
