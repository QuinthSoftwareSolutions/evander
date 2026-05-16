# Architecture Notes

## Components

- Host orchestrator: creates session manifests, launches VMs, and destroys them at the end of a run.
- MCP gateway: the only host service reachable from guests.
- OpenAI relay: a separate host service that exposes an OpenAI-compatible `/v1` endpoint to guests while keeping the real API key on the host.
- NuGet relay: a separate host service that exposes guest-reachable feed endpoints for nuget.org and private Azure Artifacts while keeping private feed credentials on the host.
- Local bare Git remote: a host-side code transfer path that lets guests push commits back over the private network.
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

Use `src/agent_sandbox/*.py` when the main job is implementing product behavior rather than
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
  - Lives primarily in `src/agent_sandbox/workflow/engine/`, `src/agent_sandbox/orchestration/`, and `src/agent_sandbox/workflow/runtime_config.py`.
  - Uses `profiles/core-minimal` to avoid loading domain provider packages by default.
- **Domain packs (opt-in convenience layer)**
  - Own provider implementations and domain-specific default handlers used by built-in workflows.
  - Lives under provider modules such as `src/agent_sandbox/providers/` and orchestration handler helpers in `src/agent_sandbox/orchestration/providers/`.
  - Enabled by selecting `profiles/agent-default` or by adding explicit plugin modules.

### Ownership boundary rules

1. Core runtime modules must stay provider-neutral and should not require domain package imports when running with `profiles/core-minimal`.
2. Domain behavior belongs in provider plugins/handler packs that can be layered onto core at config time.
3. `agent-default` remains the convenience default for broad compatibility, but orchestration-only deployments should use the dedicated orchestration-core config template (`config/trigger-orchestrator/orchestration-core.config.json.example`).

## Host-Driven Feedback Heartbeat (Design)

This model keeps feedback polling on the host while preserving host-side policy and secret
boundaries.

### Poll cadence and latency target

- Recommended steady-state cadence: every **2-5 minutes** per active session.
- Expected operator-to-guest feedback visibility: typically within one poll interval
  (**~2-5 minutes**), plus MCP round-trip time (normally seconds).
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

- Scheduled wake requests are persisted in host runtime state (`wake-requests-store.json`) as request + dispatch history records.
- A schedule can be either:
  - one-time (`resume_at` UTC timestamp), or
  - recurring (`cron` expression).
- Due schedules are dispatched by the host scheduled-wake loop, not by guests directly.
- Prompt composition for scheduled wakes is:
  - `wake_scheduled_prefix_text + requested_prompt`.
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

- `agent-sandbox-trigger-orchestrator` owns:
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
8. `gate:merge` nodes behave like a Reactive Extensions `merge`: each inbound event is forwarded
   independently as it arrives, while the gate records the latest payload seen from every upstream
   source for debugging and conflict inspection.
9. Effects consume the post-gate payload and still resolve `${{node.output_name}}` bindings against the
   upstream event payload.
10. On success, the effect publishes a completion event whose `trigger_id` is the effect id so
   downstream edges can continue the chain.

Safety/idempotency guarantees:

- Each event/node pair uses a stable idempotency key (`<event_id>::<node_id>`), so retries do not
  re-run completed work.
- Gate and effect execution records persist state plus resolved config and output snapshots.
- Immediate cycles are rejected or skipped as `cycle_detected`, and deep chains are bounded by a
  max graph depth guard.
- Filtered or failed nodes do not emit completion events, so downstream edges only activate after
  successful upstream execution.

Gate execution state is intentionally explicit:

- `succeeded`: the gate emitted a downstream event.
- `dropped`: a filter gate suppressed the event.
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
inside the pack (`id`, `name`, `description`, `directory_paths`, `user_ids`, `node_ids`) so the
editor can select, filter, and label that workflow without treating the whole pack as one graph.
`user_ids` identify associated users for ownership and audit reporting; they do not hide graphs from
control-plane operators.

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

## Planned Next Integrations

- Discord integration now uses a provider-backed bot API adapter (`discord_backend: bot_api`) while
  preserving the same MCP tool contract.
- Email integration now uses provider-backed Gmail and Microsoft Graph adapters, with the JSON
  store retained only as an optional development fallback while preserving the same MCP tool
  contract.
- Trust boundary: guests can call MCP tools only; host-side gateway is the only component that
  holds Discord credentials, calls Discord HTTPS APIs, and enforces channel/user allowlists before
  commands reach guest automation.
- Azure DevOps backlog creation follows the same boundary: guests call the policy-gated
  `create_ado_backlog_item` MCP tool, while host-side provider config (`azure_devops_organization_url`,
  project defaults, and PAT env indirection) is resolved by directory scope and the PAT remains
  host-only.
- Approval request notifications follow the host-side operator boundary. Directory policy can set
  `approval_notification_target_ids` to named communication targets with the `notify` capability.
  When a new `ApprovalRequest` is created, the host sends one notification per configured target
  for that deterministic request id. The message contains request metadata and Admin Web/CLI review
  instructions, but not the redacted payload. Approval decisions still happen only through the
  existing Admin Web, admin service, or `agent-sandbox-cli approve` paths, so notification targets
  do not grant guests broader Discord, Teams, or email permissions.
- Discord ingestion boundary in `bot_api` mode:
  - Poll targets are the channel IDs already present in the Discord command store
    (`discord_command_store_path`), not all reachable guild channels.
  - The gateway rejects command envelopes unless metadata marks them as
    `source = discord_bot_api` with non-empty `channel_id` and `author_id`.
  - Acceptance is `(session channel allowlist) OR (global operator user allowlist)`.
  - Post destinations remain session-channel-allowlist only.
  - There is no implicit "poll everywhere" or unrestricted history-derived source expansion.
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
