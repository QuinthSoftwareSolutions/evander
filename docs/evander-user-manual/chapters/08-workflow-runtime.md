# 8. Workflow Runtime and Orchestration

> **At a glance.** Evander workflows are validated event graphs. Triggers publish durable events; gates shape control flow; queries read data; effects change state; and completion events continue the graph. Two workers split observation from execution and share a SQLite-backed queue/journal by default. Node execution is idempotent at the event-and-node boundary, while external providers must tolerate recovery of a previously running operation. Invalid hot reloads retain the last-known-good graph.

## The four node families

A workflow graph contains globally unique node IDs from four families:

| Family | Responsibility | Example |
|---|---|---|
| Trigger | Observe time, a provider, webhook, or internal event and publish a source event | `trigger:ticket:created`, `trigger:cron`, `trigger:event` |
| Gate | Decide or reshape flow without being the external business effect | `gate:filter`, `gate:dedupe`, `gate:map`, `gate:for`, `gate:merge` |
| Query | Read data and publish structured outputs | `query:session:list`, `query:ado:work-items` |
| Effect | Change host or external state and publish completion outputs | `effect:agent:create`, `effect:agent:prompt`, `effect:git:promote-agent-branch` |

Edges connect source nodes and output ports to target nodes. A completion is itself an event, so a query or effect does not call the next node directly. This preserves queue durability and lets the graph resume after process failure.

Northstar's telemetry-retry flow might be summarized as:

```text
ticket created
  -> filter mission-control
  -> dedupe work-item start
  -> query active sessions
  -> filter none active
  -> create Imani's agent
  -> clone managed repo
  -> prompt implementation
  -> await/pin Git result
  -> Rowan review
  -> Riley publication gates
```

This is a representative composition. The exact providers, gates, and review/release graph are operator-defined, and Team Add-on chapters explain the additional task and governance nodes.

![Durable workflow path from trigger observation through gates, queries, and effects](../../assets/evander-user-manual/diagrams/06-workflow-pipeline.svg)

<p class="figure-caption">Figure 8.1 — Workers coordinate through durable event and execution records instead of an in-memory call chain.</p>

## Packs, graphs, and IR

A workflow pack has a `pack_id`, `pack_version`, optional description, graph metadata, and a versioned intermediate representation. Each graph has one canonical directory path, optional user IDs, and a list of node IDs. Each IR node wraps a typed node envelope plus bindings and optional retry/idempotency policy; edges are part of the IR.

`config/trigger-orchestrator/config.json` selects workflow packs and can also contain inline graphs or IR. During load, Evander:

1. reads global orchestration settings;
2. migrates supported older node/port shapes;
3. composes configured packs and inline IR;
4. appends the hidden built-in system pack;
5. resolves configured workflow transforms;
6. materializes typed provider schemas;
7. validates graph, binding, directory, and communication-target constraints;
8. computes the compiled runtime configuration.

The hidden system pack is runtime-managed and not an editable operator graph. Node IDs and webhook slugs must be globally unique across active configuration. A graph cannot claim multiple directory paths.

Checked-in example packs illustrate supported combinations but are not automatically active. Live operator packs may be ignored by Git and edited through Admin Web. Use its revision and lock behavior; writing a stale file directly can defeat concurrency protection even if the JSON itself is valid.

![Admin Web workflow editor showing the synthetic mission-control telemetry-retry graph](../../assets/evander-user-manual/screenshots/admin-workflow.png)

<p class="figure-caption">Figure 8.2 — The editor is the authoring projection over a revisioned graph; runtime events and executions remain durable records underneath. Sanitized Northstar fixture.</p>

## Compilation and bindings

Compilation turns authoring IR into concrete trigger, gate, query, effect, and edge definitions. Provider schemas validate node configuration before execution. Graph validation rejects missing endpoints, illegal trigger inbound edges, accidental cycles, invalid ports, duplicate IDs, and inconsistent graph membership.

Bindings carry upstream output into downstream configuration. A value such as:

```text
${{node.output}}
```

is resolved from declared upstream output context. Validation uses provider output schemas and graph ancestry to catch references that are unavailable, misspelled, or not dominant on the path. Execution stores a binding trace and a hash of the resolved configuration so an operator can see what value the node actually received.

Bindings are data selection, not arbitrary Python or shell evaluation. An effect that intentionally executes a command has its own typed provider and policy boundary; a generic binding cannot become host code.

## Providers and extensions

Node type names resolve through the orchestration provider loader. A profile determines the built-in provider set, and configuration can add explicit Python modules or enabled entry points. Add-ons contribute providers through their registration surfaces. Provider factories receive shared administration/runtime services so they operate on the same sessions, Git registry, policy, and stores as the CLI and Admin Web.

The graph's directory path is passed into provider construction and effective configuration resolution. A single active graph set can therefore contain projects using different ADO projects, backends, contacts, or Team state without duplicating a daemon per directory.

Provider initialization failures are isolated and logged where possible; one bad optional provider should not erase a valid current graph. A node whose provider cannot be constructed cannot execute successfully, and its failure remains visible in execution evidence.

## The trigger orchestrator

The trigger worker runs as:

```bash
evander-cli trigger-orchestrator --runtime-dir runtime
```

or with `--once` for one diagnostic cycle. A single-instance polling kernel owns its lock and local wake mechanism. Each cycle:

1. checks workflow and provider configuration for a valid reload;
2. publishes due scheduled internal events;
3. selects enabled, non-system triggers;
4. constructs each provider using the graph's effective directory context;
5. polls for candidate events;
6. validates provider output and publishes each stable event ID, using an additional idempotency key when the publisher supplies one;
7. acknowledges the provider item only after durable publication;
8. records cursor state and wakes the effect orchestrator.

Provider initialization and polling failures are logged per trigger so the cycle can continue with other definitions. A publish failure prevents acknowledgement of that item. Redelivery is therefore possible and expected; stable idempotency is part of a correct trigger provider.

The default polling interval is five seconds with up to five seconds of configured jitter. A successful publish lets the loop return promptly rather than sleeping through available work.

## The durable orchestration store

`OrchestrationCoreConfig` defaults to:

- SQLite backend at `runtime/orchestration.db`;
- 30-day orchestration retention;
- WAL checkpoint mode `truncate`;
- a checkpoint threshold of 64 MiB, interval of 300 seconds, and checkpoint busy timeout of 1,000 ms;
- maximum graph depth 16;
- maximum 200 events per effect-worker cycle.

The database unifies the trigger-event queue, workflow events, gate and effect/query executions, and workflow configuration snapshots behind repository interfaces. Provider and worker cursors remain in their repository-backed JSON paths (for example `trigger-cursors-*.json` and `trigger-orchestrator-cursors.json`). SQLite WAL supports independent trigger, effect, and Admin readers/writers while the repository applies a busy timeout and transactions.

The `json` backend remains a rollback/compatibility option with legacy paths for queue, cursors, journals, execution records, and snapshots. It is not the preferred concurrent production backend. `evander-cli migrate-orchestration-store` imports legacy state through a supported migration flow.

## The effect orchestrator

The effect worker runs as:

```bash
evander-effect-orchestrator --runtime-dir runtime
```

It resets stale queue `inflight` markers at startup, reloads the current graph, retries completion-pending publication, claims queued events, and routes them along matching source node/port edges. Gates execute through registered gate providers; queries and effects run through a bounded executor so short independent operations can overlap.

For every dispatched node, the worker records an idempotency key of:

```text
<event-id>::<node-id>
```

The execution record includes execution and idempotency IDs, source event/trace/node/type, status and timestamps, resolved configuration and hash, input, outputs, published-completion state, error, binding trace, and configuration revision.

If a non-running execution already exists for that key, the node is not run again. If the record is still `running` after a process restart but no in-process future exists, the worker resubmits the provider using the stored resolved configuration. That recovery rule means Evander does **not** promise exactly-once external side effects. Providers that call external systems need their own stable idempotency key or reconciliation behavior.

## Completion events and recovery

When a query, effect, or gate finishes, the worker builds a deterministic completion event and publishes it back to the queue. Downstream edges select its output port. The original execution is not considered fully propagated until completion publication is recorded.

If the provider succeeds but queue publication fails, the record becomes `completion_pending` with the intended outcome. Later cycles retry publication rather than rerunning the successful provider. This distinction prevents a transient queue error from turning an already completed external effect into an immediate duplicate.

There is still an unavoidable crash window around external systems that do not support idempotency: the process can die after the remote side effect but before the local success record is durable. Provider design and reconciliation must account for it. ADO work-item updates, Git promotion, communication sends, and release operations should use their provider-specific stable identities and leases.

## Cycles, depth, and event budgets

Graphs are acyclic by default. A feedback or iteration edge must set `allow_cycle: true`; accidental cycles and self edges are rejected. Deliberate cycles are still bounded by `max_graph_depth`, and each worker cycle claims only `max_events_per_cycle` records.

The Northstar graph may use a declared cycle for Rowan's change request to return to Imani's implementation step. The edge declares intent; dedupe, iteration state, task state, and depth limits prevent it from becoming an unbounded hot loop. An explicit cycle is permission to route again, not proof that the business condition will eventually terminate.

## Configuration revisions and hot reload

Both workers fingerprint the global configuration, referenced pack files, and communication-target SQLite files (including WAL/SHM state). When the fingerprint changes, the reloader parses, composes, validates, and compiles a candidate.

If successful, the new current configuration and provider revision take effect. Workflow snapshots and node records retain revision context so existing evidence remains intelligible. If invalid, the worker logs the exception and keeps the last-known-good configuration. A bad edit therefore does not replace a working graph with an empty one.

Effect-worker store-binding changes are treated conservatively: switching the live queue/execution store is deferred until old queued/inflight work drains. Provider-loader changes rebuild provider factories. Operators should still schedule major graph or store migrations deliberately rather than relying on opportunistic hot reload.

## Team Add-on on the same runtime

The Team Add-on ships a workflow pack with core trigger/gate/effect/query shapes such as Team outbox publication, due-agent wake, governance-manager wake, ephemeral agent lifecycle, Git promotion/consolidation, release recording, and session-routing queries.

Team state remains add-on-owned, but execution remains core-owned:

- Team decides that Imani is due to work and emits/publishes a durable intent.
- Core workflow creates or resumes the session and executes the prompt.
- Core completion becomes a Team-visible result/outbox event.
- Team governance decides whether Rowan or Riley should act next.

This seam is important during diagnosis. A Team task can exist without a core wake having been published; a core workflow run can be healthy while Team governance intentionally withholds the next dispatch.

## Failure behavior

| Symptom | Durable state to inspect | Interpretation |
|---|---|---|
| Trigger provider repeatedly returns same item | Queue idempotency and provider cursor/ack | Publication or acknowledgement may be failing; duplicates should converge. |
| Other triggers work, one does not | Per-trigger logs and effective provider config | Provider construction/poll failure is isolated. |
| Event is queued but no node runs | Effect service, source node/port, graph revision | Worker stopped, no matching edge, or graph/source changed. |
| Gate “lost” an item | Gate execution and output port | It may have deliberately filtered, deduped, serialized, or routed it. |
| Node is `running` after restart | Execution record and current inflight map | Recovery may resubmit; check provider idempotency before manual action. |
| Node succeeded but downstream is absent | `completion_pending` or missing matching edge | Retry completion publication; do not rerun the external effect first. |
| New workflow edit has no effect | Worker reload log and current revision | Candidate may be invalid; last-known-good remains active. |
| Cycle stops | Depth/event budget and `allow_cycle` | Safety bound or undeclared cycle prevented further routing. |
| Bind expression fails | Binding trace/error and provider schema | Upstream output is missing, wrong port/path, or not valid on every route. |
| Team task is open but no agent wakes | Team outbox/wake/governance plus core queue | Cross the add-on/core seam and find which durable handoff is missing. |

## Practical runbook

Validate each worker independently with one-cycle commands:

```bash
evander-cli trigger-orchestrator --runtime-dir runtime --once
evander-effect-orchestrator --runtime-dir runtime --once
```

Then inspect service logs without leaving duplicate workers running:

```bash
sudo systemctl status evander-trigger-orchestrator.service
sudo systemctl status evander-effect-orchestrator.service
sudo journalctl -u evander-trigger-orchestrator.service -n 200 --no-pager
sudo journalctl -u evander-effect-orchestrator.service -n 200 --no-pager
```

For one run, collect source event ID, trace ID, graph ID, configuration revision, source node/port, target node, node idempotency key, execution status, completion event ID, and workflow run/session IDs. In Admin Web, compare the graph revision shown for the run with the current editor revision.

Do not delete a `running` record until you know whether its external effect occurred. Do not force a second effect merely because completion publication is pending. Prefer provider reconciliation, supported retry/force-complete controls for the specific node type, or recovery of queue publication.

Before publishing a workflow edit, run validation and resolve every error. If another operator holds the workflow lock or the revision changed, reload and merge through Admin Web instead of overwriting the pack.

## Source trail

- `src/evander/config.py` — orchestration defaults, paths, validation, limits, and provider-loader configuration.
- `src/evander/workflow/packs.py`, `compiler.py`, `validation.py`, `binding.py`, and `runtime_config.py` — pack composition, compilation, graph/binding checks, and last-known-good reload.
- `src/evander/daemons/trigger_orchestrator.py` and `src/evander/orchestration/kernels.py` — trigger polling, single-instance loop, publication, acknowledgement, and wakeup.
- `src/evander/daemons/effect_orchestrator.py` — graph routing, node execution, idempotency, completion-pending recovery, cycles, and revision evidence.
- `src/evander/triggers/queue.py` and `src/evander/repositories/orchestration_db.py` — JSON/SQLite queue and shared orchestration persistence.
- `src/evander/orchestration/adapters/provider_factories.py` and `src/evander/workflow/plugins.py` — provider construction and extension loading.
- `tests/test_workflow_packs.py`, `tests/test_workflow_compiler.py`, `tests/test_workflow_validation.py`, `tests/test_trigger_orchestrator.py`, `tests/test_effect_orchestrator.py`, and `tests/test_workflow_orchestrator_live_reload.py` — workflow contract and recovery tests.
