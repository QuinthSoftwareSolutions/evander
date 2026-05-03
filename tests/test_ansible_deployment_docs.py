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
    assert "agent_sandbox_services.package_cache" in text
    assert "agent_sandbox_services.apt_cache" in text
    assert "agent_sandbox_nexus_admin_password" in text
    assert "agent-sandbox-nexus.service" in text
    assert "apt-cacher-ng.service" in text
    assert "acng-report.html" in text
    assert "service/rest/v1/status" in text
    assert "registry.npmjs.org" in text
    assert "Ubuntu mirrors" in text
    assert "agent-sandbox-cli status --runtime-dir runtime" in text


def test_repo_docs_link_ansible_deployment_path() -> None:
    repo = Path(__file__).resolve().parents[1]
    readme = (repo / "README.md").read_text(encoding="utf-8")
    operations = (repo / "docs" / "operations.md").read_text(encoding="utf-8")

    assert "ansible/README.md" in readme
    assert "ansible/README.md" in operations
