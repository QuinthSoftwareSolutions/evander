# Control-plane implementation boundaries

The public entry points below assemble smaller modules with explicit imports.
Keep domain changes in the implementing module and keep compatibility exports
in the original entry point when existing callers use them.

| Surface | Entry point | Implementation ownership |
| --- | --- | --- |
| Team Admin API | `evander_team_addon.api.admin_api_routes` | `api_routes` preserves route order; `api_routes_*` own endpoint groups; `api_context.TeamApiDependencies` supplies shared dependencies. Authorization, revisions, request parsing, and response projections have separate `api_*` modules. |
| Workflow editor | `frontend/admin-web/src/App.tsx` | `features/workflow-editor` contains typed components, hooks, action factories, and pure graph, field, validation, and run helpers. |
| Team workflow providers | `evander_team_addon.providers.register_providers` | `provider_*` modules own catalogs, effects, wake decisions, prompts, artifacts, queue operations, and Git preparation. |
| Team persistence | `evander_team_addon.sqlite_store.TeamStateSqliteStore` | `sqlite_*` modules own domain row adapters, schema definitions, ordered migrations, codecs, journals, and table mappings. |
| Integration services | `evander.services` | `integration_services.email`, `.discord`, `.teams`, and `.pull_requests` own their services; `.common` holds shared value types and helpers. |
| Team models and derived views | `evander_team_addon.models` | `model_*` modules own domain records and validation; `*_projections` construct read payloads. |
| Realtime voice media | `evander.didww_sip_bridge.DidwwSipBridgeService` | `didww_bridge_protocol` owns value records, codecs, and event parsing; `didww_realtime_media.RealtimeRtpSession` owns one call's output and timer lifecycle. |
| Workflow run details | `WorkflowReportsAdminService._read_workflow_run_detail` | `workflow_run_detail.WorkflowRunDetailBuilder` owns one report's correlation state and ordered assembly stages. |
| Communication MCP tools | `evander.gateway.communication_tools.register_communication_tools` | `communication_*_tools` build domain handlers; `communication_support` owns their dependency bundle and cursor codec. |
| Operator CLI | `evander.cli:app` | `cli_app` owns applications and help grouping; the domain `cli_*` modules register commands and own their helpers. |

## Contracts to preserve

- **Registration:** API routes and MCP tools retain their existing order, names,
  argument signatures, and descriptions. CLI commands retain their decorators,
  options, defaults, exit handling, and help ordering. Update the central
  registration list when adding an endpoint or tool.
- **Persistence:** the SQLite store owns connections, revision checks, locks,
  snapshot validation, and transactions. Row adapters use the supplied connection;
  schema migrations run in historical order. Derived read projections cannot be
  persisted as complete state.
- **Invocation state:** create a fresh run-detail builder for every read and a
  fresh realtime media session for every voice call. Their intermediate state
  must not become shared service state. Voice timing settings are captured by
  the bridge when starting a media session; the session owns cancellation and
  cleanup of its background tasks.
- **Imports:** legacy imports are explicit aliases of the real definitions.
  Patch dependencies in their implementing modules in tests. Shared services
  continue to be assembled through `build_admin_runtime`.
- **Editor state:** keep React hook ordering and effect cleanup explicit. Pass
  typed state and callbacks to action factories and components; keep pure graph
  and field transformations independent of React state.

For more detail, see [Team data ownership](team-data-boundaries.md),
[CLI module ownership](cli-module-boundaries.md), and the
[workflow editor module guide](../frontend/admin-web/src/features/workflow-editor/README.md).
