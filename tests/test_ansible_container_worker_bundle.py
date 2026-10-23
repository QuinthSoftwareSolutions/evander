import subprocess
from pathlib import Path

import pytest
import yaml
from jinja2 import StrictUndefined
from jinja2.nativetypes import NativeEnvironment


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "health,expected_state",
    [
        ({}, "restarted"),
        ({"json": {"status": "ok"}}, "restarted"),
        ({"json": {"deployment_revision": "old-revision"}}, "restarted"),
        ({"json": {"deployment_revision": "new-revision"}}, "started"),
    ],
)
def test_worker_deploy_reconciles_running_code_even_when_files_are_unchanged(
    health, expected_state,
) -> None:
    tasks = yaml.safe_load(
        (ROOT / "ansible/roles/container_worker_service/tasks/main.yml").read_text()
    )
    reconcile = next(t for t in tasks if t["name"] == "Reconcile worker daemon service")
    environment = NativeEnvironment(undefined=StrictUndefined)
    state = environment.from_string(reconcile["ansible.builtin.systemd"]["state"]).render(
        evander_worker_env_render={"changed": False},
        evander_worker_unit_render={"changed": False},
        evander_worker_current_health=health,
        evander_worker_deployed_revision={"stdout": "new-revision"},
    )
    assert state.strip() == expected_state


@pytest.mark.parametrize(
    "health,accepted",
    [
        ({"status": 200, "json": {"status": "ok"}}, False),
        (
            {
                "status": 200,
                "json": {
                    "deployment_revision": "old-revision",
                    "capabilities": {"codex_fast_mode": 1, "codex_ultrafast_mode": 1},
                },
            },
            False,
        ),
        ({"status": 200, "json": {"deployment_revision": "new-revision"}}, False),
        (
            {
                "status": 200,
                "json": {
                    "deployment_revision": "new-revision",
                    "capabilities": {"codex_fast_mode": 1},
                },
            },
            False,
        ),
        (
            {
                "status": 200,
                "json": {
                    "deployment_revision": "new-revision",
                    "capabilities": {"codex_fast_mode": 1, "codex_ultrafast_mode": 1},
                },
            },
            True,
        ),
    ],
)
def test_worker_deploy_verification_requires_loaded_revision_and_speed_modes(health, accepted) -> None:
    tasks = yaml.safe_load(
        (ROOT / "ansible/roles/container_worker_verification/tasks/main.yml").read_text()
    )
    verify = next(t for t in tasks if t["name"] == "Verify worker daemon health endpoint")
    environment = NativeEnvironment(undefined=StrictUndefined)
    conditions = verify.get("until", [])
    assert conditions, "An HTTP 200 from the previous daemon does not verify the deployment"
    results = [
        environment.from_string("{{ " + condition + " }}").render(
            evander_worker_health=health,
            evander_worker_deployed_revision={"stdout": "new-revision"},
        )
        for condition in conditions
    ]
    assert all(results) is accepted


def test_worker_playbook_exists_with_expected_roles() -> None:
    ansible_dir = ROOT / "ansible"
    worker = (ansible_dir / "worker.yml").read_text(encoding="utf-8")

    assert "hosts: evander_worker_hosts" in worker
    assert "Resolve local worker playbook revision" in worker
    assert "Default worker repo checkout to the playbook revision" in worker
    assert "Assert worker playbook and daemon checkout revisions match" in worker
    assert "evander_worker_enforce_revision_match=false" in worker
    assert "evander_worker_token must be set from Vault or inventory" in worker
    assert "evander_worker_sandbox_profile in ['default', 'strict']" in worker
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
    assert "- role: worker_mssql_client_toolchain" in worker
    assert "- role: worker_container_egress_policy" in worker
    assert "- role: container_worker_service" in worker
    assert "- role: container_worker_verification" in worker
    assert "Resolve worker Docker network and egress allowlist" in worker
    assert "Assert non-none worker Docker networking has egress policy" in worker
    assert "evander_worker_docker_network=none for isolated containers" in worker
    assert "Assert worker egress allowlist endpoints are concrete" in worker
    assert worker.index("- role: container_worker_runtime") < worker.index("- role: worker_mssql")
    assert worker.index("- role: worker_mssql") < worker.index(
        "- role: worker_mssql_client_toolchain"
    )
    assert worker.index("- role: worker_mssql_client_toolchain") < worker.index(
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
        "worker_mssql_client_toolchain",
        "worker_container_egress_policy",
        "container_worker_service",
        "container_worker_verification",
    ]:
        assert (ansible_dir / "roles" / role_name / "tasks" / "main.yml").exists()


def test_worker_group_vars_define_docker_and_durable_state_contract() -> None:
    text = (ROOT / "ansible" / "group_vars" / "evander_worker_hosts.yml").read_text(
        encoding="utf-8"
    )

    assert 'evander_deploy_path: "/opt/evander-worker"' in text
    assert 'evander_run_user: "agent-worker"' in text
    assert 'evander_worker_repo_version_mode: "playbook_revision"' in text
    assert "evander_worker_enforce_revision_match: true" in text
    assert "evander_worker_revision_guard_paths" in text
    assert "src/evander/worker" in text
    assert "src/evander/codex_capacity.py" in text
    assert "ansible/roles/python_env" in text
    assert "ansible/roles/worker_package_cache_preflight" in text
    assert "ansible/roles/worker_dotnet_toolchain" in text
    assert "ansible/roles/worker_node_toolchain" in text
    assert "ansible/roles/worker_playwright_toolchain" in text
    assert "ansible/roles/worker_mssql" in text
    assert "ansible/roles/worker_mssql_client_toolchain" in text
    assert "ansible/roles/worker_container_egress_policy" in text
    assert "ansible/roles/container_worker_service" in text
    assert "  - docker" in text
    assert "  - docker.io" in text
    assert 'evander_python_install_extras: ""' in text
    assert "evander_python_install_env" in text
    assert "UV_DEFAULT_INDEX" in text
    assert "UV_INSECURE_HOST" in text
    assert "PIP_INDEX_URL" in text
    assert "PIP_TRUSTED_HOST" in text
    assert "evander_worker_validate_package_cache" in (
        ROOT / "ansible" / "group_vars" / "all" / "defaults.yml"
    ).read_text(encoding="utf-8")
    assert "evander_worker_port: 11888" in text
    assert 'evander_worker_root: "/var/lib/agent-worker"' in text
    assert 'evander_worker_runtime_dir: "{{ evander_worker_root }}/runtime"' in text
    assert 'evander_worker_sandbox_profile: "default"' in text
    assert 'evander_worker_docker_network: "none"' in text
    assert "evander_worker_egress_policy_enabled: false" in text
    assert "evander_worker_egress_policy_chain" in text
    assert "evander_worker_egress_policy_source_cidrs: []" in text
    assert '"169.254.0.0/16"' in text
    assert "EVANDER_WORKER_TOKEN" in text
    assert "evander_control_plane_host" in text
    assert "hostvars[evander_control_plane_host].evander_deploy_path" in text
    assert "hostvars[evander_control_plane_host].evander_run_user" in text
    assert "evander_control_plane_private_ip" in text
    assert 'evander_git_host: "<control-plane-private-ip>"' not in text
    assert 'evander_git_root: "{{ evander_control_plane_deploy_path }}/runtime/managed-git"' in text
    assert 'evander_git_ssh_alias: "evander-git-host"' in text
    assert "evander_worker_allowed_git_source_prefixes" in text
    assert "ssh://{{ evander_git_ssh_alias }}{{ evander_git_root }}/" in text
    assert "evander_worker_git_ssh_source_dir" in text
    assert "evander_worker_git_access_enabled" in text
    assert "evander_worker_install_control_plane_git_authorized_key" in text
    assert "evander_worker_repo_checkout_via_control_plane_git" in text
    assert 'evander_worker_repo_managed_directory_path: "root"' in text
    assert 'evander_worker_repo_deploy_ref: "refs/evander/deploy/{{ inventory_hostname }}"' in text
    assert "evander_worker_repo_mirror_name" not in text
    assert 'evander_runner_image_name: "evander-runner:latest"' in text
    assert 'evander_claude_runner_image_name: "evander-runner-claude:latest"' in text
    assert "evander_runner_image_archive_path" in text
    assert "evander_claude_runner_image_archive_path" in text
    assert "evander_runner_image_archives" in text
    assert "scripts/build_evander_claude_runner_image.sh" in text
    assert "evander_worker_toolchain_cache_root" in text
    assert "evander_worker_dotnet_toolchain_enabled: true" in text
    assert "evander_worker_dotnet_toolchain_local_path" in text
    assert "HOST_DOTNET_ROOT" in text
    assert "evander_worker_dotnet_toolchain_archive_path" in text
    assert "evander_worker_dotnet_tools_cache_path" in text
    assert "evander_worker_dotnet_tools_archive_path" in text
    assert "HOST_DOTNET_TOOLS_ARCHIVE" in text
    assert "evander_worker_dotnet_ef_version" in text
    assert "HOST_DOTNET_EF_VERSION" in text
    assert "evander_worker_nuget_fallback_archive_path" in text
    assert "HOST_NUGET_FALLBACK_ARCHIVE" in text
    assert "evander_worker_dotnet_restore_support_archive_path" in text
    assert "HOST_DOTNET_RESTORE_SUPPORT_ARCHIVE" in text
    assert "Microsoft.Extensions.FileSystemGlobbing" in text
    assert "evander_worker_node_toolchain_enabled: true" in text
    assert "evander_worker_node_toolchain_local_path" in text
    assert "HOST_NODE_ROOT" in text
    assert "evander_worker_node_toolchain_archive_path" in text
    assert "evander_worker_playwright_toolchain_enabled: true" in text
    assert "evander_worker_playwright_node_modules_local_path" in text
    assert "HOST_PLAYWRIGHT_NODE_MODULES" in text
    assert "evander_worker_playwright_runtime_archive_path" in text
    assert "evander_worker_playwright_browsers_archive_path" in text
    assert "evander_worker_readonly_mounts" in text
    assert "/opt/evander/toolchains/dotnet" in text
    assert "/opt/evander/toolchains/dotnet-tools" in text
    assert "/opt/evander/toolchains/node-v24.22.1" in text
    assert "/opt/evander/playwright-runtime" in text
    assert "/opt/evander/ms-playwright" in text
    assert "/opt/evander/nuget-cache-ro" in text
    assert "evander_worker_mssql_client_toolchain_enabled" in text
    assert "HOST_MSSQL_CLIENT_ARCHIVE" in text
    assert "/opt/evander/toolchains/mssql-client" in text
    assert "evander_worker_preload_images" in text
    assert "evander_worker_mssql_enabled: false" in text
    assert "evander_worker_mssql_container_host" in text
    assert "EVANDER_WORKER_MSSQL_ADMIN_PASSWORD" in text
    assert "evander_worker_mssql_seed_sidecar_enabled: false" in text
    assert "evander_worker_mssql_seed_sidecar_port: 11990" in text
    assert "vault_evander_worker_mssql_seed_sidecar_token" in text
    assert "evander_worker_codex_auth_image" in text
    assert "evander_worker_codex_egress_proxy_url" in text
    assert "evander_worker_codex_egress_proxy_bind_host" in text
    assert "evander_codex_egress_proxy_port" in text
    assert '"*.chatgpt.com"' in text


def test_worker_defaults_derive_control_plane_identity_from_hostvars() -> None:
    worker_defaults = (ROOT / "ansible" / "group_vars" / "evander_worker_hosts.yml").read_text(
        encoding="utf-8"
    )

    assert "evander_install_azure_cli: false" in worker_defaults
    assert "evander_git_user" in worker_defaults
    assert "hostvars[evander_control_plane_host].evander_run_user" in worker_defaults
    assert "hostvars[evander_control_plane_host].evander_deploy_path" in worker_defaults


def test_worker_egress_policy_defaults_expose_private_control_plane_endpoints() -> None:
    defaults = (ROOT / "ansible" / "group_vars" / "all" / "defaults.yml").read_text(
        encoding="utf-8"
    )
    azure_inventory = (ROOT / "ansible" / "inventory" / "azure.example.yml").read_text(
        encoding="utf-8"
    )
    worker_defaults = (ROOT / "ansible" / "group_vars" / "evander_worker_hosts.yml").read_text(
        encoding="utf-8"
    )

    assert 'evander_worker_docker_network: "none"' in worker_defaults
    assert "evander_worker_egress_policy_enabled: false" in worker_defaults
    assert "evander_worker_egress_policy_chain" in worker_defaults
    assert "evander_worker_egress_policy_script_path" in worker_defaults
    assert "evander_worker_egress_policy_source_cidrs: []" in worker_defaults
    assert '"169.254.0.0/16"' in worker_defaults
    assert "evander_worker_private_ip" in worker_defaults
    assert "evander_worker_private_cidr" in worker_defaults
    assert r"[0-9]{1,3}\." in worker_defaults
    assert r"[0-9]{1,3}\\." not in worker_defaults
    assert "evander_worker_egress_policy_allow_tcp_endpoints" in defaults
    assert "evander_worker_egress_policy_extra_allow_tcp_endpoints: []" in defaults
    allowed_clients = defaults.split("evander_codex_egress_proxy_allowed_client_cidrs:", 1)[
        1
    ].split("# Control-plane apt cache", 1)[0]
    assert "evander_worker_private_cidr" in allowed_clients
    assert "ansible_host" not in allowed_clients
    assert 'evander_git_host: "{{ evander_control_plane_private_ip }}"' in defaults
    assert "name: control-plane git ssh" in defaults
    assert 'host: "{{ evander_git_host }}"' in defaults
    assert "name: control-plane mcp gateway" in defaults
    assert "name: control-plane openai relay" in defaults
    assert "name: control-plane Codex egress proxy" in defaults
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
    proxy_endpoint = defaults.split("- name: control-plane Codex egress proxy", 1)[1].split(
        "- name: control-plane npm audit facade", 1
    )[0]
    assert "evander_worker_codex_egress_proxy_url | urlsplit('hostname')" in proxy_endpoint
    assert "evander_worker_codex_egress_proxy_url | urlsplit('port')" in proxy_endpoint
    assert "default(80, true)" in proxy_endpoint
    assert "evander_control_plane_private_ip" not in proxy_endpoint
    assert 'port: "{{ evander_nexus_port }}"' in defaults
    assert 'port: "{{ evander_apt_cache_port }}"' in defaults
    assert 'host: "{{ evander_control_plane_private_ip }}"' in defaults
    assert "<control-plane-private-ip>" not in defaults
    assert 'evander_worker_docker_network: "bridge"' in azure_inventory
    assert "evander_worker_egress_policy_enabled: true" in azure_inventory

    worker = (ROOT / "ansible" / "worker.yml").read_text(encoding="utf-8")
    site = (ROOT / "ansible" / "site.yml").read_text(encoding="utf-8")
    verification = (
        ROOT / "ansible" / "roles" / "container_worker_verification" / "tasks" / "main.yml"
    ).read_text(encoding="utf-8")
    assert "Assert networked Codex authentication has a restricted proxy" in worker
    assert "Assert Codex egress proxy worker addresses are numeric" in site
    assert "Set it explicitly when" in site
    assert "Resolve Codex egress proxy verification endpoint" in verification
    probe_endpoint = verification.split("Resolve Codex egress proxy verification endpoint", 1)[
        1
    ].split("Wait for the private Codex egress proxy", 1)[0]
    assert "urlsplit('port') | default(80, true)" in probe_endpoint
    assert "Verify a runner container reaches only the private Codex proxy health surface" in (
        verification
    )


def test_codex_egress_proxy_reference_uses_runtime_environment_names() -> None:
    reference = (ROOT / "docs" / "configuration-reference.md").read_text(encoding="utf-8")

    assert "`EVANDER_CODEX_EGRESS_PROXY_ALLOWED_HOSTS`" in reference
    assert "`EVANDER_CODEX_EGRESS_PROXY_ALLOWED_CLIENT_CIDRS`" in reference
    assert "`EVANDER_CODEX_EGRESS_ALLOWED_HOSTS`" not in reference
    assert "`EVANDER_CODEX_EGRESS_ALLOWED_CLIENT_CIDRS`" not in reference


def test_worker_container_egress_policy_role_applies_docker_user_default_deny() -> None:
    role = ROOT / "ansible" / "roles" / "worker_container_egress_policy"
    worker_defaults = (ROOT / "ansible" / "group_vars" / "evander_worker_hosts.yml").read_text(
        encoding="utf-8"
    )
    tasks = (role / "tasks" / "main.yml").read_text(encoding="utf-8")
    script = (role / "templates" / "evander-worker-egress-policy.sh.j2").read_text(encoding="utf-8")
    unit = (role / "templates" / "evander-worker-egress-policy.service.j2").read_text(
        encoding="utf-8"
    )
    verification = (
        ROOT / "ansible" / "roles" / "container_worker_verification" / "tasks" / "main.yml"
    ).read_text(encoding="utf-8")

    assert "evander_worker_egress_policy_active" in tasks
    assert "agent-sandbox-worker-egress-policy.service" not in tasks
    assert "Render worker egress policy script" in tasks
    assert "evander-worker-egress-policy.service" in tasks
    assert "Disable inactive worker egress policy service" in tasks
    assert "docker network inspect" in script
    assert "DOCKER-USER" in script
    assert "EVANDER-WORKER-EGRESS" in worker_defaults
    assert "evander_worker_egress_policy_block_cidrs" in script
    assert "169.254.0.0/16" in worker_defaults
    assert "--dport" in script
    assert "-j ACCEPT" in script
    assert 'iptables -A "${CHAIN}" -j DROP' in script
    assert "ExecStart={{ evander_worker_egress_policy_script_path }} apply" in unit
    assert "ExecStop={{ evander_worker_egress_policy_script_path }} clear" in unit
    assert "Before=evander-worker-daemon.service" in unit
    assert "Verify worker egress policy service is active" in verification
    assert "systemctl" in verification
    assert "is-enabled" in verification
    assert "is-active" in verification
    assert "evander_worker_egress_policy_active_state.stdout == 'active'" in verification
    assert "evander-worker-egress-policy.service'].state == 'running'" not in verification


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
        / "evander-worker-egress-policy.sh.j2"
    ).read_text(encoding="utf-8")

    assert "{% raw %}${#filtered_source_cidrs[@]}{% endraw %}" in template_text
    assert "{#" not in _remove_jinja_raw_blocks(template_text)


def test_worker_service_role_renders_env_and_daemon_unit() -> None:
    role = ROOT / "ansible" / "roles" / "container_worker_service"
    tasks = (role / "tasks" / "main.yml").read_text(encoding="utf-8")
    unit = (role / "templates" / "evander-worker-daemon.service.j2").read_text(encoding="utf-8")
    env = (role / "templates" / "worker-daemon.env.j2").read_text(encoding="utf-8")

    assert "config/worker-daemon.env" in tasks
    assert "no_log: true" in tasks
    assert "agent-sandbox-worker-daemon.service" not in tasks
    assert "evander-worker-daemon.service" in tasks
    assert "evander_worker_env_render.changed" in tasks
    assert "{{ key }}={{ value | string | quote }}" in env
    assert "Requires=docker.service" in unit
    assert "SupplementaryGroups={{ evander_run_user_supplementary_groups | join(' ') }}" in unit
    assert "EnvironmentFile={{ evander_deploy_path }}/config/worker-daemon.env" in unit
    assert ".venv/bin/evander-worker-daemon" in unit
    assert "--worker-root {{ evander_worker_root }}" in unit
    assert "--runtime-dir {{ evander_worker_runtime_dir }}" in unit
    assert "--sandbox-profile {{ evander_worker_sandbox_profile }}" in unit
    assert "--docker-network {{ evander_worker_docker_network }}" in unit
    assert "--allowed-git-source-prefix {{ prefix }}" in unit
    assert "--git-ssh-source-dir {{ evander_worker_git_ssh_source_dir }}" in unit
    assert "--mssql-enabled" in unit
    assert "--mssql-container-host {{ evander_worker_mssql_container_host }}" in unit
    assert "--read-only-rootfs" in unit
    assert "--container-user {{ evander_effective_worker_container_user }}" in unit
    assert "--readonly-mount {{ mount }}" in unit
    assert '{% endfor %}{{ "" }}\nRestart=on-failure' in unit


def test_worker_service_role_installs_optional_mssql_seed_sidecar() -> None:
    role = ROOT / "ansible" / "roles" / "container_worker_service"
    tasks = (role / "tasks" / "main.yml").read_text(encoding="utf-8")
    unit = (role / "templates" / "evander-mssql-seed-sidecar.service.j2").read_text(
        encoding="utf-8"
    )
    env = (role / "templates" / "mssql-seed-sidecar.env.j2").read_text(encoding="utf-8")
    defaults = (ROOT / "ansible" / "group_vars" / "evander_worker_hosts.yml").read_text(
        encoding="utf-8"
    )
    worker_playbook = (ROOT / "ansible" / "worker.yml").read_text(encoding="utf-8")

    assert "evander_worker_mssql_seed_sidecar_active" in tasks
    assert "and (evander_worker_mssql_enabled | bool)" in tasks
    assert "config/mssql-seed-sidecar.env" in tasks
    assert "evander-mssql-seed-sidecar.service" in tasks
    assert "Ensure MSSQL seed backup directory exists" in tasks
    assert "owner: mssql" in tasks
    assert "Disable inactive MSSQL seed sidecar service" in tasks
    assert "no_log: true" in tasks
    assert "EnvironmentFile={{ evander_deploy_path }}/config/mssql-seed-sidecar.env" in unit
    assert ".venv/bin/evander-mssql-seed-sidecar" in unit
    assert "--host {{ evander_worker_mssql_seed_sidecar_host }}" in unit
    assert "--port {{ evander_worker_mssql_seed_sidecar_port }}" in unit
    assert "--token-env {{ evander_worker_mssql_seed_sidecar_token_env }}" in unit
    assert "--mssql-admin-password-env {{ evander_worker_mssql_admin_password_env }}" in unit
    assert "--mssql-sqlcmd-path {{ evander_worker_mssql_sqlcmd_path }}" in unit
    assert "--mssql-data-dir {{ evander_worker_mssql_seed_sidecar_data_dir }}" in unit
    assert "{{ evander_worker_mssql_seed_sidecar_token_env }}" in env
    assert "{{ evander_worker_mssql_admin_password_env }}" in env
    assert "src/evander_mssql_seed_addon" in defaults
    assert "config/mssql-seed-sidecar.env" in defaults
    assert "Assert MSSQL seed sidecar configuration is complete" in worker_playbook
    assert "evander_worker_mssql_enabled | bool" in worker_playbook


def test_worker_mssql_role_installs_sql_server_and_verifies_sqlcmd() -> None:
    role = (ROOT / "ansible" / "roles" / "worker_mssql" / "tasks" / "main.yml").read_text(
        encoding="utf-8"
    )
    defaults = (ROOT / "ansible" / "group_vars" / "evander_worker_hosts.yml").read_text(
        encoding="utf-8"
    )

    assert "evander_worker_mssql_accept_eula" in role
    assert "evander_worker_use_apt_proxy | bool" in role
    assert "evander_worker_apt_proxy_url" in role
    assert "evander_worker_mssql_package_proxy_environment" in role
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
    assert "register: evander_worker_mssql_admin_connectivity" in role
    assert "retries: 60" in role
    assert "delay: 2" in role
    assert "until: evander_worker_mssql_admin_connectivity.rc == 0" in role
    assert "evander_worker_mssql_allowed_source_cidr" in role
    assert "DROP" in role


def test_worker_mssql_client_toolchain_role_seeds_and_verifies_sqlcmd_cache() -> None:
    worker = (ROOT / "ansible" / "worker.yml").read_text(encoding="utf-8")
    defaults = (ROOT / "ansible" / "group_vars" / "evander_worker_hosts.yml").read_text(
        encoding="utf-8"
    )
    role = (
        ROOT / "ansible" / "roles" / "worker_mssql_client_toolchain" / "tasks" / "main.yml"
    ).read_text(encoding="utf-8")

    assert worker.index("- role: worker_mssql") < worker.index(
        "- role: worker_mssql_client_toolchain"
    )
    assert worker.index("- role: worker_mssql_client_toolchain") < worker.index(
        "- role: container_worker_service"
    )
    assert "Build MSSQL client toolchain archive from packages installed on worker" in role
    assert "build_mssql_client_toolchain_archive.sh" in role
    assert "evander_worker_mssql_client_toolchain_build_on_worker" in role
    assert '"{{ evander_deploy_path }}/scripts/build_mssql_client_toolchain_archive.sh"' in role
    assert role.index(
        "Build MSSQL client toolchain archive from packages installed on worker"
    ) < role.index("Resolve MSSQL client toolchain archive to copy")
    assert (
        'evander_worker_mssql_client_toolchain_build_on_worker: "{{ evander_worker_mssql_enabled }}"'
        in defaults
    )
    assert "Copy MSSQL client toolchain archive to worker" in role
    assert "Unpack MSSQL client toolchain into worker cache" in role
    assert "Verify worker MSSQL client toolchain on host cache" in role
    assert "Verify MSSQL client is available inside runner container" in role
    assert "/opt/evander/toolchains/mssql-client:ro" in role
    assert "sqlcmd -?" in role


def test_python_env_role_accepts_private_package_index_environment() -> None:
    role = (ROOT / "ansible" / "roles" / "python_env" / "tasks" / "main.yml").read_text(
        encoding="utf-8"
    )
    defaults = (ROOT / "ansible" / "group_vars" / "all" / "defaults.yml").read_text(
        encoding="utf-8"
    )

    assert "evander_python_install_env: {}" in defaults
    assert "uv pip install" in role
    assert 'environment: "{{ evander_python_install_env | default({}) }}"' in role


def test_worker_package_cache_preflight_checks_private_pypi_asset_access() -> None:
    role = (
        ROOT / "ansible" / "roles" / "worker_package_cache_preflight" / "tasks" / "main.yml"
    ).read_text(encoding="utf-8")
    defaults = (ROOT / "ansible" / "group_vars" / "all" / "defaults.yml").read_text(
        encoding="utf-8"
    )

    assert "evander_worker_validate_package_cache: true" in defaults
    assert "evander_worker_package_cache_probe_url" in defaults
    assert "evander_nexus_pypi_probe_asset_path" in defaults
    assert "Probe worker PyPI package-cache asset access" in role
    assert "evander_worker_package_cache_probe_url" in role
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
    assert "evander_worker_root" in runtime
    assert "evander_worker_runtime_dir" in runtime
    assert "Resolve worker service user uid" in runtime
    assert "evander_effective_worker_container_user" in runtime
    assert "Check local runner image archives" in runtime
    assert "evander_runner_image_archives" in runtime
    assert "scripts/build_evander_runner_image.sh" in runtime
    assert "docker\n      - load" in runtime
    assert "docker\n      - image\n      - inspect" in runtime
    assert "Verify Codex runner runtime executables" in runtime
    assert "/usr/local/bin/codex-code-mode-host" in runtime
    assert "--network\n      - none" in runtime
    assert "evander_worker_toolchain_cache_root" in runtime
    assert "evander_worker_preload_images" in runtime
    assert "evander_worker_egress_policy_active" in egress
    assert "evander-worker-egress-policy.service" in egress
    assert "evander-worker-daemon.service" in verification
    assert "evander-worker-egress-policy.service" in verification
    assert "evander-mssql-seed-sidecar.service" in verification
    assert "Resolve MSSQL seed sidecar verification host" in verification
    assert "evander_worker_mssql_seed_sidecar_probe_host" in verification
    assert "evander_worker_mssql_seed_sidecar_probe_url_host" in verification
    assert "evander_worker_mssql_seed_sidecar_bind_host in ['', '0.0.0.0', '::']" in verification
    assert 'host: "{{ evander_worker_mssql_seed_sidecar_probe_host }}"' in verification
    assert (
        "http://{{ evander_worker_mssql_seed_sidecar_probe_url_host }}:"
        "{{ evander_worker_mssql_seed_sidecar_port }}/healthz"
    ) in verification
    assert "Verify MSSQL seed sidecar health endpoint" in verification
    assert "docker ps" not in verification
    assert "Verify Docker is usable by the worker user" in verification
    assert "/healthz" in verification
    assert "container user: {{ evander_effective_worker_container_user }}" in verification
    assert "evander-gateway.service" not in verification
    assert "evander-admin-web.service" not in verification


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
    assert "evander_runner_image_name" in role
    assert "/opt/evander/toolchains/node-v24.22.1/bin" in role
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
    assert "evander_worker_nuget_fallback_archive_path/HOST_NUGET_FALLBACK_ARCHIVE" in role
    assert "Pack local dotnet restore support packages" in role
    assert "Copy dotnet toolchain archive to worker" in role
    assert "Copy dotnet tools archive to worker" in role
    assert "Copy dotnet restore support archive to worker" in role
    assert "Unpack dotnet toolchain into worker cache" in role
    assert "Unpack dotnet tools into worker cache" in role
    assert "Unpack dotnet restore support packages into worker NuGet fallback cache" in role
    assert "Verify worker dotnet toolchain on host cache" in role
    assert "Verify dotnet, EF tooling, and restore support inside runner container" in role
    assert "evander_runner_image_name" in role
    assert "/opt/evander/toolchains/dotnet" in role
    assert "/opt/evander/toolchains/dotnet-tools" in role
    assert "/opt/evander/nuget-cache-ro:ro" in role
    assert "dotnet --info" in role
    assert "dotnet ef --version" in role
    assert "dotnet-ef --version" in role
    assert 'dotnet restore "$smoke_dir/restore-smoke.csproj"' in role
    assert "evander_worker_dotnet_restore_support_package_id" in role
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
    assert "verify_seeded_playwright_runtime.mjs" in role
    assert "Verify staged Playwright package and browser cache are compatible" in role
    assert role.index("Verify staged Playwright package and browser cache are compatible") < (
        role.index("Reset worker Playwright runtime cache")
    )
    assert ".playwright-preflight" in role
    assert "Unpack Playwright runtime into worker cache" in role
    assert "Unpack Playwright browsers into worker cache" in role
    assert "Verify worker Playwright runtime on host cache" in role
    assert "Verify Playwright is available inside runner container" in role
    assert "evander_worker_node_cache_path" in role
    assert "/opt/evander/playwright-runtime" in role
    assert "/opt/evander/ms-playwright" in role
    assert "PLAYWRIGHT_MCP_EXECUTABLE_PATH" in role
    assert "PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD" in role
    assert 'path.join(runtime, "playwright")' in role
    assert "chromium.executablePath()" in role


def test_worker_git_access_role_sets_up_private_repo_checkout_before_repo_checkout() -> None:
    worker = (ROOT / "ansible" / "worker.yml").read_text(encoding="utf-8")
    role = (ROOT / "ansible" / "roles" / "worker_git_access" / "tasks" / "main.yml").read_text(
        encoding="utf-8"
    )
    defaults = (ROOT / "ansible" / "group_vars" / "evander_worker_hosts.yml").read_text(
        encoding="utf-8"
    )

    assert worker.index("- role: host_prereqs") < worker.index("- role: worker_git_access")
    assert worker.index("- role: worker_git_access") < worker.index("- role: repo_checkout")
    assert "Generate restricted worker Git SSH key" in role
    assert "IdentityFile ~/.ssh/evander-git" in role
    assert "Remove prior worker deployment authorization before managed repo resolution" in role
    assert "Resolve control-plane checkout as a root-owned managed repository" in role
    assert '"{{ evander_control_plane_deploy_path }}/.venv/bin/evander-cli"' in role
    assert "git-repos\n      - register" in role
    assert "--no-refresh" in role
    assert "Assert managed deployment repository is root-owned" in role
    assert "Authorize worker managed-repository key on the control plane" in role
    assert "git_ssh_command_wrapper.sh" in role
    assert "EVANDER_GIT_ROOT_B64={{ evander_git_root | b64encode }}" in role
    assert "EVANDER_GIT_AUTH_ACCESS=read-only" in role
    assert "EVANDER_GIT_REFRESH_REQUIRED=1" in role
    assert "EVANDER_GIT_AUTH_REPO_B64={{ evander_worker_repo_managed_path | b64encode }}" in role
    assert "EVANDER_GIT_AUTH_REPO_PREFIX_B64" not in role
    assert "scripts/create_git_remote.sh" not in role
    assert "evander.mirrorSource" not in role
    assert "Publish requested control-plane revision to managed deployment ref" in role
    assert '"--git-dir={{ evander_worker_repo_managed_path }}"' in role
    assert "fetch\n      - --force" in role
    assert "evander_worker_repo_deploy_ref" in role
    assert "evander_control_plane_repo_revision_check.stdout" in role
    assert "Use private control-plane managed repository for worker repo checkout" in role
    assert 'evander_repo_url: "{{ evander_worker_repo_checkout_url }}"' in role
    assert (
        'evander_repo_refspec: "+{{ evander_worker_repo_deploy_ref }}:'
        '{{ evander_worker_repo_checkout_ref }}"' in role
    )
    assert (
        'evander_worker_repo_checkout_ref: "refs/remotes/origin/evander-deploy/'
        '{{ inventory_hostname }}"' in defaults
    )
    assert "Deploy the control plane first, then rerun ansible/worker.yml" in role
    assert "not the literal <control-plane-private-ip> placeholder" in role


def test_dotnet_bundle_selector_only_publishes_validated_directories(tmp_path):
    import yaml

    tasks = yaml.safe_load(
        (ROOT / "ansible/roles/worker_dotnet_toolchain/tasks/main.yml").read_text()
    )
    task = next(
        row
        for row in tasks
        if row["name"] == "Atomically select validated dotnet bundle for future sessions"
    )
    script = task["ansible.builtin.command"]["argv"][2]
    old, new = tmp_path / "old", tmp_path / "new"
    old.mkdir()
    new.mkdir()
    selector = tmp_path / "current"
    selector.symlink_to(old)
    args = ["python3", "-c", script, str(new), str(selector)]
    failed = subprocess.run(args, capture_output=True, text=True, timeout=10)
    assert failed.returncode != 0
    assert selector.resolve() == old
    (new / ".complete").write_text("validated")
    passed = subprocess.run(args, capture_output=True, text=True, timeout=10, check=True)
    assert "selected" in passed.stdout
    assert selector.resolve() == new
    assert old.is_dir()
    repeated = subprocess.run(args, capture_output=True, text=True, timeout=10, check=True)
    assert not repeated.stdout


def test_enabling_dotnet_after_disabled_jobs_can_create_bundle_selector(tmp_path):
    import yaml
    from jinja2.nativetypes import NativeEnvironment

    defaults = yaml.safe_load((ROOT / "ansible/group_vars/evander_worker_hosts.yml").read_text())
    environment = NativeEnvironment()
    environment.filters["bool"] = bool
    template = environment.from_string(defaults["evander_worker_readonly_mounts"])
    selector = tmp_path / "dotnet-current"
    paths = {
        name: str(tmp_path / name)
        for name in defaults if name.endswith("_cache_path")
    }
    paths.update(
        evander_worker_dotnet_cache_path=str(selector / "sdk"),
        evander_worker_dotnet_tools_cache_path=str(selector / "tools"),
        evander_worker_dotnet_toolchain_enabled=False,
        evander_worker_mssql_client_toolchain_enabled=False,
    )
    disabled = template.render(**paths)
    assert all("/opt/evander/toolchains/dotnet" not in mount for mount in disabled)
    # Reproduce Docker -v's missing-source directory creation for a first job.
    for mount in disabled:
        Path(mount.split(":", 1)[0]).mkdir(parents=True, exist_ok=True)
    assert not selector.exists()
    paths["evander_worker_dotnet_toolchain_enabled"] = True
    enabled = template.render(**paths)
    assert f"{selector}/sdk:/opt/evander/toolchains/dotnet" in enabled
    assert f"{selector}/tools:/opt/evander/toolchains/dotnet-tools" in enabled
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / ".complete").write_text("validated")
    tasks = yaml.safe_load((ROOT / "ansible/roles/worker_dotnet_toolchain/tasks/main.yml").read_text())
    selection = next(t for t in tasks if t["name"] == "Atomically select validated dotnet bundle for future sessions")
    subprocess.run(
        ["python3", "-c", selection["ansible.builtin.command"]["argv"][2], str(bundle), str(selector)],
        check=True, capture_output=True, timeout=10,
    )
    assert selector.is_symlink()
    assert selector.resolve() == bundle
