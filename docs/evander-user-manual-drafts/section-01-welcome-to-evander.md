# 1. Welcome To Evander

Evander is the operator console and automation control plane for supervised agent work.
It gives operators one place to watch active work, create and steer agent sessions, review sensitive actions, and recover from failures. Evander helps coordinate what runs in agents while keeping a human in the loop for decisions.

This manual section is for day-to-day operators, team leads, administrators, and support staff. It does not assume familiarity with internal service names or runtime internals.

## 1.1 What Evander Helps You Do

Evander is best used when you need to supervise or recover work that uses one of these core surfaces:

- **Monitor active work:** quickly see the current state of workers, sessions, approvals, and recent activity.
- **Create or resume agent work:** open or restart work sessions without hand-building infrastructure each time.
- **Review approvals and safety gates:** inspect and decide on actions that need human approval.
- **Inspect audit evidence:** trace tool calls, workflow runs, and communication records.
- **Coordinate people, directories, and policies:** keep work separated by team or scope.
- **Respond to common failures:** identify why a run paused, why an approval stalled, and where recovery should start.

Evander is also where you use operational pages for issues, sessions, workflows, users, configuration, communication targets, and worker health.
For first-time onboarding and navigation basics, continue to **Getting Started**.

## 1.2 Important Concepts In Plain Language

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

## 1.3 Operator Responsibilities

Even with automation, operators remain accountable for safety and correctness:

1. **Approve high-risk actions.** Use the approval flow for tool actions that can alter external systems, run privileged operations, or publish artifacts.
2. **Verify outcomes before trust.** Always validate summaries, artifacts, and logs before taking results as final.
3. **Manage access and scope.** Keep user roles, directory assignments, and session lifetimes current.
4. **Review incidents and warnings.** A missing panel, denied action, or stalled run is a signal to investigate and document before proceeding.
5. **Maintain external dependencies.** Credentials, service health, and integration targets should be updated outside the session flow if needed.
6. **Own handoff quality.** Capture context when handing off between shifts or teams.

Evander supports this responsibility model, but it does not replace operator judgment. For account control and directory edits, keep working through **Getting Started**, **Users, Groups, And Permissions**, and **Configuration**.

## 1.4 Safety And Trust Model

Evander is designed around trust-safe supervision:

- **Host-held secrets:** model keys and other secrets remain on the host control plane.
- **Scoped access:** agents and tools operate within explicit session and directory boundaries.
- **Approval gates:** sensitive actions can require human approval before execution.
- **Policy enforcement before action:** allowed tools, policy rules, and communication limits are enforced before work proceeds.
- **Exact action matching:** approved requests are checked against the requested action details.
- **Auditability by default:** approvals, tool calls, and execution decisions are recorded so operators can reconstruct what happened later.
- **Privilege lock for safety:** many mutation actions are intentionally separated from read-only actions.

Evander lowers risk, but operators should still review unusual output, confirm approvals, and track incidents in the dedicated incident sections.

## How-to: Decide Whether Evander Is The Right Surface For A Task

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
