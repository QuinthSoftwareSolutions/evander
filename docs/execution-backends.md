# Execution Backends

The execution backend abstraction separates host control-plane workflows from the runtime that actually runs an agent target. Phase 1 is intentionally narrow: it introduces the contract and routes low-risk inspection and power-control operations through it while the existing VM services and scripts remain the source of truth.

## Backends

`libvirt_vm` is the default backend. It wraps the current libvirt VM behavior and delegates to the existing admin VM service for status, start, shutdown, destroy, and guest log snapshots.

`container_worker_vm` has a worker-side HTTP daemon, `agent-sandbox-worker-daemon`, that can run jobs in disposable Docker containers while preserving durable per-session state on the worker VM. In Phase 4 it remains early single-worker control-plane use and is exposed as an explicit manual/operator backend through `agent-sandbox-cli execution ...`. Phase 4 formalizes the worker container sandbox profile; it does not implement Phase 5 networking, AKS/Kubernetes, worker lifecycle rebuilds, ADO/wake automation, or secret transport. It is additive; `libvirt_vm` remains the default and is unchanged.

## Phase 4 Scope

The backend owns:

- single-target status inspection
- VM start
- VM shutdown
- VM destroy
- guest agent log snapshots
- manual container job start/list/smoke operations through the execution CLI

The backend does not yet own:

- VM creation
- issue VM orchestration
- repository clone/bootstrap
- Python/frontend/toolchain bootstrap
- agent resume and wake orchestration
- issue-worker and prompt-effect orchestration
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
container_worker_job_timeout_seconds: 1800
container_worker_guest_openai_base_url: "http://<control-plane-private-ip>:11434/v1"
container_worker_guest_gateway_url: "http://<control-plane-private-ip>:8000/mcp"
container_worker_guest_nuget_relay_base_url: "http://<control-plane-private-ip>:11666"
container_worker_guest_pypi_index_url: "http://<control-plane-private-ip>:8081/repository/pypi-all/simple"
container_worker_guest_npm_registry_url: "http://<control-plane-private-ip>:8081/repository/npm-all/"
```

This does not replace `vm_provider`; provider-specific lifecycle details still live behind the existing VM services in this phase.

For `container_worker_vm`, execution-defining inputs are first-class `ExecutionSpec` fields: `command`, `image`, `repo_source`, `clone_branch`, `resource_limits`, `persistence_mode`, and `workspace_mode`. `metadata` is reserved for sanitized caller context and is not used to smuggle the container command or clone settings.

The execution CLI never mutates the configured backend. `start-container-job` and `smoke-container-worker` explicitly construct the container backend from the loaded config. `status`, `logs`, `stop`, and `destroy` auto-use the container backend only when the target resolves in `container-worker-executions.json`, or when the operator passes `--backend container_worker_vm`.

Portable workflow nodes use `effect:agent:*` for session setup. On `libvirt_vm` they delegate to
the existing VM services. On `container_worker_vm`, `effect:agent:create` prepares a durable
session manifest and worker directories, `effect:agent:clone-repo` clones into `/workspace`,
`effect:agent:copy-files` writes under `/workspace` or `/home/agent`, `effect:agent:exec`
runs deterministic setup commands in a transient worker job, and `effect:agent:prompt`
starts a disposable worker job for Codex and removes the job after completion.

Container sessions are bootstrapped through mounted `/home/agent`, not image rebuilds. The host writes
Codex config, shell profile setup, and optional NuGet/.NET/Playwright environment into the durable
agent home. Toolchains are expected to come from worker-level read-only cache mounts, while writable
session cache state remains under `/home/agent`. MSSQL bootstrap is not implemented for
`container_worker_vm`; `mssql=true` and `mssql_init_root` fail explicitly.

## Migration Path

Next steps should move behavior only when the contract is stable:

- move standalone VM creation behind `prepare` and `start`
- have issue VM orchestration build an `ExecutionSpec`
- migrate issue-worker and prompt-effect orchestration to build container worker jobs without changing business logic callers
