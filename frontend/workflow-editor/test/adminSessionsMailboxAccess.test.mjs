import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const sessionsScreen = readFileSync(
  new URL("../src/features/sessions/SessionsScreen.tsx", import.meta.url),
  "utf8",
);
const legacyCommunicationPolicy = readFileSync(
  new URL("../src/features/communications/legacyCommunicationPolicy.ts", import.meta.url),
  "utf8",
);

function sourceBetween(startMarker, endMarker) {
  const start = sessionsScreen.indexOf(startMarker);
  assert.notEqual(start, -1, `start marker not found: ${startMarker}`);
  const end = sessionsScreen.indexOf(endMarker, start + startMarker.length);
  assert.notEqual(end, -1, `end marker not found: ${endMarker}`);
  return sessionsScreen.slice(start, end);
}

test("sessions empty mailbox state opens configuration for the session directory", () => {
  assert.match(
    sessionsScreen,
    /const \{ privilegedUnlocked, selectedDirectoryPath, setSelectedDirectoryPath \} = useAdminUi\(\);/,
  );

  const handler = sourceBetween(
    "function openMailboxConfiguration() {",
    "function convertLegacyCommunicationPolicy() {",
  );
  assert.match(handler, /modelDirty \|\| toolsDirty \|\| policyDirty/);
  assert.match(handler, /setSelectedDirectoryPath\(selectedSession\.directory_path\);/);
  assert.match(handler, /window\.location\.hash = "configuration";/);

  const mailboxEditor = sourceBetween(
    "<span>Email mailbox access</span>",
    "<span>Web search</span>",
  );
  assert.match(
    mailboxEditor,
    /No mailbox accounts are available under this session's directory policy\. Configure the mailbox allowlist and folder policy first\./,
  );
  assert.match(mailboxEditor, />\s*Configure mailbox access\s*<\/button>/);
  assert.match(mailboxEditor, /onClick=\{openMailboxConfiguration\}/);
});

test("legacy session conversion preserves every Teams grant kind", () => {
  const conversion = sourceBetween(
    "function convertLegacyCommunicationPolicy() {",
    "function updateApprovalNotificationTargets(",
  );

  for (const field of [
    "discord_channel_allowlist",
    "teams_channel_allowlist",
    "teams_chat_allowlist",
    "teams_user_allowlist",
  ]) {
    assert.match(conversion, new RegExp(`selectedSession\\.${field}`));
  }
  assert.match(conversion, /allowedTools: toolsDraft/);
  assert.doesNotMatch(conversion, /selectedSession\.allowed_tools/);
  for (const tool of [
    "email_create_draft",
    "email_create_draft_with_attachments",
    "email_send",
    "email_send_with_attachments",
    "email_send_approved",
    "email_send_approved_with_attachments",
  ]) {
    assert.match(legacyCommunicationPolicy, new RegExp(`"${tool}"`));
  }
});
