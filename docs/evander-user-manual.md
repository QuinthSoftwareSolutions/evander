---
pagetitle: Evander Technical Manual — Core Platform and Team Add-on
subtitle: Core Platform and Team Add-on
date: 2026-07-13
lang: en
---

# Evander Technical Manual

## Core Platform and Team Add-on

<div class="manual-edition">

**Edition 1.0** · Evander 0.3.1 · verified against `518b15bafef1` · 2026-07-13

Current implementation is normative. Chapters explicitly labelled as design history are non-normative and describe how the present system evolved.

</div>

This manual explains the complete core Evander control plane and the Team Add-on from first principles through architecture, operation, failure recovery, and engineering extension. Readers can follow it front to back or use the glossary and subject index as a technical reference.

## Contents

- [Part I: Orientation](#part-i-orientation)
  - [1. How to Read This Manual](#1-how-to-read-this-manual)
  - [2. One Work Item, End to End](#2-one-work-item-end-to-end)
  - [3. Trust Model and Security Boundaries](#3-trust-model-and-security-boundaries)
- [Part II: Core Evander Architecture](#part-ii-core-evander-architecture)
  - [4. System Topology, Services, and Ports](#4-system-topology-services-and-ports)
  - [5. Runtime Assembly, Configuration, and Directories](#5-runtime-assembly-configuration-and-directories)
  - [6. Identity, Sessions, RBAC, and Policy](#6-identity-sessions-rbac-and-policy)
  - [7. MCP, Approvals, and Audit](#7-mcp-approvals-and-audit)
  - [8. Workflow Runtime and Orchestration](#8-workflow-runtime-and-orchestration)
  - [9. Execution Backends](#9-execution-backends)
  - [10. Networking, Relays, Packages, and Secrets](#10-networking-relays-packages-and-secrets)
  - [11. Managed Git, Artifacts, and Code Consultations](#11-managed-git-artifacts-and-code-consultations)
  - [12. Issues and Provider Integrations](#12-issues-and-provider-integrations)
  - [13. Communications, Meetings, Calls, and Wakes](#13-communications-meetings-calls-and-wakes)
  - [14. Semantic Memory, Model Usage, Skills, and Add-ons](#14-semantic-memory-model-usage-skills-and-add-ons)
  - [15. Admin Operations, Observability, Backup, and Recovery](#15-admin-operations-observability-backup-and-recovery)
- [Part III: Team Add-on](#part-iii-team-add-on)
  - [16. Team Add-on Boundary and Domain Model](#16-team-add-on-boundary-and-domain-model)
  - [17. Enabling and Operating the Team Add-on](#17-enabling-and-operating-the-team-add-on)
  - [18. Organization, Projects, Tasks, and Cases](#18-organization-projects-tasks-and-cases)
  - [19. Commands, Identity, Revisions, and the Durable Outbox](#19-commands-identity-revisions-and-the-durable-outbox)
  - [20. Wakes, Leases, Context Rotation, and Stand-down](#20-wakes-leases-context-rotation-and-stand-down)
  - [21. Chat, Agent Mail, Voice, and Escalation](#21-chat-agent-mail-voice-and-escalation)
  - [22. Deliverables, Reviews, and Decision Cascades](#22-deliverables-reviews-and-decision-cascades)
  - [23. Ephemeral Delegation](#23-ephemeral-delegation)
  - [24. Team Git Promotion and Release Gates](#24-team-git-promotion-and-release-gates)
  - [25. Governance, Memory, Cost, and Internals](#25-governance-memory-cost-and-internals)
- [Part IV: Engineering and Evolution](#part-iv-engineering-and-evolution)
  - [26. Engineering and Extension Guide](#26-engineering-and-extension-guide)
  - [27. Design History and Compatibility Boundaries](#27-design-history-and-compatibility-boundaries)
- [Part V: Reference](#part-v-reference)
  - [28. System Reference](#28-system-reference)
  - [29. Troubleshooting Decision Tables](#29-troubleshooting-decision-tables)
  - [30. Glossary](#30-glossary)
  - [31. Subject Index](#31-subject-index)

<div class="page-break"></div>

# Part I: Orientation

Build a trustworthy mental model before introducing implementation detail.

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


# 2. One Work Item, End to End

> **At a glance.** A useful Evander run is a chain of durable, bounded decisions: an external or internal trigger becomes an event; a compiled workflow routes it; effects create an expiring session and isolated execution target; the agent reaches models, tools, packages, and Git only through host-controlled surfaces; reviews and publication operate on pinned commits; and the host retains enough workflow, approval, audit, and cleanup evidence to explain the outcome.

## A representative mission

Northstar's `mission-control` project receives a work item: **add retry behavior to telemetry delivery**. An operator has defined a workflow for this class of work. Imani will implement it, Rowan will review it, and Riley will handle release. The Team Add-on can coordinate those agent roles, but the machinery in this chapter is core Evander.

No single built-in workflow is implied. The repository includes providers and example packs that can compose this path—ticket triggers, filters, deduplication, agent creation, managed-repository cloning, prompts, Git promotion, and pull-request creation. An operator selects the providers and exact graph appropriate to the installation.

The simplest mental model is a relay race. Each stage hands the next stage a durable identifier instead of a vague assertion that work happened:

```text
work item -> trigger event -> workflow run -> session -> execution
          -> tool/audit records -> Git SHA -> review -> publication -> cleanup evidence
```

The identifiers are what make the run recoverable and explainable.

![Evander system context from operator intent through the host control plane to isolated execution and external services](assets/evander-user-manual/diagrams/01-system-context.svg)

<p class="figure-caption">Figure 2.1 — The end-to-end path crosses explicit host boundaries; no arrow represents general guest access.</p>

## Before the event: define the boundary

Northstar's project lives at `root/northstar-platform/mission-control`. The operator has already:

- registered the source repository as an Evander-managed repository and obtained a `managed_repo_id`;
- configured provider, policy, VM or worker, and automation settings at root or along the project directory ancestry;
- created or selected a workflow graph whose directory is the Northstar project;
- configured any external trigger credential as a host secret, referenced by environment-variable name rather than copied into the workflow;
- selected the tools, argument constraints, communication targets, and approval behavior appropriate to an implementation session.

This setup answers three questions before compute is created: *whose work is this, which configuration applies, and what authority may the future session receive?*

## Stage 1: a trigger becomes a durable event

Suppose an Azure DevOps or ticket provider observes the telemetry-retry work item. The trigger orchestrator polls enabled trigger definitions, resolves the graph's directory-scoped provider settings, and asks the provider for candidate events. A provider event carries a stable event identity, source identity, and payload; the queue deduplicates that event ID and also supports an explicit idempotency key where the publisher supplies one.

Only after durable publication does the trigger worker call the provider's acknowledgement hook and advance its cursor. This order prevents an ordinary crash between “seen” and “queued” from silently discarding the work. It does not mean a provider can never redeliver; stable event identity, optional queue idempotency keys, and downstream node execution keys make redelivery converge at the orchestration boundary.

A successful publish wakes the effect orchestrator. The default store is SQLite at `runtime/orchestration.db`; a legacy JSON backend remains available as a rollback path. The event now exists independently of either worker process.

## Stage 2: the graph narrows intent

The compiled graph routes the event along edges from its source node. A realistic Northstar path may use:

- `gate:filter` to accept only the relevant project, type, state, or label;
- `gate:dedupe` to collapse a repeated start request;
- a query such as `query:session:list` to discover an existing active session;
- `effect:agent:create` only when no suitable session exists.

Queries read and return data to later nodes. Gates transform, branch, delay, iterate, merge, serialize, or reject flow. Effects change the world. Each node receives resolved configuration and a graph-aware input payload. Expressions such as `${{node.output}}` are validated and resolved against upstream outputs rather than being arbitrary host code.

The graph is compiled and validated before it becomes the current runtime configuration. Missing endpoints, duplicate node IDs, invalid provider schemas, unsafe cycles, and invalid bindings are rejected. If a later hot reload is invalid, the workers retain their last-known-good configuration and log the failure.

## Stage 3: create an expiring session

`effect:agent:create` creates a session manifest and prepares the selected execution backend. The manifest is Imani's agent capability envelope for this run. It includes:

- a unique session ID and expiry;
- `root/northstar-platform/mission-control` as its directory;
- a VM or worker binding and selected model, where applicable;
- the MCP tools it may request and which require per-invocation approval;
- allowlists for contacts, scripts, channels, mailboxes, files, and search where enabled;
- argument-policy and quota settings;
- metadata that correlates the session with the workflow run, work item, agent role, and Git intent.

The manifest contains authority descriptions, not general host credentials. Provider keys remain on the host. An expired or unknown session is denied even if an old client still knows its ID.

The backend then creates compute. With `libvirt_vm`, that is normally a disposable VM overlay. With `container_worker_vm`, the control plane asks the authenticated worker daemon to prepare durable session directories and later run disposable Docker jobs. Chapter 9 compares their precise lifecycle and isolation semantics.

## Stage 4: transport code without smuggling authority

The workflow passes `managed_repo_id`, not an arbitrary host checkout path, to the clone effect. The managed Git service resolves the registered bare repository and provisions scoped SSH access for the session. The guest or worker container clones into its own workspace.

Imani's execution environment can read the repository and, when explicitly granted, push only through the restricted Git path and permitted agent namespace. The supported flow separates:

1. a session's work branch;
2. the exact commit SHA produced by that session;
3. a workflow decision to pin and promote that SHA;
4. a later push or pull-request effect guarded by an explicit lease.

Branch names are convenient labels; the commit SHA is the evidence. The host never has to serialize a source tree through MCP, and the agent never needs the host operator's unrestricted Git identity.

## Stage 5: the agent works through narrow host surfaces

Inside its VM or container, Imani's agent sees an ordinary development environment, but its useful external paths are mediated:

- model requests go to the OpenAI-compatible host relay;
- MCP calls go to the gateway at `/mcp` and are bound to the session;
- NuGet, npm audit, PyPI/npm cache, or apt cache paths use the configured package surfaces;
- allowlisted external HTTP environments use the session-scoped HTTP target relay;
- source changes move through restricted Git over SSH.

General guest or container internet access is not the default. The host retains real model and provider secrets. The HTTP target relay is deliberately different from a secret-injecting proxy: it constrains the target, while forwarding authentication headers or cookies supplied by the guest session; it does not place a host credential into the request.

For MCP, the session ID is only the start. The connection must present the session proof token, its MCP transport becomes bound to the session, `tools/list` is filtered, and every invocation is checked against effective directory policy, session grants, argument rules, approval state, and workflow-scoped leases.

## Stage 6: pause safely for human approval

Assume the implementation needs one tool call that Northstar policy marks approval-required. The first exact invocation does not execute. It creates or reuses a pending approval request containing a digest of the unredacted payload, a redacted review payload, a human summary, the session, directory, tool, intended effect, expiry, and grant settings.

An authorized operator reviews it. A decision records the approver and a reason. If approved, Imani retries with the returned request ID and the same payload. The gateway rejects a changed payload under the default exact-payload mode, a request for another session or tool, an expired or denied request, and a grant whose use count is exhausted. Consumption is atomic.

Approval is not a blanket elevation. The directory may still deny the tool, argument policy may still reject a value, and the session must still be active. Conversely, approval is not required for every mutation by an immutable global rule: operators configure it per policy and session, while the two built-in destructive tools `delete_ado_backlog_item` and `semantic_delete_points` are always exact-payload, one-use approval operations.

## Stage 7: complete, review, and publish a pinned result

When the prompt effect finishes, its completion event carries structured outputs back into the graph. A workflow can inspect the session context, copy artifacts, update issue state, await a Git push event, or enqueue another prompt. It can then pin the pushed SHA, promote an agent branch, create a pull request, and route new review feedback back to the same session.

Rowan reviews the commit that the workflow identified—not merely “whatever is now at the branch tip.” If changes are requested, another durable event can enqueue a bounded follow-up prompt. Once review and configured gates pass, Riley's publication path receives the approved SHA and a lease that describes what may be pushed or released.

The Team Add-on adds a richer organizational record around this sequence: Mira's team, Priya's project, Imani's task, Rowan's review assignment, Riley's release authority, one durable agent-inbox collection, command outbox events, wake scheduling, and governance checks. It does not replace the core session, workflow, policy, execution, approval, or managed-Git controls described here.

## Stage 8: retain evidence and clean up compute

The effect orchestrator journals node executions with event, trace, node, type, status, resolved configuration hash, inputs, outputs, errors, binding trace, and configuration revision. MCP calls produce append-only JSONL audit events and a queryable SQLite index. Approval decisions and uses remain linked by request ID. Session and execution records preserve the selected backend and cleanup result.

Destroying disposable compute is not the same as erasing the evidence. A VM disk or worker session directory may be removed while the host retains the session, workflow, Git, audit, and cleanup records according to retention policy. A `cleanup_failed` result should remain visible until the leaked resource is actually reconciled.

## End-to-end invariants

The story depends on a handful of rules that hold across subsystems:

- Directory context is chosen before provider, policy, and execution settings are resolved.
- External observation is acknowledged only after durable event publication.
- Session identity, transport proof, tool visibility, argument policy, and approval are separate checks.
- A network-open port does not confer a tool, target, repository, or secret.
- Workflow node idempotency prevents a completed node from being casually rerun, but external effects must still tolerate at-least-once recovery around crashes.
- Code provenance is a managed repository plus an exact commit, not an MCP payload or a mutable branch label.
- Disposable compute may disappear; durable host evidence must still explain the run.

## Failure behavior

| Where the story stops | Likely durable evidence | Meaning |
|---|---|---|
| Trigger sees nothing | Provider cursor and trigger logs | Provider credentials, filter, directory settings, or polling are wrong. |
| Event exists but graph does not advance | Queue and workflow journal | The graph has no matching edge, a gate dropped the item, or execution failed. |
| Agent is not created | Effect execution record | Backend configuration, capacity, image/template, or provider validation failed. |
| Clone is denied | Session, managed-repo registry, Git authorization log | The repository ID, session scope, key, or allowed ref is wrong. |
| Tool is absent | Session manifest and visibility explanation | It is not granted, is directory-denied, or lacks required structural context. |
| Tool returns an approval request | Approval store and tool-call audit | This is a controlled pause, not a gateway outage. |
| Prompt completed but downstream is quiet | Effect record with completion state | Completion publication may be pending or its port has no edge. |
| Review targets the wrong code | Git event, pinned SHA, promotion record | A mutable ref was used where the exact trigger SHA was required. |
| Compute remains after completion | Backend cleanup record | Cleanup is incomplete; preserve and reconcile the failure evidence. |

## Practical runbook

Start with the broad stores, then follow IDs rather than timestamps alone:

```bash
evander-cli status --runtime-dir runtime --json
evander-cli list-sessions --runtime-dir runtime --all
evander-cli list-approvals --runtime-dir runtime
systemctl status evander-trigger-orchestrator.service
systemctl status evander-effect-orchestrator.service
```

For the Northstar run, record these values together: directory path, source item ID, trigger event ID, trace ID, workflow run ID, configuration revision, session ID, backend execution or VM ID, approval request IDs, managed repository ID, pushed SHA, and cleanup result.

Do not “unstick” a run by deleting queue, journal, approval, or session files. First determine whether the item is pending, running, completion-pending, failed, skipped, exhausted, expired, or already completed under the same idempotency key. Use the supported Admin Web controls or CLI after that diagnosis.

## Source trail

- `src/evander/daemons/trigger_orchestrator.py` — trigger polling, durable publication, acknowledgement, cursors, and worker wakeup.
- `src/evander/daemons/effect_orchestrator.py` — graph routing, idempotent execution, completion events, journals, and recovery behavior.
- `src/evander/models.py`, `src/evander/policy.py`, and `src/evander/gateway/` — session capability, policy evaluation, MCP binding, approval, and audit mechanics.
- `src/evander/effects/providers/agent_create.py`, `agent_clone_repo.py`, `agent_prompt.py`, and `git_promote_agent_branch.py` — representative agent and Git lifecycle effects.
- `src/evander/admin/services/managed_git_service.py` and `docs/git-remotes.md` — managed repository, restricted SSH, pinning, promotion, and publication contracts.
- `config/workflow-packs/agent-container-worker.v1-example.json` — a non-default example composition containing the node families used in this story.
- `tests/test_trigger_orchestrator.py`, `tests/test_effect_orchestrator.py`, `tests/test_gateway.py`, and `tests/test_managed_git_workflow_contract.py` — focused end-to-end invariants.


# 3. Trust Model and Security Boundaries

> **At a glance.** Evander assumes that agent-generated actions and isolated workloads are not entitled to the host's ambient authority. The host keeps secrets and policy, the workload receives an expiring session, and every useful crossing—models, MCP tools, packages, external HTTP, Git, and worker dispatch—uses a narrow surface with its own authentication and scope. Isolation reduces the blast radius; it does not replace policy, approval, provenance, or audit.

## Start with zones, not processes

The security model is clearer when the system is divided into trust zones:

| Zone | What it may hold | What it must not assume |
|---|---|---|
| Operator plane | Human identity, RBAC, privileged Admin Web session | That a browser login grants an agent capability |
| Host control plane | Real provider secrets, policy, sessions, workflow state, managed Git, audit | That data from a guest, webhook, model, or provider is trustworthy |
| Isolated execution | Source workspace, bounded session configuration, temporary credentials or proof tokens | General internet, host filesystem access, or ambient provider credentials |
| Worker VM | Worker bearer-token verifier, Docker runtime, durable session directories | Authority beyond jobs accepted from its configured control plane |
| External systems | Models, Azure DevOps, Git hosts, communication and HTTP targets, package sources | That a request claiming to be “the agent” is sufficient without the relevant relay/provider credential |

The default execution boundary is a libvirt VM. The additive container path places Docker jobs on a dedicated worker VM; the VM is still the stronger outer boundary. Containers and VMs do not have identical isolation properties, which is why Chapter 9 states their separate lifecycle and sandbox contracts.

![Evander trust zones and the narrow crossings between them](assets/evander-user-manual/diagrams/02-trust-zones.svg)

<p class="figure-caption">Figure 3.1 — Authority stays in its zone and crosses only through authenticated, scoped surfaces.</p>

## The authority chain

An action is permitted by an intersection, not by one master switch:

```text
directory policy
  AND active session grant
  AND authenticated/bound transport
  AND argument constraints
  AND provider/target/repository scope
  AND approval or workflow lease when required
```

Removing any term denies or prevents the action. This is intentional defense in depth. If Imani's Northstar session can connect to port 8000 but `send_email` is not visible, the socket does not upgrade the session. If the tool is visible but its recipient violates the directory and session allowlists, the call is rejected. If the payload is allowed but approval-required, execution pauses. If an approved payload changes, exact binding rejects the retry.

The human plane is separate. An operator's Admin Web permission to edit Northstar workflows does not become Imani's MCP permission. Conversely, a session granted a tool cannot edit users or unlock privileged Admin Web operations.

## Default-deny workload networking

Guests and worker containers do not receive general internet access by default. The host firewall admits explicit guest-to-host ports needed for controlled services. Those firewall openings are routing prerequisites, not capability grants.

The standard guest-facing paths are:

- SSH for restricted Git and controlled host/guest administration;
- the MCP gateway;
- model, NuGet, HTTP-target, and npm-audit relays;
- optional operator-deployed package caches.

The container worker defaults to Docker network mode `none`. A different mode is valid only when the worker deployment explicitly enables its egress policy and supplies an allowlist. The worker backend is not a general network escape hatch.

This posture changes how dependencies are supplied. Base VMs remain small; host-side bootstraps seed optional toolchains. Package traffic uses configured relays or caches. Large immutable worker toolchains belong in runner images or read-only cache mounts rather than being downloaded from arbitrary sites during a prompt.

## Secrets remain host-owned

Live secrets belong in host environment files, a secret manager feeding those environments, or other host-owned provider configuration. Directory JSON and workflow packs normally refer to an environment-variable name such as a PAT or token variable; they should not contain the secret value.

The OpenAI relay demonstrates the pattern: the guest is configured with a host relay URL and a placeholder API key suitable for client compatibility. The host relay authenticates upstream with the real credential and keeps the upstream client alive for the full streaming response. The guest never needs the real key.

Not every relay injects a host credential. The HTTP target relay is session-scoped and checks an allowlisted base URL, but forwards authentication headers and cookies supplied by the guest request. Its guarantee is target confinement, not secret substitution. Treating it as a host credential broker would be a serious design error.

Secrets can still leak through payloads, logs, prompts, source files, or provider responses if an operator places them there. Redaction reduces accidental disclosure in approval and audit views; it is not retroactive secret revocation. Rotate a credential whenever exposure is plausible.

## Session capability and MCP proof

A session manifest ties a workload to a directory, expiry, execution identity, allowed tools, approval behavior, allowlists, argument policies, quotas, and correlation metadata. It is the durable capability description.

MCP adds proof and binding:

1. A session-specific proof token is generated when the session is prepared. Session MCP-auth metadata retains the issued token for guest provisioning together with its SHA-256 verifier, so that metadata is secret-bearing and must not be exposed through ordinary operator payloads.
2. The client declares a session in the connection query or `x-evander-session-id` header and presents the proof token through a supported header or query field.
3. Once the MCP transport receives its `mcp-session-id`, Evander persists the transport-to-session binding in `runtime/mcp-transport-bindings.json`.
4. A later attempt to reuse that transport for a conflicting Evander session is rejected.
5. `tools/list` and tool invocation are filtered or denied for missing, invalid, or expired session context.

Passing a session ID as a tool argument is retained only as a compatibility shape; the gateway binds the authoritative session from the connection and rejects a conflict. Session identifiers are correlators, not bearer secrets.

## Tools, targets, and arguments

Tool policy has several independent dimensions:

- **Visibility:** whether the tool appears in the session's MCP catalog.
- **Directory denial:** an ancestor can mark a tool denied; a child cannot undo that deny.
- **Session grant:** the manifest or a valid workflow-scoped lease must grant it.
- **Argument policy:** required, fixed, pattern, enum, allowlist, and denylist rules constrain payload fields. Inherited and session rules both apply.
- **Resource allowlists:** contacts, scripts, channels, users, mailboxes, folders, files, search, and HTTP targets narrow provider-specific access.
- **Approval:** selected calls require a human decision bound to the request.

This lets Northstar grant Imani a useful tool without granting its most general form. A script tool can be restricted to named scripts; a communication tool can be restricted to configured target IDs; an HTTP tool can select only a session metadata alias whose base URL the host already accepted.

## Git is the code boundary

Evander moves code through Git, not through MCP file payloads. The host registers or refreshes an upstream into an Evander-owned bare repository. A session receives a managed repository ID; the control plane resolves the internal path and provisions a scoped SSH key. The restricted SSH wrapper limits permitted operations and refs.

Agent writes belong under `refs/heads/agents/<session-id>/<topic>`. Workflows consume the exact SHA from the triggering push, pin it, and promote or publish it with explicit lease checks. This prevents a time-of-check/time-of-use swap in which a mutable branch advances after review.

The managed repository and ephemeral host worktrees live beneath the runtime root. Exposing a trusted host checkout path to an agent would bypass the ownership, cleanup, and provenance model.

## Provider and worker boundaries

Provider adapters run on the host. They resolve settings for the selected directory and use host credentials to call Azure DevOps, communication systems, or other integrations. Provider output remains untrusted workflow input: schemas, gates, and bindings validate its shape before effects consume it.

The container worker API is authenticated with a bearer token retained by the control plane. Jobs receive a sanitized execution specification and explicit mounts/configuration. The protocol intentionally lacks general secret transport and arbitrary host-path transport. A compromised worker remains serious—it sees its session workspaces and can attack its own VM—but it should not automatically inherit unrelated control-plane secrets or repositories.

## Approval is a boundary, not absolution

An approval request includes a digest of the real payload while presenting a redacted copy for review. Exact-payload mode, expiry, use count, session, and tool checks prevent casual reuse. Operator decisions require identity and reason and enter the audit trail.

Approval does not prove the requested action is wise or the displayed summary is complete. The reviewer must inspect intended effect, relevant redacted fields, session, directory, tool, and surrounding workflow context. Highly sensitive content should be represented by safe references rather than hidden behind so much redaction that the decision becomes meaningless.

Workflow-scoped MCP leases are similarly narrow. They can add a tool for one session and workflow run, with expiry and usage limits. They cannot override a directory deny or eliminate a tool's per-invocation approval requirement.

## Failure behavior and containment

| Failure or attack | Expected boundary response |
|---|---|
| Guest calls the public model endpoint directly | Default networking blocks it; the supported client points at the host relay. |
| Stolen session ID without proof token | MCP authentication fails; the ID alone is insufficient. |
| One MCP transport declares a second session | Persistent binding detects the conflict and rejects it. |
| Child directory tries to re-enable an ancestor-denied tool | Effective policy re-applies the inherited deny. |
| Approved invocation changes an argument | Exact payload digest check rejects it unless policy explicitly chose a broader mode. |
| Agent pushes outside its namespace | Restricted SSH/ref enforcement rejects the write. |
| Workflow pack contains a secret value | Validation cannot make that safe; remove it, rotate the secret, and replace it with an env reference. |
| Audit SQLite indexing fails | Append-only JSONL remains the canonical record; the index can be rebuilt. |
| Worker cleanup fails | Host records `cleanup_failed`; operators retain evidence and retry cleanup. |
| Host relay is compromised | The relay's upstream credential is exposed; isolate the host and rotate it. Workload isolation does not contain a host compromise. |

## Practical security runbook

For an unexpected capability, work from outside inward:

1. Identify the runtime directory and session ID.
2. Confirm the session is active and bound to the expected directory and execution target.
3. Inspect effective directory policy and provenance, not just the child overlay.
4. Inspect the session's allowed tools and argument/resource allowlists.
5. Inspect MCP transport binding, approval or lease IDs, and the matching tool-call audit record.
6. Verify the relay, Git wrapper, or provider enforced its own target scope.

Useful first checks include:

```bash
evander-cli status --runtime-dir runtime --json
evander-cli list-sessions --runtime-dir runtime --all
evander-cli directories config-show root/northstar-platform/mission-control \
  --domain policy --runtime-dir runtime
sudo nft list ruleset
sudo ss -ltnp
```

If a secret may have crossed a boundary, stop the affected session or service, preserve relevant logs and audit records, rotate the credential at its authority source, update the host secret, and then restart/reissue the smallest affected scope. Deleting a redacted UI record does not invalidate a credential.

## Source trail

- `docs/architecture.md`, `docs/execution-backends.md`, `docs/container-worker-backend.md`, and `docs/git-remotes.md` — supported trust and transport boundaries.
- `config/firewall.env.example`, `scripts/apply_firewall.sh`, and `scripts/git_ssh_command_wrapper.sh` — network defaults and restricted Git enforcement.
- `src/evander/models.py`, `src/evander/policy.py`, and `src/evander/policy_config.py` — session capabilities, tool decisions, argument rules, approvals, and inherited denies.
- `src/evander/gateway/app.py`, `src/evander/gateway/session_context.py`, `src/evander/session_auth.py`, and `src/evander/repositories/mcp_transport_bindings.py` — MCP proof and persistent connection binding.
- `src/evander/relays/openai.py` and `src/evander/relays/http_target.py` — contrasting model-secret and target-confinement relay semantics.
- `src/evander/admin/services/managed_git_service.py` and `src/evander/worker/` — managed Git and worker trust boundaries.
- `tests/test_policy.py`, `tests/test_gateway.py`, `tests/test_firewall_defaults.py`, and `tests/test_managed_git_workflow_contract.py` — boundary enforcement tests.


<div class="page-break"></div>

# Part II: Core Evander Architecture

Trace configuration, policy, orchestration, execution, Git, integration, and operator surfaces through the host control plane.

# 4. System Topology, Services, and Ports

> **At a glance.** Evander is a set of small host services around shared runtime state, not one monolithic daemon. Admin Web and the CLI assemble the control plane; the MCP gateway and relays face isolated workloads; provider workers ingest external change; trigger and effect orchestrators advance workflows; and an optional worker daemon runs on a separate worker VM. Ports describe reachability, while sessions, policy, target allowlists, and credentials describe authority.

## The topology in one view

For a standard libvirt deployment, the important paths are:

```text
operator browser/CLI
        |
        v
Admin Web + shared admin runtime ---- runtime stores/databases
        |                                      ^
        v                                      |
trigger/effect workers <---- provider workers/events
        |
        v
libvirt VM ---- MCP / model / package / HTTP relays ---- external providers
        `------ restricted SSH/Git -------------------- managed bare repos
```

With `container_worker_vm`, agent effects call `evander-worker-daemon` on a dedicated worker VM. The disposable Docker job then calls back to the control-plane gateway and relays through explicitly configured guest URLs.

Processes share domain services and stores, but they do not all need to be running for every installation. A host used only for local VM sessions may not configure Teams, Discord, DIDWW, Azure DevOps, or a container worker. “Installed by the systemd installer” and “required for this workflow” are different statements.

## Operator and guest-facing services

These are the default listeners in current configuration. Live environment files can override them.

| Surface | Entry point or service | Default bind/port | Intended caller | Notes |
|---|---|---:|---|---|
| Admin Web | `evander-admin-web` | `127.0.0.1:8100` | Operator browser or reverse proxy | Dashboard, sessions, approvals, workflows, config, users, chat, and audit. `/healthz` is the health endpoint. |
| MCP gateway | `evander-gateway` | `0.0.0.0:8000` | Guest/container MCP client | Streamable MCP at `/mcp`; a plain GET may return `406` and still prove the endpoint is listening. |
| OpenAI relay | `evander-openai-relay` | `0.0.0.0:11434` | Guest/container model client | Keeps host model credentials; forwards streaming SSE without buffering away the stream lifetime. |
| NuGet relay | `evander-nuget-relay` | `0.0.0.0:11666` | Guest/container package client | Narrow NuGet package surface; `/healthz` is available. |
| HTTP target relay | `evander-http-target-relay` | `0.0.0.0:11777` | Session with configured target alias | Constrains external HTTP targets; forwards guest-managed auth rather than injecting a host secret. |
| npm audit facade | `evander-npm-audit-facade` | `0.0.0.0:11881` | Guest/container npm tooling | Narrow audit compatibility surface. |
| DIDWW SIP bridge | `evander-didww-sip-bridge` | Configurable; example is loopback `11888` | Host telephony integration | Optional, host-local integration; do not confuse it with the worker daemon's port on a separate host. |
| Worker daemon | `evander-worker-daemon` | CLI default `127.0.0.1:11888`; Ansible worker default `0.0.0.0:11888` | Authenticated control plane on the constrained worker network | Deployed by `ansible/worker.yml`, not the control-plane systemd installer. |

Admin Web deliberately defaults to loopback. Exposing it through a reverse proxy requires the operator to preserve authentication, cookie, TLS, and network controls; changing its bind address alone is not a complete exposure design.

## Firewall defaults

`config/firewall.env.example` permits guest-to-host TCP only on an explicit list:

| Port | Expected use |
|---:|---|
| `22` | SSH and restricted managed Git |
| `8000` | MCP gateway |
| `11434` | OpenAI-compatible model relay |
| `11666` | NuGet relay |
| `11777` | HTTP target relay |
| `11881` | npm audit facade |
| `8081` | Optional Nexus PyPI/npm cache |
| `3142` | Optional apt-cacher-ng provisioning cache |

The last two are firewall allowances for optional services, not services started by Evander. Conversely, loopback Admin Web and host-local support databases are not guest firewall entries. Qdrant, where used for semantic storage, defaults to a host-local `127.0.0.1:6333` provider endpoint rather than a general guest-facing database.

Opening a port establishes a route. The service still applies its own session proof, target, repository, or upstream-authentication rules.

## Workers without public listeners

Most background services are polling or queue consumers:

| Worker | Responsibility |
|---|---|
| `evander-trigger-orchestrator.service` | Poll enabled workflow triggers, publish durable events, advance cursors, and wake the effect worker. Its command is `evander-cli trigger-orchestrator`, not a separate `evander-trigger-orchestrator` console script. |
| `evander-effect-orchestrator.service` | Route queued events through graphs and execute gates, queries, and effects. |
| `evander-ado-worker.service` | Poll Azure DevOps PR feedback/completion; work-item intake remains workflow-owned. |
| `evander-proxy-issue-sync.service` | Refresh local proxy issue state from Azure DevOps. |
| `evander-discord-ingest.service` | Ingest Discord data. |
| `evander-discord-wake.service` | Turn eligible Discord messages into agent wake activity. |
| `evander-scheduled-wake.service` | Resume sessions whose scheduled wake is due. |
| `evander-teams-ingest.service` | Run `evander-cli teams-ingest` through `scripts/run_teams_ingest.sh`. |
| `evander-teams-meeting-ingest.service` | Ingest configured Teams meeting media/events. |

There is no current `evander-host-heartbeat.service`. Older references to it are stale. Azure DevOps feedback polling belongs to the ADO worker; Discord and scheduled wakeups belong to their dedicated workers. Optional guest-local heartbeat timer assets are a different mechanism and are disabled by default by provider bootstraps.

## Control-plane installation set

`scripts/install_systemd_units.sh` renders and enables the firewall, gateway, Admin Web, OpenAI relay, DIDWW bridge, NuGet relay, npm audit facade, HTTP target relay, ADO and proxy workers, Discord ingest/wake, scheduled wake, Teams ingest/meeting ingest, and both orchestrators.

Enabling a unit does not make an unconfigured integration healthy. Before install or start, populate the required local files for the services in use, including `config/firewall.env`, `config/admin-web.env`, provider worker JSON, trigger-orchestrator JSON, optional relay env files, and optional repository-root `secrets.env`.

The unit templates generally order processes after network readiness and load their configured environment. They are deliberately separate failure domains. The gateway can be healthy while a Teams credential is invalid; Admin Web can render while the effect worker is stopped; a package relay can be absent without changing workflow queue durability.

## Runtime state shared across services

All control-plane processes must agree on the runtime root. Important shared surfaces include:

- `host-config.json` and `directory-config/` for bootstrap and effective configuration;
- session, approval, directory, wake, prompt, and provider state stores;
- `orchestration.db` for the default trigger queue and workflow execution state;
- `audit.jsonl` plus `audit.db` for canonical audit and its query index;
- `managed-git-repositories.json`, `managed-git/`, and `git-worktrees/` for managed source;
- add-on state below `runtime/addons/` when an add-on is installed.

If two services point at different runtime roots, each may look internally consistent while the system appears split-brained. Always inspect the unit's effective command and environment before editing the repository's example configuration.

## A Northstar request across processes

For the telemetry-retry work item, a typical process sequence is:

1. A provider worker or workflow trigger sees Northstar's source event using credentials resolved for `root/northstar-platform/mission-control`.
2. The trigger orchestrator writes a queue record to `orchestration.db` and signals the effect worker's local wake mechanism.
3. The effect worker creates Imani's session and calls the selected backend.
4. Imani's VM reaches ports `8000`, `11434`, and package surfaces through the host firewall; it uses port `22` for restricted managed Git.
5. Operators inspect state through Admin Web on `8100`, normally via the host or its reverse proxy.
6. Review feedback returns through a provider worker or trigger and re-enters the same durable event path.

No request needs to pass through Admin Web to reach the gateway, and Admin Web is not the workflow engine. It is an operator surface over the same domain services and stores.

## Failure behavior

| Symptom | Layer to inspect first | Important distinction |
|---|---|---|
| Connection refused | Listener/process and bind address | The service is not reachable; policy has not run yet. |
| Timeout from guest | Firewall, routing, service bind, or worker network | A port may listen on the wrong interface. |
| HTTP `401` from MCP | Session proof | The network path works; authentication failed. |
| HTTP `406` from plain GET `/mcp` | MCP content negotiation | This can be expected liveness evidence, not a dead gateway. |
| Tool absent or denied | Session and effective policy | Gateway reachability does not grant the tool. |
| Admin Web healthy, runs stalled | Trigger/effect services and queue | The UI is not the executor. |
| Trigger healthy, effects absent | Effect worker, graph, queue, completion state | Trigger publication and effect execution are distinct stages. |
| Optional cache port open, restore fails | Actual cache service and client URL | Firewall allowance does not start Nexus or apt-cacher-ng. |
| Worker daemon unreachable | Control-plane-to-worker routing/token/service | Guest firewall settings are not the same path. |
| All stores appear empty after restart | Runtime-root mismatch | The process may be using `/var/lib/evander` while the operator inspects repository `runtime`. |

## Practical runbook

Begin with the application view and then descend to systemd and sockets:

```bash
evander-cli status --runtime-dir runtime --json
sudo systemctl --failed
sudo systemctl status evander-gateway.service
sudo systemctl status evander-admin-web.service
sudo systemctl status evander-trigger-orchestrator.service
sudo systemctl status evander-effect-orchestrator.service
sudo ss -ltnp
```

Check the narrow health paths:

```bash
curl -i http://127.0.0.1:8100/healthz
curl -i http://127.0.0.1:11666/healthz
curl -i http://127.0.0.1:8000/mcp
```

For the gateway probe, record the status and headers; do not require `200` from a content-negotiation-free GET. For service failures, inspect the actual unit and recent logs:

```bash
sudo systemctl cat evander-gateway.service
sudo journalctl -u evander-gateway.service -n 200 --no-pager
```

Before reinstalling units, run the supported preflight:

```bash
./scripts/install_systemd_units.sh --preflight
```

Do not start every optional worker merely to make `systemctl --failed` empty. Configure and run only the integrations the deployment intends to use, while keeping required core surfaces healthy.

## Source trail

- `pyproject.toml` — installed console entry points.
- `systemd/*.service.template` and `scripts/install_systemd_units.sh` — current control-plane units, commands, environment files, and installation set.
- `config/firewall.env.example` and `scripts/apply_firewall.sh` — guest-to-host port defaults and firewall realization.
- `src/evander/host_config.py`, `src/evander/admin/web/app.py`, `src/evander/worker/cli.py`, and `src/evander/didww_sip_bridge.py` — listener defaults.
- `src/evander/daemons/trigger_orchestrator.py` and `src/evander/daemons/effect_orchestrator.py` — orchestration worker topology.
- `docs/operations.md`, `docs/architecture.md`, and `docs/container-worker-backend.md` — supported deployment and worker layout.
- `tests/test_install_systemd_units_script.py`, `tests/test_systemd_docs.py`, and `tests/test_firewall_defaults.py` — executable service and port documentation checks.


# 5. Runtime Assembly, Configuration, and Directories

> **At a glance.** Evander separates host bootstrap settings from inheritable directory settings, workflow definitions, host secrets, and durable runtime state. `build_admin_runtime` is the supported composition root for operator and integration surfaces. Directory configuration resolves from immutable `root` through the selected ancestry: objects merge recursively, while lists, scalars, and `null` replace. Effective configuration and its provenance—not one JSON overlay—determine behavior.

## Five kinds of configuration-shaped data

Several Evander files contain JSON or environment variables, but they have different ownership and update rules:

| Kind | Typical location | Purpose | Safe mutation path |
|---|---|---|---|
| Host bootstrap | `runtime/host-config.json` | Runtime root, listener addresses, core store paths, add-on loader | Setup/CLI and supported host configuration flow |
| Directory config | `runtime/directory-config/root/...` | Inheritable policy, providers, contacts, email, wake, VM, and automation behavior | Admin Web or `evander-cli directories config-set` |
| Workflow config | `config/trigger-orchestrator/config.json` and referenced packs | Graphs, nodes, edges, provider bindings, orchestration store settings | Admin Web revision/lock API; files are loading surfaces, not a concurrency bypass |
| Secrets/service env | `config/*.env`, provider JSON env references, optional `secrets.env` | Credentials and process-specific listener/auth settings | Host operator and secret-management process |
| Runtime state | JSON/SQLite beneath the runtime root | Sessions, approvals, queue/journal, Git registry, audit, add-ons | Domain CLI/Admin/API only; do not hand-edit |

The distinction prevents two recurring mistakes. First, a listener port belongs to host bootstrap or service environment, not a child project's provider overlay. Second, `orchestration.db` may contain JSON columns, but it is a durable state database, not configuration to edit into a desired state.

## Bootstrapping a runtime

The repository-oriented first-run sequence is:

```bash
uv pip install -e '.[dev]'
evander-cli init-state --runtime-dir runtime
```

Initialization establishes `host-config.json`, the directory registry, the immutable root directory, root directory-config files, and other required stores. The packaged `HostConfig` default runtime is `/var/lib/evander`; commands and repository services commonly pass `runtime` explicitly. Relative runtime paths are resolved, and default store paths follow the selected runtime root.

Legacy gateway YAML is accepted as explicit migration or bootstrap input. It is not the current live baseline. Once host and root directory configuration exist, diagnose those current surfaces rather than repeatedly editing an old YAML file.

At startup, `load_runtime_gateway_config` combines the host bootstrap with root directory domains into a validated `GatewayConfig`. Broader assembly then adds the repositories and services that a process needs.

## The supported composition root

`src/evander/admin/composition.py::build_admin_runtime` is the supported assembly surface for the CLI, gateway integrations, Admin Web, workflow providers, and add-ons that need administration services. It:

1. resolves an explicit legacy path only when supplied or when the input is clearly YAML;
2. loads or creates host bootstrap configuration;
3. opens the directory store and ensures `root` exists;
4. opens the directory-config store and ensures root domains exist;
5. resolves the current runtime gateway configuration;
6. builds the shared `RuntimeContext`;
7. opens the audit index and starts best-effort import from canonical JSONL;
8. composes narrow domain services for directories, sessions, approvals, issues, Git, workflows, workers, reports, skills, and other operator functions.

`RuntimeContext` is the lower-level shared bundle. It contains repositories and runtime services such as sessions, approvals, tool approval leases, directories, MCP transport bindings, drafts, email and communication stores, wake state, prompt queues, scripts, policy/provider resolvers, `PolicyEngine`, and `AuditLogger`.

This assembly boundary matters to extensions. Reconstructing stores ad hoc can choose a different runtime root, omit a migration, bypass a repository adapter, or create policy and provider resolvers with different context. Add behavior through narrow services and the add-on/provider registration surfaces rather than creating a second hidden control plane.

![Runtime composition from bootstrap files through resolved configuration to shared services](assets/evander-user-manual/diagrams/03-runtime-composition.svg)

<p class="figure-caption">Figure 5.1 — One supported composition path keeps CLI, gateway, Admin Web, workflows, and add-ons on the same runtime truth.</p>

## Directories are configuration and authorization scope

Every installation has an immutable top-level directory named and addressed as `root`. Child directories form a tree. The directory store rejects a missing parent, duplicate sibling name, invalid canonical path, and cycles. Root cannot be moved into or replaced by a child.

Northstar can therefore be represented as:

```text
root
└── northstar-platform
    └── mission-control
```

The same canonical path can scope:

- effective policy and provider settings;
- Admin Web RBAC assignments;
- session and workflow ownership;
- managed repositories and provider operations;
- communication targets and Team Add-on records.

A directory is not a filesystem checkout. It is a logical ownership and inheritance node whose configuration happens to be stored under a matching runtime path.

## Configuration domains

The current directory-config store recognizes these canonical domains:

| Domain | File | Examples of responsibility |
|---|---|---|
| `policy` | `policy.json` | Session TTL, tool denies/approvals, arguments, communication/file rules, redaction |
| `providers` | `providers.json` | Azure DevOps, Discord, Teams, execution backend, worker, model/relay and Git provider settings |
| `contacts` | `contacts.json` | Named communication destinations without exposing raw addresses to sessions |
| `email` | `email.json` | Mailbox/provider settings |
| `wake` | `wake.json` | Wake and resume provider behavior |
| `ado_worker` | `ado-worker.json` | Directory-owned ADO worker behavior |
| `vm` | `vm.json` | Libvirt templates and VM defaults |
| `issue_setup` | `issue-setup.json` | Issue-session bootstrap choices |
| `pr_template` | `pr-template.json` | Pull-request template content/settings |
| `automation` | `automation.json` | Directory automation configuration |

Hyphenated CLI aliases such as `ado-worker` and `issue-setup` are normalized to canonical domain names. Legacy domain files and metadata overlays are migration compatibility surfaces; new work should use the canonical files.

## Resolution: root to leaf

To resolve Northstar's `policy`, Evander starts with model defaults, applies any raw `root/policy.json`, then `root/northstar-platform/policy.json`, then the leaf `root/northstar-platform/mission-control/policy.json`. Each applied layer records directory ID, path, domain, and overlay. Provenance maps effective leaf fields back to their source.

Merge semantics are deliberately simple:

- when both current and overlay values are objects, keys merge recursively;
- every other overlay value replaces the current value;
- therefore lists replace rather than concatenate;
- an explicit `null` replaces an inherited value with `null` if the domain schema permits it;
- the final effective payload is validated as a domain model.

For example, a child can replace an inherited list of approval notification targets without inheriting every root target. It cannot append one item merely by supplying a one-item list. Operators should inspect the effective result after a list change.

Policy adds one monotonic security rule: every MCP tool denied by any applied ancestor remains denied in the effective result, even if a child overlay writes `denied: false`. This exception prevents delegation from weakening an upstream prohibition while retaining ordinary replacement semantics for other values.

![Root-to-leaf directory configuration inheritance](assets/evander-user-manual/diagrams/04-directory-config.svg)

<p class="figure-caption">Figure 5.2 — Effective configuration is validated after ordered root-to-leaf overlay, with inherited tool denials remaining monotonic.</p>

## Raw, effective, and selected values

Three views answer different questions:

- **Raw** is exactly what one directory overrides.
- **Effective** is the validated merge through the full ancestry plus defaults.
- **Selected runtime value** may additionally be pinned or overridden by session metadata—for example a model-provider mode or the exact worker image reference selected when the session was created.

The Admin Web configuration surface and `directories config-show` expose raw and effective information. Use provenance to answer “why is this value here?” A root overlay may be absent because the value came from the code default; a leaf may have no file because it intentionally inherits.

Sessions carry a canonical directory path. Policy resolution uses that path for current effective policy. Provider resolution also begins there, with supported session-level selections layered where defined. Moving or deleting directory ownership while sessions or workflows refer to it is therefore guarded by administrative ownership checks and validation.

## Runtime directories and durable stores

The precise set grows with enabled features, but these paths are foundational:

| Path below the selected runtime | Role |
|---|---|
| `host-config.json` | Host bootstrap configuration |
| `directories.json` | Directory tree records |
| `directory-config/` | Canonical raw directory overlays |
| `sessions.json` | Session manifests through the repository adapter |
| `approvals.json` | Per-invocation approval requests |
| `session-tool-approval-leases.json` | Workflow-scoped temporary MCP grants |
| `mcp-transport-bindings.json` | Persistent MCP transport-to-session binding |
| `orchestration.db` | Default trigger queue, workflow execution and snapshot state |
| `audit.jsonl` / `audit.db` | Canonical audit stream and query index |
| `managed-git-repositories.json` | Managed-repository registry |
| `managed-git/` / `git-worktrees/` | Evander-owned bare repositories and ephemeral host worktrees |
| `admin-web.db` | Admin users, groups, assignments, and grants |
| `addons/` | Add-on-owned runtime state, including Team state when enabled |

The service and repository types define transaction and locking behavior. JSON stores use their supported atomic mutation paths; SQLite stores use schema, busy timeout, WAL, and domain transactions. A text editor provides none of those guarantees.

## Northstar configuration in practice

Suppose root selects `libvirt_vm`, disables email sending, and denies a destructive MCP tool. `root/northstar-platform` selects a provider project and approval notification target. `mission-control` selects a VM template and narrows allowed contacts.

Imani's telemetry-retry session receives the effective intersection. The project does not have to duplicate root relay settings. It cannot undo the destructive-tool deny. Its contacts list replaces the inherited list rather than extending it. If an operator inspects the provider setting, provenance can show that the organization came from root while the project name came from `northstar-platform`.

This same structure lets operators set team-wide policy while owning project-specific automation without copying one large global configuration file.

## Failure behavior

| Symptom | Likely cause | Safe response |
|---|---|---|
| Service shows empty runtime | Different `--runtime-dir` or unit environment | Inspect the running unit and `host-config.json`; do not copy state blindly. |
| Child list lost inherited entries | Lists replace, not concatenate | Write the full intended child list or remove the override. |
| Child cannot re-enable a tool | An ancestor deny is monotonic | Change the denying ancestor after a security review; do not patch runtime output. |
| Configuration save is rejected | Domain schema, directory path, revision, or lock conflict | Correct the validation error or reload the current revision. |
| Hot-running process retains older workflow | Invalid reload kept last-known-good | Inspect worker log and Admin Web validation; fix and publish a new valid revision. |
| Legacy YAML edit has no effect | Current runtime has migrated to host/root configuration | Inspect `host-config.json` and root domains. |
| Admin and gateway disagree | Independently launched processes used different roots or code revisions | Compare command lines, environments, checkout revision, and resolved config. |
| SQLite/JSON becomes corrupt after manual edit | Domain transaction/locking was bypassed | Stop writers, preserve evidence, restore from backup or supported reconstruction; do not keep editing. |

## Practical runbook

Inspect rather than infer:

```bash
evander-cli status --runtime-dir runtime --json
evander-cli directories show root/northstar-platform/mission-control \
  --runtime-dir runtime
evander-cli directories config-show root/northstar-platform/mission-control \
  --domain policy --runtime-dir runtime
evander-cli directories config-show root/northstar-platform/mission-control \
  --domain providers --runtime-dir runtime
```

Before a mutation:

1. Record the selected runtime and current directory path.
2. Read the raw overlay, effective value, provenance, and validation errors.
3. Determine whether the field belongs to host, directory, workflow, secret, or state configuration.
4. Use Admin Web's current revision/lock path or `directories config-set`; never write a live workflow pack from a stale local copy.
5. Read back the raw and effective result.
6. Check the relevant worker/service log for reload acceptance.

For first-run or recovery initialization, `evander-cli init-state --runtime-dir runtime` is idempotent setup, not a command to erase current state.

## Source trail

- `src/evander/host_config.py` and `src/evander/bootstrap.py` — host bootstrap, runtime-relative paths, legacy import, and root initialization.
- `src/evander/admin/composition.py` and `src/evander/runtime.py` — supported composition root and shared runtime services.
- `src/evander/directories.py` and `src/evander/directory_config.py` — tree invariants, domain files, ancestry merge, provenance, and validation.
- `src/evander/policy_config.py` and `src/evander/provider_config.py` — effective policy/provider resolution and monotonic tool denies.
- `src/evander/workflow/runtime_config.py` and `src/evander/admin/services/workflow_config_service.py` — workflow loading, validation, hot reload, revision, lock, and history behavior.
- `docs/configuration-reference.md` and `docs/operations.md` — field-level and operator guidance.
- `tests/test_directory_config.py`, `tests/test_directory_admin.py`, `tests/test_cli.py`, and `tests/test_admin_extracted_services.py` — inheritance, initialization, mutation, and assembly contracts.


# 6. Identity, Sessions, RBAC, and Policy

> **At a glance.** Evander has two deliberately separate authorization planes. Human operators authenticate to Admin Web and receive directory-scoped RBAC permissions plus a transient privileged-operation lock. Agents receive expiring session manifests whose tools and resources are intersected with effective directory policy. Workflow, transport, provider, and external identities correlate actions, but none substitutes for either human RBAC or session capability.

## The identity stack

“Who did this?” can refer to several valid identities:

| Identity | Examples | What it governs |
|---|---|---|
| Human operator | An operator's Admin Web login | Reading or mutating operator surfaces through RBAC |
| Directory scope | `root/northstar-platform/mission-control` | Which inherited configuration and RBAC assignments apply |
| Agent/session | Imani's `session_id` and optional VM/provider binding | MCP visibility, allowlists, quotas, expiry, execution correlation |
| Workflow | Run ID, graph ID/revision, trace ID, node execution ID | Why the action was scheduled and which configuration produced it |
| MCP transport | Protocol `mcp-session-id` bound to an Evander session | Which session the connection is allowed to represent |
| Provider identity | ADO project, Git account, Teams app, relay credential | Which external service principal the host uses |
| Team identity | Mira, Priya, Imani, Rowan, and Riley as AI Team agents | Add-on task, wake, review, governance, and release responsibility |

The audit trail combines several of these so an operator can distinguish “an operator approved a request for Imani's session during workflow run X” from the less useful “a tool ran.”

## Admin Web authentication

Admin Web supports three current modes:

| Mode | Purpose |
|---|---|
| `none` | An explicitly unauthenticated/local mode. Appropriate only inside a trusted, otherwise protected operator boundary. |
| `local_password` | Local users and PBKDF2 password credentials, with an environment-supplied bootstrap/break-glass account. |
| `azure_sso` | Azure SSO identity mapped to the local user/RBAC store. |

If no mode is supplied, a configured admin password selects `local_password`; otherwise the process falls back to `none`. Production operators should set the mode deliberately rather than relying on that convenience behavior.

The durable user, group, assignment, and direct-grant store is SQLite at `runtime/admin-web.db`. Legacy JSON user data is migration input. Local passwords use PBKDF2-HMAC-SHA-256 with 200,000 iterations and a 32-byte random salt. The database stores a verifier, not the plaintext password.

Successful authentication creates an HMAC-signed `evander_admin_session` cookie. The default session TTL is 43,200 seconds (12 hours), with a minimum of 60 seconds. Set a stable `EVANDER_ADMIN_WEB_SESSION_SECRET`; if absent, the process generates one, so a restart invalidates existing cookies. Set secure-cookie and reverse-proxy/TLS behavior as part of a complete exposure design.

The first-owner bootstrap is guarded. The configured local bootstrap account or Azure bootstrap allowlist can establish the first usable owner, but later authorization comes from durable users and assignments. User-management logic protects the effective owner boundary so an operator cannot casually remove or disable the last owner.

## Directory-scoped RBAC

Admin RBAC has this permission catalog:

| Permission | Meaning |
|---|---|
| `admin.owner` | Full permission catalog |
| `users.manage` | Manage users and relevant assignments |
| `groups.manage` | Manage groups and permissions |
| `workflows.read` | Read workflow definitions |
| `workflows.write` | Modify workflow definitions; implies read for direct grants |
| `workflow_packs.manage` | Manage workflow packs |
| `workflow_runs.read` | Inspect workflow runs |
| `workflow_runs.control` | Operate workflow runs |
| `team_chats.read_all` | Read Team chats beyond ordinary participant scope |

Built-in groups are `owner`, `operator`, `workflow-maintainer`, `workflow-viewer`, `auditor`, and `viewer`. Owner and operator are required group definitions. `admin.owner` expands to the full catalog. Operator receives workflow read/write, pack management, run read/control, and Team chat read-all; it does not implicitly receive user/group management unless configured through another group or ownership.

A group assignment at a directory applies to that directory and descendants. Thus a Northstar assignment at `root/northstar-platform` applies to `mission-control` but not to an unrelated root child. Direct workflow grants are limited to workflow read or write, and write expands to read. Effective permission is the union of matching group assignments and matching direct workflow grants.

Scope inheritance is downward only. An assignment on `mission-control` does not authorize its parent or sibling.

## The privileged-operation lock

Authentication and RBAC answer whether an operator *may* perform an action. Sensitive mutation routes add a transient lock: the current authenticated Admin Web session must also be marked as privileged-unlocked.

Login begins locked. Calling the unlock endpoint marks the current cookie session in process memory; it is not a second password or SSO challenge. Lock, logout, cookie expiry, or process restart removes the mark. Routes call `request_has_privileged_operations`, optionally with a required permission and directory scope, so both authorization and the transient state must pass.

This is a guard against accidental clicks and unattended open consoles, not multi-factor authentication. It must not be described as a second human approval. Workflow/MCP approvals are a separate durable mechanism with payload binding and an audit decision.

## Session manifests: agent identity as a bounded capability

A `SessionManifest` includes:

- `session_id`, creation and expiry timestamps;
- VM, execution-provider, and model bindings where selected;
- canonical `directory_path`;
- allowed MCP tools, approval-required tools, and per-tool grant settings;
- allowed contact and script IDs;
- Discord, Teams, mailbox, folder, recipient, and domain allowlists;
- tool argument policies;
- communication file limits and enablement;
- web-search enablement and quotas;
- metadata for workflow, issue, Team, Git, HTTP-target, and execution correlation.

An empty or absent permission is not interpreted as broad ambient access. Unknown and expired sessions are denied. Renewal or reissue is an explicit operator/domain action; an old workload cannot extend its own expiry simply by continuing to send traffic.

Northstar can create Imani's session with only the tools needed to inspect the work item, operate the managed repository workflow, and report results. Rowan's review session can have a different tool set even though both belong to the same directory.

![Admin Web session detail for Imani's synthetic telemetry-retry session](assets/evander-user-manual/screenshots/admin-sessions.png)

<p class="figure-caption">Figure 6.1 — Session state, scope, backend, and tool grants meet in one operator view. Sanitized Northstar fixture.</p>

## How policy is evaluated

For a tool request, the policy engine effectively asks:

1. Does the session exist and remain unexpired?
2. What is the effective policy for its directory?
3. Is the tool denied by directory policy or a special capability profile?
4. Is it granted by `allowed_tools`, or by a valid workflow-scoped approval lease for this session and run?
5. Are any structural prerequisites present—for example a bound execution for resume behavior or a feedback stream for feedback tools?
6. Do inherited and session argument/resource rules permit this payload?
7. Does the tool require per-invocation approval, and is the presented approval valid?

Tool visibility and tool execution use the same underlying policy decisions, but visibility cannot pre-validate a future payload. A tool can appear in `tools/list` and still reject a disallowed recipient, script, target, or argument.

Directory policy supplies safe defaults: approval TTL 3,600 seconds, session TTL 28,800 seconds, email sending disabled, and communication file send/receive disabled. Live directory overlays may alter supported values. These defaults are not a substitute for explicitly reviewing a production root policy.

Argument-policy operators include required fields, fixed values, patterns, enums, allowlists, and denylists. The resolved directory rule list and the session rule list both apply; a permissive session rule does not erase a directory constraint. Within directory inheritance, ordinary list replacement follows the merge rules in Chapter 5.

## Workflow-scoped tool leases

A `SessionToolApprovalLease` temporarily grants named MCP tools to exactly one session and workflow run. It contains a lease ID, effect execution correlation, per-tool approval settings, optional notification targets, expiry, revocation time, and used counts.

The workflow context must present matching run and lease IDs. Use is consumed under the repository's atomic update path. A lease cannot:

- revive an expired session;
- cross to another session or workflow run;
- override an inherited directory deny;
- bypass argument/resource policy;
- remove mandatory or configured per-invocation approval.

This distinction is useful for the Northstar flow. The project's graph may reserve one tool for Imani during a bounded implementation step without putting that tool in every long-lived session. The lease says the workflow may expose the tool; a separate exact approval may still be required for a sensitive call.

## Failure behavior

| Symptom | Decision that failed | Typical cause |
|---|---|---|
| Admin Web redirects or returns unauthenticated | Human authentication | Missing/expired/bad cookie, restart with generated secret, SSO or password failure |
| Authenticated user sees read-only UI | RBAC | No matching directory assignment or direct grant |
| Mutation says privileged operations are locked | Transient guard | Current cookie session has not been unlocked or process restarted |
| User can edit Northstar but not a sibling | Directory scope | Expected descendant-only assignment behavior |
| Agent's `tools/list` is empty | Session/MCP identity | Unknown, expired, unproved, or incorrectly bound session |
| Tool is missing | Visibility policy | Not granted, directory-denied, profile-denied, or structurally unavailable |
| Visible tool rejects call | Payload policy | Argument, target, quota, lease, or approval failed |
| Lease works in one run but not another | Workflow scope | Run/session mismatch is intentional |
| Last owner mutation is rejected | Recovery invariant | Store prevents loss of the effective owner boundary |

## Practical runbook

For a human access issue, collect: auth mode, login, current directory, requested permission, matching group assignments, direct grants, and privileged-lock state. Do not inspect or copy password-verifier fields unless incident response requires it.

For an agent access issue:

```bash
evander-cli list-sessions --runtime-dir runtime --all
evander-cli directories config-show root/northstar-platform/mission-control \
  --domain policy --runtime-dir runtime
evander-cli list-approvals --runtime-dir runtime
```

Then use Admin Web's session tool-visibility view or the gateway audit entry to identify the exact reason. Compare all of the following before changing policy: normalized tool name, session expiry, directory, session grant, inherited deny, structural prerequisite, argument payload, workflow run/lease, approval request, and remaining uses.

Use the lifecycle commands rather than editing `sessions.json`:

```bash
evander-cli renew-session <session-id> --runtime-dir runtime
evander-cli revoke-session <session-id> --runtime-dir runtime
```

Check each command's `--help` for the required renewal/expiry arguments in the installed revision. Revoke the smallest affected session or lease when authority is no longer appropriate; changing a human's group does not automatically revoke an already issued agent session.

## Source trail

- `src/evander/admin/web/shared.py`, `src/evander/admin/web/user_store.py`, and `src/evander/admin/web/rbac.py` — authentication, cookies, password storage, groups, permission scope, and privileged lock.
- `src/evander/admin/web/users.py` and `tests/test_admin_web_user_store.py` — user/group administration, bootstrap, migration, and owner protection.
- `src/evander/models.py` — `SessionManifest`, `SessionToolApprovalLease`, and approval models.
- `src/evander/policy.py` and `src/evander/policy_config.py` — visibility, grants, denial, structural checks, arguments, approvals, and defaults.
- `src/evander/repositories/session_manifests.py` and `src/evander/repositories/session_tool_approval_leases.py` — durable session and lease mutation semantics.
- `tests/test_admin_web_rbac.py`, `tests/test_policy.py`, `tests/test_gateway.py`, and `tests/test_session_tool_approval_leases.py` — authorization-plane invariants.


# 7. MCP, Approvals, and Audit

> **At a glance.** The MCP gateway turns one host endpoint into a session-specific tool catalog. It binds each MCP transport to an Evander session, verifies session proof, filters tools, validates every invocation, and records the decision. Approval-required calls use a two-step protocol: create a durable request without executing, obtain a reasoned human decision, then retry the bound payload before expiry and use exhaustion. JSONL audit is canonical; SQLite is its query index.

## MCP is a policy gateway

`evander-gateway` hosts a FastMCP server named “Evander Gateway” at the streamable `/mcp` path. It is not a transparent proxy to arbitrary host functions. Registered tools are adapters over explicit host services, and both discovery and invocation are session-aware.

The broad request path is:

```text
MCP HTTP request
  -> resolve declared/bound Evander session
  -> verify session proof token
  -> check active session and effective directory policy
  -> filter catalog or validate invocation arguments
  -> validate workflow lease and per-invocation approval
  -> dispatch one registered tool
  -> append audit event and best-effort index it
```

This order explains why “the gateway is reachable” and “the tool is usable” are different observations.

![MCP tool invocation policy, approval, execution, and audit sequence](assets/evander-user-manual/diagrams/05-mcp-policy-sequence.svg)

<p class="figure-caption">Figure 7.1 — Catalog visibility, invocation authorization, approval, execution, and audit are separate checks in one request envelope.</p>

## Connection binding and proof

An MCP client initially declares its Evander session with the `session_id` query parameter or `x-evander-session-id` header. It supplies the session proof token with one of the supported MCP session token query parameters or headers, normally `x-evander-mcp-session-token`.

The MCP protocol itself assigns an `mcp-session-id`. When that transport ID and a declared Evander session first appear together, Evander persists their relationship in `runtime/mcp-transport-bindings.json`. Future requests on the transport inherit the binding. A request that declares a conflicting Evander session is rejected rather than silently rebound.

Session setup issues a random proof token and records MCP-auth metadata on the session, including a SHA-256 verifier. Comparison is constant-time. Query-string tokens are redacted when the gateway logs request URLs. Headers are preferred because URLs are more likely to appear in infrastructure logs.

The `session_id` field remains optional on tool schemas for legacy clients. The wrapper supplies the bound connection session and rejects a conflicting explicit field. An unbound caller cannot choose another session merely by changing a tool argument.

## Catalog filtering

MCP `tools/list` is evaluated for the authenticated session. The result contains only tools that are registered, compatible with the session's capability profile, not directory-denied, granted by the session or an eligible workflow lease, and structurally usable.

Structural examples include resume operations that need a bound execution and feedback operations that need a configured feedback stream. This avoids advertising a tool that cannot make sense for the session even before arguments are known.

A bad or missing proof can produce an HTTP authentication rejection before catalog evaluation. An otherwise valid session with no visible tools can legitimately receive an empty catalog. Inspect the status and audit/policy reason rather than treating both outcomes as “MCP returned nothing.”

Catalog visibility is not pre-approval. A visible tool can still require an approval request when invoked, and argument/resource policy can reject a particular payload.

## Invocation checks

The gateway's registered-tool wrapper performs the current invocation controls around the tool implementation:

1. bind the authoritative session from the request context;
2. require valid session proof and an active manifest;
3. enforce add-on registration/capability policy where the tool came from an add-on;
4. enforce effective directory and session argument/resource policy;
5. determine per-invocation approval settings;
6. validate and, when appropriate, consume approval or workflow-lease use;
7. dispatch the host operation;
8. audit allowed, blocked, denied, error, and result context with redaction.

The exact helper ordering can differ for specialized tool families, but none may treat a free-form `session_id` as sufficient authority. Add-on tools register into the same gateway and remain subject to the same connection, session, and audit envelope.

## The approval handshake

When Imani invokes an approval-required tool during the telemetry-retry work, the first call omits `approval_request_id`. Evander does not run the effect. It creates or reuses a request whose ID is deterministic for the session and invocation payload, then returns an error/instruction identifying the pending request and asking the agent to retry after a decision.

The stored `ApprovalRequest` contains:

- request, session, directory, VM, tool/action, and intended-effect identity;
- a digest of the unredacted invocation payload;
- a recursively redacted payload for review plus a human summary;
- status, creation and expiry timestamps;
- effective grant settings and current use count;
- notification targets, deliveries, errors, and attempt state;
- approver, decision time, reason, and decision history.

The digest and review payload serve different purposes. Redaction limits disclosure to the operator surface; hashing the original payload preserves exact binding even when a sensitive field is hidden.

An operator approves or denies a pending request and supplies a non-empty reason. An approval does not itself execute the action. Imani retries the tool with the request ID. The policy engine atomically validates status and optionally consumes use while checking expiry, payload mode, session, tool, and maximum uses.

The `approval_submit` and `approval_check` protocol tools do not require their own approval, which would otherwise create an infinite loop.

![Admin Web approval detail bound to the telemetry-retry operation](assets/evander-user-manual/screenshots/admin-approvals.png)

<p class="figure-caption">Figure 7.2 — The operator sees the request identity, bound action, status, and decision surface without receiving ambient guest authority. Sanitized Northstar fixture.</p>

## Grant settings and limits

The ordinary default is:

- exact payload binding;
- one use;
- an expiry derived from effective policy, 3,600 seconds by default.

Session per-tool settings can request a TTL, use count, and payload mode. Directory policy remains the upper security boundary: it can cap TTL and use count and require exact payload. A broader `ANY` payload mode is therefore an explicit policy choice, not an accidental consequence of a redacted value.

Two built-in destructive operations are always stricter: `delete_ado_backlog_item` and `semantic_delete_points` require exact-payload, one-use approval. This mandatory set is intentionally small and explicit. Other tools become approval-required through effective directory policy, session settings, communication-file rules, or specialized policy behavior.

A reusable request ID does not mean unlimited use. Once the configured use count is reached, status becomes exhausted and the next attempt is rejected. A pending or approved request that passes its deadline becomes expired. Denial, expiry, exhaustion, payload mismatch, wrong session, wrong tool, and unknown request are distinct rejection states for audit and diagnosis.

## Workflow-scoped leases versus invocation approvals

A workflow may issue a `SessionToolApprovalLease` to expose a tool temporarily for one session and workflow run. The MCP context presents the lease and workflow-run IDs, and consumption tracks per-tool use.

The two controls answer different questions:

| Control | Question |
|---|---|
| Workflow tool lease | May this workflow step temporarily add this tool to this session's catalog? |
| Invocation approval | May this exact sensitive call execute now? |

A lease cannot cross a session/run boundary, revive an expired session, override a directory deny, or bypass invocation approval. Where both apply, both must pass.

## Approval notifications

Effective policy or the session can select configured communication target IDs for pending approvals. Request creation records queued/disabled status and delivery attempts. Notification failure does not silently approve or deny the request; the durable approval remains pending until decision or expiry.

Notifications carry a review prompt, not provider credentials. Operators should use the canonical request in Admin Web or CLI for the final decision, especially if a chat notification is truncated or delayed.

Northstar can route Imani's approval request to an authorized operator without revealing a raw address or phone number to the agent. The session sees the target ID and approval outcome, while the host resolves the contact/provider details.

## Audit: canonical stream and query index

`AuditLogger` appends one JSON object per line to the configured audit path, normally `runtime/audit.jsonl`. For tool calls, records can include:

- timestamp, directory, session, VM, agent role, and actor user ID;
- tool name, status, decision, and error summary;
- request, correlation, workflow run, trace, approval, and lease IDs;
- duration and redacted arguments/results.

The effective policy's redacted keys are applied recursively, with additional sensitive markers guarded by the index. Proof-token query values are also redacted in transport logs. Redaction is designed to reduce accidental disclosure; it should not be used as permission to place secrets in ordinary payloads.

`runtime/audit.db` is a SQLite search index built from the JSONL stream. It stores normalized columns plus the payload/search text and uses WAL. Appends try to index immediately; Admin runtime startup can import historical tail data. If indexing fails, the JSONL append remains canonical. Rebuilding the index must not rewrite the source log.

Approval decisions create their own audit context, linked by request and session. Workflow execution journals are complementary: audit explains tool policy/decision activity, while orchestration state explains graph routing and node execution.

## Failure behavior

| Symptom | Meaning | First evidence |
|---|---|---|
| `/mcp` plain GET returns `406` | Endpoint is listening but request did not negotiate MCP | HTTP status/headers; use an MCP client for protocol health |
| HTTP `401` | Missing or invalid session proof | Session metadata, client header/query, expiry |
| HTTP/session conflict `409` or tool conflict error | Transport is already bound differently | `mcp-transport-bindings.json` and declared header/query |
| Tool absent from list | Visibility denial or missing structural prerequisite | Tool visibility explanation and effective policy |
| First call returns approval request | Expected controlled pause | `show-approval` and pending audit record |
| Approved retry says payload mismatch | Retried arguments differ from the approved digest | Original intended effect and current redacted payload |
| Approved retry says exhausted/expired | Use or time bound has ended | Request status/settings/history |
| Notification failed | Review delivery failed, not the durable request | Notification deliveries/error and pending queue health |
| Admin audit view misses recent calls | SQLite index stale or unavailable | Canonical `audit.jsonl`; rebuild index |
| JSONL lacks expected event | Wrong runtime, process, or code path; append itself did not occur | Running unit config and gateway log |

## Practical runbook

For a pending Northstar request:

```bash
evander-cli list-approvals --runtime-dir runtime \
  --status pending \
  --directory-path root/northstar-platform/mission-control \
  --show-payload
evander-cli show-approval <request-id> --runtime-dir runtime
```

After confirming session, tool, intended effect, expiry, exact redacted payload, and surrounding workflow context:

```bash
evander-cli approve <request-id> <operator-id> \
  --approve \
  --reason "Telemetry retry test needs this bounded operation" \
  --runtime-dir runtime
```

To deny, use `--deny` and an equally useful reason. Never approve merely to clear queue health.

If the query index is stale but JSONL is intact:

```bash
evander-cli rebuild-audit-index --runtime-dir runtime --reset --json
```

For an MCP identity problem, compare the declared Evander session, protocol `mcp-session-id`, proof-token header, manifest expiry, and persistent binding. Reissuing a session is safer than hand-editing a binding when a guest was cloned or its session material was copied unexpectedly.

## Source trail

- `src/evander/gateway/server.py`, `src/evander/gateway/app.py`, and `src/evander/gateway/session_context.py` — MCP registration, middleware, catalog filtering, binding, and invocation context.
- `src/evander/session_auth.py` and `src/evander/repositories/mcp_transport_bindings.py` — proof-token issuance/verification and persistent transport binding.
- `src/evander/policy.py`, `src/evander/policy_config.py`, and `src/evander/models.py` — tool visibility, approval creation/validation/consumption, settings, and durable records.
- `src/evander/approval_notifications.py` and `src/evander/admin/services/approval_audit_service.py` — notifications, operator decisions, queue health, and audit context.
- `src/evander/audit.py` and `src/evander/repositories/audit_index.py` — canonical JSONL append and queryable SQLite index.
- `tests/test_gateway.py`, `tests/test_policy.py`, `tests/test_approval_notifications.py`, and `tests/test_session_tool_approval_leases.py` — gateway, exact binding, notification, and lease behavior.


# 8. Workflow Runtime and Orchestration

> **At a glance.** Evander workflows are validated event graphs. Triggers publish durable events; gates shape control flow; queries read data; effects change state; and completion events continue the graph. Two workers split observation from execution and share a SQLite-backed queue/journal by default. Node execution is idempotent at the event-and-node boundary, while external providers must tolerate recovery of a previously running operation. Invalid hot reloads retain the last-known-good graph.

## The four node families

A workflow graph contains globally unique node IDs from four families:

| Family | Responsibility | Example |
|---|---|---|
| Trigger | Observe time, a provider, webhook, or internal event and publish a source event | `trigger:ticket:created`, `trigger:cron`, `trigger:event` |
| Gate | Decide or reshape flow without being the external business effect | `gate:filter`, `gate:dedupe`, `gate:map`, `gate:for`, `gate:merge` |
| Query | Read data and publish structured outputs | `query:session:list`, `query:ado:work-items` |
| Effect | Change host or external state and publish completion outputs | `effect:agent:create`, `effect:agent:prompt`, `effect:git:promote-agent-branch` |

Edges connect source nodes and output ports to target nodes. A completion is itself an event, so a query or effect does not call the next node directly. This preserves queue durability and lets the graph resume after process failure.

Northstar's telemetry-retry flow might be summarized as:

```text
ticket created
  -> filter mission-control
  -> dedupe work-item start
  -> query active sessions
  -> filter none active
  -> create Imani's agent
  -> clone managed repo
  -> prompt implementation
  -> await/pin Git result
  -> Rowan review
  -> Riley publication gates
```

This is a representative composition. The exact providers, gates, and review/release graph are operator-defined, and Team Add-on chapters explain the additional task and governance nodes.

![Durable workflow path from trigger observation through gates, queries, and effects](assets/evander-user-manual/diagrams/06-workflow-pipeline.svg)

<p class="figure-caption">Figure 8.1 — Workers coordinate through durable event and execution records instead of an in-memory call chain.</p>

## Packs, graphs, and IR

A workflow pack has a `pack_id`, `pack_version`, optional description, graph metadata, and a versioned intermediate representation. Each graph has one canonical directory path, optional user IDs, and a list of node IDs. Each IR node wraps a typed node envelope plus bindings and optional retry/idempotency policy; edges are part of the IR.

`config/trigger-orchestrator/config.json` selects workflow packs and can also contain inline graphs or IR. During load, Evander:

1. reads global orchestration settings;
2. migrates supported older node/port shapes;
3. composes configured packs and inline IR;
4. appends the hidden built-in system pack;
5. resolves configured workflow transforms;
6. materializes typed provider schemas;
7. validates graph, binding, directory, and communication-target constraints;
8. computes the compiled runtime configuration.

The hidden system pack is runtime-managed and not an editable operator graph. Node IDs and webhook slugs must be globally unique across active configuration. A graph cannot claim multiple directory paths.

Checked-in example packs illustrate supported combinations but are not automatically active. Live operator packs may be ignored by Git and edited through Admin Web. Use its revision and lock behavior; writing a stale file directly can defeat concurrency protection even if the JSON itself is valid.

![Admin Web workflow editor showing the synthetic mission-control telemetry-retry graph](assets/evander-user-manual/screenshots/admin-workflow.png)

<p class="figure-caption">Figure 8.2 — The editor is the authoring projection over a revisioned graph; runtime events and executions remain durable records underneath. Sanitized Northstar fixture.</p>

## Compilation and bindings

Compilation turns authoring IR into concrete trigger, gate, query, effect, and edge definitions. Provider schemas validate node configuration before execution. Graph validation rejects missing endpoints, illegal trigger inbound edges, accidental cycles, invalid ports, duplicate IDs, and inconsistent graph membership.

Bindings carry upstream output into downstream configuration. A value such as:

```text
${{node.output}}
```

is resolved from declared upstream output context. Validation uses provider output schemas and graph ancestry to catch references that are unavailable, misspelled, or not dominant on the path. Execution stores a binding trace and a hash of the resolved configuration so an operator can see what value the node actually received.

Bindings are data selection, not arbitrary Python or shell evaluation. An effect that intentionally executes a command has its own typed provider and policy boundary; a generic binding cannot become host code.

## Providers and extensions

Node type names resolve through the orchestration provider loader. A profile determines the built-in provider set, and configuration can add explicit Python modules or enabled entry points. Add-ons contribute providers through their registration surfaces. Provider factories receive shared administration/runtime services so they operate on the same sessions, Git registry, policy, and stores as the CLI and Admin Web.

The graph's directory path is passed into provider construction and effective configuration resolution. A single active graph set can therefore contain projects using different ADO projects, backends, contacts, or Team state without duplicating a daemon per directory.

Provider initialization failures are isolated and logged where possible; one bad optional provider should not erase a valid current graph. A node whose provider cannot be constructed cannot execute successfully, and its failure remains visible in execution evidence.

## The trigger orchestrator

The trigger worker runs as:

```bash
evander-cli trigger-orchestrator --runtime-dir runtime
```

or with `--once` for one diagnostic cycle. A single-instance polling kernel owns its lock and local wake mechanism. Each cycle:

1. checks workflow and provider configuration for a valid reload;
2. publishes due scheduled internal events;
3. selects enabled, non-system triggers;
4. constructs each provider using the graph's effective directory context;
5. polls for candidate events;
6. validates provider output and publishes each stable event ID, using an additional idempotency key when the publisher supplies one;
7. acknowledges the provider item only after durable publication;
8. records cursor state and wakes the effect orchestrator.

Provider initialization and polling failures are logged per trigger so the cycle can continue with other definitions. A publish failure prevents acknowledgement of that item. Redelivery is therefore possible and expected; stable idempotency is part of a correct trigger provider.

The default polling interval is five seconds with up to five seconds of configured jitter. A successful publish lets the loop return promptly rather than sleeping through available work.

## The durable orchestration store

`OrchestrationCoreConfig` defaults to:

- SQLite backend at `runtime/orchestration.db`;
- 30-day orchestration retention;
- WAL checkpoint mode `truncate`;
- a checkpoint threshold of 64 MiB, interval of 300 seconds, and checkpoint busy timeout of 1,000 ms;
- maximum graph depth 16;
- maximum 200 events per effect-worker cycle.

The database unifies the trigger-event queue, workflow events, gate and effect/query executions, and workflow configuration snapshots behind repository interfaces. Provider and worker cursors remain in their repository-backed JSON paths (for example `trigger-cursors-*.json` and `trigger-orchestrator-cursors.json`). SQLite WAL supports independent trigger, effect, and Admin readers/writers while the repository applies a busy timeout and transactions.

The `json` backend remains a rollback/compatibility option with legacy paths for queue, cursors, journals, execution records, and snapshots. It is not the preferred concurrent production backend. `evander-cli migrate-orchestration-store` imports legacy state through a supported migration flow.

## The effect orchestrator

The effect worker runs as:

```bash
evander-effect-orchestrator --runtime-dir runtime
```

It resets stale queue `inflight` markers at startup, reloads the current graph, retries completion-pending publication, claims queued events, and routes them along matching source node/port edges. Gates execute through registered gate providers; queries and effects run through a bounded executor so short independent operations can overlap.

For every dispatched node, the worker records an idempotency key of:

```text
<event-id>::<node-id>
```

The execution record includes execution and idempotency IDs, source event/trace/node/type, status and timestamps, resolved configuration and hash, input, outputs, published-completion state, error, binding trace, and configuration revision.

If a non-running execution already exists for that key, the node is not run again. If the record is still `running` after a process restart but no in-process future exists, the worker resubmits the provider using the stored resolved configuration. That recovery rule means Evander does **not** promise exactly-once external side effects. Providers that call external systems need their own stable idempotency key or reconciliation behavior.

## Completion events and recovery

When a query, effect, or gate finishes, the worker builds a deterministic completion event and publishes it back to the queue. Downstream edges select its output port. The original execution is not considered fully propagated until completion publication is recorded.

If the provider succeeds but queue publication fails, the record becomes `completion_pending` with the intended outcome. Later cycles retry publication rather than rerunning the successful provider. This distinction prevents a transient queue error from turning an already completed external effect into an immediate duplicate.

There is still an unavoidable crash window around external systems that do not support idempotency: the process can die after the remote side effect but before the local success record is durable. Provider design and reconciliation must account for it. ADO work-item updates, Git promotion, communication sends, and release operations should use their provider-specific stable identities and leases.

## Cycles, depth, and event budgets

Graphs are acyclic by default. A feedback or iteration edge must set `allow_cycle: true`; accidental cycles and self edges are rejected. Deliberate cycles are still bounded by `max_graph_depth`, and each worker cycle claims only `max_events_per_cycle` records.

The Northstar graph may use a declared cycle for Rowan's change request to return to Imani's implementation step. The edge declares intent; dedupe, iteration state, task state, and depth limits prevent it from becoming an unbounded hot loop. An explicit cycle is permission to route again, not proof that the business condition will eventually terminate.

## Configuration revisions and hot reload

Both workers fingerprint the global configuration, referenced pack files, and communication-target SQLite files (including WAL/SHM state). When the fingerprint changes, the reloader parses, composes, validates, and compiles a candidate.

If successful, the new current configuration and provider revision take effect. Workflow snapshots and node records retain revision context so existing evidence remains intelligible. If invalid, the worker logs the exception and keeps the last-known-good configuration. A bad edit therefore does not replace a working graph with an empty one.

Effect-worker store-binding changes are treated conservatively: switching the live queue/execution store is deferred until old queued/inflight work drains. Provider-loader changes rebuild provider factories. Operators should still schedule major graph or store migrations deliberately rather than relying on opportunistic hot reload.

## Team Add-on on the same runtime

The Team Add-on ships a workflow pack with core trigger/gate/effect/query shapes such as Team outbox publication, due-agent wake, governance-manager wake, ephemeral agent lifecycle, Git promotion/consolidation, release recording, and session-routing queries.

Team state remains add-on-owned, but execution remains core-owned:

- Team decides that Imani is due to work and emits/publishes a durable intent.
- Core workflow creates or resumes the session and executes the prompt.
- Core completion becomes a Team-visible result/outbox event.
- Team governance decides whether Rowan or Riley should act next.

This seam is important during diagnosis. A Team task can exist without a core wake having been published; a core workflow run can be healthy while Team governance intentionally withholds the next dispatch.

## Failure behavior

| Symptom | Durable state to inspect | Interpretation |
|---|---|---|
| Trigger provider repeatedly returns same item | Queue idempotency and provider cursor/ack | Publication or acknowledgement may be failing; duplicates should converge. |
| Other triggers work, one does not | Per-trigger logs and effective provider config | Provider construction/poll failure is isolated. |
| Event is queued but no node runs | Effect service, source node/port, graph revision | Worker stopped, no matching edge, or graph/source changed. |
| Gate “lost” an item | Gate execution and output port | It may have deliberately filtered, deduped, serialized, or routed it. |
| Node is `running` after restart | Execution record and current inflight map | Recovery may resubmit; check provider idempotency before manual action. |
| Node succeeded but downstream is absent | `completion_pending` or missing matching edge | Retry completion publication; do not rerun the external effect first. |
| New workflow edit has no effect | Worker reload log and current revision | Candidate may be invalid; last-known-good remains active. |
| Cycle stops | Depth/event budget and `allow_cycle` | Safety bound or undeclared cycle prevented further routing. |
| Bind expression fails | Binding trace/error and provider schema | Upstream output is missing, wrong port/path, or not valid on every route. |
| Team task is open but no agent wakes | Team outbox/wake/governance plus core queue | Cross the add-on/core seam and find which durable handoff is missing. |

## Practical runbook

Validate each worker independently with one-cycle commands:

```bash
evander-cli trigger-orchestrator --runtime-dir runtime --once
evander-effect-orchestrator --runtime-dir runtime --once
```

Then inspect service logs without leaving duplicate workers running:

```bash
sudo systemctl status evander-trigger-orchestrator.service
sudo systemctl status evander-effect-orchestrator.service
sudo journalctl -u evander-trigger-orchestrator.service -n 200 --no-pager
sudo journalctl -u evander-effect-orchestrator.service -n 200 --no-pager
```

For one run, collect source event ID, trace ID, graph ID, configuration revision, source node/port, target node, node idempotency key, execution status, completion event ID, and workflow run/session IDs. In Admin Web, compare the graph revision shown for the run with the current editor revision.

Do not delete a `running` record until you know whether its external effect occurred. Do not force a second effect merely because completion publication is pending. Prefer provider reconciliation, supported retry/force-complete controls for the specific node type, or recovery of queue publication.

Before publishing a workflow edit, run validation and resolve every error. If another operator holds the workflow lock or the revision changed, reload and merge through Admin Web instead of overwriting the pack.

## Source trail

- `src/evander/config.py` — orchestration defaults, paths, validation, limits, and provider-loader configuration.
- `src/evander/workflow/packs.py`, `compiler.py`, `validation.py`, `binding.py`, and `runtime_config.py` — pack composition, compilation, graph/binding checks, and last-known-good reload.
- `src/evander/daemons/trigger_orchestrator.py` and `src/evander/orchestration/kernels.py` — trigger polling, single-instance loop, publication, acknowledgement, and wakeup.
- `src/evander/daemons/effect_orchestrator.py` — graph routing, node execution, idempotency, completion-pending recovery, cycles, and revision evidence.
- `src/evander/triggers/queue.py` and `src/evander/repositories/orchestration_db.py` — JSON/SQLite queue and shared orchestration persistence.
- `src/evander/orchestration/adapters/provider_factories.py` and `src/evander/workflow/plugins.py` — provider construction and extension loading.
- `tests/test_workflow_packs.py`, `tests/test_workflow_compiler.py`, `tests/test_workflow_validation.py`, `tests/test_trigger_orchestrator.py`, `tests/test_effect_orchestrator.py`, and `tests/test_workflow_orchestrator_live_reload.py` — workflow contract and recovery tests.


# 9. Execution Backends

> **At a glance.** Evander separates the decision to run an agent from the machinery that runs it. The supported default, `libvirt_vm`, gives a session a disposable virtual machine. The additive `container_worker_vm` backend sends narrowly described jobs to one configured worker VM, where Docker containers are disposable but selected session state is durable. Both paths share session, workflow, policy, relay, Git, and observability contracts; they do not offer identical isolation or lifecycle semantics.

## The mental model: a control plane and an execution plane

The host control plane decides *what* may happen. It owns the session manifest, effective directory configuration, tool policy, credentials, managed Git identity, workflow state, and audit trail. An execution backend decides *where and how* a permitted operation runs. This distinction is one of Evander's most useful design seams: a workflow can use portable `effect:agent:*` nodes without embedding `virsh`, Docker, host paths, or worker credentials in its graph.

![The Evander control plane dispatches the same portable agent lifecycle to either a libvirt VM or the authenticated container worker.](assets/evander-user-manual/diagrams/07-execution-backends.svg)

<p class="figure-caption">Figure 9.1 — Backend selection changes the execution mechanism, not the host-owned session, policy, relay, Git, or evidence contracts.</p>

The shared backend protocol has six operations:

| Operation | Meaning |
|---|---|
| `prepare` | Validate and normalize an `ExecutionSpec` into a backend-specific prepared target. |
| `start` | Start a prepared target and return a durable handle where applicable. |
| `inspect` | Return normalized status and backend metadata. |
| `stop` | Stop active execution without necessarily deleting its durable state. |
| `destroy` | Remove the backend resource or mark an explicit cleanup outcome. |
| `get_log_snapshot` | Read a bounded, operator-facing view of the agent log. |

Normalized status values are `unknown`, `preparing`, `starting`, `running`, `stopped`, `failed`, and `destroyed`. The Admin surfaces may expose more specific retained container-session states such as `abandoned`, `cleanup_failed`, and `cleaned_up`; those are lifecycle evidence, not additional values in the generic execution protocol.

`ExecutionSpec` carries execution-defining fields explicitly: backend, session and target identity, directory, command, image, resolved clone source and branch, working directory, resource limits, persistence mode, and workspace mode. The free-form `metadata` field is sanitized context. It is not an escape hatch for commands, credentials, host repository paths, or other execution inputs.

## The two current backends

### `libvirt_vm`: the default

The libvirt adapter deliberately wraps the established VM administration services. `prepare` resolves a target VM name directly or through the session's `vm_id`; lifecycle and log calls then delegate to the focused VM service. Power states are normalized into the shared execution status model.

A VM session normally has:

- a qcow2 overlay under `/var/lib/libvirt/images/evander`;
- cloud-init state derived from `config/cloud-init-user-data.yaml`;
- an unprivileged `agent` user for normal work and a privileged `agent-admin` setup user;
- session routing and relay variables in `/etc/evander/session.env`;
- a guest artifact root at `/artifacts`, linked from `/home/agent/artifacts`;
- optional toolchains installed after launch by explicit bootstrap scripts.

The base image is intentionally small. Python, .NET, NuGet, Playwright, frontend dependencies, and SQL Server are opt-in bootstraps rather than assumptions baked into every VM.

Directory-scoped `vm.json` may define `libvirt_vm_templates` and `libvirt_vm_default_template`. `effect:agent:create.vm_image` selects an approved alias; Evander records the resolved selection with the session. The catalog validates the alias and map shape, not the actual image. The operator remains responsible for verifying that a referenced file exists, is readable by `libvirt-qemu`, is qcow2, and accepts cloud-init. Supplying a Docker image in this field is a category error.

### `container_worker_vm`: disposable jobs on a durable worker

The container backend calls the authenticated HTTP API of `evander-worker-daemon` on one configured worker VM. It is currently a single-worker execution path, not a scheduler. The worker creates durable per-session directories and starts disposable Docker jobs against them.

The portable agent effects map as follows:

| Portable effect | Container-worker behavior |
|---|---|
| `effect:agent:create` | Create the host session manifest and prepare worker-side session directories and agent home. |
| `effect:agent:clone-repo` | Resolve a `managed_repo_id` on the control plane and clone into `/home/agent/repos/<repo-name>`. |
| `effect:agent:copy-files` | Write validated paths below the durable `/home/agent`. |
| `effect:agent:exec` | Run a deterministic setup command in a transient job. |
| `effect:agent:prompt` | Start the provider harness in a disposable job, retain outcome/log evidence, then remove the job. |
| `effect:agent:destroy` | Delete worker jobs and session files while retaining host cleanup evidence. |

The selected container image comes from the directory-scoped `container_worker_images` alias map. `container_worker_default_image` is the fallback for older or unspecified sessions. Once an alias is resolved, the exact image reference is stored with the session and reused; a later catalog change does not silently change an existing agent's runtime. Claude sessions normally select an appropriate Claude runner image and `model_provider_mode: "anthropic"`.

Container sessions are configured by mounting their durable agent home, not by rebuilding an image. Codex or Claude configuration, shell profile, MCP routing, relay URLs, and managed skills are written into that home. Large toolchains and immutable caches belong on worker-level read-only mounts; mutable caches remain session-scoped.

The default security posture is no general container network. A non-`none` Docker network mode is valid only when the worker deployment explicitly enables its egress policy and supplies an allowlist. The current backend does not provide worker scheduling, autoscaling, Kubernetes, general secret transport, or a general internet path. The worker bearer token stays on the control plane. Provider keys stay behind host relays.

SQL Server is also intentionally separate. `effect:agent:create` rejects the VM-style `mssql=true` and `mssql_init_root` options for container sessions. Container agents use the worker-host SQL Server through the scoped MCP provisioning operations `mssql_provision_database`, `mssql_list_databases`, and `mssql_drop_database`.

## A Northstar Platform run from intent to cleanup

Suppose Priya, project lead for Northstar Platform's `mission-control` project, assigns Imani the work item “add telemetry retry behavior.”

1. The trigger selects a directory and creates or reuses a session manifest. Effective provider configuration selects `libvirt_vm` unless the session or directory explicitly selects `container_worker_vm`.
2. `effect:agent:create` emits `metric_agent_create_backend_path` and prepares the backend. A VM path launches or associates a VM. A container path prepares the worker session and records the selected image.
3. `effect:agent:clone-repo` receives only `mission-control`'s `managed_repo_id`. The control plane resolves an internal, restricted SSH source and grants Imani's session either read-only or session-scoped write access.
4. Bootstrap and prompt effects run. Model calls go to the host relay; MCP calls go to the gateway; package restores go to package relays or mounted caches.
5. The prompt result and logs are recorded. For a container job, the execution mapping in `runtime/container-worker-executions.db` connects Evander's execution/session identity to the worker job ID.
6. The workflow promotes only the pinned Git commit it received from the managed Git event. Backend identity does not change that publication rule.
7. Destruction removes the disposable compute. Host session and cleanup records remain long enough to explain what happened.

This flow illustrates an important invariant: *backend portability does not weaken provenance*. A worker job may be short lived, but the session, Git SHA, workflow run, dispatch, policy decision, and model-usage correlation remain host-owned.

## Persistence and cleanup semantics

Stopping and destroying are intentionally different. Stopping a VM leaves its disk. Stopping a worker job leaves the durable session home. Destroying a VM removes the linked VM through the VM service. Destroying a container session asks the worker to remove jobs, files, Git keys, and session-owned databases, then retains the host session manifest and execution mapping as a cleanup tombstone.

Do not erase a `cleanup_failed` record to make a dashboard look tidy. It is the evidence needed to retry worker cleanup and to discover leaked files, jobs, keys, or databases. The container repository accepts the old JSON filename as configuration and one-time import input, but current live mappings are SQLite-backed.

Chat Assistant mappings are also backend-aware. Libvirt uses one global assistant VM/session with distinct per-user agent thread IDs. Container execution uses a distinct assistant session per user. The durable mapping in `runtime/chat-assistant-session-mappings.json` makes concurrent “open chat” and “first message” initialization converge instead of creating duplicates.

## Failure behavior

| Symptom | What it means | First checks |
|---|---|---|
| `unknown` for a VM | No matching VM summary or session-to-VM mapping was found. | Inspect the session manifest, `vm_id`, and `virsh list --all`. |
| Container backend construction fails | `container_worker_base_url` is missing or invalid. | Inspect effective directory provider configuration, not only root defaults. |
| Worker returns unauthorized/unreachable | Bearer token, URL, routing, service, or firewall is wrong. | Check the daemon unit, `/healthz`, token env name/value, and control-plane-to-worker reachability. |
| Prompt never starts | Queue persistence or worker dispatch failed before the harness ran. | Find `metric_prompt_enqueue_failed`, then inspect the prompt store and worker session state. |
| Wrong image or toolchain | The session recorded a different image alias/ref, or the worker cache is incomplete. | Inspect session metadata and the worker image catalog; do not change the catalog and assume an existing session changed. |
| `cleanup_failed` | Worker deletion did not complete. | Preserve the record, inspect its cleanup error, retry after fixing reachability or worker state. |
| Model unavailable | The provider rejected the configured model before useful work. | Inspect sanitized wake/job failure metadata and the directory/session model override. |
| VM starts but guest tools are stale | Optional bootstrap state no longer matches the host. | Re-run the relevant idempotent bootstrap script. |

## Practical runbook

Start with the broad control-plane view:

```bash
evander-cli status --runtime-dir runtime --json
evander-cli list-vms --all
evander-cli list-sessions --all
```

For a VM-backed session:

```bash
evander-cli start-vm <vm-name>
evander-cli logs <vm-name>
evander-cli shutdown-vm <vm-name>
```

For the configured worker, exercise the backend before trusting a workflow:

```bash
evander-cli execution smoke-container-worker --json
evander-cli execution list-container-worker-executions
evander-cli execution status <execution-or-session-id>
evander-cli execution logs <execution-or-session-id>
```

`execution start-container-job` and `smoke-container-worker` explicitly construct the container backend. The generic `status`, `logs`, `stop`, and `destroy` commands infer it only when the target is present in the container execution mapping, unless `--backend container_worker_vm` is supplied. These commands never change the configured default backend.

When cleanup fails:

1. Record the session ID, execution ID, worker job ID, last observed status, and cleanup error.
2. Restore worker connectivity or fix the worker-side resource problem.
3. Retry the explicit destroy operation.
4. Confirm the Admin Sessions view reports `cleaned_up` and that no worker job, session directory, Git authorization, or session database remains.
5. Only then apply the configured retention policy to host tombstones.

For deployment details and a deeper worker security checklist, continue with `docs/container-worker-backend.md`. For libvirt template preparation, use `docs/libvirt-vm-template-operations.md`.

## Source trail

- `src/evander/execution/backend.py` and `src/evander/execution/models.py` — shared backend protocol, execution specification, handles, and status model.
- `src/evander/execution/libvirt_backend.py` — libvirt adapter and power-state normalization.
- `src/evander/execution/container_worker_backend.py` and `src/evander/execution/container_worker_repository.py` — worker requests, durable mappings, logs, and cleanup outcomes.
- `src/evander/effects/providers/agent_create.py`, `agent_clone_repo.py`, `agent_prompt.py`, and `agent_destroy.py` — portable agent lifecycle dispatch.
- `src/evander/worker/` and `ansible/worker.yml` — worker API, Docker runtime, durable store, sandbox policy, and deployment.
- `docs/execution-backends.md`, `docs/container-worker-backend.md`, and `docs/libvirt-vm-template-operations.md` — supported operator contracts.
- `tests/test_libvirt_execution_backend.py`, `tests/test_container_worker_execution_backend.py`, `tests/test_worker_daemon.py`, and `tests/test_vm_to_container_migration.py` — backend and migration behavior.


# 10. Networking, Relays, Packages, and Secrets

> **At a glance.** An Evander guest is useful precisely because it can reach *selected capabilities* without receiving the credentials behind them. The guest or worker container talks to private, host-owned endpoints for MCP, model inference, packages, Git, and allowlisted HTTP targets. The host resolves the session and directory policy, supplies upstream credentials where that relay owns them, strips transport secrets, and records the consequential action. Default-deny network forwarding supports this design; it does not replace application-level session and policy checks.

## The mental model: doors, not an open corridor

Think of the isolated execution environment as a workshop with a few labelled service doors:

- MCP on port `8000` is the tool-capability door;
- the model relay on `11434` is the inference door;
- the NuGet relay on `11666`, npm audit facade on `11881`, and optional Nexus cache on `8081` are package doors;
- the HTTP target relay on `11777` is a session-specific door to a named external origin;
- host SSH on `22` is the managed Git transport door;
- apt-cacher-ng on `3142`, when enabled, is a worker-provisioning door rather than an agent application dependency.

The checked-in firewall example allows those host TCP ports and drops other forwarded traffic from the libvirt bridge. A deployment may deliberately change the list, but adding a port is a security decision: it creates network reachability that the application may not further mediate. Container-worker networking has its own worker-side egress policy and must be configured explicitly; the libvirt firewall is not a container policy.

MCP is therefore a central capability boundary, but not the only reachable host service. Every direct relay must enforce its own narrow contract.

## Session routing and the guest environment

Cloud-init writes `/etc/evander/session.env`, and the shell profile loads it for the `agent` user. The file contains routing data such as session identity, MCP URL/token, model relay URL, HTTP-target relay prefix, and package locations. It also sets a placeholder `OPENAI_API_KEY` so tools that insist on an API-key variable can start. That placeholder is not the upstream provider secret.

Current bootstrap scripts still read `/etc/agent-sandbox/session.env` as an upgrade compatibility fallback, but `/etc/evander/session.env` is the current path. A shell that was already open before a bootstrap repair may need:

```bash
source ~/.profile
```

Session-bearing relay requests are authenticated with the session's MCP token. The token may be carried in a supported header, query parameter, or generated auth path, depending on the client. Relays remove it from the upstream query and headers. The session manifest must exist and be active; the model relay also rejects expired sessions.

## Model inference relay

`evander-openai-relay` exposes a session-scoped model surface while resolving the actual provider from effective directory and session configuration. Public OpenAI is the default when no mode override is supplied. The current modes are:

| Mode | Upstream shape | Current boundary |
|---|---|---|
| `openai_public` | OpenAI-compatible HTTP, SSE, and supported Responses WebSocket | Host injects the OpenAI key. |
| `azure_openai` | Azure OpenAI deployments selected through the model-to-deployment map | Host injects the Azure key; managed identity is not part of this implementation. |
| `local_openai` | OpenAI-compatible HTTP/SSE endpoint such as Ollama | WebSocket/realtime is rejected; a configured placeholder bearer may be used upstream. |
| `anthropic` | `GET /v1/models`, `POST /v1/messages`, and `POST /v1/messages/count_tokens` | Host injects `x-api-key`; WebSocket/realtime is rejected. |

The general OpenAI-compatible HTTP allowlist is `GET /v1/models` and `POST` to `/v1/responses`, `/v1/responses/compact`, `/v1/chat/completions`, `/v1/completions`, or `/v1/embeddings`. Only `/v1/responses` is accepted on the OpenAI-compatible WebSocket surface. Unsupported paths fail before they become arbitrary proxy access.

The relay removes guest `Authorization`, `x-api-key`, hop-by-hop headers, content length/encoding, host, and the Evander session token before building the upstream request. It then supplies the provider credential selected by host configuration. For streaming responses, the upstream client remains alive until the response iterator finishes, so raw SSE bytes can be forwarded without truncating the stream.

Hosted OpenAI web search is denied unless the session manifest explicitly sets `web_search_enabled: true`. The relay examines both HTTP request bodies and WebSocket messages, rather than trusting a client-side Codex flag alone. Model use is recorded in `runtime/model-usage.db` when the provider supplies token usage; completed responses without a usage block are recorded as `usage_unavailable`.

### Local-model routing is still a relay boundary

A host-local model is not a reason to connect the guest directly to Ollama. Keep the agent pointed at Evander's session-scoped relay and configure `local_openai_api_base_url` on the host. If the relay listens on wildcard port `11434`, it rejects the ambiguous default of calling a loopback service on that same port. Bind the relay to the guest-facing address or move the local model to another private port, such as `127.0.0.1:11435`.

## Package paths

### NuGet

`evander-nuget-relay` exposes two feed roots:

```text
/nuget/nuget-org/...
/nuget/azure/...
```

The public feed needs no host credential. The Azure Artifacts feed reads the PAT from the configured host environment variable and adds Basic authorization upstream. Service-index JSON is rewritten so clients continue through the relay; package bytes are streamed. Origin-rooted Azure packaging paths are accepted only for feed IDs learned from the configured service index, preventing the relay from becoming an arbitrary Azure DevOps proxy.

`bootstrap_nuget_vm.sh` creates a guest NuGet configuration with writable caches in `/home/agent/.nuget`, the relay feeds, and an optional read-only fallback package directory at `/opt/evander/nuget-cache-ro`. Container workers use their own worker-level read-only cache and session-local writable cache.

### PyPI, npm, apt, and npm audit

The optional Nexus service on `8081` is the worker/application cache for PyPI and npm. It is separate from apt-cacher-ng on `3142`, which Ansible uses while provisioning a worker VM.

The npm audit protocol is a special case. `evander-npm-audit-facade` sends ordinary package metadata and tarball requests to the configured package registry, normally Nexus, but sends only the two npm audit POST endpoints to the audit registry:

```text
/-/npm/v1/security/advisories/bulk
/-/npm/v1/security/audits/quick
```

This lets `npm audit` work without turning the worker's npm registry setting into a direct public package route. The facade does not inject an npm credential.

## Session-scoped HTTP targets

The HTTP target relay differs deliberately from the model and NuGet relays: **it forwards guest-managed authorization and cookies**. It does not inject a host secret for the target.

Each session manifest contains named HTTP-target metadata, normally under `metadata.http_targets`. A request uses the session and alias:

```text
$HTTP_TARGET_RELAY_BASE_URL/session/<session-id>/target/<alias>/path
```

The relay:

1. authenticates the Evander session token;
2. resolves the alias only from that session manifest;
3. combines the configured base path with validated, traversal-safe path segments;
4. removes the Evander token and hop-by-hop headers but preserves application headers, including guest `Authorization`;
5. makes one request without automatically following redirects;
6. rewrites a same-origin redirect back through the relay and rejects a redirect outside the configured origin.

This is useful while Imani verifies the telemetry retry behavior in Northstar Platform's provisioned `mission-control` environment. A workflow can add `mission-control-api` and `mission-control-ui` aliases to the session, while the agent authenticates to those applications using credentials it legitimately holds. The public environment URL does not become a general network permission.

## Where secrets belong

Provider configuration stores *environment-variable names*, URLs, IDs, and non-secret routing metadata. The environment of the relevant host service stores the values. Typical examples are `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `AZURE_DEVOPS_PAT`, `AZURE_ARTIFACTS_PAT`, `DISCORD_BOT_TOKEN`, `EVANDER_WORKER_TOKEN`, and bridge/provider tokens.

Keep secrets out of:

- session manifests and workflow payloads;
- Git remote URLs, config, ref names, and registry records;
- guest images and cloud-init templates;
- command arguments and logs;
- add-on state and communication-target addresses.

The repo-root `secrets.env` can be loaded by supported systemd templates, but it is still an operator-managed secret file. Check its ownership and mode. A health endpoint may report whether a key was loaded; it must not return the key.

There are narrow exceptions where a guest receives a short-lived or scoped credential: the MCP session token, a session-scoped restricted Git key, a one-time response/consultation token, or an ephemeral OpenAI Realtime client secret. These are capabilities with a bounded audience and purpose, not copies of the long-lived host credential.

## Failure behavior

| Failure | Expected behavior | Interpretation |
|---|---|---|
| Missing model provider key | Relay returns a service error before upstream use. | Fix the service environment named by effective provider config. |
| Expired or bad session token | Session relay request is rejected (`401`/`403` or WebSocket policy close). | Repair session/bootstrap state; do not bypass session routing. |
| Hosted web search requested but disabled | Relay returns a policy error and logs the denial. | Enable only on the session that genuinely needs it. |
| Unsupported provider path | Relay returns `501`, validation error, or WebSocket policy close. | Use a supported API; the relay is not a generic reverse proxy. |
| Azure NuGet PAT missing | Public NuGet may work; Azure feed returns `503`. | Set the configured PAT environment variable for the NuGet unit. |
| HTTP target alias missing | Relay returns `404`. | Add the alias through the session/workflow metadata contract. |
| HTTP target redirect changes origin | Relay returns `502` rather than following it. | Correct the target base URL or application redirect behavior. |
| Package restore misses cache | Restore may fail because direct internet is unavailable. | Prewarm the cache or make the appropriate relay/cache reachable. |
| SSE transport closes after final bytes | Relay logs `relay_stream_transport_terminated`; the client may still have a complete response. | Correlate chunks/bytes and provider events before treating it as data loss. |

## Practical runbook

First verify the listeners locally on the control plane:

```bash
curl -fsS http://127.0.0.1:11434/healthz
curl -fsS http://127.0.0.1:11666/healthz
curl -fsS http://127.0.0.1:11777/healthz
curl -fsS http://127.0.0.1:11881/healthz
```

Then verify service and firewall state:

```bash
sudo systemctl status evander-openai-relay.service
sudo systemctl status evander-nuget-relay.service
sudo systemctl status evander-http-target-relay.service
sudo systemctl status evander-npm-audit-facade.service
sudo nft list table inet evander
```

Inspect relay logs without printing service environments:

```bash
sudo journalctl -u evander-openai-relay.service -n 200 --no-pager
sudo journalctl -u evander-nuget-relay.service -n 200 --no-pager
```

Finally test *from the execution environment*, because host-local success does not prove guest or worker routing. For Codex in a VM:

```bash
source ~/.profile
codex exec --skip-git-repo-check -C /tmp "Reply with exactly: relay works"
```

For NuGet, inspect the generated sources and perform a restore that is expected to be satisfiable by the relay/cache:

```bash
dotnet nuget list source
dotnet restore <solution-or-project>
```

When diagnosing, preserve the separation of concerns: a DNS or firewall failure is not an MCP policy failure; a `401` from the HTTP target application is not an Evander token failure if the relay accepted the request; a provider `429` is not worker unreachability. Follow one request from session identity, to relay decision, to upstream response.

## Source trail

- `config/firewall.env.example` and `scripts/apply_firewall.sh` — guest-facing host-port defaults and nftables forwarding policy.
- `config/cloud-init-user-data.yaml` and `scripts/bootstrap_codex_vm.sh` — guest session environment and relay-aware Codex configuration.
- `src/evander/relays/openai.py` — session authentication, provider routing, endpoint allowlists, web-search policy, streaming, and usage capture.
- `src/evander/relays/nuget.py` and `scripts/bootstrap_nuget_vm.sh` — public/private feed proxying, URL rewriting, and guest NuGet layout.
- `src/evander/relays/http_target.py` — session aliases, guest-managed auth, path validation, and redirect confinement.
- `src/evander/relays/npm_audit_facade.py` — split npm package/audit routing.
- `tests/test_openai_relay.py`, `tests/test_nuget_relay.py`, `tests/test_app_relay.py`, `tests/test_npm_audit_facade.py`, and `tests/test_firewall_defaults.py` — relay and network boundary regression coverage.


# 11. Managed Git, Artifacts, and Code Consultations

> **At a glance.** Evander moves code with Git, moves result files through artifact stores, and answers bounded repository questions with disposable consultations. These are three different data planes. Managed Git preserves commit identity and controls ref publication. Published artifacts preserve immutable file bundles. A code consultation pins one managed commit, gives a short-lived worker read-only access to it, accepts one structured answer, and then removes the worker. Host paths are private implementation details in all three flows.

## The mental model: source, output, and inquiry

Use the narrowest transport that matches the thing being moved:

| Need | Correct surface | Durable identity |
|---|---|---|
| Clone, review, merge, or publish source code | Managed Git | `managed_repo_id` + ref + exact commit SHA |
| Preserve generated files or a deliverable | Published artifact | immutable artifact key + archive SHA-256 |
| Organize versions of published outputs | Artifact space | space path + logical artifact ID + version |
| Ask a bounded question about a code snapshot | Code consultation | consultation ID + managed repo + pinned commit |

A host checkout path is not a public repository identity. `/artifacts` is not a Git remote. An artifact key is not a mutable “latest” file. A consultation is not a persistent Team member. Keeping those distinctions sharp prevents a large class of provenance and cleanup errors.

![Managed Git carries a session-owned agent ref through an exact-SHA host pin, review, and lease-protected upstream publication.](assets/evander-user-manual/diagrams/08-managed-git.svg)

<p class="figure-caption">Figure 11.1 — Ref ownership and exact-SHA pinning keep guest work, host review, and canonical publication as separate authority transitions.</p>

## Managed repository identity and trust scope

Evander keeps one host-owned bare repository for each canonical upstream *within an owning directory trust boundary*. The registry is `runtime/managed-git-repositories.json`; bare repositories live below `runtime/managed-git/`. Consumers address a repository by a deterministic ID shaped as `mr_` plus 20 hexadecimal characters.

Registration normalizes equivalent GitHub and Azure DevOps SSH/HTTPS URLs. It strips credentials, query strings, fragments, and a trailing `.git` from generic identities. A local checkout uses its canonical `origin` when available, otherwise its resolved path establishes identity. Registration never rewrites the operator's checkout.

Directory inheritance matters:

- an owning directory makes its managed repository visible to descendants;
- a descendant registration of the same upstream reuses the ancestor-owned repository;
- siblings are separate trust boundaries and receive separate managed IDs even for the same upstream;
- an operation must be authorized against the repository owner, not merely against a caller-supplied child path.

The registry contains canonical identity, provider, display name, default branch, refresh source, host commit identity, internal repository location, timestamps, and active bindings. It must not contain provider credentials or temporary worktree paths.

## Ref ownership: one namespace, one writer

The bare repository is a controlled exchange, not a shared free-for-all.

| Namespace | Owner |
|---|---|
| ordinary `refs/heads/*` and tags | canonical refresh |
| `refs/heads/agents/<session-id>/**` and that session's notes | the authorized guest session |
| `refs/heads/review/**`, `refs/heads/team/**`, and `refs/evander/**` | host workflows |

Registration rejects an upstream that already uses reserved top-level branch prefixes such as `agents/`, `review/`, or `team/`.

Canonical refresh first fetches into an operation-scoped namespace under `refs/evander/refresh/`, then updates the ordinary refs in one `git update-ref --stdin` transaction. It does not prune agent, review, Team, note, or operation refs.

A writable guest receives a dedicated SSH key with a forced command. It can read only its authorized repository scope and write only its exact agent-branch namespace and notes ref. It cannot write ordinary branches, another session's branch, review/Team refs, tags, hooks, or configuration. A fetch-only session uses `git_access_mode: read-only` and receives no write path.

Guest clones include three helpers:

- `git sync-base` refreshes the expected base safely;
- `git push-agent` validates ancestry and pushes the current commit into the session namespace;
- `git agent-pr-note` attaches the structured PR note to the configured notes ref.

The bare repository's post-receive hook only enqueues a structured trigger event. It never receives an upstream token and never publishes to the canonical remote.

## Pinned promotion and publication

A `trigger:git:push` event names the managed repository, changed ref, old and new SHA, session, notes ref, default branch, and derived review branch. Downstream logic must carry the event's exact new SHA.

Promotion checks that the source agent ref still equals `expected_commit_sha`, then pins that object under a host-only operation ref in the same transaction. If the agent has pushed again, the event becomes `source_ref_moved`; Evander does not silently review the newer commit. Review and Team target updates use compare-and-swap.

Publication is equally strict. When Northstar Platform's release agent Riley publishes the reviewed change:

1. Fetch the upstream destination into an operation-local ref.
2. Verify that destination is an ancestor of the approved SHA.
3. Push the exact approved SHA with an explicit destination lease.
4. Serialize operations for the same destination while allowing unrelated destinations to proceed.

This makes a race visible instead of letting stale automation overwrite a newer upstream branch. PR creation may reuse an existing PR for the same source/destination, but it does not weaken the exact-SHA push contract.

### Ephemeral host worktrees

Most managed Git operations manipulate bare refs. Operations that genuinely require files create a unique detached worktree at:

```text
runtime/git-worktrees/<managed_repo_id>/<operation-id>/checkout
```

It starts from a pinned host-only ref. Hooks, LFS smudge, submodule initialization, inherited commit signing, credential prompts, and interactive prompts are disabled. A secret-free manifest and operation lock sit beside the checkout.

The worktree is scratch space, not an agent sandbox. Never execute agent-authored builds or tests there. Success, failure, cancellation, and normal termination remove the worktree, prune its registration, and compare-and-swap delete its operation ref. Startup and periodic maintenance reconcile abandoned worktrees only when their lock is free and they are older than the configured stale threshold, one hour by default.

## Artifact lifecycle

### Session artifacts

An agent stages output under `/artifacts` (or the configured `EVANDER_ARTIFACTS_DIR`). Paths must be relative, normalized, and free of empty, dot, or parent segments. Host-side readers open path components without following symlinks and require a regular file. These checks defend against a workspace symlink redirecting a supposedly safe artifact read outside its root.

Session artifacts are working output. They are not durable publication until a workflow or operator publishes them.

### Published artifacts

`effect:artifact:publish-artifacts` collects selected regular files, creates a `tar.gz`, and writes it to the host `PublishedArtifactStore` below `runtime/published-artifacts/`. A published key is immutable: trying to publish the same key again fails rather than replacing history.

Metadata includes the key, storage ID, archive filename and format, byte count, SHA-256, publication time, source session, execution backend, source file count/bytes, and caller metadata. The storage ID is derived from the key, while the recorded SHA-256 verifies the actual archive bytes. Import copies the stored archive into a target session and extracts only safe archive members below its artifact root.

For Imani's telemetry retry work, Northstar Platform might publish:

```text
northstar-platform/mission-control/telemetry-retry/dispatch-8/test-report
```

That key always identifies that exact archive. A newer report receives a new key.

### Artifact spaces

An artifact space is a mutable, permissioned index over immutable published artifacts. Paths are normalized absolute logical paths such as `/northstar-platform/mission-control/releases`. Each logical `artifact_id` receives monotonically increasing versions that point to published artifact keys.

Space state in `runtime/artifact-spaces.json` carries a revision. Mutations can supply `expected_revision`; stale writers receive a conflict instead of overwriting another operator's change. Grants distinguish read, write, and manage subjects. Empty grants preserve the open/default behavior; once grants are populated, the caller must have the corresponding subject capability.

The split is intentional: the artifact blob remains immutable, while the space can answer “what is the latest accepted `mission-control` release report?” without changing any old archive.

## Request-scoped codebase consultations

Use `consult_codebase` when an agent needs a bounded answer about a repository snapshot but does not need to assign work to a persistent Team agent. The caller supplies a `managed_repo_id`, a question, and either a source ref, exact SHA, or a repository with a default branch. Evander resolves and records one exact commit before queueing the work.

The lifecycle is:

1. Authenticate the source session and validate the named consultation profile.
2. Resolve the managed repository inside the caller's directory scope and pin the exact commit under a consultation snapshot ref.
3. Insert a record in `runtime/agent-consultations.db`; an idempotency key is unique per source session and may not be reused for a different request.
4. Publish the durable `agent.consultation.requested` internal event. The initial MCP call may wait briefly, but the gateway transaction does not remain open for the entire model run.
5. The hidden system workflow claims capacity for the directory/profile, creates a `container_worker_vm` session, and grants it only `submit_codebase_consultation_result`.
6. Clone the managed repository read-only, detach at the pinned commit, write a one-time result token, and prompt the configured model with web search disabled.
7. Accept one structured result: answer, zero or more path/line evidence entries, confidence from 0 to 1, and explicit limitations. Evidence is validated against the pinned snapshot.
8. Finalize, cancel, fail, or time out; destroy the worker, revoke its Git access, release the snapshot pin, and only then release profile capacity.

Queue time counts against the consultation deadline. `max_concurrency` is per directory/profile. Fully cleaned terminal records older than `agent_consultation_retention_days` are compacted on new requests; records with incomplete cleanup remain for diagnosis.

The result token is bound to the worker session, stored only as a hash in the database, and consumed once. The signing secret comes from the host environment named by `agent_consultation_token_secret_env`. Never place the secret value in provider configuration.

## Failure behavior

| Failure | Safe outcome |
|---|---|
| Managed source ref moves before promotion | Promotion returns `source_ref_moved`; no newer SHA is substituted. |
| Upstream destination changes before publication | The explicit lease fails; no blind overwrite occurs. |
| Merge conflict in an operation worktree | The operation fails and cleans up; Evander does not auto-resolve agent code. |
| Guest pushes outside its namespace | The forced SSH command rejects the update. |
| Duplicate published artifact key | Publication fails; the existing artifact remains unchanged. |
| Unsafe artifact path, symlink, or archive member | Read/stage/import rejects it instead of escaping the artifact root. |
| Artifact-space revision is stale | Mutation returns a revision conflict; reload and reapply intentionally. |
| Consultation exceeds capacity | It remains queued until capacity is released or its deadline expires. |
| Consultation result cites invalid lines/path | Result submission fails validation; the snapshot remains authoritative. |
| Consultation worker cleanup fails | The terminal record is retained and capacity is not released prematurely. |

## Practical runbook

Register and inspect the Northstar Platform `mission-control` repository:

```bash
evander-cli git-repos register \
  --directory-path root/northstar-platform/mission-control \
  --repo-source /srv/repos/mission-control

evander-cli git-repos list --directory-path root/northstar-platform/mission-control
evander-cli git-repos show <managed-repo-id> --directory-path root/northstar-platform/mission-control
```

Clone it into an existing VM session without exposing the host path:

```bash
evander-cli clone-repo-into-vm <vm-name> \
  --managed-repo-id <managed-repo-id> \
  --directory-path root/northstar-platform/mission-control \
  --session-id <session-id>
```

Run routine reconciliation and Git maintenance:

```bash
evander-cli git-repos maintenance
evander-cli git-repos maintenance --managed-repo-id <managed-repo-id>
```

Delete only after `git-repos show` confirms there are no issue, project, session, SSH, worktree, or migration bindings. There is no force-delete path.

For a consultation, verify that the gateway, trigger orchestrator, effect orchestrator, model relay, and container worker are healthy; confirm the caller has `consult_codebase` and `get_codebase_consultation` (plus `cancel_codebase_consultation` if needed); then request and poll by consultation ID. If it stalls, inspect in this order:

1. the row in `runtime/agent-consultations.db`;
2. the internal event and orchestration run;
3. container session/job status and logs;
4. the consultation snapshot ref in the managed repository;
5. cleanup status and error before attempting to release or delete anything manually.

For a legacy mirror deployment, do not improvise a partial conversion. Stop trigger/effect workers, run `evander-cli git-repos migrate --dry-run`, resolve every blocker, use `--apply`, execute the documented VM and container smoke path, and only then run `--finalize <migration-id>`. Apply and finalize are intentionally separate decisions.

## Source trail

- `src/evander/admin/services/managed_git_service.py` — registry identity, ref transactions, restricted SSH integration, worktrees, publication, maintenance, and migration.
- `scripts/git_ssh_command_wrapper.sh`, `scripts/guest/push_agent.sh`, and `scripts/guest/sync_base.sh` — guest capability and helper enforcement.
- `src/evander/session_artifacts.py`, `src/evander/effects/providers/artifacts.py`, and `src/evander/published_artifacts.py` — safe artifact paths, publication, and immutable storage.
- `src/evander/artifact_spaces.py` and `src/evander/admin/services/artifact_service.py` — logical spaces, grants, versions, and revision checking.
- `src/evander/consultations.py`, `src/evander/gateway/consultation_tools.py`, and `src/evander/effects/providers/code_consultation.py` — persisted consultations, one-time result submission, worker lifecycle, and cleanup.
- `docs/git-remotes.md` and `docs/operations.md` — supported managed-Git and consultation runbooks.
- `tests/test_managed_git_service.py`, `tests/test_managed_git_workflow_contract.py`, `tests/test_published_artifacts.py`, `tests/test_artifact_spaces.py`, and `tests/test_consultations.py` — current contract coverage.


# 12. Issues and Provider Integrations

> **At a glance.** Evander keeps a local issue record as the control-plane anchor for work, while provider adapters read or change the corresponding Azure DevOps or GitHub object through host-owned credentials. A local issue can be authored in Evander or act as a cache-backed proxy for a remote work item. Workflow triggers own intake; provider queries and effects own external API calls; the ADO worker owns Azure DevOps pull-request feedback and completion polling. Those responsibilities are deliberately separate.

## The mental model: anchor, projection, provider

Three records that look like “the issue” serve different purposes:

| Record | Authority | Purpose |
|---|---|---|
| Local issue | Evander runtime | Stable identity, directory scope, priority, state, VM/session association |
| Proxy cache | Evander runtime, refreshed from provider | Fast display of selected remote fields and synchronization diagnostics |
| Remote issue or pull request | Azure DevOps or GitHub | Provider-native discussion, status, reviewers, commits, and links |

The local record never smuggles a provider token into a guest. When an agent uses an issue tool, the gateway resolves its session and directory, applies policy, and calls a host provider. Provider-normalized responses then cross the boundary.

Northstar Platform's `mission-control` project, for example, can track “add telemetry retry behavior” as a local proxy for Azure DevOps work item 14084. Imani's execution session refers to the local record; the proxy cache shows the remote title and state; a workflow effect posts a progress comment to Azure DevOps.

## Local issue identity and lifecycle

A `LocalIssueRecord` has an internal `issue_id`, a human-facing `issue_number`, directory path, title, description, state, source type, timestamps, and metadata. Identity is scoped. The same visible number can exist in different directories or provider projects, so callers should use the internal ID whenever a number could be ambiguous.

The supported local states are:

```text
New -> Approved -> Committed -> In Progress -> Completed -> Closed
```

These are allowed values, not a promise that every transition must visit every intermediate state. State-changing services enforce the operation-specific invariant. `claim_next_issue`, for example, selects an eligible local manual issue by priority and age and moves it to `In Progress`. `complete_issue` refuses an issue that is not already `In Progress`.

Priority values are `P0` through `P3`; omitted priority behaves as `P3` for queue ordering. `claim_next_issue` considers `Committed` records in the calling session's directory scope, orders them by priority and age, and moves the winner to `In Progress`. Only `local_manual` issues participate in the local claim/complete queue. A `remote_proxy` remains governed by its remote system and is changed through provider-aware operations.

Issue VM creation and repository bootstrap are separate actions. Creating the VM first makes failures and cleanup visible; cloning a managed repository later gives the session only the repository identity and access mode it needs. Deleting a local issue is blocked while a VM or session still depends on it.

## Remote proxies and freshness

A proxy stores its binding in `metadata.remote`, including the normalized provider and remote issue ID. `metadata.proxy_cache` contains the most recently materialized provider payload and refresh time. Provider errors belong in synchronization metadata; they must not be disguised as fresh remote state.

Current proxy mutation support is Azure DevOps-specific. Provider-neutral workflow queries can read remote issues from Azure DevOps and GitHub, but this does not imply that every mutation exists for every provider. Unsupported combinations fail explicitly.

The `evander-proxy-issue-sync` worker periodically batches Azure DevOps summaries for known proxies. It refreshes changed cache entries and records diagnostics. A missing remote item does not automatically delete its local proxy: disappearance can mean permissions, a transient provider failure, or deletion, and requires an operator decision.

Remote intake is workflow-owned. A remote-issue trigger or query discovers matching work and an explicit proxy-upsert step creates or refreshes the local anchor. The proxy sync worker maintains already-known records; it is not a second intake engine.

## Provider-normalized workflow nodes

Provider adapters translate external shapes into stable workflow contracts:

- remote issue queries accept provider-specific filters and return canonical issue items;
- Azure DevOps effects create, update, delete, and comment on work items where configured;
- pull-request list/get queries normalize identifiers, branches, SHAs, status, reviewers, commits, and raw provider context;
- pull-request effects update supported metadata or tags;
- reply and publish services expose provider-specific discussion and PR publication behind normalized calls.

Normalization is intentionally lossy at the portable layer. A workflow that needs a provider-only field should declare that dependency rather than assuming every provider has it. Raw response data is available only where the node contract explicitly exposes it.

Inline provider media is also a trust boundary. Media URLs must use the expected HTTPS provider host and pass size and content checks before Evander makes them available. Arbitrary HTML or image URLs in an issue body are not fetched merely because a provider returned them.

## Pull requests, feedback, and merge preflight

Pull requests have two operating modes:

1. **Explicit streams.** Current workflow configuration names provider, repository, and matching rules. The ADO worker lists and polls those Azure DevOps PRs, records feedback cursors, and publishes new-feedback or completion events idempotently.
2. **Legacy PR-map bindings.** Older sessions may be associated with entries in `runtime/pr-map.json`. This compatibility path remains readable, but new workflow design should prefer explicit PR triggers and managed repository identity.

The ADO worker owns Azure DevOps review feedback and completion pickup. It does not own general work-item intake. GitHub PR reads, replies, and publication use their provider services; do not infer a GitHub polling worker from the ADO service name.

Merge preflight is read-only analysis. It resolves the provider PR snapshot, verifies that expected refs and commits exist in the authorized managed repository, and uses Git's merge-tree machinery for single, pairwise, or ordered checks. Results distinguish a clean merge from conflicts, missing refs, a stale target branch, repository errors, and provider errors. Preflight never merges, pushes, or updates the PR.

For Rowan's review of Imani's retry-telemetry change, a stale-target result means the target SHA reported by the PR no longer matches the current target ref. Rowan should refresh the PR snapshot and rerun preflight—not treat an earlier clean report as permission to publish.

## Failure behavior

| Symptom | Likely invariant or boundary | Safe response |
|---|---|---|
| Issue number resolves to several records | Number is not globally unique | Supply directory/provider context or internal `issue_id` |
| Claim returns no work | No eligible `local_manual` issue, or state/priority filter excludes it | Inspect source type, directory, state, and priority |
| Proxy fields look old | Sync has not succeeded or provider is unavailable | Inspect `proxy_cache` time and `proxy_sync` diagnostics; refresh explicitly |
| Provider mutation is rejected | Provider/operation pair is unsupported, policy denies it, or credentials are missing | Confirm the node's provider contract and host credential configuration |
| PR feedback repeats or disappears | Cursor/stream identity changed or legacy and explicit modes were mixed | Inspect the trigger stream, worker state, and published event IDs |
| Preflight reports `missing_ref` | Required SHA was not fetched into the managed repository | Refresh the managed repository and rerun; do not substitute branch tips |
| Preflight reports `stale_target_branch` | Provider target SHA and current target ref differ | Refresh provider and Git state before deciding |

Provider failures should leave enough context to retry without fabricating success. Workflow effects record attempts and outcomes; polling workers preserve cursors only at their defined success boundary; proxy refresh keeps the last known cache with an explicit diagnostic.

## Practical runbook

1. **Identify the record.** In Admin Web, select the directory and record the internal issue ID, visible number, `source_type`, and current state.
2. **Separate local from remote truth.** For a proxy, inspect `metadata.remote`, cache timestamp, and sync diagnostics. Open the provider URL when remote authority matters.
3. **Check host configuration.** Verify the selected directory inherits the intended provider organization/project/repository and that its token environment variable exists on the host.
4. **Check the responsible component.** Intake failures belong to the trigger workflow; known-proxy freshness belongs to `evander-proxy-issue-sync`; Azure DevOps PR feedback/completion belongs to `evander-ado-worker`.
5. **Inspect journals and workflow records.** Use `journalctl -u evander-proxy-issue-sync.service` or `journalctl -u evander-ado-worker.service`, then correlate the issue/PR identity with trigger and effect records in Admin Web.
6. **Refresh before mutating.** Re-read the remote object and, for code changes, refresh the managed repository. Preserve the exact provider ID and commit SHA.
7. **Retry the narrow operation.** Refresh a proxy, replay the failed workflow effect under its normal idempotency rules, or rerun merge preflight. Never edit provider/cache JSON to simulate success.

## Source trail

- `src/evander/models.py`
- `src/evander/admin/services/issue_helpers.py`
- `src/evander/admin/services/issue_git_service.py`
- `src/evander/issues/service.py`
- `src/evander/daemons/ado_issue_worker.py`
- `src/evander/daemons/proxy_issue_sync.py`
- `src/evander/pull_request_automation.py`
- `src/evander/pr_tracking.py`
- `src/evander/pr_merge_preflight.py`
- `src/evander/queries/providers/remote_issues.py`
- `src/evander/queries/providers/pull_request_get.py`
- `src/evander/effects/providers/pull_request_update.py`
- `tests/test_issue_git_admin_service.py`
- `tests/test_proxy_issue_sync.py`
- `tests/test_ado_issue_worker_state.py`
- `tests/test_pr_query_service.py`
- `tests/test_pr_merge_preflight.py`


# 13. Communications, Meetings, Calls, and Wakes

> **At a glance.** Evander turns external conversations into bounded, auditable control-plane data. Communication targets say *where* a session may read, write, or reply. Ingest workers copy allowed provider messages into local stores. Workflow triggers decide what those messages mean. Wake workers resume an existing agent context or start an explicitly allowed new one. Sending, scheduling, attachment retrieval, email, meetings, and calls each retain their own policy boundary.

## The mental model: address, message, event, wake

Keep four identities distinct:

| Concept | Example | Meaning |
|---|---|---|
| Communication target | `northstar-ops` | Stable, secret-free policy name for a Discord channel, Teams chat/channel/user, or similar endpoint |
| Stored message | provider + target + message/cursor ID | Locally materialized content and thread context |
| Workflow event | `trigger:communication:new-message` | A durable fact eligible to start workflow logic |
| Wake dispatch | session + source event + mode | A request to resume or create an agent turn |

Receiving a message does not automatically wake every session. A send permission does not imply read permission. A successful provider API call does not prove that a workflow consumed the resulting event.

For the Northstar Platform team, an operator might configure a readable/replyable Teams chat for `mission-control`. The project workflow filters for the telemetry-retry work item, and only then dispatches a wake to Imani's active session. Rowan can reply in the thread without gaining access to unrelated chats.

## Communication targets and directory policy

A target definition has a stable ID, provider and target kind, provider-native address, display metadata, and capabilities. Capabilities are explicit—typically `read`, `write`, and `reply`—and are checked per operation. Sessions refer to target IDs or legacy provider references; the resolver applies directory inheritance and overlays before returning the effective target.

Target definitions contain addresses, not access tokens. Discord bot tokens, Microsoft Graph credentials, email credentials, and telephony secrets remain in host environment/configuration. Directory-scoped policy can narrow the root catalog. A child directory cannot turn an unknown or forbidden endpoint into an allowed target merely by supplying its raw provider ID.

Direct-message routes deserve a second check: the target may be allowed while the sender is not. Inbound routing and workflow triggers apply sender/user allowlists where configured.

## Ingest, query, trigger, and wake are separate stages

### Ingest

`evander-discord-ingest` and the Teams ingest command poll provider APIs using host credentials, normalize messages, and upsert them into provider-specific inbox stores. Cursor state makes repeated polling safe. Teams meeting ingest consumes the media bridge's structured event stream rather than pretending a meeting transcript is an ordinary Graph chat message.

### Query and trigger

`query:communication:messages` reads stored messages through the normalized target boundary. `trigger:communication:new-message` evaluates new stored messages for explicit targets and sender rules. Provider-specific compatibility triggers still exist, but the normalized trigger is the preferred design for portable workflow packs.

### Wake

Wake dispatch is a later decision. `evander-discord-wake` considers unread Discord messages for eligible active sessions. `evander-scheduled-wake` handles persisted schedules. Azure DevOps PR feedback is handled by `evander-ado-worker`, not either communication wake worker. There is no current `evander-host-heartbeat` service.

This separation prevents a provider polling hiccup from becoming a session lifecycle decision and prevents one stored message from being delivered repeatedly without a durable cursor or dispatch record.

## Reading, replying, and attachments

Gateway tools first resolve the calling session, then the effective target, capability, directory policy, and approval requirement. Reads are limited to allowed targets. Replies additionally bind to an existing message/thread. New outbound messages require `write`; threaded responses require `reply`.

Inbound attachments are metadata until explicitly fetched. Attachment helpers re-resolve the source message and target, enforce allowlists, bound file size, reject unsafe filenames and media types, and store accepted bytes as communication artifacts. An artifact link records the provider/message relationship without making provider URLs a permanent trust token.

Outbound files follow the inverse path: Evander reads a safe regular file from the session artifact root, verifies the selected target and capability, then uploads through the host provider. A guest cannot supply an arbitrary host path.

All sensitive tool calls pass through policy and audit. If an approval is required, the provider action does not happen until the approval is granted and still valid.

## Email

Email providers support Gmail and Microsoft Graph behind a normalized interface. Mailbox and folder reads are restricted by host policy. Sync cursors are stored locally so polling can continue without scanning the entire mailbox.

Drafting and sending are different operations. Recipient and domain allowlists constrain drafts; a send can require explicit approval. The guest sees message and draft abstractions, not OAuth refresh tokens. JSON-backed email is available for controlled/local use but should not be mistaken for a live provider.

When a send result is uncertain—for example, the network fails after the provider accepted the request—inspect the provider's sent/draft state and Evander's audit entry before retrying. Blind retry can duplicate a message.

## Scheduled wakes

A wake request contains a target session, prompt/context, source, directory, resume mode, and either a one-time `resume_at` or a cron expression. The SQLite-backed wake store also records status, lease/attempt state, next run, and dispatch history. Older JSON state may be imported once; SQLite is the live authority.

Two resume modes matter:

- `same_context` continues the existing agent context;
- `new_context` creates an intentionally fresh turn where the Admin surface permits it.

MCP scheduling supports only `same_context`, because the calling session cannot grant itself authority to create an unrelated context. Admin operations can select the supported mode explicitly.

The worker claims due records, validates that the session is still dispatchable, and records the outcome. A recurring schedule advances only across its handled-dispatch boundary. A failed one-time dispatch remains retryable with bounded backoff/deadline state rather than being marked successful. Disabled or completed one-time schedules have no next run.

Useful commands include:

```bash
evander-cli wake-schedules list
evander-cli wake-schedules list --session-id <session-id>
```

Use `evander-cli wake-schedules --help` for the current create/edit/delete syntax; timestamps are interpreted as explicit instants and should include a timezone.

## Voice calls and Teams meetings

Phone calls use host-owned contact and provider configuration. Public session records contain a contact ID or masked display information, not a raw secret credential. Retell-backed calls and OpenAI Realtime over the DIDWW SIP bridge have different media transports but share a projected voice-session lifecycle that operators can inspect.

Call creation, status reconciliation, and the wake that reports a terminal call result are separate steps. If the provider accepted a call but the agent has not resumed, inspect the provider/call record and the voice-session reconciliation/wake path independently.

Teams meetings use a dedicated media bridge because real-time meeting media is not exposed as an ordinary Linux Graph polling API. The Windows bridge joins or leaves, captures media/transcript events, and sends authenticated structured events to the host ingest surface. Evander persists meeting/session records and turns eligible transcript events into workflow input. Starting or stopping a bridge session is operational control, not proof that a workflow saw a transcript.

```bash
evander-cli teams-meeting start --help
evander-cli teams-meeting stop --help
```

## Failure behavior

| Symptom | Check first | Meaning |
|---|---|---|
| Provider shows a message; Evander does not | Ingest service journal and cursor | Polling/auth/target resolution failed before local storage |
| Message is stored; workflow did not run | Trigger target, sender filter, and trigger cursor | Ingest succeeded; workflow eligibility did not |
| Workflow ran; agent did not wake | Wake dispatch record and session state | Dispatch was denied, deferred, or failed |
| Read works but reply fails | Target capabilities and thread identity | `read` does not imply `reply` |
| Attachment fetch is rejected | Source target, filename/type/size, and stored message link | File did not pass the attachment trust boundary |
| Cron fires repeatedly at once | Schedule/cursor migration or clock assumptions | Inspect persisted `next_run_at`; do not edit the database live |
| Call/meeting exists but no summary arrives | Provider/bridge record, ingest event, then wake record | Transport, event ingestion, and agent delivery are distinct |

Workers keep last-known-good configuration on a failed hot reload where supported and log the rejected reload. They should not replace a working policy with a partially parsed file.

## Practical runbook

1. **Name the stage.** Decide whether the failure is provider receipt, ingest, local query, trigger, effect/send, or wake.
2. **Resolve the target.** In Admin Web, inspect the effective directory target, provider kind, native address, capabilities, and sender restrictions.
3. **Check the owner service.** Use `journalctl` for `evander-discord-ingest`, `evander-discord-wake`, `evander-teams-ingest`, `evander-teams-meeting-ingest`, or `evander-scheduled-wake` as appropriate.
4. **Correlate durable IDs.** Follow provider message ID/cursor → stored record → trigger event ID → workflow run → wake dispatch/session. Do not correlate only by message text.
5. **Inspect policy and approval.** Confirm the session's target allowlist/capability and any pending or expired approval.
6. **Retry at the failed boundary.** Poll again, replay the eligible workflow event, retry the effect under its idempotency contract, or allow the wake worker's bounded retry. Avoid resending when provider acceptance is uncertain.
7. **For meetings/calls, check transport separately.** Verify bridge/provider health before debugging transcript or wake logic.

## Source trail

- `src/evander/messaging/targets.py`
- `src/evander/policy_config.py`
- `src/evander/messaging/discord_ingest.py`
- `src/evander/messaging/teams_ingest.py`
- `src/evander/messaging/discord_wake.py`
- `src/evander/messaging/scheduled_wake.py`
- `src/evander/messaging/wake_dispatch.py`
- `src/evander/messaging/wake_store.py`
- `src/evander/gateway/communication_tools.py`
- `src/evander/communication_artifacts.py`
- `src/evander/email/providers.py`
- `src/evander/voice_sessions.py`
- `src/evander/retell_phone.py`
- `src/evander/didww_sip_bridge.py`
- `src/evander/teams_meeting.py`
- `docs/teams-meeting-media-bridge.md`
- `tests/test_communication_targets.py`
- `tests/test_communication_new_message_trigger.py`
- `tests/test_scheduled_wake_worker.py`
- `tests/test_wake_store.py`
- `tests/test_gateway_communication_files.py`
- `tests/test_email_providers.py`
- `tests/test_voice_sessions.py`
- `tests/test_teams_meeting.py`


# 14. Semantic Memory, Model Usage, Skills, and Add-ons

> **At a glance.** These four facilities extend what Evander can know or do without weakening its control-plane boundaries. Semantic storage indexes operator-selected text in Qdrant; it does not replace canonical records. Model-usage events record what providers report and estimate cost from an operator-owned catalog; they are not invoices. Managed skills copy reviewed instructions into selected agents. Add-ons register bounded UI, API, MCP, workflow, and private-state surfaces and take effect only after the relevant services restart.

## Four extension layers

| Facility | Adds | Does not become |
|---|---|---|
| Semantic store | Searchable vector projection | Canonical database or access-control bypass |
| Model usage | Provider-neutral usage/cost observations | Provider billing ledger |
| Managed skill | Reviewed `SKILL.md` installed into selected execution | Executable host plugin or secret carrier |
| Add-on | Packaged control-plane integration | Permission to modify arbitrary core state |

For Northstar Platform, an operator might index approved architecture notes, attach a reviewed telemetry-debugging skill to Imani's execution, inspect model use for the `mission-control` workflow, and use Team Add-on to inspect the coordinated assignment. Each action still carries directory, session, and audit context.

## Semantic storage is a projection

The current semantic-store provider is Qdrant and the current embedding provider is OpenAI-compatible embeddings. Defaults target host-local Qdrant at `http://127.0.0.1:6333` and `text-embedding-3-small` with 1,536 dimensions and cosine distance. Provider configuration is directory-resolved; API keys are named by environment variable and resolved on the host.

The v1 collection contract is strict:

- a collection has one unnamed vector, not a named-vector schema;
- its dimension and distance must match the effective embedding configuration;
- a point ID is an unsigned 64-bit integer or a UUID string;
- upsert embeds the supplied text and records that source text under the reserved payload key `_semantic_text`;
- user payload must not counterfeit reserved fields;
- get/search return normalized points and scores, while delete names explicit IDs.

`effect:semantic:ensure-collection` is idempotent only when an existing collection has the expected schema. It refuses a mismatch instead of silently re-embedding old points into an incompatible space. Changing model or dimensions therefore requires an explicit migration: create a new collection, re-index from canonical sources, verify it, then switch consumers.

Queries and effects are available both to workflows and, where policy allows, through gateway tools. Deletion is destructive and may require approval. A similarity result is evidence of proximity in the selected embedding space, not a statement of truth or authorization.

### A safe indexing pattern

Use a deterministic point ID derived outside the semantic store and retain the canonical record ID in payload metadata. For example, Rowan's approved review note remains authoritative in Team or the artifact store; Qdrant receives its text plus `{team_id, project_id, source_type, source_id}`. Re-indexing the same point updates its vector, while deleting Qdrant never deletes the source note.

## Model-usage events and estimates

Relay and execution adapters normalize provider observations into `ModelInferenceUsageEvent` records in `runtime/model-usage.db`. An event can include provider/endpoint, model or deployment, input/output/total tokens, request status, duration, body byte counts and hashes, and correlation fields such as session, workflow run, effect, node, dispatch, harness, backend, directory, trace, and request IDs.

Two cases must remain distinguishable:

- `available`: the provider supplied usable token data;
- `usage_unavailable`: a request completed or failed without a usable usage block.

Evander does not invent missing token counts. Correlation is best effort within an explicitly bounded context; unattributed events remain useful provider observations rather than being assigned to the nearest-looking Team task.

Cost calculation uses `runtime/model-pricing.openai.json` by default, or `EVANDER_MODEL_PRICING_PATH`. `evander-cli init-state` seeds the runtime catalog from `config/model-pricing.openai.json.example`; the example is not a live price feed. Rules can match provider, exact model or pattern, effective date, service tier, cached input, cache writes, batch multiplier, and long-context adjustments.

An estimate reports statuses such as `estimated`, `pricing_unavailable`, `usage_unavailable`, `unsupported_provider`, or `insufficient_token_breakdown`. It is an operational estimate under a named catalog version and rule—not a substitute for the provider invoice. Operators must update pricing with revision checks when provider prices change.

## Managed skills

The skill library stores one reviewed instruction document at:

```text
runtime/skills/<skill-name>/SKILL.md
```

Metadata lives in `runtime/skills-library.json`. Names may contain letters, numbers, dots, underscores, and dashes, but no traversal segments. Content must be non-empty. Creation uses a temporary file and atomic replacement before metadata is committed.

When a container execution requests skills, Evander copies the selected files to:

```text
/home/agent/.config/evander/codex/skills/<skill-name>/SKILL.md
```

The agent receives text, not a host path. Unknown or missing skill content fails provisioning. Admin deletion is blocked when active configuration or workflow references would make the deletion unsafe.

A skill can instruct an agent to call tools it already has; it cannot itself grant a tool, network route, host secret, or policy exemption. Treat skill review like prompt/code review: inspect instructions for data exfiltration, unbounded mutations, conflicting authority, and obsolete commands.

## Add-ons

An add-on returns an `AddonRegistration` with a validated ID, display name, optional Admin navigation, API/UI route factories, MCP registration hook, workflow providers, and metadata. It receives an `AddonContext` containing its runtime directory and supported core services. Private state belongs under:

```text
runtime/addons/<addon-id>/
```

Storage helpers reject absolute paths and traversal. Removing private add-on state must not corrupt core state, although it can intentionally erase that add-on's records.

Discovery scans configured local package directories—`addons/` and `src/` by default—plus explicitly named modules. Entry-point discovery is disabled by default. Discovery and enablement are different: Admin Web can show a package that is present but disabled or failed to load.

`addon_loader.enabled_addon_ids` is revision-edited configuration. Enabling or disabling is restart-gated because Admin routes, MCP tools, and workflow registries are assembled at process startup. Disabling prevents the next load; it does not delete private state.

### The Team Add-on boundary

The bundled Team Add-on registers its own `/api/addons/team-addon/...` API, `/addons/team-addon/` UI, MCP tools, workflow primitives, and relational state at `runtime/addons/team-addon/state.db`. A legacy `state.json` can be imported once. Core Evander remains responsible for sessions, execution, policy, Git, artifacts, workflows, audit, communication, and model usage; Team records refer to those core identities instead of duplicating their implementation.

The add-on is disabled by default. Enable `team-addon`, restart every service listed by the Add-ons page (including the gateway when MCP registration changes), then verify loaded state. Team-specific chapters describe its organization, task, review, and release semantics.

## Failure behavior

| Symptom | Likely cause | Safe response |
|---|---|---|
| Semantic ensure fails with schema mismatch | Model dimensions/distance changed or collection is not v1 single-vector | Create and backfill a new collection; do not alter config to misdescribe old vectors |
| Semantic search returns irrelevant results | Wrong source text/model/filter or stale projection | Inspect `_semantic_text` and payload; re-index from canonical records |
| Cost is unavailable | Missing usage, model rule, provider support, or token breakdown | Preserve the status and update the catalog/adapter; do not treat zero as cost |
| Skill appears in Admin but execution cannot install it | Metadata/content mismatch or skill not selected | Repair through the skill service and reprovision; do not copy ad hoc host files |
| Add-on says “pending restart” | Configuration changed after process assembly | Restart the listed services and verify loaded state |
| Add-on load error | Bad manifest/module/registration, duplicate ID, or missing asset | Inspect discovery and the journal; an explicitly enabled/mapped add-on can fail process assembly rather than start with an incomplete contract |
| Team UI works but Team MCP tools are missing | Admin restarted but gateway did not | Restart gateway after confirming enablement |

## Practical runbook

1. **Semantic:** inspect effective directory settings, Qdrant health, collection size/distance, and embedding endpoint. Test store status before changing data.
2. **Projection integrity:** retrieve a point by exact ID and compare its source metadata to the canonical record. Rebuild rather than hand-edit vectors.
3. **Usage:** filter by provider, session/workflow/effect, and time. Check `normalized_usage_status` before interpreting token or cost totals.
4. **Pricing:** open the active runtime catalog in Admin Web, note its revision/version/effective dates, make a revision-checked update, and re-query estimates.
5. **Skills:** validate the library entry and every configuration/workflow reference before adding or deleting it. Start a fresh execution to verify installed content.
6. **Add-ons:** open Add-ons, distinguish discovered/enabled/loaded/pending-restart states, inspect load errors, restart the listed services, and verify `/api/admin/state` plus the add-on's health/read endpoint.
7. **Team Add-on:** verify `runtime/addons/team-addon/state.db` exists and is readable through the add-on API. Never repair it by copying a stale JSON snapshot over the database.

## Source trail

- `src/evander/semantic/settings.py`
- `src/evander/semantic/qdrant.py`
- `src/evander/semantic/workflow_helpers.py`
- `src/evander/gateway/semantic_tools.py`
- `src/evander/model_usage/models.py`
- `src/evander/model_usage/store.py`
- `src/evander/model_usage/pricing.py`
- `src/evander/repositories/skills_library.py`
- `src/evander/admin/services/skill_admin_service.py`
- `src/evander/addons.py`
- `src/evander_team_addon/__init__.py`
- `docs/addons.md`
- `docs/configuration-reference.md`
- `config/model-pricing.openai.json.example`
- `tests/test_semantic_store.py`
- `tests/test_gateway_semantic_tools.py`
- `tests/test_model_usage_store.py`
- `tests/test_model_usage_pricing.py`
- `tests/test_skill_library_repository.py`
- `tests/test_addons.py`
- `tests/test_team_addon_repository.py`


# 15. Admin Operations, Observability, Backup, and Recovery

> **At a glance.** Admin Web is the supported operator console over the same runtime stores, service managers, workflow registries, and policy engine used by Evander's CLI and gateway. Read views help reconstruct state; mutations remain protected by authentication, directory-scoped permissions, privileged unlock, approvals, and optimistic revision checks. Recovery starts from durable runtime truth—not from what the browser last rendered.

## The mental model: one runtime, several views

Admin Web is not a second control plane. `build_admin_runtime` assembles focused services for sessions, issues/Git, VMs, workers, workflows, reports, approvals/audit, artifacts, configuration, add-ons, skills, capacity, and model usage around one `RuntimeContext`.

| Surface | Best use | Authority |
|---|---|---|
| Admin Web | Interactive inspection and revision-checked operations | Calls live core/admin services |
| `evander-cli` | Automation, bootstrap, narrow diagnostics | Calls the same repositories/services where supported |
| systemd/journal | Process lifecycle and stderr/stdout history | Host service manager |
| runtime files/databases | Durable application state | Repository-specific source of truth |
| provider APIs | External issues, PRs, messages, calls | External provider |

A green dashboard card is a summarized observation. It does not replace the underlying session manifest, workflow run, effect attempt, service journal, or provider object.

![Evander Admin operations dashboard in dark mode for the synthetic Northstar directory](assets/evander-user-manual/screenshots/admin-dashboard.png)

<p class="figure-caption">Figure 15.2 — The dashboard is an orientation layer over live detail APIs, not a replacement source of truth. Sanitized Northstar fixture.</p>

## Operator surfaces

The core interface exposes:

- dashboard health and automation activity;
- VMs, issues, agents, sessions, and logs;
- approvals, tool visibility, and read-only tool-call audit;
- workflow packs, editor, runs, reports, events, and webhooks;
- Git repositories, artifacts, skills, add-ons, and Team navigation;
- configuration, workers, capacity, model pricing/usage, users, and groups;
- chat and operator steering where enabled.

Use the selected directory as part of every diagnosis. Directory inheritance changes effective provider, policy, communication-target, workflow, and repository visibility. A root owner can see more than a project-scoped workflow maintainer; comparing their screens without noting scope is misleading.

## Authentication, authorization, and privileged unlock

Admin users and groups are stored in `runtime/admin-web.db`. Built-in groups include owner, operator, workflow maintainer, workflow viewer, auditor, and viewer. Permissions are directory-scoped and inherited downward. Direct workflow write implies workflow read; ownership expands to the full permission catalog.

Authentication answers *who*. RBAC answers *whether that identity can act in this directory*. Privileged unlock is an additional per-login-session guard for sensitive host mutations. It does not add a missing RBAC permission and should not be treated as a permanent administrator mode.

Approval gates are separate again. A privileged owner can configure policy but an agent's gated tool invocation still needs its matching, unexpired approval. The approved tool, session, and payload digest must match at execution time.

## Revision-checked changes

Configuration, workflow packs, model pricing, add-on enablement, and other shared documents expose a content revision. The client sends the revision it read. If another operator saved first, Evander returns a conflict instead of overwriting newer work.

On conflict:

1. preserve your intended change outside the form;
2. reload the current document and read the other change;
3. merge deliberately;
4. save against the new revision.

Never work around a revision conflict by writing the live JSON file directly. Workflow pack edits also use lock and revision behavior; a checked-in example pack is not necessarily the live operator pack.

## Observability and evidence

### Health and capacity

Start broad:

```bash
evander-cli status --runtime-dir runtime --json
systemctl --failed
```

The capacity endpoint, `/api/admin/capacity-health`, reports control-plane disk, known storage roots, session samples, and unavailable/permission-denied telemetry explicitly. Built-in disk thresholds warn below 15% free and become critical below 5% unless configured otherwise. “Unknown” is not healthy.

### Journals and structured runtime records

Use service journals for process startup, reload, provider transport, and worker-loop failures:

```bash
journalctl -u evander-admin-web.service --since today
journalctl -u evander-trigger-orchestrator.service --since today
journalctl -u evander-effect-orchestrator.service --since today
```

Then move to the durable domain record: session manifest, trigger event, orchestration row, effect attempt, wake dispatch, managed Git operation, or add-on database. Correlate by stable IDs and exact SHA, not timestamps alone.

### Tool-call audit

`runtime/audit.jsonl` is the canonical append-only tool-call and policy evidence by default. `runtime/audit.db` is a rebuildable search index used by Admin Web; it is imported incrementally from JSONL. If the index is corrupt or incomplete, preserve the JSONL and rebuild the index:

```bash
evander-cli rebuild-audit-index --config runtime
```

Audit redaction intentionally removes configured sensitive fields. Absence of a secret from an audit row is expected, not evidence that no credential was used.

## Durable state inventory

Before backup or repair, inventory the configured paths. Common current stores include:

![Configuration, JSON registries, SQLite stores, audit evidence, managed Git, and artifact blobs converge at a consistent backup boundary; derived indexes are rebuilt after restore.](assets/evander-user-manual/diagrams/09-runtime-stores.svg)

<p class="figure-caption">Figure 15.1 — A recoverable snapshot preserves every canonical store at one consistency boundary and rebuilds derived state only after restore.</p>

| Data | Typical location | Recovery role |
|---|---|---|
| Host/directory config | `runtime/host-config.json`, `runtime/directory-config/` | Effective runtime configuration |
| Sessions/issues/approvals and JSON repositories | paths below `runtime/` selected by config | Control-plane identity/state |
| Orchestration | `runtime/orchestration.db`, event journals | Workflow runs, jobs, effects |
| Audit | `runtime/audit.jsonl`; rebuildable `audit.db` | Security evidence/index |
| Admin identities | `runtime/admin-web.db` | Users, groups, grants |
| Managed Git | `runtime/managed-git-repositories.json`, `runtime/managed-git/` | Canonical cached objects and controlled refs |
| Published output | `runtime/published-artifacts/`, artifact-space metadata | Immutable deliverables and organization |
| Communications/wakes/usage | provider inbox DBs, wake DB, `model-usage.db` | Cursors, dispatches, observations |
| Add-on state | `runtime/addons/<id>/`, including Team `state.db` | Add-on-private durable records |
| VM and worker execution storage | libvirt images and configured worker storage | Running execution state, outside the runtime directory |

Ephemeral Git worktrees under `runtime/git-worktrees/` are reconstructible scratch space. Bare managed repositories are not the same thing and must not be discarded casually: host-only pinned and Team/review refs may not yet exist upstream.

Secrets and local service configuration—`config/*.env`, provider JSON, `secrets.env`, systemd environment, certificates—may live outside `runtime/`. Back them up separately with stricter permissions. Never place them in published artifacts or documentation captures.

## Consistent backup

There is no single “copy this directory while everything runs” guarantee across JSON, SQLite WAL, bare Git, add-on databases, VM disks, and external providers. Choose a consistency boundary.

For a control-plane backup:

1. Record the Evander revision, installed package version, effective config paths, enabled add-ons, and current service list.
2. Quiesce writers: stop trigger/effect, provider ingest/wake, Admin Web, gateway mutations, worker control, and add-on services as appropriate. Keep the outage explicit and bounded.
3. Confirm no managed Git operation or Team lease is active. Do not freeze midway through an upstream push.
4. Back up `runtime/`, including SQLite `-wal`/`-shm` companions if any remain. With services stopped, a file-level snapshot is consistent; while live, use SQLite's backup API per database rather than `cp`.
5. Back up external execution storage only if recovery must resume those exact VMs/worker homes. Otherwise record that executions will be recreated.
6. Back up operator config and secrets separately with restrictive ownership.
7. Hash the backup, record its scope/time, and test restore into an isolated path.
8. Restart services in dependency order and run the health checks below.

External provider state is not captured by this backup. Record outstanding PRs, messages, and call IDs when an incident or migration requires end-to-end reconciliation.

## Restore and recovery

Restore to a compatible checkout/package revision first. Keep services stopped while placing runtime and secrets. Restore file ownership and modes, then initialize only missing state—do not run `init-state` over a restored runtime without reviewing what it will seed.

Recommended order:

1. host config, directory config, secrets, and admin identity store;
2. runtime databases/JSON/journals and add-on state;
3. managed bare repositories and published artifacts;
4. optional VM disks or worker storage;
5. firewall and relay services;
6. Admin Web/gateway, then trigger/effect and provider/wake workers.

After startup, verify:

```bash
evander-cli status --runtime-dir runtime --json
./scripts/install_systemd_units.sh --preflight
```

Check SQLite integrity with the SQLite tooling appropriate to each database, inspect migrations/import markers, and verify a read through the owning API. Rebuild only derived indexes such as `audit.db`. Do not replace a current SQLite authority with a legacy JSON import file: import paths are deliberately one-way and often marker-gated.

For a partially failed operation, prefer domain reconciliation over rollback by file editing. Examples: reconcile a managed Git operation against its pinned SHA and destination lease; reconcile an effect attempt against provider state; reconcile a Team lease/outbox entry through Team operations; reconcile a wake against its dispatch history.

## Failure behavior

| Symptom | Interpretation | Next evidence |
|---|---|---|
| Admin Web is unavailable | UI process/config/auth may be down; runtime can still be intact | service status/journal, then CLI and files |
| UI shows revision conflict | Concurrent write was detected | reload and merge; do not force file write |
| Tool Calls is empty but JSONL has rows | Search index is stale/corrupt | audit index status and rebuild |
| Dashboard says unknown capacity | Collection failed or lacks permission | endpoint errors and filesystem ownership |
| SQLite says locked | A live writer or abandoned operation holds access | identify owning service; do not delete WAL/lock files |
| Restored worker repeats effects | Store/provider boundary was captured inconsistently | inspect effect idempotency key and provider state before enabling workers |
| Team/Add-on data is missing after restore | Private add-on directory was omitted or wrong add-on not loaded | restored add-on state, enablement, restart, API read |

## Practical incident runbook

1. **Stabilize.** Stop only the component causing unsafe repeated mutations; preserve journals and state files before repair.
2. **Declare scope and time.** Record directory, session/workflow/effect/provider IDs, commit SHA, and first observed failure.
3. **Read durable truth.** Inspect the owning repository/database and provider. Treat UI caches and derived indexes as secondary.
4. **Build the event chain.** Trigger → workflow run/job → effect attempt → provider result → wake/session, or issue → session → managed ref → pinned SHA → publication.
5. **Classify uncertainty.** Distinguish “not attempted,” “attempt failed,” and “provider may have accepted.” The last case requires reconciliation before retry.
6. **Repair through the owner.** Use Admin/API/CLI service operations, migrations, maintenance, or an isolated restore. Avoid direct live edits.
7. **Verify both sides.** Check local state, external provider state, audit entry, and user-visible result.
8. **Capture prevention.** Add a focused regression test, update the runbook/CHANGELOG, and retain a redacted incident timeline.

## Source trail

- `src/evander/admin/composition.py`
- `src/evander/admin/web/app.py`
- `src/evander/admin/web/rbac.py`
- `src/evander/admin/web/shared.py`
- `src/evander/admin/services/operations_service.py`
- `src/evander/admin/services/approval_audit_service.py`
- `src/evander/admin/services/capacity_health_service.py`
- `src/evander/capacity_health.py`
- `src/evander/repositories/audit_index.py`
- `src/evander/admin/web/user_store.py`
- `docs/operations.md`
- `docs/admin-ui-user-manual.md`
- `scripts/install_systemd_units.sh`
- `tests/test_admin_web_rbac.py`
- `tests/test_admin_web_config_mutation_auth.py`
- `tests/test_admin_web_tool_calls.py`
- `tests/test_capacity_health.py`
- `tests/test_operations_admin_service.py`


<div class="page-break"></div>

# Part III: Team Add-on

Move from the Team domain model through commands, wakes, collaboration, review, and exact-SHA release publication.

# 16. Team Add-on Boundary and Domain Model

> **At a glance.** The Team Add-on is an optional Evander domain module for durable organizations, projects, work, communication, review, release, governance, and memory. Evander core still owns sessions, execution backends, workflows, managed Git, communications, artifacts, policy, and model usage. The add-on composes those capabilities through registered APIs, MCP tools, providers, and a workflow pack; its canonical business state lives in <code>runtime/addons/team-addon/state.db</code>.

## A second control plane, not a second product

Evander core answers questions such as “which isolated session should run?”, “which tools may it use?”, and “which pinned commit may be published?” The Team Add-on answers a different set:

- Which persistent agent is the manager, lead, implementer, or reviewer?
- Which project, case, task, deliverable, review, or release does an action belong to?
- Who may direct, review, stand down, delegate, or publish work?
- What durable context should survive an individual model conversation?

The distinction matters operationally. A Team agent refers to a core Evander session, but it is not the session. A Team project may refer to a core managed repository, but it does not own the upstream repository definition. A Team release requests core Git effects, but it does not bypass their leases and compare-and-swap checks.

The add-on is loaded through Evander's generic add-on mechanism. It contributes:

- a self-contained Admin UI at <code>/addons/team-addon/</code>;
- an Admin API below <code>/api/addons/team-addon/</code>;
- Team-owned MCP read and command tools;
- trigger, query, and effect providers;
- the registered <code>team-runtime.v1</code> workflow-pack definition.

No Team-specific model has to be embedded in core Admin Web or the core gateway. Disabling the add-on removes those registered surfaces; it does not turn Team records into core records.

![Responsibility boundary between Evander core and the Team Add-on](assets/evander-user-manual/diagrams/10-team-boundary.svg)

<p class="figure-caption">Figure 16.1 — Team owns organizational truth while core owns execution and integration capabilities.</p>

## Canonical and derived state

The canonical Team database is relational SQLite:

<code>runtime/addons/team-addon/state.db</code>

It contains the validated Team domain plus command-support tables for dispatch activity, idempotency, and the durable outbox. The store uses foreign-key enforcement, a busy timeout, write-ahead logging, and serialized write transactions. On the first database initialization only, an old <code>state.json</code> may be imported. That JSON file is migration input, not a live fallback.

Several useful projections are deliberately non-canonical:

| Projection | Purpose | Rebuild or recovery rule |
|---|---|---|
| Core session <code>metadata.team_addon</code> | Compact routing and operator context | Refresh it from Team state; never edit it as Team truth. |
| Team UI view payloads | Scoped, revision-tagged read models | Reload after an invalidation or revision conflict. |
| Qdrant Team-memory collection | Semantic retrieval index | Reindex from canonical memory records. |
| Timeline view | Human-readable events derived from current state | Do not treat it as an append-only audit log. |
| Artifact-space and managed-Git records | External durable capabilities referenced by Team state | Diagnose them in their owning core services as well as Team state. |

This boundary prevents a common debugging error: changing a session manifest, a Qdrant point, or a UI payload does not repair the Team record that produced it.

## The domain from the outside in

The durable hierarchy is easiest to understand in layers.

### Organization

A <code>Team</code> has a manager, optional cost cap, optional operator communication target, projects, roles, agents, and a policy that chooses direct operator escalation or governance escalation. A <code>GovernanceGroup</code> spans one or more Teams and has its own manager and agenda.

A <code>TeamRole</code> names an organizational function. It may be project-scoped and may grant narrow permissions such as <code>create_root_task</code> or <code>request_release</code>. A <code>PersistentAgent</code> binds an organizational identity to a core Evander session. Reporting lines and project assignments are explicit records, not deductions from a display name.

### Work

A <code>Project</code> groups goals and executable work. It may reference a core <code>managed_repo_id</code>, base branch, canonical release prefix, and release policy. A <code>Case</code> holds intake and lifecycle context; a <code>Task</code> is the assignable unit of execution. Tasks can form parent/child and dependency graphs.

### Evidence and decisions

A <code>Deliverable</code> records an agent's evidence for a task: artifact references, a pinned Git context, verification status, and supersession history. A <code>ReviewRequest</code> binds a deliverable, reviewer, optional profile, criteria, findings, and immutable decision. A <code>ReleasePackage</code> freezes accepted scope and the candidate evidence needed to decide whether publication is safe.

### Runtime and communication

<code>AgentRuntime</code> records leases, dispatch identity, wake timing, context-rotation policy, and no-progress state. Inbox items, conversations, messages, stand-downs, ephemeral assignments, Git promotions, and governance agenda items connect durable intent to asynchronous runtime work.

### Memory

<code>TeamMemory</code> preserves decisions, preferences, facts, runbooks, postmortems, research, and handoffs. Scope and visibility remain part of the canonical record even when semantic search is enabled.

![Team Add-on domain relationships](assets/evander-user-manual/diagrams/11-team-domain.svg)

<p class="figure-caption">Figure 16.2 — Durable Team records connect organization, work, evidence, runtime, and governance.</p>

## Northstar: one thread through the model

Throughout this manual, Northstar uses the stable fixture below:

| Record | Example |
|---|---|
| Team | <code>northstar-platform</code> |
| Project | <code>mission-control</code> |
| Manager | Mira |
| Project lead | Priya |
| Implementer | Imani |
| Reviewer | Rowan |
| Release agent | Riley |
| Work | Add safe retry behavior to telemetry delivery |

Mira is the Team manager and organizational root. Priya leads <code>mission-control</code>. Imani owns the telemetry retry task. Rowan reviews its deliverable under the project's review policy. Riley has release authority but does not acquire managerial authority merely by holding it.

This example demonstrates why the records stay separate. Mira's Team role, Priya's project leadership, Imani's task ownership, Rowan's assigned review, and Riley's release permission are five independent authorization facts. A friendly title such as “senior engineer” would not safely encode them.

## Model invariants that protect the graph

The repository validates the complete next state before committing it. Important invariants include:

- referenced Teams, projects, roles, agents, tasks, cases, deliverables, and reviews must exist and have compatible scope;
- an agent attached to a Team has a valid role and reporting relationship; a teamless agent cannot retain Team reporting metadata;
- a project lead and project members are registered agents of the owning Team;
- parent and dependency task links cannot create cycles or cross project boundaries;
- case/task links are bidirectional and remain in the same Team and, where applicable, project;
- task ownership, deliverable production, review assignment, and release scope must agree;
- IDs and map keys agree, and enumerated states are validated;
- a Team manager remains the reporting root rather than an ordinary subordinate.

Task states are <code>pending</code>, <code>in_progress</code>, <code>blocked</code>, <code>waiting_for_review</code>, <code>finished</code>, <code>accepted</code>, and <code>rejected</code>. Cases use <code>open</code>, <code>triage</code>, <code>in_progress</code>, <code>waiting</code>, <code>resolved</code>, and <code>closed</code>. Other records have domain-specific state machines; do not infer that the same word has identical transition rules everywhere.

The project status is recalculated from its goals and work. A project becomes complete only when its goals are accepted and no active work remains. Blocking work can make it blocked; otherwise it remains active. It is not an operator-maintained cosmetic flag.

## Where mutations enter

Three supported write paths share the repository rules:

1. Ordinary Admin API and UI domain mutations use an explicit expected Team revision. Append-specialized surfaces such as Chat use their own current-record contract.
2. Team MCP commands run with session-derived identity and a current dispatch context.
3. Team workflow effects perform operator-authored automation through registered providers.

Templates are a fourth convenience surface, but they materialize the same Team records through one optimistic mutation. A template does not create a core session, VM, container job, managed repository, or external accounting action. Those resources must already exist or be created through their owning Evander flows.

Reads are grouped rather than duplicated across every chapter. Operators use the scoped Team UI views and Admin API. Agents use Team list/get tools such as tasks, inbox, cases, deliverables, reviews, releases, projects, agents, runtime attention, identity, and memory search. Workflows use <code>query:team:state</code>, <code>query:team:memory</code>, and <code>query:team:session-routing</code>.

## Failure patterns

| Symptom | Likely boundary mistake | Correct first move |
|---|---|---|
| Team UI shows stale data after a write | A scoped projection was retained across a revision invalidation. | Reload the affected view and compare its revision. |
| A session exists but no Team agent can act | Core session creation was mistaken for Team registration. | Register the persistent agent and validate its Team role and project assignment. |
| Editing session metadata has no lasting effect | A mirror was changed instead of canonical Team state. | Repair through the Team API or supported command surface. |
| A managed repository cannot be found | Team state contains an obsolete or wrong managed-repository ID. | Inspect the core managed-Git registry, then update the project through the revision-checked API. |
| Legacy JSON changes are ignored | SQLite has already been initialized. | Treat <code>state.db</code> as authoritative; do not replay JSON over it. |
| A Timeline row is missing an old intermediate value | Timeline was mistaken for an audit ledger. | Use command activity, workflow runs, Git promotion records, and external audit stores appropriate to the event. |

## Practical runbook

For a live deployment:

1. Confirm the add-on is enabled and registered before interpreting missing Team routes as missing data.
2. Read the broad status and Team bootstrap/view payloads through the supported UI or API.
3. For a state discrepancy, inspect <code>runtime/addons/team-addon/state.db</code> with the current schema. Prefer the deployment's own Evander environment and a read-only or safely copied database.
4. Compare the relevant core resource by stable ID: session, managed repository, workflow run, artifact ref, communication target, or model-usage record.
5. Perform repairs through the Team API, UI, MCP command, or workflow effect that owns the invariant. Do not edit SQLite rows by hand.
6. Verify the new revision, the affected scoped view, and any queued outbox or workflow work.

For backup, use a SQLite-consistent backup mechanism or stop writers before copying the database. Back up referenced artifacts, avatars, core voice-session records, managed repositories, workflow configuration, and model-usage data separately; not all of those bytes live in Team SQLite.

## Source trail

- <code>src/evander_team_addon/addon.json</code> and <code>src/evander_team_addon/__init__.py</code> — add-on identity, UI/API mounts, MCP registration, providers, and runtime-pack registration.
- <code>src/evander_team_addon/models.py</code> — canonical domain types, state machines, graph validation, and project derivation.
- <code>src/evander_team_addon/repository.py</code> and <code>src/evander_team_addon/sqlite_store.py</code> — optimistic repository updates, relational persistence, migration, and schema.
- <code>src/evander_team_addon/api.py</code>, <code>mcp.py</code>, and <code>providers.py</code> — the three supported integration surfaces.
- <code>docs/addons.md</code> — add-on loading, Team boundary, UI, and operator contracts.
- <code>tests/test_team_addon_models.py</code>, <code>tests/test_team_addon_repository.py</code>, and <code>tests/test_team_addon_api.py</code> — domain invariants, storage behavior, and mounted UI/API behavior.


# 17. Enabling and Operating the Team Add-on

> **At a glance.** Enable the bundled Team Add-on from Admin Web's Add-ons page, allow Evander to materialize its registered runtime pack, restart the listed services, and verify the UI, API, MCP, and workflow catalogs. Day-to-day Team operation happens in fourteen add-on-owned views. Ordinary domain edits are revision-checked, so an operator must resolve concurrent changes rather than overwrite them; append-only Chat uses a specialized current-conversation contract.

## Before enabling it

The bundled Python package must include <code>evander_team_addon</code>, its <code>addon.json</code>, and the packaged UI assets. The deployment also needs the core capabilities used by the workflows you intend to run:

- persistent Evander sessions for registered Team agents;
- a working gateway for Team MCP tools;
- trigger and effect orchestrators for wakes and asynchronous effects;
- a configured execution backend for persistent and ephemeral work;
- managed Git, artifact publication, communications, model relays, and Qdrant only where the chosen Team features need them.

The add-on is disabled by default. Enabling it is a host configuration change, not merely a browser preference.

## Enablement flow

The supported operator path is:

1. Open Admin Web at <code>/#addons</code>.
2. Select **Team Add-on** and enable it.
3. Save the revision-checked change.
4. Let the Add-ons service materialize the registered <code>team-runtime.v1</code> definition into operator-owned workflow configuration.
5. Restart every service listed by the page, normally including Admin Web, the trigger orchestrator, the effect orchestrator, and the gateway.

Equivalent configuration sets <code>addon_loader.enabled_addon_ids</code> to include <code>team-addon</code>. Explicit module and Python entry-point loading remain compatibility mechanisms, but the Add-ons page is the normal operational surface because it coordinates host and worker configuration and reports the restart boundary.

When the packaged runtime definition changes, use **Refresh runtime pack** on the same page or its equivalent revision-checked Admin API action. A live workflow pack is operator state. Never copy over its JSON file, rewrite it from a checkout, or race another editor with a stale document.

## Verify the complete registration

After restart, verify all four planes:

| Plane | Positive check |
|---|---|
| Admin | <code>/api/admin/state</code> lists <code>team-addon</code>. |
| UI and API | <code>/addons/team-addon/</code> loads and <code>/api/addons/team-addon/templates</code> returns a catalog. |
| MCP | The gateway exposes Team tools only in an authorized Team context. |
| Workflow | The workflow editor catalog and validator recognize <code>trigger:team:*</code>, <code>query:team:*</code>, and <code>effect:team:*</code> providers. |

A <code>404</code> from all Team routes usually indicates loading or restart state. An empty Team view with working routes usually indicates valid but empty Team state. Those are different failures.

## The fourteen operator views

The add-on UI currently has these top-level views:

| View | Primary purpose |
|---|---|
| **Workspace** | Broad operational summary and the next records that need attention. |
| **Operations** | Wake, lease, session, context, stand-down, and runtime attention. |
| **Delivery** | Deliverables, review requests and profiles, releases, and Git promotions. |
| **Memory** | Durable Team memory and semantic-index operations. |
| **Projects** | Project goals, leads, Git configuration, and release policy. |
| **Task board** | Task ownership, hierarchy, dependencies, state, and priority. |
| **Cases** | Intake records, lifecycle state, external references, and linked tasks. |
| **Teams** | Team setup, persistent agents, roles, assignments, and stand-down controls. |
| **Chat** | Human-operator conversations with one persistent agent. |
| **Mail** | Sent and received projections over one durable Team inbox collection. |
| **Governance** | Cross-Team groups, agendas, cost warnings, and directives. |
| **Planning** | Planning-oriented summaries of goals and work. |
| **Timeline** | A derived chronological rendering of current Team records. |
| **Org chart** | Reporting hierarchy and operational details per agent. |

There is no top-level **Roles** view. Roles are maintained inside **Teams** and setup/edit forms. Reviews and releases similarly live inside **Delivery**, not separate top-level tabs.

The Org chart is diagnostic as well as visual. Agent nodes expose session, project, reporting, task, inbox, wake, and ephemeral-capacity information when available. It renders unreachable or malformed references for inspection; rendering never repairs persisted state.

![Team Workspace dashboard with attention, delivery, team, and workload summaries](assets/evander-user-manual/screenshots/team-workspace.png)

<p class="figure-caption">Figure 17.1 — Workspace brings cross-cutting attention together while retaining links to the owning detailed view. Sanitized Northstar fixture.</p>

## Bootstrap Northstar

The quickest way to learn the UI is to preview a built-in template, such as the development-squad template, then materialize it once. Templates create Team-domain records in a single optimistic write. For the Northstar example, use stable IDs and names:

- Team <code>northstar-platform</code>, managed by Mira;
- project <code>mission-control</code>, led by Priya;
- Imani as implementer, Rowan as reviewer, and Riley as release agent;
- project goal and root task for safe telemetry retry behavior.

Before materialization, create or identify the Evander sessions that the persistent agents will reference. A Team template does not provision VMs or containers, create sessions, register managed repositories, push Git branches, or contact an external service.

After materialization:

1. Inspect the reporting tree in **Org chart**.
2. Confirm each agent's stable session mapping and active status in **Teams**.
3. Confirm Priya's project-lead assignment in **Projects** without changing her reporting role.
4. Inspect Imani's task in **Task board** and any intake case in **Cases**.
5. Leave release policy and managed Git unset until the corresponding core resources and verification design are ready.

## Optimistic writes and scoped reads

The canonical Team state has a monotonically advancing revision. Ordinary domain edit routes require <code>expected_revision</code>:

- missing or null revision returns <code>400</code>;
- a stale revision returns <code>409</code>;
- a valid mutation commits the complete validated state and advances the revision.

The UI does not need to reload the full state after every successful write. Endpoints return scoped views and revision invalidations. A state stream can announce that a newer revision exists without embedding all changed records.

Some edit flows support a narrowly defined conflict policy, <code>target_unchanged</code>, with a base resource token. That allows a mutation to rebase only when the particular target still matches what the operator read; creation can rebase only when the explicit ID is still absent. It is not a general “last writer wins” switch.

Append-only Chat send is the important exception. It validates the current conversation and agent and deliberately does not reject a message merely because an unrelated global Team revision advanced. Conversation ownership, signed-in operator identity, agent eligibility, and the asynchronous dispatch rules still apply.

When a <code>409</code> occurs:

1. Preserve the operator's intended change.
2. Reload the affected record and current revision.
3. Compare the concurrent change field by field.
4. Reapply or deliberately merge the intent.
5. Submit against the new revision and verify the returned view.

Never solve a conflict by directly replacing a live pack file or Team database.

## UI identity, privilege, and special media

Administrative mutations are protected by Admin authentication and the add-on's privileged-mutation guard. Chat creation and message submission follow the signed-in human conversation contract rather than pretending to be an agent command; users inspecting another operator's Chat through <code>team_chats.read_all</code> receive read-only behavior.

Optional avatars are UI metadata. Uploaded PNG, JPEG, or WebP images are normalized to square WebP under the Team add-on runtime directory. An OpenAI-generated preview becomes durable only after explicit acceptance. Avatars are not injected into prompts or used as core session identity.

Browser voice calls use OpenAI Realtime with a projection of the selected agent's Team context. They are not DIDWW, Asterisk, or SIP calls. Core voice-session records live outside the Team database; a call started from Chat can be projected back into that conversation as a system message.

## Common setup failures

| Symptom | Cause to test first | Recovery |
|---|---|---|
| Add-on appears enabled but Team UI is absent | Admin Web was not restarted or packaged UI files are missing. | Restart the listed unit and verify the installed package contents. |
| UI loads but tools are missing | Gateway was not restarted with the add-on enabled. | Restart the gateway and test from an authorized Team session. |
| Workflow pack validates as unknown nodes | Trigger/effect service config did not load the add-on or pack refresh was incomplete. | Verify enabled IDs in both runtime configurations, restart, then refresh through the Add-ons API. |
| Template materialization rejects agent references | Core sessions or chosen Team agent IDs do not match the template inputs. | Correct the mapping; do not invent placeholder sessions for production use. |
| Repeated <code>409</code> writes | Another operator or runtime command is advancing Team state. | Reload narrowly, merge intentionally, and retry once with the new revision. |
| An enabled add-on has no runtime behavior | The registered pack is disabled, invalid, or its orchestrators are stopped. | Inspect the live workflow document, validation report, run history, and service state. |

## Practical runbook

### Enable

1. Confirm package and UI assets.
2. Enable **Team Add-on** at <code>/#addons</code>.
3. Save and record the resulting configuration revision.
4. Restart every service named by the page.
5. Verify Admin, UI/API, MCP, and workflow registration.

### Establish a new Team

1. Create persistent core sessions with the right backend and model configuration.
2. Register or select any managed Git repository in the core registry.
3. Preview a Team template or create the Team, roles, agents, project, and assignments explicitly.
4. Configure project Git using only <code>managed_repo_id</code>; configure release policy deliberately.
5. Create the first case, goal, and root task, then confirm reporting and ownership in the Org chart.
6. Trigger or wait for a wake and verify the Operations view, session logs, and resulting command activity.

### Upgrade

1. Deploy the new package.
2. Read release notes and back up Team SQLite plus external referenced stores.
3. Restart through the normal service procedure.
4. Refresh the registered runtime pack through the Add-ons page.
5. Validate it before enabling or resuming runs.
6. Smoke-test a read, a harmless revision-checked edit, one agent wake, and one asynchronous outbox event.

## Source trail

- <code>src/evander_team_addon/__init__.py</code>, <code>addon.json</code>, and <code>ui_static/</code> — registration, mounts, and the current fourteen-view UI.
- <code>src/evander/admin/web/addons.py</code> and the add-on loader services — revision-checked enablement, runtime-pack materialization, and restart reporting.
- <code>src/evander_team_addon/api.py</code> — bootstrap, scoped views, state stream, optimistic writes, templates, avatars, Chat, and voice routes.
- <code>src/evander_team_addon/templates.py</code> — built-in catalog, preview, input binding, and materialization.
- <code>src/evander_team_addon/workflow_packs.py</code> — registered <code>team-runtime.v1</code> definition.
- <code>docs/addons.md</code> — bundled enablement and Team operator surface.
- <code>tests/test_admin_web_addons.py</code>, <code>tests/test_team_addon_api.py</code>, and <code>tests/test_team_addon_runtime.py</code> — loading, conflict behavior, pack registration, and runtime integration.


# 18. Organization, Projects, Tasks, and Cases

> **At a glance.** Team organization is an explicit authorization graph, while projects, cases, goals, and tasks form the work graph. Reporting lines govern directional coordination; project assignments and narrow role permissions govern project actions. Cases preserve intake context, tasks carry executable ownership, and repository validation keeps every cross-link within the right Team and project.

## Two graphs that meet at an agent

The organizational graph says who reports to whom. The work graph says what depends on what. They meet at persistent agents, but neither should be reconstructed from the other.

For Northstar:

- Mira is manager of <code>northstar-platform</code>.
- Priya reports through that hierarchy and leads <code>mission-control</code>.
- Imani owns the telemetry retry task.
- Rowan is assigned its review.
- Riley has release permission.

Priya's project leadership does not make her the Team manager. Rowan's review assignment does not make Rowan Imani's superior. Riley's release permission does not confer task ownership. Keeping these statements independent makes authorization explainable.

## Teams, roles, and reporting lines

A Team has exactly one manager identity and may contain subordinate agents. Agents are stable domain records attached to core session IDs. Display names and avatars help people navigate the UI but do not participate in authorization.

A role contains:

- a stable ID and display name;
- a Team and optional project scope;
- a default ephemeral-delegation policy;
- a narrow permission set;
- archival state.

Current permission names include <code>create_root_task</code>, <code>prepare_bookkeeping_action</code>, <code>request_payment_preparation</code>, <code>request_release</code>, and <code>review_bookkeeping_action</code>. They are capabilities, not a replacement for contextual checks. For example, <code>request_release</code> is still limited to the relevant project and accepted scope.

Reporting relationships constrain agent mail:

- questions and progress reports travel upward to the direct superior;
- status requests travel downward to a direct subordinate;
- no ordinary tool permits skip-level messaging.

This keeps routine coordination local. Broader intervention uses task reassignment, manager routing, governance, or an operator directive rather than a covert communication shortcut.

An active stand-down overlays the hierarchy. A hold on an agent's subtree prevents descendants from acquiring Team work even if their tasks otherwise appear ready. Chapter 20 covers its runtime effect.

![Team Org chart for Mira, Priya, Imani, Rowan, and Riley](assets/evander-user-manual/screenshots/team-org-chart.png)

<p class="figure-caption">Figure 18.1 — The Org chart projects explicit reporting and operational relationships; it does not infer authority from project work. Sanitized Northstar fixture.</p>

## Project membership and leadership

A project belongs to one Team and has:

- a stable project lead chosen from registered Team agents;
- explicit member assignments;
- goals and derived status;
- optional managed-Git configuration;
- optional release policy.

Assignments distinguish <code>project_lead</code> and <code>project_member</code>. Project leadership is stored on the project and its assignment, not encoded by changing the agent's primary Team role or reporting line.

Project Git configuration references a core <code>managed_repo_id</code>. It may also select a base branch and canonical release prefix. The Team Add-on does not accept a host checkout path as repository identity.

## Goals

Goals express outcomes and use the shared record states <code>pending</code>, <code>in_progress</code>, <code>accepted</code>, <code>rejected</code>, and <code>blocked</code> to drive project status. They are not tasks: a goal can be satisfied by multiple tasks and deliverables.

For <code>mission-control</code>, a goal might be “telemetry delivery survives transient upstream failures without duplicate submissions.” Imani's retry implementation, Rowan's review, and Riley's eventual release are steps toward that outcome. Accepting a single task should not automatically accept a broader goal unless the evidence really establishes it.

The <code>team_update_goal</code> command permits an authorized agent to update goal state inside its current Team context. Admin and workflow paths expose corresponding project state through revision-checked APIs and queries.

## Tasks: the executable work graph

A task records Team, project, owner, creator, title, description, priority, state, and optional:

- parent task;
- dependency tasks;
- case;
- deliverables;
- metadata and timestamps.

Task state has a deliberate review-aware lifecycle:

| State | Meaning |
|---|---|
| <code>pending</code> | Defined but not yet actively executed. |
| <code>in_progress</code> | The owner is working or reworking it. |
| <code>blocked</code> | Work cannot proceed without a named dependency or intervention. |
| <code>waiting_for_review</code> | Evidence has been submitted and active review remains. |
| <code>finished</code> | Work was marked finished but has not completed the acceptance cascade. |
| <code>accepted</code> | Required deliverables and reviews have accepted the task outcome. |
| <code>rejected</code> | The task outcome was rejected under the applicable transition. |

The state payload derives child IDs, subtask counts, dependency status, and blockers. A task whose dependencies are waiting or blocked is not ready merely because its own state says pending.

Only authorized agents may create a root task. An owner may decompose assigned work into child tasks within policy, and <code>team_create_tasks</code> provides one validated batch mutation. Batch validation happens before commit, so a bad item does not leave a half-created plan.

Task graphs cannot contain parent or dependency cycles. Dependencies and parents remain within the project. A deliverable, review, or Git work context cannot silently migrate a task to another project.

![Team task board showing telemetry-retry work by lane](assets/evander-user-manual/screenshots/team-task-board.png)

<p class="figure-caption">Figure 18.2 — The task board is a workflow-lane projection over validated tasks, dependencies, and ownership. Sanitized Northstar fixture.</p>

## Cases: intake, not execution

A case holds the durable story around incoming work:

- title, description, state, priority, owner, and tags;
- optional project scope;
- optional external <code>source</code> and <code>source_ref</code>;
- linked task IDs.

Cases use <code>open</code>, <code>triage</code>, <code>in_progress</code>, <code>waiting</code>, <code>resolved</code>, and <code>closed</code>. External systems such as GitHub, Jira, Zendesk, or email remain references only; the base Team Add-on does not synchronize them.

Suppose Northstar receives support report <code>OBS-184</code>: telemetry delivery fails during short gateway outages. Create case <code>telemetry-retry-incident</code> with <code>source=observability</code> and <code>source_ref=OBS-184</code>. Link Imani's execution task to it. The case can remain open while several tasks investigate, implement, document, and deploy the fix.

The case/task link is bidirectional. If a task names a case, the case must list that task; repository mutations maintain and validate both sides. A project-scoped case cannot link work from another project.

## Work creation surfaces

Operators can create and edit projects, cases, goals, and tasks through the UI and Admin API with <code>expected_revision</code>. Agent commands include:

- <code>team_create_case</code> and <code>team_update_case</code>;
- <code>team_create_task</code> and <code>team_create_tasks</code>;
- <code>team_update_task</code> and <code>team_update_goal</code>;
- task, case, project, agent, and identity read tools.

Workflow authors can use <code>effect:team:create-case</code>, <code>effect:team:update-case</code>, and <code>effect:team:create-task</code>. These providers are useful for an intake bridge: first create or update a case using the external stable reference, then create concrete Team work and let the runtime wake its owner.

The effect providers use the same model validation. They are not a back door around ownership, scope, or graph rules.

## Readiness and derived project state

The wake service evaluates ready work rather than simply searching for nonterminal tasks. It considers:

- ownership and active agent status;
- dependency readiness;
- pending reviews and rework;
- inbox and action feedback;
- active leases, stand-downs, no-progress state, and cost caps.

A task in <code>waiting_for_review</code> with an active review normally wakes its reviewer, not its producer. A rejected review returns the task to active rework. Accepted deliverables and closed findings can cascade the task to accepted. Only then should project goals and status be reevaluated.

The repository derives project status. A project is completed only when all required goals are accepted and no active work remains. Blocking work can produce blocked status; otherwise it remains active. An operator should fix the underlying goal/task state rather than overwrite the project summary.

## Northstar work breakdown

One sensible plan is:

1. Case <code>telemetry-retry-incident</code> captures <code>OBS-184</code>, user impact, reproduction evidence, and priority.
2. Root task <code>telemetry-retry</code>, owned by Imani, defines bounded retry and idempotency behavior.
3. Child task <code>telemetry-retry-tests</code> depends on the implementation seam or is assigned as part of the same work package.
4. Imani submits a deliverable containing the pinned Git context and verification evidence.
5. Rowan reviews the current deliverable, not a moving branch tip.
6. Priya updates the goal only when the accepted evidence demonstrates the intended outcome.
7. Riley requests a release only after project and release rules are satisfied.

That sequence lets later chapters build from one traceable line of work.

## Failure patterns

| Error or symptom | Meaning | Recovery |
|---|---|---|
| Unknown role, agent, or project | A referenced stable ID is absent or archived. | Reload Team state and choose an active in-scope record. |
| Reporting cycle or invalid manager hierarchy | The organizational graph would no longer have a valid root. | Correct the proposed reporting edge; do not patch the database. |
| Parent or dependency cycle | The task graph would make readiness undefined. | Remove or reverse the bad edge and resubmit the whole intended mutation. |
| Case/task mismatch | Only one side of the relation, Team, or project agrees. | Update through the supported case/task mutation so both sides converge. |
| Task never wakes | It is not actually ready, or runtime safety guards apply. | Inspect derived dependency status, active review, lease, stand-down, cost, and no-progress attention. |
| Project status looks wrong | A goal, task, review, or deliverable remains active or blocked. | Inspect the underlying records; project status is derived. |
| External ticket changes do not appear | A source reference was mistaken for synchronization. | Add a workflow integration or update the case through its API/effect. |

## Practical runbook

When establishing a project:

1. Confirm every persistent agent has a valid core session, active Team record, role, and reporting position.
2. Create the project and select an existing agent as lead.
3. Add explicit project-member assignments.
4. Define goals as outcomes, not implementation steps.
5. Create intake cases with stable external references where useful.
6. Create root tasks with clear owners, then add only necessary parent and dependency edges.
7. Verify the Org chart, Task board dependency summary, Cases links, and project status at the returned revision.

When work appears stuck:

1. Read the task, owner, dependencies, current deliverable, and active review.
2. Read the agent runtime attention and inherited stand-down state.
3. Inspect pending inbox and action feedback.
4. Check Team cost coverage and cap.
5. Correct the domain record or runtime blocker through its owning surface.
6. Confirm that a wake is queued or that the normal scheduler will evaluate it.

## Source trail

- <code>src/evander_team_addon/models.py</code> — Team, role, agent, assignment, project, goal, case, and task models plus graph validation.
- <code>src/evander_team_addon/services/commands/task_creation_commands.py</code>, <code>task_update_commands.py</code>, and <code>case_commands.py</code> — task and case authorization and mutation behavior.
- <code>src/evander_team_addon/api.py</code> — revision-checked project, role, agent, case, and task routes.
- <code>src/evander_team_addon/mcp.py</code> — agent-facing organization and work commands and grouped read tools.
- <code>src/evander_team_addon/providers.py</code> and <code>provider_schemas.py</code> — workflow case/task effects and filtered state queries.
- <code>docs/addons.md</code> — Team roles, cases, tasks, templates, communication, and runtime overview.
- <code>tests/test_team_addon_models.py</code>, <code>tests/test_team_addon_mcp.py</code>, and <code>tests/test_team_addon_api.py</code> — graph, authorization, link, and transition cases.


# 19. Commands, Identity, Revisions, and the Durable Outbox

> **At a glance.** A Team MCP command is not a loose edit followed by best-effort messaging. The server derives the caller from the connected session, validates its current command scope, mutates the complete Team model, handles selected inbox items, records dispatch activity, enqueues asynchronous intent, and stores an idempotent response inside one SQLite write transaction. External effects happen later through the durable outbox and workflow runtime.

## Two concurrency contracts

The Team Add-on exposes two related but distinct write contracts.

Ordinary administrative domain writes use optimistic state revisions. The client reads revision 41, submits <code>expected_revision=41</code>, and either commits revision 42 or receives a conflict because someone else already advanced the state. Append-only Chat is deliberately outside this general edit contract and validates its current conversation instead.

Agent MCP commands use a runtime identity and dispatch contract. The caller supplies the business arguments and, for replay-sensitive operations, a <code>request_id</code>. It does not get to claim “I am Imani on dispatch X.” The gateway connection and session determine that.

Both paths end in the same validated SQLite state, but they solve different problems:

| Concern | Admin mutation | Team command |
|---|---|---|
| Human or automation identity | Admin authentication or workflow context | Ambient MCP session plus resolved Team scope |
| Concurrency | <code>expected_revision</code> | Serialized transaction plus active dispatch/lease |
| Replay | Client resolves revision conflicts | <code>request_id</code> idempotency record |
| Async work | Route or service may enqueue it | Durable outbox intent in the command transaction |
| Inbox handling | Separate revision-checked route or edit | Optional atomic <code>handled_inbox_item_ids</code> |

## Identity is ambient

For an ordinary persistent wake, the command executor resolves:

- the ambient core session;
- the registered persistent Team agent;
- Team, role, project, and reporting scope;
- the current active Team dispatch and unexpired lease;
- active stand-downs and agent status.

An old explicit <code>session_id</code> argument may be accepted for compatibility, but it must agree with the connected session. It cannot switch identity.

Governance commands use a governance-group scope and validate that the caller manages that group and, when required, holds its current lease. Ephemeral commands use the assignment/session scope; an ephemeral worker does not borrow its parent's persistent dispatch. Its narrow tool surface is described in Chapter 23.

An inactive, teamless, stood-down, mismatched, or unleased persistent agent cannot mutate normal Team state. Tool hiding improves the prompt, but server authorization remains decisive even if a client manually invokes a hidden name.

## The command transaction

Conceptually, a successful command follows this order:

1. Normalize command context, payload, and inbox IDs.
2. Begin a serialized SQLite write transaction.
3. Read and validate the complete current <code>TeamState</code>.
4. Resolve the ambient caller and command scope.
5. Check for an idempotent replay.
6. Validate every requested inbox item and the command-specific authorization.
7. Apply the domain mutation in memory.
8. Mark the caller-owned pending inbox items handled.
9. Advance the Team revision and validate the complete next state.
10. Insert or reuse durable outbox records.
11. Record successful dispatch activity and its progress classification.
12. Store the idempotency response, including created domain and event IDs.
13. Commit.

If validation fails after identity can be resolved, the executor records a failure activity and returns a structured correction response without applying the requested domain mutation or advancing Team state. If the transaction itself fails, SQLite rolls it back.

This design gives Northstar a useful guarantee. If Imani submits the telemetry retry deliverable while marking Priya's status request handled, a committed response means both Team facts are committed together. It cannot acknowledge the request while silently losing the deliverable.

![Atomic Team command and durable outbox flow](assets/evander-user-manual/diagrams/12-team-command-outbox.svg)

<p class="figure-caption">Figure 19.1 — State, inbox handling, activity, replay evidence, and outbox intent commit together.</p>

## Idempotency

Commands that create, append, send, or enqueue asynchronous work should use a stable <code>request_id</code>. The effective replay key contains:

- tool name;
- resolved caller identity;
- scope kind and scope ID;
- request ID.

The executor also stores a digest of the normalized payload.

Reusing the same key with the same payload returns the original stored response. It does not create a second task, message, deliverable, escalation, ephemeral assignment, Git promotion, or outbox row. Reusing the same key with a different payload returns <code>idempotency_conflict</code> and does not mutate state.

Choose request IDs at the semantic operation boundary. For example:

<code>northstar:telemetry-retry:deliverable:v2</code>

Do not generate a fresh random request ID on every network retry, because that converts a transport retry into a new business command. Do not reuse one ID for “submit v2” and “submit v3,” because those are different operations.

Responses must refer to the durable row that actually won deduplication. When an outbox idempotency key already exists, the transaction returns the stored outbox record ID rather than a newly generated but unpersisted candidate ID.

## Atomic inbox handling

Many commands accept <code>handled_inbox_item_ids</code>. Every listed item must:

- exist;
- be pending;
- belong to the resolved caller;
- be legal for that command context.

The executor validates the complete list before changing any item. One bad ID rejects the whole command. This is preferable to a two-call pattern in which an agent handles a directive and then crashes before recording the work it performed.

Use the feature for genuine “read, act, and close” operations. A message with <code>response_requested=false</code> normally needs no acknowledgement, but the recipient should still handle it after reading or acting. Chapter 21 explains that reply intent and wake priority are independent.

## Dispatch activity and progress

Every resolved command attempt can produce a durable <code>TeamDispatchActivity</code> with:

- tool and caller identity;
- persistent agent, governance group, or ephemeral assignment;
- dispatch and request IDs;
- correlation and causation IDs;
- success or failure status;
- progress classification;
- related action or audit record IDs.

The dispatch finalizer, not the individual command, closes a persistent lease. It reads this ledger and decides whether substantive progress occurred. Context-only actions and no-op communication can be tracked without falsely resetting the no-progress throttle.

Use one <code>correlation_id</code> to group related actions across a Northstar telemetry-retry dispatch. Use <code>causation_id</code> to identify the directive, inbox item, or earlier action that caused the next one. These fields improve diagnosis; they do not alter authorization.

## The durable outbox

Some accepted commands describe work that cannot safely happen inside the database transaction: publish an internal event, wake an agent, start an ephemeral worker, perform Git promotion, run release recovery, or route an escalation.

The command writes a <code>TeamOutboxRecord</code> instead. Important fields include:

- intent type;
- aggregate type and ID;
- optional unique idempotency key;
- payload and availability time;
- status, attempt count, claim token, and claim expiry;
- last error and timestamps.

Outbox state moves through <code>pending</code>, <code>claimed</code>, <code>completed</code>, and <code>failed</code>. A publisher claims eligible rows with a lease. On success it completes them. On a configured retry it records the error and makes the row pending at a later availability time. A failure recorded without a retry delay remains failed.

The bundled runtime pack calls <code>effect:team:publish-outbox-events</code> on its cron loop and after command-driven dispatch paths. Publication creates the Team internal event that the rest of the workflow graph consumes.

The command response therefore distinguishes durable acceptance from external completion. “Promotion requested” means the request and outbox intent are committed. It does not mean Git finished. “Escalation queued” does not mean the communication provider delivered it.

## Northstar transaction traces

### Create a task

Priya calls <code>team_create_task</code> with a stable request ID. The transaction validates her authority, creates the task, advances the revision, records command activity, and stores the replay response. If the ready-task transition emits a wake event, its outbox row is committed with the task.

### Submit a deliverable

Imani publishes local <code>artifact_paths</code> before the final Team transaction. The command then validates task ownership and Git work context, creates the deliverable, changes the task to <code>waiting_for_review</code>, handles selected inbox items, and queues any notice or wake intent. If artifact publication succeeded but the Team transaction later fails, the stable artifact can be orphaned; replay or deliberate cleanup must reconcile it.

### Request a release

Riley's command freezes the accepted scope and any prerequisite Git promotions, stores the release package, and enqueues the next durable intent. The response says which release, promotion, and outbox records were accepted. The Delivery view and later workflow runs say whether verification and publication actually succeeded.

## Failure patterns

| Response or symptom | Meaning | Operator or caller action |
|---|---|---|
| Identity/session conflict | Supplied compatibility identity disagrees with the connection. | Stop supplying identity fields; use the intended session. |
| No active dispatch or lease mismatch | A persistent agent called outside its leased Team wake. | Diagnose routing and lease state; do not fabricate a dispatch ID. |
| Stand-down error | The caller or an ancestor has an active hold. | Resolve the hold through its authorized revocation path. |
| <code>idempotency_conflict</code> | One request ID was reused for different normalized content. | Read the original response and issue a new semantic request ID only for truly new work. |
| Invalid handled inbox item | At least one ID is missing, already handled, or owned by another agent. | Reload the caller inbox and resubmit the atomic command with the correct set. |
| Command accepted but effect absent | The outbox row is still pending, claimed, retrying, or failed. | Inspect the exact outbox record, publisher workflow run, and downstream event route. |
| Duplicate external activity despite one command | A downstream effect lacks or violated its own idempotency/lease contract. | Preserve evidence and diagnose the effect; do not delete the Team idempotency record. |

## Practical runbook

For an agent command problem:

1. Capture the tool, ambient session, request ID, returned activity ID, outbox IDs, and Team revision.
2. Read the current persistent-agent or assignment mapping and runtime lease.
3. Look up dispatch activity for that dispatch and caller.
4. If replay is involved, inspect the stored idempotency record and payload digest.
5. If asynchronous work is involved, follow the exact outbox record through claim, event publication, workflow run, and effect result.
6. Retry with the same request ID only when repeating the same semantic payload.

For an Admin <code>409</code>, do not look for a command replay. Reload the resource, merge the competing revision, and submit with the new <code>expected_revision</code>.

For database inspection, use a consistent snapshot and current schema. The relevant tables are relational Team-domain tables plus <code>team_dispatch_activity</code>, <code>team_idempotency_records</code>, and <code>team_outbox</code>. Direct edits bypass full-model validation and are unsupported.

## Source trail

- <code>src/evander_team_addon/services/commands/executor.py</code>, <code>context.py</code>, and <code>types.py</code> — ambient identity, command scopes, validation, transaction order, and activity.
- <code>src/evander_team_addon/services/commands/outbox.py</code> and <code>responses.py</code> — event conversion, durable response IDs, replay, and correction payloads.
- <code>src/evander_team_addon/sqlite_store.py</code> — serialized command transaction, idempotency keys, outbox claims, retries, and schema.
- <code>src/evander_team_addon/mcp.py</code> — grouped command/read registration and session-aware call context.
- <code>src/evander_team_addon/providers.py</code> and <code>workflow_packs.py</code> — outbox publication provider and runtime routes.
- <code>tests/test_team_addon_mcp.py</code>, <code>tests/test_team_addon_outbox_runtime.py</code>, and <code>tests/test_team_addon_repository.py</code> — identity, replay, rollback, stored outbox IDs, and publication behavior.


# 20. Wakes, Leases, Context Rotation, and Stand-down

> **At a glance.** A Team wake is a leased dispatch of already-eligible work to one persistent agent session. Eligibility, immediacy, and safety are separate decisions: normal/high inbox can wake even without a requested reply; low-priority mail becomes its own wake reason only when it requests a response, and is then batched; active leases, busy sessions, stand-downs, cost caps, and no-progress controls still prevent unsafe or wasteful dispatch. Completion is finalized from durable command activity.

## The wake loop

The registered Team runtime pack runs a periodic evaluation, by default every fifteen minutes. Its wake effect asks the Team runtime to lease due agents. The current service defaults are:

| Setting | Default |
|---|---|
| Maximum leases per evaluation | 10 |
| Dispatch lease | 900 seconds |
| Normal wake interval | 900 seconds |
| No-progress threshold | 3 |
| Low-priority batch threshold | 4 evaluation cycles |

These are provider parameters and may be configured in the live workflow pack. Treat that revision-checked live pack as operational truth.

The wake effect prepares the persistent session, optionally prepares the project's managed repository checkout, constructs a dynamic Team context, and resumes the agent. Static Team guidance is maintained in a managed section of the guest's <code>AGENTS.md</code>; repeated wakes do not need to restate every rule.

## Eligibility is not just “has an open task”

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

![Team agent wake decision flow](assets/evander-user-manual/diagrams/13-team-wake-decision.svg)

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

## Leases and dispatch identity

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

## Runtime and workflow surfaces

The bundled pack coordinates:

- the cron wake evaluation;
- debounced <code>team.agent.wake_requested</code> events;
- core session completion classified by <code>query:team:session-routing</code>;
- persistent dispatch finalization;
- Chat claims, governance wakes, ephemeral lifecycle, outbox publication, Git, release, and escalation routes described later.

The primary runtime provider is <code>effect:team:wake-due-agents</code>. Operators should inspect its outputs, including leased agents and repository-preparation failures, rather than assuming a workflow run with no prompt is a model failure.

The Operations view and Team runtime-attention reads expose wake due state, leases, stand-down, context policy, no-progress counters, and downstream failures. Core session logs remain necessary for startup and provider failures.

## Failure patterns

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

## Practical runbook

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


# 21. Chat, Agent Mail, Voice, and Escalation

> **At a glance.** Team communication has four distinct paths. Chat is a signed-in human operator's durable conversation with one persistent agent. Agent Mail is directional agent-to-agent inbox state governed by reporting lines. Voice is a browser OpenAI Realtime session projected from Team context. Escalation is a manager-to-operator or manager-to-governance workflow. They share some wake machinery, but not identity, authorization, storage, or delivery semantics.

## Choose the path by who is speaking

| Source and destination | Correct path |
|---|---|
| Human operator → persistent agent | **Chat**, or an operator directive when durable Team work is intended |
| Persistent agent → direct superior | Agent Mail question or progress report |
| Persistent superior → direct subordinate | Agent Mail status request |
| Top-level manager → human/external operator target | Direct escalation workflow |
| Top-level manager → governance manager | Governance agenda escalation |
| Human operator ↔ persistent agent by audio | Browser voice call |

The paths are not interchangeable. A Chat transcript is not an agent inbox. A communication target does not grant inbound access to the manager's session. A voice call is not an Agent Mail exchange. The distinction protects both user expectations and authorization.

## Operator Chat

A Team conversation contains exactly one signed-in operator user and one persistent agent. That operator/agent pair has one durable conversation. Messages record sender, status, content, timestamps, error information, and dispatch metadata.

Operators normally see their own conversations. A user with <code>team_chats.read_all</code> may inspect another operator's history, but the UI marks that mode read-only and blocks sending. The permission does not broaden Agent Mail visibility.

Sending a Chat message does not execute the agent inline in the API request:

1. The Admin API records the operator message and queues <code>team.chat.message_queued</code>.
2. The Team runtime claims a queued message only when the agent is active, not stood down, not cost-blocked, not already leased, and its core session is not busy.
3. It creates a Chat dispatch and prompts the agent with the human operator identity and message context.
4. The agent replies with <code>team_reply_to_chat</code>.
5. Dispatch finalization marks the operator message replied; a dispatch without a successful reply is marked failed with the available error context.

A Chat dispatch can be **reply-only** or allow ordinary Team mutations. Its <code>allow_team_mutations</code> metadata controls whether the agent receives normal Team action tools alongside <code>team_reply_to_chat</code>. When a human asks “please change Imani's task priority,” a mutation-enabled dispatch can perform the authorized state change and reply. A reply-only dispatch must answer without changing Team state.

The agent may use <code>team_send_operator_chat_message</code> only for operator participants listed in its current dispatch context. That is a bounded proactive message, not a way to enumerate arbitrary Admin users.

![Team Chat conversation between a synthetic operator and Imani](assets/evander-user-manual/screenshots/team-chat.png)

<p class="figure-caption">Figure 21.1 — Human Chat is visually and operationally separate from Agent Mail and ordinary Team work. Sanitized Northstar fixture.</p>

## Agent Mail

The **Mail** view renders durable <code>AgentInboxItem</code> records. Each item has a type, sender, recipient, priority, reply intent, payload, state, and timestamps. Types include:

- <code>operator_directive</code>;
- <code>question</code>;
- <code>status_request</code>;
- <code>progress_report</code>;
- <code>deliverable_notice</code>;
- <code>escalation</code>.

State is <code>pending</code> or <code>handled</code>. The UI presents received and sent projections over this one durable inbox collection, with structured payload details, errors, and fallback content. There is no separate Agent Mail outbox collection, and Mail is not backed by the Chat conversation tables.

Direction is enforced:

- <code>team_ask_question</code> targets the caller's direct superior;
- <code>team_send_progress_report</code> targets the direct superior and defaults to low priority with no response requested;
- <code>team_request_status</code> targets a direct subordinate and normally requests a response;
- ordinary agent mail does not permit skip-level or lateral routing.

If broader coordination is necessary, change the work structure, ask the manager to route it, or use a governance/operator surface. Do not disguise lateral communication as a status report.

### Reply intent is not wake permission

<code>response_requested</code> tells the recipient whether a meaningful response is expected. It does not control delivery and is only one part of wake eligibility.

- normal or high priority can wake even when <code>response_requested=false</code>;
- low priority wakes by itself only when a response is requested, and is then normally batched;
- low/no-reply mail is delivered on another eligible wake;
- every processed item should eventually be marked handled.

When no response is requested, do not send a thank-you, receipt, or empty acknowledgement. Read it, act if needed, and handle it atomically with the relevant command where possible.

For Northstar, Imani can send Priya a low/no-reply progress report saying the retry algorithm is implemented and tests are running. It should not cause an immediate conversational cycle. Priya can send Imani a normal status request, which wakes Imani and expects a useful answer.

## Operator directives

A directive is durable Team work addressed to a Team manager, not merely a Chat utterance. It can be created through:

- the Team Admin API;
- <code>effect:team:create-operator-directive</code> from Discord, Teams, Admin Web, or another workflow;
- <code>team_create_operator_directive</code> by an authorized governance manager.

It becomes a pending normal-priority, response-requested <code>operator_directive</code> in the manager's inbox and makes the manager due. A governance manager can direct only a member Team and only its manager. The manager handles it through ordinary Team work and communication.

Use a directive when “please investigate telemetry retry safety” should survive a Chat failure, become schedulable work, and have explicit handled state.

## Browser voice

The Team UI can start a direct browser voice call to a persistent agent. The add-on prepares an OpenAI Realtime session with a projection of the agent's current:

- Team, role, project, and reporting context;
- tasks, cases, inbox, reviews, deliverables, and runtime state;
- authorized visible tool context;
- optional voice profile and speaking style.

The browser talks to OpenAI Realtime using the prepared session. This path does not use DIDWW, Asterisk, or SIP. Core voice-session state is stored in <code>runtime/voice-sessions-store.json</code>, outside Team SQLite.

Projected context is intentionally bounded. If the realtime agent needs nonprojected detail, it can create a durable <code>query_origin_agent</code> request for the originating text agent. Terminal calls schedule reconciliation so those requests and final call state can re-enter the normal wake flow.

A call launched from Chat can add a voice-call system message to that conversation. The audio call still remains a voice session rather than a sequence of Chat messages.

An avatar is unrelated UI metadata. Saving Rowan's portrait does not select Rowan's voice or change prompt identity.

## Escalation

Only the top-level Team manager may escalate out of the Team.

### Direct operator escalation

When <code>Team.escalate_to_governance</code> is false, the Team needs an <code>operator_communication_target_id</code>. The manager's <code>team_escalate_operator</code> command records durable intent and an outbox event. The runtime pack routes <code>team.operator.escalation_requested</code> to the generic <code>effect:communication:send-message</code> provider bound to that target.

The Team service does not call a Discord or Teams sender directly. The workflow document owns routing and delivery evidence.

The target is outbound-only. It does not let external users address or wake Mira's real session. Inbound Discord or Teams access must be configured on the core session with the appropriate readable communication target and tool policy.

### Governance escalation

When <code>escalate_to_governance</code> is true, the command creates pending governance agenda items in the groups containing that Team and makes their runtimes due. The governance manager receives Team summaries and decides whether to route an operator directive, mark the agenda handled, or take another authorized action.

This path is useful when Mira must report that the telemetry fix is blocked by a cross-Team API policy. It is not a substitute for a routine question to Priya.

## Failure and delivery semantics

| Symptom | Interpretation and next check |
|---|---|
| Chat message remains queued | Inspect agent active status, stand-down, cost cap, active lease, busy core session, event publication, and Chat-claim workflow. |
| Chat dispatch finishes without a reply | Finalizer should mark the message failed; inspect command activity and prompt/session logs. |
| Agent cannot mail a peer | Lateral routing is intentionally forbidden; use reporting or work-routing mechanisms. |
| No-reply normal mail woke the recipient | Correct behavior: priority is non-low. |
| Low/no-reply mail is unseen | It waits for another eligible wake; inspect pending Mail and eventual handling. |
| Escalation is recorded but not delivered | Follow the outbox row, internal event, workflow run, communication target, and provider result. |
| External operator cannot message the manager | Outbound target was mistaken for inbound session authorization. |
| Voice has incomplete knowledge | The projection is bounded; inspect or retry the durable origin-agent query path. |
| Voice record is absent from Team SQLite | Correct storage boundary; inspect the core voice-session store and optional Chat projection. |

## Practical runbook

For Chat:

1. Record conversation ID, operator message ID, agent/session, and message status.
2. Follow <code>team.chat.message_queued</code> from its published internal event into the runtime workflow. When a queued event originated in a Team command or finalizer, include its outbox row in that trace.
3. Inspect lease/busy/cost/stand-down guards.
4. Match the Chat dispatch to <code>team_reply_to_chat</code> activity and finalization.

For Agent Mail:

1. Confirm sender and recipient are a legal direct reporting pair.
2. Inspect type, priority, <code>response_requested</code>, and pending/handled state.
3. Apply the wake rule from Chapter 20 rather than assuming all pending mail wakes.
4. When acting, handle the item in the same command transaction.

For escalation:

1. Verify the caller is the top-level manager.
2. Confirm direct-versus-governance Team policy.
3. For direct delivery, verify the communication target and live workflow route.
4. For governance, verify group membership, agenda creation, manager lease, directive, and handled state.

For voice:

1. Capture voice-session ID and originating Team conversation if any.
2. Inspect the prepared projection and terminal status in the core voice store.
3. Follow unresolved <code>query_origin_agent</code> records and reconciliation wakes.

## Source trail

- <code>src/evander_team_addon/services/chat_service.py</code> and <code>services/domain/chat.py</code> — conversation ownership, queued messages, claim guards, and reply finalization.
- <code>src/evander_team_addon/services/commands/agent_message_commands.py</code>, <code>chat_commands.py</code>, and <code>operator_escalation_commands.py</code> — directional Mail, Chat replies, and escalation commands.
- <code>src/evander_team_addon/api.py</code> — Chat streams/messages, inbox views, operator directives, and browser voice routes.
- <code>src/evander_team_addon/providers.py</code> and <code>workflow_packs.py</code> — Chat wake prompts, escalation routing, governance wakes, and voice reconciliation integration.
- <code>src/evander_team_addon/models.py</code> — conversations, messages, inbox, communication targets, voice profiles, and governance agenda.
- <code>docs/addons.md</code> — Chat/Mail distinction, voice boundary, communication direction, and escalation.
- <code>tests/test_team_addon_runtime.py</code>, <code>tests/test_team_addon_mcp.py</code>, and <code>tests/test_team_addon_api.py</code> — operator isolation, reply-only dispatch, direction, wake, and escalation behavior.


# 22. Deliverables, Reviews, and Decision Cascades

> **At a glance.** A Team deliverable is a versioned evidence record for one task attempt, not merely a “done” flag. A review targets that exact deliverable and, for Git work, its pinned reviewed commit. Decisions are immutable, criteria are policy-enforced, and acceptance or rejection cascades deterministically through findings, deliverable state, task state, and project status. Rework creates a superseding deliverable instead of rewriting history.

## Evidence before opinion

Tasks describe intended work. Deliverables describe what was produced and how it was checked. Review requests bind a reviewer and policy to that evidence.

A deliverable can include:

- a summary and producer;
- stable artifact references;
- a persistent Git work-context ID and derived agent branch/commit;
- verification status, summary, and evidence refs;
- supersession links;
- review findings and state.

Verification status is <code>passed</code>, <code>failed</code>, <code>partial</code>, <code>not_run</code>, or <code>unknown</code>. “Tests could not run because the integration service was unavailable” is <code>not_run</code> with a concrete explanation, not <code>passed</code>.

For Northstar, Imani's first telemetry retry deliverable might contain:

- the pinned commit from Imani's task worktree;
- unit and fault-injection test evidence;
- an artifact with the retry-state-machine trace;
- verification status <code>passed</code>;
- a concise summary of backoff, jitter, idempotency, and exhaustion behavior.

## Artifact publication boundary

There are two different ways artifact information can appear:

- <code>artifact_paths</code> asks the command adapter to publish safe paths from the current session's <code>/artifacts</code> tree and store the resulting stable refs;
- <code>artifact_refs</code> or an artifact-space reference records already-published metadata.

Supplying a ref does not publish bytes. Paths are validated as safe relative paths, and host publication is replay-idempotent. Publication occurs before the final Team state transaction because it crosses service boundaries. If publication succeeds but the Team mutation fails, the artifact may be orphaned until the same command is replayed or an operator cleans it up deliberately.

Reviewer wakes import referenced review artifacts to a bounded path below:

<code>/artifacts/team-review-inputs/&lt;review&gt;/&lt;deliverable&gt;/...</code>

The review context records successful imports and permission or retrieval errors. A reviewer should distinguish “evidence disproves the change” from “evidence could not be accessed.”

## Persistent Git work contexts

When a project uses managed Git, a persistent Team wake creates or reuses one task-specific work context and worktree. The context binds:

- agent, task, project, session, and dispatch;
- deterministic agent branch;
- prepared checkout/worktree;
- status and timestamps.

A Git deliverable must submit its <code>branch_context_id</code>. The server verifies the caller, task, dispatch, and context and derives the accepted agent branch. An agent cannot substitute an arbitrary branch string. Artifact-only deliverables remain valid without Git.

The branch is evidence inside the managed bare repository, not a canonical publication. Promotion begins only through the reviewed Git flows in Chapter 24.

## Submitting and requesting review

<code>team_submit_deliverable</code> records evidence and changes the task to <code>waiting_for_review</code>. The combined <code>team_submit_deliverable_for_review</code> can submit and create the intended handoff without a redundant intermediate notice.

A review may be requested by an authorized producer, supervisor, project lead, or Team manager. The assigned reviewer must be active, in the same Team, and compatible with any selected review profile.

Review profiles can apply by project and role and define required and optional criteria. Archived profiles cannot be assigned to new reviews. Use profiles for repeatable policy such as:

| Criterion | Requirement for telemetry retry |
|---|---|
| Behavior | Bounded exponential backoff and idempotent retry are demonstrated. |
| Tests | Transient recovery and terminal exhaustion tests pass. |
| Observability | Attempt count and final failure are diagnosable without sensitive payloads. |
| Scope | Changed paths match the task and reviewed evidence. |

Creating the same active review for one deliverable and reviewer is replay-safe: the service returns the existing review rather than duplicating inbox and wake work.

Workflow authors can use <code>trigger:team:deliverable-created</code> and one <code>effect:team:create-review-request</code> node per required reviewer/profile. The trigger owns its cursor and suppresses old deliverables when first enabled, avoiding an accidental historical review storm.

## Review decisions

Only the assigned reviewer may decide a review. The decision is immutable.

### Accepted

For a profiled review, every required criterion must be present and passed. Optional criteria may be omitted. The accepted decision pins its reviewed evidence and findings.

A deliverable becomes accepted only when:

- all active attached reviews are accepted; and
- no carried review finding remains open.

When every current required deliverable for the task is accepted, the task can become accepted. Project status and goals then recalculate under their own rules.

### Rejected

A rejected review:

- marks the reviewed deliverable rejected;
- returns the task to <code>in_progress</code>;
- creates a rework inbox item for the producer;
- preserves concrete findings.

Imani does not edit the rejected deliverable after Rowan finds that jitter can exceed the deadline. Imani produces a new deliverable that explicitly supersedes the rejected one. Open findings are carried forward.

An accepted later review can resolve only findings owned by its same review profile. For legacy reviews without a profile, reviewer-role compatibility is used. One unrelated reviewer cannot silently close another policy's finding.

### Blocked

Use <code>blocked</code> when the review process or evidence access cannot proceed, not when the candidate is known to be defective. For an ordinary deliverable review, blocked state propagates blocking state to the deliverable/task and preserves a concrete blocker.

Release-review blocked behavior is stricter and retry-oriented; Chapter 24 distinguishes it from candidate rejection.

## Multi-review cascades

Suppose Northstar requires both Rowan's software-quality review and Priya's operational-readiness review.

1. Imani submits deliverable D1.
2. The runtime creates R1 for Rowan and R2 for Priya.
3. Rowan accepts R1. D1 stays pending because R2 remains open.
4. Priya rejects R2 with an unresolved deployment-safety finding. D1 becomes rejected and the task returns to rework.
5. Imani submits D2 superseding D1. The open finding is carried to D2.
6. New reviews target D2 and its exact pinned commit.
7. Both profiles accept and the owning profile resolves the carried finding.
8. D2 and the task become accepted.

This cascade prevents “one approval wins,” prevents review of a moving branch tip, and preserves why rework occurred.

![Deliverable review and rework cascade](assets/evander-user-manual/diagrams/14-review-cascade.svg)

<p class="figure-caption">Figure 22.1 — Immutable decisions either advance accepted evidence or create an explicit rework successor.</p>

![Team Delivery view showing the telemetry-retry deliverable and review state](assets/evander-user-manual/screenshots/team-delivery.png)

<p class="figure-caption">Figure 22.2 — Delivery groups deliverables, review evidence, and decisions while keeping their durable identities visible. Sanitized Northstar fixture.</p>

## Review authorization and tool surface

Ordinary Team command tools in this domain are:

- <code>team_submit_deliverable</code>;
- <code>team_submit_deliverable_for_review</code>;
- <code>team_request_review</code>;
- <code>team_decide_review</code>;
- delivery list/get tools;
- Git promotion and release tools covered in Chapter 24.

Admin routes provide revision-checked operator create/edit/decision paths. Workflow providers add deliverable-created triggering and deterministic review-request creation. All paths reuse the same repository invariants.

Review visibility is not review authority. A manager can inspect Team delivery state; only the assigned reviewer records that review's decision. Likewise, release authority does not authorize rewriting a deliverable review.

## Decision and state failures

| Symptom or error | Meaning | Recovery |
|---|---|---|
| Artifact path rejected | The requested path escapes the allowed artifact root or has invalid shape. | Publish from a safe relative path below the session artifact root. |
| Artifact ref exists but bytes are unavailable | Metadata was recorded without publication, or permission/version access failed. | Inspect the artifact-space record and reviewer import error. |
| Git context mismatch | Agent/task/session/dispatch or derived branch does not match the submitted context. | Use the current task work context; never type an alternate branch. |
| Reviewer cannot be assigned | Agent is inactive, cross-Team, or incompatible with the profile. | Select an active same-Team reviewer matching policy. |
| Accepted decision rejected by validation | A required criterion is missing/failed or findings remain open. | Record complete criterion results and resolve only authorized findings. |
| Second decision refused | Review decisions are immutable. | Create the appropriate successor deliverable/review; do not overwrite history. |
| Task stays waiting after one acceptance | Another active review or open carried finding remains. | Inspect the complete current deliverable review set. |
| Rejected task has no useful next step | Findings were vague. | Preserve evidence and create concrete, testable findings in the review decision. |

## Practical runbook

### Producer

1. Confirm the task, active dispatch, and task-specific Git context.
2. Run proportionate verification and record honest status.
3. Publish artifacts from safe paths or attach existing stable refs.
4. Submit one deliverable with a stable request ID.
5. Request the required reviewers/profiles, or use the combined submission command.
6. Mark related inbox items handled atomically.

### Reviewer

1. Confirm the review targets the current deliverable and pinned commit.
2. Inspect imported artifacts and any access failures.
3. Apply every required profile criterion.
4. Record specific findings and decide once.
5. Use rejected for a product defect and blocked for an actual process/evidence blocker.

### Operator

1. In **Delivery**, trace task → current deliverable → all active reviews → findings.
2. Match each review to its profile, reviewer, evidence refs, and reviewed commit.
3. When a cascade looks stuck, inspect open findings before changing task state.
4. Reconcile orphaned published artifacts deliberately.
5. Do not “fix” history by editing terminal review records.

## Source trail

- <code>src/evander_team_addon/models.py</code> — deliverable, verification, review, profile, criterion, finding, and supersession models.
- <code>src/evander_team_addon/services/commands/deliverable_submission_commands.py</code> and <code>review_commands.py</code> — submission, request authority, deduplication, and immutable decisions.
- <code>src/evander_team_addon/services/domain/deliverables.py</code>, <code>review.py</code>, and <code>git_work_contexts.py</code> — acceptance cascades, findings, artifact/Git evidence, and context validation.
- <code>src/evander_team_addon/providers.py</code> — deliverable trigger, review-request effect, artifact import, and wake prompt projection.
- <code>src/evander_team_addon/mcp.py</code> and <code>api.py</code> — grouped delivery tools and operator routes.
- <code>docs/addons.md</code> — artifact publication, review profiles, rework findings, and release handoff.
- <code>tests/test_team_addon_mcp.py</code>, <code>tests/test_team_addon_workflow_primitives.py</code>, and <code>tests/test_team_addon_runtime.py</code> — submission, profile enforcement, duplicate review, cascade, and reviewer-input behavior.


# 23. Ephemeral Delegation

> **At a glance.** A persistent Team agent may delegate a bounded task to a disposable Evander session when its configured ephemeral policy permits it. The child receives one assignment, one narrow reporting tool, a deterministic Git branch, and a lifecycle managed by the Team workflow pack. Completion produces evidence for the parent to accept or reject; it never grants the child general Team authority or automatically accepts the parent task.

## Why ephemeral agents exist

Persistent agents carry organizational identity, reporting relationships, Team memory visibility, inbox, and recurring responsibility. Ephemeral agents are intentionally different. They are short-lived workers for a bounded prompt such as:

- reproduce a failing telemetry retry test;
- inspect one dependency's retry semantics;
- implement a small isolated change on a deterministic branch;
- gather evidence in parallel.

The parent remains responsible for the Team task. Delegation increases execution capacity without multiplying durable organizational actors.

For Northstar, Imani can delegate “add deterministic jitter edge-case tests” while continuing the main retry implementation. The child cannot reassign Imani's task, message Priya, request release, or decide Rowan's review.

## Admission and capacity

The parent persistent agent has an effective ephemeral policy with <code>max_concurrent</code>. A value of zero disables delegation. Active capacity counts assignments in <code>requested</code>, <code>spawn_pending</code>, and <code>running</code>.

A request is valid only when:

- the caller is the resolved persistent parent on an active dispatch;
- the parent has positive capacity and the complete requested batch fits;
- each target task belongs to a project assigned to the parent;
- the parent owns the target task;
- requested model, provider mode, execution backend, image, tools, TTL, directory, and metadata pass schema and policy validation.

Batch requests validate all items before mutation. One invalid child does not leave the other children partially created.

The default execution backend is <code>container_worker_vm</code>. An assignment may carry an explicit compatible model/provider, image, and <code>ttl_hours</code>. These are bounded execution inputs, not general secret or host-path transport.

## The assignment record

An <code>EphemeralAssignment</code> captures:

- parent agent, Team, project, and task;
- title and complete prompt;
- eventual core session ID;
- model/provider/backend/image choices;
- allowed tools and TTL;
- directory and metadata;
- summary, artifacts, optional agent branch, error, and timestamps;
- lifecycle state.

States are <code>requested</code>, <code>spawn_pending</code>, <code>running</code>, <code>completed</code>, <code>accepted</code>, <code>rejected</code>, <code>cleanup_pending</code>, <code>cleaned_up</code>, and <code>failed</code>. They describe execution, decision, and cleanup milestones rather than one mandatory linear chain. In particular, core-session cleanup can finish before the parent accepts a completed result, and the parent may accept a result whose session is already cleaned up. Failures may occur during spawn, execution, result validation, or cleanup; durable records remain useful evidence after compute is gone.

## Asynchronous lifecycle

The parent's <code>team_request_ephemeral_agent</code> or batch command performs only the durable request transaction. It creates the assignment and outbox intent. The registered runtime pack then:

1. consumes <code>team.ephemeral.requested</code>;
2. creates an ordinary core Evander agent/session through <code>effect:agent:create</code>;
3. records the spawned session with <code>effect:team:mark-ephemeral-spawned</code>;
4. prompts the worker through <code>effect:agent:prompt</code>;
5. routes the completed session through <code>query:team:session-routing</code>;
6. records result/completion and notifies the parent;
7. destroys the core agent/session;
8. records cleanup through <code>effect:team:mark-ephemeral-cleaned-up</code>.

The lifecycle composes core execution effects; the add-on does not implement a hidden second container scheduler.

![Ephemeral assignment lifecycle across Team and core execution](assets/evander-user-manual/diagrams/15-ephemeral-lifecycle.svg)

<p class="figure-caption">Figure 23.1 — A durable assignment surrounds disposable execution, parent decision, and cleanup evidence.</p>

## The worker's authority

An ephemeral wake exposes only:

<code>team_report_ephemeral_result</code>

The executor resolves the assignment from the ambient worker session. The child cannot call ordinary persistent Team commands. It receives the prompt and execution resources necessary for the assignment, but not the parent's organizational identity or lease.

A successful report requires a useful summary. It may attach stable artifact references and a Git branch. An error report marks the assignment failed with its evidence.

If the child reports Git output, the branch must be exactly:

<code>agents/&lt;session-id&gt;/&lt;assignment-id&gt;</code>

The server derives and verifies the name. A mismatched branch fails the assignment instead of accepting untraceable code.

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

## Practical runbook

Before delegating:

1. Confirm parent ownership, project assignment, and remaining capacity.
2. Write a bounded prompt with a verifiable output and no implied Team authority.
3. Select backend/model/image only when the defaults are unsuitable.
4. Decide whether the result should be artifact-only or use the deterministic Git branch.
5. Use stable request IDs for the semantic batch.

To diagnose:

1. Record assignment ID, parent, task, session ID, lifecycle state, outbox ID, and workflow run.
2. Follow create → mark spawned → prompt → session routing → report → destroy → cleanup.
3. Inspect the worker's only Team command activity.
4. Verify branch, artifact refs, summary, and error independently.
5. Keep the durable assignment even after fixing leaked compute.

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


# 24. Team Git Promotion and Release Gates

> **At a glance.** Team Git moves reviewed, pinned commits through internal agent, review, feature, and release branches before one lease-protected canonical push. Release packages freeze accepted deliverables, verification evidence, waivers, reviewed change manifests, commands, reviewers, scope, and candidate SHA. Failed candidate verification or rejected release review requires changed reviewed content and a new release; retryable infrastructure or blocked-process failures create immutable successor attempts.

## The repository boundary

A Team project stores only a core <code>managed_repo_id</code>, directory scope, optional base branch, and canonical release prefix. The central managed-Git registry owns the upstream identity and host bare repository. Team commands never accept a trusted host checkout path as repository authority.

All Team branches before canonical publication live in that managed bare repository:

| Stage | Deterministic shape | Purpose |
|---|---|---|
| Ephemeral agent | <code>agents/&lt;session&gt;/&lt;assignment&gt;</code> | Disposable worker output |
| Review | <code>team/&lt;team&gt;/reviews/&lt;source-id&gt;</code> | Captured reviewed source |
| Feature | <code>team/&lt;team&gt;/features/&lt;task&gt;-&lt;agent&gt;</code> | Consolidated accepted task work |
| Internal release | <code>team/&lt;team&gt;/releases/&lt;release&gt;</code> | Consolidated candidate |
| Canonical | default <code>releases/&lt;project&gt;/&lt;release&gt;</code> | The only Team branch pushed upstream |

Branch validation rejects whitespace, control characters, colon, a leading slash, <code>..</code>, and a leading <code>refs/</code>. Operators may configure the canonical prefix, but it remains a validated plain branch name.

## Intent, effect, and evidence

Every promotion is a durable <code>GitPromotionRecord</code>. It records promotion kind, source refs and pinned SHAs, expected commit, target branch, state, output, and error.

The flow has three steps:

1. An authorized MCP/Admin command records promotion intent and durable outbox event.
2. The workflow pack invokes the relevant managed-Git effect.
3. <code>effect:team:record-git-promotion</code> attaches the actual success/failure and updates related Team evidence.

The command's success means intent was recorded. The promotion record and effect output say whether Git succeeded.

Managed-Git effects use operation-scoped worktrees and temporary refs. Target updates are compare-and-swap operations. Conflicts fail; Evander never auto-resolves them. Temporary resources are removed on success and failure.

## The promotion ladder

### Agent or ephemeral output → review

<code>team_publish_review_branch</code> captures a persistent deliverable's validated agent branch/commit or an accepted ephemeral assignment branch into a deterministic review ref. The actor must be the producer/parent, an authorized superior, project lead, or manager as applicable.

### Review → feature

<code>team_update_feature_branch</code> consolidates accepted current deliverables for one task. Each source must match the commit actually accepted by review. The resulting reviewed manifest freezes:

- base and result tree;
- included commits;
- changed paths;
- source deliverable/review evidence.

### Feature → internal release

<code>team_request_release</code> selects accepted deliverables from one project. If needed, prerequisite feature promotions are created first. Release consolidation merges pinned feature commits into the internal release branch and freezes the candidate SHA and tree.

### Internal release → canonical

Only after every gate passes does the workflow create the canonical-push promotion. Core <code>effect:git:push-branch</code> performs expected-commit and destination-lease preflight, then pushes the approved release commit to the canonical branch. <code>effect:team:mark-release-published</code> records canonical branch, published commit, timestamp, and accepted package state.

The internal release branch is not the published branch operators should test or advertise. The release package's <code>canonical_branch</code> and <code>published_commit_sha</code> are the publication truth.

![Pinned Team Git promotion and release-gate flow](assets/evander-user-manual/diagrams/16-team-release.svg)

<p class="figure-caption">Figure 24.1 — Reviewed commits move through internal refs and immutable gates before the sole canonical push.</p>

## Creating a release package

The caller must be:

- the project lead;
- the top-level Team manager; or
- an agent whose same-project role grants <code>request_release</code>.

All selected deliverables must be accepted and belong to one project. Artifact-only releases are allowed without Git configuration. A Git release requires project Git configuration and an explicit release policy unless <code>allow_ungated=true</code> was deliberately configured.

The release package freezes a typed manifest containing:

- deliverable IDs, verification status, evidence refs, and waiver records;
- accepted immutable deliverable reviews;
- reviewed change manifests;
- candidate commit and tree;
- scope-validation result;
- ordered verification commands;
- release-review requirements;
- actor and timestamp evidence.

This prevents policy drift after request time from silently redefining what was approved.

## Verification evidence and waivers

Deliverable verification is enforced at release time:

| Status | Release rule |
|---|---|
| <code>passed</code> | Eligible without a verification waiver. |
| <code>failed</code> | Cannot be waived. Fix, re-review, and submit new evidence. |
| missing, <code>partial</code>, <code>not_run</code>, or <code>unknown</code> | Requires a reasoned waiver from project lead, Team manager, or authenticated operator. |

A role with <code>request_release</code> alone cannot grant the waiver. Each waiver records deliverable, original status, reason, actor kind and ID, and timestamp.

A waiver acknowledges incomplete verification evidence; it does not waive reviewed-scope mismatch.

## Release policy

<code>Project.release_policy</code> selects:

- <code>container_worker_vm</code> verification backend;
- optional container image;
- ordered commands, each with name, working directory, argv, and timeout;
- required reviewer/profile pairs;
- or explicit <code>allow_ungated=true</code>.

Without <code>allow_ungated</code>, at least one command or review requirement is required.

The verifier creates a disposable worker session, clones by <code>managed_repo_id</code>, checks out the exact candidate SHA, runs commands in order, records bounded results, and destroys the session. Release reviewers decide that same candidate SHA under their profiles.

## Scope validation

Feature promotion records the exact reviewed commits and changed paths. Release consolidation computes the candidate's changes and compares them with the union of reviewed manifests.

An extra commit or path outside that union is a hard blocker. It cannot be waived. This catches accidental merge content and deliberate attempts to smuggle unreviewed scope into the candidate.

For Northstar, if the reviewed telemetry change covers <code>src/telemetry/retry.py</code> and its tests but the release candidate also changes an authentication policy file, the release stops even if all tests pass.

## Gate outcomes and retries

Candidate identity never changes within one release package.

### Terminal candidate failures

A verification command returning nonzero means the candidate failed. The release is rejected. A release-review decision of <code>rejected</code> also means the candidate is defective. Repair the content, create and accept new deliverables, and request a new release with a new reviewed tree.

The system refuses a cosmetic new release of the same failed deliverables and tree.

### Retryable process failures

Verification timeout or infrastructure failure can be retryable. A release review recorded <code>blocked</code> with a concrete process/evidence blocker can also be retried. <code>team_retry_release_gate</code> creates a linked immutable successor attempt against the same candidate SHA with reason and requester identity. It never edits the old run or review.

Use <code>blocked</code> only when the review process cannot complete. Use <code>rejected</code> for an actual candidate defect.

### Git recovery

<code>team_recover_release</code> is different. It recovers failed prerequisite feature or internal release Git promotions. It is not a verification or review-gate retry mechanism.

Legacy nonterminal packages without current immutable candidate evidence are rejected with <code>legacy_unpinned_release</code> and must be recreated. Historical terminal records remain readable.

## Northstar release trace

1. Rowan accepts Imani's current telemetry deliverable and pinned commit.
2. Priya promotes its review evidence into the task feature branch.
3. Riley requests release <code>telemetry-retry-2026-07</code>.
4. The package freezes deliverables, reviews, reviewed paths, commands, required release reviewers, candidate SHA, and tree.
5. A disposable verification worker checks out that SHA and runs unit and integration commands.
6. Scope validation confirms no unreviewed paths entered consolidation.
7. Required release reviews accept the same SHA.
8. Canonical push publishes it to the configured <code>releases/mission-control/...</code> branch under a destination lease.
9. The accepted release package records the canonical branch and published SHA.

If integration verification times out, Riley may request an audited retry against the same SHA. If it exits nonzero, Imani must change and re-review the content; Riley cannot retry it into approval.

## Failure patterns

| Failure | Correct interpretation |
|---|---|
| Source branch moved after review | Promotion uses the pinned reviewed commit if it remains contained; a moving tip is not silently substituted. |
| Merge conflict | Deterministic consolidation failed; Evander does not auto-resolve. Create reviewed conflict-resolution content. |
| Compare-and-swap target mismatch | Another operation advanced the target. Re-read promotion truth and decide whether a new intent is valid. |
| Missing release policy | Git publication is blocked unless explicitly ungated. Configure policy; do not bypass the gate. |
| Unreviewed changed path | Hard scope failure, never waiver-eligible. Rebuild from reviewed content. |
| Verification command nonzero | Terminal candidate defect. New content and release required. |
| Verification infrastructure error | Potentially retryable immutable successor run. |
| Release review rejected | Terminal candidate defect. |
| Release review blocked | Retryable only with a concrete process/evidence blocker. |
| Release package says accepted but upstream seems different | Compare canonical branch/published SHA, promotion record, core push effect, and remote ref. Do not inspect only the internal release branch. |

## Practical runbook

Before release:

1. Confirm every selected deliverable is current, accepted, and in one project.
2. Verify each Git source resolves to the pinned reviewed commit.
3. Inspect reviewed manifests and expected path union.
4. Resolve incomplete verification with an authorized, reasoned waiver or new evidence.
5. Validate the project release policy and worker image/commands/reviewers.

During release:

1. Track release package, prerequisite promotions, candidate SHA/tree, verification run, release reviews, and scope validation.
2. Treat outbox acceptance and effect completion as separate facts.
3. Retry only typed retryable failures through <code>team_retry_release_gate</code>.
4. Use <code>team_recover_release</code> only for eligible Git promotion failure.

After release:

1. Read <code>canonical_branch</code>, <code>published_commit_sha</code>, and <code>published_at</code> from the package.
2. Match them to the successful canonical-push promotion and core Git effect.
3. Verify the remote ref equals the approved SHA.
4. Preserve the immutable manifest and all gate attempts as release evidence.

## Source trail

- <code>src/evander_team_addon/git_naming.py</code> — deterministic internal and canonical branch names and validation.
- <code>src/evander_team_addon/services/commands/git_release.py</code> and <code>services/git_release_service.py</code> — promotion/release authority, intent, recovery, and state transitions.
- <code>src/evander_team_addon/services/domain/git_promotion.py</code>, <code>release_packages.py</code>, and <code>release_gates.py</code> — pinned sources, manifests, scope, verification, reviews, and retries.
- <code>src/evander_team_addon/providers.py</code> — publish, consolidate, verify, record, and mark-published effects.
- <code>src/evander_team_addon/workflow_packs.py</code> — review, feature, release, verification, and canonical-push routes.
- <code>src/evander/admin/services/managed_git_service.py</code> and core Git effects — managed bare repositories, operation worktrees, leases, compare-and-swap, and remote push.
- <code>docs/addons.md</code> and <code>docs/git-remotes.md</code> — Team release and core managed-Git contracts.
- <code>tests/test_team_addon_git_effects.py</code>, <code>tests/test_team_addon_mcp.py</code>, and <code>tests/test_team_addon_runtime.py</code> — promotion, scope, gates, retry, recovery, and publication behavior.


# 25. Governance, Memory, Cost, and Internals

> **At a glance.** Governance provides cross-Team attention without erasing Team ownership. Durable memory preserves scoped organizational knowledge while Qdrant remains a rebuildable index. Cost totals are estimated operational guards with explicit coverage, not invoices. Under all three features, relational SQLite remains canonical; derived views, workflow events, semantic vectors, and session mirrors must be diagnosed through their owning boundaries.

## Governance across Teams

A <code>GovernanceGroup</code> has:

- one persistent governance manager;
- a set of member Team IDs;
- goals and optional estimated cost cap;
- a durable agenda;
- runtime lease, wake timing, and change fingerprints.

The governance manager is an oversight role, not the manager of every member Team. The manager receives summaries of member Teams, projects, Team managers, open/blocked work, cost, and pending agenda. It can create an operator directive for a member Team's manager and mark its own agenda handled.

Current agenda types include:

- <code>operator_directive</code>;
- <code>team_escalation</code>;
- <code>cost_cap_warning</code> and <code>cost_cap_reached</code>;
- <code>stale_team</code> and <code>blocked_team</code>;
- <code>scheduled_checkin</code>;
- <code>policy_decision</code>.

Adding or removing group membership creates a pending <code>policy_decision</code> agenda item containing previous, added, removed, and current Team IDs. It also makes governance evaluation due, so the next prompt contains the new Team summaries.

The runtime leases a governance manager when agenda is pending and due or when member-Team cost/blocked fingerprints materially changed. Governance commands require the correct managed group and lease. The tool surface is deliberately small: create a directive and mark an agenda item handled, plus appropriate reads.

Membership proves that a Team is an authorized governance target. It does not prove that the governance manager owns a product decision. For Northstar, a governance manager may direct Mira to assess a telemetry-policy conflict, but should not assign Imani's implementation details directly or declare Rowan's review accepted.

## Escalation into governance

When <code>northstar-platform</code> has <code>escalate_to_governance=true</code>, Mira's escalation creates agenda in each group containing Northstar. The manager sees the Team and project summaries, decides what action is justified, creates a directive to Mira if needed, and handles the agenda.

This creates an auditable loop:

<code>Team escalation → governance agenda → governance lease/decision → Team-manager directive → Team work → handled states</code>

Do not mark an agenda handled merely to silence a wake before routing the necessary directive or recording the decision.

![Governance escalation and directive communication loop](assets/evander-user-manual/diagrams/17-governance-communication.svg)

<p class="figure-caption">Figure 25.1 — Governance routes decisions through Team managers instead of bypassing ownership.</p>

## Durable Team memory

Team memory is for compact knowledge that should survive dispatches and model contexts:

- <code>decision</code>;
- <code>preference</code>;
- <code>fact</code>;
- <code>runbook</code>;
- <code>postmortem</code>;
- <code>research</code>;
- <code>handoff</code>;
- <code>other</code>.

Scopes are <code>team_global</code>, <code>project</code>, <code>agent_private</code>, and <code>governance</code>. Records can be active, superseded, or archived. They contain title, summary, tags, refs, optional body, ownership/scope, and timestamps.

Broad state and search payloads omit the potentially long body. Explicit detail reads return it. Store bulky logs, datasets, and test evidence as immutable artifacts, then link their refs from the memory record.

Normal wake prompts do not inject all memory. Agents call <code>team_search_memory</code> when recall is useful. This prevents a growing knowledge base from consuming every dispatch context.

Memory commands include <code>team_record_memory</code> and <code>team_archive_memory</code>. Archiving or superseding preserves history; it does not mutate the old statement into a new one. These actions are classified as context activity rather than substantive task progress, so repeatedly writing notes does not defeat no-progress protection.

For Northstar, useful records include:

- decision: “Telemetry retries must preserve the original idempotency key.”
- runbook: “Diagnose retry exhaustion using attempt count, terminal reason, and upstream correlation.”
- handoff: “D2 supersedes D1 after jitter deadline correction; Rowan review R4 is current.”

## Semantic indexing

Canonical memory records live in Team SQLite. Qdrant stores a derived per-Team semantic collection with a generated name based on Team ID. Semantic results are joined back to canonical records and filtered by current visibility.

Visibility protects:

- agent-private records for the owning agent;
- project records for agents with visible project scope;
- governance records for the appropriate governance context;
- broader subordinate/project context available to authorized managers.

The exact visibility evaluator, not possession of a Qdrant point ID, determines access.

Workflow providers are:

- <code>query:team:memory</code>;
- <code>effect:team:index-memory</code>;
- <code>effect:team:reindex-memory</code>.

If Qdrant is unavailable or corrupt, canonical memory remains. Repair connectivity and reindex rather than treating the vector collection as the backup.

## Cost as an operational guard

A Team may have <code>max_cost_usd</code>; a governance group may also have a cap. Cost aggregation reads recorded model usage from <code>runtime/model-usage.db</code>, applies the runtime pricing catalog, and may backfill supported Claude transcript usage. It deduplicates records and attributes them using request, dispatch, session, Team/project, and ephemeral-assignment metadata.

The result separates Team, project, and unattributed amounts and includes:

- estimated cost;
- priced and unpriced usage;
- <code>coverage_status</code>;
- <code>coverage_reasons</code>.

These totals are an estimated lifetime operational safety rail, not billing-grade truth. Missing prices, incomplete attribution, transcript gaps, or ingestion delay can produce partial coverage.

When a Team reaches its cap, future Team wakes are blocked, including queued Chat. Existing external provider spend may already have occurred, and unrelated core workloads are not retroactively canceled. Raising or clearing the cap re-enables evaluation; do so only after understanding coverage.

Governance can emit a warning before the group or Team reaches its cap, using the configured warning ratio, and an agenda item when it reaches the cap. A warning is not proof of exact invoiced spend.

## SQLite internals

<code>state.db</code> contains normalized relational tables for Teams, roles, agents, assignments, runtimes, projects, tasks, cases, deliverables, reviews, inbox, conversations, memory, governance, releases, promotions, ephemeral assignments, and related evidence.

Three support ledgers are particularly important:

- <code>team_dispatch_activity</code>;
- <code>team_idempotency_records</code>;
- <code>team_outbox</code>.

Writes use <code>BEGIN IMMEDIATE</code> and full <code>TeamState</code> validation before commit. WAL and busy timeout improve concurrent service behavior but do not make direct multi-writer SQL safe.

The broad domain model is current-state storage. Dispatch activity, idempotency, and outbox records provide more durable execution evidence, but the UI Timeline itself is a derived rendering of current records. It cannot reconstruct every overwritten intermediate field value.

The one-time legacy <code>state.json</code> import is recorded in database metadata with path, timestamp, and digest. Once SQLite exists, changing the JSON does nothing.

![Team UI, canonical state, and derived data flow](assets/evander-user-manual/diagrams/18-team-ui-data-flow.svg)

<p class="figure-caption">Figure 25.2 — Scoped UI reads refresh from revision-only invalidations while optimistic writes remain explicit.</p>

## Workflow and provider internals

The add-on registers its providers only while enabled. The main grouped contract is:

| Kind | Current Team providers |
|---|---|
| Trigger | <code>trigger:team:deliverable-created</code> |
| Query | <code>query:team:state</code>, <code>query:team:memory</code>, <code>query:team:session-routing</code> |
| Runtime effects | wake due agents, wake governance managers, publish outbox, finalize dispatch |
| Domain effects | create/update cases and tasks, create directives/reviews, ephemeral lifecycle |
| Git/release effects | publish review branch, consolidate, record promotion, verify candidate, mark published |
| Memory effects | index and reindex |

The registered <code>team-runtime.v1</code> pack composes these with core agent, communication, Git, and event effects. Internal event routes include Chat queued, agent wake requested, ephemeral requested/completed, operator escalation, Git promotion/recovery, and release verification.

The packaged Python definition is a source definition. Enablement materializes an operator-owned live workflow document under the normal revision and lock contract. Refresh it through the Add-ons surface. Never directly rewrite a live workflow-pack file, even during incident repair.

## Diagnosis by source of truth

Start with the observed symptom, then cross the boundary deliberately:

| Symptom | First authoritative sources |
|---|---|
| Wrong Team ownership/state | <code>state.db</code> current schema and revision-checked Team API |
| Agent did or did not wake | Team Operations/runtime payload, runtime row, live pack parameters, workflow run, core session state |
| Async action missing | Team outbox row, internal event, workflow run, effect result |
| Git release discrepancy | release package, promotion record, managed bare ref, canonical push effect, remote ref |
| Memory search omission | canonical memory visibility, Qdrant health/index generation, query result |
| Cost discrepancy | model-usage DB, attribution metadata, pricing catalog, coverage reasons |
| Voice discrepancy | core voice-session store and optional Team Chat projection |

Avoid broad guesses from UI appearance alone.

## Backup and recovery

A useful backup set includes:

1. a SQLite-consistent backup of <code>runtime/addons/team-addon/state.db</code>;
2. Team avatar files;
3. referenced artifact data and artifact-space metadata;
4. core managed repositories and registry;
5. live workflow configuration and its revision history;
6. core voice-session and model-usage stores;
7. Qdrant only as a convenience snapshot, because it can be rebuilt.

Either stop Team writers before a filesystem copy or use SQLite's supported backup mechanism. Copying only the main DB file while WAL writes are active can yield an inconsistent backup.

Recovery order:

1. restore canonical Team SQLite and validate it with the current code/schema;
2. restore referenced core stores;
3. start services and verify add-on/provider registration;
4. inspect pending/claimed outbox and stale leases before resuming runtime;
5. rebuild semantic memory index;
6. reconcile managed Git refs, artifacts, voice records, and cost coverage.

Do not replay legacy JSON over restored relational state.

## Northstar operational review

At the end of the telemetry retry release, an operator should be able to answer:

- Which governance decisions affected Northstar and who handled them?
- Which current memory records explain the retry policy and supersede old guidance?
- What proportion of Northstar usage was priced and attributed?
- Which command activity and outbox rows led to the release?
- Which canonical branch and SHA were published?

If the answer depends only on a Chat transcript or Timeline rendering, the durable operational record is incomplete.

## Failure patterns

| Symptom | Correct response |
|---|---|
| Governance manager targets a Team outside the group | Membership authorization fails; update membership through revision-checked policy only if genuinely intended. |
| Same agenda wakes repeatedly | Inspect pending/handled state, lease finalization, cost/blocked fingerprint change, and whether a directive was actually routed. |
| Memory exists but semantic search misses it | Confirm visibility and canonical status, then inspect/rebuild the Team collection. |
| Search exposes a private record | Treat as an authorization defect; preserve query context and inspect canonical visibility rejoin immediately. |
| Team cap blocks Chat unexpectedly | Chat is a Team wake and is intentionally guarded; inspect estimated total and coverage. |
| Cost total differs from invoice | Expected possibility; reconcile usage ingestion, pricing, attribution, and unpriced events. |
| SQLite is locked | Identify the legitimate writer and long transaction; do not disable serialized writes or hand-edit WAL files. |
| Live runtime behavior differs from packaged definition | The operator-owned pack may be customized or stale; inspect its current revision and history, then refresh/merge through the supported API. |

## Practical runbook

### Governance

1. Confirm group manager, membership, goals, cap, and current runtime lease.
2. Read agenda and member-Team summaries.
3. Route only justified directives to member Team managers.
4. Mark agenda handled after the decision/action is recorded.

### Memory

1. Search canonical metadata by Team/project/scope before blaming semantic retrieval.
2. Read detail for long body content.
3. Supersede or archive; never overwrite the meaning of an old decision.
4. Reindex Qdrant from canonical records and retest visibility by representative identities.

### Cost

1. Read totals together with coverage status/reasons and unpriced usage.
2. Trace attribution to session, dispatch, project, and ephemeral metadata.
3. Compare the active pricing catalog with event timestamps/models.
4. Change caps only with explicit operational intent.

### Internals

1. Take a consistent snapshot before deep inspection.
2. Inspect current schema, Team revision, support ledgers, and live workflow pack.
3. Follow stable IDs across Team, workflow, session, Git, artifact, and usage stores.
4. Repair through supported services and verify the resulting revision/readback.

## Source trail

- <code>src/evander_team_addon/models.py</code> — governance, memory, cost-cap, runtime, activity, idempotency, and outbox records.
- <code>src/evander_team_addon/services/commands/governance_commands.py</code> and <code>services/domain/governance.py</code> — group authorization, directives, agenda, and membership decisions.
- <code>src/evander_team_addon/services/memory_service.py</code> and <code>services/commands/memory_commands.py</code> — canonical memory, visibility, semantic retrieval, and lifecycle.
- <code>src/evander_team_addon/costs.py</code> and <code>src/evander/model_usage/</code> — usage attribution, pricing, coverage, and cap payloads.
- <code>src/evander_team_addon/sqlite_store.py</code> and <code>repository.py</code> — relational schema, WAL, transactions, migration, and support ledgers.
- <code>src/evander_team_addon/providers.py</code>, <code>provider_schemas.py</code>, and <code>workflow_packs.py</code> — grouped node contracts, governance runtime, indexing, and live-pack definition.
- <code>docs/addons.md</code> — governance, memory, cost, runtime, and enablement contracts.
- <code>tests/test_team_addon_costs.py</code>, <code>tests/test_team_addon_models.py</code>, <code>tests/test_team_addon_mcp.py</code>, and <code>tests/test_team_addon_runtime.py</code> — visibility, cap, agenda, transactions, and provider behavior.


<div class="page-break"></div>

# Part IV: Engineering and Evolution

Explain how to extend and verify Evander, then separate the historical decisions from the current contract.

# 26. Engineering and Extension Guide

> **At a glance.** Extend Evander at the narrowest stable seam. Prefer configuration for policy, a workflow pack for orchestration, a provider plugin for a reusable node, an add-on for a bounded product surface, and core changes only for control-plane invariants. Every extension must preserve directory scope, host-owned secrets, typed contracts, idempotency, auditability, and restart/migration behavior.

## Choose the right seam

| Requirement | Preferred seam |
|---|---|
| Change an endpoint, model, allowlist, threshold, or backend selection | Directory/host configuration |
| Compose existing triggers, gates, queries, and effects | Workflow pack |
| Add a reusable typed workflow node | Provider plugin |
| Add a cohesive UI/API/MCP/workflow feature with private state | Add-on |
| Add a new isolated execution mechanism | `ExecutionBackend` implementation plus deployment bundle |
| Change policy, lifecycle, identity, or durable core semantics | Focused core service/repository change |

Northstar Platform's telemetry-retry process, for example, needs no new core scheduler: a communication or issue trigger, existing Git/session effects, and Team workflow primitives are enough. A genuinely new telemetry SaaS might warrant a provider plugin; a full telemetry-management product with its own records and UI might warrant an add-on.

## Architectural rules contributors must preserve

1. **Host paths and secrets stay private.** Public contracts use managed IDs, relative artifact paths, target IDs, and environment-variable names.
2. **Code moves with Git.** Do not add MCP payload transport for repositories.
3. **Directory ownership is part of identity.** Resolve effective config and authorize against the owning scope.
4. **Writes are explicit and replay-aware.** Define an idempotency key or compare-and-swap/lease boundary for every external mutation.
5. **Schemas reject ambiguity.** Typed models, forbidden extras where appropriate, stable normalized outputs, and explicit provider-only fields make failures reviewable.
6. **Durable truth has one owner.** Derived caches and indexes must be rebuildable or visibly stale.
7. **Cleanup is part of success.** Temporary credentials, refs, worktrees, containers, leases, and pending records need terminal cleanup and startup reconciliation.

## Extending the Admin runtime

`src/evander/admin/composition.py::build_admin_runtime` and `AdminRuntimeComponents` are the supported assembly seam. Domain behavior belongs in a focused service under `src/evander/admin/services/`; web routes should validate HTTP/auth concerns and delegate. CLI and gateway adapters should use the same service rather than reimplementing business rules.

A new admin domain normally needs:

- a dependency dataclass/port for the narrow collaborators it uses;
- a service with plain, testable methods;
- construction and exposure in `AdminRuntimeComponents`;
- thin route and payload models;
- RBAC/privileged-unlock/revision behavior for mutations;
- focused service tests and route authorization tests.

Do not recreate the removed monolithic `AdminService` facade or rely on another service's private attributes. Compatibility adapters, when unavoidable, should be thin and scheduled for removal.

## Adding a workflow node

Workflow nodes are divided by semantics:

- **trigger** discovers or accepts an event;
- **gate** makes a deterministic routing/state decision;
- **query** reads without external mutation;
- **effect** performs a mutation or long-running action.

Putting a write in a query or a provider poll in an effect makes retry behavior impossible to reason about.

### Complete node contract

A production node includes all of the following:

1. A namespaced node type such as `query:telemetry:deployment`.
2. A typed definition model and resolver from `WorkflowNodeEnvelope`.
3. A provider subclass of the appropriate base with declared node capability.
4. A stable output schema registered for expression binding.
5. Runtime-context use for directory config, secret resolution, services, and transport—never ambient guest state.
6. A workflow-editor catalog entry with useful labels, field schemas, defaults, and output documentation.
7. An internal event catalog entry only if the node introduces a stable event type.
8. Registration in built-ins or a `ProviderPluginRegistration`.
9. Validation, materialization, provider, registry, expression-binding, failure, and replay tests.
10. Documentation, configuration reference, and `CHANGELOG.md` entry.

`ProviderPluginRegistration` can contribute trigger, gate, effect, and query mappings; definition resolvers; editor entries; and internal event entries. The loader rejects duplicate catalog entries and catalog entries that do not match a provider of the declared kind. Module and `evander.providers` entry-point plugins are assembled at startup.

### Effect design checklist

Before implementing an effect, write down:

- canonical input and normalized output;
- external idempotency key and what a retry observes;
- timeout/cancellation behavior;
- which state is persisted before and after the call;
- how an uncertain provider acceptance is reconciled;
- policy/approval and audit fields;
- secret redaction;
- terminal and abandoned-work cleanup.

Northstar Platform's “publish telemetry branch” effect should carry `managed_repo_id`, exact approved SHA, destination ref, and lease—not a host path and mutable branch tip.

## Building an add-on

An add-on package exposes `register_addon()` returning `AddonRegistration` or a compatible mapping. A typical package contains:

```text
my_addon/
├── addon.json
├── __init__.py
├── api.py
├── service.py
├── repository.py
├── workflow.py
└── ui_static/
```

Use `AddonContext.addon_runtime_dir` or `addon_storage_path`; never construct traversal-prone paths from user input. Keep add-on state private and references to core entities explicit. If the add-on needs core behavior, depend on a supported service/port rather than opening core databases itself.

Admin route factories register under `/api/addons/<id>/...`; UI under `/addons/<id>/...`; MCP tools register through the gateway hook; workflow providers use the same provider-plugin contract. Enablement is revision-checked and restart-gated. Document exactly which processes must restart.

Add-on migrations must be transactional and idempotent. A legacy import should be one-way, marker-gated, and leave enough provenance to explain what happened. Discovery may report a broken disabled package without loading it; an explicitly configured or enabled add-on should fail assembly clearly rather than leave half of its routes/providers registered.

## Adding an execution backend

Implement the complete `ExecutionBackend` protocol, not only “start”:

- create/start/stop/destroy;
- status, list, and logs;
- command execution and file transfer where the contract requires them;
- cancellation and terminal cleanup;
- stable mapping from session/execution ID to backend resource;
- normalized errors and bounded timeouts.

Register the backend in `ExecutionBackendRegistry`, expose explicit configuration, and ensure generic workflow effects continue to address `execution_backend` rather than backend-specific implementation names.

Threat-model the backend independently. The container worker, for example, needs authenticated control-plane calls, worker identity binding, no general egress, private runtime state, relay endpoints, restricted Git keys, package caches, resource limits, and startup reconciliation. A smoke test is not a substitute for lifecycle and isolation tests.

## Adding a host relay or service

A new host service touches more than its Python module. Update:

- a `pyproject.toml` entry point;
- config model/example and secret-variable documentation;
- systemd template and `scripts/install_systemd_units.sh` rendering/preflight;
- firewall defaults only for ports guests or workers must reach;
- bootstrap/session configuration if guests consume it;
- runtime assembly and health/status reporting;
- `README.md`, `docs/operations.md`, and `docs/configuration-reference.md`;
- focused service, install-script, firewall, and documentation tests;
- `CHANGELOG.md`.

Bind management-only services to loopback. If a guest-reachable port is needed, authenticate it with session-scoped capability and enforce the same policy server-side. Forward streaming response lifetime correctly and redact credentials from logs.

## Storage, revisions, and migrations

Choose storage based on concurrency and query needs. JSON repositories are suitable for small, low-contention records when atomic replacement/locking is explicit. SQLite is preferred for concurrent claims, leases, histories, and relational invariants. Enable foreign keys and define busy timeout/transaction boundaries consciously.

For operator-edited documents, expose a deterministic revision and require `expected_revision`. For workflow packs, use the existing editor lock/revision path. For remote publication, use provider leases or compare-and-swap. These are the same concurrency principle at different layers.

A migration plan must specify:

- old and new authority;
- whether migration is breaking or dual-read;
- exact preflight and blocking conditions;
- idempotent apply and dry-run behavior;
- rollback limit;
- active-worker/session handling;
- verification and cleanup of legacy state.

Avoid indefinite dual authority. The managed Git migration deliberately replaced path-based checkouts with IDs and bare repositories rather than teaching every future feature both models.

## Testing and verification

Run focused tests while developing, then the Python suite:

```bash
./.venv/bin/pytest -q tests/test_workflow_plugins.py tests/test_workflow_validation.py
./.venv/bin/pytest -q
```

For a new node, include registry and runtime-contract tests plus provider-specific failure/retry tests. For a service, include install/preflight and docs assertions. For UI/browser tests, apply explicit command timeouts; stop a hung process and report its last useful output rather than leaving it running.

Before committing in a dirty tree:

```bash
git status --short
git diff --check
git diff --cached --name-only
```

Preserve unrelated user changes. Record every feature and bug fix in `CHANGELOG.md`.

## Extension failure runbook

1. **Confirm loading.** Check the provider/add-on catalog, process restart, and duplicate-registration errors.
2. **Validate the schema.** Materialize the smallest node/config payload and read the exact diagnostic before debugging transport.
3. **Inspect effective scope.** Verify directory config, secret environment name, target/repository ownership, and policy.
4. **Trace one ID.** Follow one trigger/run/effect/dispatch or one API request through its service and repository.
5. **Test replay.** Execute the same idempotency key twice and simulate a timeout after provider acceptance.
6. **Test cleanup.** Cancel and crash at each resource-acquisition boundary; run startup reconciliation.
7. **Check compatibility.** Run migration dry-run and verify old data cannot silently become a second authority.
8. **Verify documentation surfaces.** Search for stale command paths, service names, config fields, and generated UI pointers.

## Source trail

- `src/evander/admin/composition.py`
- `src/evander/admin/services/`
- `src/evander/workflow/plugins.py`
- `src/evander/workflow/catalog.py`
- `src/evander/workflow/contracts.py`
- `src/evander/workflow/validation.py`
- `src/evander/triggers/providers/base.py`
- `src/evander/gates/providers/base.py`
- `src/evander/queries/providers/base.py`
- `src/evander/effects/providers/base.py`
- `src/evander/providers/builtin.py`
- `src/evander/addons.py`
- `src/evander/execution/backend.py`
- `src/evander/execution/registry.py`
- `docs/addons.md`
- `docs/execution-backends.md`
- `tests/test_workflow_plugins.py`
- `tests/test_provider_runtime_contracts.py`
- `tests/test_workflow_validation.py`
- `tests/test_addons.py`
- `tests/test_execution_registry.py`
- `tests/test_install_systemd_units_script.py`
- `tests/test_firewall_defaults.py`


# 27. Design History and Compatibility Boundaries

> **At a glance.** This chapter is non-normative. It explains why current Evander has its present boundaries and identifies a few deliberate upgrade shims. For current commands and behavior, use the preceding chapters and the current source trail. A historical name, worker, file, or flow appearing here is not evidence that it remains supported.

## How to read this history

Evander grew from a small host gateway around disposable VMs into a directory-scoped control plane with provider-neutral workflows, a second execution backend, packaged add-ons, and managed Git. Most architectural changes followed the same pressure: replace an implicit shared resource with an explicit identity and owner.

| Earlier pressure | Current design response |
|---|---|
| One host path represented “the repo” | `managed_repo_id`, bare object store, controlled ref namespaces |
| One periodic loop owned unrelated work | dedicated trigger/effect, ingest, wake, proxy, and PR workers |
| One large Admin facade mediated every domain | explicit composition graph and focused services |
| JSON files coordinated concurrent leases/queues | selective SQLite stores and transactional claims |
| Team actions arrived as guest artifacts | Team MCP commands, idempotency, private SQLite state, outbox |
| A VM was the execution model | provider-neutral session plus `ExecutionBackend` |

![Evander's architecture evolves from a VM gateway through directory ownership and typed workflows to multiple execution backends, add-ons, focused services, and managed Git.](assets/evander-user-manual/diagrams/19-design-evolution.svg)

<p class="figure-caption">Figure 27.1 — Each major redesign made an implicit shared resource into an explicit, scoped identity or owner.</p>

## Timeline of the major seams

### March 20, 2026: the secure host boundary

The initial platform established the enduring security shape: host-side policy and audit, MCP gateway, OpenAI relay, firewall/service templates, disposable libvirt VMs, cloud-init, and Git-based code transport. Discord and NuGet support quickly exercised the same rule—providers and secrets live on the host; guests receive narrow endpoints.

This remains the foundation. Later abstractions did not turn guests into trusted hosts or add general internet access.

### March 21–30: work, polling, and directory ownership

Issue VM automation, provider email, ADO polling, and PR feedback made it clear that “one global configuration” was insufficient. Directory metadata, inherited configuration overlays, scoped issue identities, approvals, local/remote issue distinction, and Teams/Discord ingest followed.

The host heartbeat of this period was an intermediate consolidation: it moved periodic responsibility out of guests. It is no longer a current service. Today ADO PR feedback/completion, Discord wake, scheduled wake, provider ingest, and workflow polling have dedicated owners. Optional guest heartbeat assets remain disabled by default.

### April 3–20: workflows become typed programs

Trigger and effect models, registries, durable event queues, gates, queries, asynchronous completion, and the visual editor turned ad hoc automation into typed workflow graphs. Pack architecture, provider-owned schemas, runtime contexts, plugin loading, revisions, and live reload later separated stable orchestration semantics from provider implementation.

This history explains two current rules: node kinds must reflect read/write semantics, and editor catalog/schema work is part of adding a provider—not optional UI polish.

### April 21–30: repository and execution abstractions

Repository interfaces replaced broad file access, VM provider identity was separated from session semantics, and issue VM creation was split from repository setup. The `ExecutionBackend` abstraction then added `container_worker_vm` without removing `libvirt_vm`.

Container workers sharpened boundaries that VMs had previously made implicit: authenticated worker API, private session homes, relay and package-cache networking, explicit resource profiles, lifecycle reconciliation, and backend-neutral Admin/workflow operations.

### May: concurrent state moves selectively to SQLite

Teams/Discord inboxes, scheduled wakes, worker jobs, and orchestration adopted SQLite where concurrent claims, paging, WAL, or transactional history required it. Tool-call audit kept JSONL as canonical evidence and added SQLite only as a derived search index.

This was not a blanket “JSON is obsolete” migration. Small operator documents and registries still use atomic, locked JSON; queues and relational state use SQLite; append-only evidence uses JSONL. The owner repository determines the format.

### June 17–25: Team becomes an add-on with command semantics

Team Add-on moved its canonical state from `state.json` to relational `state.db` with a one-time import. Roles, leases, task/review/release records, artifact references, and activity required transactional invariants.

The clean MCP cutover then retired action-artifact providers. Agents report Team mutations through Team-owned MCP tools bound to dispatch permissions and idempotency. Asynchronous intent goes through the Team outbox and workflow events. Artifacts remain for deliverable files, not as a covert command bus.

### June 26–30: Evander identity and focused Admin composition

Operational naming moved to Evander: Python package, `evander-*` services, `EVANDER_*` variables, and guest `.evander`/`/etc/evander` paths. Installer/setup code retained narrow upgrade handling for earlier service, firewall, network, environment, and guest paths.

In parallel, Admin domains were extracted behind ports and focused services. The short-lived compatibility guidance around `AdminService` was superseded on June 30: the facade was removed, and Admin Web, gateway, CLI, providers, and tests now assemble through `build_admin_runtime` and `AdminRuntimeComponents`.

### July 2 onward: add-on workflow composition and Git work identity

Add-ons gained runtime workflow packs and Team gained server-owned Git work contexts. This made add-on enablement a runtime-registry concern: a pack cannot remain active when its providers will not load, and enablement requires the affected processes to restart.

### July 13: managed bare repositories and bounded consultations

Managed Git replaced issue-specific mirrors and persistent checkout routing with a deterministic ID, one directory-owned bare repository per canonical upstream, disjoint ref ownership, exact-SHA pinning, compare-and-swap promotion, lease-protected publication, and ephemeral hardened worktrees.

This was a breaking migration, not an indefinite dual read. The same commit-identity machinery enabled code consultations: a disposable read-only worker answers one bounded question against one pinned commit, then is cleaned up.

## Current compatibility surfaces

Compatibility is intentionally narrow. Treat these as migration bridges, not design examples.

| Surface | Current status | New work should use |
|---|---|---|
| Legacy gateway YAML | Import/migration input only | `runtime/host-config.json` plus directory config |
| Older guest session/config paths | Read/repair fallback in selected bootstrap paths | `/etc/evander/session.env`, `/home/agent/.evander`, provider-neutral guest config |
| `runtime/pr-map.json` | Compatibility association for older PR sessions | explicit PR workflow streams and managed repository identity |
| JSON wake/inbox/worker/Team stores | One-time import where the owning repository supports it | the current SQLite store |
| ADO-specific workflow node types | Supported compatibility surfaces | provider-neutral remote issue/PR nodes where semantics match |
| Old service/firewall/network names | Installer/setup cleanup only | `evander-*` services and current network/config names |
| Checked-in workflow pack examples | Examples/built-ins, not assumed live authority | revision-checked live packs through Admin Web/API |

An import is not dual authority. Once a SQLite repository has initialized and recorded its migration, editing the legacy JSON should not change live state. Keep the legacy file only as documented rollback evidence until finalization says it may be archived.

## Retired designs: do not revive them

- There is no current `evander-host-heartbeat` service. Use the dedicated worker that owns the event.
- Do not write Team commands as action artifacts. Use Team MCP commands and the outbox/runtime workflow.
- Do not address repositories by trusted-checkout or mirror paths. Register and pass `managed_repo_id`.
- Do not run agent-authored code in managed Git operation worktrees.
- Do not add new calls through `AdminService`; it no longer exists.
- Do not write live workflow pack files from a stale copy. Use lock/revision APIs.
- Do not make the gateway or MCP carry repository contents.
- Do not treat `audit.db`, semantic vectors, proxy caches, or UI payloads as canonical state.

## Why compatibility sometimes fails closed

Upgrade shims stop when continuing would hide ambiguity. Examples include:

- an issue number matches several directory/provider records;
- an old repository path cannot be mapped uniquely to a managed repository;
- active workers or Team leases make a Git migration unsafe;
- a live workflow references a provider that the configured add-on loader will not load;
- a legacy Team release lacks the exact pinned evidence required by current release gates;
- an existing semantic collection has a different vector schema.

The remedy is an explicit migrator or operator decision, not a guess. A fail-closed upgrade can be inconvenient, but it preserves the identity or evidence the newer invariant was introduced to protect.

## Upgrade archaeology runbook

When a deployment behaves as if an older design is still active:

1. **Identify the running revision.** Record package/checkout commit, service unit `ExecStart`, config path, and runtime path. Do not assume the shell checkout is what systemd serves.
2. **Read the relevant changelog window.** Find both the introduction and later retirement. A June compatibility note may have been superseded days later.
3. **Inspect current assembly.** Check `pyproject.toml` entry points, systemd templates/install script, `build_admin_runtime`, provider/add-on catalogs, and the owning repository constructor.
4. **Find migration markers and both candidate stores.** Determine which one current code opens before editing either.
5. **Run the supported preflight/dry-run.** For managed Git, use the `git-repos migrate` flow; for add-ons, use discovery/enablement diagnostics; for service rename, use installer preflight.
6. **Quiesce the writers named by the migrator.** Preserve backups and active lease/operation evidence.
7. **Apply once and verify through the current API.** Check IDs, counts, SHAs, revisions, and provider state—not merely that the new file exists.
8. **Finalize only when rollback artifacts are no longer needed.** Do not leave two writable authorities.
9. **Remove stale operational instructions.** Update local runbooks and automation so the next operator does not invoke a retired command.

## Source trail

- `CHANGELOG.md`
- `README.md`
- `docs/architecture.md`
- `docs/execution-backends.md`
- `docs/addons.md`
- `docs/repository-layer-refactor-note.md`
- `src/evander/admin/composition.py`
- `src/evander/workflow/plugins.py`
- `src/evander/execution/backend.py`
- `src/evander/messaging/wake_store.py`
- `src/evander/repositories/audit_index.py`
- `src/evander/addons.py`
- `src/evander/admin/services/managed_git_service.py`
- `src/evander_team_addon/repository.py`
- `scripts/install_systemd_units.sh`
- `tests/test_workflow_plugins.py`
- `tests/test_container_worker_execution_backend.py`
- `tests/test_addons.py`
- `tests/test_managed_git_migration_rewrite.py`
- `tests/test_team_addon_repository.py`


<div class="page-break"></div>

# Part V: Reference

Provide compact maps, diagnostic tables, terminology, and the cross-format subject index.

# 28. System Reference

> **At a glance.** This chapter is the compact map to Evander's operational surface: processes, ports, runtime stores, command entry points, UI areas, and source-code ownership. Use the earlier chapters to understand *why* these pieces exist; use this chapter when you need to find one quickly.

## 28.1 Reference conventions

All paths are relative to the repository root unless an absolute path is shown. Defaults describe the checked-in configuration for this edition; an installed system may override them. A row marked **optional** is part of the supported platform but is only useful when its integration is configured.

There are three names worth keeping distinct:

- a **console entry point**, such as `evander-gateway`;
- a **systemd unit**, such as `evander-gateway.service`;
- a **CLI subcommand**, such as `evander-cli trigger-orchestrator`.

The trigger worker illustrates why the distinction matters: its persistent unit is `evander-trigger-orchestrator.service`, while its executable interface is the `trigger-orchestrator` subcommand of `evander-cli`. There is no standalone `evander-trigger-orchestrator` console script.

## 28.2 Persistent control-plane services

`scripts/install_systemd_units.sh` is the installation inventory. It renders and enables the following units, then prints explicit start commands; installation itself does not start them.

| Unit | Process or command | Responsibility | Start when |
|---|---|---|---|
| `evander-firewall.service` | `scripts/apply_firewall.sh` | Applies the guest-to-host allowlist | Before isolated execution |
| `evander-gateway.service` | `evander-gateway` | MCP gateway, identity, policy, approval, and audit boundary | Agents need host tools |
| `evander-admin-web.service` | `evander-admin-web` | Operator UI and Admin API | Operators need the supported GUI/API |
| `evander-openai-relay.service` | `evander-openai-relay` | Host-owned OpenAI/Anthropic-compatible model access | Agents need configured models |
| `evander-nuget-relay.service` | `evander-nuget-relay` | Allowlisted NuGet feeds and cache | .NET workloads need packages |
| `evander-npm-audit-facade.service` | `evander-npm-audit-facade` | Narrow npm audit/package-cache surface | Front-end workloads need it |
| `evander-http-target-relay.service` | `evander-http-target-relay` | Session-scoped, allowlisted external HTTP targets | A workflow grants an HTTP target |
| `evander-didww-sip-bridge.service` | `evander-didww-sip-bridge` | DIDWW/ARI telephony and realtime voice bridge | Telephony is configured |
| `evander-ado-worker.service` | `evander-ado-worker` | Azure DevOps PR-feedback and completion polling | ADO PR automation is configured; work-item intake is workflow-owned |
| `evander-proxy-issue-sync.service` | `evander-proxy-issue-sync` | Refreshes proxy issue status | Proxy issue providers are configured |
| `evander-discord-ingest.service` | `evander-discord-ingest` | Ingests allowlisted Discord commands/messages | Discord is configured |
| `evander-discord-wake.service` | `evander-discord-wake` | Turns eligible Discord inbox activity into wakes | Discord wake is enabled |
| `evander-scheduled-wake.service` | `evander-scheduled-wake` | Resumes sessions whose scheduled wake is due | Scheduled work is enabled |
| `evander-teams-ingest.service` | `evander-cli teams-ingest` via its runner script | Ingests Microsoft Teams messages | Teams is configured |
| `evander-teams-meeting-ingest.service` | `evander-teams-meeting-ingest` | Ingests queued meeting transcript and speech-status events | Meeting integration is configured |
| `evander-trigger-orchestrator.service` | `evander-cli trigger-orchestrator` | Polls trigger providers, validates/deduplicates, and publishes durable trigger events | Workflow triggers are enabled |
| `evander-effect-orchestrator.service` | `evander-effect-orchestrator` | Traverses downstream workflow nodes and executes gates, queries, and effects | Workflows may run beyond their trigger |

The container worker is intentionally absent from this list. `evander-worker-daemon` runs on a dedicated worker VM deployed by `ansible/worker.yml`, not as another control-plane service installed by the script above.

## 28.3 Default guest-reachable ports

The firewall's checked-in default is `ALLOWED_TCP_PORTS="22 8000 11434 11666 11777 11881 8081 3142"`. Allowing a port makes a narrow host surface reachable; it does not grant arbitrary host access.

| Port | Surface | Typical guest use | Important qualification |
|---:|---|---|---|
| 22 | Restricted SSH/Git | Clone and push through managed Git | The command wrapper and scoped key enforce allowed repositories and refs |
| 8000 | MCP gateway | Invoke host tools at `/mcp` | A plain `GET /mcp` may return `406` and still prove reachability |
| 11434 | Model relay | OpenAI-compatible `/v1` and configured Anthropic surface | The host keeps upstream credentials |
| 11666 | NuGet relay | Restore from configured NuGet feeds | It is not a general HTTP proxy; health is `/healthz` |
| 11777 | HTTP target relay | Reach a session allowlist of external targets | Guest-supplied auth can pass through; host secrets are not injected |
| 11881 | npm audit facade | Narrow npm audit/cache operations | It is not general npm internet access |
| 8081 | Nexus cache | PyPI/npm package cache when enabled | Optional host package-cache service |
| 3142 | apt-cacher-ng | Worker provisioning package cache | Optional and primarily for provisioning |

Admin Web defaults to host port `8100`; it is an operator surface, not part of the default guest allowlist.

## 28.4 Selected runtime source-of-truth map

The runtime tree contains both live configuration and operational state. The table highlights the stores most useful for orientation; it is not an exhaustive file inventory. Back up related stores consistently, avoid ad-hoc concurrent edits, and use the service/API responsible for each path.

| Path | Canonical content | Primary owner or reader |
|---|---|---|
| `runtime/host-config.json` | Host-global bootstrap settings | Host configuration and runtime composition |
| `runtime/directories.json` | Directory tree and ownership metadata | Directory service |
| `runtime/directory-config/root/.../<domain>.json` | Root baseline and directory overlays | Directory configuration service |
| `runtime/admin-web.db` | Admin Web users and authentication state | Admin Web auth service |
| `runtime/sessions.json` | Core session manifests and lifecycle metadata | Session repository |
| `runtime/local-issues.json` | Unified local and proxy issue records | Issue service and provider workflows |
| `runtime/audit.jsonl` | Append-only tool and denial audit journal | MCP gateway and audit writers |
| `runtime/audit.db` | Rebuildable query index over audit entries | Admin Web Tool Calls |
| `runtime/orchestration.db` | Trigger events, workflow/effect/gate executions | Trigger and effect orchestrators |
| `runtime/scheduled-internal-events.json` | Durable one-shot internal events | Schedule effect and trigger worker |
| `runtime/workflow-pack-history/` | Pack revisions and append-only change ledger | Workflow pack service |
| `runtime/workflow-state.json` | Workflow-scoped state records | Workflow state providers |
| `runtime/wake-requests-store.db` | Scheduled wake requests and dispatch history | Scheduled-wake and reconciliation services |
| `runtime/model-usage.db` | Normalized model-usage observations and attribution | Model usage service and Team cost aggregation |
| `runtime/teams-meetings.json` | Teams meeting session/projection state | Teams meeting services |
| `runtime/managed-git-repositories.json` | Stable managed repository identities and bindings | Managed Git service |
| `runtime/managed-git/` | Host-owned bare repositories | Managed Git service and restricted SSH |
| `runtime/git-worktrees/` | Ephemeral operation worktrees | Managed Git effects; safe cleanup is service-owned |
| `runtime/artifact-spaces.json` | Artifact-space metadata and published versions | Artifact-space service |
| `runtime/published-artifacts/` | Host-published artifact blobs | Artifact effects |
| `runtime/communication-targets.db` | Directory-scoped communication targets | Messaging target service |
| `runtime/communication-artifacts/` | Staged message attachments | Communication effects |
| `runtime/review-request-store.json` | PR review-request inbox | Review request provider |
| `runtime/agent-consultations.db` | Consultation lifecycle and results | Consultation service |
| `runtime/host-agent-control-state.json` | Durable enabled/disabled agent state | Wake and execution control paths |
| `runtime/voice-sessions-store.json` | Phone/meeting/realtime session projection | Voice and meeting integrations |
| `runtime/addons/<addon-id>/` | Add-on-private state | The named add-on only |
| `runtime/addons/team-addon/state.db` | Team's canonical relational state | Team repository and Team Admin API |

`runtime/addons/team-addon/state.json` is a legacy import source, not the current Team database. The Team add-on imports it once when appropriate; ongoing state belongs in `state.db`.

## 28.5 Core command map

Run `evander-cli --help` and a subcommand's `--help` for the precise option set in the installed edition. These are the stable operator intentions behind the most common commands.

| Intent | Command or entry point |
|---|---|
| Initialize runtime state | `evander-cli init-state --runtime-dir runtime` |
| Broad machine-readable status | `evander-cli status --runtime-dir runtime --json` |
| Run the trigger loop interactively | `evander-cli trigger-orchestrator` |
| Register or refresh a managed repository | `evander-cli git-repos register ...` |
| Inspect managed repositories | `evander-cli git-repos list ...` |
| Create an issue VM | `evander-cli create-issue-vm <issue-id> ...` |
| Clone by managed identity | `evander-cli clone-repo-into-vm <vm-name> --managed-repo-id <id>` |
| Smoke-test a container worker | `evander-cli execution smoke-container-worker --json` |
| Start the MCP gateway outside systemd | `evander-gateway` |
| Start Admin Web outside systemd | `evander-admin-web` |
| Start the effect loop outside systemd | `evander-effect-orchestrator` |

Service installation has its own preflight contract:

```bash
./scripts/install_systemd_units.sh --preflight
sudo ./scripts/install_systemd_units.sh
```

## 28.6 Admin Web area map

Admin Web is the supported operator GUI. Its core areas answer different questions rather than presenting interchangeable views.

| Area | Operator question |
|---|---|
| Dashboard | Is the control plane broadly healthy, and what needs attention? |
| Agents | Which active and historical execution targets represent agent work? |
| Approvals | Which gated tool calls require an operator decision? |
| Artifacts | Which durable agent archives can be resolved or downloaded? |
| Add-ons | Which runtime extensions are available, enabled, or awaiting refresh/restart? |
| Skills | Which packaged agent capabilities are visible and managed? |
| VMs | Which libvirt machines exist, and what is their lifecycle state? |
| Issues | Which local/provider issue records are available for work intake? |
| Sessions | Which agent sessions are active, paused, terminal, or awaiting intervention? |
| Tool Visibility | Which tools does a session actually see, and what binding or denial explains that result? |
| MCP Tools | Which directory policy controls approval and denial for each tool? |
| Tool Calls | What was invoked, allowed, denied, or failed? |
| Configuration | Which host and directory-scoped values resolve here? |
| Reports | What happened during orchestration and operations, including effects and gates? |
| Users & Groups | Who can authenticate, and which groups/permissions govern operator access? |
| Workers | Which host/background systemd workers are configured, active, or reporting journal errors? |
| Workflow | Which pack revision is active, what do its graphs define, and what is the runtime activity? |

Workflow pack changes made in Admin Web are revision- and lock-aware. Do not bypass those semantics by rewriting a live pack file from a stale checkout.

## 28.7 Team Add-on view map

The Team Add-on contributes one navigation area with fourteen current views. There is no separate top-level **Roles** view; roles are edited in the **Teams** view.

| View | What it projects |
|---|---|
| Workspace | Cross-cutting attention, delivery, team, and work-status summary |
| Operations | Active leases, dispatches, sessions, and operational exceptions |
| Delivery | Deliverables and review progress |
| Memory | Durable Team memory records and artifact references |
| Projects | Project definitions, memberships, leads, and goals |
| Task board | Project tasks arranged by workflow lane |
| Cases | Structured operational cases and their lifecycle |
| Teams | Teams, agents, memberships, responsibilities, roles, and models |
| Chat | Team chat channels and messages |
| Mail | Sent/received projections over the durable Team inbox |
| Governance | Governance groups, goals, agenda, oversight, and cost rollups |
| Planning | Goals, deliverables, and status-filtered task context for planning work |
| Timeline | Time-ordered domain activity |
| Org chart | Visual team, reporting, and governance relationships |

UI state is a projection. For a disputed operational fact, prefer the Team Admin API payload and then `runtime/addons/team-addon/state.db` over what a stale browser tab last rendered.

## 28.8 Source-code ownership map

| Concern | Start here |
|---|---|
| Runtime assembly | `src/evander/admin/composition.py` (`build_admin_runtime`, `AdminRuntimeComponents`) |
| Admin domain logic | `src/evander/admin/services/` |
| Admin Web routes | `src/evander/admin/web/app.py` and domain route modules |
| MCP gateway | `src/evander/gateway/server.py`, `app.py`, and `cli.py`; `src/evander/gateway.py` is a compatibility shim |
| Policy and approval | `src/evander/policy.py`, approval services under `src/evander/admin/services/` |
| Trigger runtime | `src/evander/orchestration/`, `src/evander/triggers/`, and `src/evander/daemons/trigger_orchestrator.py` |
| Effects | `src/evander/effects/` and `src/evander/daemons/effect_orchestrator.py` |
| Event models | `src/evander/events/models.py` |
| Execution abstraction | `src/evander/execution/` |
| VM lifecycle | `src/evander/vm/` plus `scripts/*_vm.sh` |
| Container worker | `src/evander/worker_daemon.py`, `ansible/worker.yml` |
| Managed Git | `src/evander/admin/services/managed_git_service.py` |
| Relays | `src/evander/relays/` |
| Communications | `src/evander/messaging/` |
| Core providers | `src/evander/providers/` |
| Add-on discovery | `src/evander/addons.py` and Python `evander.addons` entry points |
| Team domain/repository | `src/evander_team_addon/models.py`, `src/evander_team_addon/repository.py`, and `src/evander_team_addon/services/domain/` |
| Team commands | `src/evander_team_addon/services/commands/` |
| Team providers and wake context | `src/evander_team_addon/providers.py` |
| Team MCP tools | `src/evander_team_addon/mcp.py` |
| Team UI | `src/evander_team_addon/ui_static/` |

Route handlers and compatibility adapters should stay thin. New domain behavior belongs in a narrow service or domain module and should enter Admin surfaces through the supported runtime composition layer.

## 28.9 Fast orientation commands

These commands avoid intentional business-state mutation. `evander-cli status` is bootstrap-aware: it may create or migrate missing runtime configuration, and it reports a useful core subset rather than every installed service or endpoint. Run it as the intended operator against the intended runtime directory.

```bash
# Basic control-plane summary; may initialize/migrate missing runtime configuration
evander-cli status --runtime-dir runtime --json

# Installed unit state
systemctl --no-pager --full status evander-gateway.service

# MCP endpoint reachability: 406 is a valid liveness signal for a plain GET
curl -i http://127.0.0.1:8000/mcp

# NuGet relay health
curl -fsS http://127.0.0.1:11666/healthz

# Team database shape without creating a missing database or exposing row contents
test -f runtime/addons/team-addon/state.db && \
  sqlite3 -readonly runtime/addons/team-addon/state.db '.tables'

# Repository and generated-document state
git status --short
python scripts/build_evander_user_manual.py --check
```

When a command reveals secrets or user content, prefer field names, counts, redacted identifiers, and status codes in incident notes. Do not paste raw runtime databases or environment files into tickets or documentation.

## Source trail

- `pyproject.toml` — installed console entry points and add-on registrations.
- `scripts/install_systemd_units.sh` — persistent control-plane unit inventory.
- `config/firewall.env.example` — default guest-reachable ports.
- `docs/configuration-reference.md` — runtime stores and configuration ownership.
- `frontend/workflow-editor/src/adminRouting.ts` and `frontend/workflow-editor/src/features/workers/WorkersScreen.tsx` — current Admin navigation and Workers semantics.
- `src/evander_team_addon/ui_static/index.html` and `src/evander_team_addon/ui_static/team-ui.js` — current Team view inventory and projections.
- `src/evander/admin/composition.py` and `src/evander/admin/services/` — supported Admin runtime assembly and domain services.


# 29. Troubleshooting Decision Tables

> **At a glance.** Start with the failed boundary, collect one authoritative observation, and only then change state. These tables turn common symptoms into bounded investigations for core Evander and the Team Add-on.

## 29.1 The diagnostic discipline

Most difficult incidents become simpler when the evidence is read in this order:

1. **Name the intended outcome.** “The agent should have been woken” is more useful than “Team is broken.”
2. **Locate the boundary.** Browser/API, command transaction, durable store, orchestrator, execution backend, relay, or provider.
3. **Read the source of truth.** Use live service status, canonical store rows, and exact event/effect records before local speculation.
4. **Correlate identities.** Keep the session ID, directory, event ID, workflow run, effect execution, Team task/lease, and Git SHA together.
5. **Choose the smallest reversible action.** Retry through the owning API or worker; do not hand-edit a database or live workflow pack.
6. **Verify the outcome and the side effects.** A successful HTTP response is not proof that the downstream event, effect, wake, or release happened.

The first broad check is:

```bash
evander-cli status --runtime-dir runtime --json
```

Then narrow the investigation. Avoid running broad smoke suites without an explicit timeout; UI and browser processes can retain open handles after nearby assertions have passed.

## 29.2 Core control-plane symptoms

| Symptom | First authoritative observation | Likely boundary | Next safe action |
|---|---|---|---|
| Admin Web does not load | `systemctl status evander-admin-web.service` and its journal | Process, bind address, or `config/admin-web.env` | Correct the service/config problem, restart only that unit, then fetch its health/UI URL |
| Admin Web loads but a panel is stale | Compare the panel's API response with its canonical runtime store | Browser cache, API projection, or store/index lag | Hard-refresh; if the API is stale, inspect the owning service rather than rebuilding unrelated state |
| MCP plain `GET` returns `406` | Response is from `/mcp` on the expected host/port | Usually no fault: transport negotiation is missing | Treat it as liveness; test an actual initialized MCP client for protocol behavior |
| Guest cannot reach MCP | Guest `curl -i` to the configured gateway URL, then host firewall/unit status | Guest environment, network, firewall, or gateway | Verify `/etc/evander/session.env`, source `~/.profile`, and compare the configured host IP/port with firewall rules |
| Tool call is denied | Matching entry in `runtime/audit.jsonl` with subject, directory, tool, and reason | Identity, capability, policy, or approval | Correct the narrow policy/capability or approve through Admin Web; do not broaden unrelated tools |
| Approval appears stuck | Approval record and corresponding audit/tool-call state | Approval lifecycle or client retry | Decide/cancel through the approval surface; confirm the resumed call uses the intended request identity |
| Tool call succeeded but no workflow ran | Published event record plus the relevant provider/worker cursor (often `runtime/trigger-cursors-*.json`), not just the tool response | Event emission, trigger match, or cursor | Trace the event type and trigger predicates; then inspect the exact pack revision used |
| Trigger worker is inactive | `evander-trigger-orchestrator.service` status and journal | Worker service/config | Remember the executable is `evander-cli trigger-orchestrator`; run preflight and correct its config before restart |
| Workflow matched but a downstream node did not run | Trigger event, workflow event, and gate/effect execution rows in `runtime/orchestration.db` | Queueing, gate, lease, or effect worker | Trace the node/event identities and inspect `evander-effect-orchestrator.service`; retry only when idempotency semantics permit |
| Workflow edit reports a revision conflict | Current pack revision/lock from Admin API | Concurrent operator edit | Reload, reapply the intended change to the new revision, and save through the API/UI |
| Audit UI disagrees with journal | Tail of `runtime/audit.jsonl` versus query result in `runtime/audit.db` | Rebuildable audit index | Use the documented audit-index refresh; preserve the append-only journal |

## 29.3 Execution and relay symptoms

| Symptom | First authoritative observation | Likely boundary | Next safe action |
|---|---|---|---|
| VM creation fails before boot | Exact output of `launch_agent_vm.sh`, libvirt domain/network state | Host prerequisites, storage, libvirt network, or permissions | Keep disks under `/var/lib/libvirt/images/evander`; fix the named prerequisite and rerun the reconciliatory script |
| Guest tool is missing or stale | Tool version inside the guest and relevant bootstrap log | Optional bootstrap layer | Rerun the specific bootstrap script; do not rebuild the base image for one optional toolchain |
| Codex asks for login | `OPENAI_API_KEY`, relay base URL, and current shell profile inside guest | Guest bootstrap/profile | `source ~/.profile`; if values remain absent, rerun `bootstrap_codex_vm.sh` and verify relay-aware config |
| Codex calls `api.openai.com` directly | Effective client base URL inside guest | Relay configuration overwritten or absent | Restore the relay URL from session/bootstrap configuration; do not place the real upstream key in the guest |
| Stream starts and then truncates | OpenAI relay journal plus upstream/downstream status | Streaming relay lifetime, client disconnect, or upstream error | Preserve raw status/timing evidence; verify the relay keeps its upstream client open for the whole stream |
| NuGet restore cannot connect | `curl -fsS http://<host>:11666/healthz` from the relevant network | Firewall, relay service, or feed allowlist | Restore relay health first, then inspect the named feed; do not add general guest egress |
| HTTP target request is rejected | Session's effective target allowlist and relay response | Target grant, method/path policy, or expired session | Reissue the narrow session grant or correct the target; never inject a host secret as a shortcut |
| Container worker smoke fails | `evander-cli execution smoke-container-worker --json` plus worker journal | Control-plane URL/token, worker daemon, image, or network | Correct the exact failed hop; remember worker deployment is owned by `ansible/worker.yml` |
| Browser smoke hangs | Last useful output and process list after command timeout | Pending promise, `networkidle`, unclosed browser/server, or missing UI condition | Stop the process, retain artifacts, and run the focused scenario with explicit assertions and timeout |

## 29.4 Managed Git and artifact symptoms

| Symptom | First authoritative observation | Likely boundary | Next safe action |
|---|---|---|---|
| Guest clone is denied | Restricted-SSH log plus managed repository ID/binding | Unknown ID, directory binding, key scope, or wrapper rule | Register/refresh through `evander-cli git-repos`; clone by returned `managed_repo_id` |
| Guest push is denied | Attempted ref and session-scoped allowed prefix | Ref protection or stale scoped key | Push to `refs/heads/agents/<session-id>/<topic>` with the provisioned restricted key |
| Published branch is not the reviewed commit | Review candidate SHA, approved SHA, lease expectation, and remote ref | Exact-SHA/lease gate | Stop publication; re-establish review evidence and promote the exact approved SHA through the managed effect |
| Managed Git operation left a worktree | Operation manifest/lock under `runtime/git-worktrees/` and process liveness | Interrupted operation or stale process | Use the service's reconciliation/cleanup path; do not blindly delete an active worktree |
| Artifact reference exists but reviewer cannot read it | Artifact-space ACL and latest published version | Visibility or version publication | Grant the intended reader at the space boundary or republish correctly; do not copy private blobs into a public path |
| Review session lacks expected files | Review import list/errors in the lease wake context | Artifact publish/import effect | Resolve the reported import error and redispatch; reviewers should use imported `/artifacts` paths, not open `artifact-space:` refs directly |

## 29.5 Provider, communication, and wake symptoms

| Symptom | First authoritative observation | Likely boundary | Next safe action |
|---|---|---|---|
| Azure DevOps work item never enters a workflow | Matching trigger/query run, workflow-owned cursor, and effective ADO provider config | Trigger/query filters, credentials, cursor, or downstream proxy-issue effect | Trace `trigger:ado:work-item-matching` or `query:ado:work-items`; the ADO worker owns PR feedback/completion, not work-item intake |
| PR feedback is present but agent does not resume | `runtime/pr-feedback-store.json`, agent control state, and ADO worker journal | Ingest, dedupe, disabled agent, or wake | Correlate comment/PR/session IDs; enable only the intended agent or correct the failed wake |
| Scheduled wake is overdue | Scheduled wake record, host agent control state, and worker journal | Due-time parsing, disabled agent, session terminality, or worker | Confirm UTC/time zone and eligibility, then wake through the owning worker/API |
| Discord or Teams message is visible externally but absent internally | Ingest cursor/store and integration worker journal | External permission, poll/webhook, allowlist, or cursor | Repair ingest first; do not debug wake logic until a canonical inbox/message record exists |
| Message is stored but no wake occurs | Message/inbox record, wake eligibility, and wake worker log | Priority, handled state, disabled agent, dedupe, or active-session policy | Evaluate current wake rules and idempotency key; avoid duplicating a successful wake |
| Outbound notification fails | Effect execution and configured target in `runtime/communication-targets.db` | Target capability, credential, attachment, or provider | Correct the named target/provider and retry the effect only when its idempotency record permits |

## 29.6 Team command and UI symptoms

| Symptom | First authoritative observation | Likely boundary | Next safe action |
|---|---|---|---|
| Team view shows old data | Current Team state API revision and browser network response | UI refresh/projection | Reload the view; if API state is current, capture a UI defect without changing `state.db` |
| Team mutation returns conflict | Current resource revision/token and submitted expected revision | Optimistic concurrency | Refresh, reassess intent against the current record, and resubmit with the new expected revision |
| Command says success but derived work is missing | Command receipt, outbox rows, and projection rows in `state.db` | Command transaction, outbox dispatch, or projection | Trace the command ID through outbox processing; never manufacture the missing projection by hand |
| Role cannot be found in navigation | Teams view for the relevant team | UI mental model | Manage roles inside **Teams**; there is no top-level Roles view |
| Mail appears inconsistent | Durable inbox rows and sent/received projections | Status/filter misunderstanding | Treat Mail as views over one Team inbox collection; compare direction, recipient, pending, and handled fields |
| Team tool is unavailable to an agent | MCP tool catalog for that identity, session, and Team membership | Tool class, add-on registration, capability, or Team scope | Verify the add-on loaded and inspect the specific identity/capability decision |
| Agent is shown busy forever | Active lease, dispatch/session state, and active enqueue-prompt activity | Lease finalization, stuck prompt activity, or lost session | Reconcile through lease/session lifecycle operations; preserve the dispatch and prompt-activity evidence for diagnosis |

## 29.7 Team wake and delivery symptoms

| Symptom | First authoritative observation | Likely boundary | Next safe action |
|---|---|---|---|
| Pending Team inbox item does not wake an agent | Inbox priority/handled state, active lease/session, and wake decision payload | Eligibility or duplicate suppression | Current behavior treats non-low pending inbox work as wakeable; inspect the actual decision reason rather than assuming `response_requested` is required |
| Agent wakes with irrelevant context | Runtime operations payload for that exact wake | Context compaction, scope, or stale related records | Fix the selector/relationship; do not expand every wake with the entire Team database |
| Task remains queued | Task status, assignment, dependencies, team capacity, active leases | Planning/dispatch eligibility | Resolve the explicit blocker or assignment; confirm a dispatch was actually created |
| Deliverable was submitted but no review begins | Deliverable, review request, reviewer assignment, and outbox/event state | Review cascade creation or reviewer capacity | Trace deliverable ID to review request and dispatch; requeue only if no idempotent equivalent exists |
| Reviewer rejects but implementer is not reactivated | Review decision, cascade link, follow-up task/lease, inbox | Cascade transition or wake | Inspect the rejection's requested changes and generated follow-up relationship before manual reassignment |
| Ephemeral delegate never exits | Delegation task, lease, child session, terminal result | Cleanup/finalization | Preserve result/artifacts, then run the supported terminal cleanup; do not silently convert it into a persistent agent |
| Release is waiting | Release package, verification records, approved candidate SHA, and gate status | Missing evidence, failed job, or lease mismatch | Satisfy the named gate; only partial, not-run, unknown, or missing verification can receive an authorized reasoned waiver—failed verification is unwaivable and requires a corrected reviewed candidate |
| Release reports success but remote ref differs | Remote ref readback and recorded published SHA | Publish/readback failure | Treat as failed publication, stop follow-on completion, and reconcile the remote with the exact approved SHA |

## 29.8 Evidence bundle for escalation

An effective escalation contains a small, correlated packet:

- expected outcome and actual outcome;
- UTC time window and host/service identity;
- directory, session, workflow run/event/effect IDs;
- for Team: team, project, task, dispatch, lease, review, and release package IDs as applicable;
- for Git: managed repository ID, candidate/approved/published SHA, and target ref;
- relevant status rows or redacted JSON fields;
- exact command, exit status, timeout, and last useful output;
- what was already retried and why that retry was safe.

Do not include API keys, cookies, bearer tokens, private key material, raw environment files, or unrestricted database dumps. A screenshot should be dark-mode when practical and sanitized just like text evidence.

## 29.9 Recovery boundaries

Stop and obtain a deliberate operator decision before any action that would:

- rewrite or discard live workflow-pack revisions;
- delete a runtime database, audit journal, managed bare repository, or active worktree;
- reset a provider cursor that could replay external actions;
- broaden guest internet access or place a host credential inside a guest;
- bypass an approval, review, verification, or exact-SHA publication gate;
- synthesize Team projection rows instead of repairing the owning command/outbox path.

Recovery is complete only when the original outcome is verified **and** the control evidence agrees: store state, worker status, audit trail, and external readback where applicable.

## Source trail

- `docs/operations.md` — supported operational procedures and live-state locations.
- `docs/live-smoke-tests.md` — bounded end-to-end verification patterns.
- `docs/git-remotes.md` — restricted Git, exact-SHA promotion, and worktree operations.
- `docs/addons.md` — Team state, UI, and MCP boundaries.
- `src/evander_team_addon/providers.py` — current Team wake context, review, artifact, and release behavior.
- `src/evander_team_addon/services/commands/` and `src/evander_team_addon/repository.py` — Team command transactions, revisions, and outbox persistence.


# 30. Glossary

> **At a glance.** Evander uses ordinary words—session, event, team, release—with precise technical meanings. This glossary defines those meanings for the current system and points out distinctions that prevent operational mistakes.

## A

**Active lease**<br>
A Team record giving an agent responsibility for a dispatch for a bounded period/lifecycle. A task can exist without an active lease; inspect the lease when determining whether work is actually assigned or running.

**Add-on**<br>
A packaged extension identified by `addon.json` and normally discovered by scanning configured package roots. Explicit modules and the `evander.addons` entry-point path remain supported compatibility/opt-in loading surfaces. An add-on may contribute providers, MCP tools, Admin Web UI, schema/catalog metadata, and private state under `runtime/addons/<addon-id>/` without owning core state.

**Admin API**<br>
The host-side HTTP surface behind Admin Web. It exposes composed domain services to authenticated operators and add-on UIs.

**Admin runtime**<br>
The assembled service graph returned by `build_admin_runtime`. CLI, gateway, and Admin Web integrations should use this composition surface rather than rebuilding services independently.

**Admin Web**<br>
Evander's supported operator GUI for health, execution, approvals, audit, workflows, configuration, workers, skills, chat, users, and add-on views.

**Agent**<br>
An execution identity capable of working inside an isolated session. In Team, an agent also has membership, responsibilities, model selection, availability, and potentially a reporting position.

**Agent Mail**<br>
The Team UI's sent and received projections over the durable Team inbox collection. It is not a second independent mail transport or a pair of unrelated databases.

**Approval**<br>
An operator decision required by policy before a gated tool call may proceed. Approval complements identity and capability checks; it does not replace them.

**Artifact**<br>
A file or bundle moved through a controlled host-owned publishing/import path. Guest files are not durable merely because they existed in a disposable session.

**Artifact space**<br>
A named, access-controlled namespace with published artifact versions, referenced as `artifact-space:<path>`. Review sessions receive allowed versions through controlled imports under `/artifacts`.

**Audit journal**<br>
The append-only `runtime/audit.jsonl` record of tool calls, denials, errors, and related decisions. `runtime/audit.db` is a query index, not the canonical journal.

## B

**Bare repository**<br>
A host-owned Git repository under `runtime/managed-git/` used as the controlled internal remote. It is addressed operationally by a managed repository ID, not exposed host paths.

**Base VM**<br>
The minimal reusable VM image from which disposable overlays are created. Optional language, browser, and package tooling is added by post-launch bootstraps.

**Bootstrap**<br>
An idempotent or reconciling host script that installs/configures a particular guest capability, such as Codex, .NET, NuGet, Python, or Playwright.

## C

**Canonical branch**<br>
The approved upstream branch that operators and external systems treat as the published result. It must be distinguished from internal candidate or release branches.

**Capability**<br>
An explicit permission associated with an identity/session that is evaluated before a host tool may be used. Capabilities are narrower than general network or host access.

**Case**<br>
A Team domain object for structured operational work that benefits from its own lifecycle and context rather than being represented only as a task or message.

**Chat tool**<br>
A Team MCP operation classified for interactive communication behavior. Classification matters to exposure and wake semantics; `team_reply_to_chat` is a chat tool, not an ordinary mutation.

**Command**<br>
A Team write request applied transactionally to canonical state with validation, authorization, revision checks, receipts, and often outbox records.

**Communication target**<br>
A directory-scoped, host-owned description of a communication endpoint and its supported capabilities—such as read, write, reply, notify, or comment—stored in `runtime/communication-targets.db`.

**Consultation**<br>
A bounded request to another agent/session for analysis or advice, persisted independently from the caller so lifecycle and cleanup can be tracked.

**Container worker VM**<br>
An execution backend in which a dedicated worker VM runs disposable Docker containers through `evander-worker-daemon`. It is additive to, not a replacement for, the libvirt VM backend.

**Control plane**<br>
The trusted host-side services that hold secrets, enforce policy, schedule work, manage state, and expose narrow capabilities to isolated execution environments.

**Cursor**<br>
Durable provider progress used to avoid rereading or replaying external events. Resetting a cursor can duplicate work and must be treated as a recovery action.

## D

**Deliverable**<br>
A Team submission representing completed work, usually tied to a task and containing a Git candidate or published artifact references plus a summary.

**Directory**<br>
Evander's hierarchical ownership and configuration scope. `root` is the immutable top-level directory and default baseline.

**Directory overlay**<br>
A domain-specific JSON configuration at `runtime/directory-config/root/...` that inherits from ancestors and overrides only its supplied fields.

**Dispatch**<br>
A Team execution assignment derived from work such as a task or review request. The dispatch describes work to run; a lease records who currently owns it.

**Disposable execution**<br>
A VM or container intended to be created for bounded work and removed afterward. Durable state and secrets remain on the host or pass through explicit artifacts/Git surfaces.

## E

**Effect**<br>
A workflow-requested side effect executed by the effect orchestrator, such as session creation, communication, artifact publication, Git operations, or internal event scheduling.

**Effect execution**<br>
The durable orchestration record for an effect attempt, including status, idempotency/lease context, output, and error.

**Ephemeral delegate**<br>
A temporary Team agent/session created for a bounded subtask. It must return a result and follow terminal cleanup rather than silently becoming persistent staff.

**Event**<br>
A typed fact offered to orchestration. An event does nothing by itself until a trigger/provider and active workflow revision match it.

**Exact-SHA publication**<br>
The rule that Git publication must use the precise commit that passed review and verification, protected by lease/readback checks rather than a movable branch assumption.

**Execution backend**<br>
The abstraction that creates, resumes, inspects, and terminates isolated execution. Current choices are `libvirt_vm` and the additive `container_worker_vm`.

## G

**Gate**<br>
A workflow or release condition that must be satisfied before a dependent effect or transition proceeds. Gates preserve evidence and policy rather than serving as informal checklists.

**Gateway**<br>
The MCP-facing host process (`evander-gateway`) that composes tools, establishes request context, applies policy/approval, invokes handlers, and writes audit evidence.

**Governance agenda**<br>
A Team record surfacing a management signal to the appropriate governance group manager. Pending agenda can contribute to wake and Workspace attention.

**Governance group**<br>
A Team grouping that oversees member teams, goals, agenda, and aggregate cost/operational signals.

**Guest**<br>
An untrusted or less-trusted VM/container execution environment. It receives session-scoped configuration and narrow host URLs, never the host's general secret set.

## H

**Host config**<br>
The host-global bootstrap settings in `runtime/host-config.json`. It is composed with directory-scoped domains; legacy gateway YAML is import input, not the current baseline.

**HTTP target relay**<br>
A session-scoped relay for explicit external HTTP targets. It forwards guest-managed headers/cookies subject to the grant but does not inject host secrets.

## I

**Idempotency key**<br>
A stable identity used to recognize that an event/effect/command has already been applied. It permits safe retry only when the operation's contract actually honors it.

**Identity**<br>
The authenticated subject attached to a request: operator, agent, session, directory, and related scope. Identity answers “who”; policy answers “may they do this here now?”

**Inbox item**<br>
A durable Team communication/work signal with priority, recipient, handling state, and optional response intent. Current wake logic makes pending non-low inbox work eligible without requiring `response_requested`.

**Internal event**<br>
A host-local typed event used to connect commands, schedules, Team transitions, and workflows without pretending it came from an external provider.

## L

**Lease**<br>
A time- or ownership-bounded claim that prevents conflicting workers or actors from safely performing the same mutable operation. Team work and Git release publication both use lease concepts, at different layers.

**libvirt VM backend**<br>
The default execution backend using disposable overlays and libvirt domains on the control-plane host.

**Low priority**<br>
A Team inbox priority that does not by itself make the item wakeable. It can still be read and handled through normal work.

## M

**Managed repository ID**<br>
The stable opaque identity returned when an upstream repository is registered. Workflows, Team, CLI, and guest clone operations use it instead of a host filesystem path.

**Managed Git**<br>
Evander's host-owned system for canonical upstream refresh, restricted guest refs, detached operation worktrees, exact-SHA promotion, and lease-protected publication.

**MCP (Model Context Protocol)**<br>
The protocol used to expose typed host tools to agent clients. In Evander, MCP is a narrow control surface, not a code-transport mechanism or general remote shell.

**Membership**<br>
A Team relationship placing an agent in a team with responsibility/role context. Project membership and team membership are related but not interchangeable.

**Memory (Team)**<br>
A durable Team knowledge record with scope and optional published artifacts. It is explicit domain state, not the model's transient context window.

**Model relay**<br>
The host service on the OpenAI relay surface that holds upstream credentials and exposes configured compatible APIs to guests.

## O

**Optimistic concurrency**<br>
The pattern in which a client submits an expected revision/token and a mutation fails on mismatch. The safe response is refresh, reassess, and resubmit—not overwrite.

**Org chart**<br>
The Team visual projection of teams, agents, reporting, and governance relationships. It is derived from domain records and is not a separate source of truth.

**Outbox**<br>
Rows written in the same transaction as a Team command so downstream events/actions can be dispatched reliably after canonical state commits.

## P

**Pack revision**<br>
An immutable/versioned view of an operator workflow pack used for validation, concurrency control, and run provenance.

**Policy**<br>
Rules resolving whether a subject can invoke a tool in a directory/session context and whether the invocation needs approval. Policy denial is itself audited.

**Projection**<br>
A read-oriented representation derived from canonical state, such as the Team Workspace, Org chart, Mail view, or audit SQLite index.

**Project**<br>
A Team scope organizing goals, members, leads, tasks, plans, deliverables, and release activity.

**Provider**<br>
A registered implementation that supplies triggers, gates, queries, effects, schemas, editor metadata, or other runtime behavior to the orchestration catalog.

## Q

**Query**<br>
A workflow node that reads host or provider state and publishes structured outputs without being the external business side effect. Like effect completions, query completions continue the graph through durable events.

## R

**Relay**<br>
A narrow host-owned network service that mediates a specific external capability—models, NuGet, HTTP targets, or npm audit—without granting general guest internet.

**Release package**<br>
The Team record gathering candidate commit, target, review and verification evidence, retry state, waivers, and publication result for a release attempt.

**Resource token**<br>
A Team UI/API concurrency token tied to a mutable resource. It complements numeric revisions where a more specific mutation guard is required.

**Review cascade**<br>
The linked Team lifecycle from deliverable submission through review requests/dispatches, decisions, requested changes, follow-up work, and final acceptance.

**Review request**<br>
A request for a reviewer to evaluate a deliverable or PR. Core's PR review-request inbox and Team review requests are distinct domain records even when a workflow connects them.

**Role**<br>
A Team-defined responsibility profile used within a team. Roles are managed inside the **Teams** view; there is no current top-level Roles view.

**Root directory**<br>
The immutable top node named `root` in Evander's directory tree and the baseline for inherited domain configuration.

**Run**<br>
A durable workflow evaluation/execution instance tied to an event and pack revision. It contains matches, gates, and queued effects rather than being synonymous with an agent session.

## S

**Scheduled wake**<br>
A durable request to resume an eligible session at/after a time. The dedicated wake worker also respects disabled-agent and terminal-session state.

**Secret boundary**<br>
The design rule that real upstream secrets remain on trusted host surfaces and are not copied into guests; narrow relays/effects perform authorized operations.

**Session**<br>
A bounded agent execution identity and lifecycle, usually associated with one disposable VM/container and session-scoped environment, grants, Git keys, and audit context.

**Session environment**<br>
Guest configuration written to `/etc/evander/session.env` and loaded by the agent shell profile. It contains narrow endpoints and session values, not the host's general secrets.

**Skill**<br>
An Evander-managed, reviewed `SKILL.md` copied into the selected user or system skill library. Skills are distinct from MCP tools and workflow providers; the current managed-skill unit is one instruction file rather than an arbitrary asset bundle.

**Source trail**<br>
The end section of each manual chapter identifying the authoritative implementation and documentation used for verification.

**State database (Team)**<br>
`runtime/addons/team-addon/state.db`, the current canonical SQLite store for Team domain records, leases, command receipts, and related state.

## T

**Task**<br>
A Team unit of planned work with status, ownership/assignment, dependencies, project context, and possible dispatch/deliverable/review relationships.

**Team**<br>
An organizational domain object containing agents, responsibilities, roles, leadership/reporting context, and project participation. “Team” may also refer to the add-on when capitalized as **Team Add-on**.

**Team Add-on**<br>
The `evander_team_addon` package whose manifest ID is `team-addon` (its Python entry-point name is `team`). It contributes Team state, commands, providers, MCP tools, and an Admin Web area while preserving the core/add-on boundary.

**Tool class**<br>
A Team MCP exposure class. Current classes are ordinary, chat, ephemeral-worker, governance, and read; the active wake/session receives only the classes appropriate to its role and dispatch.

**Trigger**<br>
A workflow definition/provider that turns eligible external or internal events into workflow runs when its predicates and cursor rules match.

**Trust zone**<br>
A boundary with a defined threat/authority level: external providers, host control plane, isolated guest/container, and operator browser are separate zones even when they communicate.

## V

**Verification evidence**<br>
Structured proof attached to a release candidate, such as job result, command, commit SHA, artifact, timestamp, and any policy-authorized reasoned waiver. A failed verification is unwaivable; narrative confidence is never equivalent evidence.

**VM overlay**<br>
A disposable disk derived from the base image. New overlays default to 20 GB and belong under `/var/lib/libvirt/images/evander`.

## W

**Wake**<br>
A decision and action that starts or resumes an agent because eligible work exists. A task/message's existence is not proof that a wake row was created or delivered.

**Wake context**<br>
The compact, scoped payload given to an agent when it resumes, containing the relevant work, inbox, lease, review, artifact, and control facts rather than all Team state.

**Workflow**<br>
A revisioned graph connecting triggers, gates, queries, and effects. It coordinates durable host-side reads/actions and may create or resume agent sessions, but it is not itself an agent prompt transcript.

**Workflow pack**<br>
An operator-managed JSON document containing one or more workflows and provider configuration references, edited through revision/lock-aware surfaces.

**Worktree**<br>
A detached temporary checkout beneath `runtime/git-worktrees/<managed-repo-id>/<operation-id>/` used for a managed Git operation and removed/reconciled by its owner.

## Source trail

- Chapters 3–15 — core trust, configuration, policy, orchestration, execution, networking, Git, integrations, and operations terminology.
- Chapters 16–25 — Team boundary, domain, commands, wakes, communication, review, delegation, release, and governance terminology.
- `docs/configuration-reference.md` — canonical configuration and store names.
- `docs/addons.md` — add-on and Team contracts.
- `docs/git-remotes.md` — managed Git and exact-SHA publication language.
- `src/evander_team_addon/models.py`, `src/evander_team_addon/services/commands/`, and `src/evander_team_addon/providers.py` — current Team model and runtime behavior.


<div class="page-break"></div>

# 31. Subject Index

> **At a glance.** Use this linked index when you know the concept but not the subsystem that owns it.

## A

- **active leases** — [20. Wakes, Leases, Context Rotation, and Stand-down](#20-wakes-leases-context-rotation-and-stand-down), [28. System Reference](#28-system-reference).
- **add-on boundary** — [14. Semantic Memory, Model Usage, Skills, and Add-ons](#14-semantic-memory-model-usage-skills-and-add-ons), [16. Team Add-on Boundary and Domain Model](#16-team-add-on-boundary-and-domain-model).
- **Admin API** — [15. Admin Operations, Observability, Backup, and Recovery](#15-admin-operations-observability-backup-and-recovery), [17. Enabling and Operating the Team Add-on](#17-enabling-and-operating-the-team-add-on).
- **Admin Web** — [2. One Work Item, End to End](#2-one-work-item-end-to-end), [15. Admin Operations, Observability, Backup, and Recovery](#15-admin-operations-observability-backup-and-recovery), [28. System Reference](#28-system-reference).
- **agent identity** — [6. Identity, Sessions, RBAC, and Policy](#6-identity-sessions-rbac-and-policy), [18. Organization, Projects, Tasks, and Cases](#18-organization-projects-tasks-and-cases).
- **Agent Mail** — [21. Chat, Agent Mail, Voice, and Escalation](#21-chat-agent-mail-voice-and-escalation), [29. Troubleshooting Decision Tables](#29-troubleshooting-decision-tables).
- **agent sessions** — [6. Identity, Sessions, RBAC, and Policy](#6-identity-sessions-rbac-and-policy), [9. Execution Backends](#9-execution-backends).
- **approvals** — [7. MCP, Approvals, and Audit](#7-mcp-approvals-and-audit), [15. Admin Operations, Observability, Backup, and Recovery](#15-admin-operations-observability-backup-and-recovery).
- **architecture** — [3. Trust Model and Security Boundaries](#3-trust-model-and-security-boundaries), [4. System Topology, Services, and Ports](#4-system-topology-services-and-ports).
- **artifact spaces** — [11. Managed Git, Artifacts, and Code Consultations](#11-managed-git-artifacts-and-code-consultations), [22. Deliverables, Reviews, and Decision Cascades](#22-deliverables-reviews-and-decision-cascades).
- **audit** — [7. MCP, Approvals, and Audit](#7-mcp-approvals-and-audit), [15. Admin Operations, Observability, Backup, and Recovery](#15-admin-operations-observability-backup-and-recovery).
- **Azure DevOps** — [12. Issues and Provider Integrations](#12-issues-and-provider-integrations), [13. Communications, Meetings, Calls, and Wakes](#13-communications-meetings-calls-and-wakes).
## B

- **backup** — [15. Admin Operations, Observability, Backup, and Recovery](#15-admin-operations-observability-backup-and-recovery), [29. Troubleshooting Decision Tables](#29-troubleshooting-decision-tables).
- **bare repositories** — [11. Managed Git, Artifacts, and Code Consultations](#11-managed-git-artifacts-and-code-consultations), [24. Team Git Promotion and Release Gates](#24-team-git-promotion-and-release-gates).
- **base VM** — [9. Execution Backends](#9-execution-backends), [10. Networking, Relays, Packages, and Secrets](#10-networking-relays-packages-and-secrets).
- **bootstrap scripts** — [9. Execution Backends](#9-execution-backends), [26. Engineering and Extension Guide](#26-engineering-and-extension-guide).
## C

- **canonical branch** — [11. Managed Git, Artifacts, and Code Consultations](#11-managed-git-artifacts-and-code-consultations), [24. Team Git Promotion and Release Gates](#24-team-git-promotion-and-release-gates).
- **capabilities** — [6. Identity, Sessions, RBAC, and Policy](#6-identity-sessions-rbac-and-policy), [7. MCP, Approvals, and Audit](#7-mcp-approvals-and-audit).
- **cases** — [18. Organization, Projects, Tasks, and Cases](#18-organization-projects-tasks-and-cases), [28. System Reference](#28-system-reference).
- **chat** — [13. Communications, Meetings, Calls, and Wakes](#13-communications-meetings-calls-and-wakes), [21. Chat, Agent Mail, Voice, and Escalation](#21-chat-agent-mail-voice-and-escalation).
- **CLI** — [15. Admin Operations, Observability, Backup, and Recovery](#15-admin-operations-observability-backup-and-recovery), [28. System Reference](#28-system-reference).
- **code consultations** — [11. Managed Git, Artifacts, and Code Consultations](#11-managed-git-artifacts-and-code-consultations).
- **command receipts** — [19. Commands, Identity, Revisions, and the Durable Outbox](#19-commands-identity-revisions-and-the-durable-outbox).
- **communication targets** — [13. Communications, Meetings, Calls, and Wakes](#13-communications-meetings-calls-and-wakes), [28. System Reference](#28-system-reference).
- **configuration** — [5. Runtime Assembly, Configuration, and Directories](#5-runtime-assembly-configuration-and-directories), [28. System Reference](#28-system-reference).
- **container worker VM** — [9. Execution Backends](#9-execution-backends), [10. Networking, Relays, Packages, and Secrets](#10-networking-relays-packages-and-secrets).
- **control plane** — [3. Trust Model and Security Boundaries](#3-trust-model-and-security-boundaries), [4. System Topology, Services, and Ports](#4-system-topology-services-and-ports).
- **cost tracking** — [25. Governance, Memory, Cost, and Internals](#25-governance-memory-cost-and-internals).
- **credentials** — [3. Trust Model and Security Boundaries](#3-trust-model-and-security-boundaries), [10. Networking, Relays, Packages, and Secrets](#10-networking-relays-packages-and-secrets).
- **cursors** — [8. Workflow Runtime and Orchestration](#8-workflow-runtime-and-orchestration), [12. Issues and Provider Integrations](#12-issues-and-provider-integrations).
## D

- **dark mode** — [17. Enabling and Operating the Team Add-on](#17-enabling-and-operating-the-team-add-on), [26. Engineering and Extension Guide](#26-engineering-and-extension-guide).
- **deliverables** — [22. Deliverables, Reviews, and Decision Cascades](#22-deliverables-reviews-and-decision-cascades), [24. Team Git Promotion and Release Gates](#24-team-git-promotion-and-release-gates).
- **design history** — [27. Design History and Compatibility Boundaries](#27-design-history-and-compatibility-boundaries).
- **directories** — [5. Runtime Assembly, Configuration, and Directories](#5-runtime-assembly-configuration-and-directories), [6. Identity, Sessions, RBAC, and Policy](#6-identity-sessions-rbac-and-policy).
- **directory overlays** — [5. Runtime Assembly, Configuration, and Directories](#5-runtime-assembly-configuration-and-directories).
- **Discord** — [13. Communications, Meetings, Calls, and Wakes](#13-communications-meetings-calls-and-wakes), [29. Troubleshooting Decision Tables](#29-troubleshooting-decision-tables).
- **dispatch** — [18. Organization, Projects, Tasks, and Cases](#18-organization-projects-tasks-and-cases), [20. Wakes, Leases, Context Rotation, and Stand-down](#20-wakes-leases-context-rotation-and-stand-down).
- **disposable execution** — [3. Trust Model and Security Boundaries](#3-trust-model-and-security-boundaries), [9. Execution Backends](#9-execution-backends).
## E

- **effect orchestrator** — [8. Workflow Runtime and Orchestration](#8-workflow-runtime-and-orchestration), [28. System Reference](#28-system-reference).
- **effects** — [8. Workflow Runtime and Orchestration](#8-workflow-runtime-and-orchestration), [26. Engineering and Extension Guide](#26-engineering-and-extension-guide).
- **email** — [13. Communications, Meetings, Calls, and Wakes](#13-communications-meetings-calls-and-wakes), [21. Chat, Agent Mail, Voice, and Escalation](#21-chat-agent-mail-voice-and-escalation).
- **ephemeral delegates** — [23. Ephemeral Delegation](#23-ephemeral-delegation), [29. Troubleshooting Decision Tables](#29-troubleshooting-decision-tables).
- **events** — [8. Workflow Runtime and Orchestration](#8-workflow-runtime-and-orchestration), [19. Commands, Identity, Revisions, and the Durable Outbox](#19-commands-identity-revisions-and-the-durable-outbox).
- **exact-SHA publication** — [11. Managed Git, Artifacts, and Code Consultations](#11-managed-git-artifacts-and-code-consultations), [24. Team Git Promotion and Release Gates](#24-team-git-promotion-and-release-gates).
- **execution backends** — [9. Execution Backends](#9-execution-backends), [28. System Reference](#28-system-reference).
- **extension points** — [14. Semantic Memory, Model Usage, Skills, and Add-ons](#14-semantic-memory-model-usage-skills-and-add-ons), [26. Engineering and Extension Guide](#26-engineering-and-extension-guide).
## F

- **failure recovery** — [15. Admin Operations, Observability, Backup, and Recovery](#15-admin-operations-observability-backup-and-recovery), [29. Troubleshooting Decision Tables](#29-troubleshooting-decision-tables).
- **firewall** — [3. Trust Model and Security Boundaries](#3-trust-model-and-security-boundaries), [10. Networking, Relays, Packages, and Secrets](#10-networking-relays-packages-and-secrets), [28. System Reference](#28-system-reference).
## G

- **gates** — [8. Workflow Runtime and Orchestration](#8-workflow-runtime-and-orchestration), [24. Team Git Promotion and Release Gates](#24-team-git-promotion-and-release-gates).
- **Git** — [11. Managed Git, Artifacts, and Code Consultations](#11-managed-git-artifacts-and-code-consultations), [24. Team Git Promotion and Release Gates](#24-team-git-promotion-and-release-gates).
- **GitHub** — [12. Issues and Provider Integrations](#12-issues-and-provider-integrations), [24. Team Git Promotion and Release Gates](#24-team-git-promotion-and-release-gates).
- **glossary** — [30. Glossary](#30-glossary).
- **governance agenda** — [25. Governance, Memory, Cost, and Internals](#25-governance-memory-cost-and-internals).
- **governance groups** — [18. Organization, Projects, Tasks, and Cases](#18-organization-projects-tasks-and-cases), [25. Governance, Memory, Cost, and Internals](#25-governance-memory-cost-and-internals).
- **guest** — [3. Trust Model and Security Boundaries](#3-trust-model-and-security-boundaries), [9. Execution Backends](#9-execution-backends), [10. Networking, Relays, Packages, and Secrets](#10-networking-relays-packages-and-secrets).
## H

- **host configuration** — [5. Runtime Assembly, Configuration, and Directories](#5-runtime-assembly-configuration-and-directories), [28. System Reference](#28-system-reference).
- **HTTP target relay** — [10. Networking, Relays, Packages, and Secrets](#10-networking-relays-packages-and-secrets), [28. System Reference](#28-system-reference).
## I

- **idempotency** — [8. Workflow Runtime and Orchestration](#8-workflow-runtime-and-orchestration), [19. Commands, Identity, Revisions, and the Durable Outbox](#19-commands-identity-revisions-and-the-durable-outbox), [24. Team Git Promotion and Release Gates](#24-team-git-promotion-and-release-gates).
- **inbox** — [13. Communications, Meetings, Calls, and Wakes](#13-communications-meetings-calls-and-wakes), [20. Wakes, Leases, Context Rotation, and Stand-down](#20-wakes-leases-context-rotation-and-stand-down), [21. Chat, Agent Mail, Voice, and Escalation](#21-chat-agent-mail-voice-and-escalation).
- **index** — [1. How to Read This Manual](#1-how-to-read-this-manual), [30. Glossary](#30-glossary).
- **internal events** — [8. Workflow Runtime and Orchestration](#8-workflow-runtime-and-orchestration), [19. Commands, Identity, Revisions, and the Durable Outbox](#19-commands-identity-revisions-and-the-durable-outbox).
- **issue providers** — [12. Issues and Provider Integrations](#12-issues-and-provider-integrations).
## L

- **leases** — [20. Wakes, Leases, Context Rotation, and Stand-down](#20-wakes-leases-context-rotation-and-stand-down), [24. Team Git Promotion and Release Gates](#24-team-git-promotion-and-release-gates).
- **libvirt** — [9. Execution Backends](#9-execution-backends), [28. System Reference](#28-system-reference).
## M

- **managed Git** — [11. Managed Git, Artifacts, and Code Consultations](#11-managed-git-artifacts-and-code-consultations), [28. System Reference](#28-system-reference).
- **managed repository ID** — [11. Managed Git, Artifacts, and Code Consultations](#11-managed-git-artifacts-and-code-consultations), [24. Team Git Promotion and Release Gates](#24-team-git-promotion-and-release-gates).
- **MCP** — [7. MCP, Approvals, and Audit](#7-mcp-approvals-and-audit), [16. Team Add-on Boundary and Domain Model](#16-team-add-on-boundary-and-domain-model).
- **meetings** — [13. Communications, Meetings, Calls, and Wakes](#13-communications-meetings-calls-and-wakes), [21. Chat, Agent Mail, Voice, and Escalation](#21-chat-agent-mail-voice-and-escalation).
- **memory** — [14. Semantic Memory, Model Usage, Skills, and Add-ons](#14-semantic-memory-model-usage-skills-and-add-ons), [25. Governance, Memory, Cost, and Internals](#25-governance-memory-cost-and-internals).
- **Microsoft Teams** — [13. Communications, Meetings, Calls, and Wakes](#13-communications-meetings-calls-and-wakes), [21. Chat, Agent Mail, Voice, and Escalation](#21-chat-agent-mail-voice-and-escalation).
- **model relay** — [10. Networking, Relays, Packages, and Secrets](#10-networking-relays-packages-and-secrets), [14. Semantic Memory, Model Usage, Skills, and Add-ons](#14-semantic-memory-model-usage-skills-and-add-ons).
- **model usage** — [14. Semantic Memory, Model Usage, Skills, and Add-ons](#14-semantic-memory-model-usage-skills-and-add-ons), [25. Governance, Memory, Cost, and Internals](#25-governance-memory-cost-and-internals).
## N

- **networking** — [4. System Topology, Services, and Ports](#4-system-topology-services-and-ports), [10. Networking, Relays, Packages, and Secrets](#10-networking-relays-packages-and-secrets).
- **Northstar example** — [2. One Work Item, End to End](#2-one-work-item-end-to-end), [16. Team Add-on Boundary and Domain Model](#16-team-add-on-boundary-and-domain-model), [22. Deliverables, Reviews, and Decision Cascades](#22-deliverables-reviews-and-decision-cascades).
- **NuGet relay** — [10. Networking, Relays, Packages, and Secrets](#10-networking-relays-packages-and-secrets), [28. System Reference](#28-system-reference).
## O

- **observability** — [7. MCP, Approvals, and Audit](#7-mcp-approvals-and-audit), [15. Admin Operations, Observability, Backup, and Recovery](#15-admin-operations-observability-backup-and-recovery).
- **OpenAI relay** — [10. Networking, Relays, Packages, and Secrets](#10-networking-relays-packages-and-secrets), [28. System Reference](#28-system-reference).
- **operator workflows** — [8. Workflow Runtime and Orchestration](#8-workflow-runtime-and-orchestration), [15. Admin Operations, Observability, Backup, and Recovery](#15-admin-operations-observability-backup-and-recovery).
- **optimistic concurrency** — [19. Commands, Identity, Revisions, and the Durable Outbox](#19-commands-identity-revisions-and-the-durable-outbox), [29. Troubleshooting Decision Tables](#29-troubleshooting-decision-tables).
- **Org chart** — [17. Enabling and Operating the Team Add-on](#17-enabling-and-operating-the-team-add-on), [18. Organization, Projects, Tasks, and Cases](#18-organization-projects-tasks-and-cases).
- **outbox** — [19. Commands, Identity, Revisions, and the Durable Outbox](#19-commands-identity-revisions-and-the-durable-outbox), [29. Troubleshooting Decision Tables](#29-troubleshooting-decision-tables).
## P

- **pack revisions** — [8. Workflow Runtime and Orchestration](#8-workflow-runtime-and-orchestration), [15. Admin Operations, Observability, Backup, and Recovery](#15-admin-operations-observability-backup-and-recovery).
- **package relays** — [10. Networking, Relays, Packages, and Secrets](#10-networking-relays-packages-and-secrets), [28. System Reference](#28-system-reference).
- **phone calls** — [13. Communications, Meetings, Calls, and Wakes](#13-communications-meetings-calls-and-wakes), [21. Chat, Agent Mail, Voice, and Escalation](#21-chat-agent-mail-voice-and-escalation).
- **Playwright** — [9. Execution Backends](#9-execution-backends), [26. Engineering and Extension Guide](#26-engineering-and-extension-guide), [29. Troubleshooting Decision Tables](#29-troubleshooting-decision-tables).
- **policy** — [6. Identity, Sessions, RBAC, and Policy](#6-identity-sessions-rbac-and-policy), [7. MCP, Approvals, and Audit](#7-mcp-approvals-and-audit).
- **ports** — [4. System Topology, Services, and Ports](#4-system-topology-services-and-ports), [28. System Reference](#28-system-reference).
- **projects** — [18. Organization, Projects, Tasks, and Cases](#18-organization-projects-tasks-and-cases), [25. Governance, Memory, Cost, and Internals](#25-governance-memory-cost-and-internals).
- **provider registry** — [8. Workflow Runtime and Orchestration](#8-workflow-runtime-and-orchestration), [26. Engineering and Extension Guide](#26-engineering-and-extension-guide).
## Q

- **queries** — [8. Workflow Runtime and Orchestration](#8-workflow-runtime-and-orchestration), [30. Glossary](#30-glossary).
## R

- **RBAC** — [6. Identity, Sessions, RBAC, and Policy](#6-identity-sessions-rbac-and-policy), [19. Commands, Identity, Revisions, and the Durable Outbox](#19-commands-identity-revisions-and-the-durable-outbox).
- **recovery** — [15. Admin Operations, Observability, Backup, and Recovery](#15-admin-operations-observability-backup-and-recovery), [29. Troubleshooting Decision Tables](#29-troubleshooting-decision-tables).
- **relays** — [3. Trust Model and Security Boundaries](#3-trust-model-and-security-boundaries), [10. Networking, Relays, Packages, and Secrets](#10-networking-relays-packages-and-secrets).
- **release gates** — [24. Team Git Promotion and Release Gates](#24-team-git-promotion-and-release-gates), [29. Troubleshooting Decision Tables](#29-troubleshooting-decision-tables).
- **release package** — [24. Team Git Promotion and Release Gates](#24-team-git-promotion-and-release-gates).
- **resource tokens** — [19. Commands, Identity, Revisions, and the Durable Outbox](#19-commands-identity-revisions-and-the-durable-outbox).
- **restricted SSH** — [11. Managed Git, Artifacts, and Code Consultations](#11-managed-git-artifacts-and-code-consultations), [24. Team Git Promotion and Release Gates](#24-team-git-promotion-and-release-gates).
- **review cascades** — [22. Deliverables, Reviews, and Decision Cascades](#22-deliverables-reviews-and-decision-cascades).
- **review requests** — [11. Managed Git, Artifacts, and Code Consultations](#11-managed-git-artifacts-and-code-consultations), [22. Deliverables, Reviews, and Decision Cascades](#22-deliverables-reviews-and-decision-cascades).
- **roles** — [17. Enabling and Operating the Team Add-on](#17-enabling-and-operating-the-team-add-on), [18. Organization, Projects, Tasks, and Cases](#18-organization-projects-tasks-and-cases), [28. System Reference](#28-system-reference).
- **root directory** — [5. Runtime Assembly, Configuration, and Directories](#5-runtime-assembly-configuration-and-directories).
- **runbooks** — [15. Admin Operations, Observability, Backup, and Recovery](#15-admin-operations-observability-backup-and-recovery), [29. Troubleshooting Decision Tables](#29-troubleshooting-decision-tables).
- **runtime assembly** — [4. System Topology, Services, and Ports](#4-system-topology-services-and-ports), [5. Runtime Assembly, Configuration, and Directories](#5-runtime-assembly-configuration-and-directories).
- **runtime stores** — [5. Runtime Assembly, Configuration, and Directories](#5-runtime-assembly-configuration-and-directories), [15. Admin Operations, Observability, Backup, and Recovery](#15-admin-operations-observability-backup-and-recovery), [28. System Reference](#28-system-reference).
## S

- **scheduled wakes** — [13. Communications, Meetings, Calls, and Wakes](#13-communications-meetings-calls-and-wakes), [20. Wakes, Leases, Context Rotation, and Stand-down](#20-wakes-leases-context-rotation-and-stand-down).
- **screenshots** — [1. How to Read This Manual](#1-how-to-read-this-manual), [26. Engineering and Extension Guide](#26-engineering-and-extension-guide).
- **secrets** — [3. Trust Model and Security Boundaries](#3-trust-model-and-security-boundaries), [10. Networking, Relays, Packages, and Secrets](#10-networking-relays-packages-and-secrets).
- **semantic memory** — [14. Semantic Memory, Model Usage, Skills, and Add-ons](#14-semantic-memory-model-usage-skills-and-add-ons).
- **services** — [4. System Topology, Services, and Ports](#4-system-topology-services-and-ports), [28. System Reference](#28-system-reference).
- **session environment** — [6. Identity, Sessions, RBAC, and Policy](#6-identity-sessions-rbac-and-policy), [9. Execution Backends](#9-execution-backends).
- **skills** — [14. Semantic Memory, Model Usage, Skills, and Add-ons](#14-semantic-memory-model-usage-skills-and-add-ons), [26. Engineering and Extension Guide](#26-engineering-and-extension-guide).
- **stand-down** — [20. Wakes, Leases, Context Rotation, and Stand-down](#20-wakes-leases-context-rotation-and-stand-down), [23. Ephemeral Delegation](#23-ephemeral-delegation).
- **systemd** — [4. System Topology, Services, and Ports](#4-system-topology-services-and-ports), [15. Admin Operations, Observability, Backup, and Recovery](#15-admin-operations-observability-backup-and-recovery), [28. System Reference](#28-system-reference).
## T

- **task board** — [17. Enabling and Operating the Team Add-on](#17-enabling-and-operating-the-team-add-on), [18. Organization, Projects, Tasks, and Cases](#18-organization-projects-tasks-and-cases).
- **Team Add-on** — [16. Team Add-on Boundary and Domain Model](#16-team-add-on-boundary-and-domain-model), [17. Enabling and Operating the Team Add-on](#17-enabling-and-operating-the-team-add-on).
- **Team commands** — [19. Commands, Identity, Revisions, and the Durable Outbox](#19-commands-identity-revisions-and-the-durable-outbox).
- **Team state database** — [16. Team Add-on Boundary and Domain Model](#16-team-add-on-boundary-and-domain-model), [25. Governance, Memory, Cost, and Internals](#25-governance-memory-cost-and-internals), [28. System Reference](#28-system-reference).
- **Team views** — [17. Enabling and Operating the Team Add-on](#17-enabling-and-operating-the-team-add-on), [28. System Reference](#28-system-reference).
- **telephony** — [13. Communications, Meetings, Calls, and Wakes](#13-communications-meetings-calls-and-wakes), [21. Chat, Agent Mail, Voice, and Escalation](#21-chat-agent-mail-voice-and-escalation).
- **tool classes** — [21. Chat, Agent Mail, Voice, and Escalation](#21-chat-agent-mail-voice-and-escalation), [23. Ephemeral Delegation](#23-ephemeral-delegation), [25. Governance, Memory, Cost, and Internals](#25-governance-memory-cost-and-internals), [30. Glossary](#30-glossary).
- **triggers** — [8. Workflow Runtime and Orchestration](#8-workflow-runtime-and-orchestration), [12. Issues and Provider Integrations](#12-issues-and-provider-integrations).
- **troubleshooting** — [29. Troubleshooting Decision Tables](#29-troubleshooting-decision-tables).
- **trust model** — [3. Trust Model and Security Boundaries](#3-trust-model-and-security-boundaries).
## V

- **verification evidence** — [22. Deliverables, Reviews, and Decision Cascades](#22-deliverables-reviews-and-decision-cascades), [24. Team Git Promotion and Release Gates](#24-team-git-promotion-and-release-gates).
- **VM overlays** — [9. Execution Backends](#9-execution-backends).
- **voice** — [13. Communications, Meetings, Calls, and Wakes](#13-communications-meetings-calls-and-wakes), [21. Chat, Agent Mail, Voice, and Escalation](#21-chat-agent-mail-voice-and-escalation).
## W

- **wake context** — [20. Wakes, Leases, Context Rotation, and Stand-down](#20-wakes-leases-context-rotation-and-stand-down), [25. Governance, Memory, Cost, and Internals](#25-governance-memory-cost-and-internals).
- **wakes** — [13. Communications, Meetings, Calls, and Wakes](#13-communications-meetings-calls-and-wakes), [20. Wakes, Leases, Context Rotation, and Stand-down](#20-wakes-leases-context-rotation-and-stand-down).
- **workflow packs** — [8. Workflow Runtime and Orchestration](#8-workflow-runtime-and-orchestration), [15. Admin Operations, Observability, Backup, and Recovery](#15-admin-operations-observability-backup-and-recovery).
- **workflow runtime** — [8. Workflow Runtime and Orchestration](#8-workflow-runtime-and-orchestration).
- **worktrees** — [11. Managed Git, Artifacts, and Code Consultations](#11-managed-git-artifacts-and-code-consultations), [24. Team Git Promotion and Release Gates](#24-team-git-promotion-and-release-gates).

## Source trail

- `docs/evander-user-manual/index.json` — canonical term-to-section mappings.
- `scripts/build_evander_user_manual.py` — target validation and index generation.
