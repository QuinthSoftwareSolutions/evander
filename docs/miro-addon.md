# Miro visual collaboration

The opt-in Miro Add-on lets agents and workflows use editable Miro canvases as shared visual
context. Architecture views, application and sequence flows, explanations, human feedback,
and another agent's review all use the same host-side service. Operators decide when to publish,
monitor, request a review, or wake an agent by composing normal Evander workflows.

## Setup

1. Enable **Miro Add-on** in Admin Web **Add-ons** and restart the services listed there:
   Admin Web, gateway, trigger orchestrator, and effect orchestrator. Install the updated package
   dependencies first; the adapter uses the MCP Python SDK's managed HTTP client interface.
2. Open **Miro**, select the owning directory, and unlock privileged operations in Evander.
   Create a named connection with the public Admin Web callback URL ending
   `/api/addons/miro-addon/oauth/callback`. HTTPS is required except on loopback in development.
3. Enable the required `miro_*` tools in each agent's normal session policy. Under **Session board
   permissions**, select the exact agent session, enter a board ID or URL, and grant **Read** or
   **Read and edit**. Connection inheritance makes the connection available; every session still
   needs its own board grant. Permit workflow use separately. Board creation is separately opt-in;
   a successfully created board receives read/edit access for its creating session only.
4. Save, choose **Connect account**, and follow **Continue to Miro authorization**. Select the Miro
   account and team containing the intended boards. Refresh the page when authorization completes.
   Writes use that account; Evander separately records the responsible agent or workflow.
5. Use **Check connection** to discover the current upstream tool catalog. Match the local daily
   call budget to the allocation for this connection. Each actual tool invocation, including each
   page and readback, is counted across gateway and workflow processes. The default is 100/day UTC.
6. Compose a workflow or ask an authorized agent to read the approved board. Polling starts only
   when a configured Miro trigger runs. Enabling the add-on alone never starts agent work.

The Miro page follows Admin Web's selected light or dark theme, including changes while the form
is open. When opened directly, it uses the saved Admin Web preference or the system theme.

Connections inherit into descendant directories; a child connection with the same ID shadows its
parent, even when the child connection is disabled. Edit inherited connections in their owning
directory. Settings use revision checks to avoid overwriting another operator's edits.
OAuth registration credentials, PKCE material, and access and refresh tokens are encrypted in
add-on-private storage with restrictive file permissions. Keep its
key and database in protected host backups. Disconnect invalidates local authorization and pending
callbacks; remove the app in Miro as well when provider-side consent must be revoked.

Grants are bound to the connection, exact board ID, session ID and session creation time. Wakes,
renewals and service restarts retain grants; expiry blocks their use. Deleting and recreating a
session, even under the same name, requires fresh grants. Edit access includes read access.
Administrators can change or revoke a grant with revision checks, including through inherited
connections in the selected session directory. Admin grant requests include the selected session's
creation time; a recreated session requires refreshing the selection before approval. Listing boards
and connection board counts reveals only the current session's grants.

On the first upgraded startup, legacy connection-wide board approvals are invalidated atomically
and retained as private migration evidence. They are never copied to sessions. OAuth credentials,
snapshots and operation history remain stored; retaining history does not grant access. Legacy
`boards` and `session_ids` configuration inputs do not authorize sessions or workflows.

Miro's official server is fixed at `https://mcp.miro.com/`. OAuth discovery and token destinations
must share that exact origin. Guests do not receive credentials or a new internet/firewall route.
The implementation follows Miro's [OAuth connection guidance](https://developers.miro.com/docs/connecting-to-miro-mcp).

## Agent and workflow interfaces

Agents start with `miro_list_connections()` to discover enabled connections in their directory
and its ancestors. The response includes `connection_id`, display name, owning directory,
`inherited`, approved/writable board counts, and the connection's `allow_create_boards` setting.
It reads local configuration without contacting Miro or reading credentials. These settings do
not grant operation permissions or verify the current OAuth connection.

An existing session grant to any other Miro tool automatically makes connection discovery
available, provided that tool is not denied by directory policy. Discovery's own directory denials
and approval requirements still apply. Operators can also grant only `miro_list_connections` for
metadata access; this does not grant board reads or writes.

Every other operation takes `connection_id`. Use an ID returned by discovery with
`miro_list_boards(connection_id="...")` to find approved boards and their write grants. An empty
board list means this session has no board grants on that connection; connecting a Miro account
does not automatically approve or enumerate its upstream boards. Creating a board requires both the
`miro_create_board` tool and the connection's board-creation setting.

Agent identity and directory come from the authenticated transport session. Every Miro workflow
query, effect and polling trigger requires a `session_id` and checks that session's grants, tool
policy (including argument restrictions), lifetime and directory. Workflow directory scope and the
connection's `allow_workflows` setting are additional requirements. There is no session-free workflow board permission fallback. All tools appear in the **Miro** tool
catalog. Discovery is agent-only; matching workflow query/effect names for the other tools replace
underscores with hyphens, for example `miro_read_canvas` → `query:miro:read-canvas`.

| Agent tool | Workflow kind | Purpose and additional inputs |
|---|---|---|
| `miro_list_connections` | Agent only | No inputs; discover enabled connections and their public configuration. |
| `miro_list_boards` | Query | List only this session's approved boards and their read/edit grants. |
| `miro_get_canvas_format` | Query | Follow the composer using `step`; optional `format_name`, `notation`, and `is_repository` load the current diagram guidance. Diagram guidance defaults to `notation="free_form"` when supported, for individually editable items. Returns provider instructions plus `authoring_checks`. |
| `miro_search_canvas` | Query | `board_id`, `result_mode` (`overview`, `areas`, or `matches`); optional `patterns`, `target_id`, `cursor`. Find areas and item IDs before reading. |
| `miro_read_canvas` | Query | `board_id` and `canvas_scope` or `frame_id`/`item_ids`; returns editable SVG, stable item IDs, exact selection, URL and `snapshot_id`. |
| `miro_read_comments` | Query | Same selection; reads bounded complete pagination and returns a comment snapshot. |
| `miro_get_snapshot` | Query | `snapshot_id`; read a retained source with advisory `layout_review`. The agent tool accepts `include_preview=true` to return a reconstructed PNG alongside the snapshot. |
| `miro_get_operation` | Query | `operation_id`; inspect the durable outcome, attribution and snapshot references. |
| `miro_create_board` | Effect | `title`, `operation_key`; creates a board and grants read/edit access only to the creating session. Sharing follows Miro's workspace policy. |
| `miro_create_canvas` | Effect | `board_id`, `svg`, `operation_key`; `frame_id` or `canvas_scope` covering the new content, and optional `source_refs`. Creates editable content, including a separate revision frame. |
| `miro_update_canvas` | Effect | Same selection as a prior read, `svg`, `snapshot_id`, `operation_key`; optional `source_refs`/`allow_delete`. |
| `miro_reply_comment` | Effect | Board/selection, `comment_id`, `text`, `snapshot_id`, `operation_key`; replies to an existing thread. |
| `miro_resolve_comment` | Effect | Board/selection, `comment_id`, `snapshot_id`, `operation_key`; `resolved=false` reopens a thread. |

For canvas navigation, grant `miro_search_canvas` through the normal session tool policy, then:

1. Call `miro_search_canvas(connection_id="...", board_id="...")` for an overview.
2. Find relevant content with `result_mode="areas"` or `"matches"` and `patterns=["Order service"]`.
   Use `target_id` to narrow the search or the returned `data.next_cursor` for another page.
3. Pass a result's `scope` as `canvas_scope={"x": ..., "y": ..., "width": ..., "height": ...}`
   to `miro_read_canvas`, or select known `frame_id`/`item_ids`. Coordinates are integers and
   dimensions must be positive. Choose an area or IDs, not both. A frame selection includes its
   descendants; Miro may also include ancestor frames for selected items.
4. Retain the returned `scope` and `snapshot_id`, and use that exact selection for updates.

The matching workflow search is `query:miro:search-canvas`; its `data` contains the upstream
results and pagination cursor. Search results are external content, and an overview can contain
only a sample of board regions. Neither search pages nor a bounded SVG read claim to represent
the entire board. An unscoped read of a populated board returns `scope_required` with search
guidance. Existing workflows that read or monitor a populated board must select an area, frame,
or items.

For an empty board, Evander first verifies emptiness with canvas search, then reads an initial
area at `x=-500, y=-500, width=1000, height=1000`. This lets board creation finish its readback
without guessing a widget ID. The area is explicit in the snapshot and retained for subsequent
readback, reconciliation, and polling. Select a larger or different area when adding content
outside it. Each search and scoped read consumes a separate upstream call.

Obtain the current canvas specification before authoring SVG. Call `miro_get_canvas_format` first
without a step, follow it with `step="design"` for a new composition or `step="edit"` for a
revision, and load `step="dsl"` before writing SVG. For collaborative diagrams, including flowcharts,
architecture, ERD and UML, request `format_name="diagramming", notation="free_form"`. When a diagram
format is requested without a notation, Evander supplies `free_form` if Miro advertises the notation
parameter. Explicit notation choices and older schemas without that parameter remain supported.
Stop if any guidance call fails. The payload is Miro's structured editable canvas format, not an SVG
image upload.

Human-agent collaboration requires separate native shapes, text, and connectors so people can
select, move, relabel, and reconnect individual elements on the board. Use the relevant diagram
conventions with those native items. A Mermaid diagram widget can look polished, but its inner
nodes and edges are not individual Miro items. Use it only when the user explicitly requests a
code-based diagram. A provider guide's preference for Mermaid does not override the collaboration
requirement. Do not flatten a collaborative diagram into a single HTML or image widget either.
Existing Mermaid widgets need to be recreated as native nodes and connectors to gain individual
editability; these instructions do not convert existing boards automatically.

A collaborative flow uses native connectors with `data-start`, `data-end`, `data-arrow`, attachment-side
attributes, and `data-content` for its label. Do not draw arrowheads as separate polygons or place a
separate text element over a connector: Miro can normalize the polygon orientation and move the
label independently. Old `diagram_*`,
`layout_*`, and `context_*` remote tools are not used. Parameter schemas are negotiated against an
allowlisted set of remote tools; an unknown required parameter fails closed instead of guessing.
See [Miro's current canvas and comment tools](https://developers.miro.com/docs/miro-mcp-tools).

The same inputs are available on `query:miro:get-canvas-format`. The legacy `format_name="diagram"`
maps to `diagramming` when the connected provider advertises that replacement. New writes reject
unbound lines/paths, duplicate local IDs, references to missing/deleted widgets, frames or other
connectors, and unsupported connector direction/routing attributes before contacting Miro. These
checks apply to proposed writes; older snapshots remain readable and completed operations retain
their original outcomes on retry. They validate connector structure, not the intended business flow
or pixel-level overlap.

For example, a native top-down link keeps its direction and label together. Connectors can precede
their endpoint widgets in the SVG so the shapes appear above the connecting line:

```xml
<svg xmlns="http://www.w3.org/2000/svg">
  <line id="next" x1="100" y1="80" x2="100" y2="160"
        data-start="first" data-end="second" data-arrow="end" data-shape="elbowed"
        data-start-side="bottom" data-end-side="top" data-content="Next" />
  <rect id="first" x="0" y="0" width="200" height="80" data-content="First" />
  <rect id="second" x="0" y="160" width="200" height="80" data-content="Second" />
</svg>
```

Keep the readback scope large enough for all the new content, including return loops and labels;
the initial empty-board area is not a viewport that scales a larger SVG. Inspect the returned
`result_snapshot_id` with `miro_get_snapshot(include_preview=true)`, checking that expected nodes and connectors survived as
separate items with stable Miro IDs and that their labels survived,
and read uncovered areas when necessary. Review the Miro rendering for direction and legibility.
An `applied` outcome records successful delivery and readback, not a visual-quality assessment.
Before subsequent revisions, read the current board to include human edits. Update only the intended
items using their stable `data-miro-id` values and retain the rest of the composition.

Canvas reads, retained snapshots, and new completed canvas operations include `layout_review`.
The report names stable item IDs for shared branch exits, overlapping reconstructed routes, and
potential crossings of unrelated item bounds. These are advisory findings: a delivered write stays
`applied` even if its layout needs repair. Diagnostics do not change snapshot identities, stale-write
checks, permissions, or durable retries. Workflow queries/effects expose the same report.

The optional PNG is a **reconstructed layout, not a Miro screenshot**. It uses native item bounds
and explicit attachment sides because readback may substitute placeholder connector coordinates.
Solid red links show reconstructed straight/aligned routes; dashed links show relationships whose
actual routing is unknown. Elbow and curve routing, rich labels, rotation, clipping, and unsupported
geometry need inspection in Miro or an actual screenshot when available. The renderer never loads
external URLs or executes SVG/HTML. Reviews are bounded to 500 items and 50 warnings; coverage
reports truncation and unreviewed items/routes. `no_detected_issues` is not visual approval.
Preview retrieval uses the existing `miro_get_snapshot` grant and makes no upstream calls.

Use an iterative completion check:

1. Create or update native items, then inspect the returned report and view its result snapshot.
2. Separate decision branches with distinct exit sides. Keep the main path below; route a bypass
   through an empty side lane into the later node from the side. Move nodes to make room rather
   than drawing through skipped nodes or adding fake anchor widgets.
3. Read current state and repair only the affected items, preserving human edits. Give each changed
   repair a new operation key; identical retries keep their original key.
4. Read/view the repair result again. Normally stop after three repair passes and explain any
   remaining issues or unavailable visual evidence. Stop immediately on an uncertain write.

Comments and board content are marked `external_content`; workflows and agents must treat them as
input, not policy, authorization, or trusted instructions. Agents initiate new questions with board
annotations; native comment operations work on existing human-created threads.

Snapshots are durable add-on artifacts, retrieved with `miro_get_snapshot`. Operation records retain
the submitted diagram source and `source_refs` (for example a managed repository's pinned commit
and a task reference), plus baseline/result snapshot IDs. Code stays in the managed Git flow;
diagram publication does not grant source checkout transport or unrestricted file upload.

## Safe revisions and recovery

Read a selection, then use its snapshot when updating. The service rereads exactly that selection
before sending a write. If it has changed, the operation returns a conflict without writing. Updates
may reference only IDs in the snapshot. Omitted items remain unchanged. Delete an item by keeping
its ID, marking it `data-deleted="true"`, and explicitly setting `allow_delete=true`. Creating
content requires fresh IDs. An SVG payload is limited to 1 MiB, rejects active content and XML
entities, and is never rendered as trusted HTML in Admin Web.

Writes to the same board are serialized across host processes, even through different connections.
A durable `operation_key` identifies the caller's exact operation and arguments across all boards
in a connection. Concurrent requests sharing a key are serialized. Reusing it returns
the existing outcome, including board creation after reaching the session's board limit;
changing the payload or caller with the same key is a conflict. A write
acknowledged by Miro is followed by a fresh readback snapshot. This is observation, not an atomic
compare-and-swap guarantee: a human can still edit during the remote operation.

The returned `status` is `applied`, `not_applied`, or `uncertain` (interrupted records may initially
show `running`). Workflows must branch on status before treating publication as complete. A timeout
or partial remote failure leaves the operation uncertain and blocks subsequent writes to that board.
Repeating the operation key does not resend it. In **Miro → Activity**, use **Read back**, inspect the
observed snapshot and live board, then record evidence and the outcome. **Mark not applied** does
not automatically retry; schedule a fresh operation key only after verifying absence of the write.
For an uncertain board creation without a returned ID, inspect the selected Miro team manually and
include the identified board in the resolution evidence; grant that board to the intended session
under **Session board permissions** before resuming agent work.
If Miro acknowledged creation and returned a board ID, an operator may **Mark applied** with
explicit evidence even when a later deletion or sharing change prevents readback. This confirms
the original creation; it does not restore board access or replay the operation. Canvas and comment
writes still require readback before resolution, as does marking an identified creation not applied.
Operators may inspect and recover operations in their selected directory and its descendants.
An inherited connection does not permit recovery of operations owned by an ancestor or sibling.

## Approving a session through chat

The optional workflow-only `query:miro:board-access` query checks local grants. Give it a
`connection_id`, `session_id`, and board ID/URL. For personal-assistant conversations, bind
`message_cursor` to the original Teams trigger cursor and `approver_user_id` to the operator's fixed
user-to-assistant mapping. It resolves one exact board from the stored message or a matching pending
request. It returns current access, a scoped pending-state key, pending read/edit requests and
approval question text. These queries never contact Miro.

`effect:miro:grant-board-access` takes the same connection, session, exact board reference and source
message fields, plus `access` (`read` or `edit`) and a stable `operation_key`. It checks the original
inbox author and direct chat, the live session instance, source freshness and the exact board. An
old message predating the session cannot approve a recreated session. Confirmation without a board
URL must match an unexpired pending request for that user, conversation, session instance and access
level, after the question has been delivered. The add-on retains approval provenance and deduplicates by the source message and session;
redelivery cannot restore a revoked grant. Agent MCP tools cannot grant their own board access.

Use `evander_miro_addon.approval_workflow.approval_fragment` to build the opt-in workflow branch.
It uses the existing Smart switch with a 0.98 approval cutoff and the configured Decisions provider.
Only explicit permission selects a grant branch. An ordinary request to read or edit an unapproved
board asks a short approval question, stores pending consent using the existing workflow-state
nodes, and activates that request only after the question is sent. Pending consent uses the
configured approval TTL (one hour by default). Quoted, forwarded, hypothetical, negated and
ambiguous statements never count as approval. Only the current message and minimal pending-board
metadata are sent to classification, not general conversation history.

A successful grant is confirmed before an accompanying or pending task continues. Permission-only
messages finish after confirmation. State conflicts, failed delivery and grant errors do not start
an unapproved edit. Keep the mapped user and target session bindings operator-controlled; never
bind them to classifier output or user-supplied identity fields. Update workflow-generated AGENTS.md
with the included `assistant_guidance` helper so assistants explain chat approval and offer the
Miro settings page as a manual fallback.

The [session approval example](examples/miro-session-approval.v1.json) shows the complete branch with
synthetic placeholders. To prepare a change to an existing operator-owned pack, export its current
editor document and pack, keep a backup outside the repository, and run:

```bash
python -m evander_miro_addon.approval_workflow baseline.json candidate.json --bindings bindings.json
```

`bindings.json` supplies the keyword arguments documented by `add_approval_fragment`, including the
graph, insertion edge, enqueue node, fixed user/session/message bindings and public Admin Web URL.
When model or reasoning routing has several enqueue branches, list every additional branch in
`additional_enqueue_node_ids` so approved task continuation reaches each existing route.
The command writes a separate candidate and never saves live configuration. Review it, validate the
baseline and candidate, then import/save through Admin Web or the revision-gated workflow service.
Keep customer-specific packs and bindings in operator-owned storage outside this repository.

## Upgrading existing installations

Drain Miro work and stop Admin Web, gateway, trigger orchestrator and effect orchestrator together
before upgrading. Back up protected add-on state and the OAuth key. Deploy the new code, then start
all four services; do not mix old and new authorization implementations. The first upgraded store
initialization invalidates the legacy approvals once. No live workflow currently lacking a
`session_id` can use Miro until it is updated. Grant existing boards to individual sessions as
needed, or enable the reviewed chat-approval workflow.

Verify the new grant API, both orchestrator revisions, and denial from a second session with the
same connection and tools. Old operation records remain available to appropriately scoped operator
recovery; session operation receipts require their owning session instance. If rolling code back,
keep Miro disabled until authorization state is reviewed: restoring a pre-upgrade database would
also restore its former shared approvals.

## Workflow-owned monitoring and examples

`trigger:miro:board-changed` and `trigger:miro:comments-changed` take `connection_id`, `session_id`, `board_id`,
optional `frame_id`/`item_ids`, `poll_interval_seconds` (default 900, minimum 60), `include_self`
(default false), and `emit_initial` (default false). Canvas monitoring also accepts `canvas_scope`
instead of frame/item IDs. It watches that selection, including when a previously empty board
gains content; changes outside the area are not observed. The first complete read normally establishes
a baseline. A changed observation produces a durable event with board/frame references, previous
and current snapshot IDs, exact `scope`, URL, origin, and observation time. Pending events survive process restarts
and remain pending until publication acknowledgment. Changes to a subscription's selection establish
a fresh baseline. When a child connection overrides the monitored connection, polling also resets
the baseline and discards pending observations tied to the old connection. Event IDs remain unique
across that change, so a delayed acknowledgment cannot consume a new event.
Comment reads use Miro's `limit`/`offset` pagination, with at most 20 pages of
50 comments per observation. Incomplete pagination and read failures never advance the snapshot
checkpoint.

Changes attributable to recorded Evander writes are suppressed by default, including across
connections and frame selections. A human edit that breaks the recorded sequence of item states
still emits an event, including edits made between a write and its readback. Suppression requires
an observed state matching the requested edit; new items also require the mutation's SVG receipt.
Unrecognized changes, older operation records without this evidence, and comment replies without
a verifiable message receipt are conservatively treated as external. Enable
`include_self` deliberately when coordinating agents via board events, and add workflow-level
routing/deduplication rules. Only the observed state is known: polling can miss edits that are
reverted between observations. Miro [retired experimental webhooks](https://developers.miro.com/changelog/removed-experimental-webhooks-support),
and its [daily MCP allowances](https://developers.miro.com/docs/mcp-usage-and-daily-limits) apply even
to read-only tools. Poll less frequently or narrow the selection when the available call budget is
small. Multiple named connections to one Miro account must share its real upstream allowance;
Evander's local per-connection counters do not discover other clients' usage.

[Example workflow pack](examples/miro-workflows.v1.json) contains four optional graphs: publishing,
directed explanation, monitored feedback, and an agent review handoff. It is a template, not a
runtime pack: replace the directory, board, connection and session placeholders; inspect prompts
and permissions; then import through the existing workflow editor/API with revision checks. Never
copy it directly over live workflow files. The examples reuse existing agent-prompt effects and
are independent of the Team Add-on. Operators may substitute Team routing/review nodes.

## Verification

Run `pytest -q tests/test_miro_addon.py` plus the add-on discovery, workflow-editor catalog, and
Admin Web add-on suites. Tests use stateful synthetic Miro responses and synthetic OAuth secrets;
they cover grants, conflicts, concurrency, uncertain writes, feedback, snapshots, polling recovery,
budgets, and credential custody. They do not authorize a real Miro account.

Before enabling production workflows, use a designated test board to verify OAuth and capabilities,
create and read back an editable diagram, have a human edit it, reject the stale update, publish a
fresh revision, reply to a human comment, and exercise each selected workflow. Verify the gateway,
Admin Web and both orchestrators load `miro-addon`; check activity and call budgets after one
polling interval. Disable the relevant workflow to stop monitoring. Disabling the add-on is restart-
gated and preserves its state; removing its private state does not alter core Evander records.
