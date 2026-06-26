from pathlib import Path


def test_ansible_readme_covers_ubuntu_azure_and_repo_native_entrypoints() -> None:
    repo = Path(__file__).resolve().parents[1]
    text = (repo / "ansible" / "README.md").read_text(encoding="utf-8")

    assert "Ubuntu 24.04" in text
    assert "nested virtualization" in text
    assert "scripts/install_systemd_units.sh" in text
    assert "scripts/create_libvirt_network.sh" in text
    assert "scripts/apply_firewall.sh" in text
    assert "scripts/create_base_vm.sh" in text
    assert "config/admin-web.env" in text
    assert "agent_sandbox_services.admin_web" in text
    assert "agent_sandbox_admin_web_caddy_enabled" in text
    assert "agent_sandbox_services.package_cache" in text
    assert "agent_sandbox_services.apt_cache" in text
    assert "agent_sandbox_services.npm_audit_facade" in text
    assert "agent_sandbox_services.semantic_store" in text
    assert "agent_sandbox_services.proxy_issue_sync" in text
    assert "agent_sandbox_npm_audit_facade_port" in text
    assert "config/npm-audit-facade.env" in text
    assert "agent_sandbox_nexus_admin_password" in text
    assert "agent_sandbox_nexus_accept_community_eula" in text
    assert "agent-sandbox-nexus.service" in text
    assert "package-cache reader role" in text
    assert "agent_sandbox_nexus_pypi_probe_asset_path" in text
    assert "apt-cacher-ng.service" in text
    assert "acng-report.html" in text
    assert "service/rest/v1/status" in text
    assert "registry.npmjs.org" in text
    assert "11881" in text
    assert "Ubuntu mirrors" in text
    assert "agent_sandbox_worker_egress_policy_enabled: true" in text
    assert "agent-sandbox-worker-egress-policy.service" in text
    assert "DOCKER-USER" in text
    assert "169.254.169.254" in text
    assert "evander-cli execution smoke-container-worker --json" in text
    assert "agent-sandbox-qdrant.service" in text
    assert "qdrant/qdrant:v1.18.0" in text
    assert "/var/lib/agent-sandbox/qdrant" in text
    assert "127.0.0.1:6333" in text
    assert "/healthz" in text
    assert "evander-cli status --runtime-dir runtime" in text
    assert "dist/agent-sandbox-runner.tar.manifest" in text
    assert "dist/agent-sandbox-runner-claude.tar.manifest" in text
    assert "./scripts/build_agent_sandbox_claude_runner_image.sh" in text
    assert "HOST_CLAUDE_BIN" in text
    assert "RUNNER_FORCE_REBUILD=1 ./scripts/build_agent_sandbox_runner_image.sh" in text
    assert "RUNNER_FORCE_REBUILD=1 ./scripts/build_agent_sandbox_claude_runner_image.sh" in text
    assert "./scripts/build_agent_sandbox_runner_image.sh --force" in text
    assert "./scripts/build_agent_sandbox_claude_runner_image.sh --force" in text
    assert "ubuntu:24.04" in text
    assert "reuse cached archives" in text
    assert "worker-side remote manifest comparison is deferred" in text
    assert "tar-copy/load deployment model" in text


def test_repo_docs_link_ansible_deployment_path() -> None:
    repo = Path(__file__).resolve().parents[1]
    readme = (repo / "README.md").read_text(encoding="utf-8")
    operations = (repo / "docs" / "operations.md").read_text(encoding="utf-8")

    assert "ansible/README.md" in readme
    assert "ansible/README.md" in operations
