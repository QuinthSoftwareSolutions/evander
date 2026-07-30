# 19. Commands, Identity, Revisions, and the Durable Outbox

> **At a glance.** When an agent changes Team state, Evander treats the change as one complete, accountable operation rather than a handful of unrelated edits. Either the important records are accepted together or the change fails without leaving a half-finished result. If the same request is safely repeated after a timeout, Evander can return the earlier answer instead of performing the work twice.

## Three promises behind a Team command

The transaction machinery in this chapter exists to keep three operator-visible promises:

1. **No half-finished Team update.** If Imani submits a deliverable and handles the directive that caused it, Team records both changes together or records neither.
2. **A safe retry does not repeat the business action.** If the response is lost in transit, repeating the same request can return the first result rather than create a second task, message, or release request.
3. **Accepted does not mean externally completed.** Team can durably accept “promote this commit” before Git has performed the promotion. The request and its later execution have separate status and evidence.

These promises are usually enough to interpret the UI. The rest of the chapter explains the exact identity, revision, replay, and delivery records behind them.

## Deeper detail: two write contracts

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

An agent does not tell Team who it is by putting a name or session ID in a command. Team starts from the already authenticated Evander connection and the currently leased dispatch. That prevents a caller from impersonating another Team member merely by changing an argument.

For an ordinary persistent wake, the command executor resolves:

- the ambient core session;
- the registered persistent Team agent;
- Team, role, project, and reporting scope;
- the current active Team dispatch and unexpired lease;
- active stand-downs and agent status.

An old explicit <code>session_id</code> argument may be accepted for compatibility, but it must agree with the connected session. It cannot switch identity.

Governance commands use a governance-group scope and validate that the caller manages that group and, when required, holds its current lease. Ephemeral commands use the assignment/session scope; an ephemeral worker does not borrow its parent's persistent dispatch. Its narrow tool surface is described in Chapter 23.

An inactive, teamless, stood-down, mismatched, or unleased persistent agent cannot mutate normal Team state. Tool hiding improves the prompt, but server authorization remains decisive even if a client manually invokes a hidden name.

## Durable dispatch scope

A persistent wake first creates a durable <code>TeamDispatch</code> in
<code>preparing</code>. Its task, blocker, review-request, inbox, project, and
managed-repository ID lists are the exact records selected by the scheduler.
They are immutable authorization scope, not a convenient snapshot that an
agent may widen.

The lists remain source-specific. A blocker that mentions a scoped task is not
automatically in <code>blocker_ids</code>; another agent's task mentioned by a
review is not automatically in <code>task_ids</code>. Before applying a
command, Team confirms that the target is in the corresponding list and is
still assigned, open, and actionable. Work created during a dispatch becomes
eligible for a later dispatch.

Repository preparation happens before the dispatch becomes
<code>leased</code>. Every required task/repository checkout must succeed and
receive a fresh dispatch-bound Git context; otherwise the complete dispatch is
marked <code>failed</code>, partial contexts are finalized, and the runtime
reservation is released. Team never silently drops one failed checkout and
resumes the agent with a smaller scope.

This is why a valid Team command needs both ambient identity and the current
leased dispatch. A session that remembers an older task or Git context still
cannot act on it after its authorization scope has ended.

## One command, one durable result

Suppose Imani has read Priya's request and is ready to submit the telemetry deliverable. A single command can validate Imani's current authority, create the deliverable, mark the selected inbox item handled, record activity, and queue the next asynchronous work. If any required part is invalid, the intended Team change is not partly committed.

> <span class="sidebar-kicker">Technical sidebar</span> **The command transaction, step by step**
>
> Conceptually, a successful command follows this order:
>
> 1. Normalize command context, payload, and inbox IDs.
> 2. Begin a serialized SQLite write transaction.
> 3. Read and validate the complete current <code>TeamState</code>.
> 4. Resolve the ambient caller and command scope.
> 5. Check for an idempotent replay.
> 6. Validate every requested inbox item and the command-specific authorization.
> 7. Apply the domain mutation in memory.
> 8. Mark the caller-owned pending inbox items handled.
> 9. Advance the Team revision and validate the complete next state.
> 10. Insert or reuse durable outbox records.
> 11. Record successful dispatch activity and its progress classification.
> 12. Store the idempotency response, including created domain and event IDs.
> 13. Commit.
>
> If validation fails after identity can be resolved, the executor records a failure activity and returns a structured correction response without applying the requested domain mutation or advancing Team state. If the transaction itself fails, SQLite rolls it back.

This design gives Northstar a useful guarantee. If Imani submits the telemetry retry deliverable while marking Priya's status request handled, a committed response means both Team facts are committed together. It cannot acknowledge the request while silently losing the deliverable.

![Atomic Team command and durable outbox flow](../../assets/evander-user-manual/diagrams/12-team-command-outbox.svg)

Read the figure as one commit boundary: the Team change, inbox handling, activity, replay response, and instruction for later work become durable together.

<p class="figure-caption">Figure 19.1 — State, inbox handling, activity, replay evidence, and outbox intent commit together.</p>

## Human changes requested is one transaction

An authenticated Admin human decision uses a separate HTTP identity boundary,
but its domain cascade follows the same all-or-nothing principle. A
<code>changes_requested</code> decision atomically:

1. stores the immutable decision and deterministic findings;
2. ends the pinned release attempt without queuing canonical publication;
3. creates or reuses one correction <code>TeamCase</code>;
4. creates one coordination-only parent and one exact-text child per finding;
5. derives the affected candidate work and direct owner when that mapping is
   deterministic;
6. links each finding to its child task; and
7. creates or reuses the Team inbox, immediate wake, and work-due consequences.

The idempotency key covers the complete cascade. A retry or racing identical
terminal decision reuses the same decision, case, tasks, notices, and wake
obligations. A failure in any part rolls the transition back; Team never leaves
a recorded human veto without actionable correction work, or correction work
without the veto that authorized it.

The affected-deliverable list is optional. A sole reviewed candidate
deliverable is inferred. With several candidate contributions, the human can
choose named work in the form; an omitted selection routes directly only when
all applicable work has one active owner. Otherwise the distinct finding child
stays with the project lead until
<code>team_route_human_finding_correction</code> assigns its owner.

The system may create this narrowly scoped graph because the authenticated
request and target release already authorize the same-team, same-project
relationship. It does not grant <code>create_root_task</code> to the original
agent. Accepted tasks, deliverables, agent reviews, and the predecessor
candidate remain immutable. Correction worktree preparation verifies the
rejected release ref, commit, and tree, then uses that immutable candidate as
the base regardless of later movement on the main branch.

After every finding child has exactly one current accepted correction
deliverable with required review evidence, Team derives the resolution summary
and evidence from those deliverables and creates one changed successor release
automatically. The coordination parent produces no deliverable and finishes
when that successor exists.

## Idempotency

**Idempotency** means that repeating the same request has the same business effect as making it once. It matters when the caller cannot tell whether a timeout happened before or after Team committed the command.

Commands that create, append, send, or enqueue asynchronous work should use a stable <code>request_id</code>. Choose request IDs at the semantic operation boundary—that is, one stable ID for one meaningful business operation—such as:

<code>northstar:telemetry-retry:deliverable:v2</code>

Retry that same operation with the same request ID and payload. Use a new ID for a genuinely new operation, such as submitting version 3.

> <span class="sidebar-kicker">Deeper detail</span> **How replay is recognized**
>
> The effective replay key contains:
>
> - tool name;
> - resolved caller identity;
> - scope kind and scope ID;
> - request ID.
>
> The executor also stores a digest of the normalized payload.
>
> Reusing the same key with the same payload returns the original stored response. It does not create a second task, message, deliverable, escalation, ephemeral assignment, Git promotion, or outbox row. Reusing the same key with a different payload returns <code>idempotency_conflict</code> and does not mutate state.

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

## Deeper detail: dispatch activity and progress

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

Some accepted commands describe work that must happen elsewhere or may take longer: wake an agent, start an ephemeral worker, promote a Git commit, recover a release, or deliver an escalation. Team first saves an **outbox** record—a durable instruction for that later work. This keeps the accepted command from disappearing if a service restarts between the database change and the external effect.

The command response is therefore a receipt, not always a completion notice. “Promotion requested” means the request and outbox intent are committed. It does not mean Git finished. “Escalation queued” does not mean the communication provider delivered it.

> <span class="sidebar-kicker">Technical sidebar</span> **Outbox records and publication**
>
> Some accepted commands describe work that cannot safely happen inside the database transaction: publish an internal event, wake an agent, start an ephemeral worker, perform Git promotion, run release recovery, or route an escalation.
>
> The command writes a <code>TeamOutboxRecord</code> instead. Important fields include:
>
> - intent type;
> - aggregate type and ID;
> - optional unique idempotency key;
> - payload and availability time;
> - status, attempt count, claim token, and claim expiry;
> - last error and timestamps.
>
> Outbox state moves through <code>pending</code>, <code>claimed</code>, <code>completed</code>, and <code>failed</code>. A publisher claims eligible rows with a lease. On success it completes them. On a configured retry it records the error and makes the row pending at a later availability time. A failure recorded without a retry delay remains failed.
>
> The bundled runtime pack calls <code>effect:team:publish-outbox-events</code> on its cron loop and after command-driven dispatch paths. Publication creates the Team internal event that the rest of the workflow graph consumes.

## Northstar transaction traces

### Create a task

Priya calls <code>team_create_task</code> with a stable request ID. The transaction validates her authority, creates the task, advances the revision, records command activity, and stores the replay response. If the ready-task transition emits a wake event, its outbox row is committed with the task.

### Submit a deliverable

Imani publishes local <code>artifact_paths</code> before the final Team transaction. The command then validates task ownership and Git work context, creates the deliverable, changes the task to <code>waiting_for_review</code>, handles selected inbox items, and queues any notice or wake intent. If artifact publication succeeded but the Team transaction later fails, the stable artifact can be orphaned; replay or deliberate cleanup must reconcile it.

### Request a release

Riley's command freezes the accepted scope and any prerequisite Git promotions, stores the release package, and enqueues the next durable intent. The response says which release, promotion, and outbox records were accepted. The Delivery view and later workflow runs say whether verification and publication actually succeeded.

## Troubleshooting

First ask which promise appears to have failed: was the Team command rejected, safely replayed, durably accepted but still waiting on its outbox work, or completed downstream with an unexpected result? That distinction prevents an operator from retrying a finished business action under a new request ID or deleting the evidence needed to trace it.

| Response or symptom | Meaning | Operator or caller action |
|---|---|---|
| Identity/session conflict | Supplied compatibility identity disagrees with the connection. | Stop supplying identity fields; use the intended session. |
| No active dispatch or lease mismatch | A persistent agent called outside its leased Team wake. | Diagnose routing and lease state; do not fabricate a dispatch ID. |
| Target is not in dispatch scope | The current wake did not deliver that task, blocker, review, or inbox record in the corresponding scope list. | Finish or finalize the current dispatch and let the scheduler construct a later scope; do not reuse a remembered ID. |
| Dispatch is still <code>preparing</code> | Required repository/task-worktree setup has not completed. | Inspect the exact preparation failure; commands remain denied until the complete immutable scope is leased. |
| Stand-down error | The caller or an ancestor has an active hold. | Resolve the hold through its authorized revocation path. |
| <code>idempotency_conflict</code> | One request ID was reused for different normalized content. | Read the original response and issue a new semantic request ID only for truly new work. |
| Invalid handled inbox item | At least one ID is missing, already handled, or owned by another agent. | Reload the caller inbox and resubmit the atomic command with the correct set. |
| Command accepted but effect absent | The outbox row is still pending, claimed, retrying, or failed. | Inspect the exact outbox record, publisher workflow run, and downstream event route. |
| Duplicate external activity despite one command | A downstream effect lacks or violated its own idempotency/lease contract. | Preserve evidence and diagnose the effect; do not delete the Team idempotency record. |

## Operator handoff

From the visible command result, capture the Team and project, agent and session, tool name, request ID, Team revision, and any returned activity or outbox IDs. State whether the command was rejected, returned an earlier replayed result, or was accepted while the later effect remained absent. If a task, deliverable, message, promotion, or release was created, include its stable ID.

Retry with the same request ID only when repeating the same semantic payload. Do not create a fresh ID merely because the first response timed out, and do not delete a Team idempotency or outbox record to make the UI look clear.

## Administrator runbook

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
- <code>src/evander_team_addon/services/domain/dispatch_scope.py</code> and <code>human_reviews.py</code> — immutable source-specific dispatch scope and atomic human-decision cascades.
- <code>src/evander_team_addon/sqlite_store.py</code> — serialized command transaction, idempotency keys, outbox claims, retries, and schema.
- <code>src/evander_team_addon/mcp.py</code> — grouped command/read registration and session-aware call context.
- <code>src/evander_team_addon/providers.py</code> and <code>workflow_packs.py</code> — outbox publication provider and runtime routes.
- <code>tests/test_team_addon_mcp.py</code>, <code>tests/test_team_addon_outbox_runtime.py</code>, and <code>tests/test_team_addon_repository.py</code> — identity, replay, rollback, stored outbox IDs, and publication behavior.
