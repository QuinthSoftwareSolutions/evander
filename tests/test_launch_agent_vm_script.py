from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "launch_agent_vm.sh"


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    os.chmod(path, 0o755)


def _prepare_fake_bin(tmp_path: Path) -> tuple[Path, Path, Path]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()

    qemu_log = tmp_path / "qemu-img.log"
    cloud_log = tmp_path / "cloud-localds.log"
    virt_log = tmp_path / "virt-install.log"

    _write_executable(
        fake_bin / "qemu-img",
        f"""#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$*" >> "{qemu_log}"
case "${{1:-}}" in
  create)
    touch "${{@: -1}}"
    ;;
  resize)
    :
    ;;
  *)
    exit 1
    ;;
esac
""",
    )
    _write_executable(
        fake_bin / "cloud-localds",
        f"""#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$*" >> "{cloud_log}"
touch "${{1:-}}"
""",
    )
    _write_executable(
        fake_bin / "virt-install",
        f"""#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$*" >> "{virt_log}"
""",
    )

    return fake_bin, qemu_log, cloud_log


def _run_launch(
    tmp_path: Path,
    *,
    vm_storage_dir: Path,
    selected_env: dict[str, str] | None = None,
) -> tuple[subprocess.CompletedProcess[str], Path]:
    fake_bin, qemu_log, _ = _prepare_fake_bin(tmp_path)

    images_dir = vm_storage_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    (images_dir / "ubuntu-24.04-server-cloudimg-amd64.img").write_text("default", encoding="utf-8")
    (images_dir / "dotnet-agent.qcow2").write_text("dotnet", encoding="utf-8")

    ssh_key_file = tmp_path / "ssh.pub"
    ssh_key_file.write_text("ssh-ed25519 AAAA-test-key\n", encoding="utf-8")

    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{fake_bin}:{env['PATH']}",
            "USER": "agent",
            "VM_STORAGE_DIR": str(vm_storage_dir),
            "AGENT_SANDBOX_RUNTIME_DIR": str(tmp_path / "runtime"),
            "SSH_PUBLIC_KEY_FILE": str(ssh_key_file),
            "GATEWAY_URL": "http://gateway.test/mcp",
            "HTTP_TARGET_RELAY_BASE_URL": "http://relay.test",
            "LLM_BASE_URL": "http://llm.test/v1",
            "ANTHROPIC_BASE_URL": "http://anthropic.test",
        }
    )
    if selected_env:
        env.update(selected_env)

    result = subprocess.run(
        [str(SCRIPT), "vm-test", "session-test"],
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )
    return result, qemu_log


@pytest.mark.parametrize("case", ["default", "relative", "absolute"])
def test_launch_agent_vm_uses_selected_or_default_base_image(
    tmp_path: Path, case: str
) -> None:
    vm_storage_dir = tmp_path / "storage"
    selected_env: dict[str, str] = {}
    expected_base_image: Path

    if case == "default":
        expected_base_image = vm_storage_dir / "images" / "ubuntu-24.04-server-cloudimg-amd64.img"
    elif case == "relative":
        selected_env = {
            "LIBVIRT_BASE_IMAGE": "dotnet-agent.qcow2",
            "BASE_IMAGE": str(tmp_path / "should-not-be-used.qcow2"),
        }
        expected_base_image = vm_storage_dir / "images" / "dotnet-agent.qcow2"
    else:
        custom_base = tmp_path / "custom-base.qcow2"
        custom_base.write_text("custom", encoding="utf-8")
        selected_env = {"BASE_IMAGE": str(custom_base)}
        expected_base_image = custom_base

    result, qemu_log = _run_launch(
        tmp_path, vm_storage_dir=vm_storage_dir, selected_env=selected_env
    )

    assert result.returncode == 0, result.stderr
    assert "Selected libvirt base image:" in result.stdout

    qemu_lines = qemu_log.read_text(encoding="utf-8").splitlines()
    assert qemu_lines, "qemu-img was not invoked"
    create_args = qemu_lines[0].split()
    assert create_args[:4] == ["create", "-f", "qcow2", "-F"]
    assert create_args[4:7] == ["qcow2", "-b", str(expected_base_image)]


def test_launch_agent_vm_fails_before_qemu_when_selected_image_is_missing(
    tmp_path: Path,
) -> None:
    vm_storage_dir = tmp_path / "storage"

    result, qemu_log = _run_launch(
        tmp_path,
        vm_storage_dir=vm_storage_dir,
        selected_env={
            "LIBVIRT_BASE_IMAGE": "missing-template.qcow2",
            "BASE_IMAGE": str(vm_storage_dir / "images" / "ubuntu-24.04-server-cloudimg-amd64.img"),
        },
    )

    assert result.returncode != 0
    assert f"Missing base image: {vm_storage_dir / 'images' / 'missing-template.qcow2'}" in result.stderr
    assert not qemu_log.exists() or qemu_log.read_text(encoding="utf-8").strip() == ""
