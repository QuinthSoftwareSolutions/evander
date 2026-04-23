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
- Runtime-wide session access still mostly uses `JsonStore[SessionManifest]` directly outside the focused seams changed here.
- Some orchestration callers still rely on compatibility methods like `read_all()` while the semantic contracts are adopted incrementally.

## Remaining work before SQLite

- Replace direct `JsonStore` usage for sessions and other runtime state with the semantic repository contracts introduced here.
- Introduce typed stream/session key objects for PR feedback and inbox state so repository interfaces stop accepting ad hoc dict payloads.
- Separate orchestration write paths from read/query paths where execution history and operational reporting need different access patterns.
- Continue moving durable invariants into repositories only where they are genuinely persistence semantics rather than presentation/reporting logic.
