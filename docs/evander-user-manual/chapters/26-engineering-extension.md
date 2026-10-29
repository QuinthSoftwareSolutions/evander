# 26. Engineering and Extension Guide

> **At a glance.** Not every new requirement calls for changing Evander's core. Many can be expressed as configuration, a workflow, a provider plugin, or a self-contained add-on. This chapter helps an engineer choose the smallest extension point that fits, so new capability arrives without bypassing the security, recovery, and audit behavior the rest of the system relies on.
>
> This is an engineering chapter, so it keeps exact contracts and implementation checklists. Read **Choose the right seam** and **Seven rules every extension must preserve** first. Then go directly to the section for the extension you are building, and use the testing and troubleshooting sections as the completion gate.

## Choose the right seam

Before writing code, ask whether the requirement changes **values**, **composition**, **a reusable integration**, **a product-sized domain**, **where isolated work runs**, or **Evander's own durable semantics**. Pick the first seam in the table that fully expresses the requirement.

| Requirement | Preferred seam |
|---|---|
| Change an endpoint, model, allowlist, threshold, or backend selection | Directory/host configuration |
| Compose existing triggers, gates, queries, and effects | Workflow pack |
| Add a reusable typed workflow node | Provider plugin |
| Add a cohesive UI/API/MCP/workflow feature with private state | Add-on |
| Add a new isolated execution mechanism | `ExecutionBackend` implementation plus deployment bundle |
| Change policy, lifecycle, identity, or durable core semantics | Focused core service/repository change |

Northstar Platform's telemetry-retry process, for example, needs no new core scheduler: a communication or issue trigger, existing Git/session effects, and Team workflow primitives are enough. A genuinely new telemetry SaaS might warrant a provider plugin; a full telemetry-management product with its own records and UI might warrant an add-on.

Three follow-up questions catch many over-sized designs:

1. Can existing typed nodes express the behavior as a workflow?
2. Does the feature need private durable state and its own UI/API surface, or only one reusable provider?
3. Would the proposal introduce a second owner for secrets, source code, identity, or durable truth?

If the third answer is yes, revise the boundary before implementation.

## Seven rules every extension must preserve

1. **Host paths and secrets stay private.** Public contracts use managed IDs, relative artifact paths, target IDs, and environment-variable names.
2. **Code moves with Git.** Do not add MCP payload transport for repositories.
3. **Directory ownership is part of identity.** Resolve effective config and authorize against the owning scope.
4. **Writes are explicit and replay-aware.** Define an idempotency key or compare-and-swap/lease boundary for every external mutation.
5. **Schemas reject ambiguity.** Typed models, forbidden extras where appropriate, stable normalized outputs, and explicit provider-only fields make failures reviewable.
6. **Durable truth has one owner.** Derived caches and indexes must be rebuildable or visibly stale.
7. **Cleanup is part of success.** Temporary credentials, refs, worktrees, containers, leases, and pending records need terminal cleanup and startup reconciliation.

These rules are not merely style conventions. They are compatibility with Evander's security, retry, and audit model. An extension that works on the happy path but bypasses one of them is incomplete.

## Extending the Admin runtime

`src/evander/admin/composition.py::build_admin_runtime` and `AdminRuntimeComponents` are the supported assembly seam. Domain behavior belongs in a focused service under `src/evander/admin/services/`; web routes should validate HTTP/auth concerns and delegate. CLI and gateway adapters should use the same service rather than reimplementing business rules.

Use this seam when the new behavior belongs to Evander's supported administration surface and should be shared consistently by Admin Web, CLI, or gateway adapters.

> <span class="sidebar-kicker">Engineering checklist</span> **A new Admin domain normally needs**
>
> - a dependency dataclass/port for the narrow collaborators it uses;
> - a service with plain, testable methods;
> - construction and exposure in `AdminRuntimeComponents`;
> - thin route and payload models;
> - RBAC/privileged-unlock/revision behavior for mutations;
> - focused service tests and route authorization tests.

Do not recreate the removed monolithic `AdminService` facade or rely on another service's private attributes. Compatibility adapters, when unavoidable, should be thin and scheduled for removal.

## Adding a workflow node

Workflow nodes are divided by semantics:

- **trigger** discovers or accepts an event;
- **gate** makes a deterministic routing/state decision;
- **query** reads without external mutation;
- **effect** performs a mutation or long-running action.

Putting a write in a query or a provider poll in an effect makes retry behavior impossible to reason about.

Start by classifying the node by what it does to the outside world. That decision determines its retry contract: triggers discover, gates decide, queries read, and effects mutate.

### Complete node contract

A node is not complete when only its Python provider runs. It must also be validatable, discoverable in the editor, bindable by later nodes, configurable at runtime, and testable under failure and replay.

> <span class="sidebar-kicker">Engineering checklist</span> **Complete production-node contract**
>
> A production node includes all of the following:
>
> 1. A namespaced node type such as `query:telemetry:deployment`.
> 2. A typed definition model and resolver from `WorkflowNodeEnvelope`.
> 3. A provider subclass of the appropriate base with declared node capability.
> 4. A stable output schema registered for expression binding.
> 5. Runtime-context use for directory config, secret resolution, services, and transport—never ambient guest state.
> 6. A workflow-editor catalog entry with useful labels, field schemas, defaults, and output documentation.
> 7. An internal event catalog entry only if the node introduces a stable event type.
> 8. Registration in built-ins or a `ProviderPluginRegistration`.
> 9. Validation, materialization, provider, registry, expression-binding, failure, and replay tests.
> 10. Documentation, configuration reference, and `CHANGELOG.md` entry.

`ProviderPluginRegistration` can contribute trigger, gate, effect, and query mappings; definition resolvers; editor entries; and internal event entries. The loader rejects duplicate catalog entries and catalog entries that do not match a provider of the declared kind. Module and `evander.providers` entry-point plugins are assembled at startup.

### Effect design checklist

An effect crosses the most important boundary because it changes something. Define its replay, uncertainty, approval, and cleanup behavior before writing the provider call.

> <span class="sidebar-kicker">Engineering checklist</span> **Before implementing an effect, write down**
>
> - canonical input and normalized output;
> - external idempotency key and what a retry observes;
> - timeout/cancellation behavior;
> - which state is persisted before and after the call;
> - how an uncertain provider acceptance is reconciled;
> - policy/approval and audit fields;
> - secret redaction;
> - terminal and abandoned-work cleanup.

Northstar Platform's “publish telemetry branch” effect should carry `managed_repo_id`, exact approved SHA, destination ref, and lease—not a host path and mutable branch tip.

## Building an add-on

Choose an add-on when the extension is a cohesive product surface with private state and several integration points—not merely one reusable workflow node.

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

An execution backend owns the full life of a temporary workspace. A successful start method is not enough; generic Evander workflows must be able to observe, stop, destroy, and reconcile the resource through the same abstraction.

> <span class="sidebar-kicker">Engineering checklist</span> **Complete <code>ExecutionBackend</code> protocol**
>
> Implement the complete `ExecutionBackend` protocol, not only “start”:
>
> - create/start/stop/destroy;
> - status, list, and logs;
> - command execution and file transfer where the contract requires them;
> - cancellation and terminal cleanup;
> - stable mapping from session/execution ID to backend resource;
> - normalized errors and bounded timeouts.

Register the backend in `ExecutionBackendRegistry`, expose explicit configuration, and ensure generic workflow effects continue to address `execution_backend` rather than backend-specific implementation names.

Threat-model the backend independently. The container worker, for example, needs authenticated control-plane calls, worker identity binding, no general egress, private runtime state, relay endpoints, restricted Git keys, package caches, resource limits, and startup reconciliation. A smoke test is not a substitute for lifecycle and isolation tests.

## Adding a host relay or service

A host relay or service changes deployment as well as application code. Decide who must reach it, which credential remains on the host, how calls are authenticated, and how operators will see its health before choosing its listener and firewall behavior.

> <span class="sidebar-kicker">Engineering checklist</span> **A new host service touches more than its Python module**
>
> Update:
>
> - a `pyproject.toml` entry point;
> - config model/example and secret-variable documentation;
> - systemd template and `scripts/install_systemd_units.sh` rendering/preflight;
> - firewall defaults only for ports guests or workers must reach;
> - bootstrap/session configuration if guests consume it;
> - runtime assembly and health/status reporting;
> - `README.md`, `docs/operations.md`, and `docs/configuration-reference.md`;
> - focused service, install-script, firewall, and documentation tests;
> - `CHANGELOG.md`.

Bind management-only services to loopback. If a guest-reachable port is needed, authenticate it with session-scoped capability and enforce the same policy server-side. Forward streaming response lifetime correctly and redact credentials from logs.

## Storage, revisions, and migrations

Choose storage based on concurrency and query needs. JSON repositories are suitable for small, low-contention records when atomic replacement/locking is explicit. SQLite is preferred for concurrent claims, leases, histories, and relational invariants. Enable foreign keys and define busy timeout/transaction boundaries consciously.

Use `evander.storage.write_json_atomically` for JSON file replacement. It writes a
unique temporary file beside the destination, flushes it, replaces the destination
atomically, and preserves existing permissions unless an explicit mode is supplied.
New files can specify their mode, and durable stores can request a parent-directory
fsync. Atomic replacement protects the write; it does not protect a read-modify-write
sequence. Use the store's mutation methods and stable sidecar file lock across that
whole sequence, rather than reading a snapshot and writing it back after another
process may have changed it.

For operator-edited documents, expose a deterministic revision and require `expected_revision`. For workflow packs, use the existing editor lock/revision path. For remote publication, use provider leases or compare-and-swap. These are the same concurrency principle at different layers.

A migration changes which representation is authoritative. State that transition explicitly and avoid leaving old and new stores as indefinite co-owners.

> <span class="sidebar-kicker">Engineering checklist</span> **A migration plan must specify**
>
> - old and new authority;
> - whether migration is breaking or dual-read;
> - exact preflight and blocking conditions;
> - idempotent apply and dry-run behavior;
> - rollback limit;
> - active-worker/session handling;
> - verification and cleanup of legacy state.

Avoid indefinite dual authority. The managed Git migration deliberately replaced path-based checkouts with IDs and bare repositories rather than teaching every future feature both models.

## Testing and verification

Test the narrow contract while developing, then run the complete Python suite before treating the extension as ready. Add failure, replay, authorization, cleanup, and deployment coverage in proportion to the seam you changed.

Run focused tests while developing, then the complete runner when validating the extension:

```bash
timeout --kill-after=5s 120s ./.venv/bin/pytest -q tests/test_workflow_plugins.py tests/test_workflow_validation.py
./.venv/bin/python scripts/run_tests.py
```

For a new node, include registry and runtime-contract tests plus provider-specific failure/retry tests. For a service, include install/preflight and docs assertions. For UI/browser tests, apply explicit command timeouts; stop a hung process and report its last useful output rather than leaving it running.

Every new test should protect an observable behavior against a realistic regression.
Prefer stable service, API, persistence, or rendered behavior over source-text and
incidental wording assertions. See [Testing](../../testing.md) for test-value rules,
the complete runner, and frontend verification.

Before committing in a dirty tree:

```bash
git status --short
git diff --check
git diff --cached --name-only
```

Preserve unrelated user changes. Each feature or bugfix branch includes exactly one
version bump and matching `CHANGELOG.md` entry: patch for fixes and maintenance,
minor for a new user-visible capability. Keep `pyproject.toml` and
`src/evander/__init__.py` synchronized and recheck the target branch's version before
merge. A major bump requires explicit direction.

## Troubleshooting an extension

First identify which boundary failed: registration, schema materialization, scoped configuration, provider transport, replay, cleanup, migration, or documentation/deployment. Then trace one stable identity through that boundary instead of changing several layers at once.

### Engineering runbook

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
