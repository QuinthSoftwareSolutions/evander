export type EmailMailboxFolderPolicyDraftEntry = {
  mailboxId: string;
  folders: string[];
};

export type EmailMailboxFolderPolicyDraftParseResult = {
  entries: EmailMailboxFolderPolicyDraftEntry[];
  error: string | null;
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function normalizedEmailMailboxStrings(values: unknown): string[] {
  if (!Array.isArray(values)) return [];
  const seen = new Set<string>();
  const result: string[] = [];
  for (const value of values) {
    const normalized = String(value ?? "").trim();
    if (!normalized || seen.has(normalized)) continue;
    seen.add(normalized);
    result.push(normalized);
  }
  return result;
}

export function emailMailboxIdFromAuthProfileRef(value: string): string {
  const normalized = value.trim();
  return normalized.startsWith("email.") ? normalized.slice("email.".length) : normalized;
}

export function emailMailboxAuthProfileRef(mailboxId: string): string {
  const normalized = mailboxId.trim();
  return normalized ? `email.${normalized}` : "";
}

export function emailMailboxFolderPolicyEntriesFromConfig(
  value: unknown,
): EmailMailboxFolderPolicyDraftEntry[] {
  if (!isRecord(value)) return [];
  return Object.entries(value).map(([mailboxId, folders]) => ({
    mailboxId,
    folders: normalizedEmailMailboxStrings(folders),
  }));
}

export function parseEmailMailboxFolderPolicyDraftValue(
  value: string | boolean,
): EmailMailboxFolderPolicyDraftParseResult {
  if (typeof value === "boolean") return { entries: [], error: null };
  const text = value.trim();
  if (!text) return { entries: [], error: null };
  try {
    const parsed = JSON.parse(text);
    if (!Array.isArray(parsed)) {
      return { entries: [], error: "Mailbox folder policy must be a list." };
    }
    return {
      entries: parsed.map((item) => {
        const entry = isRecord(item) ? item : {};
        return {
          mailboxId: String(entry.mailboxId ?? entry.mailbox_id ?? ""),
          folders: normalizedEmailMailboxStrings(entry.folders),
        };
      }),
      error: null,
    };
  } catch (reason) {
    return {
      entries: [],
      error: reason instanceof Error ? reason.message : String(reason),
    };
  }
}

export function emailMailboxFolderPolicyDraftValue(
  entries: EmailMailboxFolderPolicyDraftEntry[],
): string {
  return JSON.stringify(entries, null, 2);
}

export function emailMailboxFolderPolicyDraftEntriesToConfig(
  entries: EmailMailboxFolderPolicyDraftEntry[],
): Record<string, string[]> {
  const result: Record<string, string[]> = {};
  for (const [index, entry] of entries.entries()) {
    const mailboxId = entry.mailboxId.trim();
    if (!mailboxId) {
      throw new Error(`Mailbox folder policy row ${index + 1} requires a mailbox.`);
    }
    if (Object.prototype.hasOwnProperty.call(result, mailboxId)) {
      throw new Error(`Duplicate mailbox folder policy: ${mailboxId}.`);
    }
    result[mailboxId] = normalizedEmailMailboxStrings(entry.folders);
  }
  return result;
}
