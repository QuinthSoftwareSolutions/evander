import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_worker_playbook_exists_with_expected_roles() -> None:
    ansible_dir = ROOT / "ansible"
    worker = (ansible_dir / "worker.yml").read_text(encoding="utf-8")

    assert "hosts: agent_sandbox_worker_hosts" in worker
    assert "Resolve local worker playbook revision" in worker
    assert "Default worker repo checkout to the playbook revision" in worker
    assert "Assert worker playbook and daemon checkout revisions match" in worker
    assert "agent_sandbox_worker_enforce_revision_match=false" in worker
    assert "agent_sandbox_worker_token must be set from Vault or inventory" in worker
    assert "agent_sandbox_worker_sandbox_profile in ['default', 'strict']" in worker
    assert "container worker daemon refuses Docker host networking" in worker
    assert "- role: host_prereqs" in worker
    assert "- role: worker_git_access" in worker
    assert "- role: repo_checkout" in worker
    assert "- role: python_env" in worker
    assert "- role: container_worker_runtime" in worker
    assert "- role: container_worker_service" in worker
    assert "- role: container_worker_verification" in worker

    for role_name in [
        "container_worker_runtime",
        "worker_git_access",
        "container_worker_service",
        "container_worker_verification",
    ]:
        assert (ansible_dir / "roles" / role_name / "tasks" / "main.yml").exists()


def test_worker_role_task_files_are_not_ignored_by_git() -> None:
    role_task_paths = [
        "ansible/roles/container_worker_runtime/tasks/main.yml",
        "ansible/roles/worker_git_access/tasks/main.yml",
        "ansible/roles/container_worker_service/tasks/main.yml",
        "ansible/roles/container_worker_verification/tasks/main.yml",
    ]

    for role_task_path in role_task_paths:
        result = subprocess.run(
            ["git", "check-ignore", "--quiet", "--", role_task_path],
            cwd=ROOT,
            check=False,
        )

        assert result.returncode == 1


def test_worker_group_vars_define_docker_and_durable_state_contract() -> None:
    text = (ROOT / "ansible" / "group_vars" / "agent_sandbox_worker_hosts.yml").read_text(
        encoding="utf-8"
    )

    assert 'agent_sandbox_deploy_path: "/opt/agent-sandbox-worker"' in text
    assert 'agent_sandbox_run_user: "agent-worker"' in text
    assert 'agent_sandbox_worker_repo_version_mode: "playbook_revision"' in text
    assert "agent_sandbox_worker_enforce_revision_match: true" in text
    assert "agent_sandbox_worker_revision_guard_paths" in text
    assert "src/agent_sandbox/worker" in text
    assert "ansible/roles/container_worker_service" in text
    assert "  - docker" in text
    assert "  - docker.io" in text
    assert 'agent_sandbox_python_install_extras: ""' in text
    assert "agent_sandbox_worker_port: 11888" in text
    assert 'agent_sandbox_worker_root: "/var/lib/agent-worker"' in text
    assert 'agent_sandbox_worker_runtime_dir: "{{ agent_sandbox_worker_root }}/runtime"' in text
    assert 'agent_sandbox_worker_sandbox_profile: "default"' in text
    assert "AGENT_SANDBOX_WORKER_TOKEN" in text
    assert "agent_sandbox_control_plane_host" in text
    assert "hostvars[agent_sandbox_control_plane_host].agent_sandbox_deploy_path" in text
    assert "hostvars[agent_sandbox_control_plane_host].agent_sandbox_run_user" in text
    assert "agent_sandbox_control_plane_private_ip" in text
    assert 'agent_sandbox_git_host: "<control-plane-private-ip>"' not in text
    assert 'agent_sandbox_git_root: "{{ agent_sandbox_control_plane_deploy_path }}/git-remotes"' in text
    assert 'agent_sandbox_git_ssh_alias: "agent-sandbox-git-host"' in text
    assert "agent_sandbox_worker_allowed_git_source_prefixes" in text
    assert "ssh://{{ agent_sandbox_git_ssh_alias }}{{ agent_sandbox_git_root }}/" in text
    assert "agent_sandbox_worker_git_ssh_source_dir" in text
    assert "agent_sandbox_worker_git_access_enabled" in text
    assert "agent_sandbox_worker_install_control_plane_git_authorized_key" in text
    assert "agent_sandbox_worker_repo_checkout_via_control_plane_git" in text
    assert "agent_sandbox_worker_repo_mirror_name" in text
    assert "agent_sandbox_worker_repo_checkout_url" in text
    assert 'agent_sandbox_runner_image_name: "agent-sandbox-runner:latest"' in text
    assert "agent_sandbox_runner_image_archive_path" in text
    assert "agent_sandbox_worker_toolchain_cache_root" in text
    assert "agent_sandbox_worker_readonly_mounts" in text
    assert "/opt/agent-sandbox/toolchains/dotnet" in text
    assert "/opt/agent-sandbox/toolchains/node-v24.14.0" in text
    assert "/opt/agent-sandbox/playwright-runtime" in text
    assert "/opt/agent-sandbox/ms-playwright" in text
    assert "/opt/agent-sandbox/nuget-cache-ro" in text
    assert "agent_sandbox_worker_preload_images" in text


def test_worker_azure_inventory_uses_truman_control_plane_identity() -> None:
    azure = (ROOT / "ansible" / "group_vars" / "azure.yml").read_text(encoding="utf-8")
    worker_defaults = (
        ROOT / "ansible" / "group_vars" / "agent_sandbox_worker_hosts.yml"
    ).read_text(encoding="utf-8")

    assert 'agent_sandbox_deploy_path: "/opt/truman"' in azure
    assert 'agent_sandbox_run_user: "truman"' in azure
    assert "agent_sandbox_git_user" in worker_defaults
    assert "hostvars[agent_sandbox_control_plane_host].agent_sandbox_run_user" in worker_defaults
    assert "hostvars[agent_sandbox_control_plane_host].agent_sandbox_deploy_path" in worker_defaults


def test_worker_service_role_renders_env_and_daemon_unit() -> None:
    role = ROOT / "ansible" / "roles" / "container_worker_service"
    tasks = (role / "tasks" / "main.yml").read_text(encoding="utf-8")
    unit = (role / "templates" / "agent-sandbox-worker-daemon.service.j2").read_text(
        encoding="utf-8"
    )
    env = (role / "templates" / "worker-daemon.env.j2").read_text(encoding="utf-8")

    assert "config/worker-daemon.env" in tasks
    assert "no_log: true" in tasks
    assert "agent-sandbox-worker-daemon.service" in tasks
    assert "agent_sandbox_worker_env_render.changed" in tasks
    assert "{{ key }}={{ value | string | quote }}" in env
    assert "Requires=docker.service" in unit
    assert (
        "SupplementaryGroups={{ agent_sandbox_run_user_supplementary_groups | join(' ') }}" in unit
    )
    assert "EnvironmentFile={{ agent_sandbox_deploy_path }}/config/worker-daemon.env" in unit
    assert ".venv/bin/agent-sandbox-worker-daemon" in unit
    assert "--worker-root {{ agent_sandbox_worker_root }}" in unit
    assert "--runtime-dir {{ agent_sandbox_worker_runtime_dir }}" in unit
    assert "--sandbox-profile {{ agent_sandbox_worker_sandbox_profile }}" in unit
    assert "--docker-network {{ agent_sandbox_worker_docker_network }}" in unit
    assert "--allowed-git-source-prefix {{ prefix }}" in unit
    assert "--git-ssh-source-dir {{ agent_sandbox_worker_git_ssh_source_dir }}" in unit
    assert "--read-only-rootfs" in unit
    assert "--container-user {{ agent_sandbox_effective_worker_container_user }}" in unit
    assert "--readonly-mount {{ mount }}" in unit
    assert "{% endfor %}{{ \"\" }}\nRestart=on-failure" in unit


def test_worker_runtime_and_verification_roles_cover_docker_health_and_no_control_plane_units() -> (
    None
):
    runtime = (
        ROOT / "ansible" / "roles" / "container_worker_runtime" / "tasks" / "main.yml"
    ).read_text(encoding="utf-8")
    verification = (
        ROOT / "ansible" / "roles" / "container_worker_verification" / "tasks" / "main.yml"
    ).read_text(encoding="utf-8")

    assert "docker.service" in runtime
    assert "agent_sandbox_worker_root" in runtime
    assert "agent_sandbox_worker_runtime_dir" in runtime
    assert "Resolve worker service user uid" in runtime
    assert "agent_sandbox_effective_worker_container_user" in runtime
    assert "Check local runner image archive" in runtime
    assert "scripts/build_agent_sandbox_runner_image.sh" in runtime
    assert "docker\n      - load" in runtime
    assert "docker\n      - image\n      - inspect" in runtime
    assert "agent_sandbox_worker_toolchain_cache_root" in runtime
    assert "agent_sandbox_worker_preload_images" in runtime
    assert "agent-sandbox-worker-daemon.service" in verification
    assert "docker ps" not in verification
    assert "Verify Docker is usable by the worker user" in verification
    assert "/healthz" in verification
    assert "container user: {{ agent_sandbox_effective_worker_container_user }}" in verification
    assert "agent-sandbox-gateway.service" not in verification
    assert "agent-sandbox-admin-web.service" not in verification


def test_worker_git_access_role_sets_up_private_repo_checkout_before_repo_checkout() -> None:
    worker = (ROOT / "ansible" / "worker.yml").read_text(encoding="utf-8")
    role = (ROOT / "ansible" / "roles" / "worker_git_access" / "tasks" / "main.yml").read_text(
        encoding="utf-8"
    )

    assert worker.index("- role: host_prereqs") < worker.index("- role: worker_git_access")
    assert worker.index("- role: worker_git_access") < worker.index("- role: repo_checkout")
    assert "Generate restricted worker Git SSH key" in role
    assert "IdentityFile ~/.ssh/agent-sandbox-git" in role
    assert "Authorize worker Git key on the control plane" in role
    assert "git_ssh_command_wrapper.sh" in role
    assert "Ensure control-plane deployment repo mirror exists" in role
    assert "scripts/create_git_remote.sh" in role
    assert "agent-sandbox.mirrorSource" in role
    assert "agent-sandbox.mirrorSyncRemote" in role
    assert "Use private control-plane mirror for worker repo checkout" in role
    assert "agent_sandbox_repo_url: \"{{ agent_sandbox_worker_repo_checkout_url }}\"" in role
    assert "Deploy the control plane first, then rerun ansible/worker.yml" in role
    assert "not the literal <control-plane-private-ip> placeholder" in role


def test_worker_docs_and_inventory_reference_playbook() -> None:
    inventory = (ROOT / "ansible" / "inventory" / "hosts.example.yml").read_text(encoding="utf-8")
    ansible_docs = (ROOT / "ansible" / "README.md").read_text(encoding="utf-8")
    worker_docs = (ROOT / "docs" / "container-worker-backend.md").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    operations = (ROOT / "docs" / "operations.md").read_text(encoding="utf-8")

    assert "agent_sandbox_worker_hosts" in inventory
    assert "ansible/worker.yml" in ansible_docs
    assert "agent_sandbox_worker_token" in ansible_docs
    assert "container_worker_base_url" in ansible_docs
    assert "container_worker_guest_pypi_index_url" in ansible_docs
    assert "container_worker_guest_npm_registry_url" in ansible_docs
    assert "local playbook commit" in ansible_docs
    assert "agent_sandbox_worker_enforce_revision_match: false" in ansible_docs
    assert "Worker outbound rules should still deny direct access" in ansible_docs
    assert "allowed Git source prefix" in ansible_docs
    assert "GitHub/Azure DevOps egress" in ansible_docs
    assert "smoke-container-worker" in ansible_docs
    assert "ansible/worker.yml" in worker_docs
    assert "dedicated container-worker VM playbook" in readme
    assert "dedicated container-worker VM playbook" in operations
