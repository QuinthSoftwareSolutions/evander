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
