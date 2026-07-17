# 23. Ephemeral Delegation

> **At a glance.** A regular Team agent can sometimes ask a temporary helper to investigate or implement one clearly bounded piece of work. The helper receives only that assignment and a narrow way to return its result; it does not become a new permanent team member. The parent agent remains responsible for checking the evidence and deciding whether to use it.
>
> Think of this as controlled parallel work, not hiring another member of the organization. The request, temporary session, result, parent decision, and cleanup are all recorded separately. Operators can follow those milestones without needing to understand the workflow nodes that move the assignment between them.

## When a temporary helper is useful

Persistent agents carry organizational identity, reporting relationships, Team memory visibility, inbox, and recurring responsibility. Ephemeral agents are intentionally different. They are short-lived workers for a bounded prompt such as:

- reproduce a failing telemetry retry test;
- inspect one dependency's retry semantics;
- implement a small isolated change on a deterministic branch;
- gather evidence in parallel.

The parent remains responsible for the Team task. Delegation increases execution capacity without multiplying durable organizational actors.

For Northstar, Imani can delegate “add deterministic jitter edge-case tests” while continuing the main retry implementation. The child cannot reassign Imani's task, message Priya, request release, or decide Rowan's review.

The parent therefore asks a narrow question, receives evidence, and remains accountable for the task. A useful assignment has a clear stopping point—such as a test, patch, or research report—and does not imply authority to coordinate the Team.

```text
parent requests bounded help -> temporary worker runs -> result returns
                             -> parent accepts or rejects -> workspace is cleaned up
```

Completion and acceptance are deliberately separate. A worker can finish successfully while the parent still decides that its result is obsolete, incomplete, or unsuitable.

## Admission and capacity

Before creating a helper, Team checks that the parent really owns the work, that delegation is enabled, and that enough capacity remains for the whole request. A batch either passes together or creates nothing; one invalid assignment cannot leave a half-created set behind.

> <span class="sidebar-kicker">Technical sidebar</span> **Admission and capacity checks**
>
> The parent persistent agent has an effective ephemeral policy with <code>max_concurrent</code>. A value of zero disables delegation. Active capacity counts assignments in <code>requested</code>, <code>spawn_pending</code>, and <code>running</code>.
>
> A request is valid only when:
>
> - the caller is the resolved persistent parent on an active dispatch;
> - the parent has positive capacity and the complete requested batch fits;
> - each target task belongs to a project assigned to the parent;
> - the parent owns the target task;
> - requested model, provider mode, execution backend, image, tools, TTL, directory, and metadata pass schema and policy validation.
>
> Batch requests validate all items before mutation. One invalid child does not leave the other children partially created.
>
> The default execution backend is <code>container_worker_vm</code>. An assignment may carry an explicit compatible model/provider, image, and <code>ttl_hours</code>. These are bounded execution inputs, not general secret or host-path transport.

## The assignment record

The assignment is the durable envelope around disposable work. It remains after the worker session is removed and tells an operator what was requested, which session ran it, what came back, what the parent decided, and whether cleanup finished.

> <span class="sidebar-kicker">Technical reference</span> **Assignment fields and states**
>
> An <code>EphemeralAssignment</code> captures:
>
> - parent agent, Team, project, and task;
> - title and complete prompt;
> - eventual core session ID;
> - model/provider/backend/image choices;
> - allowed tools and TTL;
> - directory and metadata;
> - summary, artifacts, optional agent branch, error, and timestamps;
> - lifecycle state.
>
> States are <code>requested</code>, <code>spawn_pending</code>, <code>running</code>, <code>completed</code>, <code>accepted</code>, <code>rejected</code>, <code>cleanup_pending</code>, <code>cleaned_up</code>, and <code>failed</code>. They describe execution, decision, and cleanup milestones rather than one mandatory linear chain. In particular, core-session cleanup can finish before the parent accepts a completed result, and the parent may accept a result whose session is already cleaned up. Failures may occur during spawn, execution, result validation, or cleanup; durable records remain useful evidence after compute is gone.

## Asynchronous lifecycle

The helper does not have to start before the parent's command returns. Team first records the request, then the ordinary Evander workflow creates and prompts the temporary worker in the background. When the result returns, the parent is notified and the worker session is destroyed.

> <span class="sidebar-kicker">Deeper detail</span> **The composed workflow path**
>
> The parent's <code>team_request_ephemeral_agent</code> or batch command performs only the durable request transaction. It creates the assignment and outbox intent. The registered runtime pack then:
>
> 1. consumes <code>team.ephemeral.requested</code>;
> 2. creates an ordinary core Evander agent/session through <code>effect:agent:create</code>;
> 3. records the spawned session with <code>effect:team:mark-ephemeral-spawned</code>;
> 4. prompts the worker through <code>effect:agent:prompt</code>;
> 5. routes the completed session through <code>query:team:session-routing</code>;
> 6. records result/completion and notifies the parent;
> 7. destroys the core agent/session;
> 8. records cleanup through <code>effect:team:mark-ephemeral-cleaned-up</code>.
>
> The lifecycle composes core execution effects; the add-on does not implement a hidden second container scheduler.

![Ephemeral assignment lifecycle across Team and core execution](../../assets/evander-user-manual/diagrams/15-ephemeral-lifecycle.svg)

<p class="figure-caption">Figure 23.1 — A durable assignment surrounds disposable execution, parent decision, and cleanup evidence.</p>

## The worker's authority

The worker receives no ordinary Team command surface. It can report the result of this assignment, but it cannot inherit the parent's identity, lease, or organizational authority.

> <span class="sidebar-kicker">Technical sidebar</span> **The one Team command and the Git branch check**
>
> An ephemeral wake exposes only:
>
> <code>team_report_ephemeral_result</code>
>
> The executor resolves the assignment from the ambient worker session. The child cannot call ordinary persistent Team commands. It receives the prompt and execution resources necessary for the assignment, but not the parent's organizational identity or lease.
>
> A successful report requires a useful summary. It may attach stable artifact references and a Git branch. An error report marks the assignment failed with its evidence.
>
> If the child reports Git output, the branch must be exactly:
>
> <code>agents/&lt;session-id&gt;/&lt;assignment-id&gt;</code>
>
> The server derives and verifies the name. A mismatched branch fails the assignment instead of accepting untraceable code.

## Completion is not acceptance

When the worker reports success, the assignment becomes <code>completed</code>. The parent receives an inbox item and wake intent. The parent must inspect the summary, artifacts, and Git evidence, then call:

- <code>team_accept_ephemeral_result</code>; or
- <code>team_reject_ephemeral_result</code>.

Only the parent may make that decision.

Acceptance idempotently creates a Team deliverable attributed to the parent, records the child evidence, and moves the parent task toward review. The attribution is deliberate: the persistent agent owns the Team obligation and is accountable for selecting the child's result.

Rejection records a concrete reason and does not convert the output into accepted task evidence. A parent can use useful observations in a later attempt without rewriting the rejected assignment.

The child's core session is cleaned up after routing. Session destruction does not delete the durable assignment, its result, or the acceptance/rejection decision.

## Northstar parallel work

Imani has capacity two and requests:

1. assignment E1: “Add deterministic tests for jitter boundaries.”
2. assignment E2: “Probe upstream timeout and retry-after behavior; report findings only.”

Both requests are committed in one batch. The runtime starts two disposable container sessions. E1 returns a deterministic branch and test evidence; E2 returns a research artifact without Git.

Imani inspects both. E1 is accepted and becomes a deliverable attached to the telemetry retry task. E2 is rejected as a deliverable because its probe used an obsolete upstream version, but its raw artifact remains evidence. Imani then submits the combined current deliverable for Rowan's review.

At no point can either child declare the task accepted or request release.

## Git handoff

An accepted child branch is still internal. An authorized persistent actor may request publication to an add-on review branch:

<code>team/&lt;team-id&gt;/reviews/&lt;assignment-id&gt;</code>

That request becomes a Git promotion intent and runs through the same managed-Git effect and recording flow as persistent evidence. Later feature/release promotion pins the accepted commit; it does not merge whatever happens to be at the source branch tip.

Artifact-only ephemeral work is fully supported and needs no repository.

## Failure patterns

| Symptom or error | Meaning | Recovery |
|---|---|---|
| Delegation disabled | Parent's effective <code>max_concurrent</code> is zero. | Change the role/agent policy through a revision-checked operator action if delegation is actually intended. |
| Capacity exceeded | Active plus requested assignments exceed the parent limit. | Wait for completion/cleanup or reduce the batch; do not create children under another identity. |
| Task rejected for delegation | Parent does not own it or lacks the project assignment. | Delegate the parent's own bounded work or reassign the task explicitly. |
| Assignment stays requested | Outbox/event/runtime-pack path did not reach core agent creation. | Follow outbox, workflow run, worker configuration, and create-effect result. |
| Spawned session is not marked running | Core creation succeeded but Team lifecycle recording failed. | Preserve the session ID and reconcile via the workflow/effect path before retrying. |
| Git result rejected | Branch differs from the deterministic assignment branch. | Push/report the exact derived branch; investigate possible wrong session checkout. |
| Completed result never becomes deliverable | Parent has not accepted it, or acceptance failed validation. | Wake the parent, inspect evidence, and accept or reject deliberately. |
| Cleanup failed | Core destroy or Team cleanup recording failed. | Preserve the assignment/session evidence, retry cleanup safely, and confirm worker resources are gone. |

## Troubleshooting and handoff

In Team Operations, begin with the assignment rather than the temporary worker. Record the assignment ID, parent, task, session ID if one exists, lifecycle state, and the last transition. If the assignment is still waiting, capture its outbox ID and workflow run. If it finished, capture the summary, artifact references, branch, error, and the parent's decision.

A useful handoff states the boundary that failed: the request was not picked up, the session was created but not recorded, the worker could not report, the parent has not decided, or cleanup is incomplete. Do not delete the assignment after removing leaked compute; it is the evidence that connects the cleanup to the original request.

> <span class="sidebar-kicker">Administrator tracing checklist</span> **Follow one assignment end to end**
>
> 1. Record assignment ID, parent, task, session ID, lifecycle state, outbox ID, and workflow run.
> 2. Follow create → mark spawned → prompt → session routing → report → destroy → cleanup.
> 3. Inspect the worker's only Team command activity.
> 4. Verify branch, artifact refs, summary, and error independently.
> 5. Keep the durable assignment even after fixing leaked compute.

## Delegation runbook

Before delegating:

1. Confirm parent ownership, project assignment, and remaining capacity.
2. Write a bounded prompt with a verifiable output and no implied Team authority.
3. Select backend/model/image only when the defaults are unsuitable.
4. Decide whether the result should be artifact-only or use the deterministic Git branch.
5. Use stable request IDs for the semantic batch.

To accept:

1. Inspect the exact output and verification.
2. Confirm it still applies to the current parent task.
3. Accept with a stable request ID or reject with a concrete reason.
4. Continue through normal deliverable review and Git promotion; do not skip those gates.

## Source trail

- <code>src/evander_team_addon/models.py</code> — ephemeral policy, assignment record, states, result, and validation.
- <code>src/evander_team_addon/services/domain/ephemeral.py</code> and <code>services/ephemeral_service.py</code> — admission, capacity, acceptance/rejection, spawn, result, and cleanup transitions.
- <code>src/evander_team_addon/services/commands/ephemeral.py</code> — parent requests, worker report, deterministic branch validation, and idempotent acceptance.
- <code>src/evander_team_addon/git_naming.py</code> — assignment and review branch derivation.
- <code>src/evander_team_addon/workflow_packs.py</code> and <code>providers.py</code> — composed core create/prompt/destroy lifecycle and session routing.
- <code>docs/addons.md</code> — ephemeral tool scope, artifacts, Git handoff, and runtime flow.
- <code>tests/test_team_addon_mcp.py</code>, <code>tests/test_team_addon_runtime.py</code>, and <code>tests/test_team_addon_outbox_runtime.py</code> — capacity, authority, result validation, workflow composition, and cleanup.
