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
  - `email_get_message`
- Providers:
  - one dedicated Gmail test mailbox
  - one dedicated Microsoft Graph test mailbox
- Email send remains disabled.

## Prerequisites

- Host services are running:

  ```bash
  sudo systemctl status agent-sandbox-firewall.service
  sudo systemctl status agent-sandbox-gateway.service
  sudo systemctl status agent-sandbox-openai-relay.service
  ```

- The effective `root` config uses `discord_backend: "bot_api"`.
- Host env contains live credentials:
  - `DISCORD_BOT_TOKEN`
  - `GMAIL_OPS_ACCESS_TOKEN` or equivalent Gmail test token env var
  - `GRAPH_EXEC_ACCESS_TOKEN` or equivalent Graph test token env var
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
      access_token: "GRAPH_EXEC_ACCESS_TOKEN"
    folder_allowlist_defaults: ["inbox", "drafts"]
    folder_mapping:
      inbox: "Inbox"
      drafts: "Drafts"
```

## Session Setup

Create a dedicated smoke-test session:

```bash
cd /path/to/agent-sandbox
./.venv/bin/agent-sandbox-cli create-session \
  smoke-live \
  --runtime-dir runtime \
  --vm-id vm-smoke-live \
  --discord-channel 123456789012345678
```

Seed the dedicated Discord channel into the command store and note the returned cursor:

```bash
./.venv/bin/agent-sandbox-cli seed-discord-channel \
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
3. Repeat for the Graph test mailbox and `inbox`.

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
cd /path/to/agent-sandbox
sudo ./scripts/destroy_agent_vm.sh vm-smoke-live
sudo ./scripts/launch_agent_vm.sh vm-smoke-live smoke-live
./scripts/bootstrap_codex_vm.sh vm-smoke-live
# (Codex provider bootstrap; generic smoke checks still rely on provider-neutral agent run/log/state wiring.)
```

Find the VM IP and connect:

```bash
virsh domifaddr vm-smoke-live
ssh agent@<vm-ip>
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

## Pass Criteria

The smoke test passes only if all of these are true:

- host gateway can talk to live Discord and both live email providers
- VM can complete the same live read-only flows through the host gateway
- only allowlisted Discord/email operations succeed
- blocked Discord/email operations are denied and audited
- provider auth/timeout failures are audited as `adapter_http_error`
- no email send flow is enabled or exercised
- all visible artifacts stay within dedicated test channels and test mailboxes
- optional attachment tests use dedicated Discord, Teams, Gmail, and Graph destinations only, and
  outbound attachment sends occur only with matching approval when send policy is intentionally enabled

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
