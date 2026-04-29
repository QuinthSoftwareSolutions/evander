from __future__ import annotations

import os
import subprocess
from pathlib import Path


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    os.chmod(path, 0o755)


def test_destroy_agent_vm_falls_back_to_sudo_for_vm_storage_cleanup(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    source_script = root / "scripts" / "destroy_agent_vm.sh"
    source_lib = root / "scripts" / "lib" / "guest_ssh.sh"

    sandbox_root = tmp_path / "sandbox"
    scripts_dir = sandbox_root / "scripts"
    lib_dir = scripts_dir / "lib"
    scripts_dir.mkdir(parents=True)
    lib_dir.mkdir(parents=True)

    script_path = scripts_dir / "destroy_agent_vm.sh"
    script_path.write_text(source_script.read_text(encoding="utf-8"), encoding="utf-8")
    os.chmod(script_path, 0o755)
    (lib_dir / "guest_ssh.sh").write_text(source_lib.read_text(encoding="utf-8"), encoding="utf-8")

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()

    virsh_log = tmp_path / "virsh.log"
    sudo_log = tmp_path / "sudo.log"

    _write_executable(
        fake_bin / "virsh",
        f"""#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$*" >> "{virsh_log}"
exit 0
""",
    )

    _write_executable(
        fake_bin / "ssh-keygen",
        """#!/usr/bin/env bash
set -euo pipefail
exit 0
""",
    )

    _write_executable(
        fake_bin / "rm",
        """#!/usr/bin/env bash
set -euo pipefail
if [[ "$*" == *"/var/lib/libvirt/images/agent-sandbox/vms/vm-1234"* ]]; then
  echo "rm: permission denied" >&2
  exit 1
fi
exec /bin/rm "$@"
""",
    )

    _write_executable(
        fake_bin / "sudo",
        f"""#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$*" >> "{sudo_log}"
if [[ "${{1:-}}" == "-n" ]]; then
  shift
fi
if [[ "${{1:-}}" == "rm" ]]; then
  shift
  exec /bin/rm "$@"
fi
exec "$@"
""",
    )

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    env["USER"] = env.get("USER", "root")
    env["HOME"] = str(tmp_path / "home")
    vm_storage_dir = tmp_path / "vm-storage"
    vm_dir = vm_storage_dir / "vms" / "vm-1234"
    vm_dir.mkdir(parents=True)
    (vm_dir / "disk.qcow2").write_text("disk", encoding="utf-8")
    env["VM_STORAGE_DIR"] = str(vm_storage_dir)
    host_control_state_path = tmp_path / "runtime" / "host-agent-control-state.json"
    host_control_state_path.parent.mkdir(parents=True)
    host_control_state_path.write_text(
        (
            "{\n"
            '  "vm-1234": {\n'
            '    "phase": "operator-disable",\n'
            '    "status": "disabled",\n'
            '    "updated_at": "2026-03-25T01:17:00Z",\n'
            '    "vm_name": "vm-1234"\n'
            "  },\n"
            '  "vm-9999": {\n'
            '    "phase": "operator-disable",\n'
            '    "status": "disabled",\n'
            '    "updated_at": "2026-03-25T01:17:01Z",\n'
            '    "vm_name": "vm-9999"\n'
            "  }\n"
            "}\n"
        ),
        encoding="utf-8",
    )
    env["HOST_AGENT_CONTROL_STATE_PATH"] = str(host_control_state_path)

    _write_executable(
        fake_bin / "rm",
        f"""#!/usr/bin/env bash
set -euo pipefail
if [[ "$*" == *"{vm_dir}"* ]]; then
  echo "rm: permission denied" >&2
  exit 1
fi
exec /bin/rm "$@"
""",
    )

    result = subprocess.run(
        [str(script_path), "vm-1234"],
        capture_output=True,
        text=True,
        env=env,
        check=True,
    )

    assert result.stdout.strip() == "Destroyed VM vm-1234"
    assert any(
        "destroy vm-1234" in line or "undefine vm-1234" in line
        for line in virsh_log.read_text(encoding="utf-8").splitlines()
    )
    sudo_calls = sudo_log.read_text(encoding="utf-8").splitlines()
    assert any(str(vm_dir) in call and "rm -rf" in call for call in sudo_calls)
    state_text = host_control_state_path.read_text(encoding="utf-8")
    assert '"vm-1234"' not in state_text
    assert '"vm-9999"' in state_text
