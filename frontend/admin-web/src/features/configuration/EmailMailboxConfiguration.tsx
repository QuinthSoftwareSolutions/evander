import { RecordCollection } from "../admin/RecordCollection";
import {
  encodeStringListDraftValue,
  parseStringListDraftValue,
} from "../admin/structuredPolicyInputs";
import {
  EMAIL_MAILBOX_PROVIDERS,
  emailMailboxDraftValue,
  emailMailboxEntriesFromConfig,
  emailMailboxFoldersAfterProviderChange,
  emptyEmailMailboxDraftEntry,
  parseEmailMailboxDraftValue,
  type EmailMailboxAuthMode,
  type EmailMailboxDraftEntry,
  type EmailMailboxFolderDraft,
  type EmailMailboxProvider,
} from "./emailMailboxDraft";
import {
  emailMailboxFolderPolicyDraftValue,
  normalizedEmailMailboxStrings,
  parseEmailMailboxFolderPolicyDraftValue,
  type EmailMailboxFolderPolicyDraftEntry,
} from "./emailMailboxPolicyDraft";
import type { ConfigurationDraftFieldState } from "./configurationDraftState";

type EmailMailboxDraftProps = {
  draftValue: string | boolean;
  disabled: boolean;
  onDraftChange: (value: string) => void;
};

type EmailMailboxEditorProps = EmailMailboxDraftProps & {
  onError: (message: string | null) => void;
};

type EmailMailboxPolicyEditorProps = EmailMailboxEditorProps & {
  mailboxes: EmailMailboxDraftEntry[];
};

type EmailMailboxAllowlistEditorProps = EmailMailboxDraftProps & {
  mailboxes: EmailMailboxDraftEntry[];
  fieldName: string;
};

export function emailMailboxLabel(mailbox: EmailMailboxDraftEntry): string {
  const mailboxId = mailbox.mailboxId.trim();
  const account = mailbox.accountIdentifier.trim();
  return account && account !== mailboxId ? `${mailboxId} — ${account}` : mailboxId;
}

function emailMailboxLogicalFolders(mailbox: EmailMailboxDraftEntry): string[] {
  return normalizedEmailMailboxStrings(mailbox.folders.map((folder) => folder.folder));
}

function emailMailboxDefaultFolders(mailbox: EmailMailboxDraftEntry): string[] {
  return normalizedEmailMailboxStrings(
    mailbox.folders
      .filter((folder) => folder.allowedByDefault)
      .map((folder) => folder.folder),
  );
}

export function resolveConfiguredEmailMailboxEntries(
  fieldState: ConfigurationDraftFieldState | undefined,
  effectiveValue: unknown,
  isRoot: boolean,
): EmailMailboxDraftEntry[] {
  if (fieldState) {
    if (fieldState.clear && fieldState.include) {
      return [];
    }
    const usesDraft = isRoot ? !fieldState.clear : fieldState.include && !fieldState.clear;
    if (usesDraft) {
      const parsed = parseEmailMailboxDraftValue(fieldState.value);
      if (!parsed.error) return parsed.entries;
    }
  }
  return emailMailboxEntriesFromConfig(effectiveValue);
}

export function EmailMailboxesEditor({
  draftValue,
  disabled,
  onDraftChange,
  onError,
}: EmailMailboxEditorProps) {
  const parsed = parseEmailMailboxDraftValue(draftValue);
  const entries = parsed.entries;

  function mutate(
    update: (current: EmailMailboxDraftEntry[]) => EmailMailboxDraftEntry[],
  ): boolean {
    if (parsed.error) {
      onError(`Unable to edit email mailboxes: ${parsed.error}`);
      return false;
    }
    onDraftChange(emailMailboxDraftValue(update(parsed.entries)));
    onError(null);
    return true;
  }

  function updateEntry(mailboxIndex: number, nextMailbox: EmailMailboxDraftEntry) {
    mutate((current) =>
      current.map((entry, index) => (index === mailboxIndex ? nextMailbox : entry)),
    );
  }

  function updateFolder(
    mailboxIndex: number,
    folderIndex: number,
    nextFolder: EmailMailboxFolderDraft,
  ) {
    mutate((current) =>
      current.map((entry, index) =>
        index === mailboxIndex
          ? {
              ...entry,
              folders: entry.folders.map((folder, currentIndex) =>
                currentIndex === folderIndex ? nextFolder : folder,
              ),
            }
          : entry,
      ),
    );
  }

  return (
    <div className="email-mailboxes-editor contacts-editor">
      <div className="contacts-editor-header">
        <p className="muted-text">
          Define the mailbox identity and host credential references once. Microsoft Graph client credentials are exchanged for short-lived access tokens automatically; credential values stay in the host environment.
        </p>
        <div className="inline-actions contacts-editor-actions">
          <button
            type="button"
            onClick={() => mutate((current) => [...current, emptyEmailMailboxDraftEntry()])}
            disabled={disabled}
          >
            Add mailbox
          </button>
        </div>
      </div>
      {parsed.error ? (
        <div className="callout error-callout">Email mailbox draft could not be read: {parsed.error}</div>
      ) : null}
      {!parsed.error ? <RecordCollection label="Email mailboxes" entries={entries}
        name={(mailbox, index) => mailbox.mailboxId.trim() || `Mailbox ${index + 1}`}
        description={mailbox => mailbox.accountIdentifier}
        searchText={mailbox => [mailbox.provider, ...mailbox.folders.map(folder => folder.folder)].join(" ")}
        category={mailbox => mailbox.provider}
        metadata={mailbox => <><span className="record-provider">{mailbox.provider}</span><span>{mailbox.folders.length} folders</span></>}
        isNew={mailbox => !mailbox.mailboxId.trim()}
      >
        {(mailbox, mailboxIndex) => {
            const accountLabel = mailbox.provider === "graph"
              ? "Mailbox address"
              : mailbox.provider === "gmail"
                ? "Account"
                : "Mailbox account";
            const accountPlaceholder = mailbox.provider === "graph"
              ? "ops@example.com"
              : mailbox.provider === "gmail"
                ? "me"
                : "default";
            return (
              <div key={`email-mailbox-${mailboxIndex}`} className="contact-editor-item email-mailbox-editor-item">
                <div className="contact-editor-item-header">
                  <strong>{mailbox.mailboxId.trim() || `Mailbox ${mailboxIndex + 1}`}</strong>
                  <div className="inline-actions compact-actions">
                    <button
                      type="button"
                      className="danger"
                      onClick={() =>
                        mutate((current) => current.filter((_, index) => index !== mailboxIndex))
                      }
                      disabled={disabled}
                    >
                      Remove
                    </button>
                  </div>
                </div>
                <div className="communication-target-grid email-mailbox-grid">
                  <label className="field-stack">
                    <span>Mailbox id</span>
                    <input
                      value={mailbox.mailboxId}
                      onChange={(event) => updateEntry(mailboxIndex, {
                        ...mailbox,
                        mailboxId: event.target.value,
                      })}
                      disabled={disabled}
                      placeholder="m365-ops"
                    />
                  </label>
                  <label className="field-stack">
                    <span>Provider</span>
                    <select
                      value={mailbox.provider}
                      onChange={(event) => {
                        const provider = event.target.value as EmailMailboxProvider;
                        updateEntry(mailboxIndex, {
                          ...mailbox,
                          provider,
                          authMode: provider === "graph"
                            ? mailbox.provider === "graph"
                              ? mailbox.authMode
                              : "client_credentials"
                            : "access_token",
                          folders: emailMailboxFoldersAfterProviderChange(
                            mailbox.provider,
                            provider,
                            mailbox.folders,
                          ),
                        });
                      }}
                      disabled={disabled}
                    >
                      {EMAIL_MAILBOX_PROVIDERS.map((provider) => (
                        <option key={provider} value={provider}>
                          {provider === "graph"
                            ? "Microsoft Graph (Office 365)"
                            : provider === "gmail"
                              ? "Gmail"
                              : "Local JSON store"}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label className="field-stack">
                    <span>{accountLabel}</span>
                    <input
                      value={mailbox.accountIdentifier}
                      onChange={(event) => updateEntry(mailboxIndex, {
                        ...mailbox,
                        accountIdentifier: event.target.value,
                      })}
                      disabled={disabled}
                      placeholder={accountPlaceholder}
                    />
                  </label>
                  {mailbox.provider === "graph" ? (
                    <label className="field-stack">
                      <span>Authentication</span>
                      <select
                        value={mailbox.authMode}
                        onChange={(event) => updateEntry(mailboxIndex, {
                          ...mailbox,
                          authMode: event.target.value as EmailMailboxAuthMode,
                        })}
                        disabled={disabled}
                      >
                        <option value="client_credentials">OAuth client credentials (automatic)</option>
                        <option value="access_token">Static access token (legacy)</option>
                      </select>
                    </label>
                  ) : null}
                  {mailbox.provider === "graph" && mailbox.authMode === "client_credentials" ? (
                    <>
                      <label className="field-stack">
                        <span>Tenant ID env var</span>
                        <input
                          value={mailbox.tenantIdEnv}
                          onChange={(event) => updateEntry(mailboxIndex, {
                            ...mailbox,
                            tenantIdEnv: event.target.value,
                          })}
                          disabled={disabled}
                          placeholder="GRAPH_M365_TENANT_ID"
                        />
                      </label>
                      <label className="field-stack">
                        <span>Client ID env var</span>
                        <input
                          value={mailbox.clientIdEnv}
                          onChange={(event) => updateEntry(mailboxIndex, {
                            ...mailbox,
                            clientIdEnv: event.target.value,
                          })}
                          disabled={disabled}
                          placeholder="GRAPH_M365_CLIENT_ID"
                        />
                      </label>
                      <label className="field-stack">
                        <span>Client secret env var</span>
                        <input
                          value={mailbox.clientSecretEnv}
                          onChange={(event) => updateEntry(mailboxIndex, {
                            ...mailbox,
                            clientSecretEnv: event.target.value,
                          })}
                          disabled={disabled}
                          placeholder="GRAPH_M365_CLIENT_SECRET"
                        />
                      </label>
                    </>
                  ) : mailbox.provider !== "json_store" ? (
                    <label className="field-stack">
                      <span>
                        {mailbox.provider === "graph"
                          ? "Host access-token env var (legacy)"
                          : "Host access-token env var"}
                      </span>
                      <input
                        value={mailbox.accessTokenEnv}
                        onChange={(event) => updateEntry(mailboxIndex, {
                          ...mailbox,
                          accessTokenEnv: event.target.value,
                        })}
                        disabled={disabled}
                        placeholder={mailbox.provider === "graph" ? "GRAPH_M365_ACCESS_TOKEN" : "GMAIL_ACCESS_TOKEN"}
                      />
                    </label>
                  ) : null}
                </div>
                <div className="contact-subsection">
                  <div className="contact-subsection-header">
                    <div>
                      <strong>Folders</strong>
                      <p className="muted-text">Map policy folder names to provider folder names and select the defaults available to new sessions.</p>
                    </div>
                    <div className="inline-actions compact-actions">
                      <button
                        type="button"
                        onClick={() => updateEntry(mailboxIndex, {
                          ...mailbox,
                          folders: [
                            ...mailbox.folders,
                            { folder: "", providerFolder: "", allowedByDefault: true },
                          ],
                        })}
                        disabled={disabled}
                      >
                        Add folder
                      </button>
                    </div>
                  </div>
                  {mailbox.folders.length === 0 ? (
                    <div className="empty-state compact-empty-state">No folder mappings are configured.</div>
                  ) : null}
                  {mailbox.folders.map((folder, folderIndex) => (
                    <div key={`email-mailbox-${mailboxIndex}-folder-${folderIndex}`} className="email-mailbox-folder-row">
                      <label className="field-stack">
                        <span>Folder</span>
                        <input
                          value={folder.folder}
                          onChange={(event) => updateFolder(mailboxIndex, folderIndex, {
                            ...folder,
                            folder: event.target.value,
                          })}
                          disabled={disabled}
                          placeholder="inbox"
                        />
                      </label>
                      <label className="field-stack">
                        <span>Provider folder</span>
                        <input
                          value={folder.providerFolder}
                          onChange={(event) => updateFolder(mailboxIndex, folderIndex, {
                            ...folder,
                            providerFolder: event.target.value,
                          })}
                          disabled={disabled}
                          placeholder={mailbox.provider === "gmail" ? "INBOX" : "Inbox"}
                        />
                      </label>
                      <label className="checkbox-row email-mailbox-allowed-folder">
                        <input
                          type="checkbox"
                          checked={folder.allowedByDefault}
                          onChange={(event) => updateFolder(mailboxIndex, folderIndex, {
                            ...folder,
                            allowedByDefault: event.target.checked,
                          })}
                          disabled={disabled}
                        />
                        <span>Allow by default</span>
                      </label>
                      <div className="inline-actions compact-actions">
                        <button
                          type="button"
                          className="danger"
                          onClick={() => updateEntry(mailboxIndex, {
                            ...mailbox,
                            folders: mailbox.folders.filter((_, index) => index !== folderIndex),
                          })}
                          disabled={disabled}
                        >
                          Remove
                        </button>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            );
          }}
      </RecordCollection> : null}
    </div>
  );
}

export function EmailMailboxFolderPolicyEditor({
  draftValue,
  disabled,
  mailboxes,
  onDraftChange,
  onError,
}: EmailMailboxPolicyEditorProps) {
  const parsed = parseEmailMailboxFolderPolicyDraftValue(draftValue);
  const entries = parsed.entries;
  const configuredMailboxes = new Map(
    mailboxes
      .filter((mailbox) => mailbox.mailboxId.trim())
      .map((mailbox) => [mailbox.mailboxId.trim(), mailbox]),
  );
  const selectedMailboxIds = new Set(entries.map((entry) => entry.mailboxId.trim()));
  const nextMailbox = mailboxes.find(
    (mailbox) => mailbox.mailboxId.trim() && !selectedMailboxIds.has(mailbox.mailboxId.trim()),
  );

  function mutate(
    update: (
      current: EmailMailboxFolderPolicyDraftEntry[],
    ) => EmailMailboxFolderPolicyDraftEntry[],
  ): boolean {
    if (parsed.error) {
      onError(`Unable to edit mailbox folder policy: ${parsed.error}`);
      return false;
    }
    onDraftChange(emailMailboxFolderPolicyDraftValue(update(parsed.entries)));
    onError(null);
    return true;
  }

  return (
    <div className="email-mailbox-folder-policy-editor contacts-editor">
      <div className="contacts-editor-header">
        <p className="muted-text">
          Select the logical folders each configured mailbox may expose to sessions. An explicitly
          empty folder selection grants no folders.
        </p>
        <div className="inline-actions contacts-editor-actions">
          <button
            type="button"
            onClick={() =>
              nextMailbox
                && mutate((current) => [
                  ...current,
                  {
                    mailboxId: nextMailbox.mailboxId.trim(),
                    folders: emailMailboxDefaultFolders(nextMailbox),
                  },
                ])
            }
            disabled={disabled || !nextMailbox || Boolean(parsed.error)}
          >
            Add mailbox policy
          </button>
        </div>
      </div>
      {parsed.error ? (
        <div className="callout error-callout">
          Mailbox folder policy draft could not be read: {parsed.error}
        </div>
      ) : null}
      {!parsed.error && entries.length === 0 ? (
        <div className="empty-state">
          {mailboxes.length
            ? "No mailbox-specific folder policies are configured."
            : "Configure an email mailbox before adding folder policy."}
        </div>
      ) : null}
      {!parsed.error
        ? entries.map((entry, entryIndex) => {
            const mailboxId = entry.mailboxId.trim();
            const mailbox = configuredMailboxes.get(mailboxId);
            const mailboxOptions = mailboxes.filter((candidate) => {
              const candidateId = candidate.mailboxId.trim();
              return candidateId && (candidateId === mailboxId || !selectedMailboxIds.has(candidateId));
            });
            const folderOptions = normalizedEmailMailboxStrings([
              ...(mailbox ? emailMailboxLogicalFolders(mailbox) : []),
              ...entry.folders,
            ]);
            const selectedFolders = new Set(entry.folders);
            return (
              <div
                key={`email-mailbox-folder-policy-${entryIndex}`}
                className="contact-editor-item email-mailbox-folder-policy-item"
              >
                <div className="contact-editor-item-header">
                  <strong>{mailbox ? emailMailboxLabel(mailbox) : mailboxId || `Policy ${entryIndex + 1}`}</strong>
                  <div className="inline-actions compact-actions">
                    <button
                      type="button"
                      className="danger"
                      onClick={() => mutate((current) =>
                        current.filter((_, index) => index !== entryIndex),
                      )}
                      disabled={disabled}
                    >
                      Remove
                    </button>
                  </div>
                </div>
                <label className="field-stack">
                  <span>Mailbox</span>
                  <select
                    value={mailboxId}
                    onChange={(event) => {
                      const nextMailboxId = event.target.value;
                      const selectedMailbox = configuredMailboxes.get(nextMailboxId);
                      mutate((current) =>
                        current.map((currentEntry, index) =>
                          index === entryIndex
                            ? {
                                mailboxId: nextMailboxId,
                                folders: selectedMailbox
                                  ? emailMailboxDefaultFolders(selectedMailbox)
                                  : currentEntry.folders,
                              }
                            : currentEntry,
                        ),
                      );
                    }}
                    disabled={disabled}
                  >
                    {!mailbox && mailboxId ? (
                      <option value={mailboxId}>{mailboxId} (unknown mailbox)</option>
                    ) : null}
                    {mailboxOptions.map((option) => (
                      <option key={option.mailboxId} value={option.mailboxId.trim()}>
                        {emailMailboxLabel(option)}
                      </option>
                    ))}
                  </select>
                </label>
                <div className="contact-subsection">
                  <strong>Allowed folders</strong>
                  {folderOptions.length ? (
                    <div className="mailbox-folder-options">
                      {folderOptions.map((folder) => (
                        <label key={folder} className="checkbox-row">
                          <input
                            type="checkbox"
                            checked={selectedFolders.has(folder)}
                            onChange={(event) => mutate((current) =>
                              current.map((currentEntry, index) =>
                                index === entryIndex
                                  ? {
                                      ...currentEntry,
                                      folders: event.target.checked
                                        ? normalizedEmailMailboxStrings([
                                            ...currentEntry.folders,
                                            folder,
                                          ])
                                        : currentEntry.folders.filter((value) => value !== folder),
                                    }
                                  : currentEntry,
                              ),
                            )}
                            disabled={disabled}
                          />
                          <span>{folder}</span>
                        </label>
                      ))}
                    </div>
                  ) : (
                    <div className="empty-state compact-empty-state">
                      This mailbox has no logical folders configured.
                    </div>
                  )}
                </div>
              </div>
            );
          })
        : null}
    </div>
  );
}

export function EmailMailboxAllowlistEditor({
  fieldName,
  draftValue,
  disabled,
  mailboxes,
  onDraftChange,
}: EmailMailboxAllowlistEditorProps) {
  const entries = parseStringListDraftValue(draftValue);
  const configuredMailboxes = mailboxes.filter((mailbox) => mailbox.mailboxId.trim());
  const configuredMailboxIds = new Set(
    configuredMailboxes.map((mailbox) => mailbox.mailboxId.trim()),
  );
  const nextMailbox = configuredMailboxes.find(
    (mailbox) => !entries.includes(mailbox.mailboxId.trim()),
  );

  function updateEntries(nextEntries: string[]) {
    onDraftChange(encodeStringListDraftValue(nextEntries));
  }

  return (
    <div className="string-list-selector email-mailbox-allowlist-editor">
      <div className="contacts-editor-header">
        <p className="muted-text">
          Select the configured mailboxes that sessions in this directory may access.
        </p>
        <div className="inline-actions contacts-editor-actions">
          <button
            type="button"
            onClick={() =>
              nextMailbox && updateEntries([...entries, nextMailbox.mailboxId.trim()])
            }
            disabled={disabled || !nextMailbox}
          >
            Add mailbox
          </button>
        </div>
      </div>
      {entries.length === 0 ? (
        <div className="empty-state">
          {configuredMailboxes.length
            ? "No mailboxes are allowed by this policy."
            : "Configure an email mailbox before adding it to the policy."}
        </div>
      ) : null}
      {entries.map((entry, index) => {
        const mailboxOptions = configuredMailboxes.filter((mailbox) => {
          const mailboxId = mailbox.mailboxId.trim();
          return mailboxId === entry || !entries.includes(mailboxId);
        });
        const isUnknownMailbox = !configuredMailboxIds.has(entry);
        return (
          <div key={`${fieldName}-${index}-${entry}`} className="string-list-selector-row">
            <label className="field-stack">
              <span>Mailbox</span>
              <select
                value={entry}
                onChange={(event) => {
                  const nextEntries = [...entries];
                  nextEntries[index] = event.target.value;
                  updateEntries(nextEntries);
                }}
                disabled={disabled}
              >
                {isUnknownMailbox ? (
                  <option value={entry}>{entry} (unknown mailbox)</option>
                ) : null}
                {mailboxOptions.map((mailbox) => (
                  <option key={mailbox.mailboxId} value={mailbox.mailboxId.trim()}>
                    {emailMailboxLabel(mailbox)}
                  </option>
                ))}
              </select>
            </label>
            <div className="inline-actions compact-actions">
              <button
                type="button"
                className="danger"
                onClick={() => updateEntries(
                  entries.filter((_, currentIndex) => currentIndex !== index),
                )}
                disabled={disabled}
              >
                Remove
              </button>
            </div>
          </div>
        );
      })}
    </div>
  );
}
