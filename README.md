# Agent Sandbox

`agent-sandbox` is a host-side control plane for running autonomous AI agents inside disposable libvirt VMs while exposing only narrow, audited capabilities through MCP.

## What This Scaffold Includes

- A Python MCP gateway with policy-gated Discord and email tool surfaces
- A host-side OpenAI relay that keeps the API key on the host and exposes an OpenAI-compatible endpoint to guests
- A host-side NuGet relay that proxies nuget.org and private Azure Artifacts feeds for guests
- A host-side Azure DevOps backlog worker that polls tagged work items and drives `setup_issue_vm.sh`
- A host-side workflow orchestrator that polls provider triggers, evaluates gates, and persists durable queue events for downstream workers/hooks
- A host-side shared NuGet cache that can be mounted read-only into guests for faster cold restores
- File-backed session, approval, audit, and draft storage for local development
- A small CLI for bootstrapping state and creating session manifests
- Additive directory ownership and directory-scoped configuration support with inherited overrides
- Libvirt, cloud-init, and firewall scripts for an isolated VM network
- Systemd service templates and an install script for persistent host services
- Local bare Git remote tooling for getting code changes back out of VMs
- Configuration templates and implementation notes for evolving the scaffold into a production setup
- A consolidated configuration parameter reference at `docs/configuration-reference.md`

## Core Security Model

- Guests have no direct access to host secrets.
- Guests reach only the MCP gateway over a private libvirt network.
- Every tool call is checked against a session manifest.
- Irreversible actions require a separate approval record and exact payload match.
- State is append-only where possible and intentionally simple in v1.

## Directory Model

- Every owned entity defaults to directory path `root`.
- `root` is hard-coded, immutable, and always present.
- `root` is the live top-level baseline.
- Child directories inherit configuration from `root -> ... -> selected`.
- `root` itself is the live top-level baseline and the top of the directory-scoped inheritance chain.
- Directory config uses per-domain overlays, with deep-merge for maps, replace semantics for lists, and last-write-wins for scalars.
- Legacy gateway YAML is only a migration/import source for existing installs; it is not the live runtime baseline.

Live config is now split into:

- host-global bootstrap config at `runtime/host-config.json`
- directory-scoped config rooted at `runtime/directory-config/root/...`

Directory-scoped config is rooted at `root`:

- configure `root` directly for top-level defaults and overlays
- use `migrate-global-config-to-root` only to import legacy gateway YAML content into `root`
- leave `enable_directory_scoped_config` as documented in the configuration reference when editing surfaces are available in your build

## Quick Start

1. Install host dependencies and create a virtual environment:

   ```bash
   sudo apt install qemu-system-x86 libvirt-daemon-system libvirt-clients virtinst cloud-image-utils ovmf
   uv venv
   . .venv/bin/activate
   uv pip install -e .
   ```

2. Initialize runtime state and host bootstrap config:

   ```bash
   agent-sandbox-cli init-state --runtime-dir runtime
   ```

   This creates `runtime/host-config.json`, store files, and the immutable `root` directory.
   Edit host-global settings in `host-config.json`, then configure `root` and child directories
   through the admin UI or `agent-sandbox-cli directories config-set`.

3. Initialize runtime state:

   ```bash
   agent-sandbox-cli init-state --runtime-dir runtime
   ```

   If you are adopting directory ownership on an existing runtime, backfill missing ownership fields without rewriting unrelated records:

   ```bash
   agent-sandbox-cli migrate-directory-ownership --runtime-dir runtime
   ```

4. Start the MCP gateway:

   ```bash
   agent-sandbox-gateway --runtime-dir runtime
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
   agent-sandbox-gateway --runtime-dir runtime
   agent-sandbox-openai-relay --runtime-dir runtime
   ```

7. Download the base image and launch VMs from a libvirt-accessible storage path:

   ```bash
   sudo ./scripts/create_base_vm.sh
   sudo ./scripts/launch_agent_vm.sh vm-real real-session
   ```

   By default, the launch script injects your host `~/.ssh/id_ed25519.pub` key into the guest and exposes a host-side LLM base URL of `http://192.168.250.1:11434/v1`. Override with `SSH_PUBLIC_KEY_FILE=...`, `SSH_PRIVATE_KEY_PATH=...`, or `LLM_BASE_URL=...` if needed.
   It also creates a shared host NuGet cache under `/var/lib/libvirt/images/agent-sandbox/nuget-cache/global-packages` and mounts it read-only into the guest at `/opt/agent-sandbox/nuget-cache-ro` via `virtiofs`.
   New VMs default to a `20G` virtual disk; override with `DISK_SIZE_GB=...` before launch if needed.
   New VMs receive provider-neutral baseline config (`/etc/agent-sandbox/session.env` and `/home/agent/.agent/config.env`) plus a shell profile hook; provider-specific config is written later by `bootstrap_codex_vm.sh` or `bootstrap_copilot_vm.sh`.
   Host-side guest SSH commands cache guest host keys in `~/.ssh/agent-sandbox-known_hosts` keyed by VM name, so repeated `list-vms`, bootstrap, and `agent-sandbox-cli ssh` calls stay quiet while preserving normal SSH host-key warnings.
   To see all currently running VMs together with live guest agent status, run `agent-sandbox-cli list-vms`.

8. (Optional) Bootstrap a provider runtime inside the VM:

   ```bash
   ./scripts/bootstrap_codex_vm.sh vm-real
   ./scripts/bootstrap_copilot_vm.sh vm-real
   ```

   By default, orchestration uses provider-neutral agent run state under `/home/agent/.agent-sandbox` (`agent-*.state`, `agent-*.pid`, `agent-*.log`).

   ### Codex provider bootstrap

   This copies the vendored Linux Codex binary from the host install into the guest user's `~/bin`, writes `~/.codex/config.toml` to use the host relay with bootstrap defaults (currently `model = "gpt-5.4"`, `model_reasoning_effort = "high"`, and `personality = "pragmatic"`), registers the host MCP gateway as `agent_sandbox`, mirrors the session manifest's `allowed_tools` into `enabled_tools` when available, prefers `LLM_BASE_URL` and otherwise falls back to `~/.agent/config.env`, ensures the placeholder API-key shell hook exists, and verifies `codex --version`.

   ### GitHub Copilot CLI provider bootstrap

   This copies the host `copilot` binary into the guest user's `~/bin`, writes `~/.copilot/mcp-config.json` so Copilot CLI can reach the host MCP gateway as `agent_sandbox`, installs a guest `gh` shim that serves `gh auth token` from a host gateway endpoint using the active `session_id`, sets `COPILOT_HOME` to `~/.copilot`, and verifies `copilot version`. The long-lived GitHub token stays on the host in the effective `github_token_env` provider setting and is not persisted in the guest. If you intentionally set `COPILOT_GITHUB_TOKEN`, `GH_TOKEN`, or `GITHUB_TOKEN`, or run `copilot login`, those methods still take precedence over the host-mediated `gh` fallback.

9. Optionally install the host `.NET` SDK into the VM:

   ```bash
   ./scripts/bootstrap_dotnet_vm.sh vm-real
   ```

   This copies the host `.NET` installation into the guest, seeds host dotnet tool state into `~/.dotnet`, appends `DOTNET_ROOT`, `DOTNET_CLI_HOME`, and tool-aware `PATH` exports to the guest profile, and verifies `dotnet --info`. When host-seeded Entity Framework tooling is available, the bootstrap also exposes `dotnet-ef` in the guest so agents can generate migrations offline.

10. Optionally bootstrap a Python repo into a guest-local virtualenv:

   ```bash
   ./scripts/bootstrap_python_vm.sh vm-real /path/to/repo /home/agent/$(basename /path/to/repo)
   ```

   This builds a temporary host wheelhouse from the repo's `pyproject.toml`, copies it into the guest, creates a repo-local `.venv`, and installs the repo with dev dependencies offline.

11. Optionally install host-relay NuGet sources into the VM:

   ```bash
   ./scripts/bootstrap_nuget_vm.sh vm-real
   ```

   This writes `~/.nuget/NuGet/NuGet.Config` in the guest so package restore can use host relay endpoints for nuget.org and Azure Artifacts, while keeping guest-local writable caches under `~/.nuget` and using the shared host cache as a read-only fallback package folder.

12. Optionally provision a guest-local Microsoft SQL Server instance into the VM:

   ```bash
   MSSQL_SA_PASSWORD='your-strong-password' ./scripts/bootstrap_mssql_vm.sh vm-real
   ```

   This bootstraps host-installed SQL Server tooling plus the database engine into the guest, starts a local `mssql-server` service listening on `localhost:1433`, and verifies `sqlcmd` can query the guest-local instance. If you also need schema or data initialization, pass `--init-root /path/to/mssql-init`, where `init.sql` can run arbitrary setup or restore commands against the local instance.

13. Optionally prewarm the shared host NuGet cache from the host before launching or restoring in guests:

   ```bash
   ./scripts/prewarm_nuget_cache.sh /path/to/project-or-solution.slnx
   ./scripts/inspect_nuget_cache.sh Acme.Product.Core 2026.3.6.1
   ```

   Prewarming fills the shared host cache explicitly through real host restores, and the inspect script shows cache size or whether a specific package/version is already present.

14. Optionally install Playwright into the VM for frontend debugging:

   ```bash
   ./scripts/bootstrap_playwright_vm.sh vm-real
   ```

   This copies a host-seeded Node runtime, Playwright packages, and browser cache into the guest and verifies that Playwright can resolve Chromium offline.

15. Optionally install a repo-specific browser-test runtime into the VM for frontend unit tests:

   ```bash
   ./scripts/bootstrap_frontend_deps_vm.sh vm-real /path/to/repo /path/to/repo/Src/Clients
   ```

   This copies a host-seeded Node runtime plus one or more frontend install roots from a host checkout into the guest. It is intended to make Angular and other frontend builds work offline from the guest clone, and each selected install root must already contain `package.json` and `node_modules`.

## Persistent Host Services

Install the gateway and relay services:

```bash
./scripts/install_systemd_units.sh --preflight
sudo ./scripts/install_systemd_units.sh
$EDITOR config/firewall.env
sudo systemctl start agent-sandbox-firewall.service
sudo systemctl start agent-sandbox-gateway.service
sudo systemctl start agent-sandbox-openai-relay.service
sudo systemctl start agent-sandbox-nuget-relay.service
sudo systemctl start agent-sandbox-ado-worker.service
sudo systemctl start agent-sandbox-host-heartbeat.service
```

### Trigger orchestrator

The scaffold includes `agent-sandbox-trigger-orchestrator`, a host-side worker that:

- validates and loads trigger definitions from `config/trigger-orchestrator/config.json`
- polls provider-specific trigger implementations (`cron`, `ticket`, `discord`, `email`, `pull-request`, `git`)
- evaluates gate nodes and routes events through explicit edges
- persists discovered events into a durable queue (`runtime/trigger-orchestrator-events.json` by default)
- records per-trigger cursor/checkpoint metadata (`runtime/trigger-orchestrator-cursors.json` by default)

One-shot cycle execution is supported for ops/debugging:

```bash
agent-sandbox-trigger-orchestrator --runtime-dir runtime --once --log-level INFO
```

For continuous operation, run the systemd unit:

```bash
sudo systemctl start agent-sandbox-trigger-orchestrator.service
```

The generated units run as your user where appropriate. Runtime settings come from runtime/root
config, while service env vars can come from optional per-service `config/*.env` files and/or
optional `secrets.env`.
For private PR feedback polling, the service environment must provide `GITHUB_TOKEN` and/or
`AZURE_DEVOPS_PR_PAT` as needed, and the effective `root` config must set the correct
`azure_devops_organization_url`, `azure_devops_project`, and heartbeat timing/lock settings.

### Azure DevOps backlog worker

For foreground/backfill operation, run:

```bash
set -a; source secrets.env; set +a
agent-sandbox-cli poll-ado-backlog --log-level INFO
```

The ADO worker reads `azure_devops_organization_url` and `azure_devops_project` from the
effective `root` provider config. Its work-item PAT env-var name now comes from
`config/ado-worker/config.json` via `ado_work_items_azure_devops_pat_env` (default
`ADO_WORK_ITEMS_AZURE_DEVOPS_PAT`). The root `ado_worker` directory config is the primary source
for worker bootstrap and filter settings, while `config/ado-worker/config.json` now also carries
the canonical directory list that the singleton worker polls on each cycle.

For one-off recovery actions:

```bash
# Forget prior state for an item so the next poll can process it again.
set -a; source secrets.env; set +a
agent-sandbox-cli poll-ado-backlog --forget 12345

# Immediately reprocess one item now (fetch + setup path), then exit.
set -a; source secrets.env; set +a
agent-sandbox-cli poll-ado-backlog --reprocess 12345

# List matching work items that have not yet been picked up by agents.
set -a; source secrets.env; set +a
./scripts/list_unassigned_ado_work_items.sh
```

The dedicated worker entry point is also available as `agent-sandbox-ado-worker` for systemd.
Backlog-worker VM setup calls can opt into `--dotnet`, `--nuget`, `--mssql`, `--playwright`,
and `--frontend-deps` via `ADO_BOOTSTRAP_DOTNET`, `ADO_BOOTSTRAP_NUGET`,
`ADO_BOOTSTRAP_MSSQL`, `ADO_BOOTSTRAP_PLAYWRIGHT`, and `ADO_BOOTSTRAP_FRONTEND_DEPS`.
The first three default to `true`; the frontend-oriented toggles default to `false` because
they depend on host-seeded artifacts. `ADO_BOOTSTRAP_MSSQL` also requires
`ADO_MSSQL_SA_PASSWORD` (or legacy `MSSQL_SA_PASSWORD`), and can optionally use
`ADO_MSSQL_INIT_ROOT`. `ADO_BOOTSTRAP_FRONTEND_DEPS`
requires `ADO_FRONTEND_INSTALL_ROOTS`, and can optionally use `ADO_FRONTEND_HOST_CHECKOUT`
when the mirrored repo source is remote.
Directory-scoped `ado_worker` config can also set `ado_repo_source` so backlog-driven setup uses
the correct host repo path or remote URL per directory.

## Desktop Admin Console

Install the optional desktop dependency set when you want a local Ubuntu operator GUI:

```bash
uv pip install -e '.[desktop]'
```

The desktop console is intended for a single operator on the host machine. It shows all
libvirt guests, separates VM power state from guest agent state, and exposes one-click
`Start`, `Shutdown`, `Enable`, `Disable`, and `Resume` actions together with local issue
creation and issue-VM startup.

Issue handling is now a single local abstraction in both Desktop and CLI flows:

- local-only issues are created and managed in `runtime/local-issues.json`
- ADO-backed issues are stored as local **proxy issues** (`source: proxy_azure_devops`) with
  remote identity under `metadata.remote` plus a cached snapshot under `metadata.proxy_cache`
- Desktop/CLI read/comment/state operations use the same command surface for both types; proxy
  records forward those operations to Azure DevOps and refresh the local cache afterward
- VM setup can target either a local issue number (`77`) or proxy identifier forms like
  `proxy:ado/77`, and both resolve through the same local issue record before invoking
  `setup_issue_vm.sh`
- ADO backlog create operations can include `parent_backlog_item_id`; when provided, the create
  patch appends a `System.LinkTypes.Hierarchy-Reverse` relation in the same org/project context.
  The parent ID must be non-empty digits. If that validation fails, the request is rejected before
  contacting ADO; if ADO rejects the relation (for example parent is in a different project/team
  path), the create call returns a parent-linkage error from ADO.

Run it with:

```bash
agent-sandbox-admin
```

The CLI also exposes machine-readable and GUI-supporting surfaces:

```bash
agent-sandbox-cli list-vms --all --json
agent-sandbox-cli start-vm vm-14084
agent-sandbox-cli shutdown-vm vm-14084
agent-sandbox-cli create-local-issue --title "Fix failing restore" --body "Investigate restore timeout"
agent-sandbox-cli list-local-issues --json
agent-sandbox-cli directories list --parent root
agent-sandbox-cli directories show root
```

## Local Git Remotes

For the full issue-VM bring-up path in one command, use:

```bash
MSSQL_SA_PASSWORD='your-strong-password' ./scripts/setup_issue_vm.sh --python --dotnet --nuget --mssql --mssql-init-root /path/to/mssql-init --playwright --frontend-deps --frontend-install-root /path/to/repo/Src/Clients /path/to/issue.yaml /path/to/project-or-solution.slnx
```

`setup_issue_vm.sh` now resolves host-dependent paths in this order:

1. explicit `repo-source` argument (third positional argument)
2. `AGENT_SANDBOX_DEFAULT_REPO_SOURCE`
3. repo-relative fallback: the agent-sandbox repo root

When `session-id` is omitted, `setup_issue_vm.sh` now generates one automatically in the form
`issue-<issue-number>-<utc timestamp>-<pid>`. That keeps review branches distinct across multiple
agents or reruns for the same issue.

Toolchain bootstraps are now opt-in:

- `--python` runs `bootstrap_python_vm.sh` and creates a repo-local `.venv` from a host-built wheelhouse
- `--dotnet` runs `bootstrap_dotnet_vm.sh`
- `--nuget` runs `bootstrap_nuget_vm.sh` and host `prewarm_nuget_cache.sh` (requires `project-or-solution`)
- `--mssql` runs `bootstrap_mssql_vm.sh` and requires `MSSQL_SA_PASSWORD` in the environment
- `--mssql-init-root PATH` copies a host init bundle into `/var/opt/mssql/init` and executes `init.sql`
- `--playwright` runs `bootstrap_playwright_vm.sh`, which also provides `node`, `npm`, and `npx`
- `--frontend-deps` runs `bootstrap_frontend_deps_vm.sh`
- `--frontend-install-root PATH` selects one frontend install root and may be repeated
- `--frontend-host-checkout PATH` overrides the host checkout used for frontend snapshots when `repo-source` is remote

When `--frontend-deps` is enabled, each install root must live under the selected host checkout and
already contain `package.json` plus `node_modules`. For local repo sources, `setup_issue_vm.sh`
uses the resolved local repo checkout by default. For remote repo sources, pass
`--frontend-host-checkout` so the helper can snapshot dependencies from a host checkout and
restore them into the matching relative paths inside the guest clone.

The guest clone URL is also derived from configurable Git-root settings:

- `AGENT_SANDBOX_GIT_ROOT` selects the host bare-remote root. Set it in the `root`
  issue setup config or as a host environment variable, and make sure it already exists
  before guest clone starts.
- `AGENT_SANDBOX_GIT_SSH_ALIAS` controls the SSH alias used in the guest clone URL
  (default: `agent-sandbox-git-host`).

That now also clones the mirrored repo into the guest user's home directory using
the host repo basename, for example `/home/agent/AcmePlatform`. It also writes a short
`/home/agent/AGENTS.md` note, copies the issue YAML into the repo root as
`AGENT_TASK.yaml`, configures a repo-local `git push-agent` alias for the safe
session-namespaced push pattern, and starts a background agent run inside the guest by
default. The guest-side agent log, PID, and state files live under `/home/agent/.agent-sandbox/`.
Use `--no-start-agent` with `agent-sandbox-cli create-issue-vm` or `--dry-run` with
`setup_issue_vm.sh` if you want the VM prepared without starting the agent process. The
helper still accepts a raw issue number as the first argument for the older workflow, but
YAML is the preferred input for long-running agent sessions.
When `--python` is enabled, the guest repo also gets a repo-local `.venv`. Activate it with
`. .venv/bin/activate`, then run `python -m pytest -q` and `python -m ruff check` from inside
the guest clone.
After the initial provider command run exits, the helper now performs one
provider-specific follow-up verification pass to verify checklist completion
(tests, docs, and PR markdown note). Override that follow-up prompt text with
`AGENT_POST_EXEC_VERIFICATION_PROMPT` in the `root` issue setup config or as a host
environment variable.

For operator visibility from the host, use:

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

`list-vms` shows running VMs together with live guest agent status by default.
Pass `--all` to include inactive VMs as well. `logs` prints the requested
number of recent lines first, then follows the guest agent log live over SSH.

Use the included bare-repo helper when you want VMs to push code changes back to the host over the private network:

Set the `root` issue setup config values for `AGENT_SANDBOX_GIT_ROOT`,
`AGENT_SANDBOX_GIT_HOST`, `AGENT_SANDBOX_GIT_USER`, and `AGENT_SANDBOX_GIT_SSH_ALIAS`
through the admin UI or `agent-sandbox-cli directories config-set`, then run:

```bash
./scripts/setup_issue_git_vm.sh vm-real 14084
```

If you want to mirror the default host repo into an issue-numbered bare remote first, use:

```bash
./scripts/mirror_issue_repo.sh 14084
```

You can choose freshness behavior explicitly:

```bash
# Local working tree source (default: no upstream fetch)
./scripts/mirror_issue_repo.sh --sync-remote --remote-name origin 14084 /path/to/local/repo

# Remote URL source (always refreshed in runtime/repo-cache before mirroring)
./scripts/mirror_issue_repo.sh 14084 https://example.com/org/repo.git

# Pinned mirror behavior
./scripts/mirror_issue_repo.sh --branch main 14084 /path/to/local/repo
./scripts/mirror_issue_repo.sh --refspec refs/heads/main:refs/heads/main 14084 /path/to/local/repo
```

When `--sync-remote` is active for a local source, the helper fetches
`git fetch --prune <remote-name>` and checks whether the local default branch is behind the remote
default branch. If so, it stops with a divergence message unless `--force-local` is supplied.
Every mirror run logs the source mode (local vs remote), selected base branch, and source commit
SHA for auditability.

`setup_issue_git_vm.sh` is the shortest path: it bootstraps the guest Git key and creates or
refreshes the issue-numbered host mirror in one command. The lower-level
`bootstrap_git_vm.sh` helper installs a dedicated guest SSH key and a restricted host
`authorized_keys` entry so guests can push Git branches without needing your host password or
general shell access. Issue mirrors also carry a `post-receive` hook so host-side automation can
promote pushed `agents/...` branches into the trusted clone, push the review branch to `origin`,
and create the upstream PR automatically.
Issue mirrors also remember their source settings, so later guest `git fetch` / `git pull`
operations can trigger a host-side mirror refresh before the read is served.

For fixed PR boilerplate, set `AGENT_SANDBOX_PR_FIXED_BODY_FILE` as a host environment variable
pointing at a markdown file. For per-directory boilerplate, use the root `pr_template` directory config
domain and migrate the legacy repo-level `config/pr-template.md` with
`agent-sandbox-cli migrate-pr-template-to-root --config config/gateway.yaml`.
Automation assembles PR descriptions in this order: fixed boilerplate, `## Agent Summary`
heading, then the git note content from `refs/notes/agent-pr`.

Guest issue VMs install a `git agent-pr-note` helper so agents can maintain that note as an
additive branch summary instead of overwriting it with per-commit deltas. `git push-agent`
refreshes the cached summary onto the current `HEAD` and pushes `refs/notes/agent-pr`
automatically.

Example assembled PR body:

```markdown
## Release Notes
- reviewer-owned context

---

## Agent Summary

- agent-authored markdown summary
```

If no note is available, automation still proceeds with fixed boilerplate only (when configured) or
falls back to default PR body behavior. Treat note text as untrusted markdown content only, and do
not interpolate it into shell commands.

See [git-remotes.md](/home/jonathan/agent-sandbox/docs/git-remotes.md) for the review flow and branch naming pattern.
The host-side safe promotion flow is:

```bash
./scripts/promote_agent_branch.sh /path/to/trusted-clone sample-repo agents/<session-id>/work-<agent-suffix>
./scripts/open_agent_pr.sh --create-pr --base main /path/to/trusted-clone review/<session-id>/work-<agent-suffix>
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
    --runtime-dir runtime \
    --discord-channel 123456789012345678 \
    --discord-channel 234567890123456789
  ```

- Operator user allowlist is configured in the effective `root` policy config with
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

## Teams Adapter Configuration

- Teams access is host-side. Configure the gateway with:
  - `teams_backend: graph_api`
  - `teams_graph_access_token_env: <ENV_VAR_NAME>`
  - optional `teams_graph_api_base_url` when you need a non-default Graph endpoint
- The access token referenced by `teams_graph_access_token_env` must be able to read Microsoft
  Teams channel messages and, if you enable outbound status posts, create channel messages/replies
  for the teams/channels you plan to use.
- Session manifests opt into Teams access explicitly:

  ```json
  {
    "session_id": "teams-session",
    "allowed_tools": ["teams_get_messages", "teams_post_status"],
    "teams_channel_allowlist": ["<team-id>/<channel-id>"]
  }
  ```

- Channel targets use the literal `team-id/channel-id` format. The Teams adapter polls
  `GET /teams/{team-id}/channels/{channel-id}/messages` and stores both top-level messages and any
  expanded replies in `runtime/teams_messages.json`.
- The `teams_get_messages` gateway tool returns normalized envelopes with:
  - `cursor`
  - `message_id`
  - `team_id`
  - `channel_id`
  - `thread_id`
  - `author_id`
  - `content`
  - `created_at`
  - raw Graph payload preserved under `metadata.message`
- The `teams_post_status` gateway tool accepts:
  - `channel_target`
  - `message`
  - optional `thread_id` to reply into an existing channel thread
- `teams_post_status` writes via:
  - `POST /teams/{team-id}/channels/{channel-id}/messages` for top-level posts
  - `POST /teams/{team-id}/channels/{channel-id}/messages/{thread_id}/replies` for replies
- Outbound posts are audited and persisted in `runtime/teams_messages.json` with
  `metadata.source == "teams_graph_api_status"` and the originating `session_id`.
- Source filtering is strict: only envelopes tagged with `metadata.source == "teams_graph_api"`
  and originating from a session allowlisted `team-id/channel-id` are returned to that session.
- Guests never need direct Microsoft Graph credentials or internet access; Teams API access
  happens only in the host-side gateway process.

## Issue Tooling

- Use `list_issues` to browse issues in the current directory scope and avoid duplicating work.
- Use `get_issue` once you already know an issue number or canonical issue ID. It returns the
  current issue record directly, regardless of state, and is the preferred MCP path for agents to
  inspect rejected, approved, committed, or completed issues without depending on list filters.
- Use `create_ado_backlog_item` to create Azure DevOps backlog work items through the host-side
  provider adapter (no guest PAT required).
- `create_ado_backlog_item` request fields:
  - required: `type`, `project`, `iteration`, `title`
  - optional: `description`, `acceptance_criteria`, `tags`, `assigned_user`,
    `parent_backlog_item_id`
- Session allowlisting is required: include `create_ado_backlog_item` in the session manifest
  `allowed_tools`, or the call is denied by policy.

### Minimal MCP client example (request/response)

Request:

```json
{
  "method": "tools/call",
  "params": {
    "name": "create_ado_backlog_item",
    "arguments": {
      "session_id": "issue-14084-session",
      "type": "task",
      "project": "Example.Project",
      "iteration": "Example.Project\\Sprint 12",
      "title": "Add retry guard for relay timeout"
    }
  }
}
```

Response:

```json
{
  "result": {
    "created_item_id": "14382",
    "url": "https://dev.azure.com/example-org/Example.Project/_workitems/edit/14382",
    "provider": "azure_devops",
    "project": "Example.Project",
    "type": "task",
    "iteration": "Example.Project\\Sprint 12",
    "title": "Add retry guard for relay timeout",
    "description": null,
    "acceptance_criteria": null,
    "tags": [],
    "assigned_user": null,
    "parent_backlog_item_id": null
  }
}
```

## Agent Inspection Tooling

- `get_agent_status` and `get_agent_logs` are directory-scoped inspection tools.
- A session with either tool can inspect its own VM and any other VM visible in the same logical
  `directory_path`.
- Requests for VMs outside the caller's directory scope are rejected at the gateway boundary with a
  scope-specific error.

## Relay Environment Configuration

- `OPENAI_API_KEY` is required on the host for `agent-sandbox-openai-relay`.
- `AZURE_ARTIFACTS_PAT` is required on the host only when the NuGet relay must reach private
  Azure Artifacts feeds.
- Keep both values in host-only shell environment or systemd env files such as:
  - `config/openai-relay.env`
  - `config/nuget-relay.env`
- Do not place either value in guest images, cloud-init, session manifests, or guest shell
  profiles.

## PR Feedback Heartbeat and Reply Loop

Use this when you want the guest to wake only for new review feedback and post threaded replies safely.

### Setup steps

1. Include PR feedback tools/stream metadata in the session manifest (same `session_id` the guest uses).
2. Bootstrap a provider runtime in the guest (`./scripts/bootstrap_codex_vm.sh <vm-name>` or `./scripts/bootstrap_copilot_vm.sh <vm-name>`).
3. Run the host heartbeat worker service:
   `sudo systemctl start agent-sandbox-host-heartbeat.service`.
   Common lifecycle commands:
   - start: `sudo systemctl start agent-sandbox-host-heartbeat.service`
   - stop: `sudo systemctl stop agent-sandbox-host-heartbeat.service`
   - status: `sudo systemctl status agent-sandbox-host-heartbeat.service`
4. Use MCP calls from the agent in this order:
   `has_pending_feedback` → `list_unread_feedback` → `post_pr_feedback_reply` (with an idempotency key).
   For full thread history (including already-consumed items), call `list_all_feedback`.

### Expected timing behavior

- Host heartbeat cadence is controlled by the host heartbeat worker config:
  `host_heartbeat_directory_paths`, `host_heartbeat_poll_interval_seconds`, `host_heartbeat_jitter_seconds`,
  and `host_heartbeat_cooldown_seconds`.
- Feedback polling happens once per configured directory on the host and updates the shared host feedback store.
- Guests consume pending feedback via MCP tools by default.
- Provider bootstrap scripts default `ENABLE_GUEST_HEARTBEAT=0`, which keeps
  `agent-heartbeat.timer` disabled for new guests.
- Set `ENABLE_GUEST_HEARTBEAT=1` during bootstrap to install and enable an optional guest-local resume timer.
  The timer uses the same provider adapter path as the host resume flow and accepts
  `GUEST_HEARTBEAT_PROMPT` to override the resume prompt per agent.
- Migration note for existing VMs: disable any previously enabled guest timer if you want host-only wakeups:
- Agent enable/disable control is now mirrored in host state at `runtime/host-agent-control-state.json`,
  so the host heartbeat worker can continue ignoring disabled agents even while their VMs are shut down.
- Guests consume pending feedback via MCP tools by default.
- Provider bootstrap scripts default `ENABLE_GUEST_HEARTBEAT=0`, which keeps
  `agent-heartbeat.timer` disabled for new guests.
- Set `ENABLE_GUEST_HEARTBEAT=1` during bootstrap to install and enable an optional guest-local resume timer.
  The timer uses the same provider adapter path as the host resume flow and accepts
  `GUEST_HEARTBEAT_PROMPT` to override the resume prompt per agent.
- Migration note for existing VMs: disable any previously enabled guest timer if you want host-only wakeups:
  `systemctl --user disable --now agent-heartbeat.timer || true`.

### Scheduled wake examples (one-time + cron)

- One-time wake via MCP tool (`schedule_resume`):

  ```json
  {
    "session_id": "issue-11-session",
    "resume_at": "2026-04-01T10:30:00Z",
    "prompt": "Check unread feedback and continue implementation.",
    "resume_mode": "same_context",
    "source": "mcp_schedule_resume"
  }
  ```

- Recurring cron wake via host CLI:

  ```bash
  agent-sandbox-cli wake-schedules create \
    --runtime-dir runtime \
    --session-id issue-11-session \
    --cron "*/15 * * * *" \
    --prompt "Periodic wake: check unread feedback and keep the branch moving." \
    --mode new_context \
    --source admin_cli
  ```

- MCP-sourced schedules enforce `resume_mode=same_context`; `new_context` is valid for admin/CLI-created schedules.

### Troubleshooting

- **Run consolidated status first:** start with a host snapshot before debugging individual services.
  - Human-readable: `agent-sandbox-cli status --runtime-dir runtime`
  - Machine-readable: `agent-sandbox-cli status --runtime-dir runtime --json`
  - The command summarizes config loading, runtime path permissions, key systemd units, gateway/relay reachability, and session expiration health.
- **Host heartbeat lock contention:** worker exits because another instance is running.
  - Check the configured lock path (`host_heartbeat_lock_path`) and active systemd processes before restarting.
- **Provider API failures while replying:** gateway tool errors from GitHub/Azure DevOps calls.
  - Validate host tokens (`GITHUB_TOKEN`/configured env, `AZURE_DEVOPS_PR_PAT`/configured env).
  - Retry with the **same idempotency key** so `post_pr_feedback_reply` deduplicates safely.

### Audit/log locations and healthy signals

- Host heartbeat service logs: `journalctl -u agent-sandbox-host-heartbeat.service`.
- Host runtime: `runtime/pr-feedback-store.json`, `runtime/pr-reply-idempotency.json`,
  `runtime/host-agent-control-state.json`, `runtime/audit.jsonl`.
- Healthy pattern:
  - host worker alternates between quiet polls and discovered feedback events,
  - audit shows allowed calls for `has_pending_feedback`, `list_unread_feedback`, `list_all_feedback`, and `post_pr_feedback_reply`,
  - idempotent retry returns `status=deduplicated` instead of creating duplicate provider comments.

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
agent-sandbox-cli directories config-set \
  --runtime-dir runtime \
  --domain policy \
  root \
  '{"email_mailbox_allowlist":["ops-gmail","exec-graph","finance-graph"],"email_folder_allowlist_by_mailbox":{"ops-gmail":["inbox","drafts"],"exec-graph":["inbox","drafts"],"finance-graph":["inbox","drafts"]}}'

agent-sandbox-cli create-session real-session --runtime-dir runtime
```

## Project Layout

- `src/agent_sandbox/`: gateway, relay, policy, storage, and CLI code
- `scripts/`: libvirt and firewall automation
- `config/`: sample configuration and cloud-init template
- `systemd/`: service templates for the host gateway, relay, and firewall policy
- `docs/`: operating notes and next steps
- `docs/architecture.md`: design rationale, including when logic belongs in `scripts/*.sh` versus
  `src/agent_sandbox/*.py`

## Current Limitations

- Email supports provider-backed Gmail and Microsoft Graph adapters; the local JSON store is an optional fallback for development/testing only.
- Discord has both `stub` and `bot_api` backends; production deployments should enforce strict
  channel and operator allowlists.
- The gateway is ready for integration adapters, not production credentials by default.
- Network/firewall scripts assume Ubuntu 24.04 and nftables-backed libvirt hosts.
- VM disk and seed artifacts default to `/var/lib/libvirt/images/agent-sandbox`, so VM lifecycle scripts should be run with `sudo` unless you deliberately provision an alternate libvirt-accessible storage path.
- Guests can reach only the specific host TCP ports you allow in `ALLOWED_TCP_PORTS`; the defaults are `22` for host SSH/Git, `8000` for MCP, `11434` for the host-side OpenAI relay or another OpenAI-compatible LLM endpoint, and `11666` for the host-side NuGet relay.
