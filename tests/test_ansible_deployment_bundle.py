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
        "admin_web_caddy",
        "network_vm_bootstrap",
        "service_runtime",
        "apt_cache_relay",
        "package_cache_nexus",
        "semantic_store_qdrant",
        "worker_apt_proxy",
        "worker_git_access",
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
    assert "- role: apt_cache_relay" in site
    assert "- role: repo_checkout" in site
    assert "- role: python_env" in site
    assert "- role: runtime_bootstrap" in site
    assert "- role: service_config" in site
    assert "- role: systemd_install" in site
    assert "- role: frontend_build" in site
    assert "- role: admin_web_caddy" in site
    assert "- role: network_vm_bootstrap" in site
    assert "- role: service_runtime" in site
    assert "- role: package_cache_nexus" in site
    assert "- role: semantic_store_qdrant" in site
    assert "- role: verification" in site
    assert site.index("- role: host_prereqs") < site.index("- role: apt_cache_relay")
    assert site.index("- role: apt_cache_relay") < site.index("- role: repo_checkout")
    assert site.index("- role: frontend_build") < site.index("- role: admin_web_caddy")
    assert site.index("- role: admin_web_caddy") < site.index("- role: service_runtime")
    assert site.index("- role: package_cache_nexus") < site.index("- role: semantic_store_qdrant")
    assert site.index("- role: semantic_store_qdrant") < site.index("- role: verification")


def test_ansible_admin_web_caddy_role_serves_static_assets_directly() -> None:
    repo = Path(__file__).resolve().parents[1]
    defaults = (repo / "ansible" / "group_vars" / "all" / "defaults.yml").read_text(
        encoding="utf-8"
    )
    azure = (repo / "ansible" / "group_vars" / "azure.yml").read_text(encoding="utf-8")
    tasks = (
        repo / "ansible" / "roles" / "admin_web_caddy" / "tasks" / "main.yml"
    ).read_text(encoding="utf-8")
    caddyfile = (
        repo / "ansible" / "roles" / "admin_web_caddy" / "templates" / "Caddyfile.j2"
    ).read_text(encoding="utf-8")
    verification = (
        repo / "ansible" / "roles" / "verification" / "tasks" / "main.yml"
    ).read_text(encoding="utf-8")

    assert "agent_sandbox_admin_web_frontend_dist_dir" in defaults
    assert "agent_sandbox_admin_web_caddy_enabled: false" in defaults
    assert "agent_sandbox_admin_web_caddy_verification_connect_timeout_seconds: 5" in defaults
    assert "agent_sandbox_admin_web_caddy_verification_max_time_seconds: 20" in defaults
    assert "agent_sandbox_admin_web_caddy_verification_retries: 6" in defaults
    assert "agent_sandbox_admin_web_caddy_verification_delay_seconds: 10" in defaults
    assert "agent_sandbox_admin_web_caddy_enabled: true" in azure
    assert "Install Caddy package" in tasks
    assert "Assert Admin Web frontend assets were built" in tasks
    assert "Render Admin Web Caddyfile" in tasks
    assert "caddy validate --adapter caddyfile --config" in tasks
    assert "Reload Caddy after Caddyfile changes" in tasks

    assert "handle /api/*" in caddyfile
    assert "handle /healthz" in caddyfile
    assert (
        "reverse_proxy {{ agent_sandbox_admin_web_caddy_upstream_host }}:{{ agent_sandbox_admin_web_caddy_upstream_port }}"
        in caddyfile
    )
    assert "root * {{ agent_sandbox_admin_web_frontend_dist_dir }}" in caddyfile
    assert "try_files {path} /index.html" in caddyfile
    assert "file_server" in caddyfile

    assert "Verify managed Admin Web Caddy is active" in verification
    assert "Verify managed Admin Web Caddy serves frontend and proxies health" in verification
    assert "--resolve" in verification
    assert "--connect-timeout" in verification
    assert "agent_sandbox_admin_web_caddy_verification_max_time_seconds" in verification
    assert "register: agent_sandbox_admin_web_caddy_frontend_probe" in verification
    assert "retries: \"{{ agent_sandbox_admin_web_caddy_verification_retries | int }}\"" in verification
    assert "until: agent_sandbox_admin_web_caddy_frontend_probe.rc == 0" in verification
    assert "https://${host}/index.html" in verification
    assert 'find "${dist_dir}/assets"' in verification
    assert "No built ${extension} asset found" in verification


def test_ansible_site_announces_control_plane_deployment_status() -> None:
    repo = Path(__file__).resolve().parents[1]
    site = (repo / "ansible" / "site.yml").read_text(encoding="utf-8")
    defaults = (repo / "ansible" / "group_vars" / "all" / "defaults.yml").read_text(
        encoding="utf-8"
    )

    assert "agent_sandbox_deployment_status_path" in defaults
    assert "deployment-status.json" in defaults
    assert "agent_sandbox_deployment_status_message" in defaults
    assert "agent_sandbox_deployment_status_ttl_seconds" in defaults
    assert "Check whether an existing runtime directory is present" in site
    assert "Announce deployment in control-plane runtime" in site
    assert '"source": "ansible"' in site
    assert "strftime('%Y-%m-%dT%H:%M:%SZ')" in site
    assert "Clear deployment status runtime flag" in site
    assert site.index("Announce deployment in control-plane runtime") < site.index(
        "- role: host_prereqs"
    )


def test_ansible_nexus_package_cache_role_is_control_plane_only() -> None:
    repo = Path(__file__).resolve().parents[1]
    site = (repo / "ansible" / "site.yml").read_text(encoding="utf-8")
    worker = (repo / "ansible" / "worker.yml").read_text(encoding="utf-8")
    role = repo / "ansible" / "roles" / "package_cache_nexus"

    assert "- role: package_cache_nexus" in site
    assert "package_cache_nexus" not in worker
    assert (role / "tasks" / "main.yml").exists()
    assert (role / "templates" / "agent-sandbox-nexus.service.j2").exists()


def test_ansible_apt_cache_relay_role_is_control_plane_only() -> None:
    repo = Path(__file__).resolve().parents[1]
    site = (repo / "ansible" / "site.yml").read_text(encoding="utf-8")
    worker = (repo / "ansible" / "worker.yml").read_text(encoding="utf-8")
    role = repo / "ansible" / "roles" / "apt_cache_relay"

    assert "- role: apt_cache_relay" in site
    assert "apt_cache_relay" not in worker
    assert (role / "tasks" / "main.yml").exists()


def test_ansible_qdrant_semantic_store_role_is_control_plane_only() -> None:
    repo = Path(__file__).resolve().parents[1]
    site = (repo / "ansible" / "site.yml").read_text(encoding="utf-8")
    worker = (repo / "ansible" / "worker.yml").read_text(encoding="utf-8")
    role = repo / "ansible" / "roles" / "semantic_store_qdrant"

    assert "- role: semantic_store_qdrant" in site
    assert "semantic_store_qdrant" not in worker
    assert (role / "tasks" / "main.yml").exists()
    assert (role / "templates" / "agent-sandbox-qdrant.service.j2").exists()


def test_ansible_host_prereqs_can_install_azure_cli_for_control_plane() -> None:
    repo = Path(__file__).resolve().parents[1]
    defaults = (repo / "ansible" / "group_vars" / "all" / "defaults.yml").read_text(
        encoding="utf-8"
    )
    azure = (repo / "ansible" / "group_vars" / "azure.yml").read_text(encoding="utf-8")
    tasks = (repo / "ansible" / "roles" / "host_prereqs" / "tasks" / "main.yml").read_text(
        encoding="utf-8"
    )

    assert "agent_sandbox_install_azure_cli: false" in defaults
    assert "agent_sandbox_azure_cli_deb_architecture" in defaults
    assert "agent_sandbox_install_azure_cli: true" in azure
    assert "Download Microsoft signing key for Azure CLI" in tasks
    assert "https://packages.microsoft.com/repos/azure-cli/" in tasks
    assert "name: azure-cli" in tasks
    assert "when: agent_sandbox_install_azure_cli | bool" in tasks


def test_ansible_nexus_package_cache_tasks_are_not_ignored_by_git() -> None:
    repo = Path(__file__).resolve().parents[1]
    role_task_path = "ansible/roles/package_cache_nexus/tasks/main.yml"

    result = subprocess.run(
        ["git", "check-ignore", "--quiet", "--", role_task_path],
        cwd=repo,
        check=False,
    )

    assert result.returncode == 1


def test_ansible_apt_cache_role_tasks_are_not_ignored_by_git() -> None:
    repo = Path(__file__).resolve().parents[1]
    role_task_path = "ansible/roles/apt_cache_relay/tasks/main.yml"

    result = subprocess.run(
        ["git", "check-ignore", "--quiet", "--", role_task_path],
        cwd=repo,
        check=False,
    )

    assert result.returncode == 1


def test_ansible_qdrant_semantic_store_tasks_are_not_ignored_by_git() -> None:
    repo = Path(__file__).resolve().parents[1]
    role_task_path = "ansible/roles/semantic_store_qdrant/tasks/main.yml"

    result = subprocess.run(
        ["git", "check-ignore", "--quiet", "--", role_task_path],
        cwd=repo,
        check=False,
    )

    assert result.returncode == 1


def test_ansible_admin_web_caddy_role_tasks_are_not_ignored_by_git() -> None:
    repo = Path(__file__).resolve().parents[1]
    role_task_path = "ansible/roles/admin_web_caddy/tasks/main.yml"

    result = subprocess.run(
        ["git", "check-ignore", "--quiet", "--", role_task_path],
        cwd=repo,
        check=False,
    )

    assert result.returncode == 1


def test_ansible_worker_apt_proxy_tasks_are_not_ignored_by_git() -> None:
    repo = Path(__file__).resolve().parents[1]
    role_task_path = "ansible/roles/worker_apt_proxy/tasks/main.yml"

    result = subprocess.run(
        ["git", "check-ignore", "--quiet", "--", role_task_path],
        cwd=repo,
        check=False,
    )

    assert result.returncode == 1


def test_ansible_worker_git_access_tasks_are_not_ignored_by_git() -> None:
    repo = Path(__file__).resolve().parents[1]
    role_task_path = "ansible/roles/worker_git_access/tasks/main.yml"

    result = subprocess.run(
        ["git", "check-ignore", "--quiet", "--", role_task_path],
        cwd=repo,
        check=False,
    )

    assert result.returncode == 1


def test_ansible_nexus_package_cache_defaults_and_role_contract() -> None:
    repo = Path(__file__).resolve().parents[1]
    defaults = (repo / "ansible" / "group_vars" / "all" / "defaults.yml").read_text(
        encoding="utf-8"
    )
    tasks = (
        repo / "ansible" / "roles" / "package_cache_nexus" / "tasks" / "main.yml"
    ).read_text(encoding="utf-8")
    unit = (
        repo
        / "ansible"
        / "roles"
        / "package_cache_nexus"
        / "templates"
        / "agent-sandbox-nexus.service.j2"
    ).read_text(encoding="utf-8")

    assert "package_cache: true" in defaults
    assert 'agent_sandbox_nexus_version: "3.91.1-04"' in defaults
    assert "agent_sandbox_nexus_port: 8081" in defaults
    assert 'agent_sandbox_nexus_bind_host: "0.0.0.0"' in defaults
    assert "agent_sandbox_nexus_accept_community_eula: true" in defaults
    assert "agent_sandbox_nexus_community_eula_disclaimer" not in defaults
    assert 'agent_sandbox_nexus_pypi_proxy_repository: "pypi-proxy"' in defaults
    assert 'agent_sandbox_nexus_pypi_group_repository: "pypi-all"' in defaults
    assert 'agent_sandbox_nexus_npm_proxy_repository: "npm-proxy"' in defaults
    assert 'agent_sandbox_nexus_npm_group_repository: "npm-all"' in defaults
    assert (
        'agent_sandbox_nexus_anonymous_reader_role: "agent-sandbox-package-cache-reader"'
        in defaults
    )
    assert (
        'agent_sandbox_nexus_pypi_probe_asset_path: "packages/wheel/0.47.0/wheel-0.47.0-py3-none-any.whl"'
        in defaults
    )
    assert "agent_sandbox_package_cache_allowed_tcp_ports" in defaults
    assert "[agent_sandbox_nexus_port]" in defaults
    assert "agent_sandbox_worker_pypi_index_url" in defaults
    assert "agent_sandbox_worker_npm_registry_url" in defaults

    assert "agent_sandbox_nexus_admin_password must be set from Vault" in tasks
    assert "quiet: true" in tasks
    assert "Assert Nexus admin password is configured\n" in tasks
    assert "Read Nexus Community Edition EULA status" in tasks
    assert "Accept Nexus Community Edition EULA" in tasks
    assert "service/rest/v1/system/eula" in tasks
    assert "agent_sandbox_nexus_community_eula_status.json.disclaimer" in tasks
    assert "agent_sandbox_nexus_community_eula_disclaimer" not in tasks
    assert "no_log: true\n\n    - name: Ensure Nexus service group exists" not in tasks
    assert "Download pinned Nexus archive" in tasks
    assert "agent-sandbox-nexus.service" in tasks
    assert "/service/rest/v1/security/users\"" in tasks
    assert "/service/rest/v1/security/user\"" not in tasks
    assert "service/rest/v1/security/anonymous" in tasks
    assert (
        "service/rest/v1/security/roles/{{ agent_sandbox_nexus_anonymous_reader_role }}"
        in tasks
    )
    assert "service/rest/v1/security/users/anonymous" in tasks
    assert "Probe anonymous Nexus PyPI asset access" in tasks
    assert "agent_sandbox_nexus_pypi_probe_asset_path" in tasks
    assert "Nexus anonymous PyPI asset access is working" in tasks
    assert (
        "nx-repository-view-pypi-{{ agent_sandbox_nexus_pypi_group_repository }}-read"
        in tasks
    )
    assert (
        "nx-repository-view-pypi-{{ agent_sandbox_nexus_pypi_proxy_repository }}-read"
        in tasks
    )
    assert (
        "nx-repository-view-npm-{{ agent_sandbox_nexus_npm_group_repository }}-read"
        in tasks
    )
    assert (
        "nx-repository-view-npm-{{ agent_sandbox_nexus_npm_proxy_repository }}-read"
        in tasks
    )
    assert "source=default" in tasks
    assert "repositories/pypi/proxy" in tasks
    assert "repositories/pypi/group" in tasks
    assert "repositories/npm/proxy" in tasks
    assert "repositories/npm/group" in tasks
    assert "https://pypi.org/" in defaults
    assert "https://registry.npmjs.org" in defaults
    assert "ExecStart={{ agent_sandbox_nexus_current_link }}/bin/nexus run" in unit
    assert "User={{ agent_sandbox_nexus_user }}" in unit


def test_ansible_apt_cache_relay_defaults_and_role_contract() -> None:
    repo = Path(__file__).resolve().parents[1]
    defaults = (repo / "ansible" / "group_vars" / "all" / "defaults.yml").read_text(
        encoding="utf-8"
    )
    tasks = (
        repo / "ansible" / "roles" / "apt_cache_relay" / "tasks" / "main.yml"
    ).read_text(encoding="utf-8")
    verification = (
        repo / "ansible" / "roles" / "verification" / "tasks" / "main.yml"
    ).read_text(encoding="utf-8")

    assert "apt_cache: true" in defaults
    assert "agent_sandbox_apt_cache_port: 3142" in defaults
    assert 'agent_sandbox_apt_cache_bind_host: "0.0.0.0"' in defaults
    assert 'agent_sandbox_apt_cache_cache_dir: "/var/cache/apt-cacher-ng"' in defaults
    assert (
        'agent_sandbox_apt_cache_passthrough_pattern: "^(packages\\\\.microsoft\\\\.com):443$"'
        in defaults
    )
    assert "agent_sandbox_apt_cache_allowed_tcp_ports" in defaults
    assert "[agent_sandbox_apt_cache_port]" in defaults

    assert "Install apt-cacher-ng package" in tasks
    assert "name: apt-cacher-ng" in tasks
    assert "Port: {{ agent_sandbox_apt_cache_port }}" in tasks
    assert "BindAddress: {{ agent_sandbox_apt_cache_bind_host }}" in tasks
    assert "CacheDir: {{ agent_sandbox_apt_cache_cache_dir }}" in tasks
    assert "PassThroughPattern: {{ agent_sandbox_apt_cache_passthrough_pattern }}" in tasks
    assert "apt-cacher-ng.service" in tasks
    assert "acng-report.html" in tasks

    assert "apt-cacher-ng.service" in verification
    assert "agent_sandbox_apt_cache_port" in verification
    assert "http://127.0.0.1:{{ agent_sandbox_apt_cache_port }}/acng-report.html" in verification


def test_ansible_qdrant_semantic_store_defaults_and_role_contract() -> None:
    repo = Path(__file__).resolve().parents[1]
    defaults = (repo / "ansible" / "group_vars" / "all" / "defaults.yml").read_text(
        encoding="utf-8"
    )
    tasks = (
        repo / "ansible" / "roles" / "semantic_store_qdrant" / "tasks" / "main.yml"
    ).read_text(encoding="utf-8")
    unit = (
        repo
        / "ansible"
        / "roles"
        / "semantic_store_qdrant"
        / "templates"
        / "agent-sandbox-qdrant.service.j2"
    ).read_text(encoding="utf-8")
    verification = (
        repo / "ansible" / "roles" / "verification" / "tasks" / "main.yml"
    ).read_text(encoding="utf-8")

    assert "semantic_store: true" in defaults
    assert 'agent_sandbox_qdrant_image: "qdrant/qdrant:v1.18.0"' in defaults
    assert 'agent_sandbox_qdrant_data_dir: "/var/lib/agent-sandbox/qdrant"' in defaults
    assert 'agent_sandbox_qdrant_bind_host: "127.0.0.1"' in defaults
    assert "agent_sandbox_qdrant_port: 6333" in defaults
    assert "guest/worker firewall allowances" in defaults

    assert "Stop Qdrant semantic store when disabled" in tasks
    assert "agent-sandbox-qdrant.service" in tasks
    assert "Install Docker for Qdrant semantic store" in tasks
    assert "name: docker.io" in tasks
    assert "Pull pinned Qdrant semantic store image" in tasks
    assert "docker\n          - pull" in tasks
    assert "agent_sandbox_qdrant_image" in tasks
    assert "Wait for Qdrant semantic store listener" in tasks
    assert "Verify Qdrant semantic store health endpoint" in tasks
    assert "/healthz" in tasks

    assert "Requires=docker.service" in unit
    assert "docker run --name agent-sandbox-qdrant" in unit
    assert (
        "--publish {{ agent_sandbox_qdrant_bind_host }}:{{ agent_sandbox_qdrant_port }}:6333"
        in unit
    )
    assert "--volume {{ agent_sandbox_qdrant_data_dir }}:/qdrant/storage" in unit

    assert "agent-sandbox-qdrant.service" in verification
    assert "agent_sandbox_services.get(item.key, false) | bool" in verification
    assert "agent_sandbox_services[item.key]" not in verification
    assert "agent_sandbox_qdrant_bind_host" in verification
    assert "agent_sandbox_qdrant_port" in verification
    assert (
        "http://{{ agent_sandbox_qdrant_bind_host }}:{{ agent_sandbox_qdrant_port }}/healthz"
        in verification
    )


def test_ansible_azure_inventory_maps_nexus_password_from_vault() -> None:
    repo = Path(__file__).resolve().parents[1]
    azure = (repo / "ansible" / "group_vars" / "azure.yml").read_text(encoding="utf-8")
    inventory = (repo / "ansible" / "inventory" / "azure.yml").read_text(encoding="utf-8")
    docs = (repo / "ansible" / "README.md").read_text(encoding="utf-8")

    assert (
        'agent_sandbox_nexus_admin_password: "{{ vault_agent_sandbox_nexus_admin_password | default(\'\') }}"'
        in azure
    )
    assert 'agent_sandbox_control_plane_private_ip: "10.2.0.4"' in azure
    assert "agent_sandbox_control_plane_private_ip" not in inventory
    assert "vault_agent_sandbox_nexus_admin_password" in docs


def test_ansible_worker_egress_policy_defaults_are_private_control_plane_endpoints() -> None:
    repo = Path(__file__).resolve().parents[1]
    defaults = (repo / "ansible" / "group_vars" / "all" / "defaults.yml").read_text(
        encoding="utf-8"
    )
    worker_defaults = (
        repo / "ansible" / "group_vars" / "agent_sandbox_worker_hosts.yml"
    ).read_text(encoding="utf-8")
    azure = (repo / "ansible" / "group_vars" / "azure.yml").read_text(encoding="utf-8")
    azure_inventory = (repo / "ansible" / "inventory" / "azure.yml").read_text(
        encoding="utf-8"
    )

    assert 'agent_sandbox_worker_docker_network: "none"' in worker_defaults
    assert "agent_sandbox_worker_egress_policy_enabled: false" in worker_defaults
    assert "agent_sandbox_worker_egress_policy_chain" in worker_defaults
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
    assert "name: control-plane nexus package cache" in defaults
    assert "name: control-plane apt-cacher-ng" in defaults
    assert "port: 22" in defaults
    assert "port: 8000" in defaults
    assert "port: 11434" in defaults
    assert "port: 11666" in defaults
    assert "port: 11777" in defaults
    assert 'port: "{{ agent_sandbox_nexus_port }}"' in defaults
    assert 'port: "{{ agent_sandbox_apt_cache_port }}"' in defaults
    assert 'host: "{{ agent_sandbox_control_plane_private_ip }}"' in defaults
    assert 'agent_sandbox_worker_docker_network: "bridge"' not in azure
    assert "agent_sandbox_worker_egress_policy_enabled: true" not in azure
    assert 'agent_sandbox_worker_docker_network: "bridge"' in azure_inventory
    assert "agent_sandbox_worker_egress_policy_enabled: true" in azure_inventory


def test_worker_apt_proxy_runs_before_host_prereqs_and_can_be_disabled() -> None:
    repo = Path(__file__).resolve().parents[1]
    worker = (repo / "ansible" / "worker.yml").read_text(encoding="utf-8")
    defaults = (repo / "ansible" / "group_vars" / "all" / "defaults.yml").read_text(
        encoding="utf-8"
    )
    worker_defaults = (
        repo / "ansible" / "group_vars" / "agent_sandbox_worker_hosts.yml"
    ).read_text(encoding="utf-8")
    tasks = (
        repo / "ansible" / "roles" / "worker_apt_proxy" / "tasks" / "main.yml"
    ).read_text(encoding="utf-8")
    template = (
        repo
        / "ansible"
        / "roles"
        / "worker_apt_proxy"
        / "templates"
        / "01agent-sandbox-proxy.j2"
    ).read_text(encoding="utf-8")

    assert "- role: worker_apt_proxy" in worker
    assert worker.index("- role: worker_apt_proxy") < worker.index("- role: host_prereqs")
    assert "agent_sandbox_worker_use_apt_proxy: true" in defaults
    assert (
        'agent_sandbox_worker_apt_proxy_url: "http://{{ agent_sandbox_control_plane_private_ip }}:{{ agent_sandbox_apt_cache_port }}"'
        in defaults
    )
    assert "/etc/apt/apt.conf.d/01agent-sandbox-proxy" in defaults
    assert "ansible/roles/worker_apt_proxy" in worker_defaults

    assert "Remove managed worker apt proxy when disabled" in tasks
    assert "state: absent" in tasks
    assert "when: not (agent_sandbox_worker_use_apt_proxy | bool)" in tasks
    assert "Render worker apt proxy config" in tasks
    assert 'Acquire::http::Proxy "{{ agent_sandbox_worker_apt_proxy_url }}";' in template
    assert 'Acquire::https::Proxy "{{ agent_sandbox_worker_apt_proxy_https_url }}";' in template


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
        "config/scheduled-wake/config.json",
        "config/teams-ingest/config.json",
        "config/teams-meeting-ingest/config.json",
    ]
    example_paths = [
        "config/ado-worker/config.json.example",
        "config/discord-ingest/config.json.example",
        "config/discord-wake/config.json.example",
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


def test_ansible_ado_worker_defaults_omit_retired_setup_keys() -> None:
    repo = Path(__file__).resolve().parents[1]
    defaults = (repo / "ansible" / "group_vars" / "all" / "defaults.yml").read_text(
        encoding="utf-8"
    )

    assert "agent_sandbox_ado_worker_config:" in defaults
    assert "processed_store_path:" in defaults
    assert "setup_log_dir:" not in defaults
    assert "setup_script_path:" not in defaults
    assert "setup_timeout_seconds:" not in defaults
    assert "setup_concurrency_limit:" not in defaults
    assert "setup_lock_path:" not in defaults


def test_ansible_manages_proxy_issue_sync_service() -> None:
    repo = Path(__file__).resolve().parents[1]
    defaults = (repo / "ansible" / "group_vars" / "all" / "defaults.yml").read_text(
        encoding="utf-8"
    )
    service_runtime = (
        repo / "ansible" / "roles" / "service_runtime" / "tasks" / "main.yml"
    ).read_text(encoding="utf-8")
    verification = (
        repo / "ansible" / "roles" / "verification" / "tasks" / "main.yml"
    ).read_text(encoding="utf-8")

    assert "proxy_issue_sync: true" in defaults
    assert "key: proxy_issue_sync" in service_runtime
    assert "unit: agent-sandbox-proxy-issue-sync.service" in service_runtime
    assert "key: proxy_issue_sync" in verification
    assert "unit: agent-sandbox-proxy-issue-sync.service" in verification


def test_ansible_systemd_install_passes_service_profile() -> None:
    repo = Path(__file__).resolve().parents[1]
    text = (repo / "ansible" / "roles" / "systemd_install" / "tasks" / "main.yml").read_text(
        encoding="utf-8"
    )

    assert "AGENT_SANDBOX_SERVICE_PROFILE" in text
    assert "{{ agent_sandbox_service_profile }}" in text


def test_ansible_verification_checks_nexus_package_cache() -> None:
    repo = Path(__file__).resolve().parents[1]
    verification = (
        repo / "ansible" / "roles" / "verification" / "tasks" / "main.yml"
    ).read_text(encoding="utf-8")

    assert "agent-sandbox-nexus.service" in verification
    assert "agent_sandbox_nexus_port" in verification
    assert "http://127.0.0.1:{{ agent_sandbox_nexus_port }}/service/rest/v1/status" in verification
