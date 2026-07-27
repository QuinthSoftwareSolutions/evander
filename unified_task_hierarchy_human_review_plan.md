# Unified Team Hierarchical Execution and Human Release Review Plan

Status: consolidated design and implementation plan only; no feature
implementation has been made.

This document consolidates:

- `task_hierarchy_discussion_v2.md`; and
- `human_review_discussion.md`.

`task_hierarchy_discussion.md` is intentionally disregarded because version 2
supersedes it.

The two source proposals remain the approved feature baseline. This document
does not remove their features. It gives the two feature groups one shared
domain model, removes overlapping implementation machinery, resolves lifecycle
ambiguities between them, and defines one delivery order.

## Outcome

Team work should form one continuous, auditable lifecycle:

```text
constituent tasks
      |
      v
accepted child contributions
      |
      v
integrated parent deliverable
      |
      v
pinned release attempt
      |
      +--> automated verification, if configured
      |
      +--> agent release review, if configured
      |
      +--> human release review, if configured or carried findings require it
      |
      v
canonical publication
```

If a human requests changes, the lifecycle continues without rewriting any
accepted or reviewed history:

```text
human changes_requested
      |
      v
durable findings + one Team case + correction root tasks
      |
      v
accepted correction deliverables
      |
      v
successor release attempt based on the predecessor candidate
      |
      v
all applicable gates rerun
      |
      v
human confirms carried findings + canonical publication
```

The hierarchy feature supplies reliable decomposition, integration, readiness,
dispatch, blocker, and worktree semantics. Human review uses those semantics for
correction work instead of creating a second task or escalation system. Human
review in turn gives hierarchy a concrete release-level consumer that requires
correct contribution closure, immutable lineage, and project-completion rules.

## Consolidation decisions

The merged implementation has the following single owners for shared concerns.

| Concern | Canonical owner |
| --- | --- |
| Whether a task is resolved or actionable | One derived `TaskAssessment` |
| Why Team work cannot proceed | `TeamBlocker` and its events |
| Human feedback intake and grouping | Existing `TeamCase` |
| Constituent output lineage | `Deliverable.source_deliverable_ids` |
| Release revision history | `ReleasePackage.supersedes_release_package_id` |
| Exact release evidence and policy | Existing release candidate manifest |
| Agent wake authorization and work scope | Durable `TeamDispatch` |
| Human identity and decision audit | `HumanReviewRequest` plus durable decisions |
| Human defects carried between attempts | Standalone `HumanReviewFinding` |
| Human notification delivery | Existing Admin user notifications driven by Team outbox |
| Preview deployment | Existing workflow runtime, not Team deployment logic |
| Physical task checkout history | Existing Git context/action audit plus cleanup results |

Do not add:

- a persisted `waiting_on_children` task state;
- a separate hierarchy status table;
- a second blocker or escalation subsystem for human review;
- a release-cycle or release-journey aggregate;
- a synthetic human Team agent;
- a human review variant of agent `ReviewRequest`;
- a separate post-release report aggregate when `TeamCase` can be the report;
- reverse ID arrays when the relationship can be queried from its owning record;
- copied child acceptance, review, Git, artifact, or blocker state on a parent;
- delegated or transferable root-task grants;
- a persistent selected-task shell state;
- a second worktree-history model.

## Shared terminology and relationship rules

The following relationships are intentionally different.

| Relationship | Meaning | Completion effect |
| --- | --- | --- |
| `parent_task_id` | The child is a constituent part of the parent. | The child recursively gates the parent. |
| `dependency_task_ids` | This task cannot proceed until another task resolves. | The dependency gates readiness, not ownership or composition. |
| `originating_task_id` | Work on one task caused independent work to be created. | Provenance only; neither task gates the other. |
| `case_id` | Tasks belong to one intake or correction case. | The case groups work; task and finding state determine completion. |
| `source_deliverable_ids` | These exact accepted direct-child outputs were incorporated. | They prove composition of an aggregate deliverable. |
| `supersedes_release_package_id` | A release attempt revises an immutable predecessor attempt. | It creates release-attempt lineage without changing the predecessor. |
| Human finding to correction task | A current independent task is responsible for proposing a resolution. | The finding remains open until a successor human approval confirms it. |

If work may continue independently after task A resolves, it is not a child of
task A. In particular, a support task whose outcome is “create task X” does not
make X its child. X is an independent root with optional provenance.

Human correction tasks are likewise not children of the accepted historical
tasks whose deliverables exposed a defect. They are new roots linked to the
human-review case. When the original task is unambiguous, it may be recorded as
the correction task's `originating_task_id`.

## Non-negotiable invariants

### Task and deliverable invariants

1. A support or coordination task resolves when it is `finished`.
2. An outcome task resolves when its result is `accepted`.
3. An accepted evidenced no-op is a valid outcome resolution when the task
   contract permits it.
4. A task with an authorized cancellation disposition resolves as `cancelled`.
5. `blocked` and `rejected` never resolve a task for its ancestors.
6. A parent cannot finish, become accepted, submit its final deliverable, or
   enter a release while any descendant is unresolved.
7. An aggregate parent deliverable names exact accepted direct-child source
   deliverables. Transitive lineage is derived recursively.
8. Submission, specialist review, direct acceptance, task resolution, release
   review, human acceptance, and publication remain separate lifecycle stages.

### Release and human-review invariants

1. Every review decision is pinned to an immutable candidate SHA and tree hash.
2. A changed candidate always creates a successor release attempt and a new
   human request.
3. Automated verification precedes agent release review, which precedes human
   review. Missing stages are skipped by the domain sequencer.
4. If a human gate applies, no other path may set final approval or queue
   canonical publication.
5. `blocked` preserves the same candidate and is recoverable without product
   changes.
6. `changes_requested` prevents publication and atomically creates durable
   findings, one case, and actionable Team work.
7. Accepted tasks, deliverables, reviews, candidates, and published releases are
   never reopened or rewritten by human feedback.
8. Open human findings can be resolved only by an approved human decision on a
   successor candidate that carries those findings.
9. Existing projects and historical releases are not retroactively given a
   human gate.
10. A project cannot complete while hierarchy violations, pending or blocked
    human review, correction work, successor preparation, or unresolved human
    findings remain.

### Authority and identity invariants

1. Agent root creation remains governed by project-scoped
   `create_root_task`.
2. The atomic human-feedback domain transition may create correction roots
   under system authority. It does not grant root authority to an agent.
3. Dispatch scope is scheduler-computed, immutable, and cannot be widened by an
   agent.
4. A human actor is derived from a real authenticated Admin Web session.
5. Client-supplied user IDs or actor fields are ignored and rejected where
   supplied.
6. No MCP tool can approve, reject, or unblock a human review on behalf of a
   human.
7. Reviewer eligibility never acts as release-management or abandonment
   authority.

## Minimal domain model

### Extend `AgentTask`

Add:

```text
AgentTask
- originating_task_id: str | None
- cancellation: TaskCancellation | None
```

Add `cancelled` to `TeamTaskState`.

`TaskCancellation` is an embedded terminal disposition, not a standalone
aggregate:

```text
TaskCancellation
- reason_code
- reason
- actor_kind
- actor_id
- successor_task_id
- cancelled_at
```

Rules:

- cancellation fields are required when state is `cancelled` and forbidden
  otherwise;
- a successor, when present, belongs to the same project and cannot be the
  cancelled task;
- cancellation authority is limited to policy-authorized owners, accountable
  parent owners, project leads, Team managers, or operators;
- cancellation is audited and never silently substitutes for acceptance;
- for every user- or agent-initiated creation, `originating_task_id` must
  reference an existing same-team, same-project task visible to the creator at
  the command boundary;
- `originating_task_id` remains provenance only, confers no authority, and
  creates no completion dependency;
- the atomic human-feedback transition may set `originating_task_id` under
  system authority only when it derives the origin from a target deliverable
  or task already authorized by the same human-review request and case. This is
  a narrow same-team, same-project correction path, not a general visibility
  bypass.

### Extend `Deliverable`

Add:

```text
Deliverable
- source_deliverable_ids: list[str]
- completion: TaskCompletion | None
```

`source_deliverable_ids` is used only for direct-child composition. It does not
copy any child state.

The optional `completion` closes one current lifecycle gap: an outcome task's
evidenced no-op must be accepted rather than self-declared terminal. For the
initial implementation:

- a submitted deliverable may carry
  `TaskCompletion(kind="evidenced_noop")`;
- that form is valid only for an outcome task whose contract allows it;
- it follows the existing direct-acceptance or formal-review path;
- accepting it copies the accepted completion snapshot to the task and moves
  the task to `accepted`;
- normal delivered outcomes continue to derive task completion from accepted
  deliverable outcomes;
- agents may no longer finish an outcome task directly through
  `team_update_task`; support tasks retain direct `finished` completion.

This reuses the existing deliverable handoff and review lifecycle instead of
adding a second “no-op approval” entity or command.

### Add a derived `TaskAssessment`

`TaskAssessment` is a value returned by one domain service. It is not stored.

At minimum it contains:

```text
TaskAssessment
- task_id
- resolution_status: resolved | unresolved | invalid
- resolution_reason
- readiness:
    actionable
    waiting_on_children
    waiting_on_dependencies
    waiting_for_acceptance
    waiting_for_review
    blocked
    resolved
    invalid
- direct_child_ids
- unresolved_descendant_ids
- unsatisfied_dependency_ids
- active_acceptance_ids
- active_review_request_ids
- active_blocker_ids
- integration_ready
- contribution_coverage
- invalid_reason_codes
```

The evaluator uses this precedence:

1. structurally or historically contradictory state is `invalid`;
2. a valid terminal disposition is `resolved`;
3. an intrinsic open blocker or unrecovered rejection is `blocked`;
4. unresolved descendants produce `waiting_on_children`;
5. unsatisfied dependencies produce `waiting_on_dependencies`;
6. active direct acceptance or review produces the corresponding waiting state;
7. otherwise the task is `actionable`.

A parent waiting on a blocked child remains `waiting_on_children`; the response
also exposes the descendant's blocker IDs and reason. Do not create a duplicate
parent blocker.

The same evaluator must be called by:

- task and deliverable commands;
- direct-acceptance and formal-review cascades;
- release-package construction;
- project status;
- wake selection;
- dispatch scope construction;
- Git/worktree preparation;
- Admin and MCP read payloads;
- task-board and detail UI.

Replace scattered local terminal-state and dependency tests where they make a
lifecycle decision. Small presentation-only enum checks may remain, but they
must consume the assessment result rather than define new semantics.

### Extend `TeamBlocker`

`TeamBlocker` remains the only durable blocker.

Add:

```text
TeamBlocker
- reason_code
- reporter_actor: str | None
- affected_task_ids: list[str]
```

Add source kinds for:

- `human_review_request`; and
- `agent_execution_stall`.

Human-review blockers use distinct stable reason codes for at least:

- `human_review_assignment_unavailable`;
- `human_review_testing_blocked`;
- `human_review_preparation_failed`; and
- `human_review_target_expired`.

An assignment-unavailable blocker is accountable to a directory-scoped
`admin.owner` or later explicitly authorized review manager. Project leads and
Team managers retain visibility and escalation context, but do not gain
reassignment authority from blocker ownership.

Keep `task_id` as the primary task context for existing single-task blockers.
`affected_task_ids` is used for one incident that legitimately covers several
tasks, especially an agent-wide execution stall.

Do not add blocker ownership or escalation fields to tasks, human requests, or
release packages. Those records may persist their lifecycle state, but the
current explanation, accountable resolver, escalation route, follow-up, and
event history come from `TeamBlocker`.

The task description is never used as a substitute for a blocker reason.

Entering an intrinsic blocked state and opening the blocker is one transaction.
Resolving a blocker invokes source-aware reconciliation:

- task blockers restore the readiness-consistent task state;
- review blockers restore the appropriate review/deliverable state;
- release and human-review blockers resume the correct unchanged gate;
- execution-stall resolution resets the agent no-progress allowance.

The generic blocker command must never blindly set a source record to `pending`.

Task reassignment and cancellation must also reconcile every linked blocker in
the same Team-state transaction:

- reassignment updates or resolves task-local blockers as appropriate,
  recomputes their accountable owner and route, and clears or revalidates any
  delegate made obsolete by the assignment; it also recomputes the task's
  membership in shared blockers, including removal from an old owner's
  execution-stall incident;
- cancellation resolves task-local blockers when appropriate and removes the
  task from a shared blocker's `affected_task_ids`;
- a shared blocker is resolved only when its source-aware blocking condition no
  longer applies to any affected task; and
- unrelated blockers are unchanged, after which task assessment and project
  status are recalculated.

### Add `TeamDispatch`

Add one durable dispatch record:

```text
TeamDispatch
- id
- agent_id
- session_id
- cause_kind
- cause_id
- task_ids
- blocker_ids
- review_request_ids
- inbox_item_ids
- project_ids
- managed_repo_ids
- status: preparing | leased | completed | expired | failed
- created_at
- prepared_at
- leased_at
- lease_expires_at
- completed_at
- error
```

All scope lists are immutable after creation. Status, timestamps, and error are
the only mutable lifecycle fields.

`TeamDispatchActivity` remains the append-only action/result ledger. It does not
replace `TeamDispatch`: command authorization and preparation need one current,
transactionally readable scope record rather than reconstructing scope from
activities.

Human review requests are not inserted into an agent dispatch. Human-requested
correction tasks, agent reviews, blockers, cases, and inbox notices use their
ordinary Team records and enter dispatch scope through those records.

### Extend release policy and manifest

Add one bounded initial policy type:

```text
HumanReviewRequirement
- reviewer_group_ids
- checklist
```

Add:

```text
ProjectReleasePolicy
- human_review_requirement: HumanReviewRequirement | None

ReleaseCandidateManifest
- human_review_requirement
- predecessor_release_package_id
- correction_deliverable_ids
- carried_human_findings
```

The effective project Admin directory path is derived from the project's
configured directory context, using its Git directory path when applicable and
`root` otherwise, then snapshotted into the resolved human requirement. Do not
let the UI infer a different directory.

The first policy version supports:

- one human requirement;
- one eligible reviewer pool;
- one approval;
- an inline checklist;
- manual test instructions.

Automated preview preparation is added later through an explicit preparation
mode. Multiple gates, quorums, reusable profiles, and emergency waivers remain
conditional advanced-policy work.

Verification commands, agent review requirements, and human review each satisfy
the existing “at least one release gate unless explicitly ungated” validation.
Known missing or empty human reviewer assignments make a policy invalid at
configuration time. That validation is preventive, not the runtime recovery
mechanism: if a previously valid assignment later resolves to no
decision-eligible users, the release attempt becomes durably blocked and never
bypasses the human gate.

The manifest snapshots configured policy. A human request additionally
snapshots resolved reviewer eligibility and the checklist. Policy edits do not
change an in-progress attempt.

### Extend `ReleasePackage`

Add one forward link:

```text
ReleasePackage
- supersedes_release_package_id: str | None
```

Do not add a reverse successor list or a release-cycle entity. Attempt history
is derived by querying the predecessor link.

For a successor:

- the predecessor candidate is the immutable Git baseline;
- the predecessor's accepted release scope is retained;
- only newly accepted correction deliverables are applied to that baseline;
- all release deliverable evidence remains in the successor manifest;
- the resulting tree must differ from the changes-requested predecessor;
- all gates applicable to the successor rerun against the complete candidate.

The request API accepts the predecessor ID plus accepted correction
deliverables. The domain service derives the retained predecessor scope; callers
must not manually reconstruct it.

If unresolved human findings are carried, the effective successor policy must
still include a human gate even if a later project policy edit removed it.
Otherwise those findings could neither be confirmed nor safely resolved. Use
the current valid human requirement when available; otherwise carry the
predecessor requirement snapshot and require audited reassignment if its users
are no longer available. This is a lineage obligation, not retroactive gating
of unrelated releases.

### Add human-review records

Three new durable record types are justified.

#### `HumanReviewRequest`

```text
HumanReviewRequest
- id
- release_package_id
- candidate_commit_sha
- candidate_tree_hash
- requirement_snapshot
- current_assignment
- assignment_generation
- state: preparing | pending | blocked | approved | changes_requested
- preparation_generation
- test_target
- created_at
- ready_at
- completed_at
```

`requirement_snapshot` is immutable and contains:

- directory path;
- configured Admin group IDs;
- initially resolved eligible user IDs;
- checklist;
- initial test instructions.

`current_assignment` contains the currently eligible and explicitly reassigned
users. Reassignment increments `assignment_generation`; it does not rewrite the
original requirement snapshot.

`test_target` stores only safe metadata such as a URL or alias, expiry, test
instructions, and evidence. It never stores credentials.

Candidate SHA and tree hash are intentionally repeated on the request. That
small duplication is an enforcement and audit boundary, not a second candidate
source of truth.

#### `HumanReviewDecision`

```text
HumanReviewDecision
- id
- human_review_request_id
- reviewer_user_id
- reviewer_display_name_snapshot
- decision: approved | changes_requested | blocked
- checklist_results
- confirmed_finding_ids
- summary
- evidence_refs
- request_id
- decided_at
```

A separate decision record remains useful even with one initial approval:

- a request can be blocked, resumed, and later approved;
- every human action retains its actor, checklist, evidence, and time;
- idempotency and concurrent-decision handling have a durable key;
- a later quorum feature can build on the same record without rewriting history.

`changes_requested` requires at least one actionable finding. `approved`
requires explicit confirmation of every carried finding on the request.
Terminal decisions after `approved` or `changes_requested` are rejected.

Release abandonment is not a reviewer decision and is not added by this
feature. If needed, it must be a separate reason-required management operation.

#### `HumanReviewFinding`

```text
HumanReviewFinding
- id
- source_human_review_request_id
- text
- target_deliverable_ids
- evidence_refs
- correction_task_id
- state: open | resolved
- proposed_resolution
- resolution_evidence_refs
- proposed_by_deliverable_ids
- resolved_by_human_review_request_id
- created_at
- resolved_at
```

The finding is standalone because it outlives one request, can target several
deliverables, drives correction work, and is confirmed by a later request.
Existing agent `ReviewFindingRecord` is deliverable- and agent-review-specific
and should not be made into a large union type.

The correction task may record a proposed resolution after its ordinary
deliverable acceptance. That does not resolve the finding. At successor
candidate pinning, the manifest snapshots the proposal and evidence. Only a
later approved human decision that confirms the finding changes it to
`resolved`.

Do not add reverse human request or finding ID lists to `ReleasePackage`.
Release payloads derive them by `release_package_id`, and finding payloads derive
their source and resolving packages through their requests.

### Reuse `TeamCase`

For pre-release changes:

```text
TeamCase
- source = "human_release_review"
- source_ref = <human-review-request-id>
```

The case should not copy the release candidate, finding text, and evidence into
metadata. Those remain available through `source_ref`. The case owns grouping,
priority, owner, status, and task links.

For post-release feedback, the case itself is the durable report:

```text
TeamCase
- source = "post_release_feedback"
- source_ref = <release-id-and-idempotency-key>
- description = authenticated report
- metadata = reporter snapshot, release_package_id, evidence_refs
```

Those post-release fields are native report data, not copies of another domain
record, so a separate `PostReleaseReport` is unnecessary.

## Canonical task resolution and hierarchy enforcement

### Resolution rules

The assessment service recursively evaluates descendants. Direct-child
transitions are sufficient for notifications because a child cannot resolve
until its own descendants resolve.

Valid resolutions are:

| Task kind | Valid resolution |
| --- | --- |
| Support/coordination | `finished` |
| Outcome with accepted delivered result | `accepted` |
| Outcome with accepted evidenced no-op | `accepted` |
| Any task made obsolete by authorized disposition | `cancelled` |

An outcome task in `finished` without accepted output is invalid under the new
contract. A parent in `finished` or `accepted` with an unresolved descendant is
also invalid.

### Shared command boundaries

The following operations must call the same assessment and reject an invariant
violation:

- task finish;
- task cancel;
- deliverable submission;
- direct deliverable acceptance;
- formal review acceptance and cascade;
- release-package creation;
- canonical publication;
- Admin task/review/release actions;
- MCP task/deliverable/review/release commands.

Validation errors return exact IDs for unresolved descendants, dependencies,
acceptances, reviews, blockers, and missing contributions.

### Direct acceptance routing

Containment and organization usually align, but the domain must not assume they
always do.

For an ordinary direct handoff:

- a child contribution is offered to its parent-task owner when that owner is
  different from the producer;
- a root task continues to use the owner's reporting superior;
- a self-owned child uses the owner's reporting superior rather than silently
  self-accepting;
- configured specialist reviews keep their existing independent authority;
- the parent owner receives the child transition/handoff notice even when a
  formal reviewer is the accepting actor.

This keeps direct-superior and specialist review semantics intact while making
the accountable parent aware of every constituent result.

### Contribution coverage

Before an aggregate parent deliverable may be submitted:

- every direct child is resolved;
- each direct outcome child has a current accepted source deliverable unless it
  resolved through an accepted evidenced no-op or authorized cancellation;
- each support child with a current accepted assessable deliverable is included;
- a resolved support child with no assessable output needs no artificial
  deliverable;
- every named source belongs to a direct child;
- every named source is accepted, current, and not superseded;
- the resulting parent Git commit or artifact bundle is the exact submitted
  object.

If a child is itself aggregate, its accepted deliverable provides the transitive
lineage. A root deliverable does not relist every leaf.

Release scope follows the same closure:

- a constituent child deliverable cannot be released while its aggregate
  ancestor is unresolved;
- a release uses the accepted top-level aggregate deliverable rather than both
  that deliverable and sources already contained within it;
- independent root deliverables may coexist in one release;
- the candidate manifest exposes the recursively derived source graph for human
  inspection without copying its records.

## Transition-driven supervision

An aggregate parent with unresolved children is not ordinarily actionable and
does not receive periodic `task_ready` wakes merely because children remain
open.

Wake or notify its owner when:

- a child reports a blocker or asks for help;
- a child submits a deliverable or requests acceptance;
- a child contribution or review is rejected;
- a child reaches a resolved disposition;
- the direct-child set changes from not integration-ready to integration-ready;
- an operator or existing configured mechanism creates an explicit checkpoint.

Coalesce notices by parent and transition. Reuse inbox, blocker, scheduled-wake,
and operator mechanisms. Do not add a new persisted supervision checkpoint
model until an observed need justifies it.

A parent's own substantive implementation is represented by an explicit
self-owned child. The aggregate parent represents planning, supervision,
integration, and the final combined output.

## Root creation and human correction work

### Normal root creation

1. A manager who already knows the independent work creates it directly.
2. A project-scoped planner or lead may create roots when its role has
   `create_root_task`.
3. An agent without that permission proposes work through the existing
   deliverable or inbox flow; an authorized actor materializes it.

Every user- or agent-facing root-creation command rejects an
`originating_task_id` that is outside the Team, outside the project, or not
visible to its creator. Supplying provenance never supplies root-creation
authority. The system-created human-correction path uses only the constrained
exception defined in the model rules above.

Only add a first-class draft proposal if that manual pattern becomes common and
error-prone.

### Atomic human changes-requested transition

`changes_requested` performs one Team-state transaction:

1. insert the human decision;
2. insert deterministic durable findings and evidence;
3. move the request to `changes_requested`;
4. move the immutable release attempt to terminal
   changes-requested/rejected state;
5. guarantee no canonical publication event exists;
6. create or reuse one deterministic `TeamCase`;
7. create at least one deterministic independent correction or triage root;
8. link each finding to its current correction task;
9. create or reuse deterministic Team inbox items and work-due wake
   consequences;
10. recalculate release and project status.

Any failure rolls back the entire transition.

The transition's idempotency boundary includes the decision, findings, case,
tasks, inbox items, and wake/work-due consequences. Derive inbox source IDs and
wake deduplication keys from the human request, routing target, and transition
kind. Retrying the command or racing the same terminal decision must reuse or
coalesce those consequences rather than duplicate them. Wake coalescing may
reuse an already pending agent wake; the invariant is one logical work-due
obligation per deterministic target, not one new physical agent turn for every
record.

Initial routing remains intentionally simple:

- one finding that unambiguously targets a deliverable routes to that
  deliverable task's current owner;
- the correction root records the original task as provenance;
- ambiguous, multi-owner, or release-wide findings route to one project-lead
  triage task;
- several findings may share that triage task;
- triage may close an invalid mapping or create independent correction roots
  and retarget the findings;
- when a correction root needs decomposition, it uses the normal task hierarchy
  and contribution rules.

Do not automatically wrap every correction in an otherwise empty aggregate
task. The case already groups the work. Use an aggregate correction task only
when there is a real combined correction outcome to integrate.

## One release-gate sequencer

Replace stage-specific “create reviews or approve” branching with one idempotent
domain operation, conceptually `advance_release_gates`.

For the pinned manifest it evaluates, in order:

1. candidate and hierarchy validity;
2. automated verification;
3. all current agent release reviews;
4. human test-target preparation, when configured;
5. human review;
6. final approval and canonical publication.

Every stage callback calls the same sequencer. The sequencer creates only the
next missing stage and returns when a current stage is pending or blocked.

| Stage result | Release behavior |
| --- | --- |
| Verification candidate failure | Existing terminal failed-candidate behavior |
| Verification infrastructure failure or timeout | Block same attempt; retry unchanged candidate |
| Agent review rejection | Existing terminal agent-review behavior |
| Agent review blocked | Block and retry review against unchanged candidate |
| Human preparation failure | Block same request and candidate; retry preparation |
| Human `blocked` | Block same request and candidate; resume after recovery |
| Human `changes_requested` | Terminal attempt plus atomic finding/case/task handoff |
| Human `approved` | Resolve carried findings and pass final gate |
| No human requirement or carried obligation | Skip human stage |

Final approval is idempotent and queues canonical publication exactly once.
When a human requirement applies, `approved_commit_sha` is not set before the
human stage accepts.

## Human review lifecycle

### Request creation

Create the human request only after verification and every required agent
release review have accepted.

Creation validates:

- the manifest requires or inherits a human gate;
- candidate SHA and tree hash are both present;
- configured reviewer groups are resolved only in the snapshotted Admin
  directory;
- checklist IDs are unique;
- no current request already exists for the release attempt.

When resolution produces at least one eligible user, manual mode creates a
`pending` request with instructions. Preview mode creates a `preparing` request
and emits one idempotent preparation event.

If configured groups are missing or resolve to zero eligible users at this
boundary, still create or reuse the request for the pinned attempt, put it in
`blocked`, and open one `TeamBlocker` sourced from that request with the
assignment-unavailable reason. Do not throw an unrecorded sequencing error,
skip the gate, or create a replacement release attempt.

Run idempotent assignment reconciliation before notification, decision,
reassignment, and resume operations and on each release-gate reconciliation
pass. A low-frequency reconciliation of nonterminal human requests detects
directory membership or permission changes even when no reviewer acts. A
decision-eligible user for this purpose is an active current assignee who still
belongs to a configured group or has an explicit audited assignment and has the
directory-scoped decision permission. If no such user remains, move or retain
the same request in `blocked` and create or reuse the same assignment blocker.
This transition is not a human `blocked` decision: no reviewer decision is
fabricated.

An authorized audited reassignment may add a current eligible user while the
request is blocked. It increments `assignment_generation`, reruns assignment
reconciliation, resolves the blocker when eligibility is restored, and returns
the same request to `preparing` when target preparation is outstanding or
`pending` otherwise. The candidate, request, and release attempt remain
unchanged, and the new assignment generation may issue fresh deduplicated
notifications.

### Human decisions

An authorized decision transaction:

1. requires a real authenticated session;
2. checks `team.human_reviews.decide` in the request directory;
3. checks the current eligible assignment;
4. verifies continued group membership or explicit audited reassignment;
5. checks privileged-operation confirmation;
6. checks optimistic revision and request idempotency;
7. validates candidate SHA/tree and request state;
8. stores the decision;
9. applies the release, finding, blocker, and project cascade atomically.

For the initial one-approval policy:

- one eligible `approved` decision passes the gate;
- `changes_requested` immediately terminates the candidate attempt;
- `blocked` is nonterminal;
- late or concurrent terminal decisions cannot change the result.

### Blocking and resumption

A human `blocked` decision or preparation failure:

- leaves the request pinned to the same candidate;
- opens or reuses a `TeamBlocker` sourced from the human request;
- sets a stable reason code and safe explanation;
- routes first to the project lead, then Team manager, governance, or operator;
- keeps the project blocked.

Assignment unavailability follows the same pinned-attempt rule but uses its
dedicated blocker reason and directory-scoped reassignment route. It cannot be
resolved merely by marking the blocker closed: request-aware recovery must
first prove that at least one current user is decision-eligible.

Resolving the blocker invokes request-aware recovery:

- manual testing returns to `pending`;
- workflow preparation increments `preparation_generation`, returns to
  `preparing`, and emits a new idempotent preparation request;
- no recovery path creates a new release attempt unless candidate content
  changes.

### Successor closure

The existing release request command accepts an optional
`supersedes_release_package_id`.

For a human-correction successor it validates:

- the predecessor ended in human `changes_requested`;
- correction and triage tasks are resolved;
- every carried finding has an accepted proposed resolution and evidence;
- correction deliverables are current, accepted, hierarchy-valid, and not
  already part of predecessor scope;
- the predecessor candidate is available as the baseline;
- the new candidate tree differs from the predecessor tree.

The successor manifest snapshots:

- predecessor ID and candidate baseline;
- original accepted release scope;
- accepted correction deliverables;
- recursive contribution lineage;
- every carried finding;
- proposed resolutions and evidence;
- the effective release policy.

All configured verification and agent review gates rerun. A human gate always
reruns while findings remain open. On human approval, the decision explicitly
confirms all carried finding IDs, those findings become resolved, and canonical
publication may proceed.

## Project status and blocking sources

Keep `Project.status`, but derive it through one project-status service that
consumes canonical assessments rather than independently reinterpreting task
states.

`BLOCKED` includes:

- an intrinsically blocked or invalid task;
- an open project-affecting `TeamBlocker`;
- a blocked deliverable or agent review;
- blocked release verification or release review;
- blocked human preparation or review;
- an agent execution stall affecting project tasks.

`ACTIVE` includes:

- actionable or waiting tasks;
- pending acceptance or reviews;
- pending human review;
- human-requested correction work;
- open human findings;
- successor release preparation.

`COMPLETED` requires:

- accepted project goals;
- every task validly resolved;
- no active deliverable acceptance or agent review;
- no active release attempt;
- no pending or blocked human request;
- no open human finding;
- no active project-affecting blocker.

`project_blocking_sources` should include canonical blocker IDs, reason codes,
source records, affected tasks, current owner/route, and exact next action.
Dependency-waiting records point to the upstream blocker rather than creating a
downstream copy.

## Dispatch, multi-worktree preparation, and no-progress

### Scope construction

For an ordinary persistent-agent dispatch, include every task that is
`actionable` in the canonical assessment and:

- is owned by the agent;
- belongs to the agent's Team and project authority;
- is not suppressed by an open execution-stall incident.

The wake cause does not narrow ordinary agent-wide scope.

Blockers, agent reviews, and inbox items retain their own scope lists. They are
not placed in `task_ids` simply because they mention another agent's task.

The task, blocker, review, and inbox scope lists contain only records actually
delivered to and assigned to the agent by that dispatch. Project and repository
scope contains only resources required by those delivered record scopes. A
relationship to a scoped task does not itself put a blocker, review, or inbox
item into scope.

Consequences remain source-specific:

- task no-progress policy concerns `task_ids`;
- blocker progress, delegation, and escalation concern `blocker_ids`; and
- agent reviews and inbox items use their own acceptance, retry, and escalation
  policies against `review_request_ids` and `inbox_item_ids`.

Before a command or source-specific consequence, revalidate that the record is
in the corresponding scope list and remains assigned, open, and actionable. A
newly created task does not silently widen the current dispatch; it is eligible
for a later dispatch. Progress on any validly scoped work may still reset the
separate agent-wide no-progress counter; that reset does not authorize a
cross-kind consequence.

### Preparation and leasing

Use this order:

1. compute and persist immutable scope with status `preparing`;
2. derive every required project, repository, and task worktree;
3. prepare all repositories and task worktrees;
4. persist fresh dispatch-bound Git contexts;
5. mark the dispatch `leased`;
6. only then resume the agent.

The runtime may reserve the agent while preparation is in progress, but Team
commands require the referenced `TeamDispatch` to be `leased`.

If any preparation fails:

- mark the dispatch `failed`;
- finalize any contexts created for it;
- release the runtime reservation;
- expose the exact preparation error;
- schedule normal retry where appropriate;
- never omit a scoped task or mutate the scope.

Prepare every repository required by the scope. Do not choose a project from
incidental list order.

Retain a deterministic, stable physical worktree path for the same durable
task/repository checkout identity across dispatches and recreation after
cleanup. Each dispatch still receives a fresh dispatch-bound Git context ID;
stable physical paths and fresh authorization contexts are separate
requirements.

### Prompt and UI mapping

For every prepared task show:

| Task | Project | Repository | Worktree | Branch | Context |
| --- | --- | --- | --- | --- | --- |

Label the base checkout as a control checkout. Tell the agent to enter the
worktree for the task before editing. Durable submission validation continues
to reject a context belonging to another task, agent, branch, or dispatch.

### Agent-wide execution stall

The no-progress counter remains agent-wide. When it reaches the configured
limit:

1. create or reuse one open blocker with
   `source_kind = agent_execution_stall` and `source_id = agent_id`;
2. append an event for the exhausted dispatch, timestamp, and scoped tasks;
3. merge current affected task IDs into the blocker;
4. route through the agent's reporting line, Team manager, governance, or
   operator;
5. send coalesced notices to distinct accountable parent-task owners;
6. suppress ordinary task dispatch for tasks still owned by the stalled agent;
7. leave those tasks' intrinsic states unchanged;
8. reset the allowance only when the stall blocker resolves.

Progress on any scoped record continues to reset the agent-wide counter.
Neglect of one task while other scoped work progresses is starvation/aging, not
this incident, and remains separate future policy work.

### Source-aware blocker routing

Extend the existing route calculation:

- task, deliverable, and task-review blockers walk distinct parent-task owners
  before the Team manager;
- execution stalls walk the agent reporting line;
- release and human testing/preparation blockers start at the project lead;
- human assignment-unavailable blockers route to the directory-scoped
  `admin.owner` or authorized review manager, while retaining project-lead
  visibility;
- existing review, governance, explicit, and terminal routes retain their
  source-appropriate behavior;
- governance and operator remain terminal fallbacks.

Do not build another routing engine. Compute one ordered, de-duplicated route
for the blocker source and let existing transfer/delegation/event mechanics
advance through it.

## Physical task-worktree cleanup

Implement physical cleanup immediately after task resolution is trustworthy.
It is not deferred UI polish.

Enqueue a cleanup candidate when:

- a task reaches a valid resolved disposition;
- a task is reassigned away from the session owning its checkout;
- an earlier cleanup failure becomes retryable.

Run a periodic reconciler for existing and failed candidates. Use a short
configurable recovery grace period.

Before deletion:

- acquire a session/repository cleanup lock;
- reread Team state after locking;
- confirm the task remains resolved or permanently reassigned;
- confirm no preparing or leased dispatch references it;
- confirm no active Git context references it;
- validate task, branch, repository, and expected worktree path;
- reject tracked modifications;
- reject non-ignored untracked files;
- reject an unexpected `HEAD`;
- confirm any required terminal deliverable retained or pushed the expected
  commit.

Ignored caches and generated files are inventoried for audit and size reporting
but may be removed after source-safety checks pass.

On success:

1. immediately before mutation, repeat the Team-state, active-dispatch,
   Git-context, task, branch, path, dirty-file, `HEAD`, and retained-commit
   checks while holding the cleanup lock;
2. remove the worktree through the selected execution backend;
   use forced Git removal only after proving every remaining file is
   ignored/generated;
3. prune the Git worktree registration;
4. record task, branch, path, timestamp, ignored-file inventory, and bytes
   reclaimed in the existing action/audit surface;
5. only then prune obsolete unreferenced Git context metadata.

Dirty or unexpected work creates a visible recovery item containing the exact
task, repository, worktree path, branch, failed safety check, and timestamp. It
is never silently deleted, and cleanup failure does not reopen accepted work.

Whole-session deletion remains the final boundary. Long-idle clean worktree
eviction remains conditional future cache behavior.

## Human notifications, preview preparation, and post-release feedback

### Notifications

When a human request becomes reviewable, emit:

```text
team.release.human_review_requested
```

A retryable consumer publishes existing Admin user notifications. Deduplicate
using:

```text
human-review-request-id + recipient-user-id + assignment-generation
```

The existing notification store can encode request ID and assignment generation
in its source ID. Notification state is not review state.

### Preview preparation

Preview preparation is a later independent slice.

When enabled, the release sequencer emits:

```text
team.release.human_review_preparation_requested
```

The workflow receives only the pinned request and candidate identity. It returns
safe target metadata through an authenticated, idempotent internal command.

The result command:

- verifies request ID, candidate SHA/tree, and preparation generation;
- rejects secret-bearing fields;
- records URL/alias, expiry, instructions, and evidence;
- moves the same request from `preparing` to `pending`;
- blocks the same request on preparation failure.

Credentials remain in host-owned workflow/provider state.

### Post-release feedback

“Report regression or adjustment” on a published release calls the same
case-and-task intake service with a different source adapter.

Atomically:

- preserve the published package unchanged;
- create or reuse a deterministic post-release `TeamCase`;
- capture authenticated reporter, report, and evidence;
- create one project-lead triage root.

Triage may:

- close an invalid, duplicate, or already-resolved report with evidence;
- create independent correction roots;
- request a separately controlled rollback;
- authorize a successor release linked to the published package and case.

Reporting never rolls back, mutates the published package, or automatically
creates a successor. Rollback remains a separate controlled operation.

## API, MCP, event, and authorization surfaces

### Human-facing API

```text
GET  /human-review-requests?assigned_to=me&state=pending
GET  /human-review-requests/{id}
POST /human-review-requests/{id}/decisions
POST /human-review-requests/{id}/reassign
POST /human-review-requests/{id}/resume
POST /release-packages/{id}/report-issue
```

Internal workflow surface:

```text
POST /human-review-requests/{id}/preparation-result
```

Mutations require optimistic revision checks and idempotency keys.

Add `team.human_reviews.decide` to the Admin RBAC permission catalog.
`admin.owner` covers reassignment and management initially. Do not add broad
read or delegated-manage permissions until a concrete role requires them.
Current assignees may read their requests without a separate broad-read
permission.

The human endpoints use a strict identity helper rather than the current
operator helper's generic `admin` fallback. A request without a real session
principal cannot decide a review.

### Agent MCP

Existing agent read payloads expose:

- compact human-gate state;
- relevant carried findings;
- linked case and correction-task IDs;
- exact next action.

There is no human-decision mutation tool.

Update task/deliverable tools to support:

- root provenance;
- cancellation through an authorized command;
- source deliverable IDs;
- accepted evidenced-noop deliverables;
- blocker reason codes;
- dispatch-scope enforcement.

`team_report_blocker` becomes the normal atomic way to put task work into
`blocked`. `team_update_blocker resolve` performs source-aware recovery.
Direct task state edits into or out of `blocked` should be rejected after
cutover except through explicit operator repair.

### Events

Only effects outside the Team transaction need external events:

```text
team.release.human_review_requested
team.release.human_review_preparation_requested
```

Human decisions, findings, cases, tasks, blocker changes, and project status are
already one atomic Team transition and need no duplicate event unless a real
subscriber is later introduced.

This event minimization does not weaken the idempotency requirement for the
existing Team inbox and work-due wake consequences created by
`changes_requested`.

## UI, guidance, and documentation

### Task and release UI

Enhance existing surfaces before adding new top-level visualizations.

Task board and detail:

- recursive hierarchy and indentation;
- actionable versus waiting readiness;
- unresolved descendant count and exact IDs;
- integration readiness and contribution coverage;
- direct acceptance and formal-review state;
- canonical blocker ID, reason code, summary and explanation, blocked-since
  time, source kind and source record, accountable owner and active delegate,
  route and current escalation level, latest event, follow-up, and next action;
- dispatch and task-to-worktree mapping;
- invalid legacy combinations and repair action.

Release detail:

- project identity and release-attempt lineage;
- exact candidate SHA/tree and release branch;
- included top-level deliverables, release notes, and recursive contribution
  graph;
- automated verification;
- agent review decisions and their review criteria;
- human request and decision history;
- carried findings and resolution proposals;
- manual or prepared test instructions, preview target, and expiry;
- human acceptance checklist;
- evidence and attachments;
- publication state.

Render this context from the existing project, release manifest, review,
requirement, target, and evidence records. Do not introduce copied presentation
state merely to populate the human-review detail.

Start with a “My Human Reviews” filter and human-review detail on the release
view. Add a dedicated queue only when volume justifies it.

Primary human actions:

- Approve;
- Request changes;
- Blocked.

### Agent guidance

Parent owners are told to:

- create constituent children;
- model their own substantive work as a self-owned child;
- remain quiet while children are merely running;
- supervise child blockers and handoffs;
- decide child handoffs when they are the acceptance recipient;
- integrate exact accepted direct-child sources;
- submit one consolidated parent deliverable;
- resolve, reassign, cancel, or escalate unresolved children.

Child owners are told how to:

- finish support work;
- submit an assessable delivered or evidenced-noop contribution;
- request specialist review;
- report a blocker with a stable reason.

Planner guidance distinguishes child containment from independent root creation
and provenance.

Increment the Team guidance version. Refresh:

- managed guest `/home/agent/AGENTS.md`;
- stored persistent-session metadata;
- future wake context.

Updating source text only for newly created sessions is not a complete rollout.

### Documentation

Update the canonical Evander manual chapters and generated publications with:

- hierarchy, dependencies, provenance, and cases;
- resolution and readiness;
- contribution composition;
- role-scoped root creation;
- dispatch scope and worktree mapping;
- blocker routing;
- human release gates and authenticated decisions;
- changes-requested correction work;
- successor release lineage;
- preview preparation;
- post-release reporting and separate rollback;
- worktree cleanup and recovery;
- operator repair of invalid legacy state.

Include one complete APP walkthrough covering the combined lifecycle.

## Migration and compatibility

### Persistence

Add SQLite migrations for:

- task cancellation and origin;
- deliverable sources and optional completion;
- blocker reason/actor/affected tasks;
- durable dispatches;
- release predecessor lineage and manifest additions;
- human requests, decisions, and findings.

Add the corresponding `TeamState` collections, key validation, foreign-key
validation, normalized-table hydration, and round-trip repository tests.

Do not use opaque JSON metadata for relationships that commands must validate
or query.

### Existing task state

Migration adds empty optional fields without changing historical acceptance or
review records.

Inventory and surface:

- accepted or finished parents with unresolved descendants;
- outcome tasks finished without accepted output;
- parent deliverables missing required direct-child composition;
- invalid parent/dependency references;
- terminal tasks with active Git contexts or worktrees.

Business contradictions become `TaskAssessment.invalid`; they do not make the
entire Team read payload unusable. New release or publication is refused until
an authorized repair, cancellation, or acceptance decision resolves them.

Do not fabricate historical human or agent approvals. Operator repair commands
must be reason-required and audited.

### Existing releases

- Projects without human policy retain current behavior.
- Open and historical releases are not retroactively gated.
- New attempts snapshot the new manifest shape.
- A successor carrying human findings retains its inherited human obligation.
- Existing agent review and verification retry lineage remains valid.

### Active dispatches and worktrees

Before enabling durable dispatch scope:

- drain or expire active dispatch leases;
- finalize their Git contexts through supported lifecycle code;
- start new work only under a `TeamDispatch`;
- do not invent scope for an already running agent.

Run worktree cleanup first in inventory/dry-run mode. Enable deletion only after
path, branch, dirty-state, active-context, and retained-commit checks pass on
both execution backends.

## Implementation ownership

Keep domain rules in narrow services and leave API/provider adapters thin.

| Responsibility | Primary implementation seam |
| --- | --- |
| Models, enums, snapshots, payload validation | `src/evander_team_addon/models.py` |
| Canonical recursive assessment | new `services/domain/task_assessment.py` |
| Task creation, update, cancellation, and provenance | `services/commands/task_creation_commands.py`, `task_update_commands.py`, and `domain/case_task.py` |
| Contribution submission and acceptance | `services/commands/deliverable_*` and `services/domain/deliverables.py` |
| Agent review cascades | `services/domain/review.py` |
| Blocker source lifecycle and routing | `services/domain/blockers.py` and blocker commands |
| Project completion and blocking sources | `services/domain/project_status.py` |
| Release construction and successor baselines | `services/domain/release_packages.py` and `git_promotion.py` |
| Ordered release gates | `services/domain/release_gates.py` |
| Human request, decision, finding, and case transition | new `services/domain/human_reviews.py` behind an Admin-facing service |
| Dispatch scope, leasing, and no-progress | `services/runtime_service.py` and `services/domain/wake.py` |
| Repository/worktree preparation and cleanup | provider execution code plus `domain/git_work_contexts.py` and a narrow cleanup service |
| SQLite migrations and normalized persistence | `sqlite_store.py`, `persistence.py`, and `repository.py` |
| Authenticated routes and read projections | `api.py` and `services/read_service.py` |
| Admin RBAC and notifications | `evander/admin/web/rbac.py`, `user_notifications.py`, and `admin/composition.py` |
| Team UI | `ui_static/team-ui.js`, `team-ui.css`, and existing release/task views |
| Workflow preparation integration | Team outbox, workflow pack/catalog, and effect result adapter |

Wire Admin-owned notification and workflow-result dependencies through
`build_admin_runtime`/`AdminRuntimeComponents`; do not construct parallel
runtime services in route handlers.

## Unified implementation order

Every slice includes focused tests, documentation for exposed behavior, and a
`CHANGELOG.md` entry when it implements a feature or bugfix.

### Slice 1: Canonical task lifecycle foundation

- add `TaskAssessment`;
- add `cancelled`, cancellation disposition, and provenance;
- enforce manager/planner root authority, unauthorized rejection, proposal-only
  behavior, and creator visibility for interactive provenance;
- keep the human-correction provenance exception narrow and system-owned;
- make evidenced no-op an accepted deliverable path;
- enforce recursive resolution at finish, submission, acceptance, review, and
  release boundaries;
- expose invalid legacy combinations without breaking Team reads;
- route all task read models through the assessment.

### Slice 2: Composition, parent transitions, and canonical blockers

- add direct-child `source_deliverable_ids`;
- validate contribution coverage and top-level release scope;
- add parent-owner handoff and integration-ready transitions;
- remove periodic wakes for waiting parents;
- add blocker reason codes and source-aware reconciliation;
- reconcile linked blockers atomically during task reassignment and
  cancellation;
- add hierarchy-aware blocker routes;
- expose blocker source, owner/delegate, route, and escalation level in task
  details;
- update project blocking sources.

### Slice 3: Safe physical worktree cleanup

- add backend-neutral cleanup candidates and reconciler;
- remove resolved/reassigned clean worktrees after grace;
- repeat safety validation immediately before mutation;
- preserve dirty and unexpected work as recovery items with exact path, branch,
  and failed-check evidence;
- record reclaimed bytes and prune metadata only after physical removal;
- reconcile existing worktrees.

### Slice 4: Durable dispatch scope and execution stalls

- add `TeamDispatch`;
- scope only actually delivered and assigned tasks, blockers, reviews, and
  inbox items, then derive only their required projects and repositories;
- authorize commands against immutable scope;
- keep task, blocker, review, and inbox consequences source-specific;
- prepare all repositories before leasing;
- retain deterministic stable physical worktree paths while issuing fresh
  dispatch-bound Git context IDs;
- expose task/worktree mapping;
- create one agent execution-stall incident on exhausted no-progress;
- use dispatch scope for consequences and affected-parent notices.

### Slice 5: Human records, policy, identity, and approved path behind disabled policy

- add policy/manifest fields and human persistence;
- add release predecessor lineage;
- add strict human identity and RBAC;
- replace release branching with the unified gate sequencer;
- create requests only after earlier gates;
- implement candidate-pinned approval and exactly-once publication;
- add read-only release-detail presentation.

Human policy remains unconfigurable in operator UI.

### Slice 6: Changes requested, blockers, and successor closure behind disabled policy

- add atomic findings/case/correction-task intake;
- route correction roots with hierarchy provenance;
- implement blocked/resume against the unchanged candidate;
- add resolution proposals and carried findings;
- build successors from predecessor baseline plus accepted corrections;
- rerun all gates;
- resolve findings only on human approval;
- integrate project status.

### Slice 7: Enable the core human gate

- expose bounded policy configuration;
- add My Human Reviews and detail/actions with the complete project, release
  notes, review criteria, test instructions, evidence, and attachment context;
- add retryable notifications and deep links;
- add audited reassignment;
- reconcile active assignments after directory membership or permission changes;
- validate the complete changes-requested-to-successor loop end to end.

Do not expose human policy before this slice succeeds. A gate that merely
terminates a release would reproduce the original lifecycle gap.

### Slice 8: Preview preparation

- add explicit workflow preparation mode;
- emit preparation requests;
- authenticate and validate idempotent results;
- handle target expiry, failure, and same-candidate retry;
- support manual mode throughout.

### Slice 9: Post-release feedback

- add report action on published packages;
- reuse case/task intake;
- add project-lead triage;
- support explicit correction roots and optional successor authorization;
- keep rollback separate.

### Slice 10: Conditional advanced policy and scale work

Only when justified by a concrete need:

- multiple independent human requirements;
- configurable unique-user approval quorums;
- reusable human-review profiles;
- higher-volume finding-to-owner grouping;
- delegated review management or emergency waivers;
- dedicated top-level human queue;
- task starvation/aging policy;
- long-idle clean-worktree eviction;
- first-class draft task proposals.

If this slice adds an approval quorum, its acceptance tests must prove:

- one authenticated user counts at most once for one request;
- threshold evaluation and the resulting terminal transition are atomic under
  concurrent decisions;
- `changes_requested` is an immediate veto of that candidate attempt; and
- decisions arriving after a terminal result cannot change it, publish, or
  create rework.

If an emergency waiver is ever justified, it is a separate highly privileged,
reason-required manifest record. It must never masquerade as human approval.

## Acceptance coverage

Tests accompany the owning slice.

### Task hierarchy and composition

- three-level recursive containment;
- support `finished`, outcome `accepted`, accepted evidenced no-op, and
  authorized cancellation;
- blocked and rejected descendants remain unresolved;
- finish, submit, direct accept, formal accept, release, and publication refuse
  unresolved descendants;
- invalid historical parent state remains readable and blocks new publication;
- direct-child contribution coverage;
- nested transitive lineage without copied child state;
- release scope refuses both an aggregate and a source already contained in it;
- an authorized manager creates and assigns an independent root directly;
- a project-scoped planner with `create_root_task` creates an independent root;
- root creation without the required authority is rejected;
- an `originating_task_id` must be same-Team, same-project, and visible to the
  creator, records provenance, and does not gate either task;
- proposal-only work confers no authority and creates no root until an
  authorized actor materializes it;
- the constrained human-correction system path creates deterministic roots
  without granting root authority to an agent.

### Supervision and blockers

- waiting parents do not poll;
- parent wakes for blocker, handoff, rejection, resolution, and
  integration-ready transition;
- repeated transitions coalesce;
- task blocking and blocker creation are atomic;
- blocker resolution restores the assessment-consistent source state;
- task reassignment and cancellation reconcile linked task-local and shared
  blockers atomically without changing unrelated blockers;
- child blockers walk distinct parent owners;
- human testing/preparation and release blockers start at the project lead;
- assignment-unavailable blockers route to directory-scoped reassignment
  authority while retaining project-lead visibility;
- execution stalls walk the reporting line;
- unrelated activity does not count as blocker progress;
- task details show the blocker source record, accountable owner, active
  delegate, route, and current escalation level;
- dependency waiters display the upstream reason without a copied blocker.

### Dispatch and worktrees

- cause does not narrow ordinary agent-wide scope;
- task, blocker, review, and inbox scope lists are immutable, command-specific,
  and contain only records actually delivered and assigned by that dispatch;
- project and repository scope contains only resources required by those
  delivered record scopes;
- task, blocker, review, and inbox consequences operate only against their
  corresponding scope lists even though progress on any scoped work may reset
  the agent-wide no-progress counter;
- stale scope is revalidated;
- every required repository is prepared or the dispatch fails;
- only actionable scoped tasks receive worktrees;
- multiple projects and worktrees map unambiguously;
- a durable task/repository checkout retains its deterministic stable physical
  path while each dispatch receives a fresh Git context ID;
- cross-task and cross-dispatch context submission remains rejected;
- no agent command is accepted before dispatch status is `leased`;
- one stall blocker covers an exhausted agent-wide dispatch;
- task states remain unchanged by the stall;
- resolved/reassigned clean worktrees are removed;
- active contexts, tracked changes, non-ignored files, and unexpected commits
  prevent deletion;
- ignored generated files may be reclaimed after safety checks;
- cleanup repeats its safety validation immediately before physical mutation;
- a refused cleanup records the exact path, branch, and failed safety check;
- cleanup is idempotent and periodically retried;
- physical removal precedes metadata pruning.

### Human gate and identity

- human-only, agent-review-only, and combined policies are independently valid;
- when combined, human review is not created before all agent reviews accept;
- exact SHA and tree pinning is enforced;
- an agent, unauthorized user, spoofed client actor, or generic unauthenticated
  fallback cannot decide;
- reviewer eligibility, current group membership or explicit assignment,
  directory scope, permission, and privileged confirmation are all enforced;
- a policy known to have no eligible reviewer is rejected at configuration
  time;
- an assignment that becomes empty at request creation or while a request is
  active creates one assignment blocker, prevents bypass, and exposes audited
  reassignment as the durable recovery action;
- audited reassignment resolves that blocker and resumes the same request,
  candidate, and release attempt with a new assignment generation;
- repeated decisions are idempotent;
- concurrent terminal decisions cannot publish or create rework twice;
- notification retry deduplicates one assignment generation;
- reassignment increments generation and may notify the same user again;
- human-review detail presents project identity, release lineage, candidate
  identity, deliverables, release notes, verification, agent-review decisions
  and criteria, carried findings, test instructions and target, checklist,
  evidence, and attachments;
- existing projects and releases retain legacy behavior without retroactive
  gating.

### Changes requested and successor release

- request changes requires actionable findings;
- the complete transition creates exactly one decision, finding set, case, and
  deterministic task, inbox-item, and logical wake/work-due set;
- command retries and concurrent equivalent decisions do not duplicate any
  finding, case, task, inbox item, or wake/work-due consequence;
- transaction failure creates none of them;
- no canonical publication is queued;
- accepted historical work is unchanged;
- correction tasks are roots, not children of accepted historical tasks;
- specific findings route to the original owner with provenance;
- ambiguous findings route to project-lead triage;
- rework completion proposes but does not resolve findings;
- successor baseline is the predecessor candidate;
- only accepted corrections are applied;
- unchanged candidate trees are rejected;
- every applicable gate reruns;
- carried human obligation cannot be removed by a policy edit;
- only a human approval confirming all carried findings resolves them;
- project completion is impossible while requests, findings, correction tasks,
  or successor work remain active.

### Blocked review and preview

- human `blocked` preserves request, SHA, and tree;
- zero decision-eligible reviewers creates or reuses an
  assignment-unavailable blocker against the same request and attempt;
- audited reassignment is required before assignment-blocker recovery and
  increments the notification generation;
- resolving manual-test blockage returns the same request to pending;
- resolving preparation failure retries the same request with a new generation;
- preparation result must match request, candidate, and generation;
- preparation result is idempotent;
- secret-bearing target metadata is rejected;
- expiry blocks the same candidate and can be recovered.

### Post-release feedback

- reporting creates exactly one linked case and triage root per idempotency key;
- published package and commit remain unchanged;
- triage can close with evidence or create correction roots;
- successor authorization is explicit;
- reporting and correction do not imply rollback;
- rollback remains a separate controlled action.

### Conditional advanced human policy

If approval quorum support is implemented:

- one authenticated user counts at most once;
- concurrent threshold evaluation is atomic;
- `changes_requested` immediately vetoes the candidate attempt; and
- late decisions cannot alter any terminal result.

### Representative APP end-to-end

Keep one combined, representative end-to-end flow:

1. create an aggregate APP outcome task;
2. decompose it three levels, including a self-owned implementation child;
3. accept leaf contributions and integrate direct-child sources;
4. submit and accept the root deliverable;
5. create a pinned release candidate;
6. pass verification and agent release review;
7. request human changes;
8. atomically create findings, case, and correction roots;
9. accept decomposed correction contributions;
10. create a successor from the predecessor candidate;
11. block human testing on preview readiness and resume the same candidate;
12. approve the successor and resolve carried findings;
13. publish exactly the approved SHA;
14. reclaim eligible task worktrees without deleting dirty recovery work.

Use focused model, command, repository, runtime, API, release-gate, Git-effect,
RBAC, notification, and UI tests for permutations. Do not turn the representative
flow into one test for every branch.

## Rollout checks

Before enabling the combined behavior:

- run the focused Python suites for Team models, repository, runtime, API,
  release gates, Git effects, workflow packs, and Admin RBAC;
- run the full Python suite and record unrelated pre-existing failures;
- run focused browser/UI checks with explicit timeouts;
- verify SQLite migration and normalized-table round trips from a current live
  snapshot copy;
- dry-run the worktree reconciler and inspect every proposed deletion;
- verify existing persistent sessions receive the new guidance version;
- verify human policy is absent until the successor loop is complete;
- verify outbox retries do not duplicate notifications or preparation;
- verify a human-gated candidate cannot reach canonical publication through any
  Admin, MCP, worker, retry, or recovery path;
- rebuild the canonical Evander user manual and run its generated-output check;
- add changelog entries in every implementation slice.

The release is ready only when the combined APP journey passes and current
ungated projects retain their existing behavior.

## Explicit non-goals

This plan does not introduce:

- transferable or one-shot root-task grants;
- a fake parent-child edge for independent work;
- persisted task waiting state in addition to lifecycle state;
- separate cancelled, superseded, obsolete, and waived task states;
- copied child contribution snapshots on parents;
- a second blocker/escalation engine;
- one automatic blocker per task for agent-wide no-progress;
- task-specific dispatch while ordinary dispatch remains agent-wide;
- persistent shell task selection;
- migration from the existing control checkout to a bare control repository or
  equivalent worktree anchor unless and until that checkout causes a
  demonstrated safety or operational failure;
- silent dirty-worktree deletion;
- a synthetic human agent or agent-callable human approval;
- a release journey entity;
- retroactive human gates;
- inherited approval across a changed candidate;
- automatic rollback from human feedback;
- Team-owned deployment credentials;
- advanced quorum/profile/waiver machinery without demonstrated need.
