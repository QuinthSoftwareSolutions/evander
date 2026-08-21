# 8. Workflow Runtime and Orchestration

> **At a glance.** A workflow is Evander's prepared answer to “when this happens, what should we
> check and do next?” It can notice an event, make a decision, look something up, perform an action,
> and continue when that action finishes.
>
> Evander saves the event and each completed step as the work advances. A stopped worker can
> therefore continue from evidence rather than guessing what happened. An operator can follow the
> run as a sequence of named steps; the later administrator reference explains packs, compiled
> graphs, queue records, bindings, revisions, and recovery windows.

## Read a workflow as a prepared route

Most workflow steps have one of four everyday jobs:

| Job in the route | What it does | Technical name |
|---|---|---|
| Notice something | Observes time, another system, a webhook, or an internal event | Trigger |
| Choose the path | Filters, deduplicates, branches, loops deliberately, or joins work | Gate |
| Find information | Reads state without performing the external business change | Query |
| Do something | Creates a session, sends a prompt, updates a system, or publishes code | Effect |

Connections between steps show which result leads to which next step. A query or effect does not
directly call the next box; its completion becomes a saved event. That extra handoff is what lets
the route survive a process restart.

A representative project route might read:

```text
ticket created
  -> keep items for mission-control
  -> ignore a start already handled
  -> look for an active session
  -> continue only when none is active
  -> create an implementation session
  -> clone the managed repository
  -> prompt the implementation work
  -> wait for and pin the Git result
  -> obtain a review decision
  -> pass the release gates
```

This is an example, not a built-in universal process. Operators choose the integrations, checks,
and release rules that fit their organization. The Team Add-on chapters explain the additional
task, review, and governance route around core workflow execution.

![Durable workflow path from trigger observation through gates, queries, and effects](../../assets/evander-user-manual/diagrams/06-workflow-pipeline.svg)

The figure's main message is that each handoff has a saved record. The trigger worker and effect
worker do not have to remain in one uninterrupted in-memory conversation.

<p class="figure-caption">Figure 8.1 — Workers coordinate through durable event and execution records instead of an in-memory call chain.</p>

## Following a run in Admin Web

The workflow editor shows the prepared route. A workflow run shows what happened when one event
traveled through a particular revision of that route. The current editor can change after a run
begins, so keep the run's recorded revision in view when comparing expected and actual behavior.

For one run, an operator usually needs only a small set of landmarks:

- the source item or event and its time;
- the run or trace ID;
- the last step that completed and its output path;
- the first step that is waiting, denied, or failed;
- the session ID, approval ID, or code version linked from that step.

![Admin Web workflow editor showing a synthetic mission-control graph](../../assets/evander-user-manual/screenshots/admin-workflow.png)

The editor is the authoring view. Runtime events and executions remain separate durable records
underneath it, which is why opening or closing the browser does not start or stop the workflow.

For a cross-run question, select the relevant node and expand **Execution history** in its
inspector. Choose a local-time date window (the default is the previous seven days), optionally
limit statuses, and add exact JSON Pointer filters over input or output fields. Up to eight filters
are combined with AND. Equality is type-aware and case-sensitive; numeric comparisons and
presence/absence checks are also available. Array contents are visible in detail but array-element
predicates are not supported. Results show whether the full record is still retained or the
sanitized compact record is being used, along with the retention boundary and backfill status.
The compact tier defaults to 365 days while full execution payloads default to 30 days.

<p class="figure-caption">Figure 8.2 — The editor is the authoring projection over a revisioned graph; runtime events and executions remain durable records underneath. Sanitized Northstar fixture.</p>

## What an operator may notice

| What you see | What it usually means | Safe next step |
|---|---|---|
| The same source item appears again | Publication or acknowledgement may have been retried; stable identities should converge on the existing event | Compare the event/source IDs before treating it as duplicate work |
| Other sources advance but one does not | One trigger or provider can fail without stopping the others | Capture the integration, directory, expected item, and last successful activity |
| An event exists but no step runs | The effect worker, route from the source, or active graph revision needs attention | Record event, run/trace, source step and output, and shown revision |
| A gate appears to have “lost” an item | It may have deliberately filtered, deduplicated, serialized, or sent the item down another output | Inspect the gate result and selected output path |
| A step remains `running` after a restart | Recovery may resubmit it; the external action may already have happened | Do not manually run the action again until an administrator checks its evidence |
| A step succeeded but no later step appears | Completion publication may be pending, or no connection matches its output | Record the execution status, output, and any `completion_pending` state |
| A workflow edit has no effect | Validation may have kept the last-known-good revision active | Compare the run revision with the editor and read validation messages |
| A review loop stops | Its declared cycle, depth limit, event budget, or business condition ended the route | Capture the last gate/output and iteration state |
| A value cannot be passed to the next step | The binding may name missing or unavailable upstream output | Capture the source output and binding error |
| A Team task is open but no agent wakes | The Team-to-core handoff or governance decision may still be pending | Check both the Team task/outbox state and the linked core run |

## Why a completed action may still be waiting

An important recovery state is `completion_pending`. It means a step's provider succeeded, but the
saved completion event that should move the route onward was not published successfully. Evander
retries that publication instead of immediately performing the successful external action again.

This is safer than treating every stalled route as permission to repeat work. There is still an
unavoidable uncertainty if a process dies after an outside system changes but before Evander saves
the local success record. That is why release, Git, communication, and provider operations use
stable identities, leases, or reconciliation where available.

## Troubleshooting a workflow run

Start with Admin Web. Record the selected directory; source item and event ID; run or trace ID;
graph ID and revision; last completed step and output; first waiting or failed step; execution
status; and any linked session, approval, completion event, or Team task ID. Include the exact
visible error and approximate time.

Do not delete a `running` record until someone has established whether its external effect
occurred. Do not force a second effect merely because completion publication is pending. If a new
edit is not active, do not overwrite the workflow file: resolve validation errors or reload and
merge through the editor when another revision or lock is current.

An administrator can use the detailed IDs and status records below to locate the exact durable
handoff. The remainder of this chapter is administrator and workflow-author reference material.

## Administrator reference: node families and identifiers

A workflow graph contains globally unique node IDs from four families:

| Family | Responsibility | Example |
|---|---|---|
| Trigger | Observe time, a provider, webhook, or internal event and publish a source event | `trigger:ticket:created`, `trigger:cron`, `trigger:event` |
| Gate | Decide or reshape flow without being the external business effect | `gate:filter`, `gate:dedupe`, `gate:map`, `gate:for`, `gate:merge` |
| Query | Read data and publish structured outputs | `query:session:list`, `query:ado:work-items` |
| Effect | Change host or external state and publish completion outputs | `effect:agent:create`, `effect:agent:prompt`, `effect:git:promote-agent-branch` |

Edges connect source nodes and output ports to target nodes. A completion is itself an event, so a
query or effect does not call the next node directly.

### Packs, graphs, and intermediate representation

A workflow pack has a `pack_id`, `pack_version`, optional description, graph metadata, and a
versioned intermediate representation (IR). Each graph has one canonical directory path, optional
user IDs, and a list of node IDs. Each IR node wraps a typed node envelope plus bindings and
optional retry/idempotency policy; edges are part of the IR.

`config/trigger-orchestrator/config.json` selects workflow packs and can also contain inline graphs
or IR. During load, Evander:

1. reads global orchestration settings;
2. migrates supported older node/port shapes;
3. composes configured packs and inline IR;
4. appends the hidden built-in system pack;
5. resolves configured workflow transforms;
6. materializes typed provider schemas;
7. validates graph, binding, directory, and communication-target constraints;
8. computes the compiled runtime configuration.

The hidden system pack is runtime-managed and not an editable operator graph. Node IDs and webhook
slugs must be globally unique across active configuration. A graph cannot claim multiple directory
paths.

Checked-in example packs illustrate supported combinations but are not automatically active. Live
operator packs may be ignored by Git and edited through Admin Web. Use its revision and lock
behavior; writing a stale file directly can defeat concurrency protection even if the JSON itself
is valid.

### Compilation and bindings

Compilation turns authoring IR into concrete trigger, gate, query, effect, and edge definitions.
Provider schemas validate node configuration before execution. Graph validation rejects missing
endpoints, illegal trigger inbound edges, accidental cycles, invalid ports, duplicate IDs, and
inconsistent graph membership.

Bindings carry upstream output into downstream configuration. A value such as:

```text
${{node.output}}
```

is resolved from declared upstream output context. Validation uses provider output schemas and
graph ancestry to catch references that are unavailable, misspelled, or not dominant on the path.
Execution stores a binding trace and a hash of the resolved configuration so an operator can see
what value the node actually received.

Bindings are data selection, not arbitrary Python or shell evaluation. An effect that
intentionally executes a command has its own typed provider and policy boundary; a generic binding
cannot become host code.

### Providers and extensions

Node type names resolve through the orchestration provider loader. A profile determines the
built-in provider set, and configuration can add explicit Python modules or enabled entry points.
Add-ons contribute providers through their registration surfaces. Provider factories receive
shared administration/runtime services so they operate on the same sessions, Git registry,
policy, and stores as the CLI and Admin Web.

The graph's directory path is passed into provider construction and effective configuration
resolution. One active graph set can therefore contain projects using different ADO projects,
backends, contacts, or Team state without duplicating a daemon per directory.

Provider initialization failures are isolated and logged where possible; one bad optional
provider should not erase a valid current graph. A node whose provider cannot be constructed
cannot execute successfully, and its failure remains visible in execution evidence.

### The trigger orchestrator

The trigger worker runs as:

```bash
evander-cli trigger-orchestrator --runtime-dir runtime
```

or with `--once` for one diagnostic cycle. A single-instance polling kernel owns its lock and local
wake mechanism. Each cycle:

1. checks workflow and provider configuration for a valid reload;
2. publishes due scheduled internal events;
3. selects enabled, non-system triggers;
4. constructs each provider using the graph's effective directory context;
5. polls for candidate events;
6. validates provider output and publishes each stable event ID, using an additional idempotency
   key when the publisher supplies one;
7. acknowledges the provider item only after durable publication;
8. records cursor state and wakes the effect orchestrator.

Provider initialization and polling failures are logged per trigger so the cycle can continue with
other definitions. A publish failure prevents acknowledgement of that item. Redelivery is expected;
stable idempotency is part of a correct trigger provider.

The default polling interval is five seconds with up to five seconds of configured jitter. A
successful publish lets the loop return promptly rather than sleeping through available work.
New durable scheduled events also notify the trigger worker, and each cycle caps its next sleep at
the earliest pending due time. This keeps deadline delivery precise without busy polling; the
provider sweep rechecks deadlines before sleeping and immediately polls only internal-event
listeners when new events became due. Those events enter the trigger queue in the same cycle
without polling unrelated external providers twice. The normal polling cadence remains the
recovery fallback.

### The durable orchestration store

`OrchestrationCoreConfig` defaults to:

- SQLite backend at `runtime/orchestration.db`;
- 30-day orchestration retention;
- WAL checkpoint mode `truncate`;
- a checkpoint threshold of 64 MiB, interval of 300 seconds, and checkpoint busy timeout of
  1,000 ms;
- maximum graph depth 16;
- maximum 200 events per effect-worker cycle.

The database unifies the trigger-event queue, workflow events, gate and effect/query executions,
and workflow configuration snapshots behind repository interfaces. Provider and worker cursors
remain in repository-backed JSON paths, for example `trigger-cursors-*.json` and
`trigger-orchestrator-cursors.json`. SQLite WAL supports independent trigger, effect, and Admin
readers/writers while the repository applies a busy timeout and transactions.

SQLite at `runtime/orchestration.db` is the only supported orchestration state backend. It stores
the queue, workflow journal, execution records, and workflow configuration snapshots.

### The effect orchestrator

The effect worker runs as:

```bash
evander-effect-orchestrator --runtime-dir runtime
```

It resets stale queue `inflight` markers at startup, reloads the current graph, retries
completion-pending publication, claims queued events, and routes them along matching source
node/port edges. Gates execute through registered gate providers; queries and effects run through a
bounded executor so short independent operations can overlap.

For every dispatched node, the worker records an idempotency key of:

```text
<event-id>::<node-id>
```

The execution record includes execution and idempotency IDs, source event/trace/node/type, status
and timestamps, resolved configuration and hash, input, outputs, published-completion state, error,
binding trace, and configuration revision.

If a non-running execution already exists for that key, the node is not run again. If the record
is still `running` after a process restart but no in-process future exists, the worker resubmits the
provider using the stored resolved configuration. Evander therefore does **not** promise
exactly-once external side effects. Providers that call external systems need a stable idempotency
key or reconciliation behavior of their own.

### Completion events and recovery

When a query, effect, or gate finishes, the worker builds a deterministic completion event and
publishes it back to the queue. Downstream edges select its output port. The original execution is
not considered fully propagated until completion publication is recorded.

If the provider succeeds but queue publication fails, the record becomes `completion_pending` with
the intended outcome. Later cycles retry publication rather than rerunning the successful provider.

There is still an unavoidable crash window around external systems without idempotency: the
process can die after the remote side effect but before the local success record is durable.
Provider design and reconciliation must account for it. ADO work-item updates, Git promotion,
communication sends, and release operations should use provider-specific stable identities and
leases.

### Cycles, depth, and event budgets

Graphs are acyclic by default. A feedback or iteration edge must set `allow_cycle: true`;
accidental cycles and self edges are rejected. Deliberate cycles remain bounded by
`max_graph_depth`, and each worker cycle claims only `max_events_per_cycle` records.

A review-change request may use a declared cycle to return to implementation. The edge declares
intent; dedupe, iteration state, task state, and depth limits prevent an unbounded hot loop. An
explicit cycle is permission to route again, not proof that the business condition will eventually
terminate.

### Configuration revisions and hot reload

Both workers fingerprint the global configuration, referenced pack files, and
communication-target SQLite files, including WAL/SHM state. When the fingerprint changes, the
reloader parses, composes, validates, and compiles a candidate.

If successful, the new current configuration and provider revision take effect. Workflow snapshots
and node records retain revision context so existing evidence remains intelligible. If invalid,
the worker logs the exception and keeps the last-known-good configuration. A bad edit does not
replace a working graph with an empty one.

Effect-worker store-binding changes are treated conservatively: switching the live queue/execution
store is deferred until old queued/inflight work drains. Provider-loader changes rebuild provider
factories. Administrators should still schedule major graph or store migrations deliberately rather
than relying on opportunistic hot reload.

### Team Add-on on the same runtime

The Team Add-on ships a workflow pack with core trigger/gate/effect/query shapes such as Team
outbox publication, due-agent wake, governance-manager wake, ephemeral agent lifecycle, Git
promotion/consolidation, release recording, and session-routing queries.

Team state remains add-on-owned, but execution remains core-owned:

- Team decides that an implementation agent is due to work and emits or publishes a durable
  intent.
- Core workflow creates or resumes the session and executes the prompt.
- Core completion becomes a Team-visible result or outbox event.
- Team governance decides whether review or release work should happen next.

This seam matters during diagnosis. A Team task can exist without a core wake having been
published; a core workflow run can be healthy while Team governance intentionally withholds the
next dispatch.

## Administrator failure reference

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

## Administrator runbook

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

For one run, collect source event ID, trace ID, graph ID, configuration revision, source node/port,
target node, node idempotency key, execution status, completion event ID, and workflow run/session
IDs. In Admin Web, compare the graph revision shown for the run with the current editor revision.

Do not delete a `running` record until you know whether its external effect occurred. Do not force
a second effect merely because completion publication is pending. Prefer provider reconciliation,
supported retry/force-complete controls for the specific node type, or recovery of queue
publication.

Before publishing a workflow edit, run validation and resolve every error. If another operator
holds the workflow lock or the revision changed, reload and merge through Admin Web instead of
overwriting the pack.

## Source trail

- `src/evander/config.py` — orchestration defaults, paths, validation, limits, and provider-loader
  configuration.
- `src/evander/workflow/packs.py`, `compiler.py`, `validation.py`, `binding.py`, and
  `runtime_config.py` — pack composition, compilation, graph/binding checks, and last-known-good
  reload.
- `src/evander/daemons/trigger_orchestrator.py` and `src/evander/orchestration/kernels.py` —
  trigger polling, single-instance loop, publication, acknowledgement, and wakeup.
- `src/evander/daemons/effect_orchestrator.py` — graph routing, node execution, idempotency,
  completion-pending recovery, cycles, and revision evidence.
- `src/evander/triggers/queue.py` and `src/evander/repositories/orchestration_db.py` — JSON/SQLite
  queue and shared orchestration persistence.
- `src/evander/orchestration/adapters/provider_factories.py` and
  `src/evander/workflow/plugins.py` — provider construction and extension loading.
- `tests/test_workflow_packs.py`, `tests/test_workflow_compiler.py`,
  `tests/test_workflow_validation.py`, `tests/test_trigger_orchestrator.py`,
  `tests/test_effect_orchestrator.py`, and `tests/test_workflow_orchestrator_live_reload.py` —
  workflow contract and recovery tests.
