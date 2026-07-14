# 19. Commands, Identity, Revisions, and the Durable Outbox

> **At a glance.** A Team MCP command is not a loose edit followed by best-effort messaging. The server derives the caller from the connected session, validates its current command scope, mutates the complete Team model, handles selected inbox items, records dispatch activity, enqueues asynchronous intent, and stores an idempotent response inside one SQLite write transaction. External effects happen later through the durable outbox and workflow runtime.

## Two concurrency contracts

The Team Add-on exposes two related but distinct write contracts.

Ordinary administrative domain writes use optimistic state revisions. The client reads revision 41, submits <code>expected_revision=41</code>, and either commits revision 42 or receives a conflict because someone else already advanced the state. Append-only Chat is deliberately outside this general edit contract and validates its current conversation instead.

Agent MCP commands use a runtime identity and dispatch contract. The caller supplies the business arguments and, for replay-sensitive operations, a <code>request_id</code>. It does not get to claim “I am Imani on dispatch X.” The gateway connection and session determine that.

Both paths end in the same validated SQLite state, but they solve different problems:

| Concern | Admin mutation | Team command |
|---|---|---|
| Human or automation identity | Admin authentication or workflow context | Ambient MCP session plus resolved Team scope |
| Concurrency | <code>expected_revision</code> | Serialized transaction plus active dispatch/lease |
| Replay | Client resolves revision conflicts | <code>request_id</code> idempotency record |
| Async work | Route or service may enqueue it | Durable outbox intent in the command transaction |
| Inbox handling | Separate revision-checked route or edit | Optional atomic <code>handled_inbox_item_ids</code> |

## Identity is ambient

For an ordinary persistent wake, the command executor resolves:

- the ambient core session;
- the registered persistent Team agent;
- Team, role, project, and reporting scope;
- the current active Team dispatch and unexpired lease;
- active stand-downs and agent status.

An old explicit <code>session_id</code> argument may be accepted for compatibility, but it must agree with the connected session. It cannot switch identity.

Governance commands use a governance-group scope and validate that the caller manages that group and, when required, holds its current lease. Ephemeral commands use the assignment/session scope; an ephemeral worker does not borrow its parent's persistent dispatch. Its narrow tool surface is described in Chapter 23.

An inactive, teamless, stood-down, mismatched, or unleased persistent agent cannot mutate normal Team state. Tool hiding improves the prompt, but server authorization remains decisive even if a client manually invokes a hidden name.

## The command transaction

Conceptually, a successful command follows this order:

1. Normalize command context, payload, and inbox IDs.
2. Begin a serialized SQLite write transaction.
3. Read and validate the complete current <code>TeamState</code>.
4. Resolve the ambient caller and command scope.
5. Check for an idempotent replay.
6. Validate every requested inbox item and the command-specific authorization.
7. Apply the domain mutation in memory.
8. Mark the caller-owned pending inbox items handled.
9. Advance the Team revision and validate the complete next state.
10. Insert or reuse durable outbox records.
11. Record successful dispatch activity and its progress classification.
12. Store the idempotency response, including created domain and event IDs.
13. Commit.

If validation fails after identity can be resolved, the executor records a failure activity and returns a structured correction response without applying the requested domain mutation or advancing Team state. If the transaction itself fails, SQLite rolls it back.

This design gives Northstar a useful guarantee. If Imani submits the telemetry retry deliverable while marking Priya's status request handled, a committed response means both Team facts are committed together. It cannot acknowledge the request while silently losing the deliverable.

![Atomic Team command and durable outbox flow](../../assets/evander-user-manual/diagrams/12-team-command-outbox.svg)

<p class="figure-caption">Figure 19.1 — State, inbox handling, activity, replay evidence, and outbox intent commit together.</p>

## Idempotency

Commands that create, append, send, or enqueue asynchronous work should use a stable <code>request_id</code>. The effective replay key contains:

- tool name;
- resolved caller identity;
- scope kind and scope ID;
- request ID.

The executor also stores a digest of the normalized payload.

Reusing the same key with the same payload returns the original stored response. It does not create a second task, message, deliverable, escalation, ephemeral assignment, Git promotion, or outbox row. Reusing the same key with a different payload returns <code>idempotency_conflict</code> and does not mutate state.

Choose request IDs at the semantic operation boundary. For example:

<code>northstar:telemetry-retry:deliverable:v2</code>

Do not generate a fresh random request ID on every network retry, because that converts a transport retry into a new business command. Do not reuse one ID for “submit v2” and “submit v3,” because those are different operations.

Responses must refer to the durable row that actually won deduplication. When an outbox idempotency key already exists, the transaction returns the stored outbox record ID rather than a newly generated but unpersisted candidate ID.

## Atomic inbox handling

Many commands accept <code>handled_inbox_item_ids</code>. Every listed item must:

- exist;
- be pending;
- belong to the resolved caller;
- be legal for that command context.

The executor validates the complete list before changing any item. One bad ID rejects the whole command. This is preferable to a two-call pattern in which an agent handles a directive and then crashes before recording the work it performed.

Use the feature for genuine “read, act, and close” operations. A message with <code>response_requested=false</code> normally needs no acknowledgement, but the recipient should still handle it after reading or acting. Chapter 21 explains that reply intent and wake priority are independent.

## Dispatch activity and progress

Every resolved command attempt can produce a durable <code>TeamDispatchActivity</code> with:

- tool and caller identity;
- persistent agent, governance group, or ephemeral assignment;
- dispatch and request IDs;
- correlation and causation IDs;
- success or failure status;
- progress classification;
- related action or audit record IDs.

The dispatch finalizer, not the individual command, closes a persistent lease. It reads this ledger and decides whether substantive progress occurred. Context-only actions and no-op communication can be tracked without falsely resetting the no-progress throttle.

Use one <code>correlation_id</code> to group related actions across a Northstar telemetry-retry dispatch. Use <code>causation_id</code> to identify the directive, inbox item, or earlier action that caused the next one. These fields improve diagnosis; they do not alter authorization.

## The durable outbox

Some accepted commands describe work that cannot safely happen inside the database transaction: publish an internal event, wake an agent, start an ephemeral worker, perform Git promotion, run release recovery, or route an escalation.

The command writes a <code>TeamOutboxRecord</code> instead. Important fields include:

- intent type;
- aggregate type and ID;
- optional unique idempotency key;
- payload and availability time;
- status, attempt count, claim token, and claim expiry;
- last error and timestamps.

Outbox state moves through <code>pending</code>, <code>claimed</code>, <code>completed</code>, and <code>failed</code>. A publisher claims eligible rows with a lease. On success it completes them. On a configured retry it records the error and makes the row pending at a later availability time. A failure recorded without a retry delay remains failed.

The bundled runtime pack calls <code>effect:team:publish-outbox-events</code> on its cron loop and after command-driven dispatch paths. Publication creates the Team internal event that the rest of the workflow graph consumes.

The command response therefore distinguishes durable acceptance from external completion. “Promotion requested” means the request and outbox intent are committed. It does not mean Git finished. “Escalation queued” does not mean the communication provider delivered it.

## Northstar transaction traces

### Create a task

Priya calls <code>team_create_task</code> with a stable request ID. The transaction validates her authority, creates the task, advances the revision, records command activity, and stores the replay response. If the ready-task transition emits a wake event, its outbox row is committed with the task.

### Submit a deliverable

Imani publishes local <code>artifact_paths</code> before the final Team transaction. The command then validates task ownership and Git work context, creates the deliverable, changes the task to <code>waiting_for_review</code>, handles selected inbox items, and queues any notice or wake intent. If artifact publication succeeded but the Team transaction later fails, the stable artifact can be orphaned; replay or deliberate cleanup must reconcile it.

### Request a release

Riley's command freezes the accepted scope and any prerequisite Git promotions, stores the release package, and enqueues the next durable intent. The response says which release, promotion, and outbox records were accepted. The Delivery view and later workflow runs say whether verification and publication actually succeeded.

## Failure patterns

| Response or symptom | Meaning | Operator or caller action |
|---|---|---|
| Identity/session conflict | Supplied compatibility identity disagrees with the connection. | Stop supplying identity fields; use the intended session. |
| No active dispatch or lease mismatch | A persistent agent called outside its leased Team wake. | Diagnose routing and lease state; do not fabricate a dispatch ID. |
| Stand-down error | The caller or an ancestor has an active hold. | Resolve the hold through its authorized revocation path. |
| <code>idempotency_conflict</code> | One request ID was reused for different normalized content. | Read the original response and issue a new semantic request ID only for truly new work. |
| Invalid handled inbox item | At least one ID is missing, already handled, or owned by another agent. | Reload the caller inbox and resubmit the atomic command with the correct set. |
| Command accepted but effect absent | The outbox row is still pending, claimed, retrying, or failed. | Inspect the exact outbox record, publisher workflow run, and downstream event route. |
| Duplicate external activity despite one command | A downstream effect lacks or violated its own idempotency/lease contract. | Preserve evidence and diagnose the effect; do not delete the Team idempotency record. |

## Practical runbook

For an agent command problem:

1. Capture the tool, ambient session, request ID, returned activity ID, outbox IDs, and Team revision.
2. Read the current persistent-agent or assignment mapping and runtime lease.
3. Look up dispatch activity for that dispatch and caller.
4. If replay is involved, inspect the stored idempotency record and payload digest.
5. If asynchronous work is involved, follow the exact outbox record through claim, event publication, workflow run, and effect result.
6. Retry with the same request ID only when repeating the same semantic payload.

For an Admin <code>409</code>, do not look for a command replay. Reload the resource, merge the competing revision, and submit with the new <code>expected_revision</code>.

For database inspection, use a consistent snapshot and current schema. The relevant tables are relational Team-domain tables plus <code>team_dispatch_activity</code>, <code>team_idempotency_records</code>, and <code>team_outbox</code>. Direct edits bypass full-model validation and are unsupported.

## Source trail

- <code>src/evander_team_addon/services/commands/executor.py</code>, <code>context.py</code>, and <code>types.py</code> — ambient identity, command scopes, validation, transaction order, and activity.
- <code>src/evander_team_addon/services/commands/outbox.py</code> and <code>responses.py</code> — event conversion, durable response IDs, replay, and correction payloads.
- <code>src/evander_team_addon/sqlite_store.py</code> — serialized command transaction, idempotency keys, outbox claims, retries, and schema.
- <code>src/evander_team_addon/mcp.py</code> — grouped command/read registration and session-aware call context.
- <code>src/evander_team_addon/providers.py</code> and <code>workflow_packs.py</code> — outbox publication provider and runtime routes.
- <code>tests/test_team_addon_mcp.py</code>, <code>tests/test_team_addon_outbox_runtime.py</code>, and <code>tests/test_team_addon_repository.py</code> — identity, replay, rollback, stored outbox IDs, and publication behavior.
