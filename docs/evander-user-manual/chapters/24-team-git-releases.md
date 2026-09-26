# 24. Team Git Promotion and Release Gates

> **At a glance.** A safe release publishes the exact code that passed its required gates. Team carries that version through preparation, verification, and review, then authorizes publication under a Git lease. Candidate failures normally create correction work; process failures can permit a retry. Operators also have explicit, audited exception controls described below. Every retry preserves the earlier evidence and pinned candidate.
>
> An operator can follow the release with four questions: What exact code was approved? What evidence and policy apply? Did each required gate pass? What exact version reached the canonical branch? The internal branch names, worktrees, leases, and record fields later in the chapter explain how Evander makes those answers reliable.

## The release promise

A branch name can move after review. A commit SHA cannot: it identifies one exact snapshot. Team therefore turns accepted deliverables into a fixed release candidate, checks that candidate, and publishes that same candidate only after its gates pass.

In plain language, the path is:

```text
accepted work -> fixed candidate -> enabled gates in configured order
              -> canonical publication of that same candidate
```

Internal review, feature, and release branches are preparation areas. The release package's canonical branch and published commit are the final publication evidence. Candidate defects normally require corrected, reviewed content. A worker timeout or inaccessible review evidence may justify a linked retry against the unchanged candidate; neither a retry request nor an internal branch proves publication.

## Cancelling an unwanted release

A directory owner can choose **Cancel release** on a release card or its human-review
details. The reason is optional. Cancellation permanently ends that attempt without
creating a blocker, assigning rework, or automatically requesting another release.
Outstanding reviews, verification runs, release-owned blockers and exclusive correction
tasks are closed together. Completed and shared work, candidate Git refs, findings and
earlier decisions remain available as history; cancellation does not claim a finding was fixed.

Cancellation is available before canonical publication is queued. Once a canonical push
has been queued or attempted, the action is unavailable because an external Git effect
may already have run. Cancel the current attempt when an older attempt has a successor.
Cancellation does not undo published code. A running isolated check may finish, but its
late result cannot resume the cancelled release or create correction work.

The authenticated operator API is `POST /release-packages/{release_package_id}/cancel`
under the Team add-on API prefix. Send the current `expected_revision` and an optional
`reason`. It requires `admin.owner` in the project's directory. Repeating cancellation
preserves the original audit record. A subsequent release must be explicitly requested;
the cancelled attempt cannot be retried or used as a successor's predecessor.

Cancellation is persisted in `team_release_packages.cancellation_json`. Upgrade
all Team processes together before using it; older binaries may not understand
the `cancelled` release/review states.

## The repository boundary

Team refers to a repository by its registered identity rather than by an arbitrary host folder. Every preparation branch stays inside the managed repository until the one canonical publication step.

> <span class="sidebar-kicker">Technical reference</span> **Repository identity and deterministic branches**
>
> A Team project stores only a core <code>managed_repo_id</code>, directory scope, optional base branch, and canonical release prefix. The central managed-Git registry owns the upstream identity and host bare repository. Team commands never accept a trusted host checkout path as repository authority.
>
> All Team branches before canonical publication live in that managed bare repository:
>
> | Stage | Deterministic shape | Purpose |
> |---|---|---|
> | Ephemeral agent | <code>agents/&lt;session&gt;/&lt;assignment&gt;</code> | Disposable worker output |
> | Review | <code>team/&lt;team&gt;/reviews/&lt;source-id&gt;</code> | Captured reviewed source |
> | Feature | <code>team/&lt;team&gt;/features/&lt;task&gt;-&lt;agent&gt;</code> | Consolidated accepted task work |
> | Internal release | <code>team/&lt;team&gt;/releases/&lt;release&gt;</code> | Consolidated candidate |
> | Canonical | default <code>releases/&lt;project&gt;/&lt;release&gt;</code> | The only Team branch pushed upstream |
>
> Branch validation rejects whitespace, control characters, colon, a leading slash, <code>..</code>, and a leading <code>refs/</code>. Operators may configure the canonical prefix, but it remains a validated plain branch name.

## Intent, effect, and evidence

Requesting a promotion and completing it are two different events. The first records what should happen; a background workflow performs the Git operation; a result record then says what actually happened. A successful request therefore does not by itself prove that Git changed.

> <span class="sidebar-kicker">Deeper detail</span> **Intent, effect, and promotion record**
>
> Every promotion is a durable <code>GitPromotionRecord</code>. It records promotion kind, source refs and pinned SHAs, expected commit, target branch, state, output, and error.
>
> The flow has three steps:
>
> 1. An authorized MCP/Admin command records promotion intent and durable outbox event.
> 2. The workflow pack invokes the relevant managed-Git effect.
> 3. <code>effect:team:record-git-promotion</code> attaches the actual success/failure and updates related Team evidence.
>
> The command's success means intent was recorded. The promotion record and effect output say whether Git succeeded.
>
> Managed-Git effects use operation-scoped worktrees and temporary refs. Target updates are compare-and-swap operations. Conflicts fail; Evander never auto-resolves them. Temporary resources are removed on success and failure.

## The promotion ladder

Each rung freezes evidence for the next one. The names below are useful when an operator follows a release record; the exact command and manifest behavior matters most to release administrators.

### Agent or ephemeral output → review

The first step captures the exact output that is ready to be reviewed. It does not publish that output upstream.

> <span class="sidebar-kicker">Technical detail</span> **Capturing the review ref**
>
> <code>team_publish_review_branch</code> captures a persistent deliverable's validated agent branch/commit or an accepted ephemeral assignment branch into a deterministic review ref. The actor must be the producer/parent, an authorized superior, project lead, or manager as applicable.

### Review → feature

Accepted results for one task are combined into a task-level feature branch. Only the exact commits accepted by review may enter it.

> <span class="sidebar-kicker">Technical detail</span> **Consolidating accepted review evidence**
>
> <code>team_update_feature_branch</code> consolidates accepted current deliverables for one task. Each source must match the commit actually accepted by review. The resulting reviewed manifest freezes:
>
> - base and result tree;
> - included commits;
> - changed paths;
> - source deliverable/review evidence.

### Feature → internal release

Accepted task results from one project are combined into the fixed release candidate.

> <span class="sidebar-kicker">Technical detail</span> **Freezing the internal release candidate**
>
> <code>team_request_release</code> selects accepted deliverables from one project. If needed, prerequisite feature promotions are created first. Release consolidation adopts a single dominant pinned commit directly or combines divergent pinned feature commits in one meaningful integration commit, then freezes the candidate SHA and tree.

### Internal release → approved handoff → optional canonical publication

After every required check and review passes, Team emits
<code>team.release.approved</code> for that fixed candidate. This durable handoff
does not depend on whether Team publishes a canonical branch.

The internal release branch is not a branch operators should test or advertise.
When automatic publication is enabled, the release package's
<code>canonical_branch</code> and <code>published_commit_sha</code> are the remote
publication truth. When it is disabled, <code>published_commit_sha</code> and
<code>published_at</code> remain empty and the approval event supplies the exact
candidate and internal source ref to the operator workflow.

> <span class="sidebar-kicker">Technical detail</span> **Publishing the exact candidate**
>
> Only after every gate passes may the workflow create the canonical-push
> promotion. It does so when
> <code>automatic_release_branch_publication</code> is enabled. Core
> <code>effect:git:push-branch</code> verifies the exact approved
> commit, requires an existing canonical destination to still equal the
> release's immutable canonical base commit, and applies a destination lease
> before pushing. A generated destination that does not exist yet is created
> only when that pinned base is an ancestor of the approved candidate, under an
> empty-ref lease. Correction successors keep this canonical base separate from
> their rejected-candidate merge base. A release based on another branch cannot
> carry that branch's pre-existing changes to this destination; deliberate
> cross-base work must be reviewed through an integration task based on the destination.
> <code>effect:team:mark-release-published</code> records canonical branch,
> published commit, timestamp, and accepted package state.

![Pinned Team Git promotion and release-gate flow](../../assets/evander-user-manual/diagrams/16-team-release.svg)

<p class="figure-caption">Figure 24.1 — Reviewed commits move through internal refs and immutable gates before approval and any configured canonical push.</p>

## Creating a release package

The release package is the sealed description of one candidate: what work it contains, what reviewers accepted, what changed, which checks must run, and who requested it. Later policy changes do not silently rewrite that package.

All selected deliverables must be accepted and belong to one project. Artifact-only releases are allowed without Git configuration. A Git release requires project Git configuration and an explicit release policy. An explicit policy with every gate disabled is the deliberate ungated configuration; a missing policy still blocks approval. Set <code>automatic_release_branch_publication</code> to <code>false</code> when an operator workflow should publish only its own named branch. The setting and managed-repository scope are frozen into the candidate manifest, so later project edits do not redirect an in-flight release or its publication identity.

Git inputs must also share one authoritative task base branch and exact base
commit. Team returns <code>mixed_git_base_branches</code> before creating a
package when they do not. To merge workstreams deliberately, create an explicit
integration task on the chosen base and release only its accepted integrated
deliverable. For coordinated work, that integration task is a fresh top-level
task: leave <code>parent_task_id</code> unset, link the coordination task through
<code>originating_task_id</code>, and declare the exact accepted source tasks as
dependencies and required contributions. A task based on <code>feature/foo</code> does not publish there by
implication. Agent release requests cannot override <code>canonical_branch</code>;
Team derives it from the project's canonical release branch policy. An
authenticated operator may explicitly select a different destination for an
exceptional manual release, which still uses exact-SHA, fast-forward, and
destination-lease protections.

The first release package remains an explicit authorized request. A successor
for post-release feedback also remains explicit and must name its feedback
case. The exception is system-generated correction work for a failed candidate
verification, rejected agent release review, or human
<code>changes_requested</code>: after its required correction deliverables are
accepted and reviewed, Team creates the successor automatically.

For a newly accepted ordinary root deliverable in a release-configured project,
Team records an initial-release disposition as <code>pending</code>. This closes
the coordination gap without making the initial release automatic. The project
lead or top-level Team manager must request a package or use
<code>team_decide_initial_release</code> to record <code>not_planned</code> with
a reason. Pending rows remain manager-fallback work even if their inbox notices
were handled. A fallback dispatch can batch only compatible deliverables and is
authorized for the exact IDs shown in its prompt. An authorized
<code>reopen</code> restores a not-planned row to pending while preserving the
reopen audit. Existing deliverables from before this mechanism stay untracked.

This prevents policy drift after request time from silently redefining what was approved.

> <span class="sidebar-kicker">Technical reference</span> **Request authority and frozen manifest**
>
> The caller must be:
>
> - the project lead;
> - the top-level Team manager; or
> - an agent whose same-project role grants <code>request_release</code>.
>
> The release package freezes a typed manifest containing:
>
> - deliverable IDs, verification status, evidence refs, and waiver records;
> - accepted immutable deliverable reviews;
> - reviewed change manifests;
> - candidate commit and tree;
> - task/release base branch and exact base commit;
> - scope-validation result;
> - ordered enabled/disabled release-gate sequence;
> - ordered verification commands;
> - release-review requirements;
> - optional human reviewer groups, checklist, test instructions, and
>   preparation mode;
> - predecessor release, correction deliverables, contribution graph, and
>   carried human findings when this is a correction successor;
> - actor and timestamp evidence.

## Verification evidence

Deliverable verification status and evidence are advisory diagnostics. A report can be
<code>passed</code>, <code>failed</code>, <code>partial</code>, <code>not_run</code>, or
<code>unknown</code> without blocking acceptance or package intake. The release candidate's
server-executed command results are authoritative and run after candidate construction. Historical
waiver records remain visible for audit only; new release decisions never use them.

## Release policy

The project policy says how the fixed candidate will be checked and who must review it. Automated verification, agent review, and human review may each be enabled or disabled and may run in any order. An explicit all-disabled policy is valid.

> <span class="sidebar-kicker">Technical sidebar</span> **Release-policy fields and verifier behavior**
>
> <code>Project.release_policy</code> selects:
>
> - <code>container_worker_vm</code> verification backend;
> - optional container image;
> - optional <code>resource_limits</code> with a Docker memory string,
>   positive CPU count, and positive integer <code>pids_limit</code>;
> - <code>gate_sequence</code>, containing each gate exactly once with an
>   <code>enabled</code> flag, in execution order;
> - ordered commands, each with name, working directory, argv, timeout, optional
>   result protocol, and failure action;
> - workspace recovery-generation and retained-log limits;
> - required reviewer/profile pairs;
> - optional human-review requirement with Admin reviewer groups, checklist,
>   manual or workflow-prepared test instructions, and directory scope.
>
> Settings for a disabled stage remain stored for later reuse. New policies use
> agent review, automated verification, then human review by default. Policies
> without <code>gate_sequence</code> retain the historical verification,
> agent-review, then human-review order. The legacy <code>allow_ungated</code>
> input remains readable and upgrades to an explicit all-disabled sequence.
>
> The verifier creates a disposable worker session, clones by
> <code>managed_repo_id</code>, checks out the exact candidate SHA, runs
> commands in order, records bounded results, and destroys the session. Agent
> release reviewers decide that same candidate SHA under their profiles. When
> human review is configured, its group resolution and instructions are
> snapshotted into the candidate manifest rather than reread as mutable policy
> during the decision.

When a new release reaches agent review, Team first validates the complete
reviewer/profile set frozen in the manifest and seals those slots as one cohort.
No reviewer handoff is created unless the whole set is valid. Decisions remain
independent, but correction does not begin on the first rejection: Team waits
until every current cohort slot is accepted or rejected, then creates at most
one correction task and wake containing all rejected findings in manifest
order. If all reviewers accept, the release advances. A blocked review pauses
the cohort until a retry replaces the same frozen slot against the same
candidate. Review rows created before cohort support are not backfilled and
retain their legacy eager-rejection behavior.

If <code>resource_limits</code> is omitted, a Team release verifier uses the
worker/host CPU and memory defaults plus a 2,048 process/thread limit. A
configured field overrides its corresponding default, and only an explicitly
configured CPU value creates a Docker CPU quota. The image and resource limits
are snapshotted from current project policy when a verification attempt is
created. Restart recovery preserves that attempt's settings; an authorized
retry creates another attempt with the then-current settings. Older nonterminal
attempts without a settings snapshot capture it once before resuming. Candidate
commands and review requirements remain frozen in the release manifest.

Worker inspection reports <code>pids_limit_exhausted</code> from cgroup
<code>pids.events</code> and <code>memory_limit_exhausted</code> from Docker's
OOM-killed state. Those structured causes are infrastructure failures, not
strings inferred from test output, and enter the evidence-gated retry path.

### Command outcomes, restart recovery, and logs

Commands default to <code>result_protocol: "exit_code"</code> and
<code>command_failure_action: "correction"</code>. Set the failure action to
<code>"block"</code> when a failed command should wait for operator assessment
instead of opening candidate correction work. Only an operator can retry such
a command-blocked gate.

The opt-in <code>json-v1</code> protocol lets a project-owned evaluator report
<code>passed</code>, <code>baseline_exception</code>, <code>failed</code>, or
<code>infrastructure</code> for the frozen candidate and base. Baseline exceptions
also require explicit policy permission and evidence references. A timeout,
nonzero evaluator exit, malformed report, or mismatched identity cannot be
turned into success by a positive report. The UI distinguishes a permitted
baseline exception from a clean pass. See the
[verifier protocol and evidence contract](../../release-recovery-and-verifier-policy.md#structured-evidence-opt-in)
before configuring an evaluator.

An interrupted command sequence restarts in a new disposable workspace and
reruns every prerequisite. Earlier command passes cannot skip setup in a fresh
checkout. The frozen <code>verification_max_generations</code> defaults to 3;
exhaustion or failed cleanup blocks on infrastructure. Completed evidence and
earlier generation results remain inspectable.

Command output and local structured evidence are retained before cleanup in
<code>runtime/release-verification-logs</code>, available through release details
and the <code>execution logs</code> CLI. The manifest freezes retention, defaulting
to 30 days, with bounded logs and best-effort secret redaction. Logs are not a
complete live heartbeat or a permanent archive.

## Scope validation

Feature promotion records the exact reviewed commits and changed paths. Release consolidation computes the candidate's changes and compares them with the union of reviewed manifests.

An extra commit or path outside that union is a hard blocker. It cannot be waived. This catches accidental merge content and deliberate attempts to smuggle unreviewed scope into the candidate.

For Northstar, if the reviewed telemetry change covers <code>src/telemetry/retry.py</code> and its tests but the release candidate also changes an authentication policy file, the release stops even if all tests pass.

## Gate outcomes and retries

Candidate identity never changes within one release package.

The practical distinction is **content failure versus process failure**. Content failure means the candidate must change. Process failure may justify another attempt against the same candidate. Git-promotion recovery is a third, separate path.

### Terminal candidate failures

Under the default exit-code protocol and correction failure action, a nonzero
verification command rejects the candidate. A release-review decision of
<code>rejected</code> also records a candidate defect. For a sealed review cohort,
Team waits for every slot to reach <code>accepted</code> or <code>rejected</code>,
then creates one candidate-pinned correction containing all rejected findings.
Repair the content and complete its accepted, reviewed Git deliverables; Team
then creates one deterministic successor with the new reviewed tree. The project
lead does not make a second release request.

The review acceptance and successor intent are persisted atomically. Scheduled
maintenance applies the same idempotent reconciliation to interrupted or older
states and recognizes an existing direct successor instead of duplicating it.
If Team cannot validate or package the resolved correction, it opens a durable
blocker for the correction owner rather than waiting for a manager to remember
the release.

That blocker is a downstream release-recovery incident, not a reason to reopen
the completed correction task. It therefore does not invalidate the correction
or tasks that depend on it. In Admin Web, **Retry packaging** re-evaluates the
current evidence, and **Create verified replacement** creates dependency-free work
pinned to the accepted correction branch and exact commit. Verification-waiver
recovery is retired: corrected reviewed content must pass candidate verification.
Generic blocker resolution cannot bypass these actions. Reconciliation continues
while the incident remains open, prefers a current accepted downstream recovery
deliverable, and resolves the incident immediately when a direct successor exists.

The system refuses a cosmetic new release of the same failed deliverables and tree.

### Retryable process failures

Verification timeout or infrastructure failure can be retryable. A release review recorded <code>blocked</code> with a concrete process/evidence blocker can also be retried. <code>team_retry_release_gate</code> creates a linked immutable successor attempt against the same candidate SHA with reason and requester identity. It never edits the old run or review. In a sealed cohort, that successor occupies the same frozen reviewer/profile slot and the cohort remains paused until the blocker is replaced and every slot has a final content decision.

Retry is not polling. An agent should request it only after citing concrete
evidence that the infrastructure recovered and what changed. If an
agent-requested verification retry fails against the unchanged candidate, no
agent may schedule another verification retry in that chain; escalate the
current run and recovery requirement to an operator. An authorized operator
may decide whether another typed recovery is justified. This agent bound does
not change the separate blocked release-review retry rules.

Use <code>blocked</code> only when the review process cannot complete. Use <code>rejected</code> for an actual candidate defect.

### Operator-authorized retries

In **Projects → Releases** or **Delivery → Release history**, an operator can
choose **Retry verification gate** on an eligible rejected verification run.
Use **Show rejected** if the project list hides it. The action requires unlocked
privileged operations and a comment. It creates an immutable replacement run
against the same candidate and frozen commands, preserves the failed result, and
cancels unused generated verification correction work. It refuses a release with
a successor or correction work that already has submitted deliverables, linked
tasks, or active execution. Agents cannot invoke this exception.

The API sends <code>allow_rejected_verification_retry: true</code> with the current
gate ID and Team revision. The separate **Retry rejected review by exception**
action uses <code>allow_rejected_review_exception: true</code>; it replaces the
selected immutable review slot under operator authority and preserves other
rejections and their correction obligations. Neither action accepts the release
or skips remaining gates. A rejected-review exception instructs the replacement
reviewer to treat the previous rejected finding as explicitly excepted, reevaluate
every other required criterion, and report new defects. Use ordinary correction
for a confirmed content defect.

### Git recovery

<code>team_recover_release</code> is different. It recovers failed prerequisite feature or internal release Git promotions. It is not a verification or review-gate retry mechanism.

Legacy nonterminal packages without current immutable candidate evidence are rejected with <code>legacy_unpinned_release</code> and must be recreated. Historical terminal records remain readable.

## One ordered release-gate sequencer

Every gate callback returns to the same idempotent sequencer. For the frozen
manifest it checks:

1. candidate identity, hierarchy, aggregate contribution coverage, and reviewed
   scope;
2. each enabled stage in <code>gate_sequence</code>, in its configured order;
   and
3. final approval and canonical publication.

The sequencer creates only the next missing stage and stops while that stage is
pending or blocked. A disabled stage is skipped while its settings remain in the
project policy; no separate callback may jump ahead. In particular,
<code>approved_commit_sha</code> is not set while an applicable human request
is preparing, pending, blocked, or changes-requested.

| Gate result | What happens to the pinned release attempt |
|---|---|
| Verification candidate failure | Terminal rejection; generated correction work requires changed, reviewed content, then Team creates its successor automatically. |
| Verification command failure with <code>command_failure_action="block"</code> | Candidate remains blocked for operator assessment; only an operator may retry. |
| Verification infrastructure failure or timeout | Same attempt is blocked and may retry the unchanged candidate only after concrete recovery evidence; a failed agent-requested retry requires operator escalation. |
| Agent review rejection | Candidate failure is recorded immediately. A sealed cohort waits for all other slots, then generates one correction from every rejection; legacy uncohorted reviews keep their eager behavior. |
| Agent review blocked | Same candidate stays blocked until the typed review recovery runs. |
| Preview preparation failure or expiry | Same human request and candidate are blocked; preparation can rerun. |
| Human <code>blocked</code> | Same request and candidate remain recoverable without product changes. |
| Human <code>changes_requested</code> | Attempt ends without publication and correction work is created atomically. |
| Human <code>approved</code> | Carried findings are confirmed, final approval passes, and publication may be queued once. |

## Authenticated human release review

The human request is created when the sequencer reaches the enabled human-review
stage, after every preceding enabled gate in the frozen order has passed. Human
review may therefore precede verification or agent review. It freezes the release
ID, candidate SHA and tree, Admin directory, reviewer groups, initially eligible users, checklist,
test instructions, and preparation mode.

Manual mode creates a <code>pending</code> request. Workflow mode creates a
<code>preparing</code> request and one idempotent preparation event. If the
snapshotted groups have no eligible current users, Team still preserves the
same request and candidate in <code>blocked</code> and opens the canonical
assignment-unavailable blocker. It never skips the gate or creates a new
release merely because assignment failed.

A human decision requires all of the following:

- a real authenticated Admin Web session, not client-supplied actor fields;
- directory-scoped <code>team.human_reviews.decide</code>;
- current assignment plus continued group membership, or an explicit audited
  assignment;
- privileged-operation confirmation;
- the current request revision and a stable idempotency key; and
- candidate SHA/tree equality with the frozen release.

One eligible <code>approved</code> decision passes the initial policy.
<code>changes_requested</code> immediately vetoes the attempt, and
<code>blocked</code> is nonterminal. Concurrent or late decisions cannot change
a terminal result. Agent MCP reads expose compact gate state and next action,
but no MCP mutation can approve, reject, or unblock a human request.

The Admin surface is:

```text
GET  /api/addons/team-addon/human-review-requests?assigned_to=me
GET  /api/addons/team-addon/human-review-requests/<id>
GET  /api/addons/team-addon/human-review-requests/<id>/review-bundle
POST /api/addons/team-addon/human-review-requests/<id>/decisions
POST /api/addons/team-addon/human-review-requests/<id>/reassign
POST /api/addons/team-addon/human-review-requests/<id>/resume
```

Use **My Human Reviews** and the release detail to compare the candidate,
verification, agent decisions, contribution graph, test target, checklist,
carried findings, and publication state before deciding.

For a remotely hosted control plane, **Download review bundle** downloads a
source-only ZIP of that same pinned candidate. It contains no Git history or
credentials; `EVANDER-REVIEW.json` records the request, release, immutable ref,
commit, tree, test instructions, and release manifest. Unpack it locally and
run the repository's normal setup, such as `dotnet restore` and `npm ci` where
applicable. The export fails rather than silently substituting a moved release
ref.

The authorized release detail also includes **Test this candidate**. It shows
the canonical repository, immutable internal release ref, exact candidate
commit and tree, and a copyable supported command:

<pre><code>evander-cli team checkout-release-candidate &lt;release-package-id&gt; \
  --target ./release-candidate-review \
  --expected-commit-sha &lt;candidate-sha&gt; \
  --runtime-dir /absolute/path/to/evander/runtime</code></pre>

The command resolves the private managed repository inside Evander, verifies
the ref, commit, and tree again, and creates a new detached checkout at the
chosen target. The absolute runtime root keeps the copied command independent
of the current working directory; it refuses existing targets and never
exposes the internal bare repository path.

### Blocked review, reassignment, and preview preparation

A human <code>blocked</code> decision, assignment loss, preview-preparation
failure, or expired target opens or reuses a
<code>TeamBlocker</code> sourced from the human request. The candidate and
request remain unchanged. Project-lead, Team, governance, operator, and
directory-owner routes are selected according to the reason; a downstream
waiting task does not receive a duplicate blocker.

At request creation Team resolves the frozen group and directory requirement
against current Admin membership, active users, and directory-scoped
<code>team.human_reviews.decide</code> permission. It does not trust the
policy-time eligibility list. An empty result routes accountable recovery to
directory-scoped <code>admin.owner</code>, while the project lead retains
read-only visibility of the blocker and its history.

An authorized reassignment increments <code>assignment_generation</code>,
reconciles current eligibility, resolves the assignment blocker, and returns
the same request to <code>pending</code> or <code>preparing</code>. Resuming a
manual testing blocker returns the same request to <code>pending</code>.
Resuming workflow preparation increments
<code>preparation_generation</code> and emits a fresh idempotent request.

The preparation workflow receives only request ID, candidate SHA/tree, and
generation. Its authenticated result may store a URL or alias, expiry,
instructions, and evidence refs. Secret-bearing fields are rejected; preview
credentials remain in host-owned workflow/provider state. URL userinfo and
secret-bearing query or fragment parameters are rejected as credentials too.

### Changes requested and successor closure

<code>changes_requested</code> is one atomic Team transition. It records the
human decision and actionable findings, ends the immutable release attempt,
guarantees no canonical publication event exists, creates or reuses one
correction case, creates one coordination-only parent and one immutable-text
child task per finding, links each finding to its child, and creates
deduplicated inbox/wake obligations.

Accepted historical tasks, deliverables, reviews, and candidate content are not
reopened. Each new finding explicitly selects either the entire release
candidate or one or more named candidate deliverables. A single-owner
deliverable selection routes to that task's current owner and records the
original task as provenance. The entire candidate, a multi-owner selection, or
a selection whose historical owner is unavailable routes directly to the
project lead without waiting for a second routing decision. Only scope-less
legacy findings can retain ambiguous-routing metadata. The coordination parent
groups the response but has no combined deliverable or agent-authored
integration branch of its own.

Each finding child starts from the verified rejected candidate and must produce
exactly one current accepted correction deliverable with required review
evidence. Team then derives the finding's resolution summary and evidence and
automatically creates one successor with
<code>supersedes_release_package_id</code>. The successor:

- uses the predecessor candidate as its baseline;
- preserves the original accepted top-level scope;
- adds accepted correction deliverables and recursive contribution lineage;
- carries every still-open human finding with proposed resolution and evidence;
- requires a different candidate tree; and
- runs its own frozen enabled gates in order.

If that successor disables the stage that rejected its predecessor, Team blocks
publication until a directory owner records an audited reason tied to the
predecessor, gate, and new candidate SHA. The waiver remains visible on the
release package and does not alter the predecessor's immutable rejection.

The platform, not the coordination-parent owner, performs the Git aggregation.
It orders the correction sources deterministically by finding and deliverable,
pins every accepted reviewed source SHA, verifies that every source descends
from the rejected candidate, and merges them once into
<code>team/&lt;team&gt;/corrections/&lt;successor&gt;</code> with the rejected
candidate as the exact base. The separate finding deliverables and reviews
remain the evidence records; they do not become independent merges against
<code>main</code>.

After that correction aggregate succeeds, Team performs one final release
promotion using the predecessor candidate workstream recorded on the successor
release. It never rereads the project's mutable default. That integration result
becomes the candidate before verification and review gates begin. A
correction-to-correction conflict and a later aggregate-to-release conflict are
therefore two distinct, serial package-level recovery boundaries. Recovery
reuses the failed boundary's pinned branch and commit and never fans the package
back out into per-finding promotions. The predecessor's explicit canonical
publication destination is preserved on the successor.

The coordination parent finishes when that successor exists. The new human
approval must explicitly confirm every carried finding ID. Only then do those
findings become resolved and publication become eligible. A changed candidate
never reuses the predecessor human request.

### Publication and post-release feedback

Approval always emits <code>team.release.approved</code> with the candidate,
approved SHA, internal source ref, project/repository scope, and publication
policy. With automatic publication disabled, the package becomes accepted at
that point without claiming a canonical push occurred. Custom packaging should
trigger from this event.

When automatic canonical publication is enabled, final publication requires the candidate, approved, and published SHA to be
identical and a linked succeeded <code>canonical_push</code> promotion from the
release branch to the configured canonical branch. The human request must be
approved against the same candidate SHA/tree, with one durable approval
decision and every carried finding confirmed.

After publication, **Report regression or adjustment** creates or reuses a
deterministic post-release case and one project-lead triage root under the
authenticated reporter. It preserves the published package unchanged. Triage
may close an invalid report with evidence, create independent correction
roots, request a separately controlled rollback, or later authorize a linked
successor. Reporting alone never rolls back, mutates the package, or creates a
successor release. A later successor request must explicitly name both the
published `supersedes_release_package_id` and the still-open linked
`post_release_feedback_case_id`; only current accepted correction contributions
from resolved tasks in that case may extend the predecessor candidate.

## Northstar release trace

1. Rowan accepts Imani's current telemetry deliverable and pinned commit.
2. Priya promotes its review evidence into the task feature branch.
3. Riley requests release <code>telemetry-retry-2026-07</code>.
4. The package freezes deliverables, reviews, reviewed paths, commands, required release reviewers, candidate SHA, and tree.
5. A disposable verification worker checks out that SHA and runs unit and integration commands.
6. Scope validation confirms no unreviewed paths entered consolidation.
7. Required release reviews accept the same SHA.
8. Team emits <code>team.release.approved</code> for the exact candidate.
9. Because this project enables automatic publication, canonical push publishes it to the configured <code>releases/mission-control/...</code> branch under a destination lease.
10. The accepted release package records the canonical branch and published SHA.

If integration verification times out, Riley may request one evidence-backed
audited retry against the same SHA after the worker recovers. If that
agent-requested attempt also fails for infrastructure reasons, Riley must
escalate it rather than retry again. If a command identifies a content defect,
Imani completes the generated correction with reviewed changes. An exceptional
operator retry preserves the failed evidence and must still pass all gates.

## Complete App walkthrough: guarded line-speed control

The following representative manufacturing flow combines task hierarchy,
composition, durable dispatch, human testing, correction lineage, exact-SHA
publication, and cleanup. The IDs are illustrative, but every transition maps
to the supported records described in Chapters 18–24.

1. A plant engineer reports that Packaging Line 4 can accept a line-speed
   setpoint above the commissioned limit. Team creates case
   <code>App-482</code> and an outcome root
   <code>guard-line-speed</code>, owned by the App project lead. The contract
   requires a reviewed software change and operator-verification evidence.
2. The lead decomposes three levels: PLC/domain validation, API enforcement,
   operator UI, automated tests, operating instructions, and a self-owned
   integration child. Parent edges mean constituent work. The UI child depends
   on the domain contract. A separately discovered historian optimization is a
   new root with <code>originating_task_id=guard-line-speed</code>, not a child.
3. The scheduler computes one immutable dispatch per agent. Each coding task is
   shown with project, managed repository, stable task worktree, deterministic
   branch, base commit, and a fresh dispatch-bound context. A preparation
   failure fails the complete dispatch rather than omitting a repository.
4. Leaf owners submit pinned deliverables. The documentation support task
   finishes directly. A hardware-lab probe that correctly finds no configured
   exception submits an evidence-backed no-op only because its outcome
   contract permits it; a reviewer accepts that no-op.
5. The integration child accepts the leaf contributions. The root deliverable
   names the exact accepted direct-child <code>source_deliverable_ids</code>;
   Team derives the recursive graph and rejects missing, unexpected, stale, or
   superseded sources.
6. Specialist reviews accept the root's current combined deliverable. The
   release package freezes its candidate SHA/tree, reviewed paths,
   contribution graph, verification commands, agent reviewers, human reviewer
   groups, checklist, and manual or preview instructions.
7. A disposable worker checks out the pinned candidate and passes domain, API,
   UI, and integration verification. Required agent release reviews accept the
   same SHA.
8. Workflow preview preparation creates a short-lived Line 4 simulation target
   without returning credentials to Team state. The assigned plant engineer
   opens **My Human Reviews**, verifies limit enforcement, audit messages, and
   operator recovery, then chooses **Request changes** because the HMI warning
   does not identify the commissioned limit.
9. The decision atomically ends the attempt without publication, records the
   finding, creates one human-review correction case and coordination parent,
   creates one exact-text HMI correction child with provenance to the UI task,
   and queues its derived owner. The accepted original work remains immutable.
10. The correction child starts from the verified rejected candidate. Its
    reviewed correction deliverable is accepted, Team derives the finding's
    proposed resolution and evidence, and Team automatically creates one
    successor from the predecessor release and candidate baseline. The new tree
    must differ, while original scope, corrections, and the carried finding
    remain visible.
11. The successor reruns automated and agent gates. Its preview cannot start
    because the simulator allocation is temporarily unavailable, so the same
    human request and candidate become <code>blocked</code>. An operator repairs
    capacity and resumes that request; Team increments the preparation
    generation rather than creating another release.
12. The plant engineer tests the replacement target, explicitly confirms the
    carried finding, and approves. Team resolves the finding and queues one
    canonical push. Remote readback, the succeeded promotion, and the release
    package all show the same approved SHA.
13. Resolved task checkouts become cleanup candidates. Clean expected
    worktrees are reclaimed and audited. A worktree containing an unexpected
    calibration note remains <code>recovery_required</code> with its exact
    path and failed safety check; no broad delete discards it.
14. A later operator report that the warning should be localized creates a
    post-release case and triage root only. The published App release stays
    immutable, and rollback or a further successor requires a separate
    controlled decision.

## Failure patterns

| Failure | Correct interpretation |
|---|---|
| Source branch moved after review | Promotion uses the pinned reviewed commit if it remains contained; a moving tip is not silently substituted. |
| Merge conflict | Deterministic consolidation failed; Evander does not auto-resolve. Create reviewed conflict-resolution content. |
| Compare-and-swap target mismatch | Another operation advanced the target. Re-read promotion truth and decide whether a new intent is valid. |
| Missing release policy | Git publication is blocked unless explicitly ungated. Configure policy; do not bypass the gate. |
| Unreviewed changed path | Hard scope failure, never waiver-eligible. Rebuild from reviewed content. |
| Verification command nonzero | Under the default correction policy, the candidate is rejected. Complete the generated correction with accepted, reviewed content, or use an eligible operator-authorized retry. A command configured to block instead waits for operator assessment. |
| Verification infrastructure error, including structured PID or memory exhaustion | Potentially retryable immutable successor run only after concrete recovery evidence. After an agent-requested retry fails, further agent retry is blocked and operator recovery is required. |
| Release review rejected | Terminal candidate defect. For a sealed cohort, wait until all slots decide, then complete the single correction containing every rejection; Team creates the successor automatically without a manager release request. |
| Resolved generated correction has no successor | Reconciliation must either create or recognize one direct successor. Inspect the durable <code>candidate_correction_successor_failed</code> blocker; do not ask the manager to submit a release request. |
| Release review blocked | Retryable only with a concrete process/evidence blocker. |
| Human assignment unavailable | Same request and candidate stay blocked. Repair group membership or use audited reassignment; do not skip the gate. |
| Human preview preparation failed or expired | Same request and candidate stay blocked. Resume to prepare a new generation after fixing the target. |
| Human requested changes | Predecessor attempt is terminal and cannot publish. Complete the generated correction finding children with accepted, reviewed deliverables; Team creates the changed successor automatically. |
| Successor omits or silently closes a human finding | Candidate is not publishable. Carry every finding and require explicit confirmation in the successor approval. |
| Post-release issue was reported | This is intake, not rollback or a new release. Follow the case and triage root. |
| Release package says accepted but upstream seems different | Compare canonical branch/published SHA, promotion record, core push effect, and remote ref. Do not inspect only the internal release branch. |

## Troubleshooting and handoff

Start with the release package in Team Delivery. Capture its ID, predecessor or
successor ID, state, candidate SHA and tree, scope-validation result,
verification run, agent release reviews, human request/revision/assignment,
carried finding IDs, prerequisite promotion IDs, and any canonical branch or
published SHA. State whether the failure is a candidate defect, a
process/evidence problem, human assignment/testing, or Git promotion; the
correct recovery differs for each.

If the package says accepted but the upstream result looks wrong, do not test or advertise the internal release branch. Hand the package's <code>canonical_branch</code> and <code>published_commit_sha</code>, the canonical-push promotion, and the visible remote mismatch to a release administrator.

For confirmed content defects, complete the generated correction with reviewed
changes and let Team create its successor. Use the explicit operator retry
controls only when another attempt against unchanged content is justified;
preserve the reason and inspect every resulting gate.

## Release runbook

Before release:

1. Confirm every selected deliverable is current, accepted, and in one project.
2. Verify each Git source resolves to the pinned reviewed commit.
3. Inspect reviewed manifests and expected path union.
4. Treat agent-reported verification as diagnostic context. Do not create a waiver; the frozen
   candidate receives a fresh visible server-owned verification run after the package is created.
5. Validate the project release policy, worker image/commands, agent reviewers,
   and any human reviewer groups/checklist/preparation instructions.

During release:

1. Track release package, prerequisite promotions, candidate SHA/tree,
   verification run, agent reviews, human request/preparation/decision, and
   scope validation.
2. For a sealed agent-review cohort, let every unblocked slot finish before
   expecting correction work; retry a blocked slot against the same candidate.
3. Treat outbox acceptance and effect completion as separate facts.
4. Retry only typed retryable failures through
   <code>team_retry_release_gate</code>, cite concrete infrastructure recovery,
   and stop agent retries after an agent-requested attempt fails.
5. Use <code>team_recover_release</code> only for eligible Git promotion failure.
6. For a blocked human request, recover or reassign the same request; for
   changes requested, follow the generated case and let Team create the changed
   successor after every finding child has accepted, reviewed work.
7. For failed candidate verification or a closed cohort with rejected agent
   reviews, complete the generated correction task and let Team create its
   successor; do not hand a release-request obligation to the project lead.

After release:

1. Confirm the package's frozen automatic-publication setting and exact approved SHA.
2. If automatic publication is disabled, verify the approval-triggered workflow published only its named destination branch; empty <code>published_commit_sha</code> and <code>published_at</code> are expected.
3. If automatic publication is enabled, match <code>canonical_branch</code>, <code>published_commit_sha</code>, and <code>published_at</code> to the successful canonical-push promotion and core Git effect, then verify the remote ref equals the approved SHA.
4. Verify the approved human request matches the same SHA/tree and explicitly
   confirms every carried finding.
5. Preserve the immutable manifest and all gate attempts as release evidence.
6. Route later feedback through the post-release report action, not direct
   release mutation.

## Source trail

- <code>src/evander_team_addon/git_naming.py</code> — deterministic internal and canonical branch names and validation.
- <code>src/evander_team_addon/services/commands/git_release.py</code> and <code>services/git_release_service.py</code> — promotion/release authority, intent, recovery, and state transitions.
- <code>src/evander_team_addon/services/domain/git_promotion.py</code>, <code>release_packages.py</code>, and <code>release_gates.py</code> — pinned sources, manifests, scope, verification, reviews, and retries.
- <code>src/evander_team_addon/services/release_verification_runner.py</code>, <code>src/evander/effects/providers/agent_session.py</code>, and <code>src/evander/worker/docker_runtime.py</code> — live verifier limits, disposable sessions, and structured resource-exhaustion evidence.
- <code>src/evander_team_addon/services/domain/human_reviews.py</code> and <code>human_review_projection.py</code> — candidate-pinned human requests, identity-safe decisions, preview preparation, findings, successors, feedback intake, and bounded read views.
- <code>src/evander_team_addon/providers.py</code> — publish, consolidate, verify, record, and mark-published effects.
- <code>src/evander_team_addon/workflow_packs.py</code> — review, feature, release, verification, and canonical-push routes.
- <code>src/evander/admin/services/managed_git_service.py</code> and core Git effects — managed bare repositories, operation worktrees, leases, compare-and-swap, and remote push.
- <code>docs/addons.md</code> and <code>docs/git-remotes.md</code> — Team release and core managed-Git contracts.
- <code>tests/test_team_addon_git_effects.py</code>, <code>tests/test_team_addon_mcp.py</code>, and <code>tests/test_team_addon_runtime.py</code> — promotion, scope, gates, retry, recovery, and publication behavior.

### Task commit authors

Set **Commit author name** and **Commit author email** on a task to attribute its Git
commits to the delegating user. The `authored_by` identity is copied to child and
follow-up tasks and to corrections of that work. With no task identity, the project
author is used. Aggregate corrections involving different authors also use the project
default. Clearing the two task fields restores that fallback. Changes apply to future
commits after the task next resumes, and do not rewrite history or existing descendants.
