# Team context reduction: deployment and pilot runbook

This change is implemented and tested locally. It does not edit local or remote
operator configuration, start a live pilot, or change model assignments. The
proposed pilot sets the existing `work_lineage` policy for the concrete persistent
agents fulfilling Truman's and Trudy's management roles. Other roles keep their
current policies. Planning, parallel work, independent review, acceptance, and
release gates remain required.

## Delivered behavior

Every ordinary or mutation-enabled chat wake supplies the current role, scope,
work instructions, and obligations independently of retained messages. The mode
is `fresh_full` or `retained_current`. No compaction detector, historical backfill,
relay history rewriting, or generic tool-result truncation is introduced.

Preview caps produce `omitted_counts`, collection-level `retrieval` references,
and `overflow` records with IDs, current state, actionable relationships, release
SHAs/next actions, and `read_required`. Read the full referenced record before
acting on omitted detail. All urgent inbox messages and operator directives are
included completely, even above the normal list cap. Existing chat delivery,
source-message acknowledgement, and participant revocation rules still apply.

`project_repos[].git_work_context_refs` addresses the exact bindings in
`git_work_contexts`. `repository_snapshot_ref` and
`review_repository_snapshot_refs` address `review_repository_snapshots`.
These content-derived references apply within that payload; their records retain
the durable context IDs, worktree paths, pinned refs, and commits. Different
bindings or captures are never collapsed merely because their repository or
external version matches. Manager summary rows with `record_collection` inherit
matching fields from the named collection's record with the same `id`.

`team_get_current_dispatch_context(detail="summary")` returns compact authorized
records, complete immutable scope IDs, evidence references, and exact dispatch Git
bindings. It does not widen access or resolve a newer Git binding. Omitted `detail`
and `detail="full"` retain the previous full response. Complete records remain
available through individual Team reads. General task reads can include other
visible Git contexts; those never replace the wake/dispatch's frozen binding. The new
`team_get_source_evidence_snapshot(snapshot_id=...)` reads one complete capture
only when its ID belongs to the caller's active immutable dispatch. No active
dispatch or an out-of-scope ID returns the normal not-found/scope response.

Managed guidance version 40 teaches summary-first reads, complete specification
and evidence retrieval before implementation/review, targeted `rg` searches, and
bounded excerpts. Large build/test logs belong in complete artifacts under
`/artifacts`; report status, relevant failures, and the artifact reference. The
review and manager-fallback procedures now live in managed guidance. They remain
part of the agent's instructions and their loading cost must be counted.

Managers receive `supervisory_overview` counts and paginated retrieval references
across their existing read visibility. The overview is advisory read context;
`current_dispatch` remains the execution boundary. Work-lineage dispatch still
resumes the explicit saved family thread. Children and corrections inherit their
family, mixed/partly unbound releases receive a release lineage, and successors
inherit that release lineage. Existing unbound tasks run fresh without backfill.
The existing missing-thread path retries once with fresh context.

### Native thread recovery (0.32.1)

Asynchronous container dispatch returns no native thread until the provider reports
one. `dispatch_id` and `execution_id` remain correlation IDs. Reconciliation reads
the worker's native metadata or searches that execution's raw log for its startup
header, then saves `native_thread_id` and `native_thread_observed_at` on the Team
dispatch. Each lineage binding retains its owning dispatch, so delayed completions
cannot replace the binding of a later wake or change another lineage's context.

The Team SQLite store adds these receipt columns and the lineage owner map on
normal startup; no operator pack or direct database edits are required. Already
running or completed pre-fix dispatches can recover their own poisoned bindings
from retained execution evidence. If the native identity cannot be recovered,
the next wake treats a synthetic binding as unbound and uses the current durable
Team records with `fresh_full` context. It does not reconstruct conversation
history or re-investigate work already recorded in tasks and deliverables.
If a provider later reports that an explicitly resumed native thread is missing,
completion reconciliation invalidates only that dispatch's owned binding. The
next Team wake uses fresh durable context; an older failure cannot invalidate a
binding subsequently established by another wake.

After an authorized deployment, the first wake may start fresh with no requested
resume ID. On the next same-lineage wake, compare its requested `resume_thread_id`
and worker-observed native ID with the first wake's observed ID and the persisted
Team lineage binding. They must identify the same native conversation. Repeat
after a wake for another lineage and verify the return uses the original native ID. A
`retained_current` label or successful dispatch alone does not prove resumption.

The columns are additive. Older code can read the database, but may erase new
receipts when it writes and reintroduce synthetic bindings. For rollback, use the
supported revision-checked Team controls to leave `work_lineage` before deploying
the older code; changing the policy clears bindings and their owners together.
Returning to 0.32.1 and `work_lineage` starts with fresh, durable context and captures
new native identities. Preserve runtime evidence before rollback.

Local 0.32.1 verification passed the native-thread, lineage, selected runtime and
container-prompt suites, context-retry checks, and focused repository, context,
operator-chat, and source-evidence tests. Run the new regression suite with an
explicit timeout, using the checkout's `src` on `PYTHONPATH` if its virtualenv is
shared with another worktree:

```bash
timeout 120s python -m pytest -q tests/test_team_native_threads.py
```

The broader unified-persistence suite has nine failures that reproduce on the
unchanged 0.32.0 base: one stale collection-count expectation and eight legacy
release-base/retry expectations. A combined 642-test batch timed out at 180 seconds
at the sandbox HTTP tests; the isolated operator-chat and source-evidence suites
passed all 31 tests outside the sandbox. These results are local regression
evidence; the live native-ID comparison above remains required.

## Reproduce local validation and measurements

Original 0.32.0 validation on 2026-09-09 passed 377 distinct tests across the suites below,
plus Ruff and `git diff --check`. The initial read/evidence command reached its
180-second sandbox timeout; the identical focused suite passed outside the
sandbox (42 tests in 13.64 seconds). No live configuration was changed.

From the checkout, with its development virtual environment:

```bash
timeout --preserve-status -k 10s 180s ./.venv/bin/pytest -q \
  tests/test_team_context_reductions.py \
  tests/test_team_addon_mcp.py \
  tests/test_team_addon_review_mcp_capability.py \
  tests/test_team_addon_mcp_read_tools.py \
  tests/test_team_external_source_evidence.py \
  tests/test_team_work_lineage_domain_regressions.py \
  tests/test_team_work_lineage_runtime_regressions.py \
  tests/test_team_work_lineage_scope_regressions.py \
  tests/test_team_operator_chat_context.py

timeout --preserve-status -k 10s 180s ./.venv/bin/pytest -q \
  tests/test_team_addon_runtime.py

timeout --preserve-status -k 10s 60s ./.venv/bin/python scripts/compare_team_context.py \
  --baseline 243aed9fcda2f558d2eeea49992dba5481b71595 \
  --json-output /tmp/team-context-comparison.json \
  --markdown-output /tmp/team-context-comparison.md
```

The harness reads only trusted local Git code and synthetic fixtures. It records
its baseline commit and fixture hash, uses no live state, and makes no model or
network calls. The [fixture report](reports/team-context-comparison.md) and
[machine-readable measurements](reports/team-context-comparison.json) include wake
bytes/characters, read bytes/characters, duplicate string content, and extra
retrieval calls/bytes. Its selected-item scenario retrieves one full task and one
full evidence record; it does not represent completion of the whole dispatch.
The all-detail scenario shows the penalty of rehydrating everything.

The original approximately $81 saving against a $373 internal Team baseline
(21.7%) is a scenario estimate: $23 from wakes, $40 from focused reads, and $18
from management history after payload reductions. The aggregate cost report
cannot support exact historical token replay. Neither these fixtures nor a
smaller prompt demonstrate that saving. The 50% wake and 35% targeted-read
reductions are ambitious measurement targets; functional acceptance is preserved
obligations and controls.

The evidence suite includes local TestClient calls. If the managed sandbox stalls,
stop the timed command, record the exact command, duration, and last output, then
rerun the same focused suite outside the sandbox with a timeout. Do not change
runtime behavior or weaken assertions to accommodate a sandbox socket artifact.

## Later deployment

1. Record the deployed application revision/version, Team state revision, model
   assignments, provider settings, and the two agents' current context policies
   and idle grace values through Admin Web or its authenticated API. Resolve
   concrete IDs from their current roles and display names; names alone are not
   authorization. Keep a supported backup under the site's normal procedure.
2. Pause new Team scheduling through the supported operator controls and let
   active and queued dispatches drain. Record any dispatch that cannot drain.
   Avoid changing guidance or code halfway through a prepared/queued prompt.
3. Deploy the tested revision through the site's normal package/service process.
   Update the gateway and Team runtime together so the new evidence tool and
   guidance are available together. Use the supported runtime target and deployed
   service units (see `scripts/install_systemd_units.sh`); do not kill individual
   agents or edit live JSON/SQLite. This feature adds no state-schema migration.
4. Resume scheduling and verify service health, deployed revision, next prepared
   dispatch and tool-grant metadata. Confirm that managed section
   `evander-team-addon` in `/home/agent/AGENTS.md` was reseeded at version 40.
   Read a real delivered prompt and its dispatch record, not just a queued event.
   Confirm a summary read, an individual evidence read, exact worktree/SHA binding,
   and normal inbox/review completion on an appropriate authorized item.
5. After collecting an unchanged-policy baseline, edit each selected agent's
   Context policy to **Work lineage** in Admin Web. The supported equivalent is
   `PATCH /api/addons/team-addon/agents/{agent_id}` with only
   `context_clearing_policy: "work_lineage"` and the freshly read
   `expected_revision`. Read back after each edit; on a revision conflict reload
   and reapply only the intended field. Do not change role permissions, models,
   other agents, project/release policies, or workflow pack files.
6. Confirm the next dispatch's lineage, exact task scope, and explicit thread
   binding; switch families and return to verify resumption. An unbound wake must
   start fresh. Compare supervisory counts with the supported current state view
   while confirming they do not appear as added dispatch task/project authority.

## Pilot measurements and acceptance

Use comparable completed items matched by work kind, size, repository,
review/release requirements, and model assignment. Record baseline and pilot
windows and sample sizes. Count all provider-billed usage, including cached input,
output, extra reads, tool requests/envelopes, managed-guidance loading, fresh
context rebuilding, missing-thread retries, ordinary retries, and rework.

For each item record completion time, cost to accepted completion, review results,
reopened findings, releases and their actual approval/publication evidence. Count
blocked or abandoned work as well, so fewer completions cannot look like savings.
Report wake/read/history contributions separately and then the net item cost.
Correctness requires every obligation to remain discoverable and handled, exact
candidate/approved SHAs, immutable execution scope, independent review, release
gates, and correct operator delivery/acknowledgement. Stop the pilot on lost work,
scope widening, stale-binding use, or a review/release bypass. Billing savings and
management-history savings require live measurements.

## Rollback

1. Pause new scheduling and drain active/queued dispatches through the supported
   controls before reversing code or policy.
2. Restore each selected agent's captured context policy and idle grace using
   Admin Web or the same revision-checked agent API; read back each result.
   Switching into or out of `work_lineage` clears the saved lineage-thread map by
   existing design. Do not restore thread maps or backfill tasks through DB edits.
3. If code rollback is needed, redeploy the captured earlier application revision
   and restart the supported gateway/runtime units together. A guidance version
   mismatch causes the earlier managed guidance to be reseeded on preparation.
   Verify the actual delivered guidance version and restored tool grants.
4. Resume scheduling and verify a completed dispatch, operator acknowledgement,
   review outcome and any release gate involved. Keep the comparison and pilot
   evidence for analysis; application rollback does not erase durable Team work.
