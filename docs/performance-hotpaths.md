# Control-plane performance paths

The 0.59.2 optimizations address the thirteen findings from the September 2026
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

Final validation on 2026-09-23 after merging main: all 8,510 Python tests passed. Focused Workers
browser/configuration checks, TypeScript checking, and Ruff on changed Python
files also passed. The broad frontend suite exceeded its 180-second sandbox
limit; its failing/stalled navigation and tool-visibility files passed individually
outside the sandbox. The complete frontend suite is not claimed verified.

Synthetic local probes from the initial performance implementation compared the
original `81544e61` code with the optimizations:

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

## Request storage concurrency (0.64.1)

MCP transport authentication, binding persistence, tool discovery, invocation policy and
approval consumption, and fallback dispatch auditing now await storage work in Starlette's
worker pool. Synchronous tool bodies already used that pool. Request context is propagated
to the workers; approval-context tokens are installed and reset by the owning async task.
Checks and approval consumption finish before a tool starts, and audit writes finish before
its result is returned. Transport ownership is checked again after authentication yields.

Admin Web authentication middleware and password login also await worker execution. Session,
bootstrap and asset handlers use synchronous Starlette endpoints, which Starlette
dispatches through the worker pool. Cached release/status handlers await their response
builders in that pool. Azure sign-in offloads its blocking signing-key fetch
and user persistence. These paths retain their existing error and permission behavior.

The shared AnyIO pool keeps its default limit of 40 active worker operations per process.
Requests wait for capacity; this change does not increase SQLite writer concurrency or
introduce fire-and-forget persistence. Monitor worker queue delay as well as event-loop
delay when sizing a deployment. CLI and synchronous orchestration workers retain their
existing repository interfaces and transactions.

Session lookups reuse one validated manifest snapshot while the file's device, inode,
size, nanosecond mtime and ctime match. A changed revision discards the previous snapshot;
failed reads are not cached. Snapshots above 16 MiB of encoded input or 10,000 sessions
are not retained. Every returned model is detached from the cache. External replacements,
in-place edits, deletion and permission revocation are observed on the next lookup, and
session expiry is still evaluated by policy on every invocation. Cold reads still parse
and validate the complete collection.

Run the reproducible synthetic workload against the desired checkout's source:

```bash
PYTHONPATH="$PWD/src" timeout 600s ./.venv/bin/python scripts/benchmark_request_storage.py \
  --concurrency 10 50 100 --history 1000 --rounds 3 --repeat 3 \
  --output /tmp/evander-request-storage.json
PYTHONPATH="$PWD/src" timeout 60s ./.venv/bin/python scripts/benchmark_request_storage.py \
  --lock-probe --repeat 3 --output /tmp/evander-storage-lock-wait.json
```

For a before/after comparison, run this same script with `PYTHONPATH` pointing first to
the baseline checkout's `src`, then to the changed checkout's `src`. Use the same interpreter
and arguments, and do not run tests or another benchmark concurrently. Each sample uses
fresh temporary state, 1,000 synthetic stored sessions and a database-backed operator
account. Each active session performs tool discovery, two audited approval lookups, three
Admin session polls, three workflow-run polls and three durable workflow publications and
completion updates. MCP initialization is outside the timed interval. All responses and
workflow updates must succeed for a sample to be reported.

The report includes request percentiles, event-loop scheduling delay, worker queue and
execution delay, peak active/pending workers, SQLite statement times and busy errors.
Statement time includes execution and any lock wait; it is not a pure lock-wait measure.
The separate lock probe holds the Admin SQLite writer for 200 ms after login attempts
`BEGIN IMMEDIATE`, measuring the actual writer wait and its effect on the event loop.
This is an in-process ASGI stress workload with real temporary JSON and SQLite storage;
it excludes network transport, model inference, guest execution and production data.

Regression coverage lives in `test_request_storage_concurrency.py` and
`test_session_manifest_repository.py`, alongside the existing gateway, Admin Web,
workflow and relay suites. Synchronization tests hold storage operations open while
unrelated requests complete, and check connection ownership, single-use approvals,
per-call identity/audit isolation and cache freshness.

### Measured comparison

On 2026-09-26, the same workload completed three times at each concurrency level
against baseline commit `2faa1686` and the 0.64.1 implementation. Both sides completed
all 18 workloads combined: 8,640 HTTP requests and 2,880 published-and-delivered
workflow events, with no SQLite busy errors. Runs were sequential, without concurrent
tests or other benchmark processes. Python 3.12.13, SQLite 3.50.4, Starlette 1.0.0,
AnyIO 4.13.0 and MCP 1.27.0 were shared by both revisions.

Each cell below is the median of three runs; event-loop figures are the median of
the three per-run p95 values, rather than a pooled percentile.

| Concurrent clients | Workload before | Workload after | Event-loop p95 before | Event-loop p95 after |
| --- | ---: | ---: | ---: | ---: |
| 10 | 9.574 s | 0.752 s | 553.313 ms | 1.228 ms |
| 50 | 45.401 s | 5.375 s | 1,993.695 ms | 1.487 ms |
| 100 | 102.923 s | 13.177 s | 2,117.582 ms | 1.881 ms |

The controlled contention probe still waited approximately 229 ms for the SQLite
writer on both revisions. Its median worst event-loop delay fell from 261.148 ms
to 0.800 ms. This demonstrates isolation of a database wait, rather than increased
SQLite write concurrency. The mixed workload retained the 40-worker ceiling.

[Raw measurements and environment](benchmarks/request-storage-2026-09-26.json)
contain all repeats, per-route latency, queue/execution delay and SQLite timings.
These results establish improvement for this synthetic workload, not production
capacity or a universal latency guarantee. Large uncached manifest files, slow
queries and sustained writer contention still require separate measurement.

The final complete Python test run passed all 8,541 tests: 8,533 in the standard
phase and eight in the isolated performance phase. Ruff passed for every changed
Python file, and `git diff --check` passed.
