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
  "timeout_seconds": 21600
}
```

Omitting the timeout selects 21,600 seconds for tracked coverage and 3,600 for
provider coverage. Tracked runs permit shorter budgets for bounded qualification,
but never more than six hours. Omit both commit inputs. Create scanner and
notification sessions with seven-hour lifetimes. The final five minutes are
reserved for stopping workers and consolidating reports. Each child review gets
at most one hour, clipped to the remaining review budget. Retries and quota waits
never extend the original deadline.

`coverage_progress` contains `phase`, `files_total`, `files_reviewed`,
`files_not_applicable`, `files_blocked`, `files_pending`, `active_reviews`,
`completed_reviews`, `attempts`, `deadline_at`, `last_progress_at` and
`cleanup_condition`. Waiting workflow executions refresh these outputs.
`cleanup_confirmed: false` means the repository reservation must remain held,
including when the scan itself is terminal. Failed/partial scans use the `error`
port and can still provide findings and an archive.

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
100 tracked entries and 1 MiB of text. Small binary files, symlinks and submodule
entries share scopes while still requiring their own explicit dispositions.
Files larger than 1 MiB stay intact in singleton scopes. Known unavailable
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
scope. It must report complete coverage with no exclusions, follow-up surfaces
or deferred work. Native `openQuestions` are advisory: they remain visible with
their source receipts in the aggregate report and coverage ledger, without
preventing an otherwise complete static review from closing. Partial coverage,
deferred work and `needs_follow_up` surfaces still prevent completion, even when
the report also contains advisory questions. Binary/symlink/submodule
dispositions also need explicit source-backed notes in `file:<path>` coverage surfaces. Unavailable
content remains blocked. Text cannot be dismissed as not applicable. Boundary
results must explicitly resolve previously retained work using `deferred:<key>`
surfaces. Architecture mapping itself earns no file coverage.

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

An explicit continuation of an earlier batching-policy journal (version 1 or 2)
regroups only unstarted root file and boundary scopes under the 1 MiB bound.
Completed reviews, previously attempted scopes and split children keep their
assignments, including scopes with retained retry errors after a prior run reset
its attempt counters; file
credit, findings, native receipts and outstanding boundary work are preserved.
The new journal records `batching_version: 3`, also included in its aggregate
coverage ledger and manifest. Further resumes retain that plan. Source journals
and published archives are immutable, and an active scan keeps its existing
assignments until an explicit continuation after confirmed cleanup. Raising a
source constant alone does not resize a saved plan; active version-1 and
version-2 runs also retain their 512 KiB bound when planning later boundary scopes.
For a continuation within an
existing operator budget, pass a timeout clipped to that budget's remaining time;
regrouping does not authorize a new budget. It does not change the snapshot, selected model/access route (except an explicitly
requested subscription handoff), per-run concurrency
limit or review-completion requirements.
Results accepted during cancellation remain completed, and partial scopes retain
their split children. On continuation, older scheduling flags cannot reopen an
already accepted architecture map or credited file scope. This reuses existing
host-owned evidence without granting additional file credit.

Older journals may have promoted advisory questions into required work. On
continuation, the controller verifies their preserved native reports and removes
only exact question-derived blockers with authenticated provenance; genuine
deferred work and ambiguous records remain required. The same repair applies to
active journals after an orchestrator restart. This does not grant file credit:
previously uncredited scopes still require a complete validated review. Earlier
finished runs and their published reports remain unchanged.

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
