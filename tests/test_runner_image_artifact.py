from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_runner_dockerfile_contains_minimal_runtime_contract() -> None:
    dockerfile = (ROOT / "docker" / "agent-sandbox-runner" / "Dockerfile").read_text(
        encoding="utf-8"
    )

    assert "FROM ubuntu:24.04" in dockerfile
    assert "COPY codex /usr/local/bin/codex" in dockerfile
    assert "ENV HOME=/home/agent" in dockerfile
    assert "WORKDIR /workspace" in dockerfile
    assert "ripgrep" in dockerfile
    assert "openssh-client" in dockerfile
    assert "libnss3" in dockerfile
    assert "libgtk-3-0" in dockerfile
    assert "/home/agent/.agent-sandbox/session.env" in dockerfile


def test_runner_build_script_builds_tag_and_saves_tarball() -> None:
    script = (ROOT / "scripts" / "build_agent_sandbox_runner_image.sh").read_text(
        encoding="utf-8"
    )

    assert "HOST_CODEX_BINARY_PATH" in script
    assert "RUNNER_BUILD_NETWORK" in script
    assert "agent-sandbox-runner:latest" in script
    assert "dist/agent-sandbox-runner.tar" in script
    assert 'build_args=(build -t "${RUNNER_IMAGE_NAME}")' in script
    assert 'build_args+=(--network "${RUNNER_BUILD_NETWORK}")' in script
    assert '"${DOCKER}" save -o "${RUNNER_ARCHIVE_PATH}" "${RUNNER_IMAGE_NAME}"' in script
