# 22. Deliverables, Reviews, and Decision Cascades

> **At a glance.** Marking a task “done” is not enough to explain what was produced or why it was accepted. Team records a deliverable for each attempt, attaches the evidence, and asks a reviewer to decide against that exact submission and code version. If rework is needed, the next attempt becomes a new record so the earlier evidence and decision remain understandable.
>
> On a first read, remember three things: the producer submits a specific result, each reviewer decides only on that result, and corrections create a successor instead of rewriting history. The exact fields, artifact rules, Git checks, and command surfaces later in the chapter are reference detail for readers who operate or troubleshoot the delivery system.

## A review in plain language

Suppose the implementation agent finishes Northstar's telemetry-retry change. “Finished” is only the agent's claim. Team still needs a package that a reviewer can inspect: the exact code version, the tests that ran, any supporting artifacts, and an honest account of anything that could not be checked. That package is the **deliverable**.

A **review request** points to that deliverable rather than to the task in general. The reviewer can accept it, reject it with findings, or say that the review is blocked because the evidence or process is unavailable. If the producer corrects a defect, the correction becomes a new deliverable. The rejected attempt remains visible, so a later reader can understand what changed and why.

This gives an operator two explicit decision paths to follow:

```text
task -> submitted deliverable
     -> direct superior -> accepted result or a new rework deliverable
     -> formal review(s) -> accepted result, blocked review, or a new rework deliverable
```

The key question is always **“Which exact deliverable and code version did this decision cover?”**

## Evidence before opinion

Tasks describe intended work. Deliverables describe what was produced and how it was checked. Review requests bind a reviewer and policy to that evidence.

An operator will usually start with the summary, verification result, artifacts, code version, and current reviews. Those are enough to understand whether the work is ready, waiting, or returning for correction.

> <span class="sidebar-kicker">Technical sidebar</span> **What a deliverable can record**
>
> A deliverable can include:
>
> - a summary and producer;
> - stable artifact references;
> - a persistent Git work-context ID and derived agent branch/commit;
> - verification status, summary, and evidence refs;
> - structured outcome kinds, durable reference IDs, and outcome evidence refs;
> - the direct-acceptance recipient and that superior's immutable decision, when applicable;
> - supersession links;
> - review findings and state.
>
> Verification status is <code>passed</code>, <code>failed</code>, <code>partial</code>, <code>not_run</code>, or <code>unknown</code>. “Tests could not run because the integration service was unavailable” is <code>not_run</code> with a concrete explanation, not <code>passed</code>.

For Northstar, Imani's first telemetry retry deliverable might contain:

- the pinned commit from Imani's task worktree;
- unit and fault-injection test evidence;
- an artifact with the retry-state-machine trace;
- verification status <code>passed</code>;
- a concise summary of backoff, jitter, idempotency, and exhaustion behavior.

## Outcome contracts prevent report-only completion

Some tasks exist to coordinate or acknowledge work; others must create a durable external result.
Team records the distinction on the task instead of relying on wording in its description.

A <code>support</code> task can finish directly without inventing a deliverable. An
<code>outcome</code> task carries an <code>outcome_contract</code> that names required and
forbidden outcome kinds, chooses whether any or all required kinds must be present, and may
explicitly permit an evidence-backed no-op.

Deliverables for outcome tasks include typed outcomes. Each submitted outcome must carry durable
<code>reference_ids</code>; evidence refs can support that result but cannot replace its identity.
A review profile can require outcome kinds before the review is requested or accepted. When no new
external effect is correct, submit an <code>evidenced_noop</code> deliverable completion with
evidence refs, and only when the contract allows it. That submission still follows direct
acceptance or formal review; only acceptance copies the completion to the task. A prose report,
review, or evidence-only deliverable does not satisfy an outcome contract by itself.

## Composing an aggregate result

A parent task represents a combined outcome, not a label placed over unrelated
work. Before its final deliverable can be submitted, every descendant must be
validly resolved. The parent deliverable then names the exact accepted,
current, non-superseded deliverables incorporated from its direct children in
<code>source_deliverable_ids</code>.

Contribution coverage follows these rules:

- every resolved direct outcome child contributes its accepted deliverable,
  unless it resolved through an accepted evidenced no-op or authorized
  cancellation;
- a support child contributes an accepted assessable deliverable when it has
  one, but a support task that finished without output needs no artificial
  deliverable;
- every named source must belong to a direct child and be current and accepted;
- missing or unexpected direct-child sources make the parent not
  integration-ready; and
- if a child is itself aggregate, its accepted deliverable carries the next
  layer. Recursive lineage is derived rather than copying every leaf into the
  root.

The resulting parent Git commit or artifact bundle is the submitted object.
Source IDs prove which accepted components were integrated; they do not copy
child state or make a branch tip trustworthy. A release selects the accepted
top-level aggregate deliverable instead of releasing it alongside the sources
already contained within it. Independent root deliverables may still coexist
in one release.

## Artifact publication boundary

An artifact is supporting material that can be opened again after the temporary workspace is gone—for example, a trace, test report, screenshot, or generated package. Team stores a stable reference to published material; merely typing a reference does not copy the underlying bytes into artifact storage.

If a reviewer cannot retrieve an artifact, that is an evidence-access problem. It does not mean the artifact disproved the change.

> <span class="sidebar-kicker">Deeper detail</span> **Publication and reviewer imports**
>
> There are two different ways artifact information can appear:
>
> - <code>artifact_paths</code> asks the command adapter to publish safe paths from the current session's <code>/artifacts</code> tree and store the resulting stable refs;
> - <code>artifact_refs</code> or an artifact-space reference records already-published metadata.
>
> Supplying a ref does not publish bytes. Paths are validated as safe relative paths, and host publication is replay-idempotent. Publication occurs before the final Team state transaction because it crosses service boundaries. If publication succeeds but the Team mutation fails, the artifact may be orphaned until the same command is replayed or an operator cleans it up deliberately.
>
> For an ordinary task or inbox handoff, the recipient can call <code>team_import_artifact</code> with an <code>artifact-space:</code> ref. The host checks the calling <code>team-agent:&lt;id&gt;</code> read grant and imports the latest requested artifact version into that caller's own <code>/artifacts/team-artifact-imports/...</code> tree. A path under another agent's <code>/home/agent</code> or <code>/artifacts</code> tree is never a shared path.
>
> Reviewer wakes import referenced review artifacts to a bounded path below:
>
> <code>/artifacts/team-review-inputs/&lt;review&gt;/&lt;deliverable&gt;/...</code>
>
> The review context records successful imports and permission or retrieval errors. A reviewer should distinguish “evidence disproves the change” from “evidence could not be accessed.”

## Persistent Git work contexts

When the result includes code, Team does not trust a branch name typed into the submission. It checks that the code came from the task's prepared Git workspace and derives the accepted branch itself. This is what lets a reviewer return later to the same code version.

The branch remains internal evidence at this stage. Review does not publish it to the project's canonical upstream branch; Chapter 24 covers that later boundary.

> <span class="sidebar-kicker">Technical sidebar</span> **Persistent work-context checks**
>
> When a project uses managed Git, a persistent Team wake reuses the stable
> physical task/repository worktree path but creates a fresh dispatch-bound work
> context. The context binds:
>
> - agent, task, project, session, and dispatch;
> - deterministic agent branch;
> - prepared checkout/worktree;
> - base branch, base commit, and current verified commit;
> - status and timestamps.
>
> The base branch is copied from the task's durable snapshot and the first
> preparation pins its exact commit. Later dispatches reuse that commit even if
> the project default or remote branch advances.
>
> An explicitly Git-backed deliverable supplies
> <code>branch_context_id</code> and <code>git_refs.commit_sha</code>. Unknown Git
> reference fields are rejected. The server verifies caller, task, dispatch,
> context, current managed branch tip, and exact commit, then derives the
> accepted agent branch and pins the same submitted SHA for review.
>
> Omitting Git fields is not a way to hide changed code. If exactly one active
> task context exists, Team resolves its live branch tip. A tip different from
> the pinned base is automatically bound to the deliverable with the exact
> context, branch, and commit. If the tip still equals the base, an artifact-only
> result is legitimate. No active context also remains artifact-only. Multiple
> contexts, an unavailable base, or an unresolvable branch fails with an
> actionable correction instead of guessing. Historical accepted records
> without pinned provenance remain readable and immutable; any explicit rework
> replacement must satisfy the current rules.
>
> The branch is evidence inside the managed bare repository, not a canonical publication. Promotion begins only through the reviewed Git flows in Chapter 24.

### Immutable dependency composition

When a task declares dependencies or required contribution tasks, Team derives the Git inputs
from the accepted deliverables. A newly accepted reviewed commit is retained under an immutable
<code>refs/evander/team-reviews/...</code> ref. Later preparation resolves that retained ref, not
the mutable agent branch, so a subsequent push cannot change an already accepted input.

The prepared context records a dependency-composition manifest containing the pinned task base,
base tree, every source relationship, deliverable, immutable ref and commit, the ancestry-reduced
commit frontier, the deterministic source fingerprint, and the resulting composed commit and
tree. Required contributions are derived from the deliverable contract even when the planner did
not duplicate them as ordinary task dependencies. Redundant ancestor commits are recorded as
reduced sources rather than cherry-picked twice. A genuine content conflict still creates the
typed dependency-composition recovery; it never falls back to an unreviewed history merge.

These rules apply prospectively. Historical accepted deliverables remain readable and use their
legacy branch/context validation. A new reviewed acceptance records policy version 2 and must
satisfy the immutable-ref and composition evidence gates that apply to its task.

## Submitting, superior acceptance, and formal review

The producer submits the evidence, then chooses the handoff the task actually requires:

- <code>team_submit_deliverable</code> sends a deliverable notice to the producer's direct
  superior. The task exposes <code>waiting_reason: superior_acceptance</code>; this is not formal
  review work. That recorded recipient uses <code>team_decide_deliverable</code> to accept or
  reject the exact handoff and handles the notice in the same command.
- <code>team_submit_deliverable_for_review</code> submits the evidence and creates the intended
  formal review handoff in one command.
- <code>team_request_review</code> attaches a formal review to an existing deliverable.

A top-level agent with no direct superior cannot use the superior-acceptance form. It must use
formal review for a reviewable deliverable, or finish support/coordination work directly without
manufacturing one. Team prevents accidental duplicate active reviews of the same deliverable by
the same reviewer.

For a direct handoff, acceptance can cascade to task acceptance once every active deliverable is
accepted. Rejection requires a concrete reason, returns the task to <code>in_progress</code>, and
queues actionable rework for the owner. The superior can request specialist review instead, or
report a blocker when evidence or a dependency prevents a decision. Once a formal review is
attached, the direct decision command cannot bypass it; the assigned reviewer path becomes
authoritative.

A review may be requested by an authorized producer, supervisor, project lead, or Team manager. The assigned reviewer must be active, in the same Team, and compatible with any selected review profile.

An assigned reviewer must inspect the immutable work specification, not only
the deliverable summary. For a leaf, read the complete task description,
outcome and deliverable contracts, profile, implementation, artifacts, and
verification. For a composite task, also traverse the declared contribution
tasks, required current source deliverables, and recursively relevant ancestor
specifications and evidence. Complete contribution IDs prove coverage, not
correct integration. If any required task or evidence record is unavailable,
decide <code>blocked</code> and name the missing IDs instead of guessing.

Review profiles can apply by project and role, define required and optional criteria, and require
specific outcome kinds. Archived profiles cannot be assigned to new reviews. When more than one
profile matches a reviewer, delivered outcome kinds narrow the implicit choice; an ambiguous
remainder requires an explicit profile ID. Use profiles for repeatable policy such as:

| Criterion | Requirement for telemetry retry |
|---|---|
| Behavior | Bounded exponential backoff and idempotent retry are demonstrated. |
| Tests | Transient recovery and terminal exhaustion tests pass. |
| Observability | Attempt count and final failure are diagnosable without sensitive payloads. |
| Scope | Changed paths match the task and reviewed evidence. |

Creating the same active review for one deliverable and reviewer is replay-safe: the service returns the existing review rather than duplicating inbox and wake work.

### Pre-acceptance verification and evidence

New formal-review submissions for integration boundaries and system-created rejected-candidate
corrections run the project's frozen release verification commands in a disposable
<code>container_worker_vm</code> against the exact immutable review snapshot. This is a
server-owned, release-equivalent check; an agent-supplied verification claim cannot satisfy it.
The review can be inspected while the run is pending, but it cannot be accepted until the run has
passed and recorded a durable evidence reference.

If no release verification commands are configured, the result is <code>not_run</code> and the
acceptance remains closed. An operator can authorize a waiver only for <code>partial</code> or
<code>not_run</code>, must supply a reason, and has their authenticated operator identity and time
stored on the deliverable. A failed check cannot be waived; corrected reviewed content is
required. Policy-version-2 reviews also require an evidence reference for every required passed
criterion.

> <span class="sidebar-kicker">Technical sidebar</span> **Submission and automatic review creation**
>
> Both submission paths record evidence and change the task to
> <code>waiting_for_review</code>. The derived <code>waiting_reason</code> distinguishes
> <code>superior_acceptance</code> from <code>review</code>, so UI labels and manager routing do
> not treat an ordinary superior notice as an unassigned reviewer gap.
>
> Workflow authors can use <code>trigger:team:deliverable-created</code>.
> <code>effect:team:create-review-request</code> creates an independent review
> request when each request should retain its ordinary individual lifecycle. If
> the complete reviewer set must decide as one round, use one
> <code>effect:team:create-review-cohort</code>; it creates, seals, and queues
> every reviewer slot in one transaction. Exact retries are idempotent, while
> mismatched membership or pre-existing independent requests fail closed. The
> combined <code>team_submit_deliverable_for_review</code> command likewise
> atomically seals the review list supplied with a new deliverable. The trigger
> owns its cursor and suppresses old deliverables when first enabled, avoiding
> an accidental historical review storm.

## Review decisions

Only the assigned reviewer may decide a review. The decision is immutable.

“Immutable” means the decision is not edited later to improve the history. Rework creates another deliverable and another review, while the earlier decision remains evidence.

### Accepted

For a profiled review, every required criterion must be present and passed. Optional criteria may be omitted. The accepted decision pins its reviewed evidence and findings.

A deliverable becomes accepted only when:

- all current, non-retired attached reviews are accepted; and
- no carried review finding remains open.

When every current required deliverable for the task is accepted, every
descendant is resolved, and aggregate contribution coverage is complete, the
task can become accepted. Project status and goals then recalculate under their
own rules.

### Rejected

For an ordinary independent review, a rejection immediately:

- marks the reviewed deliverable rejected;
- returns the task to <code>in_progress</code>;
- creates a rework inbox item for the producer;
- preserves concrete findings.

For a sealed cohort, the review decision and findings are still persisted
immediately, but deliverable rejection and the consolidated rework notice wait
until every other active cohort slot is accepted or rejected. A pending or
blocked slot keeps the cohort unresolved as described below.

Imani does not edit the rejected deliverable after Rowan finds that jitter can exceed the deadline. Imani produces a new deliverable that explicitly supersedes the rejected one. Open findings are carried forward.

An accepted later review can resolve only findings owned by its same review profile. For legacy reviews without a profile, reviewer-role compatibility is used. One unrelated reviewer cannot silently close another policy's finding.

### Blocked

Use <code>blocked</code> when the review process or evidence access cannot proceed, not when the candidate is known to be defective. For an ordinary deliverable review, blocked state propagates blocking state to the deliverable/task and preserves a concrete blocker.

Release-review blocked behavior is stricter and retry-oriented; Chapter 24 distinguishes it from candidate rejection.

A blocked non-release review can be retried with <code>team_retry_review</code>. Retry preserves
the blocked attempt, links a new pending successor, reopens the deliverable, and resolves the
obsolete review blocker. Do not overwrite the original decision.

For a Git-backed retry, Team re-resolves the work context's authoritative agent
branch. The retry proceeds only when that tip still equals the one pinned
reviewed/submitted commit. A missing or inconsistent context, missing pinned
commit, or advanced branch requires a replacement deliverable; the immutable
review is never retargeted to new code.

An authorized requester, producer, supervisor, project lead, or manager can withdraw a mistaken
pending non-release review with <code>team_withdraw_review</code>. A project lead or manager can
retire a terminal non-release attempt with <code>team_retire_review</code>. Withdrawal records no
decision; both actions preserve history. Retirement preserves the decision and does not resolve or
waive open findings.

An ordinary rejected or blocked deliverable can be replaced by a same-task
rework submission. A sealed-cohort deliverable cannot be replaced while any
cohort slot is pending or blocked; settle the round or recover the exact
blocked slot first. When an accepted deliverable from another task is the
intentional replacement, an authorized project lead or manager can link it
explicitly with <code>team_link_deliverable_replacement</code>. Migration does
not guess replacement lineage.

When a current accepted deliverable later proves invalid, the task owner,
project lead, or same-Team manager uses
<code>team_reopen_task_for_rework</code> with that exact accepted deliverable
and a reason. The recorded task owner must still be active and a member of the
project's Team; otherwise the reopen fails before creating an obligation. The
original acceptance and reviews remain immutable. The task
returns to <code>in_progress</code>, its ancestor/project/release readiness is no
longer complete, and the original owner receives one high-priority rework
obligation. The next same-task deliverable must explicitly supersede that
accepted ID and repeat every current required review profile through fresh
formal review; direct finish, direct superior acceptance, unrelated
replacement, and cross-project or foreign deliverables cannot close the
rework. The original owner receives one high-priority item and targeted wake,
not a stream of duplicate obligations. If the accepted predecessor had a
sealed cohort, the replacement must create the complete fresh cohort described
below.

An unlocked operator has equivalent revision-checked <code>/withdraw</code> and
<code>/retire</code> routes. Release reviews continue through their release-gate recovery path.

## Independent reviews and sealed cohorts

Independent requests retain the ordinary lifecycle above: each decision is
immutable and a rejection can return the deliverable to rework while other
independent evidence remains visible.

A sealed cohort represents one coordinated review round. Suppose Northstar
requires Rowan's software-quality slot and Priya's operational-readiness slot:

1. Imani submits D1 with both reviewer slots, or a workflow atomically creates
   the complete cohort on existing D1.
2. Rowan may accept while Priya remains pending; D1 stays under review.
3. If Priya rejects, Team persists that decision but waits for every other
   active slot to settle before notifying Imani. One early rejection does not
   interrupt reviewers who are still producing evidence.
4. A blocked slot makes the cohort and task unresolved while the other
   reviewers may still decide. Recover that exact slot with a retry or
   authorized replacement; do not discard the rest of the cohort.
5. Once no active slot is pending or blocked, Team consolidates all rejected
   reviews and open findings into one rework notice.
6. Imani submits D2 superseding D1. D2 must atomically create a complete fresh
   cohort preserving every predecessor slot: review-profile multiplicity and
   each unprofiled reviewer identity.
7. The new cohort reviews D2's exact pinned commit and carried findings. Only
   complete acceptance can accept D2 and the task.

Membership is sealed: no late reviewer can be appended, two slots cannot be
collapsed onto one reviewer, and the final active member cannot simply be
withdrawn. An exact terminal-cohort workflow retry remains terminal; it does
not reopen accepted or rejected decisions. This cascade prevents “one approval
wins,” preserves the complete evidence round, and keeps rework replacement
from silently reducing required review coverage.

![Deliverable review and rework cascade](../../assets/evander-user-manual/diagrams/14-review-cascade.svg)

<p class="figure-caption">Figure 22.1 — Immutable decisions either advance accepted evidence or create an explicit rework successor.</p>

![Team Delivery view showing the telemetry-retry deliverable and review state](../../assets/evander-user-manual/screenshots/team-delivery.png)

<p class="figure-caption">Figure 22.2 — Delivery groups deliverables, review evidence, and decisions while keeping their durable identities visible. Sanitized Northstar fixture.</p>

## Review authorization and tool surface

The distinction operators should remember is that **seeing a review is not the same as being allowed to decide it**. A manager can inspect delivery state, but only the assigned reviewer records that review's decision. Release authority also does not authorize rewriting a deliverable review.

> <span class="sidebar-kicker">Technical reference</span> **Commands and integration surfaces**
>
> Ordinary Team command tools in this domain are:
>
> - <code>team_submit_deliverable</code>;
> - <code>team_submit_deliverable_for_review</code>;
> - <code>team_decide_deliverable</code> for the superior recorded on a direct handoff;
> - <code>team_import_artifact</code> for permission-checked ordinary artifact handoffs;
> - <code>team_request_review</code>;
> - <code>team_decide_review</code>;
> - <code>team_retry_review</code>;
> - <code>team_withdraw_review</code>;
> - <code>team_retire_review</code>;
> - <code>team_link_deliverable_replacement</code>;
> - <code>team_reopen_task_for_rework</code>;
> - delivery list/get tools;
> - Git promotion and release tools covered in Chapter 24.
>
> Admin routes provide revision-checked operator create/edit/decision paths and explicit review
> retirement. Workflow providers add deliverable-created triggering and deterministic
> review-request creation. All paths reuse the same repository invariants.

## Decision and state failures

| Symptom or error | Meaning | Recovery |
|---|---|---|
| Artifact path rejected | The requested path escapes the allowed artifact root or has invalid shape. | Publish from a safe relative path below the session artifact root. |
| Artifact ref exists but bytes are unavailable | Metadata was recorded without publication, permission/version access failed, or an ordinary recipient has not imported it yet. | Inspect the artifact-space record; for ordinary handoffs call <code>team_import_artifact</code>, and for reviews inspect the reviewer import error. |
| Git context mismatch | Agent/task/session/dispatch or derived branch does not match the submitted context. | Use the current task work context; never type an alternate branch. |
| Artifact-only submission gains Git provenance or is rejected | The single active task branch changed from its pinned base, or Team could not prove a safe unchanged/unique context. | Accept the automatic exact binding, or supply the named current context and commit; do not omit Git fields to hide code. |
| Reviewer cannot be assigned | Agent is inactive, cross-Team, or incompatible with the profile. | Select an active same-Team reviewer matching policy. |
| Accepted decision rejected by validation | A required criterion is missing/failed or findings remain open. | Record complete criterion results and resolve only authorized findings. |
| Second decision refused | Review decisions are immutable. | Create the appropriate successor deliverable/review; do not overwrite history. |
| Task stays waiting after one acceptance | Another active review or open carried finding remains. | Inspect the complete current deliverable review set. |
| Cohort rejection does not wake the producer yet | Another sealed-cohort slot is pending or blocked. | Let remaining reviewers decide and recover any blocked slot; rework is consolidated only after the cohort settles. |
| Sealed-cohort replacement is refused | The fresh review list omitted a predecessor profile slot or unprofiled reviewer identity, or attempted a direct-acceptance replacement. | Resubmit through <code>team_submit_deliverable_for_review</code> with the complete fresh cohort and exact predecessor ID. |
| Previously accepted result is now known invalid | Immutable acceptance cannot be edited in place. | Use <code>team_reopen_task_for_rework</code>; the original owner must submit an explicit formally reviewed replacement. |
| Rejected task has no useful next step | Findings were vague. | Preserve evidence and create concrete, testable findings in the review decision. |
| Outcome deliverable rejected before review | Its outcome kind is missing, forbidden, or lacks a durable reference ID. | Match the task contract and identify the actual external record; do not substitute prose or evidence-only output. |
| Evidenced no-op does not resolve the task | The contract disallows it, evidence refs are absent, or the submitted completion has not been accepted. | Correct the contract-compatible evidence and follow the normal acceptance/review path; do not mark the outcome task finished. |
| Aggregate deliverable reports missing or unexpected sources | Named <code>source_deliverable_ids</code> do not exactly cover current accepted direct-child contributions. | Read contribution coverage, resolve descendants, and integrate only the listed current child outputs. |
| Blocked review remains current after recovery | Immutable history needs an explicit successor. | Use <code>team_retry_review</code> with a recovery reason. |
| Retirement does not accept the deliverable | Another current review or carried finding still blocks acceptance. | Inspect the current review set and resolve findings through their owning profile. |
| Plain submission has no recipient | The producer has no direct superior for superior acceptance. | Use formal review, or finish support work directly when no deliverable is required. |

## Troubleshooting and handoff

In **Delivery**, begin with the task and identify its current deliverable. Then check every active review and every unresolved finding. Capture the task ID, deliverable ID, review IDs, verification status, artifact references, and reviewed commit. If an artifact cannot be opened, include the visible import or permission error rather than describing the candidate as failed.

Do not edit a terminal review or force the task state merely because one acceptance did not advance it. Another review or a carried finding may still be open. Give the captured identities and the relevant row from the failure table to a Team administrator or the responsible producer/reviewer.

## Producer, reviewer, and operator runbooks

### Producer

1. Confirm the task, active dispatch, and task-specific Git context.
2. Run proportionate verification and record honest status.
3. Publish artifacts from safe paths or attach existing stable refs.
4. For an outcome task, record the required outcome kinds and durable reference IDs.
5. Choose superior acceptance or formal review deliberately; use the combined submission command
   when formal review is already known.
6. Mark related inbox items handled atomically.

### Reviewer

1. Confirm the review targets the current deliverable and pinned commit.
2. Inspect imported artifacts and any access failures.
3. Apply every required profile criterion.
4. Record specific findings and decide once.
5. Use rejected for a product defect and blocked for an actual process/evidence blocker.

### Operator

1. In **Delivery**, trace task → current deliverable → all active reviews → findings.
2. Match each review to its profile, reviewer, evidence refs, and reviewed commit.
3. When a cascade looks stuck, inspect open findings before changing task state.
4. Retry, replace, or retire through the explicit recovery surface; do not rewrite a terminal
   attempt.
5. Reconcile orphaned published artifacts deliberately.

## Source trail

- <code>src/evander_team_addon/models.py</code> — task outcome contracts/completions plus deliverable, verification, review, profile, criterion, finding, and supersession models.
- <code>src/evander_team_addon/services/commands/deliverable_submission_commands.py</code> and <code>review_commands.py</code> — submission, request authority, deduplication, and immutable decisions.
- <code>src/evander_team_addon/services/commands/task_update_commands.py</code> — accepted-deliverable rework plus outcome-completion and blocked-source guards.
- <code>src/evander_team_addon/services/domain/deliverables.py</code>, <code>review.py</code>, and <code>git_work_contexts.py</code> — outcome validation, acceptance cascades, retries, findings, artifact/Git evidence, and context validation.
- <code>src/evander_team_addon/providers.py</code> — deliverable trigger, independent/cohort review effects, artifact import, and hierarchy-aware reviewer prompt projection.
- <code>src/evander_team_addon/mcp.py</code> and <code>api.py</code> — grouped delivery tools and operator routes.
- <code>docs/addons.md</code> — artifact publication, review profiles, rework findings, and release handoff.
- <code>tests/test_team_addon_mcp.py</code>, <code>tests/test_team_addon_workflow_primitives.py</code>, and <code>tests/test_team_addon_runtime.py</code> — submission, profile enforcement, duplicate review, cascade, and reviewer-input behavior.
