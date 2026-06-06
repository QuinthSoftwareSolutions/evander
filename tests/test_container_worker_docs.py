from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_worker_daemon_console_script_is_registered() -> None:
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")

    assert 'agent-sandbox-worker-daemon = "agent_sandbox.worker_daemon:main"' in pyproject


def test_container_worker_docs_describe_codex_home_preservation() -> None:
    docs = (ROOT / "docs" / "container-worker-backend.md").read_text(encoding="utf-8")

    assert "/var/lib/agent-worker/sessions/<session_id>/agent_home" in docs
    assert "/home/agent/repos/<repo-name>" in docs
    assert "CODEX_HOME=/home/agent/.config/agent-sandbox/codex" in docs
    assert "agent_home/.config/agent-sandbox/codex/sessions" in docs
    assert "agent_home/.codex/config.toml" in docs
    assert "Authorization: Bearer <AGENT_SANDBOX_WORKER_TOKEN>" in docs
    assert "--security-opt no-new-privileges" in docs
    assert "--sandbox-profile strict" in docs
    assert "--tmpfs /tmp:rw,noexec,nosuid,size=512m" in docs
    assert "Phase 5 first slice" in docs
    assert "single-worker" in docs
    assert "agent_sandbox_worker_egress_policy_enabled" in docs
    assert "DOCKER-USER" in docs
    assert "169.254.0.0/16" in docs
    assert "169.254.169.254" in docs
    assert "agent-sandbox-worker-egress-policy.service" in docs
    assert "sudo systemctl status agent-sandbox-worker-egress-policy.service" in docs
    assert "sudo iptables -S DOCKER-USER" in docs
    assert "agent-sandbox-cli execution smoke-container-worker --json" in docs
    assert "container_worker_guest_http_target_relay_base_url" in docs
    assert "container_worker_guest_pypi_index_url" in docs
    assert "container_worker_guest_npm_registry_url" in docs
    assert "container_worker_images" in docs
    assert "claude: \"agent-sandbox-runner-claude:latest\"" in docs
    assert "dotnet-playwright" in docs
    assert "container_worker_guest_anthropic_base_url" in docs
    assert "model_provider_mode: \"anthropic\"" in docs
    assert "select the `claude` image alias" in docs
    assert "`container_worker_default_image` is the fallback" in docs
    assert "does not pull or build images" in docs
    assert "Directory-scoped `providers.json` overlays" in docs
    assert "inherit the root catalog" in docs
    assert "agent-sandbox-npm-audit-facade" in docs
    assert "11881" in docs
    assert "npm audit POSTs" in docs
    assert "/home/agent/.config/pip/pip.conf" in docs
    assert "apt-cacher-ng relay on `3142`" in docs
    assert "registry.npmjs.org" in docs
    assert "does not turn containers into a VM-equivalent trust boundary" in docs
    assert "Non-`none` networking requires the worker egress policy" in docs
    assert "--allowed-git-source-prefix" in docs
    assert "ssh://<git_ssh_alias><git_root>/<mirror>.git" in docs
    assert "GitHub/Azure DevOps egress" in docs
    assert "mssql_provision_database" in docs
    assert "session-owned" in docs
    assert "/v1/sessions/{session_id}/mssql/databases" in docs
    assert "one configured worker" in docs
    assert "explicit egress policy" in docs
    assert "The worker VM only covers this bounded first slice" in docs
    assert "dist/agent-sandbox-runner.tar.manifest" in docs
    assert "dist/agent-sandbox-runner-claude.tar.manifest" in docs
    assert "scripts/build_agent_sandbox_claude_runner_image.sh" in docs
    assert "RUNNER_FORCE_REBUILD=1 ./scripts/build_agent_sandbox_runner_image.sh" in docs
    assert "RUNNER_FORCE_REBUILD=1 ./scripts/build_agent_sandbox_claude_runner_image.sh" in docs
    assert "./scripts/build_agent_sandbox_runner_image.sh --force" in docs
    assert "./scripts/build_agent_sandbox_claude_runner_image.sh --force" in docs
    assert "ubuntu:24.04" in docs
    assert "cached archive was reused" in docs
    assert "approved refs must already be present on the worker VM" in docs
    assert "worker-side remote manifest comparison" in docs
    assert "tar-copy/load deployment model" in docs


def test_execution_backend_docs_distinguish_daemon_from_gateway_integration() -> None:
    docs = (ROOT / "docs" / "execution-backends.md").read_text(encoding="utf-8")

    assert "agent-sandbox-worker-daemon" in docs
    assert "one configured worker URL" in docs
    assert "single-worker Phase 5 deployment slice" in docs
    assert "libvirt_vm` remains the default" in docs
    assert "## Current Scope" in docs
    assert "backend-aware `effect:agent:*` workflow nodes" in docs
    assert "agent_sandbox_worker_egress_policy_enabled: true" in docs
    assert "container_worker_images" in docs
    assert "claude: \"agent-sandbox-runner-claude:latest\"" in docs
    assert "container_worker_guest_anthropic_base_url" in docs
    assert "model_provider_mode: \"anthropic\"" in docs
    assert "stored image" in docs
    assert "## Remaining Migration Work" in docs
    assert "Phase 5" in docs
