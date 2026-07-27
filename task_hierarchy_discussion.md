# Team Add-on Hierarchical Task Execution Proposals

Status: design proposal only; no implementation has been made.

## Objective

Make deep task decomposition a reliable execution model:

1. A supervisor owns an outcome.
2. The outcome is decomposed into child tasks.
3. Agents complete and hand off their contributions.
4. The parent owner integrates those contributions.
5. One consolidated parent deliverable proceeds through the appropriate final review.
6. Stalled work escalates without continuously polling supervisors.

The Team Add-on already represents task trees, dependencies, task-scoped
deliverables, direct-superior handoffs, formal reviews, blockers, and per-task Git
worktrees. The missing piece is a consistent lifecycle tying those facilities
together.

## Settled design principles

### 1. Parent-child means lifecycle containment

`parent_task_id` should mean that the child is a constituent part of the parent.
The parent cannot be complete while any child or descendant remains unresolved.

A child is resolved when it reaches the terminal outcome appropriate to its type:

- support or coordination work may be `finished`;
- work with an assessable outcome must be accepted;
- obsolete work must be explicitly cancelled, superseded, or waived;
- an investigation may resolve with an accepted negative result.

Every child is completion-relevant. It need not succeed, but it must reach an
explicit terminal disposition before its parent can be accepted. Work that may
remain open after parent completion must not be modeled as a child.

If task B can continue independently without affecting completion of task A, task B
is not a child of task A.

### 2. Keep distinct relationships distinct

| Relationship | Meaning |
| --- | --- |
| Parent-child | B is a constituent part of A and gates A's completion. |
| Dependency | A cannot proceed until B reaches a required state. |
| Delegation provenance | A caused B to be created or assigned. |
| Deliverable contribution | B's exact output was incorporated into A's product. |

These relationships may coexist, but one must not be used as a substitute for
another.

### 3. Root-task authority remains restricted

The proposal must not restore the ability for arbitrary agents to invent root work.
Ordinary root creation remains protected by `create_root_task`.

A delegation task may authorize its assignee to create one independent root task,
but that authority must travel through a narrow grant rather than through
`parent_task_id`. The created task remains a root and records delegation provenance.

### 4. Wake cause and work scope are different

A task, inbox item, review, or blocker may cause an agent to wake. That does not
necessarily mean the dispatch is scoped exclusively to that task.

The current Team dispatch is agent-scoped and presents all active owned work.
No-progress consequences must apply to the work the agent was expected and able to
advance, not merely to the item that caused the wake.

## Proposal 1: Enforce hierarchical completion

Make the task tree a real completion contract.

### Required behavior

- Derive recursive descendant status, not only immediate-child counts.
- Treat a parent with unresolved children as `waiting_on_children` unless the parent
  has separate actionable integration work.
- Refuse terminal completion or acceptance of a parent while any descendant remains
  unresolved.
- Refuse final parent-deliverable submission until required child outcomes are
  resolved and contribution requirements are satisfied.
- Enforce task dependencies at completion and delivery boundaries, not only during
  wake selection.
- Prevent or repair contradictory states such as an accepted parent with an active,
  rejected, or blocked descendant.
- Provide explicit terminal dispositions for obsolete work so that a dead child
  does not block its ancestors forever.

### Supervisor-owned work

If the supervisor will implement a substantive slice alongside subordinates, that
slice should be an explicit self-owned child task. The parent itself represents the
combined outcome and final integration. This allows the supervisor to remain
wakeable for their own work without making every parent with open children
continuously wakeable.

## Proposal 2: Make contribution aggregation first-class

Child deliverables are currently task-scoped, but the parent deliverable has no
durable record of what it incorporated. Add explicit contribution lineage.

### Proposed model

A parent deliverable should record:

- source child deliverable IDs;
- exact source commits and artifact versions;
- the terminal state and acceptance decision for every contributing child;
- integration evidence;
- coverage of all contribution-producing children;
- any explicitly waived or superseded contribution and its reason.

A compose/submit operation should validate that:

- each source belongs to the correct descendant task;
- each source is in an acceptable state;
- exact immutable Git and artifact references are used;
- all required contributions are represented;
- the resulting parent commit or artifact bundle is the object being submitted.

Support children that produce no assessable artifact still gate parent completion,
but do not need artificial deliverables.

## Proposal 3: Add safe delegated root creation

A privileged agent may create a delegation task whose outcome is the creation of a
new independent root task.

The delegation grant should be:

- issued only by an actor already allowed to create roots;
- constrained to one team and project;
- constrained to permitted assignees and an explicit purpose;
- one-shot and idempotent;
- non-transferable and unable to confer general root authority;
- consumed or revoked when the delegation task closes.

The created task should have:

- `parent_task_id = null`;
- provenance such as `created_from_delegation_task_id`;
- the grant and authorizing actor recorded in audit history.

For loosely specified work, the new root can begin as a draft and become active
when the delegator accepts the delegation task. Tightly specified delegations may
permit immediate activation.

## Proposal 4: Replace parent polling with event-driven supervision

An incomplete parent waiting on children should not generate periodic task-ready
wakes by itself.

### Wake the parent owner when

- a child reports a blocker or asks for help;
- a child submits a deliverable;
- a child contribution or review is rejected;
- a child changes owner or reaches a terminal state;
- all children become resolved and parent integration is ready;
- an explicit supervision checkpoint becomes due.

Optional periodic check-ins should be a visible policy with an interval or deadline,
not an accidental consequence of open children.

This keeps parent completion safely gated while avoiding repeated “still waiting”
wakes.

## Proposal 5: Make dispatch work scope explicit

The scheduler must compute and persist an immutable snapshot of the work assigned
by each dispatch:

- `scope_task_ids`: tasks the agent was expected and authorized to advance in that
  dispatch;
- `scope_blocker_ids`: blockers for which the agent was accountable;
- `scope_review_request_ids`: reviews assigned to the agent;
- `scope_inbox_item_ids`: acceptance, question, status, and other actionable inbox
  items delivered in the dispatch.

The scheduler is the authority for scope. It must compute the snapshot from
canonical Team state before resuming the agent. The agent must not supply its own
scope, and a task-linked wake must not implicitly narrow the scope.

### Determine the task scope

For an ordinary persistent-agent dispatch, `scope_task_ids` contains every task
that is actionable at lease time and:

- is currently owned by the agent;
- is nonterminal and not blocked;
- has satisfied dependencies;
- is not waiting for unresolved children;
- is not waiting for acceptance, review, or another external event;
- belongs to the Team, project, and repository scope prepared for the dispatch.

An aggregate parent becomes actionable when its children are resolved and parent
integration can proceed. A supervisor's own implementation slice remains
actionable through its explicit self-owned child task.

If A, B, and C are actionable, the scope is `[A, B, C]` even when an event linked
to B triggered the wake. If A is waiting on children and B is blocked, while C is
actionable, the task scope is `[C]`.

A future genuinely task-scoped dispatch may deliberately intersect the ordinary
scope with a trusted scheduler-selected target set. That restriction must be
explicit on the dispatch. A task-specific cause alone never narrows the scope.

### Account for non-task work

An agent may be expected to resolve a subordinate's blocker, decide a deliverable,
or perform a review without owning the associated task. Those records belong in
their corresponding blocker, review, or inbox scopes rather than being inserted
into `scope_task_ids`.

No-progress consequences follow the relevant scope:

- exhausted task work may block tasks in `scope_task_ids`;
- exhausted blocker work transfers or escalates blockers in
  `scope_blocker_ids`;
- review and inbox inactivity follows its review, acceptance, or escalation policy
  rather than blocking unrelated owned tasks.

Before applying a consequence, revalidate that the scoped record is still assigned,
open, and actionable. External progress after dispatch creation must not create a
stale automatic block.

The current agent-wide `no_progress_count` remains coherent while ordinary
dispatches contain all actionable owned tasks. If genuinely targeted task scopes
are introduced later, no-progress streaks must be keyed by compatible scope or
reset when scope changes; otherwise failure on B could incorrectly be attributed
to A.

Git worktree preparation should use `scope_task_ids`, not every merely active task.
The prompt and action feedback should expose the persisted scope so the agent and
operator can see exactly what the dispatch covered.

## Proposal 6: Convert exhausted no-progress into durable blockers

When an agent exhausts its no-progress allowance, atomically block every task in
the exhausted dispatch's `scope_task_ids` that remains owned, open, and actionable
when the consequence is applied.

### For each affected task

1. Set the task to `blocked`.
2. Record reason code `owner_no_progress_exhausted`.
3. Record factual evidence, including dispatches and timestamps.
4. Create a linked `TeamBlocker`.
5. Assign blocker accountability to the nearest distinct parent-task owner.
6. Wake that accountable owner.
7. Stop ordinary task polling for the blocked task.

Create one blocker per task so each task has correct lifecycle and audit history,
but coalesce wake notifications when several blockers have the same accountable
owner.

The reason describes an ownership/execution stall, not an intrinsic defect in the
task. The accountable owner may reassign, clarify, split, cancel, or resume the
work. Unblocking resets the relevant no-progress allowance.

If an agent progresses one scoped task, the current agent-wide counter resets.
Continual neglect of another task while useful work continues is a separate
task-aging/starvation problem and should not be conflated with total no-progress.

## Proposal 7: Use targeted blocker escalation with manager fallback

Stalled child work should follow its task hierarchy:

```text
blocked child
  -> nearest distinct parent-task owner
  -> higher accountable owner
  -> Team manager
  -> governance/operator
```

If an accountable owner makes no blocker-specific progress within its attempt or
deadline policy, transfer the blocker upward. Unrelated Team activity must not
suppress this escalation.

The existing generic manager fallback should remain as a catch-all for otherwise
unroutable open work. It should not be the mechanism relied upon for a known
stalled child because it is team-wide, can be suppressed by unrelated leases, and
does not inherently identify the stalled task chain.

## Proposal 8: Make blocking reasons visible and actionable

Every transition into `blocked` must carry a useful reason. A task description is
not a blocking reason.

The task board and task details should show:

- “Blocked because …”;
- reason code and human-readable explanation;
- source task, review, dependency, agent, or system event;
- blocked-since timestamp;
- current accountable resolver;
- escalation route and level;
- latest blocker event and next action.

Dependency-blocked tasks should surface the upstream task's actual blocking reason,
not only its ID. Automatic no-progress blockers should state the evidence, for
example:

> Owner made no recorded progress across three eligible agent-wide dispatches.

Resolving, cancelling, or reassigning a task must update or resolve its linked
blocker atomically.

## Proposal 9: Make multi-worktree operation explicit and safe

For a Git-configured project, one dispatch may prepare multiple sibling worktrees:
one stable worktree and deterministic branch per active owned task. They are all
simultaneously accessible, while each dispatch receives fresh task-bound context
IDs.

### Improve task/worktree selection

- Present a prominent task -> worktree -> branch -> context table.
- Require an explicit task selection before repository-changing actions.
- Show the selected task and worktree in action feedback.
- Clearly identify the base checkout as a control checkout, not a place for task
  edits.
- Prefer a bare/control repository as the long-term worktree anchor so there is no
  tempting mutable base working tree.
- Continue rejecting deliverables whose context belongs to another task, agent,
  branch, or dispatch.

### Handle multiple projects deliberately

The current runtime silently prepares only the first Git-configured project found
for the agent. Replace that behavior with one of:

- prepare every repository/worktree needed by the explicit dispatch scope; or
- explicitly scope the dispatch to one project and defer other project work.

The selected policy must be represented in dispatch scope rather than depend on
project-list order.

## Proposal 10: Add safe task-worktree cleanup

Physical Team task worktrees currently persist until the whole container session or
VM is removed. Terminal tasks can therefore accumulate worktrees indefinitely,
even after abandoned Git context metadata is pruned.

Remove a task worktree only when:

- its task is terminal or permanently reassigned;
- no active dispatch or Git context references it;
- relevant commits have been pushed and captured by deliverables;
- the worktree is clean;
- the configured retention period has elapsed.

Cleanup should:

1. acquire a task-worktree cleanup lease;
2. revalidate task, dispatch, context, branch, and dirty-state conditions;
3. run `git worktree remove` and then `git worktree prune`;
4. record a durable cleanup result;
5. only then prune obsolete Git context metadata.

A dirty worktree must produce a visible blocker or recovery action, never be
silently deleted. Whole-session deletion remains the final cleanup boundary for all
remaining worktrees.

## Proposal 11: Align agent guidance, UI, and documentation

Agent guidance should state the complete decomposition recipe.

Parent owners need explicit instructions to:

- create constituent child tasks;
- distinguish their own actionable child work from parent waiting;
- accept or reject child contributions;
- integrate exact accepted inputs;
- submit the consolidated parent deliverable for final review;
- handle blocked children and escalations.

Child owners need to know whether to:

- finish support work;
- submit a contribution to the parent owner;
- request an explicitly required specialist review;
- report a blocker.

The UI should provide a recursive task-tree view showing:

- actionable versus waiting nodes;
- descendant completion state;
- contribution and direct-acceptance state;
- integration readiness;
- blocker reason and accountable resolver;
- formal-review coverage;
- contradictory or invalid lifecycle states;
- task-to-worktree mapping for the owning agent.

Documentation should include one complete APP example from root decomposition
through child handoff, integration, final review, rework, and release. Guidance
rollout must refresh existing persistent sessions rather than updating only newly
created agents.

## Proposal 12: Add end-to-end invariants and regression coverage

At minimum, cover:

- three-level decomposition and recursive completion gating;
- rejection of an accepted parent with an unresolved descendant;
- explicit cancellation, supersession, and negative-result resolution;
- delegated one-shot root creation without general root permission;
- parent silence while children are merely running;
- event-driven parent wakes for handoff, rejection, blocking, and subtree completion;
- agent-wide no-progress blocking every actionable scoped task;
- exclusion of legitimately waiting tasks from automatic blocking;
- immutable scheduler-computed task, blocker, review, and inbox scopes;
- stale-scope revalidation before automatic blocking or escalation;
- targeted-scope changes that do not misattribute no-progress;
- blocker transfer through parent owners to manager and operator;
- blocker reason visibility and atomic resolution;
- simultaneous task worktrees and cross-task context rejection;
- explicit multi-project dispatch behavior;
- safe clean-worktree cleanup and dirty-worktree preservation;
- guidance-version rollout to existing sessions.

## Suggested implementation order

1. Define and enforce hierarchy and resolution invariants.
2. Add contribution lineage.
3. Separate parent waiting from actionable wake readiness.
4. Persist dispatch cause/scope and implement automatic blocking.
5. Route blocker escalation and expose blocker reasons.
6. Harden task/worktree selection and multi-project behavior.
7. Add physical worktree cleanup.
8. Complete UI, documentation, migration, and end-to-end rollout.

The ordering matters: scheduler and cleanup behavior should not be changed until the
system can reliably determine whether a task is actionable, waiting, resolved, or
still required by an ancestor.
