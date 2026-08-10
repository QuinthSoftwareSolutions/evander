from __future__ import annotations

import os
import subprocess
from pathlib import Path


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    os.chmod(path, 0o755)


def test_tail_issue_vm_log_exits_cleanly_when_log_is_missing(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    script_path = root / "scripts" / "tail_issue_vm_log.sh"
    private_key = tmp_path / "id_ed25519"
    private_key.write_text("PRIVATE\n", encoding="utf-8")

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()

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

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    env["SSH_PRIVATE_KEY_PATH"] = str(private_key)
    env["USER"] = env.get("USER", "root")

    result = subprocess.run(
        [str(script_path), "12305"],
        text=True,
        capture_output=True,
        env=env,
        check=True,
    )

    assert result.stdout == ""
    assert "No guest agent state or pid files found under" in result.stderr


def test_tail_issue_vm_log_rejects_non_positive_line_count(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    script_path = root / "scripts" / "tail_issue_vm_log.sh"
    private_key = tmp_path / "id_ed25519"
    private_key.write_text("PRIVATE\n", encoding="utf-8")

    env = os.environ.copy()
    env["SSH_PRIVATE_KEY_PATH"] = str(private_key)
    env["USER"] = env.get("USER", "root")

    result = subprocess.run(
        [str(script_path), "12305", "0"],
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )

    assert result.returncode == 1
    assert result.stdout == ""
    assert "lines must be a positive integer: 0" in result.stderr


def test_tail_issue_vm_log_forwards_tail_line_count_to_guest_shell(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    script_path = root / "scripts" / "tail_issue_vm_log.sh"
    private_key = tmp_path / "id_ed25519"
    private_key.write_text("PRIVATE\n", encoding="utf-8")

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()

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

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    env["SSH_PRIVATE_KEY_PATH"] = str(private_key)
    env["USER"] = env.get("USER", "root")

    result = subprocess.run(
        [str(script_path), "12305", "25", "AdminClient"],
        text=True,
        capture_output=True,
        env=env,
        check=True,
    )

    assert result.stdout == ""
    assert result.stderr == ""


def test_tail_issue_vm_log_accepts_explicit_vm_name(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    script_path = root / "scripts" / "tail_issue_vm_log.sh"
    private_key = tmp_path / "id_ed25519"
    private_key.write_text("PRIVATE\n", encoding="utf-8")
    virsh_args_path = tmp_path / "virsh-args.txt"

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()

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

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    env["SSH_PRIVATE_KEY_PATH"] = str(private_key)
    env["VIRSH_ARGS_PATH"] = str(virsh_args_path)
    env["USER"] = env.get("USER", "root")

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
    )

    assert result.stdout == ""
    assert result.stderr == ""
    assert virsh_args_path.read_text(encoding="utf-8").strip() == (
        "domifaddr evander-receptionist-vm"
    )


def test_tail_issue_vm_log_auto_detects_codex_heartbeat_log(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    script_path = root / "scripts" / "tail_issue_vm_log.sh"
    private_key = tmp_path / "id_ed25519"
    private_key.write_text("PRIVATE\n", encoding="utf-8")

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()

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

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    env["SSH_PRIVATE_KEY_PATH"] = str(private_key)
    env["USER"] = env.get("USER", "root")

    result = subprocess.run(
        [str(script_path), "12305"],
        text=True,
        capture_output=True,
        env=env,
        check=True,
    )

    assert "Repo: codex-heartbeat" in result.stdout
    assert "codex-heartbeat-run.log" in result.stdout
    assert result.stderr == ""
