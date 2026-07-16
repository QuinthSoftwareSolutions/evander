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

![Evander system context from operator intent through the host control plane to isolated execution and external services](../../assets/evander-user-manual/diagrams/01-system-context.svg)

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
