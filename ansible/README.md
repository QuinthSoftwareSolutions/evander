# Ansible Deployment

This bundle bootstraps a fresh Ubuntu 24.04 host for `agent-sandbox` by orchestrating the repo's
existing scripts and CLI entrypoints.

It does not replace the repo's operational model. Ansible installs host prerequisites, checks out
the repo, creates `.venv`, initializes `runtime/`, renders managed config files, installs systemd
units, applies the firewall when enabled, and starts the enabled services. In the default `full`
profile it also creates the libvirt network and downloads the base Ubuntu cloud image.

## Supported Target

- Ubuntu 24.04
- `systemd` and `nftables`
- For the default `full` profile: `libvirt` and KVM-capable virtualization support
- If you deploy on Azure and only need the host control plane, use `agent_sandbox_service_profile:
  control_plane_only`
- If you deploy on Azure and need local guest VMs on the same host, keep the `full` profile and use
  a VM size that supports nested virtualization

## Layout

- `site.yml`: main deployment playbook
- `inventory/hosts.example.yml`: example inventory
- `group_vars/all.yml`: documented variable contract and defaults
- `roles/`: deployment steps grouped by responsibility

## Managed Flow

The playbook follows the repo-native bootstrap order:

1. Install Ubuntu packages and `uv`
2. Clone or update the repo
3. Create `.venv` and install the package
4. Run `agent-sandbox-cli init-state --runtime-dir runtime`
5. Render managed config under `config/` and optional `secrets.env`
6. Run `scripts/install_systemd_units.sh`
7. Run `scripts/create_libvirt_network.sh` in the `full` profile
8. Run `scripts/apply_firewall.sh` when the firewall service is enabled
9. Run `scripts/create_base_vm.sh` in the `full` profile
10. Enable and start only the selected services
11. Verify unit state, enabled firewall, full-profile VM host state, and repo-aware status

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
- `agent_sandbox_allowed_tcp_ports`: `22`, `8000`, `11434`, `11666`, `11777`

Service profiles:

- `full`: installs local VM host packages, adds the run user to `libvirt`/`kvm`, creates the
  libvirt network, downloads the base cloud image, and verifies local VM host state.
- `control_plane_only`: skips local VM host packages, libvirt/kvm run-user groups, libvirt network
  creation, base-image download, and local VM host verification. Use this for an Azure VM that only
  runs the control-plane services.

Service toggles:

- `agent_sandbox_services.admin_web`
- `agent_sandbox_services.gateway`
- `agent_sandbox_services.firewall`
- `agent_sandbox_services.openai_relay`
- `agent_sandbox_services.nuget_relay`
- `agent_sandbox_services.http_target_relay`
- `agent_sandbox_services.ado_worker`
- `agent_sandbox_services.discord_ingest`
- `agent_sandbox_services.discord_wake`
- `agent_sandbox_services.scheduled_wake`
- `agent_sandbox_services.teams_ingest`
- `agent_sandbox_services.effect_orchestrator`
- `agent_sandbox_services.trigger_orchestrator`

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
- `init-state` is re-run against the same runtime directory
- managed config files are re-rendered from variables
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
- when firewall is enabled: nftables table `inet agent_sandbox` exists
- for `full`: the Ubuntu 24.04 base cloud image is present under libvirt-accessible storage
- `.venv/bin/agent-sandbox-cli status --runtime-dir runtime` succeeds
- local relay endpoints respond where applicable

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
