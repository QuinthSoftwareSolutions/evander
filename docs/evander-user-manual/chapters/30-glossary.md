# 30. Glossary

> **At a glance.** Evander gives familiar words such as “session,” “event,” “team,” and “release” specific meanings. Use this glossary whenever a term feels more precise than ordinary conversation or when two nearby concepts seem easy to confuse. Each definition describes the current system rather than a historical or generic software meaning.

## How to use this glossary

Look up the word exactly as it appears in Admin Web, a status message, a workflow, or an administrator's explanation. A definition often says both what the thing **is** and what nearby thing it **is not**; that distinction is part of the meaning, not a qualification to skip.

Several common chains are easier to remember together:

| If these terms seem similar… | Keep this distinction in mind |
|---|---|
| event, trigger, workflow, run | An event is a saved fact; a trigger decides whether it enters a workflow; the workflow is the prepared process; a run is one recorded use of that process. |
| session, sandbox, agent | A session is the bounded job identity; the sandbox is its temporary execution environment; the agent is the execution identity doing the work. |
| task, dispatch, lease | A task describes planned Team work; a dispatch asks for execution; a lease records who currently owns that execution. |
| capability, network route, approval | Permission, reachability, and a person's decision are separate checks. Having one does not imply the others. |
| branch, commit SHA, release | A branch is a movable label; a commit SHA identifies one exact code version; a release publishes the approved version under controlled rules. |
| canonical state, projection | Canonical state is the authoritative saved record; a projection is a useful view derived from it. |

For a broad explanation, use the Subject Index to find the relevant chapter. The **Source trail** below also points to the chapter ranges and implementation references behind these definitions.

## A

**Accepted-deliverable rework**<br>
An authorized recovery that reopens a finished or accepted task without
rewriting its accepted deliverable or reviews. The original owner must submit a
fresh, formally reviewed deliverable that explicitly supersedes the accepted
one.

**Active lease**<br>
A Team record giving an agent responsibility for a dispatch for a bounded period/lifecycle. A task can exist without an active lease; inspect the lease when determining whether work is actually assigned or running.

**Add-on**<br>
A packaged extension identified by `addon.json` and normally discovered by scanning configured package roots. Explicit modules and the `evander.addons` entry-point path remain supported compatibility/opt-in loading surfaces. An add-on may contribute providers, MCP tools, Admin Web UI, schema/catalog metadata, and private state under `runtime/addons/<addon-id>/` without owning core state.

**Admin API**<br>
The host-side HTTP surface behind Admin Web. It exposes composed domain services to authenticated operators and add-on UIs.

**Admin runtime**<br>
The assembled service graph returned by `build_admin_runtime`. CLI, gateway, and Admin Web integrations should use this composition surface rather than rebuilding services independently.

**Admin Web**<br>
Evander's supported operator GUI for health, execution, approvals, audit, workflows, configuration, workers, skills, chat, users, and add-on views.

**Agent**<br>
An execution identity capable of working inside an isolated session. In Team, an agent also has membership, responsibilities, model selection, availability, and potentially a reporting position.

**Agent harness**<br>
The program that coordinates an agent's work by sending prompts and context to the configured model, presenting tools, and applying model responses in the working session.

**Agent Mail**<br>
The Team UI's sent and received projections over the durable Team inbox collection. It is not a second independent mail transport or a pair of unrelated databases.

**Approval**<br>
An operator decision required by policy before a gated tool call may proceed. Approval complements identity and capability checks; it does not replace them.

**Artifact**<br>
A file or bundle moved through a controlled host-owned publishing/import path. Guest files are not durable merely because they existed in a disposable session.

**Artifact space**<br>
A named, access-controlled namespace with published artifact versions, referenced as `artifact-space:<path>`. Review sessions receive allowed versions through controlled imports under `/artifacts`.

**Audit journal**<br>
The append-only `runtime/audit.jsonl` record of tool calls, denials, errors, and related decisions. `runtime/audit.db` is a query index, not the canonical journal.

## B

**Bare repository**<br>
A host-owned Git repository under `runtime/managed-git/` used as the controlled internal remote. It is addressed operationally by a managed repository ID, not exposed host paths.

**Base VM**<br>
The minimal reusable VM image from which disposable overlays are created. Optional language, browser, and package tooling is added by post-launch bootstraps.

**Blocker (Team)**<br>
The canonical durable record for a condition that prevents task, review,
release, human-review, or agent execution progress. It keeps a stable reason
code, source record, affected tasks, owner/delegate, escalation route, events,
and source-aware recovery.

**Bootstrap**<br>
An idempotent or reconciling host script that installs/configures a particular guest capability, such as Codex, .NET, NuGet, Python, or Playwright.

## C

**Canonical branch**<br>
The approved upstream branch that operators and external systems treat as the published result. It must be distinguished from internal candidate or release branches.

**Capability**<br>
An explicit permission associated with an identity/session that is evaluated before a host tool may be used. Capabilities are narrower than general network or host access.

**Case**<br>
A Team domain object for structured operational work that benefits from its own lifecycle and context rather than being represented only as a task or message.

**Cancellation (task)**<br>
An authorized terminal disposition with reason code, explanation, actor, time,
and optional same-project successor. It resolves an obsolete task but does not
claim that its outcome was accepted.

**Chat tool**<br>
A Team MCP operation classified for interactive communication behavior. Classification matters to exposure and wake semantics; `team_reply_to_chat` is a chat tool, not an ordinary mutation.

**Command**<br>
A Team write request applied transactionally to canonical state with validation, authorization, revision checks, receipts, and often outbox records.

**Communication target**<br>
A directory-scoped, host-owned description of a communication endpoint and its supported capabilities—such as read, write, reply, notify, or comment—stored in `runtime/communication-targets.db`.

**Consultation**<br>
A bounded request to another agent/session for analysis or advice, persisted independently from the caller so lifecycle and cleanup can be tracked.

**Context handoff**<br>
The bounded, best-effort advisory-summary dispatch attempted before a manual or
idle Team context rotation. It has 120 seconds and only the handoff-recording
tool; the fresh-context reset continues if it fails, times out, or records
nothing.

**Contribution graph**<br>
The recursively derived lineage from an aggregate deliverable to its exact
accepted direct-child `source_deliverable_ids`. It proves composition without
copying child state or relisting every leaf on the root.

**Container worker VM**<br>
An execution backend in which a dedicated worker VM runs disposable Docker containers through `evander-worker-daemon`. It is additive to, not a replacement for, the libvirt VM backend.

**Control plane**<br>
The trusted host-side services that hold secrets, enforce policy, schedule work, manage state, and expose narrow capabilities to isolated execution environments.

**Cursor**<br>
Durable provider progress used to avoid rereading or replaying external events. Resetting a cursor can duplicate work and must be treated as a recovery action.

## D

**Default-deny egress**<br>
A network posture in which outbound traffic is blocked unless a specific route or destination has been allowed.

**Defense in depth**<br>
The use of several independent controls so that bypassing or misconfiguring one does not remove the others.

**Deliverable**<br>
A Team submission representing completed work, usually tied to a task and containing a Git candidate or published artifact references plus a summary.

**Deliverable contract**<br>
The explicit completion requirements on an integration-boundary task, including
whether Git provenance is mandatory and which direct child/dependency tasks
must contribute their current accepted deliverables.

**Directory**<br>
Evander's hierarchical ownership and configuration scope. `root` is the immutable top-level directory and default baseline.

**Directory overlay**<br>
A domain-specific JSON configuration at `runtime/directory-config/root/...` that inherits from ancestors and overrides only its supplied fields.

**Dispatch**<br>
A Team execution assignment derived from work such as a task or review request. The dispatch describes work to run; a lease records who currently owns it.

**Dispatch hydration**<br>
Reading the exact immutable scope of the current Team dispatch on demand. It
complements compact wake prompts and cannot be used to broaden the assigned
tasks, reviews, blockers, repositories, or command authority.

**Disposable execution**<br>
A VM or container intended to be created for bounded work and removed afterward. Durable state and secrets remain on the host or pass through explicit artifacts/Git surfaces.

## E

**Effect**<br>
A workflow-requested side effect executed by the effect orchestrator, such as session creation, communication, artifact publication, Git operations, or internal event scheduling.

**Effect execution**<br>
The durable orchestration record for an effect attempt, including status, idempotency/lease context, output, and error.

**Ephemeral delegate**<br>
A temporary Team agent/session created for a bounded subtask. It must return a result and follow terminal cleanup rather than silently becoming persistent staff.

**Event**<br>
A typed fact offered to orchestration. An event does nothing by itself until a trigger/provider and active workflow revision match it.

**Evidenced no-op**<br>
An outcome-task completion used when no new external effect is the correct
result. The task contract must allow it, evidence refs are mandatory, and the
submitted completion must be accepted through the normal deliverable path.

**Exact-SHA publication**<br>
The rule that Git publication must use the precise commit that passed review and verification, protected by lease/readback checks rather than a movable branch assumption.

**Execution backend**<br>
The abstraction that creates, resumes, inspects, and terminates isolated execution. Current choices are `libvirt_vm` and the additive `container_worker_vm`.

**External Team Chat bridge**<br>
An operator-configured workflow path that persists an authenticated external
human message in an ordinary Team Chat conversation and durably requests
delivery of the Team reply back to the source. It does not turn arbitrary
external identities into Team operators.

## F

**Fail closed**<br>
To deny an action when a check encounters an error or cannot reach a safe decision, rather than treating uncertainty as permission.

## G

**Gate**<br>
A workflow or release condition that must be satisfied before a dependent effect or transition proceeds. Gates preserve evidence and policy rather than serving as informal checklists.

**Gateway**<br>
The MCP-facing host process (`evander-gateway`) that composes tools, establishes request context, applies policy/approval, invokes handlers, and writes audit evidence.

**Governance agenda**<br>
A Team record surfacing a management signal to the appropriate governance group manager. Pending agenda can contribute to wake and Workspace attention.

**Governance group**<br>
A Team grouping that oversees member teams, goals, agenda, and aggregate cost/operational signals.

**Guest**<br>
An untrusted or less-trusted VM/container execution environment. It receives session-scoped configuration and narrow host URLs, never the host's general secret set.

## H

**Human review finding**<br>
An immutable actionable issue from a human changes-requested decision, linked
to its own child under a coordination-only correction parent. A deterministic
candidate/owner mapping routes that child directly; an ambiguous mapping waits
for explicit routing. The finding stays open through successor lineage until a
later approved human decision explicitly confirms its carried resolution.

**Human review request**<br>
A release-gate record pinned to one candidate SHA/tree, reviewer assignment,
checklist, and manual or prepared test target. Only a real eligible Admin user
with scoped permission and privileged confirmation can decide it.

**Host config**<br>
The host-global bootstrap settings in `runtime/host-config.json`. It is composed with directory-scoped domains to form the runtime configuration.

**HTTP target relay**<br>
A session-scoped relay for explicit external HTTP targets. It forwards guest-managed headers/cookies subject to the grant but does not inject host secrets.

## I

**Idempotency**<br>
The property that repeating an operation has the same intended effect as performing it once. It depends on stable identity and an implementation that honors the retry contract.

**Idempotency key**<br>
A stable identity used to recognize that an event/effect/command has already been applied. It permits safe retry only when the operation's contract actually honors it.

**Identity**<br>
The authenticated subject attached to a request: operator, agent, session, directory, and related scope. Identity answers “who”; policy answers “may they do this here now?”

**Inbox item**<br>
A durable Team communication/work signal with priority, recipient, handling state, and optional response intent. Current wake logic makes pending non-low inbox work eligible without requiring `response_requested`.

**Integration boundary**<br>
An explicit task flag stating that the task owns a reviewed final-acceptance
boundary. Its contract may require Git provenance, named task contributions, or
both. It is independent of the task's title or position in the hierarchy and
prevents direct-finish or artifact-only escape from the declared contract.

**Internal event**<br>
A host-local typed event used to connect commands, schedules, Team transitions, and workflows without pretending it came from an external provider.

## L

**Lease**<br>
A time- or ownership-bounded claim that prevents conflicting workers or actors from safely performing the same mutable operation. Team work and Git release publication both use lease concepts, at different layers.

**Least privilege**<br>
The rule that each job receives only the access needed for its current purpose and duration.

**libvirt VM backend**<br>
The default execution backend using disposable overlays and libvirt domains on the control-plane host.

**Low priority**<br>
A Team inbox priority that does not by itself make the item wakeable. It can still be read and handled through normal work.

## M

**Managed repository ID**<br>
The stable opaque identity returned when an upstream repository is registered. Workflows, Team, CLI, and guest clone operations use it instead of a host filesystem path.

**Managed Git**<br>
Evander's host-owned system for canonical upstream refresh, restricted guest refs, detached operation worktrees, exact-SHA promotion, and lease-protected publication.

**MCP (Model Context Protocol)**<br>
The protocol used to expose typed host tools to agent clients. In Evander, MCP is a narrow control surface, not a code-transport mechanism or general remote shell.

**Membership**<br>
A Team relationship placing an agent in a team with responsibility/role context. Project membership and team membership are related but not interchangeable.

**Memory (Team)**<br>
A durable Team knowledge record with scope and optional published artifacts. It is explicit domain state, not the model's transient context window.

**Model relay**<br>
The host service on the OpenAI relay surface that holds upstream credentials and exposes configured compatible APIs to guests.

## O

**Optimistic concurrency**<br>
The pattern in which a client submits an expected revision/token and a mutation fails on mismatch. The safe response is refresh, reassess, and resubmit—not overwrite.

**Originating task**<br>
A provenance-only link recording that one task caused independent work to be
created. It grants no root-creation authority and creates no ownership,
containment, dependency, or completion edge.

**Org chart**<br>
The Team visual projection of teams, agents, reporting, and governance relationships. It is derived from domain records and is not a separate source of truth.

**Outbox**<br>
Rows written in the same transaction as a Team command so downstream events/actions can be dispatched reliably after canonical state commits.

## P

**Pack revision**<br>
An immutable/versioned view of an operator workflow pack used for validation, concurrency control, and run provenance.

**Parent task**<br>
The aggregate task for which a child is a constituent contribution. Every
descendant recursively gates the parent's submission, acceptance, resolution,
and release.

**Policy**<br>
Rules resolving whether a subject can invoke a tool in a directory/session context and whether the invocation needs approval. Policy denial is itself audited.

**Projection**<br>
A read-oriented representation derived from canonical state, such as the Team Workspace, Org chart, Mail view, or audit SQLite index.

**Project**<br>
A Team scope organizing goals, members, leads, tasks, plans, deliverables, and release activity.

**Provenance**<br>
Evidence of a result's origin and history, including the event, session, code version, decisions, and actions that produced it.

**Provider**<br>
A registered implementation that supplies triggers, gates, queries, effects, schemas, editor metadata, or other runtime behavior to the orchestration catalog.

## Q

**Query**<br>
A workflow node that reads host or provider state and publishes structured outputs without being the external business side effect. Like effect completions, query completions continue the graph through durable events.

## R

**Relay**<br>
A narrow host-owned network service that mediates a specific external capability—models, NuGet, HTTP targets, or npm audit—without granting general guest internet.

**Release package**<br>
The Team record gathering candidate commit, target, review and verification evidence, retry state, waivers, and publication result for a release attempt.

**Resource token**<br>
A Team UI/API concurrency token tied to a mutable resource. It complements numeric revisions where a more specific mutation guard is required.

**Review cascade**<br>
The linked Team lifecycle from deliverable submission through review requests/dispatches, decisions, requested changes, follow-up work, and final acceptance.

**Review cohort**<br>
A sealed, atomically created set of review requests for one deliverable. Team
waits for every active member to decide before consolidating requested changes,
and any replacement must recreate the complete reviewer/profile multiplicity
as a fresh cohort.

**Review request**<br>
A request for a reviewer to evaluate a deliverable or PR. Core's PR review-request inbox and Team review requests are distinct domain records even when a workflow connects them.

**Role**<br>
A Team-defined responsibility profile used within a team. Roles are managed inside the **Teams** view; there is no current top-level Roles view.

**Root directory**<br>
The immutable top node named `root` in Evander's directory tree and the baseline for inherited domain configuration.

**Run**<br>
A durable workflow evaluation/execution instance tied to an event and pack revision. It contains matches, gates, and queued effects rather than being synonymous with an agent session.

## S

**Sandbox**<br>
The bounded execution environment used for an agent session: a disposable VM or a container on a worker VM, not an unrestricted host workspace.

**Scheduled wake**<br>
A durable request to resume an eligible session at/after a time. The dedicated wake worker also respects disabled-agent and terminal-session state.

**Secret boundary**<br>
The design rule that real upstream secrets remain on trusted host surfaces and are not copied into guests; narrow relays/effects perform authorized operations.

**Session**<br>
A bounded agent execution identity and lifecycle, usually associated with one disposable VM/container and session-scoped environment, grants, Git keys, and audit context.

**Session environment**<br>
Guest configuration written to `/etc/evander/session.env` and loaded by the agent shell profile. It contains narrow endpoints and session values, not the host's general secrets.

**Skill**<br>
An Evander-managed, reviewed `SKILL.md` copied into the selected user or system skill library. Skills are distinct from MCP tools and workflow providers; the current managed-skill unit is one instruction file rather than an arbitrary asset bundle.

**Source trail**<br>
The end section of each manual chapter identifying the authoritative implementation and documentation used for verification.

**State database (Team)**<br>
`runtime/addons/team-addon/state.db`, the current canonical SQLite store for Team domain records, leases, command receipts, and related state.

## T

**Task**<br>
A Team unit of planned work with status, ownership/assignment, dependencies, project context, and possible dispatch/deliverable/review relationships.

**Task assessment**<br>
The non-persisted recursive Team value that gives one task's resolution status,
readiness, unresolved descendants, dependencies, acceptances, reviews,
blockers, integration readiness, contribution coverage, and invalid reasons.

**Team**<br>
An organizational domain object containing agents, responsibilities, roles, leadership/reporting context, and project participation. “Team” may also refer to the add-on when capitalized as **Team Add-on**.

**Team Add-on**<br>
The `evander_team_addon` package whose manifest ID is `team-addon` (its Python entry-point name is `team`). It contributes Team state, commands, providers, MCP tools, and an Admin Web area while preserving the core/add-on boundary.

**Team dispatch**<br>
The immutable scheduler-computed task, blocker, review, inbox, project, and
repository scope for one persistent-agent wake. It moves from preparation to a
leased or terminal status; Team commands cannot widen it.

**TOCTOU race**<br>
A time-of-check/time-of-use race: state validated during one step changes before a later action uses it.

**Tool class**<br>
A Team MCP exposure class. Current classes are ordinary, chat, ephemeral-worker, governance, and read; the active wake/session receives only the classes appropriate to its role and dispatch.

**Trigger**<br>
A workflow definition/provider that turns eligible external or internal events into workflow runs when its predicates and cursor rules match.

**Trust zone**<br>
A boundary with a defined threat/authority level: external providers, host control plane, isolated guest/container, and operator browser are separate zones even when they communicate.

## V

**Verification evidence**<br>
Structured proof attached to a release candidate, such as job result, command, commit SHA, artifact, timestamp, and any policy-authorized reasoned waiver. A failed verification is unwaivable; narrative confidence is never equivalent evidence.

**VM overlay**<br>
A disposable disk derived from the base image. New overlays default to 20 GB and belong under `/var/lib/libvirt/images/evander`.

## W

**Wake**<br>
A decision and action that starts or resumes an agent because eligible work exists. A task/message's existence is not proof that a wake row was created or delivered.

**Wake context**<br>
The compact, scoped payload given to an agent when it resumes, containing the relevant work, inbox, lease, review, artifact, and control facts rather than all Team state.

**Workflow**<br>
A revisioned graph connecting triggers, gates, queries, and effects. It coordinates durable host-side reads/actions and may create or resume agent sessions, but it is not itself an agent prompt transcript.

**Workflow pack**<br>
An operator-managed JSON document containing one or more workflows and provider configuration references, edited through revision/lock-aware surfaces.

**Worktree**<br>
Either a short-lived detached core managed-Git operation checkout below
`runtime/git-worktrees/<managed-repo-id>/<operation-id>/`, or a stable
task/repository checkout inside a Team execution environment. The first is
operation-scoped; the second is mapped to fresh dispatch-bound authorization
contexts and has its own safety-checked cleanup lifecycle.

**Worktree cleanup candidate**<br>
A durable Team record proposing reclamation of one resolved or reassigned task
checkout. It stores exact path/branch/commit safety evidence, attempts,
inventoried ignored files, bytes reclaimed, and any
`recovery_required` refusal before context metadata is pruned.

## Source trail

- Chapters 3–15 — core trust, configuration, policy, orchestration, execution, networking, Git, integrations, and operations terminology.
- Chapters 16–25 — Team boundary, domain, commands, wakes, communication, review, delegation, release, and governance terminology.
- `docs/configuration-reference.md` — canonical configuration and store names.
- `docs/addons.md` — add-on and Team contracts.
- `docs/git-remotes.md` — managed Git and exact-SHA publication language.
- `src/evander_team_addon/models.py`, `src/evander_team_addon/services/commands/`, and `src/evander_team_addon/providers.py` — current Team model and runtime behavior.
