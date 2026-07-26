# Human Review in Team Workflows

## Purpose

This document breaks the human-review design into focused proposals that can be
discussed and accepted independently.

The central recommendation is to make human acceptance an independently
configurable release gate. If Team release review is also configured, it takes
place before human review. If the human requests changes, the decision should
automatically create linked Team work and carry the feedback into a new, fully
reviewed release candidate. The human should not have to ask a manager to
manually reconstruct the work as an unrelated task.

No proposal treats a human as a synthetic Team agent, and no proposal allows an
agent to satisfy a human-review requirement.

## Current Gap

The current release policy supports automated verification and agent release
reviews. Agent reviewers inspect a pinned candidate commit, and successful gates
lead to canonical publication.

The missing lifecycle is what happens after a human finds a problem:

- Human identity and authority are not represented as release-review concepts.
- A rejected release package is terminal.
- Rejection does not create a durable human finding, case, or rework task.
- The human has no first-class action that returns the candidate to the Team.
- Multiple attempts are not presented as revisions of one release journey.

Relevant current seams include:

- `ProjectReleasePolicy`, `ReviewRequest`, and `ReleasePackage` in
  `src/evander_team_addon/models.py`
- release sequencing in
  `src/evander_team_addon/services/domain/release_gates.py`
- release-review decisions in
  `src/evander_team_addon/services/domain/review.py`
- existing case intake through `TeamCase`
- Admin Web identity and per-user notifications

## Proposal 1: Add an Explicit Human Release Gate

### Recommendation

Make human acceptance an optional but enforceable project release-policy gate.
Automated verification, Team release review, and human review are independently
configurable. When a stage is absent, the release sequencer skips it:

```text
accepted deliverables
        |
        v
pinned release candidate
        |
        v
automated verification (if configured)
        |
        v
Team release review (if configured)
        |
        v
human acceptance review (if configured)
        |
        v
canonical publication
```

Team release review and human review do not imply one another. If both are
configured, the human gate must be created only after all required Team release
reviews accept. This ordering must be enforced by the domain service rather than
by UI convention or agent instructions.

Only successful human review may set the candidate's final approval and queue
canonical publication when a human gate is configured.

### Suggested initial policy shape

```json
{
  "human_review_requirement": {
    "reviewer_group_ids": ["product-owners"],
    "checklist": [
      {
        "id": "web-uat",
        "prompt": "Complete the documented web acceptance test."
      }
    ]
  }
}
```

The first supported version should have one human-review requirement and require
one approval from its eligible reviewer pool. Multiple independent human gates,
configurable approval quorums, and reusable human-review profile catalogs should
be added only when a concrete project needs them. This keeps the first state
machine and authorization model small without preventing later expansion.

### Rules

- Projects without human requirements retain their existing behavior.
- A project may require human review without requiring Team release review.
- A project may require Team release review without requiring human review.
- If both are configured, Team release review always finishes before human
  review begins.
- Automated verification, Team release review, and human review each count as a
  valid release gate for policy validation.
- Missing or empty reviewer assignments block the release; they never bypass it.
- Reviewer group IDs refer to directory-scoped Admin Web groups, not Team
  governance groups.
- The effective policy is snapshotted in the release-candidate manifest, while
  each human request snapshots its resolved reviewer eligibility and checklist.
- Policy edits do not silently remove a gate from an in-progress attempt.

## Proposal 2: Model Human Review Separately from Agent Review

### Recommendation

Introduce a dedicated `HumanReviewRequest` rather than extending the current
agent-oriented `ReviewRequest` with many mutually exclusive fields.

The existing review record assumes:

- an assigned `reviewer_agent_id`
- Team inbox delivery and agent wakes
- agent-role and review-profile checks
- decisions made under an active agent dispatch

Human identity, notification, authorization, and interaction are different
enough to deserve a separate domain record.

### Suggested record

```text
HumanReviewRequest
- id
- release_package_id
- candidate_commit_sha
- candidate_tree_hash
- requirement_snapshot
- state
- test_target
- created_at
- completed_at
```

`requirement_snapshot` contains the applicable directory path, configured group
IDs, resolved eligible user IDs, checklist, and assignment generation. The
candidate SHA and tree hash are intentionally repeated on the request so the
record can enforce and audit the exact candidate being decided. Decisions and
findings reference the request directly; reverse ID arrays are derived rather
than stored in both directions.

Individual decisions should also be durable:

```text
HumanReviewDecision
- id
- human_review_request_id
- reviewer_user_id
- reviewer_display_name_snapshot
- decision
- checklist_results
- summary
- evidence_refs
- decided_at
```

The authenticated Admin Web identity supplies the reviewer fields. The client
must not be allowed to assert `reviewer_user_id` or `decided_by`.

## Proposal 3: Use Human-Specific Decision Semantics

### Recommendation

Avoid overloading generic `accepted`, `rejected`, and `blocked` states where they
would obscure the next action.

Human decisions should be:

| Decision | Meaning | Result |
| --- | --- | --- |
| `approved` | The exact candidate is acceptable. | Pass the human gate. |
| `changes_requested` | The candidate needs implementation changes. | Create linked rework. |
| `blocked` | Testing cannot proceed. | Preserve and resume the pinned candidate. |

`blocked` and `changes_requested` must remain distinct. A broken preview
environment should be recoverable without changing code. A product defect
requires a new candidate and a fresh human decision.

An approved decision is valid only for the request's pinned commit SHA and tree
hash.

For the initial single-approval policy, one eligible approval passes the gate.
If configurable quorum is added later, approvals must be unique by authenticated
user, `changes_requested` must immediately fail that candidate attempt, and late
decisions after a terminal result must be rejected. A `blocked` record is not an
approval or rejection: it preserves the request and its candidate until an
authorized resume operation records recovery.

Abandoning an entire release is a separate, audited management operation. It is
not a human-review decision and should not be granted merely because a user is
eligible to test the candidate.

## Proposal 4: Keep Release Attempts Immutable and Link Their Revisions

### Recommendation

Do not replace the candidate inside an existing `ReleasePackage` after human
review has started. Preserve the current immutable-candidate principle.

Add one forward lineage link:

```text
ReleasePackage
- supersedes_release_package_id
```

Successors, human requests, and change cases already point to their parent
records, so reverse relationships can be derived without maintaining matching
lists and pointers.

Each changed candidate becomes a successor attempt. The Admin UI groups the
lineage as one release journey:

```text
Release 2026.07
- Attempt 1: changes requested
- Attempt 2: blocked by preview environment, resumed, approved and published
```

This prevents approval races and retains an audit trail of exactly what each
human tested.

A successor attempt must identify its predecessor, use the predecessor's pinned
candidate as its immutable baseline, and apply only accepted correction
deliverables. Its manifest retains the predecessor's accepted release scope and
adds the correction evidence. Every configured gate still reruns against the
complete successor candidate.

A separate release-cycle entity is not necessary initially. Attempt lineage is
enough unless later querying or retention needs justify a dedicated aggregate.

## Proposal 5: Convert “Request Changes” into Team Work Atomically

### Recommendation

A `changes_requested` decision should perform one atomic domain transition:

1. Store the human decision, findings, and evidence.
2. Mark the candidate attempt as requiring changed content.
3. Guarantee that canonical publication is not queued.
4. Create or reuse a `TeamCase` with:
   - `source = "human_release_review"`
   - `source_ref = <human-review-request-id>`
   - release, candidate, finding, and evidence metadata
5. Create the required rework tasks.
6. Deliver those tasks through the existing Team inbox and work-due paths.

The operation must be idempotent. Repeating the same request must not create
duplicate cases, tasks, findings, inbox items, or wake events.

The case and task IDs should be derived deterministically from the human-review
request and routing target. Project activity should be derived from the open
findings, release state, and ordinary task state rather than maintained as a
separate flag.

### Task routing

- A finding aimed at a specific deliverable goes to that deliverable's task
  owner when that mapping is unambiguous.
- Findings with several possible owners, release-wide findings, and ambiguous
  findings create one triage/rework task for the project lead.
- More elaborate grouping by responsible owner may be added later if real
  review volume justifies it.
- At least one task is created transactionally so unresolved feedback cannot
  coexist with an apparently completed project.

### Preserve accepted history

Do not reopen accepted tasks or rewrite accepted deliverables. They are
historical facts and may already have unblocked dependent work.

Human-requested revisions create correction tasks linked through the case. Their
deliverables pass through the normal Team submission, review, finding,
acceptance, and Git-promotion lifecycle.

## Proposal 6: Carry Human Findings into the Successor Candidate

### Recommendation

Human findings should be durable records rather than free-form text copied only
into task descriptions.

Suggested fields include:

```text
HumanReviewFinding
- id
- source_human_review_request_id
- text
- target_deliverable_ids
- evidence_refs
- state
- proposed_resolution
- resolution_evidence_refs
- resolved_by_human_review_request_id
- created_at
- resolved_at
```

When rework deliverables have satisfied ordinary task acceptance and any
configured Team reviews, the successor release attempt should contain:

- the original accepted release scope
- the accepted correction deliverables
- a proposed resolution for every carried human finding
- evidence showing what changed

The predecessor request identifies the source release package, and the resolving
request identifies the successor package, so those package IDs do not need to
be duplicated on every finding. Completing a rework task records at most a
proposed resolution. A finding is resolved only when an approved human decision
on the corrected candidate explicitly confirms its resolution.

Every successor attempt reruns every gate configured for that attempt, in this
order:

1. automated verification, if configured
2. Team release review, if configured
3. human review, if configured

No earlier approval from a configured Team or human release gate is inherited
across a changed candidate.

## Proposal 7: Add Human Review Identity and Permissions

### Recommendation

Add one explicit Admin Web decision permission initially:

- `team.human_reviews.decide`

An eligible assignment grants read access to that request. Existing
`admin.owner` authority should cover reassignment and management initially; add
a delegated `team.human_reviews.manage` permission only if a concrete operator
role needs it. A broad read permission can likewise wait until an auditor or
support use case requires cross-user access.

A human decision must:

- derive its actor from the authenticated Admin Web session
- reject sessions without a real authenticated user rather than falling back to
  a generic `admin` actor
- verify that the actor is in the request's current eligible-user snapshot and
  either still belongs to an eligible group in the project's Admin directory
  scope or was added by an explicit audited reassignment
- verify `team.human_reviews.decide`
- retain the existing privileged-operation confirmation
- snapshot the display name for audit readability
- record the candidate SHA, decision, checklist, evidence, and timestamp

There should be no MCP tool through which an agent can approve a human review.
Agents may read human-review status and receive findings, but only an
authenticated human principal can decide the gate.

Reviewer reassignment should be explicit and audited. A reviewer becoming
unavailable must never result in an automatic waiver.

An emergency waiver, if ever added, should be a separate, highly privileged,
reason-required record displayed in the release manifest. It should not masquerade
as human approval.

## Proposal 8: Add a Human Review and Test Surface

### Recommendation

Begin with a human-review detail surface on the existing release view and a
“My Human Reviews” filter. A dedicated top-level queue can be added when review
volume makes it useful.

Each review should show:

- project and release-attempt lineage
- exact candidate SHA and release branch
- included deliverables and release notes
- automated verification results
- agent review decisions and criteria
- carried human findings from earlier attempts
- test instructions and preview target
- human acceptance checklist
- evidence and attachments

Primary actions are:

- Approve
- Request changes
- Blocked

“Request changes” requires at least one actionable finding. Findings may
optionally identify affected deliverables.

The existing Admin per-user notification mechanism can notify eligible
reviewers and deep-link to the request. The durable Team review record remains
the source of truth. Notification publication should be driven by a retryable
outbox event and deduplicated by human-review request ID, recipient user ID, and
assignment generation. That allows a later reassignment or retry to notify the
same user again without duplicating one assignment's notification.

## Proposal 9: Support Preview Preparation Without Embedding Deployment Logic

### Recommendation

Human review should be able to depend on a prepared test target, but deployment
should remain workflow-owned rather than being hard-coded into the Team Add-on.

After automated verification and any configured Team release review succeed,
Team may publish:

```text
team.release.human_review_preparation_requested
```

A configured workflow prepares the pinned candidate and returns safe review
metadata:

- preview URL or target alias
- expiry
- test instructions
- preparation evidence

The workflow returns that metadata through an idempotent preparation-result
command tied to the human-review request and pinned candidate. The human request
remains `preparing` until the target is ready. Preparation failure blocks the
same request and candidate; recovery retries preparation rather than creating a
new release attempt.

Projects that cannot create a preview may explicitly use manual mode with
branch, artifact, or installation instructions.

The Team database should store only safe target metadata. Credentials and
deployment secrets remain in host-owned systems.

Preview preparation has substantial operational value when human testing needs
a stable environment, but it also adds a separate workflow result, expiry, and
recovery lifecycle. Keeping it in its own delivery slice preserves that value
without making every human gate depend on deployment automation.

## Proposal 10: Make Post-Release Feedback First Class Too

### Recommendation

Add “Report regression or adjustment” to published release packages.

The action uses the same finding-to-case service as pre-release
`changes_requested`, but reporting does not itself decide the remedy:

- preserve the published release as immutable history
- create or reuse a case linked to the released package
- record the report, evidence, and authenticated reporter
- create one triage task for the project lead

Triage may then:

- close an invalid, duplicate, or already-resolved report with evidence
- create correction tasks and Team inbox work
- request a separately controlled rollback
- authorize a successor release linked to the published package and case

This handles problems that cannot reasonably be discovered before release.

Rollback remains a separate controlled release operation. Reporting an issue
must not silently revert or mutate the published release, and it must not
automatically create a successor release before triage.

Post-release feedback is likewise a distinct product capability rather than a
prerequisite for human approval. It should reuse the same case and correction
services while retaining its own triage and rollback decisions.

## Proposal 11: Add Explicit API and Event Surfaces

### API surfaces

Suggested routes:

```text
GET  /human-review-requests?assigned_to=me&state=pending
GET  /human-review-requests/{id}
POST /human-review-requests/{id}/decisions
POST /human-review-requests/{id}/reassign
POST /human-review-requests/{id}/resume
POST /human-review-requests/{id}/preparation-result
POST /release-packages/{id}/report-issue
```

Decision requests should use optimistic revision checks and idempotency keys.
Actor identity always comes from authentication.

The preparation-result route is an internal workflow/effect surface rather than
a general human-facing mutation. It must authenticate the workflow result,
verify the pinned request and candidate, and reject secret-bearing metadata.

### Domain events

Suggested events:

```text
team.release.human_review_requested
team.release.human_review_preparation_requested
```

These events exist for effects outside the Team state transaction: reviewer
notification and optional preview preparation. Human decisions, linked case and
task creation, and finding state changes are already durable atomic domain
transitions and do not need parallel events without a concrete subscriber. A
post-release event may be added later if an external consumer, rather than the
atomic case-intake service, needs it.

Agents need no human-decision mutation tool. Their existing release-package read
payloads should expose compact human-gate status, findings relevant to their
work, and the exact next action.

## Proposal 12: Preserve Project and Blocker Semantics

### Recommendation

Project status calculation must account for:

- pending human review
- blocked human review
- human-requested rework
- successor release preparation

A project must not become completed while any of those states remains active.

This should be derived from existing release and task state plus unresolved
human findings. A separate mutable release-journey status is unnecessary:
package lineage, requests, findings, cases, and tasks already describe the
journey.

Suggested behavior:

- pending human review keeps the release package pending and the project active
- blocked human review creates a visible release/human-review blocker
- changes requested creates active rework tasks and open findings in the same
  transaction
- completing rework tasks without a successor human decision does not complete
  the release journey because the human findings remain open

Blocker summaries and next-action hints should distinguish:

- retry the same human review after environment recovery
- implement human-requested changes
- prepare the successor candidate
- wait for human acceptance

## Proposal 13: Deliver the Feature in Safe Slices

The core can be implemented in small increments without exposing a half-built
gate. Slices A through C remain unavailable in project policy until Slice D
closes the successor loop.

### Slice A: Records and persistence behind disabled policy

- human-review policy and snapshots
- human request, decision, and finding models
- persistence and migrations
- release-attempt predecessor lineage
- read models for requests, decisions, findings, and attempt history

### Slice B: Authenticated approval path behind disabled policy

- release-gate sequencing after Team review
- candidate-pinning and no-bypass invariants
- authenticated actor capture and decision authorization
- approved-decision handling
- exactly-once canonical publication
- basic release-detail presentation

### Slice C: Changes-requested and blocked recovery behind disabled policy

- changes-requested case creation
- deterministic rework task routing
- Team inbox and wake integration
- blocked-review resume against the same candidate
- open findings preventing premature project completion

### Slice D: Successor closure and feature enablement

- successor release-attempt lineage
- carried human findings and resolutions
- complete gate reruns against the successor candidate
- project-status integration
- basic approve, request-changes, and blocked UI actions

Human-review policy becomes configurable only after Slice D passes its
end-to-end acceptance tests. This prevents a gate that merely terminates a
release from reproducing the lifecycle gap the feature is meant to close.

Slices E through G are independent after the core gate is enabled. Their order
should follow product priority; separating them reflects their different state
and failure modes, not a judgment that preview preparation or post-release
feedback lacks value.

### Slice E: Operator experience

- My Human Reviews filter and, when justified by volume, a dedicated queue
- review detail and checklist
- notifications and deep links
- release revision history
- audited reassignment

### Slice F: Preview preparation

- preparation event
- idempotent workflow result integration
- target readiness and expiry
- manual test-target mode
- environment-recovery handling

### Slice G: Post-release feedback

- regression or adjustment reporting
- linked case and project-lead triage
- explicit correction-work creation
- optional successor linkage after triage
- separate rollback escalation

### Slice H: Advanced policy and routing

- multiple independent human requirements
- configurable approval quorums
- reusable human-review profiles
- higher-volume finding-to-owner routing
- delegated review management or emergency waivers if justified

## Acceptance Tests

The implementation should prove at least these invariants:

1. A human-only release policy is valid and does not create a Team release
   review.
2. A Team-review-only release policy is valid and does not create a human
   review.
3. When both gates are configured, human review cannot be created before all
   required Team release reviews accept.
4. Human approval can publish only the exact pinned SHA and tree hash.
5. Unauthorized users and agents cannot decide a human review.
6. Client-supplied actor identity cannot spoof the authenticated reviewer.
7. A session without a real authenticated user cannot fall back to a generic
   actor and decide a human review.
8. Reviewer eligibility and permissions are enforced in the project's Admin
   directory scope.
9. Repeated decisions are idempotent, and concurrent terminal decisions cannot
   publish or create rework twice.
10. `changes_requested` never queues canonical publication.
11. `changes_requested` creates exactly one case and the required deterministic
    tasks; failure rolls back the complete transition.
12. `blocked` preserves the request and candidate and permits recovery without
    code changes.
13. A changed candidate reruns every gate configured for the successor attempt.
14. Successor attempts carry every unresolved human finding, and only an
    approved human decision can resolve it.
15. Accepted historical tasks and deliverables are not reopened or rewritten.
16. A project cannot complete while a human review or unresolved human finding
    remains active.
17. Notification retries do not duplicate one assignment's notification, while
    an audited reassignment can notify the same user again.
18. Existing projects without human-review policy retain existing behavior.
19. Existing open or historical releases are not retroactively gated.
20. Preview preparation results are idempotent, match the pinned request and
    candidate, and cannot persist credentials or secret-bearing metadata.
21. Preview preparation failure blocks and resumes the same candidate.
22. Reporting a post-release issue creates exactly one linked case and triage
    task without mutating the published package or automatically creating a
    successor release.
23. Triage can explicitly create linked correction work or authorize a successor
    without conflating either action with rollback.

If Slice H adds quorum, its tests must also prove that one user cannot count
twice, the threshold is evaluated atomically, `changes_requested` has defined
veto behavior, and late decisions cannot alter a terminal result.

## Recommended First Decision

The foundational decision is whether human review is:

1. merely another approval button on the existing agent review record, or
2. a distinct, authenticated release gate with its own feedback and rework
   lifecycle.

This document recommends the second option. It is slightly more domain work, but
it preserves agent-review semantics, creates a trustworthy audit trail, and
solves the actual operational problem rather than only postponing final
publication.

The first implementation should then use the bounded policy from Proposal 1:
one eligible reviewer pool, one approval, and an inline checklist. The richer
policy options remain an explicit later slice rather than implicit complexity in
the foundational release gate.
