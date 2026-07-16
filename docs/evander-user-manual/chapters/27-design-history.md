# 27. Design History and Compatibility Boundaries

> **At a glance.** This chapter is non-normative. It explains why current Evander has its present boundaries and identifies a few deliberate upgrade shims. For current commands and behavior, use the preceding chapters and the current source trail. A historical name, worker, file, or flow appearing here is not evidence that it remains supported.

## How to read this history

Evander grew from a small host gateway around disposable VMs into a directory-scoped control plane with provider-neutral workflows, a second execution backend, packaged add-ons, and managed Git. Most architectural changes followed the same pressure: replace an implicit shared resource with an explicit identity and owner.

| Earlier pressure | Current design response |
|---|---|
| One host path represented “the repo” | `managed_repo_id`, bare object store, controlled ref namespaces |
| One periodic loop owned unrelated work | dedicated trigger/effect, ingest, wake, proxy, and PR workers |
| One large Admin facade mediated every domain | explicit composition graph and focused services |
| JSON files coordinated concurrent leases/queues | selective SQLite stores and transactional claims |
| Team actions arrived as guest artifacts | Team MCP commands, idempotency, private SQLite state, outbox |
| A VM was the execution model | provider-neutral session plus `ExecutionBackend` |

![Evander's architecture evolves from a VM gateway through directory ownership and typed workflows to multiple execution backends, add-ons, focused services, and managed Git.](../../assets/evander-user-manual/diagrams/19-design-evolution.svg)

<p class="figure-caption">Figure 27.1 — Each major redesign made an implicit shared resource into an explicit, scoped identity or owner.</p>

## Timeline of the major seams

### March 20, 2026: the secure host boundary

The initial platform established the enduring security shape: host-side policy and audit, MCP gateway, OpenAI relay, firewall/service templates, disposable libvirt VMs, cloud-init, and Git-based code transport. Discord and NuGet support quickly exercised the same rule—providers and secrets live on the host; guests receive narrow endpoints.

This remains the foundation. Later abstractions did not turn guests into trusted hosts or add general internet access.

### March 21–30: work, polling, and directory ownership

Issue VM automation, provider email, ADO polling, and PR feedback made it clear that “one global configuration” was insufficient. Directory metadata, inherited configuration overlays, scoped issue identities, approvals, local/remote issue distinction, and Teams/Discord ingest followed.

The host heartbeat of this period was an intermediate consolidation: it moved periodic responsibility out of guests. It is no longer a current service. Today ADO PR feedback/completion, Discord wake, scheduled wake, provider ingest, and workflow polling have dedicated owners. Optional guest heartbeat assets remain disabled by default.

### April 3–20: workflows become typed programs

Trigger and effect models, registries, durable event queues, gates, queries, asynchronous completion, and the visual editor turned ad hoc automation into typed workflow graphs. Pack architecture, provider-owned schemas, runtime contexts, plugin loading, revisions, and live reload later separated stable orchestration semantics from provider implementation.

This history explains two current rules: node kinds must reflect read/write semantics, and editor catalog/schema work is part of adding a provider—not optional UI polish.

### April 21–30: repository and execution abstractions

Repository interfaces replaced broad file access, VM provider identity was separated from session semantics, and issue VM creation was split from repository setup. The `ExecutionBackend` abstraction then added `container_worker_vm` without removing `libvirt_vm`.

Container workers sharpened boundaries that VMs had previously made implicit: authenticated worker API, private session homes, relay and package-cache networking, explicit resource profiles, lifecycle reconciliation, and backend-neutral Admin/workflow operations.

### May: concurrent state moves selectively to SQLite

Teams/Discord inboxes, scheduled wakes, worker jobs, and orchestration adopted SQLite where concurrent claims, paging, WAL, or transactional history required it. Tool-call audit kept JSONL as canonical evidence and added SQLite only as a derived search index.

This was not a blanket “JSON is obsolete” migration. Small operator documents and registries still use atomic, locked JSON; queues and relational state use SQLite; append-only evidence uses JSONL. The owner repository determines the format.

### June 17–25: Team becomes an add-on with command semantics

Team Add-on moved its canonical state from `state.json` to relational `state.db` with a one-time import. Roles, leases, task/review/release records, artifact references, and activity required transactional invariants.

The clean MCP cutover then retired action-artifact providers. Agents report Team mutations through Team-owned MCP tools bound to dispatch permissions and idempotency. Asynchronous intent goes through the Team outbox and workflow events. Artifacts remain for deliverable files, not as a covert command bus.

### June 26–30: Evander identity and focused Admin composition

Operational naming moved to Evander: Python package, `evander-*` services, `EVANDER_*` variables, and guest `.evander`/`/etc/evander` paths. Installer/setup code retained narrow upgrade handling for earlier service, firewall, network, environment, and guest paths.

In parallel, Admin domains were extracted behind ports and focused services. The short-lived compatibility guidance around `AdminService` was superseded on June 30: the facade was removed, and Admin Web, gateway, CLI, providers, and tests now assemble through `build_admin_runtime` and `AdminRuntimeComponents`.

### July 2 onward: add-on workflow composition and Git work identity

Add-ons gained runtime workflow packs and Team gained server-owned Git work contexts. This made add-on enablement a runtime-registry concern: a pack cannot remain active when its providers will not load, and enablement requires the affected processes to restart.

### July 13: managed bare repositories and bounded consultations

Managed Git replaced issue-specific mirrors and persistent checkout routing with a deterministic ID, one directory-owned bare repository per canonical upstream, disjoint ref ownership, exact-SHA pinning, compare-and-swap promotion, lease-protected publication, and ephemeral hardened worktrees.

This was a breaking migration, not an indefinite dual read. The same commit-identity machinery enabled code consultations: a disposable read-only worker answers one bounded question against one pinned commit, then is cleaned up.

## Current compatibility surfaces

Compatibility is intentionally narrow. Treat these as migration bridges, not design examples.

| Surface | Current status | New work should use |
|---|---|---|
| Legacy gateway YAML | Import/migration input only | `runtime/host-config.json` plus directory config |
| Older guest session/config paths | Read/repair fallback in selected bootstrap paths | `/etc/evander/session.env`, `/home/agent/.evander`, provider-neutral guest config |
| `runtime/pr-map.json` | Compatibility association for older PR sessions | explicit PR workflow streams and managed repository identity |
| JSON wake/inbox/worker/Team stores | One-time import where the owning repository supports it | the current SQLite store |
| ADO-specific workflow node types | Supported compatibility surfaces | provider-neutral remote issue/PR nodes where semantics match |
| Old service/firewall/network names | Installer/setup cleanup only | `evander-*` services and current network/config names |
| Checked-in workflow pack examples | Examples/built-ins, not assumed live authority | revision-checked live packs through Admin Web/API |

An import is not dual authority. Once a SQLite repository has initialized and recorded its migration, editing the legacy JSON should not change live state. Keep the legacy file only as documented rollback evidence until finalization says it may be archived.

## Retired designs: do not revive them

- There is no current `evander-host-heartbeat` service. Use the dedicated worker that owns the event.
- Do not write Team commands as action artifacts. Use Team MCP commands and the outbox/runtime workflow.
- Do not address repositories by trusted-checkout or mirror paths. Register and pass `managed_repo_id`.
- Do not run agent-authored code in managed Git operation worktrees.
- Do not add new calls through `AdminService`; it no longer exists.
- Do not write live workflow pack files from a stale copy. Use lock/revision APIs.
- Do not make the gateway or MCP carry repository contents.
- Do not treat `audit.db`, semantic vectors, proxy caches, or UI payloads as canonical state.

## Why compatibility sometimes fails closed

Upgrade shims stop when continuing would hide ambiguity. Examples include:

- an issue number matches several directory/provider records;
- an old repository path cannot be mapped uniquely to a managed repository;
- active workers or Team leases make a Git migration unsafe;
- a live workflow references a provider that the configured add-on loader will not load;
- a legacy Team release lacks the exact pinned evidence required by current release gates;
- an existing semantic collection has a different vector schema.

The remedy is an explicit migrator or operator decision, not a guess. A fail-closed upgrade can be inconvenient, but it preserves the identity or evidence the newer invariant was introduced to protect.

## Upgrade archaeology runbook

When a deployment behaves as if an older design is still active:

1. **Identify the running revision.** Record package/checkout commit, service unit `ExecStart`, config path, and runtime path. Do not assume the shell checkout is what systemd serves.
2. **Read the relevant changelog window.** Find both the introduction and later retirement. A June compatibility note may have been superseded days later.
3. **Inspect current assembly.** Check `pyproject.toml` entry points, systemd templates/install script, `build_admin_runtime`, provider/add-on catalogs, and the owning repository constructor.
4. **Find migration markers and both candidate stores.** Determine which one current code opens before editing either.
5. **Run the supported preflight/dry-run.** For managed Git, use the `git-repos migrate` flow; for add-ons, use discovery/enablement diagnostics; for service rename, use installer preflight.
6. **Quiesce the writers named by the migrator.** Preserve backups and active lease/operation evidence.
7. **Apply once and verify through the current API.** Check IDs, counts, SHAs, revisions, and provider state—not merely that the new file exists.
8. **Finalize only when rollback artifacts are no longer needed.** Do not leave two writable authorities.
9. **Remove stale operational instructions.** Update local runbooks and automation so the next operator does not invoke a retired command.

## Source trail

- `CHANGELOG.md`
- `README.md`
- `docs/architecture.md`
- `docs/execution-backends.md`
- `docs/addons.md`
- `docs/repository-layer-refactor-note.md`
- `src/evander/admin/composition.py`
- `src/evander/workflow/plugins.py`
- `src/evander/execution/backend.py`
- `src/evander/messaging/wake_store.py`
- `src/evander/repositories/audit_index.py`
- `src/evander/addons.py`
- `src/evander/admin/services/managed_git_service.py`
- `src/evander_team_addon/repository.py`
- `scripts/install_systemd_units.sh`
- `tests/test_workflow_plugins.py`
- `tests/test_container_worker_execution_backend.py`
- `tests/test_addons.py`
- `tests/test_managed_git_migration_rewrite.py`
- `tests/test_team_addon_repository.py`
