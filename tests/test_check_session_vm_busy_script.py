from __future__ import annotations

import os
import subprocess
from pathlib import Path


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    os.chmod(path, 0o755)


def _prepare_script(tmp_path: Path) -> tuple[Path, Path]:
    root = Path(__file__).resolve().parents[1]
    source_script = root / "scripts" / "check_session_vm_busy.sh"
    source_vm_lib = root / "scripts" / "lib" / "vm_network.sh"
    source_codex_lib = root / "scripts" / "lib" / "codex_status.sh"

    sandbox_root = tmp_path / "sandbox"
    scripts_dir = sandbox_root / "scripts"
    lib_dir = scripts_dir / "lib"
    scripts_dir.mkdir(parents=True)
    lib_dir.mkdir(parents=True)

    script_path = scripts_dir / "check_session_vm_busy.sh"
    script_path.write_text(source_script.read_text(encoding="utf-8"), encoding="utf-8")
    os.chmod(script_path, 0o755)

    (lib_dir / "vm_network.sh").write_text(source_vm_lib.read_text(encoding="utf-8"), encoding="utf-8")
    (lib_dir / "codex_status.sh").write_text(source_codex_lib.read_text(encoding="utf-8"), encoding="utf-8")

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    return script_path, fake_bin


def test_check_session_vm_busy_reports_busy(tmp_path: Path) -> None:
    script_path, fake_bin = _prepare_script(tmp_path)
    private_key = tmp_path / "id_ed25519"
    private_key.write_text("PRIVATE\n", encoding="utf-8")

    _write_executable(
        fake_bin / "virsh",
        """#!/usr/bin/env bash
set -euo pipefail
if [[ "${1:-}" == "domifaddr" ]]; then
  printf 'vnet0 52:54:00:00:00:01 ipv4 192.168.122.11/24\\n'
  exit 0
fi
exit 1
""",
    )

    _write_executable(
        fake_bin / "ssh",
        """#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' 'running (repo-one)'
""",
    )

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    env["SSH_PRIVATE_KEY_PATH"] = str(private_key)
    env["USER"] = env.get("USER", "root")

    result = subprocess.run(
        [str(script_path), "session-1", "vm-1"],
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )

    assert result.returncode == 0
    assert "status=busy" in result.stdout
    assert "reason=agent-busy" in result.stdout
    assert "session=session-1" in result.stdout
    assert "vm=vm-1" in result.stdout
