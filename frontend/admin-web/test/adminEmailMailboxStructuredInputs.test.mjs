import assert from "node:assert/strict";
import { test } from "node:test";

import {
  defaultEmailMailboxFolders,
  emailMailboxDraftEntriesToConfig,
  emailMailboxDraftValue,
  emailMailboxEntriesFromConfig,
  emailMailboxFoldersAfterProviderChange,
  parseEmailMailboxDraftValue,
} from "../src/features/configuration/emailMailboxDraft.ts";
import {
  emailMailboxAuthProfileRef,
  emailMailboxFolderPolicyDraftEntriesToConfig,
  emailMailboxFolderPolicyDraftValue,
  emailMailboxFolderPolicyEntriesFromConfig,
  emailMailboxIdFromAuthProfileRef,
  parseEmailMailboxFolderPolicyDraftValue,
} from "../src/features/configuration/emailMailboxPolicyDraft.ts";

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
      authMode: "access_token",
      accessTokenEnv: "GRAPH_M365_ACCESS_TOKEN",
      tenantIdEnv: "",
      clientIdEnv: "",
      clientSecretEnv: "",
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
      authMode: "access_token",
      accessTokenEnv: "GMAIL_SUPPORT_ACCESS_TOKEN",
      tenantIdEnv: "",
      clientIdEnv: "",
      clientSecretEnv: "",
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

test("Graph mailbox client credentials round-trip to host environment references", () => {
  const entries = emailMailboxEntriesFromConfig([
    {
      mailbox_id: "m365-accounting",
      provider: "graph",
      account_identifier: "accounting@example.com",
      auth_secret_refs: {
        tenant_id: "GRAPH_M365_TENANT_ID",
        client_id: "GRAPH_M365_CLIENT_ID",
        client_secret: "GRAPH_M365_CLIENT_SECRET",
      },
      folder_mapping: { inbox: "Inbox" },
      folder_allowlist_defaults: ["inbox"],
    },
  ]);

  assert.equal(entries[0].authMode, "client_credentials");
  assert.equal(entries[0].tenantIdEnv, "GRAPH_M365_TENANT_ID");
  assert.equal(entries[0].clientIdEnv, "GRAPH_M365_CLIENT_ID");
  assert.equal(entries[0].clientSecretEnv, "GRAPH_M365_CLIENT_SECRET");
  assert.deepEqual(emailMailboxDraftEntriesToConfig(entries)[0].auth_secret_refs, {
    tenant_id: "GRAPH_M365_TENANT_ID",
    client_id: "GRAPH_M365_CLIENT_ID",
    client_secret: "GRAPH_M365_CLIENT_SECRET",
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

test("email target mailbox references map between selector ids and canonical auth profiles", () => {
  assert.equal(emailMailboxIdFromAuthProfileRef("email.accounting"), "accounting");
  assert.equal(emailMailboxIdFromAuthProfileRef(" accounting "), "accounting");
  assert.equal(emailMailboxAuthProfileRef(" accounting "), "email.accounting");
  assert.equal(emailMailboxAuthProfileRef(""), "");
});

test("mailbox folder policy drafts round-trip and preserve explicit empty folder lists", () => {
  const entries = emailMailboxFolderPolicyEntriesFromConfig({
    accounting: ["inbox", "drafts", "inbox"],
    restricted: [],
  });

  assert.deepEqual(entries, [
    { mailboxId: "accounting", folders: ["inbox", "drafts"] },
    { mailboxId: "restricted", folders: [] },
  ]);
  assert.deepEqual(
    parseEmailMailboxFolderPolicyDraftValue(emailMailboxFolderPolicyDraftValue(entries)),
    { entries, error: null },
  );
  assert.deepEqual(emailMailboxFolderPolicyDraftEntriesToConfig(entries), {
    accounting: ["inbox", "drafts"],
    restricted: [],
  });
});

test("mailbox folder policy drafts reject blank and duplicate mailbox rows", () => {
  assert.throws(
    () => emailMailboxFolderPolicyDraftEntriesToConfig([{ mailboxId: "", folders: [] }]),
    /requires a mailbox/,
  );
  assert.throws(
    () =>
      emailMailboxFolderPolicyDraftEntriesToConfig([
        { mailboxId: "accounting", folders: ["inbox"] },
        { mailboxId: "accounting", folders: [] },
      ]),
    /Duplicate mailbox folder policy: accounting/,
  );
});
