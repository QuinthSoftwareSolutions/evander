import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import {
  defaultEmailMailboxFolders,
  emailMailboxDraftEntriesToConfig,
  emailMailboxEntriesFromConfig,
} from "../src/features/configuration/emailMailboxDraft.ts";

const configuration = readFileSync(
  new URL("../src/features/configuration/ConfigurationScreen.tsx", import.meta.url),
  "utf8",
);

test("email mailboxes use a structured configuration editor", () => {
  assert.match(configuration, /isEmailMailboxesConfigField\(field\)/);
  assert.match(configuration, /renderEmailMailboxesEditor/);
  assert.match(configuration, /Add mailbox/);
  assert.match(configuration, /Microsoft Graph \(Office 365\)/);
  assert.match(configuration, /Host access-token env var/);
  assert.match(configuration, /Map policy folder names to provider folder names/);
  assert.match(configuration, /Add folder/);
  assert.match(configuration, /emailMailboxDraftEntriesToConfig/);
});

test("email mailbox drafts preserve Graph aliases and produce the canonical config shape", () => {
  const entries = emailMailboxEntriesFromConfig([
    {
      mailbox_id: "m365-ops",
      provider: "graph",
      address: "ops@example.com",
      auth_env_var_names: { access_token: "GRAPH_M365_ACCESS_TOKEN" },
      folder_mapping: { inbox: "Inbox", drafts: "Drafts" },
      folder_allowlist_defaults: ["inbox"],
    },
  ]);

  assert.deepEqual(entries, [
    {
      mailboxId: "m365-ops",
      provider: "graph",
      accountIdentifier: "ops@example.com",
      accessTokenEnv: "GRAPH_M365_ACCESS_TOKEN",
      folders: [
        { folder: "inbox", providerFolder: "Inbox", allowedByDefault: true },
        { folder: "drafts", providerFolder: "Drafts", allowedByDefault: false },
      ],
    },
  ]);

  assert.deepEqual(emailMailboxDraftEntriesToConfig(entries), [
    {
      mailbox_id: "m365-ops",
      provider: "graph",
      account_identifier: "ops@example.com",
      auth_secret_refs: { access_token: "GRAPH_M365_ACCESS_TOKEN" },
      folder_mapping: { inbox: "Inbox", drafts: "Drafts" },
      folder_allowlist_defaults: ["inbox"],
    },
  ]);
});

test("mailbox drafts default Graph folders and reject incomplete secret references", () => {
  assert.deepEqual(defaultEmailMailboxFolders("graph"), [
    { folder: "inbox", providerFolder: "Inbox", allowedByDefault: true },
    { folder: "drafts", providerFolder: "Drafts", allowedByDefault: true },
  ]);

  assert.deepEqual(
    emailMailboxEntriesFromConfig([
      {
        mailbox_id: "m365-empty-folders",
        provider: "graph",
        address: "ops@example.com",
        auth_secret_refs: { access_token: "GRAPH_M365_ACCESS_TOKEN" },
        folder_mapping: {},
        folder_allowlist_defaults: [],
      },
    ])[0].folders,
    [],
  );

  assert.throws(
    () =>
      emailMailboxDraftEntriesToConfig([
        {
          mailboxId: "m365-ops",
          provider: "graph",
          accountIdentifier: "ops@example.com",
          accessTokenEnv: "",
          folders: [],
        },
      ]),
    /Mailbox m365-ops requires a host access-token environment variable\./,
  );
});
