# Tracked coverage and continuation

Tracked coverage is opt-in. It applies only to managed Git repositories, the
container-worker backend, Standard mode and full snapshots. The default
`coverage_policy: provider` preserves the existing single-review behavior,
including commit ranges and deep scans. Existing sessions and historical scan
records are not migrated.

## Workflow inputs and outputs

```json
{
  "session_id": "${{scanner.session_id}}",
  "repo_path": "${{clone.target_dir}}",
  "managed_repo_id": "${{selection.managed_repo_id}}",
  "coverage_policy": "all_tracked_files",
  "mode": "standard",
  "max_batch_text_bytes": 1048576,
  "max_parallel_reviews": 2,
  "timeout_seconds": 21600
}
```

`max_batch_text_bytes` accepts an integer from `1048576` (1 MiB) through
`2097152` (2 MiB), including either endpoint. It applies only to tracked
coverage. Omit it (or use null) to select 1 MiB for a new scan or inherit the
saved limit on continuation. The value can be a workflow binding, for example
`"max_batch_text_bytes": "${{event.payload.review_bytes}}"`, whose resolved
value must be an integer. Use `2097152` to request 2 MiB for this scan only.
The host freezes the effective budget before scheduling any child review.

`max_parallel_reviews` accepts an integer from `1` through `4` for tracked
coverage. Omit it (or use null) for two reviews on a new scan or to inherit the
saved limit on continuation. It supports workflow bindings such as
`"max_parallel_reviews": "${{event.payload.review_parallelism}}"`; the resolved
value must be an integer. File and boundary reviews share this limit.
Architecture mapping remains serial. Uncertain launches and jobs awaiting
report retention or cleanup occupy slots until reconciled, even after a host
restart. Historical journals without this field keep their two-review limit.
The host freezes the value before launching reviews; editing workflow defaults
does not resize an active run or change existing child generations. To change
it, use an explicit linked continuation after confirmed cleanup. This changes
neither assigned scopes nor accepted results, access routes or the run deadline.
The setting requires Evander 0.127.0 and add-on 0.4.0; older providers reject
explicit settings. It does not require a different SDK or scanner image.

Omitting the timeout selects 21,600 seconds for tracked coverage and 3,600 for
provider coverage. Tracked runs permit shorter budgets for bounded qualification,
but never more than six hours. Omit both commit inputs. Create scanner and
notification sessions with seven-hour lifetimes. The final five minutes are
reserved for stopping workers and consolidating reports. Each child review gets
at most one hour, clipped to the remaining review budget. Retries and quota waits
never extend the original deadline.

For an explicit linked continuation, core 0.128.0 and add-on 0.5.0 also accept:

```json
{
  "exclude_paths": ["vendor/legacy-installer.bin"],
  "rebuild_pending_batches": true,
  "incomplete_review_policy": "retry_scope"
}
```

Exclusions must be exact files in the pinned inventory. Core 0.129.0 and add-on
0.6.0 also allow exclusions on a new scan or after boundary planning on an explicit
continuation. Only pending scopes are partitioned; completed scopes stay accepted.
Exclusions are inherited
and cannot be removed within the same lineage. Excluded files receive no new
review credit; accepted findings, existing credit and immutable native reports
remain retained. Questions wholly owned by an excluded file or original excluded
review scope receive an explicit exclusion disposition. Questions that also
name non-excluded tracked files remain required.

Rebuilding repacks unfinished file scopes under the saved byte and file caps,
including earlier split work. The original journal and accepted evidence stay
unchanged. This is an explicit continuation action, not a live edit or a fresh
time budget. `retry_scope` retains incomplete scopes for at most three attempts;
the default remains `split`, and omitted policies inherit the previous setting.
Partial native headers alone never grant file credit; see the sealed source
evidence rules below for individual file credit in add-on 0.6.2 and later.

Core 0.129.0 and add-on 0.6.0 add pattern exclusions and persistent file isolation:

```json
{
  "exclude_patterns": ["*.pfx", "bin/*"],
  "incomplete_review_policy": "isolate_blockers"
}
```

Patterns are positive, case-insensitive globs resolved against the frozen tracked
inventory. A slashless pattern matches names at any depth; a pattern containing
`/` starts at the repository root. `*` stays within a path segment and `**` spans
directories: `**/bin/**` matches `bin` directories anywhere. Matched directories
include tracked descendants, and a trailing slash selects directories only. No
negation, absolute paths, backslashes or traversal are accepted. Reports retain
each pattern's exact matches, including zero-match patterns. Exact paths and
pattern matches are combined and inherited; a continuation cannot reintroduce
an excluded path. An all-excluded scan schedules no worker reviews and earns no
coverage. Exclusions remove scheduled review obligations, not access to supporting
repository context; architecture mapping still uses the full snapshot.

With `isolate_blockers`, exact assigned files named by sealed native
`deferred[].paths` or `file:<path>` follow-up surfaces enter a persistent blocked
queue with reasons and native archive references. A missing required non-text
disposition also identifies its own file. Paths guessed from prose, directories,
globs or out-of-scope references cannot identify a blocker. Files lacking positive
sealed review facts need a new review. The host keeps that remainder together
under the existing caps instead of repeatedly
halving it. If no exact blocker can be identified, the incomplete file scope
blocks for operator attention after one attempt.

On a continuation, diagnostics can be recovered from hash-checked retained native
reports for unfinished file scopes before any new review launches. Blocked files
remain parked through normal resumes and batch rebuilding. To retry specific
files after providing a password, decoder or other prerequisite, explicitly pass
`retry_blocked_paths: ["vendor/encrypted.bin"]` on a continuation. This does not
retry excluded files, satisfy dependent questions or grant coverage. Accepted
findings, original native archives, other-file coverage and completed boundary
scopes stay retained. Unresolved questions depending on blocked content remain
visible and unresolved outside ordinary boundary work. A boundary review that
reintroduces a known blocker is parked without repeated attempts.

Version-2 journals persist isolation and pattern controls. Version-1 sources can
be continued without rewriting them; older runtimes reject version-2 sources on
resume instead of silently losing quarantine state. Active scans keep their
frozen configuration. These host-side controls need no SDK or image change and
do not change the snapshot, execution route, byte limit, parallelism, cleanup
protocol, or run deadline.

### Checkpoint identity and component closure

File and boundary reviews share the same resolved exclusion set. Excluded files
are absent from both target queues and from the required path slices supplied
to component reviews. A native report may mention an approved exclusion outside
its assignment without invalidating otherwise complete coverage. An assigned
file omission or an unknown/unapproved exclusion remains blocking. Exact native
path fields are treated literally; only pattern fields use glob matching. The
host never upgrades a partial native report to complete.

Required checkpoints keep a stable host identity. Returning a saved ID updates
that checkpoint; a local ID remains stable across retries of the same task but
does not merge unrelated tasks. Continuations fold historical saved-ID echoes
into their canonical requirements, retaining alias references, native proofs and
each accepted closure's original scope. Native reports and source journals are
not rewritten. A legacy completion for one component cannot certify the rest of
a wider requirement.

For a checkpoint spanning several components, each boundary review receives only
its assigned paths and certifies only that part. The host retains the remaining
parts until all are explicitly resolved or excluded. Adding required paths
reopens global closure while preserving already verified parts. Unknown
nonempty references stay required for operator scope clarification; they cannot
be resolved by an unrelated component, including a legacy frozen worker.
Pathless checkpoints remain separate explicit obligations rather than inferred
file coverage.

Completed scopes remain completed. A genuinely unresolved checkpoint part can
need a separate bounded follow-up; this does not reset prior accepted scopes or
give already-failed scopes fresh automatic retry budgets. Split follow-ups create
real pending children for their current obligations without reusing historical
completed children. The existing byte, file, parallelism and deadline limits
still apply.

A continuation can also reuse a complete exact-scope native boundary proof that
was rejected solely for approved outside-scope exclusions, provided its local
checkpoint obligations are closed. The host verifies archive and coverage seals,
snapshot and scope, records the retained proof, and creates no synthetic worker
attempt. Partial reports, changed scopes and unresolved obligations do not
qualify. This reconciles the earlier false block without silently granting file
credit or bypassing source cleanup.

Finishing all non-excluded work produces `status: completed`, `success: true`
and `scope_complete: true`, with exact `excluded_paths` in the result. Overall
`coverage` remains `partial` when files are excluded; the original denominator
and individual unreviewed exclusions remain in the reports. The scan effect takes
its `completed` port. A policy requiring full repository coverage must separately
require `coverage: complete`. Reports list the requested exclusions and every file disposition. File
reviews receive no repository-wide outstanding queue; boundary reviews retain
the required questions assigned to them.

`coverage_progress` contains `phase`, `files_total`, `files_reviewed`,
`files_not_applicable`, `files_blocked`, `files_pending`, `files_excluded`, `active_reviews`,
`completed_reviews`, `attempts`, `max_batch_text_bytes`, `max_parallel_reviews`,
`files_isolated`, `isolated_files` (path, reason, native receipt and provider scan ID),
`exclude_patterns`, `incomplete_review_policy`, `deadline_at`, `last_progress_at` and
`cleanup_condition`. Waiting workflow executions refresh these outputs.
`required_questions_total`, `required_questions_pending` and
`required_question_aliases` distinguish the canonical required-work queue from
historical ID echoes; per-scope resolution receipts remain in `coverage.json`.
`cleanup_confirmed: false` means the repository reservation must remain held,
including when the scan itself is terminal. Failed scans and partial scans with
unfinished required work use the `error` port and can still provide findings and
an archive. Exclusions do not bypass required boundary reviews, unresolved
checkpoints, cancellation or time-budget stops.

For a submitted launch with no saved execution handle, the host uses the worker's
session-bound `POST /v1/jobs/{id}/cancel` protocol. The worker durably fences the
job identity against late admission and acknowledges cleanup only after any
existing container has been removed. A plain missing-job response is insufficient.
Deploy Evander 0.129.1 or later on both the control plane and worker to use this
protocol. Older workers, transport failures and failed cleanup retain the
reservation. Cancellation preserves session artifacts and never recreates the
missing launch. No SDK or scanner-image update is required.

The directory-scoped `query:security:scan` node takes `scan_id` and reads safe
metadata without polling or starting work. It returns `found`, repository ID,
revision, policy, status, progress, lineage, inventory digest, retained ref,
resumability and cleanup status. Unknown IDs and IDs outside the exact directory
return `found: false`. It exposes no logs, credentials, findings or private paths.
The node requires persisted workflow graph metadata.

## Coverage evidence

The trusted runner enumerates the pinned Git tree with NUL-delimited records.
The host checks every path, object ID, mode and size against the authorized managed
repository. No ignore rule removes a tracked file. Text classification streams
the content; binary files, symlinks and Git submodules remain in the inventory.

The host alone writes `runtime/codex-security-coverage/journal.sqlite3` in a
mode-0700 directory, with a mode-0600 database and staged requests. Containers
cannot mount it. The journal stores the inventory digest, exact revision,
pending scopes, generations, jobs, immutable proof hashes, outstanding work,
deadline and model/access route. SQLite transactions and per-scan process locks
serialize updates and repository claims across orchestrator restarts.

Scopes follow path order and pack across adjacent folders, containing at most
100 tracked entries and the scan's selected text budget (1 MiB by default).
Small binary files, symlinks and submodule entries share scopes while still
requiring their own explicit dispositions. Files larger than the selected
budget stay intact in singleton scopes. Known unavailable
entries are isolated so they do not obstruct otherwise reviewable batches.
A shared architecture review
precedes component work. Final boundary scopes use that map and outstanding
validation items. Every review uses an isolated SDK state directory and clone of
the same source commit, through the pinned public scoped Standard-scan API. Supporting
reads outside the assigned scope earn no file credit.

The pinned SDK follows symlinks in path targets. A scope containing symlinks
therefore uses a deterministic, temporary Git projection: only the assigned
links become regular JSON metadata files at their original paths. Each record
contains the original commit, mode, blob ID and exact target bytes. Targets are
read from Git objects, never by following the links, so broken, external,
cyclic and directory links can be assessed explicitly. Other tree entries remain
unchanged, and the projection commit has the original snapshot as its parent.
The source repository and the logical scan's revision and inventory stay unchanged.

The host independently reconstructs the expected projection from its authorized
managed repository without writing objects. Both the native revision and exact
scope must match, and every link still needs its own justified `file:<path>`
surface. Reviewing a destination gives no credit for the link. Unsupported or
unresolved link review remains blocked, as do link payloads over 1 MiB. Native
reports retain their derived revision unchanged; the aggregate manifest records the verified projection and
its original link identities. Projection receipts survive restart and resume
alongside the original snapshot, including after the default branch advances.
Deploy the rebuilt scanner image with the matching host code; the projection
runtime is included in the image cache fingerprint. Existing accepted receipts
and historical archives are unchanged.

Only a sealed native result matching the revision and exact scope can close a
scope. Complete native coverage must have no assigned-source exclusions or
unfinished source work. Native `openQuestions` are advisory: they remain visible
with their source receipts in the aggregate report and coverage ledger.

Core 0.129.2 and add-on 0.6.2 distinguish source coverage from vulnerability
validation. A justified terminal `file:<path>` surface in a sealed report can
certify that individual file even when the native report is partial. A structured
`deferred[].candidate` with a title, source and exact in-scope paths describes an
unverified hypothesis, not unread source. Its source obligation closes only after
all referenced files have accepted credit or explicit exclusions. Candidate
follow-up surfaces and external validation stay visible with an unverified status
and native receipts; they do not enter finding/severity counts. Unknown, pathless
or out-of-scope deferred references, unexplained partial results, contradictory
file follow-ups and actual omitted source cannot grant credit. The host keeps
certified files out of retries and queues only unfinished files. Binary/symlink/submodule
dispositions also need explicit source-backed notes in `file:<path>` coverage surfaces. Unavailable
content remains blocked. Text cannot be dismissed as not applicable. Boundary
results must explicitly resolve previously retained work using `deferred:<key>`
surfaces. A partial boundary report can instead classify a checkpoint as external
validation only with a justified terminal `review:boundary` surface, accepted
file coverage for the assigned component, and a `validation:<exact saved key>`
follow-up explaining the missing evidence. Genuine remaining source work keeps
the component unfinished. The classification closes only that component's part;
all other parts remain required. Architecture mapping itself earns no file coverage.

`validation_questions_pending` counts canonical checkpoints with unverified
validation, including partially resolved boundary checkpoints. A checkpoint may
therefore also have required source work pending in another component. Aggregate
reports record `source_review_policy_version`, preserve native partial headers
unchanged, and clearly separate unverified limitations from findings. Journal
version 3 can continue retained version 1/2 scans after proof verification; older
runtimes cannot resume version-3 journals.

Each boundary scope owns a durable subset of outstanding items. Partial scopes
pass those assignments to their split children; one scope cannot consume retries
waiting for another scope's work. The run still requires all outstanding items
to be resolved before reporting complete coverage.

Partial multi-file scopes split in half. A leaf or transient execution gets at
most three attempts in a run (two retries), with short backoff. Quota failures
wait before retrying, still under that limit and the original deadline.
Authentication/policy failures stop the run. Blocked scopes do not prevent other
scopes from being reviewed. There are at most two active or uncertain child jobs
for a managed repository, across directories. An unconfirmed old job occupies a
slot until stopped. No account, model, Cyber program or billing fallback is used.

Before a child job is removed, its native reports are preserved and hash-checked,
then its proof is journaled. Duplicate/lost launch replies rejoin the saved job
identity and frozen request. Old generations cannot reopen completed work.
Earlier partial findings remain; exact native identities/root-control matches
are deduplicated while uncertain matches stay separate. If repeated exact
identities disagree on severity, the aggregate uses the highest retained severity
and preserves all source receipts. Unsealed interrupted artifacts are retained
separately without earning coverage credit.
Every retained artifact listed in the native seal is checked by its exact path
and hash after transport. Private artifacts stay excluded from public delivery.

## Resume and retention

An explicit continuation uses a **new** workflow request identity and adds
`resume_from_scan_id`. Query the source first, recheck repository/channel approval
in the operator workflow, create an isolated scanner session, and clone using
its `pinned_ref` and `to_commit` through `effect:agent:clone-repo`'s `pinned_ref`
and `pinned_commit_sha` inputs. Then invoke the same scan effect with tracked
coverage and the source scan ID. This works through the existing operator manual
workflow invocation surface as well as a Teams intake workflow; no new API or
schedule is needed. Repeated delivery of the same workflow event rejoins its run.

The controller verifies access, inventory, native archive hashes, policy/SDK
version and selected route before reusing accepted work. Active, complete,
incompatible or expired sources cannot resume. Legacy scans without a journal
require a new tracked scan. A continuation receives a fresh budget and linked
scan ID; the source report and artifacts remain immutable. It always reviews the
original commit even after the branch advances or is force-pushed.

### Explicit subscription handoff

By default, a continuation must use the source run's exact access route, including
its subscription profile. To authorize one different ChatGPT subscription,
configure **only the new isolated scanner session** with the destination profile
and pass its ID explicitly alongside `resume_from_scan_id`:

```json
{
  "type": "effect:security:scan-repo",
  "id": "resume-scan",
  "display_name": "Resume security scan",
  "session_id": "new-scanner-session",
  "repo_path": "/home/agent/repo",
  "coverage_policy": "all_tracked_files",
  "managed_repo_id": "approved-managed-repository",
  "resume_from_scan_id": "0123456789abcdef0123456789abcdef",
  "resume_subscription_profile_id": "subscription-b"
}
```

The input authorizes that named destination; it does not configure the session
or choose an available account. The operator must verify the destination profile,
account access and positive applicable quota before invocation. A named profile
may equal the original profile, but both routes must still use public ChatGPT
subscription authentication. API-authenticated or non-public source/destination
routes are rejected. The original commit, inventory digest, directory/repository
access, policy/SDK, model, reasoning effort, Cyber program, image and every other
route field remain subject to the existing equality checks.

The controller verifies retained proof hashes and source cleanup before creating
the new journal and snapshot pin. It carries forward accepted work and findings,
records source and destination profile IDs in the **new** journal and aggregate
manifest/ledger, and leaves earlier journals, native archives and reports intact.
The same request identity rejoins the same run after a lost response or restart;
changing its source or destination is an idempotency error. The repository claim
and two-review limit still apply. Once started, the destination route is frozen;
changing it stops the run and performs normal worker cleanup. There is no automatic
subscription switch, API fallback, quota reset or reset-credit operation.

This host-side option requires Evander 0.122.0 and add-on 0.2.0. Upgrade the core
and optional add-on together, then reload the workflow services. It changes no
pinned SDK or scanner-image runtime files, so this change alone does not require
a scanner image rebuild. Deployment-specific workflow bindings and monitoring
remain operator-owned. Configure their explicit destination and quota guards
through the revision-gated workflow Admin API after deployment.

A linked continuation inherits its source's saved byte limit unless an
explicit `max_batch_text_bytes` requests a new value. Version-1 and version-2
journals upgrade from 512 KiB to the 1 MiB default when the input is omitted;
version-3 journals use 1 MiB. A changed budget regroups only untouched pending
root file and boundary scopes in the new journal, including when lowering
a 2 MiB budget to 1 MiB.
Completed reviews, previously attempted scopes and split children keep their
assignments, including scopes with retained retry errors after a prior run reset
its attempt counters; file
credit, findings, native receipts and outstanding boundary work are preserved.
The new journal records `batching_version: 4` and the effective
`max_batch_text_bytes`, also included in progress, the aggregate coverage
ledger and manifest. Further resumes retain that plan and limit by default.
Changing the limit under the same request identity is rejected; select it only
when starting a new scan or explicit linked continuation after cleanup.
Source journals
and published archives are immutable, and an active scan keeps its existing
assignments until an explicit continuation after confirmed cleanup. Raising a
source constant alone does not resize a saved plan; active version-1 and
version-2 runs also retain their 512 KiB bound when planning later boundary scopes;
active version-3 runs retain 1 MiB.
The configurable input requires Evander 0.125.0 with add-on 0.3.0. Deploy both
host packages and reload the workflow services; no worker daemon deployment
or scanner-image rebuild is required for this setting alone. After deployment,
save the per-scan value through revision-gated Workflow Admin APIs. A larger
budget can reduce child-start overhead but increases per-review context and
retry cost; the 100-entry cap and evidence requirements are unchanged. Review
parallelism is controlled separately by the frozen `max_parallel_reviews`
setting, defaulting to two.
For a continuation within an
existing operator budget, pass a timeout clipped to that budget's remaining time;
regrouping does not authorize a new budget. It does not change the snapshot, selected model/access route (except an explicitly
requested subscription handoff) or review-completion requirements. A byte-budget
change alone does not alter the inherited review-parallelism limit.
Results accepted during cancellation remain completed, and partial scopes retain
their split children. On continuation, older scheduling flags cannot reopen an
already accepted architecture map or credited file scope. This reuses existing
host-owned evidence without granting additional file credit.

Older journals may have promoted advisory questions into required work. On
continuation, the controller verifies their preserved native reports and removes
only exact question-derived blockers with authenticated provenance; genuine
deferred work and ambiguous records remain required. The same repair applies to
active journals after an orchestrator restart. Separately, a continuation on
add-on 0.6.2 can recover positive per-file source facts from the last accepted,
hash-checked native proof of an unfinished scope. Unread files and unknown work
remain blocked. This creates no worker attempts and changes neither the source
journal nor any native archive. Earlier finished runs and published reports
remain unchanged.

If the saved evidence is missing or fails verification during this conversion,
the run stops with partial coverage and still stops and cleans up its workers.
An unreachable worker keeps the repository reservation until cleanup is confirmed.
The journal and remaining evidence are retained for operator investigation;
an aggregate archive cannot be published from unverifiable source reports.

Existing managed-Git retained snapshots protect that commit. Journals and pins
remain for seven days after the latest referencing run; active runs and their
ancestors are protected. The effect orchestrator performs retention maintenance
every five minutes. It releases pins with the expected commit before deleting
expired inactive journals. Published reports have their existing independent
retention; raw-log expiry never removes coverage evidence.

## Reports and rollout checks

The archive contains an **Evander aggregate report**, normalized findings,
per-file coverage ledger, inventory and manifest of source archive hashes. Native
canonical reports under `sources/` are unchanged; interrupted artifacts are
separate. This is not a native provider completion record. A complete result
means the requested static review covered every tracked entry and required
boundary scopes. It does not establish live deployment or exploit behavior.

Operator-owned delivery must attempt ZIP and inline PNG independently before
waking the summary agent. Show findings counts even for partial results, along
with blocked/pending counts and resume instructions. Keep raw runner/broker
diagnostics private. Release the workflow reservation only when terminal status
**and** confirmed cleanup are recorded. Reconcile uncertain cleanup by querying
the saved scan ID; do not clear a lock based on elapsed time alone.

Deploy the control plane, worker code, pinned runtime/profile and rebuilt scanner
image before enabling the policy in live workflows. The image fingerprint covers
both `coverage_runner.py` and `coverage_sdk.mjs`; unchanged inputs reuse it.
Batch packing and continuation regrouping execute on the control plane and are
not worker-image inputs; changes confined to those policies do not require a
scanner-image rebuild.
Use a disposable managed synthetic repository and the selected real subscription
for a bounded qualification after deployment: interrupt after accepted progress,
verify private diagnostics and native hashes, resume at the original SHA, and
confirm no third child or second logical repository scan starts. Stop active jobs
through the workflow cancellation surface and inspect `cleanup_confirmed`.

Only after qualification should the operator workflow be revision-gated through
Workflow Admin, validated against its fresh baseline, read back and checked in
both orchestrators. Preserve other graphs' layout and keep the longest execution
path under the configured depth limit. Iteration within the scan adds no graph
depth as repository size grows.
