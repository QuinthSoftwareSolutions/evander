# Agent Sandbox Repo Notes

This file is for future Codex instances working in `/home/jonathan/agent-sandbox`.

## Purpose

This repo implements a host-side control plane for isolated libvirt VMs that can:

- reach a host MCP gateway
- reach a host OpenAI relay
- optionally run Codex inside the guest
- optionally run Playwright inside the guest
- optionally push Git branches back to a host-local bare remote

The security model is:

- no general guest internet
- host keeps real secrets
- guests get only specific host ports
- code transport uses Git, not MCP

## Current Working Pieces

- MCP gateway:
  - Entry point: `agent-sandbox-gateway`
  - Code: `src/agent_sandbox/gateway.py`
  - Default host port: `8000`

- OpenAI relay:
  - Entry point: `agent-sandbox-openai-relay`
  - Code: `src/agent_sandbox/openai_relay.py`
  - Default host port: `11434`
  - Important: streaming path had multiple bugs; current version keeps the upstream client alive for the full stream and proxies raw bytes for SSE.

- NuGet relay:
  - Entry point: `agent-sandbox-nuget-relay`
  - Code: `src/agent_sandbox/nuget_relay.py`
  - Default host port: `11666`

- Host workers:
  - Azure DevOps backlog worker: `agent-sandbox-ado-worker`
  - Host heartbeat worker: `agent-sandbox-cli host-heartbeat` / `agent-sandbox-host-heartbeat`

- VM lifecycle:
  - `scripts/create_base_vm.sh`
  - `scripts/launch_agent_vm.sh`
  - `scripts/destroy_agent_vm.sh`
  - `scripts/setup_vm.sh`
  - Default VM disk size is `20G`
  - VM disks live under `/var/lib/libvirt/images/agent-sandbox`, not under `/home/jonathan`
  - issue flows now create the VM first, then clone/bootstrap the repo as separate steps
  - repo clone still writes `/home/agent/AGENTS.md` and configures `git push-agent` in the cloned repo

- Guest Codex bootstrap:
  - `scripts/bootstrap_codex_vm.sh`
  - Installs vendored Codex binary into `~/bin/codex`
  - Rewrites `~/.codex/config.toml`
  - Ensures placeholder `OPENAI_API_KEY`
  - Repairs `/home/agent` ownership first

- Guest Playwright bootstrap:
  - `scripts/bootstrap_playwright_vm.sh`
  - Copies host Node runtime, `playwright`, `playwright-core`, and browser cache into guest
  - Appends required environment to guest `~/.profile`

- Host services:
  - `scripts/install_systemd_units.sh`
  - Templates in `systemd/`
  - Systemd-managed units currently include:
    - firewall
    - gateway
    - OpenAI relay
    - NuGet relay
    - ADO worker
    - host-heartbeat worker

- Host-local Git remotes:
  - `scripts/create_git_remote.sh`
  - `scripts/bootstrap_git_vm.sh`
  - `scripts/setup_issue_git_vm.sh`
  - `scripts/promote_agent_branch.sh`
  - `scripts/open_agent_pr.sh`
  - `scripts/git_ssh_command_wrapper.sh`
  - Docs: `docs/git-remotes.md`
- Live smoke-test runbook:
  - `docs/live-smoke-tests.md`
  - Covers host-only then VM end-to-end Discord + read-only email validation

## Important Defaults

- Firewall default allowed host ports:
  - `22` for SSH/Git
  - `8000` for MCP
  - `11434` for OpenAI relay
  - `11666` for NuGet relay
  - Source of truth: `config/firewall.env.example`

- Guest cloud-init:
  - `config/cloud-init-user-data.yaml`
  - Injects SSH public key
  - Installs `openssh-server`
  - Writes `/etc/agent-sandbox/session.env`
  - Writes provider-neutral guest baseline config at `/home/agent/.agent/config.env`
  - Appends a shell profile hook that loads `/etc/agent-sandbox/session.env`
  - Fixes `/home/agent` ownership

## Known Good Flows

### Bring up host stack

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
   - `uv pip install -e '.[dev]'`

3. Install/start host services:
   - `sudo ./scripts/install_systemd_units.sh`
   - fill in `config/openai-relay.env`
   - fill in `config/nuget-relay.env`, `config/ado-worker.env`, and `config/host-heartbeat.env` as needed
   - `sudo systemctl start agent-sandbox-firewall.service`
   - `sudo systemctl start agent-sandbox-gateway.service`
   - `sudo systemctl start agent-sandbox-openai-relay.service`
   - optional/currently supported additional units:
     - `sudo systemctl start agent-sandbox-nuget-relay.service`
     - `sudo systemctl start agent-sandbox-ado-worker.service`
     - `sudo systemctl start agent-sandbox-host-heartbeat.service`

### Create/recreate VM

1. `sudo ./scripts/destroy_agent_vm.sh vm-real`
2. `sudo ./scripts/launch_agent_vm.sh vm-real real-session`
3. `./scripts/bootstrap_codex_vm.sh vm-real`
4. optional: `./scripts/bootstrap_playwright_vm.sh vm-real`

If guest tools seem stale, rerun the bootstrap scripts; they are intended to reconcile state.

### Verify guest Codex

Inside VM:

1. `source ~/.profile` if using the current shell
2. `codex --version`
3. `codex exec --skip-git-repo-check -C /tmp -m gpt-5.2-codex "Reply with exactly: relay works"`

If Codex tries to call `https://api.openai.com/v1` directly, guest config/bootstrap did not land correctly.

### Verify guest Playwright

Inside VM:

1. `source ~/.profile`
2. `node -e 'const { chromium } = require("playwright"); console.log(chromium.executablePath())'`

## Common Pitfalls Already Hit

- Putting VM disks under `/home/jonathan` fails with `libvirt-qemu` permission errors.
- Guest VM root disk was initially too small; launch script now resizes overlays to `20G`.
- Guest `/home/agent` ownership was wrong; cloud-init and Codex bootstrap now repair it.
- Codex wrapper copied into guest without its vendored binary was not enough; current bootstrap copies the working vendored binary.
- Guest shell often needs `source ~/.profile` after bootstrap for `PATH` updates in the current session.
- Codex may prompt for auth if guest placeholder `OPENAI_API_KEY` is missing.
- Codex using `https://api.openai.com/v1` means relay-aware guest config is missing or overwritten.
- OpenAI relay SSE path had real bugs:
  - upstream client lifetime
  - content encoding handling
  - raw SSE byte forwarding
  Current code is the known-good version.
- Git clone from VM to host requires both:
  - host `openssh-server` running
  - firewall allowing port `22`
- Guest Git push no longer needs the host password when `bootstrap_git_vm.sh` has installed the
  restricted SSH key path correctly.
- Safe Git promotion flow is:
  - VM pushes to `refs/heads/agents/<session-id>/<topic>` in the bare repo
  - host runs `promote_agent_branch.sh` into a trusted clone
  - host runs `open_agent_pr.sh` only after review

## Files To Read First

- `README.md`
- `docs/operations.md`
- `docs/architecture.md`
- `docs/git-remotes.md`
- `docs/live-smoke-tests.md`

## Tests

Run:

```bash
cd /home/jonathan/agent-sandbox
./.venv/bin/pytest -q
```

The suite includes docs/regression checks for:

- relay behavior
- bootstrap scripts
- firewall defaults
- doc references
- live smoke-test workflow

Do not assume the suite is currently green; run it and inspect failures in the current worktree.

## Guidance For Future Changes

- Keep the base VM minimal; prefer post-launch bootstraps for optional toolchains.
- Prefer host-seeded artifacts over guest internet installs.
- If adding a new guest capability, update:
  - bootstrap script
  - `README.md`
  - `docs/operations.md`
  - tests
- If adding a new host service, consider:
  - systemd template
  - env example
  - firewall impact
