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
3. Add exact board IDs and grant reads or writes for each. Enable the required `miro_*` tools in
   the agents' normal session policy; that policy authorizes use of connections in their directory
   and inherited from parent directories. Permit workflow access separately. Creation of new boards
   is separately opt-in; successfully created boards join this connection's read/write grants.
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

Older per-connection `session_ids` lists are accepted for compatibility and ignored. Normal session
tool permissions now control agent access. Saving existing settings removes the obsolete list
without requiring a new OAuth connection; directory and board restrictions still apply.

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
board list means no boards are registered in that connection; connecting a Miro account does not
automatically approve or enumerate its upstream boards. Creating a board requires both the
`miro_create_board` tool and the connection's board-creation setting.

Agent identity and directory come from the authenticated session; workflows use their execution
directory, not a directory supplied in the operation input. All tools appear in the **Miro** tool
catalog. Discovery is agent-only; matching workflow query/effect names for the other tools replace
underscores with hyphens, for example `miro_read_canvas` → `query:miro:read-canvas`.

| Agent tool | Workflow kind | Purpose and additional inputs |
|---|---|---|
| `miro_list_connections` | Agent only | No inputs; discover enabled connections and their public configuration. |
| `miro_list_boards` | Query | List only configured boards and their write grants. |
| `miro_get_canvas_format` | Query | Read the current upstream SVG specification; optional `format_name` loads provider-advertised diagram guidance. |
| `miro_search_canvas` | Query | `board_id`, `result_mode` (`overview`, `areas`, or `matches`); optional `patterns`, `target_id`, `cursor`. Find areas and item IDs before reading. |
| `miro_read_canvas` | Query | `board_id` and `canvas_scope` or `frame_id`/`item_ids`; returns editable SVG, stable item IDs, exact selection, URL and `snapshot_id`. |
| `miro_read_comments` | Query | Same selection; reads bounded complete pagination and returns a comment snapshot. |
| `miro_get_snapshot` | Query | `snapshot_id`; read a retained source for another agent or review. |
| `miro_get_operation` | Query | `operation_id`; inspect the durable outcome, attribution and snapshot references. |
| `miro_create_board` | Effect | `title`, `operation_key`; creates and binds a board when explicitly granted. Sharing follows Miro's workspace policy. |
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

Obtain the current canvas specification before authoring SVG. The payload is Miro's structured
editable canvas format, not an SVG image upload. Use its diagram format for Mermaid-based native
diagrams, or supported shapes, frames, text and sticky notes for explanations. Old `diagram_*`,
`layout_*`, and `context_*` remote tools are not used. Parameter schemas are negotiated against an
allowlisted set of remote tools; an unknown required parameter fails closed instead of guessing.
See [Miro's current canvas and comment tools](https://developers.miro.com/docs/miro-mcp-tools).

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
the existing outcome, including board creation after reaching the connection's board limit;
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
include the identified board in the resolution evidence; bind it explicitly in connection settings.
Operators may inspect and recover operations in their selected directory and its descendants.
An inherited connection does not permit recovery of operations owned by an ancestor or sibling.

## Workflow-owned monitoring and examples

`trigger:miro:board-changed` and `trigger:miro:comments-changed` take `connection_id`, `board_id`,
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
