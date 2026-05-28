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

For reproducible fresh-host Ubuntu 24.04 deployment paths that wrap the same repo-native bootstrap
sequence with Ansible, including the dedicated container-worker VM playbook, see
`ansible/README.md`.

If Admin Web is part of the deployed surface, treat `agent-sandbox-admin-web` as a managed host
service and configure `config/admin-web.env` before exposing it beyond localhost.

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
12. Optionally bootstrap generic guest Python packages into an offline tools virtualenv with `./scripts/bootstrap_python_packages_vm.sh <vm-name> <package-spec> [package-spec ...]`.
13. Optionally install the host `.NET` SDK into the guest with `./scripts/bootstrap_dotnet_vm.sh <vm-name>`.
14. Optionally prewarm the shared host NuGet cache with `./scripts/prewarm_nuget_cache.sh <path> [more paths...]`.
15. Optionally install NuGet relay sources into the guest with `./scripts/bootstrap_nuget_vm.sh <vm-name>`.
16. Optionally provision a guest-local Microsoft SQL Server instance into the guest with `MSSQL_SA_PASSWORD=... ./scripts/bootstrap_mssql_vm.sh <vm-name>`.
17. Optionally install Playwright into the guest with `./scripts/bootstrap_playwright_vm.sh <vm-name>` for rendered-DOM debugging.
18. Optionally create a local bare Git remote with `./scripts/create_git_remote.sh <repo-name>` or mirror the default host repo with `./scripts/mirror_issue_repo.sh <issue-number>` if the VM should push code back to the host.
19. Use [live-smoke-tests.md](live-smoke-tests.md) when you want to verify live Discord and provider-backed email behavior against real credentials.

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

## Workflow Runtime Files and Troubleshooting

The workflow orchestrators share runtime state under `runtime/` (or your configured
`runtime_dir`):

- `trigger-orchestrator-events.json`: durable queue of trigger and completion events.
- `trigger-orchestrator-cursors.json`: trigger-worker cursors/checkpoints.
- `gate-orchestrator-executions.json`: gate execution log with idempotency key, gate type, state,
  resolved config, outputs, and merge errors.
- `gate-orchestrator-cursors.json`: gate-worker cursor/checkpoint data.
- `effect-orchestrator-cursors.json`: effect-worker cursor/checkpoint data.
- `trigger-orchestrator.lock` / `gate-orchestrator.lock`: single-instance lock files.
- `trigger-conformance-report.json` and `effect-conformance-report.json`: conformance snapshots
  written by the trigger and effect suites under the configured `runtime_dir`.

Gate execution state is useful when debugging workflow gates:

- `succeeded`: the gate emitted a downstream event.
- `dropped`: a filter gate intentionally suppressed the event.
- `failed`: the gate hit a validation, binding, or merge projection error.
- `skipped`: the gate was bypassed because of a validation or cycle guard.

Troubleshooting checklist:

1. **No downstream nodes running**
   - Confirm `triggers`, `gates`, and `effects` exist, and confirm there is at least one edge from
     the upstream node that should start the flow.
   - Confirm pending source events exist in `trigger-orchestrator-events.json`.
2. **Bindings fail with missing output references**
   - Check upstream node `outputs` declarations and the actual upstream payload keys.
   - Exact `${{node.output_name}}` and embedded `"...${{node.output_name}}..."` both require the referenced key to exist.
   - After a branch re-merges, bind directly only to nodes that are guaranteed on every path to
     the downstream node. Map branch-local values into merge outputs when they must be used later.
3. **Merge gate output is not being emitted**
   - Confirm the merge gate has at least one inbound edge and that the configured edges point at
     the upstream node ids you expect to observe.
   - Remember that `gate:merge` follows Reactive Extensions `merge` semantics: each inbound event
     is forwarded as it arrives, and the execution record snapshots the latest payload per source
     for inspection.
4. **Merge output looks incomplete or wrong**
   - Inspect `gate-orchestrator-executions.json` for the recorded `resolved_config`, the latest
     inbound payload remembered for each source, and the emitted `outputs` for the specific event.
   - Verify the configured shared `outputs` projection matches the keys that should be forwarded
     from each inbound event.
5. **Deserialize output is not bindable**
   - If `content` is bound to an upstream string, declare the expected `outputs` schema on the
     `gate:deserialize` node so editor binding validation can see those keys before runtime.
   - Inspect the gate execution record to confirm the parsed object was recorded under `outputs`.
6. **Serialized text is missing**
   - Bind downstream nodes to `gate:serialize` output `content`, not the input field `value`.
   - Inspect the gate execution record to confirm the serialized string was recorded under
     `outputs.content`.
7. **Unexpected duplicate behavior**
   - Check idempotency collisions in `gate-orchestrator-executions.json` using
     `idempotency_key=<event_id>::<node_id>`.
8. **Cycle-like configs**
   - Direct cycles are marked `skipped` with `error: cycle_detected`; break the loop by changing
     the edge wiring between nodes.

### Workflow pack operations

- The trigger/effect workers compose workflow definitions from `workflow_packs` in
  `config/trigger-orchestrator/config.json`.
- Operator pack files live at `config/workflow-packs/*.json` and include versioned IR (`pack_id`,
  `pack_version`, `ir.version`, `ir.nodes`, `ir.edges`). These live pack files are ignored by Git;
  checked-in starting points use `*-example.json`.
- A pack can contain multiple connected graphs. Optional `graphs` entries name those connected
  node sets with `id`, `name`, `description`, `directory_paths`, `user_ids`, and `node_ids`; the
  runtime still executes from the IR, while the editor uses this metadata for directory-scoped
  graph selection and labels. `user_ids` are owner/audit metadata only; workflow graphs are visible
  to control-plane operators. Saving from a filtered view keeps unchanged graph metadata entries
  intact, so graphs outside the selected directory are not rewritten by the presentation filter.
- In the Workflow editor, node inspector edits are staged into the local workflow document as soon
  as fields change. They are still not persisted to disk until **Save workflow** succeeds. Dirty
  nodes show an **Unsaved changes** badge, selected dirty nodes can be reverted with **Reset
  changes**, and leaving the workflow view or switching directories prompts for confirmation while
  any workflow changes remain unsaved.
- Admin Web exposes workflow reporting under **Reports**. The workflow graph activity report ranks
  graph metadata by trigger events plus gate/effect execution records over the selected period.
- Admin Web also exposes read-only conformance snapshots under the same **Reports** section. See
  [conformance-reports.md](conformance-reports.md) for the trigger/effect artifact names,
  regeneration commands, and report states.
- For a repeatable local workflow run inspector demo, see
  [workflow-run-inspector-demo.md](workflow-run-inspector-demo.md). It seeds a dedicated
  `runtime/workflow-run-inspector-demo` tree with a deterministic trigger, gate, effect,
  approval, artifact, and graph snapshot that the workflow tab can inspect by exact
  `event_id`, `trace_id`, `session_id`, or `artifact_id`.
- To add a new domain workflow, add a new pack file and list it in `workflow_packs`. No core
  orchestrator Python code changes are required for that path.
- `config/trigger-orchestrator/config.json` is operator-local too. Start from
  `config/trigger-orchestrator/config-example.json`, then point `workflow_packs` at the local pack
  files you want the host to run.
- Optional one-off overlays can be provided with inline `workflow_ir` in
  `config/trigger-orchestrator/config.json`.

## Admin Web Operator Console

Admin Web is the supported operator GUI. It uses the same host-side orchestration layer as the CLI
and surfaces:

- a default operations dashboard after login, with live system health, worker, agent,
  workflow, approval, session, audit, workflow-onboarding, and capacity-health panels
- all active and inactive VMs
- VM power state and guest agent state separately
- one-click `Start`, `Shutdown`, `Enable`, `Disable`, and `Resume`
- local issue creation plus issue-VM startup
- persistent selected-directory context with `root` fallback
- direct-owned VM/session/issue listings for the selected directory
- a Tool Calls tab for read-only inspection of the configured `audit_log_path`
  (`runtime/audit.jsonl` by default), with filtering by session, tool, status,
  directory/scope, and time range. The UI uses `runtime/audit.db` as an indexed
  SQLite read model when available and falls back to JSONL when needed.
- an Approvals tab for reviewing queued approval requests with redacted context and
  selected-directory filtering
- Sessions policy controls for `approval_required_tools`, which lets operators require human
  approval before selected allowed tools execute
- a configuration tab that inspects inherited per-domain directory config

For detailed operator procedures and screenshots for every Admin UI section, see
[`admin-ui-user-manual.md`](admin-ui-user-manual.md).

The dashboard is the default landing view for `/` and normal post-login redirects. Existing hash
deep links remain direct: for example `/#workflow`, `/#sessions`, `/#approvals`, `/#tool-calls`,
and `/#configuration` still open those sections instead of the dashboard. Dashboard panels use the
same live Admin Web APIs as the detail tabs. The capacity-health view is available at
`/api/admin/capacity-health`; it returns control-plane disk and known-storage summaries, session
counts plus per-directory age/lifecycle/backend breakdowns, worker reachability, live-check
timestamps, telemetry states, queue age, and unread prompt pressure. Unconfigured, unreachable,
permission-denied, or unsupported sources are reported explicitly, and raw host paths or bearer
secrets are not exposed. When an endpoint has no data or is unavailable in the current environment,
the panel shows an explicit missing or unavailable state rather than inferred or placeholder values.

The product context panel is backed by `/api/admin/release-info`. It reads the installed package
version or `pyproject.toml` version when present, an explicit deployed build identity when the
environment provides one, recent release notes from the maintained top section of `CHANGELOG.md`,
and the latest prose update from `NEWS.md`. Operators should update `[project].version` when the
package version changes, add release notes to the top changelog section, and write a user-facing
news entry before deployment. The `/api/admin/release-info` endpoint accepts `news_index` and returns
one news entry plus previous/next paging data, which keeps old blog history available without
returning the whole `NEWS.md` archive to the dashboard. The panel also exposes an authenticated
changelog download button. Missing version/build/changelog/news sources are reported as explicit
missing reasons.

Dashboard-tagged GET requests use a short-lived backend JSON cache to avoid repeating every
systemd, VM inventory, workflow, approval, session, and audit read while an operator is moving in
and out of the dashboard. The detailed tabs are not opted into that cache, and the dashboard Refresh
button sends a cache-bypass marker for a fresh read.

For machine-readable scripting that matches the GUI data model, use:

```bash
agent-sandbox-cli list-vms --all --json
agent-sandbox-cli list-local-issues --json
```

## Admin Web Authentication

The browser admin surface supports three auth modes:

- `none`: no sign-in required. This remains the default when
  `AGENT_SANDBOX_ADMIN_WEB_AUTH_MODE` is unset and `AGENT_SANDBOX_ADMIN_WEB_PASSWORD` is also unset.
- `local_password`: the existing local username/password form. If
  `AGENT_SANDBOX_ADMIN_WEB_AUTH_MODE` is unset but `AGENT_SANDBOX_ADMIN_WEB_PASSWORD` is set, this mode is selected automatically for backward compatibility.
- `azure_sso`: Microsoft Entra browser sign-in through OIDC Authorization Code + PKCE.

Required local session settings:

- `AGENT_SANDBOX_ADMIN_WEB_SESSION_SECRET`
- optional `AGENT_SANDBOX_ADMIN_WEB_SESSION_TTL_SECONDS`
- optional `AGENT_SANDBOX_ADMIN_WEB_COOKIE_SECURE=true`

`local_password` settings:

- `AGENT_SANDBOX_ADMIN_WEB_AUTH_MODE=local_password`
- `AGENT_SANDBOX_ADMIN_WEB_USERNAME`
- `AGENT_SANDBOX_ADMIN_WEB_PASSWORD`

`azure_sso` settings:

- `AGENT_SANDBOX_ADMIN_WEB_AUTH_MODE=azure_sso`
- `AGENT_SANDBOX_ADMIN_WEB_AZURE_TENANT_ID`
- `AGENT_SANDBOX_ADMIN_WEB_AZURE_CLIENT_ID`
- `AGENT_SANDBOX_ADMIN_WEB_AZURE_CLIENT_SECRET`
- `AGENT_SANDBOX_ADMIN_WEB_AZURE_REDIRECT_URI`
  or `AGENT_SANDBOX_ADMIN_WEB_PUBLIC_BASE_URL`
- optional `AGENT_SANDBOX_ADMIN_WEB_AZURE_AUTHORITY_BASE_URL`
- optional first-run `AGENT_SANDBOX_ADMIN_WEB_AZURE_ALLOWED_USERS`
- optional `AGENT_SANDBOX_ADMIN_WEB_AZURE_POST_LOGIN_REDIRECT`

Azure SSO enabling sequence:

1. Register a confidential Microsoft Entra web application for the admin web.
2. Add the exact admin callback URI to the app registration.
3. Allow OpenID scopes `openid profile email`.
4. Set the trusted tenant id in `AGENT_SANDBOX_ADMIN_WEB_AZURE_TENANT_ID`.
5. On first startup, add initial admin UPN/email-style logins to
   `AGENT_SANDBOX_ADMIN_WEB_AZURE_ALLOWED_USERS`.
6. Restart `agent-sandbox-admin-web`.

Azure SSO users are stored in `runtime/admin-web.db`, keyed by normalized login/email. When the
SQLite user table is empty, `AGENT_SANDBOX_ADMIN_WEB_AZURE_ALLOWED_USERS` seeds those users with the
`admin` role. A legacy `runtime/admin-web-users.json` file is imported once when the SQLite table is
empty. After that first bootstrap, the SQLite runtime store is the source of truth. Admins manage
membership and the `admin` / `regular` role from the Admin Web Users tab. Removing an Azure user
from the runtime store invalidates their existing local admin session on the next authenticated
request.

The admin backend performs the token exchange server-side, validates issuer/audience/tenant, then mints the same local signed session cookie used by the password flow. Privileged lock/unlock behavior remains tied to that local admin session rather than the upstream Azure token.

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
  - `wake.json`
  - `ado-worker.json`
  - `issue-setup.json`
  - `vm.json`
- `root` is the live top-level layer; there is no hidden YAML base beneath it.

## SSH Access

- New VMs install `openssh-server` and `ripgrep`.
- The launch script injects the caller's `~/.ssh/id_ed25519.pub` by default, falling back to `~/.ssh/id_rsa.pub`.
- Override the key with `SSH_PUBLIC_KEY_FILE=/path/to/key.pub`, or point `SSH_PRIVATE_KEY_PATH` at the matching private key so the script can derive a temporary public key when needed.
- After boot, find the guest IP with `virsh domifaddr <vm-name>` and connect with `ssh agent@<vm-ip>`.
- Host-side guest SSH commands store guest host keys in `~/.ssh/agent-sandbox-known_hosts` keyed by VM name. `destroy_agent_vm.sh` removes the stale alias for a destroyed VM so a replacement VM can be learned cleanly.
- To list all currently running VMs with live guest agent status from the host, run `agent-sandbox-cli list-vms`.

## VM Agent Runtime Baseline

- Cloud-init writes provider-neutral baseline config files: `/etc/agent-sandbox/session.env` and `/home/agent/.agent/config.env`.
- Cloud-init also appends a shell profile hook that loads `/etc/agent-sandbox/session.env` and exports `OPENAI_API_KEY=relay-placeholder` if no key is already set.
- Cloud-init creates `agent` as the unprivileged runtime user and `agent-admin` as the host-control SSH user with passwordless sudo. Host resume/log tooling uses `agent-admin` to append root-owned logs while running the actual agent process as `agent`.
- Generic orchestration status is provider-neutral and read from `/home/agent/.agent-sandbox/agent-*.state`, with companion `agent-*.pid` files. Transcript logs are written through the privileged host-control path to root-owned files under `/var/log/agent-sandbox/agent-runs/`.
- New VMs pick this up automatically; existing VMs need to be recreated or migrated if they predate baseline config changes. For one-off maintenance against an old sudo-enabled runtime user, set `VM_SSH_USER=agent` explicitly.

### Codex provider bootstrap

- `./scripts/bootstrap_codex_vm.sh <vm-name>` copies the vendored Linux Codex binary from the host into `~/bin/codex` for the guest user and verifies Codex.
- The bootstrap script writes `~/.codex/config.toml` with relay-aware defaults (currently `model = "gpt-5.4"`, `model_reasoning_effort = "high"`, and `personality = "pragmatic"`), preferring explicit `LLM_BASE_URL` and otherwise reading `AGENT_SANDBOX_LLM_BASE_URL` from `~/.agent/config.env`; it also registers the host MCP gateway as `[mcp_servers.agent_sandbox]` using `AGENT_SANDBOX_GATEWAY_URL` and copies the host Codex model cache when available so offline guests see the same model menu. It then appends the placeholder-key shell hook plus `~/bin` PATH entry to both `~/.profile` and `~/.bashrc`.
- Runtime start/resume model selection is host-session-bound: `session.model` wins, otherwise effective `issue_setup.default_model` is used. If neither resolves, start/resume fails with a remediation error instead of using an implicit runtime fallback model.
- The bootstrap script waits for guest cloud-init via the privileged host-control user and repairs `/home/agent` ownership before the first runtime-user write, so SSH readiness alone cannot race the Codex bootstrap.

### GitHub Copilot CLI provider bootstrap

- `./scripts/bootstrap_copilot_vm.sh <vm-name>` copies the host `copilot` binary into `~/bin/copilot` for the guest user and verifies `copilot version`.
- The bootstrap script writes `~/.copilot/config.json` with provider-local defaults and `~/.copilot/mcp-config.json` so Copilot CLI can reach the host MCP gateway as `agent_sandbox`, including the active `session_id` query string when present.
- The bootstrap script also installs a guest `gh` shim in `~/bin/gh`. Copilot CLI uses GitHub CLI as its lowest-priority auth fallback, so the shim answers `gh auth token` by calling the host gateway at `/copilot-auth/token` with the guest session id. That keeps the long-lived GitHub token on the host while avoiding permanent guest-side token storage.
- The bootstrap script appends `~/bin` and `COPILOT_HOME=~/.copilot` to both `~/.profile` and `~/.bashrc`.
- Host-mediated auth reads the effective provider `github_token_env` on the host. That environment variable must contain a GitHub token that can satisfy Copilot CLI requests. Guest-side `COPILOT_GITHUB_TOKEN`, `GH_TOKEN`, `GITHUB_TOKEN`, or `copilot login` still override the host-mediated `gh` fallback if you set them intentionally.

### Microsoft Teams delegated auth

- Teams access is host-side and delegated. The preferred operator flow is Admin Web ->
  Configuration -> Communications: verify the directory-scoped Teams provider settings, inspect
  delegated auth status, and start the device code flow sign-in or sign-out from the browser while
  keeping the resulting credential on the host only.
- Bind each directory to the intended Teams identity through per-directory Teams identity binding
  in directory-scoped provider config. Subdirectories inherit the binding, so different directory
  trees can point at different Teams accounts without guest-side secrets.
- Preferred delegated provider config keys are:
  - `teams_backend: delegated_graph_api`
  - `teams_delegated_identity_id`
  - `teams_delegated_client_id`
  - optional `teams_delegated_tenant_id`, `teams_delegated_scopes`,
    `teams_delegated_authority_base_url`, and `teams_delegated_credential_store_path`
  - the default delegated scope set includes channel read/send and `Chat.ReadWrite`
  - Teams direct-message sender mail/UPN resolution may require adding a Graph user-read scope such
    as `User.ReadBasic.All` or `User.Read`
- The Entra app behind `teams_delegated_client_id` must be configured for public client /
  device-code auth. `AADSTS7000218` means the app is being treated as a confidential client and
  the host-side delegated flow in this repo cannot complete until that registration is fixed.
- In Admin Web -> Configuration -> Communications, use the Teams auth panel to:
  - inspect the current delegated auth state for the selected directory
  - start device-code sign-in and complete the login polling flow
  - sign out when you need to revoke the stored host-side credential
- Use the channel lookup helper in the same Communications area to resolve `team-id/channel-id`
  entries and copy them into communication targets.
- Use the host CLI for delegated auth lifecycle when you need a headless fallback:
  - `agent-sandbox-cli teams-auth login --directory <directory-path>`
  - `agent-sandbox-cli teams-auth status --directory <directory-path>`
  - `agent-sandbox-cli teams-auth logout --directory <directory-path>`
  - `agent-sandbox-cli teams-auth list`
- After changing `teams_delegated_scopes`, repeat `teams-auth login` for that directory so the
  stored delegated credential is refreshed with the new scopes.
- The legacy static env-token mode remains supported for compatibility. In that mode,
  `teams_graph_access_token_env` names the host environment variable that holds the token.
- Do not place Teams credentials in cloud-init, session manifests, or guest shell profiles.
- Guests never need direct Microsoft Graph credentials or internet access; Teams API access
  happens only in the host-side gateway process, and the token must cover the Teams channels or
  chats you intend to read and write.
- Teams message arrival for `trigger:teams:new-message` and
  `trigger:teams:direct-message` comes from host-side ingest polling on the host, using the same
  delegated Teams identity/configuration. That polling path is separate from PR and chat wakeups,
  which are handled by the ADO worker, Discord wake worker, and scheduled wake worker.
- Teams targets can be channel threads, group chats, or explicitly allowlisted one-on-one users:
  - channel targets use `team-id/channel-id` and should be declared in `session.teams_channel_allowlist`
  - chat targets use `chat:<chat-id>` and should be declared in `session.teams_chat_allowlist`
  - user targets use `user:<user-id-or-upn>` and are resolved host-side into a one-on-one
    `chat:<chat-id>` before sending. User targets require `session.teams_user_allowlist` or a
    declared communication target; they are not valid for message reads, reactions, or attachment
    downloads.
- Session communication allowlists can also contain directory-scoped communication target IDs. A
  target ID is resolved from the session directory at use time and must expose `read`, `write`, or
  `reply` to match the attempted read/poll/download, top-level send, or reply/reaction operation.
  Raw Teams and Discord IDs remain supported.
- The host-side Teams adapter reads channel threads from
  `GET /teams/{team-id}/channels/{channel-id}/messages` and group chats from
  `GET /chats/{chat-id}/messages`. Direct-message triggers discover one-on-one chats with
  `GET /me/chats` and only persist messages from configured sender user ids.
- The host-side Teams adapter creates or reuses one-on-one chats with `POST /chats`. The default
  delegated `Chat.ReadWrite` scope is sufficient; a narrower deployment can grant `Chat.Create`
  for first-contact one-on-one chat creation.
- `teams_get_messages` accepts an optional `channel_target` so agents can read the specific
  channel or chat that woke them. Cursorless reads still stay on a bounded recent path: the gateway
  applies `teams_poll_limit` as the default message limit, and the Graph adapter caps each history
  scan with `teams_history_page_cap` plus `teams_history_deadline_seconds`.

## VM Python Bootstrap

- `./scripts/bootstrap_python_vm.sh <vm-name> <host-checkout-root> <guest-repo-root>` builds a temporary host wheelhouse from the repo's `pyproject.toml`, copies it into the guest, creates a repo-local `.venv`, and installs the repo with dev dependencies offline.
- The bootstrap validates that the guest Python interpreter satisfies the repo's `requires-python` lower bound before it creates the virtualenv.
- The guest install path uses `pip install --no-index --find-links ... --no-build-isolation -e ".[dev]"` so later guest edits still run against the cloned checkout rather than a prebuilt wheel.
- `./scripts/bootstrap_python_packages_vm.sh <vm-name> <package-spec> [package-spec ...]` is the lower-level generic variant for guest-local Python tools that are not tied to a repo checkout.
- The generic package bootstrap builds a temporary host wheelhouse for the requested package specs plus `setuptools`, `wheel`, and `virtualenv`, copies that wheelhouse into the guest under `~/.agent-sandbox/python-wheelhouse/tools`, creates or reuses a guest tools virtualenv under `~/.agent-sandbox/python-tools-venv`, and installs the requested packages offline with `pip install --no-index --find-links ...`.
- Override host/guest interpreter paths with `HOST_PYTHON_BIN` and `GUEST_PYTHON_BIN`, or override guest destinations with `GUEST_WHEELHOUSE_DIR` and `--venv` when needed.

## VM Playwright Bootstrap

- `./scripts/bootstrap_playwright_vm.sh <vm-name>` copies a host-seeded Node runtime into `~/toolchains/node-v24.14.0`.
- It copies `playwright` and `playwright-core` into `~/playwright-runtime/node_modules`.
- It copies the host Playwright browser cache into `~/.cache/ms-playwright`.
- It appends `PATH`, `PLAYWRIGHT_BROWSERS_PATH`, `NODE_PATH`, `PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD`, `PLAYWRIGHT_MCP_SANDBOX`, and `PLAYWRIGHT_MCP_EXECUTABLE_PATH` setup to `~/.profile`.
- `PLAYWRIGHT_MCP_EXECUTABLE_PATH` is resolved from the seeded Playwright package/cache so `playwright-cli` does not try to use `/opt/google/chrome/chrome` or download a browser during an agent task.
- It verifies the guest can resolve the Chromium executable without guest internet access.

## VM Frontend Dependency Bootstrap

- `./scripts/bootstrap_frontend_deps_vm.sh <vm-name> <host-checkout-root> <install-root> [install-root ...]` copies a host-seeded Node runtime into `~/toolchains/node-v24.14.0`.
- It snapshots each selected install root under `~/frontend-deps-runtime/install-roots/<relative-path>`.
- Each install root must already contain `package.json` and `node_modules` on the host.
- `bootstrap-frontend-in-vm` restores those snapshots into the matching relative paths inside the guest clone so Angular builds can use the copied dependency trees offline.
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
- Defaults: `22` for host SSH/Git, `8000` for the MCP gateway, `11434` for the host-side OpenAI relay or a local OpenAI-compatible LLM endpoint such as Ollama, `11666` for the host-side NuGet relay, `11777` for the host-side HTTP target relay used to reach approved external web endpoints such as APP demo environments, `8081` for the host-side Nexus package cache used by worker PyPI/npm restores, and `3142` for the host-side apt-cacher-ng relay used by worker apt provisioning. The optional Qdrant semantic store binds to `127.0.0.1:6333` by default and is intentionally host-local in this slice.
- Override when applying firewall rules, for example:

  ```bash
  sudo ALLOWED_TCP_PORTS="22 8000 11434 11666 11777 8081 3142 4000" ./scripts/apply_firewall.sh
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

## Host-Side Workflow Snapshot Tool

- Gateway tool name: `get_workflow_snapshot`.
- The tool is policy-gated: each session manifest must include `get_workflow_snapshot` in
  `allowed_tools` before calls are accepted.
- Required MCP arguments: none beyond the session binding.
- Optional MCP arguments:
  - `include_runs` (default `false`) includes the current workflow run snapshot alongside the
    workflow editor document.
- Response payload includes the active workflow editor document: config path, layout path, revision,
  saved timestamp, normalized workflow config, layout, graph metadata, and derived output schemas.

## Host-Side Azure DevOps Backlog Tools

- Gateway tool name: `create_ado_backlog_item`.
- The tool is policy-gated: each session manifest must include `create_ado_backlog_item` in
  `allowed_tools` before calls are accepted.
- Required MCP arguments: `type`, `project`, `iteration`, `title`.
- Optional MCP arguments: `description`, `acceptance_criteria`, `tags`, `assigned_user`,
  `parent_backlog_item_id`, `description_artifact_ids`, and
  `acceptance_criteria_artifact_ids`.
- Gateway tool name: `post_ado_backlog_comment`.
  - Policy-gated with `post_ado_backlog_comment`.
  - Required MCP arguments: `id`, `comment`.
  - Optional MCP arguments: `project`, `artifact_ids`, `request_id`.
- Gateway tool name: `update_ado_backlog_text`.
  - Policy-gated with `update_ado_backlog_text`.
  - Required MCP argument: `id`; at least one text field or artifact list must be present.
  - Optional MCP arguments: `project`, `description`, `acceptance_criteria`,
    `description_artifact_ids`, `acceptance_criteria_artifact_ids`, `request_id`.
- Gateway tool name: `update_ado_backlog_tags`.
  - Policy-gated with `update_ado_backlog_tags`.
  - Required MCP argument: `id`; at least one tag must be present in `add_tags` or `remove_tags`.
  - Optional MCP arguments: `project`, `add_tags`, `remove_tags`.
  - The tool reads the current `System.Tags`, merges the requested changes, and writes the full
    tag field back to ADO.
- Artifact arguments use opaque IDs returned by `communication_stage_workspace_file` or inbound
  media download tools. When `communication_file_require_approval_for_send` is enabled, the call
  must include a matching approved `request_id`.

## Host-Side Azure DevOps Backlog Query Tool

- Gateway tool name: `query_ado_backlog_items`.
- The tool is policy-gated: each session manifest must include `query_ado_backlog_items` in
  `allowed_tools` before calls are accepted.
- MCP arguments:
  - `project` (optional when `azure_devops_project` is configured in provider config)
  - optional filters: `iteration`, `area_path`, `states`, `types`, `tags_any`, `text_search`
  - paging inputs: `top` (default `50`, max `200`) and `skip` (default `0`)
- Response payload includes `items`, `count`, and `paging` (`skip`, `top`, `returned_count`,
  `total_matches`, `has_more`, `next_skip`).

## Host-Side Azure DevOps Current Iteration Backlog Tool

- Gateway tool name: `list_ado_current_iteration_backlog_items`.
- This is a convenience wrapper over `query_ado_backlog_items` that first resolves the team’s
  current iteration in Azure DevOps, resolves the team field values used by the team taskboard,
  and then runs a backlog query scoped to both the iteration path and the team field values.
- The tool is policy-gated: each session manifest must include
  `list_ado_current_iteration_backlog_items` in `allowed_tools` before calls are accepted.
- MCP arguments:
  - `project` (optional when `azure_devops_project` is configured)
  - `team` (optional; when omitted, the helper resolves the Azure DevOps project's default team)
  - optional filters: `area_path`, `states`, `types`, `tags_any`, `text_search`
    - `area_path` is applied in addition to the resolved team field values.
  - paging inputs: `top` (default `50`, max `200`) and `skip` (default `0`)
- Response payload includes:
  - `project` (resolved Azure DevOps project)
  - `team` (resolved Azure DevOps team)
  - `iteration_path` (resolved current iteration path from ADO team settings)
  - `items`
  - `count`

## Host-Side Azure DevOps Daily Status Report Tool

- Gateway tool name: `get_ado_daily_status_report_data`.
- The tool is policy-gated: each session manifest must include
  `get_ado_daily_status_report_data` in `allowed_tools` before calls are accepted.
- It resolves the team current iteration in Azure DevOps, fetches the work items with relation
  metadata, calculates item counts, PR coverage, effort totals, elapsed sprint time, and the
  percentages used by `templates/daily-status-report.html`.
- MCP arguments:
  - `project` (optional when `azure_devops_project` is configured)
  - `team` (optional; when omitted, the helper resolves the Azure DevOps project's default team)
  - optional filters: `area_path`, `states`, `types`, `tags_any`, `text_search`
  - optional type exclusions: `exclude_types`, defaulting to `["Task"]`
  - optional report overrides: `title`, `report_date`, `sprint_label`, `sprint_length`,
    `elapsed_sprint_percent`
  - optional elapsed-time options: `elapsed_time_mode` (`swedish_work_time` or
    `calendar_time`), `workday_start`, `workday_end`, and
    `include_common_swedish_non_working_days`
  - optional calculation overrides: `done_states`, `in_progress_states`, `effort_fields`,
    `max_items`
- By default, elapsed time uses Swedish working time in `Europe/Stockholm`, counts only weekdays
  inside the configured workday window, excludes Swedish public holidays, and also excludes common
  non-working eves such as Midsummer Eve, Christmas Eve, and New Year's Eve.
- By default, the query excludes `Task` work items so the report is based on backlog-level items
  rather than implementation subtasks. Pass `exclude_types: []` to include Tasks.
- Response payload includes:
  - `template_data`: the exact JSON object to paste into the template's `report-data` block
  - `template_data_json`: formatted JSON for agents that are editing the HTML directly
  - `percentages`: derived percentage values for auditability
  - `source`: resolved project/team/iteration metadata plus state and effort diagnostics

## Host-Side Azure DevOps Backlog Get-by-ID Tool

- Gateway tool name: `get_ado_backlog_item`.
- The tool is policy-gated: each session manifest must include `get_ado_backlog_item` in
  `allowed_tools` before calls are accepted.
- MCP arguments:
  - `id` (required numeric backlog item id)
  - `project` (optional when `azure_devops_project` is configured in provider config)
  - `include_comments` (optional, default `true`)
  - `include_relations` (optional, default `true`)
  - `include_updates` (optional, default `false`)
- Response payload includes normalized ADO work item metadata (`fields`, `relations`, `comments`,
  `updates`, `url`, `rev`, and normalized timestamps).

Example session tool allowlist entry:

```json
{
  "allowed_tools": [
    "post_ado_backlog_comment",
    "update_ado_backlog_text",
    "query_ado_backlog_items",
    "get_ado_daily_status_report_data",
    "get_ado_backlog_item"
  ]
}
```

Example MCP call:

```json
{
  "tool": "query_ado_backlog_items",
  "arguments": {
    "session_id": "real-session",
    "project": "example-project",
    "iteration": "Example\\Sprint 12",
    "states": ["New", "Active"],
    "types": ["Task", "Bug"],
    "tags_any": ["api", "backend"],
    "text_search": "retry policy",
    "top": 25,
    "skip": 0
  }
}
```

## Agent Prompt/Response Tools

- Gateway tool names:
  - `prompt_agent`
  - `respond_to_agent`
- Policy gating:
  - A session must include `prompt_agent` in `allowed_tools` to dispatch a prompt.
  - A session must include `respond_to_agent` in `allowed_tools` to answer one.
- Scope enforcement:
  - Dispatch is directory-scoped. The target session must be in the caller's effective
    directory scope or the gateway denies the request.
- Response token semantics:
  - `prompt_agent` returns a signed `response_token` bound to the issued `message_id` and
    `target_session_id`.
  - Token signing secret is loaded from the env var named by
    `agent_prompt_token_secret_env`.
  - Token validity is bounded by `agent_prompt_token_ttl_seconds`.
  - `respond_to_agent` consumes the token one time; replay attempts are denied.
  - Tampered/invalid/expired tokens are denied.
- CLI defaults and overrides:
  - `agent-sandbox-cli create-session` includes both tools by default.
  - Use `agent-sandbox-cli add-tool-permission` / `revoke-tool-permission` to grant or remove
    either tool per session.
- Least-privilege guidance:
  - Grant `prompt_agent` only to dispatcher sessions.
  - Grant `respond_to_agent` only to responder sessions.
  - Keep `directory_path` boundaries narrow so prompt routing cannot cross unrelated teams.

## Host-Side Retell Phone Calls

Use this only when agents should call configured directory contacts. The gateway owns the Retell API
key, contact resolution, API requests, webhook verification, and transcript store. Guests never
receive raw phone numbers or Retell credentials.

- Gateway tool names:
  - `list_contacts`
  - `make_phone_call`
  - `get_phone_call_result`
  - `update_ado_backlog_tags` when the call result should approve ADO items by tag
- Policy gating:
  - Add `list_contacts` to the session manifest `allowed_tools` to list visible contact ids and safe
    summaries.
  - Add `make_phone_call` to the session manifest `allowed_tools` to start calls.
  - Add `get_phone_call_result` to read call status and transcripts.
  - `get_phone_call_result` only returns calls owned by the same session and directory path.
  - Add `update_ado_backlog_tags` only for sessions that may change ADO item tags.
- Provider config with defaults:
  - `retell_api_base_url` defaults to `https://api.retellai.com`; leave the default in the GUI or
    omit the key from JSON unless Retell gives you a different endpoint or you intentionally route
    through a host-side proxy/mock.
  - `retell_api_key_env` defaults to `RETELL_API_KEY`; leave the default unless the host secret uses
    a different env-var name.
- Required provider config:
  - `retell_from_number`
- Required contact config:
  - `contacts.json`, with callable contact ids under `contacts`
  - Each callable contact needs one phone number marked `"primary": true`
- Required host secret:
  - Set the configured `retell_api_key_env` value in the gateway host environment. For systemd,
    repo-root `secrets.env` is already loaded by `agent-sandbox-gateway.service`.
- Required Retell agent prompt:
  - The Retell agent/LLM referenced by `retell_default_agent_id` must include
    `{{agent_question}}` in its Retell prompt or begin message.
  - `make_phone_call.prompt` is sent as `retell_llm_dynamic_variables.agent_question`; Retell only
    uses that value where the configured Retell prompt references the dynamic variable.
- Runtime state:
  - `runtime/retell-phone-calls-store.json`
- Webhook endpoint:
  - `POST /retell/webhook`
  - Verifies `X-Retell-Signature` with the configured Retell API key against the raw request body.
  - Handles `call_ended`, `call_analyzed`, and `transcript_updated`.

Example directory-scoped `providers.json` overlay:

```json
{
  "retell_api_key_env": "RETELL_API_KEY",
  "retell_from_number": "+15551230000",
  "retell_default_agent_id": "agent_123"
}
```

Example directory-scoped `contacts.json` overlay:

```json
{
  "contacts": {
    "ops_duty": {
      "name": "Ops Duty",
      "description": "Deployment window escalation contact",
      "email_addresses": ["ops@example.com"],
      "phone_numbers": [
        {"label": "mobile", "number": "+15551230001", "primary": true}
      ]
    }
  }
}
```

Example call sequence from an agent:

First list available contacts:

```json
{"session_id":"session-1"}
```

Use the returned contact `id` as the `contact` field:

```json
{"session_id":"session-1","contact":"ops_duty","prompt":"Ask whether the deployment window is still approved.","request_id":"deploy-window-1"}
```

Then poll:

```json
{"session_id":"session-1","request_id":"deploy-window-1"}
```

For verbal ADO approval flows, have the agent read the call transcript, identify the explicitly
approved work item IDs, and call:

```json
{"session_id":"session-1","id":15446,"project":"Example Industry APP","add_tags":["auto-dev-approved"]}
```

Audit/result behavior:

- Tool audit redacts `prompt` through the existing `redacted_keys` policy.
- `list_contacts` returns contact ids, names, descriptions, address counts, phone counts, primary
  phone labels, and whether the contact is callable. It does not return phone numbers or email
  addresses.
- Agent-facing results include contact id/name, status, timestamps, disconnection
  reason, transcript fields, call analysis, and sanitized Retell metadata.
- Agent-facing responses and audit results do not include `from_number`, `to_number`, configured
  contact phone numbers, or Retell credentials.

### Required provider config and environment

- Configure Azure DevOps work-item connectivity in effective provider config:
  - `azure_devops_organization_url` (for example: `https://dev.azure.com/example-org`)
  - `azure_devops_project` (default project when per-call/project-level values are not overridden)
  - `azure_devops_pat_env` (environment variable name that stores the PAT)
- Export the PAT only on the host (for example `export AZURE_DEVOPS_PAT=...`) and
  never in guest images or session manifests.

### Directory-scoped overrides

- The tool resolves provider config through directory inheritance (`root -> ... -> session.directory_path`).
- Use directory-scoped provider overlays when teams/projects differ by directory boundary, for
  example `root/team-a` vs `root/team-b`.
- Session calls use the resolved config for that session's `directory_path`, so the same MCP tool
  can target different Azure DevOps org/project defaults safely by directory scope.

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
  - Destination `channel_id` must be in `session.discord_channel_allowlist`, either directly or
    through a communication target ID with `write`.
  - `discord_operator_user_allowlist` does not override post destination checks.
- Reaction precedence for `add_message_reaction`:
  - `provider=discord` requires `channel_id` in `session.discord_channel_allowlist`, either
    directly or through a communication target ID with `reply`.
  - `provider=teams` requires `channel_target` in the effective Teams channel/chat allowlist:
    `session.teams_channel_allowlist` for channel targets or `session.teams_chat_allowlist` for
    chat targets. Communication target IDs with `reply` can be used in the same fields. Send tools
    can also target entries declared in `session.teams_user_allowlist` with
    `user:<user-id-or-upn>` or a `write`-capable Teams user communication target.
  - Message reads do not apply automatic read reactions; agents must call this tool explicitly.
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

## Operator Approval Notifications

Approval notifications are host-side operator alerts for newly created `ApprovalRequest` records.
They are separate from guest communication permissions: a session does not need Discord, Teams, or
email send allowlists for the host to notify an operator target.

1. Define one or more host-owned communication targets with the `notify` capability. In Admin Web,
   use Configuration -> Communications; the targets are stored in
   `runtime/communication-targets.db`. For example, a Teams channel target uses this payload shape:

   ```json
   {
     "targets": [
       {
         "id": "ops-approvals",
         "provider": "teams",
         "kind": "teams",
         "address": "team-id/channel-id",
         "policy": {"capabilities": ["notify"]}
       }
     ]
   }
   ```

2. Configure the owning directory policy with those target IDs:

   ```bash
   agent-sandbox-cli directories config-set \
     --runtime-dir runtime \
     --domain policy \
     root \
     '{"approval_notification_target_ids":["ops-approvals"]}'
   ```

   Child directories inherit the root value unless they replace the list in their own
   `policy.json` overlay. An empty list disables approval notifications.

   In Admin Web, the Access Policy tab exposes `approval_notification_target_ids` as an explicit
   list of known notify-capable communication targets with an `Add communication target` control
   instead of free-form text entry.

3. Have the guest create the approval request through `approval_submit`. The notification message
   includes the `request_id`, action/tool name, directory, session and VM context, expiry,
   `human_summary`, payload digest, and review commands. It does not include the approval payload.

4. Review with Admin Web Approvals or with:

   ```bash
   agent-sandbox-cli show-approval <request_id> --runtime-dir runtime
   ```

   The review payload includes notification status and delivery attempts, so failed delivery is
   visible without changing the approval state.

5. Decide through the existing audited path:

   ```bash
   agent-sandbox-cli approve <request_id> <approver> --approve --reason "approved reason" --runtime-dir runtime
   agent-sandbox-cli approve <request_id> <approver> --deny --reason "denied reason" --runtime-dir runtime
   ```

Delivery failure logs `metric_approval_notification` with the target id, request id, review command,
and error. It never auto-approves, denies, or blocks the pending approval record.

## Approval-Gated MCP Tool Invocations

Directory policy overlays and per-session policy can set `approval_required_tools` to require human
approval before selected MCP tools execute. This is an additional gate on top of `allowed_tools`: the
tool must still be allowed for the session, and `approval_submit` / `approval_check` are excluded so
agents can create and inspect approvals.

When a gated tool is called without `approval_request_id`, the gateway does not run the tool. It
creates a pending approval with action type `mcp_tool_invocation`, records the tool name, intended
effect, canonical payload digest, and an audited `pending_approval` tool-call event, then returns an
MCP tool error instructing the agent to retry the exact same payload with the generated
`approval_request_id` after an operator approves it.

Approval grants default to the restrictive shape: one use, 3600 second TTL, and exact payload
matching. Operators can override those settings per session when enabling `Require approval` for a
tool, and directory policy can set tool defaults or hard limits. A hard directory limit wins over
the session setting.

After approval, the agent retries the same tool call and includes `approval_request_id`. The gateway
executes only if the request is approved, belongs to the same session, targets the same tool, has not
expired, has remaining uses, and satisfies the configured payload matching mode. Denied, expired,
exhausted, wrong-tool, wrong-session, or changed-payload retries are blocked and audited with the
approval request id for review in Admin Web -> Tool Calls.

Directory policy can also set `mcp_tool_policies` for known tools. A rule can deny a tool outright,
force per-invocation approval, provide default grant settings, cap TTL or use count, or force exact
payload matching. Deny rules are inherited downward and cannot be reopened by a descendant directory
or by session policy.

### Tool Argument Policies

Directory policy overlays and per-session policy can also set `tool_argument_policies` to narrow the
arguments accepted by already-allowed MCP tools. These rules are additive across directory and
session sources; they do not replace inherited rules, and they do not create approval requests when
they deny a call.

This is a separate gate from `approval_required_tools`. A tool still must be listed in
`allowed_tools`, and a policy denial happens before approval gating, so rejected calls never produce
an approval request.

Supported operators are `required`, `allowlist`, `denylist`, `fixed`, `pattern`, and `enum`. Paths
use dotted object access with `[]` for list flattening, such as `tags[]` or
`metadata.labels[].name`. Missing optional paths pass for every operator except `required` and
`fixed`. Set `case_sensitive: false` when you need case-insensitive comparisons or regex matching.

Example policy that constrains `create_ado_backlog_item`:

```json
{
  "tool_argument_policies": {
    "create_ado_backlog_item": [
      {
        "id": "app-ado-work-item-constraints",
        "name": "APP backlog item constraints",
        "constraints": [
          {
            "id": "project-fixed",
            "path": "project",
            "operator": "fixed",
            "value": "Example Industry APP"
          },
          {
            "id": "type-allowed",
            "path": "type",
            "operator": "enum",
            "values": ["Task", "Product Backlog Item"]
          },
          {
            "id": "title-required",
            "path": "title",
            "operator": "required"
          },
          {
            "id": "iteration-required",
            "path": "iteration",
            "operator": "required"
          },
          {
            "id": "tags-denylist",
            "path": "tags[]",
            "operator": "denylist",
            "values": ["auto-dev-forbidden"]
          }
        ]
      }
    ]
  }
}
```

This policy still requires `allowed_tools` to include `create_ado_backlog_item`; argument policies
only narrow calls that are already allowed.

When a call violates argument policy, the gateway writes a `tool_call` audit event with
`decision: denied`, `error_class: tool_argument_policy_violation`, the policy rule id/name and
constraint id/path, and a redacted argument summary. The agent receives a clear `ToolError`, and the
denial is visible in Admin Web -> Tool Calls. Approval requests are not created for these denials.

## Communication File Transfer Runbook

Use this runbook only for dedicated operator channels/mailboxes that are already covered by the
normal Discord, Teams, Gmail, or Graph allowlists. File transfer policy is additive: a session must
still pass the provider destination/mailbox checks, and host-side credentials remain on the host.

### 1. Enable receive/send policy

Start from the conservative example and apply it to the directory that owns the test session:

```bash
agent-sandbox-cli directories config-set \
  --runtime-dir runtime \
  --domain policy \
  root \
  "$(cat config/communication-files/policy.json.example)"
```

This example enables inbound receive, disables outbound file send, and sets
`communication_file_require_approval_for_send: true` so future send enablement is never approval-free.
For a tightly scoped outbound-send test, temporarily overlay only the send flag after approval
reviewers are available:

```bash
agent-sandbox-cli directories config-set \
  --runtime-dir runtime \
  --domain policy \
  root \
  '{"communication_file_send_enabled":true,"communication_file_require_approval_for_send":true}'
```

Return to the conservative example immediately after the send test, or apply an explicit
`{"communication_file_send_enabled":false}` overlay.

### 2. Configure MIME, extension, and size limits

Set all three controls together so provider-supplied MIME metadata is not the only guardrail:

- `communication_file_max_bytes`: maximum staged artifact size in bytes. Use `1048576` (1 MiB) for
  live smoke tests.
- `communication_file_allowed_mime_types`: allow only expected content types, for example
  `text/plain`, `application/pdf`, `image/png`, `image/jpeg`, and `image/gif`.
- `communication_file_allowed_extensions`: allow matching filename extensions such as `.txt`,
  `.pdf`, `.png`, `.jpg`, `.jpeg`, and `.gif`.
- `communication_file_blocked_extensions`: keep executable/script extensions blocked even when an
  allowlist is later broadened.

After changing policy, create or update the session manifest so the session receives the effective
policy values and the expected provider allowlists.

### 3. Receive an inbound attachment

For each provider, use an allowlisted destination and a tiny fixture file such as `hello.txt` or a
small PNG:

1. Send the fixture into the dedicated Discord channel, Teams channel/chat, Gmail mailbox, or Graph
   mailbox.
2. Call `communication_list_attachments` with the correct provider scope:
   - Discord: `provider="discord"`, `message_id`, and `channel_id`.
   - Teams: `provider="teams"`, `message_id`, and `channel_target`.
   - Gmail/Graph email: `provider="email"`, `message_id`, and `mailbox_id`.
3. Call `communication_download_attachment` for one returned `attachment_id`.
4. Confirm the response includes an opaque `artifact.artifact_id`, normalized filename, byte count,
   MIME type, and SHA-256 digest.
5. If the guest needs the file, call `communication_copy_artifact_to_workspace`; confirm the
   destination is under `/home/agent/communication-artifacts/`.

### 4. Send an outbound file only with approval

Outbound sends must use staged artifact IDs. Do not send arbitrary guest paths.

1. Keep `communication_file_require_approval_for_send: true`.
2. If the agent created the file locally, call `communication_stage_workspace_file` first with a
   regular file path under `/home/agent`; use the returned opaque `artifact.artifact_id` for the
   send.
3. If sending an email attachment, keep draft creation separate from actual send: use
   `email_create_draft_with_attachments` first, then `email_send_approved_with_attachments` only
   after both `email_send_enabled: true` and a matching approval are present.
4. For Discord and Teams, submit/approve the exact payload expected by `discord_post_file` or
   `teams_post_file`, including destination, message, and `artifact_ids`; then pass the approval
   `request_id` to the send tool.
   For Azure DevOps PR/backlog tools, approve the exact comment/text payload and artifact IDs.
5. Disable `communication_file_send_enabled` again after the test unless the production change was
   intentionally approved.

### 5. Review audit logs

Use Admin Web -> Tool Calls for operator review, or read the audit log directly when scripting
against raw JSONL:

```bash
tail -n 100 runtime/audit.jsonl | jq 'select(.tool_name | test("communication_|discord_post_file|teams_post_file|email_.*attachments"))'
```

If the Tool Calls SQLite index needs a full refresh after deleting or repairing `runtime/audit.db`,
run:

```bash
agent-sandbox-cli rebuild-audit-index --config runtime
```

Review for:

- `decision == "allowed"` on expected list/download/copy/send calls.
- `decision == "denied"` when receive is disabled, send is disabled, a file exceeds
  `communication_file_max_bytes`, or MIME/extension policy blocks the file.
- `error_class == "adapter_http_error"` for provider API/auth/timeout failures.
- Provider target fields (`channel_id`, `channel_target`, or `mailbox_id`), `artifact_id`,
  `byte_count`, MIME type, filename, and approval request/decision fields on outbound sends.

### 6. Clean artifact storage

Staged metadata and blobs live under `runtime/communication-artifacts/`. The default artifact TTL is
seven days, and the store removes expired records opportunistically when it is used. To clean
expired artifacts on demand from the host, run:

```bash
python - <<'PY'
from pathlib import Path
from agent_sandbox.communication_artifacts import CommunicationArtifactStore
removed = CommunicationArtifactStore(Path("runtime")).cleanup_expired()
print(f"removed_expired_artifacts={removed}")
PY
```

For emergency cleanup of a dedicated test runtime only, stop the gateway/workers that might use the
store, archive any records needed for audit review, and remove only the contents of that runtime's
`communication-artifacts/` directory. Never delete artifacts from a shared production runtime while
active sessions may still reference them.

### 7. Provider smoke matrix

Use dedicated test channels/mailboxes only and keep files small:

- **Discord:** send `hello.txt` to the seeded test channel; list/download/copy it; optionally send
  it back with `discord_post_file` only after approval.
- **Teams:** send the same fixture to an allowlisted Teams test channel/chat; list/download/copy it;
  optionally send it back with `teams_post_file` only after approval.
- **Gmail:** send the fixture to the Gmail test mailbox; list/download/copy it; optionally create a
  draft with `email_create_draft_with_attachments`; avoid actual send unless `email_send_enabled`
  and matching approval are both intentionally enabled.
- **Graph:** repeat the Gmail checks against the Microsoft Graph test mailbox.

Pass criteria: all inbound receives are policy-allowed and audited, blocked file types/sizes are
denied and audited, outbound sends fail while send is disabled, and any intentional outbound send
succeeds only with an approval record that matches the exact payload.

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
   - `list_pull_requests(session_id, provider, repo, status?, creator_id?, reviewer_id?, top?, cursor?)`
     to enumerate PRs directly from the provider; `status` is forwarded to provider status criteria
     (Azure DevOps uses `searchCriteria.status`), and pagination resumes with `next_cursor`.
   - `get_pull_request(session_id, provider, repo, pr_id, include_reviewers?, include_commits?)`
     to fetch a single provider PR record, map stable top-level fields (title/status/branches/commits/reviewers),
     and preserve provider-specific details in `raw`.
3. Post threaded replies (when needed):
   - `post_pr_feedback_reply(session_id, provider, repo, pr_id, body_text, thread_id/comment_id, idempotency_key)`
4. Cursor advancement:
   - advance `last_read_cursor` only after local handling and any provider reply posting have completed durably.

### Guest state model

- Persist `last_read_cursor` per `session_id` in guest-local durable state.
- Never share cursors across sessions.
- Advance cursor only after the fetched items are durably handled.
- Keep handlers idempotent by feedback ID to tolerate retries/replays.

### Cron-driven pull request polling

Use `trigger:cron` with `query:pull-request:list` when a workflow needs one-shot
query control over open pull requests by provider state, branch, time window, or
review labels, then use `effect:pull-request:update` to claim or release work by
changing PR tags or metadata. For Azure DevOps, set `provider` to
`azure_devops` or `ado`; the node uses the directory-scoped ADO organization,
project, and PAT unless `organization_url` or `project` is supplied on the node.

Use `trigger:pull-request:new-feedback` or `trigger:pull-request:completed`
with `provider=ado` and `repo` when the Azure DevOps PR worker should own
polling directly. Setting `repo` enables explicit trigger criteria. Omitting
`repo` keeps the legacy `runtime/pr-map.json` compatibility path for existing
agent-created PR streams.

Use `query:pull-request:get` when a workflow needs a single normalized snapshot
of a specific pull request. The node accepts Azure DevOps `provider` values
`azure_devops`, `azure-devops`, and `ado`, plus GitHub `provider` `github`, and
it does not store cursor or workflow state. For Azure DevOps, it uses the
directory-scoped organization, project, and PAT unless `organization_url` or
`project` is supplied on the node. For GitHub, it uses the directory-scoped
`github_token_env` when configured and can fetch public data without a token.
Set `include_commits=true` to fetch commit details and `include_reviewers=true`
to fetch review API data; `include_reviewers=false` returns an empty reviewer
list.

Azure DevOps exposes PR labels through separate endpoints, so the `tags`
expression is applied after listing candidate PRs. The expression supports tag
names with case-insensitive `and`, `or`, `not`, and parentheses, for example
`(TagA or TagB) and not TagC`. Quote tag names that contain spaces or collide
with an operator. Keep polling bounded with `status`, `source_ref_name`,
`target_ref_name`, `min_time`/`max_time`, `top`, `skip`, or `cursor`.

Use `query:session:list` when the workflow should route by pack-owned session
metadata instead of a platform-specific PR binding. For PR collaboration packs,
common metadata conventions are keys such as `owns_pr`, `reviewing_pr`,
`pr_provider`, `pr_repo`, and `pr_project`, but those names are not special to
the platform. A cron workflow can list active sessions by generic metadata,
poll one stream with `query:pull-request:feedback`, store its cursor in
workflow-owned state, and enqueue prompts to every matched owner or reviewer
session. An explicit ADO PR trigger can use the same downstream routing pattern:
it emits normalized `pr` and feedback/completion payloads without `session_id`,
leaving session selection to workflow metadata queries. Existing guest tools
still require `metadata.pr_feedback_streams` on sessions that should call
`list_unread_feedback`, `list_all_feedback`, or `post_pr_feedback_reply`.

Workflow-owned chat polling should follow the same pattern with
`query:communication:messages`: read the local Teams or Discord inbox store,
persist the returned `next_cursor` in workflow-owned state, and keep the query
separate from the host wake workers. That query is read-only and does not call
live provider APIs.

### Wake workers

- `agent-sandbox-cli discord-wake` owns Discord inbox wakeups and resumes.
- `agent-sandbox-cli scheduled-wake` owns scheduled resume dispatch.
- Existing Azure DevOps PR feedback ingestion and PR completion polling are
  still owned by `agent-sandbox-ado-worker`. Legacy trigger mode writes
  `runtime/pr-feedback-store.json`, updates `runtime/pr-map.json` PR status
  fields, and publishes `trigger:pull-request:new-feedback` plus
  `trigger:pull-request:completed` events directly. Explicit trigger mode is
  selected by setting `repo` on the PR trigger; it lists candidate PRs from ADO
  using the trigger criteria, stores trigger-owned cursors outside
  `pr-map.json`, and emits normalized PR events without session bindings.
  `trigger:pull-request:new-feedback` can set `agent_author_names`,
  `agent_mention_names`, `agent_author_ids`, and `agent_mention_ids` so
  downstream gates can filter on
  `feedback.metadata.agent_directed` without running host scripts.
  Keep `pr-map.json` compatibility until a later cleanup removes the older path.
- Recommended service lifecycle commands:
  - `sudo systemctl start agent-sandbox-discord-wake.service`
  - `sudo systemctl stop agent-sandbox-discord-wake.service`
  - `sudo systemctl status agent-sandbox-discord-wake.service`
  - `sudo systemctl start agent-sandbox-scheduled-wake.service`
  - `sudo systemctl stop agent-sandbox-scheduled-wake.service`
  - `sudo systemctl status agent-sandbox-scheduled-wake.service`
- Discord wake cadence and resume behavior are controlled by `config/discord-wake/config.json`.
- Scheduled wake cadence and resume behavior are controlled by `config/scheduled-wake/config.json`.
- In both wake worker configs, `backoff_seconds` controls how long the worker waits before
  retrying after a whole-cycle exception. `cooldown_seconds` remains the per-session delay used
  after activity or busy-session checks, not the process-level exception retry delay.
- `discord_resume_prompt` remains directory-scoped and is surfaced in the admin GUI under Communications; it is used by the Discord wake worker.
- `scheduled_wake_prefix_text` lives in the scheduled-wake worker global config and is used as the shared prompt prefix for scheduled wake dispatch.
- MCP-triggered scheduled resumes are always dispatched with `resume_mode=same_context`.
- For `container_worker_vm` sessions, that same-context dispatch uses Codex resume-last behavior when a worker prompt does not already carry an explicit thread id.
- Admin-created recurring wake schedules can still request `resume_mode=new_context`, which starts a fresh Codex exec session on container workers.
- Busy container wakes queue the prompt for later dispatch and still count as handled for schedule advancement.
- Recurring cron wake schedules are consumed only after a successful host resume dispatch. Failed, timed-out, or missing-script attempts stay due/retryable and remain visible through wake dispatch history.
- Wake schedule CRUD is available in the CLI under `wake-schedules`:
  - create one-time wake: `agent-sandbox-cli wake-schedules create --runtime-dir runtime --session-id issue-11-session --resume-at 2026-04-01T10:30:00Z --prompt "Resume pending work."`
  - create recurring wake: `agent-sandbox-cli wake-schedules create --runtime-dir runtime --session-id issue-11-session --cron "*/10 * * * *" --prompt "Periodic check-in." --mode new_context`
  - edit existing wake: `agent-sandbox-cli wake-schedules edit <request-id> --cron "*/15 * * * *" --prompt "Updated prompt"`
  - delete wake: `agent-sandbox-cli wake-schedules delete <request-id>`
- Agent control state is mirrored on the host in `runtime/host-agent-control-state.json`; the wake workers check that file before probing or resuming a session so disabled agents stay ignored even when their VMs are powered off.
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
### Audit and log file locations

- Host feedback state: `runtime/pr-feedback-store.json` (ingested comments + cursors), `runtime/pr-reply-idempotency.json` (reply dedupe keys/results), `runtime/host-agent-control-state.json` (durable enabled/disabled state by VM).
- Host audit trail: `runtime/audit.jsonl` (canonical append-only audit journal, plus
  denials/errors). Admin Web Tool Calls indexes tool-call entries into `runtime/audit.db`
  for faster filtering and can rebuild that index from JSONL.

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

## Review Request Workflow

Use this run pattern when a host-side receptionist or chat-facing agent should enqueue PR review
work and let the trigger orchestrator start a temporary review VM.

### Host-side contract

- Enqueue review work with `request_pr_review(session_id, provider, repo, pr_id, ...)`.
- Consume queued review requests with `trigger:review:new-request`.
- Publish review output with:
  - `post_pr_comment(...)` for top-level PR conversation comments
  - `post_pr_inline_comment(...)` for one file/line finding; prefer this over writing
    `path line N` at the start of a top-level comment
  - `submit_pr_review(...)` for a summary plus optional batched inline findings
  - `post_pr_feedback_reply(...)` when replying to an existing feedback thread/comment

The review-request inbox is persisted in `runtime/review-request-store.json`. Records transition
through `pending`, `leased`, `dispatched`, `completed`, `failed`, or `cancelled`.
For Azure DevOps PRs, these publish tools can include staged `artifact_ids`; the gateway uploads
them as PR attachments and appends Markdown image/link references to the posted text.

### Recommended workflow graph

1. `trigger:review:new-request`
2. `effect:vm:create`
3. `effect:session:update-metadata`
4. `effect:vm:exec`
5. `effect:agent:prompt`
6. `gate:await-agent`
7. `post_pr_comment`, `post_pr_inline_comment`, or `submit_pr_review`
8. `effect:vm:stop`

Connect `effect:agent:prompt` or `effect:agent:enqueue-prompt` to `gate:await-agent`
through the prompt's `started` source port. Use the prompt's `completed` source
port for downstream work that should wait for the agent run itself to finish.

### Guest repo preparation

After the VM is created and the repo is cloned, prepare the checkout explicitly instead of
reviewing whatever the default branch clone happens to contain:

```bash
./scripts/guest/setup_review_repo.sh \
  --repo-path /home/agent/<repo-name> \
  --base-ref refs/heads/main \
  --head-ref refs/heads/review/topic
```

The helper:

- resolves explicit target/head refs to local refs under `refs/agent-sandbox/review/*`
- computes `REVIEW_DIFF_BASE_COMMIT` with `git merge-base` so normal review findings use
  `REVIEW_DIFF_BASE_COMMIT..REVIEW_HEAD_COMMIT` rather than target/head drift
- detaches the worktree onto the head commit
- hard-resets and cleans the worktree
- writes `.agent-sandbox/review-context.env` for downstream prompt or shell steps
- fails fast when an explicit `--base-commit` or `--head-commit` cannot be materialized

### Failure handling

- `request_pr_review` provider lookup failures:
  - fail fast and do not enqueue a partial request
  - verify host-side `GITHUB_TOKEN` or `AZURE_DEVOPS_PR_PAT`
- Trigger/provider retries:
  - retry leased-but-undispatched requests after lease expiry
  - do not overload the trigger cursor with timestamp-only semantics
- Publish retries:
  - reuse the same `idempotency_key` when retrying `post_pr_comment`, `post_pr_inline_comment`, or `submit_pr_review`
  - validate host-side provider credentials before replaying
- Inline PR comments:
  - use `post_pr_inline_comment(provider, repo, pr_id, path, line, body_text, ...)` for a single finding
  - pass `comments=[{"path": "...", "line": 123, "body": "..."}]` to `submit_pr_review` for batches
  - do not encode the file path and line number in `body_text`; the tool attaches the provider-native line context
  - for Azure DevOps, inline comments are resolved against the latest PR iteration changes and require a matching changed path; if a path cannot be safely anchored, post a top-level comment instead

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

## Host-Side Azure DevOps PR Worker And Work-Item Workflow Intake

- The scaffold includes `agent-sandbox-ado-worker` and the equivalent foreground CLI command
  `agent-sandbox-cli poll-ado-backlog`.
- The worker owns Azure DevOps PR feedback ingestion and PR completion polling for configured PR
  streams. Legacy triggers without `repo` use `runtime/pr-map.json`, update
  `runtime/pr-feedback-store.json`, record PR status in `runtime/pr-map.json`, and publish
  `trigger:pull-request:new-feedback` plus `trigger:pull-request:completed` events. PR triggers
  with `repo` use explicit ADO list criteria, keep cursor ownership in trigger cursor stores, and
  do not require or update `runtime/pr-map.json`.
- The worker no longer polls Azure DevOps work items, writes backlog YAML, checks processed work
  item state, or requires the work-item PAT just to run PR feedback/completion paths.
- Azure DevOps work-item intake is now workflow-owned. Use `trigger:ado:work-item-matching` for
  cursor-backed polling that emits raw work-item payloads on first match and when
  `System.ChangedDate` advances, or use `query:ado:work-items` for explicit one-shot queries.
  Wire either output into `effect:issue:upsert-ado-proxy` to write or refresh
  `runtime/local-issues.json` and `runtime/ado-work-items/work-item-*.yaml`.
- For terminal-PR cleanup workflows using legacy PR-map mode, wire
  `trigger:pull-request:completed.session_id` into `effect:agent:destroy.session_id`. For explicit
  PR trigger mode, route with `query:session:list` against session metadata and pass the selected
  session id downstream.
- A workflow-upserted ADO work item is represented in the unified local issue store as a proxy issue
  (`source: proxy_azure_devops`) keyed by local issue number with remote linkage in
  `metadata.remote` and a last-seen cache in `metadata.proxy_cache`.
- Re-polling the same remote issue updates the existing local proxy record/cache instead of
  creating duplicates, so Desktop and CLI views remain stable across workflow runs.
- `agent-sandbox-proxy-issue-sync` keeps all Azure DevOps proxy issues fresh independently of the
  intake workflow. It defaults to a five-minute interval/freshness window, uses batched
  `workitemsbatch` summary reads, leaves comments to on-demand detail reads, and writes missing or
  inaccessible item diagnostics into `metadata.proxy_sync` without deleting local issues.
- Directory-scoped `ado_worker` config can set `ado_repo_source` to control the repo source path
  or remote URL recorded on proxy issue records when workflow packs bind it into
  `effect:issue:upsert-ado-proxy.repo_source`.
- Preferred configuration now lives in directory domain `ado_worker` (`ado-worker.json`) so values
  inherit through directory ancestry and appear in the Admin GUI automatically.
- Global worker settings now live in `config/ado-worker/config.json` and are edited from the
  Admin GUI's Workers tab when the `ado` worker is selected.
- `ADO_*` env vars continue to work as compatibility fallbacks for one release and log deprecation
  warnings.
- Materialize `root/ado-worker.json` with `agent-sandbox-cli migrate-ado-worker-env-to-root` or
  `migrate-global-config-to-root` when you still use directory-scoped PR automation settings.

Foreground runbook:

```bash
set -a; source secrets.env; set +a
agent-sandbox-cli poll-ado-backlog --log-level INFO
```

Systemd runbook:

1. Ensure `runtime/directory-config/root/ado-worker.json` exists if you use directory-scoped PR
   automation settings.
2. Ensure `config/ado-worker/config.json` exists if you want to override the worker defaults.
3. Ensure `AZURE_DEVOPS_PR_PAT` or the configured `azure_devops_pr_pat_env` is available to the
   service user when Azure DevOps PR feedback/completion polling is enabled.
4. Source `secrets.env` or another secret env file if that is how you manage host secrets.
5. Start/inspect:

```bash
sudo systemctl start agent-sandbox-ado-worker.service
sudo systemctl start agent-sandbox-proxy-issue-sync.service
sudo systemctl status agent-sandbox-ado-worker.service
sudo systemctl status agent-sandbox-proxy-issue-sync.service
sudo journalctl -u agent-sandbox-ado-worker.service -f
```

Workflow work-item intake example:

```json
{
  "type": "trigger:ado:work-item-matching",
  "config": {
    "directory_path": "root/team-a",
    "states": ["Committed"],
    "tags_any": ["truman"],
    "top": 50
  }
}
```

```json
{
  "type": "effect:issue:upsert-ado-proxy",
  "config": {
    "directory_path": "root/team-a",
    "item": "${{trigger.work_item}}",
    "repo_source": "/srv/repos/example"
  }
}
```

Failure recovery:

- `--reprocess` and `--list-unprocessed` are deprecated with workflow-owned work-item intake.
- `--forget` remains available only for removing legacy processed-store entries during migration.
- To backfill, run `query:ado:work-items` and feed its `items` array into
  `effect:issue:upsert-ado-proxy.items`.

- If the service is crash-looping, validate config and PAT availability with a foreground run,
  then restart the unit:

  ```bash
  sudo systemctl restart agent-sandbox-ado-worker.service
  ```


### CI run completion polling

- `trigger:ci:run:completed` is a host-side polling trigger for CI run completion events.
- GitHub Actions polling resolves the token from the trigger provider's configured
  `github_token_env`; Azure DevOps polling resolves the PAT from the configured
  `azure_devops_pr_pat_env`.
- The provider stores its durable cursor in `runtime/trigger-cursors-ci-runs.json` and suppresses
  historical runs on the first poll unless `include_existing=true`.
- The first implementation only reads completed runs. It does not fetch logs, jobs, artifacts,
  or queue reruns.
- When operators need backfill behavior, set `include_existing=true` explicitly and narrow the
  trigger with `branches`, `conclusions`, and provider-specific repo or pipeline selectors.


## Host-Side Trigger Orchestrator

- The scaffold includes `agent-sandbox-trigger-orchestrator`, a singleton host worker that polls
  configured triggers and persists trigger events to a durable queue.
- Global worker config lives at `config/trigger-orchestrator/config.json`.
- For orchestration-only production deployments, start from `config/trigger-orchestrator/orchestration-core.config.json.example` (uses `runtime/orchestration-core` + `profiles/core-minimal`).
- Runtime defaults (when config fields are null/omitted):
  - queue: `runtime/trigger-orchestrator-events.json`
  - cursor: `runtime/trigger-orchestrator-cursors.json`
  - lock: `runtime/trigger-orchestrator.lock`
  - wake socket: `runtime/trigger-orchestrator.wake.sock`
- The trigger and effect workers remain separate processes, but host-owned ingress sends best-effort
  wake signals over derived Unix sockets (`runtime/trigger-orchestrator.wake.sock` and
  `runtime/effect-orchestrator.wake.sock`). The durable trigger queue remains the source of truth; a
  missing wake socket falls back to the normal polling interval.

Run one cycle for validation/debug:

```bash
agent-sandbox-trigger-orchestrator --runtime-dir runtime --once --log-level DEBUG
```

Run continuously under systemd:

```bash
sudo systemctl start agent-sandbox-trigger-orchestrator.service
sudo systemctl status agent-sandbox-trigger-orchestrator.service
sudo journalctl -u agent-sandbox-trigger-orchestrator.service -f
```

### APP demo provisioning workflow

- `trigger:script-run-request` is wired to accept exactly one host-approved provisioning wrapper:
  `provision_exampleindustry_app_demo_environment.sh`.
- The checked-in workflow no longer runs a host-side Bash provisioning script. It queues a typed
  Azure DevOps pipeline with `effect:ado:pipeline-run`, waits for the terminal result, and then
  maps the deterministic demo URLs and aliases with `gate:map`.
- The Azure DevOps pipeline owns Azure resource creation, deployment, and any cloud-side
  prerequisites. The effect uses the directory-scoped Azure DevOps provider configuration and PAT
  to queue and poll the pipeline.
- The first request parameter must be a lowercase alphanumeric resource name. The gate rejects
  names outside `^[a-z0-9]+$` so `gate:map` can safely derive DNS labels without string
  normalization helpers.
- The mapped `session_metadata_patch` object appends two relay targets under
  `metadata.http_targets.targets`: `<environment-alias>-api` for backend/API traffic and
  `<environment-alias>-ui` for browser/DOM traffic. It keeps the APP-specific
  `metadata.example_app.environments.<environment-alias>` record for convenience and refreshes
  `metadata.example_app_last_provision` with the selected environment alias plus its API alias, UI
  alias, `app_base_url` (API base URL), and `app_public_url` (public UI URL).
- The trigger graph writes that metadata patch into the originating session and resumes the same
  session with the provisioning pipeline run URL in the follow-up prompt.
- Guests authenticate to the selected APP environment themselves through the relay-backed URL for
  the appropriate API or UI alias. The generic HTTP target relay no longer injects host-side APP
  authorization headers.
- Container-backed agents receive `HTTP_TARGET_RELAY_BASE_URL` and should build relay URLs as
  `$HTTP_TARGET_RELAY_BASE_URL/session/<session-id>/target/<alias>/` rather than calling the public
  Azure URL directly.

The older `scripts/provision_exampleindustry_app_demo_environment.sh` wrapper is retained only for
compatibility with older live workflow packs and should not be used for new provisioning graphs.

### Teams ingest worker

- `trigger:teams:new-message` and `trigger:teams:direct-message` events are produced by a
  host-side Teams ingest worker that polls Microsoft Graph on the host and writes into the Teams
  message store consumed by trigger orchestration.
- The ingest worker uses the same delegated Teams auth settings described above.
- `trigger:communication:new-message` is the provider-neutral inbound chat trigger for declared
  communication targets. Configure it with explicit `communication_target_ids`; each selected
  target must be a read-capable Teams channel/chat or Discord channel target in the trigger's
  `directory_path`. The trigger consumes the existing Teams and Discord inbox stores, so no
  separate ingest worker or provider credential is configured for this node.
- Direct-message triggers require `sender_user_ids`, which accepts Graph user IDs, UPNs, or mail
  addresses so only configured one-on-one senders can wake workflows.
- This polling path is independent from the wake workers; Discord/scheduled wake behavior now stays with
  `agent-sandbox-discord-wake` and `agent-sandbox-scheduled-wake` while Azure DevOps PR feedback ingestion stays with
  `agent-sandbox-ado-worker`.

Git hook ingestion path:

- `scripts/handle_git_push_trigger.sh <bare-repo-path>` is intended for bare repo
  `post-receive` hooks.
- It reads `<old> <new> <ref>` updates from stdin, matches configured `trigger:git:push`
  triggers, and appends deduplicated pending records into the same orchestrator queue.
- Run `PYTHONPATH=src python -m pytest -q tests/test_trigger_conformance_suite.py` for full
  trigger contract regression coverage and the generated trigger conformance report.
- Run `PYTHONPATH=src python -m pytest -q tests/test_effect_conformance_suite.py` for the paired
  effect contract regression coverage and the generated effect conformance report.
- See [conformance-reports.md](conformance-reports.md) for the shared Admin Web conformance report
  view and the exact runtime artifact names it reads.

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

Before running install mode, ensure preflight is clean (or only warning-only), then:

1. Copy `config/firewall.env.example` to `config/firewall.env` if needed.
2. Ensure `runtime/directory-config/root/ado-worker.json` exists if you use directory-scoped PR
   automation settings.
3. Ensure the needed service env vars are available. The rendered units will load optional
   per-service `config/*.env` files and optional repo-root `secrets.env`.
4. Ensure `OPENAI_API_KEY` is available for the OpenAI relay and `AZURE_ARTIFACTS_PAT` when the
   NuGet relay needs private Azure Artifacts access. Additional directory-specific key env vars
   should be supplied through `config/admin-web.env`, `secrets.env`, or the service manager
   environment. The HTTP
   target relay is auth-transparent and exists to enforce destination allowlisting for approved external web
   endpoints. In the APP demo workflow, guests authenticate to APP interactively or with
   guest-managed bearer tokens/cookies through that relay path.
5. For private PR feedback polling, ensure the service environment exposes `GITHUB_TOKEN`
   and/or `AZURE_DEVOPS_PR_PAT` and ensure the effective `root` provider config sets the correct
   `azure_devops_organization_url`, `azure_devops_project`, and `azure_devops_pr_pat_env`.
6. Start and inspect services:

```bash
sudo systemctl start agent-sandbox-firewall.service
sudo systemctl start agent-sandbox-gateway.service
sudo systemctl start agent-sandbox-openai-relay.service
sudo systemctl start agent-sandbox-nuget-relay.service
sudo systemctl start agent-sandbox-http-target-relay.service
sudo systemctl start agent-sandbox-ado-worker.service
sudo systemctl start agent-sandbox-proxy-issue-sync.service
sudo systemctl start agent-sandbox-discord-ingest.service
sudo systemctl start agent-sandbox-discord-wake.service
sudo systemctl start agent-sandbox-scheduled-wake.service
sudo systemctl start agent-sandbox-teams-ingest.service
sudo systemctl start agent-sandbox-teams-meeting-ingest.service
sudo systemctl start agent-sandbox-effect-orchestrator.service
sudo systemctl start agent-sandbox-trigger-orchestrator.service
sudo systemctl status agent-sandbox-firewall.service
sudo systemctl status agent-sandbox-gateway.service
sudo systemctl status agent-sandbox-openai-relay.service
sudo systemctl status agent-sandbox-nuget-relay.service
sudo systemctl status agent-sandbox-http-target-relay.service
sudo systemctl status agent-sandbox-ado-worker.service
sudo systemctl status agent-sandbox-proxy-issue-sync.service
sudo systemctl status agent-sandbox-discord-ingest.service
sudo systemctl status agent-sandbox-discord-wake.service
sudo systemctl status agent-sandbox-scheduled-wake.service
sudo systemctl status agent-sandbox-teams-ingest.service
sudo systemctl status agent-sandbox-teams-meeting-ingest.service
sudo systemctl status agent-sandbox-effect-orchestrator.service
sudo systemctl status agent-sandbox-trigger-orchestrator.service
```

The Discord wake and scheduled wake units must run as the same operator user as the
gateway and orchestrator services so they can read the operator user's SSH keys and
write operator-owned wake state. After installing units, verify the rendered service
users:

```bash
systemctl show -p User agent-sandbox-discord-wake.service
systemctl show -p User agent-sandbox-scheduled-wake.service
```

Both commands should report the operator account used for deployment, not an empty
root/default user.

## Local Git Remote Flow

For the common issue workflow, create the VM first:

```bash
MSSQL_SA_PASSWORD='your-strong-password' ./.venv/bin/agent-sandbox-cli create-issue-vm 14084 --dotnet --nuget /path/to/project-or-solution.slnx --mssql --mssql-init-root /path/to/mssql-init --playwright
```

Then clone the repo and apply repo-specific bootstraps as needed:

```bash
./.venv/bin/agent-sandbox-cli clone-repo-into-vm vm-14084 --repo-source /path/to/local/repo
./.venv/bin/agent-sandbox-cli bootstrap-python-in-vm vm-14084 --host-checkout /path/to/local/repo --target-dir /home/agent/$(basename /path/to/local/repo)
./.venv/bin/agent-sandbox-cli bootstrap-frontend-in-vm vm-14084 --host-checkout /path/to/local/repo --install-root /path/to/local/repo/Src/Clients
```

`create-issue-vm` still resolves issue-scoped Git settings from the `root` issue setup config,
including `AGENT_SANDBOX_GIT_ROOT` and `AGENT_SANDBOX_GIT_SSH_ALIAS`, so later repo clone steps
can use the issue-numbered host mirror.
The CLI path wraps `scripts/setup_vm.sh`; that setup script remains a standalone operator/CLI
surface and is not invoked by the ADO worker.

Toolchain bootstraps are now split by concern:

- `create-issue-vm --dotnet` runs `bootstrap_dotnet_vm.sh`
- `create-issue-vm --nuget PATH` runs `bootstrap_nuget_vm.sh` and prewarms the shared NuGet cache
- `create-issue-vm --mssql` runs `bootstrap_mssql_vm.sh` and requires `MSSQL_SA_PASSWORD`
- `create-issue-vm --playwright` runs `bootstrap_playwright_vm.sh`
- `bootstrap-python-in-vm` runs `bootstrap_python_vm.sh` against a cloned repo
- `bootstrap-frontend-in-vm` runs `bootstrap_frontend_deps_vm.sh` against a cloned repo

When frontend bootstrapping is enabled, each install root must be inside the selected host
checkout and already contain `package.json` plus `node_modules`.

When `--python` is enabled, the guest clone also gets a repo-local `.venv`. Activate it with
`. .venv/bin/activate`, then run `python -m pytest -q` and `python -m ruff check` from inside
the guest clone. These checks validate the repo contents in-guest, but they do not replace
host-side libvirt, firewall, or systemd verification for this scaffold.

VM and container agents share `/artifacts` as the workflow-readable artifacts
folder. Container workers mount it from durable session storage; VM setup creates
it as an agent-writable guest directory and exports `AGENT_SANDBOX_ARTIFACTS_DIR`.
Use `query:agent:artifact-text` with a session id and relative path to read a
UTF-8 text file from that folder.
Use `effect:artifact:stage` to copy a file or directory from `/home/agent` into
that folder, then `effect:artifact:publish-artifacts` to publish the full folder
as an immutable keyed `tar.gz` archive under `runtime/published-artifacts/`.
Use `effect:artifact:import-published` to extract a previously published archive
back into a selected session's `/artifacts` folder; imports fail on existing
files unless `overwrite` is set.
The Admin Web Artifacts tab can resolve archives by key, list them by publish
time, download the stored archive, and manually upload a single file as a
published artifact archive.

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
agent-sandbox-cli directories config-set --domain issue_setup root "{\"git_root\": \"$PWD/git-remotes\", \"git_host\": \"192.168.250.1\", \"git_user\": \"$(whoami)\", \"git_ssh_alias\": \"agent-sandbox-git-host\"}"
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
The cloned repo also gets `AGENT_TASK.yaml` in the repo root, a local `work` branch created from
the configured base branch, repo-local `git sync-base` and `git push-agent` aliases, and the
guest-home `AGENTS.md` guidance file during the repo-clone step. `sync-base` merges the latest
configured base branch through `origin`; `push-agent` verifies the destination and base branches
before pushing.
Mirror freshness flags are accepted and forwarded through the git-setup helper and the repo-clone
step:

```bash
./scripts/setup_issue_git_vm.sh --remote-name origin vm-real 14084 /path/to/local/repo
./.venv/bin/agent-sandbox-cli clone-repo-into-vm vm-real --repo-source /path/to/local/repo
./.venv/bin/agent-sandbox-cli clone-repo-into-vm vm-real --repo-source https://example.com/org/repo.git
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
- `AGENT_SANDBOX_PR_NOTES_REF` (optional): host-wide override for the notes ref used to read
  agent summary markdown from the promoted commit. New repo clones configure
  `agent-sandbox.prNotesRef` as `refs/notes/agent-pr/<session-hash>`, where
  `<session-hash>` is the first 12 hex characters of `sha256(session_id)`. Older checkouts
  still default to the legacy `refs/notes/agent-pr` ref.
- `AGENT_SANDBOX_PR_BODY_MAX_BYTES` (optional, default `60000`): hard byte cap applied to the
  composed PR body before creating/updating the PR.
- `AGENT_SANDBOX_PR_BODY_ARTIFACT_IDS` (optional): comma-, semicolon-, or newline-separated staged
  artifact IDs to append to the Azure DevOps PR description. The same behavior is available with
  repeated `open_agent_pr.sh --body-artifact-id <artifact-id>` options.

PR creation behavior is also directory-scoped via `ado-worker.json` overlays:

- `create_prs_automatically` (default `true`): when `false`, hook automation still promotes and
  pushes review branches but skips `--create-pr`.
- `create_prs_as_draft` (default `false`): when `true`, Azure DevOps PRs are opened as drafts.

Optional host-wide runtime overrides are available:

- `AGENT_SANDBOX_AUTO_CREATE_PRS` to force-enable/disable PR creation across all directories.
- `AGENT_SANDBOX_CREATE_PRS_AS_DRAFT` to force draft vs ready-for-review for Azure DevOps PRs.

Per-session overrides are supported through session metadata in `runtime/sessions.json`:

- top-level metadata keys: `create_prs_automatically`, `create_prs_as_draft`
- or nested metadata object: `pr_automation.create_prs_automatically`, `pr_automation.create_prs_as_draft`

To migrate the legacy repo-level `config/pr-template.md` into the root directory-scoped template,
run:

```bash
agent-sandbox-cli migrate-pr-template-to-root --config config/gateway.yaml
```

On guest issue VMs, prefer `git agent-pr-note set` / `git agent-pr-note append` instead of raw
`git notes add -f`. The helper keeps an additive branch-local summary cache. Agents should use
`git sync-base` when they need to merge the latest configured base branch, such as `main`, because
their local coding branch is `work`. `git push-agent` fetches the configured destination and base
branches through the guest-visible mirror before pushing. If either branch has commits missing from
`HEAD`, `git push-agent` refuses the push and prints the merge command the agent should run. Once
those branches are contained, it reattaches the full summary to the current `HEAD` before it pushes
the configured `agent-sandbox.prNotesRef` notes ref.

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

- local repo source path: default is **upstream fetch + divergence check**
- remote URL source: default is **always fetch latest refs** into cache before mirror update
- `--sync-remote`: explicitly force that fetch/divergence-check behavior for local source
- `--no-sync-remote`: skip upstream fetch for local/offline-only mirroring
- `--force-local`: continue even when local default branch is behind upstream
- optional pinning: `--branch <name>` or `--refspec <src:dst>`

Examples:

```bash
./scripts/mirror_issue_repo.sh --remote-name origin 14084 /path/to/local/repo
./scripts/mirror_issue_repo.sh --no-sync-remote 14084 /path/to/local/repo
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

For Azure DevOps origins, set `AZURE_DEVOPS_PR_PAT` in the environment so the PR helper and
mirror-refresh path can authenticate automatically against the Azure-hosted canonical repository. When
`open_agent_pr.sh` has an Azure work item id, or a session id like `issue-14084-...`, it also
associates the PR with that work item in Azure DevOps.

## Live Smoke Tests

Use [live-smoke-tests.md](live-smoke-tests.md) for the
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
- Keep Retell API keys in repo-root `secrets.env`, the service manager environment, or another
  host-only secret source; provider config should contain only the env-var name.
- Rotate Discord and email credentials independently from VM lifecycle.
- Keep Discord bot tokens only in host environment files or shell context; do not write them into
  guest images, cloud-init, or session manifests.
- Review Admin Web -> Tool Calls for privileged activity; it reads the indexed
  `runtime/audit.db` view of the configured `audit_log_path` (`runtime/audit.jsonl`
  by default), shows redacted payloads, and falls back to the JSONL journal if the
  index is unavailable.
- Keep VM disks and seed ISOs out of home directories when using `qemu:///system`; the default scripts now place them under `/var/lib/libvirt/images/agent-sandbox`.


## Observability Markers Runbook

The gateway and effect providers emit structured log markers for agent orchestration troubleshooting:

- `metric_chat_session_mapping_created` / `metric_chat_session_mapping_reused`
  - Emitted during chat assistant session resolution.
  - `libvirt_vm` uses a global mapping owner (`mapping_user_id=global`); `container_worker_vm` uses per-user mappings.
- `metric_agent_create_backend_path`
  - Emitted by `effect:agent:create` before backend dispatch.
  - Confirms whether provisioning went through `libvirt_vm` or `container_worker_vm`.
- `metric_prompt_enqueue_success` / `metric_prompt_enqueue_failed`
  - Emitted during queue writes for deferred/resume prompt dispatch.
  - Includes `latency_ms`, queue key, and failure class when enqueue fails.
- `metric_mcp_tool_decision`
  - Emitted for each MCP tool decision with `outcome=allowed|denied|error`.
  - Includes tool name and policy/runtime reason for deny/error cases.

### Recovery steps

1. **Chat stuck on first message**
   - Opening the Admin Web assistant should create or reuse the assistant
     session mapping before the first message is sent. Check for
     `metric_chat_session_mapping_created` followed by subsequent `..._reused`
     entries.
   - If missing, verify runtime write permissions to `runtime/chat-assistant-session-mappings.json`.
2. **Unexpected backend path**
   - Filter logs for `metric_agent_create_backend_path` by `session_id`/`vm_name`.
   - Confirm session metadata `execution_backend` and effective directory provider config.
3. **Prompt not dispatched after enqueue**
   - Verify `metric_prompt_enqueue_success` and inspect `latency_ms`.
   - If only `metric_prompt_enqueue_failed` appears, inspect the failure `reason` class and runtime store health.
4. **Tool calls denied unexpectedly**
   - Filter by `metric_mcp_tool_decision` and inspect `reason`.
   - Compare with session `allowed_tools` and any provider-specific allowlists in session policy metadata.
