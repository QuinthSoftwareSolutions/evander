# 9. Execution Backends

> **At a glance.** Once Evander decides that an agent may work, it still needs a safe place for that work to happen. The usual choice is a temporary virtual machine; another supported choice runs temporary containers on a dedicated worker machine. This chapter compares those environments while keeping the more important promise in view: changing where the agent runs does not change who grants permission or where durable evidence belongs.

## Where the agent's work happens

Evander separates two responsibilities. The control plane decides what the job is allowed to do and keeps the durable record. The **execution backend** provides the temporary workspace in which the permitted work runs.

An installation currently has two choices:

| Workspace choice | What the agent receives | What remains outside it |
|---|---|---|
| Temporary virtual machine (`libvirt_vm`) | A disposable VM with its own disk and selected toolchains | Session policy, real credentials, workflow state, managed Git, and audit evidence remain host-owned. |
| Worker container (`container_worker_vm`) | A disposable Docker job on one configured worker VM, with selected session state kept between jobs | The control plane still owns permission, credentials, workflow state, Git identity, and durable evidence. |

To an operator, the shared promises matter more than the infrastructure names:

- the session and directory rules are chosen before the workspace starts;
- the agent uses the same controlled model, tool, package, HTTP, and Git routes in either kind of workspace;
- the exact code version and the work's evidence remain traceable after temporary compute is removed;
- selecting a different backend does not grant broader authority.

The two backends share session, workflow, policy, relay, Git, and observability contracts, but they do not have identical isolation or lifecycle semantics. The later reference sections explain those differences.

![The Evander control plane dispatches the same portable agent lifecycle to either a libvirt VM or the authenticated container worker.](../../assets/evander-user-manual/diagrams/07-execution-backends.svg)

The diagram's main point is that Evander can change the kind of temporary workspace without moving its policy, credentials, Git authority, or durable records into that workspace.

<p class="figure-caption">Figure 9.1 — Backend selection changes the execution mechanism, not the host-owned session, policy, relay, Git, or evidence contracts.</p>

## What an operator may notice

The workspace is only one stage in a run. These observations help distinguish an execution problem from an earlier workflow or later publication problem.

| What you see | What it usually tells you | Useful information to capture |
|---|---|---|
| A session exists, but no VM or worker job appears | Evander reached session creation, but backend preparation or startup may have failed. | Session ID, selected backend, execution or VM ID if shown, last status, and visible error |
| The workspace starts with the wrong image or missing tools | The recorded template/image selection or an optional bootstrap/cache may not match the job. | Session ID, backend, image or template shown, and the missing command or tool |
| A prompt never starts | The workspace may exist even though queue persistence or dispatch failed before the agent harness began. | Session ID, execution ID, prompt status, and time of the last change |
| The agent works, but model, tool, package, HTTP, or Git access fails | Compute is running; the relevant controlled connection or job permission needs inspection. | Exact error, requested capability, session ID, and whether the failure repeats |
| Cleanup reports `cleanup_failed` | Evander could not prove that every worker-owned resource was removed. The record is evidence, not clutter. | Session ID, execution ID, worker job ID if shown, and the cleanup error |

## A Northstar Platform run from assignment to cleanup

Suppose the project lead for Northstar Platform's `mission-control` project assigns an implementation agent the work item “add telemetry retry behavior.” The operator-visible journey is the same with either backend:

1. Evander selects the project directory, creates or reuses a session, and chooses the configured workspace type.
2. It prepares a VM or a worker session and records which backend and image or template were selected.
3. It gives the session controlled access to `mission-control` through the repository's managed identity, not through an arbitrary host path.
4. Setup and agent work run inside the temporary workspace. Model calls, Evander tools, packages, and Git continue through their controlled routes.
5. Evander records the result and logs. A container job may disappear at this point while its session evidence remains.
6. The workflow can promote only the exact Git commit received from the managed Git event. The workspace type does not change that publication rule.
7. Cleanup removes the disposable compute while host records remain available to explain the run and its cleanup outcome.

> <span class="sidebar-kicker">Deeper detail</span> **The same journey in backend terms**
>
> Effective provider configuration selects `libvirt_vm` unless the session or directory explicitly selects `container_worker_vm`. `effect:agent:create` emits `metric_agent_create_backend_path`; the VM path launches or associates a VM, while the container path prepares the worker session and records the selected image. `effect:agent:clone-repo` receives only `mission-control`'s `managed_repo_id`; the control plane resolves an internal, restricted SSH source and grants the implementation session read-only or session-scoped write access.
>
> Bootstrap and prompt effects then use the host model relay, MCP gateway, and package relays or mounted caches. For a container job, `runtime/container-worker-executions.db` maps Evander's execution/session identity to the worker job ID. Destruction removes disposable compute but retains host session and cleanup records long enough to explain what happened.
>
> This is the central invariant: *backend portability does not weaken provenance*. A worker job may be short lived, but the session, Git SHA, workflow run, dispatch, policy decision, and model-usage correlation remain host-owned.

## Reading backend failures

An operator can use the first column to describe the symptom. The remaining columns guide an administrator toward the relevant backend layer.

| Symptom | What it means | First checks |
|---|---|---|
| `unknown` for a VM | No matching VM summary or session-to-VM mapping was found. | Inspect the session manifest, `vm_id`, and `virsh list --all`. |
| Container backend construction fails | `container_worker_base_url` is missing or invalid. | Inspect effective directory provider configuration, not only root defaults. |
| Worker returns unauthorized/unreachable | Bearer token, URL, routing, service, or firewall is wrong. | Check the daemon unit, `/healthz`, token env name/value, and control-plane-to-worker reachability. |
| Prompt never starts | Queue persistence or worker dispatch failed before the agent harness started. | Find `metric_prompt_enqueue_failed`, then inspect the prompt store and worker session state. |
| Wrong image or toolchain | The session recorded a different image alias/ref, or the worker cache is incomplete. | Inspect session metadata and the worker image catalog; do not change the catalog and assume an existing session changed. |
| `cleanup_failed` | Worker deletion did not complete. | Preserve the record, inspect its cleanup error, retry after fixing reachability or worker state. |
| Model unavailable | The provider rejected the configured model before useful work. | Inspect sanitized wake/job failure metadata and the directory/session model override. |
| ChatGPT subscription turn says the account is not signed in | The session selected the worker's subscription account, but the broker has no usable login. | Ask a root owner to inspect the worker account; do not switch silently to a metered credential. |
| VM starts but guest tools are stale | Optional bootstrap state no longer matches the host. | Re-run the relevant idempotent bootstrap script. |

## Troubleshooting: what to hand to an administrator

Start in Admin Web and capture what is available:

1. Record the directory, run or trace ID, session ID, and last status.
2. Record the selected backend and any VM, execution, worker, or worker-job identity shown.
3. Copy the exact error and note the last successful stage—for example, “the session exists, but no workspace was created” or “the workspace runs, but model access fails.”
4. If cleanup is involved, preserve the `cleanup_failed` record and its error. Do not delete it to tidy the dashboard.
5. Give those details to an Evander administrator. Do not repeatedly create replacement sessions unless the workflow's recovery procedure calls for it.

The remainder of this chapter is implementation and administrator reference material.

## Administrator reference: control plane and execution plane

The host control plane decides *what* may happen. It owns the session manifest, effective directory configuration, tool policy, credentials, managed Git identity, workflow state, and audit trail. An execution backend decides *where and how* a permitted operation runs. This distinction is one of Evander's most useful design seams: a workflow can use portable `effect:agent:*` nodes without embedding `virsh`, Docker, host paths, or worker credentials in its graph.

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

## Administrator reference: the two current backends

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
| `effect:agent:prompt` | Start the configured agent harness in a disposable job, retain outcome/log evidence, then remove the job. |
| `effect:agent:destroy` | Delete worker jobs and session files while retaining host cleanup evidence. |

The selected container image comes from the directory-scoped `container_worker_images` alias map. `container_worker_default_image` is the fallback for older or unspecified sessions. Once an alias is resolved, the exact image reference is stored with the session and reused; a later catalog change does not silently change an existing agent's runtime. Claude sessions normally select an appropriate Claude runner image and `model_provider_mode: "anthropic"`.

Container sessions are configured by mounting their durable agent home, not by rebuilding an image. Codex or Claude configuration, shell profile, MCP routing, relay URLs, and managed skills are written into that home. Large toolchains and immutable caches belong on worker-level read-only mounts; mutable caches remain session-scoped.

The default security posture is no general container network. A non-`none` Docker network mode is valid only when the worker deployment explicitly enables its egress policy and supplies an allowlist. The current backend does not provide worker scheduling, autoscaling, Kubernetes, general secret transport, or a general internet path. The worker bearer token stays on the control plane. Provider keys stay behind host relays.

An optional ChatGPT subscription account follows the same host-ownership principle but uses a
worker-local broker and restricted HTTPS proxy instead of putting the account or a provider key in
the container. It does not turn the container network into a general public route.

SQL Server is also intentionally separate. `effect:agent:create` rejects the VM-style `mssql=true` and `mssql_init_root` options for container sessions. Container agents use the worker-host SQL Server through the scoped MCP provisioning operations `mssql_provision_database`, `mssql_list_databases`, and `mssql_drop_database`.

### Codex metered API and ChatGPT subscription usage

Container-worker Codex sessions can choose between `metered_api` and `chatgpt_subscription`.
`container_worker_codex_auth_mode` supplies the directory-resolved default, while a session's
nullable `codex_auth_mode` is an explicit override. The setting applies only to Codex on
`container_worker_vm`; it is not a Claude default and subscription mode is rejected as an explicit
Claude override. Changing an existing session's selection takes effect on its next turn and keeps
the existing Codex thread history.

The subscription selection is effective only for a turn whose model provider mode is
`openai_public`. A local OpenAI-compatible or Azure OpenAI turn keeps using its configured private
endpoint and API-key route even when the stored session preference says `chatgpt_subscription`.
Evander does not rewrite that preference, so a later public-OpenAI turn can use it. Chapter 10
describes the network-routing distinction.

There is one signed-in ChatGPT account per worker, shared by every subscription-mode agent on that
worker. The worker auth broker is the sole durable writer of its `auth.json`, holds an exclusive
ownership lock, and serializes login, logout, account reads, and token refresh. Concurrent refresh
requests are coalesced. Each disposable job authenticates its own Codex app-server from an
in-memory external-token snapshot; it receives no account-file mount, token environment variable,
command-line token, or persisted account metadata. Public HTTPS goes only through the worker's
allowlisted CONNECT proxy, while the ordinary container egress policy remains default-deny.

This path fails closed. If the worker account is absent, logged out, cannot refresh, or the broker
cannot prove exclusive ownership, a subscription turn fails instead of falling back to metered API
use. Login start, cancellation, and logout are audited. Because the account is worker-global,
Admin Web account controls require an authenticated operator, an unlocked privileged-operations
session, and effective `admin.owner` permission at `root`; ownership of one child directory is not
enough.

## Administrator reference: persistence and cleanup

Stopping and destroying are intentionally different. Stopping a VM leaves its disk. Stopping a worker job leaves the durable session home. Destroying a VM removes the linked VM through the VM service. Destroying a container session asks the worker to remove jobs, files, Git keys, and session-owned databases, then retains the host session manifest and execution mapping as a cleanup tombstone.

Do not erase a `cleanup_failed` record to make a dashboard look tidy. It is the evidence needed to retry worker cleanup and to discover leaked files, jobs, keys, or databases. The container repository accepts the old JSON filename as configuration and one-time import input, but current live mappings are SQLite-backed.

Chat Assistant mappings are also backend-aware. Libvirt uses one global assistant VM/session with distinct per-user agent thread IDs. Container execution uses a distinct assistant session per user. The durable mapping in `runtime/chat-assistant-session-mappings.json` makes concurrent “open chat” and “first message” initialization converge instead of creating duplicates.

## Administrator runbook

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

For subscription-mode Codex, a root owner should also open **Agents → Worker ChatGPT
subscription**, verify the worker account status, and complete device login when required. Verify
that worker egress policy allows only the configured subscription proxy and that the proxy's
OpenAI/ChatGPT hostname allowlist is current. Do not copy the broker's account file into a job for
diagnosis; a missing login is meant to fail closed.

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
- `src/evander/codex_auth.py`, `src/evander/worker/codex_auth_broker.py`,
  `src/evander/worker/codex_subscription.py`, and `src/evander/worker/codex_egress_proxy.py` —
  effective auth selection, single-writer account ownership, external job authentication, and
  restricted subscription traffic.
- `src/evander/admin/web/codex_auth.py` — root-owner account-management boundary and audit events.
- `src/evander/effects/providers/agent_create.py`, `agent_clone_repo.py`, `agent_prompt.py`, and `agent_destroy.py` — portable agent lifecycle dispatch.
- `src/evander/worker/` and `ansible/worker.yml` — worker API, Docker runtime, durable store, sandbox policy, and deployment.
- `docs/execution-backends.md`, `docs/container-worker-backend.md`, and `docs/libvirt-vm-template-operations.md` — supported operator contracts.
- `tests/test_libvirt_execution_backend.py`, `tests/test_container_worker_execution_backend.py`,
  `tests/test_worker_daemon.py`, `tests/test_codex_auth_modes.py`,
  `tests/test_codex_auth_broker.py`, and `tests/test_vm_to_container_migration.py` — backend,
  subscription-auth, and migration behavior.
