from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
RUNNER_SCRIPT = ROOT / "scripts" / "build_evander_runner_image.sh"
RUNNER_DOCKERFILE = ROOT / "docker" / "evander-runner" / "Dockerfile"
CLAUDE_RUNNER_SCRIPT = ROOT / "scripts" / "build_evander_claude_runner_image.sh"
CLAUDE_RUNNER_DOCKERFILE = ROOT / "docker" / "evander-runner-claude" / "Dockerfile"
HOST_CODEX_PACKAGE = "@openai/codex"
RUNNER_PLATFORM_PACKAGE = "@openai/codex-linux-x64"
RUNNER_TARGET_TRIPLE = "x86_64-unknown-linux-musl"


def _copy_runner_repo(tmp_path: Path) -> Path:
    repo_root = tmp_path / "runner-repo"
    script_path = repo_root / "scripts" / "build_evander_runner_image.sh"
    claude_script_path = repo_root / "scripts" / "build_evander_claude_runner_image.sh"
    dockerfile_path = repo_root / "docker" / "evander-runner" / "Dockerfile"
    claude_dockerfile_path = repo_root / "docker" / "evander-runner-claude" / "Dockerfile"

    script_path.parent.mkdir(parents=True, exist_ok=True)
    dockerfile_path.parent.mkdir(parents=True, exist_ok=True)
    claude_dockerfile_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(RUNNER_SCRIPT, script_path)
    shutil.copy2(CLAUDE_RUNNER_SCRIPT, claude_script_path)
    shutil.copy2(RUNNER_DOCKERFILE, dockerfile_path)
    shutil.copy2(CLAUDE_RUNNER_DOCKERFILE, claude_dockerfile_path)
    return repo_root


def _write_package_json(path: Path, *, name: str, version: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"name": name, "version": version}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _create_codex_fixture(
    tmp_path: Path,
    *,
    layout: str = "current",
    version: str = "1.0.0",
    binary_text: str = "#!/usr/bin/env bash\nexit 0\n",
) -> tuple[Path, Path, Path]:
    host_codex_root = tmp_path.joinpath(
        "host-codex", "node_modules", *HOST_CODEX_PACKAGE.split("/")
    )
    platform_root = tmp_path.joinpath(
        "host-codex", "node_modules", *RUNNER_PLATFORM_PACKAGE.split("/")
    )

    if layout == "current":
        binary_path = platform_root / "vendor" / RUNNER_TARGET_TRIPLE / "bin" / "codex"
    elif layout == "legacy":
        binary_path = platform_root / "vendor" / RUNNER_TARGET_TRIPLE / "codex" / "codex"
    else:
        raise ValueError(f"Unknown layout: {layout}")

    binary_path.parent.mkdir(parents=True, exist_ok=True)
    binary_path.write_text(binary_text, encoding="utf-8")
    binary_path.chmod(0o755)
    _write_package_json(host_codex_root / "package.json", name=HOST_CODEX_PACKAGE, version=version)
    _write_package_json(
        platform_root / "package.json", name=RUNNER_PLATFORM_PACKAGE, version=version
    )
    return host_codex_root, binary_path, platform_root


def _create_claude_fixture(
    tmp_path: Path,
    *,
    binary_text: str = "#!/usr/bin/env bash\nexit 0\n",
) -> Path:
    binary_path = tmp_path / "host-claude" / "bin" / "claude"
    binary_path.parent.mkdir(parents=True, exist_ok=True)
    binary_path.write_text(binary_text, encoding="utf-8")
    binary_path.chmod(0o755)
    return binary_path


def _write_fake_docker(tmp_path: Path) -> tuple[Path, Path]:
    docker_log_path = tmp_path / "docker.log"
    fake_docker_path = tmp_path / "docker"
    fake_docker_path.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "${1:-}" >> "${FAKE_DOCKER_LOG:?}"
if [[ -n "${FAKE_DOCKER_COMMAND_LOG:-}" ]]; then
  printf '%s\\n' "$*" >> "${FAKE_DOCKER_COMMAND_LOG}"
fi
case "${1:-}" in
  build)
    exit 0
    ;;
  save)
    archive_path=""
    while [[ "$#" -gt 0 ]]; do
      if [[ "$1" == "-o" ]]; then
        shift
        archive_path="$1"
      fi
      shift
    done
    mkdir -p "$(dirname "$archive_path")"
    if [[ "${FAKE_DOCKER_FAIL_SAVE_AFTER_WRITE:-}" == "1" ]]; then
      printf 'partial image\\n' > "$archive_path"
      exit 42
    fi
    printf 'fake image\\n' > "$archive_path"
    exit 0
    ;;
esac
exit 0
""",
        encoding="utf-8",
    )
    fake_docker_path.chmod(0o755)
    return fake_docker_path, docker_log_path


def _run_runner_script(
    script_path: Path,
    repo_root: Path,
    env: dict[str, str],
    *,
    args: list[str] | None = None,
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        [str(script_path), *(args or [])],
        cwd=repo_root,
        env={**os.environ, **env},
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return completed


def _run_runner_script_raw(
    script_path: Path,
    repo_root: Path,
    env: dict[str, str],
    *,
    args: list[str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(script_path), *(args or [])],
        cwd=repo_root,
        env={**os.environ, **env},
        text=True,
        capture_output=True,
        check=False,
    )


def _docker_invocations(log_path: Path) -> list[str]:
    if not log_path.exists():
        return []
    return [
        line.strip() for line in log_path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def test_runner_dockerfile_contains_minimal_runtime_contract() -> None:
    dockerfile = RUNNER_DOCKERFILE.read_text(encoding="utf-8")

    assert "FROM ubuntu:24.04" in dockerfile
    assert "COPY codex /usr/local/bin/codex" in dockerfile
    assert "bubblewrap" in dockerfile
    assert "ENV HOME=/home/agent" in dockerfile
    assert "WORKDIR /home/agent" in dockerfile
    assert "useradd --uid 1001" not in dockerfile
    assert "USER agent:agent" not in dockerfile
    assert "/home/agent/repos" in dockerfile
    assert "python3-pip" in dockerfile
    assert "python3-venv" in dockerfile
    assert "ripgrep" in dockerfile
    assert "openssh-client" in dockerfile
    assert "libgssapi-krb5-2" in dockerfile
    assert "libicu74" in dockerfile
    assert "libssl3t64" in dockerfile
    assert "libunwind8" in dockerfile
    assert "zlib1g" in dockerfile
    assert "libnss3" in dockerfile
    assert "libgtk-3-0" in dockerfile
    assert "/home/agent/.evander/session.env" in dockerfile
    assert "/opt/evander/toolchains/mssql-client/env.sh" in dockerfile


def test_claude_runner_dockerfile_contains_minimal_runtime_contract() -> None:
    dockerfile = CLAUDE_RUNNER_DOCKERFILE.read_text(encoding="utf-8")

    assert "FROM ubuntu:24.04" in dockerfile
    assert "COPY claude /usr/local/bin/claude" in dockerfile
    assert "COPY codex" not in dockerfile
    assert "ENV HOME=/home/agent" in dockerfile
    assert "WORKDIR /home/agent" in dockerfile
    assert "CODEX_HOME" not in dockerfile
    assert "/home/agent/repos" in dockerfile
    assert "/home/agent/.claude" in dockerfile
    assert "/home/agent/.evander/session.env" in dockerfile
    assert "/opt/evander/toolchains/mssql-client/env.sh" in dockerfile
    assert "/opt/evander/toolchains/node-v24.14.0/bin" in dockerfile
    assert "ANTHROPIC_AUTH_TOKEN" in dockerfile
    assert "DISABLE_TELEMETRY" in dockerfile
    assert "python3-pip" in dockerfile
    assert "python3-venv" in dockerfile
    assert "ripgrep" in dockerfile
    assert "openssh-client" in dockerfile
    assert "libnss3" in dockerfile
    assert "libgtk-3-0" in dockerfile
    assert 'CMD ["bash", "-lc", "claude --version"]' in dockerfile


def test_runner_build_script_builds_tag_and_saves_tarball() -> None:
    script = RUNNER_SCRIPT.read_text(encoding="utf-8")

    assert "HOST_CODEX_BINARY_PATH" in script
    assert "HOST_CODEX_PLATFORM_PACKAGE" in script
    assert "CODEX_BINARY_RELATIVE_PATH" in script
    assert "CODEX_LEGACY_BINARY_RELATIVE_PATH" in script
    assert "add_codex_binary_candidates" in script
    assert "HOISTED_CODEX_BINARY_ROOT" in script
    assert (
        'HOST_CODEX_NODE_MODULES="$(cd "${HOST_CODEX_ROOT}/../.." 2>/dev/null && pwd || true)"'
        in script
    )
    assert "NODE_RESOLVED_CODEX_BINARY_ROOT" in script
    assert "RUNNER_BUILD_NETWORK" in script
    assert "RUNNER_MANIFEST_PATH" in script
    assert "RUNNER_FORCE_REBUILD" in script
    assert "evander-runner:latest" in script
    assert "dist/evander-runner.tar" in script
    assert 'build_args=(build -t "${RUNNER_IMAGE_NAME}")' in script
    assert 'build_args+=(--network "${RUNNER_BUILD_NETWORK}")' in script
    assert "build_args+=(--pull)" in script
    assert (
        'RUNNER_ARCHIVE_TMP_PATH="$(mktemp "$(dirname "${RUNNER_ARCHIVE_PATH}")/.runner-archive.XXXXXX.tar")"'
        in script
    )
    assert '"${DOCKER}" save -o "${RUNNER_ARCHIVE_TMP_PATH}" "${RUNNER_IMAGE_NAME}"' in script
    assert 'mv -f "${RUNNER_ARCHIVE_TMP_PATH}" "${RUNNER_ARCHIVE_PATH}"' in script
    assert "Reused cached ${RUNNER_IMAGE_NAME} archive at ${RUNNER_ARCHIVE_PATH}" in script


def test_claude_runner_build_script_builds_tag_and_saves_tarball() -> None:
    script = CLAUDE_RUNNER_SCRIPT.read_text(encoding="utf-8")

    assert "HOST_CLAUDE_BIN" in script
    assert "command -v claude" in script
    assert "RUNNER_BUILD_NETWORK" in script
    assert "RUNNER_MANIFEST_PATH" in script
    assert "RUNNER_FORCE_REBUILD" in script
    assert "evander-runner-claude:latest" in script
    assert "dist/evander-runner-claude.tar" in script
    assert "claude_bin_realpath" in script
    assert "claude_bin_sha256" in script
    assert 'cp "${RESOLVED_CLAUDE_BIN}" "${BUILD_CONTEXT}/claude"' in script
    assert 'build_args=(build -t "${RUNNER_IMAGE_NAME}")' in script
    assert 'build_args+=(--network "${RUNNER_BUILD_NETWORK}")' in script
    assert "build_args+=(--pull)" in script
    assert (
        'RUNNER_ARCHIVE_TMP_PATH="$(mktemp "$(dirname "${RUNNER_ARCHIVE_PATH}")/.runner-archive.XXXXXX.tar")"'
        in script
    )
    assert '"${DOCKER}" save -o "${RUNNER_ARCHIVE_TMP_PATH}" "${RUNNER_IMAGE_NAME}"' in script
    assert 'mv -f "${RUNNER_ARCHIVE_TMP_PATH}" "${RUNNER_ARCHIVE_PATH}"' in script
    assert "Reused cached ${RUNNER_IMAGE_NAME} archive at ${RUNNER_ARCHIVE_PATH}" in script


def test_runner_build_script_writes_manifest_and_reuses_cached_archive(
    tmp_path: Path,
) -> None:
    repo_root = _copy_runner_repo(tmp_path)
    script_path = repo_root / "scripts" / "build_evander_runner_image.sh"
    archive_path = repo_root / "dist" / "evander-runner.tar"
    manifest_path = Path(f"{archive_path}.manifest")
    codex_root, _, _ = _create_codex_fixture(tmp_path, layout="current")
    fake_docker, docker_log_path = _write_fake_docker(tmp_path)

    env = {
        "DOCKER": str(fake_docker),
        "FAKE_DOCKER_LOG": str(docker_log_path),
        "HOST_CODEX_ROOT": str(codex_root),
        "HOST_CODEX_PLATFORM_PACKAGE": RUNNER_PLATFORM_PACKAGE,
        "CODEX_TARGET_TRIPLE": RUNNER_TARGET_TRIPLE,
    }

    first = _run_runner_script(script_path, repo_root, env)
    first_manifest = manifest_path.read_text(encoding="utf-8")

    assert archive_path.read_text(encoding="utf-8") == "fake image\n"
    assert manifest_path.is_file()
    assert first.stdout.splitlines() == [
        "Built evander-runner:latest",
        f"Saved {archive_path}",
    ]
    assert _docker_invocations(docker_log_path) == ["build", "save"]

    second = _run_runner_script(script_path, repo_root, env)

    assert second.stdout.strip() == f"Reused cached evander-runner:latest archive at {archive_path}"
    assert manifest_path.read_text(encoding="utf-8") == first_manifest
    assert _docker_invocations(docker_log_path) == ["build", "save"]


def test_claude_runner_build_script_writes_manifest_and_reuses_cached_archive(
    tmp_path: Path,
) -> None:
    repo_root = _copy_runner_repo(tmp_path)
    script_path = repo_root / "scripts" / "build_evander_claude_runner_image.sh"
    archive_path = repo_root / "dist" / "evander-runner-claude.tar"
    manifest_path = Path(f"{archive_path}.manifest")
    claude_bin = _create_claude_fixture(tmp_path)
    fake_docker, docker_log_path = _write_fake_docker(tmp_path)

    env = {
        "DOCKER": str(fake_docker),
        "FAKE_DOCKER_LOG": str(docker_log_path),
        "HOST_CLAUDE_BIN": str(claude_bin),
    }

    first = _run_runner_script(script_path, repo_root, env)
    first_manifest = manifest_path.read_text(encoding="utf-8")

    assert archive_path.read_text(encoding="utf-8") == "fake image\n"
    assert manifest_path.is_file()
    assert first.stdout.splitlines() == [
        "Built evander-runner-claude:latest",
        f"Saved {archive_path}",
    ]
    assert "claude_bin_sha256=" in first_manifest
    assert _docker_invocations(docker_log_path) == ["build", "save"]

    second = _run_runner_script(script_path, repo_root, env)

    assert (
        second.stdout.strip()
        == f"Reused cached evander-runner-claude:latest archive at {archive_path}"
    )
    assert manifest_path.read_text(encoding="utf-8") == first_manifest
    assert _docker_invocations(docker_log_path) == ["build", "save"]


def test_runner_build_script_resolves_hoisted_codex_binary_current_layout(
    tmp_path: Path,
) -> None:
    repo_root = _copy_runner_repo(tmp_path)
    script_path = repo_root / "scripts" / "build_evander_runner_image.sh"
    archive_path = repo_root / "dist" / "evander-runner.tar"
    codex_root, _, _ = _create_codex_fixture(tmp_path, layout="current")
    fake_docker, docker_log_path = _write_fake_docker(tmp_path)

    env = {
        "DOCKER": str(fake_docker),
        "FAKE_DOCKER_LOG": str(docker_log_path),
        "HOST_CODEX_ROOT": str(codex_root),
        "HOST_CODEX_PLATFORM_PACKAGE": RUNNER_PLATFORM_PACKAGE,
        "CODEX_TARGET_TRIPLE": RUNNER_TARGET_TRIPLE,
    }

    result = _run_runner_script(script_path, repo_root, env)

    assert result.returncode == 0
    assert archive_path.read_text(encoding="utf-8") == "fake image\n"
    assert _docker_invocations(docker_log_path) == ["build", "save"]


def test_runner_build_script_resolves_hoisted_codex_binary_legacy_layout(
    tmp_path: Path,
) -> None:
    repo_root = _copy_runner_repo(tmp_path)
    script_path = repo_root / "scripts" / "build_evander_runner_image.sh"
    archive_path = repo_root / "dist" / "evander-runner.tar"
    codex_root, _, _ = _create_codex_fixture(tmp_path, layout="legacy")
    fake_docker, docker_log_path = _write_fake_docker(tmp_path)

    env = {
        "DOCKER": str(fake_docker),
        "FAKE_DOCKER_LOG": str(docker_log_path),
        "HOST_CODEX_ROOT": str(codex_root),
        "HOST_CODEX_PLATFORM_PACKAGE": RUNNER_PLATFORM_PACKAGE,
        "CODEX_TARGET_TRIPLE": RUNNER_TARGET_TRIPLE,
    }

    result = _run_runner_script(script_path, repo_root, env)

    assert result.returncode == 0
    assert archive_path.read_text(encoding="utf-8") == "fake image\n"
    assert _docker_invocations(docker_log_path) == ["build", "save"]


def test_runner_build_script_rebuilds_when_copied_dockerfile_changes(
    tmp_path: Path,
) -> None:
    repo_root = _copy_runner_repo(tmp_path)
    script_path = repo_root / "scripts" / "build_evander_runner_image.sh"
    dockerfile_path = repo_root / "docker" / "evander-runner" / "Dockerfile"
    manifest_path = repo_root / "dist" / "evander-runner.tar.manifest"
    codex_root, _, _ = _create_codex_fixture(tmp_path, layout="current")
    fake_docker, docker_log_path = _write_fake_docker(tmp_path)

    env = {
        "DOCKER": str(fake_docker),
        "FAKE_DOCKER_LOG": str(docker_log_path),
        "HOST_CODEX_ROOT": str(codex_root),
        "HOST_CODEX_PLATFORM_PACKAGE": RUNNER_PLATFORM_PACKAGE,
        "CODEX_TARGET_TRIPLE": RUNNER_TARGET_TRIPLE,
    }

    _run_runner_script(script_path, repo_root, env)
    first_manifest = manifest_path.read_text(encoding="utf-8")

    dockerfile_path.write_text(
        dockerfile_path.read_text(encoding="utf-8") + "\n# cache-bust\n",
        encoding="utf-8",
    )

    _run_runner_script(script_path, repo_root, env)

    assert _docker_invocations(docker_log_path) == ["build", "save", "build", "save"]
    assert manifest_path.read_text(encoding="utf-8") != first_manifest


@pytest.mark.parametrize("mutation", ["package_json", "binary"])
def test_runner_build_script_rebuilds_when_codex_identity_changes(
    tmp_path: Path,
    mutation: str,
) -> None:
    repo_root = _copy_runner_repo(tmp_path)
    script_path = repo_root / "scripts" / "build_evander_runner_image.sh"
    manifest_path = repo_root / "dist" / "evander-runner.tar.manifest"
    codex_root, binary_path, platform_root = _create_codex_fixture(tmp_path, layout="current")
    fake_docker, docker_log_path = _write_fake_docker(tmp_path)

    env = {
        "DOCKER": str(fake_docker),
        "FAKE_DOCKER_LOG": str(docker_log_path),
        "HOST_CODEX_ROOT": str(codex_root),
        "HOST_CODEX_PLATFORM_PACKAGE": RUNNER_PLATFORM_PACKAGE,
        "CODEX_TARGET_TRIPLE": RUNNER_TARGET_TRIPLE,
    }

    _run_runner_script(script_path, repo_root, env)
    first_manifest = manifest_path.read_text(encoding="utf-8")

    if mutation == "package_json":
        _write_package_json(
            platform_root / "package.json",
            name=RUNNER_PLATFORM_PACKAGE,
            version="2.0.0",
        )
    else:
        binary_path.write_text(
            "#!/usr/bin/env bash\nprintf 'changed binary\\n'\n",
            encoding="utf-8",
        )
        binary_path.chmod(0o755)

    _run_runner_script(script_path, repo_root, env)

    assert _docker_invocations(docker_log_path) == ["build", "save", "build", "save"]
    assert manifest_path.read_text(encoding="utf-8") != first_manifest


@pytest.mark.parametrize("mutation", ["dockerfile", "binary"])
def test_claude_runner_build_script_rebuilds_when_identity_changes(
    tmp_path: Path,
    mutation: str,
) -> None:
    repo_root = _copy_runner_repo(tmp_path)
    script_path = repo_root / "scripts" / "build_evander_claude_runner_image.sh"
    dockerfile_path = repo_root / "docker" / "evander-runner-claude" / "Dockerfile"
    manifest_path = repo_root / "dist" / "evander-runner-claude.tar.manifest"
    claude_bin = _create_claude_fixture(tmp_path)
    fake_docker, docker_log_path = _write_fake_docker(tmp_path)

    env = {
        "DOCKER": str(fake_docker),
        "FAKE_DOCKER_LOG": str(docker_log_path),
        "HOST_CLAUDE_BIN": str(claude_bin),
    }

    _run_runner_script(script_path, repo_root, env)
    first_manifest = manifest_path.read_text(encoding="utf-8")

    if mutation == "dockerfile":
        dockerfile_path.write_text(
            dockerfile_path.read_text(encoding="utf-8") + "\n# cache-bust\n",
            encoding="utf-8",
        )
    else:
        claude_bin.write_text(
            "#!/usr/bin/env bash\nprintf 'changed claude\\n'\n",
            encoding="utf-8",
        )
        claude_bin.chmod(0o755)

    _run_runner_script(script_path, repo_root, env)

    assert _docker_invocations(docker_log_path) == ["build", "save", "build", "save"]
    assert manifest_path.read_text(encoding="utf-8") != first_manifest


def test_runner_build_script_rebuilds_when_archive_is_deleted(tmp_path: Path) -> None:
    repo_root = _copy_runner_repo(tmp_path)
    script_path = repo_root / "scripts" / "build_evander_runner_image.sh"
    archive_path = repo_root / "dist" / "evander-runner.tar"
    manifest_path = Path(f"{archive_path}.manifest")
    codex_root, _, _ = _create_codex_fixture(tmp_path, layout="current")
    fake_docker, docker_log_path = _write_fake_docker(tmp_path)

    env = {
        "DOCKER": str(fake_docker),
        "FAKE_DOCKER_LOG": str(docker_log_path),
        "HOST_CODEX_ROOT": str(codex_root),
        "HOST_CODEX_PLATFORM_PACKAGE": RUNNER_PLATFORM_PACKAGE,
        "CODEX_TARGET_TRIPLE": RUNNER_TARGET_TRIPLE,
    }

    _run_runner_script(script_path, repo_root, env)
    archive_path.unlink()
    assert manifest_path.exists()

    _run_runner_script(script_path, repo_root, env)

    assert archive_path.read_text(encoding="utf-8") == "fake image\n"
    assert _docker_invocations(docker_log_path) == ["build", "save", "build", "save"]


def test_runner_build_script_rebuilds_when_manifest_is_deleted(tmp_path: Path) -> None:
    repo_root = _copy_runner_repo(tmp_path)
    script_path = repo_root / "scripts" / "build_evander_runner_image.sh"
    archive_path = repo_root / "dist" / "evander-runner.tar"
    manifest_path = Path(f"{archive_path}.manifest")
    codex_root, _, _ = _create_codex_fixture(tmp_path, layout="current")
    fake_docker, docker_log_path = _write_fake_docker(tmp_path)

    env = {
        "DOCKER": str(fake_docker),
        "FAKE_DOCKER_LOG": str(docker_log_path),
        "HOST_CODEX_ROOT": str(codex_root),
        "HOST_CODEX_PLATFORM_PACKAGE": RUNNER_PLATFORM_PACKAGE,
        "CODEX_TARGET_TRIPLE": RUNNER_TARGET_TRIPLE,
    }

    _run_runner_script(script_path, repo_root, env)
    manifest_path.unlink()

    _run_runner_script(script_path, repo_root, env)

    assert archive_path.read_text(encoding="utf-8") == "fake image\n"
    assert manifest_path.exists()
    assert _docker_invocations(docker_log_path) == ["build", "save", "build", "save"]


@pytest.mark.parametrize(
    "force_mode",
    [
        "env",
        "flag",
    ],
)
def test_runner_build_script_forces_rebuild_even_with_matching_manifest(
    tmp_path: Path,
    force_mode: str,
) -> None:
    repo_root = _copy_runner_repo(tmp_path)
    script_path = repo_root / "scripts" / "build_evander_runner_image.sh"
    archive_path = repo_root / "dist" / "evander-runner.tar"
    manifest_path = Path(f"{archive_path}.manifest")
    codex_root, _, _ = _create_codex_fixture(tmp_path, layout="current")
    fake_docker, docker_log_path = _write_fake_docker(tmp_path)

    env = {
        "DOCKER": str(fake_docker),
        "FAKE_DOCKER_LOG": str(docker_log_path),
        "HOST_CODEX_ROOT": str(codex_root),
        "HOST_CODEX_PLATFORM_PACKAGE": RUNNER_PLATFORM_PACKAGE,
        "CODEX_TARGET_TRIPLE": RUNNER_TARGET_TRIPLE,
    }

    _run_runner_script(script_path, repo_root, env)
    first_manifest = manifest_path.read_text(encoding="utf-8")

    force_env = dict(env)
    force_args: list[str] = []
    if force_mode == "env":
        force_env["RUNNER_FORCE_REBUILD"] = "1"
    else:
        force_args = ["--force"]

    result = _run_runner_script(script_path, repo_root, force_env, args=force_args)

    assert "Reused cached" not in result.stdout
    assert archive_path.read_text(encoding="utf-8") == "fake image\n"
    assert manifest_path.read_text(encoding="utf-8") == first_manifest
    assert _docker_invocations(docker_log_path) == ["build", "save", "build", "save"]


def test_runner_build_script_pulls_base_image_on_forced_rebuild(
    tmp_path: Path,
) -> None:
    repo_root = _copy_runner_repo(tmp_path)
    script_path = repo_root / "scripts" / "build_evander_runner_image.sh"
    codex_root, _, _ = _create_codex_fixture(tmp_path, layout="current")
    fake_docker, docker_log_path = _write_fake_docker(tmp_path)
    docker_command_log_path = tmp_path / "docker-command.log"

    env = {
        "DOCKER": str(fake_docker),
        "FAKE_DOCKER_LOG": str(docker_log_path),
        "FAKE_DOCKER_COMMAND_LOG": str(docker_command_log_path),
        "HOST_CODEX_ROOT": str(codex_root),
        "HOST_CODEX_PLATFORM_PACKAGE": RUNNER_PLATFORM_PACKAGE,
        "CODEX_TARGET_TRIPLE": RUNNER_TARGET_TRIPLE,
    }

    _run_runner_script(script_path, repo_root, env)
    _run_runner_script(script_path, repo_root, env, args=["--force"])

    build_commands = [
        line
        for line in docker_command_log_path.read_text(encoding="utf-8").splitlines()
        if line.startswith("build ")
    ]
    assert len(build_commands) == 2
    assert " --pull " not in f" {build_commands[0]} "
    assert " --pull " in f" {build_commands[1]} "


def test_claude_runner_build_script_pulls_base_image_on_forced_rebuild(
    tmp_path: Path,
) -> None:
    repo_root = _copy_runner_repo(tmp_path)
    script_path = repo_root / "scripts" / "build_evander_claude_runner_image.sh"
    claude_bin = _create_claude_fixture(tmp_path)
    fake_docker, docker_log_path = _write_fake_docker(tmp_path)
    docker_command_log_path = tmp_path / "docker-command.log"

    env = {
        "DOCKER": str(fake_docker),
        "FAKE_DOCKER_LOG": str(docker_log_path),
        "FAKE_DOCKER_COMMAND_LOG": str(docker_command_log_path),
        "HOST_CLAUDE_BIN": str(claude_bin),
    }

    _run_runner_script(script_path, repo_root, env)
    _run_runner_script(script_path, repo_root, env, args=["--force"])

    build_commands = [
        line
        for line in docker_command_log_path.read_text(encoding="utf-8").splitlines()
        if line.startswith("build ")
    ]
    assert len(build_commands) == 2
    assert " --pull " not in f" {build_commands[0]} "
    assert " --pull " in f" {build_commands[1]} "


def test_runner_build_script_preserves_cache_after_failed_save(
    tmp_path: Path,
) -> None:
    repo_root = _copy_runner_repo(tmp_path)
    script_path = repo_root / "scripts" / "build_evander_runner_image.sh"
    archive_path = repo_root / "dist" / "evander-runner.tar"
    manifest_path = Path(f"{archive_path}.manifest")
    codex_root, _, _ = _create_codex_fixture(tmp_path, layout="current")
    fake_docker, docker_log_path = _write_fake_docker(tmp_path)

    env = {
        "DOCKER": str(fake_docker),
        "FAKE_DOCKER_LOG": str(docker_log_path),
        "HOST_CODEX_ROOT": str(codex_root),
        "HOST_CODEX_PLATFORM_PACKAGE": RUNNER_PLATFORM_PACKAGE,
        "CODEX_TARGET_TRIPLE": RUNNER_TARGET_TRIPLE,
    }

    _run_runner_script(script_path, repo_root, env)
    first_manifest = manifest_path.read_text(encoding="utf-8")

    failed = _run_runner_script_raw(
        script_path,
        repo_root,
        {**env, "FAKE_DOCKER_FAIL_SAVE_AFTER_WRITE": "1"},
        args=["--force"],
    )

    assert failed.returncode == 42
    assert archive_path.read_text(encoding="utf-8") == "fake image\n"
    assert manifest_path.read_text(encoding="utf-8") == first_manifest
    assert list(archive_path.parent.glob(".runner-archive.*.tar")) == []

    reused = _run_runner_script(script_path, repo_root, env)

    assert reused.stdout.strip() == f"Reused cached evander-runner:latest archive at {archive_path}"
    assert archive_path.read_text(encoding="utf-8") == "fake image\n"
    assert _docker_invocations(docker_log_path) == ["build", "save", "build", "save"]


def test_claude_runner_build_script_preserves_cache_after_failed_save(
    tmp_path: Path,
) -> None:
    repo_root = _copy_runner_repo(tmp_path)
    script_path = repo_root / "scripts" / "build_evander_claude_runner_image.sh"
    archive_path = repo_root / "dist" / "evander-runner-claude.tar"
    manifest_path = Path(f"{archive_path}.manifest")
    claude_bin = _create_claude_fixture(tmp_path)
    fake_docker, docker_log_path = _write_fake_docker(tmp_path)

    env = {
        "DOCKER": str(fake_docker),
        "FAKE_DOCKER_LOG": str(docker_log_path),
        "HOST_CLAUDE_BIN": str(claude_bin),
    }

    _run_runner_script(script_path, repo_root, env)
    first_manifest = manifest_path.read_text(encoding="utf-8")

    failed = _run_runner_script_raw(
        script_path,
        repo_root,
        {**env, "FAKE_DOCKER_FAIL_SAVE_AFTER_WRITE": "1"},
        args=["--force"],
    )

    assert failed.returncode == 42
    assert archive_path.read_text(encoding="utf-8") == "fake image\n"
    assert manifest_path.read_text(encoding="utf-8") == first_manifest
    assert list(archive_path.parent.glob(".runner-archive.*.tar")) == []

    reused = _run_runner_script(script_path, repo_root, env)

    assert (
        reused.stdout.strip()
        == f"Reused cached evander-runner-claude:latest archive at {archive_path}"
    )
    assert archive_path.read_text(encoding="utf-8") == "fake image\n"
    assert _docker_invocations(docker_log_path) == ["build", "save", "build", "save"]
