# Control-plane performance paths

The 0.58.2 optimizations address the thirteen findings from the September 2026
performance audit. They preserve the existing API payloads, authorization filters,
command atomicity, dispatch ownership, and durable cursor/checkpoint behavior.

| Finding | Change | Behavior covered by regression tests |
| --- | --- | --- |
| 1. Task-page enrichment | Slice task/project pages before enrichment and share snapshot assessments. | Pagination totals, cursors, visible records, task payloads and role readiness. |
| 2. Repeated task assessment | Index deliverables by task, share project assessments, defer assessment for empty wake candidates. | Superseded contributions, dependencies, blocker inheritance and integration readiness. |
| 3. Team writer contention | Prepare coherent baselines before reserving the SQLite writer; recheck revision before callbacks; renew leases through a narrow transaction. | Callbacks execute once, stale snapshots refresh, command writes roll back atomically, replaced/terminal dispatches cannot renew. |
| 4. Relay event-loop blocking | Await thread offload for session resolution, steering and usage persistence. | Concurrent requests remain responsive; disconnected streams record usage and close upstream resources. |
| 5. Worker polling | Batch systemctl inventory, coalesce short-lived snapshots, offload blocking work and prevent overlapping browser refreshes. | Worker fields, selected-worker logs and refresh after operator actions. |
| 6. Chat history polling | Cache bounded transcript pages by file revision and move reads, writes and stream encoding off the event loop. | Pagination, permissions, write/external-file invalidation, detached return values and SSE snapshots. |
| 7. Worker logs | Incremental transcript projections, reverse tail reads and linear overlap detection. | Completion/failure reporting, active tools, time windows, partial records, replacement, truncation and multi-file order. |
| 8. Session lifecycle reads | Batch lifecycle-only execution fields, retain compact evaluator inputs and look up a requested session directly. | Historical cleanup precedence, active status, timestamps, expiry and external database changes. |
| 9. VM completion polling | Pass the known VM target through inventory/provider layers. | Exact dispatch matching and idempotent completion publication; unrelated VMs are not probed. |
| 10. Discord trigger history | Apply channel/author/cursor predicates in SQL and fetch bounded pages. | Retry before acknowledgement, equal-timestamp groups larger than a page, late arrivals at the cursor and filtered history. |
| 11. Semantic index passes | Fetch bounded pages using a durable hash/path continuation. | Cached vectors, duplicate hashes/paths, old numeric checkpoints, cancellation and failed vector writes. |
| 12. Index retention | Queue deleted snapshots' candidate hashes and reclaim bounded batches through indexes. | Shared content, protected snapshots, worker cleanup and old-schema migration. |
| 13. Admin response cache | Evict expired/LRU entries under entry/byte budgets and retain serialized bodies. | ETags, conditional requests, coalesced reads, cancellation and cache isolation. |

## Operational details

Team mutations still perform full candidate validation where cross-record invariants
require it. Only baseline reads can repeat when another process changes the revision;
command callbacks remain inside their original atomic transaction. Maintenance lease
renewal validates the exact active dispatch/runtime pair and updates only its timestamps
and the state revision. It does not hydrate unrelated historical collections.

Chat remains in its existing JSON format. The cache uses file identity, size, and
nanosecond modification/change times, clears on writes and external changes, and retains
at most 64 pages with an 8 MiB encoded-page budget. A cold read or a new file revision
still parses the file. Session summaries cache only compact lifecycle inputs; expiry is
reevaluated on every request.

Worker transcript caches retain derived state, not raw events, with bounded entry and
estimated-memory budgets. They assume append-only transcript writers. Replacement,
truncation, same-size rewrites and changed earlier files invalidate the projection;
prefix/boundary checks detect common truncate-and-regrow cases. Edits to old records
should replace the transcript file. An unterminated final record is provisional until
its newline arrives. Cold reads still need to process the transcript once.

Repository indexing upgrades its SQLite schema from version 1 to version 2 on first
access, adding hash indexes, an FTS row mapping and a durable garbage-collection queue.
That one-time migration scans existing search rows/orphan candidates. Normal maintenance
then bounds snapshot deletion and content reclamation per pass, so a large backlog may
need multiple passes. Existing protection and worker-release checks run before deletion.
The previous binary accepts only schema version 1; reverting after first access requires
restoring the pre-upgrade index database or an explicit compatible schema downgrade.
Older numeric embedding checkpoints replay the last hash group before continuing with
hash/path keys; vector point upserts remain idempotent.

## Validation

The focused regression modules are `test_team_performance_hotpaths.py`,
`test_openai_relay_thread_offload.py`, `test_worker_log_projection.py`,
`test_admin_performance_hotpaths.py`, and `test_workflow_performance_paths.py`.
Run them alongside the existing domain suites. Performance regressions are primarily
checked through bounded reads, constructor/query counts and synchronization barriers,
so normal test runs do not depend on machine-specific timing thresholds.

Final validation on 2026-09-22: all 8,408 Python tests passed. Focused Workers
browser/configuration checks, TypeScript checking, and Ruff on changed Python
files also passed. The broad frontend suite exceeded its 180-second sandbox
limit; its failing/stalled navigation and tool-visibility files passed individually
outside the sandbox. The complete frontend suite is not claimed verified.

Synthetic local probes compared the original `81544e61` code with these changes:

| Workload | Before | After |
| --- | ---: | ---: |
| Task list, 1,000 tasks, page limit 1 | 2,378 ms | 2.23 ms |
| Assess 4,000 tasks and deliverables | 2,453 ms | 155 ms |
| 40 wake checks for agents without tasks | 853 ms | 3.7 ms |
| Team mutation writer occupancy with 10,000 historical actions | 313 ms | 111 ms |
| Repeated chat page read from 40,000 messages | 79 ms | 0.13 ms |
| Repeated worker active-tool read from 50,000 events | 276 ms | 0.13 ms |
| Merge a 500 KB tail with no overlap | 1,860 ms | 43 ms |

These temporary-data probes demonstrate the affected scaling paths, not production
latency or throughput. Repeated chat/transcript figures include warm cache reads;
their first read remains proportional to history size. Full Team mutations still
validate the complete candidate, while narrow maintenance lease renewals avoid
historical reads and measured approximately 1.7–2.0 ms in the same fixture.

When using the shared editable virtual environment from an isolated worktree, set
`PYTHONPATH` to that worktree's `src`. Some sandbox environments deadlock Starlette
TestClient/threadpool tests; a bounded rerun of the identical test outside that sandbox
can distinguish the environment issue from product behavior.
