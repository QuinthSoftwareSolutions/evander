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
    assert "- role: worker_package_cache_preflight" in worker
    assert "- role: python_env" in worker
    assert "- role: container_worker_runtime" in worker
    assert "- role: worker_dotnet_toolchain" in worker
    assert "- role: worker_node_toolchain" in worker
    assert "- role: worker_playwright_toolchain" in worker
    assert "- role: worker_mssql" in worker
    assert "- role: worker_container_egress_policy" in worker
    assert "- role: container_worker_service" in worker
    assert "- role: container_worker_verification" in worker
    assert "Resolve worker Docker network and egress allowlist" in worker
    assert "Assert non-none worker Docker networking has egress policy" in worker
    assert "agent_sandbox_worker_docker_network=none for isolated containers" in worker
    assert "Assert worker egress allowlist endpoints are concrete" in worker
    assert worker.index("- role: container_worker_runtime") < worker.index(
        "- role: worker_mssql"
    )
    assert worker.index("- role: worker_mssql") < worker.index(
        "- role: worker_container_egress_policy"
    )
    assert worker.index("- role: worker_container_egress_policy") < worker.index(
        "- role: container_worker_service"
    )

    for role_name in [
        "container_worker_runtime",
        "worker_git_access",
        "worker_package_cache_preflight",
        "worker_dotnet_toolchain",
        "worker_node_toolchain",
        "worker_playwright_toolchain",
        "worker_mssql",
        "worker_container_egress_policy",
        "container_worker_service",
        "container_worker_verification",
    ]:
        assert (ansible_dir / "roles" / role_name / "tasks" / "main.yml").exists()


def test_worker_role_task_files_are_not_ignored_by_git() -> None:
    role_task_paths = [
        "ansible/roles/container_worker_runtime/tasks/main.yml",
        "ansible/roles/worker_git_access/tasks/main.yml",
        "ansible/roles/worker_package_cache_preflight/tasks/main.yml",
        "ansible/roles/worker_dotnet_toolchain/tasks/main.yml",
        "ansible/roles/worker_node_toolchain/tasks/main.yml",
        "ansible/roles/worker_playwright_toolchain/tasks/main.yml",
        "ansible/roles/worker_mssql/tasks/main.yml",
        "ansible/roles/worker_container_egress_policy/tasks/main.yml",
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
    assert "ansible/roles/python_env" in text
    assert "ansible/roles/worker_package_cache_preflight" in text
    assert "ansible/roles/worker_dotnet_toolchain" in text
    assert "ansible/roles/worker_node_toolchain" in text
    assert "ansible/roles/worker_playwright_toolchain" in text
    assert "ansible/roles/worker_mssql" in text
    assert "ansible/roles/worker_container_egress_policy" in text
    assert "ansible/roles/container_worker_service" in text
    assert "  - docker" in text
    assert "  - docker.io" in text
    assert 'agent_sandbox_python_install_extras: ""' in text
    assert "agent_sandbox_python_install_env" in text
    assert "UV_DEFAULT_INDEX" in text
    assert "UV_INSECURE_HOST" in text
    assert "PIP_INDEX_URL" in text
    assert "PIP_TRUSTED_HOST" in text
    assert "agent_sandbox_worker_validate_package_cache" in (
        ROOT / "ansible" / "group_vars" / "all" / "defaults.yml"
    ).read_text(encoding="utf-8")
    assert "agent_sandbox_worker_port: 11888" in text
    assert 'agent_sandbox_worker_root: "/var/lib/agent-worker"' in text
    assert 'agent_sandbox_worker_runtime_dir: "{{ agent_sandbox_worker_root }}/runtime"' in text
    assert 'agent_sandbox_worker_sandbox_profile: "default"' in text
    assert 'agent_sandbox_worker_docker_network: "none"' in text
    assert "agent_sandbox_worker_egress_policy_enabled: false" in text
    assert "agent_sandbox_worker_egress_policy_chain" in text
    assert "agent_sandbox_worker_egress_policy_source_cidrs: []" in text
    assert '"169.254.0.0/16"' in text
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
    assert "agent_sandbox_worker_dotnet_toolchain_enabled: true" in text
    assert "agent_sandbox_worker_dotnet_toolchain_local_path" in text
    assert "HOST_DOTNET_ROOT" in text
    assert "agent_sandbox_worker_dotnet_toolchain_archive_path" in text
    assert "agent_sandbox_worker_dotnet_tools_cache_path" in text
    assert "agent_sandbox_worker_dotnet_tools_archive_path" in text
    assert "HOST_DOTNET_TOOLS_ARCHIVE" in text
    assert "agent_sandbox_worker_dotnet_ef_version" in text
    assert "HOST_DOTNET_EF_VERSION" in text
    assert "agent_sandbox_worker_nuget_fallback_archive_path" in text
    assert "HOST_NUGET_FALLBACK_ARCHIVE" in text
    assert "agent_sandbox_worker_dotnet_restore_support_archive_path" in text
    assert "HOST_DOTNET_RESTORE_SUPPORT_ARCHIVE" in text
    assert "Microsoft.Extensions.FileSystemGlobbing" in text
    assert "agent_sandbox_worker_node_toolchain_enabled: true" in text
    assert "agent_sandbox_worker_node_toolchain_local_path" in text
    assert "HOST_NODE_ROOT" in text
    assert "agent_sandbox_worker_node_toolchain_archive_path" in text
    assert "agent_sandbox_worker_playwright_toolchain_enabled: true" in text
    assert "agent_sandbox_worker_playwright_node_modules_local_path" in text
    assert "HOST_PLAYWRIGHT_NODE_MODULES" in text
    assert "agent_sandbox_worker_playwright_runtime_archive_path" in text
    assert "agent_sandbox_worker_playwright_browsers_archive_path" in text
    assert "agent_sandbox_worker_readonly_mounts" in text
    assert "/opt/agent-sandbox/toolchains/dotnet" in text
    assert "/opt/agent-sandbox/toolchains/dotnet-tools" in text
    assert "/opt/agent-sandbox/toolchains/node-v24.14.0" in text
    assert "/opt/agent-sandbox/playwright-runtime" in text
    assert "/opt/agent-sandbox/ms-playwright" in text
    assert "/opt/agent-sandbox/nuget-cache-ro" in text
    assert "agent_sandbox_worker_preload_images" in text
    assert "agent_sandbox_worker_mssql_enabled: false" in text
    assert "agent_sandbox_worker_mssql_container_host" in text
    assert "AGENT_SANDBOX_WORKER_MSSQL_ADMIN_PASSWORD" in text


def test_worker_defaults_derive_control_plane_identity_from_hostvars() -> None:
    worker_defaults = (
        ROOT / "ansible" / "group_vars" / "agent_sandbox_worker_hosts.yml"
    ).read_text(encoding="utf-8")

    assert "agent_sandbox_install_azure_cli: false" in worker_defaults
    assert "agent_sandbox_git_user" in worker_defaults
    assert "hostvars[agent_sandbox_control_plane_host].agent_sandbox_run_user" in worker_defaults
    assert "hostvars[agent_sandbox_control_plane_host].agent_sandbox_deploy_path" in worker_defaults


def test_worker_egress_policy_defaults_expose_private_control_plane_endpoints() -> None:
    defaults = (ROOT / "ansible" / "group_vars" / "all" / "defaults.yml").read_text(
        encoding="utf-8"
    )
    azure_inventory = (ROOT / "ansible" / "inventory" / "azure.example.yml").read_text(
        encoding="utf-8"
    )
    worker_defaults = (
        ROOT / "ansible" / "group_vars" / "agent_sandbox_worker_hosts.yml"
    ).read_text(encoding="utf-8")

    assert 'agent_sandbox_worker_docker_network: "none"' in worker_defaults
    assert "agent_sandbox_worker_egress_policy_enabled: false" in worker_defaults
    assert "agent_sandbox_worker_egress_policy_chain" in worker_defaults
    assert "agent_sandbox_worker_egress_policy_script_path" in worker_defaults
    assert "agent_sandbox_worker_egress_policy_source_cidrs: []" in worker_defaults
    assert '"169.254.0.0/16"' in worker_defaults
    assert "agent_sandbox_worker_egress_policy_allow_tcp_endpoints" in defaults
    assert "agent_sandbox_worker_egress_policy_extra_allow_tcp_endpoints: []" in defaults
    assert 'agent_sandbox_git_host: "{{ agent_sandbox_control_plane_private_ip }}"' in defaults
    assert "name: control-plane git ssh" in defaults
    assert 'host: "{{ agent_sandbox_git_host }}"' in defaults
    assert "name: control-plane mcp gateway" in defaults
    assert "name: control-plane openai relay" in defaults
    assert "name: control-plane nuget relay" in defaults
    assert "name: control-plane http target relay" in defaults
    assert "name: control-plane npm audit facade" in defaults
    assert "name: control-plane nexus package cache" in defaults
    assert "name: control-plane apt-cacher-ng" in defaults
    assert "port: 22" in defaults
    assert "port: 8000" in defaults
    assert "port: 11434" in defaults
    assert "port: 11666" in defaults
    assert "port: 11777" in defaults
    assert "port: 11881" in defaults
    assert 'port: "{{ agent_sandbox_nexus_port }}"' in defaults
    assert 'port: "{{ agent_sandbox_apt_cache_port }}"' in defaults
    assert 'host: "{{ agent_sandbox_control_plane_private_ip }}"' in defaults
    assert "<control-plane-private-ip>" not in defaults
    assert 'agent_sandbox_worker_docker_network: "bridge"' in azure_inventory
    assert "agent_sandbox_worker_egress_policy_enabled: true" in azure_inventory


def test_worker_container_egress_policy_role_applies_docker_user_default_deny() -> None:
    role = ROOT / "ansible" / "roles" / "worker_container_egress_policy"
    worker_defaults = (
        ROOT / "ansible" / "group_vars" / "agent_sandbox_worker_hosts.yml"
    ).read_text(encoding="utf-8")
    tasks = (role / "tasks" / "main.yml").read_text(encoding="utf-8")
    script = (role / "templates" / "agent-sandbox-worker-egress-policy.sh.j2").read_text(
        encoding="utf-8"
    )
    unit = (role / "templates" / "agent-sandbox-worker-egress-policy.service.j2").read_text(
        encoding="utf-8"
    )
    verification = (
        ROOT / "ansible" / "roles" / "container_worker_verification" / "tasks" / "main.yml"
    ).read_text(encoding="utf-8")

    assert "agent_sandbox_worker_egress_policy_active" in tasks
    assert "Render worker egress policy script" in tasks
    assert "agent-sandbox-worker-egress-policy.service" in tasks
    assert "Disable inactive worker egress policy service" in tasks
    assert "docker network inspect" in script
    assert "DOCKER-USER" in script
    assert "AGENT-SANDBOX-WORKER-EGRESS" in worker_defaults
    assert "agent_sandbox_worker_egress_policy_block_cidrs" in script
    assert "169.254.0.0/16" in worker_defaults
    assert "--dport" in script
    assert "-j ACCEPT" in script
    assert 'iptables -A "${CHAIN}" -j DROP' in script
    assert "ExecStart={{ agent_sandbox_worker_egress_policy_script_path }} apply" in unit
    assert "ExecStop={{ agent_sandbox_worker_egress_policy_script_path }} clear" in unit
    assert "Before=agent-sandbox-worker-daemon.service" in unit
    assert "Verify worker egress policy service is active" in verification
    assert "systemctl" in verification
    assert "is-enabled" in verification
    assert "is-active" in verification
    assert "agent_sandbox_worker_egress_policy_active_state.stdout == 'active'" in verification
    assert "agent-sandbox-worker-egress-policy.service'].state == 'running'" not in verification


def _remove_jinja_raw_blocks(text: str) -> str:
    raw_start = "{% raw %}"
    raw_end = "{% endraw %}"
    while raw_start in text:
        start = text.index(raw_start)
        end = text.index(raw_end, start)
        text = text[:start] + text[end + len(raw_end) :]
    return text


def test_worker_container_egress_policy_script_escapes_jinja_comment_sequences() -> None:
    template_text = (
        ROOT
        / "ansible"
        / "roles"
        / "worker_container_egress_policy"
        / "templates"
        / "agent-sandbox-worker-egress-policy.sh.j2"
    ).read_text(encoding="utf-8")

    assert "{% raw %}${#filtered_source_cidrs[@]}{% endraw %}" in template_text
    assert "{#" not in _remove_jinja_raw_blocks(template_text)


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
    assert "--mssql-enabled" in unit
    assert "--mssql-container-host {{ agent_sandbox_worker_mssql_container_host }}" in unit
    assert "--read-only-rootfs" in unit
    assert "--container-user {{ agent_sandbox_effective_worker_container_user }}" in unit
    assert "--readonly-mount {{ mount }}" in unit
    assert "{% endfor %}{{ \"\" }}\nRestart=on-failure" in unit


def test_worker_mssql_role_installs_sql_server_and_verifies_sqlcmd() -> None:
    role = (
        ROOT / "ansible" / "roles" / "worker_mssql" / "tasks" / "main.yml"
    ).read_text(encoding="utf-8")
    defaults = (
        ROOT / "ansible" / "group_vars" / "agent_sandbox_worker_hosts.yml"
    ).read_text(encoding="utf-8")

    assert "agent_sandbox_worker_mssql_accept_eula" in role
    assert "agent_sandbox_worker_use_apt_proxy | bool" in role
    assert "agent_sandbox_worker_apt_proxy_url" in role
    assert "agent_sandbox_worker_mssql_package_proxy_environment" in role
    assert "mssql-conf" in role
    assert "set-sa-password" in role
    assert "/var/opt/mssql/data/master.mdf" in role
    assert "mssql.conf" not in role
    assert "Stop SQL Server before reconciling SA password" in role
    assert "mssql-tools18" in defaults
    assert "https://packages.microsoft.com/keys/microsoft.asc" in defaults
    assert "https://packages.microsoft.com/ubuntu" not in defaults
    assert "http://packages.microsoft.com/ubuntu" in defaults
    assert "SQLCMDPASSWORD" in role
    assert "agent_sandbox_worker_mssql_allowed_source_cidr" in role
    assert "DROP" in role


def test_python_env_role_accepts_private_package_index_environment() -> None:
    role = (ROOT / "ansible" / "roles" / "python_env" / "tasks" / "main.yml").read_text(
        encoding="utf-8"
    )
    defaults = (ROOT / "ansible" / "group_vars" / "all" / "defaults.yml").read_text(
        encoding="utf-8"
    )

    assert "agent_sandbox_python_install_env: {}" in defaults
    assert "uv pip install" in role
    assert "environment: \"{{ agent_sandbox_python_install_env | default({}) }}\"" in role


def test_worker_package_cache_preflight_checks_private_pypi_asset_access() -> None:
    role = (
        ROOT
        / "ansible"
        / "roles"
        / "worker_package_cache_preflight"
        / "tasks"
        / "main.yml"
    ).read_text(encoding="utf-8")
    defaults = (ROOT / "ansible" / "group_vars" / "all" / "defaults.yml").read_text(
        encoding="utf-8"
    )

    assert "agent_sandbox_worker_validate_package_cache: true" in defaults
    assert "agent_sandbox_worker_package_cache_probe_url" in defaults
    assert "agent_sandbox_nexus_pypi_probe_asset_path" in defaults
    assert "Probe worker PyPI package-cache asset access" in role
    assert "agent_sandbox_worker_package_cache_probe_url" in role
    assert "status_code:" in role
    assert "- 403" in role
    assert "package_cache_nexus role creates the anonymous package-cache reader role" in role
    assert "Worker can download PyPI package assets through Nexus" in role


def test_worker_runtime_and_verification_roles_cover_docker_health_and_no_control_plane_units() -> (
    None
):
    runtime = (
        ROOT / "ansible" / "roles" / "container_worker_runtime" / "tasks" / "main.yml"
    ).read_text(encoding="utf-8")
    egress = (
        ROOT / "ansible" / "roles" / "worker_container_egress_policy" / "tasks" / "main.yml"
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
    assert "agent_sandbox_worker_egress_policy_active" in egress
    assert "agent-sandbox-worker-egress-policy.service" in egress
    assert "agent-sandbox-worker-daemon.service" in verification
    assert "agent-sandbox-worker-egress-policy.service" in verification
    assert "docker ps" not in verification
    assert "Verify Docker is usable by the worker user" in verification
    assert "/healthz" in verification
    assert "container user: {{ agent_sandbox_effective_worker_container_user }}" in verification
    assert "agent-sandbox-gateway.service" not in verification
    assert "agent-sandbox-admin-web.service" not in verification


def test_worker_node_toolchain_role_seeds_and_verifies_runner_node_cache() -> None:
    worker = (ROOT / "ansible" / "worker.yml").read_text(encoding="utf-8")
    role = (ROOT / "ansible" / "roles" / "worker_node_toolchain" / "tasks" / "main.yml").read_text(
        encoding="utf-8"
    )

    assert worker.index("- role: container_worker_runtime") < worker.index(
        "- role: worker_node_toolchain"
    )
    assert worker.index("- role: worker_node_toolchain") < worker.index(
        "- role: container_worker_service"
    )
    assert "Check local Node toolchain directory" in role
    assert "bin/node" in role
    assert "bin/npm" in role
    assert "bin/npx" in role
    assert "Pack local Node toolchain directory" in role
    assert "Copy Node toolchain archive to worker" in role
    assert "Unpack Node toolchain into worker cache" in role
    assert "Verify worker Node toolchain binaries on host cache" in role
    assert "Verify Node toolchain is available inside runner container" in role
    assert "agent_sandbox_runner_image_name" in role
    assert "/opt/agent-sandbox/toolchains/node-v24.14.0/bin" in role
    assert "node --version && npm --version && npx --version" in role


def test_worker_dotnet_toolchain_role_seeds_and_verifies_runner_dotnet_cache() -> None:
    worker = (ROOT / "ansible" / "worker.yml").read_text(encoding="utf-8")
    role = (
        ROOT / "ansible" / "roles" / "worker_dotnet_toolchain" / "tasks" / "main.yml"
    ).read_text(encoding="utf-8")

    assert worker.index("- role: container_worker_runtime") < worker.index(
        "- role: worker_dotnet_toolchain"
    )
    assert worker.index("- role: worker_dotnet_toolchain") < worker.index(
        "- role: worker_node_toolchain"
    )
    assert worker.index("- role: worker_dotnet_toolchain") < worker.index(
        "- role: container_worker_service"
    )
    assert "Check local dotnet toolchain directory" in role
    assert "/dotnet" in role
    assert "Pack local dotnet toolchain directory" in role
    assert "Install dotnet EF tool into local staging directory" in role
    assert "Pack local dotnet tools directory" in role
    assert "Restore local dotnet restore support package into staging directory" in role
    assert "agent_sandbox_worker_nuget_fallback_archive_path/HOST_NUGET_FALLBACK_ARCHIVE" in role
    assert "Pack local dotnet restore support packages" in role
    assert "Copy dotnet toolchain archive to worker" in role
    assert "Copy dotnet tools archive to worker" in role
    assert "Copy dotnet restore support archive to worker" in role
    assert "Unpack dotnet toolchain into worker cache" in role
    assert "Unpack dotnet tools into worker cache" in role
    assert "Unpack dotnet restore support packages into worker NuGet fallback cache" in role
    assert "Verify worker dotnet toolchain on host cache" in role
    assert "Verify dotnet, EF tooling, and restore support inside runner container" in role
    assert "agent_sandbox_runner_image_name" in role
    assert "/opt/agent-sandbox/toolchains/dotnet" in role
    assert "/opt/agent-sandbox/toolchains/dotnet-tools" in role
    assert "/opt/agent-sandbox/nuget-cache-ro:ro" in role
    assert "dotnet --info" in role
    assert "dotnet ef --version" in role
    assert "dotnet-ef --version" in role
    assert "dotnet restore \"$smoke_dir/restore-smoke.csproj\"" in role
    assert "agent_sandbox_worker_dotnet_restore_support_package_id" in role
    assert "DOTNET_SKIP_WORKLOAD_MANIFEST_UPDATE" in role
    assert "DOTNET_CLI_WORKLOAD_UPDATE_NOTIFY_DISABLE" in role
    assert "DOTNET_NOLOGO" in role


def test_worker_playwright_toolchain_role_seeds_and_verifies_runner_playwright_cache() -> None:
    worker = (ROOT / "ansible" / "worker.yml").read_text(encoding="utf-8")
    role = (
        ROOT / "ansible" / "roles" / "worker_playwright_toolchain" / "tasks" / "main.yml"
    ).read_text(encoding="utf-8")

    assert worker.index("- role: worker_node_toolchain") < worker.index(
        "- role: worker_playwright_toolchain"
    )
    assert worker.index("- role: worker_playwright_toolchain") < worker.index(
        "- role: container_worker_service"
    )
    assert "Check local Playwright node_modules directory" in role
    assert "playwright-core" in role
    assert "Pack local Playwright runtime packages" in role
    assert "Copy Playwright runtime archive to worker" in role
    assert "Copy Playwright browsers archive to worker" in role
    assert "Unpack Playwright runtime into worker cache" in role
    assert "Unpack Playwright browsers into worker cache" in role
    assert "Verify worker Playwright runtime on host cache" in role
    assert "Verify Playwright is available inside runner container" in role
    assert "agent_sandbox_worker_node_cache_path" in role
    assert "/opt/agent-sandbox/playwright-runtime" in role
    assert "/opt/agent-sandbox/ms-playwright" in role
    assert "PLAYWRIGHT_MCP_EXECUTABLE_PATH" in role
    assert "PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD" in role
    assert 'path.join(runtime, "playwright")' in role
    assert "chromium.executablePath()" in role


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
    assert "agent-sandbox.mirrorAutoRefresh" in role
    assert "Publish requested control-plane revision to deployment repo mirror" in role
    assert '"--git-dir={{ agent_sandbox_worker_repo_mirror_path }}"' in role
    assert "fetch\n      - --force" in role
    assert "refs/heads/deploy/agent-sandbox" in role
    assert "agent_sandbox_control_plane_repo_revision_check.stdout" in role
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
    assert "container_worker_guest_http_target_relay_base_url" in ansible_docs
    assert "container_worker_guest_pypi_index_url" in ansible_docs
    assert "container_worker_guest_npm_registry_url" in ansible_docs
    assert "agent_sandbox_npm_audit_facade_port" in ansible_docs
    assert "agent_sandbox_services.npm_audit_facade" in ansible_docs
    assert "config/npm-audit-facade.env" in ansible_docs
    assert "local playbook commit" in ansible_docs
    assert "agent_sandbox_worker_enforce_revision_match: false" in ansible_docs
    assert "Worker outbound rules should still deny direct access" in ansible_docs
    assert "allowed Git source prefix" in ansible_docs
    assert "GitHub/Azure DevOps egress" in ansible_docs
    assert "agent_sandbox_worker_egress_policy_enabled: true" in ansible_docs
    assert "agent_sandbox_worker_docker_network: \"bridge\"" in ansible_docs
    assert "agent-sandbox-worker-egress-policy.service" in ansible_docs
    assert "sudo systemctl status agent-sandbox-worker-egress-policy.service" in ansible_docs
    assert "sudo iptables -S DOCKER-USER" in ansible_docs
    assert "agent-sandbox-cli execution smoke-container-worker --json" in ansible_docs
    assert "169.254.169.254" in ansible_docs
    assert "smoke-container-worker" in ansible_docs
    assert "ansible/worker.yml" in worker_docs
    assert "Phase 5 first slice" in worker_docs
    assert "single-worker" in worker_docs
    assert "DOCKER-USER" in worker_docs
    assert "agent-sandbox-npm-audit-facade" in worker_docs
    assert "dedicated container-worker VM playbook" in readme
    assert "dedicated container-worker VM playbook" in operations
