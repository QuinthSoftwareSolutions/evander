# Ansible Deployment

This bundle bootstraps a fresh Ubuntu 24.04 host for `agent-sandbox` by orchestrating the repo's
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
- If you deploy on Azure and only need the host control plane, use `agent_sandbox_service_profile:
  control_plane_only`
- If you deploy on Azure and need local guest VMs on the same host, keep the `full` profile and use
  a VM size that supports nested virtualization

## Layout

- `site.yml`: main deployment playbook
- `worker.yml`: dedicated container-worker VM deployment playbook
- `inventory/hosts.example.yml`: example inventory
- `group_vars/all/defaults.yml`: documented variable contract and defaults
- `group_vars/agent_sandbox_worker_hosts.yml`: container-worker VM defaults
- `roles/`: deployment steps grouped by responsibility

## Managed Flow

The playbook follows the repo-native bootstrap order:

1. Install Ubuntu packages, Node/npm, and `uv`
2. Clone or update the repo
3. Create `.venv` and install the package
4. Run `agent-sandbox-cli init-state --runtime-dir runtime`
5. Render managed config under `config/` and optional `secrets.env`
6. Run `scripts/install_systemd_units.sh`
7. Build the Admin Web frontend bundle with `npm ci && npm run build`
8. Run `scripts/create_libvirt_network.sh` in the `full` profile
9. Run `scripts/apply_firewall.sh` when the `full` profile firewall service is enabled
10. Run `scripts/create_base_vm.sh` in the `full` profile
11. Enable and start only the selected repo-native services
12. Install and configure the Nexus PyPI/npm package cache when enabled
13. Verify unit state, enabled firewall, full-profile VM host state, package-cache health, and repo-aware status

VM/session launch is intentionally not part of initial deployment. After the host is prepared, use
the existing operator flow to create a session manifest and launch a VM.

## Variable Contract

Core required variables:

- `agent_sandbox_repo_url`
- `agent_sandbox_repo_version`
- `agent_sandbox_deploy_path`
- `agent_sandbox_run_user`

Core defaults:

- `agent_sandbox_runtime_dir`: `{{ agent_sandbox_deploy_path }}/runtime`
- `agent_sandbox_service_profile`: `full`
- `agent_sandbox_run_user_supplementary_groups`: `libvirt`, `kvm` for `full`; empty for
  `control_plane_only`
- `agent_sandbox_allowed_tcp_ports`: `22`, `8000`, `11434`, `11666`, `11777`, and `8081`
  when `agent_sandbox_services.package_cache` is enabled

Service profiles:

- `full`: installs local VM host packages, adds the run user to `libvirt`/`kvm`, creates the
  libvirt network, downloads the base cloud image, and verifies local VM host state.
- `control_plane_only`: skips local VM host packages, libvirt/kvm run-user groups, the repo-native
  guest firewall, libvirt network creation, base-image download, and local VM host verification. Use
  this for an Azure VM that only runs the control-plane services.

The playbook validates `agent_sandbox_service_profile` as either `full` or `control_plane_only`.
Other values fail during preflight instead of silently behaving like a non-full profile.

Service toggles:

- `agent_sandbox_services.admin_web`
- `agent_sandbox_services.gateway`
- `agent_sandbox_services.firewall`
- `agent_sandbox_services.openai_relay`
- `agent_sandbox_services.nuget_relay`
- `agent_sandbox_services.package_cache`
- `agent_sandbox_services.http_target_relay`
- `agent_sandbox_services.ado_worker`
- `agent_sandbox_services.discord_ingest`
- `agent_sandbox_services.discord_wake`
- `agent_sandbox_services.scheduled_wake`
- `agent_sandbox_services.teams_ingest`
- `agent_sandbox_services.effect_orchestrator`
- `agent_sandbox_services.trigger_orchestrator`

Workflow config ownership:

- `agent_sandbox_manage_workflow_config: true` renders
  `config/trigger-orchestrator/config.json` from Ansible variables, copies managed workflow pack
  examples into their operator-local runtime filenames, and copies the pack-scoped workflow layout
  source configured by `agent_sandbox_workflow_layout_source_path` when it exists.
- Set `agent_sandbox_manage_workflow_config: false` for hosts where operators edit workflows through
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
- `config/http-target-relay.env`
- `config/ado-worker.env`
- `config/ado-worker/config.json`
- `config/discord-ingest/config.json`
- `config/discord-wake/config.json`
- `config/scheduled-wake/config.json`
- `config/teams-ingest/config.json`
- `config/host-heartbeat/config.json`
- `config/trigger-orchestrator/config.json`
- optional `secrets.env`

The Nexus package cache also requires `agent_sandbox_nexus_admin_password` from Vault when
`agent_sandbox_services.package_cache` is enabled. The role changes the initial Nexus admin
password, enables anonymous read access, and creates only proxy/group repositories:
`pypi-proxy`, `pypi-all`, `npm-proxy`, and `npm-all`.
The Azure inventory maps this from `vault_agent_sandbox_nexus_admin_password`; add that value to
the Vault file or set `agent_sandbox_services.package_cache: false` for deployments that should not
run Nexus.

Worker/global JSON files are also editable from the Admin GUI. Set
`agent_sandbox_force_render_worker_config: false` for hosts where operators own those settings
after first install; the playbook will still create missing worker config files but will not replace
existing ones.

## First Install

1. Copy the example inventory and defaults to your operator-managed inventory.
2. Put secret values in Vault-backed vars.
3. Run:

```bash
cd /path/to/agent-sandbox
ansible-playbook -i ansible/inventory/hosts.example.yml ansible/site.yml
```

If your control host needs a Vault password file or prompt:

```bash
ansible-playbook \
  -i ansible/inventory/prod.yml \
  --ask-vault-pass \
  ansible/site.yml
```

## Re-Runs And Idempotency

The playbook is intended to be safe to re-run:

- apt packages converge in place
- the Git checkout updates to the requested revision
- `uv venv` and `uv pip install` reconcile the Python environment
- `npm ci` and `npm run build` refresh the Admin Web frontend bundle
- `init-state` is re-run against the same runtime directory
- managed config files are re-rendered from variables
- worker/global JSON files are preserved when `agent_sandbox_force_render_worker_config: false`
- `create_libvirt_network.sh` and `create_base_vm.sh` are repo-native and idempotent in the `full`
  profile
- if the configured run user's supplementary groups change, managed services are restarted so
  the new process credentials take effect for libvirt-backed actions such as `virsh start`
- systemd enablement and service state are reconciled to the selected profile

## Libvirt Access For The Run User

For the `full` profile, the configured `agent_sandbox_run_user` must be able to issue writable
libvirt operations for resume and VM lifecycle flows. By default, the playbook ensures that user is
in the supplementary groups listed under `agent_sandbox_run_user_supplementary_groups`, which
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
- Admin Web responds on the configured host port
- for `full`: libvirt network `agent-sandbox` exists
- for `full` when firewall is enabled: nftables table `inet agent_sandbox` exists
- for `full`: the Ubuntu 24.04 base cloud image is present under libvirt-accessible storage
- `.venv/bin/agent-sandbox-cli status --runtime-dir runtime` succeeds
- local relay endpoints respond where applicable
- when enabled, `agent-sandbox-nexus.service` is active and
  `http://127.0.0.1:8081/service/rest/v1/status` responds

After the playbook completes, common manual follow-ups are:

```bash
cd /opt/agent-sandbox
./.venv/bin/agent-sandbox-cli status --runtime-dir runtime
curl -I http://127.0.0.1:8100/healthz
sudo systemctl status agent-sandbox-admin-web.service
sudo systemctl status agent-sandbox-gateway.service
# Full-profile hosts only:
sudo virsh net-info agent-sandbox
```

If you expose Admin Web beyond localhost, set `config/admin-web.env` from Vault-backed variables,
enable authentication, and prefer TLS plus ingress policy in front of the service.

## Container Worker VMs

Use `worker.yml` for Azure VMs that only run the container execution worker. This is intentionally
separate from `site.yml`: the worker VM gets Docker, a checkout of this repo, the Python package, a
durable worker root, and one systemd unit for `agent-sandbox-worker-daemon`. It does not install the
control-plane services, libvirt, the repo-native guest firewall, Admin Web, relays, or ADO workers.

The worker inventory group is `agent_sandbox_worker_hosts`. Worker defaults live in
`group_vars/agent_sandbox_worker_hosts.yml`; override at least these values from inventory or Vault:

- `agent_sandbox_repo_url`
- `agent_sandbox_repo_version`
- `agent_sandbox_repo_ssh_private_key` when the repo is private
- `agent_sandbox_worker_token`
- `agent_sandbox_git_host` with the control-plane private IP or DNS name
- `agent_sandbox_git_user`, `agent_sandbox_git_root`, and `agent_sandbox_git_ssh_alias` when they
  differ from the control-plane issue-setup Git settings

When the inventory includes `agent_sandbox_hosts`, the worker defaults derive
`agent_sandbox_git_user` from that control-plane host's `agent_sandbox_run_user` and
`agent_sandbox_git_root` from that host's `agent_sandbox_deploy_path`. Override those values only
when the Git mirror is intentionally served by a different account or path.

Run the worker deployment with:

```bash
./scripts/build_agent_sandbox_runner_image.sh
ansible-playbook \
  -i ansible/inventory/prod.yml \
  --ask-vault-pass \
  ansible/worker.yml
```

The worker playbook renders systemd units from the local Ansible checkout and installs the daemon
package from Git on the worker VM. To keep those artifacts compatible, the worker defaults set
`agent_sandbox_repo_version` to the local playbook commit before checkout and verify that
worker-playbook/daemon contract files are committed. Commit and push local worker changes before
deploying. If you intentionally deploy a different pinned revision, run the playbook from that
revision or set `agent_sandbox_worker_enforce_revision_match: false` only after confirming the
rendered unit and daemon CLI are compatible.

The worker daemon listens on `agent_sandbox_worker_port` (`11888` by default) and stores durable
session state under `agent_sandbox_worker_root` (`/var/lib/agent-worker` by default). Restrict Azure
NSG or subnet rules so only the control-plane VM can reach that port. The daemon token is rendered
only to `config/worker-daemon.env`; set the same value on the control plane through the environment
variable named by `container_worker_token_env`.

The worker playbook also provisions restricted SSH for Git mirrors. It generates
`~agent-worker/.ssh/agent-sandbox-git`, renders an SSH config entry for
`agent_sandbox_git_ssh_alias`, and, when the `agent_sandbox_hosts` group is present, installs the
worker public key into the control-plane Git user's `authorized_keys` with
`git_ssh_command_wrapper.sh` as the forced command and `from=<worker-private-cidr-or-ip>` via
`agent_sandbox_worker_git_allowed_from`. The worker daemon receives the same SSH directory through
`--git-ssh-source-dir` and copies that restricted config/key into each session `agent_home`, so
in-container `git fetch` and `git push agent` use only the control-plane mirror.

The default allowed Git source prefix is
`ssh://{{ agent_sandbox_git_ssh_alias }}{{ agent_sandbox_git_root }}/`. The worker rejects
job-level and session repo clone requests outside `agent_sandbox_worker_allowed_git_source_prefixes`.
Do not grant worker hosts or runner containers direct GitHub/Azure DevOps egress for this mode; the
control plane owns upstream credentials and mirror refresh. npm and pip package access must use
separate registry proxies or mirrors.

Configure the control plane to point at the worker's private address:

```yaml
execution_backend: container_worker_vm
container_worker_base_url: "http://<worker-private-ip>:11888"
container_worker_token_env: "AGENT_SANDBOX_WORKER_TOKEN"
container_worker_default_image: "agent-sandbox-runner:latest"
container_worker_guest_openai_base_url: "http://<control-plane-private-ip>:11434/v1"
container_worker_guest_gateway_url: "http://<control-plane-private-ip>:8000/mcp"
container_worker_guest_nuget_relay_base_url: "http://<control-plane-private-ip>:11666"
container_worker_guest_pypi_index_url: "http://<control-plane-private-ip>:8081/repository/pypi-all/simple"
container_worker_guest_npm_registry_url: "http://<control-plane-private-ip>:8081/repository/npm-all/"
```

The runner image is deployed from `dist/agent-sandbox-runner.tar`; the worker playbook copies it to
the worker and runs `docker load`. The same playbook creates worker-level read-only cache
directories for .NET, Node/Playwright, Playwright browsers, and NuGet fallback packages under
`agent_sandbox_worker_toolchain_cache_root`, then wires them into the daemon with repeated
`--readonly-mount` flags. Populate those caches before relying on `dotnet=true` or
`playwright=true` sessions; npm workflows also require the worker Node cache to be populated so the
mounted Node/npm binary is present. Docker `host` networking remains refused; choose a private Docker
network/routing setup that can reach only the required control-plane relay and gateway endpoints
plus the Nexus package cache on `8081`. Worker outbound rules should still deny direct access to
`pypi.org` and `registry.npmjs.org`; the control plane is the only host that needs upstream package
internet access for these formats.
If the host Docker bridge cannot reach apt mirrors while building the tarball, build it with
`RUNNER_BUILD_NETWORK=host ./scripts/build_agent_sandbox_runner_image.sh`; this does not change the
worker daemon's runtime networking policy.

After the worker playbook and control-plane config are both deployed, verify the full path from the
control plane:

```bash
cd /opt/agent-sandbox
export AGENT_SANDBOX_WORKER_TOKEN='<same token>'
./.venv/bin/agent-sandbox-cli execution smoke-container-worker \
  --session-id smoke-worker \
  --image agent-sandbox-runner:latest \
  --json
```

The worker playbook verifies Docker, the daemon systemd unit, and `/healthz`. It does not run the
control-plane smoke command because that check belongs on the control-plane side and depends on the
selected runner image and private network path.
