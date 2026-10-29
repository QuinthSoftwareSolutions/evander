# Post-Critique Team MCP Design Decisions

> **Status:** Historical and implemented. This file records decisions made during the Team MCP
> migration; it is not the current API reference. See [`../addons.md`](../addons.md).

This document is the consolidated design overlay for the Team MCP action surface
migration. It incorporates the critique decisions plus the later concurrency,
refresh, and clean-cut cutover decisions.

The decisions here supersede contradictory guidance in:

- `team-mcp-action-surface.md`
- `agent-guidance-surface-inventory.md`
- `persistent-agent-instruction-replacements.md`
- `specialized-instruction-replacements.md`
- the now-superseded concurrency/refresh follow-up note

Future edits to those files should apply the decisions below.

## Core Decisions

### Clean Cut, No Legacy Action Mode

No legacy action-file compatibility path is required for this migration.

The Team MCP action surface is a clean replacement for
`/artifacts/team-action.json`, not a parallel mode. This repo is not deployed as
a multi-tenant or externally versioned product, and existing local teams can be
migrated directly. Keeping legacy/native branching would add prompt
conditionals, artifact-seeding conditionals, completion-routing branches,
missing-action recovery branches, and compatibility tests that would mostly
increase bug surface.

Implementation should remove the old JSON action mode instead of introducing an
action-surface flag.

Implications:

- Do not keep a per-session, per-Team, or per-dispatch
  `legacy_artifact`/`mcp_native` action-surface mode.
- Do not seed `/artifacts/team-action.schema.json`,
  `/artifacts/team-action.example.json`, or `team-finish` for Team wakes.
- Do not mention `team-action.json`, `schema_version`, `agent_id`, or
  `dispatch_id` in normal Team prompts.
- Do not keep missing-action recovery for Team wakes.
- Session completion should always finalize Team dispatches from the MCP
  dispatch activity ledger.
- Existing local Team state should be migrated once during a controlled local
  cutover.

Suggested local cutover:

1. Stop Team-related trigger/effect/Admin services.
2. Inspect or clear active Team leases.
3. Manually apply any already-written pending `team-action.json` artifacts only
   if they represent work worth preserving.
4. Apply the state/schema migration for activity, idempotency, and outbox
   storage.
5. Remove artifact seeding and legacy prompt text.
6. Restart services with MCP tools as the only Team action path.

### Immediate Tool Effects Remain The Goal

MCP tools should not become a different syntax for the old end-of-wake action
artifact. The migration should preserve the main product win: an agent can make
state-changing Team calls during a wake, observe returned ids/state, and use
that result in later tool calls during the same wake.

This means Team mutations should not be batched until session completion.
Multi-step sequences such as "create tasks, then delegate one of them" or
"submit a deliverable, then immediately request review" should be possible in
one wake when no other actor or external asynchronous effect must complete in
between.

### Command-Style Concurrency

Normal Team agent MCP action tools do not need agent-facing optimistic
concurrency through a global `expected_revision` argument.

Team action tools should instead behave as command-style operations:

1. Start a storage transaction and acquire the repository lock.
2. Resolve the ambient caller session, Team agent, active lease, dispatch, and
   idempotency key.
3. Re-read the current rows and related semantic dependencies needed by that
   tool.
4. Validate the operation against latest committed state.
5. Mutate only the scoped rows involved in the operation.
6. Append dispatch activity, audit, and durable async intent/outbox rows as
   needed.
7. Bump the Team state revision and commit.

This avoids false conflicts when unrelated Team state changes during the same
wake, while still preventing stale writes for the surface being changed.

The "mutated surface" for a tool means all semantic dependencies of that tool,
not only the literal row being updated.

Examples:

- `team_mark_inbox_handled`: the inbox item must still exist, belong to the
  caller, and be pending.
- `team_decide_review`: the review must still be assigned to the caller, still
  be decidable, and the deliverable/task cascade must still be valid.
- `team_request_release`: deliverables must still exist, belong to the project,
  be accepted, and satisfy release/package invariants.
- `team_update_task`: ownership/supervision, terminal-state rules, and
  dependency graph constraints must still hold.

Global revision guards can remain useful for Admin/UI/workflow surfaces that
edit a full snapshot, but they are not part of the normal agent action tool
contract.

### Scoped Storage Writes

The current generic repository path reads the whole Team state and rewrites all
state tables for each update. That was acceptable for coarse one-shot action
application, but it should not be the long-term hot path for MCP-native trivial
actions.

The migration should introduce scoped transactional command methods for
high-frequency tools. Full-state projections can remain useful for UI, prompts,
tests, and compatibility with read/projection code, but command tools should
mutate only the rows they need.

### Split Immediate Mutation From Dispatch Finalization

Tool calls apply their own Team state mutation immediately. Tool calls do not
close the active dispatch lease. Session completion closes the lease and
performs dispatch-finalization bookkeeping from the dispatch activity ledger.

Implementation should therefore extract lower-level validation/apply services
that can be used by MCP tools without triggering end-of-dispatch behavior.
There is no long-term legacy action adapter; the old action-file path should be
removed during the clean cut.

### Use A Dispatch Activity Ledger

Team MCP dispatches need a dispatch-scoped activity ledger or equivalent model.
Every Team MCP action attempt should be associated with the active dispatch and
should record enough information for completion-time bookkeeping:

- tool name
- caller session/agent
- dispatch id
- request id
- success/failure status
- progress classification
- action/audit record id when one exists
- correlation id when provided

Session completion consumes this activity to clear the active lease, update
no-progress state, update context/activity bookkeeping, and finalize dispatch
attention/feedback.

### Tool Calls Are Transaction Boundaries

Atomicity is per tool call, not per wake. If an agent makes three successful
tool calls and the fourth fails validation, the first three remain applied.
That is acceptable and intentional because MCP calls are live operations, not a
draft action object.

Each tool still needs an explicit atomicity contract:

- Single-object tools should be all-or-nothing.
- Compound simplification tools should be all-or-nothing unless specifically
  documented otherwise.
- New batch tools should default to all-or-nothing. A partial batch mode should
  exist only after an explicit product decision, not for parity with the removed
  action-file contract.
- Partial batch tools, if any are kept, must return item-scoped ids,
  item-scoped errors, and safe retry guidance.
- `handled_inbox_item_ids` should be atomic with the primary action for
  single-object and compound tools. For partial batch tools, either disallow
  inbox handling or require an explicit mode that defines when inbox items close.

### Idempotency Must Be Exact

`request_id` is an idempotency key for one intended tool operation. It must not
be used as a prefix or grouping mechanism.

Replay semantics should be exact:

- same tool + caller + dispatch + request id + same normalized payload returns
  the original result
- same tool + caller + dispatch + request id + different normalized payload
  fails without mutation
- event ids and created domain ids are persistently associated with the
  idempotency key so retries do not duplicate inbox items, messages, tasks,
  deliverables, release packages, assignments, or workflow events

Create, append, message, and async-intent tools should require a stable
`request_id` unless they have a natural explicit domain id that fully prevents
duplication. This applies to tools that create durable objects, messages, inbox
items, review requests, release packages, ephemeral assignments, promotion
events, or other outbox/intent records.

Pure updates to an existing object can rely on the existing object id plus
surface-specific precondition checks when that is enough to prevent duplicate
effects.

Related calls should use a separate optional `correlation_id` or `causation_id`
that is copied into activity/audit records.

### Immediate Intent, Durable Async Execution

Some tool effects should be visible immediately without pretending that external
work completed synchronously.

Repository state changes should commit synchronously when they can be validated
and applied transactionally. External or long-running effects should be recorded
as durable intent/outbox entries in the same transaction, then processed by the
existing workflow/effect path or an equivalent retryable worker path.

Examples:

- A release request can immediately create a release package and promotion
  intent, but Git consolidation/push remains asynchronous.
- An ephemeral-agent request can immediately create assignments and enqueue
  worker-start intent, but worker execution remains asynchronous.
- A deliverable submission can immediately record the deliverable, but artifact
  publication/import needs a clear host-side boundary and retry/orphan cleanup
  story.

Tool returns for async intent should distinguish `intent_recorded` from
`effect_completed`.

### Progress Is Tool-Classified

No-progress accounting should not infer progress from "any state changed".
Every tool should declare whether successful use counts as progress and, where
needed, whether it counts as context/activity.

Initial policy:

- `team_record_note`: applies audit/handoff context but does not count as
  progress.
- `team_mark_inbox_handled`: counts as progress only when it handles a real
  pending inbox item.
- ordinary communication, task, deliverable, review, case, goal, stand-down,
  release-intent, and delegation tools count as progress when successfully
  applied.
- failed validation does not count as progress.
- accepted asynchronous intent counts as progress when the intent is recorded,
  not when the later external effect completes.

### Correction And Refresh Contract

Rich validation errors are part of the action surface contract.

Because agents may still act from stale wake-start context, failed tool calls
must return enough current information for the agent to correct the request
during the same wake. Errors should include relevant current facts such as
current record state, owner or assignee, active related review requests,
terminal state, or the reason a precondition no longer holds.

Start with rich mutation responses and rich validation errors. Add narrow
read/refresh tools only after a concrete tool flow proves that returned error
context is insufficient.

### Ambient Session Identity

All Team MCP tools should use a shared ambient-session helper that reads the
gateway-bound caller session, resolves the Team agent or governance manager,
and checks the appropriate authorization model.

Normal persistent-agent action tools should not accept `session_id`, `agent_id`,
or `dispatch_id` arguments. Existing governance MCP tools should be moved onto
the same ambient-session helper during the cutover. Any remaining signature that
temporarily accepts an explicit identity argument during refactor must reject a
value that conflicts with the ambient caller rather than silently trusting it.

### Tool Visibility Should Be Wake-Scoped

The suite should remain intent-specific, but the visible surface should still be
as small as practical for the current wake/session class.

Target visibility:

- normal persistent Team wake: ordinary Team action tools
- Team Chat wake: `team_reply_to_chat`, plus ordinary state-mutating tools only
  when that chat dispatch is allowed to mutate Team state
- governance wake: governance tools, not ordinary project-work tools
- ephemeral worker wake: `team_report_ephemeral_result` only unless a specific
  extra capability is required
- inactive or teamless session: no ordinary Team action tools, except
  governance tools when the caller is an authorized governance manager

Tool hiding is for agent ergonomics. Server-side authorization must still
enforce every boundary even when a tool is visible.

### Agent-Facing Guidance Should Stay Positive And Minimal

Fresh Team prompts should not teach agents about removed artifacts or internal
completion machinery.

Steady-state guidance should say things like:

```text
Use Team MCP tools when you need to change Team state or record a useful note.
When you are done, finish normally.
```

Do not include steady-state prompt text such as:

- `Do not write team-action.json.`
- `Do not pass dispatch_id.`
- `Normal session completion closes the wake and records no-progress state.`

Those are implementation facts, not useful agent-facing instructions once the
old action-file surface is gone.

### Avoid Schema Escape Hatches

The MCP migration should not recreate the old action-file blob contract through
loosely defined arguments.

Avoid generic `payload` fields, broad `metadata` objects, and "accepted for
parity" fields that current semantics ignore. If an escape hatch is genuinely
needed, name it narrowly, document the current consumer, constrain the shape,
and include validation rules.

### Validation Feedback Scope

Failed MCP validation attempts should be audit-visible and activity-ledger
visible, but they should not automatically become next-wake failed-action
feedback. The agent receives those errors synchronously and can correct them in
the same wake.

Delayed failed-action feedback should be reserved for asynchronous failures or
failures discovered after the wake, where the agent did not already receive an
actionable synchronous tool response.

## Modifications To The Original Four Files

### `team-mcp-action-surface.md`

Apply these changes when the action-surface design is revised:

- Replace any legacy/native action-surface mode with the clean-cut decision:
  MCP tools are the only Team action path after the local cutover.
- Add a "Command-Style Concurrency" section describing lock/transaction,
  current-row re-read, surface-specific precondition checks, scoped mutation,
  revision bump, and no normal agent-facing global `expected_revision`.
- Add a "Scoped Storage Writes" section describing row-scoped command methods
  for hot MCP tools instead of whole-state rewrites.
- Add a "Dispatch Activity Ledger" section describing the activity log and
  session-completion finalizer.
- Replace the simple optional `request_id` description with exact idempotency
  semantics and a separate `correlation_id`/`causation_id` for grouping.
- Require `request_id` for create, append, message, and async-intent tools
  unless a natural explicit domain id fully prevents duplication.
- Add a tool atomicity/retry table covering every tool.
- Add a progress-classification table covering every tool.
- Add a tool availability matrix by wake/session type.
- Clarify that tool calls mutate immediately but do not finalize dispatches.
- Clarify that external effects are recorded as durable intent/outbox entries
  and processed asynchronously/retryably.
- Split return shape fields so `applied` and `progress_made` are not
  overloaded.
- For async tools, include result fields such as `intent_recorded`,
  `async_effect_pending`, created event ids, current package/assignment state,
  and next-action hints.
- Define artifact publication/import ownership for deliverable tools, including
  path safety checks, idempotent replay, and orphan cleanup.
- Define whether compound tools such as `team_submit_deliverable_for_review`
  suppress, create, or auto-handle the normal superior deliverable notice.
- Reconsider `team_create_case.team_id`; if teamless case creation is real, give
  it an explicit scoped path rather than leaking cross-team selection into the
  normal active-lease surface.
- Require meaningful content for question/progress tools unless a tool
  explicitly supports a default message.
- Revisit `team_update_task.state` values. If `accepted` and `rejected` are
  review/system transitions only, remove them from ordinary task update args or
  document the legitimate direct-transition case precisely.
- Remove generic `payload`, broad `metadata`, and unused parity fields unless a
  narrow documented consumer and validation rule exist.
- Define rich validation-error payloads that return correction context for
  stale or failed preconditions.

### `agent-guidance-surface-inventory.md`

Apply these changes when the inventory is revised:

- Remove legacy action-file mode as a target surface. The inventory may mention
  old surfaces only as things being removed during cutover.
- Add the local cutover as an implementation/migration surface.
- Add the ambient session/active-lease helper and dispatch activity ledger as
  implementation-owned surfaces, not agent-facing docs.
- Inventory existing governance MCP instructions separately from ordinary
  persistent-agent action guidance.
- Note that ephemeral worker result reporting is MCP-native but worker-scoped,
  not a normal persistent Team lease.
- Track every seeded file, prompt fragment, and recovery path that must be
  removed so no stale artifact guidance remains.

### `persistent-agent-instruction-replacements.md`

Apply these changes when the persistent guidance replacement is revised:

- Replace detailed session-completion/no-progress prose in agent-facing text
  with short positive wording: use tools for real Team actions; finish normally
  when done.
- Remove steady-state negative instructions about `team-action.json`,
  `dispatch_id`, or mutation outside tools; the removed surfaces should not be
  visible.
- Keep `team_record_note` as a note tool only, not as a completion ritual.
- Remove `available_team_mcp_tools` from prompt context unless MCP client
  limitations require duplicating tool discovery.
- Remove `request_id prefix` guidance. If related calls need grouping, describe
  `correlation_id` only in tool docs or narrow prompt text where useful.
- Make inbox-handling guidance action-oriented without exposing internal
  completion accounting.
- Remove references to seeded schema/example/helper files without replacement.

### `specialized-instruction-replacements.md`

Apply these changes when specialized prompt replacements are revised:

- Team Chat prompts should say tool calls are live state changes and should be
  used only when the agent is ready for the state change to take effect.
- Replace `request_id prefix` guidance with `correlation_id` only if the action
  surface adds that field.
- Governance prompts should expose governance tools only, unless the wake is
  explicitly also a normal Team action dispatch.
- Ephemeral worker prompts should expose `team_report_ephemeral_result` as the
  minimal worker-scoped action surface.
- Remove missing-action recovery entirely; there is no action artifact recovery
  path after cutover.
- Correction hints for synchronous MCP validation failures should focus on
  retrying the specific failed tool call during the same wake.
- Keep delayed correction/attention only for asynchronous or post-wake failures.

## Implementation Order Suggested By These Decisions

1. Add the shared ambient session and active-lease helper for add-on MCP tools,
   including existing governance tools.
2. Add storage/schema support for dispatch activity, idempotency records, and
   durable outbox/intent records.
3. Add scoped transactional command methods for high-frequency Team tool
   operations.
4. Add the session-completion finalizer that consumes the dispatch activity
   ledger and remove action-file completion/recovery behavior.
5. Implement one simple repository-only tool, such as
   `team_send_progress_report`.
6. Implement one inbox-closing tool path proving atomic
   `handled_inbox_item_ids`.
7. Implement one artifact path, such as `team_submit_deliverable`.
8. Implement one async intent/outbox path, such as
   `team_request_ephemeral_agent` or `team_request_release`.
9. Remove action-artifact seeding, helper creation, old prompt text, and
   missing-action recovery prompts.
10. Perform the local cutover: stop services, inspect or clear active leases,
    apply any pending action artifacts worth preserving, migrate state, and
    restart with MCP tools as the only Team action path.

## Acceptance Criteria To Carry Forward

- No Team wake seeds action schema/example/helper files.
- No normal Team prompt mentions `team-action.json`, `schema_version`,
  `agent_id`, or `dispatch_id`.
- No legacy/native action-surface flag or runtime branch remains.
- A tool call outside the active lease fails without mutation.
- A valid tool call mutates state and records activity without closing the
  lease.
- Session completion closes the lease and updates no-progress using the
  dispatch activity ledger.
- Normal agent tools validate latest relevant state under lock and do not
  require global `expected_revision`.
- Hot MCP tools use scoped transactional command methods rather than
  whole-state rewrites.
- Idempotent replay returns the same result and does not duplicate state or
  events.
- Conflicting replay payloads fail without mutation.
- Create, append, message, and async-intent tools require a stable `request_id`
  unless a natural explicit domain id prevents duplication.
- Precondition failures return enough current context for same-wake correction.
- Primary action plus inbox handling is atomic where documented.
- Batch tools are all-or-nothing by default; any explicit partial batch tool has
  item-scoped results and safe retry behavior.
- Artifact publication failure leaves no inconsistent deliverable state.
- Async event publication is recoverable without duplicate events.
- Failed synchronous validation is audit/activity-visible but does not become
  next-wake failed-action feedback.
- Tool visibility is scoped to wake/session class while server-side
  authorization still enforces all boundaries.
