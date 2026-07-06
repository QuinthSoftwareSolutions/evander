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

If Admin Web is part of the deployed surface, treat `evander-admin-web` as a managed host
service. Keep active Admin Web passwords and session secrets in
`/etc/evander/local-secrets.env` or another service-manager secret source, keep
`config/admin-web.env` for non-secret listener/auth mode settings, and do not expose
the service beyond localhost without HTTPS, secure cookies, and ingress policy.
By default, Admin Web uses the bundled Evander and Quinth Software Solutions branding in
`frontend/workflow-editor/branding`. Use the Admin Web branding variables in that env file when a
deployment needs a different browser title, shell header title, or logo set; relative logo paths
can be served from `EVANDER_ADMIN_WEB_BRANDING_ASSET_DIR` through the proxied
`/api/admin/branding/assets/...` route.

## Bootstrap Flow

1. Install Python dependencies with `uv`.
2. Run `evander-cli init-state --runtime-dir runtime`.
3. Edit host-global bootstrap settings in `runtime/host-config.json`.
4. Configure `root` directory overlays directly with the admin UI or `evander-cli directories config-set`.
5. If you are importing an older install, run `evander-cli migrate-global-config-to-root --config /path/to/legacy-gateway.yaml` once to seed `host-config.json` plus `root` from the legacy YAML file.
6. Create the libvirt network with `./scripts/create_libvirt_network.sh`.
7. Apply guest forwarding policy with `./scripts/apply_firewall.sh`.
8. Download the Ubuntu cloud image with `sudo ./scripts/create_base_vm.sh`.
9. Create a session manifest with `evander-cli create-session`.
10. Launch a VM with `sudo ./scripts/launch_agent_vm.sh`.
10. Optionally bootstrap a provider runtime in the guest (for example `./scripts/bootstrap_codex_vm.sh <vm-name>` or `./scripts/bootstrap_copilot_vm.sh <vm-name>`).
11. Optionally bootstrap a Python repo into a guest-local virtualenv with `./scripts/bootstrap_python_vm.sh <vm-name> <host-checkout-root> <guest-repo-root>`.
12. Optionally bootstrap generic guest Python packages into an offline tools virtualenv with `./scripts/bootstrap_python_packages_vm.sh <vm-name> <package-spec> [package-spec ...]`.
13. Optionally install the host `.NET` SDK into the guest with `./scripts/bootstrap_dotnet_vm.sh <vm-name>`.
14. Optionally prewarm the shared host NuGet cache for libvirt VMs with `./scripts/prewarm_nuget_cache.sh <path> [more paths...]`.
15. Optionally install NuGet relay sources into the guest with `./scripts/bootstrap_nuget_vm.sh <vm-name>`.
16. Optionally provision a guest-local Microsoft SQL Server instance into the guest with `MSSQL_SA_PASSWORD=... ./scripts/bootstrap_mssql_vm.sh <vm-name>`.
17. Optionally install Playwright into the guest with `./scripts/bootstrap_playwright_vm.sh <vm-name>` for rendered-DOM debugging.
18. Optionally create a local bare Git remote with `./scripts/create_git_remote.sh <repo-name>` or mirror the default host repo with `./scripts/mirror_issue_repo.sh <issue-number>` if the VM should push code back to the host.
19. Use [live-smoke-tests.md](live-smoke-tests.md) when you want to verify live Discord and provider-backed email behavior against real credentials.
20. Use [libvirt-vm-template-operations.md](libvirt-vm-template-operations.md) when you need to prepare or register a non-default libvirt base image.

- The launch script resizes each overlay disk to `20G` by default.
- The launch script also exposes a read-only shared NuGet cache via `virtiofs` at `/opt/evander/nuget-cache-ro` inside the guest.
- Override per VM with `DISK_SIZE_GB=40 sudo ./scripts/launch_agent_vm.sh ...`.

## First Troubleshooting Step

Before debugging individual scripts or services, run the consolidated host status command:

- Human-readable summary:
  - `evander-cli status --runtime-dir runtime`
- Machine-readable output:
  - `evander-cli status --runtime-dir runtime --json`

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
- `model-usage.db`: durable provider-neutral model usage store queried by
  `query:model-usage:list`; it records only usage/provider/correlation metadata
  and does not persist prompt or completion text.
- `operator-steering-notes.db`: host-side operator steering notes keyed by
  session and prompt dispatch for legacy relay-delivered steering. Container
  worker steering interrupts the active worker execution and resumes it as a new
  worker job instead of waiting for the next model request.
- `config/model-pricing.openai.json`: versioned local OpenAI pricing snapshot
  used by `query:model-usage:list` for query-time cost estimates. Override with
  `EVANDER_MODEL_PRICING_PATH` when operating from a different rate card.
  Admin Web exposes the loaded catalog status and structured editor on the
  Configuration page and saves changes back to the active catalog path.
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

## Team MCP Cutover Runbook

Use this runbook when cutting a local Team Add-on deployment over from the
legacy `/artifacts/team-action.json` action path to Team-owned MCP tools. The
post-cutover steady state has no legacy/native action-surface flag: Team MCP
tools are the only Team action path.

1. Stop services that can lease, complete, ingest, or display Team work while
   state is being inspected:

   ```bash
   sudo systemctl stop evander-trigger-orchestrator.service
   sudo systemctl stop evander-effect-orchestrator.service
   sudo systemctl stop evander-admin-web.service
   sudo systemctl stop evander-discord-wake.service evander-scheduled-wake.service
   sudo systemctl stop evander-discord-ingest.service evander-teams-ingest.service evander-teams-meeting-ingest.service
   ```

   Stop only the wake/ingest services enabled on the host. If Admin Web must
   remain online for read-only operator coordination, do not use it to edit Team
   state until the cutover is complete.

2. Inspect active Team leases in `runtime/addons/team-addon/state.db` before
   changing schema or workflow packs:

   ```bash
   sqlite3 runtime/addons/team-addon/state.db ".tables"
   sqlite3 runtime/addons/team-addon/state.db "SELECT * FROM active_leases;"
   ```

   If the schema has a different active-lease table name in the current build,
   inspect the table list and query the Team runtime lease table that contains
   session, dispatch, and agent identifiers.

3. Manually apply or intentionally discard any pending `team-action.json`
   artifacts worth preserving. Prefer applying only actions whose dispatch id
   still matches the active Team lease. Record discarded artifacts in an
   operator note or incident log so missing state changes are intentional rather
   than silent.

4. Back up the Team state database:

   ```bash
   cp runtime/addons/team-addon/state.db \
     "runtime/addons/team-addon/state.db.pre-team-mcp-cutover.$(date -u +%Y%m%dT%H%M%SZ)"
   ```

5. Apply the state/schema migration for the build being deployed. If the
   migration is part of service startup, run the documented one-shot migration or
   start the service once in a maintenance window and confirm it exits cleanly
   before reopening wake traffic.

6. Restart services with Team MCP tools as the only action path:

   ```bash
   sudo systemctl start evander-effect-orchestrator.service
   sudo systemctl start evander-trigger-orchestrator.service
   sudo systemctl start evander-admin-web.service
   sudo systemctl start evander-discord-ingest.service evander-discord-wake.service
   sudo systemctl start evander-scheduled-wake.service
   sudo systemctl start evander-teams-ingest.service evander-teams-meeting-ingest.service
   ```

   Start only services that are configured on the host. The Team runtime workflow
   pack should finalize persistent sessions with
   `effect:team:finalize-agent-dispatch` using `session_id`, `dispatch_id`, and
   `agent_id`; ephemeral worker completion should use
   `team_report_ephemeral_result`.

7. Run focused smoke checks:

   ```bash
   evander-cli status --runtime-dir runtime --json
   ```

   Confirm the migration grep from the implementation plan shows only expected
   migration-history, deleted-code, or removal-test references. In Admin Web,
   confirm the Team Add-on loads, a normal persistent Team wake sees ordinary
   Team MCP action tools, Team Chat wakes can call
   `team_reply_to_chat`, governance wakes only see governance tools, and
   ephemeral workers only report through `team_report_ephemeral_result`.

Troubleshooting:

- **Active lease mismatch** — if a completed session's dispatch does not match
  the active Team lease, do not force it through Team finalization. Inspect the
  active lease row, the session manifest `metadata.team_addon`, and the trigger
  event that reported completion. Clear or repair only the stale lease after
  preserving any useful operator context.
- **Idempotency conflict** — `request_id` identifies one create, append,
  message, or async-intent operation. Reusing the same key with different
  payload is rejected without mutation. Retry with the original payload to read
  the existing result, or issue a new `request_id` when the operator really
  wants a distinct action.
- **Async intent recorded but effect pending** — intent tools may commit Team
  state before external work finishes. Inspect the matching activity/audit row,
  outbox event, and effect-orchestrator cursor/execution logs before retrying.
  Do not mark the Team action complete until the external effect records its
  result.
- **Artifact publication cleanup/orphans** — deliverable `artifact_paths` are
  published host-side before the final Team mutation. If publication succeeds
  but the mutation fails, replay the same idempotent request when the payload is
  still valid; otherwise record the orphaned artifact-space ref and remove or
  quarantine the published files according to the deployment's artifact
  retention policy.

## Model Usage Querying

Use `query:model-usage:list` in Admin Web when you need to inspect persisted
model inference activity. The node returns JSON with `items` and `count`, and
supports filters for:

- `session_id`, `workflow_run_id`, `effect_id`, and `dispatch_id`
- `directory_path` with optional descendant matching
- `provider`, `model`, `agent_harness`, and `execution_backend`
- `normalized_usage_status` and `request_status`
- `occurred_after`, `occurred_before`, and `limit`

Each item includes the normalized token fields, correlation fields, the raw
usage metadata envelope, and OpenAI-first query-time cost estimate fields when
`include_costs` is enabled. The query also returns aggregate token totals and
estimated cost totals for the matched rows.

Cost estimates are derived from the local pricing catalog, model, event time,
input/output token split, and cached-input token details when the provider
reported them. They are operational estimates, not billing-grade truth: OpenAI
does not expose a model price-rate catalog through the API, and the Admin Usage
Costs API reports aggregated billed costs rather than the per-model rates used
here. Keep `config/model-pricing.openai.json` current when OpenAI pricing
changes. Built-in tool fees, non-text token modalities, regional uplift, and
account-specific discounts are not inferred unless the needed quantities are
recorded in the usage metadata.

Admin Web Configuration includes a model pricing catalog panel that shows the
active path, path source, version, currency, source URLs, rule count, rules, and
any load/validation error. Operators can edit catalog metadata, source URLs, and
pricing rules in the structured panel after unlocking privileged operations.
Saves include the catalog revision and fail on stale revisions, so concurrent
operator edits require a reload before retrying. The backend validates the
catalog before writing and writes atomically to either
`EVANDER_MODEL_PRICING_PATH` or the default
`config/model-pricing.openai.json` path.

The store intentionally excludes request and response text, so it is safe to
inspect for operational attribution without exposing prompt content.

## Operator Steering

When inspecting a workflow run in Admin Web, agent links that include both a
`session_id` and `dispatch_id` can accept operator steering after workflow-run
control permission and privileged operations unlock.

For `container_worker_vm` sessions, Operator steering interrupts the active
worker execution and immediately starts a replacement worker job with the
steering instruction. The workflow waiting record keeps the same logical
dispatch and is updated to point at the replacement execution, so workflow nodes
do not see the interrupted job as completed or failed. If the replacement job
cannot be started after the active job was stopped, the original waiting record
is failed so the workflow sees the real steering failure.

For legacy relay-delivered steering, the note is stored on the host and is
delivered once, on the next eligible model-generation HTTP request for the same
active session and dispatch.

Legacy relay delivery is intentionally best-effort and non-interrupting:

- A note cannot alter a model call that is already in progress.
- The relay does not poll the agent or add background context checks.
- Only OpenAI/Azure OpenAI `responses` and `chat/completions`, plus Anthropic
  `messages`, consume notes.
- `/responses/compact`, Anthropic `/messages/count_tokens`, and websocket
  traffic do not consume notes.
- If the upstream request fails before the relay receives response headers, the
  note returns to pending so a later eligible call can deliver it.

## Workflow pack operations

- The trigger/effect workers compose workflow definitions from `workflow_packs` in
  `config/trigger-orchestrator/config.json`.
- Operator pack files live at `config/workflow-packs/*.json` and include versioned IR (`pack_id`,
  `pack_version`, `ir.version`, `ir.nodes`, `ir.edges`). These live pack files are ignored by Git;
  checked-in starting points use `*-example.json`.
- A pack can contain multiple connected graphs. Optional `graphs` entries name those connected
  node sets with `id`, `name`, `description`, canonical `directory_path`, `user_ids`, and
  `node_ids`; the runtime still executes from the IR, while the editor uses this metadata for
  directory-scoped graph selection and labels. A legacy one-item `directory_paths` list is accepted
  and emitted as compatibility metadata, but multi-directory graph ownership is rejected. `user_ids`
  are owner/audit metadata only; workflow graph visibility is controlled by Admin Web group
  assignments and direct workflow grants. Saving from a filtered view keeps unchanged graph
  metadata entries intact, so graphs outside the selected directory are not rewritten by the
  presentation filter.
- For the live Weekly APP Audit graph, use Admin Web or the workflow API to edit the audit
  `effect:agent:create` node and set `npm_registry_url` to the control-plane facade URL,
  `http://<control-plane-private-ip>:11881/`. Keep the other container-worker agent-create nodes
  on Nexus-backed registry defaults so only audit sessions opt into the facade.
- In the Workflow editor, node inspector edits are staged into the local workflow document as soon
  as fields change. They are still not persisted to disk until **Save workflow** succeeds. Dirty
  nodes show an **Unsaved changes** badge, selected dirty nodes can be reverted with **Reset
  changes**, and leaving the workflow view or switching directories prompts for confirmation while
  any workflow changes remain unsaved.
- Admin Web and workflow API pack mutations write internal change history under
  `runtime/workflow-pack-history/`. `changes.jsonl` is the append-only ledger, and
  `snapshots/<change-id>/` stores before/after copies of the touched pack, layout, and trigger
  wrapper config. Use the Workflow tab history panel or
  `/api/workflow-editor/packs/history` endpoints to inspect who changed a pack and what changed.
  Direct file edits under `config/workflow-packs/` are outside that attribution path.
- Admin Web exposes workflow reporting under **Reports**. The workflow graph activity report ranks
  graph metadata by trigger events plus gate/effect execution records over the selected period.
- Admin Web also exposes workflow failure clusters under **Reports**. That report groups repeated
  stored run failures by graph, node, and normalized error signature, strips volatile IDs and
  timestamps from the signature, and treats the active/recent/resolved label as a heuristic triage
  signal only. Inspect a representative workflow run detail before taking remediation steps.
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
Container-worker session storage telemetry is cached by the worker daemon so normal dashboard reads
do not block on deep session-tree scans; use the worker API's `GET /v1/telemetry?refresh=1` only
when an explicit blocking refresh is needed.

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
evander-cli list-vms --all --json
evander-cli list-local-issues --json
```

## Admin Web Authentication

The browser admin surface supports three auth modes:

- `none`: no sign-in required. This remains the default when
  `EVANDER_ADMIN_WEB_AUTH_MODE` is unset and `EVANDER_ADMIN_WEB_PASSWORD` is also unset.
- `local_password`: local username/password sign-in with one or more Admin Web users. If
  `EVANDER_ADMIN_WEB_AUTH_MODE` is unset but `EVANDER_ADMIN_WEB_PASSWORD` is set, this mode is selected automatically for backward compatibility.
- `azure_sso`: Microsoft Entra browser sign-in through OIDC Authorization Code + PKCE.

Required local session settings:

- `EVANDER_ADMIN_WEB_SESSION_SECRET`
- optional `EVANDER_ADMIN_WEB_SESSION_TTL_SECONDS`
- optional `EVANDER_ADMIN_WEB_COOKIE_SECURE=true`

`local_password` settings:

- `EVANDER_ADMIN_WEB_AUTH_MODE=local_password`
- `EVANDER_ADMIN_WEB_USERNAME` for bootstrap/break-glass access
- `EVANDER_ADMIN_WEB_PASSWORD` for bootstrap/break-glass access

`azure_sso` settings:

- `EVANDER_ADMIN_WEB_AUTH_MODE=azure_sso`
- `EVANDER_ADMIN_WEB_AZURE_TENANT_ID`
- `EVANDER_ADMIN_WEB_AZURE_CLIENT_ID`
- `EVANDER_ADMIN_WEB_AZURE_CLIENT_SECRET`
- `EVANDER_ADMIN_WEB_AZURE_REDIRECT_URI`
  or `EVANDER_ADMIN_WEB_PUBLIC_BASE_URL`
- optional `EVANDER_ADMIN_WEB_AZURE_AUTHORITY_BASE_URL`
- optional first-run `EVANDER_ADMIN_WEB_AZURE_ALLOWED_USERS`
- optional `EVANDER_ADMIN_WEB_AZURE_POST_LOGIN_REDIRECT`

Azure SSO enabling sequence:

1. Register a confidential Microsoft Entra web application for the admin web.
2. Add the exact admin callback URI to the app registration.
3. Allow OpenID scopes `openid profile email`.
4. Set the trusted tenant id in `EVANDER_ADMIN_WEB_AZURE_TENANT_ID`.
5. On first startup, add initial admin UPN/email-style logins to
   `EVANDER_ADMIN_WEB_AZURE_ALLOWED_USERS`.
6. Restart `evander-admin-web`.

Admin Web users are stored in `runtime/admin-web.db`, keyed by normalized login/email. In
`local_password` mode, the configured username/password seeds the first local owner when the user
table is empty, and Users & Groups can set or reset per-user local passwords. In `azure_sso` mode,
`EVANDER_ADMIN_WEB_AZURE_ALLOWED_USERS` seeds initial users with the Owner group when the
SQLite user table is empty. A legacy `runtime/admin-web-users.json` file is imported once when the
SQLite table is empty; legacy `admin` and `regular` roles are retained for compatibility and mapped
to Owner and Operator group assignments. After that first bootstrap, the SQLite runtime store is the source of
truth. Users with `groups.manage` can create and edit reusable permission groups; users with
`users.manage` can create users and edit their directory-scoped group assignments and direct
workflow read/write grants from the Admin Web Users & Groups tab. Directory scopes inherit downward,
direct workflow write grants imply read, and privileged unlock remains a separate guard for
high-risk mutations. Removing an Azure user from the runtime store invalidates their existing local
admin session on the next authenticated request.

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
evander-cli migrate-directory-ownership --runtime-dir runtime
```

Common CLI flows:

```bash
evander-cli directories list --runtime-dir runtime --parent root
evander-cli directories create --runtime-dir runtime --parent root team
evander-cli directories rename --runtime-dir runtime root/team sandbox
evander-cli directories move --runtime-dir runtime root/sandbox --new-parent root
evander-cli directories show --runtime-dir runtime root
evander-cli directories config-set --runtime-dir runtime --domain policy root/team '{"default_session_ttl_seconds":120}'
evander-cli directories config-show --runtime-dir runtime --domain policy root/team
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
  - `vm.json` for guest-facing relay/network defaults plus libvirt VM template selection data
- `root` is the live top-level layer; there is no hidden YAML base beneath it.

## SSH Access

- New VMs install `openssh-server` and `ripgrep`. Provider bootstraps also copy a host `rg` binary into `~/bin/rg`, so rerunning the provider bootstrap repairs older VMs that predate the cloud-init package list or could not use guest apt.
- For a current VM where you only want `rg`, run `./scripts/bootstrap_ripgrep_vm.sh <vm-name> [vm-user]`.
- The launch script injects the caller's `~/.ssh/id_ed25519.pub` by default, falling back to `~/.ssh/id_rsa.pub`.
- Override the key with `SSH_PUBLIC_KEY_FILE=/path/to/key.pub`, or point `SSH_PRIVATE_KEY_PATH` at the matching private key so the script can derive a temporary public key when needed.
- After boot, find the guest IP with `virsh domifaddr <vm-name>` and connect with `ssh agent@<vm-ip>`.
- Host-side guest SSH commands store guest host keys in `~/.ssh/evander-known_hosts` keyed by VM name. `destroy_agent_vm.sh` removes the stale alias for a destroyed VM so a replacement VM can be learned cleanly.
- To list all currently running VMs with live guest agent status from the host, run `evander-cli list-vms`.

## VM Agent Runtime Baseline

- Cloud-init writes provider-neutral baseline config files: `/etc/evander/session.env` and `/home/agent/.agent/config.env`.
- Cloud-init also appends a shell profile hook that loads `/etc/evander/session.env` and exports placeholder `OPENAI_API_KEY` and `ANTHROPIC_AUTH_TOKEN` values if none are already set.
- Cloud-init creates `agent` as the unprivileged runtime user and `agent-admin` as the host-control SSH user with passwordless sudo. Host resume/log tooling uses `agent-admin` to append root-owned logs while running the actual agent process as `agent`.
- Generic orchestration status is provider-neutral and read from `/home/agent/.evander/agent-*.state`, with companion `agent-*.pid` files. Transcript logs are written through the privileged host-control path to root-owned files under `/var/log/evander/agent-runs/`.
- New VMs pick this up automatically; existing VMs need to be recreated or migrated if they predate baseline config changes. For one-off maintenance against an old sudo-enabled runtime user, set `VM_SSH_USER=agent` explicitly.

### Codex provider bootstrap

- `./scripts/bootstrap_codex_vm.sh <vm-name>` copies the vendored Linux Codex binary from the host into `~/bin/codex` for the guest user, seeds `~/bin/rg`, and verifies Codex.
- The bootstrap script writes `~/.codex/config.toml` with relay-aware defaults (currently `model = "gpt-5.4"`, `model_reasoning_effort = "high"`, and `personality = "pragmatic"`), preferring explicit `LLM_BASE_URL` and otherwise reading `EVANDER_LLM_BASE_URL` from `~/.agent/config.env`; it registers the host relay as `[model_providers.evander_openai_relay]` with `supports_websockets = false` so Codex does not run websocket prewarm against the HTTP/SSE relay. It also registers the host MCP gateway as `[mcp_servers.evander]` using `EVANDER_GATEWAY_URL` and copies the host Codex model cache when available so offline guests see the same model menu. It then appends the placeholder-key shell hook plus `~/bin` PATH entry to both `~/.profile` and `~/.bashrc`.
- Runtime start/resume model selection is host-session-bound: `session.model` wins, otherwise effective `issue_setup.default_model` is used. If neither resolves, start/resume fails with a remediation error instead of using an implicit runtime fallback model.
- The bootstrap script waits for guest cloud-init via the privileged host-control user and repairs `/home/agent` ownership before the first runtime-user write, so SSH readiness alone cannot race the Codex bootstrap.

### Claude Code provider bootstrap

- `./scripts/bootstrap_claude_vm.sh <vm-name>` copies a host Claude Code CLI command into `~/bin/claude` for the guest user, seeds `~/bin/rg`, and verifies `claude --version`.
- The bootstrap script writes `~/.claude/settings.json` with the session-scoped `ANTHROPIC_BASE_URL`, placeholder `ANTHROPIC_AUTH_TOKEN`, gateway model discovery enabled, telemetry disabled, and Claude Code commit-message attribution disabled. It also writes `~/.claude/mcp-config.json` so Claude Code can reach the host MCP gateway as `evander`, including the active `session_id` query string when present.
- The bootstrap script appends `~/bin`, `AGENT_PROVIDER=claude`, placeholder Anthropic auth, and the configured Anthropic base URL to both `~/.profile` and `~/.bashrc`.
- The bootstrap script waits for guest cloud-init via the privileged host-control user and repairs `/home/agent` ownership before the first runtime-user write, so SSH readiness alone cannot race the Claude bootstrap.
- Host-side routing is selected with `model_provider_mode: "anthropic"` in the effective provider config. The relay keeps the real Anthropic API key on the host and injects it as `x-api-key` upstream.

### GitHub Copilot CLI provider bootstrap

- `./scripts/bootstrap_copilot_vm.sh <vm-name>` copies the host `copilot` binary into `~/bin/copilot` for the guest user, seeds `~/bin/rg`, and verifies `copilot version`.
- The bootstrap script writes `~/.copilot/config.json` with provider-local defaults and `~/.copilot/mcp-config.json` so Copilot CLI can reach the host MCP gateway as `evander`, including the active `session_id` query string when present.
- The bootstrap script also installs a guest `gh` shim in `~/bin/gh`. Copilot CLI uses GitHub CLI as its lowest-priority auth fallback, so the shim answers `gh auth token` by calling the host gateway at `/copilot-auth/token` with the guest session id. That keeps the long-lived GitHub token on the host while avoiding permanent guest-side token storage.
- The bootstrap script appends `~/bin` and `COPILOT_HOME=~/.copilot` to both `~/.profile` and `~/.bashrc`.
- Host-mediated auth reads the effective provider `github_token_env` on the host. That environment variable must contain a GitHub token that can satisfy Copilot CLI requests. Guest-side `COPILOT_GITHUB_TOKEN`, `GH_TOKEN`, `GITHUB_TOKEN`, or `copilot login` still override the host-mediated `gh` fallback if you set them intentionally.

### Microsoft Teams delegated auth

- Teams access is host-side and delegated. The preferred operator flow is Admin Web ->
  Configuration -> People & Communications: verify the directory-scoped Teams provider settings, inspect
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
  - Teams direct-message sender mail/UPN resolution and outbound mention lookup may require adding
    a Graph user-read scope such as `User.ReadBasic.All` or `User.Read`
- The Entra app behind `teams_delegated_client_id` must be configured for public client /
  device-code auth. `AADSTS7000218` means the app is being treated as a confidential client and
  the host-side delegated flow in this repo cannot complete until that registration is fixed.
- In Admin Web -> Configuration -> People & Communications, use the Teams auth panel to:
  - inspect the current delegated auth state for the selected directory
  - start device-code sign-in and complete the login polling flow
  - sign out when you need to revoke the stored host-side credential
- Use the channel lookup helper in the same People & Communications area to resolve `team-id/channel-id`
  entries and copy them into communication targets.
- Use the host CLI for delegated auth lifecycle when you need a headless fallback:
  - `evander-cli teams-auth login --directory <directory-path>`
  - `evander-cli teams-auth status --directory <directory-path>`
  - `evander-cli teams-auth logout --directory <directory-path>`
  - `evander-cli teams-auth list`
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
- `teams_post_status` accepts `mentions` for channel posts, channel thread replies, and chat posts.
  User targets are resolved into a one-on-one chat before sending. Plain `@name` text in the
  message body does not notify users; pass Graph user IDs, UPNs, or mail addresses in `mentions`.
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
- `teams_get_messages` requires `channel_target` so agents read only the specific channel or chat
  that woke them. Optional cursors can be full returned cursor values or bare message ids, which
  the gateway scopes to the required target. Cursorless reads still stay on a bounded recent path
  for that target: the gateway applies `teams_poll_limit` as the default message limit, and the
  Graph adapter caps each history scan with `teams_history_page_cap` plus
  `teams_history_deadline_seconds`.

## VM Python Bootstrap

- `./scripts/bootstrap_python_vm.sh <vm-name> <host-checkout-root> <guest-repo-root>` builds a temporary host wheelhouse from the repo's `pyproject.toml`, copies it into the guest, creates a repo-local `.venv`, and installs the repo with dev dependencies offline.
- The bootstrap validates that the guest Python interpreter satisfies the repo's `requires-python` lower bound before it creates the virtualenv.
- The guest install path uses `pip install --no-index --find-links ... --no-build-isolation -e ".[dev]"` so later guest edits still run against the cloned checkout rather than a prebuilt wheel.
- `./scripts/bootstrap_python_packages_vm.sh <vm-name> <package-spec> [package-spec ...]` is the lower-level generic variant for guest-local Python tools that are not tied to a repo checkout.
- The generic package bootstrap builds a temporary host wheelhouse for the requested package specs plus `setuptools`, `wheel`, and `virtualenv`, copies that wheelhouse into the guest under `~/.evander/python-wheelhouse/tools`, creates or reuses a guest tools virtualenv under `~/.evander/python-tools-venv`, and installs the requested packages offline with `pip install --no-index --find-links ...`.
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
  - read-only `fallbackPackageFolders` at `/opt/evander/nuget-cache-ro`
- It runs `dotnet nuget list source` when `dotnet` is available in the guest.

## VM Microsoft SQL Server Bootstrap

- `MSSQL_SA_PASSWORD=... ./scripts/bootstrap_mssql_vm.sh <vm-name>` bootstraps both SQL Server tooling and a guest-local SQL Server engine.
- The tooling step copies host-installed `sqlcmd` into `~/toolchains/mssql-tools` and appends its `bin` directory to `~/.profile` and `~/.bashrc`.
- The server step copies the host SQL Server engine into `/opt/mssql`, creates a guest-local `/var/opt/mssql` data directory, initializes or resets the SA password with `mssql-conf`, and starts `mssql-server` as a local systemd service.
- The final verification runs `sqlcmd -S localhost,1433 ... -Q "SELECT 1"` against the guest-local instance.
- `--init-root /path/to/mssql-init` copies that directory into `/var/opt/mssql/init` and executes `init.sql`, which can run arbitrary setup or restore commands for the local instance.

## Host Shared NuGet Cache

- Default host cache path: `/var/lib/libvirt/images/evander/nuget-cache/global-packages`
- Guests see that cache read-only at `/opt/evander/nuget-cache-ro` via `virtiofs`.
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

## Container Worker NuGet Fallback Cache

- Container workers use a separate fallback cache, typically
  `/var/lib/agent-worker/toolchain-cache/nuget-cache-ro` on each worker VM.
- Prewarm that cache from the control plane, through the host NuGet relay, with:

  ```bash
  ./scripts/prewarm_container_worker_nuget_cache.sh \
    --worker truman-worker-1 \
    --target /opt/truman/runtime/repo-cache/acd57ed0241e2a600b46d33f743d5f25f35dca73ac822e6ce4619ac37b8f4def-worktree/Src/App.slnx
  ```

- The script restores into a temporary global-packages folder, includes the
  worker restore-support package used by the offline smoke test, and extracts
  the archive additively into each worker cache.
- When the `.NET` toolchain and checkout are already on the worker, run the
  same script on that worker with `--local --dotnet /var/lib/agent-worker/toolchain-cache/dotnet/dotnet`
  and set `NUGET_RELAY_BASE_URL` to the control-plane relay URL visible from the
  worker.

## Host-Side LLM Access

- The firewall allows only the host TCP ports listed in `ALLOWED_TCP_PORTS`.
- Defaults: `22` for host SSH/Git, `8000` for the MCP gateway, `11434` for the host-side OpenAI relay or a local OpenAI-compatible LLM endpoint such as Ollama, `11666` for the host-side NuGet relay, `11777` for the host-side HTTP target relay used to reach approved external web endpoints such as APP demo environments, `11881` for the host-side npm audit facade, `8081` for the host-side Nexus package cache used by worker PyPI/npm restores, and `3142` for the host-side apt-cacher-ng relay used by worker apt provisioning. The optional Qdrant semantic store binds to `127.0.0.1:6333` by default and is intentionally host-local in this slice.
- Override when applying firewall rules, for example:

  ```bash
  sudo ALLOWED_TCP_PORTS="22 8000 11434 11666 11777 11881 8081 3142 4000" ./scripts/apply_firewall.sh
  ```

- The guest receives `EVANDER_LLM_BASE_URL`, `OPENAI_BASE_URL`, `EVANDER_ANTHROPIC_BASE_URL`, and `ANTHROPIC_BASE_URL` in `/etc/evander/session.env`.
- The scaffold includes `evander-openai-relay`, a host-side model proxy that reads real provider API keys only from the host environment. In public OpenAI mode it forwards guest requests to `https://api.openai.com/v1`.
- The guest still talks to the relay URL and keeps placeholder provider auth values; only host-side provider settings decide whether the relay forwards to public OpenAI, a host-local OpenAI-compatible endpoint, Azure OpenAI, or Anthropic.
- For Azure OpenAI Codex routing, set `model_provider_mode: "azure_openai"` in `root` or directory `providers.json` and configure `azure_openai_endpoint`, `azure_openai_api_key_env`, `azure_openai_api_version`, and `azure_openai_model_deployments`.
- Example directory-scoped `providers.json` overlay:

  ```json
  {
    "model_provider_mode": "azure_openai",
    "azure_openai_endpoint": "https://example-resource.openai.azure.com/",
    "azure_openai_api_key_env": "AZURE_OPENAI_API_KEY",
    "azure_openai_api_version": "2024-10-21-preview",
    "azure_openai_model_deployments": {
      "gpt-5.4": "codex-gpt-5-4"
    }
  }
  ```
- For local OpenAI-compatible Codex routing, keep `agent_provider` as `codex` and set `model_provider_mode: "local_openai"`. The relay forwards normal OpenAI-compatible HTTP requests to `local_openai_api_base_url`, sends `local_openai_api_key` as a placeholder bearer token, and records usage rows under `local_openai_provider_name`. Guest Codex config should use the generated `evander_openai_relay` provider instead of overriding the built-in `openai` provider so websocket prewarm remains disabled for the relay.
- `local_openai` mode is HTTP/SSE only. The relay rejects websocket/realtime requests
  before dialing the upstream local endpoint because the supported Ollama target does
  not implement OpenAI websocket `/responses`.
- Ollama is the v1 local runtime target. Pull the recommended initial model on an RTX 5090-class host:

  ```bash
  ollama pull qwen3-coder:30b
  ```

  Keep Ollama host-local or on a private control-plane address; guests and worker containers should still reach only Truman's session-scoped relay URL. Because the relay itself defaults to `0.0.0.0:11434`, `local_openai` rejects the default `http://127.0.0.1:11434/v1` upstream unless the relay is moved off wildcard/loopback on that port. Either bind the relay only on the guest-facing control-plane address while Ollama keeps `127.0.0.1:11434`, or move Ollama to another private port and set `local_openai_api_base_url` accordingly.
- Example local Ollama directory-scoped `providers.json` overlay when the relay keeps its default listener and Ollama is moved to `127.0.0.1:11435`:

  ```json
  {
    "model_provider_mode": "local_openai",
    "local_openai_api_base_url": "http://127.0.0.1:11435/v1",
    "local_openai_api_key": "ollama",
    "local_openai_provider_name": "local_openai"
  }
  ```

- To mix public OpenAI and local OpenAI Codex agents in the same directory, keep
  the directory `providers.json` populated with both the public OpenAI settings
  and the local OpenAI endpoint settings, then set `model_provider_mode` on the
  individual agent/session. A session with no override uses the directory
  default; a session with metadata `{"model_provider_mode": "local_openai"}`
  routes through the local endpoint; a session with
  `{"model_provider_mode": "openai_public"}` routes through public OpenAI. The
  Admin Web Create Agent dialog exposes this as **Model relay** for Codex or
  directory-default agents, and CLI-created agents can pass
  `--model-provider-mode local_openai`.
- Restart `evander-openai-relay.service` after changing provider config that the relay process has already loaded.
- Public OpenAI remains the default when `model_provider_mode` is omitted.
- For Claude Code routing, set `model_provider_mode: "anthropic"` and configure `anthropic_api_key_env`; optional settings include `anthropic_api_base_url`, `anthropic_api_version`, and `anthropic_relay_timeout_seconds`. The Anthropic mode currently forwards `GET /v1/models`, `POST /v1/messages`, and `POST /v1/messages/count_tokens`; Anthropic websocket/realtime requests are rejected.
- The relay records provider-neutral model usage metadata for HTTP, SSE, and
  WebSocket Responses/Completions traffic that exposes a provider `usage`
  payload. It also records `usage_unavailable` when a completed response has no
  usage block. Azure OpenAI and Anthropic WebSocket/realtime and Azure managed
  identity are not included.
- Hosted OpenAI web search is denied by default at the relay. Set the session
  policy flag `web_search_enabled: true` only for agents that should be allowed
  to declare Responses `web_search` tools.
- Codex bootstrap and resume paths set `web_search` from that session policy,
  defaulting to `disabled`.
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
- Session-scoped MCP `tools/list` responses follow the same policy visibility rules: tools denied
  by directory policy or missing from `allowed_tools` are omitted, approval-required tools remain
  visible with approval metadata in policy summaries, and direct calls still enforce policy even
  when a tool is hidden from the manifest.
- Once an MCP transport session is bound to a Truman session, later `/mcp` requests on that
  transport must omit `session_id` or repeat the same value. A conflicting `session_id` is rejected
  instead of switching the manifest to another session.
- Required MCP arguments: none beyond the session binding.
- Optional MCP arguments:
  - `include_runs` (default `false`) includes the current workflow run snapshot alongside the
    workflow editor document.
- Response payload includes the active workflow editor document: config path, layout path, revision,
  saved timestamp, normalized workflow config, layout, graph metadata, and derived output schemas.

## Tool Visibility Inspector

- Admin Web exposes a read-only `Tool Visibility` tab for diagnosing a single session's effective
  tool set.
- Choose a session from the left sidebar or deep-link with `#tool-visibility?session_id=<id>`.
- The inspector compares policy visibility, transport binding telemetry, and denied-at-execution
  evidence without exposing raw tool arguments or results.
- Use the `State` filter to compare `Visible`, `Hidden by policy`, `Unavailable to session`,
  `Denied at execution`, and `Unknown` rows. The detail panel shows the policy reason and safe
  denial summary only.
- Approval badges distinguish operator-configured approval requirements from locked host-policy
  requirements. A locked approval badge means the tool always requires approval and operators
  cannot disable that requirement through session or directory policy.
- The transport banner can report `Bound`, `Mismatch`, `Unknown`, or `Stale manifest possible`.
  `Denied at execution` is historical audit evidence, not a live policy grant.
- If the manifest is stale, reconnect or refresh the MCP client so it fetches the current server
  manifest. The inspector is diagnostic only and cannot force a client refresh or grant new tools.

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
  - Optional MCP arguments: `project`, `mentions`, `artifact_ids`, `request_id`.
  - `mentions` accepts Azure DevOps identity IDs, descriptors, email/UPN values, or display/name
    search text. The gateway resolves refs with the ADO identities API, renders the comment as
    ADO HTML with documented `data-vss-mention` anchors, and posts with explicit `format=html`;
    unresolved or ambiguous refs fail the call.
- Gateway tool name: `update_ado_backlog_text`.
  - Policy-gated with `update_ado_backlog_text`.
  - Required MCP argument: `id`; at least one text field or artifact list must be present.
  - Optional MCP arguments: `project`, `description`, `acceptance_criteria`,
    `description_artifact_ids`, `acceptance_criteria_artifact_ids`, `request_id`.
- Gateway tool name: `update_ado_backlog_title`.
  - Policy-gated with `update_ado_backlog_title`.
  - Required MCP arguments: `id`, `title`.
  - Optional MCP argument: `project`.
  - The tool trims `title`, rejects blank or whitespace-only values, and writes the result to
    `System.Title`.
- Gateway tool name: `update_ado_backlog_tags`.
  - Policy-gated with `update_ado_backlog_tags`.
  - Required MCP argument: `id`; at least one tag must be present in `add_tags` or `remove_tags`.
  - Optional MCP arguments: `project`, `add_tags`, `remove_tags`.
  - The tool reads the current `System.Tags`, merges the requested changes, and writes the full
    tag field back to ADO.
- Gateway tool name: `delete_ado_backlog_item`.
  - Policy-gated with `delete_ado_backlog_item`, and the gateway also requires per-invocation
    approval before the DELETE runs.
  - Required MCP argument: `id`.
  - Optional MCP arguments: `project`, `destroy`.
  - `destroy` defaults to `false`, which sends the work item to the recycle bin for recovery. Set
    `destroy=true` only for permanent deletion.
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

## Host-Side Azure DevOps Work Item Activity Tool

- Gateway tool name: `query_ado_work_item_activity`.
- The tool is policy-gated: each session manifest must include
  `query_ado_work_item_activity` in `allowed_tools` before calls are accepted.
- MCP arguments:
  - required `since` timestamp
  - optional `until`, `project`, `actor`, `activity_types`, `states`, `types`, `tags_any`
  - paging inputs: `top` (default `100`, max `200`) and `skip` (default `0`)
- `activity_types` defaults to `["created", "changed", "commented"]`.
- Response payload includes matching work items and per-item `activities` records with the matched
  actor, timestamp, provenance (`created_field`, `changed_field`, `update`, or `comment`), and raw
  provider payload snippets.

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
  - optional report overrides: `title`, `report_date`, `sprint_label`, `sprint_length`
  - optional elapsed-time options: `elapsed_time_mode` (`swedish_work_time`,
    `calendar_time`, or `manual_override`), `workday_start`, `workday_end`, and
    `include_common_swedish_non_working_days`
  - optional manual elapsed-time override: `elapsed_sprint_percent`; this is honored only
    when `elapsed_time_mode` is `manual_override`
  - optional calculation overrides: `done_states`, `in_progress_states`, `effort_fields`,
    `max_items`
- By default, elapsed time uses Swedish working time in `Europe/Stockholm`, counts only weekdays
  inside the configured workday window, excludes Swedish public holidays, and also excludes common
  non-working eves such as Midsummer Eve, Christmas Eve, and New Year's Eve.
- The daily status report normalizes `max_items` values below 1000 up to 1000 so small
  agent-selected caps do not truncate sprint totals.
- By default, the query includes only Product Backlog Items and Bugs and excludes `Task` work items
  so the report is based on sprint delivery items rather than features or implementation subtasks.
  Pass an explicit non-empty `types` list to override the default, and pass `exclude_types: []` to
  include Tasks.
- By default, only `In Progress` and `Active` count as active work. `Committed` work items remain
  in the not-started/committed status bucket unless `in_progress_states` is overridden.
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
  - `evander-cli create-session` includes both tools by default.
  - Use `evander-cli add-tool-permission` / `revoke-tool-permission` to grant or remove
    either tool per session.
- Least-privilege guidance:
  - Grant `prompt_agent` only to dispatcher sessions.
  - Grant `respond_to_agent` only to responder sessions.
  - Keep `directory_path` boundaries narrow so prompt routing cannot cross unrelated teams.

## Host-Side Phone Calls

Use this only when agents should call configured directory contacts. The gateway owns contact
resolution, provider requests, webhook/result state, and transcript storage. Guests never receive
raw phone numbers or phone-provider credentials.

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
  - Add `submit_voice_query_response` to let the originating agent answer live delegated questions
    from a Realtime call.
  - `get_phone_call_result` only returns calls owned by the same session and directory path.
  - Add `update_ado_backlog_tags` only for sessions that may change ADO item tags.
- Provider config with defaults:
  - `phone_call_backend` defaults to `retell`. Set it to `openai_realtime_didww` to use the
    projected OpenAI Realtime/DIDWW bridge backend.
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
- Session manifests may set `allowed_contact_ids` to narrow the contact set further. Leave it
  omitted or `null` to inherit all directory contacts; use `[]` to make no contacts callable for
  that session. Unknown ids are rejected against the session directory's effective contacts.
- Required host secret:
  - Set the configured `retell_api_key_env` value in the gateway host environment. For systemd,
    repo-root `secrets.env` is already loaded by `evander-gateway.service`.
- Required Retell agent prompt:
  - The Retell agent/LLM referenced by `retell_default_agent_id` must include
    `{{agent_question}}` in its Retell prompt or begin message.
  - `make_phone_call.prompt` is sent as `retell_llm_dynamic_variables.agent_question`; Retell only
    uses that value where the configured Retell prompt references the dynamic variable.
- Runtime state:
  - `runtime/retell-phone-calls-store.json`
  - `runtime/voice-sessions-store.json` for projected OpenAI Realtime phone, meeting, and Team
    Add-on browser direct agent sessions
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

### Team Add-on browser direct agent calls

The Team Add-on People tab can start a browser WebRTC call to the selected persistent agent. This
path reuses projected voice-session state, origin-agent query delegation, and reconciliation wakes,
but it does not use DIDWW, Asterisk, SIP, or the host media bridge. The Admin API validates the
selected agent and origin session, writes a `browser_agent_call` record to
`runtime/voice-sessions-store.json`, and mints an OpenAI Realtime ephemeral client secret with the
agent's current team, role, task, inbox, review, deliverable, runtime, and visible tool context.
Each persistent agent can optionally select a built-in Realtime voice and speaking-style
instruction; the style is appended only to browser-call session instructions and is not exposed to
agent runtime prompts.

Set `openai_realtime_api_key_env` to the host environment variable used for the OpenAI Realtime API
key, or leave it at the default `OPENAI_API_KEY`. `openai_realtime_api_base_url` defaults to
`https://api.openai.com/v1`. The browser receives only the ephemeral client secret, never the host
API key. When a browser call ends, the add-on schedules the same same-context reconciliation wake
used by phone and meeting voice sessions.

For the `openai_realtime_didww` backend, configure:

```json
{
  "phone_call_backend": "openai_realtime_didww",
  "openai_realtime_model": "gpt-realtime-2",
  "voice_media_controller_url": "http://127.0.0.1:8000",
  "voice_media_controller_token_env": "VOICE_MEDIA_CONTROLLER_TOKEN",
  "didww_sip_bridge_url": "https://your-phone-bridge.example",
  "didww_sip_bridge_token_env": "DIDWW_SIP_BRIDGE_TOKEN",
  "didww_from_number": "+15551230000"
}
```

For fixed inbound DIDWW routes, add an `inbound_phone_routes` entry that binds the DID to an
existing active Truman session in the same directory. Keep the bridge host-local and enable the
inbound listener only when needed:

```bash
DIDWW_SIP_BRIDGE_ENABLE_INBOUND_PHONE_CALLS=true
DIDWW_SIP_BRIDGE_INBOUND_GATEWAY_URL=http://127.0.0.1:8000
DIDWW_SIP_BRIDGE_INBOUND_GATEWAY_TOKEN_ENV=VOICE_MEDIA_CONTROLLER_TOKEN
```

```json
{
  "phone_call_backend": "openai_realtime_didww",
  "openai_realtime_model": "gpt-realtime-2",
  "voice_media_controller_url": "http://127.0.0.1:8000",
  "voice_media_controller_token_env": "VOICE_MEDIA_CONTROLLER_TOKEN",
  "didww_sip_bridge_url": "https://your-phone-bridge.example",
  "didww_sip_bridge_token_env": "DIDWW_SIP_BRIDGE_TOKEN",
  "didww_from_number": "+15551230000",
  "inbound_phone_routes": [
    {
      "id": "ops-main",
      "did": "+15551230000",
      "directory_path": "root/ops",
      "session_id": "session-1234",
      "allowed_callers": ["+15551230001"],
      "blocked_callers": ["+15551239999"],
      "route_prompt": "Answer inbound calls for the ops team.",
      "startup_text": "Thanks for calling ops."
    }
  ]
}
```

The bridge routes fixed inbound calls from Asterisk by sending the DID to
`Stasis(evander_didww,inbound:<did-or-route-id>)`. When the bridge accepts an inbound
`StasisStart`, it POSTs to `POST /voice-sessions/inbound-phone-calls` using the token named by
`DIDWW_SIP_BRIDGE_INBOUND_GATEWAY_TOKEN_ENV` (default `VOICE_MEDIA_CONTROLLER_TOKEN`).
After the call ends, the bridge POSTs the sanitized terminal bridge result to
`POST /voice-sessions/{voice_session_id}/bridge-result`; the gateway merges the transcript,
summary, decisions, and action items into `runtime/voice-sessions-store.json` and schedules the
same-origin reconciliation wake.

The gateway resolves exactly one configured route by route id or DID, requires the configured
`session_id` to exist and remain active in the same directory, and enforces caller policy before
creating the voice-session record. `route_prompt` becomes the projected Realtime task
prompt; `startup_text` is the caller-facing greeting. Raw DID and caller numbers are normalized
for matching, but only redacted or hashed forms are stored in audit logs and JSON stores.

Configure the OpenAI Realtime key env var and upstream base URL in the DIDWW SIP bridge service
environment. The gateway does not pass those credential-routing values through each phone-call
request.

The DIDWW bridge receives a projected voice-session prompt, the selected contact phone number, and
Realtime connection metadata. It owns SIP trunk origination and raw audio forwarding. When the bridge
returns a terminal call state, the control plane schedules a same-context wake for the originating
agent with the transcript, summary, decisions, and follow-up actions.

For outbound startup, the bridge treats `make_phone_call.prompt` as authoritative first-turn task
state. When that prompt contains a complete opening or objective, the projected Realtime agent must
begin from that task instead of asking the origin agent for the same script, instructions, expected
behavior, or call context. If the prompt is too incomplete to start and the missing data must come
from the caller, the initial turn asks one concise caller-facing clarification question.

When `voice_media_controller_url` is configured, the Realtime voice agent also gets a
`query_origin_agent` function tool. It uses that tool when the caller asks for files, repository
state, live tools, or memory that is not in the projected context. The bridge creates a pending
voice query through the gateway, the gateway schedules a same-context wake for the originating
agent, and the origin agent answers with `submit_voice_query_response`. The voice call keeps going
silently while that answer is pending; it does not generate generic startup or context-check filler.
When the answer arrives, the bridge injects it back into the Realtime conversation. If the caller is
speaking, or if their audio input turn has not produced its final transcript yet, the bridge queues
the origin-query answer instead of creating assistant speech immediately. After the caller turn
completes, the projected agent first acknowledges or briefly responds to the latest caller turn,
then transitions to the queued answer with caller-facing wording such as "I heard back on the
earlier question." Normal origin-query answers, failures, and timeouts do not barge in; only the
existing caller-speech cancellation path interrupts playback.

### DIDWW SIP/OpenAI Realtime bridge

The built-in bridge service is `evander-didww-sip-bridge`. It runs on the control-plane host
and exposes the bridge API consumed by `didww_sip_bridge_url`:

- `POST /phone-calls` starts a projected outbound call.
- `POST /voice-sessions/inbound-phone-calls` provisions a fixed inbound route when the bridge is
  configured to listen for inbound `Stasis(evander_didww,inbound:<did-or-route-id>)` events.
- `GET /voice-sessions/{voice_session_id}` returns status, transcript events, summary, decisions,
  action items, and any captured audio-quality diagnostics.
- `GET /voice-sessions/{voice_session_id}/audio-quality` and
  `GET /phone-calls/{call_id}/audio-quality` return the same call payload with the
  `audio_quality` section flushed from any active in-memory tracker before responding.
- `POST /voice-sessions/{voice_session_id}/hangup` ends a live call for operator cleanup.

The bridge uses Asterisk ARI instead of implementing SIP in Python. Asterisk handles DIDWW PJSIP
trunk registration/auth, outbound SIP dialogs, RTP, NAT, and hangups. Asterisk itself is open-source;
there is no Asterisk license fee, but DIDWW call/trunk usage and OpenAI Realtime usage still apply.
Inbound handling stays host-local as well: the bridge only enables its long-lived inbound listener
when `DIDWW_SIP_BRIDGE_ENABLE_INBOUND_PHONE_CALLS=true` and uses
`DIDWW_SIP_BRIDGE_INBOUND_GATEWAY_URL` plus `VOICE_MEDIA_CONTROLLER_TOKEN` to provision calls
through the gateway after matching a route. Terminal inbound results use the same controller URL and
token to sync the final bridge payload back to the gateway for voice-session reconciliation.
The bridge sends G.711 audio to OpenAI Realtime, so its Asterisk `ExternalMedia` RTP settings must
use a static payload codec. The default is A-law/PCMA:
`DIDWW_SIP_BRIDGE_MEDIA_FORMAT=alaw`, `DIDWW_SIP_BRIDGE_RTP_PAYLOAD_TYPE=8`, and
`DIDWW_SIP_BRIDGE_SAMPLE_RATE_HZ=8000`. For u-law/PCMU, use `ulaw`, payload type `0`, and the same
sample rate.

For barge-in, the bridge keeps OpenAI Realtime built-in input noise reduction enabled by default
for handset audio and keeps `server_vad` as the default turn-detection mode while disabling
automatic caller-turn response creation and immediate Realtime interruption. The bridge waits
`DIDWW_SIP_BRIDGE_BARGE_IN_DELAY_MS` before sending `response.cancel`; when substantive caller
speech cancels playback, it schedules the queued RTP tail to fade over
`DIDWW_SIP_BRIDGE_BARGE_IN_FADE_MS` while promptly truncating the assistant audio item to the
RTP-played prefix so the next model turn does not assume the caller heard queued audio. Short
configured or fuzzily matched backchannels such as `ok` or `uh-huh` are stored in the transcript
without cancelling current assistant playback. If a cancel already happened before the transcript is
classified, the bridge queues a resume/recovery response instead of waiting for the idle prompt. Use
`OPENAI_REALTIME_INPUT_NOISE_REDUCTION_TYPE=far_field` for speakerphone, headset, or room audio,
and tune `OPENAI_REALTIME_VAD_THRESHOLD`, `OPENAI_REALTIME_VAD_PREFIX_PADDING_MS`, and
`OPENAI_REALTIME_VAD_SILENCE_DURATION_MS` if you need a more or less aggressive turn detector.

Minimum host setup:

1. Install and configure Asterisk with ARI enabled.
2. Configure a DIDWW PJSIP trunk in Asterisk.
3. Copy `config/didww-sip-bridge.env.example` to `config/didww-sip-bridge.env`.
4. Put `DIDWW_SIP_BRIDGE_TOKEN`, `VOICE_MEDIA_CONTROLLER_TOKEN`, `OPENAI_API_KEY`, and
   `ASTERISK_ARI_PASSWORD` in `secrets.env` or the systemd service manager environment.
5. Set `didww_sip_bridge_url` to the bridge listener, for example `http://127.0.0.1:11888`.

For reproducible deployments, the Ansible bundle can own the Asterisk side as well. Set
`evander_asterisk_didww_enabled: true` in the operator deployment config, provide the DIDWW
SIP username/password and Asterisk ARI password from Vault, and set
`evander_asterisk_public_address` to the public SIP/RTP address advertised by the control
plane. The role renders the same ARI user/app and PJSIP endpoint names consumed by
`config/didww-sip-bridge.env`, then verifies the local ARI endpoint before the bridge service is
checked.

The known-good Asterisk/DIDWW shape for a NATed Azure control-plane host is:

- PJSIP UDP transport bound on `0.0.0.0:5060`.
- `external_signaling_address` and `external_media_address` set to the control-plane public IP.
- `local_net` set to the control-plane private subnet, for example `10.2.0.0/24`.
- DIDWW endpoint allows `ulaw,alaw`, uses `direct_media=no`, `rewrite_contact=yes`,
  `force_rport=yes`, and `rtp_symmetric=no`.
- DIDWW endpoint identification matches DIDWW signaling sources, for example `46.19.208.0/21` and
  `185.238.172.0/22`.
- Asterisk `rtp.conf` uses the narrow public RTP range `rtpstart=12000` and `rtpend=12100`.
- Azure NSG and DIDWW trunk allowed-RTP rules allow UDP `12000-12100` to the control-plane public
  IP; SIP signaling allows UDP `5060`. The Python bridge's loopback RTP range `41000-41100` is
  host-local and should not be opened externally.

The bridge creates `runtime/didww-sip-bridge-store.json`. It stores redacted phone numbers, ARI
channel IDs, RTP port metadata, route identifiers, transcripts, terminal summaries, and bounded
audio-quality counters/events. The `audio_quality` payload is keyed by both `voice_session_id` and
bridge `call_id`; it includes UTC timestamps, codec/RTP tags, packet-loss and jitter counters,
frame-gap events, queue underrun/overrun counters, and decode/parse error counts. When audio breakup
is reported, capture the bridge `call_id` or `voice_session_id` plus the rough UTC time and inspect
the `audio_quality` windows/events around that time. Caller and DID values are hashed for audit
correlation, and the bridge does not persist raw audio.

Inbound failure handling is deterministic:

- Route miss or ambiguous DID: the bridge hangs up the inbound Asterisk channel and records a
  sanitized provisioning failure.
- Unauthorized caller: the gateway returns `403`, no voice-session record is exposed, and the audit
  log contains only route ids plus redacted/hash identity fields.
- Missing or inactive origin session: the gateway rejects the request before exposing a record.
- ARI bridge or `ExternalMedia` failure after acceptance: the call record ends as `failed`, the
  bridge cleans up the Asterisk channels, and reconciliation is not duplicated.
- Caller hangup before media is bridged: the call record ends as `ended`, and the origin-session
  reconciliation wake remains idempotent.

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
- Export the matching host environment variable before launching `evander-gateway`:

  ```bash
  export DISCORD_BOT_TOKEN=...
  ```

- Use per-session channel allowlists when creating session manifests:

  ```bash
  evander-cli create-session \
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
- `evander-discord-ingest` is the host-side inbox poller. It discovers Discord channels from
  active sessions that have `discord_get_commands` and read-capable `discord_channel_allowlist`
  entries, plus enabled `trigger:communication:new-message` nodes that name read-capable Discord
  communication targets. It writes normalized messages into the Discord inbox store used by
  unread-message tools and communication triggers.
- Seed a dedicated test/control channel and get an initial cursor with:

  ```bash
  ./.venv/bin/evander-cli seed-discord-channel 123456789012345678 --runtime-dir runtime
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
  to allow `email_send` and enforce approval through MCP approval policy where required.

## Operator Approval Notifications

Approval notifications are host-side operator alerts for newly created `ApprovalRequest` records.
They are separate from guest communication permissions: a session does not need Discord, Teams, or
email send allowlists for the host to notify an operator target.

1. Define one or more host-owned communication targets with the `notify` capability. In Admin Web,
   use Configuration -> People & Communications -> Communication targets; the targets are stored in
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
   evander-cli directories config-set \
     --runtime-dir runtime \
     --domain policy \
     root \
     '{"approval_notification_target_ids":["ops-approvals"]}'
   ```

   Child directories inherit the root value unless they replace the list in their own
   `policy.json` overlay. An empty list disables approval notifications. Directory policy
   sets the inherited baseline, session policy can add more target IDs for a specific session,
   and a live workflow-run approval lease can add run-scoped targets. The runtime resolves the
   final list in that order and deduplicates first-seen IDs.

   In Admin Web, Configuration -> People & Communications -> Approval notification targets exposes
   `approval_notification_target_ids` as an explicit list of known notify-capable communication
   targets with an `Add communication target` control instead of free-form text entry.

3. Have the guest create the approval request through `approval_submit`. The notification message
   includes the `request_id`, action/tool name, directory, session and VM context, expiry,
   `human_summary`, payload digest, and review commands. It does not include the approval payload.

4. Review with Admin Web Approvals or with:

   ```bash
   evander-cli show-approval <request_id> --runtime-dir runtime
   ```

   The review payload includes notification status and delivery attempts, so failed delivery is
   visible without changing the approval state.

5. Decide through the existing audited path:

   ```bash
   evander-cli approve <request_id> <approver> --approve --reason "approved reason" --runtime-dir runtime
   evander-cli approve <request_id> <approver> --deny --reason "denied reason" --runtime-dir runtime
   ```

Delivery failure logs `metric_approval_notification` with the target id, request id, review command,
and error. It never auto-approves, denies, or blocks the pending approval record.

## Approval-Gated MCP Tool Invocations

Directory policy overlays and per-session policy can set `approval_required_tools` to require human
approval before selected MCP tools execute. This is an additional gate on top of `allowed_tools`: the
tool must still be allowed for the session, and `approval_submit` / `approval_check` are excluded so
agents can create and inspect approvals.

Some tools are code-level mandatory approval tools and do not depend on operator configuration.
`semantic_delete_points` always uses this approval gate when the session is otherwise allowed to
call it; removing it from `approval_required_tools` or setting a directory rule with
`require_approval: false` does not make it approval-free.

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
approval request id for review in Admin Web -> Tool Calls. Approval-gate audit events now include a
redacted approval payload diagnostic with the attempted payload digest and, when safe, field-level
changed/missing/extra summaries. Admin Web shows the same diagnostic on Approval and Tool Call
detail panes for operator diagnosis only; it does not change the approval boundary or exact-payload
matching requirement.

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
evander-cli directories config-set \
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
evander-cli directories config-set \
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
   `email_create_draft_with_attachments` first, then `email_send_with_attachments` only after
   `email_send_enabled: true`. Pass the exact file-send approval as `request_id`; if the tool
   invocation itself also requires MCP approval, pass that separate request as `approval_request_id`.
   The older `email_send_approved_with_attachments` tool remains available for compatibility.
4. For Discord and Teams, submit/approve the exact payload expected by `discord_post_file` or
   `teams_post_file`, including destination, message, and `artifact_ids`; then pass the approval
   `request_id` to the send tool.
   Teams image artifacts are sent inline through Graph. Teams non-image artifacts are posted as
   signed control-plane download links; configure `communication_artifact_public_base_url` in
   Admin Web under Configuration -> People & Communications -> Communication artifact links, via provider
   config, or with `EVANDER_COMMUNICATION_ARTIFACT_PUBLIC_BASE_URL`. Set the secret named by
   `communication_artifact_link_secret_env` in an environment loaded by both gateway and Admin Web,
   usually repo-root `secrets.env`.
   For Azure DevOps PR/backlog tools, approve the exact comment/text payload, `mentions`, and
   artifact IDs.
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
evander-cli rebuild-audit-index --config runtime
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
from evander.communication_artifacts import CommunicationArtifactStore
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
  and the intended MCP approval policy are both intentionally enabled.
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
   - `has_pending_feedback()`
2. Unread fetch when pending is true:
   - `list_unread_feedback(last_read_cursor, limit)` (set `limit=1` for strict one-comment handling)
   - `list_all_feedback(limit)` when you need full PR comment history, including already-read items
   - `list_pull_requests(provider, repo, status?, statuses?, creator_id?, reviewer_id?, project?,
     min_time?, max_time?, query_time_range_type?, source_ref_name?, target_ref_name?, tags?, top?,
     skip?, cursor?)` to enumerate PRs directly from the provider. Azure DevOps status values are
     forwarded to `searchCriteria.status`; `closed` expands to ADO `completed` and `abandoned`.
   - `get_pull_request(provider, repo, pr_id, include_reviewers?, include_commits?)`
     to fetch a single provider PR record, map stable top-level fields (title/status/branches/commits/reviewers),
     and preserve provider-specific details in `raw`.
   - `resolve_ado_identity(query)` to resolve `me`, an email/UPN, display name, ADO id, or descriptor
     before using personal activity filters.
   - `list_ado_pr_comment_activity(repo, since, author?, until?, project?, statuses?, top?, skip?)`
     to find ADO PR comments matching an author and date range.
   - `list_ado_pr_commit_activity(repo, since, author?, until?, project?, statuses?, top?, skip?)`
     to find ADO PR commits matching an author and date range.
3. Post threaded replies (when needed):
   - `post_pr_feedback_reply(provider, repo, pr_id, body_text, thread_id/comment_id, idempotency_key)`
4. Cursor advancement:
   - advance `last_read_cursor` only after local handling and any provider reply posting have completed durably.

For session-scoped MCP connections, agents should omit `session_id` on these self-scoped tools. The
gateway binds the caller session from the MCP connection and rejects conflicting legacy
`session_id` arguments.

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

### Read-only PR integration preflight

Use the preflight when you need to check whether one PR, every pair in a related
set of PRs, or a proposed merge order will conflict before you take any action
on the branch.
Admin Web exposes the same check under **Reports > PR integration preflight**
and calls:

```bash
POST /api/admin/pr-integration-preflight
```

Request body fields:

- `provider`: use `azure_devops`, `azure-devops`, or `ado`; the endpoint
  normalizes them to Azure DevOps only.
- `repo`: the Azure DevOps repo identifier.
- `mode`: `single`, `pairwise`, or `ordered`. Pairwise mode checks every
  unordered PR pair in the submitted set.
- `pr_ids`: an array of PR ids, or a comma/newline-delimited string in the UI.
- `directory_path`: defaults to `root`.
- `repo_source`: optional; omit it when the selected directory already defines
  `ado_worker.ado_repo_source`.
- `organization_url` and `project`: optional Azure DevOps overrides.

The response includes `status`, `summary_text`, `participants`, `checks`,
`warnings`, and `errors`. Status values are:

- `clean`: the merge simulation was clean.
- `conflict`: one or more merge checks found content conflicts.
- `missing_ref`: a source or target commit/ref could not be resolved locally.
- `stale_target_branch`: the target branch moved since Azure DevOps recorded it.
- `repo_error`: the local repository could not be resolved or queried.
- `provider_error`: the Azure DevOps lookup failed.

Example request and conflict summary:

```bash
curl -sS -X POST http://127.0.0.1:8100/api/admin/pr-integration-preflight \
  -H 'content-type: application/json' \
  -d '{
    "provider": "azure_devops",
    "repo": "repo-1",
    "directory_path": "root/team-a",
    "repo_source": "/work/repo",
    "mode": "ordered",
    "pr_ids": ["17", "18"]
  }'
```

```json
{
  "status": "conflict",
  "summary_text": "ordered preflight found conflict for PRs 17, 18: 2 conflicted paths.",
  "participants": [
    {
      "pr_id": "17",
      "source_commit": "1111111",
      "target_commit": "aaaaaaa",
      "status": "clean"
    },
    {
      "pr_id": "18",
      "source_commit": "2222222",
      "target_commit": "aaaaaaa",
      "status": "conflict",
      "explanation": "merge conflict on 2 paths: src/app.py, tests/test_app.py"
    }
  ],
  "checks": [
    {
      "index": 1,
      "pr_ids": ["17"],
      "status": "clean",
      "explanation": "merged cleanly"
    },
    {
      "index": 2,
      "pr_ids": ["17", "18"],
      "status": "conflict",
      "conflicted_paths": ["src/app.py", "tests/test_app.py"]
    }
  ]
}
```

This is read-only: it does not update refs, resolve conflicts, push branches,
or change PR completion policy.

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

- `evander-cli discord-wake` owns Discord inbox wakeups and resumes.
- `evander-cli scheduled-wake` owns scheduled resume dispatch.
- Existing Azure DevOps PR feedback ingestion and PR completion polling are
  still owned by `evander-ado-worker`. Legacy trigger mode writes
  `runtime/pr-feedback-store.json`, updates `runtime/pr-map.json` PR status
  fields, and publishes `trigger:pull-request:new-feedback` plus
  `trigger:pull-request:completed` events directly. Explicit trigger mode is
  selected by setting `repo` on the PR trigger; it lists candidate PRs from ADO
  using the trigger criteria, stores trigger-owned cursors outside
  `pr-map.json`, and emits normalized PR events without session bindings.
  `trigger:pull-request:new-feedback` can set `agent_author_names`,
  `agent_mention_names`, `agent_author_ids`, and `agent_mention_ids` so
  downstream gates can filter on
  `feedback.metadata.agent_directed` without running host scripts. Agent-authored
  feedback still publishes by default; set `suppress_agent_authored_feedback`
  only for workflows that should store those comments without emitting wake
  events.
  Keep `pr-map.json` compatibility until a later cleanup removes the older path.
- Recommended service lifecycle commands:
  - `sudo systemctl start evander-discord-wake.service`
  - `sudo systemctl stop evander-discord-wake.service`
  - `sudo systemctl status evander-discord-wake.service`
  - `sudo systemctl start evander-scheduled-wake.service`
  - `sudo systemctl stop evander-scheduled-wake.service`
  - `sudo systemctl status evander-scheduled-wake.service`
- Discord wake cadence and resume behavior are controlled by `config/discord-wake/config.json`.
  When no explicit directory list is configured, the worker derives its scan scope from active
  session directories. It wakes sessions from unread Discord inbox messages only for channels
  resolved from that session's read-capable `discord_channel_allowlist`; global operator Discord
  channels are not used as wake routes.
- Scheduled wake cadence and resume behavior are controlled by `config/scheduled-wake/config.json`.
  The scheduled-wake worker reloads that file between cycles, so saved changes
  such as `scheduled_wake_prefix_text` apply without restarting
  `evander-scheduled-wake.service`.
- Scheduled wakes are recorded in the durable wake request store with current
  ledger status, latest dispatch evidence, sanitized failure reason, scheduled
  time, source, and redacted prompt summary. Admin Web exposes these entries on
  the Sessions page, and operators can use `GET /api/admin/scheduled-wakes` with
  `statuses`, `session_id`, `limit`, and `include_completed` query parameters to
  list/filter recent wake delivery state.
- A `dispatched` scheduled wake means the resume command was launched or handed
  to the worker backend; it is not treated as completed until agent-run
  completion evidence is recorded. One-time wakes stay enabled while awaiting
  confirmation, and Admin Web labels these entries as awaiting confirmation.
  `completion_confirmation_timeout_seconds` in
  `config/scheduled-wake/config.json` controls how long the scheduled-wake
  worker waits before an unconfirmed one-time dispatch becomes recoverable
  again. If an operator retries a one-time wake from Admin Web or the API, the
  retry starts a fresh automatic confirmation batch instead of inheriting the
  exhausted attempt counters from the previous batch.
- Failed, due, and missed/late scheduled wakes can be retried immediately or
  rescheduled with `POST /api/admin/scheduled-wakes/{request_id}/retry`. Send
  `{ "run_now": true }` for immediate retry or `{ "resume_at": "<ISO-8601>" }`
  to reschedule the existing request while preserving retry history.
- `completion_confirmation_retry_max_attempts`,
  `completion_confirmation_retry_window_seconds`,
  `completion_confirmation_retry_base_backoff_seconds`, and
  `completion_confirmation_retry_max_backoff_seconds` in
  `config/scheduled-wake/config.json` bound the automatic retry batch for
  one-time wakes that are waiting on completion confirmation. Those settings do
  not change cron wake handling.
- `late_threshold_seconds` in `config/scheduled-wake/config.json` controls how
  overdue a scheduled wake can be before the worker/API marks it
  `missed_or_late`.
- In both wake worker configs, `backoff_seconds` controls how long the worker waits before
  retrying after a whole-cycle exception. `cooldown_seconds` remains the per-session delay used
  after activity or busy-session checks, not the process-level exception retry delay.
- `discord_resume_prompt` remains directory-scoped and is surfaced in the admin GUI under Communications; it is used by the Discord wake worker.
- `scheduled_wake_prefix_text` lives in the scheduled-wake worker global config, defaults to empty, and lets operators prepend optional text to scheduled wake dispatch prompts.
- MCP-triggered scheduled resumes are always dispatched with `resume_mode=same_context`.
- For `container_worker_vm` sessions, that same-context dispatch uses Codex resume-last behavior when a worker prompt does not already carry an explicit thread id.
- Admin-created recurring wake schedules can still request `resume_mode=new_context`, which starts a fresh Codex exec session on container workers.
- Busy container wakes queue the prompt for later dispatch and still count as handled for schedule advancement.
- Expired sessions do not dispatch wakes. The wake history records a failed
  dispatch with the session-expired message, and cron or one-time requests stay
  due until `effect:session:renew-if-expired` or an operator renewal extends the
  session.
- Recurring cron wake schedules are consumed only after a successful host resume dispatch. Failed, timed-out, or missing-script attempts stay due/retryable and remain visible through wake dispatch history.
- Wake schedule CRUD is available in the CLI under `wake-schedules`:
  - create one-time wake: `evander-cli wake-schedules create --runtime-dir runtime --session-id issue-11-session --resume-at 2026-04-01T10:30:00Z --prompt "Resume pending work."`
  - create recurring wake: `evander-cli wake-schedules create --runtime-dir runtime --session-id issue-11-session --cron "*/10 * * * *" --prompt "Periodic check-in." --mode new_context`
  - edit existing wake: `evander-cli wake-schedules edit <request-id> --cron "*/15 * * * *" --prompt "Updated prompt"`
  - delete wake: `evander-cli wake-schedules delete <request-id>`
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
They also accept `mentions` on top-level comments, inline comments, review summaries, batched
review comments, and feedback-thread replies. Mention refs use the same ADO identity ID,
descriptor, email/UPN, or display/name lookup behavior as `post_ado_backlog_comment`; the PAT must
be able to read ADO identities, equivalent to Azure DevOps OAuth scope `vso.identity`.

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
Use the prompt's `error` source port for notification, retry, or cleanup
branches that should only fire when a supported prompt dispatch failure is
routed. If no error edge is wired, the prompt still fails normally.
For `effect:agent:enqueue-prompt`, routed dispatch failures preserve retry
context such as `queued`, `queue_key`, and `coalesced_count` when the prompt was
queued before dispatch failed. Queue retries are also capped by
`agent_prompt_queue_max_retries` (default `3`) for the same queue key and prompt
digest. When the cap is exceeded, the prompt effect fails with
`failed_phase: retry_limit` and reports `retry_count` plus `max_retries`.
For Codex container-worker prompts, completed executions marked as context
exhausted are retried once with deterministic reduced context by default.
Use `agent_context_retry_enabled`, `agent_context_retry_max_attempts`, and
`agent_context_retry_target_chars` to tune or disable that behavior.

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

- resolves explicit target/head refs to local refs under `refs/evander/review/*`
- computes `REVIEW_DIFF_BASE_COMMIT` with `git merge-base` so normal review findings use
  `REVIEW_DIFF_BASE_COMMIT..REVIEW_HEAD_COMMIT` rather than target/head drift
- detaches the worktree onto the head commit
- hard-resets and cleans the worktree
- writes `.evander/review-context.env` for downstream prompt or shell steps
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
evander-cli directories config-set \
  --runtime-dir runtime \
  --domain policy \
  root \
  '{"email_mailbox_allowlist":["ops-gmail","exec-graph","finance-graph"],"email_folder_allowlist_by_mailbox":{"ops-gmail":["inbox","drafts"],"exec-graph":["inbox","drafts"],"finance-graph":["inbox","drafts"]}}'

evander-cli create-session real-session --runtime-dir runtime
```

## Host-Side NuGet Access

- The scaffold includes `evander-nuget-relay`, a host-side relay that proxies `nuget.org` and private Azure Artifacts feeds.
- The relay reads Azure feed credentials from host environment (`AZURE_ARTIFACTS_PAT`) and does not require guest-side private feed credentials.
- Configure the relay environment in `config/nuget-relay.env`.
- After installing units, run `./scripts/bootstrap_nuget_vm.sh <vm-name>` so guest restores target relay URLs on `http://192.168.250.1:11666`.

## Host-Side npm Audit Facade

- The scaffold includes `evander-npm-audit-facade`, a host-side relay that forwards npm audit POSTs to `https://registry.npmjs.org` while routing package metadata and tarballs through the control-plane Nexus npm group.
- The facade listens on `11881` and exposes `/healthz` for host-side and guest-side smoke checks.
- Configure any optional host-local overrides in `config/npm-audit-facade.env`. The runtime bind and upstream settings still come from the directory/runtime config surface.
- Manual smoke commands:

```bash
curl -fsS http://127.0.0.1:11881/healthz
curl -fsS http://127.0.0.1:11881/left-pad
curl -fsS -X POST http://127.0.0.1:11881/-/npm/v1/security/audits/quick \
  -H 'content-type: application/json' \
  --data '{}'
```

## Host-Side Azure DevOps PR Worker And Work-Item Workflow Intake

- The scaffold includes `evander-ado-worker` and the equivalent foreground CLI command
  `evander-cli poll-ado-backlog`.
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
- Use `trigger:ado:work-item-comment-created` when new ADO comments should wake a workflow. The
  trigger owns `runtime/trigger-cursors-ado-work-item-comments.json`, baselines existing comments
  by default, and leaves mention text, bot author exclusion, tags, and state filters to workflow
  config.
- For new remote issue workflows, prefer the provider-neutral primitives
  `trigger:remote-issue-matching`, `trigger:remote-issue-comment-created`, and
  `query:remote-issues`. Set `provider=azure_devops` or `provider=ado` for ADO work-item filters
  including WIQL/current iteration, or `provider=github` for GitHub issue filters such as
  `repo`, `state`, labels, assignee, author, mentions, milestone, and `since`. GitHub reads use
  the directory-scoped `github_token_env` when configured and may read public issues without a
  token when GitHub allows it.
- For terminal-PR cleanup workflows using legacy PR-map mode, wire
  `trigger:pull-request:completed.session_id` into `effect:agent:destroy.session_id`. For explicit
  PR trigger mode, route with `query:session:list` against session metadata and pass the selected
  session id downstream.
- A workflow-upserted ADO work item is represented in the unified local issue store as a proxy issue
  (`source: proxy_azure_devops`) keyed by local issue number with remote linkage in
  `metadata.remote` and a last-seen cache in `metadata.proxy_cache`.
- Re-polling the same remote issue updates the existing local proxy record/cache instead of
  creating duplicates, so Desktop and CLI views remain stable across workflow runs.
- `evander-proxy-issue-sync` keeps all Azure DevOps proxy issues fresh independently of the
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
- The ADO worker `directories` list is exact-match. Add each child directory that should be
  polled; a parent directory no longer implicitly observes sessions in its descendants.
- `ADO_*` env vars continue to work as compatibility fallbacks for one release and log deprecation
  warnings.
- Materialize `root/ado-worker.json` with `evander-cli migrate-ado-worker-env-to-root` or
  `migrate-global-config-to-root` when you still use directory-scoped PR automation settings.

Foreground runbook:

```bash
set -a; source secrets.env; set +a
evander-cli poll-ado-backlog --log-level INFO
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
sudo systemctl start evander-ado-worker.service
sudo systemctl start evander-proxy-issue-sync.service
sudo systemctl status evander-ado-worker.service
sudo systemctl status evander-proxy-issue-sync.service
sudo journalctl -u evander-ado-worker.service -f
```

Workflow work-item intake example:

```json
{
  "type": "trigger:remote-issue-matching",
  "config": {
    "provider": "azure_devops",
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

GitHub issue intake example:

```json
{
  "type": "trigger:remote-issue-comment-created",
  "config": {
    "provider": "github",
    "directory_path": "root/team-a",
    "repo": "owner/repo",
    "state": "open",
    "labels_any": ["truman"],
    "comment_contains": "@WorkflowBot",
    "top": 50
  }
}
```

Failure recovery:

- `--reprocess` and `--list-unprocessed` are deprecated with workflow-owned work-item intake.
- `--forget` remains available only for removing legacy processed-store entries during migration.
- To backfill ADO proxy issues, run `query:remote-issues` with `provider=azure_devops`
  or the compatibility `query:ado:work-items`, then feed its `items` array into
  `effect:issue:upsert-ado-proxy.items`.

- If the service is crash-looping, validate config and PAT availability with a foreground run,
  then restart the unit:

  ```bash
  sudo systemctl restart evander-ado-worker.service
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

- The scaffold includes `evander-trigger-orchestrator`, a singleton host worker that polls
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
evander-trigger-orchestrator --runtime-dir runtime --once --log-level DEBUG
```

Run continuously under systemd:

```bash
sudo systemctl start evander-trigger-orchestrator.service
sudo systemctl status evander-trigger-orchestrator.service
sudo journalctl -u evander-trigger-orchestrator.service -f
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
- Container-backed agents receive `HTTP_TARGET_RELAY_BASE_URL` and should treat it as an opaque
  relay prefix; tokenized sessions may include relay auth in that prefix. Build relay URLs as
  `$HTTP_TARGET_RELAY_BASE_URL/session/<session-id>/target/<alias>/` rather than calling the public
  Azure URL directly.

The older `scripts/provision_exampleindustry_app_demo_environment.sh` wrapper is retained only for
compatibility with older live workflow packs and should not be used for new provisioning graphs.

### Teams and Discord ingest workers

- `trigger:teams:new-message` and `trigger:teams:direct-message` events are produced by a
  host-side Teams ingest worker that polls Microsoft Graph on the host and writes into the Teams
  message store consumed by trigger orchestration.
- The ingest worker uses the same delegated Teams auth settings described above.
- The Discord ingest worker polls the Discord Bot API on the host and writes into the Discord inbox
  store. Its target set comes from active sessions with `discord_get_commands` plus read-capable
  Discord session allowlists, and from enabled provider-neutral communication triggers that name
  read-capable Discord targets.
- `trigger:communication:new-message` is the provider-neutral inbound chat trigger for declared
  communication targets. Configure it with explicit `communication_target_ids`; each selected
  target must be a read-capable Teams channel/chat or Discord channel target in the trigger's
  `directory_path`. The Teams and Discord ingest workers observe those declared targets and feed
  the host-owned inbox stores consumed by the trigger.
- Direct-message triggers require `sender_user_ids`, which accepts Graph user IDs, UPNs, or mail
  addresses so only configured one-on-one senders can wake workflows.
- This polling path is independent from the wake workers; Discord/scheduled wake behavior now stays with
  `evander-discord-wake` and `evander-scheduled-wake` while Azure DevOps PR feedback ingestion stays with
  `evander-ado-worker`.

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

- `evander-firewall.service`
- `evander-gateway.service`
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
- `evander-teams-ingest.service`
- `evander-teams-meeting-ingest.service`
- `evander-effect-orchestrator.service`
- `evander-trigger-orchestrator.service`

Before running install mode, ensure preflight is clean (or only warning-only), then:

1. Copy `config/firewall.env.example` to `config/firewall.env` if needed.
2. Ensure `runtime/directory-config/root/ado-worker.json` exists if you use directory-scoped PR
   automation settings.
3. Ensure the needed service env vars are available. The rendered units will load optional
   per-service `config/*.env` files and optional repo-root `secrets.env`.
4. Ensure the host environment exposes the secret env vars referenced by `openai_api_key_env`
   or `azure_openai_api_key_env` for the OpenAI relay, `OPENAI_API_KEY` for the DIDWW SIP bridge
   when using default OpenAI Realtime settings, and `AZURE_ARTIFACTS_PAT` when the NuGet relay
   needs private Azure Artifacts access. The npm audit facade does not require a host secret by
   default, but you can keep optional local overrides in `config/npm-audit-facade.env`.
   Additional directory-specific key env vars should be supplied through `config/admin-web.env`,
   `secrets.env`, or the service manager environment. The HTTP
   target relay is auth-transparent and exists to enforce destination allowlisting for approved external web
   endpoints. In the APP demo workflow, guests authenticate to APP interactively or with
   guest-managed bearer tokens/cookies through that relay path.
5. For private PR feedback polling, ensure the service environment exposes `GITHUB_TOKEN`
   and/or `AZURE_DEVOPS_PR_PAT` and ensure the effective `root` provider config sets the correct
   `azure_devops_organization_url`, `azure_devops_project`, and `azure_devops_pr_pat_env`.
6. Start and inspect services:

```bash
sudo systemctl start evander-firewall.service
sudo systemctl start evander-gateway.service
sudo systemctl start evander-openai-relay.service
sudo systemctl start evander-didww-sip-bridge.service
sudo systemctl start evander-nuget-relay.service
sudo systemctl start evander-npm-audit-facade.service
sudo systemctl start evander-http-target-relay.service
sudo systemctl start evander-ado-worker.service
sudo systemctl start evander-proxy-issue-sync.service
sudo systemctl start evander-discord-ingest.service
sudo systemctl start evander-discord-wake.service
sudo systemctl start evander-scheduled-wake.service
sudo systemctl start evander-teams-ingest.service
sudo systemctl start evander-teams-meeting-ingest.service
sudo systemctl start evander-effect-orchestrator.service
sudo systemctl start evander-trigger-orchestrator.service
sudo systemctl status evander-firewall.service
sudo systemctl status evander-gateway.service
sudo systemctl status evander-openai-relay.service
sudo systemctl status evander-didww-sip-bridge.service
sudo systemctl status evander-nuget-relay.service
sudo systemctl status evander-npm-audit-facade.service
sudo systemctl status evander-http-target-relay.service
sudo systemctl status evander-ado-worker.service
sudo systemctl status evander-proxy-issue-sync.service
sudo systemctl status evander-discord-ingest.service
sudo systemctl status evander-discord-wake.service
sudo systemctl status evander-scheduled-wake.service
sudo systemctl status evander-teams-ingest.service
sudo systemctl status evander-teams-meeting-ingest.service
sudo systemctl status evander-effect-orchestrator.service
sudo systemctl status evander-trigger-orchestrator.service
```

The Discord wake and scheduled wake units must run as the same operator user as the
gateway and orchestrator services so they can read the operator user's SSH keys and
write operator-owned wake state. After installing units, verify the rendered service
users:

```bash
systemctl show -p User evander-discord-wake.service
systemctl show -p User evander-scheduled-wake.service
```

Both commands should report the operator account used for deployment, not an empty
root/default user.

## Local Git Remote Flow

For the common issue workflow, create the VM first:

```bash
MSSQL_SA_PASSWORD='your-strong-password' ./.venv/bin/evander-cli create-issue-vm 14084 --dotnet --nuget /path/to/project-or-solution.slnx --mssql --mssql-init-root /path/to/mssql-init --playwright
```

Then clone the repo and apply repo-specific bootstraps as needed:

```bash
./.venv/bin/evander-cli clone-repo-into-vm vm-14084 --repo-source /path/to/local/repo
./.venv/bin/evander-cli bootstrap-python-in-vm vm-14084 --host-checkout /path/to/local/repo --target-dir /home/agent/$(basename /path/to/local/repo)
./.venv/bin/evander-cli bootstrap-frontend-in-vm vm-14084 --host-checkout /path/to/local/repo --install-root /path/to/local/repo/Src/Clients
```

`create-issue-vm` still resolves issue-scoped Git settings from the `root` issue setup config,
including `EVANDER_GIT_ROOT` and `EVANDER_GIT_SSH_ALIAS`, so later repo clone steps
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
it as an agent-writable guest directory and exports `EVANDER_ARTIFACTS_DIR`.
Use `query:agent:artifact-text` with a session id and relative path to read a
UTF-8 text file from that folder.
Agents can call the `stage_artifact` MCP tool with a file or directory path
under `/home/agent` and an optional relative `destination_path` to copy
deliverables into that folder without changing directories.
Use `effect:artifact:stage` to copy a file or directory from `/home/agent` into
that folder, then `effect:artifact:publish-artifacts` to publish the full folder
as an immutable keyed `tar.gz` archive under `runtime/published-artifacts/`.
Set `source_paths` to publish only specific relative files or directories under
`/artifacts`; unsafe paths, symlinks, and paths outside the artifacts root are
rejected.
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
evander-cli list-vms
evander-cli list-vms --all
evander-cli logs 14084 --lines 50
```

For a lower-level point-in-time VM inspection, `./scripts/check_issue_vm.sh 14084`
is still available.

The script and CLI wrappers share the same timeout environment variables (defaults shown):

- `VM_WAIT_IP_TIMEOUT_SECONDS` (default `180`)
- `VM_WAIT_SSH_TIMEOUT_SECONDS` (default `180`)


Create a host-local bare repository:

```bash
evander-cli directories config-set --domain issue_setup root "{\"git_root\": \"$PWD/git-remotes\", \"git_host\": \"192.168.250.1\", \"git_user\": \"$(whoami)\", \"git_ssh_alias\": \"evander-git-host\"}"
./scripts/setup_issue_git_vm.sh vm-real 14084
```

That one command bootstraps the guest SSH key path, binds writable access to the
issue-numbered repo's `refs/heads/agents/<session-id>/...` namespace, and creates or refreshes the
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
./.venv/bin/evander-cli clone-repo-into-vm vm-real --repo-source /path/to/local/repo
./.venv/bin/evander-cli clone-repo-into-vm vm-real --repo-source https://example.com/org/repo.git
```

Issue mirrors now carry a `post-receive` hook. When the bare repo has a trusted clone mapping,
guest pushes to `refs/heads/agents/...` can automatically promote the branch into the trusted clone,
push the review branch to `origin`, create or reuse the upstream PR, and append an event to
`runtime/agent-events.log`.

### PR body boilerplate controls

PR body composition for automated `open_agent_pr.sh --create-pr` runs is controlled by host
environment variables:

- `EVANDER_PR_FIXED_BODY_FILE` (optional): absolute path to operator-maintained markdown
  boilerplate that should always appear at the top of auto-created PR descriptions. This is a
  host-wide override; the directory-scoped `pr_template` domain is the primary per-directory
  surface for PR boilerplate.
- `EVANDER_PR_NOTES_REF` (optional): host-wide override for the notes ref used to read
  agent summary markdown from the promoted commit. New repo clones configure
  `evander.prNotesRef` as `refs/notes/agent-pr/<session-hash>`, where
  `<session-hash>` is the first 12 hex characters of `sha256(session_id)`. Older checkouts
  still default to the legacy `refs/notes/agent-pr` ref.
- `EVANDER_PR_BODY_MAX_BYTES` (optional, default `60000`): hard byte cap applied to the
  composed PR body before creating/updating the PR.
- `EVANDER_PR_BODY_ARTIFACT_IDS` (optional): comma-, semicolon-, or newline-separated staged
  artifact IDs to append to the Azure DevOps PR description. The same behavior is available with
  repeated `open_agent_pr.sh --body-artifact-id <artifact-id>` options.

PR creation behavior is also directory-scoped via `ado-worker.json` overlays:

- `create_prs_automatically` (default `true`): when `false`, hook automation still promotes and
  pushes review branches but skips `--create-pr`.
- `create_prs_as_draft` (default `false`): when `true`, Azure DevOps PRs are opened as drafts.

Optional host-wide runtime overrides are available:

- `EVANDER_AUTO_CREATE_PRS` to force-enable/disable PR creation across all directories.
- `EVANDER_CREATE_PRS_AS_DRAFT` to force draft vs ready-for-review for Azure DevOps PRs.

Per-session overrides are supported through session metadata in `runtime/sessions.json`:

- top-level metadata keys: `create_prs_automatically`, `create_prs_as_draft`
- or nested metadata object: `pr_automation.create_prs_automatically`, `pr_automation.create_prs_as_draft`

To migrate the legacy repo-level `config/pr-template.md` into the root directory-scoped template,
run:

```bash
evander-cli migrate-pr-template-to-root --config config/gateway.yaml
```

On guest issue VMs, prefer `git agent-pr-note set` / `git agent-pr-note append` instead of raw
`git notes add -f`. The helper keeps an additive branch-local summary cache. Agents should use
`git sync-base` when they need to merge the latest configured base branch, such as `main`, because
their local coding branch is `work`. `git push-agent` fetches the configured destination and base
branches through the guest-visible mirror before pushing. If either branch has commits missing from
`HEAD`, `git push-agent` refuses the push and prints the merge command the agent should run. Once
those branches are contained, it reattaches the full summary to the current `HEAD` before it pushes
the configured `evander.prNotesRef` notes ref.

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

- with fixed boilerplate only when `EVANDER_PR_FIXED_BODY_FILE` is set
- otherwise with the default PR body behavior from `open_agent_pr.sh`

Security reminder: git note content is untrusted agent-authored text. Treat it as markdown only;
never interpolate PR body text into shell commands.

Or mirror the default host repo into an issue-numbered bare remote:

```bash
./scripts/mirror_issue_repo.sh 14084
```

Freshness behavior for `mirror_issue_repo.sh`:

- local repo source path: default is **upstream fetch + safe fast-forward + divergence check**
- remote URL source: default is **always fetch latest refs** into cache before mirror update
- `--sync-remote`: explicitly force that fetch/fast-forward/divergence-check behavior for local source
- `--no-sync-remote`: skip upstream fetch for local/offline-only mirroring
- `--force-local`: continue even when local and remote default branches diverged
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
`evander-git-host` so the VM does not need your host password. For writable access, pass
`EVANDER_GIT_REPO=<repo-name-or-path>` and `EVANDER_GIT_SESSION_ID=<session-id>`. With
`EVANDER_GIT_REPO` but no session metadata, the helper installs read-only access for only that
repo; without repo metadata, it refuses to install an unscoped key.

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

When a workflow only needs to update an existing PR source branch from a trusted host checkout, use
`effect:git:push-branch` instead of `effect:pr:create`. The typed push effect keeps the destination
branch preflight and returns `reason: destination_branch_ahead` for merge-required retries, but it
does not create a new PR or patch an existing PR description.

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
- Keep VM disks and seed ISOs out of home directories when using `qemu:///system`; the default scripts now place them under `/var/lib/libvirt/images/evander`.


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
