# Team MCP Migration Implementation Plan

This is the implementation plan for replacing the Team Add-on
`/artifacts/team-action.json` action surface with Team-owned MCP tools.

Authoritative design inputs:

- `post-critique-design-decisions.md` is the highest-precedence contract.
- `team-mcp-action-surface.md`, `agent-guidance-surface-inventory.md`,
  `persistent-agent-instruction-replacements.md`, and
  `specialized-instruction-replacements.md` define the lower-level design.
- `critiques/` is background provenance only.

The plan below is organized as vertical slices that can be assigned to separate
coding agents. Slices name dependencies explicitly. A slice may start in parallel
only when its listed dependencies are either complete or its agent is working
against an agreed stub/interface from that dependency.

## Non-Negotiable Constraints

- Team-specific behavior stays in `src/truman_team_addon`.
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

- Add-on MCP registration: `src/truman_team_addon/mcp.py`
- Add-on state and semantics: `src/truman_team_addon/repository.py`,
  `src/truman_team_addon/models.py`, `src/truman_team_addon/sqlite_store.py`
- Team runtime, prompts, workflow providers: `src/truman_team_addon/providers.py`
- Built-in runtime workflow pack: `src/truman_team_addon/workflow_packs.py` plus
  checked-in `config/workflow-packs/team-runtime.v1.json`
- Focused tests: `tests/test_team_addon_mcp.py`,
  `tests/test_team_addon_repository.py`, `tests/test_team_addon_runtime.py`,
  `tests/test_team_addon_templates.py`, `tests/test_workflow_packs.py`,
  `tests/test_addons.py`, `tests/test_workflow_editor_addon_catalog.py`

The core gateway already has generic ambient-session mechanics and add-on MCP
registration. If a new generic helper is needed, expose that capability for all
add-ons rather than adding Team-specific core code.

## Decisions To Confirm Before Affected Slices

These are the only remaining design points that appear underspecified after
applying the post-critique overlay. Coding agents should not guess silently.

1. `team_submit_deliverable_for_review` notice behavior.
   Recommendation: suppress the normal superior `DELIVERABLE_NOTICE` when the
   compound tool creates explicit review requests, because the compound tool is
   designed to avoid the extra superior-routing wake. If a superior notice is
   still wanted, specify whether it is auto-handled or created as pending.

2. `team_create_case.team_id`.
   Recommendation: omit `team_id` from the normal active-lease tool. The caller's
   team is implied by the active lease. If teamless case creation is real, add a
   separate explicitly scoped operator/governance/admin path rather than leaking
   cross-team selection into ordinary agent tools.

3. `team_update_task.state`.
   Recommendation: ordinary task updates allow `pending`, `in_progress`,
   `blocked`, `waiting_for_review`, and `finished`. Remove `accepted` and
   `rejected` from ordinary agent arguments unless there is a precise direct
   transition outside review decisions.

4. Generic `payload` and broad `metadata` fields.
   Recommendation: remove `payload` from question/status/progress tools and
   replace it with named fields that have current consumers. Keep assignment
   metadata only if a bounded worker-start consumer is documented and validation
   constrains its shape.

5. Meaningful content requirements.
   Recommendation: require non-empty meaningful body text for
   `team_request_status`, `team_ask_question`, `team_send_progress_report`,
   `team_send_operator_chat_message`, and `team_escalate_operator` unless the
   tool has an explicit default-message rule.

6. Artifact publication ownership.
   Recommendation: Team deliverable tools own host-side publication of
   `artifact_paths`, using path safety checks, idempotent replay, and cleanup or
   orphan tracking when publication succeeds but the final state mutation fails.

These confirmations can happen once, before the impacted tool slices start.

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

- `src/truman_team_addon/mcp.py`
- new `src/truman_team_addon/mcp_context.py` or equivalent add-on-owned helper
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
- Register all Team tool names from `truman_team_addon.mcp`, but keep
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

- `src/truman_team_addon/sqlite_store.py`
- `src/truman_team_addon/models.py`
- `src/truman_team_addon/repository.py`
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
  - normalized payload digest
- Persist the original normalized payload digest, response payload, created
  domain ids, created event ids, and status.
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
- Outbox records can be queried/claimed idempotently by the existing workflow
  effect path or an add-on worker.

## Slice 3 - Repository Command Framework

Goal: create the command-style Team mutation path used by every MCP tool.

Dependencies: Slice 1 helper contract and Slice 2 storage primitives.

Parallelism: blocks most tool implementation. Tool agents may start extracting
validators into pure helpers in parallel if they do not change behavior.

Primary files:

- `src/truman_team_addon/repository.py`
- `src/truman_team_addon/sqlite_store.py`
- `src/truman_team_addon/models.py`
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
  - re-reads semantic dependencies for the command
  - validates latest state
  - applies scoped mutations
  - records dispatch activity
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

- `src/truman_team_addon/repository.py`
- `src/truman_team_addon/providers.py`
- `src/truman_team_addon/provider_schemas.py`
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

- `src/truman_team_addon/mcp.py`
- `src/truman_team_addon/repository.py`
- `src/truman_team_addon/models.py`
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

- `src/truman_team_addon/mcp.py`
- `src/truman_team_addon/repository.py`
- `src/truman_team_addon/models.py`
- `tests/test_team_addon_mcp.py`
- `tests/test_team_addon_repository.py`
- `tests/test_team_addon_runtime.py`

Implementation tasks:

- Use existing repository authorization and graph-validation helpers.
- Make `team_create_tasks` all-or-nothing unless product explicitly accepts a
  partial batch mode with item-scoped results and retry guidance.
- Require `request_id` for create/batch tools unless explicit domain ids fully
  prevent duplication.
- Remove or confirm broad `metadata` on cases before implementing the normal
  agent MCP schema.
- Remove or confirm `team_id` from normal `team_create_case`.
- Remove or confirm `accepted`/`rejected` from normal `team_update_task.state`.
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

- `src/truman_team_addon/mcp.py`
- `src/truman_team_addon/repository.py`
- `src/truman_team_addon/providers.py` for reusable artifact publication helpers
- `src/truman_team_addon/models.py`
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
- Compound deliverable-for-review behavior follows the confirmed notice decision.
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

- `src/truman_team_addon/mcp.py`
- `src/truman_team_addon/repository.py`
- `src/truman_team_addon/git_naming.py`
- `src/truman_team_addon/providers.py`
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

- `src/truman_team_addon/mcp.py`
- `src/truman_team_addon/repository.py`
- `src/truman_team_addon/providers.py`
- `src/truman_team_addon/models.py`
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

- `src/truman_team_addon/workflow_packs.py`
- `config/workflow-packs/team-runtime.v1.json`
- `config/workflow-packs/team-runtime.v1.layout.json`
- `src/truman_team_addon/provider_schemas.py`
- `src/truman_team_addon/providers.py`
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

- `src/truman_team_addon/providers.py`
- `src/truman_team_addon/repository.py` for embedded correction text
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
- `docs/team-mcp-migration/*.md`
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

- `rg "team-action|team-finish|agent-action-artifact|apply-agent-action|recover_missing_action" docs src/truman_team_addon config/workflow-packs tests`
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

## Integration Test Plan

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
