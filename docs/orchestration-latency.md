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
Execution-history backfill only runs after a completed recovery pass with no in-flight effects
or pending events, so it does not sit between a running provider's return and reaping.

Team recovery reads typed, revision-pinned candidate projections. Active work, pending activation,
governance reservations, current native-thread owners and overdue metadata mirrors are checked
before separately paginated historical receipt recovery. Prompt receipts are selected by immutable
dispatch IDs using an additive SQLite expression index. All receipts for a selected dispatch are
considered, so a newer missing-thread receipt wins over an older success. Actual changes still use
the authoritative transactional Team runtime service. Generic replay revalidates Team ownership
before claiming; FIFO, single-flight, capacity/profile deferrals and compare-delete remain in place.
Process-local cursors only reduce repeated work: restart begins a fresh recovery pass, and durable
records remain the source of truth. Missing events are repaired by recovery.

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
Execution receipts also use a separate read-only connection with a 100 ms busy timeout, without
holding the foreground execution repository's lock or negotiating WAL.

Queued-prompt ownership validation remains synchronous and read-only; an unavailable or invalid
ownership check never grants permission to replay. Configuration rebinding drains the lane before
changing stores or providers and rechecks the old event queue afterwards. Graph-only edits preserve
the running lane and its cursors. Orderly shutdown and every locked single-cycle exit, including
failures, drain the lane before releasing the worker lock. Replacing a live Team database is
unsupported: the worker detects a changed file identity and requires restart/schema initialization.

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
and `history` phases. `record_write` includes serialization and the SQL upsert, including any
writer wait there; `history` covers compact history maintenance. Total duration also includes
repository locks, connection setup, commit/close and journal publication. Gate-completion totals
include atomic successor publication. The phase durations are inside the total and must not be
added to it. These diagnostic logs use DEBUG and are disabled under the usual INFO configuration.
Temporarily enable DEBUG for `evander.repositories.orchestration` to capture them. These measurements
identify storage work but do not independently measure SQLite lock wait.

Lifecycle-only history updates reuse already stored compressed payloads and leave unchanged
scalar-index rows in place. Changed input/output payloads rebuild their indexes in the same
transaction; status, directory scope and source provenance continue to be refreshed.

Recovery phase logs name each underlying hook and report elapsed time, records examined, changes,
whether work remains, and budget overruns. Examined count is unavailable for legacy hooks.
`Isolated maintenance completed` logs report actual background duration and outcome, separately
from the foreground submission timing. Team subphase logs distinguish repository initialization,
database connection, candidate selection, indexed receipt lookup, lease renewal and completion
finalization. Slow or failed phases are logged at info/warning level; fast subphases use debug.

## Verification and deployment gate

Run focused coverage from the checkout using its own source path:

```bash
PYTHONPATH=src timeout 120s .venv/bin/pytest -q -s tests/test_orchestration_handoff_latency.py
PYTHONPATH=src timeout 120s .venv/bin/pytest -q -s tests/test_team_isolated_maintenance.py
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
Code rollback can leave additive indexes and optional payload fields in place. No live configuration
change, service restart or synthetic external message is required by local verification.
