# Team MCP Action Surface Design

This document designs an MCP tool surface that can replace the persistent
agent-authored `/artifacts/team-action.json` contract without changing Team
semantics. It is a design document only; it does not define an implemented API.

The goal is not to expose one generic action blob through MCP. The goal is to
give agents small, intent-specific tools with synchronous validation, immediate
state updates, and built-in tool documentation, while preserving the repository
rules that currently validate and apply `team-action.json`.

## Scope

The tool surface below covers the current persistent action fields:

- `task_creations`
- `task_updates`
- `case_updates`
- `status_requests`
- `questions`
- `status_reports`
- `chat_replies`
- `operator_chat_messages`
- `operator_escalations`
- `stand_downs`
- `stand_down_revocations`
- `deliverables`
- `review_requests`
- `review_decisions`
- `goal_updates`
- `git_review_promotions`
- `git_feature_updates`
- `git_release_requests`
- `git_release_recoveries`
- `ephemeral_requests`
- `ephemeral_acceptances`
- `handled_inbox_item_ids`
- `notes`

The fields `schema_version`, `agent_id`, and `dispatch_id` are transport
metadata in the JSON file and should not be normal agent arguments in the MCP
surface.

Disposable ephemeral workers also currently report through their own
`/artifacts/team-action.json` shape. That worker-scoped action is covered by
`team_report_ephemeral_result` below.

## Shared Rules

### Caller Identity

All Team action tools infer the caller from the session-scoped MCP connection.
Agents do not pass `agent_id`, `session_id`, `dispatch_id`, or
`schema_version`.

The gateway/runtime resolves:

- calling `session_id`
- Team agent bound to the session
- active Team runtime lease
- active dispatch id
- current Team state revision

Mutating tools require an active Team lease for the caller unless explicitly
documented otherwise. A tool call from a session that is not the active lease
for that Team agent fails with a structured error and does not mutate state.

### Timing

The current JSON artifact applies after the agent finishes its wake. MCP action
tools should apply their state changes immediately during the wake. That means:

- invalid requests fail while the agent can still correct them
- created inbox items can mark recipients due immediately
- handled inbox items can be closed immediately
- deliverables, review requests, release packages, and promotion events can be
  recorded before the caller finishes the wake

Long-running external work still remains asynchronous where it is asynchronous
today. For example, a release request can immediately create a release package
and git promotion events, but the git consolidation/push effects still execute
through the workflow runtime.

### Wake Completion

Mutating action tools do not automatically end the caller's wake. The active
lease remains open until the normal session completion event closes it.

Agents do not call a finish tool. If an agent has no Team action to take, it
does not need to call any Team MCP tool. If it needs to leave a durable note, it
uses `team_record_note`.

No-progress accounting is computed at session completion by comparing the Team
state changes made during the dispatch. A dispatch with no progress-making tool
calls is recognized as no progress even when the agent called no Team tool at
all.

### Idempotency

Every mutating tool accepts optional `request_id`.

`request_id` is a caller-supplied idempotency key scoped to:

- tool name
- caller agent id
- active dispatch id

If omitted, the server may generate an action record id, matching the current
behavior where many JSON item ids are optional. For robust retries, agents
should provide a stable `request_id` when repeating a tool call after a transport
failure.

Existing domain ids such as `task_id`, `id`, `deliverable_id`,
`release_package_id`, and `assignment_id` keep their current semantics and
remain the natural idempotency keys for objects that already support explicit
ids.

### Optional Inbox Handling

Most mutating tools accept optional `handled_inbox_item_ids`.

When present, each listed inbox item must exist and belong to the caller. The
tool applies its primary action and marks those inbox items handled in the same
state update. This is the primary intended simplification over the JSON file:
agents can answer or act on an inbox item and close it in one call.

Tools that only mark inbox items handled still exist for cases where the agent
read or acted on an item without another state mutation.

### Return Shape

All tools return a structured result. Exact fields can vary by tool, but the
base shape should be:

```json
{
  "success": true,
  "applied": true,
  "revision": 123,
  "agent_id": "agent-1",
  "dispatch_id": "dispatch-abc",
  "errors": [],
  "handled_inbox_item_ids": [],
  "action_record_id": "action-..."
}
```

Failures return `success: false`, `applied: false`, and one or more actionable
errors. Validation failures should be precise enough for an agent to correct the
same call during the same wake.

### Audit

Every mutating tool writes the same durable action/audit trail as the current
artifact path. The canonical stored action should be equivalent to the JSON
field it replaces so existing UI, timeline, failed-action feedback, and runtime
attention behavior can continue to work during migration.

## Tool Surface

### `team_record_note`

Record a durable note for the caller's current Team dispatch.

Arguments:

| Name | Type | Required | Semantics |
| --- | --- | --- | --- |
| `body` | string | yes | Free-form note text. Replaces the old action-level `notes` field. |
| `handled_inbox_item_ids` | string[] | no | Inbox items to mark handled atomically after recording the note. |
| `request_id` | string | no | Idempotency key. |

Semantics:

- Records a Team action/audit record containing only the note and any handled
  inbox ids.
- Does not close the active dispatch/lease; normal session completion closes the
  wake.
- A note by itself is not delivery, review, release, or task progress. It is
  audit/handoff context. Session completion can still count the dispatch as no
  progress when no progress-making tool calls occurred.

### `team_mark_inbox_handled`

Mark one or more of the caller's inbox items handled.

Arguments:

| Name | Type | Required | Semantics |
| --- | --- | --- | --- |
| `inbox_item_ids` | string[] | yes | Replaces `handled_inbox_item_ids`. |
| `request_id` | string | no | Idempotency key. |

Semantics:

- Each inbox item must exist and have `recipient_agent_id` equal to the caller.
- Marks valid items `handled`.
- Counts as progress.
- Does not close the wake.

### `team_create_case`

Create a Team case.

Arguments:

| Name | Type | Required | Semantics |
| --- | --- | --- | --- |
| `id` | string | no | Explicit case id. Same as `case_updates[].id` for new cases. |
| `team_id` | string | no | Defaults to caller's team. Required only for teamless agents. |
| `project_id` | string or null | no | Optional project scope. |
| `title` | string | yes | Case title. |
| `description` | string | no | Defaults to empty string. |
| `status` | enum | no | `open`, `triage`, `in_progress`, `waiting`, `resolved`, `closed`. Defaults to `open`. |
| `priority` | enum | no | `low`, `normal`, `high`. Defaults to `normal`. |
| `owner_agent_id` | string or null | no | Optional case owner. |
| `source` | string or null | no | Source label. |
| `source_ref` | string or null | no | External source reference. |
| `tags` | string[] | no | Unique tags. |
| `metadata` | object | no | Case metadata object. |
| `task_ids` | string[] | no | Tasks to link to the case. |
| `handled_inbox_item_ids` | string[] | no | Inbox items to mark handled atomically. |
| `request_id` | string | no | Idempotency key. |

Semantics:

- The caller can create cases only for its own team.
- If `project_id` is set, the caller must be the team manager or assigned to the
  project.
- The case scope must allow the selected project and owner.
- Linked tasks must be linkable to the case under current case/task scope rules.
- Returns `case_id`.

### `team_update_case`

Update an existing Team case.

Arguments:

| Name | Type | Required | Semantics |
| --- | --- | --- | --- |
| `case_id` | string | yes | Existing case id. Replaces existing-case `case_updates[].case_id` or `id`. |
| `project_id` | string or null | no | New project scope. |
| `title` | string | no | New title. |
| `description` | string | no | New description. |
| `status` | enum | no | Case status. Closing stamps `closed_at`; reopening clears it. |
| `priority` | enum | no | `low`, `normal`, `high`. |
| `owner_agent_id` | string or null | no | New owner. |
| `source` | string or null | no | New source label. |
| `source_ref` | string or null | no | New source reference. |
| `tags` | string[] | no | Replaces tags. |
| `metadata` | object | no | Replaces metadata. |
| `task_ids` | string[] | no | Replaces linked tasks. |
| `handled_inbox_item_ids` | string[] | no | Inbox items to mark handled atomically. |
| `request_id` | string | no | Idempotency key. |

Semantics:

- Caller must be able to edit the case: owner, team manager, project lead, or
  supervisor of the case owner.
- Scope changes revalidate project/owner/task links.
- Returns `case_id`.

### `team_create_task`

Create a single task.

Arguments:

| Name | Type | Required | Semantics |
| --- | --- | --- | --- |
| `task_id` | string | no | Explicit task id. Replaces preferred `task_creations[].task_id`. |
| `owner_agent_id` | string | yes | Task owner. |
| `project_id` | string | yes | Project id. |
| `case_id` | string or null | no | Case link. |
| `priority` | integer | no | Non-negative priority. |
| `title` | string | yes | Task title. |
| `description` | string | yes | Task description. |
| `state` | enum | no | `pending`, `in_progress`, `blocked`, or `waiting_for_review`. Defaults to `pending`. |
| `parent_task_id` | string or null | no | Parent task. Required for agents lacking `create_root_task`. |
| `dependency_task_ids` | string[] | no | Same-project dependencies. |
| `handled_inbox_item_ids` | string[] | no | Inbox items to mark handled atomically. |
| `request_id` | string | no | Idempotency key. |

Semantics:

- Preserves current task creation authority rules.
- Roles with `create_root_task` can create top-level tasks.
- Other agents must create child tasks under their own active task.
- Target owner must be within the allowed team/project/supervision boundary.
- Parent/dependency tasks must belong to the same project and cannot create
  cycles.
- Returns `task_id`.

### `team_create_tasks`

Create multiple tasks in one graph-planning call.

Arguments:

| Name | Type | Required | Semantics |
| --- | --- | --- | --- |
| `tasks` | object[] | yes | Each item has the same fields as `team_create_task`, except `handled_inbox_item_ids` and `request_id`. |
| `handled_inbox_item_ids` | string[] | no | Inbox items to mark handled atomically. |
| `request_id` | string | no | Idempotency key for the batch. |

Semantics:

- Replaces multi-item `task_creations`.
- Preserves current batch graph validation so sibling-created tasks can depend on
  each other safely.
- Valid task creations apply; invalid items return item-scoped errors matching
  current partial-apply behavior unless implementation chooses stricter
  all-or-nothing semantics. To preserve current semantics, default to partial
  apply with per-item errors.
- Returns `created_task_ids`.

### `team_update_task`

Update task details, dependencies, or owner.

Arguments:

| Name | Type | Required | Semantics |
| --- | --- | --- | --- |
| `task_id` | string | yes | Existing task id. |
| `state` | enum | no | `pending`, `in_progress`, `blocked`, `waiting_for_review`, `finished`. Owner-only, except a superior may set `finished` on claimable subordinate work. |
| `priority` | integer | no | Owner-only. |
| `title` | string | no | Owner-only. |
| `description` | string | no | Owner-only. |
| `owner_agent_id` | string | no | Claim/reassign non-terminal subordinate work when permitted. |
| `dependency_task_ids` | string[] | no | Replace dependency edges when graph edit is permitted. |
| `handled_inbox_item_ids` | string[] | no | Inbox items to mark handled atomically. |
| `request_id` | string | no | Idempotency key. |

Semantics:

- State, priority, title, and description remain owner-only, except a superior
  may set `state: finished` on subordinate work they could otherwise claim.
- A superior may claim or reassign non-terminal subordinate tasks within the
  same project/team rules.
- Dependency edits are allowed for owner, supervisor, project lead, or team
  manager and must pass same-project/cycle validation.
- Do not use this to accept/reject deliverables; use review tools.
- Returns `task_id`.

### `team_request_status`

Ask a direct subordinate for status.

Arguments:

| Name | Type | Required | Semantics |
| --- | --- | --- | --- |
| `recipient_agent_id` | string | yes | Direct subordinate. |
| `task_id` | string or null | no | Optional task context. |
| `question` | string | no | Status question. |
| `response_requested` | boolean | no | Defaults to `true`. |
| `priority` | enum | no | `low`, `normal`, `high`. Defaults to `normal`. |
| `payload` | object | no | Additional payload fields currently allowed by the JSON item. |
| `handled_inbox_item_ids` | string[] | no | Inbox items to mark handled atomically. |
| `request_id` | string | no | Idempotency key. |

Semantics:

- Recipient must be a direct subordinate.
- Creates a `STATUS_REQUEST` inbox item.
- Marks the recipient due according to priority/wake rules.
- Returns `inbox_item_id`.

### `team_ask_question`

Ask the caller's direct superior a question.

Arguments:

| Name | Type | Required | Semantics |
| --- | --- | --- | --- |
| `recipient_agent_id` | string or null | no | Defaults to caller's `reports_to_agent_id`; if supplied, must equal it. |
| `task_id` | string or null | no | Optional task context. |
| `question` | string | no | Question text. |
| `message` | string | no | Message text, preserved in payload. |
| `summary` | string | no | Summary, preserved in payload. |
| `response_requested` | boolean | no | Defaults to `true`. |
| `priority` | enum | no | `low`, `normal`, `high`. Defaults to `normal`. |
| `payload` | object | no | Additional payload fields currently allowed by the JSON item. |
| `handled_inbox_item_ids` | string[] | no | Inbox items to mark handled atomically. |
| `request_id` | string | no | Idempotency key. |

Semantics:

- Replaces `questions`.
- Communication is upward only; no skip-level messaging.
- Top-level managers cannot use this to reach the operator and receive a
  corrective error directing them to `team_escalate_operator`.
- Creates a `QUESTION` inbox item.
- Returns `inbox_item_id`.

### `team_send_progress_report`

Send an upward progress report.

Arguments:

| Name | Type | Required | Semantics |
| --- | --- | --- | --- |
| `recipient_agent_id` | string or null | no | Defaults to caller's `reports_to_agent_id`; if supplied, must equal it. |
| `task_id` | string or null | no | Optional task context. |
| `message` | string | no | Report body. |
| `summary` | string | no | Summary. |
| `response_requested` | boolean | no | Defaults to `false`. |
| `priority` | enum | no | `low`, `normal`, `high`. Defaults to `low`. |
| `payload` | object | no | Additional payload fields currently allowed by the JSON item. |
| `handled_inbox_item_ids` | string[] | no | Inbox items to mark handled atomically. |
| `request_id` | string | no | Idempotency key. |

Semantics:

- Replaces `status_reports`.
- Communication is upward only; no skip-level messaging.
- Creates a `PROGRESS_REPORT` inbox item.
- Returns `inbox_item_id`.

### `team_reply_to_chat`

Reply to a specific human operator Team Chat message.

Arguments:

| Name | Type | Required | Semantics |
| --- | --- | --- | --- |
| `conversation_id` | string | yes | Conversation from the current Team context. |
| `message_id` | string | yes | Operator message being answered. |
| `body` | string | yes | Reply body. |
| `summary` | string or null | no | Stored in reply metadata. |
| `handled_inbox_item_ids` | string[] | no | Inbox items to mark handled atomically when the chat asked for Team work. |
| `request_id` | string | no | Idempotency key. |

Semantics:

- Replaces `chat_replies`.
- Conversation must belong to the caller.
- Source message must be in that conversation and must be an operator message.
- Marks the source operator message `replied`.
- Creates an agent chat message.
- If the operator message asks for a Team state change, the agent should call the
  relevant Team action tool and include the same inbox handling in that call or
  this one.
- Returns `chat_message_id` and `replied_message_id`.

### `team_send_operator_chat_message`

Send a new Team Chat message to a known human operator.

Arguments:

| Name | Type | Required | Semantics |
| --- | --- | --- | --- |
| `recipient_operator_user_id` | string | yes | Known operator user id from Team context. |
| `body` | string | yes | Message body. |
| `summary` | string or null | no | Stored in metadata. |
| `handled_inbox_item_ids` | string[] | no | Inbox items to mark handled atomically. |
| `request_id` | string | no | Idempotency key. |

Semantics:

- Replaces `operator_chat_messages`.
- Recipient must be listed in current known Team Chat operator participants.
- Creates or reuses the caller/operator conversation.
- Returns `chat_message_id` and `conversation_id`.

### `team_escalate_operator`

Escalate from the top-level manager to the operator or governance layer.

Arguments:

| Name | Type | Required | Semantics |
| --- | --- | --- | --- |
| `title` | string | yes | Escalation title. |
| `message` | string | yes | Escalation message. |
| `urgency` | string | no | Defaults to `normal`. |
| `project_id` | string or null | no | Optional project context. |
| `task_id` | string or null | no | Optional task context. |
| `handled_inbox_item_ids` | string[] | no | Inbox items to mark handled atomically. |
| `request_id` | string | no | Idempotency key. |

Semantics:

- Replaces `operator_escalations`.
- Only the top-level team manager may call it.
- If the Team is not governance-escalated, the Team must have
  `operator_communication_target_id`; the tool creates the audit inbox item and
  emits `team.operator.escalation_requested`.
- If the Team escalates to governance, the tool creates governance agenda items
  and wakes the relevant governance managers instead.
- Returns `operator_escalation_event_ids` or
  `created_governance_agenda_item_ids`.

### `team_stand_down_agent`

Stand down a direct subordinate and that subordinate's subtree.

Arguments:

| Name | Type | Required | Semantics |
| --- | --- | --- | --- |
| `agent_id` | string | yes | Direct subordinate to hold. |
| `reason` | string | yes | Required stand-down reason. |
| `handled_inbox_item_ids` | string[] | no | Inbox items to mark handled atomically. |
| `request_id` | string | no | Idempotency key. |

Semantics:

- Replaces `stand_downs`.
- Target must be a direct subordinate in the caller's team and not archived.
- Creates a superior-sourced stand-down.
- Held agents are skipped by future Team wake leasing.
- Already-running dispatches from held agents cannot apply non-empty Team
  actions.
- Returns `stand_down_id`.

### `team_revoke_stand_down`

Revoke a superior stand-down previously set by the caller.

Arguments:

| Name | Type | Required | Semantics |
| --- | --- | --- | --- |
| `agent_id` | string | yes | Direct subordinate whose superior hold should be revoked. |
| `handled_inbox_item_ids` | string[] | no | Inbox items to mark handled atomically. |
| `request_id` | string | no | Idempotency key. |

Semantics:

- Replaces `stand_down_revocations`.
- Target must be a direct subordinate.
- Only superior-sourced stand-downs set by the caller are revoked.
- Operator stand-downs cannot be revoked by this tool.
- Returns `revoked_stand_down_ids`.

### `team_submit_deliverable`

Record finished work for a task owned by the caller.

Arguments:

| Name | Type | Required | Semantics |
| --- | --- | --- | --- |
| `id` | string | no | Explicit deliverable id. |
| `task_id` | string | yes | Caller-owned task. |
| `summary` | string | yes | Deliverable summary. |
| `supersedes_deliverable_id` | string or null | no | Required when responding to a `review_rejected` inbox item. |
| `artifact_paths` | string[] | no | Paths relative to session `/artifacts`; published before recording the deliverable. |
| `artifact_refs` | string[] | no | Existing artifact refs. |
| `artifact_space_ref` | string or null | no | Appended to `artifact_refs` when supplied. |
| `git_refs` | object | no | `{ "agent_branch"?, "commit_sha"? }`. |
| `verification` | object | no | `{ "status": "passed" \| "failed" \| "partial" \| "not_run" \| "unknown", "summary"?, "evidence_refs"? }`. |
| `handled_inbox_item_ids` | string[] | no | Inbox items to mark handled atomically. |
| `request_id` | string | no | Idempotency key. |

Semantics:

- Replaces `deliverables`.
- Task must be owned by the caller.
- `artifact_paths`, when present and non-empty, are published to the host
  artifact store before state mutation. Failed publication rejects this
  deliverable item.
- `artifact_space_ref` is preserved by appending it to `artifact_refs`.
- `git_refs.agent_branch` must be a valid branch name.
- `git_refs` are provenance only; they do not publish a review branch. Call
  `team_publish_review_branch` separately only when a durable review branch is
  needed for reviewer access, feature promotion, release sequencing, or an
  explicit superior request.
- `verification.status` records what was or was not checked. Use `not_run` when
  test tooling, services, or credentials were unavailable and put the reason in
  `verification.summary`.
- Sets deliverable state to pending and task state to `waiting_for_review`.
- Creates a `DELIVERABLE_NOTICE` inbox item for the caller's direct superior
  when one exists.
- Returns `deliverable_id`, created inbox ids, published artifact refs, optional
  verification, and `next_action_hints`.

### `team_submit_deliverable_for_review`

Record a deliverable and create one or more review requests in one call.

Arguments:

| Name | Type | Required | Semantics |
| --- | --- | --- | --- |
| `deliverable` | object | yes | Same arguments as `team_submit_deliverable`, except `handled_inbox_item_ids` and `request_id`. |
| `reviews` | object[] | yes | Each item has `id`?, `reviewer_agent_id`, `review_profile_id`?, and `instructions`?. |
| `handled_inbox_item_ids` | string[] | no | Inbox items to mark handled atomically. |
| `request_id` | string | no | Idempotency key. |

Semantics:

- Intentional simplification over the JSON flow.
- Equivalent to `team_submit_deliverable` followed immediately by
  `team_request_review` for each requested reviewer, in one repository update.
- Avoids an extra wake where a superior notices a deliverable and then asks for
  review.
- Preserves all deliverable and review request validation rules.
- Returns `deliverable_id` and `created_review_request_ids`.

### `team_request_review`

Request review of an existing deliverable.

Arguments:

| Name | Type | Required | Semantics |
| --- | --- | --- | --- |
| `id` | string | no | Explicit review request id. |
| `deliverable_id` | string | yes | Deliverable to review. |
| `reviewer_agent_id` | string | yes | Same-team reviewer. |
| `review_profile_id` | string or null | no | Optional active review profile. |
| `instructions` | string or null | no | Reviewer instructions. |
| `handled_inbox_item_ids` | string[] | no | Inbox items to mark handled atomically. |
| `request_id` | string | no | Idempotency key. |

Semantics:

- Replaces `review_requests`.
- Caller must be the deliverable producer, supervise the producer, be project
  lead, or be team manager.
- Reviewer must be in the same team.
- Review profile must be active, in the same team, applicable to the
  deliverable's project, and applicable to the reviewer's role when constrained.
- Creates a review request and a normal-priority `STATUS_REQUEST` inbox item for
  the reviewer.
- Returns `review_request_id` and created inbox ids.

### `team_decide_review`

Accept, reject, or block a review assigned to the caller.

Arguments:

| Name | Type | Required | Semantics |
| --- | --- | --- | --- |
| `review_request_id` | string | yes | Review request assigned to the caller. |
| `state` | enum | yes | `accepted`, `rejected`, or `blocked`. |
| `findings` | string[] | no | Review findings. |
| `criteria` | object[] | no | `{ "criterion_id", "state", "finding"? }`; state is `passed`, `failed`, or `skipped`. |
| `finding_resolutions` | object[] | no | `{ "finding_id", "resolution", "evidence_refs"? }` for carried findings owned by this review profile or legacy reviewer role. |
| `override_routing_gap_reason` | string or null | no | Manager-fallback escape hatch when required by current runtime rules. |
| `handled_inbox_item_ids` | string[] | no | Inbox items to mark handled atomically. |
| `request_id` | string | no | Idempotency key. |

Semantics:

- Replaces `review_decisions`.
- Only the assigned reviewer may decide.
- Criterion ids must exist on the attached review profile when criteria are
  supplied.
- An accepted profiled review must supply every required criterion with state
  `passed`; optional criteria may be omitted.
- Rejected/blocked findings survive on superseding deliverables. Acceptance
  requires this review to resolve every open finding it owns, and the
  deliverable remains pending until all carried findings are resolved.
- `accepted` marks the review accepted; the deliverable becomes accepted only
  when all attached reviews are accepted, and the task becomes accepted only when
  all active deliverables for that task are accepted.
- `rejected` marks the review and deliverable rejected, moves the task back to
  `in_progress`, and creates a `review_rejected` inbox item for rework carrying
  `supersedes_deliverable_id`.
- `blocked` marks the review, deliverable, and task blocked.
- Returns `review_request_id`, updated deliverable/task ids, and created inbox
  ids when rejection creates rework.

### `team_update_goal`

Update explicit project goal state.

Arguments:

| Name | Type | Required | Semantics |
| --- | --- | --- | --- |
| `project_id` | string | yes | Project id. |
| `goal_id` | string | yes | Goal id on the project. |
| `state` | enum | yes | `pending`, `in_progress`, `accepted`, `rejected`, or `blocked`. |
| `description` | string | no | Accepted for parity with the JSON shape; current semantics update state only. |
| `handled_inbox_item_ids` | string[] | no | Inbox items to mark handled atomically. |
| `request_id` | string | no | Idempotency key. |

Semantics:

- Replaces `goal_updates`.
- Only the project lead or team manager may update goals.
- Goal acceptance remains explicit and is never inferred from text.
- Recalculates project status.
- Returns `goal_id`.

### `team_publish_review_branch`

Promote a completed assignment or persistent deliverable to a durable review
branch.

Arguments:

| Name | Type | Required | Semantics |
| --- | --- | --- | --- |
| `assignment_id` | string | conditional | Ephemeral assignment to promote. Required for ephemeral promotion shape. |
| `deliverable_id` | string | conditional | Deliverable to promote. Required for persistent promotion shape; optional with `assignment_id`. |
| `source_branch` | string or null | no | Persistent deliverable source branch override. |
| `commit_sha` | string or null | no | Persistent deliverable commit override. |
| `handled_inbox_item_ids` | string[] | no | Inbox items to mark handled atomically. |
| `request_id` | string | no | Idempotency key. |

Semantics:

- Replaces `git_review_promotions`.
- Exactly one promotion shape must be valid:
  - Ephemeral: `assignment_id`, optional `deliverable_id`.
  - Persistent: `deliverable_id`, optional `source_branch` or `commit_sha`.
- Ephemeral promotion requires the assignment to belong to the caller, be
  completed or accepted, and have an `agent_branch`.
- Persistent promotion requires the caller to be the producer, supervise the
  producer, be project lead, or be team manager.
- Project Git config is required.
- Records a `review` git promotion and emits the existing promotion event.
- Returns `git_promotion_event_id` and target review branch.
- This is not required merely because `team_submit_deliverable` included
  `git_refs`; use it only when the branch must be promoted for review, feature,
  or release flow.

### `team_update_feature_branch`

Consolidate review branches into a feature branch for a task.

Arguments:

| Name | Type | Required | Semantics |
| --- | --- | --- | --- |
| `task_id` | string | yes | Task whose feature branch should be updated. |
| `source_review_branches` | string[] | yes | Review branches to consolidate. |
| `feature_branch` | string | no | Target feature branch override. Defaults to deterministic Team feature branch. |
| `handled_inbox_item_ids` | string[] | no | Inbox items to mark handled atomically. |
| `request_id` | string | no | Idempotency key. |

Semantics:

- Replaces `git_feature_updates`.
- Caller must own the task or supervise the owner.
- Project Git config is required.
- Branch names are validated before any git work.
- Every source branch must map to exactly one accepted current deliverable and
  is pinned to the immutable commit accepted by its review.
- Records a `feature` git promotion and emits the existing promotion event.
- Returns `git_promotion_event_id` and target feature branch.

### `team_request_release`

Create a release package from accepted deliverables.

Arguments:

| Name | Type | Required | Semantics |
| --- | --- | --- | --- |
| `id` | string | no | Explicit release package id. |
| `project_id` | string | yes | Project id. |
| `deliverable_ids` | string[] | yes | Accepted deliverables to release. |
| `release_branch` | string or null | no | Release branch override. |
| `canonical_branch` | string or null | no | Canonical branch override. |
| `summary` | string or null | no | Release summary. |
| `verification_waivers` | object[] | no | `{ "deliverable_id", "reason" }` for partial, not-run, unknown, or missing verification. Only a project lead or top-level team manager may supply agent waivers. |
| `handled_inbox_item_ids` | string[] | no | Inbox items to mark handled atomically. |
| `request_id` | string | no | Idempotency key. |

Semantics:

- Replaces `git_release_requests`.
- Only the project lead, team manager, or a same-project role with
  `request_release` may request a release.
- Every deliverable must belong to the project and be accepted.
- Failed verification is never waivable. Other incomplete verification states
  require an audited waiver from authorized leadership (or an authenticated
  operator through the Admin API).
- Accepted Git reviews, feature commits, and reviewed path manifests are pinned;
  a moved branch tip or path outside the reviewed union hard-blocks the release
  and cannot be waived.
- The consolidated release commit must pass the project's configured release
  verification commands and release-review profiles before canonical push.
- The repository derives source branches from deliverable `git_refs`; agents do
  not provide `source_branch`, `target_branch`, or `commit_sha` here.
- Do not use this to retry an existing blocked package.
- May create prerequisite feature promotions immediately when accepted
  deliverables carry review/agent branches.
- Pure artifact releases remain supported and do not require project Git config.
- Cannot be combined with `team_update_feature_branch` in one atomic tool call;
  this preserves the current sequence rule that release requests stand alone.
- Returns `release_package_id`, promotion event ids, and next action hints.

### `team_recover_release`

Resume a blocked release package after a failed release promotion has been
manually resolved and pushed to an `agents/...` branch.

Arguments:

| Name | Type | Required | Semantics |
| --- | --- | --- | --- |
| `release_package_id` | string | yes | Existing blocked release package. |
| `agent_branch` | string | yes | Recovery branch under the allowed agent branch shape. |
| `commit_sha` | string | yes | Expected commit sha to publish. |
| `summary` | string or null | no | Recovery summary. |
| `handled_inbox_item_ids` | string[] | no | Inbox items to mark handled atomically. |
| `request_id` | string | no | Idempotency key. |

Semantics:

- Replaces `git_release_recoveries`.
- Caller must have the same release-request permission for the project.
- Release package must be `blocked`.
- Project Git config is required.
- A failed release promotion must exist for the package.
- Emits the existing release recovery event.
- Returns `release_package_id`.

### `team_request_ephemeral_agent`

Request one disposable worker assignment.

Arguments:

| Name | Type | Required | Semantics |
| --- | --- | --- | --- |
| `id` | string | no | Explicit ephemeral assignment id. |
| `task_id` | string | yes | Caller-owned task to delegate. |
| `worker_session_id` | string | no | Explicit worker session id, mapped to the current `ephemeral_requests[].session_id`; generated when omitted. |
| `title` | string | yes | Assignment title. |
| `prompt` | string | yes | Worker prompt. |
| `model` | string or null | no | Worker model override. |
| `agent_provider` | string or null | no | Worker provider override. |
| `execution_backend` | string or null | no | Defaults to `container_worker_vm`. |
| `container_image` | string or null | no | Container image override. |
| `allowed_tools` | string[] | no | Worker allowed tools. |
| `ttl_hours` | integer | no | Worker TTL. |
| `directory_path` | string or null | no | Directory scope. |
| `metadata` | object | no | Assignment metadata. |
| `handled_inbox_item_ids` | string[] | no | Inbox items to mark handled atomically. |
| `request_id` | string | no | Idempotency key. |

Semantics:

- Replaces one item in `ephemeral_requests`.
- Caller must own the task and be assigned to the project.
- Caller must have `ephemeral_policy.max_concurrent > 0`.
- Active assignments cannot exceed `max_concurrent`.
- Creates an `EphemeralAssignment` in `requested` state and emits the existing
  requested event for the workflow runtime to spawn the worker.
- Returns `assignment_id` and event payload.

### `team_request_ephemeral_agents`

Request multiple disposable worker assignments with one concurrency check.

Arguments:

| Name | Type | Required | Semantics |
| --- | --- | --- | --- |
| `assignments` | object[] | yes | Each item has the same fields as `team_request_ephemeral_agent`, except `handled_inbox_item_ids` and `request_id`. |
| `handled_inbox_item_ids` | string[] | no | Inbox items to mark handled atomically. |
| `request_id` | string | no | Idempotency key for the batch. |

Semantics:

- Replaces multi-item `ephemeral_requests`.
- Preserves current batch behavior where the whole request set is checked
  against `max_concurrent`.
- Returns `created_ephemeral_assignment_ids` and event payloads.

### `team_accept_ephemeral_result`

Accept a completed ephemeral assignment and turn it into a deliverable owned by
the parent persistent agent.

Arguments:

| Name | Type | Required | Semantics |
| --- | --- | --- | --- |
| `assignment_id` | string | yes | Ephemeral assignment id. |
| `deliverable_id` | string | no | Explicit deliverable id for accepted work. |
| `handled_inbox_item_ids` | string[] | no | Inbox items to mark handled atomically. |
| `request_id` | string | no | Idempotency key. |

Semantics:

- Replaces accepted `ephemeral_acceptances`.
- Assignment must belong to the caller and be completed, accepted,
  cleanup-pending, or cleaned up.
- Creates a deliverable on the assignment task using the assignment summary and
  artifact refs.
- Sets the assignment state to accepted and the task to `waiting_for_review`.
- Returns `assignment_id` and `deliverable_id`.

### `team_reject_ephemeral_result`

Reject an ephemeral assignment result.

Arguments:

| Name | Type | Required | Semantics |
| --- | --- | --- | --- |
| `assignment_id` | string | yes | Ephemeral assignment id. |
| `rejection_reason` | string or null | no | Stored as assignment error. |
| `handled_inbox_item_ids` | string[] | no | Inbox items to mark handled atomically. |
| `request_id` | string | no | Idempotency key. |

Semantics:

- Replaces rejected `ephemeral_acceptances`.
- Assignment must belong to the caller.
- Sets assignment state to rejected and stores the reason.
- Returns `assignment_id`.

### `team_report_ephemeral_result`

Report the result of a disposable ephemeral worker assignment.

Arguments:

| Name | Type | Required | Semantics |
| --- | --- | --- | --- |
| `assignment_id` | string | no | Explicit ephemeral assignment id. When omitted, the assignment is inferred from the caller session. |
| `summary` | string | conditional | Required for successful completion; result summary stored on the assignment. |
| `artifact_refs` | string[] | no | Published artifact refs produced by the worker. |
| `agent_branch` | string | no | Worker branch. When supplied, must equal deterministic `agents/<session-id>/<assignment-id>`. |
| `commit_sha` | string | no | Commit produced by the worker. |
| `error` | string | no | Marks the assignment failed when set. |
| `request_id` | string | no | Idempotency key. |

Semantics:

- Replaces the ephemeral worker's own `/artifacts/team-action.json` result file.
- This tool is worker-scoped, not parent-persistent-agent scoped.
- The assignment is resolved from `assignment_id` or from the caller session.
- On success, records `summary`, `artifact_refs`, optional `agent_branch`, and
  optional `commit_sha`, marks the assignment completed, creates the existing
  low-priority deliverable notice for the parent, and emits the existing
  ephemeral-completed event.
- When `error` is set, marks the assignment failed and stores the error.
- `agent_branch`, when supplied, must match the deterministic ephemeral branch
  name for the assignment; mismatches fail the assignment.
- The current ephemeral prompt's `task_notes` field has no repository effect
  today. It should not be part of this tool unless implementation intentionally
  adds audit storage for it.
- Returns `assignment_id`, `state`, created inbox ids, and the completion event
  payload when successful.

## Existing Governance MCP Tools

The existing governance tools remain outside the persistent action file
replacement but should stay part of the Team MCP surface:

- `team_create_operator_directive`
- `team_mark_governance_agenda_handled`

They are governance-manager scoped tools and should not be merged into the
persistent-agent action surface. They already model operator/governance
coordination as durable Team state instead of ad hoc action file writes.

## Coverage Map

| Current JSON field | MCP replacement |
| --- | --- |
| `schema_version` | Not an argument; tool schema version is owned by MCP registration. |
| `agent_id` | Inferred from session-scoped MCP caller. |
| `dispatch_id` | Inferred from active Team lease. |
| `case_updates` | `team_create_case`, `team_update_case`. |
| `task_creations` | `team_create_task`, `team_create_tasks`. |
| `task_updates` | `team_update_task`. |
| `status_requests` | `team_request_status`. |
| `questions` | `team_ask_question`. |
| `status_reports` | `team_send_progress_report`. |
| `chat_replies` | `team_reply_to_chat`. |
| `operator_chat_messages` | `team_send_operator_chat_message`. |
| `operator_escalations` | `team_escalate_operator`. |
| `stand_downs` | `team_stand_down_agent`. |
| `stand_down_revocations` | `team_revoke_stand_down`. |
| `deliverables` | `team_submit_deliverable`, `team_submit_deliverable_for_review`. |
| `review_requests` | `team_request_review`, `team_submit_deliverable_for_review`. |
| `review_decisions` | `team_decide_review`. |
| `goal_updates` | `team_update_goal`. |
| `git_review_promotions` | `team_publish_review_branch`. |
| `git_feature_updates` | `team_update_feature_branch`. |
| `git_release_requests` | `team_request_release`. |
| `git_release_recoveries` | `team_recover_release`. |
| `ephemeral_requests` | `team_request_ephemeral_agent`, `team_request_ephemeral_agents`. |
| `ephemeral_acceptances` | `team_accept_ephemeral_result`, `team_reject_ephemeral_result`. |
| Ephemeral worker result action file | `team_report_ephemeral_result`. |
| `handled_inbox_item_ids` | `team_mark_inbox_handled` or per-tool `handled_inbox_item_ids`. |
| `notes` | `team_record_note`. |

## Intentional Simplifications

These are semantic simplifications relative to the file transport, not changes
to Team authority rules:

1. Agents no longer pass `agent_id`, `dispatch_id`, or `schema_version`.
2. Each action validates synchronously and returns corrective errors immediately.
3. Inbox items can be marked handled in the same call that answers or acts on
   them.
4. Deliverable submission and review request creation can be one compound call.
5. Ephemeral acceptance and deliverable creation can be one explicit accept call.
6. Release requests can create release packages and prerequisite promotion
   events during the wake rather than waiting for the action artifact after
   completion.
7. Ephemeral workers report results through `team_report_ephemeral_result`
   instead of writing their own action artifact.
8. Notes-only completion uses `team_record_note` instead of writing a
   syntactically valid no-op JSON artifact. Pure no-op completion needs no Team
   MCP tool call.

## Non-Goals

- Do not expose a broad `team_submit_action` tool as the primary interface.
- Do not move Team-specific MCP tools into core control-plane enums or policy
  hooks.
- Do not let agents bypass Team repository authorization rules.
- Do not let mutating tools run outside the caller's active Team lease unless a
  tool is explicitly governance/operator scoped.
- Do not make long-running git or worker execution synchronous inside MCP.
- Do not remove the JSON artifact path until the MCP surface is implemented,
  verified, and deployed to agent prompts/configuration.
