from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    os.chmod(path, 0o755)


def _prepare_sandbox(tmp_path: Path) -> tuple[Path, Path, Path]:
    root = tmp_path / "sandbox"
    scripts_dir = root / "scripts"
    lib_dir = scripts_dir / "lib"
    guest_scripts_dir = scripts_dir / "guest"
    config_dir = root / "config"
    fake_bin = tmp_path / "bin"
    scripts_dir.mkdir(parents=True)
    lib_dir.mkdir(parents=True)
    guest_scripts_dir.mkdir(parents=True)
    config_dir.mkdir(parents=True)
    fake_bin.mkdir(parents=True)

    source_script = Path(__file__).resolve().parents[1] / "scripts" / "setup_issue_vm.sh"
    source_bootstrap_codex = Path(__file__).resolve().parents[1] / "scripts" / "bootstrap_codex_vm.sh"
    setup_script = scripts_dir / "setup_issue_vm.sh"
    setup_script.write_text(source_script.read_text(encoding="utf-8"), encoding="utf-8")
    os.chmod(setup_script, 0o755)
    bootstrap_codex_script = scripts_dir / "bootstrap_codex_vm.sh"
    bootstrap_codex_script.write_text(source_bootstrap_codex.read_text(encoding="utf-8"), encoding="utf-8")
    os.chmod(bootstrap_codex_script, 0o755)

    source_agent_pr_note = (
        Path(__file__).resolve().parents[1] / "scripts" / "guest" / "agent_pr_note.sh"
    )
    target_agent_pr_note = guest_scripts_dir / "agent_pr_note.sh"
    target_agent_pr_note.write_text(source_agent_pr_note.read_text(encoding="utf-8"), encoding="utf-8")
    os.chmod(target_agent_pr_note, 0o755)

    source_runner_lib = Path(__file__).resolve().parents[1] / "scripts" / "lib" / "agent_runner.sh"
    source_provider_assets_lib = (
        Path(__file__).resolve().parents[1] / "scripts" / "lib" / "agent_provider_assets.sh"
    )
    source_runner_codex_lib = (
        Path(__file__).resolve().parents[1] / "scripts" / "lib" / "agent_runner_codex.sh"
    )
    source_state_lib = Path(__file__).resolve().parents[1] / "scripts" / "lib" / "agent_state.sh"
    source_runtime_config_lib = Path(__file__).resolve().parents[1] / "scripts" / "lib" / "runtime_config.sh"
    (lib_dir / "agent_runner.sh").write_text(source_runner_lib.read_text(encoding="utf-8"), encoding="utf-8")
    (lib_dir / "agent_provider_assets.sh").write_text(
        source_provider_assets_lib.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (lib_dir / "agent_runner_codex.sh").write_text(
        source_runner_codex_lib.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (lib_dir / "agent_state.sh").write_text(
        source_state_lib.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (lib_dir / "runtime_config.sh").write_text(
        source_runtime_config_lib.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    os.chmod(lib_dir / "runtime_config.sh", 0o755)

    _write_executable(
        scripts_dir / "resolve_repo_source.sh",
        """#!/usr/bin/env bash
set -euo pipefail
if [[ "${1:-}" == "--print-env" ]]; then
  printf '%s\n' "${2:-}" > "${RESOLVE_INPUT_LOG:?}"
  cat <<'EOF'
RESOLVED_REPO_PATH=/tmp/fake-repo
RESOLVED_REPO_NAME=fake-repo
EOF
  exit 0
fi
printf '/tmp/fake-repo\n'
""",
    )

    _write_executable(
        fake_bin / "virsh",
        """#!/usr/bin/env bash
set -euo pipefail
# Simulate an already-existing VM so setup exits before any heavy operations.
if [[ "${1:-}" == "dominfo" ]]; then
  exit 0
fi
exit 1
""",
    )

    return root, setup_script, fake_bin


def _base_env(tmp_path: Path, fake_bin: Path) -> dict[str, str]:
    pub = tmp_path / "id_ed25519.pub"
    priv = tmp_path / "id_ed25519"
    pub.write_text("ssh-ed25519 AAAA test\n", encoding="utf-8")
    priv.write_text("PRIVATE\n", encoding="utf-8")

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    env["SSH_PUBLIC_KEY_FILE"] = str(pub)
    env["SSH_PRIVATE_KEY_PATH"] = str(priv)
    env["RESOLVE_INPUT_LOG"] = str(tmp_path / "resolve-input.log")
    env["USER"] = env.get("USER", "root")
    return env


def _run_setup(setup_script: Path, env: dict[str, str], prewarm_target: Path, *extra_args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(setup_script), "--nuget", "14084", str(prewarm_target), *extra_args],
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )


def test_setup_issue_vm_prefers_explicit_repo_source_argument(tmp_path: Path) -> None:
    root, setup_script, fake_bin = _prepare_sandbox(tmp_path)
    prewarm_target = tmp_path / "project.slnx"
    prewarm_target.write_text("", encoding="utf-8")

    git_root = tmp_path / "git-remotes"
    git_root.mkdir()

    env = _base_env(tmp_path, fake_bin)
    env["AGENT_SANDBOX_GIT_ROOT"] = str(git_root)
    env["AGENT_SANDBOX_DEFAULT_REPO_SOURCE"] = "/env/default/repo"

    result = _run_setup(setup_script, env, prewarm_target, "/explicit/repo")

    assert result.returncode == 1
    assert "VM already exists: vm-14084" in result.stderr
    assert Path(env["RESOLVE_INPUT_LOG"]).read_text(encoding="utf-8").strip() == "/explicit/repo"


def test_setup_issue_vm_uses_env_repo_source_when_argument_omitted(tmp_path: Path) -> None:
    _, setup_script, fake_bin = _prepare_sandbox(tmp_path)
    prewarm_target = tmp_path / "project.slnx"
    prewarm_target.write_text("", encoding="utf-8")

    git_root = tmp_path / "git-remotes"
    git_root.mkdir()

    env = _base_env(tmp_path, fake_bin)
    env["AGENT_SANDBOX_GIT_ROOT"] = str(git_root)
    env["AGENT_SANDBOX_DEFAULT_REPO_SOURCE"] = "/env/default/repo"

    result = _run_setup(setup_script, env, prewarm_target)

    assert result.returncode == 1
    assert "VM already exists: vm-14084" in result.stderr
    assert Path(env["RESOLVE_INPUT_LOG"]).read_text(encoding="utf-8").strip() == "/env/default/repo"


def test_setup_issue_vm_falls_back_to_repo_root_when_repo_source_unset(tmp_path: Path) -> None:
    root, setup_script, fake_bin = _prepare_sandbox(tmp_path)
    prewarm_target = tmp_path / "project.slnx"
    prewarm_target.write_text("", encoding="utf-8")

    git_root = tmp_path / "git-remotes"
    git_root.mkdir()

    env = _base_env(tmp_path, fake_bin)
    env["AGENT_SANDBOX_GIT_ROOT"] = str(git_root)
    env.pop("AGENT_SANDBOX_DEFAULT_REPO_SOURCE", None)

    result = _run_setup(setup_script, env, prewarm_target)

    assert result.returncode == 1
    assert "VM already exists: vm-14084" in result.stderr
    assert Path(env["RESOLVE_INPUT_LOG"]).read_text(encoding="utf-8").strip() == str(root)


def test_setup_issue_vm_requires_existing_git_remote_root(tmp_path: Path) -> None:
    root, setup_script, fake_bin = _prepare_sandbox(tmp_path)
    prewarm_target = tmp_path / "project.slnx"
    prewarm_target.write_text("", encoding="utf-8")

    configured_git_root = root / "custom-git-remotes"

    env = _base_env(tmp_path, fake_bin)
    env["AGENT_SANDBOX_GIT_ROOT"] = str(configured_git_root)

    result = _run_setup(setup_script, env, prewarm_target)

    assert result.returncode == 1
    assert f"Configured AGENT_SANDBOX_GIT_ROOT does not exist: {configured_git_root}" in result.stderr


def test_setup_issue_vm_reads_git_root_from_config_file(tmp_path: Path) -> None:
    root, setup_script, fake_bin = _prepare_sandbox(tmp_path)
    prewarm_target = tmp_path / "project.slnx"
    prewarm_target.write_text("", encoding="utf-8")

    configured_git_root = tmp_path / "from-config-git-root"
    configured_git_root.mkdir()
    (root / "runtime" / "directory-config" / "root").mkdir(parents=True, exist_ok=True)
    (root / "runtime" / "directory-config" / "root" / "issue-setup.json").write_text(
        json.dumps(
            {
                "git_root": str(configured_git_root),
                "git_host": "10.1.2.3",
                "git_user": "gitbot",
                "git_ssh_alias": "issue-git",
                "enable_guest_heartbeat": False,
                "guest_heartbeat_prompt": "continue",
            }
        ),
        encoding="utf-8",
    )

    env = _base_env(tmp_path, fake_bin)
    env.pop("AGENT_SANDBOX_GIT_ROOT", None)

    result = _run_setup(setup_script, env, prewarm_target)

    assert result.returncode == 1
    assert "VM already exists: vm-14084" in result.stderr


def test_setup_issue_vm_rejects_missing_provider_assets(tmp_path: Path) -> None:
    _, setup_script, fake_bin = _prepare_sandbox(tmp_path)
    prewarm_target = tmp_path / "project.slnx"
    prewarm_target.write_text("", encoding="utf-8")

    env = _base_env(tmp_path, fake_bin)
    env["AGENT_PROVIDER"] = "copilot"

    result = _run_setup(setup_script, env, prewarm_target)

    assert result.returncode == 1
    assert "Unsupported AGENT_PROVIDER 'copilot': missing runner adapter" in result.stderr


def test_setup_issue_vm_default_session_id_includes_issue_number() -> None:
    setup_issue_vm = (Path(__file__).resolve().parents[1] / "scripts" / "setup_issue_vm.sh").read_text(
        encoding="utf-8"
    )

    assert "default_session_id()" in setup_issue_vm
    assert "issue-%s-%s-%s" in setup_issue_vm
    assert 'SESSION_ID="$(default_session_id "${ISSUE_NUMBER}")"' in setup_issue_vm


def test_setup_issue_vm_prefers_sudo_virsh_probe_before_plain_virsh(tmp_path: Path) -> None:
    _, setup_script, fake_bin = _prepare_sandbox(tmp_path)
    prewarm_target = tmp_path / "project.slnx"
    prewarm_target.write_text("", encoding="utf-8")

    git_root = tmp_path / "git-remotes"
    git_root.mkdir()

    sudo_log = tmp_path / "sudo.log"
    virsh_log = tmp_path / "virsh.log"

    _write_executable(
        fake_bin / "sudo",
        f"""#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$*" >> "{sudo_log}"
if [[ "${{1:-}}" == "-n" ]]; then
  shift
fi
export FAKE_FROM_SUDO=1
exec "$@"
""",
    )

    _write_executable(
        fake_bin / "virsh",
        f"""#!/usr/bin/env bash
set -euo pipefail
printf '%s %s\\n' "${{FAKE_FROM_SUDO:-0}}" "$*" >> "{virsh_log}"
if [[ "${{1:-}}" == "dominfo" && "${{FAKE_FROM_SUDO:-0}}" == "1" ]]; then
  exit 0
fi
exit 97
""",
    )

    env = _base_env(tmp_path, fake_bin)
    env["AGENT_SANDBOX_GIT_ROOT"] = str(git_root)

    result = _run_setup(setup_script, env, prewarm_target)

    assert result.returncode == 1
    assert "VM already exists: vm-14084" in result.stderr
    assert sudo_log.read_text(encoding="utf-8").splitlines() == ["-n virsh dominfo vm-14084"]
    assert virsh_log.read_text(encoding="utf-8").splitlines() == ["1 dominfo vm-14084"]
