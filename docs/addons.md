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

A hierarchical agent-team module should live as an external add-on. Team,
manager, project lead, task, deliverable, review, and release-package schemas
belong in that package. Only generic hooks that are useful to other add-ons
should be proposed for Truman core.

The bundled `truman_team_addon` package provides the initial state-model layer
for this pattern. It is still disabled by default; enable it with
`addon_loader.modules: ["truman_team_addon"]` or by enabling the
`agent_sandbox.addons` entry-point group. Its canonical state lives under
`runtime/addons/team-addon/state.json`, while Truman session metadata remains a
non-canonical integration index.
