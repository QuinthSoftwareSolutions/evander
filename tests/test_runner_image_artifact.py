from __future__ import annotations

import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_runner_dockerfile_contains_minimal_runtime_contract() -> None:
    dockerfile = (ROOT / "docker" / "agent-sandbox-runner" / "Dockerfile").read_text(
        encoding="utf-8"
    )

    assert "FROM ubuntu:24.04" in dockerfile
    assert "COPY codex /usr/local/bin/codex" in dockerfile
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
    assert "/home/agent/.agent-sandbox/session.env" in dockerfile


def test_runner_build_script_builds_tag_and_saves_tarball() -> None:
    script = (ROOT / "scripts" / "build_agent_sandbox_runner_image.sh").read_text(
        encoding="utf-8"
    )

    assert "HOST_CODEX_BINARY_PATH" in script
    assert "HOST_CODEX_PLATFORM_PACKAGE" in script
    assert "CODEX_BINARY_RELATIVE_PATH" in script
    assert "CODEX_LEGACY_BINARY_RELATIVE_PATH" in script
    assert "add_codex_binary_candidates" in script
    assert "HOISTED_CODEX_BINARY_ROOT" in script
    assert 'HOST_CODEX_NODE_MODULES="$(cd "${HOST_CODEX_ROOT}/../.."' in script
    assert "NODE_RESOLVED_CODEX_BINARY_ROOT" in script
    assert "RUNNER_BUILD_NETWORK" in script
    assert "agent-sandbox-runner:latest" in script
    assert "dist/agent-sandbox-runner.tar" in script
    assert 'build_args=(build -t "${RUNNER_IMAGE_NAME}")' in script
    assert 'build_args+=(--network "${RUNNER_BUILD_NETWORK}")' in script
    assert '"${DOCKER}" save -o "${RUNNER_ARCHIVE_PATH}" "${RUNNER_IMAGE_NAME}"' in script


def test_runner_build_script_resolves_hoisted_codex_binary_current_layout(
    tmp_path: Path,
) -> None:
    codex_root = tmp_path / "host-codex" / "node_modules" / "@openai" / "codex"
    hoisted_binary = (
        tmp_path
        / "host-codex"
        / "node_modules"
        / "@openai"
        / "codex-linux-x64"
        / "vendor"
        / "x86_64-unknown-linux-musl"
        / "bin"
        / "codex"
    )
    archive_path = tmp_path / "dist" / "agent-sandbox-runner.tar"
    fake_docker = tmp_path / "docker"

    codex_root.mkdir(parents=True)
    hoisted_binary.parent.mkdir(parents=True)
    hoisted_binary.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    hoisted_binary.chmod(0o755)

    fake_docker.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
if [[ "${1:-}" == "save" ]]; then
  while [[ "$#" -gt 0 ]]; do
    if [[ "$1" == "-o" ]]; then
      shift
      mkdir -p "$(dirname "$1")"
      printf 'fake image' > "$1"
      exit 0
    fi
    shift
  done
fi
exit 0
""",
        encoding="utf-8",
    )
    fake_docker.chmod(0o755)

    env = {
        **os.environ,
        "DOCKER": str(fake_docker),
        "HOST_CODEX_ROOT": str(codex_root),
        "RUNNER_ARCHIVE_PATH": str(archive_path),
    }
    result = subprocess.run(
        [str(ROOT / "scripts" / "build_agent_sandbox_runner_image.sh")],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert archive_path.read_text(encoding="utf-8") == "fake image"


def test_runner_build_script_resolves_hoisted_codex_binary_legacy_layout(
    tmp_path: Path,
) -> None:
    codex_root = tmp_path / "host-codex" / "node_modules" / "@openai" / "codex"
    hoisted_binary = (
        tmp_path
        / "host-codex"
        / "node_modules"
        / "@openai"
        / "codex-linux-x64"
        / "vendor"
        / "x86_64-unknown-linux-musl"
        / "codex"
        / "codex"
    )
    archive_path = tmp_path / "dist" / "agent-sandbox-runner.tar"
    fake_docker = tmp_path / "docker"

    codex_root.mkdir(parents=True)
    hoisted_binary.parent.mkdir(parents=True)
    hoisted_binary.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    hoisted_binary.chmod(0o755)

    fake_docker.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
if [[ "${1:-}" == "save" ]]; then
  while [[ "$#" -gt 0 ]]; do
    if [[ "$1" == "-o" ]]; then
      shift
      mkdir -p "$(dirname "$1")"
      printf 'fake image' > "$1"
      exit 0
    fi
    shift
  done
fi
exit 0
""",
        encoding="utf-8",
    )
    fake_docker.chmod(0o755)

    env = {
        **os.environ,
        "DOCKER": str(fake_docker),
        "HOST_CODEX_ROOT": str(codex_root),
        "RUNNER_ARCHIVE_PATH": str(archive_path),
    }
    result = subprocess.run(
        [str(ROOT / "scripts" / "build_agent_sandbox_runner_image.sh")],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert archive_path.read_text(encoding="utf-8") == "fake image"
