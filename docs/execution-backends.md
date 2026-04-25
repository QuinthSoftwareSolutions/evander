# Execution Backends

The execution backend abstraction separates host control-plane workflows from the runtime that actually runs an agent target. Phase 1 is intentionally narrow: it introduces the contract and routes low-risk inspection and power-control operations through it while the existing VM services and scripts remain the source of truth.

## Backends

`libvirt_vm` is the supported backend. It wraps the current libvirt VM behavior and delegates to the existing admin VM service for status, start, shutdown, destroy, and guest log snapshots.

`container_worker_vm` is reserved for a future implementation. Selecting it in `GatewayConfig.execution_backend` currently fails with a clear `NotImplementedError`.

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

The default is `libvirt_vm`. This is additive and does not replace `vm_provider`; provider-specific lifecycle details still live behind the existing VM services in this phase.

## Migration Path

Next steps should move behavior only when the contract is stable:

- move standalone VM creation behind `prepare` and `start`
- have issue VM orchestration build an `ExecutionSpec`
- add `container_worker_vm` without changing orchestration and business logic callers
