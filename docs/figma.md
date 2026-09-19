# Figma collaboration

The optional `figma-addon` lets agents work on native, editable UI mockups without an open
desktop editor. Collaboration is defined by workflows: separate proposals followed by composition,
one author with reviewers, or multiple authors sharing the same screens. The add-on does not
require the Team add-on and does not give agents Figma credentials or general internet access.

## Deployment prerequisite

Figma currently restricts its hosted MCP server to approved clients. Register an approved
**Evander** OAuth client; another application's identity or a personal REST token is not a
substitute. Use a paid Full seat with edit permission for the target files. The implementation
uses the real `evander` client identity and discovers/validates the upstream tool schemas.

Authoring remains disabled until an operator runs the connection test in a disposable registered
file. That test creates native frames and text, changes the text, checks readback, renders a PNG,
and removes its temporary page. A successful test is bound to the connection configuration and
OAuth authorization generation. Reconnecting requires another test. A failed or interrupted test
does not open the gate; inspect its operation record for any required cleanup.

Local tests execute the production canvas interpreter against a simulated Plugin API. They are
not evidence of Figma approval or a successful hosted connection. Do not roll out authoring until
the real test passes with no Figma editor open.

Provider references, checked September 2026:

- [Hosted MCP setup and client approval](https://developers.figma.com/docs/figma-mcp-server/remote-server-installation/)
- [Native canvas editing and seat requirements](https://developers.figma.com/docs/figma-mcp-server/write-to-canvas/)
- [Plugin API guidance](https://github.com/figma/mcp-server-guide/tree/main/skills/figma-use)
- [Comment API](https://developers.figma.com/docs/rest-api/comments-endpoints/)
- [REST account identity and scope](https://developers.figma.com/docs/rest-api/users-endpoints/)

## Setup

1. Enable **Figma Add-on** in Admin Web **Add-ons** and restart the listed services, including
   Admin Web, gateway, and workflow orchestrators. No additional worker service or guest port is needed.
2. Configure the approved client secret in the host service environment. Configure a separate,
   scoped REST token if using comments or external feedback polling. Posting comments also requires
   `current_user:read`: the add-on pins the REST account before posting so it can verify delivery
   after a lost response. Never put either credential value in a session, workflow, prompt,
   configuration JSON, or repository.
3. Open **Figma**, select the exact directory, and add the approved client ID, secret setting name,
   and registered HTTPS callback ending in `/api/addons/figma-addon/oauth/callback`. Select
   **Connect** and complete Figma's consent flow. OAuth refresh tokens are retained on the host
   with mode `0600`; the access token refreshes before expiry. Disconnect removes the locally
   held authorization; use Figma's account settings if provider-side revocation is also needed.
4. Register a disposable Figma Design file and run **Test unattended authoring**. Then register
   the working files and assign session read, comment, and edit capabilities.
5. Grant sessions the MCP tools `figma_list_targets`, `figma_query`, and/or `figma_execute` as
   appropriate. Tool permission and file capability are both required. Reviewers can use
   `figma_execute` with only read/comment file access to capture, import, and discuss snapshots;
   canvas editing additionally requires both read and edit.

Directory configuration and records live in `runtime/addons/figma-addon/state.db`. Settings use
revision-checked writes through the add-on UI/API. Scope is an exact directory, with no implicit
inheritance. `config/figma/settings.json.example` shows the settings shape; do not write the live
database directly. OAuth data lives separately in `runtime/addons/figma-addon/oauth.json`.

Each target binds an exact file key to one connection. Grants name exact session IDs. Empty
`edit_roots` permits edits throughout that registered file; a nonempty list permits edits only
within those node subtrees. Subtree authors cannot create new pages. Creating a working area
within a granted page/frame creates an auto-layout frame. Replacing a file registration preserves
its explicit grants, so review access before saving a different file key.

## Tools and request contract

`figma_list_targets` discovers granted targets and capabilities. `figma_query` accepts a
`DesignRequest` with `operation` set to `pages`, `inspect`, `variables`, `comments`, or `history`.
`figma_execute` accepts the same typed request contract for authoring and artifact operations.
The server derives session and directory authority; requests cannot supply a directory or raw code.

| Operation | Required inputs beyond `operation` and `target_id` |
| --- | --- |
| `pages` | None; discover page IDs. |
| `inspect` / `variables` | `node_id`; inspect returns native structure and `revision`. |
| `comments` / `history` | None. |
| `create_area` | `name`, `request_id`; optional `node_id` plus `expected_revision` to create inside a node. |
| `edit` | `node_id`, `expected_revision`, `request_id`, `edits`. |
| `compose` | Destination `node_id`, `expected_revision`, `request_id`, `source_snapshot_ids`. |
| `comment` | `message`, `request_id`; optional `node_id` or root `comment_id` for a reply. |
| `snapshot` | `node_id`, `request_id`; choose a renderable frame. |
| `import_snapshot` | `snapshot_id`. |
| `reconcile` | `operation_id`; captures evidence, never blindly repeats a write. |

An edit is `create`, `update`, `clone`, `move`, or `remove`. Creation supports `FRAME`, `TEXT`,
`RECTANGLE`, `ELLIPSE`, and `INSTANCE`; instances reference an existing component ID. Editable
properties include text, loaded fonts, dimensions, position, fills, corners, auto layout and sizing,
plus existing text styles and color-variable bindings. No arbitrary JavaScript, asset URLs, or
network requests are accepted. Work incrementally: create a container, then use its returned ID
in the next batch. Inspect existing components and query variables/styles before inventing new ones.

Example after inspecting frame `12:34`:

```json
{
  "operation": "edit",
  "target_id": "mockups",
  "node_id": "12:34",
  "expected_revision": "<revision returned by inspect>",
  "request_id": "task-184-header-attempt-1",
  "edits": [{
    "action": "create", "parent_id": "12:34", "kind": "TEXT",
    "properties": {"characters": "Production overview", "font_size": 28}
  }]
}
```

Results for durable operations include `operation_id`, `status`, and, on success, `result`.
Only `succeeded` means completion. Reuse the same request ID and arguments after a timeout to
retrieve the recorded outcome. Reusing an ID for different arguments is rejected. After changing
the intended edit or its base, use a new request ID. A stale revision requires another inspection
and a considered update to the intended edits.

Before changing the canvas, the add-on checks each intermediate batch state against the
1000-node inspection limit, including descendants introduced by clones, composition, and
instances. Oversized batches return `rejected` without changing the canvas or blocking the file.
Use a smaller batch or working area and a new request ID after such a rejection.

## Review snapshots and recovery

A snapshot contains `preview.png`, `design.json`, and `README.md` in an immutable published
archive, with a protected artifact-space version, SHA-256 integrity, and exact source node URL.
Capture checks that structure did not change around rendering. Composition accepts retained
snapshot IDs from the same registered file and rejects changed source frames; it does not perform
a pixel merge or overwrite an accepted snapshot. New revisions receive new IDs.

`import_snapshot` checks the recipient's current file grant, then stages the three files as
session-owned communication artifacts. Use `communication_copy_artifact_to_workspace` and the
normal receive policy to materialize them in either execution backend. A reference string alone
does not copy files. For Team delivery, submit the actual imported files using `artifact_paths`;
retain the Figma snapshot ID/URL in the deliverable. Standard Team review/import then handles
the published Team bundle without requiring Figma-specific core models.

File locks cover Evander writes across sessions, directories, and gateway/orchestrator processes.
They do not lock out humans in Figma. The interpreter checks the inspected subtree again before
mutation, but Figma does not provide an atomic transaction with external editors. Small edits and
dedicated working areas reduce contention. Review evidence is immutable even if the source changes.
Revision checks cover the inspected node hierarchy and captured properties; they are not Figma
file-version locks and do not cover every Plugin API property or changes to external libraries.

An uncertain write blocks further writes to the file. Inspect it with `reconcile` or **Inspect
current state** in Admin Web. Comment POSTs can be reconciled automatically only when one comment
matches the complete posted message and the REST account recorded before posting. Quoted markers,
copies by other users, and old records without a pinned account require operator resolution.
Canvas uncertainty requires an operator to review the returned evidence,
then **Accept current state and close operation**. This records `reconciled`, not a fabricated
success. A new request can then implement any remaining work. If the canvas changes after the
inspection, resolution is rejected. Snapshot failures are read failures and do not block authors.

## Workflow integration

- `query:figma:read`: `session_id` and typed `request`; read operations only.
- `effect:figma:execute`: the same inputs and the same file grants as MCP. Non-successful durable
  operations fail the effect with an operation ID for recovery.
- `trigger:figma:changed`: a registered `target_id`, `origin` (`external`, `evander`, `any`), event
  `kinds`, and optional `poll_external` with `poll_interval_seconds` (default 300, minimum 60).

Operation providers require a session in the workflow's exact directory. They never escalate
that session's file grants. Successful edits and snapshots immediately enter the add-on's durable
event queue. Trigger delivery is acknowledged individually after publication, so out-of-order
acknowledgements and process restarts cannot skip earlier unacknowledged events.

External polling establishes a baseline on its first successful run. Later polls emit new human
comments and **named** versions. These give explicit review milestones without treating every
autosave as another assignment. Polling suppresses the integration's own known comments, deduplicates
events, and persists the next poll time even on rate-limit/authentication failure. Version traversal
is bounded to ten pages and fails visibly if exceeded. Native file changes are not a real-time lock.

The three example packs in `config/workflow-packs/figma-*.v1-example.json` use pre-existing sessions
and working frames. The request event supplies the design brief and node IDs; no sample is
automatically installed or enabled. Replace the session/target aliases and route acceptance through
your chosen review workflow. The separate-proposals example consumes an explicit acceptance event
containing snapshot IDs; receiving a comment by itself never authorizes composition.

| Example pack | Request event payload |
| --- | --- |
| `figma-proposals.v1-example.json` | `example.figma.proposals_requested`: `brief`, `proposal_a_node_id`, `proposal_b_node_id`. After review, publish `example.figma.proposals_accepted` with `integration_node_id` and `accepted_snapshot_ids`. |
| `figma-review.v1-example.json` | `example.figma.review_requested`: `brief`, `node_id`. Both reviewers receive the same retained snapshot. |
| `figma-shared.v1-example.json` | `example.figma.shared_requested`: `brief`, `node_id`, `assignment_a`, `assignment_b`. Each author captures a completion snapshot; these are individual observations, not a joint acceptance barrier. |

Snapshot effects use the agent prompt's `completed` port. Connect the `error` port to your own
recovery workflow if needed. Request IDs derive from the request event and step, so re-delivering
the same event does not repeat completed Figma effects. Configure the event source as a trusted
workflow input, especially for the explicit acceptance event.

## Verification and limits

Run `PYTHONPATH=src .venv/bin/pytest -q tests/test_figma_addon.py` and the focused add-on/workflow
registration suites. The fixtures cover author/reviewer, separate proposals, shared-screen conflicts,
native font/layout operations, lost responses, durable event delivery, authorization, OAuth refresh,
and immutable artifact handoff. Run the live authoring test separately before deployment.

V1 targets Figma Design, same-file composition, and small incremental changes (50 edits per batch,
1000 inspected nodes, bounded MCP/REST results). It does not implement Figma branching, image import,
custom-font provisioning, library publishing, or production code generation. REST OAuth token
rotation is owned by the operator's host credential provisioning; the built-in refresh flow is for
the approved MCP OAuth connection. Disabling the add-on and restarting removes its capabilities
without deleting retained state or making any changes to Figma files.
