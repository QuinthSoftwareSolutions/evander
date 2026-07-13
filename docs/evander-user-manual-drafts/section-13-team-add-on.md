# 13. Team Add-On

The Team Add-on is an optional operator surface for running durable team-based work with persistent
agents. Use it when you need shared ownership, review workflows, and delivery tracking beyond one-off
sessions.

You will see this surface only after the Team Add-on is enabled in host config and the add-on routes are
available under **Teams** in the Admin UI.

At a high level:

1. Teams define people structure, reporting lines, and operational scope.
2. Persistent Team agents do repeated work and can receive structured work through wake-ups.
3. Cases and tasks keep delivery work in a visible chain.
4. Deliverables and reviews let operators control what is safe to ship.
5. Optional release tooling publishes accepted delivery state to Git in a controlled sequence.

For worker health, use section 18. For reports and repeated-failure trends, use section 12. For session
level controls and approvals, use sections 5 and 6. For files and published bundles, use section 10. For
workflow references behind Team automation, use section 11.

## 13.1 Team Concepts

Use this subsection to build a shared operational model before touching setup forms.

Core concepts:

1. Team: the owning unit of work and policy context.
2. Manager: the top-level team lead for escalations and routine direction.
3. Project lead: the person or agent accountable for a project end-to-end.
4. Persistent team agent: a reusable agent record that can be assigned tasks repeatedly and can wake on
   new work signals.
5. Role: a named permission bundle used by agents for delivery and approval decisions.
6. Case: an intake envelope for a stream of work with one source reference, status, priority, and tags.
7. Task: the unit of execution that can be attached to a case, ordered by dependencies.
8. Inbox item: a request or update item in Team flow.
9. Deliverable: concrete output tied to work, often linked to artifacts and review requests.
10. Review request: a formal review checkpoint with a reviewer, rubric, and decision outcome.
11. Review profile: the rubric template that standardizes what a reviewer evaluates.
12. Release package: the final package state when a project is ready to publish.
13. Governance group: a higher-level coordination layer for cross-team escalation.

Important operator boundaries:

1. Team Add-on state is tracked separately from core Evander session records.
2. Team actions are surfaced as Team-specific admin tabs and APIs.
3. Team-specific records are still constrained by directory scope and your normal unlock/policy rules.

## 13.2 Teams Tab Overview

When the Team Add-on is enabled, the **Teams** tab opens a dedicated workspace with several areas.

1. Setup area: create a team from a template or manual input.
2. Workspace panels: overview of active cases, open tasks, live notifications, and pending work.
3. Org chart: visual hierarchy of manager and subordinate lines.
4. People view: persistent agent inventory, role context, and edit actions.
5. Chat view: operator-to-agent chat and team chat queue inspection.
6. Roles view: role catalog and permissions.
7. Cases view: create/edit case records and assign task links.
8. Review and Delivery workspace: submit deliverables, open review requests, and inspect publication status.
9. Timeline/Audit view: read-only sequence of recent Team activity and attention signals.

Use this layout sequence during daily operations:

1. Open Teams.
2. Confirm directory and unlock state.
3. Check Timeline/Audit for newly surfaced attention items.
4. Open People or Cases depending on whether people or intake is the current bottleneck.
5. Finish in Review and Delivery when any task state is near completion.

If the tab is missing:

1. Confirm Team Add-on configuration.
2. Confirm the workflow workers have been restarted after any add-on configuration change.
3. Refresh and re-open after service config updates.

## 13.3 Set Up A Team

You can create a team either from a proven template or from manual setup.

Template setup is fastest:

1. **auto-dev-squad** creates a manager, implementer, reviewer, and release-operator pattern for reviewed
   development work.
2. **accounting-department** creates bookkeeping-oriented roles for accounting flows and review control.

Manual setup gives more flexibility:

1. Use when team shape does not match existing templates.
2. Use when you must align with strict naming and role rules.

Template setup flow (operator view):

1. Open **Setup from template**.
2. Select the template.
3. Fill required inputs:
   1. team id and name
   2. team manager agent and session
   3. project id and name
   4. project lead id/session
   5. implementer, reviewer, and release operator role owners
4. (Optional) add project git inputs if deliveries use project Git.
5. Click preview.
6. Validate hierarchy, role mapping, starter tasks, and required references.
7. Confirm any optional fields such as operator communication target and communication allowlist.
8. Submit and wait for materialization.

Expected result:

1. Team, project, roles, and starter records appear immediately in People, Cases, and Org chart.
2. Case/task/review scaffolding appears where included by the template.

Manual setup:

1. Open setup form.
2. Add team name, manager, and initial project.
3. Add initial roles, then at least one team agent and project lead.
4. Set sessions and operator communication target after creation.
5. Save with a quick verification pass.

Notes:

1. Template materialization should be treated as one atomic action.
2. If required inputs are missing, the system will block save and show exactly what needs completion.
3. For non-trivial project/git mapping, the form can import existing repo data, but you still need to review and
   submit manually.

## 13.4 Manage People And Roles

People and roles in Team are your first control surface for quality and continuity.

1. Register an agent record:
   1. create or edit the persistent agent entry
   2. bind an agent id and session
   3. assign role
   4. assign manager/supervisor
2. Check hierarchy:
   1. manager should be above direct reports
   2. project lead visibility in dashboard and task assignment
3. Use display names for readable operational views.
4. Use avatar upload or generated preview to distinguish people in UI quickly.

Roles:

1. Roles are not decorative labels; they carry permissions.
2. Review role permission scope before assigning any review or release-capable agent.
3. Keep role boundaries narrow if operators rely on this team for high-risk actions.

How to verify role setup:

1. Open Roles.
2. Confirm role assignment appears for each active person.
3. Confirm permission summary for review/release/research scope.
4. Confirm no one is in a role that grants unintended release capability.

If a role mismatch is found:

1. Edit the role record.
2. Re-assign the affected agent.
3. Save.
4. Recheck the target task owner, because pending decisions are already scoped to actor.

## 13.5 Cases, Goals, And Tasks

Cases keep related work coherent when many tasks arrive quickly.

Case model:

1. You can link one external reference, such as issue id or ticket number.
2. You can set status (`open`, `triage`, `in_progress`, `waiting`, `resolved`, `closed`).
3. You can set priority (`low`, `normal`, `high`).
4. You can assign tasks to cases and keep the task list visible in one place.

Task model:

1. Tasks are the execution unit that agents work on.
2. Tasks can be linked to cases.
3. Tasks can depend on other tasks (`blocked` until upstream work is complete).
4. Tasks can have child tasks for decomposition.

Operating pattern:

1. Start from a case with clear scope.
2. Add one task per meaningful output.
3. Assign owners and links.
4. Add blockers and priorities if sequencing is required.
5. Assign outcomes as tasks progress.

For deeper task graph diagnostics or repeated failures, continue in section 11.

## 13.6 Team Chat And Direct Calls

Team chat is separate from general notification settings, so operators can keep agent state and conversation
history in one place.

Chat basics:

1. Chat is one-to-one operator-to-agent.
2. Agents can send and receive messages from their inbox context.
3. Each operator normally sees only their own conversations by default.
4. `team_chats.read_all` is a read-only inspection permission; when used, the UI labels this state clearly
   and disables sending.

Direct calls:

1. People view includes a direct browser call option for selected persistent agents.
2. Calls use projected voice context, including current Team task and runtime status.
3. Calls are recorded in voice-session state and can be reconciled back into normal Team wake workflows.

What to expect after a call:

1. The reconciliation wake usually brings back summary and follow-up intent.
2. If action is required, check the related case/task and decide whether to post operator directions.
3. For broader communication target setup, see section 15.

## 13.7 Deliverables And Reviews

Deliverables are how work is handed to review and, eventually, release.

Submit and request flow:

1. Submit deliverable text, artifact references, and relevant metadata.
2. Attach a review profile with clear criteria.
3. Create a review request and route to an eligible reviewer.

Decision behavior:

1. Accepted: deliverable may proceed only when all required review requests for that deliverable are accepted.
2. Rejected: review is rejected and task is moved to blocked until corrected.
3. Blocked: task, review, and deliverable are all blocked for that request path.

Decision cascade summary:

1. A rejected or blocked review can hold dependent work.
2. All required task outputs and review decisions are checked together before project-level transitions.
3. Use the Review and Delivery workspace to confirm cascade status.

Operational tips:

1. Attach all relevant artifacts up front.
2. Keep reviewer profiles explicit and stable between related deliverables.
3. For artifact-only work with no Git flow, publish as artifact references and still keep review gating.

## 13.8 Git Promotion And Releases

When your team uses Git-backed delivery, promotion follows a controlled ladder:

1. Agent output branch or committed source.
2. Review branch.
3. Feature branch.
4. Release branch.
5. Canonical branch push.

Project Git setup stores a managed repository ID, optional base-branch override, and canonical
release-prefix policy. Use the project's **Import Git repo** action to submit a local checkout or
canonical URL transiently; Evander registers/refreshes it inside the directory trust boundary and
keeps provider identity in the central registry. Do not put a host checkout or bare-repository path
in Team project state.

Use this flow for traceability and consistent operator control:

1. Ensure deliverable status is ready for publish.
2. Publish/prepare review branch if required.
3. Consolidate review output into a task feature branch.
4. Request project-level release package only with valid approved tasks.
5. Request release.
6. Watch promotion and publication state until canonical result is final.

Who can request release:

1. Project lead.
2. Team manager.
3. A role with explicit release permission.

What publication output means:

1. Internal branches are maintained inside the project's managed bare repository.
2. Only the canonical route pushes the pinned, approved SHA outward under an explicit lease.
3. Non-Git artifact deliveries remain valid as artifact-based package flows.

If release is blocked, review missing prerequisites and error state before retrying.

## 13.9 Timeline And Attention

The Timeline view is the first place to answer "what changed recently and what needs action now?"

Use filters for:

1. team id or project id
2. agent id
3. entry type
4. status
5. attention needed true/false

Interpretation:

1. Timeline entries are read-only.
2. Entries are ordered by timestamp when available; missing timestamps are marked clearly as unavailable.
3. Attention items usually point to open tasks, pending inbox handling, failed review outcomes, and release states.

For review fatigue reduction:

1. Dismiss failed-action attention cards you intentionally de-prioritize.
2. Keep notes in handoff to avoid silent policy drift.

Do not confuse timeline with full audit log:

1. It is practical for operator sequencing.
2. For stronger evidence trails, use section 7 and Tool Calls references.

## 13.10 Wake Behavior And Stand-Down

Understanding wake behavior prevents false alarms and missed work.

Why a team agent wakes:

1. New high-signal inbox item with response requested.
2. New task work that is now ready.
3. Pending review or rework request.
4. Explicit operator directive in the Team context.
5. Periodic normal wake checks from the Team cadence.
6. Manager fallback when a team has open work and no active normal wakeable manager is already running.
7. Governance wake requests for active groups and blocked/cost-warning states.

Why a team agent may not wake:

1. The agent is stand-down and marked inactive.
2. Agent is not active in current scope.
3. Team is under cost guard and is being paused.
4. Another lease is already active and not expired.
5. Task dependencies block execution path.
6. Low-priority inbox has not reached the low-priority wake threshold.
7. A stand-down exists on a higher node in reporting line.
8. No eligible wake reason is due in the current tick.

Operational rule:

1. High and normal inbox causes can wake promptly.
2. Low-priority items are visible immediately but may wait for batching cycles before dispatch.
3. Stand-down state skips future wake eligibility until revoked.

## How-to: Create A Team From A Template

Use this when you want standardized structure quickly.

Prerequisites:

1. Team Add-on is enabled and visible in Teams.
2. Correct directory selected.
3. Privileges unlocked.

Steps:

1. Open **Teams** in the left navigation.
2. Open **Setup from template**.
3. Click **Create from template**.
4. Select `auto-dev-squad` or `accounting-department`.
5. Review required inputs and complete them:
   1. team id and name
   2. manager agent and session
   3. project id and name
   4. project lead id and session
   5. implementer, reviewer, and release operator identity/session fields
6. If applicable, import/register the project repository and enter its managed ID, base-branch
   override, and canonical release-prefix policy.
7. Open the preview and review role hierarchy and initial tasks.
8. Click **Materialize**.
9. Confirm team, people, and case scaffolding appears in the workspace.

Expected result:

1. Team hierarchy and initial people records are visible in People and Org chart.
2. Cases, tasks, and review profile scaffolds appear where defined by the template.

If materialization is blocked:

1. Re-check required fields.
2. Clear stale directory lock or stale session scope.
3. Retry after one refresh.

## How-to: Register Or Edit A Team Agent

Use this when an agent should be added, re-bound, or corrected.

Prerequisites:

1. Teams tab open and correct directory selected.
2. Privileges unlocked for edit.
3. Target session exists and is intended for persistent role use.

Steps:

1. Open **People**.
2. Select the relevant agent node or click **Create agent**.
3. Enter a stable agent id and display name.
4. Choose role and manager/reports-to relationship.
5. Link the correct session id.
6. Add optional avatar image or request generated avatar.
7. Save changes.
8. Return to Org chart and confirm the reporting line.

Expected result:

1. Agent appears in People with the selected role and reports-to.
2. Org chart hierarchy updates to show inheritance.

If save is blocked:

1. Confirm session belongs to the active directory.
2. Confirm role fields are valid.
3. Confirm any stand-down or active role constraints are resolved.

## How-to: Create A Case And Assign Work

Use this to create structured intake when recurring work is landing.

Prerequisites:

1. Teams tab open.
2. Active directory set.
3. Privileges unlocked.

Steps:

1. Open **Cases**.
2. Click **Create case**.
3. Enter title, owner, and source reference.
4. Set priority and initial status.
5. Save the case.
6. Open the case and add the first task.
7. Set task owner and team assignment.
8. Link the task to this case.
9. Add dependency or subtask links if needed.
10. Save task state.
11. Open People and verify the assigned agent is aware of new or updated task context.

Expected result:

1. Case is visible in case list and timeline.
2. The case shows as open work in Team context and can trigger wake behavior.

If the case does not enter wake context:

1. Confirm task assignment to an active agent.
2. Verify case/task status is not terminal.
3. Confirm no stand-down exists on that agent or reporting chain.

## How-to: Chat With A Team Agent

Use this to get direct status, clarification, or handoff notes from one persistent agent.

Prerequisites:

1. Team agent is active and not stood down.
2. Chat feature available in the agent record.
3. You have permissions in the current directory.

Steps:

1. Open **People** or **Chat** view.
2. Select the target agent.
3. Open the chat thread.
4. Send a concise question with expected output format.
5. Save/send the message.
6. Wait for reply and response status update.
7. If read-only inspection mode is enabled, confirm it is visible and note limited actions.
8. If reply arrives with no decision, create an operator directive or task note with context.

Expected result:

1. New messages appear in the thread.
2. Agent reply comes back with current task context.
3. Thread is linkable to case/task context from People or Timeline.

If no reply appears:

1. Recheck message sent state.
2. Verify wake conditions in section 13.10.
3. Escalate only if communication target configuration is missing (see section 15).

## How-to: Start A Browser Call With A Team Agent

Use this to resolve complex or urgent context-sensitive coordination.

Prerequisites:

1. Team Add-on People view available.
2. Target agent has active task context.
3. Browser call permissions and runtime voice bridge are configured.

Steps:

1. Open **People**.
2. Select the target persistent agent.
3. Open the direct call control.
4. Start the browser call.
5. Confirm call connection and speaking prompt.
6. State the objective and expected decision at start of the call.
7. End call only after action items are summarized.
8. Open call history and confirm reconciliation state.

Expected result:

1. Call starts and the agent receives projected context.
2. Reconciled wake or follow-up note appears in Team context after call end.

If the call does not connect:

1. Confirm call bridge health in host runtime checks.
2. Retry once.
3. Fall back to chat and create a task follow-up.

## How-to: Request And Decide A Review

Use this whenever you need explicit quality signoff.

Prerequisites:

1. Deliverable is staged and linked to task.
2. Review profile is available for the project/reviewer role.
3. Review decision surface is accessible.

Steps:

1. Open **Review & Delivery** workspace.
2. Choose the target deliverable.
3. Create a review request.
4. Attach one review profile.
5. Add reviewer and expected outcome criteria.
6. Attach required deliverable artifacts.
7. Send for review.
8. Open the review request and read importer status.
9. Record findings against criteria.
10. Choose decision:
    1. accepted
    2. rejected
    3. blocked
11. Save decision.
12. Confirm task and review status changes.

Expected result:

1. Review decision is recorded.
2. Deliverable/task status reflects the decision path.
3. Any blocking reasons are visible for follow-up.

If review stays pending:

1. Confirm the assigned reviewer is still active.
2. Confirm review profile and artifacts are loaded.
3. Check timeline entry for attention state.

## How-to: Publish A Reviewed Delivery

Use this only after reviews and task dependencies are clear.

Prerequisites:

1. Required review decisions are accepted (or approved for your policy model).
2. The task is in a promotable state.
3. You have project lead or release-authorized role.

Steps:

1. Open the Deliverables list in the Review & Delivery workspace.
2. Locate an accepted deliverable.
3. For artifact-only delivery, confirm artifact references are stable.
4. For Git deliveries, request review branch publish if needed.
5. Request feature branch update or consolidation when policy requires.
6. Request release package when task/project milestone allows.
7. Confirm project lead/release authorizer action is complete.
8. Confirm promotion state transitions from requested to succeeded.
9. If blocked, inspect block reason and stop.

Expected result:

1. Delivery enters published state or clearly shows blocked reason.
2. Canonical publication appears in timeline and can be referenced in handoff notes.

If publication is blocked:

1. Check missing prerequisites and unresolved dependencies.
2. Correct source branch or artifact state.
3. Retry only after prerequisites are confirmed.

## How-to: Stand Down A Problematic Agent

Use this when an agent is repeatedly unsafe, misrouted, or producing incorrect actions.

Prerequisites:

1. Correct directory and unlocked privileges.
2. Team operator access to the People view.

Steps:

1. Open **People**.
2. Select the problematic agent.
3. Open the stand-down action.
4. Enter a clear reason.
5. Confirm action.
6. Verify the agent row shows stood-down state.
7. Check for subordinate impact if the agent has reports.
8. Watch wake behavior for that branch and affected task paths.
9. After corrective action, open stand-down and revoke it with a reason.
10. Confirm subordinates regain normal wake eligibility.

Expected result:

1. Problematic agent stops receiving Team wake actions immediately.
2. Subordinate or managed impact is explicit and can be addressed separately.

If tasks still appear to proceed:

1. Confirm active dispatch lease completion states.
2. Check Timeline for any running actions started before stand-down.
3. Use section 7 for full evidence.

## How-to: Investigate Why A Team Agent Did Not Wake

Use this when a team member appears silent without obvious reason.

Prerequisites:

1. You can view timeline and case/task context.
2. Correct directory and unlocked privileges.

Steps:

1. Open **Timeline/Audit** and filter by the target agent.
2. Confirm the case/task is open and not blocked.
3. Check inbox state:
   1. if high/normal and response requested, expect prompt wake behavior
   2. if low only, check batching threshold expectations
4. Open the target task and inspect dependency chain and child-task blockers.
5. Open People and confirm the agent is not stood down.
6. Confirm the reporting chain does not include an active stand-down on ancestors.
7. Check for active leases that may already cover this dispatch.
8. Confirm cost guard is not preventing wake for the team.
9. Confirm wake reason exists (task ready, review request, manager fallback, governance queue).
10. Reopen the case and task in Review & Delivery for any pending status request.
11. If no answer remains after checks, verify relevant worker health in section 18 before retrying.

Expected result:

1. You identify one of:
   1. no wake reason yet
   2. blocked by low-priority batching
   3. blocked by stand-down/lease/dependencies/cost guard
2. You can choose either wait condition or explicit recovery action.

If wake delay remains:

1. Keep a short evidence note with the checks you ran.
2. Escalate only after cost/stand-down and dependency checks are ruled out.
3. Open a wake investigation incident path with Timeline evidence and task references.
