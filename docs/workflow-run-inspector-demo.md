# Workflow Run Inspector Demo

This demo seeds a dedicated runtime tree under `runtime/workflow-run-inspector-demo` with a
representative workflow path. It is built from the existing ticket-created agent prompt slice,
then augmented with a graph snapshot, a staged communication artifact, an approval request, and a
legacy run that still shows missing historical detail.

## Seed The Demo Runtime

From the repository root:

```bash
./scripts/seed_workflow_run_inspector_demo.sh
```

The script is idempotent. Re-running it rewrites the same demo records with the same identifiers:

- `event_id`: `evt-detail`
- `trace_id`: `trace-detail`
- `session_id`: `session-detail`
- `artifact_id`: `comm_0123456789abcdef0123456789abcdef`

If you want the files somewhere else, pass `--runtime-dir` explicitly.

## Start Admin Web

Run Admin Web against the demo runtime:

```bash
./.venv/bin/agent-sandbox-admin-web \
  --runtime-dir runtime/workflow-run-inspector-demo \
  --host 127.0.0.1 \
  --port 8100
```

Then open `http://127.0.0.1:8100` and switch to the workflow tab.

## Inspect The Run

Use the workflow run detail lookup in the Current runs panel. Select the matching lookup key or
leave it on Auto for event, trace, session, and communication artifact ids. The Inspect button on a
visible current run opens the same detail panel by event id.

- `event_id=evt-detail`
- `trace_id=trace-detail`
- `session_id=session-detail`
- `artifact_id=comm_0123456789abcdef0123456789abcdef`

The rich run should show:

- trigger node `trigger-ticket`
- gate node `gate-await`
- effect node `effect-prompt`
- graph snapshot revision `rev-detail-rich`
- approval link `approval-rich`
- communication artifact `notes.txt`
- resumable failed effect history plus a waiting branch

The legacy run `evt-legacy` should still resolve, but its detail payload should show missing
historical graph data and no recorded binding trace.

## Expected Shapes

The detail payload is intentionally demo-sized, but it should still be representative of the real
workflow inspector:

- a trigger event with the original payload
- a gate execution with `waiting` status
- an agent prompt effect with bindings, outputs, and a resumable failure
- an approval record linked by `request_id`
- a staged communication artifact linked by `artifact_id`
- a graph snapshot for the captured config revision

If you need to reset the demo, run the seed script again. It will overwrite the demo records in the
same runtime tree without touching other runtime directories.
