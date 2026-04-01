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
2. Run `agent-sandbox-cli init-state --runtime-dir runtime`.
3. Edit host-global bootstrap settings in `runtime/host-config.json`.
4. Configure `root` directory overlays directly with the admin UI or `agent-sandbox-cli directories config-set`.
5. If you are importing an older install, run `agent-sandbox-cli migrate-global-config-to-root --config /path/to/legacy-gateway.yaml` once to seed `host-config.json` plus `root` from the legacy YAML file.
6. Create the libvirt network with `./scripts/create_libvirt_network.sh`.
7. Apply guest forwarding policy with `./scripts/apply_firewall.sh`.
8. Download the Ubuntu cloud image with `sudo ./scripts/create_base_vm.sh`.
9. Create a session manifest with `agent-sandbox-cli create-session`.
10. Launch a VM with `sudo ./scripts/launch_agent_vm.sh`.
10. Optionally bootstrap a provider runtime in the guest (for example `./scripts/bootstrap_codex_vm.sh <vm-name>` or `./scripts/bootstrap_copilot_vm.sh <vm-name>`).
11. Optionally bootstrap a Python repo into a guest-local virtualenv with `./scripts/bootstrap_python_vm.sh <vm-name> <host-checkout-root> <guest-repo-root>`.
12. Optionally install the host `.NET` SDK into the guest with `./scripts/bootstrap_dotnet_vm.sh <vm-name>`.
13. Optionally prewarm the shared host NuGet cache with `./scripts/prewarm_nuget_cache.sh <path> [more paths...]`.
14. Optionally install NuGet relay sources into the guest with `./scripts/bootstrap_nuget_vm.sh <vm-name>`.
15. Optionally provision a guest-local Microsoft SQL Server instance into the guest with `MSSQL_SA_PASSWORD=... ./scripts/bootstrap_mssql_vm.sh <vm-name>`.
16. Optionally install Playwright into the guest with `./scripts/bootstrap_playwright_vm.sh <vm-name>` for rendered-DOM debugging.
17. Optionally create a local bare Git remote with `./scripts/create_git_remote.sh <repo-name>` or mirror the default host repo with `./scripts/mirror_issue_repo.sh <issue-number>` if the VM should push code back to the host.
18. Use [live-smoke-tests.md](/home/jonathan/agent-sandbox/docs/live-smoke-tests.md) when you want to verify live Discord and provider-backed email behavior against real credentials.

- The launch script resizes each overlay disk to `20G` by default.
- The launch script also exposes a read-only shared NuGet cache via `virtiofs` at `/opt/agent-sandbox/nuget-cache-ro` inside the guest.
- Override per VM with `DISK_SIZE_GB=40 sudo ./scripts/launch_agent_vm.sh ...`.

## First Troubleshooting Step

Before debugging individual scripts or services, run the consolidated host status command:

- Human-readable summary:
  - `agent-sandbox-cli status --runtime-dir runtime`
- Machine-readable output:
  - `agent-sandbox-cli status --runtime-dir runtime --json`

The status output reports config load status, runtime store path permissions, key systemd unit states,
gateway/relay endpoint reachability, and active/expired session summary.

## Desktop Operator Console

For a host-local desktop GUI on Ubuntu, install the optional desktop extra:

```bash
uv pip install -e '.[desktop]'
```

Then launch:

```bash
agent-sandbox-admin
```

The console is intentionally single-user and localhost-oriented. It uses the same host-side
orchestration layer as the CLI and surfaces:

- all active and inactive VMs
- VM power state and guest agent state separately
- one-click `Start`, `Shutdown`, `Enable`, `Disable`, and `Resume`
- local issue creation plus issue-VM startup
- persistent selected-directory context with `root` fallback
- direct-owned VM/session/issue listings for the selected directory
- a configuration tab that inspects inherited per-domain directory config

For machine-readable scripting that matches the GUI data model, use:

```bash
agent-sandbox-cli list-vms --all --json
agent-sandbox-cli list-local-issues --json
```

## Directory Operations

Directory ownership defaults to `root`. The directory tree is host-managed, with immutable `root`
at the top and inherited config flowing from `root` down to the selected child directory. When
enabled, `root` can also store explicit directory overlay files and those are the first
directory-scoped values in the inheritance chain.

To keep the rollout safe, directory-scoped config remains transition-gated:

- `enable_directory_scoped_config: false` or unset:
  - directory edit/config write surfaces are disabled
  - worker directory shadow resolution stays off
- `enable_directory_scoped_config: true`:
  - host-side admin and CLI directory edit/config write surfaces are enabled
  - workers compute directory config in shadow/readiness mode

Initialize missing ownership on an existing runtime:

```bash
agent-sandbox-cli migrate-directory-ownership --runtime-dir runtime
```

Common CLI flows:

```bash
agent-sandbox-cli directories list --runtime-dir runtime --parent root
agent-sandbox-cli directories create --runtime-dir runtime --parent root team
agent-sandbox-cli directories rename --runtime-dir runtime root/team sandbox
agent-sandbox-cli directories move --runtime-dir runtime root/sandbox --new-parent root
agent-sandbox-cli directories show --runtime-dir runtime root
agent-sandbox-cli directories config-set --runtime-dir runtime --domain policy root/team '{"default_session_ttl_seconds":120}'
agent-sandbox-cli directories config-show --runtime-dir runtime --domain policy root/team
```

Storage layout for directory config:

- directory metadata source of truth: `runtime/directories.json`
- host-global bootstrap source of truth: `runtime/host-config.json`
- file-backed per-directory overlays: `runtime/directory-config/root/.../policy.json`
- supported domains:
  - `policy.json`
  - `providers.json`
  - `email.json`
  - `heartbeat.json`
  - `ado-worker.json`
  - `issue-setup.json`
  - `vm.json`
- `root` is the live top-level layer; there is no hidden YAML base beneath it.

## SSH Access

- New VMs install `openssh-server`.
- The launch script injects the caller's `~/.ssh/id_ed25519.pub` by default, falling back to `~/.ssh/id_rsa.pub`.
- Override the key with `SSH_PUBLIC_KEY_FILE=/path/to/key.pub`, or point `SSH_PRIVATE_KEY_PATH` at the matching private key so the script can derive a temporary public key when needed.
- After boot, find the guest IP with `virsh domifaddr <vm-name>` and connect with `ssh agent@<vm-ip>`.
- Host-side guest SSH commands store guest host keys in `~/.ssh/agent-sandbox-known_hosts` keyed by VM name. `destroy_agent_vm.sh` removes the stale alias for a destroyed VM so a replacement VM can be learned cleanly.
- To list all currently running VMs with live guest agent status from the host, run `agent-sandbox-cli list-vms`.

## VM Agent Runtime Baseline

- Cloud-init writes provider-neutral baseline config files: `/etc/agent-sandbox/session.env` and `/home/agent/.agent/config.env`.
- Cloud-init also appends a shell profile hook that loads `/etc/agent-sandbox/session.env` and exports `OPENAI_API_KEY=relay-placeholder` if no key is already set.
- Generic orchestration status is provider-neutral and read from `/home/agent/.agent-sandbox/agent-*.state`, with companion `agent-*.pid` and `agent-*.log` files.
- New VMs pick this up automatically; existing VMs need to be recreated if they predate baseline config changes.

### Codex provider bootstrap

- `./scripts/bootstrap_codex_vm.sh <vm-name>` copies the vendored Linux Codex binary from the host into `~/bin/codex` for the guest user and verifies Codex.
- The bootstrap script writes `~/.codex/config.toml` with relay-aware defaults (currently `model = "gpt-5.4"`, `model_reasoning_effort = "high"`, and `personality = "pragmatic"`), preferring explicit `LLM_BASE_URL` and otherwise reading `AGENT_SANDBOX_LLM_BASE_URL` from `~/.agent/config.env`; it also registers the host MCP gateway as `[mcp_servers.agent_sandbox]` using `AGENT_SANDBOX_GATEWAY_URL` and, when the host session manifest is available, mirrors that session's `allowed_tools` into `enabled_tools` so Codex only discovers the MCP tools that session may use. It then appends the placeholder-key shell hook plus `~/bin` PATH entry to both `~/.profile` and `~/.bashrc`.
- Runtime start/resume model selection is host-session-bound: `session.model` wins, otherwise effective `issue_setup.default_model` is used. If neither resolves, start/resume fails with a remediation error instead of using an implicit runtime fallback model.
- The bootstrap script repairs `/home/agent` ownership first, so it also works against VMs created before the home directory ownership fix.

### GitHub Copilot CLI provider bootstrap

- `./scripts/bootstrap_copilot_vm.sh <vm-name>` copies the host `copilot` binary into `~/bin/copilot` for the guest user and verifies `copilot version`.
- The bootstrap script writes `~/.copilot/config.json` with provider-local defaults and `~/.copilot/mcp-config.json` so Copilot CLI can reach the host MCP gateway as `agent_sandbox`, including the active `session_id` query string when present.
- The bootstrap script also installs a guest `gh` shim in `~/bin/gh`. Copilot CLI uses GitHub CLI as its lowest-priority auth fallback, so the shim answers `gh auth token` by calling the host gateway at `/copilot-auth/token` with the guest session id. That keeps the long-lived GitHub token on the host while avoiding permanent guest-side token storage.
- The bootstrap script appends `~/bin` and `COPILOT_HOME=~/.copilot` to both `~/.profile` and `~/.bashrc`.
- Host-mediated auth reads the effective provider `github_token_env` on the host. That environment variable must contain a GitHub token that can satisfy Copilot CLI requests. Guest-side `COPILOT_GITHUB_TOKEN`, `GH_TOKEN`, `GITHUB_TOKEN`, or `copilot login` still override the host-mediated `gh` fallback if you set them intentionally.

## VM Python Bootstrap

- `./scripts/bootstrap_python_vm.sh <vm-name> <host-checkout-root> <guest-repo-root>` builds a temporary host wheelhouse from the repo's `pyproject.toml`, copies it into the guest, creates a repo-local `.venv`, and installs the repo with dev dependencies offline.
- The bootstrap validates that the guest Python interpreter satisfies the repo's `requires-python` lower bound before it creates the virtualenv.
- The guest install path uses `pip install --no-index --find-links ... --no-build-isolation -e ".[dev]"` so later guest edits still run against the cloned checkout rather than a prebuilt wheel.

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

- Set `discord_backend: "bot_api"` in the effective `root` provider config to enable the provider-backed Discord adapter.
- Set `discord_bot_token_env` in the effective `root` provider config (default `DISCORD_BOT_TOKEN`).
- Export the matching host environment variable before launching `agent-sandbox-gateway`:

  ```bash
  export DISCORD_BOT_TOKEN=...
  ```

- Use per-session channel allowlists when creating session manifests:

  ```bash
  agent-sandbox-cli create-session \
    real-session \
    --runtime-dir runtime \
    --discord-channel 123456789012345678
  ```

- Use `discord_operator_user_allowlist` in the effective `root` policy config for trusted operator user IDs that
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
  ./.venv/bin/agent-sandbox-cli seed-discord-channel 123456789012345678 --runtime-dir runtime
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
  - The Discord `bot_api` adapter retries transient failures with exponential backoff and jitter.
  - Retries apply to connection/time-out failures plus HTTP `429` and `5xx` responses.
  - Non-retryable Discord HTTP errors still bubble up to the tool caller immediately.
  - Retryable failures bubble up to the tool caller only after the retry budget is exhausted.
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

- Configure one or more `email_mailboxes` entries in the effective `root` email config with:
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
   - `list_all_feedback(session_id, limit)` when you need full PR comment history, including already-read items
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
- Cadence and resume behavior are controlled by `config/host-heartbeat/config.json`:
  - `host_heartbeat_directory_paths` (canonical directories to process; the worker skips polling when the list is empty)
  - `host_heartbeat_poll_interval_seconds`
  - `host_heartbeat_jitter_seconds`
  - `host_heartbeat_cooldown_seconds`
  - `host_heartbeat_http_timeout_seconds`
  - `host_heartbeat_lock_path` (single-instance lock file)
  - `host_heartbeat_session_cooldown_path`
  - `host_busy_check_script_path`
  - `host_resume_script_path`
  - `host_heartbeat_wake_state_path`
  - `host_heartbeat_vm_user`
  - `host_heartbeat_resume_timeout_seconds`
  - `host_heartbeat_stream_conflict_policy`
  - `host_heartbeat_scheduled_wake_prefix_text` (applied as `prefix + scheduled prompt` for MCP-triggered schedule dispatches)
  - `host_heartbeat_schedule_max_active_per_session` (optional scheduling guardrail)
  - `host_heartbeat_schedule_max_active_total` (optional scheduling guardrail)
  - `host_heartbeat_schedule_min_interval_seconds` (optional scheduling guardrail)
- `host_heartbeat_pr_resume_prompt` and `host_heartbeat_discord_resume_prompt` remain directory-scoped in `heartbeat.json`.
- The host heartbeat worker runs one pass per configured directory and resolves the effective PR/Discord wake prompt text from that directory's config before dispatching a resume.
- MCP-triggered scheduled resumes are always dispatched with `resume_mode=same_context`.
- Wake schedule CRUD is available in the CLI under `wake-schedules`:
  - create one-time wake: `agent-sandbox-cli wake-schedules create --runtime-dir runtime --session-id issue-11-session --resume-at 2026-04-01T10:30:00Z --prompt "Resume pending work."`
  - create recurring wake: `agent-sandbox-cli wake-schedules create --runtime-dir runtime --session-id issue-11-session --cron "*/10 * * * *" --prompt "Periodic check-in." --mode new_context`
  - edit existing wake: `agent-sandbox-cli wake-schedules edit <request-id> --cron "*/15 * * * *" --prompt "Updated prompt"`
  - delete wake: `agent-sandbox-cli wake-schedules delete <request-id>`
- Agent control state is mirrored on the host in `runtime/host-agent-control-state.json`; the host heartbeat worker checks that file before probing or resuming a session so disabled agents stay ignored even when their VMs are powered off.
- The host worker reads session manifests through runtime state and writes provider feedback/cursors to `runtime/pr-feedback-store.json`.
- Guests no longer run `agent-heartbeat.service` or `agent-heartbeat.timer` by default.
- Provider bootstrap scripts default `ENABLE_GUEST_HEARTBEAT=0`, which disables the guest timer on bootstrap.
- Set `ENABLE_GUEST_HEARTBEAT=1` during bootstrap to install and enable `agent-heartbeat.timer` inside the guest.
  The timer resumes via the configured provider adapter and accepts `GUEST_HEARTBEAT_PROMPT` to override the guest wake prompt.
- Migration note for existing VMs: run `systemctl --user disable --now agent-heartbeat.timer || true` to keep host-only wake behavior.

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

- Host feedback state: `runtime/pr-feedback-store.json` (ingested comments + cursors), `runtime/pr-reply-idempotency.json` (reply dedupe keys/results), `runtime/host-agent-control-state.json` (durable enabled/disabled state by VM).
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
agent-sandbox-cli directories config-set \
  --runtime-dir runtime \
  --domain policy \
  root \
  '{"email_mailbox_allowlist":["ops-gmail","exec-graph","finance-graph"],"email_folder_allowlist_by_mailbox":{"ops-gmail":["inbox","drafts"],"exec-graph":["inbox","drafts"],"finance-graph":["inbox","drafts"]}}'

agent-sandbox-cli create-session real-session --runtime-dir runtime
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
- A polled ADO work item is represented in the unified local issue store as a proxy issue
  (`source: proxy_azure_devops`) keyed by local issue number with remote linkage in
  `metadata.remote` and a last-seen cache in `metadata.proxy_cache`.
- Re-polling the same remote issue updates the existing local proxy record/cache instead of
  creating duplicates, so Desktop and CLI views remain stable across worker cycles.
- Worker-triggered setup calls honor `ADO_BOOTSTRAP_DOTNET`, `ADO_BOOTSTRAP_NUGET`,
  `ADO_BOOTSTRAP_MSSQL`, `ADO_BOOTSTRAP_PLAYWRIGHT`, and `ADO_BOOTSTRAP_FRONTEND_DEPS` to
  decide which guest toolchains are bootstrapped for backlog-driven issue VMs. The first three
  default to `true`; the frontend-oriented toggles default to `false` because they require
  host-seeded Node artifacts. `ADO_BOOTSTRAP_MSSQL` requires `ADO_MSSQL_SA_PASSWORD`
  (or legacy `MSSQL_SA_PASSWORD`). `ADO_BOOTSTRAP_FRONTEND_DEPS` requires
  `ADO_FRONTEND_INSTALL_ROOTS` and can optionally use `ADO_FRONTEND_HOST_CHECKOUT`
  when the mirrored repo source is remote.
- Directory-scoped `ado_worker` config can set `ado_repo_source` to control the repo source path
  or remote URL passed to `setup_issue_vm.sh` for each directory.
- Preferred configuration now lives in directory domain `ado_worker` (`ado-worker.json`) so values
  inherit through directory ancestry and appear in the Admin GUI automatically.
- Global worker settings now live in `config/ado-worker/config.json` and are edited from the
  Admin GUI's Workers tab when the `ado` worker is selected. That file now also carries the
  dedicated host env-var name for the work-item Azure DevOps PAT and the list of directories
  the worker should poll each cycle.
- `ADO_*` env vars continue to work as compatibility fallbacks for one release and log deprecation
  warnings.
- Materialize `root/ado-worker.json` with `agent-sandbox-cli migrate-ado-worker-env-to-root` or
  `migrate-global-config-to-root`, then export the PAT value in the host shell or `secrets.env`
  named by `ado_work_items_azure_devops_pat_env` in `config/ado-worker/config.json` (default
  `ADO_WORK_ITEMS_AZURE_DEVOPS_PAT`).

Foreground runbook:

```bash
set -a; source secrets.env; set +a
agent-sandbox-cli poll-ado-backlog --log-level INFO
```

Systemd runbook:

1. Ensure `runtime/directory-config/root/ado-worker.json` exists and is populated.
2. Ensure `config/ado-worker/config.json` exists if you want to override the worker defaults.
3. Ensure the PAT variable named by `ado_work_items_azure_devops_pat_env` in
   `config/ado-worker/config.json` is available to the service user.
4. Source `secrets.env` or another secret env file if that is how you manage host secrets.
5. Start/inspect:

```bash
sudo systemctl start agent-sandbox-ado-worker.service
sudo systemctl status agent-sandbox-ado-worker.service
sudo journalctl -u agent-sandbox-ado-worker.service -f
```

Failure recovery:

- Re-run a specific work item once:
  Source `secrets.env` first if you keep secrets there.

  ```bash
  set -a; source secrets.env; set +a
  agent-sandbox-cli poll-ado-backlog --reprocess 12345
  ```

- Clear processed state for a specific work item so the next normal poll can pick it up again:
  Source `secrets.env` first if you keep secrets there.

  ```bash
  set -a; source secrets.env; set +a
  agent-sandbox-cli poll-ado-backlog --forget 12345
  ```

- List work items that match the worker query but are not yet in the processed store:

  ```bash
  export ADO_WORK_ITEMS_AZURE_DEVOPS_PAT=...
  ./scripts/list_unassigned_ado_work_items.sh
  ```

- If the service is crash-looping, validate config and PAT availability with a foreground run,
  then restart the unit:

  ```bash
  sudo systemctl restart agent-sandbox-ado-worker.service
  ```

## Host-Side Issue Orchestrator

- The scaffold includes `agent-sandbox-issue-orchestrator`, a dedicated host-side worker for the
  new `root/agent-sandbox/test` issue-generation workflow.
- This worker is separate from `agent-sandbox-ado-worker` and `agent-sandbox-host-heartbeat`; it
  does not change their behavior or scheduling.
- The orchestrator is intended to manage a small, role-based set of VM sessions that generate,
  triage, and implement local issues in that directory without affecting the existing APP or PR
  feedback flows.
- The worker reads its global settings from `config/issue-orchestrator/config.json`.
- The directory automation policy for `root/agent-sandbox/test` remains isolated from the ADO and
  feedback workers so the new workflow can evolve without changing existing issue handling.

Runbook:

```bash
sudo systemctl start agent-sandbox-issue-orchestrator.service
sudo systemctl status agent-sandbox-issue-orchestrator.service
sudo journalctl -u agent-sandbox-issue-orchestrator.service -f
```

## Systemd Services

Install persistent host services with a validation-first flow:

```bash
./scripts/install_systemd_units.sh --preflight
sudo ./scripts/install_systemd_units.sh
```

Run `--preflight` before the first install (and before reruns) to verify required binaries,
runtime config files, and required env vars for enabled services without writing unit files.

This creates:

- `agent-sandbox-firewall.service`
- `agent-sandbox-gateway.service`
- `agent-sandbox-openai-relay.service`
- `agent-sandbox-nuget-relay.service`
- `agent-sandbox-ado-worker.service`
- `agent-sandbox-host-heartbeat.service`
- `agent-sandbox-issue-orchestrator.service`

Before running install mode, ensure preflight is clean (or only warning-only), then:

1. Copy `config/firewall.env.example` to `config/firewall.env` if needed.
2. Ensure `runtime/directory-config/root/ado-worker.json` exists and the PAT variable named by
   `ado_work_items_azure_devops_pat_env` in `config/ado-worker/config.json` is exported for the
   service user.
3. Ensure `config/issue-orchestrator/config.json` exists if you plan to enable the new
   orchestrator scaffold.
4. Ensure the needed service env vars are available. The rendered units will load optional
   per-service `config/*.env` files and optional repo-root `secrets.env`.
5. Ensure `OPENAI_API_KEY` is available for the OpenAI relay and `AZURE_ARTIFACTS_PAT` when the
   NuGet relay needs private Azure Artifacts access.
6. For private PR feedback polling, ensure the service environment exposes `GITHUB_TOKEN`
   and/or `AZURE_DEVOPS_PR_PAT` and ensure the effective `root` provider config sets the correct
   `azure_devops_organization_url`, `azure_devops_project`, and `azure_devops_pr_pat_env`.
7. Start and inspect services:

```bash
sudo systemctl start agent-sandbox-firewall.service
sudo systemctl start agent-sandbox-gateway.service
sudo systemctl start agent-sandbox-openai-relay.service
sudo systemctl start agent-sandbox-nuget-relay.service
sudo systemctl start agent-sandbox-ado-worker.service
sudo systemctl start agent-sandbox-host-heartbeat.service
sudo systemctl start agent-sandbox-issue-orchestrator.service
sudo systemctl status agent-sandbox-firewall.service
sudo systemctl status agent-sandbox-gateway.service
sudo systemctl status agent-sandbox-openai-relay.service
sudo systemctl status agent-sandbox-nuget-relay.service
sudo systemctl status agent-sandbox-ado-worker.service
sudo systemctl status agent-sandbox-host-heartbeat.service
sudo systemctl status agent-sandbox-issue-orchestrator.service
```

## Local Git Remote Flow

For the common issue workflow, you can do the full host-side setup in one command:

```bash
MSSQL_SA_PASSWORD='your-strong-password' ./scripts/setup_issue_vm.sh --python --dotnet --nuget --mssql --mssql-init-root /path/to/mssql-init --playwright --frontend-deps --frontend-install-root /path/to/repo/Src/Clients /path/to/issue.yaml /path/to/project-or-solution.slnx
```

`setup_issue_vm.sh` path defaults are configurable so host-specific `/home/...` paths are not
hard-coded:

- repo source precedence:
  1. explicit third positional argument
  2. `AGENT_SANDBOX_DEFAULT_REPO_SOURCE`
  3. repo-relative fallback (`<agent-sandbox-repo-root>`)
- guest clone remote root:
  - `AGENT_SANDBOX_GIT_ROOT` from the `root` issue setup config or host environment, plus
    `/${ISSUE_NUMBER}_repo.git`
  - `AGENT_SANDBOX_GIT_ROOT` must exist on the host before guest clone starts
- clone URL SSH alias:
  - `AGENT_SANDBOX_GIT_SSH_ALIAS` (default `agent-sandbox-git-host`)

That launches `vm-14084`, bootstraps provider runtime tooling plus any requested optional toolchains, and
configures the issue-numbered Git mirror and guest SSH path. It also clones the mirrored repo into
the guest user's home directory using the source repo basename. The cloned repo gets a
`git push-agent` alias, the guest home gets a short `AGENTS.md` note describing the expected
push pattern, the issue YAML is copied into the repo root as `AGENT_TASK.yaml`, and a background
agent run starts in the guest by default. The guest-side agent PID, state, and log live under
`/home/agent/.agent-sandbox/`. Use `--no-start-agent` with `agent-sandbox-cli create-issue-vm`
or `--dry-run` with `setup_issue_vm.sh` if you want the VM prepared without starting the agent
process. The helper still accepts a raw issue number as the first argument for the older
workflow, but YAML is the preferred input when you want the task definition to stay available on
disk during long agent runs.
Optional toolchain flags for `setup_issue_vm.sh`:

- `--python`: run `bootstrap_python_vm.sh` and create a repo-local `.venv` from a host-built wheelhouse
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

When `--python` is enabled, the guest clone also gets a repo-local `.venv`. Activate it with
`. .venv/bin/activate`, then run `python -m pytest -q` and `python -m ruff check` from inside
the guest clone. These checks validate the repo contents in-guest, but they do not replace
host-side libvirt, firewall, or systemd verification for this scaffold.

After the initial guest provider command run exits, setup triggers one
provider-specific verification pass. Customize that verification
prompt with `AGENT_POST_EXEC_VERIFICATION_PROMPT` in the `root` issue setup config or as a
host environment variable when you need different checklist wording.

To inspect or follow the guest agent from the host:

```bash
agent-sandbox-cli list-vms
agent-sandbox-cli list-vms --all
agent-sandbox-cli logs 14084 --lines 50
```

For a lower-level point-in-time VM inspection, `./scripts/check_issue_vm.sh 14084`
is still available.

The script and CLI wrappers share the same timeout environment variables (defaults shown):

- `VM_WAIT_IP_TIMEOUT_SECONDS` (default `180`)
- `VM_WAIT_SSH_TIMEOUT_SECONDS` (default `180`)


Create a host-local bare repository:

```bash
agent-sandbox-cli directories config-set --domain issue_setup root '{"git_root": "/home/jonathan/agent-sandbox/git-remotes", "git_host": "192.168.250.1", "git_user": "jonathan", "git_ssh_alias": "agent-sandbox-git-host"}'
./scripts/setup_issue_git_vm.sh vm-real 14084
```

That one command bootstraps the guest SSH key path and creates or refreshes the
issue-numbered bare mirror on the host. Use the lower-level
`create_git_remote.sh` and `bootstrap_git_vm.sh` helpers directly only when you
need a non-issue-specific repository layout.
After that initial setup, a guest-side `git fetch` or `git pull` against the issue mirror causes the
restricted host Git wrapper to refresh the mirror from its configured source before serving the read.
That keeps the guest on the latest mirrored refs without granting the guest direct access to the
canonical remote.
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

PR body composition for automated `open_agent_pr.sh --create-pr` runs is controlled by host
environment variables:

- `AGENT_SANDBOX_PR_FIXED_BODY_FILE` (optional): absolute path to operator-maintained markdown
  boilerplate that should always appear at the top of auto-created PR descriptions. This is a
  host-wide override; the directory-scoped `pr_template` domain is the primary per-directory
  surface for PR boilerplate.
- `AGENT_SANDBOX_PR_NOTES_REF` (optional, default `refs/notes/agent-pr`): notes ref used to read
  agent summary markdown from the promoted commit.
- `AGENT_SANDBOX_PR_BODY_MAX_BYTES` (optional, default `60000`): hard byte cap applied to the
  composed PR body before creating/updating the PR.

To migrate the legacy repo-level `config/pr-template.md` into the root directory-scoped template,
run:

```bash
agent-sandbox-cli migrate-pr-template-to-root --config config/gateway.yaml
```

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

For Azure DevOps origins, set `AZURE_DEVOPS_PR_PAT` in the environment so the PR helper can
create upstream PRs automatically against the Azure-hosted canonical repository. When
`open_agent_pr.sh` has an Azure work item id, or a session id like `issue-14084-...`, it also
associates the PR with that work item in Azure DevOps.

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
