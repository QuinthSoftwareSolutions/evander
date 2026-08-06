import assert from "node:assert/strict";
import test from "node:test";

import {
  LEGACY_EMAIL_DRAFT_TOOL_VARIANTS,
  LEGACY_EMAIL_SEND_TOOL_VARIANTS,
  legacyEmailMailboxAccess,
} from "../src/features/communications/legacyCommunicationPolicy.ts";

test("legacy mailbox conversion recognizes every current write tool variant", () => {
  for (const tool of LEGACY_EMAIL_DRAFT_TOOL_VARIANTS) {
    assert.equal(
      legacyEmailMailboxAccess({
        allowedTools: [tool],
        folders: ["inbox", "drafts"],
        emailSendEnabled: true,
      }),
      "draft",
    );
  }
  for (const tool of LEGACY_EMAIL_SEND_TOOL_VARIANTS) {
    assert.equal(
      legacyEmailMailboxAccess({
        allowedTools: [tool],
        folders: ["inbox", "drafts"],
        emailSendEnabled: true,
      }),
      "send",
    );
  }
});

test("legacy mailbox conversion does not broaden folder or send access", () => {
  assert.equal(
    legacyEmailMailboxAccess({
      allowedTools: ["email_send_approved_with_attachments"],
      folders: ["inbox"],
      emailSendEnabled: true,
    }),
    "read",
  );
  assert.equal(
    legacyEmailMailboxAccess({
      allowedTools: ["email_send"],
      folders: ["inbox", "drafts"],
      emailSendEnabled: false,
    }),
    "read",
  );
});
