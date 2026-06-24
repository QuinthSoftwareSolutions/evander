# Truman Add-ons

Add-ons are optional, opinionated layers that run on top of Truman's low-level
orchestration primitives. Truman core remains responsible for sessions,
execution backends, workflow events, effects, artifacts, communication policy,
and audit. Add-ons may consume those primitives, but must not make their domain
concepts required by core.

## Boundary Contract

Add-ons may register generic integration points:

- Admin Web API routes under `/api/addons/<addon_id>/...`
- Admin Web navigation metadata and optional UI routes under `/addons/<addon_id>/...`
- Workflow providers using the same trigger/gate/effect/query provider shape as
  `agent_sandbox.providers`
- Workflow editor catalog metadata through those workflow provider registrations
- Host-side MCP tools registered through the add-on MCP registrar and governed
  by the same session, directory policy, approval, and audit machinery as core
  tools
- Private runtime state under `runtime/addons/<addon_id>/`

Add-ons must not add domain-specific fields to core models such as
`SessionManifest`, nor should they require existing workflows, workflow packs,
or Admin Web pages to adopt their abstractions.

## Loading

Add-on discovery is configured with `addon_loader`:

```json
{
  "addon_loader": {
    "modules": ["my_company.truman_team_addon"],
    "enabled_entry_points": false,
    "enabled_addon_ids": null
  }
}
```

External packages can also expose Python entry points in the
`agent_sandbox.addons` group. Entry-point loading is disabled by default and
must be explicitly enabled.

An add-on module should expose `register_addon()` and return either an
`AddonRegistration` or a compatible mapping:

```python
from starlette.routing import Route


def register_addon():
    return {
        "addon_id": "team-addon",
        "display_name": "Team Add-on",
        "admin_navigation": {
            "label": "Teams",
            "short_label": "Teams",
            "description": "Team-oriented add-on surface.",
            "ui_path": "/addons/team-addon/",
        },
        "admin_api_routes": [Route("/health", health, methods=["GET"])],
        "admin_ui_routes": [Route("/", index, methods=["GET"])],
        "workflow_providers": {
            "effect_providers": {
                "effect:team:example": ExampleEffectProvider,
            },
            "workflow_editor_catalog_entries": [
                {
                    "kind": "effect",
                    "type": "effect:team:example",
                    "label": "Team example",
                    "description": "Run the add-on owned Team example effect.",
                    "fields": [],
                },
            ],
        },
    }
```

## Storage

Use the add-on storage helpers from `agent_sandbox.addons` so state remains
scoped and removable:

```python
from agent_sandbox.addons import addon_json_object_store

store = addon_json_object_store(runtime_dir, "example-addon", "state.json")
```

The helper rejects malformed add-on ids, absolute paths, and traversal segments.
Removing `runtime/addons/<addon_id>/` must not corrupt Truman core state.

## Skatteverket Add-on Guidance

The bundled `evander_skatteverket_addon` package provides an optional
Skatteverket accounting and filing surface. Enable it with:

```json
{
  "addon_loader": {
    "modules": ["evander_skatteverket_addon"],
    "enabled_entry_points": false,
    "enabled_addon_ids": ["skatteverket-addon"]
  }
}
```

When enabled, Admin Web shows a `Skatteverket` tab backed by
`/api/addons/skatteverket-addon/...` and `/addons/skatteverket-addon/`. The
add-on keeps canonical state in `runtime/addons/skatteverket-addon/state.db`.
It exposes add-on-owned workflow nodes under `query:skatteverket:*` and
`effect:skatteverket:*`, plus host-side MCP tools named `skatteverket_*`.

The add-on is intentionally host-side:

- Skatteverket credentials, API keys, certificates, and OAuth metadata stay on
  the control plane.
- Agents only receive MCP/workflow results, never raw secrets.
- Submission tools require an exact draft-revision approval request before a
  stored draft can be submitted.
- APIs that are represented in the catalog but lack a configured adapter fail
  closed with `not_implemented_for_v1` or `blocked` status.

Skatteverket production access still requires external onboarding, agreements,
API keys, OAuth2 setup, and in some cases e-legitimation or
organisationslegitimation. Configure connection metadata through the add-on API
after that onboarding is complete.

## Bokio Add-on Guidance

The bundled `evander_bokio_addon` package provides an optional assisted
bookkeeping surface for Bokio private integrations. Enable it with:

```json
{
  "addon_loader": {
    "modules": ["evander_bokio_addon"],
    "enabled_entry_points": false,
    "enabled_addon_ids": ["bokio-addon"]
  }
}
```

When enabled, Admin Web shows a `Bokio` tab backed by
`/api/addons/bokio-addon/...` and `/addons/bokio-addon/`. The add-on stores
prepared bookkeeping actions under `runtime/addons/bokio-addon/` and exposes
workflow nodes under `query:bokio:*` and `effect:bokio:*`, plus host-side MCP
tools named `bokio_*`.

Configure the private integration on the host:

```bash
export BOKIO_COMPANY_ID=<company-guid>
export BOKIO_INTEGRATION_TOKEN=<private-integration-token>
```

Optional provider config can override the token and company-id environment
variable names with `provider_state.bokio.integration_token_env` and
`provider_state.bokio.company_id_env`; do not store the token value in config,
runtime state, guest images, or session manifests.

The Bokio add-on is intentionally an assisted-bookkeeping layer:

- Agents can inspect company information, accounts, journal entries, invoices,
  supplier invoices, uploads, and prepared actions.
- Agents can prepare journal entries, supplier invoices, and bank payments as
  local add-on actions for operator approval.
- Document uploads read only from add-on-owned staging directories under
  `runtime/addons/bokio-addon/uploads/`; MCP uploads use
  `uploads/sessions/<session-id>`, workflow uploads use
  `uploads/scopes/workflows` unless a `session_id` is supplied, and Admin API
  uploads use `uploads/scopes/admin`.
- Creation tools execute only approved prepared actions and should also be
  policy-gated with MCP invocation approval for production sessions.
- Bank payments created through Bokio still require a signatory to log in to
  Bokio and sign with BankID before submission to the bank.
- Supplier invoice support uses Bokio's preview API surface and should be
  treated as less stable than company, account, journal-entry, invoice, upload,
  and SIE reads.

## Team Module Guidance

A hierarchical agent-team module should live as an add-on outside Truman core.
Team, manager, project lead, task, deliverable, review, and release-package
schemas belong in that package. Only generic hooks that are useful to other
add-ons should be proposed for Truman core.

The bundled `truman_team_addon` package provides the initial state-model layer
for this pattern. It is still disabled by default; enable it with
`addon_loader.modules: ["truman_team_addon"]` or by enabling the
`agent_sandbox.addons` entry-point group. For the bundled add-on, explicit
module loading is the recommended operator path because entry-point loading is
disabled by default. Its canonical state lives in relational SQLite under
`runtime/addons/team-addon/state.db`; a legacy
`runtime/addons/team-addon/state.json` file is imported once when the database
is first initialized. Truman session metadata remains a non-canonical
integration index.

When the Team Add-on is enabled, Admin Web shows an optional `Teams` tab. The
tab is served from the add-on's own `/addons/team-addon/` UI route and uses the
add-on API under `/api/addons/team-addon/`; it does not add team-specific views
or models to Truman core Admin Web pages. The bundled UI now includes a
dedicated Review & Delivery workspace that groups deliverables, review
requests, review profiles, release packages, and Git promotion state while
keeping the existing setup forms available in the side panel.

The `Teams` tab includes an org-chart hierarchy view for each team. It renders
the manager, subordinate agents, nested reporting lines, and unreachable or
malformed hierarchy references without changing persisted state. Agent nodes
show `display_name` when it is present and fall back to the stable agent id;
the create-team and register-agent forms expose optional display-name fields
for managers and additional agents. The create-project form assigns an existing
registered team agent as project lead instead of collecting a session ID,
display name, or role name inline. Project leadership is stored on the project,
so a team manager can also lead a project without changing that agent's primary
team-manager role or reporting line. Hover
or keyboard focus on an agent node reveals operational details from the add-on
state payload, including session, project, reports-to, task, inbox, runtime
wake, and ephemeral-capacity information when those records are available.

Persistent agents may also have an optional Team Add-on avatar. Avatars are
operator-facing UI metadata only: they are stored in Team Add-on state and
add-on-private image files under `runtime/addons/team-addon/avatars/`, and are
not mirrored into session metadata, prompts, wake context, or core Truman agent
identity. Operators can upload PNG/JPEG/WebP images, which the add-on normalizes
to square WebP assets, or generate an OpenAI-backed preview and explicitly
accept it before it becomes the saved avatar.

The People tab can start a direct browser voice call to the selected persistent
agent. The add-on prepares an OpenAI Realtime browser session with the selected
agent's current team, role, task, inbox, review, deliverable, runtime, and
visible tool context; it does not use DIDWW, Asterisk, or SIP. Calls are
recorded in `runtime/voice-sessions-store.json`, and terminal calls schedule the
same voice-session reconciliation wake used by other projected voice sessions.
When the voice projection needs details outside the projected context, it can
delegate a durable `query_origin_agent` request back to the originating text
agent.

The Chat tab stores conversations as one human operator plus one persistent
agent. Operators see their own conversations by default. Users with
`team_chats.read_all` may select another operator's chat history for read-only
inspection; the UI labels that mode separately from "My chats" and blocks
sending while inspecting. Team chat messages recorded by the Admin API carry
human-operator metadata, and chat wake prompts tell the agent that the source is
a human operator rather than another Team agent.

Team roles are first-class state records under `TeamState.roles`. Agents store a
`role_id` instead of a free-form role string, and role records carry the display
name, optional project scope, default ephemeral policy, and permission list for
that role. The Admin API exposes role CRUD at `/api/addons/team-addon/roles`,
and the bundled UI includes a `Roles` view plus create/edit forms. Bundled
permissions include `create_root_task`, `prepare_bookkeeping_action`,
`review_bookkeeping_action`, `request_payment_preparation`, and
`request_release`; release authorization checks the agent's role permission
rather than an exact role name. The built-in `auto-dev-squad` template grants
`request_release` to its project-scoped release-operator role, and the
`accounting-department` template grants the bookkeeping permissions to its
preparation and review roles.

Admin API write routes require `expected_revision`. Missing or null revisions
return `400`, stale revisions return `409`, and successful writes advance the
Team state revision. Workflow effects and agent-action application may omit
`expected_revision` when the workflow is operating as the single writer for that
step; they still honor the revision check when a value is provided.

### Template Catalog And Setup Flow

The bundled Team Add-on exposes a built-in template catalog at
`/api/addons/team-addon/templates`. The Admin Web `Teams` tab includes a
`Setup from template` panel that loads that catalog, previews the selected
template, and materializes it with one optimistic request to
`POST /api/addons/team-addon/templates/{template_id}/materialize`.

The built-in catalog currently includes:

- `auto-dev-squad`: an opinionated
  manager/project-lead/implementer/reviewer/release-operator squad for reviewed
  auto-dev delivery.
- `accounting-department`: an accounting operations department with an
  accounting manager, bookkeeping lead, bookkeeping clerk, invoice reviewer,
  payment preparer, and tax-compliance reviewer. It is intended for
  Bokio/Skatteverket-assisted bookkeeping coordination and deliberately omits
  Git setup fields.

The preview shows the planned role hierarchy, review rubric, project goal,
starter tasks, optional Git fields, and the concrete inputs that must be
supplied before creation.

Required inputs are:

- `team.id` and `team.name`
- `team.manager_agent_id` and `team.manager_session_id`
- `project.id` and `project.name`
- `project.lead_agent_id` and `project.lead_session_id`
- `agents.implementer.agent_id` and `agents.implementer.session_id`
- `agents.reviewer.agent_id` and `agents.reviewer.session_id`
- `agents.release_operator.agent_id` and `agents.release_operator.session_id`

The generalized request shape may instead provide template role keys under a
top-level `roles` object, for example `roles.manager.session_id` and
`roles.bookkeeping_clerk.session_id`. If `team.id`, `project.id`, or individual
role `agent_id` values are omitted, the repository generates stable ids from
the team, project, and role labels.

Optional inputs are:

- `team.operator_communication_target_id`
- `project.git.host_repo_path`
- one of `project.git.bare_repo_name` or `project.git.bare_repo_path`
- `project.git.base_branch`, `project.git.canonical_origin_url`,
  `project.git.provider`, and `project.git.canonical_release_branch_prefix`

The Team Add-on setup, project-create, and project-edit forms include an
`Import Git repo` action beside the Git fields. It accepts the current host repo
path or canonical origin URL as the repo source, creates or refreshes a
control-plane bare mirror under the configured root `issue_setup.git_root`, and
fills the form with the resolved `host_repo_path`, `bare_repo_name`, base
branch, canonical origin URL, and provider when they can be inferred. The import
does not refresh an existing mirror once it contains `refs/heads/agents/**`
work; create a new bare repo name instead. It does not save Team state by
itself; review the populated fields and submit the form normally.

The template uses a fixed hierarchy:

- `manager -> project_lead -> implementer`
- `manager -> project_lead -> reviewer`
- `manager -> project_lead -> release_operator`

It also seeds one project goal, two initial tasks, and one review profile with
criteria for `scope`, `tests`, `risk`, and `handoff`. The accounting template
seeds one bookkeeping-operations goal, five starter tasks for receipt,
supplier-invoice, payment, and tax-compliance work, and one review profile with
criteria for supporting documentation, Bokio mapping, tax treatment, and the
human-approval boundary. Generated goal, task, and review-profile ids are
prefixed with the concrete `project.id`, so separate projects can be
materialized from the same template without colliding.

Setup is atomic. The request must include `expected_revision`; stale revisions
fail without changing state, and duplicate ids are rejected before any write.
The resulting records live only in Team Add-on state. They do not create real
Truman sessions, VMs, Git repositories, Bokio entries, payments, or tax filings.

Example Auto-Dev compatibility request shape:

```json
{
  "expected_revision": 0,
  "team": {
    "id": "team-auto-dev-squad",
    "name": "Auto-Dev Squad",
    "manager_agent_id": "manager-auto-dev-squad",
    "manager_session_id": "session-manager-auto-dev-squad"
  },
  "project": {
    "id": "project-auto-dev-squad",
    "name": "Reviewed Auto-Dev Delivery",
    "lead_agent_id": "project-lead-auto-dev-squad",
    "lead_session_id": "session-project-lead-auto-dev-squad"
  },
  "agents": {
    "implementer": {
      "agent_id": "implementer-auto-dev-squad",
      "session_id": "session-implementer-auto-dev-squad"
    },
    "reviewer": {
      "agent_id": "reviewer-auto-dev-squad",
      "session_id": "session-reviewer-auto-dev-squad"
    },
    "release_operator": {
      "agent_id": "release-operator-auto-dev-squad",
      "session_id": "session-release-operator-auto-dev-squad"
    }
  }
}
```

### Team Activity Timeline

The Teams tab includes a derived `Timeline / Audit` view backed by the current
`TeamState` snapshot. `GET /api/addons/team-addon/timeline` returns a read-only
timeline; it is not an append-only audit log, so overwritten intermediate
changes are not reconstructed and legacy records without timestamps stay
timestamp-missing.

The endpoint accepts these filters:

- `team_id`
- `project_id`
- `agent_id` matches the entry's actor or any related agent id
- `type` supports repeat values or comma-separated lists
- `status` supports repeat values or comma-separated lists
- `attention_needed=true|false`

Each entry carries JSON-safe fields for `id`, `type`, `category`,
`timestamp`/`timestamp_source`, `timestamp_missing`, `actor_agent_id`,
`related_agent_ids`, `team_id`, `project_id`, the flat related ids
(`task_id`, `deliverable_id`, `review_request_id`, `inbox_item_id`,
`action_record_id`, `ephemeral_assignment_id`, `git_promotion_id`,
`release_package_id`), `status`, `attention_needed`, and `summary`.

The view includes these entry families:

- task created and updated entries
- inbox item created and handled or updated entries when `updated_at` differs
- deliverable created entries
- review request created entries, with `timestamp: null` and
  `timestamp_missing: true` for legacy requests without `created_at`
- review decision entries at `decision_at`
- action record created entries
- ephemeral assignment requested entries and lifecycle state entries when
  `updated_at` differs
- git promotion requested and result entries
- release package created entries, plus published or blocked state entries

Runtime attention hides failed action records after a later successful action
from the same agent. Operators can also dismiss a failed-action attention card;
this sets dismissal metadata on the action record for audit instead of deleting
the record.

Entries sort chronologically ascending when they have a timestamp. Entries with
missing timestamps sort after dated entries, with stable secondary ordering by
category, type, and id. In the UI, missing timestamps render as `Timestamp
unavailable`.

When enabled, the Team Add-on also registers an optional runtime-loop provider
set:

- `effect:team:wake-due-agents` leases due persistent agents, prompts their
  Truman sessions, and mirrors a compact `metadata.team_addon` index into the
  session manifest. When the agent's project has `project.git` configured, the
  wake also prepares the project repo through `effect:agent:clone-repo` before
  the resume prompt and includes the prepared guest checkout path in the Team
  context. Repo preparation is reported in the wake node outputs as
  `prepared_project_repos` or `failed_project_repo_preparations`; it is not a
  separate workflow node because one wake tick can lease multiple agents.
  Static Team operating rules are seeded as a managed Team Add-on section in
  `/home/agent/AGENTS.md`, so repeated same-context wakes only need to carry the
  dynamic dispatch context and action artifact reminder.
  Each persistent agent can set `context_clearing_policy` to `off`, `idle`, or
  `every_wake`. `idle` preserves the original automatic behavior by rotating
  after real work has completed and the agent becomes idle. `every_wake` starts
  each otherwise-eligible Team wake with a fresh context without bypassing
  low-priority batching or waking idle agents by itself.
  `low_priority_wake_after_cycles` defaults to `4`; low-priority inbox items
  count Team wake evaluations and are batched until the threshold is reached
  unless another wake reason delivers them earlier.
- `query:team:agent-action-artifact` reads the structured JSON action artifact
  that a prompted persistent agent must write to `/artifacts/team-action.json`.
- `effect:team:apply-agent-action` validates the action, updates add-on state,
  routes questions/status reports/deliverable notices, and clears the runtime
  lease. Persistent actions may include `ephemeral_requests` to request bounded
  disposable workers and `ephemeral_acceptances` to promote or reject completed
  ephemeral results.
- If the most recent recorded action for an agent failed validation, the next
  successful `effect:team:wake-due-agents` dispatch includes one-shot
  `recent_action_feedback` for that failed dispatch only. The runtime marks that
  feedback delivered after the resume succeeds, so older failures are not
  replayed and the same feedback does not repeat on later wakes.
- `effect:team:create-operator-directive` turns an operator request from
  Discord, Teams, Admin Web, or another workflow into a durable
  `operator_directive` inbox item. The item defaults to the team manager,
  resets the manager's no-progress throttle, and is handled by the next normal
  Team wake through `team-action.json`.
- `team_create_operator_directive` is the add-on-owned MCP tool for governance
  managers. It creates the same durable `operator_directive` inbox item, but
  only for member Teams of a governance group managed by the caller, and only
  for the target Team manager inbox.
- `team_mark_governance_agenda_handled` is the add-on-owned MCP tool for
  governance managers to mark agenda items in their own governance groups
  handled after they have decided, routed any needed directive, or determined no
  action is needed.
- Updating a governance group's member Teams records a pending `policy_decision`
  agenda item with the added, removed, previous, and current member Team IDs.
  This clears any delayed governance wake for that group so the manager receives
  a fresh governance prompt with the new member-team summaries on the next Team
  runtime evaluation.
- `query:team:session-routing` classifies a completed Truman session as a
  persistent team agent, ephemeral assignment, ignored non-Team dispatch, or
  unknown session before action artifacts are applied. Persistent agents are
  routed only when the completed dispatch id matches the active Team runtime
  lease, so ordinary Discord/Teams wake completions do not create missing
  `team-action.json` failures.
- `effect:team:mark-ephemeral-spawned`, `effect:team:apply-ephemeral-action`,
  `effect:team:accept-ephemeral-deliverable`, and
  `effect:team:mark-ephemeral-cleaned-up` track the ephemeral worker lifecycle
  around normal Truman `effect:agent:create`, `effect:agent:prompt`, and
  `effect:agent:destroy` nodes.
- `query:team:state` returns filtered add-on state for workflows or UI.

These node types are add-on-owned. They are not present unless the add-on is
loaded, and their workflow-editor catalog entries are registered by the add-on's
provider registration rather than hard-coded into Truman core. The packaged
`truman_team_addon.workflow_packs.TEAM_RUNTIME_V1` definition is a current-schema
workflow pack for the intended cron plus agent-completion loop. Operators can
write that definition into an operator-owned pack file when they want this
opinionated runtime.

### Agent Action Protocol

A prompted persistent agent drives the team by writing a single structured JSON
action artifact to `/artifacts/team-action.json`. `effect:team:apply-agent-action`
validates that artifact against the add-on state, records per-item errors without
aborting the rest of the action, and returns the ids it produced. All of the
fields below are optional lists; an agent includes only the ones it needs. Each
applied item counts as progress and resets the agent's `no_progress_count`.

- `task_creations[]` — delegate work down or across the hierarchy. Shape:
  `{"id"?, "owner_agent_id", "project_id", "priority"?, "title", "description",
  "state"?, "parent_task_id"?, "dependency_task_ids"?}`. Top-level task
  creation requires the actor's role to hold `create_root_task`; the built-in
  manager and project-lead roles carry that permission, while ordinary worker
  roles do not. Agents without `create_root_task` must create child tasks under
  one of their own active parent tasks by setting `parent_task_id`. The actor may
  create a task for itself, for a direct subordinate, for any agent in a project
  it leads (`lead_agent_id`), or — as the team manager — for that project's
  direct lead. Targets outside the actor's team or outside the target project
  are rejected. Parent and dependency tasks must belong to the same project,
  parent cycles and dependency cycles are rejected, tasks default to `pending`,
  and tasks may only start in a non-terminal state. Created ids are returned in
  `created_task_ids`.
- `task_updates[]` — update owned task details or maintain dependency edges.
  Shape: `{"task_id", "state"?, "priority"?, "title"?, "description"?,
  "dependency_task_ids"?}`. State, priority, title, and description remain
  owner-only. Dependency edits may also be made by a supervising agent, the
  project lead, or the team manager, and use the same same-project and cycle
  validation as task creation. Do not use `task_updates` to accept or reject
  another agent's delivered work; use `review_requests[]` and
  `review_decisions[]` so the deliverable/task cascade is applied. Updated ids
  are returned in `updated_task_ids`.
- `status_requests[]` — ask a direct subordinate for an update (downward).
  Shape: `{"recipient_agent_id", "task_id"?, "question"?,
  "response_requested"?, "priority"?}`. The recipient must be a direct
  subordinate; otherwise the item is rejected. `response_requested` defaults to
  `true` and `priority` defaults to `"normal"`. A `STATUS_REQUEST` inbox item is
  created for the subordinate and surfaced through the normal pending-inbox wake.
  Returned in
  `created_status_request_ids`.
- `questions[]` and `status_reports[]` — talk upward only. Shape:
  `{"recipient_agent_id"?, "task_id"?, "response_requested"?, "priority"?, ...}`; the
  recipient defaults to the actor's `reports_to_agent_id` and must equal it. A
  top-level manager (`reports_to_agent_id` is `None`) receives a clear per-item
  error directing it to `operator_escalations` instead. `questions` default
  `response_requested` to `true` and `priority` to `"normal"`; `status_reports`
  default `response_requested` to `false` and `priority` to `"low"`. These create
  `QUESTION` and `PROGRESS_REPORT` inbox items.
- `operator_escalations[]` — manager-to-operator escalation. Shape:
  `{"title", "message", "urgency"?, "project_id"?, "task_id"?}` (`urgency`
  defaults to `"normal"`). Only the team's top-level manager may escalate. If the
  team has no `operator_communication_target_id`, the item is recorded as a
  failed action and **no** event is emitted. On success an `ESCALATION` inbox
  item is written for audit and a `team.operator.escalation_requested` event is
  published with payload `{team_id, manager_agent_id, communication_target_id,
  title, message, urgency, project_id?, task_id?}`. Escalation ids are returned
  in `operator_escalation_event_ids`.
- `stand_downs[]` and `stand_down_revocations[]` — direct-superior hold control.
  Shape: `{"agent_id", "reason"}` to stand down a direct subordinate and that
  subordinate's subtree; `{"agent_id"}` to revoke a superior stand-down the
  actor previously set. Held agents are skipped by future Team wake leasing, and
  already-running dispatches from a held agent cannot apply non-empty Team
  actions. Operator stand-downs are a separate source and cannot be revoked by
  these agent-authored action items.
- `operator_directive` inbox items — operator-to-manager instructions created
  by the add-on Admin API or `effect:team:create-operator-directive`. These are
  not agent-authored action items. The manager handles them during its normal
  Team dispatch by taking the requested Team actions and listing the inbox id in
  `handled_inbox_item_ids`.
- `operator_chat_messages[]` — send a built-in Team Chat message to a known
  human operator. Shape: `{"recipient_operator_user_id", "body", "summary"?}`.
  Recipients must be listed in the dispatch context's
  `operator_chat_participants`; this is direct human chat, not an agent-to-agent
  inbox item. Returned in `created_operator_chat_message_ids`.
- `deliverables[]` — record finished work for an owned task. Shape:
  `{"id"?, "task_id", "summary", "artifact_paths"?, "artifact_refs"?,
  "artifact_space_ref"?, "git_refs"?, "supersedes_deliverable_id"?}`.
  When responding to a `review_rejected` inbox item, set
  `supersedes_deliverable_id` to the rejected deliverable id from the inbox
  payload. `artifact_paths` are relative paths under the session `/artifacts`
  folder; when present, the Team runtime publishes only those files or
  directories into the control-plane artifact store, records the latest version under
  `/teams/<team_id>/projects/<project_id>/deliverables/<deliverable_id>`, and
  appends the resulting `artifact-space:` ref to `artifact_refs`. Failed artifact
  publication rejects that deliverable item without creating a ref-less
  deliverable. Deliverable notices and review-request inbox items include the
  final `artifact_refs`. Reviewer wakes automatically import review-request
  `artifact-space:` refs into
  `/artifacts/team-review-inputs/<review>/<deliverable>/<artifact>/<ref-version>`
  and surface successful imports or import errors in the Team context JSON.
- `review_requests[]` — request a review of a deliverable; reviews are never
  auto-assigned. Shape: `{"id"?, "deliverable_id", "reviewer_agent_id",
  "review_profile_id"?, "instructions"?}`. The actor must be the deliverable's
  `producer_agent_id`, supervise the producer (direct or transitive), or hold
  project-lead/manager authority over the deliverable's project; the reviewer
  must be in the same team. When `review_profile_id` is provided, the profile
  must be active, in the same team, applicable to the deliverable's project, and
  applicable to the assigned reviewer's role when the profile lists roles.
  Leads that want reviewer-agent sign-off create a review request for that
  reviewer; reassigning an existing review request is not part of the agent
  action protocol. Returned in `created_review_request_ids`.
- `review_decisions[]` — decide a review. Shape: `{"review_request_id",
  "state", "findings"?, "criteria"?}` where `state` is `accepted`, `rejected`,
  or `blocked`, and `criteria[]` contains `{"criterion_id", "state",
  "finding"?}` entries with criterion state `passed`, `failed`, or `skipped`.
  Criterion ids must exist on the review profile attached to the review request.
  Only the assigned `reviewer_agent_id` may decide. Returned in
  `decided_review_request_ids`.
- `goal_updates[]` — set explicit goal acceptance. Shape: `{"project_id",
  "goal_id", "state"}`. Only that project's `lead_agent_id` or the team manager
  may update goals; goal acceptance is always explicit and never inferred from
  text. Returned in `updated_goal_ids`.
- `git_review_promotions[]`, `git_feature_updates[]`, and
  `git_release_requests[]` — the Git delivery actions, documented under
  *Deterministic Git Branch Naming* and *Git Promotion And Release Publication*
  below. They populate `git_promotion_event_ids` and `release_package_ids`.

Ephemeral workers report through their own `/artifacts/team-action.json`, which
may additionally carry `agent_branch` and `commit_sha`. When present,
`agent_branch` must equal the deterministic `agents/<session-id>/<assignment-id>`
name for that assignment; a mismatch fails the assignment rather than recording a
branch the add-on cannot later promote.

Persistent agents may include `git_refs` on a deliverable with `agent_branch`
and/or `commit_sha` when the project has Git configured. Those refs are not
published automatically; a supervising agent, project lead, or manager requests
publication with `git_review_promotions[]`.

### Communication And Escalation Rules

Team communication is directional and enforced by the repository, not by prose:

- **Upward** — `questions` and `status_reports` may only target the actor's
  direct superior. There is no skip-level messaging.
- **Downward** — `status_requests` may only target a direct subordinate.
- **Reply intent** — inbox items carry `response_requested`. When it is `false`,
  the recipient should mark the item handled after reading or acting on it, but
  should not send thank-you, acknowledgement, or other no-op replies. This
  metadata does not delay delivery; priority and batching are separate runtime
  features.
- **Manager to operator** — only the top-level manager escalates, and only when
  `Team.operator_communication_target_id` is set. The repository emits
  `team.operator.escalation_requested`; the packaged `TEAM_RUNTIME_V1` pack routes
  that event to an `effect:communication:send-message` node bound to the team's
  `operator_communication_target_id`. The repository and effect providers never
  call the communication sender directly — routing is the workflow pack's job.
- **Operator or governance manager to manager** — use the add-on
  `POST /operator-directives` Admin API, `effect:team:create-operator-directive`,
  or the `team_create_operator_directive` MCP tool for governance managers.
  These surfaces create a pending `operator_directive` inbox item for the
  manager and mark the manager due, so an instruction like "ask the lead for
  status on next dispatch" becomes normal Team work instead of an out-of-band
  `team-action.json` write.
- **Governance agenda completion** — governance managers use the
  `team_mark_governance_agenda_handled` MCP tool to mark agenda items handled
  for groups they manage. Operators can still use the Admin API or UI handle
  action.
- **Team Chat to a human** — use `chat_replies` only to answer a specific human
  operator message from the built-in Chat tab. Use `operator_chat_messages[]`
  only for human operators listed in the current dispatch context. Do not use
  `questions`, `status_reports`, or `operator_chat_messages[]` for normal
  agent-to-agent coordination.
- `Team.operator_communication_target_id` is outbound-only. It does not grant the
  manager's real session inbound Discord or Teams access and does not wake that
  session by itself. If the operator should message or wake the manager through
  Discord, create the manager agent/session with a read-capable Discord
  communication target in its session allowlist and grant `discord_get_commands`.
- **Operator stand-down** — use `POST /agents/{agent_id}/stand-down` with a
  required `reason` to disable that agent and its subordinate subtree. Held
  agents are skipped by future Team wake leasing, and any already-running
  dispatch that later submits non-empty Team actions is recorded as a failed
  action instead of being applied. Operator holds are durable state and can only
  be cleared through `POST /agents/{agent_id}/stand-down/revoke`; agent actions
  cannot revoke or override them.

Inbox priority controls wake timing, not visibility or permission. `high` and
`normal` pending inbox items wake recipients immediately on the next Team wake
evaluation. `low` items are visible in Team state immediately but do not wake an
otherwise idle recipient until `low_priority_wake_after_cycles` evaluations have
seen that low-priority work. Additional low-priority items do not reset an active
countdown. Any other wake reason cancels the countdown and includes all pending
low-priority inbox in that dispatch.

### Action-Driven Reviews And Decision Cascade

Reviews are created explicitly and decided only by the assigned reviewer. A
decision cascades deterministically through the deliverable, task, and project,
and the same cascade backs both the `review_decisions` action and the
`POST /review-requests/{id}/decision` Admin API endpoint:

- **accepted** — the review is accepted. The deliverable becomes accepted only
  when **all** of its review requests are accepted. The task becomes accepted
  only when every deliverable on that task is accepted (multi-review and
  multi-deliverable tasks are held back until the last one lands).
- **rejected** — the review and deliverable are rejected and the task is set to
  `blocked`.
- **blocked** — the review, deliverable, and task are all set to `blocked`.

Every decision stamps `decision_at` and triggers
`_recalculate_project_status`, which also runs after goal updates, task state
changes, and release changes. A project is `completed` when all goals are
accepted and no active task, deliverable, review, or release package is pending
or blocked; `blocked` if any of those is blocked; otherwise `active`.

### Task Graphs And Review Profiles

Team tasks can be split into tracked child tasks with `parent_task_id` and can
declare ordering constraints with `dependency_task_ids`. The add-on stores those
edges in canonical Team state, validates missing tasks, cross-project edges, and
cycles, and exposes derived task fields in API/query payloads:

- `child_task_ids`
- `dependency_status` (`satisfied`, `waiting`, or `blocked`)
- `blocked_by_task_ids`
- `subtask_counts`

The runtime wake loop uses this derived graph state: persistent agents are not
prompted to start tasks whose dependencies are still waiting or blocked, while
agents with open child tasks can still be woken to supervise or consolidate that
work.

Review profiles are reusable, team-owned review rubrics. A profile may be scoped
to one project, constrained to specific reviewer roles with `applicable_role_ids`,
and populated with criteria rows. Review requests can attach a profile and
instructions; review decisions can then record per-criterion outcomes. Archived
profiles remain visible for history but cannot be assigned to new review
requests through either the agent action protocol or Admin API.

### Deterministic Git Branch Naming

Branch names are derived deterministically so the repository, effect, and
workflow layers all compute the same name without external lookups. The helpers
live in `truman_team_addon.git_naming`:

- ephemeral agent branch: `agents/<session-id>/<assignment-id>`
- review branch: `team/<team-id>/reviews/<assignment-id>`
- feature branch: `team/<team-id>/features/<task-id>-<agent-id>`
- release branch: `team/<team-id>/releases/<release-id>`
- canonical release branch: `releases/<project-id>/<release-id>` (the default
  prefix `releases/<project-id>/` is overridable per project via
  `ProjectGitConfig.canonical_release_branch_prefix`).

`validate_branch_name` accepts only plain branch names: it rejects empty values,
any whitespace, control characters, `:`, a leading `/`, the substring `..`, and a
leading `refs/`. Every operator- or agent-supplied branch is validated before any
subprocess git call.

### Git Promotion And Release Publication

Git delivery flows up a fixed ladder — agent branch → review branch → feature
branch → release branch → canonical push — using `GitPromotionRecord` rows in
`TeamState.git_promotions` and `team.git.promotion_requested` events. The
repository only records intent; the packaged pack routes each event on its
`promotion_kind`:

- `review` — `git_review_promotions[]` supports two shapes. Ephemeral workers
  use `{"assignment_id", "deliverable_id"?}`; the actor must be the assignment's
  parent and the assignment must be completed or accepted with an `agent_branch`.
  Persistent agents use `{"deliverable_id", "source_branch"?, "commit_sha"?}`;
  the actor must be the producer, supervise the producer, or hold project-lead or
  manager authority over the deliverable's project, and the source may also come
  from the deliverable's `git_refs.agent_branch` / `git_refs.commit_sha`.
  Routes to `effect:team:publish-review-branch` then
  `effect:team:record-git-promotion`.
- `feature` — `git_feature_updates[]` (`{"task_id", "source_review_branches"[],
  "feature_branch"?}`): the actor owns the task or supervises its owner. Routes to
  `effect:team:consolidate-git-branches` then `effect:team:record-git-promotion`.
- `release` and `canonical_push` — `git_release_requests[]` (`{"id"?,
  "project_id", "deliverable_ids"[], "release_branch"?, "canonical_branch"?}`):
  only the project lead, the team manager, or an agent whose same-project role
  has `request_release` may request a release, and every deliverable must belong
  to the project, be accepted, and carry either a `git_refs.feature_branch` or
  non-Git `artifact_refs`. Deliverables that only have an agent branch or review
  branch are not release sources yet; first publish a review branch if needed,
  then run `git_feature_updates[]` so the deliverable records
  `git_refs.feature_branch`. This creates a pending `ReleasePackage` and, when
  feature branches are present, requires project Git config and creates a
  `release` promotion (consolidate feature branches → release branch) followed by
  a `canonical_push` promotion (release branch → canonical branch, finalized by
  `effect:team:mark-release-published`). Pure artifact releases do not require
  project Git config and do not create release or canonical branches.

Four add-on-owned effect providers complete these flows:
`effect:team:publish-review-branch` (publish a source branch or commit from the
project's bare repo into a durable `team/<team-id>/reviews/...` branch inside
the project's `host_repo_path`),
`effect:team:record-git-promotion` (mark a promotion succeeded/failed and attach
branches to `git_refs`), `effect:team:mark-release-published` (publish or block a
release package), and `effect:team:consolidate-git-branches` (deterministic merge
of multiple source branches strictly inside the project's `host_repo_path`,
aborting and failing on conflict — never auto-resolving). The core
`effect:git:push-branch` effect is reused unchanged for canonical publication.

**Publication boundary.** Internal branches (agent, review, feature, and release)
never leave the project's `host_repo_path`. The **only** branch pushed to the
canonical remote is the release branch, and only on the `canonical_push` route via
`effect:git:push-branch`. Deliverables that ship non-Git artifacts remain fully
supported through `artifact_refs`, require no push at all, and can be packaged
even when the project has no Git configuration. For Team-authored deliverables,
`artifact_paths` publish to immutable keys shaped as
`team/<team_id>/<project_id>/<deliverable_id>/<dispatch_id>/bundle` and are
indexed through artifact spaces; Team state stores only the resulting stable
refs.

### Team Cost Guard

Teams may carry an optional `max_cost_usd` estimated lifetime cap. The wake loop
checks Team cost payloads before leasing due agents; when the estimated total
reaches the cap, future Team wakes for that team are skipped until the cap is
raised or cleared. This is an operational safety rail, not billing-grade
enforcement: totals are derived from recorded model-usage events, the local
pricing catalog, and optional Claude transcript backfill. Cost payloads expose
`coverage_status` and `coverage_reasons`; operators should treat partial
coverage as a reason to inspect the underlying usage data before making spend
promises.

### Bundled Team Add-on Enablement

Use this checklist when enabling the bundled Team Add-on on a deployment:

1. Confirm the installed package includes `truman_team_addon` and its
   `ui_static` files.
2. Enable Admin Web routes/navigation in the host config or legacy gateway
   config:

   ```json
   {
     "addon_loader": {
       "modules": ["truman_team_addon"],
       "enabled_entry_points": false,
       "enabled_addon_ids": ["team-addon"]
     }
   }
   ```

3. Enable workflow providers in `config/trigger-orchestrator/config.json` with
   the same `addon_loader` block. Trigger and effect workers both read this
   worker config.
4. If autonomous team wakeups are desired, write the current-schema payload from
   `truman_team_addon.workflow_packs.team_runtime_v1()` into an operator-owned
   workflow pack file under `config/workflow-packs/` and list that file in
   `workflow_packs`. Do not edit checked-in example packs for live operator
   state.
5. Restart Admin Web and the trigger/effect orchestrator services.
6. Verify `/api/admin/state` lists `team-addon`, `/api/addons/team-addon/templates`
   returns the built-in catalog, `/addons/team-addon/` serves the add-on UI,
   and `/api/workflow-editor/catalog` plus workflow validation recognize the
   `effect:team:*` and `query:team:*` node types only after the add-on is enabled.
