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
        "frontend_build",
        "network_vm_bootstrap",
        "service_runtime",
        "verification",
    ]:
        assert (ansible_dir / "roles" / role_name / "tasks" / "main.yml").exists()


def test_ansible_site_uses_repo_native_bootstrap_order() -> None:
    repo = Path(__file__).resolve().parents[1]
    site = (repo / "ansible" / "site.yml").read_text(encoding="utf-8")

    assert "agent_sandbox_service_profile in ['full', 'control_plane_only']" in site
    assert (
        "agent_sandbox_services.firewall must be false for control_plane_only deployments" in site
    )
    assert "agent_sandbox_runtime_dir == agent_sandbox_expected_runtime_dir" in site
    assert "- role: host_prereqs" in site
    assert "- role: repo_checkout" in site
    assert "- role: python_env" in site
    assert "- role: runtime_bootstrap" in site
    assert "- role: service_config" in site
    assert "- role: systemd_install" in site
    assert "- role: frontend_build" in site
    assert "- role: network_vm_bootstrap" in site
    assert "- role: service_runtime" in site
    assert "- role: verification" in site


def test_ansible_group_vars_document_required_contract_and_service_toggles() -> None:
    repo = Path(__file__).resolve().parents[1]
    text = (repo / "ansible" / "group_vars" / "all.yml").read_text(encoding="utf-8")

    assert "agent_sandbox_repo_url" in text
    assert "agent_sandbox_repo_version" in text
    assert "agent_sandbox_repo_accept_newhostkey" in text
    assert "agent_sandbox_repo_ssh_private_key" in text
    assert "agent_sandbox_repo_ssh_private_key_path" in text
    assert "agent_sandbox_repo_managed_tracked_config_paths" in text
    assert "agent_sandbox_manage_workflow_config: true" in text
    assert "agent_sandbox_repo_managed_workflow_config_paths" in text
    assert "agent_sandbox_repo_managed_generated_tracked_paths" in text
    assert "config/admin-web.env" in text
    assert "frontend/workflow-editor/dist/index.html" in text
    assert "config/trigger-orchestrator/layout.json" in text
    assert "config/workflow-packs/agent-default.v1.json" in text
    assert "agent_sandbox_repo_managed_untracked_config_paths" in text
    assert "agent_sandbox_deploy_path" in text
    assert 'agent_sandbox_runtime_dir: "{{ agent_sandbox_deploy_path }}/runtime"' in text
    assert "agent_sandbox_run_user" in text
    assert "agent_sandbox_allowed_tcp_ports" in text
    assert "11777" in text
    assert "agent_sandbox_service_profile == 'full'" in text
    assert "firewall: \"{{ agent_sandbox_service_profile == 'full' }}\"" in text
    common_packages = text.split("agent_sandbox_local_vm_host_packages:", 1)[0]
    assert "  - nftables" not in common_packages
    assert "  - nodejs" in common_packages
    assert "  - npm" in common_packages
    assert "agent_sandbox_local_vm_host_packages:\n  - nftables" in text
    assert "agent_sandbox_services:" in text
    assert "agent_sandbox_admin_web_env:" in text
    assert "agent_sandbox_admin_web_frontend_build_enabled: true" in text
    assert 'agent_sandbox_workflow_layout_path: "config/trigger-orchestrator/layout.json"' in text
    assert "agent_sandbox_workflow_layout_source_path" in text
    assert "admin_web: true" in text
    assert "http_target_relay: true" in text
    assert "trigger_orchestrator: true" in text
    assert "effect_orchestrator: true" in text


def test_ansible_repo_checkout_supports_private_deploy_key() -> None:
    repo = Path(__file__).resolve().parents[1]
    text = (repo / "ansible" / "roles" / "repo_checkout" / "tasks" / "main.yml").read_text(
        encoding="utf-8"
    )

    assert "Install repo deploy key" in text
    assert "no_log: true" in text
    assert "Verify repo deploy key is usable non-interactively" in text
    assert "passphrase-free deploy key" in text
    assert "List tracked Ansible-managed paths known to the deployed checkout" in text
    assert "Reset tracked Ansible-managed config before repo update" in text
    assert "'ls-files', '--'" in text
    assert "Clean untracked Ansible-managed config before repo update" in text
    assert "'clean', '-f', '--'" in text
    assert "key_file:" in text
    assert "agent_sandbox_repo_ssh_private_key_path" in text
    assert "accept_newhostkey:" in text


def test_ansible_env_template_shell_quotes_values() -> None:
    repo = Path(__file__).resolve().parents[1]
    text = (repo / "ansible" / "roles" / "service_config" / "templates" / "env_file.j2").read_text(
        encoding="utf-8"
    )

    assert "{{ key }}={{ value | string | quote }}" in text
    assert "to_json" not in text


def test_ansible_workflow_config_render_can_be_disabled() -> None:
    repo = Path(__file__).resolve().parents[1]
    service_config = (
        repo / "ansible" / "roles" / "service_config" / "tasks" / "main.yml"
    ).read_text(encoding="utf-8")

    assert 'path: "config/trigger-orchestrator/config.json"' in service_config
    assert 'enabled: "{{ agent_sandbox_manage_workflow_config }}"' in service_config
    assert "when: item.enabled | default(true) | bool" in service_config
    assert "Check whether a controller-side workflow editor layout exists" in service_config
    assert "Copy managed workflow editor layout" in service_config
    assert "agent_sandbox_workflow_layout_source_path" in service_config


def test_ansible_restarts_services_after_managed_config_changes() -> None:
    repo = Path(__file__).resolve().parents[1]
    service_config = (
        repo / "ansible" / "roles" / "service_config" / "tasks" / "main.yml"
    ).read_text(encoding="utf-8")
    service_runtime = (
        repo / "ansible" / "roles" / "service_runtime" / "tasks" / "main.yml"
    ).read_text(encoding="utf-8")

    assert "agent_sandbox_managed_service_config_changed" in service_config
    assert "agent_sandbox_env_file_render.changed" in service_config
    assert "agent_sandbox_json_config_render.changed" in service_config
    assert "agent_sandbox_managed_service_config_changed | default(false)" in service_runtime
    assert "'restarted'" in service_runtime


def test_ansible_systemd_install_passes_service_profile() -> None:
    repo = Path(__file__).resolve().parents[1]
    text = (repo / "ansible" / "roles" / "systemd_install" / "tasks" / "main.yml").read_text(
        encoding="utf-8"
    )

    assert "AGENT_SANDBOX_SERVICE_PROFILE" in text
    assert "{{ agent_sandbox_service_profile }}" in text
