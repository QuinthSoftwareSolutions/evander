# Team case-context history

Case-context snapshots are immutable evidence. Their payloads remain in
`team_case_context_snapshots` in the Team `state.db`, alongside operational state,
command receipts, and outbox records. Ordinary `TeamState` reads, dumps,
validation, and record deltas exclude the archive.

## Reading and writing snapshots

Use `TeamStateRepository.snapshots` for explicit reads by ID or dispatch, case
pages, and eligible previous-dispatch selection. Case pages and baseline selection
read indexed metadata; only selected snapshot retrieval deserializes evidence.
Directory filters use complete path segments. Case pages retain their existing
offset cursors and descending creation-time/ID ordering.

`snapshot_in_state` stages new snapshots on the mutation's state. The private
buffer contains only additions from that mutation and is excluded from state
serialization. State validation and revision checks still apply. Persistence
commits the payload, lookup metadata, operational state, command receipts, and
outbox in the same SQLite transaction. Failed writes or stale prepared commits
publish none of these changes. Replanning starts with a fresh buffer.

Snapshot IDs, content digests, recipient grants, captured views, and evidence are
preserved. Agent/operator permission checks and HTTP/MCP response shapes are
unchanged. The external Team-state adapters continue to return an empty
`case_context_snapshots` compatibility field.

## Upgrade and rollback

1. Quiesce all processes that write Team state and take a consistent SQLite backup
   of the complete database, including any committed WAL contents. Copying just
   the main database file while writers are active is insufficient.
2. Upgrade all Team writers together. Schema version 81 adds metadata and
   case-association tables and indexes. Initialization extracts only headers in
   batches of 128 snapshots; it does not reconstruct the archived evidence as
   Python models or rewrite payload JSON. Startup readiness follows completion.
3. Check readiness and ordinary wake/completion behavior. Read an existing
   snapshot using its original authorized session, and check a case-history page.
4. Monitor process RSS and host memory pressure through representative workload
   cycles before increasing concurrency. Deployment and live checks are separate
   from local implementation verification.

The previous release can still read the original payload table. To roll back,
quiesce writers again before replacing binaries. Retain the additive tables and
triggers: older inserts/updates mark metadata for repair, and older deletes remove
their index entries. When the new release reopens the database, initialization
repairs the queued metadata before declaring the schema ready. A partially
completed initial backfill is also retried. Mixed-version concurrent writers are
not supported. Rolling back also restores the older archive-loading memory cost.

## Copy, backup, import, and reset

- Team configuration copies retain the source archive and create no copied
  snapshot history or recipient grants.
- `read_object()` and `write_object()` operate on operational state. Replacement
  and migrations preserve the archive. A legacy `write_object()` import may
  explicitly supply `case_context_snapshots`; these rows are merged atomically,
  with ID/key validation and rejection of conflicting immutable snapshots.
- Use a consistent backup of the entire SQLite database for a complete export or
  restore. An operational state dump alone is not a history backup.
- Operational replacement with an empty state does not erase archived evidence.
  A deliberate complete reset requires an offline replacement of the whole
  database after backup. There is no implicit archive cleanup in ordinary state
  mutations.

## Reproducible memory measurement

Run the same script and Python environment against a baseline checkout and a
candidate checkout:

```bash
python scripts/benchmark_team_history_memory.py --source /path/to/baseline --archive-mib 86
python scripts/benchmark_team_history_memory.py --source /path/to/candidate --archive-mib 86
python scripts/benchmark_team_history_memory.py --source /path/to/candidate --archive-mib 862
```

Each run creates temporary synthetic evidence, then uses a fresh subprocess for
five concurrent chat-claim/completion-decision cycles. Seeding is excluded from
the measurement. Subprocesses have a 3 GiB address-space cap and a 180-second
timeout. The output reports actual archive bytes, snapshot count, peak/retained
RSS, and elapsed time. The larger case exceeds ten times the smaller archive
after rounding to whole snapshots.

Acceptance targets: at least 50% lower peak RSS than baseline at the smaller
size; growing unrelated history tenfold should increase candidate peak RSS by
no more than the greater of 10% or 32 MiB. These synthetic checks establish
history-independent lifecycle reads; production working sets still depend on
operational state, new snapshot size, and concurrent work.
