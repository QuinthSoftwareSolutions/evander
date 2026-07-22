from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest


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


def _make_fake_ssh_execute_guest_script(fake_bin: Path) -> None:
    (fake_bin / "ssh").write_text(
        """#!/usr/bin/env bash
set -euo pipefail
guest_command="${!#}"
HOME="${EVANDER_TEST_GUEST_HOME}" bash -c "${guest_command}"
""",
        encoding="utf-8",
    )
    os.chmod(fake_bin / "ssh", 0o755)


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
        "EVANDER_GIT_REPO": "readonly",
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
    assert "EVANDER_GIT_REFRESH_REQUIRED=1" in updated_authorized_keys
    assert "keep-me" in updated_authorized_keys


def test_bootstrap_git_vm_removes_legacy_guest_key_and_config_block(
    tmp_path: Path,
) -> None:
    _, bootstrap_script = _copy_bootstrap_sandbox(tmp_path)
    host_home = tmp_path / "host-home"
    (host_home / ".ssh").mkdir(parents=True)
    guest_home = tmp_path / "guest-home"
    guest_ssh = guest_home / ".ssh"
    guest_ssh.mkdir(parents=True)
    legacy_key = guest_ssh / "evander-git"
    subprocess.run(
        ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(legacy_key)],
        check=True,
        text=True,
        capture_output=True,
    )
    (guest_ssh / "config").write_text(
        "\n".join(
            [
                "# BEGIN evander git host",
                "Host evander-git-host",
                "  IdentityFile ~/.ssh/evander-git",
                "# END evander git host",
                "",
            ]
        ),
        encoding="utf-8",
    )
    fake_bin = _write_fake_bin(tmp_path)
    _make_fake_ssh_execute_guest_script(fake_bin)
    env = _bootstrap_env(
        tmp_path,
        host_home,
        "ssh-ed25519 unused",
        fake_bin,
    )
    env.update(
        {
            "EVANDER_TEST_GUEST_HOME": str(guest_home),
            "EVANDER_GIT_KEY_NAME": "evander-git-repository",
            "EVANDER_GIT_REMOVE_LEGACY_KEY": "1",
        }
    )

    result = subprocess.run(
        [str(bootstrap_script), "vm-test"],
        check=False,
        text=True,
        capture_output=True,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    assert not legacy_key.exists()
    assert not legacy_key.with_suffix(".pub").exists()
    assert (guest_ssh / "evander-git-repository").is_file()
    config_text = (guest_ssh / "config").read_text(encoding="utf-8")
    assert "# BEGIN evander git host" not in config_text
    assert "IdentityFile ~/.ssh/evander-git\n" not in config_text
    assert "# BEGIN evander git grant evander-git-repository" in config_text


def test_bootstrap_git_vm_does_not_embed_configured_secrets_env_file(tmp_path: Path) -> None:
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
    authorized_keys = ssh_dir / "authorized_keys"
    fake_bin = _write_fake_bin(tmp_path)
    secrets_env_file = tmp_path / "local-secrets.env"
    env = _bootstrap_env(tmp_path, home, public_key, fake_bin)
    env["EVANDER_SECRETS_ENV_FILE"] = str(secrets_env_file)

    result = subprocess.run(
        [str(bootstrap_script), "vm-test"],
        check=False,
        text=True,
        capture_output=True,
        env=env,
    )

    updated_authorized_keys = authorized_keys.read_text(encoding="utf-8")
    assert result.returncode == 0, result.stderr
    assert "EVANDER_SECRETS_ENV_FILE" not in updated_authorized_keys
    assert str(secrets_env_file) not in updated_authorized_keys


def test_bootstrap_git_vm_preserves_authorized_keys_group_during_atomic_replace(
    tmp_path: Path,
) -> None:
    alternate_groups = [gid for gid in os.getgroups() if gid != os.getgid()]
    if not alternate_groups:
        pytest.skip("requires a supplementary group to exercise ownership preservation")

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
    authorized_keys = ssh_dir / "authorized_keys"
    authorized_keys.write_text(
        f'from="192.168.250.0/24",command="old" {public_key}\n',
        encoding="utf-8",
    )
    try:
        os.chown(authorized_keys, os.getuid(), alternate_groups[0])
    except PermissionError:
        pytest.skip("current user cannot change file group")
    original_stat = authorized_keys.stat()
    fake_bin = _write_fake_bin(tmp_path)

    result = subprocess.run(
        [str(bootstrap_script), "vm-test"],
        check=False,
        text=True,
        capture_output=True,
        env=_bootstrap_env(tmp_path, home, public_key, fake_bin),
    )

    assert result.returncode == 0, result.stderr
    updated_stat = authorized_keys.stat()
    assert (updated_stat.st_uid, updated_stat.st_gid) == (
        original_stat.st_uid,
        original_stat.st_gid,
    )


def test_bootstrap_git_vm_rejects_read_only_key_without_repo_scope(tmp_path: Path) -> None:
    _, bootstrap_script = _copy_bootstrap_sandbox(tmp_path)
    home = tmp_path / "home"
    key_path = tmp_path / "guest-key"
    subprocess.run(
        ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key_path)],
        check=True,
        text=True,
        capture_output=True,
    )
    public_key = key_path.with_suffix(".pub").read_text(encoding="utf-8").strip()
    fake_bin = _write_fake_bin(tmp_path)
    env = _bootstrap_env(tmp_path, home, public_key, fake_bin)
    env.pop("EVANDER_GIT_REPO")

    result = subprocess.run(
        [str(bootstrap_script), "vm-test"],
        check=False,
        text=True,
        capture_output=True,
        env=env,
    )

    assert result.returncode != 0
    assert "without EVANDER_GIT_REPO metadata" in result.stderr
    assert not (home / ".ssh" / "authorized_keys").exists()
