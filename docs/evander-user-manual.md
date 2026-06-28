---
title: Evander User Manual
subtitle: Operator Guide
date: 2026-06-26
---

# Evander User Manual

Operator guide for day-to-day use of Evander. This manual focuses on practical how-to procedures, safe operating habits, and cross-page troubleshooting rather than low-level implementation details.

## Table Of Contents

- [1. Welcome To Evander](#1-welcome-to-evander)
- [2. Getting Started](#2-getting-started)
- [3. Dashboard And Daily Monitoring](#3-dashboard-and-daily-monitoring)
- [4. Working With Agents](#4-working-with-agents)
- [5. Sessions, Access, And Policy](#5-sessions-access-and-policy)
- [6. Approvals And Human Decisions](#6-approvals-and-human-decisions)
- [7. Audit Trail And Tool Calls](#7-audit-trail-and-tool-calls)
- [8. Issues And Work Intake](#8-issues-and-work-intake)
- [9. Execution Targets](#9-execution-targets)
- [10. Artifacts And Files](#10-artifacts-and-files)
- [11. Workflows](#11-workflows)
- [12. Reports And Operational Insight](#12-reports-and-operational-insight)
- [13. Team Add-On](#13-team-add-on)
- [14. Accounting And Tax Add-Ons](#14-accounting-and-tax-add-ons)
- [15. Communication And Notifications](#15-communication-and-notifications)
- [16. Configuration](#16-configuration)
- [17. Users, Groups, And Permissions](#17-users-groups-and-permissions)
- [18. Workers And Background Services](#18-workers-and-background-services)
- [19. Assistant](#19-assistant)
- [20. Common End-To-End Tasks](#20-common-end-to-end-tasks)
- [21. Troubleshooting](#21-troubleshooting)
- [22. Reference](#22-reference)

## 1. Welcome To Evander

Evander is the operator console and automation control plane for supervised agent work.
It gives operators one place to watch active work, create and steer agent sessions, review sensitive actions, and recover from failures. Evander helps coordinate what runs in agents while keeping a human in the loop for decisions.

This manual section is for day-to-day operators, team leads, administrators, and support staff. It does not assume familiarity with internal service names or runtime internals.

### 1.1 What Evander Helps You Do

Evander is best used when you need to supervise or recover work that uses one of these core surfaces:

- **Monitor active work:** quickly see the current state of workers, sessions, approvals, and recent activity.
- **Create or resume agent work:** open or restart work sessions without hand-building infrastructure each time.
- **Review approvals and safety gates:** inspect and decide on actions that need human approval.
- **Inspect audit evidence:** trace tool calls, workflow runs, and communication records.
- **Coordinate people, directories, and policies:** keep work separated by team or scope.
- **Respond to common failures:** identify why a run paused, why an approval stalled, and where recovery should start.

Evander is also where you use operational pages for issues, sessions, workflows, users, configuration, communication targets, and worker health.
For first-time onboarding and navigation basics, continue to **Getting Started**.

### 1.2 Important Concepts In Plain Language

Use these terms as the core map for the manual:

- **Directory:** a scope boundary for configuration and ownership. Think of it as a folder for one team, product, or tenant.
- **Agent:** a supervised execution context that performs work on behalf of a task.
- **Session:** the permission envelope for one agent; this is where access and tool limits are stored.
- **VM:** a full disposable virtual machine target for heavier or environment-specific work.
- **Container Worker:** a shorter-lived containerized target used for many parallel or lightweight tasks.
- **Workflow:** a reusable sequence that starts, gates, and finishes work automatically.
- **Approval:** a human checkpoint for actions marked sensitive, risky, or destructive.
- **Tool Call:** a logged request showing what was asked, what happened, and the result.
- **Artifact:** a persisted output such as files, reports, logs, or test results from work.
- **Add-on:** an optional surface that adds capabilities beyond core Evander functions. The core control plane still governs sessions, policy, approvals, and audit behavior.
- **Team:** an optional add-on domain for structured team coordination, project roles, cases, reviews, and release handoffs.

If you need detailed terms for dashboards, policies, and operator pages, see **Common End-To-End Tasks** and **Troubleshooting**.

### 1.3 Operator Responsibilities

Even with automation, operators remain accountable for safety and correctness:

1. **Approve high-risk actions.** Use the approval flow for tool actions that can alter external systems, run privileged operations, or publish artifacts.
2. **Verify outcomes before trust.** Always validate summaries, artifacts, and logs before taking results as final.
3. **Manage access and scope.** Keep user roles, directory assignments, and session lifetimes current.
4. **Review incidents and warnings.** A missing panel, denied action, or stalled run is a signal to investigate and document before proceeding.
5. **Maintain external dependencies.** Credentials, service health, and integration targets should be updated outside the session flow if needed.
6. **Own handoff quality.** Capture context when handing off between shifts or teams.

Evander supports this responsibility model, but it does not replace operator judgment. For account control and directory edits, keep working through **Getting Started**, **Users, Groups, And Permissions**, and **Configuration**.

### 1.4 Safety And Trust Model

Evander is designed around trust-safe supervision:

- **Host-held secrets:** model keys and other secrets remain on the host control plane.
- **Scoped access:** agents and tools operate within explicit session and directory boundaries.
- **Approval gates:** sensitive actions can require human approval before execution.
- **Policy enforcement before action:** allowed tools, policy rules, and communication limits are enforced before work proceeds.
- **Exact action matching:** approved requests are checked against the requested action details.
- **Auditability by default:** approvals, tool calls, and execution decisions are recorded so operators can reconstruct what happened later.
- **Privilege lock for safety:** many mutation actions are intentionally separated from read-only actions.

Evander lowers risk, but operators should still review unusual output, confirm approvals, and track incidents in the dedicated incident sections.

### How-to: Decide Whether Evander Is The Right Surface For A Task

Use this checklist before starting a task:

1. **Define the immediate outcome in one sentence.**
   If the goal is visibility, coordination, or approval-heavy work, continue in Evander.

2. **Check who and what is affected.**
   If the outcome spans a team scope, a directory, or an existing session, use Evander so scope and ownership stay correct.

3. **Pick the surface by intent:**
   1. **Dashboard:** when you need the current attention list and health snapshot.
   2. **Agents:** when creating, prompting, stopping, or steering agent sessions.
   3. **Issues:** when you are starting from an issue record and want repeatable launch context.
   4. **Workflow:** when your goal is repeatable logic and scheduled/linked automation.
   5. **Teams:** only if the Team add-on is enabled and your task is team coordination, review, or deliverable flow.
   6. **External systems:** if the task is pure external communication or system-specific operations that already exist outside Evander workflows, do that in the external system first, then update Evander records if needed.

4. **Check the operation type.**
   If any action can change systems, files, repositories, or approvals, keep it in Evander so it is logged and approved.

5. **Check your prerequisites.**
   Confirm you are in the right directory, have correct privileges, and the relevant surface is unlocked as needed.

6. **Decide where to land the result.**
   If the task ends with an operational follow-up, record it in Evander dashboards, sessions, tool-call evidence, or reports.

7. **If in doubt, start with monitoring and evidence.**
   Open **Dashboard** first, then move into the matching detailed page before taking any mutating action.

## 2. Getting Started

This section gets you into Evander quickly and helps you orient yourself before using sensitive actions.

### 2.1 Open Evander

For most installs, Evander runs on your operator machine at:

`http://127.0.0.1:8100/`

If you were told a different address, use that URL instead.

1. Open the URL in a browser that has your normal workstation access.
2. If a sign-in screen appears, complete it as described in section 2.2.
3. After a successful sign-in, you should land on the Dashboard.
4. Confirm the page shows live status cards and not obvious errors.

What to expect first:

- A left navigation menu for the main operator pages.
- A shared top header that shows the current directory, lock state, and selected user.
- A Dashboard that gives an operator-level snapshot.

If the page does not load, treat it as a potential runtime issue and note the page URL, time, and any browser error before moving on. Then use the health-check guidance in section 21.

### 2.2 Sign In And Sign Out

Evander can be used with one of three sign-in modes, depending on how your host is configured.

#### Local sign-in

- You sign in using a local username and password.
- The browser session is managed by Evander cookies.

#### Microsoft sign-in

- You are redirected to an Entra sign-in flow.
- Complete sign-in with your approved work account.

#### No sign-in mode

- Some trusted local deployments use no sign-in gate.
- If you are unexpectedly sent to a sign-in screen, ask an administrator to confirm the deployment settings.

How to handle common sign-in outcomes:

1. If login is accepted, proceed to section 2.3 and check the Dashboard state.
2. If login is rejected:
   - recheck your username, password, and account status;
   - retry once;
   - if it still fails, contact an administrator and avoid guessing credentials.
3. If your session expires while you are working:
   - re-authenticate at the same screen;
   - reopen the page you were using after login.
4. To sign out, use the sign-out action in the header.

### 2.3 Understand The Main Screen

Once signed in, the shared shell is the same across pages so you can move between sections quickly.

1. Header: shows active section, directory, current user, theme, privilege lock, directory chooser, and sign-out.
2. Navigation: opens all major operator pages such as Dashboard, Agents, Sessions, Approvals, Tool Calls, Workflow, Configuration, Users, Reports, and Workers.
3. Directory scope: shown in the header and applied to many lists and actions.
4. Privilege lock icon/state: `locked` means read-only mode. Mutations are blocked until you unlock.
5. Deployment banner: if present, gives platform-level health or update context.
6. Assistant launcher: optional lower-corner tool when enabled by your deployment.
7. Page content: the page you selected in navigation.

Tip for first sessions: stay on Dashboard first (section 3) and use it to verify that data is appearing as expected before editing anything.

### 2.4 Choose The Right Directory

Directory selection controls the scope for team, tenant, environment, product, or project-facing operations.

1. Click **Choose directory** in the header.
2. Browse the directory tree and choose the scope you are supposed to operate in.
3. Confirm the selection appears in the header.
4. Perform your task in that directory.
5. When you switch, the following pages usually change what they show:
   - Agents
   - Sessions
   - Issues
   - Approvals
   - Workflow
   - Tool Calls
   - Configuration
   - Users

Why this matters:

- Teams and environments can look similar but hold different active work.
- You can accidentally read or approve the wrong work if the directory is wrong.
- The same action name can affect different records in another directory.

If you are uncertain, pause and recheck in section 2.4 before creating agents, changing policies, or approving requests.

### 2.5 Unlock Privileged Operations

Evander uses a deliberate safety gate before changes.

When the lock is closed, the interface is in read-only mode.

- Safe for review, triage, filtering, and inspection.
- Not safe to run changes through.

When unlocked, privileged actions become available, including:

- creating, starting, stopping, and deleting agents and VMs;
- approving or denying approval requests;
- saving configuration;
- editing sessions and workflow items;
- changing directory-scoped policy-like state.

Use the lock deliberately:

1. Unlock only when you are ready to make a real change.
2. Complete and save your changes quickly.
3. Lock again immediately after completion.
4. Leave long-running windows unlocked only when absolutely needed.

### How-to: Open Evander For The First Time

1. Open the Evander URL for your deployment.
2. Sign in using the method your environment requires (local password or Microsoft).
3. Confirm your user name appears in the header and note the active directory.
4. Check the header for `locked` and the Dashboard for a live read.
5. Read the Dashboard summary once, then open a second page you will work on next.

Expected result:

- You are signed in and have a baseline understanding of current system status.

If the screen stays blank or shows nothing useful:

1. Capture the URL and current directory shown in the header.
2. Try a browser refresh.
3. Check for sign-in state issues.
4. Use the sign-out/in cycle if needed before moving to troubleshooting.

### How-to: Switch To A Team Or Project Directory

1. Click **Choose directory** in the top header.
2. Open the team/product branch of the directory tree.
3. Select the target directory.
4. Confirm the new directory name appears in the header.
5. Open one page tied to that scope, such as Sessions or Agents, and verify records match the target team/project.

Expected result:

- Lists, approvals, and policies reflect the selected scope.

Common recovery:

1. If the wrong records are still visible, return to directory chooser and reselect.
2. If selection does not persist, refresh once and reselect.
3. If you still cannot change directory, stop and escalate using section 21.

### How-to: Safely Unlock And Relock Changes

1. Verify you are in the correct directory.
2. Click **Unlock** in the header.
3. Perform one focused set of changes only.
4. Save when the save action becomes available.
5. Confirm the result on the page or by opening the related linked page.
6. Click **Lock** as soon as changes are complete.

When to unlock:

- creating or stopping agents;
- approving, denying, or revising approvals;
- editing workflow or configuration items;
- deleting records.

When not to unlock:

- during initial browsing of unrelated pages;
- when validating only;
- when you are uncertain about directory scope.

Result:

- The system returns to read-only mode and reduces accidental edits.

After you finish, continue to:
- Section 3 for day-to-day monitoring habits,
- Section 3.2 for refresh and staleness handling,
- Section 6 for review workflows if you unlocked for approvals,
- Section 16 for safe configuration changes.

## 3. Dashboard And Daily Monitoring

The Dashboard is your first check-in point at the start of each shift and whenever something feels wrong. It is a consolidated view meant to answer one question: *What needs attention now?*

Use the same directory and header settings from section 2 for all Dashboard checks. The dashboard is scoped to the directory you have selected, so it is normal to see different traffic in different scopes.

### 3.1 Dashboard Panels

Open the Dashboard from the left navigation or default landing view after sign-in.

1. Health and deployment overview
   - Use this panel to confirm the control plane itself is healthy.
   - Good signals are expected run state, expected worker reachability, and no persistent "degraded" alerts.
   - If this panel is missing or reports unknown state, jump to the Workers section next.

2. Worker state
   - This panel helps you see if background workers are up, sleeping, failing, or blocked.
   - A healthy worker set matters before expecting workflow activity, approvals, wake jobs, and Git/review automation to execute.
   - If a worker looks unhealthy, continue to section 18.2 before changing workflows.

3. Agents and sessions
   - Use this area to see whether active work is balanced, stalled, or empty for your directory.
   - High active counts plus no fresh session transitions can indicate queue pressure.
   - If you need the exact list, open Agents (section 4) or Sessions (section 5).

4. Workflow activity
   - This is the quickest signal for live automation.
   - Confirm there are recent successful runs and no unexplained red failure clusters.
   - If a run fails repeatedly, use section 20 for the end-to-end failure investigation flow and start with Workflow in section 11.

5. Approval queue
   - This shows whether people or systems are waiting for human review.
   - Pending approvals should be expected during active sensitive work, but a growing queue is an early sign of process backlog.
   - Review these from section 6 when they exceed your normal daily range.

6. Sessions and audit summary
   - Use this for a high-level sense of who is running what, and whether tool-call evidence is being produced.
   - If you need evidence links, move to Tool Calls (section 7) and follow record relations.

7. Reports, version, and news
   - The Reports shortcuts help you decide if a spike is isolated or systemic.
   - The version/changelog card tells you what build is currently visible to operators.
   - The news area usually carries release notes and notices for product updates.

8. Capacity and queue context
   - Capacity signals indicate whether demand is growing faster than control-plane throughput.
   - Persistent saturation here is a precursor to longer response times across sessions and approvals.

If a panel appears empty, missing, or clearly stale, do not assume a UI bug first. Confirm directory scope and then verify with the linked detail page before you make any changes.

### 3.2 Refresh And Staleness

Dashboard tiles are meant for speed. In practice, this means some values can remain valid for a short period but not always live at millisecond precision.

Use these rules:

1. First pass in normal monitoring: read at a glance and note any warning badges.
2. Before any decision that affects real action (e.g., starting extra intervention), click **Refresh**.
3. If something still looks wrong after Refresh, open the detail page linked from the panel.
4. Treat the detail page as the current source for action and deeper investigation.

When to refresh:

- At shift start or after a system update.
- Right after switching directory scope.
- Before paging any colleague about a failed run, blocked worker, or approval backlog.
- When a panel looks inconsistent with a nearby page.

What to expect from refresh:

- Fresh card data for all visible panels.
- Bypassing stale cached summaries for dashboard readbacks.
- No immediate remediation by itself; refresh is for accurate visibility, not for fixing state.

If refresh does not change a suspicious value:

- Open the related panel's detail page.
- Verify that your directory is correct.
- Confirm you have the privileges needed to view restricted records.
- If visibility remains confusing, move to section 21 with the exact page, time, and IDs you observed.

### 3.3 Attention Signals

The following conditions usually require immediate follow-up, even when the interface still looks otherwise normal.

1. Missing data or unavailable panels
   - Meaning: data path could not be read, API path was denied, or scoped data is empty by policy.
   - First action: verify directory and lock state, then open the corresponding detail page.

2. Unhealthy or stopped workers
   - Meaning: background automation may stop moving.
   - First action: check Workers (section 18) and then the related logs.

3. Failed workflow runs
   - Meaning: one or more triggers/effects did not complete.
   - First action: open Workflow and then run the end-to-end incident flow in section 20.

4. Pending approvals not moving
   - Meaning: people or configured thresholds are delaying execution.
   - First action: open Approvals (section 6) and verify queue filters, ages, and required actions.

5. High queue age / slow processing
   - Meaning: requests are waiting too long, usually from staffing delay, backend pressure, or dependency issues.
   - First action: compare queue age in Dashboard against worker health and session activity before escalating.

6. Prompt pressure
   - Meaning: many items waiting for the same decision point (agents, approvals, or workflow triggers) while no recent completions occur.
   - First action: review approval and workflow context and confirm there are no blocking external dependencies.

7. Repeated warning banners with no clear source
   - Meaning: a systemic condition is changing between refreshes.
   - First action: use the linked detail pages and open a focused report in Reports (section 12).

For every signal, avoid changing live workflow definitions until you confirm the actual impacted records from detail pages. The Dashboard is for triage, not execution.

### 3.4 Release And Product Information

The Dashboard is also the safest place to confirm what release level and operational notices apply to your current Evander operator session.

You should check:

- Version: current running build identity and whether it matches your deployment expectations.
- Changelog: detailed changes in current and prior updates.
- News: operational announcements tied to product use and updates.
- Deployment banner and build notices: active or pending update state visible near header or dashboard context.

Use this section during shift handover so outgoing operators and incoming operators align on the same release baseline.

Keep in mind:

- If the changelog points to behavior you did not expect, avoid assumptions and confirm the effective config in Configuration (section 16) before action.
- New operator behavior and new surfaces are most often due to release-level changes plus config scope differences.

### How-to: Run A Morning Operations Check

When to use: start-of-shift routine, after returning from an incident, and after scheduled maintenance.

Prerequisites:

- You are signed in and on the correct directory (section 2).
- You know whether you are in normal monitoring mode or an incident mode.

Steps:

1. Open Dashboard and immediately note the active directory in the header.
2. Click **Refresh** once.
3. Read the deployment/health card for global service condition.
4. Open the Workers card and verify major background services are up.
5. Open the Sessions and Agents summary and check that recent work exists where expected.
6. Open Workflow activity and confirm recent successful runs exist.
7. Open Approvals and confirm pending count is manageable.
8. Open Reports or queue snapshots if the day historically has recurring spikes.
9. Open Version and News, then check the changelog date range before your first action.
10. Record your findings in a short handover note: "all green", "watch items", or "investigate".

Expected result:

- You have a single-screen snapshot and a clean decision on whether the system is ready for routine work.

Common recovery:

- If any core signal is yellow or red, do not skip to changes.
- Open the relevant detail page and validate by ID/timestamps before taking the first corrective action.

### How-to: Follow A Dashboard Warning To The Detail Page

When to use: any time a Dashboard card shows a warning, unavailable marker, or unusual spike.

Prerequisites:

- Use the same directory as shown on the header.
- Ensure you know whether your user can view and act in the target section.

Steps:

1. Note the exact warning text and the panel that shows it.
2. Use the panel's quick link (for example Open workflow, Open approvals, Open sessions, Open tool calls, Open workers, Open reports, Open config).
3. In the detail page, apply filters to the same scope and time range implied by the warning.
4. Confirm at least one concrete record that matches what you saw in the Dashboard card.
5. Follow any relation links to the next page, if available.
6. Return to Dashboard and recheck the warning after actions.

Expected result:

- You move from a summary warning to one specific record or event chain.

Common recovery paths:

- If the detail page is empty, verify directory scope and permissions first.
- If it is still empty and you expect data, collect timestamp, directory, and warning details and use section 21 for evidence capture.

### How-to: Download The Changelog

When to use: before shift handover, after a deployment notice, or before changing config/workflow behavior.

Prerequisites:

- Access to the Dashboard version/changelog panel.

Steps:

1. Open Dashboard and find the version/changelog panel.
2. Locate the release or changelog link.
3. Click **Download** (or the page equivalent control).
4. Save the file with a clear name including date and directory.
5. Read the release notes entries relevant to your directory scope.
6. Keep the changelog next to your handover notes.

Expected result:

- You have the full release note text available locally for shift context and incident handoff.

If the changelog cannot be downloaded:

1. Use the dashboard news entry as a temporary reference.
2. Check if the deployment banner indicates an active update state.
3. Confirm visibility in Configuration and version settings (section 16), then retry once service health is stable.

## 4. Working With Agents

The Agents page is where operators create, prompt, steer, and close day-to-day agent work.
Use this section when you need to act on a specific agent session; for safety limits and approvals, continue to **Sessions, Access, And Policy** (section 5). For what requires human sign-off, see **Approvals And Human Decisions** (section 6). For backend-specific target differences, use **Execution Targets** (section 9).

This section uses plain workflow language. You can perform these actions from the main navigation in Admin Web.

### 4.1 Agent List And Details

In **Agents**, each row usually represents an active or recently completed execution target linked to a session.

You use this view to:

1. Confirm the selected directory and active lock state.
2. Locate the right agent by session, name, target type, or labels.
3. Read live status before acting.
4. Open an agent to inspect:
   - attached session information
   - current VM or container-worker context
   - labels and metadata
   - policy summary
   - recent logs
   - any steering/discovery notes
5. Follow links to related records (session, tool calls, approvals, or related runs).

What to expect:

- The list refresh is tuned for quick browsing and may not reflect every sub-second execution change.
- Use **Refresh** before taking any decision.
- If the agent list does not match your working directory, pause and reselect the directory.

Notes:

- The selected agent context can help decide if you should send a prompt, steer, pause, or retire.
- For target-specific behavior (VM versus container worker), defer to **Execution Targets** (section 9) before changing target-level assumptions.

### 4.2 Create An Agent

Use this flow to start supervised work for a task, issue, or investigation.

When to create:

- You need a controlled environment to run repository changes or automation.
- You need policy-based constraints while the agent works.
- You need an agent tied to a specific directory scope and session.

When not to create:

- If a session already exists for the same task, reuse and resume it instead of creating a duplicate.
- If the work is purely informational and not allowed in the selected scope, use the correct directory/scope first.

### 4.3 Prompt Or Resume An Agent

Once an agent is created, use this flow for new instructions and follow-up work.

Use prompts to:

- ask the next step directly
- recover from partial or ambiguous outputs
- continue from context when handoffs are needed
- resume with explicit thread continuity when needed

You can usually keep one continuous thread for simple work.

If the agent drifts across tasks, you can choose a fresh context in your prompt rather than forcing continuation.

### 4.4 Operator Steering

Steering is your way to correct an active run before it goes too far.

For container worker sessions, steering interrupts the current execution and immediately launches a corrected replacement dispatch behind the same operation context so work can continue without restarting the whole target.

For non-container sessions, steering is queued as a host note and consumed on the next eligible model call for that running flow.

Use this distinction to set your expectations:

- Container worker steering is immediate and interruption-based.
- Relay-style steering is gentler but may wait for a model request to take effect.

### 4.5 Lifecycle Actions

Lifecycle actions include routine and cleanup controls used during normal supervision:

- start
- stop or shutdown
- revoke session access
- destroy
- verify cleanup state

Operators should always treat lifecycle changes as state transitions with recovery steps:

- verify the right agent and directory
- confirm no active approvals are required to proceed
- confirm related outputs and logs are captured before cleanup

### 4.6 Skills For Agents

Skills are reusable agent instruction packages that can be selected at creation or attached later to an existing session.

Use managed skills for:

- repeating the same behavior across many agents
- codified safety patterns (coding style, response format, escalation rules)
- reducing the need for large one-off prompts

If a skill is too broad for one task, prefer a short follow-up prompt plus scoped allowed tools instead of global skill changes.

### How-to: Create A Basic Agent Session

Use this when you need a fresh agent to begin new work.

Prerequisites:

- You are in the correct directory.
- You have unlocked privilege mode if your environment requires it.
- You know which target class you expect (for example standard Codex task execution).

Steps:

1. Open **Agents** from the left navigation.
2. Click **Create**.
3. Confirm the directory is correct.
4. Enter or accept the proposed **session ID**.
5. Choose the model and model relay/mode.
6. Select the execution target shape (use the same choice as your directory and policy expects).
7. Set policy controls needed for this task:
   - allowed tools
   - communication allowlists
   - bootstrap options
8. Add one or more managed skills only if they are required for the task.
9. Click **Create** and wait for the page to show the created agent.
10. Open the new agent row and verify:
   - status is correct
   - session link is visible
   - policy restrictions match your intent

Expected result:

- A new agent row appears with attached session context and a safe starting status.

If it does not appear:

1. Click **Refresh**.
2. Verify directory lock/scope and required privileges.
3. Check if creation failed due to missing session or policy requirements; then review section 5 before retrying.

### How-to: Send A Follow-Up Prompt To An Existing Agent

Use this when the first response is complete but more work is still needed.

Prerequisites:

- You can open the target agent.
- The agent is associated with the expected session.

Steps:

1. Open **Agents** and select the active agent row.
2. Use the **Prompt** action.
3. Write a concise instruction and include exactly what has changed.
4. Keep one objective per prompt.
5. Decide whether to continue in the current thread or start fresh context:
   - keep continuity by default
   - start fresh if you need a clean restart on a different plan
6. (Optional) provide the resume thread explicitly if the UI does not prefill as expected.
7. Send the prompt and wait for a response cycle to start.
8. Watch logs and follow-up entries from the same agent record.

Expected result:

- A prompt dispatch appears, and the next output or action reflects the instruction direction.

If no action appears:

1. Confirm the session allows prompt dispatch in section 5.
2. Check the active lock and required privileges.
3. For no-response cases, inspect related approval/state paths (section 6 and section 7).

### How-to: Install A Managed Skill Into A Session

Use this when you need to add reusable instructions to a session already in progress.

Steps:

1. Open **Agents** and select the target agent.
2. Open the skill install action for the selected session.
3. Choose a skill from the managed library.
4. Confirm no duplicate instruction conflicts with the existing session plan.
5. Install the skill.
6. Open the agent details and verify the skill is now listed.

Expected result:

- The selected session gains the skill behavior for future prompts and work cycles.

If install fails:

1. Confirm the session is unlocked for mutation.
2. Ensure the skill exists in **Skills** and is visible in the current directory context.
3. Retry after refresh; if unavailable, ask an administrator to check managed skill publishing.

### How-to: Steer An Agent That Is Going Off Track

Use this when the current output is clearly wrong direction, unsafe, or out of scope.

Steps:

1. Open the agent and review recent logs to confirm the wrong path.
2. Open the **Operator steering** control.
3. Send one short corrective message with desired outcome, constraints, and boundaries.
4. If the run is a container-worker execution, continue to monitor for a replacement dispatch.
5. If non-container execution is in use, monitor for effect on the next model request and keep the correction visible in context.
6. If the correction is not honored:
   - send one stronger, specific follow-up steering note
   - consider stopping and restarting with a clearer follow-up prompt

Expected result:

- The very next relevant action aligns with the steering intent, or the session is clearly blocked for safe manual intervention.

Common recovery:

- If steering does not change behavior, open related tool call and approval context, then decide whether to pause/stop and route a safe restart sequence.
- For repeated off-track behavior, treat this as possible session design issue and review in section 5 before continuing.

### How-to: Retire Or Destroy An Agent Safely

Use this when work is complete, or when the session can no longer be trusted to continue safely.

Steps:

1. Select the target agent and verify directory, session ID, and target identity.
2. Review recent logs or tool call references so you do not lose critical evidence.
3. Confirm outstanding approvals are handled or irrelevant.
4. Stop or shut down active execution first if it is still running.
5. Revoke session access if no future prompts are needed.
6. Destroy when no recovery or rerun context is required.
7. Confirm destroy result and cleanup state through the agent and sessions views.
8. Download or note the relevant evidence before cleanup if required by your team process.

Expected result:

- The agent is no longer active and is marked as retired/removed per the platform behavior.

If the cleanup action fails:

1. Do not force repeated destructive actions.
2. Re-check directory and lock state.
3. Review the failure path and retry once after a short interval.
4. Record unresolved cleanup in handover notes and escalate through incident notes if needed.

If you need recurring cleanup habits for production directories, use **Lifecycle Actions** as your routine pattern before deleting any historical context needed for audit.

## 5. Sessions, Access, And Policy

Sessions are the operator control surface for what an agent is allowed to do next.
They are useful to think of as a short contract: when the session exists, the agent
can act; when the contract changes, the agent cannot.

This section helps you find sessions, tune what is allowed, apply approvals safely,
control file communication, and schedule wake behavior.

### 5.1 Find A Session

Open **Sessions** when you need to answer:

1. Which active work is tied to which session.
2. Which policy controls are currently active.
3. Whether a session is still usable or already expired.

Use these steps:

1. Open **Sessions** from the left navigation.
2. Verify the selected directory is correct.
3. Use the search box for session ID, VM name, issue label, or model.
4. Toggle **Active** and **Expired** lists.
5. Select a row to inspect its detail card.

What to read in the detail card:

- **Session ID**: the key used by Agents, Tool Calls, and approvals.
- **VM / Issue link**: where the work started and why this session exists.
- **Directory**: scope boundary that controls inherited policy.
- **Model**: current model used for resumption prompts and behavior.
- **Created time**: for handoff and audit context.
- **Expiry time**: when guest access stops unless renewed.
- **Allowed tools**: current allowed actions.
- **Wake schedule list**: any pending recurring or one-time resumes.

If a row is missing, check these first:

1. Directory context.
2. Lock state is irrelevant for reading but required for editing.
3. Manual filters or stale search text.
4. Active list vs Expired list.

If you still do not find the session, capture timestamp, directory, and search terms and use
the troubleshooting workflow in section 21.

### 5.2 Session Expiry And Revocation

Session timing and revocation decide whether an agent can continue receiving prompts.

- **Active session**: session is still accepted by dispatch and wake flows.
- **Expired session**: time window has ended and normal dispatch is blocked.
- **Revoked session**: manually denied future guest access regardless of expiry.
- **Deleted session**: local record is removed from session history.

You normally use:

1. Extend when work is not finished but will run longer than expected.
2. Revoke when work is complete or access must stop immediately.
3. Delete only when cleanup policy requires removing old records.

Keep in mind:

- Revocation is usually the safer cleanup step while preserving evidence.
- Revoking does not erase past records, but it blocks future execution.
- Expired sessions can be extended only through session edit actions and then become usable again.

### 5.3 Allowed Tools

`allowed_tools` is the boundary for what each session can request.

Use it to:

- Stop dangerous tool classes from being called.
- Let one session remain narrow while another can do file, communication, or workflow actions.
- Prevent accidental use of high-cost integrations in the wrong session.

Rules to use:

1. A tool must be in session `allowed_tools` for any call to be accepted.
2. Directory-level policy can deny a tool globally; session-level changes cannot re-enable blocked inherited denies.
3. Some high-impact tools still require human approval even when allowed.
4. Removing a tool from `allowed_tools` removes access immediately for future calls.

What changes in `allowed_tools` should be paired with:

- A quick check of approvals in the same session context.
- A check of related tool-call outcomes in section 7 if a call was denied.

### 5.4 Approval Requirements

Approval settings let you keep a session usable while adding a human checkpoint.

When you enable approval for a tool, that tool can still be in `allowed_tools` but cannot run
without queueing an approval request first.

Three practical settings matter for operators:

1. **Approval TTL**: how long an approval decision remains valid.
2. **Use count**: how many tool calls can reuse one approved ticket.
3. **Payload matching**: whether retries must match the original request details closely.

Common guidance:

- Keep strict matching for external writes, reviews, and releases.
- Use broader matching only when your operator workflow has clear and repetitive idempotent payloads.
- If approval was approved but action is still blocked, check section 6 for status reasons and
  section 7 for execution/audit detail.

### 5.5 Allowed Scripts And Communication

This area controls what in-session helper scripts and communication paths are trusted for a session.

- **Allowed scripts** control which named scripts can be requested by the session.
- **Communication allowlist** controls which configured targets are visible for send/list/download actions.
- **Communication file settings** control inbound and outbound transfer safety:
  - receive enabled / send enabled
  - per-send or per-file approval
  - max bytes
  - allowed MIME types
  - allowed extensions

For file safety:

1. Always set limits before enabling send.
2. Keep executable-style extensions blocked even if extension allowlists grow.
3. Use explicit MIME and extension checks together, not just one.
4. Keep approvals enabled for send actions when the work scope is external.

If you need provider setup, target ownership, outbound behavior, or inbound attachment flows in more depth,
continue in section 15.

### 5.6 Scheduled Wakes

Scheduled wakes let a session resume itself or get prompted on a timer.

Evander supports:

- **One-time wake** with a specific resume time.
- **Recurring wake** from cron-style rhythm.

Each wake entry records dispatch state in a durable ledger that operators can review:

- pending / dispatched
- failed / due / missed or late
- completed or cancelled

Guidance:

1. If a wake is not dispatched, confirm scheduled wake workers are running.
2. If it is repeatedly due or failed, use the retry flow before deleting any schedule.
3. If a session expires while wake entries remain pending, extend the session if additional work is still expected.

Recurring schedule entries stay visible after misses unless removed; this helps you see gaps and operator
interventions over time.

### How-to: Require Human Approval For A Tool

Use this when a tool is currently needed by the session but should never run without
human confirmation.

Prerequisites:

- Correct directory selected.
- Privileges unlocked.
- The target session is active and selected.

Steps:

1. Open **Sessions** and open the target session.
2. In **Model and access**, confirm the tool is allowed.
3. Turn on **Require approval** for that same tool.
4. Open the settings gear next to approval and set:
   1. the approval TTL,
   2. use count,
   3. payload matching strictness.
5. Save session settings.
6. Trigger one expected call in normal agent flow.
7. Open **Approvals** and confirm a pending request appears with the correct session/tool context.

Expected result:

- The tool does not execute until a human approves the request.

Common recovery:

1. If no approval appears, check that the tool remains in **allowed_tools**.
2. If still absent, confirm no hard policy lock blocks it in higher scope.
3. If approvals keep auto-failing, inspect request history using section 7 and continue in section 6.

### How-to: Extend A Session Before It Expires

Use this when work is ongoing and the expiry time is too soon.

Prerequisites:

- Correct directory selected.
- Privileges unlocked.
- The session is found in the active list.

Steps:

1. Open **Sessions**.
2. Search for the session and open it.
3. Read current expiry and confirm active state.
4. Click **Extend**.
5. Enter a new expiry and save.
6. Reload details or return to the list.
7. Verify the new expiry time appears in the session row.

Expected result:

- Session remains active and can resume work until the new expiry.

Common recovery:

1. If the session still appears expired, confirm the extension save completed successfully.
2. If extension is rejected, check lock/state or stale directory assumptions.
3. If active work still cannot resume, inspect worker availability and section 4 for agent lifecycle checks.

### How-to: Revoke A Session After Work Is Finished

Use this when work is complete, handover is done, or no further model access is needed.

Prerequisites:

- Correct directory selected.
- Privileges unlocked.
- Optional: a final check of artifacts or tool-call entries for completeness.

Steps:

1. Open **Sessions** and find the completed session.
2. Review any final status indicators or links to expected artifacts.
3. Click **Revoke**.
4. Confirm the action.
5. Re-open the session in the list and confirm it is no longer usable for new access.
6. If retention policy requires, delete only after handoff documentation is complete.

Expected result:

- The session is blocked from further guest access, but historical records remain.

Common recovery:

1. If the session still accepts resumes, refresh and confirm the status changed from active to revoked.
2. If an agent still runs after revoke, stop the runtime action first and then verify directory policy.
3. For questions about why a denied tool still appears in old evidence, use section 7.

### How-to: Schedule A Recurring Agent Wake

Use this when you want repeated checkpoints, periodic status prompts, or regular
maintenance checks without manually prompting each time.

Prerequisites:

- Correct directory selected.
- Privileges unlocked.
- The scheduled wake worker is enabled by your host setup.

Steps:

1. Open **Sessions** and select a stable session.
2. Go to wake schedule controls.
3. Choose **Recurring** and set:
   1. cron expression,
   2. prompt text,
   3. desired resume mode.
4. Enable the schedule and save.
5. Review the new schedule entry in the wake ledger on that session row.
6. Observe the first two wake runs for timing and result.
7. If a wake is due, late, or failed, use the retry action in the same session area.

Expected result:

- The session receives repeat prompts according to the schedule, and wake history is visible.

Common recovery:

1. If no wakes run, confirm scheduled wake worker is running and resume mode matches expectations.
2. If retries continue to fail, disable and re-enable with a corrected prompt.
3. If wake history shows "missed or late" repeatedly, reduce schedule intensity and confirm directory scope.

### How-to: Allow A Session To Receive Or Send Files

Use this when a session needs in-session file transfer or outbound artifact sharing.

Prerequisites:

- Correct directory and unlocked privileges.
- Communication target and file policy reviewed in configuration context if needed.
- A clear understanding of who should receive or send files.

Steps:

1. Open **Sessions** and pick the target session.
2. Under communication controls, open allowed targets and target list.
3. Enable receive and/or send as needed.
4. Set max file bytes and confirm limits are realistic for real outputs.
5. Set allowed MIME types and extensions.
6. Keep blocked extensions in place for executable and unknown script types.
7. If send is enabled, set approval expectations so outbound actions are never silent.
8. Save the policy.
9. For outbound flow, run a controlled approval test path and verify:
   1. request appears in **Approvals**,
   2. final send is in Tool Calls with matching result context.

Expected result:

- File exchange works only for approved targets and matching file-type rules.

Common recovery:

1. If inbound or outbound is blocked, confirm the size/MIME/extension policy in the session itself.
2. If approvals are missed, check section 6 and section 7 before changing limits.
3. If this is a temporary test, revert send settings back to standard limits afterward.

## 6. Approvals And Human Decisions

Approvals let you keep automation fast while preserving human control. They are the pause point for
actions that need a person to confirm intent before a tool runs.

Use this section when you want to handle queued requests, confirm a safe decision, or investigate a
failed approval flow. For policy configuration, keep section 5 nearby; for execution traces, follow
to section 7.

### 6.1 When Approval Is Required

Approval is usually triggered by one of these layers:

1. Directory policy requires it. This is often used for sensitive or broad operational areas.
2. Session policy requires it for specific tools or communication flows.
3. The tool path is safety-gated and still needs human confirmation even if it is generally
   allowed.

In practice, an action can be blocked by policy before it reaches approval:

- The action is not in the session's allowed tools.
- Directory or session rules deny the tool family.

If the action is allowed but still needs review, Evander creates a human decision record in
Approvals. The safest way to predict this is to set expectations in section 5 before running the
task.

What to expect:

- High-risk and external-facing actions are often approval-gated first.
- Approval does not remove the underlying policy checks. It only adds a signed human checkpoint.
- A denied action stays in the system as evidence and should be reviewable with related records.

When you see frequent queue growth, review session rules and policy defaults in section 5 before
adding temporary operators to the queue.

### 6.2 Approval Queue

Open **Approvals** from the left navigation to work from a single decision list.

Use filters to focus the queue:

1. `status` to separate Pending, Approved, Denied, and failed/expired records.
2. `session` and `VM` to narrow by active work item.
3. `tool` to find one risky tool family.
4. `age` to surface items waiting the longest.
5. directory scope to avoid deciding on another tenant or project by mistake.

Queue health is a practical operating signal:

- Low and steady pending count is normal during heavy tool use.
- A rising pending count with little progress usually means one of:
  1. missing staffing,
  2. too-short lock windows,
  3. too-strict payload settings,
  4. blocked downstream delivery of notifications (section 15).

When you open a row, review the item in this order:

1. summary text and target action,
2. intended effect,
3. status and age,
4. related session/workflow context.

If you cannot identify the owner or purpose of a request quickly, stop and return only once you
have directory, session, and actor context.

### 6.3 Reviewing A Request

The request row is the operator decision surface; keep review structured:

1. Confirm request scope by reading session, directory, tool, and related entity fields.
2. Confirm what the action is expected to do from the request summary and intended effect.
3. Check the `digest`, which identifies the expected payload shape for this approval.
4. Review redacted payload for context and obvious safety flags.
5. Open the approval payload diagnostic when there is mismatch risk.
6. Read the decision history so you can see who approved, denied, or reopened context.
7. Use the matching audit context to confirm this request is tied to the call path you expected.

Why redacted data is normal:

- Redaction protects secrets and internal system fields in operator surfaces.
- Redacted records can still be enough for safe decisions.
- Missing payload details do not automatically imply missing approval, but they do require careful
  reliance on summary, context links, and scope.

If you need original data for a secure internal investigation, use the cross-record workflow in
section 7 and the local incident escalation path in section 21.

### 6.4 Approve Or Deny

Approving or denying is the final step in a gated action. Use this order:

1. Confirm your directory and lock state are correct.
2. Confirm you can read the request context completely enough for a decision.
3. Unlock privileged operations if the action buttons are hidden.
4. Enter approver identity (operator name or team handle).
5. Enter a short reason before submission.
6. Choose **Approve** or **Deny**.
7. Re-check status changes in the same record and in related Tool Calls.

Decision quality habits:

- Keep reasons specific enough for audits, for example:
  "Reviewed target path and approved send to known workspace."
- One-line reasons are better than blank or generic text.
- If you deny, include what change would be required to retry safely.

Remember:

- Approve or deny only affects the request outcome.
- It does not grant unlimited retries; follow-up logic is enforced by policy settings.
- A denied request remains reviewable; do not delete it as a cleanup shortcut.

### 6.5 Approval Diagnostics

Use this section when the outcome is not expected.

#### Stale or Expired

An approval can become too old to use. Typical operator checks:

1. Check status and expiry timestamps.
2. Compare current queue age and session timing.
3. Re-request or trigger a fresh retry path as designed by the calling flow.
4. Re-open if needed with corrected context.

#### Denied

A denied request can reflect risk policy, reviewer judgment, or changed conditions.

1. Check decision reason.
2. Confirm required manual change is understood (payload, tool, scope, or recipient).
3. Run the safe retry after the controlling operator action is complete.

#### Missing

Missing means no active matching approval was available when the action retried.

1. Confirm the original request was tied to the right session and action.
2. Verify you did not review a different but similarly named request.
3. Ask whether the retry path includes the exact request ID and same context.

#### Payload-Mismatched

This is a frequent source of confusion after approval. It means the later attempt differs from the
approved content.

1. Compare the approved digest against the latest attempted intent.
2. Confirm whether the change was intentional; if not, deny and re-request.
3. If intentional, approve a fresh request for the new payload before retrying.

For details on request-to-call linkage and diagnostics fields, use section 7 and the end-to-end flow
in section 20.

### How-to: Review And Decide A Pending Approval

Use this when an operator is waiting on a pending request and the work cannot progress.

Prerequisites:

- Correct directory selected.
- Privileges unlocked for mutation.
- You understand the underlying task context.

Steps:

1. Open **Approvals**.
2. Filter to `Pending`.
3. Select the request by session, VM, or tool.
4. Read request summary, intended effect, and directory context first.
5. Review the digest and redacted payload area.
6. Open the linked audit context (if present) to confirm the request is the one you expect.
7. Enter approver identity and reason.
8. Click **Approve** or **Deny**.
9. Re-open the request detail and confirm status changed.
10. Check the linked Tool Call record for execution result or retry status.

Expected result:

- The request state moves to approved or denied, and the calling workflow/agent action either
  resumes, retries correctly, or stops with an expected status.

If you cannot decide safely:

1. Pause and return when the session context is fully known.
2. Ask a second operator using the handover process if a policy boundary may be disputed.

### How-to: Investigate Why An Approved Action Still Failed

Use this when the request is approved but the action did not succeed.

Prerequisites:

- You have the approval ID and session context.
- Privileges unlocked for review actions.
- Related tool-call or workflow entries are visible.

Steps:

1. Open the approved request and note `approved`, `expired`, and `use` metadata.
2. Open the related Tool Call link from the request.
3. Compare timestamps and tool names:
   1. Was the retry tied to the same request?
   2. Did it use the same session?
   3. Did the digest and matching mode remain valid?
4. Check the approval payload diagnostic for stale/mismatch clues.
5. Check status path:
   - `denied` after review,
   - `expired` before retry,
   - missing approval record at retry time,
   - or payload mismatch on retry.
6. If the payload changed, deny further retries, close the old request, and request a fresh approval
   with corrected values.
7. If expiry is the issue, decide whether a fresh request under updated policy is needed.
8. Re-run the action with the expected request path and monitor Tool Calls again.

Expected result:

- You have a specific reason (stale, missing, mismatch, expiry, denial) and a safe repeat path.

Common recovery:

- Keep one retry policy in place and avoid repeatedly retrying by hand after mismatch until root cause is
  corrected.

### How-to: Set Up Approval Notifications

Use this when approvers should be notified as soon as new requests enter the queue.

Prerequisites:

- Access to Configuration controls.
- At least one notify-capable communication target defined.
- Privileges unlocked for config changes.

Steps:

1. Open **Configuration** and go to the directory-specific policy area.
2. Open the access policy controls for approval notifications.
3. Choose notify-capable communication targets from the known list.
4. Add one or more target IDs to the approval notification target list.
5. Save the policy.
6. Trigger a low-risk approval path (for example a non-production test session) and confirm a request appears.
7. Verify notification delivery in the communication target output or target provider channel.
8. Record the expected route and test time in handover notes.

Expected result:

- New approval requests notify the selected recipients.
- If delivery fails, pending approval state stays unchanged and requires operator review in this screen.

If notifications are not arriving:

1. Confirm target capabilities include notify.
2. Confirm target IDs are selected in this directory or session override.
3. Confirm the user is not filtering notification channels too narrowly.
4. Use section 15 for deeper communication troubleshooting if target integration is suspect.

## 7. Audit Trail And Tool Calls

Use the Audit Trail and Tool Calls view when you need evidence, not guesswork.
It shows what agents, workflows, and operators asked Evander to do, in a read-only timeline with
links to nearby records.

For approvals, workflow execution decisions, and common failure flow patterns,
use the deeper decision guidance in section 6, workflow execution details in section 11,
and end-to-end recovery patterns in section 20.

### 7.1 Tool Call Search

Use filters first, so you can answer a question before opening a long list.

1. Open **Tool Calls** from the left navigation or from the Dashboard quick link.
2. Set the correct directory in the shared header first.
3. Choose filters for what you need:
   - free-text search
   - session or agent identifier
   - tool name
   - status (`allowed`, `denied`, `error`)
   - directory scope
   - start/end time
   - result limit
4. Apply the most specific filter first (for example: session first, then tool, then time).
5. Sort by newest or oldest first based on whether you are checking a current issue or reconstructing
   a timeline.

Common search shortcuts:

- to verify one job: filter by session and then narrow by status
- to confirm a specific impact area: filter by tool name
- to investigate a gap: filter a narrow time window first, then widen if no rows appear

### 7.2 Reading A Tool Call

Open a row to confirm what was attempted and what happened.

1. Note the timestamp and event status (`allowed`, `denied`, or `error`).
2. Confirm who or what started it:
   - actor (agent or operator)
   - directory
   - session
3. Confirm what was requested:
   - tool name
   - request ID
   - correlation ID when one action is part of a larger flow
   - workflow run ID when automation called the tool
4. Check outcomes and safety gates:
   - decision
   - duration
   - payload digest and approval ID when approval was required
5. Review the redacted arguments and redacted result for context.

Notes for operators:

- Redaction is expected. Sensitive values are hidden by design.
- If the action was approved, the approval ID links the call back to the human decision trail.
- If the call was denied, the row is still usable evidence for what was blocked and why.

### 7.3 Following Related Records

Tool Calls are designed to move you quickly to the owning record.

1. In an open Tool Call row, use relation links to open:
   - session record
   - approvals
   - workflow run
   - agent
   - VM
   - reports
   - configuration
   - users
   - workers
2. Open links in the order that matches your question:
   - for "who requested it", open session/agent first
   - for "did approval matter", open approvals first
   - for "what run did it come from", open workflow first
3. Keep the same IDs with your notes (session, request, correlation).
   This makes handoff and follow-up updates much faster.

Tip: Use this section to avoid rebuilding state from memory; every related tab should point to the same
source IDs when you need follow-up evidence.

### 7.4 Incident Review

Use this section when several calls are connected.

1. Set a narrow time window first.
2. Search by session, tool, or actor depending on the incident scope.
3. Add all relevant calls to a single view by status and sort order.
4. Read each row in timeline order and write down the key IDs:
   - request ID
   - correlation ID
   - session ID
   - workflow run ID
   - approval ID
5. Open linked records to confirm chain of custody:
   - approvals explain human gates and decision timing,
   - workflow links explain trigger and node context,
   - agent/session links explain who owned the active session.
6. Keep non-sensitive findings only in handover notes; redacted values are usually enough for
   operational decisions.

A short incident check works best in this order:

1) Was there an action at the expected time?
2) Was it allowed, denied, or errored?
3) Was a required approval present and valid?
4) Did any workflow step show a mismatch between request and execution?
5) What is the smallest follow-up action for a safe recovery?

### How-to: Find What An Agent Did

Use this when you need a direct, auditable statement about one agent's behavior.

1. Open **Tool Calls**.
2. Filter by **Session** and, if needed, exact **actor/agent**.
3. Limit the time range to the period when the agent was active.
4. Sort newest-first for quick discovery, then open likely rows.
5. For each row, record:
   1. timestamp,
   2. tool,
   3. status,
   4. request ID,
   5. workflow run ID (if present).
6. Open the linked Session or Agent record to confirm ownership and context.
7. Open the linked Workflow run if the action was produced by automation.

Expected result:

- A clear list of what the agent attempted, when, and with what execution outcome.

### How-to: Confirm Whether A Sensitive Action Ran

Use this when verifying if a risky action happened before escalation or rollout.

1. Open **Tool Calls**.
2. Filter by the sensitive tool name.
3. Set status to show both `allowed` and `denied` to catch policy blocks.
4. Narrow by session or actor to avoid false positives.
5. Identify the most relevant entry and confirm the status:
   - `allowed` means Evander executed (or executed past the gate path).
   - `denied` means execution did not proceed in that attempt.
   - `error` means execution failed after permission was granted.
6. Open the linked approval row if an approval ID appears.
7. Copy the request ID and correlation ID into the incident notes for follow-up.

Decision point:

- If status is not what policy expected, use section 6 for approval logic and section 20 for recovery
  flow.

### How-to: Build A Timeline For An Incident

Use this when multiple tools, sessions, or runs overlap.

1. Pick an incident window and enter it in the time filters.
2. Filter to the affected directory and one key session.
3. Add status filters so both successful and failed attempts are visible.
4. Sort oldest-to-newest to reconstruct cause and effect.
5. In each row, capture the stable IDs:
   1. request ID,
   2. correlation ID,
   3. session ID,
   4. workflow run ID,
   5. approval ID.
6. Open links from each row in this order:
   - Session
   - Approvals
   - Workflow
   - Agent/VM
7. Build a plain-language sequence:
   1. what started,
   2. what gate or approval was expected,
   3. what actually ran,
   4. where it failed or succeeded.
8. Add one line of handoff summary: "next action", "open owner", "decision owner", and "current risk".

Expected result:

- A clean chronological account you can hand to another operator without relying on memory.

## 8. Issues And Work Intake

Issues are how operators bring new work into Evander in a consistent, traceable way.
An issue record is a small ticket-like entry you control from Admin Web: it stores the task
description, repo source, and execution hints you want the workspace to use.

Use this section to create intake records, start work from them, and follow the first lifecycle after
an issue is launched.

### 8.1 Issue List

Open **Issues** to see local records available for your selected directory.

The list helps you answer:

1. What work is currently queued or active.
2. Which issue belongs to the selected directory and scope.
3. Which issue already has a VM or session linked.
4. Which issues are editable or already locked by system state.

For fast triage, use the state filters at the top:

1. **Active** to focus on in-progress and recently started items.
2. **All** to audit historical and inactive items.
3. A specific state if your deployment exposes more granular categories.

When you open an issue row, check these fields first:

1. Title and source.
2. Last-updated time.
3. Repo source.
4. Directory and YAML path.
5. Body text.
6. Metadata block (if present).

If actions are unexpectedly missing in the list:

1. Confirm directory selection.
2. Confirm the lock state is correct for mutation.
3. Refresh the page.

### 8.2 Create Or Import An Issue

Use manual entry when you are starting from a new internal request.
Use import when you already have a prepared issue file from another system or an exported template.

Use **New issue** for both cases.

For manual entry, fill:

1. **Title**: one clear sentence.
2. **Repo source**: where the work should run from.
3. **Body**: task goal plus constraints.
4. Optional VM bootstrap choices (for example dotnet, nuget, playwright, mssql) if required.

For import, choose the issue YAML file and let the form prefill fields.

Common safety expectations:

1. Keep one objective per issue.
2. Put only actionable bootstrap options in the setup part.
3. Save the issue before launching multiple downstream actions from it.

If an issue is linked to active work, the UI may mark it as non-editable.
In those cases, treat it as a reference row and start from a new issue entry or a defined follow-up path.

### 8.3 Start Work From An Issue

You can start execution from an issue record by creating a new VM or by creating work immediately during
issue creation.

Before launch, confirm:

1. Directory and lock state.
2. Correct issue selection.
3. Repo source and bootstrap options reflect the task.

Issue launch behavior:

1. Issue data is used to prefill the work target setup.
2. Launch inherits the current directory unless the issue form has a more specific value.
3. The system creates a target record you can move into the execution flow from.

How to think about VM launch:

1. Use immediate launch from issue creation when you want no delay between intake and execution.
2. Use later launch when priority or repo source needs review first.
3. Use bootstrap options to install only what this issue needs.

For target-specific differences (VM versus container backend), do not change assumptions in this section; use section 9 for target behavior details.

### 8.4 Track Issue Follow-Up

Once work starts, issue tracking continues across multiple pages:

1. **VMs** for runtime status, power state, and host machine details.
2. **Agents** for control state and live session context (see section 4).
3. **Sessions** for expiry and access policy on the running work context.
4. **Tool Calls** to audit what the agent requested and how calls were executed.
5. **Artifacts** when outputs are published.
6. **Reports** for workflow-level outcome signals and repeated failure patterns.

Use this sequence to keep handoff clean:

1. Confirm the issue to VM or session link is visible and stable.
2. Check if any approvals are pending for high-risk actions.
3. Capture artifact/result references once work reaches expected checkpoints.
4. Record issue outcomes and residual risks in notes before handoff.

If a problem appears after issue launch, use section 7 for operational trace reconstruction and
section 20 for end-to-end incident analysis.

### How-to: Create A Local Issue And Start Work Immediately

Use this when you want to capture a task and begin execution in one operator action.

Prerequisites:

1. Correct directory selected in the shared shell.
2. Privileges unlocked if your environment requires mutation unlock.

Steps:

1. Open **Issues**.
2. Click **New issue**.
3. Enter a clear **title**.
4. Set the correct **repo source**.
5. Write the task goal in the **body**.
6. Choose minimal bootstrap options needed for this task.
7. If supported in your deployment, enable immediate VM startup.
8. Save the issue.
9. Confirm the created VM or agent target in the related tab.
10. Open **Agents** or **VMs** to verify the target is running and linked.

Expected result:

1. New issue appears in the list.
2. A matching execution target is started from that issue.
3. The running target links back to the issue context for traceability.

If launch does not start:

1. Confirm bootstrap settings are supported in the selected directory.
2. Re-open the issue and check for validation errors.
3. Retry from **Issues** or open directly in section 9 for target-specific checks.

### How-to: Start A VM From An Existing Issue

Use this when an issue already exists and you now need to begin VM-backed execution.

Prerequisites:

1. The issue row shows the correct task and scope.
2. Privileges are unlocked.

Steps:

1. Open **Issues** and select the issue.
2. Verify repo source, title, and directory.
3. Open the action to start work from the selected issue.
4. Choose launch bootstrap options.
5. Confirm launch settings.
6. Start the VM.
7. Return to the VM row and wait for live status.
8. Open the linked session or agent entry for policy and thread context.

Expected result:

1. VM is created for that issue.
2. The issue row shows progress toward a running target.

If launch fails:

1. Check lock state and directory scope.
2. Verify the issue still allows launch actions.
3. Validate that the repo source is reachable.
4. Continue with the operator follow-up flow in this section, or inspect execution target details in section 9.

### How-to: Edit Or Delete A Local Issue

Use this when the intake details need correction or when cleanup is required.

Prerequisites:

1. Correct directory selected.
2. Privileges unlocked.
3. You have checked whether work has already started from the issue.

Steps:

1. Open **Issues**.
2. Find the target issue and confirm state.
3. If the issue is still editable, open **Edit**.
4. Update title, body, repo source, or bootstrap note fields only.
5. Save the update.
6. If the issue must be retired and no active context depends on it, use **Delete**.
7. Confirm deletion only after understanding downstream impact.

Expected result:

1. Edits apply immediately to the issue row.
2. Deletions remove only the local issue record that you selected.

If deletion is blocked:

1. Check whether the row is marked non-editable/non-deletable.
2. Create a replacement issue if the operational context must continue.
3. Use related active session or report evidence to hand off safely.

## 9. Execution Targets

Execution targets are the environments where agent work actually runs. In Evander today, operators mainly use two user-facing target types:

1. Virtual machines (VMs), usually for longer or heavier tasks that need a full guest environment.
2. Container workers, for disposable container execution with tighter, bounded defaults.

This section helps you decide what to inspect and how to operate safely. For creating sessions, use **Section 4: Working With Agents** and **Section 5: Sessions, Access, And Policy**. For background worker status and health, use **Section 18: Workers And Background Services**.

### 9.1 Choosing An Execution Target

Pick the target type that matches what you want to run:

1. If you need to see a live VM-style guest with network bootstraps, traditional tooling compatibility, or predictable full-host behavior, use a VM-backed target.
2. If you need quick disposable sessions with container-backed execution, use container worker-backed targets.
3. If the selected directory or provider policy is set to `libvirt_vm`, most operator workflows route to VMs.
4. If the provider policy is set to `container_worker_vm`, operator sessions and related actions are executed as container jobs.

Operator checks before proceeding:

1. Confirm directory context is correct.
2. Confirm unlocked state if you plan to create or change anything.
3. Confirm the current backend expectation from your session create flow.

Where to find each type:

1. **VMs page**: visible and useful for VM-backed work, usually on `libvirt_vm`.
2. **Agents and Sessions**: both show target identity, session links, and state across both target types.
3. **Container worker action context**: shows execution/job evidence in execution-oriented views.

If a target appears in a different shape than expected, pause and verify whether you are using the right backend for the directory and session.

### 9.2 Virtual Machines

VM targets are useful for full guest-style execution and traditional lifecycle actions.

What operators usually inspect:

1. Session linkage: which session is attached and who started it.
2. Directory and provider: so you know scope and ownership.
3. Power state and timestamps: whether the VM is running, stopped, or recently changed.
4. IP and labels: for quick triage of connectivity and routing.

Where to operate:

1. Open **VMs** for direct target management.
2. Open **Agents** for target-linked context and quick policy/approval review.

Lifecycle controls (mutations):

1. **start**
2. **shutdown**
3. **destroy**

Use lifecycle actions only after confirming you are on the right VM and the session is the expected one.

Important reminder:

- Destroy removes VM state managed by the control plane for that target. Use with care.
- If work came from an issue, cross-check **Section 8: Issues And Work Intake** for expected start context before deleting.

### 9.3 Container Workers

Container workers run sessions through the worker daemon and container jobs.

What operators usually inspect:

1. Worker-backed execution status on the session and agent views.
2. Execution history and thread continuity for the same session.
3. Cleanup state, especially after stop/completion/failure.

How container logs are presented:

1. **Executions**: raw job-level log stream by run.
2. **Threads**: Codex conversation log grouped by thread id (for container sessions where available).
3. Agent output summary is visible alongside session and tool-call context.

Cleanup states to recognize:

1. `stopped`
2. `failed`
3. `abandoned`
4. `cleanup_failed`
5. `cleaned_up`

Use these states to decide whether to wait, retry cleanup, or escalate to artifact-based review.

### 9.4 Logs And Output

Execution output is spread across a few tabs by purpose:

1. **Agent reasoning and output**
   Use the agent/session views for user-facing messages tied to the target.
2. **Execution logs**
   Use job-level execution logs for timing, command-level progress, and worker failures.
3. **Workflow results**
   Use **Workflow** runs and related run inspection for approval chains and orchestration context.
4. **Artifacts**
   Use **Artifacts** for downloaded or downloadable run outputs and bundles.

Quick reading order:

1. Confirm the target state and state change times.
2. Open the target view (VMs or Agents/Sessions).
3. Read recent execution/job logs.
4. If an approval or external action is involved, open **Approvals** and then **Tool Calls**.

Use **Section 7: Audit Trail And Tool Calls** when you need a strict action timeline.

### 9.5 Cleanup And Recovery

Cleanup and recovery are separate from normal stop/complete behavior.

Typical outcomes:

1. `stopped`: work ended and target is inactive.
2. `failed`: run did not complete cleanly.
3. `abandoned`: cleanup path did not complete automatically.
4. `cleanup_failed`: cleanup was attempted but not completed.
5. `cleaned_up`: worker-side cleanup is done while host manifest remains for review.

General operator flow:

1. Check whether the run has already produced needed output.
2. Confirm no pending approvals are blocked by this state.
3. Decide whether automatic cleanup is sufficient or manual intervention is required.
4. If cleanup state is problematic, review logs before any destructive action.
5. Retain evidence through session, tool-call, and logs before retrying.

For unresolved worker service issues, check background services in **Section 18** before touching more targets.

### How-to: Inspect A VM Before Taking Action

Use this when you need to decide whether to start, shut down, or destroy a VM.

1. Open **VMs**.
2. Select a directory and verify the active scope.
3. Select the VM row.
4. Confirm VM session link, provider, and power state.
5. Check the latest status timestamps and any linked session IDs.
6. Review recent log entry notes or summaries.
7. If needed, open the related session from the linked row.
8. Decide action:
   1. If healthy but idle, keep it stopped and monitor.
   2. If running unexpectedly, shut down first and capture evidence.
   3. If obsolete and confirmed, proceed to destroy only when it is safe.
9. Confirm lock/unlock state matches your organization's change process.
10. Refresh and re-open the row after action to verify the new state.

Expected result:

1. You have a confirmed target action aligned with the current state.
2. The VM list shows the updated lifecycle state.

### How-to: Create A Standalone VM

Use this when you need an independent VM that is not tied to a workflow trigger.

1. Open **VMs**.
2. Unlock privileged mode.
3. Click **New VM**.
4. Enter a clear VM name.
5. (Optional) set session context and model.
6. Choose required bootstraps (for example .NET, NuGet, Playwright, or MSSQL) based on expected task needs.
7. Confirm directory and provider context.
8. Start creation.
9. Open the new VM row and confirm:
   1. session attachment
   2. power state
   3. timestamps
   4. provider and identity
10. Add any agent session notes after creation if your run plan requires special handling.

If creation does not appear:

1. Check directory and privileges.
2. Refresh the VMs view.
3. Re-check required bootstrap toggles.

### How-to: Review A Container Worker Execution

Use this when you need to decide if a container-backed run completed cleanly or needs follow-up.

1. Open **Agents** or **Sessions** and select the target session.
2. Confirm backend indicates container worker execution.
3. Open the agent execution section in **Executions**.
4. Read the latest execution entry and status.
5. Open **Threads** if you need task-level conversation continuity.
6. Check cleanup state and last worker completion signal if the job appears to have ended.
7. If the session includes external interactions, open related tool-call records.
8. If artifacts were produced, open **Artifacts** and resolve by artifact key.
9. Record the run outcome before making any next state transition.

Expected result:

1. You can answer whether the run is finished, failed, or waiting for cleanup.
2. You know the next safe action, either continue, re-run, or recovery cleanup.

### How-to: Handle A Cleanup Failure

Use this when an execution shows `cleanup_failed` or similar incomplete cleanup state.

1. Open the target session or agent record.
2. Confirm the session is in a cleanup-failed or failed completion state.
3. Capture the displayed cleanup error and last worker observation.
4. Open related execution log lines around the cleanup window.
5. Check for pending approvals or locked policies that could block safe cleanup.
6. Confirm you still have required evidence:
   1. session record
   2. logs
   3. tool-call link
7. Retry cleanup only once the immediate blocker is clear.
8. If cleanup still fails, keep state visible and escalate through normal operational notes.
9. Revisit the session for manual recovery or safe teardown only after the run context is clear.

Safe completion pattern:

1. cleanup retried successfully -> target returns to a stable non-actionable state.
2. cleanup still failing -> preserve evidence, create a follow-up ticket, and avoid destructive action until recovery path is clear.

## 10. Artifacts And Files

Artifacts are how Evander preserves important outputs from agent and workflow work.
Use this section to find, download, upload, and verify those outputs.

For workflow execution context, see section 11. For who requested or executed the actions that created an artifact, see section 7. For Team deliverable handoff, see section 13. For file transfer approvals and send limits, see section 15.

### 10.1 What Artifacts Are

For operators, an artifact is a durable package of one or more files that is stored for later use.

Use this concept in daily operations:

1. A session or workflow publishes finished outputs as an archived package.
2. The package gets a stable artifact key.
3. You can retrieve it later from the Artifacts tab.

Artifacts are different from:

1. temporary runtime files in an active VM or container session,
2. session logs and Tool Calls,
3. notifications and chat messages.

If a file should survive beyond a live session and be replayed later, it belongs in the artifact store.

### 10.2 Artifact Keys

An artifact key is the stable identifier used to find a published package later.

You usually see keys in:

1. workflow run outputs,
2. Tool Call metadata,
3. Team deliverable details,
4. manual uploads.

Use keys as your first lookup target when you already know the reference.

Safe key use:

1. Use one meaningful naming style per team.
2. Keep keys unique.
3. Prefer existing keys from source records over guessing.

If two processes use the same key, the result can become confusing to audit later, even when uploads succeed.

### 10.3 Resolving And Downloading

Use the Artifacts tab when you need to get a previously published package.

1. Open **Artifacts** from the left navigation.
2. Confirm the active directory is the one that owns the work.
3. If you know the key, enter it and choose **Resolve key**.
4. If you do not know the exact key, filter by time range and result limit instead.
5. Select a row and open its metadata panel to check:
   - session id,
   - backend type,
   - file count,
   - byte count,
   - SHA-256,
   - source artifacts root.
6. Use **Download** to save the archive to your local machine.

Expected result:

1. You get a `.tar.gz` archive matching the selected row.
2. The filename and metadata align with the source context.

If nothing resolves:

1. Check for active directory mismatches and page scope.
2. Re-check the key for typos and case mismatch.
3. Use a wider published-time window in the filter.
4. If still missing, trace the run or deliverable where the key should have been written and verify it was published.

### 10.4 Manual Uploads

Use manual upload when an operator or external tool needs a file ready for future workflow steps.

1. Open **Artifacts**.
2. Click **Upload** (or equivalent upload action in the same tab).
3. Enter a unique artifact key for later references.
4. Select the local file.
5. Confirm upload.

Expected result:

1. A new artifact archive appears in the list.
2. Its key is immediately searchable and reusable by workflows.

Use this when:

1. You want to provide templates, test fixtures, or baseline docs.
2. You need a file ready before a workflow starts.
3. You need a deterministic input to a repeatable run.

If upload is blocked:

1. Confirm you have the privileges needed for mutation actions.
2. Confirm the file type or size is acceptable in your deployment policy.
3. Try a different artifact key if one already exists and you intended to create a new entry.

### 10.5 Artifact Safety

Treat artifacts as reusable evidence, not as automatically trusted content.

Before you open or reuse a package:

1. Check the source session and backend.
2. Verify the publish time matches expected work timing.
3. Confirm file count and byte count look reasonable for the known output.
4. Compare SHA-256 when you need strong integrity checks.

Do not use any artifact blindly for production output or external sharing.
Before sensitive reuse:

1. Confirm the artifact belongs to the correct session or workflow.
2. Confirm policy and approval expectations from section 15 if the artifact is being shared.
3. Confirm no approval bypass happened by linking to section 7 for the full call trail.

If a hash or metadata looks wrong:

1. Pause handoff or publication actions using this artifact.
2. Identify the producing session/workflow.
3. Re-open the source record and trace with section 7 or section 11 run inspection before deciding to continue.

### How-to: Download A Published Artifact

Use this when you already have a reference and need the exact package locally.

Prerequisites:

1. Correct directory selected.
2. Correct artifact key, or ability to narrow by time range.

Steps:

1. Open **Artifacts**.
2. Enter the key and click **Resolve key**, or filter by publish window.
3. Select the target row.
4. Confirm metadata (session id, file count, byte count, SHA-256).
5. Click **Download**.
6. Save the archive and verify the filename is what you expected.

Expected result:

1. Download begins immediately for the selected artifact.
2. Saved archive includes metadata that matches the selected row.

If download fails:

1. Confirm network access and local browser session.
2. Retry after reloading the page.
3. Resolve from source workflow/session again and verify the key still exists.

### How-to: Upload A File For Later Workflow Use

Use this when you need to stage a file before a workflow starts.

Prerequisites:

1. Correct directory selected.
2. Mutation permission unlocked, if your environment requires it.
3. A unique artifact key ready.

Steps:

1. Open **Artifacts**.
2. Click **Upload**.
3. Enter a new artifact key.
4. Choose the file from local disk.
5. Click **Upload**.
6. Wait for the new row to appear in the list.
7. Open the new row and verify key, size, and source artifact metadata.

Expected result:

1. The file is available as a published artifact package.
2. The key can be reused by future workflow operations.

If upload does not create a row:

1. Retry with a different key.
2. Reduce file size or retry with a non-binary file if policy may block type.
3. Verify you still have permission to perform upload actions.

### How-to: Trace An Artifact Back To Work

Use this when you need the producing owner, session, or workflow for an artifact.

Steps:

1. Open the artifact row in the Artifacts tab.
2. Record the artifact key and session/metadata fields.
3. Open the linked session record (or search by session id in **Sessions**).
4. Follow the workflow run link if the artifact came from automation.
5. Open Tool Calls for the same session window and compare request IDs.
6. If this artifact belongs to Team output, open Team deliverables for that session or case.
7. Validate the chain from creation to publish before making handoff decisions.

Expected result:

1. A readable chain that answers who produced, where it was produced, and when.

If links are incomplete:

1. Re-check the correct directory and time range in section 7 records.
2. Confirm the artifact key did not come from another active run.
3. Review Team or delivery context in section 13 for alternative attribution.

## 11. Workflows

Workflows connect events, decisions, and actions so that Evander can run recurring automation safely and with human oversight.

Use this section to open, inspect, and adjust workflows without needing deep developer knowledge.
For operator details about action logging, use Section 7: Audit Trail and Tool Calls.
For repeated-failure trends and reporting, use Section 12: Reports And Operational Insight.
For safe config revision handling, see Section 16: Configuration.
For end-to-end incident flow, use Section 20: Common End-To-End Tasks.

### 11.1 Workflow Basics

Think of a workflow as a flow chart.

1. A trigger starts the flow when a specific event occurs.
2. Gates decide whether the flow should continue, split, wait, or stop.
3. Queries pull additional context from systems that the flow needs.
4. Effects perform an action, such as sending notifications, starting an Agent session, or updating external state.

You can read this like a human process:

1. "Something happened." (trigger)
2. "Is this the right kind of something?" (gate)
3. "What extra details do we need first?" (query)
4. "Do the approved action." (effect)

The lines between boxes are edges, and each edge carries output from one node to the next.

For operators, the key mindset is this:

1. Every node is accountable for one clear behavior.
2. Edges are where data moves, so broken edges usually show as missing values or later node errors.
3. You can disable a trigger instead of deleting it when you only want to pause a workflow safely.

### 11.2 The Workflow Editor

Use the Workflow tab to manage both graph design and execution viewing.

1. Open the Workflow tab from the left navigation.
2. Select the correct directory so you are editing the right scope.
3. Unlock privileges before editing.
4. Use the Graphs panel to choose the graph and review its name, description, and scope.
5. Use the Create panel to add nodes, then place them on the canvas.
6. Draw or confirm edges that move data between related nodes.
7. Open a node in the Inspector to edit its fields, display label, and binding expressions.
8. Use the Outputs section to verify what values the node sends forward.
9. Watch diagnostics for missing fields, unresolved bindings, or schema mismatches.
10. Use Save workflow only after the graph is clean and the node states are accepted.

The editor keeps your local, unsaved edits staged in the browser until you save.
If you leave the page with unsaved edits, the UI warns before discarding.
If you need a quick graph-level run-through, use the Tutorials control in this tab.

Common editor notes:

1. Disabled triggers appear muted and do not start new work.
2. Query nodes can be used without heavy mental overhead if their input requirements are clearly set.
3. Effects that touch external systems are the places where Section 7 and approvals workflows matter most.

### 11.3 Safe Editing

Safe editing is mostly about avoiding accidental overwrite and unintended side effects.

1. Keep the workflow editor in one directory at a time.
2. Make one change at a time when multiple operators are active.
3. Use Reset changes for a node if a single node became messy.
4. Check diagnostics after each meaningful change.
5. Save in short cycles, especially before moving to a different graph.

What can happen when changes are not staged correctly:

1. You can lose local changes when navigating away without confirmation.
2. Concurrent edits can create revision conflicts and force a reload.
3. A valid change can be delayed if a different operator updated the same graph first.

If this happens, follow the recovery pattern in this manual's how-to "Recover From A Save Conflict".
For the deeper revision model and why it happens, see Section 16.3 Safe Save Behavior.

### 11.4 Workflow Runs

The workflow page also shows runtime execution data so you can diagnose what happened.

1. Use Current runs for active and recently active executions.
2. Use Latest runs when you need a broader history view by graph or node scope.
3. Use event, trace, session, artifact, and other IDs to locate a specific execution quickly.
4. Select Inspect mode to see node-level execution status.
5. In inspect mode, review node state, duration, inputs, outputs, errors, and binding trace.
6. Use links on run rows to open related sessions, tool calls, approvals, or artifacts when the run touches external systems.
7. Exit inspect mode to return to graph editing.

A failed run is usually easiest to understand by checking:

1. Which node started the failure.
2. Whether the failure is from missing input or an external effect failure.
3. Whether upstream binding paths resolved correctly.
4. Whether this looks like a one-off versus a repeat across multiple runs.

Use Section 12 for failure frequency and pattern reporting, and Section 20.1 for the full incident chain.

### 11.5 Generated Draft Workflows

The generator can create a draft graph from a plain-language description.

Important behavior for operators:

1. The draft is a preview. It is not live until you review and save.
2. Draft generation does not run workflows.
3. Accepting the draft imports it as unsaved edits into the normal editor.
4. You can still review each node and binding before saving.

Use this when:

1. You need a fast starter graph for a known pattern.
2. You want to compare an idea against current operator style before manual drafting.
3. You need to avoid repeated boilerplate for common automations.

Use careful review for all effect and query nodes after accepting a draft, especially in production directories.

### 11.6 Workflow Tutorials

Use built-in tutorials as a fast reorientation tool, not a substitute for production review.

1. Open Tutorials from the workflow page.
2. Start First steps for a guided micro-graph.
3. Use Skip if your team has already completed the onboarding walkthrough.
4. Use Restart later if you need to refresh the operator sequence after a long gap.

Because workflows impact live automation, treat tutorial changes and production changes separately.
Do not carry assumptions from tutorial graphs into production graphs without an explicit review pass.

### How-to: Inspect A Failed Workflow Run

Use this when an automation job shows an error in the Dashboard or operators report a missed action.

Prerequisites:

1. Correct directory selected.
2. Read access to Workflow and Tool Calls.
3. Optionally, unlocked privileges for any related follow-up actions.

Steps:

1. Open **Workflow**.
2. Open **Current runs** and locate the failing run.
3. Copy the known reference (event ID, trace ID, session ID, or artifact ID) if needed.
4. Click **Inspect**.
5. Select the failing node in the run graph.
6. Review the node status, duration, and error text.
7. Open binding trace and compare expected versus provided inputs.
8. Open linked session/approval/Tool Call records if the node calls outside systems.
9. Confirm whether this is a one-time data error or a repeated pattern.
10. Record one root cause and next action before continuing.

Expected result:

1. You can name the first node that failed.
2. You can determine whether missing data, bad trigger source, or external dependency failure caused the break.
3. You have links to supporting evidence in Tool Calls or Approvals.

If no output appears:

1. Re-check directory scope.
2. Confirm the run reference is in the current runtime set.
3. Switch to Latest runs for a wider search window.

### How-to: Add A Simple Node To A Workflow

Use this when you need a small enhancement, such as one extra check or one extra notification.

Prerequisites:

1. Correct directory selected.
2. Workflow page open.
3. Privileges unlocked.

Steps:

1. Open the graph you want to change.
2. Click **Create** and choose a node type (trigger, gate, query, or effect).
3. Place the node on canvas near related nodes.
4. Connect one incoming edge from the source and one outgoing edge to the next target.
5. Open the node inspector and set:
   1. display name,
   2. required settings,
   3. any downstream binding uses.
6. Review Outputs for the exact fields you expect to pass forward.
7. Check diagnostics for unresolved placeholders or missing required fields.
8. Save workflow.
9. Run the quick verification loop:
   1. open Latest runs,
   2. trigger a known event if possible,
   3. confirm the new node executes as expected.

If save blocks:

1. Fix diagnostics first.
2. If blocked by a stale revision, follow Recover From A Save Conflict.

### How-to: Change A Trigger Without Breaking The Graph

Use this when an event source, event type, or trigger condition must be adjusted.

Prerequisites:

1. The graph is not currently under active edit by multiple operators.
2. Lock/unlock state is understood.
3. You have identified at least one recent successful run path.

Steps:

1. Open the target graph and select the trigger node.
2. Note current downstream node targets in the inspector panel.
3. If needed, disable the trigger temporarily while editing.
4. Update trigger fields (for example filter, event source, polling, or route selectors).
5. Save a draft with the trigger change only.
6. Re-run diagnostics and confirm no edges become unresolved.
7. Use Current runs to confirm no new unexpected failures appear.
8. Re-enable the trigger and save.

Expected result:

1. The graph keeps the same downstream shape.
2. Existing downstream nodes still receive compatible inputs.
3. Old and new trigger events behave according to the new definition.

Common recovery:

1. If failures appear at the first downstream node, check whether the output contract changed.
2. If needed, revert to saved state and reapply trigger edits with narrower field changes.

### How-to: Recover From A Save Conflict

Use this when the UI reports the graph was changed by someone else before your save completed.

Prerequisites:

1. Unsaved local edits still visible in the editor.
2. You can read the latest graph state from the server.
3. You have identified your original intent (for example one changed field).

Steps:

1. Stop and pause additional edits while the recovery runs.
2. Reload the latest workflow revision from the server.
3. Compare required changes against the new baseline.
4. Re-apply only intentional edits, one node at a time.
5. Re-check diagnostics before saving.
6. Save workflow.
7. Open the graph in Current or Latest runs and confirm normal behavior.

Expected result:

1. Your intended change is preserved.
2. The graph remains consistent with the latest shared version.
3. No unrelated edits from other operators are overwritten.

If you need the full process and implications:

1. See Section 16.3 Safe Save Behavior.
2. Keep a short note in your operational handoff if conflict frequency increases.

### How-to: Generate A Draft Workflow From A Description

Use this when you have a business requirement but want the editor to draft a first pass.

Prerequisites:

1. No unsaved edits in the current editor (or you are ready to confirm they can be discarded).
2. Access to **Generate workflow** in the workflow page.

Steps:

1. Open the workflow generator.
2. Enter a clear plain-language brief in one or two short paragraphs.
3. Start generation and wait for the draft preview.
4. Open the draft and inspect:
   1. node list order,
   2. trigger source,
   3. branch flow,
   4. outputs and bindings.
5. Accept the draft into the editor as unsaved changes.
6. Review every node for production safety:
   1. trigger scope,
   2. effect actions,
   3. query dependencies,
   4. output mapping.
7. Save workflow.
8. Run at least one validation run path from Current or Latest runs.

Expected result:

1. A reviewable draft replaces the empty space in the graph with edits.
2. You either save a safe production graph or keep changes staged for later.

If the draft is not acceptable:

1. Return without saving and keep the current graph untouched.
2. Re-run generation with tighter wording, or build the graph manually.

## 12. Reports And Operational Insight

Reports are where operators check "is this working well?" versus "do we have an outage or drift signal now?"

Use this section to read workflow health patterns, repeated failures, PR integration safety checks, conformance guarantees, and cost signals before you make release or retry decisions.

For detailed step-level workflow diagnosis, see section 11. For evidence trail rebuilds, see section 7. For incident handoff and safe action decisions across multiple pages, see section 20.

### 12.1 Workflow Graph Activity

The workflow graph activity report helps you answer:

1. Which workflow graphs are running successfully and which are not.
2. Whether activity has shifted toward one graph unexpectedly.
3. Whether run count or node count changed in a way that indicates new noise, retries, or duplicate routing.

What the report usually shows:

1. A ranked list of graphs for the selected time window.
2. Activity and run counts.
3. Node-level totals and latest activity time.
4. Status breakdowns and active node status.

Use this report during handoff windows when a dashboard warning is non-specific and you need objective evidence first.

### 12.2 Workflow Failure Clusters

Failure clusters group similar workflow failures so you can separate one-off incidents from repeat issues.

You use this when a spike is suspected or when many failures appear to share the same root cause.

What the cluster view does:

1. Group failures by graph, node, and normalized error signature.
2. Remove volatile IDs and timestamps from the grouping signature.
3. Show active, recent, and resolved labels as triage signals.
4. Link each cluster to representative workflow run examples.

Label meaning is a starting point, not a full verdict. For root cause and external context, open a representative run in the workflow detail view from section 11.

### 12.3 PR Integration Preflight

This report gives a dry-run read-only check before PRs are combined in branch order.

Use it when:

1. You are planning multiple merges into the same target branch.
2. A conflict is suspected but no conflict has been applied yet.
3. You need a quick safety signal before human merge sequencing decisions.

Expected read-only status meanings:

1. `clean`: merge simulation succeeded.
2. `conflict`: one or more content conflicts were found.
3. `missing_ref`: source or target commit/ref could not be resolved.
4. `stale_target_branch`: target changed after recorded upstream branch state.
5. `repo_error`: repository resolution failed.
6. `provider_error`: provider lookup failed.

The report does not change branch state, merge policy, or completion status.

### 12.4 Conformance Reports

Conformance reports show whether workflow contracts are still complete and what requirements are missing.

Two standard snapshots are exposed:

1. Trigger conformance snapshot.
2. Effect conformance snapshot.

Each record includes status fields such as:

1. `pass` or `fail` from report state,
2. `missing`, `invalid`, and `stale` when artifacts are unavailable, broken, or old,
3. generated time, requirement counts, missing coverage, and traceability task counts,
4. compact remediation hints when coverage gaps exist.

Treat this as an operational health signal. Use it with your CI or change-control flow, and investigate failing items before changing workflow packs.

### 12.5 Usage And Cost Awareness

This report section helps operators understand inference volume and expected spend before a work burst becomes costly.

Use it to:

1. Compare usage over selected time windows.
2. Spot unusual token spikes per session, model, or directory.
3. Estimate cost trends with catalog-driven model rates.

Cost values here are operational estimates:

1. They are based on local pricing rules and available token metadata.
2. They are not a billing invoice.
3. They may not include all provider-specific surcharge patterns.
4. OpenAI billing totals can differ from per-model estimate totals.

Before using these numbers for approval or budget messaging, confirm current catalog and pricing context in the pricing controls referenced by operations.

### How-to: Run A Workflow Activity Report

Use this when a deployment warning says "activity changed" but not where.

Prerequisites:

1. Admin Web is open and you are in the correct directory.
2. You have read access to Reports.
3. You know the desired time span (24h, 7d, 30d, or custom).

Steps:

1. Open **Reports**.
2. Select **Workflow graph activity**.
3. Choose a time preset or custom range.
4. Click **Run report**.
5. Scan ranked graphs and identify unusual changes.
6. Select a graph with unusual activity.
7. Check status split and latest activity time.
8. Open the most relevant graph detail in section 11 for node-level context.

Expected result:

1. You can point to a specific graph that changed materially.
2. You get a clear starting hypothesis for escalation or mitigation.

If the report returns no rows:

1. Check directory scope.
2. Confirm the selected window overlaps with expected activity.
3. Re-run with a wider custom range.

### How-to: Find Repeated Workflow Failures

Use this when repeated failed rows are visible across current or latest runs.

Prerequisites:

1. You have workflow run examples from section 11 or the Reports tab.
2. You can open representative run detail rows.
3. You have enough window size to capture repeated patterns.

Steps:

1. Open **Reports**.
2. Select **Workflow failure clusters**.
3. Choose a range that includes multiple runs (start with 7d).
4. Click **Run report**.
5. Sort by count and scan for highest frequency clusters.
6. Open one representative run from the cluster.
7. Compare failure signature with the node and status details.
8. Verify if the failures are:
   1. isolated (single node, single session),
   2. systemic (multiple nodes, broad graph surface), or
   3. stale (already labeled and no new activity).
9. Link to section 7 for action-level evidence and section 11 for run detail.
10. Decide whether to contain with scope changes, retry policy, or deeper root-cause triage.

Expected result:

1. You can state if the issue is isolated or systemic.
2. You have a representative run to support the next operational decision.

If the cluster view looks inconsistent:

1. Re-run with a wider range.
2. Confirm the same directory and selection is used consistently.
3. Open the run details directly and verify cluster labeling is a signal only.

### How-to: Check Whether PRs Are Safe To Combine

Use this before you merge or queue PR merge order.

Prerequisites:

1. You are reviewing Azure DevOps PR IDs for one repository.
2. You know your target directory policy and base branch.
3. You can use reports mode that supports PR simulation.

Steps:

1. Open **Reports**.
2. Select **PR integration preflight**.
3. Enter:
   1. provider (`azure_devops`),
   2. repo,
   3. directory path,
   4. mode (`single`, `pairwise`, or `ordered`),
   5. PR ID list.
4. Keep `repo_source` blank unless the directory override must be replaced.
5. Click **Run report**.
6. Read the overall status and `summary_text`.
7. Review participants and checks to see where the conflicts or branch drift appear.
8. If needed, expand PR order checks in `ordered` mode.
9. Only proceed with actual merge or merge scheduling when status is clean and findings are low risk.

Expected result:

1. You get an explicit go/no-go signal before branch changes happen.

If status is unsafe:

1. Share the exact conflict paths and participant list with the owning team.
2. Investigate using section 7 and 11 where the linked run context exists.
3. Defer combination until conflicts are resolved or sequencing is adjusted.

### How-to: Regenerate A Conformance Snapshot

Use this when conformance is stale, missing, or stale policy changes are expected.

Prerequisites:

1. You have unlocked privileges if your deployment requires mutation permissions for report actions.
2. You can access the **Reports** tab.
3. You understand trigger/effect separation.

Steps:

1. Open **Reports**.
2. Select **Conformance**.
3. Choose **trigger** or **effect**.
4. Click **Run report**.
5. Wait for `state`, `status`, and `generated_at` fields to refresh.
6. Review:
   1. requirement count,
   2. missing coverage,
   3. traceability task count,
   4. remediation snippets.
7. If any key requirement remains missing, confirm whether it is expected by current workflow behavior.
8. If needed, use the fallback command path for manual regeneration from the control host using existing conformance suite commands in the conformance report documentation.

Expected result:

1. Conformance state moves from missing/invalid/stale to a fresh pass/fail state.
2. The refreshed snapshot matches your latest workflow runtime shape.

If report regeneration fails:

1. Confirm the runtime directory is writable and correct.
2. Re-run after checking active locks and service health.
3. Inspect service and report errors, then correlate to section 7 for exact action history.

## 13. Team Add-On

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

### 13.1 Team Concepts

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

### 13.2 Teams Tab Overview

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

### 13.3 Set Up A Team

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

### 13.4 Manage People And Roles

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

### 13.5 Cases, Goals, And Tasks

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

### 13.6 Team Chat And Direct Calls

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

### 13.7 Deliverables And Reviews

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

### 13.8 Git Promotion And Releases

When your team uses Git-backed delivery, promotion follows a controlled ladder:

1. Agent output branch or committed source.
2. Review branch.
3. Feature branch.
4. Release branch.
5. Canonical branch push.

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

1. Internal branches are maintained inside the project Git repository context.
2. Only the canonical route pushes outward to the canonical branch.
3. Non-Git artifact deliveries remain valid as artifact-based package flows.

If release is blocked, review missing prerequisites and error state before retrying.

### 13.9 Timeline And Attention

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

### 13.10 Wake Behavior And Stand-Down

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

### How-to: Create A Team From A Template

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
6. If applicable, enter project git fields (host repo path, base branch, provider details).
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

### How-to: Register Or Edit A Team Agent

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

### How-to: Create A Case And Assign Work

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

### How-to: Chat With A Team Agent

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

### How-to: Start A Browser Call With A Team Agent

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

### How-to: Request And Decide A Review

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

### How-to: Publish A Reviewed Delivery

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

### How-to: Stand Down A Problematic Agent

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

### How-to: Investigate Why A Team Agent Did Not Wake

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

## 14. Accounting And Tax Add-Ons

Accounting and tax add-ons are optional, assisted workflows for operations that depend on
external services. They let automation prepare bookkeeping and tax actions, while leaving production
submissions and spend decisions to operator review.

Use this section when you run or monitor:

1. bookkeeping prep in Bokio
2. tax filing drafts in Skatteverket
3. accounting teams that coordinate both from one workflow

For base add-on registration details, start with section 16. For approval flow mechanics, use
section 6. For evidence artifacts and follow-on review chains, use section 10. For team setup and
handoff conventions, use section 13.

### 14.1 Add-On Availability

Both add-ons exist only when enabled in host add-on loading. If the add-on is not loaded, its tab is
not shown in Admin Web.

What to expect:

1. Each add-on keeps its own navigation and runtime surface (for example `Bokio` and
   `Skatteverket` in Admin Web).
2. Each add-on owns its own persistent records separate from core session records.
3. Each add-on is backed by host-side credentials and service configuration.
4. Add-on actions are still enforced by normal tool policy and approval controls.

If a tab is missing:

1. Open **Configuration** and confirm the add-on is enabled for the active directory.
2. Confirm the workflow workers have been restarted after add-on configuration changes.
3. Refresh and re-open Admin Web.
4. If still missing, review host settings in section 16 and service restart guidance in
   `operations.md`.

### 14.2 Bokio Assisted Bookkeeping

The Bokio add-on is used for bookkeeping preparation and execution.

Read capabilities:

1. Company and chart reads, including journal entries and invoices.
2. Supplier invoice reads and document uploads through controlled staging paths.
3. Prepared actions for:
   - journal entries
   - supplier invoices
   - bank payments

Operational model:

1. Agents and workflows create prepared actions first.
2. A prepared action stays pending until approved.
3. Approved actions are available for production execution through the same policy and approval system.

Important safety boundaries:

1. `prepared_action.status` values are stateful, such as `pending_approval`, `approved`,
   `executed`, `rejected`, and `failed`.
2. Bank payment actions still require signatory confirmation in Bokio before bank submission.
3. Supplier invoice paths may rely on preview APIs and should be treated as less stable.

For quick operational checks, use section 6 if approvals are not moving and section 10 for action
artifact traceability.

### 14.3 Skatteverket Assisted Filing

The Skatteverket add-on is for tax filing preparation and submission support.

What it tracks:

1. Available catalog of supported Skatteverket APIs and enabled API names.
2. Connection objects and readiness fields (`configured` and `ready`).
3. Drafts and submission records with revision and status.

Typical assisted filing flow:

1. Create or validate a draft for a supported API.
2. Check `submission` API status and API-specific validation.
3. Request submission approval.
4. Submit only with a matching approved request.

Important constraints:

1. Some API catalog entries may show `blocked`/`not_implemented_for_v1` behavior depending on workflow
   coverage.
2. External onboarding is still required before production filing can work.
3. API endpoints, secrets, and certificate values are host-side and should remain out of shared config.

Use section 6 for human review and section 16 for environment variable and connection metadata setup.

### 14.4 Approval Boundaries

The core principle is operator control. No add-on is intended to bypass human sign-off for sensitive
actions.

Boundary rules:

1. Agents or workflows can prepare actions and drafts.
2. Agents cannot complete sensitive production steps without policy-compliant approvals.
3. Exact-request matching is enforced on approved requests so stale or mismatched approvals are not used.
4. Bank payment actions that are prepared in Bokio still need external signatory workflow.

Recommended operating pattern:

1. Review and approve only after comparing purpose, supporting references, and expected mapping.
2. Reject and request a fresh action if the mapping or payload changed.
3. Execute or submit only the latest approved version.

See section 6 for the exact review sequence and payload diagnostics.

### 14.5 Accounting Department Team Pattern

Use the Team Add-on accounting template when bookkeeping and tax filing should be coordinated by
named roles.

Pattern in practice:

1. The Team template creates ownership roles (for example accounting manager, bookkeeping lead,
   reviewer, and payment preparer).
2. Team reviews and tasks provide explicit ownership handoffs before live submission.
3. Drafts and submissions are recorded in dedicated add-on stores, while Team state tracks who owns
   each handoff.

Why this pattern helps:

1. It removes role ambiguity for who can prepare, who can review, and who can submit.
2. It prevents accidental cross-team ownership by keeping accounting operations in one Team domain.
3. It supports continuity: when one person is unavailable, the role owner can be reassigned in Team.

For Team setup, template behavior, and role permissions, see section 13. For communication targets and
notification behavior, see section 15.

### How-to: Check Whether Bokio Is Ready

Use this when Bokio action support should be active but appears unavailable.

Prerequisites:

1. Correct directory selected.
2. Access to Admin Web.

Steps:

1. Open **Bokio** from Admin Web.
2. Open **Refresh**.
3. Read the connection summary:
   1. `status` should indicate a configured and healthy setup.
   2. `company_id` should be present.
4. Open the **Company** panel and confirm a valid response.
5. Open **Prepared actions** and confirm recent actions list the expected `kind`.
6. If status is not ready:
   1. Compare host config with section 16 expectations.
   2. Confirm the required Bokio company and integration-token settings are present in the host
      configuration.
   3. Restart the affected services only after configuration is corrected.

Expected result:

1. Company data is visible.
2. New actions appear with expected `pending_approval` or `approved` state.

### How-to: Review A Prepared Bokio Action

Use this when a prepared action is waiting, failing, or before execution.

Prerequisites:

1. Correct directory selected.
2. Access to **Bokio** > **Prepared actions**.
3. If a separate core tool approval appears, access to **Approvals** and privileges unlocked as
   required by your policy.

Steps:

1. Open **Bokio** and go to **Prepared actions**.
2. Find the action by `action_id` or most recent timestamp.
3. Review action metadata:
   1. `kind` (journal entry, supplier invoice, or bank payment)
   2. status
   3. payload note
4. Confirm supporting documents were uploaded to the intended scope.
5. Check mapped accounts and tax category matches expected policy.
6. If the action is `pending_approval`, approve or reject it in **Bokio** > **Prepared actions**.
7. If your deployment also creates a core MCP/tool approval for the same operation, open
   **Approvals** and satisfy that separate policy gate after the Bokio prepared action decision.
8. If approved:
   1. confirm account mapping still matches current rules
   2. execute through the normal approved action path used in your workflow
9. If status is `rejected` or `failed`, review the error/reason and request a corrected re-submission.

Expected result:

1. Only Bokio prepared actions approved in the add-on proceed to execution.
2. Failed actions are either corrected and re-prepared or explicitly blocked by operator decision.

### How-to: Check Skatteverket Connection Status

Use this when filing actions appear blocked or always fail validation.

Prerequisites:

1. Correct directory selected.
2. Access to the **Skatteverket** tab.

Steps:

1. Open **Skatteverket**.
2. Click **Refresh**.
3. Open **Connection status** and confirm:
   1. `configured` is true.
   2. the environment and connection id are expected.
   3. readiness flag is true after connection values are set.
4. Open **Connection settings** and review:
   1. enabled APIs
   2. environment
   3. token/OAuth/certificate settings
5. Open **Catalog** and confirm your target API name is listed and selected.
6. Open **Drafts** to confirm the draft status lifecycle for current work.

Troubleshooting:

1. If `configured` is false, check host-side secrets and section 16 config values.
2. If draft actions fail with unsupported status, confirm API coverage and disabled API operations.
3. If no connection is selected as default, set the intended connection as enabled.

Expected result:

1. A stable connection object is selected.
2. The catalog/API list includes your filing target.
3. Drafts and submissions move through validation and approval stages without unexpected blocks.

### How-to: Review And Submit An Approved Tax Draft

Use this when a Skatteverket draft has been prepared and approved for submission.

Prerequisites:

1. Matching approval exists in **Approvals** with matching scope.
2. Unlock as required for approval-dependent operator tasks.

Steps:

1. Open **Skatteverket** and review **Drafts**.
2. Select the target draft and confirm:
   1. `api_name`
   2. `status` is `validated`
   3. revision appears current
3. If validation failed, return to draft validation before submission.
4. Open the approval path in **Approvals** and confirm:
   1. decision is approved
   2. operator identity is expected
   3. request context matches this draft revision
5. Submit the draft only once with the matching approved request.
6. Open **Submissions** and confirm:
   1. a `submission_id` is present
   2. draft and submission status align
   3. result details are recorded
7. Link the submission record to the task or ticket and add a short operator note.

Expected result:

1. Draft status moves to a submitted outcome.
2. A submission record exists with traceable status and IDs.
3. Operator evidence is available for review and handoff.

## 15. Communication And Notifications

Communication settings control where Evander can read messages, send status or alerts, and move files through
external channels. Use this section when you need reliable message flow, approval delivery, or file handoff.

Read this with:

- session and policy controls in section 5,
- approval flow control in section 6,
- evidence checks in section 7,
- file artifacts in section 10,
- Team wake and direct agent collaboration in section 13,
- directory/provider configuration in section 16.

### 15.1 Communication Targets

A communication target is a named destination in **Configuration > Communication targets**.

Targets are scoped by directory and can be inherited from the root directory.

Each target is built with:

1. `id`: short operator friendly key, such as `ops-discord-releases` or `lead-ops-email`.
2. `provider`: which service owns the destination.
3. `kind`: such as `discord`, `teams`, or `email`.
4. `address`: the exact channel/chat/mailbox target value.
5. capabilities: what this target is allowed to do (`read`, `write`, `reply`, `notify`).

Capability meanings:

1. `read` lets Evander fetch inbound messages and attachment metadata.
2. `write` sends top-level messages and attachment posts.
3. `reply` sends thread replies or reactions when `thread_id` is required.
4. `notify` receives operator-facing status and approval alert messages.

Common target address formats:

1. Teams channel: `team-id/channel-id`
2. Teams chat: `chat:<chat-id>`
3. Teams one-on-one: `user:<user-id-or-upn>` for send-only targets
4. Discord: configured channel ID
5. Email: mapped by mailbox policy, with host-side provider auth for the selected directory

If you want one directory to hear on one set of channels and another directory to use a different set,
configure in that directory's communication target list rather than duplicating sessions.

### 15.2 Read And Send Capabilities

Use capability checks to avoid silent failures:

1. Inbound reads and attachment listing/download need `read`.
2. Outbound top-level sends need `write`.
3. Replies and reaction-like follow-up flows need `reply`.
4. Approval notices and workflow notices need `notify`.

Provider guardrail note:

1. A target can have `read` and `notify` but no `write`; this is valid for read-only monitoring.
2. Teams `user:` targets are send-only in practice and do not support read/reaction/attachment fetch for
   these user direct targets.
3. Teams and Discord reads still follow provider-side allowlists in sessions, so check session and directory
   policy in section 5.

Use section 6 before turning on higher-risk outbound send behavior, and use section 5 if file send policies
need tightening.

### 15.3 Approval Notifications

Approval notifications are operator alerts sent when a new approval record is created.

They are separate from session destination permissions. A session can still be blocked from outbound sends while
approval notices continue to work.

How approval notifications are chosen:

1. Directory policy sets a base approval-notification target list.
2. Session policy may add more IDs for narrower cases.
3. A live workflow run can add run-scope targets.
4. The host deduplicates and sends through targets with `notify`.

If no list is configured, notifications stay off by default.

### 15.4 File Transfer

Evander handles two distinct file pathways:

1. Session file policy in section 5, which decides if a session can receive or send attachments.
2. Communication file policy here, which controls destination and payload safety for message attachments.

Inbound flow:

1. Provider receives a file in a configured target.
2. Operator or tool lists matching attachments from that message/thread/mailbox.
3. Selected attachment is downloaded to a staged artifact record for review.
4. If needed for agent tool use, it can be copied into the session's communication workspace.

Outbound flow:

1. File must be a valid staged artifact from the session workspace.
2. Send payload must name the exact artifact references.
3. Destination target must match send allowlists and target `write`/`reply` needs.
4. If approval for send is enabled, the exact payload must be approved before dispatch.

Operator reminder:

1. Keep receive and send permissions conservative by default.
2. Keep executable or unknown extensions blocked even when MIME filters look safe.
3. Keep size and MIME caps tied to workload needs.

### 15.5 Teams, Discord, And Email Workflows

Evander uses three practical incoming channels:

1. Discord commands and mentions through host-side wake and ingest paths.
2. Teams inbox/chat events through delegated Teams authentication and ingest workers.
3. Email read and draft workflows through mailbox policy and token-backed provider config.

In practice:

1. Discord/Teams inbound message flows are host-side only. Credentials stay on the host.
2. Email is commonly read and draft first; outbound send is controlled separately.
3. Provider-native auth is configured in directory provider settings and visible in Configuration.
4. Team and workflow routing can consume inbound messages even when outbound is disabled.

If a downstream workflow should wake from messages, verify trigger nodes are enabled and
their allowlist or target settings match the current directory/session.

### How-to: Add Or Update A Communication Target

Use this when you need to add a new destination or correct an existing one.

Prerequisites:

1. Correct directory selected.
2. Communication targets section opened in `Configuration`.
3. Privileges unlocked for configuration saves.

Steps:

1. Open **Configuration > Communication targets**.
2. Click **Add communication target**.
3. Enter:
   1. `id` and clear display name.
   2. `provider` and `kind` (`discord`, `teams`, or `email`).
   3. Exact `address`.
4. Add capabilities:
   1. add `read` for inbound workflow reads,
   2. add `write` for top-level outbound sends,
   3. add `reply` for thread replies,
   4. add `notify` if you want approval alerts.
5. For email targets, confirm mailbox/identity pairing in section 16 provider settings.
6. Save target and verify it appears in the current directory effective list.
7. In section 5, update session communication allowlists only if the new target should be usable by that session.

Expected result:

1. The target appears in `Communication` targets for the directory.
2. The target only works for the selected capabilities.
3. Sessions with matching allowlists can now read/write using that target.

If validation fails:

1. Recheck `address` format (`chat:` prefix for Teams chat IDs, channel IDs for Discord).
2. Re-check capability set for the exact action.
3. For email, re-check provider auth and target mailbox setup in section 16.

### How-to: Send An Approval Notification To The Right People

Use this when approval events need to land in a team channel instead of generic defaults.

Prerequisites:

1. At least one notify-capable communication target is defined.
2. Access to `approval_notification_target_ids` in Configuration.
3. Privileges unlocked for policy edits.

Steps:

1. Open `Configuration` and go to Access Policy (or policy tab view).
2. Confirm the selected directory is correct.
3. Open the approval notification target chooser.
4. Add one or more notify-capable targets (for example an operator channel).
5. Save policy changes.
6. Run a low-risk approval path to generate a real pending record.
7. Confirm the operator receives the notification and that it contains request context.

Expected result:

1. New approval records trigger a host-side alert to selected targets.
2. Missing recipients do not block approvals, but the notification entry stays visible in delivery history.

If alerts do not arrive:

1. Confirm targets still include `notify`.
2. Confirm target still exists in directory and is not accidentally disabled.
3. Confirm the notification action status in logs and Tool Calls from section 7.

### How-to: Review An Inbound Attachment

Use this when you receive a file and want to confirm safety before handling it.

Prerequisites:

1. Message or mail item ID from the expected inbound message/chat thread.
2. Read permissions (`read` target capability) in the active directory and session.
3. Basic file policy enabled for inbound receive.

Steps:

1. Open the source context where the item arrived (session workflow context or message reference).
2. List attachments for the item:
   1. Discord by channel and message ID,
   2. Teams by thread/channel target and message ID,
   3. Gmail/Graph mail by mailbox ID and message ID.
3. Choose the attachment and run download.
4. Record destination artifact id, file name, size, MIME type, and hash.
5. If an agent needs local access, copy the artifact into the communication workspace path and continue from
   section 10.

Expected result:

1. Metadata and file bytes are available as a staged artifact.
2. The artifact can be reviewed before any outbound handoff.

If download is blocked:

1. Confirm attachment policies for MIME type, extension, and max bytes.
2. Confirm the target capability includes `read`.
3. Check for audit denial in section 7 or the host audit log.

### How-to: Approve An Outbound File Send

Use this when a send tool call is waiting for human sign-off.

Prerequisites:

1. Correct directory and session selected.
2. The pending send request is visible in Approvals.
3. Privileges unlocked for approval actions.

Steps:

1. Open **Approvals** and pick the request.
2. Read intended target, message body, destination target IDs, and `artifact_ids`.
3. Compare the request against session destination allowlists in section 5.
4. Check for policy limits on size, MIME, and extension.
5. Enter approver ID and a concrete reason.
6. Approve only if:
   1. target is correct,
   2. recipient is expected,
   3. artifacts are the right files.
7. Continue with the send tool using the same approval request id.
8. Re-open Tool Calls and confirm send status.

Expected result:

1. Approved sends move to execution.
2. Tool Call shows success, or a clear error with the exact failure reason.

If approval is repeatedly denied unexpectedly:

1. Check file-policy mismatch between approved request and actual send payload.
2. Ensure target capability is `write` for top-level send or `reply` for thread responses.
3. Re-request with corrected payload and re-approve.

### How-to: Troubleshoot A Missing Teams Or Discord Wake

Use this when you expect a wake and the agent does not resume.

Prerequisites:

1. Active session in the expected directory.
2. Target service and worker logs accessible.
3. Privileges unlocked for runtime checks.

Steps:

1. Check service health:
   1. Discord wake dispatch,
   2. Discord inbox intake,
   3. Teams inbound polling,
   4. workflow trigger routing.
2. Confirm session is not expired and not revoked in **Sessions**.
3. Confirm channel/chat allowlist still contains the exact target:
   1. Discord wake comes from read-capable `discord_channel_allowlist`,
   2. Teams wake through configured trigger/allowlist paths in the active workflow/session.
4. Confirm wake type:
   1. live command/message wake vs scheduled wake,
   2. one-time or recurring wake status in the session wake ledger.
5. Check whether the incoming message was captured at host side:
   1. use session message/query logs,
   2. confirm no provider-level allowlist or auth block.
6. Verify wake result in audit or Tool Calls:
   1. look for denied reasons before wake dispatch,
   2. look for policy mismatch or credential failure reasons.
7. For Teams, verify delegated auth/session provider settings were recently refreshed and signed in.
8. For Discord, confirm the route is not blocked by source/user allowlist mismatch.
9. Retry the wake source command/message once after fixing any config mistake.

If it still fails:

1. Review scheduled wake config and pending state in section 5.6 for one-time queue aging.
2. Check workflow trigger wiring for provider-neutral or provider-specific wake nodes.
3. Use section 20 for full end-to-end incident capture, including IDs and timeline evidence.

## 16. Configuration

This section helps you control what Evander is allowed to do and how it behaves for a selected directory.

Use this section before changing any setting that can affect message routing, approvals, wake signals, session launch, model routing, or host-visible UI behavior.
For session-level policy details, see section 5. For approval implications, see section 6. For message channels, see section 15.
For who can edit configuration, see section 17. For worker-level service impact, see section 18.

### 16.1 Configuration Domains

Think of configuration as grouped into domains that each control a different operational surface.

1. Policy controls common safety defaults such as session expiry and approval behavior.
2. Providers control integrations like OpenAI routing, NuGet, GitHub, Azure DevOps, Teams, and container worker settings.
3. Contacts and communication targets define who appears in provider actions and who is reachable.
4. Email covers mailbox definitions and mailbox runtime behavior.
5. Wake covers wake prompts used by scheduled and PR-based resume paths.
6. ADO worker settings control repository and feedback poll behavior for Azure DevOps completion and feedback streams.
7. Issue setup controls issue workflow defaults such as git host, git user, and model fallback behavior.
8. VM controls relay and network defaults and the VM template selection catalog.
9. PR template defines the standard markdown that can be prepended when automation writes pull requests.
10. Model pricing controls local usage estimate rates and labels used in usage reporting.
11. Admin Web branding controls browser title, header text, and logo presentation.

Add-on loader settings are not edited in the selected directory's Configuration page. Optional tabs
and add-on workflow provider blocks come from host `addon_loader` configuration and worker
configuration loaded when Admin Web, the trigger orchestrator, and the effect orchestrator start or
reload. If an add-on tab is missing, check host add-on configuration and restart or reload the
affected services after correcting it.

The system uses inherited values:

1. `root` provides the baseline.
2. Parent directories add overrides.
3. The selected directory can add its own override.
4. The live effective value is what the system and operators will actually use.

Root is the starting point for rollout, and subdirectories can tighten or change behavior for one team or product without affecting siblings.

Directory-scoped editing is only available when directory-scoped configuration is enabled in host settings. If that switch is off, this tab may still show values but editing will remain disabled.

### 16.2 Effective Values And Overrides

The most important operator question is: what value is actually used right now?

1. The Configuration page shows **effective values** first.
2. It also shows where values came from: local override versus parent defaults.
3. In many cases, each row has a raw override view so you can confirm the exact data being saved.
4. Some fields are inherited by default and remain editable only when you intentionally set a local override.

Use this pattern:

1. Open Effective view and confirm inherited values for context.
2. Compare against the directory override view for the live changes already stored here.
3. Make only the specific edits needed for your use case.
4. Save and verify that the effective value changed as expected.

For operator-safe troubleshooting, take both:

1. A screenshot of the effective view for evidence.
2. The override JSON copy for diffing with peer operators.

If something is rejected at save time, use the validation message as the first clue before changing a second field.

### 16.3 Safe Save Behavior

Evander uses revision checks to avoid silent overwrite.

1. A save can fail if another operator changed the same configuration in between your edit and save.
2. In that case, the UI asks you to reload and reapply your intended edits.
3. Communication or high-risk edits should be done one domain at a time.
4. Locking, unlocking, and privilege mode are still required for writes.
5. Keep local edits short and re-open read-only if a page feels stale.

Safe save habits:

1. Refresh after selecting a directory.
2. Open only one domain section at a time.
3. Edit the smallest possible set of fields.
4. Save.
5. Confirm the effective value in the read result.
6. If the save fails, reload first, then reapply.

If your team changes settings in parallel, avoid copying files from a stale local checkout. The UI and save path are designed to coordinate revisions and protect concurrent edits.

### 16.4 Teams Delegated Auth

Teams access in this deployment is delegated host-side auth.

1. Teams credentials are not placed into guest VMs.
2. Each directory can have its own delegated identity binding.
3. Child directories inherit binding from parent unless they define their own.
4. You can start/refresh/sign out auth from Configuration.
5. Teams ingest paths depend on these credentials for channel and chat reads/writes.

The expected operator flow is:

1. Keep directory and communication provider settings correct.
2. Check the Teams delegated auth status in Configuration.
3. Start device-code sign-in and complete the browser step.
4. Refresh status and verify it shows active.
5. If the app registration changed scopes, sign out and sign back in.

Use session allowlist and target settings from section 15 alongside this section to ensure directory policy and channel/user lists match the authenticated identity.

### 16.5 Model Pricing And Usage

Evander stores a local model pricing catalog for cost estimates used by usage queries.

1. Estimates are operational, not final bill data.
2. The estimates use the loaded catalog file, model name, and reported token metadata.
3. Missing or custom pricing data can make cost values stale or incomplete.
4. Billing backends may include regional or account adjustments that are not inferred here.

Operator guidance:

1. Keep the catalog current when provider rates change.
2. Prefer minimal edits to only the affected rules.
3. Confirm the active catalog path before editing.
4. Keep currency and rule format consistent.

For report workflow and history after edits, use the usage and report sections for follow-up checks.

### 16.6 Branding And Admin Web Settings

Branding changes are global host-side UI settings, not per-directory controls.

1. Browser title and shell header title.
2. Header logo size and logo assets.
3. Sign-in bar and company logo assets.
4. Optional relative logo paths through the built-in branding asset route.

Most branding changes are applied through `config/admin-web.env` values in the `AGENT_SANDBOX_ADMIN_WEB_*` family.

After changing these values:

1. Save the file.
2. Restart or reload `agent-sandbox-admin-web.service`.
3. Refresh browser tabs and confirm the header and sign-in branding.

If a deployment is exposed beyond localhost, confirm TLS and authentication policy as part of branding rollout.

### How-to: Update Directory Configuration Safely

Use this when you must change one directory-specific setting and keep blast radius small.

Prerequisites:

1. Correct directory selected.
2. Privileged operations unlocked.
3. You know whether the change is policy, provider, or communication related.

Steps:

1. Open **Configuration**.
2. Open the domain you need (policy, provider, wake, etc.).
3. Read **Effective config** and **Override JSON**.
4. Compare current parent values with directory override values.
5. Edit only the minimal fields needed for this task.
6. Save using the current config toolbar state.
7. If save reports a conflict, reopen the section and reload.
8. Recheck effective values and confirm the target behavior changed only in the selected directory.

Expected result:

1. The live effective value changes where intended.
2. No unrelated domain is changed.
3. A second operator can still continue with their own domain edits.

### How-to: Copy Effective Configuration For Support

Use this when support asks for live values during an incident.

Prerequisites:

1. Correct directory selected.
2. Read access to Configuration.
3. You can share operator evidence externally.

Steps:

1. Open **Configuration** and switch to the target directory.
2. Open the domain and copy the **effective config** view.
3. Copy the **directory override JSON** view immediately after.
4. Redact any host-only secret names, tokens, or local token names before sharing.
5. If needed, export status from:
   1. `evander-cli directories config-show --runtime-dir runtime --domain <domain> <directory>`
   2. `evander-cli status --runtime-dir runtime --json`
6. Send both a brief context note: operator action taken, issue id, and observed error/timeout.

Expected result:

1. Support can compare live effective values to overrides without guessing what is inherited.
2. You avoid exposing secrets while still sharing enough data to troubleshoot.

If output is still ambiguous, ask support for the same section from the same timestamp after a fresh page reload.

### How-to: Refresh Microsoft Teams Delegated Auth

Use this when Teams actions are not working, or after App ID/scope changes.

Prerequisites:

1. Directory provider binding is set in provider config.
2. Teams delegated auth is available in Configuration for that directory.
3. Access to the browser for device-code login.

Steps:

1. Open **Configuration** and confirm the target directory.
2. Open the Teams delegation area.
3. Read the current auth status; if `not signed in` or expired, use **Sign in** or **Start sign in**.
4. Complete the device-code flow in the browser tab.
5. Return and click **Refresh** to confirm signed-in status updates.
6. Run a narrow non-critical operator check that uses Teams (for example a low-impact communication target or inbound target check).
7. If scopes changed or status remains stale, use sign out and sign in again.
8. If UI flow is blocked, use CLI fallback:
   1. `evander-cli teams-auth status --directory <directory-path>`
   2. `evander-cli teams-auth login --directory <directory-path>`

Expected result:

1. Teams delegated status shows active for the intended identity.
2. Ingest and notification flows return to expected behavior without broad resets.

If auth repeatedly fails with tenant/client errors, check provider app registration and flow type in section 16.

### How-to: Adjust Model Pricing Data

Use this when the usage cost panel is showing outdated or suspicious model estimates.

Prerequisites:

1. Privileged configuration access.
2. Correct directory selected.
3. Agreement on whether to update full catalog or only a targeted rule.

Steps:

1. Open **Configuration**.
2. Go to the model pricing catalog editor.
3. Review active path and source (default file or overridden path).
4. Edit only the needed rules (rate values, rule match, currency label).
5. Run a quick validation pass in the editor before saving.
6. Save and resolve any stale-revision warning by reloading first.
7. Re-open a recent usage query to verify new estimate totals.

Expected result:

1. New rates are loaded and visible in pricing metadata.
2. Cost estimates move in line with new pricing values.

If no estimate changes appear immediately, confirm the same catalog path is still active for usage query runtime.

## 17. Users, Groups, And Permissions

This section covers the identity and access layer operators use every day inside **Users & Groups**.
Use section 2 for sign-in basics, section 13 when Team chat access needs to be reviewed in
team workflows, and section 16 when directory configuration or session-wide ownership boundaries are
part of your change.

### 17.1 User Records

A user record is the base entry for Admin Web sign-in and permission assignment.

User fields usually include:

1. login identifier (email-style ID),
2. display name,
3. local password (only when using local password sign-in),
4. legacy role,
5. linked contact,
6. group assignments,
7. direct workflow grants,
8. effective directory access.

How these apply:

1. In local password mode, a local password is required before the user can sign in. In Azure SSO mode,
   admin users usually log in through the external identity flow and do not depend on the local password
   field.
2. Legacy role values are kept for compatibility, but day-to-day access control is usually managed through
   groups and direct grants.
3. You can assign a directory-scoped contact so messages and reports can be routed to the right person.
4. Removing a user from the directory clears their record from that scope; a removed user loses
   authenticated access on the next Admin Web request.

### 17.2 Groups

Groups let you assign repeated permission sets once and reuse them across users.

Users & Groups has two modes:

1. **Users** mode at `/#users`,
2. **Groups** mode at `/#users/groups`.

For each group you can define:

1. `id`: short stable group key,
2. display name,
3. optional description,
4. selected permissions.

Use directory-scoped group assignments so a user can have one core role in root and narrower additions in a
subdirectory. Directory assignments inherit downward, so keep high-level access in parent directories and avoid
copying the same grant into many child folders.

Editing rules:

1. `groups.manage` permission is required to create and change reusable groups.
2. `users.manage` permission is required to edit user records, assign user groups, and set direct workflow grants.
3. Users without those permissions can still use other pages based on their effective rights, but cannot
   perform these access-management mutations.

### 17.3 Workflow Grants

Workflow grants control who can open and read workflow records, and who can edit workflow content.

Two grant types are exposed to operators:

1. read access,
2. write access.

Practical rule:

1. write grants include read capabilities.
2. You can grant workflow access directly on a user, or via a group.
3. Direct user grants are useful for one-off exceptions.

Guidance:

1. Use groups first for common patterns (for example all operators in a directory).
2. Use direct grants when a single user needs extra access for a short period.
3. If a direct grant is temporary, remove it after the work closes to avoid over-permissioned accounts.

### 17.4 Team Chat Visibility

Team chat visibility is separated from normal workflow, issue, and session permissions.

1. By default, operators can inspect their own Team Add-on chat stream.
2. `team_chats.read_all` adds a read-only inspection mode for other operators' chats.
3. `team_chats.read_all` does not grant send rights in those chats.
4. The UI marks read-only inspection mode explicitly to prevent accidental posting.

Use this with caution. Any audit trail or review workflow that depends on chat visibility should be logged and
checked with section 7 and section 13 before escalation.

### 17.5 Access Reviews

Access reviews keep directory access tight and reduce surprise permissions.

Run periodic checks for:

1. users who no longer work in the directory,
2. users with broad workflow write grants,
3. users in too many groups for their role,
4. direct grants that were meant to be temporary,
5. legacy role-only accounts that still need explicit groups or direct grants,
6. team members carrying broad `team_chats.read_all` outside operational windows.

Use this pattern before big workflow changes:

1. confirm the directory,
2. unlock once for the review edits,
3. compare grants versus active responsibilities,
4. remove stale grants first,
5. only then run save,
6. document why any high-risk grant remains.

### How-to: Create A New User

Use when onboarding a person who needs direct Admin Web access.

Prerequisites:

1. You are signed in with enough privilege to open **Users & Groups**.
2. Privileged operations are unlocked.
3. Correct directory is selected.

Steps:

1. Open **Users & Groups** and select **Users**.
2. Click **New user**.
3. Enter login identifier and optional display name.
4. Set a local password when local-password authentication is active.
5. Choose a legacy role if required by your admin policy.
6. Optionally attach a directory contact.
7. Add group assignments and/or direct workflow read/write grants.
8. Click **Save**.
9. Confirm:
   1. the user appears in the list,
   2. the user can sign in with expected method,
   3. permissions appear as expected in workflow and directory tests.

Expected result:

1. A usable user record exists.
2. The user can perform only actions covered by their effective permissions.

### How-to: Create A Permission Group

Use this when multiple users need the same access set.

Prerequisites:

1. `groups.manage` permission.
2. Privileges unlocked.
3. Correct directory selected.

Steps:

1. Open **Users & Groups** and switch to **Groups** (or open `/#users/groups`).
2. Click **New group**.
3. Enter:
   1. group ID,
   2. group name,
   3. optional description.
4. Select the permissions this group should grant.
5. Save the group.
6. Return to **Users** mode and assign the group to one or more users.
7. Re-open one user to verify the new effective permissions are visible.

Expected result:

1. The group is reusable and can be assigned to many users.
2. Revoking the group later updates all assigned users consistently.

### How-to: Grant Workflow Access

Use this for short-term or role-specific workflow read/write permissions.

Prerequisites:

1. `users.manage` permission.
2. Target user is in the selected directory list.

Steps:

1. Open **Users & Groups** in **Users** mode.
2. Find and open the target user.
3. Add direct workflow grants.
4. Choose read or write:
   1. read for viewing runs and definitions,
   2. write for editing workflow definitions and publishing updates.
5. Note that write access includes read access.
6. Save and keep a short reason in your change notes.
7. Validate by checking a read path and a write path that the user needs.

Expected result:

1. The user gains the intended workflow capability.
2. Existing group grants remain active and add to the final permission set.

### How-to: Remove A User Safely

Use this when a user leaves the team or no longer needs Admin Web access.

Prerequisites:

1. Privileges unlocked.
2. Correct directory selected.
3. You have a replacement for any active responsibility.

Steps:

1. Open **Users & Groups** and locate the user.
2. Verify active work:
   1. open recent sessions, approvals, and workflow actions tied to that user role,
   2. confirm no active handoff depends on the user,
   3. close pending work in section 6 if needed.
3. Reassign direct tasks, workflow ownership, and group-based responsibility before deletion.
4. Remove direct workflow grants if this user may be restored later as inactive.
5. Delete the user record.
6. Confirm with a colleague sign-in test that the user no longer has valid Admin Web access.
7. Check section 7 for a final audit record of the removal.

Expected result:

1. The user can no longer authenticate in that scope.
2. Directory access is reduced without deleting unrelated users or groups.
3. Remaining workflow and team ownership is explicit and assigned.

## 18. Workers And Background Services

This section helps operators understand and manage background workers in one place.
Workers are the always-on processes that keep automation moving when no one is actively
watching the screen.

Core control-plane services such as the MCP gateway, relays, and Admin Web are managed as host
systemd services. Check those with the Operations runbook, `systemctl`, and service logs rather
than expecting them to appear in the Workers page.

For live operator status in one screen, use section 3. For event-level workflow failures,
go to section 11. For recurring failure patterns and operational cost signals, use section
12. For inbound notification flow and communication channel behavior, use section 15. For safe
service setting changes and revision handling, use section 16.

### 18.1 What Workers Do

Workers are grouped by role:

1. Workflow automation processing
   - `agent-sandbox-trigger-orchestrator`
   - `agent-sandbox-effect-orchestrator`
2. Inbound and resume automation
   - `agent-sandbox-ado-worker`
   - `agent-sandbox-proxy-issue-sync`
   - `agent-sandbox-discord-ingest`
   - `agent-sandbox-discord-wake`
   - `agent-sandbox-scheduled-wake`
   - `agent-sandbox-teams-ingest`
   - `agent-sandbox-teams-meeting-ingest`

In many deployments, the exact set matches what is installed and enabled on the host.
If a worker is not active in this environment, it may still appear with a stopped state or not
show up at all.

Services such as `agent-sandbox-gateway.service`, `agent-sandbox-openai-relay.service`,
`agent-sandbox-nuget-relay.service`, `agent-sandbox-http-target-relay.service`, and
`agent-sandbox-admin-web.service` are not Workers-managed entries. Use host service checks for
start, stop, and log review for those units.

What to remember:

1. Stopping a wake or ingest worker stops background message-driven automation for that channel,
   but does not erase past automation state.
2. Stopping workflow orchestrators pauses scheduled automation runs and effect execution.
3. Restarting a worker can require short re-sync time while it reconnects to runtime queues.

### 18.2 Worker Status

The Workers view is the operator surface for checking worker-level health.

1. **Active state** tells you if the process is currently running.
2. **Sub-state** shows if the service is currently starting, running, or cleaning up.
3. **Result** gives the last known startup, exit, or failure state.
4. **Unit name** links the worker in the host service manager so you can jump to `systemctl`
   checks.
5. **Main PID** tells you which process is currently handling the worker.
6. **Settings** shows current worker-global config snapshot.
7. **Recent log/Journaling** is where you confirm startup errors, failed connections, and retry
   activity.

Read status as a health signal, not a full diagnosis:

1. Green running state with active workflow events usually means background tasks are healthy.
2. Repeatedly failing worker starts means dependency checks (tokens, relay access, or network) often need operator attention.
3. If state is stale, verify the page and host time are aligned before changing settings.

### 18.3 Start And Stop

Use start/stop only when needed.

1. Use start when a paused automation path needs to resume now.
2. Use stop when a channel is unhealthy, under maintenance, or before a targeted recovery step.
3. Do not stop workers while an incident review is in progress unless you are intentionally pausing
   automation to stabilize active activity.

If you stop one worker, check which dependent workflow or wake path uses it before continuing
with manual runbooks.

### 18.4 Worker Settings

Some workers expose global settings on the Workers screen. Typical use cases include queue timing
and provider behavior defaults.

1. Settings are host-level and should be changed carefully.
2. Save conflicts can happen if two operators edit the same worker at the same time.
3. A setting change may require a service refresh for effect to apply.
4. A worker can have no settings in this interface; in that case, settings fields will be empty.

Common setting impact pattern:

1. Change only one area at a time.
2. Save and confirm.
3. Re-check if the worker status changed cleanly.
4. If the action is still failing, follow section 16 safe save guidance and re-apply the change from
   a fresh settings baseline.

### 18.5 Service Installation And Health

For initial host rollout, use the normal install flow from the operations runbook:

1. Run install preflight checks before enabling or changing services.
2. Install or re-render units once checks pass.
3. Start required services from the host service manager.

Typical service bootstrap steps are:

1. `./scripts/install_systemd_units.sh --preflight`
2. `sudo ./scripts/install_systemd_units.sh`
3. `sudo systemctl start ...`

Broad checks are best done from two locations:

1. Admin Web dashboard for one-page operator signals.
2. Host status command for machine-readable health and dependency checks.

### How-to: Start Or Stop A Worker

When to use: Use this to restart or pause background automation on one service without changing global
configuration.

Prerequisites:

1. Correct directory is selected in Admin Web.
2. Privileged operations are unlocked.
3. You understand which automation path depends on this worker.

Steps:

1. Open **Workers**.
2. Select the worker from the left list.
3. Review the worker name, active state, and recent log.
4. Click **Start** to start, or **Stop** to pause.
5. Click **Refresh**.
6. Confirm:
   1. active state updates as expected,
   2. sub-state matches the new lifecycle,
   3. logs show startup or stop confirmation.
7. If stopping, verify dependent flow owners to avoid confusion (for example wake workflows, triggers, or
   orchestrator paths).

If this is not the intended worker effect: stop immediately and restart the correct worker, then run the
broad health check.

### How-to: Change A Worker Setting

When to use: Use this when timing windows, queue behavior, or a global worker default needs tuning.

Prerequisites:

1. Privileged operations are unlocked.
2. You have confirmed the worker exposes editable global settings.
3. You know the intended effect and rollback plan.

Steps:

1. Open **Workers**.
2. Select the worker.
3. Open **Global settings** and expand the field you need.
4. Make one change only.
5. Click **Save**.
6. If a revision warning appears:
   1. reload the setting view,
   2. re-check your intended value,
   3. save again.
7. Reload the worker or restart it if the interface indicates restart is needed.
8. Run **Check Whether Background Automation Is Running** to verify behavior after the change.

Expected result:

1. The worker keeps the new behavior with no unexpected side effects.
2. Other directories are unaffected by host-global worker setting changes unless the deployment explicitly
   uses directory overlays.

### How-to: Check Whether Background Automation Is Running

When to use: Use this when a response is delayed, an automation did not run, or you need a quick operational
health check before changing settings.

Steps:

1. Open **Dashboard** and check the worker summary.
2. Open the **Workers** tab for each worker shown as non-running or warning.
3. Open **Workflow** and verify current runs if automation is delayed.
4. Open linked **Tool Calls** or approvals for worker-owned paths with errors.
5. If a worker appears stopped, verify whether the associated service is intentionally disabled.

If automation is not moving:

1. Confirm `Dashboard` and `Workers` show the same worker state.
2. Confirm the corresponding service process is running in host service status.
3. Collect evidence in a short note before any start/stop or config changes.

### How-to: Run A Broad Health Check

When to use: Use this at the start of a shift, after deployments, or after maintenance.

Steps:

1. Open Admin Web and use **Dashboard** refresh.
2. Verify worker panel, workflow activity, approvals, and session health cards are populated.
3. Open **Workers** and confirm key services are active.
4. Run:
   1. `evander-cli status --runtime-dir runtime`
   2. `evander-cli status --runtime-dir runtime --json`
   The status output reports config load, runtime store access, required units, endpoint reachability,
   and active/expired session summary.
5. If needed for one service, run:
   1. `sudo systemctl status <worker-service>`
   2. `sudo journalctl -u <worker-service> -n 200`
6. If there is a mismatch, pause new changes and follow the incident path in section 21.

Expected result:

1. You have a short evidence list for each worker and can justify whether background automation is healthy.
2. Any stopped critical worker is flagged before operators lose visibility into workflows or approvals.

## 19. Assistant

The Assistant is an optional in-app support assistant available from Admin Web. It helps you ask for guidance, summarize operational context, and sometimes recommend next actions.

Use this feature for speed, not replacement. Any suggested action that changes workflow state, agents, approvals, sessions, issues, or system settings still needs your confirmation against the live Evander pages.

For broad incident workflows, use section 20 (Common End-To-End Tasks). For session, tool-call, and workflow evidence, see section 7 (Audit Trail And Tool Calls). For configuration and access behavior, see section 16 (Configuration).

### 19.1 Open The Assistant

The Assistant launcher is shown in the lower corner of Admin Web when assistant integration is enabled.

1. Open the lower-right assistant button or card.
2. If available, click it to expand the chat panel.
3. If needed, click **New chat** to reset the thread.
4. Use the minimize control to hide the panel while you continue operator tasks.

Possible states and what they mean:

1. **Hidden**: integration is not enabled for this deployment.
2. **Loading**: interface is still connecting to the assistant service.
3. **Disabled**: the backend currently blocks or cannot start new chats.
4. **Error**: request path is failing; use the troubleshooting guidance below.

### 19.2 Assistant Modes

The assistant displays its current mode clearly in the panel. Confirm the mode before asking it to do anything.

1. **Read only mode**
   1. Safe for investigation and explanation.
   2. It can summarize likely causes, read context, and suggest checks.
   3. It should not be relied on as a permission for direct system changes.
2. **Full access mode**
   1. Can request or prepare actions using your current directory context and available tools.
   2. Still use operator judgment before approving risky changes.
   3. Keep prompts specific and explicit about safety boundaries.

Before asking for direct changes, confirm:

1. You are in the correct directory for the issue.
2. The assistant mode is the one you expect.
3. You have unlocked privileges only if your workflow requires action (for example approving, saving, or editing).

### 19.3 Chat Practices

To get useful results, keep messages concise and context-rich.

1. State the scope first: directory, session ID, workflow/run ID, or issue number.
2. Describe the objective in one sentence before the details.
3. Mention what you already checked to avoid repeated investigation steps.
4. Ask for concrete next steps, not broad theory.
5. Use **Shift + Enter** for multiline input, and **Enter** to send.

Useful investigation pattern:

1. Give the assistant the symptom and affected scope.
2. Ask for probable causes ranked by likely probability.
3. Ask which page to check next.
4. Confirm each recommendation with sectioned evidence.

When the assistant suggests action:

1. Verify the recommendation against current context in section 3 (dashboard), section 7 (tool calls), or workflow pages.
2. Prefer one narrow verification step before moving to the next.
3. Keep separate investigations in separate chats (new thread).

### 19.4 Assistant Troubleshooting

If the launcher is missing, disabled, or failing:

1. Reload the page and wait briefly for reconnect.
2. Sign out and sign in again if the panel still does not appear.
3. Check whether the current deployment has assistant integration enabled.
4. Confirm you are on the page for the correct directory scope.
5. If it still fails, request an operator-admin check of chat session service logs and runtime service health.
6. Switch to manual workflows from section 3 (Dashboard and Daily Monitoring), section 7 (Audit Trail And Tool Calls), and section 16 (Configuration) while the assistant is unavailable.

### How-to: Ask The Assistant To Investigate A Problem

Use this for fast issue triage before a full manual investigation.

1. Open the assistant.
2. Start a new message and set the scope:
   1. directory
   2. affected session or VM
   3. workflow/run ID if known
   4. tool call ID when investigating a failing action
3. Describe the observed behavior and when it started.
4. Ask for likely root causes and the shortest verification path.
5. Ask for the exact pages to open next.
6. After receiving guidance, validate each step in Admin Web.

Expected result:

1. You get a short list of likely causes.
2. You get 1-3 concrete checks to run.
3. You now have a validated next step instead of guessing.

### How-to: Start A Fresh Assistant Thread

Use a new thread when one conversation grows too broad or mixes unrelated incidents.

1. Open the assistant panel.
2. Click **New chat**.
3. Enter a short label for the problem in your first message (for example: "workflow run investigation").
4. Keep messages in that thread focused to the same incident.
5. If a second topic is unrelated, start a second new thread.
6. Close the old thread only after you copy useful context notes if needed later.

Expected result:

1. New issue context stays separate.
2. The assistant response quality improves because prompts are focused.
3. You reduce accidental mixing of unrelated operational contexts.

## 20. Common End-To-End Tasks

This section brings together daily operator workflows that touch more than one Evander page. Use these recipes when a single feature page is not enough and you need a full incident or delivery flow.

Use section 20 as the bridge between:

1. Live operations and status in section 3.
2. Work execution and session control in sections 4, 5, and 8.
3. Safety gates in section 6 and evidence in section 7.
4. Workflow design and change control in section 11.
5. Team and operator coordination in sections 12, 15, and 17.

### 20.1 Investigate A Failed Automation

Use this when an external action was expected and did not complete, or when a run appears healthy in one tab but failed in another.

Execution order:

1. Confirm the directory and lock state from the shared header first.
2. Start from Dashboard warnings and identify:
   1. Which failure signal appeared (alert badge, missed action, stale card, or spike).
3. Open Workflow current runs and find the failing run row.
4. Read node-level failure cause in inspect mode.
5. Open the linked Tool Calls row for the same time window.
6. Open the related Session or Agent record to confirm who owned the action.
7. Check Approvals if the node required human confirmation.
8. Check Reports for trend confirmation before taking rollback action.
9. If applicable, open VMs for host/runtime state and then stop.

Decision rule:

1. If the issue is missing data or a transient timeout, trigger a narrow retry only after approval/timeouts are validated.
2. If repeated across runs, route to workflow diagnostics and temporary risk mitigation (pause trigger or narrow filters) before broad policy edits.

### 20.2 Create Work From A Local Issue

This path links intake, launch, and oversight for work that starts as a local request.

End-to-end flow:

1. Create or open an issue with scope, repo, and constraints.
2. Verify that the selected directory is correct.
3. Decide the launch mode:
   1. Start VM/session immediately from the issue, or
   2. Launch through later follow-up in case priority is uncertain.
4. Confirm bootstrap options are narrow and necessary.
5. Start the target.
6. Move to Agents or VMs immediately to verify target is running.
7. Open Sessions if policy or expiry constraints matter.
8. Keep the issue link visible in notes while the work is active.

Expected exit state:

1. Issue is linked to a live target.
2. Session and directory are correct.
3. You have a known check-in interval and a clear owner for the next escalation.

### 20.3 Prepare Agent Output For Human Review

Use this when agent outputs need to be consumed by someone outside the active run.

Recipe:

1. Open the relevant Agent or Session.
2. Confirm the work has reached a stable checkpoint (no active sensitive in-flight action).
3. Open Artifacts and download the relevant output bundle.
4. Capture logs for key steps in Tool Calls and Execution history.
5. Review verification markers in the run details (tests, status flags, and completion summary).
6. Note limitations:
   1. redactions in payloads,
   2. unresolved checks,
   3. any pending approvals.
7. Move to approval/review pages based on sensitivity.

Good review practice:

1. Always include exact IDs (issue, session, run, or artifact) in handover text.
2. Separate raw output from assessment notes.

### 20.4 Approve A Sensitive Action

Use this when the action path explicitly requires human intervention.

Flow:

1. Open Approvals and filter for the relevant directory and status.
2. Sort by most recent and capture one high-risk item.
3. Open the request and read:
   1. tool,
   2. requested effect,
   3. session/directory scope.
4. Review the redacted payload and the approval payload diagnostic.
5. Confirm the linked Tool Call and session context.
6. Enter approver identity and concise reason.
7. Approve or deny only if the chain is understood.
8. Re-open the linked Tool Call after the decision.
9. Watch for follow-up retries, expiry refresh, or mismatch messages.

If denied or stale:

1. Capture the reason in the handoff notes.
2. Re-run the specific step with corrected context.

### 20.5 Roll Out A Workflow Change

Use this path for small production-safe workflow edits.

Recommended sequence:

1. Open Workflow and pick the target graph.
2. Duplicate intent in a short note before editing.
3. Make the smallest possible edit (one node or one binding at a time).
4. Validate diagnostics and fix unresolved bindings.
5. Save and confirm revision update.
6. Watch Current runs for at least one expected healthy transition.
7. Open linked Runs/Tool Calls if side effects touch external tools.
8. If failure appears, immediately pause the trigger and use section 20.1 to trace.

Rollout rules:

1. Keep non-essential changes out of the same edit window.
2. Have a rollback plan:
   1. disable the trigger,
   2. restore previous graph revision from the page controls.
3. Capture report entry ID and before/after behavior in the operator note.

### 20.6 Handle An Operator Handover

Use this when ending a shift, covering incidents, or changing operators for a live directory.

Minimum handover package:

1. Directory and current lock state.
2. Active agents and sessions, including expiry and policy notes.
3. Pending approvals and any decision blockers.
4. Failed or retried workflow runs in the last few hours.
5. Worker health and any warning banners.
6. Open issues, active incidents, and Team tasks.
7. Artifact references and required follow-ups.

Suggested cadence:

1. Capture status from Dashboard first.
2. Confirm each item with linked detail pages.
3. Add a short "next action owner" line for each open item.
4. Share only non-sensitive IDs and results.
5. Re-lock UI access after handover is posted.

### How-to: Investigate A Failed Automation End To End

Use this when an expected external result failed and no single page explains why.

Prerequisites:

1. Correct directory selected in section 2.
2. Read access to Workflow, Tool Calls, Approvals, Sessions, Agents, and Reports.
3. Optional privilege unlock for any needed follow-up action.

Steps:

1. Open Dashboard and take a snapshot of current error signals.
2. Open Workflow current runs and copy the failing run ID.
3. Enter inspect mode for the failed run.
4. Record the first failed node and the node output or binding error.
5. Open linked Session and Agent rows from that run.
6. Open Tool Calls and filter to the same session, actor, and time window.
7. If the flow crossed an approval gate, open the related approval row and check expiry, digest, and mismatch flags.
8. If the action involved a target environment, open the VM and confirm target status.
9. Open Reports and check whether the failure is isolated or recurring.
10. Open a new handover note with:
    1. timeline,
    2. stable IDs,
    3. likely root cause category,
    4. required operator decision.

Expected result:

1. A clear chain from signal -> workflow run -> tool call -> approval/target state.
2. A single recommended next action, not multiple random retries.

Common recovery:

1. If approval payload mismatch appears, stop re-running from UI and request corrected approval.
2. If repeated missing target calls appear, halt workflow trigger and move to targeted diagnostics.

### How-to: Run A Human Review Cycle

Use this when work should be checked by a person before the result is accepted.

Prerequisites:

1. Target artifact and session references.
2. Access to Artifacts, Tool Calls, Approvals, and Sessions.
3. Clear ownership for final decision.

Steps:

1. Open the artifact list and download the latest relevant output package.
2. Capture the session and workflow run IDs from the run list.
3. Open Tool Calls for the run window and gather outcome flags.
4. Read the run verification summary and note missing validations.
5. If approvals were required, open the decision path and confirm status/identity.
6. Create a short review note with:
   1. what changed,
   2. what was validated,
   3. known risks,
   4. recommended disposition.
7. Choose review outcome:
   1. approve and proceed,
   2. deny with required changes,
   3. request additional run with clearer evidence.
8. Record decision outcome in handoff notes and link IDs.

Expected result:

1. The team can act from a single review record.
2. Review decisions are tied to concrete IDs and evidence, not memory.

### How-to: Perform A Shift Handover

Use this at shift boundaries or whenever one operator leaves active work running.

Prerequisites:

1. Shared directory and current operator context.
2. Dashboard and detail pages available in read mode.
3. Permission to add a handoff note or internal issue commentary.

Steps:

1. Open Dashboard and read:
   1. workers,
   2. active runs,
   3. approvals queue,
   4. recent warnings.
2. Confirm active directory, unlocked privileges, and any pending privilege-sensitive actions.
3. Open active sessions and mark those near expiry.
4. Open open approvals and identify blockers by session ID.
5. Open recent failed runs and note recurrence rate from Reports.
6. Open Team-related active work if configured, and verify outstanding deliverables.
7. Confirm artifact links needed by the next operator are copied.
8. Write a handover entry with:
   1. current state,
   2. top risks,
   3. owner suggestions,
   4. exact IDs for every open item.
9. Lock privileged operations once handover is complete.

Expected result:

1. Next operator receives enough context to continue without re-discovering evidence.
2. Open action items have owners and one clear next step each.

## 21. Troubleshooting

This section is for operator-facing diagnosis of common symptoms without assuming deep implementation knowledge. Use it as a quick path to the right evidence first, then follow the cross references for full repair steps.

Use section 20 (Common End-To-End Tasks) when a single symptom touches multiple pages.

If the UI does not match the behavior described here, treat the live UI and host state as authoritative and gather evidence before changing settings.

### 21.1 Empty Or Missing Pages

This usually means one of these is true:

1. The page is scoped to a different directory than the one you selected.
2. Your account currently has read-only mode or locked privileges.
3. A service used by that page is not running or not reachable.
4. A feature is not available for your deployment (for example add-on-specific pages).

Quick check:

1. Confirm the directory in the shared header.
2. Open the same area from the left navigation and wait for one refresh cycle.
3. Click **Refresh** on Dashboard or the target tab.
4. If still empty, open a detail tab for direct error text instead of staying on the summary card.
5. Confirm add-on visibility if the page is feature-specific.

Cross-check:

1. If Dashboard panels are missing, open section 3 procedures and then open the related tab.
2. If an Add-on page is missing, use section 13 (Team Add-On).

Expected result:

1. The page shows data or a clear backend message explaining why it is unavailable.
2. You know whether the next step is privilege, directory, or service recovery.

### 21.2 Disabled Buttons

Common causes are safety and permission controls, not UI defects.

1. Privilege lock is enabled for that session.
2. Your user role does not include the action.
3. You are viewing the wrong directory or a non-editable scope.
4. A form is incomplete or invalid.
5. The page state is stale while edits are in progress.

First pass:

1. Check privilege lock and unlock if required.
2. Verify directory and target record selection.
3. Look for inline field errors and required-field hints.
4. Perform a manual refresh.
5. Retry once, then check if a tooltip or status message appears.

If still blocked:

1. Review user permission paths in section 17 (Users, Groups, And Permissions).
2. Compare workflow editing expectations with section 11 (Workflows) if saving is blocked during graph edits.

### 21.3 Workflow Did Not Run

When automation is expected but no run is produced, treat the issue as staged and inspect three control points:

1. Trigger state and run start conditions.
2. Worker and orchestrator health.
3. Approval, tool-call, and target-side evidence.

Investigation order:

1. Open **Workflow** and confirm the trigger node is enabled.
2. Open **Current runs** and check whether a run started recently.
3. Open **Workers** and verify runtime processes are active (especially trigger/effect orchestration workers).
4. Open **Tool Calls** filtered by expected workflow or time window.
5. Open related **Approvals** if the path contains a human gate.
6. Open **Reports** to see whether the behavior is isolated.

If the trigger was disabled or queued:

1. Do not toggle unrelated settings.
2. Follow section 16 (Configuration) for worker and trigger-related config checks.
3. Re-run a narrow end-to-end trace from section 20.

### 21.4 Agent Did Not Respond

This section is for agents that look idle while action was recently expected.

1. Confirm session is not expired, paused, or revoked.
2. Confirm the selected directory and agent are correct.
3. Verify that the target (VM or container worker) is in expected state.
4. Check wake behavior if this is a scheduled resume case.
5. Review latest tool calls for model connectivity and blocked approvals.

Operational sequence:

1. Open **Agents** and **Refresh** target state.
2. Open the selected agent and check recent logs and steering state.
3. Open **Sessions** for expiry and approval requirements.
4. Open **Scheduled wakes** (if configured) and check dispatch state.
5. Open **Tool Calls** for failed or pending calls on the same session.

If no useful data appears in Agent logs:

1. Open **Workflow** for any node waiting on this session or dispatch ID.
2. Review relay-like symptom pages through the operator support process in section 19 (Assistant) only for guidance, not as a substitute for evidence.
3. Prepare escalation notes if the gap remains.

### 21.5 Approval Problems

Approval issues are usually due to queue state, policy boundaries, or payload matching.

1. Request stays pending longer than expected.
2. Approval appears expired or automatically denied.
3. Payload mismatch is shown during retry.
4. Requester received no notification.

Use this path:

1. Open **Approvals** and confirm filter by directory, session, and status.
2. Open the request and review summary, intended effect, and reason fields.
3. Open related **Tool Calls** and compare the linked IDs.
4. If payload mismatch is shown, request a corrected request flow rather than repeated retries.
5. If notifications fail, verify communication targets in section 15 (Communication And Notifications).

### 21.6 Configuration Save Problems

Most save failures are controlled by one of three conditions:

1. Validation errors in required fields.
2. Revision conflict because another operator updated the same page.
3. Directory-level inheritance or denied overwrite path.

Recovery flow:

1. Note the exact field and error message.
2. If the page says "changed while editing," reload, compare, then reapply minimal edits.
3. Confirm all required values are present and valid.
4. Retry after the targeted reload, not after broad unrelated changes.
5. Save again only when diagnostics are clear.

If the page still fails:

1. Capture a screenshot and validation summary.
2. Check section 16 (Configuration) for lock, revision, and inheritance behavior.

### 21.7 Team Add-On Problems

Team workflows can appear disconnected from operator views when state or wake pathways are not aligned.

Typical symptoms:

1. Teams tab is missing.
2. Team state appears empty.
3. Agents do not wake from team messages.
4. Review or promotion steps do not complete.
5. Team Chat is not visible where expected.
6. Deliverables are in stand-down state.

Action sequence:

1. Confirm Team Add-on scope and directory scope are correct.
2. Verify expected Team permissions and operator role in section 17.
3. Open section 13 (Team Add-On) and follow the specific Team visibility and wake checks there.
4. Cross-check worker-level context in section 18 (Workers And Background Services).
5. Confirm issue links and communication targets are configured for the same directory.

If only one directory is affected:

1. Compare two recent actions in two different directories to isolate deployment versus directory config.
2. Use the handover/review flow only with exact Team IDs and run/session references.

### 21.8 When To Escalate

Escalate when one page shows a result but related pages disagree, when data is missing across multiple sections, or when behavior is reproducible after basic checks.

Send a support packet with:

1. Current date/time and timezone.
2. Directory, operator user, and browser session context.
3. Relevant IDs:
   1. Session ID
   2. Agent ID
   3. Workflow run ID
   4. Tool call ID
   5. Approval ID
   6. Worker name
   7. Issue or wake request ID if used
4. Exact symptom timeline (when first seen, expected behavior, actual behavior).
5. Screenshots of:
   1. Dashboard
   2. The failing detail page
   3. Any error state banner.
6. Steps already performed and result from each step.
7. Last known change in operators' workflow, permissions, or target config.

If the incident touches multiple systems or production workloads, include a single-line impact statement and preferred recovery window.

### How-to: Recover From An Empty Page

Use this when one or more pages are blank or show no rows after navigation.

Prerequisites:

1. Read access to the target page.
2. Shared header showing current directory.
3. Access to both Dashboard and the related section page.

Steps:

1. Confirm the directory matches the scope of the missing page.
2. Refresh the current page and, if needed, the whole browser tab.
3. Click **Refresh** in Dashboard, then open the linked detail page.
4. Open the same page via deep link hash route.
5. Compare with a known working directory.
6. Check for feature scope (for example Add-on only pages).
7. Re-check section availability after unlock/lock state change.

Expected result:

1. The page populates correctly or shows a clear service-level or permission message.
2. You can name one next section to inspect (Dashboard, Workers, or section-specific page).

Common recovery paths:

1. If only one directory is blank, check that directory access.
2. If only one browser session is blank, log out, log in again, and retry.
3. If still empty, prepare escalation per 21.8.

### How-to: Find Why A Button Is Disabled

Use this when a control looks disabled and you need the reason quickly.

Prerequisites:

1. Operator account with that page visible.
2. Section loaded in the correct directory.

Steps:

1. Check the privilege lock icon/button in the header and unlock if allowed.
2. Check whether your role has the required permission for that section.
3. Select a valid item first (many actions target a row or session).
4. Resolve any form validation errors in required fields.
5. Try once after a manual refresh.
6. Check if the action target is outside the selected directory.
7. Check if the action belongs to a disabled global state (for example trigger disabled, workflow not editable).

Expected result:

1. Either the button becomes active or the exact prerequisite is identified.
2. You have a minimal corrective step, not broad configuration changes.

Common recovery paths:

1. Re-open the related section and confirm save prerequisites.
2. Follow section 11 if edits do not commit due to graph validation.
3. Log and escalate if no prerequisite change resolves the state.

### How-to: Diagnose A Workflow That Did Not Fire

Use this when a workflow should have started but no automation run appears.

Prerequisites:

1. Directory and privileges confirmed.
2. Access to Workflow, Tool Calls, Workers, and Approvals pages.

Steps:

1. Open **Workflow** and confirm the target trigger is enabled.
2. Open **Current runs** and capture the expected run window.
3. Open **Workers** and ensure trigger/effect workers are healthy.
4. If no run exists, open **Tool Calls** and filter by query/window.
5. If run exists with an error, open inspect mode and capture first failing node.
6. Open **Approvals** only if the graph includes required gate steps.
7. If it is now running elsewhere, check time-zone drift and scheduling expectations in wake/cron definitions.

Expected result:

1. A root cause category is established: trigger off, worker unhealthy, approval block, or missed scheduling.
2. Next action is limited to one repair path.

Common recovery paths:

1. Correct only the minimum trigger/worker issue.
2. Retry through section 20.1 if a narrow reproduce window exists.

### How-to: Diagnose A Silent Agent

Use this when no response appears in recent agent activity despite expected action.

Prerequisites:

1. Access to Agents and Sessions.
2. Target agent/session IDs available.

Steps:

1. Open **Agents** and refresh the selected agent.
2. Check if target status is active and associated with the expected directory.
3. Open the latest log lines and steering state.
4. Open **Sessions** and verify expiry, allowed tools, and approval requirements.
5. Open **Scheduled wakes** and check dispatch status if this was a resume.
6. Open **Tool Calls** for blocked/failed requests on the same session.
7. Confirm related Approvals for payload mismatch or stale approvals.
8. If no change appears in the same page, open a small reproduction attempt in section 20.

Expected result:

1. You identify whether this is policy gating, target inactivity, wake issue, or tool-call/approval blocking.
2. You can choose either a direct retry or a controlled escalation.

Common recovery paths:

1. Correct session or wake gating state in-place.
2. Avoid duplicate prompts while state is unknown.

### How-to: Prepare A Support Escalation

Use this when troubleshooting stalls after the basic checks above.

Prerequisites:

1. You have completed the relevant checks in sections 21.1 to 21.7.
2. You can capture screenshots.
3. You can copy IDs from the related records.

Steps:

1. Take one screenshot per page:
   1. Dashboard
   2. Failing detail page
   3. Related relation targets (for example Tool Calls, Approvals, Sessions, Workflow).
2. Record these IDs exactly as shown:
   1. Directory
   2. Session ID
   3. Agent or VM ID
   4. Workflow run ID
   5. Tool call ID
   6. Approval ID
   7. Worker name
   8. Issue/wake request ID if applicable
3. Write a short time line with:
   1. First observed time
   2. Last successful state
   3. Reproduction attempt steps and result
4. List the checks already done and what changed.
5. Include recent configuration or permission changes.
6. Add the current impact statement (who is blocked and why).
7. Open a single escalation record rather than multiple partial notes.

Expected result:

1. Support receives a complete, reproducible packet.
2. The first round of triage is faster and focused.
3. Operators spend less time repeating basic checks.

## 22. Reference

This section is the quick lookup surface for common operator terms, page locations, status
signals, and safe checklists when you are already in day-to-day work.

Use it when you want to move fast without re-reading the full manual.

### 22.1 Glossary

| Term | What it means for operators | Related section |
|---|---|---|
| Directory | Your current operational scope (tenant, team, project, or environment branch) used for all list filtering and edits. | Section 2, Section 16 |
| Session | The active contract that lets an agent continue work in a target. | Section 5 |
| Agent | The running control-surface object for a workload (for example target status, tools, logs, wake state). | Section 4 |
| Worker | Background host service that runs orchestration, triggers, wakes, or integrations. | Section 18 |
| Workflow | An automation graph: trigger, gate, query, effect; plus run history and status. | Section 11 |
| Tool Call | A logged record of what was requested, allowed, denied, or errored. | Section 7 |
| Approval | A human safety gate for sensitive actions. | Section 6 |
| Wake | A scheduled or on-demand resume action that prompts agent continuation. | Section 5 |
| Add-on | Optional feature surface that adds pages and domain behavior (for example Team, Bokio, Skatteverket). | Section 13 and `docs/addons.md` |
| Review request | Team quality checkpoint before release movement. | Section 13 |
| Deliverable | Work output package prepared for review and possible release. | Section 13 |
| Release package | A controlled publish step that moves reviewed work toward canonical output. | Section 13 |
| Privilege lock | A safety mode in the header that blocks most changes until unlocked. | Section 2 |

### 22.2 Page Map

| Page | Purpose | Common tasks |
|---|---|---|
| Dashboard | Live health overview and first troubleshooting triage surface. | Shift start check, warning follow-up, quick context scan. |
| Agents | Target inventory, policy context, session links, and basic target control. | Find active agent, check recent logs, pause/resume steering. |
| Sessions | Session scope, expiry, allowed tools, file and wake settings. | Extend/revoke sessions, review allowed tools, inspect wake schedules. |
| VMs | VM lifecycle control for libvirt-based targets. | Inspect power and session linkage, stop/shutdown/destroy after checks. |
| Issues | Local intake queue and issue-to-workflow startup bridge. | Create/edit issue, start issue-based VM, review source/owner. |
| Approvals | Human decisions waiting for or already made. | Review requests, approve/deny with reason, check queue age. |
| Tool Calls | Audit trail for actions and decision outcomes. | Trace who/what ran, see allowed vs denied vs error paths. |
| Workflow | Graph editor and run inspection. | Edit graph nodes/edges, inspect failed runs, verify effect behavior. |
| Reports | Trend and summary reporting for automation health. | Check failure clusters, PR preflight, conformance snapshots. |
| Configuration | Directory settings and effective policy. | Edit execution/model/provider/user-facing settings safely. |
| Artifacts | Output bundles and upload references used for evidence. | Resolve artifact keys, download published outputs. |
| Users & Groups | Operator access and workflow grant control. | Add users, create groups, review permissions. |
| MCP Tools | Directory tool deny/allow and default approval behavior. | Configure MCP tool limits and approval defaults. |
For optional add-on mode, this map also includes:

| Add-on Page | Purpose | Common tasks |
|---|---|---|
| Teams (`/addons/team-addon/`) | Team planning, reviews, and review-to-release workflow. | Open People/Cases/Review & Delivery, check stand-downs and wake causes. |
| Bokio / Skatteverket pages | Dedicated accounting or bookkeeping workflows when enabled. | Run domain actions that are available only in the add-on scope. |

### 22.3 Permission Summary

Permission is easiest to think of in layers.

- **Read mode (default safe mode):** view-only for almost all pages. Good for investigation.
- **Unlocked mode:** allows create, edit, and lifecycle actions on the current surface if your user has permission.
- **Workflow write access:** required for graph edits, and includes read access.
- **Approval/decision rights:** required for queue action and direct review decisions.
- **Team read-all chat access:** only for explicit operators and only for read-only team-chat review.

Common operator permissions:

| Permission area | Gives you | Safe default use |
|---|---|---|
| `users.manage` | User and grant edits in Users & Groups. | Needed when onboarding/offboarding access. |
| `groups.manage` | Group creation and group membership management. | Use for broad role templates instead of many direct grants. |
| Workflow grants (read/write) | Access to workflow views and workflow editing. | Write grants should be limited to required graph ownership. |
| Workflow/section privileges + unlock | Mutations in Sessions, Agents, Workers, Configuration, VMs, and reviews. | Keep unlocked only while taking action. |

For the add-on sections, treat permissions as scoped by directory and add-on visibility.

### 22.4 Status Meanings

Use this to avoid changing state on a false signal.

#### Sessions

- **Active**: dispatch and wakes can continue.
- **Expired**: time limit reached; normal dispatch is blocked.
- **Revoked**: manually stopped for future access.
- **Deleted**: session record is removed from local history.

#### Workers

- **running**: service is active.
- **starting/stopping**: short transition states; check next refresh before action.
- **failed**: service reported an error and may need host/service check.
- **stopped**: intentional stop or not started.
- **missing page/state**: page may not include this worker for your deployment.

#### Approvals

- **Pending**: waiting for human decision.
- **Approved**: queued request is cleared for the approved action path.
- **Denied**: action will not continue on that request.
- **Expired**: request timeout or invalid/too-old approval window.
- **Failed**: queue/action path could not complete cleanly.

#### Workflow runs

- **running**: execution is in progress.
- **completed**: run reached its end without current hard fault.
- **failed**: at least one node could not complete.
- **error**: run or node ended in an execution fault.
- **dismissed/cleared**: record removed after manual cleanup; evidence for this run remains elsewhere.

#### Team reviews

- **pending**: waiting for the assigned reviewer.
- **in_progress**: work is actively being reviewed.
- **accepted**: review approved; can move toward deliverable readiness.
- **rejected**: needs correction before forward progress.
- **blocked**: dependent states prevent continuation.

#### Releases

- **requested**: release path started.
- **in_progress**: release branch/publish sequence is running.
- **published**: release package reached expected final branch/output.
- **failed**: one or more release checks did not pass.
- **blocked**: missing task/review prereqs or policy gate.

#### Cleanup

- **stopped**: execution ended and cleanup is stable.
- **failed**: run ended with incomplete execution.
- **abandoned**: cleanup process did not finish automatically.
- **cleanup_failed**: explicit cleanup attempt did not complete.
- **cleaned_up**: worker-side cleanup done and host manifest is stable for review.

### 22.5 Default URLs And Deep Links

Base entry for local Admin UI:

- Admin Web base: `http://127.0.0.1:8100/`

Primary direct routes:

| Area | Deep link |
|---|---|
| Dashboard | `/#` |
| Agents | `/#agents` |
| Sessions | `/#sessions` |
| VMs | `/#vms` |
| Issues | `/#issues` |
| Approvals | `/#approvals` |
| Tool Calls | `/#tool-calls` |
| Workflow | `/#workflow` |
| Reports | `/#reports` |
| Configuration | `/#configuration` |
| Artifacts | `/#artifacts` |
| Skills | `/#skills` |
| MCP Tools | `/#mcp-tools` |
| Users | `/#users` |
| User Groups | `/#users/groups` |
| Workers | `/#workers` |

Optional add-on routes (enabled only where add-on modules are loaded):

- Teams: `/addons/team-addon/`
- Bokio: `/addons/bokio-addon/`
- Skatteverket: `/addons/skatteverket-addon/`

### 22.6 Operator Checklists

#### Daily Monitoring Checklist

1. Open Dashboard and refresh.
2. Verify worker set health and major warning cards.
3. Open Approval queue and check pending age.
4. Open current workflow runs for at least one active graph.
5. Open Tool Calls if any warning appears in sessions, workflows, or approvals.
6. Open Version/Changelog in Dashboard and check release alignment.
7. Save two short notes: "green", "watch", or "investigate".

#### Pre-Change Checklist

1. Confirm directory scope.
2. Confirm your lock state is unlocked only if you are ready to save.
3. Confirm the target has the minimum permission for this action.
4. Check dependent records first:
   - session or agent status,
   - approvals or wake impact,
   - worker health,
   - related tool/graph dependencies.
5. Read the current status on related pages from the quick map:
   - Dashboard for overall impact,
   - Tool Calls for recent side effects,
   - Reports if similar failures are recurring.
6. Prepare rollback plan:
   - what to undo,
   - what evidence to keep,
   - who to notify.
7. Make only the smallest change needed.
8. Save, then immediately confirm changed state through linked records.

#### Approval Review Checklist

1. Open Approvals with narrow filters for session or tool.
2. Verify status and scope timestamps.
3. Read request summary and directory context.
4. Confirm decision history and related audit link.
5. Apply decision with short reason.
6. Confirm request status changed and Tool Call outcome matches intent.

#### Incident Response Checklist

1. Capture exact page, directory, and time.
2. Record top-level symptom status and first affected record IDs.
3. Jump from summary page to the linked detail record.
4. Confirm no conflicting records across Dashboard, Workers/Workflow, and Tool Calls.
5. Hold additional mutations until the chain is clear.

#### Release Review Checklist

1. Confirm deliverable is complete and artifact set is present.
2. Confirm all required review requests are accepted.
3. Confirm no blocked release prerequisites remain.
4. Confirm project/release roles are valid for the request.
5. Request release and watch package publication state until final.
6. If blocked, stop and document why before retry.

#### Operator Handover Checklist

1. Directory and lock state.
2. Active sessions and privileged actions in progress.
3. Open approvals and pending queue.
4. Current worker or workflow warnings.
5. Open incidents and incident notes.
6. Last meaningful artifact or run output for next operator.

### 22.7 Further Reading

For deeper setup and safety details, use:

- `docs/operations.md` for host setup, bootstrap flow, and service installation.
- `docs/admin-ui-user-manual.md` for detailed page behavior.
- `docs/configuration-reference.md` for parameter and domain behavior.
- `docs/addons.md` for Team, Bokio, and Skatteverket enablement.
- `docs/live-smoke-tests.md` for post-change safety checks.
- `docs/container-worker-backend.md` when container worker execution is in scope.
- `docs/execution-backends.md` for backend selection and target behavior.
- `docs/libvirt-vm-template-operations.md` for VM template and boot-time dependencies.

### How-to: Look Up A Status Quickly

Use this when you only have a status term and need fast interpretation.

1. Copy the exact status text from the page.
2. Open the source page from the glossary area in this section:
   - session status -> Sessions,
   - worker status -> Workers,
   - approval status -> Approvals,
   - run status -> Workflow,
   - review/release status -> Teams page.
3. Confirm the record scope (directory and date/time).
4. Open the record detail and read one linked path (for example session, tool call, or workflow run).
5. Do not act on a stale or filtered status without checking the linked detail row.

Decision rule:

1. If status is healthy but action needed, continue.
2. If status is ambiguous, gather one linked ID and re-check linked records.
3. If status remains suspicious, route to the incident checklist.

### How-to: Use The Page Map To Find A Task

1. Name the task outcome in one sentence, for example:
   - "I need to review queued approvals."
2. Use the page map in 22.2 to pick the right starting page.
3. Open that page first and apply the smallest needed filter:
   - directory, session id, actor, or time window.
4. If missing records appear, open the linked detail page from the card.
5. If still unclear, jump to Dashboard then back to the chosen page.

Expected result:

1. You are on the right page for the intended action.
2. You have at least one supporting ID (session, request, run, or artifact key).

### How-to: Run A Pre-Change Checklist

Use this before changing workflow settings, approvals, sessions, workers, or add-on state.

1. Confirm scope and lock state:
   - correct directory,
   - unlock if change is required.
2. Confirm affected record status:
   - session state, approval queue, and worker health.
3. Confirm no dependent action is running:
   - open linked session/agent/tool-call/approvals as needed.
4. Reduce changes to one area only.
5. Prepare rollback note:
   - what field changed,
   - previous value,
   - owner to notify if rollback is needed.
6. Save once and re-open the changed record immediately.
7. Confirm expected delta:
   - status changed,
   - no unintended related record drift.
8. If behavior is different than expected:
   - stop further edits,
   - save the state snapshot,
   - continue with incident response.
