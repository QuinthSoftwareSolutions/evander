from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_worker_daemon_console_script_is_registered() -> None:
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")

    assert 'agent-sandbox-worker-daemon = "agent_sandbox.worker_daemon:main"' in pyproject


def test_container_worker_docs_describe_codex_home_preservation() -> None:
    docs = (ROOT / "docs" / "container-worker-backend.md").read_text(encoding="utf-8")

    assert "/var/lib/agent-worker/sessions/<session_id>/agent_home" in docs
    assert "CODEX_HOME=/home/agent/.config/agent-sandbox/codex" in docs
    assert "agent_home/.config/agent-sandbox/codex/sessions" in docs
    assert "agent_home/.codex/config.toml" in docs
    assert "Authorization: Bearer <AGENT_SANDBOX_WORKER_TOKEN>" in docs
    assert "--security-opt no-new-privileges" in docs
    assert "--sandbox-profile strict" in docs
    assert "--tmpfs /tmp:rw,noexec,nosuid,size=512m" in docs
    assert "container_worker_guest_pypi_index_url" in docs
    assert "container_worker_guest_npm_registry_url" in docs
    assert "/home/agent/.config/pip/pip.conf" in docs
    assert "registry.npmjs.org" in docs
    assert "does not turn containers into a VM-equivalent trust boundary" in docs
    assert "Non-`none` networking requires Phase 5" in docs
    assert "--allowed-git-source-prefix" in docs
    assert "ssh://<git_ssh_alias><git_root>/<mirror>.git" in docs
    assert "GitHub/Azure DevOps egress" in docs


def test_execution_backend_docs_distinguish_daemon_from_gateway_integration() -> None:
    docs = (ROOT / "docs" / "execution-backends.md").read_text(encoding="utf-8")

    assert "agent-sandbox-worker-daemon" in docs
    assert "early single-worker control-plane use" in docs
    assert "libvirt_vm` remains the default" in docs
    assert "Phase 4 formalizes the worker container sandbox profile" in docs
    assert "does not implement Phase 5 networking" in docs
