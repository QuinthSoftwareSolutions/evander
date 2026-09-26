# 17. Enabling and Operating the Team Add-on

> **At a glance.** The Team Add-on is enabled from Admin Web and becomes part of the same Evander installation after its services are restarted. Once enabled, its views let operators inspect teams, projects, tasks, conversations, reviews, releases, and governance in one place. This chapter covers both the initial readiness check and the everyday habits that prevent one operator from overwriting another's recent change.

## What enabling Team changes

Enabling Team adds a coordinated set of screens and background capabilities to the existing Evander installation. After the required services restart, operators can create the organization, connect persistent agents to existing Evander sessions, follow work, and handle reviews and releases. The add-on does not create those sessions, repositories, or execution environments on its own.

There are two useful readiness questions:

1. **Can people open and use the Team views?** This checks the add-on UI and API.
2. **Can Team work actually run?** This also needs the gateway, workflows, sessions, and whichever integrations the chosen process uses.

A working screen with stalled background work is therefore possible and diagnosable. It does not mean the enablement setting was ignored.

## Before enabling it

An operator should know which Team features the installation intends to use and who can restart the affected Evander services. Features such as managed Git, semantic memory, or external communication can be added when needed; they are not prerequisites for every Team.

> <span class="sidebar-kicker">For administrators</span> **Package and service prerequisites**
>
> The bundled Python package must include <code>evander_team_addon</code>, its <code>addon.json</code>, and the packaged UI assets. The deployment also needs the core capabilities used by the workflows you intend to run:
>
> - persistent Evander sessions for registered Team agents;
> - a working gateway for Team MCP tools;
> - trigger and effect orchestrators for wakes and asynchronous effects;
> - a configured execution backend for persistent and ephemeral work;
> - managed Git, artifact publication, communications, model relays, and Qdrant only where the chosen Team features need them.

The add-on is disabled by default. Enabling it is a host configuration change, not merely a browser preference.

## Enablement flow

The supported operator path is:

1. Open Admin Web at <code>/#addons</code>.
2. Select **Team Add-on** and enable it.
3. Save the revision-checked change.
4. Let the Add-ons service materialize the registered <code>team-runtime.v1</code> definition into operator-owned workflow configuration.
5. Restart every service listed by the page, normally including Admin Web, the trigger orchestrator, the effect orchestrator, and the gateway.

If the operator cannot restart host services, record the saved configuration revision and the restart list shown by Admin Web, then hand those to an administrator. Do not expect the new screens, tools, and workflow providers to appear consistently until that restart boundary has been completed.

> <span class="sidebar-kicker">Deeper detail</span> **Configuration and runtime-pack ownership**
>
> Equivalent configuration sets <code>addon_loader.enabled_addon_ids</code> to include <code>team-addon</code>. Explicit module and Python entry-point loading remain compatibility mechanisms, but the Add-ons page is the normal operational surface because it coordinates host and worker configuration and reports the restart boundary.
>
> When the packaged runtime definition changes, use **Refresh runtime pack** on the same page or its equivalent revision-checked Admin API action. A live workflow pack is operator state. Never copy over its JSON file, rewrite it from a checkout, or race another editor with a stale document.

## Administrator check: verify the complete registration

After restart, verify all four planes:

| Plane | Positive check |
|---|---|
| Admin | <code>/api/admin/state</code> lists <code>team-addon</code>. |
| UI and API | <code>/addons/team-addon/</code> loads and <code>/api/addons/team-addon/templates</code> returns a catalog. |
| MCP | The gateway exposes Team tools only in an authorized Team context. |
| Workflow | The workflow editor catalog and validator recognize <code>trigger:team:*</code>, <code>query:team:*</code>, and <code>effect:team:*</code> providers. |

A <code>404</code> from all Team routes usually indicates loading or restart state. An empty Team view with working routes usually indicates valid but empty Team state. Those are different failures.

## The fifteen operator views

An operator does not need to learn all fifteen views at once. **Workspace** is the broad starting point; **Operations** answers “why is an agent waiting?”; **Delivery** follows evidence, reviews, and releases; and **Teams**, **Projects**, **Task board**, and **Cases** hold the main organizational and work records. The remaining views support communication, dispatch activity, memory, governance, planning, history, and reporting structure.

The add-on UI currently has these top-level views:

| View | Primary purpose |
|---|---|
| **Workspace** | Broad operational summary and the next records that need attention. |
| **Operations** | Wake, lease, session, context, stand-down, and runtime attention. |
| **Delivery** | Attention-first, project-grouped deliverables, reviews, releases, Git promotions, and exact gate evidence. |
| **Memory** | Durable Team memory and semantic-index operations. |
| **Projects** | Project goals, leads, Git configuration, and release policy. |
| **Task board** | Task ownership, hierarchy, dependencies, state, and priority. |
| **Cases** | Intake records, lifecycle state, external references, and linked tasks. |
| **Teams** | Team setup, persistent agents, roles, assignments, and stand-down controls. |
| **Chat** | Human-operator conversations with one persistent agent. |
| **Mail** | Sent and received projections over one durable Team inbox collection. |
| **Activity** | Redacted dispatch/tool success and failure history, filterable by Team, agent, dispatch, status, and tool. |
| **Governance** | Cross-Team groups, agendas, cost warnings, and directives. |
| **Planning** | Planning-oriented summaries of goals and work. |
| **Activity timeline** | A derived chronological rendering of current Team records. |
| **Org chart** | Reporting hierarchy and operational details per agent. |

There is no top-level **Roles** view. Roles are maintained inside **Teams** and setup/edit forms. Reviews and releases similarly live inside **Delivery**, not separate top-level tabs.

### Read narratives and copy exact references

Team messages, activity, task details, and case context keep readable titles and
summaries prominent. Long opaque IDs and Git SHAs appear as compact **ID** or **SHA**
badges. Hover to inspect the full value; click or activate the badge with the keyboard
to copy that full value. **Copied** confirms success; **Copy failed** means the browser
could not write to the clipboard. The compact caption is a display aid, not a shortened
stored identifier. URLs, paths, commands, and raw structured values retain their original
content.

In **Cases → Context**, evidence is grouped by request, sources, plans, decisions,
delivery, review, verification, and conversation. Expand **Record fields and provenance**
for structured detail or **Raw JSON** for the record content. **Load complete records**
retrieves evidence beyond a shortened preview; **Load next record page** continues when
more records remain. Check freshness, missing-source, restricted-source, and truncation
warnings before using a brief as evidence. See [Case context](../../case-context.md)
for saved views, frozen snapshots, and recipient grants.

### Follow delivery and task state

**Delivery** opens on records that still need attention. Projects remain
collapsed until selected or opened, and the project selector and status filters
narrow the workspace without removing accepted or historical records from
**All records**. Opening a deliverable shows the evidence stages that current
state can prove: implementation, deliverable acceptance or agent review,
release inclusion, automated verification, release-agent review, human review,
and publication. The view calls out the current stage and blocker rather than
presenting every record as equally urgent. For release-backed work, **Load exact
gate evidence** reads the existing permission-filtered release detail so
individual verifier attempts and review decisions appear only when authorized
evidence is available.

When a project policy enables the human release gate, **Delivery** also exposes
**My Human Reviews** and the human-review detail on the release. The filter is
assignment-aware: it shows requests the signed-in Admin user may currently
decide, not every release the user can read. The detail keeps the immutable
candidate SHA and tree beside automated verification, agent review decisions,
test instructions or prepared preview target, checklist, carried findings,
release-attempt lineage, and publication state.

The initial human actions are **Approve**, **Request changes**, and **Blocked**.
They are release decisions, not ordinary Team-agent commands. A decision
requires the current request revision, an idempotency key, privileged-operation
confirmation, the directory-scoped
<code>team.human_reviews.decide</code> permission, and current assignment
eligibility. There is deliberately no MCP tool that can decide on behalf of a
person.

The Org chart is diagnostic as well as visual. Agent nodes expose session, project, reporting, task, inbox, wake, and ephemeral-capacity information when available. It renders unreachable or malformed references for inspection; rendering never repairs persisted state.

The task board and task detail expose **Cancel task** as a separate audited
action. Its dialog requires a stable reason code and explanation and may name
an optional same-project successor. When lifecycle rules forbid cancellation,
the action remains visible but disabled and its hover explanation names the
unresolved descendants, active acceptance, or active review that must be
settled first. The generic task editor cannot move work directly into
<code>accepted</code> or <code>cancelled</code>.

**Planning** and **Task board** use the same revision-checked task editor. When a
title, description, owner, dependency frontier, deliverable contract, or another
preparation input changes after Git preparation, Team abandons that context and
queues a fresh generation. The task status distinguishes queued re-preparation,
an active agent lease, and a review awaiting dispatch. A priority-only edit does
not invalidate the checkout.

Background state and cost refreshes preserve open **Planning** project, task,
hierarchy, full-brief, worktree, and technical-detail disclosures, so a live
update does not collapse the operator's place in a long task. Disclosure state
is scoped to the owning Team view, and project-local goal keys are additionally
scoped by project, so shared renderers and repeated local IDs cannot couple
hidden copies. Chat state-stream and automatic refreshes similarly preserve a
reader's scroll position; an intentional thread change, send, or already-bottom
transcript still follows the newest message.

![Team Workspace dashboard with attention, delivery, team, and workload summaries](../../assets/evander-user-manual/screenshots/team-workspace.png)

<p class="figure-caption">Figure 17.1 — Workspace brings cross-cutting attention together while retaining links to the owning detailed view. Sanitized Northstar fixture.</p>

## Bootstrap Northstar

The quickest way to learn the UI is to preview a built-in template, such as the development-squad template, then materialize it once. Templates create Team-domain records in a single optimistic write. For the Northstar example, use stable IDs and names:

- Team <code>northstar-platform</code>, managed by Mira;
- project <code>mission-control</code>, led by Priya;
- Imani as implementer, Rowan as reviewer, and Riley as release agent;
- project goal and root task for safe telemetry retry behavior.

Before materialization, create or identify the Evander sessions that the persistent agents will reference. A Team template does not provision VMs or containers, create sessions, register managed repositories, push Git branches, or contact an external service.

After materialization:

1. Inspect the reporting tree in **Org chart**.
2. Confirm each agent's stable session mapping and active status in **Teams**.
3. Confirm Priya's project-lead assignment in **Projects** without changing her reporting role.
4. Inspect Imani's task in **Task board** and any intake case in **Cases**.
5. Leave release policy and managed Git unset until the corresponding core resources and verification design are ready.

## When two changes happen at once

Suppose one operator changes Imani's task while another changes the same Team from a different screen. Team does not silently let the last browser overwrite the first operator's work. It detects that the state has advanced and asks the later editor to reload, compare, and deliberately apply the intended change to the current record.

The practical response is straightforward:

1. Preserve the intended edit.
2. Reload the affected record.
3. Compare what changed while it was open.
4. Merge or reapply the intent deliberately.
5. Save again and verify the result.

> <span class="sidebar-kicker">Deeper detail</span> **Revisions, scoped reads, and conflict policy**
>
> The canonical Team state has a monotonically advancing revision. Ordinary domain edit routes require <code>expected_revision</code>:
>
> - missing or null revision returns <code>400</code>;
> - a stale revision returns <code>409</code>;
> - a valid mutation commits the complete validated state and advances the revision.
>
> The UI does not need to reload the full state after every successful write. Endpoints return scoped views and revision invalidations. A state stream can announce that a newer revision exists without embedding all changed records.
>
> Some edit flows support a narrowly defined conflict policy, <code>target_unchanged</code>, with a base resource token. That allows a mutation to rebase only when the particular target still matches what the operator read; creation can rebase only when the explicit ID is still absent. It is not a general “last writer wins” switch.
>
> Append-only Chat send is the important exception. It validates the current conversation and agent and deliberately does not reject a message merely because an unrelated global Team revision advanced. Conversation ownership, signed-in operator identity, agent eligibility, and the asynchronous dispatch rules still apply.
>
> When a <code>409</code> occurs:
>
> 1. Preserve the operator's intended change.
> 2. Reload the affected record and current revision.
> 3. Compare the concurrent change field by field.
> 4. Reapply or deliberately merge the intent.
> 5. Submit against the new revision and verify the returned view.

Never solve a conflict by directly replacing a live pack file or Team database.

## UI identity, privilege, and special media

Administrative mutations are protected by Admin authentication and the add-on's privileged-mutation guard. Human release decisions additionally require a real session principal; client-supplied user or actor fields are rejected rather than falling back to a generic administrator identity. Assignment is reconciled against current group membership and permission before notification, reassignment, resume, or decision. If no eligible reviewer remains, the same request and candidate become blocked until an audited reassignment or directory repair restores eligibility.

Chat creation and message submission follow the signed-in human conversation contract rather than pretending to be an agent command; users inspecting another operator's Chat through <code>team_chats.read_all</code> receive read-only behavior.

Optional avatars are UI metadata. Uploaded PNG, JPEG, or WebP images are normalized to square WebP under the Team add-on runtime directory. An OpenAI-generated preview becomes durable only after explicit acceptance. Avatars are not injected into prompts or used as core session identity.

Browser voice calls use OpenAI Realtime with a projection of the selected agent's Team context. They are not DIDWW, Asterisk, or SIP calls. Core voice-session records live outside the Team database; a call started from Chat can be projected back into that conversation as a system message.

## Troubleshooting

Use the symptom to distinguish registration from empty state and foreground UI from background runtime. Capture the enabled configuration revision, the selected Team or project, the failing page or action, and any visible run or session ID before asking for host-level help.

| Symptom | Cause to test first | Recovery |
|---|---|---|
| Add-on appears enabled but Team UI is absent | Admin Web was not restarted or packaged UI files are missing. | Restart the listed unit and verify the installed package contents. |
| UI loads but tools are missing | Gateway was not restarted with the add-on enabled. | Restart the gateway and test from an authorized Team session. |
| Workflow pack validates as unknown nodes | Trigger/effect service config did not load the add-on or pack refresh was incomplete. | Verify enabled IDs in both runtime configurations, restart, then refresh through the Add-ons API. |
| Template materialization rejects agent references | Core sessions or chosen Team agent IDs do not match the template inputs. | Correct the mapping; do not invent placeholder sessions for production use. |
| Repeated <code>409</code> writes | Another operator or runtime command is advancing Team state. | Reload narrowly, merge intentionally, and retry once with the new revision. |
| An enabled add-on has no runtime behavior | The registered pack is disabled, invalid, or its orchestrators are stopped. | Inspect the live workflow document, validation report, run history, and service state. |
| Human review is visible but actions are disabled | The signed-in user is not a current eligible assignee, lacks the directory permission, or privileged operations are locked. | Inspect assignment, group membership, scoped RBAC, and the privilege banner; do not supply another user's ID. |
| Human review says assignment unavailable | No current assignee satisfies the snapshotted reviewer groups or an audited explicit assignment. | Repair directory membership or use the authorized reassignment action; resume the same request rather than creating a replacement release. |

## Operator checklist

After enablement, an operator can confirm that **Workspace** loads, create or preview a Team, inspect its reporting structure, and make one harmless revision-checked edit. If agents should run, also confirm that a wake appears in **Operations** and that the expected Team tools and workflow nodes are available. A failure in that second group is useful evidence for an administrator even when the UI itself works.

## Administrator runbook

### Enable

1. Confirm package and UI assets.
2. Enable **Team Add-on** at <code>/#addons</code>.
3. Save and record the resulting configuration revision.
4. Restart every service named by the page.
5. Verify Admin, UI/API, MCP, and workflow registration.

### Establish a new Team

1. Create persistent core sessions with the right backend and model configuration.
2. Register or select any managed Git repository in the core registry.
3. Preview a Team template or create the Team, roles, agents, project, and assignments explicitly.
4. Configure project Git using only <code>managed_repo_id</code>; configure release policy deliberately.
5. Create the first case, goal, and root task, then confirm reporting and ownership in the Org chart.
6. Trigger or wait for a wake and verify the Operations view, session logs, and resulting command activity.

### Upgrade

1. Deploy the new package.
2. Read release notes and back up Team SQLite plus external referenced stores.
3. Restart through the normal service procedure.
4. Refresh the registered runtime pack through the Add-ons page.
5. Validate it before enabling or resuming runs.
6. Smoke-test a read, a harmless revision-checked edit, one agent wake, and one asynchronous outbox event.

## Source trail

- <code>src/evander_team_addon/__init__.py</code>, <code>addon.json</code>, and <code>ui_static/</code> — registration, mounts, and the current fifteen-view UI.
- <code>src/evander/admin/web/addons.py</code> and the add-on loader services — revision-checked enablement, runtime-pack materialization, and restart reporting.
- <code>src/evander_team_addon/api.py</code> — bootstrap, scoped views, state stream, optimistic writes, templates, avatars, Chat, and voice routes.
- <code>src/evander_team_addon/templates.py</code> — built-in catalog, preview, input binding, and materialization.
- <code>src/evander_team_addon/workflow_packs.py</code> — registered <code>team-runtime.v1</code> definition.
- <code>docs/addons.md</code> — bundled enablement and Team operator surface.
- <code>tests/test_admin_web_addons.py</code>, <code>tests/test_team_addon_api.py</code>, and <code>tests/test_team_addon_runtime.py</code> — loading, conflict behavior, pack registration, and runtime integration.
