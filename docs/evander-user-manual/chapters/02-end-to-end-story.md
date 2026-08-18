# 2. One Work Item, End to End

> **At a glance.** Imagine a piece of work passing through a well-run workshop. Evander notices the work, decides which prepared process applies, gives an agent a temporary workspace and only the permissions needed for that job, pauses when a person must approve something sensitive, and records exactly what was reviewed and released.
>
> This chapter follows one work item through that journey. You do not need to understand every service or record on the first read. The important idea is that each handoff is saved, limited, and traceable, so a stopped process can be resumed and a finished one can be explained.

## A representative mission

Northstar's `mission-control` project receives a work item: **add retry behavior to telemetry delivery**. An operator has prepared the route that this kind of work should follow. An implementation agent will make the change, a review agent will examine it, and a release agent will handle publication. The Team Add-on can coordinate those roles, but the journey in this chapter uses Evander's core safety and execution machinery.

That prepared route is called a **workflow**. A **provider** is the connector that lets Evander observe or act in another system, such as Azure DevOps. A workflow **pack** is a saved collection of workflow definitions, and the visual arrangement of steps and connections is often called a **graph**. These terms become precise later; for now, think *connector, reusable process, and process map*.

Evander does not ship one universal process for every organization. An operator chooses the connectors and workflow that match the local tools, review rules, and release practices. The Northstar story is therefore a realistic example, not a hidden default.

The simplest mental model is a relay race. Each stage hands the next stage a durable identifier—a saved reference to what happened—instead of a vague assertion that work happened:

```text
work item -> saved event -> workflow run -> agent session
          -> temporary workspace -> work and approvals
          -> exact code version -> review -> release -> cleanup
```

Those identifiers are what make the run recoverable and explainable.

![Evander system context from operator intent through the host control plane to isolated execution and external services](../../assets/evander-user-manual/diagrams/01-system-context.svg)

Put simply, Evander is the guarded coordinator; the agent is a temporary worker with limited access. The arrows show approved routes through the coordinator, not general access from the worker to the host or internet.

<p class="figure-caption">Figure 2.1 — The end-to-end path crosses explicit host boundaries; no arrow represents general guest access.</p>

## Before the work arrives: agree on the boundary

Before the first run, an administrator connects the source repository and any external work system, chooses the project settings, prepares the workflow, and decides which tools may require human approval. An ordinary operator does not need to memorize that wiring to follow a run; they need to know which project is selected and which prepared process is expected to handle the item.

The setup answers three plain questions before any temporary workspace is created:

1. **Whose work is this?** The selected directory identifies the organization and project.
2. **Which rules apply?** The directory determines the effective provider, workflow, security, and execution settings.
3. **What may the agent do?** The future session receives only the tools, code access, contacts, and limits chosen for this job.

> <span class="sidebar-kicker">For administrators</span> **What was prepared in advance**
>
> The repository was registered with Evander and assigned a `managed_repo_id`. Provider, policy, execution-backend, and automation settings were configured at `root` or along `root/northstar-platform/mission-control`. A workflow graph was selected for that directory. External credentials stayed in host secret storage, and the session's tools, argument constraints, communication targets, and approval behavior were chosen before dispatch.

## Stage 1: a new item becomes a saved event

Suppose the Azure DevOps connector notices the telemetry-retry work item. Evander first turns that observation into an **event**: a saved statement that something happened and may need a response. Saving the event separates the work from the connector that noticed it. If that connector restarts a moment later, the work does not disappear with it.

Evander saves the event before telling Azure DevOps that it has been received. If a crash happens at an awkward moment, the connector may offer the same item again. Stable identifiers make that redelivery **idempotent**[^idempotency]: Evander continues from the existing record instead of quietly losing the item or beginning unrelated duplicate work.

Once the event is safely stored, Evander wakes the part of the system that advances workflows.

> <span class="sidebar-kicker">Deeper detail</span> **Durable publication and redelivery**
>
> The trigger orchestrator polls enabled trigger definitions, resolves directory-scoped provider settings, and asks each provider for candidate events. A provider supplies a stable event identity, source identity and type, and payload; it may also supply an idempotency key. The queue deduplicates the event ID and, when present, the idempotency key. Only after durable publication does the worker call the provider's acknowledgement hook and advance its cursor.
>
> The event and execution store is SQLite at `runtime/orchestration.db`. This storage choice matters to administrators and recovery tooling, but it does not change the operator-level promise: acknowledgement follows durable publication.

## Stage 2: the workflow decides what should happen

The workflow is the prepared checklist and route for this kind of event. In Northstar's case it might ask:

1. Is this item for the `mission-control` project and in a state we handle?
2. Have we already started this exact request?
3. Is there a suitable active agent session?
4. If not, should Evander create one?

Evander calls these kinds of steps **gates**, **queries**, and **effects**. A gate makes a decision about the route, like a checkpoint. A query looks something up without changing it. An effect changes the world, such as creating a session or posting a message. The everyday example above is the concept; the technical names are useful when reading or editing the visual workflow.

Evander checks a workflow before making it active. If a later edit is invalid, the running services keep the last known good version and report the failed update. A bad edit therefore does not silently replace a working process.

> <span class="sidebar-kicker">Deeper detail</span> **What “compiled workflow” means**
>
> A workflow graph is compiled into validated nodes and edges. A realistic path may use `gate:filter`, `gate:dedupe`, `query:session:list`, and `effect:agent:create`. Queries return data to later nodes; gates can branch, delay, iterate, merge, serialize, or reject flow; effects perform state changes. References such as `${{node.output}}` are checked against upstream outputs rather than executed as arbitrary host code.
>
> Compilation rejects missing endpoints, duplicate node IDs, invalid provider settings, unsafe cycles, and broken bindings. A failed hot reload leaves the previous compiled graph in service.

## Stage 3: give the agent a temporary, limited job

When the workflow decides that implementation should begin, Evander creates a **session**. The session is both a job identity and a temporary permission card. It says which project the job belongs to, when the permission expires, which tools and resources the agent may use, and how later records should be connected back to this work item.

The session does not contain the host's general credentials. Those stay with Evander. Knowing an old session ID is also not enough to keep working after it expires.

Evander then starts a **sandbox**[^sandbox]: a temporary, isolated work environment. Depending on the installation, this may be a disposable virtual machine or a disposable container running on a dedicated worker machine. To an operator, both mean the same thing at this point in the story: the implementation agent gets a bounded place to work, while the host keeps control of policy and secrets. Chapter 9 explains the infrastructure differences.

> <span class="sidebar-kicker">Technical sidebar</span> **What the session manifest records**
>
> The manifest contains a unique session ID and expiry; the directory `root/northstar-platform/mission-control`; the selected model and VM or worker binding where applicable; visible and approval-required MCP tools; allowlists for contacts, scripts, channels, mailboxes, files, and search; argument and quota rules; and metadata linking the session to the workflow run, source item, agent role, and intended Git operation.
>
> With `libvirt_vm`, the environment is normally a disposable VM overlay. With `container_worker_vm`, the control plane asks an authenticated worker daemon to prepare session directories and run disposable Docker jobs.

## Stage 4: give the agent controlled access to the code

Evander gives the implementation agent access through a registered repository rather than exposing an arbitrary folder on the host. The agent clones the code into its own workspace and, if explicitly allowed, pushes changes only through a restricted Git route and an agent-specific branch area.

The key safety idea is simple: Evander records exactly which version was produced, reviewed, and released. Git calls that unique version identifier a **commit SHA** (usually shortened here to **SHA**). A branch name is a movable label; a SHA identifies one exact snapshot of the code.

This distinction prevents a subtle but serious mistake: approving one version and later publishing a different version merely because the same branch name moved.

> <span class="sidebar-kicker">Deeper detail</span> **The managed-Git handoff**
>
> The workflow passes a `managed_repo_id`, not a trusted host checkout path, to the clone effect. The managed-Git service resolves the host-owned bare repository and provisions scoped SSH access. The supported sequence separates the agent's work branch, the exact SHA produced by that session, the workflow decision that pins and promotes that SHA, and the later push or pull-request effect protected by an explicit lease. Source code moves with Git, not inside MCP messages.

## Stage 5: let the agent use only the services it needs

Inside the sandbox, the implementation agent sees familiar development tools. What is different is the route outward. Evander provides a few controlled doors for model requests, approved tools, packages, code, and explicitly allowed test systems. General internet access is not the default, and the host's model or provider credentials are not copied into the workspace.

Every tool request is checked against this job's permissions. A reachable service does not automatically give the agent permission to use every tool, contact, repository, or destination behind it.

> <span class="sidebar-kicker">Technical sidebar</span> **The controlled doors**
>
> Model requests use the OpenAI-compatible host relay. MCP tool calls use the gateway at `/mcp`. Package traffic may use the NuGet relay, npm-audit facade, PyPI/npm cache, or apt cache. Restricted Git uses SSH. An allowlisted external environment uses the session-scoped HTTP target relay.
>
> The HTTP relay constrains the destination but does not inject a host credential; it forwards authentication supplied by the guest. For MCP, a proof token binds the transport to the session, `tools/list` is filtered, and each invocation is checked against directory policy, session grants, argument rules, approval state, and any workflow lease.

## Stage 6: pause safely for human approval

Suppose the implementation agent requests a sensitive tool action. Evander does not perform it immediately. It creates a pending approval that tells an authorized operator which job is asking, which tool it wants to use, what the action is expected to do, when the request expires, and a safe-to-display summary of the requested input.

If the operator approves, the agent retries that same action using the approval request ID. Approval applies to the bounded request; it is not a general promotion to greater power. The session must still be active, the tool must still be allowed, and the input must still satisfy policy.

> <span class="sidebar-kicker">Deeper detail</span> **Why the agent retries the exact request**
>
> The durable request stores a digest of the original payload, a redacted review payload, a human summary, session and directory identity, tool and intended effect, expiry, and grant settings. Under the default exact-payload mode, changing the payload, session, or tool invalidates the approval. Expired, denied, or exhausted grants are rejected, and use consumption is atomic.
>
> Approval requirements are configurable, but the built-in destructive tools `delete_ado_backlog_item` and `semantic_delete_points` always require one-use, exact-payload approval. Chapter 7 is the full protocol reference.

## Stage 7: complete, review, and publish the exact result

When the implementation agent finishes, Evander records the result and advances the prepared process. It can collect artifacts, update the work item, wait for the Git push, open a pull request, and route feedback back to the same job.

The review agent examines the exact commit identified by the workflow—not merely whatever happens to be at the tip of a branch later. If changes are requested, Evander can start a bounded follow-up while keeping the earlier attempt visible. Once review and release checks pass, the release path receives that approved SHA and the limited authority needed to publish it.

The Team Add-on adds the organizational story around these core controls: a team manager, a project lead, a task owner, an assigned reviewer, a release authority, Agent Mail, wake scheduling, and governance. It coordinates *who should do what*; core Evander still owns the session, workflow, policy, execution, approval, and controlled Git paths.

> <span class="sidebar-kicker">Technical sidebar</span> **How completion re-enters the workflow**
>
> A completed prompt publishes structured output as another event. Later nodes can inspect session context, copy artifacts, update issue state, wait for Git evidence, enqueue a follow-up prompt, pin and promote the pushed SHA, create a pull request, or route review feedback. These transitions remain journalled under the same trace and workflow revision.

## Stage 8: keep the evidence and remove the sandbox

After the work finishes, Evander can remove the sandbox without erasing the job's **provenance**[^provenance]. The host retains the session, workflow decisions, approvals, tool-call history, exact code version, review result, release evidence, and cleanup outcome according to its retention policy.

Removing compute and removing evidence are different actions. If cleanup fails, the failure remains visible until an operator or automated recovery process cleans up the leaked resource through a follow-up action.

> <span class="sidebar-kicker">Technical sidebar</span> **The retained records**
>
> Workflow node executions retain event, trace, node, type, status, configuration hash and revision, inputs, outputs, errors, and binding trace. MCP calls produce append-only JSONL audit events with a queryable SQLite index. Approval decisions and uses remain linked by request ID. Session and backend records retain the selected execution target and cleanup result.

## What should always be true

The full system is easier to trust when its promises are stated plainly:

- Evander decides which project and rules apply before it creates the job.
- It saves observed work before telling the external system that the work was received.
- A session ID, a network connection, a visible tool, and an approval are separate checks; none grants all the others.
- The agent receives controlled routes to useful services, not the host's general network access or credentials.
- Repeated delivery or a restarted worker should converge on the existing saved work rather than quietly lose it.
- The exact code version—not a movable branch label—is the evidence used for review and release.
- The sandbox may disappear; enough host-side evidence remains to explain what happened.

## Troubleshooting

Start with what an operator can observe, then ask an administrator for the deeper store or service evidence only when needed.

| What you see | First place to look | What it usually means |
|---|---|---|
| The work item never appears | Provider status and trigger history | The external connection, filter, project settings, or polling may be wrong. |
| The item appears but does not advance | Workflow run and event history | No route matched, a checkpoint rejected it, or a workflow step failed. |
| The agent never starts | Session and execution details | Capacity, the selected environment, or its configuration failed. |
| The code cannot be cloned | Repository and session authorization details | The repository, session scope, key, or allowed branch is wrong. |
| An expected tool is missing | Session permission explanation | The tool was not granted, was denied by project policy, or lacks required context. |
| A tool reports that approval is required | Approvals and tool-call audit | This is a controlled pause, not a gateway outage. |
| The agent finished but nothing follows | Workflow run after the completion step | Completion may still be publishing, or the process has no next route. |
| Review appears to target the wrong code | Reviewed SHA and promotion/release record | A movable branch label may have been used instead of the pinned version. |
| The sandbox remains | Session cleanup status | Cleanup is incomplete; preserve the evidence and follow the supported recovery path. |

> <span class="sidebar-kicker">For administrators</span> **Authoritative evidence behind the table**
>
> The corresponding implementation records are the provider cursor and trigger logs; orchestration queue and node-execution journal; backend effect execution; managed-repository registry and Git authorization log; session manifest and visibility explanation; approval store and append-only tool audit; completion event; pinned-SHA promotion record; and backend cleanup record.

## A practical operator handoff

In Admin Web, select the affected directory first. Capture the source work-item ID, workflow run or trace ID, session ID, any approval request ID, and the exact commit SHA if code was produced. Those few identifiers usually let an administrator or support engineer trace the same run across the detailed records.

Do not delete queue, journal, approval, or session files to “unstick” a run. First establish whether it is waiting, running, waiting for completion, failed, skipped, exhausted, expired, or already completed. Use the supported Admin Web or CLI action after that diagnosis.

> <span class="sidebar-kicker">Administrator runbook</span> **Host-level checks**
>
> Start with broad status and then follow identifiers rather than timestamps alone:
>
> ```bash
> evander-cli status --runtime-dir runtime --json
> evander-cli list-sessions --runtime-dir runtime --all
> evander-cli list-approvals --runtime-dir runtime
> systemctl status evander-trigger-orchestrator.service
> systemctl status evander-effect-orchestrator.service
> ```
>
> For a deep support handoff, add the directory path, configuration revision, backend execution or VM ID, managed repository ID, cleanup result, and any relevant node-execution IDs. Run bootstrap-aware commands against the intended runtime and back up an unfamiliar runtime first.

[^idempotency]: Idempotency means that repeating the same operation has the same intended effect as performing it once. Here, redelivery returns to the saved event and run instead of starting unrelated work.

[^sandbox]: In this manual, a sandbox is the bounded execution environment used for one session—a disposable VM or a container on a worker VM—not an unrestricted host workspace.

[^provenance]: Provenance is evidence of a result's origin and history: the event, session, code version, decisions, and actions that produced it.

## Source trail

- `src/evander/daemons/trigger_orchestrator.py` — trigger polling, durable publication, acknowledgement, cursors, and worker wakeup.
- `src/evander/daemons/effect_orchestrator.py` — graph routing, idempotent execution, completion events, journals, and recovery behavior.
- `src/evander/models.py`, `src/evander/policy.py`, and `src/evander/gateway/` — session capability, policy evaluation, MCP binding, approval, and audit mechanics.
- `src/evander/effects/providers/agent_create.py`, `agent_clone_repo.py`, `agent_prompt.py`, and `git_promote_agent_branch.py` — representative agent and Git lifecycle effects.
- `src/evander/admin/services/managed_git_service.py` and `docs/git-remotes.md` — managed repository, restricted SSH, pinning, promotion, and publication contracts.
- `config/workflow-packs/agent-container-worker.v1-example.json` — a non-default example composition containing the node families used in this story.
- `tests/test_trigger_orchestrator.py`, `tests/test_effect_orchestrator.py`, `tests/test_gateway.py`, and `tests/test_managed_git_workflow_contract.py` — focused end-to-end invariants.
