# 16. Team Add-on Boundary and Domain Model

> **At a glance.** The Team Add-on is an optional Evander domain module for durable organizations, projects, work, communication, review, release, governance, and memory. Evander core still owns sessions, execution backends, workflows, managed Git, communications, artifacts, policy, and model usage. The add-on composes those capabilities through registered APIs, MCP tools, providers, and a workflow pack; its canonical business state lives in <code>runtime/addons/team-addon/state.db</code>.

## A second control plane, not a second product

Evander core answers questions such as “which isolated session should run?”, “which tools may it use?”, and “which pinned commit may be published?” The Team Add-on answers a different set:

- Which persistent agent is the manager, lead, implementer, or reviewer?
- Which project, case, task, deliverable, review, or release does an action belong to?
- Who may direct, review, stand down, delegate, or publish work?
- What durable context should survive an individual model conversation?

The distinction matters operationally. A Team agent refers to a core Evander session, but it is not the session. A Team project may refer to a core managed repository, but it does not own the upstream repository definition. A Team release requests core Git effects, but it does not bypass their leases and compare-and-swap checks.

The add-on is loaded through Evander's generic add-on mechanism. It contributes:

- a self-contained Admin UI at <code>/addons/team-addon/</code>;
- an Admin API below <code>/api/addons/team-addon/</code>;
- Team-owned MCP read and command tools;
- trigger, query, and effect providers;
- the registered <code>team-runtime.v1</code> workflow-pack definition.

No Team-specific model has to be embedded in core Admin Web or the core gateway. Disabling the add-on removes those registered surfaces; it does not turn Team records into core records.

![Responsibility boundary between Evander core and the Team Add-on](../../assets/evander-user-manual/diagrams/10-team-boundary.svg)

<p class="figure-caption">Figure 16.1 — Team owns organizational truth while core owns execution and integration capabilities.</p>

## Canonical and derived state

The canonical Team database is relational SQLite:

<code>runtime/addons/team-addon/state.db</code>

It contains the validated Team domain plus command-support tables for dispatch activity, idempotency, and the durable outbox. The store uses foreign-key enforcement, a busy timeout, write-ahead logging, and serialized write transactions. On the first database initialization only, an old <code>state.json</code> may be imported. That JSON file is migration input, not a live fallback.

Several useful projections are deliberately non-canonical:

| Projection | Purpose | Rebuild or recovery rule |
|---|---|---|
| Core session <code>metadata.team_addon</code> | Compact routing and operator context | Refresh it from Team state; never edit it as Team truth. |
| Team UI view payloads | Scoped, revision-tagged read models | Reload after an invalidation or revision conflict. |
| Qdrant Team-memory collection | Semantic retrieval index | Reindex from canonical memory records. |
| Timeline view | Human-readable events derived from current state | Do not treat it as an append-only audit log. |
| Artifact-space and managed-Git records | External durable capabilities referenced by Team state | Diagnose them in their owning core services as well as Team state. |

This boundary prevents a common debugging error: changing a session manifest, a Qdrant point, or a UI payload does not repair the Team record that produced it.

## The domain from the outside in

The durable hierarchy is easiest to understand in layers.

### Organization

A <code>Team</code> has a manager, optional cost cap, optional operator communication target, projects, roles, agents, and a policy that chooses direct operator escalation or governance escalation. A <code>GovernanceGroup</code> spans one or more Teams and has its own manager and agenda.

A <code>TeamRole</code> names an organizational function. It may be project-scoped and may grant narrow permissions such as <code>create_root_task</code> or <code>request_release</code>. A <code>PersistentAgent</code> binds an organizational identity to a core Evander session. Reporting lines and project assignments are explicit records, not deductions from a display name.

### Work

A <code>Project</code> groups goals and executable work. It may reference a core <code>managed_repo_id</code>, base branch, canonical release prefix, and release policy. A <code>Case</code> holds intake and lifecycle context; a <code>Task</code> is the assignable unit of execution. Tasks can form parent/child and dependency graphs.

### Evidence and decisions

A <code>Deliverable</code> records an agent's evidence for a task: artifact references, a pinned Git context, verification status, and supersession history. A <code>ReviewRequest</code> binds a deliverable, reviewer, optional profile, criteria, findings, and immutable decision. A <code>ReleasePackage</code> freezes accepted scope and the candidate evidence needed to decide whether publication is safe.

### Runtime and communication

<code>AgentRuntime</code> records leases, dispatch identity, wake timing, context-rotation policy, and no-progress state. Inbox items, conversations, messages, stand-downs, ephemeral assignments, Git promotions, and governance agenda items connect durable intent to asynchronous runtime work.

### Memory

<code>TeamMemory</code> preserves decisions, preferences, facts, runbooks, postmortems, research, and handoffs. Scope and visibility remain part of the canonical record even when semantic search is enabled.

![Team Add-on domain relationships](../../assets/evander-user-manual/diagrams/11-team-domain.svg)

<p class="figure-caption">Figure 16.2 — Durable Team records connect organization, work, evidence, runtime, and governance.</p>

## Northstar: one thread through the model

Throughout this manual, Northstar uses the stable fixture below:

| Record | Example |
|---|---|
| Team | <code>northstar-platform</code> |
| Project | <code>mission-control</code> |
| Manager | Mira |
| Project lead | Priya |
| Implementer | Imani |
| Reviewer | Rowan |
| Release agent | Riley |
| Work | Add safe retry behavior to telemetry delivery |

Mira is the Team manager and organizational root. Priya leads <code>mission-control</code>. Imani owns the telemetry retry task. Rowan reviews its deliverable under the project's review policy. Riley has release authority but does not acquire managerial authority merely by holding it.

This example demonstrates why the records stay separate. Mira's Team role, Priya's project leadership, Imani's task ownership, Rowan's assigned review, and Riley's release permission are five independent authorization facts. A friendly title such as “senior engineer” would not safely encode them.

## Model invariants that protect the graph

The repository validates the complete next state before committing it. Important invariants include:

- referenced Teams, projects, roles, agents, tasks, cases, deliverables, and reviews must exist and have compatible scope;
- an agent attached to a Team has a valid role and reporting relationship; a teamless agent cannot retain Team reporting metadata;
- a project lead and project members are registered agents of the owning Team;
- parent and dependency task links cannot create cycles or cross project boundaries;
- case/task links are bidirectional and remain in the same Team and, where applicable, project;
- task ownership, deliverable production, review assignment, and release scope must agree;
- IDs and map keys agree, and enumerated states are validated;
- a Team manager remains the reporting root rather than an ordinary subordinate.

Task states are <code>pending</code>, <code>in_progress</code>, <code>blocked</code>, <code>waiting_for_review</code>, <code>finished</code>, <code>accepted</code>, and <code>rejected</code>. Cases use <code>open</code>, <code>triage</code>, <code>in_progress</code>, <code>waiting</code>, <code>resolved</code>, and <code>closed</code>. Other records have domain-specific state machines; do not infer that the same word has identical transition rules everywhere.

The project status is recalculated from its goals and work. A project becomes complete only when its goals are accepted and no active work remains. Blocking work can make it blocked; otherwise it remains active. It is not an operator-maintained cosmetic flag.

## Where mutations enter

Three supported write paths share the repository rules:

1. Ordinary Admin API and UI domain mutations use an explicit expected Team revision. Append-specialized surfaces such as Chat use their own current-record contract.
2. Team MCP commands run with session-derived identity and a current dispatch context.
3. Team workflow effects perform operator-authored automation through registered providers.

Templates are a fourth convenience surface, but they materialize the same Team records through one optimistic mutation. A template does not create a core session, VM, container job, managed repository, or external accounting action. Those resources must already exist or be created through their owning Evander flows.

Reads are grouped rather than duplicated across every chapter. Operators use the scoped Team UI views and Admin API. Agents use Team list/get tools such as tasks, inbox, cases, deliverables, reviews, releases, projects, agents, runtime attention, identity, and memory search. Workflows use <code>query:team:state</code>, <code>query:team:memory</code>, and <code>query:team:session-routing</code>.

## Failure patterns

| Symptom | Likely boundary mistake | Correct first move |
|---|---|---|
| Team UI shows stale data after a write | A scoped projection was retained across a revision invalidation. | Reload the affected view and compare its revision. |
| A session exists but no Team agent can act | Core session creation was mistaken for Team registration. | Register the persistent agent and validate its Team role and project assignment. |
| Editing session metadata has no lasting effect | A mirror was changed instead of canonical Team state. | Repair through the Team API or supported command surface. |
| A managed repository cannot be found | Team state contains an obsolete or wrong managed-repository ID. | Inspect the core managed-Git registry, then update the project through the revision-checked API. |
| Legacy JSON changes are ignored | SQLite has already been initialized. | Treat <code>state.db</code> as authoritative; do not replay JSON over it. |
| A Timeline row is missing an old intermediate value | Timeline was mistaken for an audit ledger. | Use command activity, workflow runs, Git promotion records, and external audit stores appropriate to the event. |

## Practical runbook

For a live deployment:

1. Confirm the add-on is enabled and registered before interpreting missing Team routes as missing data.
2. Read the broad status and Team bootstrap/view payloads through the supported UI or API.
3. For a state discrepancy, inspect <code>runtime/addons/team-addon/state.db</code> with the current schema. Prefer the deployment's own Evander environment and a read-only or safely copied database.
4. Compare the relevant core resource by stable ID: session, managed repository, workflow run, artifact ref, communication target, or model-usage record.
5. Perform repairs through the Team API, UI, MCP command, or workflow effect that owns the invariant. Do not edit SQLite rows by hand.
6. Verify the new revision, the affected scoped view, and any queued outbox or workflow work.

For backup, use a SQLite-consistent backup mechanism or stop writers before copying the database. Back up referenced artifacts, avatars, core voice-session records, managed repositories, workflow configuration, and model-usage data separately; not all of those bytes live in Team SQLite.

## Source trail

- <code>src/evander_team_addon/addon.json</code> and <code>src/evander_team_addon/__init__.py</code> — add-on identity, UI/API mounts, MCP registration, providers, and runtime-pack registration.
- <code>src/evander_team_addon/models.py</code> — canonical domain types, state machines, graph validation, and project derivation.
- <code>src/evander_team_addon/repository.py</code> and <code>src/evander_team_addon/sqlite_store.py</code> — optimistic repository updates, relational persistence, migration, and schema.
- <code>src/evander_team_addon/api.py</code>, <code>mcp.py</code>, and <code>providers.py</code> — the three supported integration surfaces.
- <code>docs/addons.md</code> — add-on loading, Team boundary, UI, and operator contracts.
- <code>tests/test_team_addon_models.py</code>, <code>tests/test_team_addon_repository.py</code>, and <code>tests/test_team_addon_api.py</code> — domain invariants, storage behavior, and mounted UI/API behavior.
