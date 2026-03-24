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
13. Optionally provision a guest-local Microsoft SQL Server instance into the guest with `MSSQL_SA_PASSWORD=... ./scripts/bootstrap_mssql_vm.sh <vm-name>`.
14. Optionally install Playwright into the guest with `./scripts/bootstrap_playwright_vm.sh <vm-name>` for rendered-DOM debugging.
15. Optionally create a local bare Git remote with `./scripts/create_git_remote.sh <repo-name>` or mirror the default host repo with `./scripts/mirror_issue_repo.sh <issue-number>` if the VM should push code back to the host.
16. Use [live-smoke-tests.md](/home/jonathan/agent-sandbox/docs/live-smoke-tests.md) when you want to verify live Discord and provider-backed email behavior against real credentials.

- The launch script resizes each overlay disk to `20G` by default.
- The launch script also exposes a read-only shared NuGet cache via `virtiofs` at `/opt/agent-sandbox/nuget-cache-ro` inside the guest.
- Override per VM with `DISK_SIZE_GB=40 sudo ./scripts/launch_agent_vm.sh ...`.

## First Troubleshooting Step

Before debugging individual scripts or services, run the consolidated host status command:

- Human-readable summary:
  - `agent-sandbox-cli status --config config/gateway.yaml`
- Machine-readable output:
  - `agent-sandbox-cli status --config config/gateway.yaml --json`

The status output reports config load status, runtime store path permissions, key systemd unit states,
gateway/relay endpoint reachability, and active/expired session summary.

## SSH Access

- New VMs install `openssh-server`.
- The launch script injects the caller's `~/.ssh/id_ed25519.pub` by default, falling back to `~/.ssh/id_rsa.pub`.
- Override the key with `SSH_PUBLIC_KEY_FILE=/path/to/key.pub`.
- After boot, find the guest IP with `virsh domifaddr <vm-name>` and connect with `ssh agent@<vm-ip>`.
- To list all currently running VMs with live guest agent status from the host, run `agent-sandbox-cli list-vms`.

## VM Codex Configuration

- Cloud-init writes provider-neutral baseline config files: `/etc/agent-sandbox/session.env` and `/home/agent/.agent/config.env`.
- Cloud-init also appends a shell profile hook that loads `/etc/agent-sandbox/session.env` and exports `OPENAI_API_KEY=relay-placeholder` if no key is already set.
- `./scripts/bootstrap_codex_vm.sh <vm-name>` copies the vendored Linux Codex binary from the host into `~/bin/codex` for the guest user and verifies Codex.
- The bootstrap script writes `~/.codex/config.toml` with the relay-aware configuration, `model = "gpt-5.4"`, `model_reasoning_effort = "high"`, and `personality = "pragmatic"`, preferring explicit `LLM_BASE_URL` and otherwise reading `AGENT_SANDBOX_LLM_BASE_URL` from `~/.agent/config.env`; it then appends the placeholder-key shell hook plus `~/bin` PATH entry to both `~/.profile` and `~/.bashrc`.
- The bootstrap script repairs `/home/agent` ownership first, so it also works against VMs created before the home directory ownership fix.
- New VMs pick this up automatically; existing VMs need to be recreated.

## VM Playwright Bootstrap

- `./scripts/bootstrap_playwright_vm.sh <vm-name>` copies a host-seeded Node runtime into `~/toolchains/node-v24.14.0`.
- It copies `playwright` and `playwright-core` into `~/playwright-runtime/node_modules`.
- It copies the host Playwright browser cache into `~/.cache/ms-playwright`.
- It appends `PATH`, `PLAYWRIGHT_BROWSERS_PATH`, and `NODE_PATH` exports to `~/.profile`.
- It verifies the guest can resolve the Chromium executable without guest internet access.

## VM Frontend Dependency Bootstrap

- `./scripts/bootstrap_frontend_deps_vm.sh <vm-name> <host-checkout-root> <install-root> [install-root ...]` copies a host-seeded Node runtime into `~/toolchains/node-v24.14.0`.
- It snapshots each selected install root under `~/frontend-deps-runtime/install-roots/<relative-path>`.
- Each install root must already contain `package.json` and `node_modules` on the host.
- `setup_issue_vm.sh` restores those snapshots into the matching relative paths inside the guest clone so Angular builds can use the copied dependency trees offline.
- It appends the Node `PATH` export to `~/.profile` and `~/.bashrc`.

## VM .NET Bootstrap

- `./scripts/bootstrap_dotnet_vm.sh <vm-name>` copies the host `.NET` installation into `~/toolchains/dotnet`.
- It seeds host dotnet tool state into `~/.dotnet` and copies any resolver-cache-backed tool payloads from the host NuGet cache into `~/.nuget/packages`.
- It appends `DOTNET_ROOT`, `DOTNET_CLI_HOME`, and a tool-aware `PATH` to `~/.profile` and `~/.bashrc`.
- It verifies the guest can run `dotnet --info` without guest internet access.
- When the host has Entity Framework tooling cached, it also exposes `dotnet-ef` in the guest and verifies `dotnet-ef --version`.

## VM NuGet Bootstrap

- `./scripts/bootstrap_nuget_vm.sh <vm-name>` writes `~/.nuget/NuGet/NuGet.Config`.
- The generated config points to the host NuGet relay for both nuget.org and Azure Artifacts.
- The generated config uses:
  - guest-local writable `globalPackagesFolder` at `/home/agent/.nuget/packages-local`
  - guest-local writable `httpCacheFolder` at `/home/agent/.nuget/http-cache`
  - read-only `fallbackPackageFolders` at `/opt/agent-sandbox/nuget-cache-ro`
- It runs `dotnet nuget list source` when `dotnet` is available in the guest.

## VM Microsoft SQL Server Bootstrap

- `MSSQL_SA_PASSWORD=... ./scripts/bootstrap_mssql_vm.sh <vm-name>` bootstraps both SQL Server tooling and a guest-local SQL Server engine.
- The tooling step copies host-installed `sqlcmd` into `~/toolchains/mssql-tools` and appends its `bin` directory to `~/.profile` and `~/.bashrc`.
- The server step copies the host SQL Server engine into `/opt/mssql`, creates a guest-local `/var/opt/mssql` data directory, initializes or resets the SA password with `mssql-conf`, and starts `mssql-server` as a local systemd service.
- The final verification runs `sqlcmd -S localhost,1433 ... -Q "SELECT 1"` against the guest-local instance.
- `--init-root /path/to/mssql-init` copies that directory into `/var/opt/mssql/init` and executes `init.sql`, which can run arbitrary setup or restore commands for the local instance.

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
  ./scripts/inspect_nuget_cache.sh Acme.Product.Core 2026.3.6.1
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

### Relay streaming troubleshooting

When diagnosing streamed `/v1/*` relay calls, inspect host relay logs for these structured events:

- `relay_stream_request_start` with `method`, `path`, `upstream`, and stream counters (`stream_started`, `clean_end`, `transport_terminated_end`).
- `relay_stream_upstream_response` with upstream `status_code` and `content_type`.
- `relay_stream_completed` for clean ends, with per-request `chunks` and `bytes` totals plus global counters.
- `relay_stream_transport_terminated` when `ReadError`/`RemoteProtocolError` is suppressed after partial or full delivery. This warning includes `exception_class`, `exception_message`, `chunks`, `bytes`, and global counters.

A transport-terminated warning without client-visible failure can be expected for some upstream SSE responses that close abruptly after emitting the final event.

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

## Feedback Heartbeat (Host Worker + Guest MCP Loop)

Use this run pattern when a guest should continuously pick up human/provider feedback through MCP
without exposing provider credentials in the VM.

### Polling objective

- Target cadence: **2-5 minutes** per active session.
- Expected feedback latency: usually one interval (**~2-5 minutes**) plus MCP request time.

### Required MCP call sequence

1. Lightweight pending check:
   - `has_pending_feedback(session_id)`
2. Unread fetch when pending is true:
   - `list_unread_feedback(session_id, last_read_cursor, limit)` (set `limit=1` for strict one-comment handling)
3. Post threaded replies (when needed):
   - `post_pr_feedback_reply(session_id, provider, repo, pr_id, body_text, thread_id/comment_id, idempotency_key)`
4. Cursor advancement:
   - advance `last_read_cursor` only after local handling and any provider reply posting have completed durably.

### Guest state model

- Persist `last_read_cursor` per `session_id` in guest-local durable state.
- Never share cursors across sessions.
- Advance cursor only after the fetched items are durably handled.
- Keep handlers idempotent by feedback ID to tolerate retries/replays.

### Host heartbeat worker

- Feedback polling now runs on the host via `agent-sandbox-cli host-heartbeat` (typically managed by `agent-sandbox-host-heartbeat.service`).
- Recommended service lifecycle commands:
  - `sudo systemctl start agent-sandbox-host-heartbeat.service`
  - `sudo systemctl stop agent-sandbox-host-heartbeat.service`
  - `sudo systemctl status agent-sandbox-host-heartbeat.service`
- Cadence is controlled by gateway config:
  - `host_heartbeat_poll_interval_seconds`
  - `host_heartbeat_jitter_seconds`
  - `host_heartbeat_cooldown_seconds`
  - `host_heartbeat_lock_path` (single-instance lock file)
- The host worker reads session manifests through runtime state and writes provider feedback/cursors to `runtime/pr-feedback-store.json`.
- Guests no longer run `agent-heartbeat.service` or `agent-heartbeat.timer`.
- `scripts/bootstrap_codex_vm.sh` now defaults `ENABLE_GUEST_HEARTBEAT=0` and skips legacy guest timer install unless explicitly overridden with `ENABLE_GUEST_HEARTBEAT=1` (deprecated compatibility mode).
- Migration note for existing VMs: run `systemctl --user disable --now agent-heartbeat.timer || true` to avoid double wake behavior when the host worker is enabled.

### Failure handling

- MCP unavailable/timeouts:
  - Keep cursor unchanged.
  - Log and retry on next loop (optionally with bounded backoff/jitter).
- Provider API failures during `post_pr_feedback_reply`:
  - Keep cursor unchanged for unhandled items.
  - Retry with the same `idempotency_key` so duplicates are suppressed if the prior attempt actually succeeded upstream.
  - Validate host-side provider credentials and endpoint reachability before replaying.
- Stale cursor:
  - Detect host cursor error.
  - Reset/recover cursor using a host-supported baseline, then continue.
- Partial fetch or partial local processing:
  - Treat as uncommitted.
  - Do not advance cursor; re-fetch on next loop.
- Stale host lock:
  - `host_heartbeat_lock_path` is enforced as single-instance protection.
  - If lock contention persists unexpectedly, inspect the host worker process/service before removing the lock file.

### Audit and log file locations

- Host feedback state: `runtime/pr-feedback-store.json` (ingested comments + cursors), `runtime/pr-reply-idempotency.json` (reply dedupe keys/results).
- Host audit trail: `runtime/audit.jsonl` (tool calls and denials/errors).

### Security boundary reminder

- Guests call MCP only; they do not hold Discord, email, or OpenAI provider credentials.
- Upstream provider access and secret material stay host-side in gateway/relay components.

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

## Host-Side Azure DevOps Backlog Worker

- The scaffold includes `agent-sandbox-ado-worker` and the equivalent foreground CLI command
  `agent-sandbox-cli poll-ado-backlog`.
- The worker polls Azure DevOps work items in `Committed` state tagged `truman`, writes YAML
  payloads under `ADO_YAML_OUTPUT_DIR`, and invokes `scripts/setup_issue_vm.sh`.
- Worker-triggered setup calls honor `ADO_BOOTSTRAP_DOTNET`, `ADO_BOOTSTRAP_NUGET`,
  `ADO_BOOTSTRAP_MSSQL`, `ADO_BOOTSTRAP_PLAYWRIGHT`, and `ADO_BOOTSTRAP_FRONTEND_DEPS` to
  decide which guest toolchains are bootstrapped for backlog-driven issue VMs. The first three
  default to `true`; the frontend-oriented toggles default to `false` because they require
  host-seeded Node artifacts. `ADO_BOOTSTRAP_FRONTEND_DEPS` requires
  `ADO_FRONTEND_INSTALL_ROOTS` and can optionally use `ADO_FRONTEND_HOST_CHECKOUT`
  when the mirrored repo source is remote.
- Configure environment in `config/ado-worker.env` (start from
  `config/ado-worker.env.example`), then export the PAT value in the host shell/env-file named by
  `ADO_PAT_ENV_VAR_NAME` (default `AZURE_DEVOPS_PAT`).

Foreground runbook:

```bash
cp config/ado-worker.env.example config/ado-worker.env
set -a; source config/ado-worker.env; set +a
export AZURE_DEVOPS_PAT=...
agent-sandbox-cli poll-ado-backlog --log-level INFO
```

Systemd runbook:

1. Ensure `config/ado-worker.env` exists and is populated.
2. Ensure the PAT variable named by `ADO_PAT_ENV_VAR_NAME` is available to the service user
   (for example through the user shell profile or a drop-in env file).
3. Start/inspect:

```bash
sudo systemctl start agent-sandbox-ado-worker.service
sudo systemctl status agent-sandbox-ado-worker.service
sudo journalctl -u agent-sandbox-ado-worker.service -f
```

Failure recovery:

- Re-run a specific work item once:

  ```bash
  set -a; source config/ado-worker.env; set +a
  export AZURE_DEVOPS_PAT=...
  agent-sandbox-cli poll-ado-backlog --reprocess 12345
  ```

- Clear processed state for a specific work item so the next normal poll can pick it up again:

  ```bash
  set -a; source config/ado-worker.env; set +a
  agent-sandbox-cli poll-ado-backlog --forget 12345
  ```

- List work items that match the worker query but are not yet in the processed store:

  ```bash
  export AZURE_DEVOPS_PAT=...
  ./scripts/list_unassigned_ado_work_items.sh
  ```

- If the service is crash-looping, validate config and PAT availability with a foreground run,
  then restart the unit:

  ```bash
  sudo systemctl restart agent-sandbox-ado-worker.service
  ```

## Systemd Services

Install persistent host services with a validation-first flow:

```bash
./scripts/install_systemd_units.sh --preflight
sudo ./scripts/install_systemd_units.sh
```

Run `--preflight` before the first install (and before reruns) to verify required binaries,
config files, and required env vars for enabled services without writing unit files.

This creates:

- `agent-sandbox-firewall.service`
- `agent-sandbox-gateway.service`
- `agent-sandbox-openai-relay.service`
- `agent-sandbox-nuget-relay.service`
- `agent-sandbox-ado-worker.service`
- `agent-sandbox-host-heartbeat.service`

Before running install mode, ensure preflight is clean (or only warning-only), then:

1. Copy `config/gateway.env.example` to `config/gateway.env` if needed.
2. Copy `config/firewall.env.example` to `config/firewall.env` if needed.
3. Copy `config/openai-relay.env.example` to `config/openai-relay.env` if needed.
4. Copy `config/nuget-relay.env.example` to `config/nuget-relay.env` if needed.
5. Copy `config/ado-worker.env.example` to `config/ado-worker.env` if needed.
6. Copy `config/host-heartbeat.env.example` to `config/host-heartbeat.env` if needed.
7. Set the real `OPENAI_API_KEY` in `config/openai-relay.env`.
8. Set `AZURE_ARTIFACTS_PAT` in `config/nuget-relay.env` when private feeds are required.
9. Ensure the PAT variable named by `ADO_PAT_ENV_VAR_NAME` (default `AZURE_DEVOPS_PAT`) is
   exported for the service user and `config/ado-worker.env` points at the right project/paths.
10. For private PR feedback polling, set `GITHUB_TOKEN` and/or `AZURE_DEVOPS_PAT` in
    `config/host-heartbeat.env` and ensure `config/gateway.yaml` sets the correct
    `azure_devops_organization_url` and `azure_devops_project`.
11. Review cadence/lock settings in `config/host-heartbeat.env` and then start and inspect services:

```bash
sudo systemctl start agent-sandbox-firewall.service
sudo systemctl start agent-sandbox-gateway.service
sudo systemctl start agent-sandbox-openai-relay.service
sudo systemctl start agent-sandbox-nuget-relay.service
sudo systemctl start agent-sandbox-ado-worker.service
sudo systemctl start agent-sandbox-host-heartbeat.service
sudo systemctl status agent-sandbox-firewall.service
sudo systemctl status agent-sandbox-gateway.service
sudo systemctl status agent-sandbox-openai-relay.service
sudo systemctl status agent-sandbox-nuget-relay.service
sudo systemctl status agent-sandbox-ado-worker.service
sudo systemctl status agent-sandbox-host-heartbeat.service
```

## Local Git Remote Flow

For the common issue workflow, you can do the full host-side setup in one command:

```bash
MSSQL_SA_PASSWORD='your-strong-password' ./scripts/setup_issue_vm.sh --dotnet --nuget --mssql --mssql-init-root /path/to/mssql-init --playwright --frontend-deps --frontend-install-root /path/to/repo/Src/Clients /path/to/issue.yaml /path/to/project-or-solution.slnx
```

`setup_issue_vm.sh` path defaults are configurable so host-specific `/home/...` paths are not
hard-coded:

- repo source precedence:
  1. explicit third positional argument
  2. `AGENT_SANDBOX_DEFAULT_REPO_SOURCE`
  3. repo-relative fallback (`<agent-sandbox-repo-root>`)
- guest clone remote root:
  - `AGENT_SANDBOX_GIT_ROOT` (from env or `config/git-server.env`) plus
    `/${ISSUE_NUMBER}_repo.git`
  - `AGENT_SANDBOX_GIT_ROOT` must exist on the host before guest clone starts
- clone URL SSH alias:
  - `AGENT_SANDBOX_GIT_SSH_ALIAS` (default `agent-sandbox-git-host`)

That launches `vm-14084`, bootstraps Codex plus any requested optional toolchains, and
configures the issue-numbered Git mirror and guest SSH path. It also clones the mirrored repo into
the guest user's home directory using the source repo basename. The cloned repo gets a
`git push-agent` alias, the guest home gets a short `AGENTS.md` note describing the expected
push pattern, the issue YAML is copied into the repo root as `AGENT_TASK.yaml`, and a background
Codex run starts in the guest by default. The guest-side Codex PID and log live under
`/home/agent/.agent-sandbox/`. Use `--no-start-agent` with `agent-sandbox-cli create-issue-vm`
or `--dry-run` with `setup_issue_vm.sh` if you want the VM prepared without starting the agent
process. The helper still accepts a raw issue number as the first argument for the older
workflow, but YAML is the preferred input when you want the task definition to stay available on
disk during long agent runs.
Optional toolchain flags for `setup_issue_vm.sh`:

- `--dotnet`: run `bootstrap_dotnet_vm.sh`
- `--nuget`: run `bootstrap_nuget_vm.sh` and prewarm the shared NuGet cache on the host (requires `project-or-solution`)
- `--mssql`: run `bootstrap_mssql_vm.sh` and require `MSSQL_SA_PASSWORD` in the environment
- `--mssql-init-root PATH`: copy a host init bundle into `/var/opt/mssql/init` and execute `init.sql`
- `--playwright`: run `bootstrap_playwright_vm.sh`, which also provides `node`, `npm`, and `npx`
- `--frontend-deps`: run `bootstrap_frontend_deps_vm.sh`
- `--frontend-install-root PATH`: select one frontend install root and repeat as needed
- `--frontend-host-checkout PATH`: override the host checkout used for frontend snapshots when `repo-source` is remote

When `--frontend-deps` is enabled, each install root must be inside the selected host checkout and
already contain `package.json` plus `node_modules`. For local repo sources, `setup_issue_vm.sh`
uses the resolved local checkout automatically. For remote repo sources, pass
`--frontend-host-checkout` so the helper can snapshot dependencies from a host checkout and
restore them into the matching relative paths inside the guest clone.

After the initial guest `codex exec` run exits, setup triggers one
`codex exec resume --last` verification pass. Customize that verification
prompt with `AGENT_SANDBOX_CODEX_POST_EXEC_VERIFICATION_PROMPT` (for example
in `config/git-server.env`) when you need different checklist wording.

To inspect or follow the guest agent from the host:

```bash
agent-sandbox-cli list-vms
agent-sandbox-cli logs 14084 --lines 50
```

For a lower-level point-in-time VM inspection, `./scripts/check_issue_vm.sh 14084`
is still available.

The script and CLI wrappers share the same timeout environment variables (defaults shown):

- `VM_WAIT_IP_TIMEOUT_SECONDS` (default `180`)
- `VM_WAIT_SSH_TIMEOUT_SECONDS` (default `180`)


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
Mirror freshness flags are accepted and forwarded through both setup helpers:

```bash
./scripts/setup_issue_git_vm.sh --sync-remote --remote-name origin vm-real 14084 /path/to/local/repo
./scripts/setup_issue_vm.sh --sync-remote /path/to/issue.yaml /path/to/project.slnx /path/to/local/repo
```

Issue mirrors now carry a `post-receive` hook. When the bare repo has a trusted clone mapping,
guest pushes to `refs/heads/agents/...` can automatically promote the branch into the trusted clone,
push the review branch to `origin`, create or reuse the upstream PR, and append an event to
`runtime/agent-events.log`.

### PR body boilerplate controls

PR body composition for automated `open_agent_pr.sh --create-pr` runs is controlled by host env
values loaded from `config/git-server.env`:

- `AGENT_SANDBOX_PR_FIXED_BODY_FILE` (optional): absolute path to operator-maintained markdown
  boilerplate that should always appear at the top of auto-created PR descriptions.
- `AGENT_SANDBOX_PR_NOTES_REF` (optional, default `refs/notes/agent-pr`): notes ref used to read
  agent summary markdown from the promoted commit.
- `AGENT_SANDBOX_PR_BODY_MAX_BYTES` (optional, default `60000`): hard byte cap applied to the
  composed PR body before creating/updating the PR.

On guest issue VMs, prefer `git agent-pr-note set` / `git agent-pr-note append` instead of raw
`git notes add -f`. The helper keeps an additive branch-local summary cache and `git push-agent`
reattaches that full summary to the current `HEAD` before it pushes `refs/notes/agent-pr`.

Expected resulting PR body shape when both fixed boilerplate and note summary are present:

```markdown
## Operator Checklist
- [ ] Security review complete
- [ ] Tests validated by human reviewer

---

## Agent Summary

### What changed
- ...
```

If the note is missing or unreadable, automation still proceeds:

- with fixed boilerplate only when `AGENT_SANDBOX_PR_FIXED_BODY_FILE` is set
- otherwise with the default PR body behavior from `open_agent_pr.sh`

Security reminder: git note content is untrusted agent-authored text. Treat it as markdown only;
never interpolate PR body text into shell commands.

Or mirror the default host repo into an issue-numbered bare remote:

```bash
./scripts/mirror_issue_repo.sh 14084
```

Freshness behavior for `mirror_issue_repo.sh`:

- local repo source path: default is **no upstream fetch**
- remote URL source: default is **always fetch latest refs** into cache before mirror update
- `--sync-remote`: fetch `git fetch --prune <remote-name>` for local source and run divergence checks
- `--force-local`: continue even when local default branch is behind upstream
- optional pinning: `--branch <name>` or `--refspec <src:dst>`

Examples:

```bash
./scripts/mirror_issue_repo.sh --sync-remote --remote-name origin 14084 /path/to/local/repo
./scripts/mirror_issue_repo.sh 14084 https://example.com/org/repo.git
./scripts/mirror_issue_repo.sh --branch main 14084 /path/to/local/repo
```

Each mirror run logs the source mode, selected base branch, and source commit SHA for auditing VM runs.

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
git push agent HEAD:refs/heads/agents/<session-id>/<branch-name>-<agent-suffix>
```

From the host, promote a specific quarantined agent branch into a trusted working clone:

```bash
./scripts/promote_agent_branch.sh /path/to/trusted-clone sample-repo agents/<session-id>/<branch-name>-<agent-suffix>
```

That creates a local review branch like `review/<session-id>/<branch-name>-<agent-suffix>` in the trusted clone
without pushing anything to `origin`.

After review and tests pass, push the reviewed branch to `origin` and optionally open the PR:

```bash
./scripts/open_agent_pr.sh /path/to/trusted-clone review/<session-id>/<branch-name>-<agent-suffix>
./scripts/open_agent_pr.sh --create-pr --base main /path/to/trusted-clone review/<session-id>/<branch-name>-<agent-suffix>
```

This is the preferred flow: VM -> quarantine bare repo -> trusted host clone -> `origin`/PR.

For Azure DevOps origins, set `AZURE_DEVOPS_PAT` in `config/git-server.env` so the PR helper can
create upstream PRs automatically against the Azure-hosted canonical repository.

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
