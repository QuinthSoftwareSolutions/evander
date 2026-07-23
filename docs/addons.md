# Evander Add-ons

Add-ons are optional, opinionated layers that run on top of Evander's low-level
orchestration primitives. Evander core remains responsible for sessions,
execution backends, workflow events, effects, artifacts, communication policy,
and audit. Add-ons may consume those primitives, but must not make their domain
concepts required by core.

## Boundary Contract

Add-ons may register generic integration points:

- Admin Web API routes under `/api/addons/<addon_id>/...`
- Admin Web navigation metadata and optional UI routes under `/addons/<addon_id>/...`
- Workflow providers using the same trigger/gate/effect/query provider shape as
  `evander.providers`
- Workflow editor catalog metadata through those workflow provider registrations
- Host-side MCP tools registered through the add-on MCP registrar and governed
  by the same session, directory policy, approval, and audit machinery as core
  tools
- Static MCP tool descriptors used to merge enabled add-on tools into the core
  Sessions, MCP Tools, workflow-editor, and Tool Visibility catalogs
- Connector drivers registered by type name and invoked only through the core connector service,
  preserving directory resolution, exact session grants, approvals, auth handling, and audit
- Private runtime state under `runtime/addons/<addon_id>/`

Add-ons must not add domain-specific fields to core models such as
`SessionManifest`, nor should they require existing workflows, workflow packs,
or Admin Web pages to adopt their abstractions.

## Loading

Add-on discovery is configured with `addon_loader`:

```json
{
  "addon_loader": {
    "modules": [],
    "package_dirs": ["addons", "src"],
    "enabled_entry_points": false,
    "enabled_addon_ids": null
  }
}
```

Evander scans local add-on package directories. By default, `addons/` and
`src/` at the project root are scanned. `addons/` is operator-local and ignored
by Git; `src/` lets bundled add-ons use the same manifest shape as copied
packages. Each immediate child directory is treated as a package candidate when
it contains `addon.json`:

```json
{
  "addon_id": "example-addon",
  "module": "example_addon"
}
```

The declared `module` is loaded from that package directory, so a copied package
can carry its own Python module. The module can either live inside the package
directory, such as `addons/example-addon/example_addon/__init__.py`, or be the
package directory itself, such as `addons/example_addon/__init__.py` with
`"module": "example_addon"`. Scanned packages appear on the Admin Web
**Add-ons** page automatically, but are not loaded by default; enable them
through Admin Web so `addon_loader.enabled_addon_ids` contains the add-on id
before restarting the listed services.

External packages can also expose Python entry points in the
`evander.addons` group. Entry-point loading is disabled by default and
must be explicitly enabled.

Admin Web exposes an **Add-ons** page for first-class add-on visibility and
restart-gated enablement. The page edits `addon_loader.enabled_addon_ids` in
host config and trigger-orchestrator config, while leaving module lists and
entry-point discovery in advanced configuration. Saving add-on enablement does
not hot-load or hot-unload add-on routes, workflow providers, or MCP tools;
restart the listed services for changes to take effect.

Open the page at `/#addons`. It shows discovered packages, enabled and loaded
state, pending restart state, load errors, configuration mismatches, registered
capabilities, runtime workflow-pack state, and the services affected by a
change. Enablement is host-global, not scoped to the directory selected in the
Admin Web header. When an enabled add-on registers a runtime workflow pack,
the Add-ons save path materializes it through the revision-checked workflow
configuration service. Use the page's **Refresh runtime pack** action (or the
equivalent Admin API action) to refresh that materialized pack. Never write a
live workflow-pack file directly.

Disabling an add-on means it will not load after restart. It does not delete
private state under `runtime/addons/<addon_id>/`.

An add-on module should expose `register_addon()` and return either an
`AddonRegistration` or a compatible mapping:

```python
from starlette.routing import Route


def register_addon():
    return {
        "addon_id": "team-addon",
        "display_name": "Team Add-on",
        "admin_navigation": {
            "label": "Teams",
            "short_label": "Teams",
            "description": "Team-oriented add-on surface.",
            "ui_path": "/addons/team-addon/",
        },
        "admin_api_routes": [Route("/health", health, methods=["GET"])],
        "admin_ui_routes": [Route("/", index, methods=["GET"])],
        "mcp_tools": register_mcp_tools,
        "mcp_tool_catalog": [
            {
                "name": "team_example",
                "label": "Team example",
                "description": "Run the add-on-owned Team example tool.",
                "group": "Team Add-on",
            }
        ],
        "workflow_providers": {
            "effect_providers": {
                "effect:team:example": ExampleEffectProvider,
            },
            "workflow_editor_catalog_entries": [
                {
                    "kind": "effect",
                    "type": "effect:team:example",
                    "label": "Team example",
                    "description": "Run the add-on owned Team example effect.",
                    "fields": [],
                },
            ],
        },
        "connector_drivers": {
            "example_service": ExampleServiceConnectorDriver(),
        },
    }
```

Connector-driver extensions implement the small async driver protocol documented in
[Agent Connectors](connectors.md). Add-ons should use a driver only when the built-in declarative
REST or filesystem drivers cannot express the integration. A custom driver never receives a
session's unrestricted environment or arbitrary agent headers; the core resolves configured auth
and validates the operation before invoking it. Adding or removing a connector driver requires a
gateway restart.

## Storage

Use the add-on storage helpers from `evander.addons` so state remains
scoped and removable:

```python
from evander.addons import addon_json_object_store

store = addon_json_object_store(runtime_dir, "example-addon", "state.json")
```

The helper rejects malformed add-on ids, absolute paths, and traversal segments.
Removing `runtime/addons/<addon_id>/` must not corrupt Evander core state.

## Azure DevOps Activity Add-on Guidance

The bundled `evander_ado_activity_addon` is a headless add-on: it intentionally
does not register an Admin Web navigation tab. It provides host-side MCP tools
and workflow providers for durable Azure DevOps activity indexing and queries.
Its presence is therefore verified on the core **Add-ons** page by its loaded
state and registered capabilities, not by looking for a separate navigation
item.

Enable `ado-activity-addon` through `/#addons` or the equivalent Admin API and
restart the services listed there. Because the add-on registers MCP tools, that
restart set includes `evander-gateway.service` as well as Admin Web and the
trigger/effect orchestrators. This add-on complements, but does not replace,
`evander-ado-worker`; PR feedback polling and completion pickup remain owned by
that worker.

## MSSQL Seed Add-on Guidance

The bundled `evander_mssql_seed_addon` package provides an optional seed-restore
layer for container-agent MSSQL workflows. It is intentionally an add-on rather
than a Core bootstrap feature: Core still owns session creation, worker MSSQL
database provisioning, and cleanup, while the add-on restores an
operator-approved `.bak` file into that Core-owned database.

Enable it with:

```json
{
  "addon_loader": {
    "modules": ["evander_mssql_seed_addon"],
    "enabled_entry_points": false,
    "enabled_addon_ids": ["mssql-seed-addon"]
  }
}
```

Every tool exposed by an add-on registrar should have one static
`mcp_tool_catalog` descriptor. When descriptors are present, gateway startup
verifies that the declared and registered tool-name sets match. Admin Web groups
the descriptors under their optional `group`, falling back to the add-on
navigation label (or display name for a headless add-on), and merges them with
core tools. Operators can then select the tools on session create/edit,
configure inherited approval or deny rules on **MCP Tools**, and select them in
workflow tool fields without typing an unknown name. Catalog presence does not
grant the tool: session allowlists, directory policy, invocation approval, and
audit still apply.

When enabled, Admin Web shows an `MSSQL Seeds` tab backed by
`/api/addons/mssql-seed-addon/...` and `/addons/mssql-seed-addon/`. The add-on
stores sidecar and seed catalog state under
`runtime/addons/mssql-seed-addon/state.json`.

The restore sidecar runs near the worker SQL Server instance and needs SQL
Server admin access plus a bearer token. For worker-VM deployments, enable it
through the worker inventory. The sidecar is disabled by default and is only
installed/started when both worker MSSQL and
`evander_worker_mssql_seed_sidecar_enabled` are true:

```yaml
evander_worker_mssql_enabled: true
evander_worker_mssql_seed_sidecar_enabled: true
evander_worker_mssql_seed_sidecar_token: "{{ vault_evander_worker_mssql_seed_sidecar_token }}"
evander_worker_mssql_seed_sidecar_port: 11990
evander_worker_mssql_seed_sidecar_backup_dir: "/var/lib/evander/mssql-seeds"
```

The worker playbook renders `config/mssql-seed-sidecar.env`, installs
`evander-mssql-seed-sidecar.service`, creates the backup directory for approved
`.bak` files, and verifies `/healthz`. Restrict network reachability so only
the control plane can call the sidecar port.

For a manual foreground run, use:

```bash
EVANDER_MSSQL_SEED_SIDECAR_TOKEN='...' \
EVANDER_WORKER_MSSQL_ADMIN_PASSWORD='...' \
evander-mssql-seed-sidecar --host 0.0.0.0 --port 11990
```

Operators configure:

- sidecars with `sidecar_id`, `base_url`, `token_env`, and optional
  `restore_timeout_seconds` for large `.bak` restores
- approved seeds with `seed_alias`, `sidecar_id`, and absolute worker-side
  `backup_path`

Agents do not receive arbitrary backup paths. Sessions that are allowed to use
the add-on MCP tools call:

- `mssql_seed_provision_database` with `logical_name` and `seed_alias`
- `mssql_seed_reset_database` with `logical_name`
- `mssql_seed_list_databases`

Provisioning first calls the Core container-worker `mssql_provision_database`
path, so the worker records the physical database and session login. The add-on
then asks the sidecar to run `RESTORE DATABASE ... WITH REPLACE` into that
database and reattach the session login as `db_owner` inside the restored
database. Reset uses the same restore path against the tracked seed alias.
Worker session deletion still uses Core worker MSSQL cleanup for databases and
logins.

## Skatteverket Add-on Guidance

The bundled `evander_skatteverket_addon` package provides an optional
Skatteverket accounting and filing surface. Enable it with:

```json
{
  "addon_loader": {
    "modules": ["evander_skatteverket_addon"],
    "enabled_entry_points": false,
    "enabled_addon_ids": ["skatteverket-addon"]
  }
}
```

When enabled, Admin Web shows a `Skatteverket` tab backed by
`/api/addons/skatteverket-addon/...` and `/addons/skatteverket-addon/`. The
add-on keeps canonical state in `runtime/addons/skatteverket-addon/state.db`.
It exposes add-on-owned workflow nodes under `query:skatteverket:*` and
`effect:skatteverket:*`, plus host-side MCP tools named `skatteverket_*`.

The add-on is intentionally host-side:

- Skatteverket credentials, API keys, certificates, and OAuth metadata stay on
  the control plane.
- Agents only receive MCP/workflow results, never raw secrets.
- Submission tools require an exact draft-revision approval request before a
  stored draft can be submitted.
- APIs that are represented in the catalog but lack a configured adapter fail
  closed with `not_implemented_for_v1` or `blocked` status.

Skatteverket production access still requires external onboarding, agreements,
API keys, OAuth2 setup, and in some cases e-legitimation or
organisationslegitimation. Configure connection metadata through the add-on API
after that onboarding is complete.

## Bokio Add-on Guidance

The bundled `evander_bokio_addon` package provides an optional assisted
bookkeeping surface for Bokio private integrations. Enable it with:

```json
{
  "addon_loader": {
    "modules": ["evander_bokio_addon"],
    "enabled_entry_points": false,
    "enabled_addon_ids": ["bokio-addon"]
  }
}
```

When enabled, Admin Web shows a `Bokio` tab backed by
`/api/addons/bokio-addon/...` and `/addons/bokio-addon/`. The add-on stores
prepared bookkeeping actions under `runtime/addons/bokio-addon/` and exposes
workflow nodes under `query:bokio:*` and `effect:bokio:*`, plus host-side MCP
tools named `bokio_*`. Its static tool descriptors also appear in the `Bokio`
group on **Sessions**, **MCP Tools**, and workflow tool selectors.

Configure the private integration on the host:

```bash
export BOKIO_COMPANY_ID=<company-guid>
export BOKIO_INTEGRATION_TOKEN=<private-integration-token>
```

Optional provider config can override the token and company-id environment
variable names with `provider_state.bokio.integration_token_env` and
`provider_state.bokio.company_id_env`; do not store the token value in config,
runtime state, guest images, or session manifests.

The Bokio add-on is intentionally an assisted-bookkeeping layer:

- Agents can inspect company information, accounts, journal entries, invoices,
  customers, suppliers, supplier invoices, uploads, and prepared actions.
- `bokio_list_customers` and `bokio_list_suppliers` expose Bokio's paginated
  customer and supplier directories, while `bokio_get_customer` and
  `bokio_get_supplier` retrieve one record by its Bokio ID. Customer queries can
  filter on `name`, `type`, `vatNumber`, `orgNumber`, and `modifiedDateTime`;
  supplier queries can filter on `name`, `vatNumber`, and `orgNumber`. Each tool
  requires its own session grant. Bokio currently marks the supplier API as
  preview, so supplier response fields and behavior may change.
- `bokio_list_uploads` queries documents already stored remotely in Bokio, with
  Bokio pagination and optional `description` or `journalEntryId` query filters.
  `bokio_get_upload` reads one remote upload's metadata, while
  `bokio_download_upload` downloads its file into a session-owned communication
  artifact. Only a complete HTTP `200` response is staged; redirects, partial
  responses, and interrupted or truncated transfers are rejected. Downloads
  require their exact MCP tool grant, are bounded to 100 MiB, and do not
  automatically create durable local evidence; call
  `bokio_import_evidence_artifact` explicitly when local retention is required.
- Receipt and invoice intake is connector- and communication-driven. An agent can
  import a PDF, JPEG, or PNG returned as an artifact by a granted filesystem/REST
  connector, or by `communication_download_attachment`, with
  `bokio_import_evidence_artifact`. The add-on makes a durable, content-deduplicated
  evidence record and retains artifact provenance without retaining a connector
  credential or a guest-visible host path.
- The preferred source for vendor receipts is a dedicated billing mailbox. A
  scheduled bookkeeping workflow can use `email_list_unread`,
  `communication_list_attachments`, and `communication_download_attachment`; the
  last tool returns the artifact that the Bokio import tool accepts. Use a
  filesystem connector for a known file in a controlled host dropbox of manually
  downloaded or scanned documents. A REST connector may provide vendor cost data
  or a fixed document download endpoint, but it does not replace the email
  integration for user-delegated mailboxes.
- `bokio_list_evidence` and `bokio_get_evidence` expose metadata, provenance, and
  Bokio association state from Evander's durable local evidence store; they do not
  query Bokio's remote uploads. `bokio_stage_evidence_for_workspace` can make
  previously retained evidence available to a new session as an artifact.
- `bokio_check_journal_duplicates` scans every page of the journal-entry list and
  matches a non-empty combination of `date`, `account`, `amount`, `title`,
  `invoice_number`, and `receipt_number`. It returns the number of entries
  inspected, matching entry IDs, a source fingerprint, and a durable
  `bokio-duplicate-check:...` evidence reference. Equivalent checks reuse evidence
  for 15 minutes by default; set `refresh=true` to force a new scan. The durable
  records live in
  `runtime/addons/bokio-addon/duplicate-checks/duplicate-checks.db`.
- `bokio_attach_evidence` uploads managed evidence with a Bokio journal-entry ID,
  linking it to an existing transaction or entry. `bokio_create_journal_entry_with_evidence`
  creates an entry and attaches one or more evidence records with a durable
  operation key. These outward writes follow the standard per-tool MCP policy;
  enable autonomous use only for sessions that are explicitly permitted to make
  those calls.
- `bokio_prepare_journal_entry` accepts optional `evidence_ids` from the durable
  evidence store. The prepared action keeps that exact evidence list for operator
  review, and approved execution uses the same durable create-and-attach ledger as
  the direct evidence-aware write. The action becomes `executed` only after every
  attachment succeeds. If the journal entry succeeds but an upload fails, **Retry**
  resumes the evidence upload without creating another journal entry.
- If an evidence upload has an uncertain failure, its state becomes
  `needs_attention`; an agent can make an explicit `retry_if_failed` call after
  reconciling the existing entry. A definite journal-create rejection becomes
  `failed` and can be retried with `retry_if_failed`. An uncertain journal create
  can be retried only after checking Bokio and setting `confirm_not_created`; the
  add-on never blindly recreates a journal entry whose creation outcome is unknown.
- Agents can prepare journal entries, supplier invoices, and bank payments as
  local add-on actions for operator approval.
- `bokio_create_supplier_invoice` is the deliberately narrow autonomous write
  path. It requires a caller-supplied `operation_key` and stores the exact
  payload digest and outcome in `runtime/addons/bokio-addon/operations.db`.
  Repeating the same key and payload returns the saved result without another
  Bokio call; reusing a key for a different payload is rejected. A definite
  pre-request or client rejection becomes `failed` and requires
  `retry_if_failed=true`. A timeout, server failure, or other uncertain result
  becomes `needs_attention`; retry is blocked until Bokio has been checked and
  the caller explicitly sets `confirm_not_created=true`.
- Direct operations and prepared actions record the process that owns an
  `executing` claim. **Confirm absent** never steals a claim from a live process;
  wait for that attempt to finish. A stranded claim can be recovered only after
  reconciling Bokio, explicitly confirming that the object was not created, and
  verifying that the recorded owner process has exited. Missing or untrustworthy
  ownership fails closed and requires reconciliation or repair outside the retry
  control.
- Document uploads read only from add-on-owned staging directories under
  `runtime/addons/bokio-addon/uploads/`; MCP uploads use
  `uploads/sessions/<session-id>`, workflow uploads use
  `uploads/scopes/workflows` unless a `session_id` is supplied, and Admin API
  uploads use `uploads/scopes/admin`.
- The Bokio Admin tab is the prepared-action review workspace. Pending actions
  use one atomic **Approve & execute** operation; approved legacy actions can be
  executed or rejected separately. Definite failures expose both **Retry** and
  **Reject**, preserving their failure details and transition history. Uncertain
  outcomes expose **Confirm absent & retry** only after reconciliation, while a stranded
  `executing` action uses **Confirm absent & recover** and still refuses to take
  over a live or unknown owner. Prepared evidence IDs and final evidence results
  are shown alongside the payload, result, error, source, and transition history.
  All Admin mutations require an Admin owner with privileged operations unlocked.
- Prepared-action execution tools execute only approved prepared actions and
  should also be policy-gated with MCP invocation approval for production
  sessions.
- Bank payments created through Bokio still require a signatory to log in to
  Bokio and sign with BankID before submission to the bank.
- Supplier invoice support uses Bokio's preview API surface and should be
  treated as less stable than company, account, journal-entry, invoice, upload,
  and SIE reads.

For an autonomous bookkeeping session, allow the relevant email tools or the
generic `list_connectors` and `call_connector` tools with exact connector-operation
grants, then allow the required `bokio_*` tools. To permit supplier invoices
without per-call approval, allow `bokio_create_supplier_invoice` on the session
and leave that tool without an inherited `require_approval` rule. Do not grant
`bokio_create_approved_bank_payment`; alternatively keep it denied or approval
required. There is no autonomous direct bank-payment MCP tool, and every Bokio
bank payment still requires BankID signing. Keep
`communication_copy_artifact_to_workspace` separate: it is needed only when the
agent must inspect the original file in its workspace, not to persist or attach
the document to Bokio.

## Team Module Guidance

A hierarchical agent-team module should live as an add-on outside Evander core.
Team, manager, project lead, task, deliverable, review, and release-package
schemas belong in that package. Only generic hooks that are useful to other
add-ons should be proposed for Evander core.

The bundled `evander_team_addon` package provides the initial state-model layer
for this pattern. It ships an `addon.json` manifest beside its `__init__.py`, so
it uses the same scanned-package contract as copied add-ons in a source
checkout. It is still disabled by default; enable `team-addon` from Admin Web
or set `addon_loader.enabled_addon_ids: ["team-addon"]`. Explicit
`addon_loader.modules: ["evander_team_addon"]` and `evander.addons` entry-point
loading remain supported compatibility paths. Its canonical state lives in
relational SQLite under `runtime/addons/team-addon/state.db`; a legacy
`runtime/addons/team-addon/state.json` file is imported once when the database
is first initialized. Evander session metadata remains a non-canonical
integration index.

When the Team Add-on is enabled, Admin Web shows an optional `Teams` tab. The
tab is served from the add-on's own `/addons/team-addon/` UI route and uses the
add-on API under `/api/addons/team-addon/`; it does not add team-specific views
or models to Evander core Admin Web pages. The current UI has fifteen top-level
views: **Workspace**, **Operations**, **Delivery**, **Memory**, **Projects**,
**Task board**, **Cases**, **Teams**, **Chat**, **Mail**, **Activity**, **Governance**,
**Planning**, **Timeline**, and **Org chart**. Review requests, review profiles,
deliverables, release packages, and Git promotion state are grouped inside
**Delivery**. Team setup, persistent agents, roles, and stand-down controls are
grouped inside **Teams** rather than separate People or Roles tabs.

The `Teams` tab includes an org-chart hierarchy view for each team. It renders
the manager, subordinate agents, nested reporting lines, and unreachable or
malformed hierarchy references without changing persisted state. Agent nodes
show `display_name` when it is present and fall back to the stable agent id;
the create-team and register-agent forms expose optional display-name fields
for managers and additional agents. The create-project form assigns an existing
registered team agent as project lead instead of collecting a session ID,
display name, or role name inline. Project leadership is stored on the project,
so a team manager can also lead a project without changing that agent's primary
team-manager role or reporting line. Hover
or keyboard focus on an agent node reveals operational details from the add-on
state payload, including session, project, reports-to, task, inbox, runtime
wake, and ephemeral-capacity information when those records are available.

Persistent agents may also have an optional Team Add-on avatar. Avatars are
operator-facing UI metadata only: they are stored in Team Add-on state and
add-on-private image files under `runtime/addons/team-addon/avatars/`, and are
not mirrored into session metadata, prompts, wake context, or core Evander agent
identity. Operators can upload PNG/JPEG/WebP images, which the add-on normalizes
to square WebP assets, or generate an OpenAI-backed preview and explicitly
accept it before it becomes the saved avatar.

The Teams view can start a direct browser voice call to the selected persistent
agent. The add-on prepares an OpenAI Realtime browser session with the selected
agent's current team, role, task, inbox, review, deliverable, runtime, and
visible tool context; it does not use DIDWW, Asterisk, or SIP. Calls are
recorded in `runtime/voice-sessions-store.json`, and terminal calls schedule the
same voice-session reconciliation wake used by other projected voice sessions.
When the voice projection needs details outside the projected context, it can
delegate a durable `query_origin_agent` request back to the originating text
agent.

The Chat tab stores conversations as one human operator plus one persistent
agent. Operators see their own conversations by default. Users with
`team_chats.read_all` may select another operator's chat history for read-only
inspection; the UI labels that mode separately from "My chats" and blocks
sending while inspecting. Team chat messages recorded by the Admin API carry
human-operator metadata, and chat wake prompts tell the agent that the source is
a human operator rather than another Team agent.

Built-in Team Chat supports file and image attachments in both directions. An
operator can attach up to the effective per-message limit when sending a Chat
message; the add-on stores the bytes privately under
`runtime/addons/team-addon/attachments/`, records metadata and SHA-256 in Team
SQLite state, and copies the files into the recipient agent workspace under
`/home/agent/team-chat-attachments/<message-id>/` before the Chat wake. Attachment
contents are untrusted operator input. Authorized Chat readers can download files,
and the UI renders permitted raster images inline.

Agent-originated files use `team_stage_chat_attachment` first, then pass the
returned attachment IDs to `team_reply_to_chat` or
`team_send_operator_chat_message`. Staging accepts only regular files under
`/home/agent`, is bound to the calling session, and expires after 24 hours. The
root policy enables both directions, allows five common document/image files up
to 10 MiB each, and requires exact approval for an agent send. Configure inherited
overrides from Chat's **Files** dialog or the add-on-owned
`GET`/`PUT /api/addons/team-addon/chat/attachment-policy` routes. These rules are
deliberately independent of core communication-file policy and Microsoft Teams
file policy; changing one does not broaden either of the others.

The Mail view is the Agent Mail surface for agent-to-agent messages. It is
separate from operator Chat: use Chat for a signed-in human's conversation with
one persistent agent, and use Mail to inspect the sent and received projections
over Team's single durable inbox collection, including handling state and
message context. `team_chats.read_all` applies only to other operators' Chat
histories; it does not grant broader Agent Mail access.

The separate Activity view is a redacted operational tail over Team's durable
dispatch-activity ledger. It shows dispatch start/finish and Team tool
success/failure events without tool arguments, tool results, session IDs, or
model reasoning. Operators can filter it by team, agent, dispatch, status, tool,
or search text. The same data is available from
`GET /api/addons/team-addon/dispatch-events` and
`evander-cli team dispatch-tail [--follow] [--json]`. Activity rows are retained
for 30 days; Mail remains the correspondence history and is not merged into this
operational stream.

### Team Memory

The Team Add-on supports durable team-owned memory records for decisions,
preferences, facts, runbooks, handoffs, research notes, postmortems, and other
long-lived context. Canonical memory records live in Team SQLite state under
`TeamState.memories`; Qdrant is a derived per-team semantic index, not the
source of truth. Collection names are generated from the Team id, so two Teams
do not share a semantic memory collection even when they work in the same
directory.

Memory records are intentionally compact in broad state payloads. The Admin UI
Memory tab, Team state payloads, and memory search results include titles,
summaries, tags, scope, refs, and status, but omit the optional long `body`.
Detailed bodies are available through explicit memory detail APIs or tools.
Normal Team wake prompts do not include memories automatically; agents must call
`team_search_memory` or workflow automation must use `query:team:memory` when
recall is useful.

Agents can record memory with `team_record_memory` and archive or supersede it
with `team_archive_memory`. Records may be team-global, project-scoped,
agent-private, or governance-scoped. Bulky evidence should be published as
artifacts or artifact-space refs and linked from the memory record rather than
stored in the body.

Operators and workflows can use:

- `GET /api/addons/team-addon/memory`
- `POST /api/addons/team-addon/memory`
- `GET /api/addons/team-addon/memory/{memory_id}`
- `PATCH /api/addons/team-addon/memory/{memory_id}`
- `POST /api/addons/team-addon/memory/{memory_id}/archive`
- `query:team:memory`
- `effect:team:index-memory`
- `effect:team:reindex-memory`

Team roles are first-class state records under `TeamState.roles`. Agents store a
`role_id` instead of a free-form role string, and role records carry the display
name, optional project scope, default ephemeral policy, and permission list for
that role. The Admin API exposes role CRUD at `/api/addons/team-addon/roles`,
and the bundled UI includes role create/edit forms inside the `Teams` view;
there is no separate top-level `Roles` view. Bundled
permissions include `create_root_task`, `prepare_bookkeeping_action`,
`review_bookkeeping_action`, `request_payment_preparation`, and
`request_release`; release authorization checks the agent's role permission
rather than an exact role name. The built-in `auto-dev-squad` template grants
`request_release` to its project-scoped release-operator role, and the
`accounting-department` template grants the bookkeeping permissions to its
preparation and review roles.

### Case And Ticket Tracking

The Team Add-on has a first-class case domain for intake and lifecycle tracking.
The internal object is always named `case`. External systems such as GitHub,
Jira, Zendesk, email, or support tools are represented only as optional
`source` and `source_ref` fields on the case. The initial implementation does
not synchronize external ticket systems; operators and agents may preserve the
external reference so a later integration can reconcile against it.

Cases are containers for intake context, ownership, status, priority, tags, and
linked execution tasks. Tasks remain the unit of work assigned to agents. A task
may set `case_id`, and a case may list `task_ids`; the repository validates that
the link is bidirectional, same-team, and same-project when the case has a
project scope.

Case status values are `open`, `triage`, `in_progress`, `waiting`, `resolved`,
and `closed`. Case priority values use the existing Team inbox priority set:
`low`, `normal`, and `high`.

The Admin API exposes:

- `POST /api/addons/team-addon/cases`
- `PATCH /api/addons/team-addon/cases/{case_id}`
- `case_id` on `POST /api/addons/team-addon/tasks`
- `case_id` on `PATCH /api/addons/team-addon/tasks/{task_id}`; send `null` to
  clear the link

The Teams UI includes a dedicated `Cases` tab, a workspace cases panel, a
`Create case` setup form, case selectors on task create/update forms, and case
edit support in the generic record editor.

Agent wake leases and the Team state query include visible open cases. Normal
Team MCP tools let agents create and update case records, link cases to tasks,
and attach new work to an existing case.

Workflow automation can manage cases directly through the add-on-owned
`effect:team:create-case` and `effect:team:update-case` nodes. These effects use
the same repository validation as the Admin API, may link existing `task_ids`,
and are intended for ticket/accounting intake bridges that should create or
refresh Team cases before waking persistent Team agents.

Workflow automation can also create concrete execution work with
`effect:team:create-task`. The node is operator-authored, requires `team_id`,
`project_id`, `owner_agent_id`, `priority`, `title`, and `description`, and uses
the Admin API task creation semantics: a missing `task_id` creates a task, while
an existing matching task id updates/reprioritizes that task.

Workflow automation can enforce deliverable review handoffs without relying on
agent prompt discipline. `trigger:team:deliverable-created` emits newly created
Team deliverables, suppressing already-existing deliverables when a trigger is
first enabled. `effect:team:create-review-request` creates a review request for
an existing deliverable, requires an `actor_agent_id` with normal Team review
request authority, validates the optional review profile, creates the reviewer
inbox item, and publishes the reviewer wake event. Use one effect node per
required reviewer or review profile; the existing review cascade keeps the
deliverable pending until all attached review requests are accepted.

The Team Add-on also registers its stable internal event names in the workflow
editor catalog, so `trigger:event.event_type` suggests the event names without
requiring code inspection. Current Team events are
`team.chat.message_queued`, `team.agent.wake_requested`,
`team.ephemeral.requested`, `team.ephemeral.completed`,
`team.operator.escalation_requested`, `team.git.promotion_requested`, and
`team.git.release_recovery_requested`. Use these for generic Team event
workflows; use `trigger:team:deliverable-created` for deliverable-created
workflows because it owns the deliverable cursor and startup baseline behavior.

Admin API write routes require `expected_revision`. Missing or null revisions
return `400`, stale revisions return `409`, and successful writes advance the
Team state revision. Workflow effects and agent-action application may omit
`expected_revision` when the workflow is operating as the single writer for that
step; they still honor the revision check when a value is provided.

### Template Catalog And Setup Flow

The bundled Team Add-on exposes a built-in template catalog at
`/api/addons/team-addon/templates`. The Admin Web `Teams` tab includes a
`Setup from template` panel that loads that catalog, previews the selected
template, and materializes it with one optimistic request to
`POST /api/addons/team-addon/templates/{template_id}/materialize`.

The built-in catalog currently includes:

- `auto-dev-squad`: an opinionated
  manager/project-lead/implementer/reviewer/release-operator squad for reviewed
  auto-dev delivery.
- `accounting-department`: an accounting operations department with an
  accounting manager, bookkeeping lead, bookkeeping clerk, invoice reviewer,
  payment preparer, and tax-compliance reviewer. It is intended for
  Bokio/Skatteverket-assisted bookkeeping coordination and deliberately omits
  Git setup fields.

The preview shows the planned role hierarchy, review rubric, project goal,
starter tasks, optional Git fields, and the concrete inputs that must be
supplied before creation.

Required inputs are:

- `team.id` and `team.name`
- `team.manager_agent_id` and `team.manager_session_id`
- `project.id` and `project.name`
- `project.lead_agent_id` and `project.lead_session_id`
- `agents.implementer.agent_id` and `agents.implementer.session_id`
- `agents.reviewer.agent_id` and `agents.reviewer.session_id`
- `agents.release_operator.agent_id` and `agents.release_operator.session_id`

The generalized request shape may instead provide template role keys under a
top-level `roles` object, for example `roles.manager.session_id` and
`roles.bookkeeping_clerk.session_id`. If `team.id`, `project.id`, or individual
role `agent_id` values are omitted, the repository generates stable ids from
the team, project, and role labels.

Optional inputs are:

- `team.operator_communication_target_id`
- `project.git.managed_repo_id`
- `project.git.base_branch` and `project.git.canonical_release_branch_prefix`

The Team Add-on setup, project-create, and project-edit forms include an
`Import Git repo` action beside the Git fields. It accepts a local checkout or
canonical origin URL as transient `repo_source`, registers or refreshes the
directory-scoped managed bare repository, and fills the form with its
`managed_repo_id` and detected default branch. URL/provider identity remains in
the central registry rather than Team state. Import does not save the project
by itself; review the managed ID and branch policy, then submit normally.

The template uses a fixed hierarchy:

- `manager -> project_lead -> implementer`
- `manager -> project_lead -> reviewer`
- `manager -> project_lead -> release_operator`

It also seeds one project goal, two initial tasks, and one review profile with
criteria for `scope`, `tests`, `risk`, and `handoff`. The accounting template
seeds one bookkeeping-operations goal, five starter tasks for receipt,
supplier-invoice, payment, and tax-compliance work, and one review profile with
criteria for supporting documentation, Bokio mapping, tax treatment, and the
human-approval boundary. Generated goal, task, and review-profile ids are
prefixed with the concrete `project.id`, so separate projects can be
materialized from the same template without colliding.

Setup is atomic. The request must include `expected_revision`; stale revisions
fail without changing state, and duplicate ids are rejected before any write.
The resulting records live only in Team Add-on state. They do not create real
Evander sessions, VMs, Git repositories, Bokio entries, payments, or tax filings.

Example Auto-Dev compatibility request shape:

```json
{
  "expected_revision": 0,
  "team": {
    "id": "team-auto-dev-squad",
    "name": "Auto-Dev Squad",
    "manager_agent_id": "manager-auto-dev-squad",
    "manager_session_id": "session-manager-auto-dev-squad"
  },
  "project": {
    "id": "project-auto-dev-squad",
    "name": "Reviewed Auto-Dev Delivery",
    "lead_agent_id": "project-lead-auto-dev-squad",
    "lead_session_id": "session-project-lead-auto-dev-squad"
  },
  "agents": {
    "implementer": {
      "agent_id": "implementer-auto-dev-squad",
      "session_id": "session-implementer-auto-dev-squad"
    },
    "reviewer": {
      "agent_id": "reviewer-auto-dev-squad",
      "session_id": "session-reviewer-auto-dev-squad"
    },
    "release_operator": {
      "agent_id": "release-operator-auto-dev-squad",
      "session_id": "session-release-operator-auto-dev-squad"
    }
  }
}
```

### Team Activity Timeline

The Teams tab includes a derived `Timeline / Audit` view backed by the current
`TeamState` snapshot. `GET /api/addons/team-addon/timeline` returns a read-only
timeline; it is not an append-only audit log, so overwritten intermediate
changes are not reconstructed and legacy records without timestamps stay
timestamp-missing.

The endpoint accepts these filters:

- `team_id`
- `project_id`
- `agent_id` matches the entry's actor or any related agent id
- `type` supports repeat values or comma-separated lists
- `status` supports repeat values or comma-separated lists
- `attention_needed=true|false`

Each entry carries JSON-safe fields for `id`, `type`, `category`,
`timestamp`/`timestamp_source`, `timestamp_missing`, `actor_agent_id`,
`related_agent_ids`, `team_id`, `project_id`, the flat related ids
(`task_id`, `deliverable_id`, `review_request_id`, `inbox_item_id`,
`action_record_id`, `ephemeral_assignment_id`, `git_promotion_id`,
`release_package_id`), `status`, `attention_needed`, and `summary`.

The view includes these entry families:

- task created and updated entries
- inbox item created and handled or updated entries when `updated_at` differs
- deliverable created entries
- review request created entries, with `timestamp: null` and
  `timestamp_missing: true` for legacy requests without `created_at`
- review decision entries at `decision_at`
- action record created entries
- ephemeral assignment requested entries and lifecycle state entries when
  `updated_at` differs
- git promotion requested and result entries
- release package created entries, plus published or blocked state entries

Runtime attention hides failed action records after a later successful action
from the same agent. Operators can also dismiss a failed-action attention card;
this sets dismissal metadata on the action record for audit instead of deleting
the record.

Entries sort chronologically ascending when they have a timestamp. Entries with
missing timestamps sort after dated entries, with stable secondary ordering by
category, type, and id. In the UI, missing timestamps render as `Timestamp
unavailable`.

When enabled, the Team Add-on also registers an optional runtime-loop provider
set:

- `effect:team:wake-due-agents` leases due persistent agents, prompts their
  Evander sessions, and mirrors a compact `metadata.team_addon` index into the
  session manifest. When the agent's project has `project.git` configured, the
  wake also prepares the project repo through `effect:agent:clone-repo` before
  the resume prompt and includes the prepared guest checkout path in the Team
  context. Repo preparation is reported in the wake node outputs as
  `prepared_project_repos` or `failed_project_repo_preparations`; it is not a
  separate workflow node because one wake tick can lease multiple agents.
  Static Team operating rules are seeded as a managed Team Add-on section in
  `/home/agent/AGENTS.md`, so repeated same-context wakes only need to carry the
  dynamic dispatch context. Team state changes are made through Team-owned MCP
  tools exposed only to the appropriate wake/session class.
  Each persistent agent can set `context_clearing_policy` to `off`, `idle`, or
  `every_wake`. `idle` preserves the original automatic behavior by rotating
  after real work has completed and the agent becomes idle. `every_wake` starts
  each otherwise-eligible Team wake with a fresh context without bypassing
  low-priority batching or waking idle agents by itself.
  `low_priority_wake_after_cycles` defaults to `4`; low-priority inbox items
  count Team wake evaluations and are batched until the threshold is reached
  unless another wake reason delivers them earlier.
  High-signal wake requests, such as high/normal pending inbox,
  ready task handoffs, review requests, rework requests, and remaining work
  found when a dispatch finalizes, emit a debounced
  `team.agent.wake_requested` internal event. The default debounce is 10
  seconds, after which the wake-request path leases only the requested agent
  with `respect_wake_interval=false`. Active leases, stand-downs, cost guards,
  no-progress suppression, and low-priority batching still apply.
- Team-owned MCP tools mutate Team state immediately and atomically during the
  wake. They infer the caller identity, active Team lease, dispatch, and worker
  assignment from the MCP connection/session and do not accept agent-facing
  dispatch identity fields.
- Session completion is finalized by `effect:team:finalize-agent-dispatch`,
  which closes the persistent dispatch lease from `session_id`, `dispatch_id`,
  and `agent_id`, then computes progress or no-progress from the dispatch
  activity ledger. Individual MCP tool calls do not close the dispatch lease.
- `effect:team:create-operator-directive` turns an operator request from
  Discord, Teams, Admin Web, or another workflow into a durable
  `operator_directive` inbox item. The item defaults to the team manager,
  resets the manager's no-progress throttle, and is handled by the next normal
  Team wake through ordinary Team MCP tools.
- `effect:team:create-task` directly creates or updates an operator-authored Team
  task for a specific project owner. Agent-authored task creation remains through
  `team_create_task` and `team_create_tasks` during persistent Team wakes.
- `team_create_operator_directive` is the add-on-owned MCP tool for governance
  managers. It creates the same durable `operator_directive` inbox item, but
  only for member Teams of a governance group managed by the caller, and only
  for the target Team manager inbox.
- `team_mark_governance_agenda_handled` is the add-on-owned MCP tool for
  governance managers to mark agenda items in their own governance groups
  handled after they have decided, routed any needed directive, or determined no
  action is needed.
- Updating a governance group's member Teams records a pending `policy_decision`
  agenda item with the added, removed, previous, and current member Team IDs.
  This clears any delayed governance wake for that group so the manager receives
  a fresh governance prompt with the new member-team summaries on the next Team
  runtime evaluation.
- `query:team:session-routing` classifies a completed Evander session as a
  persistent team agent, ephemeral assignment, ignored non-Team dispatch, or
  unknown session before completion finalization. Persistent agents are finalized
  only when the completed dispatch id matches the active Team runtime lease, so
  ordinary Discord/Teams wake completions do not affect Team dispatch state.
- `query:team:memory` returns compact memory records from canonical Team state
  or, when semantic search is enabled, from the Team's Qdrant collection.
- `effect:team:index-memory` and `effect:team:reindex-memory` refresh the
  derived Team Qdrant memory collection from canonical Team state.
- `effect:team:mark-ephemeral-spawned`,
  `effect:team:accept-ephemeral-deliverable`, and
  `effect:team:mark-ephemeral-cleaned-up` track the ephemeral worker lifecycle
  around normal Evander `effect:agent:create`, `effect:agent:prompt`, and
  `effect:agent:destroy` nodes.
- `query:team:state` returns filtered add-on state for workflows or UI.

These node types are add-on-owned. They are not present unless the add-on is
loaded, and their workflow-editor catalog entries are registered by the add-on's
provider registration rather than hard-coded into Evander core. The packaged
`evander_team_addon.workflow_packs.TEAM_RUNTIME_V1` definition is a current-schema
workflow pack for the intended cron plus agent-completion loop. Enablement from
the Add-ons page materializes this registered definition through the
revision-checked workflow configuration service. Refresh it through the same
page or its Admin API action; never replace a live pack file directly.

### Team MCP Action Surface

Prompted persistent agents drive the team by calling Team-owned MCP tools. The
tools live in the Team Add-on, not in the core gateway policy surface, and the
server enforces authorization even when a tool is hidden from the caller's MCP
tool list. Normal Team tools infer the session, agent, active dispatch, Team
lease, and worker assignment from the MCP connection/session; the deprecated
explicit `session_id` argument is accepted only for compatibility and is
rejected when it conflicts with the connected session. Tool calls mutate Team
state immediately and atomically per call; they do not close the active dispatch
lease. Session completion closes persistent dispatches through the dispatch
activity ledger/finalizer and computes progress or no-progress from that
ledger.

Ordinary persistent Team wakes may receive these tools:

- `team_record_note`
- `team_mark_inbox_handled`
- `team_request_status`
- `team_ask_question`
- `team_send_progress_report`
- `team_send_operator_chat_message`
- `team_stage_chat_attachment`
- `team_escalate_operator`
- `team_record_memory`
- `team_archive_memory`
- `team_create_case`
- `team_update_case`
- `team_create_task`
- `team_create_tasks`
- `team_update_task`
- `team_report_blocker`
- `team_update_blocker`
- `team_update_goal`
- `team_stand_down_agent`
- `team_revoke_stand_down`
- `team_submit_deliverable`
- `team_submit_deliverable_for_review`
- `team_import_artifact`
- `team_request_review`
- `team_decide_review`
- `team_retry_review`
- `team_link_deliverable_replacement`
- `team_publish_review_branch`
- `team_update_feature_branch`
- `team_request_release`
- `team_recover_release`
- `team_retry_release_gate`
- `team_request_ephemeral_agent`
- `team_request_ephemeral_agents`
- `team_accept_ephemeral_result`
- `team_reject_ephemeral_result`

Tool visibility is scoped by wake class:

- Normal persistent wakes get ordinary Team action tools.
- Team Chat wakes get the chat-class tool `team_reply_to_chat` and the attachment-staging tool
  `team_stage_chat_attachment`, plus ordinary tools only when that chat dispatch may mutate Team
  state.
- Governance wakes get governance tools only.
- Ephemeral worker wakes get `team_report_ephemeral_result` only.
- Inactive or teamless sessions get no ordinary Team action tools, except
  governance tools for authorized governance managers.

`request_id` is the idempotency key for create, append, message, and
async-intent operations. Reusing the same key with the same normalized payload
returns the original result; reusing it with a different payload is an
idempotency conflict and does not mutate state. Related tool calls may carry
`correlation_id` or `causation_id` for audit grouping. Tools that accept
`handled_inbox_item_ids` mark those pending caller-owned inbox items handled
atomically with the primary action; if any listed inbox item fails validation,
the whole tool call fails without partial handling.

Async-intent tools may record durable intent while external work is still
pending. Their result reports the recorded intent and pending effect state
rather than claiming the external operation completed synchronously.

`team_report_blocker` records a durable blocker for an explicit dependency or a
blocked task, deliverable, review request, or release package. The nearest
available superior becomes accountable and is woken immediately. An accountable
dispatch that finishes without a blocker-specific progress event transfers the
blocker up exactly one reporting level; unrelated Team activity does not suppress
that transfer. `team_update_blocker` records progress, delegates to a direct
subordinate, returns a delegate result, resolves the blocker, or explicitly
escalates it. Delegation retains the superior as owner: a delegate result or
stalled delegate dispatch wakes the owner, and only a later owner dispatch with
no blocker-specific progress transfers accountability upward. A blocker with no
remaining superior routes to the Team's governance group when configured, or to
its operator communication target otherwise. The existing manager fallback
remains a periodic recovery mechanism rather than the normal blocker route.

Manual resolution remains available to every active Team agent on the blocker's
structural escalation line, from its reporter through that reporter's superiors
and Team manager. Ownership and terminal routing determine accountability and
wake behavior; they do not revoke resolution authority from that line. Peers and
temporary delegates are not added to the line. Automated blockers without a
reporter use their first recorded owner as the line anchor. Source-owned state
transitions may also auto-resolve a blocker without an agent-authored resolution.
Once routing reaches governance or an operator, an unlocked Admin Web operator
may also resolve it with a required audited explanation. That terminal authority
is additive and does not remove resolution authority from the Team escalation line.

Blockers and their event history are stored in Team SQLite, exposed through
`team_list_blockers` and `team_get_blocker`, included in bounded wake context and
runtime attention, and shown alongside blocked tasks, deliverables, reviews, and
release packages in the Team UI.

Deliverable tools publish `artifact_paths` host-side before recording the final
Team mutation. Paths are checked for safety, publication is replay-idempotent,
and Team state stores the resulting stable artifact references. If publication
succeeds but the final mutation fails, operators should treat the published
artifact as an orphan until it is linked by a replay or cleaned up intentionally.
For ordinary task, status-request, question, or progress-report handoffs, the
recipient can pass an `artifact-space:` ref to `team_import_artifact`. The tool
checks that artifact space's read grant for the calling `team-agent:<id>`, selects
the requested latest artifact version (`bundle` by default), and imports it into
the caller's own `/artifacts/team-artifact-imports/<artifact>/<ref-version>`
path. The caller cannot select another session or a host destination path.
Reviewer wakes automatically import review-request artifact refs into
`/artifacts/team-review-inputs/<review>/<deliverable>/<artifact>/<ref-version>`
and surface successful imports or import errors in the Team context JSON.

Ephemeral workers report assignment results with `team_report_ephemeral_result`.
When they report Git output, the branch must match the deterministic
`agents/<session-id>/<assignment-id>` branch for that assignment; a mismatch
fails the assignment rather than recording a branch the add-on cannot later
promote.

Persistent agents may include `git_refs` on deliverables when the project has
Git configured and the runtime has created a Git work context for that task
worktree. Persistent sessions receive one deterministic branch and physical Git
worktree per task under `/home/agent/worktrees/<repo>/...`; later wakes reuse the
same task branch without sharing a mutable checkout with another task. Git
deliverables must include the context's `branch_context_id`; the
server derives the accepted `git_refs.agent_branch` from that context and
rejects mismatched agent-supplied branches. These refs are not published
automatically; a supervising agent, project lead, or manager requests
publication with `team_publish_review_branch`, `team_update_feature_branch`, or
`team_request_release`.
Deliverables may also include `verification` with `status` set to `passed`,
`failed`, `partial`, `not_run`, or `unknown`, plus an optional summary and
evidence refs. Use `not_run` when test tooling, services, or credentials were
unavailable.

Review acceptance is policy-enforced. When a review has a profile, every
required criterion must be present and `passed`; optional criteria may be
omitted. Rejected or blocked review findings are stored on the deliverable and
copied to its superseding rework deliverable. An accepted review can resolve
only findings owned by the same review profile (or, for legacy unprofiled
reviews, the same reviewer role), and a deliverable cannot become accepted
while any carried finding remains open.

Release requests enforce the verification record instead of treating it as
informational. `failed` verification cannot be waived. `partial`, `not_run`,
`unknown`, or missing verification requires a reasoned waiver from the project
lead, top-level team manager, or an authenticated operator; ordinary roles with
`request_release` cannot grant waivers. Each waiver records the deliverable,
status, reason, actor kind, actor id, and timestamp on the release package.

### Communication And Escalation Rules

Team communication is directional and enforced by the repository, not by prose:

- **Upward** — `questions` and `status_reports` may only target the actor's
  direct superior. There is no skip-level messaging.
- **Downward** — `status_requests` may only target a direct subordinate.
- **Reply intent** — inbox items carry `response_requested`. When it is `false`,
  the recipient should mark the item handled after reading or acting on it, but
  should not send thank-you, acknowledgement, or other no-op replies. This
  metadata does not delay delivery; priority and batching are separate runtime
  features.
- **Manager to operator** — only the top-level manager escalates, and only when
  `Team.operator_communication_target_id` is set. The repository emits
  `team.operator.escalation_requested`; the packaged `TEAM_RUNTIME_V1` pack routes
  that event to an `effect:communication:send-message` node bound to the team's
  `operator_communication_target_id`. The repository and effect providers never
  call the communication sender directly — routing is the workflow pack's job.
- **Operator or governance manager to manager** — use the add-on
  `POST /operator-directives` Admin API, `effect:team:create-operator-directive`,
  or the `team_create_operator_directive` MCP tool for governance managers.
  These surfaces create a pending `operator_directive` inbox item for the
  manager and mark the manager due, so an instruction like "ask the lead for
  status on next dispatch" becomes normal Team work.
- **Governance agenda completion** — governance managers use the
  `team_mark_governance_agenda_handled` MCP tool to mark agenda items handled
  for groups they manage. Operators can still use the Admin API or UI handle
  action.
- **Team Chat to a human** — use `team_reply_to_chat` only to answer a specific
  human operator message from the built-in Chat tab. If that operator message
  explicitly asks for a Team state change, use the relevant ordinary Team tools
  in the same dispatch when mutation is allowed. Use
  `team_send_operator_chat_message` only for human operators listed in the
  current dispatch context. Do not use question, status-report, or operator-chat
  tools for normal agent-to-agent coordination.
- `Team.operator_communication_target_id` is outbound-only. It does not grant the
  manager's real session inbound Discord or Teams access and does not wake that
  session by itself. If the operator should message or wake the manager through
  Discord, create the manager agent/session with a read-capable Discord
  communication target in its session allowlist and grant `discord_get_commands`.
- **Operator stand-down** — use `POST /agents/{agent_id}/stand-down` with a
  required `reason` to disable that agent and its subordinate subtree. Held
  agents are skipped by future Team wake leasing, and any already-running
  dispatch that later attempts Team mutations is rejected instead of being
  applied. Operator holds are durable state and can only be cleared through
  `POST /agents/{agent_id}/stand-down/revoke`; ordinary Team MCP tools cannot
  revoke or override them.

Inbox priority controls wake timing, not visibility or permission. `high` and
`normal` pending inbox items request a debounced recipient wake outside the
15-minute Team cron; the default delay is 10 seconds so short bursts can batch
into one dispatch. `low` items are visible in Team state immediately but do not
wake an otherwise idle recipient until `low_priority_wake_after_cycles`
evaluations have seen that low-priority work. Additional low-priority items do
not reset an active countdown. Any other wake reason cancels the countdown and
includes all pending low-priority inbox in that dispatch.

### Review Decisions And Decision Cascade

Reviews are created explicitly and decided only by the assigned reviewer. A
decision cascades deterministically through the deliverable, task, and project,
and the same cascade backs both the `team_decide_review` MCP tool and the
`POST /review-requests/{id}/decision` Admin API endpoint:

- **accepted** — the review is accepted. The deliverable becomes accepted only
  when **all** of its review requests are accepted. The task becomes accepted
  only when every deliverable on that task is accepted (multi-review and
  multi-deliverable tasks are held back until the last one lands).
- **rejected** — the review and deliverable are rejected and the task is set to
  `blocked`.
- **blocked** — the review, deliverable, and task are all set to `blocked`.

Every decision stamps `decision_at` and triggers
`_recalculate_project_status`, which also runs after goal updates, task state
changes, and release changes. A project is `completed` when all goals are
accepted and no active task, deliverable, review, or release package is pending
or blocked; `blocked` if any of those is blocked; otherwise `active`.

### Task Graphs And Review Profiles

Team tasks can be split into tracked child tasks with `parent_task_id` and can
declare ordering constraints with `dependency_task_ids`. The add-on stores those
edges in canonical Team state, validates missing tasks, cross-project edges, and
cycles, and exposes derived task fields in API/query payloads:

- `child_task_ids`
- `dependency_status` (`satisfied`, `waiting`, or `blocked`)
- `blocked_by_task_ids`
- `subtask_counts`

The runtime wake loop uses this derived graph state: persistent agents are not
prompted to start tasks whose dependencies are still waiting or blocked, while
agents with open child tasks can still be woken to supervise or consolidate that
work.

Review profiles are reusable, team-owned review rubrics. A profile may be scoped
to one project, constrained to specific reviewer roles with `applicable_role_ids`,
and populated with criteria rows. Review requests can attach a profile and
instructions; review decisions can then record per-criterion outcomes. Archived
profiles remain visible for history but cannot be assigned to new review
requests through either Team MCP tools or the Admin API.

Tasks that must produce an external or durable result can use
`task_kind: outcome` and an `outcome_contract`. The contract names required
deliverable outcome kinds, chooses `all` or `any` matching, can forbid outcome
kinds, and may explicitly allow an evidence-backed no-op. Deliverables carry
structured outcome kinds, durable reference IDs, and evidence references. A
report-only deliverable cannot satisfy an outcome task, and a review profile can
require specific outcome kinds before review is requested or accepted. Ordinary
coordination tasks remain `task_kind: support` and finish without inventing a
deliverable. When several profiles apply to the same reviewer role, their
required outcome kinds also select the implicit profile; callers still pass
`review_profile_id` when more than one matching profile remains.

Review decisions are immutable attempts. A blocked non-release review records a
`block_kind` and can be retried with `team_retry_review`; the successor is linked
to the preserved attempt and becomes the only current review. A blocked or
rejected deliverable can be replaced by a normal same-task rework submission, or
a project lead/manager can explicitly link an accepted cross-task replacement
with `team_link_deliverable_replacement`. An operator can retire one erroneous
current non-release review through
`POST /api/addons/team-addon/review-requests/{id}/retire`. Migrations never infer
or repair these relationships automatically, so historical blocked records stay
blocked until an explicit retry, replacement, retirement, or blocker resolution.

Terminal commands reject a stale dispatch snapshot when a newer high-priority
inbox item arrived after the agent woke. The agent must read the item and include
its ID in `handled_inbox_item_ids` in the same command. Re-sending already handled
IDs and resolving an already-resolved blocker with compatible evidence are
successful no-ops, with `applied: false` and no Team-state revision bump.

### Deterministic Git Branch Naming

Branch names are derived deterministically so the repository, effect, and
workflow layers all compute the same name without external lookups. The helpers
live in `evander_team_addon.git_naming`:

- ephemeral agent branch: `agents/<session-id>/<assignment-id>`
- review branch: `team/<team-id>/reviews/<assignment-id>`
- feature branch: `team/<team-id>/features/<task-id>-<agent-id>`
- release branch: `team/<team-id>/releases/<release-id>`
- canonical release branch: `releases/<project-id>/<release-id>` (the default
  prefix `releases/<project-id>/` is overridable per project via
  `ProjectGitConfig.canonical_release_branch_prefix`).

`validate_branch_name` accepts only plain branch names: it rejects empty values,
any whitespace, control characters, `:`, a leading `/`, the substring `..`, and a
leading `refs/`. Every operator- or agent-supplied branch is validated before any
subprocess git call.

### Git Promotion And Release Publication

Git delivery flows up a fixed ladder — agent branch → review branch → feature
branch → release branch → canonical push — using `GitPromotionRecord` rows in
`TeamState.git_promotions` and `team.git.promotion_requested` events. Team MCP
tools record intent; the packaged pack routes each event on its
`promotion_kind`:

Git work contexts are optional Team state records for coding worktrees. They are
created only by host/runtime setup for tasks that actually involve Git, and they
bind an agent, task, optional dispatch, worktree path, base branch/base commit,
expected agent branch, and last verified commit. Artifact-only deliverables,
administrative work, customer-service work, operations work, and tasks with no
deliverable do not need Git contexts. Once a deliverable includes Git metadata,
`team_submit_deliverable` and `team_submit_deliverable_for_review` require a
matching `branch_context_id` and reject missing, wrong-task, wrong-agent,
wrong-dispatch, inactive, or mismatched branch contexts.

- `review` — `team_publish_review_branch` can promote an ephemeral assignment
  branch after the assignment is completed or accepted with an `agent_branch`, or
  promote a persistent deliverable branch when the actor is the producer,
  supervises the producer, or holds project-lead or manager authority over the
  deliverable's project. Persistent sources may come from the deliverable's
  `git_refs.agent_branch` / `git_refs.commit_sha`.
  Routes to `effect:team:publish-review-branch` then
  `effect:team:record-git-promotion`.
- `feature` — `team_update_feature_branch` can consolidate review branches into a
  task feature branch when the actor owns the task or supervises its owner.
  Every source must match one accepted current deliverable, and the promotion
  pins the exact commit accepted by that deliverable's review.
  Routes to `effect:team:consolidate-git-branches` then
  `effect:team:record-git-promotion`.
- `release` and `canonical_push` — `team_request_release` creates a pending
  `ReleasePackage` for accepted deliverables in one project. Only the project
  lead, the team manager, or an agent whose same-project role has
  `request_release` may request a release. Deliverables may already carry
  `git_refs.feature_branch`, may carry `git_refs.review_branch` or
  `git_refs.agent_branch` for automatic prerequisite feature promotion, or may
  be non-Git artifact deliverables with `artifact_refs`. If prerequisite feature
  promotions are needed, they are enqueued first and the release continues after
  they succeed. Once feature branches are available, project Git config is
  required and the runtime creates a `release` promotion (consolidate feature
  branches → release branch) followed by a `canonical_push` promotion (release
  branch → canonical branch, finalized by `effect:team:mark-release-published`).
  Pure artifact releases do not require project Git config and do not create
  release or canonical branches.

New Git release packages use the current release policy; historical terminal
records remain readable without being retroactively reopened, while legacy
nonterminal packages are rejected with `legacy_unpinned_release` and must be
recreated. Consolidation merges the pinned reviewed/feature commit rather than
the current branch tip; it rejects a source only when that pinned commit is no
longer contained by the source ref. The feature promotion records a reviewed manifest (base, tree, commits,
and changed paths), and release consolidation rejects commits or paths outside
the union of those manifests. Scope mismatches are hard blockers and cannot be
waived.

For Git releases, configure `Project.release_policy`. It selects the
`container_worker_vm` verification backend, optional container image, ordered
verification commands (`name`, `cwd`, `argv`, and timeout), and release review
requirements (`reviewer_agent_id` plus `review_profile_id`). The consolidated
release commit is pinned as the candidate, verification runs against that exact
commit, and configured reviewers decide that same candidate. Canonical push is
created only after verification, all release reviews, and scope validation pass.
The candidate also receives a frozen typed manifest containing its deliverable
verification, waiver audit records, accepted immutable reviews, reviewed change
manifests, scope result, verification commands, and review requirements.

A nonzero verification-command exit is a candidate failure: the release is
terminally rejected and requires changed, reviewed content in a new release.
Creating another release with the same deliverables and failed tree is refused.
Only timeouts and infrastructure failures leave verification retryable, and only
a review recorded as process-blocked may be replaced. Release-review decisions
are immutable: use `rejected` for a candidate defect and reserve `blocked` for a
non-candidate process or evidence-access blocker with a concrete finding.
`team_retry_release_gate`
and the Admin API/UI create a linked immutable successor attempt against the same
pinned SHA and persist the audited reason and requesting agent/operator identity;
they never rewrite the failed attempt.
`team_recover_release` remains limited to failed Git feature/release promotions
and is not a release-gate retry mechanism.

Set `allow_ungated: true` only for an intentionally ungated project; a missing
policy blocks a new Git release rather than silently publishing it. Admin Web
project create/edit forms expose the policy as JSON and release rows show the
pinned candidate, verification state, release-review state, approved SHA, and a
retry action only for an eligible typed gate failure.

Five add-on-owned effect providers complete these flows:
`effect:team:publish-review-branch` (publish a source branch or commit from the
project's managed bare repository into a durable
`team/<team-id>/reviews/...` ref),
`effect:team:record-git-promotion` (mark a promotion succeeded/failed and attach
branches to `git_refs`), `effect:team:verify-release-candidate` (create a
disposable container-worker session, clone by `managed_repo_id`, check out the pinned candidate, run the
ordered commands, and destroy the session),
`effect:team:mark-release-published` (publish or block a release package), and
`effect:team:consolidate-git-branches` (deterministic merge
of explicitly pinned source SHAs in an operation-scoped detached worktree,
aborting and failing on conflict — never auto-resolving). The central managed
Git service applies the configured host commit identity, compare-and-swap
updates the Team target ref, and removes the temporary worktree and operation
ref on every exit. The core `effect:git:push-branch` effect performs an
expected-commit and destination-lease preflight for canonical publication and
refuses to publish anything except the approved candidate.

**Publication boundary.** Internal branches (agent, review, feature, and release)
remain only in the project's managed bare repository. The **only** branch pushed
to the canonical remote is the release branch, and only on the `canonical_push` route via
`effect:git:push-branch`. Deliverables that ship non-Git artifacts remain fully
supported through `artifact_refs`, require no push at all, and can be packaged
even when the project has no Git configuration. For Team-authored deliverables,
`artifact_paths` publish to immutable keys shaped as
`team/<team_id>/<project_id>/<deliverable_id>/<dispatch_id>/bundle` and are
indexed through artifact spaces; Team state stores only the resulting stable
refs.

### Team Cost Guard

Teams may carry an optional `max_cost_usd` estimated lifetime cap. The wake loop
checks Team cost payloads before leasing due agents; when the estimated total
reaches the cap, future Team wakes for that team are skipped until the cap is
raised or cleared. This is an operational safety rail, not billing-grade
enforcement: totals are derived from recorded model-usage events, the local
pricing catalog, and optional Claude transcript backfill. Cost payloads expose
`coverage_status` and `coverage_reasons`; operators should treat partial
coverage as a reason to inspect the underlying usage data before making spend
promises.

### Bundled Team Add-on Enablement

Use this checklist when enabling the bundled Team Add-on on a deployment:

1. Confirm the installed package includes `evander_team_addon`, its
   `addon.json` manifest, and its `ui_static` files.
2. Open `/#addons`, select **Team Add-on**, and enable it. The revision-checked
   save updates host and worker add-on configuration and materializes the
   registered Team runtime workflow pack automatically.
3. If the registered pack definition changes later, use **Refresh runtime
   pack** on the Add-ons page or the equivalent Admin API action. Never write a
   live pack file directly or replace it from a stale checkout.
4. Restart every service listed by the page. For Team this includes
   `evander-admin-web.service`, `evander-trigger-orchestrator.service`,
   `evander-effect-orchestrator.service`, and `evander-gateway.service`; the
   gateway restart is required for Team MCP registration.
5. Verify `/api/admin/state` lists `team-addon`, `/api/addons/team-addon/templates`
   returns the built-in catalog, `/addons/team-addon/` serves the add-on UI,
   and `/api/workflow-editor/catalog` plus workflow validation recognize the
   `effect:team:*` and `query:team:*` node types only after the add-on is enabled.
