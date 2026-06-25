# Specialized Team Instruction Replacements

This document maps Team guidance surfaces that are not the ordinary persistent
Team wake prompt: Team Chat, governance, inactive Team agents, ephemeral
workers, missing-action recovery, and delayed action feedback. It is
design-only.

## Team Chat Wake

Source: `_build_team_chat_prompt()`.

| Topic | Old instruction | New instruction |
| --- | --- | --- |
| Chat classification | `You have received a message from a human operator in the Team Add-on Chat tab. This is not an agent-to-agent inbox message.` | Keep unchanged. This distinction is still important. |
| Reply mechanism | `Reply to this human operator by writing /artifacts/team-action.json with a chat_replies entry.` | Replace with: `Reply to this human operator by calling team_reply_to_chat with the provided conversation_id, message_id, and body.` |
| Avoid agent-to-agent tools | `Do not use questions or status_reports for this human chat reply; those are for agent-to-agent inbox communication.` | Keep semantics. Replace field names with tools: `Do not use team_ask_question or team_send_progress_report for a human chat reply.` |
| State change plus reply | `If the operator explicitly asks you to change Team state, include the corresponding Team action fields in the same action object alongside chat_replies; otherwise send only the chat reply.` | Replace with: `If the operator asks for a Team state change, call the relevant Team MCP action tool(s), then call team_reply_to_chat summarizing the result. Use the same request_id prefix for related calls when practical.` |
| Helper command | `A helper command has been seeded at ... team-finish... fill schema_version, agent_id, dispatch_id, and write team-action.json.` | Remove without replacement. The reply tool infers identity and dispatch. |
| Required reply JSON shape | Prompt prints a `chat_replies` JSON object with `schema_version`, `agent_id`, `dispatch_id`, `conversation_id`, `message_id`, and `body`. | Remove. The `team_reply_to_chat` tool schema owns the argument shape. |
| Conversation transcript | Current prompt includes current agent, operator, conversation id, message id, dispatch id, recent conversation, and human message. | Keep, except dispatch id is context only and should not be an argument. |

New Team Chat prompt text:

```text
You have received a message from a human operator in the Team Add-on Chat tab.
This is not an agent-to-agent inbox message. Use team_reply_to_chat to reply to
the human operator. Do not use team_ask_question or team_send_progress_report for
this human chat reply.

If the operator asks for a Team state change, call the relevant Team MCP action
tool first, then call team_reply_to_chat with a concise summary of what you did
or why the request could not be completed.
```

## Governance Managed Guidance

Source: `_team_governance_guidance_content()`.

| Topic | Old instruction | New instruction |
| --- | --- | --- |
| Governance role | `You are a Team Add-on governance manager. You may oversee governance groups even when your persistent agent record has no team_id.` | Keep unchanged. |
| Current context | `Treat each governance wake prompt as the source of current governance group, agenda, member-team summaries, cost signals, and blocker signals.` | Keep unchanged. |
| Boundary | `Member teams remain separate execution units. Do not take ownership of member-team project tasks, deliverables, reviews, releases, or direct-report chains.` | Keep unchanged. |
| Directives | `Route directives to the relevant member-team manager with the team_create_operator_directive MCP tool when action is needed.` | Keep unchanged. This is already MCP-oriented. |
| Agenda handling | `Mark governance agenda items handled with the team_mark_governance_agenda_handled MCP tool...` | Keep unchanged. |
| Artifact warning | `Governance wakes are not normal Team action-artifact dispatches... Do not write or rely on /artifacts/team-action.json for governance-only decisions.` | Replace with: `Governance wakes are not normal persistent Team action-tool dispatches unless the wake prompt explicitly says otherwise. Use governance MCP tools for governance-only decisions.` |
| Concision | `Keep decisions concise. If no governance action is needed, say so briefly and do not invent member-team project work.` | Keep unchanged. |

## Governance Wake Prompt

Source: `_build_governance_prompt()`.

| Topic | Old instruction | New instruction |
| --- | --- | --- |
| Continue as governance manager | `Continue as a Team Add-on governance manager.` | Keep unchanged. |
| Boundary | `Do not take ownership of member-team project tasks, deliverables, reviews, or releases...` | Keep unchanged. |
| Decision scope | `Use this wake to inspect agenda items, cost-cap signals, blocked-team signals, and member-team summaries.` | Keep unchanged. |
| Directive tool | `route ... directives ... with the team_create_operator_directive MCP tool.` | Keep unchanged. |
| Agenda tool | `Mark agenda items handled with the team_mark_governance_agenda_handled MCP tool...` | Keep unchanged. |
| No invented work | `Do not invent project work.` | Keep unchanged. |
| Governance context JSON | Current prompt includes governance context JSON. | Keep unchanged. |

Governance is already close to the desired model because it uses MCP tools
instead of the action artifact.

## Inactive Team Agent Guidance

Source: `_team_agent_guidance_inactive_content()`.

| Topic | Old instruction | New instruction |
| --- | --- | --- |
| No current Team assignment | `This persistent agent is not currently assigned to a Team.` | Keep unchanged. |
| Normal Team instructions do not apply | `Normal Team-agent task, deliverable, review, release, subordinate, and /artifacts/team-action.json instructions do not apply unless a future wake prompt explicitly assigns normal Team work.` | Replace with: `Normal Team-agent task, deliverable, review, release, subordinate, and Team action MCP tool instructions do not apply unless a future wake prompt explicitly assigns normal Team work.` |
| Governance exception | `If this agent also manages governance groups, follow the separate governance section and current governance wake prompt instead.` | Keep unchanged. |

## Ephemeral Worker Prompt

Source: `_build_ephemeral_prompt()`.

| Topic | Old instruction | New instruction |
| --- | --- | --- |
| Scope | `Complete this scoped ephemeral team-agent assignment. You are a disposable worker for one parent agent; do not spawn other agents, ask hierarchy questions, or update unrelated team state.` | Keep unchanged. |
| Assignment prompt/context | Current prompt includes assignment prompt and assignment context JSON. | Keep unchanged. |
| Result mechanism | `Before finishing, write a UTF-8 JSON action object to /artifacts/team-action.json using this shape...` | Replace with: `Before finishing, call team_report_ephemeral_result with the assignment result.` |
| `ephemeral_assignment_id` | Current JSON shape includes `ephemeral_assignment_id`. | New tool argument: `assignment_id`, optional when inferred from the caller session. |
| `summary` | Current JSON shape includes `summary`. | New tool argument: `summary`, required for successful completion. |
| `artifact_refs` | Current JSON shape includes `artifact_refs`. | New tool argument: `artifact_refs`, preserving current semantics. |
| `agent_branch` and `commit_sha` | Current repository accepts these even though the compact prompt shape does not show them. | New tool should document them. `agent_branch` must equal deterministic `agents/<session-id>/<assignment-id>` when supplied. |
| `error` | Current JSON shape includes `error`; setting it marks the assignment failed. | New tool argument: `error`; when set, the assignment is failed and `summary` may be optional. |
| `task_notes` | Current prompt shape includes `task_notes`, but repository apply currently ignores it. | Remove without replacement unless implementation chooses to persist it as audit metadata. |

New ephemeral prompt text:

```text
Complete this scoped ephemeral team-agent assignment. You are a disposable worker
for one parent agent; do not spawn other agents, ask hierarchy questions, or
update unrelated Team state.

Before finishing, call team_report_ephemeral_result. For successful work include
summary, artifact_refs when you produced artifacts, and agent_branch/commit_sha
when you produced Git work. If you could not complete the assignment, call the
same tool with error explaining the failure.
```

## Missing Action Recovery

Source: `_missing_action_recovery_prompt()`.

| Topic | Old instruction | New instruction |
| --- | --- | --- |
| Recovery cause | `Your previous Team dispatch completed without the required Team action artifact.` | Remove for MCP-native dispatches. There is no required artifact. Keep only for legacy action-file fallback. |
| Recovery limit | `Do not continue implementation work in this recovery turn. Write only the missing UTF-8 JSON action object...` | Remove for MCP-native dispatches. |
| Recovery alternatives | `If work is not ready for a deliverable, write a status_reports item... or a question... Include status-request inbox ids in handled_inbox_item_ids.` | Replace by normal same-wake tools: `Use team_send_progress_report, team_ask_question, team_submit_deliverable, team_mark_inbox_handled, or team_record_note during the original wake only when useful. If there is nothing useful to do, call no Team tool.` |
| Minimum JSON shape | Prompt prints minimal JSON action shape. | Remove. |

MCP-native replacement:

```text
If you have no Team action to take, call no Team tool. The normal session
completion event closes the wake and can recognize no progress. If a tool call
fails validation, correct it during this wake. There is no action artifact
recovery turn in the MCP-native path.
```

## Recent Action Feedback And Correction Hints

Sources: `recent_action_feedback` in wake context and `_correction_hints()`.

| Topic | Old instruction | New instruction |
| --- | --- | --- |
| Delayed validation feedback | `Your previous Team dispatch had validation errors. Do not repeat these mistakes... retry only missing corrected action items.` | Mostly remove for MCP-native validation errors because tools return errors immediately. Keep a smaller feedback path only for legacy action-file fallback and asynchronous failures that are discovered after the wake. |
| Artifact missing hint | `Before finishing any Team wake, write /artifacts/team-action.json. Use the seeded team-finish helper...` | Remove for MCP-native wakes. The normal session completion event closes the wake and can recognize no progress. Do not write `team-action.json`. |
| Branch-name hint | `Use bare branch names like agents/... or team/..., not refs/heads/...` | Move to relevant tool error messages for `team_publish_review_branch`, `team_update_feature_branch`, `team_recover_release`, and deliverable git refs. |
| Deliverable ownership hint | `Only the task owner can create deliverables for that task.` | Move to `team_submit_deliverable` and `team_submit_deliverable_for_review` tool errors. |
| Empty artifact paths hint | `For Git-only deliverables, omit artifact_paths; otherwise list staged paths under /artifacts.` | Move to deliverable tool descriptions/errors. |
| Rework lineage hint | `When revising a rejected deliverable, set supersedes_deliverable_id...` | Keep as durable guidance and in deliverable tool docs/errors. |
| Release source hint | `Release requests can auto-promote accepted Git deliverables only when they have git_refs.review_branch or git_refs.agent_branch...` | Move to `team_request_release` tool docs/errors and keep shorter durable release guidance. |
| Ephemeral promotion shape hint | `Use the assignment_id shape for ephemeral promotions, or the deliverable_id shape for persistent deliverables.` | Move to `team_publish_review_branch` tool docs/errors. |
| Existing release package hint | Current hint warns not to create another `git_release_requests` item for a blocked package and to use recoveries only with failed release promotion. | Replace with `team_request_release` / `team_recover_release` tool errors. Keep shorter durable guidance around `release_packages[id].next_action_hint`. |
| Existing review request hint | `For review handoff, do not reuse the existing review request id...` | Move to `team_request_review` tool errors. |

## Added Cross-Surface Instruction

Add this sentence to normal wake, Team Chat, and any other prompt where a human
operator request can lead to state mutation:

```text
When related tool calls are part of one human or inbox request, use a stable
request_id prefix so retries do not duplicate state changes.
```

## Removed Without Replacement

- Any instruction to write `/artifacts/team-action.json`.
- Any instruction to read `/artifacts/team-action.schema.json`.
- Any instruction to use `/artifacts/team-action.example.json`.
- Any instruction to use `team-finish`.
- Missing-action recovery turns for MCP-native wakes.
- Ephemeral `task_notes`, unless implementation intentionally persists it.
