from pathlib import Path


def test_ansible_bundle_exists_with_expected_roles() -> None:
    repo = Path(__file__).resolve().parents[1]
    ansible_dir = repo / "ansible"

    assert (ansible_dir / "site.yml").exists()
    assert (ansible_dir / "README.md").exists()
    assert (ansible_dir / "inventory" / "hosts.example.yml").exists()
    assert (ansible_dir / "group_vars" / "all.yml").exists()

    for role_name in [
        "host_prereqs",
        "repo_checkout",
        "python_env",
        "runtime_bootstrap",
        "service_config",
        "systemd_install",
        "network_vm_bootstrap",
        "service_runtime",
        "verification",
    ]:
        assert (ansible_dir / "roles" / role_name / "tasks" / "main.yml").exists()


def test_ansible_site_uses_repo_native_bootstrap_order() -> None:
    repo = Path(__file__).resolve().parents[1]
    site = (repo / "ansible" / "site.yml").read_text(encoding="utf-8")

    assert "agent_sandbox_runtime_dir == agent_sandbox_expected_runtime_dir" in site
    assert "- role: host_prereqs" in site
    assert "- role: repo_checkout" in site
    assert "- role: python_env" in site
    assert "- role: runtime_bootstrap" in site
    assert "- role: service_config" in site
    assert "- role: systemd_install" in site
    assert "- role: network_vm_bootstrap" in site
    assert "- role: service_runtime" in site
    assert "- role: verification" in site


def test_ansible_group_vars_document_required_contract_and_service_toggles() -> None:
    repo = Path(__file__).resolve().parents[1]
    text = (repo / "ansible" / "group_vars" / "all.yml").read_text(encoding="utf-8")

    assert "agent_sandbox_repo_url" in text
    assert "agent_sandbox_repo_version" in text
    assert "agent_sandbox_deploy_path" in text
    assert 'agent_sandbox_runtime_dir: "{{ agent_sandbox_deploy_path }}/runtime"' in text
    assert "agent_sandbox_run_user" in text
    assert "agent_sandbox_allowed_tcp_ports" in text
    assert "11777" in text
    assert "agent_sandbox_services:" in text
    assert "agent_sandbox_admin_web_env:" in text
    assert "admin_web: true" in text
    assert "http_target_relay: true" in text
    assert "trigger_orchestrator: true" in text
    assert "effect_orchestrator: true" in text
