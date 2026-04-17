from pathlib import Path
import os
import subprocess


ROOT = Path(__file__).resolve().parents[1]


def test_issue_vm_scripts_source_shared_network_helper() -> None:
    setup = (ROOT / 'scripts' / 'setup_issue_vm.sh').read_text(encoding='utf-8')
    check = (ROOT / 'scripts' / 'check_issue_vm.sh').read_text(encoding='utf-8')

    assert 'source "${SCRIPT_DIR}/lib/vm_network.sh"' in setup
    assert 'source "${SCRIPT_DIR}/lib/vm_network.sh"' in check
    assert 'vm_network_wait_for_ip' in setup
    assert 'vm_network_wait_for_ssh' in setup
    assert 'vm_network_wait_for_ip' in check
    assert 'vm_network_wait_for_ssh' in check


def test_issue_vm_timeout_env_names_are_standardized() -> None:
    setup = (ROOT / 'scripts' / 'setup_issue_vm.sh').read_text(encoding='utf-8')
    check = (ROOT / 'scripts' / 'check_issue_vm.sh').read_text(encoding='utf-8')

    for text in (setup, check):
        assert 'VM_WAIT_IP_TIMEOUT_SECONDS' in text
        assert 'VM_WAIT_SSH_TIMEOUT_SECONDS' in text
        assert 'VM_IP_WAIT_TIMEOUT_SECONDS' not in text
        assert 'VM_SSH_WAIT_TIMEOUT_SECONDS' not in text


def test_timeout_env_names_are_documented_consistently() -> None:
    readme = (ROOT / 'README.md').read_text(encoding='utf-8')
    operations = (ROOT / 'docs' / 'operations.md').read_text(encoding='utf-8')

    for text in (readme, operations):
        assert 'VM_WAIT_IP_TIMEOUT_SECONDS' in text
        assert 'VM_WAIT_SSH_TIMEOUT_SECONDS' in text
        assert 'default `180`' in text


def test_issue_vm_helpers_auto_detect_repo_name_when_omitted() -> None:
    tail_script = (ROOT / 'scripts' / 'tail_issue_vm_log.sh').read_text(encoding='utf-8')
    check_script = (ROOT / 'scripts' / 'check_issue_vm.sh').read_text(encoding='utf-8')

    for text in (tail_script, check_script):
        assert 'Repo basename used by setup_issue_vm.sh. Default: auto-detect' in text
        assert 'sort -u' in text
        assert 'agent-*.pid' in text
        assert 'agent-*.log' in text
        assert 'codex-heartbeat-run.log' in text
        assert 'codex-heartbeat.pid' in text

    assert 'Number of log lines to print before following. Default: 10' in tail_script
    assert 'Multiple guest agent logs found under' in tail_script
    assert 'pass an explicit repo-name' in check_script


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    os.chmod(path, 0o755)


def test_vm_network_wait_for_ssh_refreshes_options_each_retry(tmp_path: Path) -> None:
    sandbox_root = tmp_path / "sandbox"
    scripts_dir = sandbox_root / "scripts"
    lib_dir = scripts_dir / "lib"
    fake_bin = tmp_path / "bin"
    scripts_dir.mkdir(parents=True)
    lib_dir.mkdir(parents=True)
    fake_bin.mkdir()

    (lib_dir / "vm_network.sh").write_text((ROOT / "scripts" / "lib" / "vm_network.sh").read_text(encoding="utf-8"), encoding="utf-8")
    _write_executable(
        lib_dir / "guest_ssh.sh",
        f"""#!/usr/bin/env bash
set -euo pipefail
guest_ssh_options() {{
  local _vm_name="$1"
  local _vm_ip="$2"
  local ssh_private_key_path="$3"
  local counter_file="{tmp_path}/guest_ssh_options.count"
  local count=0
  if [[ -f "${{counter_file}}" ]]; then
    count="$(cat "${{counter_file}}")"
  fi
  count=$((count + 1))
  printf '%s\\n' "${{count}}" > "${{counter_file}}"
  printf '%s\\n' -o "TestMarker=attempt-${{count}}" -i "${{ssh_private_key_path}}"
}}
""",
    )
    _write_executable(
        fake_bin / "ssh",
        f"""#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$*" >> "{tmp_path}/ssh.log"
if [[ "$*" == *"TestMarker=attempt-2"* ]]; then
  exit 0
fi
exit 1
""",
    )

    driver = scripts_dir / "driver.sh"
    _write_executable(
        driver,
        """#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/lib/vm_network.sh"
vm_network_wait_for_ssh "vm-1" "192.168.122.11" "agent" "/tmp/id_ed25519" 5
""",
    )

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    env["USER"] = env.get("USER", "root")

    result = subprocess.run(
        [str(driver)],
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )

    assert result.returncode == 0
    ssh_calls = (tmp_path / "ssh.log").read_text(encoding="utf-8").splitlines()
    assert any("TestMarker=attempt-1" in call for call in ssh_calls)
    assert any("TestMarker=attempt-2" in call for call in ssh_calls)


def test_guest_ssh_resolve_vm_ip_falls_back_after_sudo_failure(tmp_path: Path) -> None:
    sandbox_root = tmp_path / "sandbox"
    scripts_dir = sandbox_root / "scripts"
    lib_dir = scripts_dir / "lib"
    fake_bin = tmp_path / "bin"
    scripts_dir.mkdir(parents=True)
    lib_dir.mkdir()
    fake_bin.mkdir()

    (lib_dir / "guest_ssh.sh").write_text((ROOT / "scripts" / "lib" / "guest_ssh.sh").read_text(encoding="utf-8"), encoding="utf-8")
    _write_executable(
        fake_bin / "sudo",
        """#!/usr/bin/env bash
set -euo pipefail
exit 1
""",
    )
    _write_executable(
        fake_bin / "virsh",
        """#!/usr/bin/env bash
set -euo pipefail
cat <<'EOF'
 Name       MAC address          Protocol     Address
-------------------------------------------------------------------------------
 vnet0      52:54:00:12:34:56    ipv4         192.168.122.11/24
EOF
""",
    )

    driver = scripts_dir / "driver.sh"
    _write_executable(
        driver,
        """#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/lib/guest_ssh.sh"
guest_ssh_resolve_vm_ip "vm-1"
""",
    )

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"

    result = subprocess.run(
        [str(driver)],
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )

    assert result.returncode == 0
    assert result.stdout.strip() == "192.168.122.11"


def test_vm_network_resolve_ip_falls_back_after_sudo_failure(tmp_path: Path) -> None:
    sandbox_root = tmp_path / "sandbox"
    scripts_dir = sandbox_root / "scripts"
    lib_dir = scripts_dir / "lib"
    fake_bin = tmp_path / "bin"
    scripts_dir.mkdir(parents=True)
    lib_dir.mkdir()
    fake_bin.mkdir()

    (lib_dir / "guest_ssh.sh").write_text((ROOT / "scripts" / "lib" / "guest_ssh.sh").read_text(encoding="utf-8"), encoding="utf-8")
    (lib_dir / "vm_network.sh").write_text((ROOT / "scripts" / "lib" / "vm_network.sh").read_text(encoding="utf-8"), encoding="utf-8")
    _write_executable(
        fake_bin / "sudo",
        """#!/usr/bin/env bash
set -euo pipefail
exit 1
""",
    )
    _write_executable(
        fake_bin / "virsh",
        """#!/usr/bin/env bash
set -euo pipefail
if [[ "${1:-}" == "domifaddr" ]]; then
  cat <<'EOF'
 Name       MAC address          Protocol     Address
-------------------------------------------------------------------------------
 vnet0      52:54:00:12:34:56    ipv4         192.168.122.42/24
EOF
  exit 0
fi
exit 1
""",
    )

    driver = scripts_dir / "driver.sh"
    _write_executable(
        driver,
        """#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/lib/vm_network.sh"
vm_network_resolve_ip "vm-1"
""",
    )

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"

    result = subprocess.run(
        [str(driver)],
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )

    assert result.returncode == 0
    assert result.stdout.strip() == "192.168.122.42"
