# Team Agent Guidance Surface Inventory

This inventory lists every Team-specific instruction surface agents receive
today and where its MCP-era replacement is designed. It is design-only and does
not change prompts, seeded files, or runtime behavior.

Generic VM instructions from `templates/guest-AGENTS*.tmpl`, such as checking a
repository root `AGENTS.md`, are not Team action guidance and should remain
unchanged unless a later migration explicitly changes the generic guest
contract.

## Current Guidance Sources

| Source | Recipient | Current purpose | Migration doc |
| --- | --- | --- | --- |
| `_team_agent_guidance_content()` managed section in `/home/agent/AGENTS.md` | Normal persistent Team agents | Durable Team rules: identity, directional communication, task/case/review/release/action-field usage, inbox handling, chat handling, stand-downs. | `persistent-agent-instruction-replacements.md` |
| `_team_agent_guidance_inactive_content()` managed section in `/home/agent/AGENTS.md` | Persistent agents with no current Team assignment | Says normal Team task/action-artifact instructions do not apply unless a future wake assigns normal Team work. | `specialized-instruction-replacements.md` |
| `_team_governance_guidance_content()` managed section in `/home/agent/AGENTS.md` | Governance managers | Durable governance rules and existing governance MCP tools. | `specialized-instruction-replacements.md` |
| `_build_prompt()` normal wake prompt | Normal persistent Team agents on each wake | Dynamic dispatch context, task creation mandate, deliverable mechanics, repo checkout guidance, review artifact access, git release sequencing, manager fallback, action schema paths, and final action JSON shape. | `persistent-agent-instruction-replacements.md` |
| `_build_team_chat_prompt()` | Persistent agents woken by Team Chat | Tells the agent to reply to a human by writing `chat_replies` to `team-action.json` and to include state changes in the same action object. | `specialized-instruction-replacements.md` |
| `_build_governance_prompt()` | Governance managers on governance wakes | Dynamic governance context plus instructions to use governance MCP tools and avoid member-team project work. | `specialized-instruction-replacements.md` |
| `_build_ephemeral_prompt()` | Disposable ephemeral workers | Scoped assignment instructions and ephemeral action JSON shape. | `specialized-instruction-replacements.md` |
| `_missing_action_recovery_prompt()` | Persistent agents that finished without `team-action.json` | One-shot recovery turn to write the missing JSON artifact and do no more implementation work. | `specialized-instruction-replacements.md` |
| `recent_action_feedback` in normal wake context | Persistent agents after failed action validation | Delayed one-shot feedback about previous action validation errors. | `specialized-instruction-replacements.md` |
| `_correction_hints()` feedback text | Persistent agents after failed action validation | Follow-up correction hints, including writing `team-action.json` and using `team-finish`. | `specialized-instruction-replacements.md` |
| `/artifacts/team-action.schema.json` | Persistent Team agents | Seeded JSON Schema for the action file. | `persistent-agent-instruction-replacements.md` |
| `/artifacts/team-action.example.json` | Persistent Team agents | Seeded JSON example/template for the action file. | `persistent-agent-instruction-replacements.md` |
| `/home/agent/.evander/bin/team-finish` | Persistent Team agents and Team Chat wakes | Helper to fill `schema_version`, `agent_id`, and `dispatch_id`, then write `team-action.json`. | `persistent-agent-instruction-replacements.md` and `specialized-instruction-replacements.md` |

## Target Guidance Surfaces

The MCP migration should shrink prompt and seeded-file instructions. The target
surfaces are:

| Target surface | Purpose |
| --- | --- |
| Team MCP tool descriptions and schemas | Exact arguments, field defaults, validation rules, and examples for individual actions. This replaces most JSON Schema/example guidance. |
| Managed `/home/agent/AGENTS.md` Team section | Durable policy and decision guidance: when to use tools, who may be contacted, what not to do, and how to reason about work. |
| Dynamic normal wake prompt | Current context, active lease reminder, task creation mandate, role-aware delegation hint, repo checkout path, review artifact imports, manager fallback context, and instruction to use tools only when there is an action to take. |
| Dynamic Team Chat prompt | Current conversation context and instruction to use `team_reply_to_chat` plus other Team action tools if state changes are requested. |
| Dynamic governance wake prompt | Current governance context and instruction to use governance MCP tools. |
| Dynamic ephemeral prompt | Current assignment context and instruction to report completion through a dedicated ephemeral result tool. |
| Tool return errors | Immediate corrective feedback. This replaces most delayed next-wake action feedback. |

## Additional Tool Surface Found During Guidance Inventory

`team-mcp-action-surface.md` focuses on persistent-agent action fields. The
guidance inventory shows one additional current action-file surface: disposable
ephemeral workers report their result by writing `/artifacts/team-action.json`
with `ephemeral_assignment_id`, `summary`, `artifact_refs`, optional
`agent_branch`, optional `commit_sha`, and optional `error`.

The MCP migration therefore needs an additional worker-scoped tool, now included
in `team-mcp-action-surface.md`:

`team_report_ephemeral_result`

Arguments:

| Name | Type | Required | Semantics |
| --- | --- | --- | --- |
| `assignment_id` | string | no | Optional explicit assignment id; inferred from caller session when omitted. |
| `summary` | string | yes unless `error` is set | Result summary for successful work. |
| `artifact_refs` | string[] | no | Published artifact refs produced by the worker. |
| `agent_branch` | string | no | Must equal deterministic `agents/<session-id>/<assignment-id>` when supplied. |
| `commit_sha` | string | no | Commit produced by the worker. |
| `error` | string | no | Marks the assignment failed when set. |
| `request_id` | string | no | Idempotency key for retrying the report. |

`task_notes` appears in the current ephemeral prompt shape but is not applied by
the repository today. Unless implementation chooses to persist it as audit
metadata, the migration should remove that instruction without replacement.

## Migration Principles

- Keep the Team-specific tool surface add-on-owned.
- Remove instructions about writing, validating, or helper-generating
  `team-action.json`.
- Preserve authority rules: direct superior/upward messages, direct subordinate
  status requests, project/team/review/release permissions, and stand-down
  boundaries.
- Move field-level shape documentation into MCP tool schemas.
- Keep durable guidance about when and why to use a tool in `AGENTS.md` and
  dynamic prompts.
- Prefer immediate tool correction over next-wake feedback.
- Keep long-running git and worker effects asynchronous; tools record intent and
  return current state/event ids.
