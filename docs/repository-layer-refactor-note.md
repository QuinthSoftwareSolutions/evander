# Repository Layer Refactor Note

## Reshaped repositories

- `session_manifest_repository.py` now exposes session lifecycle operations (`get_session`, `list_sessions`, `list_active_sessions`, `upsert_session`, `delete_session`) instead of being read-only.
- `orchestration_repositories.py` now exposes semantic operations for:
  - recording trigger poll state
  - recording effect processing checkpoints
  - recording executions
  - looking up executions by idempotency key
  - finding the latest gate execution for a gate
- `wake_store.py` now owns due-wake discovery through `find_due_requests(...)` instead of leaving that logic in the heartbeat worker.
- `pr_feedback_worker.py` now exposes semantic feedback listing and session-read advancement operations used by `PrFeedbackService`.
- `review_request_store.py` keeps the existing workflow model and now exposes `claim_review_requests(...)` as the semantic leasing entrypoint, with the older name retained as a transitional alias.

## Still transitional

- `records_repository.py` is still mostly a thin JSON/document wrapper.
- `cursor_repositories.py` still contains storage-shaped wrappers for email, Discord, and Teams cursor state.
- Some admin service and VM lifecycle flows still reach into `runtime.sessions` directly, especially where bulk pruning still depends on `write_all(...)`-style compatibility behavior.
- Some orchestration and effect-provider callers still rely on compatibility methods like `read_all()` / `put()` while the semantic contracts are adopted incrementally.

## Adoption progress in this pass

- Session-heavy operational callers now use `SessionManifestRepository` semantics on important paths:
  - CLI session lifecycle commands (`create-session`, `prune-sessions`, `list-sessions`, `renew-session`, `revoke-session`, `delete-session`)
  - CLI session mutation commands (`set-session-model`, `add-tool-permission`, `revoke-tool-permission`, `attach-pr-feedback`)
  - CLI issue-state pruning now deletes session manifests through repository lifecycle methods instead of rewriting raw session payloads
  - `SessionAdminFacade`, `WorkerAdminFacade`, `DiscordIngestWorker`, `TeamsIngestWorker`, `IssueOrchestratorWorker`, and provider-runtime session lookup now read through the semantic repository
- Session-aware infrastructure/config seams now also use repository semantics:
  - `ProviderConfigResolver`, `PolicyConfigResolver`, `VmDefaultsConfigResolver`, `PolicyEngine`, `DirectoryOwnershipQueries`, and `WorkerDirectoryContextResolver`
  - runtime helper paths for allowed Discord/Teams routing now consume session repository semantics rather than raw store iteration
- `ReviewRequestService` now depends on `SessionManifestRepository` instead of a raw `JsonStore[SessionManifest]` when resolving session-backed directory context.
- `AgentPromptNewTriggerProvider` now discovers scoped sessions through `JsonSessionManifestRepository` instead of opening a raw `JsonStore`.
- Session-heavy VM and issue admin flows now use repository semantics for high-value call sites:
  - `VmAgentAdminService` now resolves target sessions/VMs and performs session create/update/delete through repository semantics instead of reading and rewriting raw session maps
  - `IssueGitAdminService` now resolves and updates session state through the session repository
  - `ado_issue_worker.py` now creates missing issue sessions through the repository rather than direct `runtime.sessions` access
- `session_manifest_repository.py` now owns a small amount of operational lookup behavior (`list_sessions_for_vm`, `list_sessions_for_issue`, `resolve_session_target`, `resolve_vm_name_from_target`) so callers stop re-implementing session selection against raw payload maps.

## Remaining work before SQLite

- Replace direct `JsonStore` usage for sessions and other runtime state with the semantic repository contracts introduced here.
- Migrate the remaining VM/admin service session mutation paths that still bulk-edit manifests through raw store operations.
  The biggest remaining seams are selected `admin_service.py` helpers plus any gateway-facing contexts that still expose `runtime.sessions` directly for compatibility.
- Introduce typed stream/session key objects for PR feedback and inbox state so repository interfaces stop accepting ad hoc dict payloads.
- Separate orchestration write paths from read/query paths where execution history and operational reporting need different access patterns.
- Continue moving durable invariants into repositories only where they are genuinely persistence semantics rather than presentation/reporting logic.
