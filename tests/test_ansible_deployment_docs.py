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
    assert "agent-sandbox-cli status --runtime-dir runtime" in text


def test_repo_docs_link_ansible_deployment_path() -> None:
    repo = Path(__file__).resolve().parents[1]
    readme = (repo / "README.md").read_text(encoding="utf-8")
    operations = (repo / "docs" / "operations.md").read_text(encoding="utf-8")

    assert "ansible/README.md" in readme
    assert "ansible/README.md" in operations
