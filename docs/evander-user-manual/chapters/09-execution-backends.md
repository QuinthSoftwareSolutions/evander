# 9. Execution Backends

> **At a glance.** Evander separates the decision to run an agent from the machinery that runs it. The supported default, `libvirt_vm`, gives a session a disposable virtual machine. The additive `container_worker_vm` backend sends narrowly described jobs to one configured worker VM, where Docker containers are disposable but selected session state is durable. Both paths share session, workflow, policy, relay, Git, and observability contracts; they do not offer identical isolation or lifecycle semantics.

## The mental model: a control plane and an execution plane

The host control plane decides *what* may happen. It owns the session manifest, effective directory configuration, tool policy, credentials, managed Git identity, workflow state, and audit trail. An execution backend decides *where and how* a permitted operation runs. This distinction is one of Evander's most useful design seams: a workflow can use portable `effect:agent:*` nodes without embedding `virsh`, Docker, host paths, or worker credentials in its graph.

![The Evander control plane dispatches the same portable agent lifecycle to either a libvirt VM or the authenticated container worker.](../../assets/evander-user-manual/diagrams/07-execution-backends.svg)

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
