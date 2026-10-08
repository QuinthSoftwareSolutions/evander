# Control-plane performance paths

## Further repeated-work fixes (0.86.3)

Ten additional findings were fixed against main `e300a4c4` on 2026-10-08.
Each change removes a specific repeated read, query or graph construction while
retaining the existing visibility, ordering, pagination and durable state behavior.

| Finding | Previous work | Fix and regression boundary |
| --- | --- | --- |
| 1. Discord message pages | Decode the entire allowed channel history before applying unread cursors and page limits. | Apply cursor predicates in SQLite and decode only enough valid rows to fill the requested page. Keep chronological/message-ID order, corrupt-record skipping, timestamp ties, timezone normalization and cursor advancement. |
| 2. Discord unread checks | Query the session cursor and message existence separately for every channel. | Load requested channel cursors together, then probe indexed existence in one statement. Keep channel/session isolation and empty-channel behavior. |
| 3. Cost-report identity lookup | Execute a separate event-ID query for every supplied session or dispatch ID, including duplicates. | Deduplicate identifiers and query indexed batches of 400; preserve the exact event-ID set, empty inputs and rejected field names. |
| 4. Team memory visibility | Rebuild team/project/reporting visibility for every memory record or semantic candidate. | Resolve visibility lazily once per read. Canonical records still determine permissions; assignment changes are observed by the next read. |
| 5. Team chat/voice assessments | Construct a complete task-assessment graph for each task and each project in one context. | Share one lazy assessment service within a context, keeping its task/project payloads and building fresh assessments for subsequent contexts. Idle contexts avoid graph construction. |
| 6. Team attention links | Sort and scan all deliverables separately for every blocked or waiting task. | Build one lazy task-to-smallest-deliverable-ID index for the projection. Keep the selected links and observe changed deliverables on the next projection. |
| 7. Workflow graph metadata | Rescan and normalize every saved graph for every scoped connected component. | Normalize each graph once and index its component overlaps. Keep first-error ordering, exact component requirements and the last matching metadata record. |
| 8. Repository symbol navigation | Execute one relationship query per expanded symbol. | Expand each breadth layer with indexed queries in one statement; keep directed definitions, symmetric references, snapshot isolation, stable result pages and the 2,000-symbol limit. |
| 9. Repository build reuse | Read and sort all historical snapshots for an identity before selecting the first reusable row. | Select eligible statuses and one newest row in SQLite using partial identity/time indexes. Keep rebuild eligibility, timestamp tie order and unsupported-status skipping. |
| 10. Approval audit review | Load and split the entire audit file to find the latest eight related entries. | Read backward in 64 KiB blocks and stop when the context is complete. Keep chronological context, request/digest/session matching, redaction, long Unicode records and empty context on encountered read/encoding errors. |

The focused regressions are `test_messaging_usage_performance_paths.py`,
`test_team_additional_performance_paths.py`, `test_workflow_index_performance.py`,
and `test_approval_review_performance_paths.py`. Work counts are checked together
with observable payloads and state; the tests do not depend on timing thresholds.

Repository-index state databases upgrade from internal schema 1 or 2 to schema 3
on first access. The new migration adds two partial indexes and preserves existing
data; its version recheck and DDL share a writer transaction. Existing schema-1
content maintenance migration still runs when required. Normal schema-3 reads do
not repeat DDL. Portable exports retain format version 1. Index creation has a
one-time cost proportional to snapshot history. Previous binaries reject internal
schema 3, so binary rollback requires restoring the pre-upgrade database or an
operator-controlled schema-version downgrade while index workers are stopped.

Memory visibility, task assessments and attention indexes live only for one read
or context. They do not cache permissions or task state across mutations. Negative
Discord page limits retain their previous Python slicing behavior and read all
matching valid records. Corrupt records can require scanning beyond the requested
page size. Approval context may still scan the whole file if related entries are
sparse; its retained data is limited to file blocks, an individual line and the
requested context. It does not inspect unrelated older bytes once the context is
complete. Consequently, an encoding error before a complete valid context no
longer discards that context; corruption encountered while searching still returns
an empty context. Multi-channel Discord ordering can still scan/sort matching
database rows even though payload decoding and positive SQL page sizes are bounded.

Controlled synthetic comparisons used Python 3.12.13 and SQLite 3.50.4, with the
same inputs against baseline `e300a4c4` and the changed source. Work counts are the
primary evidence; these figures do not establish live latency or production capacity.

| Synthetic workload | Before | After |
| --- | ---: | ---: |
| Ten unread Discord messages, one corrupt row, 6,000 stored messages | 6,000 payloads decoded | 11 payloads decoded |
| Unread existence over 201 channels | 402 SELECTs | 2 SELECTs |
| Event-ID lookup for 801 identities supplied twice | 1,602 SELECTs | 3 SELECTs |
| 500 project memories | 500 team and 500 project visibility resolutions | 1 team and 1 project resolution |
| Chat wake with 200 tasks and two projects | 202 assessment graph builds | 1 graph build |
| Attention for 250 tasks and 5,000 deliverables | 250 deliverable scans/sorts | 1 deliverable scan |
| Metadata for 300 disconnected scoped graphs | 90,600 node-list reads | 300 node-list reads |
| Navigation over 702 related symbols | 702 relationship SELECTs | 2 relationship SELECTs |
| Reuse among 5,001 snapshots for one identity | 5,001 SQLite rows hydrated | 1 row hydrated |
| Approval review in a 29,500,408-byte audit log | 29,500,408 bytes read; 63,802,368-byte peak allocation | 65,536 bytes read; 149,958-byte peak allocation |

In the same instrumented workloads, SQLite VM instructions fell from 30,101 to
557 for the Discord page, 7,897,901 to 86,024 for symbol navigation, and 200,293
to 293 for either repository-rebuild mode. Team memory, wake and attention output
hashes matched the baseline. Navigation pages, selected build IDs and Discord
pages/cursors also matched; randomized probes compared 250 graph results/errors
and 60 navigation pages with the baseline. Independent review additionally
compared 5,000 graph results/errors and 600 navigation pages, and verified that an
injected index-migration failure leaves schema 2 intact and succeeds on retry.

Validation covered all 10,097 Python cases: 10,000 passed and 97 opt-in cases
skipped. The initial complete runner (`scripts/run_tests.py --workers 4 --timeout
900`) reported 9,982 passed, 97 skipped and 18 failures. All eighteen were script
subprocesses unable to use the missing worktree `.venv`, causing unavailable
refresh clients or missing dependencies. After linking the existing shared
virtual environment, an exact eighteen-case rerun passed without production-code
changes. Execution ledgers were checked to prove that the rerun covered every
initial failure. All eight isolated timing checks passed. Changed-file Ruff,
formatting and `git diff --check` also passed. The four added regression modules
contain 37 cases covering the ten findings.

HTTP/TestClient selections attempted under the restricted sandbox exceeded their
90- or 120-second limits; their processes were stopped. The complete runner used
permitted local socket access and exercised those cases successfully. This source
change has not been deployed or tested against live operator workloads.

## Additional bounded-work fixes (0.86.1)

This audit identified ten further costs in the current source. The checks use
synthetic state and deterministic read/iteration bounds; these are not production
latency measurements.

| Finding | Previous work | Fix and preserved behavior |
| --- | --- | --- |
| 1. Cost attribution updates | Read every historical session attribution before a small update. | Look up only requested session IDs in batches of 500; retain duplicate-input handling, unchanged-row detection, and invalidation of old and new Team balances. |
| 2. Teams notification status | Load and validate every queued notification to calculate depth and age; scan all received times for the latest notification. | Aggregate count/minimum over a covering index and use an indexed maximum; preserve active-state selection, empty queues, and future-time age clamping. |
| 3. Semantic code hits | Hydrate every matching chunk and its text before sorting and keeping 1,000. | Rank and limit chunk metadata in SQLite before loading at most 1,000 texts; preserve snapshot scope, last occurrence rank for repeated hashes, and path/line ordering. |
| 4. Team collection pages | Serialize and enrich all matching inbox, blocker, case, deliverable, review, release, and agent records before taking a page. | Apply the existing permission filters and sort, then project only the requested page; retain totals, cursors, offsets, and the 50-record cap. |
| 5. Team reporting visibility | Walk every reporting chain separately and repeatedly scan project assignments for subordinates. | Index reporting links and assignments once per read-service snapshot; preserve the separate explicit-subordinate and reporting-chain visibility rules, including cycles. |
| 6. Supervisory overview | Rescan all tasks, blockers, and releases for every visible project, then discard all but eight projects. | Select the eight displayed projects first and aggregate each collection once; preserve visible project totals, omitted counts, and record visibility checks. |
| 7. Workflow downstream depth | Recursively expand shared descendants once per path, with exponential work on layered branching graphs. | Use an iterative traversal with per-call completed depths; visit each reachable node once, preserve cycle errors, and support paths beyond Python's recursion limit. |
| 8. Merge-gate context | Repeatedly propagate full dominator sets over the entire graph to identify guaranteed upstream context. | Walk only ancestors and construct their dominator tree in topological order; preserve explicit loop exclusions and the previous fallback for unvalidated cyclic graphs. |
| 9. Overdue scheduled events | Decode the entire due backlog before publishing, then reread each payload during atomic publication. | Select up to 200 ordered schedule IDs per publication pass (before and after trigger polling); publication still rereads authoritative state atomically, and remaining due work keeps the next cycle immediately runnable. |
| 10. Internal-event retry pages | Sort and join the complete pending delivery queue despite a page limit. | Order by the equal delivery foreign key so SQLite can use the existing pending index and stop at the limit; preserve event order, retry, and acknowledgement behavior. |

The new Teams indexes are created by the existing store initialization path; the
first access to an existing database pays the index-build cost. Notification counts
still visit active index entries. Team pages still filter and sort matching records;
semantic search still ranks matching chunk metadata. The fixes bound the expensive
payload and projection work without introducing persistent response caches.

Focused regressions are in `test_storage_performance_paths.py`,
`test_team_read_performance_paths.py`, `test_workflow_graph.py`, and
`test_workflow_scaling.py`, alongside the existing domain suites.

Controlled comparisons against the original source showed:

| Synthetic workload | Before | After |
| --- | ---: | ---: |
| One changed attribution among 3,001 stored rows | 3,001 attribution rows read | 1 row read |
| Two-record Team page with 80 matching records | 80 records projected | 2 records projected |
| Reporting visibility for a 200-agent chain | 404 assignment-collection scans | 1 scan |
| Supervisory overview with 30 visible projects | 30 scans of each record collection | 1 scan of each collection |
| Semantic hits with 2,504 matching chunks | 2,504 hydrated hits | 1,000 hydrated hits and text loads |
| Ten pending deliveries among 4,000 | 36,206 SQLite VM instructions | 182 instructions |

The new bounded-work checks fail against the original implementations. Existing
behavior checks also cover partial scheduled-publication failure, permission
boundaries, graph edits, multiple graph roots, explicit loop returns, and cycles.

Initial validation on 2026-10-07 used `scripts/run_tests.py --workers 4 --timeout 900`:
9,697 passed, 97 skipped, and one pre-existing failure across 9,795 collected cases.
All eight timing tests passed. The skips are the opt-in WordPress browser test
and Docker integration cases. The failure,
`test_template_materialization_assigns_forward_child_to_parent_lineage`, also fails
on unchanged baseline `e23a7616`: its monkeypatch targets the absent
`admin_service.get_template` attribute. Changed-file Ruff and whitespace checks passed.

After merging main `9af6dd50` on 2026-10-08, 246 focused tests passed. The complete
runner recorded 9,932 passed, 97 skipped, and three failures across 10,032 cases;
all eight timing tests passed. The template failure and
`test_configuration_schema_offers_named_assistants_and_root_only_routing` both
reproduce on unchanged main; the latter's test runtime lacks
`inbound_phone_routing_service`. The timing-sensitive
`test_slow_origin_query_answer_supersedes_pending_hold_offer` passed in isolated
reruns on both unchanged main and the merged branch. The complete run is therefore
not claimed green. Ruff, conflict-marker, version consistency, and whitespace
checks passed on the merged branch.

## Earlier performance audit (0.59.2)

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
