from __future__ import annotations

import os
import subprocess
from pathlib import Path


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    os.chmod(path, 0o755)


def _prepare_script(tmp_path: Path) -> tuple[Path, Path]:
    root = Path(__file__).resolve().parents[1]
    source_script = root / "scripts" / "list_running_vms.sh"
    source_lib = root / "scripts" / "lib" / "vm_network.sh"
    source_agent_lib = root / "scripts" / "lib" / "agent_status.sh"
    source_guest_ssh_lib = root / "scripts" / "lib" / "guest_ssh.sh"

    sandbox_root = tmp_path / "sandbox"
    scripts_dir = sandbox_root / "scripts"
    lib_dir = scripts_dir / "lib"
    scripts_dir.mkdir(parents=True)
    lib_dir.mkdir(parents=True)

    script_path = scripts_dir / "list_running_vms.sh"
    script_path.write_text(source_script.read_text(encoding="utf-8"), encoding="utf-8")
    os.chmod(script_path, 0o755)

    lib_path = lib_dir / "vm_network.sh"
    lib_path.write_text(source_lib.read_text(encoding="utf-8"), encoding="utf-8")
    agent_lib_path = lib_dir / "agent_status.sh"
    agent_lib_path.write_text(source_agent_lib.read_text(encoding="utf-8"), encoding="utf-8")
    guest_ssh_lib_path = lib_dir / "guest_ssh.sh"
    guest_ssh_lib_path.write_text(source_guest_ssh_lib.read_text(encoding="utf-8"), encoding="utf-8")

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


def test_list_running_vms_reports_agent_statuses(tmp_path: Path) -> None:
    script_path, fake_bin = _prepare_script(tmp_path)
    private_key = tmp_path / "id_ed25519"
    private_key.write_text("PRIVATE\n", encoding="utf-8")

    _write_executable(
        fake_bin / "virsh",
        """#!/usr/bin/env bash
set -euo pipefail
cmd="${1:-}"
shift || true

case "${cmd}" in
  list)
    printf 'vm-active\\nvm-idle\\nvm-booting\\n'
    ;;
  dominfo)
    case "${1:-}" in
      vm-active)
        cat <<'EOF'
Id:             7
Name:           vm-active
State:          running
EOF
        ;;
      vm-idle)
        cat <<'EOF'
Id:             8
Name:           vm-idle
State:          running
EOF
        ;;
      vm-booting)
        cat <<'EOF'
Id:             9
Name:           vm-booting
State:          running
EOF
        ;;
      *)
        exit 1
        ;;
    esac
    ;;
  domifaddr)
    case "${1:-}" in
      vm-active)
        printf 'vnet0 52:54:00:00:00:01 ipv4 192.168.122.11/24\\n'
        ;;
      vm-idle)
        printf 'vnet0 52:54:00:00:00:02 ipv4 192.168.122.12/24\\n'
        ;;
      vm-booting)
        exit 1
        ;;
      *)
        exit 1
        ;;
    esac
    ;;
  *)
    exit 1
    ;;
esac
""",
    )

    _write_executable(
        fake_bin / "ssh",
        """#!/usr/bin/env bash
set -euo pipefail
target="${@: -2:1}"

case "${target}" in
  agent@192.168.122.11)
    printf '%s\\n' 'running (repo-one)'
    ;;
  agent@192.168.122.12)
    printf '%s\\n' 'idle'
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
        [str(script_path)],
        text=True,
        capture_output=True,
        env=env,
        check=True,
    )

    lines = result.stdout.strip().splitlines()
    assert lines[0].split() == ["ID", "Name", "State", "IP", "Agent"]
    assert "7    vm-active" in lines[1]
    assert "192.168.122.11" in lines[1]
    assert lines[1].endswith("running (repo-one)")
    assert "8    vm-idle" in lines[2]
    assert "192.168.122.12" in lines[2]
    assert lines[2].endswith("idle")
    assert "9    vm-booting" in lines[3]
    assert "unavailable" in lines[3]
    assert lines[3].endswith("unknown (no IP)")


def test_list_running_vms_exits_cleanly_when_no_vms_are_running(tmp_path: Path) -> None:
    script_path, fake_bin = _prepare_script(tmp_path)

    _write_executable(
        fake_bin / "virsh",
        """#!/usr/bin/env bash
set -euo pipefail
if [[ "${1:-}" == "list" ]]; then
  exit 0
fi
exit 1
""",
    )

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    env["USER"] = env.get("USER", "root")

    result = subprocess.run(
        [str(script_path)],
        text=True,
        capture_output=True,
        env=env,
        check=True,
    )

    assert result.stdout.strip() == "ID   Name                     State      IP              Agent"


def test_list_running_vms_all_includes_inactive_vms_without_ssh(tmp_path: Path) -> None:
    script_path, fake_bin = _prepare_script(tmp_path)
    private_key = tmp_path / "id_ed25519"
    private_key.write_text("PRIVATE\n", encoding="utf-8")
    ssh_calls = tmp_path / "ssh_calls"

    _write_executable(
        fake_bin / "virsh",
        """#!/usr/bin/env bash
set -euo pipefail
cmd="${1:-}"
shift || true

case "${cmd}" in
  list)
    if [[ "${1:-}" == "--all" ]]; then
      printf 'vm-active\\nvm-stopped\\n'
      exit 0
    fi
    printf 'vm-active\\n'
    ;;
  dominfo)
    case "${1:-}" in
      vm-active)
        cat <<'EOF'
Id:             7
Name:           vm-active
State:          running
EOF
        ;;
      vm-stopped)
        cat <<'EOF'
Id:             -
Name:           vm-stopped
State:          shut off
EOF
        ;;
      *)
        exit 1
        ;;
    esac
    ;;
  domifaddr)
    case "${1:-}" in
      vm-active)
        printf 'vnet0 52:54:00:00:00:01 ipv4 192.168.122.11/24\\n'
        ;;
      *)
        exit 1
        ;;
    esac
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
printf 'called\\n' >> "{ssh_calls}"
target="${{@: -2:1}}"

case "${{target}}" in
  agent@192.168.122.11)
    printf '%s\\n' 'running (repo-one)'
    ;;
  *)
    exit 1
    ;;
esac
""",
    )
    _write_executable(
        fake_bin / "sudo",
        """#!/usr/bin/env bash
set -euo pipefail
if [[ "${1:-}" == "-n" ]]; then
  shift
fi
exec "$@"
""",
    )

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    env["SSH_PRIVATE_KEY_PATH"] = str(private_key)
    env["USER"] = env.get("USER", "root")

    result = subprocess.run(
        [str(script_path), "--all"],
        text=True,
        capture_output=True,
        env=env,
        check=True,
    )

    lines = result.stdout.strip().splitlines()
    assert len(lines) == 3
    assert "7    vm-active" in lines[1]
    assert lines[1].endswith("running (repo-one)")
    assert "-    vm-stopped" in lines[2]
    assert "shut off" in lines[2]
    assert "unavailable" in lines[2]
    assert lines[2].endswith("inactive VM")
    assert ssh_calls.read_text(encoding="utf-8").splitlines() == ["called"]


def test_list_running_vms_uses_vm_alias_known_hosts_file(tmp_path: Path) -> None:
    script_path, fake_bin = _prepare_script(tmp_path)
    private_key = tmp_path / "id_ed25519"
    private_key.write_text("PRIVATE\n", encoding="utf-8")
    ssh_log = tmp_path / "ssh.log"

    _write_executable(
        fake_bin / "virsh",
        """#!/usr/bin/env bash
set -euo pipefail
cmd="${1:-}"
shift || true

case "${cmd}" in
  list)
    printf 'vm-clean\\n'
    ;;
  dominfo)
    [[ "${1:-}" == "vm-clean" ]] || exit 1
    cat <<'EOF'
Id:             11
Name:           vm-clean
State:          running
EOF
    ;;
  domifaddr)
    [[ "${1:-}" == "vm-clean" ]] || exit 1
    printf 'vnet0 52:54:00:00:00:04 ipv4 192.168.122.14/24\\n'
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
printf '%s\\n' 'idle'
""",
    )

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    env["SSH_PRIVATE_KEY_PATH"] = str(private_key)
    env["USER"] = env.get("USER", "root")

    result = subprocess.run(
        [str(script_path)],
        text=True,
        capture_output=True,
        env=env,
        check=True,
    )

    assert result.stderr == ""
    ssh_args = ssh_log.read_text(encoding="utf-8").strip()
    assert "-o StrictHostKeyChecking=yes" in ssh_args
    assert "-o HostKeyAlias=vm-clean" in ssh_args
    assert "agent-sandbox-known_hosts" in ssh_args


def test_list_running_vms_adds_missing_host_key_types_for_existing_vm_alias(tmp_path: Path) -> None:
    script_path, fake_bin = _prepare_script(tmp_path)
    private_key = tmp_path / "id_ed25519"
    private_key.write_text("PRIVATE\n", encoding="utf-8")
    home_dir = tmp_path / "home"
    ssh_dir = home_dir / ".ssh"
    ssh_dir.mkdir(parents=True)
    known_hosts_path = ssh_dir / "agent-sandbox-known_hosts"
    known_hosts_path.write_text("vm-partial ssh-rsa AAAA-existing-rsa\n", encoding="utf-8")

    _write_executable(
        fake_bin / "virsh",
        """#!/usr/bin/env bash
set -euo pipefail
cmd="${1:-}"
shift || true

case "${cmd}" in
  list)
    printf 'vm-partial\\n'
    ;;
  dominfo)
    [[ "${1:-}" == "vm-partial" ]] || exit 1
    cat <<'EOF'
Id:             12
Name:           vm-partial
State:          running
EOF
    ;;
  domifaddr)
    [[ "${1:-}" == "vm-partial" ]] || exit 1
    printf 'vnet0 52:54:00:00:00:05 ipv4 192.168.122.15/24\\n'
    ;;
  *)
    exit 1
    ;;
esac
""",
    )

    _write_executable(
        fake_bin / "ssh-keyscan",
        """#!/usr/bin/env bash
set -euo pipefail
host="${@: -1}"
printf '%s %s %s\\n' "${host}" "ssh-ed25519" "AAAA-added-ed25519"
""",
    )

    _write_executable(
        fake_bin / "ssh",
        """#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' 'idle'
""",
    )

    env = os.environ.copy()
    env["AGENT_SANDBOX_GUEST_KNOWN_HOSTS_FILE"] = str(known_hosts_path)
    env["HOME"] = str(home_dir)
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    env["SSH_PRIVATE_KEY_PATH"] = str(private_key)
    env["USER"] = env.get("USER", "root")

    result = subprocess.run(
        [str(script_path)],
        text=True,
        capture_output=True,
        env=env,
        check=True,
    )

    assert result.stderr == ""
    known_hosts_text = known_hosts_path.read_text(encoding="utf-8")
    assert "vm-partial ssh-rsa AAAA-existing-rsa" in known_hosts_text
    assert "vm-partial ssh-ed25519 AAAA-added-ed25519" in known_hosts_text


def test_list_running_vms_replaces_changed_host_key_for_existing_vm_alias(tmp_path: Path) -> None:
    script_path, fake_bin = _prepare_script(tmp_path)
    private_key = tmp_path / "id_ed25519"
    private_key.write_text("PRIVATE\n", encoding="utf-8")
    home_dir = tmp_path / "home"
    ssh_dir = home_dir / ".ssh"
    ssh_dir.mkdir(parents=True)
    known_hosts_path = ssh_dir / "agent-sandbox-known_hosts"
    known_hosts_path.write_text("vm-14357 ssh-ed25519 AAAA-old-ed25519\n", encoding="utf-8")

    _write_executable(
        fake_bin / "virsh",
        """#!/usr/bin/env bash
set -euo pipefail
cmd="${1:-}"
shift || true

case "${cmd}" in
  list)
    printf 'vm-14357\n'
    ;;
  dominfo)
    [[ "${1:-}" == "vm-14357" ]] || exit 1
    cat <<'EOF'
Id:             13
Name:           vm-14357
State:          running
EOF
    ;;
  domifaddr)
    [[ "${1:-}" == "vm-14357" ]] || exit 1
    printf 'vnet0 52:54:00:00:00:06 ipv4 192.168.122.16/24\n'
    ;;
  *)
    exit 1
    ;;
esac
""",
    )

    _write_executable(
        fake_bin / "ssh-keyscan",
        """#!/usr/bin/env bash
set -euo pipefail
host="${@: -1}"
printf '%s %s %s\n' "${host}" "ssh-ed25519" "AAAA-new-ed25519"
""",
    )

    _write_executable(
        fake_bin / "ssh",
        """#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' 'idle'
""",
    )

    env = os.environ.copy()
    env["AGENT_SANDBOX_GUEST_KNOWN_HOSTS_FILE"] = str(known_hosts_path)
    env["HOME"] = str(home_dir)
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    env["SSH_PRIVATE_KEY_PATH"] = str(private_key)
    env["USER"] = env.get("USER", "root")

    result = subprocess.run(
        [str(script_path)],
        text=True,
        capture_output=True,
        env=env,
        check=True,
    )

    assert result.stderr == ""
    known_hosts_text = known_hosts_path.read_text(encoding="utf-8")
    assert "vm-14357 ssh-ed25519 AAAA-new-ed25519" in known_hosts_text
    assert "AAAA-old-ed25519" not in known_hosts_text
