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
