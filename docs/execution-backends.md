# Execution Backends

The execution backend abstraction separates host control-plane workflows from the runtime that
actually runs an agent target. It started as a narrow lifecycle and log-inspection seam, and is now
also used by container-worker sessions and explicit operator container jobs. It is not a scheduler
or a replacement for the existing VM provider layer; provider-specific VM details still live behind
the existing VM services.

## Backends

`libvirt_vm` is the default backend. It wraps the current libvirt VM behavior and delegates to the existing admin VM service for status, start, shutdown, destroy, and guest log snapshots.

`container_worker_vm` has a worker-side HTTP daemon, `agent-sandbox-worker-daemon`, that can run
jobs in disposable Docker containers while preserving durable per-session state on the worker VM.
It is wired for one configured worker URL, the single-worker Phase 5 deployment slice, explicit
manual/operator use through `agent-sandbox-cli execution ...`, and backend-aware
`effect:agent:*` workflow nodes. It is additive; `libvirt_vm` remains the default and is
unchanged.

For that single-worker slice, non-`none` Docker networking is only valid when
`agent_sandbox_worker_egress_policy_enabled: true` is set on the worker VM and the allowlist is
explicit.

## Current Scope

The shared backend/operator surface owns:

- single-target status inspection
- VM start
- VM shutdown
- VM destroy
- guest agent log snapshots
- manual container job start/list/smoke operations through the execution CLI
- container-worker session create/clone/copy/exec/prompt/destroy support through
  backend-aware `effect:agent:*` workflow nodes

The current implementation does not provide:

- worker scheduling, autoscaling, AKS, or Kubernetes
- worker lifecycle rebuilds
- general secret transport to workers
- general internet egress or Docker-network-only metadata protection
- full ADO/wake automation on the manual container execution CLI surface
- ADO issue processing
- PR publishing

## Configuration

Backend selection lives on `GatewayConfig`:

```yaml
execution_backend: libvirt_vm
```

The default is `libvirt_vm`. `container_worker_vm` also requires a worker daemon URL and a bearer token environment variable on the host:

```yaml
execution_backend: container_worker_vm
container_worker_base_url: "http://10.0.2.10:11888"
container_worker_token_env: "AGENT_SANDBOX_WORKER_TOKEN"
container_worker_default_image: "agent-sandbox-runner:latest"
container_worker_images:
  default: "agent-sandbox-runner:latest"
  claude: "agent-sandbox-runner-claude:latest"
  dotnet-playwright: "agent-sandbox-runner-dotnet-playwright:2026-06-05"
container_worker_job_timeout_seconds: 1800
container_worker_guest_openai_base_url: "http://<control-plane-private-ip>:11434/v1"
container_worker_guest_anthropic_base_url: "http://<control-plane-private-ip>:11434/v1"
container_worker_guest_gateway_url: "http://<control-plane-private-ip>:8000/mcp"
container_worker_guest_http_target_relay_base_url: "http://<control-plane-private-ip>:11777"
container_worker_guest_nuget_relay_base_url: "http://<control-plane-private-ip>:11666"
container_worker_guest_pypi_index_url: "http://<control-plane-private-ip>:8081/repository/pypi-all/simple"
container_worker_guest_npm_registry_url: "http://<control-plane-private-ip>:8081/repository/npm-all/"
container_worker_mssql_enabled: true
container_worker_mssql_host: "172.17.0.1"
container_worker_mssql_port: 1433
```

`container_worker_default_image` is the fallback image for legacy sessions or for sessions that do
not select an approved alias. `container_worker_images` maps operator-safe aliases to exact image
refs and can be set at the root or directory `providers.json` layer. When `effect:agent:create`
selects one of those aliases, the resolved image is stored with the session and later
container-worker jobs for that session reuse the stored image instead of re-resolving a free-form
reference. Anthropic-backed Claude sessions should use `model_provider_mode: "anthropic"` together
with the `claude` image alias.

Those container-session PyPI/npm URLs are separate from worker VM OS provisioning. The worker
Ansible playbook configures apt to use the control-plane apt-cacher-ng relay on `3142` before
`host_prereqs` runs; Nexus remains only the PyPI/npm path.
The control plane also exposes `agent-sandbox-npm-audit-facade` on `11881` for npm audit POSTs;
it is separate from the worker registry setting and routes package metadata/tarballs through
Nexus while sending audit requests to public npm.

This does not replace `vm_provider`; provider-specific lifecycle details still live behind the existing VM services.

For `container_worker_vm`, execution-defining inputs are first-class `ExecutionSpec` fields: `command`, `image`, `repo_source`, `clone_branch`, `resource_limits`, `persistence_mode`, and `workspace_mode`. `metadata` is reserved for sanitized caller context and is not used to smuggle the container command or clone settings.

The execution CLI never mutates the configured backend. `start-container-job` and
`smoke-container-worker` explicitly construct the container backend from the loaded config.
`status`, `logs`, `stop`, and `destroy` auto-use the container backend only when the target
resolves in the container worker execution mapping store, or when the operator passes
`--backend container_worker_vm`. The mapping store is backed by
`runtime/container-worker-executions.db`; `runtime/container-worker-executions.json` is still
accepted as the configured path and as one-time legacy import input.

Portable workflow nodes use `effect:agent:*` for session setup. On `libvirt_vm` they delegate to
the existing VM services. On `container_worker_vm`, `effect:agent:create` prepares a durable
session manifest and worker directories, `effect:agent:clone-repo` clones into
`/home/agent/repos/<repo-name>`, `effect:agent:copy-files` writes under `/home/agent`,
`effect:agent:exec`
runs deterministic setup commands in a transient worker job, and `effect:agent:prompt`
starts a disposable worker job for Codex and removes the job after completion.
`effect:agent:destroy` is the cleanup counterpart: VM-backed sessions destroy the linked VM,
while container-backed sessions delete worker-side files and jobs but keep the host session
manifest and container execution mappings as cleanup tombstones. Admin Web > Sessions surfaces
the resulting container lifecycle state as `stopped`, `failed`, `abandoned`, `cleanup_failed`, or
`cleaned_up`, along with the last worker observation/completion signal and any cleanup error. A
cleanup failure must remain visible so operators can retry manual cleanup without losing the
session/run context in `runtime/sessions.json` or `runtime/container-worker-executions.db`.


Chat Assistant session resolution is backend-aware and durable. Admin Web prewarms
this resolution when a user opens or starts a chat session, then the hidden
`system.chat_assistant` workflow refreshes the same mapping when the first
message is posted. For `libvirt_vm`, assistant initialization reuses one shared
assistant VM/session globally while issuing distinct per-user `agent_thread_id`
values. For `container_worker_vm`, assistant initialization reuses a distinct
assistant session per user. The mapping is persisted in
`runtime/chat-assistant-session-mappings.json` and initialized idempotently so
concurrent session-start or first-message requests converge on the same mapping.

Container sessions are bootstrapped through mounted `/home/agent`, not image rebuilds. The host writes
Codex config, shell profile setup, and optional NuGet/.NET/Playwright environment into the durable
agent home. Toolchains are expected to come from worker-level read-only cache mounts, while writable
session cache state remains under `/home/agent`. MSSQL bootstrap is not implemented for
`effect:agent:create` on `container_worker_vm`; `mssql=true` and `mssql_init_root` fail explicitly.
Container-backed sessions use SQL Server through the separate MCP provisioning tools
`mssql_provision_database`, `mssql_list_databases`, and `mssql_drop_database`, which create
session-owned databases on the worker-host SQL Server instance and return scoped direct TDS
connection details for app/test commands.

## Remaining Migration Work

Next steps should move behavior only when the contract is stable:

- move standalone VM creation behind `prepare` and `start`
- have issue VM orchestration build an `ExecutionSpec`
- migrate issue-worker and prompt-effect orchestration to build container worker jobs without changing business logic callers
- add explicit worker-network and firewall controls before treating non-`none` Docker networking as
  a metadata-protection boundary


## Backend-specific Observability and Recovery

### `libvirt_vm`

- `effect:agent:create` emits `metric_agent_create_backend_path backend=libvirt_vm` before VM create delegation.
- Chat assistant session mappings are global and should show one `metric_chat_session_mapping_created` and steady `..._reused` markers.
- Recovery:
  - If backend selection is wrong, inspect session metadata and directory-level provider config resolution.
  - If chat session mapping churn appears, inspect and repair `runtime/chat-assistant-session-mappings.json`.

### `container_worker_vm`

- `effect:agent:create` emits `metric_agent_create_backend_path backend=container_worker_vm` before session/worker preparation.
- Chat assistant mappings are per user; expect `metric_chat_session_mapping_created` once per user and backend, then reuse.
- Prompt queue markers (`metric_prompt_enqueue_success|failed`) indicate queue persistence latency and failures before worker dispatch retries.
- Recovery:
  - On repeated enqueue failures, check runtime store writeability and worker-session state files.
  - On misrouted tool calls, use `metric_mcp_tool_decision` deny/error reasons and compare to session allowlists.
