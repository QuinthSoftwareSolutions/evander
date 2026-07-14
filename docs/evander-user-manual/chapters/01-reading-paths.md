# 1. How to Read This Manual

> **At a glance.** This manual is both a guided tour and a technical reference. Read Chapters 1–3 for the governing ideas, follow the numbered chapters for a complete system model, or jump from a symptom to the runbook and source trail in the relevant chapter. Examples use one fictional organization throughout so that the machinery remains concrete without being mistaken for a built-in deployment.

## The two useful speeds

Evander is easier to understand when it is read at two speeds.

At overview speed, keep four ideas in mind:

1. The host is the control plane. It owns policy, credentials, workflow state, managed Git, and durable evidence.
2. An agent works in an isolated execution environment. It receives a bounded session, not the host's ambient authority.
3. Narrow relays and the MCP gateway mediate useful access. Network reachability alone never grants a capability.
4. Workflows connect durable events to gates, queries, and effects. The Team Add-on builds organization and governance behavior on those same core primitives.

At reference speed, use the mechanics, invariants, failure tables, commands, and source trails in each chapter. A claim such as “a directory policy is inherited” is only the overview. The reference account also says which values merge, which replace, which restrictions cannot be relaxed by a child, where the files live, and what happens when validation fails.

The opening **At a glance** paragraph and the first section of every chapter are written for overview speed. The remainder is designed to survive close technical use.

## Choose a reading path

The chapters are ordered so that later mechanisms have a foundation, but not every reader needs the same first pass.

| If you are… | Read first | Then use |
|---|---|---|
| New to Evander | Chapters 2–5 | The execution, Git, relay, and operations chapters |
| Operating a host | Chapters 4–8 | The Admin Web, observability, backup, and troubleshooting chapters |
| Designing workflows | Chapters 2, 5, and 8 | The workflow authoring, provider, and run-inspection chapters |
| Reviewing security | Chapters 3, 6, and 7 | Execution-backend, managed-Git, relay, secret, and audit details |
| Building with the Team Add-on | Chapters 2 and 8 | The Team concepts, task, wake, review, governance, and release chapters |
| Debugging a live incident | The symptom's chapter runbook | Its failure table and source trail, then the observability chapter |

For a broad understanding, read the story in Chapter 2 before studying individual services. It gives names to the records that will recur later: directory, session, workflow run, event, approval, execution, Git commit, and audit entry.

## The continuous Northstar example

The manual follows a synthetic team named `northstar-platform` and its `mission-control` project. A work item asks the team to add telemetry retry behavior. Mira manages the team, Priya leads the project, Imani implements the change, Rowan reviews it, and Riley handles the release. These five named characters are AI Team agents throughout the manual. Human actions are attributed generically to **an operator** or, for interactive chat and voice, **a signed-in user**.

Core chapters use this example without assuming the Team Add-on is installed. For example, “Imani's session” means an ordinary core session associated with the work. Team chapters then explain how the add-on records Imani's role, assigns the task, schedules wakeups, governs review, and coordinates Riley's release action.

Unless a passage explicitly says otherwise, Northstar names and payloads are illustrative. They are not default accounts, reserved identifiers, a shipped workflow, or a promise that every installation has Azure DevOps, Teams, or a container worker configured. The example's directory path is:

```text
root/northstar-platform/mission-control
```

The path matters because configuration, RBAC scope, provider selection, sessions, workflows, and Team records all use directory context to prevent one project from silently inheriting another project's authority.

## How to read the illustrations

Architecture and sequence figures deliberately omit incidental implementation detail so the ownership or transition remains legible; the surrounding text states the full contract. UI screenshots come from the checked-in production front end running against a fixed, sanitized Northstar fixture—not from a live operator runtime. They use dark mode, frozen timestamps, fictional identities, and non-secret data. Treat visible counts, IDs, build labels, and states as explanatory examples, then inspect the equivalent live API/store when operating a deployment.

## How statements are qualified

The manual uses a small set of deliberate terms:

- **Must** describes an enforced invariant or a required operator step. The text should name the enforcement point.
- **Should** describes the supported and safest operating practice, even when the code cannot enforce every part of it.
- **May** describes an optional capability, often dependent on directory configuration or an installed add-on.
- **Default** means the value in the current source or example configuration. It is not proof that a live deployment still uses that value.
- **Representative flow** means a technically supported composition, not a workflow installed automatically.

Paths are relative to the repository unless they begin with `/`. Commands assume the repository root and an installed `evander` package; development commands may use `./.venv/bin/evander-cli` where that distinction matters. Runtime examples use `runtime`, while packaged defaults may resolve to `/var/lib/evander`. Always follow the `--runtime-dir` selected by the running service.

Checked-in files ending in `.example`, `.example.json`, or `.env.example` are templates. They are not live secrets or proof of live settings. In particular, checked-in workflow packs can be examples or built-ins, while an operator's active pack can be ignored by Git and managed through Admin Web.

## Reading a mechanism from intent to evidence

Most technical sections follow the same chain:

| Question | Where the answer appears |
|---|---|
| What problem does this solve? | Mental model or opening explanation |
| What record carries the decision? | Mechanics and data-flow sections |
| What cannot be bypassed? | Invariants |
| What happens after a crash or bad input? | Failure behavior |
| How do I inspect or recover it? | Practical runbook |
| Where is the behavior enforced? | Source trail |

That chain prevents a common documentation error: describing the happy-path user interface while omitting the durable state and rejection behavior underneath it. If Admin Web says an approval is pending, for example, the reference question is not merely which button to press. It is also which session and exact payload the request binds, when it expires, how use is consumed, and what the audit record contains.

## Correctness and the live source of truth

This manual describes the repository revision that contains it. For a live host, use the deployed checkout, runtime database or JSON store, effective directory configuration, service environment, logs, and process state as the final source of truth. A local example file cannot overrule a live revision; a stale browser screen cannot overrule a durable execution record.

When a broad statement and a detailed statement appear to conflict, prefer the narrower one with an enforcement point. When the manual and current behavior appear to conflict:

1. Confirm that the command, service, and browser are pointed at the same runtime directory.
2. Inspect effective configuration, not only the root or child overlay.
3. Check the deployed code revision and active service unit.
4. Reproduce with the focused test named in the source trail.
5. Treat the mismatch as either a software defect or a documentation defect; do not invent an undocumented exception.

## Failure behavior of the documentation model

| Misreading | Consequence | Correction |
|---|---|---|
| Treating an example as active configuration | An operator diagnoses the wrong provider, port, or workflow. | Read effective live configuration and service environment. |
| Treating a visible tool as permission to use any payload | A call is denied by argument policy or approval binding. | Follow the complete policy order in Chapters 6 and 7. |
| Treating a queued event as a completed effect | Work appears “lost” although it is waiting, running, or completion-pending. | Trace the event and execution records in Chapter 8. |
| Treating an add-on concept as a core primitive | Team task state is confused with a core workflow event or session. | Follow the ownership boundary stated in the Team chapters. |
| Editing a live workflow pack as a plain file | Concurrent edits are overwritten or a stale revision is installed. | Use Admin Web's revision and lock behavior. |
| Looking only at a branch name | The reviewed or released commit is misidentified. | Follow the pinned-SHA and lease rules in the Git and release chapters. |

## Practical runbook

When arriving at an unfamiliar Evander host, establish context before making changes:

```bash
evander-cli status --runtime-dir runtime --json
evander-cli directories list --runtime-dir runtime
evander-cli list-sessions --runtime-dir runtime --all
git rev-parse --show-toplevel
git rev-parse HEAD
git status --short
```

Then choose the record closest to the symptom:

- An agent cannot see or call a tool: start with the session, effective policy, MCP binding, and audit trail.
- A workflow did not advance: start with its trigger event, graph revision, queue record, and node execution.
- A VM or worker job did not start: start with the session's selected backend and backend execution record.
- A Git result is missing: start with the managed repository ID, session authorization, pushed SHA, and promotion lease.
- A Team task did not wake: start with Team state and its outbox/wake evidence, then cross into the core workflow run.

Non-mutating observation is the safest first move, but commands that assemble the runtime—including `evander-cli status` and some directory commands—can bootstrap or migrate missing runtime configuration. Run them as the intended operator against the intended runtime, and back up an unfamiliar runtime first. JSON and SQLite files are durable implementation surfaces, not general edit APIs. Use the CLI or Admin Web mutation path so validation, locking, revision checks, audit, and related state transitions remain intact.

## Source trail

- `README.md` and `AGENTS.md` — repository purpose, current surfaces, supported operating flows, and maintenance conventions.
- `docs/architecture.md`, `docs/operations.md`, and `docs/configuration-reference.md` — core architecture, operator procedures, and configuration fields.
- `src/evander/admin/composition.py` and `src/evander/runtime.py` — current runtime assembly and shared services.
- `src/evander/models.py` — canonical session, approval, event, and other durable record models.
- `tests/test_systemd_docs.py`, `tests/test_container_worker_docs.py`, and `tests/test_workflow_run_inspector_demo_docs.py` — examples of documentation claims kept executable through tests.
