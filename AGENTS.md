# Evander Repo Notes

This file is for future Codex instances working in the `evander` repo.

## Purpose

This repo implements a host-side control plane for isolated agent execution. The main execution
target is still disposable libvirt VMs, with an additive `container_worker_vm` backend for worker
VMs that run disposable Docker containers.

The security model is:

- no general guest/container internet by default
- host keeps real secrets
- guests and worker containers get only explicit host/control-plane ports
- model, package, Git, HTTP-target, and MCP access go through narrow host-owned surfaces
- code transport uses Git, not MCP payloads

## Current Core Surfaces

- MCP gateway:
  - Entry point: `evander-gateway`
  - Code: `src/evander/gateway.py`
  - Default guest-reachable port: `8000`
  - Gateway liveness is checked on `/mcp`; a plain GET can return `406 Not Acceptable` and still
    prove the MCP endpoint is reachable.

- OpenAI relay:
  - Entry point: `evander-openai-relay`
  - Code: `src/evander/relays/openai.py`
  - Default guest-reachable port: `11434`
  - The streaming path intentionally keeps the upstream client alive for the full stream and
    forwards raw SSE bytes.

- NuGet relay:
  - Entry point: `evander-nuget-relay`
  - Code: `src/evander/relays/nuget.py`
  - Default guest-reachable port: `11666`
  - Health endpoint: `/healthz`

- HTTP target relay:
  - Entry point: `evander-http-target-relay`
  - Code: `src/evander/relays/http_target.py`
  - Default guest-reachable port: `11777`
  - Session-scoped relay for allowlisted external HTTP targets. It forwards guest-managed auth
    headers/cookies; host secrets are not injected into the guest request.

- Admin Web:
  - Entry point: `evander-admin-web`
  - Code: `src/evander/admin/web/app.py`
  - Default port from `config/admin-web.env.example`: `8100`
  - This is the supported operator GUI for dashboard, VMs/issues, sessions, approvals, tool-call
    audit, workflow editing/runs/reports, configuration, workers, skills, chat, and users.

- Workflow runtime:
  - Trigger worker command: `evander-cli trigger-orchestrator`
  - Trigger worker service: `evander-trigger-orchestrator.service`
  - Effect worker: `evander-effect-orchestrator`
  - Config: `config/trigger-orchestrator/config.json`
  - Operator workflow packs: `config/workflow-packs/*.json`
  - Checked-in pack files are examples or built-ins; live operator pack files may be ignored by
    Git and edited through Admin Web.

- Provider and wake workers:
  - ADO PR feedback/completion worker: `evander-ado-worker`; Azure DevOps work-item intake is
    workflow-owned
  - ADO proxy issue freshness worker: `evander-proxy-issue-sync`
  - Discord ingest: `evander-discord-ingest`
  - Discord wake: `evander-discord-wake`
  - Scheduled wake: `evander-scheduled-wake`
  - Teams ingest: `evander-cli teams-ingest` via `scripts/run_teams_ingest.sh`
  - Teams meeting ingest: `evander-teams-meeting-ingest`
  - There is no current `evander-host-heartbeat` service. Azure DevOps PR feedback polling
    lives in the ADO worker; Discord/scheduled wakeups live in their dedicated wake workers.

- Execution backends:
  - Default: `libvirt_vm`
  - Additive worker backend: `container_worker_vm`
  - Worker daemon entry point: `evander-worker-daemon`
  - Worker daemon docs: `docs/container-worker-backend.md`
  - The worker daemon is deployed by the dedicated Ansible worker playbook, not by
    `scripts/install_systemd_units.sh` on the control plane.

- Managed Git repositories:
  - Domain service: `src/evander/admin/services/managed_git_service.py`
  - Operator CLI: `evander-cli git-repos ...`
  - Registry: `runtime/managed-git-repositories.json`
  - Bare repositories: `runtime/managed-git/`
  - Ephemeral worktrees: `runtime/git-worktrees/`
  - Restricted SSH enforcement: `scripts/git_ssh_command_wrapper.sh`
  - Docs: `docs/git-remotes.md`

## Important Defaults

- Firewall default allowed host ports:
  - `22` for SSH/Git
  - `8000` for MCP
  - `11434` for OpenAI relay
  - `11666` for NuGet relay
  - `11777` for HTTP target relay
  - `11881` for the npm audit relay
  - `8081` for Nexus PyPI/npm package cache when enabled
  - `3142` for apt-cacher-ng worker provisioning when enabled
  - Source of truth: `config/firewall.env.example`

- Runtime configuration:
  - Host-global bootstrap config: `runtime/host-config.json`
  - Directory-scoped config root: `runtime/directory-config/root/...`
  - `root` is the immutable top-level directory and the live default baseline.
  - Legacy gateway YAML is migration/import input only; it is not the current live baseline.

- Guest cloud-init:
  - Template: `config/cloud-init-user-data.yaml`
  - Creates unprivileged `agent` and privileged setup user `agent-admin`.
  - Writes `/etc/evander/session.env`.
  - Writes provider-neutral guest config at `/home/agent/.agent/config.env`.
  - Adds a shell profile hook that loads the session env and sets a placeholder
    `OPENAI_API_KEY`.
  - Creates `/artifacts` and links `/home/agent/artifacts` to it.
  - Fixes `/home/agent` ownership.

- VM storage:
  - VM disks live under `/var/lib/libvirt/images/evander`.
  - New VM overlays default to `20G`; override with `DISK_SIZE_GB`.
  - Do not put VM disks under a normal user's home directory.

## Systemd Services

Install persistent control-plane services with:

```bash
./scripts/install_systemd_units.sh --preflight
sudo ./scripts/install_systemd_units.sh
```

The current install script renders and enables:

- `evander-firewall.service`
- `evander-gateway.service`
- `evander-admin-web.service`
- `evander-openai-relay.service`
- `evander-didww-sip-bridge.service`
- `evander-nuget-relay.service`
- `evander-npm-audit-facade.service`
- `evander-http-target-relay.service`
- `evander-ado-worker.service`
- `evander-proxy-issue-sync.service`
- `evander-discord-ingest.service`
- `evander-discord-wake.service`
- `evander-scheduled-wake.service`
- `evander-managed-git-refresh.service`
- `evander-teams-ingest.service`
- `evander-teams-meeting-ingest.service`
- `evander-effect-orchestrator.service`
- `evander-trigger-orchestrator.service`

Before install/start, populate the required local config and secret files for the services you use:

- `config/firewall.env`
- `config/admin-web.env`
- optional `config/gateway.env`
- optional `config/openai-relay.env`
- optional `config/nuget-relay.env`
- optional `config/npm-audit-facade.env`
- optional `config/didww-sip-bridge.env`
- `config/ado-worker/config.json`
- optional `config/proxy-issue-sync/config.json`
- optional `config/discord-ingest/config.json`
- optional `config/discord-wake/config.json`
- optional `config/scheduled-wake/config.json`
- optional `config/managed-git-refresh.env`
- `config/trigger-orchestrator/config.json`
- optional `config/teams-ingest/config.json`
- optional `config/teams-meeting-ingest/config.json`
- optional repo-root `secrets.env`

## Known Good Flows

### Bring Up The Host Stack

1. Ensure host packages exist:
   - `openssh-server`
   - `qemu-system-x86`
   - `libvirt-daemon-system`
   - `libvirt-clients`
   - `virtinst`
   - `cloud-image-utils`
   - `ovmf`
   - `nftables`

2. Install Python package into `.venv`:

   ```bash
   uv pip install -e '.[dev]'
   ```

3. Initialize runtime state:

   ```bash
   evander-cli init-state --runtime-dir runtime
   ```

4. Create network/firewall and install services:

   ```bash
   sudo ./scripts/create_libvirt_network.sh
   test -f config/firewall.env || cp config/firewall.env.example config/firewall.env
   ./scripts/install_systemd_units.sh --preflight
   sudo ./scripts/install_systemd_units.sh
   ```

5. Start the services you need, usually beginning with:

   ```bash
   sudo systemctl start evander-firewall.service
   sudo systemctl start evander-gateway.service
   sudo systemctl start evander-admin-web.service
   sudo systemctl start evander-openai-relay.service
   sudo systemctl start evander-nuget-relay.service
   sudo systemctl start evander-http-target-relay.service
   sudo systemctl start evander-trigger-orchestrator.service
   sudo systemctl start evander-effect-orchestrator.service
   ```

Use `evander-cli status --runtime-dir runtime --json` as the first broad health check.

### Create Or Recreate A VM

```bash
sudo ./scripts/destroy_agent_vm.sh vm-real
sudo ./scripts/create_base_vm.sh
sudo ./scripts/launch_agent_vm.sh vm-real real-session
./scripts/bootstrap_codex_vm.sh vm-real
```

Optional guest bootstraps include:

- `./scripts/bootstrap_copilot_vm.sh vm-real`
- `./scripts/bootstrap_dotnet_vm.sh vm-real`
- `./scripts/bootstrap_nuget_vm.sh vm-real`
- `./scripts/bootstrap_python_vm.sh vm-real /path/to/repo /home/agent/repo`
- `./scripts/bootstrap_python_packages_vm.sh vm-real <package-spec> [...]`
- `./scripts/bootstrap_mssql_vm.sh vm-real`
- `./scripts/bootstrap_playwright_vm.sh vm-real`
- `./scripts/bootstrap_frontend_deps_vm.sh vm-real /path/to/repo /path/to/repo/frontend`

If guest tools seem stale, rerun the relevant bootstrap script; they are intended to reconcile
state.

### Verify Guest Codex

Inside the VM:

1. `source ~/.profile` if using the current shell.
2. `codex --version`
3. `codex exec --skip-git-repo-check -C /tmp "Reply with exactly: relay works"`

If Codex tries to call `https://api.openai.com/v1` directly, relay-aware guest config did not land
or was overwritten.

### Verify Guest Playwright

Inside the VM:

```bash
source ~/.profile
node -e 'const { chromium } = require("playwright"); console.log(chromium.executablePath())'
```

### Issue VM And Git Flow

Issue flows create the VM first, then clone/bootstrap a repo as separate steps:

```bash
evander-cli create-issue-vm 14084 --dotnet --nuget /path/to/project.slnx --playwright
evander-cli git-repos register --directory-path root --repo-source /path/to/local/repo
evander-cli clone-repo-into-vm vm-14084 --managed-repo-id <managed-repo-id>
```

For managed Git repositories:

1. Register or refresh the upstream with `evander-cli git-repos register`.
2. Clone into the VM/container using only the returned `managed_repo_id`; the control plane
   provisions scoped guest Git SSH internally.
3. Let the guest push only to `refs/heads/agents/<session-id>/<topic>`.
4. Let the queued workflow pin and promote the trigger's exact SHA.
5. Publish that approved SHA with the managed push/PR effects and an explicit lease.

The guest clone flow writes `/home/agent/AGENTS.md`, configures `git sync-base` and
`git push-agent`, and uses root-owned logs under `/var/log/evander/agent-runs`.

### Container Worker Flow

Use this only when the directory/provider config selects `execution_backend: container_worker_vm`.

1. Build the runner image with `scripts/build_evander_runner_image.sh`.
2. Deploy the worker VM with `ansible/worker.yml`.
3. Configure root provider settings such as `container_worker_base_url`,
   `container_worker_token_env`, and guest relay URLs.
4. Verify from the control plane:

   ```bash
   evander-cli execution smoke-container-worker --json
   ```

The worker backend is intentionally bounded: no general secret transport, no general internet
egress, no autoscaling/Kubernetes scheduler, and no replacement for the existing ADO issue worker.

## Common Pitfalls Already Hit

- `src/evander/openai_relay.py` and `src/evander/nuget_relay.py` are stale paths; use
  `src/evander/relays/openai.py` and `src/evander/relays/nuget.py`.
- `evander-host-heartbeat.service` and `config/host-heartbeat.env` are stale references.
- Guest heartbeat still exists as optional guest-local timer assets, but provider bootstraps
  default `ENABLE_GUEST_HEARTBEAT=0`.
- Azure DevOps PR feedback and completion pickup are handled by `evander-ado-worker`.
- Discord inbox wakeups and scheduled resumes are handled by `evander-discord-wake` and
  `evander-scheduled-wake`.
- Putting VM disks under a normal user's home directory fails with `libvirt-qemu` permission
  errors.
- Guest shell often needs `source ~/.profile` after bootstrap for current-session `PATH` and relay
  env updates.
- Codex may prompt for auth if the placeholder `OPENAI_API_KEY` shell hook is missing.
- Git clone from VM to host requires both host `openssh-server` and firewall port `22`.
- Guest Git push should use the restricted SSH key installed by `bootstrap_git_vm.sh`, not the host
  password.
- Workflow pack edits can race with other operators. Use Admin Web/API revision and lock behavior;
  do not rewrite live pack files from stale local copies. Never write directly to live pack files.
- `frontend/admin-web/dist/index.html` is tracked and often churns during builds. If built
  assets are not in scope, avoid carrying generated hash-pointer changes.

## Files To Read First

- `README.md`
- `docs/operations.md`
- `docs/architecture.md`
- `docs/execution-backends.md`
- `docs/container-worker-backend.md`
- `docs/git-remotes.md`
- `docs/configuration-reference.md`
- `docs/live-smoke-tests.md`
- `pyproject.toml`

## Tests

Run the Python suite with:

```bash
./.venv/bin/pytest -q
```

Focused suites that often matter for control-plane changes:

- systemd/install docs: `tests/test_install_systemd_units_script.py`, `tests/test_systemd_docs.py`
- firewall defaults: `tests/test_firewall_defaults.py`
- relay behavior: `tests/test_openai_relay.py`, `tests/test_nuget_relay.py`, `tests/test_app_relay.py`
- VM/bootstrap docs: `tests/test_bootstrap_script_docs.py`, `tests/test_playwright_bootstrap_docs.py`,
  `tests/test_nuget_bootstrap_docs.py`
- workflow runtime: `tests/test_trigger_orchestrator.py`, `tests/test_effect_orchestrator.py`,
  `tests/test_workflow_packs.py`
- container worker: `tests/test_container_worker_docs.py`,
  `tests/test_container_worker_execution_backend.py`, `tests/test_ansible_container_worker_bundle.py`

Do not assume the suite is currently green; run it and inspect failures in the current worktree.

For UI, browser, Playwright, Admin Web, and broad smoke tests, use explicit command-level
timeouts and prefer focused tests first. These tests can hang on pending promises, unclosed
browser/server handles, `networkidle` waits, or missing UI conditions even after nearby assertions
have passed. If a broad UI/smoke command times out, stop it, report the exact command, timeout, and
last useful output, then keep verification scoped to the focused suites that cover the change.
Do not leave stalled test processes running while continuing work.

## Guidance For Future Changes

- Treat `src/evander/admin/composition.py::build_admin_runtime` and
  `AdminRuntimeComponents` as the supported admin runtime assembly surface for CLI, gateway,
  and Admin Web integration. Place domain logic in the narrow services under
  `src/evander/admin/services/`; keep route handlers and compatibility adapters thin and
  domain-scoped.
- Keep base VMs minimal; prefer post-launch bootstraps for optional toolchains.
- Prefer host-seeded artifacts over guest internet installs.
- If adding a new guest capability, update the bootstrap script, `README.md`,
  `docs/operations.md`, `docs/configuration-reference.md`, and focused tests.
- If adding a new host service, update `pyproject.toml`, a systemd template,
  `scripts/install_systemd_units.sh`, config examples, firewall impact, docs, and tests.
- If adding a workflow node/provider, update provider schema/implementation, registry wiring,
  schema bootstrap/catalog metadata, docs, and focused validation/conformance tests.
- All new features and bugfixes in this repo must also be recorded in `CHANGELOG.md`.
- If touching workflow packs over SSH or Admin Web, assume other users may be editing them at the
  same time. Use existing API, revision, and lock mechanisms to avoid version mismatches and
  unintended overwrites.
- The user often has unrelated work in the tree. Do not revert changes you did not make.
