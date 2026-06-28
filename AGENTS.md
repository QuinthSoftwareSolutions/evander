# Agent Sandbox Repo Notes

This file is for future Codex instances working in the `agent-sandbox` repo.

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
  - Entry point: `agent-sandbox-gateway`
  - Code: `src/agent_sandbox/gateway.py`
  - Default guest-reachable port: `8000`
  - Gateway liveness is checked on `/mcp`; a plain GET can return `406 Not Acceptable` and still
    prove the MCP endpoint is reachable.

- OpenAI relay:
  - Entry point: `agent-sandbox-openai-relay`
  - Code: `src/agent_sandbox/relays/openai.py`
  - Default guest-reachable port: `11434`
  - The streaming path intentionally keeps the upstream client alive for the full stream and
    forwards raw SSE bytes.

- NuGet relay:
  - Entry point: `agent-sandbox-nuget-relay`
  - Code: `src/agent_sandbox/relays/nuget.py`
  - Default guest-reachable port: `11666`
  - Health endpoint: `/healthz`

- HTTP target relay:
  - Entry point: `agent-sandbox-http-target-relay`
  - Code: `src/agent_sandbox/relays/http_target.py`
  - Default guest-reachable port: `11777`
  - Session-scoped relay for allowlisted external HTTP targets. It forwards guest-managed auth
    headers/cookies; host secrets are not injected into the guest request.

- Admin Web:
  - Entry point: `agent-sandbox-admin-web`
  - Code: `src/agent_sandbox/admin/web/app.py`
  - Default port from `config/admin-web.env.example`: `8100`
  - This is the supported operator GUI for dashboard, VMs/issues, sessions, approvals, tool-call
    audit, workflow editing/runs/reports, configuration, workers, skills, chat, and users.

- Workflow runtime:
  - Trigger worker command: `evander-cli trigger-orchestrator`
  - Trigger worker service: `agent-sandbox-trigger-orchestrator.service`
  - Effect worker: `agent-sandbox-effect-orchestrator`
  - Config: `config/trigger-orchestrator/config.json`
  - Operator workflow packs: `config/workflow-packs/*.json`
  - Checked-in pack files are examples or built-ins; live operator pack files may be ignored by
    Git and edited through Admin Web.

- Provider and wake workers:
  - ADO backlog/PR feedback worker: `agent-sandbox-ado-worker`
  - ADO proxy issue freshness worker: `agent-sandbox-proxy-issue-sync`
  - Discord ingest: `agent-sandbox-discord-ingest`
  - Discord wake: `agent-sandbox-discord-wake`
  - Scheduled wake: `agent-sandbox-scheduled-wake`
  - Teams ingest: `evander-cli teams-ingest` via `scripts/run_teams_ingest.sh`
  - Teams meeting ingest: `agent-sandbox-teams-meeting-ingest`
  - There is no current `agent-sandbox-host-heartbeat` service. Azure DevOps PR feedback polling
    lives in the ADO worker; Discord/scheduled wakeups live in their dedicated wake workers.

- Execution backends:
  - Default: `libvirt_vm`
  - Additive worker backend: `container_worker_vm`
  - Worker daemon entry point: `agent-sandbox-worker-daemon`
  - Worker daemon docs: `docs/container-worker-backend.md`
  - The worker daemon is deployed by the dedicated Ansible worker playbook, not by
    `scripts/install_systemd_units.sh` on the control plane.

- Host-local Git remotes:
  - `scripts/create_git_remote.sh`
  - `scripts/bootstrap_git_vm.sh`
  - `scripts/setup_issue_git_vm.sh`
  - `scripts/mirror_issue_repo.sh`
  - `scripts/promote_agent_branch.sh`
  - `scripts/open_agent_pr.sh`
  - `scripts/git_ssh_command_wrapper.sh`
  - Docs: `docs/git-remotes.md`

## Important Defaults

- Firewall default allowed host ports:
  - `22` for SSH/Git
  - `8000` for MCP
  - `11434` for OpenAI relay
  - `11666` for NuGet relay
  - `11777` for HTTP target relay
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
  - Writes `/etc/agent-sandbox/session.env`.
  - Writes provider-neutral guest config at `/home/agent/.agent/config.env`.
  - Adds a shell profile hook that loads the session env and sets a placeholder
    `OPENAI_API_KEY`.
  - Creates `/artifacts` and links `/home/agent/artifacts` to it.
  - Fixes `/home/agent` ownership.

- VM storage:
  - VM disks live under `/var/lib/libvirt/images/agent-sandbox`.
  - New VM overlays default to `20G`; override with `DISK_SIZE_GB`.
  - Do not put VM disks under a normal user's home directory.

## Systemd Services

Install persistent control-plane services with:

```bash
./scripts/install_systemd_units.sh --preflight
sudo ./scripts/install_systemd_units.sh
```

The current install script renders and enables:

- `agent-sandbox-firewall.service`
- `agent-sandbox-gateway.service`
- `agent-sandbox-admin-web.service`
- `agent-sandbox-openai-relay.service`
- `agent-sandbox-nuget-relay.service`
- `agent-sandbox-http-target-relay.service`
- `agent-sandbox-ado-worker.service`
- `agent-sandbox-proxy-issue-sync.service`
- `agent-sandbox-discord-ingest.service`
- `agent-sandbox-discord-wake.service`
- `agent-sandbox-scheduled-wake.service`
- `agent-sandbox-teams-ingest.service`
- `agent-sandbox-teams-meeting-ingest.service`
- `agent-sandbox-effect-orchestrator.service`
- `agent-sandbox-trigger-orchestrator.service`

Before install/start, populate the required local config and secret files for the services you use:

- `config/firewall.env`
- `config/admin-web.env`
- optional `config/gateway.env`
- optional `config/openai-relay.env`
- optional `config/nuget-relay.env`
- `config/ado-worker/config.json`
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
   sudo systemctl start agent-sandbox-firewall.service
   sudo systemctl start agent-sandbox-gateway.service
   sudo systemctl start agent-sandbox-admin-web.service
   sudo systemctl start agent-sandbox-openai-relay.service
   sudo systemctl start agent-sandbox-nuget-relay.service
   sudo systemctl start agent-sandbox-http-target-relay.service
   sudo systemctl start agent-sandbox-trigger-orchestrator.service
   sudo systemctl start agent-sandbox-effect-orchestrator.service
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
evander-cli clone-repo-into-vm vm-14084 --repo-source /path/to/local/repo
```

For host-local Git remotes:

1. Create or refresh the bare mirror with `scripts/create_git_remote.sh`,
   `scripts/mirror_issue_repo.sh`, or `scripts/setup_issue_git_vm.sh`.
2. Bootstrap guest Git SSH with `scripts/bootstrap_git_vm.sh`.
3. Let the guest push only to `refs/heads/agents/<session-id>/<topic>`.
4. Promote from the host with `scripts/promote_agent_branch.sh`.
5. Open or update the PR from a trusted host clone with `scripts/open_agent_pr.sh`.

The guest clone flow writes `/home/agent/AGENTS.md`, configures `git sync-base` and
`git push-agent`, and uses root-owned logs under `/var/log/agent-sandbox/agent-runs`.

### Container Worker Flow

Use this only when the directory/provider config selects `execution_backend: container_worker_vm`.

1. Build the runner image with `scripts/build_agent_sandbox_runner_image.sh`.
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

- `src/agent_sandbox/openai_relay.py` and `src/agent_sandbox/nuget_relay.py` are stale paths; use
  `src/agent_sandbox/relays/openai.py` and `src/agent_sandbox/relays/nuget.py`.
- `agent-sandbox-host-heartbeat.service` and `config/host-heartbeat.env` are stale references.
- Guest heartbeat still exists as optional guest-local timer assets, but provider bootstraps
  default `ENABLE_GUEST_HEARTBEAT=0`.
- Azure DevOps PR feedback and completion pickup are handled by `agent-sandbox-ado-worker`.
- Discord inbox wakeups and scheduled resumes are handled by `agent-sandbox-discord-wake` and
  `agent-sandbox-scheduled-wake`.
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
- `frontend/workflow-editor/dist/index.html` is tracked and often churns during builds. If built
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

For UI, browser, Playwright, workflow-editor, and broad smoke tests, use explicit command-level
timeouts and prefer focused tests first. These tests can hang on pending promises, unclosed
browser/server handles, `networkidle` waits, or missing UI conditions even after nearby assertions
have passed. If a broad UI/smoke command times out, stop it, report the exact command, timeout, and
last useful output, then keep verification scoped to the focused suites that cover the change.
Do not leave stalled test processes running while continuing work.

## Guidance For Future Changes

- Treat `src/agent_sandbox/admin/service.py::AdminService` as the legacy/stable facade for
  CLI, gateway, and Admin Web integration. Prefer placing new admin domain logic in narrower
  services under `src/agent_sandbox/admin/services/` with clear ownership boundaries for
  operations, issue/git, VM/agent, workflow config, skills, reporting, and PR preflight.
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
