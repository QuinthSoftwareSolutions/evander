from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_worker_daemon_console_script_is_registered() -> None:
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")

    assert 'agent-sandbox-worker-daemon = "agent_sandbox.worker_daemon:main"' in pyproject


def test_container_worker_docs_describe_codex_home_preservation() -> None:
    docs = (ROOT / "docs" / "container-worker-backend.md").read_text(encoding="utf-8")

    assert "/var/lib/agent-worker/sessions/<session_id>/agent_home" in docs
    assert ".codex" in docs
    assert "Authorization: Bearer <AGENT_SANDBOX_WORKER_TOKEN>" in docs


def test_execution_backend_docs_distinguish_daemon_from_gateway_integration() -> None:
    docs = (ROOT / "docs" / "execution-backends.md").read_text(encoding="utf-8")

    assert "agent-sandbox-worker-daemon" in docs
    assert "early single-worker control-plane use" in docs
    assert "libvirt_vm` remains the default" in docs
