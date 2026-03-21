# Operations

## Host Packages

Install these packages on the Ubuntu host before using the scripts:

- `openssh-server`
- `qemu-system-x86`
- `libvirt-daemon-system`
- `libvirt-clients`
- `virtinst`
- `cloud-image-utils`
- `ovmf`
- `nftables`

## Bootstrap Flow

1. Install Python dependencies with `uv`.
2. Copy `config/gateway.example.yaml` to `config/gateway.yaml`.
3. Run `agent-sandbox-cli init-state`.
4. Create the libvirt network with `./scripts/create_libvirt_network.sh`.
5. Apply guest forwarding policy with `./scripts/apply_firewall.sh`.
6. Download the Ubuntu cloud image with `sudo ./scripts/create_base_vm.sh`.
7. Create a session manifest with `agent-sandbox-cli create-session`.
8. Launch a VM with `sudo ./scripts/launch_agent_vm.sh`.
9. Install the Codex binary into the guest with `./scripts/bootstrap_codex_vm.sh <vm-name>`.
10. Optionally install the host `.NET` SDK into the guest with `./scripts/bootstrap_dotnet_vm.sh <vm-name>`.
11. Optionally prewarm the shared host NuGet cache with `./scripts/prewarm_nuget_cache.sh <path> [more paths...]`.
12. Optionally install NuGet relay sources into the guest with `./scripts/bootstrap_nuget_vm.sh <vm-name>`.
13. Optionally install Playwright into the guest with `./scripts/bootstrap_playwright_vm.sh <vm-name>` for rendered-DOM debugging.
14. Optionally create a local bare Git remote with `./scripts/create_git_remote.sh <repo-name>` or mirror the default host repo with `./scripts/mirror_issue_repo.sh <issue-number>` if the VM should push code back to the host.
15. Use [live-smoke-tests.md](/home/jonathan/agent-sandbox/docs/live-smoke-tests.md) when you want to verify live Discord and provider-backed email behavior against real credentials.

- The launch script resizes each overlay disk to `20G` by default.
- The launch script also exposes a read-only shared NuGet cache via `virtiofs` at `/opt/agent-sandbox/nuget-cache-ro` inside the guest.
- Override per VM with `DISK_SIZE_GB=40 sudo ./scripts/launch_agent_vm.sh ...`.

## SSH Access

- New VMs install `openssh-server`.
- The launch script injects the caller's `~/.ssh/id_ed25519.pub` by default, falling back to `~/.ssh/id_rsa.pub`.
- Override the key with `SSH_PUBLIC_KEY_FILE=/path/to/key.pub`.
- After boot, find the guest IP with `virsh domifaddr <vm-name>` and connect with `ssh agent@<vm-ip>`.

## VM Codex Configuration

- Cloud-init writes `/home/agent/.codex/config.toml` for the `agent` user.
- That config points Codex at the host relay via `openai_base_url`.
- Cloud-init also appends a shell profile hook that loads `/etc/agent-sandbox/session.env` and exports `OPENAI_API_KEY=relay-placeholder` if no key is already set.
- `./scripts/bootstrap_codex_vm.sh <vm-name>` copies the vendored Linux Codex binary from the host into `~/bin/codex` for the guest user and verifies it.
- The bootstrap script also overwrites `~/.codex/config.toml` with the relay-aware configuration, `model = "gpt-5.4"`, `model_reasoning_effort = "high"`, and `personality = "pragmatic"`, then appends the placeholder-key shell hook plus `~/bin` PATH entry to both `~/.profile` and `~/.bashrc`, so it corrects preexisting Codex state in the VM.
- The bootstrap script repairs `/home/agent` ownership first, so it also works against VMs created before the home directory ownership fix.
- New VMs pick this up automatically; existing VMs need to be recreated.

## VM Playwright Bootstrap

- `./scripts/bootstrap_playwright_vm.sh <vm-name>` copies a host-seeded Node runtime into `~/toolchains/node-v24.14.0`.
- It copies `playwright` and `playwright-core` into `~/playwright-runtime/node_modules`.
- It copies the host Playwright browser cache into `~/.cache/ms-playwright`.
- It appends `PATH`, `PLAYWRIGHT_BROWSERS_PATH`, and `NODE_PATH` exports to `~/.profile`.
- It verifies the guest can resolve the Chromium executable without guest internet access.

## VM .NET Bootstrap

- `./scripts/bootstrap_dotnet_vm.sh <vm-name>` copies the host `.NET` installation into `~/toolchains/dotnet`.
- It appends `DOTNET_ROOT`, `DOTNET_CLI_HOME`, and `PATH` exports to `~/.profile`.
- It verifies the guest can run `dotnet --info` without guest internet access.

## VM NuGet Bootstrap

- `./scripts/bootstrap_nuget_vm.sh <vm-name>` writes `~/.nuget/NuGet/NuGet.Config`.
- The generated config points to the host NuGet relay for both nuget.org and Azure Artifacts.
- The generated config uses:
  - guest-local writable `globalPackagesFolder` at `/home/agent/.nuget/packages-local`
  - guest-local writable `httpCacheFolder` at `/home/agent/.nuget/http-cache`
  - read-only `fallbackPackageFolders` at `/opt/agent-sandbox/nuget-cache-ro`
- It runs `dotnet nuget list source` when `dotnet` is available in the guest.

## Host Shared NuGet Cache

- Default host cache path: `/var/lib/libvirt/images/agent-sandbox/nuget-cache/global-packages`
- Guests see that cache read-only at `/opt/agent-sandbox/nuget-cache-ro` via `virtiofs`.
- Guests still use guest-local writable caches for cache misses and new downloads.
- Prewarm the shared cache explicitly from the host with:

  ```bash
  ./scripts/prewarm_nuget_cache.sh /path/to/project-or-solution.slnx
  ```

- Inspect the cache with:

  ```bash
  ./scripts/inspect_nuget_cache.sh
  ./scripts/inspect_nuget_cache.sh ExampleIndustry.Elastic.Linq 2026.3.6.1
  ```

## Host-Side LLM Access

- The firewall allows only the host TCP ports listed in `ALLOWED_TCP_PORTS`.
- Defaults: `22` for host SSH/Git, `8000` for the MCP gateway, `11434` for the host-side OpenAI relay or a local OpenAI-compatible LLM endpoint such as Ollama, and `11666` for the host-side NuGet relay.
- Override when applying firewall rules, for example:

  ```bash
  sudo ALLOWED_TCP_PORTS="22 8000 11434 11666 4000" ./scripts/apply_firewall.sh
  ```

- The guest receives `AGENT_SANDBOX_LLM_BASE_URL` and `OPENAI_BASE_URL` in `/etc/agent-sandbox/session.env`.
- The scaffold includes `agent-sandbox-openai-relay`, a host-side OpenAI-compatible proxy that reads the real API key only from the host environment and forwards guest requests to `https://api.openai.com/v1`.
- For OpenAI/Codex-backed workflows while keeping guests off the public internet, run the relay on the host and leave `LLM_BASE_URL` at its default `http://192.168.250.1:11434/v1`.

## Host-Side Discord Access

- Set `discord_backend: "bot_api"` in `config/gateway.yaml` to enable the provider-backed Discord adapter.
- Set `discord_bot_token_env` in `config/gateway.yaml` (default `DISCORD_BOT_TOKEN`).
- Export the matching host environment variable before launching `agent-sandbox-gateway`:

  ```bash
  export DISCORD_BOT_TOKEN=...
  ```

- Use per-session channel allowlists when creating session manifests:

  ```bash
  agent-sandbox-cli create-session \
    real-session \
    --config config/gateway.yaml \
    --discord-channel 123456789012345678
  ```

- Use `discord_operator_user_allowlist` in `config/gateway.yaml` for trusted operator user IDs that
  can issue commands outside per-session channel allowlists.
- `bot_api` polling scope is explicit and narrow:
  - The gateway polls only channel IDs that already exist in `discord_command_store_path`
    (default `runtime/discord_commands.json`).
  - It does **not** auto-discover channels from guild membership.
  - It does **not** treat prior Discord history as an open-ended source universe.
  - Polling additional channels requires explicitly seeding those channel IDs into the command
    store.
- Seed a dedicated test/control channel and get an initial cursor with:

  ```bash
  ./.venv/bin/agent-sandbox-cli seed-discord-channel 123456789012345678 --config config/gateway.yaml
  ```

  Use the printed cursor as the first `since_cursor` for `discord_get_commands` so the seed entry
  stays out of the first live poll or smoke test result set.
- Allowlist precedence for `discord_get_commands`:
  1. Command metadata must identify trusted adapter source (`source = discord_bot_api`).
  2. If the command `channel_id` is in `session.discord_channel_allowlist`, allow it.
  3. Otherwise, if `author_id` is in global `discord_operator_user_allowlist`, allow it.
  4. Otherwise deny and audit as denied.
- Posting precedence for `discord_post_status`:
  - Destination `channel_id` must be in `session.discord_channel_allowlist`.
  - `discord_operator_user_allowlist` does not override post destination checks.
- Retry/backoff and failure handling:
  - The Discord `bot_api` adapter currently has no internal retry or exponential backoff loop.
  - Discord HTTP errors and timeouts bubble up to the tool caller.
  - Policy denials are audited to `audit_log_path` (default `runtime/audit.jsonl`), including
    per-command denied source metadata when filtering `discord_get_commands`.
  - Successful command polls are also audited with `count` and `denied_count` summary fields.
- Operator checklist (token + least privilege):
  1. Keep the bot token only on host-side env variables/env files (`discord_bot_token_env`); never
     place it in guest disks, cloud-init payloads, or session manifests.
  2. Use per-session `--discord-channel` allowlists and avoid wildcard/shared catch-all channels.
  3. Keep `discord_operator_user_allowlist` minimal and human-specific.
  4. Seed only intended control channels in `discord_command_store_path`.
  5. Review denied Discord entries in `runtime/audit.jsonl` during operations.
- Guests should not have direct Discord internet access; only the host-side gateway talks to
  Discord APIs and enforces source/channel/operator policy checks.

## Host-Side Email Access

- Configure one or more `email_mailboxes` entries in `config/gateway.yaml` with:
  - `mailbox_id`
  - `provider` (`gmail` or `graph`)
  - `account` (Gmail `me`) or `address` (Graph mailbox address)
  - `auth_env_var_names` (env vars that hold OAuth access tokens)
  - `folder_allowlist_defaults` (typically `inbox` and `drafts`)
- Define `email_folder_allowlist_by_mailbox` for each mailbox so session policy is explicit.
- Keep OAuth access tokens host-only (for example:
  `GMAIL_OPS_ACCESS_TOKEN`, `GRAPH_EXEC_ACCESS_TOKEN`, `GRAPH_FINANCE_ACCESS_TOKEN`) and never
  place those token values in guest images, cloud-init, or session manifests.
- Email is **read + draft only by default**. Keep `email_send_enabled: false` unless you are ready
  to enforce explicit approval checks for send.

### Gmail OAuth/token setup

1. Create OAuth credentials in Google Cloud for Gmail API access.
2. Obtain/refresh an access token for the mailbox scopes you need.
3. Export the token only in host-side environment context (for example a systemd env file):

```bash
export GMAIL_OPS_ACCESS_TOKEN=...
```

4. Reference that env var in mailbox config:

```yaml
email_mailboxes:
  - mailbox_id: "ops-gmail"
    provider: "gmail"
    account: "me"
    auth_env_var_names:
      access_token: "GMAIL_OPS_ACCESS_TOKEN"
    folder_allowlist_defaults: ["inbox", "drafts"]
```

### Microsoft Graph OAuth/token setup

1. Register an application in Microsoft Entra ID and grant Microsoft Graph Mail permissions.
2. Obtain an OAuth access token for the target mailbox.
3. Export the token only on the host:

```bash
export GRAPH_EXEC_ACCESS_TOKEN=...
export GRAPH_FINANCE_ACCESS_TOKEN=...
```

4. Reference that env var in mailbox config:

```yaml
email_folder_allowlist_by_mailbox:
  ops-gmail: ["inbox", "drafts"]
  exec-graph: ["inbox", "drafts"]
  finance-graph: ["inbox", "drafts"]
email_mailboxes:
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

### Multi-mailbox session allowlist example

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

## Host-Side NuGet Access

- The scaffold includes `agent-sandbox-nuget-relay`, a host-side relay that proxies `nuget.org` and private Azure Artifacts feeds.
- The relay reads Azure feed credentials from host environment (`AZURE_ARTIFACTS_PAT`) and does not require guest-side private feed credentials.
- Configure the relay environment in `config/nuget-relay.env`.
- After installing units, run `./scripts/bootstrap_nuget_vm.sh <vm-name>` so guest restores target relay URLs on `http://192.168.250.1:11666`.

## Systemd Services

Install persistent host services with:

```bash
sudo ./scripts/install_systemd_units.sh
```

This creates:

- `agent-sandbox-firewall.service`
- `agent-sandbox-gateway.service`
- `agent-sandbox-openai-relay.service`
- `agent-sandbox-nuget-relay.service`

Then:

1. Copy `config/firewall.env.example` to `config/firewall.env` if needed.
2. Copy `config/openai-relay.env.example` to `config/openai-relay.env` if needed.
3. Copy `config/nuget-relay.env.example` to `config/nuget-relay.env` if needed.
4. Set the real `OPENAI_API_KEY` in `config/openai-relay.env`.
5. Set `AZURE_ARTIFACTS_PAT` in `config/nuget-relay.env` when private feeds are required.
6. Start and inspect the services:

```bash
sudo systemctl start agent-sandbox-firewall.service
sudo systemctl start agent-sandbox-gateway.service
sudo systemctl start agent-sandbox-openai-relay.service
sudo systemctl start agent-sandbox-nuget-relay.service
sudo systemctl status agent-sandbox-firewall.service
sudo systemctl status agent-sandbox-gateway.service
sudo systemctl status agent-sandbox-openai-relay.service
sudo systemctl status agent-sandbox-nuget-relay.service
```

## Local Git Remote Flow

For the common issue workflow, you can do the full host-side setup in one command:

```bash
./scripts/setup_issue_vm.sh /path/to/issue.yaml /path/to/project-or-solution.slnx
```

That prewarms the shared NuGet cache, launches `vm-14084`, bootstraps Codex/.NET/NuGet, and
configures the issue-numbered Git mirror and guest SSH path. It also clones the mirrored repo into
the guest user's home directory using the source repo basename. The cloned repo gets a
`git push-agent` alias, the guest home gets a short `AGENTS.md` note describing the expected
push pattern, the issue YAML is copied into the repo root as `AGENT_TASK.yaml`, and a background
Codex run starts in the guest by default. The guest-side Codex PID and log live under
`/home/agent/.agent-sandbox/`. Use `--dry-run` if you want the VM prepared without starting the
agent process. The helper still accepts a raw issue number as the first argument for the older
workflow, but YAML is the preferred input when you want the task definition to stay available on
disk during long agent runs.

To inspect or follow the guest agent from the host:

```bash
./scripts/check_issue_vm.sh 14084
./scripts/tail_issue_vm_log.sh 14084
```

Create a host-local bare repository:

```bash
cp config/git-server.env.example config/git-server.env
$EDITOR config/git-server.env
./scripts/setup_issue_git_vm.sh vm-real 14084
```

That one command bootstraps the guest SSH key path and creates or refreshes the
issue-numbered bare mirror on the host. Use the lower-level
`create_git_remote.sh` and `bootstrap_git_vm.sh` helpers directly only when you
need a non-issue-specific repository layout.

Or mirror the default host repo into an issue-numbered bare remote:

```bash
./scripts/mirror_issue_repo.sh 14084
```

Make sure the host SSH service is installed and reachable from the VM network:

```bash
sudo apt-get install -y openssh-server
sudo systemctl enable --now ssh
sudo ./scripts/apply_firewall.sh
```

`bootstrap_git_vm.sh` creates a dedicated guest SSH key, installs a forced-command
`authorized_keys` entry on the host, and configures the guest-side alias
`agent-sandbox-git-host` so the VM does not need your host password.

Then in the VM, add the printed SSH remote URL and push into a session-specific namespace such as:

```bash
git push agent HEAD:refs/heads/agents/<session-id>/<branch-name>
```

From the host, promote a specific quarantined agent branch into a trusted working clone:

```bash
./scripts/promote_agent_branch.sh /path/to/trusted-clone sample-repo agents/<session-id>/<branch-name>
```

That creates a local review branch like `review/<session-id>/<branch-name>` in the trusted clone
without pushing anything to `origin`.

After review and tests pass, push the reviewed branch to `origin` and optionally open the PR:

```bash
./scripts/open_agent_pr.sh /path/to/trusted-clone review/<session-id>/<branch-name>
./scripts/open_agent_pr.sh --create-pr --base main /path/to/trusted-clone review/<session-id>/<branch-name>
```

This is the preferred flow: VM -> quarantine bare repo -> trusted host clone -> `origin`/PR.

## Live Smoke Tests

Use [live-smoke-tests.md](/home/jonathan/agent-sandbox/docs/live-smoke-tests.md) for the
decision-complete runbook covering:

- host gateway only verification
- full guest-to-host verification
- dedicated Discord test channels
- one Gmail and one Graph mailbox
- read-only email validation

## Operational Guardrails

- Keep the base cloud image read-only and only boot guests from overlays.
- Destroy guests after each session.
- Store provider credentials only on the host side.
- Keep email OAuth tokens in host env files/shell context only (for example,
  `config/gateway.env` consumed by systemd); never embed them in guest images, cloud-init, or
  session manifests.
- Rotate Discord and email credentials independently from VM lifecycle.
- Keep Discord bot tokens only in host environment files or shell context; do not write them into
  guest images, cloud-init, or session manifests.
- Review `runtime/audit.jsonl` as the source of truth for privileged activity.
- Keep VM disks and seed ISOs out of home directories when using `qemu:///system`; the default scripts now place them under `/var/lib/libvirt/images/agent-sandbox`.
