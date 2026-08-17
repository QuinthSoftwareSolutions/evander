# Architecture Notes

## Components


Guest and worker-container networking is default-deny. Reachability is limited to host ports explicitly allowlisted for the deployment, such as SSH/Git (`22`), MCP (`8000`), enabled model/package/HTTP relays and caches (`11434`, `11666`, `11777`, `11881`, `8081`, and `3142`), plus any deliberately configured provider-specific endpoint. MCP is the tool-capability boundary, not the sole host service on that allowlist.
- Host orchestrator: creates session manifests, launches VMs, and destroys them at the end of a run.
- MCP gateway: the MCP capability boundary reachable from guests; it is not the only guest-reachable host service.
- Connector service: directory-scoped named operations implemented by fixed REST, approved-root
  filesystem, or add-on drivers; sessions see only their granted operation schemas and never host
  credentials or arbitrary destinations.
- Host Directory service: dedicated directory-scoped filesystem resources with per-session
  list/import/delete grants, descriptor-relative traversal, artifact-backed imports, and
  recoverable quarantine deletes. Agent and trigger payloads never expose configured roots.
- OpenAI relay: a separate host service that exposes an OpenAI-compatible `/v1` endpoint to guests while keeping the real API key on the host.
- NuGet relay: a separate host service that exposes guest-reachable feed endpoints for nuget.org and private Azure Artifacts while keeping private feed credentials on the host.
- Managed Git service: directory-scoped bare repositories, capability-limited guest intake, pinned ref transactions, publication, and ephemeral worktree ownership.
- Disposable guests: run agent code and call the MCP gateway over the isolated libvirt network.
- Audit store: append-only JSONL log for every privileged tool attempt.

## Why This Shape

- `KVM + libvirt` is the practical default for Ubuntu 24.04.
- Guests have no direct internet path, which makes MCP the explicit capability boundary.
- Guests use the host relay for model inference instead of holding cloud API credentials directly.
- Guests use host relays for package and model access instead of holding upstream credentials directly.
- Git handles code transport; MCP remains the control and policy path.
- Session manifests are the smallest useful policy primitive for v1.
- Approval records let you keep autonomy for drafts and read operations while gating sends.

## Connector Boundary

The gateway's `list_connectors` and `call_connector` tools are a generic semantic capability
surface, not a general HTTP client. Operators fix the endpoint, method, path template, query/body
mapping, result bounds, authentication profile, and approval requirement in directory config.
Session manifests then grant exact connector-operation pairs. The service validates grants and
JSON Schema before resolving credentials or performing I/O.

REST credentials remain in host environment variables. OAuth client-credential tokens are
host-cached and never cross the MCP response boundary. File and binary responses become opaque
communication-artifact handles, preserving the existing separate policy gate for copying bytes
into a guest workspace. Add-ons can contribute protocol-specific drivers while reusing the same
discovery, grants, approvals, artifact, and audit contracts. See [connectors](connectors.md).

## Managed Git Boundary

`build_admin_runtime` assembles one managed-Git domain service for CLI, Admin API, VM/container
clone, workflow, and Team consumers. Its registry at
`runtime/managed-git-repositories.json` maps stable `managed_repo_id` values to host-owned bare
repositories. An owning directory shares a repository with descendants, while sibling directories
are separate trust boundaries.

Canonical refresh, guests, and host workflows own disjoint ref namespaces. Refresh stages ordinary
branches and tags under `refs/evander/refresh/<operation-id>/**` and commits changes in one ref
transaction without pruning agent, review, Team, notes, or operation refs. Guest SSH capabilities
permit writes only to the exact session's `refs/heads/agents/**` namespace and notes ref.

Host workflows operate on pinned commits. Promotion verifies the source ref and creates a host-only
operation ref atomically; review/Team targets use compare-and-swap, and upstream pushes use explicit
leases for the exact approved SHA. A newer agent push cannot change the object being reviewed or
published.

Most managed Git work is bare-ref manipulation. Operations that need files receive a unique,
detached worktree beneath `runtime/git-worktrees/<managed_repo_id>/<operation-id>`, which is removed
on completion and reconciled after abnormal termination. The worktree is operational scratch
space, never the isolation boundary for running agent-authored code.

Upstream credentials stay in the process environment and fixed host credential integration; they
do not appear in URLs, repository config, workflow payloads, logs, manifests, or the registry.
Issue/session deletion removes bindings and SSH authorization but deliberately retains shared
repositories and unpublished refs until explicit operator deletion.

## Unified Issue Abstraction

Issue workflows now use one host-side abstraction (`local-issues.json`) regardless of origin:

- **Local issues** stay local (`source: local_gui`) and are handled entirely in host runtime state.
- **Remote-backed issues** are modeled as local proxy issues (`source: proxy_azure_devops`) with:
  - remote identity and routing data in `metadata.remote`
  - locally cached remote snapshot data in `metadata.proxy_cache`

This keeps Desktop and CLI behavior consistent: listing, selecting, and VM setup always target a
local issue record first, then optionally forward provider operations.

### Forwarding path for proxy issues

When an operator reads a proxy issue, posts a comment, or updates state/fields:

1. Admin service resolves the local issue record (number, session-style ID, or proxy ID alias).
2. The issue service maps it to `(provider, remote_issue_id)` from `metadata.remote`.
3. The provider adapter executes the remote Azure DevOps call.
4. The local proxy cache (`metadata.proxy_cache`) is refreshed from the latest remote payload.

Non-proxy records bypass forwarding and update only local runtime data.

## Why Logic Is Split Between Shell And Python

The split is about operational boundary, not language preference.

- Shell scripts own machine orchestration.
- Python modules own application and policy logic.

### Shell-first responsibilities

Use `scripts/*.sh` when the main job is coordinating host and guest environments by invoking
existing system tools. Typical examples in this repo include:

- libvirt lifecycle work such as `virt-install`, `virsh`, `qemu-img`, and `cloud-localds`
- SSH, `scp`, and filesystem copy or move steps between host and guest
- bootstrap sequencing across multiple host tools and guest commands
- systemd unit installation and other host integration glue
- thin wrappers that collect environment variables and then launch Python entry points

This repo keeps that work in shell because the code is mostly process orchestration. Shell keeps
those flows short, explicit, and easy to run manually while debugging.

### Python-first responsibilities

Use `src/evander/*.py` when the main job is implementing product behavior rather than
invoking the operating system. Typical examples in this repo include:

- MCP gateway behavior and relay request handling
- policy checks, approval gating, and audit decisions
- typed config loading and validation
- persistent state management and worker loops
- structured provider integrations, retries, and error handling

This repo keeps that work in Python because it is easier to make structured logic testable,
maintainable, and less brittle there than in Bash.

### Mixed cases

Some shell scripts embed small Python snippets. That is intentional.

- Keep the overall flow in shell if the script still primarily orchestrates external commands.
- Use a small Python helper inline when shell would be awkward or fragile for structured parsing,
  YAML or JSON manipulation, base64 handling, or path normalization.
- If the embedded Python grows into substantial logic or starts carrying business rules, move that
  logic into a proper Python module or CLI entry point and let shell stay as the orchestration
  layer.

### Practical rule of thumb

When adding new code, ask:

- Is this mostly orchestrating the environment, host tools, or guest setup? Put it in shell.
- Is this mostly implementing long-lived service behavior, policy, state, or structured data
  processing? Put it in Python.

If both apply, keep the boundary narrow: shell should launch and sequence, Python should decide and
validate.

## Orchestration Core vs Domain Pack Layering

The trigger/effect runtime is designed as two layers with explicit ownership boundaries:

- **Orchestration core (production-safe baseline)**
  - Owns queueing, graph traversal, lock/cursor/execution stores, and worker polling.
  - Lives primarily in `src/evander/workflow/engine/`, `src/evander/orchestration/`, and `src/evander/workflow/runtime_config.py`.
  - Uses `profiles/core-minimal` to avoid loading domain provider packages by default.
- **Domain packs (opt-in convenience layer)**
  - Own provider implementations and domain-specific default handlers used by built-in workflows.
  - Lives under provider modules such as `src/evander/providers/` and orchestration handler helpers in `src/evander/orchestration/providers/`.
  - Enabled by selecting `profiles/agent-default` or by adding explicit plugin modules.

### Ownership boundary rules

1. Core runtime modules must stay provider-neutral and should not require domain package imports when running with `profiles/core-minimal`.
2. Domain behavior belongs in provider plugins/handler packs that can be layered onto core at config time.
3. `agent-default` remains the convenience default for broad compatibility, but orchestration-only deployments should use the dedicated orchestration-core config template (`config/trigger-orchestrator/orchestration-core.config.json.example`).

## Optional Add-ons

Add-ons are external, opt-in layers for opinionated product experiences on top
of Evander. Core exposes generic loading, Admin Web mounting, workflow-provider
bridging, and runtime storage namespacing; add-ons own their domain concepts.
For example, a hierarchical agent-team module belongs in an add-on package, not
in core session, workflow, or Admin Web models. See [add-ons](addons.md).

## Host-Driven Feedback Heartbeat (Design)

This model keeps feedback polling on the host while preserving host-side policy and secret
boundaries.

### Poll cadence and latency target

- Azure DevOps PR feedback is polled by `evander-ado-worker`; there is no current host-heartbeat service.
- `config/ado-worker/config.json` controls cadence with `poll_interval_seconds`. The checked-in example uses `5` seconds, while the daemon fallback is `30` seconds.
- Feedback visibility latency is configuration and workload dependent: approximately one polling interval plus provider and request processing time. A 2-5 minute interval is an optional scaling choice, not the runtime default.
- Expected operator-to-guest feedback visibility: typically within one poll interval
- Faster cadences are possible but increase gateway/provider load; slower cadences reduce load but
  increase human feedback latency.

### Required MCP calls

Per PR feedback polling loop, the host worker processes streams allowlisted in active session manifests and updates the shared feedback store.

Guests continue to use MCP tools (`has_pending_feedback`, `list_unread_feedback`, `list_all_feedback`, `post_pr_feedback_reply`) for consumption and reply actions.

This sequence minimizes payload volume when there is no new feedback and supports explicit at-least-once processing semantics.

### State model (unread cursor per session)

- The host keeps provider cursors per feedback stream in host runtime state.
- `list_unread_feedback` returns:
  - ordered feedback items after the provided cursor
  - a `next_cursor` watermark
- Cursor advancement rule:
  - advance only after feedback is successfully persisted/processed
  - optionally coupled with explicit host acknowledgment/update call
- Sessions are isolated: cursor `A` for session `X` is never reused for session `Y`.

### Scheduled wake dispatch model

- Scheduled wake requests are persisted in host runtime state (`wake-requests-store.db`) as request + dispatch history records.
  Existing `wake-requests-store.json` files are first-use migration input and are left
  untouched after import.
- A schedule can be either:
  - one-time (`resume_at` UTC timestamp), or
  - recurring (`cron` expression).
- Due schedules are dispatched by the host scheduled-wake loop, not by guests directly.
- Recurring cron schedules advance to the next occurrence only after the host resume
  dispatch reports `status=dispatched`. Failed, timed-out, or missing-script dispatch
  attempts remain due/retryable and are retained in dispatch history without updating
  the request-level `last_dispatched_at` success timestamp.
- Prompt composition for scheduled wakes is:
  - optional `scheduled_wake_prefix_text` + `requested_prompt`.
- MCP-created schedules are restricted to `resume_mode=same_context`; admin-created schedules may also use `new_context`.

### Failure behavior

- **Provider API unavailable:** keep current cursor, log failure, retry next cadence with bounded backoff/jitter; do not skip forward.
- **Stale/invalid cursor:** host returns cursor error; guest falls back to a safe recovery mode
  (for example reset to latest checkpoint or request replay from a host-defined baseline), then
  resumes normal polling.
- **Partial fetch/processing failure:** treat batch as uncommitted; retain prior cursor and
  re-fetch on next poll so unread entries are not lost.
- **Duplicate delivery tolerance:** guest handlers should be idempotent by feedback ID because
  at-least-once replay can occur during failures.

### Security boundary

- Provider credentials (Discord, email, OpenAI, etc.) remain **host-side only**.
- Guest has access only to MCP tool interfaces over the private VM network.
- The host wake loops exchange only policy-filtered feedback records and cursor metadata, never upstream provider secrets.

## Trigger Orchestrator Ownership Boundary

Trigger ingestion is intentionally split so queue ownership stays centralized while provider-specific
logic remains isolated:

- The trigger worker (`evander-cli trigger-orchestrator`, or
  `evander-trigger-orchestrator.service` under systemd) owns:
  - loading/validating global trigger config (`config/trigger-orchestrator/config.json`)
  - provider polling cadence + single-instance locking
  - durable event queue persistence (`trigger-orchestrator-events.json`)
  - cursor/checkpoint snapshots (`trigger-orchestrator-cursors.json`)
- Provider workers/hooks own:
  - provider-specific fetch/filter semantics (Discord/email/PR/etc.)
  - provider-side cursor semantics
  - review-request inbox leasing/polling semantics for `trigger:review:new-request`
  - git hook emission path for `trigger:git:push` (post-receive -> queue append)

This boundary keeps trigger transport and idempotent persistence in one host-owned queue while
allowing each provider integration to evolve independently.

## Review Request Inbox Boundary

Review requests are modeled as host-owned workflow jobs, not as PR feedback items:

- `request_pr_review` writes normalized review requests into `runtime/review-request-store.json`
- `trigger:review:new-request` leases those requests and emits `review_request` events
- downstream workflow effects create the VM, prepare the repo, prompt the reviewer agent, and
  publish results back to the PR

This is intentionally separate from `runtime/pr-feedback-store.json`:

- PR feedback is provider-originated inbound discussion
- review requests are host-originated workflow intents

Keeping them separate avoids mixing cursor semantics, replay rules, and audit meaning.

## Workflow Graph + Edge Semantics

The workflow runtime now treats `trigger`, `gate`, and `effect` as first-class node kinds. Wiring
is edge-based:

- trigger nodes: `triggers[*].id`
- gate nodes: `gates[*].id`
- effect nodes: `effects[*].id`
- edges: explicit graph wiring records that connect upstream node ids to downstream node ids

Effects no longer carry `upstream_id`. A node's inbound dependencies come only from edges, which
makes fan-out, fan-in, and mixed graphs visible in the config instead of being implied by a single
parent pointer.

Execution model:

1. Trigger orchestrator publishes source events to the shared queue.
2. Gate execution consumes upstream events through declared edges.
3. `gate:filter` nodes either forward or suppress an event.
4. `gate:map` nodes transform payload shape and can rename or derive outputs.
5. `gate:deserialize` nodes parse JSON/YAML strings into object outputs for downstream bindings.
6. `gate:serialize` nodes serialize bindable values into JSON/YAML text outputs.
7. `gate:dedupe` nodes atomically forward the first activation for a resolved key inside its TTL
   window and drop duplicate activations until the key expires.
8. `gate:throttle` nodes atomically pass a configured number of activations per resolved key in a
   fixed window through their `default` port; overflow activations are dropped or sent through
   their `dropped` port. New throttle outbound edges declare one of those ports explicitly;
   legacy unported edges remain compatible with the `default` route.
9. `gate:counter` nodes atomically count each activation for a resolved key in a fixed window and
   forward the original payload with its current `count`.
10. `gate:switch` nodes evaluate ordered cases, forward the first matching branch through the case
   `source_port`, and fall back to `default_port` when present.
11. `gate:merge` nodes behave like a Reactive Extensions `merge`: each inbound event is forwarded
   independently as it arrives, while the gate records the latest payload seen from every upstream
   source for debugging and conflict inspection.

The complete built-in gate catalog is `gate:filter`, `gate:map`, `gate:deserialize`, `gate:serialize`, `gate:dedupe`, `gate:throttle`, `gate:counter`, `gate:switch`, `gate:merge`, `gate:debounce`, `gate:buffer`, `gate:aggregate`, `gate:await-agent`, `gate:for`, and `gate:while`.
12. Effects consume the post-gate payload and still resolve `${{node.output_name}}` bindings against the
    upstream event payload.
13. On success, the effect publishes a completion event whose `trigger_id` is the effect id so
    downstream edges can continue the chain.

Safety/idempotency guarantees:

- Each event/node pair uses a stable idempotency key (`<event_id>::<node_id>`), so retries do not
  re-run completed work.
- Gate and effect execution records persist state plus resolved config and output snapshots.
- Unmarked cycles are rejected or skipped as `cycle_detected`, and deep chains are bounded by a
  max graph depth guard.
- `gate:for` and `gate:while` implement bounded loops. Their loop-back paths must use edges
  explicitly marked `allow_cycle`; that flag is not a general exemption for arbitrary graph cycles.
- Filtered or failed nodes do not emit completion events, so downstream edges only activate after
  successful upstream execution.

Gate execution state is intentionally explicit:

- `succeeded`: the gate emitted a downstream event.
- `dropped`: a filter, dedupe, or switch gate suppressed the event.
- `failed`: evaluation, binding, or merge projection failed.
- `skipped`: the node was bypassed because of a validation or cycle guard.

## Workflow IR and Domain Workflow Packs

Core orchestrator logic now loads a minimal workflow IR and keeps domain workflow definitions in
versioned workflow-pack files under `config/workflow-packs/`.

### Minimal IR schema (`v1`)

Each pack uses this shape:

- `pack_id`: stable identifier.
- `pack_version`: version label (`v1`, etc.).
- `ir.version`: workflow IR schema version.
- `ir.nodes[]`: node envelopes.
  - `node.id`, `node.display_name`, `node.type`
  - `node.config` (or legacy top-level config keys)
  - optional `node.outputs` schema declarations
  - optional IR metadata: `bindings`, `retry`, `idempotency`
- `ir.edges[]`: edge envelopes.
  - `source_id`, `target_id`
  - optional `source_port`, `allow_cycle`

`bindings`, `retry`, and `idempotency` are part of the IR contract for portable workflow
representation. In the current runtime, binding resolution is still driven by node config values
(`\${{...}}` expressions), while retry/idempotency execution guarantees remain enforced by the
orchestrator’s durable execution stores.

### Composition model

`config/trigger-orchestrator/config.json` now selects packs with:

- `workflow_packs`: ordered list of pack file names (relative to `config/workflow-packs/`)
- optional `workflow_ir`: inline IR fragment for local overlays

Each pack can also carry optional `graphs` metadata. A graph entry names one connected node set
inside the pack (`id`, `name`, `description`, `directory_path`, `user_ids`, `node_ids`) so the
editor can select, filter, and label that workflow without treating the whole pack as one graph.
Operator-owned ignored packs may still be read with a legacy one-item `directory_paths` value,
which the loader normalizes to `directory_path` without re-emitting the legacy field. New and
updated graph configuration is canonical, and multi-directory graph ownership is unsupported.
`user_ids` identify associated users for ownership and audit reporting; Admin Web group assignments
and direct workflow grants control graph visibility.

At load time, the orchestrator composes selected pack IR + optional inline IR into executable
runtime arrays (`triggers`, `gates`, `effects`, `queries`, `edges`) and then runs existing graph
validation/compilation.

The workflow editor stores layout separately from runtime IR. For a config backed by exactly one
workflow pack, layout lives beside that pack as `config/workflow-packs/<pack>.layout.json`. Multi-pack
or inline workflows use the composition-level fallback at `config/trigger-orchestrator/layout.json`.
Runtime config, pack, and pack-layout files are operator-local and ignored by Git. Checked-in
examples use `config/trigger-orchestrator/config-example.json` and
`config/workflow-packs/*-example.json`.

### Adding a new domain pack (no core code changes)

1. Create a new operator-local `config/workflow-packs/<name>.v1.json`, or a checked-in
   `config/workflow-packs/<name>.v1-example.json` when you are publishing a starter pack.
2. Define nodes/edges in `ir` using existing trigger/gate/effect/query types.
3. Add the pack file name to `workflow_packs` in `config/trigger-orchestrator/config.json`.
4. Optionally add a focused test that loads the worker config and validates expected wiring.

This keeps domain workflow evolution in configuration artifacts while the orchestrator engine
remains generic.

### Request-scoped agent consultations

Codebase consultation is a generic control-plane primitive rather than a Team agent type. The MCP
gateway authenticates the caller, resolves an exact directory-scoped managed-Git commit, persists
the request, and publishes a durable internal event. The hidden system workflow then materializes
a container session and repository workspace, invokes the configured model through the existing
relay, and always tears the session down after a structured result, failure, cancellation, or
deadline. The caller may wait briefly but does not hold a workflow worker or gateway transaction
for the full model run. Per-profile queues provide admission control, and capacity is released only
after cleanup. This keeps persistent agent identity, inboxes, and Team governance out of a
short-lived read-only query path.

## Current Provider Integrations And Trust Boundaries

- Discord integration now uses a provider-backed bot API adapter (`discord_backend: bot_api`) while
  preserving the same MCP tool contract.
- Email integration now uses provider-backed Gmail and Microsoft Graph adapters, with the JSON
  store retained only as an optional development fallback while preserving the same MCP tool
  contract.
- Trust boundary: guests can call MCP tools only; host-side gateway is the only component that
  holds Discord credentials, calls Discord HTTPS APIs, and enforces channel/user allowlists before
  commands reach guest automation.
- Azure DevOps backlog creation follows the same boundary: guests call the policy-gated
  `create_ado_backlog_item` and `delete_ado_backlog_item` MCP tools, while host-side provider
  config (`ado_organization`, project defaults, and PAT env indirection) is resolved
  by directory scope and the PAT remains host-only. Delete requests stay approval-gated per
  invocation, and `destroy=true` is required for permanent removal.
- Approval request notifications follow the host-side operator boundary. Directory policy can set
  `approval_notification_target_ids` to named communication targets with the `notify` capability,
  and session policy or a live workflow-run approval lease can add more notify-capable targets for
  that approval path. When a new `ApprovalRequest` is created, the host sends one notification per
  resolved target for that deterministic request id. The message contains request metadata and
  Admin Web/CLI review instructions, but not the redacted payload. Approval decisions still happen
  only through the existing Admin Web, admin service, or `evander-cli approve` paths, so
  notification targets do not grant guests broader Discord, Teams, or email permissions.
- Discord ingestion boundary in `bot_api` mode:
  - Poll targets are the channel IDs already present in the Discord command store
    (`discord_command_store_path`), not all reachable guild channels.
  - The gateway rejects command envelopes unless metadata marks them as
    `source = discord_bot_api` with non-empty `channel_id` and `author_id`.
  - Acceptance is `(session channel allowlist) OR (global operator user allowlist)`.
  - Post destinations remain session-channel-allowlist only.
  - There is no implicit "poll everywhere" or unrestricted history-derived source expansion.
  - A Discord communication target may explicitly opt into public child threads. The inbox SQLite
    store then records each child thread's guild, configured parent, type, and archived/locked
    state before messages become visible. Trigger, session, attachment, and outbound policy all
    resolve child authority through that durable binding; a caller-supplied thread ID is never an
    independent grant.
  - Forum posts and public/announcement threads are supported. Private threads and automatic
    unarchive/moderation are outside the boundary.
- Failure/audit model for Discord commands:
  - Policy failures are logged as denied `tool_call` audit events in `audit_log_path`.
  - `discord_get_commands` audits each denied envelope and records aggregate `denied_count`.
  - Transport/API failures from Discord provider calls are returned to callers; no built-in retry
    backoff loop is currently implemented in the adapter.
- Add a separate admin channel for approving requests without granting the guest broader power.


## Observability Markers

Runtime observability now includes explicit metrics/log markers for orchestration control points:

- **Chat session lifecycle markers** from assistant session mapping resolution:
  - `metric_chat_session_mapping_created`
  - `metric_chat_session_mapping_reused`
- **Backend path markers** from agent provisioning:
  - `metric_agent_create_backend_path`
- **Prompt enqueue latency and failure markers** from queued prompt persistence:
  - `metric_prompt_enqueue_success` (includes `latency_ms`)
  - `metric_prompt_enqueue_failed` (includes failure class `reason`)
- **MCP authorization outcome markers** for tool execution decisions:
  - `metric_mcp_tool_decision` with `outcome=allowed|denied|error`

These markers complement audit JSONL events: audit entries remain the policy source of truth, while markers provide low-friction operational telemetry for filtering and alerting.
