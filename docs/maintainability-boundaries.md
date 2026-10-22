# Control-plane implementation boundaries

The public entry points below assemble smaller modules with explicit imports.
Keep domain changes in the implementing module and keep compatibility exports
in the original entry point when existing callers use them.

| Surface | Entry point | Implementation ownership |
| --- | --- | --- |
| Team Admin API | `evander_team_addon.api.admin_api_routes` | `api_routes` preserves route order; `api_routes_*` own endpoint groups; `api_context.TeamApiDependencies` supplies shared dependencies. Authorization, revisions, request parsing, and response projections have separate `api_*` modules. |
| Workflow editor | `frontend/admin-web/src/App.tsx` | `features/workflow-editor` contains typed components, hooks, action factories, and pure graph, field, validation, and run helpers. |
| Team workflow providers | `evander_team_addon.providers.register_providers` | `provider_*` modules own catalogs, effects, wake decisions, prompts, artifacts, queue operations, and Git preparation. |
| Team wake dispatch | `provider_wake.TeamWakeDueAgentsEffectProvider.execute` | `_WakeDispatchBatch` owns state and output records for one invocation; a fresh `_WakeDispatch` prepares repositories, confirms MCP grants, imports review artifacts, submits prompts, activates delivered reservations, and records failure or conflict recovery for each lease. Global maintenance and planning retain their order in the provider. |
| Team Git context preparation | `provider_git_contexts._create_project_repo_git_work_contexts` | `_ProjectGitContextPreparation` owns project state, output rows, and retry counters; a fresh `_TaskGitContextPreparation` selects the task's base strategy, reserves preparation, prepares the worktree, records its receipt, revalidates provenance, and commits the context. Worktree preparation through context finalization stays under the cleanup lock. |
| Team persistence | `evander_team_addon.sqlite_store.TeamStateSqliteStore` | `sqlite_*` modules own domain row adapters, schema definitions, ordered migrations, codecs, journals, and table mappings. |
| Integration services | `evander.services` | `integration_services.email`, `.discord`, `.teams`, and `.pull_requests` own their services; `.common` holds shared value types and helpers. |
| Team models and derived views | `evander_team_addon.models` | `model_*` modules own domain records and validation; `*_projections` construct read payloads. |
| Scoped Team query state | `provider_projection._filtered_team_state_payload` | `_ScopedTeamStateProjection` owns one query's visibility sets and ordered collection projections. Task assessments use global state with scoped references; lineage reference closure runs after all projections that rebuild records. |
| Team case-history evidence | `services.case_context_service.collect_evidence` | `_CaseEvidenceCollection` selects visible roots before collecting associated evidence, then collects sources, notes, artifacts, reviews, releases, and linked conversation anchors in order. Deduplication and candidate-time annotations finish the collection before view rendering. |
| Realtime voice media | `evander.didww_sip_bridge.DidwwSipBridgeService` | `didww_bridge_protocol` owns value records, codecs, and event parsing; `didww_realtime_media.RealtimeRtpSession` owns one call's output and timer lifecycle. |
| Workflow bindings | `workflow.binding.resolve_workflow_bindings` | A fresh `_WorkflowBindingResolver` owns recursive value and key traversal, reference selection, fallbacks, and trace collection for one configuration. Existing aliases and strict/non-strict behavior remain on the public function. |
| Active workflow snapshots | `WorkflowReportsAdminService._read_workflow_run_snapshot` | A fresh `workflow_run_snapshot.WorkflowRunSnapshotBuilder` reads active stores, registers queue/effect/gate/await and recent activity in order, and projects the snapshot. The facade retains store resolution, its patchable clock, and the pending-payload loader. |
| Workflow graph activity | `WorkflowReportsAdminService._collect_workflow_graph_activity_report` | A fresh `workflow_graph_activity.WorkflowGraphActivityReportBuilder` loads graph metadata, aggregates activity through the existing bounded SQLite read, and projects graph/node totals. The facade supplies the existing connection/schema seams and captures the report clock. |
| Workflow run details | `WorkflowReportsAdminService._read_workflow_run_detail` | `workflow_run_detail.WorkflowRunDetailBuilder` owns one report's correlation state and ordered assembly stages. |
| Workflow run search | `WorkflowReportsAdminService._read_workflow_run_search` | A fresh `workflow_run_search.WorkflowRunSearchBuilder` selects nodes, reads the SQLite event slice and pending agent work, groups activity, enriches sessions from approvals and artifacts, and indexes shared traces before filtering, classifying, sorting, and limiting runs. The service keeps input normalization, SQLite selection and hydration, and the patchable report clock. |
| Workflow failure clusters | `WorkflowReportsAdminService._read_workflow_failure_clusters_report` | A fresh `workflow_failure_clusters.WorkflowFailureClusterReportBuilder` loads graph metadata, reads and normalizes activity in one SQLite transaction, groups failures, classifies cluster state, then sorts and limits the response. The facade retains period normalization and supplies the operational-payload loader used by reporting read-budget checks. |
| Generic queued prompt replay | `effects.providers.lifecycle_hooks.AgentEnqueuePromptQueueDispatchHook` | A fresh `_QueuedPromptDispatch` selects the queue page, validates graph membership, replays containers, and loads VM inventory before VM replay. A fresh `_LibvirtQueuedPromptReplay` rechecks each current record, prepares its session, claims prompt and capacity leases, claims replay ownership, submits the prompt, and records delivery. Slice progress remains on the hook; failed receipt persistence retains claims and the queued row. |
| Communication MCP tools | `evander.gateway.communication_tools.register_communication_tools` | `communication_*_tools` build domain handlers; `communication_support` owns their dependency bundle and cursor codec. |
| Operator CLI | `evander.cli:app` | `cli_app` owns applications and help grouping; the domain `cli_*` modules register commands and own their helpers. |
| Team agent leasing | `TeamRuntimeService.lease_due_agents` | `services.agent_leasing._LeasePlanning` reconciles state and role allocations before manager fallback. `_OrdinaryLeaseCandidate` owns one agent's ordinary lease phases. `_ManagerFallbackCandidate` selects and reserves one team's manager work; a fresh `_ManagerFallbackLineageCandidate` keeps exclusions and full work fingerprints local to each lineage evaluation, returning named `_ManagerFallbackWork` fields. Authority, scope, reservation, runtime updates, and payload construction retain their order. The service keeps the public signature and patchable clock seam. |
| Team agent actions | `TeamAgentActionService.apply_agent_action` | `services.agent_action_application.AgentActionApplication` owns the repository transaction and response projection; `services.agent_action_batch.AgentActionBatch` owns validation, ordered action stages, and dispatch completion with state local to one invocation. The service remains the repository-backed compatibility facade. |
| Team administrative agent edits | `TeamAdminService.update_agent` | `services.agent_update_application.AgentUpdateRequest` preserves normalized inputs and explicit edit flags; a fresh `AgentUpdateApplication` applies team, project, role, session, profile, context-policy, reporting, and wake stages in order within one transaction. The facade keeps normalization, revision checks, its patchable clock, and transactional outbox. |
| Team configuration copying | `TeamAdminService.copy_team` | `services.team_copy_application.TeamCopyRequest` preserves normalized copy inputs; a fresh `TeamCopyApplication` selects the clean source graph, validates destination sessions, allocates IDs, and applies ordered record-copy stages. `domain.team_copy` owns source selection and its configuration token. The facade retains the captured source revision, its patchable clock, and managed repository binding locks and rollback. |
| Team template materialization | `TeamAdminService.materialize_template` | `services.template_materialization_application.TemplateMaterializationRequest` normalizes template inputs before reading state; a fresh `TemplateMaterializationApplication` allocates IDs and creates roles, agents, teams, projects, outcome kinds, review profiles, tasks, and work lineages in one transaction. The facade retains revision checks, its patchable clock, managed Git resolution, and binding rollback. Both graph applications use the shared `domain.team_cost_budget` helpers. |
| Team release intake | `services.domain.release_packages._build_release_package` | `_ReleasePackageBuilder` owns one request's lineage resolution, validation, duplicate reuse, destination selection, package persistence, and promotion queueing in their established order. Agent and operator requests retain the shared function signature. |
| Team release verification | `TeamReleaseVerificationRunner.run` | The runner validates request identity before single-flight coordination. A fresh `_ReleaseVerificationExecution` loads frozen inputs, reconciles persisted command results, prepares a workspace generation, executes commands, cleans up partial or complete setup, and persists the terminal result. A fresh `_ReleaseVerificationCommandExecution` records worker progress, classifies execution, evaluates evidence, retains logs, and records policy outcomes. Generation checks, cancellation checks, and infrastructure precedence remain in their established stages. |
| Team task administration | `TeamAdminService` task methods | `services.task_administration.TeamTaskAdministration` owns task creation, editing, cancellation, lifecycle migration, and recovery. `_TaskUpsertRequest` and `_TaskEditRequest` preserve explicit edits; fresh `_TaskUpsertApplication` and `_AdministrativeTaskEdit` instances apply their ordered validation, mutation, preparation, blocker, and wake stages within one transaction. The public admin service retains its constructor and method surface. |
| Team blocker commands | `TeamBlockerCommandService.update_blocker_command` | A fresh `_BlockerUpdateApplication` validates authority, applies progress/delegation/resolution/escalation, and constructs receipts inside the existing command transaction. Normalization, idempotency, and dispatch requirements stay in the command facade. |
| Team task cancellation | `TeamTaskUpdateCommandService.cancel_task_command` | A fresh `_TaskCancellationApplication` validates cancellation and successors, plans dependent replacement, cancels/rebinds work, and reconciles recovery in order inside the existing command transaction. |
| Team dispatch finalization | `TeamRuntimeService.finalize_agent_dispatch_from_activity` | A fresh `_DispatchActivityFinalization` validates the dispatch, reads scoped activity, observes capabilities, finalizes chat and work, updates progress, and queues follow-up work inside the existing command transaction. The facade retains prepared-finalization maintenance and input normalization. |
| Team task update commands | `TeamTaskUpdateCommandService.update_task_command` | `commands.task_update_execution.execute_task_update_command` owns normalization and executor integration; `_TaskUpdateApplication` owns validation, edits, preparation invalidation, and response projection within one command transaction. The command service delegates through its existing signature. |
| Team MCP registration | `evander_team_addon.mcp.register_mcp_tools` | Named collaboration, work-management, delivery-review, Git-release, and ephemeral-worker registration functions preserve the existing tool order and definitions. |
| Worker Git workspaces | `evander.worker.service.WorkerDaemon` | `worker.git_workspace.GitWorkspaceOperations` retains lock ownership, cleanup, hardened subprocess helpers, and existing signatures. Fresh `_ReferenceRepoPreparation`, `_TaskWorktreePreparation`, and `_TaskDependencyComposition` applications own ordered checkout, validation, replay, composition, recovery, and receipt stages. The service module re-exports established exception and helper imports. |
| Team standalone UI | `ui_static/team-ui.js` | The entry point composes feature controllers. `team-ui-app.js` owns navigation, aggregate state, scoped loads, and the state stream. Chat, task board, projects, releases, human reviews, and other controllers own feature state, rendering, and actions. `team-ui-events.js` retains delegated listener order and stops after a handler consumes an event. `team-ui-state.js` retains initial contracts and deep links; `ui.py` explicitly publishes every module. |
| Session policy UI | `features/sessions/SessionsScreen.tsx` | `sessionApprovalSettings.ts` owns typed approval policy normalization; `SessionPolicyModals.tsx` owns renewal and approval-settings dialogs. The screen retains data loading and mutation orchestration. |
| Libvirt VM creation | `vm.provider._libvirt_create_vm` | `_LibvirtVmCreationRequest` carries the existing options; a fresh `_LibvirtVmCreation` validates the request, builds or reuses a session, applies metadata, validates the model, persists the manifest, prepares setup, and handles recovery. Helper and subprocess execution return `_VmSetupResult` to one exit-status check. Setup failures delete only newly created sessions when no VM survives. |
| Realtime voice origin queries | `DidwwSipBridgeService` | `didww_origin_queries.DidwwOriginQueryHandling` owns delegated origin-query calls, polling, acknowledgement, timeout, and terminal response handling. |
| Model relay HTTP forwarding | `OpenAIRelay.create_app` | `_proxy_http_request` resolves session policy before provider routing; `_proxy_azure_openai_http` and `_proxy_openai_compatible_http` prepare provider requests. `_forward_openai_http` owns shared streaming selection, transport, steering delivery/release, usage accounting, and optional local-tool response adaptation. Module-local transport and thread-offload seams stay intact. |
| OpenAI usage accounting | `OpenAIRelay` | `relays.openai_usage.OpenAIUsageAccounting` owns usage extraction, stream aggregation, and provider request correlation. The relay retains transport and policy orchestration. |
| Teams credential migration | `credentials.migration.migrate_teams` | A fresh `_TeamsCredentialMigration` inspects identity bindings and legacy sources before mutation. Input revalidation, all credential imports, the encrypted recovery snapshot, reference switches, and source retirement remain in that order under one configuration mutation lock; partial switches reuse durable migration markers. |

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
- **Compatibility facades:** existing service constructors, public and externally
  imported method signatures, exception imports, clock seams, and tool registration
  order remain stable. Capability bases carry no independent mutable state.
- **Editor state:** keep React hook ordering and effect cleanup explicit. Pass
  typed state and callbacks to action factories and components; keep pure graph
  and field transformations independent of React state.

For more detail, see [Team data ownership](team-data-boundaries.md),
[CLI module ownership](cli-module-boundaries.md), and the
[workflow editor module guide](../frontend/admin-web/src/features/workflow-editor/README.md).

## Team standalone UI

The entry point builds the controller graph before registering DOM listeners.
Controller constructors initialize local state; peer calls run after composition.
Keep cross-feature calls on the named controller interfaces and use owner methods
for updates. Request counters, refresh timers, streams, selections, and drafts
belong to the controller that manages their lifecycle.

- `team-ui-app.js` coordinates shared state, view loading, and rendering dispatch.
- `team-ui-chat.js`, `team-ui-voice.js`, and `team-ui-costs.js` own their asynchronous
  lifecycles, request fencing, cleanup, and presentation.
- `team-ui-board.js`, `team-ui-cases.js`, `team-ui-planning.js`, and `team-ui-tasks.js`
  own task navigation, readiness, work hierarchy, and recovery controls.
- `team-ui-people.js`, `team-ui-projects.js`, `team-ui-org.js`, and
  `team-ui-governance.js` own record selection, focused views, and their editors.
- `team-ui-delivery.js`, `team-ui-reviews.js`, `team-ui-releases.js`,
  `team-ui-evidence.js`, and `team-ui-human-reviews.js` own delivery and review views.
- Form creation, record dialogs, policy editors, drafts, revisions, and privilege
  checks have separate `forms`, `editor`, `policy`, `drafts`, `mutations`, and
  `access` controllers. Shared `config`, `dom`, and `format` modules contain options,
  element references, and formatting helpers.

When splitting delegated event handlers, preserve listener order and return
`true` when a branch consumes an event, including when a privilege guard rejects
it. Return `false` to let the next handler inspect the event. Preserve modal
containment barriers, request generations, conflict metadata, and form drafts.

Add new browser imports to `ui.py`'s explicit asset allowlist. The API publication
test walks the real import graph; browser regressions exercise the composed UI.
Controller tests import the actual modules through `team_ui_controller_harness`
instead of copying source snippets from the entry point.
