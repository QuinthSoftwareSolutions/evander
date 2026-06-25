# Critique 2

Overall, I think the proposal is directionally strong. It preserves the main
Team repository semantics, rejects a broad `team_submit_action` as the primary
interface, and moves the agent-facing surface toward intent-specific tools with
synchronous validation. My comments below are mostly about places where the
current design could accidentally change runtime semantics during
implementation.

## 1. Define the dispatch completion model explicitly

The largest semantic change is not the tool surface itself. It is that Team
state can now mutate before the session completion event.

Today, applying the action artifact is also where the repository records the
action, clears the active dispatch lease, updates `no_progress_count`, updates
context-rotation timestamps, queues retryable chat follow-up events, and records
failed-action feedback. The design says mutating tools do not close the wake and
that no-progress accounting is computed later by comparing Team state changes
made during the dispatch. That is plausible, but it needs a concrete model.

Suggested addition:

- Add an explicit "dispatch activity ledger" concept for MCP-native Team wakes.
- Every successful or failed mutating tool call records an activity row keyed by
  active dispatch id, tool name, request id, progress classification, and action
  record id.
- Session completion consumes that ledger to clear the active lease, increment
  or reset `no_progress_count`, update context-rotation state, and finalize any
  per-dispatch attention/feedback state.

Without this, it will be easy to implement the tools correctly in isolation but
silently lose the completion-time bookkeeping that the action artifact currently
bundles together.

## 2. Separate "state changed" from "progress made"

The proposed base return shape includes `applied`. In the current action path,
`applied` is closer to "progress-making action happened" than simply "state was
mutated". That difference matters for `team_record_note`, no-op dispatches, and
possibly low-value status reports.

For the MCP tools, I would avoid overloading one boolean. Suggested return
fields:

- `applied`: the tool made the requested state change.
- `progress_made`: the change should reset no-progress accounting.
- `action_record_id`: the audit record for this tool call, when one was written.
- `dispatch_id`: the active dispatch that the call was attached to.

Then `team_record_note` can return `applied: true` and `progress_made: false`
without ambiguity.

## 3. Make idempotency stricter than "optional request_id"

Optional `request_id` is fine for low-risk updates, but it is not enough for
create/event tools where a transport timeout can hide a successfully applied
operation. The proposal currently says the server may generate an action record
id when `request_id` is omitted. That does not protect retries, because the
agent cannot know the generated id after a lost response.

I would make `request_id` required for tools that create durable objects or emit
workflow events:

- `team_create_task`
- `team_create_tasks`
- `team_submit_deliverable`
- `team_submit_deliverable_for_review`
- `team_request_review`
- `team_request_release`
- `team_recover_release`
- `team_request_ephemeral_agent`
- `team_request_ephemeral_agents`
- `team_report_ephemeral_result`
- chat and escalation tools that create messages or external events

Also specify exact replay behavior:

- Same tool, caller, dispatch, and `request_id` with identical normalized
  arguments returns the original result.
- Same key with different arguments fails without mutation.
- Event ids are derived from or persistently associated with the idempotency key
  so retries do not emit duplicate workflow events.

## 4. Decide partial-apply semantics per tool family

`team_create_tasks` preserves current batch behavior where valid items may apply
while invalid items return item-scoped errors. That matches the artifact path,
but it is surprising for an MCP tool unless the result shape is explicit.

Suggested rule:

- Single-object tools are all-or-nothing.
- Compound convenience tools are all-or-nothing unless documented otherwise.
- Batch tools either expose `mode: "atomic" | "partial"` or state clearly that
  they can return `success: false`, `applied: true`, and per-item errors.

This is especially important for `handled_inbox_item_ids`: if the primary action
is valid but one inbox id is invalid, the design should say whether the whole
call fails, the primary action applies, or only valid inbox ids are handled. My
preference is all-or-nothing for "primary action plus handled inbox ids" so an
agent does not accidentally close or half-close the item it was responding to.

## 5. Keep tool descriptions small and remove schema escape hatches

The design is strongest where tools are intent-specific. A few arguments still
feel like the old blob contract leaking through:

- `payload` on `team_request_status`, `team_ask_question`, and
  `team_send_progress_report`
- broad `metadata` on `team_create_case`
- fields accepted only "for parity" when current semantics ignore them, such as
  `team_update_goal.description`

Some metadata escape hatches may be necessary, but they should be deliberately
named and narrowly documented. Otherwise agents still have to reason about
loosely-defined JSON payloads, which undercuts the MCP migration.

## 6. Specify how add-on tools receive ambient session identity

The documents say agents do not pass `agent_id`, `session_id`, or `dispatch_id`.
That is the right target. The implementation plan should still spell out how
add-on-registered MCP tools receive the ambient gateway session.

Current Team governance MCP tools still accept an optional `session_id` argument
and call policy with that value. For the new persistent action tools, the design
should require a shared helper that reads the gateway-bound caller session,
rejects conflicting legacy `session_id` arguments if any are accepted during
transition, resolves the Team agent, and checks the active lease.

This helper should be part of the migration design, not rediscovered in each
tool implementation.

## 7. Event publishing needs failure and retry semantics

Several proposed tools record intent and emit existing workflow events:

- operator escalation
- governance escalation
- git review/feature/release promotion
- release recovery
- ephemeral worker request
- ephemeral worker completion

The current effect provider applies repository state and then publishes events.
If MCP tools publish events synchronously from the gateway path, the design
should define what happens when state mutation succeeds but event publication
fails. Options include:

- persist an outbox item in Team state and let a worker publish it,
- return a retriable warning while guaranteeing idempotent event replay, or
- keep event publication in the existing workflow/effect path.

I would strongly prefer a persisted outbox or existing workflow path over
best-effort synchronous publishing from the MCP handler.

## 8. Artifact publication needs a host-side boundary plan

`team_submit_deliverable` says `artifact_paths` are published before recording
the deliverable. That mirrors the current effect-provider behavior, but in an
MCP-native world this tool will be running on the gateway/add-on path. The design
should say which service owns:

- resolving the caller session's `/artifacts` namespace,
- publishing selected paths into the artifact store,
- rejecting unsafe or missing paths,
- avoiding orphaned artifact-space records if state mutation fails after publish,
- returning already-published refs on idempotent replay.

This is not just repository logic, so it should be called out as an integration
point.

## 9. Be cautious with immediate downstream wakes

Immediate mutation is the main benefit, but it also means a tool call can wake a
reviewer, subordinate, governance manager, or release workflow before the caller
has finished its response. That is usually fine when the tool call is the final
intent, but agents sometimes reason by trying actions speculatively.

The prompts should make the positive instruction simple: call mutating Team
tools when you are ready for that state change to take effect. This is better
than warning them about internals, but it preserves the important behavioral
shift from "draft a file before finishing" to "tool calls are live."

## 10. Align the guidance with the inline comments on negative instructions

I agree with the inline human comments. Once MCP-native agents no longer see
`team-action.json`, most replacement prompts should not say "do not write
team-action.json." That phrase is useful only in mixed-mode migration prompts
where the legacy file path is still visible or seeded.

Similarly, ordinary agents do not need to hear about "normal session completion
event closes the wake" or "no-progress accounting." Better agent-facing wording:

```text
Use Team MCP tools when you need to change Team state or record a useful note.
When you are done, finish normally.
```

The implementation docs can describe completion events and no-progress
accounting; prompt text should stay at the action level.

## 11. Preserve legacy fallback without keeping legacy concepts in native prompts

The non-goals correctly keep the JSON artifact path until the MCP surface is
implemented, verified, and deployed. I would make the migration mode explicit:

- Legacy action-file dispatches still seed schema/example/helper files and use
  missing-action recovery.
- MCP-native dispatches do not seed those files and do not mention them.
- Mixed-mode sessions should be avoided where possible; if unavoidable, the
  prompt must clearly identify which mode the current dispatch is using.

This avoids a half-migrated prompt where agents see both a tool surface and old
file artifacts and have to infer which one wins.

## 12. Add an implementation order

The docs are broad enough that implementation could sprawl. I would add a
phased order that starts with a narrow vertical slice and proves the shared
plumbing:

1. Ambient session/active lease helper.
2. Dispatch activity ledger and completion finalizer.
3. Idempotency store for mutating Team tools.
4. A simple tool such as `team_send_progress_report`.
5. An inbox-closing flow such as `team_request_status` with
   `handled_inbox_item_ids`.
6. A side-effect flow such as `team_submit_deliverable` with artifact publish.
7. A workflow-event flow such as `team_request_ephemeral_agent` or
   `team_request_release`.
8. Prompt migration for MCP-native dispatches.
9. Legacy artifact fallback deprecation.

That order reduces the chance of implementing many superficially similar tools
before the hard shared semantics are settled.

## 13. Suggested acceptance criteria

Before switching normal Team agents to MCP-native action tools, I would want
focused tests proving:

- a tool call from a non-active dispatch fails without mutation;
- a valid tool call records audit/activity under the active dispatch without
  closing the lease;
- session completion closes the lease and updates no-progress accounting based
  on recorded tool activity;
- idempotent retry returns the same result and does not duplicate inbox items,
  deliverables, release packages, git events, or chat messages;
- conflicting retry payloads fail without mutation;
- primary action plus handled inbox ids is atomic;
- artifact publication failure leaves no deliverable and gives a corrective
  tool error;
- event publication failure is recoverable without duplicate events;
- MCP-native prompts contain no action-file instructions;
- legacy action-file dispatches still work during migration.
