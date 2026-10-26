# Orchestration latency and recovery

Effect handoffs use the existing durable FIFO event queue. Completed executor futures are reaped
between events, so ready successors need not wait for broad recovery scans. Each event slice is
bounded by the configured `max_events_per_cycle` (normally 200) and a cooperative 100 ms elapsed
budget. The elapsed check is between events, never inside a transaction or dependency fan-out.

Recovery starts on a five-second cadence, including under a continuous event backlog. Continuations
run on later outer cycles without sleeping. Known lifecycle hooks use 50-candidate / 100 ms slices;
due Team leases get priority, including during a long recovery pass. Gate reconciliation deadlines
and the completion wake socket remain independent. Legacy extension hooks still run as whole
invocations on the recovery cadence. A slow single operation cannot be preempted: it is logged as
a budget overrun and can exceed the cadence/latency target. These constants are internal defaults,
not new workflow settings; lease renewal windows and gate deadlines have not changed.
Storage maintenance runs on a separate single-flight background lane. Its history indexing,
retention, payload collection and checkpoint phases make independent progress in bounded slices.
Maintenance acquires writer slots opportunistically and defers for one second when busy.
Historical backfill waits while the durable event queue has pending work. The dispatcher only
submits or observes maintenance work; it does not run database-wide cleanup between handoffs.
Retention commits scalar-index deletion in chunks before removing expired execution headers.
Partially retired executions are hidden from history reports; an update cancels retirement and
rebuilds the complete index. A deadline rollback halves the record and index batch limits, stored
durably for subsequent slices and restarts, while preserving the current phase and cutoff.

Team recovery reads typed, revision-pinned candidate projections. Active work, pending activation,
governance reservations, current native-thread owners and overdue metadata mirrors are checked
before separately paginated historical receipt recovery. Prompt receipts are selected by immutable
dispatch IDs using an indexed metadata projection, with the expression-indexed inline lookup
retained for legacy records. All receipts for a selected dispatch are
considered, so a newer missing-thread receipt wins over an older success. Actual changes still use
the authoritative transactional Team runtime service. Generic replay revalidates Team ownership
before claiming; FIFO, single-flight, capacity/profile deferrals and compare-delete remain in place.
Process-local cursors only reduce repeated work: restart begins a fresh recovery pass, and durable
records remain the source of truth. Missing events are repaired by recovery.

### Container-worker RPC continuations

Container-worker prompt reconciliation uses four background RPC workers and retains at most 32
pending execution continuations. Inspection, log reads, watchdog interruption, stop and cleanup
run there with their existing client timeouts. A slow worker request leaves the dispatcher free
to process events and other recovery candidates. Completed operations are reused while that
reconciliation pass continues, so a later pending log read or cleanup does not repeat an RPC.
Timeout decisions use the pass's observation time; waiting for the RPC pool cannot turn an
earlier running observation into a newly expired job. Capacity deferral is recorded after cleanup,
so resuming a pass cannot advance the retry ledger twice.

Callbacks only wake the dispatcher. The orchestration thread rechecks the durable waiting record
and worker execution identity, then performs local state changes and completion publication. A
ready result makes recovery due immediately rather than adding another five-second polling delay.
Each continuation pins its worker job, and RPC observations commit conditionally against the
current lifecycle row. Late results cannot restore deleted jobs, overwrite cleanup or target a
replacement job. Concurrent receipt progress and unrelated metadata are retained.

Configuration rebinding and orderly shutdown drain the pool before releasing runtime bindings.
Restart loses only process-local results; durable waiting records are inspected again. These
changes benefit container-backed agents across entrypoints, including personal assistants and
browser sessions, as well as Team agents. Libvirt reconciliation retains its existing path.

### Request-scoped Team MCP reads

Read-only Team tools open one lazy, query-only SQLite transaction after the session policy check.
Caller authorization and operational data use that committed revision. Keyed lookups hydrate
only the selected records and their child rows; enumeration loads the requested collection.
Existing relationship, assessment, pagination and visibility rules still apply. The request
closes its snapshot on both success and failure, and the partial view cannot be persisted as
complete Team state. Tool names and input/output schemas are unchanged.

An indexed attention-candidate check avoids full state hydration when an active caller has no
pending operator messages. Pending messages retain the existing current-lease checks, delivery
bookkeeping and conversation context. That exceptional write path can still load full state,
as can ordinary action tools; this is not a bound on every Team operation. Immutable archived
case-context snapshots retain their existing dispatch-scoped lookup.

### Isolated Team maintenance

`TeamAgentPromptCompletionFinalizeHook` opts into a dedicated single-flight background lane.
Foreground reconciliation calls submit coalesced work and return without waiting for Team
transactions or execution-receipt queries. Due lease slices take priority over targeted completion
hints and broad recovery. At most 50 immutable dispatch-ID hints are retained; overflow is repaired
from durable receipts, and a continuous hint stream cannot starve broad recovery. Completion
callbacks only wake the dispatcher; they do not run workflow successors.

The Team repository is initialized once per runtime binding, before background work starts.
Candidate discovery uses query-only connections and a single revision-pinned read transaction,
without taking the repository writer lock or renegotiating WAL. Maintenance writes use a 100 ms
SQLite busy timeout. Contention retains the failed candidate and defers that phase for five seconds;
unrelated foreground events and other ready maintenance phases can continue. Transactions are not
cancelled. The existing 50-candidate / 100 ms cooperative budget still applies between candidates.
Execution receipts also use a separate read-only connection without holding the foreground
execution repository's lock or negotiating WAL.

Queued-prompt ownership validation remains synchronous and read-only; an unavailable or invalid
ownership check never grants permission to replay. Configuration rebinding drains the lane before
changing stores or providers and rechecks the old event queue afterwards. Graph-only edits preserve
the running lane and its cursors. Orderly shutdown and every locked single-cycle exit, including
failures, drain the lane before releasing the worker lock. Replacing a live Team database is
unsupported: the worker detects a changed file identity and requires restart/schema initialization.

## Team dispatch storage

Team repository construction checks schema and state version markers through a read-only
connection. Initial setup and upgrades run under a shared initialization lock, and empty state is
created only after checking again with the writer reserved. Ordinary state and projection reads
pin a committed snapshot without taking the Team writer lock.

Chat dispatch selection uses the same optimistic planning pattern as ordinary Team leases.
Session activity checks and candidate validation happen before the writer reservation; commit
verifies the Team revision. A conflict causes at most three complete replans. Targeted wake
exhaustion schedules a delayed successor while preserving the queued message. The final prompt
claim still excludes concurrent prompts; if activity changes after planning, delivery queues the
reserved dispatch and starts its lease only after actual delivery.

Finalization publishes a compact receipt rather than historical Team state. Immediate and delayed
event publishers initialize lazily and reuse their store objects across provider invocations and
outbox batches. Actual SQLite operations use separate connections, and an empty event list does
not initialize a publisher. Team state and outbox intents remain committed together before
publication, so a publication failure can be retried.

Completed futures, durable pending receipts and delivery bookkeeping share a rotating budget of
32 entries and 100 ms per cycle. A transient storage error keeps ownership of the completed result.
SQLite contention retries start at 25 ms and cap at 250 ms per entry; the shared completion lane
waits at most 25 ms so one contended item cannot hold ready work behind its backoff. Adapter
publication outages use a separate 250 ms to five-second exponential backoff. Retries have jitter.
Before a retry controls sleep, the worker checks that its future, bookkeeping entry or pending
receipt still exists. Cleared receipts lose their retry alarms. Checks share the completion budget
and rotate through the backlog; storage failures preserve ownership and use the existing backoff.
The trigger worker also defers transient storage failures with a 25 ms to 250 ms retry;
subscription reconciliation and durable event publication resume without restarting the worker.
Trigger and effect cycles explicitly cap storage lock waits at 100 ms. This allowance covers
repository locks and SQLite contention; elapsed query execution and record hydration do not abort
an otherwise uncontended operation. Other callers retain a 30-second lock allowance by default.
Effect and gate receipt, successor and journal commits are atomic; external provider execution
itself is not made transactional by SQLite.

## Reading timings

Effect execution payloads may now include:

| Field | Meaning |
| --- | --- |
| `executor_queue_duration_ms` | Submission to actual provider start, monotonic clock |
| `provider_started_at`, `provider_returned_at` | UTC timestamps inside the executor wrapper |
| `provider_duration_ms` | Provider execution duration, monotonic clock, including failed calls |
| `completion_observed_at`, `return_to_reap_ms` | When the orchestrator noticed return, and the monotonic delay |
| `completion_published_at` | Successful completion-publication observation in the orchestrator |

Old records have no measurements; null means unavailable, not zero. Existing `started_at`,
`finished_at` and `completion_event_enqueued_at` retain their old lifecycle meanings. In particular,
`finished_at - started_at` is **not** pure provider runtime. For enqueue-prompt, provider runtime
describes the enqueue call, not the entire model/worker job. Event-dispatch logs include event ID,
trace ID and UTC dispatch timestamp for downstream correlation. Cross-process timestamp differences
require synchronized clocks. No prompt text, message contents or credentials are included in the
new timing instrumentation.

`workflow_execution_storage` logs separate the full persistence operation from `record_write`
and `history` phases. `record_write` covers the SQL upsert and immutable object references, including any
writer wait there; `history` covers compact history metadata maintenance. Payload serialization,
hashing and compression are prepared before acquiring the writer. Total duration also includes
payload preparation, repository locks, connection setup, commit/close and journal publication. Gate-completion totals
include atomic successor publication. The phase durations are inside the total and must not be
added to it. These diagnostic logs use DEBUG and are disabled under the usual INFO configuration.
Temporarily enable DEBUG for `evander.repositories.orchestration` to capture them. These measurements
identify storage work but do not independently measure SQLite lock wait.

Lifecycle-only history updates reuse stored compressed payloads. Changed input/output payloads
commit a durable indexing job with their receipt; background maintenance builds the scalar
projection. History query responses expose `indexing_complete` and `indexing_pending_count` so
pending work is visible. Details retain the recorded payload while indexing catches up. Status,
directory scope and source provenance remain durable.
Lifecycle fields, including long errors and clearing reasons, remain inline. Older referenced
errors are readable in summaries; replacing them removes obsolete field references while
preserving shared payloads. Workflow reports, including execution-history search, fields,
authorization and detail reads, have an explicit 30-second operation deadline. Ordinary storage
operations have no implicit execution deadline. Callers choose any execution deadline separately
from their lock-wait limit; reports use the normal 30-second lock allowance.

`sqlite_operation` logs report operation duration and connection-close time. `storage_maintenance`
logs identify the phase, elapsed time, outcome and progress. Orchestration background checkpointing uses PASSIVE;
legacy blocking checkpoint settings are normalized for online work. No connection operation
renegotiates WAL after initialization. Shared idle read-only connections keep the database open
without holding a transaction or snapshot, avoiding a large last-connection checkpoint on each
request. Explicit SQL operation deadlines also cap lock waits and interrupt long SQL execution; filesystem
commit/close I/O is measured separately and cannot be given the same hard time bound.
The Team database retains SQLite automatic checkpointing because it has no dedicated background
checkpoint owner. Disabling automatic checkpoints is an explicit orchestration-store choice.

Recovery phase logs name each underlying hook and report elapsed time, records examined, changes,
whether work remains, and budget overruns. Examined count is unavailable for legacy hooks.
`Isolated maintenance completed` logs report actual background duration and outcome, separately
from the foreground submission timing. Team subphase logs distinguish repository initialization,
database connection, candidate selection, indexed receipt lookup, lease renewal and completion
finalization. Slow or failed phases are logged at info/warning level; fast subphases use debug.

Team background dispatch activation, native receipt reconciliation and finalization prepare
state validation and row deltas before taking the writer lock. Finalization checks both the
Team revision and its dispatch activity evidence again under the writer; conflicting plans
are deferred without publishing their events. Background snapshot assembly has a separate
five-second operation budget, while its SQLite lock waits remain capped at 100 ms.

## Verification and deployment gate

Run focused coverage from the checkout using its own source path:

```bash
PYTHONPATH=src timeout 120s .venv/bin/pytest -q -s tests/test_orchestration_handoff_latency.py
PYTHONPATH=src timeout 120s .venv/bin/pytest -q -s tests/test_team_isolated_maintenance.py
PYTHONPATH=src:tests timeout 120s .venv/bin/pytest -q tests/test_deferred_backend_reconciliation.py tests/test_team_mcp_read_scope.py
```

The 30-chain benchmark stubs external work. Its target is cumulative inter-provider handoff p95
under two seconds; it does not measure cloning, worker startup, reference preparation, model time
or Teams delivery. Local success is not a production latency claim.
The isolated-maintenance suite also holds a real SQLite write lock while 30 six-stage workflows
run alongside two additional active teams, 5,000 historical dispatches and 10,000 unrelated effect
records. It checks foreground p95 below five seconds, eventual lease renewal, restart recovery,
and idempotent finalization. External provider/session behavior is stubbed.

Before an explicitly approved deployment, validate index creation/open time on a representative
copy of the orchestration and Team databases, record baseline sizes and timings, and follow the
normal controlled deployment/restart procedure. Never rewrite live workflow packs for this change.
Then observe operator-generated assistant messages and normal PR-review activity: compare at least
30 traces, inspect handoff p50/p95, recovery lag, lease renewals, duplicate executions and queue age.
Before code rollback, use the explicit
[storage downgrade procedure](operations.md#orchestration-storage-migration-and-rollback) to
rehydrate referenced operational records. Additive tables and indexes may remain. Local verification
requires no live configuration change, service restart or synthetic external message.
