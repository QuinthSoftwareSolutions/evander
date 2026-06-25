# Persistent Team Agent Instruction Replacements

This document maps the normal persistent Team agent guidance from the current
`team-action.json` world to the MCP tool world. It is design-only.

`Old instruction` entries quote or summarize the current instruction agents
receive from seeded `/home/agent/AGENTS.md`, normal wake prompts, seeded action
contract files, or action feedback. `New instruction` is the replacement text or
the explicit removal decision.

## Managed `AGENTS.md` Section

Source: `_team_agent_guidance_content()`, managed section
`truman-team-addon` in `/home/agent/AGENTS.md`.

| Topic | Old instruction | New instruction | Human comment |
| --- | --- | --- | --- |
| Identity | `Persistent agent id: ...`; display name line. | Keep. Identity remains useful, but clarify that MCP tools infer the caller from the session: `Your Team MCP tools infer your agent and active dispatch from this session; do not pass agent_id, session_id, or dispatch_id unless a tool explicitly asks for a different worker/session id.` |
| Context source | `Use the provided Team context and update only your own assigned tasks.` | Keep, with tool wording: `Use the current Team context and Team MCP tools. Do not mutate Team state outside the provided tools.` |
| Directional communication | `Questions and progress reports must go to your direct superior; agents without a direct superior use operator_escalations. Use status_requests only for direct subordinates.` | Keep semantics. Replace action names: `Use team_ask_question or team_send_progress_report only upward to your direct superior. Use team_request_status only for direct subordinates. Top-level managers use team_escalate_operator for operator/governance escalation.` |
| Case tracking | `Use case_updates for intake/lifecycle case tracking.` | Replace with: `Use team_create_case and team_update_case for intake and lifecycle tracking. Use tasks for execution work.` Move field details to tool docs. |
| Task creation | `task_creations only according to the task creation mandate in your wake prompt.` | Replace with: `Use team_create_task or team_create_tasks only according to the task creation mandate in your wake prompt.` |
| Review actions | `Use review_requests/review_decisions for deliverable review.` | Replace with: `Use team_request_review for existing deliverables, team_submit_deliverable_for_review when submitting and requesting review together, and team_decide_review only for reviews assigned to you.` |
| Goal updates | `goal_updates for project goals.` | Replace with: `Use team_update_goal for explicit project goal state changes.` |
| Git actions | `git_* actions for review/feature/release branch promotion.` | Replace with named tools: `Use team_publish_review_branch, team_update_feature_branch, team_request_release, and team_recover_release for Git promotion/release intent.` Move exact branch arguments into tool docs. |
| Stand-downs | `stand_downs/stand_down_revocations for direct-subordinate hold control.` | Replace with: `Use team_stand_down_agent and team_revoke_stand_down for direct-subordinate hold control.` |
| Ephemeral delegation | `ephemeral_* actions for scoped worker delegation.` | Replace with: `Use team_request_ephemeral_agent or team_request_ephemeral_agents for scoped worker delegation. Use team_accept_ephemeral_result or team_reject_ephemeral_result after a worker reports completion.` |
| Task graph awareness | `Each task carries derived graph context... Do not start tasks whose dependency_status is not 'satisfied'... Decompose large work by creating child tasks via task_creations with parent_task_id... Declare ordering with dependency_task_ids.` | Keep semantics. Replace `task_creations` wording with `team_create_task` / `team_create_tasks`; keep dependency guidance in durable instructions because it is decision guidance, not just argument schema. |
| `task_updates` detail | Current text explains owner-only fields, superior reassignment, and not accepting/rejecting others' tasks via `task_updates`. | Replace with `team_update_task` wording. Keep the warning: `Do not accept or reject someone else's delivered work with team_update_task; use review tools.` |
| `case_updates` detail | Current text explains cases track intake/source/priority/owner/lifecycle and tasks remain execution steps. | Replace with `team_create_case` / `team_update_case` wording. Move field shape details to tool docs. |
| Review artifact import | Current text says `review_requests` automatically imports artifact-space refs into reviewer `/artifacts/team-review-inputs`; do not pass local paths through `status_requests`. | Keep semantics. Replace action names: `team_request_review and team_submit_deliverable_for_review are the only handoff tools that trigger review artifact import.` |
| Non-Git deliverables | Current text tells agents to stage files under `/artifacts` and list relative paths in `deliverables[].artifact_paths`. | Replace with: `For non-Git deliverables, stage files under /artifacts and pass relative paths to team_submit_deliverable or team_submit_deliverable_for_review as artifact_paths.` |
| Rejected review rework | Current text says set `deliverables[].supersedes_deliverable_id` from the `review_rejected` inbox payload. | Replace with: `When responding to a review_rejected inbox item, call team_submit_deliverable or team_submit_deliverable_for_review with supersedes_deliverable_id from the payload.` |
| Review decision cascade | Current text says accepting a review cascades to deliverable/task acceptance once all reviews are accepted. | Keep, with tool name: `team_decide_review accepted/rejected/blocked drives the cascade.` |
| Manager fallback review routing | Current text says route idle profile-eligible reviewer gaps to `review_requests`; use `override_routing_gap_reason` only when intentionally bypassing. | Keep. Replace `review_requests` with `team_request_review`; keep `override_routing_gap_reason` in durable guidance because it is a rare policy exception. |
| Release sequencing | Current text says use `git_release_requests`, do not combine with `git_feature_updates`, use `git_release_recoveries` for blocked packages. | Replace with tool names: `Use team_request_release for accepted deliverables. Do not pair it with team_update_feature_branch as one compound action. Use team_recover_release only when release_package.next_action_hint identifies a failed release promotion and the resolved commit is pushed to an agents/... ref.` |
| Deliverable review handoff | Current text says create a `review_requests` item for another reviewer; do not mark the task accepted yourself unless it is yours. | Replace with `team_request_review` or `team_submit_deliverable_for_review`; keep the warning. |
| Operator directive inbox items | Current text says handle `operator_directive` by writing the appropriate actions to the action artifact and adding ids to `handled_inbox_item_ids`. | Replace with: `Handle operator_directive inbox items by calling the relevant Team MCP tools. Include the directive id in that tool's handled_inbox_item_ids, or call team_mark_inbox_handled after acting.` |
| `response_requested` | Current text says no thank-you/no-op replies when false; take action or mark handled. | Keep. Replace `handled_inbox_item_ids` wording with `per-tool handled_inbox_item_ids or team_mark_inbox_handled`. |
| Priority | Current text explains low/normal/high inbox priority. | Keep unchanged; this is behavioral guidance, not schema detail. |
| Team Chat | Current text says use `chat_replies` only when wake prompt gives `conversation_id` and `message_id`; use `operator_chat_messages` only for listed operators. | Replace with: `Use team_reply_to_chat only for a human operator message with conversation_id and message_id. Use team_send_operator_chat_message only for operators listed in operator_chat_participants.` |
| Stand-down prose warning | Current text says do not encode stand-downs as prose-only `status_requests`. | Keep semantics with tool names: `Use team_stand_down_agent or team_revoke_stand_down; do not encode stand-downs as prose-only team_request_status messages.` |

## Normal Wake Prompt

Source: `_build_prompt()`.

| Topic | Old instruction | New instruction |
| --- | --- | --- |
| Opening instruction | `Continue your assigned team-agent work... write the requested action artifact before finishing.` | Replace with: `Continue your assigned team-agent work. Use Team MCP action tools during this active dispatch only when there is Team state to change or a note to record. Tool calls validate and apply immediately. When you are done, simply finish your response; the normal session completion event closes the wake and records whether progress occurred.` |
| Managed guidance reference | `Follow the Team Add-on guidance in /home/agent/AGENTS.md, managed section truman-team-addon, version ...` | Keep, but remove artifact wording: `Follow the Team Add-on guidance in /home/agent/AGENTS.md... Use the Team context below for this dispatch and use the Team MCP tools for state changes.` |
| Role-aware delegation | `Use subordinates[].role_name and subordinates[].role_description. Reviewer roles should generally receive review_requests or review-focused tasks, not implementation task ownership.` | Keep. Replace `review_requests` with `team_request_review` or review-focused `team_create_task`. |
| Task creation mandate | `If create_root_task... You may create top-level tasks...`; otherwise `Every task_creations item you submit must set parent_task_id...` | Keep. Replace `task_creations item` with `team_create_task or team_create_tasks item`. |
| Deliverable mechanics, no owned tasks | `If no deliverable is ready, still write the Team action artifact with notes, questions, status_reports, or handled_inbox_item_ids... Do not finish without writing the action artifact.` | Replace with: `If no deliverable is ready, use team_send_progress_report, team_ask_question, team_mark_inbox_handled, or team_record_note only when one of those actions is actually useful. If there is nothing useful to do, do not call a Team tool; normal session completion will close the wake and count no progress.` |
| Deliverable mechanics, owned tasks | Current text gives valid owned `task_id` values, tells agents to write report files under `/artifacts`, and list relative paths in `deliverables[].artifact_paths`. | Keep valid task and `/artifacts` guidance. Replace final action instruction with: `Pass relative artifact_paths to team_submit_deliverable or team_submit_deliverable_for_review.` |
| Project repo checkout | `Project repository: work in the prepared guest checkout at ...` | Keep unchanged. |
| Prior handoff summary | `Prior handoff summary from Team state:` | Keep unchanged. |
| Review artifact access | Current text tells reviewers to inspect imported paths under `/artifacts` and block/ask lead if import errors exist. | Keep unchanged. This is context usage, not action schema. |
| Git release sequencing | Current prompt repeats release sequencing using `git_release_requests`, `git_feature_updates`, and `git_release_recoveries`. | Shorten and replace with named tools. Detailed argument rules move to tool docs. Keep the policy: release request stands alone; blocked package recovery follows `next_action_hint`. |
| Manager fallback | Current prompt says inspect `manager_fallback_summary`, route review gaps with `review_requests`, use `review_decisions` with override only when bypassing, and do not no-op. | Keep semantics. Replace actions with `team_request_review`, `team_decide_review`, `team_send_progress_report`, `team_escalate_operator`, or `team_record_note` as applicable. If there is truly nothing useful to do, no tool call is required. |
| Team context JSON | Includes `action_artifact_path`. | Remove `action_artifact_path`. Add `available_team_mcp_tools` or rely on MCP tool discovery; if included, it should be informational only. Keep Team context fields: agent, reason, superior/subordinates, tasks, cases, inbox, release packages, runtime attention, recent feedback where still needed, operator chat participants, repo info, review imports. |
| Contract files | `Team action contract files have been seeded at /artifacts/team-action.schema.json and /artifacts/team-action.example.json. Read schema... use example as writable template.` | Remove without direct replacement. Exact argument schemas and examples belong to MCP tool metadata. |
| Helper command | `A helper command has also been seeded at ... team-finish... fill schema_version, agent_id, dispatch_id, and write /artifacts/team-action.json.` | Remove. New instruction: `Do not write team-action.json. The tools infer identity and dispatch from the session.` |
| Final JSON shape | Prompt prints a large JSON object containing all action arrays. | Remove. New instruction: `Use the specific Team MCP tool for the action you intend. Use team_record_note only when you need to leave durable note context. If there is nothing to do, call no Team tool.` |

## Seeded Action Contract Files

Sources: `/artifacts/team-action.schema.json`,
`/artifacts/team-action.example.json`, and
`/home/agent/.agent-sandbox/bin/team-finish`.

| Old seeded file or instruction | Replacement |
| --- | --- |
| JSON Schema file for all action fields. | Remove. Tool schemas describe each action. |
| Example action JSON template. | Remove. Tool examples should live in MCP descriptions or optional generated help. |
| `team-finish [draft-action.json]` helper. | Remove. Normal session completion closes the wake; use `team_record_note` only when note text itself is useful. |
| Helper fills `schema_version`, `agent_id`, and `dispatch_id`. | Remove. MCP gateway/runtime infers caller identity and active dispatch. |
| Helper writes `/artifacts/team-action.json`. | Remove. Tools mutate Team state directly and synchronously. |

## Tool-Owned Documentation

These old instructions should move primarily into MCP tool descriptions:

| Old action field | Tool-owned replacement docs |
| --- | --- |
| `case_updates` shape and create/update behavior | `team_create_case`, `team_update_case` argument schemas and examples. |
| `task_creations` shape, `task_id` alias, dependencies | `team_create_task`, `team_create_tasks` schemas and validation errors. |
| `task_updates` fields | `team_update_task` schema. |
| `status_requests` shape | `team_request_status` schema. |
| `questions` shape | `team_ask_question` schema. |
| `status_reports` shape | `team_send_progress_report` schema. |
| `chat_replies` shape | `team_reply_to_chat` schema. |
| `operator_chat_messages` shape | `team_send_operator_chat_message` schema. |
| `operator_escalations` shape | `team_escalate_operator` schema. |
| `deliverables` shape, artifact refs, git refs | `team_submit_deliverable` and `team_submit_deliverable_for_review` schemas. |
| `review_requests` shape | `team_request_review` schema. |
| `review_decisions` criteria shape | `team_decide_review` schema. |
| `goal_updates` shape | `team_update_goal` schema. |
| `git_review_promotions` oneOf shapes | `team_publish_review_branch` schema. |
| `git_feature_updates` shape | `team_update_feature_branch` schema. |
| `git_release_requests` shape | `team_request_release` schema. |
| `git_release_recoveries` shape | `team_recover_release` schema. |
| `ephemeral_requests` worker options | `team_request_ephemeral_agent` and `team_request_ephemeral_agents` schemas. |
| `ephemeral_acceptances` shape | `team_accept_ephemeral_result` and `team_reject_ephemeral_result` schemas. |

## New Durable Instructions To Add

Add these to the managed normal Team agent guidance:

```text
Use Team MCP tools for Team state changes. Do not write team-action.json.
The tools infer your Team agent id and active dispatch from this session.
When retrying a mutating tool after a transport failure, reuse the same
request_id so the operation remains idempotent.
```

```text
When a tool returns a validation error, correct the request during the same
wake. Do not wait for a later wake to learn whether the action was accepted.
```

```text
When acting on an inbox item, include that inbox id in the action tool's
handled_inbox_item_ids when the tool supports it, or call
team_mark_inbox_handled after you have acted. Do not send no-op acknowledgements
for response_requested=false items.
```

```text
If there is no Team action to take, do not call a Team tool. The normal session
completion event closes the wake and records whether the dispatch made progress.
Use team_record_note only when durable note text is itself useful.
```

## Instructions Removed Without Replacement

- Read `/artifacts/team-action.schema.json`.
- Use `/artifacts/team-action.example.json` as a writable template.
- Use `/home/agent/.agent-sandbox/bin/team-finish`.
- Fill or preserve `schema_version`.
- Fill or preserve `agent_id`.
- Fill or preserve `dispatch_id`.
- Write `/artifacts/team-action.json` before finishing.
- Do not finish without writing the action artifact.
- Use an all-fields JSON object shape as the action contract.
