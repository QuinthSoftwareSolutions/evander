# Agent Sandbox

`agent-sandbox` is a host-side control plane for running autonomous AI agents inside disposable libvirt VMs while exposing only narrow, audited capabilities through MCP.

## What This Scaffold Includes

- A Python MCP gateway with policy-gated Discord and email tool surfaces
- A host-side OpenAI relay that keeps the API key on the host and exposes an OpenAI-compatible endpoint to guests
- A host-side NuGet relay that proxies nuget.org and private Azure Artifacts feeds for guests
- A host-side shared NuGet cache that can be mounted read-only into guests for faster cold restores
- File-backed session, approval, audit, and draft storage for local development
- A small CLI for bootstrapping state and creating session manifests
- Libvirt, cloud-init, and firewall scripts for an isolated VM network
- Systemd service templates and an install script for persistent host services
- Local bare Git remote tooling for getting code changes back out of VMs
- Configuration templates and implementation notes for evolving the scaffold into a production setup

## Core Security Model

- Guests have no direct access to host secrets.
- Guests reach only the MCP gateway over a private libvirt network.
- Every tool call is checked against a session manifest.
- Irreversible actions require a separate approval record and exact payload match.
- State is append-only where possible and intentionally simple in v1.

## Quick Start

1. Install host dependencies and create a virtual environment:

   ```bash
   sudo apt install qemu-system-x86 libvirt-daemon-system libvirt-clients virtinst cloud-image-utils ovmf
   uv venv
   . .venv/bin/activate
   uv pip install -e .
   ```

2. Copy the example configuration and adjust it:

   ```bash
   cp config/gateway.example.yaml config/gateway.yaml
   ```

   For Discord bot polling/posting, switch `discord_backend` to `bot_api` and keep
   `discord_bot_token_env` set to the host environment variable that holds your bot token
   (default: `DISCORD_BOT_TOKEN`).

   For provider-backed email, define one or more `email_mailboxes` entries using:
   `mailbox_id`, `provider` (`gmail` or `graph`), `account`/`address`, auth env var names,
   and mailbox-level `folder_allowlist_defaults`.

3. Initialize runtime state:

   ```bash
   agent-sandbox-cli init-state --config config/gateway.yaml
   ```

4. Start the MCP gateway:

   ```bash
   agent-sandbox-gateway --config config/gateway.yaml
   ```

5. Create the libvirt network and firewall policy:

   ```bash
   sudo ./scripts/create_libvirt_network.sh
   sudo ./scripts/apply_firewall.sh
   ```

   The default firewall policy allows host ports `22`, `8000`, `11434`, and `11666` so VMs can use SSH for Git remotes, MCP on `8000`, the OpenAI relay on `11434`, and the NuGet relay on `11666`.

6. Start the host-side services:

   ```bash
   export OPENAI_API_KEY=...
   export DISCORD_BOT_TOKEN=...
   agent-sandbox-gateway --config config/gateway.yaml
   agent-sandbox-openai-relay --config config/gateway.yaml
   ```

7. Download the base image and launch VMs from a libvirt-accessible storage path:

   ```bash
   sudo ./scripts/create_base_vm.sh
   sudo ./scripts/launch_agent_vm.sh vm-real real-session
   ```

   By default, the launch script injects your host `~/.ssh/id_ed25519.pub` key into the guest and exposes a host-side LLM base URL of `http://192.168.250.1:11434/v1`. Override with `SSH_PUBLIC_KEY_FILE=...` or `LLM_BASE_URL=...` if needed.
   It also creates a shared host NuGet cache under `/var/lib/libvirt/images/agent-sandbox/nuget-cache/global-packages` and mounts it read-only into the guest at `/opt/agent-sandbox/nuget-cache-ro` via `virtiofs`.
   New VMs default to a `20G` virtual disk; override with `DISK_SIZE_GB=...` before launch if needed.
   New VMs also receive `/home/agent/.codex/config.toml` and a shell profile hook so Codex defaults to the host relay without the deprecated `OPENAI_BASE_URL` warning.

8. Install the Codex binary into the VM:

   ```bash
   ./scripts/bootstrap_codex_vm.sh vm-real
   ```

   This copies the vendored Linux Codex binary from the host install into the guest user's `~/bin`, rewrites `~/.codex/config.toml` to use the host relay with `model = "gpt-5.4"`, `model_reasoning_effort = "high"`, and `personality = "pragmatic"`, ensures the placeholder API-key shell hook exists, and verifies `codex --version`.

9. Optionally install the host `.NET` SDK into the VM:

   ```bash
   ./scripts/bootstrap_dotnet_vm.sh vm-real
   ```

   This copies the host `.NET` installation into the guest, appends `DOTNET_ROOT`, `DOTNET_CLI_HOME`, and `PATH` exports to the guest profile, and verifies `dotnet --info`.

10. Optionally install host-relay NuGet sources into the VM:

   ```bash
   ./scripts/bootstrap_nuget_vm.sh vm-real
   ```

   This writes `~/.nuget/NuGet/NuGet.Config` in the guest so package restore can use host relay endpoints for nuget.org and Azure Artifacts, while keeping guest-local writable caches under `~/.nuget` and using the shared host cache as a read-only fallback package folder.

11. Optionally prewarm the shared host NuGet cache from the host before launching or restoring in guests:

   ```bash
   ./scripts/prewarm_nuget_cache.sh /path/to/project-or-solution.slnx
   ./scripts/inspect_nuget_cache.sh ExampleIndustry.Elastic.Linq 2026.3.6.1
   ```

   Prewarming fills the shared host cache explicitly through real host restores, and the inspect script shows cache size or whether a specific package/version is already present.

12. Optionally install Playwright into the VM for frontend debugging:

   ```bash
   ./scripts/bootstrap_playwright_vm.sh vm-real
   ```

   This copies a host-seeded Node runtime, Playwright packages, and browser cache into the guest and verifies that Playwright can resolve Chromium offline.

## Persistent Host Services

Install the gateway and relay services:

```bash
sudo ./scripts/install_systemd_units.sh
$EDITOR config/firewall.env
$EDITOR config/openai-relay.env
$EDITOR config/nuget-relay.env
sudo systemctl start agent-sandbox-firewall.service
sudo systemctl start agent-sandbox-gateway.service
sudo systemctl start agent-sandbox-openai-relay.service
sudo systemctl start agent-sandbox-nuget-relay.service
```

The generated units run as your user where appropriate and read environment files from `config/`.

## Local Git Remotes

For the full issue-VM bring-up path in one command, use:

```bash
./scripts/setup_issue_vm.sh /path/to/issue.yaml /path/to/project-or-solution.slnx
```

That now also clones the mirrored repo into the guest user's home directory using
the host repo basename, for example `/home/agent/ExampleIndustryAPP`. It also writes a short
`/home/agent/AGENTS.md` note, copies the issue YAML into the repo root as
`AGENT_TASK.yaml`, configures a repo-local `git push-agent` alias for the safe
session-namespaced push pattern, and starts a background Codex run inside the guest by
default. The guest-side Codex log and PID file live under `/home/agent/.agent-sandbox/`.
Use `--dry-run` if you want the VM prepared without starting the agent process. The
helper still accepts a raw issue number as the first argument for the older workflow, but
YAML is the preferred input for long-running agent sessions.

For operator visibility from the host, use:

```bash
./scripts/check_issue_vm.sh 14084
./scripts/tail_issue_vm_log.sh 14084
```

The first command shows the VM state, IP, guest Codex PID, and the last log lines. The
second follows the guest Codex log live over SSH.

Use the included bare-repo helper when you want VMs to push code changes back to the host over the private network:

```bash
cp config/git-server.env.example config/git-server.env
$EDITOR config/git-server.env
./scripts/setup_issue_git_vm.sh vm-real 14084
```

If you want to mirror the default host repo into an issue-numbered bare remote first, use:

```bash
./scripts/mirror_issue_repo.sh 14084
```

`setup_issue_git_vm.sh` is the shortest path: it bootstraps the guest Git key and creates or
refreshes the issue-numbered host mirror in one command. The lower-level
`bootstrap_git_vm.sh` helper installs a dedicated guest SSH key and a restricted host
`authorized_keys` entry so guests can push Git branches without needing your host password or
general shell access.

See [git-remotes.md](/home/jonathan/agent-sandbox/docs/git-remotes.md) for the review flow and branch naming pattern.
The host-side safe promotion flow is:

```bash
./scripts/promote_agent_branch.sh /path/to/trusted-clone sample-repo agents/<session-id>/work
./scripts/open_agent_pr.sh --create-pr --base main /path/to/trusted-clone review/<session-id>/work
```

That keeps the VM-facing bare remote as a quarantine intake only; reviewed changes reach `origin`
only from a normal trusted clone on the host.
See [live-smoke-tests.md](/home/jonathan/agent-sandbox/docs/live-smoke-tests.md) for the live Discord and provider-backed email verification workflow.

## Discord Adapter Configuration

- Required host-side env var for Discord bot API mode: `DISCORD_BOT_TOKEN` (or the name in
  `discord_bot_token_env`).
- Session Discord channel allowlists are set when creating a session:

  ```bash
  agent-sandbox-cli create-session \
    real-session \
    --config config/gateway.yaml \
    --discord-channel 123456789012345678 \
    --discord-channel 234567890123456789
  ```

- Operator user allowlist is configured globally on the host in `config/gateway.yaml` with
  `discord_operator_user_allowlist`.
- In `bot_api` mode, polling targets come **only** from channel IDs already present in
  `runtime/discord_commands.json` (the Discord command store). The adapter does not discover
  channels from guild membership, does not enumerate channel history beyond configured per-channel
  fetches, and does not poll arbitrary channels unless they are explicitly seeded in that store.
- Source filtering for `discord_get_commands` is strict:
  - Message source metadata must be `source == "discord_bot_api"`.
  - A command is accepted when **either** its `channel_id` is in the session's
    `discord_channel_allowlist` **or** its `author_id` is in global
    `discord_operator_user_allowlist`.
  - Commands that satisfy neither condition are denied and audited as denied tool attempts.
  - For `discord_post_status`, only session allowlisted `channel_id` values are permitted;
    operator user allowlists do not bypass post destination checks.
- There is currently no built-in Discord retry/backoff loop in the `bot_api` adapter. HTTP
  failures (timeouts, 4xx/5xx, transport errors) surface back to the MCP caller. Policy denials
  are written to `audit_log_path` (default runtime path `runtime/audit.jsonl`).
- Operator least-privilege checklist:
  1. Keep the Discord bot token only on the host (environment or host env files), never in guest
     cloud-init, manifests, or guest shell profiles.
  2. Create per-session manifests with the minimum required `--discord-channel` entries.
  3. Keep `discord_operator_user_allowlist` short and explicit (human operators only, no broad
     role-style expansion).
  4. Seed only intended control channels into the Discord command store; remove stale channels.
  5. Periodically review denied Discord events in `runtime/audit.jsonl` to catch mis-scoped
     channels/users.
- Guests never need direct Discord credentials or internet access; Discord API access happens only
  in the host-side gateway process.

## Relay Environment Configuration

- `OPENAI_API_KEY` is required on the host for `agent-sandbox-openai-relay`.
- `AZURE_ARTIFACTS_PAT` is required on the host only when the NuGet relay must reach private
  Azure Artifacts feeds.
- Keep both values in host-only shell environment or systemd env files such as:
  - `config/openai-relay.env`
  - `config/nuget-relay.env`
- Do not place either value in guest images, cloud-init, session manifests, or guest shell
  profiles.

### NuGet Relay Notes

- The NuGet relay proxies both `nuget.org` and optional private Azure Artifacts feeds.
- Guests should use the relay bootstrap script instead of private feed credentials:

  ```bash
  ./scripts/bootstrap_nuget_vm.sh vm-real
  ```

- That writes guest-side `NuGet.Config` entries pointing at the host relay on
  `http://192.168.250.1:11666`.
- The guest NuGet config also uses:
  - a guest-local writable `global-packages` folder at `/home/agent/.nuget/packages-local`
  - a guest-local writable HTTP cache at `/home/agent/.nuget/http-cache`
  - a read-only fallback package folder at `/opt/agent-sandbox/nuget-cache-ro`
- Prewarm the shared host cache explicitly with `./scripts/prewarm_nuget_cache.sh ...` when you
  want faster first restores in disposable VMs.

## Email Provider Configuration (Host-Side Secrets Only)

- `email_mailboxes` supports multiple mailbox entries with explicit provider and auth env-var wiring.
- Keep mailbox OAuth tokens in host-only environment variables (for example:
  `GMAIL_OPS_ACCESS_TOKEN`, `GRAPH_EXEC_ACCESS_TOKEN`, `GRAPH_FINANCE_ACCESS_TOKEN`).
- Session manifests should allow explicit mailbox/folder scope, but **never include provider
  access tokens**.
- Token values are never injected into guests, cloud-init payloads, or session manifests.
- Email actions are **read + draft only by default**; sending remains disabled unless
  `email_send_enabled: true` and the approval flow is used.

Example snippet:

```yaml
email_folder_allowlist_by_mailbox:
  ops-gmail: ["inbox", "drafts"]
  exec-graph: ["inbox", "drafts"]
  finance-graph: ["inbox", "drafts"]
email_mailboxes:
  - mailbox_id: "ops-gmail"
    provider: "gmail"
    account: "me"
    auth_env_var_names:
      access_token: "GMAIL_OPS_ACCESS_TOKEN"
    folder_allowlist_defaults: ["inbox", "drafts"]
  - mailbox_id: "exec-graph"
    provider: "graph"
    address: "leader@example.com"
    auth_env_var_names:
      access_token: "GRAPH_EXEC_ACCESS_TOKEN"
    folder_allowlist_defaults: ["inbox", "drafts"]
  - mailbox_id: "finance-graph"
    provider: "graph"
    address: "finance@example.com"
    auth_env_var_names:
      access_token: "GRAPH_FINANCE_ACCESS_TOKEN"
    folder_allowlist_defaults: ["inbox", "drafts"]
```

Session allowlist example with all mailboxes:

```bash
cat <<'YAML' >> config/gateway.yaml
email_mailbox_allowlist:
  - "ops-gmail"
  - "exec-graph"
  - "finance-graph"
email_folder_allowlist_by_mailbox:
  ops-gmail: ["inbox", "drafts"]
  exec-graph: ["inbox", "drafts"]
  finance-graph: ["inbox", "drafts"]
YAML

agent-sandbox-cli create-session real-session --config config/gateway.yaml
```

## Project Layout

- `src/agent_sandbox/`: gateway, relay, policy, storage, and CLI code
- `scripts/`: libvirt and firewall automation
- `config/`: sample configuration and cloud-init template
- `systemd/`: service templates for the host gateway, relay, and firewall policy
- `docs/`: operating notes and next steps

## Current Limitations

- Email supports provider-backed Gmail and Microsoft Graph adapters; the local JSON store is an optional fallback for development/testing only.
- Discord has both `stub` and `bot_api` backends; production deployments should enforce strict
  channel and operator allowlists.
- The gateway is ready for integration adapters, not production credentials by default.
- Network/firewall scripts assume Ubuntu 24.04 and nftables-backed libvirt hosts.
- VM disk and seed artifacts default to `/var/lib/libvirt/images/agent-sandbox`, so VM lifecycle scripts should be run with `sudo` unless you deliberately provision an alternate libvirt-accessible storage path.
- Guests can reach only the specific host TCP ports you allow in `ALLOWED_TCP_PORTS`; the defaults are `22` for host SSH/Git, `8000` for MCP, `11434` for the host-side OpenAI relay or another OpenAI-compatible LLM endpoint, and `11666` for the host-side NuGet relay.
