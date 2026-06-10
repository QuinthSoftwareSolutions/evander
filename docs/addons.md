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
        },
    }
```

## Storage

Use the add-on storage helpers from `agent_sandbox.addons` so state remains
scoped and removable:

```python
from agent_sandbox.addons import addon_json_object_store

store = addon_json_object_store(runtime_dir, "team-addon", "state.json")
```

The helper rejects malformed add-on ids, absolute paths, and traversal segments.
Removing `runtime/addons/<addon_id>/` must not corrupt Truman core state.

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
disabled by default. Its canonical state lives under
`runtime/addons/team-addon/state.json`, while Truman session metadata remains
a non-canonical integration index.

When the Team Add-on is enabled, Admin Web shows an optional `Teams` tab. The
tab is served from the add-on's own `/addons/team-addon/` UI route and uses the
add-on API under `/api/addons/team-addon/`; it does not add team-specific views
or models to Truman core Admin Web pages.

When enabled, the Team Add-on also registers an optional runtime-loop provider
set:

- `effect:team:wake-due-agents` leases due persistent agents, prompts their
  Truman sessions, and mirrors a compact `metadata.team_addon` index into the
  session manifest.
- `query:team:agent-action-artifact` reads the structured JSON action artifact
  that a prompted persistent agent must write to `/artifacts/team-action.json`.
- `effect:team:apply-agent-action` validates the action, updates add-on state,
  routes questions/status reports/deliverable notices, and clears the runtime
  lease. Persistent actions may include `ephemeral_requests` to request bounded
  disposable workers and `ephemeral_acceptances` to promote or reject completed
  ephemeral results.
- `query:team:session-routing` classifies a completed Truman session as a
  persistent team agent, ephemeral assignment, or unknown session before action
  artifacts are applied.
- `effect:team:mark-ephemeral-spawned`, `effect:team:apply-ephemeral-action`,
  `effect:team:accept-ephemeral-deliverable`, and
  `effect:team:mark-ephemeral-cleaned-up` track the ephemeral worker lifecycle
  around normal Truman `effect:agent:create`, `effect:agent:prompt`, and
  `effect:agent:destroy` nodes.
- `query:team:state` returns filtered add-on state for workflows or UI.

These node types are add-on-owned. They are not present unless the add-on is
loaded, and they must not be moved into Truman core. The packaged
`truman_team_addon.workflow_packs.TEAM_RUNTIME_V1` definition shows the intended
cron plus agent-completion loop. Operators can import that definition into a
workflow pack when they want this opinionated runtime.

### Agent Action Protocol

A prompted persistent agent drives the team by writing a single structured JSON
action artifact to `/artifacts/team-action.json`. `effect:team:apply-agent-action`
validates that artifact against the add-on state, records per-item errors without
aborting the rest of the action, and returns the ids it produced. All of the
fields below are optional lists; an agent includes only the ones it needs. Each
applied item counts as progress and resets the agent's `no_progress_count`.

- `task_creations[]` — delegate work down or across the hierarchy. Shape:
  `{"id"?, "owner_agent_id", "project_id", "priority"?, "title", "description",
  "state"?, "parent_task_id"?, "dependency_task_ids"?}`. The actor may create a
  task for itself, for a direct subordinate, for any agent in a project it leads
  (`lead_agent_id`), or — as the team manager — for that project's direct lead.
  Targets outside the actor's team or outside the target project are rejected.
  Parent and dependency tasks must belong to the same project, parent cycles and
  dependency cycles are rejected, tasks default to `pending`, and tasks may only
  start in a non-terminal state. Created ids are returned in `created_task_ids`.
- `task_updates[]` — update owned task details or maintain dependency edges.
  Shape: `{"task_id", "state"?, "priority"?, "title"?, "description"?,
  "dependency_task_ids"?}`. State, priority, title, and description remain
  owner-only. Dependency edits may also be made by a supervising agent, the
  project lead, or the team manager, and use the same same-project and cycle
  validation as task creation. Updated ids are returned in `updated_task_ids`.
- `status_requests[]` — ask a direct subordinate for an update (downward).
  Shape: `{"recipient_agent_id", "task_id"?, "question"?}`. The recipient must be
  a direct subordinate; otherwise the item is rejected. A `STATUS_REQUEST` inbox
  item is created for the subordinate and surfaced through the normal pending-inbox
  wake. Returned in `created_status_request_ids`.
- `questions[]` and `status_reports[]` — talk upward only. Shape:
  `{"recipient_agent_id"?, "task_id"?, ...}`; the recipient defaults to the
  actor's `reports_to_agent_id` and must equal it. A top-level manager
  (`reports_to_agent_id` is `None`) receives a clear per-item error directing it
  to `operator_escalations` instead. These create `QUESTION` and
  `PROGRESS_REPORT` inbox items.
- `operator_escalations[]` — manager-to-operator escalation. Shape:
  `{"title", "message", "urgency"?, "project_id"?, "task_id"?}` (`urgency`
  defaults to `"normal"`). Only the team's top-level manager may escalate. If the
  team has no `operator_communication_target_id`, the item is recorded as a
  failed action and **no** event is emitted. On success an `ESCALATION` inbox
  item is written for audit and a `team.operator.escalation_requested` event is
  published with payload `{team_id, manager_agent_id, communication_target_id,
  title, message, urgency, project_id?, task_id?}`. Escalation ids are returned
  in `operator_escalation_event_ids`.
- `review_requests[]` — request a review of a deliverable; reviews are never
  auto-assigned. Shape: `{"id"?, "deliverable_id", "reviewer_agent_id",
  "review_profile_id"?, "instructions"?}`. The actor must be the deliverable's
  `producer_agent_id`, supervise the producer (direct or transitive), or hold
  project-lead/manager authority over the deliverable's project; the reviewer
  must be in the same team. When `review_profile_id` is provided, the profile
  must be active, in the same team, applicable to the deliverable's project, and
  applicable to the assigned reviewer's role when the profile lists roles.
  Returned in `created_review_request_ids`.
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

### Communication And Escalation Rules

Team communication is directional and enforced by the repository, not by prose:

- **Upward** — `questions` and `status_reports` may only target the actor's
  direct superior. There is no skip-level messaging.
- **Downward** — `status_requests` may only target a direct subordinate.
- **Manager to operator** — only the top-level manager escalates, and only when
  `Team.operator_communication_target_id` is set. The repository emits
  `team.operator.escalation_requested`; the packaged `TEAM_RUNTIME_V1` pack routes
  that event to an `effect:communication:send-message` node bound to the team's
  `operator_communication_target_id`. The repository and effect providers never
  call the communication sender directly — routing is the workflow pack's job.

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
to one project, constrained to specific reviewer roles with `applicable_roles`,
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

- `review` — `git_review_promotions[]` (`{"assignment_id", "deliverable_id"?}`):
  the actor must be the assignment's parent and the assignment must be completed
  or accepted with an `agent_branch`. Routes to `effect:git:promote-agent-branch`
  then `effect:team:record-git-promotion`.
- `feature` — `git_feature_updates[]` (`{"task_id", "source_review_branches"[],
  "feature_branch"?}`): the actor owns the task or supervises its owner. Routes to
  `effect:team:consolidate-git-branches` then `effect:team:record-git-promotion`.
- `release` and `canonical_push` — `git_release_requests[]` (`{"id"?,
  "project_id", "deliverable_ids"[], "release_branch"?, "canonical_branch"?}`):
  only the project lead or team manager may request a release, and every
  deliverable must belong to the project, be accepted, and carry either a
  `git_refs.feature_branch` or non-Git `artifact_refs`. This creates a pending
  `ReleasePackage` and, when feature branches are present, a `release` promotion
  (consolidate feature branches → release branch) followed by a `canonical_push`
  promotion (release branch → canonical branch, finalized by
  `effect:team:mark-release-published`).

Three add-on-owned effect providers complete these flows:
`effect:team:record-git-promotion` (mark a promotion succeeded/failed and attach
branches to `git_refs`), `effect:team:mark-release-published` (publish or block a
release package), and `effect:team:consolidate-git-branches` (deterministic merge
of multiple source branches strictly inside the project's `host_repo_path`,
aborting and failing on conflict — never auto-resolving). The single-branch core
effects `effect:git:promote-agent-branch` and `effect:git:push-branch` are reused
unchanged.

**Publication boundary.** Internal branches (agent, review, feature, and release)
never leave the project's `host_repo_path`. The **only** branch pushed to the
canonical remote is the release branch, and only on the `canonical_push` route via
`effect:git:push-branch`. Deliverables that ship non-Git artifacts remain fully
supported through `artifact_refs` and require no push at all.

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
4. If autonomous team wakeups are desired, materialize
   `truman_team_addon.workflow_packs.team_runtime_v1()` into an operator-owned
   workflow pack file under `config/workflow-packs/` and list that file in
   `workflow_packs`. Do not edit checked-in example packs for live operator
   state.
5. Restart Admin Web and the trigger/effect orchestrator services.
6. Verify `/api/admin/state` lists `team-addon`, `/addons/team-addon/` serves
   the add-on UI, and workflow validation recognizes the `effect:team:*` and
   `query:team:*` node types only after the add-on is enabled.
