# Ansible Deployment

This bundle bootstraps a fresh Ubuntu 24.04 host for `evander` by orchestrating the repo's
existing scripts and CLI entrypoints.

It does not replace the repo's operational model. Ansible installs host prerequisites, checks out
the repo, creates `.venv`, initializes `runtime/`, renders managed config files, installs systemd
units, applies the firewall when enabled, and starts the enabled services. In the default `full`
profile it also applies the repo-native guest firewall, creates the libvirt network, and downloads
the base Ubuntu cloud image.

## Supported Target

- Ubuntu 24.04
- `systemd`
- For the default `full` profile: `libvirt` and KVM-capable virtualization support
- If you deploy on Azure and only need the host control plane, use `evander_service_profile:
  control_plane_only`
- If you deploy on Azure and need local guest VMs on the same host, keep the `full` profile and use
  a VM size that supports nested virtualization

## Layout

- `site.yml`: main deployment playbook
- `worker.yml`: dedicated container-worker VM deployment playbook
- `inventory/hosts.example.yml`: example inventory
- `inventory/azure.example.yml`: example Azure inventory shape
- `group_vars/all/defaults.yml`: documented variable contract and defaults
- `group_vars/azure.example.yml`: example Azure deployment overrides
- `group_vars/all/vault.yml.example`: non-secret Vault variable template
- `group_vars/evander_worker_hosts.yml`: container-worker VM defaults
- `roles/`: deployment steps grouped by responsibility

## Managed Flow

The playbook follows the repo-native bootstrap order:

1. On existing installs, write `runtime/deployment-status.json` so Admin Web can show that an
   update is in progress
2. Install Ubuntu packages, Node/npm, and `uv`
3. Install and configure the apt-cacher-ng relay when enabled
4. Clone or update the repo
5. Create `.venv` and install the package
6. Run `evander-cli init-state --runtime-dir runtime`
7. Render managed config under `config/` and optional `secrets.env`
8. Optionally install and configure Asterisk for the DIDWW SIP bridge
9. Run `scripts/install_systemd_units.sh`
10. Build the Admin Web frontend bundle with `npm ci && npm run build`
11. Install/configure Caddy for Admin Web public static-file serving when enabled
12. Run `scripts/create_libvirt_network.sh` in the `full` profile
13. Run `scripts/apply_firewall.sh` when the `full` profile firewall service is enabled
14. Run `scripts/create_base_vm.sh` in the `full` profile
15. Enable and start only the selected repo-native services
16. Install and configure the Nexus PyPI/npm package cache when enabled
17. Verify unit state, enabled firewall, full-profile VM host state, package-cache health, Admin
    Web Caddy routing when enabled, and repo-aware status
18. Clear the Admin Web deployment-status flag after a successful run

VM/session launch is intentionally not part of initial deployment. After the host is prepared, use
the existing operator flow to create a session manifest and launch a VM.

## Variable Contract

Core required variables:

- `evander_repo_url`
- `evander_repo_version`
- `evander_deploy_path`
- `evander_run_user`

Core defaults:

- `evander_runtime_dir`: `{{ evander_deploy_path }}/runtime`
- `evander_deployment_status_path`: `{{ evander_runtime_dir }}/deployment-status.json`
- `evander_deployment_status_message`: banner text shown by Admin Web during deployment
- `evander_deployment_status_ttl_seconds`: stale-file cutoff for the Admin Web deployment banner
- `evander_service_profile`: `full`
- `evander_run_user_supplementary_groups`: `libvirt`, `kvm` for `full`; empty for
  `control_plane_only`
- `evander_allowed_tcp_ports`: `22`, `8000`, `11434`, `11666`, `11777`, plus `8081`
  when `evander_services.package_cache` is enabled and `3142` when
  `evander_services.apt_cache` is enabled, and `11881` when
  `evander_services.npm_audit_facade` is enabled. The optional Qdrant semantic store binds
  to localhost and is not added to guest firewall allowances.

Service profiles:

- `full`: installs local VM host packages, adds the run user to `libvirt`/`kvm`, creates the
  libvirt network, downloads the base cloud image, and verifies local VM host state.
- `control_plane_only`: skips local VM host packages, libvirt/kvm run-user groups, the repo-native
  guest firewall, libvirt network creation, base-image download, and local VM host verification. Use
  this for an Azure VM that only runs the control-plane services.

The playbook validates `evander_service_profile` as either `full` or `control_plane_only`.
Other values fail during preflight instead of silently behaving like a non-full profile.

Service toggles:

- `evander_services.admin_web`
- `evander_services.gateway`
- `evander_services.firewall`
- `evander_services.openai_relay`
- `evander_services.didww_sip_bridge`
- `evander_services.nuget_relay`
- `evander_services.npm_audit_facade`
- `evander_services.package_cache`
- `evander_services.apt_cache`
- `evander_services.semantic_store`
- `evander_services.http_target_relay`
- `evander_services.ado_worker`
- `evander_services.proxy_issue_sync`
- `evander_services.discord_ingest`
- `evander_services.discord_wake`
- `evander_services.scheduled_wake`
- `evander_services.teams_ingest`
- `evander_services.teams_meeting_ingest`
- `evander_services.effect_orchestrator`
- `evander_services.trigger_orchestrator`

DIDWW SIP bridge Asterisk provisioning:

- `evander_services.didww_sip_bridge` controls the Python bridge systemd unit.
- `evander_asterisk_didww_enabled` controls whether Ansible also installs Asterisk and
  renders `/etc/asterisk/http.conf`, `ari.conf`, `rtp.conf`, `pjsip.conf`, and `extensions.conf`.
  It defaults to `false` so existing hand-managed Asterisk hosts are not overwritten.
- When enabled, Asterisk ARI and PJSIP values are tied to the bridge env defaults:
  `ASTERISK_ARI_USER`, `ASTERISK_ARI_APP`, and `ASTERISK_PJSIP_ENDPOINT`. Set
  `evander_didww_sip_bridge_ari_user` if the ARI user should differ from
  `evander_run_user`.
- Set `evander_asterisk_public_address` to the control-plane public SIP/RTP address,
  `evander_asterisk_local_nets` to the private subnet CIDRs, and the DIDWW trunk values from
  Vault: `vault_didww_sip_username`, `vault_didww_sip_password`, and optionally
  `vault_didww_sip_from_user`.
- The default DIDWW source CIDRs are `46.19.208.0/21` and `185.238.172.0/22`; override
  `evander_asterisk_didww_identify_matches` if DIDWW assigns different signaling sources.
- For inbound routes, set `evander_asterisk_didww_inbound_route_id` and enable
  `evander_didww_sip_bridge_enable_inbound_phone_calls`.

Admin Web public access:

- `evander_admin_web_caddy_enabled: false` by default. Set it to `true` on deployments that
  expose Admin Web through Caddy.
- The managed Caddy config proxies `/`, `/index.html`, `/api/*`, `/public/*`, and `/healthz` to
  the local Admin Web Python service, while serving hashed frontend assets from
  `frontend/workflow-editor/dist` directly. The proxied document entrypoints let Admin Web inject
  the configured title and branding payload before the React app starts.
- Admin Web defaults to the bundled Evander and Quinth Software Solutions branding under
  `frontend/workflow-editor/branding`. Runtime overrides still come from `evander_admin_web_env`.
  For custom local logo files, set `EVANDER_ADMIN_WEB_BRANDING_ASSET_DIR` and use relative logo
  paths so Caddy reaches them through the proxied `/api/admin/branding/assets/...` route.
- To copy operator-owned logo files from the Ansible controller, set
  `evander_admin_web_branding_assets` to records with `source_path`, destination `path`, and
  optional `mode`. The files are copied into
  `evander_admin_web_branding_asset_dir`, which defaults to the configured
  `EVANDER_ADMIN_WEB_BRANDING_ASSET_DIR`.
- `evander_admin_web_caddy_hostname` defaults to the host from
  `EVANDER_ADMIN_WEB_PUBLIC_BASE_URL`; override it when the public hostname differs from the
  Admin Web base URL.
- The post-deploy Caddy probe retries the local HTTPS/SNI path. Tune
  `evander_admin_web_caddy_verification_retries`,
  `evander_admin_web_caddy_verification_delay_seconds`,
  `evander_admin_web_caddy_verification_connect_timeout_seconds`, and
  `evander_admin_web_caddy_verification_max_time_seconds` if certificate or listener
  readiness is slower on a target host.
- Azure deployments can enable this role by setting
  `evander_admin_web_caddy_enabled: true` in operator-owned group vars.

Workflow config ownership:

- `evander_manage_workflow_config: true` renders
  `config/trigger-orchestrator/config.json` from Ansible variables, copies managed workflow pack
  examples into their operator-local runtime filenames, and copies the pack-scoped workflow layout
  source configured by `evander_workflow_layout_source_path` when it exists.
- Set `evander_manage_workflow_config: false` for hosts where operators edit workflows through
  Admin Web and deployment must not overwrite those edits.
- The live workflow config, pack, and pack-layout files are ignored by Git. Checked-in examples use
  `config/trigger-orchestrator/config-example.json` and `config/workflow-packs/*-example.json`.
- The repo checkout role also resets tracked deploy-generated frontend files such as
  `frontend/workflow-editor/dist/index.html` before updates, because the frontend build can rewrite
  those files on the target host.

Secret-bearing variables should come from encrypted inventory or Ansible Vault. The playbook
renders these host-local managed artifacts with restricted permissions:

- `config/admin-web.env`
- `config/firewall.env`
- `config/gateway.env`
- `config/openai-relay.env`
- `config/nuget-relay.env`
- `config/npm-audit-facade.env`
- `config/http-target-relay.env`
- `config/ado-worker.env`
- `config/didww-sip-bridge.env`
- `config/ado-worker/config.json`
- `config/discord-ingest/config.json`
- `config/discord-wake/config.json`
- `config/scheduled-wake/config.json`
- `config/teams-ingest/config.json`
- `config/teams-meeting-ingest/config.json`
- `config/trigger-orchestrator/config.json`
- optional `secrets.env`

The Nexus package cache also requires `evander_nexus_admin_password` from Vault when
`evander_services.package_cache` is enabled. The role changes the initial Nexus admin
password, accepts the Nexus Community Edition EULA when
`evander_nexus_accept_community_eula` is true, enables anonymous read access, and creates only
proxy/group repositories: `pypi-proxy`, `pypi-all`, `npm-proxy`, and `npm-all`. It also assigns the
anonymous user a least-privilege package-cache reader role so worker `uv` and npm installs can
download package assets through Nexus without direct PyPI/npm egress or worker-side Nexus
credentials.
Deployment-specific group vars usually map this from
`vault_evander_nexus_admin_password`; add that value to an encrypted Vault file or set
`evander_services.package_cache: false` for deployments that should not run Nexus.

The apt cache relay uses the Ubuntu `apt-cacher-ng` package and does not share Nexus. When
`evander_services.apt_cache` is enabled, the control plane listens on
`evander_apt_cache_bind_host:evander_apt_cache_port` (`0.0.0.0:3142` by default).
Worker VM apt provisioning points at
`http://{{ evander_control_plane_private_ip }}:{{ evander_apt_cache_port }}` before
the worker playbook's first `apt update`.

The npm audit facade uses `evander-npm-audit-facade.service`, listens on
`evander_npm_audit_facade_port` (`11881` by default), exposes `/healthz`, sends npm audit
POSTs to `https://registry.npmjs.org`, and keeps package metadata and tarballs on the control-plane
Nexus npm group. Keep any optional local overrides in `config/npm-audit-facade.env`.

The optional semantic store uses `evander-qdrant.service`, a systemd-managed Docker container
running `evander_qdrant_image` (`qdrant/qdrant:v1.18.0` by default). It persists data under
`evander_qdrant_data_dir` (`/var/lib/evander/qdrant`) and binds to
`evander_qdrant_bind_host:evander_qdrant_port` (`127.0.0.1:6333` by default). The role
verifies Qdrant readiness through `/healthz`.

Worker/global JSON files are also editable from the Admin GUI. The playbook creates missing worker
config files from Ansible variables, but never replaces existing worker config files. To re-seed one
from Ansible defaults, remove that specific live config file before a deployment.

## First Install

1. Copy the example inventory and defaults to your operator-managed inventory.
2. Put secret values in Vault-backed vars.
3. Run:

```bash
cd /path/to/evander
ansible-playbook -i ansible/inventory/hosts.example.yml ansible/site.yml
```

If your control host needs a Vault password file or prompt:

```bash
ansible-playbook \
  -i ansible/inventory/prod.yml \
  --ask-vault-pass \
  ansible/site.yml
```

## Azure Pipelines

Azure Pipelines deployment YAML is intentionally not checked into this product repo because it
usually contains client-specific service connection names, Secure File names, target hosts, public
IPs, NSG settings, and inventory paths. Keep that pipeline in an operator-owned deployment config
repo together with the real inventory and encrypted Vault files. The pipeline should reuse these
playbooks instead of replacing them:

1. `ansible-playbook -i <inventory> ansible/site.yml`
2. `./scripts/build_evander_runner_image.sh`
3. `./scripts/build_evander_claude_runner_image.sh`
4. `ansible-playbook -i <inventory> ansible/worker.yml`

The deployment pipeline should check out this product repo as a repository resource, compute the
checked-out product commit with `git rev-parse HEAD`, and pass that value as
`evander_repo_version` so target hosts deploy the same revision the pipeline built. The
inventory's `evander_repo_url` must point at this product repo or at a mirror that already
contains that exact commit. A lagging mirror fails during `repo_checkout` with
`fatal: reference is not a tree`.

Set the secret Azure Pipelines variable `ANSIBLE_VAULT_PASSWORD` so the pipeline can read the
encrypted Vault-backed variables. Upload the SSH private keys to Azure DevOps Secure Files and make
them available to the deployment pipeline.

The pipeline downloads those secure files per job, sets
`EVANDER_CONTROL_PLANE_SSH_KEY_FILE` and `EVANDER_WORKER_SSH_KEY_FILE`, and the
operator-owned Azure inventory uses those paths for Ansible SSH. A worker SSH path can use the
control-plane VM as a `ProxyCommand` jump host, so the pipeline agent must be able to reach the
control-plane public SSH endpoint and the control plane must be able to reach the worker private IP.
For local operator runs, export the same environment variables before invoking `ansible-playbook`.

For Microsoft-hosted agents, the pipeline can temporarily allow the current build agent's public
`/32` to reach SSH on the control-plane NSG. Set `azureServiceConnection` to an Azure Resource
Manager service connection with permission to read public IP/NIC/NSG resources and edit NSG rules
(`Network Contributor` on the NSG or containing resource group is sufficient). By default the NSG
resource group/name parameters stay `auto`: the pipeline resolves the NSG from
`controlPlanePublicIp`, adds a run-scoped `Allow-AzurePipelines-SSH-*` rule before Ansible, and
removes it in an `always()` cleanup step. If discovery cannot find the NSG, set
`controlPlaneSshNsgResourceGroup` and `controlPlaneSshNsgName` explicitly.

The selected agent pool must have Python and Docker for the runner-image build. A self-hosted agent
in the Azure VNet or on the control-plane host is usually the lowest-friction option for private
worker access.

The worker stage installs Node.js 24.14.0 before building `dist/evander-runner.tar` from a
host-seeded Codex binary and `dist/evander-runner-claude.tar` from a host-resolved Claude Code
command. On hosted agents the pipeline installs the npm package named by
`hostCodexNpmPackage`, then explicitly installs `@openai/codex-linux-x64` at the version declared by
the installed Codex package into `$(Pipeline.Workspace)/host-codex`. It exports that directory as
`HOST_CODEX_ROOT`, installs the workflow-editor npm dependencies, and downloads Chromium into
`$(Pipeline.Workspace)/ms-playwright`. It then runs
`scripts/build_evander_runner_image.sh` and
`scripts/build_evander_claude_runner_image.sh`. Set `HOST_CLAUDE_BIN` when `claude` is not on
the build host PATH.
Those scripts write `dist/evander-runner.tar.manifest` and
`dist/evander-runner-claude.tar.manifest` beside their tarballs and reuse cached archives when
the manifests still match. The manifest keys cover the Dockerfile text, the build script text,
`RUNNER_IMAGE_NAME`, the resolved agent executable path and SHA-256, provider package identity where
applicable, and the base-image policy from the Dockerfile `FROM` line. Use
`RUNNER_FORCE_REBUILD=1 ./scripts/build_evander_runner_image.sh` or
`./scripts/build_evander_runner_image.sh --force` for Codex, and
`RUNNER_FORCE_REBUILD=1 ./scripts/build_evander_claude_runner_image.sh` or
`./scripts/build_evander_claude_runner_image.sh --force` for Claude, to force a rebuild; that is the right path
when you want a moving base tag such as `ubuntu:24.04` refreshed, because the tag text can stay the
same while the remote image changes.
Before `ansible/worker.yml`, the pipeline resolves `HOST_NODE_ROOT` from the installed Node runtime
and exports `HOST_PLAYWRIGHT_NODE_MODULES` plus `HOST_PLAYWRIGHT_BROWSERS` so the worker toolchain
roles do not depend on nvm-style or home-cache hosted-agent paths. The default package spec is
`@openai/codex`; override it with a pinned package version when deployment reproducibility requires
it.

Before `ansible/worker.yml`, the worker stage also installs the SDK named by `dotnetSdkVersion` into
`$(Pipeline.Workspace)/dotnet` and exports that path as `HOST_DOTNET_ROOT`. This avoids archiving the
entire Microsoft-hosted agent dotnet installation, which may include many SDK/runtime versions. The
worker role installs `dotnet-ef` into the shared dotnet tools cache during deploy; override the
default EF tool version with `HOST_DOTNET_EF_VERSION`, or provide `HOST_DOTNET_TOOLS_ARCHIVE` to
deploy a prebuilt dotnet tool-path archive.

## Re-Runs And Idempotency

The playbook is intended to be safe to re-run:

- apt packages converge in place
- the Git checkout updates to the requested revision
- `uv venv` and `uv pip install` reconcile the Python environment
- `npm ci` and `npm run build` refresh the Admin Web frontend bundle
- when `evander_admin_web_caddy_enabled` is true, the managed Caddyfile is reconciled after
  the frontend bundle is built
- `init-state` is re-run against the same runtime directory
- managed env files and workflow pack files are re-rendered from variables
- worker/global JSON files are created when missing and otherwise preserved
- `create_libvirt_network.sh` and `create_base_vm.sh` are repo-native and idempotent in the `full`
  profile
- if the configured run user's supplementary groups change, managed services are restarted so
  the new process credentials take effect for libvirt-backed actions such as `virsh start`
- systemd enablement and service state are reconciled to the selected profile

## Libvirt Access For The Run User

For the `full` profile, the configured `evander_run_user` must be able to issue writable
libvirt operations for resume and VM lifecycle flows. By default, the playbook ensures that user is
in the supplementary groups listed under `evander_run_user_supplementary_groups`, which
defaults to `libvirt` and `kvm` for `full` deployments.

The `control_plane_only` profile leaves the supplementary group list empty by default because it
does not install local VM host packages or operate libvirt.

On a fresh deploy that is usually enough because the systemd units are installed and started after
the user is reconciled. On a re-run against an existing host, Ansible now restarts the managed
services if that group membership changes so the service processes pick up the new supplementary
groups. If you also expect interactive shell access with plain `virsh`, start a fresh login
session after the membership change.

## Post-Deploy Verification

The verification role checks:

- selected systemd units are enabled and active
- disabled units remain installed but not enabled/started
- when enabled, managed Asterisk is active and ARI credentials are accepted
- Admin Web responds on the configured host port
- when enabled, managed Caddy is active, proxies `/`, `/index.html`, `/public/*`, and `/healthz`,
  and serves one built JS asset plus one built CSS asset through retried local TLS/SNI resolution
- for `full`: libvirt network `evander` exists
- for `full` when firewall is enabled: nftables table `inet evander` exists
- for `full`: the Ubuntu 24.04 base cloud image is present under libvirt-accessible storage
- `.venv/bin/evander-cli status --runtime-dir runtime` succeeds
- local relay endpoints respond where applicable
- when enabled, `evander-nexus.service` is active and
  `http://127.0.0.1:8081/service/rest/v1/status` responds
- when enabled, `apt-cacher-ng.service` is active and
  `http://127.0.0.1:3142/acng-report.html` responds

After the playbook completes, common manual follow-ups are:

```bash
cd /opt/evander
./.venv/bin/evander-cli status --runtime-dir runtime
curl -I http://127.0.0.1:8100/healthz
sudo systemctl status evander-admin-web.service
sudo systemctl status evander-gateway.service
# Full-profile hosts only:
sudo virsh net-info evander
```

If you expose Admin Web beyond localhost, set `config/admin-web.env` from Vault-backed variables,
enable authentication, and prefer TLS plus ingress policy in front of the service. For Ansible-owned
Caddy exposure, set `evander_admin_web_caddy_enabled: true`; Caddy will proxy the HTML
entrypoints and API/health routes to the local Admin Web service while serving static hashed assets
directly. The default title and logos come from the product checkout under
`frontend/workflow-editor/branding`. Put environment-specific Admin Web title, header, and logo
settings in `evander_admin_web_env`; relative logo paths are served from
`EVANDER_ADMIN_WEB_BRANDING_ASSET_DIR` through `/api/admin/branding/assets/...`. Use
`evander_admin_web_branding_assets` when Ansible should also copy those logo files from the
operator-owned deployment config into that branding asset directory.
For Azure SSO deployments, `EVANDER_ADMIN_WEB_AZURE_ALLOWED_USERS` is a first-run seed for
`runtime/admin-web-users.json`; after the file exists, use the Admin Web Users tab for membership
and role changes.

## Container Worker VMs

Use `worker.yml` for Azure VMs that only run the container execution worker. This is intentionally
separate from `site.yml`: the worker VM gets Docker, a checkout of this repo, the Python package, a
durable worker root, and one systemd unit for `evander-worker-daemon`. It does not install the
control-plane services, libvirt, the repo-native guest firewall, Admin Web, relays, or ADO workers.

The worker inventory group is `evander_worker_hosts`. Worker defaults live in
`group_vars/evander_worker_hosts.yml`; override at least these values from inventory or Vault:

- `evander_repo_url`
- `evander_repo_version`
- `evander_repo_ssh_private_key` when the repo is private
- `evander_worker_token`
- `evander_git_host` with the control-plane private IP or DNS name
- `evander_git_user`, `evander_git_root`, and `evander_git_ssh_alias` when they
  differ from the control-plane issue-setup Git settings

When the inventory includes `evander_hosts`, the worker defaults derive
`evander_git_user` from that control-plane host's `evander_run_user` and
`evander_git_root` from that host's `evander_deploy_path`. Override those values only
when the Git mirror is intentionally served by a different account or path.

Run the worker deployment with:

```bash
./scripts/build_evander_runner_image.sh
./scripts/build_evander_claude_runner_image.sh
ansible-playbook \
  -i ansible/inventory/prod.yml \
  --ask-vault-pass \
  ansible/worker.yml
```

Deploy or re-run the control-plane `site.yml` first when `evander_services.apt_cache` is
enabled. The worker playbook renders `/etc/apt/apt.conf.d/01evander-proxy` before
`host_prereqs`, so the first worker `apt update` uses the private apt-cacher-ng relay instead of
direct Ubuntu mirror egress. Verify the private path from a worker with
`curl http://<control-plane-private-ip>:3142/acng-report.html`.

When `evander_worker_repo_checkout_via_control_plane_git` is enabled, the worker playbook
also prepares restricted SSH before `repo_checkout`, creates a control-plane bare mirror named by
`evander_worker_repo_mirror_name`, and points `evander_repo_url` at
`ssh://{{ evander_git_ssh_alias }}{{ evander_worker_repo_mirror_path }}`. This keeps the
worker deploy checkout on the private control-plane Git path instead of requiring worker egress to
GitHub. The control-plane checkout must already contain the requested `evander_repo_version`;
deploy `site.yml` first, then run `worker.yml`. During the worker run, Ansible publishes that exact
control-plane commit to the deployment mirror at `refs/heads/deploy/evander` before the worker
checkout task fetches from the mirror. That deployment mirror disables the restricted SSH wrapper's
normal auto-refresh path so the run-scoped deploy ref is not pruned during `git-upload-pack`.

The worker `python_env` install uses the control-plane Nexus PyPI group through
`evander_python_install_env`, setting `UV_DEFAULT_INDEX`, `UV_INDEX_URL`, `UV_INSECURE_HOST`,
`PIP_INDEX_URL`, and `PIP_TRUSTED_HOST` from `evander_worker_pypi_index_url`. This keeps
`uv pip install -e .` on the same private package path as runtime pip sessions. The control-plane
Nexus role and worker playbook both probe `evander_nexus_pypi_probe_asset_path` anonymously,
so a missing package-cache reader role or stale control-plane deployment fails before the worker
reaches the repo install.

The worker playbook renders systemd units from the local Ansible checkout and installs the daemon
package from Git on the worker VM. To keep those artifacts compatible, the worker defaults set
`evander_repo_version` to the local playbook commit before checkout and verify that
worker-playbook/daemon contract files are committed. Commit and push local worker changes before
deploying. If you intentionally deploy a different pinned revision, run the playbook from that
revision or set `evander_worker_enforce_revision_match: false` only after confirming the
rendered unit and daemon CLI are compatible.

The worker daemon listens on `evander_worker_port` (`11888` by default) and stores durable
session state under `evander_worker_root` (`/var/lib/agent-worker` by default). Restrict Azure
NSG or subnet rules so only the control-plane VM can reach that port. The daemon token is rendered
only to `config/worker-daemon.env`; set the same value on the control plane through the environment
variable named by `container_worker_token_env`.

When worker MSSQL seed restore is needed, enable the optional sidecar alongside worker MSSQL. The
sidecar is disabled by default and is installed/started only when both flags are true:

```yaml
evander_worker_mssql_enabled: true
evander_worker_mssql_seed_sidecar_enabled: true
evander_worker_mssql_seed_sidecar_token: "{{ vault_evander_worker_mssql_seed_sidecar_token }}"
evander_worker_mssql_seed_sidecar_port: 11990
evander_worker_mssql_seed_sidecar_backup_dir: "/var/lib/evander/mssql-seeds"
```

The worker playbook renders `config/mssql-seed-sidecar.env`, installs
`evander-mssql-seed-sidecar.service`, creates the seed backup directory owned by
SQL Server, and verifies `/healthz` on the sidecar port. Put approved `.bak`
files under the configured backup directory and register them as seed aliases in
the `mssql-seed-addon` Admin Web page. Restrict network rules so only the
control plane can reach the sidecar port, then configure the add-on sidecar URL
as `http://<worker-private-ip>:11990` with token env
`EVANDER_MSSQL_SEED_SIDECAR_TOKEN`.

The worker playbook also provisions restricted SSH for Git mirrors. It generates
`~agent-worker/.ssh/evander-git`, renders an SSH config entry for
`evander_git_ssh_alias`, and, when the `evander_hosts` group is present, installs the
worker public key into the control-plane Git user's `authorized_keys` with
`git_ssh_command_wrapper.sh` as the forced command and `from=<worker-private-cidr-or-ip>` via
`evander_worker_git_allowed_from`. The worker daemon receives the same SSH directory through
`--git-ssh-source-dir` and copies that restricted config/key into each session `agent_home`, so
in-container `git fetch` can read control-plane mirrors under `evander_git_root` while pushes
remain disabled for the shared worker key.

The default allowed Git source prefix is
`ssh://{{ evander_git_ssh_alias }}{{ evander_git_root }}/`. The worker rejects
job-level and session repo clone requests outside `evander_worker_allowed_git_source_prefixes`.

The worker daemon still treats `none` as the safe default for disposable job containers. The
Azure/single-worker deployment slice uses a non-`none` network only when
`evander_worker_egress_policy_enabled: true` is set alongside the worker network override.
That worker-managed policy lives in `DOCKER-USER`, blocks `169.254.0.0/16` including
`169.254.169.254`, and only allows the explicit private control-plane endpoints from
`group_vars/all/defaults.yml` unless you append more entries.

Those default endpoints are:

- control-plane Git SSH on `22`
- control-plane MCP gateway on `8000`
- control-plane OpenAI relay on `11434`
- control-plane NuGet relay on `11666`
- control-plane HTTP target relay on `11777`
- control-plane npm audit facade on `11881`
- control-plane Nexus package cache on `8081`
- control-plane apt-cacher-ng on `3142`

The single-worker override is intentionally narrow. It does not grant public internet egress, and
it does not replace the worker-host SQL Server rules that already manage container-to-host SQL
access when `worker_mssql` is enabled.

Example inventory override and worker-side verification:

```yaml
evander_worker_docker_network: "bridge"
evander_worker_egress_policy_enabled: true
```

```bash
sudo systemctl status evander-worker-egress-policy.service
sudo iptables -S DOCKER-USER
evander-cli execution smoke-container-worker --json
```
Do not grant worker hosts or runner containers direct GitHub/Azure DevOps egress for this mode; the
control plane owns upstream credentials and mirror refresh. Worker host apt provisioning uses
apt-cacher-ng on `3142`; npm and pip package access uses the separate Nexus registry proxies on
`8081`, while npm audit POSTs can go through the host-side facade on `11881`.

Configure the control plane to point at the worker's private address:

```yaml
execution_backend: container_worker_vm
container_worker_base_url: "http://<worker-private-ip>:11888"
container_worker_token_env: "EVANDER_WORKER_TOKEN"
container_worker_default_image: "evander-runner:latest"
container_worker_images:
  default: "evander-runner:latest"
  claude: "evander-runner-claude:latest"
  dotnet-playwright: "evander-runner-dotnet-playwright:2026-06-05"
container_worker_guest_openai_base_url: "http://<control-plane-private-ip>:11434/v1"
container_worker_guest_anthropic_base_url: "http://<control-plane-private-ip>:11434/v1"
container_worker_guest_gateway_url: "http://<control-plane-private-ip>:8000/mcp"
container_worker_guest_http_target_relay_base_url: "http://<control-plane-private-ip>:11777"
container_worker_guest_nuget_relay_base_url: "http://<control-plane-private-ip>:11666"
container_worker_guest_pypi_index_url: "http://<control-plane-private-ip>:8081/repository/pypi-all/simple"
container_worker_guest_npm_registry_url: "http://<control-plane-private-ip>:8081/repository/npm-all/"
```

Use `container_worker_images` for the approved alias-to-image catalog. Keep
`container_worker_default_image` as the fallback image reference for sessions that do not select an
alias. The worker playbook does not pull or build images during runtime dispatch, so any approved
image refs that are not already included in the repo runner image archives should be listed in
`evander_worker_preload_images` and pre-pulled during deployment.

The default Codex and Claude runner images are deployed from `dist/evander-runner.tar` and
`dist/evander-runner-claude.tar`; the worker playbook copies each configured archive to the
worker and runs `docker load`. Anthropic-backed Claude sessions should use
`model_provider_mode: "anthropic"` and select the `claude` image alias. The same playbook creates
worker-level read-only cache directories for .NET, Node/Playwright, Playwright browsers, and NuGet
fallback packages under
`evander_worker_toolchain_cache_root`, then wires them into the daemon with repeated
`--readonly-mount` flags. The .NET cache is seeded from the Ansible control machine
(`HOST_DOTNET_ROOT`, `/usr/lib/dotnet`, or
`evander_worker_dotnet_toolchain_archive_path`), and the .NET tools cache is seeded with
`dotnet-ef` or copied from `evander_worker_dotnet_tools_archive_path`. The playbook also
stages `Microsoft.Extensions.FileSystemGlobbing` into the worker NuGet fallback cache, or copies a
prebuilt `evander_worker_nuget_fallback_archive_path` /
`HOST_NUGET_FALLBACK_ARCHIVE`, and verifies an offline `dotnet restore` inside
`evander-runner:latest` using that fallback cache. The older
`evander_worker_dotnet_restore_support_archive_path` /
`HOST_DOTNET_RESTORE_SUPPORT_ARCHIVE` names remain supported. The Node
cache is seeded the same way
(`HOST_NODE_ROOT`, `~/.nvm/versions/node/v24.14.0`, or
`evander_worker_node_toolchain_archive_path`) and verified by running `node`, `npm`, and `npx`.
The Playwright runtime and browser caches are seeded from `HOST_PLAYWRIGHT_NODE_MODULES` or the repo
frontend `node_modules`, plus `HOST_PLAYWRIGHT_BROWSERS` or `~/.cache/ms-playwright`; archive
variables can provide prebuilt tarballs. Playwright-enabled container sessions resolve the seeded
Chromium executable and export it as `PLAYWRIGHT_MCP_EXECUTABLE_PATH`, `CHROME_BIN`, and
`CHROMIUM_BIN`. Populate the NuGet fallback cache before relying on fallback restores for
project-specific packages beyond the playbook's restore-support seed; use
`scripts/prewarm_container_worker_nuget_cache.sh` from the control plane to restore through the
host NuGet relay and additively update worker caches. Docker `host` networking
remains refused; choose a private Docker
network/routing setup that can reach only the required control-plane relay and gateway endpoints
plus the apt relay on `3142` and the Nexus package cache on `8081`. Worker outbound rules should still deny direct access to Ubuntu mirrors, `pypi.org`, and `registry.npmjs.org`; the control plane is the only host that needs upstream package internet access for these formats.
If the host Docker bridge cannot reach apt mirrors while building either tarball, build it with
`RUNNER_BUILD_NETWORK=host ./scripts/build_evander_runner_image.sh` or
`RUNNER_BUILD_NETWORK=host ./scripts/build_evander_claude_runner_image.sh`; this does not
change the worker daemon's runtime networking policy. This first slice keeps Ansible's
tar-copy/load deployment model; worker-side remote manifest comparison is deferred because the host-side cache
already skips the expensive local `docker build`/`docker save` path when inputs are unchanged, while
a remote skip would need a separate trusted manifest storage/read path on the worker.

After the worker playbook and control-plane config are both deployed, verify the full path from the
control plane:

```bash
cd /opt/evander
export EVANDER_WORKER_TOKEN='<same token>'
./.venv/bin/evander-cli execution smoke-container-worker \
  --session-id smoke-worker \
  --image evander-runner:latest \
  --json
```

The worker playbook verifies Docker, the daemon systemd unit, and `/healthz`. It does not run the
control-plane smoke command because that check belongs on the control-plane side and depends on the
selected runner image and private network path.
