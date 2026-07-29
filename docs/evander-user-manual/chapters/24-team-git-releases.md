# 24. Team Git Promotion and Release Gates

> **At a glance.** A safe release must publish the exact code that passed review, not whatever a branch happens to contain later. Team carries that exact version through its internal preparation and verification steps, then gives the final publication a narrow, time-bounded authority. If the code itself fails verification, it must change and be reviewed again; a retry is reserved for failures in the surrounding infrastructure or process.
>
> An operator can follow the release with four questions: What exact code was approved? What evidence and policy apply? Did each required gate pass? What exact version reached the canonical branch? The internal branch names, worktrees, leases, and record fields later in the chapter explain how Evander makes those answers reliable.

## The release promise

A branch name can move after review. A commit SHA cannot: it identifies one exact snapshot. Team therefore turns accepted deliverables into a fixed release candidate, checks that candidate, and publishes that same candidate only after its gates pass.

In plain language, the path is:

```text
accepted work -> fixed candidate -> verification and release review
              -> authenticated human review when policy requires it
              -> canonical publication of that same candidate
```

Internal review, feature, and release branches are preparation areas. The release package's canonical branch and published commit are the final publication evidence. If content fails, it must be corrected and reviewed as new content. If the candidate is sound but a test worker times out or a reviewer cannot access evidence, a linked retry may run against the unchanged candidate.

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
> <code>team_request_release</code> selects accepted deliverables from one project. If needed, prerequisite feature promotions are created first. Release consolidation merges pinned feature commits into the internal release branch and freezes the candidate SHA and tree.

### Internal release → canonical

After every required check and review passes, that fixed candidate becomes the one canonical publication.

The internal release branch is not the published branch operators should test or advertise. The release package's <code>canonical_branch</code> and <code>published_commit_sha</code> are the publication truth.

> <span class="sidebar-kicker">Technical detail</span> **Publishing the exact candidate**
>
> Only after every gate passes does the workflow create the canonical-push promotion. Core <code>effect:git:push-branch</code> performs expected-commit and destination-lease preflight, then pushes the approved release commit to the canonical branch. <code>effect:team:mark-release-published</code> records canonical branch, published commit, timestamp, and accepted package state.

![Pinned Team Git promotion and release-gate flow](../../assets/evander-user-manual/diagrams/16-team-release.svg)

<p class="figure-caption">Figure 24.1 — Reviewed commits move through internal refs and immutable gates before the sole canonical push.</p>

## Creating a release package

The release package is the sealed description of one candidate: what work it contains, what reviewers accepted, what changed, which checks must run, and who requested it. Later policy changes do not silently rewrite that package.

All selected deliverables must be accepted and belong to one project. Artifact-only releases are allowed without Git configuration. A Git release requires project Git configuration and an explicit release policy unless <code>allow_ungated=true</code> was deliberately configured.

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
> - scope-validation result;
> - ordered verification commands;
> - release-review requirements;
> - optional human reviewer groups, checklist, test instructions, and
>   preparation mode;
> - predecessor release, correction deliverables, contribution graph, and
>   carried human findings when this is a correction successor;
> - actor and timestamp evidence.

## Verification evidence and waivers

Deliverable verification is enforced at release time:

| Status | Release rule |
|---|---|
| <code>passed</code> | Eligible without a verification waiver. |
| <code>failed</code> | Cannot be waived. Fix, re-review, and submit new evidence. |
| missing, <code>partial</code>, <code>not_run</code>, or <code>unknown</code> | Requires a reasoned waiver from project lead, Team manager, or authenticated operator. |

A role with <code>request_release</code> alone cannot grant the waiver. Each waiver records deliverable, original status, reason, actor kind and ID, and timestamp.

A waiver acknowledges incomplete verification evidence; it does not waive reviewed-scope mismatch.

In everyday terms, “we could not run this check” may be acknowledged by an authorized person with a reason; “this check failed” may not. A waiver never permits extra, unreviewed content.

## Release policy

The project policy says how the fixed candidate will be checked and who must review it. Unless the project was deliberately configured for ungated release, at least one command or review requirement is mandatory.

> <span class="sidebar-kicker">Technical sidebar</span> **Release-policy fields and verifier behavior**
>
> <code>Project.release_policy</code> selects:
>
> - <code>container_worker_vm</code> verification backend;
> - optional container image;
> - optional <code>resource_limits</code> with a Docker memory string,
>   positive CPU count, and positive integer <code>pids_limit</code>;
> - ordered commands, each with name, working directory, argv, and timeout;
> - required reviewer/profile pairs;
> - optional human-review requirement with Admin reviewer groups, checklist,
>   manual or workflow-prepared test instructions, and directory scope;
> - or explicit <code>allow_ungated=true</code>.
>
> Without <code>allow_ungated</code>, at least one verification command, agent
> review requirement, or human-review requirement is required.
>
> The verifier creates a disposable worker session, clones by
> <code>managed_repo_id</code>, checks out the exact candidate SHA, runs
> commands in order, records bounded results, and destroys the session. Agent
> release reviewers decide that same candidate SHA under their profiles. When
> human review is configured, its group resolution and instructions are
> snapshotted into the candidate manifest rather than reread as mutable policy
> during the decision.

If <code>resource_limits</code> is omitted, a Team release verifier defaults to
8 CPUs and 2,048 processes/threads; memory uses the worker/host default. A
configured field overrides its corresponding default. These limits are read
from the live project policy for each disposable verifier session, including a
retry; unlike candidate commands and review requirements, they are not frozen
candidate evidence.

Worker inspection reports <code>pids_limit_exhausted</code> from cgroup
<code>pids.events</code> and <code>memory_limit_exhausted</code> from Docker's
OOM-killed state. Those structured causes are infrastructure failures, not
strings inferred from test output, and enter the evidence-gated retry path.

## Scope validation

Feature promotion records the exact reviewed commits and changed paths. Release consolidation computes the candidate's changes and compares them with the union of reviewed manifests.

An extra commit or path outside that union is a hard blocker. It cannot be waived. This catches accidental merge content and deliberate attempts to smuggle unreviewed scope into the candidate.

For Northstar, if the reviewed telemetry change covers <code>src/telemetry/retry.py</code> and its tests but the release candidate also changes an authentication policy file, the release stops even if all tests pass.

## Gate outcomes and retries

Candidate identity never changes within one release package.

The practical distinction is **content failure versus process failure**. Content failure means the candidate must change. Process failure may justify another attempt against the same candidate. Git-promotion recovery is a third, separate path.

### Terminal candidate failures

A verification command returning nonzero means the candidate failed. The release is rejected. A release-review decision of <code>rejected</code> also means the candidate is defective. Repair the content, create and accept new deliverables, and request a new release with a new reviewed tree.

The system refuses a cosmetic new release of the same failed deliverables and tree.

### Retryable process failures

Verification timeout or infrastructure failure can be retryable. A release review recorded <code>blocked</code> with a concrete process/evidence blocker can also be retried. <code>team_retry_release_gate</code> creates a linked immutable successor attempt against the same candidate SHA with reason and requester identity. It never edits the old run or review.

Retry is not polling. An agent should request it only after citing concrete
evidence that the infrastructure recovered and what changed. If an
agent-requested verification retry fails against the unchanged candidate, no
agent may schedule another verification retry in that chain; escalate the
current run and recovery requirement to an operator. An authorized operator
may decide whether another typed recovery is justified. This agent bound does
not change the separate blocked release-review retry rules.

Use <code>blocked</code> only when the review process cannot complete. Use <code>rejected</code> for an actual candidate defect.

### Git recovery

<code>team_recover_release</code> is different. It recovers failed prerequisite feature or internal release Git promotions. It is not a verification or review-gate retry mechanism.

Legacy nonterminal packages without current immutable candidate evidence are rejected with <code>legacy_unpinned_release</code> and must be recreated. Historical terminal records remain readable.

## One ordered release-gate sequencer

Every gate callback returns to the same idempotent sequencer. For the frozen
manifest it checks, in order:

1. candidate identity, hierarchy, aggregate contribution coverage, and reviewed
   scope;
2. automated verification;
3. every current agent release review;
4. human test-target preparation when configured;
5. authenticated human review when required; and
6. final approval and canonical publication.

The sequencer creates only the next missing stage and stops while that stage is
pending or blocked. A project that does not configure one of the stages skips
it; no separate callback may jump ahead. In particular,
<code>approved_commit_sha</code> is not set while an applicable human request
is preparing, pending, blocked, or changes-requested.

| Gate result | What happens to the pinned release attempt |
|---|---|
| Verification candidate failure | Terminal rejection; changed, reviewed content is required. |
| Verification infrastructure failure or timeout | Same attempt is blocked and may retry the unchanged candidate only after concrete recovery evidence; a failed agent-requested retry requires operator escalation. |
| Agent review rejection | Terminal candidate failure under existing immutable-review rules. |
| Agent review blocked | Same candidate stays blocked until the typed review recovery runs. |
| Preview preparation failure or expiry | Same human request and candidate are blocked; preparation can rerun. |
| Human <code>blocked</code> | Same request and candidate remain recoverable without product changes. |
| Human <code>changes_requested</code> | Attempt ends without publication and correction work is created atomically. |
| Human <code>approved</code> | Carried findings are confirmed, final approval passes, and publication may be queued once. |

## Authenticated human release review

The human request is created only after verification and all required agent
release reviews have accepted. It freezes the release ID, candidate SHA and
tree, Admin directory, reviewer groups, initially eligible users, checklist,
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
POST /api/addons/team-addon/human-review-requests/<id>/decisions
POST /api/addons/team-addon/human-review-requests/<id>/reassign
POST /api/addons/team-addon/human-review-requests/<id>/resume
```

Use **My Human Reviews** and the release detail to compare the candidate,
verification, agent decisions, contribution graph, test target, checklist,
carried findings, and publication state before deciding.

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
correction case, creates independent correction or project-lead triage roots,
links each finding to its current correction task, and creates deduplicated
inbox/wake obligations.

Accepted historical tasks, deliverables, reviews, and candidate content are not
reopened. A finding that clearly targets one deliverable routes to that task's
current owner and records the original task as provenance. Ambiguous or
release-wide findings share a project-lead triage root. The correction case
groups the response; use parent/child containment only when the correction
itself has a genuine combined outcome.

After correction/triage tasks resolve and their current deliverables are
accepted, request a successor with
<code>supersedes_release_package_id</code>. The successor:

- uses the predecessor candidate as its baseline;
- preserves the original accepted top-level scope;
- adds accepted correction deliverables and recursive contribution lineage;
- carries every still-open human finding with proposed resolution and evidence;
- requires a different candidate tree; and
- reruns verification, agent review, and human review.

The new human approval must explicitly confirm every carried finding ID. Only
then do those findings become resolved and publication become eligible. A
changed candidate never reuses the predecessor human request.

### Publication and post-release feedback

Final publication requires the candidate, approved, and published SHA to be
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
8. Canonical push publishes it to the configured <code>releases/mission-control/...</code> branch under a destination lease.
9. The accepted release package records the canonical branch and published SHA.

If integration verification times out, Riley may request one evidence-backed
audited retry against the same SHA after the worker recovers. If that
agent-requested attempt also fails for infrastructure reasons, Riley must
escalate it rather than retry again. If a command exits nonzero, Imani must
change and re-review the content; no one can retry it into approval.

## Complete APP walkthrough: guarded line-speed control

The following representative manufacturing flow combines task hierarchy,
composition, durable dispatch, human testing, correction lineage, exact-SHA
publication, and cleanup. The IDs are illustrative, but every transition maps
to the supported records described in Chapters 18–24.

1. A plant engineer reports that Packaging Line 4 can accept a line-speed
   setpoint above the commissioned limit. Team creates case
   <code>APP-482</code> and an outcome root
   <code>guard-line-speed</code>, owned by the APP project lead. The contract
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
   finding, creates one human-review correction case, creates an independent
   HMI correction root with provenance to the UI task, and queues its owner.
   The accepted original work remains immutable.
10. The correction root is decomposed, its new contributions are accepted, and
    the finding receives a proposed resolution plus evidence. The lead requests
    a successor from the predecessor release and candidate baseline. The new
    tree must differ, while original scope, corrections, and the carried
    finding remain visible.
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
    post-release case and triage root only. The published APP release stays
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
| Verification command nonzero | Terminal candidate defect. New content and release required. |
| Verification infrastructure error, including structured PID or memory exhaustion | Potentially retryable immutable successor run only after concrete recovery evidence. After an agent-requested retry fails, further agent retry is blocked and operator recovery is required. |
| Release review rejected | Terminal candidate defect. |
| Release review blocked | Retryable only with a concrete process/evidence blocker. |
| Human assignment unavailable | Same request and candidate stay blocked. Repair group membership or use audited reassignment; do not skip the gate. |
| Human preview preparation failed or expired | Same request and candidate stay blocked. Resume to prepare a new generation after fixing the target. |
| Human requested changes | Predecessor attempt is terminal and cannot publish. Complete the generated correction case/tasks and request a changed successor. |
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

Never turn a failed test or rejected review into a retry merely to preserve the same package. New content requires new reviewed deliverables and a new release.

## Release runbook

Before release:

1. Confirm every selected deliverable is current, accepted, and in one project.
2. Verify each Git source resolves to the pinned reviewed commit.
3. Inspect reviewed manifests and expected path union.
4. Resolve incomplete verification with an authorized, reasoned waiver or new evidence.
5. Validate the project release policy, worker image/commands, agent reviewers,
   and any human reviewer groups/checklist/preparation instructions.

During release:

1. Track release package, prerequisite promotions, candidate SHA/tree,
   verification run, agent reviews, human request/preparation/decision, and
   scope validation.
2. Treat outbox acceptance and effect completion as separate facts.
3. Retry only typed retryable failures through
   <code>team_retry_release_gate</code>, cite concrete infrastructure recovery,
   and stop agent retries after an agent-requested attempt fails.
4. Use <code>team_recover_release</code> only for eligible Git promotion failure.
5. For a blocked human request, recover or reassign the same request; for
   changes requested, follow the generated case and request a changed successor.

After release:

1. Read <code>canonical_branch</code>, <code>published_commit_sha</code>, and <code>published_at</code> from the package.
2. Match them to the successful canonical-push promotion and core Git effect.
3. Verify the remote ref equals the approved SHA.
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
