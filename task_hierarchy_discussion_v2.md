# Team Add-on Hierarchical Task Execution Proposal, Revised

Status: design proposal only; no implementation has been made.

This revision keeps the lifecycle-containment objective of the original proposal
while reducing duplicated state and special-purpose mechanisms. In particular, it:

- removes delegated one-shot root-creation grants;
- treats independent task creation as role-scoped authority plus provenance;
- centralizes task resolution and readiness in one derived model;
- records only minimal contribution lineage and derives the rest from canonical
  deliverable records;
- reuses the existing blocker escalation machinery;
- represents agent-wide no-progress as one execution-stall incident rather than
  one blocker per task;
- makes physical task-worktree cleanup an early implementation priority.

## Objective

Make deep task decomposition a reliable execution model:

1. A parent owner is accountable for a combined outcome.
2. The outcome is decomposed into constituent child tasks.
3. Child owners complete and hand off their contributions.
4. The parent owner integrates the accepted contributions.
5. One consolidated parent deliverable proceeds through the appropriate final
   acceptance and review.
6. Waiting parents remain quiet until a meaningful event requires supervision or
   integration.
7. Stalled work is routed to an accountable resolver with a useful explanation.
8. Task worktrees are reclaimed safely instead of accumulating for the lifetime
   of a persistent session.

The Team Add-on already represents task trees, dependencies, task-scoped
deliverables, direct-superior handoffs, formal reviews, blockers, Git work
contexts, and task worktrees. The missing piece is one consistent lifecycle
connecting those facilities.

## Design principles

### 1. Parent-child means lifecycle containment

`parent_task_id` means that the child is a constituent part of the parent. The
parent cannot resolve while any child or descendant remains unresolved.

A task is resolved for hierarchy purposes only when it reaches the disposition
appropriate to its kind:

- a support or coordination task is resolved when it is `finished`;
- an outcome task is resolved when its delivered result, including an accepted
  negative or no-action result, is `accepted`;
- obsolete work is resolved when it is explicitly `cancelled` with an authorized
  reason and, when applicable, a successor task reference.

`blocked` and `rejected` do not resolve a task for its ancestors. They require
recovery, replacement, or explicit cancellation.

Every child is completion-relevant. Work that may continue independently after a
parent resolves must not be modeled as a child.

If task B can continue without affecting the completion of task A, task B is not a
child of task A.

### 2. Derive resolution and readiness once

The system should have one canonical derived task assessment used by commands,
wake selection, dispatch scope, Git preparation, API payloads, and UI.

At minimum, derive:

- `resolution_status`: whether the task is resolved for its ancestors and why;
- `readiness`: one of `actionable`, `waiting_on_children`,
  `waiting_on_dependencies`, `waiting_for_acceptance`, `waiting_for_review`,
  `blocked`, `resolved`, or `invalid`;
- recursive unresolved descendant IDs;
- integration readiness for aggregate parents.

`waiting_on_children` is derived readiness, not another persisted task state.
Persisting both a lifecycle state and a second waiting state would create
contradictory sources of truth.

### 3. Keep distinct relationships distinct

| Relationship | Meaning |
| --- | --- |
| Parent-child | B is a constituent part of A and gates A's completion. |
| Dependency | A cannot proceed until B reaches the required state. |
| Originating task | Work on A caused independent task B to be created. |
| Deliverable contribution | B's exact accepted output was incorporated into A's product. |

These relationships may coexist, but one must not substitute for another.

In particular, a planning task such as “Create task X” does not make X its child.
The planning task resolves when X has been created; X remains an independent root
and may record the planning task as its origin.

### 4. Root-task authority follows planning responsibility

Ordinary root creation remains protected by `create_root_task`. Do not introduce a
one-shot delegation-grant subsystem.

Use the following simpler patterns:

1. If a manager already knows the required root task, the manager creates it
   directly and assigns it.
2. If an agent is expected to define and organize independent project work, give
   that agent's project-scoped planner or lead role `create_root_task`.
3. If an agent may propose but not create independent work, the agent submits a
   proposal and an authorized manager or lead materializes it.

An independently created root may carry `originating_task_id` for durable
provenance. That field confers no authority, creates no completion dependency, and
must refer to a same-team, same-project task visible to the creator.

If proposal-and-approval becomes common enough to deserve first-class support,
introduce a draft task proposal rather than a transferable creation capability.

### 5. Wake cause and dispatch scope are different

A task, blocker, review, inbox item, or system event may cause an agent to wake.
That cause does not implicitly narrow the work the scheduler assigned.

The scheduler owns dispatch scope and persists the exact records the agent was
expected and authorized to advance. The agent cannot supply or widen its own
scope.

### 6. Reuse canonical records

New relationships should point to existing durable records instead of copying
their state.

For example:

- a parent contribution points to a child deliverable ID instead of copying its
  acceptance decision, Git SHA, artifacts, and outcome snapshots;
- a blocked task points to a `TeamBlocker` instead of copying blocker ownership
  and escalation fields onto the task;
- a cleanup result uses the existing action or audit record surface instead of a
  parallel worktree history model.

## Proposal 1: Enforce hierarchical resolution and completion

Make the task tree a real completion contract.

### Canonical derived assessment

Add one shared task-assessment function that:

- evaluates task-kind-specific resolution;
- walks descendants recursively;
- evaluates dependency readiness;
- distinguishes actionable work from external waiting;
- identifies contradictory legacy states;
- reports the exact unresolved child, dependency, review, acceptance, or blocker
  IDs responsible for waiting.

All lifecycle decisions must call this function rather than reimplementing parts
of it.

### Completion and delivery boundaries

Refuse:

- finishing or accepting a parent while any descendant is unresolved;
- final parent-deliverable submission while required direct-child contributions
  are unresolved or missing;
- task completion or deliverable submission while dependencies are unsatisfied;
- direct acceptance or formal-review acceptance that would produce an accepted
  parent with unresolved descendants;
- release or publication requests based on a task that violates hierarchy
  invariants.

The checks must exist in the shared domain boundary used by MCP commands, Admin
API routes, operator actions, and review cascades.

### Obsolete work

Add one terminal task state, `cancelled`, with one required terminal disposition:

- `reason_code`;
- human-readable reason;
- authorizing actor;
- optional `successor_task_id`.

Reason codes may distinguish obsolete, superseded, duplicate, or explicitly
waived work without adding separate lifecycle states for each.

Cancellation authority must be limited to the task owner where policy permits,
the accountable parent owner, the project lead, the Team manager, or an operator.
Cancelling a required child is an auditable decision, not a silent way to bypass
parent completion.

### Supervisor-owned implementation

If a parent owner will implement a substantive slice alongside subordinates, that
slice is an explicit self-owned child task.

The aggregate parent represents coordination, integration, and the final combined
outcome. It does not remain ordinarily actionable merely because children are
open.

### Existing contradictory state

Do not make old contradictory rows render the entire Team state unreadable.

Migration and reconciliation should:

- identify accepted or finished parents with unresolved descendants;
- mark them as invalid in derived payloads;
- prevent further release or publication;
- require an authorized repair, cancellation, or migration decision;
- preserve historical acceptance and review records.

## Proposal 2: Replace parent polling with transition-driven supervision

An aggregate parent waiting on children must not generate periodic `task_ready`
wakes solely because its children are open.

### Wake the parent owner when

- a child reports a blocker or asks for help;
- a child submits a deliverable or requests acceptance;
- a contribution or review is rejected;
- a child reaches a resolved disposition;
- the direct-child set transitions from not ready to fully ready for integration;
- an operator or configured policy creates an explicit supervision checkpoint.

Direct-child transitions are sufficient when Proposal 1 is enforced recursively:
a child cannot resolve until its own descendants are resolved.

Coalesce repeated notifications for the same transition. Do not create a new
periodic checkpoint policy until there is an observed need; existing inbox,
blocker, scheduled-wake, and operator mechanisms should be reused first.

## Proposal 3: Add minimal contribution lineage

Parent deliverables need durable composition lineage, but should not duplicate the
contents of their child deliverables.

### Minimal model

A parent deliverable records:

- `source_deliverable_ids`: exact accepted deliverables from direct child tasks;
- its own resulting Git commit, artifact bundle, structured outcomes, and
  verification through the existing deliverable fields;
- existing Git promotion or reviewed-manifest references when Git integration was
  used.

Transitive contribution lineage is obtained by recursively reading each source
deliverable's own `source_deliverable_ids`.

Do not copy onto the parent:

- child task state;
- child acceptance decisions;
- child Git SHAs or artifact references already pinned on the source
  deliverables;
- review findings or outcome definition snapshots already stored with those
  deliverables;
- separate waiver records that duplicate an authorized child cancellation.

### Compose and submit validation

Validate that:

- each source deliverable belongs to a direct child of the parent task;
- each source is current, accepted, and not superseded;
- every direct outcome child has an accepted source unless it resolved through an
  accepted negative/no-action result or authorized cancellation;
- every direct support child with a current accepted deliverable is represented;
- support children without assessable output are resolved but do not require an
  artificial deliverable;
- the resulting parent commit or artifact bundle is the exact object being
  submitted.

If a child is itself an aggregate, its accepted parent-level deliverable provides
the transitive lineage. The root does not list every leaf deliverable again.

## Proposal 4: Support independent task creation without delegated grants

The task “Create task X” is a planning or coordination task. Task X is an
independent root, not its child.

### Authorized planner flow

An agent whose project-scoped role has `create_root_task` may:

1. receive a support task to define or create independent work;
2. create one or more root tasks within its existing Team and project authority;
3. record the planning task as `originating_task_id` when useful;
4. finish the planning task once the required root records exist.

The originating task does not wait for the created roots to complete.

### Manager-created flow

When the manager already knows the work, skip the planning task. Create and assign
the root directly.

### Proposal-only flow

When an agent lacks root authority, it proposes the task through the existing
deliverable or inbox workflow. An authorized manager or lead creates the root.

Only add a first-class draft proposal record if this manual approval pattern
becomes common and error-prone. Do not add:

- transferable root authority;
- one-shot grants;
- grant consumption and revocation state;
- special activation modes for roots;
- a root disguised as a child.

## Proposal 5: Persist explicit dispatch scope

Create a durable `TeamDispatch` record. Its scope fields are immutable after the
scheduler computes them. Its lifecycle status may progress from preparing to
leased, then to completed, expired, or failed.

### Persist

- `cause_kind` and `cause_id`;
- `task_ids`;
- `blocker_ids`;
- `review_request_ids`;
- `inbox_item_ids`;
- `project_ids` and managed repository IDs required by the scope;
- agent, session, lease, and creation timestamps.

The lists contain only records actually delivered and assigned by that dispatch.

### Determine task scope

For an ordinary persistent-agent dispatch, include every task that is actionable
at lease time and:

- is owned by the agent;
- is nonterminal and not intrinsically blocked;
- has satisfied dependencies;
- is not waiting on unresolved children;
- is not waiting for acceptance, review, or another external event;
- belongs to the Team and project authority available to the agent.

An aggregate parent becomes actionable only when its children are resolved and
integration is ready. A supervisor's own substantive work remains actionable
through its self-owned child task.

A task-specific wake cause never narrows this ordinary scope.

Do not introduce genuinely targeted task dispatches in the first implementation.
Persist the current agent-wide semantics correctly before adding another
scheduling mode.

### Account for non-task work

Blocker ownership, reviews, and actionable inbox items have their own scope lists.
They are not inserted into `task_ids` merely because they refer to a task the agent
does not own.

Consequences follow the corresponding scope:

- task no-progress policy concerns `task_ids`;
- blocker progress and escalation concern `blocker_ids`;
- reviews and inbox items use their own acceptance, retry, and escalation policy.

Before applying a consequence, revalidate that the scoped record remains assigned,
open, and actionable.

### Repository preparation

Prepare worktrees only for scoped actionable tasks:

1. compute and persist the immutable scope with status `preparing`;
2. derive its complete repository and worktree requirements;
3. prepare every required repository and task context;
4. move the dispatch to `leased` and resume the agent only after preparation
   succeeds.

If any repository cannot be prepared, mark the dispatch failed, release its
runtime lease, and expose the preparation error. Do not silently omit a scoped
task, mutate the scope after preparation begins, or rely on project-list order.

## Proposal 6: Convert exhausted no-progress into one durable execution-stall incident

The current no-progress counter is agent-wide. Its exhaustion therefore indicates
an agent execution or ownership stall, not an intrinsic defect in every task.

When the allowance is exhausted:

1. create or reuse one open `agent_execution_stall` blocker for the agent;
2. record the exhausted dispatch IDs, timestamps, and affected scoped task IDs;
3. route the blocker to the nearest available reporting-line superior, then the
   Team manager, governance, or operator;
4. send coalesced notices to distinct accountable parent-task owners whose
   children are affected;
5. suppress ordinary task polling for tasks still owned by the stalled agent;
6. leave each task's intrinsic lifecycle state unchanged;
7. reset the allowance when the execution-stall blocker is resolved.

The UI may derive that affected tasks are waiting on an owner execution stall, but
must not mislabel them as intrinsically broken.

If genuinely targeted task dispatches are introduced later, a no-progress incident
may be task-specific. Do not add that complexity while dispatches remain
agent-wide.

Progress on any scoped work continues to reset the current agent-wide counter.
Continual neglect of one task while other work progresses is task aging or
starvation and requires a separate policy.

## Proposal 7: Extend existing blocker escalation with task-hierarchy routing

Do not build a second blocker escalation engine.

The existing blocker lifecycle already supports ownership, delegation,
blocker-specific progress, transfer, governance, and operator routing. Extend its
next-owner calculation according to blocker source:

- a blocked child task or child deliverable first routes through distinct
  parent-task owners;
- after the parent-task chain is exhausted, route to the Team manager;
- an agent execution-stall blocker follows the agent's reporting line;
- review, release, governance, and explicit blockers retain their appropriate
  existing source-aware routes;
- terminal routing continues to governance or the configured operator target.

Unrelated Team activity must not count as blocker progress. Existing
blocker-event-by-dispatch semantics remain authoritative.

The generic manager fallback stays as a catch-all for otherwise unroutable open
work. It is not the primary mechanism for a known blocker with an accountable
task or reporting chain.

## Proposal 8: Make blocking reasons canonical, visible, and actionable

`TeamBlocker` is the canonical explanation for why work is blocked.

Every transition into an intrinsic `blocked` task state must atomically create or
link an open blocker containing:

- stable `reason_code`;
- human-readable summary and details;
- source kind and source ID;
- reporter and accountable owner;
- created timestamp;
- route and escalation depth;
- follow-up deadline when applicable.

A task description is not a blocker explanation.

### UI and API

Show:

- “Blocked because …”;
- reason code and explanation;
- blocked-since timestamp;
- source record;
- current owner or delegate;
- route and escalation level;
- latest blocker event;
- next follow-up or required action.

Dependency-waiting tasks derive and display the upstream task's current blocker
reason. Do not copy that reason into a second downstream blocker.

### Atomic lifecycle

- Reporting a task blocker and moving the task to `blocked` occur in one command.
- Moving a task out of `blocked` resolves the linked blocker when appropriate.
- Resolving a blocker recalculates the task's correct state and readiness; it does
  not blindly set the task to `pending` when another blocker, review, or dependency
  remains.
- Reassignment and cancellation update or resolve linked blockers in the same
  transaction.

## Proposal 9: Make scoped multi-worktree operation explicit

The runtime already creates sibling task worktrees and validates task-bound Git
contexts. Keep that model and improve its selection and multi-project behavior.

### Prompt and UI mapping

For each prepared repository, display a prominent table:

| Task | Project | Worktree | Branch | Context |
| --- | --- | --- | --- | --- |

Instructions must tell the agent to enter the worktree matching the task before
editing. The base checkout is labeled as a control checkout and must not be
presented as the default place for task changes.

Do not add persistent “selected task” state that cannot actually prevent arbitrary
shell edits. Safety remains enforced at the durable boundary by rejecting
deliverables whose context belongs to another task, agent, branch, or dispatch.

### Scope integration

- prepare worktrees from `TeamDispatch.task_ids`, not every active task;
- prepare every repository required by the scope;
- never select a project from incidental list order;
- expose preparation failure before the agent starts work;
- retain deterministic stable physical worktree paths and fresh dispatch-bound
  Git context IDs.

Do not migrate to a bare control repository until the existing control checkout
causes a demonstrated safety or operational failure.

## Proposal 10: Add early, safe physical task-worktree cleanup

Physical task worktrees duplicate checked-out repository files and can consume
substantial disk even though Git objects are shared. Cleanup is an early
reliability requirement, not end-stage polish.

Implement cleanup immediately after the hierarchy model can reliably distinguish
resolved, unresolved, rejected, and blocked tasks.

### Trigger cleanup

Enqueue a cleanup candidate when:

- a task reaches a genuinely resolved terminal disposition;
- a task is reassigned away from the session that owns its old worktree;
- a previous cleanup attempt becomes retryable.

Also run a periodic reconciler so existing and failed candidates do not depend on
another agent wake.

Use a short configurable recovery grace period. Whole-session deletion remains the
final cleanup boundary.

### Revalidate safety

Before removal:

- acquire a session/repository cleanup lock;
- re-read Team state after acquiring the lock;
- confirm the task is still resolved or permanently reassigned;
- confirm no active dispatch or Git context references the worktree;
- confirm the worktree path and branch match the expected task identity;
- confirm there are no tracked modifications;
- confirm there are no untracked, non-ignored files;
- confirm the worktree `HEAD` has not advanced beyond the latest safely retained
  task commit;
- confirm any required terminal deliverable captured or pushed the expected
  commit.

Ignored generated files and caches should not indefinitely prevent cleanup.
Inventory them for audit and size reporting, then permit removal after the
source-safety checks pass.

### Remove and record

1. Revalidate immediately before mutation.
2. Run `git worktree remove`; use forced removal only when the service has proven
   that the remaining files are ignored/generated.
3. Run `git worktree prune`.
4. Record success, bytes reclaimed, task, branch, worktree path, and timestamp in
   the existing action or audit surface.
5. Only then prune obsolete, unreferenced Git context metadata.

Tracked changes, non-ignored untracked files, or an unexpected commit produce a
visible recovery item with the exact path, branch, and safety failure. Never
silently delete them. A cleanup failure does not automatically reopen an accepted
task.

If terminal cleanup is insufficient, clean worktrees for long-idle waiting tasks
may later be treated as an evictable cache: preserve the task branch, remove the
checkout, and recreate it on the next actionable dispatch.

## Proposal 11: Align guidance, UI, and documentation

### Agent guidance

Parent owners need explicit instructions to:

- create constituent child tasks;
- create a self-owned child for their own substantive implementation;
- remain quiet while children are merely running;
- accept or reject child deliverables;
- integrate exact accepted direct-child sources;
- submit the consolidated parent deliverable;
- resolve, reassign, cancel, or escalate blocked children.

Child owners need to know whether to:

- finish support work;
- submit an assessable contribution;
- request specialist review when required;
- report a blocker.

Planner guidance must explain that independent root creation uses
`create_root_task` and optional provenance, never a fake parent-child edge.

Increment the guidance version and refresh both stored session metadata and
persistent worker `AGENTS.md` content. Updating only newly created sessions is not
a complete rollout.

### UI

Enhance the existing task board and task details before creating another large
visualization surface. Show:

- recursive hierarchy and indentation;
- actionable versus waiting readiness;
- unresolved descendant counts;
- contribution coverage;
- direct acceptance and formal-review state;
- blocker reason and accountable resolver;
- task-to-worktree mapping;
- invalid legacy combinations requiring repair.

### Documentation

Document:

- lifecycle containment versus dependencies and provenance;
- role-scoped independent root creation;
- one complete APP example from decomposition through child handoff,
  integration, review, rework, and release;
- worktree reclamation and recovery behavior;
- operator repair procedures for contradictory legacy states.

## Proposal 12: Add focused invariants and representative end-to-end coverage

Tests accompany each implementation slice rather than waiting for a final testing
phase.

### Hierarchy

- three-level decomposition and recursive resolution gating;
- refusal of finish, submission, direct acceptance, and formal acceptance with an
  unresolved descendant;
- support `finished`, outcome `accepted`, accepted negative result, and authorized
  cancellation;
- rejected and blocked descendants remaining unresolved;
- legacy contradictory state surfaced without making Team state unreadable.

### Supervision and contributions

- no periodic parent wake while children are merely running;
- parent wakes for blockers, handoff, rejection, child resolution, and
  integration-ready transition;
- direct-child contribution coverage;
- transitive lineage through nested aggregate deliverables;
- no duplicated acceptance or Git state required on the parent.

### Root creation and provenance

- authorized manager creates and assigns a root directly;
- project-scoped planner creates an independent root;
- unauthorized root creation remains rejected;
- `originating_task_id` records provenance without gating either task;
- proposal-only flow does not confer authority.

### Dispatch and no-progress

- immutable scheduler-computed task, blocker, review, inbox, project, and
  repository scope;
- stale-scope revalidation before consequences;
- cause does not narrow ordinary agent-wide scope;
- one agent execution-stall blocker for exhausted agent-wide dispatches;
- affected task notices are coalesced;
- starvation remains distinct from total no-progress.

### Blockers

- child blockers route through distinct parent-task owners;
- execution-stall blockers route through the reporting line;
- manager, governance, and operator fallbacks;
- unrelated activity does not suppress blocker-specific escalation;
- reason visibility and atomic state recalculation.

### Worktrees

- only actionable scoped tasks receive worktrees;
- several task worktrees are mapped unambiguously;
- every scoped Git project is prepared or the dispatch fails explicitly;
- cross-task context rejection remains enforced;
- resolved and reassigned clean worktrees are removed;
- active contexts, tracked changes, untracked files, and unexpected commits prevent
  deletion;
- ignored generated files may be reclaimed after safety checks;
- cleanup is idempotent and periodic reconciliation retries failures;
- physical removal precedes obsolete metadata pruning.

### Rollout

- guidance-version refresh reaches existing persistent sessions;
- API, MCP, Admin, and review-cascade boundaries share the same task assessment;
- one representative APP flow covers root decomposition, nested child handoff,
  integration, final review, rework, worktree cleanup, and release readiness.

Use focused model, command, runtime, and provider tests for most cases. Keep one
representative end-to-end flow rather than building one large test for every
permutation.

## Suggested implementation order

1. Define canonical recursive resolution/readiness, add one cancellation
   disposition, enforce all completion and acceptance boundaries, and surface
   legacy contradictions.
2. Add event-driven and periodic physical worktree cleanup for resolved and
   reassigned tasks, including reconciliation of existing worktrees.
3. Stop parent polling, add transition-driven supervision, update planner/root
   guidance, and refresh existing persistent sessions.
4. Add minimal direct-child contribution lineage and compose validation.
5. Persist dispatch scope, prepare all required repositories, and present explicit
   task/worktree mappings.
6. Add the single agent execution-stall incident, task-hierarchy-aware blocker
   routing, and complete blocker reason display.
7. Finish recursive UI refinements, the APP walkthrough, migration operations,
   and representative end-to-end rollout validation.

Tests and changelog updates belong in every slice.

The ordering matters:

- cleanup must follow correct resolution semantics so rejected or blocked work is
  not mistaken for safely terminal work;
- dispatch and Git preparation must share the same readiness calculation;
- no-progress consequences require durable dispatch scope;
- UI and guidance must not describe lifecycle behavior before the command
  boundaries enforce it.

## Explicit non-goals

This proposal does not introduce:

- delegated or transferable root-task creation grants;
- a persisted `waiting_on_children` task state;
- separate cancelled, superseded, and waived lifecycle states;
- copied contribution acceptance, review, Git, or artifact snapshots;
- one automatic blocker per task for agent-wide no-progress;
- a second blocker escalation subsystem;
- persistent task-selection state for arbitrary shell work;
- an immediate migration to a bare worktree anchor;
- silent deletion of dirty or unexpected worktrees.
