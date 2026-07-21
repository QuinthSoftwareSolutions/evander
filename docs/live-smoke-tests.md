# Live Smoke Tests

This runbook verifies live `Discord + read-only email` behavior in two stages:

1. Host gateway only
2. Full VM end to end

The intent is to validate real credentials, policy enforcement, and host/guest wiring without
enabling send flows or touching non-test destinations.

## Scope

- Discord:
  - `discord_get_commands`
  - `discord_post_status`
- Email:
  - `email_list_unread`
  - `email_list_messages`
  - `email_get_message`
- Providers:
  - one dedicated Gmail test mailbox
  - one dedicated Microsoft Graph test mailbox
- Email send remains disabled.

## Prerequisites

- Host services are running:

  ```bash
  sudo systemctl status evander-firewall.service
  sudo systemctl status evander-gateway.service
  sudo systemctl status evander-openai-relay.service
  ```

- The effective `root` config uses `discord_backend: "bot_api"`.
- Host env contains live credentials:
  - `DISCORD_BOT_TOKEN`
  - `GMAIL_OPS_ACCESS_TOKEN` or equivalent Gmail test token env var
  - `GRAPH_TENANT_ID`, `GRAPH_EXEC_CLIENT_ID`, and `GRAPH_EXEC_CLIENT_SECRET` (preferred), or an
    equivalent static Graph test token env var for compatibility testing
- Dedicated test destinations already exist:
  - one Discord test channel
  - one Gmail test mailbox
  - one Graph test mailbox
  - optionally, one Teams test channel or chat for attachment smoke tests
- `email_send_enabled: false`
- For optional attachment smoke tests, `communication_file_send_enabled: false` unless the
  specific outbound-send approval path is under test.

## Suggested Test Fixture Config

Use dedicated mailbox IDs and keep allowlists narrow:

```yaml
discord_backend: "bot_api"
discord_bot_token_env: "DISCORD_BOT_TOKEN"
discord_operator_user_allowlist:
  - "YOUR_DISCORD_USER_ID"
email_mailbox_allowlist:
  - "ops-gmail-test"
  - "exec-graph-test"
email_folder_allowlist_by_mailbox:
  ops-gmail-test: ["inbox", "drafts"]
  exec-graph-test: ["inbox", "drafts"]
email_send_enabled: false
communication_file_receive_enabled: true
communication_file_send_enabled: false
communication_file_require_approval_for_send: true
communication_file_max_bytes: 1048576
communication_file_allowed_mime_types:
  - "text/plain"
  - "application/pdf"
  - "image/png"
  - "image/jpeg"
communication_file_allowed_extensions:
  - ".txt"
  - ".pdf"
  - ".png"
  - ".jpg"
  - ".jpeg"
communication_file_blocked_extensions:
  - ".exe"
  - ".js"
  - ".sh"
email_mailboxes:
  - mailbox_id: "ops-gmail-test"
    provider: "gmail"
    account: "me"
    auth_env_var_names:
      access_token: "GMAIL_OPS_ACCESS_TOKEN"
    folder_allowlist_defaults: ["inbox", "drafts"]
    folder_mapping:
      inbox: "INBOX"
      drafts: "DRAFT"
  - mailbox_id: "exec-graph-test"
    provider: "graph"
    address: "leader@example.com"
    auth_env_var_names:
      tenant_id: "GRAPH_TENANT_ID"
      client_id: "GRAPH_EXEC_CLIENT_ID"
      client_secret: "GRAPH_EXEC_CLIENT_SECRET"
    folder_allowlist_defaults: ["inbox", "drafts"]
    folder_mapping:
      inbox: "Inbox"
      drafts: "Drafts"
```

## Session Setup

Create a dedicated smoke-test session:

```bash
cd /path/to/evander
./.venv/bin/evander-cli create-session \
  smoke-live \
  --runtime-dir runtime \
  --vm-id vm-smoke-live \
  --discord-channel 123456789012345678
./.venv/bin/evander-cli add-tool-permission \
  --runtime-dir runtime \
  smoke-live \
  email_list_messages
```

Seed the dedicated Discord channel into the command store and note the returned cursor:

```bash
./.venv/bin/evander-cli seed-discord-channel \
  123456789012345678 \
  --runtime-dir runtime
```

The command prints a numeric seed cursor. Use that value as the initial `since_cursor` for
`discord_get_commands` so the placeholder seed entry is excluded from the smoke test.

## Stage A: Host Gateway Only

Start with one positive Discord round trip:

1. Post a real command into the dedicated Discord test channel from your allowlisted operator user.
2. Call `discord_get_commands` using the session ID and the seed cursor.
3. Confirm the returned command originates from the dedicated test channel.
4. Call `discord_post_status` to the same channel.
5. Confirm the status message appears in Discord.

Then verify email for both providers:

1. Call `email_list_unread` for the Gmail test mailbox and `inbox`.
2. Pick one returned message ID and call `email_get_message`.
3. Call `email_list_messages` with a small `limit`, then use its `next_cursor` for a second page
   when one is returned. Repeat with a bounded date filter.
4. Repeat for the Graph test mailbox and `inbox`.

Run at least one negative policy check:

1. Attempt `discord_post_status` to a non-allowlisted channel.
2. Attempt `email_list_unread` for a non-allowlisted mailbox or a blocked folder.

Inspect the audit log:

```bash
tail -n 40 runtime/audit.jsonl
```

Expected audit behavior:

- successful Discord and email calls are logged as `allowed`
- policy failures are logged as `denied`
- live provider/token failures are logged as `error` with `error_class = adapter_http_error`

## Stage B: Full VM End-to-End

Launch a fresh VM for the same session:

If you are validating a non-default libvirt base image, use
[libvirt-vm-template-operations.md](libvirt-vm-template-operations.md) for the alias registration,
overlay verification, and host-side proof checks that go with this launch.

```bash
cd /path/to/evander
sudo ./scripts/destroy_agent_vm.sh vm-smoke-live
sudo ./scripts/launch_agent_vm.sh vm-smoke-live smoke-live
./scripts/bootstrap_codex_vm.sh vm-smoke-live
# (Codex provider bootstrap; generic smoke checks still rely on provider-neutral agent run/log/state wiring.)
```

Find the VM IP and connect:

```bash
VM_IP="$(virsh domifaddr vm-smoke-live | awk '$3 == "ipv4" {sub("/.*", "", $4); print $4; exit}')"
test -n "${VM_IP}" || { echo "No IPv4 address found for vm-smoke-live" >&2; exit 1; }
ssh "agent@${VM_IP}"
```

Inside the VM, use the real guest-to-host path:

1. Call `discord_get_commands` with the same seed cursor.
2. Call `discord_post_status` to the dedicated test channel.
3. Call `email_list_unread` and `email_get_message` for the Gmail test mailbox.
4. Call `email_list_unread` and `email_get_message` for the Graph test mailbox.

Repeat one negative policy call from the VM path:

- blocked Discord destination or blocked mailbox/folder

Then verify the host audit log again and confirm entries include the active session and VM context.

## Optional Attachment Smoke Tests

Run these steps only in dedicated test channels/mailboxes. Do not use personal, production, or
shared business channels/mailboxes, and keep all fixtures intentionally small (for example a
`hello.txt` file under 1 KiB and one small PNG/PDF under the configured 1 MiB limit). Keep outbound
send disabled unless you are explicitly testing the approval path.

### Host gateway attachment receive

1. Apply the conservative communication-file policy from
   `config/communication-files/policy.json.example` to the smoke-test directory/session.
2. Upload `hello.txt` to the dedicated Discord test channel. Call
   `communication_list_attachments` with `provider="discord"`, the Discord `message_id`, and the
   allowlisted `channel_id`; then call `communication_download_attachment` for the returned
   `attachment_id`.
3. Upload `hello.txt` to the dedicated Teams test channel/chat, if Teams credentials and allowlists
   are configured. Call `communication_list_attachments` with `provider="teams"`, the Teams
   `message_id`, and the allowlisted `channel_target`; then download one returned attachment.
4. Send `hello.txt` to the Gmail test mailbox. Call `communication_list_attachments` with
   `provider="email"`, the Gmail `message_id`, and `mailbox_id="ops-gmail-test"`; then download
   one returned attachment.
5. Repeat the email attachment receive check for the Graph test mailbox with
   `mailbox_id="exec-graph-test"`.
6. For one staged artifact, call `communication_copy_artifact_to_workspace` and confirm the
   returned destination path is under `/home/agent/communication-artifacts/`.

### Optional outbound-send approval path

Only run this subsection when the test owner has approved sending visible test artifacts back into
the same dedicated test destinations.

1. Keep `communication_file_require_approval_for_send: true` and temporarily set
   `communication_file_send_enabled: true` for the smoke-test session/directory.
2. If using an agent-created local fixture, call `communication_stage_workspace_file` for a regular
   file under `/home/agent` and use the returned `artifact.artifact_id`.
3. Submit and approve an exact payload for one provider-specific send tool. The payload must match
   the destination, message/body, thread fields when present, and `artifact_ids`.
4. For Discord, call `discord_post_file` with the approved `request_id` and the staged
   `artifact_ids`, targeting only the dedicated Discord test channel.
5. For Teams, call `teams_post_file` with the approved `request_id`, targeting only the dedicated
   Teams test channel/chat. Image artifacts should render inline; non-image artifacts should appear
   as signed control-plane download links when the public base URL and link secret are configured.
6. For Gmail/Graph, prefer `email_create_draft_with_attachments` first. Exercise
   `email_send_with_attachments` only if `email_send_enabled: true` is intentionally set
   and the exact file-send approval is passed as `request_id`. If MCP tool approval is also
   required, pass that separate approval as `approval_request_id`. The older
   `email_send_approved_with_attachments` compatibility tool can still be used for legacy smokes.
7. Immediately return `communication_file_send_enabled` to `false` after the outbound test.

### Attachment audit checks

Inspect the host audit log and confirm attachment operations are scoped and audited:

```bash
tail -n 100 runtime/audit.jsonl | jq 'select(.tool_name | test("communication_|discord_post_file|teams_post_file|email_.*attachments"))'
```

Expected optional attachment behavior:

- inbound list/download/copy calls for Discord, Teams, Gmail, and Graph test destinations are
  logged as `allowed`
- attempts against non-test channels/mailboxes are logged as `denied`
- oversized or blocked MIME/extension fixtures are logged as `denied`
- outbound sends fail while send policy is disabled
- any intentionally enabled outbound send includes approval request/decision fields and stays in
  the dedicated test destination

## Stage C: Managed Git Migration Acceptance

Run this stage after `git-repos migrate --apply` and before finalizing its migration manifest.
Use a disposable upstream branch and PR project.

1. Confirm `evander-cli git-repos list` reports the migrated repository and `show` contains the
   expected directory owner, canonical URL, default branch, and bindings without credentials or
   temporary worktree paths.
2. Clone that `managed_repo_id` through one libvirt VM and one `container_worker_vm` session.
3. In each isolated checkout, create a distinct commit, attach a PR note to that exact commit, and
   push with `git push-agent`. Confirm a different session key cannot update the ref.
4. Confirm `trigger:git:push` enqueues the managed ID, exact old/new SHAs, session, notes ref,
   default branch, and derived review branch. The post-receive path must not publish upstream.
5. Let the workflow promote the pinned SHA. Push a newer commit while one run is in flight and
   confirm it either publishes the already pinned SHA or returns `source_ref_moved`; it must not
   substitute the newer tip.
6. Confirm `query:git:pr-note` returns only the note attached to the pinned commit.
7. Publish a disposable destination branch and create/reuse its PR. Verify the upstream branch is
   exactly the approved SHA and a stale lease cannot regress it.
8. Run one Team feature/release consolidation from explicit source SHAs, then run its release
   verification in `container_worker_vm`. Confirm host-authored merge identity is recorded and no
   persistent host checkout appears in Team state.
9. Run `evander-cli git-repos maintenance --managed-repo-id <id>` and confirm no completed
   operation worktree or operation ref remains. Check logs, process arguments, repository config,
   registry, and manifests for credentials.
10. Destroy both sessions. Confirm their bindings and SSH keys are gone while the managed
    repository and unpublished refs remain.

Only after these checks and operator acceptance should you run:

```bash
evander-cli git-repos migrate --finalize <migration-id>
```

Confirm finalization deletes only the Evander-owned rollback artifacts named in that manifest and
does not touch any operator checkout.

## Cleanup

Always remove the live fixture, including after a failed smoke:

```bash
evander-cli revoke-session smoke-live --runtime-dir runtime
sudo ./scripts/destroy_agent_vm.sh vm-smoke-live
evander-cli delete-session smoke-live --runtime-dir runtime
```

Remove the dedicated Discord messages and email fixtures according to the test accounts' retention
policy. Do not reuse their IDs as evidence for a later run.

## Pass Criteria

The smoke test passes only if all of these are true:

- host gateway can talk to live Discord and both live email providers
- each email provider returns the dedicated unread fixture, and `email_get_message` returns the
  expected fixture message ID, sender, subject, and body metadata
- VM can complete the same live read-only flows through the host gateway
- only allowlisted Discord/email operations succeed
- blocked Discord/email operations are denied and audited
- provider auth/timeout failures are audited as `adapter_http_error`
- no email send flow is enabled or exercised
- all visible artifacts stay within dedicated test channels and test mailboxes
- optional attachment tests use dedicated Discord, Teams, Gmail, and Graph destinations only, and
  outbound attachment sends occur only with matching approval when send policy is intentionally enabled
- when migration acceptance is in scope, both execution backends use the same managed repository,
  publication matches the pinned SHA, operation worktrees clean up, and finalization remains explicit

## Failure Triage

- `discord_get_commands` returns nothing:
  - verify the channel was seeded with `seed-discord-channel`
  - verify the message author is in `discord_operator_user_allowlist` or the message channel matches the session allowlist
- Discord HTTP failures:
  - verify `DISCORD_BOT_TOKEN`
  - inspect `runtime/audit.jsonl`
- Gmail/Graph auth failures:
  - verify the configured token env vars exist on the host
  - confirm mailbox config points to the correct env var names
  - inspect `runtime/audit.jsonl` for `adapter_http_error`
- VM path failures:
  - verify firewall, gateway service, and guest network reachability first
