# 27. Design History and Compatibility Boundaries

> **At a glance.** Evander began as a guarded host around disposable virtual machines, then acquired more kinds of work, more operators, another execution method, prepared workflows, optional domains, and stronger release evidence. Growth forced shared mechanisms to say clearly *whose work this is, who may act, and which record is current*.
>
> This chapter follows those turning points. It is non-normative—background, not instruction—but it explains why today's system insists on clear ownership, temporary job permissions, one current source of truth, and the exact code version that was reviewed. The precise technical names and migration consequences follow the plain story. Whenever history and the preceding technical chapters seem to disagree, the current implementation and its source trail win.

## How to read this history

This is an architectural reconstruction from the changelog, retained design notes, tests, and current contracts. It is not an incident report, and it does not assume that every redesign followed a production failure. In many cases, a second writer, backend, or use case simply made an ambiguity visible. The history does not by itself tell us whether that ambiguity ever caused a concrete incident.

An earlier design can therefore have been a sensible step and still be wrong to restore today. The host heartbeat, JSON coordination stores, action artifacts, repository paths, and the broad Admin facade each solved a real problem at one stage. They were retired or narrowed when the problem acquired more owners, more concurrency, or stronger evidence requirements.

For an operator, three lessons matter more than the dates:

1. **An old name can explain a leftover file without making that file current.** Confirm which service and store the running revision actually uses.
2. **A migration bridge is not a second source of truth.** Once current state has moved, changing the old representation should not be expected to change the system.
3. **Newer boundaries usually exist because the older shortcut became ambiguous or unsafe.** Work through the current API or service instead of restoring the shortcut.

> <span class="sidebar-kicker">Historical perspective</span> **An earlier design can be both correct and retired**
>
> A design is judged against the system that exists around it. One host loop is easy to understand when it has one periodic duty. One JSON document is practical when one owner edits a small record. A host path can be an adequate local reference while only one process uses it. The same choices become poor authorities when several workers claim jobs, several directories use the same upstream repository, or review and release must prove that they acted on one exact version.
>
> The lesson is not that the first design was foolish. It is that compatibility work must preserve the *meaning* of old state without preserving every old shortcut as a permanent operating model.

## The recurring design pattern

Evander grew from a host gateway around disposable VMs into a directory-scoped control plane with provider-neutral workflows, multiple execution environments, packaged add-ons, and managed Git. Across those changes, the same five-stage pattern appears repeatedly:

1. A small system uses one shared mechanism because its owner and meaning are obvious.
2. A second writer, directory, backend, or domain arrives.
3. Location, identity, permission, and durable authority begin to blur together.
4. Evander introduces an explicit ID, owner, transaction, lease, or interface.
5. The earlier surface becomes migration input, a narrow compatibility bridge, or a retired design.

Different parts of the system reached those stages at different times:

| Earlier pressure | Boundary that became explicit | What the boundary protects |
|---|---|---|
| One global configuration described all work | directory path, inherited overlay, and direct ownership | whose rules and records apply |
| One periodic loop owned unrelated duties | dedicated trigger/effect, ingest, wake, proxy, and PR workers | one accountable owner for each kind of work |
| A VM looked like the session itself | provider-neutral session plus `ExecutionBackend` | policy identity independent of compute mechanism |
| JSON files coordinated concurrent claims and histories | selective SQLite repositories and transactions | atomic claims, retries, paging, and relational invariants |
| One large Admin facade mediated every domain | explicit composition graph and focused services | visible dependencies and one owner per domain |
| Team mutations arrived as guest artifacts | Team MCP commands, idempotency, private SQLite state, and outbox | validated intent separate from result files |
| One host path represented “the repo” | `managed_repo_id`, owned ref namespaces, and operation worktrees | repository identity independent of storage location |
| A branch name represented reviewed code | pinned commit SHA, compare-and-swap, and publication lease | the exact content approved for release |

![Evander's architecture evolves from a guarded VM host through scoped workflows, pluggable execution and add-ons, focused ownership, durable proof, and managed Git.](../../assets/evander-user-manual/diagrams/19-design-evolution.svg)

The figure is cumulative, not a sequence of discarded products. The original guarded host boundary remains underneath the later layers. Most redesigns made ownership *inside* that boundary more precise.

<p class="figure-caption">Figure 27.1 — New capabilities repeatedly turn implicit location or authority into an explicit identity and owner.</p>

## What did not change

The history contains many replacements, but its central promises were unusually stable:

- Broad credentials and policy decisions remain host-owned rather than being copied into an agent workspace.
- Agent compute remains constrained and disposable; adding container workers did not make the host or worker generally accessible.
- A session remains the job and permission identity even when its execution environment changes.
- Git carries source code, MCP carries controlled operations, and artifact storage carries result files. Later designs made those roles sharper rather than merging them.
- Durable host-side evidence is expected to outlive temporary compute.
- Reachability is not authority. A visible service, a known session ID, an approval, and permission to perform one action remain separate checks.

These invariants are the thread to follow when an older path or service name appears. The implementation around the invariant may have changed; the security or evidence promise usually did not.

## Chronology at a glance

The detailed stories below overlap. This compact chronology preserves the order of the main seams before examining why they mattered.

| Period | What Evander learned to make explicit |
|---|---|
| March 20, 2026 | keep broad authority on the host |
| March 21–April 2 | give work an owner and a scope |
| April 3–20 | turn automation into durable, understandable workflows |
| April 21–May 4 | separate the job from the machine that runs it |
| May 19–31 | make approvals, results, and retries explainable after a restart |
| June 9–25 | add Team without creating a second control plane |
| June 22–July 4 | make people, services, and add-ons explicit actors and owners |
| July 2–14 | replace guessed identity and movable labels with proof and exact versions |

## From a guarded host to scoped ownership

### March 20: establish the boundary first

The initial platform established the security shape that all later work inherited: host-side policy and audit, an MCP gateway, an OpenAI relay, firewall and service templates, disposable libvirt VMs, cloud-init, and Git-based code transport. Discord and NuGet support quickly exercised the same rule—provider integrations and secrets live on the host; guests receive narrow endpoints.

This first version was small, but its separation of *control* from *execution* mattered. Later abstractions did not turn guests into trusted hosts or grant them general internet access. They supplied more precise ways to describe which host-controlled capability a particular job could use.

### March 21–April 2: automation needs an owner and a scope

Issue VM automation, provider email, Azure DevOps polling, pull-request feedback, Teams and Discord ingest, and scheduled wakes made “one global configuration” insufficient. Directory metadata, inherited configuration overlays, scoped issue identities, approvals, local-versus-remote issue records, and ownership queries followed.

Directory scope began as a way to place configuration and records. It gradually became an authority boundary: workers resolve the owning directory; workflow graphs and session queries stay in the selected scope; ambiguous issue identities are rejected instead of guessed; and authorization checks use the same ownership context. A directory is not merely a folder-like label. It answers which configuration lineage and owner apply to the work.

> <span class="sidebar-kicker">Design turning point</span> **The heartbeat changed owners twice**
>
> Early guest heartbeat behavior made the temporary worker responsible for noticing more work. Moving that responsibility to a host heartbeat improved secret ownership and allowed the host to wake idle sessions, reconcile state, and report status without trusting the guest.
>
> As periodic duties multiplied, however, one host loop became another ambiguous owner. Azure DevOps feedback and completion, provider ingest, Discord wake, scheduled wake, proxy freshness, and workflow polling acquired dedicated workers. There is no current `evander-host-heartbeat` service; optional guest heartbeat assets remain disabled by default.
>
> This is evolution rather than reversal: responsibility moved from an isolated worker to the host, then from a shared host loop to the specific host service that owns each duty.

## From scripts to a durable workflow language

### April 3–20: a graph becomes an executable contract

The first workflow model separated “something happened” from “change something.” It then gave decisions and read-only lookups their own visible steps. Saving events and results made a process resumable; validating its routes before activation made it understandable. Automation was becoming a durable program rather than a chain of scripts that happened to run in order.

Workflow packs were the next step. The shared runtime could own progress and recovery while optional providers supplied the behavior for a particular external system. Operators could edit a process without allowing an invalid draft to displace the last known good version.

Once a workflow could perform consequential external work, however, “the action succeeded” was no longer the whole story. Evander also had to know whether the result had been safely published for the next step. Completion and publication became separate, recoverable facts.

> <span class="sidebar-kicker">Deeper detail</span> **Why typed capabilities replaced arbitrary scripts**
>
> Triggers, effects, gates, and queries gave discovery, mutation, routing, and read-only observation distinct contracts. Durable event queues, provider registries, asynchronous completion, graph validation, trace IDs, and the visual editor turned those contracts into typed workflow programs. Provider-owned schemas and runtime contexts let domain packs use host services without ambient access. Revisions, atomic saves, and live reload kept the previous compiled graph active after an invalid edit.
>
> The later `completion_pending` state captured the difference between completion and publication: retry publishing the result of an already completed effect without rerunning the external mutation itself.
>
> This history explains two current engineering rules: a node's kind must match its read or write semantics, and editor catalog/schema work is part of adding a provider rather than optional UI polish.
>
> An arbitrary host-script node can do almost anything, which also means the workflow definition cannot state its real inputs, outputs, authority, retry behavior, or safe destinations. Typed pipeline, diagnostics, and Git effects progressively covered concrete needs with bounded schemas and policy. The legacy host-script workflow effect was then removed.
>
> The design preference is not “Python instead of shell.” Host installation and machine orchestration still use shell where appropriate. The boundary is that an operator-authored workflow invokes a named, validated capability rather than supplying arbitrary host code or paths.

## Separating the job from the machine

### April 21–May 4: repositories and execution become interfaces

Repository interfaces began progressively replacing high-value broad file access with domain operations for sessions, triggers, executions, review requests, wakes, and feedback. This was deliberately incremental; some compatibility-shaped repository methods and transitional callers remain. The important move was to give durable behavior an owning interface before changing every backing store.

At roughly the same time, VM-provider identity was separated from session semantics, and issue VM creation was split from repository preparation. The `ExecutionBackend` abstraction then added `container_worker_vm` without removing `libvirt_vm`, which remains the default.

Container workers made a distinction visible that one VM per job had allowed the system to blur: a **session** is the job's identity, policy, expiry, and evidence boundary; an **execution backend** supplies and manages the compute. Supported lifecycle and `effect:agent:*` operations became backend-aware, while backend-specific VM behavior and some issue/PR paths remained outside that portable surface.

The later guarded VM-to-container migration is useful proof of the separation. It preserved the session manifest and its model, tools, approvals, Team metadata, and thread history while changing only the backend identity fields and copying the durable session home. The session remained the same job identity even though the machine mechanism changed.

> <span class="sidebar-kicker">Deeper detail</span> **An abstraction is a promise with a boundary**
>
> `ExecutionBackend` does not claim that every libvirt operation works on every future environment. It defines the supported preparation, start, stop, inspection, destruction, and log seams that generic callers may rely on. Backend-aware `effect:agent:*` workflow operations build on that abstraction. Container-worker authentication, private session homes, relay networking, package caches, resource profiles, and reconciliation remain implementation responsibilities.
>
> This qualification matters during upgrades: “backend-aware” is not evidence that an older VM-only command has become portable.

## Durability follows the coordination problem

### May 19–31: successful actions become durable evidence

By May, workflows could accept external events, grant run-scoped tool leases, publish immutable artifacts, and link a run to sessions, approvals, tool calls, communications, and binding traces. Approval gained exact-payload retry checks and argument policies. Session expiry was enforced across resume, workflow, wake, queue, and relay paths rather than only in one UI action. These changes made a run explainable across restarts and across several cooperating services.

Artifacts also separated into clearer roles. A session's `/artifacts` directory is a mutable workspace. Staging and publication create an immutable host-owned result that workflows and operators can inspect or later import. Team eventually added hierarchical artifact spaces over those published keys. Not every file in `/artifacts` is immutable; publication is the transition that creates durable evidence.

### May 21–27: storage follows the coordination problem

Teams and Discord inboxes, scheduled wakes, worker jobs, and orchestration adopted SQLite where concurrent claims, paging, write-ahead logging, or transactional history required it. Tool-call audit deliberately kept append-only JSONL as canonical evidence and added SQLite only as a rebuildable search index.

> <span class="sidebar-kicker">Storage sidebar</span> **Storage follows coordination, not fashion**
>
> A small operator-owned document with one writer remains a good candidate for atomic, locked JSON. A queue in which several workers claim rows, renew leases, retry, page results, and preserve history needs transactions and indexes, so SQLite is a better fit. An append-only security record benefits from JSONL's simple canonical sequence, even when SQLite makes searching it faster.
>
> The wake and worker migrations used a temporary one-way JSON import while SQLite became the live authority. After the supported deployments were verified, those import paths were removed and the JSON sources were archived. The repository APIs remained stable while their persistence semantics changed.

This was never a blanket “JSON is obsolete” migration. The owner repository and concurrency contract determine the format.

## Optional domains without a second control plane

### June 9–25: Team grows behind the add-on boundary

The generic add-on boundary arrived with a strong architectural claim: opinionated product domains may extend Admin Web, workflow providers, MCP, and private storage without forcing their concepts into core session and workflow schemas. Add-ons are discovered separately from being enabled, store state beneath their own runtime area, and must not become alternate owners of core secrets, execution, or policy.

Team was the first large proof of that seam. On June 9 it introduced private organizational state, persistent agents, durable work and review, and Git-first delivery while core remained Team-agnostic. Over the following weeks, roles, governance, communication, leases, wake rules, and release recovery tested whether a rich add-on could remain private without becoming a second control plane.

On June 17, Team's canonical state moved from `state.json` to relational `state.db` with a one-time import. Roles, assignments, leases, task/review/release records, artifact references, and activity needed transactional invariants that were awkward to maintain as one document.

On June 25, the clean MCP cutover retired action-artifact providers. Agents began reporting mutations through Team-owned MCP tools bound to dispatch permissions and idempotency. The mutation, revision, activity entry, idempotency result, and outbox intent can be committed together. A separate publisher turns pending outbox rows into workflow events and records completion or retry.

> <span class="sidebar-kicker">Design turning point</span> **Artifacts are nouns; commands are verbs**
>
> A deliverable file is naturally an artifact: it can be published, reviewed, retained, and referred to by an immutable key. “Finish this task,” “request review,” or “change this assignment” is different. A command needs an authenticated actor, permission and lease checks, validation, an idempotency key, one transactional outcome, and a useful response if the caller repeats it after a timeout.
>
> Using artifacts for both blurred evidence and authority. Team MCP commands and the outbox separated the verb from its resulting evidence. Artifacts remain important for deliverables; they are no longer a covert command bus.

The distinction between add-on and core remained intact as more add-ons appeared. Bokio and Skatteverket proved that the same seam could host very different domain integrations without adding their concepts to the core gateway. Runtime workflow packs and later package discovery made enablement an operator-managed lifecycle: a pack cannot remain active when its provider implementation will not load, and affected processes must restart after an enablement change.

## Explicit people, focused services, and a persisted name

### June 22–23: the operator becomes an explicit actor

Multi-user local authentication made the signed-in person's identity and permissions part of each operation. Team chat became per operator rather than one global conversation. The broad Lock/Unlock control was eventually narrowed to a local UI read-only guard; backend authority still comes from authentication, RBAC, privileged-operation checks, directory scope, and revisions. A visual lock is useful protection against accidental clicks, but it is not an authorization boundary.

### June 26–30: naming becomes part of the upgrade

Operational naming was standardized on Evander: Python package, `evander-*` services, `EVANDER_*` variables, and guest `.evander` and `/etc/evander` paths. Installer and setup code retained narrow upgrade handling for earlier service, firewall, network, environment, and guest paths.

> <span class="sidebar-kicker">Compatibility sidebar</span> **A rename becomes a migration when the old name is durable**
>
> Changing a heading is cosmetic. Changing a systemd unit, environment-variable prefix, network name, package import, runtime path, guest profile hook, or stored workflow field changes how a running installation finds its state. The Evander rename therefore needed discovery, repair, cleanup, and compatibility logic—not merely search and replace.
>
> Older rollback artifacts can contain names from their era. Treat deployed configuration and current service paths as the authority for an upgrade.

### June 27–30: focused services expose domain ownership

In parallel, Admin domains were extracted behind protocol ports and focused services. The large `AdminService` facade had been useful as a shared entry point. The refactor treated construction and domain ownership as separate concerns: the composition root would assemble the graph, while focused services would own behavior.

The transition was unusually quick. June 27 guidance described `AdminService` as a compatibility facade while focused services and a composition builder were introduced. By June 30 that guidance had been superseded: the facade was removed, and Admin Web, gateway, CLI, providers, and tests assembled one explicit graph through `build_admin_runtime` and `AdminRuntimeComponents`.

The lesson is not that facades are inherently bad. A convenient entry point should simply not become the hidden owner of unrelated domains. The composition root may know how services fit together; each focused service should own its own behavior.

## From a session label to proof-bearing authority

### July 2–14: tighten every consequential identity

On July 2, per-session MCP proof tokens made an important distinction explicit: knowing a `session_id` is not proof that a caller represents that session. The token bound the MCP transport more tightly. Subsequent hardening scoped relay access, HTTP targets, Git repositories, branch namespaces, approval reads, and workflow effects to their actual directory and session authority.

This was defense maturation, not evidence of a documented breach. It applied the original rule—reachability is not permission—at more boundaries as the number of reachable services grew.

Git tells the same story over a longer arc. The first platform already used host-mediated cloning, restricted agent pushes, promotion, and pull-request creation. Private container Git, mirror serialization, deterministic Team namespaces, typed Git effects, server-owned work contexts, and pinned reviewed commits progressively removed ambient paths and movable labels from consequential operations.

On July 13, managed Git completed that arc. It replaced issue-specific mirrors and persistent-checkout routing with a deterministic ID and one directory-owned bare repository per canonical upstream. Guest, refresh, review, Team, and operation refs gained disjoint owners. Host workflows pin exact commits; compare-and-swap protects internal targets; explicit leases protect upstream publication; and unique detached worktrees are created for one operation, removed on normal completion or termination, and reconciled after abnormal termination. This was a breaking migration, not an indefinite dual read.

Where a repository happens to be stored is therefore no longer how the rest of the system identifies it. The path is private implementation detail; `managed_repo_id`, directory ownership, ref, and exact SHA carry the meaning.

The same commit-identity machinery enabled bounded code consultations: a disposable read-only worker answers one question against one pinned commit, submits a structured result with validated citations, and is cleaned up. July release work also froze candidate and review criteria, classified candidate failures separately from transient infrastructure failures, and retained immutable attempt lineage. A retry may repeat an eligible gate against the same pinned candidate; it may not quietly substitute different code.

> <span class="sidebar-kicker">Deeper detail</span> **Why exact identity keeps returning**
>
> Directory paths, session IDs, event IDs, idempotency keys, managed repository IDs, commit SHAs, workflow revisions, and approval payload digests identify different things. Architecturally, they answer the same question: *what exact object or decision does this later action refer to?*
>
> A stable ID alone is not always enough. The system may also need an owner, expected current value, lease, expiry, or proof token. That combination prevents a stale label, retry, or neighboring directory from silently changing the meaning of the action.

## Current compatibility surfaces

Compatibility is intentionally narrow. Treat these as migration bridges, not design examples.

If an operator encounters one of these names, the safest first step is to record where it appeared and which live Evander revision is running. Do not copy data from the older surface into the newer one until an administrator has identified the migration contract and current authority.

| Surface | Current status | New work should use |
|---|---|---|
| Older guest session/config paths | read/repair fallback in selected bootstrap paths | `/etc/evander/session.env`, `/home/agent/.agent/config.env`, and current `/home/agent/.evander` run-state paths |
| `runtime/pr-map.json` | live compatibility store for session-bound and legacy PR streams; repository-scoped streams can bypass it | explicit PR workflow streams and managed repository identity |
| JSON wake/inbox/worker/Team stores | one-time import where the owning repository supports it | the current SQLite store |
| ADO-specific workflow node types | supported compatibility surfaces, and still appropriate for ADO-only semantics | provider-neutral remote issue/PR nodes where semantics match |
| Historical Team action artifacts and unpinned terminal releases | retained evidence; incomplete unpinned releases do not satisfy current gates | Team MCP commands and exact pinned release evidence |
| `AdminService` references | historical design or short-lived compatibility guidance | focused services assembled by `build_admin_runtime` |
| Legacy managed-Git path fields | unsupported historical residue, not live routing authority | `managed_repo_id`, owned ref, and exact SHA |
| Old service/firewall/network names | installer/setup cleanup only | `evander-*` services and current network/config names |
| Checked-in workflow pack examples | examples or built-ins, not assumed live authority | revision-checked live packs through Admin Web/API |

> <span class="sidebar-kicker">Migration model</span> **Compatibility should have a direction**
>
> A healthy migration flows one way:
>
> ```text
> old authority -> inventory/import/repair -> current authority -> verification -> finalization
> ```
>
> A bridge may read an old record long enough to import or repair it. It should not leave both ends accepting ordinary writes indefinitely. Two writable representations invite dual authority: each can be internally valid while disagreeing about the real state.

Once a SQLite repository is live authority, editing a retired JSON file must not change live state. Archive it with a manifest after finalization; do not leave an executable import path as rollback machinery.

## Retired designs: do not revive them

| Retired shortcut | Ambiguity or risk | Current owner or surface |
|---|---|---|
| `evander-host-heartbeat` or guest heartbeat as general scheduler | one loop appears to own unrelated provider and wake duties | dedicated PR, ingest, wake, proxy, trigger, and effect workers |
| Team commands written as action artifacts | a file cannot cleanly prove actor, permission, idempotency, and transactional outcome | Team MCP commands plus Team outbox/runtime workflow |
| repositories addressed by trusted-checkout or mirror paths | location is mistaken for identity and directory ownership | registered `managed_repo_id` |
| agent-authored code run in managed-Git operation worktrees | host scratch space is mistaken for an isolation boundary | disposable VM/container execution backend |
| new calls routed through `AdminService` | composition entry point appears to own unrelated domains | focused service from `AdminRuntimeComponents` |
| live workflow packs edited from a stale file copy | concurrent operator changes can be overwritten | lock and revision APIs |
| repository contents carried through gateway or MCP | control messages become an unbounded code transport | Git clone/fetch/push through managed Git |
| `audit.db`, semantic vectors, proxy caches, or UI payloads treated as canonical | a rebuildable or partial projection becomes a second truth | the owning canonical log, repository, provider, or record |

## Why compatibility sometimes fails closed

Upgrade shims stop when continuing would hide ambiguity. The refusal protects a specific invariant:

| Ambiguity | Why guessing is unsafe | Invariant protected |
|---|---|---|
| an issue number matches several directory/provider records | the wrong project could receive the operation | directory and provider identity |
| an old repository path cannot map uniquely to a managed repository | refs or credentials could cross ownership boundaries | repository ownership |
| active workers or Team leases make a Git migration unsafe | old and new writers could mutate the same work | exclusive writer and lease ownership |
| a workflow references a provider the add-on loader will not load | the graph would advertise an implementation that is absent | pack/provider consistency |
| a legacy nonterminal Team release lacks exact pinned evidence | release could proceed without proving the reviewed content | exact candidate identity |
| an existing semantic collection has a different vector schema | results from incompatible representations could be mixed | semantic result correctness |

The remedy is an explicit migrator or operator decision, not a guess. A fail-closed upgrade can be inconvenient, but it preserves the identity or evidence the newer invariant was introduced to protect. Historical terminal records may remain readable even when an incomplete old operation must be rejected and recreated under the current contract.

## Questions this history teaches us to ask

When an unfamiliar compatibility surface appears, six questions usually reveal its role:

1. What is the stable identity: a path, or an explicit ID and owner?
2. Which component is allowed to write the canonical state?
3. Is this record canonical, derived, cached, or migration input?
4. Which resource is intentionally temporary, and which evidence must survive it?
5. What value must be pinned exactly before review, retry, or publication?
6. How does the migration end, and what prevents both designs remaining writable?

These are reading questions, not a demand that an operator solve the architecture. They help distinguish a stale instruction from a supported recovery path before anything is changed.

## Troubleshooting and operator handoff

When a screen, log, or local instruction refers to an older design, record the exact name, path, command, or error together with the affected directory and time. Also capture the running version shown by the installation, if available. An operator does not need to decide which historical store is authoritative before asking for help.

Avoid “trying both” old and new commands, enabling a retired service, or editing both candidate stores. Those actions can create the dual authority that the migration was designed to remove.

## Administrator runbook: upgrade archaeology

When a deployment behaves as if an older design is still active:

1. **Identify the running revision.** Record package or checkout commit, service unit `ExecStart`, config path, and runtime path. Do not assume the shell checkout is what systemd serves.
2. **Read the relevant changelog window.** Find both the introduction and later retirement. A June compatibility note may have been superseded days later.
3. **Inspect current assembly.** Check `pyproject.toml` entry points, systemd templates and install script, `build_admin_runtime`, provider/add-on catalogs, and the owning repository constructor.
4. **Find migration markers and both candidate stores.** Determine which one current code opens before editing either.
5. **Run the supported preflight or diagnostic.** For add-ons, use discovery and enablement diagnostics; for a service rename, use installer preflight.
6. **Quiesce the writers named by the migrator.** Preserve backups and active lease or operation evidence.
7. **Apply once and verify through the current API.** Check IDs, counts, SHAs, revisions, and provider state—not merely that the new file exists.
8. **Finalize only when rollback artifacts are no longer needed.** Do not leave two writable authorities.
9. **Remove stale operational instructions.** Update local runbooks and automation so the next operator does not invoke a retired command.

## Source trail

- `CHANGELOG.md`
- `README.md`
- `docs/architecture.md`
- `docs/execution-backends.md`
- `docs/addons.md`
- `docs/git-remotes.md`
- `docs/repository-layer-refactor-note.md`
- `src/evander/admin/composition.py`
- `src/evander/workflow/plugins.py`
- `src/evander/execution/backend.py`
- `src/evander/repositories/session_manifests.py`
- `src/evander/messaging/wake_store.py`
- `src/evander/repositories/audit_index.py`
- `src/evander/addons.py`
- `src/evander/admin/services/managed_git_service.py`
- `src/evander_team_addon/repository.py`
- `src/evander_team_addon/sqlite_store.py`
- `src/evander_team_addon/services/commands/executor.py`
- `scripts/install_systemd_units.sh`
- `tests/test_workflow_plugins.py`
- `tests/test_container_worker_execution_backend.py`
- `tests/test_addons.py`
- `tests/test_team_addon_repository.py`
