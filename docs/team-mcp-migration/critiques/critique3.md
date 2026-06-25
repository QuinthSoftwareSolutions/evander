# Critique 3

This document reviews the proposed Team MCP migration design in:

- `team-mcp-action-surface.md`
- `agent-guidance-surface-inventory.md`
- `persistent-agent-instruction-replacements.md`
- `specialized-instruction-replacements.md`

Overall, I think the direction is right. Intent-specific tools are a better fit
than either `team-action.json` or a broad `team_submit_action` wrapper. The
design keeps Team semantics add-on-owned, preserves the active-dispatch guard,
and removes the brittle finish-wake concept. The main gaps I would close before
implementation are around idempotency, batch atomicity, progress accounting,
tool visibility, and removing migration-era negative instructions from
agent-facing text.

## Comments

### 1. Idempotency needs a stronger server-side contract

The current design makes `request_id` optional on every mutating tool. That
matches the current JSON contract in spirit, but MCP changes the failure mode:
an agent can make a tool call, lose the response, and retry during the same
wake. For append-like tools such as progress reports, questions, status
requests, chat replies, review requests, and operator escalations, retrying
without a stable key can duplicate state.

I would not rely on prompt guidance alone for this. Either:

- make `request_id` required on mutating tools, or
- have the gateway inject a stable per-tool-call idempotency key that the agent
  does not need to manage.

The idempotency record should store the request payload hash and the original
result. A repeated call with the same key and same payload should return the
same domain ids with an `already_applied` or equivalent flag. A repeated call
with the same key and a different payload should fail clearly.

This is especially important for batch tools, where a top-level retry after a
partial success can otherwise duplicate the successful siblings.

### 2. Batch and compound tools need explicit atomicity

The document currently mixes several atomicity models:

- `team_create_tasks` preserves current partial-apply behavior.
- `team_request_ephemeral_agents` sounds like one batch checked against
  `max_concurrent`.
- `team_submit_deliverable_for_review` is described as one repository update.
- per-tool `handled_inbox_item_ids` are intended to apply atomically with the
  primary action.

That can work, but each tool should declare one of:

- all-or-nothing
- partial apply with per-item results
- primary action plus inbox handling as one atomic unit

For new simplification tools, I would strongly prefer all-or-nothing unless
legacy parity requires partial application. If partial application is preserved,
the return shape needs item-scoped ids, item-scoped errors, and retry behavior
that is safe when only some items applied.

`team_create_tasks` is the riskiest example because graph planning often has
internal references between siblings. Partial application could leave an
agent-created plan in a surprising intermediate state unless item dependencies,
temporary aliases, and retry semantics are nailed down.

### 3. Progress accounting should be defined per tool

The action-surface document says no-progress accounting can be computed at
session completion by comparing Team state changes made during the dispatch.
That is close, but not precise enough. Some state writes should not count as
progress, especially `team_record_note`. Current runtime behavior is more like a
tool/action-specific progress flag than a generic "any state changed" check.

I would add an explicit `counts_as_progress` and, where needed,
`counts_as_context_activity` classification to the tool design. For example:

- `team_record_note`: writes audit state but does not count as progress.
- `team_mark_inbox_handled`: counts as progress.
- `team_send_progress_report`: counts as progress, but current behavior may not
  count it as context-rotation activity.
- failed validation: does not count as progress.
- accepted async intent, such as release request creation: counts as progress
  when the intent is recorded, not when the later workflow finishes.

Without this table, implementation could accidentally reset no-progress counts
on note-only wakes or fail to reset them for legitimate lightweight actions.

### 4. Tool visibility should be role- and wake-scoped where possible

The design says the surface should avoid a bloated generic tool, but the full
suite is still large. If every Team agent sees every Team tool all the time, the
context burden may still be higher than necessary.

I would add a design requirement that the gateway expose the smallest useful
tool set for the current session/wake class when feasible:

- normal persistent Team wake: persistent action tools relevant to the active
  lease
- Team Chat wake: chat reply plus normal action tools only if state mutation is
  allowed
- governance wake: governance tools, not persistent project-work tools
- ephemeral worker wake: `team_report_ephemeral_result` only, unless there is a
  specific reason to expose more
- inactive Team agent: no Team action tools, except governance tools when the
  agent is acting as governance manager

Validation still has to enforce authority, but hiding irrelevant tools is the
better agent experience and matches the goal of a minimal action surface.

### 5. Agent-facing guidance should drop migration-only negative instructions

The inline comments in `persistent-agent-instruction-replacements.md` are right
to push on this. Once agents are in the MCP-native world, they should not need
durable prompt text saying not to write `team-action.json`, not to pass
`dispatch_id`, or not to mutate Team state outside tools unless those mistakes
remain likely through some visible surface.

I would split these into two categories:

- Agent-facing replacement text: short, positive instructions about when to use
  tools.
- Migration/implementation notes: internal reminders that the old artifact
  paths and ids are removed from prompts and seeded files.

For example, "Use Team MCP tools for Team state changes" is useful. "Do not
write team-action.json" should probably live only in migration notes or legacy
fallback prompts, not in the steady-state prompt.

### 6. `team_create_case.team_id` may leak a cross-team concept into the normal surface

The action surface says mutating tools require an active Team lease, but
`team_create_case` includes optional `team_id`, "required only for teamless
agents." That is a smell in the normal persistent action surface.

If teamless agents are governance managers, they should use governance-scoped
tools. If there is another teamless case-creation path, it should be explicitly
named and scoped. Otherwise, remove `team_id` and infer the team from the active
Team lease like the other persistent-agent tools.

### 7. Communication tools should require non-empty content

Some communication tools currently allow all text fields to be omitted:

- `team_ask_question` has optional `question`, `message`, and `summary`.
- `team_send_progress_report` has optional `message` and `summary`.
- `team_request_status` has optional `question`.

This may match the loose JSON shape, but MCP validation is the chance to prevent
empty inbox noise. I would require at least one meaningful content field for
questions and progress reports. `team_request_status` can reasonably allow a
default status request, but that should be explicit in the tool description.

### 8. `team_update_task` exposes states agents probably should not set directly

`team_update_task.state` includes `accepted` and `rejected`, while the guidance
also says not to accept or reject someone else's delivered work with
`team_update_task`; use review tools.

That may preserve the current model, but as a tool surface it invites misuse.
If `accepted` and `rejected` are only legitimate through review cascades or
system paths, they should not be ordinary `team_update_task` enum values. If
there is a legitimate owner-only direct state transition, document the exact
case. Otherwise, keep `team_update_task` to normal work states and let review
tools own acceptance/rejection.

### 9. Action/audit records need a migration-level shape decision

The document says every mutating tool writes the same durable action/audit trail
as the current artifact path, and the stored action should be equivalent to the
JSON field it replaces. That is the right compatibility goal, but MCP changes
one completed dispatch from "one action record with many arrays" into
potentially many action records during one active dispatch.

Before implementation, define:

- whether each tool call creates its own `AgentActionRecord`
- whether session completion creates a dispatch summary record
- how failed MCP validations appear in the existing failed-action UI
- how multiple successful tool calls in one dispatch are grouped in timelines
- how no-progress accounting finds the set of tool calls belonging to the
  active dispatch

This is mostly an implementation seam, but the design should state it because it
affects compatibility with existing UI, attention, and recovery behavior.

### 10. Related-call grouping should not overload `request_id`

`specialized-instruction-replacements.md` suggests using a stable `request_id`
prefix for related calls. I would avoid making `request_id` serve both
idempotency and correlation.

If related tool calls need grouping, add a separate optional `correlation_id` or
`causation_id` accepted by mutating tools and copied into audit records. Keep
`request_id` unique to one intended tool operation. Prefix conventions are easy
for agents to apply inconsistently and hard for the server to validate.

### 11. Async intent tools should distinguish "recorded" from "completed"

The release, git promotion, and ephemeral-worker request tools intentionally
keep long-running effects asynchronous. Their return shapes should make that
clear so agents do not infer that the external work completed synchronously.

I would use result fields such as:

- `intent_recorded: true`
- `created_event_ids`
- `current_package_state`
- `next_action_hint`
- `async_effect_pending: true`

Tool descriptions should also say where later failures surface: inbox item,
release package state, recent feedback, governance agenda, or UI attention.

### 12. `available_team_mcp_tools` in prompt context is probably unnecessary

The normal prompt replacement says the context JSON may add
`available_team_mcp_tools` or rely on MCP discovery. I would avoid adding this
unless there is a concrete limitation in the MCP client. Duplicating tool lists
in prompt JSON can drift from actual registered tools and reintroduces some of
the same doc-sync problem this migration is meant to solve.

### 13. Keep `team_record_note`, but keep it clearly non-terminal

The current design handles this well: no finish tool, no required no-op call,
and `team_record_note` only for durable note text. I would keep checking future
prompt edits for wording that makes notes sound like a required completion step.
The note tool should not become `team_finish_wake` under another name.

## Things I Would Not Change

- Do not add a broad `team_submit_action` tool.
- Do not reintroduce a finish-wake tool.
- Keep governance tools separate from the persistent-agent action surface.
- Keep `team_report_ephemeral_result`; the inventory correctly identifies the
  ephemeral worker result file as a separate action surface.
- Keep deliverable-plus-review as a compound tool, provided its atomicity is
  explicitly all-or-nothing.
- Keep inbox handling available on primary action tools; closing the source
  inbox item in the same mutation is one of the main wins of moving to MCP.

## Suggested Design Additions

I would add three small tables to `team-mcp-action-surface.md` before
implementation:

1. Tool availability by wake/session type.
2. Tool atomicity and retry behavior.
3. Tool progress/no-progress classification.

Those tables would make the most error-prone runtime behavior explicit without
adding much agent-facing instruction.
