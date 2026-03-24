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
    source_codex_lib = root / "scripts" / "lib" / "codex_status.sh"

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
    codex_lib_path = lib_dir / "codex_status.sh"
    codex_lib_path.write_text(source_codex_lib.read_text(encoding="utf-8"), encoding="utf-8")

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
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
