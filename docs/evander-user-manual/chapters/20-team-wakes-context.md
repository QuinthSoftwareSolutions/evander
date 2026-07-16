# 20. Wakes, Leases, Context Rotation, and Stand-down

> **At a glance.** A wake is Evander's decision to bring a persistent agent back to ready work; it is not the automatic consequence of every message or scheduled check. Before waking an agent, Team considers whether the work is eligible, whether another session is already busy with it, and whether safety or cost limits say to wait. This chapter explains that decision and what happens when an agent should pause, rotate context, or stand down.

## What an operator needs to know

Most wake questions reduce to three checks:

1. **Is there work for this agent that can advance now?** An open task may still be waiting on a dependency or reviewer, while a normal-priority message may be ready immediately.
2. **Is the agent allowed and available to resume?** A live lease, busy session, stand-down, cost cap, or repeated no-progress cycle can delay otherwise ready work.
3. **What should happen to conversational context?** The agent may continue its current context, rotate after becoming idle, or start an eligible wake with a fresh context according to policy.

A wake is therefore a scheduling decision, not a message receipt. The Operations view brings the reasons and blockers together; the exact timing and lease mechanics below explain what that view reports.

## When Team wakes an agent

Team periodically looks for eligible persistent agents, and important events can request a targeted evaluation sooner. When an agent is selected, Team prepares the session, relevant repository, and a current summary of the work before resuming it.

> <span class="sidebar-kicker">Technical sidebar</span> **The wake loop and live defaults**
>
> The registered Team runtime pack runs a periodic evaluation, by default every fifteen minutes. Its wake effect asks the Team runtime to lease due agents. The current service defaults are:
>
> | Setting | Default |
> |---|---|
> | Maximum leases per evaluation | 10 |
> | Dispatch lease | 900 seconds |
> | Normal wake interval | 900 seconds |
> | No-progress threshold | 3 |
> | Low-priority batch threshold | 4 evaluation cycles |
>
> These are provider parameters and may be configured in the live workflow pack. Treat that revision-checked live pack as operational truth.
>
> The wake effect prepares the persistent session, optionally prepares the project's managed repository checkout, constructs a dynamic Team context, and resumes the agent. Static Team guidance is maintained in a managed section of the guest's <code>AGENTS.md</code>; repeated wakes do not need to restate every rule.

## Eligibility is not just “has an open task”

For example, Imani should not wake merely to stare at a deliverable that Rowan is reviewing. Rowan may be the person whose work can advance. If the review is rejected, the resulting rework notice and task state can make Imani eligible again.

An active persistent agent can be eligible because it has:

- ready task work;
- wakeable pending inbox;
- action feedback that requires attention;
- a pending manual or idle context rotation. The <code>every_wake</code> policy, by contrast, marks only an otherwise-eligible wake.

Task work must have satisfied dependencies or open subtasks, and a task waiting on an active deliverable review normally does not wake its producer. Pending review work wakes the assigned reviewer. Completed ephemeral work can wake the parent for acceptance.

The runtime then applies safety guards:

- agent and Team remain active;
- no effective stand-down exists;
- Team is not blocked by its cost cap;
- no live Team dispatch lease exists;
- core session is not already busy;
- normal wake interval has elapsed where applicable;
- no-progress suppression does not block task-only polling.

The manager fallback is last, not first. If a Team has open work but no member obtained a lease, the due Team manager can receive a broad routing summary. It exposes coordination gaps without waking the whole hierarchy.

![Team agent wake decision flow](../../assets/evander-user-manual/diagrams/13-team-wake-decision.svg)

The narrowing path is intentional: having something pending is only the first test, and each later check can safely defer the wake.

<p class="figure-caption">Figure 20.1 — Eligibility is filtered through readiness, priority, batching, and runtime safety guards.</p>

## Inbox wake semantics

The exact rule for whether a pending inbox item can wake is:

<code>response_requested OR priority is not low</code>

This means:

| Priority | <code>response_requested</code> | Wake behavior |
|---|---:|---|
| high | false or true | Wakeable immediately, subject to safety guards and debounce. |
| normal | false or true | Wakeable immediately, subject to safety guards and debounce. |
| low | true | Wakeable, but normally enters low-priority batching. |
| low | false | Not a wake reason by itself. It is still delivered with a later wake and should eventually be handled. |

Do not interpret <code>response_requested=false</code> as “do not deliver” or “cannot wake.” It says the recipient should not send a no-op acknowledgement. Priority controls urgency; reply intent controls expected response.

Normal/high inbox creation emits a debounced <code>team.agent.wake_requested</code> event, with a default ten-second debounce. The event path targets that agent and uses <code>respect_wake_interval=false</code>. It still respects active leases, busy sessions, stand-down, cost, and other safety checks.

Low-priority items accumulate evaluation cycles and are batched after the configured threshold unless another reason wakes the agent earlier. The resulting context includes the pending inbox, so a low/no-reply progress report can be read during useful work without causing a conversational ping-pong.

## Deeper detail: leases and dispatch identity

A lease is Team's temporary claim that one dispatch is responsible for this persistent agent's current work. It prevents a second scheduler pass from starting another Team dispatch at the same time, and it gives later commands and completion events an exact job identity to match.

Leasing assigns:

- a new <code>dispatch_id</code>;
- wake reason;
- <code>last_wake_at</code>, <code>next_wake_at</code>, and lease expiry;
- the dynamic tasks, cases, inbox, reviews, releases, feedback, reporting context, and visible operator-chat participants.

The active dispatch ID is an authorization boundary. Normal Team commands must come from the bound session and active lease. A lease prevents a second scheduler pass from dispatching the same agent concurrently.

The workflow's session-completion route calls <code>effect:team:finalize-agent-dispatch</code> with session, agent, and dispatch IDs. The finalizer rejects unrelated or stale completions and reads the durable activity ledger. Individual MCP tools never close the lease.

Successful activity counts as substantive progress only when its progress classification says so. Failure activity does not. The finalizer resets <code>no_progress_count</code> on progress and increments it otherwise. Repeated task-only no-progress dispatches are suppressed; inbox, action feedback, and required context handling can still surface important new work.

## Context rotation

Each persistent agent has one of three policies:

| Policy | Behavior |
|---|---|
| <code>off</code> | No automatic context rotation. Manual rotation remains possible. |
| <code>idle</code> | After substantive activity, when the agent becomes truly idle, prepare a durable handoff and rotate on its next eligible wake. |
| <code>every_wake</code> | Mark an otherwise-eligible wake to start with a fresh context. It does not make an idle agent eligible by itself. |

“Truly idle” means no active lease, active tasks, pending wakeable work, reviews, or ephemeral acceptance needing that agent. The system first creates a handoff summary so a new conversation does not discard durable orientation.

A pending rotation is cleared only after the new dispatch starts successfully. If session startup fails, it remains pending. Manual rotation can queue a change even when the automatic policy is <code>off</code>.

For Northstar, Imani might use <code>idle</code>: the telemetry implementation stays in one context while active, then rotates after all substantive work is handed off. Rowan might use <code>every_wake</code> for strongly isolated review passes. That does not make Rowan poll constantly; Rowan still needs a review or other wake reason.

## Stand-down

Stand-down is a durable operational hold, not deletion and not a prompt suggestion.

There are two sources:

- **operator** — set through the Admin surface and revocable only through the operator path;
- **superior** — set by an authorized direct superior and revocable by the agent that set that superior hold.

A stand-down applies to the target's reporting subtree. The runtime skips every affected agent. The command executor also checks effective stand-downs, so a dispatch that was already running cannot continue mutating Team state after the hold becomes active.

Revocation clears only the holds the caller is authorized to clear. If another inherited hold remains, the agent stays down. When the last effective hold clears, ready work can be made due for fresh evaluation.

Use stand-down for a real operational reason: incident containment, unsafe output, cost investigation, credential concern, or explicit operator pause. Preserve the recorded reason and actor.

## Northstar wake sequence

1. Priya creates Imani's telemetry retry task. A ready-work event is queued.
2. The wake-request route leases Imani if the session is free, the Team is under its cap, and no hold exists.
3. Imani's context contains the task, case <code>OBS-184</code>, project Git checkout information, reporting line, and relevant inbox.
4. Imani records commands. The lease remains active.
5. Session completion finalizes the exact dispatch. Substantive activity resets no-progress state.
6. Deliverable submission moves the task to review and queues Rowan rather than waking Imani to poll.
7. If Rowan rejects it, the normal-priority, response-requested rework notice wakes Imani for a successor deliverable.
8. If an operator stands down Imani during investigation, new commands fail and no new lease is granted until authorized revocation.

## Deeper detail: runtime and workflow surfaces

The bundled pack coordinates:

- the cron wake evaluation;
- debounced <code>team.agent.wake_requested</code> events;
- core session completion classified by <code>query:team:session-routing</code>;
- persistent dispatch finalization;
- Chat claims, governance wakes, ephemeral lifecycle, outbox publication, Git, release, and escalation routes described later.

The primary runtime provider is <code>effect:team:wake-due-agents</code>. Operators should inspect its outputs, including leased agents and repository-preparation failures, rather than assuming a workflow run with no prompt is a model failure.

The Operations view and Team runtime-attention reads expose wake due state, leases, stand-down, context policy, no-progress counters, and downstream failures. Core session logs remain necessary for startup and provider failures.

## Troubleshooting

Begin in **Operations** with the agent's wake reason, runtime attention, active lease, stand-down, and context policy. Then compare the task, review, inbox, or action feedback that should have made the agent eligible. The table below helps distinguish expected scheduling from a stuck workflow.

| Symptom | What to check |
|---|---|
| Normal-priority no-reply mail woke an agent | This is correct: non-low priority is wakeable independently of reply intent. |
| Low-priority report does not wake | Confirm whether it requests a response, the batch cycle count, and whether another wake delivered it. |
| Task owner never wakes | Check dependency status, active deliverable review, interval, no-progress count, Team cap, stand-down, busy session, and active lease. |
| Two prompts seem concurrent | Compare core session activity and Team <code>active_dispatch_id</code>; one may be a non-Team wake or an expired/stale completion. |
| Lease remains after the model stopped | Session completion may not have routed or matched the active dispatch. Inspect session routing and finalizer workflow run. |
| Context rotates repeatedly | Verify policy, pending flag, session startup result, and whether successful start clears it. |
| Revocation appears ineffective | Another operator or inherited superior hold may remain active. Inspect the effective stand-down list. |
| Manager wakes without owned tasks | Manager fallback found open Team work with no member lease; inspect the supplied routing summary. |

## Operator handoff

Capture the Team, agent, core session, last wake reason and time, current task or review, visible inbox priority and reply intent, effective stand-down, and any dispatch or workflow-run ID. For “did not wake,” state the specific work you expected to be eligible. For “woke unexpectedly,” record the reason shown in Operations rather than inferring it from the latest message alone.

Do not clear a lease, no-progress counter, or stand-down directly. If the visible records do not explain the decision, give those identifiers and the last known Team revision to an administrator.

## Administrator runbook

For “why did this agent wake?”:

1. Record agent, session, dispatch ID, wake reason, Team revision, and workflow run.
2. Read the lease payload and pending inbox at that revision.
3. Apply the exact inbox formula and inspect task dependencies, action feedback, and context rotation.
4. Verify safety inputs: busy session, effective stand-down, cap, interval, and no-progress.
5. Follow the matching prompt and session-completion run.

For “why did this agent not wake?”:

1. Confirm the agent and Team are active and the agent has a valid Team/session mapping.
2. Prove an eligible reason exists; an open but review-waiting task is not enough.
3. Inspect the Operations/runtime-attention payload.
4. Check the live runtime-pack parameters and service state.
5. Fix the blocking condition through its owner, then verify a new debounced event or next cron evaluation.

Do not clear leases, no-progress counters, or stand-down rows directly in SQLite. The supported services preserve the correlations needed for safe finalization.

## Source trail

- <code>src/evander_team_addon/services/runtime_service.py</code> — leasing, low-priority cycles, busy-session guard, manager fallback, finalization, and attention payloads.
- <code>src/evander_team_addon/services/domain/wake.py</code> — exact inbox wake predicate, ready-work rules, debounce, and follow-up wake decisions.
- <code>src/evander_team_addon/services/domain/context_rotation.py</code> and <code>stand_down.py</code> — context policies, durable handoff, subtree holds, and revocation.
- <code>src/evander_team_addon/providers.py</code> and <code>workflow_packs.py</code> — wake effect, session routing, prompt construction, and event paths.
- <code>src/evander_team_addon/models.py</code> — runtime lease, context, inbox priority, and stand-down records.
- <code>docs/team-addon-wake-decision-tree.md</code> and <code>docs/addons.md</code> — operator-oriented decision tree and integration overview.
- <code>tests/test_team_addon_runtime.py</code> and <code>tests/test_team_addon_outbox_runtime.py</code> — eligibility, batching, no-progress, Chat/busy guards, rotation, and event publication.
