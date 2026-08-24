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

For a local-only seed, copy the native `.bak` directly into the configured worker backup directory,
record its SHA-256, and register a versioned alias through the add-on Admin API. The backup does
not need to be checked into Evander or represented in Ansible inventory. Use a new alias when the
file changes so evidence always identifies the exact fixture it used.

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
- `bokio_check_journal_duplicates` follows Bokio's reported `totalItems` through
  every required journal-entry page and matches a non-empty combination of
  `date`, `account`, `amount`, `title`, `invoice_number`, and `receipt_number`.
  It fails closed if pagination ends before that total is reached. The result
  includes the number of entries inspected, matching entry IDs, a source
  fingerprint, and a durable `bokio-duplicate-check:...` evidence reference.
  Equivalent checks reuse evidence for 15 minutes by default, but the cache key
  includes the configured company ID and API base URL so evidence cannot cross
  Bokio companies or endpoints; set `refresh=true` to force a new scan. The
  durable records live in
  `runtime/addons/bokio-addon/duplicate-checks/duplicate-checks.db`.
  Grant `bokio_check_journal_duplicates` to the agent producing the evidence and
  `bokio_get_duplicate_check` to reviewers that must dereference the resulting
  `bokio-duplicate-check:...` ID. The read tool returns the exact retained
  criteria, source identity and fingerprint, scanned count, matching entry IDs,
  and creation time without querying Bokio again. Existing sessions do not gain
  newly added tools automatically; add these grants in **Sessions** by editing
  the session's allowed MCP tools.
- `bokio_attach_evidence` uploads managed evidence with a Bokio journal-entry ID,
  linking it to an existing transaction or entry. `bokio_create_journal_entry_with_evidence`
  creates an entry and attaches its evidence with a durable operation key. Bokio
  permits only one linked upload per journal entry, so multiple managed evidence
  records are retained individually and compiled into one PDF before the single
  linked upload. Image evidence is EXIF-orientation normalized and rejected above
  16,000,000 pixels before journal creation, preventing oversized images from
  exhausting the host. These outward writes follow the standard per-tool MCP policy;
  enable autonomous use only for sessions that are explicitly permitted to make
  those calls.
- `bokio_prepare_journal_entry` accepts optional `evidence_ids` from the durable
  evidence store. The prepared action keeps that exact evidence list for operator
  review, and approved execution uses the same durable create-and-attach ledger as
  the direct evidence-aware write. A multi-file action retains each original
  evidence record and attaches one composed PDF. The action becomes `executed`
  only after that linked upload succeeds. If the journal entry succeeds but an
  upload fails, **Retry** resumes the evidence upload without creating another
  journal entry. A journal entry that was partially attached by the former
  per-file behavior cannot be repaired by retrying: Bokio already has its one
  upload, so reconcile its missing evidence manually.
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
Team, manager, project lead, task, outcome-kind, deliverable, review, and
release-package schemas belong in that package. Only generic hooks that are
useful to other add-ons should be proposed for Evander core.

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

The Delivery view defaults to records that still need attention. Projects stay
collapsed until selected or opened, and the project selector plus status
filters can narrow the workspace without discarding access to accepted and
historical records through **All records**. Opening a deliverable shows an
evidence-backed pipeline for the stages that can be established from current
state: implementation evidence, deliverable acceptance or agent review,
release inclusion, automated verification, release-agent review, human review,
and publication. It calls out the current stage and active blocker. For a
release-backed deliverable, **Load exact gate evidence** reads the existing
permission-filtered release-detail endpoint so individual verifier attempts
and review decisions are shown only when those details are actually available.
For an authorized human-review detail, **Test this candidate** also shows the
canonical repository identity, managed repository ID, immutable internal
release ref, exact candidate commit/tree, and a copyable supported host
command. The command resolves the private repository location inside Evander,
verifies that the ref still names the pinned commit and tree, and creates a new
detached checkout at the caller-selected target. It never teaches internal bare
repository paths or overwrites an existing target. The generated command
includes the absolute runtime root, so it remains valid from another checkout,
and loads only the narrow managed-Git service rather than unrelated providers.

The Planning view presents projects and structured task briefs as nested native
disclosures, including lifecycle, blockers, hierarchy, contribution coverage,
review status, and technical fields. Their open/collapsed state is remembered
when a live refresh or mutation rerenders the view, so an operator does not lose
the task they were inspecting. Remembered disclosure keys are scoped to the
owning Team view, and project-local goal keys are also scoped by project, so
hidden copies and repeated local IDs cannot change another panel's state.
Eligible task rows use a dedicated **Cancel task** dialog rather than a generic
state edit. It requires a reason category and explanation and may link an
optional successor selected only from the same project; the dialog calls out
that cancellation is a terminal audited disposition that resolves active
blockers and finalizes the task Git context.

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

Admin Chat sends are mutation-enabled only when privileged operations are
unlocked and the sender has directory-scoped `team_chats.mutate` authority over
the recipient agent and the included projects. The server snapshots the
intersection of that authority and the agent's visible projects when accepting
the message; managed repositories are derived from those projects. Missing or
legacy mutation metadata is reply-only. Built-in Owners and Operators receive
`team_chats.mutate`; other users can still send reply-only messages. A
mutation-authorized message whose project intersection becomes empty before
lease fails without resuming the agent.

Chat refreshes and live snapshots preserve reader position when the operator is
scrolled up, while a reader already near the bottom remains pinned to new
messages. Loading older history preserves the visible anchor as messages are
prepended, and thread rerenders preserve the current composer text, focus, and
selection.

When a persistent agent already holds an active Team lease, a newly queued,
plain-text operator Chat message is soft steering: its next Team MCP response
atomically includes the message and records it as `seen`. It is not redelivered
and does not require a Chat reply. Attachment-bearing messages remain queued
for a normal Chat dispatch because their files must first be copied into the
agent workspace. Once the agent is available, queued messages from the same
conversation and external return path are coalesced into one Chat dispatch. The
prompt supplies all message IDs and one `team_reply_to_chat` reply covers the
whole batch; messages from different conversations, providers, targets, or
threads remain separate dispatches.

External communication can enter the same durable Chat lifecycle through
`effect:team:enqueue-operator-chat-message`, normally after
`trigger:communication:new-message`. Configure exactly one fixed
`conversation_id` or `agent_id`, a fixed `operator_user_id`, and a read-capable
source `communication_target_id`; do not derive those identities from message
text. The provider, target, and source-message ID form the durable ingress
idempotency key, so replay returns the original Team message and
`team-chat:<message-id>` dispatch. `allow_team_mutations` defaults to `false`;
enabling it also requires a fixed `authorized_author_ids` allowlist containing
the authenticated source author. Mutation-enabled bridge messages are further
bounded to the workflow effect directory for both the recipient agent and
included projects. The target must additionally allow `reply` for a preserved
provider thread or `write` for a top-level response.

An agent reply to an externally originated message is committed to Team state
before delivery is attempted. `team_reply_to_chat` creates a pending external
delivery and emits `team.chat.external_reply_requested`; the packaged runtime
pack sends it through `effect:communication:send-message` and records success or
failure with `effect:team:record-external-chat-delivery`. The persisted Team
reply remains visible when the external send fails, and the Chat UI exposes its
delivery state.

The external-reply event carries `attachment_ids` and sanitized attachment
metadata (`id`, filename, MIME type, byte count, and SHA-256). Its delivery
`body` is a rendered textual reference that lists each attachment's filename,
MIME type, and byte count, so an attachment-only Team reply is still non-empty
and deliverable. This contract does not upload files through the communication
provider. The unrendered source Team reply body remains separately available as
`team_chat_body`. Discord replies are top-level channel messages rather than
thread replies.

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
`team_send_operator_chat_message`. Reply-only Chat is text-only; staging and
attaching agent-originated files to a Chat reply require a non-empty
project-scoped Chat dispatch. Staging accepts only regular files under
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
permissions include `create_root_task`, `manage_outcome_kinds`,
`prepare_bookkeeping_action`, `review_bookkeeping_action`,
`request_payment_preparation`, and `request_release`; authorization checks the
agent's effective role permissions rather than exact role names.
`manage_outcome_kinds` is deliberately not granted by default. A Team manager
with a team-scoped grant may manage team-wide definitions, while a Team manager
or project lead with an effective project-scoped grant may manage that project's
definitions. The built-in `auto-dev-squad` template grants `request_release` to
its project-scoped release-operator role, and the `accounting-department`
template grants the bookkeeping permissions to its preparation and review
roles.

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

Workflows can attach an already-created core session through
`effect:team:register-agent`. The session must be unexpired and inside the
workflow directory scope (or a descendant); the effect validates the Team,
role, optional project membership, reporting edge, and unique session binding
before changing Team state. An exact retry is a successful no-op. The existing
Admin registration route remains available for its broader operator workflow.

Use `effect:team:retire-agent` after a workflow has retired children before
parents and cleared the agent's Team work. It checks the expected Team and
session identities, returns all ordinary Team removal blockers, removes project
and reporting memberships, and archives the persistent Team record without
destroying its core session. The workflow should invoke `effect:agent:destroy`
only after retirement succeeds. Retrying an already-detached archived agent is
safe even after its core session has been destroyed.

When a Case genuinely moves from a non-closed status to `closed`, Team stores a
transactional `team.case.closed` outbox event for generic `trigger:event`
workflows. The event is correlated by `case_id` and includes the Team, project,
owner, prior status, source provenance, `closed_at`, and an immutable snapshot
of the Case at closure. Workflows can therefore route a final customer message
from the recorded outcome and source-thread metadata without re-reading a
subsequently edited Case. Creating an initially closed Case, resolving a Case,
and no-op updates of an already closed Case do not emit it.

The generic workflow composition is: create the core session, register its
Team agent, assign or wake work, close the Case, retire children before parents,
then destroy the core sessions. Thread correlation, Case creation, prompt
content, waiting behavior, and session-destruction timing remain workflow
owned.

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
reviewer only when the requests are intentionally independent: those requests
may be added separately, and each terminal decision can cascade immediately.

Use `effect:team:create-review-cohort` when all required reviewers must assess
one immutable candidate as a sealed set. Its `reviews` array atomically creates
one slot per distinct reviewer and seals the deliverable only after every slot
is valid; no individual review may already exist, and no late member may join.
Retrying the effect with the same reviewer/profile membership is idempotent.
The effect contract requires `actor_agent_id`, `deliverable_id`, and a complete
`reviews` array whose entries require `reviewer_agent_id` and may set
`review_profile_id`, `review_request_id`, or `instructions`;
`publish_wake_events` and `expected_revision` are optional. Success returns
`review_cohort_id`, `review_cohort_sealed_at`,
`created_review_request_ids`, `existing_review_request_ids`,
`created_inbox_item_ids`, `events`, and `published_event_ids`.
Exact-membership replay returns the existing IDs; changed membership or any
pre-existing independent request fails closed.
`team_submit_deliverable_for_review` applies the same sealed-cohort contract at
submission time. A rejection in one cohort slot does not send partial rework
while another slot remains pending: Team waits for the complete cohort result,
then sends one consolidated rework item containing all open cohort findings.
A replacement must name the rejected deliverable in
`supersedes_deliverable_id` and create a complete fresh cohort. It must preserve
every current profiled slot by profile count and every unprofiled slot by
reviewer identity; a smaller cohort or a plain unreviewed replacement fails
before artifact publication.

The Team Add-on also registers its stable internal event names in the workflow
editor catalog, so `trigger:event.event_type` suggests the event names without
requiring code inspection. Current catalog entries are:

- `team.chat.message_queued`
- `team.chat.external_reply_requested`
- `team.agent.wake_requested`
- `team.ephemeral.requested`
- `team.ephemeral.completed`
- `team.operator.escalation_requested`
- `team.git.promotion_requested`
- `team.git.release_recovery_requested`
- `team.release.verification_requested`
- `team.release.human_review_requested`
- `team.release.human_review_preparation_requested`

The packaged runtime pack also consumes `team.governance.wake_requested`, which
blocker routing emits for an immediate governance-manager wake. Use generic
`trigger:event` workflows for these durable events; use
`trigger:team:deliverable-created` for deliverable-created workflows because it
owns the deliverable cursor and startup baseline behavior.

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
- `project.git.base_branch` (the default for newly created root tasks) and
  `project.git.canonical_release_branch_prefix`
- `project.git.agent_author_identity` and `project.git.agent_committer_identity`,
  each shaped as `{ "name": "...", "email": "..." }`

When the agent identities are omitted, Team checkouts use
`Evander Bot <evander@quinth.se>` for both roles. Configured identities are
reconciled into retained checkouts before work resumes. Divergent dependency
composition creates one commit using the configured project author and the specialized
`Evander Team Dependency Composer <team-dependencies@evander.invalid>`
committer; a single or dominant dependency is adopted without an artificial commit.
The multi-parent commit is reproducible: its author and committer dates are the
newest final-parent committer timestamp plus one second in UTC, so both execution
backends produce the same object ID for the same tree, parents, message, and identities.
Host-side feature, release, and recovery consolidations remain
governed separately by the managed repository's host commit identity.

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
human-approval boundary. It also defines the project outcome kinds
`evidence_match` and `bokio_prepared_action`; the bookkeeping-action review
profile requires the latter. Generated goal, outcome-kind, task, and
review-profile ids are
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
  The full lease context is prompt-local and is not persisted in workflow node
  output. Wake results expose bounded `lease_summaries` with
  `lease_summary_version: 1`; `leases` remains a compatibility alias for the
  same summaries. Counts, bounded identifiers, dispatch status, and bounded
  errors remain available without copying task or release history into every
  workflow execution.
  A first successful ordinary-context wake, an explicit `new_context` resume,
  or a lifecycle rotation receives a compact complete context labeled
  `context_mode: fresh_full`. A retained same-context wake is labeled
  `context_mode: retained_delta`: it carries the current immutable dispatch
  delta but intentionally omits stable identity, role, subordinate, project,
  outcome-catalog, and session details already present in the conversation.
  `team_get_current_dispatch_context` hydrates the active dispatch's frozen
  scope on demand, returning the scoped tasks, inbox items, blockers, reviews,
  projects, and Git contexts without widening its membership.
  Chat is deliberately asymmetric: only a successfully resumed,
  mutation-enabled Chat dispatch receives and marks an ordinary Team context,
  can establish the prior-wake baseline, and can consume a pending ordinary
  rotation. A reply-only Chat dispatch remains exact to its claimed message,
  even if the session transport resumes with `new_context`; it neither receives
  nor seeds ordinary Team context and leaves any pending ordinary reset
  pending. An unsuccessful mutation-enabled resume likewise seeds nothing.
  Each persistent agent can set `context_clearing_policy` to `off`, `idle`, or
  `every_wake`. `idle` rotates after real work has completed and the agent
  becomes truly idle. Its per-agent `context_clearing_idle_grace_seconds`
  setting accepts `0` through `604800` seconds. `0` preserves the original
  immediate behavior; a positive value starts a continuous idle window and
  retains the current context if new work arrives before its deadline. At or
  after the deadline, expiry wins over newly queued work so the existing
  handoff and fresh-context dispatch happen first. The idle start and next
  expiry are persisted, and a delayed wake requests precise reconciliation;
  the 15-minute Team runtime tick remains the fallback. Existing records
  migrate to `0`. Admin Web suggests 60 minutes when an operator switches an
  agent from another policy to `idle` while its saved grace is still zero.
  Manual and expired-idle
  rotations first lease a 120-second `context_handoff` preparation dispatch in
  the retained conversation. Its sole Team tool is
  `team_record_context_handoff`, which accepts one advisory summary of at most
  1,200 characters; ordinary Team work and messages are unavailable. Recording
  the handoff completes that preparation lease and makes the fresh rotation due
  immediately. Completion without a summary, timeout, or resume failure also
  allows the fresh reset to proceed, with the failure state retained for
  observability. `every_wake` skips advisory preparation and starts each
  otherwise-eligible Team wake directly with a fresh context; it does not
  bypass low-priority batching or wake an idle agent by itself.
  `low_priority_wake_after_cycles` defaults to `4`; low-priority inbox items
  count Team wake evaluations and are batched until the threshold is reached
  unless another wake reason delivers them earlier.
  High-signal wake requests, such as high/normal pending inbox,
  ready task handoffs, review requests, rework requests, and remaining work
  found when a dispatch finalizes, emit a debounced
  `team.agent.wake_requested` internal event. The default debounce is one
  second, after which the wake-request path leases only the requested agent
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
- `team_propose_human_finding_resolution`
- `team_route_human_finding_correction`
- `team_close_post_release_case`
- `team_create_task`
- `team_create_tasks`
- `team_create_outcome_kind`
- `team_update_outcome_kind`
- `team_archive_outcome_kind`
- `team_update_task`
- `team_cancel_task`
- `team_reopen_task_for_rework`
- `team_report_blocker`
- `team_update_blocker`
- `team_start_dependency_composition_recovery`
- `team_update_goal`
- `team_stand_down_agent`
- `team_revoke_stand_down`
- `team_submit_deliverable`
- `team_submit_deliverable_for_review`
- `team_decide_deliverable`
- `team_import_artifact`
- `team_request_review`
- `team_decide_review`
- `team_retry_review`
- `team_withdraw_review`
- `team_retire_review`
- `team_link_deliverable_replacement`
- `team_publish_review_branch`
- `team_update_feature_branch`
- `team_request_release`
- `team_decide_initial_release`
- `team_recover_release`
- `team_retry_release_gate`
- `team_request_ephemeral_agent`
- `team_request_ephemeral_agents`
- `team_accept_ephemeral_result`
- `team_reject_ephemeral_result`

Tool visibility is scoped by wake class:

- Normal persistent wakes get ordinary Team action tools.
- `context_handoff` preparation wakes get only
  `team_record_context_handoff`; even ordinary Team read/action tools are
  withheld for that bounded advisory turn.
- Team Chat wakes get the chat-class tool `team_reply_to_chat`. Reply-only Chat
  gets no other Team tool; mutation-enabled Chat also gets
  `team_stage_chat_attachment` and the ordinary Team tools authorized by the
  dispatch's non-empty project scope.
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
The exception is a recorded dependency-composition conflict: generic resolution
cannot discard its immutable source evidence. An escalation-line supervisor uses
`team_start_dependency_composition_recovery` to choose the active project agent
who will resolve and submit the pinned merge task.
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
`team_submit_deliverable` is a superior-acceptance handoff: it requires the
producer to have a direct superior, sends that superior a deliverable notice,
and exposes `waiting_reason: superior_acceptance`. It does not create formal
review work. The notice records the receiving superior, who uses
`team_decide_deliverable` to accept or reject the exact deliverable while
atomically handling the notice. Acceptance can cascade to task acceptance when
all active deliverables are accepted. Rejection requires a concrete reason,
marks the deliverable rejected, returns the task to `in_progress`, and creates
an actionable rework item for the owner.

When that acceptance resolves a newly accepted ordinary root task in a project
with a release policy, Team also records a durable initial-release disposition.
It starts as `pending` and is not cleared by acknowledging the root-resolution
notice. The project lead or top-level Team manager must either include the
deliverable in `team_request_release` or call `team_decide_initial_release`
with `decision: not_planned` and a reason. A later authorized
`decision: reopen` restores a not-planned disposition to `pending` while
retaining the reopen actor, reason, and timestamp. Historical deliverables that
predate this state remain untracked instead of creating a migration wake flood.

The receiving superior can instead use `team_request_review` when specialist or
policy review is needed, or `team_report_blocker` when evidence or a dependency
prevents a decision. Once formal review is attached, `team_decide_deliverable`
cannot bypass it; assigned reviewers must use `team_decide_review`. A top-level
agent with no direct superior must use formal review for a deliverable, or use
`team_update_task state=finished` for support/coordination work that produces no
reviewable result.

For an operator-owned workflow that must await a customer or another external
system, use `effect:team:set-task-work-state` with `state: waiting_for_input`.
This non-terminal state is intentionally not ordinary dispatch work: it does
not wake the owner and it does not create manager-fallback work. The workflow,
not Team runtime policy, decides how long to wait and which message to send.
When its external-input trigger arrives, set the state back to `in_progress`
and follow it with `effect:team:wake-due-agents` scoped to the owner for an
immediate wake.

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
same task branch without sharing a mutable checkout with another task.

The commit must reach that managed agent branch before submission. After
committing, run `git push-agent`, require it to succeed, and verify that the
managed branch resolves to the same commit as `git rev-parse HEAD`. A commit
that exists only in the worker's local worktree is not deliverable provenance;
Team reports the authoritative branch tip and leaves the submission unrecorded.

When a submission explicitly carries any Git metadata, it must include the
context's `branch_context_id` and `git_refs.commit_sha`. The server resolves the
server-owned branch tip, rejects a supplied commit or branch that does not match
that context, derives `git_refs.agent_branch`, and records the verified tip as
both `commit_sha` and `submitted_commit_sha`. Omitting Git metadata does not
provide a provenance bypass: when exactly one active context matches the
agent/task/dispatch, Team resolves its branch tip. An unchanged tip equal to the
pinned `base_commit` leaves a legitimate artifact-only deliverable Git-free; an
advanced tip is automatically bound to the deliverable with the full verified
Git refs. Multiple matching contexts require an explicit
`branch_context_id`, and inability to resolve the live tip fails the submission
instead of guessing that it is artifact-only.

These refs are not published automatically; a supervising agent, project lead,
or manager requests publication with `team_publish_review_branch`,
`team_update_feature_branch`, or `team_request_release`.
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
  human operator message, whether it entered from the built-in Chat tab or an
  authorized external communication bridge. If that operator message explicitly
  asks for a Team state change, use the relevant ordinary Team tools in the same
  dispatch only when its dispatch allows mutation. An externally originated
  reply is persisted first and delivered asynchronously through
  `team.chat.external_reply_requested`; delivery failure does not erase it. Use
  `team_send_operator_chat_message` only for human operators listed in the
  current dispatch context. Do not use question, status-report, or operator-chat
  tools for normal agent-to-agent coordination.
- `Team.operator_communication_target_id` is outbound-only. It does not grant the
  manager's real session inbound Discord or Teams access and does not wake that
  session by itself. If the operator should message or wake the manager through
  Discord, create the manager agent/session with a read-capable Discord
  communication target in its session allowlist and grant `discord_get_commands`.
- **Operator stand-down** — use `POST /agents/{agent_id}/stand-down` with a
  required `reason` and `scope`. `scope: agent` holds only that agent;
  `scope: subtree` also holds every reporting-line descendant. The UI's
  **Stand down** action explicitly sends direct-agent scope, while **Stand down
  subtree** sends subtree scope; the Admin API and
  `team_stand_down_agent` retain `subtree` as the omitted/default value for
  compatibility. Future leasing skips an agent under either its own active hold
  or an inherited subtree hold.

  A hold does not discard exact terminal work already leased before every
  effective hold was set. Such a pre-hold dispatch may complete only records in
  its frozen scope through the narrow deliverable/review/ephemeral/chat
  completion allowlist, or finish a scoped support task with an update whose
  entire mutation is exactly `{"state": "finished"}`. It cannot start work,
  widen the task update, or touch a record outside that dispatch. A dispatch
  leased after the hold has no exception. The privileged
  `POST /agents/{agent_id}/stand-down/revoke` route can clear an operator-issued
  or agent-issued hold at its direct target. Ordinary Team MCP tools cannot
  clear operator holds and can clear a superior hold only when the caller set
  that direct hold; inherited subtree holds must be revoked at their root.

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
  when **all current, non-retired** review requests are accepted and no carried
  finding remains open. The task becomes accepted only when every current
  deliverable on that task is accepted (multi-review and multi-deliverable tasks
  are held back until the last one lands).
- **rejected** — for independent reviews, the review and deliverable are
  rejected, findings are preserved, the task returns to `in_progress`, and the
  owner receives an actionable rework notice. A sealed cohort instead keeps the
  candidate pending while another cohort slot remains pending, then emits one
  consolidated rework notice after the whole cohort has resolved.
- **blocked** — the review, deliverable, and task are all set to `blocked`.

Independent review requests can be added one at a time. A sealed cohort fixes
its complete membership atomically and rejects late additions; blocked slots
must be retried or validly retired within their original cohort slot rather than
collapsed onto another member. Any replacement for a cohort-rejected candidate
must supersede that candidate and carry a complete fresh replacement cohort as
described above.

Every decision stamps `decision_at` and triggers
`_recalculate_project_status`, which also runs after goal updates, task state
changes, blocker transitions, and release/human-review changes. A project is
`completed` only when goals are accepted, every task is validly resolved, and
no active deliverable acceptance, agent review, release attempt, human request,
human finding, or project-affecting canonical blocker remains. Blocked or
invalid work and open project blockers produce `blocked`; actionable or waiting
work, correction work, pending human review, open findings, and successor
preparation remain `active`.

### Task Graphs And Review Profiles

Team tasks can be split into tracked child tasks with `parent_task_id` and can
declare ordering constraints with `dependency_task_ids`.
`originating_task_id` is a different, immutable provenance-only link: it records
that one task caused independent work to be created, but grants no authority
and gates neither task. `case_id` groups intake or correction work without
becoming a hidden parent.

The add-on stores those relationships in canonical Team state, validates
missing tasks, visibility, cross-project edges, and cycles, and exposes one
derived recursive `TaskAssessment` in Admin, MCP, wake, dispatch, release, and
command paths. It contains:

- resolution status and exact reason;
- readiness (`actionable`, child/dependency/acceptance/review waiting, blocked,
  resolved, or invalid);
- direct child and unresolved descendant IDs;
- unsatisfied dependency, active acceptance/review, and canonical blocker IDs;
- integration readiness and direct-child contribution coverage; and
- invalid legacy reason codes.

`integration_boundary: true` designates the explicit task that integrates and
accepts a final deliverable. It is valid only with `deliverable_contract`. A
non-boundary task may also carry a deliverable contract when that leaf must
resolve through an accepted deliverable instead of direct finish; this is used
for Git-backed correction and recovery work. Only integration-boundary
contracts may name required contribution tasks. The contract has two controls:

- `require_git_provenance` requires the final deliverable to carry a submitted,
  authoritative Git context with matching
  `branch_context_id`, `agent_branch`, `commit_sha`, and
  `submitted_commit_sha`.
- `required_contribution_task_ids` names the exact same-project contributions
  that the final deliverable must cover. Each named task must be either a direct
  child or a declared dependency of the integration task; incidental children,
  descendants, and lineage-only `originating_task_id` links do not count.

An integration boundary cannot resolve through
`team_update_task state=finished`. It resolves only when a current deliverable
whose `source_deliverable_ids` provide complete contract coverage is accepted,
and Git provenance is verified when required. Agents can set these fields with
`team_create_task` or, before any deliverable submission, an authorized
`team_update_task`; the create/update/edit UI surfaces the same controls.

A contracted non-boundary leaf also cannot resolve through direct finish. It
resolves through an accepted deliverable, with authoritative Git provenance
when required, but it does not aggregate other tasks and therefore cannot set
`required_contribution_task_ids`.

A contracted leaf cannot own child tasks. Submit its work directly, or create
independent follow-up work with `originating_task_id`; when child contributions
must be composed, designate an explicit integration boundary and its exact
contract before the first deliverable. Historical non-boundary tasks that
already persisted source-bearing aggregate deliverables retain legacy
direct-child coverage for compatibility, but new contracted leaves cannot
enter that mode.

Every task read also includes a non-persisted `inherited_blockers` projection
for blockers reached through unresolved descendants or unsatisfied
dependencies. Each row identifies the upstream task and canonical blocker,
reason code and explanation, source, accountable owner/delegate, route, and
escalation context. The projection deduplicates shared blockers and scoped
agent reads omit rows whose upstream task or blocker is not visible.

Support tasks resolve as `finished`. Outcome tasks resolve only as `accepted`
through a current contract-compatible delivered result or accepted
`evidenced_noop`. Either kind may resolve as `cancelled` through an authorized,
reason-coded disposition that records actor, time, and optional same-project
successor. `blocked` and `rejected` do not resolve ancestors. A parent cannot
submit its final deliverable, finish, become accepted, or enter release while
any descendant is unresolved.

Agents enter and leave intrinsic blocked work through `team_report_blocker` and
source-aware blocker resolution. Generic Admin task `PATCH` rejects transitions
into or out of `blocked`. Repairing a legacy contradiction uses
`POST /api/addons/team-addon/tasks/{id}/repair-state` with a required reason;
the linked blocker and its event history retain the Admin actor and references.

Only agents with project-scoped `create_root_task` may create ordinary roots;
provenance never supplies that permission. The authenticated human
changes-requested transition has one narrow system-authorized exception to
create a same-project correction graph atomically: one coordination-only
parent and one finding child per human finding. Post-release feedback still
creates its separate project-lead triage root.

An ordinary aggregate parent deliverable names the exact accepted current
direct-child outputs in `source_deliverable_ids`; an explicit integration
boundary instead uses the exact contribution tasks named by its contract,
including declared dependencies where configured. It omits contributions
resolved by accepted evidenced no-op or authorized cancellation and does not
invent a deliverable for finished support work. A nested child's accepted
aggregate deliverable carries the next layer, and recursive lineage is derived.
The runtime does not periodically wake a parent merely because children remain
open. It notifies the owner on blockers, handoffs, rejection, child resolution,
and the transition to integration-ready. Child-resolution history remains
durable when the parent owner's own active dispatch caused or observed that
transition, but that notice is recorded as handled and does not schedule a
redundant wake.

Review profiles are reusable, team-owned review rubrics. A profile may be scoped
to one project, constrained to specific reviewer roles with `applicable_role_ids`,
and populated with criteria rows. Review requests can attach a profile and
instructions; review decisions can then record per-criterion outcomes. Archived
profiles remain visible for history but cannot be assigned to new review
requests through either Team MCP tools or the Admin API.

An assigned-review wake does not treat the review's flat task row as the whole
specification. It directs the reviewer to fetch the complete deliverable and
task, including descriptions, outcome and deliverable contracts, assessment,
profile, artifacts, candidate, and verification evidence. For an integration
boundary it follows `required_contribution_task_ids` and the assessment's
required accepted source deliverables; for a legacy aggregate parent it follows
direct children. Composite contributions are traversed recursively to their
leaf specifications and evidence. Complete ID coverage alone is not proof of
correct integration, and missing required context is a `blocked` review with
the missing record IDs rather than permission to guess.

#### Outcome Kinds, Outcome Tasks, And Deliverables

In simple terms, an outcome kind is the team's shared definition of what
"done" can mean. A support task asks an agent to help, investigate, coordinate,
or report. An outcome task asks the agent to leave a particular durable result
behind. The manager chooses from the project's known outcome kinds when creating
that task. The agent then submits a deliverable that identifies the result,
points to the durable thing that now exists, and supplies the evidence needed
to verify it. A review can impose an additional reusable checklist before that
deliverable is accepted.

The complete relationship is:

```text
team/project outcome catalog
  -> task outcome contract (definition snapshot)
    -> deliverable outcome + durable references (definition snapshot)
      -> review profile requirements
        -> review request (definition snapshot)
          -> accepted deliverable
            -> accepted/completed task
```

Technically, `TeamState.outcome_kind_definitions` is a scoped catalog. Each
definition stores a stable `kind`, display name, description,
`reference_requirements`, and `evidence_requirements`. A project-specific
definition overrides a team-wide definition with the same `kind`. Archiving that
project definition intentionally hides the inherited team-wide kind for that
project until the override is restored or replaced. Historical records remain
valid because task contracts, deliverable outcomes, and review requests store
immutable semantic snapshots rather than looking up mutable catalog text later.

All project agents can inspect the effective catalog with
`team_list_outcome_kinds` and `team_get_outcome_kind`; effective definitions are
also included in bounded lease and wake context. Catalog mutations use
`team_create_outcome_kind`, `team_update_outcome_kind`, and
`team_archive_outcome_kind`. Those commands require both structural authority
(Team manager or matching project lead) and the configurable
`manage_outcome_kinds` role permission. Without that grant, an agent should ask
its manager or operator to define the missing kind. Operators can manage the
same records from the Projects UI or:

- `GET`/`POST /api/addons/team-addon/outcome-kinds`
- `GET`/`PATCH /api/addons/team-addon/outcome-kinds/{definition_id}`

Tasks that must produce an external or durable result use
`task_kind: outcome` and an `outcome_contract`. The contract names required
catalog kinds, chooses `all` or `any` matching, can list
`forbidden_outcome_kinds`, and may explicitly allow an evidence-backed no-op.
Unknown or archived kinds are rejected. Deliverables carry structured outcome
kinds, durable reference IDs, evidence references, and the applicable definition
snapshot. A report-only deliverable cannot satisfy an outcome task, and a review
profile can require specific active catalog kinds before review is requested or
accepted. Ordinary coordination tasks remain `task_kind: support` and finish
without inventing a deliverable. When several profiles apply to the same
reviewer role, their required outcome kinds also select the implicit profile;
callers still pass `review_profile_id` when more than one matching profile
remains.

Every outcome submitted for an outcome task must carry durable `reference_ids`;
evidence-only completion belongs on the explicit `evidenced_noop` task
completion attached to a submitted deliverable. It is valid only when the
contract allows it and carries evidence refs, and it resolves the task only
after direct acceptance or formal review copies the accepted completion to the
task. Agents cannot directly finish an outcome task. Normal delivered
completion is derived from outcomes on accepted, current deliverables that
satisfy the contract. A report or review is evidence, not an outcome by itself.

Review decisions are immutable attempts. A blocked non-release review records a
`block_kind` and can be retried with `team_retry_review`; the successor is linked
to the preserved attempt and becomes the only current review. An authorized
requester, producer, supervisor, project lead, or manager can withdraw a mistaken
pending non-release review with `team_withdraw_review`; no decision is invented.
A project lead or manager can retire a terminal non-release attempt with
`team_retire_review`. Operators have equivalent revision-checked `/withdraw` and
`/retire` routes. Both actions preserve history; retirement preserves the
decision and never resolves or waives open findings.

A blocked or rejected deliverable can be replaced by a normal same-task rework
submission, which carries its open findings, or a project lead/manager can link
an accepted cross-task replacement with `team_link_deliverable_replacement`.
That general rule does not bypass an unresolved sealed cohort: a candidate with
an active pending or blocked cohort slot cannot be replaced until the cohort
settles or its blocked slot is recovered. Migrations never infer these
relationships.

If a current accepted deliverable later proves invalid, the task owner, project
lead, or same-team manager uses `team_reopen_task_for_rework` with the resolved
task, exact `accepted_deliverable_id`, reason, and idempotent request ID. The
command fails with `task_rework_owner_unavailable` unless that task's original
owner is still active and belongs to the project Team. The accepted deliverable
and historical review decisions remain immutable; Team
marks the rework request on that deliverable, clears the task completion, moves
the task to `in_progress`, makes terminal ancestors unresolved through their
assessment, and sends the original active owner one high-priority rework item.
The task cannot finish or resolve again until
`team_submit_deliverable_for_review` creates a fresh formally reviewed same-task
deliverable whose `supersedes_deliverable_id` is that exact accepted
predecessor. The replacement must repeat every current required review profile;
if the predecessor used a sealed cohort, it must preserve the complete cohort
slot coverage described above.

Terminal commands reject a stale dispatch snapshot when a newer high-priority
inbox item arrived after the agent woke. The item is intentionally outside the
current immutable dispatch scope, so the agent must not retry the command with
that item ID. It ends the stale turn instead; dispatch finalization requests a
fresh wake, and the replacement dispatch can include the new item. Re-sending
already handled IDs and resolving an already-resolved blocker with compatible
evidence are successful no-ops, with `applied: false` and no Team-state revision
bump.

#### Durable Dispatch Scope And Execution Stalls

An ordinary persistent wake first stores one immutable `TeamDispatch` in
`preparing`. Its `task_ids`, `blocker_ids`, `review_request_ids`, and
`inbox_item_ids` contain only records actually selected and assigned to that
agent; related project and managed-repository lists contain only the resources
required by those delivered records. Relationship alone never adds a record to
the wrong scope list, and work created mid-dispatch waits for a later wake.
Commands require a `leased` dispatch and revalidate membership plus current
assignment/actionability in the corresponding list.

Before leasing, Team prepares every required repository and task worktree and
persists a fresh dispatch-bound Git context. A durable task/repository identity
keeps one deterministic physical worktree path across dispatches, while context
IDs remain fresh authorization boundaries. Prompt/UI projections show task,
project, managed repository, worktree, branch, and context, and label the base
checkout as a control checkout. If any preparation fails, the complete dispatch
fails, created contexts are finalized, and the runtime reservation is released;
Team never silently drops one scoped task.

Every new Git-backed root task snapshots the project's then-current default base
branch. An explicit task base overrides that default, while children inherit the
parent snapshot; batch creation resolves those parent links before committing.
The snapshot can be edited only before any Git work context, deliverable, or
promotion exists. On first worktree creation Team fetches that task snapshot
from the managed remote and records the exact base commit. Reused worktrees and
later dispatches retain both values even if the remote branch or project default
moves.

Accepted Git deliverables in one release must share one authoritative task base
branch and commit. Incompatible workstreams fail with
`mixed_git_base_branches`; a deliberate cross-base merge is represented by an
explicit integration task on the selected base, and only its accepted integrated
deliverable enters the release. A task base is not a publication destination:
updating a pre-existing branch such as `feature/foo` still requires an explicit
release `canonical_branch`, with the existing exact-SHA and destination-lease
checks.

Preparation failures are durable and target-specific so the cron loop does not
repeat the same failing setup before inference:

- Task, repository, and dependency-Git failures open or reuse a routed blocker
  keyed by task, semantic preparation generation, and a stable cause
  fingerprint. That current generation is suppressed from ordinary task
  dispatch until the blocker is resolved or meaningful task/project/dependency
  candidate state changes. A generation change automatically resolves the stale
  incident and permits one fresh preparation attempt; priority, timestamps, and
  other scheduling bookkeeping do not count as recovery.
- Inbox preparation failures suppress that exact inbox item under its
  item-and-cause blocker. Review artifact/repository preparation failures mark
  the exact immutable review attempt `blocked` with `block_kind: system`; use a
  valid review retry successor after recovery rather than repeatedly waking the
  failed attempt.
- A wake whose purpose is preparation recovery freezes only the preparation
  blockers and their referencing inbox items. It carries no task, review,
  project, managed-repository, or worktree preparation scope, so the accountable
  owner can inspect, resolve, delegate, or escalate the incident without
  retriggering the unavailable setup first.

For a Git-backed task, direct dependency edges also define code inheritance.
Before exposing the successor worktree, Team resolves every accepted, current
Git deliverable on those direct dependencies against the server-owned managed
branch tip, then composes the exact commits in dependency-list and deliverable-ID
order. A single or dominant commit is adopted directly; genuinely divergent inputs
produce one multi-parent composition commit. The resulting commit becomes the successor's immutable
`base_commit`, while `dependency_git_sources` records the dependency task,
deliverable, branch, and commit. A reused pristine worktree may receive this
composition before its next lease; a worktree with authored or dirty work and
no matching provenance fails closed. Missing, ambiguous, unaccepted, moved, or
conflicting Git dependency state likewise fails preparation before the agent is
woken and opens one routed high-priority preparation blocker. A true merge
conflict additionally persists the exact base commit/tree, ordered accepted
sources, failed source, and conflicted paths. Its supervisor uses
`team_start_dependency_composition_recovery` (or the equivalent Admin action) to
select an active project resolver and create one pinned integration-boundary child
task. The recovery worktree fetches and verifies those exact sources without
pre-merging them. Generic blocker resolution is refused. The submitted recovery
deliverable must name every exact source deliverable and its managed branch tip
must contain the original base and every source commit. Normal review/acceptance
then resolves the blocker and pins the target task to that reviewed commit; target
preparation verifies and adopts it without replacing authored work. A meaningful
source-generation change supersedes any unfinished recovery and permits a fresh
attempt. Parent/child
containment does not imply inheritance, and direct
dependencies without Git deliverables remain lifecycle-only.

No-progress accounting remains agent-wide. At the configured limit, Team opens
or reuses one blocker with `source_kind: agent_execution_stall` and the agent ID
as source, appends the exhausted dispatch and affected tasks, routes it through
the reporting/escalation chain, and suppresses ordinary task dispatch for the
still-owned affected tasks. Task states remain unchanged. Only source-aware
blocker resolution resets that allowance.

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
bind an agent, task, optional dispatch, worktree path, the task's snapshotted
base branch/base commit,
expected agent branch, exact dependency Git sources, and last verified commit.
For dependency-backed work, the context also stores an immutable composition
manifest: pinned base commit/tree, source relationship and deliverable IDs,
accepted Team review refs and commits, ancestry-reduced frontier, deterministic
source fingerprint, and the composed commit/tree. Required contribution tasks
come from the deliverable contract even when no duplicate dependency edge was
recorded. Accepted review refs are resolved instead of mutable agent branch tips;
legacy accepted rows retain the older branch/context validation.
Artifact-only deliverables,
administrative work, customer-service work, operations work, and tasks with no
deliverable do not need Git contexts. Once a deliverable includes Git metadata,
`team_submit_deliverable` and `team_submit_deliverable_for_review` require a
matching `branch_context_id` plus `git_refs.commit_sha`, verify the exact
server-owned branch tip, and reject missing, wrong-task, wrong-agent,
wrong-dispatch, inactive, or mismatched contexts and commits. When Git metadata
is omitted, the submission rules above still inspect a unique active context
and automatically bind an advanced task branch.

Formal reviewed deliverables created under acceptance policy version 2 use an
additional pre-acceptance gate for integration boundaries and system-created
rejected-candidate corrections. Team freezes the project's release verification
commands, pins the exact review snapshot, and emits
`team.deliverable.acceptance_verification_requested`; the bundled runtime routes
that event to `effect:team:verify-deliverable-acceptance` in a disposable
`container_worker_vm`. Acceptance requires a server-owned passed result with a
durable evidence ref. `partial` and `not_run` may proceed only after an
authenticated operator records a reason through
`POST /deliverables/{deliverable_id}/acceptance-verification-waiver`; failed,
pending, and running checks cannot be waived. Required passed review criteria
also require evidence refs under policy version 2.

Resolved tasks and task reassignment create durable, checkout-specific
`WorktreeCleanupCandidate` records. The periodic Team wake reconciler starts in
staged mode when `worktree_cleanup_dry_run` is omitted: the first eligible safe
pass is an audited dry run, leaves the candidate pending with
`last_safety_check: dry_run_safe`, and schedules it after the retry interval;
the next eligible pass revalidates everything before attempting removal.
Explicit `true` remains audit-only on every pass, while explicit `false` permits
removal on the first eligible pass. The reconciler also exposes configurable
cleanup limit, grace, retry, and interrupted-processing recovery intervals.
Before invoking either the libvirt or container-worker backend it rereads Team
state twice and refuses or defers cleanup while the task is still actionable
for the checkout owner, a preparing/leased dispatch references the task, an
active Git context references the path, the project repository changed, or the
expected retained commit is not durably evidenced.

Both execution backends serialize task-worktree preparation and cleanup for the
session repository. A host coordinator lock is shared by Team preparation and
reconciliation, so both durable Team-state reads happen while new preparation
is excluded; the selected backend then acquires its repository-local lock. It
validates the deterministic path, exact branch and commit, Git registration,
tracked changes, and non-ignored untracked files, then repeats the physical
inspection immediately before removal. Ignored caches are inventoried with byte
counts and may be reclaimed only after those checks pass. Durable evidence
retains the exact file count, byte totals, a SHA-256 of the complete sorted
inventory, and at most the first 200 sorted paths;
`ignored_file_inventory_truncated` says whether the sample is partial.
Refusals remain `recovery_required` with the exact path, branch, failed check,
timestamp, and bounded evidence; successful cleanup is audited before obsolete
unreferenced Git-context metadata is pruned.

`effect:team:wake-due-agents` accepts `run_global_maintenance`, defaulting to
`true` for compatibility. The bundled scheduled wake leaves it enabled. The
queued-Chat and requested-agent routes set it to `false` so unrelated global
candidate-correction, human-correction, review-assignment, and worktree-cleanup
scans do not delay an
immediate dispatch.

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

The initial package remains discretionary rather than automatic. Pending root
deliverables are included in the periodic manager-fallback summary until an
authorized actor requests a release or records `not_planned`. The fallback
dispatch freezes the exact pending deliverable IDs shown in its prompt, so the
manager may batch compatible deliverables from one project and Git base but
cannot act on newly appeared or foreign records through that stale lease.

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

For a new release-review stage, Team validates the full frozen reviewer/profile
set and seals it as one cohort before creating any reviewer handoff. A rejection
is recorded immediately, but it does not create correction work while another
cohort slot is pending. When every current slot is accepted or rejected, Team
creates at most one candidate-correction task and wake containing all rejected
findings in manifest order; if all slots accept, the release advances. A blocked
slot pauses the cohort until `team_retry_release_gate` replaces that same frozen
slot against the same candidate. Releases that already had review rows before
this behavior was introduced are not retroactively grouped and retain the
legacy eager-rejection behavior.

The optional `release_policy.resource_limits` object configures the disposable
verification worker with Docker-compatible `memory`, positive `cpus`, and
positive integer `pids_limit` values. For example:

```json
{
  "verification_backend": "container_worker_vm",
  "resource_limits": {
    "memory": "6g",
    "cpus": 4,
    "pids_limit": 1536
  },
  "verification_commands": [
    {
      "name": "solution tests",
      "cwd": "Src",
      "argv": ["dotnet", "test", "./App.slnx"],
      "timeout_seconds": 3600
    }
  ]
}
```

Admin Web's project release-policy JSON editor persists these values with the
project. Omitted values inherit the verifier defaults: 8 CPUs and 2,048
processes/threads, while memory remains governed by the worker/host default.
Resource limits are infrastructure configuration rather than candidate
evidence, so the live project policy is applied when each disposable verifier
session is created, including an authorized retry of the same pinned candidate.

A nonzero verification-command exit is a candidate failure: the release is
terminally rejected and requires changed, reviewed content in a new release.
Creating another release with the same deliverables and failed tree is refused.
Verification candidate failures, closed release-review cohorts containing at
least one rejection, and legacy uncohorted rejections create candidate-pinned
system correction tasks. When the generated task recursively
resolves with current accepted, reviewed Git deliverables, Team creates one
deterministic successor package and correction-aggregate promotion
automatically; the project lead does not submit another release request. The
review decision and successor intent are one atomic state transition. Scheduled
global maintenance performs the same idempotent reconciliation for interrupted
or older states. A packaging or validation failure opens a durable blocker on
the correction owner instead of silently returning release responsibility to
the manager. Initial releases and post-release-feedback successors remain
explicit requests.

Candidate-correction successor blockers use the typed
`candidate_correction_successor` source and `downstream` resolution scope. They
remain visible at project/release level but do not make the already-terminal
correction task unresolved or propagate through task dependencies. Admin Web
offers three source-aware actions: retry packaging against current evidence,
create a dependency-free recovery task pinned to the accepted correction branch
and full commit SHA, or authorize a verification waiver with an audited reason.
Generic blocker resolution is refused for this source. Reconciliation keeps
retrying while its blocker is open, prefers a current accepted downstream
deliverable with `verification.status: passed`, and checks for an existing
direct successor before task readiness so interrupted completion resolves the
blocker without creating another package.

Only timeouts and infrastructure failures leave verification retryable, and only
a review recorded as process-blocked may be replaced. Release-review decisions
are immutable: use `rejected` for a candidate defect and reserve `blocked` for a
non-candidate process or evidence-access blocker with a concrete finding.
`team_retry_release_gate` and the Admin API/UI create a linked immutable
successor attempt against the same pinned SHA and persist the audited reason and
requesting agent/operator identity; they never rewrite the failed attempt. An
agent should retry verification only after concrete evidence that the
infrastructure or timeout cause recovered, with a reason stating what changed;
without that evidence it must escalate the blocker. The agent-side retry budget
for an unchanged candidate is one: if an agent-requested replacement
verification attempt also fails, no agent may schedule another retry. The
current run and concrete recovery requirement must be escalated to an operator,
who may authorize a later attempt after an independently verified intervention.
`team_recover_release` remains limited to failed Git feature/release promotions
and is not a release-gate retry mechanism.

Set `allow_ungated: true` only for an intentionally ungated project; a missing
policy blocks a new Git release rather than silently publishing it. Admin Web
project create/edit forms expose the policy as JSON and release rows show the
pinned candidate, verification state, release-review state, approved SHA, and a
retry action only for an eligible typed gate failure.

#### Human Release Review, Corrections, And Feedback

`Project.release_policy.human_review_requirement` can add Admin reviewer-group
IDs, a checklist, test instructions, and `manual` or `workflow` preparation.
The frozen candidate manifest snapshots the directory, configured groups,
initially eligible users, checklist, instructions, and preparation mode. The
single gate sequencer advances candidate/hierarchy validation, verification,
agent release reviews, target preparation, human review, and final publication
in order; it creates only the next missing stage. At request creation it
resolves the frozen group/directory requirement against current Admin
membership, active users, and directory-scoped
`team.human_reviews.decide` permission rather than trusting the policy-time
eligibility list.

A human request is pinned to candidate SHA and tree. Manual requests become
`pending`; workflow requests become `preparing` and emit
`team.release.human_review_preparation_requested`. If no eligible user exists,
the same request becomes `blocked` with a canonical assignment-unavailable
blocker routed to directory-scoped `admin.owner` recovery while the project
lead retains visibility. Audited reassignment increments
`assignment_generation`; preview retry increments `preparation_generation`.
Preparation results can persist only safe URL/alias, expiry, instructions, and
evidence metadata. Secrets remain in host-owned workflow/provider state; URL
userinfo and secret-bearing query or fragment parameters are rejected.

Decisions are Admin-only. The server derives the actor from a real authenticated
session, rejects client actor/user fields, requires directory-scoped
`team.human_reviews.decide`, current group or audited explicit assignment,
privileged confirmation, optimistic request revision, idempotency key, and
candidate match. There is no MCP human-decision tool. Admin routes are:

- `GET /api/addons/team-addon/human-review-requests?assigned_to=me`
- `GET /api/addons/team-addon/human-review-requests/{id}`
- `GET /api/addons/team-addon/human-review-requests/{id}/review-bundle`
- `POST /api/addons/team-addon/human-review-requests/{id}/decisions`
- `POST /api/addons/team-addon/human-review-requests/{id}/reassign`
- `POST /api/addons/team-addon/human-review-requests/{id}/resume`
- internal authenticated `POST .../{id}/preparation-result`

The review-bundle route is available only to a real authenticated human-review
reader. It writes a source-only ZIP for the exact pinned candidate and appends
`EVANDER-REVIEW.json` with its release/request provenance and test
instructions. It includes neither Git history nor credentials, and refuses a
release ref that no longer matches the recorded candidate commit and tree.

`blocked` preserves the same request and candidate for source-aware recovery.
`changes_requested` atomically records the decision and findings, ends the
immutable attempt without publication, and creates or reuses one correction
`TeamCase` with a coordination-only parent and one immutable-text child per
finding. Every new finding must select either `release_candidate` or one or
more named candidate deliverables as its affected work. A single-owner
deliverable selection routes to that owner; the entire candidate, a
multi-owner selection, or a selection whose historical owner is unavailable
routes to the project lead without an additional routing step. The
`legacy_unspecified` scope and ambiguous-routing metadata remain readable only
for recovery of findings created before explicit scope was required. The
finding child starts from the verified rejected candidate and must produce one
accepted, reviewed correction deliverable. Accepted historical tasks,
deliverables, reviews, and candidates are never reopened.

After every finding child has exactly one current accepted, reviewed correction
deliverable, Team derives each proposed resolution and evidence and
automatically creates the changed successor release. The coordination parent
produces no deliverable and finishes when that successor exists. The successor
names `supersedes_release_package_id`, uses the predecessor candidate as
baseline, preserves original scope, carries recursive contribution lineage plus
every open finding and proposed resolution, requires a changed tree, and reruns
all gates. Human approval must explicitly confirm every carried finding before
it resolves and publication can proceed. Publication additionally requires
candidate, approved, and published SHA equality plus one linked succeeded
canonical-push promotion.

`POST /api/addons/team-addon/release-packages/{id}/report-issue` handles
post-release regression or adjustment intake. It preserves the published
release, creates or reuses a deterministic case and project-lead triage root,
and leaves rollback or a later successor to separate controlled decisions.
Creating that successor is a separate release request: it must name both the
published `supersedes_release_package_id` and the still-open linked
`post_release_feedback_case_id`, and its correction deliverables must be
current accepted contributions from resolved tasks in that case.

Five add-on-owned effect providers complete these flows:
`effect:team:publish-review-branch` (publish a source branch or commit from the
project's managed bare repository into a durable
`team/<team-id>/reviews/...` ref),
`effect:team:record-git-promotion` (mark a promotion succeeded/failed and attach
branches to `git_refs`), `effect:team:verify-release-candidate` (create a
disposable container-worker session, clone by `managed_repo_id`, check out the pinned candidate, run the
ordered commands, and destroy the session),
`effect:team:mark-release-published` (publish or block a release package), and
`effect:team:consolidate-git-branches` (deterministic composition
of explicitly pinned source SHAs, fast-forwarding when one source contains the
validated result and otherwise publishing one meaningful multi-parent commit,
aborting and failing on conflict — never auto-resolving). The central managed
Git service applies the configured host commit identity, compare-and-swap
updates the Team target ref, and removes the temporary worktree and operation
ref on every exit. The core `effect:git:push-branch` effect performs an
expected-commit and destination-lease preflight. Its optional
`expected_destination_commit_sha` input additionally requires the upstream
destination to be at an exact reviewed base before publication. If a generated
destination does not exist yet, the pinned base must be an ancestor of the
approved candidate and an empty-ref lease rejects concurrent branch creation.
The Team canonical-push route always supplies the release package's immutable
`canonical_base_commit_sha`. Initial attempts set it to the release base;
correction successors retain the predecessor's canonical destination base while
using the rejected candidate as their separate correction merge base. Existing
non-Team workflow definitions that omit the optional input retain their previous
push behavior.

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

Teams may carry an optional `max_cost_usd` estimated cap. New caps apply to a
budget window rather than growing a lifetime authorization: an operator may
start the next window with a revision-checked reset and required reason, or
opt into a monthly reset at 00:00 UTC on the first day of each month. Monthly
windows do not carry unused spend forward. The wake loop checks the active
window before leasing due agents; when it reaches the cap, future Team wakes
for that Team are skipped until the budget is reset, renewed, raised, or
cleared. The cost payload retains the lifetime total alongside the active
window total and boundary.

Existing caps remain lifetime-based until an operator performs their first
budget reset; upgrading does not silently authorize more spend. This is an
operational safety rail, not billing-grade enforcement: totals are derived from
recorded model-usage events, the local pricing catalog, and optional Claude
transcript backfill. Cost payloads expose `coverage_status` and
`coverage_reasons`; operators should treat partial coverage as a reason to
inspect the underlying usage data before making spend promises.

Each Team also stores its own `cost_warning_ratio`, which defaults to `0.8`.
When a governed Team reaches that portion of its own `max_cost_usd`, the
governance manager receives a `cost_cap_warning`; reaching the cap produces
`cost_cap_reached`. This is Team policy rather than workflow-pack configuration,
so refreshing the Team Runtime pack cannot change it. The ratio must be greater
than zero and no more than one; clearing it through the Team API resets it to
the default.

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
