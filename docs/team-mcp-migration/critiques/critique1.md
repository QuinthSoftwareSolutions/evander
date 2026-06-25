# Team MCP Migration Critique 1

## Overall

The proposal is directionally strong. Intent-specific MCP tools are a better
agent interface than a broad action JSON file, and the documents correctly keep
the Team-specific surface add-on-owned, preserve repository authorization rules,
and avoid a generic `team_submit_action` primary API.

My main concern is that the design currently says "preserve current semantics"
while also changing the timing model from one post-wake action application to
multiple immediate state mutations during the wake. That can still be the right
choice, but it needs to be treated as a deliberate semantic change with explicit
rules for lease lifetime, audit records, idempotency, inbox handling, and event
ordering.

## High-Priority Comments

### 1. The current repository action method cannot be reused directly

`team-mcp-action-surface.md` says the tools should preserve repository rules
that currently validate and apply `team-action.json`. That is right, but the
implementation cannot simply have each tool call the existing
`apply_agent_action(...)` method.

The current action path applies a whole action object, writes one action record,
updates no-progress state, and clears `runtime.active_dispatch_id` /
`wake_leased_until` at the end. The MCP design says mutating tools apply
immediately but the active lease remains open until the normal completion event.
Those two models conflict.

Suggested design update:

- Extract lower-level per-domain validation/apply services that do not close the
  active lease.
- Add a separate dispatch-completion path that computes progress/no-progress
  from the tool calls recorded during the dispatch.
- Keep the old `apply_agent_action(...)` as the legacy artifact adapter, or make
  it call the same lower-level services and then perform legacy completion.

Without this separation, the first successful MCP action call would effectively
finish the Team wake.

### 2. Immediate mutation changes event ordering

The design says MCP tools should apply state changes immediately during the
wake. That is a major behavior change from the current artifact model, where all
Team state changes are applied after the agent finishes.

Immediate mutation means a caller can create inbox items, review requests,
release events, or ephemeral-worker events while it is still running. Other
workers may then wake or consume events before the caller's wake has completed.
That may be desirable, but it is not just transport replacement.

Suggested design update:

- Explicitly decide which side effects are emitted immediately versus buffered
  until dispatch completion.
- Document whether same-dispatch downstream wakes are allowed.
- If immediate event emission is allowed, add idempotency and event-ordering
  tests for multi-tool wakes.

### 3. Idempotency needs a sharper contract

The documents make `request_id` optional and suggest using a stable
`request_id` prefix for related calls. This is underspecified and may be
dangerous.

Concerns:

- A server-generated id does not help if the client loses the response after the
  mutation is committed.
- "Same request_id prefix" is ambiguous. Idempotency keys should be exact keys,
  not prefixes.
- Related operations need correlation, not dedupe. Reusing the same request id
  across two calls to the same tool could accidentally suppress the second call.

Suggested design update:

- Make every mutating tool accept `request_id` and strongly recommend it in
  tool descriptions, but avoid pushing this into durable agent prose too much.
- Define exact semantics: same tool + caller agent + dispatch + request_id +
  same normalized payload returns the original result; same key with different
  payload returns a conflict.
- Add a separate optional `correlation_id` or `operation_group_id` for grouping
  multiple calls that belong to one human or inbox request.
- Remove "request_id prefix" language from prompt guidance unless a separate
  correlation field exists.

### 4. Atomic inbox handling is ambiguous

Most tools accept `handled_inbox_item_ids`, which is a good simplification. The
documents should define exactly when those inbox items are marked handled.

The risky case is a compound or batch call. For example, `team_create_tasks`
defaults to partial apply with per-item errors. If one task succeeds, another
fails, and `handled_inbox_item_ids` is present, should the inbox item be closed?
Closing it may hide work that was only partially handled. Not closing it may
surprise agents because the primary action partially succeeded.

Suggested design update:

- For single-action tools, make primary mutation plus inbox handling
  all-or-nothing.
- For batch tools, either use all-or-nothing by default or require an explicit
  `apply_mode` before allowing partial apply with inbox handling.
- Define that inbox items are not marked handled when the primary action fails
  validation.

### 5. Audit compatibility needs a concrete model

The action-surface doc says every mutating tool writes the same durable
action/audit trail as the current artifact path. That is a good requirement, but
the shape is not yet clear.

Questions to resolve:

- Is there one `AgentActionRecord` per MCP tool call, or one aggregate record
  per dispatch?
- Are validation failures recorded as failed action records, or only returned to
  the caller?
- How do existing timeline, runtime attention, recent action feedback, and
  failed-action UI behavior interpret multiple tool calls in one dispatch?
- What is the canonical stored action for a compound tool such as
  `team_submit_deliverable_for_review`?

Suggested design update:

- Introduce a dispatch-scoped action log model, or explicitly state that every
  mutating tool call creates one action record correlated by dispatch id.
- Record failed validation attempts somewhere useful for debugging, but do not
  necessarily surface them as next-wake "failed action" feedback when the agent
  already received synchronous errors.
- Define the legacy artifact adapter as producing the same audit primitives.

### 6. Active lease and caller identity exceptions need to be explicit

The shared rules say all Team action tools infer caller identity from the
session-scoped MCP connection and require an active Team lease unless explicitly
documented otherwise. The exception list should be precise.

Cases that need explicit treatment:

- Existing governance tools are already MCP tools and currently have their own
  governance-session authorization model.
- `team_report_ephemeral_result` is worker-scoped and likely should authenticate
  through the assignment/session binding, not a normal persistent Team lease.
- Inactive or teamless agents may have governance tools but should not receive
  ordinary persistent-agent action tools.
- Team Chat wakes should state whether they are ordinary active Team leases or a
  separate chat dispatch type.

Suggested design update:

- Add an "Authorization Matrix" table listing each tool, allowed caller type,
  active-lease requirement, and whether governance/ephemeral sessions are
  allowed.
- Make existing governance tools part of that matrix even though they are not
  replacing action-file fields.

### 7. Ambient session support should be a migration prerequisite

The documents rely on agents not passing `session_id`, `agent_id`, or
`dispatch_id`. That is the right end state. The migration plan should call out
the gateway/add-on prerequisite explicitly: Team add-on MCP handlers need a
reliable ambient session identity from the MCP connection, and explicit
conflicting identity arguments should be rejected rather than silently accepted.

This matters because it is easy to accidentally leave old tool signatures around
that still ask for `session_id`, especially for the existing governance tools.

## Medium-Priority Comments

### 8. Prompt guidance still exposes internal mechanics in a few places

I agree with the inline human comments in
`persistent-agent-instruction-replacements.md` and
`specialized-instruction-replacements.md`.

Agent-facing instructions should avoid negative or internal guidance unless the
agent is likely to infer the wrong action from old context. In particular:

- "Do not write team-action.json" should be absent from fresh MCP-native prompts
  once the artifact path is no longer visible. It is useful only in mixed
  migration prompts where old files may still exist in the VM.
- "Normal session completion will close the wake and count no progress" is an
  implementation detail. The agent only needs to know that if it has no useful
  Team action, it can finish normally.
- "Use tools only when there is Team state to change" is better than enumerating
  no-op/no-progress behavior.

Suggested replacement tone:

```text
Use the specific Team MCP tool for the Team action you intend. If there is no
useful Team action to take, finish normally.
```

### 9. The design should include a read/refresh story

The proposal focuses on mutating tools. Because mutations happen immediately,
the Team context the agent received at wake start can become stale during the
same wake.

This can be handled by rich return payloads, but that should be a deliberate
rule. Otherwise agents may need a read tool such as `team_get_context`,
`team_get_inbox_item`, or `team_get_current_state_summary`.

Suggested design update:

- Either add a small read-only Team MCP surface, or state that mutating tool
  responses must return enough updated state for the next likely tool call.
- For compound flows, return the created/updated objects, not just ids.

### 10. `team_submit_deliverable_for_review` needs to define the deliverable notice behavior

The compound deliverable/review tool says it is equivalent to
`team_submit_deliverable` followed by `team_request_review`, and also says it
avoids an extra wake where a superior notices the deliverable and then requests
review.

Those statements may conflict. If the deliverable half still creates a
`DELIVERABLE_NOTICE` for the superior, the extra wake may still happen. If the
compound tool suppresses that notice, it is a semantic simplification that
should be explicit.

Suggested design update:

- State whether the compound tool creates, suppresses, or auto-handles the
  superior deliverable notice.
- Add tests for the exact inbox items created by this tool.

### 11. Batch tools should reconsider partial apply

The batch tools preserve current partial-apply behavior. That is compatible with
the artifact path, but MCP gives the agent immediate feedback and a chance to
retry within the same wake. All-or-nothing may be easier for agents to reason
about for new MCP-native batch tools.

Suggested design update:

- Prefer all-or-nothing for new batch APIs unless current partial apply is a
  hard requirement.
- If partial apply remains, return a stable item-level result format and make
  retry guidance explicit.

### 12. Tool visibility and role scoping need a policy decision

The action surface is large. It may be acceptable for all persistent Team agents
to see all ordinary Team action tools and receive authorization errors when a
tool is not permitted. It may also be better to hide tools that can never apply
to the current role/session.

Suggested design update:

- Decide whether tool visibility is coarse-grained ("Team action tools") or
  dynamically role-scoped.
- If coarse-grained, tool descriptions should make role limitations very clear.
- If dynamic, include this in the migration plan because session allowed-tool
  grants and add-on custom tool names become part of runtime setup.

### 13. Optimistic concurrency is not addressed

Tool return shapes include `revision`, but most tool arguments do not include
`expected_revision`. Because other Team agents, operators, or governance flows
can mutate the same Team state while a wake is running, the design should decide
whether tools always apply against current state or whether agents can request a
revision guard.

Suggested design update:

- Either add optional `expected_revision` consistently, or explicitly state that
  Team MCP tools validate against the latest state and do not support
  agent-supplied revision guards.
- If `expected_revision` exists, define whether stale revisions return
  corrective state snapshots.

## Smaller Comments

### 14. `team_record_note` matches current no-progress behavior, but say that as compatibility

The design says notes are audit/handoff context and not progress. Current
repository behavior effectively ignores `notes` for non-empty action detection,
so this seems compatible. I would phrase it as an implementation compatibility
rule in the design doc, not as agent-facing guidance.

### 15. `team_mark_inbox_handled` counting as progress should be intentional

The proposal says marking inbox items handled counts as progress. That matches
the current action field behavior, but it can also become the easiest way for an
agent to appear productive. I think it is still reasonable because handling a
real inbox item is state progress, but tests should cover invalid or already
handled inbox ids so this cannot be used as a no-op progress reset.

### 16. Tool naming is mostly clear, but release/Git names should align with existing hints

The proposed names are understandable. I would double-check the Git names
against the existing runtime hints and UI labels before implementation:

- `team_publish_review_branch`
- `team_update_feature_branch`
- `team_request_release`
- `team_recover_release`

These are better than exposing raw `git_*` field names, but their tool
descriptions need to be very explicit that tools record intent and workflow
effects perform the actual Git work asynchronously.

## Migration Plan Comments

The docs say not to remove the JSON artifact path until the MCP surface is
implemented, verified, and deployed. I would make the rollout mechanics more
specific:

- Add a per-session or per-Team capability flag for MCP-native action handling.
- Keep seeding schema/example/helper files for legacy sessions until the session
  is known MCP-native.
- In mixed sessions, avoid prompts that both expose `team-action.json` files and
  say not to use them.
- Add compatibility tests for legacy artifact sessions and MCP-native sessions
  running against the same Team state.
- Include live-state migration or cleanup for active dispatches that were leased
  before the prompt/tool surface changed.

## Summary

I would proceed with the proposed intent-specific tool surface, but I would
tighten the design around the runtime contract before implementation. The core
open question is not tool naming; it is how multiple immediate MCP mutations
during one active dispatch map onto the current single post-wake action
application model.

If that is resolved with a dispatch-scoped lower-level action service,
well-defined idempotency, precise inbox atomicity, and a concrete audit model,
the migration should be viable and materially better for agents.
