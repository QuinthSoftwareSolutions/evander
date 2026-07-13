# 13. Team Add-On

The Team Add-on is an optional operator surface for durable team-based work with persistent agents.
Use it for shared ownership, project planning, agent-to-agent coordination, review, governance, and
controlled delivery beyond one-off sessions.

The add-on is enabled host-wide from `/#addons`, not per directory. Its data and actions can still be
directory-, team-, project-, role-, and permission-scoped. If the Teams navigation item is missing,
check the core Add-ons page before troubleshooting Team data.

For worker health, use section 18. For workflow runtime, use section 11. For artifacts, use section
10. For permissions, use section 17.

## 13.1 Team Concepts

1. **Team**: the owning unit for persistent agents, roles, work, and policy context.
2. **Persistent agent**: a reusable Team agent linked to an Evander session and eligible for Team wakes.
3. **Project**: a delivery scope with a lead, goals, tasks, optional managed Git repository, and release policy.
4. **Task**: an execution unit with owners, dependencies, child tasks, status, and optional case linkage.
5. **Case**: an optional intake envelope that groups related tasks and an external source reference.
6. **Inbox item**: structured work or coordination sent to an agent through Team runtime.
7. **Agent Mail**: durable agent-to-agent messages, separate from operator Chat.
8. **Memory**: durable team-owned decisions, facts, runbooks, handoffs, and other reusable context.
9. **Deliverable**: concrete output linked to task work, artifacts, and review requests.
10. **Review profile**: the criteria an eligible reviewer must evaluate.
11. **Release package**: reviewed output prepared for controlled artifact or Git publication.
12. **Governance group**: cross-team coordination with membership, agenda, summaries, and wake state.
13. **Stand-down**: a durable operator hold on an agent and its subordinate reporting subtree.

Team state is canonical in the Team Add-on, not in core session metadata. Core session records remain
the execution link and a compact integration index.

## 13.2 Navigation And Daily Sequence

The current Team UI has fourteen top-level views:

1. **Workspace**: high-level status, attention, and shortcuts.
2. **Operations**: runtime attention, leases, actions, failures, and operator controls.
3. **Delivery**: deliverables, review requests, review profiles, release packages, and Git promotion.
4. **Memory**: team memory search, record detail, scope, tags, refs, and archive state.
5. **Projects**: project ownership, goals, Git settings, and release policy.
6. **Task board**: task ownership and status movement.
7. **Cases**: optional intake records and linked task context.
8. **Teams**: teams, persistent agents, roles, setup, direct calls, and stand-down controls.
9. **Chat**: signed-in operator conversations with persistent agents.
10. **Mail**: Agent Mail inbox/outbox records and delivery state.
11. **Governance**: governance groups, membership, agenda, summaries, and coordination state.
12. **Planning**: task dependency, decomposition, and readiness context.
13. **Timeline**: derived read-only recent activity and attention signals.
14. **Org chart**: manager and reporting hierarchy.

A practical daily sequence is:

1. Open **Workspace** for the broad state.
2. Use **Operations** for urgent runtime attention and failed actions.
3. Review **Mail**, **Chat**, and **Governance** for coordination that needs a response.
4. Use **Projects**, **Task board**, **Cases**, and **Planning** to assign or unblock work.
5. Use **Delivery** for review and publication.
6. Use **Timeline** and section 7 Tool Calls for evidence and handoff.

The header privilege lock gates mutations, not Team reads. Unlock only when you are ready to save or
run an operator action.

## 13.3 Enable Or Repair The Team Surface

1. Open the core **Add-ons** page at `/#addons`.
2. Select **Team Add-on** and inspect discovered, enabled, loaded, error, configuration-mismatch,
   runtime-pack, and pending-restart state.
3. If enabling it, unlock and save. The save updates host and worker add-on configuration and
   materializes the registered Team runtime pack through the revision-checked workflow service.
4. Use **Refresh runtime pack** when the registered definition changes. Never write the live pack
   file directly.
5. Restart every service listed by the page. Team requires Admin Web, trigger orchestrator, effect
   orchestrator, and gateway because it registers UI routes, workflow providers, and MCP tools.
6. Reload Teams and confirm all fourteen views are present.

Disabling the add-on prevents it from loading after restart but does not delete Team private state.

## 13.4 Create A Team And Persistent Agents

Template setup is the fastest route:

1. Open **Teams** and choose template setup.
2. Select `auto-dev-squad` or `accounting-department`.
3. Provide stable team, manager, project, project-lead, role-owner, and session identifiers.
4. Add optional project Git inputs only through the managed repository flow.
5. Preview the hierarchy, roles, project, and starter tasks.
6. Materialize once. The operation is revision-checked and atomic.
7. Confirm the result in **Teams**, **Projects**, **Task board**, and **Org chart**. Cases appear only
   when the template or later intake creates them.

For manual setup:

1. Create the team and manager in **Teams**.
2. Create narrow roles and assign only required permissions.
3. Register persistent agents with stable agent IDs, sessions, roles, and reporting lines.
4. Create a project and choose an existing registered Team agent as project lead.
5. Verify the hierarchy in **Org chart**.

Roles are managed within the Teams surface; there is no separate top-level Roles view. Display names
and avatars are operator UI metadata. Avatars are not injected into prompts or session identity.

## 13.5 Projects, Task Board, Cases, And Planning

Use **Projects** to define the delivery boundary, lead, goals, optional managed repository, base branch,
and release policy. Import a repository through the managed repository action; do not store host
checkout or bare-repository paths in Team project state.

Use **Task board** for execution state:

1. Create one task per meaningful output.
2. Assign an eligible persistent agent or approved ephemeral path.
3. Add dependencies and child tasks only when sequencing or decomposition is real.
4. Use **Planning** to confirm dependency readiness, blockers, and child-task state.
5. Move task state only when its work and dependent records support the transition.

Use **Cases** when work needs an intake envelope:

1. Create a case with source reference, owner, priority, tags, and status.
2. Create or link tasks from **Task board**.
3. Keep case status aligned with the linked work.

Cases are optional. A project and task do not require a case, and wake investigation should start
from the actual assigned task rather than assuming a case exists.

## 13.6 Chat, Agent Mail, And Direct Calls

**Chat** is one signed-in human operator conversing with one persistent agent. Each operator sees
only their own conversations by default. `team_chats.read_all` permits read-only inspection of other
operators' Chat histories and disables sending in that inspection mode.

**Mail** is Agent Mail for durable agent-to-agent coordination. Use it to inspect sender, recipient,
thread/message context, inbox/outbox state, and delivery status. Do not treat Mail as another view of
operator Chat, and do not assume `team_chats.read_all` expands Mail access.

To review Agent Mail:

1. Open **Mail**.
2. Narrow to the relevant team, agent, or message state.
3. Open the message and record sender, recipient, time, delivery state, and linked work context.
4. Follow up in **Task board**, **Governance**, or **Operations** when the message identifies blocked
   work or a runtime problem.
5. Use **Timeline** and Tool Calls when stronger evidence is required.

To chat with an agent:

1. Open **Chat** and select the persistent agent.
2. Confirm whether the view is your conversation or read-only inspection.
3. Send a concise request with the expected output when sending is allowed.
4. If no response arrives, use the wake checks in 13.11 rather than sending duplicates.

The **Teams** view can also start a browser voice call for a selected persistent agent when the voice
bridge is configured. The projected call context includes current Team work and runtime state. After
ending the call, confirm the reconciliation wake or follow-up record before assuming action was saved.

## 13.7 Team Memory

Memory records preserve durable decisions, preferences, facts, runbooks, handoffs, research notes,
and postmortems. Canonical records live in Team state; any semantic index is derived and is not the
source of truth.

Use **Memory** to:

1. Search by text, team, project, agent, scope, tags, or references.
2. Open record detail before relying on a summary.
3. Confirm whether a record is team-global, project-scoped, agent-private, or governance-scoped.
4. Follow linked artifact or artifact-space references for bulky evidence.
5. Treat archived or superseded records as history, not current instruction.

Agents record and maintain memory through Team tools. Normal wakes do not automatically inject all
memory; agents or workflows must explicitly search when recall is useful. Keep large evidence in
artifacts and link it from a concise memory record.

## 13.8 Delivery, Reviews, And Publication

Use **Delivery** for the complete review-to-release path.

1. Submit a deliverable with task link, content summary, and stable artifact references.
2. Attach the appropriate review profile.
3. Create a review request for an eligible reviewer.
4. Record criterion findings before deciding.
5. Choose `accepted`, `rejected`, or `blocked` with a concrete reason.
6. Confirm the review, deliverable, task, and project status cascade.

An accepted deliverable can advance only after all required reviews are accepted. Rejected work
requires corrected output; blocked is reserved for a process or evidence-access blocker.

Artifact-only delivery remains valid without Git. For Git-backed delivery, the controlled ladder is
agent output, review branch, feature branch, release branch, then canonical push. Internal branches
remain in the managed bare repository. Only the pinned, approved release SHA is pushed outward under
an explicit lease.

Before requesting release:

1. Confirm tasks and required deliverables are complete.
2. Confirm every required review is accepted.
3. Resolve dependency and scope blockers.
4. Confirm the project release policy and authorized requester.
5. Request the release once and watch its immutable attempt state in **Delivery**.
6. Use the typed retry action only for an eligible retryable gate failure.

## 13.9 Operations And Timeline

Use **Operations** for actionable runtime state: attention, leases, wake decisions, failed actions,
cost guards, and operator controls. A later successful action can supersede a failed attention item;
dismissal changes the attention presentation, not the underlying evidence.

Use **Timeline** for a derived, read-only sequence of recent Team state. Filter by team, project,
agent, type, status, or attention. It is useful for sequencing and handoff but is not an append-only
audit log and cannot reconstruct every overwritten intermediate state. Use Tool Calls and workflow
run inspection when audit strength matters.

## 13.10 Governance

Governance coordinates work across teams without turning every issue into direct operator chat.

1. Open **Governance** and choose the governance group.
2. Confirm membership and active group state.
3. Review agenda items, member-team summaries, blocked/cost-warning context, and pending coordination.
4. Resolve the owning Team record first when an agenda item points to a task, delivery, or runtime
   blocker.
5. Record or route the decision through the governance workflow so the next manager wake receives
   current summaries.
6. Confirm resulting state in **Governance**, **Operations**, and **Timeline**.

Governance memory is a distinct scope. Do not copy sensitive agent-private memory into a governance
record merely to make it broadly visible.

## 13.11 Wake Behavior And Stand-Down

An agent can wake for ready task work, response-requested inbox items, review/rework, operator
directives, cadence checks, manager fallback, Agent Mail coordination, or governance work.

An agent may not wake because:

1. no eligible reason is due,
2. a lease is already active,
3. task dependencies are waiting or blocked,
4. low-priority work is waiting for batching,
5. a Team cost guard is active,
6. the agent is inactive,
7. the agent or an ancestor has an active stand-down.

A stand-down is a durable operator hold on the selected agent and its subordinate subtree. It blocks
future Team leases and rejects later Team mutations from already-running affected dispatches. Revoke
it explicitly after the issue is corrected; ordinary Team tools cannot override it.

To investigate a silent Team agent:

1. Open **Operations** and filter by the agent.
2. Confirm the assigned task in **Task board** is active and dependency-ready in **Planning**.
3. Check response-requested inbox and Agent Mail state.
4. Check active leases, cost guard, agent active state, and stand-downs on the reporting chain.
5. Confirm an actual wake reason exists.
6. Use **Timeline** for recent Team transitions.
7. Check trigger/effect workers and the Team runtime workflow in sections 18 and 11.
8. Avoid duplicate prompts while lease or dispatch state is unknown.

## How-to: Stand Down And Restore An Agent

1. Open **Teams** and select the persistent agent.
2. Unlock and choose the stand-down action.
3. Enter a concrete reason and confirm.
4. Review subordinate impact in **Org chart**.
5. Confirm the hold in **Operations** and watch any already-running dispatch finish or be rejected.
6. After corrective work, revoke the stand-down with a reason.
7. Confirm the agent and subordinate subtree regain normal wake eligibility.

## How-to: Handle A Delivery Blocker

1. Open **Delivery** and select the immutable review or release attempt.
2. Read the criterion findings, gate result, candidate SHA, artifact refs, and blocker type.
3. Open the owning task in **Task board** and its dependencies in **Planning**.
4. Correct the source work or process/evidence blocker without mutating the old decision.
5. Submit a new deliverable or use the allowed typed retry path.
6. Confirm the new attempt and final publication state in **Delivery** and **Timeline**.
