from __future__ import annotations

import os
import subprocess
from pathlib import Path


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    os.chmod(path, 0o755)


def _shell_environment(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    private_key = tmp_path / "id_ed25519"
    private_key.write_text("PRIVATE\n", encoding="utf-8")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    # Keep these command-construction tests away from the host's libvirt, SSH
    # key discovery, and known_hosts. SSH policy has its own integration tests.
    _write_executable(fake_bin / "sudo", "#!/usr/bin/env bash\nexit 1\n")
    _write_executable(
        fake_bin / "ssh-keyscan",
        """#!/usr/bin/env bash
set -euo pipefail
printf '%s ssh-ed25519 TEST-ONLY-HOST-KEY\\n' "${*: -1}"
""",
    )
    _write_executable(
        fake_bin / "ssh-keygen",
        """#!/usr/bin/env bash
set -euo pipefail
[[ "${1:-}" == "-F" && "${3:-}" == "-f" ]]
[[ "${4:-}" == "${EVANDER_GUEST_KNOWN_HOSTS_FILE}" ]]
awk -v host="$2" '$1 == host { found = 1 } END { exit(found ? 0 : 1) }' "$4"
""",
    )
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    env["SSH_PRIVATE_KEY_PATH"] = str(private_key)
    env["EVANDER_GUEST_KNOWN_HOSTS_FILE"] = str(tmp_path / "known_hosts")
    env["USER"] = env.get("USER", "root")
    return fake_bin, env


def _assert_isolated_known_host(env: dict[str, str], vm_name: str = "vm-12305") -> None:
    assert Path(env["EVANDER_GUEST_KNOWN_HOSTS_FILE"]).read_text(encoding="utf-8") == (
        f"{vm_name} ssh-ed25519 TEST-ONLY-HOST-KEY\n"
    )


def test_tail_issue_vm_log_exits_cleanly_when_log_is_missing(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    script_path = root / "scripts" / "tail_issue_vm_log.sh"
    fake_bin, env = _shell_environment(tmp_path)

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
cat >&2 <<'EOF'
No guest agent state or pid files found under /home/agent/.evander
EOF
exit 0
""",
    )

    result = subprocess.run(
        [str(script_path), "12305"],
        text=True,
        capture_output=True,
        env=env,
        check=True,
        timeout=5,
    )

    assert result.stdout == ""
    assert "No guest agent state or pid files found under" in result.stderr
    _assert_isolated_known_host(env)


def test_tail_issue_vm_log_rejects_non_positive_line_count(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    script_path = root / "scripts" / "tail_issue_vm_log.sh"
    _, env = _shell_environment(tmp_path)

    result = subprocess.run(
        [str(script_path), "12305", "0"],
        text=True,
        capture_output=True,
        env=env,
        check=False,
        timeout=5,
    )

    assert result.returncode == 1
    assert result.stdout == ""
    assert "lines must be a positive integer: 0" in result.stderr
    assert not Path(env["EVANDER_GUEST_KNOWN_HOSTS_FILE"]).exists()


def test_tail_issue_vm_log_forwards_tail_line_count_to_guest_shell(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    script_path = root / "scripts" / "tail_issue_vm_log.sh"
    fake_bin, env = _shell_environment(tmp_path)

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
if [[ "${*: -1}" != *"REPO_NAME=AdminClient"* ]]; then
  echo "missing repo name in remote command: ${*: -1}" >&2
  exit 1
fi
if [[ "${*: -1}" != *"TAIL_LINES=25"* ]]; then
  echo "missing tail lines in remote command: ${*: -1}" >&2
  exit 1
fi
cat >/dev/null
exit 0
""",
    )

    result = subprocess.run(
        [str(script_path), "12305", "25", "AdminClient"],
        text=True,
        capture_output=True,
        env=env,
        check=True,
        timeout=5,
    )

    assert result.stdout == ""
    assert result.stderr == ""
    _assert_isolated_known_host(env)


def test_tail_issue_vm_log_accepts_explicit_vm_name(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    script_path = root / "scripts" / "tail_issue_vm_log.sh"
    fake_bin, env = _shell_environment(tmp_path)
    virsh_args_path = tmp_path / "virsh-args.txt"

    _write_executable(
        fake_bin / "virsh",
        """#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" > "${VIRSH_ARGS_PATH}"
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
if [[ "${*: -1}" != *"TAIL_LINES=25"* ]]; then
  echo "missing tail lines in remote command: ${*: -1}" >&2
  exit 1
fi
cat >/dev/null
exit 0
""",
    )

    env["VIRSH_ARGS_PATH"] = str(virsh_args_path)

    result = subprocess.run(
        [
            str(script_path),
            "--vm-name",
            "evander-receptionist-vm",
            "25",
        ],
        text=True,
        capture_output=True,
        env=env,
        check=True,
        timeout=5,
    )

    assert result.stdout == ""
    assert result.stderr == ""
    _assert_isolated_known_host(env, "evander-receptionist-vm")
    assert virsh_args_path.read_text(encoding="utf-8").strip() == (
        "domifaddr evander-receptionist-vm"
    )


def test_tail_issue_vm_log_auto_detects_codex_heartbeat_log(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    script_path = root / "scripts" / "tail_issue_vm_log.sh"
    fake_bin, env = _shell_environment(tmp_path)

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
cat >/dev/null
printf 'Repo: codex-heartbeat\\n'
printf 'Log: /var/log/evander/agent-runs/codex-heartbeat-run.log\\n'
""",
    )

    result = subprocess.run(
        [str(script_path), "12305"],
        text=True,
        capture_output=True,
        env=env,
        check=True,
        timeout=5,
    )

    assert "Repo: codex-heartbeat" in result.stdout
    assert "codex-heartbeat-run.log" in result.stdout
    assert result.stderr == ""
    _assert_isolated_known_host(env)
