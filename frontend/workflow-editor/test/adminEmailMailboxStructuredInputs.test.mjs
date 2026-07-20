import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import {
  defaultEmailMailboxFolders,
  emailMailboxDraftEntriesToConfig,
  emailMailboxDraftValue,
  emailMailboxEntriesFromConfig,
  emailMailboxFoldersAfterProviderChange,
  parseEmailMailboxDraftValue,
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

test("email mailbox draft values round-trip without losing camel-case fields", () => {
  const entries = [
    {
      mailboxId: "gmail-support",
      provider: "gmail",
      accountIdentifier: "support@example.com",
      accessTokenEnv: "GMAIL_SUPPORT_ACCESS_TOKEN",
      folders: [
        { folder: "inbox", providerFolder: "INBOX", allowedByDefault: false },
        { folder: "drafts", providerFolder: "DRAFT", allowedByDefault: true },
      ],
    },
  ];

  assert.deepEqual(parseEmailMailboxDraftValue(emailMailboxDraftValue(entries)), {
    entries,
    error: null,
  });
});

test("email mailbox config keeps an explicitly empty default folder allowlist", () => {
  assert.deepEqual(
    emailMailboxDraftEntriesToConfig([
      {
        mailboxId: "gmail-restricted",
        provider: "gmail",
        accountIdentifier: "me",
        accessTokenEnv: "GMAIL_RESTRICTED_ACCESS_TOKEN",
        folders: [
          { folder: "inbox", providerFolder: "INBOX", allowedByDefault: false },
          { folder: "drafts", providerFolder: "DRAFT", allowedByDefault: false },
        ],
      },
    ]),
    [
      {
        mailbox_id: "gmail-restricted",
        provider: "gmail",
        account_identifier: "me",
        auth_secret_refs: { access_token: "GMAIL_RESTRICTED_ACCESS_TOKEN" },
        folder_mapping: { inbox: "INBOX", drafts: "DRAFT" },
        folder_allowlist_defaults: [],
      },
    ],
  );
});

test("provider changes replace untouched defaults and preserve customized folders", () => {
  assert.deepEqual(
    emailMailboxFoldersAfterProviderChange(
      "graph",
      "gmail",
      defaultEmailMailboxFolders("graph"),
    ),
    defaultEmailMailboxFolders("gmail"),
  );

  const restrictedDefaults = defaultEmailMailboxFolders("graph").map((folder) => ({
    ...folder,
    allowedByDefault: false,
  }));
  assert.deepEqual(
    emailMailboxFoldersAfterProviderChange("graph", "gmail", restrictedDefaults),
    [
      { folder: "inbox", providerFolder: "INBOX", allowedByDefault: false },
      { folder: "drafts", providerFolder: "DRAFT", allowedByDefault: false },
    ],
  );

  const customized = [
    { folder: "inbox", providerFolder: "Custom Inbox", allowedByDefault: true },
    { folder: "drafts", providerFolder: "Drafts", allowedByDefault: true },
  ];
  assert.strictEqual(
    emailMailboxFoldersAfterProviderChange("graph", "gmail", customized),
    customized,
  );
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
