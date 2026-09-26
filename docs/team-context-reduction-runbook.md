# Team context policies: operation and measurement

Use this runbook to evaluate Team context behavior or plan a controlled
`work_lineage` pilot. Context policies change conversation reuse; they do not
relax dispatch scope, task obligations, independent review, acceptance, or
release gates. Apply policy changes through Admin Web or its revision-checked
API, and measure completed work before claiming cost savings.

## Current wake and read behavior

Every ordinary or mutation-enabled Chat wake supplies current identity, role,
scope, instructions, and obligations independently of retained messages. The
prompt mode is `fresh_full` or `retained_current`. Reply-only Chat remains scoped
to the claimed messages and does not consume a pending ordinary context reset.

Bounded previews expose `omitted_counts`, collection-level `retrieval` references,
and `overflow` records with IDs, current state, actionable relationships,
release SHAs, next actions, and `read_required`. Read the referenced record before
acting on omitted detail. Urgent inbox messages and operator directives remain
complete even above the normal list cap. Chat delivery, source-message
acknowledgement, and participant revocation rules still apply.

`project_repos[].git_work_context_refs` addresses exact bindings in
`git_work_contexts`. `repository_snapshot_ref` and
`review_repository_snapshot_refs` address `review_repository_snapshots`.
These references deduplicate content within a payload; their records retain the
durable context IDs, worktree paths, pinned refs, and commits. Distinct bindings
or captures remain distinct even when their repository or external version matches.
Manager summary rows with `record_collection` inherit matching fields from the
named collection's record with the same `id`.

`team_get_current_dispatch_context(detail="summary")` returns compact authorized
records, complete immutable scope IDs, evidence references, and exact dispatch
Git bindings. Omitting `detail`, or selecting `detail="full"`, returns full
permitted detail. Neither form widens authority or substitutes a newer Git
binding. Individual Team reads provide full records; general task reads may show
other visible contexts, which do not replace the active dispatch's binding.
`team_get_source_evidence_snapshot(snapshot_id=...)` returns a complete capture
only when its ID belongs to the caller's active immutable dispatch.

Managed guidance directs agents to start from the supplied wake, use individual
record reads for missing detail, and recover the working set through a dispatch
summary read when necessary. Check the deployed
`src/evander_team_addon/provider_constants.py` guidance version against the managed
`evander-team-addon` section in `/home/agent/AGENTS.md`; do not pin rollout checks
to a version number copied from an older runbook. Preparation refreshes stale
managed guidance.

Complete specification and evidence retrieval before implementation or review.
Repository queries use the assigned dispatch commit and may return unchanged
files from an indexed ancestor, with both SHAs and a `baseline` match. They do
not start compiler indexing. Search new, changed, and dirty files locally and
verify symbol relationships even when source text is unchanged. A local commit
does not rebind the assignment. Keep complete build and test logs in `/artifacts`
and report the status, relevant failures, and artifact reference.

Managers receive `supervisory_overview` counts and paginated retrieval references
within their read visibility. Those summaries remain advisory;
`current_dispatch` defines execution authority.

## Work lineages and native conversation identity

A work-lineage dispatch resumes the saved native conversation for one task
family. Children and corrections inherit their family. Mixed or partly unbound
releases receive a release lineage, and successors inherit it. Existing unbound
tasks start fresh; no historical task backfill is required. A missing native
thread can trigger one fresh-context recovery.

An asynchronous container dispatch has no native thread identity until its
provider reports one. `dispatch_id` and `execution_id` are correlation IDs,
not resumable provider conversation IDs. Reconciliation reads worker metadata
or the execution's startup log and saves `native_thread_id` and
`native_thread_observed_at` on the dispatch. Each lineage binding retains its
owning dispatch, preventing delayed completions from replacing a newer binding.

Maintenance backfills at most 32 eligible native-thread receipts per Team-state
transaction, prioritizing active dispatches and current lineage owners. Deferred
receipts keep their dispatch completion pending until saved. The independent
completion workflow reads its target dispatch's receipt and applies it within
the lease-release transaction, so completion need not wait for the historical
batch. Already recorded or unrecordable history does not require a write.

Normal startup applies the native-receipt schema migrations. No direct database
or workflow-pack edits are required. If a legacy synthetic binding cannot be
recovered from execution evidence, the next wake starts with fresh durable Team
context. A missing-thread failure invalidates only the binding still owned by
that dispatch; it cannot invalidate a later dispatch's replacement.

To verify resumption after deployment:

1. Record the first wake's observed native thread ID and saved lineage binding.
2. On the next wake for that lineage, compare the requested `resume_thread_id`,
   worker-observed native ID, and saved binding. They must identify the same
   native conversation.
3. Wake another lineage, then return to the first and repeat the comparison.
   A `retained_current` label or a successful dispatch alone does not prove
   native conversation reuse.

## Local validation and offline measurements

Use the checkout's development environment and explicit timeouts. When a
virtualenv is shared with another worktree, set `PYTHONPATH` to this checkout's
`src` before running tests.

```bash
timeout --preserve-status -k 10s 180s ./.venv/bin/pytest -q \
  tests/test_team_context_reductions.py \
  tests/test_team_native_threads.py \
  tests/test_team_work_lineage_domain_regressions.py \
  tests/test_team_work_lineage_runtime_regressions.py \
  tests/test_team_work_lineage_scope_regressions.py \
  tests/test_team_operator_chat_context.py
```

For an offline comparison, choose a trusted local baseline commit and run:

```bash
timeout --preserve-status -k 10s 60s ./.venv/bin/python scripts/compare_team_context.py \
  --baseline BASELINE_COMMIT \
  --json-output /tmp/team-context-comparison.json \
  --markdown-output /tmp/team-context-comparison.md
```

The harness evaluates trusted local Git code against synthetic fixtures. It
records the baseline commit and fixture hash, uses no live state, and makes no
model or network calls. The checked-in [fixture report](reports/team-context-comparison.md)
and [measurements](reports/team-context-comparison.json) are historical examples,
not validation of the current checkout. Their selected-item scenario retrieves
one full task and one full evidence record; it does not measure completion of
the whole dispatch. The all-detail scenario shows the cost of rehydrating every
record. Smaller payloads alone do not demonstrate lower cost per completed item.

Some evidence suites use local TestClient calls. If a timed command stalls in
the managed sandbox, stop it, record the exact command, timeout, and last useful
output, then repeat the same focused suite outside the sandbox with a timeout.
Do not weaken assertions or leave stalled test processes running.

## Deployment and pilot

1. Record the deployed application revision, Team revision, model assignments,
   provider settings, and selected agents' current context policies and idle
   grace values. Resolve concrete IDs through current supported state. Take a
   backup under the site's normal procedure.
2. Pause new Team scheduling through supported controls and let active and
   queued dispatches drain. Record any that cannot drain. Keep guidance and
   code consistent with already prepared prompts.
3. Deploy the tested revision through the site's normal process. Update the
   gateway and Team runtime together so tools, schema readers/writers, and
   guidance remain compatible. Use deployed service units; do not kill
   individual agents or edit live JSON/SQLite.
4. Resume scheduling and verify service health, deployed revision, guidance
   version, delivered prompt, dispatch record, and tool grants. Exercise summary
   and individual evidence reads, exact worktree/SHA binding, and normal inbox
   or review completion on authorized work.
5. After an unchanged-policy baseline, set each selected agent's Context policy
   to **Work lineage** in Admin Web. The API equivalent is
   `PATCH /api/addons/team-addon/agents/{agent_id}` with
   `context_clearing_policy: "work_lineage"` and freshly read `expected_revision`.
   Read back after each edit. On conflict, reload and reapply only the intended
   field. Preserve unrelated roles, models, project policy, and workflow settings.
6. Verify lineage switching and native conversation reuse as described above.
   Confirm supervisory counts do not become added task or project authority.

## Measure completed work

Compare completed items matched by work kind, size, repository, review/release
requirements, and model. Record baseline and pilot windows and sample sizes.
Count all billed usage, including cached input, output, extra reads, tool
requests, managed-guidance loading, fresh-context rebuilding, missing-thread
retries, ordinary retries, and rework.

For each item record time and cost to accepted completion, review outcomes,
reopened findings, and actual release approval/publication evidence. Include
blocked and abandoned work so fewer completions cannot appear as savings.
Report wake, retrieval, and history costs separately, then total item cost.
Correctness requires discoverable obligations, exact candidate/approved SHAs,
immutable execution scope, independent review, release gates, and correct
operator replies and acknowledgement. Stop the pilot on lost work, widened
scope, stale bindings, or a review/release bypass.

## Rollback

1. Pause scheduling and drain active or queued dispatches through supported controls.
2. Restore captured context policies and idle grace through revision-checked
   Admin controls, then read back. Switching into or out of `work_lineage`
   clears saved lineage-thread bindings and their owners. Never restore those
   maps or backfill tasks through direct database edits.
3. Before downgrading code, verify schema compatibility. Older writers may drop
   fields they do not understand. Leave `work_lineage` through supported controls
   before deploying code that predates native-receipt support, and preserve
   runtime evidence. Restart compatible gateway/runtime components together.
4. Resume scheduling and verify a completed dispatch, operator acknowledgement,
   review result, and any affected release gate. Keep pilot evidence; restoring
   code or policy does not erase durable Team work.
