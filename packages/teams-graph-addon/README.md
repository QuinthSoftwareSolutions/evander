# Microsoft Teams and Graph add-on

This optional distribution owns Teams messaging, Microsoft device-code authentication,
Graph mail, Graph notification subscriptions, and Teams meeting/voice adapters. Core Evander
does not install it. It uses the existing host-owned secrets, policy checks, directory scopes,
and workflow runtime.

## Install or upgrade

This source release requires Evander 0.102 or later and contributes the **Respond to Teams direct
messages** starter. Once this add-on is loaded, open **Workflow starters** in Admin Web.
See the [starter guide](../../docs/workflow-starters.md#respond-to-teams-direct-messages).

From an Evander source checkout, install both projects into the same host virtualenv:

```bash
uv pip install --python .venv/bin/python -e . -e ./packages/teams-graph-addon
```

To include the integration in the add-on inventory, append
`evander_teams_graph_addon` to `addon_loader.modules`, or enable entry-point discovery with
`addon_loader.enabled_entry_points: true`. If `addon_loader.enabled_addon_ids` is an explicit
list, also append `teams-graph`; preserve the other entries. Configure this through the existing
operator configuration interfaces. The legacy default with no explicit list continues to permit
Teams/Graph when the package is installed.

After changing enablement, restart every service listed by Admin Web, including
Teams ingest, notifications, and meeting ingest. A disabled Teams worker exits
successfully at startup before opening a listener or performing provider work.

Reinstall the service definitions after installing the package:

```bash
./scripts/install_systemd_units.sh --preflight
sudo ./scripts/install_systemd_units.sh
```

The installer resolves the package's own service templates and includes the three Teams
services only when the package is available and enabled. It does not start, stop, remove, or
delete existing services. Preflight reads existing host configuration without initializing
runtime state. Restart the services listed by Admin during your normal maintenance window;
enabling or disabling this integration includes the gateway because it registers Teams tools
at startup.
The existing `evander-cli teams-ingest`, `teams-auth`, and `teams-meeting` commands remain valid.
The package supplies `evander-teams-ingest`, `evander-teams-notifications`, and
`evander-teams-meeting-ingest` console commands.

Ansible installs this project when a Teams service is selected. The existing full-stack defaults
therefore preserve their behavior. Set `evander_teams_graph_addon_enabled: true` for Graph mail
without Teams workers. To omit the integration, disable its three service toggles and set
`evander_teams_graph_addon_enabled: false`.

## Compatibility and data

No operator migration is required. Keep existing directory provider settings, environment secret
references, managed credential references, encrypted identity stores, Teams message databases,
subscription state, meeting bindings, and notification URLs. Existing workflow node and tool IDs
are unchanged. Legacy Python import paths delegate to this package and retain class identity.

Core retains the persisted data models, local stores, policy vocabulary, and workflow schemas
needed to read saved sessions and workflows. Local event consumers remain in core. Network
clients, auth implementation, workers, Teams admin handlers, MCP handlers, and deployment assets
live in this project. This boundary deliberately avoids changing existing workflow definitions
or introducing a separate execution engine.

Without the package, core runtime, CLI help, and unrelated providers remain usable. Configured
Teams/Graph calls fail with installation guidance; Teams MCP handlers are absent and existing
auth URLs return an authenticated `503`. Re-enable and restart after reinstalling the package.
Before uninstalling, stop the Teams services and remove this add-on from explicit loader lists.
The installer never deletes credentials or runtime records.

Outbound message receipts use an additive table in the Teams SQLite database, or an indexed
`<inbox filename>.outbound/` sibling store for the legacy JSON repository. Back up these receipts
with runtime data. Inbox refreshes never grant or replace ownership. Messages sent before receipt
recording was introduced remain readable but cannot be edited through this tool.

## Edit a message sent by an agent session

`teams_update_message(session_id, channel_target, message_id, message, thread_id=None,
expected_revision=None)` replaces text in a message previously posted by that exact session.
MCP uses the authenticated connection's session binding. The session must still be active, have
the explicit `teams_update_message` tool grant, and retain the target's `write` capability for a
channel root or chat, or `reply` capability for a channel reply. No new grants are added by an
upgrade. Two sessions sharing the same Microsoft account cannot edit each other's messages.

Use an existing `team-id/channel-id`, `chat:<chat-id>`, or a configured target reference resolving
to one. `user:` aliases are rejected; editing never resolves or creates a chat. The receipt supplies
the actual parent thread. An optional `thread_id` must agree with it; chats cannot specify a thread.
Caller text uses the same Markdown-like renderer as posting, including Unicode, headings, lists,
and inline formatting. Raw HTML is escaped. Replacement mentions, attachments, or card collections
are not accepted.

Successful `post_status` and `post_file` results include `message_revision: 1`. Retain the returned
message ID, target and revision, then call, for example:

```json
{
  "channel_target": "team-a/channel-a",
  "message_id": "message-a",
  "message": "**Writing:** 2 of 5 sections complete.",
  "expected_revision": 1
}
```

An edit returns `success`, `status`, `session_id`, `channel_target`, `message_id`, `thread_id`,
`message_revision`, and `web_url` (nullable). `updated` increments the revision; an identical retry
returns `unchanged` without incrementing it, even when it carries the previous revision. A different
replacement with a stale revision fails. The stub backend creates synthetic IDs and durable receipts;
its changed edits return `stubbed`, and identical retries return `unchanged`, without Graph traffic.

The host captures a unique editable region when posting, retaining the surrounding body markup and
the acknowledged mention/attachment metadata. Each edit reads the current Graph message, verifies
its author, parent, body and protected references, PATCHes only the composed body, and reads it back
before reporting success. Existing mention IDs, file links, attachments, inline images and cards
remain in place. An ambiguous original layout still posts successfully but its receipt is marked
uneditable with a reason. External text, layout or protected-content changes require operator
inspection; the tool refuses to flatten or overwrite them.

Each message has a cross-process lock and durable pending intent. Timeouts or failed readback retain
the intent. Retry the original replacement text to reconcile it: an already-applied write commits
one revision without another PATCH; an unapplied intent can retry the same body. A new replacement
is blocked while an earlier write remains unresolved. The existing bounded Graph retry/backoff
settings apply. Deleted messages, permission failures and throttling never fall back to a new post.
Local revisions coordinate Evander callers; they do not provide a Microsoft-side compare-and-swap
against a person editing simultaneously between Graph reads and PATCH.

### Workflow effect

The enabled add-on registers `effect:teams:update-message` with ordinary bindable editor fields:

| Input | Required | Meaning |
| --- | --- | --- |
| `session_id` | yes | Real, active session that owns the outbound receipt; no anonymous workflow sender |
| `channel_target` | yes | Existing channel/chat address or configured target reference |
| `message_id` | yes | ID returned by the send |
| `message` | yes | Replacement text |
| `thread_id` | no | Must match the recorded parent |
| `expected_revision` | no | Positive integer guarding a conflicting replacement |

The effect calls the same host command directly, without MCP transport or inference. The owning
session must belong to the workflow directory or a descendant. It requires the same tool and target
grants, returns the same output fields, and audits changes, no-ops, denials and provider failures with
workflow run/node context. Preserve the post result and latest revision in workflow state when
building a progress-message loop. The add-on contributes the effect only when enabled; discovery
does not access credentials or the network.

### Microsoft permissions and errors

Use a delegated work/school identity that sent the original message. Microsoft's
[update API](https://learn.microsoft.com/en-us/graph/api/chatmessage-update?view=graph-rest-1.0)
lists delegated `ChannelMessage.ReadWrite` for channels and `Chat.ReadWrite` for chats; application
permissions support policy-violation updates rather than body editing. GET/readback also needs
appropriate channel/chat read access. The permissions reference lists the narrower delegated
[`ChannelMessage.Edit`](https://learn.microsoft.com/en-us/graph/permissions-reference#channelmessageedit),
but the update endpoint's permission table does not list it. Its sufficiency for channel edits must
be confirmed in the target tenant before choosing it instead of the documented permission.

Installing this feature never changes OAuth scopes or consent. A `403` points operators to delegated
edit consent, the signed-in identity, channel/chat membership, and tenant message-editing policy.
After an approved scope change, complete a fresh consent/sign-in flow. Live confirmation of the
narrow channel permission and tenant editing policy is separate from mocked local validation.

## Extension contract

Registration contributes Teams settings, a host-only credential resolver, configuration readiness,
and three worker factories through the Stage 1 contract. Registration and readiness make no
network requests, decrypt no credentials, and start no workers. Readiness confirms local
configuration only: it does not prove Microsoft consent, grant validity, or connectivity. Existing
auth commands and mocked provider tests cover those boundaries separately.

Worker factories produce cooperative, host-supervised workers. The established systemd commands
continue to use the existing worker loops, file locks, retries, and durable state. Workflow starters
and guided setup are separate work; any existing custom workflow can keep using these providers.

## Tests

From the repository root with core's test dependencies installed:

```bash
PYTHONPATH=src:packages/teams-graph-addon .venv/bin/pytest -q packages/teams-graph-addon/tests
PYTHONPATH=src .venv/bin/pytest -q tests/test_optional_integrations.py
```

Provider tests live here. Core tests use local data contracts and synthetic adapters, and the
absence tests explicitly hide the add-on. These are local tests, not evidence of a live Microsoft
tenant sign-in, callback delivery, or meeting connection.
