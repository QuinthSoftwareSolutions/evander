import subprocess
from pathlib import Path


def test_ansible_bundle_exists_with_expected_roles() -> None:
    repo = Path(__file__).resolve().parents[1]
    ansible_dir = repo / "ansible"

    assert (ansible_dir / "site.yml").exists()
    assert (ansible_dir / "README.md").exists()
    assert (ansible_dir / "inventory" / "hosts.example.yml").exists()

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
    assert "'clean', '-f', '-x', '--'" in text
    assert "key_file:" in text
    assert "agent_sandbox_repo_ssh_private_key_path" in text
    assert "accept_newhostkey:" in text


def test_worker_global_live_configs_are_operator_local() -> None:
    repo = Path(__file__).resolve().parents[1]
    local_config_paths = [
        "config/ado-worker/config.json",
        "config/discord-ingest/config.json",
        "config/discord-wake/config.json",
        "config/host-heartbeat/config.json",
        "config/issue-orchestrator/config.json",
        "config/scheduled-wake/config.json",
        "config/teams-ingest/config.json",
        "config/teams-meeting-ingest/config.json",
    ]
    example_paths = [
        "config/ado-worker/config.json.example",
        "config/discord-ingest/config.json.example",
        "config/discord-wake/config.json.example",
        "config/issue-orchestrator/config.json.example",
        "config/scheduled-wake/config.json.example",
        "config/teams-ingest/config.json.example",
        "config/teams-meeting-ingest/config.json.example",
    ]

    ignored = subprocess.run(
        ["git", "check-ignore", *local_config_paths],
        cwd=repo,
        check=True,
        text=True,
        capture_output=True,
    ).stdout.splitlines()
    assert set(ignored) == set(local_config_paths)

    tracked = subprocess.run(
        ["git", "ls-files", "--", *local_config_paths, *example_paths],
        cwd=repo,
        check=True,
        text=True,
        capture_output=True,
    ).stdout.splitlines()
    assert not set(local_config_paths).intersection(tracked)
    assert set(example_paths).issubset(tracked)


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
    assert "Copy managed workflow pack files" in service_config
    assert "agent_sandbox_managed_workflow_pack_files" in service_config
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
