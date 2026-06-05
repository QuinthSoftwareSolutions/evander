# Agent Sandbox

`agent-sandbox` is a host-side control plane for running autonomous AI agents inside disposable libvirt VMs while exposing only narrow, audited capabilities through MCP.

## What This Scaffold Includes

- A Python MCP gateway with policy-gated Discord and email tool surfaces
- A host-side OpenAI relay that keeps the API key on the host and exposes an OpenAI-compatible endpoint to guests
- A provider-neutral model usage store and query node for persisted, privacy-preserving usage records
- An optional host-side DIDWW SIP/OpenAI Realtime bridge for projected phone calls
- An optional Teams meeting media bridge with Azure Speech or OpenAI Realtime speech modes
- A host-side NuGet relay that proxies nuget.org and private Azure Artifacts feeds for guests
- A host-side npm audit facade that sends npm audit POSTs to public npm while keeping package metadata and tarballs on Nexus
- A host-side Azure DevOps PR feedback/completion worker plus workflow ADO work-item intake nodes
- A host-side workflow orchestrator that polls provider triggers, evaluates gates, and persists durable queue events for downstream workers/hooks
- A host-side Teams ingest worker that polls Microsoft Graph and feeds `trigger:teams:new-message`
  and `trigger:teams:direct-message` orchestration
- A host-side shared NuGet cache that can be mounted read-only into guests for faster cold restores
- Optional host-local Qdrant semantic store primitives for workflow-visible health, collection setup, and explicit-ID text point upsert/search/get/delete
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

  The default firewall policy allows host ports `22`, `8000`, `11434`, `11666`, `11777`, `8081`, and `3142` so VMs can use SSH for Git remotes, MCP on `8000`, the OpenAI relay on `11434`, the NuGet relay on `11666`, the session-scoped HTTP target relay on `11777` for approved external web endpoints such as APP demo environments, the Nexus package cache on `8081` for PyPI/npm package restores, and the apt-cacher-ng relay on `3142` for worker apt provisioning. The optional Qdrant semantic store binds to `127.0.0.1:6333` by default and is not exposed to guests.

6. Start the host-side services:

   ```bash
   export OPENAI_API_KEY=...
   export DISCORD_BOT_TOKEN=...
   agent-sandbox-gateway --runtime-dir runtime
   agent-sandbox-openai-relay --runtime-dir runtime
   ```

   Optional DIDWW/OpenAI Realtime phone calls require Asterisk with a DIDWW PJSIP
   trunk plus `agent-sandbox-didww-sip-bridge --runtime-dir runtime`. The bridge
   keeps SIP, RTP, and OpenAI credentials on the host and is only reached by the
   gateway through `phone_call_backend=openai_realtime_didww`.

   Optional Teams meeting voice sessions require the Windows-hosted bridge in
   `integrations/teams-meeting-media-bridge`. Set `BRIDGE_ADAPTER_MODE=Graph`
   and choose `BRIDGE_SPEECH_MODE=AzureSpeech` or `OpenAIRealtime`.

7. Download the base image and launch VMs from a libvirt-accessible storage path:

   ```bash
   sudo ./scripts/create_base_vm.sh
   sudo ./scripts/launch_agent_vm.sh vm-real real-session
   ```

   By default, the launch script injects your host `~/.ssh/id_ed25519.pub` key into the guest and exposes a host-side LLM base URL of `http://192.168.250.1:11434/v1`. Override with `SSH_PUBLIC_KEY_FILE=...`, `SSH_PRIVATE_KEY_PATH=...`, or `LLM_BASE_URL=...` if needed.
   It also creates a shared host NuGet cache under `/var/lib/libvirt/images/agent-sandbox/nuget-cache/global-packages` and mounts it read-only into the guest at `/opt/agent-sandbox/nuget-cache-ro` via `virtiofs`.
   New VMs default to a `20G` virtual disk; override with `DISK_SIZE_GB=...` before launch if needed.
   New VMs receive provider-neutral baseline config (`/etc/agent-sandbox/session.env` and `/home/agent/.agent/config.env`) plus a shell profile hook; provider-specific config is written later by `bootstrap_codex_vm.sh` or `bootstrap_copilot_vm.sh`. The `agent` user is the unprivileged runtime user; `agent-admin` is the host-control SSH user for privileged setup, resume, and root-owned log access.
   Host-side guest SSH commands cache guest host keys in `~/.ssh/agent-sandbox-known_hosts` keyed by VM name, so repeated `list-vms`, bootstrap, and `agent-sandbox-cli ssh` calls stay quiet while preserving normal SSH host-key warnings.
   To see all currently running VMs together with live guest agent status, run `agent-sandbox-cli list-vms`.

8. (Optional) Bootstrap a provider runtime inside the VM:

   ```bash
   ./scripts/bootstrap_codex_vm.sh vm-real
   ./scripts/bootstrap_copilot_vm.sh vm-real
   ```

   By default, orchestration uses provider-neutral agent run state under `/home/agent/.agent-sandbox` (`agent-*.state`, `agent-*.pid`, and runner helper files). Agent transcripts are written by the privileged host-control path to root-owned logs under `/var/log/agent-sandbox/agent-runs`, so the unprivileged `agent` user cannot read or edit prior transcripts.
   Legacy VMs that still rely on `agent` for sudo should be recreated or migrated; for one-off maintenance against an old VM, set `VM_SSH_USER=agent` explicitly.

   ### Codex provider bootstrap

   This copies the vendored Linux Codex binary from the host install into the guest user's `~/bin`, writes `~/.codex/config.toml` to use the host relay with bootstrap defaults (currently `model = "gpt-5.4"`, `model_reasoning_effort = "high"`, and `personality = "pragmatic"`), registers the host MCP gateway as `agent_sandbox`, copies the host Codex model cache when available so offline guests see the same model menu, prefers `LLM_BASE_URL` and otherwise falls back to `~/.agent/config.env`, ensures the placeholder API-key shell hook exists, and verifies `codex --version`.

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

   For generic guest-side Python tools that are not tied to a cloned repo checkout, use the lower-level package bootstrap instead:

   ```bash
   ./scripts/bootstrap_python_packages_vm.sh vm-real openai
   ```

   This builds a temporary host wheelhouse for the requested package specs, copies it into the guest, creates or reuses a guest tools virtualenv under `~/.agent-sandbox/python-tools-venv`, and installs those packages offline there.

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

   This copies a host-seeded Node runtime, Playwright packages, and browser cache into the guest, then configures `PLAYWRIGHT_MCP_EXECUTABLE_PATH` so `playwright-cli` uses the seeded Chromium binary instead of trying to install or launch system Chrome.

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
$EDITOR config/admin-web.env
$EDITOR config/firewall.env
sudo systemctl start agent-sandbox-admin-web.service
sudo systemctl start agent-sandbox-firewall.service
sudo systemctl start agent-sandbox-gateway.service
sudo systemctl start agent-sandbox-openai-relay.service
sudo systemctl start agent-sandbox-didww-sip-bridge.service
sudo systemctl start agent-sandbox-nuget-relay.service
sudo systemctl start agent-sandbox-npm-audit-facade.service
sudo systemctl start agent-sandbox-ado-worker.service
sudo systemctl start agent-sandbox-proxy-issue-sync.service
sudo systemctl start agent-sandbox-discord-wake.service
sudo systemctl start agent-sandbox-scheduled-wake.service
```

For fresh-host Ubuntu 24.04 deployment paths that wrap this same repo-native flow with Ansible,
including the dedicated container-worker VM playbook, see `ansible/README.md`.

When you expose `agent-sandbox-admin-web` beyond localhost, configure auth in `config/admin-web.env`
and put TLS plus ingress policy in front of the service.

## Admin Web Operations Dashboard

Admin Web opens to an operations dashboard by default after sign-in. The dashboard is an operator
overview backed by the same live APIs as the detail tabs: deployment/admin state, workers, agents,
workflow runs and graph activity, approvals, sessions, tool-call audit entries, capacity health,
and the workflow document/catalog. Empty or unsupported sources are shown as explicit
missing/unavailable states, not as sample data. Capacity health intentionally omits raw host paths
and secrets while still showing storage, per-directory lifecycle counts, worker telemetry, queue
age, and unread prompt pressure.

The product context panel also reads live release metadata from `/api/admin/release-info`. That
endpoint reports the installed package or `pyproject.toml` version when available, a deployed build
identity when one is configured, the maintained top section of `CHANGELOG.md`, and the latest prose
entry from `NEWS.md`. News entries are paged with the `news_index` query parameter so the dashboard
requests one blog entry at a time as operators move through older posts. The panel includes a
changelog download button for retrieving the full `CHANGELOG.md`. When any source is missing, the
panel surfaces explicit missing reasons instead of inferring or inventing release data.

For a task-oriented guide to every Admin UI section, see
[`docs/admin-ui-user-manual.md`](docs/admin-ui-user-manual.md).

Dashboard-tagged read requests use a short-lived backend JSON cache so repeated opens do not
re-run every systemd, VM inventory, workflow, approval, session, and audit read immediately. The
detail tabs keep their normal live reads, and the dashboard Refresh button bypasses the cache.

Existing hash deep links still route directly to the requested section. For example, `/#workflow`,
`/#sessions`, `/#approvals`, `/#tool-calls`, and `/#configuration` bypass the dashboard and open the
detail view. The Workflow tab remains a first-level navigation item for editing workflow graphs and
inspecting runtime activity.

## Admin Web Authentication

`agent-sandbox-admin-web` now supports three explicit auth modes:

- `none`: default when no auth mode is configured and `AGENT_SANDBOX_ADMIN_WEB_PASSWORD` is unset.
- `local_password`: current username/password flow with the existing signed local session cookie.
- `azure_sso`: Microsoft Entra browser sign-in using OIDC Authorization Code + PKCE, followed by the same local signed session cookie used by the rest of the admin API.

Backward compatibility is preserved:

- If `AGENT_SANDBOX_ADMIN_WEB_AUTH_MODE` is unset and `AGENT_SANDBOX_ADMIN_WEB_PASSWORD` is unset, the admin web stays in `none`.
- If `AGENT_SANDBOX_ADMIN_WEB_AUTH_MODE` is unset and `AGENT_SANDBOX_ADMIN_WEB_PASSWORD` is set, the admin web behaves as `local_password`.

Common auth env vars:

- `AGENT_SANDBOX_ADMIN_WEB_AUTH_MODE=none|local_password|azure_sso`
- `AGENT_SANDBOX_ADMIN_WEB_USERNAME` for `local_password` mode. Default: `admin`
- `AGENT_SANDBOX_ADMIN_WEB_PASSWORD` for `local_password` mode
- `AGENT_SANDBOX_ADMIN_WEB_SESSION_SECRET` for the signed local session cookie
- `AGENT_SANDBOX_ADMIN_WEB_SESSION_TTL_SECONDS` for the local session lifetime
- `AGENT_SANDBOX_ADMIN_WEB_COOKIE_SECURE=true|false`

Azure SSO env vars:

- `AGENT_SANDBOX_ADMIN_WEB_AZURE_TENANT_ID`
- `AGENT_SANDBOX_ADMIN_WEB_AZURE_CLIENT_ID`
- `AGENT_SANDBOX_ADMIN_WEB_AZURE_CLIENT_SECRET`
- `AGENT_SANDBOX_ADMIN_WEB_AZURE_REDIRECT_URI`
  or `AGENT_SANDBOX_ADMIN_WEB_PUBLIC_BASE_URL`, which is combined with `/api/auth/azure-sso/callback`
- `AGENT_SANDBOX_ADMIN_WEB_AZURE_AUTHORITY_BASE_URL`
  Default: `https://login.microsoftonline.com`
- `AGENT_SANDBOX_ADMIN_WEB_AZURE_ALLOWED_USERS`
  Optional first-run seed list of admin user principal names / email-style logins, for example
  `alice@example.com,bob@example.com`
- `AGENT_SANDBOX_ADMIN_WEB_AZURE_POST_LOGIN_REDIRECT`
  Default: `/`

Azure SSO is optional and disabled by default. To enable it:

1. Register a confidential Microsoft Entra web application.
2. Add the admin web callback URL as a redirect URI.
3. Ensure the app can request OpenID scopes: `openid profile email`.
4. Set the tenant you intend to trust with `AGENT_SANDBOX_ADMIN_WEB_AZURE_TENANT_ID`.
5. For the first startup, set `AGENT_SANDBOX_ADMIN_WEB_AZURE_ALLOWED_USERS` to the initial admin UPN/email-style identities.

Azure SSO users are then managed in `runtime/admin-web.db` through the Admin Web Users & Groups
tab. The env list is only read when the SQLite user table is empty, and imported users receive the
seeded Owner group. A legacy `runtime/admin-web-users.json` file is imported once when the SQLite
table is empty; legacy `admin` and `regular` roles are preserved for compatibility and mapped to
Owner and Operator group assignments. Removing a user from the store invalidates their existing
local admin session on the next authenticated request.

The admin web only trusts a sign-in when both checks pass:

- the ID token is issued for the configured tenant and client
- the human-managed login identifier exists in the runtime user store

Sign-out clears only the local admin session cookie. v1 does not force Microsoft global logout.

### Trigger orchestrator

The scaffold includes `agent-sandbox-trigger-orchestrator`, a host-side worker that:

- validates and loads trigger definitions from `config/trigger-orchestrator/config.json`
- polls provider-specific trigger implementations (`cron`, `ticket`, `discord`, `email`, `pull-request`, `git`)
- evaluates gate nodes and routes events through explicit edges
- persists discovered events into a durable queue (`runtime/trigger-orchestrator-events.json` by default)
- records per-trigger cursor/checkpoint metadata (`runtime/trigger-orchestrator-cursors.json` by default)
- also accepts `trigger:teams:new-message` and `trigger:teams:direct-message` events, which are
  sourced from host-side Teams ingest
  polling rather than from the wake workers

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
`azure_devops_organization_url`, `azure_devops_project`, and wake timing settings.

### Azure DevOps worker and work-item workflows

`agent-sandbox-ado-worker` now owns Azure DevOps PR feedback and completion polling only. For
foreground operation, run:

```bash
set -a; source secrets.env; set +a
agent-sandbox-cli poll-ado-backlog --log-level INFO
```

The ADO worker reads `azure_devops_organization_url` and `azure_devops_project` from the
effective provider config and uses `AZURE_DEVOPS_PR_PAT` or the configured
`azure_devops_pr_pat_env` when Azure DevOps PR polling is enabled. It no longer polls backlog work
items or requires `ADO_WORK_ITEMS_AZURE_DEVOPS_PAT` to run PR feedback/completion paths.

Azure DevOps work-item intake is workflow-owned. Use `trigger:ado:work-item-matching` to emit raw
work-item payloads on first match and when `System.ChangedDate` advances, or use
`query:ado:work-items` for an explicit query. Wire either output into
`effect:issue:upsert-ado-proxy` to create or refresh local proxy issues and
`runtime/ado-work-items/work-item-*.yaml`.
Use `trigger:ado:work-item-comment-created` when comments themselves should wake a workflow. It
uses workflow-configured work-item and comment filters, keeps its own cursor in
`runtime/trigger-cursors-ado-work-item-comments.json`, and baselines existing comments unless
`include_existing` is explicitly enabled.

The dedicated worker entry point is also available as `agent-sandbox-ado-worker` for systemd.
`agent-sandbox-proxy-issue-sync` is the companion freshness worker for local Azure DevOps proxy
issues. It runs every five minutes by default, batches remote summary reads, skips comments, and
refreshes `runtime/local-issues.json` so ticket triggers and UI screens continue to read local
state.
The ADO worker does not create sessions, create VMs, run issue setup scripts, bootstrap guest
toolchains, or ingest Azure DevOps work items. Directory-scoped `ado_worker` config still controls
PR automation flags and can provide `ado_repo_source` for workflows that choose to bind it into
`effect:issue:upsert-ado-proxy.repo_source`.

## Admin Web And CLI

Use `agent-sandbox-admin-web` for the operator GUI and `agent-sandbox-cli` for terminal
automation. Admin Web now includes an Approvals tab for reviewing queued approval requests in the
browser, while the CLI remains available for terminal-based review. Issue handling is a single
local abstraction in both Admin Web and CLI flows:

- local-only issues are created and managed in `runtime/local-issues.json`
- ADO-backed issues are stored as local **proxy issues** (`source: proxy_azure_devops`) with
  remote identity under `metadata.remote` plus a cached snapshot under `metadata.proxy_cache`
- Admin Web/CLI read/comment/state operations use the same command surface for both types; proxy
  records forward those operations to Azure DevOps and refresh the local cache afterward
- VM setup can target either a local issue number (`77`) or proxy identifier forms like
  `proxy:ado/77`, and both resolve through the same local issue record before invoking the
  shared issue-VM create flow
- ADO backlog create operations can include `parent_backlog_item_id`; when provided, the create
  patch appends a `System.LinkTypes.Hierarchy-Reverse` relation in the same org/project context.
  The parent ID must be non-empty digits. If that validation fails, the request is rejected before
  contacting ADO; if ADO rejects the relation (for example parent is in a different project/team
  path), the create call returns a parent-linkage error from ADO.

The CLI also exposes machine-readable and GUI-supporting surfaces:

```bash
agent-sandbox-cli list-vms --all --json
agent-sandbox-cli start-vm vm-14084
agent-sandbox-cli shutdown-vm vm-14084
agent-sandbox-cli create-local-issue --title "Fix failing restore" --body "Investigate restore timeout"
agent-sandbox-cli list-local-issues --json
agent-sandbox-cli list-approvals
agent-sandbox-cli show-approval <request_id>
agent-sandbox-cli approve --reason "..." <request_id> <approver>
agent-sandbox-cli approve --deny --reason "..." <request_id> <approver>
agent-sandbox-cli directories list --parent root
agent-sandbox-cli directories show root
```

### Workflow event ingress

Admin Web exposes a machine-oriented ingress for workflows that start from
`trigger:event`. A signed-in Admin Web user can mint a short-lived bearer token, and trusted
server-side publishers can use a configured machine token. The submitted event type is always
namespaced with `external.` before it is stored, so a request for `deploy_requested` is consumed
by a trigger configured for `external.deploy_requested`.

```bash
TOKEN="$(curl -s -X POST -b cookies.txt http://127.0.0.1:8100/api/workflow-events/token \
  | jq -r .access_token)"

curl -X POST http://127.0.0.1:8100/api/workflow-events \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
    "event_type": "deploy_requested",
    "payload": {"target": "demo"},
    "correlation_key": "deploy:demo",
    "idempotency_key": "request-1"
  }'
```

For server-to-server publishers, set `AGENT_SANDBOX_WORKFLOW_EVENT_MACHINE_TOKENS` on Admin Web
to one or more comma-separated `source-name:token` entries. The token value is used in the same
`Authorization: Bearer ...` header, and the matching source name is recorded on
machine-authenticated events. Token values must not contain commas or colons.

Workflow-specific payloads are intentionally not schema-validated by the HTTP API beyond
requiring a JSON object. Downstream nodes read custom fields from the trigger output payload, for
example `${{trigger-external-deploy.payload.target}}`.

### Signed Workflow Webhooks

For public inbound workflows, configure `trigger:http:webhook` and POST to
`/api/workflow-webhooks/{slug}`. The request body must be a JSON object, and the signature header
must be `X-Agent-Sandbox-Signature-256: sha256=<hex>`, where the HMAC-SHA256 is computed over the
exact raw request body bytes with the configured secret.

The webhook trigger stores the durable event as `external.webhook.<slug>` by default, exposes the
webhook envelope downstream, and leaves payload shaping to gates such as `gate:filter`,
`gate:map`, `gate:deserialize`, and `gate:dedupe`.

```bash
WEBHOOK_BODY='{"target":"demo"}'
WEBHOOK_SECRET='replace-with-your-secret'
SIG="$(
  printf '%s' "$WEBHOOK_BODY" |
    openssl dgst -sha256 -hmac "$WEBHOOK_SECRET" -hex |
    awk '{print $2}'
)"

curl -X POST http://127.0.0.1:8100/api/workflow-webhooks/deploy \
  -H "Content-Type: application/json" \
  -H "X-Agent-Sandbox-Signature-256: sha256=${SIG}" \
  -d "$WEBHOOK_BODY"
```

```python
import hashlib
import hmac
import os

body = b'{"target":"demo"}'
secret = os.environ["WEBHOOK_SECRET"].encode()
signature = hmac.new(secret, body, hashlib.sha256).hexdigest()
print(signature)
```

## Local Git Remotes

Issue VM creation is now separate from repo setup. Create the VM first:

```bash
MSSQL_SA_PASSWORD='your-strong-password' ./.venv/bin/agent-sandbox-cli create-issue-vm 14084 --dotnet --nuget /path/to/project-or-solution.slnx --mssql --mssql-init-root /path/to/mssql-init --playwright
```

Then clone the repo and run any repo-specific bootstraps you need:

```bash
./.venv/bin/agent-sandbox-cli clone-repo-into-vm vm-14084 --repo-source /path/to/local/repo
./.venv/bin/agent-sandbox-cli bootstrap-python-in-vm vm-14084 --host-checkout /path/to/local/repo --target-dir /home/agent/$(basename /path/to/local/repo)
./.venv/bin/agent-sandbox-cli bootstrap-frontend-in-vm vm-14084 --host-checkout /path/to/local/repo --install-root /path/to/local/repo/Src/Clients
```

When `session-id` is omitted, `create-issue-vm` generates one automatically in the form
`issue-<issue-number>-<utc timestamp>-<pid>`. That keeps review branches distinct across multiple
agents or reruns for the same issue.

Toolchain bootstraps are opt-in and split by concern:

- `create-issue-vm --dotnet` runs `bootstrap_dotnet_vm.sh`
- `create-issue-vm --nuget PATH` runs `bootstrap_nuget_vm.sh` and host `prewarm_nuget_cache.sh`
- `create-issue-vm --mssql` runs `bootstrap_mssql_vm.sh` and requires `MSSQL_SA_PASSWORD`
- `create-issue-vm --playwright` runs `bootstrap_playwright_vm.sh`
- `bootstrap-python-in-vm` runs `bootstrap_python_vm.sh` against a cloned repo
- `bootstrap-frontend-in-vm` runs `bootstrap_frontend_deps_vm.sh` against a cloned repo

For frontend bootstrapping, each install root must live under the selected host checkout and
already contain `package.json` plus `node_modules`.

The guest clone URL is also derived from configurable Git-root settings:

- `AGENT_SANDBOX_GIT_ROOT` selects the host bare-remote root. Set it in the `root`
  issue setup config or as a host environment variable, and make sure it already exists
  before guest clone starts.
- `AGENT_SANDBOX_GIT_SSH_ALIAS` controls the SSH alias used in the guest clone URL
  (default: `agent-sandbox-git-host`).

That now also clones the mirrored repo into the guest user's home directory using
the host repo basename, for example `/home/agent/AcmePlatform`. It also writes a short
`/home/agent/AGENTS.md` note, copies the issue YAML into the repo root as
`AGENT_TASK.yaml`, creates a local `work` branch from the configured base branch, configures
repo-local `git sync-base` and `git push-agent` aliases, and starts a background agent run
inside the guest by default. `sync-base` merges the latest configured base branch via `origin`,
while `push-agent` verifies the destination and base branches before using the safe
session-namespaced push pattern. The guest-side PID and state files live under
`/home/agent/.agent-sandbox/`; root-owned transcript logs live under `/var/log/agent-sandbox/agent-runs/`.
VM and container agents share `/artifacts` as the workflow-readable artifacts
folder. Workflow query nodes can read UTF-8 text files from this folder by
session id and relative path. Artifact workflow effects can stage files from
`/home/agent` into this folder and publish the folder as an immutable keyed
`tar.gz` archive in the host runtime artifact store.
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
# Local working tree source (default: upstream fetch + divergence check)
./scripts/mirror_issue_repo.sh --remote-name origin 14084 /path/to/local/repo

# Opt out of upstream sync for local/offline-only flows
./scripts/mirror_issue_repo.sh --no-sync-remote 14084 /path/to/local/repo

# Remote URL source (always refreshed in runtime/repo-cache before mirroring)
./scripts/mirror_issue_repo.sh 14084 https://example.com/org/repo.git

# Pinned mirror behavior
./scripts/mirror_issue_repo.sh --branch main 14084 /path/to/local/repo
./scripts/mirror_issue_repo.sh --refspec refs/heads/main:refs/heads/main 14084 /path/to/local/repo
```

By default (or when `--sync-remote` is set) for a local source, the helper fetches
`git fetch --prune <remote-name>` and checks whether the local default branch is behind the remote
default branch. If so, it stops with a divergence message unless `--force-local` is supplied. Use
`--no-sync-remote` to keep a strictly local/offline mirror update.
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
heading, then the git note content from the configured PR notes ref. New checkouts use
`refs/notes/agent-pr/<session-hash>`, where `<session-hash>` is the first 12 hex characters of
`sha256(session_id)`; older checkouts can still use `refs/notes/agent-pr`.

Guest issue VMs install `git agent-pr-note`, `git sync-base`, and `git push-agent` helpers.
Agents work on a local `work` branch even when the base branch is `main`, so `git merge main`
does not accidentally merge the branch into itself. `git sync-base` fetches and merges the
configured base branch through the guest-visible mirror. `git push-agent` fetches the configured
destination and base branches before pushing, refuses when either has commits missing from `HEAD`,
refreshes the cached summary onto the current `HEAD`, and pushes the configured PR notes ref
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

See [git-remotes.md](docs/git-remotes.md) for the review flow and branch naming pattern.
The host-side safe promotion flow is:

```bash
./scripts/promote_agent_branch.sh /path/to/trusted-clone sample-repo agents/<session-id>/work-<agent-suffix>
./scripts/open_agent_pr.sh --create-pr --base main /path/to/trusted-clone review/<session-id>/work-<agent-suffix>
```

That keeps the VM-facing bare remote as a quarantine intake only; reviewed changes reach `origin`
only from a normal trusted clone on the host.
See [live-smoke-tests.md](docs/live-smoke-tests.md) for the live Discord and provider-backed email verification workflow.

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
  - For `discord_post_status` and Discord `add_message_reaction`, only session allowlisted
    `channel_id` values are permitted; operator user allowlists do not bypass destination checks.
  - Message reads do not apply automatic read reactions. Agents must call `add_message_reaction`
    when a reaction is intended.
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

- Teams access is host-side. The preferred operator flow is to sign in on the host with the CLI
  device code flow and keep the delegated credential on the host only.
- Bind each directory subtree to the intended Teams identity through per-directory Teams identity
  binding in directory-scoped provider config. Sessions below that subtree inherit the binding, so
  different teams can use different host-managed identities without guest-side secrets.
- Preferred delegated configuration:
  - `teams_backend: delegated_graph_api`
  - `teams_delegated_identity_id: <DIRECTORY_BOUND_IDENTITY>`
  - `teams_delegated_client_id: <ENTRA_APP_CLIENT_ID>`
  - optional `teams_delegated_tenant_id`, `teams_delegated_scopes`,
    `teams_delegated_authority_base_url`, and `teams_delegated_credential_store_path`
  - default delegated scopes now include channel read/send plus `Chat.ReadWrite` for group chats
  - Teams direct-message sender mail/UPN resolution and outbound mention lookup may require adding
    a Graph user-read scope such as `User.ReadBasic.All` or `User.Read`
- The Entra app registration used for `teams_delegated_client_id` must allow public client /
  device-code flows. If the token poll fails with `AADSTS7000218`, the app is configured as a
  confidential client and needs to be updated or replaced.
- Start delegated auth on the host with:
  - `agent-sandbox-cli teams-auth login --directory <directory-path>`
- Inspect or clear delegated auth on the host with:
  - `agent-sandbox-cli teams-auth status --directory <directory-path>`
  - `agent-sandbox-cli teams-auth logout --directory <directory-path>`
  - `agent-sandbox-cli teams-auth list`
- If you add or widen `teams_delegated_scopes`, run `agent-sandbox-cli teams-auth login --directory <directory-path>`
  again so the stored delegated credential is reissued with the new scope set.
- The gateway still supports the legacy static env-token mode for compatibility. If you use it,
  `teams_graph_access_token_env` names the host environment variable that holds the token.
- Configure the gateway with:
  - `teams_backend: graph_api`
  - `teams_graph_access_token_env: <ENV_VAR_NAME>`
  - optional `teams_graph_api_base_url` when you need a non-default Graph endpoint
- The access token referenced by `teams_graph_access_token_env` must be able to read Microsoft
  Teams channel and chat messages and, if you enable outbound status posts or explicit reactions,
  create channel/chat messages, replies, and reactions for the teams targets you plan to use.
- `trigger:teams:new-message` message arrival is driven by host-side Teams ingest polling on the
  host, not by the wake workers. Discord inbox wakeups and scheduled resumes are handled by
  `agent-sandbox-discord-wake` and `agent-sandbox-scheduled-wake`.
- `trigger:teams:new-message` emits one event per new allowlisted Teams message discovered
  during a poll. Its payload includes the triggering Teams `channel_target`, cursor, message id,
  target type, and a normalized `message` object for the corresponding message, not just the
  latest matching message in the batch. Agents that need surrounding context should call
  `teams_get_messages` with that `channel_target`.
- `trigger:teams:direct-message` emits one event per new one-on-one Teams direct message sent to
  the configured Teams identity by an explicitly allowlisted Graph sender user id, UPN, or mail
  address. It reuses the same host-side Teams ingest worker and emits `channel_target` as the
  resolved `chat:<chat-id>`.
- `trigger:teams:new-message` defines `targets`, where each target can optionally narrow wakes
  with its own `message_filter`.
- The initial supported filter shape is content substring matching:
  - `{"target":"team-id/channel-id"}`
  - `{"target":"chat:<chat-id>","message_filter":{"content":{"contains":"@Truman Bot"}}}`
  - optional `case_sensitive: true|false` (default `false`)
- `trigger:communication:new-message` provides the provider-neutral inbound chat path for
  directory-scoped communication targets. It requires explicit `communication_target_ids`, defaults
  `directory_path` to `root`, supports optional `author_ids`, `content_contains`, and
  `case_sensitive`, and listens only to read-capable Teams channel/chat or Discord channel targets.
  It reuses the host-owned Teams and Discord inbox stores and does not expose communication target
  auth profile references in trigger outputs.
- Direct-message triggers use a separate sender allowlist:
  - `{"type":"trigger:teams:direct-message","sender_user_ids":["<graph-user-id-or-email>"]}`
  - optional `message_filter` with the same content or mention filter shape
- Session manifests opt into Teams access explicitly:

  ```json
  {
    "session_id": "teams-session",
    "allowed_tools": ["teams_get_messages", "teams_post_status", "add_message_reaction"],
    "teams_channel_allowlist": ["<team-id>/<channel-id>"],
    "teams_chat_allowlist": ["chat:<chat-id>"]
  }
  ```

- Channel targets use the literal `team-id/channel-id` format. Chat targets use the literal
  `chat:<chat-id>` format.
- The Teams adapter polls:
  - `GET /teams/{team-id}/channels/{channel-id}/messages` for channel threads
  - `GET /chats/{chat-id}/messages` for group chats
  - `GET /me/chats` plus `GET /chats/{chat-id}/messages` for allowlisted direct-message triggers
  It stores both top-level messages and any expanded replies in `runtime/teams_messages.db`.
  Existing `runtime/teams_messages.json` stores are imported on first use.
- `teams_get_messages` requires `channel_target` plus optional `since_cursor`, `before_cursor`,
  and `limit` arguments. Cursors can be full returned cursor values or bare message ids, which the
  gateway scopes to the required target. Cursorless reads are bounded recent reads for that target:
  the gateway defaults the call to `teams_poll_limit`, and the Graph adapter stops after
  `teams_history_page_cap` pages or `teams_history_deadline_seconds`, whichever comes first.
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
  - optional `mentions` list of Teams user refs (`user:<graph-user-id-or-upn>` or bare Graph user
    ID, UPN, or mail address) for channel posts and channel thread replies. Plain `@name` text in
    `message` does not notify users.
- The `add_message_reaction` gateway tool accepts:
  - `provider`: `teams` or `discord`
  - `message_id`
  - `emoji`
    - Teams accepts Unicode emoji values directly.
    - Common ASCII aliases are normalized before Microsoft Graph: `eyes` -> `👀`,
      `like` / `thumbsup` / `thumbs_up` / `thumbs-up` -> `👍`, and `heart` / `love` -> `❤️`.
    - Unsupported ASCII aliases are rejected locally before any Graph request.
  - `channel_target` for Teams, or `channel_id` for Discord
  It is explicit only; `teams_get_messages` and Discord unread reads do not automatically react.
- When the target is a chat, the adapter uses the `chat:<chat-id>` identifier instead of a
  team/channel pair.
- `teams_post_status` writes via:
  - `POST /teams/{team-id}/channels/{channel-id}/messages` for top-level posts
  - `POST /teams/{team-id}/channels/{channel-id}/messages/{thread_id}/replies` for replies
  - `POST /chats/{chat-id}/messages` for top-level chat posts
  - `POST /chats/{chat-id}/messages/{message-id}/replies` for chat replies
- Outbound posts are audited and persisted in `runtime/teams_messages.db` with
  `metadata.source == "teams_graph_api_status"` and the originating `session_id`.
- Source filtering is strict: only envelopes tagged with `metadata.source == "teams_graph_api"`
  and originating from a session allowlisted `team-id/channel-id` are returned to that session.
- Guests never need direct Microsoft Graph credentials or internet access; Teams API access
  happens only in the host-side gateway process.

## Issue Tooling

- Use `list_issues` to browse issues in the current directory scope and avoid duplicating work.
- Pass `status=["Rejected"]` to `list_issues` when duplicate checks need rejected local issues;
  this virtual status returns closed local issues that expose a non-empty `rejection_reason`.
- Use `get_issue` once you already know an issue number or canonical issue ID. It returns the
  current issue record directly, regardless of state, and is the preferred MCP path for agents to
  inspect rejected, approved, committed, or completed issues without depending on list filters.
- Use `create_ado_backlog_item` to create Azure DevOps backlog work items through the host-side
  provider adapter (no guest PAT required).
- Use `post_ado_backlog_comment` to add an Azure DevOps backlog comment, including staged image
  artifacts when outbound communication file send is enabled for the session.
- Use `update_ado_backlog_text` to update the work item description or acceptance criteria and
  append staged artifact links/images to those fields.
- Use `update_ado_backlog_tags` to merge Azure DevOps backlog item tags, such as adding
  `auto-dev-approved` after an operator review.
- Use `query_ado_backlog_items` to list/filter Azure DevOps backlog work items.
- Use `get_ado_backlog_item` when you need a single normalized Azure DevOps work item by numeric
  id, with optional comments/relations/updates expansion.
- Use `get_ado_daily_status_report_data` to fetch the current ADO iteration, calculate the daily
  status report counts/percentages, and return the JSON block expected by
  `templates/daily-status-report.html`.
- `create_ado_backlog_item` request fields:
  - required: `type`, `project`, `iteration`, `title`
  - optional: `description`, `acceptance_criteria`, `tags`, `assigned_user`,
    `parent_backlog_item_id`, `description_artifact_ids`,
    `acceptance_criteria_artifact_ids`
- `post_ado_backlog_comment` accepts `id`, `comment`, optional `project`, and optional
  `artifact_ids`.
- `update_ado_backlog_text` accepts `id`, optional `project`, `description`,
  `acceptance_criteria`, `description_artifact_ids`, and `acceptance_criteria_artifact_ids`.
- `update_ado_backlog_tags` accepts `id`, optional `project`, `add_tags`, and `remove_tags`.
- `get_ado_daily_status_report_data` accepts optional `project`, `team`, `area_path`, `states`,
  `types`, `tags_any`, `text_search`, report-label overrides, state-group overrides,
  `effort_fields`, `max_items`, `exclude_types`, and elapsed-time options. It excludes `Task`
  work items and includes only Product Backlog Items and Bugs by default so the report tracks
  sprint delivery items rather than features or implementation subtasks. Daily status reports
  normalize `max_items` below 1000 up to 1000 so an agent-supplied small cap does not truncate
  sprint totals. By default elapsed sprint time uses Swedish working time (`Europe/Stockholm`,
  weekdays 08:00-17:00, Swedish public holidays and common non-working eves excluded), with
  `calendar_time` and manual `elapsed_sprint_percent` available as overrides.
- Session allowlisting is required: include `create_ado_backlog_item` in the session manifest
  `allowed_tools`, or the call is denied by policy. The same applies to every ADO backlog tool.

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
    "parent_backlog_item_id": null,
    "uploaded_attachments": []
  }
}
```

## Agent Inspection Tooling

- `get_agent_status` and `get_agent_logs` are directory-scoped inspection tools.
- A session with either tool can inspect its own VM and any other VM visible in the same logical
  `directory_path`.
- Requests for VMs outside the caller's directory scope are rejected at the gateway boundary with a
  scope-specific error.
- `get_workflow_snapshot` is a read-only inspection tool for the current workflow editor document.
  Add `get_workflow_snapshot` to a session manifest's `allowed_tools` when an agent should be able
  to view the active workflow config, layout, graph metadata, output schemas, and optional run
  snapshot.

## Agent Prompt Tools (`prompt_agent` / `respond_to_agent`)

- `prompt_agent` lets one session send a bounded prompt payload to another session in the **same
  directory scope**.
- `respond_to_agent` lets the target session post a response back exactly once.
- Both tools are policy-gated by `allowed_tools` and are included in default session creation via
  `agent-sandbox-cli create-session`. You can also grant/revoke them explicitly with:
  - `agent-sandbox-cli add-tool-permission <target> prompt_agent`
  - `agent-sandbox-cli add-tool-permission <target> respond_to_agent`
  - `agent-sandbox-cli revoke-tool-permission <target> prompt_agent`
  - `agent-sandbox-cli revoke-tool-permission <target> respond_to_agent`
- Response token semantics:
  - `prompt_agent` issues a signed `response_token` that is bound to the prompt message and target
    session.
  - Tokens are time-limited (`agent_prompt_token_ttl_seconds`) and secret-signed using the host
    env var named by `agent_prompt_token_secret_env`.
  - `respond_to_agent` consumes the token once; replay attempts are rejected.
  - Invalid/tampered/expired tokens are rejected.
- Least-privilege guidance:
  - grant `prompt_agent` only to sessions that need to dispatch work;
  - grant `respond_to_agent` only to sessions that need to answer prompts;
  - avoid broad cross-team sharing by keeping sessions in narrow `directory_path` scopes.

## Retell Phone Call Tools

- `list_contacts` lists the directory contact ids and safe summaries visible to the session. It does
  not return phone numbers or email addresses.
- `make_phone_call` starts an outbound Retell call to a configured directory contact id. Agents pass
  `contact` and `prompt`; they never pass raw phone numbers.
- `get_phone_call_result` reads the call state/transcript by `request_id` or `call_id`.
- Each tool is gated by its matching session permission: `list_contacts` discovers contact ids,
  `make_phone_call` starts calls, and `get_phone_call_result` reads results.
- Phone-review agents that approve ADO work items by voice also need `update_ado_backlog_tags`.
- Configure Retell under directory-scoped `providers.json` with `retell_api_key_env`,
  `retell_from_number`, and `retell_default_agent_id`.
- Configure callable contacts under directory-scoped `contacts.json`. Retell uses the contact's
  explicitly marked primary phone number; contacts without one cannot be called.
- The Retell agent/LLM used by `retell_default_agent_id` must reference `{{agent_question}}` in
  its Retell prompt or begin message. The gateway sends
  the tool `prompt` as `retell_llm_dynamic_variables.agent_question`; Retell does not treat it as
  a standalone prompt override.
- Put the actual Retell API key in the gateway host environment; the systemd gateway unit already
  loads repo-root `secrets.env`.
- The gateway webhook endpoint is `POST /retell/webhook`; it verifies `X-Retell-Signature` and
  persists supported call updates in `runtime/retell-phone-calls-store.json`.
- Agent-facing results and audit summaries omit configured phone numbers and Retell credentials.

## Relay Environment Configuration

- `OPENAI_API_KEY` is required on the host for `agent-sandbox-openai-relay`.
- `AZURE_ARTIFACTS_PAT` is required on the host only when the NuGet relay must reach private
  Azure Artifacts feeds.
- `config/npm-audit-facade.env` is optional and may remain empty; the npm audit facade reads its
  bind and upstream settings from runtime config.
- Keep these values in host-only shell environment or systemd env files such as:
  - `config/openai-relay.env` (loaded by both the relay and Admin Web units)
  - `config/nuget-relay.env`
  - `config/npm-audit-facade.env`
- Directory-specific OpenAI model key env vars named by `openai_api_key_env` should be
  available to the relay.
- Do not place these values in guest images, cloud-init, session manifests, or guest shell
  profiles.

## PR Feedback Heartbeat and Reply Loop

Use this when you want the guest to wake only for new review feedback and post threaded replies safely.

### Setup steps

1. Include PR feedback tools/stream metadata in the session manifest (same `session_id` the guest uses).
2. Bootstrap a provider runtime in the guest (`./scripts/bootstrap_codex_vm.sh <vm-name>` or `./scripts/bootstrap_copilot_vm.sh <vm-name>`).
3. For Azure DevOps PR feedback and completion cleanup, keep `agent-sandbox-ado-worker` running; it owns Azure DevOps feedback ingestion, PR status tracking, and trigger publication.
4. `agent-sandbox-discord-wake.service` and `agent-sandbox-scheduled-wake.service` cover Discord inbox wakeups and scheduled resumes. Azure DevOps PR feedback ingestion runs in `agent-sandbox-ado-worker`.
5. Use MCP calls from the agent in this order:
   `has_pending_feedback` → `list_unread_feedback` → `post_pr_feedback_reply` (with an idempotency key).
   Use `list_pull_requests(session_id, provider, repo, status?, creator_id?, reviewer_id?, top?, cursor?)`
   when you need provider-backed PR discovery; `status` maps to provider status filtering (for Azure DevOps:
   `searchCriteria.status`), and pagination continues with `next_cursor`.
   Use `get_pull_request(session_id, provider, repo, pr_id, include_reviewers?, include_commits?)`
   to fetch one PR with normalized fields plus a provider-complete `raw` payload.
   For full thread history (including already-consumed items), call `list_all_feedback`.
   Workflow graphs can also use `query:pull-request:list` and `effect:pull-request:update`
   for cron-driven PR polling and ADO tag/metadata updates. Azure DevOps PR tag expressions
   such as `(TagA or TagB) and not TagC` are client-side after the provider list call, so bound
   polling with status, branch/time filters, `top`, `skip`, or `cursor`.

Example `get_pull_request` result shape (Azure DevOps):

```json
{
  "provider": "azure_devops",
  "repo": "repo-1",
  "pr_id": "17",
  "title": "Fix race",
  "status": "active",
  "url": "https://dev.azure.com/org/project/_git/repo-1/pullrequest/17",
  "source_branch": "refs/heads/feature/fix-race",
  "target_branch": "refs/heads/main",
  "source_commit": "abc123",
  "target_commit": "def456",
  "created_by": "Agent",
  "reviewers": [{"reviewer_id": "u1", "display_name": "Reviewer One", "vote": 10}],
  "commits": [{"commit_id": "abc123", "comment": "Fix race condition"}],
  "raw": {"pullRequestId": 17, "...": "..."}
}
```

### Expected timing behavior

- Azure DevOps feedback and completion pickup is driven by `agent-sandbox-ado-worker` cadence and updates the shared host feedback store, `runtime/pr-map.json` status fields, and trigger queue.
- `agent-sandbox-discord-wake` and `agent-sandbox-scheduled-wake` do not poll Azure DevOps PR feedback streams.
- Guests consume pending feedback via MCP tools by default.
- Provider bootstrap scripts default `ENABLE_GUEST_HEARTBEAT=0`, which keeps
  `agent-heartbeat.timer` disabled for new guests.
- Set `ENABLE_GUEST_HEARTBEAT=1` during bootstrap to install and enable an optional guest-local resume timer.
  The timer uses the same provider adapter path as the host resume flow and accepts
  `GUEST_HEARTBEAT_PROMPT` to override the resume prompt per agent.
- Migration note for existing VMs: disable any previously enabled guest timer if you want host-only wakeups:
- Agent enable/disable control is now mirrored in host state at `runtime/host-agent-control-state.json`,
  so the wake workers can continue ignoring disabled agents even while their VMs are shut down.
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

- Cron schedules use standard 5-field expressions, including ranges, stepped ranges, lists,
  and month/weekday names.

- MCP-sourced schedules enforce `resume_mode=same_context`; `new_context` is valid for admin/CLI-created schedules.

### Troubleshooting

- **Run consolidated status first:** start with a host snapshot before debugging individual services.
  - Human-readable: `agent-sandbox-cli status --runtime-dir runtime`
  - Machine-readable: `agent-sandbox-cli status --runtime-dir runtime --json`
  - The command summarizes config loading, runtime path permissions, key systemd units, gateway/relay reachability, and session expiration health.
- **Wake worker lock contention:** a wake worker exits because another instance is running.
  - Check the configured worker lock path and active systemd processes before restarting.
- **Provider API failures while replying:** gateway tool errors from GitHub/Azure DevOps calls.
  - Validate host tokens (`GITHUB_TOKEN`/configured env, `AZURE_DEVOPS_PR_PAT`/configured env).
  - Retry with the **same idempotency key** so `post_pr_feedback_reply` deduplicates safely.

### Audit/log locations and healthy signals

- Discord wake service logs: `journalctl -u agent-sandbox-discord-wake.service`.
- Scheduled wake service logs: `journalctl -u agent-sandbox-scheduled-wake.service`.
- Host runtime: `runtime/pr-feedback-store.json`, `runtime/pr-reply-idempotency.json`,
  `runtime/host-agent-control-state.json`, `runtime/audit.jsonl`.
- Healthy pattern:
  - host worker alternates between quiet polls and discovered feedback events,
  - audit shows allowed calls for `has_pending_feedback`, `list_unread_feedback`, `list_all_feedback`, and `post_pr_feedback_reply`,
  - idempotent retry returns `status=deduplicated` instead of creating duplicate provider comments.

## PR Review Request Workflow

Use this when a receptionist-style agent should enqueue a PR review request and let the trigger
orchestrator drive the disposable review VM workflow.

### Host-side pieces

- New MCP tools:
  - `request_pr_review`
  - `post_pr_comment`
  - `post_pr_inline_comment`
  - `submit_pr_review`
- New trigger:
  - `trigger:review:new-request`
- New runtime state:
  - `runtime/review-request-store.json`

`request_pr_review` normalizes the PR target, resolves provider metadata (title, URL, base/head
refs and commits), and writes a durable request record into the host review-request inbox. The
trigger provider polls that inbox and emits a `review_request` payload for workflow consumption.
Use `post_pr_inline_comment` for a single file/line finding, or `submit_pr_review` with
`comments=[{"path": "...", "line": 123, "body": "..."}]` for batched inline findings. Do not
write the file path and line number into the comment body; the tool attaches the provider-native
line context.

For Azure DevOps PRs, PR comment/reply/review tools accept staged `artifact_ids`. The gateway
uploads each artifact to the PR attachments API and appends Markdown image/link references to the
posted text.

### Typical workflow shape

1. `trigger:review:new-request`
2. `effect:vm:create`
3. `effect:session:update-metadata`
4. `effect:vm:exec`
   run `scripts/guest/setup_review_repo.sh` inside the guest with explicit `--base-ref` and `--head-ref`
5. `effect:agent:prompt`
6. `gate:await-agent`
7. `post_pr_comment`, `post_pr_inline_comment`, or `submit_pr_review`
8. `effect:vm:stop`

Connect `effect:agent:prompt` or `effect:agent:enqueue-prompt` to
`gate:await-agent` through the prompt's `started` source port. Use the
prompt's `completed` source port for downstream work that should wait for the
agent run itself to finish. Use the prompt's `error` source port for
notification, retry, or cleanup branches that should only fire when a
supported prompt dispatch failure is routed. If no error edge is wired, the
prompt still fails normally.
For `effect:agent:enqueue-prompt`, routed dispatch failures preserve retry
context such as `queued`, `queue_key`, and `coalesced_count` when the prompt was
queued before dispatch failed.

### Review repo prep helper

Guests can prepare a deterministic review checkout with:

```bash
./scripts/guest/setup_review_repo.sh \
  --repo-path /home/agent/<repo-name> \
  --base-ref refs/heads/main \
  --head-ref refs/heads/review/topic
```

The helper resolves explicit target/head refs, writes durable local refs under
`refs/agent-sandbox/review/*`, computes `REVIEW_DIFF_BASE_COMMIT` with `git merge-base`,
detaches the worktree onto the head commit, and writes a shell-safe context file at
`.agent-sandbox/review-context.env` by default. Reviewers should inspect
`REVIEW_DIFF_BASE_COMMIT..REVIEW_HEAD_COMMIT` for normal PR findings; the target/head range can
include target-branch drift. If you pass an explicit `--base-commit` or `--head-commit`, the
helper fails instead of silently falling back to the branch tip.

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

## Communication File Transfer

Communication file transfer is intentionally host-mediated. Guests never receive Discord, Teams,
Gmail, or Microsoft Graph credentials; they call MCP tools on the host gateway, and the host
adapters perform provider API calls under the active session policy.

- **Inbound attachment download:** use `communication_list_attachments` to enumerate provider
  attachment metadata, then `communication_download_attachment` to fetch a single attachment into
  the host artifact store. The gateway validates the provider scope, normalized filename, MIME
  type, extension, and byte size before it stages the blob. Use
  `communication_copy_artifact_to_workspace` only when the guest needs a local copy; provider
  filenames are normalized and copied under `/home/agent/communication-artifacts/`.
- **Outbound file send:** outbound sends reuse staged artifact IDs rather than arbitrary guest
  paths. Use `communication_stage_workspace_file` to stage an agent-created file from under
  `/home/agent`; the provider-specific tools are `discord_post_file`, `teams_post_file`,
  `email_create_draft_with_attachments`, `email_send_approved_with_attachments`,
  Azure DevOps PR comment/reply/review tools, and Azure DevOps backlog comment/text tools.
  Keep `communication_file_send_enabled: false` unless the session is explicitly allowed to
  send files.
- **Approval behavior:** set `communication_file_require_approval_for_send: true` before enabling
  outbound file send. When that flag is true, each outbound file send must provide an approval
  request ID whose approved payload exactly matches the attempted provider, destination, message,
  and artifact IDs. Email still also requires `email_send_enabled: true` before an approved send
  can leave the draft state.
- **Host-only provider credentials:** Discord bot tokens, Teams/Graph tokens, and Gmail OAuth
  tokens belong in host environment variables or systemd env files referenced by config. Do not
  place token values in guest disks, cloud-init payloads, session manifests, artifacts, or prompts.
- **Artifact retention:** staged blobs and metadata live under
  `runtime/communication-artifacts/` by default. Artifact IDs are opaque (`comm_...`), blobs use
  host-chosen `.bin` filenames, and metadata records original provider fields for auditability.
  The store defaults to a seven-day TTL and cleans expired artifacts opportunistically when the
  store is used; operators can also run cleanup from the host during maintenance windows.

A conservative policy example is available at
`config/communication-files/policy.json.example`. It enables inbound receive for small text, PDF,
and image files, leaves outbound send disabled, and preconfigures approval as mandatory before any
future outbound-send enablement.

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
- Guests can reach only the specific host TCP ports you allow in `ALLOWED_TCP_PORTS`; the defaults are `22` for host SSH/Git, `8000` for MCP, `11434` for the host-side OpenAI relay or another OpenAI-compatible LLM endpoint, `11666` for the host-side NuGet relay, `11777` for the HTTP target relay, `8081` for the host-side Nexus package cache, and `3142` for the host-side apt-cacher-ng relay.
