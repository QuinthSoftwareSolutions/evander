# One editable progress message

This synthetic example never contacts Teams and never launches an agent. From the repository root:

```sh
PYTHONPATH=src:packages/teams-graph-addon .venv/bin/python packages/teams-graph-addon/examples/progress-message/demo.py
```

It uses temporary storage, an unconfigured synthetic HTTPS origin and the core stub. Expect one
message, revisions `[1, 2, 3, 4]`, and a cleared image after cancellation. Every durable creation
intent, progress snapshot, pending image artifact and revision lives in the temporary runtime,
independently of a model conversation.

`nodes.json` contains **node fragments**, not an importable production pack. The first three show
the low-level render → post → update interfaces. Run `render` again for each changed snapshot,
persist the post's message ID/revision in workflow state, then run `update` instead of `post`.
The final two fragments show the preferred durable coordinator; choose it instead of manually
composing the first three. It owns creation, staging, revisions, notification-only retries,
30-second section-update coalescing and source renewal. Its `enabled` field defaults to false.

For a later operator rollout, route `internal.teams.progress-image.renew` and
`internal.teams.progress-image.flush` through existing `trigger:internal:event` subscriptions to
the `renew` fragment. Filter the event to the owning workflow's directory/session/generation;
pass its identifiers through the bindings shown. The provider uses the same durable scheduled-event
store as `effect:event:schedule`, schedules renewal at half the actual signed lifetime, and persists
the resulting schedule ID. Old timers and old section events cannot overwrite newer progress.
There is no new timer service or model wakeup.

Supply `ProgressSnapshot` counts from host completion records and the current approved outline.
`verified_progress_counts` requires matching generation/outline, successful nonsuperseded completion
and matching fragment hashes. A fragment's existence or a queued dispatch never completes a section.
Complete chapters require every required section. Do not identify 100% drafting as final delivery.

For terminal `delivered` snapshots, `delivery` must contain the current `document_sha256`,
`outputs` with `docx` and `pdf` digests, and `receipts` for both formats. Each receipt must have
`status: posted`, matching `source_sha256`/`output_sha256`, `message_id` and a Teams `web_url`.
Only then does the coordinator add those receipt links and clear its temporary PNG. Delivery for
review does not imply owner approval. Cancellation clears the image without inventing deliveries.

The coordinator returns `notification_failed` and retains an operator-readable error without
launching, interrupting or restarting document production. Retry only this notification effect.
An uncertain initial POST is never automatically repeated; it requires operator reconciliation.
An expired session, unavailable or corrupted artifact bytes during uncertain editing, or newly
required file approval also fails closed. An expired signed link can be reconciled before renewal
while its verified source bytes remain available. Renewal cannot extend a session or bypass file policy.

Before channel rollout, obtain edit consent, test channel posts/replies and verify the graphic
refreshes in an authenticated Teams client. Existing messages have no image slot and cannot be
migrated by inference from the shared account. Enable only for new generations through revision-
guarded operator configuration. This example grants no production tools, scopes or permissions.
