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
        "asterisk_didww",
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

    assert "evander_service_profile in ['full', 'control_plane_only']" in site
    assert "evander_services.firewall must be false for control_plane_only deployments" in site
    assert "evander_runtime_dir == evander_expected_runtime_dir" in site
    assert "- role: host_prereqs" in site
    assert "- role: apt_cache_relay" in site
    assert "- role: repo_checkout" in site
    assert "- role: python_env" in site
    assert "- role: runtime_bootstrap" in site
    assert "- role: service_config" in site
    assert "- role: asterisk_didww" in site
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
    assert site.index("- role: service_config") < site.index("- role: asterisk_didww")
    assert site.index("- role: asterisk_didww") < site.index("- role: systemd_install")
    assert site.index("- role: frontend_build") < site.index("- role: admin_web_caddy")
    assert site.index("- role: admin_web_caddy") < site.index("- role: service_runtime")
    assert site.index("- role: package_cache_nexus") < site.index("- role: semantic_store_qdrant")
    assert site.index("- role: semantic_store_qdrant") < site.index("- role: verification")


def test_ansible_admin_web_caddy_role_proxies_branded_entrypoints() -> None:
    repo = Path(__file__).resolve().parents[1]
    defaults = (repo / "ansible" / "group_vars" / "all" / "defaults.yml").read_text(
        encoding="utf-8"
    )
    tasks = (repo / "ansible" / "roles" / "admin_web_caddy" / "tasks" / "main.yml").read_text(
        encoding="utf-8"
    )
    caddyfile = (
        repo / "ansible" / "roles" / "admin_web_caddy" / "templates" / "Caddyfile.j2"
    ).read_text(encoding="utf-8")
    verification = (repo / "ansible" / "roles" / "verification" / "tasks" / "main.yml").read_text(
        encoding="utf-8"
    )

    assert "evander_admin_web_frontend_dist_dir" in defaults
    assert "evander_admin_web_caddy_enabled: false" in defaults
    assert "evander_admin_web_caddy_verification_connect_timeout_seconds: 5" in defaults
    assert "evander_admin_web_caddy_verification_max_time_seconds: 20" in defaults
    assert "evander_admin_web_caddy_verification_retries: 6" in defaults
    assert "evander_admin_web_caddy_verification_delay_seconds: 10" in defaults
    assert "Install Caddy package" in tasks
    assert "Assert Admin Web frontend assets were built" in tasks
    assert "Render Admin Web Caddyfile" in tasks
    assert "caddy validate --adapter caddyfile --config" in tasks
    assert "Reload Caddy after Caddyfile changes" in tasks

    assert "handle /api/*" in caddyfile
    assert "handle /public/*" in caddyfile
    assert "handle /healthz" in caddyfile
    assert "@admin_web_index path / /index.html" in caddyfile
    assert "handle @admin_web_index" in caddyfile
    assert (
        "reverse_proxy {{ evander_admin_web_caddy_upstream_host }}:{{ evander_admin_web_caddy_upstream_port }}"
        in caddyfile
    )
    assert "root * {{ evander_admin_web_frontend_dist_dir }}" in caddyfile
    assert "try_files {path} =404" in caddyfile
    assert "file_server" in caddyfile

    assert "Verify managed Admin Web Caddy is active" in verification
    assert "Verify managed Admin Web Caddy serves frontend and proxies health" in verification
    assert "--resolve" in verification
    assert "--connect-timeout" in verification
    assert "evander_admin_web_caddy_verification_max_time_seconds" in verification
    assert "register: evander_admin_web_caddy_frontend_probe" in verification
    assert 'retries: "{{ evander_admin_web_caddy_verification_retries | int }}"' in verification
    assert "until: evander_admin_web_caddy_frontend_probe.rc == 0" in verification
    assert "https://${host}/index.html" in verification
    assert 'find "${dist_dir}/assets"' in verification
    assert "No built ${extension} asset found" in verification


def test_ansible_admin_web_branding_assets_can_be_copied() -> None:
    repo = Path(__file__).resolve().parents[1]
    defaults = (repo / "ansible" / "group_vars" / "all" / "defaults.yml").read_text(
        encoding="utf-8"
    )
    service_config = (
        repo / "ansible" / "roles" / "service_config" / "tasks" / "main.yml"
    ).read_text(encoding="utf-8")
    docs = (repo / "ansible" / "README.md").read_text(encoding="utf-8")

    assert "evander_admin_web_branding_asset_dir:" in defaults
    assert "EVANDER_ADMIN_WEB_BRANDING_ASSET_DIR" in defaults
    assert "evander_admin_web_branding_assets: []" in defaults

    assert "Ensure Admin Web branding asset directory exists" in service_config
    assert "Ensure Admin Web branding asset subdirectories exist" in service_config
    assert "Copy Admin Web branding assets" in service_config
    assert 'src: "{{ item.source_path }}"' in service_config
    assert 'dest: "{{ evander_admin_web_branding_asset_dir }}/{{ item.path }}"' in service_config
    assert "mode: \"{{ item.mode | default('0644') }}\"" in service_config
    assert "evander_admin_web_branding_asset_copy" in service_config

    assert "evander_admin_web_branding_assets" in docs
    assert "operator-owned deployment config" in docs


def test_ansible_asterisk_didww_role_is_opt_in_and_matches_bridge_contract() -> None:
    repo = Path(__file__).resolve().parents[1]
    defaults = (repo / "ansible" / "group_vars" / "all" / "defaults.yml").read_text(
        encoding="utf-8"
    )
    azure = (repo / "ansible" / "group_vars" / "azure.example.yml").read_text(encoding="utf-8")
    vault_example = (repo / "ansible" / "group_vars" / "all" / "vault.yml.example").read_text(
        encoding="utf-8"
    )
    tasks = (repo / "ansible" / "roles" / "asterisk_didww" / "tasks" / "main.yml").read_text(
        encoding="utf-8"
    )
    pjsip = (
        repo / "ansible" / "roles" / "asterisk_didww" / "templates" / "pjsip.conf.j2"
    ).read_text(encoding="utf-8")
    extensions = (
        repo / "ansible" / "roles" / "asterisk_didww" / "templates" / "extensions.conf.j2"
    ).read_text(encoding="utf-8")
    verification = (repo / "ansible" / "roles" / "verification" / "tasks" / "main.yml").read_text(
        encoding="utf-8"
    )
    docs = (repo / "ansible" / "README.md").read_text(encoding="utf-8")

    assert "evander_asterisk_didww_enabled: false" in defaults
    assert "evander_didww_sip_bridge_enable_inbound_phone_calls: false" in defaults
    assert 'evander_didww_sip_bridge_ari_user: "{{ evander_run_user }}"' in defaults
    assert (
        "DIDWW_SIP_BRIDGE_ENABLE_INBOUND_PHONE_CALLS: \"{{ (evander_didww_sip_bridge_enable_inbound_phone_calls | bool) | ternary('true', 'false') }}\""
        in defaults
    )
    assert "evander_asterisk_ari_user:" in defaults
    assert "evander_didww_sip_bridge_env.get('ASTERISK_ARI_USER'" in defaults
    assert "evander_shared_secrets_env.get('ASTERISK_ARI_PASSWORD'" in defaults
    assert 'evander_asterisk_didww_identify_section: "didww-identify"' in defaults
    assert '"46.19.208.0/21"' in defaults
    assert '"185.238.172.0/22"' in defaults
    assert "evander_asterisk_didww_inbound_route_id" in defaults

    assert "evander_asterisk_didww_enabled: false" in azure
    assert "vault_didww_sip_username" in azure
    assert "vault_didww_sip_password" in azure
    assert (
        "vault_didww_sip_identify_matches | default(['46.19.208.0/21', '185.238.172.0/22'], true)"
        in azure
    )
    assert "vault_didww_inbound_route_id" in azure
    assert 'ASTERISK_ARI_PASSWORD: "{{ vault_asterisk_ari_password }}"' in azure
    assert 'DIDWW_SIP_BRIDGE_TOKEN: "{{ vault_didww_sip_bridge_token }}"' in azure
    assert 'VOICE_MEDIA_CONTROLLER_TOKEN: "{{ vault_voice_media_controller_token }}"' in azure
    assert "vault_didww_sip_username" in vault_example
    assert "vault_didww_sip_password" in vault_example
    assert 'vault_didww_sip_identify_matches: ""' in vault_example

    assert "Skip Asterisk DIDWW provisioning when disabled" in tasks
    assert "Install Asterisk packages" in tasks
    assert "Render managed Asterisk config files" in tasks
    assert "no_log: true" in tasks
    assert "Verify Asterisk ARI credentials" in tasks
    assert "pjsip show endpoint {{ evander_asterisk_didww_endpoint }}" in tasks
    assert "evander_asterisk_ari_user == evander_didww_sip_bridge_env.get" in tasks

    assert "external_signaling_address={{ evander_asterisk_public_address }}" in pjsip
    assert "external_media_address={{ evander_asterisk_public_address }}" in pjsip
    assert "local_net={{ local_net }}" in pjsip
    assert 'allow={{ evander_asterisk_didww_codecs | join(",") }}' in pjsip
    assert "direct_media=no" in pjsip
    assert "rewrite_contact=yes" in pjsip
    assert "force_rport=yes" in pjsip
    assert "rtp_symmetric=no" in pjsip
    assert "match={{ match }}" in pjsip

    assert "Set(INBOUND_PHONE_CALL=1)" in extensions
    assert "Set(INBOUND_ROUTE_ID={{ evander_asterisk_didww_inbound_route_id }})" in extensions
    dialed_did_extension = extensions.split("exten => _X.,1", 1)[1].split("exten => s,1", 1)[0]
    fallback_extension = extensions.split("exten => s,1", 1)[1]
    assert (
        "Stasis({{ evander_asterisk_ari_app }},inbound:${EXTEN}:{{ evander_asterisk_didww_inbound_route_id }})"
        in dialed_did_extension
    )
    assert (
        "Stasis({{ evander_asterisk_ari_app }},inbound:{{ evander_asterisk_didww_inbound_route_id }})"
        in fallback_extension
    )

    assert "Verify managed Asterisk is active" in verification
    assert "Verify managed Asterisk ARI endpoint" in verification
    assert "evander_asterisk_didww_enabled | bool" in verification
    assert "evander_asterisk_didww_enabled" in docs
    assert "evander_didww_sip_bridge_ari_user" in docs
    assert "evander_didww_sip_bridge_enable_inbound_phone_calls" in docs
    assert "DIDWW source CIDRs" in docs


def test_ansible_site_announces_control_plane_deployment_status() -> None:
    repo = Path(__file__).resolve().parents[1]
    site = (repo / "ansible" / "site.yml").read_text(encoding="utf-8")
    defaults = (repo / "ansible" / "group_vars" / "all" / "defaults.yml").read_text(
        encoding="utf-8"
    )

    assert "evander_deployment_status_path" in defaults
    assert "deployment-status.json" in defaults
    assert "evander_deployment_status_message" in defaults
    assert "evander_deployment_status_ttl_seconds" in defaults
    assert "Check whether an existing runtime directory is present" in site
    assert "Announce deployment in control-plane runtime" in site
    assert '"source": "ansible"' in site
    assert "strftime('%Y-%m-%dT%H:%M:%SZ')" in site
    assert "Clear deployment status runtime flag" in site
    assert site.index("Announce deployment in control-plane runtime") < site.index(
        "- role: host_prereqs"
    )


def test_ansible_runtime_bootstrap_repairs_managed_service_lock_ownership() -> None:
    repo = Path(__file__).resolve().parents[1]
    defaults = (repo / "ansible" / "group_vars" / "all" / "defaults.yml").read_text(
        encoding="utf-8"
    )
    runtime_bootstrap = (
        repo / "ansible" / "roles" / "runtime_bootstrap" / "tasks" / "main.yml"
    ).read_text(encoding="utf-8")

    assert "evander_runtime_service_lock_files:" in defaults
    for lock_name in [
        "discord-ingest.lock",
        "discord-wake.lock",
        "scheduled-wake.lock",
        "teams-ingest.lock",
        "teams-meeting-ingest.lock",
        "trigger-orchestrator.lock",
        "effect-orchestrator.lock",
    ]:
        assert lock_name in defaults

    assert (
        "Ensure managed runtime service lock files are owned by the run user" in runtime_bootstrap
    )
    assert 'path: "{{ evander_runtime_dir }}/{{ item }}"' in runtime_bootstrap
    assert 'owner: "{{ evander_run_user }}"' in runtime_bootstrap
    assert 'group: "{{ evander_run_user }}"' in runtime_bootstrap
    assert 'mode: "0644"' in runtime_bootstrap
    assert "access_time: preserve" in runtime_bootstrap
    assert "modification_time: preserve" in runtime_bootstrap
    assert 'loop: "{{ evander_runtime_service_lock_files }}"' in runtime_bootstrap
    assert runtime_bootstrap.index("Ensure managed runtime service lock files") < (
        runtime_bootstrap.index("Initialize repo runtime state")
    )


def test_ansible_nexus_package_cache_role_is_control_plane_only() -> None:
    repo = Path(__file__).resolve().parents[1]
    site = (repo / "ansible" / "site.yml").read_text(encoding="utf-8")
    worker = (repo / "ansible" / "worker.yml").read_text(encoding="utf-8")
    role = repo / "ansible" / "roles" / "package_cache_nexus"

    assert "- role: package_cache_nexus" in site
    assert "package_cache_nexus" not in worker
    assert (role / "tasks" / "main.yml").exists()
    assert (role / "templates" / "evander-nexus.service.j2").exists()


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
    assert (role / "templates" / "evander-qdrant.service.j2").exists()


def test_ansible_host_prereqs_can_install_azure_cli_for_control_plane() -> None:
    repo = Path(__file__).resolve().parents[1]
    defaults = (repo / "ansible" / "group_vars" / "all" / "defaults.yml").read_text(
        encoding="utf-8"
    )
    tasks = (repo / "ansible" / "roles" / "host_prereqs" / "tasks" / "main.yml").read_text(
        encoding="utf-8"
    )

    assert "evander_install_azure_cli: false" in defaults
    assert "evander_azure_cli_deb_architecture" in defaults
    assert "Download Microsoft signing key for Azure CLI" in tasks
    assert "https://packages.microsoft.com/repos/azure-cli/" in tasks
    assert "name: azure-cli" in tasks
    assert "when: evander_install_azure_cli | bool" in tasks


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
    tasks = (repo / "ansible" / "roles" / "package_cache_nexus" / "tasks" / "main.yml").read_text(
        encoding="utf-8"
    )
    unit = (
        repo
        / "ansible"
        / "roles"
        / "package_cache_nexus"
        / "templates"
        / "evander-nexus.service.j2"
    ).read_text(encoding="utf-8")

    assert "package_cache: true" in defaults
    assert 'evander_nexus_version: "3.91.1-04"' in defaults
    assert "evander_nexus_port: 8081" in defaults
    assert 'evander_nexus_bind_host: "0.0.0.0"' in defaults
    assert "evander_nexus_accept_community_eula: true" in defaults
    assert "evander_nexus_community_eula_disclaimer" not in defaults
    assert 'evander_nexus_pypi_proxy_repository: "pypi-proxy"' in defaults
    assert 'evander_nexus_pypi_group_repository: "pypi-all"' in defaults
    assert 'evander_nexus_npm_proxy_repository: "npm-proxy"' in defaults
    assert 'evander_nexus_npm_group_repository: "npm-all"' in defaults
    assert 'evander_nexus_anonymous_reader_role: "evander-package-cache-reader"' in defaults
    assert (
        'evander_nexus_pypi_probe_asset_path: "packages/wheel/0.47.0/wheel-0.47.0-py3-none-any.whl"'
        in defaults
    )
    assert "evander_package_cache_allowed_tcp_ports" in defaults
    assert "[evander_nexus_port]" in defaults
    assert "evander_worker_pypi_index_url" in defaults
    assert "evander_worker_npm_registry_url" in defaults

    assert "evander_nexus_admin_password must be set from Vault" in tasks
    assert "agent-sandbox-nexus.service" not in tasks
    assert "quiet: true" in tasks
    assert "Assert Nexus admin password is configured\n" in tasks
    assert "Read Nexus Community Edition EULA status" in tasks
    assert "Accept Nexus Community Edition EULA" in tasks
    assert "service/rest/v1/system/eula" in tasks
    assert "evander_nexus_community_eula_status.json.disclaimer" in tasks
    assert "evander_nexus_community_eula_disclaimer" not in tasks
    assert "no_log: true\n\n    - name: Ensure Nexus service group exists" not in tasks
    assert "Download pinned Nexus archive" in tasks
    assert "evander-nexus.service" in tasks
    assert '/service/rest/v1/security/users"' in tasks
    assert '/service/rest/v1/security/user"' not in tasks
    assert "service/rest/v1/security/anonymous" in tasks
    assert "service/rest/v1/security/roles/{{ evander_nexus_anonymous_reader_role }}" in tasks
    assert "service/rest/v1/security/users/anonymous" in tasks
    assert "Probe anonymous Nexus PyPI asset access" in tasks
    assert "evander_nexus_pypi_probe_asset_path" in tasks
    assert "Nexus anonymous PyPI asset access is working" in tasks
    assert "nx-repository-view-pypi-{{ evander_nexus_pypi_group_repository }}-read" in tasks
    assert "nx-repository-view-pypi-{{ evander_nexus_pypi_proxy_repository }}-read" in tasks
    assert "nx-repository-view-npm-{{ evander_nexus_npm_group_repository }}-read" in tasks
    assert "nx-repository-view-npm-{{ evander_nexus_npm_proxy_repository }}-read" in tasks
    assert "source=default" in tasks
    assert "repositories/pypi/proxy" in tasks
    assert "repositories/pypi/group" in tasks
    assert "repositories/npm/proxy" in tasks
    assert "repositories/npm/group" in tasks
    assert "https://pypi.org/" in defaults
    assert "https://registry.npmjs.org" in defaults
    assert "ExecStart={{ evander_nexus_current_link }}/bin/nexus run" in unit
    assert "User={{ evander_nexus_user }}" in unit


def test_ansible_npm_audit_facade_defaults_and_role_contract() -> None:
    repo = Path(__file__).resolve().parents[1]
    defaults = (repo / "ansible" / "group_vars" / "all" / "defaults.yml").read_text(
        encoding="utf-8"
    )
    service_config = (
        repo / "ansible" / "roles" / "service_config" / "tasks" / "main.yml"
    ).read_text(encoding="utf-8")
    service_runtime = (
        repo / "ansible" / "roles" / "service_runtime" / "tasks" / "main.yml"
    ).read_text(encoding="utf-8")
    verification = (repo / "ansible" / "roles" / "verification" / "tasks" / "main.yml").read_text(
        encoding="utf-8"
    )

    assert "npm_audit_facade: true" in defaults
    assert "evander_npm_audit_facade_port: 11881" in defaults
    assert "evander_npm_audit_facade_env" in defaults
    assert "evander_npm_audit_facade_allowed_tcp_ports" in defaults
    assert "[evander_npm_audit_facade_port]" in defaults
    assert "name: control-plane npm audit facade" in defaults
    assert 'port: "{{ evander_npm_audit_facade_port }}"' in defaults

    assert 'path: "config/npm-audit-facade.env"' in service_config
    assert "evander_npm_audit_facade_env" in service_config
    assert "key: npm_audit_facade" in service_runtime
    assert "unit: evander-npm-audit-facade.service" in service_runtime
    assert "key: npm_audit_facade" in verification
    assert "unit: evander-npm-audit-facade.service" in verification
    assert "http://127.0.0.1:{{ evander_npm_audit_facade_port }}/healthz" in verification


def test_ansible_apt_cache_relay_defaults_and_role_contract() -> None:
    repo = Path(__file__).resolve().parents[1]
    defaults = (repo / "ansible" / "group_vars" / "all" / "defaults.yml").read_text(
        encoding="utf-8"
    )
    tasks = (repo / "ansible" / "roles" / "apt_cache_relay" / "tasks" / "main.yml").read_text(
        encoding="utf-8"
    )
    verification = (repo / "ansible" / "roles" / "verification" / "tasks" / "main.yml").read_text(
        encoding="utf-8"
    )

    assert "apt_cache: true" in defaults
    assert "evander_apt_cache_port: 3142" in defaults
    assert 'evander_apt_cache_bind_host: "0.0.0.0"' in defaults
    assert 'evander_apt_cache_cache_dir: "/var/cache/apt-cacher-ng"' in defaults
    assert (
        'evander_apt_cache_passthrough_pattern: "^(packages\\\\.microsoft\\\\.com):443$"'
        in defaults
    )
    assert "evander_apt_cache_allowed_tcp_ports" in defaults
    assert "[evander_apt_cache_port]" in defaults

    assert "Install apt-cacher-ng package" in tasks
    assert "name: apt-cacher-ng" in tasks
    assert "Port: {{ evander_apt_cache_port }}" in tasks
    assert "BindAddress: {{ evander_apt_cache_bind_host }}" in tasks
    assert "CacheDir: {{ evander_apt_cache_cache_dir }}" in tasks
    assert "PassThroughPattern: {{ evander_apt_cache_passthrough_pattern }}" in tasks
    assert "apt-cacher-ng.service" in tasks
    assert "acng-report.html" in tasks

    assert "apt-cacher-ng.service" in verification
    assert "evander_apt_cache_port" in verification
    assert "http://127.0.0.1:{{ evander_apt_cache_port }}/acng-report.html" in verification


def test_ansible_qdrant_semantic_store_defaults_and_role_contract() -> None:
    repo = Path(__file__).resolve().parents[1]
    defaults = (repo / "ansible" / "group_vars" / "all" / "defaults.yml").read_text(
        encoding="utf-8"
    )
    tasks = (repo / "ansible" / "roles" / "semantic_store_qdrant" / "tasks" / "main.yml").read_text(
        encoding="utf-8"
    )
    unit = (
        repo
        / "ansible"
        / "roles"
        / "semantic_store_qdrant"
        / "templates"
        / "evander-qdrant.service.j2"
    ).read_text(encoding="utf-8")
    verification = (repo / "ansible" / "roles" / "verification" / "tasks" / "main.yml").read_text(
        encoding="utf-8"
    )

    assert "semantic_store: true" in defaults
    assert 'evander_qdrant_image: "qdrant/qdrant:v1.18.0"' in defaults
    assert 'evander_qdrant_data_dir: "/var/lib/evander/qdrant"' in defaults
    assert 'evander_qdrant_bind_host: "127.0.0.1"' in defaults
    assert "evander_qdrant_port: 6333" in defaults
    assert "guest/worker firewall allowances" in defaults

    assert "Stop Qdrant semantic store when disabled" in tasks
    assert "agent-sandbox-qdrant.service" not in tasks
    assert "evander-qdrant.service" in tasks
    assert "Install Docker for Qdrant semantic store" in tasks
    assert "name: docker.io" in tasks
    assert "Pull pinned Qdrant semantic store image" in tasks
    assert "docker\n          - pull" in tasks
    assert "evander_qdrant_image" in tasks
    assert "Wait for Qdrant semantic store listener" in tasks
    assert "Verify Qdrant semantic store health endpoint" in tasks
    assert "/healthz" in tasks

    assert "Requires=docker.service" in unit
    assert "docker run --name evander-qdrant" in unit
    assert "--publish {{ evander_qdrant_bind_host }}:{{ evander_qdrant_port }}:6333" in unit
    assert "--volume {{ evander_qdrant_data_dir }}:/qdrant/storage" in unit

    assert "evander-qdrant.service" in verification
    assert "evander_services.get(item.key, false) | bool" in verification
    assert "evander_services[item.key]" not in verification
    assert "evander_qdrant_bind_host" in verification
    assert "evander_qdrant_port" in verification
    assert "http://{{ evander_qdrant_bind_host }}:{{ evander_qdrant_port }}/healthz" in verification


def test_ansible_azure_inventory_maps_nexus_password_from_vault() -> None:
    repo = Path(__file__).resolve().parents[1]
    azure = (repo / "ansible" / "group_vars" / "azure.example.yml").read_text(encoding="utf-8")
    inventory = (repo / "ansible" / "inventory" / "azure.example.yml").read_text(encoding="utf-8")
    docs = (repo / "ansible" / "README.md").read_text(encoding="utf-8")

    assert (
        "evander_nexus_admin_password: \"{{ vault_evander_nexus_admin_password | default('') }}\""
        in azure
    )
    assert 'evander_control_plane_private_ip: "10.0.0.4"' in azure
    assert "evander_control_plane_private_ip" not in inventory
    assert "vault_evander_nexus_admin_password" in docs


def test_ansible_worker_egress_policy_defaults_are_private_control_plane_endpoints() -> None:
    repo = Path(__file__).resolve().parents[1]
    defaults = (repo / "ansible" / "group_vars" / "all" / "defaults.yml").read_text(
        encoding="utf-8"
    )
    worker_defaults = (repo / "ansible" / "group_vars" / "evander_worker_hosts.yml").read_text(
        encoding="utf-8"
    )
    azure = (repo / "ansible" / "group_vars" / "azure.example.yml").read_text(encoding="utf-8")
    azure_inventory = (repo / "ansible" / "inventory" / "azure.example.yml").read_text(
        encoding="utf-8"
    )

    assert 'evander_worker_docker_network: "none"' in worker_defaults
    assert "evander_worker_egress_policy_enabled: false" in worker_defaults
    assert "evander_worker_egress_policy_chain" in worker_defaults
    assert '"169.254.0.0/16"' in worker_defaults
    assert "evander_worker_egress_policy_allow_tcp_endpoints" in defaults
    assert "evander_worker_egress_policy_extra_allow_tcp_endpoints: []" in defaults
    assert 'evander_git_host: "{{ evander_control_plane_private_ip }}"' in defaults
    assert "name: control-plane git ssh" in defaults
    assert 'host: "{{ evander_git_host }}"' in defaults
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
    assert 'port: "{{ evander_nexus_port }}"' in defaults
    assert 'port: "{{ evander_apt_cache_port }}"' in defaults
    assert 'host: "{{ evander_control_plane_private_ip }}"' in defaults
    assert 'evander_worker_docker_network: "bridge"' not in azure
    assert "evander_worker_egress_policy_enabled: true" not in azure
    assert 'evander_worker_docker_network: "bridge"' in azure_inventory
    assert "evander_worker_egress_policy_enabled: true" in azure_inventory


def test_worker_apt_proxy_runs_before_host_prereqs_and_can_be_disabled() -> None:
    repo = Path(__file__).resolve().parents[1]
    worker = (repo / "ansible" / "worker.yml").read_text(encoding="utf-8")
    defaults = (repo / "ansible" / "group_vars" / "all" / "defaults.yml").read_text(
        encoding="utf-8"
    )
    worker_defaults = (repo / "ansible" / "group_vars" / "evander_worker_hosts.yml").read_text(
        encoding="utf-8"
    )
    tasks = (repo / "ansible" / "roles" / "worker_apt_proxy" / "tasks" / "main.yml").read_text(
        encoding="utf-8"
    )
    template = (
        repo / "ansible" / "roles" / "worker_apt_proxy" / "templates" / "01evander-proxy.j2"
    ).read_text(encoding="utf-8")

    assert "- role: worker_apt_proxy" in worker
    assert worker.index("- role: worker_apt_proxy") < worker.index("- role: host_prereqs")
    assert "evander_worker_use_apt_proxy: true" in defaults
    assert (
        'evander_worker_apt_proxy_url: "http://{{ evander_control_plane_private_ip }}:{{ evander_apt_cache_port }}"'
        in defaults
    )
    assert "/etc/apt/apt.conf.d/01evander-proxy" in defaults
    assert "ansible/roles/worker_apt_proxy" in worker_defaults

    assert "Remove managed worker apt proxy when disabled" in tasks
    assert "/etc/apt/apt.conf.d/01agent-sandbox-proxy" not in tasks
    assert "state: absent" in tasks
    assert "loop:" in tasks
    assert "when: not (evander_worker_use_apt_proxy | bool)" in tasks
    assert "Render worker apt proxy config" in tasks
    assert 'Acquire::http::Proxy "{{ evander_worker_apt_proxy_url }}";' in template
    assert 'Acquire::https::Proxy "{{ evander_worker_apt_proxy_https_url }}";' in template


def test_ansible_repo_checkout_supports_private_deploy_key() -> None:
    repo = Path(__file__).resolve().parents[1]
    text = (repo / "ansible" / "roles" / "repo_checkout" / "tasks" / "main.yml").read_text(
        encoding="utf-8"
    )
    defaults = (repo / "ansible" / "group_vars" / "all" / "defaults.yml").read_text(
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
    assert "evander_repo_ssh_private_key_path" in text
    assert "refspec:" in text
    assert "evander_repo_refspec" in text
    assert 'evander_repo_refspec: ""' in defaults
    assert "accept_newhostkey:" in text


def test_ansible_repo_checkout_resets_legacy_and_current_admin_web_entrypoints() -> None:
    repo = Path(__file__).resolve().parents[1]
    defaults = (repo / "ansible" / "group_vars" / "all" / "defaults.yml").read_text(
        encoding="utf-8"
    )
    generated_paths = defaults.split("evander_repo_managed_generated_tracked_paths:", 1)[1].split(
        "evander_repo_managed_tracked_config_paths:", 1
    )[0]

    assert "- frontend/workflow-editor/dist/index.html" in generated_paths
    assert "- frontend/admin-web/dist/index.html" in generated_paths


def test_worker_global_live_configs_are_operator_local() -> None:
    repo = Path(__file__).resolve().parents[1]
    local_config_paths = [
        "config/ado-worker/config.json",
        "config/discord-ingest/config.json",
        "config/discord-wake/config.json",
        "config/npm-audit-facade.env",
        "config/scheduled-wake/config.json",
        "config/teams-ingest/config.json",
        "config/teams-meeting-ingest/config.json",
        "config/trigger-orchestrator/config.json",
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


def test_ansible_worker_global_config_render_only_creates_missing_files() -> None:
    repo = Path(__file__).resolve().parents[1]
    defaults = (repo / "ansible" / "group_vars" / "all" / "defaults.yml").read_text(
        encoding="utf-8"
    )
    service_config = (
        repo / "ansible" / "roles" / "service_config" / "tasks" / "main.yml"
    ).read_text(encoding="utf-8")

    worker_config_paths = [
        "config/ado-worker/config.json",
        "config/discord-ingest/config.json",
        "config/discord-wake/config.json",
        "config/scheduled-wake/config.json",
        "config/teams-ingest/config.json",
        "config/teams-meeting-ingest/config.json",
        "config/trigger-orchestrator/config.json",
    ]

    assert "evander_force_render_worker_config" not in defaults
    assert "evander_force_render_worker_config" not in service_config
    assert "force: false" in service_config
    assert "Render managed worker/global JSON config" in service_config
    assert "no_log: true" in service_config
    assert 'path: "config/didww-sip-bridge.env"' in service_config
    for path in worker_config_paths:
        assert f'path: "{path}"' in service_config
        assert f"  - {path}" not in defaults


def test_ansible_uv_bootstrap_uses_python_install_environment() -> None:
    repo = Path(__file__).resolve().parents[1]
    host_prereqs = (repo / "ansible" / "roles" / "host_prereqs" / "tasks" / "main.yml").read_text(
        encoding="utf-8"
    )
    worker_defaults = (repo / "ansible" / "group_vars" / "evander_worker_hosts.yml").read_text(
        encoding="utf-8"
    )

    assert "Install uv for repo bootstrap" in host_prereqs
    assert 'environment: "{{ evander_python_install_env | default({}) }}"' in host_prereqs
    assert "PIP_INDEX_URL" in worker_defaults
    assert "evander_worker_pypi_index_url" in worker_defaults


def test_ansible_manages_current_optional_systemd_units() -> None:
    repo = Path(__file__).resolve().parents[1]
    defaults = (repo / "ansible" / "group_vars" / "all" / "defaults.yml").read_text(
        encoding="utf-8"
    )
    runtime = (repo / "ansible" / "roles" / "service_runtime" / "tasks" / "main.yml").read_text(
        encoding="utf-8"
    )
    verification = (repo / "ansible" / "roles" / "verification" / "tasks" / "main.yml").read_text(
        encoding="utf-8"
    )
    runtime_bootstrap = (
        repo / "ansible" / "roles" / "runtime_bootstrap" / "tasks" / "main.yml"
    ).read_text(encoding="utf-8")

    assert "didww_sip_bridge: true" in defaults
    assert "teams_meeting_ingest: true" in defaults
    assert "managed_git_refresh: true" in defaults
    assert "evander_didww_sip_bridge_env" in defaults
    assert "evander_teams_meeting_ingest_config" in defaults
    assert "config/teams-meeting-ingest" in runtime_bootstrap
    for key, unit in [
        ("didww_sip_bridge", "evander-didww-sip-bridge.service"),
        ("teams_meeting_ingest", "evander-teams-meeting-ingest.service"),
        ("managed_git_refresh", "evander-managed-git-refresh.service"),
    ]:
        assert f"key: {key}" in runtime
        assert f"unit: {unit}" in runtime
        assert f"key: {key}" in verification
        assert f"unit: {unit}" in verification
    assert "evander_services.get(item.key, false) | bool" in runtime
    assert "evander_services[item.key]" not in runtime
    assert "Inspect managed Git refresh broker socket" in verification
    assert "/run/evander/managed-git-refresh.sock" in verification


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
    assert 'enabled: "{{ evander_manage_workflow_config }}"' in service_config
    assert "when: item.enabled | default(true) | bool" in service_config
    assert "Copy managed workflow pack files" in service_config
    assert "evander_managed_workflow_pack_files" in service_config
    assert "Check whether a controller-side workflow editor layout exists" in service_config
    assert "Copy managed workflow editor layout" in service_config
    assert "evander_workflow_layout_source_path" in service_config


def test_ansible_restarts_services_after_managed_config_changes() -> None:
    repo = Path(__file__).resolve().parents[1]
    service_config = (
        repo / "ansible" / "roles" / "service_config" / "tasks" / "main.yml"
    ).read_text(encoding="utf-8")
    service_runtime = (
        repo / "ansible" / "roles" / "service_runtime" / "tasks" / "main.yml"
    ).read_text(encoding="utf-8")

    assert "evander_managed_service_config_changed" in service_config
    assert "evander_env_file_render.changed" in service_config
    assert "evander_json_config_render.changed" in service_config
    assert "evander_managed_service_config_changed | default(false)" in service_runtime
    assert "'restarted'" in service_runtime


def test_ansible_ado_worker_defaults_omit_retired_setup_keys() -> None:
    repo = Path(__file__).resolve().parents[1]
    defaults = (repo / "ansible" / "group_vars" / "all" / "defaults.yml").read_text(
        encoding="utf-8"
    )

    assert "evander_ado_worker_config:" in defaults
    assert "processed_store_path:" not in defaults
    assert "ADO_WORK_ITEMS_AZURE_DEVOPS_PAT" not in defaults
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
    verification = (repo / "ansible" / "roles" / "verification" / "tasks" / "main.yml").read_text(
        encoding="utf-8"
    )

    assert "proxy_issue_sync: true" in defaults
    assert "key: proxy_issue_sync" in service_runtime
    assert "unit: evander-proxy-issue-sync.service" in service_runtime
    assert "key: proxy_issue_sync" in verification
    assert "unit: evander-proxy-issue-sync.service" in verification


def test_ansible_systemd_install_passes_service_profile() -> None:
    repo = Path(__file__).resolve().parents[1]
    text = (repo / "ansible" / "roles" / "systemd_install" / "tasks" / "main.yml").read_text(
        encoding="utf-8"
    )

    assert "EVANDER_SERVICE_PROFILE" in text
    assert "{{ evander_service_profile }}" in text


def test_ansible_verification_checks_nexus_package_cache() -> None:
    repo = Path(__file__).resolve().parents[1]
    verification = (repo / "ansible" / "roles" / "verification" / "tasks" / "main.yml").read_text(
        encoding="utf-8"
    )

    assert "evander-nexus.service" in verification
    assert "evander_nexus_port" in verification
    assert "http://127.0.0.1:{{ evander_nexus_port }}/service/rest/v1/status" in verification
