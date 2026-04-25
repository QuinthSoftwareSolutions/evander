# Execution Backends

The execution backend abstraction separates host control-plane workflows from the runtime that actually runs an agent target. Phase 1 is intentionally narrow: it introduces the contract and routes low-risk inspection and power-control operations through it while the existing VM services and scripts remain the source of truth.

## Backends

`libvirt_vm` is the supported backend. It wraps the current libvirt VM behavior and delegates to the existing admin VM service for status, start, shutdown, destroy, and guest log snapshots.

`container_worker_vm` now has a worker-side HTTP daemon, `agent-sandbox-worker-daemon`, that can run jobs in disposable Docker containers while preserving durable per-session state on the worker VM. Selecting it in `GatewayConfig.execution_backend` is wired for early single-worker control-plane use when `container_worker_base_url` is configured. It is additive; `libvirt_vm` remains the default and is unchanged.

## Phase 1 Scope

The backend owns:

- single-target status inspection
- VM start
- VM shutdown
- VM destroy
- guest agent log snapshots

The backend does not yet own:

- VM creation
- issue VM orchestration
- repository clone/bootstrap
- Python/frontend/toolchain bootstrap
- agent resume and wake orchestration
- issue-worker and prompt-effect orchestration

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
```

This does not replace `vm_provider`; provider-specific lifecycle details still live behind the existing VM services in this phase.

For `container_worker_vm`, execution-defining inputs are first-class `ExecutionSpec` fields: `command`, `image`, `repo_source`, `clone_branch`, `resource_limits`, `persistence_mode`, and `workspace_mode`. `metadata` is reserved for sanitized caller context and is not used to smuggle the container command or clone settings.

## Migration Path

Next steps should move behavior only when the contract is stable:

- move standalone VM creation behind `prepare` and `start`
- have issue VM orchestration build an `ExecutionSpec`
- migrate issue-worker and prompt-effect orchestration to build container worker jobs without changing business logic callers
