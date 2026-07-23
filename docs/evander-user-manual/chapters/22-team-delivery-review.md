# 22. Deliverables, Reviews, and Decision Cascades

> **At a glance.** Marking a task “done” is not enough to explain what was produced or why it was accepted. Team records a deliverable for each attempt, attaches the evidence, and asks a reviewer to decide against that exact submission and code version. If rework is needed, the next attempt becomes a new record so the earlier evidence and decision remain understandable.
>
> On a first read, remember three things: the producer submits a specific result, each reviewer decides only on that result, and corrections create a successor instead of rewriting history. The exact fields, artifact rules, Git checks, and command surfaces later in the chapter are reference detail for readers who operate or troubleshoot the delivery system.

## A review in plain language

Suppose the implementation agent finishes Northstar's telemetry-retry change. “Finished” is only the agent's claim. Team still needs a package that a reviewer can inspect: the exact code version, the tests that ran, any supporting artifacts, and an honest account of anything that could not be checked. That package is the **deliverable**.

A **review request** points to that deliverable rather than to the task in general. The reviewer can accept it, reject it with findings, or say that the review is blocked because the evidence or process is unavailable. If the producer corrects a defect, the correction becomes a new deliverable. The rejected attempt remains visible, so a later reader can understand what changed and why.

This gives an operator a simple path to follow:

```text
task -> submitted deliverable -> one or more reviews
     -> accepted result, blocked review, or a new rework deliverable
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
> When a project uses managed Git, a persistent Team wake creates or reuses one task-specific work context and worktree. The context binds:
>
> - agent, task, project, session, and dispatch;
> - deterministic agent branch;
> - prepared checkout/worktree;
> - status and timestamps.
>
> A Git deliverable must submit its <code>branch_context_id</code>. The server verifies the caller, task, dispatch, and context and derives the accepted agent branch. An agent cannot substitute an arbitrary branch string. Artifact-only deliverables remain valid without Git.
>
> The branch is evidence inside the managed bare repository, not a canonical publication. Promotion begins only through the reviewed Git flows in Chapter 24.

## Submitting and requesting review

The producer submits the evidence, then asks the appropriate reviewer or reviewers to assess it. A combined action can do both when that is the intended handoff. Team prevents accidental duplicate active reviews of the same deliverable by the same reviewer.

A review may be requested by an authorized producer, supervisor, project lead, or Team manager. The assigned reviewer must be active, in the same Team, and compatible with any selected review profile.

Review profiles can apply by project and role and define required and optional criteria. Archived profiles cannot be assigned to new reviews. Use profiles for repeatable policy such as:

| Criterion | Requirement for telemetry retry |
|---|---|
| Behavior | Bounded exponential backoff and idempotent retry are demonstrated. |
| Tests | Transient recovery and terminal exhaustion tests pass. |
| Observability | Attempt count and final failure are diagnosable without sensitive payloads. |
| Scope | Changed paths match the task and reviewed evidence. |

Creating the same active review for one deliverable and reviewer is replay-safe: the service returns the existing review rather than duplicating inbox and wake work.

> <span class="sidebar-kicker">Technical sidebar</span> **Submission and automatic review creation**
>
> <code>team_submit_deliverable</code> records evidence and changes the task to <code>waiting_for_review</code>. The combined <code>team_submit_deliverable_for_review</code> can submit and create the intended handoff without a redundant intermediate notice.
>
> Workflow authors can use <code>trigger:team:deliverable-created</code> and one <code>effect:team:create-review-request</code> node per required reviewer/profile. The trigger owns its cursor and suppresses old deliverables when first enabled, avoiding an accidental historical review storm.

## Review decisions

Only the assigned reviewer may decide a review. The decision is immutable.

“Immutable” means the decision is not edited later to improve the history. Rework creates another deliverable and another review, while the earlier decision remains evidence.

### Accepted

For a profiled review, every required criterion must be present and passed. Optional criteria may be omitted. The accepted decision pins its reviewed evidence and findings.

A deliverable becomes accepted only when:

- all active attached reviews are accepted; and
- no carried review finding remains open.

When every current required deliverable for the task is accepted, the task can become accepted. Project status and goals then recalculate under their own rules.

### Rejected

A rejected review:

- marks the reviewed deliverable rejected;
- returns the task to <code>in_progress</code>;
- creates a rework inbox item for the producer;
- preserves concrete findings.

Imani does not edit the rejected deliverable after Rowan finds that jitter can exceed the deadline. Imani produces a new deliverable that explicitly supersedes the rejected one. Open findings are carried forward.

An accepted later review can resolve only findings owned by its same review profile. For legacy reviews without a profile, reviewer-role compatibility is used. One unrelated reviewer cannot silently close another policy's finding.

### Blocked

Use <code>blocked</code> when the review process or evidence access cannot proceed, not when the candidate is known to be defective. For an ordinary deliverable review, blocked state propagates blocking state to the deliverable/task and preserves a concrete blocker.

Release-review blocked behavior is stricter and retry-oriented; Chapter 24 distinguishes it from candidate rejection.

## Multi-review cascades

Suppose Northstar requires both Rowan's software-quality review and Priya's operational-readiness review.

1. Imani submits deliverable D1.
2. The runtime creates R1 for Rowan and R2 for Priya.
3. Rowan accepts R1. D1 stays pending because R2 remains open.
4. Priya rejects R2 with an unresolved deployment-safety finding. D1 becomes rejected and the task returns to rework.
5. Imani submits D2 superseding D1. The open finding is carried to D2.
6. New reviews target D2 and its exact pinned commit.
7. Both profiles accept and the owning profile resolves the carried finding.
8. D2 and the task become accepted.

This cascade prevents “one approval wins,” prevents review of a moving branch tip, and preserves why rework occurred.

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
> - <code>team_import_artifact</code> for permission-checked ordinary artifact handoffs;
> - <code>team_request_review</code>;
> - <code>team_decide_review</code>;
> - delivery list/get tools;
> - Git promotion and release tools covered in Chapter 24.
>
> Admin routes provide revision-checked operator create/edit/decision paths. Workflow providers add deliverable-created triggering and deterministic review-request creation. All paths reuse the same repository invariants.

## Decision and state failures

| Symptom or error | Meaning | Recovery |
|---|---|---|
| Artifact path rejected | The requested path escapes the allowed artifact root or has invalid shape. | Publish from a safe relative path below the session artifact root. |
| Artifact ref exists but bytes are unavailable | Metadata was recorded without publication, permission/version access failed, or an ordinary recipient has not imported it yet. | Inspect the artifact-space record; for ordinary handoffs call <code>team_import_artifact</code>, and for reviews inspect the reviewer import error. |
| Git context mismatch | Agent/task/session/dispatch or derived branch does not match the submitted context. | Use the current task work context; never type an alternate branch. |
| Reviewer cannot be assigned | Agent is inactive, cross-Team, or incompatible with the profile. | Select an active same-Team reviewer matching policy. |
| Accepted decision rejected by validation | A required criterion is missing/failed or findings remain open. | Record complete criterion results and resolve only authorized findings. |
| Second decision refused | Review decisions are immutable. | Create the appropriate successor deliverable/review; do not overwrite history. |
| Task stays waiting after one acceptance | Another active review or open carried finding remains. | Inspect the complete current deliverable review set. |
| Rejected task has no useful next step | Findings were vague. | Preserve evidence and create concrete, testable findings in the review decision. |

## Troubleshooting and handoff

In **Delivery**, begin with the task and identify its current deliverable. Then check every active review and every unresolved finding. Capture the task ID, deliverable ID, review IDs, verification status, artifact references, and reviewed commit. If an artifact cannot be opened, include the visible import or permission error rather than describing the candidate as failed.

Do not edit a terminal review or force the task state merely because one acceptance did not advance it. Another review or a carried finding may still be open. Give the captured identities and the relevant row from the failure table to a Team administrator or the responsible producer/reviewer.

## Producer, reviewer, and operator runbooks

### Producer

1. Confirm the task, active dispatch, and task-specific Git context.
2. Run proportionate verification and record honest status.
3. Publish artifacts from safe paths or attach existing stable refs.
4. Submit one deliverable with a stable request ID.
5. Request the required reviewers/profiles, or use the combined submission command.
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
4. Reconcile orphaned published artifacts deliberately.
5. Do not “fix” history by editing terminal review records.

## Source trail

- <code>src/evander_team_addon/models.py</code> — deliverable, verification, review, profile, criterion, finding, and supersession models.
- <code>src/evander_team_addon/services/commands/deliverable_submission_commands.py</code> and <code>review_commands.py</code> — submission, request authority, deduplication, and immutable decisions.
- <code>src/evander_team_addon/services/domain/deliverables.py</code>, <code>review.py</code>, and <code>git_work_contexts.py</code> — acceptance cascades, findings, artifact/Git evidence, and context validation.
- <code>src/evander_team_addon/providers.py</code> — deliverable trigger, review-request effect, artifact import, and wake prompt projection.
- <code>src/evander_team_addon/mcp.py</code> and <code>api.py</code> — grouped delivery tools and operator routes.
- <code>docs/addons.md</code> — artifact publication, review profiles, rework findings, and release handoff.
- <code>tests/test_team_addon_mcp.py</code>, <code>tests/test_team_addon_workflow_primitives.py</code>, and <code>tests/test_team_addon_runtime.py</code> — submission, profile enforcement, duplicate review, cascade, and reviewer-input behavior.
