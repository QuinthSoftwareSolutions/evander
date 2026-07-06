from __future__ import annotations

import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _copy_bootstrap_sandbox(tmp_path: Path) -> tuple[Path, Path]:
    sandbox_root = tmp_path / "sandbox"
    scripts_dir = sandbox_root / "scripts"
    lib_dir = scripts_dir / "lib"
    scripts_dir.mkdir(parents=True)
    lib_dir.mkdir(parents=True)
    (scripts_dir / "bootstrap_git_vm.sh").write_text(
        (ROOT / "scripts" / "bootstrap_git_vm.sh").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    os.chmod(scripts_dir / "bootstrap_git_vm.sh", 0o755)
    (scripts_dir / "git_ssh_command_wrapper.sh").write_text(
        "#!/usr/bin/env bash\nexit 0\n",
        encoding="utf-8",
    )
    os.chmod(scripts_dir / "git_ssh_command_wrapper.sh", 0o755)
    (lib_dir / "runtime_config.sh").write_text(
        "load_issue_setup_config() { :; }\n",
        encoding="utf-8",
    )
    (lib_dir / "guest_ssh.sh").write_text(
        """#!/usr/bin/env bash
guest_ssh_resolve_vm_ip() {
  printf '%s\\n' "${EVANDER_VM_IP:-192.0.2.10}"
}

guest_ssh_options() {
  printf '%s\\n' -F none
}
""",
        encoding="utf-8",
    )
    return sandbox_root, scripts_dir / "bootstrap_git_vm.sh"


def _write_fake_bin(tmp_path: Path) -> Path:
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    (fake_bin / "getent").write_text(
        """#!/usr/bin/env bash
set -euo pipefail
if [[ "${1:-}" == "passwd" ]]; then
  printf 'test-user:x:1000:1000::%s:/bin/bash\\n' "${EVANDER_TEST_HOME}"
  exit 0
fi
exit 1
""",
        encoding="utf-8",
    )
    os.chmod(fake_bin / "getent", 0o755)
    (fake_bin / "ssh").write_text(
        """#!/usr/bin/env bash
set -euo pipefail
cat >/dev/null
printf 'PUBLIC_KEY=%s\\n' "${EVANDER_TEST_PUBLIC_KEY}"
printf 'SESSION_ID=session-1\\n'
""",
        encoding="utf-8",
    )
    os.chmod(fake_bin / "ssh", 0o755)
    (fake_bin / "ssh-keyscan").write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    os.chmod(fake_bin / "ssh-keyscan", 0o755)
    return fake_bin


def _bootstrap_env(tmp_path: Path, home: Path, public_key: str, fake_bin: Path) -> dict[str, str]:
    private_key = tmp_path / "host-key"
    private_key.write_text("not used by fake ssh\n", encoding="utf-8")
    os.chmod(private_key, 0o600)
    return {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "USER": "test-user",
        "EVANDER_TEST_HOME": str(home),
        "EVANDER_TEST_PUBLIC_KEY": public_key,
        "SSH_KEY_PATH": str(private_key),
        "EVANDER_GIT_READ_ONLY": "1",
        "EVANDER_VM_IP": "192.0.2.10",
    }


def test_bootstrap_git_vm_rejects_malformed_guest_public_key_without_rewriting_authorized_keys(
    tmp_path: Path,
) -> None:
    _, bootstrap_script = _copy_bootstrap_sandbox(tmp_path)
    home = tmp_path / "home"
    ssh_dir = home / ".ssh"
    ssh_dir.mkdir(parents=True)
    authorized_keys = ssh_dir / "authorized_keys"
    original_authorized_keys = (
        "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFexistingexistingexistingexistingexisting old-key\n"
        "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQCvalidish other-key\n"
    )
    authorized_keys.write_text(original_authorized_keys, encoding="utf-8")
    fake_bin = _write_fake_bin(tmp_path)

    result = subprocess.run(
        [str(bootstrap_script), "vm-test"],
        check=False,
        text=True,
        capture_output=True,
        env=_bootstrap_env(tmp_path, home, "ssh-ed25519", fake_bin),
    )

    assert result.returncode != 0
    assert "missing key material" in result.stderr
    assert authorized_keys.read_text(encoding="utf-8") == original_authorized_keys


def test_bootstrap_git_vm_atomically_replaces_existing_valid_guest_public_key_entry(
    tmp_path: Path,
) -> None:
    _, bootstrap_script = _copy_bootstrap_sandbox(tmp_path)
    home = tmp_path / "home"
    ssh_dir = home / ".ssh"
    ssh_dir.mkdir(parents=True)
    key_path = tmp_path / "guest-key"
    subprocess.run(
        ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key_path)],
        check=True,
        text=True,
        capture_output=True,
    )
    public_key = key_path.with_suffix(".pub").read_text(encoding="utf-8").strip()
    public_key_body = public_key.split()[1]
    authorized_keys = ssh_dir / "authorized_keys"
    authorized_keys.write_text(
        f'from="192.168.250.0/24",command="old" {public_key}\n'
        "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFexistingexistingexistingexistingexisting keep-me\n",
        encoding="utf-8",
    )
    fake_bin = _write_fake_bin(tmp_path)

    result = subprocess.run(
        [str(bootstrap_script), "vm-test"],
        check=False,
        text=True,
        capture_output=True,
        env=_bootstrap_env(tmp_path, home, public_key, fake_bin),
    )

    assert result.returncode == 0, result.stderr
    updated_authorized_keys = authorized_keys.read_text(encoding="utf-8")
    assert 'command="old"' not in updated_authorized_keys
    assert updated_authorized_keys.count(public_key_body) == 1
    assert "command=" in updated_authorized_keys
    assert "keep-me" in updated_authorized_keys
