# Operations

For repository security scans, use `effect:security:scan-repo` with a live session
and its absolute repository path. The [Codex Security provider guide](../packages/codex-security-addon/README.md)
describes separate installation, host-seeded VM/worker runtimes, subscription
broker profiles, private reports, and bounded smoke checks. Connect the node's
`completed` and `error` ports explicitly; completed findings are data for gates,
while incomplete coverage is an error.

The opt-in [tracked coverage controller](../packages/codex-security-addon/docs/tracked-coverage.md)
retains the original inventory and accepted component reviews for explicit
continuation. Provision seven-hour scanner/notification sessions for its six-hour
budget. Deliver partial artifacts before agent summaries, and hold repository
reservations until `cleanup_confirmed` is true. Deploy worker code and the rebuilt
scanner image, then qualify interruption/resume on a bounded synthetic repository
before enabling it in operator workflows.

For website design changes, use the optional [WordPress Add-on](../packages/wordpress-addon/docs/operations.md).
Its setup verifies recovery before enabling writes, and its directory-scoped workflow
resumes publication, live checks and conditional restore after a worker restart.

For registered services and disposable test resources, install the independent
[SQL Server](../packages/sqlserver-addon/docs/operations.md) and/or
[RabbitMQ](../packages/rabbitmq-addon/docs/operations.md) add-ons. Allocation and
production operations start disabled. Their worker timers retain cleanup during
control-plane outages. Keep the legacy [Test Fixtures add-on](../packages/test-fixtures-addon/docs/operations.md)
available in drain mode until its existing resources and bindings are verified absent.

Use this reference for setup, ongoing administration, and recovery. For a first deployment, begin
with [host packages](#host-packages), the [bootstrap flow](#bootstrap-flow), and
[systemd services](#systemd-services). For an existing deployment, start with
[the broad health check](#first-troubleshooting-step) and then the relevant domain:

| Task | Runbook |
|---|---|
| Inspect sessions, approvals, configuration, or capacity | [Admin Web](#admin-web-operator-console) and [directory operations](#directory-operations) |
| Activate a common workflow after installation | [Workflow starters](workflow-starters.md) |
| Configure or troubleshoot execution | [Execution backends](execution-backends.md) and [container workers](container-worker-backend.md) |
| Trace a workflow or wake | [Workflow runtime](#workflow-runtime-files-and-troubleshooting) and [wake workers](#wake-workers) |
| Configure models and package access | [Host-side model access](#host-side-llm-access), [NuGet](#host-side-nuget-access), and [npm audit](#host-side-npm-audit-facade) |
| Configure communications | [Teams](#microsoft-teams-delegated-auth), [Discord](#host-side-discord-access), and [email](#host-side-email-access) |
| Call an existing assistant through Teams, with camera or screen context | [Teams assistant calls](teams-calls.md) |
| Register or recover code transport | [Managed Git operations](#managed-git-repository-operations) |
| Validate a deployment | [Live smoke tests](live-smoke-tests.md) |

## MCP SDK compatibility

The gateway and installed add-ons use MCP Python SDK `>=2.3.0,<3`. The checked-in
`uv.lock` records the tested dependencies; local development can install them with
`uv sync --frozen --extra dev`. SDK 2 retains the legacy `initialize` protocol, so
existing clients can continue using their session-scoped gateway URLs.
A bare `GET /mcp` can return HTTP 400 (missing protocol session ID), or 406 on
older transports; either proves endpoint reachability, not an authenticated tool
call. The CLI health check already accepts these responses.

Clients using MCP `2026-07-28` send independent requests without an MCP transport
session ID. They must carry the Evander session identity and its proof token on
each request, normally through the existing scoped URL or
`x-evander-session-id` and `x-evander-mcp-session-token` headers. Tool discovery and
invocation still evaluate current grants, workflow approval leases and exact
invocation approvals. An Evander session is distinct from an SDK transport session;
the absence of the latter does not grant access to another Evander session.

The gateway accepts MCP HTTP bodies up to 320 MiB. This wire limit accommodates
the existing configurable 50 MiB Kibana NDJSON import limit even when JSON string
escaping expands the request. Tool-specific logical limits still apply;
requests above the wire limit receive HTTP 413 before tool dispatch.

Policy and domain-validation failures remain actionable tool errors. Unexpected
provider failures return a generic SDK error; inspect the corresponding operator
audit record for the provider failure and its classification. Domain-validation replies include field diagnostics without Pydantic input dumps.

The Figma and Miro MCP transports use HTTPX 2, which verifies TLS using the host OS
trust store. Install any required private CA in that trust store, or configure
`SSL_CERT_FILE`/`SSL_CERT_DIR`; their ordinary REST transports continue using HTTPX.
Both MCP clients allow SSE events up to 16 MiB to accommodate base64 images,
paired structured/text results and JSON escaping. Their existing content limits
remain 8 MiB per Figma screenshot and 1 MiB per Miro result.
Verify the Ansible package-install path in an isolated environment as well as the
locked test environment before deploying a new SDK release.

For contextual release reintegration, verifier recovery, retained logs and staged worker
toolchain rollout, see [Release recovery and verifier policy](release-recovery-and-verifier-policy.md).

For agent recovery of Git provenance, composition, workspace, and publication incidents,
see [Autonomous Git recovery](autonomous-git-recovery.md). Deploy compatible workers first;
recovery preservation holds also protect session and VM teardown.

For the privacy inventory, approved cleanup jobs, holds, reviewer exports, incident references,
credential upgrade and restore caveats, see [Privacy operations](privacy-operations.md).

For effect handoff timing, bounded recovery scheduling, and deployment verification, see
[Orchestration latency and recovery](orchestration-latency.md).

## Miro visual collaboration

Enable `miro-addon` in **Add-ons**, restart the listed gateway/Admin Web/orchestrator services,
and configure an OAuth connection and explicit board grants in **Miro**. Grant agents the required
Miro tools through normal session policy; existing grants also expose `miro_list_connections()`.
Agents use the returned connection IDs with `miro_list_boards` to find approved boards. Discovery
includes inherited connections and does not contact Miro. Only workflows
that include a Miro polling trigger monitor boards. Use the connection activity view for budget
and polling failures. For an uncertain write, use **Read back**, inspect Miro, and record the
evidence with **Mark applied** or **Mark not applied** before scheduling further writes.
See [Miro setup and workflow examples](miro-addon.md); examples are never installed into live
workflow packs automatically.

## Figma mockup collaboration

For native UI mockup collaboration, see [Figma setup and recovery](figma.md). Enable the optional
add-on, connect an approved OAuth client, and pass its create/edit/render/readback/cleanup test in
a disposable file before granting agent authoring. The same page shows snapshots and uncertain
operations. Sessions with `figma_query` or `figma_execute` can call `figma_list_targets()` to discover
enabled files in their exact directory without a separate discovery grant. Inspect and reconcile
interrupted writes before resuming that file; never retry them under new request IDs to bypass
recovery. This add-on uses existing host services and MCP ports.

## Personal Azure DevOps PR connections

Users manage Microsoft OAuth and personal PATs through **My ADO connection** in Admin Web.
Both methods can be connected together, and must identify the same ADO account for a given
organization and directory. OAuth is preferred within the most specific matching directory
connection, followed by that connection's PAT, then the bot. Use an organization-scoped PAT
with Code: Read & write and, for issue-driven linking, Work Items: Read & write; replace it
before expiration.
The existing directory `azure_devops_pr_pat_env` remains the bot fallback. Personal
connections affect PR publication; existing host Git push and issue-intake credentials
continue to serve those operations. The Work Items permission lets issue-driven PR publication
associate the PR with its source work item through the same personal credential.

In the PR create workflow node, **PR creator policy** defaults to **Allow bot fallback**
(`publication_policy: allow_bot_fallback`). Select **Require personal creator**
(`require_personal`) for releases that must be published as their captured ADO assignee.
This requires `publication_context_id`, one known assignee across the release contributions,
and an available personal connection. Missing context, mixed or missing assignees, or rejected
personal credentials stop publication. Same-account OAuth-to-PAT fallback remains available.
The host checks credentials and any existing PR's creator before pushing; it rechecks at PR
creation to account for concurrent changes. A later failure can leave the branch published,
but does not authorize a bot-created PR. An existing PR owned by another identity is left
for operator recovery; changing this policy does not change its creator.

With bot fallback allowed, audit records explain missing publication context
(`publication_context_missing`), credential fallback, uncertain-creation reconciliation,
and reuse of a PR whose creator differs from the intended assignee. Requiring a personal
creator keeps reconciliation on that person's connection and retains the duplicate-POST guard.

The host stores encrypted PATs and publication audit records in
`runtime/ado-user-connections/connections.db`, with a separate encryption key in
`runtime/secrets/ado-user-connections.key`. Both files are restricted to the service user.
Back up the database and key separately, retain ownership when restoring, and never
copy them into guest bundles. Removing a connection stops future credential selection;
an ADO request already in flight may finish. Removal does not revoke the PAT or app consent in ADO/Entra.
Deleting the platform user also removes their stored credentials and pending connections.

### Enabling Microsoft OAuth through CI/CD

Use a confidential Microsoft Entra **Web** app. The existing Microsoft SSO app can be reused;
connecting ADO is a separate consent operation and does not change normal sign-in scopes.
Register `https://<admin-host>/api/admin/me/ado-connections/oauth/callback` as an additional
Web redirect URI. Add Azure DevOps delegated permissions `vso.code_write`, `vso.profile`, and
`vso.work_write`, and grant consent according to the tenant's policy. The flow requests those
permissions and `offline_access`, uses PKCE, and verifies the ADO account through `connectionData`.
See [Microsoft's Entra ADO guidance](https://learn.microsoft.com/en-us/azure/devops/integrate/get-started/authentication/entra-oauth?view=azure-devops).

Configure these host environment variables through the operator-owned CI/CD deployment:

- `EVANDER_ADO_OAUTH_ENABLED=true`
- `EVANDER_ADO_OAUTH_TENANT_ID`: a concrete tenant ID or domain.
- `EVANDER_ADO_OAUTH_CLIENT_ID` and `EVANDER_ADO_OAUTH_CLIENT_SECRET`.
- `EVANDER_ADO_OAUTH_REDIRECT_URI`: the exact callback registered above.

Tenant/client/secret default to the corresponding `EVANDER_ADMIN_WEB_AZURE_*` variables.
The redirect defaults to `EVANDER_ADMIN_WEB_PUBLIC_BASE_URL` plus the callback path.
All settings, including the secret, must be available to **Admin Web and every host process
that creates PRs**, particularly the effect orchestrator and gateway. Supplying them only
in `admin-web.env` will enable connection setup but prevent background token refresh.
Use the shared service secret environment managed by CI/CD; never copy these values to workers
or agent guests. No remote checkout mutation or manual service rollout is needed.

OAuth stays disabled until configured; PATs continue to work. Once enabled, a registered user
(Microsoft SSO or local-password sign-in) selects an organization and chooses **Connect with
Microsoft**. Access and refresh tokens are encrypted with the same host key as PATs. Refresh is
serialized across host processes, rotated tokens are persisted, and revoked grants show
**Reconnect required**. **Disconnect Microsoft** and **Remove PAT** operate independently.
After changing the OAuth tenant/client/redirect, reconnect existing accounts. Secret rotation
within the same app only requires updating the shared deployment secret.
Actual tenant consent and a human-owned PR require verification after CI/CD deployment.

Publication results record the requested and actual creator, credential source, and
fallback reason. A timeout or uncertain POST leaves a durable guard in `pr_attempts`.
Subsequent publication attempts reconcile by querying ADO and reuse a discovered PR.
If ADO continues to report no PR, publication remains pending for operator investigation;
do not blindly clear the guard or recreate the PR. Verify the source/target branches
and provider result before an operator resolves an unconfirmed attempt.


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
`frontend/admin-web/branding`. Use the Admin Web branding variables in that env file when a
deployment needs a different browser title, shell header title, or logo set; relative logo paths
can be served from `EVANDER_ADMIN_WEB_BRANDING_ASSET_DIR` through the proxied
`/api/admin/branding/assets/...` route.

## Bootstrap Flow

1. Install Python dependencies with `uv`.
2. Run `evander-cli init-state --runtime-dir runtime`.
3. Edit host-global bootstrap settings in `runtime/host-config.json`.
4. Configure `root` directory overlays directly with the admin UI or `evander-cli directories config-set`.
   Configure named agent integrations on the **Connectors** page or in the `connectors` directory
   domain; use the [Credentials page and managed store](credentials.md) for host authentication.
   Environment-based profiles remain an explicit compatibility option.
5. Create the libvirt network with `./scripts/create_libvirt_network.sh`.
6. Apply guest forwarding policy with `./scripts/apply_firewall.sh`.
7. Download the Ubuntu cloud image with `sudo ./scripts/create_base_vm.sh`.
8. Create a session manifest with `evander-cli create-session`.
9. Launch a VM with `sudo ./scripts/launch_agent_vm.sh`.
10. Optionally bootstrap a provider runtime in the guest (for example `./scripts/bootstrap_codex_vm.sh <vm-name>` or `./scripts/bootstrap_copilot_vm.sh <vm-name>`).
11. Optionally bootstrap a Python repo into a guest-local virtualenv with `./scripts/bootstrap_python_vm.sh <vm-name> <host-checkout-root> <guest-repo-root>`.
12. Optionally bootstrap generic guest Python packages into an offline tools virtualenv with `./scripts/bootstrap_python_packages_vm.sh <vm-name> <package-spec> [package-spec ...]`.
13. Optionally install the host `.NET` SDK into the guest with `./scripts/bootstrap_dotnet_vm.sh <vm-name>`.
14. Optionally prewarm the shared host NuGet cache for libvirt VMs with `./scripts/prewarm_nuget_cache.sh <path> [more paths...]`.
15. Optionally install NuGet relay sources into the guest with `./scripts/bootstrap_nuget_vm.sh <vm-name>`.
16. Optionally provision a guest-local Microsoft SQL Server instance into the guest with `MSSQL_SA_PASSWORD=... ./scripts/bootstrap_mssql_vm.sh <vm-name>`.
17. Optionally install Playwright into the guest with `./scripts/bootstrap_playwright_vm.sh <vm-name>` for rendered-DOM debugging.
18. Register a managed Git repository with `evander-cli git-repos register --directory-path <directory> --repo-source <source>` when a VM or container should clone or push code.
19. Use [live-smoke-tests.md](live-smoke-tests.md) when you want to verify live Discord and provider-backed email behavior against real credentials.
20. Use [libvirt-vm-template-operations.md](libvirt-vm-template-operations.md) when you need to prepare or register a non-default libvirt base image.

- The launch script resizes each overlay disk to `20G` by default.
- The launch script also exposes a read-only shared NuGet cache via `virtiofs` at `/opt/evander/nuget-cache-ro` inside the guest.
- Override per VM with `DISK_SIZE_GB=40 sudo ./scripts/launch_agent_vm.sh ...`.

## First Troubleshooting Step

For the opt-in core classification capability, host credential setup, provider enablement,
and an explicit live smoke command, see [Classification](classification.md). It uses the
existing gateway, effect orchestrator, and Admin Web processes with no new guest ports.
OpenAI Decisions uses that same configuration surface with host-owned `OPENAI_API_KEY`,
model `gpt-6-luna`, and explicit provider enablement. See
[OpenAI Decisions](classification.md#openai-decisions) for setup and an explicit provider smoke.

Before debugging individual scripts or services, run the consolidated host status command:

- Human-readable summary:
  - `evander-cli status --runtime-dir runtime`
- Machine-readable output:
  - `evander-cli status --runtime-dir runtime --json`

The status output reports config load status, runtime store path permissions, key systemd unit states,
gateway/relay endpoint reachability, and active/expired session summary.

## Session Manifest Operations

`evander-cli create-session` creates a new identity and fails if that session ID already exists; it
does not overwrite or silently recycle an earlier manifest. Use the supported renew, revoke,
policy-edit, or delete operation for an existing session.

Deleting a session removes more than the manifest. The service revokes all of that session's
managed-Git grants and bindings, prunes its legacy `runtime/pr-map.json` entries, and removes the
matching feedback cursors and items from `runtime/pr-feedback-store.json`. This prevents a newly
created session with the same ID from inheriting historical repository or pull-request routing.
Deletion does not remove provider-side pull requests or comments.

## Connector Operations

Use Admin Web **Connectors** to inspect the effective directory configuration, verify whether each
host auth profile has all required environment secrets, save revision-checked overrides, and run a
configured health operation. CLI diagnosis is available through `evander-cli connectors list`,
`show`, `validate`, and `test`; pass `--directory` to inspect a child scope.

After changing an environment secret, restart `evander-gateway` and `evander-admin-web` so new
processes read it. Connector JSON stores only environment variable names. If a call is unavailable,
check in order: effective connector validation, auth availability, the session's
`list_connectors`/`call_connector` tool visibility, its exact `connector_grants`, pending approval,
and the connector-specific tool-call audit entry. Artifact results remain host-side until a
separately allowed communication-artifact copy operation imports them into the session workspace.

See [connectors](connectors.md) for schemas, examples, authentication behavior, and custom drivers.

## Host Directory Operations

Use Admin Web **Host Directories** to define a resource ID, absolute non-root host path, allowed
capabilities, import/watch limits, delete-approval policy, and enabled state for the selected
Evander directory. Unlock privileged operations to save or probe a host path. Run **Check health**
before granting a resource to a session or selecting it in a workflow. For a delete-enabled
resource this also creates and removes a private quarantine probe, so a green result verifies
quarantine writability rather than only opening the root.

Grant only the required `list`, `import`, and `delete` capabilities through the structured
Host Directory selector in Agent creation, Agent policy, or Sessions policy. Agents see only the
resource ID and relative entries. An import returns a session-owned artifact; it does not mount the
root or bypass the separately configured workspace-copy policy.

Delete is revision-checked and approval-gated by default. It moves the entry to the resource-local
`.evander-trash` quarantine. In **Quarantine recovery**, restore a record only when its original
relative destination is free. Permanent purge is irreversible and has no scheduled retention in
v1. Incomplete records remain visible: a missing payload or corrupt metadata disables restore but
still allows explicit cleanup by purge. If health fails, repair the real directory and permissions;
do not use a symlink, configure `/`, or hand-edit quarantine metadata.

For automation, add `trigger:host-directory:file-added` in the Workflow editor. It baselines
existing files and emits only settled new regular files through the normal trigger-orchestrator
poll loop. Diagnose it by checking the effective resource, workflow directory scope, include/
exclude globs, the resource entry limit, trigger worker logs, and
`runtime/trigger-cursors-host-directories.json`.

See [Host Directories](host-directories.md) for the complete schema, tool contracts, safety
boundary, and recovery procedure.

## Workflow Runtime Files and Troubleshooting

The workflow orchestrators share runtime state under `runtime/` (or your configured
`runtime_dir`):

- `orchestration.db`: authoritative trigger queue, internal events, event-trigger subscriptions and
  delivery state, scheduled internal events, workflow-event journal, gate/effect execution history,
  compact node-execution query history, and workflow configuration snapshots. JSON orchestration
  and internal-event stores are no longer supported. Completed full records default to 30 days;
  sanitized compact inputs/outputs, typed scalar indexes, and source provenance default to 365
  days. Existing retained rows are copied in bounded idle-worker batches and the originals expire
  normally. Large operational payloads have lossless compressed references separate from sanitized
  history. Background maintenance updates searchable projections, expires rows and collects orphan
  payloads in bounded batches. Retention reuses freed SQLite pages and does not run `VACUUM`
  automatically.
- `trigger-orchestrator-cursors.json`: trigger-worker cursors/checkpoints.
- `trigger-cursors-host-directories.json`: per-trigger Host Directory baseline, settle candidate,
  pending-event, and acknowledgement state.
- `gate-orchestrator-cursors.json`: gate-worker cursor/checkpoint data.
- `effect-orchestrator-cursors.json`: effect-worker cursor/checkpoint data.
- `model-usage.db`: durable provider-neutral model usage store queried by
  `query:model-usage:list`; it records only usage/provider/correlation metadata
  and does not persist prompt or completion text.
- `mcp-transport-bindings.db`: MCP connection bindings, last advertised tool snapshots and
  binding-conflict diagnostics. Individual updates affect only the current connection record.
- `operator-steering-notes.db`: host-side operator steering notes keyed by
  session and prompt dispatch for legacy relay-delivered steering. Container
  worker steering interrupts the active worker execution and resumes it as a new
  worker job instead of waiting for the next model request.
- `model-pricing.openai.json`: operator-owned pricing catalog seeded under the
  configured `runtime_dir` by `evander-cli init-state` and used by
  `query:model-usage:list` for query-time cost estimates. Override with
  `EVANDER_MODEL_PRICING_PATH` when operating from a different runtime rate card.
  Admin Web exposes the loaded catalog status and structured editor on the
  Configuration page and saves changes back to the active catalog path.
- `trigger-orchestrator.lock` / `gate-orchestrator.lock`: single-instance lock files.
- `trigger-conformance-report.json` and `effect-conformance-report.json`: conformance snapshots
  written by the trigger and effect suites under the configured `runtime_dir`.

### Orchestration storage migration and rollback

Inspect the exact configured database without applying migrations:

```bash
evander-cli migrate-orchestration-storage --database runtime/orchestration.db --check
evander-cli migrate-orchestration-storage --database runtime/orchestration.db --check --verify
```

`--verify` reads every operational payload and reports missing or invalid references. It can take
longer than a readiness check on a large database. Read-only checks do not initialize schemas,
convert rows or build indexes.

The new reader accepts both legacy inline records and compressed references. Large payload subtrees in new records use lossless
operational payload storage at a 16 KiB threshold; sanitized history remains separate. Startup does
not scan and rewrite all old rows. To convert retained records and build deferred maintenance
indexes, stop **every process using this database**, including the trigger/effect workers, gateway,
Admin Web and any CLI or other workers that publish events. Take a verified backup with SQLite's
backup API or a consistent copy of the stopped database and its WAL files. Then run:

```bash
evander-cli migrate-orchestration-storage --database runtime/orchestration.db --apply --offline --max-batches 20
```

`--offline` confirms that the database users have already been stopped; the command does not stop
services. Repeat the same command until its JSON result has `complete: true`, then run the read-only
`--check --verify` command before restarting. The migration commits bounded batches and records its
cursor, so an interrupted invocation resumes. Existing maintenance index construction runs before
the conversion batches and is not limited by `--max-batches`. Use a representative database copy
to measure this step before scheduling the outage.

To build only deferred maintenance indexes, use:

```bash
evander-cli migrate-orchestration-storage --database runtime/orchestration.db --indexes-only --apply --offline
```

Stop every database user and take a verified consistent backup first, as above. This mode builds
and validates indexes without rewriting operational payloads or retention progress. Its JSON
result reports `direction: "indexes_only"`, zero processed rows, and `complete: true` when all
required indexes are ready. It cannot be combined with `--downgrade` or `--max-batches`; completed
indexes survive an interrupted build and rerunning skips them. Rehearse the build on a representative
copy to measure the outage, then checkpoint the WAL and check readiness before restarting all
database users on the same release. A mismatched existing index definition fails validation rather
than replacing it automatically.

Source retention uses five partial expression indexes for delivered events, completed effects and
gates, and acknowledged failed effects and gates. Each range yields at most the effective record
batch limit; the merged batch selects the oldest eligibility timestamps, breaking ties by source
ID and execution kind. Payload byte accounting looks up only each source's reachable blobs. The
existing payload byte cap, history indexing requirement, and transactional
eligibility recheck still apply. Fresh databases create these indexes during initialization; existing
databases defer source cleanup with `maintenance_indexes_required` until the offline build succeeds.
Adding these indexes does not require payload conversion, a history rebuild, or `VACUUM`.

Workflow-event, internal-event and published scheduled-event retention uses the existing age
indexes in timestamp/key order. History field-index batches durably shrink after execution
timeouts and recover after fast full batches; incomplete generations remain hidden from field
search. Execution deadlines do not suppress independent maintenance phases and preserve the
one-second retry pause even at the one-field limit. Writer lock contention still yields the pass
to foreground work. These changes use existing tables and indexes and can be deployed without
another offline storage migration.

Execution-history payload cleanup scans at most 1,000 blob keys per retention pass,
independently of the adaptive deletion limit. It saves the cursor and a fixed upper
key in the existing retention progress metadata. Reference checks, deletion and
cursor updates share the writer transaction, so a timeout rolls them all back and
a restarted worker resumes the committed position. Ranges with no orphans advance
the cursor instead of rechecking the entire retained prefix. Newly unreferenced
blobs behind the cursor or beyond the upper key wait for the next daily retention
sweep. No additional tables, indexes or offline migration are needed.

Execution-history scalar indexes can optionally store repeated execution keys,
generation hashes and field paths in shared dictionaries. New and existing databases
continue to use text-keyed indexes until explicitly converted. Application indexing,
field queries, retention and garbage collection support both formats; the original
scalar table names remain readable views in the compact format. Those views are for
inspection, and applications must use the repository write APIs.

History reports reuse up to 16 idle read-only connections per process, partitioned
by database file identity and thread. Reports release every cursor and transaction
before idling; each subsequent report opens a fresh snapshot with its own deadline.
Database replacement, runtime shutdown and failed operations discard affected
connections. Writers and background indexing/retention connections remain transient.
This optimization supports both scalar formats and requires no database migration.

Rehearse conversion on a consistent copy before planning a production cutover:

```bash
evander-cli convert-history-index --database /backup/orchestration.db --output /backup/orchestration-compact.db --offline
```

This command opens the source read-only and uses SQLite's backup API, converts a
private temporary copy, then publishes a new file only after full integrity and
foreign-key checks pass. It refuses an existing output, preserves scalar row IDs,
and compacts the new format with `VACUUM INTO`. Reserve space beside the output for
the source-sized backup, replacement indexes and compact output together. Only
verified output is published; rerun from the source to restart an interrupted
conversion. `--offline` confirms
that no process will use the output during conversion; the command does not stop
services or switch any database paths. The new database is a snapshot: subsequent
source writes are not copied. A cutover therefore requires stopping every source
writer before taking the final copy and keeping them stopped until the verified
output is installed using the normal backup/restore procedure.

The command reports each conversion/check phase on stderr and returns phase timings
in its JSON result. SQLite's cache defaults to 1 GiB; use `--cache-mib` to size it for
the maintenance host (for example, `--cache-mib 4096` on a workstation with spare
memory). Bulk index sorts can use additional memory beyond this cache. Foreign-key
enforcement is deferred only within the unpublished temporary copy; full integrity
and foreign-key checks still have to pass before the output becomes available.

To prepare rollback, use the new executable to produce another separate output:

```bash
evander-cli convert-history-index --database /backup/orchestration-compact.db --output /backup/orchestration-legacy.db --offline --legacy
```

Legacy conversion preserves implicit scalar row IDs and deliberately skips vacuuming.
Use that output for any further operational-payload downgrade below. Never start an
older binary on a database with the compact scalar format; the read-only storage
check reports `history_index_format` and includes this restriction in `downgrade_ready`.

For rollback to a release that only understands inline operational records, keep all database users
stopped and run the **new release's CLI** before starting the old executable:

```bash
evander-cli migrate-orchestration-storage --database runtime/orchestration.db --apply --offline --downgrade --max-batches 20
evander-cli migrate-orchestration-storage --database runtime/orchestration.db --check --verify
```

Repeat the downgrade command until `complete: true`; verification must report no errors and
`downgrade_ready: true`. Downgrade rehydrates the original lossless values, completes pending indexing, restores the legacy
field-search index (including interrupted retention), and clears projections that an older
executable cannot maintain. Additional
tables and indexes may remain. Backups and migration do not physically shrink the
database; plan any space reclamation as separate offline SQLite maintenance.

Team activity retention removes at most 100 expired records per append. Team SQLite schema 80
prepares its `(created_at, id)` retention index automatically only when the activity table has
at most 10,000 rows. Larger tables retain their rows until the index is prepared offline; their
normal append path skips pruning. Include the Team database in the maintenance check and apply:

```bash
evander-cli migrate-orchestration-storage --database runtime/orchestration.db --team-database runtime/addons/team-addon/state.db --check
evander-cli migrate-orchestration-storage --database runtime/orchestration.db --team-database runtime/addons/team-addon/state.db --apply --offline --max-batches 20
```

Stop all users of both databases before applying. The Team readiness result reports whether
its retention index exists; the offline index build is not limited by `--max-batches`.

Team dispatch finalization now returns its outcome receipt, including revision, dispatch IDs,
activity counts and event IDs. It no longer copies the agent's historical state into every
successor. Before deployment, check operator workflow bindings for use of the previously
undeclared finalizer `state` output; retrieve details through Team read queries/APIs instead.

### MCP connection-history SQLite cutover

On first startup, the connection repository imports `mcp-transport-bindings.json` into
`mcp-transport-bindings.db`. Import and its completion marker commit together. Invalid input
fails startup without committing a partial import. Subsequent startups use SQLite without
reading or rewriting the legacy JSON file. The original JSON snapshot is retained.

Coordinate the first upgrade across all control-plane processes using this runtime: stop the
gateway, Admin Web, orchestrators and other old runtime consumers before starting the upgraded
code. Do not mix old JSON writers and new SQLite writers. Include the new database in runtime
backups; use SQLite's backup API or stop its writers before copying the database and any WAL.

The retained JSON file is a pre-upgrade snapshot, not a live rollback copy. For a rollback that
preserves newer connection history, stop all runtime consumers and export the current bindings
before installing the older code. Run from the deployment directory with its virtual environment:

```python
import json
from pathlib import Path
import sqlite3
from evander.storage import write_json_atomically

runtime = Path("runtime")  # Use the configured runtime directory.
connection = sqlite3.connect(
    (runtime / "mcp-transport-bindings.db").resolve().as_uri() + "?mode=ro", uri=True
)
try:
    bindings = {
        key: json.loads(payload)
        for key, payload in connection.execute(
            "SELECT transport_session_id, payload_json FROM mcp_transport_bindings"
        )
    }
finally:
    connection.close()
write_json_atomically(runtime / "mcp-transport-bindings.json", bindings, fsync_directory=True)
```

Archive the SQLite database and any accompanying `-wal`/`-shm` files outside the active runtime
before restarting the older code. A later upgrade must import the then-current JSON file into
a fresh database, rather than reuse the previous import marker.

### Internal-event SQLite cutover

This release requires a maintenance-window cutover on deployments that still contain
`internal-events.json`, `scheduled-internal-events.json`, or the event/webhook/artifact trigger
cursor JSON files. Do not start the new trigger, effect, Admin Web, or gateway processes against
those files before applying the migration.

For each deployment, stop the services that can publish or consume internal events, then run:

```bash
evander-cli migrate-internal-events-to-sqlite --runtime-dir runtime --check
evander-cli migrate-internal-events-to-sqlite --runtime-dir runtime --apply
```

The apply step acquires the trigger and effect locks, imports legacy events and scheduled records,
preserves acknowledged delivery state for currently configured triggers, and leaves other matching
legacy events pending. It copies source JSON into
`runtime/archives/internal-event-sqlite-v1/<timestamp>/`, writes a migration marker under
`runtime/migrations/`, removes the active JSON files, and removes the retired
`internal_event_store_path` and `scheduled_internal_event_store_path` keys from the trigger
orchestrator config after archiving its original contents. It is restartable after an interrupted
archive/finalization step and idempotent after completion.

After applying, start the services and verify the marker reports `state: complete`, the legacy
files are absent, and the trigger worker can consume a newly published test event. A completed
deployment fails closed if a legacy file reappears.

Gate execution state is useful when debugging workflow gates:

- `succeeded`: the gate emitted a downstream event.
- `dropped`: a filter gate intentionally suppressed the event.
- `failed`: the gate hit a validation, binding, or merge projection error.
- `skipped`: the gate was bypassed because of a validation or cycle guard.

Troubleshooting checklist:

1. **No downstream nodes running**
   - Confirm `triggers`, `gates`, and `effects` exist, and confirm there is at least one edge from
     the upstream node that should start the flow.
   - Confirm pending source events exist in `orchestration.db` table `trigger_events`.
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
   - Inspect `orchestration.db` table `gate_executions` for the recorded `resolved_config`, the latest
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
   - Check idempotency collisions in `orchestration.db` table `gate_executions` using
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
   sudo systemctl stop evander-scheduled-wake.service
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

6. Restart the Team control-plane services as one loaded-code generation, then start the
   configured ingress/wake services:

   ```bash
   sudo systemctl restart evander-team-runtime.target evander-gateway.service evander-admin-web.service evander-trigger-orchestrator.service evander-effect-orchestrator.service
   sudo systemctl start evander-discord-ingest.service
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

   Require `runtime_generation.state` to be `coherent` and
   `stale_components` to be empty. A Team wake running under systemd fails before
   leasing work or creating a checkout while the registered services are
   missing, split, or older than the current checkout.

   Confirm the migration grep from the implementation plan shows only expected
   migration-history, deleted-code, or removal-test references. In Admin Web,
   confirm the Team Add-on loads, a normal persistent Team wake sees ordinary
   Team MCP action tools, Team Chat wakes can call
   `team_reply_to_chat`, governance wakes only see governance tools, and
   ephemeral workers only report through `team_report_ephemeral_result`.

   The Team no-progress limit also bounds inbox and review replays. Once an
   agent reaches the configured limit, an unchanged pending work snapshot must
   not lease again. Newly added or updated inbox/review work receives one
   bounded attempt; removing or partially handling an item does not make the
   remaining unchanged work new. Pre-delivery preparation failures do not
   consume that attempt, and context rotation plus routed recovery work remain
   independently wakeable. Manager fallback uses a separate, uncapped trigger
   snapshot of team-wide work while its prompt remains bounded; manager-owned
   tasks, inbox, reviews, and action feedback stay in the ordinary wake domain.

### External Team Chat bridge smoke check

For a configured disposable communication target, send one allowlisted human
message and confirm it produces exactly one Team operator message with
`external_origin` metadata, one queued `team-chat:<message-id>` dispatch, and a
normal Team Chat reply. Confirm that the reply produces one
`team.chat.external_reply_requested` outbox event and appears in the source
channel. Replay the same provider message ID: it must return the original Team
message/dispatch rather than queue another turn. Verify an unauthorized author
and a bot-authored reply create no Team message.

If external delivery fails, retain the Team reply and inspect its recorded
delivery state before retrying the communication workflow. Do not send a
generic `effect:agent:enqueue-prompt` for bridge traffic: it bypasses the Team
dispatch lease and cannot safely call `team_reply_to_chat` or Team mutations.

For attachment coverage, stage one allowed Team Chat attachment and send an
external reply with an empty text body. Confirm the external-reply event
contains its attachment ID and sanitized metadata, preserves an empty
`team_chat_body`, and supplies a non-empty provider body listing the filename,
MIME type, and byte count. The generic bridge sends that textual reference; a
provider-native file upload requires a separately supported communication
capability.

Troubleshooting:

- **Active lease mismatch** — if a completed session's dispatch does not match
  the active Team lease, do not force it through Team finalization. Inspect the
  active lease row, the trigger event that reported completion, and the stable
  Team session identity/guidance metadata if it is present. Clear or repair
  only the stale lease after preserving any useful operator context.
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
- **Committed work after a task specification edit** — preparation preserves
  compatible retained commits in a fresh checkout and records `retained_work`
  on its Git context and server-owned preparation attempt. This provenance
  identifies resumed, unreviewed work; the immutable base, author history and
  acceptance/release gates remain in force. A retry uses the selected commit
  even if the old branch subsequently advances. Returning to an earlier
  specification allocates a new activation and receipt, preserving its newer
  compatible commits; ordinary resumes and interrupted retries reuse that
  activation. Final wake prompts include only its source context, commit, tree
  and original root; full provenance remains on the durable context. Both
  existing worker backends
  use their normal preparation and session execution paths; no new worker API
  or agent tool is required. Dirty work, divergent retained branches, missing
  provenance, changed owners/repositories/directories or incompatible dependency
  pins stop preparation with a concrete recovery incident. Preserve the old
  checkout and use the existing Git recovery path to resolve that prerequisite.
- **Evidence trapped behind a task blocker** — the owner can use the existing
  `team_submit_deliverable` with `evidence_only=true`, retained `artifact_paths`,
  and a failed or incomplete `verification.status`. A matching task-specific
  request in the current dispatch authorizes this evidence operation even when
  normal task scope excludes the blocked task. Inspect the returned
  `published_evidence_artifacts` receipt; keep substantive blockers and normal
  completion, acceptance, and release gates. Reuse the exact request and payload
  after interruption or context rotation to recover the existing receipt.

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
here. Keep the active `runtime/model-pricing.openai.json` catalog current through
Admin Web when provider pricing changes. The tracked
`config/model-pricing.openai.json.example` file is only the initial seed for new
runtime directories and is never the active operator catalog. Built-in tool
fees, non-text token modalities, regional uplift, and account-specific discounts
are not inferred unless the needed quantities are recorded in the usage metadata.
The legacy tracked `config/model-pricing.openai.json` file remains only for
upgrade compatibility and is no longer read or edited by default. On the first
`init-state` run for a runtime without an active catalog, a customized legacy
catalog is migrated instead of the example seed; existing runtime catalogs are
never overwritten.

Admin Web Configuration includes a model pricing catalog panel that shows the
active path, path source, version, currency, source URLs, rule count, rules, and
any load/validation error. Operators can edit catalog metadata, source URLs, and
pricing rules in the structured panel after unlocking privileged operations.
Saves include the catalog revision and fail on stale revisions, so concurrent
operator edits require a reload before retrying. The backend validates the
catalog before writing and writes atomically to either
`EVANDER_MODEL_PRICING_PATH` or the configured runtime directory's default
`model-pricing.openai.json` path.

When upgrading to Ultrafast, add a tier-specific rule to each existing active
catalog through that panel; updating the example seed or running `init-state`
does not update an existing operator catalog. The seed includes GPT-6 Astra with
`service_tier: "ultrafast"`: per 1M tokens, input $60, cached input $6, cache
writes $75, and output $300. For requests exceeding 272,000 input tokens, those
rates become $120, $12, $150, and $450 respectively. Preserve other rules, add the
[published Ultrafast pricing source](https://developers.openai.com/api/docs/pricing?latest-pricing=ultrafast),
and update the catalog version when saving. These Astra rules have no date limit
so recorded usage is repriced at query time. Add Ultrafast rules for other models
only when their rates are published; an untiered rule falls back to Standard
pricing and cannot represent an Ultrafast estimate accurately.

The store intentionally excludes request and response text, so it is safe to
inspect for operational attribution without exposing prompt content.

### Inference Cost Reports

The dashboard shows accumulated API inference costs for the current month. Use
**Full cost report** to open **Reports > Inference costs**, which also includes
daily bars stacked by category and a table grouped by provider, configured API
key name, and category. The table includes account subtotals and a grand total.
Choose **Compare provider / API key** to select several accounts on the line
chart, or **Total** for one accumulated total. The dashboard link retains that
selection and the current directory scope.

On the full report, **Category chart > Tokens** switches the daily stacked bars
to total token usage from recorded API and subscription inference. The default
is **Cost**. Only that chart switches: the monetary headline, accumulated charts,
table, and dashboard continue to show API costs. The report URL retains the
selected category metric. Category colors match across both modes, and tooltips
and keyboard focus show exact token counts.

Tokens do not require a price or API key. A recorded total is used when available;
otherwise both input and output counts are required and summed. Cached input
and reasoning breakdowns are not added again to an existing total. Missing
counts are excluded with a separate partial-coverage notice; recorded zero
counts remain valid. Local inference and unknown billing sources are excluded.
Subscription coverage includes newly recorded worker usage. Historical imports
are manual operator actions; opening the report or restarting a service does
not import old subscription histories.

Reports default to month to date in the viewer's browser timezone. A custom
period includes both the **From** and **Through** dates. Day boundaries follow
that timezone, including daylight-saving changes; days without costs remain in
the charts. Report access requires `workflow_runs.read` for the selected
directory, and results include its descendants.

Set **Cost category** when creating an agent or editing its session policy.
Labels are free text with suggestions from existing categories in the directory.
The `effect:agent:create` node also accepts `cost_category`. Both
`effect:agent:prompt` and `effect:agent:enqueue-prompt` can override the session
category: **Inherit session category** uses the session default, while an empty
override explicitly selects **Uncategorized**. Workflow bindings and templates
are supported. The chosen category is frozen when the prompt is first
submitted and retained through queueing, deferral, and context retries. Session
label changes apply to future prompts; they do not reclassify historical costs.

For an explicit historical correction, operators can use
`build_admin_runtime(runtime_dir).model_usage_admin.set_inference_cost_categories`.
Supply event IDs mapped to labels, `expected_categories` for those same IDs, and
timezone-aware `occurred_after` / `occurred_before` bounds (end exclusive). Each
batch is limited to 1,000 events and fails atomically if an event is missing,
outside the period, or its category has changed. Preserve the previous labels
before applying a correction so it can be reversed through the same service.
Only categories change; usage, billing provenance, and monetary totals remain
intact, and cost reports refresh automatically.

Amounts are in USD, using recorded provider charges when available and the
configured pricing catalog otherwise. Subscription and local inference are
excluded. Missing prices and unknown billing provenance make the total partial;
unpriced groups show **Unavailable**. Old records without a category or API key
name appear as **Uncategorized** or **Unknown key**. API key groups use the
configured credential environment-variable name, never its secret value; a
rotation of that credential stays in the same group.

The report maintains a disposable `runtime/inference-cost-report.db` projection
of `model-usage.db`. It handles new events, corrections, deletions, and transcript
replacement incrementally, and rebuilds when the pricing catalog or source
generation changes. Aggregation and serialization run outside the web event
loop. Report reads do not write to the usage database. Keep `model-usage.db` in
backups; the report projection can be rebuilt from it and the pricing catalog.

The read-only API is `GET /api/admin/reports/inference-costs`, with optional
`directory_path`, `timezone` (an IANA name), `start`, and `end`. ISO timestamps
must carry an offset; date-only boundaries are interpreted in `timezone`.
`start` is inclusive and `end` is exclusive. Omitting both selects month to date.
`GET /api/admin/inference-cost-categories?directory_path=root` supplies scoped
label suggestions under the same report permission.

## Changing the Default Container Worker

Admin provider-configuration saves preserve existing agents by pinning their current worker
before changing the default. New agents use the new worker; existing agents and their resumed
jobs continue on the original worker with its saved connection settings. Deploy assignment-aware
code to all control-plane processes before cutover.

When managing configuration outside Admin, run `evander-cli execution pin-worker-assignments
--runtime-dir runtime` to preview, then repeat with `--apply` while the original configuration
is still active. Retain both workers' token environment variables and include
`container-worker-assignments.db` in runtime backups. See
[the complete worker cutover procedure](container-worker-backend.md#changing-the-default-worker).

For individual new agents, configure named `container_workers` entries in provider
configuration and choose **Worker** in Admin Web or Team. Workflow agent-creation
effects and the manual creation APIs accept `worker_id` to override the directory
default. The assignment remains fixed for that session. See
[worker selection](container-worker-backend.md#selecting-a-worker-when-creating-an-agent).

## Switching Container Codex Usage Source

Container-worker Codex sessions can use either `metered_api` or
`chatgpt_subscription`. `container_worker_codex_auth_mode` remains an inherited compatibility
default in directory provider configuration, but directory configuration never selects, grants,
or restricts subscription profiles. The selected worker supplies the profile catalog;
creation uses the directory's `container_worker_base_url` when no worker override is provided.

New subscription agents created in Admin Web must select an enabled named profile. The selection
is persisted on the session and can later be changed to another enabled profile or back to metered
API usage without replacing the session. Running turns retain their launch-time route; later turns
and deferred prompts use the updated selection. Existing profile-less subscription sessions
resolve to the reserved `default` profile, whose credentials remain at the legacy worker auth
home.

The subscription selection is effective only for container Codex turns whose effective
`model_provider_mode` is `openai_public`. If `local_openai` or `azure_openai` is effective, the
turn uses that configured private/API-key route and the stored subscription preference is left
unchanged. Returning the session to `openai_public` makes the preference effective again.
Non-Codex agents do not inherit the Codex default; an explicit `chatgpt_subscription` override on
one is invalid.

Subscription mode on the public route requires an enabled, signed-in profile. Open Admin Web
**Subscriptions** to create or rename profiles, enable or disable them, perform device login,
inspect the pinned account ID and safe plan/rate-limit/usage state, or log out. Creating or changing
a profile and starting or ending login require unlocked privileged operations and `admin.owner` at
`root`; a child-directory owner is insufficient because profiles belong to the effective worker,
not to a directory. Profile IDs are immutable lowercase slugs, display names are editable, and
the first successful login permanently pins the OpenAI account ID. Use a new profile for a
different account. The profile list shows the percentage remaining in the worker-reported Codex
seven-day rate-limit window; it shows "Unavailable" when no seven-day window is reported.
It also shows banked resets and the provider-reported credit balance. Credits show "Unlimited"
when reported, "0" when the provider explicitly reports no credits, and "Unavailable" when a
balance is missing or invalid. These credits are separate from earned rate-limit resets.

Use **Delete** to remove an unused profile and its worker-held login. Deletion requires the same
unlocked root-owner permission, checks that the worker selection is unchanged, and refuses active
jobs, queued prompts, or unexpired session bindings across directories on that worker. Reassign or
remove bound sessions and drain or cancel pending work first. Deletion retains recorded usage
history and does not cancel the provider's paid subscription. Empty workers no longer create a
Default profile; existing legacy singleton logins are migrated only before the registry exists,
so a deleted profile stays deleted after restart.

The Admin API is `DELETE /api/admin/codex-subscriptions/{profile_id}?directory_path=root`
with JSON body `{"expected_worker_url":"http://worker.example.test:11888"}`. The worker API is
`DELETE /v1/codex-auth/profiles/{profile_id}` and requires its bearer token. Both deletions are
idempotent. Upgrade the worker before enabling the Admin Web deletion action.
Deletion and the final validation/save of a subscription session share a per-profile lock across
control-plane processes. If deletion wins while worker preparation is running, creation fails
without saving a binding to the removed profile. Profile reassignment and expired-session renewal
use the same check; updates retaining an existing unexpired binding can still use a disabled profile.

Before a subscription job starts, the worker checks the selected profile. A signed-out, disabled,
unavailable, or exhausted profile returns a structured capacity deferral and the control plane
durably queues the original prompt. A rate limit reached during a turn follows the same replay
path. These deferrals do not consume the ordinary prompt retry ledger, have no automatic expiry,
preserve FIFO order within one profile, and do not block other profiles. The reported reset time is
used when present; otherwise reconciliation uses exponential backoff capped at 15 minutes. Login,
re-enable, and a profile refresh wake reconciliation, with normal orchestrator polling as fallback.
Operators can inspect the queue reason on workflow and agent views and cancel an indefinitely
queued prompt from the agent detail. Evander never falls back to metered API.

Version 1 supports ChatGPT device login only. Business and Enterprise access tokens remain future
work even though OpenAI supports them for trusted automation; see the
[OpenAI access-token documentation](https://learn.chatgpt.com/docs/enterprise/access-tokens).
The worker integration relies on the
[OpenAI App Server account APIs](https://learn.chatgpt.com/docs/app-server). Deployment and
per-profile auth-home ownership requirements are in
[`container-worker-backend.md`](container-worker-backend.md#codex-chatgpt-subscription-authentication).

For networked workers, populate `config/codex-egress-proxy.env`, deploy
`evander-codex-egress-proxy.service` on the control plane, and set the worker daemon's
`EVANDER_WORKER_CODEX_EGRESS_PROXY_URL` to its private URL. The deployment
must admit TCP `11889` only from worker addresses, while the worker `DOCKER-USER` policy admits
containers only to that endpoint. The proxy independently restricts client CIDRs and destination
hosts. A device-login HTTP 500 should be traced through the worker broker and this proxy path
before changing profile state.

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
  directory-scoped graph selection and labels. The pack loader rejects the retired plural
  `directory_paths` field; Admin Web and model output use only `directory_path`. `user_ids` are
  owner/audit metadata only; workflow graph visibility is controlled by Admin Web group assignments
  and direct workflow grants. Saving from a filtered view keeps unchanged graph metadata entries
  intact, so graphs outside the selected directory are not rewritten by the presentation filter.
- For workflows that run npm audit, use Admin Web or the workflow API to edit the audit
  `effect:agent:create` node and set `npm_registry_url` to the control-plane facade URL,
  `http://<control-plane-private-ip>:11881/`. Keep the other container-worker agent-create nodes
  on Nexus-backed registry defaults so only audit sessions opt into the facade.
- The Workflow editor keeps the canvas above a full-width workbench. Drag the
  horizontal divider, or focus it and use the arrow keys, to adjust the split.
  **Expand editor**, **Collapse editor**, and the toolbar's **Show editor** or
  **Show canvas** controls change the visible panes without rearranging nodes or
  resetting the canvas view. The split and node-detail tab are remembered locally.
  Open **Graphs** or **Add node** from the toolbar. The workbench includes
  **Inspector**, **Runs**, and **Checks**; node details use **Configuration**,
  **Outputs**, and **History** tabs. Narrow screens retain the touch canvas and
  full-width inspector sheet.
- For `gate:map`, add named output rows and choose an upstream binding, text,
  templated text, number, boolean, null, object, or array. Objects and arrays use
  JSON sub-editors. **Raw JSON** and **Structured form** switch views without
  changing stored value types or bindings. Resolve duplicate names and invalid
  values before saving; switching tabs retains unfinished row input.
- In the Workflow editor, node inspector edits are staged into the local workflow document as soon
  as fields change. They are still not persisted to disk until **Save workflow** succeeds. Dirty
  nodes show an **Unsaved changes** badge, selected dirty nodes can be reverted with **Reset
  changes**, and leaving the workflow view or switching directories prompts for confirmation while
  any workflow changes remain unsaved.
- To understand a graph, select it in the **Graphs** panel,
  then click **Explain graph** in its graph details. The built-in assistant opens and
  explains the graph's triggers, decisions, data flow, and actions using the current
  editor document, including unsaved node changes. Existing chat history and any
  message draft are retained, and sensitive configuration fields are redacted from
  the assistant context. This requires workflow read access and an enabled
  Admin assistant; it does not save or run the workflow.
- To run an event workflow manually, select its `trigger:event` node and click
  **Trigger manually** in the inspector. Save or discard any workflow changes first,
  and enable and save the trigger if it is disabled. Fill the generated event fields;
  unregistered events retain a JSON editor with a notice. The dialog displays the
  saved event type and supplies event metadata automatically. A configured correlation key is fixed; otherwise one may
  be entered. **Trigger node** queues only the selected node and its downstream flow.
  **Inspect run** opens the queued run when you have run-read access. This action
  requires `workflow_runs.control` in the workflow's directory. **Validate payload**
  checks the registered schema without starting a run; submission also validates on
  the server. **Reload event definition** refreshes a stale schema while retaining
  entered values. Manage schemas in **Configuration → Automation & Assignment → Events**.
  Use `gate:event:validate` when automatic workflows should validate event bodies;
  event publishing itself remains unchanged.
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
  SQLite read model when available and falls back to JSONL when needed. Free-text
  searches use a compact trigram index that is backfilled in short background batches
  after an existing audit database is upgraded; the compatible SQLite scan remains
  available until that backfill finishes. A required journal read that fails returns HTTP `503`
  (`Tool-call audit log is unavailable`); inspect path, permissions, encoding, and service logs
  before treating an empty or failed display as evidence of no activity. Failed index updates
  retain the appended JSONL entry and log a warning; `evander-cli rebuild-audit-index --config
  runtime` rebuilds the derived index after the underlying problem is resolved.
- an Approvals tab for reviewing queued approval requests with redacted context and
  selected-directory filtering
- a Host Directories page for revisioned resource configuration, host-only health checks, and
  quarantine restore/purge
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

Expensive capacity collection additionally shares a 30-second service snapshot across readers,
including detailed views. Worker health and telemetry probes run concurrently, with at most four
workers at a time, one connection attempt per probe, and a five-second HTTP timeout (or a shorter
configured timeout). Rolling workflow graph reports share a 15-second snapshot and scan activity
once for node totals and distinct run counts. Explicit report periods remain fresh reads. Dashboard
Refresh and cache-control bypasses refresh these service snapshots too; payload timestamps retain
the actual collection time/window rather than pretending a cached result was just collected.

Team cost reports maintain a disposable `runtime/team-cost-report.db` projection alongside the
authoritative model-usage store. Normal reads update only changed request groups, including late
transcripts, corrections, deletions, and attribution changes. The first build, pricing changes,
and budget-window changes still rebuild historical usage and can be slower. The UI coalesces
in-flight cost reads and spaces automatic refreshes by 15 seconds; an explicit budget reset forces
a new read. Team views use read-only collection projections and never persist partial state.

Slow-request journal entries distinguish response-header, first-body and total elapsed time.
For `text/event-stream` subscriptions, `duration_ms` is startup latency, not connection lifetime;
`phase=stream_start` is emitted when a slow first body arrives, and `phase=stream_end` identifies
failed/incomplete streams. Ordinary responses use `phase=response` and total duration. These are
wall-clock phases, not separate database/handler/serialization profiles. Failed responses retain
the actual sent status (possibly 200) and add `failed=True` when an exception escapes.

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
SQLite user table is empty. The SQLite runtime store is the source of truth. Users with
`groups.manage` can create and edit reusable permission groups; users with
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

Selecting a running agent in **Agents** shows its **Current run** model, speed,
and reasoning settings. These values come from the active execution, including
prompt overrides, rather than the session's current defaults. Multiple active
container runs are shown separately. Older runs without recorded settings show
**Not reported**. Speed describes the requested service tier.

Agent reasoning effort can be set per session in **Sessions → Settings** and
overridden for one run in **Agents → Prompt** or either agent prompt workflow
effect. Existing sessions retain Codex `high` or the Claude/Copilot harness default
until explicitly configured. Resumed turns use the current session setting; queued
prompts and retries retain the value captured at submission. See
[reasoning effort configuration](configuration-reference.md#agent-reasoning-effort)
for native levels, CLI/API examples, provider support, and worker upgrade order.

Codex Fast and Ultrafast modes are configured in **Sessions → Settings → Default
speed**, at agent creation, or for one run in **Agents → Prompt → Speed for this prompt**.
`fast_mode: false` explicitly restores standard speed even when the session
default is Fast or Ultrafast. Use `fast_mode: "ultrafast"` or `--speed ultrafast`
to select Ultrafast. Workflow prompt effects accept the same override. It requires
public OpenAI and a supported model; availability and allowance or credit usage
come from the configured account. Upgrade container workers before enabling the
new control-plane fields, and rerun the Codex bootstrap for existing VMs. See
[Fast mode configuration](configuration-reference.md#agent-fast-mode).

- `./scripts/bootstrap_codex_vm.sh <vm-name>` copies the vendored Linux Codex binary from the host into `~/bin/codex` for the guest user, seeds `~/bin/rg`, and verifies Codex.
- The bootstrap script writes `~/.codex/config.toml` with relay-aware defaults (currently `model = "gpt-6-sol"`, `model_reasoning_effort = "high"`, and `personality = "pragmatic"`), preferring explicit `LLM_BASE_URL` and otherwise reading `EVANDER_LLM_BASE_URL` from `~/.agent/config.env`; it registers the host relay as `[model_providers.evander_openai_relay]` with `supports_websockets = false` so Codex does not run websocket prewarm against the HTTP/SSE relay. It also registers the host MCP gateway as a required `[mcp_servers.evander]` connection using `EVANDER_GATEWAY_URL`, preventing model turns from racing tool discovery, and copies the host Codex model cache when available so offline guests see the same model menu. It then appends the placeholder-key shell hook plus `~/bin` PATH entry to both `~/.profile` and `~/.bashrc`.
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

- The optional [Teams editing tool and workflow effect](../packages/teams-graph-addon/README.md#edit-a-message-sent-by-an-agent-session)
  require an explicit session tool grant and the target's write/reply capability. Only newly recorded
  sends by that same active session are editable. Channel editing requires approved delegated edit
  consent and a tenant policy permitting message editing; an upgrade never adds scopes or grants.
  Back up outbound receipts with runtime data, and retry the original text after uncertain writes.

- Prefer [managed Teams custody](credentials.md) for new identities. Initialize the encrypted
  store, prepare sign-in in Configuration → Credentials, then use the existing Teams auth panel.
  Migrate existing JSON credentials during maintenance. Managed references require approved
  consumer bindings; changes to client, tenant, authority or scopes need owner approval and a
  new sign-in. Approved descendants can use/refresh a shared identity, while manual sign-in and
  sign-out belong to its owning directory.

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
- Token refreshes preserve the scopes granted to the stored credential; changing configured scopes
  never broadens an existing refresh grant. New device-code logins also retain authenticated tenant
  metadata for notification validation. A credential created before tenant metadata was retained
  must either be replaced with a new login or paired with a concrete `teams_delegated_tenant_id`
  before change notifications can become healthy.
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
  `teams_history_deadline_seconds`. Each attempt is separately capped by
  `teams_history_request_timeout_seconds`; transient failures retry inside the overall deadline.
- Teams history returns compact `metadata` by default: sender/session attribution, content type,
  reply/link details, attachment identifiers and names, card content, mentions, reactions and edit
  timestamps. The message body is returned once per message representation. Raw provider payloads
  remain in host storage; request `include_metadata: true` when those details are needed.
  Attachment listing and downloads continue to use the stored full records. MCP clients should
  consume one of the text or structured representations supplied by the protocol.

### Measuring workflow preparation overhead

Run `PYTHONPATH=src ./.venv/bin/python scripts/benchmark_teams_preparation.py --samples 9`
to measure an eleven-step durable workflow with a large synthetic configuration and message/session
payloads, plus the size of a five-message Teams MCP response. Use the same script and interpreter
with each checkout's `src` on `PYTHONPATH`, repeat in both orders, and avoid concurrent benchmarks.
The script uses temporary databases and immediate fake providers. It excludes external services,
worker/container startup and model response time, so its measurements are not end-to-end chat latency.

Worker application INFO logs are disabled by default. Temporarily enable the `evander.worker`
logger at INFO during latency investigations to record `worker_subscription_preflight` and
`codex_subscription_startup` durations in the journal. The four startup phases remain available in
job metadata without enabling INFO logging. Restore normal logging after the investigation. See
[Orchestration latency and recovery](orchestration-latency.md#reading-timings) for execution-storage
timings, available at DEBUG, that help distinguish workflow persistence from provider work.

## VM Python Bootstrap

- `./scripts/bootstrap_python_vm.sh <vm-name> <host-checkout-root> <guest-repo-root>` builds a temporary host wheelhouse from the repo's `pyproject.toml`, copies it into the guest, creates a repo-local `.venv`, and installs the repo with dev dependencies offline.
- The bootstrap validates that the guest Python interpreter satisfies the repo's `requires-python` lower bound before it creates the virtualenv.
- The guest install path uses `pip install --no-index --find-links ... --no-build-isolation -e ".[dev]"` so later guest edits still run against the cloned checkout rather than a prebuilt wheel.
- `./scripts/bootstrap_python_packages_vm.sh <vm-name> <package-spec> [package-spec ...]` is the lower-level generic variant for guest-local Python tools that are not tied to a repo checkout.
- The generic package bootstrap builds a temporary host wheelhouse for the requested package specs plus `setuptools`, `wheel`, and `virtualenv`, copies that wheelhouse into the guest under `~/.evander/python-wheelhouse/tools`, creates or reuses a guest tools virtualenv under `~/.evander/python-tools-venv`, and installs the requested packages offline with `pip install --no-index --find-links ...`.
- Override host/guest interpreter paths with `HOST_PYTHON_BIN` and `GUEST_PYTHON_BIN`, or override guest destinations with `GUEST_WHEELHOUSE_DIR` and `--venv` when needed.

## VM Playwright Bootstrap

- `./scripts/bootstrap_playwright_vm.sh <vm-name>` copies a host-seeded Node runtime into `~/toolchains/node-v24.22.1`.
- It copies `playwright` and `playwright-core` into `~/playwright-runtime/node_modules`.
- It copies the host Playwright browser cache into `~/.cache/ms-playwright`.
- It appends `PATH`, `PLAYWRIGHT_BROWSERS_PATH`, `NODE_PATH`, `PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD`, `PLAYWRIGHT_MCP_SANDBOX`, and `PLAYWRIGHT_MCP_EXECUTABLE_PATH` setup to `~/.profile`.
- `PLAYWRIGHT_MCP_EXECUTABLE_PATH` is resolved from the seeded Playwright package/cache so `playwright-cli` does not try to use `/opt/google/chrome/chrome` or download a browser during an agent task.
- It verifies the guest can resolve the Chromium executable without guest internet access.

## VM Frontend Dependency Bootstrap

- `./scripts/bootstrap_frontend_deps_vm.sh <vm-name> <host-checkout-root> <install-root> [install-root ...]` copies a host-seeded Node runtime into `~/toolchains/node-v24.22.1`.
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

## Container Worker MSSQL Client Toolchain

When worker MSSQL is enabled, the worker playbook packages the worker-installed `sqlcmd` and ODBC
Driver 18 into a worker cache after installing them, then mounts the cache read-only at
`/opt/evander/toolchains/mssql-client` in runner containers. The runner profile loads the cache
automatically when present. Supply a prebuilt archive with `HOST_MSSQL_CLIENT_ARCHIVE` to override
the worker build. For a standalone client toolchain with worker MSSQL disabled, set
`evander_worker_mssql_client_toolchain_build_on_worker: false` and provide
`HOST_MSSQL_TOOLS_ROOT` and `HOST_MSSQL_ODBC_ROOT` on the Ansible control machine.

The client does not grant database access by itself. An authorized session first provisions a
session-owned database with `mssql_provision_database` or `mssql_seed_provision_database`, then
uses the returned login with `SQLCMDPASSWORD`. For local snapshot evidence, place the approved
`.bak` in the seed sidecar backup directory, register a versioned alias through the add-on API,
and record the backup SHA-256 in the resulting artifact bundle.

## Container Worker NuGet Fallback Cache

- Container workers use a separate fallback cache, typically
  `/var/lib/agent-worker/toolchain-cache/nuget-cache-ro` on each worker VM.
- Prewarm that cache from the control plane, through the host NuGet relay, with:

  ```bash
  ./scripts/prewarm_container_worker_nuget_cache.sh \
    --worker worker.example.test \
    --target /srv/sample/SampleApp.slnx
  ```

- The script restores into a temporary global-packages folder, includes the
  worker restore-support package used by the offline smoke test, and extracts
  the archive additively into each worker cache.
- For a private feed, repeat `--private-package-pattern` for the package ID patterns
  configured under `provider_state.nuget.private_package_patterns` in the owning directory.
- When the `.NET` toolchain and checkout are already on the worker, run the
  same script on that worker with `--local --dotnet /var/lib/agent-worker/toolchain-cache/dotnet/dotnet`
  and set `NUGET_RELAY_BASE_URL` to the control-plane relay URL visible from the
  worker.

## Host-Side LLM Access

- The firewall allows only the host TCP ports listed in `ALLOWED_TCP_PORTS`.
- Defaults: `22` for host SSH/Git, `8000` for MCP, `11434` for the OpenAI relay, `11666` for the
  NuGet relay, `11777` for the host-side HTTP target relay, `11778` for the authenticated
  HTTP target browser proxy, `11881` for npm audit, `8081` for the host-side Nexus package cache,
  and `3142` for the host-side apt-cacher-ng relay. The browser proxy preserves the upstream origin rather than
  rewriting application HTML.
- Override when applying firewall rules, for example:

  ```bash
  sudo ALLOWED_TCP_PORTS="22 8000 11434 11666 11777 11778 11881 8081 3142 4000" ./scripts/apply_firewall.sh
  ```

- The guest receives `EVANDER_LLM_BASE_URL`, `OPENAI_BASE_URL`, `EVANDER_ANTHROPIC_BASE_URL`, and `ANTHROPIC_BASE_URL` in `/etc/evander/session.env`.
- `evander-openai-relay` is a host-side model proxy that reads real provider API keys only from the host environment. In public OpenAI mode it forwards guest requests to `https://api.openai.com/v1`.
- For metered model access, the guest talks to the relay URL and keeps placeholder provider auth
  values. Host-side provider settings select public OpenAI, a local OpenAI-compatible endpoint,
  Azure OpenAI, Anthropic, or OpenRouter. Container Codex subscription turns use the separate
  [brokered subscription path](#switching-container-codex-usage-source).
- For Azure OpenAI Codex routing, set `model_provider_mode: "azure_openai"` in `root` or directory `providers.json` and configure `azure_openai_endpoint`, `azure_openai_api_key_env`, `azure_openai_api_version`, and `azure_openai_model_deployments`.
- Example directory-scoped `providers.json` overlay:

  ```json
  {
    "model_provider_mode": "azure_openai",
    "azure_openai_endpoint": "https://example-resource.openai.azure.com/",
    "azure_openai_api_key_env": "AZURE_OPENAI_API_KEY",
    "azure_openai_api_version": "2024-10-21-preview",
    "azure_openai_model_deployments": {
      "gpt-6-sol": "codex-gpt-6-sol"
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

  Keep Ollama host-local or on a private control-plane address; guests and worker containers should still reach only Evander's session-scoped relay URL. Because the relay itself defaults to `0.0.0.0:11434`, `local_openai` rejects the default `http://127.0.0.1:11434/v1` upstream unless the relay is moved off wildcard/loopback on that port. Either bind the relay only on the guest-facing control-plane address while Ollama keeps `127.0.0.1:11434`, or move Ollama to another private port and set `local_openai_api_base_url` accordingly.
- Example local Ollama directory-scoped `providers.json` overlay when the relay keeps its default listener and Ollama is moved to `127.0.0.1:11435`:

  ```json
  {
    "model_provider_mode": "local_openai",
    "local_openai_api_base_url": "http://127.0.0.1:11435/v1",
    "local_openai_api_key": "ollama",
    "local_openai_provider_name": "local_openai"
  }
  ```

- To protect a shared local backend, set `local_openai_max_concurrency` in the root
  `providers.json`, for example `"local_openai_max_concurrency": 1`. This is one host-global pool
  across libvirt and container-worker agents; child directories cannot override it. Evander first
  resolves the prompt's final model and provider mode, then durably queues excess local turns in
  FIFO order. Omit the field to keep unlimited local concurrency. A queued Team dispatch remains
  reserved but unleased until its prompt is accepted for delivery, so queue time does not consume
  the dispatch lease.

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
- OpenRouter is opt-in through `model_provider_mode: "openrouter"` and
  `openrouter_model_routes`. Select an explicit approved session model and
  metered API authentication. Use the [OpenRouter operator guide](openrouter.md)
  for provider endpoint selection, mandatory ZDR, compaction compatibility,
  usage evidence, and rollback. No additional service or guest port is needed.
- For Claude Code routing, set `model_provider_mode: "anthropic"` and configure `anthropic_api_key_env`; optional settings include `anthropic_api_base_url`, `anthropic_api_version`, and `anthropic_relay_timeout_seconds`. The Anthropic mode currently forwards `GET /v1/models`, `POST /v1/messages`, and `POST /v1/messages/count_tokens`; Anthropic websocket/realtime requests are rejected.
- The relay records provider-neutral model usage metadata for HTTP, SSE, and
  WebSocket Responses/Completions traffic that exposes a provider `usage`
  payload. It also records `usage_unavailable` when a completed response has no
  usage block. Azure OpenAI and Anthropic WebSocket/realtime and Azure managed
  identity are not included.
- Hosted OpenAI web search is denied by default at the relay. Set the session
  policy flag `web_search_enabled: true` only for agents that should be allowed
  to declare Responses `web_search` tools. For public OpenAI container Codex
  sessions, refreshed session files also enable Codex standalone web search
  through `POST /v1/alpha/search` on the same relay. The relay sends a hosted
  Responses web search on behalf of the session and returns source URLs.
- OpenAI image generation and editing are denied by default. Set the session
  policy flag `image_generation_enabled: true` only for agents that should be
  allowed to call `POST /v1/images/generations`, `POST /v1/images/edits`, or
  declare Responses `image_generation` tools. The standard App demo workflows
  opt in after provisioning metadata is stored; ordinary sessions remain
  disabled.
- Direct image requests use host-injected OpenAI authentication. Generation
  JSON is capped at 1 MiB by default; multipart edits are capped at 256 MiB,
  spooled, and streamed upstream. Usage audit rows contain sizes, hashes, and
  provider usage but not prompts or image/base64 content.
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
- Once an MCP transport session is bound to an Evander session, later `/mcp` requests on that
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
- Gateway tool name: `delete_ado_backlog_comment`.
  - Required MCP arguments: positive numeric work-item `id` and `comment_id`; optional `project`.
  - Read comment IDs using `get_ado_backlog_item` with comments included.
  - Deletes the selected comment through the calling session directory's ADO connection,
    then invalidates that directory's cached work-item activity scans. Provider failures
    propagate without reporting a successful deletion.
  - Grant this tool explicitly in `allowed_tools`; normal directory policy, argument rules,
    and configured per-invocation approvals apply. The host PAT needs `vso.work_write`.
  - API reference: [Azure DevOps work-item comment deletion](https://learn.microsoft.com/en-us/rest/api/azure/devops/wit/comments/delete?view=azure-devops-rest-7.1).
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
- Gateway tool name: `update_ado_backlog_state`.
  - Policy-gated with `update_ado_backlog_state`; grant it explicitly in the agent's session or
    provisioning configuration. Existing ADO read/comment grants do not imply this permission.
  - Required MCP arguments: positive numeric `id`, non-empty `state`.
  - Optional MCP arguments: `project`, `expected_state`.
  - Uses the session directory's host-owned ADO connection and updates only `System.State`.
    State names are trimmed and must be valid for the item's ADO process.
  - Supplying `expected_state` adds an atomic JSON Patch test before the state mutation. ADO
    rejects mismatches; the gateway propagates the error and does not retry without the guard.
    Omitting it sends an unconditional state update.
  - Example: after implementation tasks have been assigned, call with `state="In Progress"`
    and `expected_state="Committed"`, then verify with `get_ado_backlog_item`. If the item is
    already in the target state, no update is needed. After a conflict or timeout, read back
    before retrying, and preserve later states rather than moving them backwards.
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

## Host-Side Azure DevOps Build Read Tools

- Grant `list_ado_builds`, `get_ado_build`, `get_ado_build_timeline`, `get_ado_build_log` and
  `list_ado_build_work_items` separately in the
  session's `allowed_tools`. Installing or upgrading does not change existing grants.
- These tools use the session directory's configured Azure DevOps organization, project,
  and host credential, and make read-only requests. They accept no project or credential override.
- `list_ado_builds` accepts optional, combined filters: `build_number` (exact match, or append
  `*` for a prefix search), positive `pipeline_id` (build definition ID), `repository_id` and
  `branch` (full ref, such as `refs/heads/main`). With no filters it lists recent builds.
  `limit` defaults to `25` and must be an integer from `1` to `100`. Results are ordered by
  queue time, newest first. A provider response exceeding the requested limit is an error.
- The list response contains `project`, `builds`, `count` and `continuation_token`. Each build
  includes `run_id`, `build_number`, `run_url`, status, result, source details and timestamps,
  plus `pipeline_id`, `pipeline_name`, `repository_id` and `repository_name` when available.
  Pass a non-null continuation token unchanged with the same filters for the next page.
  A null token means there is no next page. All matches on the page are returned; build numbers
  need not be unique across pipelines. Use pipeline/repository and deployment evidence to select
  the intended build, then pass its `run_id` as `build_id` to the other read tools.
- `get_ado_build` and `list_ado_build_work_items` take a positive numeric `build_id`.
- `get_ado_build` returns the build number, status, source branch, source commit
  (`source_version`), timestamps, and build URL.
- `get_ado_build_timeline(build_id, timeline_id?, skip=0, limit=200)` returns normalized stages,
  jobs and tasks with parent IDs, status/result, issue messages, attempts, log IDs and nested
  timeline IDs. `limit` is at most `1000`. The response includes `total_count`, `count`,
  `truncated` and `next_skip`; follow `next_skip` until null. The provider JSON is bounded to
  2 MiB. Pass a returned `details_timeline_id` as `timeline_id` to inspect a nested timeline.
- `get_ado_build_log(build_id, log_id, start_line?, max_lines=200)` reads a log referenced by
  the timeline. Omit `start_line` to read a bounded tail, or supply a zero-based offset to read
  forward. `max_lines` is at most `1000`; output text is capped at 64 KiB. The response includes
  `text`, `start_line`, exclusive `end_line`, `returned_lines`, `total_lines`, `next_start_line`
  and explicit truncation flags. A byte-truncated final line is marked `partial_last_line`;
  continuation advances past that line. Log metadata is resolved on the configured build,
  never through provider-supplied URLs. Empty logs return empty text; unavailable logs fail
  explicitly. Diagnostic audit summaries omit issue messages and log contents, and responses
  reflecting the host credential are rejected.
- `list_ado_build_work_items` returns associated work-item IDs for the configured project.
  `limit` defaults to `200` and can be at most `1000`; `truncated` indicates that more IDs were
  returned by Azure DevOps than the tool included. Azure DevOps excludes work items from other
  projects in this operation. Use `get_ado_backlog_item` to inspect a returned ID.
- A build's associated work items are evidence about that build. Confirm which build was deployed
  before reporting an item's inclusion in a release.

## Host-Side Azure DevOps Work Item Activity Tool

- Gateway tool name: `query_ado_work_item_activity`.
- The tool is policy-gated: each session manifest must include
  `query_ado_work_item_activity` in `allowed_tools` before calls are accepted.
- MCP arguments:
  - required `since` timestamp
  - optional `until`, `project`, `actor`, `activity_types`, `states`, `types`, `tags_any`
  - paging inputs: `top` (default `100`, max `200`) and `skip` (default `0`)
  - scan controls: `max_candidates` (default `50`, max `1000`) and `exhaustive` (default `false`)
- `activity_types` defaults to `["created", "changed", "commented"]`.
- By default the tool bounds the number of matching candidate work items it expands into detailed
  activity records. Set `exhaustive=true` only when exact totals are needed and the wider ADO scan
  cost is acceptable.
- Identical gateway queries in the same session reuse a completed scan for five minutes;
  `top` and `skip` select pages from that result set. Overlapping identical queries share
  one scan. Expiry, eviction, or a successful work-item mutation through this gateway
  refreshes the affected result set. External changes can remain unseen until expiry.
- Each gateway instance retains at most 64 MiB of serialized scan data and 128 entries.
  Oversized results are returned without retention; active scans and decoded responses
  use additional memory. Restarting the gateway clears the cache.
- Cold scans fetch full work-item records in batches of up to 200, followed by concurrent
  comment and update-history reads. The cache does not change exhaustive or bounded
  completeness semantics. Policy and current provider credentials are checked on hits.
- `ado_activity_cache` and `ado_activity_scan` logs expose cache outcomes, retained bytes,
  candidate counts, and scan phases without logging work-item contents or credentials.
- Update-history records use the semantic `System.ChangedBy.newValue` and
  `System.ChangedDate.newValue` fields when ADO provides them, and metadata-only revisions are
  omitted from the default activity output.
- Response payload includes matching work items and per-item `activities` records with the matched
  actor, timestamp, provenance (`created_field`, `changed_field`, `update`, or `comment`), and raw
  provider payload snippets. Paging includes `candidate_scan_complete` and
  `total_activity_matches_complete`; when either is `false`, totals only describe the scanned
  candidates.

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

## Codebase Consultation Workers

Use `consult_codebase` when an agent needs a fast, bounded repository answer instead of assigning
a durable task to another existing agent. The caller must provide a `managed_repo_id` plus either
`source_ref`, `commit_sha`, or a repository with a configured default branch. Evander resolves the
ref once and records the exact commit before dispatch.

- Required services: gateway, trigger orchestrator, effect orchestrator, OpenAI relay, and the
  configured container worker.
- Caller permissions: `consult_codebase` and `get_codebase_consultation`; add
  `cancel_codebase_consultation` when cancellation is needed.
- Configure at least one `agent_consultation_profiles` entry and either pass its name or set
  `agent_consultation_default_profile`.
- For Ollama, use `model_provider_mode: local_openai` and ensure the host relay's
  `local_openai_api_base_url` reaches the Ollama OpenAI-compatible endpoint. The worker still talks
  only to the host relay.
- The initial call waits for at most the smaller of `wait_seconds` and
  `agent_consultation_max_wait_seconds`; a nonterminal response is expected and can be polled.
- Requests exceeding `max_concurrency` remain queued. `timeout_seconds` includes queue time and
  execution time.
- Timeout and cancellation flow through durable internal events. Cleanup destroys the worker
  session, revokes its managed-Git binding/key, releases the temporary consultation snapshot ref,
  and only then releases profile capacity.
- Runtime records are stored in `runtime/agent-consultations.db` and fully cleaned terminal records
  older than `agent_consultation_retention_days` are compacted on new requests. Records with
  incomplete cleanup are retained for diagnosis and retry.
- The result token defaults to the host secret named by `AGENT_PROMPT_TOKEN_SECRET`; override the
  env-var name with `agent_consultation_token_secret_env` when isolation is preferred. Never put
  the secret value in directory configuration.

The hidden `system.v1` graph owns preparation, prompting, finalization, timeout, and cleanup. Do
not duplicate those nodes in an operator workflow pack.

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

The Team Add-on **Teams** view can start a browser WebRTC call to the selected persistent agent. This
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
existing active Evander session in the same directory. Keep the bridge host-local and enable the
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

#### Caller routing to agent sessions

To share one DIDWW number across agent sessions, open **Configuration → root → People &
Communications → Phone calls → Caller routing**. Unlock operations, add a called number,
select its existing inbound phone route, then add caller phone numbers and choose their
agent sessions. The picker lists all active sessions, including issue and worker sessions;
the destination does not need to be a personal assistant. Save changes to persist the map.

The root `providers.json` representation is:

```json
{
  "inbound_phone_caller_maps": [
    {
      "did": "+15551230000",
      "route_id": "ops-main",
      "callers": [
        {
          "caller_number": "+15551230001",
          "session_id": "agent-session-a",
          "directory_path": "root/ops"
        },
        {
          "caller_number": "+15551230002",
          "session_id": "agent-session-b",
          "directory_path": "root/builds"
        }
      ]
    }
  ]
}
```

Each map references one existing `inbound_phone_routes` entry for its voice prompt, greeting
and caller access policy. Its caller assignments replace that route's default `session_id`
and directory for mapped calls. The gateway resolves provider and controller settings from
the selected session's directory and requires that session to remain active in that directory.
Keep a shared route's prompt appropriate for all destinations. The bridge must have access to
the controller token environment variable configured in each destination directory, as it does
for existing fixed routes.

Assignments match both the called DID and caller number. Enter international numbers with
country codes; formatting and an optional `+` do not affect matching, and the gateway does not
infer country codes. The same caller can reach different sessions through different DIDs.
Overlapping caller allowlists do not select a destination; each route's allow/block policy is
still enforced after the explicit lookup.

Unmapped and withheld callers are rejected. Removing the last caller retains the DID map with
an empty `callers` list and rejects all calls to that number. Use **Remove called number and
restore fixed routing** to explicitly disable caller routing for a DID; numbers without a map
keep their existing fixed routing. A bridge-supplied route ID cannot bypass the map.

Changes apply to new calls without restarting the gateway. Calls already admitted keep their
original session for delegation and transcript handling. If a destination session is replaced,
update its assignment; unavailable selections remain visible in the editor rather than being
silently redirected. Phone numbers remain host-side configuration, while voice stores and audit
events retain only their redacted or hashed identities.

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
- Missing, invalid, or out-of-scope controller credentials: the gateway returns a generic `401`,
  including when routing or caller-map lookup fails.
- Caller policy rejection after controller authentication: the gateway returns `403`, no
  voice-session record is exposed, and the audit log contains only route ids plus redacted/hash
  identity fields.
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
{"session_id":"session-1","id":15446,"project":"Example Industry App","add_tags":["auto-dev-approved"]}
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
  - `ado_organization` (for example: `example-org`)
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
- For Discord forum posts or public text-channel threads, configure the parent communication
  target with `policy.discord_conversation_scope`:
  - `channel` keeps the existing flat-channel behavior and is the default.
  - `public_threads` ingests only public/announcement child threads.
  - `channel_and_public_threads` ingests both the parent channel and its public children.
  The bot needs `View Channel`, `Read Message History`, and `Send Messages in Threads`. Thread
  discovery is REST-polled and persisted in the Discord inbox SQLite store. The first scan
  baselines already-active threads; newly discovered threads include their starter message.
- Outbound `thread_id` values are accepted only when the stored public-thread binding points back
  to the selected parent target. Archived or locked threads fail with a stable error; Evander does
  not request moderation permission or automatically reopen them. Resolved agent identities reuse
  a webhook owned by the parent channel and route its execution into the child thread.
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
- Optional per-agent sender identity is configured on a saved Discord communication target with
  `policy.sender_identity = "resolved"`; the default is `"platform"`. Raw channel-ID sends retain
  the bot identity. Identity providers are add-on extensions: Team Add-on resolves the persistent
  agent attached to the sending session and returns that agent's display name and avatar.
- Workflow `effect:communication:send-message` nodes may bind `sender_session_id` when the selected
  target should resolve an agent identity. The Team Runtime external-chat reply graph binds the
  replying agent's session automatically; older or generic workflow sends without that field keep
  the platform identity fallback.
- Resolved identities use host-created incoming webhooks scoped to the destination channel and
  avatar digest. The avatar is installed as Discord-hosted webhook image data and the display name
  is overridden per message, so no public Evander avatar URL, Discord persona registration, or
  bot/application per agent is required. Grant the Evander bot permission to manage webhooks in
  each opted-in channel.
- The gateway lazily creates persona webhooks on first use and stores their ID/token in
  `discord_webhook_store_path` (default `runtime/discord-webhooks.json`, mode `0600`). The pool is
  bounded by `discord_persona_webhook_cache_limit` (default and maximum `10`); after a new avatar
  is provisioned, least-recently-used persona webhooks beyond the limit are removed. A stale
  webhook is recreated once after a Discord `404`. Definite provisioning/delivery failures fall
  back to the ordinary bot message; transient or ambiguous failures are not duplicated through a
  second transport.
- In Admin Web, save the Discord target first, unlock privileged operations, then use the target's
  Discord persona webhook controls to Provision, Test, or Recreate it. Status is read-only and
  never returns the webhook token. Recreate stores the replacement before attempting to delete the
  old webhook.
- PNG and JPEG persona avatars are sent to Discord as image data when their webhook is created;
  WebP avatars are converted to PNG first. If image preparation fails, Evander uses a generic
  channel webhook with the resolved name and records avatar fallback metadata. The signed public
  communication-artifact settings remain relevant to non-image Teams file links, not Discord
  persona avatars.
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
  - `auth_env_var_names` (env vars that hold a static OAuth access token, or Graph tenant ID,
    client ID, and client secret)
  - `folder_allowlist_defaults` (typically `inbox` and `drafts`)
- Define `email_folder_allowlist_by_mailbox` for each mailbox so session policy is explicit.
- Keep OAuth credentials host-only and never place their values in guest images, cloud-init, or
  session manifests. Prefer Graph client credentials so the host renews short-lived access tokens
  automatically; static access-token references remain available for compatibility.
- Email is **read + draft only by default**. Keep `email_send_enabled: false` unless you are ready
  to allow `email_send` and enforce approval through MCP approval policy where required.

### Discovering mailbox grants

Call `email_list_mailboxes` before reading mail or creating drafts when the mailbox ID is unknown.
The tool returns `mailboxes`, each containing `mailbox_id`, `provider`, `account_identifier`,
`access` (`read`, `draft`, or `send`) and logical `folders`. Use `mailbox_id` in subsequent tools,
not the account's email address. For example, mailbox ID `sales` can identify `sales@example.com`.

Discovery is available to existing sessions with an allowed email operation, and is included when
creating email-enabled sessions. It can also be granted explicitly. Directory tool denials and
approval rules still apply. Only configured mailboxes authorized by the calling session are
returned, using that session's directory configuration. Explicit empty grants or folder lists stay
empty. Legacy manifests use their existing mailbox/folder allowlist and send-enable rules.

Discovery reads local configuration without contacting the mail provider or reading messages.
It exposes no credential values, credential references or provider folder IDs. The reported access
is mailbox-level permission; each operation still needs its own allowed tool, and sending still
requires send enablement, recipient targets and any applicable approval. Provider authentication is
not verified by discovery. Drafts may have empty recipients and do not require send permission.

### Paginated mailbox listing

Use `email_list_messages` for historical or filtered mailbox-folder scans. Grant the tool
explicitly and give the session read access to the selected `mailbox_id` and `folder`; ordinary
email-enabled sessions do not receive this broader listing tool automatically. Keep
`email_list_unread` for incremental unread processing.

The required arguments are `session_id`, `mailbox_id`, and `folder`. `limit` defaults to `25` and
accepts `1` through `100`. Optional filters are `received_after`, `received_before`, `unread`,
`sender_contains`, `recipient_contains`, `subject_contains`, `text_contains`, and
`has_attachments`. Date filters must be timezone-aware, and `received_after` must be earlier than
`received_before`.

The result contains `messages` and an opaque `next_cursor`. To read another page, pass that cursor
back with the same session, mailbox, folder, and complete filter set. Cursors expire after 15
minutes and cannot be reused with changed filters; omit `cursor` to start a new scan. Listing
returns message summaries. Use the separately granted `email_get_message` tool with a returned
`message_id` when the full message body or attachment metadata is needed.

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

1. Register a confidential application in Microsoft Entra ID and grant the least-privileged
   Microsoft Graph application permissions required by the mailbox workflow (for example,
   `Mail.Read` for inbox processing), including administrator consent.
2. Scope application access to the intended mailbox or mailboxes in Exchange Online.
3. Export the application identity and credential only on the host:

```bash
export GRAPH_TENANT_ID=...
export GRAPH_EXEC_CLIENT_ID=...
export GRAPH_EXEC_CLIENT_SECRET=...
export GRAPH_FINANCE_CLIENT_ID=...
export GRAPH_FINANCE_CLIENT_SECRET=...
```

4. Reference the env vars in mailbox config. Evander lazily exchanges the client credentials at
   the Microsoft identity platform token endpoint, caches the returned Graph access token, and
   renews it shortly before expiry:

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
      tenant_id: "GRAPH_TENANT_ID"
      client_id: "GRAPH_EXEC_CLIENT_ID"
      client_secret: "GRAPH_EXEC_CLIENT_SECRET"
    folder_allowlist_defaults: ["inbox", "drafts"]
  - mailbox_id: "finance-graph"
    provider: "graph"
    address: "finance@example.com"
    auth_env_var_names:
      tenant_id: "GRAPH_TENANT_ID"
      client_id: "GRAPH_FINANCE_CLIENT_ID"
      client_secret: "GRAPH_FINANCE_CLIENT_SECRET"
    folder_allowlist_defaults: ["inbox", "drafts"]
```

For compatibility, a Graph mailbox can instead reference `auth_secret_refs.access_token`, but the
operator must then replace that short-lived token whenever it expires. Do not put a client secret
in an `access_token` environment variable; client secrets must be exchanged at the token endpoint.

### Multi-mailbox session allowlist example

```bash
evander-cli directories config-set \
  --runtime-dir runtime \
  --domain policy \
  root \
  '{"email_mailbox_allowlist":["ops-gmail","exec-graph","finance-graph"],"email_folder_allowlist_by_mailbox":{"ops-gmail":["inbox","drafts"],"exec-graph":["inbox","drafts"],"finance-graph":["inbox","drafts"]}}'

evander-cli create-session real-session --runtime-dir runtime
```

Admin Web exposes two independent session controls:

- **Email mailbox access** selects mailbox accounts, folders, and `read`, `draft`, or `send`
  capability. Prefer `read` for agents that only inspect mail.
- **Communication targets** selects Teams/Discord destinations and outbound email recipients. These
  targets do not grant mailbox access.

Email send requires both a `send` mailbox grant and a write-capable recipient target bound to the
same mailbox auth profile. Draft creation requires `draft` access but no recipient target. Existing
sessions with nullable canonical fields remain on their legacy allowlists until the operator clicks
**Convert to managed grants**; explicit empty canonical lists mean no access.

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
        "name": "App backlog item constraints",
        "constraints": [
          {
            "id": "project-fixed",
            "path": "project",
            "operator": "fixed",
            "value": "Example Industry App"
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

## View images in Azure DevOps work items

`get_ado_backlog_item` recognizes both HTML and Markdown images in Description, Repro Steps,
System Info and Acceptance Criteria, plus requested comments and attachment relations. It returns
image references in `inline_media`; it does not send image bytes to the model. Markdown inline
and reference-style images are supported; images written as Markdown code examples are ignored.

For an agent that should inspect these images, use **Sessions → the agent's session** to grant
`list_inline_media`, `download_inline_media`, and `communication_copy_artifact_to_workspace`.
Enable `communication_file_receive_enabled` in its policy and retain appropriate file size,
MIME and extension limits. Use the session setting when only one agent needs access. Reading ADO
text alone does not grant file reception, and directory tool denials still apply.
Work-item responses with images include `metadata.inline_media_access` with the effective receive
flag, unavailable tools and viewing instructions. File-specific and argument policies are checked
when the agent actually downloads/copies a selected image.

The agent can use `inline_media` directly or refresh it with `list_inline_media`:

1. Use `source_kind="ado_backlog"`, `provider="azure_devops"`, `resource_id="<work item ID>"`
   and `project="<ADO project>"` when listing or downloading.
2. Call `download_inline_media` with a returned `media_id`. The host authenticates to ADO and
   stages the bytes under an opaque `artifact.artifact_id`.
3. Call `communication_copy_artifact_to_workspace` with that `artifact_id` and a unique
   `destination_filename` per image, such as `work-item-101-dialog.png`. ADO commonly names every
   screenshot `image.png`; using the same destination would overwrite the preceding image.
4. Open the returned `destination_path` with the agent's local image viewer. The copy works with
   both VM and container-worker sessions; credentials remain on the host.

An empty media list means no supported references were found in the requested fields/comments/
relations. Disabled reception or missing tools are access restrictions, not missing images.
There is no need to enable image generation or general guest internet access to inspect them.

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
   - `list_unread_feedback(cursor=..., limit=...)` fetches unread items and immediately advances
     the host's session read cursor to the returned `next_cursor`. An explicit cursor cannot
     rewind the saved host cursor. Use a small limit when bounded batches are needed.
   - `list_all_feedback(limit)` when you need full PR comment history, including already-read items
   - `list_pr_feedback_attachments(feedback_id)` when an Azure DevOps feedback body references a
     file. Select an `attachment_id`, call
     `download_pr_feedback_attachment(feedback_id, attachment_id)`, then pass the returned
     `artifact_id` to `communication_copy_artifact_to_workspace` if the guest needs to inspect it.
     The session must allow all three tools and effective policy must enable inbound file reception
     with a matching MIME, extension, and size allowance.
   - `list_pull_requests(provider, repo, status?, statuses?, creator_id?, reviewer_id?, project?,
     min_time?, max_time?, query_time_range_type?, source_ref_name?, target_ref_name?, tags?, top?,
     skip?, cursor?)` to enumerate PRs directly from the provider. Azure DevOps status values are
     forwarded to `searchCriteria.status`; `closed` expands to ADO `completed` and `abandoned`.
   - `get_pull_request(provider, repo, pr_id, include_reviewers?, include_commits?)`
     to fetch a single provider PR record, map stable top-level fields (title/status/branches/commits/reviewers),
     and preserve provider-specific details in `raw`.
   - `update_pull_request(provider, repo, pr_id, description?, title?, status?, add_tags?,
     remove_tags?, expected_statuses?, project?, artifact_ids?, request_id?)` to update Azure DevOps
     PR metadata through directory-scoped host credentials. The description argument replaces
     the full description;
     use `expected_statuses=["active"]` when the request must not mutate a closed PR.
   - `resolve_ado_identity(query)` to resolve `me`, an email/UPN, display name, ADO id, or descriptor
     before using personal activity filters.
   - `list_ado_pr_comment_activity(repo, since, author?, until?, project?, statuses?, top?, skip?,
     exhaustive?, max_candidates?)`
     to find ADO PR comments matching an author and date range.
   - `list_ado_pr_commit_activity(repo, since, author?, until?, project?, statuses?, top?, skip?,
     exhaustive?, max_candidates?)`
     to find ADO PR commits matching an author and date range.
     These PR activity tools default to scanning at most 50 candidate PRs before fetching
     comment/commit details; set `exhaustive=true` when exact totals across all candidate PRs are
     required.
3. Post threaded replies (when needed):
   - `post_pr_feedback_reply(provider, repo, pr_id, body_text, thread_id/comment_id, feedback_kind, idempotency_key)`
4. Local completion:
   - persist the returned feedback IDs and bodies before processing; record completion and reply
     idempotency keys separately. Fetching has already marked the batch read on the host, so
     local completion is not a host acknowledgement operation.

For session-scoped MCP connections, agents should omit `session_id` on these self-scoped tools. The
gateway binds the caller session from the MCP connection and rejects conflicting legacy
`session_id` arguments.

### Guest state model

- Keep a durable local inbox of fetched items and their handling status per session.
- `has_pending_feedback` checks unread state without consuming it. `list_unread_feedback`
  consumes the host unread cursor on fetch; `list_all_feedback` ignores read cursors and supports
  recovery from already-read history.
- A local checkpoint does not rewind the host cursor. Do not rely on an older `cursor` argument
  to redeliver a fetched batch.
- Keep handlers idempotent by feedback ID to tolerate retries/replays.

### Cron-driven pull request polling

Use `trigger:cron` with `query:pull-request:list` when a workflow needs one-shot
query control over open pull requests by provider state, branch, time window, or
review labels, then use `effect:pull-request:update` to claim or release work by
changing PR tags or metadata. For Azure DevOps, set `provider` to
`azure_devops` or `ado`; the node uses the directory-scoped ADO project and PAT unless
`project` is supplied, and it always constructs the provider target from the configured
or supplied `ado_organization`. Provider URL overrides are rejected so a workflow cannot
redirect directory credentials to another endpoint.

Use `trigger:pull-request:new-feedback` or `trigger:pull-request:completed`
with `provider=ado` and `repo` when the Azure DevOps PR worker should own
polling directly. Setting `repo` enables explicit trigger criteria. Omitting
`repo` keeps the legacy `runtime/pr-map.json` compatibility path for existing
agent-created PR streams.

Use `query:pull-request:get` when a workflow needs a single normalized snapshot
of a specific pull request. The node accepts Azure DevOps `provider` values
`azure_devops`, `azure-devops`, and `ado`, plus GitHub `provider` `github`, and
it does not store cursor or workflow state. For Azure DevOps, it uses the
directory-scoped organization, project, and PAT unless `ado_organization` or
`project` is supplied on the node. The organization identifier is always
constructed into the trusted `https://dev.azure.com/<org>` origin. For GitHub, it uses the directory-scoped
`github_token_env` when configured and can fetch public data without a token.
Set `include_commits=true` to fetch commit details and `include_reviewers=true`
to fetch review API data; `include_reviewers=false` returns an empty reviewer
list.

Use `effect:pull-request:reply` to post a deterministic host-owned response to
PR feedback. Bind its provider, repository, PR, thread/comment IDs,
feedback kind, and an event-derived idempotency key from the feedback trigger.
For Azure DevOps, also bind the trigger's organization and project so any trigger
override is preserved. The effect uses directory-scoped credentials, records its
claim transactionally across workers, and filters the resulting reply from later
feedback polls. It is intentionally separate from agent wake and MCP approval flows.

Use `query:pull-request:work-items` when an Azure DevOps workflow needs the
work items linked to one PR. It returns the linked work items in provider order
with a normalized assignee identity (`display_name`, `unique_name`, and `email`
when available), so a workflow can make deterministic assignee or PR-owner
decisions without passing the Azure DevOps credential to an agent.

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
- `managed_repo_id`: required; it must be visible inside `directory_path`.
- The endpoint uses the directory-configured Azure DevOps organization and project;
  it does not accept provider-target overrides.

The service refreshes the managed repository and performs the simulation in a unique detached
operation worktree. That worktree is removed on every exit and is never returned in the report.

The response includes `status`, `summary_text`, `participants`, `checks`,
`warnings`, and `errors`. Status values are:

- `clean`: the merge simulation was clean.
- `conflict`: one or more merge checks found content conflicts.
- `missing_ref`: a source or target commit/ref could not be resolved locally.
- `stale_target_branch`: the target branch moved since Azure DevOps recorded it.
- `repo_error`: the managed repository could not be refreshed or queried.
- `provider_error`: the Azure DevOps lookup failed.

Example request and conflict summary:

```bash
curl -sS -X POST http://127.0.0.1:8100/api/admin/pr-integration-preflight \
  -H 'content-type: application/json' \
  -d '{
    "provider": "azure_devops",
    "repo": "repo-1",
    "directory_path": "root/team-a",
    "managed_repo_id": "mr_0123456789abcdef0123",
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

- Discord ingest stores messages for message triggers; downstream workflow prompt effects own agent dispatch.
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
  For a session-bound legacy entry, the current session manifest's
  `directory_path` is authoritative for routing and filtering; an older
  directory value persisted in `pr-map.json` is only a fallback when the
  session no longer resolves.
  Keep `pr-map.json` compatibility until a later cleanup removes the older path.
- Recommended service lifecycle commands:
  - `sudo systemctl start evander-scheduled-wake.service`
  - `sudo systemctl stop evander-scheduled-wake.service`
  - `sudo systemctl status evander-scheduled-wake.service`
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
- Guest heartbeat timers are retired. Re-running a provider bootstrap disables and removes any old timer assets; message workflows and scheduled wakes own resume dispatch.

### Failure handling

- MCP unavailable/timeouts:
  - A fetch may have advanced the host cursor even if its response was lost. Inspect the local
    inbox and use `list_all_feedback` to reconcile missing items by feedback ID.
  - Log the failure and retry with bounded backoff as appropriate.
- Provider API failures during `post_pr_feedback_reply`:
  - Keep the fetched item pending in the local inbox; the host read cursor has already advanced.
  - Retry with the same `idempotency_key` so duplicates are suppressed if the prior attempt actually succeeded upstream.
  - Validate host-side provider credentials and endpoint reachability before replaying.
- Partial local processing or a crash before persisting the fetched response:
  - Resume locally retained pending items. Recover missing items from `list_all_feedback` and
    compare feedback IDs before replying again.
  - Fetching unread feedback is not an acknowledgement-after-processing queue; workflows that
    need their own checkpoint should use the separate read-only query and workflow-owned state.

### Audit and log file locations

- Host feedback state: `runtime/pr-feedback-store.json` (ingested comments + cursors), `runtime/pr-reply-idempotency.json` (reply dedupe keys/results), `runtime/host-agent-control-state.json` (durable enabled/disabled state by VM).
- Host audit trail: `runtime/audit.jsonl` (canonical append-only audit journal, plus
  denials/errors). Admin Web Tool Calls indexes tool-call entries into `runtime/audit.db`
  for faster filtering and can rebuild that index from JSONL.

### Security boundary reminder

- Guests use MCP for provider tools and the dedicated relays for model/package/HTTP access;
  they do not hold the host's Discord, email, or model provider credentials.
- Upstream provider access and secret material stay host-side in gateway/relay components.

## Review Request Workflow

Use this run pattern when a host-side receptionist or chat-facing agent should enqueue PR review
work and let the trigger orchestrator start a temporary review VM.

### Host-side contract

- Enqueue review work with `request_pr_review(session_id, provider, repo, pr_id, ...)`.
- Consume queued review requests with `trigger:review:new-request`.
- Update Azure DevOps PR metadata with `update_pull_request(...)` only when that tool is explicitly
  allowed for the session; directory policy may additionally require per-invocation approval.
- Publish review output with:
  - `post_pr_comment(...)` for top-level PR conversation comments
  - `post_pr_inline_comment(...)` for one file/line finding; prefer this over writing
    `path line N` at the start of a top-level comment
  - `submit_pr_review(...)` for a summary plus optional batched inline findings
  - `post_pr_feedback_reply(...)` when replying to an existing feedback thread/comment

For Azure DevOps, `submit_pr_review` accepts the following `event` values:

| Event | Reviewer vote |
| --- | --- |
| omitted or `COMMENT` | Publish comments without changing the vote |
| `APPROVE` | Approved (10) |
| `APPROVE_WITH_SUGGESTIONS` | Approved with suggestions (5) |
| `WAITING_FOR_AUTHOR` or `REQUEST_CHANGES` | Waiting for author (-5) |
| `RESET` | No vote (0) |

Reject is unsupported. A voting call requires a matching provider/repository/PR entry in the
session's `metadata.pr_feedback_streams`. The directory-scoped PR credential's authenticated
identity must already be assigned as a reviewer, optional or required. The caller cannot select
another reviewer's identity. The host publishes the summary and inline comments before updating
the vote. Positive votes include `isReapprove: true` so an unchanged approval can apply to a
new PR iteration. The host verifies the provider's returned identity and vote, and includes
`reviewer_id` and `vote` in the response and audit. Use the same `idempotency_key` only for retries of the same
submission. If voting fails after comments were published, inspect the PR before retrying to
avoid duplicate comments. Existing tool grants, argument policies, and approval requirements
still apply. GitHub retains its existing `COMMENT`, `APPROVE`, and `REQUEST_CHANGES` events.

Maintain existing comments with `delete_pr_comment(provider, repo, pr_id, comment_id, ...)`
and `resolve_pr_comment(provider, repo, pr_id, ...)`. Both require their own explicit
`allowed_tools` grant and use the session directory's host credentials. Normal directory
denials, tool argument rules, configured approvals, and tool-call audit apply.

| Operation | Azure DevOps | GitHub |
| --- | --- | --- |
| Delete one comment | Supply positive numeric `thread_id` and `comment_id`; optional `feedback_kind=thread_comment`. | Supply positive numeric `comment_id` and `feedback_kind=issue_comment` for conversation comments or `review_comment` for inline review comments. |
| Resolve a comment thread | Supply positive numeric `thread_id`; sets the entire thread to `fixed`. | Supply a positive numeric inline review `comment_id`, or a GraphQL review-thread `thread_id`, but not both. |

Use `list_all_feedback` to obtain the comment targets. GitHub feedback's `thread_id` is a
review ID, so use its `comment_id` when resolving; conversation comments and review summaries
do not have resolvable review threads. GitHub checks that the comment or thread belongs to the
specified PR before mutation. Review-thread lookup follows both thread and comment pagination,
with a limit of 100 GraphQL requests per invocation. Already resolved GitHub threads return
`resolved=true` and `already_resolved=true` without another mutation. Resolution affects all
replies in the selected thread. Failed or unconfirmed provider responses fail the tool call.

Azure DevOps PR operations use `azure_devops_pr_pat_env` and need `vso.code_write` or
`vso.threads_full`; work-item comment deletion uses `azure_devops_pat_env`. GitHub uses
`github_token_env` and the provider's comment-write and thread-resolution permissions.
See the [Azure DevOps PR comment deletion](https://learn.microsoft.com/en-us/rest/api/azure/devops/git/pull-request-thread-comments/delete?view=azure-devops-rest-7.1),
[Azure DevOps thread update](https://learn.microsoft.com/en-us/rest/api/azure/devops/git/pull-request-threads/update?view=azure-devops-rest-7.1),
and [GitHub review-thread resolution](https://docs.github.com/en/graphql/reference/pulls#resolvereviewthread)
API references.

The review-request inbox is persisted in `runtime/review-request-store.json`. Records transition
through `pending`, `leased`, `dispatched`, `completed`, `failed`, or `cancelled`.
For Azure DevOps PRs, these publish tools can include staged `artifact_ids`; the gateway uploads
them as PR attachments and appends Markdown image/link references to the posted text.
They also accept `mentions` on top-level comments, inline comments, review summaries, batched
review comments, and feedback-thread replies. Mention refs use the same ADO identity ID,
descriptor, email/UPN, or display/name lookup behavior as `post_ado_backlog_comment`; the PAT must
be able to read ADO identities, equivalent to Azure DevOps OAuth scope `vso.identity`.

To attach files directly to a PR description, stage each file under `/home/agent` with
`communication_stage_workspace_file` and pass the returned IDs as `artifact_ids` to
`update_pull_request`. Omit `description` to append the links to the current text, or supply the
complete replacement description. An empty supplied description leaves only the new links.
The response includes `uploaded_attachments` with the uploaded filenames and download URLs.
The files must belong to the caller session and pass its outbound file-send, MIME, extension,
and size policy. Supply `request_id` for the exact update payload when file-send approval is
required. Normal tool grants and argument restrictions still apply, including any requirement
to supply the complete description. The `expected_statuses` check runs before file upload.
When `description` is omitted, its text is refreshed after file uploads before the links
are appended. That refresh and the final save are separate provider requests.
Upload and description save are separate provider requests: an upload failure stops the update;
a save failure raises an error and may leave uploaded files attached to the PR.

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

### Team review repository snapshots

For a Team project configured with managed Git, every newly created review is
bound to an immutable server-owned snapshot before its reviewer is prompted.
The source is the deliverable candidate when available, the producing task's
authoritative Git base for planning or artifact-only deliverables, or the
release candidate for release reviews. The runtime retains a ref under
`refs/evander/team-reviews/` and checks out the recorded commit detached; a
review never falls back to the persistent reviewer checkout's `work` branch.

If the snapshot is unavailable, points at a different managed repository, its
retained ref conflicts, the commit cannot be materialized, or reviews sharing
one repository name different commits, preparation blocks the affected review
before prompt delivery. Inspect the Team review blocker and managed-Git record,
then correct the upstream provenance and create a retry or replacement review.
Existing review records from before this feature have no snapshot and are
intentionally not reinterpreted; recover them by creating a new retry or
replacement review after deployment. Retained Team review refs are not cleaned
up automatically because Team data currently has no physical deletion lifecycle.

### Guest repo preparation

After the VM is created and the repo is cloned, prepare the checkout explicitly instead of
reviewing whatever the default branch clone happens to contain:

```bash
./scripts/guest/setup_review_repo.sh \
  --repo-path /home/agent/<repo-name> \
  --base-ref refs/heads/main \
  --head-ref refs/evander/review/topic
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

## Host-Side NuGet Access

- `evander-nuget-relay` proxies `nuget.org` and private Azure Artifacts feeds.
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
- Directory-scoped `ado_worker` config can provide transient `ado_repo_source` registration input
  when workflow packs bind it into `effect:issue:upsert-ado-proxy.repo_source`. The effect stores
  and binds only the resolved `managed_repo_id` on the proxy issue. Prefer passing an already
  registered `managed_repo_id` when it is known.
- Preferred configuration now lives in directory domain `ado_worker` (`ado-worker.json`) so values
  inherit through directory ancestry and appear in the Admin GUI automatically.
- Global worker settings now live in `config/ado-worker/config.json` and are edited from the
  Admin GUI's Workers tab when the `ado` worker is selected.
- The ADO worker `directories` list is exact-match. Add each child directory that should be
  polled; a parent directory no longer implicitly observes sessions in its descendants.
- ADO worker cadence and directory selection are accepted only from
  `config/ado-worker/config.json`; provider credentials and project settings come from canonical
  host/directory configuration.

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
    "tags_any": ["evander"],
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
    "labels_any": ["evander"],
    "comment_contains": "@WorkflowBot",
    "top": 50
  }
}
```

Failure recovery:

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

- The scaffold includes a singleton host trigger worker, launched interactively as
  `evander-cli trigger-orchestrator` or persistently as
  `evander-trigger-orchestrator.service`, that polls configured triggers and persists trigger
  events to a durable queue.
- Global worker config lives at `config/trigger-orchestrator/config.json`.
- For orchestration-only production deployments, start from `config/trigger-orchestrator/orchestration-core.config.json.example` (uses `runtime/orchestration-core` + `profiles/core-minimal`).
- Runtime defaults (when config fields are null/omitted):
  - orchestration database: `runtime/orchestration.db` (queue, executions, graph snapshots, and workflow events)
  - cursor: `runtime/trigger-orchestrator-cursors.json`
  - lock: `runtime/trigger-orchestrator.lock`
  - wake socket: `runtime/trigger-orchestrator.wake.sock`
- The trigger and effect workers remain separate processes, but host-owned ingress sends best-effort
  wake signals for newly queued work over derived Unix sockets
  (`runtime/trigger-orchestrator.wake.sock` and `runtime/effect-orchestrator.wake.sock`). Each
  completed in-process effect or query future also wakes the effect worker so its next cycle can
  finalize durable execution state and dispatch downstream work promptly. Internal-event publication
  and scheduled-event creation wake the trigger worker on its primary socket after the SQLite commit.
  Durable state remains the source of truth; a missing wake socket falls back to the normal polling
  interval.

Run one cycle for validation/debug:

```bash
evander-cli trigger-orchestrator --runtime-dir runtime --once --log-level DEBUG
```

Run continuously under systemd:

```bash
sudo systemctl start evander-trigger-orchestrator.service
sudo systemctl status evander-trigger-orchestrator.service
sudo journalctl -u evander-trigger-orchestrator.service -f
```

### Managed website authentication

Use [managed website sessions](managed-website-sessions.md) for an assistant that
needs repeatable website login. In Admin Web's Website logins configuration, create
a directory profile, enter encrypted host credentials, and run **Test saved login**.
For disposable environments, a host administrator can authorize a reusable source
profile and destination patterns with `EVANDER_MANAGED_WEBSITE_PROFILE_TEMPLATES`.
Use `effect:website-session:ensure-profile` before agent creation, then bind its
`profile_id` to the website target. Generated profiles have independent encrypted
credentials; remove them with their environments. See
[workflow profile provisioning](managed-website-sessions.md#create-profiles-for-disposable-environments).
For preconfigured environments, configure and test a separate exact-origin JSON
profile for each environment. Use `query:website-session:resolve-profile` with the
environment's website and API origins, then a `gate:validate` readiness check before
updating targets and enqueueing work. Only the `valid` branch should dispatch an agent;
retain the safe query error code and validation failures for operator diagnosis.
Bind the resolved profile ID to the website target and grant the API origin separately.
Select the profile in the assistant's HTTP targets using **Managed website login**. Reconcile
Playwright bootstrap files for existing guests so they receive
`createManagedWebsiteContext`. Newly provisioned Playwright sessions get it automatically.
Profile, grant, and UI-stored credential changes are read on each request. Legacy
environment-backed credentials require a host-controlled scoped JSON grant in
`EVANDER_MANAGED_WEBSITE_CREDENTIAL_ENV_ALLOWLIST`. Copy
`config/http-target-relay.env.example` to the ignored `config/http-target-relay.env`, add grants
and referenced values there or in a shared service-manager secret source, then restart both
`evander-admin-web.service` and `evander-http-target-relay.service`. Each grant must bind the
directory subtree, profile ID, request fields, browser origin, and exact login URL. Verify a new
managed browser context can open the intended page and produce an artifact without printing
authentication state.

Renewal runs on the next context request when state is near expiry, including after
an idle period longer than the site's lifetime. Existing open contexts retain the
site's expiry. No scheduled job or additional firewall rule is required.

### Teams and Discord ingest workers

- `trigger:teams:new-message` and `trigger:teams:direct-message` events are produced by a
  host-side Teams ingest worker that polls Microsoft Graph on the host and writes into the Teams
  message store consumed by trigger orchestration.
- The ingest worker uses the same delegated Teams auth settings described above.
- Optional Teams Graph change notifications use the loopback-only
  `evander-teams-notifications.service`. The callback durably queues deliveries and its independent
  background processor performs exact-message retrieval without waiting for serial fallback polls.
  The ingest worker is still signalled after a new queue row commits, owns subscription
  reconciliation, and retains restart-safe queue draining. A missing or failed wake signal does not
  affect the callback acknowledgement. Polling remains the automatic per-target fallback when
  notification mode is disabled,
  creation or renewal fails, a lifecycle event marks a subscription unhealthy, or the subscription
  expires.
- Graph retry cooldowns are stored per logical endpoint/target. Inspect the Teams inbox
  `poll_health` diagnostics before changing service-wide polling; an unhealthy channel, chat,
  discovery request, or user lookup must not back off unrelated targets.
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
- Direct Teams trigger publication is enabled by default through
  `config/teams-ingest/config.json` `publish_trigger_events_directly`. After a new inbox message is
  durable, the ingest worker publishes every matching Teams-backed trigger event to the configured
  orchestration queue and wakes the effect worker once for the cycle. It does not advance trigger
  cursors; the trigger orchestrator still reconciles and acknowledges events and recovers any message
  missed because matching, queue publication, or a wake notification failed.
- To roll back the fast path, set `publish_trigger_events_directly` to `false` and restart
  `evander-teams-ingest.service`. This disables direct publication for polling; verified exact
  notifications still use deterministic queue publication to preserve out-of-order messages, so
  disable change notifications too when every direct publication path must be removed. Inspect the
  Teams ingest cycle log fields `messages_matched`,
  `events_created`, `events_deduplicated`, `publication_failures`, `effect_wake_notified`,
  `trigger_wake_notified`, and `workflow_config_revision` when diagnosing delivery.
- Message workflows own Discord dispatch; scheduled resumes stay with `evander-scheduled-wake`,
  while Azure DevOps PR feedback ingestion stays with `evander-ado-worker`.

Managed Git hook ingestion path:

- Repository registration installs the managed `post-receive` hook internally; it is not an
  operator promotion/publication interface.
- The hook reads `<old> <new> <ref>` updates from stdin, resolves the registry-owned
  `managed_repo_id`, matches configured `trigger:git:push` selectors, and appends deduplicated
  pending records into the orchestrator queue.
- It emits structured metadata only. Upstream credentials, ref promotion, and publication remain
  in the effect worker and managed Git service.
- Run `PYTHONPATH=src python -m pytest -q tests/test_trigger_conformance_suite.py` for full
  trigger contract regression coverage and the generated trigger conformance report.
- Run `PYTHONPATH=src python -m pytest -q tests/test_effect_conformance_suite.py` for the paired
  effect contract regression coverage and the generated effect conformance report.
- See [conformance-reports.md](conformance-reports.md) for the shared Admin Web conformance report
  view, explicit publication commands, and the exact runtime artifact names it reads. Ordinary
  pytest runs keep their reports in a unique test-cache directory.

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
- `evander-codex-egress-proxy.service`
- `evander-didww-sip-bridge.service`
- `evander-nuget-relay.service`
- `evander-npm-audit-facade.service`
- `evander-http-target-relay.service`
- `evander-http-target-browser-proxy.service`
- `evander-ado-worker.service`
- `evander-proxy-issue-sync.service`
- `evander-discord-ingest.service`
- `evander-scheduled-wake.service`
- `evander-managed-git-refresh.service`
- `evander-teams-ingest.service`
- `evander-teams-notifications.service`
- `evander-teams-meeting-ingest.service`
- `evander-effect-orchestrator.service`
- `evander-trigger-orchestrator.service`
- `evander-team-runtime.target` (gateway, Admin Web, trigger orchestrator, and effect
  orchestrator coordinated as one runtime generation)

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
   endpoints. In the App demo workflow, guests authenticate to App interactively or with
   guest-managed bearer tokens/cookies through that relay path.
5. For private PR feedback polling, ensure the service environment exposes `GITHUB_TOKEN`
   and/or `AZURE_DEVOPS_PR_PAT` and ensure the effective `root` provider config sets the correct
   `ado_organization`, `azure_devops_project`, and `azure_devops_pr_pat_env`.
6. Start and inspect services:

```bash
sudo systemctl start evander-firewall.service
sudo systemctl restart evander-team-runtime.target evander-gateway.service evander-admin-web.service evander-trigger-orchestrator.service evander-effect-orchestrator.service
sudo systemctl start evander-openai-relay.service
sudo systemctl start evander-didww-sip-bridge.service
sudo systemctl start evander-nuget-relay.service
sudo systemctl start evander-npm-audit-facade.service
sudo systemctl start evander-http-target-relay.service
sudo systemctl start evander-http-target-browser-proxy.service
sudo systemctl start evander-ado-worker.service
sudo systemctl start evander-proxy-issue-sync.service
sudo systemctl start evander-discord-ingest.service
sudo systemctl start evander-scheduled-wake.service
sudo systemctl start evander-managed-git-refresh.service
sudo systemctl start evander-teams-ingest.service
sudo systemctl start evander-teams-notifications.service
sudo systemctl start evander-teams-meeting-ingest.service
sudo systemctl status evander-team-runtime.target
sudo systemctl status evander-firewall.service
sudo systemctl status evander-gateway.service
sudo systemctl status evander-openai-relay.service
sudo systemctl status evander-didww-sip-bridge.service
sudo systemctl status evander-nuget-relay.service
sudo systemctl status evander-npm-audit-facade.service
sudo systemctl status evander-http-target-relay.service
sudo systemctl status evander-http-target-browser-proxy.service
sudo systemctl status evander-ado-worker.service
sudo systemctl status evander-proxy-issue-sync.service
sudo systemctl status evander-discord-ingest.service
sudo systemctl status evander-scheduled-wake.service
sudo systemctl status evander-managed-git-refresh.service
sudo systemctl status evander-teams-ingest.service
sudo systemctl status evander-teams-notifications.service
sudo systemctl status evander-teams-meeting-ingest.service
sudo systemctl status evander-effect-orchestrator.service
sudo systemctl status evander-trigger-orchestrator.service
```

The Discord wake and scheduled wake units must run as the same operator user as the
gateway and orchestrator services so they can read the operator user's SSH keys and
write operator-owned wake state. After installing units, verify the rendered service
users:

```bash
systemctl show -p User evander-scheduled-wake.service
systemctl show -p User evander-managed-git-refresh.service
```

All three commands should report the operator account used for deployment, not an empty
root/default user.

## Managed Git Repository Operations

Managed Git is the supported code-transport path for both `libvirt_vm` and
`container_worker_vm`. The control plane owns one bare repository per normalized upstream and
owning directory boundary. Do not create an issue-numbered mirror or persistent trusted checkout.

Managed clone, fetch, pull, `ls-remote`, and archive reads refresh the exact authorized repository
through `evander-managed-git-refresh.service` before serving refs. A broker/upstream failure fails
the read rather than silently serving stale refs. Diagnose with:

```bash
systemctl status evander-managed-git-refresh.service
journalctl -u evander-managed-git-refresh.service --no-pager -n 100
```

`evander-cli git-repos refresh <managed-repo-id>` remains available for operator recovery, but is
not a prerequisite for creating or using agents.

### Register and clone

Create the issue VM first, register its upstream, then use the returned ID for cloning:

```bash
./.venv/bin/evander-cli create-issue-vm 14084 --dotnet --playwright

./.venv/bin/evander-cli git-repos register \
  --directory-path root/acme \
  --repo-source /srv/repos/acme-platform

./.venv/bin/evander-cli clone-repo-into-vm vm-14084 \
  --managed-repo-id mr_0123456789abcdef0123 \
  --directory-path root/acme \
  --session-id issue-14084-session
```

Remote URLs are valid registration sources too:

```bash
./.venv/bin/evander-cli git-repos register \
  --directory-path root/acme \
  --repo-source git@github.com:acme/platform.git
```

Registration is idempotent. Equivalent GitHub/Azure DevOps URL variants normalize to one identity
inside the directory subtree; sibling directory boundaries remain separate. Local registration
reads the checkout and canonical origin but does not fetch into, configure, reset, clean, or
otherwise modify that checkout.

Use `effect:git:ensure-managed-repo` when a workflow receives a transient source during project or
issue import. Persist its `managed_repo_id`; do not persist the source as the managed workflow
contract. Clone effects take the ID and let the control plane resolve the restricted internal URL.

### Persistent agents and multiple repositories

A persistent VM or container agent can hold independent access to several managed repositories.
Granting a second repository creates a second exact-repository key and SSH alias; it does not
replace the first grant.

```bash
evander-cli git-repos grants session-123
evander-cli git-repos grant session-123 \
  --managed-repo-id mr_0123456789abcdef0123 \
  --access-mode read-only
evander-cli git-repos clone-into-agent session-123 \
  --managed-repo-id mr_0123456789abcdef0123 \
  --access-mode read-only \
  --lifetime session
```

Read-only standalone grants are session-persistent, so an existing checkout can continue to
`git fetch` and `git pull`. Use `--lifetime operation` only with `clone-into-agent` or a clone
effect when the credential must be removed immediately after a one-shot clone. Revoke exactly one
repository without deleting checkout files or disturbing other grants:

```bash
evander-cli git-repos revoke-grant session-123 \
  --managed-repo-id mr_0123456789abcdef0123
```

Admin Web exposes the same list, grant, grant-and-clone, and revoke actions on the session detail
page. The workflow equivalents are `effect:agent:grant-repo-access`,
`effect:agent:clone-repo`, and `effect:agent:revoke-repo-access`.

For persistent read-only assistants, prefer
`effect:agent:prepare-reference-repositories` over a serial `gate:for` containing ensure and clone
effects. By default, the batch effect checks independent managed mirrors concurrently without
an explicit upstream probe or refresh. It requires a cached branch commit before creating a missing
worker checkout and otherwise leaves the checkout at its last trusted prepared commit, while still
enforcing source identity, unchanged tracked and staged files, detached HEAD, read-only
configuration, and session access. Unrelated untracked files are preserved and do not block
preparation. Checkout updates reject conflicting untracked paths, including ignored files,
without overwriting local work.
The worker keeps the trusted SHA outside the writable agent home and reports a structured conflict
when a clean checkout has moved. On that mismatch path only, the control plane uses its local
managed mirror to require `trusted commit <= checkout HEAD <= cached configured-branch tip`, where
each relation is Git ancestry. It then explicitly advances worker trust to the checkout commit. A
reference repository refreshed by the assistant therefore remains usable on the next wake even if
the managed mirror has advanced farther along the same branch, while divergent and arbitrary local
commits remain rejected. If both the worker checkout and a cached branch commit are missing,
preparation fails and tells the operator to opt in to refresh. First-time checkout materialization
still fetches through the managed Git read endpoint and is subject to that endpoint's freshness
enforcement; it pins the post-refresh commit actually served by that endpoint rather than a stale
pre-refresh cached SHA. Existing trusted checkouts perform no additional Git operation during
preparation. Mismatch-only ancestry checks and the worker retry run inside the existing concurrent
per-repository tasks, so one slow reconciliation does not serialize the batch. After upgrading an
older worker session, its first preparation seeds missing trusted state only when the retained
`HEAD` matches the cached managed-mirror commit; a mismatch requires one explicit refresh and does
not add recurring refresh latency.

Set `refresh_upstream: true` when the workflow must advance repositories before every prompt. The
effect then probes only each requested upstream branch; an omitted branch uses symbolic `HEAD` so an
upstream default-branch change is detected, unless an operator default override selects an exact
branch. An unchanged advertisement reuses the canonical mirror without fetching. A changed or
missing branch, changed default branch, or uninitialized mirror runs the existing full staged fetch
and atomic ref transaction, including private-ref isolation and configured-default validation. Probe,
authentication, network, malformed advertisement, and timeout failures fail closed instead of
serving a cached ref.

For agents that must respond before choosing a repository, omit the preparation effect from the
startup path and grant `prepare_reference_repository` instead. Constrain its `managed_repo_id`
argument with the session's `tool_argument_policies`; a typical policy uses one `allowlist`
constraint containing the approved managed IDs. The agent calls the tool only when a repository is
needed. The gateway binds the call to the authenticated session, always enables verified upstream
refresh, reuses sufficient session-lifetime access or provisions read-only access, and prepares the
default branch as a detached reference checkout. When that managed repository is already an
ordinary session checkout, the request reuses it in place and returns its current commit without
changing its branch, working tree, remotes, Git configuration, or access grant. A slow or failed
request affects that tool call, not session creation or an earlier chat reply. Repeat calls are the
supported pull/reconcile path for checkouts created as references; do not give the agent upstream
credentials or rely on an unconstrained shell clone.

With refresh enabled, the stored branch verification timestamp only lets requests that began before
the same successful probe reuse it; it is not a freshness TTL and does not mark other branches or
tags as refreshed. Preparation outputs and structured logs include `probe_performed`, `probe_reused`,
`refresh_performed`, and `trust_reconciled`; the default cached path reports the first three as
false, while `trust_reconciled` is true only when mismatch ancestry verification succeeds. A dirty
checkout still fails closed.

Do not migrate a deployed workflow until the control plane and container-worker daemon containing
this effect have passed normal CI/CD and are deployed. After deployment, save the workflow through
the revision-gated Admin Web/API, replace the serial loop with the batch node, retain the existing
agent/file/debounce nodes, and verify one controlled message before removing the old path. Roll back
by restoring the prior workflow-pack revision.

The cloned guest repo receives `AGENTS.md`, the issue task file when applicable, a local coding
branch, and the `git sync-base`, `git push-agent`, and `git agent-pr-note` helpers. Optional
toolchain bootstraps remain separate:

```bash
./.venv/bin/evander-cli bootstrap-python-in-vm vm-14084 \
  --host-checkout /srv/repos/acme-platform \
  --target-dir /home/agent/acme-platform

./.venv/bin/evander-cli bootstrap-frontend-in-vm vm-14084 \
  --host-checkout /srv/repos/acme-platform \
  --install-root /srv/repos/acme-platform/frontend
```

These bootstrap `--host-checkout` arguments seed tools or package artifacts; they are not managed
Git routing fields.

For issue-backed sessions, the task file is `AGENT_TASK.yaml`; agent transcripts remain under
`/var/log/evander/agent-runs/`. Use `evander-cli list-vms`,
`evander-cli logs 14084 --lines 50`, or `./scripts/check_issue_vm.sh 14084` for host inspection.

VM lifecycle scripts and CLI wrappers share the same network-readiness timeout settings:

- `VM_WAIT_IP_TIMEOUT_SECONDS` (default `180`)
- `VM_WAIT_SSH_TIMEOUT_SECONDS` (default `180`)

### Inspect repository state

```bash
evander-cli git-repos list
evander-cli git-repos list --directory-path root/acme
evander-cli git-repos show mr_0123456789abcdef0123 \
  --directory-path root/acme/team-a
evander-cli git-repos refresh mr_0123456789abcdef0123
```

The registry is `runtime/managed-git-repositories.json`. It is lock-protected and atomically
replaced. Records contain repository identity and ownership metadata, the internal host bare path,
host commit identity, timestamps, and bindings. They contain no credentials and no temporary
worktree locations.

Refresh stages canonical branches and tags under an operation namespace and commits all ref
changes in one transaction. It rejects upstream `agents/**`, `review/**`, and `team/**`
branches and never prunes agent, review, Team, notes, or operation refs.

For Azure DevOps, effective directory configuration names two credential
variables. `azure_devops_pr_pat_env` defaults to `AZURE_DEVOPS_PR_PAT`;
`azure_devops_pat_env` defaults to `AZURE_DEVOPS_PAT`. Managed Git uses the first
populated PR PAT and falls back to the populated general PAT. When the
registered refresh source is Azure DevOps SSH but a PAT is selected, the Git
process receives an in-memory rewrite to the canonical HTTPS URL. The stored
source, registry, and operator checkout are unchanged.

A restricted SSH clone, fetch, pull, `ls-remote`, or archive read that fails
through the refresh broker reports a sanitized message plus a stable code and
category. Use the code to triage without copying raw Git output: `broker_busy`,
`upstream_host_key_verification_failed`, `upstream_host_resolution_failed`,
`upstream_authentication_failed`, `upstream_connection_failed`,
`upstream_tls_verification_failed`, `local_ref_update_failed`,
`upstream_ref_policy_rejected`, or the fallback
`upstream_refresh_failed`. Check
`journalctl -u evander-managed-git-refresh.service` with the managed repository
ID; do not add tokens to the URL or enable interactive credential prompts.
An explicit `evander-cli git-repos refresh` calls the managed-Git service
directly and therefore does not use the broker's stable error-code envelope.

### Agent push and queue processing

Each writable guest key is restricted to one managed repository, one
`refs/evander/agents/<session-id>/**` namespace, and the session PR-notes ref. A guest cannot write
ordinary branches, host review/Team namespaces, tags, configuration, or hooks.

Inside the guest:

```bash
git sync-base
git agent-pr-note set -F notes/pr-summary.md
git push-agent
```

The push hook enqueues the old SHA, new SHA, ref, managed ID, session, notes ref, default branch,
and derived review branch. It does not promote or publish synchronously.

A queue-driven workflow must pass the event's exact new SHA into promotion. Promotion atomically
verifies that the agent ref still has that SHA, pins it to a host operation ref, and compare-and-swap
updates the requested review/Team ref. If a repush supersedes the event before pinning, the effect
returns `source_ref_moved`.

The PR-note query requires the managed ID, exact commit SHA, and notes ref. It never falls back to a
parent commit. Canonical push and PR creation require the managed ID, source ref, expected SHA, and
destination branch. Publication fetches the destination into an operation ref, verifies ancestry,
and pushes the exact approved SHA under an explicit lease.

### PR description and credentials

Use directory-scoped `pr_template` configuration for operator-owned PR boilerplate. Treat the
agent note as untrusted markdown. Do not interpolate it into shell commands.

The effect worker obtains GitHub/Azure DevOps credentials from its host environment through the
fixed managed-Git askpass/provider integration. Never place tokens in a URL, workflow pack,
repository config, registry, command argument, or manifest. Interactive Git credential prompts are
disabled.

### Temporary host worktrees and maintenance

Bare-ref promotion and exact note reads require no checkout. Merge/PR preparation creates a
detached worktree only for that operation:

```text
runtime/git-worktrees/<managed_repo_id>/<operation-id>/checkout
```

The worktree is created from a pinned host-only ref with hooks, LFS smudge, submodules, inherited
signing, and interactive prompts disabled. Cleanup force-removes it and compare-and-swap deletes
the operation ref on success, failure, cancellation, and normal signals. Do not run agent-authored
tests in this host worktree; use the VM/container isolation boundary.

Run reconciliation, locked worktree pruning, and repository maintenance explicitly; the refresh
broker does not perform worktree maintenance:

```bash
evander-cli git-repos maintenance
evander-cli git-repos maintenance --managed-repo-id mr_0123456789abcdef0123
```

Startup reconciliation and maintenance remove only lock-free managed worktrees older than the
configured stale threshold (one hour by default). Routine reconciliation is rate-limited to at
most once every five minutes.

Persistent Team task worktrees use a separate durable cleanup candidate and are
reconciled by `effect:team:wake-due-agents`. Set
`run_global_maintenance: true` on a scheduled maintenance wake; the bundled
targeted Chat and requested-agent wakes set it to `false` so cleanup cannot
delay an immediate dispatch. Set
`worktree_cleanup_dry_run: true` for safety audits only. Set it to `false` to
allow an eligible candidate's first pass to attempt physical removal. Leave it
omitted for the staged default: the first pass records a safe dry run, and a
later pass attempts physical removal only after
`worktree_cleanup_retry_seconds`. Omitted therefore does not mean `true`.
Regardless of mode, never bypass a deferred or `operator_action_required` candidate
with `git-repos maintenance` or a recursive delete.

Task-worktree creation is recorded as a deterministic Team preparation attempt
before the worker is called. Container workers persist a host-owned receipt for
the exact request, branch, path, base, and HEAD. If Team state conflicts after
physical creation, the next dispatch may replay only that matching pristine
receipt; a changed request or dirty checkout is retained for operator recovery.
When preparation inputs change, the task resolves, or ownership changes before
the Git context commits, global maintenance moves the obsolete receipted checkout
into the same audited cleanup pipeline as invalidated Git work contexts. Successful
container cleanup retires the matching worker receipt and every committed attempt
covered by the terminal physical-worktree lineage, including attempts bound to an
older dispatch context. The receipt records the prepared HEAD for replay identity;
cleanup independently validates the terminal HEAD, which may contain later task commits.
If the effect process stops after a container worker writes its receipt but before Team
records the prepared HEAD, cleanup resolves that still-pristine HEAD from the matching
worker-owned receipt. Reusing a deterministic attempt ID after successful cleanup resets
its prior terminal candidate as a fresh cleanup lifecycle rather than stranding the new
checkout behind old success evidence.
Retrying an operator-action-required invalidated preparation restores its cleanup-pending
state before safety checks run. During rolling upgrades, cleanup omits a known legacy
receipt ID and retries strict older workers without the new field, so pre-receipt
checkouts remain removable.

Current Team state schema 20 / SQLite schema 69 includes provider-neutral
project directory scopes, durable Git preparation receipts, frozen cleanup
configuration scopes, bounded cleanup disposition evidence, and record-level
persistence. Before upgrading, take a
consistent `state.db` backup, then deploy and restart the target plus its four Team
control-plane services in one `systemctl restart` transaction. Naming the services
explicitly also covers the first installation, when the target was not already active.
`evander-cli status --runtime-dir runtime` treats absent, incomplete, split,
stale, or previous-boot generation registrations as degraded. Systemd invocations of both
ordinary due-agent and governance-manager Team wakes refuse state access until that generation
is current. Do not run old and new Team readers concurrently during a schema migration.

Issue, session, and Team deletion removes bindings and SSH authorization, not the shared
repository. Unpublished refs and repositories are retained until an operator explicitly deletes
them:

```bash
evander-cli git-repos delete mr_0123456789abcdef0123
```

Deletion fails when bindings, authorized sessions, or active/recoverable worktrees remain. There is
no force-delete option.

See [Managed Git Repositories](git-remotes.md) for the complete contract.

## Live Smoke Tests

Use [live-smoke-tests.md](live-smoke-tests.md) to select deployment checks covering:

- host gateway only verification
- full guest-to-host verification
- dedicated Discord test channels
- one Gmail and one Graph mailbox
- read-only email validation
- optional classification, subscription egress, and approved attachment-send checks
- managed Git acceptance across VM and container sessions

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
## Repository index operations

Use **Managed Git → repository → Repository indexing** to configure indexing, inspect
coverage, request/cancel builds and download portable snapshots. The feature is disabled
by default and uses the existing worker and workflow services. Team tasks and reviews
reuse existing coverage without queuing compiler work. Automatic branch builds default
to a one-hour minimum interval and one CPU, with one compiler job across the runtime.
The interval and CPU allowance are configurable; turn off automatic refresh for a
manually scheduled baseline. Search changed and dirty files in the agent checkout.
See
[Repository indexing](repository-indexing.md) for image preparation, App configuration,
the reconciliation workflow, CLI commands, recovery, retention and external queries.

## Installing the optional Teams/Graph provider

For Teams messaging, Graph mail, or Teams meetings, install core and
`packages/teams-graph-addon` into the same host virtualenv before reinstalling service definitions.
Follow the [Teams/Graph upgrade instructions](../packages/teams-graph-addon/README.md). Existing
worker names, directory settings, secrets, and runtime stores are preserved. The systemd installer
skips Teams service installation when the integration is missing or explicitly disabled.

### Optional Azure DevOps integration

Azure DevOps operations and the `evander-ado-worker` / `evander-proxy-issue-sync`
commands now come from [the Azure DevOps package](../packages/azure-devops-addon/README.md).
Install it in the control-plane Python environment before enabling these services.
The systemd installer includes its deployment assets only when installed and enabled.
After disabling it, follow the add-on restart list; both workers exit without polling.
Keep existing runtime configuration and credentials in place for later re-enablement.

## Submitting committed work through MCP

Grant `submit_git_changes` to coding sessions with writable managed-Git access. Agents submit an exact commit SHA and a complete Markdown summary in one call; prepared checkout IDs disambiguate multiple repositories or task worktrees. The tool supports VM and container sessions without changing upstream publication policy.

See [Git submission through MCP](git-remotes.md#submitting-committed-work-through-mcp) for permissions, receipts, retries, runtime storage, and migration. Existing Git helper commands remain supported for compatibility.

## Optional shared browser sessions

The [Browser Sessions Add-on](../packages/browser-sessions-addon/README.md) provides authenticated hosted Chrome previews, development feedback in the same browser, and an invitation-based Chrome extension for user-hosted tabs. Install and configure it separately. Its private worker supervisor and relay do not add default guest firewall ports. Hosted viewers require Admin UI sign-in and directory-scoped browser permissions.

Optional shared-browser voice settings and host credential setup are described in [Browser Sessions voice](../packages/browser-sessions-addon/docs/voice.md).

Optional [multi-step decision execution](../packages/browser-sessions-addon/docs/fast-execution.md) uses the directory’s Jev or OpenAI Decisions provider in the existing shared browser. It is disabled by default; the owning PA supplies exact input values and verifies the result.

## Conversation steering from Teams and shared browsers

`effect:agent:enqueue-prompt` accepts `delivery_mode: steer_if_running`. The default
is `queue`, preserving existing automations. Supply a stable `dispatch_id` for each
source message and a `conversation_key`, or an explicit `resume_thread_id`. Teams
conversation keys should include the exact target and, for channel conversations,
the reply thread. Browser feedback retains the sharing session's creating native
conversation. Replies belong to each message's channel, including when several
channels contribute input to one active turn.

A compatible container-worker Codex subscription runner appends input using
`turn/steer`, pinned to the existing job, thread and turn. A `steered` output means
that input was accepted; the original job alone owns its `started`/`completed`
lifecycle, capacity and usage. This is not confirmation that the agent acted on
that input. Steering waits for the agent's next opportunity to process input and
does not cancel commands. Changed model settings require a subsequent turn.
Other runners and older workers queue messages without interrupting them.

Messages are persisted before dispatch, with separate queue entries and replay
identities. `agent-message-deliveries.db` stores control-plane delivery identities,
hashes and original job pointers; `worker-jobs.db` stores worker acknowledgement
receipts without message bodies. An HTTP timeout is reconciled against the same
worker receipt. An unresolved send never automatically becomes a fresh prompt.
The browser displays uncertain delivery and asks the user to check with the
assistant before using Retry message. This explicitly cancels the old pending
request and submits a new message; it cannot undo input already received. Retain uncertain receipts until resolved;
retain terminal receipts with the corresponding job/audit history. Include the
control-plane delivery database in runtime backups.

Browser status snapshots are additive to message polling: clients update existing
message entries by ID even when their original sequence is behind the cursor.
Closing a sharing session cancels browser requests that have not started. Existing
pause, browser-control leases and origin permissions continue to govern actions.
Teams adds no routine platform delivery notices.

### Enable a personal-assistant workflow

Deploy workers first and verify the loaded deployment revision and health
capability `codex_native_steering: 1`, then deploy the control plane and Browser
Sessions 0.4.0. Existing extension builds can send steering messages; extension
0.4.0 displays their delivery status. No runner image change or new network port
is required for an installed Codex version supporting `turn/steer`.

Prepare a local candidate with
`evander.workflow.conversation_steering.prepare_conversation_steering_pack`.
It takes a workflow pack plus explicit enqueue-node IDs, the entry node before
reasoning/preparation, trusted session/conversation/message bindings and a
per-message reply prompt. It preserves the idle preparation route and adds an
activity query and busy route before it. `query:session-context` exposes the
activity summary only when `include_activity: true` is requested. Keep existing
model-selection commands before this branch. Give ordinary follow-ups steering
instructions that preserve the ongoing task unless the user clearly replaces it.

Validate the candidate and compare diagnostics with its baseline. Save through
`WorkflowConfigAdminService.save_workflow_editor_document(expected_revision=...)`,
then reread the saved revision and confirm orchestrator reload. Never write the
live pack file directly. Keep operator-specific packs, bindings and evidence
outside the repository. Initially opt in personal assistants and browser chats;
other Teams workflows retain queueing. Roll back new traffic by restoring their
`delivery_mode` to `queue`, while retaining receipt reconciliation for sends
already in progress. Explicit Admin Web operator steering remains a separate
control with its existing interruption behavior.

### Durable PR ownership grants

Use `effect:session:reconcile-policy` with `policy: {"add_tools": ["complete_pull_request"]}`
for a selected PR agent. This adds to the persisted session manifest atomically and
survives later agent turns, workflow runs, and service restarts. Use `remove_tools`
to revoke it. Run-scoped approval leases remain temporary and are not ownership grants.
Keep comment routing and human/agent-directed checks ahead of the grant. Match an
explicit standalone command such as `!GRANT ownership`; do not grant from quoted
examples, ordinary discussion, classifier inference, or comments from the bot itself.
Granting access does not complete the PR.

The `complete_pull_request` tool supports Azure DevOps and GitHub and requires a PR already
bound in the session's
`pr_feedback_streams`, its full `expected_source_commit`, and `expected_target_branch`.
It respects configured per-call approval and argument policies, rejects changed
source/target refs, drafts, unresolved reviews and blocking provider policies, and
never bypasses branch policy. Azure DevOps preserves the source branch and uses a merge
commit. GitHub uses its asynchronous merge/queue API with `bypass_rules=false`, requires
a host token with Pull requests read and Contents write permissions, and blocks a target
branch belonging to another open PR. The adapter does not delete source branches (repository automation may do so).
`completion_id` and `message`, when present, describe GitHub's asynchronous result.
A `pending` response means the provider has not yet confirmed completion. For GitHub,
pass its returned `completion_id` with the same expected values to poll the existing
job, including a later failure, without starting another attempt. For Azure DevOps,
retry with the same expected values. Only `merged: true` confirms the outcome.
