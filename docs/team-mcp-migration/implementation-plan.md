# Team MCP Migration Implementation Plan

This is the implementation plan for replacing the Team Add-on
`/artifacts/team-action.json` action surface with Team-owned MCP tools.

This plan is intended to be self-contained for implementation. The neighboring
Team MCP migration design files are background provenance only; where they
disagree with this document, this document follows the post-critique design
decisions incorporated below.

The plan below is organized as vertical slices that can be assigned to separate
coding agents. Slices name dependencies explicitly. A slice may start in parallel
only when its listed dependencies are either complete or its agent is working
against an agreed stub/interface from that dependency.

## Non-Negotiable Constraints

- Team-specific behavior stays in `src/evander_team_addon`.
- Do not add Team MCP tools to core `ToolName`, core policy enums, core gateway
  tool functions, or core control-plane authorization branches.
- Core changes are allowed only when they are generic add-on infrastructure, such
  as exposing the already-existing ambient MCP caller session to all add-ons.
  Any such change must contain no Team names and must be useful to other add-ons.
- There is no long-term legacy action-file mode. The implementation is a clean
  cut from action artifacts to MCP tools.
- Normal Team prompts must not mention `team-action.json`, `schema_version`,
  `agent_id`, or `dispatch_id`.
- Team MCP tools infer caller identity and dispatch from the MCP connection and
  active Team lease. Temporary explicit identity parameters must reject conflicts
  with the ambient caller.
- Tool calls mutate Team state immediately and atomically per tool call. They do
  not close the active dispatch lease.
- Session completion closes dispatches and updates no-progress state from the
  dispatch activity ledger.
- Hot MCP commands should not keep using the current whole-state rewrite path as
  their long-term write model.

## Current Code Surfaces

The current code already has the right ownership boundary:

- Add-on MCP registration: `src/evander_team_addon/mcp.py`
- Add-on state and semantics: `src/evander_team_addon/repository.py`,
  `src/evander_team_addon/models.py`, `src/evander_team_addon/sqlite_store.py`
- Team runtime, prompts, workflow providers: `src/evander_team_addon/providers.py`
- Built-in runtime workflow pack: `src/evander_team_addon/workflow_packs.py` plus
  checked-in `config/workflow-packs/team-runtime.v1.json`
- Focused tests: `tests/test_team_addon_mcp.py`,
  `tests/test_team_addon_repository.py`, `tests/test_team_addon_runtime.py`,
  `tests/test_team_addon_templates.py`, `tests/test_workflow_packs.py`,
  `tests/test_addons.py`, `tests/test_workflow_editor_addon_catalog.py`

The core gateway already has generic ambient-session mechanics and add-on MCP
registration. If a new generic helper is needed, expose that capability for all
add-ons rather than adding Team-specific core code.

## Resolved Product Decisions

These decisions are part of this plan and should not be re-opened by individual
implementation slices unless the user explicitly changes the product direction.

1. `team_submit_deliverable_for_review` suppresses the normal superior
   `DELIVERABLE_NOTICE` because explicit review requests are the routing signal.
   It must not create a pending notice that triggers the extra superior wake.
2. `team_create_case` does not accept `team_id` on the normal active-lease path.
   The caller's team comes from the active lease. Any future teamless case
   creation must be a separately scoped operator, governance, or Admin path.
3. `team_update_task.state` accepts only `pending`, `in_progress`, `blocked`,
   `waiting_for_review`, and `finished`. `accepted` and `rejected` are review or
   system transitions, not ordinary task-update arguments.
4. Question, status, and progress tools do not accept generic `payload`.
   Assignment metadata is not part of the first-pass MCP schema unless the
   implementation documents a bounded worker-start consumer and validates the
   exact shape.
5. `team_request_status`, `team_ask_question`, `team_send_progress_report`,
   `team_send_operator_chat_message`, and `team_escalate_operator` require
   non-empty meaningful body text. There is no implicit default-message rule.
6. Team deliverable tools own host-side publication of `artifact_paths`, with
   path safety checks, idempotent replay, and cleanup or orphan tracking when
   publication succeeds but final state mutation fails.
7. Related tool calls use optional `correlation_id` or `causation_id` for
   grouping. `request_id` is never a prefix or grouping mechanism.
8. New batch tools are all-or-nothing by default. There is no partial batch mode
   in this migration.

## Shared Tool Contract

Caller identity:

- Normal persistent Team tools infer `session_id`, Team agent, active runtime,
  active dispatch id, and wake/session class from the MCP connection and active
  Team lease.
- Normal persistent Team tools must not accept `session_id`, `agent_id`,
  `dispatch_id`, or `schema_version` as agent-facing arguments.
- Existing governance tools move onto the same ambient-session helper. During
  refactor, any temporary explicit identity argument must reject conflicts with
  the ambient caller.
- Ephemeral worker calls infer the worker assignment from the ambient worker
  session when possible. An explicit `assignment_id` is allowed only when it
  matches the ambient assignment.

Timing and concurrency:

- A successful tool call mutates Team state immediately during the wake. Tool
  calls do not close the active dispatch lease.
- Session completion closes the active dispatch and computes progress from the
  dispatch activity ledger.
- Each tool call is a transaction boundary. Earlier successful tool calls remain
  applied if a later call fails validation.
- Agent-facing tools do not use global `expected_revision`. Command methods must
  lock, re-read the relevant current rows, validate current semantic
  dependencies, mutate only the scoped rows, append activity/audit/outbox rows,
  bump Team revision as needed, and commit.

Idempotency and correlation:

- `request_id` is the idempotency key for one intended operation. The storage key
  is tool name, caller identity, dispatch or worker scope, and request id.
- The normalized payload digest is stored as the checked payload identity, not as
  part of the uniqueness key. Same key plus same digest returns the original
  result. Same key plus different digest fails without mutation.
- Create, append, message, and async-intent tools require `request_id` unless an
  explicit natural domain id fully prevents duplication.
- Pure updates may omit `request_id` when object id plus current-state
  preconditions are sufficient to prevent duplicate effects.
- Related calls use optional `correlation_id` or `causation_id`, copied to
  activity/audit records.

Inbox handling:

- Tools that accept `handled_inbox_item_ids` must mark those inbox items handled
  atomically with the primary action.
- Each handled inbox item must still exist, be pending, and belong to the caller.
- If any listed inbox item fails validation, the whole tool call fails without
  partial handling.

Return and error shape:

- All tools return structured results with at least `success`, `applied`,
  `progress_made`, `revision`, `activity_id`, `action_record_id` where one
  exists, created or updated domain ids, and `errors`.
- Async-intent tools also return `intent_recorded`, `async_effect_pending`,
  created intent or event ids, current package/assignment state, and next-action
  hints where applicable. They must not claim external work completed
  synchronously.
- Validation failures return `success: false`, `applied: false`,
  `progress_made: false`, no domain mutation, and correction context containing
  the relevant current state such as owner, assignee, terminal state, current
  review state, dependency graph facts, or failed precondition reason.
- Failed synchronous validation is visible in activity/audit but does not become
  next-wake failed-action feedback.

Tool visibility:

- Normal persistent Team wakes get ordinary Team action tools.
- Team Chat wakes get `team_reply_to_chat`, plus ordinary tools only when that
  chat dispatch may mutate Team state.
- Governance wakes get governance tools only unless explicitly also a normal
  Team action dispatch.
- Ephemeral worker wakes get `team_report_ephemeral_result` only unless a
  specific extra capability is added later.
- Inactive or teamless sessions get no ordinary Team action tools, except
  governance tools when the caller is an authorized governance manager.
- Server-side authorization must enforce every boundary even when tool hiding
  would normally prevent a bad call.

## Tool Surface Specification

Common optional arguments:

- `handled_inbox_item_ids: string[]` is accepted by ordinary persistent-agent
  tools unless the tool is explicitly worker/governance scoped.
- `correlation_id: string | null` or `causation_id: string | null` is accepted by
  all mutating tools for audit grouping.
- `request_id: string` is required according to the cross-slice matrix. Where the
  matrix allows omission, an explicit domain id plus current preconditions must
  fully prevent duplicate effects.

`team_record_note`

- Arguments: `body: string`.
- Semantics: record a durable note for the caller's current dispatch. It is
  audit/handoff context and does not count as progress by itself. Return
  `note_id` or `action_record_id`.

`team_mark_inbox_handled`

- Arguments: `inbox_item_ids: string[]`.
- Semantics: mark one or more pending caller-owned inbox items handled. Counts as
  progress only when at least one pending item is handled. Return handled ids.

`team_request_status`

- Arguments: `recipient_agent_id: string`, `task_id: string | null`,
  `question: string`, `response_requested: boolean`, `priority: low | normal |
  high`.
- Semantics: recipient must be a direct subordinate. Create a `STATUS_REQUEST`
  inbox item and wake intent according to priority. Defaults:
  `response_requested: true`, `priority: normal`. Return `inbox_item_id`.

`team_ask_question`

- Arguments: `recipient_agent_id: string | null`, `task_id: string | null`,
  `question: string`, `message: string | null`, `summary: string | null`,
  `response_requested: boolean`, `priority: low | normal | high`.
- Semantics: communication is upward only. If `recipient_agent_id` is supplied,
  it must equal the caller's direct superior. A top-level manager receives a
  corrective error pointing to `team_escalate_operator`. Create a `QUESTION`
  inbox item. Defaults: `response_requested: true`, `priority: normal`. Return
  `inbox_item_id`.

`team_send_progress_report`

- Arguments: `recipient_agent_id: string | null`, `task_id: string | null`,
  `message: string`, `summary: string | null`, `response_requested: boolean`,
  `priority: low | normal | high`.
- Semantics: communication is upward only. If `recipient_agent_id` is supplied,
  it must equal the caller's direct superior. Create a `PROGRESS_REPORT` inbox
  item. Defaults: `response_requested: false`, `priority: low`. Return
  `inbox_item_id`.

`team_reply_to_chat`

- Arguments: `conversation_id: string`, `message_id: string`, `body: string`,
  `summary: string | null`.
- Semantics: conversation must belong to the caller and source message must be a
  human operator message. Create an agent chat message and mark the source
  message replied. Return `chat_message_id`, `conversation_id`, and
  `replied_message_id`.

`team_send_operator_chat_message`

- Arguments: `recipient_operator_user_id: string`, `body: string`,
  `summary: string | null`.
- Semantics: recipient must be listed in current Team Chat operator
  participants. Create or reuse the caller/operator conversation. Return
  `chat_message_id` and `conversation_id`.

`team_escalate_operator`

- Arguments: `title: string`, `message: string`, `urgency: string`,
  `project_id: string | null`, `task_id: string | null`.
- Semantics: only the top-level team manager may call it. If the team escalates
  directly to an operator, create the operator escalation event or intent. If the
  team escalates to governance, create governance agenda items and wake relevant
  managers. Default `urgency` is `normal`. Return operator event ids or
  governance agenda item ids.

`team_create_case`

- Arguments: `id: string | null`, `project_id: string | null`, `title: string`,
  `description: string | null`, `status: open | triage | in_progress | waiting |
  resolved | closed`, `priority: low | normal | high`,
  `owner_agent_id: string | null`, `source: string | null`,
  `source_ref: string | null`, `tags: string[]`, `task_ids: string[]`.
- Semantics: caller can create cases only for the active-lease team. If
  `project_id` is set, caller must be team manager or assigned to the project.
  Case scope must allow selected project, owner, and task links. Defaults:
  `status: open`, `priority: normal`, `description: ""`. Return `case_id`.

`team_update_case`

- Arguments: `case_id: string`, plus any subset of `project_id`, `title`,
  `description`, `status`, `priority`, `owner_agent_id`, `source`, `source_ref`,
  `tags`, and `task_ids`.
- Semantics: caller must be case owner, team manager, project lead, or supervisor
  of the case owner. Scope changes revalidate project, owner, and task links.
  Return `case_id`.

`team_create_task`

- Arguments: `task_id: string | null`, `owner_agent_id: string`,
  `project_id: string`, `case_id: string | null`, `priority: integer`,
  `title: string`, `description: string`, `state: pending | in_progress |
  blocked | waiting_for_review`, `parent_task_id: string | null`,
  `dependency_task_ids: string[]`.
- Semantics: roles with `create_root_task` can create top-level tasks. Other
  agents must create child tasks under their own active task. Target owner must
  be within allowed team/project/supervision boundary. Parent and dependencies
  must be same-project and acyclic. Return `task_id`.

`team_create_tasks`

- Arguments: `tasks: object[]`, where each item has the `team_create_task`
  fields except common arguments.
- Semantics: batch graph planning call. Validate sibling-created dependencies,
  same-project constraints, and cycles across the full batch. Entire batch is
  all-or-nothing. Return `created_task_ids`.

`team_update_task`

- Arguments: `task_id: string`, and any subset of `state: pending | in_progress |
  blocked | waiting_for_review | finished`, `priority: integer`,
  `title: string`, `description: string`, `owner_agent_id: string`,
  `dependency_task_ids: string[]`.
- Semantics: owner may update state, priority, title, and description. A
  superior may claim or reassign non-terminal subordinate work when permitted.
  Dependency edits are allowed for owner, supervisor, project lead, or team
  manager and must pass same-project/cycle validation. Review tools own
  acceptance and rejection. Return `task_id`.

`team_update_goal`

- Arguments: `project_id: string`, `goal_id: string`, `state: pending |
  in_progress | accepted | rejected | blocked`.
- Semantics: only project lead or team manager may update goals. Goal acceptance
  remains explicit and is never inferred from text. Recalculate project status.
  Return `goal_id`.

`team_stand_down_agent`

- Arguments: `agent_id: string`, `reason: string`.
- Semantics: target must be a direct subordinate in the caller's team and active.
  Create a superior-sourced stand-down affecting the subordinate subtree. Return
  `stand_down_id`.

`team_revoke_stand_down`

- Arguments: `agent_id: string`.
- Semantics: revoke only superior-sourced stand-downs previously set by the
  caller for a direct subordinate. Operator stand-downs cannot be revoked by this
  tool. Return `revoked_stand_down_ids`.

`team_submit_deliverable`

- Arguments: `id: string | null`, `task_id: string`, `summary: string`,
  `supersedes_deliverable_id: string | null`, `artifact_paths: string[]`,
  `artifact_refs: string[]`, `artifact_space_ref: string | null`,
  `git_refs: {agent_branch?: string, commit_sha?: string}`.
- Semantics: task must be owned by caller. `artifact_paths` are relative to the
  session `/artifacts`, must not traverse or use absolute paths, and are
  published to the host artifact store before recording the deliverable.
  Publication failure creates no deliverable. Post-publication mutation failure
  deletes the bundle if supported or records cleanup intent. Set deliverable
  pending and task `waiting_for_review`; create a superior
  `DELIVERABLE_NOTICE` when there is a direct superior. Return `deliverable_id`,
  created inbox ids, and published artifact refs.

`team_submit_deliverable_for_review`

- Arguments: `deliverable: object` with `team_submit_deliverable` fields except
  common arguments, and `reviews: object[]` with `id`, `reviewer_agent_id`,
  `review_profile_id`, and `instructions`.
- Semantics: record the deliverable and create review requests in one
  transaction. Suppress the normal superior `DELIVERABLE_NOTICE`. Preserve all
  deliverable validation, review profile validation, artifact import, and wake
  intent behavior. Return `deliverable_id` and `created_review_request_ids`.

`team_request_review`

- Arguments: `id: string | null`, `deliverable_id: string`,
  `reviewer_agent_id: string`, `review_profile_id: string | null`,
  `instructions: string | null`.
- Semantics: caller must be producer, producer's supervisor, project lead, or
  team manager. Reviewer must be same-team. Review profile must be active and
  applicable to the project and reviewer role when constrained. Import review
  artifacts through the host boundary and create reviewer wake intent. Return
  `review_request_id` and created inbox ids.

`team_decide_review`

- Arguments: `review_request_id: string`, `state: accepted | rejected | blocked`,
  `findings: string[]`, `criteria: object[]`,
  `override_routing_gap_reason: string | null`.
- Semantics: only the assigned reviewer may decide. Criteria must reference
  attached review-profile criteria. Accepted reviews cascade to accepted
  deliverable/task only when all relevant reviews are accepted. Rejected reviews
  reject the deliverable, move task to `in_progress`, and create a rework inbox
  carrying `supersedes_deliverable_id`. Blocked reviews block deliverable/task.
  Manager-fallback override is allowed only when current routing rules require
  it. Return review, deliverable, task, and created inbox ids.

`team_publish_review_branch`

- Arguments: either `assignment_id: string` for ephemeral promotion or
  `deliverable_id: string` for persistent promotion. Persistent shape may include
  `source_branch: string | null` or `commit_sha: string | null`.
- Semantics: exactly one promotion shape must be valid. Ephemeral promotion
  requires assignment belonging to caller, completed or accepted state, and an
  `agent_branch`. Persistent promotion requires producer, supervisor, project
  lead, or team manager permission. Validate branch names, record review
  promotion intent, and return promotion event id and target review branch.

`team_update_feature_branch`

- Arguments: `task_id: string`, `source_review_branches: string[]`,
  `feature_branch: string | null`.
- Semantics: caller must own the task or supervise the owner. Project Git config
  is required. Validate branch names, record feature promotion intent, and return
  promotion event id and target feature branch.

`team_request_release`

- Arguments: `id: string | null`, `project_id: string`,
  `deliverable_ids: string[]`, `release_branch: string | null`,
  `canonical_branch: string | null`, `summary: string | null`.
- Semantics: caller must be project lead, team manager, or same-project role with
  release permission. Every deliverable must belong to project and be accepted.
  The repository derives source branches from deliverable `git_refs`; agents do
  not provide source branch, target branch, or commit arguments. The tool may
  create prerequisite feature-promotion intents, but must not combine feature
  update and release in one atomic tool call. Return release package id,
  promotion event ids, and next-action hints.

`team_recover_release`

- Arguments: `release_package_id: string`, `agent_branch: string`,
  `commit_sha: string`, `summary: string | null`.
- Semantics: caller must have release-request permission. Package must be
  blocked and have a failed release promotion. Validate recovery branch shape,
  record recovery intent, and return release package id.

`team_request_ephemeral_agent`

- Arguments: `id: string | null`, `task_id: string`,
  `worker_session_id: string | null`, `title: string`, `prompt: string`,
  `model: string | null`, `agent_provider: string | null`,
  `execution_backend: string | null`, `container_image: string | null`,
  `allowed_tools: string[]`, `ttl_hours: integer`,
  `directory_path: string | null`.
- Semantics: caller must own the task, be assigned to the project, and have
  positive `ephemeral_policy.max_concurrent`. Active assignments cannot exceed
  `max_concurrent`. Create assignment and worker-start intent. Return
  `assignment_id`, worker session id, and intent/event ids.

`team_request_ephemeral_agents`

- Arguments: `assignments: object[]`, each with `team_request_ephemeral_agent`
  fields except common arguments.
- Semantics: all assignments are checked together against `max_concurrent`.
  Entire batch is all-or-nothing. Return created assignment ids and intent/event
  ids.

`team_accept_ephemeral_result`

- Arguments: `assignment_id: string`, `deliverable_id: string | null`.
- Semantics: assignment must belong to caller and be completed, accepted,
  cleanup-pending, or cleaned up. Create a parent-owned deliverable from the
  assignment summary and artifacts, set assignment accepted, and move task to
  `waiting_for_review`. Return `assignment_id` and `deliverable_id`.

`team_reject_ephemeral_result`

- Arguments: `assignment_id: string`, `rejection_reason: string | null`.
- Semantics: assignment must belong to caller. Set assignment rejected and store
  reason. Return `assignment_id`.

`team_report_ephemeral_result`

- Arguments: `assignment_id: string | null`, `summary: string | null`,
  `artifact_refs: string[]`, `agent_branch: string | null`,
  `commit_sha: string | null`, `error: string | null`.
- Semantics: worker-scoped. Assignment is inferred from caller session unless an
  explicit matching `assignment_id` is supplied. Success requires `summary` and
  records summary, artifact refs, optional branch and commit, marks assignment
  completed, creates low-priority parent deliverable notice, and records
  completed-event intent. Error marks assignment failed. `agent_branch`, when
  supplied, must match deterministic `agents/<session-id>/<assignment-id>`.
  Return assignment id, state, created inbox ids, and event/intent ids.

`team_create_operator_directive`

- Arguments: `team_id: string`, `message: string`,
  `recipient_agent_id: string | null`, `project_id: string | null`,
  `task_id: string | null`, `title: string | null`,
  `source_message_id: string | null`.
- Semantics: governance-manager scoped. Requesting session must be an authorized,
  active manager for a governance group that contains the team. Recipient
  defaults to the member-team manager and must be active in the target team.
  Create an operator-directive inbox item and wake intent. Return inbox item id,
  recipient id, team/project/task ids, and directive payload.

`team_mark_governance_agenda_handled`

- Arguments: `agenda_item_id: string`.
- Semantics: governance-manager scoped. Requesting session must be an authorized,
  active manager for the agenda item's governance group. Item must still be
  pending. Mark handled and return agenda item id, governance group id, and new
  state.

## Prompt And Guidance Specification

Steady-state prompt style:

- Fresh normal Team prompts must use positive, minimal wording. They should not
  teach agents about removed artifacts or internal completion machinery.
- Normal prompts should not mention `team-action.json`, `schema_version`,
  `agent_id`, or `dispatch_id`.
- Normal prompts should not include `available_team_mcp_tools` unless a concrete
  MCP client limitation requires duplicating tool discovery.

Managed `/home/agent/AGENTS.md` Team guidance:

- Keep persistent identity and role context, but say that Team MCP tools infer
  the current Team agent and active dispatch from the session.
- Keep durable policy guidance: use current Team context, directional
  communication only to direct superiors/subordinates, role-aware delegation,
  task graph/dependency awareness, case-vs-task distinction, review handoff,
  review artifact import, rejected-review rework lineage, manager-fallback
  review routing, release sequencing, stand-down boundaries, and inbox handling.
- Use MCP tool names in durable guidance. Field-level argument shapes belong to
  tool descriptions, not durable prompt prose.
- `team_record_note` is a note tool only. It is not a completion ritual.
- For `response_requested: false` inbox items, agents should act or mark handled
  when useful and should not send thank-you or no-op acknowledgement messages.
- Non-Git deliverables should be staged under `/artifacts`; agents pass relative
  paths to `team_submit_deliverable` or
  `team_submit_deliverable_for_review`.
- Review artifact handoff is through `team_request_review` or
  `team_submit_deliverable_for_review`; agents should not pass local artifact
  paths through communication tools.
- Rejected-review rework uses `supersedes_deliverable_id` from the
  `review_rejected` inbox payload.
- Release requests stand alone. Agents use `team_recover_release` only when the
  release package `next_action_hint` identifies a failed release promotion and
  the resolved commit is pushed to an allowed `agents/...` ref.

Normal Team wake prompt:

```text
Continue your assigned team-agent work. Use Team MCP tools when you need to
change Team state or record a useful note. When you are done, finish normally.
```

The dynamic normal prompt must still include current context needed for decisions:
agent, wake reason, superior/subordinates, task creation mandate, assigned tasks,
cases, inbox, release packages, runtime attention, relevant asynchronous
feedback, operator chat participants, repository checkout path, review imports,
manager-fallback summary, and prior handoff summary.

When the agent owns tasks, the prompt should include valid owned task ids and the
prepared repository checkout path when one exists. When the agent is reviewing
work, the prompt should include imported review artifact paths and import errors;
reviewers should inspect imported paths and block or ask the lead when imports
failed.

Team Chat wake prompt:

```text
You have received a message from a human operator in the Team Add-on Chat tab.
This is not an agent-to-agent inbox message. Use team_reply_to_chat to reply to
the human operator.

If the operator asks for a Team state change, call the relevant Team MCP action
tool first, then call team_reply_to_chat with a concise summary of what you did
or why the request could not be completed.
```

Team Chat prompts may include conversation id, source message id, recent
transcript, current agent, operator participants, and human message. They should
not expose `dispatch_id` as an action argument. They should keep the distinction
between human chat replies and agent-to-agent inbox communication: use
`team_reply_to_chat` for the human reply, not `team_ask_question` or
`team_send_progress_report`.

Governance guidance and wake prompt:

- Governance managers may oversee governance groups even when their persistent
  agent record has no `team_id`.
- Governance wakes use the governance context as the source of group, agenda,
  member-team summary, cost signal, and blocker signal facts.
- Member teams remain separate execution units. Governance managers do not take
  ownership of member-team project tasks, deliverables, reviews, releases, or
  direct-report chains.
- Governance-only wakes expose governance tools only:
  `team_create_operator_directive` and
  `team_mark_governance_agenda_handled`.
- If no governance action is needed, the governance manager should say so
  briefly and avoid inventing member-team project work.

Inactive Team-agent guidance:

- Keep the current assignment statement: this persistent agent is not currently
  assigned to a Team.
- Normal Team-agent task, deliverable, review, release, subordinate, and Team
  action MCP tool instructions apply only when a future wake explicitly assigns
  normal Team work.
- If the agent also manages governance groups, it follows governance guidance and
  current governance wake prompt instead.

Ephemeral worker prompt:

```text
Complete this scoped ephemeral team-agent assignment. You are a disposable worker
for one parent agent; keep the work scoped to that assignment.

Before finishing, call team_report_ephemeral_result. For successful work include
summary, artifact_refs when you produced artifacts, and agent_branch/commit_sha
when you produced Git work. If you could not complete the assignment, call the
same tool with error explaining the failure.
```

Ephemeral worker prompts expose only `team_report_ephemeral_result` unless a
specific extra worker capability is added later. `task_notes` is removed unless
the implementation intentionally persists it as audit metadata.

Removed prompt and seeded-file surfaces:

- Stop seeding `/artifacts/team-action.schema.json`,
  `/artifacts/team-action.example.json`, and `team-finish`.
- Remove the large final JSON action shape from prompts.
- Remove action-artifact missing recovery for MCP-native Team wakes.
- Remove delayed correction hints for synchronous MCP validation failures. Keep
  delayed attention only for asynchronous or post-wake failures.

## Action Coverage Map

| Old action surface | MCP replacement |
| --- | --- |
| `schema_version` | Not an argument; owned by MCP tool registration. |
| `agent_id` | Inferred from ambient session. |
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
| `handled_inbox_item_ids` | Per-tool `handled_inbox_item_ids` or `team_mark_inbox_handled`. |
| `notes` | `team_record_note`. |

## Dependency Map

Serial foundations:

1. Slice 1: ambient caller and Team MCP tool framework.
2. Slice 2: storage schema for activity, idempotency, and outbox.
3. Slice 3: repository command framework.
4. Slice 4: dispatch finalizer.

Parallel tool waves after Slice 3:

- Slice 5: notes, inbox, communication, chat, escalation.
- Slice 6: cases, tasks, goals, stand-downs.
- Slice 7: deliverables, reviews, artifact publication.
- Slice 8: Git promotion, release, release recovery intents.
- Slice 9: ephemeral delegation and worker result reporting.

Cutover slices after all tool groups and Slice 4:

- Slice 10: workflow pack clean cut.
- Slice 11: prompt and seeded-file removal.
- Slice 12: docs, changelog, and local cutover runbook.
- Slice 13: local cutover execution.

## Slice 1 - Ambient Caller And Add-On MCP Tool Framework

Goal: make Team MCP tools resolve the caller from the MCP connection without
putting Team-specific logic into core.

Dependencies: none.

Parallelism: can run alongside Slice 2 if the result contract is agreed early.

Primary files:

- `src/evander_team_addon/mcp.py`
- new `src/evander_team_addon/mcp_context.py` or equivalent add-on-owned helper
- `tests/test_team_addon_mcp.py`
- optionally `src/agent_sandbox/addons.py` or `src/agent_sandbox/gateway.py` only
  for a generic add-on ambient-session helper

Implementation tasks:

- Define a shared add-on helper that resolves:
  - ambient session id
  - Team persistent agent, active runtime, active dispatch id, wake reason
  - governance manager scope
  - ephemeral worker assignment scope
- Prefer a generic core API such as `AddonContext.current_mcp_session_id()` only
  if add-ons cannot otherwise access ambient caller identity without exposing
  `session_id` in every tool schema.
- If a temporary `session_id` argument remains during refactor, keep it optional,
  let the generic gateway binding resolve it, and reject conflicts.
- Move existing governance MCP tools onto the same helper.
- Add common result and error helpers for:
  - `success`
  - `applied`
  - `progress_made`
  - `revision`
  - `agent_id` or governance/assignment id where useful
  - `action_record_id`
  - `activity_id`
  - `errors`
  - rich correction context
- Register all Team tool names from `evander_team_addon.mcp`, but keep
  implementation for later slices behind repository command calls.
- Do not add Team tool names to core `ToolName`.

Acceptance criteria:

- Existing governance tools still work through `tests/test_team_addon_mcp.py`.
- A normal Team tool call from a session with no active lease fails without
  mutation.
- A call with ambient session A and explicit session B, if explicit session is
  still accepted temporarily, fails before mutation.
- Registered Team tool names are visible only through add-on MCP registration and
  `extra_mcp_tool_names`, not core enums.

## Slice 2 - Storage Schema For Activity, Idempotency, And Outbox

Goal: add durable storage primitives required by MCP-native commands.

Dependencies: none, but coordinate model names with Slice 3.

Parallelism: can run alongside Slice 1.

Primary files:

- `src/evander_team_addon/sqlite_store.py`
- `src/evander_team_addon/models.py`
- `src/evander_team_addon/repository.py`
- `tests/test_team_addon_repository.py`
- migration helper script if needed under `scripts/` or add-on-owned module

Implementation tasks:

- Add SQLite schema and model/projection support for dispatch activity rows:
  - id
  - tool name
  - caller session id
  - agent id or governance/assignment id
  - dispatch id
  - request id
  - correlation id or causation id
  - success/failure status
  - progress classification
  - action/audit record id
  - timestamps
  - error code/message and correction context summary
- Add idempotency storage keyed by:
  - tool name
  - caller identity
  - dispatch id or worker assignment scope
  - request id
- The normalized payload digest is not part of that uniqueness key. Store it as
  the checked payload identity for exact replay:
  - same key plus same digest returns the original stored result
  - same key plus different digest fails as an idempotency conflict before any
    mutation
- Persist the original normalized payload digest, response payload, created
  domain ids, created event ids, and status.
- For tools that explicitly omit `request_id` because a natural domain id fully
  prevents duplication, document the substitute uniqueness key on the command and
  do not create a weaker synthetic idempotency scope.
- Add durable async intent/outbox storage for effects that must not run
  synchronously inside MCP tools:
  - ephemeral worker start requests
  - Git promotion requests
  - release recovery events
  - operator/governance escalation events if they are emitted asynchronously
- Keep outbox records in Team add-on storage unless a later feature proves a
  generic outbox is needed outside Team.
- Add a one-time local migration path from existing `state.db`; it should add new
  tables without requiring JSON state reimport.
- Preserve the existing full-state projection for UI and tests.

Acceptance criteria:

- Existing state reads still produce the same `TeamState` projection.
- Re-running schema initialization is idempotent.
- Idempotency records can return the original result for identical replay.
- Conflicting replay payloads fail without mutation.
- Conflicting replay detection looks up by tool, caller, dispatch or worker
  scope, and request id before comparing the stored normalized payload digest.
- Outbox records can be queried/claimed idempotently by the existing workflow
  effect path or an add-on worker.

## Slice 3 - Repository Command Framework

Goal: create the command-style Team mutation path used by every MCP tool.

Dependencies: Slice 1 helper contract and Slice 2 storage primitives.

Parallelism: blocks most tool implementation. Tool agents may start extracting
validators into pure helpers in parallel if they do not change behavior.

Primary files:

- `src/evander_team_addon/repository.py`
- `src/evander_team_addon/sqlite_store.py`
- `src/evander_team_addon/models.py`
- `tests/test_team_addon_repository.py`

Implementation tasks:

- Add a `TeamCommandContext` or equivalent containing:
  - ambient session id
  - caller agent or governance manager or ephemeral assignment
  - active dispatch id where required
  - wake/session class
  - request id
  - correlation id
  - tool name
  - progress classification
- Add a single command execution wrapper that:
  - starts a SQLite transaction and takes the repository lock
  - resolves latest caller/runtime rows
  - checks active lease or worker/governance scope
  - applies idempotency preflight
  - returns the stored result for an identical idempotent replay without domain
    mutation or outbox creation
  - records replay attempts as replay activity/audit only if the ledger needs
    attempt-level visibility; replay activity must not count as progress
  - returns an idempotency conflict for a reused key with a different normalized
    payload before any mutation
  - re-reads semantic dependencies for the command
  - validates latest state
  - on validation or precondition failure, records failed dispatch activity and
    audit visibility in the same transaction, skips domain mutation, skips
    outbox rows, and returns rich correction context
  - on success, applies scoped mutations
  - records successful dispatch activity
  - records action/audit data
  - records outbox rows when needed
  - bumps Team revision
  - stores idempotent result
  - commits
- Factor reusable validation/apply logic out of `apply_agent_action` into
  lower-level functions that do not finalize dispatches.
- Add atomic `handled_inbox_item_ids` support inside the command wrapper.
- Ensure failed synchronous validation records activity/audit visibility but does
  not create next-wake failed-action feedback.
- Keep `apply_agent_action` only as a temporary internal reference during
  migration; do not build new tools by passing synthetic JSON action blobs into
  it as the final architecture.
- Define a normalized payload function per tool. It must exclude ambient identity
  and include only semantic arguments.

Acceptance criteria:

- A successful command can mutate state without clearing
  `active_dispatch_id`/`wake_leased_until`.
- A failed command leaves no domain mutation but records failure activity.
- Failed validation activity is committed from an explicit failure path and does
  not create outbox rows or store a success response for replay.
- `team_record_note` or a simple stub command proves exact idempotency.
- `handled_inbox_item_ids` closes only pending inbox items owned by the caller and
  is atomic with the primary command.
- Current full-state update tests continue to pass while command methods are
  introduced.

## Slice 4 - Dispatch Finalizer

Goal: replace action-file completion semantics with completion-time bookkeeping
from the dispatch activity ledger.

Dependencies: Slice 2 and Slice 3.

Parallelism: can run while tool slices are in progress once the activity ledger
schema is stable.

Primary files:

- `src/evander_team_addon/repository.py`
- `src/evander_team_addon/providers.py`
- `src/evander_team_addon/provider_schemas.py`
- `tests/test_team_addon_runtime.py`
- `tests/test_team_addon_repository.py`

Implementation tasks:

- Add repository method such as `finalize_agent_dispatch_from_activity(...)`.
- It must:
  - verify session/agent/dispatch still match the active Team lease
  - read all dispatch activity rows
  - decide `progress_made` from tool-classified activity, not arbitrary state
    changes
  - clear the active lease
  - update `no_progress_count`
  - update `last_progress_at` and context-rotation state for
    context/activity-producing tools
  - queue pending chat retry events when applicable
  - mirror session metadata after finalization
  - avoid next-wake failed-action feedback for synchronous validation failures
- Add behavior for a completed dispatch with no Team tool calls: close the lease
  and count no progress.
- Add behavior for completed Team Chat dispatches:
  - `team_reply_to_chat` activity should mark the source message replied.
  - failed or absent reply should finalize according to chat-specific rules.
- Keep governance completion separate unless the wake is explicitly both
  governance and ordinary Team work.

Acceptance criteria:

- Valid tool call followed by completion closes lease only at completion.
- No tool calls followed by completion increments no-progress count.
- `team_record_note` alone does not count as progress.
- `team_mark_inbox_handled` counts as progress only when it handles pending
  inbox.
- Failed synchronous validations are visible in ledger/audit but do not appear
  as next-wake failed-action feedback.

## Slice 5 - Notes, Inbox, Communication, Chat, And Escalation Tools

Goal: implement the low-risk high-frequency MCP tools first.

Dependencies: Slice 1 and Slice 3. Slice 4 needed for final no-progress tests.

Parallelism: can run alongside Slices 6, 7, 8, and 9 after Slice 3.

Tools:

- `team_record_note`
- `team_mark_inbox_handled`
- `team_request_status`
- `team_ask_question`
- `team_send_progress_report`
- `team_reply_to_chat`
- `team_send_operator_chat_message`
- `team_escalate_operator`

Primary files:

- `src/evander_team_addon/mcp.py`
- `src/evander_team_addon/repository.py`
- `src/evander_team_addon/models.py`
- `tests/test_team_addon_mcp.py`
- `tests/test_team_addon_repository.py`
- `tests/test_team_addon_runtime.py`

Implementation tasks:

- Define narrow tool schemas and descriptions. Avoid broad `payload`.
- Require `request_id` for create/message/escalation tools.
- Add optional `correlation_id` for related calls.
- Keep `team_record_note` non-progress by default.
- Implement `team_mark_inbox_handled` as all-or-nothing for a list of inbox ids.
- Implement upward communication checks:
  - questions and progress reports go only to direct superior
  - top-level manager question returns correction context pointing to
    `team_escalate_operator`
- Implement subordinate status request checks:
  - recipient must be a direct subordinate
  - created inbox item wakes recipient according to priority
- Implement chat reply checks:
  - conversation belongs to caller
  - message is an operator message in that conversation
  - source message is marked replied
- Implement operator chat participant checks from current Team context.
- Implement operator/governance escalation path with durable outbox/intent
  records where external wake/event publication is asynchronous.

Acceptance criteria:

- Each tool has success, validation failure, idempotent replay, conflicting
  replay, and active-lease-denied tests.
- Communication tools return created inbox/message ids.
- Chat reply updates message state immediately but does not finalize dispatch.
- Escalation returns either operator event ids or governance agenda ids and
  distinguishes `intent_recorded` from downstream effect completion.

## Slice 6 - Cases, Tasks, Goals, And Stand-Down Tools

Goal: migrate structured Team planning and control operations.

Dependencies: Slice 1 and Slice 3.

Parallelism: can run alongside Slices 5, 7, 8, and 9.

Tools:

- `team_create_case`
- `team_update_case`
- `team_create_task`
- `team_create_tasks`
- `team_update_task`
- `team_update_goal`
- `team_stand_down_agent`
- `team_revoke_stand_down`

Primary files:

- `src/evander_team_addon/mcp.py`
- `src/evander_team_addon/repository.py`
- `src/evander_team_addon/models.py`
- `tests/test_team_addon_mcp.py`
- `tests/test_team_addon_repository.py`
- `tests/test_team_addon_runtime.py`

Implementation tasks:

- Use existing repository authorization and graph-validation helpers.
- Make `team_create_tasks` all-or-nothing. This migration has no partial batch
  task creation mode.
- Require `request_id` for create/batch tools unless explicit domain ids fully
  prevent duplication.
- Do not include broad case `metadata` in the normal agent MCP schema.
- Do not include `team_id` in normal `team_create_case`.
- Do not include `accepted` or `rejected` in normal `team_update_task.state`.
- Preserve task creation mandate rules:
  - root-task permission may create top-level tasks
  - other agents must create child tasks under their own active task
  - target owner must be in allowed supervision/project/team boundary
  - dependency edits must be same-project and acyclic
- Preserve case scope checks and task link checks.
- Preserve goal update permissions for project lead and team manager.
- Preserve stand-down direct-subordinate and operator-hold boundaries.

Acceptance criteria:

- Task batch graph validation supports sibling dependencies atomically.
- Dependency cycle and cross-project dependency failures return current graph
  correction context.
- Stand-down prevents later non-empty ordinary Team tool calls by the held
  subtree.
- Revoking a superior stand-down cannot revoke operator stand-downs.

## Slice 7 - Deliverables, Reviews, And Artifact Publication

Goal: implement the artifact and review path without inconsistent deliverable
state.

Dependencies: Slice 1, Slice 2, Slice 3, and the artifact ownership decision.

Parallelism: can run alongside Slices 5, 6, 8, and 9, but coordinate with Slice 8
on Git refs and release prerequisites.

Tools:

- `team_submit_deliverable`
- `team_submit_deliverable_for_review`
- `team_request_review`
- `team_decide_review`

Primary files:

- `src/evander_team_addon/mcp.py`
- `src/evander_team_addon/repository.py`
- `src/evander_team_addon/providers.py` for reusable artifact publication helpers
- `src/evander_team_addon/models.py`
- `tests/test_team_addon_mcp.py`
- `tests/test_team_addon_repository.py`
- `tests/test_team_addon_runtime.py`

Implementation tasks:

- Move deliverable artifact publication out of the action-artifact provider path
  into add-on-owned tool support.
- Implement path safety:
  - paths are relative to session `/artifacts`
  - no traversal or absolute path escape
  - empty `artifact_paths` is treated as omitted only if the tool schema allows it
- Use deterministic deliverable ids from explicit id or idempotency record before
  artifact publication.
- Revalidate task ownership and review preconditions after artifact publication
  and before state mutation.
- If publication succeeds but final mutation fails, delete the published bundle if
  supported or record an orphan cleanup intent in Team add-on storage.
- Preserve rework lineage with `supersedes_deliverable_id`.
- Preserve review artifact import semantics for `team_request_review` and
  `team_submit_deliverable_for_review`.
- Implement review profile validation and criterion validation.
- Implement manager-fallback routing guard and
  `override_routing_gap_reason`.
- Implement review decision cascades:
  - accepted review can accept deliverable/task only when all relevant reviews are
    accepted
  - rejected review creates rework inbox carrying `supersedes_deliverable_id`
  - blocked review blocks deliverable/task

Acceptance criteria:

- Artifact publication failure creates no deliverable and no task state change.
- Idempotent replay does not republish duplicate artifacts or duplicate review
  requests.
- Review requests import artifacts through the existing host boundary.
- Compound deliverable-for-review suppresses the normal superior
  `DELIVERABLE_NOTICE` and creates only the explicit review routing.
- Review decision failures return current reviewer, deliverable, task, and review
  state needed for same-wake correction.

## Slice 8 - Git Promotion, Release, And Release-Recovery Tools

Goal: record Git/release intent synchronously and keep actual Git work
asynchronous and retryable.

Dependencies: Slice 1, Slice 2, Slice 3. Coordinate with Slice 7.

Parallelism: can run alongside Slices 5, 6, 7, and 9.

Tools:

- `team_publish_review_branch`
- `team_update_feature_branch`
- `team_request_release`
- `team_recover_release`

Primary files:

- `src/evander_team_addon/mcp.py`
- `src/evander_team_addon/repository.py`
- `src/evander_team_addon/git_naming.py`
- `src/evander_team_addon/providers.py`
- `tests/test_team_addon_mcp.py`
- `tests/test_team_addon_git_effects.py`
- `tests/test_team_addon_repository.py`
- `tests/test_team_addon_runtime.py`

Implementation tasks:

- Validate branch names before recording intent.
- Record review/feature/release promotion intent in Team state/outbox in the same
  transaction as the command activity.
- Return `intent_recorded`, `async_effect_pending`, promotion ids, target branch,
  and next-action hints.
- Preserve existing promotion workflow events and idempotency keys.
- Ensure `team_request_release`:
  - requires accepted deliverables in the project
  - derives source branches from deliverable Git refs
  - does not accept source/target/commit args
  - can create prerequisite feature promotions
  - does not combine feature update and release in one atomic tool call
- Ensure `team_recover_release`:
  - only targets blocked release packages
  - requires a failed release promotion
  - records recovery intent without running Git synchronously

Acceptance criteria:

- Request replay does not duplicate promotion/release events.
- Conflicting replay payload fails without mutation.
- Release request returns package id, promotion event ids, and current
  next-action hints.
- Async event publication can be retried without duplicate events.
- Existing Git effect tests pass with outbox-driven events.

## Slice 9 - Ephemeral Delegation And Worker Result Reporting

Goal: replace both persistent-agent ephemeral action fields and worker
`team-action.json` result reporting.

Dependencies: Slice 1, Slice 2, Slice 3.

Parallelism: can run alongside Slices 5, 6, 7, and 8.

Tools:

- `team_request_ephemeral_agent`
- `team_request_ephemeral_agents`
- `team_accept_ephemeral_result`
- `team_reject_ephemeral_result`
- `team_report_ephemeral_result`

Primary files:

- `src/evander_team_addon/mcp.py`
- `src/evander_team_addon/repository.py`
- `src/evander_team_addon/providers.py`
- `src/evander_team_addon/models.py`
- `tests/test_team_addon_mcp.py`
- `tests/test_team_addon_repository.py`
- `tests/test_team_addon_runtime.py`

Implementation tasks:

- Preserve parent-agent permission checks and `max_concurrent` behavior.
- Make multi-assignment requests all-or-nothing against the concurrency limit.
- Record worker-start intent/outbox rows and return `intent_recorded` rather than
  claiming worker execution completed.
- Generate or validate worker session ids and deterministic worker branch names.
- Update ephemeral worker prompt construction to mention only
  `team_report_ephemeral_result`.
- Implement worker-scoped caller resolution:
  - infer assignment from ambient worker session when possible
  - allow explicit `assignment_id` only when it matches the ambient assignment
  - do not expose ordinary project-work tools to worker sessions
- Implement success and error result paths:
  - success requires summary
  - success can include artifact refs, agent branch, commit sha
  - error marks assignment failed
  - success creates the existing low-priority deliverable notice for the parent
  - success emits the existing completed event through durable intent/outbox
- Implement parent accept/reject tools for completed worker results.

Acceptance criteria:

- Worker result reporting no longer reads `/artifacts/team-action.json`.
- Worker tool visibility includes only `team_report_ephemeral_result`.
- Accepting a completed assignment creates the parent-owned deliverable and moves
  the task to `waiting_for_review`.
- Replaying worker result does not duplicate notices or completion events.

## Slice 10 - Workflow Pack Clean Cut

Goal: remove action-artifact read/apply/recovery nodes from the Team runtime pack.

Dependencies: Slice 4 and all replacement tool groups needed by the pack.

Parallelism: should not start until the finalizer and worker result tool are
implemented.

Primary files:

- `src/evander_team_addon/workflow_packs.py`
- `config/workflow-packs/team-runtime.v1.json`
- `config/workflow-packs/team-runtime.v1.layout.json`
- `src/evander_team_addon/provider_schemas.py`
- `src/evander_team_addon/providers.py`
- `tests/test_workflow_packs.py`
- `tests/test_team_addon_runtime.py`
- `tests/test_workflow_editor_addon_catalog.py`
- `tests/test_addons.py`

Implementation tasks:

- Replace persistent completion path:
  - keep `query:team:session-routing` or a renamed add-on-owned routing query
  - remove `query:team:agent-action-artifact`
  - remove `effect:team:apply-agent-action`
  - add finalizer effect for persistent dispatch completion
- Replace ephemeral completion path:
  - remove worker action-artifact read
  - rely on `team_report_ephemeral_result` during the worker wake
  - keep cleanup/destroy flow after finalization as needed
- Remove `recover_missing_action` and recovery resume wiring.
- Update provider schemas and workflow editor catalog entries.
- Decide whether obsolete provider classes remain only for migration tooling or
  are removed entirely. Clean-cut target is removal from the runtime pack and
  normal catalog.
- Update checked-in pack JSON and layout without touching live ignored operator
  pack files directly.

Acceptance criteria:

- Runtime pack has no `team-action.json`, `query:team:agent-action-artifact`,
  `effect:team:apply-agent-action`, or `recover_missing_action`.
- Existing workflow-pack tests assert the new finalizer path.
- Completion of persistent sessions closes leases through the ledger finalizer.
- Completion of ephemeral worker sessions does not look for an action artifact.

## Slice 11 - Prompts, Guidance, Tool Grants, And Seeded File Removal

Goal: move steady-state agent guidance to MCP-native wording and stop seeding
action-contract files.

Dependencies: Slice 1, Slice 5 for at least basic tools, and Slice 10 before
final deletion of action-artifact prompt text.

Parallelism: prompt text can be drafted earlier, but final merge should happen
after tools are registered.

Primary files:

- `src/evander_team_addon/providers.py`
- `src/evander_team_addon/repository.py` for embedded correction text
- `tests/test_team_addon_runtime.py`
- `tests/test_team_addon_templates.py`
- docs that describe Team Add-on operation

Implementation tasks:

- Update `_team_agent_guidance_content()`.
- Update `_team_agent_guidance_inactive_content()`.
- Update `_team_governance_guidance_content()`.
- Update `_build_prompt()`.
- Update `_build_team_chat_prompt()`.
- Update `_build_governance_prompt()`.
- Update `_build_ephemeral_prompt()`.
- Remove `_missing_action_recovery_prompt()` and related recovery wording.
- Remove `_seed_action_contract_files()` and helper-generation code from Team
  wakes.
- Remove schema/example/helper file constants if unused after cutover.
- Remove delayed feedback/correction hints for synchronous validation failures.
  Keep delayed correction only for asynchronous or post-wake failures.
- Remove `available_team_mcp_tools` from prompt context unless an MCP client
  limitation requires duplicating tool discovery. If retained, document the
  limitation and assert the list matches the wake-scoped tool grants.
- Update wake-time tool grants:
  - normal persistent wake gets ordinary Team action tools
  - Team Chat wake gets `team_reply_to_chat`, plus ordinary tools only when
    mutation is allowed
  - governance wake gets governance tools only
  - ephemeral worker wake gets `team_report_ephemeral_result`
  - inactive/teamless sessions get no ordinary Team action tools
- Keep server-side authorization in each tool even when visibility hides tools.

Acceptance criteria:

- No Team wake seeds `team-action.schema.json`,
  `team-action.example.json`, or `team-finish`.
- No normal Team prompt mentions `team-action.json`, `schema_version`,
  `agent_id`, or `dispatch_id`.
- Normal prompts do not duplicate `available_team_mcp_tools` unless a documented
  MCP client limitation requires it.
- Governance prompt exposes governance tools only.
- Ephemeral worker prompt exposes only `team_report_ephemeral_result`.
- Tool visibility tests cover normal, chat, governance, worker, inactive, and
  teamless sessions.

## Slice 12 - Documentation, Changelog, And Cutover Runbook

Goal: update operator-facing docs and leave a safe local cutover procedure.

Dependencies: Slices 10 and 11 for final behavior.

Parallelism: can draft early, final pass after implementation.

Primary files:

- `docs/addons.md`
- this implementation plan and any remaining migration docs
- `docs/operations.md` if runtime operation changes
- `CHANGELOG.md`
- optional migration script docs under `scripts/`

Implementation tasks:

- Update docs that currently describe action artifacts, schema files, and
  `team-finish`.
- Document the MCP-native action surface at an operator level, not as prompt
  boilerplate.
- Add a local cutover runbook:
  1. Stop Team-related trigger/effect/Admin services.
  2. Inspect active Team leases.
  3. Manually apply or intentionally discard pending `team-action.json` artifacts
     worth preserving.
  4. Apply state/schema migration.
  5. Restart services with Team MCP tools as the only action path.
  6. Run focused smoke checks.
- Add troubleshooting for:
  - active lease mismatch
  - idempotency conflict
  - async intent recorded but effect pending
  - artifact publication cleanup/orphans
- Record the feature in `CHANGELOG.md`.

Acceptance criteria:

- `rg "team-action|team-finish|agent-action-artifact|apply-agent-action|recover_missing_action" docs src/evander_team_addon config/workflow-packs tests`
  only finds migration-history references, deleted-code references, or tests that
  explicitly assert removal.
- Changelog entry names the clean MCP cutover.

## Slice 13 - Local Cutover Execution

Goal: perform the actual local state and service cutover after the code is merged.

Dependencies: all implementation slices.

Parallelism: not parallel. This is the final operational step.

Primary surfaces:

- `runtime/addons/team-addon/state.db`
- Team-related systemd services
- Admin Web live state
- workflow pack configuration through Admin Web/API revision mechanisms

Implementation tasks:

- Stop relevant services:
  - `agent-sandbox-trigger-orchestrator.service`
  - `agent-sandbox-effect-orchestrator.service`
  - `agent-sandbox-admin-web.service` if needed for a quiet UI
  - any Team wake/ingest services that could lease work during migration
- Inspect active leases in `runtime/addons/team-addon/state.db`.
- Decide whether to clear active leases or let the finalizer close them after
  restart.
- Manually apply or discard any pending action artifacts that represent valuable
  work.
- Back up `state.db`.
- Run the schema migration.
- Restart services.
- Run focused live checks:
  - Admin Team page loads.
  - Normal Team wake gets MCP tools and no action files.
  - Simple tool call mutates state and keeps lease open.
  - Completion finalizer closes lease.
  - Ephemeral worker reports through `team_report_ephemeral_result`.
  - Governance tools still work.

Acceptance criteria:

- No new dispatch attempts to read or write `/artifacts/team-action.json`.
- Live state has activity/idempotency/outbox tables.
- New Team actions appear in timeline/audit through MCP activity.
- No active dispatch is stranded because of the migration.

## Cross-Slice Tool Matrix

| Tool | Slice | Progress policy | Async intent | Requires request_id |
| --- | --- | --- | --- | --- |
| `team_record_note` | 5 | no progress, context only | no | yes |
| `team_mark_inbox_handled` | 5 | progress only when pending item handled | no | no, inbox ids plus preconditions can suffice |
| `team_request_status` | 5 | progress | creates inbox/wake intent | yes |
| `team_ask_question` | 5 | progress | creates inbox/wake intent | yes |
| `team_send_progress_report` | 5 | progress, not context-rotation activity unless configured | creates inbox/wake intent | yes |
| `team_reply_to_chat` | 5 | progress | no | yes |
| `team_send_operator_chat_message` | 5 | progress | no | yes |
| `team_escalate_operator` | 5 | progress | yes when operator/governance wake is async | yes |
| `team_create_case` | 6 | progress | no | yes unless explicit id is sufficient |
| `team_update_case` | 6 | progress on real change | no | no, object id plus preconditions can suffice |
| `team_create_task` | 6 | progress | creates owner wake intent | yes unless explicit task id is sufficient |
| `team_create_tasks` | 6 | progress | creates owner wake intents | yes |
| `team_update_task` | 6 | progress on real change | may wake new owner | no, object id plus preconditions can suffice |
| `team_update_goal` | 6 | progress | no | no, object id plus preconditions can suffice |
| `team_stand_down_agent` | 6 | progress | no | yes |
| `team_revoke_stand_down` | 6 | progress when a hold is revoked | no | no, target id plus preconditions can suffice |
| `team_submit_deliverable` | 7 | progress | artifact cleanup may be async on failure | yes unless explicit deliverable id is sufficient |
| `team_submit_deliverable_for_review` | 7 | progress | artifact cleanup may be async on failure | yes |
| `team_request_review` | 7 | progress | creates reviewer wake/import intent | yes unless explicit review id is sufficient |
| `team_decide_review` | 7 | progress | creates rework wake intent on rejection | no, review id plus state preconditions can suffice |
| `team_publish_review_branch` | 8 | progress when intent recorded | yes | yes |
| `team_update_feature_branch` | 8 | progress when intent recorded | yes | yes |
| `team_request_release` | 8 | progress when package/intent recorded | yes | yes unless explicit package id is sufficient |
| `team_recover_release` | 8 | progress when intent recorded | yes | yes |
| `team_request_ephemeral_agent` | 9 | progress when assignment/intent recorded | yes | yes unless explicit assignment id is sufficient |
| `team_request_ephemeral_agents` | 9 | progress when assignments/intents recorded | yes | yes |
| `team_accept_ephemeral_result` | 9 | progress | no | yes unless explicit deliverable id is sufficient |
| `team_reject_ephemeral_result` | 9 | progress | no | no, assignment id plus preconditions can suffice |
| `team_report_ephemeral_result` | 9 | progress when result/error recorded | completion event is async | yes |
| `team_create_operator_directive` | 1/5 | governance progress | creates manager wake intent | yes |
| `team_mark_governance_agenda_handled` | 1 | governance progress | no | no, agenda id plus state preconditions can suffice |

## Atomicity And Retry Matrix

Every tool implementation slice must carry these contracts into repository and
MCP tests. Unless the table explicitly says otherwise, the primary action,
`handled_inbox_item_ids`, activity/audit rows, and any outbox intent rows are one
transaction. Identical idempotent replay returns the original ids/result without
duplicating domain rows or events; conflicting replay payloads fail without
mutation.

| Tool | Atomicity and retry contract |
| --- | --- |
| `team_record_note` | Note record and handled inbox items are all-or-nothing. Replay returns the same note/action/activity ids. |
| `team_mark_inbox_handled` | All listed inbox items close together, or none close. Missing, stale, or already-handled items return correction context without partial handling. |
| `team_request_status` | Status inbox item, wake intent, activity, and handled inbox items are all-or-nothing. Replay returns the same inbox/intent ids. |
| `team_ask_question` | Question inbox item, wake intent, activity, and handled inbox items are all-or-nothing. Replay returns the same inbox/intent ids. |
| `team_send_progress_report` | Progress inbox/message record, wake intent, activity, and handled inbox items are all-or-nothing. Replay returns the same inbox/intent ids. |
| `team_reply_to_chat` | Agent chat message, source-message replied state, activity, and handled inbox items are all-or-nothing. Replay must not duplicate chat messages. |
| `team_send_operator_chat_message` | Conversation creation/reuse, chat message, activity, and handled inbox items are all-or-nothing. Replay must not duplicate chat messages. |
| `team_escalate_operator` | Operator event or governance agenda creation, wake/notification intent, activity, and handled inbox items are all-or-nothing. Replay must not duplicate escalations. |
| `team_create_case` | Case creation, task links, owner/scope updates, activity, and handled inbox items are all-or-nothing. Explicit case ids or request ids prevent duplicate cases. |
| `team_update_case` | All field changes, task links, activity, and handled inbox items are all-or-nothing. Stale scope/link failures leave the case unchanged. |
| `team_create_task` | Task creation, dependency edges, owner wake intent, activity, and handled inbox items are all-or-nothing. Explicit task ids or request ids prevent duplicates. |
| `team_create_tasks` | Entire batch is all-or-nothing, including sibling dependency graph validation and owner wake intents, unless a later explicit partial-batch product decision changes this table. |
| `team_update_task` | State/owner/dependency changes, wake intent when needed, activity, and handled inbox items are all-or-nothing. Stale graph or permission failures leave the task unchanged. |
| `team_update_goal` | Goal state update, project-status recalculation, activity, and handled inbox items are all-or-nothing. Stale project/goal failures leave state unchanged. |
| `team_stand_down_agent` | Superior hold creation, affected wake eligibility bookkeeping, activity, and handled inbox items are all-or-nothing. Replay must not duplicate holds. |
| `team_revoke_stand_down` | Eligible superior-hold revocation, activity, and handled inbox items are all-or-nothing. Operator holds are never partially revoked. |
| `team_submit_deliverable` | Publication plus deliverable/task/inbox/activity mutation must leave no inconsistent state: publication failure creates no deliverable; post-publication mutation failure deletes the bundle or records cleanup intent. Replay must not republish duplicates. |
| `team_submit_deliverable_for_review` | Deliverable publication, review request creation, artifact import/wake intents, notice behavior, activity, and handled inbox items are all-or-nothing, with the same cleanup/orphan rules as deliverable submission. |
| `team_request_review` | Review request, artifact import/wake intent, activity, and handled inbox items are all-or-nothing. Replay must not duplicate review requests or imports. |
| `team_decide_review` | Review decision, deliverable/task cascade, rework inbox/wake intent when needed, activity, and handled inbox items are all-or-nothing. Failed cascades leave review state unchanged. |
| `team_publish_review_branch` | Promotion intent/outbox row, activity, and handled inbox items are all-or-nothing. Git execution retries by the durable intent id, not by creating duplicate events. |
| `team_update_feature_branch` | Feature promotion intent/outbox row, activity, and handled inbox items are all-or-nothing. Git execution retries by the durable intent id. |
| `team_request_release` | Release package creation, prerequisite promotion intents, release intent, activity, and handled inbox items are all-or-nothing. Replay must not duplicate packages or promotion events. |
| `team_recover_release` | Recovery intent/outbox row, activity, and handled inbox items are all-or-nothing. Replay must not duplicate recovery events. |
| `team_request_ephemeral_agent` | Assignment creation, worker-start intent, activity, and handled inbox items are all-or-nothing. Replay must not duplicate assignments or start intents. |
| `team_request_ephemeral_agents` | Entire assignment batch is all-or-nothing against `max_concurrent`, including worker-start intents and handled inbox items. No partial worker batch is allowed without a later explicit product decision. |
| `team_accept_ephemeral_result` | Assignment acceptance, parent-owned deliverable creation, task state update, activity, and handled inbox items are all-or-nothing. Replay must not duplicate deliverables. |
| `team_reject_ephemeral_result` | Assignment rejection, stored reason, activity, and handled inbox items are all-or-nothing. Stale assignment state leaves it unchanged. |
| `team_report_ephemeral_result` | Assignment result/failure state, parent notice on success, completed-event intent, and activity are all-or-nothing. Replay must not duplicate notices or completion events. |
| `team_create_operator_directive` | Governance directive, manager wake intent, and governance activity are all-or-nothing under governance scope. Replay must not duplicate directives. |
| `team_mark_governance_agenda_handled` | Agenda `pending -> handled` transition and governance activity are all-or-nothing. Stale or already-handled agenda items fail without mutation. |

## Integration Test Plan

Each tool slice must add focused tests for the contracts in the
atomicity/retry matrix: success, validation failure with activity visibility,
identical idempotent replay, conflicting replay payload, stale precondition
correction context, active-scope denial, and atomic `handled_inbox_item_ids`
where the tool supports inbox handling.

Focused suites to run as slices land:

```bash
./.venv/bin/pytest -q tests/test_team_addon_mcp.py
./.venv/bin/pytest -q tests/test_team_addon_repository.py
./.venv/bin/pytest -q tests/test_team_addon_runtime.py
./.venv/bin/pytest -q tests/test_team_addon_templates.py
./.venv/bin/pytest -q tests/test_workflow_packs.py tests/test_workflow_editor_addon_catalog.py tests/test_addons.py
```

Full-suite gate before cutover:

```bash
./.venv/bin/pytest -q
```

Live cutover smoke, after restart:

```bash
./.venv/bin/agent-sandbox-cli status --runtime-dir runtime --json
```

Then verify through live Team state and Admin Web that a normal Team dispatch:

- receives Team MCP tools
- does not receive action-artifact files or helper
- can make a simple tool call
- keeps the lease open until session completion
- finalizes from the dispatch activity ledger

## Worktree And Review Hygiene

- The tree is often dirty. Each coding agent should inspect `git status --short`
  before editing and stage only its slice.
- Avoid touching generated frontend or workflow-editor assets unless the slice
  explicitly needs them.
- Do not edit live operator workflow pack files directly. Use checked-in pack
  sources/tests for code changes and Admin Web/API revision controls for live
  changes.
- Each implementation slice should add or update `CHANGELOG.md` only when it
  lands behavior, not for intermediate planning notes.
