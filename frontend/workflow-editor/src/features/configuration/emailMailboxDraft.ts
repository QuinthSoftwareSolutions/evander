export const EMAIL_MAILBOX_PROVIDERS = ["json_store", "gmail", "graph"] as const;

export type EmailMailboxProvider = (typeof EMAIL_MAILBOX_PROVIDERS)[number];

export type EmailMailboxFolderDraft = {
  folder: string;
  providerFolder: string;
  allowedByDefault: boolean;
};

export type EmailMailboxDraftEntry = {
  mailboxId: string;
  provider: EmailMailboxProvider;
  accountIdentifier: string;
  accessTokenEnv: string;
  folders: EmailMailboxFolderDraft[];
};

export type EmailMailboxDraftParseResult = {
  entries: EmailMailboxDraftEntry[];
  error: string | null;
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function stringArray(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.map((item) => String(item ?? "").trim()).filter(Boolean);
}

function stringRecord(value: unknown): Record<string, string> {
  if (!isRecord(value)) return {};
  return Object.fromEntries(
    Object.entries(value).map(([key, entry]) => [key, String(entry ?? "")]),
  );
}

function accountIdentifier(record: Record<string, unknown>): string {
  for (const key of ["account_identifier", "account", "address"]) {
    const value = record[key];
    if (typeof value === "string") return value;
  }
  return "";
}

export function defaultEmailMailboxFolders(provider: EmailMailboxProvider): EmailMailboxFolderDraft[] {
  if (provider === "gmail") {
    return [
      { folder: "inbox", providerFolder: "INBOX", allowedByDefault: true },
      { folder: "drafts", providerFolder: "DRAFT", allowedByDefault: true },
    ];
  }
  if (provider === "graph") {
    return [
      { folder: "inbox", providerFolder: "Inbox", allowedByDefault: true },
      { folder: "drafts", providerFolder: "Drafts", allowedByDefault: true },
    ];
  }
  return [
    { folder: "inbox", providerFolder: "inbox", allowedByDefault: true },
    { folder: "drafts", providerFolder: "drafts", allowedByDefault: true },
  ];
}

function folderDraftsFromRecord(record: Record<string, unknown>, provider: EmailMailboxProvider) {
  const mappings = stringRecord(record.folder_mapping);
  const allowedFolders = stringArray(record.folder_allowlist_defaults);
  const folderNames = [...new Set([...Object.keys(mappings), ...allowedFolders])];
  const hasExplicitFolderConfig = Object.prototype.hasOwnProperty.call(record, "folder_mapping")
    || Object.prototype.hasOwnProperty.call(record, "folder_allowlist_defaults");
  if (!folderNames.length) {
    return hasExplicitFolderConfig ? [] : defaultEmailMailboxFolders(provider);
  }
  const allowed = new Set(allowedFolders);
  return folderNames.map((folder) => ({
    folder,
    providerFolder: mappings[folder] ?? folder,
    allowedByDefault: allowed.has(folder),
  }));
}

function folderDraftsFromValue(
  record: Record<string, unknown>,
  provider: EmailMailboxProvider,
): EmailMailboxFolderDraft[] {
  if (!Array.isArray(record.folders)) return folderDraftsFromRecord(record, provider);
  return record.folders.map((item) => {
    const folder = isRecord(item) ? item : {};
    return {
      folder: String(folder.folder ?? ""),
      providerFolder: String(folder.providerFolder ?? ""),
      allowedByDefault: folder.allowedByDefault === true,
    };
  });
}

function folderMappingsEqual(
  left: EmailMailboxFolderDraft[],
  right: EmailMailboxFolderDraft[],
): boolean {
  if (left.length !== right.length) return false;
  const remaining = [...right];
  for (const folder of left) {
    const matchingIndex = remaining.findIndex((candidate) =>
      candidate.folder === folder.folder
      && candidate.providerFolder === folder.providerFolder,
    );
    if (matchingIndex < 0) return false;
    remaining.splice(matchingIndex, 1);
  }
  return true;
}

export function emailMailboxFoldersAfterProviderChange(
  previousProvider: EmailMailboxProvider,
  provider: EmailMailboxProvider,
  folders: EmailMailboxFolderDraft[],
): EmailMailboxFolderDraft[] {
  if (provider === previousProvider) return folders;
  if (!folders.length) return defaultEmailMailboxFolders(provider);
  if (!folderMappingsEqual(folders, defaultEmailMailboxFolders(previousProvider))) return folders;
  const allowedByFolder = new Map(
    folders.map((folder) => [folder.folder, folder.allowedByDefault]),
  );
  return defaultEmailMailboxFolders(provider).map((folder) => ({
    ...folder,
    allowedByDefault: allowedByFolder.get(folder.folder) ?? folder.allowedByDefault,
  }));
}

export function emailMailboxEntriesFromConfig(value: unknown): EmailMailboxDraftEntry[] {
  if (!Array.isArray(value)) return [];
  return value.map((item) => {
    const record = isRecord(item) ? item : {};
    const provider = EMAIL_MAILBOX_PROVIDERS.includes(record.provider as EmailMailboxProvider)
      ? (record.provider as EmailMailboxProvider)
      : "json_store";
    const authRefs = isRecord(record.auth_secret_refs)
      ? record.auth_secret_refs
      : isRecord(record.auth_env_var_names)
        ? record.auth_env_var_names
        : {};
    return {
      mailboxId: String(record.mailboxId ?? record.mailbox_id ?? ""),
      provider,
      accountIdentifier: typeof record.accountIdentifier === "string"
        ? record.accountIdentifier
        : accountIdentifier(record) || (provider === "json_store" ? "default" : ""),
      accessTokenEnv: String(record.accessTokenEnv ?? authRefs.access_token ?? ""),
      folders: folderDraftsFromValue(record, provider),
    };
  });
}

export function parseEmailMailboxDraftValue(
  value: string | boolean,
): EmailMailboxDraftParseResult {
  if (typeof value === "boolean") return { entries: [], error: null };
  const text = value.trim();
  if (!text) return { entries: [], error: null };
  try {
    const parsed = JSON.parse(text);
    if (!Array.isArray(parsed)) {
      return { entries: [], error: "Email mailboxes must be a list." };
    }
    return { entries: emailMailboxEntriesFromConfig(parsed), error: null };
  } catch (reason) {
    return {
      entries: [],
      error: reason instanceof Error ? reason.message : String(reason),
    };
  }
}

export function emailMailboxDraftValue(entries: EmailMailboxDraftEntry[]): string {
  return JSON.stringify(entries, null, 2);
}

export function emptyEmailMailboxDraftEntry(): EmailMailboxDraftEntry {
  return {
    mailboxId: "",
    provider: "graph",
    accountIdentifier: "",
    accessTokenEnv: "",
    folders: defaultEmailMailboxFolders("graph"),
  };
}

export function emailMailboxDraftEntriesToConfig(entries: EmailMailboxDraftEntry[]): unknown[] {
  const mailboxIds = new Set<string>();
  return entries.map((entry, index) => {
    const mailboxId = entry.mailboxId.trim();
    const label = mailboxId || `Mailbox ${index + 1}`;
    if (!mailboxId) {
      throw new Error(`Mailbox ${index + 1} id is required.`);
    }
    if (mailboxIds.has(mailboxId)) {
      throw new Error(`Duplicate mailbox id: ${mailboxId}.`);
    }
    mailboxIds.add(mailboxId);

    const account = entry.accountIdentifier.trim();
    if (!account) {
      throw new Error(`Mailbox ${mailboxId} account or address is required.`);
    }
    const accessTokenEnv = entry.accessTokenEnv.trim();
    if (entry.provider !== "json_store" && !accessTokenEnv) {
      throw new Error(`Mailbox ${mailboxId} requires a host access-token environment variable.`);
    }

    const folderMapping: Record<string, string> = {};
    const allowedFolders: string[] = [];
    const folderNames = new Set<string>();
    for (const folder of entry.folders) {
      const folderName = folder.folder.trim();
      const providerFolder = folder.providerFolder.trim();
      if (!folderName && !providerFolder) continue;
      if (!folderName || !providerFolder) {
        throw new Error(`${label} folder mappings require both folder names.`);
      }
      if (folderNames.has(folderName)) {
        throw new Error(`${label} has a duplicate folder mapping: ${folderName}.`);
      }
      folderNames.add(folderName);
      folderMapping[folderName] = providerFolder;
      if (folder.allowedByDefault) allowedFolders.push(folderName);
    }

    return {
      mailbox_id: mailboxId,
      provider: entry.provider,
      account_identifier: account,
      ...(accessTokenEnv ? { auth_secret_refs: { access_token: accessTokenEnv } } : {}),
      ...(Object.keys(folderMapping).length ? { folder_mapping: folderMapping } : {}),
      folder_allowlist_defaults: allowedFolders,
    };
  });
}
