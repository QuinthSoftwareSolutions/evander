# 25. Governance, Memory, Cost, and Internals

> **At a glance.** Governance provides cross-Team attention without erasing Team ownership. Durable memory preserves scoped organizational knowledge while Qdrant remains a rebuildable index. Cost totals are estimated operational guards with explicit coverage, not invoices. Under all three features, relational SQLite remains canonical; derived views, workflow events, semantic vectors, and session mirrors must be diagnosed through their owning boundaries.

## Governance across Teams

A <code>GovernanceGroup</code> has:

- one persistent governance manager;
- a set of member Team IDs;
- goals and optional estimated cost cap;
- a durable agenda;
- runtime lease, wake timing, and change fingerprints.

The governance manager is an oversight role, not the manager of every member Team. The manager receives summaries of member Teams, projects, Team managers, open/blocked work, cost, and pending agenda. It can create an operator directive for a member Team's manager and mark its own agenda handled.

Current agenda types include:

- <code>operator_directive</code>;
- <code>team_escalation</code>;
- <code>cost_cap_warning</code> and <code>cost_cap_reached</code>;
- <code>stale_team</code> and <code>blocked_team</code>;
- <code>scheduled_checkin</code>;
- <code>policy_decision</code>.

Adding or removing group membership creates a pending <code>policy_decision</code> agenda item containing previous, added, removed, and current Team IDs. It also makes governance evaluation due, so the next prompt contains the new Team summaries.

The runtime leases a governance manager when agenda is pending and due or when member-Team cost/blocked fingerprints materially changed. Governance commands require the correct managed group and lease. The tool surface is deliberately small: create a directive and mark an agenda item handled, plus appropriate reads.

Membership proves that a Team is an authorized governance target. It does not prove that the governance manager owns a product decision. For Northstar, a governance manager may direct Mira to assess a telemetry-policy conflict, but should not assign Imani's implementation details directly or declare Rowan's review accepted.

## Escalation into governance

When <code>northstar-platform</code> has <code>escalate_to_governance=true</code>, Mira's escalation creates agenda in each group containing Northstar. The manager sees the Team and project summaries, decides what action is justified, creates a directive to Mira if needed, and handles the agenda.

This creates an auditable loop:

<code>Team escalation → governance agenda → governance lease/decision → Team-manager directive → Team work → handled states</code>

Do not mark an agenda handled merely to silence a wake before routing the necessary directive or recording the decision.

![Governance escalation and directive communication loop](../../assets/evander-user-manual/diagrams/17-governance-communication.svg)

<p class="figure-caption">Figure 25.1 — Governance routes decisions through Team managers instead of bypassing ownership.</p>

## Durable Team memory

Team memory is for compact knowledge that should survive dispatches and model contexts:

- <code>decision</code>;
- <code>preference</code>;
- <code>fact</code>;
- <code>runbook</code>;
- <code>postmortem</code>;
- <code>research</code>;
- <code>handoff</code>;
- <code>other</code>.

Scopes are <code>team_global</code>, <code>project</code>, <code>agent_private</code>, and <code>governance</code>. Records can be active, superseded, or archived. They contain title, summary, tags, refs, optional body, ownership/scope, and timestamps.

Broad state and search payloads omit the potentially long body. Explicit detail reads return it. Store bulky logs, datasets, and test evidence as immutable artifacts, then link their refs from the memory record.

Normal wake prompts do not inject all memory. Agents call <code>team_search_memory</code> when recall is useful. This prevents a growing knowledge base from consuming every dispatch context.

Memory commands include <code>team_record_memory</code> and <code>team_archive_memory</code>. Archiving or superseding preserves history; it does not mutate the old statement into a new one. These actions are classified as context activity rather than substantive task progress, so repeatedly writing notes does not defeat no-progress protection.

For Northstar, useful records include:

- decision: “Telemetry retries must preserve the original idempotency key.”
- runbook: “Diagnose retry exhaustion using attempt count, terminal reason, and upstream correlation.”
- handoff: “D2 supersedes D1 after jitter deadline correction; Rowan review R4 is current.”

## Semantic indexing

Canonical memory records live in Team SQLite. Qdrant stores a derived per-Team semantic collection with a generated name based on Team ID. Semantic results are joined back to canonical records and filtered by current visibility.

Visibility protects:

- agent-private records for the owning agent;
- project records for agents with visible project scope;
- governance records for the appropriate governance context;
- broader subordinate/project context available to authorized managers.

The exact visibility evaluator, not possession of a Qdrant point ID, determines access.

Workflow providers are:

- <code>query:team:memory</code>;
- <code>effect:team:index-memory</code>;
- <code>effect:team:reindex-memory</code>.

If Qdrant is unavailable or corrupt, canonical memory remains. Repair connectivity and reindex rather than treating the vector collection as the backup.

## Cost as an operational guard

A Team may have <code>max_cost_usd</code>; a governance group may also have a cap. Cost aggregation reads recorded model usage from <code>runtime/model-usage.db</code>, applies the runtime pricing catalog, and may backfill supported Claude transcript usage. It deduplicates records and attributes them using request, dispatch, session, Team/project, and ephemeral-assignment metadata.

The result separates Team, project, and unattributed amounts and includes:

- estimated cost;
- priced and unpriced usage;
- <code>coverage_status</code>;
- <code>coverage_reasons</code>.

These totals are an estimated lifetime operational safety rail, not billing-grade truth. Missing prices, incomplete attribution, transcript gaps, or ingestion delay can produce partial coverage.

When a Team reaches its cap, future Team wakes are blocked, including queued Chat. Existing external provider spend may already have occurred, and unrelated core workloads are not retroactively canceled. Raising or clearing the cap re-enables evaluation; do so only after understanding coverage.

Governance can emit a warning before the group or Team reaches its cap, using the configured warning ratio, and an agenda item when it reaches the cap. A warning is not proof of exact invoiced spend.

## SQLite internals

<code>state.db</code> contains normalized relational tables for Teams, roles, agents, assignments, runtimes, projects, tasks, cases, deliverables, reviews, inbox, conversations, memory, governance, releases, promotions, ephemeral assignments, and related evidence.

Three support ledgers are particularly important:

- <code>team_dispatch_activity</code>;
- <code>team_idempotency_records</code>;
- <code>team_outbox</code>.

Writes use <code>BEGIN IMMEDIATE</code> and full <code>TeamState</code> validation before commit. WAL and busy timeout improve concurrent service behavior but do not make direct multi-writer SQL safe.

The broad domain model is current-state storage. Dispatch activity, idempotency, and outbox records provide more durable execution evidence, but the UI Timeline itself is a derived rendering of current records. It cannot reconstruct every overwritten intermediate field value.

The one-time legacy <code>state.json</code> import is recorded in database metadata with path, timestamp, and digest. Once SQLite exists, changing the JSON does nothing.

![Team UI, canonical state, and derived data flow](../../assets/evander-user-manual/diagrams/18-team-ui-data-flow.svg)

<p class="figure-caption">Figure 25.2 — Scoped UI reads refresh from revision-only invalidations while optimistic writes remain explicit.</p>

## Workflow and provider internals

The add-on registers its providers only while enabled. The main grouped contract is:

| Kind | Current Team providers |
|---|---|
| Trigger | <code>trigger:team:deliverable-created</code> |
| Query | <code>query:team:state</code>, <code>query:team:memory</code>, <code>query:team:session-routing</code> |
| Runtime effects | wake due agents, wake governance managers, publish outbox, finalize dispatch |
| Domain effects | create/update cases and tasks, create directives/reviews, ephemeral lifecycle |
| Git/release effects | publish review branch, consolidate, record promotion, verify candidate, mark published |
| Memory effects | index and reindex |

The registered <code>team-runtime.v1</code> pack composes these with core agent, communication, Git, and event effects. Internal event routes include Chat queued, agent wake requested, ephemeral requested/completed, operator escalation, Git promotion/recovery, and release verification.

The packaged Python definition is a source definition. Enablement materializes an operator-owned live workflow document under the normal revision and lock contract. Refresh it through the Add-ons surface. Never directly rewrite a live workflow-pack file, even during incident repair.

## Diagnosis by source of truth

Start with the observed symptom, then cross the boundary deliberately:

| Symptom | First authoritative sources |
|---|---|
| Wrong Team ownership/state | <code>state.db</code> current schema and revision-checked Team API |
| Agent did or did not wake | Team Operations/runtime payload, runtime row, live pack parameters, workflow run, core session state |
| Async action missing | Team outbox row, internal event, workflow run, effect result |
| Git release discrepancy | release package, promotion record, managed bare ref, canonical push effect, remote ref |
| Memory search omission | canonical memory visibility, Qdrant health/index generation, query result |
| Cost discrepancy | model-usage DB, attribution metadata, pricing catalog, coverage reasons |
| Voice discrepancy | core voice-session store and optional Team Chat projection |

Avoid broad guesses from UI appearance alone.

## Backup and recovery

A useful backup set includes:

1. a SQLite-consistent backup of <code>runtime/addons/team-addon/state.db</code>;
2. Team avatar files;
3. referenced artifact data and artifact-space metadata;
4. core managed repositories and registry;
5. live workflow configuration and its revision history;
6. core voice-session and model-usage stores;
7. Qdrant only as a convenience snapshot, because it can be rebuilt.

Either stop Team writers before a filesystem copy or use SQLite's supported backup mechanism. Copying only the main DB file while WAL writes are active can yield an inconsistent backup.

Recovery order:

1. restore canonical Team SQLite and validate it with the current code/schema;
2. restore referenced core stores;
3. start services and verify add-on/provider registration;
4. inspect pending/claimed outbox and stale leases before resuming runtime;
5. rebuild semantic memory index;
6. reconcile managed Git refs, artifacts, voice records, and cost coverage.

Do not replay legacy JSON over restored relational state.

## Northstar operational review

At the end of the telemetry retry release, an operator should be able to answer:

- Which governance decisions affected Northstar and who handled them?
- Which current memory records explain the retry policy and supersede old guidance?
- What proportion of Northstar usage was priced and attributed?
- Which command activity and outbox rows led to the release?
- Which canonical branch and SHA were published?

If the answer depends only on a Chat transcript or Timeline rendering, the durable operational record is incomplete.

## Failure patterns

| Symptom | Correct response |
|---|---|
| Governance manager targets a Team outside the group | Membership authorization fails; update membership through revision-checked policy only if genuinely intended. |
| Same agenda wakes repeatedly | Inspect pending/handled state, lease finalization, cost/blocked fingerprint change, and whether a directive was actually routed. |
| Memory exists but semantic search misses it | Confirm visibility and canonical status, then inspect/rebuild the Team collection. |
| Search exposes a private record | Treat as an authorization defect; preserve query context and inspect canonical visibility rejoin immediately. |
| Team cap blocks Chat unexpectedly | Chat is a Team wake and is intentionally guarded; inspect estimated total and coverage. |
| Cost total differs from invoice | Expected possibility; reconcile usage ingestion, pricing, attribution, and unpriced events. |
| SQLite is locked | Identify the legitimate writer and long transaction; do not disable serialized writes or hand-edit WAL files. |
| Live runtime behavior differs from packaged definition | The operator-owned pack may be customized or stale; inspect its current revision and history, then refresh/merge through the supported API. |

## Practical runbook

### Governance

1. Confirm group manager, membership, goals, cap, and current runtime lease.
2. Read agenda and member-Team summaries.
3. Route only justified directives to member Team managers.
4. Mark agenda handled after the decision/action is recorded.

### Memory

1. Search canonical metadata by Team/project/scope before blaming semantic retrieval.
2. Read detail for long body content.
3. Supersede or archive; never overwrite the meaning of an old decision.
4. Reindex Qdrant from canonical records and retest visibility by representative identities.

### Cost

1. Read totals together with coverage status/reasons and unpriced usage.
2. Trace attribution to session, dispatch, project, and ephemeral metadata.
3. Compare the active pricing catalog with event timestamps/models.
4. Change caps only with explicit operational intent.

### Internals

1. Take a consistent snapshot before deep inspection.
2. Inspect current schema, Team revision, support ledgers, and live workflow pack.
3. Follow stable IDs across Team, workflow, session, Git, artifact, and usage stores.
4. Repair through supported services and verify the resulting revision/readback.

## Source trail

- <code>src/evander_team_addon/models.py</code> — governance, memory, cost-cap, runtime, activity, idempotency, and outbox records.
- <code>src/evander_team_addon/services/commands/governance_commands.py</code> and <code>services/domain/governance.py</code> — group authorization, directives, agenda, and membership decisions.
- <code>src/evander_team_addon/services/memory_service.py</code> and <code>services/commands/memory_commands.py</code> — canonical memory, visibility, semantic retrieval, and lifecycle.
- <code>src/evander_team_addon/costs.py</code> and <code>src/evander/model_usage/</code> — usage attribution, pricing, coverage, and cap payloads.
- <code>src/evander_team_addon/sqlite_store.py</code> and <code>repository.py</code> — relational schema, WAL, transactions, migration, and support ledgers.
- <code>src/evander_team_addon/providers.py</code>, <code>provider_schemas.py</code>, and <code>workflow_packs.py</code> — grouped node contracts, governance runtime, indexing, and live-pack definition.
- <code>docs/addons.md</code> — governance, memory, cost, runtime, and enablement contracts.
- <code>tests/test_team_addon_costs.py</code>, <code>tests/test_team_addon_models.py</code>, <code>tests/test_team_addon_mcp.py</code>, and <code>tests/test_team_addon_runtime.py</code> — visibility, cap, agenda, transactions, and provider behavior.
